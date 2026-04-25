"""
LLM client — wraps LiteLLM calls, prompt caching, and token stats tracking.
"""

import warnings
import litellm
from config import MODEL, API_BASE, API_KEY

# Suppress Pydantic serialization warnings triggered by LiteLLM (harmless)
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")


def _read_field(obj, name: str, default=0):
    """Read a field from either a LiteLLM object or a dict-like response."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _drop_null_strict(value):
    """Remove function.strict=None from tool schemas before provider validation.

    Some OpenRouter routes validate OpenAI tool schemas strictly and reject
    ``strict: null``. Omitting the field preserves the default behavior.
    """
    if isinstance(value, dict):
        return {
            key: _drop_null_strict(item)
            for key, item in value.items()
            if not (key == "strict" and item is None)
        }
    if isinstance(value, list):
        return [_drop_null_strict(item) for item in value]
    return value


def _build_llm_kwargs(messages: list, model: str = None, tools: list = None) -> dict:
    """Build shared kwargs for LiteLLM calls (used by both stream and non-stream)."""
    model = model or MODEL
    _has_provider_prefix = "/" in model

    kwargs = {
        "model": model,
        "messages": messages,
        "drop_params": True,  # Auto-drop params unsupported by target model
    }
    if tools:
        kwargs["tools"] = _drop_null_strict(tools)
        kwargs["tool_choice"] = "auto"

    # Determine provider prefix (e.g. "gemini", "anthropic", "deepseek")
    # For OpenRouter-routed models (e.g. "openrouter/anthropic/claude-..."),
    # also extract the underlying provider from the second segment.
    _parts = model.split("/") if _has_provider_prefix else []
    _provider = _parts[0] if _parts else ""
    _sub_provider = _parts[1] if len(_parts) >= 3 else ""

    # ── Prompt caching ──
    # Anthropic requires explicit cache_control; OpenAI/DeepSeek auto-cache.
    # Using top-level cache_control lets OpenRouter auto-place the breakpoint
    # at the last cacheable block and advance it as conversations grow.
    # OpenRouter also enables sticky routing to keep the cache warm.
    _CACHE_PROVIDERS = {"anthropic"}
    if _provider in _CACHE_PROVIDERS or _sub_provider in _CACHE_PROVIDERS:
        kwargs["cache_control"] = {"type": "ephemeral"}

    # Enable thinking/reasoning for models that support it
    # LiteLLM maps reasoning_effort to Gemini's thinking_level automatically
    # drop_params=True ensures this is safely ignored by models that don't support it
    _THINKING_PROVIDERS = {"gemini", "anthropic"}
    if _provider in _THINKING_PROVIDERS or _sub_provider in _THINKING_PROVIDERS:
        kwargs["reasoning_effort"] = "medium"  # "low" or "high"; Gemini 3 can't fully disable

    # Only pass custom API_BASE/API_KEY for OpenAI-compatible mode.
    # Restrict this to:
    #   1) model without provider prefix (e.g. "qwen3.5-plus")
    #   2) explicit openai-compatible provider prefix
    # This prevents provider-routed models (e.g. openrouter/*, gemini/*)
    # from being accidentally sent to API_BASE.
    _OPENAI_COMPAT_PROVIDERS = {"openai", "text-completion-openai"}
    _use_custom_endpoint = bool(API_BASE) and (
        (not _has_provider_prefix) or (_provider in _OPENAI_COMPAT_PROVIDERS)
    )
    if _use_custom_endpoint:
        kwargs["api_base"] = API_BASE
    if _use_custom_endpoint and API_KEY:
        kwargs["api_key"] = API_KEY

    return kwargs


def call_llm(messages: list, model: str = None, tools: list = None):
    """Call the LLM via LiteLLM (non-streaming). Used for context compression etc.

    Returns:
        The raw LiteLLM completion response.
    """
    return litellm.completion(**_build_llm_kwargs(messages, model, tools))


def call_llm_stream(messages: list, model: str = None, tools: list = None):
    """Call the LLM via LiteLLM with streaming enabled.

    Returns:
        A streaming iterator of chunk objects.
    """
    kwargs = _build_llm_kwargs(messages, model, tools)
    kwargs["stream"] = True
    kwargs["stream_options"] = {"include_usage": True}  # Get usage in final chunk
    # Overall request timeout — caps TTFB + socket read stalls at the HTTP layer.
    # The per-chunk idle watchdog in _stream_llm_response handles mid-stream silence.
    kwargs["timeout"] = 300
    return litellm.completion(**kwargs)


def extract_cache_info(usage) -> dict:
    """Extract cache-related info from a completion usage object.

    Handles both Anthropic's field names (cache_creation_input_tokens)
    and OpenRouter's normalized names (cache_write_tokens).
    """
    cache_info = {}
    prompt_details = _read_field(usage, "prompt_tokens_details", None)
    if prompt_details:
        cache_info["cached_tokens"] = _read_field(prompt_details, "cached_tokens", 0) or 0
        cache_info["cache_creation_tokens"] = (
            _read_field(prompt_details, "cache_creation_input_tokens", 0)
            or _read_field(prompt_details, "cache_write_tokens", 0)
            or 0
        )
    return cache_info


def update_token_stats(token_stats: dict, usage, cache_info: dict) -> None:
    """Accumulate token usage into a running stats dict (mutates in place)."""
    if not usage:
        return
    token_stats.setdefault("total_prompt_tokens", 0)
    token_stats.setdefault("total_completion_tokens", 0)
    token_stats.setdefault("total_tokens", 0)
    token_stats.setdefault("total_cached_tokens", 0)
    token_stats.setdefault("total_api_calls", 0)
    token_stats["total_prompt_tokens"] += _read_field(usage, "prompt_tokens", 0) or 0
    token_stats["total_completion_tokens"] += _read_field(usage, "completion_tokens", 0) or 0
    token_stats["total_tokens"] += _read_field(usage, "total_tokens", 0) or 0
    token_stats["total_cached_tokens"] += cache_info.get("cached_tokens", 0)
    token_stats.setdefault("total_cache_write_tokens", 0)
    token_stats["total_cache_write_tokens"] += cache_info.get("cache_creation_tokens", 0)
    token_stats["total_api_calls"] += 1
    # Track last call's prompt tokens = current context size
    token_stats["last_prompt_tokens"] = _read_field(usage, "prompt_tokens", 0) or 0


def make_empty_token_stats() -> dict:
    """Create a fresh token stats dict."""
    return {
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_tokens": 0,
        "total_cached_tokens": 0,
        "total_cache_write_tokens": 0,
        "total_api_calls": 0,
        "last_prompt_tokens": 0,
    }
