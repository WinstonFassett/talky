"""Pi Durable backend — PiRPCLLMService + stable session file (resumable).

The dream: reopen a past voice conversation and continue it, optionally with a
different model. Pi binary already persists each `--mode rpc` session as a
JSONL file; passing the same `--session-id` on a new process picks up where
the prior one left off (verified WIN-533). This backend is PiRPCLLMService with
that id wired through, so a daemon restart or `talky kill && talky daemon`
resumes mid-thought instead of starting over.

Session files live under `--session-dir` (default `~/.talky/convos/`). The id
is a stable uuid4 by default, or whatever `set_resume(id)` / the `resume`
kwarg / `--resume` on `talky profile` sets. Different `--provider`/`--model`
on the same id = same history, different brain.
"""

import os
import uuid
from pathlib import Path
from typing import Optional

from talky.backends.pi_rpc import PiRPCLLMService

TALKY_CONVOS_DIR = Path(os.path.expanduser("~/.talky/convos"))


def convos_dir() -> Path:
    TALKY_CONVOS_DIR.mkdir(parents=True, exist_ok=True)
    return TALKY_CONVOS_DIR


def list_convos() -> list[dict]:
    """Scan the pi-session dir and return [{id, path, mtime, size}] newest first."""
    d = convos_dir()
    out = []
    for p in d.glob("*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        # Pi session filename: YYYY-MM-DDTHH-MM-SS-mmmZ_<sessionid>.jsonl
        stem = p.stem
        sess_id = stem.split("_", 1)[1] if "_" in stem else stem
        out.append({"id": sess_id, "path": str(p), "mtime": st.st_mtime, "size": st.st_size})
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


class PiDurableLLMService(PiRPCLLMService):
    """Pi RPC with a stable session file so convos survive daemon restarts.

    Extra kwargs vs. PiRPCLLMService:
      resume: Optional[str]  — reuse this session id (else a fresh uuid4)
      session_dir: Optional[str] — override `~/.talky/convos/`
    """

    def __init__(
        self,
        *,
        resume: Optional[str] = None,
        session_dir: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._session_dir = Path(session_dir).expanduser() if session_dir else convos_dir()
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = resume or str(uuid.uuid4())

    @property
    def session_id(self) -> str:
        return self._session_id

    def set_resume(self, session_id: Optional[str]) -> None:
        """Point at a different convo on the next pipeline (re)build. One-shot semantics
        match claude_code.set_resume; actually re-opening requires a pipeline rebuild
        or ManuallySwitchServiceFrame — the daemon arranges that."""
        if session_id:
            self._session_id = session_id

    def _build_cmd(self) -> list[str]:
        cmd = ["pi", "--mode", "rpc", "--session-id", self._session_id,
               "--session-dir", str(self._session_dir)]
        if self._provider:
            cmd += ["--provider", self._provider]
        if self._model:
            cmd += ["--model", self._model]
        cmd.extend(self._extra_args)
        return cmd
