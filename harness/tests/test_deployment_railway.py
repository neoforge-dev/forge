"""
Tests for forge_harness/deployment/railway.py

Covers RailwayProvider with mocked external dependencies.
Target: 80%+ coverage of the 297-line source.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.deployment.base import (
    DeploymentConfig,
    DeploymentResult,
    DeploymentTarget,
)
from forge_harness.deployment.railway import RailwayProvider

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def make_config(
    tmp_path: Path,
    *,
    target: DeploymentTarget = DeploymentTarget.BACKEND,
    dry_run: bool = False,
    timeout: int = 300,
    extra_env: dict[str, str] | None = None,
    provider_options: dict[str, Any] | None = None,
    create_backend: bool = True,
) -> DeploymentConfig:
    """Return a DeploymentConfig whose backend_dir optionally exists."""
    project_dir = tmp_path / "my-project"
    if create_backend:
        backend_dir = project_dir / "backend"
        backend_dir.mkdir(parents=True, exist_ok=True)

    return DeploymentConfig(
        domain="test-domain",
        project="test-project",
        project_dir=project_dir,
        target=target,
        dry_run=dry_run,
        timeout=timeout,
        extra_env=extra_env or {},
        provider_options=provider_options or {},
    )


def make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


@pytest.fixture
def provider() -> RailwayProvider:
    return RailwayProvider()


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_name_is_railway(self, provider: RailwayProvider) -> None:
        assert provider.capabilities.name == "railway"

    def test_targets_contains_backend(self, provider: RailwayProvider) -> None:
        assert DeploymentTarget.BACKEND in provider.capabilities.targets

    def test_supports_rollback(self, provider: RailwayProvider) -> None:
        assert provider.capabilities.supports_rollback is True

    def test_supports_preview(self, provider: RailwayProvider) -> None:
        assert provider.capabilities.supports_preview is True

    def test_supports_health_check(self, provider: RailwayProvider) -> None:
        assert provider.capabilities.supports_health_check is True

    def test_required_env_vars(self, provider: RailwayProvider) -> None:
        assert "RAILWAY_TOKEN" in provider.capabilities.required_env_vars

    def test_optional_env_vars(self, provider: RailwayProvider) -> None:
        caps = provider.capabilities
        assert "RAILWAY_PROJECT_ID" in caps.optional_env_vars
        assert "RAILWAY_SERVICE_ID" in caps.optional_env_vars


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


class TestIsConfigured:
    def test_configured_when_token_present(
        self, provider: RailwayProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        assert provider.is_configured() is True

    def test_not_configured_when_token_absent(
        self, provider: RailwayProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
        assert provider.is_configured() is False

    def test_missing_config_returns_token_name(
        self, provider: RailwayProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
        assert "RAILWAY_TOKEN" in provider.missing_config()

    def test_missing_config_empty_when_configured(
        self, provider: RailwayProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        assert provider.missing_config() == []


# ---------------------------------------------------------------------------
# deploy – token missing
# ---------------------------------------------------------------------------


class TestDeployMissingToken:
    def test_returns_failure_result(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
        config = make_config(tmp_path)

        result = provider.deploy(config)

        assert result.success is False
        assert result.provider == "railway"
        assert "RAILWAY_TOKEN" in result.error

    def test_target_value_preserved(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
        config = make_config(tmp_path, target=DeploymentTarget.BACKEND)

        result = provider.deploy(config)

        assert result.target == "backend"


# ---------------------------------------------------------------------------
# deploy – backend dir not found
# ---------------------------------------------------------------------------


class TestDeployMissingBackendDir:
    def test_returns_failure_when_no_backend_dir(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path, create_backend=False)

        result = provider.deploy(config)

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_error_message_contains_path(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path, create_backend=False)

        result = provider.deploy(config)

        assert str(config.backend_dir) in result.error


# ---------------------------------------------------------------------------
# deploy – dry run
# ---------------------------------------------------------------------------


class TestDeployDryRun:
    def test_dry_run_success(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path, dry_run=True)

        result = provider.deploy(config)

        assert result.success is True

    def test_dry_run_url_is_dry_run_url(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path, dry_run=True)

        result = provider.deploy(config)

        assert result.url == "https://dry-run.railway.app"

    def test_dry_run_metadata_flag(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path, dry_run=True)

        result = provider.deploy(config)

        assert result.metadata.get("dry_run") is True

    def test_dry_run_logs_contain_command(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path, dry_run=True)

        result = provider.deploy(config)

        assert "DRY RUN" in result.logs
        assert "railway up" in result.logs

    def test_dry_run_does_not_call_run_command(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path, dry_run=True)

        with patch.object(provider, "_run_command") as mock_run:
            provider.deploy(config)
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# deploy – successful real run
# ---------------------------------------------------------------------------


class TestDeploySuccess:
    def test_success_result(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        stdout = "Deploying...\nhttps://my-service.railway.app\nDone"
        proc = make_completed_process(returncode=0, stdout=stdout)

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.deploy(config)

        assert result.success is True

    def test_url_extracted_from_stdout(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        stdout = "Deploying to railway...\nhttps://my-service.railway.app"
        proc = make_completed_process(returncode=0, stdout=stdout)

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.deploy(config)

        assert result.url == "https://my-service.railway.app"

    def test_duration_is_positive(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0, stdout="ok railway done")

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.deploy(config)

        assert result.duration_seconds >= 0

    def test_logs_set_from_stdout(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        stdout = "build output from railway"
        proc = make_completed_process(returncode=0, stdout=stdout)

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.deploy(config)

        assert result.logs == stdout

    def test_provider_name_in_result(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.deploy(config)

        assert result.provider == "railway"


# ---------------------------------------------------------------------------
# deploy – service_id handling
# ---------------------------------------------------------------------------


class TestDeployServiceId:
    def test_service_id_from_provider_options(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        monkeypatch.delenv("RAILWAY_SERVICE_ID", raising=False)
        config = make_config(tmp_path, provider_options={"service_id": "svc-123"})
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc) as mock_run:
            provider.deploy(config)

        cmd_used = mock_run.call_args[0][0]
        assert "--service" in cmd_used
        assert "svc-123" in cmd_used

    def test_service_id_from_env_var(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        monkeypatch.setenv("RAILWAY_SERVICE_ID", "env-svc-456")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc) as mock_run:
            provider.deploy(config)

        cmd_used = mock_run.call_args[0][0]
        assert "--service" in cmd_used
        assert "env-svc-456" in cmd_used

    def test_no_service_flag_when_no_service_id(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        monkeypatch.delenv("RAILWAY_SERVICE_ID", raising=False)
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc) as mock_run:
            provider.deploy(config)

        cmd_used = mock_run.call_args[0][0]
        assert "--service" not in cmd_used

    def test_provider_options_service_id_takes_precedence_over_env(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        monkeypatch.setenv("RAILWAY_SERVICE_ID", "env-svc")
        config = make_config(tmp_path, provider_options={"service_id": "opts-svc"})
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc) as mock_run:
            provider.deploy(config)

        cmd_used = mock_run.call_args[0][0]
        assert "opts-svc" in cmd_used


# ---------------------------------------------------------------------------
# deploy – extra_env forwarded to _run_command
# ---------------------------------------------------------------------------


class TestDeployExtraEnv:
    def test_extra_env_merged_into_command_env(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path, extra_env={"MY_VAR": "hello"})
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc) as mock_run:
            provider.deploy(config)

        env_used = mock_run.call_args[1]["env"]
        assert env_used["MY_VAR"] == "hello"
        assert env_used["RAILWAY_TOKEN"] == "tok_abc"


# ---------------------------------------------------------------------------
# deploy – command failure (non-zero returncode)
# ---------------------------------------------------------------------------


class TestDeployFailure:
    def test_failure_result_on_nonzero_returncode(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=1, stderr="deploy error", stdout="partial")

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.deploy(config)

        assert result.success is False
        assert result.error == "deploy error"
        assert result.logs == "partial"

    def test_duration_set_on_failure(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=2, stderr="error")

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.deploy(config)

        assert result.duration_seconds >= 0


# ---------------------------------------------------------------------------
# deploy – exception from _run_command
# ---------------------------------------------------------------------------


class TestDeployException:
    def test_exception_returns_failure(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)

        with patch.object(provider, "_run_command", side_effect=RuntimeError("timeout")):
            result = provider.deploy(config)

        assert result.success is False
        assert "timeout" in result.error

    def test_exception_error_contains_message(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)

        with patch.object(provider, "_run_command", side_effect=OSError("binary not found")):
            result = provider.deploy(config)

        assert "binary not found" in result.error

    def test_timeout_exception_handled(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)

        with patch.object(
            provider, "_run_command", side_effect=subprocess.TimeoutExpired(cmd="railway", timeout=300)
        ):
            result = provider.deploy(config)

        assert result.success is False
        assert result.duration_seconds >= 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerify:
    def test_healthy_on_200(self, provider: RailwayProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("forge_harness.deployment.railway.httpx.get", return_value=mock_resp):
            result = provider.verify("https://app.railway.app")

        assert result["healthy"] is True
        assert result["status_code"] == 200
        assert result["error"] is None

    def test_unhealthy_on_non_200(self, provider: RailwayProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("forge_harness.deployment.railway.httpx.get", return_value=mock_resp):
            result = provider.verify("https://app.railway.app")

        assert result["healthy"] is False
        assert result["status_code"] == 503

    def test_url_preserved_in_result(self, provider: RailwayProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("forge_harness.deployment.railway.httpx.get", return_value=mock_resp):
            result = provider.verify("https://app.railway.app")

        assert result["url"] == "https://app.railway.app"

    def test_health_check_calls_health_endpoint(self, provider: RailwayProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("forge_harness.deployment.railway.httpx.get", return_value=mock_resp) as mock_get:
            provider.verify("https://app.railway.app")

        called_url = mock_get.call_args[0][0]
        assert called_url == "https://app.railway.app/health"

    def test_trailing_slash_stripped_before_health_path(self, provider: RailwayProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("forge_harness.deployment.railway.httpx.get", return_value=mock_resp) as mock_get:
            provider.verify("https://app.railway.app/")

        called_url = mock_get.call_args[0][0]
        assert called_url == "https://app.railway.app/health"

    def test_timeout_passed_to_httpx(self, provider: RailwayProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("forge_harness.deployment.railway.httpx.get", return_value=mock_resp) as mock_get:
            provider.verify("https://app.railway.app", timeout=60)

        assert mock_get.call_args[1]["timeout"] == 60

    def test_follow_redirects_enabled(self, provider: RailwayProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("forge_harness.deployment.railway.httpx.get", return_value=mock_resp) as mock_get:
            provider.verify("https://app.railway.app")

        assert mock_get.call_args[1]["follow_redirects"] is True

    def test_exception_returns_unhealthy(self, provider: RailwayProvider) -> None:
        with patch(
            "forge_harness.deployment.railway.httpx.get",
            side_effect=Exception("connection refused"),
        ):
            result = provider.verify("https://app.railway.app")

        assert result["healthy"] is False
        assert result["status_code"] is None
        assert "connection refused" in result["error"]

    def test_httpx_connect_error_handled(self, provider: RailwayProvider) -> None:
        import httpx as httpx_module

        with patch(
            "forge_harness.deployment.railway.httpx.get",
            side_effect=httpx_module.ConnectError("unreachable"),
        ):
            result = provider.verify("https://app.railway.app")

        assert result["healthy"] is False

    def test_default_timeout_is_30(self, provider: RailwayProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("forge_harness.deployment.railway.httpx.get", return_value=mock_resp) as mock_get:
            provider.verify("https://app.railway.app")

        assert mock_get.call_args[1]["timeout"] == 30


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rollback_missing_token(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
        config = make_config(tmp_path)

        result = provider.rollback(config)

        assert result.success is False
        assert "RAILWAY_TOKEN" in result.error

    def test_rollback_success(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0, stdout="rolled back")

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.rollback(config)

        assert result.success is True
        assert result.logs == "rolled back"

    def test_rollback_failure(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=1, stderr="nothing to roll back")

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.rollback(config)

        assert result.success is False
        assert result.error == "nothing to roll back"

    def test_rollback_with_version(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc) as mock_run:
            provider.rollback(config, version="deploy-abc123")

        cmd_used = mock_run.call_args[0][0]
        assert "deploy-abc123" in cmd_used

    def test_rollback_without_version(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc) as mock_run:
            provider.rollback(config)

        cmd_used = mock_run.call_args[0][0]
        assert cmd_used == ["railway", "rollback"]

    def test_rollback_exception_returns_failure(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)

        with patch.object(provider, "_run_command", side_effect=RuntimeError("boom")):
            result = provider.rollback(config)

        assert result.success is False
        assert "boom" in result.error

    def test_rollback_duration_set(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.rollback(config)

        assert result.duration_seconds >= 0

    def test_rollback_provider_name(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.rollback(config)

        assert result.provider == "railway"

    def test_rollback_error_none_on_success(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0)

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.rollback(config)

        assert result.error is None


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_missing_token_returns_error_dict(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
        config = make_config(tmp_path)

        status = provider.get_status(config)

        assert status["status"] == "error"
        assert "RAILWAY_TOKEN" in status["message"]

    def test_success_status_ok(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0, stdout="Service: active")

        with patch.object(provider, "_run_command", return_value=proc):
            status = provider.get_status(config)

        assert status["status"] == "ok"
        assert status["output"] == "Service: active"

    def test_failure_status_error(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=1, stderr="not linked")

        with patch.object(provider, "_run_command", return_value=proc):
            status = provider.get_status(config)

        assert status["status"] == "error"
        assert status["message"] == "not linked"

    def test_provider_key_present(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0, stdout="ok")

        with patch.object(provider, "_run_command", return_value=proc):
            status = provider.get_status(config)

        assert status["provider"] == "railway"

    def test_exception_returns_error_dict(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)

        with patch.object(provider, "_run_command", side_effect=OSError("socket error")):
            status = provider.get_status(config)

        assert status["status"] == "error"
        assert "socket error" in status["message"]

    def test_get_status_uses_railway_status_command(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0, stdout="ok")

        with patch.object(provider, "_run_command", return_value=proc) as mock_run:
            provider.get_status(config)

        cmd_used = mock_run.call_args[0][0]
        assert cmd_used == ["railway", "status"]

    def test_get_status_timeout_is_30(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0, stdout="ok")

        with patch.object(provider, "_run_command", return_value=proc) as mock_run:
            provider.get_status(config)

        assert mock_run.call_args[1]["timeout"] == 30


# ---------------------------------------------------------------------------
# _extract_url
# ---------------------------------------------------------------------------


class TestExtractUrl:
    def test_extracts_railway_url(self, provider: RailwayProvider) -> None:
        output = "Deployed successfully\nhttps://my-app.railway.app\n"
        url = provider._extract_url(output)
        assert url == "https://my-app.railway.app"

    def test_extracts_url_with_path(self, provider: RailwayProvider) -> None:
        output = "URL: https://cool-app.railway.app/api/v1"
        url = provider._extract_url(output)
        assert url == "https://cool-app.railway.app/api/v1"

    def test_returns_none_when_no_url(self, provider: RailwayProvider) -> None:
        output = "No URL here"
        url = provider._extract_url(output)
        assert url is None

    def test_returns_none_on_empty_output(self, provider: RailwayProvider) -> None:
        url = provider._extract_url("")
        assert url is None

    def test_ignores_non_railway_https_lines(self, provider: RailwayProvider) -> None:
        output = "See https://docs.example.com for info\nhttps://my-app.railway.app done"
        url = provider._extract_url(output)
        # Should find railway.app, not docs.example.com
        assert url == "https://my-app.railway.app"

    def test_requires_railway_in_line(self, provider: RailwayProvider) -> None:
        # Line has https:// but "railway" not present anywhere in line
        output = "https://other-provider.app/service"
        url = provider._extract_url(output)
        assert url is None

    def test_case_insensitive_railway_check(self, provider: RailwayProvider) -> None:
        # "Railway" (capital R) should still match
        output = "Railway deploy: https://my-app.railway.app"
        url = provider._extract_url(output)
        assert url == "https://my-app.railway.app"

    def test_first_matching_url_returned(self, provider: RailwayProvider) -> None:
        output = (
            "https://first-railway.railway.app\n"
            "https://second-railway.railway.app\n"
        )
        url = provider._extract_url(output)
        assert url == "https://first-railway.railway.app"

    def test_url_with_query_string(self, provider: RailwayProvider) -> None:
        output = "railway link: https://my-app.railway.app?foo=bar"
        url = provider._extract_url(output)
        assert url is not None
        assert "railway.app" in url


# ---------------------------------------------------------------------------
# DeploymentResult.summary (sanity checks via provider output)
# ---------------------------------------------------------------------------


class TestDeploymentResultSummary:
    def test_success_summary_contains_success(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=0, stdout="https://app.railway.app done")

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.deploy(config)

        assert "SUCCESS" in result.summary

    def test_failure_summary_contains_failed(
        self,
        provider: RailwayProvider,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RAILWAY_TOKEN", "tok_abc")
        config = make_config(tmp_path)
        proc = make_completed_process(returncode=1, stderr="error")

        with patch.object(provider, "_run_command", return_value=proc):
            result = provider.deploy(config)

        assert "FAILED" in result.summary
