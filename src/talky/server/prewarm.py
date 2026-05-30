"""Generic pre-warm step: download any HuggingFace-backed assets for the
active voice profile before the user can connect.

This exists so the daemon doesn't return "ready" while a 1.5GB whisper
model silently downloads inside a live session. Generic by construction:
any voice backend that declares ``default_model`` in voice-backends.yaml
gets pre-warmed if the model looks HF-shaped (``org/name``) AND the
backend isn't credential-gated (credential-gated providers use remote
APIs, not local model files).

If huggingface_hub isn't installed (because the user is on a fully
remote profile like deepgram+elevenlabs), this module no-ops gracefully.
"""

from __future__ import annotations

import re
from typing import Optional

from loguru import logger

from talky.server.readiness import readiness

_HF_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _looks_like_hf_repo(model: Optional[str]) -> bool:
    if not model:
        return False
    return bool(_HF_REPO_RE.match(model))


def _collect_hf_models() -> list[tuple[str, str]]:
    """Return ``[(label, repo_id), ...]`` for HF-shaped models in the
    active voice profile. Label is human-readable for progress display.
    """
    from talky.shared.profile_manager import get_profile_manager

    pm = get_profile_manager()
    profile_name = pm.get_default_voice_profile()
    vp = pm.get_voice_profile(profile_name) if profile_name else None
    if vp is None:
        return []

    out: list[tuple[str, str]] = []
    for kind, provider, model in (
        ("STT", vp.stt_provider, vp.stt_model),
        ("TTS", vp.tts_provider, vp.tts_voice),  # voice isn't a model, but provider might declare one
    ):
        if not provider:
            continue
        backend = pm.get_voice_backend_config(kind.lower(), provider) or {}
        # Skip credential-gated providers — they use remote APIs.
        if backend.get("requires_credentials"):
            continue
        # Prefer profile-level model override, fall back to backend default.
        repo = model or backend.get("default_model") or ""
        if _looks_like_hf_repo(repo):
            out.append((f"{kind} model {repo}", repo))
    return out


def prewarm_hf_assets() -> None:
    """Pre-download HF assets for the active voice profile, reporting
    progress through the readiness tracker. Safe to call multiple times —
    huggingface_hub uses an on-disk cache and skips already-present files.

    Sync function (subprocess-style HF download). Intended to be run
    from a thread or from sync startup code; called from the async
    lifespan via ``asyncio.to_thread``.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        # No HF stack installed — user is on a fully remote profile. No-op.
        return

    models = _collect_hf_models()
    if not models:
        return

    for label, repo_id in models:
        with readiness.track(f"Downloading {label}") as t:
            t.progress(msg="starting")
            try:
                snapshot_download(
                    repo_id=repo_id,
                    # tqdm output still goes to stderr/log — readiness gives the
                    # structured signal. Keep tqdm for log-file debuggability.
                )
                t.progress(msg="done")
            except Exception as e:  # noqa: BLE001
                # Don't fail startup — log and continue. The user will see
                # the failed task in the readiness stream and can act on it.
                logger.error(f"Pre-warm failed for {repo_id}: {e}")
                # Re-raise so the tracker marks the task !ok.
                raise
