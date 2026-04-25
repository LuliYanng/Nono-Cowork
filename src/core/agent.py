"""
Agent core — the main agent loop.

The loop follows a simple cycle each round:
  1. Compress context if needed
  2. Stream LLM response
  3. Execute tool calls (if any)
  4. Repeat until the LLM replies without tool calls
"""

import inspect
import re
import json
import queue
import threading
from tools import tools_map, tools_schema
from tools import composio_tools
from tools.file_ops import IMAGE_MARKER_PREFIX
from config import MODEL, MAX_ROUNDS, CONTEXT_LIMIT, TOOL_REDIRECTS
from core.prompt import make_system_prompt
from core.llm import call_llm_stream, extract_cache_info, update_token_stats, make_empty_token_stats
from logger import log_event, serialize_message, serialize_usage
from context.spill import spill_tool_output
from context.compressor import compress_history, needs_compression


def _usage_field(usage, name: str, default=0):
    if usage is None:
        return default
    if isinstance(usage, dict):
        return usage.get(name, default)
    return getattr(usage, name, default)


# ── History sanitization ──────────────────────────────────────────────────────

def _sanitize_history(history: list) -> list:
    """Ensure every assistant message with tool_calls has matching tool responses.

    If a previous agent run crashed mid-execution, the history may contain an
    assistant message with tool_calls but no (or incomplete) tool response messages.
    This causes a 400 error from the LLM API.  We fix it by inserting placeholder
    tool responses for any missing tool_call_ids.
    """
    fixed = []
    i = 0
    while i < len(history):
        msg = history[i]
        fixed.append(msg)

        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        tool_calls = (
            msg.get("tool_calls") if isinstance(msg, dict)
            else getattr(msg, "tool_calls", None)
        )

        if role == "assistant" and tool_calls:
            expected_ids = {tc.id if hasattr(tc, "id") else tc["id"] for tc in tool_calls}

            j = i + 1
            while j < len(history):
                nxt = history[j]
                nxt_role = nxt.get("role") if isinstance(nxt, dict) else getattr(nxt, "role", None)
                if nxt_role == "tool":
                    tc_id = nxt.get("tool_call_id") if isinstance(nxt, dict) else getattr(nxt, "tool_call_id", None)
                    expected_ids.discard(tc_id)
                    fixed.append(nxt)
                    j += 1
                else:
                    break

            for missing_id in expected_ids:
                fixed.append({
                    "role": "tool",
                    "tool_call_id": missing_id,
                    "content": "[Error: tool call was not executed due to a previous error]",
                })

            i = j
        else:
            i += 1

    return fixed


# ── Pretty terminal output helpers ────────────────────────────────────────────

def _fmt_tool_args(args: dict, max_val_len: int = 80) -> str:
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > max_val_len:
            v_str = v_str[:max_val_len] + "…"
        parts.append(f"    {k}: {v_str}")
    return "\n".join(parts)


def _print_tool_call(tool_name: str, args: dict, description: str = ""):
    if description:
        print(f"\033[36m  🔧 {description}\033[0m")
        print(f"\033[90m    → {tool_name}\033[0m")
    else:
        print(f"\033[36m  🔧 {tool_name}\033[0m")
    if args:
        print(f"\033[90m{_fmt_tool_args(args)}\033[0m")
    print()


def _print_tool_result(result: str, max_len: int = 500):
    display = result if len(result) <= max_len else result[:max_len] + f"\n    … ({len(result)} chars total)"
    print(f"\033[90m  ↳ {display}\033[0m\n")


