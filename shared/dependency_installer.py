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

# Check Python version compatibility
def _check_python_version() -> bool:
    """Return True if Python version is compatible with pipecat-ai dependencies."""
    # Handle both tuple and named tuple versions of sys.version_info
    if isinstance(sys.version_info, tuple):
        major, minor = sys.version_info[:2]
    else:
        major, minor = sys.version_info.major, sys.version_info.minor
    
    if (major, minor) >= (3, 14):
        logger.error(
            f"Python {major}.{minor} is not supported. "
            "onnxruntime and other dependencies require Python < 3.14. "
            "Please use Python 3.10, 3.11, 3.12, or 3.13."
        )
        return False
    elif (major, minor) < (3, 10):
        logger.error(
            f"Python {major}.{minor} is too old. "
            "Please use Python 3.10 or newer."
        )
        return False
    return True

# Check Python version early
if not _check_python_version():
    sys.exit(1)

# Keep HuggingFace cache in home dir even inside isolated tool envs
os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(Path.home() / ".cache" / "huggingface" / "hub"))

# Maps talky provider name → pyproject.toml extra name.
# Covers every provider in pipecat/services/ that requires credentials.
PROVIDER_TO_EXTRA: dict[str, str] = {
    # TTS providers
    "assemblyai":    "tts-openai",
    "asyncai":       "tts-openai", 
    "aws":           "aws",
    "azure":         "azure",
    "camb":          "tts-openai",
    "cartesia":      "tts-cartesia",
    "deepgram":      "stt-deepgram",
    "elevenlabs":    "tts-elevenlabs",
    "fal":           "fal",
    "fish":          "tts-fish",
    "gladia":        "stt-gladia",
    "google":        "stt-google",
    "gradium":       "tts-gradium",
    "groq":          "groq",
    "hume":          "tts-hume",
    "inworld":       "stt-inworld",
    "kokoro":        "tts-kokoro",
    "lmnt":          "tts-lmnt",
    "neuphonic":     "tts-neuphonic",
    "nvidia":        "tts-nvidia",
    "openai":        "tts-openai",
    "playht":        "tts-playht",
    "resembleai":    "tts-resembleai",
    "rime":          "tts-rime",
    "sambanova":     "tts-sambanova",
    "sarvam":        "tts-sarvam",
    "soniox":        "stt-soniox",
    "speechmatics":  "stt-speechmatics",
    "whisper_local": "stt-whisper-local",
    
    # Local audio (not a pipecat provider)
    "local_audio":   "audio",
}


def _is_tool_env() -> bool:
    return ".local/share/uv/tools/" in sys.executable


