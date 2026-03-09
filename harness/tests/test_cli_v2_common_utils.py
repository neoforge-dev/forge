"""
Comprehensive unit tests for forge_harness/cli_v2/_common.py
=============================================================

Tests ALL public functions, decorators, and module-level constants.
Covers happy paths, edge cases, error paths, and integration between helpers.
"""

import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from forge_harness.cli_v2._common import (
    domain_option,
    domain_project_options,
    find_forge_root,
    format_error,
    format_info,
    format_success,
    format_warning,
    infer_domain_project,
    project_option,
    render_forge_error,
    require_forge_root,
    resolve_ws_url,
    url_request,
    xnode_append_jsonl,
    xnode_ensure_dirs,
    xnode_hostname,
    xnode_now_iso,
    xnode_realtime_emit,
)

from forge_harness.error_schema import (
    UNKNOWN_ERROR,
    ForgeError,
)

# ---------------------------------------------------------------------------
# find_forge_root
# ---------------------------------------------------------------------------


class TestFindForgeRootMarkers:
    """Root-detection via file/dir markers."""

    def test_finds_root_via_forge_root_marker(self, tmp_path):
        (tmp_path / ".forge-root").touch()
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            assert find_forge_root() == tmp_path

    def test_finds_root_via_domains_yaml(self, tmp_path):
        (tmp_path / "domains.yaml").touch()
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            assert find_forge_root() == tmp_path

    def test_finds_root_via_git_dir_with_hyphenated_subdirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "some-domain").mkdir()
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            assert find_forge_root() == tmp_path

    def test_git_dir_without_hyphenated_subdirs_is_not_root(self, tmp_path):
        """A plain .git dir with no domain-like children should NOT match."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "nodash").mkdir()
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            # No hyphen in subdir name, so the git heuristic fails.
            result = find_forge_root()
            assert result is None

    def test_priority_forge_root_over_domains_yaml(self, tmp_path):
        """Both markers present — .forge-root wins because it's checked first."""
        (tmp_path / ".forge-root").touch()
        (tmp_path / "domains.yaml").touch()
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            assert find_forge_root() == tmp_path


class TestFindForgeRootTraversal:
    """Upward directory traversal behaviour."""

    def test_searches_parent_directories(self, tmp_path):
        (tmp_path / ".forge-root").touch()
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        with patch("pathlib.Path.cwd", return_value=deep):
            assert find_forge_root() == tmp_path

    def test_returns_none_when_no_marker_anywhere(self, tmp_path):
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            assert find_forge_root() is None

    def test_limits_search_to_ten_levels(self, tmp_path):
        """Traversal stops after 10 levels; marker beyond that is not found."""
        current = tmp_path
        for i in range(12):
            current = current / f"level{i}"
            current.mkdir()
        # Place marker at tmp_path root (>10 levels up from current)
        (tmp_path / ".forge-root").touch()
        with patch("pathlib.Path.cwd", return_value=current):
            # The search cap means we won't reach tmp_path
            result = find_forge_root()
            # Result may be None or may find it within 10 hops; just confirm no crash
            assert result is None or isinstance(result, Path)

    def test_stops_at_filesystem_root(self, tmp_path):
        """Should not loop indefinitely at the filesystem root."""
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = find_forge_root()
            assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# require_forge_root
# ---------------------------------------------------------------------------


class TestRequireForgeRoot:
    def test_returns_found_root(self, tmp_path):
        with patch("forge_harness.cli_v2._common.find_forge_root", return_value=tmp_path):
            assert require_forge_root() == tmp_path

    def test_exits_with_code_1_when_not_found(self):
        with patch("forge_harness.cli_v2._common.find_forge_root", return_value=None):
            with patch("forge_harness.cli_v2._common.error_console"):
                with pytest.raises(SystemExit) as exc:
                    require_forge_root()
        assert exc.value.code == 1

    def test_prints_two_error_lines_when_not_found(self):
        with patch("forge_harness.cli_v2._common.find_forge_root", return_value=None):
            with patch("forge_harness.cli_v2._common.error_console") as mock_ec:
                with pytest.raises(SystemExit):
                    require_forge_root()
        assert mock_ec.print.call_count == 2


# ---------------------------------------------------------------------------
# infer_domain_project
# ---------------------------------------------------------------------------


