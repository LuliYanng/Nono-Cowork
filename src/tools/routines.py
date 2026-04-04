"""
Routines — unified agent tools for managing all automated workflows.

"Routines" is the unified concept covering both:
  - Cron tasks: time-based scheduled execution (e.g., daily at 9am)
  - Triggers: event-driven execution via Composio (e.g., new Gmail message)

This module provides 4 tools:
  1. list_routines     — unified list of all cron + trigger routines
  2. create_routine    — unified creation (type=cron|trigger)
  3. update_routine    — unified update by ID (auto-detects type)
  4. manage_routine    — unified actions: delete, toggle, run

ID-based auto-routing:
  - trigger IDs start with "ti_" (e.g., "ti_FuB_pbwn2kMc")
  - cron IDs are 12-char hex strings (e.g., "f8572599337a")
"""

import json
import logging

from tools.registry import tool
from context import get_context

logger = logging.getLogger("tools.routines")


def _is_trigger_id(id: str) -> bool:
    """Detect if an ID belongs to a trigger (vs a cron task)."""
    return id.startswith("ti_")


def _require_context():
    """Ensure we have execution context (user_id + channel_name)."""
    ctx = get_context()
    if not ctx:
        return None, (
            "Error: Routines can only be managed from an IM channel "
            "(Desktop/Feishu/Telegram). Not available in CLI mode."
        )
    return ctx, None


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
        from scheduler.store import list_tasks
        from scheduler.engine import scheduler

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
                f"  Prompt:   {t['task_prompt'][:150]}{'...' if len(t['task_prompt']) > 150 else ''}\n"
                f"  Next run: {next_run or 'N/A'}\n"
                f"  Last run: {t.get('last_run_at') or 'Never'}\n"
                f"  Result:   {(t.get('last_result') or 'N/A')[:100]}"
            )
    except Exception as e:
        logger.warning("Failed to load cron tasks: %s", e)

    # ── Trigger recipes ──
    try:
        from composio_triggers import _load_recipes, is_enabled as composio_enabled

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
        "Create a new routine — either a scheduled task (cron) or an event trigger. "
        "\n\n"
        "For type='cron': Creates a time-based scheduled task. Provide `cron` (cron expression) "
        "and `prompt` (instructions for the agent). "
        "Examples: '0 9 * * *' = daily 9am, '*/30 * * * *' = every 30min, '0 9 * * 1-5' = weekdays 9am. "
        "\n\n"
        "For type='trigger': Creates an event-driven trigger via Composio. Provide `trigger_slug` "
        "(use composio_list_triggers to find it) and `prompt` (processing rules for the agent). "
        "Include [SKIP] rules to filter unimportant events."
    ),
    parameters={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["cron", "trigger"],
                "description": "Type of routine: 'cron' for scheduled tasks, 'trigger' for event-driven.",
            },
            "name": {
                "type": "string",
                "description": "Short descriptive name (e.g., 'Daily disk check', 'Gmail monitor').",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Detailed instructions for the agent that executes this routine. "
                    "Be specific and complete — runs in an independent session without conversation history. "
                    "For triggers, include filtering rules (use [SKIP] to ignore unimportant events)."
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
        },
        "required": ["type", "name", "prompt"],
    },
)
def create_routine(type: str, name: str, prompt: str,
                   cron: str = None, trigger_slug: str = None,
                   trigger_config: dict = None, model: str = "",
                   tool_access: str = "full") -> str:
    """Create a new routine."""
    ctx, err = _require_context()
    if err:
        return err

    if type == "cron":
        return _create_cron_routine(name, prompt, cron, model, tool_access, ctx)
    elif type == "trigger":
        return _create_trigger_routine(name, prompt, trigger_slug, trigger_config,
                                       model, tool_access, ctx)
    else:
        return f"❌ Unknown routine type: '{type}'. Use 'cron' or 'trigger'."


def _create_cron_routine(name, prompt, cron, model, tool_access, ctx) -> str:
    """Create a cron-based routine."""
    if not cron:
        return "❌ Missing `cron` parameter for cron routine."

    from scheduler.store import create_task
    from scheduler.engine import scheduler

    try:
        task = create_task(
            task_name=name,
            cron=cron,
            task_prompt=prompt,
            channel_user_id=ctx["user_id"],
            channel_name=ctx["channel_name"],
            tool_access=tool_access,
            model=model,
        )
        scheduler.add_task(task)

        next_run = scheduler.get_next_run(task["id"])
        return (
            f"✅ Cron routine created!\n"
            f"  ID:       {task['id']}\n"
            f"  Name:     {name}\n"
            f"  Cron:     {cron}\n"
            f"  Model:    {model or '(system default)'}\n"
            f"  Next run: {next_run or 'calculating...'}"
        )
    except ValueError as e:
        return f"❌ Failed to create cron routine: {e}"


def _create_trigger_routine(name, prompt, trigger_slug, trigger_config,
                            model, tool_access, ctx) -> str:
    """Create a trigger-based routine."""
    if not trigger_slug:
        return "❌ Missing `trigger_slug` parameter for trigger routine. Use composio_list_triggers to find available slugs."

    from composio_triggers import create_trigger, is_enabled

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
        },
        "required": ["id"],
    },
)
def update_routine(id: str, name: str = None, cron: str = None,
                   prompt: str = None, model: str = None,
                   enabled: bool = None) -> str:
    """Update an existing routine by ID."""
    if _is_trigger_id(id):
        return _update_trigger_routine(id, prompt, model)
    else:
        return _update_cron_routine(id, name, cron, prompt, model, enabled)


def _update_cron_routine(id, name, cron, prompt, model, enabled) -> str:
    """Update a cron routine."""
    from scheduler.store import get_task, update_task
    from scheduler.engine import scheduler

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
        return "\n".join(parts)

    except ValueError as e:
        return f"❌ Update failed: {e}"


def _update_trigger_routine(id, prompt, model) -> str:
    """Update a trigger routine's recipe."""
    from composio_triggers import _load_recipes, _save_recipes

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
    """Delete a routine (cron or trigger)."""
    if _is_trigger_id(id):
        from composio_triggers import delete_trigger, is_enabled
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
    else:
        from scheduler.store import get_task, delete_task
        from scheduler.engine import scheduler

        task = get_task(id)
        if not task:
            return f"❌ Cron routine '{id}' not found."

        scheduler.remove_task(id)
        delete_task(id)
        return f"✅ Cron routine '{id}' ({task['task_name']}) deleted."


def _toggle_routine(id) -> str:
    """Toggle a routine's enabled state."""
    if _is_trigger_id(id):
        return _toggle_trigger(id)
    else:
        return _toggle_cron(id)


def _toggle_cron(id) -> str:
    """Toggle a cron routine on/off."""
    from scheduler.store import get_task, update_task
    from scheduler.engine import scheduler

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
    from composio_triggers import _load_recipes, _save_recipes, is_enabled

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


def _run_routine(id) -> str:
    """Manually execute a cron routine once."""
    if _is_trigger_id(id):
        return "❌ Manual execution is only available for cron routines, not triggers."

    from scheduler.store import get_task
    from scheduler.executor import execute_task

    task = get_task(id)
    if not task:
        return f"❌ Cron routine '{id}' not found."

    # Execute in background thread (async — result delivered via notification)
    execute_task(task)
    return (
        f"✅ Routine '{id}' ({task['task_name']}) is executing now.\n"
        f"Results will be delivered as a notification when complete."
    )
