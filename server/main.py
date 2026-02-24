#!/usr/bin/env python3
"""Wrapper script for running bot with profile support"""

import argparse
import os
import sys
from pathlib import Path

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

    parser.add_argument("--host", help="Host address for Pipecat server (default: localhost)")
    
    parser.add_argument("--ssl", action="store_true", help="Enable HTTPS with self-signed certificates")

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

            # Determine host and protocol using shared network utility
            host = getattr(args, 'host', 'localhost')
            try:
                from config.profile_manager import get_profile_manager
                pm = get_profile_manager()
                network_config = getattr(pm, 'settings', {}).get("network", {})
                config_host = network_config.get("host", "localhost")
                external_host = network_config.get("external_host")
                
                # Import shared network utility
                import sys
                sys.path.append(str(Path(__file__).parent.parent))
                from shared.network_utils import detect_external_hostname, get_browser_url
                
                # Get port configuration
                frontend_port = network_config.get("frontend_port", 5173)
                backend_port = network_config.get("backend_port", 7860)
                
                if host == '0.0.0.0':
                    # For external binding, detect actual hostname
                    detected_host = detect_external_hostname(config_host, external_host)
                    if detected_host != config_host:
                        print(f"⚠️  No external_host configured in settings.yaml, using detected hostname: {detected_host}")
                        print("   Set external_host in ~/.talky/settings.yaml for reliable external access")
                    host = detected_host
                
                protocol = 'https' if getattr(args, 'ssl', False) else 'http'
                vite_url = get_browser_url(host, frontend_port, getattr(args, 'ssl', False))
                debug_url = get_browser_url(host, backend_port, getattr(args, 'ssl', False))
                
            except Exception as e:
                print(f"⚠️  Could not detect external hostname: {e}")
                print("   Configure external_host in settings.yaml for reliable external access")
                host = 'localhost'
                protocol = 'https' if getattr(args, 'ssl', False) else 'http'
                # Use default ports for fallback
                frontend_port = 5173
                backend_port = 7860
                vite_url = f"{protocol}://{host}:{frontend_port}?autoconnect=true"
                debug_url = f"{protocol}://{host}:{backend_port}/client?autoconnect=true"

            # Try to open custom Vite client first
            try:
                webbrowser.open(vite_url)
                print(f"🌐 Opened browser to custom UI: {vite_url}")
            except Exception as e:
                print(f"⚠️  Could not auto-open browser: {e}")
                print(f"🔗 Connect manually to: {vite_url}")
                
                # Fallback to debug UI
                try:
                    webbrowser.open(debug_url)
                    print(f"🌐 Opened browser to debug UI (fallback): {debug_url}")
                except Exception as e2:
                    print(f"⚠️  Could not open debug UI either")
                    print(f"🔗 Debug UI fallback: {debug_url}")

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
    
    # Add host argument if provided
    if args.host:
        remaining.extend(['--host', args.host])
    
    # Add SSL arguments if requested
    if args.ssl:
        # Validate certificate files exist
        from pathlib import Path
        server_dir = Path(__file__).parent
        key_file = server_dir / "server-key.pem"
        cert_file = server_dir / "server-cert.pem"
        
        if not key_file.exists():
            print(f"❌ SSL private key not found: {key_file}")
            sys.exit(1)
        
        if not cert_file.exists():
            print(f"❌ SSL certificate not found: {cert_file}")
            sys.exit(1)
        
        # Set environment variables for monkey patch
        os.environ["SSL_ENABLED"] = "1"
        os.environ["SSL_KEYFILE"] = "server-key.pem"
        os.environ["SSL_CERTFILE"] = "server-cert.pem"
    
    sys.argv = [sys.argv[0]] + remaining

    # Set environment variables for bot() function (Pipecat calls bot(), not run_bot)
    os.environ["LLM_BACKEND"] = talky_profile.llm_backend
    os.environ["VOICE_PROFILE"] = final_voice_profile
    if args.config_dir:
        os.environ["CONFIG_DIR"] = args.config_dir
    if args.session:
        os.environ["SESSION_KEY"] = args.session

    # Call Pipecat's main which will call bot() with proper transport
    # Monkey patch uvicorn to support SSL from environment variables
    import uvicorn
    original_run = uvicorn.run
    
    def patched_run(*args, **kwargs):
        # Add SSL from environment if SSL is enabled
        if os.getenv('SSL_ENABLED'):
            if 'ssl_keyfile' not in kwargs and os.getenv('SSL_KEYFILE'):
                kwargs['ssl_keyfile'] = os.getenv('SSL_KEYFILE')
            if 'ssl_certfile' not in kwargs and os.getenv('SSL_CERTFILE'):
                kwargs['ssl_certfile'] = os.getenv('SSL_CERTFILE')
        return original_run(*args, **kwargs)
    
    uvicorn.run = patched_run
    
    from pipecat.runner.run import main

    try:
        main()
    finally:
        # Clean up HTTP sessions
        try:
            from shared.service_factory import close_http_sessions
            close_http_sessions()
        except Exception:
            pass  # Silently fail during shutdown


def run_bot_main(transport, llm_profile_name: str = None, voice_profile_name: str = None, session_key: str = None):
    """Run bot with given transport and profiles - for programmatic use"""
    # Import and run the actual bot
    # Call bot with the transport
    import asyncio

    import bot

    return asyncio.run(bot.run_bot(transport, llm_profile_name, voice_profile_name, session_key))


if __name__ == "__main__":
    main()
