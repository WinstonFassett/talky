"""Daemon readiness tracker.

Tracks slow startup work (pip extras, HF model downloads, native lib probes,
backend binary checks, anything that gates user-perceived readiness) and
emits structured progress through ``event_bus`` so the CLI and SPA can
surface it.

Design notes:
- Generic by construction: callers register a *task name* and optionally
  push progress updates. The tracker doesn't know what a "whisper download"
  or "pyaudio install" is — providers just call ``track(name)``.
- One ready flag for the whole daemon. ``is_ready()`` is False while any
  task is open. The lifespan hook gates the ready-file on this.
- Events on ``event_bus`` use type ``"readiness"`` with payloads like::

      {"phase": "start",    "name": "Downloading whisper model",
       "task_id": "...", "pct": null, "msg": null}
      {"phase": "progress", "name": "Downloading whisper model",
       "task_id": "...", "pct": 47.0, "msg": "model.safetensors"}
      {"phase": "done",     "name": "Downloading whisper model",
       "task_id": "...", "ok": true, "msg": null}
      {"phase": "done",     "name": "Installing pyaudio",
       "task_id": "...", "ok": false, "msg": "uv exit 1"}

  Subscribers can render a list of open tasks by tracking
  task_id and replaying start/progress/done.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from loguru import logger

from talky.server.event_bus import event_bus


@dataclass
class _Task:
    task_id: str
    name: str
    started_at: float
    pct: Optional[float] = None
    msg: Optional[str] = None


@dataclass
class ReadinessTracker:
    """Tracks open readiness tasks. One per process (singleton below)."""

    _tasks: dict[str, _Task] = field(default_factory=dict)
    _ready_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        # Daemon starts ready; tasks flip it to not-ready when opened.
        self._ready_event.set()

    # ── state ──────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return len(self._tasks) == 0

    def open_tasks(self) -> list[dict[str, Any]]:
        return [
            {
                "task_id": t.task_id,
                "name": t.name,
                "pct": t.pct,
                "msg": t.msg,
                "elapsed_s": round(time.monotonic() - t.started_at, 2),
            }
            for t in self._tasks.values()
        ]

    async def wait_ready(self, timeout: Optional[float] = None) -> bool:
        """Block until all tasks are done. Returns True if ready, False on timeout."""
        try:
            if timeout is None:
                await self._ready_event.wait()
            else:
                await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ── emit (no asyncio loop required; safe to call from sync code) ──────

    def _emit(self, payload: dict[str, Any]) -> None:
        """Emit a readiness event. Safe from both async and sync contexts."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.emit("readiness", payload))
        except RuntimeError:
            # No running loop — sync context (e.g. CLI startup before lifespan).
            # Caller can still read open_tasks() directly; SSE just won't fire
            # for these. That's the right tradeoff: don't make sync callers
            # spin up a loop.
            pass

    # ── task lifecycle ─────────────────────────────────────────────────────

    def _open(self, name: str) -> str:
        task_id = uuid.uuid4().hex[:8]
        self._tasks[task_id] = _Task(task_id=task_id, name=name, started_at=time.monotonic())
        self._ready_event.clear()
        logger.info(f"readiness: start {name!r} (id={task_id})")
        self._emit({"phase": "start", "task_id": task_id, "name": name, "pct": None, "msg": None})
        return task_id

    def _progress(self, task_id: str, *, pct: Optional[float] = None, msg: Optional[str] = None) -> None:
        t = self._tasks.get(task_id)
        if t is None:
            return
        if pct is not None:
            t.pct = pct
        if msg is not None:
            t.msg = msg
        self._emit({"phase": "progress", "task_id": task_id, "name": t.name, "pct": pct, "msg": msg})

    def _close(self, task_id: str, *, ok: bool = True, msg: Optional[str] = None) -> None:
        t = self._tasks.pop(task_id, None)
        if t is None:
            return
        elapsed = time.monotonic() - t.started_at
        logger.info(f"readiness: done {t.name!r} ok={ok} elapsed={elapsed:.1f}s")
        self._emit({"phase": "done", "task_id": task_id, "name": t.name, "ok": ok, "msg": msg})
        if not self._tasks:
            self._ready_event.set()

    # ── public API ─────────────────────────────────────────────────────────

    @contextmanager
    def track(self, name: str) -> Iterator["ReadinessHandle"]:
        """Sync context manager that opens a readiness task and closes it on exit.

        Usage::

            with tracker.track("Installing pyaudio") as t:
                t.progress(pct=50.0, msg="downloading")
                ...
        """
        task_id = self._open(name)
        handle = ReadinessHandle(self, task_id)
        try:
            yield handle
            self._close(task_id, ok=True)
        except BaseException as exc:
            self._close(task_id, ok=False, msg=str(exc))
            raise

    @asynccontextmanager
    async def track_async(self, name: str):
        """Async variant for async call sites."""
        task_id = self._open(name)
        handle = ReadinessHandle(self, task_id)
        try:
            yield handle
            self._close(task_id, ok=True)
        except BaseException as exc:
            self._close(task_id, ok=False, msg=str(exc))
            raise


@dataclass
class ReadinessHandle:
    """Caller-side handle for reporting progress on an open task."""

    _tracker: ReadinessTracker
    _task_id: str

    def progress(self, *, pct: Optional[float] = None, msg: Optional[str] = None) -> None:
        self._tracker._progress(self._task_id, pct=pct, msg=msg)


# Module-level singleton.
readiness = ReadinessTracker()
