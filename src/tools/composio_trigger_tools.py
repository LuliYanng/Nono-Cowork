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
    tags=["read"],
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
    tags=["admin"],
    description=(
        "Create a new trigger to monitor events from a connected app. "
        "The trigger will automatically process events using a dedicated agent "
        "with the system prompt you provide in agent_prompt. "
        "IMPORTANT: You MUST write a detailed agent_prompt that tells the processing agent "
        "exactly how to handle events. Include rules for filtering (use [SKIP] for events "
        "to ignore), formatting, and any specific actions. "
        "First use composio_list_triggers to find the right trigger slug."
    ),
    parameters={
        "type": "object",
        "properties": {
            "trigger_slug": {
                "type": "string",
                "description": "The trigger type slug (e.g., 'GMAIL_NEW_GMAIL_MESSAGE', 'GITHUB_COMMIT_EVENT').",
            },
            "agent_prompt": {
                "type": "string",
                "description": (
                    "System prompt for the event-processing agent. Write clear rules for: "
                    "1) When to notify the user vs skip ([SKIP]). "
                    "2) How to format the notification. "
                    "3) Any specific processing logic. "
                    "This prompt will be used every time an event is received."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "LLM model for processing trigger events. Choose based on task complexity: "
                    "- Simple filtering/notification: use a fast cheap model (e.g., 'gemini/gemini-3-flash-preview'). "
                    "- Complex analysis/multi-step actions: use a stronger model (e.g., 'gemini/gemini-3.1-pro-preview'). "
                    "Leave empty to use the system default model."
                ),
            },
            "trigger_config": {
                "type": "object",
                "description": "Optional configuration for the trigger. Use composio_list_triggers to see required config fields.",
            },
        },
        "required": ["trigger_slug", "agent_prompt"],
    },
)
def composio_create_trigger(trigger_slug: str, agent_prompt: str,
                            trigger_config: dict = None, model: str = "") -> str:
    if not _is_enabled():
        return '{"error": "Composio is not enabled. Set COMPOSIO_API_KEY in .env."}'
    from composio_triggers import create_trigger
    return create_trigger(trigger_slug, agent_prompt, trigger_config, model=model)


@tool(
    name="composio_list_active_triggers",
    tags=["read"],
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
    tags=["admin"],
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


@tool(
    name="composio_wait_for_connection",
    tags=["admin"],
    description=(
        "Wait for a user to complete authentication for a toolkit/app. "
        "Call this IMMEDIATELY after COMPOSIO_MANAGE_CONNECTIONS returns an 'initiated' "
        "status with a redirect URL. This tool BLOCKS until the user completes auth "
        "or times out (default 300s). "
        "Example flow: "
        "1) Call COMPOSIO_MANAGE_CONNECTIONS → get auth link → share with user. "
        "2) Call composio_wait_for_connection(toolkit='discord') → blocks until user completes auth. "
        "3) Tool returns success → continue with the original task."
    ),
    parameters={
        "type": "object",
        "properties": {
            "toolkit": {
                "type": "string",
                "description": "The toolkit/app slug to wait for (e.g., 'gmail', 'discord', 'github').",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait for auth completion. Default: 300.",
            },
        },
        "required": ["toolkit"],
    },
)
def composio_wait_for_connection(toolkit: str, timeout: int = None) -> str:
    if not _is_enabled():
        return '{"error": "Composio is not enabled. Set COMPOSIO_API_KEY in .env."}'
    import json
    from tools.composio_tools import wait_for_connection
    result = wait_for_connection(toolkit, timeout)
    return json.dumps(result, ensure_ascii=False)
