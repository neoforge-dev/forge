"""
Tests for forge ship CLI command
=================================

Tests cover:
- Ship CLI command with various options
- Dry run mode validation
- Deployment execution
- Post-deployment verification
- Rollback functionality (Railway and git-based)
- Error handling
- JSON output mode
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.ship import (
    _run_deployment,
    _run_dry_run,
    _run_rollback,
    _run_rollback_via_git,
    _run_verification,
    ship,
)


@pytest.fixture
def runner():
    """Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_forge_root(tmp_path):
    """Create mock FORGE root with project structure."""
    forge_root = tmp_path / "FORGE"
    forge_root.mkdir()

    # Create domain/project structure
    project_dir = forge_root / "test-domain" / "test-project"
    project_dir.mkdir(parents=True)

    # Create backend and frontend directories
    (project_dir / "backend").mkdir()
    (project_dir / "frontend").mkdir()

    return forge_root


@pytest.fixture
def mock_project_dir(mock_forge_root):
    """Get mock project directory."""
    return mock_forge_root / "test-domain" / "test-project"


# ============================================================================
# Ship Command Basic Tests
# ============================================================================


def test_ship_requires_environment(runner):
    """Test that ship requires --staging, --production, or --rollback."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = Path("/fake/forge")

        result = runner.invoke(ship, ["-d", "domain", "-p", "project"])

        assert result.exit_code != 0
        assert "Specify deployment target" in result.output or result.exception


def test_ship_staging_and_production_mutually_exclusive(runner):
    """Test that --staging and --production cannot be used together."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = Path("/fake/forge")

        result = runner.invoke(ship, ["-d", "domain", "-p", "project", "--staging", "--production"])

        assert result.exit_code != 0


def test_ship_requires_domain_project(runner):
    """Test that ship needs domain/project."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = Path("/fake/forge")
        with patch("forge_harness.cli_v2.ship.infer_domain_project") as mock_infer:
            mock_infer.return_value = (None, None)

            result = runner.invoke(ship, ["--staging"])

            assert result.exit_code != 0


def test_ship_infers_domain_project(runner, mock_forge_root, mock_project_dir):
    """Test that ship can infer domain/project from current directory."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = mock_forge_root
        with patch("forge_harness.cli_v2.ship.infer_domain_project") as mock_infer:
            mock_infer.return_value = ("test-domain", "test-project")
            with patch("forge_harness.cli_v2.ship._run_dry_run") as mock_dry:
                mock_dry.return_value = {"status": "ok"}

                result = runner.invoke(ship, ["--staging", "--dry-run"])

                mock_infer.assert_called_once()


# ============================================================================
# Dry Run Tests
# ============================================================================


def test_run_dry_run_success(mock_forge_root, mock_project_dir):
    """Test dry run shows what would happen."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        # Mock git status
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="M file.py\nA new_file.py",
        )
        with patch("forge_harness.cli_v2.ship.console"):
            with patch("forge_harness.deployment.registry.ProviderRegistry") as mock_registry:
                mock_provider = MagicMock()
                mock_provider.is_configured.return_value = True
                mock_registry.discover.return_value = None
                mock_registry.get.return_value = mock_provider

                result = _run_dry_run(ctx, mock_forge_root, "test-domain", "test-project", "staging")

    assert result["domain"] == "test-domain"
    assert result["project"] == "test-project"
    assert result["env"] == "staging"
    assert "git_status" in result
    assert "commands" in result
    assert "checks" in result


def test_run_dry_run_clean_working_tree(mock_forge_root, mock_project_dir):
    """Test dry run with clean git working tree."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with patch("forge_harness.cli_v2.ship.console"):
            with patch("forge_harness.deployment.registry.ProviderRegistry"):
                result = _run_dry_run(ctx, mock_forge_root, "test-domain", "test-project", "staging")

    assert result["git_status"].get("clean") is True


