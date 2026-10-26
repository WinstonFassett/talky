"""T3LLMService turn logic against a fake T3 client (no T3 server needed)."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, TextFrame

from talky.backends.t3 import T3LLMService


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(T3LLMService, "_load_token", lambda self: "tok")


class FakeClient:
    def __init__(self):
        self.sent: list[str] = []
        self.launched: list[str] = []
        self.polls = 0

    async def shell(self):
        return {"projects": [{"id": "p-scratch", "title": "No project"}]}

    async def launch_thread(self, *, text, **kw):
        self.launched.append(text)
        return {"threadId": "t-1"}

    async def send_message(self, thread_id, text):
        self.sent.append(text)

    async def thread_projection(self, thread_id):
        self.polls += 1
        steps = [
            ([{"id": "r1", "status": "running"}], "Hel"),
            ([{"id": "r1", "status": "completed"}], "Hello there"),
        ]
        runs, text = steps[min(self.polls - 1, 1)] if self.launched or self.sent else ([], "")
        if self.sent and self.polls == 1:  # pre-send snapshot for a follow-up: no runs yet
            return {"runs": [], "messages": []}
        return {
            "runs": runs,
            "messages": [{"id": "m1", "role": "assistant", "runId": "r1", "text": text}],
        }


async def _collect(svc: T3LLMService) -> list:
    frames: list = []

    async def push(frame, direction=None):
        frames.append(frame)

    svc.push_frame = push  # type: ignore[method-assign]
    return frames


@pytest.mark.asyncio
async def test_first_turn_launches_thread_and_streams_deltas():
    svc = T3LLMService(poll_interval=0)
    svc._client = FakeClient()  # type: ignore[assignment]
    frames = await _collect(svc)
    await svc._run_turn("hi")
    assert svc._client.launched == ["hi"]  # type: ignore[attr-defined]
    assert svc._session_id == "t-1"  # native id → convo resume
    assert [type(f) for f in frames] == [
        LLMFullResponseStartFrame, TextFrame, TextFrame, LLMFullResponseEndFrame,
    ]
    assert "".join(f.text for f in frames if isinstance(f, TextFrame)) == "Hello there"


@pytest.mark.asyncio
async def test_resume_attaches_to_existing_thread_and_sends():
    svc = T3LLMService(poll_interval=0, resume="t-existing")
    client = FakeClient()
    svc._client = client  # type: ignore[assignment]
    frames = await _collect(svc)
    await svc._run_turn("follow up")
    assert client.sent == ["follow up"] and client.launched == []
    assert svc._session_id == "t-existing"
    assert isinstance(frames[-1], LLMFullResponseEndFrame)


# --- WIN-564: thread list, serialization, working cue, token refresh ---

import asyncio  # noqa: E402
import datetime as _dt  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

from pipecat.frames.frames import AggregatedTextFrame, OutputAudioRawFrame  # noqa: E402

from talky.backends import t3 as t3_mod  # noqa: E402
from talky.backends import t3_auth, t3_threads  # noqa: E402
from talky.server.turn import UserTurnTextFrame  # noqa: E402


def test_normalise_orders_running_first_and_drops_archived():
    snap = {
        "projects": [{"id": "p1", "title": "ai-setup"}],
        "threads": [
            {"id": "a", "title": "old", "projectId": "p1", "status": "completed", "updatedAt": "2026-10-17T00:00:00Z",
             "modelSelection": {"model": "m1"}},
            {"id": "b", "title": "busy", "projectId": "p1", "status": "idle", "activeRunId": "r", "updatedAt": "2026-10-16T00:00:00Z"},
            {"id": "c", "title": "new", "projectId": "zzz", "status": "failed", "updatedAt": "2026-10-18T00:00:00Z"},
            {"id": "d", "title": "gone", "archivedAt": "2026-10-18T00:00:00Z", "updatedAt": "2026-10-19T00:00:00Z"},
        ],
    }
    rows = t3_threads.normalise(snap)
    assert [r["id"] for r in rows] == ["b", "c", "a"]
    assert rows[0]["status"] == "running" and rows[0]["project"] == "ai-setup"
    assert rows[1]["project"] == "No project" and rows[2]["model"] == "m1"


def test_resolve_thread_by_prefix_title_and_ambiguity():
    rows = [{"id": "abc123", "title": "Fix login"}, {"id": "abd999", "title": "Fix logout"}]
    assert t3_threads.resolve_thread(rows, "abc")["id"] == "abc123"
    assert t3_threads.resolve_thread(rows, "logout")["id"] == "abd999"
    with pytest.raises(LookupError, match="2 threads"):
        t3_threads.resolve_thread(rows, "fix")
    with pytest.raises(LookupError):
        t3_threads.resolve_thread(rows, "nope")


class SlowClient(FakeClient):
    """Replies after ``steps`` polls; tracks how many turns poll at once."""

    def __init__(self):
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.turns = 0

    async def send_message(self, thread_id, text):
        self.sent.append(text)

    async def thread_projection(self, thread_id):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        n = len(self.sent)
        runs = [{"id": f"r{i}", "status": "completed"} for i in range(n)]
        return {"runs": runs[:-1] if self.polls_pending() else runs, "messages": []}

    def polls_pending(self):
        return False


@pytest.mark.asyncio
async def test_second_turn_queues_instead_of_polling_in_parallel(monkeypatch):
    monkeypatch.setattr(t3_mod.T3LLMService, "_load_token", lambda self: "tok")
    svc = T3LLMService(poll_interval=0, resume="t-x")
    client = SlowClient()
    client._token = "tok"
    svc._client = client  # type: ignore[assignment]
    await _collect(svc)
    await svc.process_frame(UserTurnTextFrame(text="one"), None)  # type: ignore[arg-type]
    await svc.process_frame(UserTurnTextFrame(text="two"), None)  # type: ignore[arg-type]
    assert list(svc._queued) == ["two"]
    while svc._turn_task or svc._queued:
        await asyncio.sleep(0.02)
    assert client.sent == ["one", "two"]
    assert client.max_active == 1


@pytest.mark.asyncio
async def test_working_cue_emits_status_and_earcon(monkeypatch):
    monkeypatch.setattr(t3_mod, "CUE_DELAY", 0.01)
    monkeypatch.setattr(t3_mod, "CUE_EVERY", 100)
    svc = T3LLMService(poll_interval=0)
    frames = await _collect(svc)
    task = asyncio.create_task(svc._working_cue(lambda: False))
    await asyncio.sleep(0.1)
    task.cancel()
    await task
    assert any(isinstance(f, AggregatedTextFrame) and f.aggregated_by == "info" for f in frames)
    assert any(isinstance(f, OutputAudioRawFrame) and f.audio for f in frames)
    # a reply that already started means no cue at all
    frames.clear()
    await svc._working_cue(lambda: True)
    assert frames == []


def test_token_refresh_reissues_near_expiry_and_keeps_mode_600(tmp_path, monkeypatch):
    path = tmp_path / "t3.json"
    soon = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=2)).isoformat()
    later = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=8)).isoformat()
    path.write_text(json.dumps({"sessionId": "old", "token": "tok-old", "expiresAt": soon}))
    os.chmod(path, 0o644)
    calls: list[tuple] = []

    def fake_run(*args):
        calls.append(args)
        if args[1] == "session" and args[2] == "issue":
            return json.dumps({"sessionId": "new", "token": "tok-new", "expiresAt": later, "scopes": list(t3_auth.SCOPES)})
        return ""

    monkeypatch.setattr(t3_auth, "_run", fake_run)
    monkeypatch.delenv("T3_TOKEN", raising=False)
    assert t3_auth.ensure_token(token_file=str(path)) == "tok-new"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert ("auth", "session", "revoke", "old") in calls
    issue_args = next(c for c in calls if c[2] == "issue")
    assert "orchestration:read" in issue_args and "orchestration:operate" in issue_args and "--ttl" in issue_args
    # a fresh token is used as-is, no further CLI calls
    calls.clear()
    assert t3_auth.ensure_token(token_file=str(path)) == "tok-new" and calls == []


# --- WIN-575: auto-connect ---


@pytest.mark.asyncio
async def test_run_turn_loads_token_off_the_event_loop(monkeypatch):
    import threading

    seen: list[bool] = []

    def load(self):
        seen.append(threading.current_thread() is not threading.main_thread())
        return "tok"

    monkeypatch.setattr(T3LLMService, "_load_token", load)
    svc = T3LLMService(poll_interval=0)
    svc._client = FakeClient()  # type: ignore[assignment]
    await _collect(svc)
    await svc._run_turn("hi")
    assert seen == [True]


def test_status_is_ready_with_cli_but_no_token_and_misconfigured_without(monkeypatch, tmp_path):
    from talky.backends import BackendStatus

    monkeypatch.delenv("T3_TOKEN", raising=False)
    monkeypatch.setenv("T3_TOKEN_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("T3_BIN", "/bin/true")
    assert T3LLMService.status()[0] == BackendStatus.READY
    monkeypatch.delenv("T3_BIN")
    monkeypatch.setattr(t3_auth, "t3_bin", lambda: None)
    st, msg = T3LLMService.status()
    assert st == BackendStatus.MISCONFIGURED and "CLI not found" in msg


# --- WIN-585: show T3's thinking + commands (turnItems) ---
# Shapes are copied from a real getThreadProjection capture; the client is a FAKE.

import re  # noqa: E402
from pathlib import Path  # noqa: E402

_R1 = {"id": "r1", "status": "running"}
_R1_DONE = {"id": "r1", "status": "completed"}


def _item(type_, ordinal, id_, **kw):
    return {"id": id_, "runId": "r1", "ordinal": ordinal, "type": type_, "status": "completed", **kw}


_PREP = _item("command_execution", 1, "i-prep", title="Workspace ready", input="Preparing workspace", outputOmitted=True)
_USER = _item("user_message", 0, "i-user", text="hi")
_CMD = _item("command_execution", 3, "i-cmd", title="Ran command", input="echo done", outputOmitted=True)


def _reason(text, streaming=False):
    return _item("reasoning", 2, "i-th", title="Thinking", text=text, streaming=streaming)


class ItemsClient:
    """Fake T3 client: replays one projection per poll (last one repeats)."""

    def __init__(self, snapshots):
        self.snapshots, self.polls = snapshots, 0

    async def shell(self):
        return {"projects": [{"id": "p", "title": "No project"}]}

    async def launch_thread(self, **kw):
        return {"threadId": "t-1"}

    async def thread_projection(self, thread_id):
        snap = self.snapshots[min(self.polls, len(self.snapshots) - 1)]
        self.polls += 1
        return snap


def _snap(run, items, text=""):
    msgs = [{"id": "m1", "role": "assistant", "runId": "r1", "text": text}] if text else []
    return {"runs": [run], "messages": msgs, "turnItems": items}


async def _run(snapshots):
    svc = T3LLMService(poll_interval=0)
    svc._client = ItemsClient(snapshots)  # type: ignore[assignment]
    frames = await _collect(svc)
    await svc._run_turn("hi")
    return frames


def _agg(frames):
    return [(f.aggregated_by, f.text) for f in frames if isinstance(f, AggregatedTextFrame)]


@pytest.mark.asyncio
async def test_reasoning_and_command_become_thinking_and_tool_start_before_reply():
    frames = await _run([
        _snap(_R1, [_USER, _PREP, _reason("Plan it.")]),
        _snap(_R1, [_USER, _PREP, _reason("Plan it."), _CMD]),
        _snap(_R1_DONE, [_USER, _PREP, _reason("Plan it."), _CMD], "done"),
    ])
    assert _agg(frames) == [
        ("thinking", "Plan it."), ("tool_start", "▶ echo done"), ("tool_end", "✓ echo done"),
    ]  # prep skipped, no repeats
    assert [type(f) for f in frames] == [
        LLMFullResponseStartFrame, AggregatedTextFrame, AggregatedTextFrame, AggregatedTextFrame, TextFrame,
        LLMFullResponseEndFrame,
    ]


@pytest.mark.asyncio
async def test_reasoning_sends_only_new_chars_and_resends_when_rewritten():
    frames = await _run([
        _snap(_R1, [_reason("Let me", True)]),
        _snap(_R1, [_reason("Let me think", True)]),
        _snap(_R1, [_reason("Rewritten entirely", True)]),
        _snap(_R1_DONE, [_reason("Rewritten entirely")], "ok"),
    ])
    assert _agg(frames) == [("thinking", "Let me"), ("thinking", " think"), ("thinking", "Rewritten entirely")]


@pytest.mark.asyncio
async def test_long_command_truncated_and_no_items_means_plain_reply():
    long = _item("command_execution", 3, "i-l", title="Ran command", input="x" * 100)
    frames = await _run([_snap(_R1_DONE, [long], "hi")])
    assert _agg(frames) == [("tool_start", "▶ " + "x" * 60), ("tool_end", "✓ " + "x" * 60)]
    frames = await _run([_snap(_R1_DONE, [], "hi")])  # low effort / old server: no turnItems
    assert [type(f) for f in frames] == [LLMFullResponseStartFrame, TextFrame, LLMFullResponseEndFrame]
    snap = _snap(_R1_DONE, [], "hi")
    del snap["turnItems"]  # key absent entirely: messages fallback still works
    assert "".join(f.text for f in await _run([snap]) if isinstance(f, TextFrame)) == "hi"


@pytest.mark.asyncio
async def test_first_thinking_item_stops_the_working_cue(monkeypatch):
    monkeypatch.setattr("talky.backends.t3.CUE_DELAY", 0.05)
    monkeypatch.setattr("talky.backends.t3.CUE_EVERY", 0.05)

    class Slow(ItemsClient):
        async def thread_projection(self, thread_id):
            await asyncio.sleep(0.12 if self.polls == 0 else 0)
            return await super().thread_projection(thread_id)

    svc = T3LLMService(poll_interval=0)
    svc._client = Slow([_snap(_R1, [_reason("hm")]), _snap(_R1_DONE, [_reason("hm")], "ok")])  # type: ignore[assignment]
    frames = await _collect(svc)
    await svc._run_turn("hi")
    await asyncio.sleep(0.2)
    kinds = [a for a, _ in _agg(frames)]
    assert kinds[0] == "info" and "thinking" in kinds
    assert kinds.index("thinking") == kinds.index("info") + 1 or "info" not in kinds[kinds.index("thinking"):]


@pytest.mark.asyncio
async def test_tts_does_not_speak_thinking_or_tool_start_frames():
    """Real pipecat TTSService with the skip list the daemon uses: T3's frames pass through unspoken."""
    from pipecat.services.tts_service import TTSService

    src = (Path(__file__).parent.parent / "src/talky/server/voice_switcher.py").read_text()
    skip = re.search(r"skip_aggregator_types=(\[[^\]]*\])", src).group(1)  # the list create_tts_service_from_config gets
    skip = eval(skip)  # noqa: S307 — literal list from our own source

    spoken: list[str] = []

    class Tts(TTSService):
        async def run_tts(self, text, context_id):  # noqa: ANN001
            spoken.append(text)
            if False:
                yield

    tts = Tts(skip_aggregator_types=skip)
    pushed: list = []

    async def push(frame, direction=None):
        pushed.append(frame)

    tts.push_frame = push  # type: ignore[method-assign]
    frames = await _run([_snap(_R1, [_reason("Plan it."), _CMD]), _snap(_R1_DONE, [_reason("Plan it."), _CMD], "ok")])
    for f in frames:
        if isinstance(f, AggregatedTextFrame):
            await tts._push_tts_frames(f)
    assert spoken == [] and len(pushed) == 3


