"""talky auth — keyboard-driven TUI for managing provider credentials."""

from __future__ import annotations

import json
from pathlib import Path

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator

CREDS_DIR = Path.home() / ".talky" / "credentials"

PROVIDERS: list[dict] = [
    {"name": "cartesia",    "type": "TTS",     "file": "cartesia.json",    "field": "api_key"},
    {"name": "elevenlabs",  "type": "TTS",     "file": "elevenlabs.json",  "field": "api_key"},
    {"name": "deepgram",    "type": "STT",     "file": "deepgram.json",    "field": "api_key"},
    {"name": "assemblyai",  "type": "STT",     "file": "assemblyai.json",  "field": "api_key"},
    {"name": "google",      "type": "TTS+STT", "file": "google.json",      "field": "credentials_path"},
]


def _read_cred(provider: dict) -> str | None:
    path = CREDS_DIR / provider["file"]
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data.get(provider["field"])
    except Exception:
        return None


def _write_cred(provider: dict, value: str) -> None:
    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    path = CREDS_DIR / provider["file"]
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            pass
    data[provider["field"]] = value
    path.write_text(json.dumps(data, indent=2) + "\n")


def _delete_cred(provider: dict) -> None:
    path = CREDS_DIR / provider["file"]
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        data.pop(provider["field"], None)
        if data:
            path.write_text(json.dumps(data, indent=2) + "\n")
        else:
            path.unlink()
    except Exception:
        pass


def _mask(value: str) -> str:
    """Return a masked preview: first 8 chars + ••••••"""
    if len(value) <= 8:
        return value[:2] + "••••••"
    return value[:8] + "••••••"


def _status(provider: dict) -> str:
    value = _read_cred(provider)
    if value:
        return f"✓  {_mask(value)}"
    return "✗  not set"


def _provider_label(p: dict) -> str:
    return f"{p['name']:<12} {p['type']:<8} {_status(p)}"


def _handle_provider(provider: dict) -> None:
    value = _read_cred(provider)
    field = provider["field"]
    name = provider["name"]

    if value:
        action = inquirer.select(
            message=f"{name} / {field}:  (currently set)",
            choices=["Edit", "Delete", "Back"],
        ).execute()
    else:
        action = inquirer.select(
            message=f"{name} / {field}:  (not set)",
            choices=["Set", "Back"],
        ).execute()

    if action in ("Edit", "Set"):
        new_value = inquirer.secret(
            message=f"New value for {name} {field}:",
        ).execute()
        if new_value and new_value.strip():
            _write_cred(provider, new_value.strip())
            print(f"  ✓ Saved {name} {field}")
    elif action == "Delete":
        confirmed = inquirer.confirm(
            message=f"Delete {name} {field}?",
            default=False,
        ).execute()
        if confirmed:
            _delete_cred(provider)
            print(f"  ✓ Deleted {name} {field}")


def run_auth_tui() -> None:
    print("\n  talky credentials\n")
    while True:
        choices = [Choice(p, _provider_label(p)) for p in PROVIDERS]
        choices += [Separator(), Choice(None, "done")]

        provider = inquirer.select(
            message="Select provider:",
            choices=choices,
        ).execute()

        if provider is None:
            break

        _handle_provider(provider)
        print()
