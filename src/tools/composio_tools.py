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

        # Filter out sandbox tools — our agent runs on its own server with
        # full shell access (run_command), so Composio's remote sandbox is
        # redundant and actively misleads the LLM into using an isolated
        # environment instead of the local server.
        # REMOTE_WORKBENCH alone is ~10k chars (40% of all Composio schemas),
        # which dominates LLM attention and causes repeated wrong decisions.
        _EXCLUDED_TOOLS = {
            "COMPOSIO_REMOTE_WORKBENCH",
            "COMPOSIO_REMOTE_BASH_TOOL",
        }
        before = len(_composio_tools_schema)
        _composio_tools_schema = [
            t for t in _composio_tools_schema
            if t.get("function", {}).get("name") not in _EXCLUDED_TOOLS
        ]
        excluded = before - len(_composio_tools_schema)
        if excluded:
            logger.info(
                "Filtered %d sandbox tools: %s",
                excluded, ", ".join(_EXCLUDED_TOOLS),
            )

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


# ── Tool access presets → Composio session config ──

_COMPOSIO_ACCESS_CONFIG = {
    "read_only": {"tags": ["readOnlyHint"]},
    "read_write": {"tags": {"disable": ["destructiveHint"]}},
    "safe": {"tags": {"disable": ["destructiveHint"]}},
    # "full" = no restrictions
}


def create_restricted_tools_schema(tool_access: str) -> list[dict] | None:
    """Create Composio meta-tool schemas with restricted tool access.

    Creates a separate Composio session with the specified restrictions.
    The returned schemas can replace the default Composio schemas in tools_override.

    Args:
        tool_access: Preset name ("read_only", "read_write", "safe", "full").

    Returns:
        Restricted meta-tool schemas, or None if tool_access is "full" / unsupported.
    """
    if not _composio_client or tool_access in (None, "full"):
        return None

    config = _COMPOSIO_ACCESS_CONFIG.get(tool_access)
    if not config:
        logger.warning("Unknown Composio tool_access preset: %s, using full access", tool_access)
        return None

    try:
        restricted_session = _composio_client.create(
            user_id=COMPOSIO_USER_ID,
            **config,
        )
        restricted_schemas = restricted_session.tools()

        # Apply same exclusion filter as init()
        _EXCLUDED_TOOLS = {"COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_REMOTE_BASH_TOOL"}
        restricted_schemas = [
            t for t in restricted_schemas
            if t.get("function", {}).get("name") not in _EXCLUDED_TOOLS
        ]

        logger.info(
            "Created restricted Composio session (tool_access=%s): %d meta tools",
            tool_access, len(restricted_schemas),
        )
        return restricted_schemas

    except Exception as e:
        logger.error("Failed to create restricted Composio session: %s", e)
        return None


def is_composio_tool(tool_name: str) -> bool:
    """Check if a tool name is a Composio meta-tool."""
    return tool_name.startswith("COMPOSIO_")


def _find_connection_for_toolkit(toolkit: str) -> str | None:
    """Find the most recent pending connection for a toolkit.

    Checks both INITIALIZING and INITIATED statuses because
    Composio API uses them interchangeably in different contexts.
    """
    try:
        conns = _composio_client.connected_accounts.list(
            user_ids=[COMPOSIO_USER_ID],
            toolkit_slugs=[toolkit],
            statuses=["INITIALIZING", "INITIATED"],
            order_by="created_at",
            order_direction="desc",
            limit=1,
        )
        if conns.items:
            return conns.items[0].id
    except Exception as e:
        logger.warning("Failed to find connection for toolkit %s: %s", toolkit, e)
    return None


