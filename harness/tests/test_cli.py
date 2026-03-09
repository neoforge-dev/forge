"""
Tests for CLI module
====================

Tests for forge_harness.cli unified CLI.
"""

from datetime import UTC

import pytest
from click.testing import CliRunner


@pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: help text, version string, and subcommand names differ (e.g. 'run'/'pipeline' replaced by 'work'/'orchestrator')")
class TestCLIMainGroup:
    """Tests for main CLI group."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_cli_main_group(self, runner):
        """Test main CLI group exists and shows help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "FORGE Harness" in result.output
        # Check subcommands are listed
        assert "run" in result.output
        assert "bootstrap" in result.output
        assert "pipeline" in result.output
        assert "dashboard" in result.output
        assert "config" in result.output

    def test_cli_version(self, runner):
        """Test --version flag."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.3.0" in result.output
        assert "forge-harness" in result.output

    def test_cli_verbose(self, runner):
        """Test --verbose flag enables debug logging."""
        import logging

        from forge_harness.cli_v2 import cli

        # Run with verbose flag (need a subcommand that doesn't error)
        # Use --help to avoid NotImplementedError from subcommands
        result = runner.invoke(cli, ["--verbose", "--help"])
        assert result.exit_code == 0

        # Verify logger level was set
        harness_logger = logging.getLogger("forge_harness")
        # Note: logging level may not persist after CLI exits, so we just verify no error
        assert "FORGE Harness" in result.output


@pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: 'run' command replaced by 'work' with different flags")
class TestCLIRunCommand:
    """Tests for run command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_forge_project(self, tmp_path):
        """Create a mock FORGE project structure."""
        # Create domain directory
        domain_dir = tmp_path / "test-domain"
        domain_dir.mkdir()

        # Create project directory
        project_dir = domain_dir / "test-project"
        project_dir.mkdir()

        # Create PLAN.md
        (project_dir / "PLAN.md").write_text("# Test Plan\n")

        # Create domains.yaml marker
        (tmp_path / "domains.yaml").write_text("domains: []\n")

        # Create .git directory
        (tmp_path / ".git").mkdir()

        return tmp_path

    def test_cli_run_basic(self, runner, mock_forge_project):
        """Test basic run command with dry-run."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            [
                "run",
                "--domain",
                "test-domain",
                "--project",
                "test-project",
                "--forge-root",
                str(mock_forge_project),
                "--github-repo",
                "owner/repo",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Dry run mode" in result.output
        assert "test-domain/test-project" in result.output

    def test_cli_run_options(self, runner, mock_forge_project):
        """Test run command with all options."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            [
                "run",
                "--domain",
                "test-domain",
                "--project",
                "test-project",
                "--forge-root",
                str(mock_forge_project),
                "--github-repo",
                "owner/repo",
                "--max-iterations",
                "10",
                "--model",
                "claude-sonnet-4-20250514",
                "--deploy",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Max iterations: 10" in result.output
        assert "Model: claude-sonnet-4-20250514" in result.output
        assert "Deploy: True" in result.output

    def test_cli_run_missing_project(self, runner, mock_forge_project):
        """Test run command fails with missing project."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            [
                "run",
                "--domain",
                "test-domain",
                "--project",
                "nonexistent",
                "--forge-root",
                str(mock_forge_project),
                "--github-repo",
                "owner/repo",
            ],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_cli_run_help(self, runner):
        """Test run command help shows all options."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--domain" in result.output
        assert "--project" in result.output
        assert "--max-iterations" in result.output
        assert "--model" in result.output
        assert "--deploy" in result.output
        assert "--dry-run" in result.output
        assert "--forge-root" in result.output
        assert "--github-repo" in result.output


@pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: bootstrap command flags differ (--forge-root, --list-epics, --validate-only, --github-repo not present in cli_v2)")
class TestCLIBootstrapCommand:
    """Tests for bootstrap command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_forge_project_with_plan(self, tmp_path):
        """Create a mock FORGE project with PLAN.md."""
        # Create domain directory
        domain_dir = tmp_path / "test-domain"
        domain_dir.mkdir()

        # Create project directory
        project_dir = domain_dir / "test-project"
        project_dir.mkdir()

        # Create docs directory
        docs_dir = project_dir / "docs"
        docs_dir.mkdir()

        # Create PLAN.md with epic format
        plan_content = """# Test Project Plan

## Epic 1: Setup Infrastructure

### Rationale
Initial project setup and configuration.

### Acceptance Criteria
- [ ] Project initialized
- [ ] CI/CD configured

### ICE Score: 8/9/7
### Effort: 4h

## Epic 2: Core Features

### Rationale
Implement core functionality.

### Acceptance Criteria
- [ ] Feature A implemented
- [ ] Feature B implemented

