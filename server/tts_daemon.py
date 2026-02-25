#!/usr/bin/env python3
"""Persistent TTS daemon for fast speech generation.

Uses Unix sockets with async server. Runs as a background process,
managed via PID file. TTS service stays initialized for fast subsequent requests.
"""

import argparse
import asyncio
import json
import os
import signal
import struct
import sys
import time
from pathlib import Path
from typing import Optional

# Add project root for shared imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from pipecat.frames.frames import StartFrame, TTSAudioRawFrame
from shared.daemon_protocol import (
    PID_FILE,
    SOCKET_PATH,
    daemon_is_running,
    recv_message,
    send_message,
)
from shared.voice_config import create_tts_for_profile

# Idle timeout (seconds)
IDLE_TIMEOUT = 60 * 60


class TTSDaemon:
    """Persistent TTS daemon server using asyncio."""

    def __init__(self, idle_timeout: Optional[float] = None):
        self.tts_services = {}
        self.default_tts_service = None
        self.running = True
        self.server = None
        self.last_activity = time.time()
        self.idle_timeout = idle_timeout

    async def initialize_tts(self) -> bool:
        try:
            # Ensure dependencies are available before initializing TTS
            import sys
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from shared.dependency_installer import ensure_dependencies
            
            if not ensure_dependencies():
                logger.error("Failed to install required dependencies")
                return False
            
            self.default_tts_service = create_tts_for_profile()
            await self.default_tts_service.start(StartFrame())
            logger.info("TTS service initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize TTS: {e}")
            return False

    async def get_tts_service(
        self,
        voice_profile: Optional[str] = None,
        provider: Optional[str] = None,
        voice_id: Optional[str] = None,
    ):
        if not voice_profile and not provider and not voice_id:
            return self.default_tts_service

        cache_key = f"{voice_profile or ''}:{provider or ''}:{voice_id or ''}"
        if cache_key in self.tts_services:
            return self.tts_services[cache_key]

        tts_service = create_tts_for_profile(voice_profile, provider, voice_id)
        await tts_service.start(StartFrame())
        self.tts_services[cache_key] = tts_service
        logger.info(f"Created TTS service for: {cache_key}")
        return tts_service

    async def generate_speech(
        self,
        text: str,
        output_file: Optional[str] = None,
        voice_profile: Optional[str] = None,
        provider: Optional[str] = None,
        voice_id: Optional[str] = None,
    ) -> dict:
        try:
            logger.info(f"Generating speech: {text[:50]}{'...' if len(text) > 50 else ''}")
            tts_service = await self.get_tts_service(voice_profile, provider, voice_id)
            context_id = tts_service.create_context_id()
            audio_data = []

            async for frame in tts_service.run_tts(text, context_id):
                if isinstance(frame, TTSAudioRawFrame):
                    if hasattr(frame, "audio") and frame.audio:
                        audio_data.append(frame.audio)

            if not audio_data:
                return {"success": False, "error": "No audio generated"}

            combined_audio = b"".join(audio_data)
            result = {"success": True, "audio_bytes": len(combined_audio)}

            if output_file:
                with open(output_file, "wb") as f:
                    f.write(combined_audio)
                result["output_file"] = output_file
                logger.info(f"Saved to: {output_file}")
            else:

                def play_audio():
                    try:
                        import pyaudio

                        p = pyaudio.PyAudio()
                        stream = p.open(
                            format=pyaudio.paInt16,
                            channels=1,
                            rate=tts_service.sample_rate,
                            output=True,
                        )
                        stream.write(combined_audio)
                        stream.stop_stream()
                        stream.close()
                        p.terminate()
                        return True
                    except Exception as e:
                        logger.warning(f"Could not play audio: {e}")
                        return False

                loop = asyncio.get_event_loop()
                played = await loop.run_in_executor(None, play_audio)
                result["played"] = played

            return result

        except Exception as e:
            logger.error(f"Speech generation error: {e}")
            return {"success": False, "error": str(e)}

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.last_activity = time.time()
        try:
            length_data = await asyncio.wait_for(reader.readexactly(4), timeout=5.0)
            msg_len = struct.unpack("!I", length_data)[0]
            data = await asyncio.wait_for(reader.readexactly(msg_len), timeout=5.0)
            request = json.loads(data.decode())

            cmd = request.get("cmd")

            if cmd == "speak":
                result = await self.generate_speech(
                    text=request.get("text", ""),
                    output_file=request.get("output_file"),
                    voice_profile=request.get("voice_profile"),
                    provider=request.get("provider"),
                    voice_id=request.get("voice_id"),
                )
            elif cmd == "ping":
                result = {"success": True, "status": "running"}
            elif cmd == "stop":
                self.running = False
                result = {"success": True, "status": "stopping"}
            else:
                result = {"success": False, "error": f"Unknown command: {cmd}"}

            payload = json.dumps(result).encode()
            writer.write(struct.pack("!I", len(payload)) + payload)
            await writer.drain()

        except asyncio.TimeoutError:
            logger.warning("Client timeout")
        except Exception as e:
            logger.error(f"Client handler error: {e}")
            try:
                payload = json.dumps({"success": False, "error": str(e)}).encode()
                writer.write(struct.pack("!I", len(payload)) + payload)
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def run(self) -> None:
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        if not await self.initialize_tts():
            logger.error("TTS initialization failed, exiting")
            return

        self.server = await asyncio.start_unix_server(self.handle_client, path=str(SOCKET_PATH))
        PID_FILE.write_text(str(os.getpid()))
        logger.info(f"TTS daemon listening on {SOCKET_PATH} (PID: {os.getpid()})")

        try:
            while self.running:
                await asyncio.sleep(1.0)
                if self.idle_timeout and time.time() - self.last_activity > self.idle_timeout:
                    logger.info(f"Idle timeout ({self.idle_timeout}s), shutting down")
                    self.running = False
        finally:
            self.server.close()
            await self.server.wait_closed()
            SOCKET_PATH.unlink(missing_ok=True)
            PID_FILE.unlink(missing_ok=True)
            logger.info("TTS daemon stopped")


