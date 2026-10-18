"""LazyLLMSwitcher: a backend added mid-call starts and takes turns (WIN-556)."""

from __future__ import annotations

import asyncio

import pytest
from pipecat.frames.frames import EndFrame, Frame, ManuallySwitchServiceFrame, StartFrame, TextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talky.backends.echo import EchoLLMService
from talky.server.lazy_switcher import LazyLLMSwitcher
from talky.server.turn import UserTurnTextFrame


class _CountingEcho(EchoLLMService):
    def __init__(self, name: str):
        super().__init__(template=name + ": {text}")
        self.starts = 0

    async def start(self, frame: StartFrame):
        self.starts += 1
        await super().start(frame)


class _Collector(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.texts: list[str] = []
        self.got = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            self.texts.append(frame.text)
            self.got.set()
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_added_llm_starts_once_and_replies():
    first = _CountingEcho("first")
    later = _CountingEcho("later")
    switcher = LazyLLMSwitcher(llms=[first], strategy_type=ServiceSwitcherStrategyManual)
    sink = _Collector()
    task = PipelineTask(Pipeline([switcher, sink]), idle_timeout_secs=None, cancel_on_idle_timeout=False)
    runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
    try:
        for _ in range(100):
            if switcher._start_frame is not None:  # noqa: SLF001
                break
            await asyncio.sleep(0.02)
        assert later.starts == 0, "a backend nobody switched to must not start"

        await switcher.add_llm(later)
        await switcher.add_llm(later)  # idempotent
        await task.queue_frames([ManuallySwitchServiceFrame(service=later), UserTurnTextFrame(text="hi")])
        await asyncio.wait_for(sink.got.wait(), timeout=5)

        assert sink.texts == ["later: hi"]
        assert later.starts == 1
        assert first.starts == 1
    finally:
        await task.queue_frame(EndFrame())
        await asyncio.wait_for(runner, timeout=5)
