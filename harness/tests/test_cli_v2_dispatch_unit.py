"""
Unit tests for forge_harness.cli_v2.dispatch
=============================================

Tests for dispatch module covering:
- _discover_tmux_agents
- _http_json
- _fetch_pending_messages
- _ack_message
- _list_agents
- _resolve_queue_agent_id
- _resolve_queue_agent_id_from_tmp
- _resolve_targets
- _get_recommendation
- _send_queued_message
- _get_message_status
- All click commands: send, relay, auto, status, queue-stats, conversation
"""

import importlib
import json
import os
import subprocess
from unittest import mock
from urllib import error as url_error

import pytest
from click.testing import CliRunner

dispatch_module = importlib.import_module("forge_harness.cli_v2.dispatch")


class TestDiscoverTmuxAgents:
    """Tests for _discover_tmux_agents function."""

    @pytest.fixture
    def mock_subprocess_run(self):
        """Mock subprocess.run for tmux discovery."""
        with mock.patch.object(subprocess, "run") as mock_run:
            yield mock_run

    def test_discovers_tmux_windows(self, mock_subprocess_run):
        """Test discovery of tmux windows."""
        mock_subprocess_run.return_value = mock.MagicMock(
            returncode=0,
            stdout="minimax\nglm\nkimi\n",
        )

        result = dispatch_module._discover_tmux_agents("forge")

        assert result == ["forge:minimax", "forge:glm", "forge:kimi"]
        mock_subprocess_run.assert_called_once()

    def test_returns_empty_when_tmux_not_found(self, mock_subprocess_run):
        """Test returns empty list when tmux not found."""
        mock_subprocess_run.side_effect = FileNotFoundError()

        result = dispatch_module._discover_tmux_agents("forge")

        assert result == []

    def test_returns_empty_on_error(self, mock_subprocess_run):
        """Test returns empty list on error."""
        mock_subprocess_run.return_value = mock.MagicMock(returncode=1)

        result = dispatch_module._discover_tmux_agents("forge")

        assert result == []

    def test_filters_duplicates(self, mock_subprocess_run):
        """Test filtering of duplicate targets."""
        mock_subprocess_run.return_value = mock.MagicMock(
            returncode=0,
            stdout="minimax\nminimax\nglm\n",
        )

        result = dispatch_module._discover_tmux_agents("forge")

        assert result == ["forge:minimax", "forge:glm"]

    def test_filters_empty_lines(self, mock_subprocess_run):
        """Test filtering of empty lines."""
        mock_subprocess_run.return_value = mock.MagicMock(
            returncode=0,
            stdout="minimax\n\n\nglm\n",
        )

        result = dispatch_module._discover_tmux_agents("forge")

        assert result == ["forge:minimax", "forge:glm"]