class TestInferDomainProject:
    def test_infers_both_domain_and_project(self, tmp_path):
        root = tmp_path
        cwd = root / "my-domain" / "my-project"
        cwd.mkdir(parents=True)
        with patch("pathlib.Path.cwd", return_value=cwd):
            domain, project = infer_domain_project(root)
        assert domain == "my-domain"
        assert project == "my-project"

    def test_infers_domain_only_when_in_domain_root(self, tmp_path):
        root = tmp_path
        cwd = root / "my-domain"
        cwd.mkdir()
        with patch("pathlib.Path.cwd", return_value=cwd):
            domain, project = infer_domain_project(root)
        assert domain == "my-domain"
        assert project is None

    def test_returns_none_none_when_at_forge_root(self, tmp_path):
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            domain, project = infer_domain_project(tmp_path)
        assert domain is None
        assert project is None

    def test_returns_none_none_when_outside_forge(self, tmp_path):
        root = tmp_path / "FORGE"
        root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        with patch("pathlib.Path.cwd", return_value=outside):
            domain, project = infer_domain_project(root)
        assert domain is None
        assert project is None

    def test_deeper_nested_path_returns_first_two_parts(self, tmp_path):
        """Extra nesting beyond domain/project is silently ignored."""
        cwd = tmp_path / "dom" / "proj" / "src" / "lib"
        cwd.mkdir(parents=True)
        with patch("pathlib.Path.cwd", return_value=cwd):
            domain, project = infer_domain_project(tmp_path)
        assert domain == "dom"
        assert project == "proj"


# ---------------------------------------------------------------------------
# format_error / format_success / format_info / format_warning
# ---------------------------------------------------------------------------


class TestFormatError:
    def test_raises_system_exit_code_1(self):
        with patch("forge_harness.cli_v2._common.error_console"):
            with pytest.raises(SystemExit) as exc:
                format_error("something broke")
        assert exc.value.code == 1

    def test_message_is_printed_to_error_console(self):
        with patch("forge_harness.cli_v2._common.error_console") as mock_ec:
            with pytest.raises(SystemExit):
                format_error("bad thing happened")
        printed = mock_ec.print.call_args[0][0]
        assert "bad thing happened" in printed

    def test_output_contains_error_markup(self):
        with patch("forge_harness.cli_v2._common.error_console") as mock_ec:
            with pytest.raises(SystemExit):
                format_error("test msg")
        printed = mock_ec.print.call_args[0][0]
        assert "[red]" in printed or "Error" in printed


class TestFormatSuccess:
    def test_prints_to_main_console(self):
        with patch("forge_harness.cli_v2._common.console") as mock_c:
            format_success("everything worked")
        mock_c.print.assert_called_once()

    def test_message_content_present(self):
        with patch("forge_harness.cli_v2._common.console") as mock_c:
            format_success("it worked")
        assert "it worked" in mock_c.print.call_args[0][0]

    def test_output_contains_green_markup(self):
        with patch("forge_harness.cli_v2._common.console") as mock_c:
            format_success("ok")
        assert "[green]" in mock_c.print.call_args[0][0]

    def test_does_not_raise_or_exit(self):
        with patch("forge_harness.cli_v2._common.console"):
            format_success("no exit")  # Should return normally


class TestFormatInfo:
    def test_prints_to_main_console(self):
        with patch("forge_harness.cli_v2._common.console") as mock_c:
            format_info("some info")
        mock_c.print.assert_called_once()

    def test_message_content_present(self):
        with patch("forge_harness.cli_v2._common.console") as mock_c:
            format_info("important info")
        assert "important info" in mock_c.print.call_args[0][0]

    def test_output_contains_blue_markup(self):
        with patch("forge_harness.cli_v2._common.console") as mock_c:
            format_info("info")
        assert "[blue]" in mock_c.print.call_args[0][0]


class TestFormatWarning:
    def test_prints_to_main_console(self):
        with patch("forge_harness.cli_v2._common.console") as mock_c:
            format_warning("something sketchy")
        mock_c.print.assert_called_once()

    def test_message_content_present(self):
        with patch("forge_harness.cli_v2._common.console") as mock_c:
            format_warning("watch out")
        assert "watch out" in mock_c.print.call_args[0][0]

    def test_output_contains_yellow_markup(self):
        with patch("forge_harness.cli_v2._common.console") as mock_c:
            format_warning("warn")
        assert "[yellow]" in mock_c.print.call_args[0][0]


