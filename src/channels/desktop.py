"""
Desktop channel adapter — HTTP + SSE API for Electron desktop client

Start: uv run src/channels/desktop.py
"""
import os
import sys
import json
import queue
import logging
import threading

# Ensure src/ is on the Python path
_src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sse_starlette.sse import EventSourceResponse

from channels.base import Channel, SLASH_COMMANDS
from channels.registry import register_channel
from core.session import sessions, _serialize_history
from config import MODEL, MODEL_POOL, MODEL_REGISTRY, CONTEXT_LIMIT, OWNER_USER_ID

logger = logging.getLogger("channel.desktop")

# Desktop channel uses the unified owner ID (shared with other channels)
DESKTOP_USER_ID = OWNER_USER_ID
DESKTOP_PORT = int(os.getenv("DESKTOP_PORT", "8080"))
DESKTOP_API_TOKEN = os.getenv("DESKTOP_API_TOKEN", "")


# ──────────────────────────────────────────────────────────────
#  VPS-side folder path conventions
# ──────────────────────────────────────────────────────────────
# Synced workspace folders live under this parent on the VPS. Pure
# dev/internal convention — users never see this name.
import pathlib as _pathlib
VPS_SYNC_PARENT = _pathlib.Path.home() / "NonoWorkspaces"


class DesktopChannel(Channel):
    """HTTP + SSE channel for the Electron desktop client."""

    name = "desktop"

    def __init__(self):
        self._event_queues: dict[str, queue.Queue] = {}
        self._queue_lock = threading.Lock()

    def _get_queue(self, user_id: str) -> queue.Queue:
        """Get or create an event queue for a user."""
        with self._queue_lock:
            if user_id not in self._event_queues:
                self._event_queues[user_id] = queue.Queue()
            return self._event_queues[user_id]

    def _push_event(self, user_id: str, event_type: str, data: dict):
        """Push an SSE event to the user's queue."""
        q = self._get_queue(user_id)
        q.put({"event": event_type, "data": json.dumps(data, ensure_ascii=False)})

    # ── Channel interface implementation ──

    def start(self):
        """Start is a no-op; the FastAPI server is started externally."""
        pass

    def send_reply(self, user_id: str, text: str):
        """Send the Agent's final reply."""
        self._push_event(user_id, "reply", {"text": text})

    def send_status(self, user_id: str, text: str):
        """Send a status update."""
        self._push_event(user_id, "status", {"text": text})

    def dispatch_and_stream(self, user_id: str, user_text: str, images: list[dict] | None = None):
        """Dispatch the message and signal 'done' when the agent finishes.

        This overrides the base dispatch to add a 'done' event at the end
        and to capture on_event callbacks for richer SSE streaming.

        Args:
            images: Optional list of image dicts for multimodal input.
                    Each dict: {"data": "data:image/...;base64,...", "filename": "..."}
        """
        from core.agent_runner import run_agent_for_message

        def reply_func(text):
            self.send_reply(user_id, text)

        def status_func(text):
            self.send_status(user_id, text)

        # Forward structured agent events as SSE events
        def event_hook(evt):
            evt_type = evt.get("type")
            if evt_type == "text_chunk":
                # Streaming text chunk — forward immediately for real-time display
                self._push_event(user_id, "text_chunk", {
                    "content": evt.get("content", ""),
                })
            elif evt_type == "reasoning_chunk":
                # Streaming reasoning chunk
                self._push_event(user_id, "reasoning_chunk", {
                    "content": evt.get("content", ""),
                })
            elif evt_type == "reasoning":
                # Full reasoning (for non-streaming consumers) — skip for desktop
                # (desktop already received reasoning_chunk events)
                pass
            elif evt_type == "narration":
                # Skip — narration text was already delivered via text_chunk events
                pass
            elif evt_type == "tool_call":
                self._push_event(user_id, "thought", {
                    "type": "tool_call",
                    "tool_name": evt.get("tool_name", ""),
                    "args": evt.get("args", {}),
                    "description": evt.get("description", ""),
                    "round": evt.get("round"),
                })
            elif evt_type == "tool_result":
                # Truncate large results for SSE transport
                result = evt.get("result", "")
                if len(result) > 500:
                    result = result[:500] + f"… ({len(result)} chars)"
                self._push_event(user_id, "thought", {
                    "type": "tool_result",
                    "tool_name": evt.get("tool_name", ""),
                    "result": result,
                    "round": evt.get("round"),
                })

        # Check slash commands first (reuse base class logic)
        user_text_stripped = user_text.strip()
        if user_text_stripped.startswith("/"):
            parts = user_text_stripped[1:].split(None, 1)
            cmd_name = parts[0].lower()
            cmd_args = parts[1] if len(parts) > 1 else ""
            handler = SLASH_COMMANDS.get(cmd_name)
            if handler:
                handler[0](self, user_id, cmd_args)
                self._push_event(user_id, "done", {})
                return
        elif user_text_stripped.lower() in ("reset", "help"):
            handler = SLASH_COMMANDS.get(user_text_stripped.lower())
            if handler:
                handler[0](self, user_id, "")
                self._push_event(user_id, "done", {})
                return

        # Run agent (blocking in this thread)
        self.send_status(user_id, "Thinking...")
        run_agent_for_message(
            user_id, user_text_stripped,
            reply_func, status_func,
            channel_name=self.name,
            on_event_hook=event_hook,
            images=images or None,
        )
        self._push_event(user_id, "done", {})


# ── Token Authentication ──

class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Bearer token authentication for all API endpoints except /api/health."""

    # Paths that don't require authentication
    PUBLIC_PATHS = {"/api/health", "/docs", "/openapi.json"}

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths
        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        # Skip auth if no token is configured (dev mode)
        if not DESKTOP_API_TOKEN:
            return await call_next(request)

        # Validate Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "Missing Authorization header"}, status_code=401)

        token = auth_header[7:]  # Strip "Bearer "
        if token != DESKTOP_API_TOKEN:
            return JSONResponse({"error": "Invalid token"}, status_code=403)

        return await call_next(request)


# ── FastAPI app ──

channel = DesktopChannel()
app = FastAPI(title="Desktop Agent API")

# Auth middleware (must be added before CORS)
app.add_middleware(TokenAuthMiddleware)

# CORS: allow Electron dev server (localhost:5173) and production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    """Health check (public, no auth required)."""
    from channels.registry import list_channels
    return {
        "status": "ok",
        "model": MODEL,
        "channels": list_channels(),
        "auth_required": bool(DESKTOP_API_TOKEN),
    }


@app.get("/api/status")
async def status():
    """Get current session status."""
    info = sessions.get_status(DESKTOP_USER_ID)
    if not info:
        return {"active": False}

    stats = info.get("token_stats") or {}
    pt = stats.get("last_prompt_tokens", 0)
    pct = min(pt / CONTEXT_LIMIT * 100, 100) if CONTEXT_LIMIT else 0

    return {
        "active": True,
        "model": info.get("model_override") or MODEL,
        "history_len": info["history_len"],
        "api_calls": stats.get("total_api_calls", 0),
        "context_pct": round(pct, 1),
        "prompt_tokens": pt,
        "context_limit": CONTEXT_LIMIT,
        "total_tokens": stats.get("total_tokens", 0),
        "total_prompt_tokens": stats.get("total_prompt_tokens", 0),
        "total_completion_tokens": stats.get("total_completion_tokens", 0),
        "total_cached_tokens": stats.get("total_cached_tokens", 0),
        "total_cache_write_tokens": stats.get("total_cache_write_tokens", 0),
        "is_running": info["is_running"],
    }


@app.post("/api/chat")
async def chat(request: Request):
    """Send a message and stream back events via SSE.

    Request body: {"message": "user text here", "images": [{"data": "data:...", "filename": "..."}]}
    Returns: SSE stream with events: status, reply, done
    """
    body = await request.json()
    message = body.get("message", "").strip()
    images = body.get("images") or None  # list of {"data": "data:image/...;base64,...", "filename": "..."}
    if not message and not images:
        return JSONResponse({"error": "empty message"}, status_code=400)
    if not message and images:
        message = "(see attached images)"

    user_id = DESKTOP_USER_ID

    # Clear any leftover events from previous request
    q = channel._get_queue(user_id)
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break

    # Start agent in a background thread
    thread = threading.Thread(
        target=channel.dispatch_and_stream,
        args=(user_id, message),
        kwargs={"images": images},
        daemon=True,
    )
    thread.start()

    # Stream SSE events
    async def event_generator():
        import asyncio
        loop = asyncio.get_event_loop()

        while True:
            try:
                # IMPORTANT: q.get() is blocking — must run in executor
                # to avoid blocking the asyncio event loop (which causes
                # SSE connection drops and "stuck on thinking" in the UI).
                event = await loop.run_in_executor(
                    None, lambda: q.get(timeout=0.5)
                )
            except queue.Empty:
                # No event yet — send heartbeat to keep SSE alive
                yield {"comment": "heartbeat"}
                continue

            yield event

            # Stop streaming after 'done' event
            if event.get("event") == "done":
                break

    return EventSourceResponse(event_generator())


# ── RESTful: Session Management ──

@app.get("/api/sessions")
async def list_sessions(workspace_id: str | None = None):
    """List all saved sessions for the desktop user.

    Query params:
        workspace_id: when set, only return sessions belonging to that
                     workspace. Sessions without a workspace_id fall back
                     to the default workspace for filtering purposes.
    """
    # Ensure there is always an active session in memory so get_status works
    # (on server restart, _sessions is empty until get_or_create is called)
    sessions.get_or_create(DESKTOP_USER_ID)

    saved = sessions.list_sessions(DESKTOP_USER_ID)

    # Mark which session is currently active
    status = sessions.get_status(DESKTOP_USER_ID)
    active_id = status["session_id"] if status else None

    # Resolve fallback workspace for sessions missing workspace_id.
    # Uses the soft fallback (default → most-recently-active) so orphan
    # sessions always render under *some* workspace group, even before
    # the user has chosen a real default via onboarding.
    from core.workspace import workspaces as _workspaces
    fallback_ws = _workspaces.get_any_fallback()
    fallback_ws_id = fallback_ws["id"] if fallback_ws else None

    for s in saved:
        s["is_current"] = (s["id"] == active_id)
        if not s.get("workspace_id"):
            s["workspace_id"] = fallback_ws_id

    if workspace_id:
        saved = [s for s in saved if s.get("workspace_id") == workspace_id]

    return {"sessions": saved}


@app.post("/api/sessions")
async def create_session(request: Request):
    """Archive the current session and start a new one.

    Body (optional): { workspace_id: str } — bind the new session to
    a specific workspace. When omitted, inherits from the previous
    session or falls back to the default workspace.

    Refuses if an agent is still running — otherwise reset() would close
    the log file out from under the running thread and crash it.
    """
    # Accept optional body without failing when empty
    workspace_id: str | None = None
    try:
        if request.headers.get("content-length", "0") != "0":
            body = await request.json()
            if isinstance(body, dict):
                ws = body.get("workspace_id")
                if ws:
                    workspace_id = str(ws).strip() or None
    except Exception:
        workspace_id = None

    lock = sessions.get_lock(DESKTOP_USER_ID)
    if not lock.acquire(blocking=False):
        return JSONResponse(
            {"error": "Agent is currently running. Stop it and wait for it to finish before starting a new session."},
            status_code=409,
        )
    try:
        sessions.reset(DESKTOP_USER_ID, workspace_id=workspace_id)
    finally:
        lock.release()
    new_status = sessions.get_status(DESKTOP_USER_ID)
    return {
        "session_id": new_status["session_id"] if new_status else None,
        "workspace_id": new_status["workspace_id"] if new_status else None,
        "message": "New session created",
    }


@app.get("/api/sessions/current")
async def get_current_session():
    """Get the current session details including message history."""
    session = sessions.get_or_create(DESKTOP_USER_ID)
    info = sessions.get_status(DESKTOP_USER_ID)

    # Filter messages for the frontend:
    # - Strip system prompt (large, not useful for display)
    # - Serialize LiteLLM objects to plain dicts
    raw_history = _serialize_history(session["history"])
    display_messages = [
        msg for msg in raw_history if msg.get("role") != "system"
    ]

    return {
        "id": session["session_id"],
        "workspace_id": session.get("workspace_id"),
        "messages": display_messages,
        "model": session.get("model_override") or MODEL,
        "created_at": session["created_at"],
        "last_active": session["last_active"],
        "token_stats": dict(session["token_stats"]),
        "is_running": info["is_running"] if info else False,
    }


@app.put("/api/sessions/{session_id}/switch")
async def switch_session(session_id: str):
    """Switch to a different saved session.

    Returns the switched session's messages inline so the frontend
    doesn't need a separate GET /sessions/current call.
    """
    lock = sessions.get_lock(DESKTOP_USER_ID)
    if not lock.acquire(blocking=False):
        return JSONResponse(
            {"error": "Agent is currently running. Wait for it to finish before switching."},
            status_code=409,
        )
    try:
        ok = sessions.switch_session(DESKTOP_USER_ID, session_id)
    finally:
        lock.release()

    if not ok:
        return JSONResponse(
            {"error": f"Session '{session_id}' not found"},
            status_code=404,
        )

    # Return session data inline (same shape as GET /sessions/current)
    session = sessions.get_or_create(DESKTOP_USER_ID)
    info = sessions.get_status(DESKTOP_USER_ID)
    raw_history = _serialize_history(session["history"])
    display_messages = [msg for msg in raw_history if msg.get("role") != "system"]

    return {
        "message": f"Switched to session {session_id}",
        "id": session["session_id"],
        "workspace_id": session.get("workspace_id"),
        "messages": display_messages,
        "model": session.get("model_override") or MODEL,
        "created_at": session["created_at"],
        "last_active": session["last_active"],
        "token_stats": dict(session["token_stats"]),
        "is_running": info["is_running"] if info else False,
    }


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a saved session."""
    # Check if trying to delete the current session
    status = sessions.get_status(DESKTOP_USER_ID)
    if status and status["session_id"] == session_id:
        return JSONResponse(
            {"error": "Cannot delete the currently active session. Switch to another session first."},
            status_code=409,
        )

    ok = sessions.delete_session(DESKTOP_USER_ID, session_id)
    if not ok:
        return JSONResponse(
            {"error": f"Session '{session_id}' not found"},
            status_code=404,
        )
    return {"message": f"Session {session_id} deleted"}


