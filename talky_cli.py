#!/usr/bin/env python3
"""Talky CLI Tool

Install with: uv tool install talky -e .
Run from anywhere: talky moltis
"""

import argparse
import json
import logging
import os
import signal
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


def _kill_pids_on_port(port: int) -> bool:
    """Kill anything bound to the given TCP port. Returns True if anything was killed."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            killed = False
            for pid in pids:
                try:
                    subprocess.run(["kill", "-9", pid], capture_output=True)
                    print(f"port {port}: killed {pid}")
                    killed = True
                except subprocess.SubprocessError:
                    pass
            return killed
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
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


def cmd_kill(args):
    """Handle the 'kill' subcommand — stop the talky daemon on :9090.

    The voice daemon (unix socket) is intentionally left alone — its
    lifecycle is separate. Use `talky say --stop-daemon` to bounce that one.
    """
    any_killed = _kill_pids_on_port(9090)
    if not any_killed:
        print("port 9090: clear")

    # Verify nothing snuck back in.
    time.sleep(0.3)
    result = subprocess.run(["lsof", "-ti", ":9090"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        print("port 9090: STILL HELD after kill -9", file=sys.stderr)
        return 1

    if not any_killed:
        print("nothing to kill — talky daemon was not running")
    return 0


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


def cmd_profile(args):
    """Show or switch the active LLM profile in the running talky server."""
    import urllib.error
    import urllib.request

    if not ensure_daemon():
        sys.exit(1)

    host = os.environ.get("TALKY_DAEMON_HOST", os.environ.get("TALKY_MCP_HOST", "localhost"))
    port = int(os.environ.get("TALKY_DAEMON_PORT", os.environ.get("TALKY_MCP_PORT", "9090")))
    base_url = f"http://{host}:{port}"

    name = getattr(args, "name", None)

    if name is None:
        # GET mode: list available + show active
        url = f"{base_url}/api/profile"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            print(f"❌ could not reach talky daemon at {base_url}: {e}")
            print("   is `talky daemon` running? run it in another terminal if not.")
            sys.exit(1)

        active = data.get("active") or "(none — no live pipeline)"
        available = data.get("available") or []
        print(f"active profile: {active}")
        if available:
            print("available profiles:")
            for p in available:
                marker = "*" if p == data.get("active") else " "
                print(f"  {marker} {p}")
        else:
            print("no profiles available — connect a browser to localhost:9090 first")
        return

    # POST mode: switch to the named profile
    url = f"{base_url}/api/profile"
    body = json.dumps({"profile": name}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            print(f"❌ {err.get('error', e.reason)}")
        except Exception:
            print(f"❌ HTTP {e.code}: {e.reason}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"❌ could not reach talky daemon at {base_url}: {e}")
        sys.exit(1)

    print(f"✅ profile: {data.get('active', name)}")

    # If the pipeline isn't live yet, open the browser so the user can
    # get into the convo. The daemon's already stored the desired profile
    # — the next pipeline build will auto-apply it.
    status_url = f"{base_url}/status"
    try:
        with urllib.request.urlopen(status_url, timeout=2) as resp:
            st = json.loads(resp.read().decode("utf-8"))
        live = st.get("channel", {}).get("live", False)
    except Exception:  # noqa: BLE001
        live = True  # assume live if we can't tell

    if not live:
        import webbrowser
        client_url = f"{base_url}?autoconnect=true"
        print(f"   no live pipeline — opening {client_url}")
        webbrowser.open(client_url)


def cmd_daemon(args):
    """Ensure the talky daemon is running, or run it in foreground with --foreground.

    Default (user-facing): same shape as `talky openclaw` — ensures the
    daemon is up on :9090 and returns immediately. Safe to run repeatedly.

    --force: kill any existing daemon first, then start a fresh one.

    --foreground (hidden): actually run the daemon in-process, blocking.
    This is what the detached child spawned from `ensure_daemon` uses.
    Users should not pass this directly.
    """
    foreground = bool(getattr(args, "foreground", False))
    force = bool(getattr(args, "force", False))
    voice_profile = getattr(args, "voice_profile", None)

    # Foreground mode: actually run the daemon. Only reached via the
    # detached child Popen'd from ensure_daemon.
    if foreground:
        if force:
            os.environ["TALKY_DAEMON_FORCE"] = "1"
        try:
            daemon_src_path = _root / "mcp-server" / "src"
            sys.path.insert(0, str(daemon_src_path))
            from pipecat_mcp_server.server import main as daemon_main
            daemon_main()
        except Exception as e:
            print(f"❌ talky daemon failed to start: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # User-facing mode: ensure the daemon is up, return immediately.
    if force and _daemon_is_running():
        print("🔪 stopping existing daemon...", file=sys.stderr)
        try:
            subprocess.run(["talky", "kill"], check=False)
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            print(f"⚠️  talky kill failed: {e}", file=sys.stderr)

    if _daemon_is_running():
        print("✅ talky daemon already running on :9090")
        return

    if not ensure_daemon(verbose=True):
        sys.exit(1)

    if voice_profile:
        print(f"(note: --voice-profile {voice_profile} is not yet propagated through the ensure path)")


def _daemon_is_running() -> bool:
    """Return True if the talky daemon is listening on 9090."""
    try:
        result = subprocess.run(
            ["lsof", "-ti:9090"], capture_output=True, text=True, timeout=1.0
        )
        return bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def ensure_daemon(wait_secs: float = 12.0, verbose: bool = True) -> bool:
    """Ensure the talky daemon is running on :9090. Spawn it (detached) if not.

    Mirrors the voice daemon auto-start pattern: any talky subcommand
    that needs the daemon can call this first, and the daemon will be
    lazy-started on first use. Subsequent calls see it already running
    and return immediately.

    Returns True on success. Prints an error and returns False if the
    spawn fails or the daemon doesn't come up within wait_secs.
    """
    if _daemon_is_running():
        return True

    if verbose:
        print("⚙️  starting talky daemon...", file=sys.stderr)

    log_dir = Path.home() / ".talky" / "run"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "talky-daemon.log"

    try:
        log_fh = open(log_path, "a")
        subprocess.Popen(
            ["talky", "daemon", "--foreground"],
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach so the daemon outlives this CLI
            close_fds=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        print(f"❌ could not spawn `talky daemon`: {e}", file=sys.stderr)
        return False

    # Poll for the daemon to come up.
    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        if _daemon_is_running():
            if verbose:
                print(f"✅ talky daemon up on :9090 (log: {log_path})", file=sys.stderr)
            return True
        time.sleep(0.2)

    print(
        f"❌ talky daemon failed to come up within {wait_secs:.0f}s. "
        f"Check {log_path} for details.",
        file=sys.stderr,
    )
    return False


def main():
    """Main CLI entry point."""
    # Shortcut: treat first non-option, non-command arg as a profile name.
    # `talky openclaw` → `talky profile openclaw`. `cmd_profile` ensures
    # the daemon is up.
    known_commands = {"config", "say", "ask", "daemon", "ls", "auth", "pi", "claude", "transcribe", "kill", "profile"}
    if len(sys.argv) > 1 and sys.argv[1] not in known_commands and not sys.argv[1].startswith("-"):
        profile_name = sys.argv.pop(1)
        sys.argv.insert(1, "profile")
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

    # === kill subcommand ===
    kill_parser = subparsers.add_parser(
        "kill",
        help="Stop the talky daemon on :9090 (voice daemon untouched)",
    )
    kill_parser.set_defaults(func=cmd_kill)

    # === daemon subcommand ===
    # Ensures the talky daemon is running on :9090. The daemon hosts
    # the voice pipeline, the WebRTC transport, the client static files,
    # an HTTP control plane, and (among other things) a FastMCP SSE
    # mount. MCP is a *feature* of the daemon, not the daemon itself.
    daemon_parser = subparsers.add_parser("daemon", help="Ensure the talky daemon is running on :9090")
    daemon_parser.add_argument("--voice-profile", "-v", help="Voice profile to use")
    daemon_parser.add_argument("--host", help="Override host binding (default: from config)")
    daemon_parser.add_argument(
        "--force",
        action="store_true",
        help="Kill any existing daemon first, then start a fresh one",
    )
    # Hidden: actually run the daemon in foreground, blocking. This is
    # what the detached child spawned from ensure_daemon uses.
    daemon_parser.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    daemon_parser.set_defaults(func=cmd_daemon)

    # === profile subcommand ===
    profile_parser = subparsers.add_parser(
        "profile",
        help="Show or switch the active LLM profile in the running daemon",
    )
    profile_parser.add_argument(
        "name",
        nargs="?",
        help="Profile name to switch to (e.g. openclaw, moltis, __mcp__). Omit to list.",
    )
    profile_parser.set_defaults(func=cmd_profile)

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

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
        return

    # No subcommand: route bare `talky` to the default talky profile via
    # the daemon profile-switch path. Same shape as `talky <profile>`.
    try:
        from shared.profile_manager import get_profile_manager
        pm = get_profile_manager()
        default_profile = pm.defaults.get("talky_profile")
        if not (default_profile and default_profile in pm.list_talky_profiles()):
            profiles = pm.list_talky_profiles()
            if not profiles:
                print("❌ No talky profiles configured.", file=sys.stderr)
                sys.exit(1)
            default_profile = next(iter(profiles))
    except Exception as e:
        print(f"❌ Error loading profiles: {e}", file=sys.stderr)
        sys.exit(1)

    args.name = default_profile
    cmd_profile(args)


def cmd_run_client_profile(args):
    """Run an app profile (e.g., 'talky pi', 'talky claude')."""
    import asyncio

    from shared.profile_manager import get_profile_manager

    profile_name = getattr(args, 'profile', None)
    if not profile_name:
        print("❌ No profile specified", file=sys.stderr)
        sys.exit(1)

    pm = get_profile_manager()
    profile = pm.get_talky_profile(profile_name)
    if not (profile and hasattr(profile, 'backend') and hasattr(profile, 'app')):
        print(
            f"❌ Profile {profile_name!r} is not a backend+app profile. "
            f"Expected fields: backend, app.",
            file=sys.stderr,
        )
        sys.exit(1)

    work_dir = getattr(args, 'dir', None)
    asyncio.run(_run_backend_client_profile(profile, work_dir))


async def _run_backend_client_profile(profile, work_dir):
    """Run a new-style backend + app profile."""
    import asyncio

    from shared.client_launcher import AppLauncher, DaemonManager

    print(f"🚀 Starting {profile.app} with {profile.backend} voice backend...")

    daemon_manager = DaemonManager()
    app_launcher = AppLauncher(work_dir)

    try:
        daemon_config = {}
        if profile.voice_profile:
            daemon_config["voice_profile"] = profile.voice_profile

        daemon_available = await daemon_manager.ensure_running(daemon_config)
        if not daemon_available:
            print("❌ Failed to start talky daemon")
            return

        app_config = {}
        await app_launcher.launch_app(profile.app, app_config)
        await app_launcher.trigger_voice_command(profile.app)

        print(f"✅ {profile.app.capitalize()} is running. Use Ctrl+C to stop.")

        app_process = app_launcher.processes.get(profile.app)
        if app_process:
            await asyncio.get_event_loop().run_in_executor(None, app_process.wait)

        print(f"👋 {profile.app.capitalize()} session completed.")

    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await app_launcher.stop_all()
        await daemon_manager.stop()
        print("✅ Done")


if __name__ == "__main__":
    main()
