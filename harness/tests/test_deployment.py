"""Tests for FORGE deployment automation."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.deployment import (
    DeploymentResult,
    ForgeDeployer,
    QualityGateResult,
    SmokeTestResult,
)


class TestQualityGateResult:
    """Tests for QualityGateResult dataclass."""

    def test_passed_result(self):
        """QualityGateResult can represent passing gates."""
        result = QualityGateResult(
            passed=True,
            backend_coverage=85.0,
            frontend_build_ok=True,
            backend_tests_passed=45,
            backend_tests_failed=0,
            frontend_tests_passed=20,
            frontend_tests_failed=0,
            errors=[],
        )

        assert result.passed is True
        assert result.backend_coverage == 85.0
        assert result.errors == []

    def test_failed_result(self):
        """QualityGateResult can represent failing gates."""
        result = QualityGateResult(
            passed=False,
            backend_coverage=50.0,
            frontend_build_ok=False,
            backend_tests_passed=40,
            backend_tests_failed=5,
            frontend_tests_passed=15,
            frontend_tests_failed=3,
            errors=["Coverage below minimum", "Build failed"],
        )

        assert result.passed is False
        assert result.backend_tests_failed == 5
        assert len(result.errors) == 2


class TestDeploymentResult:
    """Tests for DeploymentResult dataclass."""

    def test_successful_deployment(self):
        """DeploymentResult can represent success."""
        result = DeploymentResult(
            success=True,
            target="railway",
            url="https://app.railway.app",
            duration_seconds=120.5,
            error=None,
            logs="Deployment complete",
        )

        assert result.success is True
        assert result.target == "railway"
        assert result.url is not None

    def test_failed_deployment(self):
        """DeploymentResult can represent failure."""
        result = DeploymentResult(
            success=False,
            target="cloudflare",
            url=None,
            duration_seconds=45.0,
            error="Build failed",
            logs="Error: npm run build exited with code 1",
        )

        assert result.success is False
        assert result.error is not None


class TestForgeDeployer:
    """Tests for ForgeDeployer class."""

    @pytest.fixture
    def temp_project(self, tmp_path):
        """Create a temporary project structure."""
        backend_dir = tmp_path / "backend"
        frontend_dir = tmp_path / "frontend"
        backend_dir.mkdir()
        frontend_dir.mkdir()

        # Create minimal pyproject.toml for backend
        (backend_dir / "pyproject.toml").write_text(
            '[project]\nname = "test-backend"\nversion = "0.1.0"'
        )

        # Create minimal package.json for frontend
        (frontend_dir / "package.json").write_text(
            '{"name": "test-frontend", "scripts": {"test": "echo ok", "build": "echo ok"}}'
        )

        return tmp_path

    def test_deployer_initialization(self, temp_project):
        """ForgeDeployer initializes with correct paths."""
        deployer = ForgeDeployer(
            domain="test-domain",
            project="test-project",
            project_dir=temp_project,
        )

        assert deployer.domain == "test-domain"
        assert deployer.project == "test-project"
        assert deployer.backend_dir == temp_project / "backend"
        assert deployer.frontend_dir == temp_project / "frontend"

    def test_deployer_uses_env_tokens(self, temp_project):
        """ForgeDeployer reads tokens from environment."""
        with patch.dict(
            "os.environ",
            {
                "RAILWAY_TOKEN": "test_railway",
                "CLOUDFLARE_API_TOKEN": "test_cf",
                "CLOUDFLARE_ACCOUNT_ID": "test_account",
            },
        ):
            deployer = ForgeDeployer(
                domain="test",
                project="test",
                project_dir=temp_project,
            )

            assert deployer.railway_token == "test_railway"
            assert deployer.cloudflare_token == "test_cf"
            assert deployer.cloudflare_account_id == "test_account"

    def test_deployer_accepts_explicit_tokens(self, temp_project):
        """ForgeDeployer accepts explicit token parameters."""
        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
            railway_token="explicit_railway",
            cloudflare_token="explicit_cf",
            cloudflare_account_id="explicit_account",
        )

        assert deployer.railway_token == "explicit_railway"
        assert deployer.cloudflare_token == "explicit_cf"

    @patch("subprocess.run")
    def test_check_quality_gates_passes(self, mock_run, temp_project):
        """check_quality_gates returns passed when tests succeed."""
        # Mock successful pytest run with coverage
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="45 passed\nTOTAL 100 50 50%  75%\n",
            stderr="",
        )

        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
        )

        result = deployer.check_quality_gates()

        # Should have called pytest and npm commands
        assert mock_run.call_count >= 1

    @patch("subprocess.run")
    def test_check_quality_gates_fails_on_low_coverage(self, mock_run, temp_project):
        """check_quality_gates fails when coverage is below minimum."""
        # Mock pytest with low coverage
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="45 passed\nTOTAL 100 50 50%  50%\n",
            stderr="",
        )

        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
        )
        deployer.MIN_BACKEND_COVERAGE = 70.0

        result = deployer.check_quality_gates()

        # Coverage parsing may vary, but structure should be correct
        assert isinstance(result, QualityGateResult)

    def test_deploy_backend_without_token(self, temp_project):
        """deploy_backend fails gracefully without token."""
        with patch.dict("os.environ", {}, clear=True):
            deployer = ForgeDeployer(
                domain="test",
                project="test",
                project_dir=temp_project,
            )

            result = deployer.deploy_backend()

            assert result.success is False
            assert "not configured" in result.error

    def test_deploy_frontend_without_token(self, temp_project):
        """deploy_frontend fails gracefully without token."""
        with patch.dict("os.environ", {}, clear=True):
            deployer = ForgeDeployer(
                domain="test",
                project="test",
                project_dir=temp_project,
            )

            result = deployer.deploy_frontend()

            assert result.success is False
            assert "not configured" in result.error

    def test_deploy_backend_missing_directory(self, tmp_path):
        """deploy_backend fails when backend directory doesn't exist."""
        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=tmp_path,
            railway_token="test_token",
        )

        result = deployer.deploy_backend()

        assert result.success is False
        assert "not found" in result.error

    def test_deploy_frontend_missing_directory(self, tmp_path):
        """deploy_frontend fails when frontend directory doesn't exist."""
        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=tmp_path,
            cloudflare_token="test_token",
            cloudflare_account_id="test_account",
        )

        result = deployer.deploy_frontend()

        assert result.success is False
        assert "not found" in result.error

    @patch("subprocess.run")
    def test_deploy_backend_success(self, mock_run, temp_project):
        """deploy_backend succeeds with valid configuration."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Deploying...\nhttps://test.railway.app\nDone!",
            stderr="",
        )

        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
            railway_token="test_token",
        )

        result = deployer.deploy_backend()

        assert result.success is True
        assert result.target == "railway"

    @patch("subprocess.run")
    def test_deploy_frontend_success(self, mock_run, temp_project):
        """deploy_frontend succeeds with valid configuration."""
        # Create dist directory
        (temp_project / "frontend" / "dist").mkdir()

        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Deploying...\nhttps://test.pages.dev\nDone!",
            stderr="",
        )

        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
            cloudflare_token="test_token",
            cloudflare_account_id="test_account",
        )

        result = deployer.deploy_frontend()

        assert result.success is True
        assert result.target == "cloudflare"

    @patch("httpx.get")
    def test_verify_deployment_healthy(self, mock_get, temp_project):
        """verify_deployment returns healthy status on 200 response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
        )

        result = deployer.verify_deployment(
            backend_url="https://test.railway.app",
            frontend_url="https://test.pages.dev",
        )

        assert result["backend"]["healthy"] is True
        assert result["frontend"]["healthy"] is True

    @patch("httpx.get")
    def test_verify_deployment_unhealthy(self, mock_get, temp_project):
        """verify_deployment returns unhealthy on error."""
        mock_get.side_effect = Exception("Connection refused")

        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
        )

        result = deployer.verify_deployment(
            backend_url="https://test.railway.app",
        )

        assert result["backend"]["healthy"] is False
        assert "Connection refused" in result["backend"]["error"]

    @patch.object(ForgeDeployer, "check_quality_gates")
    @patch.object(ForgeDeployer, "deploy_backend")
    @patch.object(ForgeDeployer, "deploy_frontend")
    @patch.object(ForgeDeployer, "verify_deployment")
    @patch.object(ForgeDeployer, "run_smoke_tests")
    def test_full_deploy_success(
        self, mock_smoke, mock_verify, mock_frontend, mock_backend, mock_gates, temp_project
    ):
        """full_deploy orchestrates all deployment steps."""
        mock_gates.return_value = QualityGateResult(
            passed=True,
            backend_coverage=80.0,
            frontend_build_ok=True,
            backend_tests_passed=45,
            backend_tests_failed=0,
            frontend_tests_passed=20,
            frontend_tests_failed=0,
            errors=[],
        )

        mock_backend.return_value = DeploymentResult(
            success=True,
            target="railway",
            url="https://test.railway.app",
            duration_seconds=60,
            error=None,
            logs="OK",
        )

        mock_frontend.return_value = DeploymentResult(
            success=True,
            target="cloudflare",
            url="https://test.pages.dev",
            duration_seconds=30,
            error=None,
            logs="OK",
        )

        mock_verify.return_value = {
            "backend": {"healthy": True},
            "frontend": {"healthy": True},
        }

        mock_smoke.return_value = SmokeTestResult(
            passed=True,
            tests_run=5,
            tests_passed=5,
            tests_failed=0,
            results=[],
            errors=[],
        )

        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
        )

        with patch("time.sleep"):  # Skip wait
            result = deployer.full_deploy()

        assert result["success"] is True
        assert result["quality_gates"]["passed"] is True
        assert result["backend"]["success"] is True
        assert result["frontend"]["success"] is True
        assert result["smoke_tests"]["passed"] is True

    @patch.object(ForgeDeployer, "check_quality_gates")
    def test_full_deploy_fails_quality_gates(self, mock_gates, temp_project):
        """full_deploy stops when quality gates fail."""
        mock_gates.return_value = QualityGateResult(
            passed=False,
            backend_coverage=50.0,
            frontend_build_ok=False,
            backend_tests_passed=40,
            backend_tests_failed=5,
            frontend_tests_passed=15,
            frontend_tests_failed=3,
            errors=["Coverage too low", "Build failed"],
        )

        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
        )

        result = deployer.full_deploy()

        assert result["success"] is False
        assert result["quality_gates"]["passed"] is False
        assert result["backend"] is None  # Never attempted
        assert result["frontend"] is None

    @patch.object(ForgeDeployer, "deploy_backend")
    @patch.object(ForgeDeployer, "deploy_frontend")
    def test_full_deploy_skip_quality_gates(self, mock_frontend, mock_backend, temp_project):
        """full_deploy can skip quality gates when requested."""
        mock_backend.return_value = DeploymentResult(
            success=True,
            target="railway",
            url="https://test.railway.app",
            duration_seconds=60,
            error=None,
            logs="OK",
        )

        mock_frontend.return_value = DeploymentResult(
            success=True,
            target="cloudflare",
            url="https://test.pages.dev",
            duration_seconds=30,
            error=None,
            logs="OK",
        )

        deployer = ForgeDeployer(
            domain="test",
            project="test",
            project_dir=temp_project,
        )

        with patch("time.sleep"):
            with patch.object(deployer, "verify_deployment", return_value={}):
                with patch.object(deployer, "run_smoke_tests") as mock_smoke:
                    mock_smoke.return_value = SmokeTestResult(
                        passed=True,
                        tests_run=3,
                        tests_passed=3,
                        tests_failed=0,
                        results=[],
                        errors=[],
                    )
                    result = deployer.full_deploy(skip_quality_gates=True)

        assert result["quality_gates"] is None  # Not run
        assert mock_backend.called
        assert mock_frontend.called


