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
    from session import sessions
    from channels.registry import register_channel
    from scheduler import scheduler
    from composio_triggers import start_listener as start_trigger_listener

    # ── 1. Shared initialization (once for all channels) ──
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    recover_orphaned_logs()
    atexit.register(sessions.close_all)
    atexit.register(scheduler.stop)

    # ── 2. Shared services (once) ──
    scheduler.start()
    start_trigger_listener()

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
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
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
