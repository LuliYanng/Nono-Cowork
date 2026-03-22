"""
Agent tool implementations.

Importing this package triggers auto-registration of all tools via @tool decorators.
The two main exports are:
  - tools_map:    dict[str, callable]  — tool name → function
  - tools_schema: list[dict]           — JSON schemas for LLM function calling
"""

# Import all tool modules to trigger @tool decorator registration
from tools import command, file_ops, web, syncthing, scheduler, memory, channel_ops, delegate  # noqa: F401

# Re-export the registry contents
from tools.registry import get_tools_map, get_tools_schema

tools_map = get_tools_map()
tools_schema = get_tools_schema()

# ── Composio integration (optional) ──
from tools import composio_tools
if composio_tools.is_enabled():
    composio_tools.init()
    tools_schema = tools_schema + composio_tools.get_tools_schema()

    # Register trigger management tools (LLM-callable)
    from tools import composio_trigger_tools  # noqa: F401
    # Re-fetch after trigger tools are registered
    tools_map = get_tools_map()
    tools_schema_updated = get_tools_schema()
    # Merge: keep Composio meta-tools + our trigger tools
    tools_schema = tools_schema_updated + composio_tools.get_tools_schema()
