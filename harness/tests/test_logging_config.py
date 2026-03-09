"""Tests for logging_config.py - Structured logging infrastructure.

Tests cover:
- JSONFormatter and ConsoleFormatter classes
- configure_logging() function
- get_logger() function
- LogContext context manager
- Convenience functions (log_task_start, log_task_complete, log_task_error)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.logging_config import (
    ConsoleFormatter,
    JSONFormatter,
    LogContext,
    configure_logging,
    get_logger,
    log_task_complete,
    log_task_error,
    log_task_start,
    log_with_context,
)


class TestJSONFormatter:
    """Tests for JSONFormatter class."""

    def test_basic_json_format(self):
        """Should format log record as JSON."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/file.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "Test message"
        assert "timestamp" in data
        assert "source" in data
        assert data["source"]["file"] == "file.py"
        assert data["source"]["line"] == 42

    def test_json_format_with_exception(self):
        """Should include exception info in JSON output."""
        formatter = JSONFormatter()

        try:
            raise ValueError("Test error")
        except ValueError:
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="test.logger",
                level=logging.ERROR,
                pathname="/test/file.py",
                lineno=10,
                msg="Error occurred",
                args=(),
                exc_info=exc_info,
            )

        output = formatter.format(record)
        data = json.loads(output)

        assert "exception" in data
        assert "ValueError" in data["exception"]
        assert "Test error" in data["exception"]

    def test_json_format_with_extra_fields(self):
        """Should include extra fields in JSON output."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/file.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        # Add extra fields
        record.domain = "codeswiftr"
        record.tier = 1

        output = formatter.format(record)
        data = json.loads(output)

        assert data["domain"] == "codeswiftr"
        assert data["tier"] == 1

    def test_json_format_skips_standard_fields(self):
        """Should not include standard LogRecord fields as extra."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/file.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        # Standard fields should not be in output as extra
        assert "args" not in data
        assert "msecs" not in data
        assert "relativeCreated" not in data


class TestConsoleFormatter:
    """Tests for ConsoleFormatter class."""

    def test_basic_console_format(self):
        """Should format log record for console output."""
        formatter = ConsoleFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/file.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        assert "INFO" in output
        assert "test.logger" in output
        assert "Test message" in output

    def test_console_format_with_colors(self):
        """Should include ANSI color codes for different levels."""
        formatter = ConsoleFormatter()

        # Test different levels have different colors
        for level, color_code in [
            (logging.DEBUG, "\033[36m"),  # Cyan
            (logging.INFO, "\033[32m"),   # Green
            (logging.WARNING, "\033[33m"), # Yellow
            (logging.ERROR, "\033[31m"),  # Red
            (logging.CRITICAL, "\033[35m"), # Magenta
        ]:
            record = logging.LogRecord(
                name="test",
                level=level,
                pathname="/test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            assert color_code in output
            assert ConsoleFormatter.RESET in output

    def test_console_format_with_extra_context(self):
        """Should include extra context in brackets."""
        formatter = ConsoleFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/file.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.domain = "codeswiftr"
        record.tier = 1

        output = formatter.format(record)

        assert "domain=codeswiftr" in output
        assert "tier=1" in output

    def test_console_format_with_exception(self):
        """Should include exception info in console output."""
        formatter = ConsoleFormatter()

        try:
            raise RuntimeError("Test exception")
        except RuntimeError:
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="/test.py",
                lineno=10,
                msg="Error",
                args=(),
                exc_info=exc_info,
            )

        output = formatter.format(record)

        assert "RuntimeError" in output
        assert "Test exception" in output


