"""Pipeline processors that persist each turn to the call's convo (WIN-534).

- ``TalkyUserTranscriptProcessor`` sees talky's ``UserTurnTextFrame`` (talky
  doesn't emit pipecat's ``TranscriptionFrame``, which the stock user
  processor keys on).
- ``ConvoAssistantTranscriptProcessor`` is pipecat's assistant processor
  (aggregates ``TTSTextFrame`` until the bot stops / is interrupted) plus a
  ``flush()`` for teardown, when no closing frame ever arrives.

Both pass every frame through unchanged.
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionUpdateFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.transcript_processor import AssistantTranscriptProcessor
from pipecat.utils.string import concatenate_aggregated_text

from talky.convos.session import ConvoSession
from talky.server.turn import UserTurnTextFrame


class TalkyUserTranscriptProcessor(FrameProcessor):
    def __init__(self, session: ConvoSession, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session = session

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserTurnTextFrame) and not frame.replay:
            self._session.append("user", frame.text, timestamp=frame.timestamp)
        await self.push_frame(frame, direction)


class ConvoAssistantTranscriptProcessor(AssistantTranscriptProcessor):
    def __init__(self, session: ConvoSession, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session = session
        self._fenced = False

        @self.event_handler("on_transcript_update")
        async def _persist(_processor, frame: TranscriptionUpdateFrame):  # noqa: ARG001
            if self._fenced:
                return
            for msg in frame.messages or []:
                if msg.role == "assistant":
                    self._session.append("assistant", msg.content or "")

    def fence(self) -> None:
        """Opening another chat mid-call (WIN-582): keep what was said so far for the chat being left, and
        drop whatever the old backend's still-playing reply says afterwards, until the interruption (or the
        next backend's first reply) arrives -- it must not be saved into the chat opened next."""
        self.flush()
        self._fenced = True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if self._fenced and isinstance(frame, (InterruptionFrame, LLMFullResponseStartFrame)):
            self._fenced = False
            self._current_assistant_text_parts = []
            self._assistant_text_start_time = None
        # The reply is done (or cancelled): clear the interrupted-turn marker
        # so a daemon crash afterwards doesn't resume a finished turn (WIN-536).
        if isinstance(frame, (LLMFullResponseEndFrame, InterruptionFrame)):
            self._session.mark_turn_complete()
        await super().process_frame(frame, direction)

    def flush(self) -> None:
        """Persist whatever the bot had said so far; safe to call after teardown."""
        try:
            content = concatenate_aggregated_text(self._current_assistant_text_parts)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"ConvoAssistantTranscriptProcessor.flush: {e}")
            return
        self._current_assistant_text_parts = []
        self._assistant_text_start_time = None
        if content and content.strip():
            self._session.append("assistant", content)
