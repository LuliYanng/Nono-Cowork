"""
Memory tools — Agent-facing tools to save and read persistent memories.

These tools let the Agent proactively record user preferences, important facts,
and project context that should persist across sessions.
"""

from tools.registry import tool


@tool(
    name="memory_append",
    description=(
        "Save important information to persistent memory. Use this when you learn "
        "something that should be remembered across sessions, such as:\n"
        "- User preferences (coding style, language, naming conventions)\n"
        "- Important project context (tech stack, directory structure)\n"
        "- Personal facts (name, role, timezone)\n"
        "- Recurring task patterns\n\n"
        "The memory is stored as Markdown. You can use headings, lists, etc. to organize it.\n"
        "Memories persist across sessions and are automatically loaded when a new session starts."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "Markdown-formatted text to append to the memory file. "
                    "Use headings (## Topic) to organize different categories. "
                    "Be concise — record facts, not full conversations."
                ),
            },
        },
        "required": ["content"],
    },
)
def memory_append(content: str) -> str:
    """Append content to the memory file."""
    from memory.store import append_memory
    return append_memory(content)


@tool(
    name="memory_read",
    description=(
        "Read saved memories. Use this to recall information from previous sessions, "
        "check what you already know, or review saved context before starting a task."
    ),
    parameters={
        "type": "object",
        "properties": {},
    },
)
def memory_read() -> str:
    """Read the full memory file."""
    from memory.store import read_memory
    return read_memory()


@tool(
    name="memory_reset",
    description="Clear all saved memories. Use with caution — this cannot be undone.",
    parameters={
        "type": "object",
        "properties": {},
    },
)
def memory_reset() -> str:
    """Delete the memory file."""
    from memory.store import reset_memory
    return reset_memory()
