"""talky transcribe — Mic → VAD → STT → stdout/file.

Minimal Pipecat pipeline for raw transcription. No LLM, no browser.
"""

import asyncio
import json
import sys
from datetime import datetime
from typing import Optional, TextIO

from loguru import logger
from pipecat.frames.frames import EndFrame, Frame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from shared.service_factory import create_stt_service_from_config
from shared.voice_config import create_vad_analyzer


class TranscriptionWriter(FrameProcessor):
    """Intercepts TranscriptionFrames and writes formatted text to stdout or file."""

    def __init__(
        self,
        output: Optional[str] = None,
        fmt: str = "raw",
        timestamp: bool = False,
        **kwargs,
    ):
        super().__init__(name="TranscriptionWriter", **kwargs)
        self._fmt = fmt
        self._timestamp = timestamp or (fmt == "markdown")
        self._file: Optional[TextIO] = None
        self._output_path = output

        if output:
            self._file = open(output, "a")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._write(frame)

        await self.push_frame(frame, direction)

    def _write(self, frame: TranscriptionFrame):
        text = frame.text.strip()
        now = datetime.now()

        if self._fmt == "jsonl":
            line = json.dumps({"timestamp": now.isoformat(), "text": text})
        elif self._fmt == "markdown":
            ts = now.strftime("%H:%M:%S")
            line = f"**[{ts}]** {text}\n"
        else:  # raw
            if self._timestamp:
                ts = now.strftime("%H:%M:%S")
                line = f"[{ts}] {text}"
            else:
                line = text

        dest = self._file or sys.stdout
        print(line, file=dest, flush=True)

    async def cleanup(self):
        await super().cleanup()
        if self._file:
            self._file.close()
            self._file = None


async def transcribe(
    stt_provider: Optional[str] = None,
    stt_model: Optional[str] = None,
    voice_profile: Optional[str] = None,
    output: Optional[str] = None,
    fmt: str = "raw",
    timestamp: bool = False,
):
    """Run transcription pipeline: mic → VAD → STT → writer."""
    from shared.profile_manager import get_profile_manager

    pm = get_profile_manager()

    # Resolve STT provider from voice profile if not explicit
    if not stt_provider:
        vp_name = voice_profile or pm.get_default_voice_profile()
        vp = pm.get_voice_profile(vp_name)
        if not vp:
            raise ValueError(f"Voice profile not found: {vp_name}")
        stt_provider = vp.stt_provider
        if not stt_model:
            stt_model = vp.stt_model

    # Build STT service
    stt_kwargs = {}
    if stt_model:
        stt_kwargs["model"] = stt_model
    stt = create_stt_service_from_config(stt_provider, **stt_kwargs)

    # Local mic transport (input only)
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=False,
            vad_analyzer=create_vad_analyzer(),
        )
    )

    writer = TranscriptionWriter(output=output, fmt=fmt, timestamp=timestamp)

    pipeline = Pipeline([transport.input(), stt, writer])
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=False))
    runner = PipelineRunner()

    # Graceful Ctrl+C handling
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Stopping transcription...")
        stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        try:
            import signal
            loop.add_signal_handler(getattr(signal, sig_name), _signal_handler)
        except (ValueError, OSError):
            pass  # Not available on all platforms

    async def _monitor_stop():
        await stop_event.wait()
        await task.queue_frame(EndFrame())

    asyncio.create_task(_monitor_stop())

    if not output:
        # Hint: transcribing to stdout — suppress logging noise
        logger.remove()

    await runner.run(task)
