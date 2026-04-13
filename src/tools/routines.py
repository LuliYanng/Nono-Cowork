"""
Routines — unified agent tools for managing all automated workflows.

"Routines" is the unified concept covering:
  - Cron tasks: time-based scheduled execution (e.g., daily at 9am)
  - Triggers: event-driven execution via Composio (e.g., new Gmail message)
  - File-drop: file sync event-driven execution (e.g., user drops file in ~/Sync/翻译/)

This module provides 4 tools:
  1. list_routines     — unified list of all cron + trigger + file_drop routines
  2. create_routine    — unified creation (type=cron|trigger|file_drop)
  3. update_routine    — unified update by ID (auto-detects type)
  4. manage_routine    — unified actions: delete, toggle, run

ID-based auto-routing:
  - trigger IDs start with "ti_" (e.g., "ti_FuB_pbwn2kMc")
  - file_drop IDs start with "fd_" (e.g., "fd_a1b2c3d4e5")
  - cron IDs are 12-char hex strings (e.g., "f8572599337a")
"""

import json
import logging

from tools.registry import tool
from context import get_context

logger = logging.getLogger("tools.routines")


def _detect_routine_type(id: str) -> str:
    """Detect routine type from its ID prefix."""
    if id.startswith("ti_"):
        return "trigger"
    if id.startswith("fd_"):
        return "file_drop"
    return "cron"


def _require_context():
    """Ensure we have execution context (user_id + channel_name)."""
    ctx = get_context()
    if not ctx:
        return None, (
            "Error: Routines can only be managed from an IM channel "
            "(Desktop/Feishu/Telegram). Not available in CLI mode."
        )
    return ctx, None


def _channel_user_id(ctx: dict) -> str:
    """Resolve channel-native user ID from execution context."""
    return ctx.get("channel_user_id") or ctx.get("user_id", "")


# ─────────────────────────────────────────────
# Tool 1: list_routines
# ─────────────────────────────────────────────

