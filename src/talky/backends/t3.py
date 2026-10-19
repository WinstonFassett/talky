"""T3 Code backend for Talky (WIN-563 spike).

Sends each user turn to a T3 Code thread over T3's orchestration RPC and streams
the assistant reply back by polling the thread projection (see ``t3_client``).

Architecture
------------
T3 is not an ACP server and its ``/mcp`` endpoint rejects normal sessions, so this
is an RPC client: the first turn launches a thread (``orchestration.launchThread``,
or reuses ``thread_id``), later turns send ``message.dispatch``. The reply is read
from ``orchestration.getThreadProjection`` every ``poll_interval`` seconds and the
growth of the run's assistant text is emitted as ``TextFrame`` deltas.

Auth
----
``token_env`` names an env var holding a scoped bearer
(``t3 auth session issue --scope orchestration:read --scope orchestration:operate``);
``token_file`` may point at the JSON that command printed instead. The token is never
logged. Revoke the session when done (``t3 auth session revoke <id>``).

Interrupt
---------
``InterruptionFrame`` sends ``run.interrupt`` for the thread's active run.
"""

import asyncio
import math
import os
import struct
import uuid
from collections import deque
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from talky.backends import BackendStatus

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    OutputAudioRawFrame,
    StartFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from talky.backends.t3_auth import T3AuthError, available, ensure_token
from talky.backends.t3_client import T3Client
from talky.convos.replay import HistoryPreambleMixin
from talky.server.turn import UserTurnTextFrame

_TERMINAL = {"completed", "failed", "cancelled", "interrupted", "rolled_back"}

# "Working" cue (WIN-564): T3's first reply takes 8-14 s. After CUE_DELAY of silence the
# UI gets a status block and the user hears a soft double tick, repeated every CUE_EVERY.
CUE_DELAY = 2.0
CUE_EVERY = 6.0
CUE_RATE = 24000


