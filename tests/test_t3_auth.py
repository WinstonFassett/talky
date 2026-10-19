"""T3 auto-connect (WIN-575): ensure_token issues its own session; status/connect/disconnect."""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from pathlib import Path

import httpx
import pytest

from talky.backends import t3_auth, t3_threads

FAKE_CLI = Path(__file__).parent / "fakes" / "fake_t3_cli.sh"


@pytest.fixture(autouse=True)
def rig(tmp_path, monkeypatch):
    monkeypatch.delenv("T3_TOKEN", raising=False)
    monkeypatch.delenv("TALKY_T3_AUTOCONNECT", raising=False)
    monkeypatch.setenv("T3_BIN", str(FAKE_CLI))
    monkeypatch.setenv("T3_TOKEN_FILE", str(tmp_path / "t3.json"))
    monkeypatch.setenv("FAKE_T3_CLI_LOG", str(tmp_path / "cli.log"))
    monkeypatch.setenv("FAKE_T3_FAIL_FLAG", str(tmp_path / "fail"))
    monkeypatch.setattr(t3_auth, "_last_error", None)
    return tmp_path


def _log(rig) -> list[str]:
    p = rig / "cli.log"
    return p.read_text().splitlines() if p.exists() else []


def _issues(rig) -> int:
    return sum(1 for line in _log(rig) if line.startswith("argv: auth session issue"))


def test_first_use_issues_session_mode_600_and_scopes(rig):
    tok = t3_auth.ensure_token()
    assert tok.startswith("fake-")
    assert stat.S_IMODE(os.stat(rig / "t3.json").st_mode) == 0o600
    assert _issues(rig) == 1
    line = next(x for x in _log(rig) if "session issue" in x)
    assert "--scope orchestration:read --scope orchestration:operate" in line
    assert t3_auth.ensure_token() == tok and _issues(rig) == 1  # reused, no second CLI call


def test_concurrent_first_use_issues_once(rig):
    out: list[str] = []
    threads = [threading.Thread(target=lambda: out.append(t3_auth.ensure_token())) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(out)) == 1 and _issues(rig) == 1


def test_failure_is_denied_and_backs_off_for_30s(rig, monkeypatch):
    (rig / "fail").touch()
    with pytest.raises(t3_auth.T3AuthError) as ei:
        t3_auth.ensure_token()
    assert ei.value.code == "denied" and _issues(rig) == 1
    with pytest.raises(t3_auth.T3AuthError):
        t3_auth.ensure_token()
    assert _issues(rig) == 1  # cooldown: CLI not spawned again
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 31)
    with pytest.raises(t3_auth.T3AuthError):
        t3_auth.ensure_token()
    assert _issues(rig) == 2


def test_connect_resets_the_cooldown(rig):
    (rig / "fail").touch()
    with pytest.raises(t3_auth.T3AuthError):
        t3_auth.ensure_token()
    (rig / "fail").unlink()
    assert t3_auth.connect()["token"]
    assert _issues(rig) == 2


def test_no_cli_code(monkeypatch):
    monkeypatch.delenv("T3_BIN")
    monkeypatch.setattr(t3_auth, "t3_bin", lambda: None)
    with pytest.raises(t3_auth.T3AuthError) as ei:
        t3_auth.ensure_token()
    assert ei.value.code == "no_cli"


def test_killswitch_means_no_cli_call(rig, monkeypatch):
    monkeypatch.setenv("TALKY_T3_AUTOCONNECT", "0")
    with pytest.raises(t3_auth.T3AuthError, match="talky t3 login"):
        t3_auth.ensure_token()
    assert _log(rig) == [] and not (rig / "t3.json").exists()
    st = t3_auth.status("http://127.0.0.1:9")
    assert st["state"] == "needs_connect" and "TALKY_T3_AUTOCONNECT=0" in st["detail"]
    assert _log(rig) == []


def test_status_and_errors_never_contain_the_token(rig, monkeypatch):
    tok = t3_auth.ensure_token()
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200))
    st = t3_auth.status("http://x")
    assert st["state"] == "connected" and st["session"] and len(st["session"]) == 8
    assert tok not in json.dumps(st)
    err = t3_auth.T3AuthError("boom", "denied")
    assert tok not in str(err) and tok not in repr(err)


def test_status_t3_down(rig, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)
    st = t3_auth.status("http://127.0.0.1:9")
    assert st["state"] == "needs_connect" and st["code"] == "t3_down" and "isn't running" in st["detail"]


def test_disconnect_revokes_and_deletes_then_noops(rig):
    t3_auth.ensure_token()
    sid = json.loads((rig / "t3.json").read_text())["sessionId"]
    assert t3_auth.disconnect() == sid
    assert f"revoke {sid}" in _log(rig) and not (rig / "t3.json").exists()
    n = len(_log(rig))
    assert t3_auth.disconnect() is None and len(_log(rig)) == n


class _Resp:
    def __init__(self, code):
        self.status_code = code


class Client401:
    def __init__(self):
        self.calls = 0
        self._token = "old"

    async def shell(self):
        self.calls += 1
        if self.calls == 1:
            raise httpx.HTTPStatusError("401", request=None, response=_Resp(401))  # type: ignore[arg-type]
        return {"projects": [], "threads": []}

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_t3_threads_401_reissues_once(rig):
    c = Client401()
    assert await t3_threads.list_threads(client=c) == []  # type: ignore[arg-type]
    assert c.calls == 2 and _issues(rig) == 1 and c._token.startswith("fake-")


def test_delete_guard_no_destructive_rpc_in_src():
    """S0–S2 must never call delete/archive/stop on T3 threads (auto-issued scopes allow it)."""
    root = Path(__file__).resolve().parent.parent
    needles = ("thread.delete", "project.delete", "thread.archive", "thread.stop")
    skip = {Path(__file__).resolve()}
    hits = []
    for base in (root / "src" / "talky", root / "client" / "src"):
        for f in base.rglob("*"):
            if not f.is_file() or f.resolve() in skip or "fakes" in f.parts or f.suffix in (".pyc", ".png", ".woff2"):
                continue
            try:
                text = f.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            hits += [f"{f}: {n}" for n in needles if n in text]
    assert hits == []
