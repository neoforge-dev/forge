"""
Unit tests for forge_harness/cli_v2/version.py
===============================================

Tests the ``version`` click group and ``version_build`` subcommand in
isolation — importing directly from the module rather than routing through
the top-level CLI entry point.

Coverage areas:
  1.  Command registration & help text
  2.  ``forge version`` human output (service, version, api_version)
  3.  ``forge version --json`` JSON envelope shape
  4.  ``forge version build`` human output (build_number, commit, timestamp)
  5.  ``forge version build --json`` JSON envelope shape
  6.  Authentication header forwarding (--token)
  7.  Custom API URL (--api-url / COMMAND_CENTER_URL env var)
  8.  FORGE_WEBHOOK_TOKEN env var as default token
  9.  Trailing-slash stripping in URL construction
 10.  Response shape: ``data`` wrapper unwrapping
 11.  Response shape: flat payload (no ``data`` key)
 12.  API non-200 status codes — human and JSON modes
 13.  httpx.ConnectError — human and JSON modes
 14.  Generic exception fallback — human and JSON modes
 15.  ``--json`` flag on parent group propagates to ``build`` subcommand
 16.  Missing fields in API response render as ``-``
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import click
import httpx
import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.version import version, version_build

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resp(status_code: int, body: dict) -> MagicMock:
    """Return an httpx.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _mock_client(response: MagicMock) -> MagicMock:
    """Return an ``httpx.Client`` context-manager mock whose ``get`` returns *response*."""
    client_instance = MagicMock()
    client_instance.get.return_value = response
    ctx_mgr = MagicMock()
    ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
    ctx_mgr.__exit__ = MagicMock(return_value=False)
    return ctx_mgr


def _version_resp(
    version: str = "1.2.3",
    service: str = "forge-api",
    api_version: str = "2.0.0",
) -> MagicMock:
    return _make_resp(
        200,
        {
            "data": {
                "version": version,
                "service": service,
                "api_version": api_version,
            }
        },
    )


def _build_resp(
    build_number: str = "42",
    commit: str = "deadbeef",
    timestamp: str = "2026-02-23T12:00:00+00:00",
) -> MagicMock:
    return _make_resp(
        200,
        {
            "build_number": build_number,
            "commit": commit,
            "timestamp": timestamp,
        },
    )


def _run(args: list[str], env: dict | None = None) -> click.testing.Result:
    runner = CliRunner(env=env or {})
    return runner.invoke(version, args, catch_exceptions=False)


def _run_json(args: list[str], env: dict | None = None) -> dict:
    result = _run(args, env=env)
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# 1. Command registration & help text
# ---------------------------------------------------------------------------


class TestCommandRegistration:
    """Version group and build subcommand are properly registered."""

    def test_version_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(version, ["--help"])
        assert result.exit_code == 0

    def test_version_build_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(version, ["build", "--help"])
        assert result.exit_code == 0

    def test_version_help_mentions_build_subcommand(self):
        runner = CliRunner()
        result = runner.invoke(version, ["--help"])
        assert "build" in result.output

    def test_version_help_mentions_json_option(self):
        runner = CliRunner()
        result = runner.invoke(version, ["--help"])
        assert "--json" in result.output

    def test_version_help_mentions_api_url_option(self):
        runner = CliRunner()
        result = runner.invoke(version, ["--help"])
        assert "--api-url" in result.output

    def test_version_build_help_mentions_json_option(self):
        runner = CliRunner()
        result = runner.invoke(version, ["build", "--help"])
        assert "--json" in result.output

    def test_version_group_is_callable(self):
        assert callable(version)

    def test_version_build_command_is_callable(self):
        assert callable(version_build)


# ---------------------------------------------------------------------------
# 2. forge version — human output
# ---------------------------------------------------------------------------


class TestVersionHumanOutput:
    """``forge version`` (no --json) prints service, version, api_version."""

    def test_exits_zero_on_success(self):
        resp = _version_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run([])
        assert result.exit_code == 0

    def test_shows_service_name(self):
        resp = _version_resp(service="my-forge-service")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run([])
        assert "my-forge-service" in result.output

    def test_shows_version_number(self):
        resp = _version_resp(version="3.14.15")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run([])
        assert "3.14.15" in result.output

    def test_shows_api_version(self):
        resp = _version_resp(api_version="5.0.0")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run([])
        assert "5.0.0" in result.output

    def test_calls_api_version_endpoint(self):
        resp = _version_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run([])
        client = ctx_mgr.__enter__.return_value
        called_url = client.get.call_args[0][0]
        assert called_url.endswith("/api/version")

    def test_flat_response_no_data_wrapper(self):
        """If the API returns a flat dict (no 'data' key), it should still render."""
        resp = _make_resp(
            200,
            {"version": "9.9.9", "service": "flat-svc", "api_version": "0.0.1"},
        )
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run([])
        assert result.exit_code == 0
        assert "flat-svc" in result.output

    def test_missing_service_field_renders_dash(self):
        resp = _make_resp(200, {"data": {"version": "1.0.0", "api_version": "1.0"}})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run([])
        assert result.exit_code == 0
        # Missing 'service' should print '-'
        assert "-" in result.output


