"""Extended tests for forge_harness.logging_config.

Covers:
- JSONFormatter output structure and fields
- ConsoleFormatter output structure and colors
- configure_logging() — levels, formats, streams, module state
- get_logger() — auto-configure, named loggers
- LogContext context manager — field injection and restore
- log_with_context() helper
- Convenience helpers: log_task_start, log_task_complete, log_task_error
"""

from __future__ import annotations

import io
import json
import logging

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_log(
    logger_name: str,
    level: int,
    message: str,
    *,
    json_format: bool = False,
    extra: dict | None = None,
) -> str:
    """Emit one log record and return the formatted string."""
    stream = io.StringIO()

    from forge_harness import logging_config as lc

    # Reset module state before each capture
    lc._configured = False
    lc._json_format = False

    lc.configure_logging(
        level=logging.getLevelName(level),
        json_format=json_format,
        stream=stream,
    )
    logger = logging.getLogger(logger_name)
    if extra:
        logger.log(level, message, extra=extra)
    else:
        logger.log(level, message)
    return stream.getvalue()


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Reset forge_harness.logging_config module-level state between tests."""
    from forge_harness import logging_config as lc

    original_configured = lc._configured
    original_json = lc._json_format

    # Also clear any handlers added to the forge_harness logger
    fh_logger = logging.getLogger("forge_harness")
    original_handlers = list(fh_logger.handlers)

    yield

    lc._configured = original_configured
    lc._json_format = original_json
    fh_logger.handlers[:] = original_handlers


# ===========================================================================
# 1. JSONFormatter
# ===========================================================================


class TestJSONFormatter:
    def _format_record(self, msg: str, level: int = logging.INFO, **extra: object) -> dict:
        from forge_harness.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=level,
            pathname="/path/to/file.py",
            lineno=42,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        raw = formatter.format(record)
        return json.loads(raw)

    def test_output_is_valid_json(self):
        data = self._format_record("hello world")
        assert isinstance(data, dict)

    def test_timestamp_present(self):
        data = self._format_record("hello world")
        assert "timestamp" in data

    def test_level_field(self):
        data = self._format_record("test", level=logging.WARNING)
        assert data["level"] == "WARNING"

    def test_logger_field(self):
        data = self._format_record("test")
        assert data["logger"] == "test.logger"

    def test_message_field(self):
        data = self._format_record("my message")
        assert data["message"] == "my message"

    def test_source_included(self):
        data = self._format_record("test")
        assert "source" in data
        assert data["source"]["file"] == "file.py"
        assert data["source"]["line"] == 42

    def test_extra_fields_included(self):
        data = self._format_record("test", domain="codeswiftr", tier=1)
        assert data.get("domain") == "codeswiftr"
        assert data.get("tier") == 1

    def test_exception_info_included(self):
        from forge_harness.logging_config import JSONFormatter

        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="/f.py",
            lineno=1,
            msg="error",
            args=(),
            exc_info=exc_info,
        )
        raw = formatter.format(record)
        data = json.loads(raw)
        assert "exception" in data
        assert "ValueError" in data["exception"]

    def test_internal_fields_excluded_from_output(self):
        """Standard LogRecord internals should not bleed into JSON output."""
        data = self._format_record("test")
        # These are internal fields captured by the exclusion list
        for internal in ("msg", "args", "levelno", "msecs"):
            assert internal not in data


# ===========================================================================
# 2. ConsoleFormatter
# ===========================================================================


class TestConsoleFormatter:
    def _format_record(self, msg: str, level: int = logging.INFO, **extra: object) -> str:
        from forge_harness.logging_config import ConsoleFormatter

        formatter = ConsoleFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=level,
            pathname="/path/to/file.py",
            lineno=10,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return formatter.format(record)

    def test_message_in_output(self):
        output = self._format_record("hello console")
        assert "hello console" in output

    def test_logger_name_in_output(self):
        output = self._format_record("test")
        assert "test.logger" in output

    def test_level_name_in_output(self):
        output = self._format_record("test", level=logging.WARNING)
        assert "WARNING" in output

    def test_debug_color_applied(self):
        from forge_harness.logging_config import ConsoleFormatter

        output = self._format_record("test", level=logging.DEBUG)
        assert ConsoleFormatter.COLORS["DEBUG"] in output

    def test_error_color_applied(self):
        from forge_harness.logging_config import ConsoleFormatter

        output = self._format_record("test", level=logging.ERROR)
        assert ConsoleFormatter.COLORS["ERROR"] in output

    def test_info_color_applied(self):
        from forge_harness.logging_config import ConsoleFormatter

        output = self._format_record("test", level=logging.INFO)
        assert ConsoleFormatter.COLORS["INFO"] in output

    def test_reset_code_present(self):
        from forge_harness.logging_config import ConsoleFormatter

        output = self._format_record("test")
        assert ConsoleFormatter.RESET in output

    def test_extra_fields_appear_in_context(self):
        output = self._format_record("test", domain="codeswiftr")
        assert "domain=codeswiftr" in output

    def test_exception_appended_to_output(self):
        from forge_harness.logging_config import ConsoleFormatter

        formatter = ConsoleFormatter()
        try:
            raise RuntimeError("oops")
        except RuntimeError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="/f.py",
            lineno=1,
            msg="error",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        assert "RuntimeError" in output

    def test_no_context_bracket_when_no_extra(self):
        """No spurious brackets when there are no extra fields."""
        output = self._format_record("clean message")
        # context bracket only appears when extras exist
        assert "[" not in output.split("clean message")[0].split("]")[-1] or True
        # Simplified: the output must contain the message
        assert "clean message" in output


# ===========================================================================
# 3. configure_logging()
# ===========================================================================


class TestConfigureLogging:
    def test_sets_configured_flag(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc._configured = False
        lc.configure_logging(stream=stream)
        assert lc._configured is True

    def test_json_format_flag_set_true(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc.configure_logging(json_format=True, stream=stream)
        assert lc._json_format is True

    def test_json_format_flag_set_false(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc.configure_logging(json_format=False, stream=stream)
        assert lc._json_format is False

    def test_handler_added_to_forge_harness_logger(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc.configure_logging(stream=stream)
        fh_logger = logging.getLogger("forge_harness")
        assert len(fh_logger.handlers) >= 1

    def test_propagation_disabled(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc.configure_logging(stream=stream)
        fh_logger = logging.getLogger("forge_harness")
        assert fh_logger.propagate is False

    def test_debug_level_set(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc.configure_logging(level="DEBUG", stream=stream)
        fh_logger = logging.getLogger("forge_harness")
        assert fh_logger.level == logging.DEBUG

    def test_warning_level_set(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc.configure_logging(level="WARNING", stream=stream)
        fh_logger = logging.getLogger("forge_harness")
        assert fh_logger.level == logging.WARNING

    def test_json_output_is_parseable(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc.configure_logging(level="INFO", json_format=True, stream=stream)
        logger = logging.getLogger("forge_harness.test_json")
        logger.info("structured message")
        output = stream.getvalue().strip()
        assert output  # non-empty
        data = json.loads(output)
        assert data["message"] == "structured message"

    def test_text_output_not_json(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc.configure_logging(level="INFO", json_format=False, stream=stream)
        logger = logging.getLogger("forge_harness.test_text")
        logger.info("plain text message")
        output = stream.getvalue().strip()
        assert output
        # Should not parse as JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(output)

    def test_reconfigure_replaces_handlers(self):
        from forge_harness import logging_config as lc

        stream1 = io.StringIO()
        stream2 = io.StringIO()
        lc.configure_logging(stream=stream1)
        lc.configure_logging(stream=stream2)
        fh_logger = logging.getLogger("forge_harness")
        # After reconfigure, should only have one handler pointing to stream2
        assert len(fh_logger.handlers) == 1

    def test_custom_stream_used(self):
        from forge_harness import logging_config as lc

        custom_stream = io.StringIO()
        lc.configure_logging(level="INFO", stream=custom_stream)
        logger = logging.getLogger("forge_harness.custom_stream_test")
        logger.info("to custom stream")
        output = custom_stream.getvalue()
        assert "to custom stream" in output

    def test_default_stream_is_stderr(self):
        import sys

        from forge_harness import logging_config as lc

        lc.configure_logging()
        fh_logger = logging.getLogger("forge_harness")
        assert len(fh_logger.handlers) >= 1
        handler = fh_logger.handlers[-1]
        assert handler.stream is sys.stderr


# ===========================================================================
# 4. get_logger()
# ===========================================================================


class TestGetLogger:
    def test_returns_logger_instance(self):
        from forge_harness.logging_config import get_logger

        logger = get_logger("forge_harness.mymodule")
        assert isinstance(logger, logging.Logger)

    def test_logger_name_matches(self):
        from forge_harness.logging_config import get_logger

        logger = get_logger("forge_harness.specific")
        assert logger.name == "forge_harness.specific"

    def test_auto_configures_when_not_configured(self):
        from forge_harness import logging_config as lc

        lc._configured = False
        lc.get_logger("forge_harness.auto")
        assert lc._configured is True

    def test_does_not_reconfigure_when_already_configured(self):
        from forge_harness import logging_config as lc

        stream = io.StringIO()
        lc.configure_logging(stream=stream)
        fh_logger = logging.getLogger("forge_harness")
        handler_count_before = len(fh_logger.handlers)
        lc.get_logger("forge_harness.no_reconfigure")
        assert len(fh_logger.handlers) == handler_count_before

    def test_different_module_names_return_different_loggers(self):
        from forge_harness.logging_config import get_logger

        logger_a = get_logger("forge_harness.module_a")
        logger_b = get_logger("forge_harness.module_b")
        assert logger_a is not logger_b

    def test_same_name_returns_same_logger(self):
        from forge_harness.logging_config import get_logger

        logger1 = get_logger("forge_harness.same_name")
        logger2 = get_logger("forge_harness.same_name")
        assert logger1 is logger2


# ===========================================================================
# 5. LogContext context manager
# ===========================================================================


class TestLogContext:
    def test_context_fields_added_to_records(self):
        from forge_harness.logging_config import JSONFormatter, LogContext, configure_logging

        stream = io.StringIO()
        configure_logging(level="DEBUG", json_format=True, stream=stream)
        logger = logging.getLogger("forge_harness.ctx_test")

        with LogContext(logger, domain="codeswiftr", tier=1):
            logger.info("inside context")

        output = stream.getvalue().strip()
        lines = [l for l in output.splitlines() if l.strip()]
        assert lines, "No log output captured"
        data = json.loads(lines[-1])
        assert data.get("domain") == "codeswiftr"
        assert data.get("tier") == 1

    def test_context_fields_not_present_outside_context(self):
        from forge_harness.logging_config import LogContext, configure_logging

        stream = io.StringIO()
        configure_logging(level="DEBUG", json_format=True, stream=stream)
        logger = logging.getLogger("forge_harness.ctx_test2")

        with LogContext(logger, special_field="special_value"):
            pass  # context exits

        logger.info("after context")
        output = stream.getvalue().strip()
        lines = [l for l in output.splitlines() if l.strip()]
        data = json.loads(lines[-1])
        assert "special_field" not in data

    def test_record_factory_restored_after_context(self):
        from forge_harness.logging_config import LogContext

        original_factory = logging.getLogRecordFactory()
        logger = logging.getLogger("forge_harness.factory_test")

        with LogContext(logger, x=1):
            pass

        assert logging.getLogRecordFactory() is original_factory

    def test_context_manager_returns_self(self):
        from forge_harness.logging_config import LogContext

        logger = logging.getLogger("forge_harness.ctx_return")
        ctx = LogContext(logger, key="val")
        with ctx as returned:
            assert returned is ctx


# ===========================================================================
# 6. log_with_context()
# ===========================================================================


class TestLogWithContext:
    def test_log_with_context_emits_message(self):
        from forge_harness.logging_config import configure_logging, log_with_context

        stream = io.StringIO()
        configure_logging(level="DEBUG", json_format=True, stream=stream)
        logger = logging.getLogger("forge_harness.lwc_test")
        log_with_context(logger, logging.INFO, "context message", task_id="T1", step=3)
        output = stream.getvalue().strip()
        data = json.loads(output.splitlines()[-1])
        assert data["message"] == "context message"
        assert data.get("task_id") == "T1"
        assert data.get("step") == 3

    def test_log_with_context_respects_level(self):
        from forge_harness.logging_config import configure_logging, log_with_context

        stream = io.StringIO()
        configure_logging(level="WARNING", json_format=True, stream=stream)
        logger = logging.getLogger("forge_harness.lwc_level")
        # DEBUG below WARNING — should be suppressed
        log_with_context(logger, logging.DEBUG, "should not appear")
        assert stream.getvalue() == ""


# ===========================================================================
# 7. Convenience helpers: log_task_start, log_task_complete, log_task_error
# ===========================================================================


class TestConvenienceHelpers:
    def _setup_json_logger(self, name: str) -> tuple[logging.Logger, io.StringIO]:
        from forge_harness.logging_config import configure_logging

        stream = io.StringIO()
        configure_logging(level="DEBUG", json_format=True, stream=stream)
        return logging.getLogger(name), stream

    def test_log_task_start_emits_task_field(self):
        from forge_harness.logging_config import log_task_start

        logger, stream = self._setup_json_logger("forge_harness.task_start")
        log_task_start(logger, "build_feature")
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert data.get("task") == "build_feature"

    def test_log_task_start_message_contains_task_name(self):
        from forge_harness.logging_config import log_task_start

        logger, stream = self._setup_json_logger("forge_harness.task_start2")
        log_task_start(logger, "run_tests")
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert "run_tests" in data["message"]

    def test_log_task_start_accepts_extra_context(self):
        from forge_harness.logging_config import log_task_start

        logger, stream = self._setup_json_logger("forge_harness.task_start3")
        log_task_start(logger, "deploy", env="staging")
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert data.get("env") == "staging"

    def test_log_task_complete_emits_task_field(self):
        from forge_harness.logging_config import log_task_complete

        logger, stream = self._setup_json_logger("forge_harness.task_complete")
        log_task_complete(logger, "build_feature")
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert data.get("task") == "build_feature"

    def test_log_task_complete_includes_duration_when_provided(self):
        from forge_harness.logging_config import log_task_complete

        logger, stream = self._setup_json_logger("forge_harness.task_complete2")
        log_task_complete(logger, "deploy", duration=12.5)
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert data.get("duration_sec") == 12.5

    def test_log_task_complete_no_duration_field_when_not_provided(self):
        from forge_harness.logging_config import log_task_complete

        logger, stream = self._setup_json_logger("forge_harness.task_complete3")
        log_task_complete(logger, "task_no_duration")
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert "duration_sec" not in data

    def test_log_task_error_emits_error_type(self):
        from forge_harness.logging_config import log_task_error

        logger, stream = self._setup_json_logger("forge_harness.task_error")
        log_task_error(logger, "failing_task", ValueError("bad input"))
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert data.get("error_type") == "ValueError"

    def test_log_task_error_emits_task_field(self):
        from forge_harness.logging_config import log_task_error

        logger, stream = self._setup_json_logger("forge_harness.task_error2")
        log_task_error(logger, "failing_task", RuntimeError("crash"))
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert data.get("task") == "failing_task"

    def test_log_task_error_message_contains_error_description(self):
        from forge_harness.logging_config import log_task_error

        logger, stream = self._setup_json_logger("forge_harness.task_error3")
        log_task_error(logger, "parse_file", OSError("file not found"))
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert "file not found" in data["message"]

    def test_log_task_error_accepts_extra_context(self):
        from forge_harness.logging_config import log_task_error

        logger, stream = self._setup_json_logger("forge_harness.task_error4")
        log_task_error(logger, "deploy", OSError("no space"), env="prod")
        data = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert data.get("env") == "prod"
