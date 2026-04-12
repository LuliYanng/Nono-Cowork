"""
Feishu (Lark) channel adapter — handles Feishu-specific message sending/receiving

Start: .venv/bin/python src/channels/feishu.py
"""
import os
import sys
import json
import re
import time
import logging

# Ensure src/ is on the Python path
_src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from dotenv import load_dotenv

from channels.base import Channel
from channels.feishu_formatting import format_for_feishu
from formatter import split_long_text

load_dotenv()

logger = logging.getLogger("channel.feishu")

# ========== Config ==========
APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
ALLOWED_USERS_STR = os.getenv("FEISHU_ALLOWED_USERS", "")
ALLOWED_USERS: set[str] = set(u.strip() for u in ALLOWED_USERS_STR.split(",") if u.strip())

MAX_CARD_LEN = 4000

# Owner's Feishu open_id for notification delivery.
# Auto-detected: captured from the first authorized message at runtime.
# Can be overridden via env var, or falls back to first FEISHU_ALLOWED_USERS entry.
_FEISHU_OWNER_OPEN_ID = os.getenv("FEISHU_OWNER_OPEN_ID", "").strip()
if not _FEISHU_OWNER_OPEN_ID and ALLOWED_USERS:
    _FEISHU_OWNER_OPEN_ID = next(iter(ALLOWED_USERS))


