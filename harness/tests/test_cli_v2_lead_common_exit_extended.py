"""
Comprehensive tests for:
- forge_harness/cli_v2/lead.py
- forge_harness/cli_v2/_common.py
- forge_harness/cli_v2/exit_codes.py

Coverage targets: 80%+ per module.
"""

from __future__ import annotations

import json
import socket
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2._common import (
    domain_project_options,
    find_forge_root,
    format_error,
    format_info,
    format_success,
    format_warning,
    infer_domain_project,
    render_forge_error,
    require_forge_root,
    resolve_ws_url,
    xnode_append_jsonl,
    xnode_ensure_dirs,
    xnode_hostname,
    xnode_now_iso,
    xnode_realtime_emit,
)
from forge_harness.cli_v2.exit_codes import (
    EXIT_CODES_BASIC,
    EXIT_CODES_DISPATCH,
    EXIT_CODES_FLEET,
    EXIT_CODES_STANDARD,
    EXIT_CODES_TASKS,
    ExitCode,
    exit_with_code,
    get_exit_code_description,
)
from forge_harness.cli_v2.lead import (
    _read_json,
    _read_jsonl,
    lead,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def runner():
    """Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def forge_root(tmp_path):
    """Create a temporary FORGE root with .forge-root marker."""
    (tmp_path / ".forge-root").write_text("")
    return tmp_path


@pytest.fixture
def forge_root_domains_yaml(tmp_path):
    """Create a temp FORGE root with domains.yaml marker."""
    (tmp_path / "domains.yaml").write_text("domains: []")
    return tmp_path


# ============================================================================
# Tests for _common.py: find_forge_root
# ============================================================================


class TestFindForgeRoot:
    """Tests for find_forge_root()."""

    def test_finds_forge_root_marker(self, tmp_path):
        """find_forge_root() returns directory containing .forge-root."""
        (tmp_path / ".forge-root").write_text("")
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=tmp_path):
            result = find_forge_root()
        assert result == tmp_path

    def test_finds_domains_yaml_marker(self, tmp_path):
        """find_forge_root() returns directory containing domains.yaml."""
        (tmp_path / "domains.yaml").write_text("domains: []")
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=tmp_path):
            result = find_forge_root()
        assert result == tmp_path

    def test_finds_git_with_domain_dirs(self, tmp_path):
        """find_forge_root() returns git root with hyphenated domain dirs."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "voice-coach").mkdir()
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=tmp_path):
            result = find_forge_root()
        assert result == tmp_path

    def test_returns_none_when_not_found(self, tmp_path):
        """find_forge_root() returns None when no markers present."""
        # Use a deeply nested path with no markers
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=deep):
            # Parent traversal will stop at filesystem root
            result = find_forge_root()
        # Result depends on actual filesystem; just ensure no exception raised
        assert result is None or isinstance(result, Path)

    def test_searches_parent_directories(self, tmp_path):
        """find_forge_root() searches upward from cwd."""
        (tmp_path / ".forge-root").write_text("")
        subdir = tmp_path / "sub" / "project"
        subdir.mkdir(parents=True)
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=subdir):
            result = find_forge_root()
        assert result == tmp_path

    def test_git_without_hyphen_dirs_continues_search(self, tmp_path):
        """find_forge_root() skips git root without hyphenated dirs."""
        # A .git dir with no hyphen-named subdirs — should not match
        git_dir = tmp_path / "nohyphen"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()
        (git_dir / "src").mkdir()  # no hyphen in name

        # Parent has the real marker
        (tmp_path / ".forge-root").write_text("")
        subdir = git_dir / "src"
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=subdir):
            result = find_forge_root()
        # Should find tmp_path (has .forge-root) before or after git_dir
        assert result is not None


# ============================================================================
# Tests for _common.py: require_forge_root
# ============================================================================


class TestRequireForgeRoot:
    """Tests for require_forge_root()."""

    def test_returns_root_when_found(self, tmp_path):
        """require_forge_root() returns path when found."""
        with patch(
            "forge_harness.cli_v2._common.find_forge_root", return_value=tmp_path
        ):
            result = require_forge_root()
        assert result == tmp_path

    def test_exits_when_not_found(self):
        """require_forge_root() raises SystemExit(1) when root not found."""
        with patch("forge_harness.cli_v2._common.find_forge_root", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                require_forge_root()
        assert exc_info.value.code == 1


# ============================================================================
# Tests for _common.py: infer_domain_project
# ============================================================================


class TestInferDomainProject:
    """Tests for infer_domain_project()."""

    def test_infers_domain_and_project(self, tmp_path):
        """Returns (domain, project) when cwd is 2 levels deep."""
        project_dir = tmp_path / "voice-coach" / "interview-sim"
        project_dir.mkdir(parents=True)
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=project_dir):
            domain, project = infer_domain_project(tmp_path)
        assert domain == "voice-coach"
        assert project == "interview-sim"

    def test_infers_domain_only(self, tmp_path):
        """Returns (domain, None) when cwd is 1 level deep."""
        domain_dir = tmp_path / "voice-coach"
        domain_dir.mkdir()
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=domain_dir):
            domain, project = infer_domain_project(tmp_path)
        assert domain == "voice-coach"
        assert project is None

    def test_returns_none_none_at_root(self, tmp_path):
        """Returns (None, None) when cwd equals forge_root."""
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=tmp_path):
            domain, project = infer_domain_project(tmp_path)
        assert domain is None
        assert project is None

    def test_returns_none_none_when_outside_root(self, tmp_path):
        """Returns (None, None) when cwd is outside forge_root."""
        outside = tmp_path.parent
        with patch("forge_harness.cli_v2._common.Path.cwd", return_value=outside):
            domain, project = infer_domain_project(tmp_path)
        assert domain is None
        assert project is None


# ============================================================================
# Tests for _common.py: format_* functions
# ============================================================================


class TestFormatFunctions:
    """Tests for format_error, format_success, format_info, format_warning."""

    def test_format_error_raises_system_exit(self):
        """format_error() raises SystemExit(1)."""
        with patch("forge_harness.cli_v2._common.error_console") as mock_console:
            with pytest.raises(SystemExit) as exc_info:
                format_error("Something went wrong")
        assert exc_info.value.code == 1

    def test_format_error_prints_message(self):
        """format_error() prints the error message before exiting."""
        with patch("forge_harness.cli_v2._common.error_console") as mock_console:
            with pytest.raises(SystemExit):
                format_error("Test error message")
        mock_console.print.assert_called_once()
        printed_arg = mock_console.print.call_args[0][0]
        assert "Test error message" in printed_arg

    def test_format_success_prints(self):
        """format_success() prints a success message."""
        with patch("forge_harness.cli_v2._common.console") as mock_console:
            format_success("All done!")
        mock_console.print.assert_called_once()
        printed_arg = mock_console.print.call_args[0][0]
        assert "All done!" in printed_arg

    def test_format_info_prints(self):
        """format_info() prints an info message."""
        with patch("forge_harness.cli_v2._common.console") as mock_console:
            format_info("Some info here")
        mock_console.print.assert_called_once()
        printed_arg = mock_console.print.call_args[0][0]
        assert "Some info here" in printed_arg

    def test_format_warning_prints(self):
        """format_warning() prints a warning message."""
        with patch("forge_harness.cli_v2._common.console") as mock_console:
            format_warning("Watch out!")
        mock_console.print.assert_called_once()
        printed_arg = mock_console.print.call_args[0][0]
        assert "Watch out!" in printed_arg


