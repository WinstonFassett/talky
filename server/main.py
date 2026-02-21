#!/usr/bin/env python3
"""Wrapper script for running bot with profile support"""

import argparse
import os
import sys


def main():
    """Main wrapper that handles profile selection then calls bot.py"""
    parser = argparse.ArgumentParser(description="Voice Bot with Modular LLM Support")

    parser.add_argument("--llm-backend", "-l", help="LLM backend to use (openclaw, moltis, pi)")

    parser.add_argument(
        "--voice-profile", "-v", help="Voice profile to use (configured in backends.yaml)"
    )

    parser.add_argument(
        "--local-speech",
        action="store_true",
        help="Use local speech profile (configured in backends.yaml)",
    )

    parser.add_argument("--list-profiles", action="store_true", help="List available profiles")

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level (default: INFO)",
    )

    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Quiet mode - only show warnings and errors"
    )

    parser.add_argument(
        "--debug", "-d", action="store_true", help="Debug mode - show all logs (not recommended)"
    )

    parser.add_argument(
        "--minimal",
        "-m",
        action="store_true",
        help="Minimal mode - only errors and critical issues",
    )

    parser.add_argument(
        "--essential",
        "-e",
        action="store_true",
        help="Essential mode - warnings + key bot events (default)",
    )

    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

    parser.add_argument(
        "--profile", help="Talky profile to use (combines LLM backend and voice profile)"
    )

    args, remaining = parser.parse_known_args()

    # Handle list profiles
    if args.list_profiles:
        from config.profile_manager import profile_manager

        print("🤖 LLM Backends:")
        for name, desc in profile_manager.list_llm_backends().items():
            print(f"  {name:<12} - {desc}")

        print("\n🎤 Voice Profiles:")
        for name, desc in profile_manager.list_voice_profiles().items():
            print(f"  {name:<15} - {desc}")

        print("\n🤖+🎤 Talky Profiles:")
        for name, desc in profile_manager.list_talky_profiles().items():
            print(f"  {name:<20} - {desc}")
        return

    # Handle local-speech flag
    voice_profile = args.voice_profile
    if args.local_speech:
        if voice_profile:
            print("⚠️  --local-speech overrides --voice-profile")
        # Use configured local speech profile, don't hardcode
        try:
            from config.profile_manager import profile_manager

            # Find first local speech profile
            profiles = profile_manager.list_voice_profiles()
            local_profiles = [p for p in profiles if "local" in p.lower()]
            voice_profile = local_profiles[0] if local_profiles else None
        except ImportError:
            voice_profile = None
        print("🎤 Using local speech: Whisper STT + Kokoro TTS")

    # Set logging level based on arguments
    if args.minimal:
        os.environ["TALKY_LOG_LEVEL"] = "ERROR"
        from logging_config import setup_minimal_logging

        setup_minimal_logging()
    elif args.quiet:
        os.environ["TALKY_LOG_LEVEL"] = "ERROR"
        from logging_config import configure_logging

        configure_logging()
    elif args.debug:
        os.environ["TALKY_LOG_LEVEL"] = "DEBUG"
        from logging_config import setup_debug_logging

        setup_debug_logging()
    elif args.log_level:
        os.environ["TALKY_LOG_LEVEL"] = args.log_level
        from logging_config import configure_logging

        configure_logging()
    else:
        # Default to essential logging
        from logging_config import setup_essential_logging

        setup_essential_logging()

    # Use provided talky profile or require explicit selection
    from config.profile_manager import profile_manager

    talky_profile_name = getattr(args, "profile", None)
    if not talky_profile_name:
        print("❌ Error: --profile is required")
        print("\nAvailable talky profiles:")
        for name, desc in profile_manager.list_talky_profiles().items():
            print(f"  {name:<20} - {desc}")
        return

    # Get the talky profile
    talky_profile = profile_manager.get_talky_profile(talky_profile_name)
    if not talky_profile:
        print(f"❌ Error: Unknown talky profile '{talky_profile_name}'")
        return

    print(f"🤖+🎤 Talky Profile: {talky_profile_name}")
    print(f"   LLM Backend: {talky_profile.llm_backend}")

    # Use user-specified voice profile if provided, otherwise use talky profile default
    final_voice_profile = voice_profile if voice_profile else talky_profile.voice_profile
    print(f"   Voice Profile: {final_voice_profile}")

    # Set profiles in environment for bot
    os.environ["LLM_BACKEND"] = talky_profile.llm_backend
    os.environ["VOICE_PROFILE"] = final_voice_profile

    # Import and run the actual bot
    import subprocess
    import threading
    import time

    # Add auto-open browser functionality
    import webbrowser

    import bot

    def auto_open_browser():
        """Auto-open browser to appropriate UI based on mode."""
        if args.no_open or os.environ.get("NO_OPEN_BROWSER") == "1":
            return

        def delayed_open():
            # Wait for server to be ready
            time.sleep(3)

            # Try to open custom Vite client first
            vite_url = "http://localhost:5173?autoconnect=true"
            try:
                webbrowser.open(vite_url)
                print(f"🌐 Opened browser to custom UI: {vite_url}")
            except Exception as e:
                # Fallback to debug UI
                url = "http://localhost:7860/client?autoconnect=true"
                webbrowser.open(url)
                print(f"🌐 Opened browser to debug UI (fallback): {url}")

        # Start delayed browser open in background thread
        threading.Thread(target=delayed_open, daemon=True).start()
        print("🌐 Browser will open automatically...")

    # Start auto-open timer
    auto_open_browser()

    # Replace sys.argv to remove our args before passing to Pipecat
    sys.argv = [sys.argv[0]] + remaining

    # Call Pipecat's main which will call bot()
    from pipecat.runner.run import main

    main()


def run_bot_main(transport, llm_profile_name: str = None, voice_profile_name: str = None):
    """Run bot with given transport and profiles - for programmatic use"""
    # Import and run the actual bot
    # Call bot with the transport
    import asyncio

    import bot

    return asyncio.run(bot.run_bot(transport, llm_profile_name, voice_profile_name))


if __name__ == "__main__":
    main()
