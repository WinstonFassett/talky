"""Script backend — a deterministic fake agent for UI conformance tests (WIN-536).

A sibling of the echo backend. Where echo only repeats the user's turn, this
one plays a small script written inside the turn itself, so a browser test can
make the real pipeline produce what a real agent produces — streamed tokens,
reasoning, tool calls, a reply with code, an empty reply, a backend failure —
and assert on how the client renders it. No inference, no network.

A turn with no ``[[...]]`` directive streams ``you said: {text}``. Otherwise
each directive runs in order:

| Directive               | What the pipeline sees                                       |
|-------------------------|--------------------------------------------------------------|
| ``[[sleep S]]``         | nothing for S seconds (e.g. before the first token)          |
| ``[[say TEXT]]``        | TEXT streamed word by word                                   |
| ``[[stream N]]``        | N numbered words streamed one by one                         |
| ``[[lines N]]``         | N short numbered sentences streamed (a tall, multi-part reply)|
| ``[[think TEXT]]``      | a ``thinking`` part with TEXT                                |
| ``[[tool NAME S]]``     | ``tool_start`` NAME, S seconds of work, ``tool_end`` NAME     |
| ``[[toolhang NAME]]``   | ``tool_start`` NAME and never a result                        |
| ``[[code]]``            | a short reply with a fenced Python block, streamed           |
| ``[[error TEXT]]``      | an ``error`` part with TEXT                                  |
| ``[[fail TEXT]]``       | a ``backendError`` event (the backend died), as pi emits it  |
| ``[[empty]]``           | a response with no text at all                               |

Every turn is one ``LLMFullResponseStartFrame`` … ``LLMFullResponseEndFrame``
pair, the same shape real backends emit. An ``InterruptionFrame`` cancels the
script mid-way, like ``StreamingEchoLLMService``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from talky.backends.echo import StreamingEchoLLMService
from talky.server.turn import UserTurnTextFrame

DIRECTIVE = re.compile(r"\[\[(\w+)\s*([^\]]*)\]\]")

CODE_REPLY = (
    "Here is the function:\n\n"
    "```python\n"
    "def greet(name: str) -> str:\n"
    "    return f\"hello, {name}\"\n"
    "```\n\n"
    "Call it with a name."
)


class ScriptLLMService(StreamingEchoLLMService):
    """Plays the ``[[directive]]`` script written in the user's turn."""

    def __init__(self, *, name: str = "script", **kwargs: Any) -> None:
        super().__init__(name=name, **kwargs)

    def __repr__(self) -> str:  # noqa: D401
        return f"ScriptLLMService({self._echo_name})"

    async def _words(self, text: str) -> None:
        # Keep the whitespace with each word so newlines (code fences) survive.
        for token in re.findall(r"\S+\s*", text):
            await asyncio.sleep(self._token_delay_s)
            # LLMTextFrame, as hermes emits: RTVI reports it as bot-llm-text the
            # moment it's produced, before TTS has synthesized anything.
            await self.push_frame(LLMTextFrame(token))

    async def _run(self, text: str) -> None:
        steps = DIRECTIVE.findall(text)
        await self.push_frame(LLMFullResponseStartFrame())
        if not steps:
            await self._words(self.render(text))
        for op, arg in steps:
            arg = arg.strip()
            if op == "sleep":
                await asyncio.sleep(float(arg or 1))
            elif op == "say":
                await self._words(arg + " ")
            elif op == "stream":
                await self._words(" ".join(f"word{i}" for i in range(1, int(arg or 20) + 1)) + " ")
            elif op == "lines":
                n = int(arg or 10)
                await self._words(" ".join(f"Line {i} of {n} is here." for i in range(1, n + 1)) + " ")
            elif op == "think":
                await self.push_frame(AggregatedTextFrame(text=arg or "Let me think.", aggregated_by="thinking"))
            elif op == "tool":
                name, _, secs = arg.partition(" ")
                await self.push_frame(AggregatedTextFrame(text=f"▶ {name}", aggregated_by="tool_start"))
                await asyncio.sleep(float(secs or 1))
                await self.push_frame(AggregatedTextFrame(text=f"✓ {name}", aggregated_by="tool_end"))
            elif op == "toolhang":
                await self.push_frame(AggregatedTextFrame(text=f"▶ {arg or 'tool'}", aggregated_by="tool_start"))
            elif op == "code":
                await self._words(CODE_REPLY)
            elif op == "error":
                await self.push_frame(AggregatedTextFrame(text=arg or "Something went wrong.", aggregated_by="error"))
            elif op == "fail":
                from talky.server.event_bus import event_bus

                await event_bus.emit("backendError", {"profile": "script", "message": arg or "Script backend failed."})
            elif op == "empty":
                pass
            else:
                logger.warning(f"{self!r}: unknown directive {op!r}")
        await self.push_frame(LLMFullResponseEndFrame())

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Run the turn's script; cancel it on interruption."""
        await LLMService.process_frame(self, frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self._cancel_stream()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            if frame.text:
                logger.info(f"{self!r}: running {frame.text[:80]!r}")
                await self._cancel_stream()
                self._stream_task = self.create_task(self._run(frame.text))
            return

        await self.push_frame(frame, direction)
