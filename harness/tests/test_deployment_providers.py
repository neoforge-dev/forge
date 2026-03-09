"""
Tests for Deployment Provider System
=====================================

Tests for the pluggable deployment provider infrastructure.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestDeploymentProviderBase:
    """Tests for DeploymentProvider base class and types."""

    def test_deployment_result_dataclass(self):
        """Test DeploymentResult can be created."""
        from forge_harness.deployment.base import DeploymentResult

        result = DeploymentResult(
            success=True,
            provider="test",
            target="backend",
            url="https://example.com",
            duration_seconds=5.0,
            logs="Deployed successfully",
        )

        assert result.success is True
        assert result.provider == "test"
        assert result.url == "https://example.com"
        assert "SUCCESS" in result.summary
        assert "5.0s" in result.summary

    def test_deployment_result_failure_summary(self):
        """Test DeploymentResult summary for failure."""
        from forge_harness.deployment.base import DeploymentResult

        result = DeploymentResult(
            success=False,
            provider="railway",
            target="backend",
            error="Connection failed",
        )

        assert "FAILED" in result.summary
        assert result.error == "Connection failed"

    def test_deployment_config_from_path(self):
        """Test DeploymentConfig has correct derived paths."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        config = DeploymentConfig(
            domain="test-domain",
            project="test-project",
            project_dir=Path("/app"),
            target=DeploymentTarget.BACKEND,
        )

        assert config.backend_dir == Path("/app/backend")
        assert config.frontend_dir == Path("/app/frontend")

    def test_provider_capabilities_dataclass(self):
        """Test ProviderCapabilities can be created."""
        from forge_harness.deployment.base import DeploymentTarget, ProviderCapabilities

        caps = ProviderCapabilities(
            name="test",
            targets=[DeploymentTarget.BACKEND],
            supports_rollback=True,
            required_env_vars=["TEST_TOKEN"],
        )

        assert caps.name == "test"
        assert DeploymentTarget.BACKEND in caps.targets
        assert caps.supports_rollback is True

    def test_deployment_target_enum(self):
        """Test DeploymentTarget enum values."""
        from forge_harness.deployment.base import DeploymentTarget

        assert DeploymentTarget.BACKEND.value == "backend"
        assert DeploymentTarget.FRONTEND.value == "frontend"
        assert DeploymentTarget.FULL_STACK.value == "full_stack"


