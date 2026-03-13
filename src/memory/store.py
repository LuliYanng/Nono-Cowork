"""
Memory store — reads and writes a single persistent memory.md file.

The memory file lives at the path configured by MEMORY_FILE (default: data/memory.md).
The Agent can freely append to this file via the memory_append tool.
The file contents are injected into the system prompt at session start.

The Markdown format is intentionally unstructured — the LLM decides
what to remember and how to organize it.
"""

import os
import logging
from config import MEMORY_FILE

logger = logging.getLogger("memory.store")


def load_memory() -> str:
    """Load the memory file contents. Returns empty string if not found."""
    if not os.path.exists(MEMORY_FILE):
        return ""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        logger.debug("Loaded memory (%d chars)", len(content))
        return content
    except Exception as e:
        logger.error("Failed to load memory: %s", e)
        return ""


def append_memory(content: str) -> str:
    """Append content to the memory file.

    Creates the file and parent directories if they don't exist.

    Args:
        content: Markdown-formatted text to append.

    Returns:
        A status message indicating success or failure.
    """
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)

    try:
        existing = ""
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                existing = f.read()

        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            if existing.strip():
                f.write("\n\n")
            f.write(content.strip())
            f.write("\n")

        logger.info("Appended memory (%d chars)", len(content))
        return f"✅ Memory saved ({len(content)} chars)"
    except Exception as e:
        logger.error("Failed to save memory: %s", e)
        return f"❌ Failed to save memory: {e}"


def read_memory() -> str:
    """Read the full memory file. Returns a formatted result."""
    content = load_memory()
    if not content:
        return "📭 No memories saved yet."
    return f"📝 Memory contents ({len(content)} chars):\n\n{content}"


def reset_memory() -> str:
    """Delete the memory file."""
    if os.path.exists(MEMORY_FILE):
        try:
            os.remove(MEMORY_FILE)
            logger.info("Memory reset")
            return "✅ Memory cleared."
        except Exception as e:
            logger.error("Failed to reset memory: %s", e)
            return f"❌ Failed to clear memory: {e}"
    return "📭 No memory to clear."
