"""
Notification Store — central hub for autonomous agent work results.

Architecture:
  Event Source (trigger/schedule/syncthing)
    → Subagent processes event autonomously
    → NotificationStore.create()
      → Saves autonomous session (data/autonomous_sessions/)
      → Saves notification index (data/notifications.json)
      → Distributes to channels (Desktop SSE + optional IM)

Notifications are lightweight index entries that point to full
autonomous sessions. This keeps the notification list fast to load,
while the full session (with complete agent history) is loaded
on-demand when the user clicks to review or continue chatting.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import threading
import time
from datetime import datetime, timezone

from config import AUTO_SESSIONS_DIR, NOTIFICATIONS_FILE

logger = logging.getLogger("notifications")


# ═══════════════════════════════════════════
#  Title generation — auto-extract from event metadata
# ═══════════════════════════════════════════

def generate_title(source_type: str, source_name: str, event_data: dict = None) -> str:
    """Auto-generate a human-readable notification title from event metadata.

    Does NOT depend on agent output — uses raw event data for reliability.
    """
    event_data = event_data or {}

    if source_type == "trigger":
        return _title_from_trigger(source_name, event_data)
    elif source_type == "schedule":
        return f"⏰ {source_name}"
    elif source_type == "syncthing":
        action = event_data.get("action", "changed")
        path = event_data.get("path", "file")
        filename = os.path.basename(path) if path else "file"
        return f"📁 {action}: {filename}"

    return f"🔔 {source_name}"


def _title_from_trigger(slug: str, event_data: dict) -> str:
    """Generate title from a Composio trigger event payload."""
    slug_upper = slug.upper()

    if "GMAIL" in slug_upper:
        # Gmail payloads typically have sender/from and subject
        sender = (
            event_data.get("sender")
            or event_data.get("from")
            or event_data.get("messageFrom", "")
        )
        subject = event_data.get("subject", event_data.get("messageSubject", ""))

        # Strip email address from sender: "John Doe <john@example.com>" → "John Doe"
        if sender and "<" in sender:
            sender = sender.split("<")[0].strip().strip('"').strip("'")
        # If sender is just an email, keep it but shorter
        if sender and "@" in sender:
            sender = sender.split("@")[0]

        if sender and subject:
            # Truncate long subjects
            if len(subject) > 60:
                subject = subject[:57] + "..."
            return f"📧 {sender}: {subject}"
        elif sender:
            return f"📧 新邮件 from {sender}"
        return "📧 新邮件"

    elif "GITHUB" in slug_upper:
        repo = event_data.get("repository", {}).get("full_name", "")
        if "COMMIT" in slug_upper or "PUSH" in slug_upper:
            return f"🔨 {repo}: 新提交" if repo else "🔨 GitHub: 新提交"
        elif "ISSUE" in slug_upper:
            title = event_data.get("issue", {}).get("title", "")
            return f"🐛 {repo}: {title}" if title else f"🐛 {repo}: 新 Issue"
        elif "PULL_REQUEST" in slug_upper:
            title = event_data.get("pull_request", {}).get("title", "")
            return f"🔀 {repo}: {title}" if title else f"🔀 {repo}: 新 PR"
        return f"🔨 GitHub: {slug}"

    elif "SLACK" in slug_upper:
        channel = event_data.get("channel", "")
        return f"💬 Slack: #{channel}" if channel else "💬 Slack 消息"

    # Generic fallback: clean up the slug
    clean_name = slug.replace("_", " ").title()
    return f"🔔 {clean_name}"


def infer_category(source_type: str, source_name: str) -> str:
    """Infer notification category from source metadata."""
    if source_type == "schedule":
        return "report"
    if source_type == "syncthing":
        return "file"

    slug_upper = (source_name or "").upper()
    if "GMAIL" in slug_upper or "EMAIL" in slug_upper or "OUTLOOK" in slug_upper:
        return "email"
    if "GITHUB" in slug_upper or "GITLAB" in slug_upper:
        return "code"
    if "SLACK" in slug_upper or "DISCORD" in slug_upper or "TELEGRAM" in slug_upper:
        return "chat"
    return "general"


# ═══════════════════════════════════════════
#  SSE Pub/Sub — for real-time push to Desktop
# ═══════════════════════════════════════════

class _NotificationPubSub:
    """Thread-safe pub/sub for notification events.

    Desktop SSE endpoint subscribes by creating a queue.
    NotificationStore publishes to all subscriber queues.
    """

    def __init__(self):
        self._subscribers: dict[str, list[queue.Queue]] = {}  # user_id -> [queues]
        self._lock = threading.Lock()

    def subscribe(self, user_id: str) -> queue.Queue:
        """Create a new subscription queue for a user."""
        q = queue.Queue()
        with self._lock:
            if user_id not in self._subscribers:
                self._subscribers[user_id] = []
            self._subscribers[user_id].append(q)
        return q

    def unsubscribe(self, user_id: str, q: queue.Queue):
        """Remove a subscription queue."""
        with self._lock:
            subs = self._subscribers.get(user_id, [])
            if q in subs:
                subs.remove(q)

    def publish(self, user_id: str, event: dict):
        """Push an event to all subscribers for a user."""
        with self._lock:
            subs = list(self._subscribers.get(user_id, []))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # Drop if queue is full (subscriber too slow)


_pubsub = _NotificationPubSub()


# ═══════════════════════════════════════════
#  NotificationStore
# ═══════════════════════════════════════════

class NotificationStore:
    """Agent autonomous work results — storage and distribution center.

    Two-layer storage:
      1. Notification index (notifications.json) — lightweight, fast to query
      2. Autonomous sessions (autonomous_sessions/*.json) — full history, session-compatible
    """

    def __init__(self):
        self._lock = threading.Lock()
        os.makedirs(AUTO_SESSIONS_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(NOTIFICATIONS_FILE), exist_ok=True)

    # ── Core: Create ──

    def create(
        self,
        source_type: str,
        source_id: str,
        source_name: str,
        body: str,
        user_id: str,
        history: list,
        token_stats: dict = None,
        event_data: dict = None,
        agent_provider: str = "",
        agent_duration_s: float = 0,
        system_prompt: str = "",
        deliver_to: list[str] = None,
    ) -> dict:
        """Create a notification + save autonomous session + distribute.

        The notification includes structured card data extracted from
        agent output and tool call history:
          - summary: Agent's understanding of the event (concise digest)
          - deliverables: Concrete outputs with per-item actions and metadata

        Args:
            source_type: "trigger" | "schedule" | "syncthing"
            source_id: trigger_id, task_id, etc.
            source_name: human-readable or slug, e.g. "GMAIL_NEW_GMAIL_MESSAGE"
            body: agent's final text output
            user_id: who to notify
            history: subagent's complete conversation history (session-compatible)
            token_stats: agent usage stats
            event_data: raw event payload (for title generation + audit)
            agent_provider: "gemini-cli" | "self" | etc.
            agent_duration_s: how long the agent worked
            system_prompt: the system prompt used for the subagent
            deliver_to: channel names to push to; None = desktop only
        """
        from card_extractor import extract_card_data

        title = generate_title(source_type, source_name, event_data)
        category = infer_category(source_type, source_name)

        # Extract structured card data from agent's work
        card = extract_card_data(
            agent_output=body,
            history=history,
        )

        # 1. Save autonomous session
        session_id = f"auto_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"
        notification_id = f"notif_{secrets.token_hex(6)}"

        session_data = {
            "id": session_id,
            "user_id": user_id,
            "created_at": time.time(),
            "last_active": time.time(),
            "model_override": None,
            "token_stats": token_stats or {
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_cached_tokens": 0,
                "total_api_calls": 0,
            },
            "history": history,
            "autonomous": True,
            "notification_id": notification_id,
            "source": {
                "type": source_type,
                "id": source_id,
                "slug": source_name,
                "event_data": event_data,
            },
        }
        self._save_session(session_id, session_data)

        # 2. Create notification index entry (with card data)
        now = datetime.now(timezone.utc).isoformat()
        notification = {
            "id": notification_id,
            "session_id": session_id,
            "source_type": source_type,
            "source_id": source_id,
            "source_name": source_name,
            "title": title,
            "category": category,
            "status": "unread",

            # ── Card data ──
            "summary": card["summary"],
            "deliverables": card["deliverables"],

            # ── Metadata ──
            "agent_provider": agent_provider,
            "agent_duration_s": round(agent_duration_s, 1),
            "agent_tokens": (token_stats or {}).get("total_tokens", 0),
            "user_id": user_id,
            "created_at": now,
            "read_at": None,
            "delivered_channels": [],
        }
        self._append_notification(notification)

        # 3. Distribute
        self._distribute(notification, body, deliver_to)

        logger.info(
            "Notification created: %s [%s] session=%s deliverables=%d",
            notification_id, title, session_id,
            len(card["deliverables"]),
        )
        return notification

    # ── Query ──

    @staticmethod
    def _migrate_notification(n: dict) -> dict:
        """Backfill old notifications missing card fields.

        Old schema had 'preview'; new schema has 'summary' + 'deliverables'.
        """
        if "summary" not in n:
            n["summary"] = n.get("preview", "")
        if "deliverables" not in n:
            n["deliverables"] = []
        return n

    def list(self, user_id: str, status: str = None,
             limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """List notifications for a user, newest first.

        Returns (notifications, total_count).
        """
        all_notifs = self._load_notifications()
        # Filter by user
        user_notifs = [n for n in all_notifs if n.get("user_id") == user_id]
        # Filter by status
        if status:
            user_notifs = [n for n in user_notifs if n.get("status") == status]
        total = len(user_notifs)
        # Paginate (already newest-first from storage)
        page = [self._migrate_notification(n) for n in user_notifs[offset:offset + limit]]
        return page, total

    def get(self, notification_id: str) -> dict | None:
        """Get a single notification by ID."""
        for n in self._load_notifications():
            if n["id"] == notification_id:
                return self._migrate_notification(n)
        return None

    def unread_count(self, user_id: str) -> int:
        """Count unread notifications for a user."""
        return sum(
            1 for n in self._load_notifications()
            if n.get("user_id") == user_id and n.get("status") == "unread"
        )

    # ── Status updates ──

    def mark_read(self, notification_id: str) -> bool:
        """Mark a notification as read."""
        return self._update_status(notification_id, "read",
                                   read_at=datetime.now(timezone.utc).isoformat())

    def mark_all_read(self, user_id: str) -> int:
        """Mark all unread notifications as read. Returns count updated."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            notifs = self._load_notifications()
            count = 0
            for n in notifs:
                if n.get("user_id") == user_id and n.get("status") == "unread":
                    n["status"] = "read"
                    n["read_at"] = now
                    count += 1
            if count:
                self._save_notifications(notifs)
            return count

    def dismiss(self, notification_id: str) -> bool:
        """Dismiss a notification."""
        return self._update_status(notification_id, "dismissed")

    def delete(self, notification_id: str) -> bool:
        """Delete a notification and optionally its session."""
        with self._lock:
            notifs = self._load_notifications()
            target = None
            for i, n in enumerate(notifs):
                if n["id"] == notification_id:
                    target = notifs.pop(i)
                    break
            if not target:
                return False
            self._save_notifications(notifs)
        # Optionally clean up session file
        session_id = target.get("session_id")
        if session_id:
            session_path = os.path.join(AUTO_SESSIONS_DIR, f"{session_id}.json")
            if os.path.exists(session_path):
                try:
                    os.remove(session_path)
                except OSError:
                    pass
        return True

    # ── Session access ──

    def load_session(self, session_id: str) -> dict | None:
        """Load an autonomous session by ID. Returns session-compatible dict."""
        path = os.path.join(AUTO_SESSIONS_DIR, f"{session_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load autonomous session %s: %s", session_id, e)
            return None

    def save_session(self, session_id: str, session_data: dict):
        """Save/update an autonomous session (e.g., after user continues chatting)."""
        self._save_session(session_id, session_data)

    # ── SSE Pub/Sub access ──

    @staticmethod
    def subscribe(user_id: str) -> queue.Queue:
        """Subscribe to real-time notification events."""
        return _pubsub.subscribe(user_id)

    @staticmethod
    def unsubscribe(user_id: str, q: queue.Queue):
        """Unsubscribe from notification events."""
        _pubsub.unsubscribe(user_id, q)

    # ── Internal: Storage ──

    def _save_session(self, session_id: str, data: dict):
        """Persist an autonomous session to disk."""
        os.makedirs(AUTO_SESSIONS_DIR, exist_ok=True)
        path = os.path.join(AUTO_SESSIONS_DIR, f"{session_id}.json")
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp_path, path)
        except Exception as e:
            logger.error("Failed to save autonomous session %s: %s", session_id, e)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _load_notifications(self) -> list[dict]:
        """Load all notifications from disk."""
        if not os.path.exists(NOTIFICATIONS_FILE):
            return []
        try:
            with open(NOTIFICATIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load notifications: %s", e)
            return []

    def _save_notifications(self, notifs: list[dict]):
        """Persist notifications to disk."""
        tmp_path = NOTIFICATIONS_FILE + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(notifs, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, NOTIFICATIONS_FILE)
        except Exception as e:
            logger.error("Failed to save notifications: %s", e)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _append_notification(self, notification: dict):
        """Append a notification (newest first)."""
        with self._lock:
            notifs = self._load_notifications()
            notifs.insert(0, notification)
            # Auto-prune: keep at most 500 notifications
            if len(notifs) > 500:
                notifs = notifs[:500]
            self._save_notifications(notifs)

    def _update_status(self, notification_id: str, status: str, **extra) -> bool:
        """Update a notification's status."""
        with self._lock:
            notifs = self._load_notifications()
            for n in notifs:
                if n["id"] == notification_id:
                    n["status"] = status
                    n.update(extra)
                    self._save_notifications(notifs)
                    return True
            return False

    # ── Internal: Distribution ──

    def _distribute(self, notification: dict, body: str,
                    deliver_to: list[str] = None):
        """Push notification to channels."""
        user_id = notification["user_id"]

        # Always push to Desktop SSE (real-time)
        _pubsub.publish(user_id, {
            "id": notification["id"],
            "title": notification["title"],
            "summary": notification["summary"],
            "deliverables": notification["deliverables"],
            "category": notification["category"],
            "source_type": notification["source_type"],
            "source_name": notification["source_name"],
            "created_at": notification["created_at"],
            "session_id": notification["session_id"],
            "agent_provider": notification["agent_provider"],
            "agent_duration_s": notification["agent_duration_s"],
        })
        notification["delivered_channels"].append("desktop")

        # Optional: push to IM channels (summary only — they can't render cards)
        if deliver_to:
            from channels.registry import get_channel
            for ch_name in deliver_to:
                if ch_name == "desktop":
                    continue
                channel = get_channel(ch_name)
                if channel:
                    try:
                        # IM gets title + summary, not the full body
                        im_text = f"🔔 {notification['title']}\n\n{notification['summary'][:500]}"
                        channel.send_reply(user_id, im_text)
                        notification["delivered_channels"].append(ch_name)
                    except Exception as e:
                        logger.warning("Failed to deliver to %s: %s", ch_name, e)


# Global singleton
notification_store = NotificationStore()
