"""Voice Provider Status — STT/TTS equivalent of LLM Backend Status (ticket 4b1c).

8fbe wired a static ``status()`` contract on each LLM Backend Adapter so the
pipeline build loop could skip non-Ready backends and the picker could
surface a reason. Voice providers (STT/TTS) needed the same treatment but
couldn't bolt ``status()`` onto pipecat's third-party service classes.

This module gives them an equivalent via a config-driven check:

  1. Is the configured ``service_class`` importable?    (Installable if not)
  2. If ``requires_credentials``, are the creds present? (Misconfigured if not)
  3. Otherwise: Ready.

Adjacent helpers:

- ``get_voice_provider_extra(kind, provider)`` — read the ``extra:`` field
  from a voice-backends.yaml entry, with a fallback to the legacy
  ``PROVIDER_TO_EXTRA`` map in ``dependency_installer``. Drives the
  on-demand install path during voice profile switch.
- ``all_voice_provider_status()`` — sweep every provider referenced by any
  configured voice profile. Result shape mirrors ``backendStatus`` so the
  SSE init payload carries them in parallel.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Optional

from talky.backends import BackendStatus

_VALID_KINDS = ("stt", "tts")


def _entry(kind: str, provider: str) -> Optional[dict]:
    if kind not in _VALID_KINDS:
        return None
    from talky.shared.profile_manager import get_profile_manager

    pm = get_profile_manager()
    return pm.get_voice_backend_config(kind, provider)


def _credentials_present(entry: dict, provider: str) -> bool:
    if not entry.get("requires_credentials"):
        return True
    cred_type = entry.get("credential_type", provider)
    cred_path = Path.home() / ".talky" / "credentials" / f"{cred_type}.json"
    if not cred_path.exists():
        return False
    try:
        import json

        with open(cred_path) as f:
            data = json.load(f)
        return bool(data)
    except Exception:  # noqa: BLE001
        return False


def voice_provider_status(kind: str, provider: str) -> tuple[BackendStatus, str]:
    """Return (BackendStatus, reason) for a configured STT/TTS provider.

    Pure config/install check — no network. Mirrors ``LLMBackend.status()``
    semantics from 8fbe.
    """
    entry = _entry(kind, provider)
    if entry is None:
        return BackendStatus.BLOCKED, f"{kind} provider {provider!r} not configured in voice-backends.yaml"

    service_class = entry.get("service_class")
    if not service_class:
        return BackendStatus.BLOCKED, f"{kind} provider {provider!r} missing service_class"

    module_path = ".".join(service_class.split(".")[:-1])
    class_name = service_class.split(".")[-1]
    try:
        module = importlib.import_module(module_path)
        getattr(module, class_name)
    except ModuleNotFoundError as e:
        # Importable failure is the on-demand install hook.
        extra = get_voice_provider_extra(kind, provider)
        if extra:
            return BackendStatus.INSTALLABLE, f"missing dep: {e.name}. Install extra: {extra}"
        return BackendStatus.INSTALLABLE, f"missing dep: {e.name}"
    except Exception as e:  # noqa: BLE001
        return BackendStatus.BLOCKED, f"service_class import failed: {e}"

    if not _credentials_present(entry, provider):
        cred_type = entry.get("credential_type", provider)
        return (
            BackendStatus.MISCONFIGURED,
            f"credentials missing — add ~/.talky/credentials/{cred_type}.json",
        )

    return BackendStatus.READY, ""


def get_voice_provider_extra(kind: str, provider: str) -> Optional[str]:
    """Return the pyproject.toml extra name for a voice provider, or None.

    Source order:
      1. ``extra:`` field on the voice-backends.yaml entry (explicit, wins).
      2. ``PROVIDER_TO_EXTRA`` map in ``dependency_installer`` (legacy
         catch-all by bare provider name — kept so existing entries keep
         working without re-yaml-ing).
    """
    entry = _entry(kind, provider)
    if entry and entry.get("extra"):
        return str(entry["extra"])

    try:
        from talky.shared.dependency_installer import PROVIDER_TO_EXTRA

        return PROVIDER_TO_EXTRA.get(provider)
    except Exception:  # noqa: BLE001
        return None


def all_voice_provider_status() -> dict[str, dict]:
    """Sweep every provider referenced by any configured voice profile.

    Returns ``{"<kind>:<provider>": {"status": str, "reason": str}}`` so the
    SSE init payload can carry the union in parallel with ``backendStatus``.
    """
    out: dict[str, dict] = {}
    try:
        from talky.shared.profile_manager import get_profile_manager

        pm = get_profile_manager()
    except Exception:  # noqa: BLE001
        return out

    seen: set[tuple[str, str]] = set()
    for name in pm.list_voice_profiles():
        vp = pm.get_voice_profile(name)
        if vp is None:
            continue
        for kind, provider in (("tts", vp.tts_provider), ("stt", vp.stt_provider)):
            if not provider:
                continue
            key = (kind, provider)
            if key in seen:
                continue
            seen.add(key)
            bs, reason = voice_provider_status(kind, provider)
            out[f"{kind}:{provider}"] = {"status": bs.value, "reason": reason}
    return out


def voice_provider_is_ready(kind: str, provider: str) -> bool:
    bs, _ = voice_provider_status(kind, provider)
    return bs is BackendStatus.READY


__all__ = [
    "voice_provider_status",
    "get_voice_provider_extra",
    "all_voice_provider_status",
    "voice_provider_is_ready",
]


# Convenience: avoid importing Any from typing twice for callers.
_ = Any  # type: ignore[unused-ignore]