@tool(
    name="list_routines",
    tags=["read"],
    description=(
        "List all routines (scheduled tasks + event triggers). "
        "Shows a unified view of every automation with its type, "
        "schedule/trigger, status, prompt preview, model, and last run info. "
        "Use this when the user asks about their automations, scheduled tasks, "
        "or active triggers."
    ),
    parameters={"type": "object", "properties": {}},
)
def list_routines() -> str:
    """List all routines: cron tasks + trigger recipes, merged into a unified view."""
    items = []

    # ── Cron tasks ──
    try:
        from automations.scheduler.store import list_tasks
        from automations.scheduler.engine import scheduler

        tasks = list_tasks()  # All tasks, no user filter (single-user system)
        for t in tasks:
            next_run = scheduler.get_next_run(t["id"])
            status = "✅ Enabled" if t.get("enabled", True) else "⏸️ Paused"
            items.append(
                f"🕐 CRON ─────────────────────\n"
                f"  ID:       {t['id']}\n"
                f"  Name:     {t['task_name']}\n"
                f"  Cron:     {t['cron']}\n"
                f"  Status:   {status}\n"
                f"  Model:    {t.get('model') or '(system default)'}\n"
                f"  Notify:   {', '.join(t.get('notify_channels') or []) or '(desktop only)'}\n"
                f"  Prompt:   {t['task_prompt'][:150]}{'...' if len(t['task_prompt']) > 150 else ''}\n"
                f"  Next run: {next_run or 'N/A'}\n"
                f"  Last run: {t.get('last_run_at') or 'Never'}\n"
                f"  Result:   {(t.get('last_result') or 'N/A')[:100]}"
            )
    except Exception as e:
        logger.warning("Failed to load cron tasks: %s", e)

    # ── Trigger recipes ──
    try:
        from automations.composio_triggers import _load_recipes, is_enabled as composio_enabled

        if composio_enabled():
            recipes = _load_recipes()

            # Fetch live active status from Composio
            active_ids = set()
            try:
                from composio import Composio
                client = Composio()
                active_resp = client.triggers.list_active()
                for t in getattr(active_resp, 'items', active_resp):
                    tid = getattr(t, "id", None) or getattr(t, "trigger_id", str(t))
                    active_ids.add(tid)
            except Exception:
                pass

            for trigger_id, recipe in recipes.items():
                is_active = trigger_id in active_ids
                status = "✅ Active (Composio)" if is_active else "⏸️ Disabled"
                prompt = recipe.get("agent_prompt") or ""
                items.append(
                    f"⚡ TRIGGER ──────────────────\n"
                    f"  ID:       {trigger_id}\n"
                    f"  Slug:     {recipe.get('trigger_slug', '?')}\n"
                    f"  Status:   {status}\n"
                    f"  Model:    {recipe.get('model') or '(system default)'}\n"
                    f"  Prompt:   {prompt[:150]}{'...' if len(prompt) > 150 else ''}\n"
                    f"  Config:   {json.dumps(recipe.get('trigger_config') or {}, ensure_ascii=False)}\n"
                    f"  Created:  {recipe.get('created_at', '?')}"
                )
    except Exception as e:
        logger.warning("Failed to load trigger recipes: %s", e)

    # ── File-drop rules ──
    try:
        from automations.file_drop import list_rules
        rules = list_rules()
        for r in rules:
            status = "✅ Enabled" if r.get("enabled", True) else "⏸️ Paused"
            prompt = r.get("agent_prompt") or ""
            actions_str = ", ".join(r.get("actions", ["added", "modified"]))
            items.append(
                f"📁 FILE-DROP ────────────────\n"
                f"  ID:       {r['id']}\n"
                f"  Name:     {r['name']}\n"
                f"  Pattern:  {r['path_pattern']}\n"
                f"  Actions:  {actions_str}\n"
                f"  Status:   {status}\n"
                f"  Model:    {r.get('model') or '(system default)'}\n"
                f"  Prompt:   {prompt[:150]}{'...' if len(prompt) > 150 else ''}\n"
                f"  Created:  {r.get('created_at', '?')}"
            )
    except Exception as e:
        logger.warning("Failed to load file-drop rules: %s", e)

    if not items:
        return "No routines found. Use create_routine to set up a new one."

    return f"📋 You have {len(items)} routine(s):\n\n" + "\n\n".join(items)


# ─────────────────────────────────────────────
# Tool 2: create_routine
# ─────────────────────────────────────────────

