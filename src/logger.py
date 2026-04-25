"""Agent event logger — JSONL format, real-time write

Log lifecycle:
- CLI mode: one log file per interactive session (open on start, close on exit)
- IM mode:  one log file per user session (open on first message, close on reset/shutdown)

Crash safety:
- Every event is flushed to disk immediately
- On startup, any orphaned .jsonl files from a previous crash are converted to .json
- An atexit handler ensures all open log files are closed on normal shutdown
"""

import atexit
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("logger")

LOG_DIR = Path(__file__).parent.parent / "logs"

# Track all open log files for atexit cleanup
_open_log_files: list = []


def recover_orphaned_logs():
    """Convert any leftover .jsonl files from a previous crash to .json.

    Should be called once at startup.
    """
    if not LOG_DIR.exists():
        return
    for jsonl_path in LOG_DIR.glob("*.jsonl"):
        _convert_jsonl_to_json(jsonl_path)
        logger.info(f"Recovered orphaned log: {jsonl_path}")


def create_log_file():
    """Create a log file and return the file handle."""
    LOG_DIR.mkdir(exist_ok=True)
    filename = time.strftime("%Y-%m-%d_%H-%M-%S") + ".jsonl"
    filepath = LOG_DIR / filename
    f = open(filepath, "a", encoding="utf-8")
    _open_log_files.append(f)
    print(f"📝 Log file: {filepath}")
    return f


def log_event(log_file, event: dict):
    """Write a log event (flushed immediately for crash safety).

    Silently no-ops if the file has already been closed (e.g. a zombie
    agent thread whose session got reset mid-run) — crashing here would
    take down the whole channel thread.
    """
    if log_file is None or getattr(log_file, "closed", False):
        return
    event["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        log_file.flush()
    except ValueError:
        # File was closed between the check above and the write (race).
        pass


def close_log_file(log_file):
    """Close the log file and convert JSONL to pretty-printed JSON."""
    if log_file is None:
        return
    try:
        log_file.close()
    except Exception:
        pass

    # Remove from tracked list
    if log_file in _open_log_files:
        _open_log_files.remove(log_file)

    # Convert JSONL → pretty JSON
    jsonl_path = Path(log_file.name)
    _convert_jsonl_to_json(jsonl_path)


def _convert_jsonl_to_json(jsonl_path: Path):
    """Convert a JSONL file to pretty-printed JSON, then remove the JSONL file."""
    if not jsonl_path.exists():
        return

    json_path = jsonl_path.with_suffix(".json")
    try:
        entries = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        if not entries:
            # Empty file, just remove it
            jsonl_path.unlink()
            return
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        jsonl_path.unlink()
        print(f"📋 Log saved: {json_path}")
    except Exception as e:
        print(f"⚠️ Failed to convert JSONL to JSON: {e}")


def _atexit_close_all():
    """Close all open log files on process exit (safety net)."""
    for f in list(_open_log_files):
        try:
            close_log_file(f)
        except Exception:
            pass


atexit.register(_atexit_close_all)


def serialize_message(msg) -> dict:
    """Serialize an OpenAI message object to a JSON-serializable dict."""
    d = {"role": msg.role, "content": msg.content}
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning:
        d["reasoning_content"] = reasoning
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return d


def serialize_usage(usage) -> dict:
    """Serialize a usage object."""
    if usage is None:
        return {}

    def read_field(obj, name: str, default=0):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    result = {
        "prompt_tokens": read_field(usage, "prompt_tokens", 0),
        "completion_tokens": read_field(usage, "completion_tokens", 0),
        "total_tokens": read_field(usage, "total_tokens", 0),
    }
    # Cache-related fields
    prompt_details = read_field(usage, "prompt_tokens_details", None)
    if prompt_details:
        result["prompt_tokens_details"] = {
            "cached_tokens": read_field(prompt_details, "cached_tokens", 0) or 0,
            "cache_creation_input_tokens": (
                read_field(prompt_details, "cache_creation_input_tokens", 0)
                or read_field(prompt_details, "cache_write_tokens", 0)
                or 0
            ),
        }
    return result
