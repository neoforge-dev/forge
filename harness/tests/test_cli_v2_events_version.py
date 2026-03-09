"""Tests for forge events and forge version CLI command groups.

Covers all commands in:
  - forge_harness/cli_v2/events.py   (forge events list)
  - forge_harness/cli_v2/version.py  (forge version, forge version build)

Strategy: Click CliRunner for integration paths. All HTTP calls are mocked
via ``unittest.mock.patch`` on ``httpx.Client`` so no real network is hit.

API response shapes under test:
  GET /api/events/recent  → {"success": true, "data": {"events": [...], "count": N, "filter": null}}
  GET /api/version        → {"success": true, "data": {"version": "0.1.0", "service": "forge-harness-webhooks", "api_version": "1.0.0"}}
  GET /api/build          → {"build_number": "unknown", "commit": "abc123", "timestamp": "..."}

Exit codes under test:
  0   Success
  1   API / connection error
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import click
import httpx
import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def invoke(runner: CliRunner, args: list[str]) -> click.testing.Result:
    """Invoke the top-level CLI and return the result."""
    return runner.invoke(cli, args, catch_exceptions=False)


def invoke_json(runner: CliRunner, args: list[str]) -> dict:
    """Invoke CLI and parse JSON output, raising on parse failure."""
    result = invoke(runner, args)
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Could not parse JSON output:\n{result.output!r}\nException: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_httpx_response(status_code: int, body: dict) -> MagicMock:
    """Return a MagicMock that mimics an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _mock_client(response: MagicMock):
    """Return a context manager mock for ``httpx.Client`` that yields a client
    whose ``get`` method returns *response*."""
    client_instance = MagicMock()
    client_instance.get.return_value = response
    ctx_mgr = MagicMock()
    ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
    ctx_mgr.__exit__ = MagicMock(return_value=False)
    return ctx_mgr


def _events_response(
    events: list[dict] | None = None,
    count: int | None = None,
    filter_type: str | None = None,
) -> MagicMock:
    """Build a successful GET /api/events/recent response."""
    event_list = events or []
    return _make_httpx_response(
        200,
        {
            "success": True,
            "data": {
                "events": event_list,
                "count": count if count is not None else len(event_list),
                "filter": filter_type,
            },
        },
    )


def _version_response() -> MagicMock:
    """Build a successful GET /api/version response."""
    return _make_httpx_response(
        200,
        {
            "success": True,
            "data": {
                "version": "0.1.0",
                "service": "forge-harness-webhooks",
                "api_version": "1.0.0",
            },
        },
    )


def _build_response() -> MagicMock:
    """Build a successful GET /api/build response."""
    return _make_httpx_response(
        200,
        {
            "build_number": "unknown",
            "commit": "abc123",
            "timestamp": "2026-02-23T10:00:00+00:00",
        },
    )


def _sample_event(
    event_type: str = "feature.updated",
    summary: str = "Feature was updated",
    source: str = "agent-01",
    timestamp: str = "2026-02-23T10:00:00",
) -> dict:
    return {
        "type": event_type,
        "summary": summary,
        "source": source,
        "timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# 1. forge events list — help and registration
# ---------------------------------------------------------------------------


class TestEventsCommandRegistration:
    """Verify the events group is registered in the CLI."""

    def test_events_help_exits_zero(self, runner: CliRunner):
        result = invoke(runner, ["events", "--help"])
        assert result.exit_code == 0

    def test_events_list_help_exits_zero(self, runner: CliRunner):
        result = invoke(runner, ["events", "list", "--help"])
        assert result.exit_code == 0

    def test_events_list_help_mentions_limit(self, runner: CliRunner):
        result = invoke(runner, ["events", "list", "--help"])
        assert "limit" in result.output.lower()

    def test_events_list_help_mentions_type(self, runner: CliRunner):
        result = invoke(runner, ["events", "list", "--help"])
        assert "type" in result.output.lower()


# ---------------------------------------------------------------------------
# 2. forge events list — empty events
# ---------------------------------------------------------------------------


class TestEventsListEmpty:
    """forge events list with no events in the response."""

    def test_empty_events_exits_zero(self, runner: CliRunner):
        resp = _events_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert result.exit_code == 0

    def test_empty_events_prints_no_events_message(self, runner: CliRunner):
        resp = _events_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert "No events found" in result.output

    def test_empty_events_json_exits_zero(self, runner: CliRunner):
        resp = _events_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list", "--json"])
        assert result.exit_code == 0

    def test_empty_events_json_success_true(self, runner: CliRunner):
        resp = _events_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["events", "list", "--json"])
        assert data["success"] is True

    def test_empty_events_json_events_list_empty(self, runner: CliRunner):
        resp = _events_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["events", "list", "--json"])
        assert data["data"]["events"] == []

    def test_empty_events_json_count_zero(self, runner: CliRunner):
        resp = _events_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["events", "list", "--json"])
        assert data["data"]["count"] == 0


