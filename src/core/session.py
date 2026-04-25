"""
Session manager — manages independent conversation contexts for multiple users.

All IM channels share the same SessionManager instance.

Sessions are persisted to disk as JSON files in data/sessions/.
On service restart, the most recent session for each user is automatically restored.
"""
import json
import os
import threading
import time
import logging

from config import SESSIONS_DIR
from core.llm import make_empty_token_stats

logger = logging.getLogger("session")


def _generate_session_id() -> str:
    """Generate a timestamp-based session ID with random suffix."""
    import secrets
    return time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(2)


def _session_path(session_id: str) -> str:
    """Get the file path for a session."""
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def _pick_backfill_workspace_id() -> str | None:
    """Pick a workspace_id for a session that lacks one.

    Uses the strict default workspace if one is set, otherwise falls
    back to the most-recently-active workspace so the session still has
    a concrete home. Returns None only when zero workspaces exist
    (fresh install — the user will be routed through onboarding).

    Note: this is *data-layer* fallback. The UI-level notion of
    "default" (delete button hidden, default badge shown) remains
    strict — see WorkspaceManager.get_default().
    """
    try:
        from core.workspace import workspaces
        ws = workspaces.get_any_fallback()
        return ws["id"] if ws else None
    except Exception as e:
        logger.debug("Workspace lookup failed: %s", e)
        return None


# ─── Serialization helpers ──────────────────────────────────────

def _serialize_message(msg) -> dict:
    """Convert a history message (dict or OpenAI object) to a plain dict.

    Handles both plain dicts (user/system/tool messages) and
    LiteLLM Message objects (assistant messages with tool_calls).

    Multimodal content (lists with image_url parts) is sanitized:
    inline base64 data is stripped to keep session files small.
    """
    if isinstance(msg, dict):
        result = dict(msg)
        # Sanitize multimodal content arrays (strip base64 image data)
        content = result.get("content")
        if isinstance(content, list):
            result["content"] = _sanitize_multimodal_content(content)
        return result

    # OpenAI/LiteLLM message object
    d = {"role": msg.role, "content": msg.content}

    # Sanitize multimodal content
    if isinstance(d["content"], list):
        d["content"] = _sanitize_multimodal_content(d["content"])

    # Preserve reasoning_content if present (e.g. DeepSeek)
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning:
        d["reasoning_content"] = reasoning

    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        serialized_tcs = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                # Already a dict — keep as-is but ensure structure
                serialized_tcs.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": tc.get("function", {}),
                })
            else:
                # LiteLLM object
                serialized_tcs.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })
        d["tool_calls"] = serialized_tcs

    return d


def _sanitize_multimodal_content(content: list) -> list:
    """Strip inline base64 data from multimodal content arrays.

    Replaces image_url data URLs with a placeholder so session JSON
    files stay small.  The original images are ephemeral — they were
    sent to the LLM but don't need to be persisted for session replay.
    """
    sanitized = []
    for part in content:
        if not isinstance(part, dict):
            sanitized.append(part)
            continue
        if part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if url.startswith("data:"):
                # Extract mime type for display, strip data
                mime = url.split(";")[0].replace("data:", "") if ";" in url else "image/unknown"
                sanitized.append({
                    "type": "image_url",
                    "image_url": {"url": f"[image:{mime}]"},
                })
            else:
                sanitized.append(part)
        else:
            sanitized.append(part)
    return sanitized


def _serialize_history(history: list) -> list[dict]:
    """Serialize the full history to JSON-safe dicts."""
    return [_serialize_message(msg) for msg in history]


# ─── Session Manager ────────────────────────────────────────────