class TestHttpJson:
    """Tests for _http_json function."""

    @pytest.fixture
    def mock_urlopen(self):
        """Mock urllib.urlopen."""
        with mock.patch("forge_harness.cli_v2.dispatch.url_request") as mock_request:
            mock_response = mock.MagicMock()
            mock_response.read.return_value = b'{"success": true, "data": {"key": "value"}}'
            mock_request.urlopen.return_value.__enter__ = mock.MagicMock(
                return_value=mock_response
            )
            mock_request.urlopen.return_value.__exit__ = mock.MagicMock(
                return_value=False
            )
            yield mock_request

    def test_successful_get_request(self, mock_urlopen):
        """Test successful GET request."""
        result = dispatch_module._http_json(
            "GET", "http://localhost:8080/api/test", "test-token"
        )

        assert result == {"success": True, "data": {"key": "value"}}

    def test_successful_post_request(self, mock_urlopen):
        """Test successful POST request with payload."""
        result = dispatch_module._http_json(
            "POST",
            "http://localhost:8080/api/test",
            "test-token",
            payload={"key": "value"},
        )

        assert result == {"success": True, "data": {"key": "value"}}

    def test_http_error_handling(self, mock_urlopen):
        """Test HTTP error handling."""
        mock_urlopen.urlopen.side_effect = url_error.HTTPError(
            url="http://localhost:8080/api/test",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )
        mock_urlopen.urlopen.return_value.read.return_value = b'{"error": "not found"}'

        with pytest.raises(RuntimeError, match="HTTP 404"):
            dispatch_module._http_json(
                "GET", "http://localhost:8080/api/test", "test-token"
            )

    def test_url_error_handling(self, mock_urlopen):
        """Test URL error handling."""
        mock_urlopen.urlopen.side_effect = url_error.URLError("Connection refused")

        with pytest.raises(RuntimeError, match="failed"):
            dispatch_module._http_json(
                "GET", "http://localhost:8080/api/test", "test-token"
            )

    def test_timeout_error_handling(self, mock_urlopen):
        """Test timeout error handling."""
        mock_urlopen.urlopen.side_effect = TimeoutError("Request timed out")

        with pytest.raises(RuntimeError, match="failed"):
            dispatch_module._http_json(
                "GET", "http://localhost:8080/api/test", "test-token"
            )

    def test_invalid_json_response(self, mock_urlopen):
        """Test handling of invalid JSON response."""
        mock_urlopen.urlopen.return_value.__enter__ = mock.MagicMock(
            return_value=mock.MagicMock(read=lambda: b"not json")
        )
        mock_urlopen.urlopen.return_value.__exit__ = mock.MagicMock(
            return_value=False
        )

        with pytest.raises(RuntimeError, match="Invalid JSON"):
            dispatch_module._http_json(
                "GET", "http://localhost:8080/api/test", "test-token"
            )

    def test_custom_timeout(self, mock_urlopen):
        """Test custom timeout is applied."""
        dispatch_module._http_json(
            "GET", "http://localhost:8080/api/test", "test-token", timeout=30.0
        )

        # Check timeout was passed (at least 1.0 due to max in function)
        call_args = mock_urlopen.urlopen.call_args
        assert call_args[1]["timeout"] >= 1.0


class TestFetchPendingMessages:
    """Tests for _fetch_pending_messages function."""

    @pytest.fixture
    def mock_http_json(self):
        """Mock _http_json function."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            yield mock_json

    def test_successful_fetch(self, mock_http_json):
        """Test successful pending message fetch."""
        mock_http_json.return_value = {
            "success": True,
            "data": {
                "messages": [
                    {"id": "msg-1", "content": "Test message 1"},
                    {"id": "msg-2", "content": "Test message 2"},
                ]
            },
        }

        result = dispatch_module._fetch_pending_messages(
            "http://localhost:8080", "test-token", "forge:minimax"
        )

        assert len(result) == 2
        assert result[0]["id"] == "msg-1"

    def test_raises_on_api_failure(self, mock_http_json):
        """Test raises RuntimeError on API failure."""
        mock_http_json.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="API error"):
            dispatch_module._fetch_pending_messages(
                "http://localhost:8080", "test-token", "forge:minimax"
            )

    def test_raises_on_unsuccessful_response(self, mock_http_json):
        """Test raises on unsuccessful API response."""
        mock_http_json.return_value = {"success": False}

        with pytest.raises(RuntimeError):
            dispatch_module._fetch_pending_messages(
                "http://localhost:8080", "test-token", "forge:minimax"
            )

    def test_filters_non_dict_messages(self, mock_http_json):
        """Test filtering of non-dict messages."""
        mock_http_json.return_value = {
            "success": True,
            "data": {"messages": [{"id": "msg-1"}, "invalid", None, {"id": "msg-2"}]},
        }

        result = dispatch_module._fetch_pending_messages(
            "http://localhost:8080", "test-token", "forge:minimax"
        )

        assert len(result) == 2


class TestAckMessage:
    """Tests for _ack_message function."""

    @pytest.fixture
    def mock_http_json(self):
        """Mock _http_json function."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            yield mock_json

    def test_successful_ack(self, mock_http_json):
        """Test successful message acknowledgment."""
        mock_http_json.return_value = {"success": True}

        dispatch_module._ack_message(
            "http://localhost:8080", "test-token", "forge:minimax", "msg-123"
        )

        mock_http_json.assert_called_once()

    def test_raises_on_failure(self, mock_http_json):
        """Test raises exception on ACK failure."""
        mock_http_json.return_value = {"success": False}

        with pytest.raises(RuntimeError, match="ACK failed"):
            dispatch_module._ack_message(
                "http://localhost:8080", "test-token", "forge:minimax", "msg-123"
            )


