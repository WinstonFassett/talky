"""T3 Code session token handling (WIN-564).

The token is the JSON ``t3 auth session issue --json`` prints (``sessionId``, ``token``,
``expiresAt``, ``scopes``), stored at ``$T3_TOKEN_FILE`` or ``~/.talky/credentials/t3.json``
with mode 600. Sessions are narrow (``orchestration:read`` + ``orchestration:operate``) and
short-lived; ``ensure_token`` re-issues one shortly before it expires and revokes the old one.
Token values are never logged or returned from anything that prints.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

SCOPES = ("orchestration:read", "orchestration:operate")
DEFAULT_TTL = "8h"
REFRESH_MARGIN_SECS = 15 * 60
_FAIL_COOLDOWN_SECS = 30

_lock = threading.Lock()
_last_error: Optional["T3AuthError"] = None
_last_error_at = 0.0


class T3AuthError(RuntimeError):
    """``code`` ∈ no_cli | t3_down | denied | expired."""

    def __init__(self, msg: str, code: str = "denied"):
        super().__init__(msg)
        self.code = code


def autoconnect_enabled() -> bool:
    """``TALKY_T3_AUTOCONNECT=0`` restores explicit consent: no auto-issue, user runs ``talky t3 login``."""
    return os.environ.get("TALKY_T3_AUTOCONNECT", "1").strip().lower() not in ("0", "false", "no", "off")


def token_path() -> Path:
    return Path(os.environ.get("T3_TOKEN_FILE") or "~/.talky/credentials/t3.json").expanduser()


def t3_bin() -> Optional[str]:
    explicit = os.environ.get("T3_BIN")
    if explicit:
        return explicit
    home = Path("~/.t3/bin/t3").expanduser()
    return str(home) if home.exists() else shutil.which("t3")


def _expires_in(rec: dict) -> Optional[float]:
    raw = rec.get("expiresAt")
    if not raw:
        return None
    exp = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return (exp - _dt.datetime.now(_dt.timezone.utc)).total_seconds()


def read_record(path: Optional[Path] = None) -> Optional[dict]:
    path = path or token_path()
    try:
        with open(path) as f:
            rec = json.load(f)
    except FileNotFoundError:
        return None
    if os.stat(path).st_mode & 0o077:
        os.chmod(path, 0o600)  # tokens are secrets: tighten a file someone loosened
    return rec if isinstance(rec, dict) and rec.get("token") else None


def _run(*args: str) -> str:
    binary = t3_bin()
    if not binary:
        raise T3AuthError("t3 CLI not found (set T3_BIN or install T3 Code)", "no_cli")
    what = " ".join(args[:3])
    try:
        # cwd=home: the daemon can outlive the directory it was launched from, and
        # the node-based t3 CLI dies on process.cwd() (ENOENT, uv_cwd) when it does.
        proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=60,
                              cwd=str(Path.home()))
    except FileNotFoundError as e:
        raise T3AuthError("t3 CLI not found (set T3_BIN or install T3 Code)", "no_cli") from e
    except subprocess.TimeoutExpired as e:
        raise T3AuthError(f"t3 {what} timed out", "t3_down") from e
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        hint = f": {err[-1][:200]}" if err else ""
        down = any(w in (proc.stderr or "").lower() for w in ("econnrefused", "connection refused", "connect error"))
        raise T3AuthError(f"t3 {what} failed (exit {proc.returncode}){hint}", "t3_down" if down else "denied")
    return proc.stdout


def issue(path: Optional[Path] = None, ttl: str = DEFAULT_TTL, label: str = "talky") -> dict:
    """Issue a fresh narrow session into the token file (mode 600), revoking the one it replaces."""
    path = path or token_path()
    old = read_record(path)
    args = ["auth", "session", "issue", "--ttl", ttl, "--label", label, "--json"]
    for s in SCOPES:
        args += ["--scope", s]
    out = _run(*args)
    rec = json.JSONDecoder().raw_decode(out[out.index("{"):])[0]
    if not rec.get("token"):
        raise T3AuthError("t3 returned no token", "denied")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f)
    os.chmod(path, 0o600)
    if old and old.get("sessionId") and old["sessionId"] != rec["sessionId"]:
        try:
            _run("auth", "session", "revoke", old["sessionId"])
        except T3AuthError:
            pass
    return rec


def revoke(path: Optional[Path] = None) -> Optional[str]:
    """Revoke the stored session and delete the file. Returns the revoked session id."""
    path = path or token_path()
    rec = read_record(path)
    if rec is None:
        return None
    if rec.get("sessionId"):
        _run("auth", "session", "revoke", rec["sessionId"])
    path.unlink(missing_ok=True)
    return rec.get("sessionId")


def _auto_issue(path: Path) -> dict:
    """Issue a session on first use. One at a time; a recent failure is re-raised for 30 s instead of re-spawning the CLI."""
    global _last_error, _last_error_at
    with _lock:
        rec = read_record(path)  # another thread may have issued while we waited
        if rec is not None:
            return rec
        if _last_error is not None and time.monotonic() - _last_error_at < _FAIL_COOLDOWN_SECS:
            raise _last_error
        try:
            return issue(path)
        except T3AuthError as e:
            _last_error, _last_error_at = e, time.monotonic()
            raise


def ensure_token(token_env: str = "T3_TOKEN", token_file: Optional[str] = None) -> str:
    """The bearer to use: ``$T3_TOKEN`` if set, else the token file (auto-issued on first use), re-issued when near expiry."""
    tok = os.environ.get(token_env)
    if tok:
        return tok
    path = Path(token_file).expanduser() if token_file else token_path()
    rec = read_record(path)
    if rec is None:
        if not autoconnect_enabled():
            raise T3AuthError(f"no T3 token: run `talky t3 login` (looks in {path})", "denied")
        rec = _auto_issue(path)
    left = _expires_in(rec)
    if left is not None and left < REFRESH_MARGIN_SECS:
        try:
            rec = issue(path)
        except T3AuthError as e:
            if left <= 0:
                raise T3AuthError(f"T3 session expired and could not be renewed: {e}", "expired") from e
    return rec["token"]


def _reset_cooldown() -> None:
    global _last_error
    _last_error = None


def connect(path: Optional[Path] = None, ttl: str = DEFAULT_TTL) -> dict:
    """Force-issue a session (the Connect / Retry button); resets the failure cooldown."""
    with _lock:
        _reset_cooldown()
    return issue(path, ttl=ttl)


def disconnect(path: Optional[Path] = None) -> Optional[str]:
    """Revoke + delete the stored session; no-op when there is none."""
    _reset_cooldown()
    return revoke(path)


def status(base_url: Optional[str] = None) -> dict:
    """Connection state for the UI. Never contains the token. Blocking (CLI + HTTP ping): call off the event loop."""
    import httpx

    from talky.backends.t3_threads import base_url as _default_base

    base = base_url or _default_base()
    out = {"state": "needs_connect", "code": None, "detail": "", "session": None, "expires_at": None, "scopes": [], "base_url": base}
    try:
        ensure_token()
    except T3AuthError as e:
        out["code"], out["detail"] = e.code, str(e)
        if e.code == "denied" and not autoconnect_enabled():
            out["detail"] = "Auto-connect is off (TALKY_T3_AUTOCONNECT=0); run `talky t3 login`"
        return out
    rec = read_record() or {}
    try:
        httpx.get(base.rstrip("/") + "/", timeout=2.0)
    except Exception:  # noqa: BLE001
        out["code"], out["detail"] = "t3_down", f"T3 Code isn't running at {base}"
        return out
    out.update(
        state="connected",
        session=(rec.get("sessionId") or "")[:8] or None,
        expires_at=rec.get("expiresAt"),
        scopes=list(rec.get("scopes") or []),
    )
    return out


def available() -> bool:
    """Usable without a manual login: a token exists, or the CLI can issue one."""
    return bool(os.environ.get("T3_TOKEN")) or read_record() is not None or t3_bin() is not None
