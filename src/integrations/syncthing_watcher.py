"""
Syncthing Event Watcher — background daemon that tracks file sync activity.

Provides automatic context injection: when the user sends a message, their
recent file sync activity (files added/modified/deleted from their local
device) is prepended to the message so the Agent naturally understands
references like "that file I just uploaded" or "the PDFs I put in".

Architecture:
  Syncthing Events API (/rest/events/disk)
    → long-poll in daemon thread
    → filter: only RemoteChangeDetected (user's changes, not Agent's)
    → SyncEventBuffer (ring buffer, dedup by path)
    → get_sync_context() → injected into user message in agent_runner.py

Extension point:
  SyncEventBuffer.register_listener(callback) allows future features
  (e.g., File-Drop automation rules) to subscribe to events without
  modifying this module.
"""

import fnmatch
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

logger = logging.getLogger("syncthing.watcher")

# ── Data directory for state persistence ──
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


# ═══════════════════════════════════════════
#  SyncEvent data structure
# ═══════════════════════════════════════════

@dataclass
class SyncEvent:
    """A single file change event observed by the Syncthing event watcher."""
    timestamp: float        # unix timestamp
    action: str             # "added" | "modified" | "deleted"
    path: str               # relative path within sync folder, e.g. "inbox/report.pdf"
    abs_path: str           # absolute path on VPS
    file_type: str          # "file" | "dir"
    folder_id: str          # Syncthing folder ID
    size: int | None = None        # bytes, None if file not yet downloaded
    synced: bool = False           # True if file has finished transferring
    is_conflict: bool = False      # True if this is a sync-conflict file
    # Direction of transfer relative to the VPS:
    #   "inbound"  = user's device → VPS (RemoteChangeDetected)
    #   "outbound" = VPS → user's device (LocalChangeDetected, i.e. Agent wrote it)
    direction: str = "inbound"
    # Per-file transfer progress 0..100; None if not actively transferring.
    progress: int | None = None


# ═══════════════════════════════════════════
#  Event buffer (ring buffer + dedup)
# ═══════════════════════════════════════════

class SyncEventBuffer:
    """In-memory ring buffer of recent Syncthing events.

    Features:
    - Fixed-size ring buffer (oldest events evicted automatically)
    - Time-windowed retrieval with same-path deduplication
    - Listener registration for future automation rules
    """

    def __init__(self, max_size: int = 200):
        self._events: deque[SyncEvent] = deque(maxlen=max_size)
        self._listeners: list = []
        self._lock = threading.Lock()

    def add(self, event: SyncEvent):
        """Add an event to the buffer and notify listeners."""
        with self._lock:
            self._events.append(event)
        # Notify listeners outside the lock to avoid deadlocks
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                logger.debug("Listener error: %s", e)

    def patch_latest(self, folder_id: str, path: str, **fields) -> SyncEvent | None:
        """Mutate the most recent event matching (folder_id, path) and return it.

        Used by transfer-phase events (ItemStarted/ItemFinished/DownloadProgress)
        to update progress/synced state on an already-buffered change event.
        Returns None if no matching event was found.
        """
        with self._lock:
            # Walk newest → oldest so the most recent event wins.
            for e in reversed(self._events):
                if e.folder_id == folder_id and e.path == path:
                    for k, v in fields.items():
                        setattr(e, k, v)
                    return e
        return None

    def get_recent(self, minutes: int = 30, limit: int = 20) -> list[SyncEvent]:
        """Get recent events, deduplicated by path (latest wins), newest first.

        Args:
            minutes: Only include events from the last N minutes.
            limit: Maximum number of events to return.
        """
        cutoff = time.time() - minutes * 60
        with self._lock:
            # Dedup by path: later events overwrite earlier ones for the same path
            seen: dict[str, SyncEvent] = {}
            for e in self._events:
                if e.timestamp > cutoff:
                    seen[e.path] = e
        # Sort newest first, then limit
        deduped = sorted(seen.values(), key=lambda e: e.timestamp, reverse=True)
        return deduped[:limit]

    def register_listener(self, callback):
        """Register a callback invoked on every new event.

        Extension point for future features (File-Drop rules, notifications).
        Callback signature: callback(event: SyncEvent)
        """
        self._listeners.append(callback)


# ═══════════════════════════════════════════
#  Event watcher (daemon thread)
# ═══════════════════════════════════════════

