"""OpenClaw LLM Service for Pipecat

Simplified OpenClaw integration that works with the standard Pipecat pipeline.
Matches the API pattern used by moltis.py for consistency.
"""

import asyncio
import base64
import json
import os
import ssl
import time
from pathlib import Path
from typing import Optional
import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    TextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from server.config.voice_prompts import format_voice_message


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
    try:
        with open(openclaw_config_path, "r") as f:
            openclaw_config = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"OpenClaw config not found at {openclaw_config_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {openclaw_config_path}: {e}")

    # Use remote token if available, fallback to auth token
    gateway_config = openclaw_config.get("gateway", {})
    gateway_token = (gateway_config.get("remote", {}).get("token") or 
                     gateway_config.get("auth", {}).get("token"))

    if not gateway_token:
        raise ValueError("No gateway token found in OpenClaw config")

    # Load device-specific tokens from paired.json
    paired_path = os.path.join(openclaw_dir, "devices/paired.json")
    try:
        with open(paired_path, "r") as f:
            paired_config = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Paired devices config not found at {paired_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {paired_path}: {e}")

    # Get the current device ID from identity
    device_identity = load_device_identity(openclaw_dir)
    device_id = device_identity["deviceId"]

    # Get the operator token for this device
    try:
        operator_token = paired_config[device_id]["tokens"]["operator"]["token"]
    except KeyError:
        raise KeyError(f"Device {device_id} not found in paired config or missing operator token")

    return {"operator": operator_token, "node": gateway_token, "gateway": gateway_token}


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
    identity: dict, client_id: str, client_mode: str, role: str, scopes: list, token: str = "", nonce: str = ""
) -> dict:
    """Build device auth object with proper Ed25519 signature"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    signed_at_ms = int(time.time() * 1000)

    # Build payload string (matching OpenClaw's buildDeviceAuthPayload v2)
    # Generate a random nonce (not timestamp) to avoid nonce mismatch
    import secrets
    nonce = secrets.token_hex(16)  # 32-character hex string
    payload = "|".join(
        [
            "v2",
            identity["deviceId"],
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at_ms),
            token,
            nonce,
        ]
    )

    # Sign with private key
    private_key = serialization.load_pem_private_key(
        identity["privateKeyPem"].encode(), password=None
    )

    if not isinstance(private_key, ed25519.Ed25519PrivateKey):
        raise Exception("Expected Ed25519 private key")

    signature = private_key.sign(payload.encode())
    # Convert to base64url encoding (not regular base64)
    signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip('=')

    # Convert PEM public key to raw 32-byte Ed25519 key (like OpenClaw expects)
    public_key = serialization.load_pem_public_key(identity["publicKeyPem"].encode())
    # Get raw 32 bytes (skip SPKI header for Ed25519 keys)
    public_key_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    # Ed25519 SPKI has prefix 0x302a300506032b6570032100 + 32 bytes raw key
    # Extract just the raw 32 bytes
    if len(public_key_der) == 44:  # 44 bytes = 12 bytes prefix + 32 bytes raw
        public_key_raw = public_key_der[12:]  # Skip SPKI prefix
    else:
        # Fallback to Raw format (should be 32 bytes)
        public_key_raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
    public_key_b64url = base64.urlsafe_b64encode(public_key_raw).decode().rstrip('=')

    return {
        "id": identity["deviceId"],
        "publicKey": public_key_b64url,
        "signature": signature_b64,
        "signedAt": signed_at_ms,
        "nonce": nonce,  # Use the generated nonce
    }


def build_device_auth_v3(
    identity: dict, client_id: str, client_mode: str, role: str, scopes: list, token: str, nonce: str, platform: str = "macos", device_family: str = "desktop"
) -> dict:
    """Build device auth object with server-provided nonce using v3 protocol"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    signed_at_ms = int(time.time() * 1000)

    # Build payload string with server-provided nonce (v3 format)
    payload = "|".join(
        [
            "v3",  # v3 protocol
            identity["deviceId"],
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at_ms),
            token,
            nonce,  # Use server-provided nonce
            platform.lower(),  # v3 adds platform
            device_family.lower(),  # v3 adds deviceFamily
        ]
    )

    # Sign with private key
    private_key = serialization.load_pem_private_key(
        identity["privateKeyPem"].encode(), password=None
    )

    if not isinstance(private_key, ed25519.Ed25519PrivateKey):
        raise Exception("Expected Ed25519 private key")

    signature = private_key.sign(payload.encode())
    # Convert to base64url encoding (not regular base64)
    signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip('=')

    # Convert PEM public key to raw 32-byte Ed25519 key (like OpenClaw expects)
    public_key = serialization.load_pem_public_key(identity["publicKeyPem"].encode())
    # Get raw 32 bytes (skip SPKI header for Ed25519 keys)
    public_key_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    # Ed25519 SPKI has prefix 0x302a300506032b6570032100 + 32 bytes raw key
    # Extract just the raw 32 bytes
    if len(public_key_der) == 44:  # 44 bytes = 12 bytes prefix + 32 bytes raw
        public_key_raw = public_key_der[12:]  # Skip SPKI prefix
    else:
        # Fallback to Raw format (should be 32 bytes)
        public_key_raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
    public_key_b64url = base64.urlsafe_b64encode(public_key_raw).decode().rstrip('=')

    return {
        "id": identity["deviceId"],
        "publicKey": public_key_b64url,
        "signature": signature_b64,
        "signedAt": signed_at_ms,
        "nonce": nonce,  # Use the server-provided nonce
    }


