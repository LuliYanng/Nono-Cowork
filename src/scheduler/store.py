"""
Task store — JSON-file-based persistence for scheduled tasks.

Tasks are stored in `data/scheduled_tasks.json` (relative to project root).
Each task is a dict with:
  - id: str (UUID)
  - task_name: str (human-readable name)
  - cron: str (cron expression, e.g. "0 9 * * *")
  - task_prompt: str (natural language instruction for the Agent)
  - channel_user_id: str (IM-specific delivery target, e.g., feishu ou_xxx)
  - channel_name: str (which IM channel to push results to)
  - enabled: bool
  - created_at: str (ISO timestamp)
  - last_run_at: str | None (ISO timestamp of last execution)
  - last_result: str | None (last execution result summary)
"""

import os
import json
import uuid
import logging
from datetime import datetime, timezone
from threading import Lock

logger = logging.getLogger("scheduler.store")

# Persistent storage path
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data")
_STORE_PATH = os.path.join(_DATA_DIR, "scheduled_tasks.json")

_lock = Lock()


def _ensure_dir():
    os.makedirs(_DATA_DIR, exist_ok=True)


def _load_all() -> list[dict]:
    """Load all tasks from disk."""
    if not os.path.exists(_STORE_PATH):
        return []
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load task store: {e}")
        return []


def _save_all(tasks: list[dict]):
    """Save all tasks to disk."""
    _ensure_dir()
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


# ── Public API ──

def create_task(task_name: str, cron: str, task_prompt: str,
                channel_user_id: str, channel_name: str,
                tool_access: str = "full", model: str = "") -> dict:
    """Create and persist a new scheduled task. Returns the task dict."""
    task = {
        "id": uuid.uuid4().hex[:12],
        "task_name": task_name,
        "cron": cron,
        "task_prompt": task_prompt,
        "channel_user_id": channel_user_id,
        "channel_name": channel_name,
        "tool_access": tool_access,
        "model": model,
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_run_at": None,
        "last_result": None,
    }
    with _lock:
        tasks = _load_all()
        tasks.append(task)
        _save_all(tasks)
    logger.info(f"Task created: {task['id']} — {task_name}")
    return task


def get_task(task_id: str) -> dict | None:
    """Get a task by ID."""
    with _lock:
        for t in _load_all():
            if t["id"] == task_id:
                return t
    return None


def list_tasks(channel_user_id: str = None) -> list[dict]:
    """List tasks, optionally filtered by channel_user_id."""
    with _lock:
        tasks = _load_all()
    if channel_user_id:
        tasks = [t for t in tasks if t.get("channel_user_id") == channel_user_id]
    return tasks


def update_task(task_id: str, **updates) -> dict | None:
    """Update a task's fields. Returns updated task or None if not found."""
    allowed_fields = {"task_name", "cron", "task_prompt", "enabled",
                      "last_run_at", "last_result", "tool_access", "model"}
    with _lock:
        tasks = _load_all()
        for t in tasks:
            if t["id"] == task_id:
                for k, v in updates.items():
                    if k in allowed_fields:
                        t[k] = v
                _save_all(tasks)
                logger.info(f"Task updated: {task_id}")
                return t
    return None


def delete_task(task_id: str) -> bool:
    """Delete a task by ID. Returns True if found and deleted."""
    with _lock:
        tasks = _load_all()
        original_len = len(tasks)
        tasks = [t for t in tasks if t["id"] != task_id]
        if len(tasks) < original_len:
            _save_all(tasks)
            logger.info(f"Task deleted: {task_id}")
            return True
    return False
