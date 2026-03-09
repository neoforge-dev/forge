"""
Tests for forge nodes CLI command - Node telemetry and orchestration.

Tests:
- forge nodes heartbeat - Publish node telemetry
- forge nodes list - List all nodes
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.nodes import nodes


@pytest.fixture
def cli_runner():
    """Click CLI test runner."""
    return CliRunner()


class TestNodesCommand:
    """Test forge nodes command."""

    def test_nodes_help(self, cli_runner):
        """Nodes command should have help."""
        result = cli_runner.invoke(nodes, ["--help"])
        assert result.exit_code == 0
        assert "node" in result.output.lower() or "telemetry" in result.output.lower()


class TestNodeTelemetryHelpers:
    """Test node telemetry helper functions."""

    def test_get_cpu_usage_pct(self):
        """Test CPU usage calculation."""
        from forge_harness.cli_v2.nodes import _get_cpu_usage_pct

        # Mock os.getloadavg and os.cpu_count
        with patch("forge_harness.cli_v2.nodes.os.getloadavg", return_value=[1.0, 1.0, 1.0]), \
             patch("forge_harness.cli_v2.nodes.os.cpu_count", return_value=4):
            result = _get_cpu_usage_pct()
            assert 0 <= result <= 100

    def test_get_cpu_usage_handles_error(self):
        """Test CPU usage handles errors gracefully."""
        from forge_harness.cli_v2.nodes import _get_cpu_usage_pct

        with patch("forge_harness.cli_v2.nodes.os.getloadavg", side_effect=OSError):
            result = _get_cpu_usage_pct()
            assert result == 0.0

    def test_get_ram_available_mb(self):
        """Test RAM available calculation."""
        from forge_harness.cli_v2.nodes import _get_ram_available_mb

        with patch("forge_harness.cli_v2.nodes.os.sysconf", side_effect=lambda x: {
            "SC_PAGE_SIZE": 4096,
            "SC_PHYS_PAGES": 1000000,
            "SC_AVPHYS_PAGES": 800000,
        }.get(x, 0)):
            result = _get_ram_available_mb()
            assert result >= 0

    def test_get_disk_free_gb(self):
        """Test disk free space calculation."""
        from forge_harness.cli_v2.nodes import _get_disk_free_gb

        with patch("forge_harness.cli_v2.nodes.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = Mock(total=1000000000000, used=500000000000, free=500000000000)
            result = _get_disk_free_gb()
            assert result > 0  # Should calculate approximately

    def test_get_disk_free_gb_handles_error(self):
        """Test disk free handles errors gracefully."""
        from forge_harness.cli_v2.nodes import _get_disk_free_gb

        with patch("forge_harness.cli_v2.nodes.shutil.disk_usage", side_effect=OSError):
            result = _get_disk_free_gb()
            assert result == 0.0


class TestNodesListOffline:
    """Tests for forge nodes list --offline."""

    def test_list_offline_json_reads_heartbeat_files(self, cli_runner, tmp_path: Path):
        """Offline list should read local heartbeat node files and emit JSON."""
        forge_root = tmp_path / "FORGE"
        forge_root.mkdir(parents=True)
        (forge_root / "domains.yaml").write_text("domains: {}", encoding="utf-8")
        nodes_dir = forge_root / ".forge" / "heartbeat" / "nodes"
        nodes_dir.mkdir(parents=True)
        (nodes_dir / "nova.json").write_text(
            json.dumps(
                {
                    "node_id": "nova",
                    "timestamp": "2026-02-25T08:00:00+00:00",
                    "resources": {
                        "cpu_usage_percent": 22.5,
                        "ram_available_mb": 8192,
                        "ram_total_mb": 16384,
                    },
                    "capabilities": ["claude", "codex"],
                    "health": {"status": "healthy"},
                }
            ),
            encoding="utf-8",
        )

        with patch("forge_harness.cli_v2.nodes.require_forge_root", return_value=forge_root):
            with patch("forge_harness.cli_v2.nodes.httpx.Client") as mock_client:
                result = cli_runner.invoke(
                    nodes,
                    ["list", "--offline", "--json"],
                    catch_exceptions=False,
                )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["data"]["source"] == "offline"
        assert len(payload["data"]["nodes"]) == 1
        assert payload["data"]["nodes"][0]["node_id"] == "nova"
        assert payload["data"]["nodes"][0]["source"] == "heartbeat-file"
        mock_client.assert_not_called()

    def test_list_offline_text_handles_invalid_json(self, cli_runner, tmp_path: Path):
        """Offline list should skip malformed heartbeat files without failing."""
        forge_root = tmp_path / "FORGE"
        forge_root.mkdir(parents=True)
        (forge_root / "domains.yaml").write_text("domains: {}", encoding="utf-8")
        nodes_dir = forge_root / ".forge" / "heartbeat" / "nodes"
        nodes_dir.mkdir(parents=True)
        (nodes_dir / "bad.json").write_text("{not-valid-json", encoding="utf-8")
        (nodes_dir / "sati.json").write_text(
            json.dumps(
                {
                    "node_id": "sati",
                    "timestamp": "2026-02-25T08:00:00+00:00",
                    "resources": {"cpu_usage_percent": 11.0},
                    "capabilities": ["gemini"],
                }
            ),
            encoding="utf-8",
        )

        with patch("forge_harness.cli_v2.nodes.require_forge_root", return_value=forge_root):
            result = cli_runner.invoke(
                nodes,
                ["list", "--offline"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "sati" in result.output
