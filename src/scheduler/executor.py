"""
Task executor — runs an independent Agent session for a scheduled task
and stores the result via NotificationStore.
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger("scheduler.executor")


def execute_task(task: dict):
    """Execute a scheduled task in a background thread.

    Creates a fresh Agent session, runs the task's prompt through agent_loop,
    and stores the result via NotificationStore (autonomous session + notification).
    """
    thread = threading.Thread(
        target=_run_task,
        args=(task,),
        daemon=True,
        name=f"sched-{task['id']}",
    )
    thread.start()


def _run_task(task: dict):
    """Internal: run the task synchronously (called inside a thread)."""
    import time
    from agent import agent_loop
    from prompt import make_system_prompt
    from llm import make_empty_token_stats
    from logger import create_log_file, log_event, close_log_file
    from channels.registry import get_channel
    from scheduler.store import update_task
    from session import _serialize_history

    task_id = task["id"]
    task_name = task["task_name"]
    task_prompt = task["task_prompt"]
    user_id = task["user_id"]
    channel_name = task["channel_name"]

    logger.info(f"Executing scheduled task: {task_id} ({task_name})")

    # Notify user that a scheduled task is starting (best-effort)
    channel = get_channel(channel_name)
    if not channel:
        from channels.registry import list_channels
        for fallback_name in list_channels():
            channel = get_channel(fallback_name)
            if channel:
                break

    if channel:
        channel.send_status(user_id, f"⏰ Scheduled task 「{task_name}」 is running...")

    # Create a fresh Agent session for this task
    log_file = create_log_file()
    log_event(log_file, {
        "type": "scheduled_task_start",
        "task_id": task_id,
        "task_name": task_name,
        "user_id": user_id,
    })

    system_prompt = make_system_prompt()
    # Inject task context into the conversation
    task_context = (
        f"[SCHEDULED TASK]\n"
        f"This is an automated scheduled task. Task name: {task_name}\n"
        f"Please execute the following task and provide a clear result summary:\n\n"
        f"{task_prompt}"
    )

    history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_context},
    ]
    token_stats = make_empty_token_stats()

    start_time = time.time()
    try:
        # Collect events to extract final reply
        events = []

        def on_event(evt):
            events.append(evt)

        updated_history, updated_stats = agent_loop(
            history, log_file, token_stats, on_event=on_event
        )

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

        if not final_reply:
            final_reply = "✅ Task completed (no text output)"

        # Extract usage report and append as footer
        usage_line = ""
        for evt in reversed(events):
            if evt["type"] == "usage_report":
                usage_line = evt.get("summary", "")
                break

        if usage_line:
            final_reply += f"\n\n---\n{usage_line}"

        duration = time.time() - start_time

        # Store via NotificationStore
        serialized = _serialize_history(updated_history)
        _store_notification(
            task_id=task_id,
            task_name=task_name,
            user_id=user_id,
            body=final_reply,
            history=serialized,
            token_stats=dict(updated_stats),
            system_prompt=system_prompt,
            duration=duration,
            channel_name=channel_name,
        )

        # Update task record
        update_task(
            task_id,
            last_run_at=datetime.now(timezone.utc).isoformat(),
            last_result=final_reply[:500],  # Truncate for storage
        )

        log_event(log_file, {
            "type": "scheduled_task_complete",
            "task_id": task_id,
            "result_length": len(final_reply),
            "token_stats": dict(updated_stats),
        })

    except Exception as e:
        error_msg = f"❌ Scheduled task 「{task_name}」 failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        # Direct delivery for errors (NotificationStore might also be broken)
        if channel:
            channel.send_reply(user_id, error_msg)
        update_task(
            task_id,
            last_run_at=datetime.now(timezone.utc).isoformat(),
            last_result=f"Error: {str(e)}",
        )
        log_event(log_file, {
            "type": "scheduled_task_error",
            "task_id": task_id,
            "error": str(e),
        })

    finally:
        close_log_file(log_file)


def _store_notification(
    task_id: str,
    task_name: str,
    user_id: str,
    body: str,
    history: list,
    token_stats: dict,
    system_prompt: str,
    duration: float,
    channel_name: str,
):
    """Store scheduled task result via NotificationStore."""
    try:
        from notifications import notification_store
        notification_store.create(
            source_type="schedule",
            source_id=task_id,
            source_name=task_name,
            body=body,
            user_id=user_id,
            history=history,
            token_stats=token_stats,
            agent_provider="self",
            agent_duration_s=duration,
            system_prompt=system_prompt,
        )
    except Exception as e:
        logger.error("Failed to store notification for task %s: %s", task_id, e)
        # Fallback: try direct channel delivery
        try:
            from channels.registry import get_channel, list_channels
            channel = get_channel(channel_name)
            if not channel:
                for name in list_channels():
                    channel = get_channel(name)
                    if channel:
                        break
            if channel:
                header = f"📋 Scheduled Task 「{task_name}」 Result:\n\n"
                channel.send_reply(user_id, header + body)
        except Exception as e2:
            logger.error("Fallback delivery also failed: %s", e2)

