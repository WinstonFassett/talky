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
    session_key: Optional[str] = None  # Override session for LLM backend
    system_message: Optional[str] = None  # Optional system message


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
        """Load LLM backends by merging core + defaults + user extensions."""
        # Start with core backends from core/ folder
        core_path = Path(__file__).parent / "core" / "llm-backends.yaml"
        core_backends = {}
        if core_path.exists():
            with open(core_path) as f:
                core_data = yaml.safe_load(f) or {}
            core_backends = core_data.get("llm_backends", {})
        
        # Extend with defaults (if any) - for new user templates
        defaults_path = BUNDLED_DEFAULTS / "llm-backends.yaml"
        if defaults_path.exists():
            with open(defaults_path) as f:
                defaults_data = yaml.safe_load(f) or {}
            defaults_backends = defaults_data.get("llm_backends", {})
            
            # Merge defaults into core (defaults extend core)
            for name, config in defaults_backends.items():
                if name not in core_backends:
                    core_backends[name] = config
        
        # Load user extensions/overrides from YAML
        try:
            data = self._read_yaml("llm-backends.yaml")
            user_backends = data.get("llm_backends", {})
            
            if user_backends:  # Check if user_backends is not empty
                # Merge user backends (deep merge to allow partial overrides)
                for name, user_config in user_backends.items():
                    if name in core_backends:
                        # Override existing core backend
                        self.llm_backends[name] = LLMBackend(
                            name=name,
                            description=user_config.get("description", core_backends[name]["description"]),
                            service_class=user_config.get("service_class", core_backends[name]["service_class"]),
                            config={
                                **core_backends[name]["config"],
                                **user_config.get("config", {})
                            },
                        )
                    else:
                        # Add new user-defined backend
                        self.llm_backends[name] = LLMBackend(
                            name=name,
                            description=user_config.get("description", ""),
                            service_class=user_config.get("service_class", ""),
                            config=user_config.get("config", {}),
                        )
            else:
                # No user extensions - use core (+ defaults) backends
                for name, config in core_backends.items():
                    self.llm_backends[name] = LLMBackend(
                        name=name,
                        description=config["description"],
                        service_class=config["service_class"],
                        config=config["config"],
                    )
                        
        except FileNotFoundError:
            # No user extensions file - use core (+ defaults) backends
            for name, config in core_backends.items():
                self.llm_backends[name] = LLMBackend(
                    name=name,
                    description=config["description"],
                    service_class=config["service_class"],
                    config=config["config"],
                )
        except Exception as e:
            logger.warning(f"Error loading user LLM backends: {e}. Using core backends only.")
            # Fallback to core backends
            for name, config in core_backends.items():
                self.llm_backends[name] = LLMBackend(
                    name=name,
                    description=config["description"],
                    service_class=config["service_class"],
                    config=config["config"],
                )

    def _load_voice_backends(self):
        """Load voice backends by merging core + defaults + user extensions."""
        # Start with core backends from core/ folder
        core_path = Path(__file__).parent / "core" / "voice-backends.yaml"
        if core_path.exists():
            with open(core_path) as f:
                core_data = yaml.safe_load(f) or {}
            self.voice_backends = core_data.get("voice_backends", {})
        else:
            self.voice_backends = {}
        
        # Extend with defaults (if any) - for new user templates
        defaults_path = BUNDLED_DEFAULTS / "voice-backends.yaml"
        if defaults_path.exists():
            with open(defaults_path) as f:
                defaults_data = yaml.safe_load(f) or {}
            defaults_backends = defaults_data.get("voice_backends", {})
            
            # Merge defaults into core (defaults extend core)
            for backend_type, providers in defaults_backends.items():
                if backend_type not in self.voice_backends:
                    self.voice_backends[backend_type] = {}
                
                for provider_name, config in providers.items():
                    if provider_name not in self.voice_backends[backend_type]:
                        self.voice_backends[backend_type][provider_name] = config
        
        # Load user extensions/overrides from YAML
        try:
            data = self._read_yaml("voice-backends.yaml")
            user_backends = data.get("voice_backends", {})
            
            if user_backends:  # Check if user_backends is not empty
                # Merge user backends (deep merge to allow partial overrides)
                for backend_type, providers in user_backends.items():
                    if backend_type not in self.voice_backends:
                        self.voice_backends[backend_type] = {}
                    
                    if providers:  # Check if providers is not empty
                        for provider_name, config in providers.items():
                            if provider_name in self.voice_backends[backend_type]:
                                # Override existing core backend
                                self.voice_backends[backend_type][provider_name] = {
                                    **self.voice_backends[backend_type][provider_name],
                                    **config  # User config takes precedence
                                }
                            else:
                                # Add new user-defined backend
                                self.voice_backends[backend_type][provider_name] = config
                        
        except FileNotFoundError:
            # No user extensions file - use core (+ defaults) backends
            pass
        except Exception as e:
            logger.warning(f"Error loading user voice backends: {e}. Using core backends only.")

    def _load_voice_profiles(self):
        data = self._read_yaml("voice-profiles.yaml")
        profiles_data = data.get("voice_profiles", {})
        
        # First pass: load the 'default' profile to use as fallback
        default_entry = profiles_data.get("default", {})
        default_tts_provider = default_entry.get("tts_provider", "")
        default_tts_voice = default_entry.get("tts_voice", "")
        default_stt_provider = default_entry.get("stt_provider", "")
        default_stt_model = default_entry.get("stt_model", "")
        
        # Second pass: load all profiles, falling back to 'default' profile values
        for name, entry in profiles_data.items():
            self.voice_profiles[name] = VoiceProfile(
                name=name,
                description=entry.get("description", ""),
                tts_provider=entry.get("tts_provider") or default_tts_provider,
                tts_voice=entry.get("tts_voice") or default_tts_voice,
                tts_config=entry.get("tts_config", {}),
                stt_provider=entry.get("stt_provider") or default_stt_provider,
                stt_model=entry.get("stt_model") or default_stt_model,
                stt_config=entry.get("stt_config", {}),
            )

    def _load_talky_profiles(self):
        """Load talky profiles by merging core + defaults + user extensions."""
        # Start with core profiles from core/ folder
        core_path = Path(__file__).parent / "core" / "talky-profiles.yaml"
        core_profiles = {}
        if core_path.exists():
            with open(core_path) as f:
                core_data = yaml.safe_load(f) or {}
            core_profiles = core_data.get("talky_profiles", {})
        
        # Extend with defaults (if any) - for new user templates
        defaults_path = BUNDLED_DEFAULTS / "talky-profiles.yaml"
        if defaults_path.exists():
            with open(defaults_path) as f:
                defaults_data = yaml.safe_load(f) or {}
            defaults_profiles = defaults_data.get("talky_profiles", {})
            
            # Merge defaults into core (defaults extend core)
            for name, config in defaults_profiles.items():
                if name not in core_profiles:
                    core_profiles[name] = config
        
        # Load user extensions/overrides from YAML
        try:
            data = self._read_yaml("talky-profiles.yaml")
            user_profiles = data.get("talky_profiles", {})
            
            # First, load all core+defaults profiles
            for name, config in core_profiles.items():
                enabled = config.get("enabled", True)
                if enabled:
                    self.talky_profiles[name] = TalkyProfile(
                        name=name,
                        description=config.get("description", ""),
                        llm_backend=config.get("llm_backend") or self.defaults.get("llm_backend") or "",
                        voice_profile=config.get("voice_profile") or self.defaults.get("voice_profile") or "",
                        session_key=config.get("session_key"),
                    )
            
            # Then apply user overrides
            for name, user_config in user_profiles.items():
                if name in core_profiles:
                    # Override existing core profile
                    merged_config = {
                        **core_profiles[name],
                        **user_config  # User config takes precedence
                    }
                    # Check if profile is enabled (default to true if not specified)
                    enabled = merged_config.get("enabled", True)
                    if enabled:
                        self.talky_profiles[name] = TalkyProfile(
                            name=name,
                            description=merged_config.get("description", ""),
                            llm_backend=merged_config.get("llm_backend") or self.defaults.get("llm_backend") or "",
                            voice_profile=merged_config.get("voice_profile") or self.defaults.get("voice_profile") or "",
                            session_key=merged_config.get("session_key"),
                            system_message=merged_config.get("system_message"),
                        )
                    else:
                        # User disabled the profile, remove it
                        self.talky_profiles.pop(name, None)
                else:
                    # Add new user-defined profile
                    enabled = user_config.get("enabled", True)
                    if enabled:
                        self.talky_profiles[name] = TalkyProfile(
                            name=name,
                            description=user_config.get("description", ""),
                            llm_backend=user_config.get("llm_backend") or self.defaults.get("llm_backend") or "",
                            voice_profile=user_config.get("voice_profile") or self.defaults.get("voice_profile") or "",
                            session_key=user_config.get("session_key"),
                            system_message=user_config.get("system_message"),
                        )
                        
        except FileNotFoundError:
            # No user extensions file - use core (+ defaults) profiles
            for name, config in core_profiles.items():
                enabled = config.get("enabled", True)
                if enabled:
                    self.talky_profiles[name] = TalkyProfile(
                        name=name,
                        description=config.get("description", ""),
                        llm_backend=config.get("llm_backend") or self.defaults.get("llm_backend") or "",
                        voice_profile=config.get("voice_profile") or self.defaults.get("voice_profile") or "",
                        session_key=config.get("session_key"),
                    )
        except Exception as e:
            logger.warning(f"Error loading user talky profiles: {e}. Using core profiles only.")
            # Fallback to core profiles
            for name, config in core_profiles.items():
                enabled = config.get("enabled", True)
                if enabled:
                    self.talky_profiles[name] = TalkyProfile(
                        name=name,
                        description=config.get("description", ""),
                        llm_backend=config.get("llm_backend") or self.defaults.get("llm_backend") or "",
                        voice_profile=config.get("voice_profile") or self.defaults.get("voice_profile") or "",
                        session_key=config.get("session_key"),
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
        return self.defaults.get("llm_backend") or ""

    def get_default_voice_profile(self) -> str:
        return self.defaults.get("voice_profile") or ""

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