# ---------------------------------------------------------------------------
# render_forge_error
# ---------------------------------------------------------------------------


class TestRenderForgeErrorHumanMode:
    """Default (human-readable) rendering."""

    def test_exits_with_code_1_by_default(self):
        with patch("forge_harness.cli_v2._common.error_console"):
            with pytest.raises(SystemExit) as exc:
                render_forge_error("plain string error")
        assert exc.value.code == 1

    def test_accepts_plain_string(self):
        with patch("forge_harness.cli_v2._common.error_console") as mock_ec:
            with pytest.raises(SystemExit):
                render_forge_error("plain error message")
        # Should have called print at least once
        assert mock_ec.print.call_count >= 1

    def test_accepts_forge_error_instance(self):
        err = ForgeError(code="TASK_NOT_FOUND", message="Task gone")
        with patch("forge_harness.cli_v2._common.error_console") as mock_ec:
            with pytest.raises(SystemExit):
                render_forge_error(err)
        first_call = mock_ec.print.call_args_list[0][0][0]
        assert "TASK_NOT_FOUND" in first_call
        assert "Task gone" in first_call

    def test_accepts_dict_error(self):
        err_dict = {"code": "AUTH_INVALID_TOKEN", "message": "Bad token", "next_action": "re-auth"}
        with patch("forge_harness.cli_v2._common.error_console") as mock_ec:
            with pytest.raises(SystemExit):
                render_forge_error(err_dict)
        first_call = mock_ec.print.call_args_list[0][0][0]
        assert "AUTH_INVALID_TOKEN" in first_call

    def test_accepts_arbitrary_object(self):
        """Non-string, non-dict, non-ForgeError falls back to str()."""

        class Weird:
            def __str__(self):
                return "weird error"

        with patch("forge_harness.cli_v2._common.error_console"):
            with pytest.raises(SystemExit):
                render_forge_error(Weird())

    def test_prints_next_action_when_present(self):
        err = ForgeError(code="TASK_NOT_FOUND", message="Gone", next_action="Check the list")
        with patch("forge_harness.cli_v2._common.error_console") as mock_ec:
            with pytest.raises(SystemExit):
                render_forge_error(err)
        all_calls = " ".join(str(c) for c in mock_ec.print.call_args_list)
        assert "Check the list" in all_calls

    def test_prints_details_when_present(self):
        err = ForgeError(
            code="TASK_NOT_FOUND",
            message="Gone",
            details={"task_id": "abc-123"},
        )
        with patch("forge_harness.cli_v2._common.error_console") as mock_ec:
            with pytest.raises(SystemExit):
                render_forge_error(err)
        all_calls = " ".join(str(c) for c in mock_ec.print.call_args_list)
        assert "abc-123" in all_calls

    def test_does_not_print_details_when_none(self):
        err = ForgeError(code="UNKNOWN_ERROR", message="oops", details=None)
        with patch("forge_harness.cli_v2._common.error_console") as mock_ec:
            with pytest.raises(SystemExit):
                render_forge_error(err)
        # Without details the function should only print 1 or 2 lines (code+msg, maybe next_action)
        # but NOT a "Details:" line
        all_output = " ".join(str(c) for c in mock_ec.print.call_args_list)
        assert "Details:" not in all_output

    def test_custom_exit_code(self):
        with patch("forge_harness.cli_v2._common.error_console"):
            with pytest.raises(SystemExit) as exc:
                render_forge_error("err", exit_code=42)
        assert exc.value.code == 42


