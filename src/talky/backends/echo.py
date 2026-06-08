"""Echo backend — deterministic fake LLM for E2E voice-pipeline testing.

Part of the E2E test substrate (ticket 0179 / parent ad0e). This is the
"fake brain" end of the steel thread: it plugs into an ``LLMSwitcher`` as
a peer of the real inference backends (openclaw, opencode, ...) and the
``MCPDriverLLMService`` null passthrough, but does **no inference and no
network I/O**. It simply echoes the user's turn back as the assistant
response.

Why it exists
-------------
Voice features (turn-taking, interruption, latency) can only be verified
today by running the daemon and talking to it — which an agent cannot do.
A deterministic backend lets a test drive a real pipeline end-to-end and
assert on what came back, with zero cost and zero non-determinism.

Frame handling
--------------
| Frame | Behavior |
|-------|----------|
| ``UserTurnTextFrame`` | **Consumed.** Emit the canonical LLM response sequence: ``LLMFullResponseStartFrame`` → ``TextFrame("you said: {text}")`` → ``LLMFullResponseEndFrame``. This is the same emit shape the real backends use (see opencode.py), so it flows to TTS exactly like a real response. |
| ``InterruptionFrame`` | Delegated to base, then re-pushed downstream (matches MCPDriverLLMService). |
| Anything else | Passed through unchanged. |

The response template is configurable so later variants (delayed, long,
scripted) can subclass or parameterize without changing the seam.
``StreamingEchoLLMService`` (below) is the "long" variant: it streams the
response token-by-token and cancels in-flight emission on interruption,
which is what barge-in / "did the agent shut up fast" tests need.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from talky.server.turn import UserTurnTextFrame


class EchoLLMService(LLMService):
    """Deterministic echo backend for E2E pipeline tests.

    Consumes ``UserTurnTextFrame`` and emits a fixed-shape response that
    echoes the text back. Designed to sit inside an ``LLMSwitcher`` as a
    peer of real LLM services and the null ``MCPDriverLLMService``.

    Config keys (from llm-backends.yaml ``config:``):
      template: response format string with a ``{text}`` placeholder.
                Defaults to ``"you said: {text}"``.
    """

    def __init__(
        self,
        *,
        template: str = "you said: {text}",
        name: str = "echo",
        **kwargs: Any,
    ) -> None:
        """Create an EchoLLMService.

        Args:
            template: Response format string. ``{text}`` is replaced with
                the user's turn text.
            name: Human-readable name used in logs.
            **kwargs: Forwarded to ``LLMService.__init__``.
        """
        super().__init__(**kwargs)
        self._template = template
        self._echo_name = name

    def __repr__(self) -> str:  # noqa: D401
        return f"EchoLLMService({self._echo_name})"

    def render(self, text: str) -> str:
        """Render the response for a given user turn. Override for variants."""
        return self._template.format(text=text)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Process a frame flowing through the pipeline."""
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            if frame.text:
                response = self.render(frame.text)
                logger.info(f"{self!r}: echoing {frame.text[:60]!r} -> {response[:60]!r}")
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(TextFrame(response))
                await self.push_frame(LLMFullResponseEndFrame())
            else:
                logger.debug(f"{self!r}: empty user turn, skipping")
            return

        await self.push_frame(frame, direction)


class StreamingEchoLLMService(EchoLLMService):
    """Echo backend that streams its response token-by-token, interruptibly.

    The "long" variant of the echo backend (ticket 0179). Where
    ``EchoLLMService`` emits its whole response in one push,
    ``StreamingEchoLLMService`` emits one ``TextFrame`` per whitespace
    token with a configurable inter-token delay, from a managed background
    task. This gives a response that takes real wall-clock time to come
    out — so there is something *in flight* to interrupt.

    On ``InterruptionFrame`` the streaming task is cancelled (via the
    framework's ``cancel_task``), so emission stops mid-stream. This is the
    same shape the real interruptible backends use — hermes/claude_code
    abort their in-flight streaming task on interruption — but deterministic
    and offline. It is the substrate for barge-in / "did the agent shut up
    fast" tests, which today can only be caught by dogfooding.

    Config keys (in addition to ``EchoLLMService``):
      token_delay_s: seconds to wait between emitted tokens. Default 0.05.
    """

    def __init__(
        self,
        *,
        token_delay_s: float = 0.05,
        name: str = "echo-long",
        **kwargs: Any,
    ) -> None:
        """Create a StreamingEchoLLMService.

        Args:
            token_delay_s: Seconds between emitted tokens. Controls how long
                the response takes to stream, i.e. the interruption window.
            name: Human-readable name used in logs.
            **kwargs: Forwarded to ``EchoLLMService.__init__``.
        """
        super().__init__(name=name, **kwargs)
        self._token_delay_s = token_delay_s
        self._stream_task: asyncio.Task | None = None

    def __repr__(self) -> str:  # noqa: D401
        return f"StreamingEchoLLMService({self._echo_name})"

    async def _stream_response(self, response: str) -> None:
        """Emit the response one token at a time, with inter-token delay.

        Wrapped in the canonical LLM response sequence so it flows to TTS
        exactly like a real streamed response. Cancellation (from an
        ``InterruptionFrame``) stops emission partway and skips the End
        frame — the same way a real backend leaves a response unfinished
        when the user barges in.
        """
        await self.push_frame(LLMFullResponseStartFrame())
        for token in response.split():
            await asyncio.sleep(self._token_delay_s)
            await self.push_frame(TextFrame(token))
        await self.push_frame(LLMFullResponseEndFrame())

    async def _cancel_stream(self) -> None:
        """Cancel any in-flight streaming task. Idempotent."""
        if self._stream_task is not None:
            await self.cancel_task(self._stream_task)
            self._stream_task = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Process a frame, streaming responses and honoring interruptions."""
        # Deliberately bypass EchoLLMService.process_frame: that one emits
        # the whole response in one push. We stream instead, and must own
        # the InterruptionFrame + UserTurnTextFrame handling ourselves.
        await LLMService.process_frame(self, frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self._cancel_stream()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            if frame.text:
                response = self.render(frame.text)
                logger.info(
                    f"{self!r}: streaming {frame.text[:60]!r} -> {response[:60]!r}"
                )
                # New turn supersedes any in-flight response.
                await self._cancel_stream()
                self._stream_task = self.create_task(self._stream_response(response))
            else:
                logger.debug(f"{self!r}: empty user turn, skipping")
            return

        await self.push_frame(frame, direction)
