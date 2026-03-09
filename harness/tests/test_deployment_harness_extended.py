"""
Extended Tests for DeploymentHarness - Railway and Cloudflare Pages Integration
================================================================================

Covers gaps in test_deployment_harness.py:
- All DeploymentStatus / DeploymentPlatform enum values
- DeploymentResult dataclass fields
- Railway: _update_env_vars (multiple vars, HTTP error)
- Railway: rollback failure (GraphQL errors)
- Railway: get_deployment_status with GraphQL errors
- Railway: status mapping edge-cases (CANCELLED, all variants)
- Cloudflare: deploy_pages 404 auto-create-project path
- Cloudflare: deploy_pages missing directory raises FileNotFoundError
- Cloudflare: get_deployment_status error response
- Cloudflare: get_deployment_status non-terminal stage (no completed_at)
- Cloudflare: rollback - no previous successful deployment
- Cloudflare: rollback - API failure fetching deployments
- Cloudflare: status mapping all stages
- DeploymentHarness: get_deployment_status dry-run prefix detection
- DeploymentHarness: get_deployment_status unknown deployment_id
- DeploymentHarness: get_deployment_status unknown platform in metadata
- DeploymentHarness: rollback dry-run path
- DeploymentHarness: rollback unknown deployment_id
- DeploymentHarness: rollback unknown platform in metadata
- DeploymentHarness: rollback cloudflare via harness
- DeploymentHarness: deploy_to_railway missing project_id / service_id
- DeploymentHarness: metadata stored per deployment
- DeploymentHarness: wait_for_completion FAILED terminal state
- DeploymentHarness: wait_for_completion CANCELLED terminal state
- DeploymentHarness: wait_for_completion ROLLED_BACK terminal state
- DeploymentHarness: wait_for_completion dry-run (immediate success)
- create_deployment_harness: explicit params override env vars
- create_deployment_harness: no env vars produces no-credential harness
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
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

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def railway_client():
    return RailwayClient("railway_test_token_ext")


@pytest.fixture
def cloudflare_client():
    return CloudflareClient("cf_test_token_ext", "cf_account_ext")


@pytest.fixture
def full_harness():
    return DeploymentHarness(
        railway_token="rw_token",
        cloudflare_token="cf_token",
        cloudflare_account_id="cf_acct",
    )


@pytest.fixture
def dry_harness():
    return DeploymentHarness(dry_run=True)


# ============================================================================
# 1. DeploymentStatus Enum Values
# ============================================================================


def test_deployment_status_all_values():
    """All expected DeploymentStatus values are present."""
    expected = {"pending", "building", "deploying", "success", "failed", "cancelled", "rolled_back"}
    actual = {s.value for s in DeploymentStatus}
    assert actual == expected


def test_deployment_status_is_str():
    """DeploymentStatus extends str for easy comparison."""
    assert DeploymentStatus.SUCCESS == "success"
    assert DeploymentStatus.FAILED == "failed"
    assert DeploymentStatus.ROLLED_BACK == "rolled_back"


# ============================================================================
# 2. DeploymentPlatform Enum Values
# ============================================================================


def test_deployment_platform_all_values():
    """All expected DeploymentPlatform values are present."""
    expected = {"railway", "cloudflare", "unknown"}
    actual = {p.value for p in DeploymentPlatform}
    assert actual == expected


def test_deployment_platform_is_str():
    """DeploymentPlatform extends str."""
    assert DeploymentPlatform.RAILWAY == "railway"
    assert DeploymentPlatform.CLOUDFLARE == "cloudflare"
    assert DeploymentPlatform.UNKNOWN == "unknown"


# ============================================================================
# 3. DeploymentResult Dataclass
# ============================================================================


def test_deployment_result_required_fields():
    """DeploymentResult can be constructed with only required fields."""
    result = DeploymentResult(
        deployment_id="test-id",
        platform=DeploymentPlatform.RAILWAY,
        status=DeploymentStatus.PENDING,
    )
    assert result.deployment_id == "test-id"
    assert result.platform == DeploymentPlatform.RAILWAY
    assert result.status == DeploymentStatus.PENDING
    assert result.url is None
    assert result.build_logs_url is None
    assert result.started_at is None
    assert result.completed_at is None
    assert result.error_message is None
    assert result.metadata is None


def test_deployment_result_all_fields():
    """DeploymentResult stores all optional fields correctly."""
    now = datetime.now()
    result = DeploymentResult(
        deployment_id="full-id",
        platform=DeploymentPlatform.CLOUDFLARE,
        status=DeploymentStatus.SUCCESS,
        url="https://app.pages.dev",
        build_logs_url="https://logs.example.com",
        started_at=now,
        completed_at=now,
        error_message=None,
        metadata={"key": "value"},
    )
    assert result.url == "https://app.pages.dev"
    assert result.build_logs_url == "https://logs.example.com"
    assert result.started_at == now
    assert result.completed_at == now
    assert result.metadata == {"key": "value"}


def test_deployment_result_error_message():
    """DeploymentResult stores error_message for failed deployments."""
    result = DeploymentResult(
        deployment_id="fail-id",
        platform=DeploymentPlatform.RAILWAY,
        status=DeploymentStatus.FAILED,
        error_message="GraphQL error: service not found",
    )
    assert result.error_message == "GraphQL error: service not found"


# ============================================================================
# 4. RailwayClient - Status Mapping (full coverage)
# ============================================================================


def test_railway_status_mapping_cancelled(railway_client):
    """CANCELLED maps to DeploymentStatus.CANCELLED."""
    assert railway_client._map_railway_status("CANCELLED") == DeploymentStatus.CANCELLED


def test_railway_status_mapping_deploying(railway_client):
    """DEPLOYING maps to DeploymentStatus.DEPLOYING."""
    assert railway_client._map_railway_status("DEPLOYING") == DeploymentStatus.DEPLOYING


def test_railway_status_mapping_crashed_as_failed(railway_client):
    """CRASHED maps to DeploymentStatus.FAILED."""
    assert railway_client._map_railway_status("CRASHED") == DeploymentStatus.FAILED


def test_railway_status_mapping_unknown_defaults_pending(railway_client):
    """Any unrecognised status maps to PENDING."""
    assert railway_client._map_railway_status("WHATEVER") == DeploymentStatus.PENDING


def test_railway_status_mapping_case_insensitive_lower(railway_client):
    """Status mapping is case-insensitive (uppercase applied internally)."""
    # The implementation calls .upper() before lookup
    assert railway_client._map_railway_status("success") == DeploymentStatus.SUCCESS
    assert railway_client._map_railway_status("building") == DeploymentStatus.BUILDING


# ============================================================================
# 5. RailwayClient - rollback failure
# ============================================================================


@pytest.mark.asyncio
async def test_railway_rollback_graphql_error(railway_client):
    """Railway rollback returns False when GraphQL returns errors."""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"errors": [{"message": "Deployment not found"}]}
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        success = await railway_client.rollback("nonexistent-deploy")
        assert success is False


# ============================================================================
# 6. RailwayClient - get_deployment_status with GraphQL error
# ============================================================================


@pytest.mark.asyncio
async def test_railway_get_status_graphql_error(railway_client):
    """get_deployment_status returns FAILED result on GraphQL error."""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"errors": [{"message": "Not authorized"}]}
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await railway_client.get_deployment_status("deploy-err")
        assert result.status == DeploymentStatus.FAILED
        assert result.error_message == "Not authorized"
        assert result.deployment_id == "deploy-err"
        assert result.platform == DeploymentPlatform.RAILWAY


# ============================================================================
# 7. RailwayClient - get_deployment_status with no completedAt
# ============================================================================


@pytest.mark.asyncio
async def test_railway_get_status_no_completed_at(railway_client):
    """get_deployment_status sets completed_at to None when not present."""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "data": {
                    "deployment": {
                        "id": "deploy-in-progress",
                        "status": "BUILDING",
                        "createdAt": "2026-02-01T08:00:00Z",
                        "completedAt": None,
                        "url": None,
                    }
                }
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await railway_client.get_deployment_status("deploy-in-progress")
        assert result.status == DeploymentStatus.BUILDING
        assert result.completed_at is None


# ============================================================================
# 8. RailwayClient - _update_env_vars with multiple variables
# ============================================================================


@pytest.mark.asyncio
async def test_railway_update_env_vars_multiple(railway_client):
    """_update_env_vars sends one request per variable."""
    call_count = 0

    async def mock_aenter(_self):
        nonlocal call_count
        call_count += 1
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.raise_for_status = MagicMock()
        return mock_response

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        env_vars = {"KEY_A": "val_a", "KEY_B": "val_b", "KEY_C": "val_c"}
        await railway_client._update_env_vars("svc-111", env_vars)

        # One POST per env var
        assert mock_post.call_count == 3


# ============================================================================
# 9. CloudflareClient - Status Mapping (full coverage)
# ============================================================================


def test_cloudflare_status_mapping_all_stages(cloudflare_client):
    """Cloudflare stage strings map to correct DeploymentStatus values."""
    cases = [
        ("queued", DeploymentStatus.PENDING),
        ("initialize", DeploymentStatus.BUILDING),
        ("clone_repo", DeploymentStatus.BUILDING),
        ("build", DeploymentStatus.BUILDING),
        ("deploy", DeploymentStatus.DEPLOYING),
        ("success", DeploymentStatus.SUCCESS),
        ("failure", DeploymentStatus.FAILED),
        ("cancelled", DeploymentStatus.CANCELLED),
        ("unknown_stage", DeploymentStatus.PENDING),
    ]
    for stage, expected_status in cases:
        result = cloudflare_client._map_cloudflare_status(stage)
        assert result == expected_status, f"Expected {expected_status} for stage={stage!r}"


def test_cloudflare_status_mapping_case_insensitive(cloudflare_client):
    """Cloudflare stage mapping is case-insensitive."""
    assert cloudflare_client._map_cloudflare_status("SUCCESS") == DeploymentStatus.SUCCESS
    assert cloudflare_client._map_cloudflare_status("BUILD") == DeploymentStatus.BUILDING


# ============================================================================
# 10. CloudflareClient - deploy_pages missing directory raises FileNotFoundError
# ============================================================================


@pytest.mark.asyncio
async def test_cloudflare_deploy_pages_missing_directory(cloudflare_client, tmp_path):
    """deploy_pages raises FileNotFoundError when directory does not exist."""
    nonexistent = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        await cloudflare_client.deploy_pages("my-app", nonexistent)


# ============================================================================
# 11. CloudflareClient - deploy_pages 404 triggers project creation then retry
# ============================================================================


@pytest.mark.asyncio
async def test_cloudflare_deploy_pages_404_creates_project(cloudflare_client, tmp_path):
    """deploy_pages on 404 creates the project and retries the deployment."""
    build_dir = tmp_path / "dist"
    build_dir.mkdir()

    # We need to return different context managers on successive calls.
    # session.post() returns a context manager (NOT a coroutine), so we use
    # a stateful callable as side_effect that returns MagicMock ctx managers.

    call_count = [0]

    def make_post_ctx(status, json_data=None):
        mock_response = AsyncMock()
        mock_response.status = status
        mock_response.raise_for_status = MagicMock()
        if json_data is not None:
            mock_response.json = AsyncMock(return_value=json_data)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_response)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    def post_side_effect(*args, **kwargs):
        call_count[0] += 1
        n = call_count[0]
        if n == 1:
            # First call: simulate 404 (project not found)
            return make_post_ctx(404)
        elif n == 2:
            # Second call: _create_project - returns 200 with no body needed
            return make_post_ctx(200)
        else:
            # Third call: retry deploy after project created
            return make_post_ctx(
                200,
                {
                    "success": True,
                    "result": {
                        "id": "cf-new-deploy",
                        "stage": "queued",
                        "created_on": "2026-02-01T12:00:00Z",
                        "url": "https://new-project.pages.dev",
                    },
                },
            )

    with patch("aiohttp.ClientSession.post", side_effect=post_side_effect):
        result = await cloudflare_client.deploy_pages("new-project", build_dir)

    assert call_count[0] == 3  # deploy(404) + create_project(200) + retry_deploy(200)
    assert result.deployment_id == "cf-new-deploy"


# ============================================================================
# 12. CloudflareClient - get_deployment_status error response
# ============================================================================


@pytest.mark.asyncio
async def test_cloudflare_get_status_api_error(cloudflare_client):
    """get_deployment_status returns FAILED result on API error."""
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": False,
                "errors": [{"message": "Deployment not found"}],
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value.__aenter__.return_value = mock_response

        result = await cloudflare_client.get_deployment_status("my-app", "bad-deploy-id")
        assert result.status == DeploymentStatus.FAILED
        assert result.error_message == "Deployment not found"
        assert result.deployment_id == "bad-deploy-id"


# ============================================================================
# 13. CloudflareClient - get_deployment_status non-terminal (no completed_at)
# ============================================================================


@pytest.mark.asyncio
async def test_cloudflare_get_status_non_terminal_no_completed_at(cloudflare_client):
    """get_deployment_status returns None completed_at for non-terminal stages."""
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "result": {
                    "id": "cf-build-123",
                    "stage": "build",
                    "created_on": "2026-02-01T09:00:00Z",
                    "modified_on": "2026-02-01T09:02:00Z",
                    "url": None,
                },
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value.__aenter__.return_value = mock_response

        result = await cloudflare_client.get_deployment_status("my-app", "cf-build-123")
        assert result.status == DeploymentStatus.BUILDING
        assert result.completed_at is None


# ============================================================================
# 14. CloudflareClient - rollback no previous successful deployment
# ============================================================================


@pytest.mark.asyncio
async def test_cloudflare_rollback_no_previous_deployment(cloudflare_client):
    """rollback returns False when no previous successful deployment exists."""
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "result": [
                    {"id": "cf-deploy-current", "stage": "success"},
                ],
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value.__aenter__.return_value = mock_response

        # Only current deployment exists; nothing to roll back to
        success = await cloudflare_client.rollback("my-app", "cf-deploy-current")
        assert success is False


# ============================================================================
# 15. CloudflareClient - rollback API failure fetching deployments
# ============================================================================


@pytest.mark.asyncio
async def test_cloudflare_rollback_api_failure(cloudflare_client):
    """rollback returns False when API call to list deployments fails."""
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": False,
                "errors": [{"message": "Unauthorized"}],
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value.__aenter__.return_value = mock_response

        success = await cloudflare_client.rollback("my-app", "cf-deploy-x")
        assert success is False


# ============================================================================
# 16. DeploymentHarness - get_deployment_status for dry-run deployments
# ============================================================================


@pytest.mark.asyncio
async def test_harness_get_status_dry_run_prefix(dry_harness):
    """get_deployment_status handles dry-run IDs without metadata lookup."""
    result = await dry_harness.get_deployment_status("dry-run-railway-test-api")
    assert result.deployment_id == "dry-run-railway-test-api"
    assert result.status == DeploymentStatus.SUCCESS
    assert result.platform == DeploymentPlatform.UNKNOWN


# ============================================================================
# 17. DeploymentHarness - get_deployment_status unknown deployment_id
# ============================================================================


@pytest.mark.asyncio
async def test_harness_get_status_unknown_deployment_id(full_harness):
    """get_deployment_status raises ValueError for unknown deployment_id."""
    with pytest.raises(ValueError, match="Unknown deployment_id"):
        await full_harness.get_deployment_status("totally-unknown-id")


# ============================================================================
# 18. DeploymentHarness - get_deployment_status cloudflare path
# ============================================================================


@pytest.mark.asyncio
async def test_harness_get_status_cloudflare_path(full_harness):
    """get_deployment_status delegates to cloudflare_client for cloudflare deployments."""
    # Manually inject metadata to simulate a prior deploy
    full_harness._deployment_metadata["cf-123"] = {
        "platform": "cloudflare",
        "project": "my-cf-app",
    }

    with patch.object(full_harness.cloudflare_client, "get_deployment_status") as mock_status:
        mock_status.return_value = DeploymentResult(
            deployment_id="cf-123",
            platform=DeploymentPlatform.CLOUDFLARE,
            status=DeploymentStatus.SUCCESS,
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )

        result = await full_harness.get_deployment_status("cf-123")
        assert result.status == DeploymentStatus.SUCCESS
        mock_status.assert_called_once_with("my-cf-app", "cf-123")


# ============================================================================
# 19. DeploymentHarness - get_deployment_status unknown platform in metadata
# ============================================================================


@pytest.mark.asyncio
async def test_harness_get_status_unknown_platform(full_harness):
    """get_deployment_status raises ValueError for unknown platform in metadata."""
    full_harness._deployment_metadata["weird-id"] = {"platform": "heroku"}

    with pytest.raises(ValueError, match="Unknown platform"):
        await full_harness.get_deployment_status("weird-id")


# ============================================================================
# 20. DeploymentHarness - rollback dry-run path
# ============================================================================


@pytest.mark.asyncio
async def test_harness_rollback_dry_run(dry_harness):
    """rollback in dry-run mode always returns True without touching API."""
    result = await dry_harness.rollback("any-id")
    assert result is True


# ============================================================================
# 21. DeploymentHarness - rollback unknown deployment_id
# ============================================================================


@pytest.mark.asyncio
async def test_harness_rollback_unknown_deployment_id(full_harness):
    """rollback raises ValueError for unknown deployment_id."""
    with pytest.raises(ValueError, match="Unknown deployment_id"):
        await full_harness.rollback("totally-unknown-id")


# ============================================================================
# 22. DeploymentHarness - rollback unknown platform in metadata
# ============================================================================


@pytest.mark.asyncio
async def test_harness_rollback_unknown_platform(full_harness):
    """rollback raises ValueError for unknown platform in metadata."""
    full_harness._deployment_metadata["weird-rb-id"] = {"platform": "heroku"}

    with pytest.raises(ValueError, match="Unknown platform"):
        await full_harness.rollback("weird-rb-id")


# ============================================================================
# 23. DeploymentHarness - rollback cloudflare via harness
# ============================================================================


@pytest.mark.asyncio
async def test_harness_rollback_cloudflare(full_harness):
    """rollback delegates to cloudflare_client for cloudflare deployments."""
    full_harness._deployment_metadata["cf-rollback-id"] = {
        "platform": "cloudflare",
        "project": "my-cf-rollback-app",
    }

    with patch.object(full_harness.cloudflare_client, "rollback") as mock_rb:
        mock_rb.return_value = True

        result = await full_harness.rollback("cf-rollback-id")
        assert result is True
        mock_rb.assert_called_once_with("my-cf-rollback-app", "cf-rollback-id")


# ============================================================================
# 24. DeploymentHarness - deploy_to_railway missing project_id raises ValueError
# ============================================================================


@pytest.mark.asyncio
async def test_harness_deploy_railway_missing_project_id(full_harness):
    """deploy_to_railway raises ValueError when project_id is missing."""
    with pytest.raises(ValueError, match="project_id and service_id required"):
        await full_harness.deploy_to_railway(
            project="my-project",
            service="api",
            project_id=None,
            service_id="svc-123",
        )


@pytest.mark.asyncio
async def test_harness_deploy_railway_missing_service_id(full_harness):
    """deploy_to_railway raises ValueError when service_id is missing."""
    with pytest.raises(ValueError, match="project_id and service_id required"):
        await full_harness.deploy_to_railway(
            project="my-project",
            service="api",
            project_id="proj-123",
            service_id=None,
        )


# ============================================================================
# 25. DeploymentHarness - metadata is stored after successful deployment
# ============================================================================


@pytest.mark.asyncio
async def test_harness_metadata_stored_after_railway_deploy(full_harness):
    """deploy_to_railway stores metadata keyed by deployment_id."""
    with patch.object(full_harness.railway_client, "deploy_service") as mock_deploy:
        mock_deploy.return_value = DeploymentResult(
            deployment_id="meta-deploy-123",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.BUILDING,
            started_at=datetime.now(),
        )

        await full_harness.deploy_to_railway(
            project="meta-proj",
            service="svc",
            project_id="pid",
            service_id="sid",
        )

    meta = full_harness._deployment_metadata.get("meta-deploy-123")
    assert meta is not None
    assert meta["platform"] == "railway"
    assert meta["project"] == "meta-proj"
    assert meta["service"] == "svc"
    assert meta["project_id"] == "pid"
    assert meta["service_id"] == "sid"


@pytest.mark.asyncio
async def test_harness_metadata_stored_after_cloudflare_deploy(full_harness, tmp_path):
    """deploy_to_cloudflare stores metadata keyed by deployment_id."""
    build_dir = tmp_path / "dist"
    build_dir.mkdir()

    with patch.object(full_harness.cloudflare_client, "deploy_pages") as mock_deploy:
        mock_deploy.return_value = DeploymentResult(
            deployment_id="cf-meta-456",
            platform=DeploymentPlatform.CLOUDFLARE,
            status=DeploymentStatus.PENDING,
            started_at=datetime.now(),
        )

        await full_harness.deploy_to_cloudflare(project="cf-meta-proj", directory=build_dir)

    meta = full_harness._deployment_metadata.get("cf-meta-456")
    assert meta is not None
    assert meta["platform"] == "cloudflare"
    assert meta["project"] == "cf-meta-proj"


# ============================================================================
# 26. DeploymentHarness - wait_for_completion terminal states
# ============================================================================


@pytest.mark.asyncio
async def test_harness_wait_for_completion_failed_terminal(full_harness):
    """wait_for_completion returns immediately on FAILED status."""
    full_harness._deployment_metadata["deploy-fail"] = {
        "platform": "railway",
        "project": "p",
        "service": "s",
        "project_id": "pid",
        "service_id": "sid",
    }

    with patch.object(full_harness.railway_client, "get_deployment_status") as mock_status:
        mock_status.return_value = DeploymentResult(
            deployment_id="deploy-fail",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.FAILED,
            error_message="Build failed",
            started_at=datetime.now(),
        )

        result = await full_harness.wait_for_completion(
            "deploy-fail", timeout_seconds=30, poll_interval_seconds=0.01
        )

    assert result.status == DeploymentStatus.FAILED
    assert mock_status.call_count == 1


@pytest.mark.asyncio
async def test_harness_wait_for_completion_cancelled_terminal(full_harness):
    """wait_for_completion returns immediately on CANCELLED status."""
    full_harness._deployment_metadata["deploy-cancel"] = {
        "platform": "railway",
        "project": "p",
        "service": "s",
        "project_id": "pid",
        "service_id": "sid",
    }

    with patch.object(full_harness.railway_client, "get_deployment_status") as mock_status:
        mock_status.return_value = DeploymentResult(
            deployment_id="deploy-cancel",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.CANCELLED,
            started_at=datetime.now(),
        )

        result = await full_harness.wait_for_completion(
            "deploy-cancel", timeout_seconds=30, poll_interval_seconds=0.01
        )

    assert result.status == DeploymentStatus.CANCELLED
    assert mock_status.call_count == 1


@pytest.mark.asyncio
async def test_harness_wait_for_completion_rolled_back_terminal(full_harness):
    """wait_for_completion returns immediately on ROLLED_BACK status."""
    full_harness._deployment_metadata["deploy-rb"] = {
        "platform": "railway",
        "project": "p",
        "service": "s",
        "project_id": "pid",
        "service_id": "sid",
    }

    with patch.object(full_harness.railway_client, "get_deployment_status") as mock_status:
        mock_status.return_value = DeploymentResult(
            deployment_id="deploy-rb",
            platform=DeploymentPlatform.RAILWAY,
            status=DeploymentStatus.ROLLED_BACK,
            started_at=datetime.now(),
        )

        result = await full_harness.wait_for_completion(
            "deploy-rb", timeout_seconds=30, poll_interval_seconds=0.01
        )

    assert result.status == DeploymentStatus.ROLLED_BACK
    assert mock_status.call_count == 1


@pytest.mark.asyncio
async def test_harness_wait_for_completion_dry_run_immediate(dry_harness):
    """wait_for_completion on a dry-run ID resolves immediately as SUCCESS."""
    # Dry-run deploy first so we can wait on it
    result = await dry_harness.deploy_to_railway(
        project="wfc-proj", service="wfc-svc"
    )
    deployment_id = result.deployment_id

    # wait_for_completion should return on first poll (dry-run prefix detected)
    final = await dry_harness.wait_for_completion(
        deployment_id, timeout_seconds=5, poll_interval_seconds=0.01
    )
    assert final.status == DeploymentStatus.SUCCESS


# ============================================================================
# 27. create_deployment_harness - explicit params override env vars
# ============================================================================


def test_create_deployment_harness_explicit_overrides_env():
    """Explicit token params take priority over environment variables."""
    with patch.dict(
        "os.environ",
        {
            "RAILWAY_TOKEN": "env_railway",
            "CLOUDFLARE_API_TOKEN": "env_cf",
            "CLOUDFLARE_ACCOUNT_ID": "env_cf_acct",
        },
    ):
        harness = create_deployment_harness(
            railway_token="explicit_railway",
            cloudflare_token="explicit_cf",
            cloudflare_account_id="explicit_acct",
        )

    # The explicit token should win (RailwayClient stores it as api_token)
    assert harness.railway_client is not None
    assert harness.railway_client.api_token == "explicit_railway"
    assert harness.cloudflare_client is not None
    assert harness.cloudflare_client.api_token == "explicit_cf"
    assert harness.cloudflare_client.account_id == "explicit_acct"


# ============================================================================
# 28. create_deployment_harness - no env vars, no explicit params
# ============================================================================


def test_create_deployment_harness_no_credentials():
    """With no tokens provided and no env vars, both clients are None."""
    with patch.dict("os.environ", {}, clear=True):
        harness = create_deployment_harness()

    assert harness.railway_client is None
    assert harness.cloudflare_client is None
    assert harness.dry_run is False


# ============================================================================
# 29. create_deployment_harness - cloudflare partial credentials (token only)
# ============================================================================


def test_create_deployment_harness_cloudflare_token_only():
    """Cloudflare client requires both token AND account_id; partial config leaves it None."""
    with patch.dict("os.environ", {}, clear=True):
        harness = create_deployment_harness(
            cloudflare_token="token_only"
            # cloudflare_account_id intentionally omitted
        )

    assert harness.cloudflare_client is None


# ============================================================================
# 30. DeploymentHarness - initialisation with only cloudflare credentials
# ============================================================================


def test_harness_init_cloudflare_only():
    """DeploymentHarness builds correctly with only Cloudflare credentials."""
    harness = DeploymentHarness(
        cloudflare_token="cf_tok",
        cloudflare_account_id="cf_acct",
    )
    assert harness.railway_client is None
    assert harness.cloudflare_client is not None
    assert harness.cloudflare_client.api_token == "cf_tok"
    assert harness.cloudflare_client.account_id == "cf_acct"


# ============================================================================
# 31. RailwayClient - deploy_service with env vars triggers _update_env_vars
# ============================================================================


@pytest.mark.asyncio
async def test_railway_deploy_calls_update_env_vars_when_provided(railway_client):
    """deploy_service calls _update_env_vars when env_vars argument is given."""
    with (
        patch.object(railway_client, "_update_env_vars", new_callable=AsyncMock) as mock_update,
        patch("aiohttp.ClientSession.post") as mock_post,
    ):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "data": {
                    "serviceDeploy": {
                        "id": "deploy-env-test",
                        "status": "BUILDING",
                        "createdAt": "2026-02-01T10:00:00Z",
                        "url": None,
                    }
                }
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        env_vars = {"DB_URL": "postgres://localhost/db"}
        await railway_client.deploy_service("proj-x", "svc-y", env_vars)

        mock_update.assert_called_once_with("svc-y", env_vars)


@pytest.mark.asyncio
async def test_railway_deploy_skips_update_env_vars_when_none(railway_client):
    """deploy_service does NOT call _update_env_vars when env_vars is None."""
    with (
        patch.object(railway_client, "_update_env_vars", new_callable=AsyncMock) as mock_update,
        patch("aiohttp.ClientSession.post") as mock_post,
    ):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "data": {
                    "serviceDeploy": {
                        "id": "deploy-no-env",
                        "status": "BUILDING",
                        "createdAt": "2026-02-01T10:00:00Z",
                        "url": None,
                    }
                }
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        await railway_client.deploy_service("proj-x", "svc-y", None)

        mock_update.assert_not_called()


# ============================================================================
# 32. DeploymentHarness - get_deployment_status missing railway client
# ============================================================================


@pytest.mark.asyncio
async def test_harness_get_status_railway_client_missing():
    """get_deployment_status raises ValueError when Railway client not present."""
    harness = DeploymentHarness(cloudflare_token="cf", cloudflare_account_id="acct")
    harness._deployment_metadata["rw-id"] = {
        "platform": "railway",
        "project": "p",
        "service": "s",
    }

    with pytest.raises(ValueError, match="Railway client not configured"):
        await harness.get_deployment_status("rw-id")


@pytest.mark.asyncio
async def test_harness_get_status_cloudflare_client_missing():
    """get_deployment_status raises ValueError when Cloudflare client not present."""
    harness = DeploymentHarness(railway_token="rw")
    harness._deployment_metadata["cf-id"] = {
        "platform": "cloudflare",
        "project": "p",
    }

    with pytest.raises(ValueError, match="Cloudflare client not configured"):
        await harness.get_deployment_status("cf-id")


# ============================================================================
# 33. DeploymentHarness - rollback missing clients
# ============================================================================


@pytest.mark.asyncio
async def test_harness_rollback_railway_client_missing():
    """rollback raises ValueError when Railway client not present."""
    harness = DeploymentHarness(cloudflare_token="cf", cloudflare_account_id="acct")
    harness._deployment_metadata["rw-rb"] = {"platform": "railway"}

    with pytest.raises(ValueError, match="Railway client not configured"):
        await harness.rollback("rw-rb")


@pytest.mark.asyncio
async def test_harness_rollback_cloudflare_client_missing():
    """rollback raises ValueError when Cloudflare client not present."""
    harness = DeploymentHarness(railway_token="rw")
    harness._deployment_metadata["cf-rb"] = {"platform": "cloudflare", "project": "p"}

    with pytest.raises(ValueError, match="Cloudflare client not configured"):
        await harness.rollback("cf-rb")
