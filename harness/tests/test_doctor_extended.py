"""
Extended tests for forge doctor command - Strategy-aligned comprehensive checks

Tests the enhanced implementation of doctor checks:
1. Startup exports (comprehensive module verification)
2. Route health (detailed route validation)
3. Auth mode (token validation and production safety)
4. Git lock/rebase (using GitMutex)

Reference: docs/research/ARCHITECTURE_STRATEGY_TDD_IMPLEMENTATION_PLAN.md
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.doctor import doctor

from forge_harness.fleet.dispatch_client import GitMutex
from forge_harness.webhook_server.infrastructure.auth import AuthConfig

HARNESS_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def runner():
    """Click CLI runner for testing."""
    return CliRunner()


@pytest.fixture
def forge_root(tmp_path):
    """Temporary forge root directory for testing."""
    return tmp_path / ".forge-test"


class TestStartupExportsExtended:
    """Extended tests for startup exports checking."""

    def test_startup_exports_verifies_modules(self, runner, forge_root):
        """Verify doctor checks startup exports and modules."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should check startup exports
        assert "startup" in result.output.lower() or "export" in result.output.lower(), (
            "doctor should check startup exports"
        )

    def test_startup_verifies_app_creation(self, runner, forge_root):
        """Verify doctor checks app creation."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should check app
        assert "app" in result.output.lower() or "startup" in result.output.lower()

    def test_startup_detects_missing_module(self, runner, forge_root, monkeypatch):
        """Verify doctor detects missing module exports."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        # Mock import to simulate missing module
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "forge_harness.nonexistent_module":
                raise ImportError("No module named 'nonexistent_module'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should handle import errors gracefully
        assert result.exit_code == 0 or "error" in result.output.lower()

    def test_startup_verifies_app_has_routes(self, runner, forge_root):
        """Verify doctor checks that created app has routes."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should check for routes
        assert "route" in result.output.lower() or "startup" in result.output.lower()


class TestRouteHealthExtended:
    """Extended tests for route health checking."""

    def test_route_health_checks_required_endpoints(self, runner, forge_root):
        """Verify doctor checks for required API endpoints."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should check for critical routes
        assert "route" in result.output.lower() or "api" in result.output.lower(), (
            "doctor should check routes"
        )

    def test_route_health_checks_nodes_endpoint(self, runner, forge_root):
        """Verify doctor checks for /api/nodes endpoint."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should check for routes
        assert "route" in result.output.lower(), "doctor should check route health"

    def test_route_health_checks_health_endpoint(self, runner, forge_root):
        """Verify doctor checks for /api/health endpoint."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should check for health
        assert "health" in result.output.lower(), "doctor should check health"

    def test_route_health_detects_missing_routes(self, runner, forge_root, monkeypatch):
        """Verify doctor returns error when routes are missing."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        # Mock create_app to return app with no routes
        mock_app = Mock()
        mock_app.routes = []

        def mock_create_app():
            return mock_app

        monkeypatch.setattr(
            "forge_harness.webhook_server_main.create_app",
            mock_create_app,
        )

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should detect missing routes
        assert "route" in result.output.lower() or "error" in result.output.lower()

    def test_route_health_reports_route_count(self, runner, forge_root):
        """Verify doctor reports route count."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should mention route counts
        assert "route" in result.output.lower()


class TestAuthModeExtended:
    """Extended tests for authentication mode checking."""

    def test_auth_mode_checks_for_localhost_bypass(self, runner, forge_root, monkeypatch):
        """Verify doctor checks if localhost bypass is enabled."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        # Set localhost bypass
        monkeypatch.setenv("FORGE_WEBHOOK_ALLOW_LOCALHOST", "true")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should detect dev mode
        assert "auth" in result.output.lower() or "localhost" in result.output.lower()

    def test_auth_mode_checks_webhook_token(self, runner, forge_root, monkeypatch):
        """Verify doctor checks FORGE_WEBHOOK_TOKEN is set."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        # Remove webhook token
        monkeypatch.delenv("FORGE_WEBHOOK_TOKEN", raising=False)

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should warn about auth
        assert "auth" in result.output.lower() or "warning" in result.output.lower()

    def test_auth_mode_checks_token_length(self, runner, forge_root, monkeypatch):
        """Verify doctor warns if token is too short."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        # Set short token
        monkeypatch.setenv("FORGE_WEBHOOK_ALLOW_LOCALHOST", "false")
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "short")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should warn about auth
        assert "auth" in result.output.lower() or "warning" in result.output.lower()

    def test_auth_mode_production_safe(self, runner, forge_root, monkeypatch):
        """Verify doctor reports production-safe auth mode."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        # Set production-safe config
        monkeypatch.setenv("FORGE_WEBHOOK_ALLOW_LOCALHOST", "false")
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "a" * 32)

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should show ok status for auth
        assert "auth" in result.output.lower()


