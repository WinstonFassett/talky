#!/usr/bin/env python3
"""Test script for unified backend system

Tests that all backends can be created and initialized properly.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

load_dotenv(override=True)

from profiles import get_profile, list_profiles

from backends import BackendFactory, backend_manager, create_backend_from_profile


async def test_backend_creation():
    """Test that all backends can be created from profiles."""
    print("🧪 Testing Backend Creation")
    print("=" * 50)

    profiles = list_profiles()
    print(f"Available profiles: {profiles}")

    for profile_name in profiles:
        try:
            print(f"\n📝 Testing profile: {profile_name}")
            profile = get_profile(profile_name)
            print(f"  Description: {profile.description}")
            print(f"  LLM Service: {profile.llm_service}")
            print(f"  Kwargs: {profile.llm_kwargs}")

            # Create backend
            backend = create_backend_from_profile(profile)
            print(f"  ✅ Backend created: {backend.__class__.__name__}")

            # Get backend info
            info = backend.get_backend_info()
            print(f"  📊 Backend info: {info['name']} ({info['type']})")
            print(f"  🎯 Features: {', '.join(info['features'])}")

        except Exception as e:
            print(f"  ❌ Failed: {e}")

    print("\n" + "=" * 50)


async def test_backend_factory():
    """Test backend factory directly."""
    print("\n🏭 Testing Backend Factory")
    print("=" * 50)

    available = BackendFactory.list_backends()
    print(f"Available backends: {available}")

    for backend_name in available:
        try:
            print(f"\n🔧 Creating backend: {backend_name}")

            # Test with minimal config
            if backend_name == "moltis":
                backend = BackendFactory.create_backend(backend_name, session_strategy="new")
            elif backend_name == "openclaw":
                backend = BackendFactory.create_backend(
                    backend_name, gateway_url="ws://localhost:18789"
                )

            print(f"  ✅ Backend created: {backend.__class__.__name__}")

            # Test initialization (might fail if services aren't running)
            try:
                await backend.initialize()
                print(f"  ✅ Backend initialized successfully")
                await backend.cleanup()
            except Exception as e:
                print(f"  ⚠️  Initialization failed (expected if service not running): {e}")

        except Exception as e:
            print(f"  ❌ Failed to create backend: {e}")

    print("\n" + "=" * 50)


async def test_backend_manager():
    """Test backend manager functionality."""
    print("\n👨‍💼 Testing Backend Manager")
    print("=" * 50)

    try:
        # Create and register backends
        moltis_backend = BackendFactory.create_backend("moltis", session_strategy="new")
        openclaw_backend = BackendFactory.create_backend(
            "openclaw", gateway_url="ws://localhost:18789"
        )

        backend_manager.register_backend("test-moltis", moltis_backend)
        backend_manager.register_backend("test-openclaw", openclaw_backend)

        print(f"📋 Registered backends: {backend_manager.list_backends()}")

        # Test switching
        current = backend_manager.get_current_backend()
        print(f"📍 Current backend: {current}")

        # Switch to Moltis
        await backend_manager.switch_backend("test-moltis")
        current = backend_manager.get_current_backend()
        print(f"🔄 Switched to: {current.__class__.__name__}")

        # Switch to OpenClaw
        await backend_manager.switch_backend("test-openclaw")
        current = backend_manager.get_current_backend()
        print(f"🔄 Switched to: {current.__class__.__name__}")

        # Cleanup
        await backend_manager.cleanup_all()
        print("✅ All backends cleaned up")

    except Exception as e:
        print(f"❌ Backend manager test failed: {e}")

    print("\n" + "=" * 50)


async def main():
    """Run all tests."""
    print("🚀 Testing Unified Backend System")
    print("=" * 60)

    await test_backend_creation()
    await test_backend_factory()
    await test_backend_manager()

    print("\n✅ All tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
