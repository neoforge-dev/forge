"""Tests for forge CLI v2 ship command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.ship import ship

runner = CliRunner()


@pytest.fixture
def mock_forge_root(tmp_path):
    """Create a minimal FORGE root directory."""
    forge_root = tmp_path / "forge"
    forge_root.mkdir()
    (forge_root / ".forge-root").touch()
    return forge_root


@pytest.fixture
def project_dir(mock_forge_root):
    """Create a domain/project directory under the forge root."""
    p = mock_forge_root / "test-domain" / "test-project"
    p.mkdir(parents=True)
    return p


@pytest.fixture
def project_with_backend(project_dir):
    """Add a backend/ subdirectory to the project."""
    backend = project_dir / "backend"
    backend.mkdir()
    return project_dir


@pytest.fixture
def project_with_frontend(project_dir):
    """Add a frontend/ subdirectory to the project."""
    frontend = project_dir / "frontend"
    frontend.mkdir()
    return project_dir


# ---------------------------------------------------------------------------
# Guard rails: missing required args
# ---------------------------------------------------------------------------


class TestShipValidation:
    def test_no_environment_flag_exits_nonzero(self, mock_forge_root):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                result = runner.invoke(ship, ["-d", "test-domain", "-p", "test-project"])
        assert result.exit_code != 0

    def test_both_staging_and_production_exits_nonzero(self, mock_forge_root):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                result = runner.invoke(
                    ship,
                    ["-d", "test-domain", "-p", "test-project", "--staging", "--production"],
                )
        assert result.exit_code != 0

    def test_missing_domain_project_exits_nonzero(self, mock_forge_root):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=(None, None),
            ):
                result = runner.invoke(ship, ["--staging"])
        assert result.exit_code != 0

    def test_missing_domain_project_json_mode_returns_error_envelope(self, mock_forge_root):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=(None, None),
            ):
                result = runner.invoke(ship, ["--staging", "--json"])
        assert result.exit_code != 0
        # JSON error envelope must be present
        payload = json.loads(result.output)
        assert payload["success"] is False
        assert payload["error"] is not None


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


class TestShipDryRun:
    def test_dry_run_staging_exits_zero(self, mock_forge_root, project_dir):
        git_result = MagicMock(returncode=0, stdout="")
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("subprocess.run", return_value=git_result):
                    result = runner.invoke(
                        ship,
                        ["-d", "test-domain", "-p", "test-project", "--staging", "--dry-run"],
                    )
        assert result.exit_code == 0

    def test_dry_run_production_exits_zero(self, mock_forge_root, project_dir):
        git_result = MagicMock(returncode=0, stdout="")
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("subprocess.run", return_value=git_result):
                    result = runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--production",
                            "--dry-run",
                        ],
                    )
        assert result.exit_code == 0

    def test_dry_run_json_returns_success_envelope(self, mock_forge_root, project_dir):
        git_result = MagicMock(returncode=0, stdout="")
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("subprocess.run", return_value=git_result):
                    result = runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--staging",
                            "--dry-run",
                            "--json",
                        ],
                    )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["data"] is not None
        assert payload["data"]["domain"] == "test-domain"
        assert payload["data"]["project"] == "test-project"

    def test_dry_run_json_reports_env(self, mock_forge_root, project_dir):
        git_result = MagicMock(returncode=0, stdout="")
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("subprocess.run", return_value=git_result):
                    result = runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--production",
                            "--dry-run",
                            "--json",
                        ],
                    )
        payload = json.loads(result.output)
        assert payload["data"]["env"] == "production"

    def test_dry_run_with_backend_dir_includes_railway_command(
        self, mock_forge_root, project_with_backend
    ):
        git_result = MagicMock(returncode=0, stdout="")
        mock_registry = MagicMock()
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = False
        mock_registry.get.return_value = mock_provider
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("subprocess.run", return_value=git_result):
                    with patch(
                        "forge_harness.deployment.registry.ProviderRegistry",
                        mock_registry,
                    ):
                        with patch("forge_harness.cli_v2.ship._run_dry_run") as mock_dry:
                            mock_dry.return_value = {
                                "domain": "test-domain",
                                "project": "test-project",
                                "env": "staging",
                                "commands": [{"target": "backend", "cmd": "railway up"}],
                                "checks": [],
                                "git_status": {},
                            }
                            result = runner.invoke(
                                ship,
                                [
                                    "-d",
                                    "test-domain",
                                    "-p",
                                    "test-project",
                                    "--staging",
                                    "--dry-run",
                                ],
                            )
        assert result.exit_code == 0
        mock_dry.assert_called_once()

    def test_dry_run_project_not_found_exits_nonzero(self, mock_forge_root):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "missing-project"),
            ):
                result = runner.invoke(
                    ship,
                    ["-d", "test-domain", "-p", "missing-project", "--staging", "--dry-run"],
                )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# --staging / --production deployment
# ---------------------------------------------------------------------------


class TestShipDeployment:
    def test_staging_deploy_invokes_pipeline(self, mock_forge_root, project_dir):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("forge_harness.cli_v2.ship._run_deployment") as mock_deploy:
                    mock_deploy.return_value = {
                        "status": "success",
                        "domain": "test-domain",
                        "project": "test-project",
                        "env": "staging",
                        "verification": None,
                    }
                    result = runner.invoke(
                        ship,
                        ["-d", "test-domain", "-p", "test-project", "--staging"],
                    )
        assert result.exit_code == 0
        mock_deploy.assert_called_once()

    def test_production_deploy_with_force_skips_confirm(self, mock_forge_root, project_dir):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("forge_harness.cli_v2.ship._run_deployment") as mock_deploy:
                    mock_deploy.return_value = {
                        "status": "success",
                        "domain": "test-domain",
                        "project": "test-project",
                        "env": "production",
                        "verification": None,
                    }
                    result = runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--production",
                            "--force",
                        ],
                    )
        assert result.exit_code == 0
        mock_deploy.assert_called_once()

    def test_staging_deploy_json_returns_success_envelope(self, mock_forge_root, project_dir):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("forge_harness.cli_v2.ship._run_deployment") as mock_deploy:
                    mock_deploy.return_value = {
                        "status": "success",
                        "domain": "test-domain",
                        "project": "test-project",
                        "env": "staging",
                        "verification": None,
                    }
                    result = runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--staging",
                            "--json",
                        ],
                    )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["data"]["status"] == "success"

    def test_production_deploy_json_skips_interactive_confirm(self, mock_forge_root, project_dir):
        """JSON mode must never block on interactive confirmation."""
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("forge_harness.cli_v2.ship._run_deployment") as mock_deploy:
                    mock_deploy.return_value = {
                        "status": "success",
                        "domain": "test-domain",
                        "project": "test-project",
                        "env": "production",
                        "verification": None,
                    }
                    result = runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--production",
                            "--json",
                        ],
                    )
        # JSON mode bypasses the interactive confirm → should not block
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True

    def test_deploy_with_verify_calls_verification(self, mock_forge_root, project_dir):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("forge_harness.cli_v2.ship._run_deployment") as mock_deploy:
                    mock_deploy.return_value = {
                        "status": "success",
                        "domain": "test-domain",
                        "project": "test-project",
                        "env": "staging",
                        "verification": {"status": "ok"},
                    }
                    result = runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--staging",
                            "--verify",
                        ],
                    )
        assert result.exit_code == 0
        # _run_deployment(ctx, forge_root, domain, project, env, verify, output_json)
        # verify is the 6th positional argument (index 5)
        call_args = mock_deploy.call_args
        pos_args = call_args.args if call_args.args else call_args[0]
        verify_value = pos_args[5] if len(pos_args) > 5 else call_args.kwargs.get("verify")
        assert verify_value is True


# ---------------------------------------------------------------------------
# --rollback
# ---------------------------------------------------------------------------


class TestShipRollback:
    def test_rollback_json_mode_returns_success_envelope(self, mock_forge_root, project_dir):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("forge_harness.cli_v2.ship._run_rollback") as mock_rb:
                    mock_rb.return_value = {"backend": None, "git": {"status": "ok"}}
                    result = runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--rollback",
                            "--json",
                        ],
                    )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True

    def test_rollback_json_mode_calls_run_rollback(self, mock_forge_root, project_dir):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("forge_harness.cli_v2.ship._run_rollback") as mock_rb:
                    mock_rb.return_value = {"backend": None, "git": None}
                    runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--rollback",
                            "--json",
                        ],
                    )
        mock_rb.assert_called_once()

    def test_rollback_defaults_to_production_env(self, mock_forge_root, project_dir):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("forge_harness.cli_v2.ship._run_rollback") as mock_rb:
                    mock_rb.return_value = {}
                    runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--rollback",
                            "--json",
                        ],
                    )
        # _run_rollback(ctx, forge_root, domain, project, env, output_json)
        # env is the 5th positional argument (index 4)
        call_args = mock_rb.call_args
        pos_args = call_args.args if call_args.args else call_args[0]
        env_value = pos_args[4] if len(pos_args) > 4 else call_args.kwargs.get("env")
        assert env_value == "production"

    def test_rollback_staging_uses_staging_env(self, mock_forge_root, project_dir):
        with patch(
            "forge_harness.cli_v2.ship.require_forge_root",
            return_value=mock_forge_root,
        ):
            with patch(
                "forge_harness.cli_v2.ship.infer_domain_project",
                return_value=("test-domain", "test-project"),
            ):
                with patch("forge_harness.cli_v2.ship._run_rollback") as mock_rb:
                    mock_rb.return_value = {}
                    runner.invoke(
                        ship,
                        [
                            "-d",
                            "test-domain",
                            "-p",
                            "test-project",
                            "--rollback",
                            "--staging",
                            "--json",
                        ],
                    )
        # _run_rollback(ctx, forge_root, domain, project, env, output_json)
        # env is the 5th positional argument (index 4)
        call_args = mock_rb.call_args
        pos_args = call_args.args if call_args.args else call_args[0]
        env_value = pos_args[4] if len(pos_args) > 4 else call_args.kwargs.get("env")
        assert env_value == "staging"


# ---------------------------------------------------------------------------
# Internal helper: _run_verification
# ---------------------------------------------------------------------------


class TestRunVerification:
    def test_verification_returns_ok_when_script_exits_zero(self):
        from forge_harness.cli_v2.ship import _run_verification

        mock_result = MagicMock(returncode=0, stdout="All checks passed", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = _run_verification(
                ctx=MagicMock(),
                forge_root=Path("/tmp/fake"),
                domain="d",
                project="p",
                env="staging",
                output_json=True,
            )
        assert result["status"] == "ok"

    def test_verification_returns_warning_when_script_fails(self):
        from forge_harness.cli_v2.ship import _run_verification

        mock_result = MagicMock(returncode=1, stdout="", stderr="health check failed")
        with patch("subprocess.run", return_value=mock_result):
            result = _run_verification(
                ctx=MagicMock(),
                forge_root=Path("/tmp/fake"),
                domain="d",
                project="p",
                env="staging",
                output_json=True,
            )
        assert result["status"] == "warning"

    def test_verification_returns_skipped_when_script_not_found(self):
        from forge_harness.cli_v2.ship import _run_verification

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = _run_verification(
                ctx=MagicMock(),
                forge_root=Path("/tmp/fake"),
                domain="d",
                project="p",
                env="staging",
                output_json=True,
            )
        assert result["status"] == "skipped"
        assert result["reason"] == "script_not_found"

    def test_verification_returns_error_on_timeout(self):
        from forge_harness.cli_v2.ship import _run_verification

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="v", timeout=300)):
            result = _run_verification(
                ctx=MagicMock(),
                forge_root=Path("/tmp/fake"),
                domain="d",
                project="p",
                env="staging",
                output_json=True,
            )
        assert result["status"] == "error"
        assert result["reason"] == "timeout"


# ---------------------------------------------------------------------------
# Internal helper: _run_dry_run (git status branch)
# ---------------------------------------------------------------------------


class TestRunDryRunGitStatus:
    def test_dry_run_records_git_changes(self, mock_forge_root, project_dir):
        from forge_harness.cli_v2.ship import _run_dry_run

        git_result = MagicMock(returncode=0, stdout=" M app.py\n?? newfile.txt")
        with patch("subprocess.run", return_value=git_result):
            result = _run_dry_run(
                ctx=MagicMock(),
                forge_root=mock_forge_root,
                domain="test-domain",
                project="test-project",
                env="staging",
                output_json=True,
            )
        assert "changes" in result["git_status"]
        assert len(result["git_status"]["changes"]) == 2

    def test_dry_run_records_clean_tree(self, mock_forge_root, project_dir):
        from forge_harness.cli_v2.ship import _run_dry_run

        git_result = MagicMock(returncode=0, stdout="")
        with patch("subprocess.run", return_value=git_result):
            result = _run_dry_run(
                ctx=MagicMock(),
                forge_root=mock_forge_root,
                domain="test-domain",
                project="test-project",
                env="staging",
                output_json=True,
            )
        assert result["git_status"].get("clean") is True

    def test_dry_run_handles_git_not_available(self, mock_forge_root, project_dir):
        from forge_harness.cli_v2.ship import _run_dry_run

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = _run_dry_run(
                ctx=MagicMock(),
                forge_root=mock_forge_root,
                domain="test-domain",
                project="test-project",
                env="staging",
                output_json=True,
            )
        assert "error" in result["git_status"]