def wait_for_connection(toolkit: str, timeout: int = None) -> dict:
    """Block until a toolkit's connection becomes ACTIVE or FAILED.

    Called by the agent as a local tool. Blocks the agent loop, keeping
    the SSE stream alive so the frontend sees the result naturally.

    Note: We don't use SDK's wait_for_connection() because it only checks
    for ACTIVE status — if auth fails (FAILED), it keeps polling until
    timeout instead of returning immediately.

    Note 2: Composio doesn't always set FAILED status on auth failure —
    the connection may stay INITIALIZING. So we also check the user's
    /stop signal to allow early exit.
    """
    import time

    if not _composio_client:
        return {"error": "Composio not initialized", "successful": False}

    if timeout is None:
        timeout = AUTH_WAIT_TIMEOUT

    # Find the INITIALIZING connection for this toolkit
    conn_id = _find_connection_for_toolkit(toolkit)
    if not conn_id:
        return {
            "error": f"No pending connection found for '{toolkit}'. "
                     "Call COMPOSIO_MANAGE_CONNECTIONS first to initiate auth.",
            "successful": False,
        }

    # Get stop checker from session (for /stop command support)
    _is_stopped = None
    try:
        from context import get_context
        ctx = get_context()
        user_id = ctx.get("user_id")
        if user_id:
            from session import sessions
            _is_stopped = lambda: sessions.is_stopped(user_id)
    except Exception:
        pass

    logger.info("Waiting for %s connection %s (timeout=%ds)", toolkit, conn_id, timeout)

    deadline = time.time() + timeout
    while time.time() < deadline:
        # Check if user sent /stop
        if _is_stopped and _is_stopped():
            logger.info("Wait for %s interrupted by user stop", toolkit)
            return {
                "successful": False,
                "toolkit": toolkit,
                "status": "stopped",
                "error": "Stopped by user.",
            }

        try:
            conn = _composio_client.connected_accounts.get(nanoid=conn_id)
            status = conn.status

            if status == "ACTIVE":
                logger.info("%s connection %s is now ACTIVE!", toolkit, conn_id)
                return {
                    "successful": True,
                    "toolkit": toolkit,
                    "status": "ACTIVE",
                    "message": f"{toolkit} is now connected and ready to use.",
                }

            if status == "FAILED" or status == "EXPIRED":
                reason = getattr(conn, "status_reason", None) or "unknown"
                logger.warning("%s connection %s %s: %s", toolkit, conn_id, status, reason)
                return {
                    "successful": False,
                    "toolkit": toolkit,
                    "status": status,
                    "error": f"Authentication {status.lower()} for {toolkit}: {reason}. "
                             "Please initiate a new connection to retry.",
                }
        except Exception as e:
            logger.debug("Poll error for %s: %s", toolkit, e)

        time.sleep(1)

    logger.warning("wait_for_connection timed out for %s after %ds", toolkit, timeout)
    return {
        "successful": False,
        "toolkit": toolkit,
        "status": "timeout",
        "error": f"Connection did not complete within {timeout}s. "
                 "The user may not have completed the auth flow.",
    }


def _inject_wait_guidance(raw_result: dict):
    """Inject guidance into MANAGE_CONNECTIONS result to call composio_wait_for_connection.

    Composio's built-in instruction references 'COMPOSIO_WAIT_FOR_CONNECTIONS' which
    doesn't exist in our tool set. We replace it with our local tool name and add
    a strong directive so the LLM calls it automatically.
    """
    data = raw_result.get("data", {})
    results = data.get("results", {})
    initiated_toolkits = []

    for toolkit, info in results.items():
        if info.get("status") == "initiated":
            initiated_toolkits.append(toolkit)
            # Replace Composio's instruction with ours
            info["instruction"] = (
                f"IMPORTANT: Share the auth link with the user, then IMMEDIATELY call "
                f"composio_wait_for_connection(toolkit='{toolkit}') to wait for "
                f"the user to complete authentication. Do NOT ask the user to confirm — "
                f"the tool will automatically detect when auth is complete."
            )

    if initiated_toolkits:
        data["_agent_directive"] = (
            f"MUST DO: After sharing auth link(s), call composio_wait_for_connection "
            f"for each initiated toolkit: {initiated_toolkits}. "
            f"This blocks until the user completes auth — do NOT wait for user confirmation."
        )


