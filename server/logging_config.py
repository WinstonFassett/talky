"""
Simple logging configuration - defaults to ERROR, respects CLI/env/config.
"""

import logging
import os
import sys
import warnings

from loguru import logger


def setup_logging(level=None):
    """Setup logging with proper level defaults.
    
    Priority: CLI arg > env var > config > ERROR
    
    Args:
        level: Log level from CLI argument (optional)
    """
    # Determine log level with proper priority
    if level:
        log_level = level
    else:
        log_level = os.getenv("TALKY_LOG_LEVEL", "ERROR")
    
    # Suppress warnings
    warnings.filterwarnings("ignore")
    os.environ["OBJC_DISABLE_DEPRECATED_WARNINGS"] = "1"
    os.environ["PYTHONWARNINGS"] = "ignore"

    # Remove all existing handlers
    logger.remove()
    
    # Add single loguru handler
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True,
    )

    # Configure standard Python logging to match
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.getLevelName(log_level))
    
    # Remove all standard logging handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add single standard logging handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.getLevelName(log_level))
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    )
    root_logger.addHandler(console_handler)
