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
