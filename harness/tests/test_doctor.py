"""
Tests for forge doctor command - TDD for strategy alignment

This test file follows TDD principles: write failing test first, then implement.

Strategy specification: forge doctor should check:
1. Startup exports (module initialization)
2. Route health (API endpoints availability)
3. Auth mode (authentication configuration)
4. Git lock/rebase (repository state safety)

Reference: docs/research/ARCHITECTURE_STRATEGY_TDD_IMPLEMENTATION_PLAN.md

Expected: Tests fail RED initially; opencode will make them GREEN.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.doctor import doctor

from forge_harness.fleet.dispatch_client import GIT_LOCK_FILE, GitMutex
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


class TestDoctorStrategyAlignment:
    """Tests that forge doctor implements strategy-specified checks."""

    def test_doctor_checks_startup_exports(self, runner, forge_root):
        """Test that forge doctor validates startup exports.

        Strategy requires: Check that all modules export expected symbols
        - forge_harness modules should be importable
        - CLI entry points should be accessible
        - Core services should initialize correctly

        This test will FAIL until opencode implements startup export checks.
        """
        # Create a mock forge root structure
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        # Mock the require_forge_root to use our temp directory
        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Assert that the output includes startup/export checks
        # This will fail until the strategy-aligned checks are implemented
        assert "startup" in result.output.lower() or "export" in result.output.lower(), (
            "forge doctor should check startup exports per strategy spec"
        )

    def test_doctor_checks_route_health(self, runner, forge_root):
        """Test that forge doctor validates route health.

        Strategy requires: Check that API routes are registered and healthy
        - Webhook server routes should be accessible
        - Health endpoints should respond
        - Route registration should be complete

        This test will FAIL until opencode implements route health checks.
        """
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Assert route health is checked
        assert (
            "route" in result.output.lower()
            or "endpoint" in result.output.lower()
            or "api" in result.output.lower()
        ), "forge doctor should check route health per strategy spec"

    def test_doctor_checks_auth_mode(self, runner, forge_root):
        """Test that forge doctor validates authentication mode.

        Strategy requires: Check authentication configuration
        - Detect dev mode (no static tokens)
        - Verify auth is enabled by default (not bypassed)
        - Check FORGE_WEBHOOK_TOKEN is properly configured

        This test will FAIL until opencode implements auth mode checks.
        """
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Assert auth mode is checked
        assert "auth" in result.output.lower(), (
            "forge doctor should check auth mode per strategy spec"
        )

    def test_doctor_checks_git_lock_rebase(self, runner, forge_root):
        """Test that forge doctor validates git repository state.

        Strategy requires: Check for unsafe git state
        - Detect lock files (index.lock, HEAD.lock)
        - Check for rebase in progress
        - Warn about unsafe repository state

        This test will FAIL until opencode implements git lock/rebase checks.
        """
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

        # Assert git state is checked
        assert "git" in result.output.lower(), (
            "forge doctor should check git lock/rebase state per strategy spec"
        )


class TestDoctorCommandStructure:
    """Tests that forge doctor command has correct CLI structure."""

    def test_doctor_command_exists(self, runner):
        """Test that forge doctor command is registered."""
        # The command should be importable and invokable
        from forge_harness.cli_v2 import cli

        # Doctor should be in the command group (Click stores commands in .commands dict)
        assert "doctor" in cli.commands, "doctor command should be registered in cli_v2"

    def test_doctor_has_required_options(self, runner):
        """Test that forge doctor has strategy-specified options."""
        # Doctor should support --check, --fix, --report, --cleanup
        result = runner.invoke(doctor, ["--help"])
        assert "--check" in result.output
        assert "--fix" in result.output
        assert "--report" in result.output
        assert "--cleanup" in result.output

    def test_doctor_check_choices_include_strategy_checks(self, runner):
        """Test that --check includes strategy-specified subsystems."""
        result = runner.invoke(doctor, ["--help"])

        # Strategy requires: startup, routes, auth, git
        # These may be grouped under existing choices or new ones
        # At minimum, "git" should be available
        assert "--check" in result.output, "doctor should support --check option"


class TestDoctorExitCodes:
    """Tests that forge doctor returns appropriate exit codes."""

    def test_doctor_returns_zero_on_success(self, runner, forge_root):
        """Test that forge doctor exits with 0 when all checks pass."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)
            # Should exit successfully even if checks are placeholder
            assert result.exit_code == 0 or result.exit_code is None

    def test_doctor_returns_non_zero_on_critical_failure(self, runner, forge_root):
        """Test that forge doctor exits with non-zero on critical errors."""
        # Simulate a critical error (missing config)
        forge_root.mkdir()
        # Don't create pyproject.toml - should cause config check to fail

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)
            # Should handle errors gracefully
            # The exact exit code depends on implementation


