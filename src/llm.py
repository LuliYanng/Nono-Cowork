"""
LLM client — wraps LiteLLM calls, cache control injection, and token stats tracking.
"""

import warnings
import litellm
from config import MODEL, CACHE_CONTROL_PROVIDERS

# Suppress Pydantic serialization warnings triggered by LiteLLM (harmless)
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")


def inject_cache_control(messages: list, model: str) -> list:
    """Inject cache_control markers for models that support prompt caching.

    For unsupported models, returns messages unchanged.
    """
    if not any(model.startswith(p) for p in CACHE_CONTROL_PROVIDERS):
        return messages  # Unsupported model, pass through

    enhanced = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            # Plain string → add cache_control
            if isinstance(content, str):
                enhanced.append({
                    **msg,
                    "content": [{"type": "text", "text": content,
                                 "cache_control": {"type": "ephemeral"}}]
                })
            else:
                enhanced.append(msg)
        else:
            enhanced.append(msg)
    return enhanced


def call_llm(messages: list, model: str = None, tools: list = None):
    """Call the LLM via LiteLLM with cache control and standard params.

    Args:
        messages: Conversation history
        model: Model identifier (defaults to config.MODEL)
        tools: Tool schemas list

    Returns:
        The raw LiteLLM completion response.
    """
    model = model or MODEL

    kwargs = {
        "model": model,
        "messages": inject_cache_control(messages, model),
        "drop_params": True,  # Auto-drop params unsupported by target model
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return litellm.completion(**kwargs)


def extract_cache_info(usage) -> dict:
    """Extract cache-related info from a completion usage object."""
    cache_info = {}
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    if prompt_details:
        cache_info["cached_tokens"] = getattr(prompt_details, "cached_tokens", 0) or 0
        cache_info["cache_creation_tokens"] = getattr(prompt_details, "cache_creation_input_tokens", 0) or 0
    return cache_info


def update_token_stats(token_stats: dict, usage, cache_info: dict) -> None:
    """Accumulate token usage into a running stats dict (mutates in place)."""
    if not usage:
        return
    token_stats["total_prompt_tokens"] += usage.prompt_tokens or 0
    token_stats["total_completion_tokens"] += usage.completion_tokens or 0
    token_stats["total_tokens"] += usage.total_tokens or 0
    token_stats["total_cached_tokens"] += cache_info.get("cached_tokens", 0)
    token_stats["total_api_calls"] += 1


def make_empty_token_stats() -> dict:
    """Create a fresh token stats dict."""
    return {
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_tokens": 0,
        "total_cached_tokens": 0,
        "total_api_calls": 0,
    }
