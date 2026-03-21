"""
Agent core — the main agent loop and CLI entry point.
"""

import re
import json
from tools import tools_map, tools_schema
from config import MODEL, MAX_ROUNDS, CONTEXT_LIMIT
from prompt import make_system_prompt
from llm import call_llm, extract_cache_info, update_token_stats, make_empty_token_stats
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
    """Print a Cursor-style context usage progress bar."""
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

    # Progress bar
    bar_width = 20
    filled = int(bar_width * pct / 100)
    bar = "█" * filled + "░" * (bar_width - filled)

    # Format token count (e.g. 128k / 200k)
    def fmt(n):
        return f"{n/1000:.0f}k" if n >= 1000 else str(n)

    print(f"\n\n{color}  ⟨{bar}⟩ {pct:.0f}%  context: {fmt(prompt_tokens)} / {fmt(CONTEXT_LIMIT)}\033[0m")


def agent_loop(history: list[dict], log_file=None, token_stats: dict = None,
               on_event=None, check_stop=None, model_override: str = None):
    """Core Agent loop.

    Args:
        check_stop: Optional callable that returns True if the user requested a stop.
        model_override: Optional model name to use instead of the default.
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

            completion = call_llm(history, model=active_model, tools=tools_schema)

            msg = completion.choices[0].message

            # Extract cache info & update token stats
            cache_info = extract_cache_info(completion.usage)
            usage = completion.usage
            update_token_stats(token_stats, usage, cache_info)
            last_prompt_tokens = usage.prompt_tokens or 0

            # Log raw LLM response
            log_event(log_file, {
                "type": "llm_response",
                "round": round_num,
                "model": active_model,
                "message": serialize_message(msg),
                "usage": serialize_usage(completion.usage),
                "cache": cache_info,
                "token_stats_cumulative": dict(token_stats),
            })

            # Output reasoning (if any)
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                print(f"\033[96m{reasoning}\033[0m\n")

            # Output text (if any)
            final_text = ""
            if msg.content:
                # Filter out Qwen3's empty <think> tags, clean up extra blank lines
                text = re.sub(r"<think>.*?</think>", "", msg.content, flags=re.DOTALL)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if text:
                    final_text = text
                    print(text, end="")

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
                else:
                    tool_result = f"Error: unknown tool {tool_name}"

                # ── Spill large tool outputs to file ──
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
    print(
        f"\n\033[35m{'═'*50}\n"
        f"📊 Session Token Usage\n"
        f"   Prompt:     {session_token_stats['total_prompt_tokens']}\n"
        f"   Completion: {session_token_stats['total_completion_tokens']}\n"
        f"   Total:      {session_token_stats['total_tokens']}\n"
        f"   Cached:     {session_token_stats['total_cached_tokens']}\n"
        f"   API Calls:  {session_token_stats['total_api_calls']}\n"
        f"{'═'*50}\033[0m"
    )
    log_event(log_file, {"type": "session_end", "session_token_stats": session_token_stats})
    close_log_file(log_file)


if __name__ == "__main__":
    main()