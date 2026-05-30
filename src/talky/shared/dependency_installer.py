#!/usr/bin/env python3
"""Dynamic dependency installer for talky CLI.

Installs provider dependencies on-demand via pipecat-ai extras.
In a uv tool environment, uses `uv tool install --reinstall --with`
then re-execs the process so new packages are immediately available.
"""

import importlib.metadata
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Set

import yaml
from loguru import logger

# Repo root — only valid in editable installs; used as a fallback for
# `uv tool install --editable .` in install_extra_no_reexec(). Wheel installs
# don't have a repo root and shouldn't need one (extras are read from
# installed package metadata, not pyproject.toml source).
_root = Path(__file__).resolve().parents[3]

# Package directory — always valid (editable + wheel). Holds bundled
# defaults shipped with the package.
_PKG_DIR = Path(__file__).resolve().parent.parent

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


def _is_venv() -> bool:
    """True if running inside a venv / virtualenv (uv tool envs included)."""
    return sys.prefix != sys.base_prefix


def _install_context() -> str:
    """Classify how talky was installed. Drives the on-demand installer.

    Returns one of:
      - "uv-tool"    — `uv tool install talky` (the documented happy path)
      - "venv"       — a project venv (`uv venv`/`python -m venv`/etc.)
      - "system-pip" — installed into base Python's site-packages; no venv
                       wraps us. On-demand `uv pip install` will fail
                       because uv refuses to mutate system Python.
    """
    if _is_tool_env():
        return "uv-tool"
    if _is_venv():
        return "venv"
    return "system-pip"


# Extras whose Python packages compile against native libs that must be
# present on the system before pip/uv can build them. Ticket 4fbd —
# detect missing native deps before triggering a from-source build that
# would otherwise dump a C compiler error on the user.
_NATIVE_DEPS_BY_EXTRA = {
    "local_audio": ("portaudio", "pyaudio"),
    "audio": ("portaudio", "pyaudio"),
}


def _check_portaudio_present() -> tuple[bool, str]:
    """Detect libportaudio on this system. Returns (present, install_hint).

    No subprocess on the happy path — checks well-known library locations
    via Path.exists() first. Falls back to pkg-config only if those miss.
    """
    import platform
    from pathlib import Path

    system = platform.system()
    if system == "Darwin":
        # Homebrew (Apple Silicon + Intel) and macports.
        candidates = [
            "/opt/homebrew/lib/libportaudio.dylib",
            "/usr/local/lib/libportaudio.dylib",
            "/opt/local/lib/libportaudio.dylib",
        ]
        for c in candidates:
            if Path(c).exists():
                return True, ""
        return False, "Install with: brew install portaudio"

    if system == "Linux":
        candidates = [
            "/usr/lib/x86_64-linux-gnu/libportaudio.so.2",
            "/usr/lib/aarch64-linux-gnu/libportaudio.so.2",
            "/usr/lib64/libportaudio.so.2",
            "/usr/lib/libportaudio.so.2",
        ]
        for c in candidates:
            if Path(c).exists():
                return True, ""

        # Fall back to pkg-config — handles unusual install paths.
        try:
            result = subprocess.run(
                ["pkg-config", "--exists", "portaudio-2.0"],
                capture_output=True,
            )
            if result.returncode == 0:
                return True, ""
        except FileNotFoundError:
            pass

        # Distro-specific hint based on which package manager is present.
        if Path("/usr/bin/apt-get").exists() or Path("/usr/bin/apt").exists():
            hint = "Install with: sudo apt install portaudio19-dev"
        elif Path("/usr/bin/dnf").exists():
            hint = "Install with: sudo dnf install portaudio-devel"
        elif Path("/usr/bin/pacman").exists():
            hint = "Install with: sudo pacman -S portaudio"
        else:
            hint = "Install portaudio via your system package manager (look for portaudio19-dev / portaudio-devel)"
        return False, hint

    # Windows / other — pyaudio wheels exist there, native check N/A.
    return True, ""


def _check_native_deps_for_extra(extra: str) -> tuple[bool, str]:
    """Check native system libraries required by an extra's Python packages.

    Returns (ok, reason). ok=True means the extra is safe to install via
    the normal uv path. ok=False means a system lib is missing and the
    user must install it before retrying.
    """
    native = _NATIVE_DEPS_BY_EXTRA.get(extra)
    if not native:
        return True, ""
    native_lib, _python_pkg = native
    if native_lib == "portaudio":
        ok, hint = _check_portaudio_present()
        if not ok:
            return False, f"portaudio system library is missing. {hint}"
    return True, ""


def _read_project_extras() -> dict[str, list[str]]:
    """Return {extra_name: [package_spec, ...]} for the installed talky package.

    Uses importlib.metadata so it works in BOTH editable installs (uv tool
    install --editable .) and wheel installs (uv tool install talky-X.whl /
    pip install talky). Reading pyproject.toml directly would only work for
    editable installs from source.

    Extra names are returned as declared in pyproject.toml (underscores
    preserved) — importlib.metadata normalizes to PEP 685 form (hyphens),
    so we map back to the canonical form callers use ("local_audio" not
    "local-audio").
    """
    from importlib.metadata import PackageNotFoundError, requires

    try:
        reqs = requires("talky") or []
    except PackageNotFoundError:
        logger.error("talky package metadata not found — is it installed?")
        return {}

    # Map normalized (hyphen) extra names back to the canonical form used in
    # the codebase. Source of truth is pyproject's [project.optional-dependencies]
    # keys; we read them once from there for the rename, then never again.
    _CANONICAL: dict[str, str] = {
        "local-audio": "local_audio",
        # All other extras already use hyphens consistently in both places.
    }

    extras: dict[str, list[str]] = {}
    for req in reqs:
        # Format: "package-spec ; extra == \"name\"" — split on the marker.
        if "; extra ==" not in req:
            continue
        spec, marker = req.split("; extra ==", 1)
        extra_name = marker.strip().strip('"').strip("'")
        canonical = _CANONICAL.get(extra_name, extra_name)
        extras.setdefault(canonical, []).append(spec.strip())
    return extras


