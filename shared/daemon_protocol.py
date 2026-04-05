"""Shared daemon protocol — length-prefixed JSON over Unix sockets."""

import json
import os
import socket
import struct
from pathlib import Path

# Voice daemon (current)
VOICE_SOCKET_PATH = Path("/tmp/talky_voice_daemon.sock")
VOICE_PID_FILE = Path("/tmp/talky_voice_daemon.pid")

# Legacy TTS-only daemon (backward compat)
LEGACY_SOCKET_PATH = Path("/tmp/talky_tts_daemon.sock")
LEGACY_PID_FILE = Path("/tmp/talky_tts_daemon.pid")

# Default to voice daemon paths
SOCKET_PATH = VOICE_SOCKET_PATH
PID_FILE = VOICE_PID_FILE


def send_message(sock: socket.socket, data: dict) -> None:
    """Send a length-prefixed JSON message."""
    payload = json.dumps(data).encode()
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_message(sock: socket.socket, timeout: float = 30.0) -> dict:
    """Receive a length-prefixed JSON message."""
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


def _check_daemon(pid_file: Path, socket_path: Path) -> bool:
    """Check if a daemon is running by PID file + socket existence."""
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return socket_path.exists()
    except (ProcessLookupError, ValueError):
        pid_file.unlink(missing_ok=True)
        socket_path.unlink(missing_ok=True)
        return False


def daemon_is_running() -> bool:
    """Check if voice daemon is running."""
    return _check_daemon(VOICE_PID_FILE, VOICE_SOCKET_PATH)


def legacy_daemon_is_running() -> bool:
    """Check if legacy TTS daemon is running."""
    return _check_daemon(LEGACY_PID_FILE, LEGACY_SOCKET_PATH)


def cleanup_legacy_daemon() -> None:
    """Clean up stale legacy daemon socket/pid files."""
    LEGACY_SOCKET_PATH.unlink(missing_ok=True)
    LEGACY_PID_FILE.unlink(missing_ok=True)
