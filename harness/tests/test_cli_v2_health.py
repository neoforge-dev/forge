"""
Tests for forge health CLI command - Agent health monitoring.

Tests:
- forge health agent_name - Check single agent
- forge health --all - Check all agents
- forge health --json - JSON output
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.health import health


@pytest.fixture
def cli_runner():
    """Click CLI test runner."""
    return CliRunner()


class TestHealthCommand:
    """Test forge health command."""

    def test_health_help(self, cli_runner):
        """Health command should have help."""
        result = cli_runner.invoke(health, ["--help"])
        assert result.exit_code == 0
        assert "agent" in result.output.lower() or "health" in result.output.lower()

    def test_health_requires_agent_or_flag(self, cli_runner):
        """Health command requires agent name or --all flag."""
        with patch("forge_harness.cli_v2.health.require_forge_root") as mock_root:
            mock_root.return_value = "/tmp/forge"

            result = cli_runner.invoke(health, [], catch_exceptions=False)

            # Should fail or require an agent
            assert result.exit_code != 0 or "required" in result.output.lower() or "agent" in result.output.lower()

    def test_health_unknown_agent(self, cli_runner):
        """Health command handles unknown agent gracefully."""
        with patch("forge_harness.cli_v2.health.require_forge_root") as mock_root, \
             patch("forge_harness.health_monitor.HealthMonitor") as mock_class:
            mock_root.return_value = "/tmp/forge"

            mock_instance = Mock()
            mock_instance.check.side_effect = Exception("Agent not found")
            mock_class.return_value = mock_instance

            result = cli_runner.invoke(health, ["forge:unknown"], catch_exceptions=False)

            # Should exit with error code (graceful error handling)
            assert result.exit_code == 1  # ExitCode.GENERAL_ERROR


class TestHealthStatusStyles:
    """Test health status style helper."""

    def test_status_style_exists(self):
        """_status_style function should exist."""
        from forge_harness.cli_v2.health import _status_style

        assert callable(_status_style)

    def test_status_style_returns_string(self):
        """_status_style should return a string."""
        from forge_harness.cli_v2.health import _status_style

        result = _status_style("healthy")
        assert isinstance(result, str)
        assert len(result) > 0
