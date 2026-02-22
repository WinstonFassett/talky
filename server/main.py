#!/usr/bin/env python3
"""Wrapper script for running bot with profile support"""

import argparse
import os
import sys

# CRITICAL: Configure logging BEFORE any other imports to beat pipecat's loguru setup
from logging_config import setup_logging
setup_logging()  # Call this before importing pipecat or anything that uses loguru

# Monkey patch logger.add to intercept pipecat's logging setup
from loguru import logger
original_add = logger.add
original_remove = logger.remove

def patched_add(*args, **kwargs):
    # Force the log level to match what user requested via CLI
    talky_log_level = os.environ.get("TALKY_LOG_LEVEL", "ERROR")
    if 'level' in kwargs:
        kwargs['level'] = talky_log_level
    return original_add(*args, **kwargs)

def patched_remove(handler_id=None):
    return original_remove(handler_id)

# Apply the monkey patches
logger.add = patched_add
logger.remove = patched_remove

# Also patch standard Python logging to catch uvicorn
import logging
original_getLogger = logging.getLogger

def patched_getLogger(name=None):
    logger_obj = original_getLogger(name)
    # Force ERROR level for all loggers
    talky_log_level = os.environ.get("TALKY_LOG_LEVEL", "ERROR")
    logger_obj.setLevel(logging.getLevelName(talky_log_level))
    return logger_obj

logging.getLogger = patched_getLogger


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
        help="Set logging level (default: ERROR)",
    )

    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

    parser.add_argument(
        "--profile", help="Talky profile to use (combines LLM backend and voice profile)"
    )

    parser.add_argument("--config-dir", "-c", help="Config directory (default: ~/.talky)")

    parser.add_argument("--session", "-s", help="Override session key for LLM backend")

    args, remaining = parser.parse_known_args()

    # Handle list profiles
    if args.list_profiles:
        from config.profile_manager import get_profile_manager
        
        pm = get_profile_manager(config_dir=args.config_dir)

        print("🤖 LLM Backends:")
        for name, desc in pm.list_llm_backends().items():
            print(f"  {name:<12} - {desc}")

        print("\n🎤 Voice Profiles:")
        for name, desc in pm.list_voice_profiles().items():
            print(f"  {name:<15} - {desc}")

        print("\n🤖+🎤 Talky Profiles:")
        for name, desc in pm.list_talky_profiles().items():
            print(f"  {name:<20} - {desc}")
        return

    # Handle local-speech flag
    voice_profile = args.voice_profile
    if args.local_speech:
        if voice_profile:
            print("⚠️  --local-speech overrides --voice-profile")
        # Use configured local speech profile, don't hardcode
        try:
            from config.profile_manager import get_profile_manager

            pm = get_profile_manager(config_dir=args.config_dir)

            # Find first local speech profile
            profiles = pm.list_voice_profiles()
            local_profiles = [p for p in profiles if "local" in p.lower()]
            voice_profile = local_profiles[0] if local_profiles else None
        except ImportError:
            voice_profile = None
        print("🎤 Using local speech: Whisper STT + Kokoro TTS")

    # Setup logging with CLI argument support (update if needed)
    if getattr(args, 'log_level', None):
        from logging_config import setup_logging
        setup_logging(args.log_level)

    # Use provided talky profile or require explicit selection
    from config.profile_manager import get_profile_manager
    
    pm = get_profile_manager(config_dir=args.config_dir)

    talky_profile_name = getattr(args, "profile", None)
    if not talky_profile_name:
        print("❌ Error: --profile is required")
        print("\nAvailable talky profiles:")
        for name, desc in pm.list_talky_profiles().items():
            print(f"  {name:<20} - {desc}")
        return

    # Get the talky profile
    talky_profile = pm.get_talky_profile(talky_profile_name)
    if not talky_profile:
        print(f"❌ Error: Unknown talky profile '{talky_profile_name}'")
        return

    print(f"🤖+🎤 Talky Profile: {talky_profile_name}")
    print(f"   LLM Backend: {talky_profile.llm_backend}")

    # Use user-specified voice profile if provided, otherwise use talky profile default
    final_voice_profile = voice_profile if voice_profile else talky_profile.voice_profile
    print(f"   Voice Profile: {final_voice_profile}")

    # Call bot directly with parameters (no env vars needed)
    import asyncio
    import bot
    import webbrowser
    import threading
    import time

    def auto_open_browser():
        """Auto-open browser to appropriate UI based on mode."""
        if args.no_open:
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
    # Add appropriate verbose flag for pipecat based on our log level
    if getattr(args, 'log_level', None) == 'ERROR':
        # Don't add verbose flag for ERROR level (pipecat defaults to DEBUG)
        pass
    elif getattr(args, 'log_level', None) in ['DEBUG', 'INFO', 'WARNING']:
        # Add verbose flag for more verbose levels
        remaining.append('--verbose')
    
    sys.argv = [sys.argv[0]] + remaining

    # Set environment variables for bot() function (Pipecat calls bot(), not run_bot)
    os.environ["LLM_BACKEND"] = talky_profile.llm_backend
    os.environ["VOICE_PROFILE"] = final_voice_profile
    if args.config_dir:
        os.environ["CONFIG_DIR"] = args.config_dir
    if args.session:
        os.environ["SESSION_KEY"] = args.session

    # Call Pipecat's main which will call bot() with proper transport
    from pipecat.runner.run import main

    main()


def run_bot_main(transport, llm_profile_name: str = None, voice_profile_name: str = None, session_key: str = None):
    """Run bot with given transport and profiles - for programmatic use"""
    # Import and run the actual bot
    # Call bot with the transport
    import asyncio

    import bot

    return asyncio.run(bot.run_bot(transport, llm_profile_name, voice_profile_name, session_key))


if __name__ == "__main__":
    main()
