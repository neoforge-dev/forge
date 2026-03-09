"""Tests for cli_v2/_common.py"""
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest
from forge_harness.cli_v2._common import (
    domain_option,
    domain_project_options,
    find_forge_root,
    format_error,
    format_info,
    format_success,
    format_warning,
    infer_domain_project,
    load_cli_env_from_rc,
    project_option,
    require_forge_root,
    xnode_append_jsonl,
    xnode_ensure_dirs,
    xnode_hostname,
    xnode_now_iso,
    xnode_realtime_emit,
)


class TestFindForgeRoot:
    """Tests for find_forge_root function."""

    def test_finds_forge_root_with_marker(self, tmp_path):
        """Should find root when .forge-root marker exists."""
        (tmp_path / ".forge-root").touch()

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = find_forge_root()
            assert result == tmp_path

    def test_finds_forge_root_with_domains_yaml(self, tmp_path):
        """Should find root when domains.yaml exists."""
        (tmp_path / "domains.yaml").touch()

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = find_forge_root()
            assert result == tmp_path

    def test_finds_forge_root_with_git_and_domain_dirs(self, tmp_path):
        """Should find root when .git exists and domain-like dirs present."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "test-domain").mkdir()

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = find_forge_root()
            assert result == tmp_path

    def test_searches_parent_directories(self, tmp_path):
        """Should search up directory tree for root."""
        root = tmp_path / "FORGE"
        root.mkdir()
        (root / ".forge-root").touch()

        subdir = root / "subdir" / "nested"
        subdir.mkdir(parents=True)

        with patch("pathlib.Path.cwd", return_value=subdir):
            result = find_forge_root()
            assert result == root

    def test_returns_none_when_not_found(self, tmp_path):
        """Should return None when no FORGE root found."""
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = find_forge_root()
            assert result is None

    def test_limits_search_depth(self, tmp_path):
        """Should limit search to 10 levels deep."""
        # Create a very deep directory structure
        current = tmp_path
        for i in range(15):
            current = current / f"level{i}"
            current.mkdir()

        with patch("pathlib.Path.cwd", return_value=current):
            result = find_forge_root()
            assert result is None


class TestRequireForgeRoot:
    """Tests for require_forge_root function."""

    def test_returns_root_when_found(self, tmp_path):
        """Should return root when found."""
        (tmp_path / ".forge-root").touch()

        with patch("forge_harness.cli_v2._common.find_forge_root", return_value=tmp_path):
            result = require_forge_root()
            assert result == tmp_path

    def test_exits_when_not_found(self):
        """Should exit with error when root not found."""
        with patch("forge_harness.cli_v2._common.find_forge_root", return_value=None):
            with patch("forge_harness.cli_v2._common.error_console") as mock_console:
                with pytest.raises(SystemExit) as exc_info:
                    require_forge_root()
                assert exc_info.value.code == 1


class TestLoadCliEnvFromRc:
    """Tests for loading ~/.forgerc defaults."""

    def test_prefers_forgerc_over_forcerc(self, tmp_path):
        """Should load ~/.forgerc when both files exist."""
        home = tmp_path
        (home / ".forgerc").write_text("FORGE_WEBHOOK_TOKEN=from-forgerc\n")
        (home / ".forcerc").write_text("FORGE_WEBHOOK_TOKEN=from-forcerc\n")
        env = {}

        result = load_cli_env_from_rc(environ=env, home=home)

        assert result["loaded_from"] == str(home / ".forgerc")
        assert env["FORGE_WEBHOOK_TOKEN"] == "from-forgerc"

    def test_falls_back_to_forcerc(self, tmp_path):
        """Should load ~/.forcerc when ~/.forgerc is absent."""
        home = tmp_path
        (home / ".forcerc").write_text("COMMAND_CENTER_URL=http://example.test\n")
        env = {}

        result = load_cli_env_from_rc(environ=env, home=home)

        assert result["loaded_from"] == str(home / ".forcerc")
        assert env["COMMAND_CENTER_URL"] == "http://example.test"
        assert env["FORGE_API_URL"] == "http://example.test"

    def test_adds_command_center_alias_from_forge_api_url(self, tmp_path):
        """FORGE_API_URL should backfill COMMAND_CENTER_URL when only one is set."""
        home = tmp_path
        (home / ".forgerc").write_text("FORGE_API_URL=http://api.example.test\n")
        env = {}

        load_cli_env_from_rc(environ=env, home=home)

        assert env["FORGE_API_URL"] == "http://api.example.test"
        assert env["COMMAND_CENTER_URL"] == "http://api.example.test"

    def test_does_not_override_existing_env(self, tmp_path):
        """Existing environment values should win over rc defaults."""
        home = tmp_path
        (home / ".forgerc").write_text("FORGE_WEBHOOK_TOKEN=rc-token\n")
        env = {"FORGE_WEBHOOK_TOKEN": "already-set"}

        load_cli_env_from_rc(environ=env, home=home)

        assert env["FORGE_WEBHOOK_TOKEN"] == "already-set"

    def test_supports_export_and_quoted_values(self, tmp_path):
        """Parser should handle export syntax and quoted values."""
        home = tmp_path
        (home / ".forgerc").write_text(
            "export COMMAND_CENTER_URL=\"http://cc.local:8080\"\n"
            "FORGE_NODE_NAME='prya'\n"
        )
        env = {}

        load_cli_env_from_rc(environ=env, home=home)

        assert env["COMMAND_CENTER_URL"] == "http://cc.local:8080"
        assert env["FORGE_NODE_NAME"] == "prya"

    def test_uses_forge_rc_file_override(self, tmp_path):
        """FORGE_RC_FILE should override default path search."""
        home = tmp_path / "home"
        home.mkdir(parents=True)
        custom_rc = tmp_path / "custom.rc"
        custom_rc.write_text("FORGE_NODE_ID=custom-node\n")
        env = {"FORGE_RC_FILE": str(custom_rc)}

        result = load_cli_env_from_rc(environ=env, home=home)

        assert result["loaded_from"] == str(custom_rc)
        assert env["FORGE_NODE_ID"] == "custom-node"


class TestInferDomainProject:
    """Tests for infer_domain_project function."""

    def test_infers_both_domain_and_project(self, tmp_path):
        """Should infer both domain and project from path."""
        forge_root = tmp_path / "FORGE"
        domain_dir = forge_root / "test-domain" / "test-project"
        domain_dir.mkdir(parents=True)

        with patch("pathlib.Path.cwd", return_value=domain_dir):
            domain, project = infer_domain_project(forge_root)
            assert domain == "test-domain"
            assert project == "test-project"

    def test_infers_only_domain(self, tmp_path):
        """Should infer only domain when in domain root."""
        forge_root = tmp_path / "FORGE"
        domain_dir = forge_root / "test-domain"
        domain_dir.mkdir(parents=True)

        with patch("pathlib.Path.cwd", return_value=domain_dir):
            domain, project = infer_domain_project(forge_root)
            assert domain == "test-domain"
            assert project is None

    def test_returns_none_none_when_outside_forge(self, tmp_path):
        """Should return (None, None) when outside FORGE."""
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        forge_root = tmp_path / "FORGE"

        with patch("pathlib.Path.cwd", return_value=outside_dir):
            domain, project = infer_domain_project(forge_root)
            assert domain is None
            assert project is None


class TestFormatFunctions:
    """Tests for format functions."""

    def test_format_error(self):
        """Should print error message and exit."""
        with patch("forge_harness.cli_v2._common.error_console") as mock_console:
            with pytest.raises(SystemExit) as exc_info:
                format_error("Test error")
            assert exc_info.value.code == 1
            mock_console.print.assert_called_once()

    def test_format_success(self):
        """Should print success message."""
        with patch("forge_harness.cli_v2._common.console") as mock_console:
            format_success("Test success")
            mock_console.print.assert_called_once()
            args = mock_console.print.call_args[0][0]
            assert "Test success" in str(args)

    def test_format_info(self):
        """Should print info message."""
        with patch("forge_harness.cli_v2._common.console") as mock_console:
            format_info("Test info")
            mock_console.print.assert_called_once()
            args = mock_console.print.call_args[0][0]
            assert "Test info" in str(args)

    def test_format_warning(self):
        """Should print warning message."""
        with patch("forge_harness.cli_v2._common.console") as mock_console:
            format_warning("Test warning")
            mock_console.print.assert_called_once()
            args = mock_console.print.call_args[0][0]
            assert "Test warning" in str(args)


class TestXnodeHostname:
    """Tests for xnode_hostname function."""

    def test_returns_lowercase_hostname(self):
        """Should return lowercase hostname."""
        with patch("socket.gethostname", return_value="TEST-HOST"):
            result = xnode_hostname()
            assert result == "test-host"

    def test_returns_unknown_on_empty(self):
        """Should return 'unknown' when hostname is empty."""
        with patch("socket.gethostname", return_value=""):
            result = xnode_hostname()
            assert result == "unknown"

    def test_strips_whitespace(self):
        """Should strip whitespace from hostname."""
        with patch("socket.gethostname", return_value="  test-host  "):
            result = xnode_hostname()
            assert result == "test-host"


class TestXnodeNowIso:
    """Tests for xnode_now_iso function."""

    def test_returns_iso_format(self):
        """Should return ISO format timestamp."""
        result = xnode_now_iso()
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None or "Z" in result or "+" in result


class TestXnodeAppendJsonl:
    """Tests for xnode_append_jsonl function."""

    def test_appends_json_record(self, tmp_path):
        """Should append JSON record to file."""
        path = tmp_path / "test.jsonl"
        payload = {"key": "value", "number": 42}

        xnode_append_jsonl(path, payload)

        content = path.read_text()
        assert json.loads(content.strip()) == payload

    def test_appends_multiple_records(self, tmp_path):
        """Should append multiple records as separate lines."""
        path = tmp_path / "test.jsonl"

        xnode_append_jsonl(path, {"id": 1})
        xnode_append_jsonl(path, {"id": 2})

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"id": 1}
        assert json.loads(lines[1]) == {"id": 2}


class TestXnodeEnsureDirs:
    """Tests for xnode_ensure_dirs function."""

    def test_creates_all_directories(self, tmp_path):
        """Should create all xnode directories."""
        forge_root = tmp_path / "FORGE"

        dirs = xnode_ensure_dirs(forge_root)

        assert dirs["root"].exists()
        assert dirs["lead_inbox"].exists()
        assert dirs["handoffs"].exists()
        assert dirs["acks"].exists()
        assert dirs["exceptions"].exists()
        assert dirs["realtime_outbox"].exists()

    def test_returns_correct_paths(self, tmp_path):
        """Should return correct directory paths."""
        forge_root = tmp_path / "FORGE"

        dirs = xnode_ensure_dirs(forge_root)

        assert dirs["root"] == forge_root / ".forge" / "xnode"
        assert dirs["lead_inbox"] == dirs["root"] / "lead-inbox"


class TestXnodeRealtimeEmit:
    """Tests for xnode_realtime_emit function."""

    def test_records_to_outbox_without_ws_url(self, tmp_path):
        """Should record to outbox when no WebSocket URL."""
        forge_root = tmp_path / "FORGE"
        envelope = {"data": "test"}

        result = xnode_realtime_emit(
            forge_root=forge_root,
            event_type="test_event",
            envelope=envelope,
        )

        assert result["recorded"] is True
        assert result["delivered"] is False
        assert result["transport"] == "jsonl-only"
        assert result["outbox"] is not None

    def test_attempts_http_delivery_with_ws_url(self, tmp_path):
        """Should attempt HTTP delivery when WebSocket URL provided."""
        forge_root = tmp_path / "FORGE"
        envelope = {"data": "test"}

        # Create a mock response class with a status attribute
        class MockResponse:
            status = 200

        # Create a context manager mock
        mock_context = MagicMock()
        mock_context.__enter__ = MagicMock(return_value=MockResponse())
        mock_context.__exit__ = MagicMock(return_value=False)

        from forge_harness.cli_v2._common import url_request

        with patch.object(url_request, "urlopen", return_value=mock_context):
            result = xnode_realtime_emit(
                forge_root=forge_root,
                event_type="test_event",
                envelope=envelope,
                ws_url="http://test.example.com/ws",
            )

        assert result["recorded"] is True
        assert result["delivered"] is True
        assert result["transport"] == "http-post"
        assert result["status_code"] == 200

    def test_handles_http_error(self, tmp_path):
        """Should handle HTTP errors gracefully."""
        forge_root = tmp_path / "FORGE"
        envelope = {"data": "test"}

        from urllib import error as url_error

        with patch("urllib.request.urlopen", side_effect=url_error.URLError("Connection failed")):
            result = xnode_realtime_emit(
                forge_root=forge_root,
                event_type="test_event",
                envelope=envelope,
                ws_url="http://test.example.com/ws",
            )

        assert result["recorded"] is True
        assert result["delivered"] is False
        assert "Connection failed" in result["error"]

    def test_includes_required_fields_in_record(self, tmp_path):
        """Should include schema_version, event_type, recorded_at, source_node."""
        forge_root = tmp_path / "FORGE"
        envelope = {"data": "test"}

        result = xnode_realtime_emit(
            forge_root=forge_root,
            event_type="test_event",
            envelope=envelope,
        )

        outbox_file = Path(result["outbox"])
        content = outbox_file.read_text().strip()
        record = json.loads(content)

        assert record["schema_version"] == "1.0"
        assert record["event_type"] == "test_event"
        assert "recorded_at" in record
        assert "source_node" in record
        assert record["envelope"] == envelope