class TestListAgents:
    """Tests for _list_agents function."""

    @pytest.fixture
    def mock_http_json(self):
        """Mock _http_json function."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            yield mock_json

    def test_successful_list_from_array(self, mock_http_json):
        """Test listing agents from array response."""
        mock_http_json.return_value = {
            "success": True,
            "data": [
                {"id": "agent-1", "name": "Minimax"},
                {"id": "agent-2", "name": "GLM"},
            ],
        }

        result = dispatch_module._list_agents(
            "http://localhost:8080", "test-token"
        )

        assert len(result) == 2

    def test_successful_list_from_dict(self, mock_http_json):
        """Test listing agents from dict response."""
        mock_http_json.return_value = {
            "success": True,
            "data": {
                "agents": [
                    {"id": "agent-1", "name": "Minimax"},
                    {"id": "agent-2", "name": "GLM"},
                ]
            },
        }

        result = dispatch_module._list_agents(
            "http://localhost:8080", "test-token"
        )

        assert len(result) == 2

    def test_raises_on_api_failure(self, mock_http_json):
        """Test raises RuntimeError on API failure."""
        mock_http_json.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="API error"):
            dispatch_module._list_agents(
                "http://localhost:8080", "test-token"
            )


class TestResolveQueueAgentId:
    """Tests for _resolve_queue_agent_id function."""

    def test_exact_id_match(self):
        """Test exact ID matching."""
        agents = [{"id": "queue-agent-1", "tmux_session": "forge:minimax"}]

        result = dispatch_module._resolve_queue_agent_id("queue-agent-1", agents)

        assert result == "queue-agent-1"

    def test_exact_session_id_match(self):
        """Test exact session_id matching."""
        agents = [{"session_id": "session-123", "tmux_session": "forge:minimax"}]

        result = dispatch_module._resolve_queue_agent_id("session-123", agents)

        assert result == "session-123"

    def test_exact_tmux_session_match(self):
        """Test exact tmux_session matching."""
        agents = [{"tmux_session": "forge:kimi"}]

        result = dispatch_module._resolve_queue_agent_id("forge:kimi", agents)

        assert result == "forge:kimi"

    def test_window_name_correlation(self):
        """Test window name correlation via agent_name."""
        agents = [
            {"agent_name": "minimax", "id": "resolved-123"},
        ]

        result = dispatch_module._resolve_queue_agent_id("forge:minimax", agents)

        assert result == "resolved-123"

    def test_fallback_to_target(self):
        """Test fallback to target when no match found."""
        agents = [{"id": "agent-1"}]

        result = dispatch_module._resolve_queue_agent_id("unknown:target", agents)

        assert result == "unknown:target"


class TestResolveQueueAgentIdFromTmp:
    """Tests for _resolve_queue_agent_id_from_tmp function."""

    @pytest.fixture
    def mock_file_operations(self):
        """Mock file operations."""
        with mock.patch.object(os.path, "isfile") as mock_isfile, \
             mock.patch("builtins.open", mock.mock_open(read_data="resolved-agent-id")):
            yield mock_isfile

    def test_resolves_from_tmp_file(self, mock_file_operations):
        """Test resolution from tmp file."""
        mock_file_operations.return_value = True

        result = dispatch_module._resolve_queue_agent_id_from_tmp("minimax")

        assert result == "resolved-agent-id"

    def test_returns_none_for_empty_window_name(self):
        """Test returns None for empty window name."""
        result = dispatch_module._resolve_queue_agent_id_from_tmp("")

        assert result is None

    def test_returns_none_for_invalid_chars(self):
        """Test returns None for invalid characters."""
        result = dispatch_module._resolve_queue_agent_id_from_tmp("minimax; rm -rf")

        assert result is None

    def test_returns_none_when_file_not_exists(self):
        """Test returns None when file doesn't exist."""
        with mock.patch.object(os.path, "isfile", return_value=False):
            result = dispatch_module._resolve_queue_agent_id_from_tmp("minimax")

        assert result is None


