"""Daemon lifecycle helper for app launchers.

The old ``AppLauncher`` class was ripped in ticket 5d95 — agent launching
is now handled by the generic ``cmd_launch`` path in ``talky_cli.py`` and
the per-profile ``launcher:`` block in ``talky-profiles.yaml``. The
``DaemonManager`` remains for callers that want a typed wrapper around
``talky daemon`` startup.
"""

import subprocess
import sys
import time
from typing import Any, Dict, Optional

from loguru import logger


def _self_argv() -> list[str]:
    """Re-invoke talky via ``sys.executable -m talky`` so PATH isn't required.

    See ``talky.cli._self_argv`` for rationale. Duplicated here (rather
    than imported) to keep the launcher import-light and avoid pulling
    the full CLI module just to get a 1-line helper.
    """
    return [sys.executable, "-m", "talky"]


class DaemonManager:
    """Ensures the talky daemon is running.

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
        from pathlib import Path
        # The daemon's ready file is the authoritative liveness signal.
        # Port may be random (when not pinned in settings.yaml), so check the
        # ready file instead of sniffing a specific port.
        ready_path = Path.home() / ".talky" / "run" / "talky-daemon.ready"
        try:
            pid = int(ready_path.read_text().strip())
            import os
            os.kill(pid, 0)
            logger.info(f"talky daemon already running (pid={pid})")
            return True
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            pass

        logger.info("Starting talky daemon in background...")
        daemon_args = [*_self_argv(), "daemon"]

        if voice_profile := config.get("voice_profile"):
            daemon_args.extend(["--voice-profile", voice_profile])

        if host := config.get("host"):
            daemon_args.extend(["--host", host])

        # `talky daemon` is now ensure-and-return — it spawns the
        # detached server itself and exits. We just wait for the port.
        subprocess.run(daemon_args, capture_output=True)

        # Poll ready file for up to 30s.
        import os
        for _ in range(60):
            time.sleep(0.5)
            try:
                pid = int(ready_path.read_text().strip())
                os.kill(pid, 0)
                logger.info("talky daemon started successfully")
                return True
            except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
                continue
        logger.error("talky daemon failed to start (ready file never appeared)")
        return False

    async def stop(self):
        """The talky daemon is left running as a background service."""
        logger.info("talky daemon left running as background service")
