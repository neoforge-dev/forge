"""
FORGE Railway Deployment Provider
==================================

Deployment provider for Railway (https://railway.app).
Handles backend service deployments.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

from .base import (
    DeploymentConfig,
    DeploymentProvider,
    DeploymentResult,
    DeploymentTarget,
    ProviderCapabilities,
)


class RailwayProvider(DeploymentProvider):
    """
    Railway deployment provider.

    Deploys backend services to Railway using the CLI.

    Environment Variables:
        RAILWAY_TOKEN: Railway API token (required)
        RAILWAY_PROJECT_ID: Project ID (optional, uses linked project if not set)

    Usage:
        provider = RailwayProvider()
        if provider.is_configured():
            result = provider.deploy(config)
    """

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return Railway provider capabilities."""
        return ProviderCapabilities(
            name="railway",
            targets=[DeploymentTarget.BACKEND],
            supports_rollback=True,
            supports_preview=True,
            supports_health_check=True,
            required_env_vars=["RAILWAY_TOKEN"],
            optional_env_vars=["RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"],
        )

    def deploy(self, config: DeploymentConfig) -> DeploymentResult:
        """
        Deploy backend to Railway.

        Args:
            config: Deployment configuration

        Returns:
            DeploymentResult with success status and deployment URL
        """
        token = os.environ.get("RAILWAY_TOKEN")
        if not token:
            return DeploymentResult(
                success=False,
                provider="railway",
                target=config.target.value,
                error="RAILWAY_TOKEN not configured",
            )

        backend_dir = config.backend_dir
        if not backend_dir.exists():
            return DeploymentResult(
                success=False,
                provider="railway",
                target=config.target.value,
                error=f"Backend directory not found: {backend_dir}",
            )

        # Dry run mode
        if config.dry_run:
            return DeploymentResult(
                success=True,
                provider="railway",
                target=config.target.value,
                url="https://dry-run.railway.app",
                logs="[DRY RUN] Would execute: railway up --detach",
                metadata={"dry_run": True},
            )

        start_time = time.time()

        # Build deploy command
        cmd = ["railway", "up", "--detach"]

        # Add service if specified
        service_id = config.provider_options.get("service_id") or os.environ.get(
            "RAILWAY_SERVICE_ID"
        )
        if service_id:
            cmd.extend(["--service", service_id])

        env = {"RAILWAY_TOKEN": token}
        env.update(config.extra_env)

        try:
            result = self._run_command(
                cmd,
                cwd=backend_dir,
                env=env,
                timeout=config.timeout,
            )
        except Exception as e:
            return DeploymentResult(
                success=False,
                provider="railway",
                target=config.target.value,
                duration_seconds=time.time() - start_time,
                error=str(e),
            )

        duration = time.time() - start_time

        if result.returncode != 0:
            return DeploymentResult(
                success=False,
                provider="railway",
                target=config.target.value,
                duration_seconds=duration,
                error=result.stderr,
                logs=result.stdout,
            )

        # Extract deployment URL from output
        url = self._extract_url(result.stdout)

        return DeploymentResult(
            success=True,
            provider="railway",
            target=config.target.value,
            url=url,
            duration_seconds=duration,
            logs=result.stdout,
        )

    def verify(self, url: str, timeout: int = 30) -> dict[str, Any]:
        """
        Verify Railway deployment is healthy.

        Checks the /health endpoint.

        Args:
            url: Deployed application URL
            timeout: Health check timeout in seconds

        Returns:
            Dict with verification results
        """
        health_url = f"{url.rstrip('/')}/health"
        try:
            resp = httpx.get(health_url, timeout=timeout, follow_redirects=True)
            return {
                "healthy": resp.status_code == 200,
                "status_code": resp.status_code,
                "url": url,
                "error": None,
            }
        except Exception as e:
            return {
                "healthy": False,
                "status_code": None,
                "url": url,
                "error": str(e),
            }

    def rollback(self, config: DeploymentConfig, version: str | None = None) -> DeploymentResult:
        """
        Roll back Railway deployment.

        Uses `railway rollback` command.

        Args:
            config: Deployment configuration
            version: Specific deployment ID to roll back to

        Returns:
            DeploymentResult with rollback status
        """
        token = os.environ.get("RAILWAY_TOKEN")
        if not token:
            return DeploymentResult(
                success=False,
                provider="railway",
                target=config.target.value,
                error="RAILWAY_TOKEN not configured",
            )

        cmd = ["railway", "rollback"]
        if version:
            cmd.append(version)

        start_time = time.time()

        try:
            result = self._run_command(
                cmd,
                cwd=config.backend_dir,
                env={"RAILWAY_TOKEN": token},
                timeout=config.timeout,
            )
        except Exception as e:
            return DeploymentResult(
                success=False,
                provider="railway",
                target=config.target.value,
                duration_seconds=time.time() - start_time,
                error=str(e),
            )

        duration = time.time() - start_time

        return DeploymentResult(
            success=result.returncode == 0,
            provider="railway",
            target=config.target.value,
            duration_seconds=duration,
            error=result.stderr if result.returncode != 0 else None,
            logs=result.stdout,
        )

    def get_status(self, config: DeploymentConfig) -> dict[str, Any]:
        """
        Get Railway deployment status.

        Uses `railway status` command.

        Args:
            config: Deployment configuration

        Returns:
            Dict with deployment status
        """
        token = os.environ.get("RAILWAY_TOKEN")
        if not token:
            return {
                "status": "error",
                "provider": "railway",
                "message": "RAILWAY_TOKEN not configured",
            }

        try:
            result = self._run_command(
                ["railway", "status"],
                cwd=config.backend_dir,
                env={"RAILWAY_TOKEN": token},
                timeout=30,
            )
        except Exception as e:
            return {
                "status": "error",
                "provider": "railway",
                "message": str(e),
            }

        if result.returncode == 0:
            return {
                "status": "ok",
                "provider": "railway",
                "output": result.stdout,
            }
        else:
            return {
                "status": "error",
                "provider": "railway",
                "message": result.stderr,
            }

    def _extract_url(self, output: str) -> str | None:
        """
        Extract deployment URL from Railway CLI output.

        Args:
            output: CLI stdout

        Returns:
            Deployment URL or None
        """
        for line in output.split("\n"):
            if "https://" in line and "railway" in line.lower():
                url_match = re.search(r"(https://[^\s]+)", line)
                if url_match:
                    return url_match.group(1)
        return None