@tool(
    name="create_routine",
    tags=["admin"],
    description=(
        "Create a new routine — scheduled task (cron), event trigger, or file-drop automation. "
        "\n\n"
        "For type='cron': Creates a time-based scheduled task. Provide `cron` (cron expression) "
        "and `prompt` (instructions for the agent). "
        "Examples: '0 9 * * *' = daily 9am, '*/30 * * * *' = every 30min, '0 9 * * 1-5' = weekdays 9am. "
        "\n\n"
        "For type='trigger': Creates an event-driven trigger via Composio. Provide `trigger_slug` "
        "(use composio_list_triggers to find it) and `prompt` (processing rules for the agent). "
        "Include [SKIP] rules to filter unimportant events."
        "\n\n"
        "For type='file_drop': Creates a file-sync-event automation. When a user drops/modifies "
        "a file matching `path_pattern` in their sync folder, a disposable agent runs your `prompt`. "
        "The path_pattern uses glob syntax relative to the sync folder root "
        "(e.g., '翻译/*' matches any file in the 翻译/ folder, '**/*.pdf' matches all PDFs). "
        "The agent receives the file path and can read/process/move the file."
    ),
    parameters={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["cron", "trigger", "file_drop"],
                "description": (
                    "Type of routine: 'cron' for scheduled tasks, 'trigger' for Composio event-driven, "
                    "'file_drop' for file sync event-driven."
                ),
            },
            "name": {
                "type": "string",
                "description": "Short descriptive name (e.g., 'Daily disk check', 'Gmail monitor', 'Auto translate').",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Detailed instructions for the agent that executes this routine. "
                    "Be specific and complete — runs in an independent session without conversation history. "
                    "For triggers/file_drop, include filtering rules (use [SKIP] to ignore unimportant events). "
                    "For file_drop, the agent receives the file path in abs_path and can read/process it."
                ),
            },
            "cron": {
                "type": "string",
                "description": "Cron expression (5 fields: minute hour day month weekday). Required for type='cron'.",
            },
            "trigger_slug": {
                "type": "string",
                "description": (
                    "Trigger type slug (e.g., 'GMAIL_NEW_GMAIL_MESSAGE', 'GITHUB_COMMIT_EVENT'). "
                    "Required for type='trigger'. Use composio_list_triggers to discover available slugs."
                ),
            },
            "trigger_config": {
                "type": "object",
                "description": "Optional configuration for the trigger (e.g., {\"interval\": 1, \"labelIds\": \"INBOX\"}).",
            },
            "path_pattern": {
                "type": "string",
                "description": (
                    "Glob pattern for file-drop matching (relative to sync folder root). "
                    "Required for type='file_drop'. "
                    "Examples: '翻译/*' (any file in 翻译/), 'inbox/**/*' (anything under inbox/ recursively), "
                    "'报销/*.pdf' (PDFs in 报销/), '**/*.jpg' (all JPGs anywhere)."
                ),
            },
            "file_actions": {
                "type": "array",
                "items": {"type": "string", "enum": ["added", "modified", "deleted"]},
                "description": (
                    "Which file actions trigger this rule. Default: ['added', 'modified']. "
                    "Use ['added'] to only trigger on new files, not modifications."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "LLM model for execution. Choose based on task complexity: "
                    "fast/cheap (e.g., 'gemini/gemini-3-flash-preview') for simple filtering, "
                    "stronger (e.g., 'gemini/gemini-3.1-pro-preview') for complex analysis. "
                    "Leave empty to use system default."
                ),
            },
            "tool_access": {
                "type": "string",
                "description": (
                    "Permission level: 'full' (all tools, default), 'read_only' (only read), "
                    "'read_write' (read+write, no shell), 'safe' (read+write+network, no shell)."
                ),
                "enum": ["read_only", "read_write", "safe", "full"],
            },
            "notify_channels": {
                "type": "array",
                "items": {"type": "string", "enum": ["feishu", "telegram"]},
                "description": (
                    "Channels to push task results to as notifications. "
                    "By default, results only appear in Desktop notifications. "
                    "Add 'feishu' and/or 'telegram' to also push a summary to those IM apps. "
                    "Example: ['feishu'] to get notified on Feishu when the task completes."
                ),
            },
        },
        "required": ["type", "name", "prompt"],
    },
)
def create_routine(type: str, name: str, prompt: str,
                   cron: str = None, trigger_slug: str = None,
                   trigger_config: dict = None, path_pattern: str = None,
                   file_actions: list[str] = None, model: str = "",
                   tool_access: str = "full",
                   notify_channels: list[str] = None) -> str:
    """Create a new routine."""
    ctx, err = _require_context()
    if err:
        return err

    if type == "cron":
        return _create_cron_routine(name, prompt, cron, model, tool_access, ctx,
                                    notify_channels=notify_channels)
    elif type == "trigger":
        return _create_trigger_routine(name, prompt, trigger_slug, trigger_config,
                                       model, tool_access, ctx)
    elif type == "file_drop":
        return _create_file_drop_routine(name, prompt, path_pattern, file_actions,
                                         model, tool_access, ctx)
    else:
        return f"❌ Unknown routine type: '{type}'. Use 'cron', 'trigger', or 'file_drop'."