def _print_context_bar(usage):
    if not usage:
        return
    prompt_tokens = _usage_field(usage, "prompt_tokens", 0) or 0
    pct = min(prompt_tokens / CONTEXT_LIMIT * 100, 100)

    if pct < 50:
        color = "\033[32m"
    elif pct < 80:
        color = "\033[33m"
    else:
        color = "\033[31m"

    bar_width = 20
    filled = int(bar_width * pct / 100)
    bar = "█" * filled + "░" * (bar_width - filled)

    def fmt(n):
        return f"{n/1000:.0f}k" if n >= 1000 else str(n)

    print(f"\n\n{color}  ⟨{bar}⟩ {pct:.0f}%  {fmt(prompt_tokens)} / {fmt(CONTEXT_LIMIT)}\033[0m")


def format_usage_summary(token_stats: dict, usage=None) -> str:
    """Format a concise usage summary for IM channels."""
    def fmt(n):
        return f"{n/1000:.1f}k" if n >= 1000 else str(n)

    total = token_stats.get("total_tokens", 0)
    prompt = token_stats.get("total_prompt_tokens", 0)
    completion = token_stats.get("total_completion_tokens", 0)
    cached = token_stats.get("total_cached_tokens", 0)
    calls = token_stats.get("total_api_calls", 0)

    parts = [f"📊 Token: {fmt(total)}"]
    parts.append(f"(prompt {fmt(prompt)} + completion {fmt(completion)})")
    if cached:
        parts.append(f"| cached {fmt(cached)}")
    parts.append(f"| {calls} calls")

    if usage:
        prompt_tokens = _usage_field(usage, "prompt_tokens", 0) or 0
        pct = min(prompt_tokens / CONTEXT_LIMIT * 100, 100)
        parts.append(f"| context {pct:.0f}%")

    return " ".join(parts)


# ── Streaming LLM call ────────────────────────────────────────────────────────

