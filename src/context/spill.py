"""
Tool output spill — saves large tool outputs to disk.

When any tool returns output exceeding the configured threshold, the full
content is "spilled" to a temp file. A short preview + file path reference
is returned in place of the original output. The Agent can then use
read_file() with line ranges to progressively read the full content.

This is the SINGLE place that controls tool output size entering the
context window. Individual tools should return their raw output without
worrying about size.

The term "spill" comes from database/compiler terminology: when data
doesn't fit in the fast/limited space (context window), it's spilled
to a slower/larger space (disk).
"""

import os
import time
import tempfile
import logging
from config import TOOL_OUTPUT_MAX_CHARS, TOOL_OUTPUT_PREVIEW_CHARS

logger = logging.getLogger("context.spill")

# Shared temp directory for all spilled tool outputs
TEMP_DIR = os.path.join(tempfile.gettempdir(), "agent_tool_outputs")
os.makedirs(TEMP_DIR, exist_ok=True)


def _save_to_temp_file(content: str, label: str = "tool_output") -> str:
    """Save content to a temp file and return the file path."""
    safe_label = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)[:30]
    filename = f"{safe_label}_{int(time.time())}_{os.getpid()}.txt"
    filepath = os.path.join(TEMP_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def spill_tool_output(output: str, tool_name: str = "tool") -> str:
    """Spill large tool output to disk, returning a preview + file reference.

    - If output <= threshold: return unchanged.
    - If output > threshold: save full output to a temp file,
      return a preview (first N chars) + file path.

    The Agent can then use read_file(path, start_line, end_line)
    to progressively read the full content as needed.

    Args:
        output: The raw tool output string.
        tool_name: Name of the tool (used for file naming and log).

    Returns:
        The original output (if small) or a preview + file reference (if large).
    """
    if len(output) <= TOOL_OUTPUT_MAX_CHARS:
        return output

    # Spill full output to a temp file
    filepath = _save_to_temp_file(output, label=tool_name)
    total_lines = output.count("\n") + 1

    # Build preview: first N chars, cut at last newline to avoid broken lines
    preview = output[:TOOL_OUTPUT_PREVIEW_CHARS]
    last_nl = preview.rfind("\n")
    if last_nl > TOOL_OUTPUT_PREVIEW_CHARS // 2:
        preview = preview[:last_nl]

    logger.info(
        "Spilled %s output: %d chars → %s (preview: %d chars)",
        tool_name, len(output), filepath, len(preview),
    )

    return (
        f"{preview}\n\n"
        f"... [{len(output)} chars total, {total_lines} lines — full output spilled to file]\n"
        f"📄 Full output saved to: {filepath}\n"
        f"Use read_file(\"{filepath}\") to view the full content."
    )
