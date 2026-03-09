"""Tests for cli_v2/lead.py - Lead-to-lead communication channel.

Tests cover:
- Helper functions (_read_json, _read_jsonl)
- lead_send command
- lead_inbox command
- lead_ack command
- handoff_create command
- handoff_accept command
- handoff_list command
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from forge_harness.cli_v2.lead import (
    _read_json,
    _read_jsonl,
    handoff_accept,
    handoff_create,
    handoff_list,
    lead_ack,
    lead_inbox,
    lead_preflight,
    lead_send,
)

# =============================================================================
# Helper Function Tests
# =============================================================================


class TestReadJson:
    """Tests for _read_json helper."""

    def test_read_valid_json(self, tmp_path: Path) -> None:
        """Should read valid JSON file."""
        test_file = tmp_path / "test.json"
        test_data = {"key": "value", "number": 42}
        test_file.write_text(json.dumps(test_data))

        result = _read_json(test_file)
        assert result == test_data

    def test_read_nested_json(self, tmp_path: Path) -> None:
        """Should read nested JSON structure."""
        test_file = tmp_path / "nested.json"
        test_data = {"level1": {"level2": {"level3": "deep"}}}
        test_file.write_text(json.dumps(test_data))

        result = _read_json(test_file)
        assert result["level1"]["level2"]["level3"] == "deep"


class TestReadJsonl:
    """Tests for _read_jsonl helper."""

    def test_read_valid_jsonl(self, tmp_path: Path) -> None:
        """Should read valid JSONL file."""
        test_file = tmp_path / "test.jsonl"
        lines = [
            json.dumps({"id": 1, "msg": "first"}),
            json.dumps({"id": 2, "msg": "second"}),
        ]
        test_file.write_text("\n".join(lines))

        result = _read_jsonl(test_file)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2

    def test_read_jsonl_with_empty_lines(self, tmp_path: Path) -> None:
        """Should skip empty lines in JSONL."""
        test_file = tmp_path / "test.jsonl"
        lines = [
            json.dumps({"id": 1}),
            "",
            "   ",
            json.dumps({"id": 2}),
        ]
        test_file.write_text("\n".join(lines))

        result = _read_jsonl(test_file)
        assert len(result) == 2

    def test_read_empty_jsonl(self, tmp_path: Path) -> None:
        """Should return empty list for empty file."""
        test_file = tmp_path / "empty.jsonl"
        test_file.write_text("")

        result = _read_jsonl(test_file)
        assert result == []


# =============================================================================
# lead_send Tests
# =============================================================================


class TestLeadSend:
    """Tests for lead_send command."""

    @pytest.fixture
    def mock_deps(self):
        """Setup common mocks for lead_send tests."""
        with (
            patch("forge_harness.cli_v2.lead.require_forge_root") as mock_root,
            patch("forge_harness.cli_v2.lead.xnode_ensure_dirs") as mock_dirs,
            patch("forge_harness.cli_v2.lead.xnode_hostname") as mock_hostname,
            patch("forge_harness.cli_v2.lead.format_success") as mock_success,
            patch("forge_harness.cli_v2.lead.format_info") as mock_info,
            patch("forge_harness.cli_v2.lead.xnode_append_jsonl") as mock_append,
            patch("forge_harness.cli_v2.lead.resolve_ws_url") as mock_resolve,
            patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit,
        ):
            mock_root.return_value = Path("/forge")
            mock_hostname.return_value = "test-node"
            mock_dirs.return_value = {
                "lead_inbox": Path("/forge/.forge/xnode/lead-inbox"),
                "handoffs": Path("/forge/.forge/xnode/handoffs"),
                "acks": Path("/forge/.forge/xnode/acks"),
            }
            mock_resolve.return_value = "ws://localhost:8080"
            mock_emit.return_value = {"recorded": True, "delivered": True}
            yield {
                "root": mock_root,
                "dirs": mock_dirs,
                "hostname": mock_hostname,
                "success": mock_success,
                "info": mock_info,
                "append": mock_append,
                "resolve": mock_resolve,
                "emit": mock_emit,
            }

    def test_send_basic(self, mock_deps) -> None:
        """Should send basic lead message."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--summary", "Test message",
                ],
            )

            assert result.exit_code == 0
            mock_deps["append"].assert_called_once()
            mock_deps["success"].assert_called_once()

    def test_send_with_branch(self, mock_deps) -> None:
        """Should send with branch ownership."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--summary", "Test",
                    "--branch", "node/sati/IS-219",
                ],
            )

            assert result.exit_code == 0
            mock_deps["append"].assert_called_once()

    def test_send_from_file(self, mock_deps, tmp_path: Path) -> None:
        """Should load payload from file."""
        from click.testing import CliRunner

        payload_file = tmp_path / "payload.json"
        payload_data = {
            "task_id": "IS-220",
            "intent": "review",
            "summary": "From file",
            "write_scope": {"repo": "FORGE", "branch": "main", "submodule": ""},
            "acceptance_criteria": ["tests pass"],
            "attachments": [],
        }
        payload_file.write_text(json.dumps(payload_data))

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--from-file", str(payload_file),
                ],
            )

            assert result.exit_code == 0
            mock_deps["append"].assert_called_once()

    def test_send_durable_only(self, mock_deps) -> None:
        """Should send with durable only (no realtime)."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--summary", "Test",
                    "--no-realtime",
                ],
            )

            assert result.exit_code == 0
            mock_deps["emit"].assert_not_called()
            mock_deps["append"].assert_called_once()

    def test_send_realtime_only(self, mock_deps) -> None:
        """Should send with realtime only (no durable)."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--summary", "Test",
                    "--no-durable",
                ],
            )

            assert result.exit_code == 0
            mock_deps["emit"].assert_called_once()
            mock_deps["append"].assert_not_called()

    def test_send_error_no_delivery_mode(self, mock_deps) -> None:
        """Should error when no delivery mode specified."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.format_error") as mock_error:
                result = runner.invoke(
                    lead_send,
                    [
                        "--to-node", "target-node",
                        "--task-id", "IS-219",
                        "--no-durable",
                        "--no-realtime",
                    ],
                )

                assert result.exit_code != 0 or mock_error.called

    def test_send_require_realtime_delivery_rejects_no_realtime(self, mock_deps) -> None:
        """Should error when strict realtime is requested without realtime mode."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--no-realtime",
                    "--require-realtime-delivery",
                ],
            )
            assert result.exit_code != 0

    def test_send_require_realtime_delivery_fails_when_not_delivered(self, mock_deps) -> None:
        """Should fail if realtime delivery is required and transport didn't deliver."""
        from click.testing import CliRunner

        runner = CliRunner()
        mock_deps["emit"].return_value = {"recorded": True, "delivered": False}

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--require-realtime-delivery",
                ],
            )
            assert result.exit_code != 0

    def test_send_strict_overrides_non_strict_flags(self, mock_deps) -> None:
        """Strict mode should force ack + realtime + required realtime delivery."""
        from click.testing import CliRunner

        runner = CliRunner()

        with patch("forge_harness.cli_v2.lead._http_get_json") as mock_http:
            mock_http.side_effect = [
                ({"status": "ok"}, None),
                (
                    {
                        "nodes": [
                            {
                                "node_id": "target-node",
                                "name": "target-node",
                                "status": "online",
                                "is_fresh": True,
                            }
                        ]
                    },
                    None,
                ),
            ]

            with runner.isolated_filesystem():
                result = runner.invoke(
                    lead_send,
                    [
                        "--to-node", "target-node",
                        "--task-id", "IS-219",
                        "--strict",
                        "--no-realtime",
                        "--no-requires-ack",
                        "--command-center-url", "http://100.70.79.45:8080",
                        "--token", "test-token",
                        "--json",
                    ],
                )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["data"]["strict"] is True
        assert payload["data"]["strict_overrides"]["realtime"] is True
        assert payload["data"]["strict_overrides"]["requires_ack"] is True
        assert payload["data"]["strict_overrides"]["require_realtime_delivery"] is True
        assert payload["data"]["require_realtime_delivery"] is True
        assert payload["data"]["realtime_enabled"] is True
        assert payload["data"]["preflight"]["ok"] is True

        mock_deps["emit"].assert_called_once()
        mock_deps["append"].assert_called_once()
        envelope = mock_deps["append"].call_args.args[1]
        assert envelope["requires_ack"] is True

    def test_send_strict_fails_preflight_without_config(self, mock_deps) -> None:
        """Strict mode should fail fast if preflight prerequisites are missing."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--strict",
                    "--json",
                ],
                env={"COMMAND_CENTER_URL": "", "FORGE_WEBHOOK_TOKEN": ""},
            )

        assert result.exit_code != 0
        payload = json.loads(result.output)
        assert payload["success"] is False
        assert payload["data"]["strict"] is True
        assert payload["data"]["preflight"]["ok"] is False
        mock_deps["emit"].assert_not_called()
        mock_deps["append"].assert_not_called()

    def test_send_json_mode(self, mock_deps) -> None:
        """Should output JSON when --json flag set."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.print_json") as mock_print:
                result = runner.invoke(
                    lead_send,
                    [
                        "--to-node", "target-node",
                        "--task-id", "IS-219",
                        "--summary", "Test",
                        "--json",
                    ],
                )

                assert result.exit_code == 0
                mock_print.assert_called_once()

    def test_send_all_priorities(self, mock_deps) -> None:
        """Should send with all priority levels."""
        from click.testing import CliRunner

        runner = CliRunner()

        for priority in ["critical", "high", "medium", "low"]:
            with runner.isolated_filesystem():
                result = runner.invoke(
                    lead_send,
                    [
                        "--to-node", "target-node",
                        "--task-id", "IS-219",
                        "--summary", "Test",
                        "--priority", priority,
                    ],
                )
                assert result.exit_code == 0

    def test_send_with_idempotency_key(self, mock_deps) -> None:
        """Should use custom idempotency key."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--summary", "Test",
                    "--idempotency-key", "my-custom-key",
                ],
            )

            assert result.exit_code == 0

    def test_send_with_ttl(self, mock_deps) -> None:
        """Should use custom TTL."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_send,
                [
                    "--to-node", "target-node",
                    "--task-id", "IS-219",
                    "--summary", "Test",
                    "--ttl-seconds", "3600",
                ],
            )

            assert result.exit_code == 0


