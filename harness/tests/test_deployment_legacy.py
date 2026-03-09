"""
Tests for forge_harness/deployment_legacy.py

Covers:
- QualityGateResult dataclass
- DeploymentResult dataclass
- SmokeTestResult dataclass and summary property
- ForgeDeployer.__init__
- ForgeDeployer._run_command
- ForgeDeployer.check_quality_gates
- ForgeDeployer.deploy_backend
- ForgeDeployer.deploy_frontend
- ForgeDeployer.verify_deployment
- ForgeDeployer.run_smoke_tests
- ForgeDeployer.full_deploy
- ForgeDeployer.deploy_with_provider
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.deployment_legacy import (
    DeploymentResult,
    ForgeDeployer,
    QualityGateResult,
    SmokeTestResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Return a bare project directory (no backend/frontend sub-dirs)."""
    return tmp_path / "my-project"


@pytest.fixture
def project_dir_with_backend(tmp_path: Path) -> Path:
    """Return a project directory that has a backend sub-directory."""
    project = tmp_path / "my-project"
    (project / "backend").mkdir(parents=True)
    return project


@pytest.fixture
def project_dir_with_frontend(tmp_path: Path) -> Path:
    """Return a project directory that has a frontend sub-directory."""
    project = tmp_path / "my-project"
    (project / "frontend").mkdir(parents=True)
    return project


@pytest.fixture
def project_dir_full(tmp_path: Path) -> Path:
    """Return a project directory with both backend and frontend sub-dirs."""
    project = tmp_path / "my-project"
    (project / "backend").mkdir(parents=True)
    (project / "frontend").mkdir(parents=True)
    return project


@pytest.fixture
def deployer(project_dir: Path) -> ForgeDeployer:
    """Minimal ForgeDeployer with no credentials."""
    return ForgeDeployer(
        domain="test-domain",
        project="test-project",
        project_dir=project_dir,
    )


@pytest.fixture
def deployer_with_tokens(project_dir: Path) -> ForgeDeployer:
    """ForgeDeployer pre-configured with Railway and Cloudflare credentials."""
    return ForgeDeployer(
        domain="test-domain",
        project="test-project",
        project_dir=project_dir,
        railway_token="rw-token-123",
        cloudflare_token="cf-token-456",
        cloudflare_account_id="cf-account-789",
    )