def _create_cron_routine(name, prompt, cron, model, tool_access, ctx,
                         notify_channels=None) -> str:
    """Create a cron-based routine."""
    if not cron:
        return "❌ Missing `cron` parameter for cron routine."

    from automations.scheduler.store import create_task
    from automations.scheduler.engine import scheduler

    try:
        task = create_task(
            task_name=name,
            cron=cron,
            task_prompt=prompt,
            channel_user_id=_channel_user_id(ctx),
            channel_name=ctx["channel_name"],
            tool_access=tool_access,
            model=model,
            notify_channels=notify_channels,
        )
        scheduler.add_task(task)

        next_run = scheduler.get_next_run(task["id"])
        notify_str = ', '.join(notify_channels) if notify_channels else '(desktop only)'
        return (
            f"✅ Cron routine created!\n"
            f"  ID:       {task['id']}\n"
            f"  Name:     {name}\n"
            f"  Cron:     {cron}\n"
            f"  Model:    {model or '(system default)'}\n"
            f"  Notify:   {notify_str}\n"
            f"  Next run: {next_run or 'calculating...'}"
        )
    except ValueError as e:
        return f"❌ Failed to create cron routine: {e}"


def _create_trigger_routine(name, prompt, trigger_slug, trigger_config,
                            model, tool_access, ctx) -> str:
    """Create a trigger-based routine."""
    if not trigger_slug:
        return "❌ Missing `trigger_slug` parameter for trigger routine. Use composio_list_triggers to find available slugs."

    from automations.composio_triggers import create_trigger, is_enabled

    if not is_enabled():
        return "❌ Composio is not enabled. Set COMPOSIO_API_KEY in .env."

    result = create_trigger(
        trigger_slug=trigger_slug,
        agent_prompt=prompt,
        trigger_config=trigger_config,
        model=model,
        tool_access=tool_access,
    )

    # Parse result and add name context
    try:
        data = json.loads(result)
        if data.get("success"):
            return (
                f"✅ Trigger routine created!\n"
                f"  ID:       {data.get('trigger_id', '?')}\n"
                f"  Name:     {name}\n"
                f"  Slug:     {trigger_slug}\n"
                f"  Model:    {model or '(system default)'}\n"
                f"  Message:  {data.get('message', '')}"
            )
        else:
            return f"❌ Failed to create trigger routine: {data.get('error', 'Unknown error')}"
    except (json.JSONDecodeError, TypeError):
        return result


def _create_file_drop_routine(name, prompt, path_pattern, file_actions,
                              model, tool_access, ctx) -> str:
    """Create a file-drop routine."""
    if not path_pattern:
        return "❌ Missing `path_pattern` parameter for file_drop routine. Example: '翻译/*' or '报销/*.pdf'"

    from automations.file_drop import create_rule

    rule = create_rule(
        name=name,
        path_pattern=path_pattern,
        agent_prompt=prompt,
        channel_user_id=_channel_user_id(ctx),
        channel_name=ctx["channel_name"],
        model=model,
        tool_access=tool_access,
        actions=file_actions,
    )

    actions_str = ", ".join(rule.get("actions", []))
    return (
        f"✅ File-drop routine created!\n"
        f"  ID:       {rule['id']}\n"
        f"  Name:     {name}\n"
        f"  Pattern:  {path_pattern}\n"
        f"  Actions:  {actions_str}\n"
        f"  Model:    {model or '(system default)'}\n"
        f"  The agent will automatically process files matching this pattern "
        f"when they appear in your sync folder."
    )


# ─────────────────────────────────────────────
# Tool 3: update_routine
# ─────────────────────────────────────────────