# --- WIN-605: the thread watcher speaks runs Talky did not start (timer/task wakeups, T3-app typing) ---


class WatchClient:
    """Fake T3 client: ``subscribe_thread`` yields whatever the test pushes; the projection is set by the test."""

    def __init__(self, projection):
        self.projection = projection
        self.pushes: asyncio.Queue = asyncio.Queue()
        self.interrupted = 0
        self.sent: list[str] = []

    async def subscribe_thread(self, thread_id):
        yield {"kind": "snapshot", "projection": self.projection}
        while True:
            yield await self.pushes.get()

    async def thread_projection(self, thread_id):
        return self.projection

    async def send_message(self, thread_id, text):
        self.sent.append(text)

    async def interrupt(self, thread_id):
        self.interrupted += 1

    async def close(self):
        pass


def _proj(*runs):
    """runs: (run id, status, assistant text)."""
    return {
        "runs": [{"id": r, "status": st} for r, st, _ in runs],
        "messages": [{"id": f"m-{r}", "role": "assistant", "runId": r, "text": t} for r, _, t in runs if t],
    }


async def _watching(projection):
    svc = T3LLMService(poll_interval=0, resume="t-voice")
    client = WatchClient(projection)
    svc._client = client  # type: ignore[assignment]
    frames = await _collect(svc)
    svc._watch_enabled = True
    svc._ensure_watch()
    await asyncio.sleep(0.25)  # first snapshot → baseline
    return svc, client, frames


