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


def agent_loop(history: list[dict], log_file=None, token_stats: dict = None, on_event=None):
    """Core Agent loop."""

    # Initialize / reuse token stats
    if token_stats is None:
        token_stats = make_empty_token_stats()

    for round_num in range(1, MAX_ROUNDS + 1):
        print(f"\n=============== Round {round_num} ===============\n")

        try:
            completion = call_llm(history, tools=tools_schema)

            msg = completion.choices[0].message

            # Extract cache info & update token stats
            cache_info = extract_cache_info(completion.usage)
            usage = completion.usage
            update_token_stats(token_stats, usage, cache_info)

            # Log raw LLM response
            log_event(log_file, {
                "type": "llm_response",
                "round": round_num,
                "model": MODEL,
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
                break

            # Has tool calls → execute each one
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                args = json.loads(tc.function.arguments)
                print(f"Tool call >>>\n {tool_name}({args})\n")

                # Notify external: tool call started
                if on_event:
                    on_event({"type": "tool_call", "tool_name": tool_name, "args": args, "round": round_num})

                func = tools_map.get(tool_name)
                if func:
                    tool_result = str(func(**args))
                else:
                    tool_result = f"Error: unknown tool {tool_name}"

                print(f"\033[90mTool result >>>\n {tool_result}\033[0m\n")

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