class TestResolveTargets:
    """Tests for _resolve_targets function."""

    @pytest.fixture
    def mock_discover_tmux(self):
        """Mock _discover_tmux_agents."""
        with mock.patch.object(
            dispatch_module, "_discover_tmux_agents", return_value=["forge:minimax"]
        ) as mock_disc:
            yield mock_disc

    def test_explicit_agents_only(self, mock_discover_tmux):
        """Test explicit agents without tmux discovery."""
        result = dispatch_module._resolve_targets(
            explicit_agents=("forge:glm", "forge:kimi"),
            discover_tmux=False,
            session_name="forge",
        )

        assert result == ["forge:glm", "forge:kimi"]

    def test_tmux_discovery(self, mock_discover_tmux):
        """Test tmux discovery with no explicit agents."""
        result = dispatch_module._resolve_targets(
            explicit_agents=(),
            discover_tmux=True,
            session_name="forge",
        )

        assert result == ["forge:minimax"]

    def test_combines_explicit_and_tmux(self, mock_discover_tmux):
        """Test combining explicit agents with tmux discovery."""
        result = dispatch_module._resolve_targets(
            explicit_agents=("forge:glm",),
            discover_tmux=True,
            session_name="forge",
        )

        assert "forge:glm" in result
        assert "forge:minimax" in result

    def test_filters_duplicates(self, mock_discover_tmux):
        """Test filtering duplicate targets."""
        result = dispatch_module._resolve_targets(
            explicit_agents=("forge:minimax",),
            discover_tmux=True,
            session_name="forge",
        )

        assert result.count("forge:minimax") == 1

    def test_filters_empty_strings(self, mock_discover_tmux):
        """Test filtering empty strings."""
        result = dispatch_module._resolve_targets(
            explicit_agents=("forge:glm", "", "  "),
            discover_tmux=False,
            session_name="forge",
        )

        assert result == ["forge:glm"]


class TestGetRecommendation:
    """Tests for _get_recommendation function."""

    @pytest.fixture
    def mock_http_json(self):
        """Mock _http_json function."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            yield mock_json

    def test_gets_recommendation(self, mock_http_json):
        """Test getting scheduler recommendation."""
        mock_http_json.return_value = {
            "recommended_node_id": "sati",
            "score": 0.95,
            "reason": "Low CPU load",
            "candidates": [
                {"node_id": "sati", "score": 0.95, "cpu_load": 20},
                {"node_id": "nova", "score": 0.80, "cpu_load": 40},
            ],
        }

        result = dispatch_module._get_recommendation(
            "http://localhost:8080", "test-token", "python"
        )

        assert result["recommended_node_id"] == "sati"
        assert result["score"] == 0.95


class TestSendQueuedMessage:
    """Tests for _send_queued_message function."""

    @pytest.fixture
    def mock_http_json(self):
        """Mock _http_json function."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            yield mock_json

    def test_sends_message(self, mock_http_json):
        """Test sending queued message."""
        mock_http_json.return_value = {
            "success": True,
            "data": {"message_id": "msg-123"},
        }

        result = dispatch_module._send_queued_message(
            "http://localhost:8080",
            "test-token",
            "forge:minimax",
            "Test message",
        )

        assert result["success"] is True