async def _push(client, projection):
    client.projection = projection
    await client.pushes.put({"kind": "event", "sequence": 1, "event": {}})
    await asyncio.sleep(0.25)


def _texts(frames):
    return "".join(f.text for f in frames if isinstance(f, TextFrame))


@pytest.mark.asyncio
async def test_watcher_speaks_a_wakeup_run_in_full_and_not_history():
    svc, client, frames = await _watching(_proj(("r67", "completed", "old reply")))
    assert frames == []  # history is not spoken on attach
    await _push(client, _proj(("r67", "completed", "old reply"), ("r68", "running", "The scan")))
    await _push(client, _proj(("r67", "completed", "old reply"), ("r68", "completed", "The scan finished.")))
    assert _texts(frames) == "The scan finished."
    assert [type(f) for f in frames] == [LLMFullResponseStartFrame, TextFrame, TextFrame, LLMFullResponseEndFrame]
    await _push(client, _proj(("r67", "completed", "old reply"), ("r68", "completed", "The scan finished.")))
    assert len(frames) == 4  # nothing spoken twice
    svc._stop_watch()


@pytest.mark.asyncio
async def test_watcher_speaks_only_new_text_of_a_run_already_underway_at_attach():
    svc, client, frames = await _watching(_proj(("r1", "running", "Already said. ")))
    await _push(client, _proj(("r1", "completed", "Already said. And more.")))
    assert _texts(frames) == "And more."
    svc._stop_watch()