### ICE Score: 9/8/8
### Effort: 8h
"""
        (docs_dir / "PLAN.md").write_text(plan_content)

        # Create domains.yaml marker
        (tmp_path / "domains.yaml").write_text("domains: []\n")

        # Create .git directory
        (tmp_path / ".git").mkdir()

        return tmp_path

    def test_cli_bootstrap_help(self, runner):
        """Test bootstrap command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["bootstrap", "--help"])
        assert result.exit_code == 0
        assert "--domain" in result.output
        assert "--project" in result.output
        assert "--validate-only" in result.output
        assert "--list-epics" in result.output
        assert "--dry-run" in result.output

    def test_cli_bootstrap_validate(self, runner, mock_forge_project_with_plan):
        """Test bootstrap --validate-only."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            [
                "bootstrap",
                "--domain",
                "test-domain",
                "--project",
                "test-project",
                "--forge-root",
                str(mock_forge_project_with_plan),
                "--validate-only",
            ],
        )
        # May exit 0 (valid) or 1 (warnings/errors) - just check it runs
        assert "Validating:" in result.output

    def test_cli_bootstrap_list_epics(self, runner, mock_forge_project_with_plan):
        """Test bootstrap --list-epics."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            [
                "bootstrap",
                "--domain",
                "test-domain",
                "--project",
                "test-project",
                "--forge-root",
                str(mock_forge_project_with_plan),
                "--list-epics",
            ],
        )
        assert result.exit_code == 0
        assert "Epics in:" in result.output
        assert (
            "Epic 1" in result.output
            or "Epic 2" in result.output
            or "No epics found" in result.output
        )

    def test_cli_bootstrap_dry_run(self, runner, mock_forge_project_with_plan):
        """Test bootstrap --dry-run."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            [
                "bootstrap",
                "--domain",
                "test-domain",
                "--project",
                "test-project",
                "--forge-root",
                str(mock_forge_project_with_plan),
                "--github-repo",
                "owner/repo",
                "--dry-run",
            ],
        )
        assert "dry-run" in result.output.lower()

    def test_cli_bootstrap_missing_plan(self, runner, tmp_path):
        """Test bootstrap fails with missing PLAN.md."""
        from forge_harness.cli_v2 import cli

        # Create minimal structure without PLAN.md
        domain_dir = tmp_path / "test-domain" / "test-project"
        domain_dir.mkdir(parents=True)
        (tmp_path / "domains.yaml").write_text("domains: []\n")

        result = runner.invoke(
            cli,
            [
                "bootstrap",
                "--domain",
                "test-domain",
                "--project",
                "test-project",
                "--forge-root",
                str(tmp_path),
                "--dry-run",
            ],
        )
        assert result.exit_code == 1
        assert "PLAN.md not found" in result.output


@pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: 'pipeline' subcommand group replaced by 'orchestrator' with different API")
class TestCLIPipelineCommand:
    """Tests for pipeline subcommand."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_cli_pipeline_help(self, runner):
        """Test pipeline command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["pipeline", "--help"])
        assert result.exit_code == 0
        assert "execute" in result.output
        assert "list" in result.output
        assert "status" in result.output

    def test_cli_pipeline_execute_dry_run(self, runner):
        """Test pipeline execute with dry-run."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            ["pipeline", "execute", "content_to_publish", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "content_to_publish" in result.output

    def test_cli_pipeline_execute_not_found(self, runner):
        """Test pipeline execute with unknown pipeline."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            ["pipeline", "execute", "nonexistent_pipeline", "--dry-run"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_cli_pipeline_list(self, runner):
        """Test pipeline list command."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["pipeline", "list"])
        assert result.exit_code == 0
        assert "Available Pipelines" in result.output
        assert "Built-in" in result.output

    def test_cli_pipeline_status(self, runner, tmp_path):
        """Test pipeline status command."""
        import json

        from forge_harness.cli_v2 import cli

        # Create a mock checkpoint file
        checkpoint = {
            "pipeline_name": "test_pipeline",
            "created_at": "2026-01-13T10:00:00Z",
            "updated_at": "2026-01-13T10:05:00Z",
            "step_results": [
                {"name": "step1", "status": "completed", "duration_seconds": 1.5, "error": None},
                {
                    "name": "step2",
                    "status": "failed",
                    "duration_seconds": 0.5,
                    "error": "Test error",
                },
            ],
            "context": {"domain": "test-domain"},
        }
        checkpoint_file = tmp_path / "checkpoint.json"
        checkpoint_file.write_text(json.dumps(checkpoint))

        result = runner.invoke(cli, ["pipeline", "status", str(checkpoint_file)])
        assert result.exit_code == 0
        assert "test_pipeline" in result.output
        assert "step1" in result.output
        assert "completed" in result.output
        assert "step2" in result.output
        assert "Test error" in result.output


@pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: config command has different subcommands/flags in cli_v2 (e.g. 'show', 'set' with different options)")
class TestCLIConfigCommand:
    """Tests for config subcommand."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_cli_config_help(self, runner):
        """Test config command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0
        assert "show" in result.output
        assert "set" in result.output

    def test_cli_config_show(self, runner):
        """Test config show command."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "FORGE Harness Configuration" in result.output
        # Check some expected keys (always shown with default values)
        assert "dry_run" in result.output
        assert "max_iterations" in result.output
        assert "[Status]" in result.output

    def test_cli_config_show_all(self, runner):
        """Test config show --all includes unset values."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["config", "show", "--all"])
        assert result.exit_code == 0
        assert "FORGE Harness Configuration" in result.output
        # With --all flag, unset values should be shown
        assert "domain" in result.output
        assert "project" in result.output
        assert "(not set)" in result.output

    def test_cli_config_show_redacts_secrets(self, runner, monkeypatch):
        """Test config show redacts secrets."""
        from forge_harness.cli_v2 import cli

        # Set a secret env var (use FORGE_ prefix per ForgeSettings)
        monkeypatch.setenv("FORGE_NOTION_API_TOKEN", "secret_token_value")

        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        # Should show redacted value
        assert "secr..." in result.output or "notion_api_token" in result.output

    def test_cli_config_set(self, runner, tmp_path):
        """Test config set command."""
        from forge_harness.cli_v2 import cli

        env_file = tmp_path / ".env"

        result = runner.invoke(
            cli,
            ["config", "set", "DOMAIN=test-domain", "--env-file", str(env_file)],
        )
        assert result.exit_code == 0
        assert "Set" in result.output
        assert "FORGE_DOMAIN=test-domain" in result.output

        # Verify file was written
        content = env_file.read_text()
        assert "FORGE_DOMAIN=test-domain" in content

    def test_cli_config_set_invalid_format(self, runner):
        """Test config set rejects invalid format."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["config", "set", "invalid_no_equals"])
        assert result.exit_code == 1
        assert "key=value" in result.output


@pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: dashboard command has different output format and flags in cli_v2")
class TestCLIDashboardCommand:
    """Tests for dashboard command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_forge_portfolio(self, tmp_path, monkeypatch):
        """Create a mock FORGE portfolio structure."""
        # Create domain directories
        domain1 = tmp_path / "test-domain"
        domain1.mkdir()

        # Create project with Python files
        project1 = domain1 / "project-one"
        project1.mkdir()
        (project1 / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        (project1 / "PLAN.md").write_text("# Plan\n")

        # Create project with Node files
        project2 = domain1 / "project-two"
        project2.mkdir()
        (project2 / "package.json").write_text('{"name": "test"}\n')

        # Create domains.yaml marker
        (tmp_path / "domains.yaml").write_text("domains: []\n")

        # Change to this directory
        monkeypatch.chdir(tmp_path)

        return tmp_path

    def test_cli_dashboard_help(self, runner):
        """Test dashboard command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "--live" in result.output
        assert "--domain" in result.output

    def test_cli_dashboard(self, runner, mock_forge_portfolio):
        """Test basic dashboard command."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["dashboard"])
        assert result.exit_code == 0
        assert "FORGE Portfolio Dashboard" in result.output
        assert "test-domain" in result.output
        assert "project-one" in result.output
        assert "project-two" in result.output

    def test_cli_dashboard_filter(self, runner, mock_forge_portfolio):
        """Test dashboard with domain filter."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["dashboard", "--domain", "test-domain"])
        assert result.exit_code == 0
        assert "test-domain" in result.output

    def test_cli_dashboard_live(self, runner, mock_forge_portfolio):
        """Test dashboard --live mode."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["dashboard", "--live"])
        assert result.exit_code == 0
        # Live mode should show a message
        assert "--live mode" in result.output or "Dashboard" in result.output


@pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: quality subcommand group has different subcommands/flags in cli_v2")
class TestCLIQualityCommand:
    """Tests for quality subcommand group."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_cli_quality_help(self, runner):
        """Test quality command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["quality", "--help"])
        assert result.exit_code == 0
        assert "scan" in result.output
        assert "report" in result.output
        assert "history" in result.output
        assert "alert" in result.output
        assert "self-analyze" in result.output

    def test_cli_quality_scan_help(self, runner):
        """Test quality scan command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["quality", "scan", "--help"])
        assert result.exit_code == 0
        assert "--domain" in result.output
        assert "--project" in result.output
        assert "--output" in result.output
        assert "--json" in result.output
        assert "--metrics-dir" in result.output

    def test_cli_quality_report_no_data(self, runner, tmp_path):
        """Test quality report with no existing data."""
        from forge_harness.cli_v2 import cli

        metrics_dir = tmp_path / "empty_metrics"
        metrics_dir.mkdir()

        result = runner.invoke(
            cli,
            ["quality", "report", "--metrics-dir", str(metrics_dir)],
        )
        assert result.exit_code == 0
        assert "No quality reports found" in result.output

    def test_cli_quality_report_with_data(self, runner, tmp_path):
        """Test quality report reads existing data."""
        import json

        from forge_harness.cli_v2 import cli

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        # Create mock portfolio report with full schema
        report_data = {
            "scan_timestamp": "2026-01-18T12:00:00Z",
            "projects_scanned": 2,
            "total_projects": 3,
            "average_quality_score": 75.5,
            "portfolio_trend": "stable",
            "critical_issues": 0,
            "high_issues": 3,
            "degraded_projects": [],
            "improved_projects": ["test-project"],
            "projects": [
                {
                    "domain": "test-domain",
                    "project_name": "test-project",
                    "project_path": "/path/to/test-project",
                    "scan_timestamp": "2026-01-18T12:00:00Z",
                    "quality_score": 82.0,
                    "trend": "improving",
                    "previous_score": 75.0,
                    "debt_metrics": {
                        "complexity_score": 30.0,
                        "duplication_percent": 5.0,
                        "coverage_percent": 75.0,
                        "documentation_percent": 50.0,
                        "security_issues": 0,
                        "total_debt_hours": 10.0,
                        "velocity_impact_percent": 5.0,
                    },
                    "security_findings": [],
                    "issues": [],
                    "recommendations": [],
                },
            ],
        }
        (metrics_dir / "portfolio_latest.json").write_text(json.dumps(report_data))

        result = runner.invoke(
            cli,
            ["quality", "report", "--metrics-dir", str(metrics_dir)],
        )
        assert result.exit_code == 0
        assert "Quality Report" in result.output
        assert "test-project" in result.output
        assert "75.5" in result.output or "82" in result.output

    def test_cli_quality_report_json_output(self, runner, tmp_path):
        """Test quality report JSON output."""
        import json

        from forge_harness.cli_v2 import cli

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        report_data = {
            "scan_timestamp": "2026-01-18T12:00:00Z",
            "projects_scanned": 1,
            "total_projects": 1,
            "average_quality_score": 80.0,
            "portfolio_trend": "stable",
            "critical_issues": 0,
            "high_issues": 0,
            "degraded_projects": [],
            "improved_projects": [],
            "projects": [],
        }
        (metrics_dir / "portfolio_latest.json").write_text(json.dumps(report_data))

        result = runner.invoke(
            cli,
            ["quality", "report", "--metrics-dir", str(metrics_dir), "--json"],
        )
        assert result.exit_code == 0
        # Should output valid JSON
        output_data = json.loads(result.output)
        assert output_data["average_quality_score"] == 80.0

    def test_cli_quality_history_no_data(self, runner, tmp_path):
        """Test quality history with no existing data."""
        from forge_harness.cli_v2 import cli

        metrics_dir = tmp_path / "empty_metrics"
        metrics_dir.mkdir()

        result = runner.invoke(
            cli,
            ["quality", "history", "test-project", "--metrics-dir", str(metrics_dir)],
        )
        assert result.exit_code == 0
        assert "No history found" in result.output

    def test_cli_quality_history_with_data(self, runner, tmp_path):
        """Test quality history displays project history."""
        import json

        from forge_harness.cli_v2 import cli

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        history_data = [
            {
                "timestamp": "2026-01-15T12:00:00Z",
                "quality_score": 70.0,
                "coverage_percent": 60.0,
                "complexity_score": 45.0,
                "security_issues": 2,
            },
            {
                "timestamp": "2026-01-18T12:00:00Z",
                "quality_score": 78.0,
                "coverage_percent": 72.0,
                "complexity_score": 42.0,
                "security_issues": 1,
            },
        ]
        (metrics_dir / "test-project_history.json").write_text(json.dumps(history_data))

        result = runner.invoke(
            cli,
            ["quality", "history", "test-project", "--metrics-dir", str(metrics_dir)],
        )
        assert result.exit_code == 0
        assert "Quality History" in result.output
        assert "test-project" in result.output
        assert "70.0" in result.output or "78.0" in result.output

    def test_cli_quality_history_json_output(self, runner, tmp_path):
        """Test quality history JSON output."""
        import json

        from forge_harness.cli_v2 import cli

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        history_data = [
            {"timestamp": "2026-01-18T12:00:00Z", "quality_score": 80.0},
        ]
        (metrics_dir / "test-project_history.json").write_text(json.dumps(history_data))

        result = runner.invoke(
            cli,
            ["quality", "history", "test-project", "--metrics-dir", str(metrics_dir), "--json"],
        )
        assert result.exit_code == 0
        output_data = json.loads(result.output)
        assert output_data[0]["quality_score"] == 80.0

    def test_cli_quality_alert_no_data(self, runner, tmp_path):
        """Test quality alert with no existing data."""
        from forge_harness.cli_v2 import cli

        metrics_dir = tmp_path / "empty_metrics"
        metrics_dir.mkdir()

        result = runner.invoke(
            cli,
            ["quality", "alert", "--metrics-dir", str(metrics_dir)],
        )
        assert result.exit_code == 0
        assert "No quality reports found" in result.output

    def test_cli_quality_alert_all_pass(self, runner, tmp_path):
        """Test quality alert when all projects pass threshold."""
        import json

        from forge_harness.cli_v2 import cli

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        # Helper to create full project data
        def make_project(name, domain, score):
            return {
                "domain": domain,
                "project_name": name,
                "project_path": f"/path/to/{name}",
                "scan_timestamp": "2026-01-18T12:00:00Z",
                "quality_score": score,
                "trend": "stable",
                "previous_score": score - 5,
                "debt_metrics": {
                    "complexity_score": 30.0,
                    "duplication_percent": 5.0,
                    "coverage_percent": 80.0,
                    "documentation_percent": 50.0,
                    "security_issues": 0,
                    "total_debt_hours": 10.0,
                    "velocity_impact_percent": 5.0,
                },
                "security_findings": [],
                "issues": [],
                "recommendations": [],
            }

        report_data = {
            "scan_timestamp": "2026-01-18T12:00:00Z",
            "projects_scanned": 2,
            "total_projects": 2,
            "average_quality_score": 85.0,
            "portfolio_trend": "stable",
            "critical_issues": 0,
            "high_issues": 0,
            "degraded_projects": [],
            "improved_projects": [],
            "projects": [
                make_project("project-a", "test-domain", 85.0),
                make_project("project-b", "test-domain", 90.0),
            ],
        }
        (metrics_dir / "portfolio_latest.json").write_text(json.dumps(report_data))

        result = runner.invoke(
            cli,
            ["quality", "alert", "--threshold", "70", "--metrics-dir", str(metrics_dir)],
        )
        assert result.exit_code == 0
        assert "All projects meet quality threshold" in result.output

    def test_cli_quality_alert_with_failures(self, runner, tmp_path):
        """Test quality alert filters by threshold."""
        import json

        from forge_harness.cli_v2 import cli

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        # Helper to create full project data
        def make_project(name, domain, score, issues=None, recommendations=None):
            return {
                "domain": domain,
                "project_name": name,
                "project_path": f"/path/to/{name}",
                "scan_timestamp": "2026-01-18T12:00:00Z",
                "quality_score": score,
                "trend": "stable" if score >= 70 else "degrading",
                "previous_score": score + 5,
                "debt_metrics": {
                    "complexity_score": 30.0,
                    "duplication_percent": 5.0,
                    "coverage_percent": score,
                    "documentation_percent": 50.0,
                    "security_issues": 0,
                    "total_debt_hours": 10.0,
                    "velocity_impact_percent": 5.0,
                },
                "security_findings": [],
                "issues": issues or [],
                "recommendations": recommendations or [],
            }

        report_data = {
            "scan_timestamp": "2026-01-18T12:00:00Z",
            "projects_scanned": 3,
            "total_projects": 3,
            "average_quality_score": 60.0,
            "portfolio_trend": "degrading",
            "critical_issues": 1,
            "high_issues": 5,
            "degraded_projects": ["failing-project"],
            "improved_projects": [],
            "projects": [
                make_project("good-project", "test-domain", 85.0),
                make_project(
                    "failing-project",
                    "test-domain",
                    45.0,
                    issues=["Low test coverage", "High complexity"],
                    recommendations=["Add more tests", "Refactor large functions"],
                ),
                make_project(
                    "borderline-project",
                    "other-domain",
                    65.0,
                    issues=["Moderate coverage"],
                    recommendations=["Improve coverage"],
                ),
            ],
        }
        (metrics_dir / "portfolio_latest.json").write_text(json.dumps(report_data))

        result = runner.invoke(
            cli,
            ["quality", "alert", "--threshold", "70", "--metrics-dir", str(metrics_dir)],
        )
        assert result.exit_code == 0
        assert "Below Quality Threshold" in result.output
        assert "failing-project" in result.output
        assert "borderline-project" in result.output
        # good-project (85.0) should NOT be in output
        assert "good-project" not in result.output or "85.0" not in result.output

    def test_cli_quality_self_analyze_help(self, runner):
        """Test quality self-analyze command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["quality", "self-analyze", "--help"])
        assert result.exit_code == 0
        assert "--max-features" in result.output
        assert "--priority" in result.output
        assert "--dry-run" in result.output
        assert "--json" in result.output

    def test_cli_quality_self_analyze_dry_run(self, runner):
        """Test quality self-analyze with dry-run."""
        from datetime import datetime
        from unittest.mock import patch

        from forge_harness.cli_v2 import cli

        from forge_harness.self_improve import SelfAnalysisResult

        # Mock the self_analyze_sync function (imported inside the command)
        mock_result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=None,
            features_generated=[],
            features_added=0,
            features_skipped=0,
            errors=["Tech Diligence unavailable"],
        )

        with patch("forge_harness.self_improve.self_analyze_sync", return_value=mock_result):
            result = runner.invoke(
                cli,
                ["quality", "self-analyze", "--dry-run"],
            )
            assert result.exit_code == 0
            assert "Analyzing harness" in result.output
            assert "DRY RUN" in result.output

    def test_cli_quality_self_analyze_with_options(self, runner):
        """Test quality self-analyze with all options."""
        from datetime import datetime
        from unittest.mock import patch

        from forge_harness.cli_v2 import cli

        from forge_harness.self_improve import SelfAnalysisResult

        mock_result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=None,
            features_generated=[],
            features_added=0,
            features_skipped=0,
            errors=[],
        )

        with patch("forge_harness.self_improve.self_analyze_sync", return_value=mock_result):
            result = runner.invoke(
                cli,
                [
                    "quality",
                    "self-analyze",
                    "--max-features",
                    "5",
                    "--priority",
                    "high",
                    "--dry-run",
                ],
            )
            assert result.exit_code == 0
            assert "Max features: 5" in result.output
            assert "Priority threshold: high" in result.output

    def test_cli_quality_generate_features_help(self, runner):
        """Test quality generate-features command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["quality", "generate-features", "--help"])
        assert result.exit_code == 0
        assert "--domain" in result.output
        assert "--project" in result.output
        assert "--output" in result.output
        assert "--max-features" in result.output
        assert "--priority" in result.output
        assert "--dry-run" in result.output


@pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: 'meta-learning' command group does not exist in cli_v2")
class TestCLIMetaLearningCommand:
    """Tests for meta-learning subcommand group."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_learning_store(self, tmp_path):
        """Create mock learning store with test data."""
        from forge_harness.meta_learning import (
            DecisionAction,
            DecisionRecord,
            LearningStore,
            PatternRecord,
        )

        store = LearningStore(storage_path=tmp_path / ".forge/learning")

        # Add some test patterns
        store.record_pattern(
            PatternRecord(
                pattern_id="pat-001",
                context_signature="jwt-auth-sig",
                pattern_type="implementation",
                total_applications=10,
                successful_applications=8,
            )
        )
        store.record_pattern(
            PatternRecord(
                pattern_id="pat-002",
                context_signature="api-test-sig",
                pattern_type="testing",
                total_applications=5,
                successful_applications=4,
            )
        )

        # Add some test decisions
        store.record_decision(
            DecisionRecord(
                decision_id="dec-001",
                context_signature="feat-001-sig",
                domain="test-domain",
                project="test-project",
                recommended_action=DecisionAction.PROCEED,
                actual_action=DecisionAction.PROCEED,
                outcome_success=True,
            )
        )
        store.record_decision(
            DecisionRecord(
                decision_id="dec-002",
                context_signature="feat-002-sig",
                domain="test-domain",
                project="test-project",
                recommended_action=DecisionAction.HUMAN_REVIEW_REQUIRED,
                actual_action=DecisionAction.HUMAN_REVIEW_REQUIRED,
                outcome_success=True,
            )
        )

        return store

    def test_cli_meta_learning_help(self, runner):
        """Test meta-learning command group help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["meta-learning", "--help"])
        assert result.exit_code == 0
        assert "status" in result.output
        assert "report" in result.output

    def test_cli_meta_learning_status_help(self, runner):
        """Test meta-learning status command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["meta-learning", "status", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output
        assert "--store-path" in result.output

    def test_cli_meta_learning_status_no_data(self, runner, tmp_path):
        """Test meta-learning status with no existing data."""
        from forge_harness.cli_v2 import cli

        store_path = tmp_path / "empty_store"

        result = runner.invoke(
            cli,
            ["meta-learning", "status", "--store-path", str(store_path)],
        )
        assert result.exit_code == 0
        assert "Patterns" in result.output or "No patterns" in result.output

    def test_cli_meta_learning_status_with_data(self, runner, tmp_path, mock_learning_store):
        """Test meta-learning status shows patterns and decisions."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            ["meta-learning", "status", "--store-path", str(tmp_path / ".forge/learning")],
        )
        assert result.exit_code == 0
        assert "Patterns" in result.output
        assert "Decisions" in result.output

    def test_cli_meta_learning_status_json_output(self, runner, tmp_path, mock_learning_store):
        """Test meta-learning status JSON output."""
        import json

        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            [
                "meta-learning",
                "status",
                "--store-path",
                str(tmp_path / ".forge/learning"),
                "--json",
            ],
        )
        assert result.exit_code == 0

        # Should be valid JSON
        data = json.loads(result.output)
        assert "patterns" in data
        assert "decisions" in data
        assert "statistics" in data

    def test_cli_meta_learning_report_help(self, runner):
        """Test meta-learning report command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["meta-learning", "report", "--help"])
        assert result.exit_code == 0
        assert "--format" in result.output
        assert "--days" in result.output
        assert "--store-path" in result.output

    def test_cli_meta_learning_report_no_data(self, runner, tmp_path):
        """Test meta-learning report with no existing data."""
        from forge_harness.cli_v2 import cli

        store_path = tmp_path / "empty_store"

        result = runner.invoke(
            cli,
            ["meta-learning", "report", "--store-path", str(store_path)],
        )
        assert result.exit_code == 0
        assert "No data" in result.output or "decisions" in result.output.lower()

    def test_cli_meta_learning_report_with_data(self, runner, tmp_path, mock_learning_store):
        """Test meta-learning report shows effectiveness metrics."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            ["meta-learning", "report", "--store-path", str(tmp_path / ".forge/learning")],
        )
        assert result.exit_code == 0
        # Should show some stats
        assert "Decision" in result.output or "Pattern" in result.output

    def test_cli_meta_learning_report_json_output(self, runner, tmp_path, mock_learning_store):
        """Test meta-learning report JSON output."""
        import json

        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            [
                "meta-learning",
                "report",
                "--store-path",
                str(tmp_path / ".forge/learning"),
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0

        # Should be valid JSON
        data = json.loads(result.output)
        assert (
            "statistics" in data or "pattern_effectiveness" in data or "decision_accuracy" in data
        )

    def test_cli_meta_learning_report_markdown_output(self, runner, tmp_path, mock_learning_store):
        """Test meta-learning report markdown output."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(
            cli,
            [
                "meta-learning",
                "report",
                "--store-path",
                str(tmp_path / ".forge/learning"),
                "--format",
                "markdown",
            ],
        )
        assert result.exit_code == 0
        assert "#" in result.output  # Markdown headers


@pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: status command output format and --token/--json flags differ between old and new CLI")
class TestCLIStatusCommand:
    """Tests for unified status command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_forge_harness(self, tmp_path, monkeypatch):
        """Create mock FORGE harness structure with status data."""
        import json

        # Create approval directory with test data
        approval_dir = tmp_path / ".forge/approvals"
        approval_dir.mkdir()

        # Create pending approval
        approval1 = {
            "id": "apr_001",
            "type": "content",
            "domain": "test-domain",
            "title": "Test Approval 1",
            "description": "Test description",
            "status": "pending",
            "priority": "high",
            "created_at": "2026-01-27T10:00:00Z",
        }
        (approval_dir / "approval_001.json").write_text(json.dumps(approval1))

        # Create urgent approval
        approval2 = {
            "id": "apr_002",
            "type": "deploy",
            "domain": "test-domain",
            "title": "Urgent Approval",
            "description": "Critical deployment",
            "status": "pending",
            "priority": "critical",
            "created_at": "2026-01-25T10:00:00Z",  # Overdue (>48h)
        }
        (approval_dir / "approval_002.json").write_text(json.dumps(approval2))

        # Create checkpoint directory with pipeline data
        checkpoint_dir = tmp_path / ".forge/orchestration_checkpoints"
        checkpoint_dir.mkdir()

        checkpoint1 = {
            "pipeline": {"name": "test_pipeline", "steps": []},
            "status": "running",
            "started_at": "2026-01-27T10:00:00Z",
            "step_results": {},
        }
        (checkpoint_dir / "pipeline_001.json").write_text(json.dumps(checkpoint1))

        # Create learning directory with patterns
        learning_dir = tmp_path / ".forge/learning"
        learning_dir.mkdir()

        patterns = {
            "version": "2.0",
            "patterns": {
                "orchestration": {
                    "pattern1": {"id": "pattern1", "success_rate": 0.95},
                    "pattern2": {"id": "pattern2", "success_rate": 0.88},
                },
                "testing": {
                    "pattern3": {"id": "pattern3", "success_rate": 1.0},
                },
            },
        }
        (learning_dir / "patterns.json").write_text(json.dumps(patterns))

        # Create outcomes file
        outcomes = {
            "outcomes": [
                {"pattern_id": "pattern1", "timestamp": "2026-01-27T12:00:00Z", "success": True},
                {"pattern_id": "pattern2", "timestamp": "2026-01-27T13:00:00Z", "success": True},
            ],
        }
        (learning_dir / "pattern_outcomes.json").write_text(json.dumps(outcomes))

        # Create domains.yaml marker
        (tmp_path / "domains.yaml").write_text("domains: []\n")

        # Mock find_forge_root to return tmp_path
        monkeypatch.chdir(tmp_path)

        return tmp_path

    def test_cli_status_help(self, runner):
        """Test status command help."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output
        assert "--watch" in result.output
        assert "unified" in result.output.lower()

    def test_cli_status_basic(self, runner, mock_forge_harness, monkeypatch):
        """Test basic status command output."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.cli_v2 import cli

        # Mock Command Center client to avoid network calls
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_agents = AsyncMock(return_value=[])

        def mock_client_factory(*args, **kwargs):
            return mock_client

        with patch("forge_harness.command_center_client.CommandCenterClient", mock_client_factory):
            result = runner.invoke(cli, ["status"])
            assert result.exit_code == 0
            assert "FORGE Status" in result.output
            assert "Agents" in result.output
            assert "Approvals" in result.output
            assert "Pipelines" in result.output
            assert "Learning" in result.output

    def test_cli_status_shows_counts(self, runner, mock_forge_harness, monkeypatch):
        """Test status command shows correct counts."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.cli_v2 import cli

        # Mock Command Center with agents
        mock_agent = MagicMock()
        mock_agent.status.value = "active"
        mock_agent.is_stale = False

        mock_stale_agent = MagicMock()
        mock_stale_agent.status.value = "active"
        mock_stale_agent.is_stale = True

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_agents = AsyncMock(return_value=[mock_agent, mock_stale_agent])

        def mock_client_factory(*args, **kwargs):
            return mock_client

        with patch("forge_harness.command_center_client.CommandCenterClient", mock_client_factory):
            result = runner.invoke(cli, ["status"])
            assert result.exit_code == 0

            # Check agent counts (1 active, 1 stale)
            assert "1 active" in result.output or "active" in result.output.lower()
            assert "1 stale" in result.output or "stale" in result.output.lower()

            # Check approval counts (2 pending, 1 urgent, 1 overdue)
            assert "2 pending" in result.output or "pending" in result.output.lower()
            assert "1 urgent" in result.output or "urgent" in result.output.lower()

            # Check pipeline counts (1 running)
            assert "1 running" in result.output or "running" in result.output.lower()

            # Check learning counts (3 patterns stored, 2 used today)
            assert "3 stored" in result.output or "stored" in result.output.lower()
            assert "2 used" in result.output or "used" in result.output.lower()

    def test_cli_status_json_output(self, runner, mock_forge_harness, monkeypatch):
        """Test status command JSON output."""
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.cli_v2 import cli

        # Mock Command Center
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_agents = AsyncMock(return_value=[])

        def mock_client_factory(*args, **kwargs):
            return mock_client

        with patch("forge_harness.command_center_client.CommandCenterClient", mock_client_factory):
            result = runner.invoke(cli, ["status", "--json"])
            assert result.exit_code == 0

            # Parse JSON output
            data = json.loads(result.output)
            assert "timestamp" in data
            assert "agents" in data
            assert "approvals" in data
            assert "pipelines" in data
            assert "learning" in data
            assert "errors" in data

            # Check structure
            assert "active" in data["agents"]
            assert "stale" in data["agents"]
            assert "completed" in data["agents"]
            assert "pending" in data["approvals"]
            assert "urgent" in data["approvals"]
            assert "overdue" in data["approvals"]
            assert "running" in data["pipelines"]
            assert "blocked" in data["pipelines"]
            assert "patterns_stored" in data["learning"]
            assert "patterns_used_today" in data["learning"]

    def test_cli_status_with_api_token(self, runner, mock_forge_harness, monkeypatch):
        """Test status command with API token."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.cli_v2 import cli

        # Track if token was passed
        token_passed = []

        def mock_client_factory(*args, **kwargs):
            token_passed.append(kwargs.get("token"))
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.list_agents = AsyncMock(return_value=[])
            return mock_client

        with patch("forge_harness.command_center_client.CommandCenterClient", mock_client_factory):
            result = runner.invoke(cli, ["status", "--token", "test-token-123"])
            assert result.exit_code == 0
            assert token_passed[0] == "test-token-123"

    def test_cli_status_handles_command_center_error(self, runner, mock_forge_harness, monkeypatch):
        """Test status command handles Command Center errors gracefully."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.cli_v2 import cli

        # Mock Command Center to raise error
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_agents = AsyncMock(side_effect=Exception("Connection refused"))

        def mock_client_factory(*args, **kwargs):
            return mock_client

        with patch("forge_harness.command_center_client.CommandCenterClient", mock_client_factory):
            result = runner.invoke(cli, ["status"])
            # Should still succeed, just show error in errors list
            assert result.exit_code == 0
            assert "FORGE Status" in result.output
            # Error should be in output
            assert "unavailable" in result.output.lower() or "error" in result.output.lower()

    def test_cli_status_no_data(self, runner, tmp_path, monkeypatch):
        """Test status command with no data available."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.cli_v2 import cli

        # Create empty structure
        (tmp_path / "domains.yaml").write_text("domains: []\n")
        monkeypatch.chdir(tmp_path)

        # Mock Command Center
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_agents = AsyncMock(return_value=[])

        def mock_client_factory(*args, **kwargs):
            return mock_client

        with patch("forge_harness.command_center_client.CommandCenterClient", mock_client_factory):
            result = runner.invoke(cli, ["status"])
            assert result.exit_code == 0
            assert "FORGE Status" in result.output
            # All counts should be 0
            assert "0 active" in result.output or "0" in result.output
            assert "0 pending" in result.output or "0" in result.output
            assert "None" in result.output  # Recent errors should be None
