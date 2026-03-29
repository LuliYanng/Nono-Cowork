"""
Gemini CLI subagent provider — delegates tasks to Google's Gemini CLI.

Requires:
  - Gemini CLI installed: npm install -g @google/gemini-cli
  - Google account authenticated: gemini auth login
  - Ideally a Google Ultra plan for generous free quota

The provider auto-detects if Gemini CLI is installed and marks itself
unavailable if not — the framework will fall back to another provider.

Output format: Uses --output-format stream-json (NDJSON) to capture
the full execution trace (messages, tool calls, results, stats),
enabling session-compatible history for notification auditing.
"""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import logging
from pathlib import Path
from subagent.base import SubagentProvider

logger = logging.getLogger("subagent.gemini_cli")

# Gemini CLI model (override via DELEGATE_GEMINI_MODEL env var)
_DEFAULT_MODEL = "gemini-2.5-pro"

# Cached gemini binary path (resolved once, reused)
_gemini_path: str | None = None


def _find_gemini() -> str | None:
    """Locate the gemini binary, searching beyond the current PATH.

    systemd services have a minimal PATH that doesn't include user-local
    directories like ~/.npm-global/bin. This function checks:
      1. shutil.which() (respects current PATH)
      2. Common npm global install locations
      3. DELEGATE_GEMINI_PATH env var (explicit override)
    """
    global _gemini_path
    if _gemini_path is not None:
        return _gemini_path if _gemini_path else None

    # Explicit override
    explicit = os.getenv("DELEGATE_GEMINI_PATH", "").strip()
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        _gemini_path = explicit
        logger.info("Gemini CLI found via DELEGATE_GEMINI_PATH: %s", explicit)
        return _gemini_path

    # Standard PATH lookup
    found = shutil.which("gemini")
    if found:
        _gemini_path = found
        logger.info("Gemini CLI found in PATH: %s", found)
        return _gemini_path

    # Probe common npm global install locations
    home = Path.home()
    candidates = [
        home / ".npm-global" / "bin" / "gemini",
        home / ".local" / "bin" / "gemini",
        home / ".nvm" / "current" / "bin" / "gemini",      # nvm users
        Path("/usr/local/bin/gemini"),
    ]
    # Also check NVM versioned dirs
    nvm_dir = home / ".nvm" / "versions" / "node"
    if nvm_dir.is_dir():
        for ver_dir in sorted(nvm_dir.iterdir(), reverse=True):
            candidates.append(ver_dir / "bin" / "gemini")

    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            _gemini_path = str(cand)
            logger.info("Gemini CLI found at: %s", _gemini_path)
            return _gemini_path

    _gemini_path = ""  # cache negative result
    logger.debug("Gemini CLI not found")
    return None


def _read_stdout_lines(process, output_queue: queue.Queue):
    """Read stdout line by line in a background thread.

    Puts each line as a string into the queue.
    Puts None as sentinel when stdout is exhausted.
    """
    try:
        for line in process.stdout:
            stripped = line.strip()
            if stripped:
                output_queue.put(stripped)
    except Exception as e:
        logger.debug("stdout reader error: %s", e)
    finally:
        output_queue.put(None)  # sentinel: no more output


