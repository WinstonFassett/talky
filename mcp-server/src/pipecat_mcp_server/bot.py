#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Bot entry point for the Pipecat MCP server.

This module is discovered by the Pipecat runner and provides the bot()
function that processes voice commands from the MCP server.
"""

from loguru import logger
from pipecat.runner.types import RunnerArguments

from pipecat_mcp_server.agent import create_agent
from pipecat_mcp_server.agent_ipc import read_request, send_response


async def bot(runner_args: RunnerArguments):
    """Start the Pipecat agent.

    Creates the voice agent and runs a command loop that processes requests
    from the MCP server via IPC queues. This function runs in the child process
    spawned by `agent_ipc.start_pipecat_process()`.

    Supported commands:
        listen: Wait for user speech, respond with `{"text": "..."}`.
        speak: Speak the provided text, respond with `{"ok": True}`.
        stop: Stop the agent and exit the loop, respond with `{"ok": True}`.

    Args:
        runner_args: Configuration from the Pipecat runner specifying
            transport type and connection settings.

    """
    try:
        logger.info("Creating Pipecat MCP Agent...")
        # Create and start the agent
        agent = await create_agent(runner_args)
        logger.info("Starting Pipecat MCP Agent pipeline...")
        await agent.start()
        logger.info("Pipecat MCP Agent pipeline started successfully")
    except Exception as e:
        logger.error(f"Failed to start Pipecat MCP Agent: {e}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise

    logger.info("Voice agent started, processing commands...")

    while True:
        # Get command (blocking call run in executor to not block the event loop)
        request = await read_request()
        cmd = request.get("cmd")

        logger.debug(f"Command '{cmd}' received, processing...")

        try:
            if cmd == "listen":
                result = await agent.listen()
                await send_response(result)
                logger.debug(f"Command '{cmd}' finished, returning: {result['text']}")
            elif cmd == "speak":
                await agent.speak(request["text"])
                await send_response({"ok": True})
                logger.debug(f"Command '{cmd}' finished")
            elif cmd == "stop":
                await agent.stop()
                await send_response({"ok": True})
                logger.debug(f"Command '{cmd}' finished")
            else:
                await send_response({"error": f"Unknown command: {cmd}"})
        except Exception as e:
            logger.warning(f"Error processing command '{cmd}': {e}")
            await send_response({"text": str(e)})
            # Don't break on disconnect errors, just continue the loop
            if "disconnected" not in str(e).lower():
                break