def _stream_llm_response(history, active_model, active_tools, check_stop, on_event, round_num):
    """Stream an LLM response, accumulating content/reasoning/tool_calls.

    Returns:
        (msg, usage, final_text, stopped, generation_id) where msg is a LiteLLM Message object.
    """
    from litellm.types.utils import Message as LitellmMessage, ChatCompletionMessageToolCall, Function

    stream = call_llm_stream(history, model=active_model, tools=active_tools)

    accumulated_content = ""
    accumulated_reasoning = ""
    accumulated_tool_calls = {}
    usage = None
    stopped = False
    generation_id = None

    # Wrap stream iteration in a background thread so we can watchdog for
    # mid-stream silence and poll the stop flag without being blocked on the
    # underlying socket read. Some providers go quiet mid-stream without
    # closing the connection; without this, the agent hangs forever.
    import time as _time
    IDLE_TIMEOUT = 90  # seconds of mid-stream silence before we give up
    chunk_q: queue.Queue = queue.Queue()
    _SENTINEL_DONE = object()

    def _drain_stream():
        try:
            for c in stream:
                chunk_q.put(c)
        except Exception as exc:
            chunk_q.put(("__error__", exc))
        finally:
            chunk_q.put(_SENTINEL_DONE)

    drain_thread = threading.Thread(target=_drain_stream, daemon=True)
    drain_thread.start()

    last_chunk_at = _time.monotonic()
    stream_stalled = False

    while True:
        if check_stop and check_stop():
            print("\n🛑 Stop requested — aborting LLM stream")
            stopped = True
            break

        try:
            chunk = chunk_q.get(timeout=1.0)
        except queue.Empty:
            if _time.monotonic() - last_chunk_at > IDLE_TIMEOUT:
                print(f"\n⚠️  LLM stream idle for >{IDLE_TIMEOUT}s — aborting")
                stream_stalled = True
                break
            continue

        last_chunk_at = _time.monotonic()

        if chunk is _SENTINEL_DONE:
            break
        if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "__error__":
            raise chunk[1]

        delta = chunk.choices[0].delta if chunk.choices else None
        chunk_usage = getattr(chunk, "usage", None)
        chunk_id = getattr(chunk, "id", None)
        if chunk_usage:
            usage = chunk_usage
        if chunk_id:
            generation_id = chunk_id

        if not delta:
            continue

        # Reasoning chunks
        reasoning_chunk = getattr(delta, "reasoning_content", None) or ""
        if reasoning_chunk:
            accumulated_reasoning += reasoning_chunk
            print(f"\033[96m{reasoning_chunk}\033[0m", end="", flush=True)
            if on_event:
                on_event({"type": "reasoning_chunk", "content": reasoning_chunk, "round": round_num})

        # Text chunks
        text_chunk = delta.content or ""
        if text_chunk:
            accumulated_content += text_chunk
            print(text_chunk, end="", flush=True)
            if on_event:
                on_event({"type": "text_chunk", "content": text_chunk, "round": round_num})

        # Tool call chunks (accumulate, don't execute yet)
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in accumulated_tool_calls:
                    accumulated_tool_calls[idx] = {
                        "id": tc_delta.id or "",
                        "name": (tc_delta.function.name or "") if tc_delta.function else "",
                        "arguments": "",
                    }
                entry = accumulated_tool_calls[idx]
                if tc_delta.id:
                    entry["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        entry["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        entry["arguments"] += tc_delta.function.arguments

    if accumulated_reasoning:
        print()

    # Best-effort close the upstream stream so we release the socket.
    if stopped or stream_stalled:
        try:
            close_fn = getattr(stream, "close", None)
            if close_fn:
                close_fn()
        except Exception:
            pass

    if stream_stalled:
        raise TimeoutError(
            f"LLM stream went silent for more than {IDLE_TIMEOUT}s "
            "(provider stall — try again or switch model)"
        )

    # Reconstruct LiteLLM message object
    tool_calls_list = None
    if accumulated_tool_calls:
        tool_calls_list = []
        for idx in sorted(accumulated_tool_calls):
            tc = accumulated_tool_calls[idx]
            tool_calls_list.append(ChatCompletionMessageToolCall(
                id=tc["id"],
                type="function",
                function=Function(name=tc["name"], arguments=tc["arguments"]),
            ))

    msg = LitellmMessage(
        role="assistant",
        content=accumulated_content or None,
        tool_calls=tool_calls_list,
    )
    if accumulated_reasoning:
        msg.reasoning_content = accumulated_reasoning

    # Emit full reasoning event (for channels that don't handle chunks)
    if accumulated_reasoning and on_event:
        on_event({"type": "reasoning", "content": accumulated_reasoning, "round": round_num})

    # Clean up text: filter Qwen3's empty <think> tags
    final_text = ""
    if accumulated_content:
        text = re.sub(r"<think>.*?</think>", "", accumulated_content, flags=re.DOTALL)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            final_text = text

    return msg, usage, final_text, stopped, generation_id


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _dispatch_tool(tool_name: str, args: dict) -> str:
    """Execute a single tool call and return the result string."""
    if tool_name == "report_result":
        n_delivs = len(args.get("deliverables", []))
        return f"✅ Reported: {args.get('summary', '')} ({n_delivs} deliverable(s))"

    func = tools_map.get(tool_name)
    if func:
        sig = inspect.signature(func)
        valid_params = set(sig.parameters.keys())
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        if not has_var_keyword:
            args = {k: v for k, v in args.items() if k in valid_params}
        return str(func(**args))

    if composio_tools.is_composio_tool(tool_name):
        return composio_tools.execute(tool_name, args)

    if tool_name in TOOL_REDIRECTS:
        return TOOL_REDIRECTS[tool_name]

    return f"Error: unknown tool {tool_name}"


def _process_tool_result(tool_name: str, tool_result: str, tc_id: str, history: list):
    """Post-process tool result: spill, display, and append to history.

    Returns the display-friendly result string.
    """
    # Spill large outputs (skip Composio tools and read_file)
    if not composio_tools.is_composio_tool(tool_name) and tool_name != "read_file":
        tool_result = spill_tool_output(tool_result, tool_name=tool_name)

    # Prepare display result (truncate images for terminal)
    display_result = tool_result
    if isinstance(tool_result, str) and tool_result.startswith(IMAGE_MARKER_PREFIX):
        _parts = tool_result[len(IMAGE_MARKER_PREFIX):].split("|", 2)
        display_result = _parts[2] if len(_parts) == 3 else "📷 (image loaded)"

    _print_tool_result(display_result)

    # Append to history (convert image markers to multimodal content)
    if isinstance(tool_result, str) and tool_result.startswith(IMAGE_MARKER_PREFIX):
        stripped = tool_result[len(IMAGE_MARKER_PREFIX):]
        parts = stripped.split("|", 2)
        if len(parts) == 3:
            mime, b64_data, caption = parts
            history.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": [
                    {"type": "text", "text": caption},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64_data}"},
                    },
                ],
            })
        else:
            history.append({"role": "tool", "tool_call_id": tc_id, "content": tool_result})
    else:
        history.append({"role": "tool", "tool_call_id": tc_id, "content": tool_result})

    return display_result


