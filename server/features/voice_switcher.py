"""Voice Profile Switcher — handles voice profile switching using Pipecat ServiceSwitcher."""

import asyncio
import re
from typing import Dict, Optional

from loguru import logger
from pipecat.frames.frames import ManuallySwitchServiceFrame
from pipecat.pipeline.service_switcher import ServiceSwitcher, ServiceSwitcherStrategyManual
from shared.service_factory import create_tts_service_from_config


class VoiceProfileSwitcher:
    """Manages voice profile switching with proper validation and ServiceSwitcher integration."""
    
    def __init__(self, initial_profile: str, profile_manager, task=None):
        self.current_profile = initial_profile
        self.pm = profile_manager
        self.task = task
        self._lock = asyncio.Lock()
        
        # Bootstrap all TTS services and create ServiceSwitcher
        self.tts_service_map = self._bootstrap_tts_services()
        
        # Get the initial service for the requested profile
        initial_profile_obj = self.pm.get_voice_profile(initial_profile)
        if not initial_profile_obj:
            raise ValueError(f"Initial voice profile not found: {initial_profile}")
        
        initial_service = self.tts_service_map.get(initial_profile_obj.tts_provider)
        if not initial_service:
            raise ValueError(f"TTS service not available for initial profile: {initial_profile_obj.tts_provider}")
        
        # Create ServiceSwitcher with initial service as first in list
        services = list(self.tts_service_map.values())
        # Move initial service to front
        services.remove(initial_service)
        services.insert(0, initial_service)
        
        self.tts_switcher = ServiceSwitcher(
            services=services, 
            strategy_type=ServiceSwitcherStrategyManual
        )
        logger.info(f"Created TTS switcher with {len(self.tts_service_map)} services, starting with {initial_profile_obj.tts_provider}")
    
    def set_task(self, task):
        """Set the task reference (needed for ManuallySwitchServiceFrame)."""
        self.task = task
    
    def _bootstrap_tts_services(self) -> Dict[str, any]:
        """Create TTS services for all providers that have profiles AND valid credentials."""
        tts_services = {}
        
        # Get all unique TTS providers from voice profiles
        all_profiles = self.pm.list_voice_profiles()
        unique_providers = set()
        for profile_name in all_profiles:
            profile = self.pm.get_voice_profile(profile_name)
            if profile:
                unique_providers.add(profile.tts_provider)
        
        # Try to create TTS services for all providers
        for provider in unique_providers:
            try:
                # Get a profile for this provider to get default voice
                provider_profile = None
                for profile_name in all_profiles:
                    profile = self.pm.get_voice_profile(profile_name)
                    if profile and profile.tts_provider == provider:
                        provider_profile = profile
                        break
                
                if provider_profile:
                    service = create_tts_service_from_config(
                        provider, 
                        voice_id=provider_profile.tts_voice
                    )
                    tts_services[provider] = service
                    logger.info(f"Created TTS service for {provider}: {type(service).__name__}")
            except ValueError as e:
                if "Credentials required" in str(e):
                    logger.warning(f"Provider {provider} has profiles but credentials missing - switching to this provider will not be available")
                else:
                    logger.error(f"Failed to create TTS service for {provider}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error creating TTS service for {provider}: {e}")
        
        if not tts_services:
            raise ValueError("No TTS services could be created. Check credentials and configuration.")
        
        return tts_services
    
    def get_service_switcher(self):
        """Get the ServiceSwitcher instance for pipeline use."""
        return self.tts_switcher
    
    async def handle_message(self, rtvi, msg) -> None:
        """Handle RTVI client messages for voice profile control."""
        logger.debug(f"Received voice switcher message: {msg.type}")
        
        if msg.type == "getVoiceProfiles":
            await self._handle_get_voice_profiles(rtvi, msg)
        elif msg.type == "getCurrentVoiceProfile":
            await self._handle_get_current_voice_profile(rtvi, msg)
        elif msg.type == "setVoiceProfile":
            await self._handle_set_voice_profile(rtvi, msg)
        else:
            await rtvi.send_error_response(msg, f"Unknown message type: {msg.type}")
    
    async def _handle_get_voice_profiles(self, rtvi, msg) -> None:
        """Handle request to list available voice profiles."""
        try:
            profiles = self.pm.list_voice_profiles()
            await rtvi.send_server_response(msg, {
                "type": "voiceProfiles",
                "data": [
                    {"name": name, "description": desc}
                    for name, desc in profiles.items()
                ],
                "status": "success"
            })
            logger.info(f"Sent {len(profiles)} voice profiles to client")
        except Exception as e:
            logger.error(f"Error in getVoiceProfiles: {e}")
            await rtvi.send_error_response(msg, f"Failed to get voice profiles: {e}")
    
    async def _handle_get_current_voice_profile(self, rtvi, msg) -> None:
        """Handle request to get current voice profile."""
        try:
            profile = self.pm.get_voice_profile(self.current_profile)
            if not profile:
                await rtvi.send_error_response(msg, f"Voice profile not found: {self.current_profile}")
                return

            await rtvi.send_server_response(msg, {
                "type": "currentVoiceProfile",
                "data": {
                    "name": profile.name,
                    "description": profile.description
                },
                "status": "success"
            })
            logger.debug(f"Current voice profile: {self.current_profile}")
        except Exception as e:
            logger.error(f"Error in getCurrentVoiceProfile: {e}")
            await rtvi.send_error_response(msg, f"Failed to get current voice profile: {e}")
    
    async def _handle_set_voice_profile(self, rtvi, msg) -> None:
        """Handle request to switch to a new voice profile."""
        async with self._lock:
            try:
                profile_name = msg.data.get("profileName")
                
                # Input validation
                if not profile_name or not isinstance(profile_name, str):
                    await rtvi.send_error_response(msg, "Invalid profile name: must be a non-empty string")
                    return
                
                # Validate profile name format (alphanumeric, hyphens, underscores only)
                if not re.match(r'^[a-zA-Z0-9_-]+$', profile_name):
                    await rtvi.send_error_response(msg, "Invalid profile name format")
                    return
                
                # Length limit to prevent abuse
                if len(profile_name) > 50:
                    await rtvi.send_error_response(msg, "Profile name too long")
                    return
                
                new_profile = self.pm.get_voice_profile(profile_name)
                if not new_profile:
                    await rtvi.send_error_response(msg, f"Voice profile not found: {profile_name}")
                    return

                current_profile = self.pm.get_voice_profile(self.current_profile)
                if not current_profile:
                    await rtvi.send_error_response(msg, f"Current voice profile not found: {self.current_profile}")
                    return

                # Handle both same-provider and cross-provider switching
                if new_profile.tts_provider == current_profile.tts_provider:
                    # Same-provider: use set_voice method on current service
                    current_tts_service = self.tts_service_map.get(current_profile.tts_provider)
                    if current_tts_service and hasattr(current_tts_service, 'set_voice'):
                        try:
                            current_tts_service.set_voice(new_profile.tts_voice)
                            self.current_profile = profile_name
                            logger.info(f"Changed voice within {new_profile.tts_provider}: {new_profile.tts_voice}")
                            
                            await rtvi.send_server_response(msg, {
                                "type": "voiceProfileSet",
                                "data": {
                                    "name": new_profile.name,
                                    "description": new_profile.description
                                },
                                "status": "success"
                            })
                        except Exception as e:
                            logger.error(f"Failed to set voice: {e}")
                            await rtvi.send_error_response(msg, f"Failed to change voice: {e}")
                    else:
                        await rtvi.send_error_response(
                            msg, 
                            f"Current TTS service doesn't support voice changes. "
                            f"Service: {type(current_tts_service).__name__ if current_tts_service else 'Unknown'}"
                        )
                else:
                    # Cross-provider: switch using ServiceSwitcher
                    if new_profile.tts_provider in self.tts_service_map:
                        try:
                            new_tts_service = self.tts_service_map[new_profile.tts_provider]
                            # Set the voice on the new service
                            if hasattr(new_tts_service, 'set_voice'):
                                new_tts_service.set_voice(new_profile.tts_voice)
                            
                            # Use ServiceSwitcher to properly switch the service
                            await self.task.queue_frames([ManuallySwitchServiceFrame(service=new_tts_service)])
                            
                            # Update current profile tracking
                            self.current_profile = profile_name
                            
                            logger.info(f"Switched TTS provider from {current_profile.tts_provider} to {new_profile.tts_provider}: {new_profile.tts_voice}")
                            
                            await rtvi.send_server_response(msg, {
                                "type": "voiceProfileSet",
                                "data": {
                                    "name": new_profile.name,
                                    "description": new_profile.description
                                },
                                "status": "success"
                            })
                        except Exception as e:
                            logger.error(f"Failed to switch TTS provider: {e}")
                            await rtvi.send_error_response(msg, f"Failed to switch TTS provider: {e}")
                    else:
                        await rtvi.send_error_response(
                            msg, 
                            f"TTS service for {new_profile.tts_provider} not available. "
                            f"Available providers: {list(self.tts_service_map.keys())}. "
                            f"Make sure credentials are set up in ~/.talky/credentials/{new_profile.tts_provider}.json"
                        )
                    
            except Exception as e:
                logger.error(f"Error in setVoiceProfile: {e}")
                await rtvi.send_error_response(msg, f"Failed to set voice profile: {e}")
    
    def get_current_profile(self) -> str:
        """Get the current voice profile name."""
        return self.current_profile
    
    async def switch_profile(self, profile_name: str) -> bool:
        """Direct method to switch voice profile (for testing or internal use)."""
        try:
            new_profile = self.pm.get_voice_profile(profile_name)
            if not new_profile:
                return False
            
            current_profile = self.pm.get_voice_profile(self.current_profile)
            if not current_profile:
                return False
            
            # Handle both same-provider and cross-provider switching
            if new_profile.tts_provider == current_profile.tts_provider:
                # Same-provider: use set_voice method
                current_tts_service = self.tts_service_map.get(current_profile.tts_provider)
                if current_tts_service and hasattr(current_tts_service, 'set_voice'):
                    current_tts_service.set_voice(new_profile.tts_voice)
                    self.current_profile = profile_name
                    logger.info(f"Changed voice within {new_profile.tts_provider}: {new_profile.tts_voice}")
                    return True
                return False
            else:
                # Cross-provider: switch using ServiceSwitcher
                if new_profile.tts_provider in self.tts_service_map:
                    new_tts_service = self.tts_service_map[new_profile.tts_provider]
                    # Set the voice on the new service
                    if hasattr(new_tts_service, 'set_voice'):
                        new_tts_service.set_voice(new_profile.tts_voice)
                    
                    # Use ServiceSwitcher to properly switch the service
                    await self.task.queue_frames([ManuallySwitchServiceFrame(service=new_tts_service)])
                    
                    # Update current profile tracking
                    self.current_profile = profile_name
                    logger.info(f"Switched TTS provider from {current_profile.tts_provider} to {new_profile.tts_provider}: {new_profile.tts_voice}")
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to switch profile: {e}")
            return False
