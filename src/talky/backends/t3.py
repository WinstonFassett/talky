"""T3 Code backend for Talky (WIN-563 spike).

Sends each user turn to a T3 Code thread over T3's orchestration RPC and streams
the assistant reply back by polling the thread projection (see ``t3_client``).

Architecture
------------
T3 is not an ACP server and its ``/mcp`` endpoint rejects normal sessions, so this
is an RPC client: the first turn launches a thread (``orchestration.launchThread``,
or reuses ``thread_id``), later turns send ``message.dispatch``. The reply is read
from ``orchestration.getThreadProjection`` every ``poll_interval`` seconds and the
growth of the run's assistant text is emitted as ``TextFrame`` deltas. The projection's
``turnItems`` are read too (WIN-585): ``reasoning`` items become ``aggregated_by="thinking"``
frames (only newly grown text), ``command_execution`` items a ``tool_start`` (``▶ cmd``) and,
once done, a ``tool_end`` (``✓``/``✗``) frame,
the same frames hermes/opencode emit. The first of these opens the response, which ends the
"working" cue; the TTS skips both types (``skip_aggregator_types``), so they are never spoken.

Auth
----
``token_env`` names an env var holding a scoped bearer
(``t3 auth session issue --scope orchestration:read --scope orchestration:operate``);
``token_file`` may point at the JSON that command printed instead. The token is never
logged. Revoke the session when done (``t3 auth session revoke <id>``).

Thread watcher (WIN-605)
------------------------
A turn only reads the runs it caused. Runs nobody asked for out loud — a timer or background-task
wakeup, a message typed in T3's own app — are read by the thread watcher: while a thread is bound it
holds ``orchestration.subscribeThread`` open (the stream T3's app uses), and on every push re-reads the
projection and speaks any run no turn is reading, in full. Turns and the watcher share the "already sent"
state, so a run is never spoken twice, and a turn that runs out of time is finished by the watcher.

Interrupt
---------
``InterruptionFrame`` during a turn sends ``run.interrupt`` for the thread's active run. During a
watcher reply it only stops the speech: T3's run was not started by Talky, so it is left alone.
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
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from talky.backends.t3_auth import T3AuthError, available, ensure_token
from talky.backends.t3_client import T3Client
from talky.backends.t3_threads import base_url as t3_base_url
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

    # A live call can be re-pointed at another thread via ``set_resume`` (WIN-576).
    live_attach = True

    @staticmethod
    def status() -> tuple["BackendStatus", str]:
        from talky.backends import BackendStatus

        if not available():
            return BackendStatus.MISCONFIGURED, "T3 Code CLI not found (set T3_BIN or install T3 Code)"
        return BackendStatus.READY, ""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
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
        dispatch_timeout: float = 15.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._base_url = base_url or t3_base_url()  # same $T3_BASE_URL the thread picker uses
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
        # Upper bound for the token load + projection + send that precede the reply
        # read. A dispatch that exceeds it (dead socket, T3 wedged) surfaces as a
        # backend error instead of silently swallowing the user's turn (SWE-729).
        self._dispatch_timeout = dispatch_timeout
        self._client: Optional[T3Client] = None
        self._turn_task: Optional[asyncio.Task] = None
        self._queued: deque[str] = deque()  # turns spoken while one is running (WIN-564)
        # Reply state shared by turns and the thread watcher (WIN-605): what was already sent per item.
        self._emitted: dict[str, int] = {}  # message id -> chars already sent
        self._thought: dict[str, str] = {}  # reasoning item id -> text already sent
        self._ran: set[str] = set()  # command_execution item ids already announced
        self._ended: set[str] = set()  # ... and already closed with tool_end
        self._handled: set[str] = set()  # run ids fully read (or muted by a barge-in)
        # SWE-729: whether the current turn's message has reached T3 yet. A barge-in
        # must never cancel the in-flight dispatch — that silently lost what the user
        # said. When it lands before the send, ``_barge_pending`` is set instead and
        # ``_run_turn`` interrupts the run the send creates right after it completes.
        self._dispatched = False
        self._barge_pending = False
        # Set from the speaking frames the turn detector broadcasts; a reply that
        # first appears while the user is still talking must not talk over them.
        self._user_speaking = False
        self._watch_enabled = False  # set by start(): the watcher lives with a started service
        self._watch_task: Optional[asyncio.Task] = None
        self._watch_dirty = asyncio.Event()
        self._watch_open: set[str] = set()  # runs the watcher is speaking right now (response open)

    def set_resume(self, thread_id: Optional[str]) -> None:
        if thread_id != self._thread_id:
            self._stop_watch()
            self._handled.clear()
        self._thread_id = thread_id
        self._ensure_watch()

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
        try:
            self._client = T3Client(self._base_url, await asyncio.to_thread(self._load_token))
        except T3AuthError as e:  # a failed T3 must not stop the pipeline build; _run_turn reports it
            logger.warning(f"T3LLMService: no token at start ({e.code}); will retry on the first turn")
            self._client = T3Client(self._base_url, "")
        logger.info(f"T3LLMService started — base_url={self._base_url} thread={self._thread_id}")
        self._watch_enabled = True
        self._ensure_watch()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._shutdown()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._shutdown()

    async def _shutdown(self):
        self._watch_enabled = False
        self._stop_watch()
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

    async def _dispatch(self, text: str) -> tuple[str, set[str]]:
        """Send ``text`` to the bound thread (or launch it); returns ``(thread_id, known_runs)``.

        This is the one part of a turn that must never be cancelled by a barge-in:
        once the user's words leave Talky they are the only copy of the turn that
        reaches T3 (SWE-729). It is bounded by ``_dispatch_timeout`` so a dead
        socket surfaces as an error instead of hanging the queue.
        """
        assert self._client is not None
        if self._thread_id is None:
            res = await self._client.launch_thread(
                project_id=await self._scratch_project(),
                title=f"Talky {uuid.uuid4().hex[:6]}",
                text=text,
                instance_id=self._instance_id,
                model=self._model,
                runtime_mode=self._runtime_mode,
            )
            self._thread_id = res["threadId"]
            return self._thread_id, set()
        before = await self._client.thread_projection(self._thread_id)
        # Skip runs already read; an unfinished one nobody read yet (a wakeup reply) is read here too.
        known = self._handled | {r["id"] for r in before["runs"] if r["status"] in _TERMINAL}
        await self._client.send_message(self._thread_id, text)
        return self._thread_id, known

    async def _run_turn(self, text: str) -> None:
        assert self._client is not None
        c = self._client
        started = False
        cue = asyncio.create_task(self._working_cue(lambda: started))
        try:
            try:
                c._token = await asyncio.to_thread(self._load_token)  # noqa: SLF001 — picks up a refreshed session
            except T3AuthError as e:
                await self._emit_backend_error(str(e))
                return
            try:
                _, known_runs = await asyncio.wait_for(self._dispatch(text), timeout=self._dispatch_timeout)
            except asyncio.TimeoutError:
                logger.error("t3: turn dispatch timed out — dropping the socket so the next RPC reconnects")
                await c.reset_socket()
                await self._emit_backend_error("T3 dispatch timed out; the message may not have been sent — say it again")
                return
            self._dispatched = True
            if self._barge_pending:
                # A barge-in landed while the message was still being sent (SWE-729):
                # it reached T3, but the user has moved on — stop the run it created
                # and never read or speak its reply.
                self._barge_pending = False
                await self._stop_thread()
                await self._mute_dispatch_runs()
                return

            async def open_response() -> None:
                nonlocal started
                if not started:
                    started = True
                    await self.push_frame(LLMFullResponseStartFrame())

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._run_timeout
            thread_id = self._thread_id
            assert thread_id is not None  # _dispatch launched a thread or attached to one
            run_ids: set[str] = set()
            while loop.time() < deadline:
                await asyncio.sleep(self._poll)
                p = await c.thread_projection(thread_id)
                runs = [r for r in p["runs"] if r["id"] not in known_runs]
                run_ids = {r["id"] for r in runs}
                await self._emit_runs(p, run_ids, open_response)
                if run_ids and all(r["id"] in self._handled or r["status"] in _TERMINAL for r in runs):
                    self._handled |= run_ids
                    if any(r["status"] == "failed" for r in runs):
                        await self._emit_backend_error("T3 run failed")
                    break
            else:
                # Not an error any more (WIN-605): the thread watcher keeps reading the run from where this stopped.
                logger.info(f"t3: turn stopped reading after {self._run_timeout:.0f}s; the thread watcher continues")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"t3 turn failed: {e}", exc_info=True)
            await self._emit_backend_error(f"T3 turn failed: {e or type(e).__name__}")
        finally:
            cue.cancel()
            if started:
                await self.push_frame(LLMFullResponseEndFrame())

    async def _mute_dispatch_runs(self) -> None:
        """Mark the run(s) the just-sent message created as handled, so nothing speaks their reply.

        Called after a barge-in interrupted the dispatch: the message reached T3, but
        the user has moved on and the reply must not play (SWE-729). The run id is only
        known once T3 materialises it, so poll briefly. The watcher defers to a running
        turn, so it cannot read the run before we mark it.
        """
        assert self._client is not None and self._thread_id is not None
        c = self._client
        thread_id = self._thread_id
        for _ in range(40):  # up to ~10 s for T3 to create the run
            await asyncio.sleep(0.25)
            try:
                p = await c.thread_projection(thread_id)
            except Exception:  # noqa: BLE001 — best effort; the watcher falls back
                return
            new = [r for r in p["runs"] if r["id"] not in self._handled]
            if new:
                self._handled |= {r["id"] for r in new}
                return

    def _response_started(self, p: dict, run_ids: set[str]) -> bool:
        """Whether any of ``run_ids`` has already been emitted (text, thinking, or a command)."""
        for m in p.get("messages") or []:
            if m.get("runId") in run_ids and self._emitted.get(m["id"], 0) > 0:
                return True
        for it in p.get("turnItems") or []:
            if it.get("runId") in run_ids and (it["id"] in self._thought or it["id"] in self._ran):
                return True
        return False

    async def _emit_runs(  # noqa: ANN001
        self, p: dict, run_ids: set[str], open_response, *, interrupt: bool = True
    ) -> None:
        """Push what is new in ``run_ids`` since the last read: thinking, commands, then reply text."""
        if run_ids and self._user_speaking and not self._response_started(p, run_ids):
            # A reply that first appears while the user is still talking must not talk
            # over them (SWE-729): never open a response for these runs. If Talky
            # started the run, stop it too — the next turn supersedes it anyway.
            self._handled |= run_ids
            if interrupt:
                await self._stop_thread()
            return
        items = sorted(
            (i for i in p.get("turnItems") or [] if i.get("runId") in run_ids),
            key=lambda i: i.get("ordinal", 0),
        )
        for it in items:
            out: list[AggregatedTextFrame] = []
            if it.get("type") == "reasoning":
                text, prev = it.get("text") or "", self._thought.get(it["id"], "")
                if text and text != prev:
                    self._thought[it["id"]] = text
                    out.append(AggregatedTextFrame(
                        text=text[len(prev):] if text.startswith(prev) else text, aggregated_by="thinking"
                    ))
            elif it.get("type") == "command_execution" and it.get("title") != "Workspace ready" and it.get("input"):
                cmd = it["input"][:60]
                if it["id"] not in self._ran:
                    self._ran.add(it["id"])
                    out.append(AggregatedTextFrame(text=f"▶ {cmd}", aggregated_by="tool_start"))
                if it["id"] not in self._ended and it.get("status") in ("completed", "failed"):
                    self._ended.add(it["id"])
                    mark = "✗" if it["status"] == "failed" else "✓"
                    out.append(AggregatedTextFrame(text=f"{mark} {cmd}", aggregated_by="tool_end"))
            for frame in out:
                await open_response()
                await self.push_frame(frame)
        for m in p["messages"]:
            if m["role"] != "assistant" or m.get("runId") not in run_ids:
                continue
            sent = self._emitted.get(m["id"], 0)
            if len(m["text"]) > sent:
                await open_response()
                await self.push_frame(TextFrame(m["text"][sent:]))
                self._emitted[m["id"]] = len(m["text"])

    # --- Thread watcher (WIN-605) ---

    def _turn_running(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    def _ensure_watch(self) -> None:
        if not self._watch_enabled or not self._thread_id or not self._client:
            return
        if self._watch_task and not self._watch_task.done():
            return
        self._watch_task = asyncio.create_task(self._watch(self._thread_id))

    def _stop_watch(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None
        self._watch_open.clear()

    async def _watch(self, thread_id: str) -> None:
        """Hold T3's thread subscription open and speak runs no turn is reading; resubscribe on drops."""
        consumer = asyncio.create_task(self._watch_consume(thread_id))
        try:
            while self._thread_id == thread_id:
                try:
                    assert self._client is not None
                    async for item in self._client.subscribe_thread(thread_id):
                        if item.get("kind") in ("snapshot", "event"):
                            self._watch_dirty.set()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — socket drop, expired token, T3 restarting
                    logger.warning(f"t3 watcher: subscription ended ({e}); resubscribing")
                await asyncio.sleep(2.0)
                try:
                    assert self._client is not None
                    self._client._token = await asyncio.to_thread(self._load_token)  # noqa: SLF001
                except Exception:  # noqa: BLE001 — retried on the next loop
                    pass
        finally:
            consumer.cancel()

    async def _watch_consume(self, thread_id: str) -> None:
        """Coalesce pushes: re-read the projection once per burst of changes."""
        first = True
        while True:
            await self._watch_dirty.wait()
            await asyncio.sleep(0.1)
            self._watch_dirty.clear()
            if self._turn_running():
                continue  # the turn reads every new run itself; re-checked when it finishes
            try:
                assert self._client is not None
                p = await self._client.thread_projection(thread_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"t3 watcher: projection read failed: {e}")
                continue
            if self._turn_running():
                continue
            if first:
                self._baseline(p)
                first = False
            await self._watch_step(p)

    def _baseline(self, p: dict) -> None:
        """On attach: finished runs are history (not spoken); a run already underway is spoken from here on."""
        for r in p["runs"]:
            if r["status"] in _TERMINAL:
                self._handled.add(r["id"])
        for m in p["messages"]:
            if m.get("runId") not in self._handled:
                self._emitted.setdefault(m["id"], len(m.get("text") or ""))
        for it in p.get("turnItems") or []:
            if it.get("runId") not in self._handled:
                if it.get("type") == "reasoning":
                    self._thought.setdefault(it["id"], it.get("text") or "")
                elif it.get("type") == "command_execution":
                    self._ran.add(it["id"])
                    if it.get("status") in ("completed", "failed"):
                        self._ended.add(it["id"])

    async def _watch_step(self, p: dict) -> None:
        runs = [r for r in p["runs"] if r["id"] not in self._handled]
        if not runs:
            return
        run_ids = {r["id"] for r in runs}

        async def open_response() -> None:
            if not self._watch_open:
                logger.info(f"t3 watcher: speaking {len(run_ids)} run(s) Talky did not start")
                await self.push_frame(LLMFullResponseStartFrame())
            self._watch_open.update(run_ids)

        await self._emit_runs(p, run_ids, open_response, interrupt=False)
        done = {r["id"] for r in runs if r["status"] in _TERMINAL}
        self._handled |= done
        if self._watch_open and all(r["status"] in _TERMINAL for r in runs):
            self._watch_open.clear()
            await self.push_frame(LLMFullResponseEndFrame())

    async def _close_watch_response(self, mute: bool) -> None:
        """End a watcher reply in progress; ``mute`` (barge-in) also drops the rest of those runs."""
        if not self._watch_open:
            return
        if mute:
            self._handled |= self._watch_open
        self._watch_open.clear()
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

    def abandon_turn(self) -> None:
        """Stop reading the current turn's reply (the call was rebound to another chat, WIN-582).

        Unlike an ``InterruptionFrame`` this leaves T3's own run alone: the user only left the chat.
        A turn whose message has not reached T3 yet is not cancelled — it is delivered to
        the thread it was said in, and its reply is muted (SWE-729).
        """
        self._queued.clear()
        if self._watch_open:  # mute the watcher reply too; the watcher itself follows set_resume()
            self._handled |= self._watch_open
            self._watch_open.clear()
            asyncio.ensure_future(self.push_frame(LLMFullResponseEndFrame()))
        if self._turn_task and not self._turn_task.done():
            if self._dispatched:
                self._turn_task.cancel()
            else:
                self._barge_pending = True

    def _turn_finished(self, task: asyncio.Task) -> None:
        """Serialize turns: run the next queued one only after the previous poll loop is done."""
        if task is not self._turn_task:
            return
        self._turn_task = None
        if self._queued and self._client:
            self._start_turn(self._queued.popleft())
        else:
            self._ensure_watch()  # a turn that launched a new thread starts its watcher here
            self._watch_dirty.set()  # anything that arrived during the turn is checked now

    def _start_turn(self, text: str) -> None:
        if self._watch_open:  # a typed turn over a watcher reply: that reply's run is read by the turn now
            self._watch_open.clear()
            asyncio.ensure_future(self.push_frame(LLMFullResponseEndFrame()))
        self._dispatched = False
        self._barge_pending = False
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

        if isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
            self._user_speaking = True
        elif isinstance(frame, (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)):
            self._user_speaking = False
        elif isinstance(frame, InterruptionFrame):
            await self._close_watch_response(mute=True)  # stop speaking it; T3's run is not Talky's to stop
            task = self._turn_task
            if task and not task.done():
                if self._dispatched:
                    # The message is already in T3: barge-in stops reading its reply.
                    task.cancel()
                    await self._stop_thread()
                else:
                    # The message has not reached T3 yet — cancelling here would
                    # silently lose what the user said (SWE-729). Let the dispatch
                    # finish; _run_turn interrupts the run it creates right after.
                    self._barge_pending = True
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
