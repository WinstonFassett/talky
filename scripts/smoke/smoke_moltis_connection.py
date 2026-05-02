#!/usr/bin/env python3
"""
Test Moltis WebSocket connection
Run this to verify Moltis is reachable before running the full bot
"""

import asyncio
import json
import os
import ssl

from dotenv import load_dotenv

load_dotenv()


async def test_connection():
    try:
        import websockets

        gateway_url = os.getenv("MOLTIS_GATEWAY_URL", "wss://localhost:65491/ws")
        print(f"🔌 Testing connection to: {gateway_url}")

        # Setup SSL for self-signed certs
        ssl_context = None
        if gateway_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            if "localhost" in gateway_url or "127.0.0.1" in gateway_url:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                print("🔓 Accepting self-signed certificate")

        # Connect
        print("⏳ Connecting...")
        ws = await websockets.connect(gateway_url, ssl=ssl_context)
        print("✅ WebSocket connected!")

        # Send connect handshake
        connect_msg = {
            "type": "req",
            "id": "1",
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "test-client",
                    "version": "1.0.0",
                    "platform": "python",
                    "mode": "test",
                },
            },
        }

        api_key = os.getenv("MOLTIS_API_KEY")
        if api_key:
            connect_msg["params"]["auth"] = {"apiKey": api_key}
            print("🔑 Using API key authentication")

        print("📤 Sending connect message...")
        await ws.send(json.dumps(connect_msg))

        # Wait for response
        print("⏳ Waiting for response...")
        response = await ws.recv()
        data = json.loads(response)

        if data.get("ok"):
            print("✅ Connected successfully!")
            print(f"📦 Response: {json.dumps(data, indent=2)}")

            # Try sending a test message
            test_msg = {
                "type": "req",
                "id": "2",
                "method": "chat.send",
                "params": {
                    "sessionKey": "agent:main:voice-test",
                    "message": "Hello! This is a test message. Please respond with just 'Test successful!'",
                    "idempotencyKey": "test-123",
                },
            }

            print("\n📤 Sending test chat message...")
            await ws.send(json.dumps(test_msg))

            print("⏳ Waiting for chat response (this may take a few seconds)...")

            # Wait for response (may be multiple events)
            timeout = 30
            start_time = asyncio.get_event_loop().time()
            got_response = False

            while asyncio.get_event_loop().time() - start_time < timeout:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(response)

                    print(
                        f"📩 Received: {data.get('type')} - {data.get('event', data.get('method', 'N/A'))}"
                    )

                    # Look for final chat response
                    if data.get("type") == "event" and data.get("event") == "chat":
                        payload = data.get("payload", {})
                        if payload.get("state") == "final":
                            msg = payload.get("message", {})
                            content = msg.get("content", [])
                            for item in content:
                                if item.get("type") == "text":
                                    text = item.get("text", "")
                                    print(f"\n✅ Got response: {text}")
                                    got_response = True
                                    break
                        if got_response:
                            break

                except asyncio.TimeoutError:
                    print("⏳ Still waiting...")
                    continue

            if not got_response:
                print("⚠️  No chat response received within timeout")
                print("💡 Check that an LLM provider is configured in Moltis Settings")

        else:
            error = data.get("error", {})
            print(f"❌ Connection failed: {error.get('message', 'Unknown error')}")
            print(f"📦 Full error: {json.dumps(data, indent=2)}")

            if "auth" in error.get("message", "").lower():
                print("\n💡 Tip: Authentication required")
                print("   1. Create API key in Moltis Settings → Security → API Keys")
                print("   2. Set MOLTIS_API_KEY=mk_... in .env")

        await ws.close()
        print("\n🔌 Connection closed")

    except ConnectionRefusedError:
        print("❌ Connection refused - is Moltis running?")
        print("💡 Start Moltis with: moltis")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    print("=== Moltis Connection Test ===\n")
    asyncio.run(test_connection())
