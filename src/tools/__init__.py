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
from tools.registry import get_tools_map, get_tools_schema, resolve_allowed_tags, filter_tools_by_tags

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


def build_restricted_tools(tool_access: str | None) -> list[dict] | None:
    """Build a restricted tools_schema based on tool_access preset.

    Filters BOTH custom tools (by tag) AND Composio tools (by session config).
    Returns None if tool_access is "full" or None (no restriction).

    Args:
        tool_access: Preset name ("read_only", "read_write", "safe", "full", None).

    Returns:
        Restricted tools_schema list, or None (use global default).
    """
    if tool_access is None or tool_access == "full":
        return None

    allowed_tags = resolve_allowed_tags(tool_access)
    if allowed_tags is None:
        return None

    # 1. Filter custom tools by tags
    custom_schemas = get_tools_schema()  # Only our registry tools
    filtered_custom = filter_tools_by_tags(custom_schemas, allowed_tags)

    # 2. Get restricted Composio tools (creates a new session with restrictions)
    if composio_tools.is_enabled():
        restricted_composio = composio_tools.create_restricted_tools_schema(tool_access)
        if restricted_composio is not None:
            return filtered_custom + restricted_composio
        else:
            # Fallback: use default Composio schemas
            return filtered_custom + composio_tools.get_tools_schema()

    return filtered_custom
