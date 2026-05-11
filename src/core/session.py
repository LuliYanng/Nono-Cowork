"""
Session manager — manages independent conversation contexts for multiple users.

All IM channels share the same SessionManager instance.

Sessions are persisted to disk as JSON files in data/sessions/.
On service restart, the most recent session for each user is automatically restored.

Supports multiple concurrent active sessions per user (desktop multi-session).
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
    """Manages independent sessions with disk persistence.

    Supports multiple concurrent active sessions per user.

    Internal storage:
      _sessions:  session_id → session dict  (multiple can be in memory)
      _active:    user_id → session_id       (which session is "active" per user)
      _locks:     session_id → Lock          (per-session concurrency control)
    """

    def __init__(self):
        self._sessions: dict[str, dict] = {}   # session_id → session data
        self._active: dict[str, str] = {}       # user_id → active session_id
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    # ── Internal helpers ──

    def _resolve_session(self, user_id: str | None = None,
                         session_id: str | None = None) -> dict | None:
        """Resolve a session by session_id or user_id's active session."""
        if session_id:
            return self._sessions.get(session_id)
        if user_id:
            active_sid = self._active.get(user_id)
            if active_sid:
                return self._sessions.get(active_sid)
        return None

    def _resolve_session_id(self, user_id: str | None = None,
                            session_id: str | None = None) -> str | None:
        """Resolve to a session_id."""
        if session_id:
            return session_id
        if user_id:
            return self._active.get(user_id)
        return None

    # ── Locks ──

    def get_lock(self, user_id: str = "", *, session_id: str | None = None) -> threading.Lock:
        """Get a per-session lock. Falls back to user's active session if no session_id."""
        with self._global_lock:
            sid = session_id or self._active.get(user_id)
            key = sid or f"_user:{user_id}"
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def get_session_lock(self, session_id: str) -> threading.Lock:
        """Get a per-session lock by session_id directly."""
        with self._global_lock:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]

    # ── Session access ──

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
            active_sid = self._active.get(user_id)
            if active_sid and active_sid in self._sessions:
                return self._sessions[active_sid]

            # Try to restore from disk
            restored = self._load_latest_session(user_id)
            if restored:
                if not restored.get("workspace_id"):
                    restored["workspace_id"] = _pick_backfill_workspace_id()
                sid = restored["session_id"]
                self._sessions[sid] = restored
                self._active[user_id] = sid
                logger.info("Restored session %s for user %s (workspace=%s)",
                            sid, user_id, restored.get("workspace_id"))
            else:
                new_session = self._create_new_session(user_id, workspace_id=workspace_id)
                sid = new_session["session_id"]
                self._sessions[sid] = new_session
                self._active[user_id] = sid

            return self._sessions[self._active[user_id]]

    def get_session(self, session_id: str) -> dict | None:
        """Get a session by its ID. Returns None if not in memory."""
        with self._global_lock:
            return self._sessions.get(session_id)

    def ensure_session_loaded(self, user_id: str, session_id: str) -> dict | None:
        """Ensure a session is loaded into memory. Load from disk if needed."""
        with self._global_lock:
            if session_id in self._sessions:
                return self._sessions[session_id]

        # Try loading from disk
        filepath = _session_path(session_id)
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("user_id") != user_id:
                return None
        except Exception:
            return None

        hydrated = self._hydrate_session(data)
        with self._global_lock:
            self._sessions[session_id] = hydrated
        return hydrated

    def touch_session(self, user_id: str, *, session_id: str | None = None):
        """Update last_active timestamp. Call when user actually sends a message."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
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

    # ── Session creation (multi-session) ──

    def create_session(self, user_id: str, workspace_id: str | None = None) -> dict:
        """Create a new session alongside existing ones.

        Unlike reset(), this does NOT archive/remove the old session.
        The new session becomes the active session for the user.
        Used by the desktop channel for multi-session support.
        """
        # Save the currently active session to disk (but keep in memory if running)
        active_sid = self._active.get(user_id)
        if active_sid and active_sid in self._sessions:
            self._save_session_by_id(active_sid)

        new_session = self._create_new_session(user_id, workspace_id=workspace_id)
        sid = new_session["session_id"]
        with self._global_lock:
            self._sessions[sid] = new_session
            self._active[user_id] = sid

        # Persist immediately (crash safety)
        self._save_session_by_id(sid)

        # Evict idle non-running sessions from memory to save RAM
        self._evict_idle_sessions(user_id, keep_sid=sid)

        return new_session

    def _evict_idle_sessions(self, user_id: str, keep_sid: str):
        """Remove non-running, non-active sessions from memory to save RAM."""
        with self._global_lock:
            to_evict = []
            for sid, session in self._sessions.items():
                if session.get("user_id") != user_id:
                    continue
                if sid == keep_sid:
                    continue
                # Don't evict sessions with a running agent (lock is held)
                lock = self._locks.get(sid)
                if lock and lock.locked():
                    continue
                to_evict.append(sid)
            for sid in to_evict:
                session = self._sessions.pop(sid)
                log_file = session.get("log_file")
                if log_file:
                    from logger import close_log_file
                    close_log_file(log_file)

    # ── Persistence ──

    def save_session(self, user_id: str, *, session_id: str | None = None):
        """Persist a session to disk."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if not session:
                return
            sid = session["session_id"]

        self._save_session_by_id(sid)

    def _save_session_by_id(self, session_id: str):
        """Internal: save a specific session to disk by session_id."""
        with self._global_lock:
            session = self._sessions.get(session_id)
            if not session:
                return

        try:
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            data = {
                "id": session["session_id"],
                "user_id": session.get("user_id", ""),
                "workspace_id": session.get("workspace_id"),
                "created_at": session["created_at"],
                "last_active": session["last_active"],
                "token_stats": dict(session["token_stats"]),
                "model_override": session.get("model_override"),
                "history": _serialize_history(session["history"]),
            }
            filepath = _session_path(session["session_id"])
            tmp_path = filepath + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, filepath)
            logger.debug("Saved session %s (%d messages)",
                         session["session_id"], len(session["history"]))
        except Exception as e:
            logger.error("Failed to save session: %s", e)

    def apply_cache_backfill(self, user_id: str, cached_tokens: int, cache_write_tokens: int,
                             *, session_id: str | None = None):
        """Apply delayed cache usage discovered after the main response completed."""
        if not cached_tokens and not cache_write_tokens:
            return

        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if not session:
                return
            stats = session.setdefault("token_stats", make_empty_token_stats())
            stats.setdefault("total_cached_tokens", 0)
            stats.setdefault("total_cache_write_tokens", 0)
            stats["total_cached_tokens"] += cached_tokens
            stats["total_cache_write_tokens"] += cache_write_tokens
            sid = session["session_id"]

        self._save_session_by_id(sid)

    def _load_latest_session(self, user_id: str) -> dict | None:
        """Find and load the most recent session file for a user."""
        if not os.path.isdir(SESSIONS_DIR):
            return None

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

    # ── Reset (IM channel compat) ──

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
            active_sid = self._active.get(user_id)
            if active_sid and active_sid in self._sessions:
                old_session = self._sessions.pop(active_sid)
                inherited_ws_id = old_session.get("workspace_id")
                stop_flag = old_session.get("stop_flag")
                if stop_flag:
                    stop_flag.set()
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
                # Temporarily put back to save
                self._sessions[active_sid] = old_session

        if active_sid and active_sid in self._sessions:
            self._save_session_by_id(active_sid)
            with self._global_lock:
                self._sessions.pop(active_sid, None)

        # Create a new empty session
        new_ws = workspace_id or inherited_ws_id
        new_session = self._create_new_session(user_id, workspace_id=new_ws)
        sid = new_session["session_id"]
        with self._global_lock:
            self._sessions[sid] = new_session
            self._active[user_id] = sid

        self._save_session_by_id(sid)

    # ── Stop control ──

    def request_stop(self, user_id: str, *, session_id: str | None = None) -> bool:
        """Signal the running agent to stop. Returns True if a session exists."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if session:
                session["stop_flag"].set()
                return True
            return False

    def is_stopped(self, user_id: str, *, session_id: str | None = None) -> bool:
        """Check if stop has been requested for this session."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if session:
                return session["stop_flag"].is_set()
            return False

    def clear_stop(self, user_id: str, *, session_id: str | None = None):
        """Clear the stop flag (called at the start of each agent run)."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if session:
                session["stop_flag"].clear()
                session["subagent_stop_flag"].clear()

    def request_subagent_stop(self, user_id: str, *, session_id: str | None = None) -> bool:
        """Signal only the running subagent to stop. Main agent continues."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if session:
                session["subagent_stop_flag"].set()
                return True
            return False

    def is_subagent_stopped(self, user_id: str, *, session_id: str | None = None) -> bool:
        """Check if subagent stop has been requested."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if session:
                return session["subagent_stop_flag"].is_set()
            return False

    def clear_subagent_stop(self, user_id: str, *, session_id: str | None = None):
        """Clear the subagent stop flag (called after delegate returns)."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if session:
                session["subagent_stop_flag"].clear()

    # ── Model override ──

    def set_model(self, user_id: str, model: str | None, *, session_id: str | None = None):
        """Set or clear a per-session model override."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if session:
                session["model_override"] = model

    def get_model(self, user_id: str, *, session_id: str | None = None) -> str | None:
        """Get the per-session model override (None = use default)."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if session:
                return session.get("model_override")
            return None

    # ── Status ──

    def get_status(self, user_id: str, *, session_id: str | None = None) -> dict | None:
        """Get session status info for /status command."""
        with self._global_lock:
            session = self._resolve_session(user_id=user_id, session_id=session_id)
            if not session:
                return None
            sid = session["session_id"]
            token_stats = {**make_empty_token_stats(), **(session.get("token_stats") or {})}
            lock = self._locks.get(sid)
            return {
                "session_id": sid,
                "workspace_id": session.get("workspace_id"),
                "token_stats": token_stats,
                "history_len": len(session["history"]),
                "created_at": session["created_at"],
                "last_active": session["last_active"],
                "model_override": session.get("model_override"),
                "is_running": lock.locked() if lock else False,
            }

    def is_session_running(self, session_id: str) -> bool:
        """Check if a specific session has an agent running."""
        with self._global_lock:
            lock = self._locks.get(session_id)
            return lock.locked() if lock else False

    # ── Listing ──

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

                    if not has_user_msg:
                        continue

                    sid = data["id"]
                    results.append({
                        "id": sid,
                        "workspace_id": data.get("workspace_id"),
                        "created_at": data.get("created_at", 0),
                        "last_active": data.get("last_active", 0),
                        "message_count": len(data.get("history", [])),
                        "preview": preview,
                        "is_running": self.is_session_running(sid),
                    })
            except Exception:
                continue

        results.sort(key=lambda x: x["last_active"], reverse=True)
        return results

    # ── Switch (changes active pointer, loads into memory) ──

    def switch_session(self, user_id: str, session_id: str) -> bool:
        """Switch to a different session. Returns True on success.

        In multi-session mode, this just changes the active pointer and
        ensures the session is loaded into memory. It does NOT archive
        or unload the previous session (it may still be running).
        """
        filepath = _session_path(session_id)
        if not os.path.exists(filepath):
            # Maybe it's a brand-new in-memory session (not yet saved)
            with self._global_lock:
                if session_id in self._sessions:
                    self._active[user_id] = session_id
                    return True
            return False

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("user_id") != user_id:
                return False
        except Exception:
            return False

        # Save current active session to disk
        old_sid = self._active.get(user_id)
        if old_sid and old_sid in self._sessions:
            self._save_session_by_id(old_sid)

        with self._global_lock:
            # Load target session into memory if not already there
            if session_id not in self._sessions:
                self._sessions[session_id] = self._hydrate_session(data)
            self._active[user_id] = session_id

        # Evict idle sessions to save memory
        self._evict_idle_sessions(user_id, keep_sid=session_id)

        logger.info("Switched user %s to session %s", user_id, session_id)
        return True

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """Delete a saved session. Returns True on success.

        Cannot delete a session that is currently running.
        """
        # Refuse to delete a running session
        if self.is_session_running(session_id):
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

        # Remove from memory if loaded
        with self._global_lock:
            session = self._sessions.pop(session_id, None)
            if session:
                log_file = session.get("log_file")
                if log_file:
                    from logger import close_log_file
                    close_log_file(log_file)
            # If this was the active session, clear the pointer
            if self._active.get(user_id) == session_id:
                self._active.pop(user_id, None)

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
            for sid in list(self._sessions.keys()):
                session = self._sessions[sid]
                try:
                    os.makedirs(SESSIONS_DIR, exist_ok=True)
                    data = {
                        "id": session["session_id"],
                        "user_id": session.get("user_id", ""),
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

                log_file = session.get("log_file")
                if log_file:
                    from logger import log_event, close_log_file
                    log_event(log_file, {
                        "type": "session_end",
                        "user_id": session.get("user_id", ""),
                        "reason": "shutdown",
                        "session_token_stats": session.get("token_stats"),
                    })
                    close_log_file(log_file)

            self._sessions.clear()
            self._active.clear()

    def list_active_sessions(self) -> dict[str, float]:
        """List all in-memory active sessions and their last active times."""
        with self._global_lock:
            return {sid: s["last_active"] for sid, s in self._sessions.items()}


# Global singleton
sessions = SessionManager()
