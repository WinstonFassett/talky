"""LLMSwitcher that can take a new LLM after the pipeline has started (WIN-556).

Pipecat's ``ServiceSwitcher`` is a ``ParallelPipeline`` whose branches are fixed
at construction, so every Backend had to be built (and its agent process
spawned) up front. ``LazyLLMSwitcher.add_llm`` builds the extra branch at
runtime: it sets the branch up with the task's original setup and replays the
pipeline's ``StartFrame`` into it, so the new service starts exactly as if it
had been there from the beginning.
"""

from __future__ import annotations

from typing import Any, List, Optional

from loguru import logger
from pipecat.frames.frames import Frame, StartFrame
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.pipeline import Pipeline, PipelineSink, PipelineSource
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.llm_service import LLMService


class LazyLLMSwitcher(LLMSwitcher):
    def __init__(self, llms: List[LLMService], strategy_type: Any):
        super().__init__(llms, strategy_type)
        self._setup_args: Optional[FrameProcessorSetup] = None
        self._start_frame: Optional[StartFrame] = None

    async def setup(self, setup: FrameProcessorSetup):
        self._setup_args = setup
        await super().setup(setup)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, StartFrame):
            self._start_frame = frame
        await super().process_frame(frame, direction)

    async def add_llm(self, llm: LLMService) -> None:
        """Add ``llm`` as a new branch of the running switcher and start it."""
        if llm in self._services:
            return
        if self._setup_args is None or self._start_frame is None:
            raise RuntimeError("LazyLLMSwitcher.add_llm called before the pipeline started")

        start_frame = self._start_frame
        index = len(self._pipelines)

        async def _sink_push(frame: Frame, direction: FrameDirection) -> None:
            # The replayed StartFrame already left the switcher once; swallow
            # it so it doesn't disturb the parallel pipeline's lifecycle
            # bookkeeping (frame counters / synchronization).
            if frame is start_frame:
                logger.info(f"LazyLLMSwitcher: {llm} started")
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