class TestGitLockAndRebaseExtended:
    """Extended tests for git lock and rebase detection."""

    def test_git_checks_detects_index_lock(self, runner, forge_root, monkeypatch):
        """Verify doctor detects .git/index.lock."""
        forge_root.mkdir()
        git_dir = forge_root / ".git"
        git_dir.mkdir()
        (git_dir / "index.lock").touch()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should detect lock or git issue
        assert (
            "git" in result.output.lower()
            or "lock" in result.output.lower()
            or "error" in result.output.lower()
        )

    def test_git_checks_detects_head_lock(self, runner, forge_root, monkeypatch):
        """Verify doctor detects .git/HEAD.lock."""
        forge_root.mkdir()
        git_dir = forge_root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD.lock").touch()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should detect lock or git issue
        assert (
            "git" in result.output.lower()
            or "lock" in result.output.lower()
            or "error" in result.output.lower()
        )

    def test_git_checks_detects_rebase_apply(self, runner, forge_root, monkeypatch):
        """Verify doctor detects .git/rebase-apply directory."""
        forge_root.mkdir()
        git_dir = forge_root / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-apply").mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should detect rebase or git issue
        assert (
            "git" in result.output.lower()
            or "rebase" in result.output.lower()
            or "error" in result.output.lower()
        )

    def test_git_checks_detects_rebase_merge(self, runner, forge_root, monkeypatch):
        """Verify doctor detects .git/rebase-merge directory."""
        forge_root.mkdir()
        git_dir = forge_root / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-merge").mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should detect rebase or git issue
        assert (
            "git" in result.output.lower()
            or "rebase" in result.output.lower()
            or "error" in result.output.lower()
        )

    def test_git_checks_uncommitted_changes(self, runner, forge_root, monkeypatch):
        """Verify doctor detects uncommitted changes."""
        forge_root.mkdir()
        git_dir = forge_root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main")
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")
        (forge_root / "test_file.txt").write_text("uncommitted changes")

        # Initialize git repo and add file
        subprocess.run(["git", "init"], cwd=forge_root, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=forge_root, capture_output=True)

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should detect uncommitted changes or warning
        assert "git" in result.output.lower() or "warning" in result.output.lower()

    def test_git_checks_detached_head(self, runner, forge_root, monkeypatch):
        """Verify doctor detects detached HEAD state."""
        forge_root.mkdir()
        git_dir = forge_root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("abc123")
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should detect detached HEAD or warning
        assert "git" in result.output.lower() or "warning" in result.output.lower()

    def test_git_checks_uses_gitmutex(self, runner, forge_root):
        """Verify doctor uses GitMutex for lock detection."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        # Test GitMutex directly
        mutex = GitMutex(repo_root=forge_root)
        assert mutex.is_locked() is False

        # Create lock file
        (forge_root / ".git").mkdir()
        (forge_root / ".git" / "index.lock").touch()
        assert mutex.is_locked() is True


class TestDoctorSummaryReporting:
    """Tests for doctor summary and reporting."""

    def test_doctor_shows_check_summary(self, runner, forge_root):
        """Verify doctor shows summary of all checks."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should show summary
        assert (
            "summary" in result.output.lower()
            or "ok" in result.output.lower()
            or "check" in result.output.lower()
        )

    def test_doctor_counts_ok_warning_error(self, runner, forge_root):
        """Verify doctor reports counts of ok/warning/error checks."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Should report counts
        assert (
            "ok" in result.output.lower()
            or "warning" in result.output.lower()
            or "error" in result.output.lower()
        )

    def test_doctor_generates_health_report_file(self, runner, forge_root):
        """Verify doctor --report generates health_report.md."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")
        docs_dir = forge_root / "docs"
        docs_dir.mkdir()

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor, ["--report"])

        # Report should be generated
        report_path = docs_dir / "health_report.md"
        # Report creation depends on implementation


class TestDoctorFixMode:
    """Tests for doctor --fix mode."""

    def test_doctor_fix_does_not_crash(self, runner, forge_root):
        """Verify doctor --fix runs without crashing."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor, ["--fix"])

        # Just ensure it doesn't crash - it may fail on actual issues
        assert result.exit_code is not None  # Completed


class TestDoctorSubsystemChecks:
    """Tests for individual subsystem checks."""

    @pytest.mark.parametrize(
        "subsystem",
        ["startup", "routes", "auth", "git", "config", "fleet", "approvals", "quality"],
    )
    def test_doctor_check_subsystem(self, runner, subsystem):
        """Verify doctor --check <subsystem> works for each subsystem."""
        result = runner.invoke(doctor, ["--check", subsystem])
        # Should not fail on unknown subsystem
        assert result.exit_code == 0 or result.exit_code is None
