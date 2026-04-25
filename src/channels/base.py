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
from core.agent_runner import run_agent_for_message
from core.session import sessions
from config import MODEL, MODEL_POOL, CONTEXT_LIMIT, OWNER_USER_ID

logger = logging.getLogger("channel")


# ═══════════════════════════════════════════
#  Slash command registry
# ═══════════════════════════════════════════

def _cmd_reset(channel, user_id: str, args: str):
    """Reset the current session and start fresh."""
    sessions.reset(OWNER_USER_ID)
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
    info = sessions.get_status(OWNER_USER_ID)
    if not info:
        channel.send_status(user_id, "ℹ️ No active session. Send a message to start one.")
        return

    stats = info.get("token_stats") or {}
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
        f"📎 Session: `{info.get('session_id', 'unknown')}`",
        f"🤖 Model: `{model}`",
        f"⏱️ Duration: {mins}m",
        f"💬 Messages: {info['history_len']}",
        f"📡 API calls: {stats.get('total_api_calls', 0)}",
        "",
        f"⟨{bar}⟩ {pct:.0f}%  context: {fmt(pt)} / {fmt(CONTEXT_LIMIT)}",
        f"Total consumed: {fmt(stats.get('total_tokens', 0))}  "
        f"(prompt: {fmt(stats.get('total_prompt_tokens', 0))} + completion: {fmt(stats.get('total_completion_tokens', 0))})",
    ]
    if stats.get("total_cached_tokens", 0):
        lines.append(f"Cached: {fmt(stats.get('total_cached_tokens', 0))}")
    if info["is_running"]:
        lines.append("\n🔄 Agent is currently running...")

    channel.send_status(user_id, "\n".join(lines))


def _cmd_stop(channel, user_id: str, args: str):
    """Stop the currently running agent task.

    Args can include scope:
      - "delegate" → only stop the running subagent, main agent continues
      - anything else or empty → stop everything (default)
    """
    scope = args.strip().lower() if args else ""

    if scope == "delegate":
        if sessions.request_subagent_stop(OWNER_USER_ID):
            channel.send_status(user_id, "🛑 Stopping sub-agent... Main agent will continue.")
        else:
            channel.send_status(user_id, "ℹ️ No active session.")
    else:
        if sessions.request_stop(OWNER_USER_ID):
            channel.send_status(user_id, "🛑 Stop requested. The agent will halt after the current step.")
        else:
            channel.send_status(user_id, "ℹ️ No active session to stop.")


def _cmd_compact(channel, user_id: str, args: str):
    """Manually compress the session context to free up space."""
    from context.compressor import compress_history

    session = sessions.get_or_create(OWNER_USER_ID)
    lock = sessions.get_lock(OWNER_USER_ID)

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
        current = sessions.get_model(OWNER_USER_ID) or MODEL
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
        sessions.set_model(OWNER_USER_ID, None)
        channel.send_status(user_id, f"🤖 Model reset to default: `{MODEL}`")
        return

    # Set model by name (user provides the exact model identifier)
    sessions.set_model(OWNER_USER_ID, args)
    channel.send_status(user_id, f"🤖 Model switched to: `{args}`")


def _cmd_new(channel, user_id: str, args: str):
    """Start a new session (archives the current one)."""
    sessions.reset(OWNER_USER_ID)
    channel.send_status(user_id, "✨ New session started. Previous session saved.")


def _cmd_sessions(channel, user_id: str, args: str):
    """List saved sessions for this user."""
    saved = sessions.list_sessions(OWNER_USER_ID)
    if not saved:
        channel.send_status(user_id, "📭 No saved sessions.")
        return

    # Mark which one is active
    active_id = None
    status = sessions.get_status(OWNER_USER_ID)
    if status:
        active_id = status.get("session_id")

    lines = ["📋 **Saved Sessions**", ""]
    for s in saved[:20]:  # Show up to 20
        ts = time.strftime("%m-%d %H:%M", time.localtime(s["created_at"]))
        marker = " 👈 current" if s["id"] == active_id else ""
        preview = f" — {s['preview']}" if s.get("preview") else ""
        lines.append(f"• `{s['id']}` ({ts}, {s['message_count']} msgs){preview}{marker}")

    lines.append(f"\nSwitch: `/switch <session_id>`")
    channel.send_status(user_id, "\n".join(lines))


