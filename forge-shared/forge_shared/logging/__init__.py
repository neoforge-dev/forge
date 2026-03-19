"""
Logging module for structured logging.

Provides structured logging with context, custom formatters, and
request context injection.

Example:
    ```python
    from forge_shared.logging import setup_logging, get_logger

    setup_logging(log_level="INFO")
    logger = get_logger(__name__)
    logger.info("Application started")
    ```
"""

from forge_shared.logging.context import LoggingContext, get_logging_context
from forge_shared.logging.formatter import JSONFormatter, StructuredFormatter, TextFormatter
from forge_shared.logging.middleware import LoggingMiddleware

__all__ = [
    "setup_logging",
    "get_logger",
    "JSONFormatter",
    "StructuredFormatter",
    "TextFormatter",
    "LoggingContext",
    "get_logging_context",
    "LoggingMiddleware",
]

import logging
import sys


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    handlers: list[logging.Handler] | None = None,
) -> None:
    """
    Setup logging configuration.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Log format (json or text)
        handlers: Optional list of additional handlers

    Example:
        ```python
        setup_logging(log_level="DEBUG", log_format="text")
        ```
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create handler
    handler = logging.StreamHandler(sys.stdout)

    # Set formatter
    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(TextFormatter())

    root_logger.addHandler(handler)

    # Add additional handlers
    if handlers:
        for h in handlers:
            root_logger.addHandler(h)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Logger instance

    Example:
        ```python
        logger = get_logger(__name__)
        logger.info("Hello, world!")
        ```
    """
    return logging.getLogger(name)
