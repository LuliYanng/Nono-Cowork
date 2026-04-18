"""
File-Drop Automation — event-driven agent workflows triggered by file sync events.

When a user drops a file into a designated sync folder (e.g., ~/Sync/翻译/),
the file event is matched against stored rules and a disposable agent session
is spawned to process it.

Architecture:
  SyncthingEventWatcher (RemoteChangeDetected)
    → SyncEventBuffer.register_listener()
    → FileDropEngine.on_sync_event()
    → match against rules (path pattern)
    → debounce (group rapid saves)
    → _run_autonomous_agent() (reused from composio_triggers)
    → NotificationStore → channel delivery

Rule storage: data/file_drop_rules.json
Each rule has:
  - id: str (UUID hex)
  - name: str (human-readable)
  - path_pattern: str (glob pattern relative to sync folder, e.g., "翻译/*")
  - folder_id: str (Syncthing folder ID, or "" for any folder)
  - agent_prompt: str (system prompt for the disposable agent)
  - model: str (LLM model override, empty = system default)
  - tool_access: str (permission preset)
  - actions: list[str] (which file actions trigger: ["added", "modified", "deleted"])
  - channel_user_id: str (IM delivery target)
  - channel_name: str (which channel to push results to)
  - enabled: bool
  - created_at: str (ISO timestamp)
"""

import fnmatch
import json
import logging
import os
import threading
import time
import uuid

logger = logging.getLogger("file_drop")

# ── Rule storage ──

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_RULES_PATH = os.path.join(_DATA_DIR, "file_drop_rules.json")
_lock = threading.Lock()