def _make_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Build a fake CompletedProcess for patching subprocess.run."""
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestQualityGateResult:
    def test_passed_true(self) -> None:
        result = QualityGateResult(
            passed=True,
            backend_coverage=85.0,
            frontend_build_ok=True,
            backend_tests_passed=10,
            backend_tests_failed=0,
            frontend_tests_passed=5,
            frontend_tests_failed=0,
            errors=[],
        )
        assert result.passed is True
        assert result.backend_coverage == 85.0
        assert result.errors == []

    def test_passed_false_with_errors(self) -> None:
        result = QualityGateResult(
            passed=False,
            backend_coverage=50.0,
            frontend_build_ok=False,
            backend_tests_passed=3,
            backend_tests_failed=2,
            frontend_tests_passed=0,
            frontend_tests_failed=1,
            errors=["Backend coverage too low", "Frontend build failed"],
        )
        assert result.passed is False
        assert len(result.errors) == 2

    def test_none_coverage(self) -> None:
        result = QualityGateResult(
            passed=True,
            backend_coverage=None,
            frontend_build_ok=False,
            backend_tests_passed=0,
            backend_tests_failed=0,
            frontend_tests_passed=0,
            frontend_tests_failed=0,
            errors=[],
        )
        assert result.backend_coverage is None


class TestDeploymentResult:
    def test_success_result(self) -> None:
        result = DeploymentResult(
            success=True,
            target="railway",
            url="https://app.railway.app",
            duration_seconds=12.5,
            error=None,
            logs="Deployed successfully",
        )
        assert result.success is True
        assert result.url == "https://app.railway.app"
        assert result.error is None

    def test_failure_result(self) -> None:
        result = DeploymentResult(
            success=False,
            target="cloudflare",
            url=None,
            duration_seconds=3.0,
            error="Authentication failed",
            logs="",
        )
        assert result.success is False
        assert result.url is None
        assert result.error == "Authentication failed"


class TestSmokeTestResult:
    def test_summary_passed(self) -> None:
        result = SmokeTestResult(
            passed=True,
            tests_run=5,
            tests_passed=5,
            tests_failed=0,
            results=[],
            errors=[],
        )
        assert "PASSED" in result.summary
        assert "5/5" in result.summary

    def test_summary_failed(self) -> None:
        result = SmokeTestResult(
            passed=False,
            tests_run=5,
            tests_passed=3,
            tests_failed=2,
            results=[],
            errors=["Health check failed"],
        )
        assert "FAILED" in result.summary
        assert "3/5" in result.summary

    def test_summary_zero_tests(self) -> None:
        result = SmokeTestResult(
            passed=True,
            tests_run=0,
            tests_passed=0,
            tests_failed=0,
            results=[],
            errors=[],
        )
        assert "0/0" in result.summary


# ---------------------------------------------------------------------------
# ForgeDeployer.__init__
# ---------------------------------------------------------------------------


class TestForgeDeployerInit:
    def test_basic_init(self, project_dir: Path) -> None:
        deployer = ForgeDeployer(
            domain="my-domain",
            project="my-project",
            project_dir=project_dir,
        )
        assert deployer.domain == "my-domain"
        assert deployer.project == "my-project"
        assert deployer.project_dir == project_dir
        assert deployer.backend_dir == project_dir / "backend"
        assert deployer.frontend_dir == project_dir / "frontend"

    def test_tokens_from_args(self, project_dir: Path) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir,
            railway_token="rw",
            cloudflare_token="cf",
            cloudflare_account_id="acct",
        )
        assert deployer.railway_token == "rw"
        assert deployer.cloudflare_token == "cf"
        assert deployer.cloudflare_account_id == "acct"

    def test_tokens_from_env(self, project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "env-rw")
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "env-cf")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "env-acct")

        deployer = ForgeDeployer(domain="d", project="p", project_dir=project_dir)
        assert deployer.railway_token == "env-rw"
        assert deployer.cloudflare_token == "env-cf"
        assert deployer.cloudflare_account_id == "env-acct"

    def test_explicit_token_overrides_env(
        self, project_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "env-rw")
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir,
            railway_token="explicit-rw",
        )
        assert deployer.railway_token == "explicit-rw"

    def test_no_tokens(self, project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)

        deployer = ForgeDeployer(domain="d", project="p", project_dir=project_dir)
        assert deployer.railway_token is None
        assert deployer.cloudflare_token is None
        assert deployer.cloudflare_account_id is None

    def test_project_dir_converted_to_path(self, tmp_path: Path) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=str(tmp_path),  # type: ignore[arg-type]
        )
        assert isinstance(deployer.project_dir, Path)


# ---------------------------------------------------------------------------
# ForgeDeployer._run_command
# ---------------------------------------------------------------------------


class TestRunCommand:
    @patch("subprocess.run")
    def test_basic_command(self, mock_run: MagicMock, deployer: ForgeDeployer, tmp_path: Path) -> None:
        mock_run.return_value = _make_proc(returncode=0, stdout="ok", stderr="")
        result = deployer._run_command(["echo", "hello"], cwd=tmp_path)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["capture_output"] is True
        assert call_kwargs[1]["text"] is True
        assert result.returncode == 0
        assert result.stdout == "ok"

    @patch("subprocess.run")
    def test_extra_env_merged(self, mock_run: MagicMock, deployer: ForgeDeployer, tmp_path: Path) -> None:
        mock_run.return_value = _make_proc()
        deployer._run_command(["ls"], cwd=tmp_path, env={"MY_VAR": "value"})

        passed_env = mock_run.call_args[1]["env"]
        assert passed_env["MY_VAR"] == "value"

    @patch("subprocess.run")
    def test_timeout_passed(self, mock_run: MagicMock, deployer: ForgeDeployer, tmp_path: Path) -> None:
        mock_run.return_value = _make_proc()
        deployer._run_command(["ls"], cwd=tmp_path, timeout=42)

        assert mock_run.call_args[1]["timeout"] == 42

    @patch("subprocess.run")
    def test_cwd_passed(self, mock_run: MagicMock, deployer: ForgeDeployer, tmp_path: Path) -> None:
        mock_run.return_value = _make_proc()
        deployer._run_command(["ls"], cwd=tmp_path)

        assert mock_run.call_args[1]["cwd"] == tmp_path


# ---------------------------------------------------------------------------
# ForgeDeployer.check_quality_gates
# ---------------------------------------------------------------------------


class TestCheckQualityGates:
    def test_no_backend_no_frontend(self, deployer: ForgeDeployer) -> None:
        """If neither backend nor frontend dirs exist, gates pass vacuously."""
        result = deployer.check_quality_gates()
        assert result.passed is True
        assert result.backend_coverage is None
        assert result.frontend_build_ok is False
        assert result.errors == []

    @patch("subprocess.run")
    def test_backend_tests_pass_with_coverage(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_with_backend
        )
        pytest_output = (
            "TOTAL  1000    200   80%\n"
            "10 passed in 2.34s\n"
        )
        mock_run.return_value = _make_proc(returncode=0, stdout=pytest_output)

        result = deployer.check_quality_gates()
        assert result.passed is True
        assert result.backend_coverage == 80.0
        assert result.backend_tests_passed == 10
        assert result.backend_tests_failed == 0
        assert result.errors == []

    @patch("subprocess.run")
    def test_backend_coverage_below_minimum(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_with_backend
        )
        pytest_output = (
            "TOTAL  1000    800   20%\n"
            "5 passed in 1.0s\n"
        )
        mock_run.return_value = _make_proc(returncode=0, stdout=pytest_output)

        result = deployer.check_quality_gates()
        assert result.passed is False
        assert result.backend_coverage == 20.0
        assert any("20.0%" in e for e in result.errors)

    @patch("subprocess.run")
    def test_backend_tests_fail(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_with_backend
        )
        mock_run.return_value = _make_proc(returncode=1, stdout="", stderr="Some error")

        result = deployer.check_quality_gates()
        assert result.passed is False
        assert any("Backend tests failed" in e for e in result.errors)

    @patch("subprocess.run")
    def test_backend_test_count_parsing(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_with_backend
        )
        pytest_output = (
            "TOTAL  100    10   90%\n"
            "45 passed, 3 failed in 5.0s\n"
        )
        mock_run.return_value = _make_proc(returncode=0, stdout=pytest_output)

        result = deployer.check_quality_gates()
        assert result.backend_tests_passed == 45
        assert result.backend_tests_failed == 3

    @patch("subprocess.run")
    def test_frontend_tests_and_build_pass(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_with_frontend
        )
        # First call: npm test; second call: npm build
        mock_run.side_effect = [
            _make_proc(returncode=0, stdout="12 passed, 0 failed"),
            _make_proc(returncode=0, stdout="Build successful"),
        ]

        result = deployer.check_quality_gates()
        assert result.passed is True
        assert result.frontend_build_ok is True
        assert result.frontend_tests_passed == 12
        assert result.frontend_tests_failed == 0

    @patch("subprocess.run")
    def test_frontend_tests_fail(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_with_frontend
        )
        mock_run.side_effect = [
            _make_proc(returncode=1, stderr="Test failure"),
            _make_proc(returncode=0),
        ]

        result = deployer.check_quality_gates()
        assert any("Frontend tests failed" in e for e in result.errors)

    @patch("subprocess.run")
    def test_frontend_build_fail(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_with_frontend
        )
        mock_run.side_effect = [
            _make_proc(returncode=0, stdout="5 passed"),
            _make_proc(returncode=1, stderr="Build error"),
        ]

        result = deployer.check_quality_gates()
        assert result.frontend_build_ok is False
        assert any("Frontend build failed" in e for e in result.errors)

    @patch("subprocess.run")
    def test_coverage_line_with_no_percentage(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        """Coverage line present but no parseable % — should not crash."""
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_with_backend
        )
        pytest_output = "TOTAL  something weird\n5 passed\n"
        mock_run.return_value = _make_proc(returncode=0, stdout=pytest_output)

        result = deployer.check_quality_gates()
        assert result.backend_coverage is None

    @patch("subprocess.run")
    def test_exactly_minimum_coverage_passes(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_with_backend
        )
        pytest_output = "TOTAL  100    30   70%\n10 passed\n"
        mock_run.return_value = _make_proc(returncode=0, stdout=pytest_output)

        result = deployer.check_quality_gates()
        # 70.0 is not < 70.0, so no coverage error
        assert result.passed is True
        assert result.backend_coverage == 70.0

    @patch("subprocess.run")
    def test_full_stack_quality_gates(
        self,
        mock_run: MagicMock,
        project_dir_full: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_full
        )
        mock_run.side_effect = [
            # Backend pytest
            _make_proc(returncode=0, stdout="TOTAL 100 20 80%\n20 passed\n"),
            # Frontend npm test
            _make_proc(returncode=0, stdout="8 passed"),
            # Frontend npm build
            _make_proc(returncode=0),
        ]

        result = deployer.check_quality_gates()
        assert result.passed is True
        assert result.backend_coverage == 80.0
        assert result.frontend_build_ok is True


# ---------------------------------------------------------------------------
# ForgeDeployer.deploy_backend
# ---------------------------------------------------------------------------


class TestDeployBackend:
    def test_no_railway_token(self, project_dir: Path) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir,
        )
        result = deployer.deploy_backend()
        assert result.success is False
        assert result.target == "railway"
        assert "RAILWAY_TOKEN" in result.error  # type: ignore[operator]

    def test_backend_dir_missing(self, project_dir: Path) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir,
            railway_token="rw",
        )
        result = deployer.deploy_backend()
        assert result.success is False
        assert "Backend directory not found" in result.error  # type: ignore[operator]

    @patch("subprocess.run")
    def test_deploy_success_no_url(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir_with_backend,
            railway_token="rw",
        )
        mock_run.return_value = _make_proc(returncode=0, stdout="Deployment complete")

        result = deployer.deploy_backend()
        assert result.success is True
        assert result.target == "railway"
        assert result.url is None  # no URL in stdout
        assert result.error is None

    @patch("subprocess.run")
    def test_deploy_success_with_url(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir_with_backend,
            railway_token="rw",
        )
        stdout = "Deploying... https://app.up.railway.app done"
        mock_run.return_value = _make_proc(returncode=0, stdout=stdout)

        result = deployer.deploy_backend()
        assert result.success is True
        assert result.url == "https://app.up.railway.app"

    @patch("subprocess.run")
    def test_deploy_failure(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir_with_backend,
            railway_token="rw",
        )
        mock_run.return_value = _make_proc(returncode=1, stderr="Deploy error")

        result = deployer.deploy_backend()
        assert result.success is False
        assert result.error == "Deploy error"

    @patch("subprocess.run")
    def test_railway_token_passed_as_env(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir_with_backend,
            railway_token="my-secret-token",
        )
        mock_run.return_value = _make_proc(returncode=0)

        deployer.deploy_backend()

        passed_env = mock_run.call_args[1]["env"]
        assert passed_env.get("RAILWAY_TOKEN") == "my-secret-token"

    @patch("subprocess.run")
    def test_duration_is_positive(
        self,
        mock_run: MagicMock,
        project_dir_with_backend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir_with_backend,
            railway_token="rw",
        )
        mock_run.return_value = _make_proc(returncode=0)

        result = deployer.deploy_backend()
        assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# ForgeDeployer.deploy_frontend
# ---------------------------------------------------------------------------


class TestDeployFrontend:
    def test_no_cloudflare_token(self, project_dir: Path) -> None:
        deployer = ForgeDeployer(domain="d", project="p", project_dir=project_dir)
        result = deployer.deploy_frontend()
        assert result.success is False
        assert result.target == "cloudflare"
        assert "Cloudflare credentials" in result.error  # type: ignore[operator]

    def test_no_account_id(self, project_dir: Path) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir,
            cloudflare_token="cf",
        )
        result = deployer.deploy_frontend()
        assert result.success is False
        assert "Cloudflare credentials" in result.error  # type: ignore[operator]

    def test_frontend_dir_missing(self, project_dir: Path) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir,
            cloudflare_token="cf",
            cloudflare_account_id="acct",
        )
        result = deployer.deploy_frontend()
        assert result.success is False
        assert "Frontend directory not found" in result.error  # type: ignore[operator]

    @patch("subprocess.run")
    def test_deploy_success_with_dist_existing(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        # Create dist dir to skip build step
        (project_dir_with_frontend / "frontend" / "dist").mkdir()
        deployer = ForgeDeployer(
            domain="d",
            project="my-project",
            project_dir=project_dir_with_frontend,
            cloudflare_token="cf",
            cloudflare_account_id="acct",
        )
        stdout = "Deployed to https://myprojectapp.pages.dev ✓"
        mock_run.return_value = _make_proc(returncode=0, stdout=stdout)

        result = deployer.deploy_frontend()
        assert result.success is True
        assert result.target == "cloudflare"
        assert result.url == "https://myprojectapp.pages.dev"

    @patch("subprocess.run")
    def test_deploy_builds_when_no_dist(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="my-project",
            project_dir=project_dir_with_frontend,
            cloudflare_token="cf",
            cloudflare_account_id="acct",
        )
        mock_run.side_effect = [
            # npm build
            _make_proc(returncode=0, stdout="Build done"),
            # wrangler deploy
            _make_proc(returncode=0, stdout="https://myprojectapp.pages.dev"),
        ]

        result = deployer.deploy_frontend()
        assert result.success is True
        # Ensure two subprocess calls were made
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_build_failure_returns_error(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir_with_frontend,
            cloudflare_token="cf",
            cloudflare_account_id="acct",
        )
        mock_run.return_value = _make_proc(returncode=1, stderr="Build failed!")

        result = deployer.deploy_frontend()
        assert result.success is False
        assert "Build failed" in result.error  # type: ignore[operator]

    @patch("subprocess.run")
    def test_wrangler_failure(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        (project_dir_with_frontend / "frontend" / "dist").mkdir()
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir_with_frontend,
            cloudflare_token="cf",
            cloudflare_account_id="acct",
        )
        mock_run.return_value = _make_proc(returncode=1, stderr="Auth error")

        result = deployer.deploy_frontend()
        assert result.success is False
        assert result.error == "Auth error"

    @patch("subprocess.run")
    def test_project_name_dashes_removed(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        (project_dir_with_frontend / "frontend" / "dist").mkdir()
        deployer = ForgeDeployer(
            domain="d",
            project="my-cool-project",
            project_dir=project_dir_with_frontend,
            cloudflare_token="cf",
            cloudflare_account_id="acct",
        )
        mock_run.return_value = _make_proc(returncode=0)

        deployer.deploy_frontend()

        cmd = mock_run.call_args[0][0]
        # project name should have no dashes
        assert any("mycoolproject" in arg for arg in cmd)

    @patch("subprocess.run")
    def test_cloudflare_tokens_passed_as_env(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        (project_dir_with_frontend / "frontend" / "dist").mkdir()
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir_with_frontend,
            cloudflare_token="my-cf-token",
            cloudflare_account_id="my-acct-id",
        )
        mock_run.return_value = _make_proc(returncode=0)

        deployer.deploy_frontend()

        passed_env = mock_run.call_args[1]["env"]
        assert passed_env.get("CLOUDFLARE_API_TOKEN") == "my-cf-token"
        assert passed_env.get("CLOUDFLARE_ACCOUNT_ID") == "my-acct-id"

    @patch("subprocess.run")
    def test_no_url_in_stdout(
        self,
        mock_run: MagicMock,
        project_dir_with_frontend: Path,
    ) -> None:
        (project_dir_with_frontend / "frontend" / "dist").mkdir()
        deployer = ForgeDeployer(
            domain="d",
            project="p",
            project_dir=project_dir_with_frontend,
            cloudflare_token="cf",
            cloudflare_account_id="acct",
        )
        mock_run.return_value = _make_proc(returncode=0, stdout="All done")

        result = deployer.deploy_frontend()
        assert result.success is True
        assert result.url is None


# ---------------------------------------------------------------------------
# ForgeDeployer.verify_deployment
# ---------------------------------------------------------------------------


class TestVerifyDeployment:
    def test_no_urls_returns_empty(self, deployer: ForgeDeployer) -> None:
        result = deployer.verify_deployment()
        assert result == {}

    @patch("httpx.get")
    def test_backend_healthy(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp

        result = deployer.verify_deployment(backend_url="https://app.railway.app")
        assert result["backend"]["healthy"] is True
        assert result["backend"]["status_code"] == 200

    @patch("httpx.get")
    def test_backend_unhealthy(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        resp = MagicMock()
        resp.status_code = 503
        mock_get.return_value = resp

        result = deployer.verify_deployment(backend_url="https://app.railway.app")
        assert result["backend"]["healthy"] is False

    @patch("httpx.get")
    def test_backend_exception(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        mock_get.side_effect = Exception("Connection refused")

        result = deployer.verify_deployment(backend_url="https://app.railway.app")
        assert result["backend"]["healthy"] is False
        assert "Connection refused" in result["backend"]["error"]

    @patch("httpx.get")
    def test_frontend_healthy(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp

        result = deployer.verify_deployment(frontend_url="https://app.pages.dev")
        assert result["frontend"]["healthy"] is True

    @patch("httpx.get")
    def test_frontend_exception(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        mock_get.side_effect = TimeoutError("Timed out")

        result = deployer.verify_deployment(frontend_url="https://app.pages.dev")
        assert result["frontend"]["healthy"] is False
        assert result["frontend"]["error"] is not None

    @patch("httpx.get")
    def test_both_backend_and_frontend(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp

        result = deployer.verify_deployment(
            backend_url="https://api.railway.app",
            frontend_url="https://ui.pages.dev",
        )
        assert "backend" in result
        assert "frontend" in result

    @patch("httpx.get")
    def test_health_url_constructed_correctly(
        self, mock_get: MagicMock, deployer: ForgeDeployer
    ) -> None:
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp

        deployer.verify_deployment(backend_url="https://api.railway.app/")

        # The call should be to /health
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.railway.app/health"


# ---------------------------------------------------------------------------
# ForgeDeployer.run_smoke_tests
# ---------------------------------------------------------------------------


class TestRunSmokeTests:
    def test_no_urls_returns_empty(self, deployer: ForgeDeployer) -> None:
        result = deployer.run_smoke_tests()
        assert result.tests_run == 0
        assert result.tests_passed == 0
        assert result.passed is True  # no critical tests failed
        assert result.errors == []

    @patch("httpx.get")
    def test_backend_smoke_all_pass(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_404 = MagicMock()
        resp_404.status_code = 404
        # health=200, readiness=200, docs=200
        mock_get.return_value = resp_200

        result = deployer.run_smoke_tests(backend_url="https://api.railway.app")
        assert result.tests_run == 3
        assert result.passed is True
        assert result.errors == []

    @patch("httpx.get")
    def test_backend_health_fails(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        resp_500 = MagicMock()
        resp_500.status_code = 500
        mock_get.return_value = resp_500

        result = deployer.run_smoke_tests(backend_url="https://api.railway.app")
        assert result.passed is False
        assert any("Health check failed" in e for e in result.errors)

    @patch("httpx.get")
    def test_backend_health_exception(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        mock_get.side_effect = Exception("Network error")

        result = deployer.run_smoke_tests(backend_url="https://api.railway.app")
        assert result.passed is False
        assert any("Health check failed" in e for e in result.errors)

    @patch("httpx.get")
    def test_backend_readiness_404_accepted(
        self, mock_get: MagicMock, deployer: ForgeDeployer
    ) -> None:
        responses = [
            MagicMock(status_code=200),  # /health
            MagicMock(status_code=404),  # /api/v1/health/ready
            MagicMock(status_code=200),  # /docs
        ]
        mock_get.side_effect = responses

        result = deployer.run_smoke_tests(backend_url="https://api.railway.app")
        readiness = next(r for r in result.results if r["name"] == "backend_readiness")
        assert readiness["passed"] is True

    @patch("httpx.get")
    def test_backend_readiness_other_status_fails(
        self, mock_get: MagicMock, deployer: ForgeDeployer
    ) -> None:
        responses = [
            MagicMock(status_code=200),  # /health
            MagicMock(status_code=503),  # /api/v1/health/ready
            MagicMock(status_code=200),  # /docs
        ]
        mock_get.side_effect = responses

        result = deployer.run_smoke_tests(backend_url="https://api.railway.app")
        assert any("Readiness check failed" in e for e in result.errors)

    @patch("httpx.get")
    def test_frontend_smoke_html_and_js(
        self, mock_get: MagicMock, deployer: ForgeDeployer
    ) -> None:
        html_content = '<!DOCTYPE html><html><head></head><body><script src="bundle.js"></script></body></html>'
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html_content
        mock_get.return_value = resp

        result = deployer.run_smoke_tests(frontend_url="https://app.pages.dev")
        assert result.tests_run == 3
        assert result.passed is True

        html_test = next(r for r in result.results if r["name"] == "frontend_html_valid")
        js_test = next(r for r in result.results if r["name"] == "frontend_has_js")
        assert html_test["passed"] is True
        assert js_test["passed"] is True

    @patch("httpx.get")
    def test_frontend_load_fails(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        resp = MagicMock()
        resp.status_code = 503
        resp.text = ""
        mock_get.return_value = resp

        result = deployer.run_smoke_tests(frontend_url="https://app.pages.dev")
        assert result.passed is False
        assert any("Frontend load failed" in e for e in result.errors)

    @patch("httpx.get")
    def test_frontend_html_check_fails_no_html_tags(
        self, mock_get: MagicMock, deployer: ForgeDeployer
    ) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "plain text no html"
        mock_get.return_value = resp

        result = deployer.run_smoke_tests(frontend_url="https://app.pages.dev")
        html_test = next(r for r in result.results if r["name"] == "frontend_html_valid")
        assert html_test["passed"] is False
        assert any("Frontend did not return valid HTML" in e for e in result.errors)

    @patch("httpx.get")
    def test_frontend_exception_all_tests_fail(
        self, mock_get: MagicMock, deployer: ForgeDeployer
    ) -> None:
        mock_get.side_effect = Exception("timeout")

        result = deployer.run_smoke_tests(frontend_url="https://app.pages.dev")
        assert all(not r.get("passed") for r in result.results)

    @patch("httpx.get")
    def test_docs_failure_not_added_to_errors(
        self, mock_get: MagicMock, deployer: ForgeDeployer
    ) -> None:
        """Docs check failure is optional — should not appear in errors."""
        responses = [
            MagicMock(status_code=200),  # /health passes
            MagicMock(status_code=200),  # /ready passes
        ]
        # Raise on /docs
        responses.append(Exception("not found"))
        mock_get.side_effect = responses[:2] + [Exception("not found")]

        result = deployer.run_smoke_tests(backend_url="https://api.railway.app")
        # No error from docs
        assert all("docs" not in e.lower() for e in result.errors)

    @patch("httpx.get")
    def test_combined_backend_and_frontend(
        self, mock_get: MagicMock, deployer: ForgeDeployer
    ) -> None:
        html_content = "<!DOCTYPE html><html><body><script src='x.js'></script></body></html>"
        resp_200 = MagicMock(status_code=200, text=html_content)
        mock_get.return_value = resp_200

        result = deployer.run_smoke_tests(
            backend_url="https://api.railway.app",
            frontend_url="https://app.pages.dev",
        )
        # 3 backend + 3 frontend = 6 total
        assert result.tests_run == 6
        assert result.passed is True

    @patch("httpx.get")
    def test_html_tag_in_lowercase(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        """<html (lowercase without DOCTYPE) should still pass HTML check."""
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "<html><body>hello</body></html>"
        mock_get.return_value = resp

        result = deployer.run_smoke_tests(frontend_url="https://app.pages.dev")
        html_test = next(r for r in result.results if r["name"] == "frontend_html_valid")
        assert html_test["passed"] is True

    @patch("httpx.get")
    def test_js_with_type_module(self, mock_get: MagicMock, deployer: ForgeDeployer) -> None:
        """Frontend with type=module should be identified as having JS."""
        html_content = '<!DOCTYPE html><html><body><script type="module">import x from "./x.js"</script></body></html>'
        resp = MagicMock(status_code=200, text=html_content)
        mock_get.return_value = resp

        result = deployer.run_smoke_tests(frontend_url="https://app.pages.dev")
        js_test = next(r for r in result.results if r["name"] == "frontend_has_js")
        assert js_test["passed"] is True


# ---------------------------------------------------------------------------
# ForgeDeployer.full_deploy
# ---------------------------------------------------------------------------


class TestFullDeploy:
    @patch.object(ForgeDeployer, "check_quality_gates")
    def test_quality_gates_fail_stops_deploy(
        self,
        mock_gates: MagicMock,
        project_dir_full: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_full
        )
        mock_gates.return_value = QualityGateResult(
            passed=False,
            backend_coverage=40.0,
            frontend_build_ok=False,
            backend_tests_passed=0,
            backend_tests_failed=5,
            frontend_tests_passed=0,
            frontend_tests_failed=0,
            errors=["Coverage too low"],
        )

        result = deployer.full_deploy()
        assert result["success"] is False
        assert result["quality_gates"]["passed"] is False
        assert result["backend"] is None
        assert result["frontend"] is None

    @patch.object(ForgeDeployer, "check_quality_gates")
    @patch.object(ForgeDeployer, "deploy_backend")
    @patch.object(ForgeDeployer, "deploy_frontend")
    @patch.object(ForgeDeployer, "verify_deployment")
    @patch.object(ForgeDeployer, "run_smoke_tests")
    @patch("time.sleep")
    def test_full_deploy_success(
        self,
        mock_sleep: MagicMock,
        mock_smoke: MagicMock,
        mock_verify: MagicMock,
        mock_frontend: MagicMock,
        mock_backend: MagicMock,
        mock_gates: MagicMock,
        project_dir_full: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_full
        )
        mock_gates.return_value = QualityGateResult(
            passed=True,
            backend_coverage=85.0,
            frontend_build_ok=True,
            backend_tests_passed=20,
            backend_tests_failed=0,
            frontend_tests_passed=10,
            frontend_tests_failed=0,
            errors=[],
        )
        mock_backend.return_value = DeploymentResult(
            success=True,
            target="railway",
            url="https://api.railway.app",
            duration_seconds=10.0,
            error=None,
            logs="OK",
        )
        mock_frontend.return_value = DeploymentResult(
            success=True,
            target="cloudflare",
            url="https://app.pages.dev",
            duration_seconds=8.0,
            error=None,
            logs="OK",
        )
        mock_verify.return_value = {
            "backend": {"healthy": True},
            "frontend": {"healthy": True},
        }
        mock_smoke.return_value = SmokeTestResult(
            passed=True,
            tests_run=6,
            tests_passed=6,
            tests_failed=0,
            results=[],
            errors=[],
        )

        result = deployer.full_deploy()
        assert result["success"] is True
        assert result["backend"]["success"] is True
        assert result["frontend"]["success"] is True
        mock_sleep.assert_called_once_with(10)

    @patch.object(ForgeDeployer, "deploy_backend")
    @patch.object(ForgeDeployer, "deploy_frontend")
    @patch("time.sleep")
    def test_skip_quality_gates(
        self,
        mock_sleep: MagicMock,
        mock_frontend: MagicMock,
        mock_backend: MagicMock,
        project_dir_full: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_full
        )
        mock_backend.return_value = DeploymentResult(
            success=False, target="railway", url=None,
            duration_seconds=0, error="No token", logs="",
        )
        mock_frontend.return_value = DeploymentResult(
            success=False, target="cloudflare", url=None,
            duration_seconds=0, error="No token", logs="",
        )

        result = deployer.full_deploy(skip_quality_gates=True)
        # quality_gates key should be None
        assert result["quality_gates"] is None
        assert result["backend"] is not None
        assert result["frontend"] is not None

    @patch.object(ForgeDeployer, "check_quality_gates")
    @patch.object(ForgeDeployer, "deploy_backend")
    @patch("time.sleep")
    def test_backend_only(
        self,
        mock_sleep: MagicMock,
        mock_backend: MagicMock,
        mock_gates: MagicMock,
        project_dir_full: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_full
        )
        mock_gates.return_value = QualityGateResult(
            passed=True, backend_coverage=80.0, frontend_build_ok=False,
            backend_tests_passed=5, backend_tests_failed=0,
            frontend_tests_passed=0, frontend_tests_failed=0, errors=[],
        )
        mock_backend.return_value = DeploymentResult(
            success=True, target="railway", url=None,
            duration_seconds=5.0, error=None, logs="",
        )

        result = deployer.full_deploy(backend_only=True)
        assert result["frontend"] is None

    @patch.object(ForgeDeployer, "check_quality_gates")
    @patch.object(ForgeDeployer, "deploy_frontend")
    @patch("time.sleep")
    def test_frontend_only(
        self,
        mock_sleep: MagicMock,
        mock_frontend: MagicMock,
        mock_gates: MagicMock,
        project_dir_full: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_full
        )
        mock_gates.return_value = QualityGateResult(
            passed=True, backend_coverage=None, frontend_build_ok=True,
            backend_tests_passed=0, backend_tests_failed=0,
            frontend_tests_passed=5, frontend_tests_failed=0, errors=[],
        )
        mock_frontend.return_value = DeploymentResult(
            success=True, target="cloudflare", url=None,
            duration_seconds=5.0, error=None, logs="",
        )

        result = deployer.full_deploy(frontend_only=True)
        assert result["backend"] is None

    @patch.object(ForgeDeployer, "check_quality_gates")
    @patch.object(ForgeDeployer, "deploy_backend")
    @patch.object(ForgeDeployer, "deploy_frontend")
    @patch.object(ForgeDeployer, "verify_deployment")
    @patch.object(ForgeDeployer, "run_smoke_tests")
    @patch("time.sleep")
    def test_unhealthy_verification_sets_success_false(
        self,
        mock_sleep: MagicMock,
        mock_smoke: MagicMock,
        mock_verify: MagicMock,
        mock_frontend: MagicMock,
        mock_backend: MagicMock,
        mock_gates: MagicMock,
        project_dir_full: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_full
        )
        mock_gates.return_value = QualityGateResult(
            passed=True, backend_coverage=80.0, frontend_build_ok=True,
            backend_tests_passed=10, backend_tests_failed=0,
            frontend_tests_passed=5, frontend_tests_failed=0, errors=[],
        )
        mock_backend.return_value = DeploymentResult(
            success=True, target="railway", url="https://api.railway.app",
            duration_seconds=5.0, error=None, logs="",
        )
        mock_frontend.return_value = DeploymentResult(
            success=True, target="cloudflare", url="https://app.pages.dev",
            duration_seconds=3.0, error=None, logs="",
        )
        mock_verify.return_value = {
            "backend": {"healthy": False},
        }
        mock_smoke.return_value = SmokeTestResult(
            passed=True, tests_run=3, tests_passed=3, tests_failed=0,
            results=[], errors=[],
        )

        result = deployer.full_deploy()
        assert result["success"] is False

    @patch.object(ForgeDeployer, "check_quality_gates")
    @patch.object(ForgeDeployer, "deploy_backend")
    @patch.object(ForgeDeployer, "deploy_frontend")
    @patch.object(ForgeDeployer, "verify_deployment")
    @patch.object(ForgeDeployer, "run_smoke_tests")
    @patch("time.sleep")
    def test_failed_smoke_sets_success_false(
        self,
        mock_sleep: MagicMock,
        mock_smoke: MagicMock,
        mock_verify: MagicMock,
        mock_frontend: MagicMock,
        mock_backend: MagicMock,
        mock_gates: MagicMock,
        project_dir_full: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_full
        )
        mock_gates.return_value = QualityGateResult(
            passed=True, backend_coverage=80.0, frontend_build_ok=True,
            backend_tests_passed=10, backend_tests_failed=0,
            frontend_tests_passed=5, frontend_tests_failed=0, errors=[],
        )
        mock_backend.return_value = DeploymentResult(
            success=True, target="railway", url="https://api.railway.app",
            duration_seconds=5.0, error=None, logs="",
        )
        mock_frontend.return_value = DeploymentResult(
            success=True, target="cloudflare", url=None,
            duration_seconds=3.0, error=None, logs="",
        )
        mock_verify.return_value = {"backend": {"healthy": True}}
        mock_smoke.return_value = SmokeTestResult(
            passed=False, tests_run=3, tests_passed=2, tests_failed=1,
            results=[], errors=["Health failed"],
        )

        result = deployer.full_deploy()
        assert result["success"] is False
        assert result["smoke_tests"]["passed"] is False

    @patch.object(ForgeDeployer, "check_quality_gates")
    @patch.object(ForgeDeployer, "deploy_backend")
    @patch.object(ForgeDeployer, "deploy_frontend")
    def test_no_urls_skips_verification(
        self,
        mock_frontend: MagicMock,
        mock_backend: MagicMock,
        mock_gates: MagicMock,
        project_dir_full: Path,
    ) -> None:
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir_full
        )
        mock_gates.return_value = QualityGateResult(
            passed=True, backend_coverage=80.0, frontend_build_ok=True,
            backend_tests_passed=5, backend_tests_failed=0,
            frontend_tests_passed=5, frontend_tests_failed=0, errors=[],
        )
        # Both deployments succeed but return no URL
        mock_backend.return_value = DeploymentResult(
            success=True, target="railway", url=None,
            duration_seconds=5.0, error=None, logs="",
        )
        mock_frontend.return_value = DeploymentResult(
            success=True, target="cloudflare", url=None,
            duration_seconds=3.0, error=None, logs="",
        )

        result = deployer.full_deploy()
        # Verification is skipped when no URLs available
        assert result["verification"] is None
        assert result["success"] is True


# ---------------------------------------------------------------------------
# ForgeDeployer.deploy_with_provider
# ---------------------------------------------------------------------------


class TestDeployWithProvider:
    @patch("forge_harness.deployment_legacy.ForgeDeployer.deploy_with_provider")
    def test_deploy_with_provider_called(
        self, mock_method: MagicMock, project_dir: Path
    ) -> None:
        """Smoke test: ensure deploy_with_provider is callable and delegates."""
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir
        )
        from forge_harness.deployment_legacy import DeploymentResult as LegacyResult

        mock_method.return_value = LegacyResult(
            success=True,
            target="railway",
            url="https://app.railway.app",
            duration_seconds=5.0,
            error=None,
            logs="ok",
        )

        result = deployer.deploy_with_provider("railway", dry_run=True)
        assert result.success is True
        mock_method.assert_called_once_with("railway", dry_run=True)

    def test_deploy_with_provider_invalid_provider(
        self, project_dir: Path
    ) -> None:
        """Unknown provider should raise KeyError from ProviderRegistry."""
        deployer = ForgeDeployer(
            domain="d", project="p", project_dir=project_dir
        )
        with pytest.raises(KeyError):
            deployer.deploy_with_provider("nonexistent-provider")
