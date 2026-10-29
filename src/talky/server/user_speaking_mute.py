"""Suppress a bot reply that would start while the user is still talking (SWE-729, part 2).

Pipecat only interrupts bot speech at VAD speech *onset*. When the user is already
mid-utterance and a reply arrives, no new onset fires — the reply plays over the
user. Every backend (t3, pi, claude, hermes, opencode) is exposed to this; it is a
pipeline property, not a backend bug, so the fix lives here, between the LLM
switcher and the TTS switcher, and applies to all integrations.

The processor tracks the user-speaking frames the turn detector broadcasts and
swallows a response that *starts* while the user is speaking:
``LLMFullResponseStartFrame`` (never open it) and its ``TextFrame``s. The
``LLMFullResponseEndFrame`` still passes so response state closes downstream. A
response that starts while the user is silent flows through untouched, and a
barge-in mid-reply keeps working through the normal ``InterruptionFrame`` path.
"""

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class UserSpeakingMuteProcessor(FrameProcessor):
    """Swallow a bot response that starts while the user is speaking."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._user_speaking = False
        self._suppressing = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
            self._user_speaking = True
        elif isinstance(frame, (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)):
            self._user_speaking = False

        if isinstance(frame, InterruptionFrame):
            self._suppressing = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            if self._user_speaking:
                # The reply would talk over the user: never open it.
                self._suppressing = True
                return
            self._suppressing = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            # Always close the response so downstream state (turn-complete,
            # bot-speaking) does not leak.
            self._suppressing = False
            await self.push_frame(frame, direction)
            return

        if self._suppressing and isinstance(frame, TextFrame):
            return  # the suppressed response's spoken text

        await self.push_frame(frame, direction)