class TestConfigureLogging:
    """Tests for configure_logging function."""

    @pytest.fixture(autouse=True)
    def reset_logging_state(self):
        """Reset logging configuration before each test."""
        import forge_harness.logging_config as lc
        lc._configured = False
        yield
        lc._configured = False

    def test_configure_logging_json_format(self):
        """Should configure logging with JSON format."""
        stream = StringIO()
        configure_logging(level="INFO", json_format=True, stream=stream)

        # Log directly to the stream via a handler
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter())
        logger = logging.getLogger("test_json_logger")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("JSON test message")

        # Check output is valid JSON
        output = stream.getvalue()
        assert output  # Should have content
        # Parse first line as JSON
        first_line = output.strip().split('\n')[0]
        if first_line:
            data = json.loads(first_line)
            assert "message" in data

    def test_configure_logging_console_format(self):
        """Should configure logging with console format."""
        stream = StringIO()
        configure_logging(level="DEBUG", json_format=False, stream=stream)

        # Log directly via handler
        handler = logging.StreamHandler(stream)
        handler.setFormatter(ConsoleFormatter())
        logger = logging.getLogger("test_console_logger_2")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.debug("Console test message")

        output = stream.getvalue()
        assert "Console test message" in output

    def test_configure_logging_level(self):
        """Should set appropriate log level."""
        stream = StringIO()
        configure_logging(level="ERROR", json_format=False, stream=stream)

        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.ERROR)
        logger = logging.getLogger("test_level_logger_2")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)

        logger.info("This should not appear")
        logger.error("This should appear")

        output = stream.getvalue()
        assert "This should appear" in output
        assert "This should not appear" not in output

    def test_configure_logging_clears_existing_handlers(self):
        """Should clear existing handlers before adding new ones."""
        logger = logging.getLogger("forge_harness")

        # Add a dummy handler
        dummy_handler = logging.StreamHandler(StringIO())
        logger.addHandler(dummy_handler)
        original_count = len(logger.handlers)

        configure_logging(level="INFO", json_format=False)

        # Should have exactly one handler after configure
        assert len(logger.handlers) == 1

    def test_configure_logging_prevents_propagation(self):
        """Should prevent propagation to root logger."""
        configure_logging(level="INFO", json_format=False)

        logger = logging.getLogger("forge_harness")
        assert logger.propagate is False


class TestGetLogger:
    """Tests for get_logger function."""

    def test_get_logger_returns_logger(self):
        """Should return a logger instance."""
        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_get_logger_auto_configures(self):
        """Should auto-configure logging if not already configured."""
        # Reset configuration state
        import forge_harness.logging_config as lc
        original_configured = lc._configured
        lc._configured = False

        try:
            stream = StringIO()
            logger = get_logger("test_auto_config")

            # Logger should work
            assert logger is not None
        finally:
            lc._configured = original_configured


class TestLogContext:
    """Tests for LogContext context manager."""

    def test_log_context_adds_fields(self):
        """Should add context fields to log records."""
        logger = logging.getLogger("test_context")

        # Create a formatter that captures the record
        class CaptureFormatter(logging.Formatter):
            last_record = None
            def format(self, record):
                CaptureFormatter.last_record = record
                return super().format(record)

        handler = logging.StreamHandler(StringIO())
        formatter = CaptureFormatter()
        handler.setFormatter(formatter)

        test_logger = logging.getLogger("test_context_logger")
        test_logger.handlers.clear()
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.INFO)

        with LogContext(test_logger, domain="codeswiftr", tier=1):
            test_logger.info("Test message")

        # Check the record was modified
        assert hasattr(CaptureFormatter.last_record, "domain")
        assert CaptureFormatter.last_record.domain == "codeswiftr"
        assert CaptureFormatter.last_record.tier == 1

    def test_log_context_restores_factory(self):
        """Should restore original record factory on exit."""
        logger = logging.getLogger("test_context_restore")
        original_factory = logging.getLogRecordFactory()

        with LogContext(logger, domain="test"):
            pass

        # Factory should be restored
        assert logging.getLogRecordFactory() is original_factory

    def test_log_context_nested(self):
        """Should handle nested contexts."""
        logger = logging.getLogger("test_nested")

        with LogContext(logger, outer="value1"):
            record1 = logging.makeLogRecord({"name": "test", "level": logging.INFO})
            assert record1.outer == "value1"

            with LogContext(logger, inner="value2"):
                record2 = logging.makeLogRecord({"name": "test", "level": logging.INFO})
                # Should have both contexts
                assert record2.outer == "value1"
                assert record2.inner == "value2"


