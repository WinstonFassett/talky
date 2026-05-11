"""Daemon lifecycle helper for app launchers.

The old ``AppLauncher`` class was ripped in ticket 5d95 — agent launching
is now handled by the generic ``cmd_launch`` path in ``talky_cli.py`` and
the per-profile ``launcher:`` block in ``talky-profiles.yaml``. The
``DaemonManager`` remains for callers that want a typed wrapper around
``talky daemon`` startup.
"""

import subprocess
import time
from typing import Any, Dict, Optional

from loguru import logger


class DaemonManager:
    """Ensures the talky daemon (:9090) is running.

    The talky daemon is the unified server hosting the voice pipeline,
    WebRTC transport, client UI, HTTP control plane, and FastMCP SSE
    mount. This class is a thin wrapper around `talky daemon` that
    spawns it (detached) if not already up. The daemon is intentionally
    left running across sessions — no `stop()` cleanup.
    """

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None

    async def ensure_running(self, config: Dict[str, Any]) -> bool:
        """Ensure the talky daemon is running. Returns True if available."""
        try:
            result = subprocess.run(["lsof", "-ti:9090"], capture_output=True, text=True)
            if result.stdout.strip():
                logger.info("talky daemon already running on :9090")
                return True
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            logger.debug(f"Could not check port 9090: {e}")

        logger.info("Starting talky daemon in background...")
        daemon_args = ["talky", "daemon"]

        if voice_profile := config.get("voice_profile"):
            daemon_args.extend(["--voice-profile", voice_profile])

        if host := config.get("host"):
            daemon_args.extend(["--host", host])

        # `talky daemon` is now ensure-and-return — it spawns the
        # detached server itself and exits. We just wait for the port.
        subprocess.run(daemon_args, capture_output=True)

        time.sleep(1)

        try:
            result = subprocess.run(["lsof", "-ti:9090"], capture_output=True, text=True)
            if result.stdout.strip():
                logger.info("talky daemon started successfully")
                return True
            else:
                logger.error("talky daemon failed to start")
                return False
        except (FileNotFoundError, subprocess.SubprocessError):
            logger.error("Could not verify talky daemon startup")
            return False

    async def stop(self):
        """The talky daemon is left running as a background service."""
        logger.info("talky daemon left running as background service")
