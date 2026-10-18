"""claude / hermes / opencode emit ``backendError`` when a turn fails (WIN-549)."""

import asyncio

import pytest

from talky.server.event_bus import event_bus


async def _errors(q):
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "backendError":
            out.append(ev.data)
    return out


@pytest.mark.asyncio
async def test_opencode_session_error_emits_backend_error():
    from talky.backends.opencode import OpencodeLLMService

    svc = OpencodeLLMService.__new__(OpencodeLLMService)
    svc._session_id = "s1"
    svc._turn_started = False
    async with event_bus.subscribe() as q:
        await svc._dispatch({
            "type": "session.error",
            "properties": {"sessionID": "s1", "error": {"name": "APIError", "data": {"message": "key expired"}}},
        })
        errs = await _errors(q)
    assert errs == [{"profile": "opencode", "message": "key expired"}]


@pytest.mark.asyncio
async def test_claude_error_frame_emits_backend_error():
    from talky.backends.claude_code import ClaudeCodeLLMService

    svc = ClaudeCodeLLMService.__new__(ClaudeCodeLLMService)
    svc._frame_queue = asyncio.Queue()
    pushed = []

    async def push_frame(f, *a, **k):
        pushed.append(f)

    svc.push_frame = push_frame
    svc._frame_queue.put_nowait(("error", "invalid model"))
    async with event_bus.subscribe() as q:
        task = asyncio.create_task(svc._drain_frames())
        await asyncio.sleep(0.05)
        task.cancel()
        errs = await _errors(q)
    assert errs == [{"profile": "claude", "message": "invalid model"}]