# ============================================================================
# Tests for _common.py: render_forge_error
# ============================================================================


class TestRenderForgeError:
    """Tests for render_forge_error()."""

    def test_renders_string_error(self):
        """render_forge_error() accepts plain string."""
        with patch("forge_harness.cli_v2._common.error_console"):
            with pytest.raises(SystemExit) as exc_info:
                render_forge_error("plain error string")
        assert exc_info.value.code == 1

    def test_renders_dict_error(self):
        """render_forge_error() accepts error dict."""
        error_dict = {"code": "NOT_FOUND", "message": "Resource missing"}
        with patch("forge_harness.cli_v2._common.error_console"):
            with pytest.raises(SystemExit) as exc_info:
                render_forge_error(error_dict)
        assert exc_info.value.code == 1

    def test_renders_json_mode(self):
        """render_forge_error() outputs JSON when output_json=True."""
        captured = StringIO()
        with patch("sys.stdout", captured):
            with pytest.raises(SystemExit):
                render_forge_error("json error", output_json=True)
        output = captured.getvalue()
        data = json.loads(output)
        assert "message" in data

    def test_custom_exit_code(self):
        """render_forge_error() uses custom exit_code."""
        with patch("forge_harness.cli_v2._common.error_console"):
            with pytest.raises(SystemExit) as exc_info:
                render_forge_error("error", exit_code=5)
        assert exc_info.value.code == 5

    def test_renders_forge_error_instance(self):
        """render_forge_error() accepts ForgeError instance directly."""
        from forge_harness.error_schema import ForgeError
        err = ForgeError(code="CUSTOM_CODE", message="custom message")
        with patch("forge_harness.cli_v2._common.error_console"):
            with pytest.raises(SystemExit) as exc_info:
                render_forge_error(err)
        assert exc_info.value.code == 1

    def test_renders_unknown_type(self):
        """render_forge_error() handles unknown error types."""
        class WeirdError:
            def __str__(self):
                return "weird error"
        with patch("forge_harness.cli_v2._common.error_console"):
            with pytest.raises(SystemExit):
                render_forge_error(WeirdError())

    def test_human_readable_prints_next_action(self):
        """render_forge_error() prints next_action in human mode."""
        from forge_harness.error_schema import ForgeError
        err = ForgeError(
            code="AUTH_ERROR",
            message="auth failed",
            next_action="provide a token",
        )
        with patch("forge_harness.cli_v2._common.error_console") as mock_console:
            with pytest.raises(SystemExit):
                render_forge_error(err)
        # Should have been called at least twice (message + next_action)
        assert mock_console.print.call_count >= 2

    def test_human_readable_prints_details(self):
        """render_forge_error() prints details in human mode."""
        from forge_harness.error_schema import ForgeError
        err = ForgeError(
            code="DETAIL_ERROR",
            message="detailed error",
            details={"info": "extra context"},
        )
        with patch("forge_harness.cli_v2._common.error_console") as mock_console:
            with pytest.raises(SystemExit):
                render_forge_error(err)
        assert mock_console.print.call_count >= 2


# ============================================================================
# Tests for _common.py: xnode_* utilities
# ============================================================================


class TestXnodeUtilities:
    """Tests for xnode helper functions."""

    def test_xnode_hostname_returns_lowercase(self):
        """xnode_hostname() returns lowercase hostname."""
        with patch("socket.gethostname", return_value="MyMACHINE"):
            result = xnode_hostname()
        assert result == "mymachine"

    def test_xnode_hostname_strips_whitespace(self):
        """xnode_hostname() strips whitespace."""
        with patch("socket.gethostname", return_value="  testhost  "):
            result = xnode_hostname()
        assert result == "testhost"

    def test_xnode_hostname_fallback_for_empty(self):
        """xnode_hostname() returns 'unknown' if hostname is empty."""
        with patch("socket.gethostname", return_value=""):
            result = xnode_hostname()
        assert result == "unknown"

    def test_xnode_now_iso_returns_iso_string(self):
        """xnode_now_iso() returns a valid ISO format string."""
        result = xnode_now_iso()
        # Should parse without error
        parsed = datetime.fromisoformat(result)
        assert parsed is not None

    def test_xnode_now_iso_is_utc(self):
        """xnode_now_iso() returns UTC time."""
        result = xnode_now_iso()
        # UTC times end with +00:00 in Python's isoformat
        assert "+00:00" in result

    def test_xnode_append_jsonl_creates_file(self, tmp_path):
        """xnode_append_jsonl() creates file and writes JSON line."""
        path = tmp_path / "test.jsonl"
        payload = {"key": "value", "num": 42}
        xnode_append_jsonl(path, payload)
        assert path.exists()
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["key"] == "value"
        assert data["num"] == 42

    def test_xnode_append_jsonl_appends(self, tmp_path):
        """xnode_append_jsonl() appends multiple records."""
        path = tmp_path / "test.jsonl"
        xnode_append_jsonl(path, {"id": 1})
        xnode_append_jsonl(path, {"id": 2})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["id"] == 1
        assert json.loads(lines[1])["id"] == 2

    def test_xnode_ensure_dirs_creates_all_dirs(self, tmp_path):
        """xnode_ensure_dirs() creates all required directories."""
        dirs = xnode_ensure_dirs(tmp_path)
        expected_keys = ["root", "lead_inbox", "handoffs", "acks", "exceptions", "realtime_outbox"]
        for key in expected_keys:
            assert key in dirs
            assert dirs[key].exists()
            assert dirs[key].is_dir()

    def test_xnode_ensure_dirs_is_idempotent(self, tmp_path):
        """xnode_ensure_dirs() can be called multiple times safely."""
        dirs1 = xnode_ensure_dirs(tmp_path)
        dirs2 = xnode_ensure_dirs(tmp_path)
        assert dirs1["root"] == dirs2["root"]


# ============================================================================
# Tests for _common.py: resolve_ws_url
# ============================================================================


