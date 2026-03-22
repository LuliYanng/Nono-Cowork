"""
Composio Triggers — event-driven workflows via Composio trigger subscriptions.

This module provides:
  1. A background WebSocket listener that receives trigger events from Composio
  2. Agent tools for creating/managing triggers (exposed to the LLM)
  3. Event dispatch to the user via their active IM channel

Architecture:
  Composio Cloud → WebSocket → TriggerListener → agent_runner → IM Channel

The listener starts automatically when Composio is enabled and runs in a
background daemon thread.
"""

import json
import logging
import threading
import time

from config import COMPOSIO_API_KEY, COMPOSIO_USER_ID

logger = logging.getLogger("composio.triggers")

# ── Module-level state ──
_listener_thread = None
_subscription = None
_running = False


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


def _listener_loop():
    """Main listener loop with auto-reconnect."""
    global _subscription

    while _running:
        try:
            from composio import Composio
            client = Composio()
            _subscription = client.triggers.subscribe(timeout=60.0)

            @_subscription.handle()
            def _on_trigger_event(data):
                _handle_trigger_event(data)

            logger.info("Trigger WebSocket connected, waiting for events...")
            _subscription.wait_forever()

        except Exception as e:
            if not _running:
                break
            logger.error("Trigger listener error (reconnecting in 10s): %s", e)
            time.sleep(10)


def _handle_trigger_event(data):
    """Process an incoming trigger event and dispatch to the user's IM channel."""
    try:
        # Extract event metadata
        # The SDK subscription passes the trigger payload directly
        if isinstance(data, dict):
            metadata = data.get("metadata", {})
            event_data = data.get("data", data)
            trigger_slug = metadata.get("trigger_slug", "unknown_trigger")
            user_id = metadata.get("user_id", COMPOSIO_USER_ID)
        else:
            # Handle raw data format
            event_data = data
            trigger_slug = "unknown_trigger"
            user_id = COMPOSIO_USER_ID

        logger.info(
            "Trigger event received: slug=%s, user=%s",
            trigger_slug, user_id,
        )

        # Build a message for the agent
        event_summary = json.dumps(event_data, ensure_ascii=False, default=str)
        if len(event_summary) > 2000:
            event_summary = event_summary[:2000] + "... (truncated)"

        user_message = (
            f"[Composio Trigger Event: {trigger_slug}]\n"
            f"An event was triggered from an external service. Here's the data:\n"
            f"```json\n{event_summary}\n```\n"
            f"Please process this event and notify me about it."
        )

        # Dispatch through agent_runner to the user's active channel
        _dispatch_to_user(user_id, user_message)

    except Exception as e:
        logger.error("Error handling trigger event: %s", e, exc_info=True)


def _dispatch_to_user(user_id: str, message: str):
    """Dispatch a trigger event message to the user via their active IM channel.

    Tries to find the user's most recent channel and run the agent for them.
    Falls back to logging if no channel is available.
    """
    from channels.registry import list_channels, get_channel
    from session import sessions

    # Find a channel to reply through:
    # 1. If the user has an active session, we know which channel they used
    # 2. Otherwise, use the first registered channel
    channel = None
    channel_names = list_channels()

    if not channel_names:
        logger.warning(
            "No IM channels registered. Cannot deliver trigger event to user %s. "
            "Event message: %s", user_id, message[:200],
        )
        return

    # Use the first available channel
    channel = get_channel(channel_names[0])
    if not channel:
        logger.warning("Channel %s not found in registry", channel_names[0])
        return

    # Run the agent for this trigger event, same as if the user sent a message
    from agent_runner import run_agent_for_message

    def reply_func(text):
        channel.send_reply(user_id, text)

    def status_func(text):
        channel.send_status(user_id, text)

    # Run in a separate thread to not block the listener
    thread = threading.Thread(
        target=run_agent_for_message,
        args=(user_id, message, reply_func, status_func, f"{channel.name}:trigger"),
        daemon=True,
    )
    thread.start()


# ══════════════════════════════════════════════
# Agent tools for trigger management
# ══════════════════════════════════════════════

def list_available_triggers(toolkit: str) -> str:
    """List available trigger types for a toolkit.

    Args:
        toolkit: The toolkit slug (e.g. 'github', 'gmail', 'slack').

    Returns:
        JSON string with available trigger types.
    """
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


def create_trigger(trigger_slug: str, trigger_config: dict = None) -> str:
    """Create a new trigger instance.

    Args:
        trigger_slug: The trigger type slug (e.g. 'GMAIL_NEW_GMAIL_MESSAGE').
        trigger_config: Optional configuration dict for the trigger.

    Returns:
        JSON string with the created trigger info.
    """
    if not is_enabled():
        return json.dumps({"error": "Composio not enabled"})

    try:
        from composio import Composio
        client = Composio()

        # First, check what config is required
        trigger_type = client.triggers.get_type(trigger_slug)
        required_config = getattr(trigger_type, 'config', {})

        trigger = client.triggers.create(
            slug=trigger_slug,
            user_id=COMPOSIO_USER_ID,
            trigger_config=trigger_config or {},
        )

        trigger_id = getattr(trigger, 'trigger_id', str(trigger))
        return json.dumps({
            "success": True,
            "trigger_id": trigger_id,
            "trigger_slug": trigger_slug,
            "message": f"Trigger '{trigger_slug}' created successfully. Events will be delivered automatically.",
            "required_config": required_config,
        }, ensure_ascii=False)
    except Exception as e:
        logger.error("Error creating trigger: %s", e)
        return json.dumps({"error": str(e), "success": False})


def list_active_triggers() -> str:
    """List all active trigger instances for the current user.

    Returns:
        JSON string with active triggers.
    """
    if not is_enabled():
        return json.dumps({"error": "Composio not enabled"})

    try:
        from composio import Composio
        client = Composio()
        triggers = client.triggers.list_active(user_ids=[COMPOSIO_USER_ID])
        result = []
        for t in triggers:
            result.append({
                "trigger_id": getattr(t, 'trigger_id', str(t)),
                "trigger_slug": getattr(t, 'trigger_slug', ''),
                "status": getattr(t, 'status', ''),
                "created_at": getattr(t, 'created_at', ''),
            })
        return json.dumps({"active_triggers": result}, ensure_ascii=False)
    except Exception as e:
        logger.error("Error listing active triggers: %s", e)
        return json.dumps({"error": str(e)})


def delete_trigger(trigger_id: str) -> str:
    """Delete/disable a trigger instance.

    Args:
        trigger_id: The trigger instance ID to delete.

    Returns:
        JSON string with result.
    """
    if not is_enabled():
        return json.dumps({"error": "Composio not enabled"})

    try:
        from composio import Composio
        client = Composio()
        client.triggers.disable(trigger_id=trigger_id)
        return json.dumps({
            "success": True,
            "message": f"Trigger {trigger_id} disabled successfully.",
        })
    except Exception as e:
        logger.error("Error deleting trigger: %s", e)
        return json.dumps({"error": str(e), "success": False})
