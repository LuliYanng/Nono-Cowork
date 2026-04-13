"""
Composio auxiliary tools — exposed to the LLM for trigger discovery and auth.

These are the Composio-specific tools that DON'T map to the "Routines" concept:
  - composio_list_triggers: discover available trigger types for an app
  - composio_wait_for_connection: wait for OAuth completion

Trigger CRUD (create, list active, delete) is now handled by the unified
`tools/routines.py` module.
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
    from automations.composio_triggers import list_available_triggers
    return list_available_triggers(toolkit)


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
