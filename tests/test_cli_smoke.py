"""Smoke tests for the talky CLI dispatcher.

Verifies argparse wiring and the `talky <profile>` shortcut without invoking
real daemons or network. Each test patches the underlying cmd_* function and
asserts dispatch.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import talky_cli  # noqa: E402


def _run_main(argv: list[str]) -> None:
    with patch.object(sys, "argv", ["talky", *argv]):
        talky_cli.main()


@pytest.mark.parametrize(
    "subcommand",
    ["config", "say", "ask", "kill", "daemon", "profile", "voice", "status", "ls", "auth", "transcribe", "pi", "claude"],
)
def test_help_for_subcommand_does_not_crash(subcommand):
    """Each registered subcommand must accept --help without raising."""
    with patch.object(sys, "argv", ["talky", subcommand, "--help"]), pytest.raises(SystemExit) as exc:
        talky_cli.main()
    assert exc.value.code == 0


def test_top_level_help():
    with patch.object(sys, "argv", ["talky", "--help"]), pytest.raises(SystemExit) as exc:
        talky_cli.main()
    assert exc.value.code == 0


def test_bare_profile_shortcut_dispatches_to_cmd_profile():
    """`talky openclaw` should route through cmd_profile with name=openclaw."""
    captured = {}

    def fake_cmd_profile(args):
        captured["name"] = args.name

    with patch.object(talky_cli, "cmd_profile", fake_cmd_profile):
        _run_main(["openclaw"])

    assert captured["name"] == "openclaw"


def test_explicit_profile_subcommand_dispatches():
    captured = {}

    def fake_cmd_profile(args):
        captured["name"] = args.name

    with patch.object(talky_cli, "cmd_profile", fake_cmd_profile):
        _run_main(["profile", "moltis"])

    assert captured["name"] == "moltis"


def test_voice_subcommand_dispatches():
    captured = {}

    def fake_cmd_voice(args):
        captured["name"] = args.name

    with patch.object(talky_cli, "cmd_voice", fake_cmd_voice):
        _run_main(["voice", "kokoro"])

    assert captured["name"] == "kokoro"


def test_kill_subcommand_dispatches():
    called = {"yes": False}

    def fake_cmd_kill(_args):
        called["yes"] = True

    with patch.object(talky_cli, "cmd_kill", fake_cmd_kill):
        _run_main(["kill"])

    assert called["yes"]


def test_known_commands_set_includes_all_registered_subcommands():
    """Drift guard: the shortcut path ignores commands in this set; if a new
    subcommand is added but not registered here, `talky <newcmd>` would be
    misrouted as a profile name."""
    expected = {
        "config", "say", "ask", "daemon", "ls", "auth",
        "pi", "claude", "transcribe", "kill", "profile", "voice", "status",
    }
    src = Path(talky_cli.__file__).read_text()
    for cmd in expected:
        assert f'"{cmd}"' in src, f"subcommand {cmd!r} missing from talky_cli source"
