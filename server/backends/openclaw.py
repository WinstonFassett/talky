"""OpenClaw LLM Service for Pipecat

Simplified OpenClaw integration that works with the standard Pipecat pipeline.
Matches the API pattern used by moltis.py for consistency.
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
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService


def base64url_encode(data: bytes) -> str:
    """Base64url encode without padding"""
    import base64

    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def load_paired_tokens(openclaw_dir: str = None):
    """Load operator and node tokens from OpenClaw config"""
    if openclaw_dir is None:
        openclaw_dir = os.path.expanduser("~/.openclaw")

    # Load gateway auth token from openclaw.json
    openclaw_config_path = os.path.join(openclaw_dir, "openclaw.json")
    with open(openclaw_config_path, "r") as f:
        openclaw_config = json.load(f)

    gateway_token = openclaw_config["gateway"]["auth"]["token"]

    # Load device-specific tokens from paired.json
    paired_path = os.path.join(openclaw_dir, "devices/paired.json")
    with open(paired_path, "r") as f:
        paired_config = json.load(f)

    # Get the current device ID from identity
    device_identity = load_device_identity(openclaw_dir)
    device_id = device_identity["deviceId"]

    # Get the operator token for this device
    operator_token = paired_config[device_id]["tokens"]["operator"]["token"]

    return {"operator": operator_token, "node": gateway_token}


def load_device_identity(openclaw_dir: str = None):
    """Load device identity from openclaw_dir/identity/device.json"""
    if openclaw_dir is None:
        openclaw_dir = os.path.expanduser("~/.openclaw")

    identity_path = os.path.join(openclaw_dir, "identity/device.json")

    with open(identity_path, "r") as f:
        data = json.load(f)

    if data.get("version") != 1:
        raise Exception("Unsupported identity version")

    return {
        "deviceId": data["deviceId"],
        "publicKeyPem": data["publicKeyPem"],
        "privateKeyPem": data["privateKeyPem"],
    }


def build_device_auth(
    identity: dict, client_id: str, client_mode: str, role: str, scopes: list, token: str = ""
) -> dict:
    """Build device auth object with proper Ed25519 signature"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    signed_at_ms = int(time.time() * 1000)

    # Build payload string (matching OpenClaw's buildDeviceAuthPayload)
    payload = "|".join(
        [
            "v1",
            identity["deviceId"],
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at_ms),
            token,
        ]
    )

    # Sign with private key
    private_key = serialization.load_pem_private_key(
        identity["privateKeyPem"].encode(), password=None
    )

    if not isinstance(private_key, ed25519.Ed25519PrivateKey):
        raise Exception("Expected Ed25519 private key")

    signature = private_key.sign(payload.encode())
    signature_b64 = base64url_encode(signature)

    return {
        "id": identity["deviceId"],
        "publicKey": identity["publicKeyPem"],
        "signature": signature_b64,
        "signedAt": signed_at_ms,
    }


