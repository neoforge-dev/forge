"""Tests for forge dispatch command group."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from click.testing import CliRunner
from forge_harness.cli_v2 import cli


class TestDispatchCommand:
    def test_dispatch_command_callable(self):
        try:
            cli.main(["dispatch", "--help"])
        except SystemExit:
            pass


class TestDispatchHelpers:
    def test_discover_tmux_agents_parses_windows(self):
        from forge_harness.cli_v2.dispatch import _discover_tmux_agents

        with patch(
            "subprocess.run",
            return_value=Mock(returncode=0, stdout="tech\nreview\n", stderr=""),
        ):
            targets = _discover_tmux_agents("forge")

        assert targets == ["forge:tech", "forge:review"]

    def test_resolve_targets_deduplicates(self):
        from forge_harness.cli_v2.dispatch import _resolve_targets

        with patch(
            "forge_harness.cli_v2.dispatch._discover_tmux_agents",
            return_value=["forge:tech", "forge:review"],
        ):
            targets = _resolve_targets(
                explicit_agents=("forge:tech", "forge:ops"),
                discover_tmux=True,
                session_name="forge",
            )

        assert targets == ["forge:tech", "forge:ops", "forge:review"]

    def test_resolve_queue_agent_id_maps_tmux_target_to_registry_id(self):
        from forge_harness.cli_v2.dispatch import _resolve_queue_agent_id

        agents = [
            {"id": "ag_123", "tmux_session": "forge:tech", "session_id": "ag_123"},
            {"id": "ag_456", "tmux_session": "forge:review", "session_id": "ag_456"},
        ]

        resolved = _resolve_queue_agent_id("forge:tech", agents)
        assert resolved == "ag_123"

    def test_resolve_queue_agent_id_falls_back_to_target(self):
        from forge_harness.cli_v2.dispatch import _resolve_queue_agent_id

        resolved = _resolve_queue_agent_id("forge:missing", [])
        assert resolved == "forge:missing"

    def test_resolve_queue_agent_id_maps_from_window_metadata(self):
        from forge_harness.cli_v2.dispatch import _resolve_queue_agent_id

        agents = [
            {"session_id": "ag_123", "agent_name": "tech"},
            {"session_id": "forge:tech", "agent_name": "tech", "source": "tmux"},
        ]

        resolved = _resolve_queue_agent_id("forge:tech", agents)
        assert resolved == "ag_123"

    def test_resolve_queue_agent_id_uses_tmp_mapping_fallback(self):
        from forge_harness.cli_v2.dispatch import _resolve_queue_agent_id

        with patch(
            "forge_harness.cli_v2.dispatch._resolve_queue_agent_id_from_tmp",
            return_value="ag_tmp_42",
        ):
            resolved = _resolve_queue_agent_id("forge:ops", [])
        assert resolved == "ag_tmp_42"


class TestDispatchRelay:
    def test_relay_json_requires_once(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "dispatch",
                "relay",
                "--json",
                "--api-token",
                "test-token",
                "--agent",
                "forge:tech",
                "--no-discover-tmux",
            ],
        )

        assert result.exit_code != 0
        assert "only supported with --once" in result.output

    def test_relay_once_json_processes_pending_message(self):
        runner = CliRunner()

        pending = [{"id": "msg-1", "content": "Run task A"}]
        dispatch_result = SimpleNamespace(success=True, error=None)

        with patch(
            "forge_harness.cli_v2.dispatch._fetch_pending_messages",
            return_value=pending,
        ), patch(
            "forge_harness.cli_v2.dispatch._ack_message"
        ) as ack_mock, patch(
            "forge_harness.fleet.dispatch_client.DispatchClient.send",
            new=AsyncMock(return_value=dispatch_result),
        ):
            result = runner.invoke(
                cli,
                [
                    "dispatch",
                    "relay",
                    "--once",
                    "--json",
                    "--api-token",
                    "test-token",
                    "--agent",
                    "forge:tech",
                    "--no-discover-tmux",
                ],
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["data"]["pending_messages"] == 1
        assert payload["data"]["dispatched"] == 1
        assert payload["data"]["acked"] == 1
        ack_mock.assert_called_once_with(
            "http://localhost:8080", "test-token", "forge:tech", "msg-1"
        )
