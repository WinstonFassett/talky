#!/usr/bin/env python3
"""Talky CLI Tool

Install with: uv tool install talky -e .
Run from anywhere: talky moltis
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# Package directory (src/talky/) — bundled config, prompts, etc.
_PKG_DIR = Path(__file__).resolve().parent
# Repo root — only valid in editable installs; used for extensions/, skills/.
_root = _PKG_DIR.parents[1]

_DAEMON_BASE_URL_CACHE: str | None = None
_DAEMON_SSL_CTX: ssl.SSLContext | None = None


_DAEMON_PORT_FILE = Path.home() / ".talky" / "run" / "talky-daemon.port"
_DAEMON_LOOPBACK_PORT_FILE = Path.home() / ".talky" / "run" / "talky-daemon.loopback-port"


def _read_runfile_port(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _settings_net() -> dict:
    try:
        from talky.shared.profile_manager import get_profile_manager
        settings = get_profile_manager().settings or {}
        return (settings.get("network") or {}) if isinstance(settings, dict) else {}
    except Exception:
        return {}


def _daemon_host_port() -> tuple[str, int]:
    """Resolve the daemon's primary host:port.

    Precedence: env → settings.yaml → runfile (random port written at
    daemon startup) → ephemeral fallback. The runfile is the load-bearing
    discovery channel for the random-port-by-default flow.
    """
    host = os.environ.get("TALKY_DAEMON_HOST", os.environ.get("TALKY_HOST", "localhost"))
    raw_port = os.environ.get("TALKY_DAEMON_PORT") or os.environ.get("TALKY_PORT")
    if raw_port is None:
        p = _settings_net().get("port")
        if p is not None:
            raw_port = str(p)
    if raw_port is None:
        rf = _read_runfile_port(_DAEMON_PORT_FILE)
        if rf is not None:
            return host, rf
        # Daemon isn't running yet and no fixed port configured. Return a
        # sentinel — callers should generally first check `talky_daemon_is_running()`.
        return host, 0
    return host, int(raw_port)


def _daemon_tls_configured() -> bool:
    """Mirror the daemon's TLS-detection logic so the client probes the right scheme.

    Daemon serves HTTPS iff both cert and key resolve (env or settings.network.https.{cert,key}).
    """
    cert = os.environ.get("TALKY_HTTPS_CERT")
    key = os.environ.get("TALKY_HTTPS_KEY")
    if not (cert and key):
        https = (_settings_net().get("https") or {})
        cert = cert or https.get("cert")
        key = key or https.get("key")
    if not (cert and key):
        return False
    cert = os.path.expanduser(cert)
    key = os.path.expanduser(key)
    return os.path.exists(cert) and os.path.exists(key)


def _daemon_loopback_port() -> Optional[int]:
    """Plain-HTTP loopback port (only bound when TLS is on)."""
    raw = os.environ.get("TALKY_LOOPBACK_PORT")
    if raw is None:
        p = _settings_net().get("loopback_port")
        if p is not None:
            raw = str(p)
    if raw is None:
        return _read_runfile_port(_DAEMON_LOOPBACK_PORT_FILE)
    return int(raw)


def _unverified_ssl_ctx() -> ssl.SSLContext:
    global _DAEMON_SSL_CTX
    if _DAEMON_SSL_CTX is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _DAEMON_SSL_CTX = ctx
    return _DAEMON_SSL_CTX


def daemon_base_url() -> str:
    """Probe the daemon scheme + port once per process.

    Primary-port scheme follows TLS config (HTTPS iff cert+key resolve, else HTTP).
    Falls back to the plain-HTTP loopback port when TLS is on but primary HTTPS misses.
    """
    global _DAEMON_BASE_URL_CACHE
    if _DAEMON_BASE_URL_CACHE is not None:
        return _DAEMON_BASE_URL_CACHE
    host, primary_port = _daemon_host_port()
    loopback_port = _daemon_loopback_port()
    tls = _daemon_tls_configured()
    candidates: list[tuple[str, str, int]] = []
    if primary_port:
        candidates.append(("https" if tls else "http", host, primary_port))
    if tls and loopback_port:
        candidates.append(("http", "localhost", loopback_port))
    for scheme, h, p in candidates:
        url = f"{scheme}://{h}:{p}/api/profiles"
        try:
            ctx = _unverified_ssl_ctx() if scheme == "https" else None
            with urllib.request.urlopen(url, timeout=2, context=ctx):
                _DAEMON_BASE_URL_CACHE = f"{scheme}://{h}:{p}"
                return _DAEMON_BASE_URL_CACHE
        except (urllib.error.URLError, ssl.SSLError, ConnectionError, OSError):
            continue
    fallback_scheme = "https" if tls else "http"
    fallback_port = primary_port or loopback_port or 0
    _DAEMON_BASE_URL_CACHE = f"{fallback_scheme}://{host}:{fallback_port}"
    return _DAEMON_BASE_URL_CACHE


def daemon_urlopen(url_or_req, timeout: float = 3):
    """urlopen wrapper that supplies the unverified SSL context when needed."""
    target = url_or_req if isinstance(url_or_req, str) else url_or_req.full_url
    ctx = _unverified_ssl_ctx() if target.startswith("https://") else None
    return urllib.request.urlopen(url_or_req, timeout=timeout, context=ctx)



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


def _ensure_local_audio_extra() -> bool:
    """Ensure the `local_audio` extra (pyaudio) is installed.

    Local audio (talky say/ask) requires pyaudio. It's an opt-in extra so
    users who only use the browser/MCP path don't pay the portaudio system
    dep. If missing, install it via the same on-demand path the LLM
    backends use, then tell the user to retry — the running CLI process
    can't see the new package without restart.

    Returns True if pyaudio is already importable. Returns False (and
    triggers install + exits 0) if it was missing.
    """
    try:
        import pyaudio  # noqa: F401
        return True
    except ImportError:
        pass

    from talky.shared.dependency_installer import (
        _check_native_deps_for_extra,
        install_extra_no_reexec,
    )

    # Check the native dep (portaudio) before announcing the Python
    # install — gives a useful platform-specific hint instead of letting
    # pip dump a C compiler error. Ticket 4fbd.
    native_ok, native_reason = _check_native_deps_for_extra("local_audio")
    if not native_ok:
        print(f"❌ {native_reason}", file=sys.stderr)
        sys.exit(1)

    print("⚙️  local audio requires pyaudio — installing the `local_audio` extra...", file=sys.stderr)
    ok = install_extra_no_reexec("local_audio")
    if ok:
        print("✅ installed. retry your command.", file=sys.stderr)
        sys.exit(0)
    else:
        print(
            "❌ install failed. See log for details. "
            "If the failure was a C compile error, the portaudio system "
            "library may be missing or in an unusual location.",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_say(args):
    """Handle the 'say' subcommand."""
    # Set log level environment variable if specified
    if getattr(args, "log_level", None):
        os.environ["TALKY_LOG_LEVEL"] = args.log_level

    _ensure_local_audio_extra()

    from talky.shared.daemon_protocol import voice_daemon_is_running

    # Daemon management sub-actions
    if args.start_daemon or args.stop_daemon or args.daemon_status:
        cmd = [sys.executable, "-m", "talky.local_audio.daemon"]
        if args.start_daemon:
            cmd.append("--start")
        elif args.stop_daemon:
            cmd.append("--stop")
        elif args.daemon_status:
            cmd.append("--status")
        result = subprocess.run(cmd)
        sys.exit(result.returncode)

    if args.list_profiles:
        cmd = [sys.executable, "-m", "talky.local_audio.daemon", "--list-profiles"]
        result = subprocess.run(cmd)
        sys.exit(result.returncode)

    if not args.text:
        print("Usage: talky say <text>")
        sys.exit(1)

    if args.no_daemon:
        # Direct mode — no daemon, handle dependencies here
        import asyncio

        from talky.shared.dependency_installer import ensure_dependencies
        
        if not ensure_dependencies(for_cli=True):
            print("❌ Failed to install required dependencies")
            sys.exit(1)

        from talky.local_audio.say import say_text

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
        cmd = [sys.executable, "-m", "talky.local_audio.tts", args.text]
    else:
        # Auto-start daemon, then block here until it's actually accepting
        # connections. The Popen call returns immediately (daemon detaches);
        # TTS init takes 3-5s. Without the wait the tts client races and
        # gets "Daemon not running" before the socket exists.
        from talky.shared.daemon_protocol import wait_for_voice_daemon
        _spawn_voice_daemon()
        if not wait_for_voice_daemon(timeout=30.0):
            print(
                f"❌ voice daemon failed to start within 30s — see {_VOICE_DAEMON_LOG_PATH}",
                file=sys.stderr,
            )
            sys.exit(1)
        cmd = [sys.executable, "-m", "talky.local_audio.tts", args.text]

    if args.voice_profile:
        cmd.extend(["-p", args.voice_profile])
    if args.provider:
        cmd.extend(["--provider", args.provider])
    if args.voice:
        cmd.extend(["--voice", args.voice])
    if args.output:
        cmd.extend(["-o", args.output])

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_auth(args):
    """Manage provider credentials."""
    from talky.auth import run_auth_tui
    run_auth_tui()


def cmd_ask(args):
    """Handle the 'ask' subcommand — speak text then listen for response."""
    if getattr(args, "log_level", None):
        os.environ["TALKY_LOG_LEVEL"] = args.log_level

    _ensure_local_audio_extra()

    from talky.shared.daemon_protocol import voice_daemon_is_running

    if not args.text:
        print("Usage: talky ask <text>", file=sys.stderr)
        sys.exit(1)

    # Ensure daemon is running (auto-start if needed). Block until the
    # socket is up — Popen detaches before TTS init completes (3-5s), so
    # without an explicit wait the client races and fails.
    if not voice_daemon_is_running():
        from talky.shared.daemon_protocol import wait_for_voice_daemon
        _spawn_voice_daemon()
        if not wait_for_voice_daemon(timeout=30.0):
            print(
                f"❌ voice daemon failed to start within 30s — see {_VOICE_DAEMON_LOG_PATH}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Build voice_client command
    cmd = [
        sys.executable,
        "-m", "talky.local_audio.client",
        "--cmd", "ask",
        args.text,
    ]

    if args.voice_profile:
        cmd.extend(["-p", args.voice_profile])
    if getattr(args, "provider", None):
        cmd.extend(["--provider", args.provider])
    if getattr(args, "voice", None):
        cmd.extend(["--voice", args.voice])
    if getattr(args, "silence_timeout", None):
        cmd.extend(["--silence-timeout", str(args.silence_timeout)])

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_kill(args):
    """Handle the 'kill' subcommand — stop the talky daemon on both listeners.

    Kills the process listening on the primary (HTTPS) port and the secondary
    (HTTP loopback) port. The voice daemon (unix socket) is intentionally left
    alone — its lifecycle is separate. Use `talky say --stop-daemon` to bounce
    that one.
    """
    _, primary_port = _daemon_host_port()
    loopback_port = _daemon_loopback_port()
    ports = [p for p in {primary_port, loopback_port} if p]

    any_killed = False
    for port in ports:
        if _kill_pids_on_port(port):
            any_killed = True
        else:
            print(f"port {port}: clear")

    _clear_daemon_files()

    # Verify nothing snuck back in.
    time.sleep(0.3)
    held = []
    for port in ports:
        result = subprocess.run(["lsof", "-ti", f":{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            held.append(port)
    if held:
        for port in held:
            print(f"port {port}: STILL HELD after kill -9", file=sys.stderr)
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
    from talky.local_audio.logging_config import setup_logging
    log_level = getattr(args, "log_level", None)
    setup_logging(log_level)
    
    _ensure_local_audio_extra()

    import asyncio

    from talky.local_audio.transcribe import transcribe

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
    bundled_defaults = _PKG_DIR / "config" / "defaults"
    
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
            from talky.shared.profile_manager import get_profile_manager
            pm = get_profile_manager()
            for name, desc in pm.list_voice_profiles().items():
                print(f"  {name}: {desc}")
        except Exception:
            print(f"  (Run 'talky say hello' first to install dependencies)")


def cmd_list_profiles(args):
    """List all available profiles."""
    from talky.shared.profile_manager import get_profile_manager
    
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
    base_url = daemon_base_url()

    name = getattr(args, "name", None)

    if name is None:
        # GET mode: list available + show active
        url = f"{base_url}/api/profile"
        try:
            with daemon_urlopen(url, timeout=3) as resp:
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
            _, _port = _daemon_host_port()
            print(f"no profiles available — connect a browser to localhost:{_port} first")
        return

    resume_id = getattr(args, "resume", None)
    cwd_arg = getattr(args, "cwd", None)
    bypass_permissions = getattr(args, "bypass_permissions", False)
    daemon_was_running = talky_daemon_is_running()

    # Check if pipeline is live — only post live if services are already built.
    _pipeline_live = False
    if daemon_was_running:
        try:
            _st_url = f"{base_url}/status"
            with daemon_urlopen(_st_url, timeout=2) as _r:
                _st = json.loads(_r.read())
            _pipeline_live = _st.get("channel", {}).get("live", False)
        except Exception:
            pass

    if (resume_id or bypass_permissions) and not (daemon_was_running and _pipeline_live):
        # Resolve the backend name so the startup file targets only that backend.
        try:
            from talky.shared.profile_manager import get_profile_manager as _gpm
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
            with daemon_urlopen(resume_req, timeout=3) as resp:
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
        with daemon_urlopen(req, timeout=3) as resp:
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
        with daemon_urlopen(status_url, timeout=2) as resp:
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
    if not ensure_daemon():
        sys.exit(1)

    base_url = daemon_base_url()

    name = getattr(args, "name", None)

    if name is None:
        # GET mode: list available + show active
        url = f"{base_url}/api/voices"
        try:
            with daemon_urlopen(url, timeout=3) as resp:
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
        with daemon_urlopen(req, timeout=3) as resp:
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


def _read_runfile_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _gather_daemon_status() -> dict:
    """Single source of truth for daemon state — runfiles + /api/ready.

    Returns a dict with: ready, pid, port, loopback_port, profile, voice,
    tls, reachable, readiness_tasks. Missing fields are None.
    """
    run_dir = Path.home() / ".talky" / "run"
    pid = _read_runfile_int(run_dir / "talky-daemon.ready") or _read_runfile_int(run_dir / "talky-daemon.pid")
    port = _read_runfile_int(run_dir / "talky-daemon.port")
    loopback_port = _read_runfile_int(run_dir / "talky-daemon.loopback-port")

    # Liveness: pid alive + ready file exists.
    ready = False
    if pid is not None:
        try:
            os.kill(pid, 0)
            ready = (run_dir / "talky-daemon.ready").exists()
        except (ProcessLookupError, PermissionError):
            ready = False

    out: dict = {
        "ready": ready,
        "pid": pid,
        "port": port,
        "loopback_port": loopback_port,
        "tls": loopback_port is not None,  # secondary listener implies TLS on primary
        "profile": None,
        "voice": None,
        "reachable": False,
        "readiness_tasks": [],
    }

    if not ready:
        return out

    # Talk to the daemon for profile/voice/readiness.
    try:
        base_url = daemon_base_url()
        with daemon_urlopen(f"{base_url}/api/profiles", timeout=3) as resp:
            profiles_data = json.loads(resp.read().decode("utf-8"))
        with daemon_urlopen(f"{base_url}/api/voices", timeout=3) as resp:
            voices_data = json.loads(resp.read().decode("utf-8"))
        with daemon_urlopen(f"{base_url}/api/ready", timeout=3) as resp:
            ready_data = json.loads(resp.read().decode("utf-8"))
        out["reachable"] = True
        out["profile"] = next((p["name"] for p in profiles_data.get("profiles", []) if p.get("active")), None)
        out["voice"] = next((v["name"] for v in voices_data.get("voices", []) if v.get("active")), None)
        out["readiness_tasks"] = ready_data.get("tasks") or []
        out["_profiles"] = profiles_data.get("profiles", [])
        out["_voices_count"] = len(voices_data.get("voices", []))
        out["_live"] = profiles_data.get("live", False)
    except urllib.error.URLError:
        pass

    return out


def cmd_talkystatus(args):
    """Show daemon status — active profile, voice, health.

    --json: emit structured status (ready/pid/port/loopback_port/profile/voice/tls)
    suitable for shells and other tools.
    """
    status = _gather_daemon_status()

    if getattr(args, "json", False):
        # Strip private/internal fields used only by the human-readable view.
        public = {k: v for k, v in status.items() if not k.startswith("_")}
        print(json.dumps(public, indent=2))
        return

    if not status["ready"]:
        print(f"❌ daemon not running (no live pid at ~/.talky/run/talky-daemon.ready)")
        sys.exit(1)

    if not status["reachable"]:
        port = status["port"]
        print(f"⚠️  daemon pid {status['pid']} alive but HTTP not reachable on :{port}")
        sys.exit(1)

    profiles = status.get("_profiles", [])
    print(f"pipeline: {'live' if status.get('_live') else 'not live'}")
    print(f"pid: {status['pid']}  port: {status['port']}"
          + (f"  loopback: {status['loopback_port']}" if status['loopback_port'] else "")
          + (f"  tls: on" if status['tls'] else ""))
    print(f"active profile: {status['profile'] or '(none)'}")
    print(f"active voice: {status['voice'] or '(none)'}")
    print()
    print("profiles:")
    for p in profiles:
        marker = "*" if p.get("active") else " "
        health = "●" if p.get("healthy") else ("○" if p.get("healthy") is False else "?")
        print(f"  {marker} {health} {p['name']}  — {p.get('description', '')}")
    print()
    print(f"voices: {status.get('_voices_count', 0)} available")


def cmd_daemon(args):
    """Ensure the talky daemon is running, or run it in foreground with --foreground.

    Default (user-facing): same shape as `talky openclaw` — ensures the
    daemon is up and returns immediately. Safe to run repeatedly.

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
            from talky.server.__main__ import main as daemon_main
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
        _, _port = _daemon_host_port()
        print(f"✅ talky daemon already running on :{_port}")
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
_VOICE_DAEMON_LOG_PATH = _DAEMON_RUN_DIR / "voice-daemon.log"