def _cmd_switch(channel, user_id: str, args: str):
    """Switch to a different saved session."""
    session_id = args.strip()
    if not session_id:
        channel.send_status(user_id, "Usage: `/switch <session_id>`\nUse `/sessions` to see available sessions.")
        return

    lock = sessions.get_lock(OWNER_USER_ID)
    if not lock.acquire(blocking=False):
        channel.send_status(user_id, "⏳ Agent is running. Wait for it to finish before switching.")
        return
    try:
        if sessions.switch_session(OWNER_USER_ID, session_id):
            channel.send_status(user_id, f"🔄 Switched to session `{session_id}`.")
        else:
            channel.send_status(user_id, f"❌ Session `{session_id}` not found.")
    finally:
        lock.release()


# ── Command table: name → (handler_func, description) ──
SLASH_COMMANDS = {
    "help":     (_cmd_help,     "Show this help message"),
    "reset":    (_cmd_reset,    "Reset session (clear context)"),
    "new":      (_cmd_new,      "Start a new session (saves current)"),
    "sessions": (_cmd_sessions, "List saved sessions"),
    "switch":   (_cmd_switch,   "Switch to a saved session"),
    "status":   (_cmd_status,   "View session status & token usage"),
    "stop":     (_cmd_stop,     "Stop the current running task"),
    "compact":  (_cmd_compact,  "Compress context to free up space"),
    "model":    (_cmd_model,    "View or switch the LLM model"),
}


# ═══════════════════════════════════════════
#  Channel base class
# ═══════════════════════════════════════════

class Channel(ABC):
    """IM channel abstract base class"""

    name: str = "unknown"  # Override in subclass

    # Owner's native ID on this channel (e.g., Feishu open_id, Telegram chat_id).
    # Used by notification _distribute() to send messages to the correct IM target.
    # Subclasses should set this from env vars. If empty, notifications to this
    # channel will fall back to OWNER_USER_ID (which may fail for non-Desktop channels).
    owner_native_id: str = ""

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
        # Preserve the channel-native ID for message delivery (Feishu needs open_id,
        # Telegram needs numeric ID). OWNER_USER_ID is only for session indexing.
        from config import OWNER_USER_ID
        raw_user_id = user_id       # original channel ID — used for send_reply/send_status
        session_id = OWNER_USER_ID  # unified ID — used for session management

        user_text = user_text.strip()
        if not user_text:
            return

        logger.info(f"[{self.name}] Message from {raw_user_id} (session: {session_id}): {user_text}")

        # ── Slash command dispatch ──
        if user_text.startswith("/"):
            parts = user_text[1:].split(None, 1)
            cmd_name = parts[0].lower()
            cmd_args = parts[1] if len(parts) > 1 else ""

            handler = SLASH_COMMANDS.get(cmd_name)
            if handler:
                handler[0](self, raw_user_id, cmd_args)
                return
            # Also support bare command words without "/" prefix.
            # Some IM platforms (Feishu) intercept /slash messages, so users
            # can type "sessions", "new", "stop" etc. directly.
        else:
            parts = user_text.split(None, 1)
            bare_cmd = parts[0].lower()
            bare_args = parts[1] if len(parts) > 1 else ""
            handler = SLASH_COMMANDS.get(bare_cmd)
            if handler:
                handler[0](self, raw_user_id, bare_args)
                return

        # "Processing" notification
        self.send_status(raw_user_id, "Thinking...")

        # Build callbacks — use raw_user_id so messages go to the right channel recipient
        def reply_func(text):
            self.send_reply(raw_user_id, text)

        def status_func(text):
            self.send_status(raw_user_id, text)

        # Run Agent in a separate thread — use session_id for session management
        thread = threading.Thread(
            target=run_agent_for_message,
            args=(session_id, user_text, reply_func, status_func, self.name),
            kwargs={"channel_user_id": raw_user_id},
            daemon=True,
        )
        thread.start()