def _check_extra_installed(extra: str) -> bool:
    """Return True if every package required by an extra is present.
    
    Reads from pyproject.toml static definitions instead of pipecat metadata.
    Also checks for specific module availability for providers with optional deps.
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
    
    # Additional checks for specific provider modules
    if extra == "stt-google":
        if importlib.util.find_spec("google.genai") is None:
            return False

    if extra == "stt-deepgram":
        if importlib.util.find_spec("deepgram") is None:
            return False
    
    return True


def get_configured_providers() -> Set[str]:
    """Read ~/.talky config to find all providers across all voice profiles.

    Scans every profile so ensure_dependencies_for_server installs everything
    the voice switcher will try to bootstrap at startup.
    """
    config_dir = Path.home() / ".talky"
    providers: Set[str] = set()

    bundled_defaults = _PKG_DIR / "config" / "defaults"
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


def get_llm_backend_extra(backend_name: str) -> str | None:
    """Return the pyproject.toml extra name declared for an LLM backend, or None."""
    backends_file = _PKG_DIR / "config" / "core" / "llm-backends.yaml"
    # Also check user overrides
    user_file = Path.home() / ".talky" / "llm-backends.yaml"
    for path in (user_file, backends_file):
        if not path.exists():
            continue
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            backend = data.get("llm_backends", {}).get(backend_name, {})
            if extra := backend.get("extra"):
                return extra
        except Exception:
            pass
    return None


def install_extra_no_reexec(extra: str) -> bool:
    """Install a pyproject.toml extra without re-execing the current process.

    Used at daemon runtime (profile switch). In a tool env, uses
    `uv tool install --with` so the package is registered in the tool env
    and survives future reinstalls. In a dev venv, uses `uv pip install`.
    The package won't be importable until the daemon restarts — caller
    must notify the user.
    Returns True if install succeeded or extra was already present.
    """
    if _check_extra_installed(extra):
        return True

    # Install-context gate (ticket 0fee) — refuse if we're in a system
    # Python with no surrounding venv. `uv pip install` won't write to
    # system Python, and writing there would be the wrong thing anyway.
    context = _install_context()
    if context == "system-pip":
        logger.error(
            "talky was installed into system Python (no venv). On-demand "
            "extras require a uv tool install. To fix: `pip uninstall talky` "
            "and then `uv tool install talky`. See README for install docs."
        )
        return False

    # Native-dep gate (ticket 4fbd) — refuse to attempt the install if a
    # required system library is missing. Otherwise pip would try to
    # build from source and dump a C compiler error.
    native_ok, native_reason = _check_native_deps_for_extra(extra)
    if not native_ok:
        logger.error(f"Cannot install extra {extra!r}: {native_reason}")
        return False

    extras = _read_project_extras()
    packages = extras.get(extra, [])
    if not packages:
        logger.warning(f"No packages defined for extra {extra!r}")
        return False

    uv = _uv_cmd()
    if not uv:
        logger.error("uv not found — cannot install dependencies")
        return False

    logger.info(f"Installing extra {extra!r}: {packages}")
    # Surface install to readiness tracker so CLI/SPA can show progress.
    # Lazy import: dependency_installer is also used by the CLI at module
    # import time, before the server package is on sys.path in some flows.
    try:
        from talky.server.readiness import readiness
        track_cm = readiness.track(f"Installing {extra}")
    except Exception:
        from contextlib import nullcontext
        track_cm = nullcontext(None)

    with track_cm as _t:
        if _is_tool_env():
            # Use uv tool install --with so the package is persisted in the tool
            # env's --with list and survives future `uv tool install --force`.
            # Must pass ALL currently installed extras (not just the new one)
            # because --with replaces the full list.
            all_packages = list(packages)
            for e, pkgs in extras.items():
                if e != extra and _check_extra_installed(e):
                    all_packages.extend(pkgs)
            python = sys.executable

            # Detect editable vs wheel install. Editable installs have a
            # pyproject.toml at the repo root; wheel installs don't.
            is_editable = (_root / "pyproject.toml").exists()
            if is_editable:
                install_target = ["--editable", str(_root)]
            else:
                install_target = ["talky"]

            if _t is not None:
                _t.progress(msg=f"uv tool install --with {extra}")
            result = subprocess.run(
                [uv, "tool", "install", *install_target, "--python", python]
                + [f"--with={pkg}" for pkg in all_packages],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                logger.error(f"Install failed: {result.stderr}")
                return False
        else:
            if _t is not None:
                _t.progress(msg=f"uv pip install {extra}")
            result = subprocess.run(
                [uv, "pip", "install"] + packages,
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                logger.error(f"Install failed: {result.stderr}")
                return False

    logger.info(f"Extra {extra!r} installed — restart the daemon: talky kill && talky daemon")
    return True


def _uv_cmd() -> str | None:
    import shutil
    # shutil.which uses PATH which may be stripped in daemon environments.
    # Check common install locations explicitly as fallback.
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
        str(Path.home() / ".cargo/bin/uv"),
        str(Path.home() / ".local/bin/uv"),
    ):
        if Path(candidate).exists():
            return candidate
    return None


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