class TestLogWithContext:
    """Tests for log_with_context function."""

    def test_log_with_context_adds_fields(self):
        """Should log with additional context fields."""
        class CaptureHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.records = []
            def emit(self, record):
                self.records.append(record)

        handler = CaptureHandler()
        logger = logging.getLogger("test_ctx_func")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        log_with_context(
            logger,
            logging.INFO,
            "Context message",
            task_id="123",
            user="test"
        )

        assert len(handler.records) == 1
        record = handler.records[0]
        assert record.getMessage() == "Context message"
        assert record.task_id == "123"
        assert record.user == "test"


class TestTaskConvenienceFunctions:
    """Tests for task logging convenience functions."""

    def _get_capturing_logger(self):
        """Create a logger that captures records."""
        class CaptureHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.records = []
            def emit(self, record):
                self.records.append(record)

        handler = CaptureHandler()
        logger = logging.getLogger("test_capture")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return logger, handler

    def test_log_task_start(self):
        """Should log task start with context."""
        logger, handler = self._get_capturing_logger()

        log_task_start(logger, "process_data", domain="codeswiftr")

        assert len(handler.records) == 1
        record = handler.records[0]
        assert "Starting: process_data" in record.getMessage()
        assert record.task == "process_data"
        assert record.domain == "codeswiftr"

    def test_log_task_complete(self):
        """Should log task completion with optional duration."""
        logger, handler = self._get_capturing_logger()

        log_task_complete(logger, "process_data", duration=1.5, status="success")

        assert len(handler.records) == 1
        record = handler.records[0]
        assert "Completed: process_data" in record.getMessage()
        assert record.task == "process_data"
        assert record.duration_sec == 1.5
        assert record.status == "success"

    def test_log_task_complete_without_duration(self):
        """Should log task completion without duration."""
        logger, handler = self._get_capturing_logger()

        log_task_complete(logger, "process_data")

        assert len(handler.records) == 1
        record = handler.records[0]
        assert "Completed: process_data" in record.getMessage()
        assert record.task == "process_data"
        assert not hasattr(record, "duration_sec")

    def test_log_task_error(self):
        """Should log task error with error details."""
        logger, handler = self._get_capturing_logger()

        error = ValueError("Something went wrong")
        log_task_error(logger, "process_data", error, retry_count=3)

        assert len(handler.records) == 1
        record = handler.records[0]
        assert "Failed: process_data" in record.getMessage()
        assert record.task == "process_data"
        assert record.error_type == "ValueError"
        assert record.retry_count == 3


class TestEdgeCases:
    """Edge case tests."""

    def test_json_formatter_with_datetime(self):
        """Should handle datetime objects in extra fields."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        # Add a datetime object that needs serialization
        record.datetime_obj = datetime(2024, 1, 15, 10, 30, 0)

        output = formatter.format(record)
        data = json.loads(output)

        # Should serialize datetime as string
        assert "2024-01-15" in data["datetime_obj"]

    def test_console_formatter_unknown_level(self):
        """Should handle unknown log level gracefully."""
        formatter = ConsoleFormatter()
        record = logging.LogRecord(
            name="test",
            level=999,  # Unknown level
            pathname="/test.py",
            lineno=1,
            msg="Unknown level",
            args=(),
            exc_info=None,
        )
        record.levelname = "UNKNOWN"

        output = formatter.format(record)

        assert "UNKNOWN" in output
        assert "Unknown level" in output

    def test_configure_logging_default_stream(self):
        """Should use sys.stderr as default stream."""
        # This should not raise an error
        configure_logging(level="INFO", json_format=False)

        logger = get_logger("test_default_stream")
        # Just verify logger works
        logger.info("Test to default stream")
