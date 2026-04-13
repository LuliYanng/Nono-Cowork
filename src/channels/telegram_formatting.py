"""
Telegram Markdown formatting — converts standard Markdown to Telegram MarkdownV2 format.

Telegram MarkdownV2 supports: *bold*, _italic_, ~strikethrough~,
`code`, ```code blocks```, [links](url)
Not supported: # headings, tables
"""

import re
from delivery.formatter import clean_agent_output


def _adapt_md_for_telegram(text: str) -> str:
    """Convert standard Markdown to Telegram MarkdownV2 format."""
    text = _convert_tables_to_text(text)

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

        # ### heading → *heading* (Telegram uses single * for bold)
        header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if header_match:
            title = header_match.group(2)
            # Remove existing ** bold markers to avoid nesting
            title = title.replace("**", "")
            result.append(f"*{title}*")
            continue

        # **bold** → *bold* (Telegram MarkdownV2 uses single *)
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)

        result.append(line)

    return "\n".join(result)


def _convert_tables_to_text(text: str) -> str:
    """Convert Markdown tables → plain text."""
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
                result.append("*" + " | ".join(header) + "*")
                for row in parsed_rows[1:]:
                    result.append(" | ".join(row))
                result.append("")
            continue

        result.append(line)
        i += 1

    return "\n".join(result)


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for MarkdownV2 (skip inside code blocks)."""
    special_chars = r"_[]()~`>#+-=|{}.!"

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

        # Protect existing markdown format markers
        # Protect *bold* (single-asterisk bold)
        protected = []
        parts = re.split(r"(\*[^*]+\*)", line)
        for part in parts:
            if re.match(r"^\*[^*]+\*$", part):
                # This is a bold marker, escape inner special chars (excluding *)
                inner = part[1:-1]
                inner_escaped = ""
                for ch in inner:
                    if ch in special_chars and ch != "*":
                        inner_escaped += "\\" + ch
                    else:
                        inner_escaped += ch
                protected.append(f"*{inner_escaped}*")
            else:
                # Normal text, protect backtick-wrapped inline code
                code_parts = re.split(r"(`[^`]+`)", part)
                for cp in code_parts:
                    if re.match(r"^`[^`]+`$", cp):
                        protected.append(cp)  # inline code: no escaping
                    else:
                        escaped = ""
                        for ch in cp:
                            if ch in special_chars:
                                escaped += "\\" + ch
                            else:
                                escaped += ch
                        protected.append(escaped)
            result_line = "".join(protected)
            protected = []

        result.append(result_line)

    return "\n".join(result)


def format_for_telegram(text: str) -> str:
    """Full Telegram formatting pipeline."""
    text = clean_agent_output(text)
    text = _adapt_md_for_telegram(text)
    return text
