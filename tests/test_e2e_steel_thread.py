"""E2E steel thread — the thinnest end-to-end voice-pipeline test that works.

Ticket 0179 (parent ad0e). This proves the loop closes: a fake user turn
goes INTO a real Pipecat pipeline, through the echo backend (the "fake
brain"), and the assistant response comes back OUT where we can assert on
it — with no microphone, no STT, no TTS, no real LLM, no browser.

What it does NOT do yet (the "build around it" layer):
  - real audio in (WAV -> VAD -> STT)
  - TTS audio out capture
  - interruption / barge-in timing
  - a scenario file format or `talky test` CLI

Those come once this thread holds. Here we capture the response *before
TTS* — the echo backend's TextFrame as it leaves the LLM slot — because
that's the thinnest point that proves user -> pipeline -> response.

Pipeline under test (minimal, no transport):

    [ EchoLLMService ]  ->  [ CaptureProcessor ]

We inject a ``UserTurnTextFrame`` (the exact frame talky's turn detector
emits on turn-stop), then an ``EndFrame`` to drain and stop. The capture
processor records every ``TextFrame`` it sees downstream of the echo.
"""

from __future__ import annotations

import pytest
from pipecat.frames.frames import EndFrame, Frame, TextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talky.backends.echo import EchoLLMService
from talky.server.turn import UserTurnTextFrame


class CaptureProcessor(FrameProcessor):
    """Records assistant ``TextFrame`` text as it flows past, then forwards it.

    This is the "recorder" end of the steel thread, in miniature: a tap
    that makes the otherwise-ephemeral pipeline output assertable. The
    full substrate will replace this with framework observers + a JSONL
    sink, but for the thinnest thread a list is enough.
    """

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            self.captured.append(frame.text)
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_one_turn_completes_through_real_pipeline() -> None:
    """A fake user turn produces the echoed assistant response. The steel thread."""
    echo = EchoLLMService(template="you said: {text}")
    capture = CaptureProcessor()

    task = PipelineTask(Pipeline([echo, capture]))

    # Inject one user turn (as the turn detector would on turn-stop),
    # then EndFrame to drain and stop the runner.
    await task.queue_frames(
        [
            UserTurnTextFrame(text="hello", timestamp=0.0),
            EndFrame(),
        ]
    )

    await PipelineRunner(handle_sigint=False).run(task)

    assert "you said: hello" in capture.captured, (
        f"expected echoed response, captured={capture.captured!r}"
    )