class OpenClawLLMService(LLMService):
    """OpenClaw LLM service - simplified WebSocket-based implementation"""

    # OpenClaw protocol constants
    CLIENT_ID = "cli"
    CLIENT_MODE = "cli"
    ROLE = "operator"
    SCOPES = ["operator.admin", "operator.approvals", "operator.pairing"]
    SESSION_KEY = "voice-session"

    def __init__(self, *, gateway_url: str = None, agent_id: str = "main", session_key: str = None, **kwargs):
        super().__init__(**kwargs)

        # Determine gateway URL using shared network utility
        try:
            from shared.profile_manager import get_profile_manager
            pm = get_profile_manager()
            network_config = getattr(pm, 'settings', {}).get("network", {})
            config_host = network_config.get("host", "localhost")
            external_host = network_config.get("external_host")
            
            # Import shared network utility
            import sys
            sys.path.append(str(Path(__file__).parent.parent.parent))
            from shared.network_utils import get_default_gateway_url
            
            default_gateway = get_default_gateway_url(config_host, external_host, 18789)
        except Exception:
            # Fallback to localhost if config reading fails
            default_gateway = "ws://localhost:18789"
        
        self.gateway_url = gateway_url or os.getenv("OPENCLAW_GATEWAY_URL", default_gateway)
        self.agent_id = agent_id
        
        # Use provided session key or fall back to default
        self.session_key = session_key or self.SESSION_KEY

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

    async def _clear_pending_responses(self):
        """Clear all pending responses from the queue (for interruption)"""
        logger.debug("🧹 Clearing pending OpenClaw responses")
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        # Clear streaming accumulator if it exists
        if hasattr(self, "_accumulated_response"):
            delattr(self, "_accumulated_response")

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

            # Wait for connect challenge event
            response = await self._ws.recv()
            data = json.loads(response)

            # Handle connect challenge response
            if data.get("type") == "event" and data.get("event") == "connect.challenge":
                # Extract nonce from challenge
                challenge_nonce = data.get("payload", {}).get("nonce", "")
                if not challenge_nonce:
                    raise ConnectionError("No nonce in connect challenge")
                
                logger.info(f"🔐 Got connect challenge, nonce: {challenge_nonce[:8]}...")
                
                # Build device auth with the server-provided nonce using v3
                import platform
                platform_name = platform.system().lower()
                device_family = "desktop"  # Default to desktop for now
                
                device_auth = build_device_auth_v3(
                    self.device_identity, self.CLIENT_ID, self.CLIENT_MODE, self.ROLE, self.SCOPES, 
                    self.tokens["gateway"], challenge_nonce, platform_name, device_family
                )
                
                # Send connect request with device auth (v3) - ONLY ONE CONNECT REQUEST
                connect_request = {
                    "type": "req",
                    "id": str(self._next_id()),
                    "method": "connect", 
                    "params": {
                        "minProtocol": 3,
                        "maxProtocol": 3,
                        "client": {
                            "id": self.CLIENT_ID,
                            "version": "1.0.0",
                            "platform": platform_name,
                            "mode": self.CLIENT_MODE,
                            "deviceFamily": device_family,  # v3 requires deviceFamily
                        },
                        "role": self.ROLE,
                        "scopes": self.SCOPES,
                        "auth": {"token": self.tokens["gateway"]},
                        "device": device_auth,
                    },
                }
                
                await self._ws.send(json.dumps(connect_request))
                response = await self._ws.recv()
                data = json.loads(response)

            # Check final connection result
            if not data.get("ok"):
                raise ConnectionError(f"OpenClaw connection failed: {data}")

            logger.info(f"✅ Connected to OpenClaw")

            # Start message handler and wait for it to be ready
            self._message_handler_task = asyncio.create_task(self._handle_messages())
            
            # Give the message handler a moment to start processing
            await asyncio.sleep(0.05)
            
            # Now mark as connected
            self._connected = True
            self._connected_event.set()
            
            logger.info(f"✅ Connected to OpenClaw and message handler running")

        except Exception as e:
            logger.error(f"OpenClaw connection error: {e}")
            raise

    async def initialize(self):
        """Initialize connection early to avoid greeting timing issues"""
        try:
            await self._connect()
        except Exception as e:
            logger.warning(f"Failed to pre-connect OpenClaw: {e}, will connect on first message")

    async def _handle_messages(self):
        """Handle incoming WebSocket messages"""
        try:
            async for message in self._ws:
                data = json.loads(message)

                # Log important responses for debugging
                if data.get("type") in ["res", "event"] and data.get("event") not in ["tick", "health"]:
                    logger.debug(f"📨 Received: {message[:200]}")

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
                            
                            # Check if this is the end of streaming
                            if text_data.get("delta") == "" and hasattr(self, "_accumulated_response"):
                                # Empty delta often signals end of stream
                                full_response = self._accumulated_response
                                logger.info(f"✅ End of streaming, putting response in queue: {full_response[:100]}...")
                                try:
                                    self._response_queue.put_nowait(full_response)
                                except asyncio.QueueFull:
                                    logger.warning("Response queue full")
                                delattr(self, "_accumulated_response")

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

                            # Clear streaming accumulator if it exists
                            if hasattr(self, "_accumulated_response"):
                                delattr(self, "_accumulated_response")

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
        """Process frames - handle LLMContextFrame and InterruptionFrame"""
        await super().process_frame(frame, direction)

        # Handle interruption - clear pending responses
        if isinstance(frame, InterruptionFrame):
            await self._clear_pending_responses()
            await self.push_frame(frame, direction)
            return

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

            # Format message with voice conversation guidance
            full_message = format_voice_message(last_user_message)

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
                    "sessionKey": self.session_key,
                    "message": full_message,
                    "idempotencyKey": f"talky-voice-chat-{request_id}",
                },
            }

            logger.info(f"📤 Sending to OpenClaw: {json.dumps(request_data)}")
            await self._ws.send(json.dumps(request_data))

            # Wait for response (OpenClaw may choose not to respond - that's valid)
            try:
                logger.info(f"⏳ Waiting for response from queue (queue size: {self._response_queue.qsize()})")
                response = await self._response_queue.get()
                logger.info(f"🤖 Got response: {response[:100]}...")

                # Push to pipeline
                await self.push_frame(TextFrame(response))
                await self.push_frame(LLMFullResponseEndFrame())
                logger.info(f"✅ Pushed response frames to pipeline")

            except Exception as e:
                logger.error(f"Error waiting for OpenClaw response: {e}")
                await self.push_frame(TextFrame("Sorry, I'm having trouble connecting to OpenClaw right now."))
                await self.push_frame(LLMFullResponseEndFrame())

        except Exception as e:
            logger.error(f"Error in _process_context: {e}", exc_info=True)
            await self.push_frame(LLMFullResponseEndFrame())