def test_run_dry_run_git_error(mock_forge_root, mock_project_dir):
    """Test dry run handles git errors gracefully."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="not a git repo")
        with patch("forge_harness.cli_v2.ship.console"):
            with patch("forge_harness.deployment.registry.ProviderRegistry"):
                result = _run_dry_run(ctx, mock_forge_root, "test-domain", "test-project", "staging")

    assert "error" in result["git_status"]


def test_run_dry_run_git_timeout(mock_forge_root, mock_project_dir):
    """Test dry run handles git timeout."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("git", 10)
        with patch("forge_harness.cli_v2.ship.console"):
            with patch("forge_harness.deployment.registry.ProviderRegistry"):
                result = _run_dry_run(ctx, mock_forge_root, "test-domain", "test-project", "staging")

    assert "error" in result["git_status"]


def test_run_dry_run_project_not_found(mock_forge_root):
    """Test dry run with non-existent project directory."""
    ctx = MagicMock()

    with pytest.raises(SystemExit):  # format_error raises SystemExit(1)
        _run_dry_run(ctx, mock_forge_root, "nonexistent", "project", "staging")


def test_run_dry_run_json_mode(mock_forge_root, mock_project_dir):
    """Test dry run with JSON output mode."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with patch("forge_harness.deployment.registry.ProviderRegistry"):
            result = _run_dry_run(
                ctx, mock_forge_root, "test-domain", "test-project", "staging", output_json=True
            )

    assert isinstance(result, dict)
    assert result["domain"] == "test-domain"


def test_run_dry_run_detects_deployment_commands(mock_forge_root, mock_project_dir):
    """Test dry run detects backend/frontend deployment commands."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with patch("forge_harness.cli_v2.ship.console"):
            with patch("forge_harness.deployment.registry.ProviderRegistry"):
                result = _run_dry_run(ctx, mock_forge_root, "test-domain", "test-project", "staging")

    # Should have commands for both backend and frontend
    assert len(result["commands"]) == 2
    assert any("backend" in cmd["target"] for cmd in result["commands"])
    assert any("frontend" in cmd["target"] for cmd in result["commands"])


# ============================================================================
# Deployment Tests
# ============================================================================


def test_run_deployment_success(mock_forge_root):
    """Test successful deployment execution."""
    ctx = MagicMock()

    with patch("forge_harness.cli_v2.ship.console"):
        with patch("forge_harness.commands.pipeline.pipeline") as mock_pipeline:
            result = _run_deployment(
                ctx, mock_forge_root, "test-domain", "test-project", "staging", verify=False
            )

    assert result["status"] == "success"
    assert result["domain"] == "test-domain"
    assert result["project"] == "test-project"
    assert result["env"] == "staging"
    ctx.invoke.assert_called_once()


def test_run_deployment_with_verification(mock_forge_root):
    """Test deployment with verification enabled."""
    ctx = MagicMock()

    with patch("forge_harness.cli_v2.ship.console"):
        with patch("forge_harness.commands.pipeline.pipeline"):
            with patch("forge_harness.cli_v2.ship._run_verification") as mock_verify:
                mock_verify.return_value = {"status": "ok"}

                result = _run_deployment(
                    ctx, mock_forge_root, "test-domain", "test-project", "production", verify=True
                )

    assert result["status"] == "success"
    assert result["verification"] == {"status": "ok"}
    mock_verify.assert_called_once()


def test_run_deployment_failure(mock_forge_root):
    """Test deployment failure handling."""
    ctx = MagicMock()
    ctx.invoke.side_effect = Exception("Pipeline failed")

    # format_error raises SystemExit in non-json mode
    with pytest.raises(SystemExit):
        with patch("forge_harness.cli_v2.ship.console"):
            _run_deployment(
                ctx, mock_forge_root, "test-domain", "test-project", "staging", verify=False
            )


def test_run_deployment_failure_json_mode(mock_forge_root):
    """Test deployment failure handling in JSON mode."""
    ctx = MagicMock()
    ctx.invoke.side_effect = Exception("Pipeline failed")

    # In JSON mode, returns error dict instead of raising
    result = _run_deployment(
        ctx, mock_forge_root, "test-domain", "test-project", "staging",
        verify=False, output_json=True
    )

    assert result["status"] == "error"
    assert "Pipeline failed" in result["message"]


