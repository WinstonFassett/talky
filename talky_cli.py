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
import time
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


def validate_certificates(client_dir: Path, external_binding: bool) -> bool:
    """Validate SSL certificate files exist for HTTPS configuration."""
    if not external_binding:
        return True  # No validation needed for HTTP
    
    cert_file = client_dir / "localhost-cert.pem"
    key_file = client_dir / "localhost-key.pem"
    
    if not cert_file.exists():
        print(f"❌ SSL certificate not found: {cert_file}")
        print("   Generate certificates with: ./scripts/generate-certs.sh")
        return False
    
    if not key_file.exists():
        print(f"❌ SSL private key not found: {key_file}")
        print("   Generate certificates with: ./scripts/generate-certs.sh")
        return False
    
    return True


def start_client_dev_server(external_binding=False, host="localhost"):
    """Start the client dev server if not already running."""
    try:
        # Load profile manager to get network configuration
        try:
            from shared.profile_manager import get_profile_manager
            pm = get_profile_manager()
        except:
            pm = None
        
        # Check if port 5173 is already in use with retry logic
        # Get configured frontend port
        try:
            network_config = getattr(pm, 'settings', {}).get("network", {}) if pm else {}
            frontend_port = int(network_config.get("frontend_port", 5173))
        except:
            frontend_port = 5173
            
        port_in_use = False
        for attempt in range(3):  # Try 3 times to handle race conditions
            result = subprocess.run(
                ["lsof", "-ti", f":{frontend_port}"],
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                port_in_use = True
                break
            time.sleep(0.1)  # Brief delay between attempts
        
        if port_in_use:
            host_msg = "externally" if external_binding else "locally"
            print(f"📱 Client dev server already running {host_msg} on port {frontend_port}")
            return True
        
        # Start the client dev server
        client_dir = server_dir.parent / "client"
        if not client_dir.exists():
            print("⚠️ Client directory not found")
            return False
        
        # Validate certificates for HTTPS
        if not validate_certificates(client_dir, external_binding):
            return False
            

        if not (client_dir / "node_modules").exists():
            print("📦 Installing client dependencies...")
            result = subprocess.run(["npm", "install"], cwd=str(client_dir), capture_output=True, text=True)
            if result.returncode != 0:
                print(f"⚠️ npm install failed: {result.stderr.strip()}")
                return False

        print("📱 Starting client dev server...")
        if external_binding:
            print("🌐 External binding enabled (HTTPS for WebRTC)")
        
        # Choose config based on external binding
        config_file = "vite.config.https.ts" if external_binding else "vite.config.ts"
        npm_args = ["npm", "run", "dev:https" if external_binding else "dev"]
        
        # Set up environment variables for Vite
        env = os.environ.copy()
        env['VITE_HOST'] = host
        
        # Add HTTPS and external host configuration for external binding
        if external_binding:
            env['VITE_HTTPS'] = 'true'
            env['VITE_EXTERNAL_HOST'] = host
            # Set up backend URL for HTTPS
            try:
                network_config = getattr(pm, 'settings', {}).get("network", {}) if pm else {}
                backend_port = str(network_config.get("backend_port", "7860"))
                env['VITE_BOT_START_URL'] = f"https://{host}:{backend_port}/start"
            except:
                env['VITE_BOT_START_URL'] = f"https://{host}:7860/start"
        
        # Get backend port from config
        try:
            network_config = getattr(pm, 'settings', {}).get("network", {}) if pm else {}
            backend_port = str(network_config.get("backend_port", "7860"))
            env['VITE_BACKEND_PORT'] = backend_port
        except:
            env['VITE_BACKEND_PORT'] = "7860"
            
        # Start server with better error handling
        process = subprocess.Popen(
            npm_args,
            cwd=str(client_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait a moment and check if server started successfully
        try:
            stdout, stderr = process.communicate(timeout=5)
            if process.returncode != 0:
                print(f"❌ Failed to start client dev server:")
                if stderr:
                    print(f"   Error: {stderr.strip()}")
                if stdout:
                    print(f"   Output: {stdout.strip()}")
                return False
        except subprocess.TimeoutExpired:
            # Server is still running (which is good for dev server)
            pass
        
        host_desc = "externally (HTTPS)" if external_binding else "locally (HTTP)"
        print(f"📱 Client dev server starting {host_desc} on port {frontend_port}")
        
        # Wait a bit for the server to start
        time.sleep(2)
        
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
    
    from shared.daemon_protocol import daemon_is_running

    # server_dir is already defined globally from script location

    # Daemon management sub-actions
    if args.start_daemon or args.stop_daemon or args.daemon_status:
        # Check if we're in a uv tool environment and use uv run for proper dependency handling
        if ".local/share/uv/tools/" in sys.executable:
            # In tool environment, use uv run to ensure dependencies are available
            import shutil
            uv_cmd = shutil.which("uv")
            if uv_cmd:
                cmd = [uv_cmd, "run", str(server_dir / "voice_daemon.py")]
            else:
                cmd = [sys.executable, str(server_dir / "voice_daemon.py")]
        else:
            cmd = [sys.executable, str(server_dir / "voice_daemon.py")]
        
        if args.start_daemon:
            cmd.append("--start")
        elif args.stop_daemon:
            cmd.append("--stop")
        elif args.daemon_status:
            cmd.append("--status")
        result = subprocess.run(cmd)
        sys.exit(result.returncode)

    if args.list_profiles:
        # Check if we're in a uv tool environment and use uv run for proper dependency handling
        if ".local/share/uv/tools/" in sys.executable:
            import shutil
            uv_cmd = shutil.which("uv")
            if uv_cmd:
                cmd = [uv_cmd, "run", str(server_dir / "voice_daemon.py"), "--list-profiles"]
            else:
                cmd = [sys.executable, str(server_dir / "voice_daemon.py"), "--list-profiles"]
        else:
            cmd = [sys.executable, str(server_dir / "voice_daemon.py"), "--list-profiles"]
        result = subprocess.run(cmd)
        sys.exit(result.returncode)

    if not args.text:
        print("Usage: talky say <text>")
        sys.exit(1)

    if args.no_daemon:
        # Direct mode — no daemon, handle dependencies here
        import asyncio

        from shared.dependency_installer import ensure_dependencies, get_configured_providers
        
        if not ensure_dependencies(for_cli=True):
            print("❌ Failed to install required dependencies")
            sys.exit(1)

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

    # Daemon mode - let daemon handle its own dependencies
    if daemon_is_running():
        cmd = [sys.executable, str(server_dir / "tts_client.py"), args.text]
    else:
        # Auto-start daemon, use client with wait
        subprocess.Popen(
            [sys.executable, str(server_dir / "voice_daemon.py"), "--start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=server_dir
        )
        cmd = [sys.executable, str(server_dir / "tts_client.py"), "--wait", "15", args.text]

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
    
    # Check if profile has client preference
    profile_name = getattr(args, 'profile', None)
    if profile_name:
        from shared.profile_manager import get_profile_manager
        pm = get_profile_manager()
        profile = pm.get_talky_profile(profile_name)
        
        # Override debug-client flag based on profile preference
        if profile and hasattr(profile, 'client') and profile.client == "debug":
            args.debug_client = True
        elif profile and hasattr(profile, 'client') and profile.client == "vite":
            args.debug_client = False
        elif profile and hasattr(profile, 'backend') and profile.backend == "mcp":
            # MCP backend should use Vite client by default
            args.debug_client = False
    
    # CRITICAL: Setup logging early to catch dependency installer logs
    log_level = getattr(args, "log_level", None)
    if log_level:
        os.environ["TALKY_LOG_LEVEL"] = log_level
    
    # Import and setup logging before dependency check
    sys.path.insert(0, str(server_dir))
    from logging_config import setup_logging
    setup_logging(log_level)
    
    # Start client dev server FIRST if not running
    from shared.profile_manager import get_profile_manager
    pm = get_profile_manager()
    
    # Get host from config or command line
    host = getattr(args, "host", None)
    if not host:
        try:
            network_config = getattr(pm, 'settings', {}).get("network", {})
            host = network_config.get("host", "localhost")
        except:
            host = "localhost"
    
    external_binding = (host == "0.0.0.0")
    start_client_dev_server(external_binding, host)
    
    # Kill any existing process on port 7860 (after Vite is ready)
    kill_port_7860()
    
    # Ensure server dependencies are installed
    from shared.dependency_installer import ensure_dependencies

    if not ensure_dependencies():
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
        "uv", "run", "python", str(server_dir / "main.py"),
        "--profile",
        talky_profile,
    ]

    # Add host binding if specified
    if host and host != "localhost":
        cmd.extend(["--host", host])
        print(f"🌐 Using host binding: {host}")
        # Add SSL for external access (HTTPS required for WebRTC)
        cmd.extend(["--ssl"])
        print("🔒 HTTPS enabled for external access")

    if getattr(args, "voice_profile", None):
        cmd.extend(["--voice-profile", args.voice_profile])
    if getattr(args, "config_dir", None):
        cmd.extend(["--config-dir", args.config_dir])
    if getattr(args, "debug_client", False):
        cmd.append("--debug-client")
    if getattr(args, "no_open", False):
        cmd.append("--no-open")
    if getattr(args, "local_speech", False):
        cmd.append("--local-speech")

    if getattr(args, "log_level", None):
        cmd.extend(["--log-level", args.log_level])

    if getattr(args, "session", None):
        cmd.extend(["--session", args.session])

    result = subprocess.run(cmd, cwd=str(server_dir))
    sys.exit(result.returncode)


def cmd_auth(args):
    """Manage provider credentials."""
    from talky_auth import run_auth_tui
    run_auth_tui()


def cmd_ask(args):
    """Handle the 'ask' subcommand — speak text then listen for response."""
    if getattr(args, "log_level", None):
        os.environ["TALKY_LOG_LEVEL"] = args.log_level

    from shared.daemon_protocol import daemon_is_running

    if not args.text:
        print("Usage: talky ask <text>", file=sys.stderr)
        sys.exit(1)

    # Ensure daemon is running (auto-start if needed)
    if not daemon_is_running():
        subprocess.Popen(
            [sys.executable, str(server_dir / "voice_daemon.py"), "--start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=server_dir,
        )

    # Build voice_client command
    cmd = [
        sys.executable,
        str(server_dir / "voice_client.py"),
        "--cmd", "ask",
    ]

    if not daemon_is_running():
        cmd.extend(["--wait", "15"])

    cmd.append(args.text)

    if args.voice_profile:
        cmd.extend(["-p", args.voice_profile])
    if getattr(args, "provider", None):
        cmd.extend(["--provider", args.provider])
    if getattr(args, "voice", None):
        cmd.extend(["--voice", args.voice])
    if getattr(args, "silence_timeout", None):
        cmd.extend(["--silence-timeout", str(args.silence_timeout)])

    result = subprocess.run(cmd, cwd=str(server_dir))
    sys.exit(result.returncode)


def cmd_end_convo(args):
    """Handle the 'end-convo' subcommand — kill running browser pipeline session."""
    killed = False

    # Kill WebRTC server on 7860
    try:
        result = subprocess.run(["lsof", "-ti", ":7860"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            for pid in result.stdout.strip().split("\n"):
                try:
                    subprocess.run(["kill", "-9", pid], capture_output=True)
                    print(f"Killed process {pid} on port 7860")
                    killed = True
                except subprocess.SubprocessError:
                    pass
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    # Kill Vite on 5173
    try:
        result = subprocess.run(["lsof", "-ti", ":5173"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            for pid in result.stdout.strip().split("\n"):
                try:
                    subprocess.run(["kill", "-9", pid], capture_output=True)
                    print(f"Killed process {pid} on port 5173")
                    killed = True
                except subprocess.SubprocessError:
                    pass
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    if not killed:
        print("No active conversation session found")


def cmd_transcribe(args):
    """Handle the 'transcribe' subcommand."""
    # Set log level environment variable if specified
    if getattr(args, "log_level", None):
        os.environ["TALKY_LOG_LEVEL"] = args.log_level
    
    # Setup logging using the same pattern as other commands
    sys.path.insert(0, str(server_dir))
    from logging_config import setup_logging
    log_level = getattr(args, "log_level", None)
    setup_logging(log_level)
    
    try:
        import pyaudio  # noqa: F401
    except ImportError:
        print("pyaudio is required for transcription. Install with:")
        if ".local/share/uv/tools/" in sys.executable:
            print("  uv tool install --editable . --with pyaudio")
        else:
            print("  uv pip install pyaudio")
        sys.exit(1)

    import asyncio

    from server.transcribe import transcribe

    try:
        asyncio.run(
            transcribe(
                stt_provider=args.stt,
                stt_model=args.stt_model,
                voice_profile=args.voice_profile,
                output=args.output,
                fmt=args.fmt,
                timestamp=args.timestamp,
            )
        )
    except KeyboardInterrupt:
        pass


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
            from shared.profile_manager import get_profile_manager
            pm = get_profile_manager()
            for name, desc in pm.list_voice_profiles().items():
                print(f"  {name}: {desc}")
        except Exception as e:
            print(f"  (Run 'talky say hello' first to install dependencies)")


def cmd_list_profiles(args):
    """List all available profiles."""
    from shared.profile_manager import get_profile_manager
    
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
    
    # Get host from config or command line
    host = getattr(args, "host", None)
    if not host:
        try:
            from shared.profile_manager import get_profile_manager
            pm = get_profile_manager()
            network_config = getattr(pm, 'settings', {}).get("network", {})
            host = network_config.get("host", "localhost")
        except:
            host = "localhost"
    
    # Validate voice profile if provided
    if voice_profile:
        try:
            from shared.profile_manager import get_profile_manager
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
    
    if host and host != "localhost":
        print(f"🌐 Using host binding: {host}")
    
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
    known_commands = {"config", "say", "ask", "mcp", "ls", "auth", "pi", "claude", "transcribe", "end-convo"}
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
    say_parser.add_argument("-p", "-v", "--voice-profile", help="Voice profile")
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

    # === ask subcommand ===
    ask_parser = subparsers.add_parser("ask", help="Speak text then listen for response")
    ask_parser.add_argument("text", nargs="?", help="Text to speak before listening")
    ask_parser.add_argument("-p", "-v", "--voice-profile", help="Voice profile")
    ask_parser.add_argument("--provider", help="TTS provider")
    ask_parser.add_argument("--voice", help="Voice ID")
    ask_parser.add_argument("--silence-timeout", type=float, default=10.0, help="Seconds of no speech before giving up (default: 10)")
    ask_parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Set logging level")
    ask_parser.set_defaults(func=cmd_ask)

    # === end-convo subcommand ===
    end_convo_parser = subparsers.add_parser("end-convo", help="Kill running browser voice session")
    end_convo_parser.set_defaults(func=cmd_end_convo)

    # === mcp subcommand ===
    mcp_parser = subparsers.add_parser("mcp", help="Start MCP server")
    mcp_parser.add_argument("--voice-profile", "-v", help="Voice profile to use")
    mcp_parser.add_argument("--host", help="Override host binding (default: from config)")
    mcp_parser.set_defaults(func=cmd_mcp)

    # === ls subcommand ===
    ls_parser = subparsers.add_parser("ls", help="List profiles")
    ls_parser.set_defaults(func=lambda args: cmd_list_profiles(args))

    
    # === pi subcommand ===
    pi_parser = subparsers.add_parser("pi", help="Start Pi with voice")
    pi_parser.add_argument("--dir", "-d", help="Working directory for app (default: current)")
    
    def cmd_pi(args):
        # Create a simple object with the profile and copy all attributes
        class Args:
            def __init__(self, profile, **kwargs):
                self.profile = profile
                # Copy all attributes from the original args
                for key, value in kwargs.items():
                    setattr(self, key, value)
        
        # Copy all attributes from original args
        args_dict = {k: v for k, v in vars(args).items() if k != 'func'}
        args_obj = Args('pi', **args_dict)
        return cmd_run_client_profile(args_obj)
    
    pi_parser.set_defaults(func=cmd_pi)

    # === claude subcommand ===
    claude_parser = subparsers.add_parser("claude", help="Start Claude with voice")
    claude_parser.add_argument("--dir", "-d", help="Working directory for app (default: current)")
    
    def cmd_claude(args):
        # Create a simple object with the profile and copy all attributes
        class Args:
            def __init__(self, profile, **kwargs):
                self.profile = profile
                # Copy all attributes from the original args
                for key, value in kwargs.items():
                    setattr(self, key, value)
        
        # Copy all attributes from original args
        args_dict = {k: v for k, v in vars(args).items() if k != 'func'}
        args_obj = Args('claude', **args_dict)
        return cmd_run_client_profile(args_obj)
    
    claude_parser.set_defaults(func=cmd_claude)

    # === auth subcommand ===
    auth_parser = subparsers.add_parser("auth", help="Manage provider credentials")
    auth_parser.set_defaults(func=cmd_auth)

    # === transcribe subcommand ===
    tr_parser = subparsers.add_parser("transcribe", help="Live speech-to-text transcription")
    tr_parser.add_argument("-o", "--output", help="Write to file (default: stdout)")
    tr_parser.add_argument(
        "--format", dest="fmt", default="raw", choices=["raw", "markdown", "jsonl"],
        help="Output format (default: raw)",
    )
    tr_parser.add_argument("--stt", help="STT provider override")
    tr_parser.add_argument("--stt-model", help="STT model override")
    tr_parser.add_argument("--voice-profile", "-v", help="Use STT from this voice profile")
    tr_parser.add_argument("--timestamp", action="store_true", help="Include timestamps")
    tr_parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Set logging level (default: ERROR)")
    tr_parser.set_defaults(func=cmd_transcribe)

    # === Main bot arguments (default command) ===
    parser.add_argument("--profile", "-p", help="Talky profile to run")
    parser.add_argument("--dir", "-d", help="Working directory for app (default: current)")
    parser.add_argument("--voice-profile", "-v", help="Voice profile override")
    parser.add_argument("--config-dir", "-c", help="Config directory (default: ~/.talky)")
    parser.add_argument("--list-profiles", "-l", action="store_true", help="List available profiles")
    parser.add_argument("--debug-client", action="store_true", help="Use Pipecat debug client instead of custom React client")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    parser.add_argument("--local-speech", action="store_true", help="Use local speech")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Set logging level (default: ERROR)")
    parser.add_argument("--session", "-s", help="Override session key for LLM backend")

    parser.add_argument("--host", help="Override host binding (default: from config)")

    args = parser.parse_args()
    
    if hasattr(args, "func"):
        args.func(args)
    else:
        # No subcommand, use default profile or show help
        if not getattr(args, 'profile', None):
            # Use default from settings.yaml
            try:
                from shared.profile_manager import get_profile_manager
                pm = get_profile_manager()
                default_profile = pm.defaults.get("talky_profile")
                if default_profile and default_profile in pm.list_talky_profiles():
                    args.profile = default_profile
                else:
                    # Fallback to first available profile
                    profiles = pm.list_talky_profiles()
                    if profiles:
                        args.profile = profiles[0]
                    else:
                        print("❌ Error: No talky profiles configured.")
                        return
            except Exception as e:
                print(f"❌ Error loading profiles: {e}")
                return
        
        # We have a profile now - use cmd_run
        cmd_run(args)


def cmd_run_client_profile(args):
    """Run an app profile (e.g., 'talky pi', 'talky claude')."""
    import asyncio

    from shared.client_launcher import AppLauncher, MCPServerManager
    
    # Check if this is a new-style backend + app profile
    profile_name = getattr(args, 'profile', None)
    if profile_name:
        from shared.profile_manager import get_profile_manager
        pm = get_profile_manager()
        profile = pm.get_talky_profile(profile_name)
        
        if profile and hasattr(profile, 'backend') and hasattr(profile, 'app'):
            # New-style profile: backend + app
            work_dir = getattr(args, 'dir', None)
            asyncio.run(_run_backend_client_profile(profile, work_dir))
        else:
            # Legacy profile - use original bot
            cmd_run(args)
    else:
        # No profile specified - use original bot
        cmd_run(args)


async def _run_backend_client_profile(profile, work_dir):
    """Run a new-style backend + app profile."""
    import asyncio

    from shared.client_launcher import AppLauncher, MCPServerManager
    
    print(f"🚀 Starting {profile.app} with {profile.backend} voice backend...")
    
    # Initialize managers
    mcp_manager = MCPServerManager()
    app_launcher = AppLauncher(work_dir)
    
    try:
        # Start MCP server if needed (runs as background service)
        mcp_config = {}
        if profile.voice_profile:
            mcp_config["voice_profile"] = profile.voice_profile
        
        mcp_available = await mcp_manager.ensure_running(mcp_config)
        if not mcp_available:
            print("❌ Failed to start MCP server")
            return
        
        # Launch app
        app_config = {}
        await app_launcher.launch_app(profile.app, app_config)
        
        # Trigger voice command
        await app_launcher.trigger_voice_command(profile.app)
        
        print(f"✅ {profile.app.capitalize()} is running. Use Ctrl+C to stop.")
        
        # Wait for Pi process to finish (when voice conversation ends)
        pi_process = app_launcher.processes.get(profile.app)
        if pi_process:
            await asyncio.get_event_loop().run_in_executor(None, pi_process.wait)
        
        print("👋 Pi session completed.")
            
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await app_launcher.stop_all()
        await mcp_manager.stop()  # Just logs that MCP server is left running
        print("✅ Done")


if __name__ == "__main__":
    main()
