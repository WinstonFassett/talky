"""
Proper logging configuration for Pipecat - handles loguru correctly
"""

import logging
import os
import sys
import warnings

from loguru import logger


def setup_essential_logging():
    """Properly configure loguru to block Pipecat noise."""

    # Suppress warnings
    warnings.filterwarnings("ignore")
    os.environ["OBJC_DISABLE_DEPRECATED_WARNINGS"] = "1"
    os.environ["PYTHONWARNINGS"] = "ignore"

    # Remove ALL existing loguru handlers (this is the key!)
    logger.remove()

    # Add our filtered handler
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{extra[absolute_path]}:{line}</cyan> - <level>{message}</level>",
        level="WARNING",  # Only warnings and above by default
        filter=lambda record: _pipecat_filter(record),
        colorize=True,
    )

    # Also configure standard Python logging to be quiet
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)

    # Remove all standard logging handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add a simple handler for standard logging
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    )
    root_logger.addHandler(console_handler)


def _pipecat_filter(record):
    """Filter that blocks most Pipecat noise but keeps important stuff."""

    # Always allow errors and warnings
    if record["level"].name in ["ERROR", "WARNING", "CRITICAL"]:
        return True

    # Block ALL debug messages
    if record["level"].name == "DEBUG":
        return False

    # For INFO messages, be very selective
    if record["level"].name == "INFO":
        message = record.get("message", "")
        module = record.get("name", "")

        # Block all the noisy Pipecat modules
        if any(
            noisy in module
            for noisy in [
                "pipecat.transports.smallwebrtc",
                "pipecat.transports.smallwebrtc.connection",
                "pipecat.transports.smallwebrtc.request_handler",
                "pipecat.transports.smallwebrtc.transport",
                "pipecat.processors.frame_processor",
                "pipecat.services.google.tts",
                "pipecat.services.deepgram.stt",
                "pipecat.audio.vad.silero",
                "pipecat.audio.turn.smart_turn",
                "pipecat.audio.turn.smart_turn.local_smart_turn_v3",
                "pipecat.processors.aggregators",
                "pipecat.processors.aggregators.llm_response_universal",
                "pipecat.processors.metrics",
                "pipecat.processors.metrics.frame_processor_metrics",
                "pipecat.pipeline.runner",
                "pipecat.pipeline.task",
                "pipecat.transports.base_output",
                "pipecat.services.tts_service",
                "pipecat.services.stt_service",
                "pipecat.runner.run",
                "pipecat.processors.frameworks.rtvi",
            ]
        ):
            return False

        # Block specific noisy patterns
        blocked_patterns = [
            "Adding remote candidate",
            "ICE connection state",
            "Track audio received",
            "Track video received",
            "Linking",
            "PipelineTask#",
            "usage characters",
            "TTFB:",
            "processing time:",
            "cleaning up TTS context",
            "Generating TTS",
            "Websocket connection initialized",
            "User started speaking",
            "User stopped speaking",
            "Bot started speaking",
            "Bot stopped speaking",
            "Loading Silero VAD model",
            "Loaded Silero VAD",
            "Loading Local Smart Turn",
            "Loaded Local Smart Turn",
            "Setting VAD params to:",
            "End of Turn result:",
            "analyze_end_of_turn",
            "append_audio",
            "_on_user_turn_started",
            "_on_user_turn_stopped",
            "webrtc_connection_callback executed successfully",
            "Received client-ready",
            "Client Details",
            "Received app message inside",
            "StartFrame#",
            "reached the end of the pipeline",
            "Runner PipelineRunner#",
            "started running PipelineTask#",
            "_wait_for_pipeline_start",
            "_source_push_frame",
            "received interruption task frame",
            "📝 Streaming:",
            "📨 Received message:",
            "✅ Request succeeded:",
            "🗣️  User:",
            "🤖 Response:",
        ]

        for pattern in blocked_patterns:
            if pattern in message:
                return False

        # Only allow specific important messages
        allowed_patterns = [
            "Starting bot with profile",
            "Client connected",
            "Client disconnected",
            "✅ Final response:",
            "✅ Connected to OpenClaw",
            "🔌 Connecting to OpenClaw",
            "❌ Request failed:",
            "⏰ Timeout waiting",
            "Message handler error",
        ]

        return any(pattern in message for pattern in allowed_patterns)

    return False


def setup_minimal_logging():
    """Minimal logging - only errors."""
    warnings.filterwarnings("ignore")

    # Remove all loguru handlers
    logger.remove()

    # Add minimal handler
    logger.add(sys.stderr, format="{time:HH:mm:ss} | {level} | {message}", level="ERROR")

    # Configure standard logging
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.ERROR)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.ERROR)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    )
    root_logger.addHandler(console_handler)


def setup_debug_logging():
    """Debug logging - show everything (not recommended)."""
    # Remove all loguru handlers
    logger.remove()

    # Add debug handler
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{extra[absolute_path]}:{function}:{line}</cyan> - <level>{message}</level>",
        level="DEBUG",
    )

    # Configure standard logging for debug
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(pathname)s:%(funcName)s:%(lineno)d - %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root_logger.addHandler(console_handler)