class TestRailwayProvider:
    """Tests for Railway deployment provider."""

    @pytest.fixture
    def railway_provider(self):
        """Create Railway provider instance."""
        from forge_harness.deployment.railway import RailwayProvider

        return RailwayProvider()

    def test_capabilities(self, railway_provider):
        """Test Railway provider capabilities."""
        from forge_harness.deployment.base import DeploymentTarget

        caps = railway_provider.capabilities
        assert caps.name == "railway"
        assert DeploymentTarget.BACKEND in caps.targets
        assert caps.supports_rollback is True
        assert "RAILWAY_TOKEN" in caps.required_env_vars

    def test_not_configured_without_token(self, railway_provider):
        """Test provider reports not configured without token."""
        with patch.dict(os.environ, {}, clear=True):
            assert railway_provider.is_configured() is False
            assert "RAILWAY_TOKEN" in railway_provider.missing_config()

    def test_configured_with_token(self, railway_provider):
        """Test provider reports configured with token."""
        with patch.dict(os.environ, {"RAILWAY_TOKEN": "test_token"}):
            assert railway_provider.is_configured() is True
            assert railway_provider.missing_config() == []

    def test_deploy_missing_token(self, railway_provider, tmp_path):
        """Test deploy fails without token."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        config = DeploymentConfig(
            domain="test",
            project="app",
            project_dir=tmp_path,
            target=DeploymentTarget.BACKEND,
        )

        with patch.dict(os.environ, {}, clear=True):
            result = railway_provider.deploy(config)

        assert result.success is False
        assert "RAILWAY_TOKEN not configured" in result.error

    def test_deploy_missing_backend_dir(self, railway_provider, tmp_path):
        """Test deploy fails without backend directory."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        config = DeploymentConfig(
            domain="test",
            project="app",
            project_dir=tmp_path,
            target=DeploymentTarget.BACKEND,
        )

        with patch.dict(os.environ, {"RAILWAY_TOKEN": "test_token"}):
            result = railway_provider.deploy(config)

        assert result.success is False
        assert "Backend directory not found" in result.error

    def test_deploy_dry_run(self, railway_provider, tmp_path):
        """Test deploy in dry-run mode."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        # Create backend directory
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = DeploymentConfig(
            domain="test",
            project="app",
            project_dir=tmp_path,
            target=DeploymentTarget.BACKEND,
            dry_run=True,
        )

        with patch.dict(os.environ, {"RAILWAY_TOKEN": "test_token"}):
            result = railway_provider.deploy(config)

        assert result.success is True
        assert result.metadata.get("dry_run") is True
        assert "railway up" in result.logs

    def test_deploy_success(self, railway_provider, tmp_path):
        """Test successful deployment."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        # Create backend directory
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = DeploymentConfig(
            domain="test",
            project="app",
            project_dir=tmp_path,
            target=DeploymentTarget.BACKEND,
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Deployed to https://test.railway.app"
        mock_result.stderr = ""

        with patch.dict(os.environ, {"RAILWAY_TOKEN": "test_token"}):
            with patch.object(railway_provider, "_run_command", return_value=mock_result):
                result = railway_provider.deploy(config)

        assert result.success is True
        assert result.url == "https://test.railway.app"

    def test_verify_healthy(self, railway_provider):
        """Test verify with healthy endpoint."""
        with patch("httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = railway_provider.verify("https://test.railway.app")

        assert result["healthy"] is True
        assert result["status_code"] == 200

    def test_verify_unhealthy(self, railway_provider):
        """Test verify with unhealthy endpoint."""
        with patch("httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_get.return_value = mock_response

            result = railway_provider.verify("https://test.railway.app")

        assert result["healthy"] is False
        assert result["status_code"] == 500

    def test_verify_connection_error(self, railway_provider):
        """Test verify with connection error."""
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            result = railway_provider.verify("https://test.railway.app")

        assert result["healthy"] is False
        assert "Connection refused" in result["error"]


class TestCloudflareProvider:
    """Tests for Cloudflare Pages deployment provider."""

    @pytest.fixture
    def cloudflare_provider(self):
        """Create Cloudflare provider instance."""
        from forge_harness.deployment.cloudflare import CloudflareProvider

        return CloudflareProvider()

    def test_capabilities(self, cloudflare_provider):
        """Test Cloudflare provider capabilities."""
        from forge_harness.deployment.base import DeploymentTarget

        caps = cloudflare_provider.capabilities
        assert caps.name == "cloudflare"
        assert DeploymentTarget.FRONTEND in caps.targets
        assert caps.supports_rollback is False  # Cloudflare Pages doesn't support CLI rollback
        assert "CLOUDFLARE_API_TOKEN" in caps.required_env_vars
        assert "CLOUDFLARE_ACCOUNT_ID" in caps.required_env_vars

    def test_not_configured_without_credentials(self, cloudflare_provider):
        """Test provider reports not configured without credentials."""
        with patch.dict(os.environ, {}, clear=True):
            assert cloudflare_provider.is_configured() is False
            missing = cloudflare_provider.missing_config()
            assert "CLOUDFLARE_API_TOKEN" in missing
            assert "CLOUDFLARE_ACCOUNT_ID" in missing

    def test_configured_with_credentials(self, cloudflare_provider):
        """Test provider reports configured with credentials."""
        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_API_TOKEN": "token",
                "CLOUDFLARE_ACCOUNT_ID": "account",
            },
        ):
            assert cloudflare_provider.is_configured() is True
            assert cloudflare_provider.missing_config() == []

    def test_deploy_missing_credentials(self, cloudflare_provider, tmp_path):
        """Test deploy fails without credentials."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        config = DeploymentConfig(
            domain="test",
            project="app",
            project_dir=tmp_path,
            target=DeploymentTarget.FRONTEND,
        )

        with patch.dict(os.environ, {}, clear=True):
            result = cloudflare_provider.deploy(config)

        assert result.success is False
        assert "Cloudflare credentials not configured" in result.error

    def test_deploy_missing_frontend_dir(self, cloudflare_provider, tmp_path):
        """Test deploy fails without frontend directory."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        config = DeploymentConfig(
            domain="test",
            project="app",
            project_dir=tmp_path,
            target=DeploymentTarget.FRONTEND,
        )

        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_API_TOKEN": "token",
                "CLOUDFLARE_ACCOUNT_ID": "account",
            },
        ):
            result = cloudflare_provider.deploy(config)

        assert result.success is False
        assert "Frontend directory not found" in result.error

    def test_deploy_dry_run(self, cloudflare_provider, tmp_path):
        """Test deploy in dry-run mode."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        # Create frontend directory
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()

        config = DeploymentConfig(
            domain="test",
            project="app",
            project_dir=tmp_path,
            target=DeploymentTarget.FRONTEND,
            dry_run=True,
        )

        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_API_TOKEN": "token",
                "CLOUDFLARE_ACCOUNT_ID": "account",
            },
        ):
            result = cloudflare_provider.deploy(config)

        assert result.success is True
        assert result.metadata.get("dry_run") is True
        assert "wrangler pages deploy" in result.logs

    def test_project_name_generation(self, cloudflare_provider, tmp_path):
        """Test project name generation from project name."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        config = DeploymentConfig(
            domain="test",
            project="my-cool-app",
            project_dir=tmp_path,
            target=DeploymentTarget.FRONTEND,
        )

        project_name = cloudflare_provider._get_project_name(config)
        assert project_name == "mycoolapp-app"

    def test_project_name_from_env(self, cloudflare_provider, tmp_path):
        """Test project name from environment variable."""
        from forge_harness.deployment.base import DeploymentConfig, DeploymentTarget

        config = DeploymentConfig(
            domain="test",
            project="app",
            project_dir=tmp_path,
            target=DeploymentTarget.FRONTEND,
        )

        with patch.dict(os.environ, {"CLOUDFLARE_PROJECT_NAME": "custom-project"}):
            project_name = cloudflare_provider._get_project_name(config)

        assert project_name == "custom-project"

    def test_verify_healthy_html(self, cloudflare_provider):
        """Test verify with healthy HTML response."""
        with patch("httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<!DOCTYPE html><html><body>Hello</body></html>"
            mock_get.return_value = mock_response

            result = cloudflare_provider.verify("https://test.pages.dev")

        assert result["healthy"] is True
        assert result["has_html"] is True

    def test_verify_not_html(self, cloudflare_provider):
        """Test verify with non-HTML response."""
        with patch("httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"error": "not found"}'
            mock_get.return_value = mock_response

            result = cloudflare_provider.verify("https://test.pages.dev")

        assert result["healthy"] is False
        assert result["has_html"] is False


class TestProviderRegistry:
    """Tests for ProviderRegistry."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset registry before each test."""
        from forge_harness.deployment.registry import ProviderRegistry

        ProviderRegistry.reset()
        yield
        ProviderRegistry.reset()

    def test_list_providers(self):
        """Test listing available providers."""
        from forge_harness.deployment.registry import ProviderRegistry

        providers = ProviderRegistry.list_providers()
        assert "railway" in providers
        assert "cloudflare" in providers

    def test_get_provider(self):
        """Test getting a provider by name."""
        from forge_harness.deployment.base import DeploymentProvider
        from forge_harness.deployment.registry import ProviderRegistry

        railway = ProviderRegistry.get("railway")
        assert isinstance(railway, DeploymentProvider)
        assert railway.capabilities.name == "railway"

    def test_get_unknown_provider(self):
        """Test getting unknown provider raises error."""
        from forge_harness.deployment.registry import ProviderRegistry

        with pytest.raises(KeyError) as exc_info:
            ProviderRegistry.get("unknown")

        assert "unknown" in str(exc_info.value)
        assert "railway" in str(exc_info.value)  # Should list available

    def test_list_capabilities(self):
        """Test listing all provider capabilities."""
        from forge_harness.deployment.registry import ProviderRegistry

        caps = ProviderRegistry.list_capabilities()
        assert "railway" in caps
        assert "cloudflare" in caps
        assert caps["railway"].name == "railway"
        assert caps["cloudflare"].name == "cloudflare"

    def test_for_target_backend(self):
        """Test getting providers for backend target."""
        from forge_harness.deployment.base import DeploymentTarget
        from forge_harness.deployment.registry import ProviderRegistry

        providers = ProviderRegistry.for_target(DeploymentTarget.BACKEND)
        provider_names = [p.capabilities.name for p in providers]
        assert "railway" in provider_names
        assert "cloudflare" not in provider_names

    def test_for_target_frontend(self):
        """Test getting providers for frontend target."""
        from forge_harness.deployment.base import DeploymentTarget
        from forge_harness.deployment.registry import ProviderRegistry

        providers = ProviderRegistry.for_target(DeploymentTarget.FRONTEND)
        provider_names = [p.capabilities.name for p in providers]
        assert "cloudflare" in provider_names
        assert "railway" not in provider_names

    def test_configured_providers_none(self):
        """Test configured providers when none are configured."""
        from forge_harness.deployment.registry import ProviderRegistry

        with patch.dict(os.environ, {}, clear=True):
            configured = ProviderRegistry.configured_providers()

        assert configured == []

    def test_configured_providers_railway(self):
        """Test configured providers with Railway token."""
        from forge_harness.deployment.registry import ProviderRegistry

        with patch.dict(os.environ, {"RAILWAY_TOKEN": "test"}, clear=True):
            configured = ProviderRegistry.configured_providers()

        provider_names = [p.capabilities.name for p in configured]
        assert "railway" in provider_names
        assert "cloudflare" not in provider_names

    def test_unconfigured_providers(self):
        """Test unconfigured providers listing."""
        from forge_harness.deployment.registry import ProviderRegistry

        with patch.dict(os.environ, {}, clear=True):
            unconfigured = ProviderRegistry.unconfigured_providers()

        assert "railway" in unconfigured
        assert "RAILWAY_TOKEN" in unconfigured["railway"]
        assert "cloudflare" in unconfigured
        assert "CLOUDFLARE_API_TOKEN" in unconfigured["cloudflare"]

    def test_register_custom_provider(self):
        """Test registering a custom provider."""
        from forge_harness.deployment.base import (
            DeploymentConfig,
            DeploymentProvider,
            DeploymentResult,
            DeploymentTarget,
            ProviderCapabilities,
        )
        from forge_harness.deployment.registry import ProviderRegistry

        class CustomProvider(DeploymentProvider):
            @property
            def capabilities(self) -> ProviderCapabilities:
                return ProviderCapabilities(
                    name="custom",
                    targets=[DeploymentTarget.BACKEND],
                    required_env_vars=[],
                )

            def deploy(self, config: DeploymentConfig) -> DeploymentResult:
                return DeploymentResult(
                    success=True,
                    provider="custom",
                    target=config.target.value,
                )

            def verify(self, url: str, timeout: int = 30) -> dict:
                return {"healthy": True}

        ProviderRegistry.register("custom", CustomProvider)
        assert "custom" in ProviderRegistry.list_providers()

        custom = ProviderRegistry.get("custom")
        assert custom.capabilities.name == "custom"

    def test_unregister_provider(self):
        """Test unregistering a provider."""
        from forge_harness.deployment.registry import ProviderRegistry

        assert "railway" in ProviderRegistry.list_providers()
        ProviderRegistry.unregister("railway")
        assert "railway" not in ProviderRegistry.list_providers()


class TestForgeDeployerWithProviders:
    """Tests for ForgeDeployer using new provider system."""

    def test_deploy_with_provider_dry_run(self, tmp_path):
        """Test ForgeDeployer.deploy_with_provider in dry-run mode."""
        from forge_harness.deployment_legacy import ForgeDeployer

        # Create backend directory
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        deployer = ForgeDeployer(
            domain="test-domain",
            project="test-project",
            project_dir=tmp_path,
        )

        with patch.dict(os.environ, {"RAILWAY_TOKEN": "test_token"}):
            result = deployer.deploy_with_provider("railway", dry_run=True)

        assert result.success is True
        assert result.provider == "railway"
        assert result.metadata.get("dry_run") is True

    def test_deploy_with_provider_cloudflare(self, tmp_path):
        """Test ForgeDeployer.deploy_with_provider for Cloudflare."""
        from forge_harness.deployment_legacy import ForgeDeployer

        # Create frontend directory
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()

        deployer = ForgeDeployer(
            domain="test-domain",
            project="test-project",
            project_dir=tmp_path,
        )

        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_API_TOKEN": "token",
                "CLOUDFLARE_ACCOUNT_ID": "account",
            },
        ):
            result = deployer.deploy_with_provider("cloudflare", dry_run=True)

        assert result.success is True
        assert result.provider == "cloudflare"


class TestBackwardCompatibility:
    """Tests for backward compatibility with legacy imports."""

    def test_import_from_forge_harness(self):
        """Test importing from forge_harness works."""
        from forge_harness import (
            DeploymentProvider,
            ForgeDeployer,
            ProviderRegistry,
        )

        assert ForgeDeployer is not None
        assert DeploymentProvider is not None
        assert ProviderRegistry is not None

    def test_import_from_deployment_legacy(self):
        """Test importing from deployment_legacy works."""
        from forge_harness.deployment_legacy import (
            DeploymentResult,
            ForgeDeployer,
        )

        assert ForgeDeployer is not None
        assert DeploymentResult is not None

    def test_import_from_deployment_package(self):
        """Test importing from deployment package works."""
        from forge_harness.deployment import (
            ForgeDeployer,
            ProviderRegistry,
        )

        assert ForgeDeployer is not None
        assert ProviderRegistry is not None
