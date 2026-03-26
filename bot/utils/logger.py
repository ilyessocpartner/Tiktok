"""
Colored logging setup for the Polymarket trading bot.
Uses colorlog for console output with level-based colors.
"""

import logging
import os
import sys

try:
    import colorlog
    _COLORLOG_AVAILABLE = True
except ImportError:
    _COLORLOG_AVAILABLE = False

_LOG_LEVEL_ENV = os.environ.get("LOG_LEVEL", "INFO").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_ENV, logging.INFO)

# Map log levels to terminal colors
_LOG_COLORS = {
    "DEBUG": "cyan",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold_red",
}

_SECONDARY_LOG_COLORS = {
    "message": {
        "ERROR": "red",
        "CRITICAL": "bold_red",
        "WARNING": "yellow",
    }
}

_FORMAT = "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Cache so each logger name is only configured once
_configured_loggers: set = set()


def setup_logger(name: str) -> logging.Logger:
    """
    Return a logger with the given name, configured with colored console output.

    The log level is read from the LOG_LEVEL environment variable (default INFO).
    If colorlog is not installed, falls back to plain logging with the same format.

    Args:
        name: Logger name (typically __name__ of the calling module).

    Returns:
        A configured logging.Logger instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if called multiple times for the same name
    if name in _configured_loggers or logger.handlers:
        logger.setLevel(_LOG_LEVEL)
        return logger

    logger.setLevel(_LOG_LEVEL)
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(_LOG_LEVEL)

    if _COLORLOG_AVAILABLE:
        formatter = colorlog.ColoredFormatter(
            fmt="[%(asctime)s] [%(log_color)s%(levelname)-8s%(reset)s] [%(name)s] %(message_log_color)s%(message)s%(reset)s",
            datefmt=_DATE_FORMAT,
            log_colors=_LOG_COLORS,
            secondary_log_colors=_SECONDARY_LOG_COLORS,
            reset=True,
            style="%",
        )
    else:
        formatter = logging.Formatter(fmt=_FORMAT, datefmt=_DATE_FORMAT)

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    _configured_loggers.add(name)

    return logger
