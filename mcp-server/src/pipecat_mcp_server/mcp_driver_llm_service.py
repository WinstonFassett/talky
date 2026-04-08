#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""MCPDriverLLMService — null/passthrough LLM for the MCP-driven pipeline path.

See ticket c3a1 for the full design and spike results.

## What this is

A subclass of `pipecat.services.llm_service.LLMService` that bridges an
external MCP caller to the LLM slot of a talky voice pipeline. It sits
inside an `LLMSwitcher` alongside real inference LLMs (openclaw, moltis,
etc.). When it is the active service, the pipeline is in "MCP-driven"
mode — user speech is captured and pushed to a shared queue that
`convo_listen` reads from; agent speech is injected externally via
`task.queue_frames([LLMTextFrame(...)])` and flows through this service
unchanged to the TTS stage.

## What it is NOT

- Not an LLM. It does no inference. No network I/O. No API calls.
- Not a context manager. It reads `LLMContext.get_messages()[-1]` to
  extract the latest user message but does not own, modify, or persist
  any state in the context.
- Not a frame router. The `LLMSwitcher`'s sandwich filter handles
  active/inactive routing. This service just processes frames that reach
  it via the active-filter path.

## Frame handling

| Frame | Behavior |
|-------|----------|
| `InterruptionFrame` | Delegated to base, then re-pushed downstream. |
| `LLMConfigureOutputFrame` | Handled by base class (sets `_skip_tts`). |
| `LLMContextFrame` | **Consumed.** Extract last user message from `context.get_messages()` and push `{"text": ..., "timestamp": ...}` onto the shared `user_speech_queue`. Do not push the frame further. |
| Anything else | Passed through unchanged via `self.push_frame(frame, direction)`. This is critical for `LLMTextFrame` injection from `task.queue_frames()` to flow to the TTS. |

## Validated by

- Ticket c3a1's spike ([spikes/llm_switcher_spike.py](../../../spikes/llm_switcher_spike.py)),
  commit 822c481, run 2026-04-08, all 7 spike steps passed live end-to-end
  with a real openclaw backend as the second LLM in the switcher.
- Direct mirror of the proven pattern in
  [server/backends/openclaw.py](../../../server/backends/openclaw.py) minus
  the `_process_context` that calls the remote API.

## Related tickets

- **c3a1** — this file's design doc, with spike results
- **ea77** — pipeline shape routing via service switchers (parent)
- **76a3** — follow-up to drop LLMContext aggregation entirely (out of scope here)
- **3f12** — daemon process architecture and room model (parent/sibling)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMContextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService


class MCPDriverLLMService(LLMService):
    """Null/passthrough LLM service for the MCP-driven pipeline path.

    Consumes `LLMContextFrame` by extracting the latest user message and
    pushing it onto a shared `asyncio.Queue`. Passes all other frames
    through unchanged (most importantly, `LLMTextFrame` injected via
    `task.queue_frames` from outside the pipeline).

    Designed to sit inside an `LLMSwitcher` as a peer of real LLM services.
    See ticket c3a1 for the detailed design and spike results.
    """

    def __init__(
        self,
        user_speech_queue: asyncio.Queue,
        *,
        name: str = "mcp-driver",
        **kwargs: Any,
    ) -> None:
        """Create a new MCPDriverLLMService.

        Args:
            user_speech_queue: The shared asyncio.Queue that `convo_listen`
                reads from. This service pushes
                `{"text": str, "timestamp": float}` dicts onto it whenever a
                user turn completes and this service is the active one in
                the LLMSwitcher. Inactive state is managed by the
                LLMSwitcher's sandwich filter; no frames reach this service
                when another LLM is active.
            name: Human-readable name used in logs. Useful when multiple
                MCPDriver instances exist (rare).
            **kwargs: Forwarded to `LLMService.__init__`.

        """
        super().__init__(**kwargs)
        self._user_speech_queue = user_speech_queue
        self._driver_name = name

    def __repr__(self) -> str:  # noqa: D401
        return f"MCPDriverLLMService({self._driver_name})"

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Process a frame flowing through the pipeline.

        Called by the pipeline framework for every frame that reaches this
        service through the `LLMSwitcher` sandwich filter. Frames that
        arrive when this service is inactive are blocked upstream and
        never reach here.
        """
        # Base class handles InterruptionFrame (cancels in-flight function calls)
        # and LLMConfigureOutputFrame (sets _skip_tts). Always call super first.
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            # Base already handled its part; re-push for downstream processors
            # that need to know about the interruption.
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMContextFrame):
            # This is the "user turn complete, please respond" signal. We
            # don't respond — we push the user's text onto the shared queue
            # so convo_listen can read it. Consume the frame (do not push
            # further downstream).
            await self._handle_user_turn(frame.context)
            return

        # Everything else (notably LLMTextFrame injected via task.queue_frames
        # from convo_speak) flows through unchanged to the next processor.
        await self.push_frame(frame, direction)

    async def _handle_user_turn(self, context: Any) -> None:
        """Extract the latest user message from the LLMContext and push it
        onto the user speech queue for `convo_listen` to read.

        Silent no-op if no user message can be extracted (e.g., empty
        context or unexpected format). Matches the same extraction logic
        as `OpenClawLLMService._process_context`.
        """
        try:
            messages = context.get_messages()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{self!r}: could not get messages from context: {e}")
            return

        last_user_text: Optional[str] = None
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                # Content is a list of parts (OpenAI-style); find the first
                # text part.
                for item in content:
                    if item.get("type") == "text":
                        last_user_text = item.get("text", "")
                        break
            else:
                # Content is a plain string.
                last_user_text = content
            break

        if not last_user_text:
            logger.debug(f"{self!r}: no user message found in context")
            return

        logger.info(
            f"{self!r}: queuing user turn for convo_listen: {last_user_text[:80]!r}"
        )
        await self._user_speech_queue.put(
            {
                "text": last_user_text,
                "timestamp": time.time(),
            }
        )
