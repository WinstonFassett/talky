"""
Pi LLM Service for Pipecat
Spawns pi --mode rpc and handles voice-specific events
"""

import asyncio
import json
import os
from typing import Optional

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from server.config.voice_prompts import format_voice_message


class PiLLMService(LLMService):
    """Pi LLM service - RPC subprocess-based implementation for voice"""

    def __init__(self, *, pi_binary: str = "pi", working_dir: str = ".", **kwargs):
        super().__init__(**kwargs)

        self.pi_binary = pi_binary
        self.working_dir = working_dir

        # Process management
        self._pi_process = None
        self._stdout_task = None

        # Response handling
        self._response_queue = asyncio.Queue()
        self._processing_response = False

        logger.info(f"PiLLMService initialized with binary: {self.pi_binary}")

    async def _start_pi(self):
        """Start pi subprocess in RPC mode"""
        if self._pi_process:
            return

        logger.info(f"🚀 Starting pi subprocess: {self.pi_binary} --mode rpc")

        try:
            self._pi_process = await asyncio.create_subprocess_exec(
                self.pi_binary,
                "--mode",
                "rpc",
                cwd=self.working_dir,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Start stdout reader
            self._stdout_task = asyncio.create_task(self._handle_stdout())

            logger.info("✅ Pi subprocess started")

        except Exception as e:
            logger.error(f"Failed to start pi subprocess: {e}")
            raise

    async def _handle_stdout(self):
        """Handle stdout from pi process - parse JSON lines"""
        try:
            while self._pi_process and self._pi_process.returncode is None:
                line = await self._pi_process.stdout.readline()
                if not line:
                    break

                line = line.decode().strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    await self._handle_pi_event(data)
                except json.JSONDecodeError:
                    logger.debug(f"Non-JSON stdout: {line}")
                except Exception as e:
                    logger.error(f"Error handling pi event: {e}")

        except Exception as e:
            logger.error(f"Stdout handler error: {e}")

    async def _handle_pi_event(self, data: dict):
        """Handle events from pi RPC"""
        event_type = data.get("type")

        if event_type == "tool_execution_start":
            tool_name = data.get("toolName")
            if tool_name == "speak":
                args = data.get("args", {})
                text = args.get("text", "")
                if text:
                    logger.info(f"🔊 Pi wants to speak: {text[:50]}...")
                    # Put text in response queue for TTS
                    await self._response_queue.put(text)
                else:
                    logger.warning("speak tool called without text")

        elif event_type == "agent_end":
            logger.info("🏁 Pi agent finished")
            # Put sentinel to end response
            await self._response_queue.put("__END__")

        # Log other events for debugging
        else:
            logger.debug(f"Pi event: {event_type}")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames - handle LLMContextFrame like other backends"""
        await super().process_frame(frame, direction)

        # Handle LLMContextFrame - don't push it, just process it
        if isinstance(frame, LLMContextFrame):
            context = frame.context
            await self._process_context(context)
        # For all other frames, push them along
        elif not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)

    async def _process_context(self, context: LLMContext):
        """Process LLM context - send to pi and handle speak events"""
        try:
            # Ensure pi is running
            if not self._pi_process:
                await self._start_pi()

            await self.push_frame(LLMFullResponseStartFrame())

            # Get messages from context
            messages = context.get_messages()

            # Find last user message
            last_user_message = None
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for item in content:
                            if item.get("type") == "text":
                                last_user_message = item.get("text", "")
                                break
                    else:
                        last_user_message = content
                    break

            if not last_user_message:
                logger.warning("No user message found")
                await self.push_frame(LLMFullResponseEndFrame())
                return

            # Format message with voice conversation guidance
            full_message = format_voice_message(last_user_message)

            logger.info(f"🗣️  User: {last_user_message[:100]}...")

            # Clear response queue
            while not self._response_queue.empty():
                try:
                    self._response_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Send prompt to pi
            prompt = {"type": "prompt", "message": full_message}

            prompt_json = json.dumps(prompt) + "\n"
            self._pi_process.stdin.write(prompt_json.encode())
            await self._pi_process.stdin.drain()

            logger.info("📤 Sent prompt to pi")

            # Collect speak responses until agent_end
            self._processing_response = True
            while self._processing_response:
                try:
                    response = await asyncio.wait_for(
                        self._response_queue.get(),
                        timeout=30.0,  # 30 second timeout
                    )

                    if response == "__END__":
                        break

                    # Push text to TTS
                    await self.push_frame(TextFrame(response))

                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for pi response")
                    break
                except Exception as e:
                    logger.error(f"Error processing pi response: {e}")
                    break

            self._processing_response = False
            await self.push_frame(LLMFullResponseEndFrame())

        except Exception as e:
            logger.error(f"Error in _process_context: {e}", exc_info=True)
            self._processing_response = False
            await self.push_frame(LLMFullResponseEndFrame())

    async def cleanup(self):
        """Clean up resources"""
        logger.info("🧹 Cleaning up PiLLMService")

        # Cancel stdout task
        if self._stdout_task:
            self._stdout_task.cancel()
            try:
                await self._stdout_task
            except asyncio.CancelledError:
                pass

        # Terminate pi process
        if self._pi_process:
            try:
                self._pi_process.terminate()
                await self._pi_process.wait()
                logger.info("✅ Pi subprocess terminated")
            except Exception as e:
                logger.error(f"Error terminating pi process: {e}")
                try:
                    self._pi_process.kill()
                    await self._pi_process.wait()
                except:
                    pass