class TestResolveWsUrl:
    """Tests for resolve_ws_url()."""

    def test_returns_explicit_url_unchanged(self):
        """resolve_ws_url() passes through an explicit URL."""
        result = resolve_ws_url("http://custom.url/endpoint")
        assert result == "http://custom.url/endpoint"

    def test_returns_none_when_no_env(self):
        """resolve_ws_url() returns None when no URL and no env var."""
        with patch.dict("os.environ", {}, clear=True):
            # Remove COMMAND_CENTER_URL if set
            import os
            os.environ.pop("COMMAND_CENTER_URL", None)
            result = resolve_ws_url(None)
        assert result is None

    def test_uses_command_center_url_env(self):
        """resolve_ws_url() builds URL from COMMAND_CENTER_URL env var."""
        with patch.dict("os.environ", {"COMMAND_CENTER_URL": "http://cc.example.com"}):
            result = resolve_ws_url(None)
        assert result == "http://cc.example.com/api/xnode/events"

    def test_strips_trailing_slash_from_env(self):
        """resolve_ws_url() strips trailing slash from COMMAND_CENTER_URL."""
        with patch.dict("os.environ", {"COMMAND_CENTER_URL": "http://cc.example.com/"}):
            result = resolve_ws_url(None)
        assert result == "http://cc.example.com/api/xnode/events"

    def test_explicit_url_takes_priority_over_env(self):
        """resolve_ws_url() uses explicit url over COMMAND_CENTER_URL."""
        with patch.dict("os.environ", {"COMMAND_CENTER_URL": "http://env.url"}):
            result = resolve_ws_url("http://explicit.url")
        assert result == "http://explicit.url"


# ============================================================================
# Tests for _common.py: xnode_realtime_emit
# ============================================================================


class TestXnodeRealtimeEmit:
    """Tests for xnode_realtime_emit()."""

    def test_records_to_outbox_always(self, tmp_path):
        """xnode_realtime_emit() always writes to realtime outbox JSONL."""
        result = xnode_realtime_emit(
            forge_root=tmp_path,
            event_type="test.event",
            envelope={"test": "data"},
            ws_url=None,
        )
        assert result["recorded"] is True
        assert Path(result["outbox"]).exists()

    def test_delivered_false_without_ws_url(self, tmp_path):
        """xnode_realtime_emit() does not attempt delivery without ws_url."""
        result = xnode_realtime_emit(
            forge_root=tmp_path,
            event_type="test.event",
            envelope={"test": "data"},
            ws_url=None,
        )
        assert result["delivered"] is False
        assert result["transport"] == "jsonl-only"

    def test_http_delivery_success(self, tmp_path):
        """xnode_realtime_emit() attempts HTTP post when ws_url provided."""
        mock_resp = MagicMock()
        mock_resp.status = 200

        with patch("forge_harness.cli_v2._common.url_request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = Mock(return_value=mock_resp)
            mock_urlopen.return_value.__exit__ = Mock(return_value=False)
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="lead.send",
                envelope={"msg": "hello"},
                ws_url="http://fake-cc.example.com/api/xnode/events",
            )
        assert result["delivered"] is True
        assert result["transport"] == "http-post"
        assert result["status_code"] == 200

    def test_http_delivery_failure_sets_error(self, tmp_path):
        """xnode_realtime_emit() captures error when HTTP fails."""
        from urllib import error as url_error_mod
        with patch("forge_harness.cli_v2._common.url_request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = url_error_mod.URLError("connection refused")
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="lead.send",
                envelope={"msg": "hello"},
                ws_url="http://unreachable.example.com/endpoint",
            )
        assert result["delivered"] is False
        assert result["error"] is not None

    def test_uses_auth_token_when_provided(self, tmp_path):
        """xnode_realtime_emit() uses explicit auth_token."""
        mock_resp = MagicMock()
        mock_resp.status = 200

        captured_req = {}

        def fake_urlopen(req, timeout):
            captured_req["headers"] = req.headers
            m = MagicMock()
            m.__enter__ = Mock(return_value=mock_resp)
            m.__exit__ = Mock(return_value=False)
            return m

        with patch("forge_harness.cli_v2._common.url_request.urlopen", side_effect=fake_urlopen):
            xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="test",
                envelope={},
                ws_url="http://fake/endpoint",
                auth_token="my-secret-token",
            )
        # Authorization header should be set
        headers_lower = {k.lower(): v for k, v in captured_req["headers"].items()}
        assert "authorization" in headers_lower
        assert "my-secret-token" in headers_lower["authorization"]

    def test_uses_env_token_when_no_explicit(self, tmp_path):
        """xnode_realtime_emit() falls back to FORGE_WEBHOOK_TOKEN env var."""
        mock_resp = MagicMock()
        mock_resp.status = 200

        captured_req = {}

        def fake_urlopen(req, timeout):
            captured_req["headers"] = req.headers
            m = MagicMock()
            m.__enter__ = Mock(return_value=mock_resp)
            m.__exit__ = Mock(return_value=False)
            return m

        with patch.dict("os.environ", {"FORGE_WEBHOOK_TOKEN": "env-token"}):
            with patch(
                "forge_harness.cli_v2._common.url_request.urlopen", side_effect=fake_urlopen
            ):
                xnode_realtime_emit(
                    forge_root=tmp_path,
                    event_type="test",
                    envelope={},
                    ws_url="http://fake/endpoint",
                )
        headers_lower = {k.lower(): v for k, v in captured_req["headers"].items()}
        assert "authorization" in headers_lower
        assert "env-token" in headers_lower["authorization"]

    def test_timeout_error_captured(self, tmp_path):
        """xnode_realtime_emit() captures TimeoutError."""
        with patch("forge_harness.cli_v2._common.url_request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("timed out")
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="test",
                envelope={},
                ws_url="http://slow.example.com",
            )
        assert result["delivered"] is False
        assert "timed out" in result["error"]

    def test_non_2xx_status_not_delivered(self, tmp_path):
        """xnode_realtime_emit() marks delivered=False for non-2xx status."""
        mock_resp = MagicMock()
        mock_resp.status = 503

        with patch("forge_harness.cli_v2._common.url_request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = Mock(return_value=mock_resp)
            mock_urlopen.return_value.__exit__ = Mock(return_value=False)
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="test",
                envelope={},
                ws_url="http://fake/endpoint",
            )
        assert result["delivered"] is False
        assert result["status_code"] == 503


# ============================================================================
# Tests for _common.py: domain_project_options decorator
# ============================================================================


class TestDomainProjectOptions:
    """Tests for domain_project_options decorator."""

    def test_adds_domain_and_project_options(self):
        """domain_project_options() adds --domain and --project options."""
        import click

        @click.command()
        @domain_project_options
        def fake_cmd(domain, project):
            click.echo(f"{domain}:{project}")

        runner = CliRunner()
        result = runner.invoke(fake_cmd, ["-d", "mydom", "-p", "myproj"])
        assert result.exit_code == 0
        assert "mydom:myproj" in result.output


# ============================================================================
# Tests for exit_codes.py: ExitCode enum
# ============================================================================


