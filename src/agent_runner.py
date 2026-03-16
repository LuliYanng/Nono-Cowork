"""
Agent runner — shared Agent invocation logic for all IM channels

Responsibilities: session management, concurrency control, calling agent_loop, extracting replies
"""
import logging
from session import sessions
from context import set_context, clear_context

logger = logging.getLogger("agent_runner")


def run_agent_for_message(user_id: str, user_text: str,
                          reply_func, status_func=None,
                          channel_name: str = "unknown"):
    """
    Run Agent in the calling thread and reply via callback functions.

    All IM channels share this function, passing in different reply_func callbacks.

    Args:
        user_id: Unique user identifier
        user_text: User message text
        reply_func: Callback function to send the result: reply_func(text)
        status_func: Optional callback for status updates: status_func(text)
        channel_name: Channel name (for logging)
    """
    from agent import agent_loop
    from logger import log_event

    lock = sessions.get_lock(user_id)

    # Prevent concurrent execution for the same user
    if not lock.acquire(blocking=False):
        reply_func("⏳ The previous task is still running. Please wait for it to finish.")
        return

    # Set execution context so tools can access user_id and channel_name
    set_context(user_id=user_id, channel_name=channel_name)

    try:
        session = sessions.get_or_create(user_id)
        history = session["history"]
        token_stats = session["token_stats"]
        log_file = session["log_file"]  # Session-level log file

        # Append user message
        history.append({"role": "user", "content": user_text})
        log_event(log_file, {
            "type": f"{channel_name}_message",
            "user_id": user_id,
            "content": user_text,
        })

        # Agent event collector
        events = []
        narrated_rounds = set()  # Track rounds that already sent a narration

        def on_event(evt):
            events.append(evt)
            round_num = evt.get("round")
            if evt["type"] == "narration" and status_func:
                # LLM's brief narration alongside tool calls — more natural than tool name
                status_func(f"💬 {evt['content']}")
                narrated_rounds.add(round_num)
            elif evt["type"] == "tool_call" and status_func:
                # Only show generic tool status if no narration was sent this round
                if round_num not in narrated_rounds:
                    status_func(f"🔧 Running: {evt['tool_name']}...")

        # Run Agent
        try:
            updated_history, updated_stats = agent_loop(
                history, log_file, token_stats, on_event=on_event
            )
            session["history"] = updated_history
            session["token_stats"] = updated_stats

            # Extract final reply
            final_reply = ""
            for evt in reversed(events):
                if evt["type"] == "final_reply":
                    final_reply = evt["content"]
                    break

            # Fallback: extract from history
            if not final_reply:
                for msg in reversed(updated_history):
                    if isinstance(msg, dict):
                        if msg.get("role") == "assistant" and msg.get("content"):
                            final_reply = msg["content"]
                            break
                    else:
                        if getattr(msg, "role", None) == "assistant" and msg.content:
                            final_reply = msg.content
                            break

            if final_reply:
                reply_func(final_reply)
            else:
                reply_func("✅ Task completed (no text output)")

            # Send usage bar after reply (so it appears below the response)
            if status_func:
                for evt in reversed(events):
                    if evt["type"] == "usage_report":
                        from config import CONTEXT_LIMIT
                        pt = evt.get("prompt_tokens", 0)
                        pct = min(pt / CONTEXT_LIMIT * 100, 100)
                        filled = int(12 * pct / 100)
                        bar = "█" * filled + "░" * (12 - filled)
                        fmt = lambda n: f"{n/1000:.0f}k" if n >= 1000 else str(n)
                        status_func(f"⟨{bar}⟩ {pct:.0f}%  context: {fmt(pt)} / {fmt(CONTEXT_LIMIT)}")
                        break

        except Exception as e:
            logger.error(f"Agent execution error: {e}", exc_info=True)
            reply_func(f"❌ Execution error: {str(e)}")
            log_event(log_file, {"type": "error", "error": str(e)})

    finally:
        clear_context()
        lock.release()
