"""
Tests for FORGE Cloudflare Pages Deployment Provider
=====================================================

Comprehensive tests for CloudflareProvider covering:
- capabilities property
- deploy() - missing credentials, missing frontend dir, dry run,
  successful deploy, build failures, wrangler failures, exceptions
- verify() - healthy HTML, lowercase html tag, non-HTML, non-200, exceptions
- get_status() - missing credentials, success, failure, exception
- _get_project_name() - provider_options, env var, generated fallback
- _build_frontend() - success, failure (non-zero returncode), exception
- _extract_url() - found URL, no URL, multiple lines
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.deployment.base import (
    DeploymentConfig,
    DeploymentTarget,
)
from forge_harness.deployment.cloudflare import CloudflareProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CF_ENV = {
    "CLOUDFLARE_API_TOKEN": "test-token",
    "CLOUDFLARE_ACCOUNT_ID": "test-account",
}


def _make_config(
    tmp_path: Path,
    *,
    dry_run: bool = False,
    provider_options: dict | None = None,
    extra_env: dict | None = None,
    project: str = "my-project",
    timeout: int = 300,
) -> DeploymentConfig:
    return DeploymentConfig(
        domain="test-domain",
        project=project,
        project_dir=tmp_path,
        target=DeploymentTarget.FRONTEND,
        dry_run=dry_run,
        timeout=timeout,
        provider_options=provider_options or {},
        extra_env=extra_env or {},
    )


def _make_completed_process(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


@pytest.fixture
def provider() -> CloudflareProvider:
    return CloudflareProvider()


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_name_is_cloudflare(self, provider: CloudflareProvider) -> None:
        assert provider.capabilities.name == "cloudflare"

    def test_targets_frontend(self, provider: CloudflareProvider) -> None:
        assert DeploymentTarget.FRONTEND in provider.capabilities.targets

    def test_no_rollback_support(self, provider: CloudflareProvider) -> None:
        assert provider.capabilities.supports_rollback is False

    def test_supports_preview(self, provider: CloudflareProvider) -> None:
        assert provider.capabilities.supports_preview is True

    def test_supports_health_check(self, provider: CloudflareProvider) -> None:
        assert provider.capabilities.supports_health_check is True

    def test_required_env_vars(self, provider: CloudflareProvider) -> None:
        caps = provider.capabilities
        assert "CLOUDFLARE_API_TOKEN" in caps.required_env_vars
        assert "CLOUDFLARE_ACCOUNT_ID" in caps.required_env_vars

    def test_optional_env_vars(self, provider: CloudflareProvider) -> None:
        assert "CLOUDFLARE_PROJECT_NAME" in provider.capabilities.optional_env_vars


# ---------------------------------------------------------------------------
# is_configured / missing_config
# ---------------------------------------------------------------------------


class TestIsConfigured:
    def test_not_configured_with_no_env(self, provider: CloudflareProvider) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert provider.is_configured() is False

    def test_not_configured_with_only_token(self, provider: CloudflareProvider) -> None:
        with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "tok"}, clear=True):
            assert provider.is_configured() is False

    def test_not_configured_with_only_account(self, provider: CloudflareProvider) -> None:
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "acct"}, clear=True):
            assert provider.is_configured() is False

    def test_configured_with_both_vars(self, provider: CloudflareProvider) -> None:
        with patch.dict(os.environ, CF_ENV, clear=True):
            assert provider.is_configured() is True

    def test_missing_config_both_missing(self, provider: CloudflareProvider) -> None:
        with patch.dict(os.environ, {}, clear=True):
            missing = provider.missing_config()
            assert "CLOUDFLARE_API_TOKEN" in missing
            assert "CLOUDFLARE_ACCOUNT_ID" in missing

    def test_missing_config_none_missing(self, provider: CloudflareProvider) -> None:
        with patch.dict(os.environ, CF_ENV, clear=True):
            assert provider.missing_config() == []


# ---------------------------------------------------------------------------
# deploy() — credential failures
# ---------------------------------------------------------------------------


class TestDeployCredentialFailures:
    def test_missing_both_credentials(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with patch.dict(os.environ, {}, clear=True):
            result = provider.deploy(config)
        assert result.success is False
        assert result.provider == "cloudflare"
        assert result.target == DeploymentTarget.FRONTEND.value
        assert "Cloudflare credentials not configured" in result.error
        assert "CLOUDFLARE_API_TOKEN" in result.error
        assert "CLOUDFLARE_ACCOUNT_ID" in result.error

    def test_missing_api_token_only(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "acct"}, clear=True):
            result = provider.deploy(config)
        assert result.success is False
        assert "Cloudflare credentials not configured" in result.error

    def test_missing_account_id_only(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "tok"}, clear=True):
            result = provider.deploy(config)
        assert result.success is False
        assert "Cloudflare credentials not configured" in result.error


# ---------------------------------------------------------------------------
# deploy() — missing frontend directory
# ---------------------------------------------------------------------------


class TestDeployMissingFrontendDir:
    def test_frontend_dir_does_not_exist(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        # tmp_path/frontend does NOT exist
        with patch.dict(os.environ, CF_ENV, clear=True):
            result = provider.deploy(config)
        assert result.success is False
        assert "Frontend directory not found" in result.error
        assert str(tmp_path / "frontend") in result.error


# ---------------------------------------------------------------------------
# deploy() — dry run
# ---------------------------------------------------------------------------


class TestDeployDryRun:
    def test_dry_run_returns_success(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        config = _make_config(tmp_path, dry_run=True)
        with patch.dict(os.environ, CF_ENV, clear=True):
            result = provider.deploy(config)
        assert result.success is True
        assert result.provider == "cloudflare"
        assert result.target == DeploymentTarget.FRONTEND.value

    def test_dry_run_metadata_flag(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        config = _make_config(tmp_path, dry_run=True)
        with patch.dict(os.environ, CF_ENV, clear=True):
            result = provider.deploy(config)
        assert result.metadata.get("dry_run") is True

    def test_dry_run_logs_contain_wrangler_command(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        config = _make_config(tmp_path, dry_run=True)
        with patch.dict(os.environ, CF_ENV, clear=True):
            result = provider.deploy(config)
        assert "wrangler pages deploy" in result.logs
        assert "[DRY RUN]" in result.logs

    def test_dry_run_url_uses_project_name(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        config = _make_config(tmp_path, dry_run=True, project="cool-app")
        with patch.dict(os.environ, CF_ENV, clear=True):
            result = provider.deploy(config)
        assert result.url is not None
        assert ".pages.dev" in result.url

    def test_dry_run_metadata_contains_project_name(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        config = _make_config(
            tmp_path,
            dry_run=True,
            provider_options={"project_name": "explicit-name"},
        )
        with patch.dict(os.environ, CF_ENV, clear=True):
            result = provider.deploy(config)
        assert result.metadata.get("project_name") == "explicit-name"

    def test_dry_run_does_not_call_run_command(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        config = _make_config(tmp_path, dry_run=True)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command") as mock_cmd:
                provider.deploy(config)
        mock_cmd.assert_not_called()


# ---------------------------------------------------------------------------
# deploy() — successful wrangler run (dist/ already exists)
# ---------------------------------------------------------------------------


class TestDeploySuccess:
    def test_success_with_existing_dist(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()  # dist already exists — skip build

        stdout = "Uploading... Done!\nhttps://myproject.pages.dev\n"
        mock_result = _make_completed_process(0, stdout=stdout)

        config = _make_config(tmp_path, provider_options={"project_name": "myproject"})
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result):
                result = provider.deploy(config)

        assert result.success is True
        assert result.provider == "cloudflare"
        assert result.url == "https://myproject.pages.dev"

    def test_success_passes_project_name_to_wrangler(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        mock_result = _make_completed_process(0, stdout="https://proj.pages.dev\n")
        config = _make_config(tmp_path, provider_options={"project_name": "proj"})
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result) as mock_cmd:
                provider.deploy(config)
        called_cmd = mock_cmd.call_args[0][0]
        assert any("--project-name=proj" in arg for arg in called_cmd)

    def test_success_passes_default_branch_main(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        mock_result = _make_completed_process(0, stdout="https://proj.pages.dev\n")
        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result) as mock_cmd:
                provider.deploy(config)
        called_cmd = mock_cmd.call_args[0][0]
        assert any("--branch=main" in arg for arg in called_cmd)

    def test_success_respects_custom_branch(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        mock_result = _make_completed_process(0, stdout="https://proj.pages.dev\n")
        config = _make_config(tmp_path, provider_options={"branch": "staging"})
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result) as mock_cmd:
                provider.deploy(config)
        called_cmd = mock_cmd.call_args[0][0]
        assert any("--branch=staging" in arg for arg in called_cmd)

    def test_success_logs_wrangler_output(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        stdout = "Wrangler deploying...\nhttps://proj.pages.dev\n"
        mock_result = _make_completed_process(0, stdout=stdout)
        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result):
                result = provider.deploy(config)
        assert result.logs == stdout

    def test_success_metadata_contains_branch(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        mock_result = _make_completed_process(0, stdout="https://proj.pages.dev\n")
        config = _make_config(tmp_path, provider_options={"branch": "prod"})
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result):
                result = provider.deploy(config)
        assert result.metadata.get("branch") == "prod"

    def test_success_passes_extra_env_to_command(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        mock_result = _make_completed_process(0, stdout="https://proj.pages.dev\n")
        config = _make_config(tmp_path, extra_env={"CUSTOM_VAR": "custom-value"})
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result) as mock_cmd:
                provider.deploy(config)
        called_env = mock_cmd.call_args[1].get("env") or mock_cmd.call_args[0][2] if len(mock_cmd.call_args[0]) > 2 else mock_cmd.call_args[1].get("env", {})
        # env is passed as keyword arg
        kwargs = mock_cmd.call_args.kwargs if hasattr(mock_cmd.call_args, "kwargs") else mock_cmd.call_args[1]
        assert "CUSTOM_VAR" in kwargs.get("env", {})

    def test_success_duration_is_positive(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        mock_result = _make_completed_process(0, stdout="https://proj.pages.dev\n")
        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result):
                result = provider.deploy(config)
        assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# deploy() — wrangler failure (non-zero returncode)
# ---------------------------------------------------------------------------


class TestDeployWranglerFailure:
    def test_nonzero_returncode_returns_failure(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        mock_result = _make_completed_process(1, stdout="", stderr="Authentication failed")
        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result):
                result = provider.deploy(config)

        assert result.success is False
        assert "Authentication failed" in result.error

    def test_nonzero_returncode_stdout_in_logs(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        mock_result = _make_completed_process(1, stdout="partial output", stderr="Error!")
        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result):
                result = provider.deploy(config)
        assert result.logs == "partial output"


# ---------------------------------------------------------------------------
# deploy() — _run_command raises exception
# ---------------------------------------------------------------------------


class TestDeployRunCommandException:
    def test_exception_returns_failure(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", side_effect=subprocess.TimeoutExpired(["wrangler"], 300)):
                result = provider.deploy(config)

        assert result.success is False
        assert result.error is not None

    def test_generic_exception_captured(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "dist").mkdir()

        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", side_effect=RuntimeError("wrangler not found")):
                result = provider.deploy(config)

        assert result.success is False
        assert "wrangler not found" in result.error


# ---------------------------------------------------------------------------
# deploy() — build frontend (dist/ does NOT exist)
# ---------------------------------------------------------------------------


class TestDeployBuildFrontend:
    def test_builds_when_dist_missing_and_succeeds(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        # No dist/ — triggers build

        build_result = _make_completed_process(0, stdout="Build complete")
        deploy_result = _make_completed_process(0, stdout="https://proj.pages.dev\n")

        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", side_effect=[build_result, deploy_result]):
                result = provider.deploy(config)

        assert result.success is True

    def test_build_failure_returns_error(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        # No dist/ — triggers build

        build_result = _make_completed_process(1, stdout="", stderr="npm ERR! missing script: build")

        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=build_result):
                result = provider.deploy(config)

        assert result.success is False
        assert "Build failed" in result.error

    def test_build_exception_propagates_to_deploy_failure(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()

        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", side_effect=FileNotFoundError("npm not found")):
                result = provider.deploy(config)

        assert result.success is False
        assert "Build failed" in result.error


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------


class TestVerify:
    def test_healthy_with_doctype_html(self, provider: CloudflareProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<!DOCTYPE html><html><body>Hello</body></html>"
        with patch("httpx.get", return_value=mock_resp):
            result = provider.verify("https://proj.pages.dev")
        assert result["healthy"] is True
        assert result["status_code"] == 200
        assert result["has_html"] is True
        assert result["url"] == "https://proj.pages.dev"
        assert result["error"] is None

    def test_healthy_with_lowercase_html_tag(self, provider: CloudflareProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html lang='en'><head></head><body>App</body></html>"
        with patch("httpx.get", return_value=mock_resp):
            result = provider.verify("https://proj.pages.dev")
        assert result["healthy"] is True
        assert result["has_html"] is True

    def test_not_healthy_when_status_not_200(self, provider: CloudflareProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "<!DOCTYPE html><html><body>Not Found</body></html>"
        with patch("httpx.get", return_value=mock_resp):
            result = provider.verify("https://proj.pages.dev")
        assert result["healthy"] is False
        assert result["status_code"] == 404

    def test_not_healthy_when_not_html(self, provider: CloudflareProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"status": "ok"}'
        with patch("httpx.get", return_value=mock_resp):
            result = provider.verify("https://proj.pages.dev")
        assert result["healthy"] is False
        assert result["has_html"] is False

    def test_not_healthy_on_500(self, provider: CloudflareProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        with patch("httpx.get", return_value=mock_resp):
            result = provider.verify("https://proj.pages.dev")
        assert result["healthy"] is False

    def test_connection_error_returns_unhealthy(self, provider: CloudflareProvider) -> None:
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            result = provider.verify("https://proj.pages.dev")
        assert result["healthy"] is False
        assert "Connection refused" in result["error"]
        assert result["status_code"] is None

    def test_timeout_error_returns_unhealthy(self, provider: CloudflareProvider) -> None:
        import httpx

        with patch("httpx.get", side_effect=httpx.TimeoutException("Timed out")):
            result = provider.verify("https://proj.pages.dev")
        assert result["healthy"] is False
        assert result["error"] is not None

    def test_passes_timeout_to_httpx(self, provider: CloudflareProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<!DOCTYPE html>"
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            provider.verify("https://proj.pages.dev", timeout=15)
        mock_get.assert_called_once_with(
            "https://proj.pages.dev",
            timeout=15,
            follow_redirects=True,
        )

    def test_follows_redirects(self, provider: CloudflareProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<!DOCTYPE html>"
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            provider.verify("https://proj.pages.dev")
        _, kwargs = mock_get.call_args
        assert kwargs.get("follow_redirects") is True

    def test_url_preserved_in_result(self, provider: CloudflareProvider) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<!DOCTYPE html>"
        with patch("httpx.get", return_value=mock_resp):
            result = provider.verify("https://custom.pages.dev/path")
        assert result["url"] == "https://custom.pages.dev/path"


# ---------------------------------------------------------------------------
# get_status()
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_missing_credentials_returns_error(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with patch.dict(os.environ, {}, clear=True):
            result = provider.get_status(config)
        assert result["status"] == "error"
        assert result["provider"] == "cloudflare"
        assert "credentials not configured" in result["message"]

    def test_missing_token_returns_error(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "acct"}, clear=True):
            result = provider.get_status(config)
        assert result["status"] == "error"

    def test_status_ok_on_zero_returncode(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        mock_result = _make_completed_process(0, stdout="Deployment list output\nv1.0")
        config = _make_config(tmp_path, provider_options={"project_name": "myproj"})
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result):
                result = provider.get_status(config)
        assert result["status"] == "ok"
        assert result["provider"] == "cloudflare"
        assert result["project_name"] == "myproj"
        assert "Deployment list output" in result["output"]

    def test_status_error_on_nonzero_returncode(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        mock_result = _make_completed_process(1, stdout="", stderr="Project not found")
        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result):
                result = provider.get_status(config)
        assert result["status"] == "error"
        assert "Project not found" in result["message"]

    def test_status_exception_returns_error(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", side_effect=RuntimeError("wrangler missing")):
                result = provider.get_status(config)
        assert result["status"] == "error"
        assert "wrangler missing" in result["message"]

    def test_status_command_uses_project_name(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        mock_result = _make_completed_process(0, stdout="ok")
        config = _make_config(tmp_path, provider_options={"project_name": "status-proj"})
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result) as mock_cmd:
                provider.get_status(config)
        called_cmd = mock_cmd.call_args[0][0]
        assert "status-proj" in called_cmd

    def test_status_command_is_wrangler_pages_deployment_list(
        self, provider: CloudflareProvider, tmp_path: Path
    ) -> None:
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        mock_result = _make_completed_process(0, stdout="ok")
        config = _make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV, clear=True):
            with patch.object(provider, "_run_command", return_value=mock_result) as mock_cmd:
                provider.get_status(config)
        called_cmd = mock_cmd.call_args[0][0]
        assert "wrangler" in called_cmd
        assert "pages" in called_cmd
        assert "deployment" in called_cmd
        assert "list" in called_cmd


# ---------------------------------------------------------------------------
# _get_project_name()
# ---------------------------------------------------------------------------


class TestGetProjectName:
    def test_uses_provider_option_first(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(
            tmp_path,
            provider_options={"project_name": "explicit-name"},
            project="ignored-project",
        )
        with patch.dict(os.environ, {"CLOUDFLARE_PROJECT_NAME": "env-name"}, clear=True):
            name = provider._get_project_name(config)
        assert name == "explicit-name"

    def test_falls_back_to_env_var(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(tmp_path, project="my-project")
        with patch.dict(os.environ, {"CLOUDFLARE_PROJECT_NAME": "env-project-name"}, clear=True):
            name = provider._get_project_name(config)
        assert name == "env-project-name"

    def test_generates_from_project_name_when_no_option_or_env(
        self, provider: CloudflareProvider, tmp_path: Path
    ) -> None:
        config = _make_config(tmp_path, project="my-cool-app")
        with patch.dict(os.environ, {}, clear=True):
            name = provider._get_project_name(config)
        assert name == "mycoolapp-app"

    def test_generated_name_strips_hyphens(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(tmp_path, project="a-b-c")
        with patch.dict(os.environ, {}, clear=True):
            name = provider._get_project_name(config)
        assert "-" not in name.replace("-app", "")

    def test_generated_name_has_app_suffix(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(tmp_path, project="myproject")
        with patch.dict(os.environ, {}, clear=True):
            name = provider._get_project_name(config)
        assert name.endswith("-app")

    def test_provider_option_takes_priority_over_env(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        config = _make_config(
            tmp_path,
            provider_options={"project_name": "from-options"},
        )
        with patch.dict(os.environ, {"CLOUDFLARE_PROJECT_NAME": "from-env"}, clear=True):
            name = provider._get_project_name(config)
        assert name == "from-options"


# ---------------------------------------------------------------------------
# _build_frontend()
# ---------------------------------------------------------------------------


class TestBuildFrontend:
    def test_success_returns_true(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        mock_result = _make_completed_process(0, stdout="Build complete", stderr="")
        with patch.object(provider, "_run_command", return_value=mock_result):
            result = provider._build_frontend(tmp_path, timeout=120)
        assert result["success"] is True
        assert result["error"] is None
        assert result["logs"] == "Build complete"

    def test_failure_returns_false(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        mock_result = _make_completed_process(1, stdout="", stderr="Build error")
        with patch.object(provider, "_run_command", return_value=mock_result):
            result = provider._build_frontend(tmp_path, timeout=120)
        assert result["success"] is False
        assert result["error"] == "Build error"

    def test_exception_returns_failure(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        with patch.object(provider, "_run_command", side_effect=FileNotFoundError("npm not found")):
            result = provider._build_frontend(tmp_path, timeout=120)
        assert result["success"] is False
        assert "npm not found" in result["error"]
        assert result["logs"] == ""

    def test_uses_npm_run_build_command(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        mock_result = _make_completed_process(0, stdout="ok")
        with patch.object(provider, "_run_command", return_value=mock_result) as mock_cmd:
            provider._build_frontend(tmp_path, timeout=60)
        called_cmd = mock_cmd.call_args[0][0]
        assert called_cmd == ["npm", "run", "build"]

    def test_uses_correct_cwd(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        mock_result = _make_completed_process(0, stdout="ok")
        with patch.object(provider, "_run_command", return_value=mock_result) as mock_cmd:
            provider._build_frontend(tmp_path, timeout=60)
        kwargs = mock_cmd.call_args.kwargs if hasattr(mock_cmd.call_args, "kwargs") else mock_cmd.call_args[1]
        assert kwargs.get("cwd") == tmp_path

    def test_passes_timeout(self, provider: CloudflareProvider, tmp_path: Path) -> None:
        mock_result = _make_completed_process(0, stdout="ok")
        with patch.object(provider, "_run_command", return_value=mock_result) as mock_cmd:
            provider._build_frontend(tmp_path, timeout=999)
        kwargs = mock_cmd.call_args.kwargs if hasattr(mock_cmd.call_args, "kwargs") else mock_cmd.call_args[1]
        assert kwargs.get("timeout") == 999


# ---------------------------------------------------------------------------
# _extract_url()
# ---------------------------------------------------------------------------


class TestExtractUrl:
    def test_extracts_pages_dev_url(self, provider: CloudflareProvider) -> None:
        output = "Deploying...\nhttps://myapp.pages.dev\nDone!"
        url = provider._extract_url(output)
        assert url == "https://myapp.pages.dev"

    def test_extracts_url_with_path(self, provider: CloudflareProvider) -> None:
        output = "Published at: https://myapp.pages.dev/some/path"
        url = provider._extract_url(output)
        assert url == "https://myapp.pages.dev/some/path"

    def test_returns_none_when_no_url(self, provider: CloudflareProvider) -> None:
        output = "Deploying... failed.\nError: something went wrong"
        url = provider._extract_url(output)
        assert url is None

    def test_returns_none_for_empty_output(self, provider: CloudflareProvider) -> None:
        assert provider._extract_url("") is None

    def test_returns_none_for_non_pages_dev_url(self, provider: CloudflareProvider) -> None:
        output = "Pushed to https://github.com/user/repo"
        url = provider._extract_url(output)
        assert url is None

    def test_extracts_first_matching_url_from_multiple_lines(self, provider: CloudflareProvider) -> None:
        output = (
            "Check https://preview.myapp.pages.dev for preview\n"
            "Published https://myapp.pages.dev\n"
        )
        url = provider._extract_url(output)
        # Should return first match
        assert url is not None
        assert ".pages.dev" in url

    def test_ignores_lines_without_https(self, provider: CloudflareProvider) -> None:
        output = "http://myapp.pages.dev\nPlain text only"
        url = provider._extract_url(output)
        assert url is None

    def test_handles_trailing_whitespace_in_url_line(self, provider: CloudflareProvider) -> None:
        output = "Deployed to https://myapp.pages.dev   "
        url = provider._extract_url(output)
        # regex stops at whitespace boundary
        assert url == "https://myapp.pages.dev"
