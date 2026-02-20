"""Profile Manager — loads all config from ~/.talky/ with auto-copy of bundled defaults."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from loguru import logger

BUNDLED_DEFAULTS = Path(__file__).parent / "defaults"

CONFIG_FILES = [
    "llm-backends.yaml",
    "voice-backends.yaml",
    "voice-profiles.yaml",
    "talky-profiles.yaml",
    "settings.yaml",
]


@dataclass
class LLMBackend:
    name: str
    description: str
    service_class: str
    config: Dict[str, Any]
    system_message: str


@dataclass
class VoiceProfile:
    name: str
    description: str
    tts_provider: str
    tts_voice: str
    tts_config: Dict[str, Any]
    stt_provider: str
    stt_model: str
    stt_config: Dict[str, Any]


@dataclass
class TalkyProfile:
    name: str
    description: str
    llm_backend: str
    voice_profile: str
    system_message: Optional[str] = None


class ProfileManager:
    """Loads LLM backends, voice backends, voice profiles, talky profiles, and defaults."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = Path(config_dir) if config_dir else Path.home() / ".talky"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.llm_backends: Dict[str, LLMBackend] = {}
        self.voice_backends: Dict[str, Any] = {}
        self.voice_profiles: Dict[str, VoiceProfile] = {}
        self.talky_profiles: Dict[str, TalkyProfile] = {}
        self.defaults: Dict[str, Any] = {}

        self._ensure_defaults()
        self._load_configs()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _ensure_defaults(self):
        """Copy any missing config files from bundled defaults."""
        if not BUNDLED_DEFAULTS.exists():
            return
        for name in CONFIG_FILES:
            dest = self.config_dir / name
            src = BUNDLED_DEFAULTS / name
            if not dest.exists() and src.exists():
                shutil.copy2(src, dest)
                logger.info(f"Copied default config: {name}")

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_configs(self):
        self._load_defaults()
        self._load_llm_backends()
        self._load_voice_backends()
        self._load_voice_profiles()
        self._load_talky_profiles()
        logger.info(
            f"Loaded {len(self.llm_backends)} LLM backends, "
            f"{len(self.voice_profiles)} voice profiles, "
            f"{len(self.talky_profiles)} talky profiles"
        )

    def _read_yaml(self, filename: str) -> dict:
        path = self.config_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {path}")
        with open(path) as f:
            return yaml.safe_load(f) or {}

    def _load_llm_backends(self):
        data = self._read_yaml("llm-backends.yaml")
        for name, entry in data.get("llm_backends", {}).items():
            self.llm_backends[name] = LLMBackend(
                name=name,
                description=entry.get("description", ""),
                service_class=entry.get("service_class", ""),
                config=entry.get("config", {}),
                system_message=entry.get("system_message", ""),
            )

    def _load_voice_backends(self):
        data = self._read_yaml("voice-backends.yaml")
        self.voice_backends = data.get("voice_backends", {})

    def _load_voice_profiles(self):
        data = self._read_yaml("voice-profiles.yaml")
        for name, entry in data.get("voice_profiles", {}).items():
            self.voice_profiles[name] = VoiceProfile(
                name=name,
                description=entry.get("description", ""),
                tts_provider=entry.get("tts_provider") or self.defaults.get("tts_provider", ""),
                tts_voice=entry.get("tts_voice") or self.defaults.get("tts_voice", ""),
                tts_config=entry.get("tts_config", {}),
                stt_provider=entry.get("stt_provider") or self.defaults.get("stt_provider", ""),
                stt_model=entry.get("stt_model") or self.defaults.get("stt_model", ""),
                stt_config=entry.get("stt_config", {}),
            )

    def _load_talky_profiles(self):
        data = self._read_yaml("talky-profiles.yaml")
        for name, entry in data.get("talky_profiles", {}).items():
            vp = entry.get("voice_profile") or self.defaults.get("voice_profile") or ""
            self.talky_profiles[name] = TalkyProfile(
                name=name,
                description=entry.get("description", ""),
                llm_backend=entry.get("llm_backend") or self.defaults.get("llm_backend", ""),
                voice_profile=vp,
                system_message=entry.get("system_message"),
            )

    def _load_defaults(self):
        data = self._read_yaml("settings.yaml")
        self.defaults = data.get("defaults", {})

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_llm_backend(self, name: str) -> Optional[LLMBackend]:
        return self.llm_backends.get(name)

    def get_voice_profile(self, name: str) -> Optional[VoiceProfile]:
        return self.voice_profiles.get(name)

    def get_talky_profile(self, name: str) -> Optional[TalkyProfile]:
        return self.talky_profiles.get(name)

    def get_voice_backend_config(self, backend_type: str, backend_name: str) -> Dict[str, Any]:
        return self.voice_backends.get(backend_type, {}).get(backend_name, {})

    def list_llm_backends(self) -> Dict[str, str]:
        return {n: b.description for n, b in self.llm_backends.items()}

    def list_voice_profiles(self) -> Dict[str, str]:
        return {n: p.description for n, p in self.voice_profiles.items()}

    def list_talky_profiles(self) -> Dict[str, str]:
        return {n: p.description for n, p in self.talky_profiles.items()}

    def get_default_llm_backend(self) -> str:
        return self.defaults.get("llm_backend", "moltis")

    def get_default_voice_profile(self) -> str:
        return self.defaults.get("voice_profile", "cloud")

    def resolve_talky_profile(self, name: str) -> Dict[str, Any]:
        """Resolve a talky profile name into LLM backend + voice profile + configs."""
        tp = self.get_talky_profile(name)
        if not tp:
            raise ValueError(f"Unknown talky profile: {name}")
        llm = self.get_llm_backend(tp.llm_backend)
        if not llm:
            raise ValueError(f"Unknown LLM backend: {tp.llm_backend}")
        vp = self.get_voice_profile(tp.voice_profile)
        if not vp:
            raise ValueError(f"Unknown voice profile: {tp.voice_profile}")
        return {
            "talky_profile": tp,
            "llm_backend": llm,
            "voice_profile": vp,
        }


# ---------------------------------------------------------------------------
# Module-level lazy singleton
# ---------------------------------------------------------------------------

_instance: Optional[ProfileManager] = None


def get_profile_manager(config_dir: Optional[Path] = None) -> ProfileManager:
    global _instance
    if _instance is None or config_dir is not None:
        _instance = ProfileManager(config_dir=config_dir)
    return _instance


# Backwards compat: module-level instance (eagerly created on import)
profile_manager = get_profile_manager()
