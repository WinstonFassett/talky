#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""AgentExtensionLLMService — bridges an agent extension WebSocket to the voice pipeline.

Protocol (JSON over WebSocket, text frames):
  Daemon → extension:
    {"type": "ready"}                          — handshake after accept
    {"type": "greet", "instruction": "..."}    — agent should greet in own words
    {"type": "stt", "text": "..."}             — user speech transcript
    {"type": "abort"}                          — VAD barge-in, abort current agent turn

  Extension → daemon:
    {"type": "tts_start"}                 — agent response starting
    {"type": "tts", "text": "..."}        — response token delta (stream these)
    {"type": "tts_end"}                   — agent response complete
    {"type": "tool_start", "text": "..."}  — tool call began (e.g. "▶ read_file: foo.py")
    {"type": "tool_end",   "text": "..."}  — tool call finished (e.g. "✓ read_file (42 lines)")
    {"type": "event", "kind": "<string>", "text": "...", "payload": {...}}
        — Generic structured-event envelope. ``kind`` is an arbitrary string
        the backend chose (e.g. "thinking", "error", "info", "rate_limit").
        The daemon forwards it to the pipeline as
        AggregatedTextFrame(aggregated_by=kind), so the client's
        botOutputRenderers can dispatch on ``kind`` to a per-kind UI.
        ``text`` is a short human-readable summary used as the frame text.
        ``payload`` is optional structured detail; when present it's
        JSON-stringified into the frame text as ``text + "\\u0000" + json``
        so renderers that want the structured data can parse it.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from pipecat_mcp_server.talky_turn import UserTurnTextFrame


class AgentExtensionLLMService(LLMService):
    """LLM slot for an agent extension connected over WebSocket.

    Sits in the LLMSwitcher under whatever profile name the user configured.
    When an agent extension connects to /ws/agent, the handler looks up the
    service by type, switches to its profile, and calls handle_websocket.
    STT text flows to the extension; TTS text flows back from the extension
    to TTS/Speaker. InterruptionFrames trigger an abort signal to the extension.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ws: Any = None  # starlette WebSocket, set by handle_websocket
        self._send_lock: asyncio.Lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def handle_websocket(self, ws: Any, greeting_instruction: Optional[str] = None) -> None:
        """Accept a WebSocket connection and run until it closes.

        ``greeting_instruction`` (if provided) is sent as a ``greet``
        message right after the ``ready`` handshake. The agent extension
        is expected to feed it to its agent as a user message so the
        agent generates its own greeting words (which then stream back
        as ``tts`` frames through the normal pipeline).
        """
        self._ws = ws
        await self._send({"type": "ready"})
        if greeting_instruction:
            await self._send({"type": "greet", "instruction": greeting_instruction})
        try:
            await self._reader_loop(ws)
        finally:
            self._ws = None

    async def _send(self, msg: dict) -> None:
        if self._ws is None:
            return
        async with self._send_lock:
            try:
                import json
                await self._ws.send_text(json.dumps(msg))
            except Exception as e:
                logger.warning(f"AgentExtLLM: WS send failed: {e}")

    async def _reader_loop(self, ws: Any) -> None:
        """Read extension → daemon messages and push pipeline frames."""
        import json

        from starlette.websockets import WebSocketDisconnect

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")
                if msg_type == "tts_start":
                    await self.push_frame(LLMFullResponseStartFrame())
                elif msg_type == "tts":
                    text = msg.get("text", "")
                    if text:
                        await self.push_frame(TextFrame(text=text))
                elif msg_type == "tts_end":
                    await self.push_frame(LLMFullResponseEndFrame())
                elif msg_type == "event":
                    # Generic structured-event envelope. ``kind`` selects the
                    # client-side renderer; ``text`` is the short summary;
                    # ``payload`` (optional) is JSON detail appended after a
                    # NUL byte for renderers that want to parse it.
                    kind = msg.get("kind") or "event"
                    text = msg.get("text", "")
                    payload = msg.get("payload")
                    if payload is not None:
                        try:
                            text = (text or "") + "\x00" + json.dumps(payload)
                        except (TypeError, ValueError):
                            pass
                    if text:
                        await self.push_frame(AggregatedTextFrame(text=text, aggregated_by=kind))
        except WebSocketDisconnect:
            logger.info("AgentExtLLM: extension disconnected")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.info(f"AgentExtLLM: reader ended: {e}")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self._send({"type": "abort"})
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            if frame.text:
                await self._send({"type": "stt", "text": frame.text})
            return

        await self.push_frame(frame, direction)
