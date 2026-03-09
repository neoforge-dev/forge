"""
Tests for forge nodes heartbeat
================================

Tests P1-5 from INTEGRATION_PLAN: nodes heartbeat CLI for per-node telemetry.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.nodes import collect_telemetry, nodes


@pytest.fixture
def cli_runner():
    """Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def forge_root(tmp_path):
    """Create minimal FORGE-like structure."""
    root = tmp_path / "FORGE"
    root.mkdir()
    (root / "domains.yaml").write_text("domains: {}")
    (root / ".forge" / "heartbeat").mkdir(parents=True)
    (root / ".forge" / "heartbeat" / "task_queue.json").write_text(
        '{"queue": [{"status": "pending"}, {"status": "completed"}]}'
    )
    return root


class TestNodesHeartbeat:
    """Tests for forge nodes heartbeat command."""

    def test_heartbeat_collects_telemetry(self, forge_root):
        """collect_telemetry returns expected structure."""
        with patch("forge_harness.cli_v2.nodes.require_forge_root", return_value=forge_root):
            telemetry = collect_telemetry(forge_root, "test-node")
        assert telemetry["node_id"] == "test-node"
        assert "timestamp" in telemetry
        assert "resources" in telemetry
        assert "cpu_usage_percent" in telemetry["resources"]
        assert "ram_available_mb" in telemetry["resources"]
        assert "ram_total_mb" in telemetry["resources"]
        assert "queue_depth" in telemetry["resources"]
        assert "current_task" in telemetry
        assert "capabilities" in telemetry
        assert "health" in telemetry

    def test_heartbeat_text_format(self, forge_root, cli_runner):
        """Heartbeat prints human-readable output by default."""
        from unittest.mock import Mock

        mock_resp = Mock()
        mock_resp.status_code = 200
        with patch("forge_harness.cli_v2.nodes.require_forge_root", return_value=forge_root):
            with patch("forge_harness.cli_v2.nodes.httpx") as mock_httpx:
                mock_httpx.Client.return_value.__enter__.return_value.post.return_value = mock_resp
                result = cli_runner.invoke(
                    nodes,
                    ["heartbeat"],
                    catch_exceptions=False,
                )
        assert result.exit_code == 0
        assert "Node heartbeat" in result.output or "CPU" in result.output

    def test_heartbeat_json_format(self, forge_root, cli_runner):
        """Heartbeat --format json outputs valid JSON."""
        import json
        from unittest.mock import Mock

        mock_resp = Mock()
        mock_resp.status_code = 200
        with patch("forge_harness.cli_v2.nodes.require_forge_root", return_value=forge_root):
            with patch("forge_harness.cli_v2.nodes.httpx") as mock_httpx:
                mock_httpx.Client.return_value.__enter__.return_value.post.return_value = mock_resp
                result = cli_runner.invoke(
                    nodes,
                    ["heartbeat", "--format", "json"],
                    catch_exceptions=False,
                )
        assert result.exit_code == 0
        envelope = json.loads(result.output.strip())
        telemetry = envelope["data"]["telemetry"]
        assert telemetry["node_id"]
        assert "resources" in telemetry
        assert "queue_depth" in telemetry["resources"]

    def test_heartbeat_requires_forge_root(self, cli_runner):
        """Heartbeat fails when not in FORGE root."""
        with patch("forge_harness.cli_v2.nodes.require_forge_root") as mock_req:
            mock_req.side_effect = SystemExit(1)
            result = cli_runner.invoke(nodes, ["heartbeat"], catch_exceptions=True)
        assert result.exit_code != 0 or "Error" in result.output

    def test_queue_depth_from_task_queue(self, forge_root):
        """Queue depth counts pending tasks in task_queue.json."""
        telemetry = collect_telemetry(forge_root, "x")
        assert telemetry["resources"]["queue_depth"] >= 1  # 1 pending in fixture

    def test_heartbeat_node_option(self, forge_root, cli_runner):
        """--node sets node_id in telemetry."""
        import json
        from unittest.mock import Mock

        mock_resp = Mock()
        mock_resp.status_code = 200
        with patch("forge_harness.cli_v2.nodes.require_forge_root", return_value=forge_root):
            with patch("forge_harness.cli_v2.nodes.httpx") as mock_httpx:
                mock_httpx.Client.return_value.__enter__.return_value.post.return_value = mock_resp
                result = cli_runner.invoke(
                    nodes,
                    ["heartbeat", "--node", "mac-pro-01", "--format", "json"],
                    catch_exceptions=False,
                )
        assert result.exit_code == 0
        envelope = json.loads(result.output.strip())
        node_id = envelope["data"]["telemetry"]["node_id"]
        assert "mac-pro-01" in node_id or node_id == "mac-pro-01"