class SessionManager:
    """Manages independent sessions for multiple users with disk persistence."""

    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def get_lock(self, user_id: str) -> threading.Lock:
        """Get a user-level lock (prevents concurrent Agent execution for the same user)."""
        with self._global_lock:
            if user_id not in self._locks:
                self._locks[user_id] = threading.Lock()
            return self._locks[user_id]

    def get_or_create(self, user_id: str, workspace_id: str | None = None) -> dict:
        """Get the active session for a user, or create a new one.

        On first call after startup, tries to restore the most recent
        session from disk. If none found, creates a fresh session.

        If ``workspace_id`` is given, a newly-created session will be
        bound to that workspace. A restored session keeps its own
        recorded workspace_id (not overridden).

        NOTE: This does NOT update last_active.  last_active is only
        updated explicitly when a user sends a message (touch_session).
        """
        with self._global_lock:
            if user_id not in self._sessions:
                # Try to restore from disk
                restored = self._load_latest_session(user_id)
                if restored:
                    # Backfill missing workspace_id on legacy sessions
                    if not restored.get("workspace_id"):
                        restored["workspace_id"] = _pick_backfill_workspace_id()
                    self._sessions[user_id] = restored
                    logger.info("Restored session %s for user %s (workspace=%s)",
                                restored["session_id"], user_id,
                                restored.get("workspace_id"))
                else:
                    self._sessions[user_id] = self._create_new_session(
                        user_id, workspace_id=workspace_id,
                    )

            return self._sessions[user_id]

    def touch_session(self, user_id: str):
        """Update last_active timestamp. Call when user actually sends a message."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if session:
                session["last_active"] = time.time()

    def _create_new_session(
        self, user_id: str, workspace_id: str | None = None,
    ) -> dict:
        """Create a brand new session bound to a workspace.

        If no workspace_id is provided, falls back to the default
        workspace (if any). On a fresh install with zero workspaces,
        workspace_id will be None — the frontend should route the user
        through onboarding to create one before sending messages.
        """
        from core.prompt import make_system_prompt
        from logger import create_log_file, log_event

        session_id = _generate_session_id()
        resolved_ws_id = workspace_id or _pick_backfill_workspace_id()
        log_file = create_log_file()
        log_event(log_file, {
            "type": "session_start",
            "user_id": user_id,
            "session_id": session_id,
            "workspace_id": resolved_ws_id,
        })

        session = {
            "session_id": session_id,
            "workspace_id": resolved_ws_id,
            "history": [
                {"role": "system",
                 "content": make_system_prompt(workspace_id=resolved_ws_id)}
            ],
            "token_stats": make_empty_token_stats(),
            "log_file": log_file,
            "user_id": user_id,
            "created_at": time.time(),
            "last_active": time.time(),
            "stop_flag": threading.Event(),
            "subagent_stop_flag": threading.Event(),
            "model_override": None,
        }
        logger.info("Created new session %s for user %s", session_id, user_id)
        return session

    def save_session(self, user_id: str):
        """Persist the current session to disk."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if not session:
                return

        # Serialize outside the lock to avoid blocking other users
        try:
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            data = {
                "id": session["session_id"],
                "user_id": user_id,
                "workspace_id": session.get("workspace_id"),
                "created_at": session["created_at"],
                "last_active": session["last_active"],
                "token_stats": dict(session["token_stats"]),
                "model_override": session.get("model_override"),
                "history": _serialize_history(session["history"]),
            }
            filepath = _session_path(session["session_id"])
            # Write to temp file first, then rename (atomic on most filesystems)
            tmp_path = filepath + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, filepath)
            logger.debug("Saved session %s (%d messages)",
                         session["session_id"], len(session["history"]))
        except Exception as e:
            logger.error("Failed to save session: %s", e)

    def apply_cache_backfill(self, user_id: str, cached_tokens: int, cache_write_tokens: int):
        """Apply delayed cache usage discovered after the main response completed."""
        if not cached_tokens and not cache_write_tokens:
            return

        with self._global_lock:
            session = self._sessions.get(user_id)
            if not session:
                return
            stats = session.setdefault("token_stats", make_empty_token_stats())
            stats.setdefault("total_cached_tokens", 0)
            stats.setdefault("total_cache_write_tokens", 0)
            stats["total_cached_tokens"] += cached_tokens
            stats["total_cache_write_tokens"] += cache_write_tokens

        self.save_session(user_id)

    def _load_latest_session(self, user_id: str) -> dict | None:
        """Find and load the most recent session file for a user."""
        if not os.path.isdir(SESSIONS_DIR):
            return None

        # Scan all session files, find the latest one for this user_id
        latest_file = None
        latest_time = 0

        for fname in os.listdir(SESSIONS_DIR):
            if not fname.endswith(".json"):
                continue
            filepath = os.path.join(SESSIONS_DIR, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("user_id") == user_id:
                    t = data.get("last_active", 0)
                    if t > latest_time:
                        latest_time = t
                        latest_file = (filepath, data)
            except Exception:
                continue

        if not latest_file:
            return None

        filepath, data = latest_file
        return self._hydrate_session(data)

    def _hydrate_session(self, data: dict) -> dict:
        """Convert loaded JSON data back into a live session dict."""
        from logger import create_log_file, log_event

        log_file = create_log_file()
        log_event(log_file, {
            "type": "session_restored",
            "session_id": data["id"],
            "user_id": data["user_id"],
            "history_len": len(data.get("history", [])),
        })

        # Regenerate system prompt (picks up fresh time, memory, service status)
        from core.prompt import make_system_prompt
        history = data.get("history", [])
        ws_id = data.get("workspace_id")
        if history and history[0].get("role") == "system":
            history[0] = {
                "role": "system",
                "content": make_system_prompt(workspace_id=ws_id),
            }

        return {
            "session_id": data["id"],
            "workspace_id": data.get("workspace_id"),
            "history": history,
            "token_stats": {**make_empty_token_stats(), **(data.get("token_stats") or {})},
            "log_file": log_file,
            "user_id": data["user_id"],
            "created_at": data.get("created_at", time.time()),
            "last_active": data.get("last_active", time.time()),
            "stop_flag": threading.Event(),
            "subagent_stop_flag": threading.Event(),
            "model_override": data.get("model_override"),
        }

    def reset(self, user_id: str, workspace_id: str | None = None):
        """Archive current session and start a new one.

        The old session file stays on disk for history.
        A new empty session is created immediately so that
        get_or_create doesn't reload the archived session.

        If ``workspace_id`` is provided, the new session is bound to
        that workspace. Otherwise it inherits from the archived session
        (or falls back to the default workspace).
        """
        inherited_ws_id: str | None = None
        with self._global_lock:
            if user_id in self._sessions:
                old_session = self._sessions.pop(user_id)
                inherited_ws_id = old_session.get("workspace_id")
                # Signal stop to any running agent
                stop_flag = old_session.get("stop_flag")
                if stop_flag:
                    stop_flag.set()
                # Close the session's log file
                log_file = old_session.get("log_file")
                if log_file:
                    from logger import log_event, close_log_file
                    log_event(log_file, {
                        "type": "session_end",
                        "user_id": user_id,
                        "session_id": old_session.get("session_id"),
                        "session_token_stats": old_session.get("token_stats"),
                    })
                    close_log_file(log_file)
                # Save old session to disk before archiving
                self._sessions[user_id] = old_session
        self.save_session(user_id)
        # Create a new empty session so the next get_or_create
        # doesn't reload the just-archived one from disk
        new_ws = workspace_id or inherited_ws_id
        with self._global_lock:
            self._sessions[user_id] = self._create_new_session(
                user_id, workspace_id=new_ws,
            )
        # Persist the new session immediately (crash safety)
        self.save_session(user_id)

    def request_stop(self, user_id: str) -> bool:
        """Signal the running agent to stop. Returns True if a session exists."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if session:
                session["stop_flag"].set()
                return True
            return False

    def is_stopped(self, user_id: str) -> bool:
        """Check if stop has been requested for this user."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if session:
                return session["stop_flag"].is_set()
            return False

    def clear_stop(self, user_id: str):
        """Clear the stop flag (called at the start of each agent run)."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if session:
                session["stop_flag"].clear()
                session["subagent_stop_flag"].clear()

    def request_subagent_stop(self, user_id: str) -> bool:
        """Signal only the running subagent to stop. Main agent continues."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if session:
                session["subagent_stop_flag"].set()
                return True
            return False

    def is_subagent_stopped(self, user_id: str) -> bool:
        """Check if subagent stop has been requested."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if session:
                return session["subagent_stop_flag"].is_set()
            return False

    def clear_subagent_stop(self, user_id: str):
        """Clear the subagent stop flag (called after delegate returns)."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if session:
                session["subagent_stop_flag"].clear()

    def set_model(self, user_id: str, model: str | None):
        """Set or clear a per-session model override."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if session:
                session["model_override"] = model

    def get_model(self, user_id: str) -> str | None:
        """Get the per-session model override (None = use default)."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if session:
                return session.get("model_override")
            return None

    def get_status(self, user_id: str) -> dict | None:
        """Get session status info for /status command."""
        with self._global_lock:
            session = self._sessions.get(user_id)
            if not session:
                return None
            token_stats = {**make_empty_token_stats(), **(session.get("token_stats") or {})}
            return {
                "session_id": session["session_id"],
                "workspace_id": session.get("workspace_id"),
                "token_stats": token_stats,
                "history_len": len(session["history"]),
                "created_at": session["created_at"],
                "last_active": session["last_active"],
                "model_override": session.get("model_override"),
                "is_running": self._locks.get(user_id, threading.Lock()).locked(),
            }

    def list_sessions(self, user_id: str) -> list[dict]:
        """List all saved sessions for a user, sorted by last_active (newest first).

        Empty sessions (only a system prompt, no user messages) are excluded
        from the listing to keep the history clean.
        """
        results = []
        if not os.path.isdir(SESSIONS_DIR):
            return results

        for fname in os.listdir(SESSIONS_DIR):
            if not fname.endswith(".json"):
                continue
            filepath = os.path.join(SESSIONS_DIR, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("user_id") == user_id:
                    # Get first user message as preview
                    preview = ""
                    has_user_msg = False
                    for msg in data.get("history", []):
                        if msg.get("role") == "user":
                            if not preview:
                                content = msg.get("content", "")
                                if isinstance(content, list):
                                    # Multimodal: extract text part
                                    text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                                    has_images = any(p.get("type") == "image_url" for p in content)
                                    preview_text = " ".join(text_parts).strip()
                                    if has_images and not preview_text:
                                        preview_text = "📷 (image)"
                                    elif has_images:
                                        preview_text = f"📷 {preview_text}"
                                    preview = preview_text[:60]
                                else:
                                    preview = str(content)[:60]
                            has_user_msg = True
                            break

                    # Skip empty sessions (no user messages)
                    if not has_user_msg:
                        continue

                    results.append({
                        "id": data["id"],
                        "workspace_id": data.get("workspace_id"),
                        "created_at": data.get("created_at", 0),
                        "last_active": data.get("last_active", 0),
                        "message_count": len(data.get("history", [])),
                        "preview": preview,
                    })
            except Exception:
                continue

        results.sort(key=lambda x: x["last_active"], reverse=True)
        return results

    def switch_session(self, user_id: str, session_id: str) -> bool:
        """Switch to a different session. Returns True on success."""
        filepath = _session_path(session_id)
        if not os.path.exists(filepath):
            return False

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("user_id") != user_id:
                return False
        except Exception:
            return False

        # Save current session first
        self.save_session(user_id)

        # Close current session's log file
        with self._global_lock:
            old_session = self._sessions.get(user_id)
            if old_session:
                log_file = old_session.get("log_file")
                if log_file:
                    from logger import close_log_file
                    close_log_file(log_file)

            # Load the target session
            self._sessions[user_id] = self._hydrate_session(data)

        logger.info("Switched user %s to session %s", user_id, session_id)
        return True

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """Delete a saved session. Returns True on success.

        Cannot delete the currently active session — the caller should
        switch away first or start a new session if needed.
        """
        # Refuse to delete the active session
        with self._global_lock:
            active = self._sessions.get(user_id)
            if active and active["session_id"] == session_id:
                return False

        filepath = _session_path(session_id)
        if not os.path.exists(filepath):
            return False

        # Verify ownership
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("user_id") != user_id:
                return False
        except Exception:
            return False

        try:
            os.remove(filepath)
            logger.info("Deleted session %s for user %s", session_id, user_id)
            return True
        except Exception as e:
            logger.error("Failed to delete session %s: %s", session_id, e)
            return False

    def close_all(self):
        """Save and close all sessions (for shutdown)."""
        with self._global_lock:
            for user_id in list(self._sessions.keys()):
                session = self._sessions[user_id]
                # Save to disk
                try:
                    os.makedirs(SESSIONS_DIR, exist_ok=True)
                    data = {
                        "id": session["session_id"],
                        "user_id": user_id,
                        "workspace_id": session.get("workspace_id"),
                        "created_at": session["created_at"],
                        "last_active": session["last_active"],
                        "token_stats": dict(session["token_stats"]),
                        "model_override": session.get("model_override"),
                        "history": _serialize_history(session["history"]),
                    }
                    filepath = _session_path(session["session_id"])
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.error("Failed to save session on shutdown: %s", e)

                # Close log file
                log_file = session.get("log_file")
                if log_file:
                    from logger import log_event, close_log_file
                    log_event(log_file, {
                        "type": "session_end",
                        "user_id": user_id,
                        "reason": "shutdown",
                        "session_token_stats": session.get("token_stats"),
                    })
                    close_log_file(log_file)

            self._sessions.clear()

    def list_active_sessions(self) -> dict[str, float]:
        """List all in-memory active sessions and their last active times."""
        with self._global_lock:
            return {uid: s["last_active"] for uid, s in self._sessions.items()}


# Global singleton
sessions = SessionManager()
