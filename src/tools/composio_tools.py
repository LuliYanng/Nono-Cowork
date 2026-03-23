"""
Composio integration — tool discovery and execution via Composio SDK.

This module provides:
  1. Meta-tool schemas (SEARCH_TOOLS, MULTI_EXECUTE_TOOL, etc.) for the LLM
  2. Execution of Composio tools via composio.tools.execute()
  3. Result cleaning to reduce token consumption (~50% reduction)

Composio is only initialized when COMPOSIO_API_KEY is set in the environment.
"""

import json
import logging
import threading
from composio import Composio
from composio_openai import OpenAIProvider

from config import COMPOSIO_API_KEY, COMPOSIO_USER_ID, COMPOSIO_AUTH_WAIT_TIMEOUT

AUTH_WAIT_TIMEOUT = COMPOSIO_AUTH_WAIT_TIMEOUT

logger = logging.getLogger("tools.composio")

# ── Module-level state ──
_composio_client = None
_composio_session = None
_composio_tools_schema = []


def is_enabled() -> bool:
    """Check if Composio integration is enabled."""
    return bool(COMPOSIO_API_KEY)


def init():
    """Initialize Composio client and session. Call once at startup."""
    global _composio_client, _composio_session, _composio_tools_schema

    if not is_enabled():
        logger.info("Composio disabled (COMPOSIO_API_KEY not set)")
        return

    try:
        # OpenAIProvider enables agentic session with meta tools
        # (SEARCH_TOOLS, MULTI_EXECUTE_TOOL, etc.)
        # The LLM call itself still goes through our own LiteLLM layer,
        # we only use Composio for tool schemas and execution.
        _composio_client = Composio(provider=OpenAIProvider())
        _composio_session = _composio_client.create(user_id=COMPOSIO_USER_ID)
        _composio_tools_schema = _composio_session.tools()
        logger.info(
            "Composio initialized: %d meta tools loaded for user '%s'",
            len(_composio_tools_schema), COMPOSIO_USER_ID,
        )
    except Exception as e:
        logger.error("Failed to initialize Composio: %s", e, exc_info=True)
        _composio_client = None
        _composio_session = None
        _composio_tools_schema = []


def get_tools_schema() -> list[dict]:
    """Return Composio's meta-tool schemas (for merging into the tools list)."""
    return _composio_tools_schema


def is_composio_tool(tool_name: str) -> bool:
    """Check if a tool name is a Composio meta-tool."""
    return tool_name.startswith("COMPOSIO_")

def _find_initiated_connection_id(toolkit: str) -> str | None:
    """Find the most recently initiated/initializing connection ID for a toolkit.

    The MANAGE_CONNECTIONS response returns status "initiated" (lowercase), but
    the actual API status is "INITIALIZING" (not "INITIATED"). We search for
    both to be safe, and also fall back to a broader search without toolkit
    filter since the toolkit_slugs parameter may not match.
    """
    # Status values to search: MANAGE_CONNECTIONS says "initiated",
    # but the real API uses "INITIALIZING" and "INITIATED"
    search_statuses = ["INITIALIZING", "INITIATED"]

    try:
        # Try with toolkit filter first
        conns = _composio_client.connected_accounts.list(
            user_ids=[COMPOSIO_USER_ID],
            toolkit_slugs=[toolkit],
            statuses=search_statuses,
            order_by="created_at",
            order_direction="desc",
            limit=1,
        )
        if conns.items:
            logger.info("Found connection_id %s for %s (with toolkit filter)",
                        conns.items[0].id, toolkit)
            return conns.items[0].id

        # Fallback: search without toolkit filter, then match manually
        # (toolkit_slugs filter might not work with all API versions)
        conns = _composio_client.connected_accounts.list(
            user_ids=[COMPOSIO_USER_ID],
            statuses=search_statuses,
            order_by="created_at",
            order_direction="desc",
            limit=10,
        )
        for c in conns.items:
            # Check toolkit.slug in the nested structure
            tk = getattr(c, "toolkit", None)
            tk_slug = tk.get("slug", "") if isinstance(tk, dict) else getattr(tk, "slug", "")
            if tk_slug == toolkit:
                logger.info("Found connection_id %s for %s (fallback match)",
                            c.id, toolkit)
                return c.id

    except Exception as e:
        logger.warning("Failed to find connection_id for %s: %s", toolkit, e)
    return None


# ── Background auth completion watchers ──
_auth_watchers: dict[str, threading.Thread] = {}


