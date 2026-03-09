"""
Extended Tests for FORGE Deployment Provider System
====================================================

Comprehensive tests covering edge cases, boundary conditions, and paths
not covered by existing test files for:
  - forge_harness.deployment.base
  - forge_harness.deployment.cloudflare
  - forge_harness.deployment.railway

Target: 80%+ coverage on each module.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from forge_harness.deployment.base import (
    DeploymentConfig,
    DeploymentProvider,
    DeploymentResult,
    DeploymentTarget,
    ProviderCapabilities,
)
from forge_harness.deployment.cloudflare import CloudflareProvider
from forge_harness.deployment.railway import RailwayProvider

# ---------------------------------------------------------------------------
# Shared Helpers
# ---------------------------------------------------------------------------

CF_ENV = {
    "CLOUDFLARE_API_TOKEN": "cf-token",
    "CLOUDFLARE_ACCOUNT_ID": "cf-account",
}

RAILWAY_ENV = {
    "RAILWAY_TOKEN": "rw-token",
}


def make_config(
    tmp_path: Path,
    *,
    project: str = "test-project",
    domain: str = "test-domain",
    dry_run: bool = False,
    timeout: int = 300,
    provider_options: dict | None = None,
    extra_env: dict | None = None,
    target: DeploymentTarget = DeploymentTarget.BACKEND,
) -> DeploymentConfig:
    return DeploymentConfig(
        domain=domain,
        project=project,
        project_dir=tmp_path,
        target=target,
        dry_run=dry_run,
        timeout=timeout,
        provider_options=provider_options or {},
        extra_env=extra_env or {},
    )


def make_completed_process(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# base.py — DeploymentTarget
# ---------------------------------------------------------------------------


class TestDeploymentTarget:
    def test_all_enum_values(self):
        assert DeploymentTarget.BACKEND.value == "backend"
        assert DeploymentTarget.FRONTEND.value == "frontend"
        assert DeploymentTarget.FULL_STACK.value == "full_stack"

    def test_enum_members_are_distinct(self):
        members = list(DeploymentTarget)
        assert len(members) == 3
        assert len({m.value for m in members}) == 3

    def test_from_value(self):
        assert DeploymentTarget("backend") is DeploymentTarget.BACKEND
        assert DeploymentTarget("frontend") is DeploymentTarget.FRONTEND
        assert DeploymentTarget("full_stack") is DeploymentTarget.FULL_STACK

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            DeploymentTarget("unknown")


# ---------------------------------------------------------------------------
# base.py — ProviderCapabilities
# ---------------------------------------------------------------------------


class TestProviderCapabilities:
    def test_defaults(self):
        caps = ProviderCapabilities(
            name="test",
            targets=[DeploymentTarget.BACKEND],
        )
        assert caps.supports_rollback is False
        assert caps.supports_preview is False
        assert caps.supports_health_check is True
        assert caps.required_env_vars == []
        assert caps.optional_env_vars == []

    def test_custom_values(self):
        caps = ProviderCapabilities(
            name="custom",
            targets=[DeploymentTarget.BACKEND, DeploymentTarget.FRONTEND],
            supports_rollback=True,
            supports_preview=True,
            supports_health_check=False,
            required_env_vars=["TOKEN"],
            optional_env_vars=["EXTRA"],
        )
        assert caps.supports_rollback is True
        assert caps.supports_preview is True
        assert caps.supports_health_check is False
        assert "TOKEN" in caps.required_env_vars
        assert "EXTRA" in caps.optional_env_vars

    def test_targets_list_is_independent(self):
        targets = [DeploymentTarget.BACKEND]
        caps = ProviderCapabilities(name="p", targets=targets)
        targets.append(DeploymentTarget.FRONTEND)
        # caps.targets was assigned directly; check it holds the reference
        assert DeploymentTarget.FRONTEND in caps.targets

    def test_full_stack_target(self):
        caps = ProviderCapabilities(
            name="full",
            targets=[DeploymentTarget.FULL_STACK],
        )
        assert DeploymentTarget.FULL_STACK in caps.targets


# ---------------------------------------------------------------------------
# base.py — DeploymentConfig
# ---------------------------------------------------------------------------


class TestDeploymentConfig:
    def test_backend_dir_property(self, tmp_path):
        config = make_config(tmp_path)
        assert config.backend_dir == tmp_path / "backend"

    def test_frontend_dir_property(self, tmp_path):
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        assert config.frontend_dir == tmp_path / "frontend"

    def test_default_target_is_backend(self, tmp_path):
        config = DeploymentConfig(
            domain="d",
            project="p",
            project_dir=tmp_path,
        )
        assert config.target == DeploymentTarget.BACKEND

    def test_dry_run_default_false(self, tmp_path):
        config = make_config(tmp_path)
        assert config.dry_run is False

    def test_timeout_default(self, tmp_path):
        config = make_config(tmp_path)
        assert config.timeout == 300

    def test_extra_env_default_empty(self, tmp_path):
        config = make_config(tmp_path)
        assert config.extra_env == {}

    def test_provider_options_default_empty(self, tmp_path):
        config = make_config(tmp_path)
        assert config.provider_options == {}

    def test_from_settings(self, tmp_path):
        """Test from_settings class method builds config from ForgeSettings."""
        mock_settings = MagicMock()
        mock_settings.domain = "my-domain"
        mock_settings.project = "my-project"
        mock_settings.forge_root = str(tmp_path)
        mock_settings.dry_run = True

        config = DeploymentConfig.from_settings(mock_settings)

        assert config.domain == "my-domain"
        assert config.project == "my-project"
        assert config.project_dir == tmp_path
        assert config.dry_run is True
        assert config.target == DeploymentTarget.BACKEND

    def test_from_settings_with_frontend_target(self, tmp_path):
        mock_settings = MagicMock()
        mock_settings.domain = "d"
        mock_settings.project = "p"
        mock_settings.forge_root = str(tmp_path)
        mock_settings.dry_run = False

        config = DeploymentConfig.from_settings(mock_settings, target=DeploymentTarget.FRONTEND)
        assert config.target == DeploymentTarget.FRONTEND

    def test_from_settings_none_values_use_defaults(self, tmp_path):
        mock_settings = MagicMock()
        mock_settings.domain = None
        mock_settings.project = None
        mock_settings.forge_root = None
        mock_settings.dry_run = False

        config = DeploymentConfig.from_settings(mock_settings)
        assert config.domain == ""
        assert config.project == ""
        assert config.project_dir == Path(".")

    def test_from_settings_full_stack_target(self, tmp_path):
        mock_settings = MagicMock()
        mock_settings.domain = "d"
        mock_settings.project = "p"
        mock_settings.forge_root = str(tmp_path)
        mock_settings.dry_run = False

        config = DeploymentConfig.from_settings(mock_settings, target=DeploymentTarget.FULL_STACK)
        assert config.target == DeploymentTarget.FULL_STACK


# ---------------------------------------------------------------------------
# base.py — DeploymentResult
# ---------------------------------------------------------------------------


class TestDeploymentResult:
    def test_success_summary_no_url(self):
        result = DeploymentResult(success=True, provider="railway", target="backend")
        assert "[railway]" in result.summary
        assert "SUCCESS" in result.summary
        assert "0.0s" in result.summary

    def test_success_summary_with_url(self):
        result = DeploymentResult(
            success=True,
            provider="railway",
            target="backend",
            url="https://app.railway.app",
        )
        assert "-> https://app.railway.app" in result.summary

    def test_failure_summary(self):
        result = DeploymentResult(
            success=False,
            provider="cloudflare",
            target="frontend",
            error="Build failed",
        )
        assert "FAILED" in result.summary
        assert "cloudflare" in result.summary

    def test_duration_in_summary(self):
        result = DeploymentResult(
            success=True,
            provider="p",
            target="t",
            duration_seconds=12.345,
        )
        assert "12.3s" in result.summary

    def test_metadata_default_empty(self):
        result = DeploymentResult(success=True, provider="p", target="t")
        assert result.metadata == {}

    def test_logs_default_empty(self):
        result = DeploymentResult(success=True, provider="p", target="t")
        assert result.logs == ""

    def test_url_default_none(self):
        result = DeploymentResult(success=True, provider="p", target="t")
        assert result.url is None

    def test_error_default_none(self):
        result = DeploymentResult(success=True, provider="p", target="t")
        assert result.error is None

    def test_metadata_can_store_arbitrary_data(self):
        result = DeploymentResult(
            success=True,
            provider="p",
            target="t",
            metadata={"key": "value", "count": 42, "nested": {"a": 1}},
        )
        assert result.metadata["key"] == "value"
        assert result.metadata["count"] == 42


# ---------------------------------------------------------------------------
# base.py — DeploymentProvider (abstract base via concrete subclass)
# ---------------------------------------------------------------------------


class _MinimalProvider(DeploymentProvider):
    """Minimal concrete provider used only in tests."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="minimal",
            targets=[DeploymentTarget.BACKEND],
            required_env_vars=["MINIMAL_TOKEN"],
            optional_env_vars=["MINIMAL_OPTION"],
        )

    def deploy(self, config: DeploymentConfig) -> DeploymentResult:
        return DeploymentResult(success=True, provider="minimal", target=config.target.value)

    def verify(self, url: str, timeout: int = 30) -> dict:
        return {"healthy": True}


