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
from sse_starlette.sse import EventSourceResponse

from channels.base import Channel, SLASH_COMMANDS
from channels.registry import register_channel
from session import sessions
from config import MODEL, CONTEXT_LIMIT

logger = logging.getLogger("channel.desktop")

# Desktop channel uses a fixed user_id (single-user desktop app)
DESKTOP_USER_ID = "desktop_user"
DESKTOP_PORT = int(os.getenv("DESKTOP_PORT", "8080"))


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
                self._push_event(user_id, "thought", {
                    "type": "narration",
                    "content": evt.get("content", ""),
                    "round": evt.get("round"),
                })
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
        self.send_status(user_id, "💭 Thinking...")
        run_agent_for_message(
            user_id, user_text_stripped,
            reply_func, status_func,
            channel_name=self.name,
            on_event_hook=event_hook,
        )
        self._push_event(user_id, "done", {})


# ── FastAPI app ──

channel = DesktopChannel()
app = FastAPI(title="Desktop Agent API")

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
    """Health check."""
    return {"status": "ok", "model": MODEL}


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
        while True:
            try:
                event = q.get(timeout=0.3)
            except queue.Empty:
                # Send a heartbeat comment to keep the connection alive
                yield {"comment": "heartbeat"}
                continue

            yield event

            # Stop streaming after 'done' event
            if event.get("event") == "done":
                break

    return EventSourceResponse(event_generator())


@app.post("/api/command/{cmd}")
async def command(cmd: str, request: Request):
    """Execute a slash command (/reset, /stop, /model, /compact, /status, /help)."""
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    args = body.get("args", "")

    handler = SLASH_COMMANDS.get(cmd)
    if not handler:
        return JSONResponse({"error": f"unknown command: {cmd}"}, status_code=404)

    # Collect status messages
    responses = []

    def capture_status(user_id, text):
        responses.append(text)

    # Temporarily override send_status to capture output
    original_send_status = channel.send_status
    channel.send_status = lambda uid, text: responses.append(text)
    try:
        handler[0](channel, DESKTOP_USER_ID, args)
    finally:
        channel.send_status = original_send_status

    return {"result": "\n".join(responses)}


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
    print(f"     POST /api/chat     — send message (SSE stream)")
    print(f"     GET  /api/status   — session status")
    print(f"     POST /api/command/ — slash commands")
    print(f"     GET  /api/health   — health check")
    print("=" * 50)

    uvicorn.run(app, host="0.0.0.0", port=DESKTOP_PORT, log_level="info")


if __name__ == "__main__":
    main()
