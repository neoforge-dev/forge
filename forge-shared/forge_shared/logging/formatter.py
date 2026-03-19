"""
Custom logging formatters.

Provides JSON and text formatters for structured logging.

Example:
    ```python
    import logging
    from forge_shared.logging.formatter import JSONFormatter

    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    ```
"""

import json
import logging
import time
from typing import Any


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Formats log records as JSON objects with consistent schema.

    Attributes:
        timestamp_format: Timestamp format (unix or iso)
        extra_fields: Extra field names to include from log record (list of str)

    Example:
        ```python
        handler.setFormatter(JSONFormatter())
        ```
    """

    def __init__(
        self,
        timestamp_format: str = "unix",
        extra_fields: list[str] | None = None,
    ) -> None:
        """
        Initialize JSON formatter.

        Args:
            timestamp_format: Timestamp format (unix or iso)
            extra_fields: List of extra field names to include from log record
        """
        super().__init__()
        self.timestamp_format = timestamp_format
        self.extra_fields = extra_fields or []

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON.

        Args:
            record: Log record to format

        Returns:
            JSON string
        """
        # Create base log data
        log_data: dict[str, Any] = {
            "timestamp": self._get_timestamp(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add stack trace if present
        if record.stack_info:
            log_data["stack_trace"] = self.formatStack(record.stack_info)

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
            }:
                log_data[key] = value

        return json.dumps(log_data)

    def _get_timestamp(self) -> float | str:
        """
        Get current timestamp.

        Returns:
            Timestamp in configured format
        """
        if self.timestamp_format == "iso":
            return time.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        return time.time()


class StructuredFormatter(logging.Formatter):
    """
    Structured formatter for flexible JSON logging.

    Supports field inclusion/exclusion, extra fields, and stack trace control.

    Attributes:
        format_type: Output format (currently only 'json')
        include_stack_trace: Whether to include stack traces on exceptions
        include_fields: If set, only these fields are included in output
        exclude_fields: If set, these fields are excluded from output
        extra_fields: Extra dict to merge into every log record

    Example:
        ```python
        handler.setFormatter(StructuredFormatter(
            include_fields=["timestamp", "level", "message"],
            extra_fields={"app": "myservice"},
        ))
        ```
    """

    # Standard LogRecord attributes to skip when collecting extra fields
    _STANDARD_ATTRS = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
        }
    )

    def __init__(
        self,
        format: str = "json",
        datefmt: str | None = None,
        include_stack_trace: bool = False,
        include_fields: list[str] | None = None,
        exclude_fields: list[str] | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize structured formatter.

        Args:
            format: Output format; currently only 'json' is supported
            datefmt: Optional date format string passed to formatTime
            include_stack_trace: Include stack_trace key when exc_info is present
            include_fields: Whitelist of field names to include (all if None)
            exclude_fields: Blacklist of field names to exclude
            extra_fields: Dict of extra key/value pairs merged into every output
        """
        super().__init__(datefmt=datefmt)
        self.format_type = format
        self.include_stack_trace = include_stack_trace
        self.include_fields = include_fields
        self.exclude_fields = set(exclude_fields) if exclude_fields else set()
        self.extra_fields = extra_fields or {}

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as a JSON string.

        Args:
            record: Log record to format

        Returns:
            JSON-encoded string
        """
        # Build the full data dict before applying field filters
        log_data: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
            if self.include_stack_trace:
                log_data["stack_trace"] = self.formatException(record.exc_info)

        # Include stack_info if present
        if record.stack_info:
            log_data["stack_trace"] = self.formatStack(record.stack_info)

        # Add any non-standard record attributes (e.g. from extra={…})
        for key, value in record.__dict__.items():
            if key not in self._STANDARD_ATTRS:
                log_data[key] = value

        # Merge caller-supplied extra fields (overwrite record attrs if clash)
        log_data.update(self.extra_fields)

        # Apply include_fields whitelist
        if self.include_fields is not None:
            log_data = {k: v for k, v in log_data.items() if k in self.include_fields}

        # Apply exclude_fields blacklist
        if self.exclude_fields:
            log_data = {k: v for k, v in log_data.items() if k not in self.exclude_fields}

        return json.dumps(log_data)


class TextFormatter(logging.Formatter):
    """
    Text formatter for human-readable logging.

    Formats log records as readable text with colors for console output.

    Attributes:
        use_colors: Whether to use ANSI colors
        include_timestamp: Whether to include timestamp

    Example:
        ```python
        handler.setFormatter(TextFormatter(use_colors=True))
        ```
    """

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
        "RESET": "\033[0m",  # Reset
    }

    def __init__(
        self,
        use_colors: bool = True,
        include_timestamp: bool = True,
    ) -> None:
        """
        Initialize text formatter.

        Args:
            use_colors: Use ANSI colors
            include_timestamp: Include timestamp in output
        """
        super().__init__()
        self.use_colors = use_colors
        self.include_timestamp = include_timestamp

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as text.

        Args:
            record: Log record to format

        Returns:
            Formatted text string
        """
        # Get level name with color
        level_name = record.levelname
        if self.use_colors:
            color = self.COLORS.get(level_name, "")
            reset = self.COLORS["RESET"]
            level_name = f"{color}{level_name}{reset}"

        # Build format parts
        parts = []

        if self.include_timestamp:
            parts.append(self.formatTime(record, "%Y-%m-%d %H:%M:%S"))

        parts.append(f"[{level_name}]")
        parts.append(f"{record.name}:{record.funcName}:{record.lineno}")
        parts.append(f"- {record.getMessage()}")

        return " ".join(parts)