class TestDeploymentProviderBase:
    @pytest.fixture
    def provider(self):
        return _MinimalProvider()

    def test_is_configured_true_when_env_set(self, provider):
        with patch.dict(os.environ, {"MINIMAL_TOKEN": "tok"}):
            assert provider.is_configured() is True

    def test_is_configured_false_when_env_missing(self, provider):
        with patch.dict(os.environ, {}, clear=True):
            assert provider.is_configured() is False

    def test_missing_config_returns_missing_vars(self, provider):
        with patch.dict(os.environ, {}, clear=True):
            missing = provider.missing_config()
            assert "MINIMAL_TOKEN" in missing

    def test_missing_config_empty_when_all_set(self, provider):
        with patch.dict(os.environ, {"MINIMAL_TOKEN": "tok"}):
            assert provider.missing_config() == []

    def test_rollback_default_returns_not_supported(self, provider, tmp_path):
        """Default rollback implementation returns error with 'not supported'."""
        config = make_config(tmp_path)
        result = provider.rollback(config)
        assert result.success is False
        assert "Rollback not supported" in result.error
        assert result.provider == "minimal"

    def test_rollback_default_with_version_still_not_supported(self, provider, tmp_path):
        config = make_config(tmp_path)
        result = provider.rollback(config, version="v1.0")
        assert result.success is False
        assert "Rollback not supported" in result.error

    def test_get_status_default_returns_unknown(self, provider, tmp_path):
        """Default get_status returns unknown status dict."""
        config = make_config(tmp_path)
        status = provider.get_status(config)
        assert status["status"] == "unknown"
        assert status["provider"] == "minimal"
        assert "not implemented" in status["message"].lower()

    def test_run_command_invokes_subprocess(self, provider, tmp_path):
        """_run_command should call subprocess.run with merged env."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = make_completed_process(0, "ok", "")
            result = provider._run_command(
                ["echo", "hello"],
                cwd=tmp_path,
                env={"MY_VAR": "value"},
                timeout=60,
            )
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        # Verify key arguments
        assert call_kwargs.kwargs["capture_output"] is True
        assert call_kwargs.kwargs["text"] is True
        assert call_kwargs.kwargs["timeout"] == 60
        # env should include MY_VAR
        assert "MY_VAR" in call_kwargs.kwargs["env"]

    def test_run_command_merges_os_env(self, provider, tmp_path):
        """_run_command merges os.environ with provided env."""
        with patch.dict(os.environ, {"EXISTING_VAR": "existing"}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = make_completed_process()
                provider._run_command(
                    ["true"],
                    cwd=tmp_path,
                    env={"NEW_VAR": "new"},
                )
            env_passed = mock_run.call_args.kwargs["env"]
            assert "EXISTING_VAR" in env_passed
            assert "NEW_VAR" in env_passed

    def test_run_command_without_extra_env(self, provider, tmp_path):
        """_run_command works with env=None (only os.environ)."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = make_completed_process()
            provider._run_command(["true"], cwd=tmp_path)
        mock_run.assert_called_once()

    def test_run_command_passes_cwd(self, provider, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = make_completed_process()
            provider._run_command(["true"], cwd=tmp_path)
        assert mock_run.call_args.kwargs["cwd"] == tmp_path

    def test_run_command_default_timeout(self, provider, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = make_completed_process()
            provider._run_command(["true"], cwd=tmp_path)
        assert mock_run.call_args.kwargs["timeout"] == 300


# ---------------------------------------------------------------------------
# cloudflare.py — CloudflareProvider
# ---------------------------------------------------------------------------


class TestCloudflareCapabilities:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_name(self, provider):
        assert provider.capabilities.name == "cloudflare"

    def test_targets_frontend_only(self, provider):
        assert provider.capabilities.targets == [DeploymentTarget.FRONTEND]

    def test_no_rollback(self, provider):
        assert provider.capabilities.supports_rollback is False

    def test_supports_preview(self, provider):
        assert provider.capabilities.supports_preview is True

    def test_supports_health_check(self, provider):
        assert provider.capabilities.supports_health_check is True

    def test_required_env_vars(self, provider):
        required = provider.capabilities.required_env_vars
        assert "CLOUDFLARE_API_TOKEN" in required
        assert "CLOUDFLARE_ACCOUNT_ID" in required

    def test_optional_env_vars(self, provider):
        assert "CLOUDFLARE_PROJECT_NAME" in provider.capabilities.optional_env_vars


class TestCloudflareIsConfigured:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_not_configured_no_env(self, provider):
        with patch.dict(os.environ, {}, clear=True):
            assert provider.is_configured() is False

    def test_not_configured_token_only(self, provider):
        with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "tok"}, clear=True):
            assert provider.is_configured() is False

    def test_not_configured_account_only(self, provider):
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "acc"}, clear=True):
            assert provider.is_configured() is False

    def test_configured_with_both(self, provider):
        with patch.dict(os.environ, CF_ENV):
            assert provider.is_configured() is True

    def test_missing_config_both_missing(self, provider):
        with patch.dict(os.environ, {}, clear=True):
            missing = provider.missing_config()
            assert "CLOUDFLARE_API_TOKEN" in missing
            assert "CLOUDFLARE_ACCOUNT_ID" in missing

    def test_missing_config_none_missing(self, provider):
        with patch.dict(os.environ, CF_ENV):
            assert provider.missing_config() == []


