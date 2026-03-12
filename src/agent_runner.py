"""
Agent runner — shared Agent invocation logic for all IM channels

Responsibilities: session management, concurrency control, calling agent_loop, extracting replies
"""
import logging
from session import sessions

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

        def on_event(evt):
            events.append(evt)
            if status_func and evt["type"] == "tool_call":
                tool_name = evt["tool_name"]
                status_func(f"🔧 Running: {tool_name}...")

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

        except Exception as e:
            logger.error(f"Agent execution error: {e}", exc_info=True)
            reply_func(f"❌ Execution error: {str(e)}")
            log_event(log_file, {"type": "error", "error": str(e)})

    finally:
        lock.release()
