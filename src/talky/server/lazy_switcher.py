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
    # How long add_llm waits for the pipeline to start, and for the new
    # service's own start() (which may spawn an agent process).
    START_TIMEOUT_SECS = 30.0

    def __init__(self, llms: List[LLMService], strategy_type: Any):
        super().__init__(llms, strategy_type)
        self._setup_args: Optional[FrameProcessorSetup] = None
        self._start_frame: Optional[StartFrame] = None
        self._started = asyncio.Event()
        self._ending = False

    async def setup(self, setup: FrameProcessorSetup):
        self._setup_args = setup
        await super().setup(setup)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, (EndFrame, CancelFrame)):
            # Set before the frame fans out: a branch added after this would
            # miss it and its agent process would never be stopped.
            self._ending = True
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            # Recorded only after the fan-out, so a branch added meanwhile
            # can't get the StartFrame twice.
            self._start_frame = frame
            self._started.set()

    async def add_llm(self, llm: LLMService) -> bool:
        """Add ``llm`` as a new branch of the running switcher and start it.

        Returns once ``llm`` has started, so callers can hand it frames
        directly. Returns False (and adds nothing) if the pipeline is ending
        or never started.
        """
        if llm in self._services:
            return True
        if not self._started.is_set():
            try:
                await asyncio.wait_for(self._started.wait(), self.START_TIMEOUT_SECS)
            except asyncio.TimeoutError:
                logger.warning(f"LazyLLMSwitcher: pipeline never started — not adding {llm}")
                return False
        if self._ending or self._setup_args is None or self._start_frame is None:
            return False

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

        # ServiceSwitcher and its strategy share one services list.
        self._services.append(llm)
        if llm not in self._strategy.services:
            self._strategy.services.append(llm)
        self._pipelines.append(pipeline)
        await pipeline.queue_frame(start_frame, FrameDirection.DOWNSTREAM)
        try:
            await asyncio.wait_for(llm_started.wait(), self.START_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            logger.warning(f"LazyLLMSwitcher: {llm} didn't finish starting in {self.START_TIMEOUT_SECS:.0f}s")
        return True
