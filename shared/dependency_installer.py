#!/usr/bin/env python3
"""Dynamic dependency installer for talky CLI.

Installs provider dependencies on-demand via pipecat-ai extras.
In a uv tool environment, uses `uv tool install --reinstall --with`
then re-execs the process so new packages are immediately available.
"""

import functools
import importlib.metadata
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Set

import yaml
from loguru import logger

_root = Path(__file__).parent.parent

# Keep HuggingFace cache in home dir even inside isolated tool envs
os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(Path.home() / ".cache" / "huggingface" / "hub"))

# Maps talky provider name → pipecat-ai extra name.
# Covers every provider in pipecat/services/ that requires credentials.
PROVIDER_EXTRA: dict[str, str] = {
    "assemblyai":    "assemblyai",
    "asyncai":       "asyncai",
    "aws":           "aws",
    "azure":         "azure",
    "camb":          "camb",
    "cartesia":      "cartesia",
    "deepgram":      "deepgram",
    "elevenlabs":    "elevenlabs",
    "fal":           "fal",
    "fish":          "fish",
    "gladia":        "gladia",
    "google":        "google",
    "gradium":       "gradium",
    "groq":          "groq",
    "hume":          "hume",
    "inworld":       "inworld",
    "kokoro":        "kokoro",
    "lmnt":          "lmnt",
    "neuphonic":     "neuphonic",
    "nvidia":        "nvidia",
    "openai":        "openai",
    "playht":        "playht",
    "resembleai":    "resembleai",
    "rime":          "rime",
    "sambanova":     "sambanova",
    "sarvam":        "sarvam",
    "soniox":        "soniox",
    "speechmatics":  "speechmatics",
    "whisper_local": "mlx-whisper",
}


def _is_tool_env() -> bool:
    return ".local/share/uv/tools/" in sys.executable


@functools.lru_cache(maxsize=None)
def _extra_dist_names(extra: str) -> list[str]:
    """Return distribution names pulled in by a pipecat-ai extra.

    Reads pipecat-ai's own package metadata so we never hard-code
    which SDK each extra installs.
    """
    try:
        dist = importlib.metadata.distribution("pipecat-ai")
        names: list[str] = []
        for req in dist.requires or []:
            if f'extra == "{extra}"' not in req and f"extra == '{extra}'" not in req:
                continue
            # Take everything before the environment marker
            pkg_part = req.split(";")[0].strip()
            # Extract bare dist name: stop at any version specifier or extras bracket
            name = re.split(r"[><=!~\[,\s]", pkg_part)[0].strip()
            # Skip self-references
            if name and name.lower() not in ("pipecat-ai", "pipecat_ai"):
                names.append(name)
        return names
    except Exception:
        return []


def _check_extra_installed(extra: str) -> bool:
    """Return True if every package required by a pipecat-ai extra is present.

    An empty dist list means the extra has no external SDK deps (it relies only
    on pipecat-ai's own packages which are already installed), so we return True.
    """
    dists = _extra_dist_names(extra)
    if not dists:
        return True  # no external packages needed
    for name in dists:
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            return False
    return True


def get_configured_providers() -> Set[str]:
    """Read ~/.talky config to find which providers the default profile needs."""
    config_dir = Path.home() / ".talky"
    providers: Set[str] = set()

    settings_file = config_dir / "settings.yaml"
    default_voice_profile = "cloud"
    if settings_file.exists():
        try:
            with open(settings_file) as f:
                settings = yaml.safe_load(f) or {}
                default_voice_profile = (
                    settings.get("defaults", {}).get("voice_profile", default_voice_profile)
                )
        except Exception as e:
            logger.warning(f"Failed to load settings: {e}")

    bundled_defaults = _root / "server" / "config" / "defaults"
    voice_profiles_file = config_dir / "voice-profiles.yaml"
    if not voice_profiles_file.exists():
        voice_profiles_file = bundled_defaults / "voice-profiles.yaml"

    if not voice_profiles_file.exists():
        return providers

    try:
        with open(voice_profiles_file) as f:
            profiles = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load voice profiles: {e}")
        return providers

    profile = profiles.get("voice_profiles", {}).get(default_voice_profile, {})
    for key in ("tts_provider", "stt_provider"):
        if val := profile.get(key):
            providers.add(val)

    return providers


def _missing_extras(providers: Set[str]) -> list[str]:
    """Return pipecat extra names that are not yet installed."""
    return [
        extra
        for provider in providers
        if (extra := PROVIDER_EXTRA.get(provider)) and not _check_extra_installed(extra)
    ]


def _uv_cmd() -> str | None:
    import shutil
    return shutil.which("uv")


def install_dependencies(providers: Set[str]) -> bool:
    """Install missing pipecat extras for the given providers.

    In a uv tool environment: runs `uv tool install --reinstall --with`
    then re-execs the process so the new packages are loaded.

    In a regular venv: runs `uv pip install`.
    """
    missing = _missing_extras(providers)
    if not missing:
        return True

    uv = _uv_cmd()
    if not uv:
        logger.error("uv not found — cannot install dependencies")
        return False

    packages = [f"pipecat-ai[{e}]" for e in missing]
    print(f"Installing {', '.join(packages)}...")

    if _is_tool_env():
        result = subprocess.run(
            [uv, "tool", "install", "--editable", str(_root), "--reinstall"]
            + [f"--with={pkg}" for pkg in packages]
        )
        if result.returncode != 0:
            print("❌ Install failed")
            return False
        print("Restarting...")
        os.execv(sys.argv[0], sys.argv)  # does not return

    # Non-tool env (development / direct uv run)
    result = subprocess.run([uv, "pip", "install"] + packages, capture_output=True, text=True)
    if result.returncode != 0 and "No virtual environment" in result.stderr:
        result = subprocess.run(
            [uv, "pip", "install", "--user"] + packages, capture_output=True, text=True
        )
    if result.returncode != 0:
        logger.error(f"Install failed: {result.stderr}")
        return False
    return True


def ensure_dependencies_for_server(server_dir: Path) -> bool:
    """Install missing provider dependencies into the server's .venv."""
    try:
        providers = get_configured_providers()
        missing = _missing_extras(providers)
        if not missing:
            return True

        uv = _uv_cmd()
        if not uv:
            logger.error("uv not found")
            return False

        packages = [f"pipecat-ai[{e}]" for e in missing]
        logger.info(f"Installing into server venv: {', '.join(packages)}")

        python_path = server_dir.parent / ".venv" / "bin" / "python"
        if not python_path.exists():
            logger.error(f"No .venv at {python_path}; run `uv venv`")
            return False

        result = subprocess.run(
            [uv, "pip", "install", "--python", str(python_path)] + packages,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"Install failed: {result.stderr}")
            return False
        return True

    except Exception as e:
        logger.error(f"Failed to ensure server dependencies: {e}")
        return False


def ensure_dependencies() -> bool:
    """Ensure dependencies for the configured providers (current env)."""
    try:
        return install_dependencies(get_configured_providers())
    except Exception as e:
        logger.error(f"Failed to ensure dependencies: {e}")
        return False


if __name__ == "__main__":
    if ensure_dependencies():
        print("✅ Dependencies ready")
        sys.exit(0)
    else:
        print("❌ Failed to install dependencies")
        sys.exit(1)