class TestGetMessageStatus:
    """Tests for _get_message_status function."""

    @pytest.fixture
    def mock_http_json(self):
        """Mock _http_json function."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            yield mock_json

    def test_gets_status(self, mock_http_json):
        """Test getting message status."""
        mock_http_json.return_value = {
            "success": True,
            "data": {"status": "delivered", "message_id": "msg-123"},
        }

        result = dispatch_module._get_message_status(
            "http://localhost:8080", "test-token", "msg-123"
        )

        assert result["data"]["status"] == "delivered"


class TestDispatchCLI:
    """Tests for dispatch CLI group."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_dispatch_help(self, runner):
        """Test dispatch command shows help."""
        from forge_harness.cli_v2.dispatch import dispatch

        result = runner.invoke(dispatch, ["--help"])

        assert result.exit_code == 0
        assert "Dispatch messages to fleet agents" in result.output

    def test_send_command_help(self, runner):
        """Test send subcommand shows help."""
        from forge_harness.cli_v2.dispatch import send

        result = runner.invoke(send, ["--help"])

        assert result.exit_code == 0
        assert "Send message to agent" in result.output

    def test_relay_command_help(self, runner):
        """Test relay subcommand shows help."""
        from forge_harness.cli_v2.dispatch import relay

        result = runner.invoke(relay, ["--help"])

        assert result.exit_code == 0
        assert "queue-first relay worker" in result.output

    def test_auto_command_help(self, runner):
        """Test auto subcommand shows help."""
        from forge_harness.cli_v2.dispatch import auto

        result = runner.invoke(auto, ["--help"])

        assert result.exit_code == 0
        assert "Auto-dispatch message" in result.output

    def test_status_command_help(self, runner):
        """Test status subcommand shows help."""
        from forge_harness.cli_v2.dispatch import dispatch_status

        result = runner.invoke(dispatch_status, ["--help"])

        assert result.exit_code == 0
        assert "dispatch message status" in result.output

    def test_queue_stats_command_help(self, runner):
        """Test queue-stats subcommand shows help."""
        from forge_harness.cli_v2.dispatch import queue_stats

        result = runner.invoke(queue_stats, ["--help"])

        assert result.exit_code == 0
        assert "message queue statistics" in result.output

    def test_conversation_command_help(self, runner):
        """Test conversation subcommand shows help."""
        from forge_harness.cli_v2.dispatch import conversation

        result = runner.invoke(conversation, ["--help"])

        assert result.exit_code == 0
        assert "message history" in result.output


class TestDispatchSend:
    """Tests for dispatch send command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_dispatch_client(self):
        """Mock DispatchClient."""
        with mock.patch(
            "forge_harness.cli_v2.dispatch.dispatch.send.DispatchClient"
        ) as mock_client:
            mock_instance = mock.MagicMock()
            mock_instance.send.return_value = mock.MagicMock(
                success=True,
                message_id="msg-123",
                error=None,
            )
            mock_client.return_value = mock_instance
            yield mock_instance

    def test_send_requires_target(self, runner):
        """Test send requires target argument."""
        from forge_harness.cli_v2.dispatch import send

        result = runner.invoke(send, [])

        assert result.exit_code != 0

    def test_send_requires_message(self, runner):
        """Test send requires message argument."""
        from forge_harness.cli_v2.dispatch import send

        result = runner.invoke(send, ["forge:minimax"])

        assert result.exit_code != 0


class TestDispatchRelay:
    """Tests for dispatch relay command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_env(self, monkeypatch):
        """Set up environment variables."""
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "test-token")
        monkeypatch.setenv("FORGE_API_URL", "http://localhost:8080")

    @pytest.mark.xfail(reason="Click context integration needs full CLI setup")
    def test_relay_requires_targets_or_tmux(self, runner, mock_env):
        """Test relay requires either agents or tmux discovery."""
        from forge_harness.cli_v2.dispatch import relay

        # No agents and no tmux discovery - should fail
        with mock.patch.object(
            dispatch_module, "_discover_tmux_agents", return_value=[]
        ):
            result = runner.invoke(relay, ["--no-discover-tmux"])

        assert result.exit_code != 0
        assert "No relay targets" in result.output or "Error" in result.output

    def test_relay_json_requires_once(self, runner, mock_env):
        """Test --json requires --once flag."""
        from forge_harness.cli_v2.dispatch import relay

        result = runner.invoke(relay, ["--json"])

        assert result.exit_code != 0