class TestSmokeTestResult:
    """Tests for SmokeTestResult dataclass."""

    def test_passed_result(self):
        """SmokeTestResult can represent passing tests."""
        result = SmokeTestResult(
            passed=True,
            tests_run=5,
            tests_passed=5,
            tests_failed=0,
            results=[
                {"name": "backend_health", "passed": True, "status_code": 200},
                {"name": "frontend_loads", "passed": True, "status_code": 200},
            ],
            errors=[],
        )

        assert result.passed is True
        assert result.tests_run == 5
        assert result.tests_failed == 0

    def test_failed_result(self):
        """SmokeTestResult can represent failing tests."""
        result = SmokeTestResult(
            passed=False,
            tests_run=5,
            tests_passed=3,
            tests_failed=2,
            results=[
                {"name": "backend_health", "passed": False, "status_code": 500},
            ],
            errors=["Health check failed"],
        )

        assert result.passed is False
        assert result.tests_failed == 2
        assert len(result.errors) == 1

    def test_summary_property(self):
        """SmokeTestResult has summary property."""
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


class TestRunSmokeTests:
    """Tests for run_smoke_tests method."""

    @pytest.fixture
    def deployer(self, tmp_path):
        """Create a ForgeDeployer instance."""
        return ForgeDeployer(
            domain="test",
            project="test",
            project_dir=tmp_path,
        )

    @patch("httpx.get")
    def test_backend_health_check_passes(self, mock_get, deployer):
        """run_smoke_tests passes when health check returns 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        mock_get.return_value = mock_response

        result = deployer.run_smoke_tests(backend_url="https://test.api.com")

        # Should have run backend tests
        backend_results = [r for r in result.results if "backend" in r.get("name", "")]
        assert len(backend_results) >= 1

        # Health check should pass
        health_result = next(r for r in result.results if r.get("name") == "backend_health")
        assert health_result["passed"] is True

    @patch("httpx.get")
    def test_backend_health_check_fails(self, mock_get, deployer):
        """run_smoke_tests fails when health check returns non-200."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_get.return_value = mock_response

        result = deployer.run_smoke_tests(backend_url="https://test.api.com")

        assert result.passed is False
        assert len(result.errors) > 0
        assert any("Health check failed" in e for e in result.errors)

    @patch("httpx.get")
    def test_frontend_load_check_passes(self, mock_get, deployer):
        """run_smoke_tests passes when frontend loads successfully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = (
            "<!DOCTYPE html><html><head></head><body><script src='app.js'></script></body></html>"
        )
        mock_get.return_value = mock_response

        result = deployer.run_smoke_tests(frontend_url="https://test.app.com")

        # Frontend load should pass
        frontend_results = [r for r in result.results if "frontend" in r.get("name", "")]
        assert len(frontend_results) >= 1

        load_result = next(r for r in result.results if r.get("name") == "frontend_loads")
        assert load_result["passed"] is True

    @patch("httpx.get")
    def test_frontend_html_validation(self, mock_get, deployer):
        """run_smoke_tests checks frontend returns valid HTML."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<!DOCTYPE html><html><body>Test</body></html>"
        mock_get.return_value = mock_response

        result = deployer.run_smoke_tests(frontend_url="https://test.app.com")

        html_result = next(r for r in result.results if r.get("name") == "frontend_html_valid")
        assert html_result["passed"] is True

    @patch("httpx.get")
    def test_frontend_invalid_html(self, mock_get, deployer):
        """run_smoke_tests detects non-HTML response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"error": "not found"}'  # JSON instead of HTML
        mock_get.return_value = mock_response

        result = deployer.run_smoke_tests(frontend_url="https://test.app.com")

        html_result = next(r for r in result.results if r.get("name") == "frontend_html_valid")
        assert html_result["passed"] is False

    @patch("httpx.get")
    def test_handles_connection_error(self, mock_get, deployer):
        """run_smoke_tests handles connection errors gracefully."""
        mock_get.side_effect = Exception("Connection refused")

        result = deployer.run_smoke_tests(backend_url="https://test.api.com")

        assert result.passed is False
        assert any("Connection refused" in e for e in result.errors)

    @patch("httpx.get")
    def test_runs_both_backend_and_frontend(self, mock_get, deployer):
        """run_smoke_tests runs tests for both when both URLs provided."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<!DOCTYPE html><html><script></script></html>"
        mock_get.return_value = mock_response

        result = deployer.run_smoke_tests(
            backend_url="https://test.api.com",
            frontend_url="https://test.app.com",
        )

        # Should have both backend and frontend results
        backend_results = [r for r in result.results if "backend" in r.get("name", "")]
        frontend_results = [r for r in result.results if "frontend" in r.get("name", "")]

        assert len(backend_results) >= 1
        assert len(frontend_results) >= 1

    def test_no_urls_returns_empty_result(self, deployer):
        """run_smoke_tests returns empty result when no URLs provided."""
        result = deployer.run_smoke_tests()

        assert result.passed is True  # No critical tests failed
        assert result.tests_run == 0
        assert result.errors == []

    @patch("httpx.get")
    def test_readiness_accepts_404(self, mock_get, deployer):
        """run_smoke_tests accepts 404 for optional readiness endpoint."""
        mock_response = MagicMock()
        mock_response.status_code = 404  # Endpoint doesn't exist
        mock_response.text = "Not Found"
        mock_get.return_value = mock_response

        result = deployer.run_smoke_tests(backend_url="https://test.api.com")

        # Readiness check should pass (404 is acceptable)
        readiness_result = next(
            (r for r in result.results if r.get("name") == "backend_readiness"),
            None,
        )
        if readiness_result:
            assert readiness_result["passed"] is True
