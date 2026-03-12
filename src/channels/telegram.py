"""
Telegram channel adapter — uses Polling mode to receive messages

Start: .venv/bin/python src/channels/telegram.py
Config: Set TELEGRAM_BOT_TOKEN in .env

Get a Token:
  1. Search for @BotFather in Telegram
  2. Send /newbot and follow the prompts
  3. Copy the Token into .env
"""
import os
import sys
import re
import logging

# Ensure src/ is on the Python path
_src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import telebot
from dotenv import load_dotenv

from channels.base import Channel
from channels.telegram_formatting import format_for_telegram, escape_markdown_v2
from formatter import split_long_text

load_dotenv()

logger = logging.getLogger("channel.telegram")

# ========== Config ==========
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Optional: restrict allowed Telegram user IDs (numeric)
ALLOWED_USERS_STR = os.getenv("TELEGRAM_ALLOWED_USERS", "")
ALLOWED_USERS: set[int] = set(
    int(u.strip()) for u in ALLOWED_USERS_STR.split(",") if u.strip()
)

MAX_MSG_LEN = 4096  # Telegram single message length limit


# ========== Telegram channel ==========
class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self):
        self.bot = None

    # ---- Channel interface implementation ----

    def start(self):
        if not BOT_TOKEN:
            print("❌ Error: Please set TELEGRAM_BOT_TOKEN in .env")
            print("\nSteps:")
            print("  1. Search for @BotFather in Telegram")
            print("  2. Send /newbot to create a bot")
            print("  3. Copy the Token into .env:")
            print("     TELEGRAM_BOT_TOKEN=123456:ABC-DEF...")
            print("  TELEGRAM_ALLOWED_USERS=12345,67890  (optional)")
            return

        self.bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

        # Register message handler
        @self.bot.message_handler(func=lambda msg: True, content_types=["text"])
        def on_text_message(message):
            self._on_message(message)

        # Get bot info
        try:
            bot_info = self.bot.get_me()
            bot_name = bot_info.username
        except Exception as e:
            print(f"❌ Invalid token or network error: {e}")
            return

        print("=" * 50)
        print("🚀 Telegram Bot started (Polling mode)")
        print(f"   Bot: @{bot_name}")
        print(f"   Allowed users: {'not set' if not ALLOWED_USERS else f'{len(ALLOWED_USERS)} user(s)'}")
        print("=" * 50)
        print("Waiting for Telegram messages...\n")

        # Blocking polling mode (auto-reconnect)
        self.bot.infinity_polling(timeout=30, long_polling_timeout=30)

    def send_reply(self, user_id: str, text: str):
        """Send Agent reply (try Markdown, fallback to plain text)."""
        chat_id = int(user_id)
        text = format_for_telegram(text)
        if not text:
            return

        if len(text) <= MAX_MSG_LEN:
            self._send_message(chat_id, text)
        else:
            chunks = split_long_text(text, MAX_MSG_LEN)
            for i, chunk in enumerate(chunks):
                if len(chunks) > 1:
                    chunk = f"📄 [{i + 1}/{len(chunks)}]\n{chunk}"
                self._send_message(chat_id, chunk)

    def send_status(self, user_id: str, text: str):
        """Send plain text status update."""
        chat_id = int(user_id)
        try:
            self.bot.send_message(chat_id, text)
        except Exception as e:
            logger.error(f"Failed to send status: {e}")

    # ---- Telegram-specific internal methods ----

    def _on_message(self, message):
        """Handle Telegram message."""
        try:
            user_id = message.from_user.id
            chat_id = message.chat.id
            user_text = message.text or ""

            # Permission check
            if ALLOWED_USERS and user_id not in ALLOWED_USERS:
                logger.warning(f"Unauthorized user: {user_id}")
                self.bot.send_message(chat_id, "⛔ You are not authorized to use this bot.")
                return

            # Handle /start command
            if user_text.startswith("/start"):
                self.bot.send_message(
                    chat_id,
                    "👋 Hi! I'm the VPS Agent bot.\n\n"
                    "Send text commands directly and I'll execute them on the server.\n"
                    "Send /help for help."
                )
                return

            # Strip @bot_name suffix
            user_text = re.sub(r"@\w+bot\b", "", user_text, flags=re.IGNORECASE).strip()

            # Dispatch to base class
            self.dispatch(str(user_id), user_text)

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)

    def _send_message(self, chat_id: int, text: str):
        """Send message: try MarkdownV2 first, fallback to plain text."""
        try:
            # Try MarkdownV2
            escaped = escape_markdown_v2(text)
            self.bot.send_message(chat_id, escaped, parse_mode="MarkdownV2")
        except Exception:
            try:
                # Fallback to plain text
                self.bot.send_message(chat_id, text)
            except Exception as e:
                logger.error(f"Failed to send message: {e}")


# ========== Entry point ==========
def main():
    import atexit
    from logger import recover_orphaned_logs
    from session import sessions

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Recover any orphaned log files from previous crashes
    recover_orphaned_logs()
    # Ensure all sessions are closed on shutdown
    atexit.register(sessions.close_all)

    channel = TelegramChannel()
    channel.start()


if __name__ == "__main__":
    main()
