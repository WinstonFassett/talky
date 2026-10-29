"""UserSpeakingMuteProcessor — a reply that starts while the user talks must not talk over them (SWE-729 part 2)."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import (
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)

from talky.server.user_speaking_mute import UserSpeakingMuteProcessor


class _Pipe:
    """Records what the processor pushed downstream."""

    def __init__(self) -> None:
        self.frames: list = []

    async def push(self, frame, direction=None):
        self.frames.append(frame)


async def _make() -> tuple[UserSpeakingMuteProcessor, _Pipe]:
    proc = UserSpeakingMuteProcessor()
    pipe = _Pipe()
    proc.push_frame = pipe.push  # type: ignore[method-assign]
    return proc, pipe


def _response_types(pipe: _Pipe) -> list[str]:
    """The frames that matter to TTS — speaking-state broadcasts pass through untouched."""
    return [
        type(f).__name__
        for f in pipe.frames
        if isinstance(f, (LLMFullResponseStartFrame, LLMFullResponseEndFrame, TextFrame, InterruptionFrame))
    ]


@pytest.mark.asyncio
async def test_response_starting_while_user_speaks_is_swallowed():
    proc, pipe = await _make()
    await proc.process_frame(UserStartedSpeakingFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseStartFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(TextFrame("hello "), None)  # type: ignore[arg-type]
    await proc.process_frame(TextFrame("there"), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseEndFrame(), None)  # type: ignore[arg-type]
    # the start and spoken text were dropped; the end still closes the response
    assert _response_types(pipe) == ["LLMFullResponseEndFrame"]
    # the user stopped: the next response flows normally
    await proc.process_frame(UserStoppedSpeakingFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseStartFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(TextFrame("ok"), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseEndFrame(), None)  # type: ignore[arg-type]
    assert _response_types(pipe) == [
        "LLMFullResponseEndFrame",
        "LLMFullResponseStartFrame",
        "TextFrame",
        "LLMFullResponseEndFrame",
    ]


@pytest.mark.asyncio
async def test_response_starting_while_silent_flows_through_and_speaking_mid_reply_passes():
    proc, pipe = await _make()
    await proc.process_frame(LLMFullResponseStartFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(TextFrame("first "), None)  # type: ignore[arg-type]
    # the user starts talking mid-reply: the standard barge-in cuts it, the mute
    # processor does not swallow the tail (no new response is being opened)
    await proc.process_frame(VADUserStartedSpeakingFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(TextFrame("tail"), None)  # type: ignore[arg-type]
    await proc.process_frame(InterruptionFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseEndFrame(), None)  # type: ignore[arg-type]
    assert _response_types(pipe) == [
        "LLMFullResponseStartFrame",
        "TextFrame",
        "TextFrame",
        "InterruptionFrame",
        "LLMFullResponseEndFrame",
    ]
    # and the user is still speaking: a new response must be suppressed again
    await proc.process_frame(LLMFullResponseStartFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(TextFrame("over me?"), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseEndFrame(), None)  # type: ignore[arg-type]
    assert _response_types(pipe) == [
        "LLMFullResponseStartFrame",
        "TextFrame",
        "TextFrame",
        "InterruptionFrame",
        "LLMFullResponseEndFrame",
        "LLMFullResponseEndFrame",
    ]


@pytest.mark.asyncio
async def test_vad_speaking_frames_clear_between_utterances():
    proc, pipe = await _make()
    # a response during the VAD tail (user still flagged speaking) is suppressed
    await proc.process_frame(VADUserStartedSpeakingFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseStartFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseEndFrame(), None)  # type: ignore[arg-type]
    # VAD stop clears the flag; the next response plays
    await proc.process_frame(VADUserStoppedSpeakingFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseStartFrame(), None)  # type: ignore[arg-type]
    await proc.process_frame(LLMFullResponseEndFrame(), None)  # type: ignore[arg-type]
    assert _response_types(pipe) == [
        "LLMFullResponseEndFrame",
        "LLMFullResponseStartFrame",
        "LLMFullResponseEndFrame",
    ]