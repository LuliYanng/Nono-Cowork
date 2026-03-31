"""
Agent core — the main agent loop and CLI entry point.
"""

import re
import json
from tools import tools_map, tools_schema
from tools import composio_tools
from config import MODEL, MAX_ROUNDS, CONTEXT_LIMIT, TOOL_REDIRECTS
from prompt import make_system_prompt
from llm import call_llm, call_llm_stream, extract_cache_info, update_token_stats, make_empty_token_stats
from logger import create_log_file, close_log_file, log_event, recover_orphaned_logs, serialize_message, serialize_usage
from context.spill import spill_tool_output
from context.compressor import compress_history, needs_compression


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

        # Get role and tool_calls regardless of dict / object
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        tool_calls = (
            msg.get("tool_calls") if isinstance(msg, dict)
            else getattr(msg, "tool_calls", None)
        )

        if role == "assistant" and tool_calls:
            # Collect expected tool_call_ids
            expected_ids = {tc.id if hasattr(tc, "id") else tc["id"] for tc in tool_calls}

            # Walk forward and collect the existing tool responses
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

            # Back-fill any missing tool responses
            for missing_id in expected_ids:
                fixed.append({
                    "role": "tool",
                    "tool_call_id": missing_id,
                    "content": "[Error: tool call was not executed due to a previous error]",
                })

            i = j  # skip the tool messages we already consumed
        else:
            i += 1

    return fixed


# ── Pretty terminal output helpers ──────────────────────────────────────────

def _fmt_tool_args(args: dict, max_val_len: int = 80) -> str:
    """Format tool arguments for terminal display, truncating long values."""
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > max_val_len:
            v_str = v_str[:max_val_len] + "…"
        parts.append(f"    {k}: {v_str}")
    return "\n".join(parts)


def _print_tool_call(tool_name: str, args: dict):
    """Print a styled tool call header."""
    print(f"\033[36m  🔧 {tool_name}\033[0m")
    if args:
        print(f"\033[90m{_fmt_tool_args(args)}\033[0m")
    print()


def _print_tool_result(result: str, max_len: int = 500):
    """Print tool result in dimmed text, truncated if too long."""
    display = result if len(result) <= max_len else result[:max_len] + f"\n    … ({len(result)} chars total)"
    print(f"\033[90m  ↳ {display}\033[0m\n")


def format_usage_summary(token_stats: dict, usage=None) -> str:
    """Format a concise usage summary for IM channels.

    Args:
        token_stats: Cumulative token stats for the current task.
        usage: Optional last-round usage object (for context percentage).

    Returns:
        A short, human-readable usage summary string.
    """
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

    # Context usage percentage
    if usage:
        prompt_tokens = usage.prompt_tokens or 0
        pct = min(prompt_tokens / CONTEXT_LIMIT * 100, 100)
        parts.append(f"| context {pct:.0f}%")

    return " ".join(parts)


