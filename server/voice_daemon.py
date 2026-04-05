#!/usr/bin/env python3
"""Persistent voice I/O daemon — TTS + STT + mic over Unix sockets.

Evolves the TTS daemon to support bidirectional voice: speak text and listen
for user speech via local audio. Keeps TTS and STT services warm for fast
subsequent requests.

Commands:
    speak:  Generate and play TTS audio (existing behavior)
    ask:    Speak text, then listen for user response via mic + VAD + STT
    ping:   Health check
    stop:   Graceful shutdown
"""

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
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    EndFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
)
from shared.daemon_protocol import (
    VOICE_PID_FILE,
    VOICE_SOCKET_PATH,
    cleanup_legacy_daemon,
    daemon_is_running,
    recv_message,
    send_message,
)
from shared.voice_config import create_tts_for_profile

# Idle timeout (seconds)
IDLE_TIMEOUT = 60 * 60

# Audio constants (for TTS playback)
CHANNELS = 1

# Default timeouts for listening
DEFAULT_LISTEN_TIMEOUT = 300.0  # hard cap — safety net only, turn detection handles normal ending
DEFAULT_SILENCE_TIMEOUT = 10.0  # max seconds of no speech at all before returning

# Listen indicator tones (generated once, cached)
_TONE_CACHE: dict[str, bytes] = {}
TONE_SAMPLE_RATE = 16000


def _generate_tone(freq_start: float, freq_end: float, duration: float = 0.2) -> bytes:
    """Generate a short sine tone as 16-bit PCM. Lightweight — stdlib only."""
    import math

    n_samples = int(TONE_SAMPLE_RATE * duration)
    amplitude = 12000  # moderate volume, not jarring
    samples = bytearray()
    for i in range(n_samples):
        t = i / TONE_SAMPLE_RATE
        # Linear frequency sweep
        freq = freq_start + (freq_end - freq_start) * (i / n_samples)
        # Fade in/out envelope (first/last 20% of samples)
        fade_samples = int(n_samples * 0.2)
        if i < fade_samples:
            envelope = i / fade_samples
        elif i > n_samples - fade_samples:
            envelope = (n_samples - i) / fade_samples
        else:
            envelope = 1.0
        value = int(amplitude * envelope * math.sin(2 * math.pi * freq * t))
        samples.extend(struct.pack("<h", max(-32768, min(32767, value))))
    return bytes(samples)


def get_listen_start_tone() -> bytes:
    """Rising tone: 'listening now'."""
    if "start" not in _TONE_CACHE:
        _TONE_CACHE["start"] = _generate_tone(600, 900, duration=0.15)
    return _TONE_CACHE["start"]


def get_listen_stop_tone() -> bytes:
    """Falling tone: 'got it'."""
    if "stop" not in _TONE_CACHE:
        _TONE_CACHE["stop"] = _generate_tone(900, 600, duration=0.15)
    return _TONE_CACHE["stop"]


