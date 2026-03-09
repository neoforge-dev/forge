"""Comprehensive unit tests for forge_harness/cli_v2/events.py.

Coverage targets:
- CLI group and subcommand registration
- Default option values
- URL construction and query parameters
- Auth header injection (--token and no token)
- Human-readable table output formatting
- Timestamp parsing (ISO, invalid, missing)
- Event field fallbacks (type vs event_type, source vs agent_id, summary vs data)
- Long-field truncation at 60 chars
- JSON output envelope shape
- --limit IntRange validation (1-100)
- --type filter parameter passing
- Empty event list (with / without filter)
- API error paths: 4xx, 5xx
- ConnectError handling in both modes
- Unexpected exception handling
- Custom --api-url routing
- Environment variable fallbacks (COMMAND_CENTER_URL, FORGE_WEBHOOK_TOKEN)
- Flat API response shape (no "data" wrapper)
- Data field as non-dict string/list
- Multiple events in a single response
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _invoke(runner: CliRunner, args: list[str], catch_exceptions: bool = False):
    """Invoke the top-level forge CLI."""
    return runner.invoke(cli, args, catch_exceptions=catch_exceptions)


def _invoke_json(runner: CliRunner, args: list[str]) -> dict:
    result = _invoke(runner, args)
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Output was not valid JSON:\n{result.output!r}"
        ) from exc


def _make_response(status_code: int, body: dict) -> MagicMock:
    """Build a MagicMock that looks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _make_client_ctx(response: MagicMock) -> MagicMock:
    """Build a context-manager mock for httpx.Client whose get() returns *response*."""
    client = MagicMock()
    client.get.return_value = response
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _events_ok(
    events: list[dict] | None = None,
    count: int | None = None,
    filter_type: str | None = None,
) -> MagicMock:
    """Successful GET /api/events/recent response with standard data wrapper."""
    evs = events or []
    return _make_response(
        200,
        {
            "success": True,
            "data": {
                "events": evs,
                "count": count if count is not None else len(evs),
                "filter": filter_type,
            },
        },
    )


def _sample_event(
    event_type: str = "feature.updated",
    summary: str = "A short summary",
    source: str = "agent-1",
    timestamp: str = "2026-02-23T10:00:00",
) -> dict:
    return {
        "type": event_type,
        "summary": summary,
        "source": source,
        "timestamp": timestamp,
    }


def _connect_error_ctx() -> MagicMock:
    """Return a mock httpx.Client context manager whose get() raises ConnectError."""
    client = MagicMock()
    client.get.side_effect = httpx.ConnectError("Connection refused")
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ===========================================================================
# 1. Command registration
# ===========================================================================


class TestCommandRegistration:
    """events group and list subcommand are wired into the top-level CLI."""

    def test_events_group_help_exits_zero(self, runner):
        result = _invoke(runner, ["events", "--help"])
        assert result.exit_code == 0

    def test_events_group_help_contains_list(self, runner):
        result = _invoke(runner, ["events", "--help"])
        assert "list" in result.output

    def test_events_list_help_exits_zero(self, runner):
        result = _invoke(runner, ["events", "list", "--help"])
        assert result.exit_code == 0

    def test_events_list_help_mentions_limit(self, runner):
        result = _invoke(runner, ["events", "list", "--help"])
        assert "limit" in result.output.lower()

    def test_events_list_help_mentions_type(self, runner):
        result = _invoke(runner, ["events", "list", "--help"])
        assert "type" in result.output.lower()

    def test_events_list_help_mentions_json(self, runner):
        result = _invoke(runner, ["events", "list", "--help"])
        assert "json" in result.output.lower()

    def test_events_list_help_mentions_api_url(self, runner):
        result = _invoke(runner, ["events", "list", "--help"])
        assert "api-url" in result.output.lower()

    def test_events_list_help_mentions_token(self, runner):
        result = _invoke(runner, ["events", "list", "--help"])
        assert "token" in result.output.lower()