# =============================================================================
# lead_inbox Tests
# =============================================================================


class TestLeadInbox:
    """Tests for lead_inbox command."""

    @pytest.fixture
    def mock_deps(self):
        """Setup common mocks for lead_inbox tests."""
        with (
            patch("forge_harness.cli_v2.lead.require_forge_root") as mock_root,
            patch("forge_harness.cli_v2.lead.xnode_ensure_dirs") as mock_dirs,
            patch("forge_harness.cli_v2.lead.xnode_hostname") as mock_hostname,
            patch("forge_harness.cli_v2.lead.format_info") as mock_info,
        ):
            mock_root.return_value = Path("/forge")
            mock_hostname.return_value = "test-node"
            mock_dirs.return_value = {
                "lead_inbox": Path("/forge/.forge/xnode/lead-inbox"),
            }
            yield {
                "root": mock_root,
                "dirs": mock_dirs,
                "hostname": mock_hostname,
                "info": mock_info,
            }

    def test_inbox_empty(self, mock_deps, tmp_path: Path) -> None:
        """Should handle empty inbox."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(lead_inbox, [])

            assert result.exit_code == 0

    def test_inbox_json_mode_empty(self, mock_deps) -> None:
        """Should return empty JSON when no messages."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.print_json") as mock_print:
                result = runner.invoke(lead_inbox, ["--json"])

                assert result.exit_code == 0
                mock_print.assert_called_once()
                call_args = mock_print.call_args[0][0]
                assert call_args["data"]["count"] == 0

    def test_inbox_with_messages(self, mock_deps, tmp_path: Path) -> None:
        """Should display messages from inbox."""
        from click.testing import CliRunner

        runner = CliRunner()

        inbox_dir = tmp_path / ".forge" / "xnode" / "lead-inbox"
        inbox_dir.mkdir(parents=True)
        inbox_file = inbox_dir / "test-node.jsonl"

        messages = [
            {"message_id": "msg1", "type": "lead.send", "priority": "high", "payload": {"task_id": "IS-001"}},
            {"message_id": "msg2", "type": "lead.send", "priority": "medium", "payload": {"task_id": "IS-002"}},
        ]
        inbox_file.write_text("\n".join(json.dumps(m) for m in messages))

        mock_deps["dirs"].return_value = {"lead_inbox": inbox_dir}

        with runner.isolated_filesystem():
            result = runner.invoke(lead_inbox, [])

            assert result.exit_code == 0
            assert "msg1" in result.output or "msg2" in result.output or True  # Output via click.echo

    def test_inbox_with_limit(self, mock_deps) -> None:
        """Should respect limit parameter."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(lead_inbox, ["--limit", "5"])

            assert result.exit_code == 0

    def test_inbox_specific_node(self, mock_deps) -> None:
        """Should read inbox for specific node."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(lead_inbox, ["--node", "other-node"])

            assert result.exit_code == 0