class VoiceDaemon:
    """Persistent voice I/O daemon server using asyncio."""

    def __init__(self, idle_timeout: Optional[float] = None):
        # TTS
        self.tts_services = {}
        self.default_tts_service = None

        # PyAudio (for TTS playback)
        self._pyaudio = None

        # Mic lock — only one listen at a time
        self._mic_lock = asyncio.Lock()

        # Lifecycle
        self.running = True
        self.server = None
        self.last_activity = time.time()
        self.idle_timeout = idle_timeout

    async def initialize_tts(self) -> bool:
        """Initialize default TTS service."""
        try:
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

    def _ensure_pyaudio(self):
        """Lazily initialize PyAudio."""
        if self._pyaudio is None:
            import pyaudio

            self._pyaudio = pyaudio.PyAudio()

    async def get_tts_service(
        self,
        voice_profile: Optional[str] = None,
        provider: Optional[str] = None,
        voice_id: Optional[str] = None,
    ):
        """Get or create a cached TTS service."""
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
        """Generate and play TTS audio."""
        try:
            logger.info(f"Generating speech: {text[:50]}{'...' if len(text) > 50 else ''}")
            tts_service = await self.get_tts_service(voice_profile, provider, voice_id)
            if not tts_service:
                return {"success": False, "error": "TTS service not available"}
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
                loop = asyncio.get_event_loop()
                played = await loop.run_in_executor(
                    None, self._play_audio, combined_audio, tts_service.sample_rate
                )
                result["played"] = played

            return result

        except Exception as e:
            logger.error(f"Speech generation error: {e}")
            return {"success": False, "error": str(e)}

    async def _play_tone(self, tone_data: bytes) -> None:
        """Play a short indicator tone through speakers."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._play_audio, tone_data, TONE_SAMPLE_RATE)

    def _play_audio(self, audio_data: bytes, sample_rate: int) -> bool:
        """Play audio through speakers (runs in executor)."""
        try:
            import pyaudio

            self._ensure_pyaudio()
            assert self._pyaudio is not None
            stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=sample_rate,
                output=True,
            )
            stream.write(audio_data)
            stream.stop_stream()
            stream.close()
            return True
        except Exception as e:
            logger.warning(f"Could not play audio: {e}")
            return False

    async def listen_for_speech(
        self,
        voice_profile: Optional[str] = None,
        listen_timeout: float = DEFAULT_LISTEN_TIMEOUT,
        silence_timeout: float = DEFAULT_SILENCE_TIMEOUT,
    ) -> dict:
        """Open mic, detect speech via VAD, transcribe via STT.

        Returns dict with transcript or timeout info.
        """
        if self._mic_lock.locked():
            return {"success": False, "error": "Mic already in use"}

        async with self._mic_lock:
            return await self._do_listen(voice_profile, listen_timeout, silence_timeout)

    async def _do_listen(
        self,
        voice_profile: Optional[str],
        listen_timeout: float,
        silence_timeout: float,
    ) -> dict:
        """Listen using Pipecat's built-in turn detection (LLMUserAggregator).

        Uses the same pattern as the MCP agent: LLMContextAggregatorPair with
        UserTurnStrategies + LocalSmartTurnAnalyzerV3 for proper turn detection.
        The aggregator handles VAD, speech accumulation, and fires
        on_user_turn_stopped with the complete transcript.
        """
        from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
            LocalSmartTurnAnalyzerV3,
        )
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import (
            LLMContextAggregatorPair,
            LLMUserAggregatorParams,
        )
        from pipecat.transports.local.audio import (
            LocalAudioTransport,
            LocalAudioTransportParams,
        )
        from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
            SpeechTimeoutUserTurnStopStrategy,
        )
        from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
            TurnAnalyzerUserTurnStopStrategy,
        )
        from pipecat.turns.user_turn_strategies import UserTurnStrategies
        from shared.profile_manager import get_profile_manager
        from shared.service_factory import create_stt_service_from_config

        try:
            # Resolve STT config from voice profile
            pm = get_profile_manager()
            vp_name = voice_profile or pm.get_default_voice_profile()
            vp = pm.get_voice_profile(vp_name)
            if not vp:
                return {"success": False, "error": f"Voice profile not found: {vp_name}"}

            stt_kwargs = {}
            if vp.stt_model:
                stt_kwargs["model"] = vp.stt_model
            stt = create_stt_service_from_config(vp.stt_provider, **stt_kwargs)

            # Play "listening" indicator tone
            await self._play_tone(get_listen_start_tone())

            # Local mic transport — VAD is on the aggregator, not the transport
            transport = LocalAudioTransport(
                LocalAudioTransportParams(
                    audio_in_enabled=True,
                    audio_out_enabled=False,
                )
            )

            # Use Pipecat's built-in turn detection (same as MCP agent)
            context = LLMContext()
            user_aggregator, _assistant_aggregator = LLMContextAggregatorPair(
                context,
                user_params=LLMUserAggregatorParams(
                    user_turn_strategies=UserTurnStrategies(
                        stop=[
                            SpeechTimeoutUserTurnStopStrategy(
                                user_speech_timeout=2.0,
                            )
                        ]
                    ),
                    vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
                ),
            )

            # Result holder — populated by turn events
            result_holder: dict = {
                "transcript": "",
                "turn_complete": False,
                "speech_started": False,
            }

            @user_aggregator.event_handler("on_user_turn_started")
            async def on_user_turn_started(aggregator, strategy):
                result_holder["speech_started"] = True
                logger.info("User turn started (speech detected)")

            @user_aggregator.event_handler("on_user_turn_stopped")
            async def on_user_turn_stopped(aggregator, strategy, message):
                if message.content:
                    result_holder["transcript"] = message.content
                    result_holder["turn_complete"] = True
                    logger.info(f"Turn complete: {message.content[:80]}")
                    await task.queue_frame(EndFrame())

            pipeline = Pipeline([transport.input(), stt, user_aggregator])
            task = PipelineTask(
                pipeline,
                params=PipelineParams(allow_interruptions=False),
            )
            runner = PipelineRunner()

            # Silence timeout: if no speech at all within silence_timeout, stop.
            # Once speech starts, turn detection handles the rest — no hard cap.
            async def _silence_monitor():
                await asyncio.sleep(silence_timeout)
                if not result_holder["speech_started"]:
                    logger.info(f"No speech after {silence_timeout}s, stopping")
                    await task.queue_frame(EndFrame())

            silence_task = asyncio.create_task(_silence_monitor())

            try:
                await runner.run(task)
            finally:
                silence_task.cancel()
                try:
                    await silence_task
                except asyncio.CancelledError:
                    pass

            if result_holder["transcript"]:
                await self._play_tone(get_listen_stop_tone())
                return {"success": True, "transcript": result_holder["transcript"]}
            else:
                await self._play_tone(get_listen_stop_tone())
                return {"success": True, "transcript": "", "timeout": True}

        except Exception as e:
            logger.error(f"Listen error: {e}")
            return {"success": False, "error": str(e)}

    async def handle_ask(self, request: dict) -> dict:
        """Handle ask command: speak text, then listen for response."""
        text = request.get("text", "")
        voice_profile = request.get("voice_profile")
        provider = request.get("provider")
        voice_id = request.get("voice_id")
        listen_timeout = request.get("listen_timeout", DEFAULT_LISTEN_TIMEOUT)
        silence_timeout = request.get("silence_timeout", DEFAULT_SILENCE_TIMEOUT)

        # Speak first
        if text:
            speak_result = await self.generate_speech(
                text=text,
                voice_profile=voice_profile,
                provider=provider,
                voice_id=voice_id,
            )
            if not speak_result.get("success"):
                return speak_result

            # Brief pause to let speaker audio dissipate before opening mic
            await asyncio.sleep(0.5)

        # Then listen
        listen_result = await self.listen_for_speech(
            voice_profile=voice_profile,
            listen_timeout=listen_timeout,
            silence_timeout=silence_timeout,
        )
        return listen_result

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a client connection."""
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
            elif cmd == "ask":
                result = await self.handle_ask(request)
            elif cmd == "listen":
                result = await self.listen_for_speech(
                    voice_profile=request.get("voice_profile"),
                    listen_timeout=request.get("listen_timeout", DEFAULT_LISTEN_TIMEOUT),
                    silence_timeout=request.get("silence_timeout", DEFAULT_SILENCE_TIMEOUT),
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
        """Main daemon loop."""
        # Clean up stale sockets
        if VOICE_SOCKET_PATH.exists():
            VOICE_SOCKET_PATH.unlink()
        cleanup_legacy_daemon()

        if not await self.initialize_tts():
            logger.error("TTS initialization failed, exiting")
            return

        self.server = await asyncio.start_unix_server(
            self.handle_client, path=str(VOICE_SOCKET_PATH)
        )
        VOICE_PID_FILE.write_text(str(os.getpid()))
        logger.info(f"Voice daemon listening on {VOICE_SOCKET_PATH} (PID: {os.getpid()})")

        try:
            while self.running:
                await asyncio.sleep(1.0)
                if self.idle_timeout and time.time() - self.last_activity > self.idle_timeout:
                    logger.info(f"Idle timeout ({self.idle_timeout}s), shutting down")
                    self.running = False
        finally:
            self.server.close()
            await self.server.wait_closed()
            VOICE_SOCKET_PATH.unlink(missing_ok=True)
            VOICE_PID_FILE.unlink(missing_ok=True)
            if self._pyaudio:
                self._pyaudio.terminate()
            logger.info("Voice daemon stopped")


def start_daemon(wait: bool = True) -> bool:
    """Start daemon in background via double-fork."""
    if daemon_is_running():
        logger.info("Daemon already running")
        return True

    VOICE_SOCKET_PATH.unlink(missing_ok=True)
    VOICE_PID_FILE.unlink(missing_ok=True)

    pid = os.fork()
    if pid > 0:
        if not wait:
            logger.info(f"Daemon starting (PID: {pid})")
            return True
        for _ in range(20):
            time.sleep(0.5)
            if VOICE_SOCKET_PATH.exists():
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

    VOICE_PID_FILE.write_text(str(os.getpid()))

    daemon = VoiceDaemon(idle_timeout=IDLE_TIMEOUT)

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
    """Stop the daemon gracefully."""
    import socket as socket_mod

    if not daemon_is_running():
        logger.info("Daemon not running")
        return True

    try:
        sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        sock.connect(str(VOICE_SOCKET_PATH))
        send_message(sock, {"cmd": "stop"})
        recv_message(sock, timeout=5.0)
        sock.close()

        for _ in range(10):
            if not daemon_is_running():
                logger.info("Daemon stopped")
                return True
            time.sleep(0.5)

        pid = int(VOICE_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGKILL)
        VOICE_PID_FILE.unlink(missing_ok=True)
        VOICE_SOCKET_PATH.unlink(missing_ok=True)
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
    sock.connect(str(VOICE_SOCKET_PATH))

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
    parser = __import__("argparse").ArgumentParser(description="Voice Daemon")
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
        from shared.profile_manager import get_profile_manager

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
        daemon = VoiceDaemon(idle_timeout=None)
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
            pid = VOICE_PID_FILE.read_text().strip()
            print(f"Voice daemon running (PID: {pid})")
        else:
            print("Voice daemon not running")
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
