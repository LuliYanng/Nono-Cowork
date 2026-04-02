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
from session import sessions, _serialize_history
from config import MODEL, MODEL_POOL, CONTEXT_LIMIT, OWNER_USER_ID

logger = logging.getLogger("channel.desktop")

# Desktop channel uses the unified owner ID (shared with other channels)
DESKTOP_USER_ID = OWNER_USER_ID
DESKTOP_PORT = int(os.getenv("DESKTOP_PORT", "8080"))
DESKTOP_API_TOKEN = os.getenv("DESKTOP_API_TOKEN", "")


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

    def dispatch_and_stream(self, user_id: str, user_text: str):
        """Dispatch the message and signal 'done' when the agent finishes.

        This overrides the base dispatch to add a 'done' event at the end
        and to capture on_event callbacks for richer SSE streaming.
        """
        from agent_runner import run_agent_for_message

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

    stats = info["token_stats"]
    pt = stats.get("last_prompt_tokens", 0)
    pct = min(pt / CONTEXT_LIMIT * 100, 100) if CONTEXT_LIMIT else 0

    return {
        "active": True,
        "model": info.get("model_override") or MODEL,
        "history_len": info["history_len"],
        "api_calls": stats["total_api_calls"],
        "context_pct": round(pct, 1),
        "prompt_tokens": pt,
        "context_limit": CONTEXT_LIMIT,
        "total_tokens": stats["total_tokens"],
        "total_prompt_tokens": stats["total_prompt_tokens"],
        "total_completion_tokens": stats["total_completion_tokens"],
        "total_cached_tokens": stats["total_cached_tokens"],
        "is_running": info["is_running"],
    }


@app.post("/api/chat")
async def chat(request: Request):
    """Send a message and stream back events via SSE.

    Request body: {"message": "user text here"}
    Returns: SSE stream with events: status, reply, done
    """
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)

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
async def list_sessions():
    """List all saved sessions for the desktop user."""
    saved = sessions.list_sessions(DESKTOP_USER_ID)

    # Mark which session is currently active
    status = sessions.get_status(DESKTOP_USER_ID)
    active_id = status["session_id"] if status else None

    for s in saved:
        s["is_current"] = (s["id"] == active_id)

    return {"sessions": saved}


