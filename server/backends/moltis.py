"""
Simple Moltis LLM Service for Pipecat.
Consumes UserTurnTextFrame from the talky turn detector (ticket 76a3).
"""

import asyncio
import json
import os
import ssl
import time
from typing import Optional

import websockets
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat_mcp_server.talky_turn import UserTurnTextFrame
from server.config.voice_prompts import format_voice_message
from shared.profile_manager import get_profile_manager


class MoltisLLMService(LLMService):
    """Moltis LLM service - simple WebSocket-based implementation"""

    def __init__(
        self,
        *,
        gateway_url: str = None,
        api_key: str = None,
        session_key: str = None,
        session_strategy: str = "persistent",
        agent_id: str = "main",
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.gateway_url = gateway_url or os.getenv(
            "MOLTIS_GATEWAY_URL", "wss://localhost:65491/ws"
        )
        self.api_key = api_key or os.getenv("MOLTIS_API_KEY")
        self.agent_id = agent_id
        self.session_strategy = session_strategy

        # Generate session key
        if session_key:
            self.session_key = session_key
        else:
            self.session_key = self._generate_session_key()

        # WebSocket state
        self._ws = None
        self._connected = False
        self._connected_event = asyncio.Event()
        self._response_queue = asyncio.Queue()
        self._request_id_counter = int(time.time() * 1000)
        self._message_handler_task = None

        logger.info(f"✅ MoltisLLMService initialized (session: {self.session_key})")

    def _generate_session_key(self) -> str:
        """Generate session key based on strategy"""
        from datetime import datetime

        if self.session_strategy == "persistent":
            return f"agent:{self.agent_id}:voice"
        elif self.session_strategy == "per-connection":
            timestamp = int(time.time())
            return f"agent:{self.agent_id}:voice-{timestamp}"
        elif self.session_strategy == "daily":
            today = datetime.now().strftime("%Y-%m-%d")
            return f"agent:{self.agent_id}:voice-{today}"
        elif self.session_strategy == "new":
            timestamp = int(time.time())
            return f"agent:{self.agent_id}:voice-{timestamp}"
        else:
            # Default to persistent
            return f"agent:{self.agent_id}:voice"

    async def _connect(self):
        """Connect to Moltis gateway"""
        if self._connected:
            return

        logger.info(f"🔌 Connecting to Moltis at {self.gateway_url}...")

        # SSL context for self-signed certs
        ssl_context = None
        if self.gateway_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            if "localhost" in self.gateway_url or "127.0.0.1" in self.gateway_url:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        # Connect
        self._ws = await websockets.connect(self.gateway_url, ssl=ssl_context)

        # Handshake
        connect_request = {
            "type": "req",
            "id": str(self._next_id()),
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "talky-voice-chat",
                    "version": "1.0.0",
                    "platform": "python",
                    "mode": "voice",
                },
            },
        }

        if self.api_key:
            connect_request["params"]["auth"] = {"apiKey": self.api_key}

        await self._ws.send(json.dumps(connect_request))
        response = await self._ws.recv()
        data = json.loads(response)

        if not data.get("ok"):
            error = data.get("error", {})
            raise Exception(f"Moltis connection failed: {error.get('message', 'Unknown error')}")

        logger.info(f"✅ Connected to Moltis")

        # Start message handler first
        self._message_handler_task = asyncio.create_task(self._handle_messages())

        # Give it a moment to start
        await asyncio.sleep(0.1)

        # Switch to our session (important! Otherwise defaults to "main")
        logger.info(f"🔄 Switching to session: {self.session_key}")
        switch_request = {
            "type": "req",
            "id": str(self._next_id()),
            "method": "sessions.switch",
            "params": {"key": self.session_key},
        }

        await self._ws.send(json.dumps(switch_request))

        # Wait a moment for the switch to complete
        await asyncio.sleep(0.2)

        logger.info(f"✅ Session switched to: {self.session_key}")

        self._connected = True
        self._connected_event.set()

    async def _handle_messages(self):
        """Handle incoming WebSocket messages"""
        try:
            async for message in self._ws:
                data = json.loads(message)

                # Log all responses for debugging
                if data.get("type") == "res":
                    if not data.get("ok"):
                        logger.error(f"❌ Request failed: {json.dumps(data.get('error', {}))}")
                    else:
                        logger.debug(f"✅ Request succeeded: {data.get('id')}")

                if data.get("type") == "event" and data.get("event") == "chat":
                    payload = data.get("payload", {})
                    state = payload.get("state", "")

                    logger.debug(f"📨 Chat event: state={state}, payload={json.dumps(payload)}")

                    # Collect streaming deltas
                    if state == "delta":
                        text = payload.get("text", "")
                        if text:
                            if not hasattr(self, "_current_response"):
                                self._current_response = ""
                            self._current_response += text
                            logger.debug(f"📝 Delta: {text}")

                    # Send final response
                    elif state == "final":
                        response_text = getattr(self, "_current_response", "")

                        if response_text:
                            logger.info(f"✅ Got response: {response_text[:50]}...")
                            try:
                                self._response_queue.put_nowait(response_text)
                            except asyncio.QueueFull:
                                logger.warning("Response queue full")
                        else:
                            logger.warning("⚠️ Final response but no text accumulated")

                        # Clear
                        if hasattr(self, "_current_response"):
                            del self._current_response

        except Exception as e:
            logger.error(f"Message handler error: {e}", exc_info=True)
            self._connected = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames - handle UserTurnTextFrame (ticket 76a3)."""
        await super().process_frame(frame, direction)

        if isinstance(frame, UserTurnTextFrame):
            await self._process_user_text(frame.text)
            return

        # Everything else flows through unchanged.
        await self.push_frame(frame, direction)

    async def _process_user_text(self, user_text: str):
        """Send a single user turn's text to the Moltis remote session."""
        try:
            # Ensure connected
            if not self._connected:
                await self._connect()

            await self.push_frame(LLMFullResponseStartFrame())

            if not user_text:
                logger.warning("No user message text")
                await self.push_frame(LLMFullResponseEndFrame())
                return

            # Format message with voice conversation guidance
            full_message = format_voice_message(user_text)

            logger.info(f"🗣️  User: {user_text[:100]}...")

            # Clear queue
            while not self._response_queue.empty():
                try:
                    self._response_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Send to Moltis (sessionKey not needed - uses active session from sessions.switch)
            request_id = self._next_id()
            await self._ws.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": str(request_id),
                        "method": "chat.send",
                        "params": {
                            "text": full_message,
                            "idempotencyKey": f"talky-voice-chat-{request_id}",
                        },
                    }
                )
            )

            # Wait for response
            response = await self._response_queue.get()

            logger.info(f"🤖 Response: {response[:100]}...")

            # Push to pipeline
            await self.push_frame(TextFrame(response))
            await self.push_frame(LLMFullResponseEndFrame())

        except Exception as e:
            logger.error(f"Error in _process_user_text: {e}", exc_info=True)
            await self.push_frame(LLMFullResponseEndFrame())

    async def switch_session(self, session_key: str):
        """Switch to a different session"""
        if not self._connected:
            logger.warning("Not connected - will use new session key on next connect")
            self.session_key = session_key
            return

        switch_request = {
            "type": "req",
            "id": str(self._next_id()),
            "method": "sessions.switch",
            "params": {"key": session_key},
        }

        await self._ws.send(json.dumps(switch_request))
        response = await self._ws.recv()
        data = json.loads(response)

        if data.get("ok"):
            self.session_key = session_key
            logger.info(f"✅ Switched to session: {session_key}")
        else:
            logger.error(f"Failed to switch session: {data.get('error')}")

    async def list_sessions(self):
        """List all available sessions"""
        if not self._connected:
            await self._connect()

        list_request = {
            "type": "req",
            "id": str(self._next_id()),
            "method": "sessions.list",
            "params": {},
        }

        await self._ws.send(json.dumps(list_request))
        response = await self._ws.recv()
        data = json.loads(response)

        if data.get("ok"):
            return data.get("payload", [])
        else:
            logger.error(f"Failed to list sessions: {data.get('error')}")
            return []

    def _next_id(self) -> int:
        self._request_id_counter += 1
        return self._request_id_counter
