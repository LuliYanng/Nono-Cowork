"""
Skill loader — discovers and loads Agent Skills from the skills/ directory.

Each skill is a folder containing a SKILL.md file with YAML frontmatter
(name, description) and markdown body (detailed instructions).

Progressive disclosure strategy:
  1. Startup: only name + description are injected into system prompt (~100 tokens per skill)
  2. On demand: the agent reads the full SKILL.md via read_file when a task matches
"""

import os
import re
import logging

logger = logging.getLogger("skills")

# Skills directory at project root
SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")


def _parse_frontmatter(content: str) -> dict | None:
    """Parse YAML frontmatter from a SKILL.md file.

    Handles simple key: value pairs and quoted multi-line descriptions.
    Uses regex-based parsing to avoid adding a PyYAML dependency.
    """
    match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return None

    raw = match.group(1)
    meta = {}

    # Match key: value or key: "quoted value" (possibly multi-line)
    # First, try to extract quoted values (handles escaped quotes inside)
    for m in re.finditer(
        r'^(\w[\w-]*)\s*:\s*"((?:[^"\\]|\\.)*)"\s*$',
        raw, re.MULTILINE | re.DOTALL,
    ):
        meta[m.group(1)] = m.group(2).replace('\\"', '"')

    # Then extract simple key: value pairs (non-quoted)
    for m in re.finditer(
        r'^(\w[\w-]*)\s*:\s*([^"\n].*)$',
        raw, re.MULTILINE,
    ):
        key = m.group(1)
        if key not in meta:  # Don't overwrite quoted values
            meta[key] = m.group(2).strip()

    return meta if meta else None


def discover_skills() -> list[dict]:
    """Discover all skills from the skills/ directory.

    Returns:
        List of dicts with keys: name, description, path, skill_md
    """
    skills = []

    if not os.path.isdir(SKILLS_DIR):
        logger.debug("Skills directory not found: %s", SKILLS_DIR)
        return skills

    for entry in sorted(os.listdir(SKILLS_DIR)):
        skill_dir = os.path.join(SKILLS_DIR, entry)
        skill_md = os.path.join(skill_dir, "SKILL.md")

        if not os.path.isfile(skill_md):
            continue

        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                content = f.read()

            meta = _parse_frontmatter(content)
            if meta and meta.get("name"):
                skills.append({
                    "name": meta["name"],
                    "description": meta.get("description", ""),
                    "path": skill_dir,
                    "skill_md": skill_md,
                })
                logger.info("Discovered skill: %s", meta["name"])
        except Exception as e:
            logger.warning("Failed to load skill %s: %s", entry, e)

    return skills


def format_skills_prompt_section(skills: list[dict]) -> str:
    """Format discovered skills into a system prompt section.

    Only injects name + description + file path (~100 tokens per skill).
    The agent reads the full SKILL.md on demand via read_file.
    """
    if not skills:
        return ""

    lines = [
        "# Skills",
        "You have specialized skills with detailed instructions for certain tasks.",
        "When a task matches a skill below, use read_file to read its SKILL.md BEFORE starting work.",
        "",
    ]

    for s in skills:
        lines.append(f"- **{s['name']}** → `{s['skill_md']}`")
        lines.append(f"  {s['description']}")
        lines.append("")

    return "\n".join(lines)
