"""
Centralized configuration — all tunables and environment-dependent settings live here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Model ──
# Each entry carries the LiteLLM routing ID *and* display metadata so the
# frontend never needs to reverse-engineer provider/name from the routing string.
# Adding a new model = one dict here; frontend picks it up automatically.
MODEL_REGISTRY: list[dict] = [
    {"id": "openrouter/anthropic/claude-sonnet-4.6",          "name": "Claude Sonnet 4.6",             "provider": "anthropic"},
    {"id": "openrouter/anthropic/claude-haiku-4.5",           "name": "Claude Haiku 4.5",              "provider": "anthropic"},
    {"id": "openrouter/openai/gpt-5.4",                      "name": "GPT-5.4",                        "provider": "openai"},
    {"id": "openrouter/openai/gpt-5.4-mini",                 "name": "GPT-5.4 Mini",                   "provider": "openai"},
    {"id": "openrouter/google/gemini-3.1-pro-preview",       "name": "Gemini 3.1 Pro Preview",         "provider": "google"},
    {"id": "openrouter/google/gemini-3-flash-preview",       "name": "Gemini 3 Flash Preview",         "provider": "google"},
    {"id": "openrouter/google/gemini-3.1-flash-lite-preview","name": "Gemini 3.1 Flash Lite Preview",  "provider": "google"},
    {"id": "openrouter/minimax/minimax-m2.7",                "name": "MiniMax M2.7",                   "provider": "minimax"},
    {"id": "openrouter/minimax/minimax-m2.5:free",           "name": "MiniMax M2.5 (Free)",            "provider": "minimax"},
    {"id": "xiaomi/mimo-v2.5-pro",                           "name": "Mimo v2.5 Pro",                  "provider": "xiaomi"},
    {"id": "xiaomi/mimo-v2.5",                               "name": "Mimo v2.5",                      "provider": "xiaomi"},
    {"id": "openrouter/deepseek/deepseek-v4-pro",            "name": "DeepSeek V4 Pro(OpenRouter)",    "provider": "deepseek"},
    {"id": "openrouter/deepseek/deepseek-v4-flash",          "name": "DeepSeek V4 Flash(OpenRouter)",  "provider": "deepseek"},
    {"id": "deepseek/deepseek-v4-pro",                       "name": "DeepSeek V4 Pro",                "provider": "deepseek"},
    {"id": "deepseek/deepseek-v4-flash",                     "name": "DeepSeek V4 Flash",              "provider": "deepseek"},
    {"id": "openrouter/z-ai/glm-5.1",                        "name": "Z-AI GLM-5.1",                   "provider": "z-ai"},
    {"id": "openrouter/moonshotai/kimi-k2.6",                "name": "Kimi K2.6",                      "provider": "moonshotai"},
]

# Derived flat list — backward compat for base.py, llm.py, etc.
MODEL_POOL = [m["id"] for m in MODEL_REGISTRY]
MODEL = os.getenv("MODEL", "openrouter/deepseek/deepseek-v4-pro")
API_BASE = os.getenv("API_BASE", "").strip()   # Custom OpenAI-compatible endpoint
API_KEY = os.getenv("API_KEY", "").strip()      # API key for the custom endpoint
XIAOMI_API_BASE = os.getenv("XIAOMI_API_BASE", "https://token-plan-sgp.xiaomimimo.com/v1").strip()
XIAOMI_API_KEY = os.getenv("XIAOMI_API_KEY", "").strip()

# Auto-prefix: if API_BASE is set and MODEL has no provider prefix, treat as openai-compatible
if API_BASE and "/" not in MODEL:
    MODEL = f"openai/{MODEL}"

MAX_ROUNDS = 30
CONTEXT_LIMIT = 200_000  # Context window limit (used for usage percentage display)




# ── Context Compression ──
COMPRESSION_THRESHOLD = 0.7         # Trigger compression when context usage exceeds this ratio
COMPRESSION_KEEP_RECENT_TURNS = 4   # Number of recent conversation turns to keep uncompressed
COMPRESSION_MODEL = "openrouter/deepseek/deepseek-v4-flash"  # Cheap model for generating summaries

# ── Autonomous Agent (trigger / cron / file-drop subagents) ──
# Default model when a trigger/cron task has no explicit model set.
# Deliberately stronger than COMPRESSION_MODEL — autonomous tasks often need
# real reasoning (email triage, scheduling, file analysis, etc.).
AUTONOMOUS_AGENT_MODEL = os.getenv(
    "AUTONOMOUS_AGENT_MODEL", "openrouter/deepseek/deepseek-v4-flash"
)

# ── Tool Output Spill ──
TOOL_OUTPUT_MAX_CHARS = 3000        # Max chars per tool output before spilling to file
TOOL_OUTPUT_PREVIEW_CHARS = 800     # Chars to show as preview when output is spilled

# ── Dream (nightly reflection) ──
# Phase 1 scans many user queries → cluster into recurring patterns. High volume,
# low difficulty → use a cheap model.
# Phase 2 reads slimmed full conversations and writes a SKILL.md draft. Lower
# volume, demands good structured-writing → use a stronger model.
DREAM_PHASE1_MODEL = os.getenv("DREAM_PHASE1_MODEL", "openrouter/minimax/minimax-m2.7")
DREAM_PHASE2_MODEL = os.getenv("DREAM_PHASE2_MODEL", "openrouter/anthropic/claude-sonnet-4.6")

# ── Memory ──
MEMORY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "memory.md")
MEMORY_MAX_INJECT_CHARS = 2000      # Max chars of memory to inject into system prompt

# ── Session Persistence ──
SESSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sessions")

# ── Autonomous Sessions (trigger / scheduler subagent work) ──
AUTO_SESSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "autonomous_sessions")
NOTIFICATIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "notifications.json")

# ── Multi-Channel ──
# Unified user identity — all authenticated channel users map to this ID.
# This enables cross-channel session sharing (e.g., start on Desktop, continue on Telegram).
OWNER_USER_ID = os.getenv("OWNER_USER_ID", "owner")

# Which channels to start (comma-separated). Used by main.py unified entry point.
ENABLED_CHANNELS = [c.strip() for c in os.getenv("CHANNELS", "desktop").split(",") if c.strip()]

# ── Composio (optional) ──
COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "").strip()
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID", "default").strip()
COMPOSIO_AUTH_WAIT_TIMEOUT = int(os.getenv("COMPOSIO_AUTH_WAIT_TIMEOUT", "300"))  # seconds
COMPOSIO_EXECUTE_TIMEOUT = int(os.getenv("COMPOSIO_EXECUTE_TIMEOUT", "120"))  # seconds, hard timeout per tool execution

# ── Webhook / Composio Triggers ──
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "9090"))
SERVER_HOST = os.getenv("SERVER_HOST", "").strip()  # Public hostname/IP for webhooks

# ── Web Search API Keys ──
# Tavily: AI-optimized search that returns page content directly (no need for read_webpage).
# Free tier: 1000 searches/month. Get key at https://tavily.com
# When configured, becomes the default backend for web_search standard mode.
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()

# Exa: Neural/semantic search engine. Enables "semantic" and "similar" modes in web_search.
# Free tier: 1000 searches/month. Get key at https://exa.ai
EXA_API_KEY = os.getenv("EXA_API_KEY", "").strip()

# ── Jina Reader (webpage rendering fallback) ──
# Jina Reader renders JS-heavy / SPA pages in a headless browser and returns
# clean Markdown. Without an API key, the free tier is ~20 RPM per IP.
# With a key (free at https://jina.ai), the limit is higher — 200+ RPM.
# Set JINA_API_KEY to your key to get the higher tier.
JINA_API_KEY = os.getenv("JINA_API_KEY", "").strip()

# ── Agent Timezone ──
# Timezone for timestamps injected into user messages (e.g. "Asia/Shanghai", "UTC").
# Leave empty to follow the system timezone of the host machine.
AGENT_TIMEZONE = "Asia/Shanghai"

# ── Agent Work Directory ──
# Persistent agent workspace: venvs, CLI tools, staging area, and other intermediate
# artifacts that should NOT be placed inside the Syncthing sync folder.
# Override with AGENT_WORK_DIR env var if needed.
AGENT_WORK_DIR = os.path.expanduser(
    os.getenv("AGENT_WORK_DIR", "~/.nono")
)

# ── Tool Redirects ──
# When the LLM tries to call a tool that's been filtered or doesn't exist,
# return a helpful guidance message instead of a generic "unknown tool" error.
# This is especially useful when third-party tool responses (e.g. Composio)
# reference tools we've intentionally removed.
# Format: { "TOOL_NAME": "guidance message for the LLM" }
TOOL_REDIRECTS = {
    "COMPOSIO_REMOTE_WORKBENCH": (
        "This tool is not available. You are running on a dedicated server with full shell access. "
        "Use run_command to execute any bash/python commands directly. "
        "For file downloads: run_command with curl -o /path/file 'url'. "
        "For data processing: run_command with python3 scripts."
    ),
    "COMPOSIO_REMOTE_BASH_TOOL": (
        "This tool is not available. You have direct server access. "
        "Use run_command to execute bash commands locally instead."
    ),
}
