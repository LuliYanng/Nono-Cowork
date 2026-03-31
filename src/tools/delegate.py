"""
Delegate tool — expose the subagent framework to the main agent.

The main agent delegates complex tasks to an independent subagent.
Execution is synchronous (blocking) — the main agent waits for the result.

This is intentional:
- The main agent delegates because it NEEDS the result to continue
- There's no meaningful work the agent can do while waiting
- Running tools concurrently (especially self-agent) risks race conditions
- The user expects one complete answer, not status update noise
"""

import os
import logging
from tools.registry import tool
from subagent import get_provider, list_providers
from context import get_context

logger = logging.getLogger("tools.delegate")


@tool(
    name="delegate",
    tags=["admin"],
    description=(
        "Delegate a complex task to an independent sub-agent for execution. "
        "The sub-agent has its own context window and tools, making it ideal for:\n"
        "- Tasks that require deep analysis of large codebases or many files\n"
        "- Research tasks that need web search and information synthesis\n"
        "- Complex multi-step operations (refactoring, documentation, setup)\n"
        "- Tasks you want to isolate from the current conversation context\n\n"
        "The sub-agent has NO knowledge of your current conversation — include "
        "all necessary context in the task and context fields.\n\n"
        "This call blocks until the sub-agent finishes (max 5 minutes). "
        "Do NOT delegate simple tasks you can handle in 2-3 rounds yourself."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "Clear, detailed description of what the sub-agent should do. "
                    "Include all necessary context — the sub-agent cannot see "
                    "your current conversation."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Optional background information, relevant code snippets, "
                    "or file contents to provide to the sub-agent."
                ),
            },
            "working_dir": {
                "type": "string",
                "description": "Working directory for the sub-agent.",
                "default": "~",
            },
            "system_prompt": {
                "type": "string",
                "description": (
                    "Optional custom system prompt override. "
                    "Leave EMPTY to use the provider's default (recommended). "
                    "Only set this when you need to enforce specific behavior constraints."
                ),
            },
            "provider": {
                "type": "string",
                "description": (
                    "Which sub-agent provider to use. Leave empty for auto-select (recommended). "
                    "Options: 'gemini-cli' (powerful, isolated process), "
                    "'self' (uses own agent loop, always available)."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "Model for the sub-agent. Leave empty for provider's default (gemini-2.5-pro). "
                    "For gemini-cli: Gemini model names (e.g. 'gemini-2.5-flash', 'gemini-2.5-pro', "
                    "'gemini-3-flash-preview', 'gemini-3.1-pro-preview'). "
                    "For self: LiteLLM format (e.g. 'gemini/gemini-2.5-pro', 'deepseek/deepseek-chat')."
                ),
            },
        },
        "required": ["task"],
    },
)
def delegate(
    task: str,
    context: str = "",
    working_dir: str = "~",
    provider: str = "",
    model: str = "",
    system_prompt: str = "",
) -> str:
    """Delegate a task to a subagent and return its result."""
    if context:
        full_task = f"## Background\n{context}\n\n## Task\n{task}"
    else:
        full_task = task

    selected = get_provider(name=provider if provider else None)

    # Pull callbacks from execution context
    ctx = get_context()
    # Use subagent_check_stop: triggers on EITHER subagent-only stop OR full stop
    subagent_check_stop = ctx.get("subagent_check_stop")
    status_func = ctx.get("status_func")
    user_id = ctx.get("user_id")

    logger.info("Delegating to '%s' (model=%s): %s", selected.name, model or "default", task[:80])
    if status_func:
        status_func(f"🤖 Delegating to {selected.name}...")

    try:
        result = selected.run(
            task=full_task,
            system_prompt=system_prompt,
            working_dir=working_dir,
            model=model,
            check_stop=subagent_check_stop,
        )
    finally:
        # Clear subagent stop flag so main agent can continue normally
        # (only relevant when user clicked "Stop Subagent", not full stop)
        if user_id:
            from session import sessions
            sessions.clear_subagent_stop(user_id)

    return f"[Subagent: {selected.name}]\n\n{result}"


@tool(
    name="delegate_status",
    tags=["read"],
    description="Show available sub-agent providers and which one is currently active.",
    parameters={
        "type": "object",
        "properties": {},
    },
)
def delegate_status() -> str:
    """List available subagent providers."""
    providers = list_providers()
    active = get_provider()

    lines = ["## Sub-agent Providers\n"]
    for p in providers:
        status = "✅ available" if p["available"] else "❌ not available"
        active_marker = " ← active" if p["name"] == active.name else ""
        lines.append(f"- **{p['name']}**: {p['description']} ({status}{active_marker})")

    env_override = os.getenv("DELEGATE_PROVIDER", "").strip()
    if env_override:
        lines.append(f"\nDELEGATE_PROVIDER={env_override}")
    else:
        lines.append("\nDELEGATE_PROVIDER not set (auto-select mode)")

    return "\n".join(lines)