def _start_auth_watcher(toolkit: str, conn_id: str):
    """Start a background thread that polls for auth completion.

    Instead of waiting on a specific connection_id (which breaks when the user
    re-initiates auth and gets a new connection_id), we poll to check if the
    toolkit has ANY active connection for this user.
    """
    if toolkit in _auth_watchers and _auth_watchers[toolkit].is_alive():
        logger.info("Auth watcher for %s already running, skipping", toolkit)
        return

    # Capture execution context NOW (from the current thread)
    # because the watcher thread won't have it.
    try:
        from context import get_context
        ctx = get_context()
        captured_user_id = ctx.get("user_id")
        captured_channel = ctx.get("channel_name")
    except Exception:
        captured_user_id = None
        captured_channel = None

    def _watch():
        import time
        logger.info(
            "Auth watcher started for %s (timeout=%ds)",
            toolkit, AUTH_WAIT_TIMEOUT,
        )
        deadline = time.time() + AUTH_WAIT_TIMEOUT
        poll_interval = 1  # seconds

        while time.time() < deadline:
            try:
                # Try with toolkit filter first
                conns = _composio_client.connected_accounts.list(
                    user_ids=[COMPOSIO_USER_ID],
                    toolkit_slugs=[toolkit],
                    statuses=["ACTIVE"],
                    order_by="created_at",
                    order_direction="desc",
                    limit=1,
                )
                if conns.items:
                    logger.info("%s auth completed successfully!", toolkit)
                    _notify_auth_completed(toolkit, success=True,
                                           user_id=captured_user_id)
                    return

                # Fallback: search without toolkit filter, match manually
                conns = _composio_client.connected_accounts.list(
                    user_ids=[COMPOSIO_USER_ID],
                    statuses=["ACTIVE"],
                    order_by="created_at",
                    order_direction="desc",
                    limit=10,
                )
                for c in conns.items:
                    tk = getattr(c, "toolkit", None)
                    tk_slug = tk.get("slug", "") if isinstance(tk, dict) else getattr(tk, "slug", "")
                    if tk_slug == toolkit:
                        logger.info("%s auth completed (fallback match)!", toolkit)
                        _notify_auth_completed(toolkit, success=True,
                                               user_id=captured_user_id)
                        return
            except Exception as e:
                logger.debug("Auth watcher poll error for %s: %s", toolkit, e)

            time.sleep(poll_interval)

        # Timeout
        logger.warning("Auth watcher for %s timed out after %ds", toolkit, AUTH_WAIT_TIMEOUT)
        _notify_auth_completed(toolkit, success=False, status="timeout",
                               user_id=captured_user_id)

    def _watch_cleanup():
        _watch()
        _auth_watchers.pop(toolkit, None)

    t = threading.Thread(target=_watch_cleanup, name=f"auth-watcher-{toolkit}", daemon=True)
    _auth_watchers[toolkit] = t
    t.start()


def _notify_auth_completed(toolkit: str, success: bool, status: str = "ACTIVE",
                           user_id: str = None):
    """Auto-continue the agent loop after auth completion.

    For IM channels: injects a synthetic user message ("connected to xxx")
    and triggers a new agent_loop iteration, so the agent can continue the
    original task without the user having to manually type anything.

    For CLI: prints a notification (can't inject into blocking input()).
    """
    if success:
        message = f"I've connected to {toolkit} successfully. Please continue with the original task."
    else:
        message = (
            f"{toolkit} authorization was not completed (status: {status}). "
            "Please try again if needed."
        )

    if user_id:
        # IM channel mode: auto-trigger agent loop with the synthetic message
        try:
            from channels.registry import list_channels, get_channel
            from agent_runner import run_agent_for_message
            channel_names = list_channels()
            if channel_names:
                channel = get_channel(channel_names[0])
                if channel:
                    def reply_func(text):
                        channel.send_reply(user_id, text)
                    def status_func(text):
                        channel.send_status(user_id, text)
                    logger.info("Auto-triggering agent loop for %s auth completion", toolkit)
                    t = threading.Thread(
                        target=run_agent_for_message,
                        args=(user_id, message, reply_func, status_func, f"{channel.name}:auth"),
                        daemon=True,
                    )
                    t.start()
                    return
        except Exception as e:
            logger.warning("Failed to auto-trigger agent loop: %s", e)

    # CLI / fallback: print notification (can't inject into input())
    print(f"\n✅ Connected to {toolkit}! You can continue with your request.")
    logger.info("Auth notification (stdout): %s", message)


def _maybe_start_auth_watchers(raw_result: dict):
    """Check MANAGE_CONNECTIONS result for pending connections and start background watchers."""
    data = raw_result.get("data", {})
    results = data.get("results", {})
    if not results:
        return

    for toolkit, info in results.items():
        status = info.get("status")
        if status != "initiated":
            continue

        # Find the connection_id
        conn_id = (
            info.get("connection_id")
            or info.get("connectedAccountId")
            or _find_initiated_connection_id(toolkit)
        )

        if not conn_id:
            logger.warning(
                "Cannot find connection_id for %s — "
                "user will need to confirm manually",
                toolkit,
            )
            continue

        # Start background watcher (non-blocking)
        _start_auth_watcher(toolkit, conn_id)



