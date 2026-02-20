#!/usr/bin/env python3
"""Lightweight TTS daemon client — minimal imports, no pipecat dependency."""

import argparse
import os
import socket
import sys
import time
from pathlib import Path
from typing import Optional

# Add project root for shared imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.daemon_protocol import (
    PID_FILE,
    SOCKET_PATH,
    daemon_is_running,
    recv_message,
    send_message,
)


def send_speak_request(
    text: str,
    output_file: Optional[str] = None,
    voice_profile: Optional[str] = None,
    provider: Optional[str] = None,
    voice_id: Optional[str] = None,
    timeout: float = 30.0,
) -> dict:
    """Send speak request to daemon."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(SOCKET_PATH))

    try:
        send_message(
            sock,
            {
                "cmd": "speak",
                "text": text,
                "output_file": output_file,
                "voice_profile": voice_profile,
                "provider": provider,
                "voice_id": voice_id,
            },
        )
        return recv_message(sock, timeout=timeout)
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description="TTS client - connect to daemon")
    parser.add_argument("text", nargs="?")
    parser.add_argument("-p", "--voice-profile")
    parser.add_argument("--provider")
    parser.add_argument("--voice")
    parser.add_argument("-o", "--output")
    parser.add_argument("--wait", type=float, default=0, help="Wait for daemon (seconds)")

    args = parser.parse_args()

    if not args.text:
        parser.print_help()
        return

    if args.wait > 0:
        end_time = time.time() + args.wait
        while not daemon_is_running() and time.time() < end_time:
            time.sleep(0.1)

    if not daemon_is_running():
        print("Daemon not running. Start it with: talky say --start-daemon")
        sys.exit(1)

    try:
        start_time = time.time()
        result = send_speak_request(
            args.text,
            args.output,
            voice_profile=args.voice_profile,
            provider=args.provider,
            voice_id=args.voice,
        )
        elapsed = time.time() - start_time

        if result.get("success"):
            print(f"Done in {elapsed:.2f}s ({result.get('audio_bytes', 0)} bytes)")
        else:
            print(f"Error: {result.get('error')}")
            sys.exit(1)
    except ConnectionRefusedError:
        print("Cannot connect to daemon")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