def _spawn_voice_daemon() -> None:
    """Spawn the local-audio (voice) daemon with stdout+stderr captured.

    Without redirection, stderr is detached and any failure before loguru
    initializes (import errors, native library errors, segfaults) goes to
    /dev/null. Ticket f619 — always send stderr somewhere readable.
    """
    _DAEMON_RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(_VOICE_DAEMON_LOG_PATH, "a")
    subprocess.Popen(
        [sys.executable, "-m", "talky.local_audio.daemon", "--start"],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


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


def _probe_daemon_http() -> bool:
    """Return True if /api/ready answers — meaning uvicorn has bound but the
    daemon may still be running pre-warm. Used by ensure_daemon to detect
    listening-but-not-ready state and surface readiness progress.
    """
    try:
        url = daemon_base_url() + "/api/ready"
        ctx = _unverified_ssl_ctx() if url.startswith("https:") else None
        with urllib.request.urlopen(url, context=ctx, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def _fetch_readiness() -> dict | None:
    try:
        url = daemon_base_url() + "/api/ready"
        ctx = _unverified_ssl_ctx() if url.startswith("https:") else None
        with urllib.request.urlopen(url, context=ctx, timeout=1.0) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def ensure_daemon(wait_secs: float = 30.0, verbose: bool = True) -> bool:
    """Ensure the talky daemon is running. Spawn it (detached) if not.

    Uses a lock file to serialize spawning. Waits for the daemon's ready
    file (written after uvicorn binds + pre-warm completes). While
    pre-warm is in flight, polls /api/ready and surfaces task progress so
    the user sees what's happening (e.g. HF model downloads, dep installs)
    instead of silent dots. Default wait grows automatically when active
    readiness work is observed.
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
            print(
                f"\n❌ could not spawn `talky daemon`: {e} — see {log_path}",
                file=sys.stderr,
            )
            lock_fh.close()
            return False
    else:
        if verbose:
            print("⏳ talky daemon is starting...", file=sys.stderr, end="", flush=True)

    # Poll for the ready file. While the daemon is listening but not yet
    # ready, surface readiness tasks (e.g. HF model download progress,
    # extra installs) instead of silent dots. Wait extends automatically
    # while active readiness work is observed — first-run downloads can
    # legitimately take 10+ minutes.
    deadline = time.monotonic() + wait_secs
    dot_interval = 2.0
    next_dot = time.monotonic() + dot_interval
    last_status_line = ""
    saw_listening = False
    try:
        while time.monotonic() < deadline:
            if talky_daemon_is_running():
                if verbose:
                    _, _port = _daemon_host_port()
                    # Clear any in-progress status line.
                    if last_status_line:
                        print("\r" + " " * len(last_status_line) + "\r", file=sys.stderr, end="", flush=True)
                    print(f"\n✅ talky daemon up on :{_port}", file=sys.stderr)
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

            # Try to fetch readiness — only meaningful once uvicorn binds.
            r = _fetch_readiness()
            now = time.monotonic()
            if r is not None:
                if not saw_listening:
                    saw_listening = True
                    if verbose:
                        print("\n   listening, finishing setup...", file=sys.stderr, flush=True)
                tasks = r.get("tasks") or []
                if tasks:
                    # Active work — extend the deadline so first-run downloads
                    # don't hit a 30s timeout.
                    deadline = max(deadline, now + 120.0)
                    if verbose:
                        first = tasks[0]
                        name = first.get("name", "")
                        pct = first.get("pct")
                        line = f"   {name}"
                        if pct is not None:
                            line += f" ({pct:.0f}%)"
                        extra = len(tasks) - 1
                        if extra > 0:
                            line += f" (+{extra} more)"
                        # Carriage-return overwrite.
                        pad = max(0, len(last_status_line) - len(line))
                        print("\r" + line + (" " * pad), file=sys.stderr, end="", flush=True)
                        last_status_line = line
                else:
                    # Listening, no open tasks — usually a transient race
                    # between ready_file write and our next probe.
                    pass
            else:
                if verbose and now >= next_dot:
                    print(".", file=sys.stderr, end="", flush=True)
                    next_dot = now + dot_interval
            time.sleep(0.5)

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
            from talky.shared.profile_manager import get_profile_manager as _gpm
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
        help="Stop the talky daemon (voice daemon untouched)",
    )
    kill_parser.set_defaults(func=cmd_kill)

    # === daemon subcommand ===
    # Ensures the talky daemon is running. The daemon hosts
    # the voice pipeline, the WebRTC transport, the client static files,
    # an HTTP control plane, and (among other things) a FastMCP SSE
    # mount. MCP is a *feature* of the daemon, not the daemon itself.
    daemon_parser = subparsers.add_parser("daemon", help="Ensure the talky daemon is running")
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
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON (ready, pid, port, loopback_port, profile, voice, tls)",
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
        from talky.shared.profile_manager import get_profile_manager
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
    """Expand ``{project_root}``, ``{pkg_dir}``, ``{cwd}``, and ``{extension}`` in a token.

    ``{project_root}`` — repo root; only valid in editable installs.
    ``{pkg_dir}``      — the installed talky package dir; valid in both editable
                         and wheel installs. Use this for files bundled with
                         the package (e.g. extensions/pi-voice).
    """
    return token.format(
        project_root=str(_root),
        pkg_dir=str(_PKG_DIR),
        cwd=cwd,
        extension=extension,
    )


def _ensure_claude_skill_installed() -> None:
    """Copy the talky skill into ~/.claude/skills/talky/ if not already there."""
    import shutil
    skill_dest = Path.home() / ".claude" / "skills" / "talky" / "SKILL.md"
    if skill_dest.exists():
        return
    # Prefer package-bundled skill (wheel install or editable symlink);
    # fall back to repo path for legacy editable installs.
    skill_source = _PKG_DIR / "_skill" / "SKILL.md"
    if not skill_source.exists():
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
        # Pick the plain-HTTP loopback port if TLS is on, else the primary port.
        # We need a non-HTTPS URL so Claude's MCP transport doesn't choke on self-signed certs.
        loopback = _daemon_loopback_port()
        _, primary_port = _daemon_host_port()
        http_target_port = loopback if (_daemon_tls_configured() and loopback) else primary_port
        if not http_target_port:
            print("⚠️  Cannot configure Claude MCP — daemon not running and no fixed port set", file=sys.stderr)
            return
        _sp.run(
            ["claude", "mcp", "add", "--transport", "http", "talky", f"http://localhost:{http_target_port}/mcp"],
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

    from talky.shared.profile_manager import get_profile_manager

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

    if launcher.get("autoconnect_browser", True):
        client_url = f"{daemon_base_url()}?autoconnect=true"
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