@tool(
    name="update_routine",
    tags=["admin"],
    description=(
        "Update an existing routine's properties by ID. "
        "The routine type (cron/trigger) is auto-detected from the ID. "
        "For cron: can update name, cron, prompt, model, enabled. "
        "For trigger: can update prompt, model."
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Routine ID (from list_routines output).",
            },
            "name": {
                "type": "string",
                "description": "New name (cron only).",
            },
            "cron": {
                "type": "string",
                "description": "New cron expression (cron only).",
            },
            "prompt": {
                "type": "string",
                "description": "New agent prompt / task instructions.",
            },
            "model": {
                "type": "string",
                "description": "New LLM model. Empty string = system default.",
            },
            "enabled": {
                "type": "boolean",
                "description": "Enable/disable (cron only; for triggers use manage_routine toggle).",
            },
            "notify_channels": {
                "type": "array",
                "items": {"type": "string", "enum": ["feishu", "telegram"]},
                "description": (
                    "Update which channels receive result notifications. "
                    "Set to ['feishu'] or ['feishu', 'telegram']. "
                    "Set to [] (empty array) to disable IM notifications (desktop only)."
                ),
            },
        },
        "required": ["id"],
    },
)
def update_routine(id: str, name: str = None, cron: str = None,
                   prompt: str = None, model: str = None,
                   enabled: bool = None,
                   notify_channels: list[str] = None) -> str:
    """Update an existing routine by ID."""
    rtype = _detect_routine_type(id)
    if rtype == "trigger":
        return _update_trigger_routine(id, prompt, model)
    elif rtype == "file_drop":
        return _update_file_drop_routine(id, name, prompt, model, enabled)
    else:
        return _update_cron_routine(id, name, cron, prompt, model, enabled,
                                    notify_channels=notify_channels)


def _update_cron_routine(id, name, cron, prompt, model, enabled,
                         notify_channels=None) -> str:
    """Update a cron routine."""
    from automations.scheduler.store import get_task, update_task
    from automations.scheduler.engine import scheduler

    task = get_task(id)
    if not task:
        return f"❌ Cron routine '{id}' not found."

    updates = {}
    if name is not None:
        updates["task_name"] = name
    if cron is not None:
        updates["cron"] = cron
    if prompt is not None:
        updates["task_prompt"] = prompt
    if model is not None:
        updates["model"] = model
    if enabled is not None:
        updates["enabled"] = enabled
    if notify_channels is not None:
        # Allow empty list to clear, or list of channel names
        updates["notify_channels"] = notify_channels if notify_channels else None

    if not updates:
        return "No changes specified."

    try:
        updated = update_task(id, **updates)
        if not updated:
            return f"❌ Failed to update routine '{id}'."

        # Update APScheduler if schedule/status changed
        if cron is not None or enabled is not None:
            scheduler.update_task_schedule(id, cron=cron, enabled=enabled)

        parts = [f"✅ Routine '{id}' updated:"]
        if name is not None:
            parts.append(f"  Name:   {name}")
        if cron is not None:
            next_run = scheduler.get_next_run(id)
            parts.append(f"  Cron:   {cron} (next: {next_run or '...'})")
        if prompt is not None:
            parts.append(f"  Prompt: {prompt[:100]}...")
        if model is not None:
            parts.append(f"  Model:  {model or '(system default)'}")
        if enabled is not None:
            parts.append(f"  Status: {'✅ Enabled' if enabled else '⏸️ Paused'}")
        if notify_channels is not None:
            nc_str = ', '.join(notify_channels) if notify_channels else '(desktop only)'
            parts.append(f"  Notify: {nc_str}")
        return "\n".join(parts)

    except ValueError as e:
        return f"❌ Update failed: {e}"


def _update_trigger_routine(id, prompt, model) -> str:
    """Update a trigger routine's recipe."""
    from automations.composio_triggers import _load_recipes, _save_recipes

    recipes = _load_recipes()
    recipe = recipes.get(id)
    if not recipe:
        return f"❌ Trigger routine '{id}' not found in local recipes."

    changed = []
    if prompt is not None:
        recipe["agent_prompt"] = prompt
        changed.append(f"  Prompt: {prompt[:100]}...")
    if model is not None:
        recipe["model"] = model
        changed.append(f"  Model:  {model or '(system default)'}")

    if not changed:
        return "No changes specified."

    recipes[id] = recipe
    _save_recipes(recipes)

    return f"✅ Routine '{id}' updated:\n" + "\n".join(changed)


