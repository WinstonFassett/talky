#!/usr/bin/env python3
"""Dynamic dependency installer for talky CLI.

Installs provider dependencies on-demand based on user configuration.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Set

import yaml
from loguru import logger

# Get the project root directory
_root = Path(__file__).parent.parent

# Ensure HuggingFace cache is in user home directory, even in tool environments
os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(Path.home() / ".cache" / "huggingface" / "hub"))


PROVIDER_DEPS = {
    "kokoro": ["kokoro-onnx>=0.5.0"],
    "google": ["google-cloud-texttospeech>=2.13.0", "google-cloud-speech>=2.16.0"],
    "deepgram": ["deepgram-sdk>=2.3.0"],
    "whisper_local": ["mlx-whisper>=0.4.3; sys_platform == 'darwin'"],
    "openai": ["openai>=1.0.0"],
    "elevenlabs": ["elevenlabs>=1.0.0"],
    "cartesia": ["cartesia>=1.0.0"],
    "pipecat-core": ["pipecat-ai[kokoro,local,silero,webrtc]"],
    "pipecat-cloud": ["pipecat-ai[webrtc]"],  # Cloud-only - no local processing
}


def get_configured_providers() -> Set[str]:
    """Scan user config to determine which providers are needed for default profile."""
    config_dir = Path.home() / ".talky"
    providers_needed = set()
    
    # Load settings to get default voice profile
    settings_file = config_dir / "settings.yaml"
    default_voice_profile = "cloud"  # fallback default
    
    if settings_file.exists():
        try:
            with open(settings_file) as f:
                settings = yaml.safe_load(f) or {}
                default_voice_profile = settings.get("defaults", {}).get("voice_profile", default_voice_profile)
        except Exception as e:
            logger.warning(f"Failed to load settings: {e}")
    
    # Default bundled config path
    bundled_defaults = Path(__file__).parent.parent / "server" / "config" / "defaults"
    
    # Load voice profiles to find the default one
    voice_profiles_file = config_dir / "voice-profiles.yaml"
    if not voice_profiles_file.exists():
        voice_profiles_file = bundled_defaults / "voice-profiles.yaml"
        
    if voice_profiles_file.exists():
        try:
            with open(voice_profiles_file) as f:
                profiles = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Failed to load voice profiles: {e}")
            return set()
        
        # Only check the default voice profile
        profile = profiles.get("voice_profiles", {}).get(default_voice_profile, {})
        tts_provider = profile.get("tts_provider")
        stt_provider = profile.get("stt_provider")
        if tts_provider:
            providers_needed.add(tts_provider)
        if stt_provider:
            providers_needed.add(stt_provider)
    
    return providers_needed


def _check_installed(package: str) -> bool:
    """Check if a package is already installed."""
    # Extract package name without version specifier or extras
    name = package.split("[")[0].split(">")[0].split("<")[0].split("=")[0].split(";")[0].strip()
    try:
        __import__(name.replace("-", "_"))
        return True
    except ImportError:
        return False


def install_dependencies(providers: Set[str]) -> bool:
    """Install missing dependencies for specified providers."""
    if not providers:
        return True
    
    packages_to_install = []
    
    # Determine if we need cloud-only or full pipecat
    local_providers = {"kokoro", "whisper_local"}
    needs_local = any(provider in local_providers for provider in providers)
    
    # Add pipecat dependency with appropriate extras
    if needs_local:
        pipecat_extras = "kokoro,local,silero,webrtc"
    else:
        pipecat_extras = "webrtc"
    
    pipecat_pkg = f"pipecat-ai[{pipecat_extras}]"
    if not _check_installed("pipecat-ai"):
        packages_to_install.append(pipecat_pkg)
    
    # Add provider-specific dependencies
    for provider in providers:
        if provider in PROVIDER_DEPS:
            for pkg in PROVIDER_DEPS[provider]:
                if not _check_installed(pkg):
                    packages_to_install.append(pkg)
    
    if not packages_to_install:
        return True
    
    logger.info(f"Installing: {', '.join(packages_to_install)}")
    
    try:
        import shutil
        uv_cmd = shutil.which("uv")
        if not uv_cmd:
            logger.error("uv not found")
            return False
            
        # Check if we're in a uv tool environment by checking the executable path
        executable_path = sys.executable
        if "uv-tool" in executable_path or ".local/share/uv/tools/" in executable_path:
            # We're in a uv tool, assume dependencies are already available
            logger.info("CLI tool detected - assuming dependencies are available")
            return True
        else:
            # Try regular pip install
            result = subprocess.run(
                [uv_cmd, "pip", "install"] + packages_to_install,
                capture_output=True, text=True
            )
            
            if result.returncode != 0 and "No virtual environment found" in result.stderr:
                # Fall back to user install
                logger.info("Trying user install")
                result = subprocess.run(
                    [uv_cmd, "pip", "install", "--user"] + packages_to_install,
                    capture_output=True, text=True
                )
        
        if result.returncode != 0:
            logger.error(f"Install failed: {result.stderr}")
            return False
        return True
            
    except Exception as e:
        logger.error(f"Install error: {e}")
        return False


def ensure_dependencies_for_server(server_dir: Path) -> bool:
    """Ensure all required dependencies are installed in server's .venv."""
    try:
        providers_needed = get_configured_providers()
        
        # Determine if we need cloud-only or full pipecat
        local_providers = {"kokoro", "whisper_local"}
        needs_local = any(provider in local_providers for provider in providers_needed)
        
        # Find packages to install
        packages_to_install = []
        
        # Add pipecat dependency with appropriate extras
        if needs_local:
            pipecat_extras = "kokoro,local,silero,webrtc"
        else:
            pipecat_extras = "webrtc"
        
        pipecat_pkg = f"pipecat-ai[{pipecat_extras}]"
        if not _check_installed("pipecat-ai"):
            packages_to_install.append(pipecat_pkg)
        
        # Add provider-specific dependencies (skip pipecat-* as we handle it above)
        for provider in providers_needed:
            if provider in PROVIDER_DEPS and not provider.startswith("pipecat"):
                for pkg in PROVIDER_DEPS[provider]:
                    if not _check_installed(pkg):
                        packages_to_install.append(pkg)
        
        if not packages_to_install:
            return True
        
        logger.info(f"Installing: {', '.join(packages_to_install)}")
        
        # Install in server's .venv using uv
        import shutil
        uv_cmd = shutil.which("uv")
        if not uv_cmd:
            logger.error("uv not found")
            return False
        
        # Use uv pip install with server's venv Python
        result = subprocess.run(
            [uv_cmd, "pip", "install", "--python", str(server_dir.parent / ".venv" / "bin" / "python")] + packages_to_install,
            capture_output=True, text=True
        )
        
        if result.returncode != 0:
            logger.error(f"Failed to install dependencies: {result.stderr}")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to ensure dependencies: {e}")
        return False


def ensure_dependencies() -> bool:
    """Ensure all required dependencies are installed (legacy, uses current env)."""
    try:
        providers_needed = get_configured_providers()
        return install_dependencies(providers_needed)
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
