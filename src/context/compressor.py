"""
Context compressor — sliding-window compression for long conversation histories.

When context usage exceeds a threshold (default 70%), older messages are
summarized into a single compressed message, keeping recent turns intact.
"""

import logging
from config import (
    CONTEXT_LIMIT,
    COMPRESSION_THRESHOLD,
    COMPRESSION_KEEP_RECENT_TURNS,
    COMPRESSION_MODEL,
)

logger = logging.getLogger("context.compressor")

# ── Compression prompt ──

_COMPRESS_PROMPT = """\
--- CONVERSATION TO SUMMARIZE ---
{conversation}
--- END OF CONVERSATION ---

Condense the conversation above into a concise summary that preserves:
1. Key facts, decisions, and outcomes
2. Important file paths, commands, and code snippets mentioned
3. User preferences or instructions that may be relevant later
4. Any unresolved tasks or pending items

Write the summary in the same language the user used. Be concise but complete.
Do NOT add any preamble like "Here is the summary". Just output the summary directly.
"""


def _count_turns(history: list) -> list[tuple[int, int]]:
    """Identify turn boundaries in history.

    A "turn" is a (user message, ..., assistant response) group.
    Returns list of (start_idx, end_idx) pairs.
    The system message at index 0 is excluded.
    """
    turns = []
    turn_start = None

    for i, msg in enumerate(history):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)

        if role == "user":
            turn_start = i
        elif role == "assistant" and turn_start is not None:
            # Find the end of this assistant's tool call chain
            # (include subsequent tool results that belong to this turn)
            turn_end = i
            for j in range(i + 1, len(history)):
                jr = history[j].get("role") if isinstance(history[j], dict) else getattr(history[j], "role", None)
                if jr == "tool":
                    turn_end = j
                elif jr == "assistant":
                    # Continue: this assistant message is a follow-up in the same turn
                    turn_end = j
                else:
                    break
            turns.append((turn_start, turn_end))
            turn_start = None

    return turns


def _messages_to_text(messages: list) -> str:
    """Convert a slice of history messages into readable text for summarization."""
    lines = []
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
        else:
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "") or ""
            # Handle tool_calls in assistant messages
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                tc_texts = []
                for tc in tool_calls:
                    tc_texts.append(f"  → {tc.function.name}({tc.function.arguments})")
                content = (content or "") + "\n" + "\n".join(tc_texts)

        # Handle multimodal content arrays (e.g., image tool results)
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        text_parts.append("[image]")
            content = " ".join(text_parts) if text_parts else ""

        if not content:
            continue

        # Truncate very long individual messages for the summary input
        if len(content) > 1500:
            content = content[:600] + "\n...[truncated]...\n" + content[-600:]

        label = {"user": "User", "assistant": "Assistant", "tool": "Tool", "system": "System"}.get(role, role)
        lines.append(f"[{label}]: {content}")

    return "\n\n".join(lines)


def needs_compression(prompt_tokens: int) -> bool:
    """Check if the current context usage warrants compression."""
    if not prompt_tokens or not CONTEXT_LIMIT:
        return False
    return prompt_tokens / CONTEXT_LIMIT > COMPRESSION_THRESHOLD


def compress_history(history: list, prompt_tokens: int) -> list:
    """Compress older conversation history into a summary message.

    Args:
        history: The full message history (mutated in place and returned).
        prompt_tokens: Current prompt token count from the last LLM call.

    Returns:
        The (possibly compressed) history list.
    """
    if not needs_compression(prompt_tokens):
        return history

    turns = _count_turns(history)
    if len(turns) <= COMPRESSION_KEEP_RECENT_TURNS:
        logger.debug("Not enough turns to compress (%d turns, keeping %d)",
                      len(turns), COMPRESSION_KEEP_RECENT_TURNS)
        return history

    # Split: compress older turns, keep recent ones
    keep_from = turns[-COMPRESSION_KEEP_RECENT_TURNS][0]  # Start index of oldest kept turn
    old_messages = history[1:keep_from]  # Skip system prompt at [0]

    if not old_messages:
        return history

    pct = prompt_tokens / CONTEXT_LIMIT * 100
    logger.info(
        "Context at %.0f%% (%d tokens). Compressing %d messages "
        "(keeping system + last %d turns = %d messages)",
        pct, prompt_tokens, len(old_messages),
        COMPRESSION_KEEP_RECENT_TURNS, len(history) - keep_from + 1,
    )

    # Generate summary using a cheap model
    conversation_text = _messages_to_text(old_messages)
    summary = _call_summary_llm(conversation_text)

    if not summary:
        logger.warning("Compression failed, keeping original history")
        return history

    # Build new history: system + summary + recent turns
    summary_message = {
        "role": "user",
        "content": (
            f"[CONVERSATION SUMMARY - Earlier messages have been compressed]\n\n"
            f"{summary}\n\n"
            f"[END OF SUMMARY - The conversation continues below]"
        ),
    }

    new_history = [history[0], summary_message] + history[keep_from:]

    logger.info(
        "Compressed: %d messages → %d messages (removed %d)",
        len(history), len(new_history), len(history) - len(new_history),
    )

    return new_history


def _call_summary_llm(conversation_text: str) -> str | None:
    """Call a cheap LLM to generate the conversation summary."""
    try:
        from core.llm import call_llm

        messages = [
            {"role": "user", "content": _COMPRESS_PROMPT.format(conversation=conversation_text)},
        ]

        completion = call_llm(messages, model=COMPRESSION_MODEL, tools=None)
        content = completion.choices[0].message.content
        return content.strip() if content else None

    except Exception as e:
        logger.error("Summary LLM call failed: %s", e, exc_info=True)
        return None