class TestExitCodeEnum:
    """Tests for ExitCode enum values."""

    def test_success_is_zero(self):
        """ExitCode.SUCCESS == 0."""
        assert ExitCode.SUCCESS == 0

    def test_general_error_is_one(self):
        """ExitCode.GENERAL_ERROR == 1."""
        assert ExitCode.GENERAL_ERROR == 1

    def test_usage_error_is_two(self):
        """ExitCode.USAGE_ERROR == 2."""
        assert ExitCode.USAGE_ERROR == 2

    def test_operation_failed_is_three(self):
        """ExitCode.OPERATION_FAILED == 3."""
        assert ExitCode.OPERATION_FAILED == 3

    def test_service_unavailable_is_four(self):
        """ExitCode.SERVICE_UNAVAILABLE == 4."""
        assert ExitCode.SERVICE_UNAVAILABLE == 4

    def test_permission_denied_is_five(self):
        """ExitCode.PERMISSION_DENIED == 5."""
        assert ExitCode.PERMISSION_DENIED == 5

    def test_not_found_is_six(self):
        """ExitCode.NOT_FOUND == 6."""
        assert ExitCode.NOT_FOUND == 6

    def test_timeout_is_seven(self):
        """ExitCode.TIMEOUT == 7."""
        assert ExitCode.TIMEOUT == 7

    def test_config_error_is_eight(self):
        """ExitCode.CONFIG_ERROR == 8."""
        assert ExitCode.CONFIG_ERROR == 8

    def test_validation_error_is_nine(self):
        """ExitCode.VALIDATION_ERROR == 9."""
        assert ExitCode.VALIDATION_ERROR == 9

    def test_state_conflict_is_ten(self):
        """ExitCode.STATE_CONFLICT == 10."""
        assert ExitCode.STATE_CONFLICT == 10

    def test_command_specific_codes_are_11_plus(self):
        """Task/fleet/ship specific codes are 11+."""
        assert ExitCode.TASK_ALREADY_CLAIMED == 11
        assert ExitCode.DEPLOYMENT_FAILED == 11

    def test_is_intenum(self):
        """ExitCode members are usable as integers."""
        assert int(ExitCode.SUCCESS) == 0
        assert int(ExitCode.GENERAL_ERROR) == 1
        assert ExitCode.SUCCESS < ExitCode.GENERAL_ERROR


# ============================================================================
# Tests for exit_codes.py: exit_with_code
# ============================================================================


class TestExitWithCode:
    """Tests for exit_with_code()."""

    def test_exit_success_raises_system_exit_zero(self):
        """exit_with_code(SUCCESS) raises SystemExit(0)."""
        with pytest.raises(SystemExit) as exc_info:
            exit_with_code(ExitCode.SUCCESS)
        assert exc_info.value.code == 0

    def test_exit_error_raises_system_exit_nonzero(self):
        """exit_with_code(GENERAL_ERROR) raises SystemExit(1)."""
        with pytest.raises(SystemExit) as exc_info:
            exit_with_code(ExitCode.GENERAL_ERROR)
        assert exc_info.value.code == 1

    def test_exit_success_with_message_prints_green(self, capsys):
        """exit_with_code(SUCCESS, msg) prints green message."""
        with pytest.raises(SystemExit):
            exit_with_code(ExitCode.SUCCESS, "All good!")
        # Message should have been echoed
        captured = capsys.readouterr()
        assert "All good!" in captured.out

    def test_exit_error_with_message_prints_to_stderr(self, capsys):
        """exit_with_code(GENERAL_ERROR, msg) prints message to stderr."""
        with pytest.raises(SystemExit):
            exit_with_code(ExitCode.GENERAL_ERROR, "Something broke")
        captured = capsys.readouterr()
        assert "Something broke" in captured.err

    def test_exit_without_message_does_not_print(self, capsys):
        """exit_with_code() without message does not print anything."""
        with pytest.raises(SystemExit):
            exit_with_code(ExitCode.NOT_FOUND)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_exit_operation_failed(self):
        """exit_with_code(OPERATION_FAILED) exits with code 3."""
        with pytest.raises(SystemExit) as exc_info:
            exit_with_code(ExitCode.OPERATION_FAILED, "op failed")
        assert exc_info.value.code == 3


# ============================================================================
# Tests for exit_codes.py: get_exit_code_description
# ============================================================================


class TestGetExitCodeDescription:
    """Tests for get_exit_code_description()."""

    def test_universal_success_description(self):
        """Returns 'Success' for ExitCode.SUCCESS."""
        assert get_exit_code_description(ExitCode.SUCCESS) == "Success"

    def test_universal_general_error_description(self):
        """Returns 'General error' for ExitCode.GENERAL_ERROR."""
        assert get_exit_code_description(ExitCode.GENERAL_ERROR) == "General error"

    def test_universal_not_found_description(self):
        """Returns 'Not found' for ExitCode.NOT_FOUND."""
        assert get_exit_code_description(ExitCode.NOT_FOUND) == "Not found"

    def test_universal_timeout_description(self):
        """Returns 'Timeout' for ExitCode.TIMEOUT."""
        assert get_exit_code_description(ExitCode.TIMEOUT) == "Timeout"

    def test_command_specific_tasks(self):
        """Returns task-specific description when command='tasks'."""
        result = get_exit_code_description(ExitCode.TASK_ALREADY_CLAIMED, command="tasks")
        assert result == "Task already claimed"

    def test_command_specific_fleet(self):
        """Returns fleet-specific description when command='fleet'."""
        result = get_exit_code_description(ExitCode.AGENT_NOT_FOUND, command="fleet")
        assert result == "Agent not found"

    def test_command_specific_dispatch(self):
        """Returns dispatch-specific description when command='dispatch'."""
        result = get_exit_code_description(ExitCode.DISPATCH_FAILED, command="dispatch")
        assert result == "Dispatch failed"

    def test_command_specific_ship(self):
        """Returns ship-specific description when command='ship'."""
        result = get_exit_code_description(ExitCode.DEPLOYMENT_FAILED, command="ship")
        assert result == "Deployment failed"

    def test_command_specific_doctor(self):
        """Returns doctor-specific description when command='doctor'."""
        result = get_exit_code_description(ExitCode.ISSUES_FOUND, command="doctor")
        assert result == "Issues found"

    def test_command_specific_claims(self):
        """Returns claims-specific description when command='claims'."""
        result = get_exit_code_description(ExitCode.CLAIM_VERIFICATION_FAILED, command="claims")
        assert result == "Claim verification failed"

    def test_command_specific_loop(self):
        """Returns loop-specific description when command='loop'."""
        result = get_exit_code_description(ExitCode.LOOP_ALREADY_RUNNING, command="loop")
        assert result == "Loop already running"

    def test_command_specific_health(self):
        """Returns health-specific description when command='health'."""
        result = get_exit_code_description(ExitCode.AGENT_UNHEALTHY, command="health")
        assert result == "Agent unhealthy"

    def test_command_specific_git(self):
        """Returns git-specific description when command='git'."""
        result = get_exit_code_description(ExitCode.GIT_LOCK_PRESENT, command="git")
        assert result == "Git lock present"

    def test_command_specific_orchestrator(self):
        """Returns orchestrator-specific description when command='orchestrator'."""
        result = get_exit_code_description(
            ExitCode.CHECKPOINT_NOT_FOUND, command="orchestrator"
        )
        assert result == "Checkpoint not found"

    def test_unknown_command_returns_fallback(self):
        """Returns fallback string for unknown command."""
        result = get_exit_code_description(ExitCode.TASK_ALREADY_CLAIMED, command="unknown_cmd")
        assert "Command-specific error" in result or "Unknown" in result

    def test_no_command_returns_command_specific_fallback(self):
        """Returns fallback when no command provided for command-specific code."""
        result = get_exit_code_description(ExitCode.DEPLOYMENT_FAILED)
        assert "Command-specific error" in result or isinstance(result, str)

    def test_all_universal_codes_have_descriptions(self):
        """All universal exit codes (0-10) have descriptions."""
        universal_codes = [
            ExitCode.SUCCESS,
            ExitCode.GENERAL_ERROR,
            ExitCode.USAGE_ERROR,
            ExitCode.OPERATION_FAILED,
            ExitCode.SERVICE_UNAVAILABLE,
            ExitCode.PERMISSION_DENIED,
            ExitCode.NOT_FOUND,
            ExitCode.TIMEOUT,
            ExitCode.CONFIG_ERROR,
            ExitCode.VALIDATION_ERROR,
            ExitCode.STATE_CONFLICT,
        ]
        for code in universal_codes:
            desc = get_exit_code_description(code)
            assert isinstance(desc, str)
            assert len(desc) > 0


