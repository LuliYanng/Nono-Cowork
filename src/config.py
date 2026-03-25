"""
Centralized configuration — all tunables and environment-dependent settings live here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Model ──
MODEL_POOL = [
    "dashscope/qwen3.5-plus",
    "gemini/gemini-2.5-flash",
    "gemini/gemini-2.5-pro",
    "anthropic/claude-sonnet-4-20250514",
    "deepseek/deepseek-chat",
]
MODEL = os.getenv("MODEL", "dashscope/qwen3.5-plus")
API_BASE = os.getenv("API_BASE", "").strip()   # Custom OpenAI-compatible endpoint
API_KEY = os.getenv("API_KEY", "").strip()      # API key for the custom endpoint

# Auto-prefix: if API_BASE is set and MODEL has no provider prefix, treat as openai-compatible
if API_BASE and "/" not in MODEL:
    MODEL = f"openai/{MODEL}"

MAX_ROUNDS = 30
CONTEXT_LIMIT = 200_000  # Context window limit (used for usage percentage display)

# ── Prompt caching ──
# Providers that support cache_control
CACHE_CONTROL_PROVIDERS = {"dashscope/", "anthropic/"}

# ── Context Compression ──
COMPRESSION_THRESHOLD = 0.7         # Trigger compression when context usage exceeds this ratio
COMPRESSION_KEEP_RECENT_TURNS = 4   # Number of recent conversation turns to keep uncompressed
COMPRESSION_MODEL = "dashscope/qwen3.5-122b-a10b"  # Cheap model for generating summaries

# ── Tool Output Spill ──
TOOL_OUTPUT_MAX_CHARS = 3000        # Max chars per tool output before spilling to file
TOOL_OUTPUT_PREVIEW_CHARS = 800     # Chars to show as preview when output is spilled

# ── Memory ──
MEMORY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "memory.md")
MEMORY_MAX_INJECT_CHARS = 2000      # Max chars of memory to inject into system prompt

# ── Session Persistence ──
SESSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sessions")

# ── Composio (optional) ──
COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "").strip()
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID", "default").strip()
COMPOSIO_AUTH_WAIT_TIMEOUT = int(os.getenv("COMPOSIO_AUTH_WAIT_TIMEOUT", "300"))  # seconds

# ── Webhook / Composio Triggers ──
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "9090"))
SERVER_HOST = os.getenv("SERVER_HOST", "").strip()  # Public hostname/IP for webhooks
