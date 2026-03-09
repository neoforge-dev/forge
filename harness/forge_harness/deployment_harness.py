"""
Deployment Harness - Railway and Cloudflare Pages Integration
==============================================================

Handles deployments to Railway (backend services) and Cloudflare Pages
(static frontends) with status tracking and rollback capabilities.

Railway API:
    GraphQL API for service deployments, environment variables, and monitoring.
    Docs: https://docs.railway.app/reference/public-api

Cloudflare Pages API:
    REST API for static site deployments.
    Docs: https://developers.cloudflare.com/pages/platform/api/

Usage:
    from forge_harness.deployment_harness import create_deployment_harness

    harness = create_deployment_harness()

    # Deploy backend to Railway
    result = await harness.deploy_to_railway(
        project="my-project",
        service="backend",
        env_vars={"DATABASE_URL": "postgres://..."},
    )

    # Deploy frontend to Cloudflare Pages
    result = await harness.deploy_to_cloudflare(
        project="my-project",
        directory="frontend/dist",
    )

    # Check deployment status
    status = await harness.get_deployment_status(result.deployment_id)

    # Rollback if needed
    await harness.rollback(result.deployment_id)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import aiohttp

from .logging_config import get_logger

logger = get_logger(__name__)


class DeploymentStatus(str, Enum):
    """Deployment status states."""

    PENDING = "pending"
    BUILDING = "building"
    DEPLOYING = "deploying"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


class DeploymentPlatform(str, Enum):
    """Deployment platform types."""

    RAILWAY = "railway"
    CLOUDFLARE = "cloudflare"
    UNKNOWN = "unknown"


@dataclass
class DeploymentResult:
    """Result of a deployment operation.

    Attributes:
        deployment_id: Unique deployment identifier
        platform: Platform where deployed (Railway/Cloudflare)
        status: Current deployment status
        url: Public URL of deployed service (if available)
        build_logs_url: URL to view build logs
        started_at: When deployment started
        completed_at: When deployment completed (None if still in progress)
        error_message: Error details if deployment failed
        metadata: Additional platform-specific data
    """

    deployment_id: str
    platform: DeploymentPlatform
    status: DeploymentStatus
    url: str | None = None
    build_logs_url: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    metadata: dict[str, Any] | None = None


class RailwayClient:
    """Client for Railway GraphQL API.

    Railway uses GraphQL for all API interactions. This client handles
    authentication and provides methods for common deployment operations.
    """

    RAILWAY_API_URL = "https://backboard.railway.app/graphql/v2"

    def __init__(self, api_token: str):
        """Initialize Railway client.

        Args:
            api_token: Railway API token (from RAILWAY_TOKEN env var)
        """
        self.api_token = api_token
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    async def deploy_service(
        self,
        project_id: str,
        service_id: str,
        env_vars: dict[str, str] | None = None,
    ) -> DeploymentResult:
        """Deploy a service to Railway.

        Args:
            project_id: Railway project ID
            service_id: Railway service ID
            env_vars: Environment variables to set

        Returns:
            DeploymentResult with deployment details

        Raises:
            aiohttp.ClientError: If API request fails
        """
        logger.info(f"Deploying Railway service: {service_id}")

        # Update environment variables if provided
        if env_vars:
            await self._update_env_vars(service_id, env_vars)

        # Trigger deployment via serviceDeploy mutation
        mutation = """
        mutation ServiceDeploy($serviceId: String!) {
            serviceDeploy(serviceId: $serviceId) {
                id
                status
                createdAt
                url
            }
        }
        """

        variables = {"serviceId": service_id}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.RAILWAY_API_URL,
                json={"query": mutation, "variables": variables},
                headers=self.headers,
            ) as response:
                response.raise_for_status()
                data = await response.json()

                if "errors" in data:
                    error_msg = data["errors"][0]["message"]
                    logger.error(f"Railway deployment failed: {error_msg}")
                    return DeploymentResult(
                        deployment_id=service_id,
                        platform=DeploymentPlatform.RAILWAY,
                        status=DeploymentStatus.FAILED,
                        error_message=error_msg,
                        started_at=datetime.now(),
                    )

                deployment = data["data"]["serviceDeploy"]
                return DeploymentResult(
                    deployment_id=deployment["id"],
                    platform=DeploymentPlatform.RAILWAY,
                    status=self._map_railway_status(deployment.get("status", "BUILDING")),
                    url=deployment.get("url"),
                    started_at=datetime.fromisoformat(deployment["createdAt"]),
                    metadata={"project_id": project_id, "service_id": service_id},
                )

    async def get_deployment_status(self, deployment_id: str) -> DeploymentResult:
        """Get deployment status from Railway.

        Args:
            deployment_id: Railway deployment ID

        Returns:
            DeploymentResult with current status
        """
        query = """
        query Deployment($deploymentId: String!) {
            deployment(id: $deploymentId) {
                id
                status
                createdAt
                completedAt
                url
            }
        }
        """

        variables = {"deploymentId": deployment_id}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.RAILWAY_API_URL,
                json={"query": query, "variables": variables},
                headers=self.headers,
            ) as response:
                response.raise_for_status()
                data = await response.json()

                if "errors" in data:
                    error_msg = data["errors"][0]["message"]
                    return DeploymentResult(
                        deployment_id=deployment_id,
                        platform=DeploymentPlatform.RAILWAY,
                        status=DeploymentStatus.FAILED,
                        error_message=error_msg,
                    )

                deployment = data["data"]["deployment"]
                return DeploymentResult(
                    deployment_id=deployment["id"],
                    platform=DeploymentPlatform.RAILWAY,
                    status=self._map_railway_status(deployment.get("status", "BUILDING")),
                    url=deployment.get("url"),
                    started_at=datetime.fromisoformat(deployment["createdAt"]),
                    completed_at=(
                        datetime.fromisoformat(deployment["completedAt"])
                        if deployment.get("completedAt")
                        else None
                    ),
                )

    async def rollback(self, deployment_id: str) -> bool:
        """Rollback to previous deployment.

        Args:
            deployment_id: Current deployment ID to rollback from

        Returns:
            True if rollback succeeded
        """
        logger.info(f"Rolling back Railway deployment: {deployment_id}")

        # Railway rollback via deploymentRollback mutation
        mutation = """
        mutation DeploymentRollback($deploymentId: String!) {
            deploymentRollback(deploymentId: $deploymentId) {
                id
                status
            }
        }
        """

        variables = {"deploymentId": deployment_id}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.RAILWAY_API_URL,
                json={"query": mutation, "variables": variables},
                headers=self.headers,
            ) as response:
                response.raise_for_status()
                data = await response.json()

                if "errors" in data:
                    logger.error(f"Railway rollback failed: {data['errors']}")
                    return False

                logger.info("Railway rollback succeeded")
                return True

    async def _update_env_vars(self, service_id: str, env_vars: dict[str, str]) -> None:
        """Update environment variables for a service.

        Args:
            service_id: Railway service ID
            env_vars: Dictionary of environment variables to set
        """
        mutation = """
        mutation VariableUpsert($serviceId: String!, $name: String!, $value: String!) {
            variableUpsert(serviceId: $serviceId, name: $name, value: $value)
        }
        """

        async with aiohttp.ClientSession() as session:
            for name, value in env_vars.items():
                variables = {
                    "serviceId": service_id,
                    "name": name,
                    "value": value,
                }

                async with session.post(
                    self.RAILWAY_API_URL,
                    json={"query": mutation, "variables": variables},
                    headers=self.headers,
                ) as response:
                    response.raise_for_status()
                    logger.debug(f"Set Railway env var: {name}")

    def _map_railway_status(self, railway_status: str) -> DeploymentStatus:
        """Map Railway status to DeploymentStatus enum.

        Args:
            railway_status: Railway status string

        Returns:
            Corresponding DeploymentStatus
        """
        status_map = {
            "BUILDING": DeploymentStatus.BUILDING,
            "DEPLOYING": DeploymentStatus.DEPLOYING,
            "SUCCESS": DeploymentStatus.SUCCESS,
            "FAILED": DeploymentStatus.FAILED,
            "CANCELLED": DeploymentStatus.CANCELLED,
            "CRASHED": DeploymentStatus.FAILED,
        }
        return status_map.get(railway_status.upper(), DeploymentStatus.PENDING)


class CloudflareClient:
    """Client for Cloudflare Pages API.

    Cloudflare Pages uses a REST API for deployments. This client handles
    direct uploads of built static sites.
    """

    CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"

    def __init__(self, api_token: str, account_id: str):
        """Initialize Cloudflare client.

        Args:
            api_token: Cloudflare API token
            account_id: Cloudflare account ID
        """
        self.api_token = api_token
        self.account_id = account_id
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    async def deploy_pages(self, project_name: str, directory: Path) -> DeploymentResult:
        """Deploy static site to Cloudflare Pages.

        Args:
            project_name: Cloudflare Pages project name
            directory: Path to built static files

        Returns:
            DeploymentResult with deployment details

        Raises:
            aiohttp.ClientError: If API request fails
            FileNotFoundError: If directory doesn't exist
        """
        if not directory.exists():
            raise FileNotFoundError(f"Build directory not found: {directory}")

        logger.info(f"Deploying Cloudflare Pages: {project_name}")

        # Create deployment
        url = f"{self.CLOUDFLARE_API_BASE}/accounts/{self.account_id}/pages/projects/{project_name}/deployments"

        # For now, trigger deployment via Direct Upload API
        # This would need to be expanded to handle file uploads
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers) as response:
                if response.status == 404:
                    # Project doesn't exist, create it first
                    await self._create_project(project_name)
                    # Retry deployment
                    return await self.deploy_pages(project_name, directory)

                response.raise_for_status()
                data = await response.json()

                if not data.get("success"):
                    error_msg = data.get("errors", [{}])[0].get("message", "Unknown error")
                    logger.error(f"Cloudflare deployment failed: {error_msg}")
                    return DeploymentResult(
                        deployment_id=f"{project_name}-{datetime.now().isoformat()}",
                        platform=DeploymentPlatform.CLOUDFLARE,
                        status=DeploymentStatus.FAILED,
                        error_message=error_msg,
                        started_at=datetime.now(),
                    )

                deployment = data["result"]
                return DeploymentResult(
                    deployment_id=deployment["id"],
                    platform=DeploymentPlatform.CLOUDFLARE,
                    status=self._map_cloudflare_status(deployment.get("stage", "queued")),
                    url=deployment.get("url"),
                    build_logs_url=deployment.get("build_config", {}).get("build_logs_url"),
                    started_at=datetime.fromisoformat(deployment["created_on"]),
                    metadata={"project_name": project_name},
                )

    async def get_deployment_status(
        self, project_name: str, deployment_id: str
    ) -> DeploymentResult:
        """Get deployment status from Cloudflare Pages.

        Args:
            project_name: Cloudflare Pages project name
            deployment_id: Deployment ID

        Returns:
            DeploymentResult with current status
        """
        url = f"{self.CLOUDFLARE_API_BASE}/accounts/{self.account_id}/pages/projects/{project_name}/deployments/{deployment_id}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers) as response:
                response.raise_for_status()
                data = await response.json()

                if not data.get("success"):
                    error_msg = data.get("errors", [{}])[0].get("message", "Unknown error")
                    return DeploymentResult(
                        deployment_id=deployment_id,
                        platform=DeploymentPlatform.CLOUDFLARE,
                        status=DeploymentStatus.FAILED,
                        error_message=error_msg,
                    )

                deployment = data["result"]
                return DeploymentResult(
                    deployment_id=deployment["id"],
                    platform=DeploymentPlatform.CLOUDFLARE,
                    status=self._map_cloudflare_status(deployment.get("stage", "queued")),
                    url=deployment.get("url"),
                    build_logs_url=deployment.get("build_config", {}).get("build_logs_url"),
                    started_at=datetime.fromisoformat(deployment["created_on"]),
                    completed_at=(
                        datetime.fromisoformat(deployment["modified_on"])
                        if deployment.get("stage") in ["success", "failure"]
                        else None
                    ),
                )

    async def rollback(self, project_name: str, deployment_id: str) -> bool:
        """Rollback Cloudflare Pages deployment.

        Cloudflare Pages doesn't have native rollback - we need to trigger
        a new deployment from the previous version.

        Args:
            project_name: Cloudflare Pages project name
            deployment_id: Current deployment ID

        Returns:
            True if rollback succeeded
        """
        logger.info(f"Rolling back Cloudflare Pages: {project_name}")

        # Get list of deployments
        url = f"{self.CLOUDFLARE_API_BASE}/accounts/{self.account_id}/pages/projects/{project_name}/deployments"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers) as response:
                response.raise_for_status()
                data = await response.json()

                if not data.get("success"):
                    logger.error("Failed to fetch Cloudflare deployments")
                    return False

                deployments = data["result"]
                # Find previous successful deployment
                previous = None
                for deployment in deployments:
                    if deployment["id"] == deployment_id:
                        continue
                    if deployment.get("stage") == "success":
                        previous = deployment
                        break

                if not previous:
                    logger.error("No previous successful deployment found")
                    return False

                # Trigger retry of previous deployment
                retry_url = f"{url}/{previous['id']}/retry"
                async with session.post(retry_url, headers=self.headers) as retry_response:
                    retry_response.raise_for_status()
                    logger.info("Cloudflare Pages rollback triggered")
                    return True

    async def _create_project(self, project_name: str) -> None:
        """Create a new Cloudflare Pages project.

        Args:
            project_name: Name for the new project
        """
        url = f"{self.CLOUDFLARE_API_BASE}/accounts/{self.account_id}/pages/projects"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={
                    "name": project_name,
                    "production_branch": "main",
                },
                headers=self.headers,
            ) as response:
                response.raise_for_status()
                logger.info(f"Created Cloudflare Pages project: {project_name}")

    def _map_cloudflare_status(self, cloudflare_stage: str) -> DeploymentStatus:
        """Map Cloudflare stage to DeploymentStatus enum.

        Args:
            cloudflare_stage: Cloudflare deployment stage

        Returns:
            Corresponding DeploymentStatus
        """
        status_map = {
            "queued": DeploymentStatus.PENDING,
            "initialize": DeploymentStatus.BUILDING,
            "clone_repo": DeploymentStatus.BUILDING,
            "build": DeploymentStatus.BUILDING,
            "deploy": DeploymentStatus.DEPLOYING,
            "success": DeploymentStatus.SUCCESS,
            "failure": DeploymentStatus.FAILED,
            "cancelled": DeploymentStatus.CANCELLED,
        }
        return status_map.get(cloudflare_stage.lower(), DeploymentStatus.PENDING)


class DeploymentHarness:
    """Unified deployment harness for Railway and Cloudflare Pages.

    Provides a consistent interface for deploying backend services to Railway
    and frontend static sites to Cloudflare Pages, with status monitoring and
    rollback capabilities.

    Example:
        harness = DeploymentHarness(
            railway_token="...",
            cloudflare_token="...",
            cloudflare_account_id="...",
        )

        # Deploy backend
        result = await harness.deploy_to_railway(
            project="my-app",
            service="api",
            env_vars={"API_KEY": "secret"},
        )

        # Deploy frontend
        result = await harness.deploy_to_cloudflare(
            project="my-app",
            directory="dist",
        )
    """

    def __init__(
        self,
        railway_token: str | None = None,
        cloudflare_token: str | None = None,
        cloudflare_account_id: str | None = None,
        dry_run: bool = False,
    ):
        """Initialize deployment harness.

        Args:
            railway_token: Railway API token (optional)
            cloudflare_token: Cloudflare API token (optional)
            cloudflare_account_id: Cloudflare account ID (optional)
            dry_run: If True, simulate deployments without executing
        """
        self.dry_run = dry_run

        # Initialize clients only if credentials provided
        self.railway_client = RailwayClient(railway_token) if railway_token else None
        self.cloudflare_client = (
            CloudflareClient(cloudflare_token, cloudflare_account_id)
            if cloudflare_token and cloudflare_account_id
            else None
        )

        # Track deployment metadata
        self._deployment_metadata: dict[str, dict[str, Any]] = {}

    async def deploy_to_railway(
        self,
        project: str,
        service: str,
        env_vars: dict[str, str] | None = None,
        project_id: str | None = None,
        service_id: str | None = None,
    ) -> DeploymentResult:
        """Deploy a service to Railway.

        Args:
            project: Project name (for logging/tracking)
            service: Service name (for logging/tracking)
            env_vars: Environment variables to set
            project_id: Railway project ID (required if not in dry-run)
            service_id: Railway service ID (required if not in dry-run)

        Returns:
            DeploymentResult with deployment details

        Raises:
            ValueError: If Railway client not configured
            ValueError: If project_id or service_id missing
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would deploy Railway service: {project}/{service}")
            return DeploymentResult(
                deployment_id=f"dry-run-railway-{project}-{service}",
                platform=DeploymentPlatform.RAILWAY,
                status=DeploymentStatus.SUCCESS,
                url=f"https://{service}.railway.app",
                started_at=datetime.now(),
                completed_at=datetime.now(),
            )

        if not self.railway_client:
            raise ValueError("Railway client not configured - missing RAILWAY_TOKEN")

        if not project_id or not service_id:
            raise ValueError("project_id and service_id required for Railway deployment")

        result = await self.railway_client.deploy_service(project_id, service_id, env_vars)

        # Store metadata for rollback
        self._deployment_metadata[result.deployment_id] = {
            "platform": "railway",
            "project": project,
            "service": service,
            "project_id": project_id,
            "service_id": service_id,
        }

        return result

    async def deploy_to_cloudflare(self, project: str, directory: str | Path) -> DeploymentResult:
        """Deploy static site to Cloudflare Pages.

        Args:
            project: Cloudflare Pages project name
            directory: Path to built static files

        Returns:
            DeploymentResult with deployment details

        Raises:
            ValueError: If Cloudflare client not configured
            FileNotFoundError: If directory doesn't exist
        """
        directory_path = Path(directory)

        if self.dry_run:
            logger.info(f"[DRY RUN] Would deploy Cloudflare Pages: {project} from {directory}")
            return DeploymentResult(
                deployment_id=f"dry-run-cloudflare-{project}",
                platform=DeploymentPlatform.CLOUDFLARE,
                status=DeploymentStatus.SUCCESS,
                url=f"https://{project}.pages.dev",
                started_at=datetime.now(),
                completed_at=datetime.now(),
            )

        if not self.cloudflare_client:
            raise ValueError(
                "Cloudflare client not configured - missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID"
            )

        result = await self.cloudflare_client.deploy_pages(project, directory_path)

        # Store metadata for rollback
        self._deployment_metadata[result.deployment_id] = {
            "platform": "cloudflare",
            "project": project,
        }

        return result

    async def get_deployment_status(self, deployment_id: str) -> DeploymentResult:
        """Check deployment status.

        Args:
            deployment_id: Deployment ID from deploy_to_railway or deploy_to_cloudflare

        Returns:
            DeploymentResult with current status

        Raises:
            ValueError: If deployment_id not found in metadata
        """
        if deployment_id.startswith("dry-run-"):
            # Dry run deployment
            return DeploymentResult(
                deployment_id=deployment_id,
                platform=DeploymentPlatform.UNKNOWN,
                status=DeploymentStatus.SUCCESS,
                started_at=datetime.now(),
                completed_at=datetime.now(),
            )

        metadata = self._deployment_metadata.get(deployment_id)
        if not metadata:
            raise ValueError(f"Unknown deployment_id: {deployment_id}")

        platform = metadata["platform"]

        if platform == "railway":
            if not self.railway_client:
                raise ValueError("Railway client not configured")
            return await self.railway_client.get_deployment_status(deployment_id)

        elif platform == "cloudflare":
            if not self.cloudflare_client:
                raise ValueError("Cloudflare client not configured")
            project = metadata["project"]
            return await self.cloudflare_client.get_deployment_status(project, deployment_id)

        else:
            raise ValueError(f"Unknown platform: {platform}")

    async def rollback(self, deployment_id: str) -> bool:
        """Rollback to previous deployment.

        Args:
            deployment_id: Deployment ID to rollback from

        Returns:
            True if rollback succeeded

        Raises:
            ValueError: If deployment_id not found in metadata
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would rollback deployment: {deployment_id}")
            return True

        metadata = self._deployment_metadata.get(deployment_id)
        if not metadata:
            raise ValueError(f"Unknown deployment_id: {deployment_id}")

        platform = metadata["platform"]

        if platform == "railway":
            if not self.railway_client:
                raise ValueError("Railway client not configured")
            return await self.railway_client.rollback(deployment_id)

        elif platform == "cloudflare":
            if not self.cloudflare_client:
                raise ValueError("Cloudflare client not configured")
            project = metadata["project"]
            return await self.cloudflare_client.rollback(project, deployment_id)

        else:
            raise ValueError(f"Unknown platform: {platform}")

    async def wait_for_completion(
        self,
        deployment_id: str,
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 5,
    ) -> DeploymentResult:
        """Wait for deployment to complete.

        Args:
            deployment_id: Deployment ID to monitor
            timeout_seconds: Maximum time to wait (default: 10 minutes)
            poll_interval_seconds: How often to poll status (default: 5 seconds)

        Returns:
            Final DeploymentResult

        Raises:
            TimeoutError: If deployment doesn't complete within timeout
        """
        start_time = datetime.now()

        while True:
            result = await self.get_deployment_status(deployment_id)

            # Check for terminal states
            if result.status in (
                DeploymentStatus.SUCCESS,
                DeploymentStatus.FAILED,
                DeploymentStatus.CANCELLED,
                DeploymentStatus.ROLLED_BACK,
            ):
                return result

            # Check timeout
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed > timeout_seconds:
                raise TimeoutError(
                    f"Deployment {deployment_id} did not complete within {timeout_seconds}s"
                )

            # Wait before next poll
            await asyncio.sleep(poll_interval_seconds)


def create_deployment_harness(
    railway_token: str | None = None,
    cloudflare_token: str | None = None,
    cloudflare_account_id: str | None = None,
    dry_run: bool = False,
) -> DeploymentHarness:
    """Create a DeploymentHarness with credentials from environment or parameters.

    Args:
        railway_token: Railway API token (defaults to RAILWAY_TOKEN env var)
        cloudflare_token: Cloudflare API token (defaults to CLOUDFLARE_API_TOKEN env var)
        cloudflare_account_id: Cloudflare account ID (defaults to CLOUDFLARE_ACCOUNT_ID env var)
        dry_run: If True, simulate deployments

    Returns:
        Configured DeploymentHarness
    """
    return DeploymentHarness(
        railway_token=railway_token or os.getenv("RAILWAY_TOKEN"),
        cloudflare_token=cloudflare_token or os.getenv("CLOUDFLARE_API_TOKEN"),
        cloudflare_account_id=cloudflare_account_id or os.getenv("CLOUDFLARE_ACCOUNT_ID"),
        dry_run=dry_run,
    )