class GeminiCliProvider(SubagentProvider):
    """Subagent that delegates to Gemini CLI in headless mode.

    Uses --output-format stream-json to capture the full execution trace
    (messages, tool calls, stats) as NDJSON for session-compatible history.

    System prompt behavior:
    - If system_prompt is empty → Gemini CLI uses its built-in prompt (recommended)
    - If system_prompt is provided → overrides via GEMINI_SYSTEM_MD
    """

    name = "gemini-cli"
    description = "Gemini CLI (powerful, free with Google Ultra plan)"

    def is_available(self) -> bool:
        """Check if the 'gemini' command is installed."""
        return _find_gemini() is not None

    def run(self, task: str, system_prompt: str = "", working_dir: str = "~",
            model: str = "", check_stop=None, timeout: int = 300) -> str:
        text, _, _ = self.run_with_history(
            task, system_prompt, working_dir, model, check_stop, timeout
        )
        return text

    def run_with_history(self, task: str, system_prompt: str = "",
                         working_dir: str = "~", model: str = "",
                         check_stop=None, timeout: int = 300
                         ) -> tuple[str, list, dict]:
        gemini_bin = _find_gemini()
        if not gemini_bin:
            err = "❌ Gemini CLI not found. Install with: npm install -g @google/gemini-cli"
            return err, [{"role": "assistant", "content": err}], {}

        # Priority: explicit param > env var > default
        model = model or os.getenv("DELEGATE_GEMINI_MODEL", _DEFAULT_MODEL)
        approval = os.getenv("DELEGATE_GEMINI_APPROVAL", "auto_edit")
        working_dir = os.path.expanduser(working_dir)

        env = os.environ.copy()
        system_file = None

        try:
            # Only override system prompt if explicitly provided
            if system_prompt:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", prefix="subagent_system_",
                    delete=False, dir="/tmp"
                ) as f:
                    f.write(system_prompt)
                    system_file = f.name
                env["GEMINI_SYSTEM_MD"] = system_file

            cmd = [
                gemini_bin,
                "-p", task,
                "--output-format", "stream-json",
                "--approval-mode", approval,
                "--model", model,
            ]

            logger.info(
                "Gemini CLI starting (bin=%s, model=%s, approval=%s, cwd=%s, timeout=%ds)",
                gemini_bin, model, approval, working_dir, timeout,
            )

            # Start process with line-buffered stdout
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=working_dir, env=env,
                bufsize=1,  # line buffered
            )

            # Read stdout in a background thread
            line_queue: queue.Queue[str | None] = queue.Queue()
            reader = threading.Thread(
                target=_read_stdout_lines,
                args=(process, line_queue),
                daemon=True,
            )
            reader.start()

            # Collect all NDJSON lines while checking stop/timeout
            ndjson_lines: list[str] = []
            start_time = time.monotonic()

            while True:
                try:
                    line = line_queue.get(timeout=1.0)
                    if line is None:
                        break  # stdout exhausted
                    ndjson_lines.append(line)
                except queue.Empty:
                    pass  # No output yet, check stop/timeout

                # Check user-requested stop
                if check_stop and check_stop():
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    logger.info("Gemini CLI stopped by user after %.0fs",
                                time.monotonic() - start_time)
                    text = "🛑 Sub-agent stopped by user request."
                    history = self._parse_ndjson_to_history(ndjson_lines, system_prompt)
                    history.append({"role": "assistant", "content": text})
                    return text, history, {}

                # Check hard timeout
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    logger.warning("Gemini CLI timed out after %.0fs", elapsed)
                    text = f"⏰ Sub-agent timed out after {timeout}s. The task may be too complex."
                    history = self._parse_ndjson_to_history(ndjson_lines, system_prompt)
                    history.append({"role": "assistant", "content": text})
                    return text, history, {}

            # Wait for process to fully exit
            process.wait(timeout=5)
            duration = time.monotonic() - start_time

            # Also capture stderr for error reporting
            stderr = ""
            try:
                stderr = process.stderr.read() if process.stderr else ""
            except Exception:
                pass

            if process.returncode != 0 and not ndjson_lines:
                error_msg = stderr.strip() or "Unknown error"
                text = f"❌ Gemini CLI failed (exit {process.returncode})\n{error_msg}"
                return text, [{"role": "assistant", "content": text}], {}

            # Parse NDJSON into history + extract final text + stats
            return self._parse_ndjson_result(ndjson_lines, model, system_prompt, duration)

        except FileNotFoundError:
            err = "❌ Gemini CLI not found. Install with: npm install -g @google/gemini-cli"
            return err, [{"role": "assistant", "content": err}], {}
        except Exception as e:
            logger.error("Gemini CLI error: %s", e)
            err = f"❌ Gemini CLI error: {str(e)}"
            return err, [{"role": "assistant", "content": err}], {}
        finally:
            if system_file and os.path.exists(system_file):
                os.unlink(system_file)

    def _parse_ndjson_result(
        self, lines: list[str], model: str, system_prompt: str, duration: float
    ) -> tuple[str, list, dict]:
        """Parse NDJSON stream-json output into (final_text, history, stats)."""
        history = self._parse_ndjson_to_history(lines, system_prompt)
        stats = {}
        final_text = ""

        # Extract stats from the 'result' event and accumulate assistant text
        assistant_chunks: list[str] = []
        for line_str in lines:
            try:
                event = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            evt_type = event.get("type", "")

            if evt_type == "result":
                stats = event.get("stats", {})

            elif evt_type == "message" and event.get("role") == "assistant":
                content = event.get("content", "")
                if content:
                    assistant_chunks.append(content)

        # Final text is all assistant chunks concatenated
        final_text = "".join(assistant_chunks)

        if not final_text:
            # Fallback: get from the last assistant message in history
            for msg in reversed(history):
                if msg.get("role") == "assistant" and msg.get("content"):
                    final_text = msg["content"]
                    break

        if final_text:
            final_text += f"\n\n---\n📊 Executed by: Gemini CLI ({model}), {duration:.0f}s"

        # Convert stream-json stats to session-compatible format
        normalized_stats = self._normalize_stats(stats, duration)

        return final_text or "(Gemini CLI produced no output)", history, normalized_stats

    def _parse_ndjson_to_history(self, lines: list[str], system_prompt: str) -> list[dict]:
        """Parse NDJSON lines into a session-compatible message history.

        Handles:
        - init → ignored (metadata only)
        - message (role=user) → user message
        - message (role=assistant, delta=true) → accumulated into assistant text
        - tool_call → assistant message with tool_calls
        - tool_result → tool message
        - result → ignored (stats extracted separately)
        """
        history: list[dict] = []
        if system_prompt:
            history.append({"role": "system", "content": system_prompt})

        # State for accumulating assistant deltas
        assistant_text_parts: list[str] = []
        current_tool_calls: list[dict] = []

        def _flush_assistant():
            """Flush accumulated assistant text into a history message."""
            nonlocal assistant_text_parts
            if assistant_text_parts:
                history.append({
                    "role": "assistant",
                    "content": "".join(assistant_text_parts),
                })
                assistant_text_parts = []

        for line_str in lines:
            try:
                event = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            evt_type = event.get("type", "")

            if evt_type == "message":
                role = event.get("role", "")
                content = event.get("content", "")
                is_delta = event.get("delta", False)

                if role == "user":
                    _flush_assistant()
                    history.append({"role": "user", "content": content})

                elif role == "assistant":
                    if is_delta:
                        assistant_text_parts.append(content)
                    else:
                        _flush_assistant()
                        if content:
                            history.append({"role": "assistant", "content": content})

            elif evt_type == "tool_call":
                # Flush any pending assistant text before the tool call
                _flush_assistant()
                tool_name = event.get("name", event.get("tool", "unknown"))
                tool_args = event.get("args", event.get("arguments", {}))
                tool_id = event.get("id", event.get("tool_call_id", f"call_{len(history)}"))

                history.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args, ensure_ascii=False)
                                         if isinstance(tool_args, dict) else str(tool_args),
                        },
                    }],
                })

            elif evt_type == "tool_result":
                result_content = event.get("result", event.get("output", ""))
                tool_id = event.get("id", event.get("tool_call_id", ""))
                if isinstance(result_content, dict):
                    result_content = json.dumps(result_content, ensure_ascii=False)
                history.append({
                    "role": "tool",
                    "content": str(result_content),
                    "tool_call_id": tool_id,
                })

            # 'init' and 'result' events are metadata, not conversation

        # Flush any remaining assistant text
        _flush_assistant()

        return history

    @staticmethod
    def _normalize_stats(raw_stats: dict, duration: float) -> dict:
        """Convert Gemini CLI stats to session-compatible token_stats format."""
        if not raw_stats:
            return {
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_cached_tokens": 0,
                "total_api_calls": 0,
            }

        # stream-json result.stats format
        input_tokens = raw_stats.get("input_tokens", raw_stats.get("input", 0))
        output_tokens = raw_stats.get("output_tokens", 0)
        total_tokens = raw_stats.get("total_tokens", input_tokens + output_tokens)
        cached = raw_stats.get("cached", 0)
        tool_calls = raw_stats.get("tool_calls", 0)

        return {
            "total_prompt_tokens": input_tokens,
            "total_completion_tokens": output_tokens,
            "total_tokens": total_tokens,
            "total_cached_tokens": cached,
            "total_api_calls": 1 + tool_calls,  # 1 for the main request + tool calls
            "duration_s": round(duration, 1),
            "raw": raw_stats,  # keep raw stats for detailed view
        }
