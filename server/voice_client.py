#!/usr/bin/env python3
"""Lightweight voice daemon client — sends ask/listen commands.

No Pipecat dependency. Uses shared daemon protocol only.
"""

import sys
import time
from typing import Optional

from shared.daemon_protocol import (
    VOICE_SOCKET_PATH,
    daemon_is_running,
    recv_message,
    send_message,
)


def send_ask_request(
    text: str,
    voice_profile: Optional[str] = None,
    provider: Optional[str] = None,
    voice_id: Optional[str] = None,
    silence_timeout: float = 10.0,
    timeout: Optional[float] = None,
) -> dict:
    """Send ask request to daemon: speak text then listen for response.

    Turn detection (SpeechTimeoutUserTurnStopStrategy) handles ending the turn
    once the user stops talking. silence_timeout only applies if nobody speaks at all.
    """
    import socket as socket_mod

    sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
    sock.connect(str(VOICE_SOCKET_PATH))

    # No hard cap — turn detection handles ending. Just need enough for TTS + open-ended listen.
    recv_timeout = timeout or 600.0

    try:
        send_message(
            sock,
            {
                "cmd": "ask",
                "text": text,
                "voice_profile": voice_profile,
                "provider": provider,
                "voice_id": voice_id,
                "silence_timeout": silence_timeout,
            },
        )
        return recv_message(sock, timeout=recv_timeout)
    finally:
        sock.close()


def send_listen_request(
    voice_profile: Optional[str] = None,
    silence_timeout: float = 10.0,
    timeout: Optional[float] = None,
) -> dict:
    """Send listen request to daemon: just listen for speech."""
    import socket as socket_mod

    sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
    sock.connect(str(VOICE_SOCKET_PATH))

    recv_timeout = timeout or 600.0

    try:
        send_message(
            sock,
            {
                "cmd": "listen",
                "voice_profile": voice_profile,
                "silence_timeout": silence_timeout,
            },
        )
        return recv_message(sock, timeout=recv_timeout)
    finally:
        sock.close()


def main():
    """CLI entry point for voice client."""
    import argparse

    parser = argparse.ArgumentParser(description="Voice Daemon Client")
    parser.add_argument("text", nargs="?", help="Text to speak (for ask)")
    parser.add_argument("--cmd", choices=["ask", "listen"], default="ask")
    parser.add_argument("-p", "--voice-profile", help="Voice profile")
    parser.add_argument("--provider", help="TTS provider")
    parser.add_argument("--voice", help="Voice ID")
    parser.add_argument("--silence-timeout", type=float, default=10.0)
    parser.add_argument("--wait", type=float, default=0, help="Wait N seconds for daemon")

    args = parser.parse_args()

    if args.wait and not daemon_is_running():
        deadline = time.time() + args.wait
        while time.time() < deadline:
            if daemon_is_running():
                break
            time.sleep(0.5)

    if not daemon_is_running():
        print("Voice daemon not running", file=sys.stderr)
        sys.exit(1)

    try:
        if args.cmd == "ask":
            if not args.text:
                print("Text required for ask command", file=sys.stderr)
                sys.exit(1)
            result = send_ask_request(
                text=args.text,
                voice_profile=args.voice_profile,
                provider=args.provider,
                voice_id=args.voice,
                silence_timeout=args.silence_timeout,
            )
        else:
            result = send_listen_request(
                voice_profile=args.voice_profile,
                silence_timeout=args.silence_timeout,
            )

        if result.get("success"):
            transcript = result.get("transcript", "")
            if transcript:
                print(transcript)
            else:
                if result.get("timeout"):
                    print("[no speech detected]", file=sys.stderr)
                else:
                    print("[empty transcription]", file=sys.stderr)
        else:
            print(f"Error: {result.get('error')}", file=sys.stderr)
            sys.exit(1)

    except ConnectionRefusedError:
        print("Cannot connect to voice daemon", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