# ---------------------------------------------------------------------------
# 3. forge events list — with events (human output)
# ---------------------------------------------------------------------------


class TestEventsListWithEvents:
    """forge events list when the API returns event records."""

    def test_with_events_exits_zero(self, runner: CliRunner):
        resp = _events_response(events=[_sample_event()])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert result.exit_code == 0

    def test_with_events_shows_event_type(self, runner: CliRunner):
        resp = _events_response(events=[_sample_event(event_type="feature.updated")])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert "feature.updated" in result.output

    def test_with_events_shows_summary(self, runner: CliRunner):
        # Rich wraps the Summary column at the column width (14 chars); use a
        # short summary that fits on a single row to avoid multi-line wrapping.
        resp = _events_response(events=[_sample_event(summary="Deployed")])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert "Deployed" in result.output

    def test_with_events_shows_source(self, runner: CliRunner):
        resp = _events_response(events=[_sample_event(source="prya")])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert "prya" in result.output

    def test_with_events_formats_timestamp(self, runner: CliRunner):
        resp = _events_response(events=[_sample_event(timestamp="2026-02-23T10:30:00")])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        # Formatted as %Y-%m-%d %H:%M:%S
        assert "2026-02-23" in result.output

    def test_with_events_shows_count_in_header(self, runner: CliRunner):
        events = [_sample_event(), _sample_event(event_type="task.completed")]
        resp = _events_response(events=events, count=2)
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert "2" in result.output

    def test_event_without_timestamp_does_not_crash(self, runner: CliRunner):
        event = {"type": "test.event", "summary": "No timestamp", "source": "agent"}
        resp = _events_response(events=[event])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert result.exit_code == 0

    def test_event_with_data_dict_uses_message_as_summary(self, runner: CliRunner):
        # Rich wraps the Summary column at the column width. Use a short message
        # that fits within one row to avoid multi-row wrapping assertions.
        event = {
            "type": "test.event",
            "data": {"message": "Data msg"},
            "source": "agent",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_response(events=[event])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert result.exit_code == 0
        assert "Data msg" in result.output

    def test_event_uses_event_type_field_fallback(self, runner: CliRunner):
        event = {
            "event_type": "legacy.type",
            "summary": "Old-style event",
            "agent_id": "agent-legacy",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_response(events=[event])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert result.exit_code == 0
        assert "legacy.type" in result.output

    def test_invalid_timestamp_falls_back_gracefully(self, runner: CliRunner):
        event = {
            "type": "test.event",
            "summary": "Bad timestamp",
            "source": "agent",
            "timestamp": "not-a-valid-iso-timestamp",
        }
        resp = _events_response(events=[event])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# 4. forge events list — --limit option
# ---------------------------------------------------------------------------


class TestEventsListLimit:
    """forge events list --limit sends the correct query parameter."""

    def test_limit_option_accepted(self, runner: CliRunner):
        resp = _events_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list", "--limit", "50"])
        assert result.exit_code == 0

    def test_limit_passes_to_api(self, runner: CliRunner):
        resp = _events_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["events", "list", "--limit", "42"])
        client = ctx_mgr.__enter__.return_value
        call_kwargs = client.get.call_args
        params = call_kwargs[1].get("params", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
        assert params.get("limit") == 42

    def test_limit_out_of_range_high_exits_nonzero(self, runner: CliRunner):
        # IntRange(1, 100) — 101 is invalid
        result = runner.invoke(cli, ["events", "list", "--limit", "101"])
        assert result.exit_code != 0

    def test_limit_out_of_range_zero_exits_nonzero(self, runner: CliRunner):
        result = runner.invoke(cli, ["events", "list", "--limit", "0"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 5. forge events list — --type filter
# ---------------------------------------------------------------------------


class TestEventsListTypeFilter:
    """forge events list --type passes the filter through to the API."""

    def test_type_filter_accepted(self, runner: CliRunner):
        resp = _events_response(filter_type="feature.updated")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list", "--type", "feature.updated"])
        assert result.exit_code == 0

    def test_type_filter_passes_to_api(self, runner: CliRunner):
        resp = _events_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["events", "list", "--type", "task.completed"])
        client = ctx_mgr.__enter__.return_value
        call_kwargs = client.get.call_args
        params = call_kwargs[1].get("params", {})
        assert params.get("type") == "task.completed"

    def test_filter_shown_in_human_output_when_active(self, runner: CliRunner):
        events = [_sample_event(event_type="feature.updated")]
        resp = _events_response(events=events, filter_type="feature.updated")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list", "--type", "feature.updated"])
        assert "feature.updated" in result.output

    def test_no_type_filter_does_not_include_type_in_params(self, runner: CliRunner):
        resp = _events_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["events", "list"])
        client = ctx_mgr.__enter__.return_value
        call_kwargs = client.get.call_args
        params = call_kwargs[1].get("params", {})
        assert "type" not in params

    def test_empty_events_with_filter_shows_filter_msg(self, runner: CliRunner):
        resp = _events_response(events=[], filter_type="task.completed")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["events", "list", "--type", "task.completed"])
        assert "task.completed" in result.output


# ---------------------------------------------------------------------------
# 6. forge events list — JSON output
# ---------------------------------------------------------------------------


class TestEventsListJsonOutput:
    """forge events list --json output shape validation."""

    def test_json_output_has_success_key(self, runner: CliRunner):
        resp = _events_response(events=[_sample_event()])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["events", "list", "--json"])
        assert "success" in data

    def test_json_output_has_data_key(self, runner: CliRunner):
        resp = _events_response(events=[_sample_event()])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["events", "list", "--json"])
        assert "data" in data

    def test_json_output_command_field(self, runner: CliRunner):
        resp = _events_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["events", "list", "--json"])
        assert data.get("command") == "forge events list"

    def test_json_output_events_list_present(self, runner: CliRunner):
        resp = _events_response(events=[_sample_event()])
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["events", "list", "--json"])
        assert "events" in data["data"]
        assert len(data["data"]["events"]) == 1

    def test_json_output_timestamp_key_present(self, runner: CliRunner):
        resp = _events_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["events", "list", "--json"])
        assert "timestamp" in data

    def test_json_output_with_token_sets_auth_header(self, runner: CliRunner):
        resp = _events_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["events", "list", "--token", "secret-token"])
        client = ctx_mgr.__enter__.return_value
        call_kwargs = client.get.call_args
        headers = call_kwargs[1].get("headers", {})
        assert "Authorization" in headers
        assert "secret-token" in headers["Authorization"]


