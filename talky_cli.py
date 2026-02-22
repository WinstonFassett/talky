#!/usr/bin/env python3
"""Talky CLI Tool

Install with: uv tool install talky -e .
Run from anywhere: talky moltis
"""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

# Determine project root from this script's location
_script_path = Path(__file__).resolve()
_root = _script_path.parent
server_dir = _root / "server"

# Add project root + server to path
sys.path.insert(0, str(_root))
sys.path.insert(0, str(server_dir))


def kill_port_7860():
    """Kill any processes using port 7860."""
    try:
        # Find process using port 7860
        result = subprocess.run(
            ["lsof", "-ti", ":7860"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                try:
                    subprocess.run(["kill", "-9", pid], capture_output=True)
                    print(f"Killed process {pid} on port 7860")
                except subprocess.SubprocessError:
                    pass
        else:
            # No process found on port 7860
            pass
    except subprocess.SubprocessError:
        # lsof command failed (probably not on macOS/Linux)
        pass


def start_client_dev_server():
    """Start the client dev server if not already running."""
    try:
        # Check if port 5173 is already in use
        result = subprocess.run(
            ["lsof", "-ti", ":5173"],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            print("📱 Client dev server already running on port 5173")
            return True
        
        # Start the client dev server
        client_dir = server_dir.parent / "client"
        if not client_dir.exists():
            print("⚠️ Client directory not found")
            return False
            
        print("📱 Starting client dev server...")
        subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(client_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("📱 Client dev server starting on http://localhost:5173")
        
        # Wait a bit for the server to start
        import time
        time.sleep(3)
        return True
        
    except subprocess.SubprocessError:
        print("⚠️ Could not start client dev server")
        return False
    except FileNotFoundError:
        print("⚠️ npm not found. Install Node.js and npm first.")
        return False


def cmd_say(args):
    """Handle the 'say' subcommand."""
    # Set log level environment variable if specified
    if getattr(args, "log_level", None):
        os.environ["TALKY_LOG_LEVEL"] = args.log_level
    
    # Ensure dependencies are installed before importing
    from shared.dependency_installer import ensure_dependencies
    
    if not ensure_dependencies():
        print("❌ Failed to install required dependencies")
        sys.exit(1)
    
    from shared.daemon_protocol import daemon_is_running

    # server_dir is already defined globally from script location

    # Daemon management sub-actions
    if args.start_daemon or args.stop_daemon or args.daemon_status:
        python_path = server_dir.parent / ".venv" / "bin" / "python"
        if not python_path.exists():
            python_path = server_dir.parent / ".venv" / "Scripts" / "python.exe"  # Windows
        
        if not python_path.exists():
            print("Virtual environment not found. Run 'uv sync' first.")
            sys.exit(1)
            
        cmd = [str(python_path), str(server_dir / "tts_daemon.py")]
        if args.start_daemon:
            cmd.append("--start")
        elif args.stop_daemon:
            cmd.append("--stop")
        elif args.daemon_status:
            cmd.append("--status")
        result = subprocess.run(cmd)
        sys.exit(result.returncode)

    if args.list_profiles:
        python_path = server_dir.parent / ".venv" / "bin" / "python"
        if not python_path.exists():
            python_path = server_dir.parent / ".venv" / "Scripts" / "python.exe"  # Windows
        
        if not python_path.exists():
            print("Virtual environment not found. Run 'uv sync' first.")
            sys.exit(1)
            
        cmd = [str(python_path), str(server_dir / "tts_daemon.py"), "--list-profiles"]
        result = subprocess.run(cmd)
        sys.exit(result.returncode)

    if not args.text:
        print("Usage: talky say <text>")
        sys.exit(1)

    if args.no_daemon:
        # Direct mode — no daemon
        import asyncio

        from server.say_command import say_text

        success = asyncio.run(
            say_text(
                text=args.text,
                voice_profile=args.voice_profile,
                provider=args.provider,
                voice_id=args.voice,
                output_file=args.output,
            )
        )
        sys.exit(0 if success else 1)

    if daemon_is_running():
        # Use lightweight client with venv python
        python_path = server_dir.parent / ".venv" / "bin" / "python"
        if not python_path.exists():
            python_path = server_dir.parent / ".venv" / "Scripts" / "python.exe"  # Windows
        if not python_path.exists():
            print("Virtual environment not found. Run 'uv sync' first.")
            sys.exit(1)
        cmd = [str(python_path), str(server_dir / "tts_client.py"), args.text]
    else:
        # Auto-start daemon, use client with wait
        python_path = server_dir.parent / ".venv" / "bin" / "python"
        if not python_path.exists():
            python_path = server_dir.parent / ".venv" / "Scripts" / "python.exe"  # Windows
        
        if not python_path.exists():
            print("Virtual environment not found. Run 'uv sync' first.")
            sys.exit(1)
            
        subprocess.Popen(
            [str(python_path), str(server_dir / "tts_daemon.py"), "--start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=server_dir
        )
        cmd = [str(python_path), str(server_dir / "tts_client.py"), "--wait", "15", args.text]

    if args.voice_profile:
        cmd.extend(["-p", args.voice_profile])
    if args.provider:
        cmd.extend(["--provider", args.provider])
    if args.voice:
        cmd.extend(["--voice", args.voice])
    if args.output:
        cmd.extend(["-o", args.output])

    # Run from server directory
    result = subprocess.run(cmd, cwd=str(server_dir))
    sys.exit(result.returncode)


def cmd_run(args):
    """Handle the 'run' subcommand (bot)."""
    # server_dir is already defined globally from script location
    
    # Start client dev server FIRST if not running
    start_client_dev_server()
    
    # Kill any existing process on port 7860 (after Vite is ready)
    kill_port_7860()
    
    # Ensure server dependencies are installed
    from shared.dependency_installer import ensure_dependencies_for_server
    
    if not ensure_dependencies_for_server(server_dir):
        print("❌ Failed to install required dependencies")
        sys.exit(1)

    if args.list_profiles:
        cmd_list_profiles(args)
        return

    talky_profile = getattr(args, "profile", None) or getattr(args, "profile_flag", None)

    if not talky_profile:
        print("No profile specified. Use: talky <profile> or talky --profile <name>")
        print("List profiles with: talky --list-profiles")
        sys.exit(1)

    cmd = [
        "uv",
        "run",
        "--directory",
        str(server_dir),
        "python",
        "main.py",
        "--profile",
        talky_profile,
    ]

    if getattr(args, "voice_profile", None):
        cmd.extend(["--voice-profile", args.voice_profile])
    if getattr(args, "debug_client", False):
        cmd.append("--debug-client")
    if getattr(args, "minimal", False):
        cmd.append("--minimal")
    if getattr(args, "essential", False):
        cmd.append("--essential")
    if getattr(args, "no_open", False):
        cmd.append("--no-open")
    if getattr(args, "local_speech", False):
        cmd.append("--local-speech")

    if getattr(args, "log_level", None):
        cmd.extend(["--log-level", args.log_level])

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_config(args):
    """Setup wizard for talky configuration."""
    import shutil
    
    config_dir = Path.home() / ".talky"
    bundled_defaults = _root / "server" / "config" / "defaults"
    
    print(f"🔧 Talky Configuration")
    print(f"Config directory: {config_dir}")
    
    # Create config directory
    config_dir.mkdir(exist_ok=True)
    credentials_dir = config_dir / "credentials"
    credentials_dir.mkdir(exist_ok=True)
    
    # Copy default configs if they don't exist
    config_files = [
        "voice-profiles.yaml",
        "talky-profiles.yaml", 
        "llm-backends.yaml",
        "voice-backends.yaml",
        "settings.yaml"
    ]
    
    for config_file in config_files:
        dest = config_dir / config_file
        source = bundled_defaults / config_file
        
        if not dest.exists():
            if source.exists():
                shutil.copy(source, dest)
                print(f"✅ Created {str(dest)}")
            else:
                print(f"⚠️  Missing default: {config_file}")
        else:
            print(f"✅ {str(dest)} already exists")
    
    print(f"\n📝 Edit configs at: {str(config_dir.resolve())}")
    print(f"🔑 Add API keys to: {str(credentials_dir.resolve())}/")
    print(f"\nExample voice profiles:")
    print(f"  local_only: Kokoro TTS + Whisper STT (no keys)")
    print(f"  cloud_user: Google TTS + Deepgram STT (needs API keys)")
    
    if args.list_examples:
        print(f"\n📋 Available voice profiles:")
        try:
            from server.config.profile_manager import get_profile_manager
            pm = get_profile_manager()
            for name, desc in pm.list_voice_profiles().items():
                print(f"  {name}: {desc}")
        except Exception as e:
            print(f"  (Run 'talky say hello' first to install dependencies)")


def cmd_list_profiles(args):
    """List all available profiles."""
    from server.config.profile_manager import get_profile_manager
    
    try:
        pm = get_profile_manager()
        print("LLM Backends:")
        for name, desc in pm.list_llm_backends().items():
            print(f"  {name:<12} - {desc}")
        print("\nVoice Profiles:")
        for name, desc in pm.list_voice_profiles().items():
            print(f"  {name:<15} - {desc}")
        print("\nTalky Profiles:")
        for name, desc in pm.list_talky_profiles().items():
            print(f"  {name:<20} - {desc}")
    except FileNotFoundError as e:
        print(f"❌ Configuration files not found: {e}")
        print("Run 'talky config' to create configuration files.")
    except Exception as e:
        print(f"❌ Error loading profiles: {e}")


def cmd_mcp(args):
    """Start MCP server."""
    try:
        result = subprocess.run(["lsof", "-ti:9090"], capture_output=True, text=True)
        if result.stdout.strip():
            print("MCP server already running on port 9090")
            return
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        logging.debug(f"Could not check port 9090: {e}")

    voice_profile = getattr(args, 'voice_profile', None)
    
    # Validate voice profile if provided
    if voice_profile:
        try:
            from server.config.profile_manager import get_profile_manager
            pm = get_profile_manager()
            available_profiles = pm.list_voice_profiles()
            if voice_profile not in available_profiles:
                print(f"❌ Unknown voice profile: {voice_profile}")
                print(f"Available profiles: {', '.join(available_profiles.keys())}")
                sys.exit(1)
        except Exception as e:
            print(f"⚠️  Could not validate voice profile: {e}")
    
    if voice_profile:
        print(f"Starting MCP server with voice profile: {voice_profile}")
    else:
        print("Starting MCP server...")
    
    try:
        # Add mcp-server to path and import
        mcp_server_path = _root / "mcp-server" / "src"
        sys.path.insert(0, str(mcp_server_path))
        from pipecat_mcp_server.server import main as mcp_main
        try:
            mcp_main()
        except Exception as e:
            print(f"❌ MCP server failed to start: {e}")
            sys.exit(1)
    except ImportError:
        # Fall back to subprocess if dependencies not available
        cmd = ["uv", "run", "--directory", str(_root / "mcp-server"), "python", "-m", "pipecat_mcp_server.server"]
        if voice_profile:
            cmd.extend(["--voice-profile", voice_profile])
        try:
            result = subprocess.run(cmd, cwd=_root)
            sys.exit(result.returncode)
        except Exception as e:
            print(f"❌ Failed to start MCP server via subprocess: {e}")
            sys.exit(1)


def main():
    """Main CLI entry point."""
    # Shortcut: treat first non-option, non-command arg as profile name
    known_commands = {"say", "config", "mcp", "ls"}
    if len(sys.argv) > 1 and sys.argv[1] not in known_commands and not sys.argv[1].startswith("-"):
        profile_name = sys.argv.pop(1)
        sys.argv.insert(1, "--profile")
        sys.argv.insert(2, profile_name)

    parser = argparse.ArgumentParser(description="Talky Voice Bot CLI")
    subparsers = parser.add_subparsers(dest="command")

    # === config subcommand ===
    config_parser = subparsers.add_parser("config", help="Setup configuration")
    config_parser.add_argument("--list-examples", "-l", action="store_true", help="List available profiles")
    config_parser.set_defaults(func=cmd_config)

    # === say subcommand ===
    say_parser = subparsers.add_parser("say", help="Text-to-speech")
    say_parser.add_argument("text", nargs="?", help="Text to speak")
    say_parser.add_argument("-p", "--voice-profile", help="Voice profile")
    say_parser.add_argument("--provider", help="TTS provider")
    say_parser.add_argument("--voice", help="Voice ID")
    say_parser.add_argument("-o", "--output", help="Save to file")
    say_parser.add_argument("-l", "--list-profiles", action="store_true")
    say_parser.add_argument("--no-daemon", action="store_true", help="Skip daemon")
    say_parser.add_argument("--start-daemon", action="store_true")
    say_parser.add_argument("--stop-daemon", action="store_true")
    say_parser.add_argument("--daemon-status", action="store_true")
    say_parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Set logging level (default: ERROR)")
    say_parser.set_defaults(func=cmd_say)

    # === mcp subcommand ===
    mcp_parser = subparsers.add_parser("mcp", help="Start MCP server")
    mcp_parser.add_argument("--voice-profile", "-v", help="Voice profile to use")
    mcp_parser.set_defaults(func=cmd_mcp)

    # === ls subcommand ===
    ls_parser = subparsers.add_parser("ls", help="List profiles")
    ls_parser.set_defaults(func=lambda args: cmd_list_profiles(args))

    # === Main bot arguments (default command) ===
    parser.add_argument("profile", nargs="?", help="Talky profile name")
    parser.add_argument("--profile", "-p", dest="profile_flag", help="Talky profile")
    parser.add_argument("--voice-profile", "-v", help="Voice profile override")
    parser.add_argument("--list-profiles", "-l", action="store_true", help="List available profiles")
    parser.add_argument("--debug-client", "-d", action="store_true", help="Use Pipecat debug client instead of custom React client")
    parser.add_argument("--minimal", "-m", action="store_true", help="Minimal mode")
    parser.add_argument("--essential", "-e", action="store_true", help="Essential mode")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    parser.add_argument("--local-speech", action="store_true", help="Use local speech")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Set logging level (default: ERROR)")

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        # No subcommand, use default profile or show help
        if not args.profile and not args.profile_flag:
            # Use openclaw as default (now uses defaults from settings)
            args.profile = "openclaw"
        cmd_run(args)


if __name__ == "__main__":
    main()
