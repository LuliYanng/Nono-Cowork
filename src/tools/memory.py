"""
Memory tool — lets the Agent maintain a persistent memory file.

A single `memory_write` tool replaces the old append/read/reset trio.
The Agent reads memory via system prompt injection, and writes by
overwriting the entire file — giving it full control to organize,
update, deduplicate, and prune its own memories.
"""

from tools.registry import tool


@tool(
    name="memory_write",
    tags=["write"],
    description=(
        "Write the complete contents of your persistent memory file.\n"
        "This OVERWRITES the entire file, so always include ALL memories you want to keep.\n\n"
        "Use this when:\n"
        "- You learn something new about the user (add it)\n"
        "- A fact has changed (update it)\n"
        "- Memory is cluttered (reorganize / prune it)\n"
        "- User asks you to forget something (remove it)\n\n"
        "Your current memories are already loaded in the system prompt under '## Saved Memories'.\n"
        "Read them there, decide what to keep/change, then write the full updated version.\n\n"
        "Guidelines:\n"
        "- Use Markdown with headings (## Topic) to organize categories\n"
        "- Be concise — record facts, not conversations\n"
        "- Drop outdated or trivial information\n"
        "- Keep the file under ~2000 chars for efficiency"
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "The complete Markdown content for the memory file. "
                    "This replaces everything — include all memories you want to retain."
                ),
            },
        },
        "required": ["content"],
    },
)
def memory_write(content: str) -> str:
    """Overwrite the memory file with new content."""
    from memory.store import write_memory
    return write_memory(content)