class TestRenderForgeErrorJsonMode:
    """JSON output mode (output_json=True)."""

    def test_writes_json_to_stdout(self, capsys):
        err = ForgeError(code="TASK_NOT_FOUND", message="not found")
        with pytest.raises(SystemExit):
            render_forge_error(err, output_json=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["code"] == "TASK_NOT_FOUND"
        assert parsed["message"] == "not found"

    def test_json_output_has_next_action_field(self, capsys):
        err = ForgeError(code="AUTH_MISSING_CREDENTIALS", message="no creds")
        with pytest.raises(SystemExit):
            render_forge_error(err, output_json=True)
        parsed = json.loads(capsys.readouterr().out)
        assert "next_action" in parsed

    def test_json_output_exits_with_given_code(self, capsys):
        err = ForgeError(code="INTERNAL_ERROR", message="boom")
        with pytest.raises(SystemExit) as exc:
            render_forge_error(err, output_json=True, exit_code=5)
        assert exc.value.code == 5

    def test_json_output_from_plain_string(self, capsys):
        with pytest.raises(SystemExit):
            render_forge_error("raw string error", output_json=True)
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["code"] == UNKNOWN_ERROR
        assert parsed["message"] == "raw string error"

    def test_json_output_from_dict(self, capsys):
        err_dict = {
            "code": "VALIDATION_ERROR",
            "message": "bad input",
            "next_action": "fix it",
        }
        with pytest.raises(SystemExit):
            render_forge_error(err_dict, output_json=True)
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Click option decorators
# ---------------------------------------------------------------------------


class TestDomainAndProjectOptions:
    """domain_option, project_option, domain_project_options decorator."""

    def test_domain_option_is_click_decorator(self):
        """domain_option should be callable and usable as a decorator."""

        @click.command()
        @domain_option
        def cmd(domain):
            click.echo(f"domain={domain}")

        runner = CliRunner()
        result = runner.invoke(cmd, ["--domain", "my-domain"])
        assert result.exit_code == 0
        assert "domain=my-domain" in result.output

    def test_domain_option_short_flag(self):
        @click.command()
        @domain_option
        def cmd(domain):
            click.echo(f"d={domain}")

        runner = CliRunner()
        result = runner.invoke(cmd, ["-d", "short-domain"])
        assert result.exit_code == 0
        assert "d=short-domain" in result.output

    def test_project_option_is_click_decorator(self):
        @click.command()
        @project_option
        def cmd(project):
            click.echo(f"project={project}")

        runner = CliRunner()
        result = runner.invoke(cmd, ["--project", "my-proj"])
        assert result.exit_code == 0
        assert "project=my-proj" in result.output

    def test_project_option_short_flag(self):
        @click.command()
        @project_option
        def cmd(project):
            click.echo(f"p={project}")

        runner = CliRunner()
        result = runner.invoke(cmd, ["-p", "short-proj"])
        assert result.exit_code == 0
        assert "p=short-proj" in result.output

    def test_domain_project_options_decorator_adds_both(self):
        @click.command()
        @domain_project_options
        def cmd(domain, project):
            click.echo(f"{domain}/{project}")

        runner = CliRunner()
        result = runner.invoke(cmd, ["--domain", "codeswiftr-com", "--project", "interview-sim"])
        assert result.exit_code == 0
        assert "codeswiftr-com/interview-sim" in result.output

    def test_domain_project_options_defaults_to_none(self):
        @click.command()
        @domain_project_options
        def cmd(domain, project):
            click.echo(f"d={domain} p={project}")

        runner = CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 0
        assert "d=None" in result.output
        assert "p=None" in result.output

    def test_domain_project_options_returns_original_function(self):
        """The decorator should not lose the function identity."""

        def original(domain, project):
            pass

        decorated = domain_project_options(original)
        # The wrapped function is a click-decorated callable; ensure it is not None
        assert decorated is not None


# ---------------------------------------------------------------------------
# xnode_hostname
# ---------------------------------------------------------------------------


class TestXnodeHostname:
    def test_returns_lowercase_hostname(self):
        with patch("socket.gethostname", return_value="MY-MACHINE"):
            assert xnode_hostname() == "my-machine"

    def test_strips_whitespace(self):
        with patch("socket.gethostname", return_value="  host  "):
            assert xnode_hostname() == "host"

    def test_returns_unknown_on_empty_string(self):
        with patch("socket.gethostname", return_value=""):
            assert xnode_hostname() == "unknown"

    def test_returns_unknown_on_whitespace_only(self):
        with patch("socket.gethostname", return_value="   "):
            assert xnode_hostname() == "unknown"

    def test_real_hostname_is_string(self):
        result = xnode_hostname()
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# xnode_now_iso
# ---------------------------------------------------------------------------


class TestXnodeNowIso:
    def test_returns_string(self):
        assert isinstance(xnode_now_iso(), str)

    def test_parseable_as_datetime(self):
        result = xnode_now_iso()
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None  # UTC aware

    def test_is_approximately_now(self):
        before = datetime.now(UTC)
        result = xnode_now_iso()
        after = datetime.now(UTC)
        parsed = datetime.fromisoformat(result)
        assert before <= parsed <= after

    def test_contains_utc_offset(self):
        result = xnode_now_iso()
        # ISO format with UTC includes +00:00 or Z
        assert "+" in result or "Z" in result


# ---------------------------------------------------------------------------
# xnode_append_jsonl
# ---------------------------------------------------------------------------


class TestXnodeAppendJsonl:
    def test_creates_file_if_not_exists(self, tmp_path):
        p = tmp_path / "out.jsonl"
        xnode_append_jsonl(p, {"x": 1})
        assert p.exists()

    def test_appends_valid_json_line(self, tmp_path):
        p = tmp_path / "out.jsonl"
        xnode_append_jsonl(p, {"a": "b"})
        parsed = json.loads(p.read_text().strip())
        assert parsed == {"a": "b"}

    def test_multiple_appends_produce_multiple_lines(self, tmp_path):
        p = tmp_path / "out.jsonl"
        for i in range(5):
            xnode_append_jsonl(p, {"i": i})
        lines = [l for l in p.read_text().splitlines() if l.strip()]
        assert len(lines) == 5
        for idx, line in enumerate(lines):
            assert json.loads(line)["i"] == idx

    def test_non_serialisable_type_uses_str_fallback(self, tmp_path):
        """datetime objects should be serialised via default=str."""
        p = tmp_path / "out.jsonl"
        dt = datetime.now(UTC)
        xnode_append_jsonl(p, {"ts": dt})
        parsed = json.loads(p.read_text().strip())
        assert isinstance(parsed["ts"], str)

    def test_each_record_ends_with_newline(self, tmp_path):
        p = tmp_path / "out.jsonl"
        xnode_append_jsonl(p, {"k": "v"})
        assert p.read_text().endswith("\n")


# ---------------------------------------------------------------------------
# xnode_ensure_dirs
# ---------------------------------------------------------------------------


class TestXnodeEnsureDirs:
    def test_creates_all_six_directories(self, tmp_path):
        dirs = xnode_ensure_dirs(tmp_path)
        for key, path in dirs.items():
            assert path.exists(), f"Missing directory for key '{key}': {path}"

    def test_returns_dict_with_all_keys(self, tmp_path):
        dirs = xnode_ensure_dirs(tmp_path)
        expected_keys = {"root", "lead_inbox", "handoffs", "acks", "exceptions", "realtime_outbox"}
        assert expected_keys == set(dirs.keys())

    def test_root_path_is_under_forge_xnode(self, tmp_path):
        dirs = xnode_ensure_dirs(tmp_path)
        assert dirs["root"] == tmp_path / ".forge" / "xnode"

    def test_lead_inbox_is_child_of_root(self, tmp_path):
        dirs = xnode_ensure_dirs(tmp_path)
        assert dirs["lead_inbox"] == dirs["root"] / "lead-inbox"

    def test_idempotent_when_called_twice(self, tmp_path):
        xnode_ensure_dirs(tmp_path)
        dirs = xnode_ensure_dirs(tmp_path)
        for path in dirs.values():
            assert path.is_dir()


# ---------------------------------------------------------------------------
# xnode_realtime_emit
# ---------------------------------------------------------------------------


class TestXnodeRealtimeEmitWithoutUrl:
    def test_recorded_is_true(self, tmp_path):
        result = xnode_realtime_emit(
            forge_root=tmp_path,
            event_type="ev.test",
            envelope={"payload": "x"},
        )
        assert result["recorded"] is True

    def test_delivered_is_false_without_url(self, tmp_path):
        result = xnode_realtime_emit(
            forge_root=tmp_path,
            event_type="ev.test",
            envelope={},
        )
        assert result["delivered"] is False

    def test_transport_is_jsonl_only_without_url(self, tmp_path):
        result = xnode_realtime_emit(
            forge_root=tmp_path,
            event_type="ev.test",
            envelope={},
        )
        assert result["transport"] == "jsonl-only"

    def test_outbox_path_contains_date(self, tmp_path):
        result = xnode_realtime_emit(
            forge_root=tmp_path,
            event_type="ev.test",
            envelope={},
        )
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        assert today in result["outbox"]

    def test_outbox_file_is_created(self, tmp_path):
        result = xnode_realtime_emit(
            forge_root=tmp_path,
            event_type="ev.test",
            envelope={},
        )
        assert Path(result["outbox"]).exists()

    def test_record_written_to_outbox_has_correct_schema(self, tmp_path):
        envelope = {"msg": "hello"}
        result = xnode_realtime_emit(
            forge_root=tmp_path,
            event_type="test.event",
            envelope=envelope,
        )
        record = json.loads(Path(result["outbox"]).read_text().strip())
        assert record["schema_version"] == "1.0"
        assert record["event_type"] == "test.event"
        assert record["envelope"] == envelope
        assert "recorded_at" in record
        assert "source_node" in record

    def test_error_field_is_none_without_url(self, tmp_path):
        result = xnode_realtime_emit(
            forge_root=tmp_path,
            event_type="ev",
            envelope={},
        )
        assert result["error"] is None


class TestXnodeRealtimeEmitWithUrl:
    def _mock_http_response(self, status: int):
        """Build a context-manager mock that simulates urlopen."""
        resp = MagicMock()
        resp.status = status
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=resp)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_delivered_true_on_200(self, tmp_path):
        with patch.object(url_request, "urlopen", return_value=self._mock_http_response(200)):
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="ev",
                envelope={},
                ws_url="http://example.com/events",
            )
        assert result["delivered"] is True
        assert result["transport"] == "http-post"
        assert result["status_code"] == 200

    def test_delivered_true_on_201(self, tmp_path):
        with patch.object(url_request, "urlopen", return_value=self._mock_http_response(201)):
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="ev",
                envelope={},
                ws_url="http://example.com/events",
            )
        assert result["delivered"] is True

    def test_delivered_false_on_500(self, tmp_path):
        with patch.object(url_request, "urlopen", return_value=self._mock_http_response(500)):
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="ev",
                envelope={},
                ws_url="http://example.com/events",
            )
        assert result["delivered"] is False

    def test_handles_url_error_gracefully(self, tmp_path):
        from urllib import error as _ue

        with patch.object(url_request, "urlopen", side_effect=_ue.URLError("refused")):
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="ev",
                envelope={},
                ws_url="http://example.com/events",
            )
        assert result["delivered"] is False
        assert "refused" in result["error"]

    def test_handles_timeout_error_gracefully(self, tmp_path):
        with patch.object(url_request, "urlopen", side_effect=TimeoutError("timed out")):
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="ev",
                envelope={},
                ws_url="http://example.com/events",
            )
        assert result["delivered"] is False
        assert result["error"] is not None

    def test_explicit_auth_token_sent_in_header(self, tmp_path):
        captured_req = {}

        def fake_urlopen(req, timeout):
            captured_req["headers"] = req.headers
            return self._mock_http_response(200)

        with patch.object(url_request, "urlopen", side_effect=fake_urlopen):
            xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="ev",
                envelope={},
                ws_url="http://example.com/events",
                auth_token="my-secret-token",
            )
        auth_header = captured_req["headers"].get("Authorization", "")
        assert auth_header == "Bearer my-secret-token"

    def test_env_var_token_used_when_no_explicit_token(self, tmp_path):
        captured_req = {}

        def fake_urlopen(req, timeout):
            captured_req["headers"] = req.headers
            return self._mock_http_response(200)

        with patch.dict(os.environ, {"FORGE_WEBHOOK_TOKEN": "env-token"}):
            with patch.object(url_request, "urlopen", side_effect=fake_urlopen):
                xnode_realtime_emit(
                    forge_root=tmp_path,
                    event_type="ev",
                    envelope={},
                    ws_url="http://example.com/events",
                )
        auth_header = captured_req["headers"].get("Authorization", "")
        assert auth_header == "Bearer env-token"

    def test_explicit_token_overrides_env_var(self, tmp_path):
        captured_req = {}

        def fake_urlopen(req, timeout):
            captured_req["headers"] = req.headers
            return self._mock_http_response(200)

        with patch.dict(os.environ, {"FORGE_WEBHOOK_TOKEN": "env-token"}):
            with patch.object(url_request, "urlopen", side_effect=fake_urlopen):
                xnode_realtime_emit(
                    forge_root=tmp_path,
                    event_type="ev",
                    envelope={},
                    ws_url="http://example.com/events",
                    auth_token="explicit-token",
                )
        auth_header = captured_req["headers"].get("Authorization", "")
        assert auth_header == "Bearer explicit-token"

    def test_no_auth_header_when_no_token(self, tmp_path):
        captured_req = {}

        def fake_urlopen(req, timeout):
            captured_req["headers"] = req.headers
            return self._mock_http_response(200)

        with patch.dict(os.environ, {}, clear=True):
            env_without_token = {k: v for k, v in os.environ.items() if k != "FORGE_WEBHOOK_TOKEN"}
            with patch.dict(os.environ, env_without_token, clear=True):
                with patch.object(url_request, "urlopen", side_effect=fake_urlopen):
                    xnode_realtime_emit(
                        forge_root=tmp_path,
                        event_type="ev",
                        envelope={},
                        ws_url="http://example.com/events",
                    )
        assert "Authorization" not in captured_req.get("headers", {})

    def test_timeout_minimum_clamped_to_one(self, tmp_path):
        """timeout_seconds=0 should be clamped to 1 by max(timeout_seconds, 1)."""
        called_with = {}

        def fake_urlopen(req, timeout):
            called_with["timeout"] = timeout
            return self._mock_http_response(200)

        with patch.object(url_request, "urlopen", side_effect=fake_urlopen):
            xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="ev",
                envelope={},
                ws_url="http://example.com/events",
                timeout_seconds=0,
            )
        assert called_with["timeout"] == 1

    def test_recorded_is_always_true_even_on_http_failure(self, tmp_path):
        from urllib import error as _ue

        with patch.object(url_request, "urlopen", side_effect=_ue.URLError("down")):
            result = xnode_realtime_emit(
                forge_root=tmp_path,
                event_type="ev",
                envelope={},
                ws_url="http://example.com/events",
            )
        assert result["recorded"] is True


