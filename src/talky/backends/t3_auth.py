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
from pathlib import Path
from typing import Optional

SCOPES = ("orchestration:read", "orchestration:operate")
DEFAULT_TTL = "8h"
REFRESH_MARGIN_SECS = 15 * 60


class T3AuthError(RuntimeError):
    pass


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
        raise T3AuthError("t3 CLI not found (set T3_BIN or install T3 Code)")
    proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise T3AuthError(f"t3 {' '.join(args[:3])} failed (exit {proc.returncode})")
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
        raise T3AuthError("t3 returned no token")
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


def ensure_token(token_env: str = "T3_TOKEN", token_file: Optional[str] = None) -> str:
    """The bearer to use: ``$T3_TOKEN`` if set, else the token file, re-issued when near expiry."""
    tok = os.environ.get(token_env)
    if tok:
        return tok
    path = Path(token_file).expanduser() if token_file else token_path()
    rec = read_record(path)
    if rec is None:
        raise T3AuthError(f"no T3 token: run `talky t3 login` (looks in {path})")
    left = _expires_in(rec)
    if left is not None and left < REFRESH_MARGIN_SECS:
        try:
            rec = issue(path)
        except T3AuthError as e:
            if left <= 0:
                raise T3AuthError(f"T3 session expired and could not be renewed: {e}") from e
    return rec["token"]


def available() -> bool:
    return bool(os.environ.get("T3_TOKEN")) or read_record() is not None