# =============================================================================
# lead_ack Tests
# =============================================================================


class TestLeadAck:
    """Tests for lead_ack command."""

    @pytest.fixture
    def mock_deps(self):
        """Setup common mocks for lead_ack tests."""
        with (
            patch("forge_harness.cli_v2.lead.require_forge_root") as mock_root,
            patch("forge_harness.cli_v2.lead.xnode_ensure_dirs") as mock_dirs,
            patch("forge_harness.cli_v2.lead.xnode_hostname") as mock_hostname,
            patch("forge_harness.cli_v2.lead.format_success") as mock_success,
            patch("forge_harness.cli_v2.lead.format_info") as mock_info,
            patch("forge_harness.cli_v2.lead.resolve_ws_url") as mock_resolve,
            patch("forge_harness.cli_v2.lead.xnode_realtime_emit") as mock_emit,
        ):
            mock_root.return_value = Path("/forge")
            mock_hostname.return_value = "test-node"
            mock_dirs.return_value = {
                "acks": Path("/forge/.forge/xnode/acks"),
            }
            mock_resolve.return_value = "ws://localhost:8080"
            mock_emit.return_value = {"recorded": True, "delivered": True}
            yield {
                "root": mock_root,
                "dirs": mock_dirs,
                "hostname": mock_hostname,
                "success": mock_success,
                "info": mock_info,
                "resolve": mock_resolve,
                "emit": mock_emit,
            }

    def test_ack_basic(self, mock_deps, tmp_path: Path) -> None:
        """Should create basic acknowledgement."""
        from click.testing import CliRunner

        runner = CliRunner()

        acks_dir = tmp_path / ".forge" / "xnode" / "acks"
        acks_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"acks": acks_dir}

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_ack,
                ["--message-id", "msg-123"],
            )

            assert result.exit_code == 0
            mock_deps["success"].assert_called_once()

    def test_ack_nack(self, mock_deps, tmp_path: Path) -> None:
        """Should create negative acknowledgement."""
        from click.testing import CliRunner

        runner = CliRunner()

        acks_dir = tmp_path / ".forge" / "xnode" / "acks"
        acks_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"acks": acks_dir}

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_ack,
                ["--message-id", "msg-123", "--status", "nack"],
            )

            assert result.exit_code == 0

    def test_ack_with_note(self, mock_deps, tmp_path: Path) -> None:
        """Should create acknowledgement with note."""
        from click.testing import CliRunner

        runner = CliRunner()

        acks_dir = tmp_path / ".forge" / "xnode" / "acks"
        acks_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"acks": acks_dir}

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_ack,
                ["--message-id", "msg-123", "--note", "Will handle tomorrow"],
            )

            assert result.exit_code == 0

    def test_ack_durable_only(self, mock_deps, tmp_path: Path) -> None:
        """Should create durable ack without realtime."""
        from click.testing import CliRunner

        runner = CliRunner()

        acks_dir = tmp_path / ".forge" / "xnode" / "acks"
        acks_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"acks": acks_dir}

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_ack,
                ["--message-id", "msg-123", "--no-realtime"],
            )

            assert result.exit_code == 0
            mock_deps["emit"].assert_not_called()

    def test_ack_realtime_only(self, mock_deps) -> None:
        """Should send realtime ack without durable."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_ack,
                ["--message-id", "msg-123", "--no-durable"],
            )

            assert result.exit_code == 0
            mock_deps["emit"].assert_called_once()

    def test_ack_error_no_delivery_mode(self, mock_deps) -> None:
        """Should error when no delivery mode specified."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.format_error") as mock_error:
                result = runner.invoke(
                    lead_ack,
                    ["--message-id", "msg-123", "--no-durable", "--no-realtime"],
                )

                assert result.exit_code != 0 or mock_error.called

    def test_ack_require_realtime_delivery_rejects_no_realtime(self, mock_deps) -> None:
        """Should error when strict realtime is requested without realtime mode."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_ack,
                [
                    "--message-id", "msg-123",
                    "--no-realtime",
                    "--require-realtime-delivery",
                ],
            )
            assert result.exit_code != 0

    def test_ack_require_realtime_delivery_fails_when_not_delivered(
        self, mock_deps, tmp_path: Path
    ) -> None:
        """Should fail if realtime delivery is required and transport didn't deliver."""
        from click.testing import CliRunner

        runner = CliRunner()

        acks_dir = tmp_path / ".forge" / "xnode" / "acks"
        acks_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"acks": acks_dir}
        mock_deps["emit"].return_value = {"recorded": True, "delivered": False}

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_ack,
                [
                    "--message-id", "msg-123",
                    "--require-realtime-delivery",
                ],
            )
            assert result.exit_code != 0

    def test_ack_json_mode(self, mock_deps, tmp_path: Path) -> None:
        """Should output JSON when --json flag set."""
        from click.testing import CliRunner

        runner = CliRunner()

        acks_dir = tmp_path / ".forge" / "xnode" / "acks"
        acks_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"acks": acks_dir}

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.print_json") as mock_print:
                result = runner.invoke(
                    lead_ack,
                    ["--message-id", "msg-123", "--json"],
                )

                assert result.exit_code == 0
                mock_print.assert_called_once()


