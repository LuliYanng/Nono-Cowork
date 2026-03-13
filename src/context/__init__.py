"""
Context engineering — execution context, compression, trimming, and memory management.

This package provides:
  - Execution context (user_id, channel_name) for tools during Agent execution
  - Context compression (sliding-window summarization)
  - Tool output trimming

Execution context uses threading.local() so each thread has its own context.
Tools like scheduled task creation need to know WHO is asking and WHICH
channel to reply to — these come from the environment, not from the Agent.

Usage:
    # In agent_runner (set context before running agent_loop):
    from context import set_context, clear_context
    set_context(user_id="ou_xxx", channel_name="feishu")
    ...
    clear_context()

    # In tools (read context):
    from context import get_context
    ctx = get_context()
    ctx["user_id"], ctx["channel_name"]
"""

import threading

_local = threading.local()


def set_context(user_id: str, channel_name: str):
    """Set the execution context for the current thread."""
    _local.user_id = user_id
    _local.channel_name = channel_name


def get_context() -> dict:
    """Get the current execution context. Returns empty dict if not set."""
    user_id = getattr(_local, "user_id", None)
    channel_name = getattr(_local, "channel_name", None)
    if user_id and channel_name:
        return {"user_id": user_id, "channel_name": channel_name}
    return {}


def clear_context():
    """Clear the execution context for the current thread."""
    _local.user_id = None
    _local.channel_name = None
