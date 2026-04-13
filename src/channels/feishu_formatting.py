"""
Feishu Markdown formatting — converts standard Markdown to Feishu message card format.

Feishu cards do NOT support: # headings, --- horizontal rules, tables
Feishu cards support: **bold**, *italic*, ~~strikethrough~~, lists, `code`, code blocks
"""

import re
from delivery.formatter import clean_agent_output


def _adapt_md_for_feishu(text: str) -> str:
    """Convert standard Markdown to the subset supported by Feishu message cards."""
    text = _convert_tables(text)

    lines = text.split("\n")
    result = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue
        if in_code_block:
            result.append(line)
            continue

        # --- horizontal rule → blank line
        if re.match(r"^-{3,}\s*$", line.strip()):
            result.append("")
            continue

        # ### heading → **heading**
        header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if header_match:
            result.append(f"**{header_match.group(2)}**")
            continue

        result.append(line)

    return "\n".join(result)


def _convert_tables(text: str) -> str:
    """Convert Markdown tables → bold headers + pipe-separated plain text."""
    lines = text.split("\n")
    result = []
    in_code_block = False
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            i += 1
            continue
        if in_code_block:
            result.append(line)
            i += 1
            continue

        if re.match(r"^\s*\|.*\|", line):
            table_lines = []
            while i < len(lines) and re.match(r"^\s*\|.*\|", lines[i]):
                table_lines.append(lines[i])
                i += 1

            parsed_rows = []
            for tl in table_lines:
                cells = [c.strip() for c in tl.strip().strip("|").split("|")]
                if all(re.match(r"^[-:]+$", c) for c in cells):
                    continue
                parsed_rows.append(cells)

            if parsed_rows:
                header = parsed_rows[0]
                result.append(" | ".join(f"**{h}**" for h in header))
                for row in parsed_rows[1:]:
                    result.append(" | ".join(row))
                result.append("")
            continue

        result.append(line)
        i += 1

    return "\n".join(result)


def format_for_feishu(text: str) -> str:
    """Full Feishu formatting pipeline: ANSI cleanup → Feishu markdown adaptation."""
    text = clean_agent_output(text)
    text = _adapt_md_for_feishu(text)
    return text