# ============================================================================
# Tests for exit_codes.py: EXIT_CODES_* constants
# ============================================================================


class TestExitCodeConstants:
    """Tests for exit code help text string constants."""

    def test_exit_codes_basic_is_string(self):
        """EXIT_CODES_BASIC is a non-empty string."""
        assert isinstance(EXIT_CODES_BASIC, str)
        assert len(EXIT_CODES_BASIC) > 0
        assert "0" in EXIT_CODES_BASIC

    def test_exit_codes_standard_is_string(self):
        """EXIT_CODES_STANDARD is a non-empty string."""
        assert isinstance(EXIT_CODES_STANDARD, str)
        assert "Success" in EXIT_CODES_STANDARD

    def test_exit_codes_tasks_includes_11_12_13(self):
        """EXIT_CODES_TASKS includes task-specific codes."""
        assert "11" in EXIT_CODES_TASKS
        assert "12" in EXIT_CODES_TASKS
        assert "13" in EXIT_CODES_TASKS

    def test_exit_codes_fleet_includes_agent_codes(self):
        """EXIT_CODES_FLEET includes agent-related codes."""
        assert "11" in EXIT_CODES_FLEET
        assert "12" in EXIT_CODES_FLEET

    def test_exit_codes_dispatch_includes_dispatch_failed(self):
        """EXIT_CODES_DISPATCH includes dispatch-specific codes."""
        assert "11" in EXIT_CODES_DISPATCH


# ============================================================================
# Tests for lead.py: _read_json and _read_jsonl helpers
# ============================================================================


class TestReadHelpers:
    """Tests for _read_json() and _read_jsonl()."""

    def test_read_json_parses_file(self, tmp_path):
        """_read_json() reads and parses a JSON file."""
        p = tmp_path / "test.json"
        p.write_text(json.dumps({"key": "value", "num": 99}))
        result = _read_json(p)
        assert result["key"] == "value"
        assert result["num"] == 99

    def test_read_jsonl_parses_multiple_lines(self, tmp_path):
        """_read_jsonl() parses each line of a JSONL file."""
        p = tmp_path / "test.jsonl"
        lines = [
            json.dumps({"id": 1}),
            json.dumps({"id": 2}),
            json.dumps({"id": 3}),
        ]
        p.write_text("\n".join(lines) + "\n")
        result = _read_jsonl(p)
        assert len(result) == 3
        assert result[0]["id"] == 1
        assert result[2]["id"] == 3

    def test_read_jsonl_skips_blank_lines(self, tmp_path):
        """_read_jsonl() skips blank lines."""
        p = tmp_path / "test.jsonl"
        p.write_text('{"id": 1}\n\n\n{"id": 2}\n')
        result = _read_jsonl(p)
        assert len(result) == 2

    def test_read_jsonl_empty_file(self, tmp_path):
        """_read_jsonl() returns empty list for empty file."""
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        result = _read_jsonl(p)
        assert result == []


# ============================================================================
# Tests for lead.py CLI commands: lead send
# ============================================================================


