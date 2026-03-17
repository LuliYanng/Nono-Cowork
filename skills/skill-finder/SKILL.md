---
name: skill-finder
description: Use this skill when the user asks what skills are available, wants to find or install a new skill, search for skills online, or asks "what can you do" about a specific domain. Also trigger when the user says "do I have a skill for...", "find a skill", or "install a skill".
---

# Skill Finder

Find, list, and install Agent Skills — both local and online.

## 1. List Local Skills

All skills live in the `skills/` directory at the project root.

```bash
ls -1 skills/
```

To see each skill's description:

```bash
head -5 skills/*/SKILL.md
```

## 2. Search Online for Skills

When the user needs a capability not covered by local skills, search online:

### Anthropic Skills (high quality, official)
- Repo: https://github.com/anthropics/skills
- Contains office skills (pdf, xlsx, docx, pptx), creative skills, and developer tools
- License: Apache 2.0 for most; office skills are proprietary (source-available)

### ClawHub (large community collection)
- Website: https://clawhub.com
- GitHub archive: https://github.com/openclaw/skills
- Thousands of community-built skills, varying quality
- Search with: `web_search("clawhub <keyword> skill")`

### GitHub General
- Search with: `web_search("Agent Skills SKILL.md <keyword> github")`
- Look for repos with SKILL.md files following the Agent Skills spec

### Search workflow:
1. Search online using web_search
2. Find the GitHub repo URL for the skill
3. Show the user what you found (name, description, license)
4. **Check compatibility** (see below)
5. Ask if they want to install it

### Compatibility check (IMPORTANT)

Before installing an online skill, read its SKILL.md and check for:

- **OpenClaw-specific tools**: References to `clawhub`, OpenClaw CLI commands, or OpenClaw-specific APIs. These won't work in our environment.
- **Platform dependencies**: macOS-only tools (e.g. `apple-notes`, `imsg`, AppleScript). We run on Linux.
- **External service dependencies**: API keys or accounts we don't have.
- **Tool name mismatches**: The skill may reference tools by different names than ours (e.g. `shell` vs `run_command`, `computer` vs our tools).

Most skills are just instructional text (how to use Python libraries, shell commands, etc.) and work anywhere. If a skill has minor incompatibilities:
- Adapt tool names to match ours (e.g. replace `shell()` references with `run_command()`)
- Skip platform-specific sections
- Tell the user what won't work before installing

If a skill is fundamentally tied to OpenClaw or another platform, tell the user and suggest creating our own version with `skill-creator`.

## 3. Install a Skill

To install a skill from GitHub:

```bash
# Clone into skills/ directory
cd skills/
git clone --depth 1 <repo-url> <skill-name>

# Or if the skill is inside a larger repo, use sparse checkout:
git clone --depth 1 --filter=blob:none --sparse <repo-url> /tmp/skill-tmp
cd /tmp/skill-tmp
git sparse-checkout set <path-to-skill>
cp -r <path-to-skill> /path/to/project/skills/<skill-name>
rm -rf /tmp/skill-tmp
```

After installing, verify it's discovered:

```bash
head -5 skills/<skill-name>/SKILL.md
```

The skill will be automatically available next time a session starts.

## 4. Presenting Results

When listing skills, format as:

```
📦 Installed Skills (X total):

  • skill-creator — Create new skills
  • skill-finder — Find and install skills
  • pdf — Process PDF files
  • xlsx — Work with Excel spreadsheets
  ...
```

If the user needs something not available, offer to:
1. Search online for an existing skill
2. Create a new one with the `skill-creator` skill
