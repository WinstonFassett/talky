#!/usr/bin/env python3
"""talky say command using Pipecat TTS abstractions."""

import argparse
import asyncio
import os
import sys
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from pipecat.frames.frames import StartFrame, TTSAudioRawFrame, TTSTextFrame
from server.config.profile_manager import get_profile_manager
from shared.voice_config import create_tts_for_profile


async def say_text(
    text: str,
    voice_profile: Optional[str] = None,
    provider: Optional[str] = None,
    voice_id: Optional[str] = None,
    output_file: Optional[str] = None,
):
    """Generate speech using Pipecat TTS abstractions."""
    try:
        logger.info(f"Speaking: {text[:50]}{'...' if len(text) > 50 else ''}")

        tts_service = create_tts_for_profile(voice_profile, provider, voice_id)
        
        # Minimal setup for ElevenLabs HTTP service
        if provider == "elevenlabs":
            import asyncio
            from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams
            from pipecat.clocks.system_clock import SystemClock
            
            task_manager = TaskManager()
            task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
            
            class Setup:
                def __init__(self, task_manager, clock):
                    self.task_manager = task_manager
                    self.clock = clock
                    self.observer = None
            
            setup = Setup(task_manager, SystemClock())
            await tts_service.setup(setup)
        
        context_id = tts_service.create_context_id()
        await tts_service.start(StartFrame())

        audio_data = []
        frame_count = 0
        async for frame in tts_service.run_tts(text, context_id):
            frame_count += 1
            logger.info(f"Frame {frame_count}: {type(frame).__name__}")
            
            if hasattr(frame, 'error'):
                logger.error(f"ErrorFrame: {frame.error}")
            
            if isinstance(frame, TTSAudioRawFrame):
                if hasattr(frame, "audio") and frame.audio:
                    audio_data.append(frame.audio)
                    logger.info(f"Audio frame: {len(frame.audio)} bytes")
            elif hasattr(frame, "audio") and frame.audio:
                audio_data.append(frame.audio)
                logger.info(f"Other audio frame: {len(frame.audio)} bytes")
        
        logger.info(f"Total frames: {frame_count}, Audio frames: {len(audio_data)}")

        if not audio_data:
            logger.error("No audio data generated")
            return False

        combined_audio = b"".join(audio_data)
        logger.info(f"Generated {len(combined_audio)} bytes of audio")

        if output_file:
            with open(output_file, "wb") as f:
                f.write(combined_audio)
            logger.info(f"Audio saved to: {output_file}")
        else:
            try:
                import pyaudio

                p = pyaudio.PyAudio()
                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=tts_service.sample_rate,
                    output=True,
                )
                logger.info("Playing audio...")
                stream.write(combined_audio)
                stream.stop_stream()
                stream.close()
                p.terminate()
            except ImportError:
                logger.warning("PyAudio not available - audio generated but not played")
            except Exception as e:
                logger.error(f"Error playing audio: {e}")

        return True

    except Exception as e:
        logger.error(f"Error generating speech: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Generate speech using talky voice profiles")
    parser.add_argument("text", nargs="?", help="Text to speak")
    parser.add_argument("--voice-profile", "-p", help="Voice profile to use")
    parser.add_argument("--provider", help="TTS provider")
    parser.add_argument("--voice", help="Voice ID")
    parser.add_argument("--output-file", "-o", help="Save audio to file")
    parser.add_argument("--list-profiles", "-l", action="store_true")
    parser.add_argument("--status", "-s", action="store_true")

    args = parser.parse_args()

    pm = get_profile_manager()

    if args.list_profiles:
        print("Available Voice Profiles:")
        for name, desc in pm.list_voice_profiles().items():
            print(f"  {name}: {desc}")
        return

    if args.status:
        print(f"Default Voice Profile: {pm.get_default_voice_profile()}")
        print(f"Default LLM Backend: {pm.get_default_llm_backend()}")
        print(f"Voice Profiles: {len(pm.list_voice_profiles())}")
        print(f"LLM Backends: {len(pm.list_llm_backends())}")
        return

    if not args.text:
        parser.print_help()
        return

    success = asyncio.run(
        say_text(
            text=args.text,
            voice_profile=args.voice_profile,
            provider=args.provider,
            voice_id=args.voice,
            output_file=args.output_file,
        )
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