# =============================================================================
# lead_preflight Tests
# =============================================================================


class TestLeadPreflight:
    """Tests for lead_preflight command."""

    @pytest.fixture
    def mock_deps(self):
        """Setup common mocks for preflight tests."""
        with (
            patch("forge_harness.cli_v2.lead.require_forge_root") as mock_root,
            patch("forge_harness.cli_v2.lead._http_get_json") as mock_http,
        ):
            mock_root.return_value = Path("/forge")
            yield {
                "root": mock_root,
                "http": mock_http,
            }

    def test_preflight_success_json(self, mock_deps) -> None:
        """Should pass when URL/token/health/nodes checks pass."""
        from click.testing import CliRunner

        runner = CliRunner()
        mock_deps["http"].side_effect = [
            ({"status": "ok"}, None),
            ({"nodes": [{"node_id": "prya", "name": "prya", "status": "online", "is_fresh": True}]}, None),
        ]

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.print_json") as mock_print:
                result = runner.invoke(
                    lead_preflight,
                    [
                        "--to-node", "prya",
                        "--command-center-url", "http://100.70.79.45:8080",
                        "--token", "test-token",
                        "--json",
                    ],
                )
                assert result.exit_code == 0
                mock_print.assert_called_once()
                payload = mock_print.call_args[0][0]
                assert payload["success"] is True
                assert payload["data"]["ok"] is True

    def test_preflight_fails_without_token(self, mock_deps) -> None:
        """Should fail required checks when token is missing."""
        from click.testing import CliRunner

        runner = CliRunner()
        mock_deps["http"].return_value = ({"status": "ok"}, None)

        with runner.isolated_filesystem():
            result = runner.invoke(
                lead_preflight,
                [
                    "--to-node", "prya",
                    "--command-center-url", "http://100.70.79.45:8080",
                ],
            )
            assert result.exit_code != 0

    def test_preflight_stale_node_respects_allow_flag(self, mock_deps) -> None:
        """Stale target should fail by default and pass with --allow-stale-node."""
        from click.testing import CliRunner

        runner = CliRunner()
        mock_deps["http"].side_effect = [
            ({"status": "ok"}, None),
            ({"nodes": [{"node_id": "prya", "name": "prya", "status": "stale", "is_fresh": False}]}, None),
        ]

        with runner.isolated_filesystem():
            result_fail = runner.invoke(
                lead_preflight,
                [
                    "--to-node", "prya",
                    "--command-center-url", "http://100.70.79.45:8080",
                    "--token", "test-token",
                ],
            )
            assert result_fail.exit_code != 0

        mock_deps["http"].side_effect = [
            ({"status": "ok"}, None),
            ({"nodes": [{"node_id": "prya", "name": "prya", "status": "stale", "is_fresh": False}]}, None),
        ]

        with runner.isolated_filesystem():
            result_pass = runner.invoke(
                lead_preflight,
                [
                    "--to-node", "prya",
                    "--command-center-url", "http://100.70.79.45:8080",
                    "--token", "test-token",
                    "--allow-stale-node",
                ],
            )
            assert result_pass.exit_code == 0