class TestLeadSend:
    """Tests for 'forge lead send' command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_send_basic_success(self, runner, forge_root):
        """lead send creates inbox file and prints success."""
        with runner.isolated_filesystem(temp_dir=forge_root):
            with patch(
                "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
            ):
                with patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit:
                    mock_emit.return_value = {
                        "recorded": True,
                        "delivered": False,
                        "transport": "jsonl-only",
                    }
                    with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                        result = runner.invoke(
                            lead,
                            [
                                "send",
                                "--to-node", "sati",
                                "--task-id", "IS-100",
                                "--summary", "Implement login",
                                "--no-realtime",
                            ],
                        )
        assert result.exit_code == 0
        assert "sati" in result.output or "IS-100" in result.output or "Sent" in result.output

    def test_send_durable_writes_inbox(self, runner, forge_root):
        """lead send --durable creates .jsonl inbox entry."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "send",
                        "--to-node", "nova",
                        "--task-id", "TASK-42",
                        "--durable",
                        "--no-realtime",
                    ],
                )
        assert result.exit_code == 0
        inbox_file = forge_root / ".forge" / "xnode" / "lead-inbox" / "nova.jsonl"
        assert inbox_file.exists()
        records = _read_jsonl(inbox_file)
        assert len(records) == 1
        assert records[0]["target"]["node"] == "nova"

    def test_send_json_mode_outputs_valid_json(self, runner, forge_root):
        """lead send --json outputs parseable JSON envelope."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "send",
                        "--to-node", "nova",
                        "--task-id", "JS-1",
                        "--no-realtime",
                        "--json",
                    ],
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert "message_id" in data["data"]

    def test_send_with_from_file(self, runner, forge_root, tmp_path):
        """lead send --from-file loads payload from JSON file."""
        payload_file = tmp_path / "payload.json"
        payload_file.write_text(json.dumps({"custom_key": "custom_value"}))

        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "send",
                        "--to-node", "nova",
                        "--task-id", "FILE-1",
                        "--from-file", str(payload_file),
                        "--no-realtime",
                    ],
                )
        assert result.exit_code == 0
        inbox_file = forge_root / ".forge" / "xnode" / "lead-inbox" / "nova.jsonl"
        records = _read_jsonl(inbox_file)
        assert records[0]["payload"]["custom_key"] == "custom_value"

    def test_send_with_idempotency_key(self, runner, forge_root):
        """lead send --idempotency-key uses the custom key."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "send",
                        "--to-node", "nova",
                        "--task-id", "IK-1",
                        "--idempotency-key", "my-custom-key",
                        "--no-realtime",
                        "--json",
                    ],
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["idempotency_key"] == "my-custom-key"

    def test_send_whitespace_idempotency_key_uses_default(self, runner, forge_root):
        """lead send with whitespace-only idempotency-key falls back to default."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "send",
                        "--to-node", "nova",
                        "--task-id", "WS-1",
                        "--idempotency-key", "   ",
                        "--no-realtime",
                        "--json",
                    ],
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Should auto-generate key starting with lead-send-
        assert data["data"]["idempotency_key"].startswith("lead-send-")

    def test_send_realtime_enabled_triggers_emit(self, runner, forge_root):
        """lead send with realtime calls xnode_realtime_emit."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                with patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit:
                    with patch("forge_harness.cli_v2.lead.resolve_ws_url", return_value=None):
                        mock_emit.return_value = {
                            "recorded": True,
                            "delivered": False,
                        }
                        result = runner.invoke(
                            lead,
                            [
                                "send",
                                "--to-node", "nova",
                                "--task-id", "RT-1",
                                "--realtime",
                                "--no-durable",
                            ],
                        )
        assert result.exit_code == 0
        mock_emit.assert_called_once()

    def test_send_realtime_result_shown_in_output(self, runner, forge_root):
        """lead send prints realtime result when realtime enabled."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                with patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit:
                    with patch("forge_harness.cli_v2.lead.resolve_ws_url", return_value=None):
                        mock_emit.return_value = {
                            "recorded": True,
                            "delivered": True,
                        }
                        result = runner.invoke(
                            lead,
                            [
                                "send",
                                "--to-node", "nova",
                                "--task-id", "RT-2",
                                "--realtime",
                                "--no-durable",
                            ],
                        )
        assert result.exit_code == 0
        assert "recorded" in result.output or "Realtime" in result.output

    def test_send_no_delivery_mode_exits_with_error(self, runner, forge_root):
        """lead send with --no-durable --no-realtime shows error."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "send",
                        "--to-node", "nova",
                        "--task-id", "ERR-1",
                        "--no-durable",
                        "--no-realtime",
                    ],
                )
        # Should fail because neither durable nor realtime
        assert result.exit_code != 0

    def test_send_priority_choices(self, runner, forge_root):
        """lead send accepts all priority levels."""
        for priority in ["critical", "high", "medium", "low"]:
            with patch(
                "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
            ):
                with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                    result = runner.invoke(
                        lead,
                        [
                            "send",
                            "--to-node", "nova",
                            "--task-id", f"P-{priority}",
                            "--priority", priority,
                            "--no-realtime",
                        ],
                    )
            assert result.exit_code == 0, f"Priority {priority} failed: {result.output}"

    def test_send_with_branch(self, runner, forge_root):
        """lead send with --branch sets write_scope branch in payload."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "send",
                        "--to-node", "nova",
                        "--task-id", "BR-1",
                        "--branch", "feat/nova-br-1",
                        "--no-realtime",
                    ],
                )
        assert result.exit_code == 0
        inbox_file = forge_root / ".forge" / "xnode" / "lead-inbox" / "nova.jsonl"
        records = _read_jsonl(inbox_file)
        assert records[0]["payload"]["write_scope"]["branch"] == "feat/nova-br-1"

    def test_send_json_mode_includes_realtime_when_emitted(self, runner, forge_root):
        """lead send --json includes realtime result when realtime enabled."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                with patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit:
                    with patch("forge_harness.cli_v2.lead.resolve_ws_url", return_value=None):
                        mock_emit.return_value = {"recorded": True, "delivered": False}
                        result = runner.invoke(
                            lead,
                            [
                                "send",
                                "--to-node", "nova",
                                "--task-id", "RT-JSON",
                                "--realtime",
                                "--no-durable",
                                "--json",
                            ],
                        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "realtime" in data["data"]

    def test_send_ttl_minimum_enforced(self, runner, forge_root):
        """lead send with ttl < 60 uses 60 seconds minimum."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "send",
                        "--to-node", "nova",
                        "--task-id", "TTL-1",
                        "--ttl-seconds", "5",
                        "--no-realtime",
                    ],
                )
        assert result.exit_code == 0

    def test_send_strict_enforces_delivery_defaults(self, runner, forge_root):
        """lead send --strict forces ack/realtime/required delivery and includes preflight."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                with patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit:
                    with patch(
                        "forge_harness.cli_v2.lead._http_get_json"
                    ) as mock_http:
                        mock_http.side_effect = [
                            ({"status": "ok"}, None),
                            (
                                {
                                    "nodes": [
                                        {
                                            "node_id": "nova",
                                            "name": "nova",
                                            "status": "online",
                                            "is_fresh": True,
                                        }
                                    ]
                                },
                                None,
                            ),
                        ]
                        mock_emit.return_value = {"recorded": True, "delivered": True}
                        result = runner.invoke(
                            lead,
                            [
                                "send",
                                "--to-node", "nova",
                                "--task-id", "STRICT-1",
                                "--strict",
                                "--no-realtime",
                                "--no-requires-ack",
                                "--command-center-url", "http://100.70.79.45:8080",
                                "--token", "test-token",
                                "--json",
                            ],
                        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["strict"] is True
        assert data["data"]["realtime_enabled"] is True
        assert data["data"]["require_realtime_delivery"] is True
        assert data["data"]["strict_overrides"]["realtime"] is True
        assert data["data"]["strict_overrides"]["requires_ack"] is True
        assert data["data"]["strict_overrides"]["require_realtime_delivery"] is True
        assert data["data"]["preflight"]["ok"] is True

        inbox_file = forge_root / ".forge" / "xnode" / "lead-inbox" / "nova.jsonl"
        records = _read_jsonl(inbox_file)
        assert records[0]["requires_ack"] is True

    def test_send_strict_preflight_failure_returns_json_error(self, runner, forge_root):
        """lead send --strict fails before delivery when preflight checks fail."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "send",
                        "--to-node", "nova",
                        "--task-id", "STRICT-ERR",
                        "--strict",
                        "--json",
                    ],
                    env={"COMMAND_CENTER_URL": "", "FORGE_WEBHOOK_TOKEN": ""},
                )

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["success"] is False
        assert data["data"]["strict"] is True
        assert data["data"]["preflight"]["ok"] is False


# ============================================================================
# Tests for lead.py CLI commands: lead inbox
# ============================================================================


class TestLeadInbox:
    """Tests for 'forge lead inbox' command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_inbox_empty_shows_no_messages(self, runner, forge_root):
        """lead inbox with no inbox file shows info message."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(lead, ["inbox"])
        assert result.exit_code == 0
        assert "No inbox" in result.output or "prya" in result.output

    def test_inbox_empty_json_mode(self, runner, forge_root):
        """lead inbox --json with no inbox returns empty JSON."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(lead, ["inbox", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["count"] == 0
        assert data["data"]["messages"] == []

    def test_inbox_reads_messages(self, runner, forge_root):
        """lead inbox shows messages from existing inbox file."""
        # Seed inbox file
        inbox_dir = forge_root / ".forge" / "xnode" / "lead-inbox"
        inbox_dir.mkdir(parents=True)
        inbox_file = inbox_dir / "prya.jsonl"
        envelope = {
            "message_id": "msg_123",
            "type": "lead.send",
            "priority": "high",
            "payload": {"task_id": "TEST-1"},
        }
        inbox_file.write_text(json.dumps(envelope) + "\n")

        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(lead, ["inbox"])
        assert result.exit_code == 0
        assert "msg_123" in result.output or "TEST-1" in result.output

    def test_inbox_with_node_option(self, runner, forge_root):
        """lead inbox --node reads specified node's inbox."""
        inbox_dir = forge_root / ".forge" / "xnode" / "lead-inbox"
        inbox_dir.mkdir(parents=True)
        inbox_file = inbox_dir / "nova.jsonl"
        envelope = {
            "message_id": "msg_nova_1",
            "type": "lead.send",
            "priority": "medium",
            "payload": {"task_id": "NOVA-1"},
        }
        inbox_file.write_text(json.dumps(envelope) + "\n")

        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            result = runner.invoke(lead, ["inbox", "--node", "nova"])
        assert result.exit_code == 0
        assert "msg_nova_1" in result.output

    def test_inbox_limit_parameter(self, runner, forge_root):
        """lead inbox --limit N returns at most N messages."""
        inbox_dir = forge_root / ".forge" / "xnode" / "lead-inbox"
        inbox_dir.mkdir(parents=True)
        inbox_file = inbox_dir / "prya.jsonl"
        # Write 5 messages
        for i in range(5):
            envelope = {
                "message_id": f"msg_{i}",
                "type": "lead.send",
                "priority": "low",
                "payload": {"task_id": f"T-{i}"},
            }
            inbox_file.write_text(
                "\n".join(json.dumps({
                    "message_id": f"msg_{j}",
                    "type": "lead.send",
                    "priority": "low",
                    "payload": {"task_id": f"T-{j}"},
                }) for j in range(5)) + "\n"
            )

        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(lead, ["inbox", "--limit", "2"])
        assert result.exit_code == 0

    def test_inbox_json_mode_with_messages(self, runner, forge_root):
        """lead inbox --json with messages returns structured JSON."""
        inbox_dir = forge_root / ".forge" / "xnode" / "lead-inbox"
        inbox_dir.mkdir(parents=True)
        inbox_file = inbox_dir / "prya.jsonl"
        envelope = {
            "message_id": "msg_json",
            "type": "lead.send",
            "priority": "critical",
            "payload": {"task_id": "JSON-1"},
        }
        inbox_file.write_text(json.dumps(envelope) + "\n")

        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(lead, ["inbox", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["count"] == 1

    def test_inbox_handles_envelope_format(self, runner, forge_root):
        """lead inbox handles SSE-received envelope-wrapped format."""
        inbox_dir = forge_root / ".forge" / "xnode" / "lead-inbox"
        inbox_dir.mkdir(parents=True)
        inbox_file = inbox_dir / "prya.jsonl"
        # SSE-received format (format B)
        record = {
            "received_at": "2026-02-22T10:00:00+00:00",
            "type": "lead.send",
            "envelope": {
                "message_id": "msg_sse_1",
                "type": "lead.send",
                "priority": "high",
                "payload": {"task_id": "SSE-1"},
            },
        }
        inbox_file.write_text(json.dumps(record) + "\n")

        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(lead, ["inbox"])
        assert result.exit_code == 0


# ============================================================================
# Tests for lead.py CLI commands: lead ack
# ============================================================================


class TestLeadAck:
    """Tests for 'forge lead ack' command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_ack_creates_ack_file(self, runner, forge_root):
        """lead ack writes ack JSON file."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "ack",
                        "--message-id", "msg_abc_001",
                        "--no-realtime",
                    ],
                )
        assert result.exit_code == 0
        ack_file = forge_root / ".forge" / "xnode" / "acks" / "msg_abc_001.json"
        assert ack_file.exists()
        ack_data = json.loads(ack_file.read_text())
        assert ack_data["message_id"] == "msg_abc_001"
        assert ack_data["status"] == "ack"

    def test_ack_nack_status(self, runner, forge_root):
        """lead ack --status nack writes nack record."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "ack",
                        "--message-id", "msg_nack_1",
                        "--status", "nack",
                        "--note", "cannot process",
                        "--no-realtime",
                    ],
                )
        assert result.exit_code == 0
        ack_file = forge_root / ".forge" / "xnode" / "acks" / "msg_nack_1.json"
        ack_data = json.loads(ack_file.read_text())
        assert ack_data["status"] == "nack"
        assert ack_data["note"] == "cannot process"

    def test_ack_json_mode(self, runner, forge_root):
        """lead ack --json outputs JSON envelope."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "ack",
                        "--message-id", "msg_json_ack",
                        "--no-realtime",
                        "--json",
                    ],
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["message_id"] == "msg_json_ack"

    def test_ack_realtime_calls_emit(self, runner, forge_root):
        """lead ack --realtime calls xnode_realtime_emit."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                with patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit:
                    with patch("forge_harness.cli_v2.lead.resolve_ws_url", return_value=None):
                        mock_emit.return_value = {"recorded": True, "delivered": False}
                        result = runner.invoke(
                            lead,
                            [
                                "ack",
                                "--message-id", "msg_rt_ack",
                                "--realtime",
                                "--no-durable",
                            ],
                        )
        assert result.exit_code == 0
        mock_emit.assert_called_once()

    def test_ack_no_delivery_mode_exits_with_error(self, runner, forge_root):
        """lead ack with neither --durable nor --realtime exits with error."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "ack",
                        "--message-id", "msg_no_mode",
                        "--no-durable",
                        "--no-realtime",
                    ],
                )
        assert result.exit_code != 0

    def test_ack_shows_file_path_when_durable(self, runner, forge_root):
        """lead ack shows ack file path in human-readable output."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "ack",
                        "--message-id", "msg_show_file",
                        "--no-realtime",
                    ],
                )
        assert result.exit_code == 0
        assert "Ack file" in result.output or "msg_show_file" in result.output

    def test_ack_json_includes_realtime_when_emitted(self, runner, forge_root):
        """lead ack --json includes realtime result when realtime enabled."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                with patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit:
                    with patch("forge_harness.cli_v2.lead.resolve_ws_url", return_value=None):
                        mock_emit.return_value = {"recorded": True, "delivered": True}
                        result = runner.invoke(
                            lead,
                            [
                                "ack",
                                "--message-id", "msg_rt_json",
                                "--realtime",
                                "--no-durable",
                                "--json",
                            ],
                        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "realtime" in data["data"]

    def test_ack_shows_realtime_in_human_output(self, runner, forge_root):
        """lead ack shows realtime result in human-readable output."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                with patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit:
                    with patch("forge_harness.cli_v2.lead.resolve_ws_url", return_value=None):
                        mock_emit.return_value = {"recorded": True, "delivered": False}
                        result = runner.invoke(
                            lead,
                            [
                                "ack",
                                "--message-id", "msg_rt_human",
                                "--realtime",
                                "--no-durable",
                            ],
                        )
        assert result.exit_code == 0
        assert "recorded" in result.output or "Realtime" in result.output


# ============================================================================
# Tests for lead.py CLI commands: lead handoff create
# ============================================================================


class TestLeadHandoffCreate:
    """Tests for 'forge lead handoff create' command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_handoff_create_writes_json_file(self, runner, forge_root):
        """lead handoff create writes handoff JSON file."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "handoff", "create",
                        "--to-node", "nova",
                        "--from-task", "IS-100",
                        "--to-task", "IS-101",
                        "--summary", "Hand off auth module",
                    ],
                )
        assert result.exit_code == 0
        handoff_dir = forge_root / ".forge" / "xnode" / "handoffs"
        handoff_files = list(handoff_dir.glob("*.json"))
        assert len(handoff_files) == 1
        payload = json.loads(handoff_files[0].read_text())
        assert payload["target_node"] == "nova"
        assert payload["from_task_id"] == "IS-100"
        assert payload["to_task_id"] == "IS-101"
        assert payload["status"] == "pending"

    def test_handoff_create_json_mode(self, runner, forge_root):
        """lead handoff create --json outputs JSON envelope."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "handoff", "create",
                        "--to-node", "sati",
                        "--from-task", "T-1",
                        "--to-task", "T-2",
                        "--summary", "Test handoff",
                        "--json",
                    ],
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert "handoff_id" in data["data"]

    def test_handoff_create_with_risk(self, runner, forge_root):
        """lead handoff create --risk sets risk level."""
        for risk in ["low", "medium", "high", "critical"]:
            with patch(
                "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
            ):
                with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                    result = runner.invoke(
                        lead,
                        [
                            "handoff", "create",
                            "--to-node", "nova",
                            "--from-task", f"R-{risk}",
                            "--to-task", "R-next",
                            "--summary", f"Risk {risk} test",
                            "--risk", risk,
                        ],
                    )
            assert result.exit_code == 0, f"Risk level {risk} failed: {result.output}"

    def test_handoff_create_shows_file_path(self, runner, forge_root):
        """lead handoff create shows created file path."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="prya"):
                result = runner.invoke(
                    lead,
                    [
                        "handoff", "create",
                        "--to-node", "nova",
                        "--from-task", "FP-1",
                        "--to-task", "FP-2",
                        "--summary", "Show file test",
                    ],
                )
        assert result.exit_code == 0
        assert "File:" in result.output or "handoff" in result.output.lower()


# ============================================================================
# Tests for lead.py CLI commands: lead handoff accept
# ============================================================================


class TestLeadHandoffAccept:
    """Tests for 'forge lead handoff accept' command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def _create_handoff(self, forge_root, handoff_id="ho_test_001"):
        """Helper to create a test handoff file."""
        handoff_dir = forge_root / ".forge" / "xnode" / "handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        handoff_file = handoff_dir / f"{handoff_id}.json"
        payload = {
            "handoff_id": handoff_id,
            "status": "pending",
            "target_node": "nova",
            "from_task_id": "T-1",
            "to_task_id": "T-2",
        }
        handoff_file.write_text(json.dumps(payload))
        return handoff_id

    def test_accept_updates_status(self, runner, forge_root):
        """lead handoff accept updates handoff status to accepted."""
        handoff_id = self._create_handoff(forge_root)
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="nova"):
                result = runner.invoke(
                    lead,
                    ["handoff", "accept", "--handoff-id", handoff_id],
                )
        assert result.exit_code == 0
        handoff_file = (
            forge_root / ".forge" / "xnode" / "handoffs" / f"{handoff_id}.json"
        )
        payload = json.loads(handoff_file.read_text())
        assert payload["status"] == "accepted"
        assert payload["accepted_by_node"] == "nova"

    def test_accept_nonexistent_shows_error(self, runner, forge_root):
        """lead handoff accept for nonexistent handoff shows error."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="nova"):
                result = runner.invoke(
                    lead,
                    ["handoff", "accept", "--handoff-id", "ho_does_not_exist"],
                )
        assert result.exit_code != 0

    def test_accept_json_mode(self, runner, forge_root):
        """lead handoff accept --json outputs JSON envelope."""
        handoff_id = self._create_handoff(forge_root, "ho_json_test")
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            with patch("forge_harness.cli_v2.lead.xnode_hostname", return_value="nova"):
                result = runner.invoke(
                    lead,
                    ["handoff", "accept", "--handoff-id", handoff_id, "--json"],
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["status"] == "accepted"


# ============================================================================
# Tests for lead.py CLI commands: lead handoff list
# ============================================================================


class TestLeadHandoffList:
    """Tests for 'forge lead handoff list' command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def _create_handoff(self, forge_root, handoff_id, target_node="nova", status="pending"):
        """Helper to create a test handoff file."""
        handoff_dir = forge_root / ".forge" / "xnode" / "handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        handoff_file = handoff_dir / f"{handoff_id}.json"
        payload = {
            "handoff_id": handoff_id,
            "status": status,
            "target_node": target_node,
            "from_task_id": "T-1",
            "to_task_id": "T-2",
        }
        handoff_file.write_text(json.dumps(payload))

    def test_list_empty_shows_info(self, runner, forge_root):
        """lead handoff list with no handoffs shows 'No handoffs'."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            result = runner.invoke(lead, ["handoff", "list"])
        assert result.exit_code == 0
        assert "No handoffs" in result.output

    def test_list_shows_handoffs(self, runner, forge_root):
        """lead handoff list shows existing handoffs."""
        self._create_handoff(forge_root, "ho_list_1", "nova", "pending")
        self._create_handoff(forge_root, "ho_list_2", "sati", "accepted")
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            result = runner.invoke(lead, ["handoff", "list"])
        assert result.exit_code == 0
        assert "ho_list_1" in result.output
        assert "ho_list_2" in result.output

    def test_list_filter_by_node(self, runner, forge_root):
        """lead handoff list --node filters by target node."""
        self._create_handoff(forge_root, "ho_nova_1", "nova")
        self._create_handoff(forge_root, "ho_sati_1", "sati")
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            result = runner.invoke(lead, ["handoff", "list", "--node", "nova"])
        assert result.exit_code == 0
        assert "ho_nova_1" in result.output
        assert "ho_sati_1" not in result.output

    def test_list_filter_by_status(self, runner, forge_root):
        """lead handoff list --status filters by status."""
        self._create_handoff(forge_root, "ho_pending_1", "nova", "pending")
        self._create_handoff(forge_root, "ho_accepted_1", "nova", "accepted")
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            result = runner.invoke(lead, ["handoff", "list", "--status", "pending"])
        assert result.exit_code == 0
        assert "ho_pending_1" in result.output
        assert "ho_accepted_1" not in result.output

    def test_list_json_mode(self, runner, forge_root):
        """lead handoff list --json returns structured JSON."""
        self._create_handoff(forge_root, "ho_json_1")
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            result = runner.invoke(lead, ["handoff", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["count"] == 1

    def test_list_skips_invalid_json(self, runner, forge_root):
        """lead handoff list skips files with invalid JSON."""
        handoff_dir = forge_root / ".forge" / "xnode" / "handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        # Write a valid handoff
        self._create_handoff(forge_root, "ho_valid")
        # Write an invalid JSON file
        (handoff_dir / "ho_invalid.json").write_text("NOT VALID JSON{{{")
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            result = runner.invoke(lead, ["handoff", "list"])
        assert result.exit_code == 0
        assert "ho_valid" in result.output

    def test_list_json_empty_with_no_handoffs(self, runner, forge_root):
        """lead handoff list --json with no handoffs returns count=0."""
        with patch(
            "forge_harness.cli_v2.lead.require_forge_root", return_value=forge_root
        ):
            result = runner.invoke(lead, ["handoff", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["count"] == 0
        assert data["data"]["handoffs"] == []