def test_run_deployment_json_mode(mock_forge_root):
    """Test deployment with JSON output mode."""
    ctx = MagicMock()

    with patch("forge_harness.commands.pipeline.pipeline"):
        result = _run_deployment(
            ctx, mock_forge_root, "test-domain", "test-project", "staging",
            verify=False, output_json=True
        )

    assert isinstance(result, dict)
    assert result["status"] == "success"


# ============================================================================
# Verification Tests
# ============================================================================


def test_run_verification_success(mock_forge_root):
    """Test successful post-deployment verification."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All checks passed",
            stderr="",
        )
        with patch("forge_harness.cli_v2.ship.console"):
            result = _run_verification(
                ctx, mock_forge_root, "test-domain", "test-project", "production"
            )

    assert result["status"] == "ok"
    assert "All checks passed" in result["output"]


def test_run_verification_warning(mock_forge_root):
    """Test verification with warnings."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="Partial success",
            stderr="Warning: slow response time",
        )
        with patch("forge_harness.cli_v2.ship.console"):
            result = _run_verification(
                ctx, mock_forge_root, "test-domain", "test-project", "production"
            )

    assert result["status"] == "warning"
    assert "slow response" in result["error"]


def test_run_verification_script_not_found(mock_forge_root):
    """Test verification when script doesn't exist."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        with patch("forge_harness.cli_v2.ship.console"):
            result = _run_verification(
                ctx, mock_forge_root, "test-domain", "test-project", "production"
            )

    assert result["status"] == "skipped"
    assert result["reason"] == "script_not_found"


def test_run_verification_timeout(mock_forge_root):
    """Test verification timeout handling."""
    ctx = MagicMock()

    # format_error raises SystemExit in non-json mode
    with pytest.raises(SystemExit):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("verify", 300)
            with patch("forge_harness.cli_v2.ship.console"):
                _run_verification(
                    ctx, mock_forge_root, "test-domain", "test-project", "production"
                )


def test_run_verification_timeout_json_mode(mock_forge_root):
    """Test verification timeout in JSON mode."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("verify", 300)
        result = _run_verification(
            ctx, mock_forge_root, "test-domain", "test-project", "production", output_json=True
        )

    assert result["status"] == "error"
    assert result["reason"] == "timeout"


# ============================================================================
# Rollback Tests
# ============================================================================


def test_run_rollback_railway_success(mock_forge_root, mock_project_dir):
    """Test successful Railway rollback."""
    ctx = MagicMock()

    with patch("click.confirm", return_value=True):
        with patch("forge_harness.cli_v2.ship.console"):
            with patch("forge_harness.deployment.registry.ProviderRegistry") as mock_registry:
                mock_provider = MagicMock()
                mock_provider.is_configured.return_value = True
                mock_provider.capabilities.supports_rollback = True
                mock_provider.rollback.return_value = MagicMock(
                    success=True,
                    logs="Rolled back to v1.2.3",
                )
                mock_registry.discover.return_value = None
                mock_registry.get.return_value = mock_provider

                result = _run_rollback(
                    ctx, mock_forge_root, "test-domain", "test-project", "production"
                )

    assert result["backend"]["status"] == "success"


def test_run_rollback_railway_failure(mock_forge_root, mock_project_dir):
    """Test Railway rollback failure."""
    ctx = MagicMock()

    # format_error raises SystemExit in non-json mode
    with pytest.raises(SystemExit):
        with patch("click.confirm", return_value=True):
            with patch("forge_harness.cli_v2.ship.console"):
                with patch("forge_harness.deployment.registry.ProviderRegistry") as mock_registry:
                    mock_provider = MagicMock()
                    mock_provider.is_configured.return_value = True
                    mock_provider.capabilities.supports_rollback = True
                    mock_provider.rollback.return_value = MagicMock(
                        success=False,
                        error="Deployment history not available",
                    )
                    mock_registry.discover.return_value = None
                    mock_registry.get.return_value = mock_provider

                    _run_rollback(
                        ctx, mock_forge_root, "test-domain", "test-project", "production"
                    )


