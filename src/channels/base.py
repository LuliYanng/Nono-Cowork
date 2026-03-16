"""
Channel base class — all IM channels must implement this interface

To add a new channel:
1. Inherit from Channel
2. Implement start() / send_reply() / send_status()
3. Call self.dispatch() in your message handler
"""
from abc import ABC, abstractmethod
import threading
import logging
from agent_runner import run_agent_for_message
from session import sessions

logger = logging.getLogger("channel")


# Special commands shared across all channels
RESET_COMMANDS = {"/reset", "/new", "reset"}
HELP_COMMANDS = {"/help", "help"}

HELP_TEXT = (
    "🤖 VPS Agent Help\n\n"
    "Send text commands directly and I'll execute them on the server.\n\n"
    "Example commands:\n"
    "• Check server disk usage\n"
    "• Write a Python script for...\n"
    "• Search for the latest info on xxx\n\n"
    "Special commands:\n"
    "• /reset - Reset session (clear context)\n"
    "• /help - Show this help message\n"
)


class Channel(ABC):
    """IM channel abstract base class"""

    name: str = "unknown"  # Override in subclass

    @abstractmethod
    def start(self):
        """Start the channel and begin listening for messages."""
        ...

    @abstractmethod
    def send_reply(self, user_id: str, text: str):
        """Send the Agent's final reply (may contain markdown)."""
        ...

    @abstractmethod
    def send_status(self, user_id: str, text: str):
        """Send a brief status update (plain text)."""
        ...

    def send_file(self, user_id: str, file_path: str, caption: str = "") -> bool:
        """Send a file to the user via IM. Returns True on success.

        Override in subclass to enable file sending. Default: not supported.
        """
        return False

    def dispatch(self, user_id: str, user_text: str,
                 extra_context: dict = None):
        """
        Handle an incoming user message (shared dispatch logic for all channels).

        Subclass message handlers should parse out user_id and user_text, then call this method.
        """
        user_text = user_text.strip()
        if not user_text:
            return

        logger.info(f"[{self.name}] Message from {user_id}: {user_text}")

        # Special commands
        if user_text.lower() in RESET_COMMANDS:
            sessions.reset(user_id)
            self.send_status(user_id, "🔄 Session reset. You can start a new conversation.")
            return

        if user_text.lower() in HELP_COMMANDS:
            self.send_status(user_id, HELP_TEXT)
            return

        # "Processing" notification
        self.send_status(user_id, "💭 Thinking...")

        # Build callbacks
        def reply_func(text):
            self.send_reply(user_id, text)

        def status_func(text):
            self.send_status(user_id, text)

        # Run Agent in a separate thread
        thread = threading.Thread(
            target=run_agent_for_message,
            args=(user_id, user_text, reply_func, status_func, self.name),
            daemon=True,
        )
        thread.start()
