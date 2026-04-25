"""
Agent runner — shared Agent invocation logic for all IM channels

Responsibilities: session management, concurrency control, calling agent_loop, extracting replies
"""
import logging
from core.session import sessions
from context import set_context, clear_context
from integrations.syncthing_watcher import get_sync_context

logger = logging.getLogger("agent_runner")


def run_agent_for_message(user_id: str, user_text: str,
                          reply_func, status_func=None,
                          channel_name: str = "unknown",
                          on_event_hook=None,
                          channel_user_id: str | None = None,
                          images: list[dict] | None = None):
    """
    Run Agent in the calling thread and reply via callback functions.

    All IM channels share this function, passing in different reply_func callbacks.

    Args:
        user_id: Session user identifier
        channel_user_id: Native channel recipient ID (open_id/chat_id)
        user_text: User message text
        reply_func: Callback function to send the result: reply_func(text)
        status_func: Optional callback for status updates: status_func(text)
        channel_name: Channel name (for logging)
        on_event_hook: Optional callback for structured agent events: on_event_hook(evt)
        images: Optional list of image dicts [{"data": "data:image/png;base64,...", "filename": "..."}]
                for multimodal LLM input
    """
    from core.agent import agent_loop
    from logger import log_event

    lock = sessions.get_lock(user_id)

    # Prevent concurrent execution for the same user
    if not lock.acquire(blocking=False):
        reply_func("⏳ The previous task is still running. Please wait for it to finish.")
        return

    # Set execution context so tools can access user_id, channel_name, and callbacks
    channel_user_id = channel_user_id or user_id
    set_context(user_id=user_id, channel_name=channel_name,
                check_stop=lambda: sessions.is_stopped(user_id),
                status_func=status_func,
                subagent_check_stop=lambda: (
                    sessions.is_subagent_stopped(user_id) or sessions.is_stopped(user_id)
                ),
                channel_user_id=channel_user_id)

    # Clear any previous stop flag before starting
    sessions.clear_stop(user_id)

    try:
        session = sessions.get_or_create(user_id)
        history = session["history"]
        token_stats = session["token_stats"]
        log_file = session["log_file"]  # Session-level log file
        model_override = session.get("model_override")  # Per-session model

        # Scope sync context to the session's workspace (if any).
        # Falls back to all folders when the workspace/folder can't be
        # resolved so behaviour matches the pre-workspace version.
        from core.workspace import resolve_folder_id_for_session
        workspace_folder_id = resolve_folder_id_for_session(session)
        sync_ctx = get_sync_context(folder_id=workspace_folder_id)

        # Mark the workspace active when the user actually sends a message
        if session.get("workspace_id"):
            try:
                from core.workspace import workspaces as _workspaces
                _workspaces.touch(session["workspace_id"])
            except Exception:
                pass

        # Log to journalctl for live debugging
        logger.info("[%s] User: %s", channel_name, user_text)
        if sync_ctx:
            logger.info("[%s] Sync context injected:\n%s", channel_name, sync_ctx)

        # Build user message content — multimodal when images are attached
        if images:
            user_content = [
                {"type": "text", "text": user_text},
            ]
            for img in images:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": img["data"]},
                })
        else:
            user_content = user_text

        # Append ORIGINAL user message to history (this is what gets persisted & shown in frontend)
        history.append({"role": "user", "content": user_content})
        # Update last_active only when user actually sends a message
        sessions.touch_session(user_id)
        # Log the original text to session log file
        log_event(log_file, {
            "type": f"{channel_name}_message",
            "user_id": user_id,
            "content": user_text,
            "image_count": len(images) if images else 0,
        })

        # Temporarily inject sync context into the last user message for the LLM call only
        if sync_ctx:
            augmented_text = f"{sync_ctx}\n\n{user_text}"
            if images:
                # Multimodal: replace the text part, keep images
                augmented_content = [
                    {"type": "text", "text": augmented_text},
                    *[p for p in user_content if p.get("type") == "image_url"],
                ]
                history[-1] = {"role": "user", "content": augmented_content}
            else:
                history[-1] = {"role": "user", "content": augmented_text}

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
                    status_func(f"🔧 {evt['tool_name']}")
            # Forward structured event to external hook (e.g. desktop SSE)
            if on_event_hook:
                on_event_hook(evt)

        # Stop checker: agent_loop calls this to check if /stop was requested
        def check_stop():
            return sessions.is_stopped(user_id)

        # Run Agent
        try:
            updated_history, updated_stats = agent_loop(
                history, log_file, token_stats,
                on_event=on_event,
                check_stop=check_stop,
                model_override=model_override,
            )

            # Restore original user message (strip sync context before persisting)
            if sync_ctx:
                for msg in updated_history:
                    if msg.get("role") != "user":
                        continue
                    c = msg.get("content")
                    if images:
                        # Multimodal: restore to original content array
                        if isinstance(c, list) and any(
                            p.get("type") == "text" and p.get("text") == augmented_text
                            for p in c
                        ):
                            msg["content"] = user_content
                            break
                    else:
                        if c == augmented_text:
                            msg["content"] = user_text
                            break

            session["history"] = updated_history
            session["token_stats"] = updated_stats

            # Persist session to disk after each interaction
            sessions.save_session(user_id)

            # Check if we were stopped
            was_stopped = sessions.is_stopped(user_id)

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

            if was_stopped and not final_reply:
                reply_func("🛑 Task stopped by user.")
            elif final_reply:
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