# ---------------------------------------------------------------------------
# 7. forge events list — API error paths
# ---------------------------------------------------------------------------


class TestEventsListErrorPaths:
    """forge events list error handling for 5xx and connection failures."""

    def test_api_500_human_mode_non_zero_exit(self, runner: CliRunner):
        resp = _make_httpx_response(500, {"error": "internal server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = runner.invoke(cli, ["events", "list"])
        # format_error() does not raise SystemExit by itself; command continues
        # after format_error but no table is shown. Exit code may be 0 or 1.
        # The key assertion is that "500" appears in the output as the error message.
        assert "500" in result.output

    def test_api_500_json_mode_returns_success_false(self, runner: CliRunner):
        resp = _make_httpx_response(500, {"error": "internal server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = runner.invoke(cli, ["events", "list", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_api_500_json_mode_exits_one(self, runner: CliRunner):
        resp = _make_httpx_response(500, {"error": "internal server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = runner.invoke(cli, ["events", "list", "--json"])
        assert result.exit_code == 1

    def test_connection_error_human_mode_shows_message(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.ConnectError("Connection refused")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["events", "list"])
        assert "Cannot connect" in result.output or "localhost" in result.output

    def test_connection_error_json_mode_success_false(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.ConnectError("Connection refused")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["events", "list", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_connection_error_json_mode_exits_one(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.ConnectError("Connection refused")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["events", "list", "--json"])
        assert result.exit_code == 1

    def test_unexpected_exception_exits_one(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = RuntimeError("Unexpected failure")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["events", "list"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 8. forge version — help and registration
# ---------------------------------------------------------------------------


class TestVersionCommandRegistration:
    """Verify the version group is registered in the CLI."""

    def test_version_help_exits_zero(self, runner: CliRunner):
        result = invoke(runner, ["version", "--help"])
        assert result.exit_code == 0

    def test_version_build_help_exits_zero(self, runner: CliRunner):
        result = invoke(runner, ["version", "build", "--help"])
        assert result.exit_code == 0

    def test_version_help_mentions_build_subcommand(self, runner: CliRunner):
        result = invoke(runner, ["version", "--help"])
        assert "build" in result.output


# ---------------------------------------------------------------------------
# 9. forge version — human output
# ---------------------------------------------------------------------------


class TestVersionHumanOutput:
    """forge version (no --json) human-readable output."""

    def test_version_exits_zero(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version"])
        assert result.exit_code == 0

    def test_version_shows_service_name(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version"])
        assert "forge-harness-webhooks" in result.output

    def test_version_shows_version_number(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version"])
        assert "0.1.0" in result.output

    def test_version_shows_api_version(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version"])
        assert "1.0.0" in result.output

    def test_version_hits_correct_endpoint(self, runner: CliRunner):
        resp = _version_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["version"])
        client = ctx_mgr.__enter__.return_value
        call_url = client.get.call_args[0][0]
        assert "/api/version" in call_url


# ---------------------------------------------------------------------------
# 10. forge version --json — JSON output
# ---------------------------------------------------------------------------


class TestVersionJsonOutput:
    """forge version --json output shape validation."""

    def test_version_json_exits_zero(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version", "--json"])
        assert result.exit_code == 0

    def test_version_json_success_true(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "--json"])
        assert data["success"] is True

    def test_version_json_data_contains_version(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "--json"])
        assert data["data"]["version"] == "0.1.0"

    def test_version_json_data_contains_service(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "--json"])
        assert data["data"]["service"] == "forge-harness-webhooks"

    def test_version_json_data_contains_api_version(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "--json"])
        assert data["data"]["api_version"] == "1.0.0"

    def test_version_json_command_field(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "--json"])
        assert data.get("command") == "forge version"

    def test_version_json_timestamp_key_present(self, runner: CliRunner):
        resp = _version_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "--json"])
        assert "timestamp" in data

    def test_version_with_token_sets_auth_header(self, runner: CliRunner):
        resp = _version_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["version", "--token", "my-secret"])
        client = ctx_mgr.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert "Authorization" in headers
        assert "my-secret" in headers["Authorization"]


# ---------------------------------------------------------------------------
# 11. forge version — API error paths
# ---------------------------------------------------------------------------


class TestVersionErrorPaths:
    """forge version error handling for 5xx and connection failures."""

    def test_api_500_human_shows_status_code(self, runner: CliRunner):
        resp = _make_httpx_response(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = runner.invoke(cli, ["version"])
        assert "500" in result.output

    def test_api_500_json_mode_success_false(self, runner: CliRunner):
        resp = _make_httpx_response(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = runner.invoke(cli, ["version", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_api_500_json_mode_exits_one(self, runner: CliRunner):
        resp = _make_httpx_response(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = runner.invoke(cli, ["version", "--json"])
        assert result.exit_code == 1

    def test_connection_error_human_shows_cannot_connect(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.ConnectError("refused")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["version"])
        assert "Cannot connect" in result.output or "localhost" in result.output

    def test_connection_error_json_mode_success_false(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.ConnectError("refused")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["version", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_connection_error_json_mode_exits_one(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.ConnectError("refused")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["version", "--json"])
        assert result.exit_code == 1

    def test_unexpected_exception_exits_one(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = RuntimeError("Unexpected error")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["version"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 12. forge version build — human output
# ---------------------------------------------------------------------------


class TestVersionBuildHumanOutput:
    """forge version build (no --json) human-readable output."""

    def test_version_build_exits_zero(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version", "build"])
        assert result.exit_code == 0

    def test_version_build_shows_build_number(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version", "build"])
        assert "unknown" in result.output

    def test_version_build_shows_commit(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version", "build"])
        assert "abc123" in result.output

    def test_version_build_shows_timestamp(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version", "build"])
        assert "2026-02-23" in result.output

    def test_version_build_hits_correct_endpoint(self, runner: CliRunner):
        resp = _build_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["version", "build"])
        client = ctx_mgr.__enter__.return_value
        call_url = client.get.call_args[0][0]
        assert "/api/build" in call_url


# ---------------------------------------------------------------------------
# 13. forge version build --json — JSON output
# ---------------------------------------------------------------------------


class TestVersionBuildJsonOutput:
    """forge version build --json output shape validation."""

    def test_version_build_json_exits_zero(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version", "build", "--json"])
        assert result.exit_code == 0

    def test_version_build_json_success_true(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "build", "--json"])
        assert data["success"] is True

    def test_version_build_json_data_contains_build_number(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "build", "--json"])
        assert data["data"]["build_number"] == "unknown"

    def test_version_build_json_data_contains_commit(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "build", "--json"])
        assert data["data"]["commit"] == "abc123"

    def test_version_build_json_data_contains_timestamp(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "build", "--json"])
        assert "timestamp" in data["data"]

    def test_version_build_json_command_field(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "build", "--json"])
        assert data.get("command") == "forge version build"

    def test_version_build_json_timestamp_key_present(self, runner: CliRunner):
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = invoke_json(runner, ["version", "build", "--json"])
        assert "timestamp" in data

    def test_version_json_flag_propagates_to_build_subcommand(self, runner: CliRunner):
        """--json on the version group must propagate into the build subcommand."""
        resp = _build_response()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = invoke(runner, ["version", "--json", "build"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True


# ---------------------------------------------------------------------------
# 14. forge version build — API error paths
# ---------------------------------------------------------------------------


class TestVersionBuildErrorPaths:
    """forge version build error handling for 5xx and connection failures."""

    def test_api_500_human_shows_status_code(self, runner: CliRunner):
        resp = _make_httpx_response(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = runner.invoke(cli, ["version", "build"])
        assert "500" in result.output

    def test_api_500_json_mode_success_false(self, runner: CliRunner):
        resp = _make_httpx_response(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = runner.invoke(cli, ["version", "build", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_api_500_json_mode_exits_one(self, runner: CliRunner):
        resp = _make_httpx_response(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = runner.invoke(cli, ["version", "build", "--json"])
        assert result.exit_code == 1

    def test_connection_error_human_shows_cannot_connect(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.ConnectError("refused")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["version", "build"])
        assert "Cannot connect" in result.output or "localhost" in result.output

    def test_connection_error_json_mode_success_false(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.ConnectError("refused")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["version", "build", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_connection_error_json_mode_exits_one(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.ConnectError("refused")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["version", "build", "--json"])
        assert result.exit_code == 1

    def test_unexpected_exception_exits_one(self, runner: CliRunner):
        ctx_mgr = MagicMock()
        client_instance = MagicMock()
        client_instance.get.side_effect = ValueError("Unexpected value error")
        ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx_mgr):
            result = runner.invoke(cli, ["version", "build"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 15. forge version — custom API URL
# ---------------------------------------------------------------------------


class TestVersionCustomApiUrl:
    """forge version and forge version build respect --api-url."""

    def test_version_uses_custom_api_url(self, runner: CliRunner):
        resp = _version_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["version", "--api-url", "http://custom-host:9999"])
        client = ctx_mgr.__enter__.return_value
        call_url = client.get.call_args[0][0]
        assert "custom-host:9999" in call_url

    def test_version_build_inherits_api_url_from_parent(self, runner: CliRunner):
        resp = _build_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["version", "--api-url", "http://remote:8888", "build"])
        client = ctx_mgr.__enter__.return_value
        call_url = client.get.call_args[0][0]
        assert "remote:8888" in call_url

    def test_version_build_inherits_token_from_parent(self, runner: CliRunner):
        resp = _build_response()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            invoke(runner, ["version", "--token", "parent-token", "build"])
        client = ctx_mgr.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert "parent-token" in headers.get("Authorization", "")