def _update_file_drop_routine(id, name, prompt, model, enabled) -> str:
    """Update a file-drop routine."""
    from automations.file_drop import get_rule, update_rule

    rule = get_rule(id)
    if not rule:
        return f"❌ File-drop routine '{id}' not found."

    updates = {}
    changed = []
    if name is not None:
        updates["name"] = name
        changed.append(f"  Name:   {name}")
    if prompt is not None:
        updates["agent_prompt"] = prompt
        changed.append(f"  Prompt: {prompt[:100]}...")
    if model is not None:
        updates["model"] = model
        changed.append(f"  Model:  {model or '(system default)'}")
    if enabled is not None:
        updates["enabled"] = enabled
        changed.append(f"  Status: {'✅ Enabled' if enabled else '⏸️ Paused'}")

    if not updates:
        return "No changes specified."

    updated = update_rule(id, **updates)
    if not updated:
        return f"❌ Failed to update routine '{id}'."

    return f"✅ Routine '{id}' updated:\n" + "\n".join(changed)


# ─────────────────────────────────────────────
# Tool 4: manage_routine
# ─────────────────────────────────────────────

@tool(
    name="manage_routine",
    tags=["admin"],
    description=(
        "Perform an action on an existing routine. "
        "Actions: "
        "'delete' — permanently remove the routine. "
        "'toggle' — enable ↔ disable (pauses cron / disables trigger on Composio). "
        "'run' — manually execute once right now (cron only)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Routine ID (from list_routines output).",
            },
            "action": {
                "type": "string",
                "enum": ["delete", "toggle", "run"],
                "description": "Action to perform on the routine.",
            },
        },
        "required": ["id", "action"],
    },
)
def manage_routine(id: str, action: str) -> str:
    """Perform an action on a routine."""
    if action == "delete":
        return _delete_routine(id)
    elif action == "toggle":
        return _toggle_routine(id)
    elif action == "run":
        return _run_routine(id)
    else:
        return f"❌ Unknown action: '{action}'. Use 'delete', 'toggle', or 'run'."


def _delete_routine(id) -> str:
    """Delete a routine (cron, trigger, or file_drop)."""
    rtype = _detect_routine_type(id)

    if rtype == "trigger":
        from automations.composio_triggers import delete_trigger, is_enabled
        if not is_enabled():
            return "❌ Composio is not enabled."
        result = delete_trigger(id)
        try:
            data = json.loads(result)
            if data.get("success"):
                return f"✅ Trigger routine '{id}' deleted."
            return f"❌ Failed to delete: {data.get('error', 'Unknown error')}"
        except (json.JSONDecodeError, TypeError):
            return result

    elif rtype == "file_drop":
        from automations.file_drop import get_rule, delete_rule
        rule = get_rule(id)
        if not rule:
            return f"❌ File-drop routine '{id}' not found."
        delete_rule(id)
        return f"✅ File-drop routine '{id}' ({rule['name']}) deleted."

    else:
        from automations.scheduler.store import get_task, delete_task
        from automations.scheduler.engine import scheduler

        task = get_task(id)
        if not task:
            return f"❌ Cron routine '{id}' not found."

        scheduler.remove_task(id)
        delete_task(id)
        return f"✅ Cron routine '{id}' ({task['task_name']}) deleted."


def _toggle_routine(id) -> str:
    """Toggle a routine's enabled state."""
    rtype = _detect_routine_type(id)
    if rtype == "trigger":
        return _toggle_trigger(id)
    elif rtype == "file_drop":
        return _toggle_file_drop(id)
    else:
        return _toggle_cron(id)


def _toggle_cron(id) -> str:
    """Toggle a cron routine on/off."""
    from automations.scheduler.store import get_task, update_task
    from automations.scheduler.engine import scheduler

    task = get_task(id)
    if not task:
        return f"❌ Cron routine '{id}' not found."

    new_state = not task.get("enabled", True)
    update_task(id, enabled=new_state)
    scheduler.update_task_schedule(id, enabled=new_state)

    status = "✅ Enabled" if new_state else "⏸️ Paused"
    next_info = ""
    if new_state:
        next_run = scheduler.get_next_run(id)
        next_info = f"\n  Next run: {next_run or '...'}"

    return f"✅ Routine '{id}' ({task['task_name']}) → {status}{next_info}"