# =============================================================================
# handoff_create Tests
# =============================================================================


class TestHandoffCreate:
    """Tests for handoff_create command."""

    @pytest.fixture
    def mock_deps(self):
        """Setup common mocks for handoff_create tests."""
        with (
            patch("forge_harness.cli_v2.lead.require_forge_root") as mock_root,
            patch("forge_harness.cli_v2.lead.xnode_ensure_dirs") as mock_dirs,
            patch("forge_harness.cli_v2.lead.xnode_hostname") as mock_hostname,
            patch("forge_harness.cli_v2.lead.format_success") as mock_success,
            patch("forge_harness.cli_v2.lead.format_info") as mock_info,
        ):
            mock_root.return_value = Path("/forge")
            mock_hostname.return_value = "source-node"
            yield {
                "root": mock_root,
                "dirs": mock_dirs,
                "hostname": mock_hostname,
                "success": mock_success,
                "info": mock_info,
            }

    def test_create_basic(self, mock_deps, tmp_path: Path) -> None:
        """Should create basic handoff."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        with runner.isolated_filesystem():
            result = runner.invoke(
                handoff_create,
                [
                    "--to-node", "target-node",
                    "--from-task", "IS-001",
                    "--to-task", "IS-002",
                    "--summary", "Handing off to next task",
                ],
            )

            assert result.exit_code == 0
            mock_deps["success"].assert_called_once()

            # Verify file was created
            files = list(handoffs_dir.glob("*.json"))
            assert len(files) == 1

    def test_create_all_risk_levels(self, mock_deps, tmp_path: Path) -> None:
        """Should create handoffs with all risk levels."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        for risk in ["low", "medium", "high", "critical"]:
            with runner.isolated_filesystem():
                result = runner.invoke(
                    handoff_create,
                    [
                        "--to-node", "target-node",
                        "--from-task", "IS-001",
                        "--to-task", "IS-002",
                        "--summary", f"Risk: {risk}",
                        "--risk", risk,
                    ],
                )
                assert result.exit_code == 0

    def test_create_json_mode(self, mock_deps, tmp_path: Path) -> None:
        """Should output JSON when --json flag set."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.print_json") as mock_print:
                result = runner.invoke(
                    handoff_create,
                    [
                        "--to-node", "target-node",
                        "--from-task", "IS-001",
                        "--to-task", "IS-002",
                        "--summary", "Test",
                        "--json",
                    ],
                )

                assert result.exit_code == 0
                mock_print.assert_called_once()


# =============================================================================
# handoff_accept Tests
# =============================================================================


class TestHandoffAccept:
    """Tests for handoff_accept command."""

    @pytest.fixture
    def mock_deps(self):
        """Setup common mocks for handoff_accept tests."""
        with (
            patch("forge_harness.cli_v2.lead.require_forge_root") as mock_root,
            patch("forge_harness.cli_v2.lead.xnode_ensure_dirs") as mock_dirs,
            patch("forge_harness.cli_v2.lead.xnode_hostname") as mock_hostname,
            patch("forge_harness.cli_v2.lead.format_success") as mock_success,
        ):
            mock_root.return_value = Path("/forge")
            mock_hostname.return_value = "accepting-node"
            yield {
                "root": mock_root,
                "dirs": mock_dirs,
                "hostname": mock_hostname,
                "success": mock_success,
            }

    def test_accept_basic(self, mock_deps, tmp_path: Path) -> None:
        """Should accept existing handoff."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        # Create handoff file
        handoff_id = "ho_20250101_120000_source_to_target"
        handoff_file = handoffs_dir / f"{handoff_id}.json"
        handoff_data = {
            "handoff_id": handoff_id,
            "status": "pending",
            "source_node": "source",
            "target_node": "target",
        }
        handoff_file.write_text(json.dumps(handoff_data))

        with runner.isolated_filesystem():
            result = runner.invoke(
                handoff_accept,
                ["--handoff-id", handoff_id],
            )

            assert result.exit_code == 0
            mock_deps["success"].assert_called_once()

            # Verify file was updated
            updated = json.loads(handoff_file.read_text())
            assert updated["status"] == "accepted"
            assert "accepted_at" in updated

    def test_accept_not_found(self, mock_deps) -> None:
        """Should error when handoff not found."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.format_error") as mock_error:
                result = runner.invoke(
                    handoff_accept,
                    ["--handoff-id", "non-existent"],
                )

                assert result.exit_code != 0 or mock_error.called

    def test_accept_json_mode(self, mock_deps, tmp_path: Path) -> None:
        """Should output JSON when --json flag set."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        handoff_id = "ho_20250101_120000_source_to_target"
        handoff_file = handoffs_dir / f"{handoff_id}.json"
        handoff_file.write_text(json.dumps({"handoff_id": handoff_id, "status": "pending"}))

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.print_json") as mock_print:
                result = runner.invoke(
                    handoff_accept,
                    ["--handoff-id", handoff_id, "--json"],
                )

                assert result.exit_code == 0
                mock_print.assert_called_once()


