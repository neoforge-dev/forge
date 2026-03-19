"""Tests for shared CLI output helpers."""

from datetime import UTC, datetime, timedelta

from forge_shared.cli import build_error, output_error, output_success


def test_output_success_envelope() -> None:
    start = datetime.now(UTC) - timedelta(milliseconds=25)
    payload = output_success(command="campaign-intel", data={"ok": True}, start_time=start)

    assert payload["success"] is True
    assert payload["data"] == {"ok": True}
    assert payload["error"] is None
    assert payload["error_code"] is None
    assert payload["duration_ms"] >= 0
    assert payload["timestamp"].endswith("Z")
    assert payload["metadata"]["command"] == "campaign-intel"
    assert payload["metadata"]["version"] == "0.1.0"


def test_output_error_envelope() -> None:
    payload = output_error(
        command="interview-sim",
        message="request failed",
        error_code="SERVICE_UNAVAILABLE",
    )

    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["error"] == "request failed"
    assert payload["error_code"] == "SERVICE_UNAVAILABLE"
    assert payload["duration_ms"] == 0
    assert payload["metadata"]["command"] == "interview-sim"


def test_build_error_payload() -> None:
    error = build_error(
        message="bad input",
        code="INVALID_INPUT",
        details={"field": "account_id"},
    )
    assert error == {
        "message": "bad input",
        "code": "INVALID_INPUT",
        "details": {"field": "account_id"},
    }