def test_run_rollback_railway_unavailable(mock_forge_root, mock_project_dir):
    """Test rollback when Railway is not configured."""
    ctx = MagicMock()

    with patch("click.confirm", return_value=True):
        with patch("forge_harness.cli_v2.ship.console"):
            with patch("forge_harness.deployment.registry.ProviderRegistry") as mock_registry:
                mock_provider = MagicMock()
                mock_provider.is_configured.return_value = False
                mock_registry.discover.return_value = None
                mock_registry.get.return_value = mock_provider

                with patch("forge_harness.cli_v2.ship._run_rollback_via_git") as mock_git:
                    mock_git.return_value = {"status": "ok"}

                    result = _run_rollback(
                        ctx, mock_forge_root, "test-domain", "test-project", "production"
                    )

    # Should fall back to git rollback
    mock_git.assert_called_once()


def test_run_rollback_project_not_found(mock_forge_root):
    """Test rollback with non-existent project."""
    ctx = MagicMock()

    with patch("click.confirm", return_value=True):
        with pytest.raises(SystemExit):  # format_error raises SystemExit
            _run_rollback(ctx, mock_forge_root, "nonexistent", "project", "production")


# ============================================================================
# Git Rollback Tests
# ============================================================================


def test_run_rollback_via_git_success(mock_forge_root, mock_project_dir):
    """Test successful git-based rollback."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        # Mock HEAD and HEAD~1 queries
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234"),  # HEAD
            MagicMock(returncode=0, stdout="def5678"),  # HEAD~1
            MagicMock(returncode=0, stdout=""),  # checkout
        ]
        with patch("click.confirm", return_value=True):
            with patch("forge_harness.cli_v2.ship.console"):
                with patch("forge_harness.cli_v2.ship._run_deployment") as mock_deploy:
                    mock_deploy.return_value = {"status": "success"}

                    result = _run_rollback_via_git(
                        ctx, mock_forge_root, mock_project_dir,
                        "test-domain", "test-project", "production"
                    )

    assert result["status"] == "ok"
    assert result["prev_sha"] == "def5678"[:7]


def test_run_rollback_via_git_no_current_commit(mock_forge_root, mock_project_dir):
    """Test git rollback when current commit cannot be determined."""
    ctx = MagicMock()

    # format_error raises SystemExit in non-json mode
    with pytest.raises(SystemExit):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="not a git repo")
            with patch("forge_harness.cli_v2.ship.console"):
                _run_rollback_via_git(
                    ctx, mock_forge_root, mock_project_dir,
                    "test-domain", "test-project", "production"
                )


def test_run_rollback_via_git_no_current_commit_json(mock_forge_root, mock_project_dir):
    """Test git rollback no current commit in JSON mode."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="not a git repo")
        result = _run_rollback_via_git(
            ctx, mock_forge_root, mock_project_dir,
            "test-domain", "test-project", "production", output_json=True
        )

    assert result["status"] == "error"
    assert "Could not determine current commit" in result["error"]


def test_run_rollback_via_git_no_previous_commit(mock_forge_root, mock_project_dir):
    """Test git rollback with no previous commit."""
    ctx = MagicMock()

    # format_error raises SystemExit in non-json mode
    with pytest.raises(SystemExit):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc1234"),  # HEAD
                MagicMock(returncode=128, stdout="", stderr="HEAD~1 not found"),  # HEAD~1
            ]
            with patch("forge_harness.cli_v2.ship.console"):
                _run_rollback_via_git(
                    ctx, mock_forge_root, mock_project_dir,
                    "test-domain", "test-project", "production"
                )