class OpenClawLLMService(LLMService):
    """OpenClaw LLM service - simplified WebSocket-based implementation"""

    def __init__(self, *, gateway_url: str = None, agent_id: str = "main", **kwargs):
        super().__init__(**kwargs)

        self.gateway_url = gateway_url or os.getenv("OPENCLAW_GATEWAY_URL", "ws://localhost:18789")
        self.agent_id = agent_id

        # OpenClaw uses WebSocket connections with auth tokens
        self.tokens = None
        self.device_identity = None
        self._ws = None
        self._connected = False
        self._connected_event = asyncio.Event()
        self._response_queue = asyncio.Queue()
        self._request_id_counter = int(time.time() * 1000)
        self._message_handler_task = None

        logger.info(f"✅ OpenClawLLMService initialized (agent: {self.agent_id})")

    def _next_id(self) -> int:
        self._request_id_counter += 1
        return self._request_id_counter

    async def _connect(self):
        """Connect to OpenClaw gateway"""
        if self._connected:
            return

        logger.info(f"🔌 Connecting to OpenClaw at {self.gateway_url}...")

        # Load tokens if not already loaded
        if not self.tokens:
            self.tokens = load_paired_tokens()
            self.device_identity = load_device_identity()

        # SSL context for wss://
        ssl_context = None
        if self.gateway_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            if "localhost" in self.gateway_url or "127.0.0.1" in self.gateway_url:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        try:
            connect_kwargs = {"ping_interval": 20, "ping_timeout": 10}
            if ssl_context:
                connect_kwargs["ssl"] = ssl_context

            self._ws = await websockets.connect(self.gateway_url, **connect_kwargs)

            # Authenticate with OpenClaw
            client_id = "cli"  # Use the working constant
            client_mode = "cli"
            role = "operator"
            scopes = ["operator.admin", "operator.approvals", "operator.pairing"]

            device_auth = build_device_auth(
                self.device_identity, client_id, client_mode, role, scopes, self.tokens["operator"]
            )

            await self._ws.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": str(self._next_id()),
                        "method": "connect",
                        "params": {
                            "minProtocol": 3,
                            "maxProtocol": 3,
                            "client": {
                                "id": client_id,
                                "version": "1.0.0",
                                "platform": "macos",
                                "mode": client_mode,
                            },
                            "role": role,
                            "scopes": scopes,
                            "auth": {"token": self.tokens["operator"]},
                            "device": device_auth,
                        },
                    }
                )
            )

            # Handle auth response
            response = await self._ws.recv()
            data = json.loads(response)

            # Handle connect challenge
            if data.get("type") == "event" and data.get("event") == "connect.challenge":
                response = await self._ws.recv()
                data = json.loads(response)

            if not data.get("ok"):
                raise Exception(f"OpenClaw connection failed: {data}")

            logger.info(f"✅ Connected to OpenClaw")

            # Start message handler
            self._message_handler_task = asyncio.create_task(self._handle_messages())

            # Give it a moment to start
            await asyncio.sleep(0.1)

            self._connected = True
            self._connected_event.set()

        except Exception as e:
            logger.error(f"Failed to connect to OpenClaw: {e}")
            raise

    async def _handle_messages(self):
        """Handle incoming WebSocket messages"""
        try:
            async for message in self._ws:
                data = json.loads(message)

                # Log important responses for debugging (reduced frequency)
                if data.get("type") in ["res", "event"] and data.get("event") != "health":
                    logger.debug(f"📨 Received message: {json.dumps(data)}")

                # Log all requests to see if first one gets response
                if data.get("type") == "res":
                    req_id = data.get("id")
                    if req_id:
                        logger.info(
                            f"📨 Response for request {req_id}: {json.dumps(data.get('result', {}))}"
                        )

                # Handle streaming agent responses
                if data.get("type") == "event" and data.get("event") == "agent":
                    payload = data.get("payload", {})
                    if payload.get("stream") == "assistant" and "data" in payload:
                        text_data = payload["data"]
                        if "text" in text_data:
                            # This is streaming text - accumulate it
                            if not hasattr(self, "_accumulated_response"):
                                self._accumulated_response = ""
                            self._accumulated_response += text_data.get("delta", "")
                            logger.debug(f"📝 Streaming: {text_data.get('delta', '')}")

                # Handle final chat message
                elif data.get("type") == "event" and data.get("event") == "chat":
                    payload = data.get("payload", {})
                    if payload.get("state") == "final" and "message" in payload:
                        message = payload["message"]
                        if message.get("role") == "assistant" and "content" in message:
                            # Get the full response from content
                            content = message["content"]
                            full_text = ""
                            for item in content:
                                if item.get("type") == "text":
                                    full_text += item.get("text", "")

                            logger.info(f"✅ Final response: {full_text[:100]}...")
                            try:
                                self._response_queue.put_nowait(full_text)
                            except asyncio.QueueFull:
                                logger.warning("Response queue full")

                # Handle simple response type (fallback) - but check if it has content
                elif data.get("type") == "res" and "result" in data:
                    result = data.get("result", {})
                    response_text = result.get("response", "")
                    if response_text:
                        logger.info(f"✅ Got response: {response_text[:50]}...")
                        try:
                            self._response_queue.put_nowait(response_text)
                        except asyncio.QueueFull:
                            logger.warning("Response queue full")
                    else:
                        # Empty response - wait for chat event
                        logger.debug(f"📝 Empty 'res' response, waiting for 'chat' event...")

                # Log success/failure
                if data.get("type") == "res":
                    if not data.get("ok"):
                        logger.error(f"❌ Request failed: {json.dumps(data.get('error', {}))}")
                    else:
                        logger.debug(f"✅ Request succeeded: {data.get('id')}")

        except Exception as e:
            logger.error(f"Message handler error: {e}", exc_info=True)
            self._connected = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames - handle LLMContextFrame like the original implementation"""
        await super().process_frame(frame, direction)

        # Handle LLMContextFrame - don't push it, just process it
        if isinstance(frame, LLMContextFrame):
            context = frame.context
            await self._process_context(context)
        # For all other frames, push them along
        elif not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)

    async def _process_context(self, context: LLMContext):
        """Process LLM context - this is called by the base LLMService"""
        try:
            # Ensure connected
            if not self._connected:
                await self._connect()

            await self.push_frame(LLMFullResponseStartFrame())

            # Get messages from context
            messages = context.get_messages()

            # Find last user message
            last_user_message = None
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for item in content:
                            if item.get("type") == "text":
                                last_user_message = item.get("text", "")
                                break
                    else:
                        last_user_message = content
                    break

            if not last_user_message:
                logger.warning("No user message found")
                await self.push_frame(LLMFullResponseEndFrame())
                return

            logger.info(f"🗣️  User: {last_user_message[:100]}...")

            # Clear queue
            while not self._response_queue.empty():
                try:
                    self._response_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Send to OpenClaw
            request_id = self._next_id()
            request_data = {
                "type": "req",
                "id": str(request_id),
                "method": "chat.send",
                "params": {
                    "sessionKey": "voice-session",  # Use a session key
                    "message": last_user_message,
                    "idempotencyKey": f"pipecat-{request_id}",
                },
            }

            logger.info(f"📤 Sending to OpenClaw: {json.dumps(request_data)}")
            await self._ws.send(json.dumps(request_data))

            # Wait for response (OpenClaw may choose not to respond - that's valid)
            try:
                response = await self._response_queue.get()
                logger.info(f"🤖 Response: {response[:100]}...")

                # Push to pipeline
                await self.push_frame(TextFrame(response))
                await self.push_frame(LLMFullResponseEndFrame())

            except Exception as e:
                logger.error(f"Error waiting for OpenClaw response: {e}")
                await self.push_frame(TextFrame("Sorry, I didn't get a response from OpenClaw."))
                await self.push_frame(LLMFullResponseEndFrame())

        except Exception as e:
            logger.error(f"Error in _process_context: {e}", exc_info=True)
            await self.push_frame(LLMFullResponseEndFrame())