def _read_project_extras() -> dict[str, list[str]]:
    """Read optional dependencies from pyproject.toml."""
    try:
        import tomllib
    except ImportError:
        # Python < 3.11 fallback
        try:
            import tomli as tomllib
        except ImportError:
            logger.error("Neither tomllib nor tomli available for reading pyproject.toml")
            return {}
    
    try:
        with open(_root / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("optional-dependencies", {})
    except Exception as e:
        logger.error(f"Failed to read pyproject.toml: {e}")
        return {}


def _check_extra_installed(extra: str) -> bool:
    """Return True if every package required by an extra is present.
    
    Reads from pyproject.toml static definitions instead of pipecat metadata.
    """
    extras = _read_project_extras()
    if extra not in extras:
        return True  # Extra doesn't exist or has no dependencies
    
    for package in extras[extra]:
        # Extract package name from complex specs like "pipecat-ai[openai]"
        pkg_name = re.split(r"[><=!~\[,\s]", package)[0].strip()
        if not pkg_name:
            continue
            
        try:
            importlib.metadata.distribution(pkg_name)
        except importlib.metadata.PackageNotFoundError:
            return False
    return True


def get_configured_providers() -> Set[str]:
    """Read ~/.talky config to find all providers across all voice profiles.

    Scans every profile so ensure_dependencies_for_server installs everything
    the voice switcher will try to bootstrap at startup.
    """
    config_dir = Path.home() / ".talky"
    providers: Set[str] = set()

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

    for profile in profiles.get("voice_profiles", {}).values():
        for key in ("tts_provider", "stt_provider"):
            if val := profile.get(key):
                providers.add(val)
        
        # Check if local audio playback is needed for server/daemon
        output_device = profile.get("output_device", "")
        if output_device and output_device != "none":
            providers.add("local_audio")

    return providers


def get_cli_providers() -> Set[str]:
    """Get providers needed for CLI commands (always includes audio)."""
    providers = get_configured_providers()
    # CLI commands like 'say' always need audio playback
    providers.add("local_audio")
    return providers


def _providers_to_extras(providers: Set[str]) -> list[str]:
    """Return all extra names needed by the given providers."""
    extras = []
    for provider in providers:
        if extra := PROVIDER_TO_EXTRA.get(provider):
            extras.append(extra)
    return extras


def _missing_extras(providers: Set[str]) -> list[str]:
    """Return extra names that are not yet installed."""
    return [
        extra
        for extra in _providers_to_extras(providers)
        if not _check_extra_installed(extra)
    ]


def _uv_cmd() -> str | None:
    import shutil
    return shutil.which("uv")


def install_dependencies(providers: Set[str]) -> bool:
    """Install missing extras for the given providers.

    Uses static definitions from pyproject.toml to determine which packages
    to install for each extra.

    In a uv tool environment: runs `uv tool install --reinstall --with`
    then re-execs the process so the new packages are loaded.

    In a regular venv: runs `uv pip install`.
    """
    missing_extras = _missing_extras(providers)
    if not missing_extras:
        return True

    uv = _uv_cmd()
    if not uv:
        logger.error("uv not found — cannot install dependencies")
        return False

    # Get all packages needed for missing extras
    extras = _read_project_extras()
    missing_packages = []
    for extra in missing_extras:
        if extra in extras:
            missing_packages.extend(extras[extra])
    
    if not missing_packages:
        return True

    print(f"Installing {', '.join(missing_extras)} dependencies...")

    if _is_tool_env():
        # uv tool install --with replaces ALL previous --with packages,
        # so we must pass every needed extra, not just the missing ones.
        all_extras = _providers_to_extras(providers)
        all_packages = []
        for extra in all_extras:
            if extra in extras:
                all_packages.extend(extras[extra])
        
        # Pin to the same Python the tool env was created with,
        # otherwise uv defaults to the system Python which may be incompatible.
        python = sys.executable
        # Try installing just the extras first without --reinstall
        result = subprocess.run(
            [uv, "tool", "install", "--editable", str(_root), "--python", python]
            + [f"--with={pkg}" for pkg in all_packages]
        )
        if result.returncode != 0:
            # If that fails, fall back to full reinstall
            result = subprocess.run(
                [uv, "tool", "install", "--editable", str(_root), "--reinstall", "--python", python]
                + [f"--with={pkg}" for pkg in all_packages]
            )
        if result.returncode != 0:
            print("❌ Install failed")
            return False
        print("Restarting...")
        os.execv(sys.argv[0], sys.argv)  # does not return

    # Non-tool env (development / direct uv run)
    result = subprocess.run([uv, "pip", "install"] + missing_packages, capture_output=True, text=True)
    if result.returncode != 0 and "No virtual environment" in result.stderr:
        result = subprocess.run(
            [uv, "pip", "install", "--user"] + missing_packages, capture_output=True, text=True
        )
    if result.returncode != 0:
        logger.error(f"Install failed: {result.stderr}")
        return False
    return True




def ensure_dependencies(for_cli: bool = False) -> bool:
    """Ensure dependencies for the configured providers (current env).
    
    Args:
        for_cli: If True, include audio dependencies needed for CLI commands
    """
    try:
        if for_cli:
            providers = get_cli_providers()
        else:
            providers = get_configured_providers()
        return install_dependencies(providers)
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