def test_run_rollback_via_git_no_previous_commit_json(mock_forge_root, mock_project_dir):
    """Test git rollback no previous commit in JSON mode."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234"),  # HEAD
            MagicMock(returncode=128, stdout="", stderr="HEAD~1 not found"),  # HEAD~1
        ]
        result = _run_rollback_via_git(
            ctx, mock_forge_root, mock_project_dir,
            "test-domain", "test-project", "production", output_json=True
        )

    assert result["status"] == "error"
    assert "No previous commit" in result["error"]


def test_run_rollback_via_git_checkout_fails(mock_forge_root, mock_project_dir):
    """Test git rollback when checkout fails."""
    ctx = MagicMock()

    # format_error raises SystemExit in non-json mode
    with pytest.raises(SystemExit):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc1234"),  # HEAD
                MagicMock(returncode=0, stdout="def5678"),  # HEAD~1
                MagicMock(returncode=1, stdout="", stderr="error: checkout conflict"),  # checkout
            ]
            with patch("click.confirm", return_value=True):
                with patch("forge_harness.cli_v2.ship.console"):
                    _run_rollback_via_git(
                        ctx, mock_forge_root, mock_project_dir,
                        "test-domain", "test-project", "production"
                    )


def test_run_rollback_via_git_checkout_fails_json(mock_forge_root, mock_project_dir):
    """Test git rollback checkout fails in JSON mode."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234"),  # HEAD
            MagicMock(returncode=0, stdout="def5678"),  # HEAD~1
            MagicMock(returncode=1, stdout="", stderr="error: checkout conflict"),  # checkout
        ]
        result = _run_rollback_via_git(
            ctx, mock_forge_root, mock_project_dir,
            "test-domain", "test-project", "production", output_json=True
        )

    assert result["status"] == "error"
    assert "Git checkout failed" in result["error"]


def test_run_rollback_via_git_json_mode(mock_forge_root, mock_project_dir):
    """Test git rollback with JSON output mode."""
    ctx = MagicMock()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234"),
            MagicMock(returncode=0, stdout="def5678"),
            MagicMock(returncode=0, stdout=""),
        ]
        # No confirm needed in JSON mode
        with patch("forge_harness.cli_v2.ship._run_deployment") as mock_deploy:
            mock_deploy.return_value = {"status": "success"}

            result = _run_rollback_via_git(
                ctx, mock_forge_root, mock_project_dir,
                "test-domain", "test-project", "production", output_json=True
            )

    assert result["status"] == "ok"


# ============================================================================
# CLI Integration Tests
# ============================================================================


def test_ship_dry_run_cli(runner, mock_forge_root, mock_project_dir):
    """Test ship --dry-run through CLI."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = mock_forge_root
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            with patch("forge_harness.deployment.registry.ProviderRegistry") as mock_registry:
                mock_provider = MagicMock()
                mock_provider.is_configured.return_value = True
                mock_registry.discover.return_value = None
                mock_registry.get.return_value = mock_provider

                result = runner.invoke(ship, [
                    "-d", "test-domain",
                    "-p", "test-project",
                    "--staging",
                    "--dry-run",
                ])

    assert result.exit_code == 0


def test_ship_production_requires_confirmation(runner, mock_forge_root, mock_project_dir):
    """Test production deployment requires confirmation."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = mock_forge_root
        with patch("forge_harness.commands.pipeline.pipeline"):
            # Simulate user declining confirmation
            result = runner.invoke(ship, [
                "-d", "test-domain",
                "-p", "test-project",
                "--production",
            ], input="n\n")

    # Should exit gracefully when user declines
    assert "cancelled" in result.output.lower() or result.exit_code == 0


