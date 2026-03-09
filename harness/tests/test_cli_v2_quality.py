"""Tests for forge quality CLI command."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_forge_root(tmp_path):
    """Create a mock forge root with project structure."""
    forge_root = tmp_path / "forge"
    forge_root.mkdir()

    domain = forge_root / "codeswiftr-com"
    domain.mkdir()

    project = domain / "interview-simulator"
    project.mkdir()

    backend = project / "backend"
    backend.mkdir()

    frontend = project / "frontend"
    frontend.mkdir()

    return forge_root


class TestQualityCommand:
    """Test the forge quality command group."""

    def test_quality_help(self, runner):
        """Test quality --help shows subcommands."""
        result = runner.invoke(cli, ["quality", "--help"])
        assert result.exit_code == 0
        assert "check" in result.output
        assert "report" in result.output
        assert "fix" in result.output


class TestQualityCheck:
    """Test forge quality check command."""

    def test_check_requires_domain_or_project(self, runner, mock_forge_root):
        """Test that check requires domain or can infer from directory."""
        with patch("forge_harness.cli_v2._common.require_forge_root", return_value=mock_forge_root):
            with patch(
                "forge_harness.cli_v2._common.infer_domain_project", return_value=(None, None)
            ):
                result = runner.invoke(cli, ["quality", "check"], catch_exceptions=False)
                assert result.exit_code != 0

    def test_check_with_domain_and_project(self, runner, mock_forge_root):
        """Test check with explicit domain and project."""
        with patch("forge_harness.cli_v2._common.require_forge_root", return_value=mock_forge_root):
            with patch("forge_harness.cli_v2.quality._run_quality_checks") as mock_checks:
                mock_checks.return_value = {
                    "lint": {"errors": 0, "warnings": 0, "output": ""},
                    "type_check": {"errors": 0, "warnings": 0, "output": ""},
                }
                result = runner.invoke(
                    cli,
                    ["quality", "check", "-d", "codeswiftr-com", "-p", "interview-simulator"],
                    catch_exceptions=False,
                )
                assert result.exit_code == 0

    def test_check_json_output(self, runner, mock_forge_root):
        """Test check with JSON output."""
        with patch("forge_harness.cli_v2._common.require_forge_root", return_value=mock_forge_root):
            with patch("forge_harness.cli_v2.quality._run_quality_checks") as mock_checks:
                mock_checks.return_value = {
                    "lint": {"errors": 0, "warnings": 0, "output": ""},
                    "type_check": {"errors": 0, "warnings": 0, "output": ""},
                }
                result = runner.invoke(
                    cli,
                    [
                        "quality",
                        "check",
                        "-d",
                        "codeswiftr-com",
                        "-p",
                        "interview-simulator",
                        "--json",
                    ],
                    catch_exceptions=False,
                )
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True

    def test_check_lint_only(self, runner, mock_forge_root):
        """Test check with --lint-only flag."""
        with patch("forge_harness.cli_v2._common.require_forge_root", return_value=mock_forge_root):
            result = runner.invoke(
                cli,
                [
                    "quality",
                    "check",
                    "-d",
                    "codeswiftr-com",
                    "-p",
                    "interview-simulator",
                    "--lint-only",
                ],
            )
            # Either passes (0) or fails due to actual lint errors - just check it runs
            assert result.exit_code in (0, 1)


class TestQualityReport:
    """Test forge quality report command."""

    def test_report_help(self, runner):
        """Test report command shows in help."""
        result = runner.invoke(cli, ["quality", "report", "--help"])
        assert result.exit_code == 0

    def test_report_json_output(self, runner, mock_forge_root):
        """Test report with JSON output."""
        with patch("forge_harness.cli_v2._common.require_forge_root", return_value=mock_forge_root):
            with patch("forge_harness.cli_v2.quality._generate_quality_report") as mock_report:
                mock_report.return_value = {
                    "domain": "codeswiftr-com",
                    "project": "interview-simulator",
                    "projects": [],
                }
                result = runner.invoke(
                    cli,
                    [
                        "quality",
                        "report",
                        "-d",
                        "codeswiftr-com",
                        "-p",
                        "interview-simulator",
                        "--json",
                    ],
                    catch_exceptions=False,
                )
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True

    def test_report_severity_filter(self, runner, mock_forge_root):
        """Test report with severity filter."""
        with patch("forge_harness.cli_v2._common.require_forge_root", return_value=mock_forge_root):
            with patch("forge_harness.cli_v2.quality._generate_quality_report") as mock_report:
                mock_report.return_value = {"domain": "codeswiftr-com", "projects": []}
                result = runner.invoke(
                    cli,
                    ["quality", "report", "--severity", "high", "--json"],
                    catch_exceptions=False,
                )
                assert result.exit_code == 0


class TestQualityFix:
    """Test forge quality fix command."""

    def test_fix_help(self, runner):
        """Test fix command shows in help."""
        result = runner.invoke(cli, ["quality", "fix", "--help"])
        assert result.exit_code == 0

    def test_fix_with_domain_and_project(self, runner, mock_forge_root):
        """Test fix with explicit domain and project."""
        with patch("forge_harness.cli_v2._common.require_forge_root", return_value=mock_forge_root):
            with patch("forge_harness.cli_v2.quality._run_ruff_fix") as mock_fix:
                mock_fix.return_value = {"fixed": 3, "files": ["file1.py", "file2.py"]}
                result = runner.invoke(
                    cli,
                    ["quality", "fix", "-d", "codeswiftr-com", "-p", "interview-simulator"],
                    catch_exceptions=False,
                )
                assert result.exit_code == 0

    def test_fix_json_output(self, runner, mock_forge_root):
        """Test fix with JSON output."""
        with patch("forge_harness.cli_v2._common.require_forge_root", return_value=mock_forge_root):
            with patch("forge_harness.cli_v2.quality._run_ruff_fix") as mock_fix:
                mock_fix.return_value = {"fixed": 0, "files": []}
                result = runner.invoke(
                    cli,
                    [
                        "quality",
                        "fix",
                        "-d",
                        "codeswiftr-com",
                        "-p",
                        "interview-simulator",
                        "--json",
                    ],
                    catch_exceptions=False,
                )
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True

    def test_fix_dry_run(self, runner, mock_forge_root):
        """Test fix with --dry-run flag."""
        with patch("forge_harness.cli_v2._common.require_forge_root", return_value=mock_forge_root):
            with patch("forge_harness.cli_v2.quality._run_ruff_fix") as mock_fix:
                mock_fix.return_value = {"fixed": 5, "files": []}
                result = runner.invoke(
                    cli,
                    [
                        "quality",
                        "fix",
                        "-d",
                        "codeswiftr-com",
                        "-p",
                        "interview-simulator",
                        "--dry-run",
                    ],
                    catch_exceptions=False,
                )
                assert result.exit_code == 0
                mock_fix.assert_called_once()
                _, kwargs = mock_fix.call_args
                assert kwargs["dry_run"] is True


class TestQualityIntegration:
    """Integration tests for quality command."""

    def test_quality_subcommands_registered(self, runner):
        """Test all subcommands are properly registered."""
        result = runner.invoke(cli, ["quality", "--help"])
        assert "Commands:" in result.output
        assert "check" in result.output
        assert "report" in result.output
        assert "fix" in result.output
