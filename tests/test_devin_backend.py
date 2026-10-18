"""DevinACPLLMService auth behaviour (WIN-554).

Drives the backend against a fake `devin` binary that speaks just enough ACP
(initialize, session/new, session/set_config_option, session/prompt) and
records every method it receives plus whether WINDSURF_API_KEY reached it.
No real devin, no network, no browser.

Covers:
1. No ACP `authenticate` is sent — even with DEVIN_API_KEY in the env.
2. WINDSURF_API_KEY is injected only when HOME is not the real home, from the
   real home's ~/.local/share/devin/credentials.toml, and never logged.
3. A logged-out CLI ("Please log in") fails the backend with a clear message,
   and status() keeps it off until the credentials file changes.
4. Repeated deaths back off, then stop respawning.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio
from loguru import logger
from pipecat.frames.frames import LLMFullResponseEndFrame, TextFrame
from pipecat.processors.frame_processor import FrameDirection

from talky.backends import BackendStatus
from talky.backends import devin as devin_mod
from talky.backends.devin import LOGGED_OUT_MESSAGE, MAX_FAILURES, DevinACPLLMService
from talky.server.event_bus import event_bus
from talky.server.turn import UserTurnTextFrame

FAKE_KEY = "fake-windsurf-key-0123456789"

FAKE_DEVIN = r'''#!{python}
import json, os, sys
log = os.environ["FAKE_DEVIN_LOG"]
logged_out = os.environ.get("FAKE_DEVIN_LOGGED_OUT") == "1"
def rec(m):
    with open(log, "a") as f:
        f.write(json.dumps({{"method": m, "windsurf": os.environ.get("WINDSURF_API_KEY")}}) + "\n")
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    msg = json.loads(line)
    m, i = msg.get("method"), msg.get("id")
    if m:
        rec(m)
    if i is None or not m:
        continue
    if m == "initialize":
        out({{"jsonrpc": "2.0", "id": i, "result": {{"protocolVersion": 1, "authMethods": [{{"id": "devin-browser"}}]}}}})
    elif m == "session/new":
        out({{"jsonrpc": "2.0", "id": i, "result": {{"sessionId": "fake-session"}}}})
    elif m == "session/prompt":
        if logged_out:
            out({{"jsonrpc": "2.0", "id": i, "error": {{"code": -32000, "message": "Please log in to use Devin. Use `/login` to authenticate again."}}}})
        else:
            out({{"jsonrpc": "2.0", "method": "session/update", "params": {{"sessionId": "fake-session", "update": {{"sessionUpdate": "agent_message_chunk", "content": {{"type": "text", "text": "hi"}}}}}}}})
            out({{"jsonrpc": "2.0", "id": i, "result": {{"stopReason": "end_turn"}}}})
    else:
        out({{"jsonrpc": "2.0", "id": i, "result": {{}}}})
'''


@pytest.fixture(autouse=True)
def reset_class_state():
    DevinACPLLMService._logged_out = False
    DevinACPLLMService._logged_out_mtime = None
    DevinACPLLMService._failures = 0
    DevinACPLLMService._retry_at = 0.0
    yield
    DevinACPLLMService._logged_out = False
    DevinACPLLMService._logged_out_mtime = None
    DevinACPLLMService._failures = 0
    DevinACPLLMService._retry_at = 0.0


@pytest.fixture
def real_home(tmp_path, monkeypatch):
    """A fake 'real home' holding devin credentials; _real_home() points here."""
    home = tmp_path / "realhome"
    creds = home / ".local" / "share" / "devin"
    creds.mkdir(parents=True)
    (creds / "credentials.toml").write_text(
        f'windsurf_api_key = "{FAKE_KEY}"\napi_server_url = "https://example.invalid"\n'
    )
    monkeypatch.setattr(devin_mod, "_real_home", lambda: home)
    return home


@pytest.fixture
def fake_devin(tmp_path, monkeypatch):
    bin_path = tmp_path / "devin"
    bin_path.write_text(FAKE_DEVIN.format(python=sys.executable))
    bin_path.chmod(0o755)
    log = tmp_path / "devin-calls.jsonl"
    monkeypatch.setenv("FAKE_DEVIN_LOG", str(log))
    return bin_path, log


@pytest.fixture
def captured_logs():
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    yield lines
    logger.remove(sink)


@pytest_asyncio.fixture
async def make_backend(fake_devin, tmp_path, monkeypatch):
    from pipecat.services.llm_service import LLMService

    async def noop_async(self, *args, **kwargs):
        return None

    monkeypatch.setattr(LLMService, "start", noop_async, raising=True)
    monkeypatch.setattr(LLMService, "stop", noop_async, raising=True)
    monkeypatch.setattr(LLMService, "cancel", noop_async, raising=True)
    monkeypatch.setattr(LLMService, "process_frame", noop_async, raising=True)

    made: list[DevinACPLLMService] = []

    async def make():
        bin_path, _ = fake_devin
        svc = DevinACPLLMService(binary=str(bin_path), cwd=str(tmp_path))
        frames: list = []

        async def push_frame(frame, direction=None):
            frames.append(frame)

        svc.push_frame = push_frame  # type: ignore[assignment]
        await svc.start(None)  # type: ignore[arg-type]
        made.append(svc)
        return svc, frames

    yield make
    for svc in made:
        await svc._shutdown()


def _calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


async def _turn(svc, frames, text="hello", timeout=10.0):
    await svc.process_frame(UserTurnTextFrame(text=text), FrameDirection.DOWNSTREAM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(isinstance(f, LLMFullResponseEndFrame) for f in frames):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"turn did not end: {[type(f).__name__ for f in frames]}")


# ── 1. no authenticate ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_authenticate_even_with_devin_api_key(make_backend, fake_devin, real_home, monkeypatch):
    monkeypatch.setenv("DEVIN_API_KEY", "cog_expired_cloud_key")
    monkeypatch.setenv("HOME", str(real_home))
    svc, frames = await make_backend()
    assert svc.session_id == "fake-session"
    await _turn(svc, frames)

    methods = [c["method"] for c in _calls(fake_devin[1])]
    assert "authenticate" not in methods
    assert methods[:2] == ["initialize", "session/new"]
    assert "session/prompt" in methods
    assert any(isinstance(f, TextFrame) and f.text == "hi" for f in frames)


# ── 2. WINDSURF_API_KEY injection ────────────────────────────────────────


def test_child_env_real_home_gets_no_key(real_home, captured_logs):
    env = devin_mod.build_child_env({"HOME": str(real_home), "PATH": "/usr/bin", "ACP_BACKEND": "x"})
    assert "WINDSURF_API_KEY" not in env
    assert "ACP_BACKEND" not in env  # poison strip kept


def test_child_env_isolated_home_gets_key_never_logged(real_home, tmp_path, captured_logs):
    iso = tmp_path / "iso-home"
    iso.mkdir()
    env = devin_mod.build_child_env({"HOME": str(iso), "PATH": "/usr/bin"})
    assert env["WINDSURF_API_KEY"] == FAKE_KEY
    assert captured_logs, "expected an info line about the isolated HOME"
    assert not any(FAKE_KEY in line for line in captured_logs)


def test_child_env_isolated_home_without_credentials(tmp_path, monkeypatch, captured_logs):
    monkeypatch.setattr(devin_mod, "_real_home", lambda: tmp_path / "nohome")
    env = devin_mod.build_child_env({"HOME": str(tmp_path), "PATH": "/usr/bin"})
    assert "WINDSURF_API_KEY" not in env


@pytest.mark.asyncio
async def test_isolated_home_key_reaches_child_not_logs(make_backend, fake_devin, real_home, tmp_path, monkeypatch, captured_logs):
    iso = tmp_path / "iso-home"
    iso.mkdir()
    monkeypatch.setenv("HOME", str(iso))
    svc, frames = await make_backend()
    await _turn(svc, frames)
    calls = _calls(fake_devin[1])
    assert calls and all(c["windsurf"] == FAKE_KEY for c in calls)
    assert not any(FAKE_KEY in line for line in captured_logs)


@pytest.mark.asyncio
async def test_real_home_child_gets_no_key(make_backend, fake_devin, real_home, monkeypatch):
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.delenv("WINDSURF_API_KEY", raising=False)
    svc, frames = await make_backend()
    await _turn(svc, frames)
    calls = _calls(fake_devin[1])
    assert calls and all(c["windsurf"] is None for c in calls)


# ── 3. logged out ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logged_out_fails_clearly_and_stays_off(make_backend, fake_devin, real_home, monkeypatch):
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("FAKE_DEVIN_LOGGED_OUT", "1")
    monkeypatch.setattr(devin_mod.shutil, "which", lambda _: "/usr/local/bin/devin")
    async with event_bus.subscribe() as q:
        svc, frames = await make_backend()
        await _turn(svc, frames)
        errors = []
        while not q.empty():
            ev = q.get_nowait()
            if ev.type == "backendError":
                errors.append(ev.data["message"])

    assert any(isinstance(f, TextFrame) and f.text == LOGGED_OUT_MESSAGE for f in frames)
    assert errors == [LOGGED_OUT_MESSAGE]
    assert svc._dead
    # Not a crash — doesn't count toward the respawn backoff.
    assert DevinACPLLMService._failures == 0

    st, reason = DevinACPLLMService.status()
    assert st == BackendStatus.MISCONFIGURED
    assert reason == LOGGED_OUT_MESSAGE
    assert "authenticate" not in [c["method"] for c in _calls(fake_devin[1])]

    # `devin auth login` rewrites credentials.toml → Ready again.
    creds = real_home / ".local" / "share" / "devin" / "credentials.toml"
    os.utime(creds, (time.time() + 5, time.time() + 5))
    assert DevinACPLLMService.status() == (BackendStatus.READY, "")


# ── 4. respawn backoff ───────────────────────────────────────────────────


def test_deaths_back_off_then_stop(monkeypatch):
    monkeypatch.setattr(devin_mod.shutil, "which", lambda _: "/usr/local/bin/devin")
    assert DevinACPLLMService.status()[0] == BackendStatus.READY

    DevinACPLLMService._record_failure()
    st, reason = DevinACPLLMService.status()
    assert st == BackendStatus.BLOCKED and "retrying" in reason

    DevinACPLLMService._retry_at = 0.0  # backoff elapsed
    assert DevinACPLLMService.status()[0] == BackendStatus.READY

    for _ in range(MAX_FAILURES - 1):
        DevinACPLLMService._record_failure()
    DevinACPLLMService._retry_at = 0.0
    st, reason = DevinACPLLMService.status()
    assert st == BackendStatus.BLOCKED and "restart the daemon" in reason


@pytest.mark.asyncio
async def test_successful_handshake_resets_failures(make_backend, real_home, monkeypatch):
    monkeypatch.setenv("HOME", str(real_home))
    DevinACPLLMService._failures = 2
    await make_backend()
    assert DevinACPLLMService._failures == 0
