"""
Scheduler tools — Agent-facing tools for creating and managing scheduled tasks.

These tools are registered via the @tool decorator system.
The user_id and channel_name are automatically captured from the execution context
(set by agent_runner), so the Agent doesn't need to guess or ask for them.
"""

from tools.registry import tool
from context import get_context

# ── Helper ──

def _require_context():
    """Ensure we have execution context (user_id + channel_name)."""
    ctx = get_context()
    if not ctx:
        return None, "Error: Scheduled tasks can only be created from an IM channel (Feishu/Telegram). This feature is not available in CLI mode."
    return ctx, None


# ── Tools ──

@tool(
    name="create_scheduled_task",
    tags=["admin"],
    description=(
        "Create a new scheduled task that runs automatically at specified times. "
        "When the task fires, a fresh Agent session will execute the task_prompt "
        "and send the result back to the user. "
        "Use standard 5-field cron expressions (minute hour day month weekday). "
        "Examples: '0 9 * * *' = daily at 9:00, '*/30 * * * *' = every 30 minutes, "
        "'0 9 * * 1-5' = weekdays at 9:00, '0 9,18 * * *' = at 9:00 and 18:00."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_name": {
                "type": "string",
                "description": "A short, descriptive name for the task (e.g. 'Daily disk check', 'Weekly report').",
            },
            "cron": {
                "type": "string",
                "description": "Cron expression (5 fields: minute hour day month weekday). E.g. '0 9 * * *' for daily at 9am.",
            },
            "task_prompt": {
                "type": "string",
                "description": "The natural language instruction for the Agent to execute. Be specific and complete — this will run in an independent session without conversation history.",
            },
        },
        "required": ["task_name", "cron", "task_prompt"],
    },
)
def create_scheduled_task(task_name: str, cron: str, task_prompt: str) -> str:
    """Create a scheduled task."""
    ctx, err = _require_context()
    if err:
        return err

    from scheduler.store import create_task
    from scheduler.engine import scheduler

    try:
        task = create_task(
            task_name=task_name,
            cron=cron,
            task_prompt=task_prompt,
            user_id=ctx["user_id"],
            channel_name=ctx["channel_name"],
        )
        scheduler.add_task(task)

        next_run = scheduler.get_next_run(task["id"])
        return (
            f"✅ Scheduled task created successfully!\n"
            f"  ID: {task['id']}\n"
            f"  Name: {task_name}\n"
            f"  Cron: {cron}\n"
            f"  Next run: {next_run or 'calculating...'}"
        )
    except ValueError as e:
        return f"❌ Failed to create task: {str(e)}"


@tool(
    name="list_scheduled_tasks",
    tags=["read"],
    description="List all scheduled tasks for the current user. Shows task ID, name, cron schedule, enabled status, and last run info.",
    parameters={
        "type": "object",
        "properties": {},
    },
)
def list_scheduled_tasks() -> str:
    """List scheduled tasks for the current user."""
    ctx, err = _require_context()
    if err:
        return err

    from scheduler.store import list_tasks
    from scheduler.engine import scheduler

    tasks = list_tasks(user_id=ctx["user_id"])
    if not tasks:
        return "No scheduled tasks found."

    lines = [f"📋 You have {len(tasks)} scheduled task(s):\n"]
    for t in tasks:
        status = "✅ Enabled" if t.get("enabled", True) else "⏸️ Paused"
        next_run = scheduler.get_next_run(t["id"])
        lines.append(
            f"─────────────────────\n"
            f"  ID:        {t['id']}\n"
            f"  Name:      {t['task_name']}\n"
            f"  Cron:      {t['cron']}\n"
            f"  Status:    {status}\n"
            f"  Prompt:    {t['task_prompt'][:100]}{'...' if len(t['task_prompt']) > 100 else ''}\n"
            f"  Next run:  {next_run or 'N/A'}\n"
            f"  Last run:  {t.get('last_run_at') or 'Never'}\n"
            f"  Last result: {(t.get('last_result') or 'N/A')[:100]}"
        )
    return "\n".join(lines)


@tool(
    name="delete_scheduled_task",
    tags=["admin"],
    description="Delete a scheduled task by its ID. The task will be permanently removed.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to delete.",
            },
        },
        "required": ["task_id"],
    },
)
def delete_scheduled_task(task_id: str) -> str:
    """Delete a scheduled task."""
    ctx, err = _require_context()
    if err:
        return err

    from scheduler.store import get_task, delete_task
    from scheduler.engine import scheduler

    # Verify ownership
    task = get_task(task_id)
    if not task:
        return f"❌ Task '{task_id}' not found."
    if task["user_id"] != ctx["user_id"]:
        return f"❌ Task '{task_id}' does not belong to you."

    scheduler.remove_task(task_id)
    delete_task(task_id)
    return f"✅ Task '{task_id}' ({task['task_name']}) has been deleted."


@tool(
    name="update_scheduled_task",
    tags=["admin"],
    description="Update a scheduled task's properties. You can change the cron schedule, task prompt, task name, or enable/disable it.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to update.",
            },
            "cron": {
                "type": "string",
                "description": "New cron expression (optional).",
            },
            "task_prompt": {
                "type": "string",
                "description": "New task prompt (optional).",
            },
            "task_name": {
                "type": "string",
                "description": "New task name (optional).",
            },
            "enabled": {
                "type": "boolean",
                "description": "Set to false to pause the task, true to resume it (optional).",
            },
        },
        "required": ["task_id"],
    },
)
def update_scheduled_task(task_id: str, cron: str = None,
                          task_prompt: str = None, task_name: str = None,
                          enabled: bool = None) -> str:
    """Update a scheduled task."""
    ctx, err = _require_context()
    if err:
        return err

    from scheduler.store import get_task, update_task
    from scheduler.engine import scheduler

    # Verify ownership
    task = get_task(task_id)
    if not task:
        return f"❌ Task '{task_id}' not found."
    if task["user_id"] != ctx["user_id"]:
        return f"❌ Task '{task_id}' does not belong to you."

    # Build updates
    updates = {}
    if cron is not None:
        updates["cron"] = cron
    if task_prompt is not None:
        updates["task_prompt"] = task_prompt
    if task_name is not None:
        updates["task_name"] = task_name
    if enabled is not None:
        updates["enabled"] = enabled

    if not updates:
        return "No changes specified."

    try:
        # Update persistent store
        updated = update_task(task_id, **updates)
        if not updated:
            return f"❌ Failed to update task '{task_id}'."

        # Update APScheduler
        if cron is not None or enabled is not None:
            scheduler.update_task_schedule(
                task_id,
                cron=cron,
                enabled=enabled,
            )

        parts = ["✅ Task updated:"]
        if task_name is not None:
            parts.append(f"  Name: {task_name}")
        if cron is not None:
            next_run = scheduler.get_next_run(task_id)
            parts.append(f"  Cron: {cron} (next run: {next_run or 'calculating...'})")
        if task_prompt is not None:
            parts.append(f"  Prompt: {task_prompt[:100]}...")
        if enabled is not None:
            parts.append(f"  Status: {'✅ Enabled' if enabled else '⏸️ Paused'}")
        return "\n".join(parts)

    except ValueError as e:
        return f"❌ Update failed: {str(e)}"
