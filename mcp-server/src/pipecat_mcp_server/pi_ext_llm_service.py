#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""PiExtensionLLMService — bridges a Pi extension WebSocket to the voice pipeline.

Protocol (JSON over WebSocket, text frames):
  Daemon → extension:
    {"type": "ready"}                     — handshake after accept
    {"type": "stt", "text": "..."}        — user speech transcript
    {"type": "abort"}                     — VAD barge-in, abort current Pi turn

  Extension → daemon:
    {"type": "tts_start"}                 — Pi response starting
    {"type": "tts", "text": "..."}        — response token delta (stream these)
    {"type": "tts_end"}                   — Pi response complete
    {"type": "tool_start", "text": "..."}  — tool call began (e.g. "▶ read_file: foo.py")
    {"type": "tool_end",   "text": "..."}  — tool call finished (e.g. "✓ read_file (42 lines)")
"""

from __future__ import annotations

import asyncio
from typing import Any

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


class PiExtensionLLMService(LLMService):
    """LLM slot for a Pi extension connected over WebSocket.

    Sits in the LLMSwitcher as the "__pi__" profile. When a Pi extension
    connects to /ws/pi, it becomes this service's peer. STT text flows
    to the extension; TTS text flows back from the extension to TTS/Speaker.
    InterruptionFrames trigger an abort signal to the extension.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ws: Any = None  # starlette WebSocket, set by handle_websocket
        self._send_lock: asyncio.Lock = asyncio.Lock()

    async def handle_websocket(self, ws: Any) -> None:
        """Accept a WebSocket connection and run until it closes.

        Called by the /ws/pi route handler. Blocks until the extension
        disconnects. Sends {"type":"ready"} immediately after attach.
        """
        self._ws = ws
        await self._send({"type": "ready"})
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
                logger.warning(f"PiExtLLM: WS send failed: {e}")

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
                elif msg_type == "tool_start":
                    text = msg.get("text", "")
                    if text:
                        await self.push_frame(AggregatedTextFrame(text=text, aggregated_by="tool_start"))
                elif msg_type == "tool_end":
                    text = msg.get("text", "")
                    if text:
                        await self.push_frame(AggregatedTextFrame(text=text, aggregated_by="tool_end"))
                # unknown types silently ignored
        except WebSocketDisconnect:
            logger.info("PiExtLLM: extension disconnected")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.info(f"PiExtLLM: reader ended: {e}")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            # VAD barge-in — tell the extension to abort its current Pi turn.
            await self._send({"type": "abort"})
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            # User turn complete — forward transcript to the extension.
            if frame.text:
                await self._send({"type": "stt", "text": frame.text})
            return

        await self.push_frame(frame, direction)
