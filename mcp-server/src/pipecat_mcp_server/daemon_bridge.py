"""Bridge between MCP server and the voice daemon.

Provides async wrappers around the daemon's unix socket protocol for
say_local_audio and ask_local_audio MCP tools. Auto-starts the daemon
if it's not running.
"""

import asyncio
import json
import socket as socket_mod
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from loguru import logger

# Voice daemon socket/PID paths (must match shared/daemon_protocol.py)
VOICE_SOCKET_PATH = Path("/tmp/talky_voice_daemon.sock")
VOICE_PID_FILE = Path("/tmp/talky_voice_daemon.pid")


def _daemon_is_running() -> bool:
    """Check if voice daemon is running."""
    import os

    if not VOICE_PID_FILE.exists():
        return False
    try:
        pid = int(VOICE_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return VOICE_SOCKET_PATH.exists()
    except (ProcessLookupError, ValueError):
        VOICE_PID_FILE.unlink(missing_ok=True)
        VOICE_SOCKET_PATH.unlink(missing_ok=True)
        return False


def _find_voice_daemon_script() -> Optional[Path]:
    """Locate voice_daemon.py relative to project structure."""
    # Try relative to this file (mcp-server/src/pipecat_mcp_server/ -> server/)
    mcp_src = Path(__file__).resolve().parent
    project_root = mcp_src.parent.parent.parent  # up from src/pipecat_mcp_server/
    voice_daemon = project_root / "server" / "voice_daemon.py"
    if voice_daemon.exists():
        return voice_daemon

    # Try via TALKY_ROOT env var
    import os

    talky_root = os.getenv("TALKY_ROOT")
    if talky_root:
        voice_daemon = Path(talky_root) / "server" / "voice_daemon.py"
        if voice_daemon.exists():
            return voice_daemon

    return None


def ensure_daemon_running() -> bool:
    """Start the voice daemon if it's not already running."""
    if _daemon_is_running():
        return True

    import shutil

    # Prefer `talky say --start-daemon` which uses the correct venv
    talky_bin = shutil.which("talky")
    if talky_bin:
        logger.info("Auto-starting voice daemon via `talky say --start-daemon`")
        subprocess.Popen(
            [talky_bin, "say", "--start-daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        # Fallback: run voice_daemon.py directly
        script = _find_voice_daemon_script()
        if not script:
            logger.error("Cannot find voice_daemon.py or talky CLI")
            return False

        logger.info(f"Auto-starting voice daemon from {script}")
        subprocess.Popen(
            [sys.executable, str(script), "--start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(script.parent),
        )

    # Wait for daemon to be ready
    for _ in range(20):
        time.sleep(0.5)
        if VOICE_SOCKET_PATH.exists():
            logger.info("Voice daemon started")
            return True

    logger.error("Voice daemon failed to start")
    return False


def _send_recv(msg: dict, timeout: float = 60.0) -> dict:
    """Send a command to daemon and receive response (blocking)."""
    sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
    sock.connect(str(VOICE_SOCKET_PATH))
    try:
        # Send
        payload = json.dumps(msg).encode()
        sock.sendall(struct.pack("!I", len(payload)) + payload)

        # Receive
        sock.settimeout(timeout)

        length_data = b""
        while len(length_data) < 4:
            chunk = sock.recv(4 - len(length_data))
            if not chunk:
                raise ConnectionError("Connection closed")
            length_data += chunk

        msg_len = struct.unpack("!I", length_data)[0]

        data = b""
        while len(data) < msg_len:
            chunk = sock.recv(min(4096, msg_len - len(data)))
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk

        return json.loads(data.decode())
    finally:
        sock.close()


async def say(
    text: str,
    voice_profile: Optional[str] = None,
    provider: Optional[str] = None,
    voice_id: Optional[str] = None,
) -> dict:
    """Speak text via daemon (local audio out, no browser)."""
    if not ensure_daemon_running():
        return {"success": False, "error": "Voice daemon not available"}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _send_recv,
        {
            "cmd": "speak",
            "text": text,
            "voice_profile": voice_profile,
            "provider": provider,
            "voice_id": voice_id,
        },
        30.0,
    )


async def ask(
    text: str,
    voice_profile: Optional[str] = None,
    provider: Optional[str] = None,
    voice_id: Optional[str] = None,
    listen_timeout: float = 15.0,
    silence_timeout: float = 5.0,
) -> dict:
    """Speak text then listen for response via daemon (local audio, no browser)."""
    if not ensure_daemon_running():
        return {"success": False, "error": "Voice daemon not available"}

    # Timeout for recv: TTS time + listen time + buffer
    recv_timeout = listen_timeout + 30.0

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _send_recv,
        {
            "cmd": "ask",
            "text": text,
            "voice_profile": voice_profile,
            "provider": provider,
            "voice_id": voice_id,
            "listen_timeout": listen_timeout,
            "silence_timeout": silence_timeout,
        },
        recv_timeout,
    )
