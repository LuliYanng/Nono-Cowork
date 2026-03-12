"""
Session manager — manages independent conversation contexts for multiple users

All IM channels share the same SessionManager instance.

Each session owns a log_file that lives for the entire session lifecycle,
ensuring all messages in a conversation are recorded in a single log file.
"""
import threading
import time


class SessionManager:
    """Manages independent sessions for multiple users."""

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

    def get_or_create(self, user_id: str) -> dict:
        """Get or create a user session.

        On first creation, also creates a session-level log file.
        """
        with self._global_lock:
            if user_id not in self._sessions:
                from prompt import make_system_prompt
                from logger import create_log_file, log_event

                log_file = create_log_file()
                log_event(log_file, {
                    "type": "session_start",
                    "user_id": user_id,
                })

                self._sessions[user_id] = {
                    "history": [
                        {"role": "system", "content": make_system_prompt()}
                    ],
                    "token_stats": {
                        "total_prompt_tokens": 0,
                        "total_completion_tokens": 0,
                        "total_tokens": 0,
                        "total_cached_tokens": 0,
                        "total_api_calls": 0,
                    },
                    "log_file": log_file,
                    "created_at": time.time(),
                    "last_active": time.time(),
                }
            session = self._sessions[user_id]
            session["last_active"] = time.time()
            return session

    def reset(self, user_id: str):
        """Reset a user session, closing its log file."""
        with self._global_lock:
            if user_id in self._sessions:
                session = self._sessions.pop(user_id)
                # Close the session's log file
                log_file = session.get("log_file")
                if log_file:
                    from logger import log_event, close_log_file
                    log_event(log_file, {
                        "type": "session_end",
                        "user_id": user_id,
                        "session_token_stats": session.get("token_stats"),
                    })
                    close_log_file(log_file)

    def close_all(self):
        """Close all sessions and their log files (for shutdown)."""
        with self._global_lock:
            for user_id in list(self._sessions.keys()):
                session = self._sessions.pop(user_id)
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

    def list_sessions(self) -> dict[str, float]:
        """List all active sessions and their last active times."""
        with self._global_lock:
            return {uid: s["last_active"] for uid, s in self._sessions.items()}


# Global singleton
sessions = SessionManager()