def execute(tool_name: str, arguments: dict) -> str:
    """Execute a Composio tool and return the cleaned result as a JSON string.

    Args:
        tool_name: The Composio tool slug (e.g. COMPOSIO_SEARCH_TOOLS).
        arguments: The tool arguments dict.

    Returns:
        Cleaned JSON string ready to be placed in the tool message content.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
    from config import COMPOSIO_EXECUTE_TIMEOUT

    if not _composio_client:
        return json.dumps({"error": "Composio not initialized"})

    def _do_execute():
        return _composio_client.tools.execute(
            slug=tool_name,
            arguments=arguments,
            user_id=COMPOSIO_USER_ID,
            dangerously_skip_version_check=True,
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_execute)
            raw_result = future.result(timeout=COMPOSIO_EXECUTE_TIMEOUT)

        # For MANAGE_CONNECTIONS: inject guidance to call our local wait tool
        if tool_name == "COMPOSIO_MANAGE_CONNECTIONS":
            _inject_wait_guidance(raw_result)

        # Pass through full result — don't clean until we've validated
        # which fields are safe to strip without confusing the agent.
        # (Composio responses are also excluded from the spill mechanism
        # in agent.py, so the full data goes directly into context.)
        return json.dumps(raw_result, ensure_ascii=False)
    except FuturesTimeoutError:
        logger.error(
            "Composio execute TIMEOUT for %s after %ds", tool_name, COMPOSIO_EXECUTE_TIMEOUT
        )
        return json.dumps({
            "error": f"Tool execution timed out after {COMPOSIO_EXECUTE_TIMEOUT}s. "
                     "The remote API (e.g. Gmail) took too long to respond. "
                     "Please retry or try a different approach.",
            "successful": False,
            "timed_out": True,
        })
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
    """Clean COMPOSIO_SEARCH_TOOLS result — preserve guidance, trim fat.

    Previous version was too aggressive: it dropped execution_guidance,
    reference_workbench_snippets, and all Optional plan steps. This caused
    the agent to miss critical information like:
      - How to download s3url (workbench snippet)
      - When to use COMPOSIO_REMOTE_WORKBENCH (Optional Step 7)
      - How to handle expired tokens (Optional Step 6)

    New strategy: keep all guidance/steps/pitfalls, only trim:
      - Schema examples (verbose, not actionable)
      - Connection description/details (redundant with status)
      - Related tool schema descriptions (truncate, keep ref)
    """
    data = raw.get('data', {})

    # Collect all primary slugs
    primary_slugs = set()
    for r in data.get('results', []):
        primary_slugs.update(r.get('primary_tool_slugs', []))

    # Clean results: keep ALL steps, guidance, snippets, and pitfalls
    cleaned_results = []
    for r in data.get('results', []):
        entry = {
            'use_case': r.get('use_case'),
            'execution_guidance': r.get('execution_guidance'),
            'recommended_plan_steps': r.get('recommended_plan_steps', []),
            'known_pitfalls': r.get('known_pitfalls', []),
            'reference_workbench_snippets': r.get('reference_workbench_snippets', []),
            'primary_tool_slugs': r.get('primary_tool_slugs'),
            'related_tool_slugs': r.get('related_tool_slugs'),
            'toolkits': r.get('toolkits'),
            'plan_id': r.get('plan_id'),
        }
        cleaned_results.append(entry)

    # Clean connection statuses: drop verbose description & connection_details
    cleaned_connections = []
    for c in data.get('toolkit_connection_statuses', []):
        cleaned_connections.append({
            'toolkit': c.get('toolkit'),
            'has_active_connection': c.get('has_active_connection'),
            'status_message': c.get('status_message'),
        })

    # Clean tool schemas:
    #   - primary: full schema (minus examples)
    #   - related: only slug + truncated description (200 chars) + schemaRef
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
                'description': desc[:200] + '...' if len(desc) > 200 else desc,
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
