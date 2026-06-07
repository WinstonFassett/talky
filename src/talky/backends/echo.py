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
"""

from __future__ import annotations

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
