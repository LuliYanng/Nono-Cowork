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


# ========== Feishu channel ==========
class FeishuChannel(Channel):
    name = "feishu"

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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    channel = FeishuChannel()
    channel.start()


if __name__ == "__main__":
    main()