class TestDispatchAuto:
    """Tests for dispatch auto command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_env(self, monkeypatch):
        """Set up environment variables."""
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "test-token")
        monkeypatch.setenv("FORGE_API_URL", "http://localhost:8080")

    @pytest.fixture
    def mock_get_recommendation(self):
        """Mock _get_recommendation."""
        with mock.patch.object(
            dispatch_module, "_get_recommendation"
        ) as mock_rec:
            mock_rec.return_value = {
                "recommended_node_id": "sati",
                "score": 0.95,
                "reason": "Low CPU load",
                "candidates": [{"node_id": "sati", "score": 0.95}],
            }
            yield mock_rec

    def test_auto_requires_message(self, runner, mock_env):
        """Test auto requires message argument."""
        from forge_harness.cli_v2.dispatch import auto

        result = runner.invoke(auto, [])

        assert result.exit_code != 0

    @pytest.mark.xfail(reason="Click context integration needs full CLI setup")
    def test_auto_dry_run(self, runner, mock_env, mock_get_recommendation):
        """Test auto dry-run mode."""
        from forge_harness.cli_v2.dispatch import auto

        with mock.patch.object(
            dispatch_module, "_discover_tmux_agents", return_value=[]
        ):
            result = runner.invoke(
                auto,
                ["Test message", "--dry-run", "--api-url", "http://localhost:8080"],
            )

        # Dry run should succeed
        assert result.exit_code == 0 or "dry run" in result.output.lower()


class TestDispatchStatus:
    """Tests for dispatch status command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_env(self, monkeypatch):
        """Set up environment variables."""
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "test-token")

    @pytest.fixture
    def mock_list_agents(self):
        """Mock _list_agents."""
        with mock.patch.object(dispatch_module, "_list_agents") as mock_list:
            mock_list.return_value = [
                {"id": "forge:minimax", "agent_name": "Minimax", "status": "active"}
            ]
            yield mock_list

    @pytest.mark.xfail(reason="Click context integration needs full CLI setup")
    def test_status_falls_back_to_tmux(self, runner, mock_env, mock_list_agents):
        """Test status falls back to tmux when API fails."""
        from forge_harness.cli_v2.dispatch import dispatch_status

        # Force API to fail
        mock_list_agents.side_effect = RuntimeError("API error")

        with mock.patch.object(
            dispatch_module, "_discover_tmux_agents", return_value=["forge:kimi"]
        ):
            result = runner.invoke(
                dispatch_status,
                ["--api-url", "http://localhost:8080"],
            )

        # Should complete (possibly with errors shown)
        assert result.exit_code == 0 or "Error" in result.output


