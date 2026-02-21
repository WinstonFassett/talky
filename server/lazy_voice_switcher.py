"""
Voice Profile Switcher for TTS Services

Switches between different TTS services at runtime.
"""

from typing import List, Type
from loguru import logger

from pipecat.pipeline.service_switcher import ServiceSwitcher, StrategyType
from pipecat.services.tts_service import TTSService
from pipecat.processors.frame_processor import FrameProcessor

from server.config.profile_manager import get_profile_manager
from shared.service_factory import create_tts_service_from_config


class TTSSwitcher(ServiceSwitcher[StrategyType]):
    """A pipeline that switches between different TTS services at runtime.

    Example::

        tts_switcher = TTSSwitcher(
            tts_services=[google_tts, kokoro_tts],
            strategy_type=ServiceSwitcherStrategyManual
        )
    """

    def __init__(self, tts_services: List[TTSService], strategy_type: Type[StrategyType]):
        """Initialize the TTS switcher with a list of TTS services and a switching strategy.

        Args:
            tts_services: List of TTS services to switch between.
            strategy_type: The strategy class to use for switching between TTS services.
        """
        super().__init__(tts_services, strategy_type)

    @property
    def tts_services(self) -> List[TTSService]:
        """Get the list of TTS services managed by this switcher.

        Returns:
            List of TTS services managed by this switcher.
        """
        return self.services

    @property
    def active_tts(self) -> TTSService:
        """Get the currently active TTS service.

        Returns:
            The currently active TTS service.
        """
        return self._active_service

    def get_service_for_profile(self, profile_name: str) -> TTSService | None:
        """Get the TTS service for a specific profile name.

        Args:
            profile_name: The profile name to find the service for.

        Returns:
            The TTS service for the profile, or None if not found.
        """
        if hasattr(self, '_profile_mapping'):
            for service, service_profile_name in self._profile_mapping.items():
                if service_profile_name == profile_name:
                    return service
        return None

    @classmethod
    def from_profile_names(cls, profile_names: List[str], strategy_type: Type[StrategyType]):
        """Create a TTSSwitcher from a list of voice profile names.

        Args:
            profile_names: List of voice profile names to create TTS services for.
            strategy_type: The strategy class to use for switching.

        Returns:
            A TTSSwitcher instance with TTS services created from the profiles.
        """
        pm = get_profile_manager()
        tts_services = []
        profile_mapping = {}  # Map service to profile name

        for profile_name in profile_names:
            profile = pm.get_voice_profile(profile_name)
            if not profile:
                logger.warning(f"Voice profile not found: {profile_name}")
                continue

            try:
                tts_service = create_tts_service_from_config(
                    profile.tts_provider, 
                    voice=profile.tts_voice
                )
                # Store profile info on the service for later lookup
                tts_service._profile_name = profile_name
                tts_service._voice_id = profile.tts_voice
                profile_mapping[tts_service] = profile_name
                
                tts_services.append(tts_service)
                logger.info(f"Created TTS service for profile: {profile_name}")
            except Exception as e:
                logger.error(f"Failed to create TTS service for {profile_name}: {e}")

        if not tts_services:
            raise ValueError("No TTS services could be created from the provided profiles")

        switcher = cls(tts_services, strategy_type)
        switcher._profile_mapping = profile_mapping  # Store mapping for lookup
        return switcher