def _load_rules() -> list[dict]:
    """Load all file-drop rules from disk."""
    if not os.path.exists(_RULES_PATH):
        return []
    try:
        with open(_RULES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error("Failed to load file-drop rules: %s", e)
        return []


def _save_rules(rules: list[dict]):
    """Persist file-drop rules to disk."""
    os.makedirs(os.path.dirname(_RULES_PATH), exist_ok=True)
    with open(_RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


# ── Public CRUD API ──

def create_rule(
    name: str,
    path_pattern: str,
    agent_prompt: str,
    channel_user_id: str,
    channel_name: str,
    folder_id: str = "",
    model: str = "",
    tool_access: str = "full",
    actions: list[str] | None = None,
) -> dict:
    """Create and persist a new file-drop rule. Returns the rule dict."""
    rule = {
        "id": "fd_" + uuid.uuid4().hex[:10],
        "name": name,
        "path_pattern": path_pattern,
        "folder_id": folder_id,
        "agent_prompt": agent_prompt,
        "model": model,
        "tool_access": tool_access,
        "actions": actions or ["added", "modified"],
        "channel_user_id": channel_user_id,
        "channel_name": channel_name,
        "enabled": True,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with _lock:
        rules = _load_rules()
        rules.append(rule)
        _save_rules(rules)
    logger.info("File-drop rule created: %s — %s (pattern: %s)", rule["id"], name, path_pattern)
    return rule


def get_rule(rule_id: str) -> dict | None:
    """Get a rule by ID."""
    with _lock:
        for r in _load_rules():
            if r["id"] == rule_id:
                return r
    return None


def list_rules() -> list[dict]:
    """List all file-drop rules."""
    with _lock:
        return _load_rules()


def update_rule(rule_id: str, **updates) -> dict | None:
    """Update a rule's fields. Returns updated rule or None."""
    allowed = {"name", "path_pattern", "folder_id", "agent_prompt",
               "model", "tool_access", "actions", "enabled"}
    with _lock:
        rules = _load_rules()
        for r in rules:
            if r["id"] == rule_id:
                for k, v in updates.items():
                    if k in allowed:
                        r[k] = v
                _save_rules(rules)
                logger.info("File-drop rule updated: %s", rule_id)
                return r
    return None


def delete_rule(rule_id: str) -> bool:
    """Delete a rule by ID. Returns True if found and deleted."""
    with _lock:
        rules = _load_rules()
        original_len = len(rules)
        rules = [r for r in rules if r["id"] != rule_id]
        if len(rules) < original_len:
            _save_rules(rules)
            logger.info("File-drop rule deleted: %s", rule_id)
            return True
    return False


# ── Engine: event matching + debounce + execution ──

# Debounce window: if the same file is modified multiple times within this
# window, only trigger once (handles Word/Excel intermediate saves).
_DEBOUNCE_SECONDS = 3.0

# Serialize processing: one file-drop at a time to prevent OOM
_processing_lock = threading.Lock()


class FileDropEngine:
    """Matches sync events against file-drop rules and executes agents."""

    def __init__(self):
        self._pending: dict[str, float] = {}  # path → last_event_time (for debounce)
        self._pending_events: dict[str, object] = {}  # path → SyncEvent
        self._debounce_lock = threading.Lock()
        self._debounce_timer: threading.Timer | None = None

    def on_sync_event(self, event):
        """Callback registered with SyncEventBuffer.register_listener().

        Called for every change event. Matches against rules and schedules
        execution with debounce.
        """
        # Only fire on files the USER dropped in. Outbound events (the Agent's
        # own writes) would otherwise create a self-trigger loop.
        if getattr(event, "direction", "inbound") != "inbound":
            return

        # Quick filter: only process file events (not directories)
        if event.file_type != "file":
            return

        # Find matching rules
        matched_rules = self._match_rules(event)
        if not matched_rules:
            return

        logger.info(
            "File-drop match: %s %s → %d rule(s)",
            event.action, event.path, len(matched_rules),
        )

        # Debounce: buffer the event, schedule delayed processing
        key = f"{event.path}:{event.action}"
        with self._debounce_lock:
            self._pending[key] = time.time()
            self._pending_events[key] = event

            # Reset the debounce timer
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                _DEBOUNCE_SECONDS, self._process_pending
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _match_rules(self, event) -> list[dict]:
        """Find all enabled rules that match this event."""
        rules = _load_rules()
        matched = []
        for rule in rules:
            if not rule.get("enabled", True):
                continue

            # Check action type
            # NOTE: Syncthing's RemoteChangeDetected can report genuinely
            # new files as "modified" (platform quirk with index state).
            # So if a rule wants "added", we also accept "modified" and vice versa.
            rule_actions = set(rule.get("actions", ["added", "modified"]))
            effective_actions = set(rule_actions)
            if "added" in rule_actions or "modified" in rule_actions:
                effective_actions |= {"added", "modified"}
            if event.action not in effective_actions:
                continue

            # Check folder_id (empty = any folder)
            if rule.get("folder_id") and rule["folder_id"] != event.folder_id:
                continue

            # Check path pattern (glob match against relative path)
            pattern = rule["path_pattern"]
            if fnmatch.fnmatch(event.path, pattern):
                matched.append(rule)

        return matched

    def _process_pending(self):
        """Process all debounced events."""
        with self._debounce_lock:
            pending = dict(self._pending)
            events = dict(self._pending_events)
            self._pending.clear()
            self._pending_events.clear()

        now = time.time()
        for key, event_time in pending.items():
            # Only process events that have "settled" (no newer event for this path)
            if now - event_time < _DEBOUNCE_SECONDS - 0.1:
                continue  # Too recent, should have been caught by a newer timer

            event = events.get(key)
            if not event:
                continue

            # Re-match rules (they might have changed since the event was buffered)
            matched_rules = self._match_rules(event)
            for rule in matched_rules:
                self._execute_rule(rule, event)

    def _execute_rule(self, rule: dict, event):
        """Execute a file-drop rule in a background thread."""
        thread = threading.Thread(
            target=self._run_agent,
            args=(rule, event),
            daemon=True,
            name=f"file-drop-{rule['id'][:8]}",
        )
        thread.start()

    def _run_agent(self, rule: dict, event):
        """Run a disposable agent to process the file event.

        Serialized: only one file-drop agent runs at a time.
        """
        with _processing_lock:
            try:
                rule_name = rule["name"]
                agent_prompt = rule["agent_prompt"]
                model = rule.get("model", "")
                tool_access = rule.get("tool_access", "full")

                # Wait for file to be fully synced before processing
                if event.action != "deleted":
                    _wait_for_file(event.abs_path, timeout=30)

                # Build the event context for the agent
                file_info = {
                    "action": event.action,
                    "path": event.path,
                    "abs_path": event.abs_path,
                    "file_type": event.file_type,
                    "size": event.size,
                    "folder_id": event.folder_id,
                    "rule_name": rule_name,
                }

                # Append REPORT_RESULT_PROMPT for structured output
                from delivery.card_extractor import REPORT_RESULT_PROMPT
                full_prompt = agent_prompt
                if REPORT_RESULT_PROMPT not in full_prompt:
                    full_prompt = agent_prompt + "\n\n" + REPORT_RESULT_PROMPT

                # Build restricted tool set
                from tools import build_restricted_tools
                tools_override = build_restricted_tools(tool_access)

                # Format event as the task message
                event_str = json.dumps(file_info, ensure_ascii=False)
                task = (
                    f"[File-Drop Event: {rule_name}]\n"
                    f"```json\n{event_str}\n```"
                )

                # Run the subagent
                from subagent import get_provider
                provider = get_provider(name="self")

                logger.info(
                    "File-drop agent starting: rule=%s, file=%s, model=%s",
                    rule_name, event.path, model or "(default)",
                )

                final_text, history, stats = provider.run_with_history(
                    task=task,
                    system_prompt=full_prompt,
                    model=model,
                    tools_override=tools_override,
                )

                duration = time.time() - event.timestamp

                # Check for SKIP
                if not final_text or "[SKIP]" in final_text:
                    logger.info("File-drop skipped: rule=%s, file=%s", rule_name, event.path)
                    return

                # Store notification and distribute
                from config import OWNER_USER_ID
                from delivery.notifications import notification_store

                deliver_to = [rule["channel_name"]] if rule.get("channel_name") else None
                notification_store.create(
                    source_type="file_drop",
                    source_id=rule["id"],
                    source_name=f"📁 {rule_name}",
                    body=final_text,
                    user_id=OWNER_USER_ID,
                    history=history,
                    token_stats=stats,
                    event_data=file_info,
                    agent_provider=provider.name,
                    agent_duration_s=duration,
                    system_prompt=full_prompt,
                    deliver_to=deliver_to,
                )

                logger.info("File-drop completed: rule=%s, file=%s", rule_name, event.path)

            except Exception as e:
                logger.error(
                    "File-drop agent error: rule=%s, file=%s, error=%s",
                    rule.get("name", "?"), getattr(event, "path", "?"), e,
                    exc_info=True,
                )


def _wait_for_file(abs_path: str, timeout: int = 30):
    """Wait for a file to exist and stabilize (stop growing).

    Files reported by RemoteChangeDetected may still be downloading.
    We wait until the file exists and its size hasn't changed for 1 second.
    """
    start = time.time()
    last_size = -1

    while time.time() - start < timeout:
        if not os.path.exists(abs_path):
            time.sleep(0.5)
            continue

        try:
            current_size = os.path.getsize(abs_path)
        except OSError:
            time.sleep(0.5)
            continue

        if current_size == last_size and current_size > 0:
            return  # File is stable
        last_size = current_size
        time.sleep(1)

    logger.warning("File wait timeout: %s (may still be syncing)", abs_path)


# ── Singleton engine + startup ──

_engine: FileDropEngine | None = None


def get_engine() -> FileDropEngine:
    """Get (or create) the singleton FileDropEngine."""
    global _engine
    if _engine is None:
        _engine = FileDropEngine()
    return _engine


def start_file_drop_listener():
    """Register the file-drop engine as a listener on the Syncthing event buffer.

    Called by main.py at startup, AFTER start_watcher().
    Safe to call even if Syncthing watcher is not running (silently skips).
    """
    from integrations.syncthing_watcher import get_event_buffer

    buffer = get_event_buffer()
    if buffer is None:
        logger.info("File-drop listener skipped: Syncthing watcher not running")
        return

    engine = get_engine()
    buffer.register_listener(engine.on_sync_event)

    rules = _load_rules()
    enabled = [r for r in rules if r.get("enabled", True)]
    logger.info(
        "File-drop listener registered (%d rule(s), %d enabled)",
        len(rules), len(enabled),
    )