@pytest.mark.asyncio
async def test_barge_in_on_a_watcher_reply_mutes_it_without_interrupting_t3():
    from pipecat.frames.frames import InterruptionFrame

    svc, client, frames = await _watching(_proj())
    await _push(client, _proj(("r2", "running", "Long report part one")))
    await svc.process_frame(InterruptionFrame(), None)  # type: ignore[arg-type]
    await _push(client, _proj(("r2", "completed", "Long report part one, part two")))
    assert client.interrupted == 0  # T3's run is not Talky's to stop
    assert _texts(frames) == "Long report part one"
    assert isinstance([f for f in frames if not isinstance(f, InterruptionFrame)][-1], LLMFullResponseEndFrame)
    svc._stop_watch()


@pytest.mark.asyncio
async def test_watcher_defers_to_a_running_turn_and_never_double_speaks():
    svc, client, frames = await _watching(_proj(("r1", "completed", "hi")))
    gate = asyncio.Event()
    real = client.thread_projection
    turn_polls = 0

    async def gated(thread_id):
        nonlocal turn_polls
        if svc._turn_running():
            turn_polls += 1
            if turn_polls == 2:
                client.projection = _proj(("r1", "completed", "hi"), ("r2", "running", "Ans"))
                await client.pushes.put({"kind": "event"})  # the watcher sees it too, but must wait
                await asyncio.sleep(0.2)
            if turn_polls == 3:
                client.projection = _proj(("r1", "completed", "hi"), ("r2", "completed", "Answer."))
                gate.set()
        return await real(thread_id)

    client.thread_projection = gated  # type: ignore[method-assign]
    await svc.process_frame(UserTurnTextFrame(text="question"), None)  # type: ignore[arg-type]
    await gate.wait()
    while svc._turn_running():
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.3)
    assert _texts(frames) == "Answer."
    assert sum(isinstance(f, LLMFullResponseStartFrame) for f in frames) == 1
    svc._stop_watch()


@pytest.mark.asyncio
async def test_turn_dropped_before_start_surfaces_a_backend_error():
    """c088: a turn arriving while the service has no client must not vanish silently."""
    svc = T3LLMService(poll_interval=0)
    errors: list[str] = []

    async def record(msg):
        errors.append(msg)

    svc._emit_backend_error = record  # type: ignore[method-assign]
    await svc.process_frame(UserTurnTextFrame(text="hello"), None)  # type: ignore[arg-type]
    assert errors == ["T3 backend isn't running; turn dropped"]
