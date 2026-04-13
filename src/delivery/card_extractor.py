"""
Card Extractor — extracts structured notification card data from agent output.

Design: The agent calls a conceptual "report_result" at the end of its work,
outputting a structured JSON block. This module parses that output.

For agents that support real tool injection (e.g. self-agent), we inject
report_result as an actual tool. For agents that don't (e.g. Gemini CLI),
we instruct via system prompt to output the JSON block, then parse it here.

Fallback chain:
  1. Parse ```json block from agent output → best quality
  2. Use raw agent text as summary → always works
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("card_extractor")


# ═══════════════════════════════════════════
#  report_result tool definition
#  (for agents that support tool injection)
# ═══════════════════════════════════════════

REPORT_RESULT_TOOL = {
    "type": "function",
    "function": {
        "name": "report_result",
        "description": (
            "Call this tool after completing all work to report your results "
            "to the user. This should be your final action."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One-line summary: what happened + what you did",
                },
                "deliverables": {
                    "type": "array",
                    "description": "List of concrete outputs the user can act on",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "description": (
                                    "Deliverable type that determines how the frontend renders it. "
                                    "Known types: file, email_draft, report, link, data. "
                                    "Unknown types are rendered as a generic card."
                                ),
                            },
                            "label": {
                                "type": "string",
                                "description": "Display name, e.g. 'quote_template.xlsx' or 'Reply Draft'",
                            },
                            "description": {
                                "type": "string",
                                "description": "Short note, e.g. 'Saved to Inbox/' or 'Created in Gmail'",
                            },
                            "metadata": {
                                "type": "object",
                                "description": (
                                    "Type-specific data, arbitrary key-value pairs. "
                                    "email_draft: {to, subject, body, draft_id}; "
                                    "file: {path, size}; link: {url}; report: {path}"
                                ),
                            },
                        },
                        "required": ["type", "label"],
                    },
                },
            },
            "required": ["summary"],
        },
    },
}


# ═══════════════════════════════════════════
#  System prompt snippet
#  (for agents that DON'T support tool injection, e.g. Gemini CLI)
# ═══════════════════════════════════════════

REPORT_RESULT_PROMPT = """
## IMPORTANT: Output Format (MANDATORY)

You MUST end your final reply with a structured JSON block to report your results.
Do NOT write a free-form summary — use ONLY this JSON format:

```json
{
  "summary": "One-line summary: what happened + what you did",
  "deliverables": [
    {
      "type": "file|email_draft|report|link|data",
      "label": "display name",
      "description": "short note",
      "metadata": {}
    }
  ]
}
```

Metadata by type:
- email_draft: {"to": "...", "subject": "...", "body": "full draft body", "draft_id": "..."}
- file: {"path": "...", "size": "..."}
- report: {"path": "..."}
- link: {"url": "..."}

Rules:
- summary is REQUIRED — always include it
- deliverables: list each concrete output (saved file, created draft, etc.)
- If no concrete deliverables, just include summary with an empty deliverables array
- If the event is not worth notifying (spam, system messages, etc.), reply ONLY with [SKIP]
""".strip()


# ═══════════════════════════════════════════
#  Extraction logic
# ═══════════════════════════════════════════

_DEFAULT_CARD = {
    "summary": "",
    "deliverables": [],
}


def extract_card_data(
    agent_output: str,
    history: list[dict] | None = None,
) -> dict:
    """Extract structured card data from agent output.

    Tries in order:
      1. report_result tool call in history (for agents with tool injection)
      2. ```json block in agent text output (for Gemini CLI style)
      3. Fallback: raw text as summary

    Returns: {"summary": str, "deliverables": list[dict]}
    """
    # Strategy 1: Look for report_result tool call in history
    if history:
        card = _extract_from_tool_call(history)
        if card:
            return card

    # Strategy 2: Parse ```json block from agent text
    if agent_output:
        card = _extract_from_json_block(agent_output)
        if card:
            return card

    # Strategy 3: Fallback — use raw text as summary
    if agent_output:
        text = agent_output.strip()
        # Truncate at reasonable length
        if len(text) > 500:
            # Cut at last sentence boundary
            for sep in ["。", ".", "！", "!", "\n\n"]:
                pos = text[:500].rfind(sep)
                if pos > 100:
                    text = text[:pos + 1]
                    break
            else:
                text = text[:500] + "..."
        return {"summary": text, "deliverables": []}

    return dict(_DEFAULT_CARD)


def _extract_from_tool_call(history: list[dict]) -> dict | None:
    """Find a report_result tool call in the conversation history."""
    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            if func.get("name") == "report_result":
                raw_args = func.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    return _normalize_card(args)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning("report_result args parse failed: %s", e)
    return None


def _extract_from_json_block(text: str) -> dict | None:
    """Parse a ```json {...} ``` block from agent text output."""
    # Find the LAST json block (in case agent outputs multiple)
    matches = list(re.finditer(
        r'```json\s*(\{.*?\})\s*```',
        text,
        re.DOTALL,
    ))
    if not matches:
        return None

    raw = matches[-1].group(1)
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "summary" in data:
            return _normalize_card(data)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("JSON block parse failed: %s", e)
    return None


def _normalize_card(data: dict) -> dict:
    """Ensure card data has required fields with correct types."""
    card = {
        "summary": str(data.get("summary", "")),
        "deliverables": [],
    }

    raw_deliverables = data.get("deliverables", [])
    if isinstance(raw_deliverables, list):
        for d in raw_deliverables:
            if not isinstance(d, dict):
                continue
            deliverable = {
                "type": str(d.get("type", "data")),
                "label": str(d.get("label", "")),
                "description": str(d.get("description", "")),
                "metadata": d.get("metadata") if isinstance(d.get("metadata"), dict) else {},
            }
            # Agent may still output actions — silently drop them
            # (frontend hardcodes actions per component type)
            if deliverable["label"]:  # Only add if has a label
                card["deliverables"].append(deliverable)

    return card
