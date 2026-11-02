"""T3 auto-connect (WIN-575): ensure_token issues its own session; status/connect/disconnect."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
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


def test_failure_surfaces_cli_stderr(rig):
    """SWE-748: a failed CLI call must say why, or a deleted-cwd crash looks like a mystery exit 1."""
    (rig / "fail").touch()
    with pytest.raises(t3_auth.T3AuthError) as ei:
        t3_auth.ensure_token()
    assert "fake t3: issue failed (fail flag set)" in str(ei.value)


def test_run_pins_cwd(rig, monkeypatch):
    """SWE-748: ``_run`` must spawn the CLI from an explicit, existing cwd — never inherit the daemon's
    (a daemon left over from a finished agent workspace runs from a deleted cwd)."""
    captured: dict = {}

    def fake_run(args, **kw):
        captured["cwd"] = kw.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout='{"token": "fake-x"}', stderr="")

    monkeypatch.setattr(t3_auth.subprocess, "run", fake_run)
    t3_auth._run("auth", "session", "issue", "--json")
    assert captured.get("cwd") is not None and Path(captured["cwd"]).is_dir()


_ELECTRONISH_CLI = """#!/bin/sh
# Mimics the real ~/.t3/bin/t3 wrapper (shell -> Electron/Node): Node's process.cwd()
# throws ENOENT when the spawning process's cwd was deleted, crashing the CLI with
# exit 1 (SWE-748). The real wrapper execs the T3 Code desktop app's bundled Electron.
LOG="${FAKE_T3_CLI_LOG:-/dev/null}"
echo "argv: $*" >> "$LOG"
python3 - <<'PYEOF'
import os, sys
try:
    os.getcwd()
except OSError as e:
    print(
        "Error: ENOENT: process.cwd failed "
        + str(e)
        + ", the current working directory was likely removed without changing the working directory, uv_cwd",
        file=sys.stderr,
    )
    sys.exit(1)
print('{"sessionId":"00000000-0000-0000-0000-000000000001","token":"fake-electron","method":"bearer-access-token","scopes":["orchestration:read"],"expiresAt":"2099-01-01T00:00:00Z"}')
PYEOF
exit $?
"""


def test_run_survives_deleted_cwd(rig, monkeypatch):
    """SWE-748 regression: the daemon outlives its spawner's workspace, so its cwd is deleted; the
    t3 CLI then crashes on process.cwd() (exit 1) and the T3 session can never be renewed. ``_run``
    pins its cwd, so the CLI must still work from a process whose own cwd no longer exists."""
    nodeish = rig / "fake_t3_cli_electron.sh"
    nodeish.write_text(_ELECTRONISH_CLI)
    nodeish.chmod(0o755)

    doomed = rig / "doomed"
    doomed.mkdir()

    helper = rig / "helper.py"
    helper.write_text(
        "import os, sys, time\n"
        f"os.chdir({str(doomed)!r})\n"
        "open('ready', 'w').write('ready')\n"
        f"deadline = time.time() + 30\n"
        f"while os.path.exists({str(doomed)!r}):\n"
        "    if time.time() > deadline:\n"
        "        sys.exit(3)\n"
        "    time.sleep(0.02)\n"
        "try:\n"
        "    os.getcwd()\n"
        "    print('NOT-DELETED')\n"
        "    sys.exit(2)\n"
        "except OSError:\n"
        "    pass\n"
        "from talky.backends import t3_auth\n"
        "try:\n"
        "    t3_auth._run('auth', 'session', 'issue', '--ttl', '8h', '--label', 'talky', '--json')\n"
        "    print('OK')\n"
        "except Exception as e:\n"
        "    print('ERR:', repr(e)[:200])\n"
        "    sys.exit(1)\n"
    )

    env = dict(os.environ)
    env["T3_BIN"] = str(nodeish)
    env["FAKE_T3_CLI_LOG"] = str(rig / "cli.log")
    env.pop("T3_TOKEN", None)

    p = subprocess.Popen(
        [sys.executable, str(helper)], cwd=str(doomed), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        deadline = time.time() + 15
        while not (doomed / "ready").exists():
            if time.time() > deadline or p.poll() is not None:
                out, err = p.communicate(timeout=5)
                pytest.fail(f"helper never signalled ready; exit={p.returncode} stdout={out!r} stderr={err!r}")
            time.sleep(0.02)
        shutil.rmtree(doomed)
        out, err = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
    assert p.returncode == 0, f"exit={p.returncode} stdout={out!r} stderr={err!r}"
    assert "OK" in out, f"CLI failed under a deleted cwd: stdout={out!r} stderr={err!r}"


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
