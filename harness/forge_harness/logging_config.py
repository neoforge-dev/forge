"""
Structured Logging Configuration for FORGE Harness
===================================================

Provides consistent, structured logging across all harness modules.

Usage:
    from forge_harness.logging_config import get_logger, configure_logging

    # Configure once at application startup
    configure_logging(level="INFO", json_format=True)

    # Get module-specific logger
    logger = get_logger(__name__)

    # Log with structured context
    logger.info("Processing content", extra={"domain": "codeswiftr", "tier": 1})
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging output.

    Produces JSON lines format suitable for log aggregation tools.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add source location
        if record.pathname:
            log_data["source"] = {
                "file": record.filename,
                "line": record.lineno,
                "function": record.funcName,
            }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add any extra fields (structured context)
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "exc_info",
                "exc_text",
                "thread",
                "threadName",
                "taskName",
                "message",
            ):
                log_data[key] = value

        return json.dumps(log_data, default=str)


class ConsoleFormatter(logging.Formatter):
    """
    Human-readable console formatter with colors.

    Used for development and interactive sessions.
    """

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors and context."""
        color = self.COLORS.get(record.levelname, "")

        # Build context string from extra fields
        context_parts = []
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "exc_info",
                "exc_text",
                "thread",
                "threadName",
                "taskName",
                "message",
            ):
                context_parts.append(f"{key}={value}")

        context_str = " ".join(context_parts)
        if context_str:
            context_str = f" [{context_str}]"

        timestamp = datetime.now(UTC).strftime("%H:%M:%S")
        message = record.getMessage()

        formatted = f"{color}[{timestamp}] {record.levelname:8}{self.RESET} {record.name}: {message}{context_str}"

        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)

        return formatted


# Module-level state
_configured = False
_json_format = False


def configure_logging(
    level: str = "INFO",
    json_format: bool = False,
    stream: Any = None,
) -> None:
    """
    Configure logging for the forge_harness package.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: If True, output JSON lines; if False, human-readable
        stream: Output stream (defaults to sys.stderr)

    Example:
        # Development (colored console output)
        configure_logging(level="DEBUG", json_format=False)

        # Production (JSON for log aggregation)
        configure_logging(level="INFO", json_format=True)
    """
    global _configured, _json_format

    if stream is None:
        stream = sys.stderr

    # Get the forge_harness root logger
    root_logger = logging.getLogger("forge_harness")
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create appropriate handler
    handler = logging.StreamHandler(stream)
    handler.setLevel(getattr(logging, level.upper()))

    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(ConsoleFormatter())

    root_logger.addHandler(handler)

    # Prevent propagation to root logger to avoid duplicate logs
    root_logger.propagate = False

    _configured = True
    _json_format = json_format


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for the specified module.

    Args:
        name: Usually __name__ of the calling module

    Returns:
        Logger instance configured for forge_harness

    Example:
        logger = get_logger(__name__)
        logger.info("Processing", extra={"domain": "test"})
    """
    # Ensure logging is configured with defaults
    if not _configured:
        configure_logging()

    return logging.getLogger(name)


class LogContext:
    """
    Context manager for adding structured context to logs.

    Example:
        with LogContext(logger, domain="codeswiftr", tier=1):
            logger.info("Processing content")
            # Logs will include domain and tier
    """

    def __init__(self, logger: logging.Logger, **context: Any):
        self.logger = logger
        self.context = context
        self._old_factory: Any = None

    def __enter__(self) -> "LogContext":
        """Add context to log records."""
        old_factory = logging.getLogRecordFactory()
        context = self.context

        def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = old_factory(*args, **kwargs)
            for key, value in context.items():
                setattr(record, key, value)
            return record

        self._old_factory = old_factory
        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, *args: Any) -> None:
        """Restore original record factory."""
        if self._old_factory:
            logging.setLogRecordFactory(self._old_factory)


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    **context: Any,
) -> None:
    """
    Log a message with structured context.

    Args:
        logger: Logger instance
        level: Log level (e.g., logging.INFO)
        message: Log message
        **context: Additional context fields

    Example:
        log_with_context(logger, logging.INFO, "Task completed",
                        task_id="123", duration=1.5)
    """
    logger.log(level, message, extra=context)


# Convenience functions for common log patterns
def log_task_start(logger: logging.Logger, task_name: str, **context: Any) -> None:
    """Log the start of a task."""
    log_with_context(logger, logging.INFO, f"Starting: {task_name}", task=task_name, **context)


def log_task_complete(
    logger: logging.Logger, task_name: str, duration: float | None = None, **context: Any
) -> None:
    """Log successful task completion."""
    extra = {"task": task_name, **context}
    if duration is not None:
        extra["duration_sec"] = duration
    log_with_context(logger, logging.INFO, f"Completed: {task_name}", **extra)


def log_task_error(
    logger: logging.Logger, task_name: str, error: Exception, **context: Any
) -> None:
    """Log task failure."""
    log_with_context(
        logger,
        logging.ERROR,
        f"Failed: {task_name} - {error}",
        task=task_name,
        error_type=type(error).__name__,
        **context,
    )
