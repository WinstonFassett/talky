#!/usr/bin/env python3
"""Talky CLI Tool

Install with: uv tool install talky -e .
Run from anywhere: talky moltis
"""

import argparse
import json
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


def _kill_pids_on_port(port: int) -> bool:
    """Kill the process LISTENING on the given TCP port.

    Uses ``-sTCP:LISTEN`` so we only hit the server, not any connected
    clients (e.g. the Claude Code MCP HTTP transport). Without this
    filter, ``lsof -ti :PORT`` returns both the server AND every client
    with an open connection, and ``kill -9`` on the client PID kills
    the agent harness that invoked ``talky kill`` — causing the Bash
    tool to hang indefinitely. Ticket a96c.
    """
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}", "-sTCP:LISTEN"],
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
    
    from shared.daemon_protocol import voice_daemon_is_running

    # server_dir is already defined globally from script location

    # Daemon management sub-actions
    if args.start_daemon or args.stop_daemon or args.daemon_status:
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
        cmd = [sys.executable, str(server_dir / "voice_daemon.py"), "--list-profiles"]
        result = subprocess.run(cmd)
        sys.exit(result.returncode)

    if not args.text:
        print("Usage: talky say <text>")
        sys.exit(1)

    if args.no_daemon:
        # Direct mode — no daemon, handle dependencies here
        import asyncio

        from shared.dependency_installer import ensure_dependencies
        
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
    if voice_daemon_is_running():
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

    from shared.daemon_protocol import voice_daemon_is_running

    if not args.text:
        print("Usage: talky ask <text>", file=sys.stderr)
        sys.exit(1)

    # Ensure daemon is running (auto-start if needed)
    need_wait = False
    if not voice_daemon_is_running():
        subprocess.Popen(
            [sys.executable, str(server_dir / "voice_daemon.py"), "--start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=server_dir,
        )
        need_wait = True

    # Build voice_client command
    cmd = [
        sys.executable,
        str(server_dir / "voice_client.py"),
        "--cmd", "ask",
    ]

    if need_wait:
        cmd.extend(["--wait", "30"])

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
    _clear_daemon_files()
    if not any_killed:
        print("port 9090: clear")

    # Verify nothing snuck back in.
    time.sleep(0.3)
    result = subprocess.run(["lsof", "-ti", ":9090", "-sTCP:LISTEN"], capture_output=True, text=True)
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
        except Exception:
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

    resume_id = getattr(args, "resume", None)
    cwd_arg = getattr(args, "cwd", None)
    bypass_permissions = getattr(args, "bypass_permissions", False)
    daemon_was_running = talky_daemon_is_running()

    # Check if pipeline is live — only post live if services are already built.
    _pipeline_live = False
    if daemon_was_running:
        try:
            _st_url = f"http://{os.environ.get('TALKY_MCP_HOST', 'localhost')}:{int(os.environ.get('TALKY_MCP_PORT', '9090'))}/status"
            import urllib.request as _ur
            with _ur.urlopen(_st_url, timeout=2) as _r:
                _st = json.loads(_r.read())
            _pipeline_live = _st.get("channel", {}).get("live", False)
        except Exception:
            pass

    if (resume_id or bypass_permissions) and not (daemon_was_running and _pipeline_live):
        # Resolve the backend name so the startup file targets only that backend.
        try:
            from shared.profile_manager import get_profile_manager as _gpm
            _pm = _gpm()
            _tp = _pm.get_talky_profile(name)
            _backend = (_tp.llm_backend if _tp and _tp.llm_backend else None) or name
        except Exception:
            _backend = name
        _args_payload: dict = {}
        if resume_id:
            _resume_entry: dict = {"backend": _backend, "session_id": resume_id}
            if cwd_arg:
                _resume_entry["cwd"] = str(Path(cwd_arg).expanduser().resolve())
            _args_payload["resume"] = _resume_entry
        if bypass_permissions:
            _args_payload["bypass_permissions"] = True
        _DAEMON_RUN_DIR.mkdir(parents=True, exist_ok=True)
        _DAEMON_ARGS_PATH.write_text(json.dumps(_args_payload))

    if not ensure_daemon():
        sys.exit(1)

    if resume_id and daemon_was_running and _pipeline_live:
        # Daemon was already running — post live; startup file not involved.
        resume_url = f"{base_url}/api/resume"
        _live_resume: dict = {"session_id": resume_id}
        if cwd_arg:
            _live_resume["cwd"] = str(Path(cwd_arg).expanduser().resolve())
        resume_body = json.dumps(_live_resume).encode("utf-8")
        resume_req = urllib.request.Request(
            resume_url, data=resume_body, method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(resume_req, timeout=3) as resp:
                json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8"))
                print(f"❌ resume failed: {err.get('error', e.reason)}")
            except Exception:
                print(f"❌ resume failed: HTTP {e.code}")
            sys.exit(1)
        except urllib.error.URLError as e:
            print(f"❌ could not reach talky daemon at {base_url}: {e}")
            sys.exit(1)

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


def cmd_voice(args):
    """Show or switch the active voice profile in the running talky server."""
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
        url = f"{base_url}/api/voices"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            print(f"❌ could not reach talky daemon at {base_url}: {e}")
            sys.exit(1)

        voices = data.get("voices") or []
        active = next((v["name"] for v in voices if v.get("active")), None)
        print(f"active voice: {active or '(none)'}")
        if voices:
            print("available voices:")
            for v in voices:
                marker = "*" if v.get("active") else " "
                print(f"  {marker} {v['name']}  — {v.get('description', '')}")
        else:
            print("no voices available")
        return

    # POST mode: switch voice
    url = f"{base_url}/api/voices/switch"
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

    print(f"✅ voice: {data.get('active', name)}")


def cmd_talkystatus(args):  # noqa: ARG001
    """Show daemon status — active profile, voice, health."""
    import urllib.error
    import urllib.request

    host = os.environ.get("TALKY_DAEMON_HOST", os.environ.get("TALKY_MCP_HOST", "localhost"))
    port = int(os.environ.get("TALKY_DAEMON_PORT", os.environ.get("TALKY_MCP_PORT", "9090")))
    base_url = f"http://{host}:{port}"

    # Fetch profiles + voices
    try:
        with urllib.request.urlopen(f"{base_url}/api/profiles", timeout=3) as resp:
            profiles_data = json.loads(resp.read().decode("utf-8"))
        with urllib.request.urlopen(f"{base_url}/api/voices", timeout=3) as resp:
            voices_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"❌ daemon not reachable at {base_url}: {e}")
        sys.exit(1)

    live = profiles_data.get("live", False)
    profiles = profiles_data.get("profiles", [])
    voices = voices_data.get("voices", [])

    active_profile = next((p["name"] for p in profiles if p.get("active")), None)
    active_voice = next((v["name"] for v in voices if v.get("active")), None)

    print(f"pipeline: {'live' if live else 'not live'}")
    print(f"active profile: {active_profile or '(none)'}")
    print(f"active voice: {active_voice or '(none)'}")
    print()
    print("profiles:")
    for p in profiles:
        marker = "*" if p.get("active") else " "
        health = "●" if p.get("healthy") else ("○" if p.get("healthy") is False else "?")
        print(f"  {marker} {health} {p['name']}  — {p.get('description', '')}")
    print()
    print(f"voices: {len(voices)} available")


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
        finally:
            _clear_daemon_files()
        return

    # User-facing mode: ensure the daemon is up, return immediately.
    if force and talky_daemon_is_running():
        print("🔪 stopping existing daemon...", file=sys.stderr)
        try:
            subprocess.run(["talky", "kill"], check=False)
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            print(f"⚠️  talky kill failed: {e}", file=sys.stderr)

    if talky_daemon_is_running():
        print("✅ talky daemon already running on :9090")
        return

    if not ensure_daemon(verbose=True):
        sys.exit(1)

    if voice_profile:
        print(f"(note: --voice-profile {voice_profile} is not yet propagated through the ensure path)")


_DAEMON_RUN_DIR = Path.home() / ".talky" / "run"
_DAEMON_READY_PATH = _DAEMON_RUN_DIR / "talky-daemon.ready"
_DAEMON_PID_PATH = _DAEMON_RUN_DIR / "talky-daemon.pid"
_DAEMON_LOCK_PATH = _DAEMON_RUN_DIR / "talky-daemon.lock"
_DAEMON_ARGS_PATH = _DAEMON_RUN_DIR / "talky-args.json"


def talky_daemon_is_running() -> bool:
    """Return True if the daemon is running. Checks the ready file (written by
    the daemon after uvicorn binds), verifying the PID is still alive."""
    try:
        pid = int(_DAEMON_READY_PATH.read_text().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def _clear_daemon_files() -> None:
    for p in (_DAEMON_READY_PATH, _DAEMON_PID_PATH):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def ensure_daemon(wait_secs: float = 30.0, verbose: bool = True) -> bool:
    """Ensure the talky daemon is running on :9090. Spawn it (detached) if not.

    Uses a lock file to serialize spawning. Waits for the daemon's ready
    file (written after uvicorn binds) rather than sniffing ports.
    Returns True on success, False on timeout.
    """
    import fcntl

    if talky_daemon_is_running():
        return True

    _DAEMON_RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _DAEMON_RUN_DIR / "talky-daemon.log"

    # Acquire an exclusive lock so only one CLI spawns the daemon.
    lock_fh = open(_DAEMON_LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        got_lock = True
    except OSError:
        got_lock = False

    if got_lock:
        # Double-check after lock — another CLI may have finished first.
        if talky_daemon_is_running():
            lock_fh.close()
            return True

        if verbose:
            print("⚙️  starting talky daemon...", file=sys.stderr, end="", flush=True)

        try:
            log_fh = open(log_path, "a")
            proc = subprocess.Popen(
                ["talky", "daemon", "--foreground"],
                stdout=log_fh,
                stderr=log_fh,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            _DAEMON_PID_PATH.write_text(str(proc.pid))
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            print(f"\n❌ could not spawn `talky daemon`: {e}", file=sys.stderr)
            lock_fh.close()
            return False
    else:
        if verbose:
            print("⏳ talky daemon is starting...", file=sys.stderr, end="", flush=True)

    # Poll for the ready file, showing progress dots.
    deadline = time.monotonic() + wait_secs
    dot_interval = 2.0
    next_dot = time.monotonic() + dot_interval
    try:
        while time.monotonic() < deadline:
            if talky_daemon_is_running():
                if verbose:
                    print(f"\n✅ talky daemon up on :9090", file=sys.stderr)
                return True
            # Check if the spawned process died before becoming ready.
            try:
                pid = int(_DAEMON_PID_PATH.read_text().strip())
                os.kill(pid, 0)
            except (FileNotFoundError, ValueError, ProcessLookupError):
                _clear_daemon_files()
                print(f"\n❌ talky daemon process died during startup. Check {log_path}", file=sys.stderr)
                return False
            except PermissionError:
                pass  # alive but owned by another user — keep waiting
            now = time.monotonic()
            if verbose and now >= next_dot:
                print(".", file=sys.stderr, end="", flush=True)
                next_dot = now + dot_interval
            time.sleep(0.3)

        print(
            f"\n❌ talky daemon failed to come up within {wait_secs:.0f}s. "
            f"Check {log_path} for details.",
            file=sys.stderr,
        )
        return False
    finally:
        lock_fh.close()


def main():
    """Main CLI entry point."""
    # Shortcut: treat first non-option, non-command arg as a profile name.
    # `talky openclaw` → `talky profile openclaw`. `cmd_profile` ensures
    # the daemon is up.
    known_commands = {"config", "say", "ask", "daemon", "ls", "auth", "transcribe", "kill", "profile", "voice", "status", "launch"}
    if len(sys.argv) > 1 and sys.argv[1] not in known_commands and not sys.argv[1].startswith("-"):
        candidate = sys.argv[1]
        # If the profile carries a ``launcher:`` block, route through the
        # generic launcher path. Otherwise treat it as a daemon-side
        # profile switch (talky <profile>).
        try:
            from shared.profile_manager import get_profile_manager as _gpm
            _pm = _gpm()
            _tp = _pm.get_talky_profile(candidate)
        except Exception:
            _tp = None
        if _tp is not None and _tp.launcher:
            sys.argv.pop(1)
            sys.argv.insert(1, "launch")
            sys.argv.insert(2, candidate)
        else:
            sys.argv.pop(1)
            sys.argv.insert(1, "profile")
            sys.argv.insert(2, candidate)

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
    profile_parser.add_argument("--resume", "-r", metavar="SESSION_ID", help="Resume a previous agent session by ID")
    profile_parser.add_argument("--cwd", "-d", metavar="DIR", help="Working directory for the agent session")
    profile_parser.add_argument("--bypass-permissions", action="store_true", help="Skip all Claude permission checks (dangerous)")
    profile_parser.set_defaults(func=cmd_profile)

    # === voice subcommand ===
    voice_parser = subparsers.add_parser(
        "voice",
        help="Show or switch the active voice profile in the running daemon",
    )
    voice_parser.add_argument(
        "name",
        nargs="?",
        help="Voice profile name to switch to. Omit to list.",
    )
    voice_parser.set_defaults(func=cmd_voice)

    # === status subcommand ===
    status_parser = subparsers.add_parser(
        "status",
        help="Show daemon status — profile, voice, health",
    )
    status_parser.set_defaults(func=cmd_talkystatus)

    # === ls subcommand ===
    ls_parser = subparsers.add_parser("ls", help="List profiles")
    ls_parser.set_defaults(func=lambda args: cmd_list_profiles(args))

    # === launch subcommand (generic agent launcher, ticket 5d95) ===
    launch_parser = subparsers.add_parser(
        "launch",
        help="Launch the agent associated with a talky profile (uses launcher: block)",
    )
    launch_parser.add_argument("profile", help="Talky profile name (must define launcher: in YAML)")
    launch_parser.add_argument("--cwd", "-d", help="Working directory for the agent (default: current)")
    launch_parser.add_argument("--resume", "-r", metavar="SESSION_ID", help="Resume a previous agent session by ID")
    launch_parser.set_defaults(func=cmd_launch)

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


def _render_launcher_token(token: str, *, extension: str, cwd: str) -> str:
    """Expand ``{project_root}``, ``{cwd}``, and ``{extension}`` in a token."""
    return token.format(
        project_root=str(_root),
        cwd=cwd,
        extension=extension,
    )


def _ensure_claude_skill_installed() -> None:
    """Copy the talky skill into ~/.claude/skills/talky/ if not already there."""
    import shutil
    skill_dest = Path.home() / ".claude" / "skills" / "talky" / "SKILL.md"
    if skill_dest.exists():
        return
    skill_source = _root / "skills" / "talky" / "SKILL.md"
    if not skill_source.exists():
        print(f"⚠️  Talky skill not found at {skill_source} — skipping install", file=sys.stderr)
        return
    skill_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(skill_source, skill_dest)
    print(f"✅ Talky skill installed: {skill_dest}")


def _ensure_claude_mcp_connected() -> None:
    """Add the talky MCP server to Claude's config if not already present."""
    import subprocess as _sp
    try:
        result = _sp.run(["claude", "mcp", "list"], capture_output=True, text=True, timeout=10)
        if "talky" in result.stdout or "pipecat-mcp-server" in result.stdout:
            return
        _sp.run(
            ["claude", "mcp", "add", "--transport", "http", "talky", "http://localhost:9090/mcp"],
            capture_output=True, timeout=30,
        )
    except Exception as e:
        print(f"⚠️  Could not auto-configure Claude MCP: {e}", file=sys.stderr)


def cmd_launch(args):
    """Generic agent launcher (ticket 5d95).

    Resolves a talky profile, ensures the daemon is up, opens the client
    in a browser (when the profile asks for it), then exec's into the
    configured agent command. Replaces the old ``cmd_pi`` / ``cmd_claude``
    / ``cmd_run_client_profile`` / ``AppLauncher`` zoo.

    Required profile config (in ``talky-profiles.yaml``):

      pi:
        llm_backend: agent-ext
        launcher:
          command: ["pi"]
          extension_arg: "-e"
          extension: "{project_root}/extensions/pi-voice/extension.ts"
          autoconnect_browser: true
          mode: foreground   # foreground = exec; background = TBD
    """
    import shutil
    import webbrowser

    from shared.profile_manager import get_profile_manager

    profile_name = getattr(args, "profile", None)
    if not profile_name:
        print("❌ No profile specified", file=sys.stderr)
        sys.exit(1)

    pm = get_profile_manager()
    profile = pm.get_talky_profile(profile_name)
    if profile is None:
        print(f"❌ Unknown talky profile: {profile_name!r}", file=sys.stderr)
        sys.exit(1)
    launcher = profile.launcher or {}
    if not launcher:
        print(
            f"❌ Profile {profile_name!r} has no ``launcher:`` block in talky-profiles.yaml — "
            f"nothing to launch.",
            file=sys.stderr,
        )
        sys.exit(1)

    mode = launcher.get("mode", "foreground")
    if mode != "foreground":
        print(f"❌ launcher mode {mode!r} not yet implemented (only 'foreground' for now).", file=sys.stderr)
        sys.exit(1)

    cwd = getattr(args, "cwd", None) or os.getcwd()

    command = list(launcher.get("command") or [])
    if not command:
        print(f"❌ Profile {profile_name!r}.launcher.command is empty.", file=sys.stderr)
        sys.exit(1)

    extension_template = launcher.get("extension")
    # extension_arg: if set (e.g. "-e"), the launcher appends [arg, extension]
    # at the end of the rendered command — Pi style. If unset, the agent is
    # expected to receive the extension path via a {extension} substitution
    # somewhere in its own command list — Node style: ["node", "{extension}"].
    extension_arg = launcher.get("extension_arg")

    extension_path = ""
    if extension_template:
        extension_path = _render_launcher_token(extension_template, extension="", cwd=cwd)
        if not Path(extension_path).exists():
            print(f"❌ Extension not found: {extension_path}", file=sys.stderr)
            sys.exit(1)

    rendered = [_render_launcher_token(tok, extension=extension_path, cwd=cwd) for tok in command]
    if extension_path and extension_arg:
        rendered.extend([extension_arg, extension_path])

    binary = rendered[0]
    if not shutil.which(binary):
        print(f"❌ `{binary}` not found in PATH", file=sys.stderr)
        sys.exit(1)

    if not ensure_daemon():
        sys.exit(1)

    if launcher.get("autoconnect_browser", False):
        host = os.environ.get("TALKY_DAEMON_HOST", os.environ.get("TALKY_MCP_HOST", "localhost"))
        port = int(os.environ.get("TALKY_DAEMON_PORT", os.environ.get("TALKY_MCP_PORT", "9090")))
        client_url = f"http://{host}:{port}?autoconnect=true"
        webbrowser.open(client_url)

    prompt = launcher.get("prompt")
    if prompt and binary == "claude":
        _ensure_claude_skill_installed()
        _ensure_claude_mcp_connected()
        rendered.append(prompt)

    resume_id = getattr(args, "resume", None)
    if resume_id:
        resume_arg = launcher.get("resume_arg", "--resume")
        rendered.extend([resume_arg, resume_id])

    os.environ["TALKY_PROFILE"] = profile_name
    os.chdir(cwd)
    os.execvp(binary, rendered)


if __name__ == "__main__":
    main()
