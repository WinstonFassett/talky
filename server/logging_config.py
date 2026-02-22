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
    
    # Set environment variables
    os.environ["PIPECAT_LOG_LEVEL"] = log_level
    os.environ["TALKY_LOG_LEVEL"] = log_level
    
    # Suppress warnings
    warnings.filterwarnings("ignore")
    os.environ["OBJC_DISABLE_DEPRECATED_WARNINGS"] = "1"
    os.environ["PYTHONWARNINGS"] = "ignore"

    # Configure BOTH loguru AND standard Python logging
    # Loguru configuration
    logger.configure(
        handlers=[
            {
                "sink": sys.stderr,
                "format": "<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
                "level": log_level,
                "colorize": True,
            }
        ]
    )
    
    # Standard Python logging configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.getLevelName(log_level))
    
    # Remove all existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add our handler at ERROR level
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.getLevelName(log_level))
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s - %(message)s", datefmt="%H:%M:%S")
    )
    root_logger.addHandler(console_handler)
    
    # Force all existing loggers to ERROR level
    for name, logger_obj in logging.Logger.manager.loggerDict.items():
        if hasattr(logger_obj, 'setLevel'):
            try:
                logger_obj.setLevel(logging.getLevelName(log_level))
            except:
                pass
    
    # Specifically configure common web server loggers
    web_loggers = [
        'uvicorn',
        'uvicorn.access',
        'uvicorn.error',
        'fastapi',
        'uvicorn.asgi',
    ]
    
    for logger_name in web_loggers:
        logger_obj = logging.getLogger(logger_name)
        logger_obj.setLevel(logging.getLevelName(log_level))
        
        # Remove existing handlers
        for handler in logger_obj.handlers[:]:
            logger_obj.removeHandler(handler)
        
        # Add our ERROR-only handler
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.getLevelName(log_level))
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s - %(message)s", datefmt="%H:%M:%S")
        )
        logger_obj.addHandler(handler)
        # Prevent propagation to avoid duplicate logs
        logger_obj.propagate = False