class TestCloudflareDeployCredentials:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_missing_both_creds(self, provider, tmp_path):
        (tmp_path / "frontend").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        with patch.dict(os.environ, {}, clear=True):
            result = provider.deploy(config)
        assert result.success is False
        assert result.provider == "cloudflare"
        assert "CLOUDFLARE_API_TOKEN" in result.error
        assert "CLOUDFLARE_ACCOUNT_ID" in result.error

    def test_missing_token_only(self, provider, tmp_path):
        (tmp_path / "frontend").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "acc"}, clear=True):
            result = provider.deploy(config)
        assert result.success is False

    def test_missing_account_only(self, provider, tmp_path):
        (tmp_path / "frontend").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "tok"}, clear=True):
            result = provider.deploy(config)
        assert result.success is False


class TestCloudflareDeployFrontendDirMissing:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_frontend_dir_missing(self, provider, tmp_path):
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        with patch.dict(os.environ, CF_ENV):
            result = provider.deploy(config)
        assert result.success is False
        assert "Frontend directory not found" in result.error
        assert str(tmp_path / "frontend") in result.error


class TestCloudflareDeployDryRun:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_dry_run_success(self, provider, tmp_path):
        (tmp_path / "frontend").mkdir()
        config = make_config(
            tmp_path,
            target=DeploymentTarget.FRONTEND,
            dry_run=True,
        )
        with patch.dict(os.environ, CF_ENV):
            result = provider.deploy(config)
        assert result.success is True

    def test_dry_run_sets_metadata_flag(self, provider, tmp_path):
        (tmp_path / "frontend").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND, dry_run=True)
        with patch.dict(os.environ, CF_ENV):
            result = provider.deploy(config)
        assert result.metadata.get("dry_run") is True

    def test_dry_run_logs_wrangler_command(self, provider, tmp_path):
        (tmp_path / "frontend").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND, dry_run=True)
        with patch.dict(os.environ, CF_ENV):
            result = provider.deploy(config)
        assert "wrangler pages deploy" in result.logs
        assert "[DRY RUN]" in result.logs

    def test_dry_run_url_uses_project_name(self, provider, tmp_path):
        (tmp_path / "frontend").mkdir()
        config = make_config(tmp_path, project="my-app", target=DeploymentTarget.FRONTEND, dry_run=True)
        with patch.dict(os.environ, CF_ENV):
            result = provider.deploy(config)
        assert ".pages.dev" in result.url

    def test_dry_run_metadata_has_project_name(self, provider, tmp_path):
        (tmp_path / "frontend").mkdir()
        config = make_config(tmp_path, project="my-app", target=DeploymentTarget.FRONTEND, dry_run=True)
        with patch.dict(os.environ, CF_ENV):
            result = provider.deploy(config)
        assert "project_name" in result.metadata

    def test_dry_run_does_not_call_run_command(self, provider, tmp_path):
        (tmp_path / "frontend").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND, dry_run=True)
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command") as mock_cmd:
                provider.deploy(config)
        mock_cmd.assert_not_called()