def execute(tool_name: str, arguments: dict) -> str:
    """Execute a Composio tool and return the cleaned result as a JSON string.

    Args:
        tool_name: The Composio tool slug (e.g. COMPOSIO_SEARCH_TOOLS).
        arguments: The tool arguments dict.

    Returns:
        Cleaned JSON string ready to be placed in the tool message content.
    """
    if not _composio_client:
        return json.dumps({"error": "Composio not initialized"})

    try:
        raw_result = _composio_client.tools.execute(
            slug=tool_name,
            arguments=arguments,
            user_id=COMPOSIO_USER_ID,
            dangerously_skip_version_check=True,
        )

        # Start background auth watchers for any initiated connections
        if tool_name == "COMPOSIO_MANAGE_CONNECTIONS":
            _maybe_start_auth_watchers(raw_result)

        # TODO: Re-enable cleaning after functionality is verified
        # cleaned = _clean_tool_result(tool_name, raw_result)
        return json.dumps(raw_result, ensure_ascii=False)
    except Exception as e:
        logger.error("Composio execute error for %s: %s", tool_name, e)
        return json.dumps({"error": str(e), "successful": False})


# ══════════════════════════════════════════════
# Result cleaning — generic, no per-tool logic
# ══════════════════════════════════════════════

def _strip_examples(schema):
    """Recursively strip 'examples' fields from input schemas."""
    if isinstance(schema, dict):
        return {k: _strip_examples(v) for k, v in schema.items() if k != 'examples'}
    if isinstance(schema, list):
        return [_strip_examples(item) for item in schema]
    return schema


def _clean_tool_result(tool_name: str, raw: dict) -> dict:
    """Clean Composio tool results to reduce token consumption.

    Only cleans the two main meta-tools:
      - COMPOSIO_SEARCH_TOOLS: strips guidance, examples, non-primary schemas
      - COMPOSIO_MULTI_EXECUTE_TOOL: strips structure_info, remote_file_info, etc.

    All other tools pass through unchanged.
    """
    if tool_name == 'COMPOSIO_SEARCH_TOOLS':
        return _clean_search_tools(raw)
    elif tool_name == 'COMPOSIO_MULTI_EXECUTE_TOOL':
        return _clean_multi_execute(raw)
    return raw


def _clean_search_tools(raw: dict) -> dict:
    """Clean COMPOSIO_SEARCH_TOOLS result."""
    data = raw.get('data', {})

    # Collect all primary slugs
    primary_slugs = set()
    for r in data.get('results', []):
        primary_slugs.update(r.get('primary_tool_slugs', []))

    # Clean results: keep only [Required] steps, primary-related pitfalls
    cleaned_results = []
    for r in data.get('results', []):
        plan_steps = r.get('recommended_plan_steps', [])
        filtered_steps = [s for s in plan_steps if '[Required]' in s]

        pitfalls = r.get('known_pitfalls', [])
        filtered_pitfalls = [
            p for p in pitfalls
            if any(slug in p for slug in primary_slugs)
        ]

        cleaned_results.append({
            'use_case': r.get('use_case'),
            'recommended_plan_steps': filtered_steps,
            'known_pitfalls': filtered_pitfalls,
            'primary_tool_slugs': r.get('primary_tool_slugs'),
            'related_tool_slugs': r.get('related_tool_slugs'),
            'toolkits': r.get('toolkits'),
            'plan_id': r.get('plan_id'),
        })

    # Clean connection statuses: drop description, connection_details, current_user_info
    cleaned_connections = []
    for c in data.get('toolkit_connection_statuses', []):
        cleaned_connections.append({
            'toolkit': c.get('toolkit'),
            'has_active_connection': c.get('has_active_connection'),
            'status_message': c.get('status_message'),
        })

    # Clean tool schemas:
    #   - primary: full schema (minus examples)
    #   - related: only slug + truncated description (120 chars)
    cleaned_schemas = {}
    for slug, s in data.get('tool_schemas', {}).items():
        if slug in primary_slugs:
            cleaned_schemas[slug] = {
                'tool_slug': s.get('tool_slug'),
                'description': s.get('description'),
                'input_schema': _strip_examples(s.get('input_schema', {})),
                'hasFullSchema': s.get('hasFullSchema'),
            }
        else:
            desc = s.get('description', '')
            cleaned_schemas[slug] = {
                'tool_slug': s.get('tool_slug'),
                'description': desc[:120] + '...' if len(desc) > 120 else desc,
            }
            if s.get('schemaRef'):
                cleaned_schemas[slug]['schemaRef'] = s['schemaRef']

    return {
        'data': {
            'results': cleaned_results,
            'toolkit_connection_statuses': cleaned_connections,
            'tool_schemas': cleaned_schemas,
            'session': data.get('session'),
        },
        'successful': raw.get('successful'),
    }


def _clean_multi_execute(raw: dict) -> dict:
    """Clean COMPOSIO_MULTI_EXECUTE_TOOL result."""
    data = raw.get('data', {})
    cleaned_results = []
    for r in data.get('results', []):
        resp = r.get('response', {})
        cleaned_results.append({
            'tool_slug': r.get('tool_slug'),
            'data': resp.get('data', {}),
            'successful': resp.get('successful'),
            'error': resp.get('error'),
        })
    return {
        'data': {
            'results': cleaned_results,
            'session': {'id': data.get('session', {}).get('id')},
        },
        'successful': raw.get('successful'),
        'error': raw.get('error'),
    }