# Patterns to ignore (never show in context)
_IGNORE_PATTERNS = [
    ".stignore",
    ".stfolder/*",
    ".stversions/*",
    ".agent_snapshots/*",
    "*.tmp",
    "~$*",              # MS Office temp files
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.swp", "*.swo",  # vim swap files
]


def _should_ignore(path: str) -> bool:
    """Check if a file path should be excluded from the event buffer."""
    basename = os.path.basename(path)
    for pattern in _IGNORE_PATTERNS:
        if "/" in pattern:
            # Directory-based pattern: match against full relative path
            if fnmatch.fnmatch(path, pattern):
                return True
        else:
            # Basename pattern
            if fnmatch.fnmatch(basename, pattern):
                return True
    return False


def _format_time_ago(ts: float) -> str:
    """Format a timestamp as a human-readable relative time."""
    delta = int(time.time() - ts)
    if delta < 60:
        return "just now"
    elif delta < 3600:
        m = delta // 60
        return f"{m} min ago"
    elif delta < 86400:
        h = delta // 3600
        return f"{h}h ago"
    else:
        d = delta // 86400
        return f"{d}d ago"


def _format_size(size_bytes: int | None) -> str:
    """Format bytes into human-readable size."""
    if size_bytes is None:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"


class SyncthingEventWatcher:
    """Background daemon that long-polls Syncthing events and maintains a buffer.

    Only tracks RemoteChangeDetected events (changes from the user's device),
    naturally avoiding self-triggering from the Agent's own file writes.
    """

    def __init__(self):
        from tools.syncthing import SyncthingClient
        self._st = SyncthingClient()
        self._buffer = SyncEventBuffer()
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_seen_id = 0
        # v2: we subscribe to the general /rest/events stream (not /rest/events/disk),
        # which has a different ID sequence. A new state-file name forces a clean
        # restart on upgrade instead of trying to resume from a stale disk-stream ID.
        self._state_file = os.path.join(_DATA_DIR, "sync_watcher_state_v2.json")

        # Build folder path lookup: folder_id → absolute path
        self._folder_paths: dict[str, str] = {}
        try:
            for f in self._st.get_folders():
                self._folder_paths[f["id"]] = os.path.abspath(f["path"])
        except Exception as e:
            logger.warning("Failed to load folder paths: %s", e)

        self._load_state()

    # ── State persistence ──

    def _load_state(self):
        """Restore last_seen_id from disk for seamless restart."""
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file) as f:
                    state = json.load(f)
                self._last_seen_id = state.get("last_seen_id", 0)
                logger.info("Restored watcher state: last_seen_id=%d", self._last_seen_id)
        except Exception as e:
            logger.warning("Failed to load watcher state: %s", e)

    def _save_state(self):
        """Persist last_seen_id to disk."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump({"last_seen_id": self._last_seen_id}, f)
        except Exception as e:
            logger.debug("Failed to save watcher state: %s", e)

    # ── Lifecycle ──

    def start(self):
        """Start the background event polling thread."""
        if self._thread and self._thread.is_alive():
            logger.info("Event watcher already running")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._event_loop,
            name="syncthing-event-watcher",
            daemon=True,
        )
        self._thread.start()
        logger.info("Syncthing event watcher started (last_seen_id=%d)", self._last_seen_id)

    def stop(self):
        """Stop the background event polling thread."""
        self._running = False
        self._save_state()
        logger.info("Syncthing event watcher stopped")

    # ── Main polling loop ──

    # Subset of the general event stream we care about. RemoteChangeDetected
    # and LocalChangeDetected tell us WHAT changed; the Item*/DownloadProgress
    # events tell us how the transfer of that change is progressing.
    _SUBSCRIBED_EVENTS = (
        "RemoteChangeDetected,"
        "LocalChangeDetected,"
        "ItemStarted,"
        "ItemFinished,"
        "DownloadProgress"
    )

    def _event_loop(self):
        """Long-poll /rest/events with auto-reconnect."""
        while self._running:
            try:
                # Long-poll: blocks up to 60s waiting for events
                r = requests.get(
                    f"{self._st.url}/rest/events",
                    headers=self._st.headers,
                    params={
                        "since": self._last_seen_id,
                        "timeout": 60,
                        "events": self._SUBSCRIBED_EVENTS,
                    },
                    timeout=70,  # HTTP timeout slightly > Syncthing's poll timeout
                )
                r.raise_for_status()
                events = r.json()

                for event in events:
                    self._last_seen_id = event.get("id", self._last_seen_id)
                    etype = event.get("type")
                    if etype == "RemoteChangeDetected":
                        self._process_change_event(event, direction="inbound")
                    elif etype == "LocalChangeDetected":
                        self._process_change_event(event, direction="outbound")
                    elif etype == "ItemStarted":
                        self._process_item_started(event)
                    elif etype == "ItemFinished":
                        self._process_item_finished(event)
                    elif etype == "DownloadProgress":
                        self._process_download_progress(event)

                # Persist state periodically (after each batch)
                if events:
                    self._save_state()

            except requests.exceptions.Timeout:
                # Normal: no events in 60s
                continue
            except requests.exceptions.ConnectionError:
                if not self._running:
                    break
                logger.debug("Syncthing connection lost, retrying in 10s")
                time.sleep(10)
            except Exception as e:
                if not self._running:
                    break
                logger.error("Event watcher error: %s", e)
                time.sleep(5)

    def _process_change_event(self, event: dict, direction: str):
        """Handle a RemoteChangeDetected or LocalChangeDetected event.

        These announce that a file has changed on some peer; the actual
        byte-level transfer progress arrives later via ItemStarted/Finished
        and DownloadProgress events (inbound only).
        """
        data = event.get("data", {})
        path = data.get("path", "")
        action = data.get("action", "unknown")
        file_type = data.get("type", "file")
        folder_id = data.get("folder", data.get("folderID", ""))

        if not path:
            return

        # Filter out ignored files
        if _should_ignore(path):
            return

        # Resolve absolute path
        folder_root = self._folder_paths.get(folder_id, "")
        abs_path = os.path.join(folder_root, path) if folder_root else path

        # Check if this is a sync-conflict file
        is_conflict = ".sync-conflict-" in os.path.basename(path)

        # For inbound, "synced" means the file landed on our disk. For outbound,
        # the file was just written locally so it exists; "synced" from the user's
        # perspective means the remote device received it, which we only learn
        # later (currently approximated by ItemFinished on the outbound path).
        synced = False
        size = None
        if action != "deleted" and abs_path and os.path.exists(abs_path):
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                pass
            if direction == "inbound":
                synced = True

        # Use Syncthing's actual event timestamp (not time.time()!)
        # This prevents stale events from Syncthing's buffer appearing as 'just now'
        event_time = event.get("time", "")
        try:
            # Parse ISO 8601 timestamp from Syncthing, e.g. "2026-03-28T21:00:00+08:00"
            dt = datetime.fromisoformat(event_time)
            timestamp = dt.timestamp()
        except (ValueError, TypeError):
            timestamp = time.time()  # fallback

        sync_event = SyncEvent(
            timestamp=timestamp,
            action=action,
            path=path,
            abs_path=abs_path,
            file_type=file_type,
            folder_id=folder_id,
            size=size,
            synced=synced,
            is_conflict=is_conflict,
            direction=direction,
            progress=(100 if synced else None),
        )

        self._buffer.add(sync_event)
        logger.info("Sync event: %s %s %s [%s] (%s)",
                    direction, action, path, file_type,
                    "synced" if synced else "pending")

    def _process_item_started(self, event: dict):
        """ItemStarted: Syncthing has begun transferring a single item.

        We mark the corresponding buffered change event as actively transferring
        (progress=0) so the UI can switch it from 'pending' to 'syncing'.
        """
        data = event.get("data", {})
        path = data.get("item", "")
        folder_id = data.get("folder", "")
        if not path or _should_ignore(path):
            return
        self._buffer.patch_latest(folder_id, path, progress=0, synced=False)

    def _process_item_finished(self, event: dict):
        """ItemFinished: a single item's transfer has completed (or failed)."""
        data = event.get("data", {})
        path = data.get("item", "")
        folder_id = data.get("folder", "")
        err = data.get("error")
        if not path or _should_ignore(path):
            return

        if err:
            # Transfer failed — leave progress None as a signal, note in buffer.
            self._buffer.patch_latest(folder_id, path, synced=False, progress=None)
            logger.warning("Sync transfer failed: %s %s: %s", folder_id, path, err)
            return

        # Success. Refresh size from disk (inbound only — outbound file was
        # already sized at change-event time).
        folder_root = self._folder_paths.get(folder_id, "")
        abs_path = os.path.join(folder_root, path) if folder_root else path
        size = None
        if abs_path and os.path.exists(abs_path):
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                pass
        patch = {"progress": 100, "synced": True}
        if size is not None:
            patch["size"] = size
        self._buffer.patch_latest(folder_id, path, **patch)

    def _process_download_progress(self, event: dict):
        """DownloadProgress: per-folder, per-file progress by bytes/blocks.

        Event shape: {"data": {folder_id: {path: {total, pulling, copiedFromOrigin,
        copiedFromElsewhere, reused, bytesTotal, bytesDone}}}}
        """
        for folder_id, files in (event.get("data") or {}).items():
            if not isinstance(files, dict):
                continue
            for path, info in files.items():
                if not path or _should_ignore(path):
                    continue
                total = info.get("bytesTotal", 0) or info.get("total", 0)
                done = info.get("bytesDone", 0)
                if total <= 0:
                    continue
                pct = max(0, min(99, int(done * 100 / total)))
                self._buffer.patch_latest(folder_id, path, progress=pct)

    # ── Context generation ──

    def get_sync_context(self) -> str:
        """Generate the <file_sync_activity> context block for injection.

        Only inbound events (user → VPS) are surfaced — the Agent already
        knows what it wrote itself, and including outbound events would
        create noisy self-reference.

        Returns empty string if no recent events — zero overhead in that case.
        """
        all_recent = self._buffer.get_recent(minutes=30, limit=40)
        recent = [e for e in all_recent if e.direction == "inbound"][:20]
        if not recent:
            return ""

        lines = []
        for e in recent:
            ago = _format_time_ago(e.timestamp)
            icon = {"added": "📥", "modified": "📝", "deleted": "🗑️"}.get(e.action, "📄")
            conflict = "⚠️ CONFLICT " if e.is_conflict else ""
            # Use absolute path so Agent can reference files directly
            display_path = e.abs_path or e.path

            if e.action == "deleted":
                lines.append(f"• {ago} — {conflict}{icon} deleted: {display_path}")
            else:
                # Re-check sync status at context-generation time (file may
                # have finished downloading since the event was recorded)
                currently_synced = os.path.exists(e.abs_path) if e.abs_path else e.synced
                if currently_synced:
                    # Try to get current size if we didn't have it before
                    size = e.size
                    if size is None:
                        try:
                            size = os.path.getsize(e.abs_path)
                        except OSError:
                            pass
                    size_str = f" ({_format_size(size)})" if size else ""
                    lines.append(f"• {ago} — {conflict}{icon} {e.action}: {display_path}{size_str}")
                else:
                    lines.append(f"• {ago} — {conflict}{icon} {e.action}: {display_path} (⏳ syncing)")

        # Folder-level sync status
        folder_status = self._get_folder_sync_status()
        if folder_status:
            lines.append(folder_status)

        header = "Recent file changes from user's device (newest first):"
        return f"<file_sync_activity>\n{header}\n" + "\n".join(lines) + "\n</file_sync_activity>"

    def _get_folder_sync_status(self) -> str:
        """Check if any folder is still actively syncing."""
        try:
            for folder_id in self._folder_paths:
                status = self._st.get_folder_status(folder_id)
                need_files = status.get("needFiles", 0)
                need_bytes = status.get("needBytes", 0)
                if need_files > 0:
                    return (
                        f"⏳ Sync in progress: {need_files} file{'s' if need_files > 1 else ''} "
                        f"pending ({_format_size(need_bytes)})"
                    )
        except Exception:
            pass
        return ""

    @property
    def buffer(self) -> SyncEventBuffer:
        """Access the event buffer (for registering listeners)."""
        return self._buffer


# ═══════════════════════════════════════════
#  Module-level singleton + public API
# ═══════════════════════════════════════════

_watcher: SyncthingEventWatcher | None = None


def start_watcher():
    """Start the background Syncthing event watcher.

    Called by main.py at service startup. Silently skips if Syncthing
    is not configured or unreachable.
    """
    global _watcher
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()
        # Quick connectivity test
        st.get_system_status()
        _watcher = SyncthingEventWatcher()
        _watcher.start()
    except Exception as e:
        logger.info("Syncthing watcher disabled: %s", e)


def stop_watcher():
    """Stop the background watcher. Called at shutdown."""
    if _watcher:
        _watcher.stop()


def get_sync_context() -> str:
    """Get formatted sync context for injection into user messages.

    Returns empty string if watcher is not running or no recent events.
    """
    if _watcher:
        try:
            return _watcher.get_sync_context()
        except Exception as e:
            logger.debug("Failed to get sync context: %s", e)
    return ""


def get_event_buffer() -> SyncEventBuffer | None:
    """Get the event buffer for registering listeners (future automation)."""
    if _watcher:
        return _watcher.buffer
    return None
