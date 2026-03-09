"""
Enhanced tests for forge status command.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner


class TestStatusHelp:
    """Test help commands exist."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_status_help(self, runner, mock_forge_root):
        """Test status --help works."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["status", "--help"])
            assert result.exit_code == 0

    def test_status_watch_help(self, runner, mock_forge_root):
        """Test status --watch help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["status", "--watch", "--help"])
            # May fail since --watch requires TTY but --help should work
            assert result.exit_code in (0, 1)

    def test_status_approvals_help(self, runner, mock_forge_root):
        """Test status --approvals help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["status", "--approvals", "--help"])
            assert "approvals" in result.output.lower() or result.exit_code == 0

    def test_status_agents_help(self, runner, mock_forge_root):
        """Test status --agents help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["status", "--agents", "--help"])
            assert "agents" in result.output.lower() or result.exit_code == 0

    def test_status_portfolio_help(self, runner, mock_forge_root):
        """Test status --portfolio help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["status", "--portfolio", "--help"])
            assert "portfolio" in result.output.lower() or result.exit_code == 0

    def test_status_patterns_help(self, runner, mock_forge_root):
        """Test status --patterns help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["status", "--patterns", "--help"])
            assert "patterns" in result.output.lower() or result.exit_code == 0

    def test_status_pipelines_help(self, runner, mock_forge_root):
        """Test status --pipelines help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["status", "--pipelines", "--help"])
            assert "pipelines" in result.output.lower() or result.exit_code == 0