@app.post("/api/sessions")
async def create_session():
    """Archive the current session and start a new one."""
    sessions.reset(DESKTOP_USER_ID)
    new_status = sessions.get_status(DESKTOP_USER_ID)
    return {
        "session_id": new_status["session_id"] if new_status else None,
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
    """Get the list of available models and the currently active model."""
    current = sessions.get_model(DESKTOP_USER_ID) or MODEL
    return {
        "current": current,
        "default": MODEL,
        "available": list(MODEL_POOL),
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
    from notifications import notification_store

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
        agent_provider="gemini-cli",
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
        agent_provider="gemini-cli",
        agent_duration_s=8.2,
        token_stats={"total_tokens": 1500},
    )

    return {"ok": True, "injected": 3, "message": "3 mock notifications created"}

@app.get("/api/notifications")
async def list_notifications(
    status: str = None, limit: int = 50, offset: int = 0
):
    """List notifications for the desktop user."""
    from notifications import notification_store
    notifs, total = notification_store.list(
        DESKTOP_USER_ID, status=status, limit=limit, offset=offset,
    )
    unread = notification_store.unread_count(DESKTOP_USER_ID)
    return {"notifications": notifs, "total": total, "unread": unread}


@app.get("/api/notifications/unread-count")
async def notifications_unread_count():
    """Get the unread notification count (for badges)."""
    from notifications import notification_store
    return {"count": notification_store.unread_count(DESKTOP_USER_ID)}


@app.get("/api/notifications/stream")
async def notifications_stream():
    """SSE stream for real-time notification push.

    Unlike /api/chat SSE (per-request, closes on 'done'), this is a
    persistent connection that stays open while the frontend is active.
    New notifications are pushed as 'new_notification' events.
    """
    from notifications import notification_store

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
    from notifications import notification_store
    notif = notification_store.get(notification_id)
    if not notif:
        return JSONResponse({"error": "Notification not found"}, status_code=404)
    return notif


@app.put("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str):
    """Mark a notification as read."""
    from notifications import notification_store
    ok = notification_store.mark_read(notification_id)
    if not ok:
        return JSONResponse({"error": "Notification not found"}, status_code=404)
    return {"message": "Marked as read"}


@app.put("/api/notifications/read-all")
async def mark_all_notifications_read():
    """Mark all notifications as read."""
    from notifications import notification_store
    count = notification_store.mark_all_read(DESKTOP_USER_ID)
    return {"message": f"Marked {count} notifications as read", "count": count}


@app.delete("/api/notifications/{notification_id}")
async def delete_notification(notification_id: str):
    """Delete a notification and its autonomous session."""
    from notifications import notification_store
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
    from notifications import notification_store

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
    from notifications import notification_store
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
    (e.g., /root/SyncFromLocal/inbox/report.pdf) to user-local paths
    (e.g., D:\\Sync\\inbox\\report.pdf).  This endpoint provides the
    VPS-side folder roots so the frontend can compute the relative path.
    """
    try:
        from tools.syncthing import SyncthingClient
        st = SyncthingClient()
        folders = st.get_folders()
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


# ══════════════════════════════════════════════
# RESTful: Automations (Cron Tasks + Triggers)
# ══════════════════════════════════════════════


# ── Scheduled Tasks (Cron) ──

@app.get("/api/tasks")
async def list_tasks_api():
    """List all scheduled (cron) tasks."""
    from scheduler.store import list_tasks
    from scheduler import scheduler as task_scheduler

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

    Body: {task_name, cron, task_prompt, channel_name?}
    """
    from scheduler.store import create_task
    from scheduler import scheduler as task_scheduler

    chunk = await request.json()
    body = chunk
    task_name = body.get("task_name", "").strip()
    cron = body.get("cron", "").strip()
    task_prompt = body.get("task_prompt", "").strip()
    channel_name = body.get("channel_name", "desktop")
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
            task_name=task_name,
            cron=cron,
            task_prompt=task_prompt,
            user_id=DESKTOP_USER_ID,
            channel_name=channel_name,
            model=model,
            tool_access=tool_access,
        )
        task_scheduler.add_task(task)
        next_run = task_scheduler.get_next_run(task["id"])
        return {**task, "next_run_at": next_run}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/tasks/{task_id}")
async def get_task_api(task_id: str):
    """Get a single scheduled task by ID."""
    from scheduler.store import get_task
    from scheduler import scheduler as task_scheduler

    task = get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    next_run = task_scheduler.get_next_run(task_id)
    return {**task, "next_run_at": next_run}


@app.put("/api/tasks/{task_id}")
async def update_task_api(task_id: str, request: Request):
    """Update a scheduled task's fields.

    Body: {task_name?, cron?, task_prompt?, enabled?}
    """
    from scheduler.store import get_task, update_task
    from scheduler import scheduler as task_scheduler

    task = get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    body = await request.json()
    updates = {}
    for field in ("task_name", "cron", "task_prompt", "enabled", "model", "tool_access"):
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
    from scheduler.store import get_task, update_task
    from scheduler import scheduler as task_scheduler

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
    from scheduler.store import get_task, delete_task
    from scheduler import scheduler as task_scheduler

    task = get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    task_scheduler.remove_task(task_id)
    delete_task(task_id)
    return {"message": f"Task '{task_id}' deleted", "id": task_id}


@app.post("/api/tasks/{task_id}/run")
async def run_task_now_api(task_id: str):
    """Manually trigger a task to run now (one-off execution)."""
    from scheduler.store import get_task
    from scheduler.executor import execute_task

    task = get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    execute_task(task)
    return {"message": f"Task '{task_id}' triggered", "id": task_id}


# ── Composio Triggers ──

@app.get("/api/triggers")
async def list_triggers_api():
    """List all trigger recipes (local) merged with Composio active status."""
    from composio_triggers import _load_recipes, is_enabled

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
            "user_id": recipe.get("user_id", ""),
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
    from composio_triggers import _load_recipes

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
        "user_id": recipe.get("user_id", ""),
        "channel_name": recipe.get("channel_name", ""),
        "tool_access": recipe.get("tool_access", "full"),
        "created_at": recipe.get("created_at", ""),
    }