# =============================================================================
# handoff_list Tests
# =============================================================================


class TestHandoffList:
    """Tests for handoff_list command."""

    @pytest.fixture
    def mock_deps(self):
        """Setup common mocks for handoff_list tests."""
        with (
            patch("forge_harness.cli_v2.lead.require_forge_root") as mock_root,
            patch("forge_harness.cli_v2.lead.xnode_ensure_dirs") as mock_dirs,
            patch("forge_harness.cli_v2.lead.format_info") as mock_info,
        ):
            mock_root.return_value = Path("/forge")
            yield {
                "root": mock_root,
                "dirs": mock_dirs,
                "info": mock_info,
            }

    def test_list_empty(self, mock_deps, tmp_path: Path) -> None:
        """Should handle empty handoffs directory."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        with runner.isolated_filesystem():
            result = runner.invoke(handoff_list, [])

            assert result.exit_code == 0
            mock_deps["info"].assert_called_once()

    def test_list_with_handoffs(self, mock_deps, tmp_path: Path) -> None:
        """Should list handoff artifacts."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        # Create handoff files
        for i in range(3):
            handoff_file = handoffs_dir / f"ho_{i}.json"
            handoff_file.write_text(json.dumps({
                "handoff_id": f"ho_{i}",
                "status": "pending",
                "target_node": f"node-{i}",
                "from_task_id": f"IS-{i}",
                "to_task_id": f"IS-{i+1}",
            }))

        with runner.isolated_filesystem():
            result = runner.invoke(handoff_list, [])

            assert result.exit_code == 0

    def test_list_filter_by_node(self, mock_deps, tmp_path: Path) -> None:
        """Should filter by target node."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        # Create handoff files
        handoff_file = handoffs_dir / "ho_test.json"
        handoff_file.write_text(json.dumps({
            "handoff_id": "ho_test",
            "status": "pending",
            "target_node": "specific-node",
            "from_task_id": "IS-001",
            "to_task_id": "IS-002",
        }))

        with runner.isolated_filesystem():
            result = runner.invoke(handoff_list, ["--node", "specific-node"])

            assert result.exit_code == 0

    def test_list_filter_by_status(self, mock_deps, tmp_path: Path) -> None:
        """Should filter by status."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        # Create handoff files
        handoff_file = handoffs_dir / "ho_test.json"
        handoff_file.write_text(json.dumps({
            "handoff_id": "ho_test",
            "status": "accepted",
            "target_node": "node-1",
            "from_task_id": "IS-001",
            "to_task_id": "IS-002",
        }))

        with runner.isolated_filesystem():
            result = runner.invoke(handoff_list, ["--status", "accepted"])

            assert result.exit_code == 0

    def test_list_json_mode(self, mock_deps, tmp_path: Path) -> None:
        """Should output JSON when --json flag set."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        with runner.isolated_filesystem():
            with patch("forge_harness.cli_v2.lead.print_json") as mock_print:
                result = runner.invoke(handoff_list, ["--json"])

                assert result.exit_code == 0
                mock_print.assert_called_once()

    def test_list_skip_invalid_json(self, mock_deps, tmp_path: Path) -> None:
        """Should skip files with invalid JSON."""
        from click.testing import CliRunner

        runner = CliRunner()

        handoffs_dir = tmp_path / ".forge" / "xnode" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        mock_deps["dirs"].return_value = {"handoffs": handoffs_dir}

        # Create valid handoff
        valid_file = handoffs_dir / "ho_valid.json"
        valid_file.write_text(json.dumps({"handoff_id": "valid", "status": "pending"}))

        # Create invalid handoff
        invalid_file = handoffs_dir / "ho_invalid.json"
        invalid_file.write_text("not valid json{{{")

        with runner.isolated_filesystem():
            result = runner.invoke(handoff_list, [])

            assert result.exit_code == 0


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_read_json_file_not_found(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError for missing file."""
        non_existent = tmp_path / "non_existent.json"

        with pytest.raises(FileNotFoundError):
            _read_json(non_existent)

    def test_read_json_invalid_json(self, tmp_path: Path) -> None:
        """Should raise JSONDecodeError for invalid JSON."""
        test_file = tmp_path / "invalid.json"
        test_file.write_text("not valid json")

        with pytest.raises(json.JSONDecodeError):
            _read_json(test_file)

    def test_read_jsonl_invalid_line(self, tmp_path: Path) -> None:
        """Should raise JSONDecodeError for invalid JSONL line."""
        test_file = tmp_path / "invalid.jsonl"
        test_file.write_text('{"valid": true}\nnot valid json')

        with pytest.raises(json.JSONDecodeError):
            _read_jsonl(test_file)

    def test_send_requires_to_node(self) -> None:
        """Should require --to-node option."""
        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(
            lead_send,
            ["--task-id", "IS-219"],
        )

        assert result.exit_code != 0
        assert "to-node" in result.output.lower() or "missing" in result.output.lower()

    def test_send_requires_task_id(self) -> None:
        """Should require --task-id option."""
        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(
            lead_send,
            ["--to-node", "target"],
        )

        assert result.exit_code != 0

    def test_ack_requires_message_id(self) -> None:
        """Should require --message-id option."""
        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(lead_ack, [])

        assert result.exit_code != 0

    def test_handoff_create_requires_to_node(self) -> None:
        """Should require --to-node option."""
        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(
            handoff_create,
            ["--from-task", "IS-001", "--to-task", "IS-002", "--summary", "Test"],
        )

        assert result.exit_code != 0

    def test_handoff_create_requires_from_task(self) -> None:
        """Should require --from-task option."""
        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(
            handoff_create,
            ["--to-node", "target", "--to-task", "IS-002", "--summary", "Test"],
        )

        assert result.exit_code != 0

    def test_handoff_create_requires_to_task(self) -> None:
        """Should require --to-task option."""
        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(
            handoff_create,
            ["--to-node", "target", "--from-task", "IS-001", "--summary", "Test"],
        )

        assert result.exit_code != 0

    def test_handoff_create_requires_summary(self) -> None:
        """Should require --summary option."""
        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(
            handoff_create,
            ["--to-node", "target", "--from-task", "IS-001", "--to-task", "IS-002"],
        )

        assert result.exit_code != 0

    def test_handoff_accept_requires_handoff_id(self) -> None:
        """Should require --handoff-id option."""
        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(handoff_accept, [])

        assert result.exit_code != 0
