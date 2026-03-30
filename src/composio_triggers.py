"""
Composio Triggers — event-driven workflows via Composio trigger subscriptions.

This module provides:
  1. A background WebSocket listener that receives trigger events from Composio
  2. Trigger recipe storage (trigger_slug + agent_prompt, persisted to JSON)
  3. Disposable agent sessions for processing events (one-shot, no history kept)
  4. Agent tools for creating/managing triggers (exposed to the LLM)

Architecture:
  Composio Cloud → WebSocket → TriggerListener
    → load trigger recipe (agent_prompt)
    → disposable agent_loop (one-shot)
    → if not [SKIP]: channel.send_reply() to user

The listener starts automatically when Composio is enabled and runs in a
background daemon thread.
"""

import json
import logging
import os
import threading
import time

from config import COMPOSIO_API_KEY, COMPOSIO_USER_ID

logger = logging.getLogger("composio.triggers")

# ── Trigger recipe storage ──
_TRIGGER_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "trigger_recipes.json"
)


def _load_recipes() -> dict:
    """Load trigger recipes from disk. Returns {trigger_id: recipe_dict}."""
    if not os.path.exists(_TRIGGER_STORE_PATH):
        return {}
    try:
        with open(_TRIGGER_STORE_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load trigger recipes: %s", e)
        return {}


def _save_recipes(recipes: dict):
    """Persist trigger recipes to disk."""
    os.makedirs(os.path.dirname(_TRIGGER_STORE_PATH), exist_ok=True)
    with open(_TRIGGER_STORE_PATH, "w") as f:
        json.dump(recipes, f, indent=2, ensure_ascii=False)


def _find_recipe_by_trigger_id(trigger_id: str) -> dict | None:
    """Look up a trigger recipe by its Composio trigger_id."""
    recipes = _load_recipes()
    return recipes.get(trigger_id)


def _find_recipe_by_slug(trigger_slug: str) -> dict | None:
    """Look up a trigger recipe by its trigger_slug (returns first match)."""
    recipes = _load_recipes()
    for recipe in recipes.values():
        if recipe.get("trigger_slug") == trigger_slug:
            return recipe
    return None


# ── Module-level state ──
_listener_thread = None
_subscription = None
_running = False
_processed_events: set[str] = set()  # dedup: event UUIDs already processed
_processing_lock = threading.Lock()  # serialize trigger processing (one at a time)


def is_enabled() -> bool:
    """Check if Composio triggers are available."""
    return bool(COMPOSIO_API_KEY)


def start_listener():
    """Start the background trigger event listener (WebSocket-based).

    Uses Composio SDK's subscribe() which maintains a WebSocket connection
    to receive trigger events in real-time. Safe to call multiple times
    (will only start once).
    """
    global _listener_thread, _running

    if not is_enabled():
        logger.info("Composio triggers disabled (COMPOSIO_API_KEY not set)")
        return

    if _listener_thread and _listener_thread.is_alive():
        logger.info("Trigger listener already running")
        return

    _running = True
    _listener_thread = threading.Thread(
        target=_listener_loop,
        name="composio-trigger-listener",
        daemon=True,
    )
    _listener_thread.start()
    logger.info("Composio trigger listener started")


def stop_listener():
    """Stop the background trigger event listener."""
    global _running, _subscription
    _running = False
    if _subscription:
        try:
            _subscription.stop()
        except Exception as e:
            logger.warning("Error stopping trigger subscription: %s", e)
        _subscription = None
    logger.info("Composio trigger listener stopped")


def _patch_trigger_subscription(subscription):
    """Monkey-patch SDK's _parse_payload to handle missing 'nanoId' in metadata.

    Composio SDK 0.11.3 expects metadata.nanoId but some triggers (e.g. Gmail)
    don't include it, causing a KeyError that silently drops the event.
    """
    import typing as t

    original_parse = subscription._parse_payload

    def _patched_parse_payload(event: str):
        try:
            return original_parse(event)
        except (KeyError, TypeError) as e:
            logger.warning("SDK _parse_payload failed (%s), using fallback parser", e)
            try:
                data = json.loads(event)

                # Detect format: V3 has top-level 'type' field, V1 has 'appName'
                if "type" in data and "data" in data:
                    # V3 format: {id, timestamp, type, metadata: {trigger_slug, trigger_id, user_id, ...}, data: {...}}
                    metadata = data.get("metadata", {})
                    trigger_slug = metadata.get("trigger_slug", "unknown")
                    trigger_id = metadata.get("trigger_id", "")
                    user_id = metadata.get("user_id", COMPOSIO_USER_ID)
                    event_payload = data.get("data", {})

                    logger.info("V3 event parsed: slug=%s, trigger_id=%s", trigger_slug, trigger_id)
                else:
                    # V1 format: {appName, payload, originalPayload, metadata: {nanoId, triggerName, connection, ...}}
                    metadata = data.get("metadata", {})
                    connection = metadata.get("connection", {})
                    trigger_slug = metadata.get("triggerName", "unknown")
                    trigger_id = metadata.get("nanoId", metadata.get("id", ""))
                    user_id = connection.get("clientUniqueUserId", COMPOSIO_USER_ID)
                    event_payload = data.get("payload", {})

                    logger.info("V1 event parsed: slug=%s, trigger_id=%s", trigger_slug, trigger_id)

                return t.cast(dict, {
                    "id": trigger_id,
                    "uuid": data.get("id", ""),
                    "user_id": user_id,
                    "toolkit_slug": data.get("appName", ""),
                    "trigger_slug": trigger_slug,
                    "metadata": {
                        "id": trigger_id,
                        "trigger_slug": trigger_slug,
                        "connected_account": {
                            "id": metadata.get("connected_account_id", ""),
                            "uuid": "",
                            "auth_config_id": metadata.get("auth_config_id", ""),
                            "auth_config_uuid": "",
                            "user_id": user_id,
                            "status": "ACTIVE",
                        },
                    },
                    "payload": event_payload,
                    "original_payload": data.get("originalPayload", event_payload),
                })
            except Exception as e2:
                logger.error("Fallback parser also failed: %s", e2)
                return None

    subscription._parse_payload = _patched_parse_payload
    logger.info("Patched SDK _parse_payload for nanoId compatibility")


def _listener_loop():
    """Main listener loop with auto-reconnect.

    IMPORTANT: old subscription must be fully stopped before creating a new one,
    otherwise we get duplicate WebSocket connections that process the same event
    multiple times (each spawning a subagent → memory explosion).
    """
    global _subscription

    while _running:
        # ── Ensure previous subscription is fully dead ──
        if _subscription:
            try:
                _subscription.stop()
            except Exception:
                pass
            _subscription = None

        try:
            from composio import Composio
            client = Composio()
            _subscription = client.triggers.subscribe(timeout=60.0)

            # Patch SDK bug: missing nanoId in some trigger events
            _patch_trigger_subscription(_subscription)

            @_subscription.handle()
            def _on_trigger_event(data):
                event_id = data.get("uuid", "") if isinstance(data, dict) else ""
                slug = data.get("trigger_slug", "?") if isinstance(data, dict) else "?"

                # Dedup: skip if we've already processed this event
                if event_id and event_id in _processed_events:
                    logger.debug("Skipping duplicate event: %s", event_id)
                    return

                if event_id:
                    _processed_events.add(event_id)
                    # Limit dedup set size to prevent memory leak
                    if len(_processed_events) > 200:
                        # Remove oldest entries (set doesn't keep order, just clear half)
                        to_remove = list(_processed_events)[:100]
                        for item in to_remove:
                            _processed_events.discard(item)

                logger.info("Trigger event received: slug=%s, id=%s", slug, event_id)
                _handle_trigger_event(data)

            logger.info("Trigger WebSocket connected, waiting for events...")
            _subscription.wait_forever()

        except Exception as e:
            if not _running:
                break
            logger.error("Trigger listener error (reconnecting in 10s): %s", e)
            time.sleep(10)


# ══════════════════════════════════════════════
# Event processing — autonomous agent session
# ══════════════════════════════════════════════

SKIP_MARKER = "[SKIP]"

# Default system prompt for triggers without a custom agent_prompt
_DEFAULT_TRIGGER_PROMPT = (
    "You are an autonomous agent processing a trigger event.\n"
    "Your task is to understand the event, take appropriate action if possible, "
    "and provide a concise report.\n\n"
    "Be concise. The user wants to quickly understand the situation, "
    "not read a verbose report.\n\n"
)

# Lazily build the full prompt (includes REPORT_RESULT_PROMPT from card_extractor)
_full_trigger_prompt: str | None = None

def _get_trigger_prompt() -> str:
    global _full_trigger_prompt
    if _full_trigger_prompt is None:
        from card_extractor import REPORT_RESULT_PROMPT
        _full_trigger_prompt = _DEFAULT_TRIGGER_PROMPT + REPORT_RESULT_PROMPT
    return _full_trigger_prompt


def _handle_trigger_event(data):
    """Process an incoming trigger event using a disposable agent session.

    Events are processed ONE AT A TIME (serialized via lock) to prevent
    multiple Gemini CLI processes from running simultaneously and
    causing OOM on memory-constrained servers.

    Results are stored in the NotificationStore (autonomous session +
    notification index) and distributed to channels.
    """
    # Acquire lock — if another event is being processed, this blocks
    with _processing_lock:
        try:
            if isinstance(data, dict):
                trigger_slug = data.get("trigger_slug", "unknown_trigger")
                trigger_id = data.get("id", "")
                user_id = data.get("user_id", COMPOSIO_USER_ID)
                event_data = data.get("payload") or data.get("original_payload") or data
            else:
                trigger_slug = "unknown_trigger"
                trigger_id = ""
                user_id = COMPOSIO_USER_ID
                event_data = data

            logger.info(
                "Processing trigger event: slug=%s, trigger_id=%s, user=%s",
                trigger_slug, trigger_id, user_id,
            )

            # Look up the trigger recipe (agent_prompt)
            recipe = _find_recipe_by_trigger_id(trigger_id) or _find_recipe_by_slug(trigger_slug)
            agent_prompt = (recipe or {}).get("agent_prompt") or _get_trigger_prompt()
            recipe_user_id = (recipe or {}).get("user_id") or user_id

            # Process the event via subagent with full history capture
            result = _run_autonomous_agent(
                agent_prompt, event_data, trigger_slug, trigger_id, recipe_user_id,
                deliver_to_channels=(recipe or {}).get("channel_name"),
            )

            if result is None:
                logger.info("Trigger event skipped (slug=%s)", trigger_slug)

        except Exception as e:
            logger.error("Error handling trigger event: %s", e, exc_info=True)


def _run_autonomous_agent(
    agent_prompt: str,
    event_data,
    trigger_slug: str,
    trigger_id: str,
    user_id: str,
    deliver_to_channels: str = None,
) -> str | None:
    """Run a one-shot agent to process a trigger event.

    Uses run_with_history() to capture the full execution trace,
    then stores everything via NotificationStore.

    Returns the agent's response, or None if the agent decided to skip.
    """
    from subagent import get_provider
    from notifications import notification_store

    # Format event data as the task
    event_str = json.dumps(event_data, ensure_ascii=False, default=str)
    if len(event_str) > 4000:
        event_str = event_str[:4000] + "... (truncated)"

    task = (
        f"[Trigger Event: {trigger_slug}]\n"
        f"```json\n{event_str}\n```"
    )

    start_time = time.time()
    try:
        # Use gemini-cli for richer trigger processing (has full tool access).
        # Note: gemini-cli (Node.js) uses more memory than 'self' provider.
        # If OOM occurs on low-memory servers, switch back to name="self".
        provider = get_provider(name="gemini-cli")
        logger.info(
            "Processing trigger %s via subagent provider: %s",
            trigger_slug, provider.name,
        )
        final_text, history, stats = provider.run_with_history(
            task=task, system_prompt=agent_prompt,
        )
    except Exception as e:
        logger.error("Subagent error for trigger %s: %s", trigger_slug, e)
        return None

    duration = time.time() - start_time

    # Check for SKIP
    if not final_text or SKIP_MARKER in final_text:
        return None

    # Store in NotificationStore (autonomous session + notification + distribute)
    deliver_to = [deliver_to_channels] if deliver_to_channels else None
    try:
        notification_store.create(
            source_type="trigger",
            source_id=trigger_id,
            source_name=trigger_slug,
            body=final_text,
            user_id=user_id,
            history=history,
            token_stats=stats,
            event_data=event_data if isinstance(event_data, dict) else {},
            agent_provider=provider.name,
            agent_duration_s=duration,
            system_prompt=agent_prompt,
            deliver_to=deliver_to,
        )
    except Exception as e:
        logger.error("Failed to store notification for trigger %s: %s", trigger_slug, e)
        # Fallback: try direct delivery if NotificationStore fails
        _deliver_to_user_fallback(user_id, final_text)

    return final_text


def _deliver_to_user_fallback(user_id: str, message: str):
    """Fallback delivery when NotificationStore fails. Tries any available channel."""
    try:
        from channels.registry import list_channels, get_channel
        for name in list_channels():
            channel = get_channel(name)
            if channel:
                channel.send_reply(user_id, message)
                logger.info("Fallback delivery to %s via %s", user_id, name)
                return
        logger.warning("No channels available for fallback delivery.")
    except Exception as e:
        logger.warning("Fallback delivery failed: %s", e)


# ══════════════════════════════════════════════
# Agent tools for trigger management
# ══════════════════════════════════════════════

def list_available_triggers(toolkit: str) -> str:
    """List available trigger types for a toolkit."""
    if not is_enabled():
        return json.dumps({"error": "Composio not enabled"})

    try:
        from composio import Composio
        client = Composio()
        triggers = client.triggers.list(toolkit_slugs=[toolkit])
        result = []
        for t in triggers:
            result.append({
                "slug": t.slug if hasattr(t, 'slug') else str(t),
                "display_name": getattr(t, 'display_name', ''),
                "description": getattr(t, 'description', ''),
            })
        return json.dumps({"triggers": result, "toolkit": toolkit}, ensure_ascii=False)
    except Exception as e:
        logger.error("Error listing triggers: %s", e)
        return json.dumps({"error": str(e)})


def create_trigger(trigger_slug: str, agent_prompt: str = None,
                   trigger_config: dict = None) -> str:
    """Create a new trigger instance with an optional agent_prompt.

    The agent_prompt is the system prompt for the disposable agent that
    processes each event. It should be written by the main agent based on
    the user's natural language instructions.

    Args:
        trigger_slug: The trigger type slug (e.g. 'GMAIL_NEW_GMAIL_MESSAGE').
        agent_prompt: System prompt for the event-processing agent.
        trigger_config: Optional configuration dict for the trigger.
    """
    if not is_enabled():
        return json.dumps({"error": "Composio not enabled"})

    try:
        from composio import Composio
        client = Composio()

        trigger = client.triggers.create(
            slug=trigger_slug,
            user_id=COMPOSIO_USER_ID,
            trigger_config=trigger_config or {},
        )

        trigger_id = getattr(trigger, 'trigger_id', str(trigger))

        # Persist the recipe (trigger_slug + agent_prompt)
        # so _handle_trigger_event can look it up later
        from context import get_context
        ctx = get_context()
        current_user_id = ctx.get("user_id", COMPOSIO_USER_ID)

        recipes = _load_recipes()
        recipes[trigger_id] = {
            "trigger_id": trigger_id,
            "trigger_slug": trigger_slug,
            "agent_prompt": agent_prompt or _get_trigger_prompt(),
            "trigger_config": trigger_config,
            "user_id": current_user_id,
            "channel_name": ctx.get("channel_name", ""),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _save_recipes(recipes)

        return json.dumps({
            "success": True,
            "trigger_id": trigger_id,
            "trigger_slug": trigger_slug,
            "has_agent_prompt": bool(agent_prompt),
            "message": (
                f"Trigger '{trigger_slug}' created successfully. "
                f"Events will be processed by the configured agent and delivered automatically."
            ),
        }, ensure_ascii=False)
    except Exception as e:
        logger.error("Error creating trigger: %s", e)
        return json.dumps({"error": str(e), "success": False})


def list_active_triggers() -> str:
    """List all active trigger instances for the current user."""
    if not is_enabled():
        return json.dumps({"error": "Composio not enabled"})

    try:
        from composio import Composio
        client = Composio()
        triggers = client.triggers.list_active()

        # Merge with local recipes for agent_prompt info
        recipes = _load_recipes()

        result = []
        for t in triggers:
            tid = getattr(t, 'trigger_id', str(t))
            recipe = recipes.get(tid, {})
            result.append({
                "trigger_id": tid,
                "trigger_slug": getattr(t, 'trigger_slug', ''),
                "status": getattr(t, 'status', ''),
                "created_at": getattr(t, 'created_at', ''),
                "has_agent_prompt": bool(recipe.get("agent_prompt")),
                "agent_prompt_preview": (recipe.get("agent_prompt") or "")[:100],
            })
        return json.dumps({"active_triggers": result}, ensure_ascii=False)
    except Exception as e:
        logger.error("Error listing active triggers: %s", e)
        return json.dumps({"error": str(e)})


def delete_trigger(trigger_id: str) -> str:
    """Delete/disable a trigger instance and remove its recipe."""
    if not is_enabled():
        return json.dumps({"error": "Composio not enabled"})

    try:
        from composio import Composio
        client = Composio()
        client.triggers.disable(trigger_id=trigger_id)

        # Remove recipe from local storage
        recipes = _load_recipes()
        recipes.pop(trigger_id, None)
        _save_recipes(recipes)

        return json.dumps({
            "success": True,
            "message": f"Trigger {trigger_id} disabled and recipe removed.",
        })
    except Exception as e:
        logger.error("Error deleting trigger: %s", e)
        return json.dumps({"error": str(e), "success": False})
