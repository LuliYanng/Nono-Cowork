"""
Subagent provider base class.

To add a new provider:
  1. Create a new file in src/subagent/ (e.g. claude_code.py)
  2. Subclass SubagentProvider and implement run()
  3. Register it in __init__.py's _PROVIDERS dict
"""

from abc import ABC, abstractmethod


class SubagentProvider(ABC):
    """Base class for subagent providers.

    Each provider wraps a different execution backend (self agent loop,
    Gemini CLI, Claude Code, etc.) behind a uniform interface.

    All providers execute synchronously (blocking). This is intentional:
    the main agent delegates because it needs the result to continue,
    so there's no meaningful work it can do while waiting.
    """

    name: str = "base"
    description: str = "Base provider"

    @abstractmethod
    def run(
        self,
        task: str,
        system_prompt: str = "",
        working_dir: str = "~",
        model: str = "",
        check_stop=None,
        timeout: int = 300,
    ) -> str:
        """Execute a task and return the result text.

        Blocks until the subagent completes or is stopped/timed out.

        Args:
            task: Task description / instructions for the subagent.
            system_prompt: Optional system prompt. Empty = use provider's default.
            working_dir: Working directory for the subagent.
            model: Optional model override. Empty = use provider's default.
            check_stop: Optional callable returning True if user requested stop.
            timeout: Hard time limit in seconds (default 300 = 5 minutes).

        Returns:
            The subagent's final text response.
        """
        ...

    def run_with_history(
        self,
        task: str,
        system_prompt: str = "",
        working_dir: str = "~",
        model: str = "",
        check_stop=None,
        timeout: int = 300,
    ) -> tuple[str, list, dict]:
        """Execute a task and return (final_text, history, stats).

        The history is a list of session-compatible message dicts
        (role/content/tool_calls) suitable for saving as an autonomous session
        or continuing a conversation.

        Default implementation wraps run() with minimal history.
        Subclasses can override to provide richer execution traces.
        """
        text = self.run(task, system_prompt, working_dir, model, check_stop, timeout)
        history = [
            {"role": "system", "content": system_prompt or "(default system prompt)"},
            {"role": "user", "content": task},
            {"role": "assistant", "content": text},
        ]
        return text, history, {}

    def is_available(self) -> bool:
        """Check if this provider is usable (installed, configured, etc.)."""
        return True
