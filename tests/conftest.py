"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_convo_store(tmp_path, monkeypatch):
    """Never read or write the real ~/.talky/convos.

    ``VoiceChannel()`` scans the default convo store at construction (to find a
    turn a previous daemon died in) and may clear stale markers there (WIN-536).
    """
    monkeypatch.setenv("TALKY_CONVOS_DIR", str(tmp_path / "convos"))
