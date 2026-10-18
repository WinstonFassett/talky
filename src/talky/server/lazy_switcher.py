"""LLMSwitcher that can take a new LLM after the pipeline has started (WIN-556).

Pipecat's ``ServiceSwitcher`` is a ``ParallelPipeline`` whose branches are fixed
at construction, so every Backend had to be built (and its agent process
spawned) up front. ``LazyLLMSwitcher.add_llm`` builds the extra branch at
runtime: it sets the branch up with the task's original setup and replays the
pipeline's ``StartFrame`` into it, so the new service starts exactly as if it
had been there from the beginning.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from loguru import logger
from pipecat.frames.frames import CancelFrame, EndFrame, Frame, StartFrame
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.pipeline import Pipeline, PipelineSink, PipelineSource
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.llm_service import LLMService


class LazyLLMSwitcher(LLMSwitcher):
    # Upper bound on waiting for the pipeline's start, or a backend's own
    # start() (which may spawn an agent process). Every wait also ends as soon
    # as the pipeline ends, so a dead pipeline never holds a caller this long.
    START_TIMEOUT_SECS = 30.0

    def __init__(self, llms: List[LLMService], strategy_type: Any):
        super().__init__(llms, strategy_type)
        self._setup_args: Optional[FrameProcessorSetup] = None
        self._start_frame: Optional[StartFrame] = None
        self._started = asyncio.Event()
        self._ended = asyncio.Event()
        # Added LLM → set once its start() finished. Services built with the
        # switcher started with the pipeline and aren't listed.
        self._llm_started: dict[LLMService, asyncio.Event] = {}

    async def setup(self, setup: FrameProcessorSetup):
        self._setup_args = setup
        await super().setup(setup)

    async def cleanup(self):
        self._ended.set()
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, (EndFrame, CancelFrame)):
            # Set before the frame fans out: a branch added after this would
            # miss it and its agent process would never be stopped.
            self._ended.set()
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            # Recorded only after the fan-out, so a branch added meanwhile
            # can't get the StartFrame twice.
            self._start_frame = frame
            self._started.set()

    async def _wait(self, event: asyncio.Event) -> bool:
        """Wait for ``event`` unless the pipeline ends first; True iff it fired."""
        if event.is_set():
            return True
        waiters = [asyncio.ensure_future(event.wait()), asyncio.ensure_future(self._ended.wait())]
        try:
            await asyncio.wait(waiters, timeout=self.START_TIMEOUT_SECS, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()
        return event.is_set()

    async def add_llm(self, llm: LLMService) -> bool:
        """Add ``llm`` as a new branch of the running switcher and start it.

        Returns once the branch is in place and its start is queued; frames
        routed to it queue behind its start. Use ``wait_started`` before
        handing it frames directly. Returns False (and adds nothing) if the
        pipeline is ending or never started.
        """
        if llm in self._services:
            return True
        if not await self._wait(self._started) or self._ended.is_set():
            logger.warning(f"LazyLLMSwitcher: pipeline not running — not adding {llm}")
            return False
        assert self._setup_args is not None and self._start_frame is not None

        start_frame = self._start_frame
        index = len(self._pipelines)
        llm_started = asyncio.Event()

        async def _sink_push(frame: Frame, direction: FrameDirection) -> None:
            # The replayed StartFrame already left the switcher once; swallow
            # it so it doesn't disturb the parallel pipeline's lifecycle
            # bookkeeping (frame counters / synchronization).
            if frame is start_frame:
                logger.info(f"LazyLLMSwitcher: {llm} started")
                llm_started.set()
                return
            await self._pipeline_sink_push_frame(frame, direction)

        source = PipelineSource(self._parallel_push_frame, name=f"{self}::Source{index}")
        sink = PipelineSink(_sink_push, name=f"{self}::Sink{index}")
        pipeline = Pipeline(self._make_pipeline_definition(llm, self._strategy), source=source, sink=sink)
        await pipeline.setup(self._setup_args)
        if self._ended.is_set():  # ended while setting up: it would miss End/Cancel
            await pipeline.cleanup()
            return False

        # ServiceSwitcher and its strategy share one services list.
        self._services.append(llm)
        if llm not in self._strategy.services:
            self._strategy.services.append(llm)
        self._llm_started[llm] = llm_started
        self._pipelines.append(pipeline)
        await pipeline.queue_frame(start_frame, FrameDirection.DOWNSTREAM)
        return True

    async def wait_started(self, llm: LLMService) -> bool:
        """True once ``llm`` has started; False if it didn't (pipeline ended, timeout)."""
        event = self._llm_started.get(llm)
        if event is None:
            return llm in self._services and self._started.is_set()
        ok = await self._wait(event)
        if not ok:
            logger.warning(f"LazyLLMSwitcher: {llm} didn't finish starting")
        return ok