# ── Stop helpers ──────────────────────────────────────────────────────────────

def _fill_stopped_tool_responses(history: list, tool_calls):
    """Insert dummy responses for unanswered tool calls after a stop/interrupt."""
    answered_ids = {
        item.get("tool_call_id") for item in history
        if isinstance(item, dict) and item.get("role") == "tool"
    }
    for tc in tool_calls:
        tc_id = tc.id if hasattr(tc, "id") else tc.get("id")
        if tc_id not in answered_ids:
            history.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": "[Stopped by user]",
            })


# ── Main loop ─────────────────────────────────────────────────────────────────

def agent_loop(history: list[dict], log_file=None, token_stats: dict = None,
               on_event=None, check_stop=None, model_override: str = None,
               tools_override: list[dict] = None):
    """Core Agent loop.

    Args:
        check_stop: Optional callable that returns True if the user requested a stop.
        model_override: Optional model name to use instead of the default.
        tools_override: Optional tool schemas to use instead of the global tools_schema.
                       Used for subagents with restricted tool access.
    """
    if token_stats is None:
        token_stats = make_empty_token_stats()

    last_prompt_tokens = 0
    active_model = model_override or MODEL
    _stopped = False
    pending_cache_backfills = []

    # Resolve active tool schemas (inject report_result if missing)
    active_tools = tools_override or tools_schema
    from delivery.card_extractor import REPORT_RESULT_TOOL
    if not any(t.get("function", {}).get("name") == "report_result" for t in active_tools):
        active_tools = list(active_tools) + [REPORT_RESULT_TOOL]

    for round_num in range(1, MAX_ROUNDS + 1):
        if _stopped or (check_stop and check_stop()):
            print("\n🛑 Stop requested by user")
            log_event(log_file, {"type": "stopped_by_user", "round": round_num})
            break

        print(f"\n=============== Round {round_num} ===============\n")

        try:
            # ── 1. Context compression ──
            if last_prompt_tokens and needs_compression(last_prompt_tokens):
                old_len = len(history)
                history = compress_history(history, last_prompt_tokens)
                if len(history) < old_len:
                    # Keep the regenerated system prompt scoped to the
                    # current session's workspace.
                    _ws_id = None
                    try:
                        from context import get_context
                        from core.session import sessions
                        _ctx = get_context()
                        _uid = _ctx.get("user_id") if _ctx else None
                        if _uid:
                            _st = sessions.get_status(_uid)
                            _ws_id = _st.get("workspace_id") if _st else None
                    except Exception:
                        _ws_id = None
                    history[0] = {
                        "role": "system",
                        "content": make_system_prompt(workspace_id=_ws_id),
                    }
                    print(f"\033[35m  📦 Context compressed: {old_len} → {len(history)} messages\033[0m")
                    log_event(log_file, {
                        "type": "context_compressed", "round": round_num,
                        "old_messages": old_len, "new_messages": len(history),
                    })

            history = _sanitize_history(history)

            # ── 2. Stream LLM response ──
            msg, usage, final_text, stopped, generation_id = _stream_llm_response(
                history, active_model, active_tools, check_stop, on_event, round_num,
            )
            if stopped:
                _stopped = True

            # Update token stats
            cache_info = extract_cache_info(
                usage, generation_id=generation_id, model=active_model,
            ) if usage else {}
            if usage:
                update_token_stats(token_stats, usage, cache_info)
                last_prompt_tokens = _usage_field(usage, "prompt_tokens", 0) or 0
                if (
                    generation_id
                    and active_model.startswith("openrouter/")
                    and not cache_info.get("cached_tokens")
                    and not cache_info.get("cache_creation_tokens")
                ):
                    pending_cache_backfills.append({
                        "generation_id": generation_id,
                        "model": active_model,
                    })

            log_event(log_file, {
                "type": "llm_response", "round": round_num, "model": active_model,
                "message": serialize_message(msg),
                "usage": serialize_usage(usage) if usage else {},
                "cache": cache_info,
                "token_stats_cumulative": dict(token_stats),
            })

            history.append(msg)

            # ── 3a. No tool calls → final reply ──
            if not msg.tool_calls:
                _print_context_bar(usage)
                if on_event and final_text:
                    on_event({"type": "final_reply", "content": final_text, "round": round_num})
                if on_event:
                    on_event({
                        "type": "usage_report",
                        "summary": format_usage_summary(token_stats, usage),
                        "token_stats": dict(token_stats),
                        "prompt_tokens": _usage_field(usage, "prompt_tokens", 0) or 0,
                        "round": round_num,
                    })
                break

            # ── 3b. Has tool calls → execute ──
            if on_event and final_text:
                on_event({"type": "narration", "content": final_text, "round": round_num})

            for tc in msg.tool_calls:
                if check_stop and check_stop():
                    print("\n🛑 Stop requested — aborting remaining tool calls")
                    _fill_stopped_tool_responses(history, msg.tool_calls)
                    log_event(log_file, {"type": "stopped_by_user", "round": round_num})
                    _stopped = True
                    break

                tool_name = tc.function.name
                args = json.loads(tc.function.arguments)

                # Extract LLM-provided description (human-readable intent)
                tool_description = args.pop("description", "") or ""

                _print_tool_call(tool_name, args, tool_description)

                if on_event:
                    on_event({"type": "tool_call", "tool_name": tool_name, "args": args, "description": tool_description, "round": round_num})

                tool_result = _dispatch_tool(tool_name, args)
                display_result = _process_tool_result(tool_name, tool_result, tc.id, history)

                if on_event:
                    on_event({"type": "tool_result", "tool_name": tool_name, "result": display_result, "round": round_num})

                log_event(log_file, {
                    "type": "tool_result", "round": round_num,
                    "tool_name": tool_name, "tool_call_id": tc.id,
                    "args": args,
                    "result": tool_result[:2000] if len(tool_result) > 2000 else tool_result,
                })

        except KeyboardInterrupt:
            print("\n\n⚡ User interrupted the current task")
            if history and hasattr(history[-1], "tool_calls") and history[-1].tool_calls:
                _fill_stopped_tool_responses(history, history[-1].tool_calls)
            history.append({
                "role": "user",
                "content": "[User interrupted your current operation. Stop and wait for new instructions.]",
            })
            log_event(log_file, {"type": "interrupted", "round": round_num})
            break

    else:
        print(f"\n⚠️ Reached max rounds ({MAX_ROUNDS}), forcing exit")
        log_event(log_file, {"type": "max_rounds_reached", "max_rounds": MAX_ROUNDS})

    log_event(log_file, {"type": "task_token_summary", **token_stats})

    return history, token_stats, pending_cache_backfills
