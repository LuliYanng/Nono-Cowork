"""
Message formatting utilities — shared base formatting + long text splitting
"""
import re


def clean_agent_output(text: str) -> str:
    """Basic cleanup: strip ANSI color codes and excessive blank lines."""
    text = re.sub(r"\033\[[0-9;]*m", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_long_text(text: str, max_len: int) -> list[str]:
    """Split long text at paragraph boundaries, avoiding breaks inside code blocks."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Find the last paragraph separator within max_len
        split_pos = remaining.rfind("\n\n", 0, max_len)
        if split_pos == -1 or split_pos < max_len // 2:
            split_pos = remaining.rfind("\n", 0, max_len)
            if split_pos == -1:
                split_pos = max_len

        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")

    return chunks