class TestCloudflareDeploySuccess:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_success_with_existing_dist(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        proc = make_completed_process(0, "Deployed to https://myapp.pages.dev", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert result.success is True
        assert result.url == "https://myapp.pages.dev"

    def test_success_includes_logs(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        proc = make_completed_process(0, "Wrangler output here\nhttps://app.pages.dev deployed", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert "Wrangler output here" in result.logs

    def test_success_default_branch_is_main(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.deploy(config)
        cmd_args = mock_cmd.call_args[0][0]
        assert "--branch=main" in cmd_args

    def test_success_custom_branch(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(
            tmp_path,
            target=DeploymentTarget.FRONTEND,
            provider_options={"branch": "staging"},
        )
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.deploy(config)
        cmd_args = mock_cmd.call_args[0][0]
        assert "--branch=staging" in cmd_args

    def test_success_metadata_has_branch(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert result.metadata.get("branch") == "main"

    def test_success_metadata_has_project_name(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND, project="my-cool-app")
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert "project_name" in result.metadata

    def test_success_extra_env_passed_to_command(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(
            tmp_path,
            target=DeploymentTarget.FRONTEND,
            extra_env={"CUSTOM_VAR": "custom_value"},
        )
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.deploy(config)
        env_passed = mock_cmd.call_args.kwargs.get("env") or mock_cmd.call_args[1].get("env") or {}
        # Accept env from positional or keyword args
        call_args = mock_cmd.call_args
        if call_args.kwargs:
            env_passed = call_args.kwargs.get("env", {})
        else:
            env_passed = call_args[1].get("env", {})
        assert "CUSTOM_VAR" in env_passed

    def test_success_duration_positive(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert result.duration_seconds >= 0.0


class TestCloudflareDeployWranglerFailure:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_nonzero_returncode_returns_failure(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        proc = make_completed_process(1, "stdout output", "Error: wrangler failed")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert result.success is False
        assert result.error == "Error: wrangler failed"
        assert "stdout output" in result.logs

    def test_run_command_exception_returns_failure(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", side_effect=RuntimeError("subprocess error")):
                result = provider.deploy(config)
        assert result.success is False
        assert "subprocess error" in result.error


class TestCloudflareDeployBuildFrontend:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_build_triggered_when_dist_missing(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        # No dist/ directory
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)

        build_proc = make_completed_process(0, "Build success", "")
        deploy_proc = make_completed_process(0, "Deployed ok", "")

        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", side_effect=[build_proc, deploy_proc]) as mock_cmd:
                result = provider.deploy(config)

        assert mock_cmd.call_count == 2
        assert result.success is True

    def test_build_failure_returns_error(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        build_proc = make_completed_process(1, "", "npm build error")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=build_proc):
                result = provider.deploy(config)
        assert result.success is False
        assert "Build failed" in result.error

    def test_build_exception_causes_deploy_failure(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", side_effect=OSError("npm not found")):
                result = provider.deploy(config)
        assert result.success is False
        assert "Build failed" in result.error

    def test_build_frontend_success(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        proc = make_completed_process(0, "Build output", "")
        with patch.object(provider, "_run_command", return_value=proc):
            result = provider._build_frontend(frontend, timeout=120)
        assert result["success"] is True
        assert result["logs"] == "Build output"
        assert result["error"] is None

    def test_build_frontend_failure(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        proc = make_completed_process(1, "", "Build error")
        with patch.object(provider, "_run_command", return_value=proc):
            result = provider._build_frontend(frontend, timeout=120)
        assert result["success"] is False
        assert result["error"] == "Build error"

    def test_build_frontend_exception(self, provider, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        with patch.object(provider, "_run_command", side_effect=FileNotFoundError("npm not found")):
            result = provider._build_frontend(frontend, timeout=120)
        assert result["success"] is False
        assert "npm not found" in result["error"]
        assert result["logs"] == ""


class TestCloudflareVerify:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_healthy_with_doctype(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<!DOCTYPE html><html></html>"
            mock_get.return_value = resp
            result = provider.verify("https://app.pages.dev")
        assert result["healthy"] is True
        assert result["has_html"] is True
        assert result["status_code"] == 200
        assert result["error"] is None

    def test_healthy_with_lowercase_html_tag(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><head></head></html>"
            mock_get.return_value = resp
            result = provider.verify("https://app.pages.dev")
        assert result["healthy"] is True
        assert result["has_html"] is True

    def test_unhealthy_when_status_not_200(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 404
            resp.text = "<!DOCTYPE html><html>Not Found</html>"
            mock_get.return_value = resp
            result = provider.verify("https://app.pages.dev")
        assert result["healthy"] is False
        assert result["status_code"] == 404

    def test_unhealthy_when_not_html(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = '{"message": "ok"}'
            mock_get.return_value = resp
            result = provider.verify("https://app.pages.dev")
        assert result["healthy"] is False
        assert result["has_html"] is False

    def test_unhealthy_on_500(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 500
            resp.text = "Internal Server Error"
            mock_get.return_value = resp
            result = provider.verify("https://app.pages.dev")
        assert result["healthy"] is False

    def test_connection_error_returns_unhealthy(self, provider):
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            result = provider.verify("https://app.pages.dev")
        assert result["healthy"] is False
        assert "Connection refused" in result["error"]
        assert result["status_code"] is None

    def test_url_preserved_in_result(self, provider):
        url = "https://my-specific.pages.dev"
        with patch("httpx.get", side_effect=Exception("err")):
            result = provider.verify(url)
        assert result["url"] == url

    def test_passes_custom_timeout(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<!DOCTYPE html>"
            mock_get.return_value = resp
            provider.verify("https://app.pages.dev", timeout=60)
        mock_get.assert_called_once_with(
            "https://app.pages.dev", timeout=60, follow_redirects=True
        )

    def test_follows_redirects(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<!DOCTYPE html>"
            mock_get.return_value = resp
            provider.verify("https://app.pages.dev")
        assert mock_get.call_args.kwargs.get("follow_redirects") is True


class TestCloudflareGetStatus:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_missing_credentials(self, provider, tmp_path):
        config = make_config(tmp_path)
        with patch.dict(os.environ, {}, clear=True):
            status = provider.get_status(config)
        assert status["status"] == "error"
        assert "not configured" in status["message"]

    def test_missing_token_only(self, provider, tmp_path):
        config = make_config(tmp_path)
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "acc"}, clear=True):
            status = provider.get_status(config)
        assert status["status"] == "error"

    def test_status_ok_on_success(self, provider, tmp_path):
        config = make_config(tmp_path)
        proc = make_completed_process(0, "Deployment list output", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                status = provider.get_status(config)
        assert status["status"] == "ok"
        assert status["provider"] == "cloudflare"
        assert "Deployment list output" in status["output"]

    def test_status_error_on_failure(self, provider, tmp_path):
        config = make_config(tmp_path)
        proc = make_completed_process(1, "", "wrangler error")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                status = provider.get_status(config)
        assert status["status"] == "error"
        assert "wrangler error" in status["message"]

    def test_status_exception_returns_error(self, provider, tmp_path):
        config = make_config(tmp_path)
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", side_effect=RuntimeError("timeout")):
                status = provider.get_status(config)
        assert status["status"] == "error"
        assert "timeout" in status["message"]

    def test_uses_project_name(self, provider, tmp_path):
        config = make_config(tmp_path, provider_options={"project_name": "my-pages-project"})
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.get_status(config)
        cmd_args = mock_cmd.call_args[0][0]
        assert "my-pages-project" in cmd_args

    def test_command_is_wrangler_pages_deployment_list(self, provider, tmp_path):
        config = make_config(tmp_path)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.get_status(config)
        cmd_args = mock_cmd.call_args[0][0]
        assert "wrangler" in cmd_args
        assert "pages" in cmd_args
        assert "deployment" in cmd_args
        assert "list" in cmd_args


class TestCloudflareGetProjectName:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_uses_provider_option_first(self, provider, tmp_path):
        config = make_config(tmp_path, provider_options={"project_name": "explicit-project"})
        with patch.dict(os.environ, {"CLOUDFLARE_PROJECT_NAME": "env-project"}):
            name = provider._get_project_name(config)
        assert name == "explicit-project"

    def test_uses_env_var_second(self, provider, tmp_path):
        config = make_config(tmp_path)
        with patch.dict(os.environ, {"CLOUDFLARE_PROJECT_NAME": "env-project"}):
            name = provider._get_project_name(config)
        assert name == "env-project"

    def test_generates_from_project_name(self, provider, tmp_path):
        config = make_config(tmp_path, project="voice-coach")
        with patch.dict(os.environ, {}, clear=True):
            name = provider._get_project_name(config)
        assert name == "voicecoach-app"

    def test_removes_hyphens_from_project(self, provider, tmp_path):
        config = make_config(tmp_path, project="my-cool-app")
        with patch.dict(os.environ, {}, clear=True):
            name = provider._get_project_name(config)
        assert "-" not in name.split("-app")[0]  # hyphens removed before "-app"

    def test_project_name_ends_with_app(self, provider, tmp_path):
        config = make_config(tmp_path, project="test")
        with patch.dict(os.environ, {}, clear=True):
            name = provider._get_project_name(config)
        assert name.endswith("-app")


class TestCloudflareExtractUrl:
    @pytest.fixture
    def provider(self):
        return CloudflareProvider()

    def test_extracts_pages_dev_url(self, provider):
        output = "Deployed to https://myapp.pages.dev successfully"
        url = provider._extract_url(output)
        assert url == "https://myapp.pages.dev"

    def test_extracts_from_multiple_lines(self, provider):
        output = "Building...\nDeployed at https://preview-abc123.myapp.pages.dev\nDone."
        url = provider._extract_url(output)
        assert url == "https://preview-abc123.myapp.pages.dev"

    def test_returns_none_when_no_url(self, provider):
        output = "No URL in this output"
        url = provider._extract_url(output)
        assert url is None

    def test_returns_none_on_empty_output(self, provider):
        assert provider._extract_url("") is None

    def test_only_matches_pages_dev_domain(self, provider):
        output = "Visit https://example.com for docs"
        url = provider._extract_url(output)
        assert url is None

    def test_extracts_first_pages_dev_url(self, provider):
        output = (
            "https://first.pages.dev deployed\n"
            "Also see https://second.pages.dev"
        )
        url = provider._extract_url(output)
        assert url == "https://first.pages.dev"


# ---------------------------------------------------------------------------
# railway.py — RailwayProvider
# ---------------------------------------------------------------------------


class TestRailwayCapabilities:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_name(self, provider):
        assert provider.capabilities.name == "railway"

    def test_targets_backend_only(self, provider):
        assert provider.capabilities.targets == [DeploymentTarget.BACKEND]

    def test_supports_rollback(self, provider):
        assert provider.capabilities.supports_rollback is True

    def test_supports_preview(self, provider):
        assert provider.capabilities.supports_preview is True

    def test_supports_health_check(self, provider):
        assert provider.capabilities.supports_health_check is True

    def test_required_env_vars(self, provider):
        assert "RAILWAY_TOKEN" in provider.capabilities.required_env_vars

    def test_optional_env_vars(self, provider):
        optional = provider.capabilities.optional_env_vars
        assert "RAILWAY_PROJECT_ID" in optional
        assert "RAILWAY_SERVICE_ID" in optional


class TestRailwayIsConfigured:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_not_configured_no_token(self, provider):
        with patch.dict(os.environ, {}, clear=True):
            assert provider.is_configured() is False

    def test_configured_with_token(self, provider):
        with patch.dict(os.environ, RAILWAY_ENV):
            assert provider.is_configured() is True

    def test_missing_config_returns_token(self, provider):
        with patch.dict(os.environ, {}, clear=True):
            assert "RAILWAY_TOKEN" in provider.missing_config()

    def test_missing_config_empty_when_token_set(self, provider):
        with patch.dict(os.environ, RAILWAY_ENV):
            assert provider.missing_config() == []


class TestRailwayDeployMissingToken:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_deploy_fails_without_token(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        with patch.dict(os.environ, {}, clear=True):
            result = provider.deploy(config)
        assert result.success is False
        assert "RAILWAY_TOKEN not configured" in result.error


class TestRailwayDeployMissingBackendDir:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_deploy_fails_without_backend_dir(self, provider, tmp_path):
        config = make_config(tmp_path)
        with patch.dict(os.environ, RAILWAY_ENV):
            result = provider.deploy(config)
        assert result.success is False
        assert "Backend directory not found" in result.error
        assert str(tmp_path / "backend") in result.error


class TestRailwayDeployDryRun:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_dry_run_success(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path, dry_run=True)
        with patch.dict(os.environ, RAILWAY_ENV):
            result = provider.deploy(config)
        assert result.success is True

    def test_dry_run_sets_metadata_flag(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path, dry_run=True)
        with patch.dict(os.environ, RAILWAY_ENV):
            result = provider.deploy(config)
        assert result.metadata.get("dry_run") is True

    def test_dry_run_logs_contain_railway_up(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path, dry_run=True)
        with patch.dict(os.environ, RAILWAY_ENV):
            result = provider.deploy(config)
        assert "railway up" in result.logs

    def test_dry_run_has_url(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path, dry_run=True)
        with patch.dict(os.environ, RAILWAY_ENV):
            result = provider.deploy(config)
        assert result.url is not None
        assert "railway" in result.url.lower()

    def test_dry_run_does_not_call_run_command(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path, dry_run=True)
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command") as mock_cmd:
                provider.deploy(config)
        mock_cmd.assert_not_called()


class TestRailwayDeploySuccess:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_success_extracts_url(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "Deployed to https://myapp.railway.app\n", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert result.success is True
        assert result.url == "https://myapp.railway.app"

    def test_success_no_url_in_output(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "Deployed successfully", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert result.success is True
        assert result.url is None

    def test_success_includes_logs(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "Deploy log output", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert "Deploy log output" in result.logs

    def test_success_with_service_id_from_config(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path, provider_options={"service_id": "svc-123"})
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.deploy(config)
        cmd_args = mock_cmd.call_args[0][0]
        assert "--service" in cmd_args
        assert "svc-123" in cmd_args

    def test_success_with_service_id_from_env(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "ok", "")
        env = {**RAILWAY_ENV, "RAILWAY_SERVICE_ID": "env-svc-456"}
        with patch.dict(os.environ, env):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.deploy(config)
        cmd_args = mock_cmd.call_args[0][0]
        assert "--service" in cmd_args
        assert "env-svc-456" in cmd_args

    def test_deploy_command_includes_detach_flag(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.deploy(config)
        cmd_args = mock_cmd.call_args[0][0]
        assert "--detach" in cmd_args

    def test_success_duration_positive(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert result.duration_seconds >= 0.0


class TestRailwayDeployFailure:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_nonzero_returncode_returns_failure(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(1, "stdout", "Deploy error")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.deploy(config)
        assert result.success is False
        assert result.error == "Deploy error"
        assert "stdout" in result.logs

    def test_exception_returns_failure(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", side_effect=subprocess.TimeoutExpired("cmd", 300)):
                result = provider.deploy(config)
        assert result.success is False
        assert result.error is not None

    def test_runtime_error_captured(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", side_effect=RuntimeError("network unreachable")):
                result = provider.deploy(config)
        assert result.success is False
        assert "network unreachable" in result.error


class TestRailwayVerify:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_healthy_on_200(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            mock_get.return_value = resp
            result = provider.verify("https://app.railway.app")
        assert result["healthy"] is True
        assert result["status_code"] == 200
        assert result["error"] is None

    def test_unhealthy_on_500(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 500
            mock_get.return_value = resp
            result = provider.verify("https://app.railway.app")
        assert result["healthy"] is False

    def test_unhealthy_on_404(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 404
            mock_get.return_value = resp
            result = provider.verify("https://app.railway.app")
        assert result["healthy"] is False

    def test_connection_error_returns_unhealthy(self, provider):
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            result = provider.verify("https://app.railway.app")
        assert result["healthy"] is False
        assert "Connection refused" in result["error"]
        assert result["status_code"] is None

    def test_health_check_uses_health_endpoint(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            mock_get.return_value = resp
            provider.verify("https://app.railway.app")
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://app.railway.app/health"

    def test_health_check_strips_trailing_slash(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            mock_get.return_value = resp
            provider.verify("https://app.railway.app/")
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://app.railway.app/health"

    def test_url_preserved_in_result(self, provider):
        url = "https://specific.railway.app"
        with patch("httpx.get", side_effect=Exception("err")):
            result = provider.verify(url)
        assert result["url"] == url

    def test_passes_custom_timeout(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            mock_get.return_value = resp
            provider.verify("https://app.railway.app", timeout=60)
        assert mock_get.call_args.kwargs.get("timeout") == 60

    def test_follows_redirects(self, provider):
        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            mock_get.return_value = resp
            provider.verify("https://app.railway.app")
        assert mock_get.call_args.kwargs.get("follow_redirects") is True


class TestRailwayRollback:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_rollback_missing_token(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        with patch.dict(os.environ, {}, clear=True):
            result = provider.rollback(config)
        assert result.success is False
        assert "RAILWAY_TOKEN not configured" in result.error

    def test_rollback_success_no_version(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "Rolled back", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.rollback(config)
        assert result.success is True
        assert "Rolled back" in result.logs

    def test_rollback_success_with_version(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "Rolled back to v1.0", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                result = provider.rollback(config, version="deploy-abc123")
        cmd_args = mock_cmd.call_args[0][0]
        assert "deploy-abc123" in cmd_args
        assert result.success is True

    def test_rollback_failure_nonzero_returncode(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(1, "", "rollback error")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.rollback(config)
        assert result.success is False
        assert result.error == "rollback error"

    def test_rollback_exception_returns_failure(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", side_effect=RuntimeError("connection error")):
                result = provider.rollback(config)
        assert result.success is False
        assert "connection error" in result.error

    def test_rollback_no_version_command_excludes_version(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.rollback(config)
        cmd_args = mock_cmd.call_args[0][0]
        assert cmd_args == ["railway", "rollback"]

    def test_rollback_provider_field_is_railway(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.rollback(config)
        assert result.provider == "railway"

    def test_rollback_duration_captured(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                result = provider.rollback(config)
        assert result.duration_seconds >= 0.0


class TestRailwayGetStatus:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_missing_token(self, provider, tmp_path):
        config = make_config(tmp_path)
        with patch.dict(os.environ, {}, clear=True):
            status = provider.get_status(config)
        assert status["status"] == "error"
        assert "RAILWAY_TOKEN not configured" in status["message"]

    def test_status_ok_on_success(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "Status output", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                status = provider.get_status(config)
        assert status["status"] == "ok"
        assert status["provider"] == "railway"
        assert "Status output" in status["output"]

    def test_status_error_on_failure(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(1, "", "railway status error")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc):
                status = provider.get_status(config)
        assert status["status"] == "error"
        assert "railway status error" in status["message"]

    def test_status_exception_returns_error(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", side_effect=RuntimeError("timeout")):
                status = provider.get_status(config)
        assert status["status"] == "error"
        assert "timeout" in status["message"]

    def test_command_is_railway_status(self, provider, tmp_path):
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        proc = make_completed_process(0, "ok", "")
        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=proc) as mock_cmd:
                provider.get_status(config)
        cmd_args = mock_cmd.call_args[0][0]
        assert cmd_args == ["railway", "status"]


class TestRailwayExtractUrl:
    @pytest.fixture
    def provider(self):
        return RailwayProvider()

    def test_extracts_railway_url(self, provider):
        output = "Deployed to https://myapp.railway.app"
        url = provider._extract_url(output)
        assert url == "https://myapp.railway.app"

    def test_extracts_from_multiple_lines(self, provider):
        output = (
            "Building...\n"
            "Deploying service...\n"
            "URL: https://backend-prod.railway.app\n"
        )
        url = provider._extract_url(output)
        assert url == "https://backend-prod.railway.app"

    def test_returns_none_when_no_url(self, provider):
        output = "No URL in this output"
        url = provider._extract_url(output)
        assert url is None

    def test_returns_none_for_empty_output(self, provider):
        assert provider._extract_url("") is None

    def test_only_matches_railway_domain(self, provider):
        output = "Visit https://example.com for docs"
        url = provider._extract_url(output)
        assert url is None

    def test_extracts_first_railway_url(self, provider):
        output = (
            "Primary: https://first.railway.app\n"
            "Mirror: https://second.railway.app\n"
        )
        url = provider._extract_url(output)
        assert url == "https://first.railway.app"

    def test_case_insensitive_railway_match(self, provider):
        # The regex uses "railway" in line.lower(), so test with caps
        output = "Deployed at https://app.RAILWAY.APP"
        url = provider._extract_url(output)
        assert url == "https://app.RAILWAY.APP"


# ---------------------------------------------------------------------------
# Integration — Cloudflare rollback uses base default (not supported)
# ---------------------------------------------------------------------------


class TestCloudflareRollbackUsesBaseDefault:
    def test_cloudflare_rollback_not_supported(self, tmp_path):
        provider = CloudflareProvider()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        result = provider.rollback(config)
        assert result.success is False
        assert "not supported" in result.error.lower()
        assert result.provider == "cloudflare"

    def test_cloudflare_rollback_with_version_not_supported(self, tmp_path):
        provider = CloudflareProvider()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        result = provider.rollback(config, version="v1.0")
        assert result.success is False
        assert "not supported" in result.error.lower()


# ---------------------------------------------------------------------------
# Integration — base get_status default via _MinimalProvider
# ---------------------------------------------------------------------------


class TestBaseGetStatusDefault:
    def test_get_status_default_returns_unknown(self, tmp_path):
        provider = _MinimalProvider()
        config = make_config(tmp_path)
        status = provider.get_status(config)
        assert status["status"] == "unknown"

    def test_get_status_has_provider_name(self, tmp_path):
        provider = _MinimalProvider()
        config = make_config(tmp_path)
        status = provider.get_status(config)
        assert status["provider"] == "minimal"

    def test_get_status_has_message(self, tmp_path):
        provider = _MinimalProvider()
        config = make_config(tmp_path)
        status = provider.get_status(config)
        assert "message" in status


# ---------------------------------------------------------------------------
# Integration — end-to-end deploy + verify sequence
# ---------------------------------------------------------------------------


class TestDeployAndVerifySequence:
    def test_railway_deploy_then_verify(self, tmp_path):
        provider = RailwayProvider()
        (tmp_path / "backend").mkdir()
        config = make_config(tmp_path)
        deploy_proc = make_completed_process(0, "https://app.railway.app deployed", "")

        with patch.dict(os.environ, RAILWAY_ENV):
            with patch.object(provider, "_run_command", return_value=deploy_proc):
                deploy_result = provider.deploy(config)

        assert deploy_result.success is True
        assert deploy_result.url is not None

        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            mock_get.return_value = resp
            verify_result = provider.verify(deploy_result.url)

        assert verify_result["healthy"] is True

    def test_cloudflare_deploy_then_verify(self, tmp_path):
        provider = CloudflareProvider()
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "dist").mkdir()
        config = make_config(tmp_path, target=DeploymentTarget.FRONTEND)
        deploy_proc = make_completed_process(0, "https://myapp.pages.dev deployed", "")

        with patch.dict(os.environ, CF_ENV):
            with patch.object(provider, "_run_command", return_value=deploy_proc):
                deploy_result = provider.deploy(config)

        assert deploy_result.success is True

        with patch("httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<!DOCTYPE html><html></html>"
            mock_get.return_value = resp
            verify_result = provider.verify(deploy_result.url)

        assert verify_result["healthy"] is True