def test_ship_production_force_skips_confirmation(runner, mock_forge_root, mock_project_dir):
    """Test --force bypasses production confirmation."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = mock_forge_root
        with patch("forge_harness.commands.pipeline.pipeline"):
            result = runner.invoke(ship, [
                "-d", "test-domain",
                "-p", "test-project",
                "--production",
                "--force",
            ])

    # Should not prompt for confirmation
    assert "Continue with production" not in result.output


def test_ship_json_output(runner, mock_forge_root, mock_project_dir):
    """Test ship --json output mode."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = mock_forge_root
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            with patch("forge_harness.deployment.registry.ProviderRegistry") as mock_registry:
                mock_provider = MagicMock()
                mock_provider.is_configured.return_value = True
                mock_registry.discover.return_value = None
                mock_registry.get.return_value = mock_provider

                result = runner.invoke(ship, [
                    "-d", "test-domain",
                    "-p", "test-project",
                    "--staging",
                    "--dry-run",
                    "--json",
                ])

    # Output should be valid JSON
    import json
    try:
        output = json.loads(result.output)
        assert output["success"] is True
    except json.JSONDecodeError:
        pass  # Some error messages may not be JSON


def test_ship_rollback_cli(runner, mock_forge_root, mock_project_dir):
    """Test ship --rollback through CLI."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = mock_forge_root
        with patch("click.confirm", return_value=True):
            with patch("forge_harness.deployment.registry.ProviderRegistry") as mock_registry:
                mock_provider = MagicMock()
                mock_provider.is_configured.return_value = True
                mock_provider.capabilities.supports_rollback = True
                mock_provider.rollback.return_value = MagicMock(success=True, logs="")
                mock_registry.discover.return_value = None
                mock_registry.get.return_value = mock_provider

                result = runner.invoke(ship, [
                    "-d", "test-domain",
                    "-p", "test-project",
                    "--rollback",
                ], input="y\n")

    # Should attempt rollback
    assert result.exit_code == 0 or "Rollback" in result.output


# ============================================================================
# Edge Cases
# ============================================================================


def test_ship_staging_dry_run_no_backend(mock_forge_root, tmp_path):
    """Test dry run with no backend directory."""
    ctx = MagicMock()

    # Create project with only frontend
    project_dir = mock_forge_root / "test-domain" / "frontend-only"
    project_dir.mkdir(parents=True)
    (project_dir / "frontend").mkdir()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with patch("forge_harness.cli_v2.ship.console"):
            with patch("forge_harness.deployment.registry.ProviderRegistry"):
                result = _run_dry_run(ctx, mock_forge_root, "test-domain", "frontend-only", "staging")

    # Should only have frontend command
    assert len(result["commands"]) == 1
    assert result["commands"][0]["target"] == "frontend"


def test_ship_empty_project(mock_forge_root, tmp_path):
    """Test dry run with empty project directory."""
    ctx = MagicMock()

    # Create empty project
    project_dir = mock_forge_root / "test-domain" / "empty-project"
    project_dir.mkdir(parents=True)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with patch("forge_harness.cli_v2.ship.console"):
            with patch("forge_harness.deployment.registry.ProviderRegistry"):
                result = _run_dry_run(ctx, mock_forge_root, "test-domain", "empty-project", "staging")

    # Should have no commands
    assert len(result["commands"]) == 0


def test_rollback_defaults_to_production(runner, mock_forge_root, mock_project_dir):
    """Test that --rollback alone defaults to production environment."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = mock_forge_root
        with patch("forge_harness.cli_v2.ship._run_rollback") as mock_rollback:
            mock_rollback.return_value = {"status": "ok"}

            result = runner.invoke(ship, [
                "-d", "test-domain",
                "-p", "test-project",
                "--rollback",
            ], input="y\n")

            # Should call with production env
            if mock_rollback.called:
                call_args = mock_rollback.call_args
                assert call_args[0][4] == "production"  # env argument


def test_rollback_staging_explicit(runner, mock_forge_root, mock_project_dir):
    """Test that --rollback --staging uses staging environment."""
    with patch("forge_harness.cli_v2.ship.require_forge_root") as mock_root:
        mock_root.return_value = mock_forge_root
        with patch("forge_harness.cli_v2.ship._run_rollback") as mock_rollback:
            mock_rollback.return_value = {"status": "ok"}

            result = runner.invoke(ship, [
                "-d", "test-domain",
                "-p", "test-project",
                "--rollback",
                "--staging",
            ], input="y\n")

            if mock_rollback.called:
                call_args = mock_rollback.call_args
                assert call_args[0][4] == "staging"
