"""
Unified multi-channel entry point.

Starts all enabled channels within a single process, sharing:
  - SessionManager (conversation context)
  - Scheduler (cron tasks)
  - Composio Triggers (event-driven workflows)
  - Channel Registry (for result delivery)

Usage:
  CHANNELS=desktop python src/main.py                    # single channel
  CHANNELS=desktop,feishu,telegram python src/main.py    # multi-channel
"""

import os
import sys
import logging
import threading
import time
import atexit

# Ensure src/ is on the Python path
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from dotenv import load_dotenv
load_dotenv()

from config import ENABLED_CHANNELS, MODEL, OWNER_USER_ID

logger = logging.getLogger("main")


def _safe_channel_loop(channel):
    """Run channel.start() with auto-restart on crash.

    Each channel runs in its own daemon thread. If it crashes,
    wait 5 seconds then try again. Other channels are unaffected.
    """
    while True:
        try:
            logger.info(f"Starting channel: {channel.name}")
            channel.start()
            # start() returned normally = channel exited on its own
            logger.info(f"Channel {channel.name} exited normally")
            break
        except Exception as e:
            logger.error(f"Channel {channel.name} crashed, restarting in 5s: {e}", exc_info=True)
            time.sleep(5)


def main():
    from logger import recover_orphaned_logs
    from core.session import sessions
    from channels.registry import register_channel
    from automations.scheduler import scheduler
    from automations.composio_triggers import start_listener as start_trigger_listener

    # ── 1. Shared initialization (once for all channels) ──
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # Persist ERROR+ logs to a rotating file (max 2 MB × 3 = 6 MB)
    import os as _os
    import datetime as _dt
    from logging.handlers import RotatingFileHandler as _RFH

    _TZ_CST = _dt.timezone(_dt.timedelta(hours=8))

    class _CSTFormatter(logging.Formatter):
        """Formatter that always uses Beijing time (UTC+8), regardless of system timezone."""
        def formatTime(self, record, datefmt=None):
            ct = _dt.datetime.fromtimestamp(record.created, tz=_TZ_CST)
            return ct.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

    _log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "logs")
    _os.makedirs(_log_dir, exist_ok=True)
    _err_handler = _RFH(
        _os.path.join(_log_dir, "errors.log"),
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _err_handler.setLevel(logging.ERROR)
    _err_handler.setFormatter(_CSTFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(_err_handler)

    recover_orphaned_logs()
    atexit.register(sessions.close_all)
    atexit.register(scheduler.stop)
    from integrations.syncthing_watcher import stop_watcher as stop_sync_watcher
    atexit.register(stop_sync_watcher)

    # ── 2. Shared services (once) ──
    scheduler.start()
    start_trigger_listener()

    from integrations.syncthing_watcher import start_watcher as start_sync_watcher
    start_sync_watcher()

    # Bootstrap workspaces: wrap every existing Syncthing folder in a
    # workspace record (idempotent). Runs after the watcher so the
    # Syncthing API is warmed up.
    try:
        from core.workspace import workspaces as _workspaces
        bootstrapped = _workspaces.bootstrap_from_syncthing()
        logger.info(
            "Workspace bootstrap complete: %d workspace(s) registered",
            len(bootstrapped),
        )
    except Exception as e:
        logger.warning("Workspace bootstrap failed: %s", e)

    # File-drop automation (must start AFTER sync watcher)
    from automations.file_drop import start_file_drop_listener
    start_file_drop_listener()

    # ── 3. Create and register channels ──
    desktop_info = None

    for name in ENABLED_CHANNELS:
        if name == "desktop":
            from channels.desktop import channel as desktop_ch, app as desktop_app, DESKTOP_PORT
            register_channel(desktop_ch)
            desktop_info = (desktop_ch, desktop_app, DESKTOP_PORT)

        elif name == "feishu":
            from channels.feishu import FeishuChannel
            ch = FeishuChannel()
            register_channel(ch)
            threading.Thread(
                target=_safe_channel_loop, args=(ch,),
                name="channel-feishu", daemon=True,
            ).start()

        elif name == "telegram":
            from channels.telegram import TelegramChannel
            ch = TelegramChannel()
            register_channel(ch)
            threading.Thread(
                target=_safe_channel_loop, args=(ch,),
                name="channel-telegram", daemon=True,
            ).start()

        else:
            logger.warning(f"Unknown channel: {name}, skipping")

    # ── 4. Startup banner ──
    print("=" * 55)
    print("🚀 Nono CoWork — Multi-Channel Agent")
    print(f"   Owner: {OWNER_USER_ID}")
    print(f"   Model: {MODEL}")
    print(f"   Channels: {', '.join(ENABLED_CHANNELS)}")
    print("=" * 55)

    # ── 5. Main thread: Desktop (HTTP) or keep-alive ──
    if desktop_info:
        import uvicorn
        _, app, port = desktop_info
        # Kill any stale process occupying the port before binding
        from channels.desktop import _kill_stale_port_holder
        _kill_stale_port_holder(port)
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    else:
        # No Desktop channel → keep the main thread alive
        # so daemon threads (feishu, telegram) continue running.
        import signal
        try:
            signal.pause()
        except AttributeError:
            # Windows fallback
            threading.Event().wait()


if __name__ == "__main__":
    main()
