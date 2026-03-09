"""
Tests for DeploymentHarness - Railway and Cloudflare Pages Integration
========================================================================

Tests cover:
- Railway GraphQL API integration
- Cloudflare Pages REST API integration
- Status tracking and polling
- Rollback functionality
- Error handling
- Dry-run mode
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.deployment_harness import (
    CloudflareClient,
    DeploymentHarness,
    DeploymentPlatform,
    DeploymentResult,
    DeploymentStatus,
    RailwayClient,
    create_deployment_harness,
)


@pytest.fixture
def railway_token():
    """Railway API token for testing."""
    return "railway_test_token"


@pytest.fixture
def cloudflare_token():
    """Cloudflare API token for testing."""
    return "cloudflare_test_token"


@pytest.fixture
def cloudflare_account_id():
    """Cloudflare account ID for testing."""
    return "test_account_id"


@pytest.fixture
def railway_client(railway_token):
    """Railway client instance."""
    return RailwayClient(railway_token)


@pytest.fixture
def cloudflare_client(cloudflare_token, cloudflare_account_id):
    """Cloudflare client instance."""
    return CloudflareClient(cloudflare_token, cloudflare_account_id)


@pytest.fixture
def deployment_harness(railway_token, cloudflare_token, cloudflare_account_id):
    """Full deployment harness."""
    return DeploymentHarness(
        railway_token=railway_token,
        cloudflare_token=cloudflare_token,
        cloudflare_account_id=cloudflare_account_id,
    )


@pytest.fixture
def dry_run_harness():
    """Deployment harness in dry-run mode."""
    return DeploymentHarness(dry_run=True)


# ============================================================================
# Railway Client Tests
# ============================================================================


@pytest.mark.asyncio
async def test_railway_deploy_service_success(railway_client):
    """Test successful Railway service deployment."""
    with patch("aiohttp.ClientSession.post") as mock_post:
        # Mock successful deployment response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "data": {
                    "serviceDeploy": {
                        "id": "deploy-123",
                        "status": "BUILDING",
                        "createdAt": "2026-01-29T10:00:00Z",
                        "url": "https://my-service.railway.app",
                    }
                }
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await railway_client.deploy_service(
            project_id="proj-123", service_id="svc-456", env_vars={"API_KEY": "secret"}
        )

        assert result.deployment_id == "deploy-123"
        assert result.platform == DeploymentPlatform.RAILWAY
        assert result.status == DeploymentStatus.BUILDING
        assert result.url == "https://my-service.railway.app"
        assert result.metadata["project_id"] == "proj-123"
        assert result.metadata["service_id"] == "svc-456"


@pytest.mark.asyncio
async def test_railway_deploy_service_error(railway_client):
    """Test Railway deployment with GraphQL error."""
    with patch("aiohttp.ClientSession.post") as mock_post:
        # Mock error response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"errors": [{"message": "Service not found"}]})
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await railway_client.deploy_service(
            project_id="proj-123", service_id="invalid-svc"
        )

        assert result.status == DeploymentStatus.FAILED
        assert result.error_message == "Service not found"


@pytest.mark.asyncio
async def test_railway_get_deployment_status(railway_client):
    """Test fetching Railway deployment status."""
    with patch("aiohttp.ClientSession.post") as mock_post:
        # Mock deployment status response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "data": {
                    "deployment": {
                        "id": "deploy-123",
                        "status": "SUCCESS",
                        "createdAt": "2026-01-29T10:00:00Z",
                        "completedAt": "2026-01-29T10:05:00Z",
                        "url": "https://my-service.railway.app",
                    }
                }
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await railway_client.get_deployment_status("deploy-123")

        assert result.deployment_id == "deploy-123"
        assert result.status == DeploymentStatus.SUCCESS
        assert result.completed_at is not None


@pytest.mark.asyncio
async def test_railway_rollback_success(railway_client):
    """Test successful Railway rollback."""
    with patch("aiohttp.ClientSession.post") as mock_post:
        # Mock rollback response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"data": {"deploymentRollback": {"id": "deploy-122", "status": "SUCCESS"}}}
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        success = await railway_client.rollback("deploy-123")

        assert success is True


@pytest.mark.asyncio
async def test_railway_status_mapping(railway_client):
    """Test Railway status string mapping."""
    assert railway_client._map_railway_status("BUILDING") == DeploymentStatus.BUILDING
    assert railway_client._map_railway_status("DEPLOYING") == DeploymentStatus.DEPLOYING
    assert railway_client._map_railway_status("SUCCESS") == DeploymentStatus.SUCCESS
    assert railway_client._map_railway_status("FAILED") == DeploymentStatus.FAILED
    assert railway_client._map_railway_status("CRASHED") == DeploymentStatus.FAILED
    assert railway_client._map_railway_status("UNKNOWN") == DeploymentStatus.PENDING


# ============================================================================
# Cloudflare Client Tests
# ============================================================================


@pytest.mark.asyncio
async def test_cloudflare_deploy_pages_success(cloudflare_client, tmp_path):
    """Test successful Cloudflare Pages deployment."""
    # Create a temporary build directory
    build_dir = tmp_path / "dist"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("<html>Test</html>")

    with patch("aiohttp.ClientSession.post") as mock_post:
        # Mock successful deployment response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "result": {
                    "id": "cf-deploy-123",
                    "stage": "success",
                    "created_on": "2026-01-29T10:00:00Z",
                    "url": "https://my-app.pages.dev",
                    "build_config": {"build_logs_url": "https://logs.example.com"},
                },
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await cloudflare_client.deploy_pages("my-app", build_dir)

        assert result.deployment_id == "cf-deploy-123"
        assert result.platform == DeploymentPlatform.CLOUDFLARE
        assert result.status == DeploymentStatus.SUCCESS
        assert result.url == "https://my-app.pages.dev"
        assert result.build_logs_url == "https://logs.example.com"


@pytest.mark.asyncio
async def test_cloudflare_deploy_pages_error(cloudflare_client, tmp_path):
    """Test Cloudflare deployment with API error."""
    build_dir = tmp_path / "dist"
    build_dir.mkdir()

    with patch("aiohttp.ClientSession.post") as mock_post:
        # Mock error response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": False,
                "errors": [{"message": "Invalid project configuration"}],
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await cloudflare_client.deploy_pages("my-app", build_dir)

        assert result.status == DeploymentStatus.FAILED
        assert result.error_message == "Invalid project configuration"


@pytest.mark.asyncio
async def test_cloudflare_get_deployment_status(cloudflare_client):
    """Test fetching Cloudflare deployment status."""
    with patch("aiohttp.ClientSession.get") as mock_get:
        # Mock deployment status response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "result": {
                    "id": "cf-deploy-123",
                    "stage": "success",
                    "created_on": "2026-01-29T10:00:00Z",
                    "modified_on": "2026-01-29T10:05:00Z",
                    "url": "https://my-app.pages.dev",
                },
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value.__aenter__.return_value = mock_response

        result = await cloudflare_client.get_deployment_status("my-app", "cf-deploy-123")

        assert result.deployment_id == "cf-deploy-123"
        assert result.status == DeploymentStatus.SUCCESS
        assert result.completed_at is not None


@pytest.mark.asyncio
async def test_cloudflare_rollback_success(cloudflare_client):
    """Test successful Cloudflare Pages rollback."""
    with (
        patch("aiohttp.ClientSession.get") as mock_get,
        patch("aiohttp.ClientSession.post") as mock_post,
    ):
        # Mock get deployments response
        mock_get_response = AsyncMock()
        mock_get_response.status = 200
        mock_get_response.json = AsyncMock(
            return_value={
                "success": True,
                "result": [
                    {"id": "cf-deploy-123", "stage": "success"},  # Current
                    {"id": "cf-deploy-122", "stage": "success"},  # Previous
                ],
            }
        )
        mock_get_response.raise_for_status = MagicMock()
        mock_get.return_value.__aenter__.return_value = mock_get_response

        # Mock retry response
        mock_post_response = AsyncMock()
        mock_post_response.status = 200
        mock_post_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_post_response

        success = await cloudflare_client.rollback("my-app", "cf-deploy-123")

        assert success is True


@pytest.mark.asyncio
async def test_cloudflare_status_mapping(cloudflare_client):
    """Test Cloudflare stage string mapping."""
    assert cloudflare_client._map_cloudflare_status("queued") == DeploymentStatus.PENDING
    assert cloudflare_client._map_cloudflare_status("build") == DeploymentStatus.BUILDING
    assert cloudflare_client._map_cloudflare_status("deploy") == DeploymentStatus.DEPLOYING
    assert cloudflare_client._map_cloudflare_status("success") == DeploymentStatus.SUCCESS
    assert cloudflare_client._map_cloudflare_status("failure") == DeploymentStatus.FAILED


# ============================================================================
# DeploymentHarness Tests
# ============================================================================


@pytest.mark.asyncio
async def test_harness_deploy_to_railway(deployment_harness):
    """Test DeploymentHarness Railway deployment."""
    with patch.object(deployment_harness.railway_client, "deploy_service") as mock_deploy:
        mock_deploy.return_value = DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.BUILDING,
            url="https://my-service.railway.app",
            started_at=datetime.now(),
        )

        result = await deployment_harness.deploy_to_railway(
            project="my-project",
            service="api",
            project_id="proj-123",
            service_id="svc-456",
        )

        assert result.deployment_id == "deploy-123"
        assert result.platform == DeploymentPlatform.RAILWAY
        mock_deploy.assert_called_once()


@pytest.mark.asyncio
async def test_harness_deploy_to_cloudflare(deployment_harness, tmp_path):
    """Test DeploymentHarness Cloudflare deployment."""
    build_dir = tmp_path / "dist"
    build_dir.mkdir()

    with patch.object(deployment_harness.cloudflare_client, "deploy_pages") as mock_deploy:
        mock_deploy.return_value = DeploymentResult(
            deployment_id="cf-deploy-123",
            platform=DeploymentPlatform.CLOUDFLARE,
            status=DeploymentStatus.PENDING,
            url="https://my-app.pages.dev",
            started_at=datetime.now(),
        )

        result = await deployment_harness.deploy_to_cloudflare(
            project="my-app", directory=build_dir
        )

        assert result.deployment_id == "cf-deploy-123"
        assert result.platform == DeploymentPlatform.CLOUDFLARE
        mock_deploy.assert_called_once()


@pytest.mark.asyncio
async def test_harness_get_deployment_status_railway(deployment_harness):
    """Test getting Railway deployment status."""
    # First deploy to track metadata
    with patch.object(deployment_harness.railway_client, "deploy_service") as mock_deploy:
        mock_deploy.return_value = DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.BUILDING,
            started_at=datetime.now(),
        )

        await deployment_harness.deploy_to_railway(
            project="test",
            service="api",
            project_id="proj-123",
            service_id="svc-456",
        )

    # Now check status
    with patch.object(deployment_harness.railway_client, "get_deployment_status") as mock_status:
        mock_status.return_value = DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.SUCCESS,
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )

        result = await deployment_harness.get_deployment_status("deploy-123")

        assert result.status == DeploymentStatus.SUCCESS
        mock_status.assert_called_once_with("deploy-123")


@pytest.mark.asyncio
async def test_harness_rollback_railway(deployment_harness):
    """Test Railway rollback."""
    # First deploy to track metadata
    with patch.object(deployment_harness.railway_client, "deploy_service") as mock_deploy:
        mock_deploy.return_value = DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.SUCCESS,
            started_at=datetime.now(),
        )

        await deployment_harness.deploy_to_railway(
            project="test",
            service="api",
            project_id="proj-123",
            service_id="svc-456",
        )

    # Now rollback
    with patch.object(deployment_harness.railway_client, "rollback") as mock_rollback:
        mock_rollback.return_value = True

        success = await deployment_harness.rollback("deploy-123")

        assert success is True
        mock_rollback.assert_called_once_with("deploy-123")


@pytest.mark.asyncio
async def test_harness_dry_run_railway(dry_run_harness):
    """Test Railway deployment in dry-run mode."""
    result = await dry_run_harness.deploy_to_railway(
        project="test", service="api", project_id="proj-123", service_id="svc-456"
    )

    assert result.deployment_id.startswith("dry-run-railway-")
    assert result.status == DeploymentStatus.SUCCESS
    assert result.url == "https://api.railway.app"


@pytest.mark.asyncio
async def test_harness_dry_run_cloudflare(dry_run_harness):
    """Test Cloudflare deployment in dry-run mode."""
    result = await dry_run_harness.deploy_to_cloudflare(project="my-app", directory="/tmp/dist")

    assert result.deployment_id.startswith("dry-run-cloudflare-")
    assert result.status == DeploymentStatus.SUCCESS
    assert result.url == "https://my-app.pages.dev"


@pytest.mark.asyncio
async def test_harness_wait_for_completion_success(deployment_harness):
    """Test waiting for successful deployment."""
    # Deploy first
    with patch.object(deployment_harness.railway_client, "deploy_service") as mock_deploy:
        mock_deploy.return_value = DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.BUILDING,
            started_at=datetime.now(),
        )

        await deployment_harness.deploy_to_railway(
            project="test",
            service="api",
            project_id="proj-123",
            service_id="svc-456",
        )

    # Mock status polling: building -> deploying -> success
    statuses = [
        DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.BUILDING,
            started_at=datetime.now(),
        ),
        DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.DEPLOYING,
            started_at=datetime.now(),
        ),
        DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.SUCCESS,
            started_at=datetime.now(),
            completed_at=datetime.now(),
        ),
    ]

    with patch.object(deployment_harness.railway_client, "get_deployment_status") as mock_status:
        mock_status.side_effect = statuses

        result = await deployment_harness.wait_for_completion(
            "deploy-123", timeout_seconds=30, poll_interval_seconds=0.1
        )

        assert result.status == DeploymentStatus.SUCCESS
        assert mock_status.call_count == 3


@pytest.mark.asyncio
async def test_harness_wait_for_completion_timeout(deployment_harness):
    """Test timeout when waiting for deployment."""
    # Deploy first
    with patch.object(deployment_harness.railway_client, "deploy_service") as mock_deploy:
        mock_deploy.return_value = DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.BUILDING,
            started_at=datetime.now(),
        )

        await deployment_harness.deploy_to_railway(
            project="test",
            service="api",
            project_id="proj-123",
            service_id="svc-456",
        )

    # Mock status always returning building
    with patch.object(deployment_harness.railway_client, "get_deployment_status") as mock_status:
        mock_status.return_value = DeploymentResult(
            deployment_id="deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.BUILDING,
            started_at=datetime.now(),
        )

        with pytest.raises(TimeoutError):
            await deployment_harness.wait_for_completion(
                "deploy-123", timeout_seconds=1, poll_interval_seconds=0.1
            )


@pytest.mark.asyncio
async def test_harness_missing_railway_credentials():
    """Test error when Railway credentials missing."""
    harness = DeploymentHarness()

    with pytest.raises(ValueError, match="Railway client not configured"):
        await harness.deploy_to_railway(
            project="test", service="api", project_id="proj-123", service_id="svc-456"
        )


@pytest.mark.asyncio
async def test_harness_missing_cloudflare_credentials():
    """Test error when Cloudflare credentials missing."""
    harness = DeploymentHarness()

    with pytest.raises(ValueError, match="Cloudflare client not configured"):
        await harness.deploy_to_cloudflare(project="test", directory="/tmp/dist")


# ============================================================================
# Factory Function Tests
# ============================================================================


def test_create_deployment_harness_from_env():
    """Test creating harness from environment variables."""
    with patch.dict(
        "os.environ",
        {
            "RAILWAY_TOKEN": "railway_token",
            "CLOUDFLARE_API_TOKEN": "cf_token",
            "CLOUDFLARE_ACCOUNT_ID": "cf_account",
        },
    ):
        harness = create_deployment_harness()

        assert harness.railway_client is not None
        assert harness.cloudflare_client is not None
        assert harness.dry_run is False


def test_create_deployment_harness_dry_run():
    """Test creating harness in dry-run mode."""
    harness = create_deployment_harness(dry_run=True)

    assert harness.dry_run is True


def test_create_deployment_harness_partial_credentials():
    """Test creating harness with partial credentials."""
    harness = create_deployment_harness(railway_token="railway_token")

    assert harness.railway_client is not None
    assert harness.cloudflare_client is None