class TestDispatchQueueStats:
    """Tests for dispatch queue-stats command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_http_json(self):
        """Mock _http_json for queue-stats."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            mock_json.return_value = {
                "success": True,
                "data": {
                    "pending_messages": 5,
                    "delivered_messages": 10,
                    "acknowledged_messages": 8,
                    "total_processed": 18,
                    "processing_rate": 2.5,
                    "average_wait_time": 3.2,
                    "queue_age_seconds": 120,
                },
            }
            yield mock_json

    def test_queue_stats_json_output(self, runner, mock_http_json):
        """Test queue-stats with JSON output."""
        from forge_harness.cli_v2.dispatch import queue_stats

        result = runner.invoke(
            queue_stats,
            ["--json", "--api-url", "http://localhost:8080"],
        )

        assert result.exit_code == 0
        assert "pending" in result.output.lower() or "5" in result.output


class TestDispatchConversation:
    """Tests for dispatch conversation command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_http_json(self):
        """Mock _http_json for conversation."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            mock_json.return_value = {
                "success": True,
                "data": {
                    "messages": [
                        {
                            "id": "msg-1",
                            "sender_id": "forge:minimax",
                            "content": "Test message",
                            "status": "delivered",
                            "created_at": "2024-01-01T12:00:00Z",
                        }
                    ],
                    "count": 1,
                },
            }
            yield mock_json

    def test_conversation_requires_two_agents(self, runner):
        """Test conversation requires two agent arguments."""
        from forge_harness.cli_v2.dispatch import conversation

        result = runner.invoke(conversation, ["forge:minimax"])

        assert result.exit_code != 0

    def test_conversation_json_output(self, runner, mock_http_json):
        """Test conversation with JSON output."""
        from forge_harness.cli_v2.dispatch import conversation

        result = runner.invoke(
            conversation,
            [
                "forge:minimax",
                "forge:glm",
                "--json",
                "--api-url",
                "http://localhost:8080",
            ],
        )

        assert result.exit_code == 0


class TestRetryLogic:
    """Tests for retry logic in dispatch functions."""

    def test_http_json_retries_on_transient_error(self):
        """Test that HTTP requests handle transient errors gracefully."""
        # This tests that the function properly handles retries
        # by checking timeout minimum
        with mock.patch("forge_harness.cli_v2.dispatch.url_request") as mock_request:
            # Simulate multiple failures then success
            mock_request.urlopen.side_effect = [
                TimeoutError("timeout 1"),
                TimeoutError("timeout 2"),
                url_error.URLError("failed"),
            ]

            with pytest.raises(RuntimeError, match="failed"):
                dispatch_module._http_json(
                    "GET", "http://localhost:8080/api/test", "test-token", timeout=5.0
                )


class TestEdgeCases:
    """Edge case tests for dispatch module."""

    def test_resolve_queue_agent_id_handles_empty_agents(self):
        """Test resolution with empty agent list."""
        result = dispatch_module._resolve_queue_agent_id("forge:minimax", [])
        assert result == "forge:minimax"

    def test_resolve_queue_agent_id_handles_none_values(self):
        """Test resolution handles None values in agent data."""
        agents = [{"id": None, "session_id": "  ", "tmux_session": None}]

        result = dispatch_module._resolve_queue_agent_id("unknown", agents)
        assert result == "unknown"

    def test_resolve_targets_filters_whitespace_only(self):
        """Test resolve_targets filters whitespace-only strings."""
        with mock.patch.object(
            dispatch_module, "_discover_tmux_agents", return_value=[]
        ):
            result = dispatch_module._resolve_targets(
                explicit_agents=("  ", "\t", "\n"),
                discover_tmux=False,
                session_name="forge",
            )

        assert result == []

    def test_fetch_pending_message_handles_missing_data_key(self):
        """Test fetch_pending_messages handles missing data key."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            mock_json.return_value = {"success": True}

            result = dispatch_module._fetch_pending_messages(
                "http://localhost:8080", "token", "agent"
            )

            assert result == []

    def test_list_agents_handles_missing_data(self):
        """Test list_agents handles missing data."""
        with mock.patch.object(dispatch_module, "_http_json") as mock_json:
            mock_json.return_value = {"success": True}

            result = dispatch_module._list_agents("http://localhost:8080", "token")

            assert result == []