@app.post("/api/triggers")
async def create_trigger_api(request: Request):
    """Create a new trigger recipe.

    Body: {trigger_slug, agent_prompt, trigger_config?, model?, tool_access?, channel_name?}
    """
    from composio_triggers import create_trigger, is_enabled

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
    from composio_triggers import _load_recipes, _save_recipes

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
    from composio_triggers import _load_recipes, _save_recipes, is_enabled as composio_enabled

    if not composio_enabled():
        return JSONResponse({"error": "Composio not enabled"}, status_code=400)

    recipes = _load_recipes()
    recipe = recipes.get(trigger_id)
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
            trigger = client.triggers.create(
                slug=recipe["trigger_slug"],
                user_id=COMPOSIO_USER_ID,
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
            return JSONResponse({"error": f"Failed to enable: {str(e)}"}, status_code=500)


@app.delete("/api/triggers/{trigger_id}")
async def delete_trigger_api(trigger_id: str):
    """Delete a trigger: disable on Composio + remove local recipe."""
    from composio_triggers import _load_recipes, _save_recipes, is_enabled as composio_enabled

    recipes = _load_recipes()
    if trigger_id not in recipes:
        return JSONResponse({"error": "Trigger not found"}, status_code=404)

    # Try to disable on Composio (best-effort)
    if composio_enabled():
        try:
            from composio import Composio
            client = Composio()
            client.triggers.disable(trigger_id=trigger_id)
        except Exception as e:
            logger.warning("Could not disable trigger on Composio: %s", e)

    # Remove local recipe
    del recipes[trigger_id]
    _save_recipes(recipes)

    return {"message": f"Trigger '{trigger_id}' deleted", "id": trigger_id}


# ── Unified Automations (combined view) ──

@app.get("/api/automations")
async def list_automations_api():
    """Unified view: returns ALL cron tasks + trigger recipes in a consistent shape.

    Each item has:
      - id: unique identifier
      - type: "cron" | "trigger"
      - name: human-readable name
      - description: what this automation does (prompt preview)
      - schedule: cron expression (cron only) or trigger_slug (trigger only)
      - enabled: bool
      - model: LLM model override (empty = default)
      - channel_name: delivery channel
      - created_at: ISO timestamp
      - last_run_at: ISO timestamp or null (cron only)
      - next_run_at: ISO timestamp or null (cron only)
      - config: type-specific full config
    """
    from scheduler.store import list_tasks
    from scheduler import scheduler as task_scheduler
    from composio_triggers import _load_recipes, is_enabled as composio_enabled

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
            "user_id": t.get("user_id", ""),
            "created_at": t.get("created_at", ""),
            "last_run_at": t.get("last_run_at"),
            "last_result": (t.get("last_result") or "")[:200],
            "next_run_at": next_run,
            "config": t,  # full task data
        })

    # ── Triggers ──
    if composio_enabled():
        recipes = _load_recipes()
        # Quick active check
        active_ids = set()
        try:
            from composio import Composio
            client = Composio()
            active_resp = client.triggers.list_active()
            for t in getattr(active_resp, 'items', active_resp):
                active_ids.add(getattr(t, "id", None) or getattr(t, "trigger_id", str(t)))
        except Exception:
            pass

        for trigger_id, recipe in recipes.items():
            items.append({
                "id": trigger_id,
                "type": "trigger",
                "name": recipe.get("trigger_slug", ""),
                "description": (recipe.get("agent_prompt") or "")[:200],
                "schedule": recipe.get("trigger_slug", ""),
                "enabled": trigger_id in active_ids,
                "model": recipe.get("model", ""),
                "tool_access": recipe.get("tool_access", "full"),
                "channel_name": recipe.get("channel_name", ""),
                "user_id": recipe.get("user_id", ""),
                "created_at": recipe.get("created_at", ""),
                "last_run_at": None,
                "last_result": "",
                "next_run_at": None,
                "config": recipe,  # full recipe
            })

    # Sort by created_at descending
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return {
        "automations": items,
        "total": len(items),
        "counts": {
            "cron": sum(1 for x in items if x["type"] == "cron"),
            "trigger": sum(1 for x in items if x["type"] == "trigger"),
        },
    }


# ── Entry point ──

def main():
    import atexit
    import uvicorn
    from logger import recover_orphaned_logs
    from scheduler import scheduler

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
    from composio_triggers import start_listener as start_trigger_listener
    start_trigger_listener()

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
    print(f"     GET  /api/models                   — list models")
    print(f"     PUT  /api/models/current            — switch model")
    print(f"     GET  /api/notifications             — notification list")
    print(f"     GET  /api/notifications/stream      — SSE notification push")
    print(f"     GET  /api/automations               — all tasks + triggers")
    print(f"     GET  /api/tasks                     — list cron tasks")
    print(f"     POST /api/tasks                     — create cron task")
    print(f"     PUT  /api/tasks/:id                 — update cron task")
    print(f"     PATCH /api/tasks/:id/toggle          — toggle cron task")
    print(f"     DELETE /api/tasks/:id                — delete cron task")
    print(f"     POST /api/tasks/:id/run              — run task now")
    print(f"     GET  /api/triggers                  — list triggers")
    print(f"     POST /api/triggers                  — create trigger")
    print(f"     GET  /api/triggers/:id              — get trigger")
    print(f"     PUT  /api/triggers/:id              — update trigger recipe")
    print(f"     PATCH /api/triggers/:id/toggle       — toggle trigger")
    print(f"     DELETE /api/triggers/:id             — delete trigger")
    print(f"     GET  /api/health                   — health check")
    print(f"     POST /api/command/                 — slash command (IM compat)")
    print("=" * 50)

    uvicorn.run(app, host="0.0.0.0", port=DESKTOP_PORT, log_level="info")


if __name__ == "__main__":
    main()