# ── RESTful: Model Management ──

@app.get("/api/models")
async def list_models():
    """Get the list of available models and the currently active model.

    Each model in `available` carries display metadata (name, provider)
    so the frontend can render icons and group headings without parsing
    the LiteLLM routing string.
    """
    current = sessions.get_model(DESKTOP_USER_ID) or MODEL
    return {
        "current": current,
        "default": MODEL,
        "available": list(MODEL_REGISTRY),
    }


@app.put("/api/models/current")
async def set_model(request: Request):
    """Switch the LLM model for the current session."""
    body = await request.json()
    model = body.get("model", "").strip()

    if not model:
        return JSONResponse({"error": "Missing 'model' field"}, status_code=400)

    # "reset" / "default" → clear override, use global default
    if model.lower() in ("reset", "default"):
        sessions.set_model(DESKTOP_USER_ID, None)
        return {"model": MODEL, "message": f"Model reset to default: {MODEL}"}

    sessions.set_model(DESKTOP_USER_ID, model)
    return {"model": model, "message": f"Model switched to: {model}"}


# ── Slash command compat layer (for IM channels) ──

@app.post("/api/command/{cmd}")
async def command(cmd: str, request: Request):
    """Execute a slash command — compatibility layer for IM channels.

    Desktop frontend should prefer the RESTful endpoints above.
    This returns human-readable text, not structured JSON.
    """
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    args = body.get("args", "")
    # Support {scope: "delegate"} as shorthand for args="delegate" in /stop
    if not args and "scope" in body:
        args = body["scope"]

    handler = SLASH_COMMANDS.get(cmd)
    if not handler:
        return JSONResponse({"error": f"unknown command: {cmd}"}, status_code=404)

    # Use a lightweight shim channel to capture text output
    # instead of monkey-patching the real channel (thread-safe)
    responses: list[str] = []

    class _CaptureChannel:
        """Minimal channel-like object that collects send_status output."""
        name = "desktop"
        def send_status(self, user_id, text):
            responses.append(text)
        def send_reply(self, user_id, text):
            responses.append(text)

    handler[0](_CaptureChannel(), DESKTOP_USER_ID, args)

    # For stop commands: also push event to the SSE stream so frontend
    # gets instant feedback without waiting for the agent to finish
    if cmd == "stop":
        scope = args.strip().lower() if args else "all"
        channel._push_event(DESKTOP_USER_ID, "stopping", {"scope": scope})

    return {"result": "\n".join(responses)}


# ── RESTful: Notifications ──

@app.post("/api/notifications/mock")
async def inject_mock_notifications():
    """DEV ONLY: inject sample notifications for UI development."""
    import json as _json
    from delivery.notifications import notification_store

    # Helper: build a report_result tool call message
    def _report_call(call_id: str, card: dict):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "report_result",
                    "arguments": _json.dumps(card, ensure_ascii=False),
                },
            }],
        }

    # ── Mock 1: Email with full deliverables ──
    notification_store.create(
        source_type="trigger",
        source_id="ti_mock_gmail",
        source_name="GMAIL_NEW_GMAIL_MESSAGE",
        body="John Doe 发来了华南区 Q3 办公设备集中采购项目的询价邀请。已下载附件并创建回复草稿。",
        user_id=DESKTOP_USER_ID,
        history=[
            {"role": "user", "content": "[Trigger Event] 新邮件来自 John Doe，主题：询价邀请..."},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "GMAIL_GET_ATTACHMENT", "arguments": '{"message_id":"msg_123"}'}}
            ]},
            {"role": "tool", "content": '{"file_path":"SyncFromLocal/Inbox/报价表模板.xlsx"}', "tool_call_id": "c1"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "GMAIL_CREATE_EMAIL_DRAFT", "arguments": '{"to":"john@example.com","subject":"Re: 询价邀请"}'}}
            ]},
            {"role": "tool", "content": '{"id":"draft_789"}', "tool_call_id": "c2"},
            _report_call("c3", {
                "summary": "John Doe 发来了华南区 Q3 办公设备集中采购项目的询价邀请，附件是报价表模板，要求在 4 月 15 日前回复报价。已下载附件并创建 Gmail 回复草稿。",
                "deliverables": [
                    {
                        "type": "file",
                        "label": "报价表模板.xlsx",
                        "description": "附件已下载到 SyncFromLocal/Inbox/",
                        "metadata": {"path": "SyncFromLocal/Inbox/报价表模板.xlsx", "size": "24.5 KB", "action": "created"},
                    },
                    {
                        "type": "email_draft",
                        "label": "回复草稿",
                        "description": "已创建于 Gmail",
                        "metadata": {
                            "to": "john.doe@example.com",
                            "subject": "Re: 询价邀请：2026年第三季度办公设备集中采购项目（华南区）",
                            "body": (
                                "张伟先生：\n\n"
                                "您好！\n\n"
                                "感谢您对我们公司的信任与关注。我是陆莉，已收到您关于2026年第三季度华南区办公设备集中采购项目的询价邀请及附件中的需求明细。\n\n"
                                "关于您提到的员工人体工学椅（150把）和4K会议室投影仪（5台）的需求，"
                                "我们的团队正在根据您的规格要求进行评估。"
                                "我们将在一周内为您提供一份详细的阶梯报价单，并明确预计的交货周期及售后服务条款。\n\n"
                                "如有任何补充要求，请随时联系。\n\n"
                                "顺颂商祺！"
                            ),
                            "body_preview": "张伟先生：您好！感谢您对我们公司的信任与关注...",
                            "draft_id": "draft_789",
                        },
                    },
                ],
            }),
            {"role": "tool", "content": '{"status":"reported"}', "tool_call_id": "c3"},
        ],
        event_data={
            "sender": "John Doe <john.doe@example.com>",
            "subject": "询价邀请：2026年第三季度办公设备集中采购项目（华南区）",
        },
        agent_provider="self",
        agent_duration_s=42.3,
        token_stats={"total_tokens": 8500},
    )

    # ── Mock 2: Schedule — daily report ──
    notification_store.create(
        source_type="schedule",
        source_id="task_daily_report",
        source_name="每日工作汇总",
        body="今日共处理 12 封邮件，3 个文件变更。日报已生成。",
        user_id=DESKTOP_USER_ID,
        history=[
            {"role": "user", "content": "[Scheduled Task] 生成每日工作汇总报告"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c4", "type": "function", "function": {"name": "write_file", "arguments": '{"path":"Reports/2026-03-30.md"}'}}
            ]},
            {"role": "tool", "content": '{"success":true}', "tool_call_id": "c4"},
            _report_call("c5", {
                "summary": "今日共处理 12 封邮件，3 个文件变更，有 2 封邮件需要跟进回复。日报已生成。",
                "deliverables": [
                    {
                        "type": "report",
                        "label": "每日工作汇总 2026-03-30",
                        "description": "已生成到 Reports/2026-03-30.md",
                        "metadata": {"path": "Reports/2026-03-30.md"},
                    },
                ],
            }),
            {"role": "tool", "content": '{"status":"reported"}', "tool_call_id": "c5"},
        ],
        agent_provider="self",
        agent_duration_s=15.8,
        token_stats={"total_tokens": 3200},
    )

    # ── Mock 3: Syncthing file change — info only, no deliverables ──
    notification_store.create(
        source_type="syncthing",
        source_id="evt_sync_123",
        source_name="File Sync",
        body="检测到 proposal_v2.docx 更新，增加了第3章，修改了交付时间线。",
        user_id=DESKTOP_USER_ID,
        history=[
            {"role": "user", "content": "[Syncthing Event] proposal_v2.docx updated"},
            _report_call("c6", {
                "summary": "检测到 proposal_v2.docx 更新，相比 v1 增加了第3章定价策略，修改了第2章交付时间线从6周改为8周。无需操作。",
            }),
            {"role": "tool", "content": '{"status":"reported"}', "tool_call_id": "c6"},
        ],
        event_data={"action": "updated", "path": "Documents/proposal_v2.docx"},
        agent_provider="self",
        agent_duration_s=8.2,
        token_stats={"total_tokens": 1500},
    )

    return {"ok": True, "injected": 3, "message": "3 mock notifications created"}

@app.get("/api/notifications")
async def list_notifications(
    status: str = None, limit: int = 50, offset: int = 0
):
    """List notifications for the desktop user."""
    from delivery.notifications import notification_store
    notifs, total = notification_store.list(
        DESKTOP_USER_ID, status=status, limit=limit, offset=offset,
    )
    unread = notification_store.unread_count(DESKTOP_USER_ID)
    return {"notifications": notifs, "total": total, "unread": unread}


