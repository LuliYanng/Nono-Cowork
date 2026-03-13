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
MAX_ROUNDS = 30
CONTEXT_LIMIT = 200_000  # Context window limit (used for usage percentage display)

# ── Prompt caching ──
# Providers that support cache_control
CACHE_CONTROL_PROVIDERS = {"dashscope/", "anthropic/"}

# ── Context Compression ──
COMPRESSION_THRESHOLD = 0.7         # Trigger compression when context usage exceeds this ratio
COMPRESSION_KEEP_RECENT_TURNS = 4   # Number of recent conversation turns to keep uncompressed
COMPRESSION_MODEL = "deepseek/deepseek-chat"  # Cheap model for generating summaries

# ── Tool Output Spill ──
TOOL_OUTPUT_MAX_CHARS = 3000        # Max chars per tool output before spilling to file
TOOL_OUTPUT_PREVIEW_CHARS = 800     # Chars to show as preview when output is spilled

# ── Memory ──
MEMORY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "memory.md")
MEMORY_MAX_INJECT_CHARS = 2000      # Max chars of memory to inject into system prompt