def _print_context_bar(usage):
    """Print a context window usage progress bar.

    Shows the current context size (= prompt_tokens of the last API call),
    which equals the total history being sent to the LLM right now.
    This grows as the conversation progresses.
    """
    if not usage:
        return
    prompt_tokens = usage.prompt_tokens or 0
    pct = min(prompt_tokens / CONTEXT_LIMIT * 100, 100)

    # Color: green → yellow → red
    if pct < 50:
        color = "\033[32m"   # Green
    elif pct < 80:
        color = "\033[33m"   # Yellow
    else:
        color = "\033[31m"   # Red

    bar_width = 20
    filled = int(bar_width * pct / 100)
    bar = "█" * filled + "░" * (bar_width - filled)

    def fmt(n):
        return f"{n/1000:.0f}k" if n >= 1000 else str(n)

    print(f"\n\n{color}  ⟨{bar}⟩ {pct:.0f}%  {fmt(prompt_tokens)} / {fmt(CONTEXT_LIMIT)}\033[0m")


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

    # Initialize / reuse token stats
    if token_stats is None:
        token_stats = make_empty_token_stats()

    last_prompt_tokens = 0  # Track for compression decisions

    active_model = model_override or MODEL
    _stopped = False

    for round_num in range(1, MAX_ROUNDS + 1):
        # ── Check for user-requested stop ──
        if _stopped or (check_stop and check_stop()):
            print("\n🛑 Stop requested by user")
            log_event(log_file, {"type": "stopped_by_user", "round": round_num})
            break

        print(f"\n=============== Round {round_num} ===============\n")

        try:
            # ── Context compression: summarize old turns if context is getting full ──
            if last_prompt_tokens and needs_compression(last_prompt_tokens):
                old_len = len(history)
                history = compress_history(history, last_prompt_tokens)
                if len(history) < old_len:
                    # Rebuild system prompt: pick up new skills, memory, timestamp
                    # (prompt cache is already invalidated by compression, so this is free)
                    from prompt import make_system_prompt
                    history[0] = {"role": "system", "content": make_system_prompt()}

                    print(f"\033[35m  📦 Context compressed: {old_len} → {len(history)} messages\033[0m")
                    log_event(log_file, {
                        "type": "context_compressed",
                        "round": round_num,
                        "old_messages": old_len,
                        "new_messages": len(history),
                    })

            # ── Sanitize history: fix orphaned tool_calls from crashed runs ──
            history = _sanitize_history(history)

            # ── Streaming LLM call ──
            # Iterate over chunks, emit text_chunk/reasoning_chunk events in real-time,
            # accumulate full content and tool_calls for post-processing.
            active_tools = tools_override or tools_schema
            stream = call_llm_stream(history, model=active_model, tools=active_tools)

            accumulated_content = ""
            accumulated_reasoning = ""
            accumulated_tool_calls = {}  # id -> {name, arguments_str}
            usage = None

            for chunk in stream:
                # Check for stop during streaming (so user doesn't have to wait
                # for the entire LLM response to finish)
                if check_stop and check_stop():
                    print("\n🛑 Stop requested — aborting LLM stream")
                    _stopped = True
                    break

                delta = chunk.choices[0].delta if chunk.choices else None

                # Capture usage from the final chunk (may not exist on all chunks)
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage:
                    usage = chunk_usage

                if not delta:
                    continue

                # ── Reasoning chunks ──
                reasoning_chunk = getattr(delta, "reasoning_content", None) or ""
                if reasoning_chunk:
                    accumulated_reasoning += reasoning_chunk
                    print(f"\033[96m{reasoning_chunk}\033[0m", end="", flush=True)
                    if on_event:
                        on_event({"type": "reasoning_chunk", "content": reasoning_chunk, "round": round_num})

                # ── Text chunks ──
                text_chunk = delta.content or ""
                if text_chunk:
                    accumulated_content += text_chunk
                    print(text_chunk, end="", flush=True)
                    if on_event:
                        on_event({"type": "text_chunk", "content": text_chunk, "round": round_num})

                # ── Tool call chunks (accumulate, don't execute yet) ──
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
                print()  # Newline after reasoning

            # ── Post-stream: reconstruct the message object ──
            # Build a message-like object compatible with history and downstream logic
            from litellm import ModelResponse
            from litellm.types.utils import Message as LitellmMessage, ChatCompletionMessageToolCall, Function

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

            # Extract cache info & update token stats
            cache_info = extract_cache_info(usage) if usage else {}
            if usage:
                update_token_stats(token_stats, usage, cache_info)
                last_prompt_tokens = usage.prompt_tokens or 0

            # Log raw LLM response
            log_event(log_file, {
                "type": "llm_response",
                "round": round_num,
                "model": active_model,
                "message": serialize_message(msg),
                "usage": serialize_usage(usage) if usage else {},
                "cache": cache_info,
                "token_stats_cumulative": dict(token_stats),
            })

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

            # Append to history
            history.append(msg)

            # No tool calls → task complete for this turn
            if not msg.tool_calls:
                # Show context usage
                _print_context_bar(usage)
                # Notify external: agent produced its final reply
                if on_event and final_text:
                    on_event({"type": "final_reply", "content": final_text, "round": round_num})
                # Notify external: usage report
                if on_event:
                    on_event({
                        "type": "usage_report",
                        "summary": format_usage_summary(token_stats, usage),
                        "token_stats": dict(token_stats),
                        "prompt_tokens": usage.prompt_tokens or 0 if usage else 0,
                        "round": round_num,
                    })
                break

            # Has tool calls AND text → send narration to IM channels
            if on_event and final_text:
                on_event({"type": "narration", "content": final_text, "round": round_num})

            # Has tool calls → execute each one
            for tc in msg.tool_calls:
                # Check for stop between tool calls
                if check_stop and check_stop():
                    print("\n🛑 Stop requested — aborting remaining tool calls")
                    # Fill in dummy results for unanswered tool calls
                    answered_ids = {item.get("tool_call_id") for item in history
                                    if isinstance(item, dict) and item.get("role") == "tool"}
                    for remaining_tc in msg.tool_calls:
                        if remaining_tc.id not in answered_ids:
                            history.append({
                                "role": "tool",
                                "tool_call_id": remaining_tc.id,
                                "content": "[Stopped by user]",
                            })
                    log_event(log_file, {"type": "stopped_by_user", "round": round_num})
                    _stopped = True
                    break

                tool_name = tc.function.name
                args = json.loads(tc.function.arguments)
                _print_tool_call(tool_name, args)

                # Notify external: tool call started
                if on_event:
                    on_event({"type": "tool_call", "tool_name": tool_name, "args": args, "round": round_num})

                func = tools_map.get(tool_name)
                if func:
                    # Filter out hallucinated args the function doesn't accept
                    import inspect
                    sig = inspect.signature(func)
                    valid_params = set(sig.parameters.keys())
                    has_var_keyword = any(
                        p.kind == inspect.Parameter.VAR_KEYWORD
                        for p in sig.parameters.values()
                    )
                    if not has_var_keyword:
                        filtered_args = {k: v for k, v in args.items() if k in valid_params}
                    else:
                        filtered_args = args
                    tool_result = str(func(**filtered_args))
                elif composio_tools.is_composio_tool(tool_name):
                    # Composio meta-tool: execute via Composio SDK (with result cleaning)
                    tool_result = composio_tools.execute(tool_name, args)
                elif tool_name in TOOL_REDIRECTS:
                    # Tool was intentionally filtered — return guidance instead of error
                    tool_result = TOOL_REDIRECTS[tool_name]
                else:
                    tool_result = f"Error: unknown tool {tool_name}"

                # ── Spill large tool outputs to file ──
                # Skip spill in two cases:
                # 1. Composio tools: already cleaned by clean_tool_result
                # 2. read_file: has its own line-range params; spilling would cause
                #    recursive loops (LLM reads spill file → output spilled again)
                if not composio_tools.is_composio_tool(tool_name) and tool_name != "read_file":
                    tool_result = spill_tool_output(tool_result, tool_name=tool_name)

                _print_tool_result(tool_result)

                # Notify external: tool call result
                if on_event:
                    on_event({"type": "tool_result", "tool_name": tool_name, "result": tool_result, "round": round_num})

                # Log tool call result
                log_event(log_file, {
                    "type": "tool_result",
                    "round": round_num,
                    "tool_name": tool_name,
                    "tool_call_id": tc.id,
                    "args": args,
                    "result": tool_result,
                })

                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })
        except KeyboardInterrupt:
            print("\n\n⚡ User interrupted the current task")
            if history and hasattr(history[-1], "tool_calls") and history[-1].tool_calls:
                answered_ids = set()
                for item in history:
                    if isinstance(item, dict) and item.get("role") == "tool":
                        answered_ids.add(item.get("tool_call_id"))
                for tc in history[-1].tool_calls:
                    if tc.id not in answered_ids:
                        history.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": "[User interrupted this tool call]",
                        })
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

    return history, token_stats