def _working_earcon(rate: int = CUE_RATE) -> bytes:
    """Two quiet 880 Hz ticks (~150 ms total), 16-bit mono PCM."""
    def tick(ms: int) -> bytes:
        n = rate * ms // 1000
        return b"".join(
            struct.pack("<h", int(3500 * math.sin(math.pi * i / n) * math.sin(2 * math.pi * 880 * i / rate)))
            for i in range(n)
        )

    return tick(50) + b"\x00\x00" * (rate * 40 // 1000) + tick(50)


class T3LLMService(HistoryPreambleMixin, LLMService):
    """T3 Code orchestration-RPC backend.

    Config keys (from llm-backends.yaml):
      base_url:       T3 server, default http://127.0.0.1:3773
      token_env:      env var holding the scoped bearer token
      token_file:     alternative: path to JSON from ``t3 auth session issue --json``
      project_id:     T3 project to launch the thread in (default: the "No project" scratch project)
      thread_id:      existing thread to talk to instead of launching a new one
                      (``talky t3 attach`` / the web picker set it via the convo's native session id)
      instance_id:    provider instance, e.g. "claudeAgent"
      model:          model on that instance
      runtime_mode:   approval-required | auto-accept-edits | auto | full-access
      poll_interval:  seconds between projection polls
    """

    @staticmethod
    def status() -> tuple["BackendStatus", str]:
        from talky.backends import BackendStatus

        if not available():
            return BackendStatus.MISCONFIGURED, "run `talky t3 login` (or set T3_TOKEN / T3_TOKEN_FILE)"
        return BackendStatus.READY, ""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:3773",
        token_env: str = "T3_TOKEN",
        token_file: Optional[str] = None,
        project_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        resume: Optional[str] = None,
        instance_id: str = "claudeAgent",
        model: str = "claude-sonnet-5-5",
        runtime_mode: str = "approval-required",
        poll_interval: float = 0.5,
        run_timeout: float = 300.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._base_url = base_url
        self._token_env = token_env
        self._token_file = token_file  # None → $T3_TOKEN_FILE or ~/.talky/credentials/t3.json
        self._project_id = project_id
        # ``resume`` (a convo's native session id) wins over the configured ``thread_id``.
        # Either way the backend ATTACHES to that existing T3 thread instead of launching one.
        self._thread_id = resume or thread_id
        self._instance_id = instance_id
        self._model = model
        self._runtime_mode = runtime_mode
        self._poll = poll_interval
        self._run_timeout = run_timeout
        self._client: Optional[T3Client] = None
        self._turn_task: Optional[asyncio.Task] = None
        self._queued: deque[str] = deque()  # turns spoken while one is running (WIN-564)

    def set_resume(self, thread_id: Optional[str]) -> None:
        self._thread_id = thread_id

    @property
    def _session_id(self) -> Optional[str]:
        """T3 thread id, read by ``native_session_id_of`` so a Talky convo resumes onto it."""
        return self._thread_id

    def _load_token(self) -> str:
        """The current bearer; re-issues the stored session shortly before it expires."""
        return ensure_token(self._token_env, self._token_file)

    # --- Lifecycle ---

    async def start(self, frame: StartFrame):
        await super().start(frame)
        self._client = T3Client(self._base_url, self._load_token())
        logger.info(f"T3LLMService started — base_url={self._base_url} thread={self._thread_id}")

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._shutdown()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._shutdown()

    async def _shutdown(self):
        self._queued.clear()
        if self._turn_task:
            self._turn_task.cancel()
            self._turn_task = None
        if self._client:
            await self._client.close()
            self._client = None

    # --- Turn ---

    async def _scratch_project(self) -> str:
        assert self._client is not None
        if self._project_id:
            return self._project_id
        snap = await self._client.shell()
        for p in snap["projects"]:
            if p.get("title") == "No project":
                self._project_id = p["id"]
                return p["id"]
        raise RuntimeError("no project_id configured and no scratch project found")

    async def _run_turn(self, text: str) -> None:
        assert self._client is not None
        c = self._client
        started = False
        cue = asyncio.create_task(self._working_cue(lambda: started))
        try:
            try:
                c._token = self._load_token()  # noqa: SLF001 — picks up a refreshed session
            except T3AuthError as e:
                await self._emit_backend_error(str(e))
                return
            if self._thread_id is None:
                res = await c.launch_thread(
                    project_id=await self._scratch_project(),
                    title=f"Talky {uuid.uuid4().hex[:6]}",
                    text=text,
                    instance_id=self._instance_id,
                    model=self._model,
                    runtime_mode=self._runtime_mode,
                )
                self._thread_id = res["threadId"]
                known_runs: set[str] = set()
            else:
                before = await c.thread_projection(self._thread_id)
                known_runs = {r["id"] for r in before["runs"]}
                await c.send_message(self._thread_id, text)

            emitted: dict[str, int] = {}  # message id -> chars already sent
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._run_timeout
            while loop.time() < deadline:
                await asyncio.sleep(self._poll)
                p = await c.thread_projection(self._thread_id)
                runs = [r for r in p["runs"] if r["id"] not in known_runs]
                run_ids = {r["id"] for r in runs}
                for m in p["messages"]:
                    if m["role"] != "assistant" or m.get("runId") not in run_ids:
                        continue
                    sent = emitted.get(m["id"], 0)
                    if len(m["text"]) > sent:
                        if not started:
                            started = True
                            await self.push_frame(LLMFullResponseStartFrame())
                        await self.push_frame(TextFrame(m["text"][sent:]))
                        emitted[m["id"]] = len(m["text"])
                if runs and all(r["status"] in _TERMINAL for r in runs):
                    if any(r["status"] == "failed" for r in runs):
                        await self._emit_backend_error("T3 run failed")
                    break
            else:
                await self._emit_backend_error("T3 run timed out")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"t3 turn failed: {e}", exc_info=True)
            await self._emit_backend_error(f"T3 turn failed: {e}")
        finally:
            cue.cancel()
            if started:
                await self.push_frame(LLMFullResponseEndFrame())

    async def _working_cue(self, reply_started) -> None:  # noqa: ANN001
        """Fill the silence before T3's first words: a status block now, a soft earcon on a repeat."""
        try:
            await asyncio.sleep(CUE_DELAY)
            first = True
            while not reply_started():
                await self.push_frame(
                    AggregatedTextFrame(text="⏳ T3 is working…" if first else "⏳ still working…", aggregated_by="info")
                )
                await self.push_frame(OutputAudioRawFrame(audio=_working_earcon(), sample_rate=CUE_RATE, num_channels=1))
                first = False
                await asyncio.sleep(CUE_EVERY)
        except asyncio.CancelledError:
            pass

    def _turn_finished(self, task: asyncio.Task) -> None:
        """Serialize turns: run the next queued one only after the previous poll loop is done."""
        if task is not self._turn_task:
            return
        self._turn_task = None
        if self._queued and self._client:
            self._start_turn(self._queued.popleft())

    def _start_turn(self, text: str) -> None:
        self._turn_task = asyncio.create_task(self._run_turn(text))
        self._turn_task.add_done_callback(self._turn_finished)

    async def _emit_backend_error(self, message: str) -> None:
        try:
            from talky.server.event_bus import event_bus

            await event_bus.emit("backendError", {"profile": "t3", "message": message})
        except Exception:
            pass

    async def _stop_thread(self) -> None:
        if not self._client or not self._thread_id:
            return
        try:
            await self._client.interrupt(self._thread_id)
        except Exception as e:
            logger.warning(f"t3 interrupt failed: {e}")

    # --- process_frame ---

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            self._queued.clear()  # spoken before the barge-in: superseded
            if self._turn_task and not self._turn_task.done():
                self._turn_task.cancel()
                await self._stop_thread()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            if not self._client:
                logger.warning("t3 backend not started — dropping turn")
                return
            logger.info(f"t3 ← user: {frame.text[:80]}")
            text = self._consume_preamble(frame.text)
            if self._turn_task and not self._turn_task.done():
                self._queued.append(text)  # one poll loop at a time: run after the current reply
                logger.info(f"t3: turn queued behind a running one ({len(self._queued)} waiting)")
            else:
                self._start_turn(text)
            return

        await self.push_frame(frame, direction)
