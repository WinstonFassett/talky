"""WIN-584: New chat must start a new native session on Claude and Hermes too (T3 was WIN-583).

Real code under test: VoiceChannel.new_convo, ClaudeCodeLLMService + _ClaudeSDKThread, HermesLLMService +
_HermesThread. Faked: the Claude Agent SDK (``query`` and message classes; the SDK is not installed in the
test venv) and Hermes' ``AIAgent`` (no model call). The fakes record which session each turn ran in.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from talky.convos.session import ConvoSession, native_session_id_of


@pytest.fixture
def channel(tmp_path, monkeypatch):
    monkeypatch.setenv("TALKY_CONVOS_DIR", str(tmp_path))

    class _TP:
        def __init__(self, backend):
            self.llm_backend = backend

    class _PM:
        def get_talky_profile(self, name):
            return _TP({"claude": "claude-code"}.get(name, name))

    monkeypatch.setattr("talky.shared.profile_manager.get_profile_manager", lambda: _PM())
    from talky.server.channel import VoiceChannel

    return VoiceChannel()


def _bind(channel, svc, backend, old_id):
    channel._llm_services = {backend: svc}
    rec = channel._convo_store.create(backend=backend, profile=backend, native_session_id=old_id)
    session = ConvoSession(
        channel._convo_store, backend=backend, profile=backend, resume_into=rec.id,
        native_id_reader=lambda: native_session_id_of(svc),
    )
    channel._convo_session = session
    return rec, session


async def _wait_turns(q: asyncio.Queue, n: int):
    seen = 0
    while seen < n:
        item = await asyncio.wait_for(q.get(), 5)
        if item and item[0] == "end":
            seen += 1


def test_claude_new_chat_starts_a_new_session(channel, monkeypatch):
    from talky.backends import claude_code as cc

    class _Msg:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Opts(_Msg):
        resume = None

    resumes: list = []

    def fake_query(prompt, options):
        resumes.append(options.resume)
        sid = options.resume or f"claude-new-{len(resumes)}"

        async def gen():
            yield cc.SystemMessage(subtype="init", data={"session_id": sid})
        return gen()

    for name in ("AssistantMessage", "ResultMessage", "SystemMessage", "TextBlock", "ThinkingBlock", "ToolUseBlock"):
        monkeypatch.setattr(cc, name, type(name, (_Msg,), {}), raising=False)
    monkeypatch.setattr(cc, "ClaudeAgentOptions", _Opts, raising=False)
    monkeypatch.setattr(cc, "query", fake_query, raising=False)
    monkeypatch.setattr(cc, "_SDK_AVAILABLE", True)

    async def go():
        loop = asyncio.get_running_loop()
        svc = cc.ClaudeCodeLLMService(resume="claude-old")
        q: asyncio.Queue = asyncio.Queue()
        svc._bridge = cc._ClaudeSDKThread(svc._build_options(), loop, q)
        svc._resume = None  # as start() does
        svc._bridge.start()
        rec, session = _bind(channel, svc, "claude-code", "claude-old")
        try:
            svc._bridge.send_prompt("one")
            await _wait_turns(q, 1)
            assert svc.session_id == "claude-old"
            channel.new_convo()
            svc._bridge.send_prompt("two")
            await _wait_turns(q, 1)
            session.append("user", "two")
        finally:
            svc._bridge.stop()
        return svc, rec, session

    svc, rec, session = asyncio.run(go())
    assert resumes == ["claude-old", None]  # the new chat did NOT resume the old session
    assert svc.session_id == "claude-new-2"
    assert session.record.id != rec.id and session.record.native_session_id == "claude-new-2"
    assert channel._convo_store.get(rec.id).native_session_id == "claude-old"  # original Chat keeps its link


def test_hermes_new_chat_starts_a_new_session(channel, monkeypatch):
    from talky.backends import hermes as hm

    built: list = []
    runs: list = []

    class FakeAgent:
        def __init__(self, **kw):
            self.session_id = kw.get("session_id") or f"hermes-new-{len(built) + 1}"
            built.append(self.session_id)

        def clear_interrupt(self): ...
        def interrupt(self): ...
        def steer(self, text): ...

        def run_conversation(self, prompt, conversation_history=None):
            runs.append((self.session_id, len(conversation_history or [])))
            return {"messages": list(conversation_history or []) + [{"role": "user", "content": prompt}]}

    monkeypatch.setattr(hm, "AIAgent", FakeAgent)
    monkeypatch.setattr(hm.HermesLLMService, "_hermes_defaults", staticmethod(lambda: ("", "")))
    monkeypatch.setitem(sys.modules, "hermes_state_registry", types.SimpleNamespace(acquire=lambda: None))
    monkeypatch.setitem(sys.modules, "hermes_state", types.SimpleNamespace(
        SessionDB=lambda: types.SimpleNamespace(get_messages_as_conversation=lambda sid: [{"role": "user", "content": "old"}])))

    async def go():
        loop = asyncio.get_running_loop()
        svc = hm.HermesLLMService(resume="hermes-old")
        svc._loop = loop
        svc._bridge = hm._HermesThread(svc._build_agent(), loop, svc._frame_queue)
        svc._bridge._resume_session_id = svc._resume
        svc._bridge.start()
        rec, session = _bind(channel, svc, "hermes", "hermes-old")
        try:
            svc._bridge.send_prompt("one")
            await _wait_turns(svc._frame_queue, 1)
            assert svc.session_id == "hermes-old"
            channel.new_convo()
            svc._bridge.send_prompt("two")
            await _wait_turns(svc._frame_queue, 1)
            session.append("user", "two")
        finally:
            svc._bridge.stop()
        return svc, rec, session

    svc, rec, session = asyncio.run(go())
    assert runs == [("hermes-old", 1), ("hermes-new-2", 0)]  # new session AND no carried-over history
    assert svc.session_id == "hermes-new-2"
    assert session.record.id != rec.id and session.record.native_session_id == "hermes-new-2"
    assert channel._convo_store.get(rec.id).native_session_id == "hermes-old"
