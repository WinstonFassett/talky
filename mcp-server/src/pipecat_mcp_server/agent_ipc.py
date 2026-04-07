#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Inter-process communication for the Pipecat MCP server.

This module manages the IPC queues and child process lifecycle for communication
between the MCP server (parent) and the Pipecat voice agent (child). The child
process runs separately to avoid stdio collisions with the MCP protocol.
"""

import asyncio
import multiprocessing
import os
import queue as queue_module
import signal
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

# Use spawn to avoid issues with forking from async context Fork copies the
# parent's state (event loop, file descriptors, locks) which can cause
# issues. Spawn creates a fresh Python interpreter.
multiprocessing.set_start_method("spawn", force=True)

_cmd_queue: Optional[multiprocessing.Queue] = None
_response_queue: Optional[multiprocessing.Queue] = None
_pipecat_process: Optional[multiprocessing.Process] = None

# Defense #5 (ticket 727e): PID file so orphans survive parent restarts.
# The child calls os.setsid() on entry (see run_pipecat_process below), which
# makes _pipecat_process.pid the pgid leader — that's what we write here, so
# sweep_orphan_pipecat() can os.killpg() the whole subtree without needing
# the parent to still be alive.
PIPECAT_RUN_DIR = Path.home() / ".talky" / "run"
PIPECAT_PID_FILE = PIPECAT_RUN_DIR / "pipecat.pid"


def _write_pid_file(pid: int) -> None:
    """Record the pipecat child's pid (== pgid) so orphans can be swept."""
    try:
        PIPECAT_RUN_DIR.mkdir(parents=True, exist_ok=True)
        PIPECAT_PID_FILE.write_text(str(pid))
        logger.debug(f"Wrote pipecat pid file: {PIPECAT_PID_FILE} = {pid}")
    except OSError as e:
        logger.warning(f"Could not write pipecat pid file: {e}")


def _clear_pid_file() -> None:
    """Remove the pipecat pid file. Idempotent."""
    try:
        PIPECAT_PID_FILE.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"Could not remove pipecat pid file: {e}")