def _toggle_trigger(id) -> str:
    """Toggle a trigger routine (enable ↔ disable on Composio)."""
    from automations.composio_triggers import _load_recipes, _save_recipes, is_enabled

    if not is_enabled():
        return "❌ Composio is not enabled."

    recipes = _load_recipes()
    recipe = recipes.get(id)
    if not recipe:
        return f"❌ Trigger routine '{id}' not found."

    # Check current active status
    is_active = False
    try:
        from composio import Composio
        client = Composio()
        active_resp = client.triggers.list_active()
        active_ids = {
            getattr(t, "id", None) or getattr(t, "trigger_id", str(t))
            for t in getattr(active_resp, 'items', active_resp)
        }
        is_active = id in active_ids
    except Exception as e:
        logger.warning("Could not check trigger status: %s", e)

    if is_active:
        # Disable
        try:
            from composio import Composio
            client = Composio()
            client.triggers.disable(trigger_id=id)
            return f"✅ Trigger routine '{id}' ({recipe.get('trigger_slug', '?')}) → ⏸️ Disabled"
        except Exception as e:
            return f"❌ Failed to disable: {e}"
    else:
        # Re-enable: create new trigger on Composio with same config
        try:
            from composio import Composio
            from config import COMPOSIO_USER_ID
            client = Composio()
            # Use composio_user_id (Composio-side), NOT user_id (channel-side)
            cid = recipe.get("composio_user_id") or COMPOSIO_USER_ID
            trigger = client.triggers.create(
                slug=recipe["trigger_slug"],
                user_id=cid,
                trigger_config=recipe.get("trigger_config") or {},
            )
            new_id = getattr(trigger, "id", None) or getattr(trigger, "trigger_id", str(trigger))

            # If Composio assigned a new ID, migrate the recipe
            if new_id != id:
                recipes[new_id] = recipe
                recipes[new_id]["trigger_id"] = new_id
                del recipes[id]
                _save_recipes(recipes)
                return (
                    f"✅ Trigger routine re-enabled → ✅ Active\n"
                    f"  ⚠️ New ID: {new_id} (Composio reassigned)\n"
                    f"  Slug: {recipe.get('trigger_slug', '?')}"
                )

            return f"✅ Trigger routine '{id}' ({recipe.get('trigger_slug', '?')}) → ✅ Active"
        except Exception as e:
            err_msg = str(e)
            if "connected account" in err_msg.lower() or "no connected" in err_msg.lower():
                return (
                    f"❌ Connected account missing or expired for '{recipe.get('trigger_slug', '?')}'.\n"
                    f"  Trigger: {id}\n"
                    f"  Please reconnect the app in Composio and try again.\n"
                    f"  Details: {err_msg}"
                )
            return f"❌ Failed to re-enable: {e}"


def _toggle_file_drop(id) -> str:
    """Toggle a file-drop routine on/off."""
    from automations.file_drop import get_rule, update_rule

    rule = get_rule(id)
    if not rule:
        return f"❌ File-drop routine '{id}' not found."

    new_state = not rule.get("enabled", True)
    update_rule(id, enabled=new_state)

    status = "✅ Enabled" if new_state else "⏸️ Paused"
    return f"✅ Routine '{id}' ({rule['name']}) → {status}"


def _run_routine(id) -> str:
    """Manually execute a cron routine once."""
    rtype = _detect_routine_type(id)
    if rtype != "cron":
        return f"❌ Manual execution is only available for cron routines, not {rtype}s."

    from automations.scheduler.store import get_task
    from automations.scheduler.executor import execute_task

    task = get_task(id)
    if not task:
        return f"❌ Cron routine '{id}' not found."

    # Execute in background thread (async — result delivered via notification)
    execute_task(task)
    return (
        f"✅ Routine '{id}' ({task['task_name']}) is executing now.\n"
        f"Results will be delivered as a notification when complete."
    )
