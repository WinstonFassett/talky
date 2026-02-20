"""Shared daemon protocol — length-prefixed JSON over Unix sockets."""

import json
import os
import socket
import struct
from pathlib import Path

SOCKET_PATH = Path("/tmp/talky_tts_daemon.sock")
PID_FILE = Path("/tmp/talky_tts_daemon.pid")


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


def daemon_is_running() -> bool:
    """Check if daemon is running by PID file + socket existence."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return SOCKET_PATH.exists()
    except (ProcessLookupError, ValueError):
        PID_FILE.unlink(missing_ok=True)
        SOCKET_PATH.unlink(missing_ok=True)
        return False
