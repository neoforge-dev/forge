"""Comprehensive unit tests for forge_harness/cli_v2/json_output.py.

Tests cover:
- json_output() — success / error envelope construction
- print_json()  — stdout serialisation via click.echo
- handle_error_json() — conditional JSON output, all input types
- Edge cases: unknown error codes, None/falsy data, nested dicts,
  non-serialisable types (default=str), timestamp format.
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from forge_harness.cli_v2.json_output import (
    handle_error_json,
    json_output,
    print_json,
)

from forge_harness.error_schema import (
    AGENT_NOT_FOUND,
    AUTH_MISSING_CREDENTIALS,
    DISPATCH_FAILED,
    TASK_NOT_FOUND,
    UNKNOWN_ERROR,
    ForgeError,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _capture_print_json(output: dict) -> dict:
    """Call print_json and return the parsed JSON that was written to stdout."""
    captured: list[str] = []
    with patch("click.echo", side_effect=lambda s: captured.append(s)):
        print_json(output)
    assert len(captured) == 1
    return json.loads(captured[0])


# ---------------------------------------------------------------------------
# json_output — success envelopes
# ---------------------------------------------------------------------------


class TestJsonOutputSuccess:
    """Tests for json_output() when success=True."""

    def test_success_true_sets_success_flag(self):
        result = json_output(success=True, data={"key": "value"})
        assert result["success"] is True

    def test_success_error_field_is_none(self):
        result = json_output(success=True, data="hello")
        assert result["error"] is None

    def test_success_data_is_preserved(self):
        payload = {"items": [1, 2, 3], "count": 3}
        result = json_output(success=True, data=payload)
        assert result["data"] == payload

    def test_success_default_command_is_forge(self):
        result = json_output(success=True, data=None)
        assert result["command"] == "forge"

    def test_success_custom_command_stored(self):
        result = json_output(success=True, data=None, command="tasks list")
        assert result["command"] == "tasks list"

    def test_success_timestamp_present_and_iso_format(self):
        result = json_output(success=True, data=None)
        ts = result["timestamp"]
        # ISO-8601 UTC timestamps end with '+00:00' or 'Z'
        assert "T" in ts
        assert "+" in ts or ts.endswith("Z")

    def test_success_with_none_data(self):
        result = json_output(success=True, data=None)
        assert result["success"] is True
        assert result["data"] is None

    def test_success_with_empty_list_data(self):
        result = json_output(success=True, data=[])
        assert result["data"] == []

    def test_success_with_nested_dict_data(self):
        nested = {"a": {"b": {"c": 42}}}
        result = json_output(success=True, data=nested)
        assert result["data"]["a"]["b"]["c"] == 42

    def test_success_envelope_has_all_required_keys(self):
        result = json_output(success=True, data={})
        assert set(result.keys()) == {"success", "data", "error", "timestamp", "command"}

    def test_success_error_argument_ignored_when_success_true(self):
        """When success=True the error argument must NOT influence the output."""
        result = json_output(success=True, data="ok", error="should be ignored")
        # success=True with error is a caller mistake; module returns success envelope
        assert result["success"] is True
        assert result["error"] is None

    def test_success_with_integer_data(self):
        result = json_output(success=True, data=42)
        assert result["data"] == 42

    def test_success_with_boolean_data(self):
        result = json_output(success=True, data=False)
        assert result["data"] is False


# ---------------------------------------------------------------------------
# json_output — error envelopes
# ---------------------------------------------------------------------------


class TestJsonOutputError:
    """Tests for json_output() when success=False."""

    def test_error_success_flag_is_false(self):
        result = json_output(success=False, data=None, error="boom")
        assert result["success"] is False

    def test_error_data_is_none(self):
        result = json_output(success=False, data={"ignored": True}, error="err")
        assert result["data"] is None

    def test_error_string_wrapped_with_unknown_error_code(self):
        result = json_output(success=False, data=None, error="something went wrong")
        assert result["error"]["code"] == UNKNOWN_ERROR
        assert result["error"]["message"] == "something went wrong"

    def test_error_forge_error_instance_preserved(self):
        fe = ForgeError(code=TASK_NOT_FOUND, message="Task 42 missing")
        result = json_output(success=False, data=None, error=fe)
        assert result["error"]["code"] == TASK_NOT_FOUND
        assert result["error"]["message"] == "Task 42 missing"

    def test_error_dict_round_trips_correctly(self):
        error_dict = {
            "code": AGENT_NOT_FOUND,
            "message": "agent kimi not found",
            "next_action": "check the fleet",
        }
        result = json_output(success=False, data=None, error=error_dict)
        assert result["error"]["code"] == AGENT_NOT_FOUND
        assert result["error"]["message"] == "agent kimi not found"

    def test_error_forge_error_with_details(self):
        fe = ForgeError(
            code=AUTH_MISSING_CREDENTIALS,
            message="No token provided",
            details={"header": "Authorization"},
        )
        result = json_output(success=False, data=None, error=fe)
        assert result["error"]["details"]["header"] == "Authorization"

    def test_error_default_command_is_forge(self):
        result = json_output(success=False, data=None, error="err")
        assert result["command"] == "forge"

    def test_error_custom_command_stored(self):
        result = json_output(success=False, data=None, error="err", command="dispatch")
        assert result["command"] == "dispatch"

    def test_error_timestamp_present(self):
        result = json_output(success=False, data=None, error="err")
        assert "timestamp" in result
        assert result["timestamp"]

    def test_error_envelope_has_all_required_keys(self):
        result = json_output(success=False, data=None, error="err")
        assert set(result.keys()) == {"success", "data", "error", "timestamp", "command"}

    def test_error_next_action_auto_populated_from_catalogue(self):
        result = json_output(success=False, data=None, error="oops")
        # UNKNOWN_ERROR next_action should come from the catalogue
        assert result["error"]["next_action"]
        assert "forge doctor" in result["error"]["next_action"]

    def test_error_with_none_error_falls_through_to_success_path(self):
        """success=False but error=None should produce the generic success envelope
        (the guard `if not success and error is not None` is False)."""
        result = json_output(success=False, data=None, error=None)
        # Falls to the generic return — success flag kept as-is, error is None
        assert result["success"] is False
        assert result["error"] is None

    def test_error_arbitrary_exception_type_wrapped(self):
        """Non-str, non-dict, non-ForgeError objects are coerced via str()."""
        result = json_output(success=False, data=None, error=RuntimeError("kaboom"))
        assert result["error"]["code"] == UNKNOWN_ERROR
        assert "kaboom" in result["error"]["message"]


# ---------------------------------------------------------------------------
# print_json
# ---------------------------------------------------------------------------


class TestPrintJson:
    """Tests for print_json() stdout behaviour."""

    def test_prints_valid_json(self):
        payload = json_output(success=True, data={"x": 1})
        parsed = _capture_print_json(payload)
        assert parsed["success"] is True
        assert parsed["data"]["x"] == 1

    def test_output_is_indented(self):
        """print_json should emit pretty-printed (indent=2) JSON."""
        payload = json_output(success=True, data={})
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            print_json(payload)
        raw = captured[0]
        assert "\n" in raw, "Expected multi-line indented JSON"

    def test_non_serialisable_values_use_default_str(self):
        """Objects not serialisable by default should fall back to str()."""
        from datetime import date

        payload = {"success": True, "data": date(2026, 2, 23), "error": None, "command": "forge", "timestamp": "t"}
        parsed = _capture_print_json(payload)
        assert parsed["data"] == "2026-02-23"

    def test_calls_click_echo_exactly_once(self):
        payload = json_output(success=True, data=None)
        with patch("click.echo") as mock_echo:
            print_json(payload)
        mock_echo.assert_called_once()


# ---------------------------------------------------------------------------
# handle_error_json
# ---------------------------------------------------------------------------


class TestHandleErrorJson:
    """Tests for handle_error_json()."""

    # --- output_json=False (silent mode) ---

    def test_silent_mode_produces_no_output(self):
        with patch("click.echo") as mock_echo:
            handle_error_json(Exception("silent"), command="forge", output_json=False)
        mock_echo.assert_not_called()

    def test_silent_mode_is_default(self):
        """output_json defaults to False — no echo."""
        with patch("click.echo") as mock_echo:
            handle_error_json(ValueError("nope"), command="forge")
        mock_echo.assert_not_called()

    # --- output_json=True — plain Exception ---

    def test_exception_produces_json_envelope(self):
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(Exception("bad"), command="forge", output_json=True)
        parsed = json.loads(captured[0])
        assert parsed["success"] is False
        assert parsed["error"]["code"] == UNKNOWN_ERROR
        assert "bad" in parsed["error"]["message"]

    def test_exception_uses_custom_code(self):
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(
                Exception("no agent"),
                command="fleet",
                output_json=True,
                code=AGENT_NOT_FOUND,
            )
        parsed = json.loads(captured[0])
        assert parsed["error"]["code"] == AGENT_NOT_FOUND

    def test_exception_command_included_in_envelope(self):
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(Exception("err"), command="tasks create", output_json=True)
        parsed = json.loads(captured[0])
        assert parsed["command"] == "tasks create"

    # --- output_json=True — ForgeError ---

    def test_forge_error_preserved_in_envelope(self):
        fe = ForgeError(code=DISPATCH_FAILED, message="dispatch timed out")
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(fe, command="dispatch", output_json=True)
        parsed = json.loads(captured[0])
        assert parsed["error"]["code"] == DISPATCH_FAILED
        assert parsed["error"]["message"] == "dispatch timed out"

    def test_forge_error_code_not_overridden_by_default_code(self):
        """The `code` kwarg applies only to plain Exceptions, not ForgeError."""
        fe = ForgeError(code=TASK_NOT_FOUND, message="missing")
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(
                fe, command="tasks", output_json=True, code=UNKNOWN_ERROR
            )
        parsed = json.loads(captured[0])
        # ForgeError.code should win
        assert parsed["error"]["code"] == TASK_NOT_FOUND

    # --- output_json=True — dict ---

    def test_dict_error_round_trips(self):
        error_dict = {
            "code": AUTH_MISSING_CREDENTIALS,
            "message": "token required",
            "next_action": "add Authorization header",
        }
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(error_dict, command="auth", output_json=True)
        parsed = json.loads(captured[0])
        assert parsed["error"]["code"] == AUTH_MISSING_CREDENTIALS
        assert parsed["error"]["message"] == "token required"

    def test_dict_missing_fields_get_defaults(self):
        """from_dict should gracefully handle missing keys."""
        error_dict = {"message": "partial error"}
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(error_dict, command="forge", output_json=True)
        parsed = json.loads(captured[0])
        assert parsed["error"]["code"] == UNKNOWN_ERROR  # default fallback
        assert parsed["error"]["message"] == "partial error"

    # --- envelope structure ---

    def test_envelope_is_valid_json(self):
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(RuntimeError("oops"), command="forge", output_json=True)
        # Should not raise
        parsed = json.loads(captured[0])
        assert isinstance(parsed, dict)

    def test_envelope_success_false(self):
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(Exception("x"), command="forge", output_json=True)
        parsed = json.loads(captured[0])
        assert parsed["success"] is False

    def test_envelope_data_is_none(self):
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(Exception("x"), command="forge", output_json=True)
        parsed = json.loads(captured[0])
        assert parsed["data"] is None

    def test_envelope_has_timestamp(self):
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(Exception("x"), command="forge", output_json=True)
        parsed = json.loads(captured[0])
        assert "timestamp" in parsed
        assert parsed["timestamp"]

    def test_next_action_present_in_error_block(self):
        captured: list[str] = []
        with patch("click.echo", side_effect=lambda s: captured.append(s)):
            handle_error_json(Exception("x"), command="forge", output_json=True)
        parsed = json.loads(captured[0])
        assert "next_action" in parsed["error"]
        assert parsed["error"]["next_action"]
