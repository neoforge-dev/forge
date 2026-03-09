"""
Enhanced tests for forge doctor command.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner


class TestDoctorHelp:
    """Test doctor help commands exist."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_doctor_help(self, runner, mock_forge_root):
        """Test doctor --help works."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["doctor", "--help"])
            assert result.exit_code == 0

    def test_doctor_fix_help(self, runner, mock_forge_root):
        """Test doctor --fix help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["doctor", "--fix", "--help"])
            assert "fix" in result.output.lower() or result.exit_code == 0

    def test_doctor_report_help(self, runner, mock_forge_root):
        """Test doctor --report help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["doctor", "--report", "--help"])
            assert "report" in result.output.lower() or result.exit_code == 0

    def test_doctor_check_help(self, runner, mock_forge_root):
        """Test doctor --check help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["doctor", "--check", "git", "--help"])
            assert "check" in result.output.lower() or result.exit_code == 0

    def test_doctor_cleanup_help(self, runner, mock_forge_root):
        """Test doctor --cleanup help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["doctor", "--cleanup", "--help"])
            assert "cleanup" in result.output.lower() or result.exit_code == 0

    def test_doctor_json_help(self, runner, mock_forge_root):
        """Test doctor --json help."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["doctor", "--json", "--help"])
            assert "json" in result.output.lower() or result.exit_code == 0
