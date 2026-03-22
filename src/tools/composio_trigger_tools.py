"""
Composio trigger management tools — exposed to the LLM for creating and managing triggers.

These tools let the agent set up event-driven workflows via Composio triggers
(e.g., "notify me when I get a new email", "watch for GitHub commits").
"""

from tools.registry import tool
from config import COMPOSIO_API_KEY


def _is_enabled():
    return bool(COMPOSIO_API_KEY)


@tool(
    name="composio_list_triggers",
    description=(
        "List available trigger types for a given toolkit/app. "
        "Use this to discover what events can be monitored "
        "(e.g., new emails, new GitHub commits, new Slack messages). "
        "Only available when Composio is enabled."
    ),
    parameters={
        "type": "object",
        "properties": {
            "toolkit": {
                "type": "string",
                "description": "The toolkit/app slug (e.g., 'github', 'gmail', 'slack', 'figma').",
            },
        },
        "required": ["toolkit"],
    },
)
def composio_list_triggers(toolkit: str) -> str:
    if not _is_enabled():
        return '{"error": "Composio is not enabled. Set COMPOSIO_API_KEY in .env."}'
    from composio_triggers import list_available_triggers
    return list_available_triggers(toolkit)


@tool(
    name="composio_create_trigger",
    description=(
        "Create a new trigger to monitor events from a connected app. "
        "The trigger will automatically send events to the agent when they occur. "
        "First use composio_list_triggers to find the right trigger slug, "
        "then create it with any required configuration. "
        "Example: create a GMAIL_NEW_GMAIL_MESSAGE trigger to get notified of new emails."
    ),
    parameters={
        "type": "object",
        "properties": {
            "trigger_slug": {
                "type": "string",
                "description": "The trigger type slug (e.g., 'GMAIL_NEW_GMAIL_MESSAGE', 'GITHUB_COMMIT_EVENT').",
            },
            "trigger_config": {
                "type": "object",
                "description": "Optional configuration for the trigger. Use composio_list_triggers to see required config fields.",
            },
        },
        "required": ["trigger_slug"],
    },
)
def composio_create_trigger(trigger_slug: str, trigger_config: dict = None) -> str:
    if not _is_enabled():
        return '{"error": "Composio is not enabled. Set COMPOSIO_API_KEY in .env."}'
    from composio_triggers import create_trigger
    return create_trigger(trigger_slug, trigger_config)


@tool(
    name="composio_list_active_triggers",
    description=(
        "List all currently active triggers. "
        "Shows what events are being monitored and their status."
    ),
    parameters={
        "type": "object",
        "properties": {},
    },
)
def composio_list_active_triggers() -> str:
    if not _is_enabled():
        return '{"error": "Composio is not enabled. Set COMPOSIO_API_KEY in .env."}'
    from composio_triggers import list_active_triggers
    return list_active_triggers()


@tool(
    name="composio_delete_trigger",
    description=(
        "Delete/disable an active trigger. "
        "Use composio_list_active_triggers to find the trigger_id first."
    ),
    parameters={
        "type": "object",
        "properties": {
            "trigger_id": {
                "type": "string",
                "description": "The trigger instance ID to delete.",
            },
        },
        "required": ["trigger_id"],
    },
)
def composio_delete_trigger(trigger_id: str) -> str:
    if not _is_enabled():
        return '{"error": "Composio is not enabled. Set COMPOSIO_API_KEY in .env."}'
    from composio_triggers import delete_trigger
    return delete_trigger(trigger_id)