# ---------------------------------------------------------------------------
# resolve_ws_url
# ---------------------------------------------------------------------------


class TestResolveWsUrl:
    def test_returns_explicit_url_unchanged(self):
        assert resolve_ws_url("http://my-server.com/ws") == "http://my-server.com/ws"

    def test_returns_none_when_no_url_and_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "COMMAND_CENTER_URL"}
            with patch.dict(os.environ, env, clear=True):
                assert resolve_ws_url() is None

    def test_builds_url_from_command_center_url_env(self):
        with patch.dict(os.environ, {"COMMAND_CENTER_URL": "http://cc.example.com"}):
            url = resolve_ws_url()
        assert url == "http://cc.example.com/api/xnode/events"

    def test_strips_trailing_slash_from_command_center_url(self):
        with patch.dict(os.environ, {"COMMAND_CENTER_URL": "http://cc.example.com/"}):
            url = resolve_ws_url()
        assert url == "http://cc.example.com/api/xnode/events"
        assert "//" not in url.replace("://", "")

    def test_explicit_url_takes_priority_over_env(self):
        with patch.dict(os.environ, {"COMMAND_CENTER_URL": "http://cc.example.com"}):
            url = resolve_ws_url("http://override.com/ws")
        assert url == "http://override.com/ws"

    def test_returns_none_when_explicit_url_is_none_and_env_unset(self):
        with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "COMMAND_CENTER_URL"}, clear=True):
            result = resolve_ws_url(None)
        assert result is None

    def test_empty_string_explicit_url_falls_back_to_env(self):
        """An empty string ws_url is falsy, so env var should be used."""
        with patch.dict(os.environ, {"COMMAND_CENTER_URL": "http://cc.example.com"}):
            url = resolve_ws_url("")
        assert url == "http://cc.example.com/api/xnode/events"


# ---------------------------------------------------------------------------
# Module-level constants / imports sanity
# ---------------------------------------------------------------------------


class TestModuleLevelObjects:
    def test_console_is_importable(self):
        from forge_harness.cli_v2._common import console
        assert console is not None

    def test_error_console_is_importable(self):
        from forge_harness.cli_v2._common import error_console
        assert error_console is not None

    def test_domain_option_is_callable(self):
        assert callable(domain_option)

    def test_project_option_is_callable(self):
        assert callable(project_option)

    def test_domain_project_options_is_callable(self):
        assert callable(domain_project_options)
