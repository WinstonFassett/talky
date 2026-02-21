"""
Proper logging configuration for Pipecat - handles loguru correctly
"""

import logging
import os
import sys
import warnings

from loguru import logger


def setup_essential_logging():
    """Simple logging setup that respects log levels."""

    # Suppress warnings
    warnings.filterwarnings("ignore")
    os.environ["OBJC_DISABLE_DEPRECATED_WARNINGS"] = "1"
    os.environ["PYTHONWARNINGS"] = "ignore"

    # Remove ALL existing loguru handlers
    logger.remove()

    # Add simple handler with log level control
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        level="WARNING",  # Default to WARNING level
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


def configure_logging():
    """Simple logging setup that respects log levels."""
    import os
    level = os.getenv("TALKY_LOG_LEVEL", "ERROR")
    
    # Remove all handlers
    logger.remove()
    
    # Add simple handler with just log level control
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        level=level,
        colorize=True,
    )


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