def _pid_is_alive(pid: int) -> bool:
    """True iff a process with this pid currently exists (signal 0 probe)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't own it — treat as alive for safety;
        # we'd rather fail-closed than kill a stranger.
        return True


def sweep_orphan_pipecat() -> bool:
    """Kill any pipecat orphan from a prior parent, if a pid file points at one.

    Returns True if something was killed. Callers: `start_pipecat_process()`
    (before spawning a new child) and `server.main()` on startup. Safe to
    call with no pid file, a stale pid file, or a dead pid — each is a no-op.
    """
    if not PIPECAT_PID_FILE.exists():
        return False

    try:
        pid = int(PIPECAT_PID_FILE.read_text().strip())
    except (OSError, ValueError) as e:
        logger.warning(f"Pipecat pid file exists but is unreadable: {e}; removing")
        _clear_pid_file()
        return False

    if not _pid_is_alive(pid):
        logger.debug(f"Pipecat pid file points at dead pid {pid}; clearing")
        _clear_pid_file()
        return False

    # Safety: never killpg our own process group.
    if pid == os.getpgrp():
        logger.error(
            f"Pipecat pid file points at our own pgid ({pid}); refusing to killpg. "
            "Clearing the file so we can move on."
        )
        _clear_pid_file()
        return False

    logger.warning(f"Found orphan pipecat process pid={pid}; sending SIGTERM to pgid")
    killed_anything = False
    try:
        os.killpg(pid, signal.SIGTERM)
        killed_anything = True
    except ProcessLookupError:
        logger.debug(f"killpg({pid}, SIGTERM): pgid already gone")
    except PermissionError as e:
        logger.error(f"killpg({pid}, SIGTERM): {e}")
        _clear_pid_file()
        return False

    # Give it a moment to die, then SIGKILL if it didn't.
    import time
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not _pid_is_alive(pid):
            break
        time.sleep(0.1)

    if _pid_is_alive(pid):
        logger.warning(f"pgid {pid} survived SIGTERM; sending SIGKILL")
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    _clear_pid_file()
    return killed_anything


def _cleanup():
    """Clean up the pipecat child process."""
    global _pipecat_process, _cmd_queue, _response_queue

    logger.debug(f"Checking if Pipecat MCP Agent process is actually running...")
    if _pipecat_process:
        # Defense #2 (ticket 727e): fan out SIGTERM to the child's whole
        # process group, not just the child. _pipecat_process.pid is the pgid
        # because run_pipecat_process() below calls os.setsid() on entry.
        pid = _pipecat_process.pid
        if _pipecat_process.is_alive() and pid is not None:
            if pid == os.getpgrp():
                logger.error(
                    f"Pipecat pid {pid} equals our own pgid; skipping killpg to avoid suicide"
                )
            else:
                try:
                    logger.debug(
                        f"Sending SIGTERM to pipecat pgid {pid}"
                    )
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except PermissionError as e:
                    logger.warning(f"killpg({pid}, SIGTERM): {e}")
            _pipecat_process.join(timeout=5.0)

        # Belt-and-suspenders: fall back to Process.terminate()/kill() if the
        # pgid fan-out didn't take the process down (e.g. child never reached
        # os.setsid before dying).
        if _pipecat_process.is_alive():
            logger.debug(f"Terminating Pipecat MCP Agent process (PID {_pipecat_process.ident})")
            _pipecat_process.terminate()
            _pipecat_process.join(timeout=5.0)

        if _pipecat_process.is_alive():
            logger.debug(f"Killing Pipecat MCP Agent process (PID {_pipecat_process.ident})")
            if pid is not None and pid != os.getpgrp():
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            _pipecat_process.kill()
            _pipecat_process.join(timeout=5.0)

        logger.debug(f"Pipecat MCP Agent process stopped")
        _pipecat_process = None

    # Defense #5: clear the pid file as part of cleanup.
    _clear_pid_file()

    # Kill any stray processes on port 7860
    try:
        # Kill processes on port 7860 (bot) and their children
        port_7860_pids = subprocess.run(
            ["lsof", "-ti:7860"], capture_output=True, text=True
        ).stdout.strip()
        if port_7860_pids:
            for pid in port_7860_pids.split("\n"):
                if pid.strip():
                    # Kill the entire process group
                    try:
                        subprocess.run(["kill", "-9", f"-{pid.strip()}"], check=False)
                        subprocess.run(["kill", "-9", pid.strip()], check=False)
                        logger.debug(f"Killed process group on port 7860: PID {pid.strip()}")
                    except:
                        pass
    except Exception as e:
        logger.debug(f"Error killing stray processes: {e}")

    # Close the queues so their internal semaphores are released
    if _cmd_queue is not None:
        _cmd_queue.close()
        _cmd_queue.join_thread()
        _cmd_queue = None

    if _response_queue is not None:
        _response_queue.close()
        _response_queue.join_thread()
        _response_queue = None


def start_pipecat_process():
    """Start the Pipecat child process.

    Creates IPC queues and spawns a new process to run the Pipecat voice agent.
    Cleans up any existing process (in-memory) and any orphan from a prior
    parent (via pid file) before starting a new one.
    """
    global _cmd_queue, _response_queue, _pipecat_process

    # Sweep any orphan from a prior parent that we don't know about in memory.
    # This is the recovery path for "parent died but child kept running."
    sweep_orphan_pipecat()

    # Clean up any existing in-memory process handle.
    _cleanup()

    # Create IPC queues using spawn context
    _cmd_queue = multiprocessing.Queue()
    _response_queue = multiprocessing.Queue()

    # Start pipecat as separate process
    logger.debug(f"Starting Pipecat MCP Agent process...")
    _pipecat_process = multiprocessing.Process(
        target=run_pipecat_process,
        args=(_cmd_queue, _response_queue),
    )
    _pipecat_process.start()
    logger.debug(f"Started Pipecat MCP Agent process (PID {_pipecat_process.ident})")

    # Defense #5: record the child's pid so future parents can sweep it.
    # The child's pid == pgid because run_pipecat_process() calls os.setsid().
    if _pipecat_process.pid is not None:
        _write_pid_file(_pipecat_process.pid)



def stop_pipecat_process():
    """Stop the pipecat child process (explicit cleanup)."""
    logger.debug(f"Stopping Pipecat MCP Agent process...")
    _cleanup()
    logger.debug(f"Stopped Pipecat MCP Agent")


def run_pipecat_process(cmd_queue: multiprocessing.Queue, response_queue: multiprocessing.Queue):
    """Entry point for the Pipecat child process.

    Initializes logging and runs the Pipecat main loop. This function is called
    in a separate process to avoid stdio collisions with the MCP protocol.

    Args:
        cmd_queue: Queue for receiving commands from the MCP server.
        response_queue: Queue for sending responses back to the MCP server.

    """
    global _cmd_queue, _response_queue

    import os
    import sys

    # Become a new session leader. This has two important consequences:
    #
    # 1. Any subprocesses this child spawns (pipecat workers, etc.) inherit
    #    our pgid, so killing the pgid tears down the whole subtree.
    # 2. Our own pid == our pgid (session leader invariant). The parent
    #    relies on this in _cleanup() and sweep_orphan_pipecat() to call
    #    os.killpg(_pipecat_process.pid, ...) and reach every descendant.
    #
    # DO NOT remove this line without updating the parent-side kill code
    # above — killpg on a non-leader pid would target the wrong group and
    # could kill the parent.
    os.setsid()

    _cmd_queue = cmd_queue
    _response_queue = response_queue

    # Change to package directory so pipecat_main() can find bot.py
    package_dir = os.path.dirname(__file__)
    os.chdir(package_dir)

    # Import and run the pipecat main (which will call our bot() function)
    import sys

    from pipecat.runner.run import main as pipecat_main

    logger.debug("Pipecat MCP Agent process started. Launching Pipecat runner!")

    # Remove server/ from sys.path so Pipecat's `import bot` finds OUR
    # bot.py (in cwd) instead of server/bot.py. The workspace editable
    # install adds server/ to sys.path, and Pipecat's _get_bot_module()
    # does `import bot` which hits server/bot.py first.
    server_path = os.path.join(os.path.dirname(package_dir), os.pardir, os.pardir, "server")
    server_path = os.path.normpath(os.path.join(package_dir, "..", "..", "..", "server"))
    sys.path = [p for p in sys.path if os.path.normpath(p) != server_path]

    # Pass the right arguments for SmallWebRTC transport
    sys.argv = ["bot.py", "--transport", "webrtc", "--host", "localhost", "--port", "7860"]
    pipecat_main()

    logger.debug("Pipecat runner is done...")


async def send_response(response: dict):
    """Send a response from the child process to the MCP server.

    Args:
        response: Response dictionary to send.

    Raises:
        RuntimeError: If the Pipecat process has not been started.

    """
    if _response_queue is None:
        raise RuntimeError("Pipecat process not started")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _response_queue.put, response)


async def read_request() -> dict:
    """Read a request from the MCP server in the child process.

    Blocks until a command is available in the queue.

    Returns:
        Request dictionary containing the command and arguments.

    Raises:
        RuntimeError: If the Pipecat process has not been started.

    """
    if _cmd_queue is None:
        raise RuntimeError("Pipecat process not started")
    loop = asyncio.get_event_loop()
    request = await loop.run_in_executor(None, _cmd_queue.get)
    return request


def _get_with_timeout(queue: multiprocessing.Queue, timeout: float = 0.5):
    """Get from queue with timeout to allow cancellation.

    Args:
        queue: The queue to read from.
        timeout: Timeout in seconds.

    Returns:
        Item from the queue.

    Raises:
        TimeoutError: If the timeout expires before an item is available.

    """
    try:
        return queue.get(timeout=timeout)
    except queue_module.Empty:
        raise TimeoutError("Queue get timed out")


def _check_process_alive():
    """Check if the pipecat process is still alive."""
    if _pipecat_process and not _pipecat_process.is_alive():
        raise RuntimeError("Voice agent process has stopped")


async def _wait_for_command_response(timeout: float = 0.5) -> dict:
    """Wait for response from child process with health checks."""
    if _response_queue is None:
        raise RuntimeError("Pipecat process not started")

    loop = asyncio.get_event_loop()

    while True:
        try:
            return await loop.run_in_executor(None, _get_with_timeout, _response_queue, timeout)
        except TimeoutError:
            _check_process_alive()
            await asyncio.sleep(0)  # Yield to allow cancellation


async def send_command(cmd: str, **kwargs) -> dict:
    """Send a command to the Pipecat child process and wait for response.

    Args:
        cmd: Command name (e.g., "listen", "speak", "stop").
        **kwargs: Additional arguments for the command.

    Returns:
        Response dictionary from the child process.

    """
    if _cmd_queue is None or _response_queue is None:
        raise RuntimeError("Pipecat process not started")

    request = {"cmd": cmd, **kwargs}

    # Send request to child process
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _cmd_queue.put, request)

    # Wait for response with cancellation support
    try:
        response = await _wait_for_command_response()
    except asyncio.CancelledError:
        logger.info(f"Command '{cmd}' was cancelled")
        raise

    # Check for errors in response
    if "error" in response:
        error_message = response["error"]
        logger.error(f"Error running command '{cmd}': {error_message}")

    return response
