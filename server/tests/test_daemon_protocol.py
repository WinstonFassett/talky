"""Tests for shared daemon protocol."""

import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.daemon_protocol import recv_message, send_message


def test_send_recv_roundtrip():
    """Socket pair → send dict → receive same dict."""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.listen(1)

    payload = {"cmd": "speak", "text": "hello world", "nested": {"a": 1}}
    received = {}

    def server_thread():
        nonlocal received
        conn, _ = server_sock.accept()
        received = recv_message(conn, timeout=5.0)
        send_message(conn, {"ok": True})
        conn.close()

    t = threading.Thread(target=server_thread)
    t.start()

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    send_message(client, payload)
    response = recv_message(client, timeout=5.0)
    client.close()

    t.join(timeout=5)
    server_sock.close()

    assert received == payload
    assert response == {"ok": True}
