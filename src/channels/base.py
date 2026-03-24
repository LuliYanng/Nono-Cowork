"""
Channel base class — all IM channels must implement this interface

To add a new channel:
1. Inherit from Channel
2. Implement start() / send_reply() / send_status()
3. Call self.dispatch() in your message handler
"""
from abc import ABC, abstractmethod
import threading
import time
import logging
from agent_runner import run_agent_for_message
from session import sessions
from config import MODEL, MODEL_POOL, CONTEXT_LIMIT

logger = logging.getLogger("channel")


# ═══════════════════════════════════════════
#  Slash command registry
# ═══════════════════════════════════════════

def _cmd_reset(channel, user_id: str, args: str):
    """Reset the current session and start fresh."""
    sessions.reset(user_id)
    channel.send_status(user_id, "🔄 Session reset. You can start a new conversation.")


def _cmd_help(channel, user_id: str, args: str):
    """Show help message with all available commands."""
    lines = [
        "🤖 **VPS Agent Help**",
        "",
        "Send text directly — I'll handle it on the server.",
        "",
        "📝  Examples:",
        "• Check server disk usage",
        "• Write a Python script for...",
        "• Search for the latest info on xxx",
        "",
        "⚡  Commands:",
    ]
    for name, (_, desc) in sorted(SLASH_COMMANDS.items()):
        lines.append(f"• `/{name}` — {desc}")
    channel.send_status(user_id, "\n".join(lines))


def _cmd_status(channel, user_id: str, args: str):
    """Show current session status: model, tokens, context usage."""
    info = sessions.get_status(user_id)
    if not info:
        channel.send_status(user_id, "ℹ️ No active session. Send a message to start one.")
        return

    stats = info["token_stats"]
    model = info["model_override"] or MODEL
    elapsed = time.time() - info["created_at"]
    mins = int(elapsed // 60)

    # Context usage — based on last API call (= current context size)
    pt = stats.get("last_prompt_tokens", 0)
    pct = min(pt / CONTEXT_LIMIT * 100, 100) if CONTEXT_LIMIT else 0
    filled = int(12 * pct / 100)
    bar = "█" * filled + "░" * (12 - filled)

    def fmt(n):
        return f"{n/1000:.1f}k" if n >= 1000 else str(n)

    lines = [
        "📊 **Session Status**",
        "",
        f"🤖 Model: `{model}`",
        f"⏱️ Duration: {mins}m",
        f"💬 Messages: {info['history_len']}",
        f"📡 API calls: {stats['total_api_calls']}",
        "",
        f"⟨{bar}⟩ {pct:.0f}%  context: {fmt(pt)} / {fmt(CONTEXT_LIMIT)}",
        f"Total consumed: {fmt(stats['total_tokens'])}  "
        f"(prompt: {fmt(stats['total_prompt_tokens'])} + completion: {fmt(stats['total_completion_tokens'])})",
    ]
    if stats["total_cached_tokens"]:
        lines.append(f"Cached: {fmt(stats['total_cached_tokens'])}")
    if info["is_running"]:
        lines.append("\n🔄 Agent is currently running...")

    channel.send_status(user_id, "\n".join(lines))


def _cmd_stop(channel, user_id: str, args: str):
    """Stop the currently running agent task."""
    if sessions.request_stop(user_id):
        channel.send_status(user_id, "🛑 Stop requested. The agent will halt after the current step.")
    else:
        channel.send_status(user_id, "ℹ️ No active session to stop.")


def _cmd_compact(channel, user_id: str, args: str):
    """Manually compress the session context to free up space."""
    from context.compressor import compress_history

    session = sessions.get_or_create(user_id)
    lock = sessions.get_lock(user_id)

    if not lock.acquire(blocking=False):
        channel.send_status(user_id, "⏳ Agent is running. Wait for it to finish before compacting.")
        return

    try:
        history = session["history"]
        old_len = len(history)

        if old_len <= 3:
            channel.send_status(user_id, "ℹ️ Not enough context to compress.")
            return

        # Force compression regardless of threshold
        new_history = compress_history(history, CONTEXT_LIMIT)

        if len(new_history) < old_len:
            session["history"] = new_history
            channel.send_status(
                user_id,
                f"📦 Context compressed: {old_len} → {len(new_history)} messages. "
                f"Freed ~{old_len - len(new_history)} messages."
            )
        else:
            channel.send_status(user_id, "ℹ️ Context is already compact — nothing to compress.")
    finally:
        lock.release()


def _cmd_model(channel, user_id: str, args: str):
    """View or switch the LLM model for this session."""
    args = args.strip()

    if not args:
        # Show current model and reference list
        current = sessions.get_model(user_id) or MODEL
        lines = [
            f"🤖 Current model: `{current}`",
            f"Default: `{MODEL}`",
            "",
            "Reference (configured in config.py):",
        ]
        for m in MODEL_POOL:
            lines.append(f"  • `{m}`")
        lines.append(f"\nUsage: `/model <model_name>`")
        lines.append(f"Restore default: `/model reset`")
        channel.send_status(user_id, "\n".join(lines))
        return

    # Reset to default
    if args.lower() in ("reset", "default"):
        sessions.set_model(user_id, None)
        channel.send_status(user_id, f"🤖 Model reset to default: `{MODEL}`")
        return

    # Set model by name (user provides the exact model identifier)
    sessions.set_model(user_id, args)
    channel.send_status(user_id, f"🤖 Model switched to: `{args}`")


# ── Command table: name → (handler_func, description) ──
SLASH_COMMANDS = {
    "help":    (_cmd_help,    "Show this help message"),
    "reset":   (_cmd_reset,   "Reset session (clear context)"),
    "status":  (_cmd_status,  "View session status & token usage"),
    "stop":    (_cmd_stop,    "Stop the current running task"),
    "compact": (_cmd_compact, "Compress context to free up space"),
    "model":   (_cmd_model,   "View or switch the LLM model"),
}


# ═══════════════════════════════════════════
#  Channel base class
# ═══════════════════════════════════════════

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

        # ── Slash command dispatch ──
        if user_text.startswith("/"):
            parts = user_text[1:].split(None, 1)
            cmd_name = parts[0].lower()
            cmd_args = parts[1] if len(parts) > 1 else ""

            handler = SLASH_COMMANDS.get(cmd_name)
            if handler:
                handler[0](self, user_id, cmd_args)
                return
            # Also support bare "reset" / "help" without slash
        elif user_text.lower() in ("reset", "help"):
            handler = SLASH_COMMANDS.get(user_text.lower())
            if handler:
                handler[0](self, user_id, "")
                return

        # "Processing" notification
        self.send_status(user_id, "Thinking...")

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
