#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""MCPDriverLLMService — null/passthrough LLM for the MCP-driven pipeline path.

See ticket c3a1 for the original design, ticket 76a3 for the
``UserTurnTextFrame`` migration.

## What this is

A subclass of ``pipecat.services.llm_service.LLMService`` that bridges an
external MCP caller to the LLM slot of a talky voice pipeline. It sits
inside an ``LLMSwitcher`` alongside real inference LLMs (openclaw,
moltis, etc.). When it is the active service, the pipeline is in
"MCP-driven" mode — user speech is captured and pushed to a shared queue
that ``convo_listen`` reads from; agent speech is injected externally
via ``task.queue_frames([LLMTextFrame(...)])`` and flows through this
service unchanged to the TTS stage.

## What it is NOT

- Not an LLM. It does no inference. No network I/O. No API calls.
- Not a context manager. Since ticket 76a3 the pipeline does not carry
  an ``LLMContext`` at all; this service just reads ``text`` off
  ``UserTurnTextFrame`` directly.
- Not a frame router. The ``LLMSwitcher``'s sandwich filter handles
  active/inactive routing. This service just processes frames that
  reach it via the active-filter path.

## Frame handling

| Frame | Behavior |
|-------|----------|
| ``InterruptionFrame`` | Delegated to base, then re-pushed downstream. |
| ``LLMConfigureOutputFrame`` | Handled by base class (sets ``_skip_tts``). |
| ``UserTurnTextFrame`` | **Consumed.** Push ``{"text": ..., "timestamp": ...}`` onto the shared ``user_speech_queue``. Do not push the frame further. |
| Anything else | Passed through unchanged via ``self.push_frame(frame, direction)``. Critical for ``LLMTextFrame`` injection from ``task.queue_frames()`` to flow to TTS. |
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from pipecat_mcp_server.talky_turn import UserTurnTextFrame


class MCPDriverLLMService(LLMService):
    """Null/passthrough LLM service for the MCP-driven pipeline path.

    Consumes ``UserTurnTextFrame`` by pushing its text onto a shared
    ``asyncio.Queue``. Passes all other frames through unchanged (most
    importantly, ``LLMTextFrame`` injected via ``task.queue_frames``
    from outside the pipeline).

    Designed to sit inside an ``LLMSwitcher`` as a peer of real LLM
    services. See ticket c3a1 for the original design.
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
            user_speech_queue: The shared asyncio.Queue that
                ``convo_listen`` reads from. This service pushes
                ``{"text": str, "timestamp": float}`` dicts onto it
                whenever a user turn completes and this service is the
                active one in the LLMSwitcher. Inactive state is managed
                by the LLMSwitcher's sandwich filter; no frames reach
                this service when another LLM is active.
            name: Human-readable name used in logs.
            **kwargs: Forwarded to ``LLMService.__init__``.

        """
        super().__init__(**kwargs)
        self._user_speech_queue = user_speech_queue
        self._driver_name = name

    def __repr__(self) -> str:  # noqa: D401
        return f"MCPDriverLLMService({self._driver_name})"

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Process a frame flowing through the pipeline."""
        # Base class handles InterruptionFrame (cancels in-flight function
        # calls) and LLMConfigureOutputFrame (sets _skip_tts). Always call
        # super first.
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            # Base already handled its part; re-push for downstream
            # processors that need to know about the interruption.
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            # User turn complete — hand the text to convo_listen. Consume
            # the frame (do not push further downstream).
            if frame.text:
                logger.info(
                    f"{self!r}: queuing user turn for convo_listen: {frame.text[:80]!r}"
                )
                await self._user_speech_queue.put(
                    {"text": frame.text, "timestamp": frame.timestamp}
                )
            else:
                logger.debug(f"{self!r}: empty user turn, skipping")
            return

        # Everything else (notably LLMTextFrame injected via
        # task.queue_frames from convo_speak) flows through unchanged to
        # the next processor.
        await self.push_frame(frame, direction)