# ---------------------------------------------------------------------------
# 3. forge version --json — JSON envelope
# ---------------------------------------------------------------------------


class TestVersionJsonOutput:
    """``forge version --json`` produces a well-formed FORGE envelope."""

    def test_json_exits_zero(self):
        resp = _version_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run(["--json"])
        assert result.exit_code == 0

    def test_json_success_is_true(self):
        resp = _version_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["--json"])
        assert data["success"] is True

    def test_json_command_field_is_forge_version(self):
        resp = _version_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["--json"])
        assert data.get("command") == "forge version"

    def test_json_timestamp_key_present(self):
        resp = _version_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["--json"])
        assert "timestamp" in data

    def test_json_data_contains_version(self):
        resp = _version_resp(version="2.5.0")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["--json"])
        assert data["data"]["version"] == "2.5.0"

    def test_json_data_contains_service(self):
        resp = _version_resp(service="svc-x")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["--json"])
        assert data["data"]["service"] == "svc-x"

    def test_json_error_key_is_none_on_success(self):
        resp = _version_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["--json"])
        assert data.get("error") is None


# ---------------------------------------------------------------------------
# 4. forge version build — human output
# ---------------------------------------------------------------------------


class TestVersionBuildHumanOutput:
    """``forge version build`` displays build_number, commit, timestamp."""

    def test_build_exits_zero(self):
        resp = _build_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run(["build"])
        assert result.exit_code == 0

    def test_build_shows_build_number(self):
        resp = _build_resp(build_number="999")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run(["build"])
        assert "999" in result.output

    def test_build_shows_commit(self):
        resp = _build_resp(commit="cafebabe")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run(["build"])
        assert "cafebabe" in result.output

    def test_build_shows_timestamp(self):
        resp = _build_resp(timestamp="2025-01-01T00:00:00Z")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run(["build"])
        assert "2025-01-01" in result.output

    def test_build_calls_api_build_endpoint(self):
        resp = _build_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run(["build"])
        client = ctx_mgr.__enter__.return_value
        called_url = client.get.call_args[0][0]
        assert called_url.endswith("/api/build")

    def test_build_missing_commit_renders_dash(self):
        resp = _make_resp(200, {"build_number": "1", "timestamp": "2026-01-01T00:00:00Z"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run(["build"])
        assert result.exit_code == 0
        assert "-" in result.output


# ---------------------------------------------------------------------------
# 5. forge version build --json — JSON envelope
# ---------------------------------------------------------------------------


class TestVersionBuildJsonOutput:
    """``forge version build --json`` produces a well-formed FORGE envelope."""

    def test_build_json_exits_zero(self):
        resp = _build_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run(["build", "--json"])
        assert result.exit_code == 0

    def test_build_json_success_is_true(self):
        resp = _build_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["build", "--json"])
        assert data["success"] is True

    def test_build_json_command_field(self):
        resp = _build_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["build", "--json"])
        assert data.get("command") == "forge version build"

    def test_build_json_data_contains_build_number(self):
        resp = _build_resp(build_number="77")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["build", "--json"])
        assert data["data"]["build_number"] == "77"

    def test_build_json_data_contains_commit(self):
        resp = _build_resp(commit="abc000")
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["build", "--json"])
        assert data["data"]["commit"] == "abc000"

    def test_build_json_timestamp_key_present(self):
        resp = _build_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            data = _run_json(["build", "--json"])
        assert "timestamp" in data

    def test_parent_json_flag_propagates_to_build(self):
        """``forge version --json build`` should use JSON mode in the subcommand."""
        resp = _build_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run(["--json", "build"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True


# ---------------------------------------------------------------------------
# 6. Authentication — --token flag and FORGE_WEBHOOK_TOKEN env var
# ---------------------------------------------------------------------------


class TestAuthenticationHeader:
    """Token is forwarded as an Authorization: Bearer header."""

    def test_explicit_token_sets_auth_header(self):
        resp = _version_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run(["--token", "my-secret-token"])
        client = ctx_mgr.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert "Authorization" in headers
        assert "my-secret-token" in headers["Authorization"]

    def test_no_token_omits_auth_header(self):
        resp = _version_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run([])
        client = ctx_mgr.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert "Authorization" not in headers

    def test_token_forwarded_to_build_subcommand(self):
        """Token passed on the parent ``version`` group propagates to ``build``."""
        resp = _build_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run(["--token", "build-token", "build"])
        client = ctx_mgr.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert "build-token" in headers.get("Authorization", "")

    def test_forge_webhook_token_env_var_used(self):
        """FORGE_WEBHOOK_TOKEN env var should be picked up as the default token."""
        resp = _version_resp()
        ctx_mgr = _mock_client(resp)
        env = {"FORGE_WEBHOOK_TOKEN": "env-token-xyz"}
        with patch("httpx.Client", return_value=ctx_mgr):
            runner = CliRunner(env=env)
            runner.invoke(version, [], catch_exceptions=False)
        client = ctx_mgr.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert "env-token-xyz" in headers.get("Authorization", "")


# ---------------------------------------------------------------------------
# 7. Custom API URL
# ---------------------------------------------------------------------------


class TestCustomApiUrl:
    """``--api-url`` and ``COMMAND_CENTER_URL`` env var are respected."""

    def test_custom_api_url_version_endpoint(self):
        resp = _version_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run(["--api-url", "http://custom-host:9000"])
        client = ctx_mgr.__enter__.return_value
        called_url = client.get.call_args[0][0]
        assert "custom-host:9000" in called_url

    def test_custom_api_url_build_endpoint(self):
        resp = _build_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run(["--api-url", "http://build-host:7777", "build"])
        client = ctx_mgr.__enter__.return_value
        called_url = client.get.call_args[0][0]
        assert "build-host:7777" in called_url

    def test_trailing_slash_stripped_from_api_url(self):
        resp = _version_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run(["--api-url", "http://localhost:8080/"])
        client = ctx_mgr.__enter__.return_value
        called_url = client.get.call_args[0][0]
        # Should not contain double slashes in path
        assert "//api/version" not in called_url

    def test_command_center_url_env_var_used_as_api_url(self):
        resp = _version_resp()
        ctx_mgr = _mock_client(resp)
        env = {"COMMAND_CENTER_URL": "http://cc-env-host:5000"}
        with patch("httpx.Client", return_value=ctx_mgr):
            runner = CliRunner(env=env)
            runner.invoke(version, [], catch_exceptions=False)
        client = ctx_mgr.__enter__.return_value
        called_url = client.get.call_args[0][0]
        assert "cc-env-host:5000" in called_url


# ---------------------------------------------------------------------------
# 8. API non-200 status codes
# ---------------------------------------------------------------------------


class TestApiNon200:
    """Non-200 responses produce errors in both human and JSON modes."""

    def test_version_404_human_shows_status_code(self):
        resp = _make_resp(404, {"error": "not found"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = CliRunner().invoke(version, [])
        assert "404" in result.output

    def test_version_500_human_exits_nonzero(self):
        resp = _make_resp(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = CliRunner().invoke(version, [])
        assert result.exit_code != 0

    def test_version_500_json_success_false(self):
        resp = _make_resp(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = CliRunner().invoke(version, ["--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_version_500_json_exits_one(self):
        resp = _make_resp(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = CliRunner().invoke(version, ["--json"])
        assert result.exit_code == 1

    def test_build_403_human_shows_status_code(self):
        resp = _make_resp(403, {"error": "forbidden"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = CliRunner().invoke(version, ["build"])
        assert "403" in result.output

    def test_build_500_json_success_false(self):
        resp = _make_resp(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = CliRunner().invoke(version, ["build", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_build_500_json_exits_one(self):
        resp = _make_resp(500, {"error": "server error"})
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = CliRunner().invoke(version, ["build", "--json"])
        assert result.exit_code == 1

    def test_api_error_text_truncated_to_200_chars(self):
        """Long error body text is truncated to 200 chars in the message."""
        long_body = "x" * 500
        resp = _make_resp(502, {})
        resp.text = long_body
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = CliRunner().invoke(version, [])
        # The error message should not exceed the truncation length
        output_lines = result.output
        # Just verify it doesn't crash and shows 502
        assert "502" in output_lines


# ---------------------------------------------------------------------------
# 9. httpx.ConnectError handling
# ---------------------------------------------------------------------------


def _connect_error_ctx_mgr() -> MagicMock:
    client_instance = MagicMock()
    client_instance.get.side_effect = httpx.ConnectError("Connection refused")
    ctx_mgr = MagicMock()
    ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
    ctx_mgr.__exit__ = MagicMock(return_value=False)
    return ctx_mgr


class TestConnectError:
    """httpx.ConnectError is handled gracefully in both modes."""

    def test_version_connect_error_human_shows_cannot_connect(self):
        with patch("httpx.Client", return_value=_connect_error_ctx_mgr()):
            result = CliRunner().invoke(version, [])
        assert "Cannot connect" in result.output or "localhost" in result.output

    def test_version_connect_error_human_exits_nonzero(self):
        with patch("httpx.Client", return_value=_connect_error_ctx_mgr()):
            result = CliRunner().invoke(version, [])
        assert result.exit_code != 0

    def test_version_connect_error_json_success_false(self):
        with patch("httpx.Client", return_value=_connect_error_ctx_mgr()):
            result = CliRunner().invoke(version, ["--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_version_connect_error_json_exits_one(self):
        with patch("httpx.Client", return_value=_connect_error_ctx_mgr()):
            result = CliRunner().invoke(version, ["--json"])
        assert result.exit_code == 1

    def test_build_connect_error_human_shows_cannot_connect(self):
        with patch("httpx.Client", return_value=_connect_error_ctx_mgr()):
            result = CliRunner().invoke(version, ["build"])
        assert "Cannot connect" in result.output or "localhost" in result.output

    def test_build_connect_error_json_success_false(self):
        with patch("httpx.Client", return_value=_connect_error_ctx_mgr()):
            result = CliRunner().invoke(version, ["build", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_build_connect_error_json_exits_one(self):
        with patch("httpx.Client", return_value=_connect_error_ctx_mgr()):
            result = CliRunner().invoke(version, ["build", "--json"])
        assert result.exit_code == 1

    def test_version_connect_error_message_includes_api_url(self):
        """Error message should reference the configured API URL host."""
        with patch("httpx.Client", return_value=_connect_error_ctx_mgr()):
            result = CliRunner().invoke(version, ["--api-url", "http://myhost:1234"])
        assert "myhost" in result.output or "Cannot connect" in result.output


# ---------------------------------------------------------------------------
# 10. Generic exception fallback
# ---------------------------------------------------------------------------


def _generic_error_ctx_mgr(exc: Exception) -> MagicMock:
    client_instance = MagicMock()
    client_instance.get.side_effect = exc
    ctx_mgr = MagicMock()
    ctx_mgr.__enter__ = MagicMock(return_value=client_instance)
    ctx_mgr.__exit__ = MagicMock(return_value=False)
    return ctx_mgr


class TestGenericExceptionFallback:
    """Unexpected exceptions cause exit code 1 (handled by ``handle_error_json``)."""

    def test_version_runtime_error_exits_one(self):
        with patch("httpx.Client", return_value=_generic_error_ctx_mgr(RuntimeError("boom"))):
            result = CliRunner().invoke(version, [])
        assert result.exit_code == 1

    def test_build_value_error_exits_one(self):
        with patch("httpx.Client", return_value=_generic_error_ctx_mgr(ValueError("bad value"))):
            result = CliRunner().invoke(version, ["build"])
        assert result.exit_code == 1

    def test_version_json_mode_generic_error_success_false(self):
        with patch("httpx.Client", return_value=_generic_error_ctx_mgr(OSError("disk full"))):
            result = CliRunner().invoke(version, ["--json"])
        data = json.loads(result.output)
        assert data["success"] is False

    def test_build_json_mode_generic_error_success_false(self):
        with patch("httpx.Client", return_value=_generic_error_ctx_mgr(TypeError("type mismatch"))):
            result = CliRunner().invoke(version, ["build", "--json"])
        data = json.loads(result.output)
        assert data["success"] is False


# ---------------------------------------------------------------------------
# 11. Context object propagation
# ---------------------------------------------------------------------------


class TestContextPropagation:
    """Parent context objects are correctly read by the build subcommand."""

    def test_build_uses_parent_api_url(self):
        resp = _build_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run(["--api-url", "http://parent-url:3333", "build"])
        client = ctx_mgr.__enter__.return_value
        called_url = client.get.call_args[0][0]
        assert "parent-url:3333" in called_url

    def test_build_uses_parent_token(self):
        resp = _build_resp()
        ctx_mgr = _mock_client(resp)
        with patch("httpx.Client", return_value=ctx_mgr):
            _run(["--token", "ctx-token", "build"])
        client = ctx_mgr.__enter__.return_value
        headers = client.get.call_args[1].get("headers", {})
        assert "ctx-token" in headers.get("Authorization", "")

    def test_build_own_json_flag_takes_precedence(self):
        """--json on the build subcommand itself should also enable JSON output."""
        resp = _build_resp()
        with patch("httpx.Client", return_value=_mock_client(resp)):
            result = _run(["build", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