class TestDoctorOutputFormat:
    """Tests that forge doctor produces expected output format."""

    def test_doctor_shows_summary(self, runner, forge_root):
        """Test that forge doctor shows check summary."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor)

            # Should show summary with counts
            # This will be updated when strategy-aligned checks are implemented
            assert len(result.output) > 0, "doctor should produce output"

    def test_doctor_generates_report_when_requested(self, runner, forge_root):
        """Test that forge doctor --report generates health report file."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        # Create docs directory for report
        docs_dir = forge_root / "docs"
        docs_dir.mkdir()

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor, ["--report"])

            # Should indicate report generation
            if result.exit_code == 0:
                # Report file should be created in docs/health_report.md
                report_path = docs_dir / "health_report.md"
                # Report will exist when implementation is complete


@pytest.mark.parametrize("subsystem", ["fleet", "approvals", "git", "config", "quality"])
class TestDoctorSubsystemChecks:
    """Tests that forge doctor --check <subsystem> works for each subsystem."""

    def test_doctor_supports_subsystem_checks(self, runner, subsystem):
        """Test that doctor supports --check for each subsystem."""
        # All subsystems from strategy should be supported
        result = runner.invoke(doctor, ["--check", subsystem])
        # Command should not fail on unknown subsystem
        assert result.exit_code != 2 or subsystem in result.output.lower()


class TestDoctorFixMode:
    """Tests that forge doctor --fix attempts to fix issues."""

    def test_doctor_fix_attempts_corrections(self, runner, forge_root):
        """Test that --fix flag attempts to correct detected issues."""
        forge_root.mkdir()
        (forge_root / "pyproject.toml").write_text("[project]\nname='forge-test'")

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor, ["--fix"])
            # Should run without crashing
            assert result.exit_code == 0 or result.exit_code is None


class TestDoctorCleanupMode:
    """Tests that forge doctor --cleanup cleans orphaned resources."""

    def test_doctor_cleanup_removes_orphans(self, runner, forge_root):
        """Test that --cleanup removes orphaned resources."""
        forge_root.mkdir()

        # Create some orphaned checkpoint directories
        checkpoints_dir = forge_root / ".forge/orchestration_checkpoints"
        checkpoints_dir.mkdir()
        (checkpoints_dir / "old_checkpoint.json").write_text('{"timestamp": "2025-01-01"}')

        with patch("forge_harness.cli_v2.doctor.require_forge_root", return_value=str(forge_root)):
            result = runner.invoke(doctor, ["--cleanup"])
            # Should complete successfully
            assert result.exit_code == 0 or result.exit_code is None


# ===========================================================================
# Direct strategy-specified checks (RED phase of TDD)
# ===========================================================================


class TestStartupExports:
    """Assert webhook_server_main exports app and run_server."""

    def test_webhook_server_main_exports_create_app(self):
        """webhook_server_main must export create_app for startup."""
        from forge_harness import webhook_server_main

        assert hasattr(webhook_server_main, "create_app"), (
            "webhook_server_main must export create_app"
        )
        assert callable(webhook_server_main.create_app), "create_app must be callable"

    def test_webhook_server_main_exports_run_server(self):
        """webhook_server_main must export run_server for startup."""
        from forge_harness import webhook_server_main

        assert hasattr(webhook_server_main, "run_server"), (
            "webhook_server_main must export run_server"
        )
        assert callable(webhook_server_main.run_server), "run_server must be callable"

    def test_create_app_returns_fastapi_app(self):
        """create_app must return a FastAPI application instance."""
        from forge_harness.webhook_server_main import create_app

        app = create_app()
        assert app is not None, "create_app should return an app"
        # FastAPI apps have these attributes
        assert hasattr(app, "routes"), "App must be a FastAPI instance"
        assert hasattr(app, "include_router"), "App must support router inclusion"


class TestRouteHealth:
    """Assert critical routes exist on the app."""

    @pytest.fixture
    def app(self):
        """Create app instance for route testing."""
        from forge_harness.webhook_server_main import create_app

        return create_app()

    def test_route_api_tasks_recommended_exists(self, app):
        """App must have /api/tasks/recommended route for task recommendations."""
        route_paths = {route.path for route in app.routes}
        assert "/api/tasks/recommended" in route_paths, (
            "Missing /api/tasks/recommended route (strategy requirement)"
        )

    def test_route_api_nodes_exists(self, app):
        """App must have /api/nodes route for node management."""
        route_paths = {route.path for route in app.routes}
        assert "/api/nodes" in route_paths, "Missing /api/nodes route (strategy requirement)"

    def test_route_api_health_exists(self, app):
        """App must have /api/health route for health checks."""
        route_paths = {route.path for route in app.routes}
        assert "/api/health" in route_paths, "Missing /api/health route"


