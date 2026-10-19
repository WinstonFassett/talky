"""T3LLMService turn logic against a fake T3 client (no T3 server needed)."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, TextFrame

from talky.backends.t3 import T3LLMService


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