def main():
    # Recover any orphaned log files from previous crashes
    recover_orphaned_logs()

    # Create log
    log_file = create_log_file()
    log_event(log_file, {"type": "session_start", "model": MODEL})

    SYSTEM_PROMPT = make_system_prompt()
    history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("🚀 Agent started (Ctrl+C to interrupt, type 'exit' to quit)")

    # Start Composio trigger listener (if enabled)
    from composio_triggers import start_listener as start_trigger_listener
    start_trigger_listener()

    # Session-wide token stats (cumulative across multiple agent_loop calls)
    session_token_stats = make_empty_token_stats()

    # Agent loop
    while True:
        try:
            user_message = input("\nYou: ")

            if user_message.strip().lower() in ("exit", "quit"):
                print("👋 Goodbye!")
                break

            history.append({"role": "user", "content": user_message})
            log_event(log_file, {"type": "user_input", "content": user_message})

            history, session_token_stats = agent_loop(history, log_file, session_token_stats)

        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break

    # ── Session summary ──
    # total_prompt_tokens = sum of prompt_tokens from ALL API calls (billing total)
    # last_prompt_tokens  = prompt_tokens of the last API call (= current context size)
    def _fmt(n):
        return f"{n/1000:.1f}k" if n >= 1000 else str(n)

    last_ctx = session_token_stats.get('last_prompt_tokens', 0)
    print(
        f"\n\033[35m{'═'*50}\n"
        f"📊 Session Summary\n"
        f"   Context:    {_fmt(last_ctx)} / {_fmt(CONTEXT_LIMIT)}\n"
        f"   Billed:     {_fmt(session_token_stats['total_tokens'])} "
        f"(prompt {_fmt(session_token_stats['total_prompt_tokens'])} "
        f"+ completion {_fmt(session_token_stats['total_completion_tokens'])})\n"
        f"   Cached:     {_fmt(session_token_stats['total_cached_tokens'])}\n"
        f"   API Calls:  {session_token_stats['total_api_calls']}\n"
        f"{'═'*50}\033[0m"
    )
    log_event(log_file, {"type": "session_end", "session_token_stats": session_token_stats})
    close_log_file(log_file)


if __name__ == "__main__":
    main()