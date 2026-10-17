"""WIN-533: PiDurableLLMService — session file survives process death and resumes.

Two tiers:
  1. Unit: _build_cmd emits --session-id / --session-dir, set_resume mutates id,
     set_resume ignores a falsy arg (one-shot semantics).
  2. Integration (opt-in via TALKY_TEST_PI_DURABLE=1): spawn pi --mode rpc twice
     against the same session file; prove the second process answers from the
     first's history. This is the ticket's "survives daemon restart" check at
     the backend level, with no voice daemon in the loop.
"""

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import pytest

from talky.backends.pi_durable import PiDurableLLMService, list_convos


def test_build_cmd_wires_session_flags():
    s = PiDurableLLMService()
    cmd = s._build_cmd()
    assert cmd[:3] == ["pi", "--mode", "rpc"]
    assert "--session-id" in cmd
    assert "--session-dir" in cmd
    sid = cmd[cmd.index("--session-id") + 1]
    assert uuid.UUID(sid)  # it's a real uuid


def test_build_cmd_passes_provider_and_model():
    s = PiDurableLLMService(provider="anthropic", model="claude-haiku-4-5", resume="abc123")
    cmd = s._build_cmd()
    assert "abc123" in cmd
    assert cmd[cmd.index("--provider") + 1] == "anthropic"
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-5"


def test_set_resume_switches_id_and_ignores_falsy():
    s = PiDurableLLMService(resume="original")
    assert s.session_id == "original"
    s.set_resume("switched")
    assert s.session_id == "switched"
    s.set_resume(None)  # no-op
    assert s.session_id == "switched"
    s.set_resume("")  # no-op (one-shot semantics match claude_code)
    assert s.session_id == "switched"


def test_custom_session_dir_is_honored(tmp_path: Path):
    s = PiDurableLLMService(session_dir=str(tmp_path))
    assert s._session_dir == tmp_path
    assert str(tmp_path) in s._build_cmd()


def test_list_convos_scans_session_dir(tmp_path: Path, monkeypatch):
    # Point the module-level dir at a scratch location, drop two fake session files.
    monkeypatch.setattr("talky.backends.pi_durable.TALKY_CONVOS_DIR", tmp_path)
    older = tmp_path / "2026-01-01T00-00-00-000Z_alpha-session.jsonl"
    newer = tmp_path / "2026-02-01T00-00-00-000Z_beta-session.jsonl"
    older.write_text("{}\n")
    newer.write_text("{}\n")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))
    rows = list_convos()
    assert [r["id"] for r in rows] == ["beta-session", "alpha-session"]


# --- Integration: real pi subprocess roundtrip ------------------------------

PI_AVAILABLE = subprocess.run(
    ["which", "pi"], capture_output=True, text=True
).returncode == 0
REQUESTED = os.environ.get("TALKY_TEST_PI_DURABLE") == "1"


@pytest.mark.skipif(
    not (PI_AVAILABLE and REQUESTED),
    reason="opt-in: set TALKY_TEST_PI_DURABLE=1 with pi on PATH",
)
def test_pi_session_survives_process_death(tmp_path: Path):
    """Two separate pi processes, same --session-id, same --session-dir:
    the second must know a fact only the first was told. This is the WIN-533
    durability promise at the pi layer."""
    sess = f"win533-{uuid.uuid4()}"
    sd = str(tmp_path)

    def _run(prompt: str) -> str:
        # Hold stdin open past the response — pi exits on EOF before the model
        # finishes if we close stdin immediately. Read stdout until agent_end,
        # then close.
        p = subprocess.Popen(
            ["pi", "--mode", "rpc", "--session-id", sess, "--session-dir", sd],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )
        assert p.stdin and p.stdout
        p.stdin.write(json.dumps({"type": "prompt", "message": prompt}) + "\n")
        p.stdin.flush()
        deltas: list[str] = []
        try:
            for line in p.stdout:
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type")
                if etype == "message_update":
                    am = evt.get("assistantMessageEvent", {})
                    if am.get("type") == "text_delta":
                        deltas.append(am.get("delta", ""))
                elif etype == "agent_end":
                    break
        finally:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        return "".join(deltas)

    _run("Remember this: my favorite animal is a capybara. Just acknowledge.")
    reply = _run("What animal did I just name? One word.").lower()
    assert "capybara" in reply, f"second pi process lost the first's context: {reply!r}"


@pytest.mark.skipif(
    not (PI_AVAILABLE and REQUESTED),
    reason="opt-in: set TALKY_TEST_PI_DURABLE=1 with pi on PATH",
)
def test_resume_with_different_model_preserves_history(tmp_path: Path):
    """The 'resume with a different brain' half of WIN-533. Same --session-id,
    second process on a different model, still knows the first process's fact."""
    sess = f"win533-brain-{uuid.uuid4()}"
    sd = str(tmp_path)

    def _run(prompt: str, model: Optional[str] = None, provider: Optional[str] = None) -> str:
        argv = ["pi", "--mode", "rpc", "--session-id", sess, "--session-dir", sd]
        if provider:
            argv += ["--provider", provider]
        if model:
            argv += ["--model", model]
        p = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
        assert p.stdin and p.stdout
        p.stdin.write(json.dumps({"type": "prompt", "message": prompt}) + "\n")
        p.stdin.flush()
        deltas: list[str] = []
        try:
            for line in p.stdout:
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type")
                if etype == "message_update":
                    am = evt.get("assistantMessageEvent", {})
                    if am.get("type") == "text_delta":
                        deltas.append(am.get("delta", ""))
                elif etype == "agent_end":
                    break
        finally:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        return "".join(deltas)

    _run("The secret word is kumquat. Just acknowledge.")
    # Resume with a different model (pi's default swap target). Haiku vs. Sonnet
    # — anything different from the first run's default is enough to prove the
    # history survives independent of model choice.
    reply = _run("What was the secret word? One word.",
                 provider="claude-bridge", model="claude-haiku-4-5").lower()
    assert "kumquat" in reply, f"different-model resume lost first run's context: {reply!r}"