@app.get("/api/notifications/unread-count")
async def notifications_unread_count():
    """Get the unread notification count (for badges)."""
    from delivery.notifications import notification_store
    return {"count": notification_store.unread_count(DESKTOP_USER_ID)}


@app.get("/api/notifications/stream")
async def notifications_stream():
    """SSE stream for real-time notification push.

    Unlike /api/chat SSE (per-request, closes on 'done'), this is a
    persistent connection that stays open while the frontend is active.
    New notifications are pushed as 'new_notification' events.
    """
    from delivery.notifications import notification_store

    sub_queue = notification_store.subscribe(DESKTOP_USER_ID)

    async def event_generator():
        import asyncio
        loop = asyncio.get_event_loop()

        try:
            while True:
                try:
                    event = await loop.run_in_executor(
                        None, lambda: sub_queue.get(timeout=30)
                    )
                    yield {
                        "event": "new_notification",
                        "data": json.dumps(event, ensure_ascii=False),
                    }
                except queue.Empty:
                    # Heartbeat to keep connection alive
                    yield {"comment": "heartbeat"}
        finally:
            notification_store.unsubscribe(DESKTOP_USER_ID, sub_queue)

    return EventSourceResponse(event_generator())


@app.get("/api/notifications/{notification_id}")
async def get_notification(notification_id: str):
    """Get a single notification's details."""
    from delivery.notifications import notification_store
    notif = notification_store.get(notification_id)
    if not notif:
        return JSONResponse({"error": "Notification not found"}, status_code=404)
    return notif


