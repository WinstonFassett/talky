"""
Provider-based Voice Switcher for TTS Services

Switches between TTS providers at runtime, with dynamic voice switching within providers.
Much more efficient than creating one service per voice profile.
"""

from typing import Dict, Optional
from loguru import logger

from pipecat.services.tts_service import TTSService
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import StartFrame, Frame

from server.config.profile_manager import get_profile_manager
from shared.service_factory import create_tts_service_from_config


class ProviderVoiceSwitcher(FrameProcessor):
    """A pipeline that switches between TTS providers and voices at runtime.
    
    Instead of creating one service per voice profile, creates one service per provider
    and changes voices dynamically within each provider.
    """

    def __init__(self):
        """Initialize the provider voice switcher."""
        super().__init__()
        self._tts_services: Dict[str, TTSService] = {}
        self._active_provider: Optional[str] = None
        self._active_voice: Optional[str] = None
        self._profile_manager = get_profile_manager()
        
        # Don't initialize providers at startup - do it lazily when needed
        logger.info("ProviderVoiceSwitcher initialized (lazy loading enabled)")

    def _get_or_create_service(self, provider: str) -> Optional[TTSService]:
        """Get existing service or create it lazily."""
        if provider in self._tts_services:
            return self._tts_services[provider]
        
        # Create service on-demand
        try:
            logger.info(f"Creating TTS service for provider: {provider}")
            service = create_tts_service_from_config(provider)
            self._tts_services[provider] = service
            
            logger.info(f"Successfully created TTS service for provider: {provider}")
            return service
        except Exception as e:
            logger.error(f"Failed to create TTS service for provider {provider}: {e}")
            return None

    def get_service_for_profile(self, profile_name: str) -> Optional[TTSService]:
        """Get the TTS service for a specific profile name.
        
        Args:
            profile_name: The profile name to find the service for.

        Returns:
            The TTS service for the profile, or None if not found.
        """
        profile = self._profile_manager.get_voice_profile(profile_name)
        if not profile:
            return None
            
        provider = profile.tts_provider
        
        # Get or create the service for this provider
        service = self._get_or_create_service(provider)
        if service and hasattr(service, 'set_voice'):
            # For Google TTS, change the voice dynamically
            service.set_voice(profile.tts_voice)
            return service
        elif service:
            # For Kokoro and others, recreate with new voice
            try:
                new_service = create_tts_service_from_config(provider, voice_id=profile.tts_voice)
                self._tts_services[provider] = new_service
                return new_service
            except Exception as e:
                logger.error(f"Failed to recreate service for {provider}: {e}")
                return None
        
        return None

    def set_voice_profile(self, profile_name: str) -> bool:
        """Switch to a specific voice profile.
        
        Args:
            profile_name: The voice profile name to switch to
            
        Returns:
            True if successful, False otherwise
        """
        profile = self._profile_manager.get_voice_profile(profile_name)
        if not profile:
            logger.error(f"Voice profile not found: {profile_name}")
            return False

        provider = profile.tts_provider
        voice_id = profile.tts_voice

        # Get or create service lazily
        service = self._get_or_create_service(provider)
        if not service:
            logger.error(f"Failed to get TTS service for provider: {provider}")
            return False

        # Change voice if the service supports it
        if hasattr(service, 'set_voice'):
            service.set_voice(voice_id)
            logger.info(f"Switched to {provider} provider with voice: {voice_id}")
        else:
            # For services like Kokoro that don't support set_voice, recreate
            try:
                new_service = create_tts_service_from_config(provider, voice_id=voice_id)
                self._tts_services[provider] = new_service
                logger.info(f"Recreated {provider} service with voice: {voice_id}")
            except Exception as e:
                logger.error(f"Failed to switch voice for {provider}: {e}")
                return False

        self._active_provider = provider
        self._active_voice = voice_id
        return True

    @property
    def active_service(self) -> Optional[TTSService]:
        """Get the currently active TTS service."""
        if self._active_provider:
            return self._tts_services.get(self._active_provider)
        return None

    async def process_frame(self, frame, direction):
        """Process frames by routing them to the active TTS service."""
        active_service = self.active_service
        if not active_service:
            return
            
        try:
            await active_service.process_frame(frame, direction)
        except Exception as e:
            logger.error(f"Error processing frame with {self._active_provider} service: {e}")
            # For Google TTS, try to recreate the service on error
            if self._active_provider == "google":
                logger.info("Recreating Google TTS service after error")
                try:
                    new_service = create_tts_service_from_config("google")
                    self._tts_services["google"] = new_service
                    # Retry with new service
                    await new_service.process_frame(frame, direction)
                except Exception as retry_error:
                    logger.error(f"Retry with recreated Google TTS failed: {retry_error}")
            # Just log the error and continue - don't crash the bot
            
    def get_current_profile_name(self) -> Optional[str]:
        """Get the current voice profile name based on active provider/voice."""
        if not self._active_provider or not self._active_voice:
            return None

        # Find the profile that matches current provider and voice
        for name, profile in self._profile_manager.voice_profiles.items():
            if profile.tts_provider == self._active_provider and profile.tts_voice == self._active_voice:
                return name
        return None

    def link(self, parent):
        """Link this processor to the pipeline."""
        super().link(parent)
        
        # Link any already-created TTS services to this switcher
        for service in self._tts_services.values():
            service.link(self)
