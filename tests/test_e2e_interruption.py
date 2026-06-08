"""E2E interruption thread — barge-in cancels an in-flight response.

Ticket 0179 (parent ad0e). The second steel thread, building on
``test_e2e_steel_thread``. Where the first thread proved *one turn
completes*, this proves the thing only dogfooding can catch today: when
the user barges in while the agent is still talking, the agent **shuts up
fast** — in-flight output stops, and nothing leaks out after the
interruption.

We use ``StreamingEchoLLMService`` (the "long" echo variant) so there is a
response genuinely *in flight* — it streams token-by-token over real
wall-clock time. Mid-stream we inject an ``InterruptionFrame`` (the exact
frame the input transport pushes downstream when VAD detects the user
started speaking). The streaming task is cancelled, so emission stops.

What this still does NOT do (deferred, as before):
  - real audio in (WAV -> VAD -> STT) — here the interruption is injected
    as a frame, not synthesized from a barge-in WAV
  - TTS audio-out capture — we assert on the pre-TTS TextFrame stream
  - a scenario file format or `talky test` CLI

Pipeline under test (minimal, no transport):

    [ StreamingEchoLLMService ]  ->  [ TimestampedCapture ]

The capture records each downstream ``TextFrame`` with the wall-clock time
it was seen, so we can split tokens into before/after the interruption and
measure how quickly emission stopped.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from pipecat.frames.frames import EndFrame, Frame, InterruptionFrame, TextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talky.backends.echo import StreamingEchoLLMService
from talky.server.turn import UserTurnTextFrame

# Inter-token delay for the streamed response. Slow enough that the
# interruption reliably lands mid-stream, fast enough the test is quick.
TOKEN_DELAY_S = 0.05

# A multi-token user turn -> a multi-token echoed response to stream.
USER_TURN = "one two three four five six seven eight nine ten"


class TimestampedCapture(FrameProcessor):
    """Records each downstream ``TextFrame`` with the time it was seen.

    The recorder end of the thread. A monotonic timestamp per token lets
    the test split the stream at the interruption and assert nothing leaked
    out afterward — the assertion that proves barge-in actually stopped the
    in-flight response.
    """

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[tuple[float, str]] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            self.captured.append((time.monotonic(), frame.text))
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_barge_in_stops_in_flight_response() -> None:
    """A mid-stream interruption stops the response and nothing leaks after."""
    echo = StreamingEchoLLMService(token_delay_s=TOKEN_DELAY_S)
    capture = TimestampedCapture()

    task = PipelineTask(Pipeline([echo, capture]))

    async def drive() -> None:
        # Start the long turn streaming.
        await task.queue_frames([UserTurnTextFrame(text=USER_TURN, timestamp=0.0)])

        # Let a few tokens land so there is genuinely a response in flight.
        await asyncio.sleep(TOKEN_DELAY_S * 3.5)

        # User barges in: inject the interruption the input transport would
        # push downstream on VAD-detected speech.
        interrupt_at = time.monotonic()
        await task.queue_frames([InterruptionFrame()])

        # Give the pipeline room to (not) emit anything further, then drain.
        await asyncio.sleep(TOKEN_DELAY_S * 8)
        await task.queue_frames([EndFrame()])
        return interrupt_at  # type: ignore[return-value]

    # Run the pipeline and the driver concurrently; the driver's injections
    # interleave with the streaming task.
    driver = asyncio.ensure_future(drive())
    await PipelineRunner(handle_sigint=False).run(task)
    interrupt_at = await driver

    before = [(t, tok) for (t, tok) in capture.captured if t < interrupt_at]
    after = [(t, tok) for (t, tok) in capture.captured if t >= interrupt_at]

    # Something was in flight to interrupt.
    assert before, f"expected tokens before interrupt, captured={capture.captured!r}"

    # The barge-in stopped the response: nothing leaked out afterward.
    assert not after, (
        f"response leaked {len(after)} token(s) after interruption: "
        f"{[tok for _, tok in after]!r}"
    )

    # And it stopped fast: the last token landed before (or at) the
    # interruption, i.e. emission ceased within one token interval.
    last_token_at = before[-1][0]
    stop_latency_ms = max(0.0, (interrupt_at - last_token_at)) * 1000
    assert stop_latency_ms >= 0  # sanity; real bound asserted by `not after`