# ========== Feishu channel ==========
class FeishuChannel(Channel):
    name = "feishu"
    owner_native_id = _FEISHU_OWNER_OPEN_ID

    def __init__(self):
        self.client = lark.Client.builder() \
            .app_id(APP_ID) \
            .app_secret(APP_SECRET) \
            .log_level(lark.LogLevel.INFO) \
            .build()

    # ---- Channel interface implementation ----

    def start(self):
        if not APP_ID or not APP_SECRET:
            print("❌ Error: Please set FEISHU_APP_ID and FEISHU_APP_SECRET in .env")
            print("\nRequired environment variables:")
            print("  FEISHU_APP_ID=cli_xxxxxxxxxxxx")
            print("  FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxx")
            print("  FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy  (optional)")
            return

        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._on_message_event) \
            .build()

        cli = lark.ws.Client(
            APP_ID, APP_SECRET,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        print("=" * 50)
        print("🚀 Feishu Bot started (WebSocket long connection)")
        print(f"   App ID: {APP_ID[:10]}...")
        print(f"   Allowed users: {'not set' if not ALLOWED_USERS else f'{len(ALLOWED_USERS)} user(s)'}")
        print("=" * 50)
        print("Waiting for Feishu messages...\n")

        cli.start()

    def send_reply(self, user_id: str, text: str):
        """Send Agent reply (message card + Markdown rendering)."""
        text = format_for_feishu(text)
        if not text:
            return

        if len(text) <= MAX_CARD_LEN:
            self._send_card(user_id, text)
        else:
            chunks = split_long_text(text, MAX_CARD_LEN)
            for i, chunk in enumerate(chunks):
                title = f"Reply ({i + 1}/{len(chunks)})" if len(chunks) > 1 else None
                self._send_card(user_id, chunk, header_title=title)
                time.sleep(0.3)

    def send_status(self, user_id: str, text: str):
        """Send plain text status update."""
        self._send_text(user_id, text)

    def send_file(self, user_id: str, file_path: str, caption: str = "") -> bool:
        """Send a file to the user via Feishu (upload → send message)."""
        import os as _os
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

        if not _os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False

        file_name = _os.path.basename(file_path)
        ext = _os.path.splitext(file_name)[1].lower()

        # Determine file_type for Feishu API
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        if ext in image_exts:
            file_type = "image"
            msg_type = "image"
        else:
            file_type = "stream"
            msg_type = "file"

        try:
            # Step 1: Upload file to Feishu
            with open(file_path, "rb") as f:
                request = CreateFileRequest.builder() \
                    .request_body(
                        CreateFileRequestBody.builder()
                            .file_type(file_type)
                            .file_name(file_name)
                            .file(f)
                            .build()
                    ).build()
                resp = self.client.im.v1.file.create(request)

            if not resp.success():
                logger.error(f"Failed to upload file: code={resp.code}, msg={resp.msg}")
                return False

            file_key = resp.data.file_key

            # Step 2: Send file message
            if msg_type == "image":
                content = json.dumps({"image_key": file_key})
            else:
                content = json.dumps({"file_key": file_key})

            request = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                        .receive_id(user_id)
                        .msg_type(msg_type)
                        .content(content)
                        .build()
                ).build()
            resp = self.client.im.v1.message.create(request)

            if not resp.success():
                logger.error(f"Failed to send file message: code={resp.code}, msg={resp.msg}")
                return False

            # Send caption as a follow-up text if provided
            if caption:
                self._send_text(user_id, caption)

            return True

        except Exception as e:
            logger.error(f"Failed to send file: {e}", exc_info=True)
            return False

    # ---- Feishu-specific internal methods ----

    def _on_message_event(self, data) -> None:
        """Handle Feishu message event."""
        try:
            message = data.event.message
            sender = data.event.sender

            msg_type = message.message_type
            chat_type = message.chat_type
            open_id = sender.sender_id.open_id

            # Permission check
            if ALLOWED_USERS and open_id not in ALLOWED_USERS:
                logger.warning(f"Unauthorized user: {open_id}")
                self._send_text(open_id, "⛔ You are not authorized to use this bot.")
                return

            # Auto-learn owner's open_id from the first authorized message
            # (single-owner system: anyone who passes auth IS the owner)
            if not self.owner_native_id:
                self.owner_native_id = open_id
                logger.info(f"Auto-captured owner Feishu open_id: {open_id}")

            # Only handle text messages
            if msg_type != "text":
                self._send_text(open_id, "⚠️ Only text messages are supported at this time.")
                return

            # Parse text content
            content_json = json.loads(message.content)
            user_text = content_json.get("text", "").strip()
            user_text = re.sub(r"@_user_\d+", "", user_text).strip()

            if not user_text:
                return

            logger.info(f"Feishu raw text from {open_id}: {user_text!r}")

            # Dispatch to base class (handles special commands + starts Agent thread)
            self.dispatch(open_id, user_text, extra_context={
                "chat_type": chat_type,
                "chat_id": message.chat_id,
                "message_id": message.message_id,
            })

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)

    def _send_text(self, receive_id: str, text: str,
                   id_type: str = "open_id") -> bool:
        content = json.dumps({"text": text})
        request = CreateMessageRequest.builder() \
            .receive_id_type(id_type) \
            .request_body(
                CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("text")
                    .content(content)
                    .build()
            ).build()
        resp = self.client.im.v1.message.create(request)
        if not resp.success():
            logger.error(f"Failed to send message: code={resp.code}, msg={resp.msg}")
            return False
        return True

    def _send_card(self, receive_id: str, markdown_content: str,
                   id_type: str = "open_id",
                   header_title: str = None,
                   header_color: str = "blue") -> bool:
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": markdown_content}],
        }
        if header_title:
            card["header"] = {
                "title": {"tag": "plain_text", "content": header_title},
                "template": header_color,
            }

        content = json.dumps(card)
        request = CreateMessageRequest.builder() \
            .receive_id_type(id_type) \
            .request_body(
                CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("interactive")
                    .content(content)
                    .build()
            ).build()
        resp = self.client.im.v1.message.create(request)
        if not resp.success():
            logger.error(f"Failed to send card: code={resp.code}, msg={resp.msg}")
            return False
        return True


# ========== Entry point ==========
def main():
    import atexit
    from logger import recover_orphaned_logs
    from session import sessions
    from channels.registry import register_channel
    from scheduler import scheduler

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Suppress noisy httpx logs (Composio SDK telemetry)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Recover any orphaned log files from previous crashes
    recover_orphaned_logs()
    # Ensure all sessions are closed on shutdown
    atexit.register(sessions.close_all)
    atexit.register(scheduler.stop)

    channel = FeishuChannel()
    register_channel(channel)

    # Start scheduler (reloads persisted tasks)
    scheduler.start()

    # Start Composio trigger listener (if enabled)
    from composio_triggers import start_listener as start_trigger_listener
    start_trigger_listener()

    channel.start()


if __name__ == "__main__":
    main()