def start_daemon(wait: bool = True) -> bool:
    """Start daemon in background."""
    if daemon_is_running():
        logger.info("Daemon already running")
        return True

    SOCKET_PATH.unlink(missing_ok=True)
    PID_FILE.unlink(missing_ok=True)

    pid = os.fork()
    if pid > 0:
        if not wait:
            logger.info(f"Daemon starting (PID: {pid})")
            return True
        for _ in range(20):
            time.sleep(0.5)
            if SOCKET_PATH.exists():
                logger.info(f"Daemon started (PID: {pid})")
                return True
        logger.error("Daemon failed to start")
        return False

    os.setsid()
    pid = os.fork()
    if pid > 0:
        os._exit(0)

    sys.stdin.close()
    devnull = open("/dev/null", "w")
    sys.stdout = devnull
    sys.stderr = devnull

    PID_FILE.write_text(str(os.getpid()))

    daemon = TTSDaemon(idle_timeout=IDLE_TIMEOUT)

    def handle_signal(signum, frame):
        daemon.running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        asyncio.run(daemon.run())
    except Exception as e:
        logger.error(f"Daemon error: {e}")

    os._exit(0)


def stop_daemon() -> bool:
    """Stop the daemon."""
    import socket as socket_mod

    if not daemon_is_running():
        logger.info("Daemon not running")
        return True

    try:
        sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        sock.connect(str(SOCKET_PATH))
        send_message(sock, {"cmd": "stop"})
        recv_message(sock, timeout=5.0)
        sock.close()

        for _ in range(10):
            if not daemon_is_running():
                logger.info("Daemon stopped")
                return True
            time.sleep(0.5)

        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGKILL)
        PID_FILE.unlink(missing_ok=True)
        SOCKET_PATH.unlink(missing_ok=True)
        logger.info("Daemon force killed")
        return True

    except Exception as e:
        logger.error(f"Error stopping daemon: {e}")
        return False


def send_speak_request(
    text: str,
    output_file: Optional[str] = None,
    voice_profile: Optional[str] = None,
    provider: Optional[str] = None,
    voice_id: Optional[str] = None,
    timeout: float = 30.0,
) -> dict:
    """Send speak request to daemon."""
    import socket as socket_mod

    sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
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
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="TTS Daemon")
    parser.add_argument("text", nargs="?", help="Text to speak")
    parser.add_argument("-o", "--output", help="Save to file")
    parser.add_argument("-p", "--voice-profile", help="Voice profile")
    parser.add_argument("--provider", help="TTS provider")
    parser.add_argument("--voice", help="Voice ID")
    parser.add_argument("-l", "--list-profiles", action="store_true")
    parser.add_argument("--start", action="store_true", help="Start daemon")
    parser.add_argument("--stop", action="store_true", help="Stop daemon")
    parser.add_argument("--status", action="store_true", help="Check status")
    parser.add_argument("--foreground", action="store_true", help="Run in foreground")

    args = parser.parse_args()

    if args.list_profiles:
        from server.config.profile_manager import get_profile_manager

        pm = get_profile_manager()
        profiles = pm.list_voice_profiles()
        if not profiles:
            print("No voice profiles configured")
        else:
            print("Available voice profiles:")
            for name, desc in profiles.items():
                print(f"  {name}: {desc}")
        return

    if args.foreground:
        daemon = TTSDaemon(idle_timeout=None)
        signal.signal(signal.SIGTERM, lambda s, f: setattr(daemon, "running", False))
        signal.signal(signal.SIGINT, lambda s, f: setattr(daemon, "running", False))
        asyncio.run(daemon.run())
        return

    if args.start:
        sys.exit(0 if start_daemon() else 1)

    if args.stop:
        sys.exit(0 if stop_daemon() else 1)

    if args.status:
        if daemon_is_running():
            pid = PID_FILE.read_text().strip()
            print(f"Daemon running (PID: {pid})")
        else:
            print("Daemon not running")
        return

    if not args.text:
        parser.print_help()
        return

    if not daemon_is_running():
        logger.info("Starting daemon...")
        if not start_daemon():
            logger.error("Failed to start daemon, falling back to direct TTS")
            from say_command import say_text

            success = asyncio.run(say_text(args.text, output_file=args.output))
            sys.exit(0 if success else 1)

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
            logger.info(f"Completed in {elapsed:.2f}s ({result.get('audio_bytes', 0)} bytes)")
        else:
            logger.error(f"Error: {result.get('error')}")
            sys.exit(1)
    except ConnectionRefusedError:
        logger.error("Cannot connect to daemon")
        sys.exit(1)


if __name__ == "__main__":
    main()
