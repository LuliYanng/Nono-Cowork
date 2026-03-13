"""
Agent tool implementations.

Importing this package triggers auto-registration of all tools via @tool decorators.
The two main exports are:
  - tools_map:    dict[str, callable]  — tool name → function
  - tools_schema: list[dict]           — JSON schemas for LLM function calling
"""

# Import all tool modules to trigger @tool decorator registration
from tools import command, file_ops, web, syncthing, scheduler, memory  # noqa: F401

# Re-export the registry contents
from tools.registry import get_tools_map, get_tools_schema

tools_map = get_tools_map()
tools_schema = get_tools_schema()