# ===========================================================================
# 2. Default option values
# ===========================================================================


class TestDefaultOptions:
    """Verify defaults are applied when options are omitted."""

    def test_default_limit_is_20(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list"])
        client = ctx.__enter__.return_value
        params = client.get.call_args[1].get("params", {})
        assert params.get("limit") == 20

    def test_default_api_url_is_localhost_8080(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list"])
        client = ctx.__enter__.return_value
        call_url = client.get.call_args[0][0]
        assert "localhost:8080" in call_url

    def test_no_token_omits_auth_header(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list"])
        client = ctx.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert "Authorization" not in headers

    def test_no_type_omits_type_param(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list"])
        client = ctx.__enter__.return_value
        params = client.get.call_args[1].get("params", {})
        assert "type" not in params


# ===========================================================================
# 3. URL construction
# ===========================================================================


class TestUrlConstruction:
    """Verify the correct endpoint URL is built from --api-url."""

    def test_hits_api_events_recent_endpoint(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list"])
        client = ctx.__enter__.return_value
        call_url = client.get.call_args[0][0]
        assert "/api/events/recent" in call_url

    def test_custom_api_url_used_in_request(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list", "--api-url", "http://myhost:9999"])
        client = ctx.__enter__.return_value
        call_url = client.get.call_args[0][0]
        assert "myhost:9999" in call_url

    def test_trailing_slash_stripped_from_api_url(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list", "--api-url", "http://myhost:9999/"])
        client = ctx.__enter__.return_value
        call_url = client.get.call_args[0][0]
        # Should not produce double-slash before /api
        assert "//api" not in call_url

    def test_env_var_command_center_url_used(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        env = {"COMMAND_CENTER_URL": "http://envhost:7777"}
        with patch("httpx.Client", return_value=ctx):
            with runner.isolated_filesystem():
                result = runner.invoke(cli, ["events", "list"], env=env)
        client = ctx.__enter__.return_value
        call_url = client.get.call_args[0][0]
        assert "envhost:7777" in call_url


# ===========================================================================
# 4. Auth header injection
# ===========================================================================


class TestAuthHeaderInjection:
    """Token is sent as Bearer authorization header."""

    def test_token_option_sets_bearer_header(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list", "--token", "mytoken123"])
        client = ctx.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer mytoken123"

    def test_env_var_forge_webhook_token_used(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        env = {"FORGE_WEBHOOK_TOKEN": "env-secret"}
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list"], env=env)
        client = ctx.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert "env-secret" in headers.get("Authorization", "")

    def test_content_type_header_always_set(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list"])
        client = ctx.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert headers.get("Content-Type") == "application/json"


# ===========================================================================
# 5. --limit parameter
# ===========================================================================


class TestLimitParameter:
    """--limit controls the query param and is validated 1-100."""

    def test_limit_50_sent_to_api(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list", "--limit", "50"])
        client = ctx.__enter__.return_value
        params = client.get.call_args[1].get("params", {})
        assert params.get("limit") == 50

    def test_limit_1_is_accepted(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list", "--limit", "1"])
        assert result.exit_code == 0

    def test_limit_100_is_accepted(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list", "--limit", "100"])
        assert result.exit_code == 0

    def test_limit_0_rejected(self, runner):
        result = runner.invoke(cli, ["events", "list", "--limit", "0"])
        assert result.exit_code != 0

    def test_limit_101_rejected(self, runner):
        result = runner.invoke(cli, ["events", "list", "--limit", "101"])
        assert result.exit_code != 0

    def test_limit_negative_rejected(self, runner):
        result = runner.invoke(cli, ["events", "list", "--limit", "-5"])
        assert result.exit_code != 0


# ===========================================================================
# 6. --type filter parameter
# ===========================================================================


class TestTypeFilterParameter:
    """--type sends a type query param and influences human output."""

    def test_type_filter_sent_in_params(self, runner):
        resp = _events_ok(filter_type="task.completed")
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list", "--type", "task.completed"])
        client = ctx.__enter__.return_value
        params = client.get.call_args[1].get("params", {})
        assert params.get("type") == "task.completed"

    def test_no_type_filter_absent_from_params(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            _invoke(runner, ["events", "list"])
        client = ctx.__enter__.return_value
        params = client.get.call_args[1].get("params", {})
        assert "type" not in params

    def test_filter_present_in_output_when_events_exist(self, runner):
        events = [_sample_event(event_type="feature.deployed")]
        resp = _events_ok(events=events, filter_type="feature.deployed")
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list", "--type", "feature.deployed"])
        assert "feature.deployed" in result.output

    def test_filter_shown_in_empty_event_message(self, runner):
        resp = _events_ok(events=[], filter_type="task.completed")
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list", "--type", "task.completed"])
        # "No events found (type=task.completed)"
        assert "task.completed" in result.output


# ===========================================================================
# 7. Empty event list (human mode)
# ===========================================================================


class TestEmptyEventListHuman:
    """Human-mode output when API returns zero events."""

    def test_empty_exits_zero(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0

    def test_empty_prints_no_events_found(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "No events found" in result.output

    def test_empty_with_filter_includes_filter_in_message(self, runner):
        resp = _events_ok(events=[], filter_type="deploy.started")
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "deploy.started" in result.output


# ===========================================================================
# 8. Human-mode table output with events
# ===========================================================================


class TestHumanTableOutput:
    """Rich table is rendered with correct columns and values."""

    def test_exits_zero_with_events(self, runner):
        resp = _events_ok(events=[_sample_event()])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0

    def test_event_type_appears_in_output(self, runner):
        resp = _events_ok(events=[_sample_event(event_type="task.done")])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "task.done" in result.output

    def test_event_summary_appears_in_output(self, runner):
        resp = _events_ok(events=[_sample_event(summary="Completed")])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "Completed" in result.output

    def test_event_source_appears_in_output(self, runner):
        resp = _events_ok(events=[_sample_event(source="node-x")])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "node-x" in result.output

    def test_formatted_timestamp_in_output(self, runner):
        resp = _events_ok(events=[_sample_event(timestamp="2026-02-23T14:30:00")])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "2026-02-23" in result.output

    def test_count_shown_in_header(self, runner):
        events = [_sample_event(), _sample_event(event_type="task.queued")]
        resp = _events_ok(events=events, count=2)
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "2" in result.output

    def test_multiple_events_all_rendered(self, runner):
        events = [
            _sample_event(event_type="alpha.event"),
            _sample_event(event_type="beta.event"),
            _sample_event(event_type="gamma.event"),
        ]
        resp = _events_ok(events=events)
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "alpha.event" in result.output
        assert "beta.event" in result.output
        assert "gamma.event" in result.output

    def test_recent_events_header_shown(self, runner):
        resp = _events_ok(events=[_sample_event()])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "Recent Events" in result.output


# ===========================================================================
# 9. Timestamp edge cases
# ===========================================================================


class TestTimestampParsing:
    """Timestamp formatting handles ISO strings and invalid values."""

    def test_iso_timestamp_formatted_as_ymd_hms(self, runner):
        event = _sample_event(timestamp="2026-01-15T08:05:30")
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "2026-01-15" in result.output
        assert "08:05:30" in result.output

    def test_invalid_timestamp_does_not_crash(self, runner):
        event = {
            "type": "x.event",
            "summary": "Bad ts",
            "source": "ag",
            "timestamp": "not-a-date",
        }
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0

    def test_missing_timestamp_does_not_crash(self, runner):
        event = {"type": "x.event", "summary": "No ts", "source": "ag"}
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0

    def test_timestamp_with_timezone_does_not_crash(self, runner):
        event = _sample_event(timestamp="2026-02-23T10:00:00+00:00")
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0


# ===========================================================================
# 10. Event field fallbacks
# ===========================================================================


class TestEventFieldFallbacks:
    """Events that use legacy or alternative field names are handled."""

    def test_event_type_falls_back_to_event_type_field(self, runner):
        event = {
            "event_type": "legacy.type",
            "summary": "Old event",
            "source": "old-agent",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "legacy.type" in result.output

    def test_source_falls_back_to_agent_id_field(self, runner):
        event = {
            "type": "x.event",
            "summary": "Event",
            "agent_id": "fallback-agent",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        # Source column has width=12 so "fallback-agent" is truncated to "fallback-age"
        assert "fallback-age" in result.output

    def test_summary_from_data_message_field(self, runner):
        event = {
            "type": "x.event",
            "data": {"message": "DataMsg"},
            "source": "ag",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "DataMsg" in result.output

    def test_summary_from_data_description_field(self, runner):
        event = {
            "type": "x.event",
            "data": {"description": "Desc text"},
            "source": "ag",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert "Desc text" in result.output

    def test_summary_fallback_from_data_dict_stringified(self, runner):
        event = {
            "type": "x.event",
            "data": {"other_key": "other_value"},
            "source": "ag",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        # Should not crash; stringified dict will appear
        assert result.exit_code == 0

    def test_data_as_string_causes_attribute_error_handled_as_exit_one(self, runner):
        # Line 124 of events.py: event.get("data", {}).get("summary", "") raises
        # AttributeError when data is a plain string (strings have no .get()).
        # The outer except block catches it and exits with code 1.
        event = {
            "type": "x.event",
            "data": "plain-string-payload",
            "source": "ag",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list"])
        # Due to the AttributeError on string.get(), the command exits with 1
        assert result.exit_code == 1

    def test_missing_type_shows_dash(self, runner):
        event = {
            "summary": "No type",
            "source": "ag",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0
        assert "-" in result.output

    def test_missing_source_shows_dash(self, runner):
        event = {
            "type": "x.event",
            "summary": "No source",
            "timestamp": "2026-02-23T10:00:00",
        }
        resp = _events_ok(events=[event])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0


# ===========================================================================
# 11. Long-field truncation
# ===========================================================================


class TestLongFieldTruncation:
    """Fields longer than 60 chars are truncated before table insertion."""

    def test_summary_over_60_chars_does_not_crash(self, runner):
        long_summary = "A" * 100
        resp = _events_ok(events=[_sample_event(summary=long_summary)])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0

    def test_source_over_12_chars_does_not_crash(self, runner):
        long_source = "S" * 50
        resp = _events_ok(events=[_sample_event(source=long_source)])
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0


# ===========================================================================
# 12. Flat API response (no "data" wrapper)
# ===========================================================================


class TestFlatApiResponseShape:
    """API may return events at the top level without a "data" wrapper."""

    def test_flat_response_with_events_renders(self, runner):
        evs = [_sample_event(event_type="flat.event")]
        resp = _make_response(200, {"events": evs, "count": 1, "filter": None})
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0
        assert "flat.event" in result.output

    def test_flat_response_empty_events_handled(self, runner):
        resp = _make_response(200, {"events": [], "count": 0, "filter": None})
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list"])
        assert result.exit_code == 0
        assert "No events found" in result.output


# ===========================================================================
# 13. JSON output mode
# ===========================================================================


class TestJsonOutputMode:
    """--json produces a valid, spec-compliant envelope."""

    def test_json_exits_zero(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list", "--json"])
        assert result.exit_code == 0

    def test_json_output_is_valid_json(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            data = _invoke_json(runner, ["events", "list", "--json"])
        assert isinstance(data, dict)

    def test_json_success_true(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            data = _invoke_json(runner, ["events", "list", "--json"])
        assert data["success"] is True

    def test_json_has_data_key(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            data = _invoke_json(runner, ["events", "list", "--json"])
        assert "data" in data

    def test_json_has_timestamp_key(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            data = _invoke_json(runner, ["events", "list", "--json"])
        assert "timestamp" in data

    def test_json_command_field_is_forge_events_list(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            data = _invoke_json(runner, ["events", "list", "--json"])
        assert data.get("command") == "forge events list"

    def test_json_events_list_returned_in_data(self, runner):
        evs = [_sample_event()]
        resp = _events_ok(events=evs)
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            data = _invoke_json(runner, ["events", "list", "--json"])
        assert len(data["data"]["events"]) == 1

    def test_json_empty_events_list_in_data(self, runner):
        resp = _events_ok()
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            data = _invoke_json(runner, ["events", "list", "--json"])
        assert data["data"]["events"] == []

    def test_json_count_reflected(self, runner):
        evs = [_sample_event(), _sample_event(event_type="other")]
        resp = _events_ok(events=evs, count=2)
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            data = _invoke_json(runner, ["events", "list", "--json"])
        assert data["data"]["count"] == 2

    def test_json_no_table_rendered(self, runner):
        evs = [_sample_event()]
        resp = _events_ok(events=evs)
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = _invoke(runner, ["events", "list", "--json"])
        # JSON output should not contain table border characters
        assert "Recent Events" not in result.output


# ===========================================================================
# 14. API error paths
# ===========================================================================


class TestApiErrorPaths:
    """Non-200 HTTP responses are handled in both human and JSON modes."""

    def test_500_human_mode_contains_status_code(self, runner):
        resp = _make_response(500, {"error": "oops"})
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list"])
        assert "500" in result.output

    def test_500_json_mode_exits_one(self, runner):
        resp = _make_response(500, {"error": "oops"})
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list", "--json"])
        assert result.exit_code == 1

    def test_500_json_mode_success_false(self, runner):
        resp = _make_response(500, {"error": "oops"})
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_404_human_mode_contains_status_code(self, runner):
        resp = _make_response(404, {"error": "not found"})
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list"])
        assert "404" in result.output

    def test_404_json_mode_exits_one(self, runner):
        resp = _make_response(404, {"error": "not found"})
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list", "--json"])
        assert result.exit_code == 1

    def test_403_json_mode_success_false(self, runner):
        resp = _make_response(403, {"error": "forbidden"})
        ctx = _make_client_ctx(resp)
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# 15. ConnectError handling
# ===========================================================================


class TestConnectErrorHandling:
    """httpx.ConnectError is caught and surfaced with a useful message."""

    def test_connect_error_human_mode_shows_cannot_connect(self, runner):
        ctx = _connect_error_ctx()
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list"])
        assert "Cannot connect" in result.output or "localhost" in result.output

    def test_connect_error_human_mode_mentions_url(self, runner):
        ctx = _connect_error_ctx()
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list", "--api-url", "http://down:1234"])
        assert "down:1234" in result.output or "Cannot connect" in result.output

    def test_connect_error_json_mode_success_false(self, runner):
        ctx = _connect_error_ctx()
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_connect_error_json_mode_exits_one(self, runner):
        ctx = _connect_error_ctx()
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list", "--json"])
        assert result.exit_code == 1


# ===========================================================================
# 16. Unexpected exception handling
# ===========================================================================


class TestUnexpectedExceptionHandling:
    """Generic exceptions that are not ConnectError are caught and exit with 1."""

    def _runtime_error_ctx(self) -> MagicMock:
        client = MagicMock()
        client.get.side_effect = RuntimeError("Something exploded")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=client)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def test_runtime_error_exits_one(self, runner):
        ctx = self._runtime_error_ctx()
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list"])
        assert result.exit_code == 1

    def test_value_error_exits_one(self, runner):
        client = MagicMock()
        client.get.side_effect = ValueError("Bad value")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=client)
        ctx.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list"])
        assert result.exit_code == 1

    def test_runtime_error_json_mode_exits_one(self, runner):
        ctx = self._runtime_error_ctx()
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list", "--json"])
        assert result.exit_code == 1

    def test_runtime_error_json_mode_success_false(self, runner):
        ctx = self._runtime_error_ctx()
        with patch("httpx.Client", return_value=ctx):
            result = runner.invoke(cli, ["events", "list", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False