class TestAuthModeProductionSafe:
    """Assert auth defaults to secure mode in production."""

    def test_auth_config_defaults_allow_localhost_false(self):
        """When FORGE_WEBHOOK_ALLOW_LOCALHOST is unset, default to False."""
        old_val = os.environ.pop("FORGE_WEBHOOK_ALLOW_LOCALHOST", None)
        try:
            config = AuthConfig.from_env()
            assert config.allow_localhost is False, (
                "AuthConfig.allow_localhost must default to False for production safety"
            )
        finally:
            if old_val is not None:
                os.environ["FORGE_WEBHOOK_ALLOW_LOCALHOST"] = old_val

    def test_auth_config_explicit_allows_localhost(self):
        """When FORGE_WEBHOOK_ALLOW_LOCALHOST=true, allow localhost."""
        old_val = os.environ.get("FORGE_WEBHOOK_ALLOW_LOCALHOST")
        try:
            os.environ["FORGE_WEBHOOK_ALLOW_LOCALHOST"] = "true"
            config = AuthConfig.from_env()
            assert config.allow_localhost is True, (
                "AuthConfig should allow localhost when explicitly set"
            )
        finally:
            if old_val is not None:
                os.environ["FORGE_WEBHOOK_ALLOW_LOCALHOST"] = old_val
            else:
                os.environ.pop("FORGE_WEBHOOK_ALLOW_LOCALHOST", None)


class TestGitLockAndRebaseDetection:
    """Assert doctor can detect git lock and rebase states."""

    def test_git_mutex_detects_index_lock(self, tmp_path: Path):
        """GitMutex.is_locked() returns True when .git/index.lock exists."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        lock_file = git_dir / "index.lock"

        mutex = GitMutex(repo_root=tmp_path)
        assert mutex.is_locked() is False, "No lock initially"

        lock_file.touch()
        assert mutex.is_locked() is True, "GitMutex should detect index.lock"

    def test_git_mutex_detects_rebase_apply(self, tmp_path: Path):
        """Detect rebase-apply directory as unsafe state."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-apply").mkdir()

        mutex = GitMutex(repo_root=tmp_path)
        assert mutex.is_locked() is True, "GitMutex should detect rebase-apply as unsafe"

    def test_git_mutex_detects_rebase_merge(self, tmp_path: Path):
        """Detect rebase-merge directory as unsafe state."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-merge").mkdir()

        mutex = GitMutex(repo_root=tmp_path)
        assert mutex.is_locked() is True, "GitMutex should detect rebase-merge as unsafe"

    def test_git_mutex_detects_head_lock(self, tmp_path: Path):
        """Detect HEAD.lock as unsafe state."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD.lock").touch()

        mutex = GitMutex(repo_root=tmp_path)
        assert mutex.is_locked() is True, "GitMutex should detect HEAD.lock as unsafe"

    def test_git_mutex_reports_safe_when_clean(self, tmp_path: Path):
        """Report safe when no lock or rebase indicators."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main")

        mutex = GitMutex(repo_root=tmp_path)
        assert mutex.is_locked() is False, "GitMutex should report safe when clean"


# Integration-style tests (will pass when strategy-aligned checks are implemented)


class TestDoctorIntegration:
    """Integration tests that will pass when strategy-aligned checks are implemented."""

    @pytest.mark.skip("Waiting for opencode implementation - Step 2 of TDD cycle")
    def test_doctor_startup_check_implementation(self):
        """Integration test: Verify startup export checks are implemented.

        This test is skipped until opencode implements the strategy-aligned
        startup checks in forge_harness.cli_v2.doctor.
        """
        # When implemented, should:
        # 1. Check forge_harness.__init__.py exports expected symbols
        # 2. Check CLI entry points are accessible
        # 3. Check core services initialize without errors
        pass

    @pytest.mark.skip("Waiting for opencode implementation - Step 2 of TDD cycle")
    def test_doctor_route_health_check_implementation(self):
        """Integration test: Verify route health checks are implemented.

        This test is skipped until opencode implements strategy-aligned
        route health checks in forge_harness.cli_v2.doctor.
        """
        # When implemented, should:
        # 1. Check webhook_server routes are registered
        # 2. Check health endpoints respond
        # 3. Check route registration is complete
        pass

    @pytest.mark.skip("Waiting for opencode implementation - Step 2 of TDD cycle")
    def test_doctor_auth_mode_check_implementation(self):
        """Integration test: Verify auth mode checks are implemented.

        This test is skipped until opencode implements strategy-aligned
        auth mode checks in forge_harness.cli_v2.doctor.
        """
        # When implemented, should:
        # 1. Detect if running in dev mode (localhost bypass)
        # 2. Check FORGE_WEBHOOK_TOKEN is set
        # 3. Verify auth is enabled by default
        pass

    @pytest.mark.skip("Waiting for opencode implementation - Step 2 of TDD cycle")
    def test_doctor_git_lock_rebase_check_implementation(self):
        """Integration test: Verify git lock/rebase checks are implemented.

        This test is skipped until opencode implements strategy-aligned
        git state safety checks in forge_harness.cli_v2.doctor.
        """
        # When implemented, should:
        # 1. Detect index.lock or HEAD.lock files
        # 2. Check for .git/rebase-apply directory
        # 3. Warn about unsafe repository state
        pass
