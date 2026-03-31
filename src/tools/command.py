"""
Command execution tools — run shell commands and check background processes.

Output trimming is NOT handled here. The unified trimmer in context/trimmer.py
handles all tool output sizing for the context window.
"""

import os
import subprocess
import threading
import time
from tools.registry import tool


# ————— Background task management —————
_bg_processes: dict[int, dict] = {}   # PID → {"proc": Popen, "output": list[str]}


@tool(
    name="run_command",
    tags=["execute"],
    description="Execute a bash command on the Linux server. Can be used for: git clone, installing dependencies (pip install), running Python scripts, viewing file contents (cat/ls/find), creating directories, downloading files from URLs (curl -o /path/file 'url'), and any other terminal operations. Short-running commands return output directly. Long-running commands automatically return a PID; use check_command_status to view the result later.\n\nNote: If the output is very large, it will be automatically saved to a temporary file. A preview and file path will be returned; use read_file with line ranges to view specific sections.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute. Supports pipes, redirects, etc. Examples: 'ls -la', 'git clone ...', 'pip install -r requirements.txt'.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the command. Defaults to the user's home directory.",
                "default": "~",
            },
        },
        "required": ["command"],
    },
)
def run_command(command: str, cwd: str = "~") -> str:
    """Execute a bash command on the server and return its output."""
    cwd = os.path.expanduser(cwd)
    WAIT_SECONDS = 120

    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception as e:
        return f"❌ Execution failed: {str(e)}"

    output_lines: list[str] = []

    # Background thread to continuously read stdout (prevents pipe buffer deadlock)
    def _reader():
        for line in proc.stdout:
            output_lines.append(line)
    threading.Thread(target=_reader, daemon=True).start()

    _bg_processes[proc.pid] = {"proc": proc, "output": output_lines}

    # Poll until done
    start = time.time()
    while time.time() - start < WAIT_SECONDS:
        if proc.poll() is not None:
            break
        time.sleep(0.5)

    # Finished within 120s → return raw result (trimmer handles sizing)
    if proc.poll() is not None:
        output = "".join(output_lines)
        if not output.strip():
            output = "(Command executed, no output)"

        if proc.returncode != 0:
            output += f"\n(exit code: {proc.returncode})"

        return output

    # Not finished within 120s → return PID
    return (
        f"⏳ Command still running (PID: {proc.pid})\n"
        f"Use check_command_status({proc.pid}) to check progress, "
        f"or run_command(\"kill {proc.pid}\") to terminate."
    )


@tool(
    name="check_command_status",
    description="Check the status and output of a background command. Use this when run_command returns a PID to monitor progress. The output may be automatically saved to a file if it's large; use read_file with line ranges to view specific sections.",
    parameters={
        "type": "object",
        "properties": {
            "pid": {
                "type": "integer",
                "description": "The process PID, returned by run_command when the command did not finish within the timeout.",
            },
        },
        "required": ["pid"],
    },
)
def check_command_status(pid: int) -> str:
    """Check the status and output of a background command."""
    info = _bg_processes.get(pid)
    if not info:
        available = ", ".join(str(p) for p in _bg_processes.keys()) or "none"
        return f"❌ No command found with PID {pid}. Available: {available}"

    proc = info["proc"]
    output_lines = info["output"]
    output = "".join(output_lines)
    total_lines = len(output_lines)

    if not output.strip():
        output_display = "(no output yet)"
    else:
        output_display = output

    if proc.poll() is None:
        return (
            f"⏳ PID {pid} still running ({total_lines} lines so far)\n"
            f"Tip: For long time tasks, you can use run_command(\"sleep N\") to wait before checking again.\n\n"
            f"{output_display}"
        )
    else:
        return (
            f"✅ PID {pid} completed (exit code: {proc.returncode}, {total_lines} lines)\n\n"
            f"{output_display}"
        )
