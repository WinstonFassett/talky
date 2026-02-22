"""Voice Profile Switcher — handles dynamic voice profile switching during bot runtime."""

import asyncio
import re
from typing import Dict, Optional

from loguru import logger
from shared.service_factory import create_tts_service_from_config


class VoiceProfileSwitcher:
    """Manages voice profile switching with proper validation and state management."""
    
    def __init__(self, tts_service, initial_profile: str, profile_manager):
        self.tts_service = tts_service
        self.current_profile = initial_profile
        self.pm = profile_manager
        self._lock = asyncio.Lock()
    
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
                    # Same-provider: use set_voice method
                    if hasattr(self.tts_service, 'set_voice'):
                        try:
                            self.tts_service.set_voice(new_profile.tts_voice)
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
                            f"Service: {type(self.tts_service).__name__}"
                        )
                else:
                    # Cross-provider: create new TTS service
                    try:
                        new_tts_service = create_tts_service_from_config(
                            new_profile.tts_provider, 
                            voice_id=new_profile.tts_voice
                        )
                        # Replace the current TTS service
                        self.tts_service = new_tts_service
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
                        logger.error(f"Failed to create new TTS service: {e}")
                        await rtvi.send_error_response(msg, f"Failed to switch TTS provider: {e}")
                    
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
                if hasattr(self.tts_service, 'set_voice'):
                    self.tts_service.set_voice(new_profile.tts_voice)
                    self.current_profile = profile_name
                    logger.info(f"Changed voice within {new_profile.tts_provider}: {new_profile.tts_voice}")
                    return True
                return False
            else:
                # Cross-provider: create new TTS service
                new_tts_service = create_tts_service_from_config(
                    new_profile.tts_provider, 
                    voice_id=new_profile.tts_voice
                )
                # Replace the current TTS service
                self.tts_service = new_tts_service
                self.current_profile = profile_name
                logger.info(f"Switched TTS provider from {current_profile.tts_provider} to {new_profile.tts_provider}: {new_profile.tts_voice}")
                return True
        except Exception as e:
            logger.error(f"Failed to switch profile: {e}")
            return False