@app.put("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str):
    """Mark a notification as read."""
    from delivery.notifications import notification_store
    ok = notification_store.mark_read(notification_id)
    if not ok:
        return JSONResponse({"error": "Notification not found"}, status_code=404)
    return {"message": "Marked as read"}


@app.put("/api/notifications/read-all")
async def mark_all_notifications_read():
    """Mark all notifications as read."""
    from delivery.notifications import notification_store
    count = notification_store.mark_all_read(DESKTOP_USER_ID)
    return {"message": f"Marked {count} notifications as read", "count": count}


@app.delete("/api/notifications/{notification_id}")
async def delete_notification(notification_id: str):
    """Delete a notification and its autonomous session."""
    from delivery.notifications import notification_store
    ok = notification_store.delete(notification_id)
    if not ok:
        return JSONResponse({"error": "Notification not found"}, status_code=404)
    return {"message": "Notification deleted"}


@app.post("/api/notifications/{notification_id}/action")
async def execute_notification_action(notification_id: str, request: Request):
    """Execute a user-approved action on a notification deliverable.

    This is the human-in-the-loop endpoint: the agent prepares content,
    and the user approves execution (send email, save draft, archive, etc.)

    Body: { action_type: "send_email" | "save_draft" | "archive",
            deliverable_index: 0 }
    """
    import json as _json
    from datetime import datetime, timezone
    from delivery.notifications import notification_store

    body = await request.json()
    action_type = body.get("action_type")
    deliverable_index = body.get("deliverable_index", 0)

    if not action_type:
        return JSONResponse({"error": "action_type is required"}, status_code=400)

    # ── Archive: no external API call needed ──
    if action_type == "archive":
        ok = notification_store.archive(notification_id)
        if not ok:
            return JSONResponse({"error": "Notification not found"}, status_code=404)
        return {"message": "Notification archived", "status": "archived"}

    # ── Action-based: need notification + deliverable ──
    notif = notification_store.get(notification_id)
    if not notif:
        return JSONResponse({"error": "Notification not found"}, status_code=404)

    deliverables = notif.get("deliverables", [])
    if deliverable_index >= len(deliverables):
        return JSONResponse({"error": "Deliverable not found"}, status_code=404)

    deliverable = deliverables[deliverable_index]
    meta = deliverable.get("metadata", {})

    # Check Composio is available
    from tools import composio_tools
    if not composio_tools.is_enabled():
        return JSONResponse(
            {"error": "Composio is not enabled. Set COMPOSIO_API_KEY."},
            status_code=503,
        )

    # ── Send Email via Composio ──
    if action_type == "send_email":
        to = meta.get("to", "")
        if not to:
            return JSONResponse({"error": "No recipient (to) in deliverable metadata"}, status_code=400)

        result_str = composio_tools.execute("GMAIL_SEND_EMAIL", {
            "recipient_email": to,
            "subject": meta.get("subject", ""),
            "body": meta.get("body", ""),
            "user_id": "me",
        })
        result_data = _json.loads(result_str)

        if result_data.get("successful") or (result_data.get("data") or {}).get("successful"):
            # Mark notification as resolved
            notification_store._update_status(
                notification_id, "resolved",
                resolved_at=datetime.now(timezone.utc).isoformat(),
                resolved_action="send_email",
            )
            return {"message": "Email sent successfully", "status": "resolved"}

        return JSONResponse(
            {"error": "Failed to send email", "details": result_data},
            status_code=500,
        )

    # ── Save Draft via Composio ──
    if action_type == "save_draft":
        result_str = composio_tools.execute("GMAIL_CREATE_EMAIL_DRAFT", {
            "recipient_email": meta.get("to", ""),
            "subject": meta.get("subject", ""),
            "body": meta.get("body", ""),
            "user_id": "me",
        })
        result_data = _json.loads(result_str)

        if result_data.get("successful") or (result_data.get("data") or {}).get("successful"):
            # Mark as resolved (user took action — saved draft)
            notification_store._update_status(
                notification_id, "resolved",
                resolved_at=datetime.now(timezone.utc).isoformat(),
                resolved_action="save_draft",
            )
            return {"message": "Draft saved to Gmail", "status": "resolved"}

        return JSONResponse(
            {"error": "Failed to save draft", "details": result_data},
            status_code=500,
        )

    return JSONResponse({"error": f"Unknown action_type: {action_type}"}, status_code=400)


@app.get("/api/notifications/{notification_id}/session")
async def get_notification_session(notification_id: str):
    """Load the autonomous session associated with a notification.

    Returns the full conversation history (session-compatible format)
    for review or to continue chatting.
    """
    from delivery.notifications import notification_store
    notif = notification_store.get(notification_id)
    if not notif:
        return JSONResponse({"error": "Notification not found"}, status_code=404)

    session_id = notif.get("session_id")
    if not session_id:
        return JSONResponse({"error": "No session associated"}, status_code=404)

    session_data = notification_store.load_session(session_id)
    if not session_data:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    # Filter out system prompt for display (like main session API)
    display_messages = [
        msg for msg in session_data.get("history", [])
        if msg.get("role") != "system"
    ]

    # Auto-mark as read when session is loaded
    notification_store.mark_read(notification_id)

    return {
        "id": session_data["id"],
        "notification_id": notification_id,
        "messages": display_messages,
        "source": session_data.get("source", {}),
        "token_stats": session_data.get("token_stats", {}),
        "created_at": session_data.get("created_at"),
        "last_active": session_data.get("last_active"),
        "autonomous": True,
    }


# ══════════════════════════════════════════════
# RESTful: Sync Config (path mapping for desktop)
# ══════════════════════════════════════════════

@app.get("/api/sync/config")
async def get_sync_config():
    """Return Syncthing folder config for frontend path mapping.

    The desktop frontend needs to convert VPS-side absolute paths
    (e.g., /root/NonoWorkspaces/inbox/report.pdf) to user-local paths
    (e.g., D:\\Sync\\inbox\\report.pdf).  This endpoint provides the
    VPS-side folder roots so the frontend can compute the relative path.
    """
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()
        folders = st.get_folders()  # Already normalized and filtered
        return {
            "folders": [
                {
                    "id": f["id"],
                    "label": f.get("label", f["id"]),
                    "path": f["path"],
                }
                for f in folders
            ]
        }
    except Exception:
        return {"folders": []}


@app.post("/api/sync/pair")
async def sync_pair(request: Request):
    """Auto-pair: desktop sends its Syncthing Device ID, VPS adds it and returns its own.

    This enables zero-input Syncthing pairing by using the existing
    authenticated API connection as the trust channel.

    Body: {
        device_id: str,         # Desktop's Syncthing Device ID
        device_name: str,       # Optional display name (default: "Desktop Client")
    }

    Returns: {
        vps_device_id: str,
        folders: [{id, label, path}],
        paired: bool,
    }
    """
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()

        body = await request.json()
        desktop_device_id = body.get("device_id", "").strip()
        desktop_name = body.get("device_name", "Desktop Client")

        if not desktop_device_id:
            return JSONResponse({"error": "Missing 'device_id'"}, status_code=400)

        # 1. Get VPS's own Device ID
        status = st.get_system_status()
        vps_device_id = status["myID"]

        # 2. Get current config to check existing devices
        config = st._get("/rest/config")
        existing_ids = {d["deviceID"] for d in config.get("devices", [])}

        # 3. Add desktop device if not already present
        if desktop_device_id not in existing_ids:
            st._post_json("/rest/config/devices", {
                "deviceID": desktop_device_id,
                "name": desktop_name,
                "autoAcceptFolders": False,
            })
            logger.info("Paired new desktop device: %s (%s)", desktop_name, desktop_device_id[:12])

        # NOTE: We intentionally do NOT share any folders here.
        # Folder sharing is user-initiated via the SyncFolderWidget
        # (POST /api/sync/folders).  Automatically sharing all VPS
        # folders caused ghost-folder propagation and broken paths.

        return {
            "vps_device_id": vps_device_id,
            "paired": True,
        }
    except Exception as e:
        logger.error("Sync pair failed: %s", e)
        return JSONResponse(
            {"error": f"Syncthing pairing failed: {str(e)}", "paired": False},
            status_code=503,
        )


# ── RESTful: Workspace Management ──

@app.get("/api/workspaces")
async def list_workspaces():
    """List all workspaces with current sync status annotations.

    Response shape:
      { workspaces: [{
          id, label, folder_id, is_default, created_at, last_active,
          folder_path, folder_state, folder_completion,  # null if folder missing
          session_count,
        }],
        default_workspace_id: str | null,
        active_workspace_id: str | null,   # workspace of the current session
      }
    """
    from core.workspace import workspaces as _workspaces

    # Ensure there's an active session so we can report active_workspace_id
    sessions.get_or_create(DESKTOP_USER_ID)
    status = sessions.get_status(DESKTOP_USER_ID)
    active_ws_id = status.get("workspace_id") if status else None

    ws_list = _workspaces.list()

    # Build folder_id → (state, completion, path) map
    folder_info: dict[str, dict] = {}
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()
        for f in st.get_folders():
            fid = f.get("id")
            if not fid:
                continue
            entry = {"path": f.get("path"), "state": "unknown", "completion": 0.0}
            try:
                fs = st.get_folder_status(fid)
                gb = fs.get("globalBytes", 0)
                ib = fs.get("inSyncBytes", 0)
                entry["state"] = fs.get("state", "unknown")
                entry["completion"] = round(
                    (ib / gb * 100) if gb > 0 else 100.0, 1,
                )
            except Exception:
                pass
            folder_info[fid] = entry
    except Exception:
        pass

    # Session counts per workspace. Use the soft fallback for orphan
    # sessions so they contribute to whichever workspace the sidebar
    # will render them under.
    all_sessions = sessions.list_sessions(DESKTOP_USER_ID)
    default_ws = _workspaces.get_default()
    default_ws_id = default_ws["id"] if default_ws else None
    fallback_ws = _workspaces.get_any_fallback()
    fallback_ws_id = fallback_ws["id"] if fallback_ws else None
    session_counts: dict[str, int] = {}
    for s in all_sessions:
        wsid = s.get("workspace_id") or fallback_ws_id
        if wsid:
            session_counts[wsid] = session_counts.get(wsid, 0) + 1

    out = []
    for w in ws_list:
        finfo = folder_info.get(w.get("folder_id") or "", {})
        out.append({
            "id": w["id"],
            "label": w["label"],
            "folder_id": w.get("folder_id"),
            "is_default": bool(w.get("is_default")),
            "created_at": w.get("created_at", 0),
            "last_active": w.get("last_active", 0),
            "folder_path": finfo.get("path"),
            "folder_state": finfo.get("state"),
            "folder_completion": finfo.get("completion"),
            "session_count": session_counts.get(w["id"], 0),
        })

    return {
        "workspaces": out,
        "default_workspace_id": default_ws_id,
        "active_workspace_id": active_ws_id,
    }


@app.post("/api/workspaces")
async def create_workspace(request: Request):
    """Create a workspace bound to a Syncthing folder.

    Body: { label: str, folder_id: str, is_default?: bool }

    The folder must already exist (create it via POST /api/sync/folders first).
    Returns the created workspace record.
    """
    from core.workspace import workspaces as _workspaces

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    label = (body.get("label") or "").strip()
    folder_id = (body.get("folder_id") or "").strip()
    is_default = bool(body.get("is_default"))

    if not folder_id:
        return JSONResponse({"error": "folder_id is required"}, status_code=400)

    # Sanity check: folder must exist on VPS
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()
        known = {f.get("id") for f in st.get_folders()}
        if folder_id not in known:
            return JSONResponse(
                {"error": f"folder_id '{folder_id}' not found on VPS; "
                          f"POST /api/sync/folders first"},
                status_code=404,
            )
    except Exception as e:
        logger.warning("Could not verify folder existence: %s", e)
        # Proceed anyway — Syncthing may be temporarily unreachable

    workspace = _workspaces.create(
        label=label or folder_id,
        folder_id=folder_id,
        is_default=is_default,
    )
    return workspace


@app.patch("/api/workspaces/{workspace_id}")
async def patch_workspace(workspace_id: str, request: Request):
    """Update a workspace's editable fields (label, is_default)."""
    from core.workspace import workspaces as _workspaces

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    updates = {}
    if "label" in body:
        updates["label"] = str(body["label"]).strip() or "Workspace"
    if "is_default" in body:
        updates["is_default"] = bool(body["is_default"])

    if not updates:
        return JSONResponse({"error": "no fields to update"}, status_code=400)

    result = _workspaces.update(workspace_id, **updates)
    if not result:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    return result


@app.delete("/api/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str):
    """Permanently delete a workspace and all VPS-side traces.

    Destructive — intentionally. The user-visible "delete workspace"
    promise is: nothing of this workspace remains anywhere except on
    the user's local disk (which we NEVER touch). Concretely this
    endpoint:

      1. Refuses if this is the default workspace (safety net).
      2. Removes the folder from VPS Syncthing config so the VPS
         stops accepting updates and stops broadcasting its state.
      3. Deletes the VPS-side physical files (`shutil.rmtree`).
      4. Deletes the workspace record.

    The desktop caller is responsible for a 5th step: removing the
    folder from the *local* Syncthing config (via the
    `syncthing-remove-folder` IPC). We return `folder_id` so the
    caller can do that without re-querying. This ordering (VPS
    forgets folder → VPS deletes files → local forgets folder)
    ensures no Syncthing "file deleted" signal ever reaches the
    user's local disk.

    Single-device assumption: if multiple clients share this folder
    in the future, this endpoint becomes a global nuke — revisit
    semantics at that point.
    """
    import shutil
    from core.workspace import workspaces as _workspaces
    from tools.syncthing import SyncthingClient

    ws = _workspaces.get(workspace_id)
    if ws is None:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    if ws.get("is_default"):
        return JSONResponse(
            {"error": "cannot delete the default workspace"},
            status_code=409,
        )

    folder_id = ws.get("folder_id")
    folder_path: str | None = None
    vps_folder_removed = False
    vps_files_removed = False

    logger.info(
        "[delete_workspace] Start: workspace=%s label=%r folder_id=%r",
        workspace_id, ws.get("label"), folder_id,
    )

    # Step 1+2: find & remove folder from VPS Syncthing, note its path.
    if not folder_id:
        logger.warning(
            "[delete_workspace] workspace %s has no folder_id — nothing to "
            "clean up on VPS (record delete only).",
            workspace_id,
        )
    else:
        try:
            st = SyncthingClient()
            vps_folders = st.get_folders()
            for f in vps_folders:
                if f.get("id") == folder_id:
                    folder_path = f.get("path")
                    break
            if folder_path is None:
                logger.warning(
                    "[delete_workspace] folder_id %s not registered in VPS "
                    "Syncthing (known folders: %s) — skipping VPS cleanup. "
                    "Was this workspace ever pushed to VPS?",
                    folder_id, [f.get("id") for f in vps_folders],
                )
            else:
                try:
                    st._delete(f"/rest/config/folders/{folder_id}")
                    vps_folder_removed = True
                    logger.info(
                        "[delete_workspace] Removed VPS Syncthing folder %s "
                        "(was at %s)",
                        folder_id, folder_path,
                    )
                except Exception as e:
                    logger.error(
                        "[delete_workspace] Failed to remove VPS folder %s: %s",
                        folder_id, e,
                    )
                    return JSONResponse(
                        {"error": f"failed to remove VPS folder: {e}"},
                        status_code=503,
                    )
        except Exception as e:
            logger.warning(
                "[delete_workspace] Syncthing unreachable; skipping VPS cleanup: %s",
                e,
            )

    # Step 3: physically delete VPS files. Only attempt after the folder
    # is unregistered from Syncthing — otherwise Syncthing would see an
    # empty folder and broadcast deletions.
    if vps_folder_removed and folder_path:
        if os.path.isdir(folder_path):
            try:
                shutil.rmtree(folder_path)
                vps_files_removed = True
                logger.info(
                    "[delete_workspace] Deleted VPS folder contents at %s",
                    folder_path,
                )
            except Exception as e:
                # Don't abort the whole operation — workspace record cleanup
                # is still worth doing. Report the partial state.
                logger.error(
                    "[delete_workspace] Failed to rmtree %s: %s",
                    folder_path, e,
                )
        else:
            logger.warning(
                "[delete_workspace] Expected folder path %s does not exist "
                "on disk — nothing to rmtree.",
                folder_path,
            )

    # Step 4: delete the workspace record.
    ok, msg = _workspaces.delete(workspace_id)
    if not ok:
        # Would only happen in a race — we already verified it exists
        # and isn't the default. Still, surface honestly.
        code = 404 if "not found" in msg else 409
        return JSONResponse({"error": msg}, status_code=code)

    return {
        "message": "deleted",
        "workspace_id": workspace_id,
        "folder_id": folder_id,
        "vps_folder_removed": vps_folder_removed,
        "vps_files_removed": vps_files_removed,
        "vps_folder_path": folder_path,
    }


@app.post("/api/sync/folders")
async def create_sync_folder(request: Request):
    """Desktop sends a folder to share; VPS creates a matching receive folder.

    Body: {
        folder_id: str,         # Syncthing folder ID (generated by desktop)
        folder_label: str,      # Human-readable name (e.g. "Projects")
        desktop_device_id: str, # Desktop's Syncthing device ID
    }

    VPS creates /root/NonoWorkspaces/<folder_label>/ and shares it back.
    """
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()

        body = await request.json()
        folder_id = body.get("folder_id", "").strip()
        folder_label = body.get("folder_label", "").strip()
        desktop_device_id = body.get("desktop_device_id", "").strip()

        if not folder_id or not folder_label or not desktop_device_id:
            return JSONResponse(
                {"error": "Missing folder_id, folder_label, or desktop_device_id"},
                status_code=400,
            )

        # Check if folder already exists on VPS
        existing = st.get_folders()
        for f in existing:
            if f["id"] == folder_id:
                # Make sure it has a workspace wrapper (idempotent)
                ws_info = None
                try:
                    from core.workspace import workspaces as _workspaces
                    ws_info = _workspaces.create(
                        label=f.get("label") or folder_label,
                        folder_id=folder_id,
                    )
                except Exception:
                    pass
                return {
                    "folder_id": folder_id,
                    "vps_path": f["path"],
                    "already_exists": True,
                    "workspace": ws_info,
                }

        # Create receive directory. NonoWorkspaces/ is our canonical VPS-side
        # parent for all synced folders (dev convention, not user-facing).
        import pathlib
        vps_path = str(pathlib.Path.home() / "NonoWorkspaces" / folder_label)
        os.makedirs(vps_path, exist_ok=True)

        # Create Syncthing folder config on VPS
        st._post_json("/rest/config/folders", {
            "id": folder_id,
            "label": folder_label,
            "path": vps_path,
            "devices": [{"deviceID": desktop_device_id}],
            "rescanIntervalS": 60,
            "fsWatcherEnabled": True,
            "fsWatcherDelayS": 1,
        })
        logger.info("Created sync folder: %s → %s", folder_label, vps_path)

        # Auto-wrap the folder in a workspace so the UI always has a
        # 1:1 workspace for every folder (V1 simplification).
        workspace_info = None
        try:
            from core.workspace import workspaces as _workspaces
            workspace_info = _workspaces.create(
                label=folder_label,
                folder_id=folder_id,
            )
        except Exception as e:
            logger.warning("Failed to auto-create workspace for folder %s: %s",
                           folder_id, e)

        return {
            "folder_id": folder_id,
            "vps_path": vps_path,
            "already_exists": False,
            "workspace": workspace_info,
        }

    except Exception as e:
        logger.error("Create sync folder failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=503)


@app.delete("/api/sync/folders/{folder_id}")
async def delete_sync_folder(folder_id: str):
    """Remove a synced folder from VPS Syncthing config (does not delete files)."""
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()

        # Verify folder exists
        existing = st.get_folders()
        found = any(f["id"] == folder_id for f in existing)
        if not found:
            return JSONResponse({"error": "Folder not found"}, status_code=404)

        st._delete(f"/rest/config/folders/{folder_id}")
        logger.info("Removed sync folder: %s", folder_id)

        # Also drop the workspace that wrapped this folder (if any).
        # If it was the default workspace we refuse and the caller
        # should promote another workspace first.
        removed_workspace_id = None
        try:
            from core.workspace import workspaces as _workspaces
            ws = _workspaces.get_by_folder(folder_id)
            if ws:
                ok, _msg = _workspaces.delete(ws["id"])
                if ok:
                    removed_workspace_id = ws["id"]
                else:
                    logger.info(
                        "Folder %s removed but its workspace %s is default; kept",
                        folder_id, ws["id"],
                    )
        except Exception as e:
            logger.warning("Workspace cleanup failed: %s", e)

        return {"removed": True, "workspace_id": removed_workspace_id}

    except Exception as e:
        logger.error("Delete sync folder failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=503)


@app.get("/api/sync/folders")
async def list_sync_folders():
    """List all synced folders with cross-device sync status.

    state/completion reflects both VPS and every connected peer — a folder
    is only 'idle' when all sides have drained pending changes. The older
    VPS-local-only view flipped to idle while the user's machine was still
    downloading, which made the sync widget misleading.
    """
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()

        folders = st.get_folders()
        try:
            connected = st.get_connected_device_ids()
        except Exception:
            connected = set()

        result = []
        for f in folders:
            try:
                info = st.get_folder_sync_info(f["id"], connected=connected)
                state = info["state"]
                completion = info["completion"]
            except Exception:
                state = "error"
                completion = 0

            result.append({
                "id": f["id"],
                "label": f.get("label", f["id"]),
                "path": f["path"],
                "state": state,
                "completion": completion,
            })

        return {"folders": result}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@app.get("/api/sync/status")
async def get_sync_status():
    """Return real-time Syncthing sync status for the frontend indicator.

    Returns: {
        online: bool,           # Is Syncthing reachable?
        device_id: str,         # VPS Device ID (for display/debug)
        connections: {          # Connected desktop devices
            total: int,
            connected: int,
            devices: [{id, name, connected, address}]
        },
        folders: [{             # Per-folder sync status
            id, label, state,   # state: "idle" | "syncing" | "error"
            completion: float,  # 0-100 percent
        }],
    }
    """
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()

        # System status
        sys_status = st.get_system_status()
        device_id = sys_status.get("myID", "")

        # Connections
        conn_data = st.get_connections()
        connections_map = conn_data.get("connections", {})
        devices = []
        connected_count = 0
        for did, info in connections_map.items():
            if did == device_id:
                continue  # Skip self
            is_connected = info.get("connected", False)
            if is_connected:
                connected_count += 1
            devices.append({
                "id": did,
                "name": info.get("name", did[:12]),
                "connected": is_connected,
                "address": info.get("address", ""),
            })

        # Folder status — cross-device view (VPS + connected peers)
        folders = st.get_folders()
        connected_ids = {d["id"] for d in devices if d["connected"]}
        folder_statuses = []
        for f in folders:
            try:
                info = st.get_folder_sync_info(f["id"], connected=connected_ids)
                folder_statuses.append({
                    "id": f["id"],
                    "label": f.get("label", f["id"]),
                    "state": info["state"],
                    "completion": info["completion"],
                })
            except Exception:
                folder_statuses.append({
                    "id": f["id"],
                    "label": f.get("label", f["id"]),
                    "state": "error",
                    "completion": 0,
                })

        return {
            "online": True,
            "device_id": device_id,
            "connections": {
                "total": len(devices),
                "connected": connected_count,
                "devices": devices,
            },
            "folders": folder_statuses,
        }
    except Exception:
        return {
            "online": False,
            "device_id": "",
            "connections": {"total": 0, "connected": 0, "devices": []},
            "folders": [],
        }


@app.get("/api/sync/events")
async def list_sync_events(
    minutes: int = 30,
    limit: int = 20,
    workspace_id: str | None = None,
    folder_id: str | None = None,
    scope: str = "active",
):
    """List recent file-level sync events from the Syncthing event buffer.

    These are per-file events (added/modified/deleted) tracked by the
    SyncthingEventWatcher daemon. Includes both inbound (user → VPS) and
    outbound (VPS → user) directions.

    Query params:
        minutes:      Time window in minutes (default: 30)
        limit:        Max events to return (default: 20)
        workspace_id: When set, only events belonging to this workspace's folder
        folder_id:    When set, only events for this raw Syncthing folder_id
        scope:        "active" (default) = filter by the current session's
                      workspace; "all" = no workspace filter. Ignored when
                      ``workspace_id``/``folder_id`` is explicitly supplied.

    Returns: {
        events: [{
            path: str,           # Relative path within sync folder
            abs_path: str,       # Absolute path on VPS
            action: str,         # "added" | "modified" | "deleted"
            direction: str,      # "inbound" | "outbound"
            state: str,          # "syncing" | "done" | "error"
            progress: int|null,  # 0-100 if syncing with known progress, null otherwise
            time_ago: str,       # Human-readable time ("Just now", "2 min ago")
            timestamp: float,    # Unix timestamp
            folder_id: str,      # Syncthing folder ID
        }],
        total_syncing: int,      # Number of files currently being synced
    }
    """
    from integrations.syncthing_watcher import get_event_buffer, _format_time_ago
    from core.workspace import workspaces as _workspaces

    # Resolve the effective folder_id filter.
    # Explicit folder_id wins → explicit workspace_id → scope-based default.
    target_folder_id: str | None = folder_id
    if not target_folder_id and workspace_id:
        ws = _workspaces.get(workspace_id)
        if ws:
            target_folder_id = ws.get("folder_id")
    if not target_folder_id and not workspace_id and scope == "active":
        # Default: scope to the active session's workspace so the UI
        # only shows events for the workspace the user is looking at.
        status = sessions.get_status(DESKTOP_USER_ID)
        active_ws_id = status.get("workspace_id") if status else None
        if active_ws_id:
            ws = _workspaces.get(active_ws_id)
            if ws:
                target_folder_id = ws.get("folder_id")

    buffer = get_event_buffer()
    if not buffer:
        return {"events": [], "total_syncing": 0}

    # Self-healing reconcile: only when both VPS and every connected peer
    # have drained pending changes. A VPS-local-only check would fire
    # immediately after an outbound scan finishes, stranding per-file rows
    # at "just now" while the user's machine was still downloading.
    try:
        from tools.syncthing import SyncthingClient as _St
        _st = _St()
        _reconcile_folders = (
            [{"id": target_folder_id}] if target_folder_id
            else _st.get_folders()
        )
        try:
            _connected = _st.get_connected_device_ids()
        except Exception:
            _connected = set()
        for _f in _reconcile_folders:
            _fid = _f.get("id")
            if not _fid:
                continue
            try:
                _info = _st.get_folder_sync_info(_fid, connected=_connected)
            except Exception:
                continue
            if _info["state"] == "idle":
                _n = buffer.mark_folder_all_done(_fid)
                if _n:
                    logger.info(
                        "[sync-events] Reconciled %d stale 'syncing' event(s) "
                        "in idle folder %s", _n, _fid,
                    )
    except Exception as _e:
        logger.debug("[sync-events] reconcile skipped: %s", _e)

    # Over-fetch so we still have `limit` matches after folder filtering
    fetch_limit = limit if not target_folder_id else max(limit * 3, 60)
    recent = buffer.get_recent(minutes=minutes, limit=fetch_limit)
    if target_folder_id:
        recent = [e for e in recent if e.folder_id == target_folder_id]
    recent = recent[:limit]

    events = []
    event_syncing = 0
    for e in recent:
        # Derive state from the progress/synced fields maintained by the watcher.
        # The watcher updates these in response to ItemStarted/ItemFinished/
        # DownloadProgress events — no more hardcoded 50%.
        if e.action == "deleted":
            state = "done"
            progress = None
        elif e.synced or e.progress == 100:
            state = "done"
            progress = None
        else:
            state = "syncing"
            progress = e.progress  # may be None when transfer hasn't reported yet
            event_syncing += 1

        events.append({
            "path": e.path,
            "abs_path": e.abs_path,
            "action": e.action,
            "direction": e.direction,
            "state": state,
            "progress": progress,
            "time_ago": _format_time_ago(e.timestamp),
            "timestamp": e.timestamp,
            "folder_id": e.folder_id,
        })

    # Folder-level pending count — covers files queued but not yet in the
    # event buffer (e.g. after a Syncthing restart). For each folder take
    # the larger side's need-items so both directions are represented:
    #   inbound  → VPS's own needFiles
    #   outbound → connected peer's needItems
    folder_pending = 0
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()
        try:
            connected = st.get_connected_device_ids()
        except Exception:
            connected = set()
        for f in st.get_folders():
            if target_folder_id and f.get("id") != target_folder_id:
                continue
            try:
                fs = st.get_folder_status(f["id"])
                self_need = fs.get("needFiles", 0) or 0
                peer_need = 0
                for dev in st.get_peer_device_ids(f["id"]):
                    if dev not in connected:
                        continue
                    try:
                        comp = st.get_completion(f["id"], dev)
                        peer_need += comp.get("needItems", 0) or 0
                    except Exception:
                        pass
                folder_pending += max(self_need, peer_need)
            except Exception:
                pass
    except Exception:
        pass

    # Event buffer and folder status describe the same pending files from two
    # angles — in the inbound case a RemoteChangeDetected event and VPS's
    # needFiles both count the upload. Take the larger so the badge never
    # double-counts (was showing 6 when the user uploaded 3 files).
    syncing_count = max(event_syncing, folder_pending)

    return {"events": events, "total_syncing": syncing_count}


# ══════════════════════════════════════════════
# RESTful: Automations (Cron Tasks + Triggers)
# ══════════════════════════════════════════════


# ── Available Channels ──

@app.get("/api/channels")
async def list_channels_api():
    """List all registered (active) channels.

    Used by the frontend to populate the notify_channels selector.
    Returns channel name and whether it has owner_native_id configured
    (needed for notification delivery).
    """
    from channels.registry import list_channels, get_channel

    result = []
    for name in list_channels():
        ch = get_channel(name)
        if ch:
            result.append({
                "name": name,
                "has_owner_id": bool(getattr(ch, 'owner_native_id', '')),
            })
    return {"channels": result}


# ── Scheduled Tasks (Cron) ──

@app.get("/api/tasks")
async def list_tasks_api():
    """List all scheduled (cron) tasks."""
    from automations.scheduler.store import list_tasks
    from automations.scheduler import scheduler as task_scheduler

    tasks = list_tasks()
    result = []
    for t in tasks:
        next_run = task_scheduler.get_next_run(t["id"])
        result.append({
            **t,
            "next_run_at": next_run,
        })
    return {"tasks": result, "total": len(result)}


@app.post("/api/tasks")
async def create_task_api(request: Request):
    """Create a new scheduled task.

    Body: {task_name, cron, task_prompt, channel_name?, model?, tool_access?, notify_channels?}
    """
    from automations.scheduler.store import create_task
    from automations.scheduler import scheduler as task_scheduler

    chunk = await request.json()
    body = chunk
    task_name = body.get("task_name", "").strip()
    cron = body.get("cron", "").strip()
    task_prompt = body.get("task_prompt", "").strip()
    channel_name = body.get("channel_name", "desktop")
    model = body.get("model", "").strip()
    tool_access = body.get("tool_access", "full")
    notify_channels = body.get("notify_channels")  # list[str] or None

    if not task_name:
        return JSONResponse({"error": "Missing 'task_name'"}, status_code=400)
    if not cron:
        return JSONResponse({"error": "Missing 'cron'"}, status_code=400)
    if not task_prompt:
        return JSONResponse({"error": "Missing 'task_prompt'"}, status_code=400)

    try:
        task = create_task(
            task_name=task_name,
            cron=cron,
            task_prompt=task_prompt,
            channel_user_id=DESKTOP_USER_ID,
            channel_name=channel_name,
            model=model,
            tool_access=tool_access,
            notify_channels=notify_channels,
        )
        task_scheduler.add_task(task)
        next_run = task_scheduler.get_next_run(task["id"])
        return {**task, "next_run_at": next_run}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/tasks/{task_id}")
async def get_task_api(task_id: str):
    """Get a single scheduled task by ID."""
    from automations.scheduler.store import get_task
    from automations.scheduler import scheduler as task_scheduler

    task = get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    next_run = task_scheduler.get_next_run(task_id)
    return {**task, "next_run_at": next_run}


@app.put("/api/tasks/{task_id}")
async def update_task_api(task_id: str, request: Request):
    """Update a scheduled task's fields.

    Body: {task_name?, cron?, task_prompt?, enabled?, model?, tool_access?, notify_channels?}
    """
    from automations.scheduler.store import get_task, update_task
    from automations.scheduler import scheduler as task_scheduler

    task = get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    body = await request.json()
    updates = {}
    for field in ("task_name", "cron", "task_prompt", "enabled", "model",
                  "tool_access", "notify_channels"):
        if field in body:
            updates[field] = body[field]

    if not updates:
        return JSONResponse({"error": "No fields to update"}, status_code=400)

    try:
        updated = update_task(task_id, **updates)
        if not updated:
            return JSONResponse({"error": "Update failed"}, status_code=500)

        # Sync with APScheduler
        if "cron" in updates or "enabled" in updates:
            task_scheduler.update_task_schedule(
                task_id,
                cron=updates.get("cron"),
                enabled=updates.get("enabled"),
            )

        next_run = task_scheduler.get_next_run(task_id)
        return {**updated, "next_run_at": next_run}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.patch("/api/tasks/{task_id}/toggle")
async def toggle_task_api(task_id: str):
    """Toggle a task's enabled/disabled state."""
    from automations.scheduler.store import get_task, update_task
    from automations.scheduler import scheduler as task_scheduler

    task = get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    new_enabled = not task.get("enabled", True)
    updated = update_task(task_id, enabled=new_enabled)
    task_scheduler.update_task_schedule(task_id, enabled=new_enabled)
    next_run = task_scheduler.get_next_run(task_id)
    return {**updated, "next_run_at": next_run}


@app.delete("/api/tasks/{task_id}")
async def delete_task_api(task_id: str):
    """Delete a scheduled task permanently."""
    from automations.scheduler.store import get_task, delete_task
    from automations.scheduler import scheduler as task_scheduler

    task = get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    task_scheduler.remove_task(task_id)
    delete_task(task_id)
    return {"message": f"Task '{task_id}' deleted", "id": task_id}


@app.post("/api/tasks/{task_id}/run")
async def run_task_now_api(task_id: str):
    """Manually trigger a task to run now (one-off execution)."""
    from automations.scheduler.store import get_task
    from automations.scheduler.executor import execute_task

    task = get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    execute_task(task)
    return {"message": f"Task '{task_id}' triggered", "id": task_id}


# ── Composio Triggers ──

@app.get("/api/triggers")
async def list_triggers_api():
    """List all trigger recipes (local) merged with Composio active status."""
    from automations.composio_triggers import _load_recipes, is_enabled

    if not is_enabled():
        return {"triggers": [], "total": 0, "composio_enabled": False}

    recipes = _load_recipes()

    # Try to get live status from Composio
    active_map = {}
    try:
        from composio import Composio
        client = Composio()
        active_resp = client.triggers.list_active()
        for t in getattr(active_resp, 'items', active_resp):
            tid = getattr(t, "id", None) or getattr(t, "trigger_id", str(t))
            active_map[tid] = {
                "status": getattr(t, "status", "ACTIVE"),
                "created_at_remote": getattr(t, "created_at", ""),
            }
    except Exception as e:
        logger.warning("Could not fetch active triggers from Composio: %s", e)

    result = []
    for trigger_id, recipe in recipes.items():
        info = active_map.get(trigger_id, {})
        result.append({
            "id": trigger_id,
            "trigger_id": trigger_id,
            "trigger_slug": recipe.get("trigger_slug", ""),
            "agent_prompt": recipe.get("agent_prompt", ""),
            "model": recipe.get("model", ""),
            "trigger_config": recipe.get("trigger_config"),
            "channel_user_id": recipe.get("channel_user_id", recipe.get("user_id", "")),
            "channel_name": recipe.get("channel_name", ""),
            "tool_access": recipe.get("tool_access", "full"),
            "created_at": recipe.get("created_at", ""),
            "enabled": trigger_id in active_map,
            "status": info.get("status", "UNKNOWN"),
        })

    return {"triggers": result, "total": len(result), "composio_enabled": True}


@app.get("/api/triggers/{trigger_id}")
async def get_trigger_api(trigger_id: str):
    """Get a single trigger recipe's details."""
    from automations.composio_triggers import _load_recipes

    recipes = _load_recipes()
    recipe = recipes.get(trigger_id)
    if not recipe:
        return JSONResponse({"error": "Trigger not found"}, status_code=404)

    return {
        "id": trigger_id,
        "trigger_id": trigger_id,
        "trigger_slug": recipe.get("trigger_slug", ""),
        "agent_prompt": recipe.get("agent_prompt", ""),
        "model": recipe.get("model", ""),
        "trigger_config": recipe.get("trigger_config"),
        "channel_user_id": recipe.get("channel_user_id", recipe.get("user_id", "")),
        "channel_name": recipe.get("channel_name", ""),
        "tool_access": recipe.get("tool_access", "full"),
        "created_at": recipe.get("created_at", ""),
    }

@app.post("/api/triggers")
async def create_trigger_api(request: Request):
    """Create a new trigger recipe.

    Body: {trigger_slug, agent_prompt, trigger_config?, model?, tool_access?, channel_name?}
    """
    from automations.composio_triggers import create_trigger, is_enabled

    if not is_enabled():
        return JSONResponse({"error": "Composio not enabled"}, status_code=400)

    body = await request.json()
    trigger_slug = body.get("trigger_slug", "").strip()
    agent_prompt = body.get("agent_prompt", "").strip()
    trigger_config = body.get("trigger_config", {})
    model = body.get("model", "").strip()
    tool_access = body.get("tool_access", "full")
    # We implicitly set channel_name via the execute context normally, 
    # but here we can just set it during creation. 
    # Let's import context.set_context just inside to mock context if missing.

    if not trigger_slug:
        return JSONResponse({"error": "Missing 'trigger_slug'"}, status_code=400)

    from context import set_context, clear_context
    set_context(user_id=DESKTOP_USER_ID, channel_name=body.get("channel_name", "desktop"))
    try:
        import json
        result_str = create_trigger(
            trigger_slug=trigger_slug,
            agent_prompt=agent_prompt,
            trigger_config=trigger_config,
            model=model,
            tool_access=tool_access
        )
        clear_context()
        result = json.loads(result_str)
        if result.get("success"):
            return result
        else:
            return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)
    except Exception as e:
        clear_context()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/api/triggers/{trigger_id}")
async def update_trigger_api(trigger_id: str, request: Request):
    """Update a trigger recipe's editable fields.

    Body: {agent_prompt?, model?, channel_name?}
    Note: trigger_slug and trigger_config cannot be changed after creation.
    """
    from automations.composio_triggers import _load_recipes, _save_recipes

    recipes = _load_recipes()
    recipe = recipes.get(trigger_id)
    if not recipe:
        return JSONResponse({"error": "Trigger not found"}, status_code=404)

    body = await request.json()
    changed = False
    for field in ("agent_prompt", "model", "channel_name", "tool_access"):
        if field in body:
            recipe[field] = body[field]
            changed = True

    if not changed:
        return JSONResponse({"error": "No fields to update"}, status_code=400)

    recipes[trigger_id] = recipe
    _save_recipes(recipes)

    return {
        "id": trigger_id,
        **recipe,
        "message": "Trigger recipe updated",
    }


@app.patch("/api/triggers/{trigger_id}/toggle")
async def toggle_trigger_api(trigger_id: str):
    """Enable or disable a trigger on Composio.

    Disabling removes the trigger from Composio (stops events).
    Enabling re-creates it with the stored recipe config.
    """
    from automations.composio_triggers import _load_recipes, _save_recipes, is_enabled as composio_enabled

    if not composio_enabled():
        return JSONResponse({"error": "Composio not enabled"}, status_code=400)

    recipes = _load_recipes()
    recipe = recipes.get(trigger_id)
    # Auto-adopt orphan triggers (active on Composio but no local recipe)
    if not recipe:
        recipe = _adopt_orphan_trigger(trigger_id, recipes)
    if not recipe:
        return JSONResponse({"error": "Trigger recipe not found"}, status_code=404)

    # Check current active status
    is_active = False
    try:
        from composio import Composio
        client = Composio()
        active_resp = client.triggers.list_active()
        active_ids = {getattr(t, "id", None) or getattr(t, "trigger_id", str(t)) for t in getattr(active_resp, 'items', active_resp)}
        is_active = trigger_id in active_ids
    except Exception as e:
        logger.warning("Could not check trigger status: %s", e)

    if is_active:
        # Disable: remove from Composio
        try:
            from composio import Composio
            client = Composio()
            client.triggers.disable(trigger_id=trigger_id)
            return {
                "id": trigger_id,
                "enabled": False,
                "message": f"Trigger {trigger_id} disabled",
            }
        except Exception as e:
            return JSONResponse({"error": f"Failed to disable: {str(e)}"}, status_code=500)
    else:
        # Enable: re-create on Composio with stored config
        try:
            from composio import Composio
            from config import COMPOSIO_USER_ID
            client = Composio()
            # Use composio_user_id (Composio-side), NOT user_id (channel-side)
            # Fall back to COMPOSIO_USER_ID for old recipes without this field
            cid = recipe.get("composio_user_id") or COMPOSIO_USER_ID
            trigger = client.triggers.create(
                slug=recipe["trigger_slug"],
                user_id=cid,
                trigger_config=recipe.get("trigger_config") or {},
            )
            new_id = getattr(trigger, "id", None) or getattr(trigger, "trigger_id", str(trigger))

            # If Composio assigned a new ID, migrate the recipe
            if new_id != trigger_id:
                recipes[new_id] = recipe
                recipes[new_id]["trigger_id"] = new_id
                del recipes[trigger_id]
                _save_recipes(recipes)

            return {
                "id": new_id,
                "enabled": True,
                "message": f"Trigger re-enabled as {new_id}",
            }
        except Exception as e:
            err_msg = str(e)
            # Surface connection issues clearly — this is user-actionable, not a server bug
            if "connected account" in err_msg.lower() or "no connected" in err_msg.lower():
                return JSONResponse({
                    "error": f"Connected account missing or expired for {recipe.get('trigger_slug', '')}",
                    "error_code": "connection_missing",
                    "details": err_msg,
                    "trigger_slug": recipe.get("trigger_slug", ""),
                }, status_code=400)
            return JSONResponse({"error": f"Failed to enable: {err_msg}"}, status_code=500)


@app.delete("/api/triggers/{trigger_id}")
async def delete_trigger_api(trigger_id: str):
    """Delete a trigger: disable on Composio + remove local recipe."""
    from automations.composio_triggers import _load_recipes, _save_recipes, is_enabled as composio_enabled

    recipes = _load_recipes()
    has_local = trigger_id in recipes

    # Try to disable on Composio (best-effort — works for orphan triggers too)
    disabled_on_cloud = False
    if composio_enabled():
        try:
            from composio import Composio
            client = Composio()
            client.triggers.disable(trigger_id=trigger_id)
            disabled_on_cloud = True
        except Exception as e:
            logger.warning("Could not disable trigger on Composio: %s", e)

    # Remove local recipe if it exists
    if has_local:
        del recipes[trigger_id]
        _save_recipes(recipes)

    if not has_local and not disabled_on_cloud:
        return JSONResponse({"error": "Trigger not found"}, status_code=404)

    return {"message": f"Trigger '{trigger_id}' deleted", "id": trigger_id}


# ── Unified Automations (combined view) ──

@app.get("/api/automations")
async def list_automations_api():
    """Unified view: returns ALL cron tasks + trigger recipes + file-drop rules in a consistent shape.

    Each item has:
      - id: unique identifier
      - type: "cron" | "trigger" | "file_drop"
      - name: human-readable name
      - description: what this automation does (prompt preview)
      - schedule: cron expression (cron), trigger_slug (trigger), or path_pattern (file_drop)
      - enabled: bool
      - model: LLM model override (empty = default)
      - channel_name: delivery channel
      - created_at: ISO timestamp
      - last_run_at: ISO timestamp or null (cron only)
      - next_run_at: ISO timestamp or null (cron only)
      - config: type-specific full config
    """
    from automations.scheduler.store import list_tasks
    from automations.scheduler import scheduler as task_scheduler
    from automations.composio_triggers import _load_recipes, is_enabled as composio_enabled

    items = []

    # ── Cron tasks ──
    for t in list_tasks():
        next_run = task_scheduler.get_next_run(t["id"])
        items.append({
            "id": t["id"],
            "type": "cron",
            "name": t["task_name"],
            "description": t["task_prompt"][:200],
            "schedule": t["cron"],
            "enabled": t.get("enabled", True),
            "model": t.get("model", ""),
            "tool_access": t.get("tool_access", "full"),
            "channel_name": t.get("channel_name", ""),
            "channel_user_id": t.get("channel_user_id", t.get("user_id", "")),
            "notify_channels": t.get("notify_channels"),
            "created_at": t.get("created_at", ""),
            "last_run_at": t.get("last_run_at"),
            "last_result": (t.get("last_result") or "")[:200],
            "next_run_at": next_run,
            "config": t,  # full task data
        })

    # ── Triggers ──
    if composio_enabled():
        recipes = _load_recipes()
        # Build active trigger map with full details (not just IDs)
        active_map = {}
        try:
            from composio import Composio
            client = Composio()
            active_resp = client.triggers.list_active()
            for t in getattr(active_resp, 'items', active_resp):
                tid = getattr(t, "id", None) or getattr(t, "trigger_id", str(t))
                active_map[tid] = {
                    "trigger_slug": getattr(t, "trigger_slug", getattr(t, "slug", "")),
                    "status": getattr(t, "status", "ACTIVE"),
                    "created_at": getattr(t, "created_at", ""),
                }
        except Exception:
            pass

        # Local recipes (with cloud active status)
        for trigger_id, recipe in recipes.items():
            items.append({
                "id": trigger_id,
                "type": "trigger",
                "name": recipe.get("trigger_slug", ""),
                "description": (recipe.get("agent_prompt") or "")[:200],
                "schedule": recipe.get("trigger_slug", ""),
                "enabled": trigger_id in active_map,
                "model": recipe.get("model", ""),
                "tool_access": recipe.get("tool_access", "full"),
                "channel_name": recipe.get("channel_name", ""),
                "channel_user_id": recipe.get("channel_user_id", recipe.get("user_id", "")),
                "created_at": recipe.get("created_at", ""),
                "last_run_at": None,
                "last_result": "",
                "next_run_at": None,
                "config": recipe,  # full recipe
            })

        # Orphan triggers: active on Composio cloud but no local recipe
        for tid, info in active_map.items():
            if tid not in recipes:
                slug = info.get("trigger_slug", "Unknown Trigger")
                items.append({
                    "id": tid,
                    "type": "trigger",
                    "name": slug,
                    "description": "\u26a0\ufe0f Active on Composio cloud (no local configuration)",
                    "schedule": slug,
                    "enabled": True,
                    "model": "",
                    "tool_access": "full",
                    "channel_name": "",
                    "channel_user_id": "",
                    "created_at": info.get("created_at", ""),
                    "last_run_at": None,
                    "last_result": "",
                    "next_run_at": None,
                    "config": {"trigger_slug": slug, "orphan": True},
                })

    # ── File-drop rules ──
    try:
        from automations.file_drop import list_rules
        for r in list_rules():
            items.append({
                "id": r["id"],
                "type": "file_drop",
                "name": r["name"],
                "description": (r.get("agent_prompt") or "")[:200],
                "schedule": r["path_pattern"],  # "schedule" field doubles as pattern for file_drop
                "enabled": r.get("enabled", True),
                "model": r.get("model", ""),
                "tool_access": r.get("tool_access", "full"),
                "channel_name": r.get("channel_name", ""),
                "channel_user_id": r.get("channel_user_id", ""),
                "created_at": r.get("created_at", ""),
                "last_run_at": None,
                "last_result": "",
                "next_run_at": None,
                "config": r,  # full rule
            })
    except Exception as e:
        logger.warning("Failed to load file-drop rules for automations: %s", e)

    # Sort by created_at descending
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return {
        "automations": items,
        "total": len(items),
        "counts": {
            "cron": sum(1 for x in items if x["type"] == "cron"),
            "trigger": sum(1 for x in items if x["type"] == "trigger"),
            "file_drop": sum(1 for x in items if x["type"] == "file_drop"),
        },
    }


# ── Helper: detect automation type from ID prefix ──

def _detect_automation_type(automation_id: str) -> str:
    """Detect automation type from ID prefix: fd_ → file_drop, ti_ → trigger, else → cron."""
    if automation_id.startswith("fd_"):
        return "file_drop"
    if automation_id.startswith("ti_"):
        return "trigger"
    return "cron"


def _adopt_orphan_trigger(trigger_id: str, recipes: dict):
    """Check if a trigger exists on Composio cloud and auto-create a local recipe.

    Used to "adopt" orphan triggers that are active on Composio but have
    no local recipe file (e.g., created via another channel or lost data).
    Mutates `recipes` dict in place and persists to disk.
    Returns the new recipe dict, or None if not found on cloud.
    """
    try:
        from composio import Composio
        from automations.composio_triggers import _save_recipes
        client = Composio()
        active_resp = client.triggers.list_active()
        for t in getattr(active_resp, 'items', active_resp):
            tid = getattr(t, "id", None) or getattr(t, "trigger_id", str(t))
            if tid == trigger_id:
                slug = getattr(t, "trigger_slug", getattr(t, "slug", "unknown"))
                recipe = {
                    "trigger_id": trigger_id,
                    "trigger_slug": slug,
                    "agent_prompt": "",
                    "model": "",
                    "tool_access": "full",
                    "trigger_config": {},
                    "channel_name": "desktop",
                    "channel_user_id": DESKTOP_USER_ID,
                    "created_at": getattr(t, "created_at", ""),
                }
                recipes[trigger_id] = recipe
                _save_recipes(recipes)
                logger.info("Auto-adopted orphan trigger: %s (%s)", trigger_id, slug)
                return recipe
    except Exception as e:
        logger.warning("Could not adopt orphan trigger %s: %s", trigger_id, e)
    return None


@app.post("/api/automations")
async def create_automation_api(request: Request):
    """Unified creation endpoint. Body must include `type` field.

    For type='cron':      {type, task_name, cron, task_prompt, model?, tool_access?}
    For type='trigger':   {type, trigger_slug, agent_prompt, trigger_config?, model?, tool_access?}
    For type='file_drop': {type, name, path_pattern, agent_prompt, file_actions?, model?, tool_access?}
    """
    body = await request.json()
    atype = body.get("type", "").strip()

    if not atype:
        return JSONResponse({"error": "Missing 'type' field (cron | trigger | file_drop)"}, status_code=400)

    if atype == "cron":
        # Delegate to existing create task logic
        from automations.scheduler.store import create_task
        from automations.scheduler import scheduler as task_scheduler

        task_name = body.get("task_name", body.get("name", "")).strip()
        cron = body.get("cron", body.get("schedule", "")).strip()
        task_prompt = body.get("task_prompt", body.get("prompt", "")).strip()
        model = body.get("model", "").strip()
        tool_access = body.get("tool_access", "full")

        if not task_name:
            return JSONResponse({"error": "Missing 'task_name'"}, status_code=400)
        if not cron:
            return JSONResponse({"error": "Missing 'cron'"}, status_code=400)
        if not task_prompt:
            return JSONResponse({"error": "Missing 'task_prompt'"}, status_code=400)

        try:
            task = create_task(
                task_name=task_name, cron=cron, task_prompt=task_prompt,
                channel_user_id=DESKTOP_USER_ID, channel_name="desktop",
                model=model, tool_access=tool_access,
            )
            task_scheduler.add_task(task)
            next_run = task_scheduler.get_next_run(task["id"])
            return {**task, "type": "cron", "next_run_at": next_run}
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    elif atype == "trigger":
        # Delegate to existing create trigger logic
        from automations.composio_triggers import create_trigger, is_enabled as composio_enabled
        import json as _json

        if not composio_enabled():
            return JSONResponse({"error": "Composio not enabled"}, status_code=400)

        trigger_slug = body.get("trigger_slug", "").strip()
        agent_prompt = body.get("agent_prompt", body.get("prompt", "")).strip()
        trigger_config = body.get("trigger_config", {})
        model = body.get("model", "").strip()
        tool_access = body.get("tool_access", "full")

        if not trigger_slug:
            return JSONResponse({"error": "Missing 'trigger_slug'"}, status_code=400)

        from context import set_context, clear_context
        set_context(user_id=DESKTOP_USER_ID, channel_name="desktop")
        try:
            result_str = create_trigger(
                trigger_slug=trigger_slug, agent_prompt=agent_prompt,
                trigger_config=trigger_config, model=model, tool_access=tool_access,
            )
            clear_context()
            result = _json.loads(result_str)
            if result.get("success"):
                result["type"] = "trigger"
                return result
            return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=400)
        except Exception as e:
            clear_context()
            return JSONResponse({"error": str(e)}, status_code=500)

    elif atype == "file_drop":
        from automations.file_drop import create_rule

        name = body.get("name", "").strip()
        path_pattern = body.get("path_pattern", body.get("schedule", "")).strip()
        agent_prompt = body.get("agent_prompt", body.get("prompt", "")).strip()
        file_actions = body.get("file_actions", body.get("actions"))
        model = body.get("model", "").strip()
        tool_access = body.get("tool_access", "full")

        if not name:
            return JSONResponse({"error": "Missing 'name'"}, status_code=400)
        if not path_pattern:
            return JSONResponse({"error": "Missing 'path_pattern'"}, status_code=400)
        if not agent_prompt:
            return JSONResponse({"error": "Missing 'agent_prompt'"}, status_code=400)

        rule = create_rule(
            name=name, path_pattern=path_pattern, agent_prompt=agent_prompt,
            channel_user_id=DESKTOP_USER_ID, channel_name="desktop",
            model=model, tool_access=tool_access, actions=file_actions,
        )
        return {**rule, "type": "file_drop"}

    else:
        return JSONResponse({"error": f"Unknown type: '{atype}'. Use 'cron', 'trigger', or 'file_drop'."}, status_code=400)


@app.get("/api/automations/{automation_id}")
async def get_automation_api(automation_id: str):
    """Get a single automation by ID (auto-detects type from ID prefix)."""
    atype = _detect_automation_type(automation_id)

    if atype == "cron":
        from automations.scheduler.store import get_task
        from automations.scheduler import scheduler as task_scheduler
        task = get_task(automation_id)
        if not task:
            return JSONResponse({"error": "Cron task not found"}, status_code=404)
        next_run = task_scheduler.get_next_run(automation_id)
        return {**task, "type": "cron", "next_run_at": next_run}

    elif atype == "trigger":
        from automations.composio_triggers import _load_recipes
        recipes = _load_recipes()
        recipe = recipes.get(automation_id)
        if not recipe:
            return JSONResponse({"error": "Trigger not found"}, status_code=404)
        return {"id": automation_id, "type": "trigger", **recipe}

    elif atype == "file_drop":
        from automations.file_drop import get_rule
        rule = get_rule(automation_id)
        if not rule:
            return JSONResponse({"error": "File-drop rule not found"}, status_code=404)
        return {**rule, "type": "file_drop"}


@app.put("/api/automations/{automation_id}")
async def update_automation_api(automation_id: str, request: Request):
    """Update an automation's editable fields by ID (auto-detects type).

    Body: {name?, prompt/task_prompt/agent_prompt?, cron?, model?, tool_access?, enabled?, path_pattern?, file_actions?}
    """
    body = await request.json()
    atype = _detect_automation_type(automation_id)

    if atype == "cron":
        from automations.scheduler.store import get_task, update_task
        from automations.scheduler import scheduler as task_scheduler

        task = get_task(automation_id)
        if not task:
            return JSONResponse({"error": "Cron task not found"}, status_code=404)

        updates = {}
        for field in ("task_name", "cron", "task_prompt", "enabled", "model", "tool_access"):
            if field in body:
                updates[field] = body[field]
        # Accept unified field names too
        if "name" in body and "task_name" not in body:
            updates["task_name"] = body["name"]
        if "prompt" in body and "task_prompt" not in body:
            updates["task_prompt"] = body["prompt"]

        if not updates:
            return JSONResponse({"error": "No fields to update"}, status_code=400)

        try:
            updated = update_task(automation_id, **updates)
            if not updated:
                return JSONResponse({"error": "Update failed"}, status_code=500)
            if "cron" in updates or "enabled" in updates:
                task_scheduler.update_task_schedule(
                    automation_id, cron=updates.get("cron"), enabled=updates.get("enabled"),
                )
            next_run = task_scheduler.get_next_run(automation_id)
            return {**updated, "type": "cron", "next_run_at": next_run}
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    elif atype == "trigger":
        from automations.composio_triggers import _load_recipes, _save_recipes
        recipes = _load_recipes()
        recipe = recipes.get(automation_id)
        # Auto-adopt orphan trigger on first edit
        if not recipe:
            recipe = _adopt_orphan_trigger(automation_id, recipes)
        if not recipe:
            return JSONResponse({"error": "Trigger not found"}, status_code=404)

        changed = False
        for field in ("agent_prompt", "model", "channel_name", "tool_access"):
            if field in body:
                recipe[field] = body[field]
                changed = True
        # Accept unified field names
        if "prompt" in body and "agent_prompt" not in body:
            recipe["agent_prompt"] = body["prompt"]
            changed = True

        if not changed:
            return JSONResponse({"error": "No fields to update"}, status_code=400)

        recipes[automation_id] = recipe
        _save_recipes(recipes)
        return {"id": automation_id, "type": "trigger", **recipe, "message": "Updated"}

    elif atype == "file_drop":
        from automations.file_drop import get_rule, update_rule

        rule = get_rule(automation_id)
        if not rule:
            return JSONResponse({"error": "File-drop rule not found"}, status_code=404)

        updates = {}
        for field in ("name", "path_pattern", "agent_prompt", "model", "tool_access", "actions", "enabled"):
            if field in body:
                updates[field] = body[field]
        # Accept unified field names
        if "prompt" in body and "agent_prompt" not in body:
            updates["agent_prompt"] = body["prompt"]
        if "file_actions" in body and "actions" not in body:
            updates["actions"] = body["file_actions"]

        if not updates:
            return JSONResponse({"error": "No fields to update"}, status_code=400)

        updated = update_rule(automation_id, **updates)
        if not updated:
            return JSONResponse({"error": "Update failed"}, status_code=500)
        return {**updated, "type": "file_drop", "message": "Updated"}


@app.patch("/api/automations/{automation_id}/toggle")
async def toggle_automation_api(automation_id: str):
    """Toggle an automation's enabled/disabled state by ID (auto-detects type)."""
    atype = _detect_automation_type(automation_id)

    if atype == "cron":
        from automations.scheduler.store import get_task, update_task
        from automations.scheduler import scheduler as task_scheduler

        task = get_task(automation_id)
        if not task:
            return JSONResponse({"error": "Cron task not found"}, status_code=404)

        new_enabled = not task.get("enabled", True)
        updated = update_task(automation_id, enabled=new_enabled)
        task_scheduler.update_task_schedule(automation_id, enabled=new_enabled)
        next_run = task_scheduler.get_next_run(automation_id)
        return {**updated, "type": "cron", "next_run_at": next_run}

    elif atype == "trigger":
        # Reuse the existing toggle endpoint logic
        return await toggle_trigger_api(automation_id)

    elif atype == "file_drop":
        from automations.file_drop import get_rule, update_rule

        rule = get_rule(automation_id)
        if not rule:
            return JSONResponse({"error": "File-drop rule not found"}, status_code=404)

        new_enabled = not rule.get("enabled", True)
        updated = update_rule(automation_id, enabled=new_enabled)
        return {**updated, "type": "file_drop"}


@app.delete("/api/automations/{automation_id}")
async def delete_automation_api(automation_id: str):
    """Delete an automation by ID (auto-detects type)."""
    atype = _detect_automation_type(automation_id)

    if atype == "cron":
        from automations.scheduler.store import get_task, delete_task
        from automations.scheduler import scheduler as task_scheduler

        task = get_task(automation_id)
        if not task:
            return JSONResponse({"error": "Cron task not found"}, status_code=404)
        task_scheduler.remove_task(automation_id)
        delete_task(automation_id)
        return {"message": f"Cron task '{automation_id}' deleted", "id": automation_id, "type": "cron"}

    elif atype == "trigger":
        # Reuse existing delete logic
        return await delete_trigger_api(automation_id)

    elif atype == "file_drop":
        from automations.file_drop import get_rule, delete_rule

        rule = get_rule(automation_id)
        if not rule:
            return JSONResponse({"error": "File-drop rule not found"}, status_code=404)
        delete_rule(automation_id)
        return {"message": f"File-drop rule '{automation_id}' deleted", "id": automation_id, "type": "file_drop"}


@app.post("/api/automations/{automation_id}/run")
async def run_automation_api(automation_id: str):
    """Manually trigger a cron automation to run now. Only works for cron type."""
    atype = _detect_automation_type(automation_id)
    if atype != "cron":
        return JSONResponse({"error": f"Manual run is only available for cron automations, not {atype}"}, status_code=400)

    from automations.scheduler.store import get_task
    from automations.scheduler.executor import execute_task

    task = get_task(automation_id)
    if not task:
        return JSONResponse({"error": "Cron task not found"}, status_code=404)

    execute_task(task)
    return {"message": f"Task '{automation_id}' triggered", "id": automation_id}


# ── Port cleanup ──

def _kill_stale_port_holder(port: int) -> None:
    """Kill any stale process occupying the given port.

    This prevents the recurring 'Address already in use' crash loop
    caused by orphaned desktop-agent processes surviving service restarts.
    Only kills processes whose name contains 'desktop-agent' or 'python'
    to avoid accidentally killing unrelated services.
    """
    import signal
    import subprocess

    try:
        # Use ss to find the PID listening on the port
        result = subprocess.run(
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return

        my_pid = os.getpid()
        for line in result.stdout.splitlines():
            if "LISTEN" not in line:
                continue
            # Extract pid from patterns like pid=12345
            import re
            for match in re.finditer(r"pid=(\d+)", line):
                pid = int(match.group(1))
                if pid == my_pid:
                    continue
                # Safety: only kill desktop-agent / python processes
                try:
                    cmdline = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace")
                except (FileNotFoundError, PermissionError):
                    continue
                if "desktop-agent" in cmdline or "desktop" in cmdline:
                    logger.warning(
                        f"Killing stale process PID {pid} occupying port {port}"
                    )
                    os.kill(pid, signal.SIGTERM)
                    # Give it a moment to exit gracefully
                    import time
                    time.sleep(1)
                    # Force kill if still alive
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass  # Already dead
                else:
                    logger.error(
                        f"Port {port} is occupied by non-desktop process PID {pid} "
                        f"— refusing to kill. Please free the port manually."
                    )
    except Exception as e:
        logger.warning(f"Port cleanup check failed (non-fatal): {e}")


# ── Entry point ──

def main():
    import atexit
    import uvicorn
    from logger import recover_orphaned_logs
    from automations.scheduler import scheduler

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Recover any orphaned log files from previous crashes
    recover_orphaned_logs()
    # Ensure all sessions are closed on shutdown
    atexit.register(sessions.close_all)
    atexit.register(scheduler.stop)

    register_channel(channel)

    # Start scheduler (reloads persisted tasks)
    scheduler.start()

    # Start Composio trigger listener (if enabled)
    from automations.composio_triggers import start_listener as start_trigger_listener
    start_trigger_listener()

    # Kill any stale process occupying the port before binding
    _kill_stale_port_holder(DESKTOP_PORT)

    print("=" * 50)
    print(f"🖥️  Desktop Channel started on http://0.0.0.0:{DESKTOP_PORT}")
    print(f"   Model: {MODEL}")
    print(f"   API endpoints:")
    print(f"     POST /api/chat                     — send message (SSE)")
    print(f"     GET  /api/status                   — session status")
    print(f"     GET  /api/sessions                 — list sessions")
    print(f"     POST /api/sessions                 — new session")
    print(f"     GET  /api/sessions/current          — current session detail")
    print(f"     PUT  /api/sessions/:id/switch       — switch session")
    print(f"     DELETE /api/sessions/:id             — delete session")
    print(f"     GET  /api/workspaces                — list workspaces")
    print(f"     POST /api/workspaces                — create workspace")
    print(f"     PATCH /api/workspaces/:id            — edit workspace (label/default)")
    print(f"     DELETE /api/workspaces/:id           — delete workspace")
    print(f"     GET  /api/models                   — list models")
    print(f"     PUT  /api/models/current            — switch model")
    print(f"     GET  /api/notifications             — notification list")
    print(f"     GET  /api/notifications/stream      — SSE notification push")
    print(f"     --- Unified Automations ---")
    print(f"     GET  /api/automations               — list all (cron+trigger+file_drop)")
    print(f"     POST /api/automations               — create (type: cron|trigger|file_drop)")
    print(f"     GET  /api/automations/:id           — get by ID")
    print(f"     PUT  /api/automations/:id           — update by ID")
    print(f"     PATCH /api/automations/:id/toggle    — toggle enable/disable")
    print(f"     DELETE /api/automations/:id          — delete by ID")
    print(f"     POST /api/automations/:id/run        — run now (cron only)")
    print(f"     --- Legacy (backward compat) ---")
    print(f"     /api/tasks/*                        — cron CRUD")
    print(f"     /api/triggers/*                     — trigger CRUD")
    print("=" * 50)

    uvicorn.run(app, host="0.0.0.0", port=DESKTOP_PORT, log_level="info")


if __name__ == "__main__":
    main()
