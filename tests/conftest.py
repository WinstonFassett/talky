"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_convo_store(tmp_path, monkeypatch):
    """Never read or write the real ~/.talky/convos.

    ``VoiceChannel()`` scans the default convo store at construction (to find a
    turn a previous daemon died in) and may clear stale markers there (WIN-536).
    """
    monkeypatch.setenv("TALKY_CONVOS_DIR", str(tmp_path / "convos"))


@pytest.fixture(autouse=True)
def _isolated_profile_state(tmp_path, monkeypatch):
    """Never read or write the real ~/.talky/state.json (the persisted Talky profile, WIN-579)."""
    from talky.server.channel import VoiceChannel

    monkeypatch.setattr(VoiceChannel, "_state_path", staticmethod(lambda: tmp_path / "state.json"))
