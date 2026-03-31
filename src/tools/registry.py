"""
Tool registry — auto-registration via @tool decorator.

Usage:
    from tools.registry import tool

    @tool(
        name="my_tool",
        description="Does something useful.",
        parameters={
            "type": "object",
            "properties": {
                "arg1": {"type": "string", "description": "..."},
            },
            "required": ["arg1"],
        },
        tags=["read"],
    )
    def my_tool(arg1: str) -> str:
        ...

Adding a new tool only requires writing the function + decorator.
No separate registration file needed.

Tags:
    read     — only reads data (files, emails, status)
    write    — creates/modifies/deletes data (files, drafts, messages)
    execute  — runs shell commands (high risk)
    network  — makes external HTTP requests (web search)
    admin    — system admin ops (triggers, scheduling, delegation)
"""

_tools_map: dict[str, callable] = {}
_tools_schema: list[dict] = []
_tools_tags: dict[str, list[str]] = {}  # tool_name → tags


def tool(name: str, description: str, parameters: dict, tags: list[str] = None):
    """Decorator to register an agent tool with its JSON schema and optional tags."""
    def decorator(func):
        _tools_map[name] = func
        _tools_schema.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })
        _tools_tags[name] = tags or []
        return func
    return decorator


# ── Public accessors ──

def get_tools_map() -> dict[str, callable]:
    """Return the registered tool name → function mapping."""
    return _tools_map


def get_tools_schema() -> list[dict]:
    """Return the registered tool JSON schemas."""
    return _tools_schema


def get_tools_tags() -> dict[str, list[str]]:
    """Return the tool name → tags mapping."""
    return _tools_tags


# ── Tag-based presets ──
# Maps a preset name to the set of allowed tags.

TOOL_ACCESS_PRESETS = {
    "full":       None,                                      # No filtering
    "read_only":  {"read"},                                  # Only read tools
    "read_write": {"read", "write"},                         # Read + write, no shell
    "safe":       {"read", "write", "network"},              # No shell commands
}


def resolve_allowed_tags(tool_access) -> set[str] | None:
    """Resolve a tool_access value to a set of allowed tags.

    Args:
        tool_access: One of:
            - str preset: "full", "read_only", "read_write", "safe"
            - list of tags: ["read", "write"]
            - None: no filtering (full access)

    Returns:
        Set of allowed tags, or None for no filtering.
    """
    if tool_access is None or tool_access == "full":
        return None

    if isinstance(tool_access, str):
        preset = TOOL_ACCESS_PRESETS.get(tool_access)
        if preset is None and tool_access != "full":
            raise ValueError(f"Unknown tool_access preset: {tool_access}")
        return preset

    if isinstance(tool_access, list):
        return set(tool_access)

    return None


def filter_tools_by_tags(
    tools_schema: list[dict],
    allowed_tags: set[str] | None,
) -> list[dict]:
    """Filter tool schemas to only include tools with matching tags.

    Tools without tags are always included (e.g., Composio meta-tools).
    Only filters tools that have been registered with tags.

    Args:
        tools_schema: List of tool JSON schemas.
        allowed_tags: Set of allowed tags. None = no filtering.

    Returns:
        Filtered list of tool schemas.
    """
    if allowed_tags is None:
        return tools_schema

    filtered = []
    for schema in tools_schema:
        tool_name = schema.get("function", {}).get("name", "")
        tool_tags = _tools_tags.get(tool_name)

        if tool_tags is None:
            # Not in our registry (e.g., Composio meta-tools) → keep
            filtered.append(schema)
        elif not tool_tags:
            # Registered but no tags → keep (backward compat)
            filtered.append(schema)
        elif allowed_tags & set(tool_tags):
            # Has at least one matching tag → keep
            filtered.append(schema)
        # else: tags don't match → skip

    return filtered
