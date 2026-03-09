"""
FORGE Cloudflare Pages Deployment Provider
===========================================

Deployment provider for Cloudflare Pages.
Handles frontend/static site deployments.
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


class CloudflareProvider(DeploymentProvider):
    """
    Cloudflare Pages deployment provider.

    Deploys frontend applications to Cloudflare Pages using Wrangler CLI.

    Environment Variables:
        CLOUDFLARE_API_TOKEN: Cloudflare API token (required)
        CLOUDFLARE_ACCOUNT_ID: Cloudflare account ID (required)

    Usage:
        provider = CloudflareProvider()
        if provider.is_configured():
            result = provider.deploy(config)
    """

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return Cloudflare provider capabilities."""
        return ProviderCapabilities(
            name="cloudflare",
            targets=[DeploymentTarget.FRONTEND],
            supports_rollback=False,  # Cloudflare Pages doesn't have CLI rollback
            supports_preview=True,
            supports_health_check=True,
            required_env_vars=["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"],
            optional_env_vars=["CLOUDFLARE_PROJECT_NAME"],
        )

    def deploy(self, config: DeploymentConfig) -> DeploymentResult:
        """
        Deploy frontend to Cloudflare Pages.

        Args:
            config: Deployment configuration

        Returns:
            DeploymentResult with success status and deployment URL
        """
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")

        if not token or not account_id:
            return DeploymentResult(
                success=False,
                provider="cloudflare",
                target=config.target.value,
                error="Cloudflare credentials not configured (CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID)",
            )

        frontend_dir = config.frontend_dir
        if not frontend_dir.exists():
            return DeploymentResult(
                success=False,
                provider="cloudflare",
                target=config.target.value,
                error=f"Frontend directory not found: {frontend_dir}",
            )

        # Dry run mode
        if config.dry_run:
            project_name = self._get_project_name(config)
            return DeploymentResult(
                success=True,
                provider="cloudflare",
                target=config.target.value,
                url=f"https://{project_name}.pages.dev",
                logs=f"[DRY RUN] Would execute: wrangler pages deploy dist --project-name={project_name}",
                metadata={"dry_run": True, "project_name": project_name},
            )

        start_time = time.time()

        # Build frontend if dist/ doesn't exist
        dist_dir = frontend_dir / "dist"
        if not dist_dir.exists():
            build_result = self._build_frontend(frontend_dir, config.timeout)
            if not build_result["success"]:
                return DeploymentResult(
                    success=False,
                    provider="cloudflare",
                    target=config.target.value,
                    duration_seconds=time.time() - start_time,
                    error=f"Build failed: {build_result['error']}",
                    logs=build_result.get("logs", ""),
                )

        # Deploy with wrangler
        project_name = self._get_project_name(config)
        branch = config.provider_options.get("branch", "main")

        cmd = [
            "wrangler",
            "pages",
            "deploy",
            "dist",
            f"--project-name={project_name}",
            f"--branch={branch}",
        ]

        env = {
            "CLOUDFLARE_API_TOKEN": token,
            "CLOUDFLARE_ACCOUNT_ID": account_id,
        }
        env.update(config.extra_env)

        try:
            result = self._run_command(
                cmd,
                cwd=frontend_dir,
                env=env,
                timeout=config.timeout,
            )
        except Exception as e:
            return DeploymentResult(
                success=False,
                provider="cloudflare",
                target=config.target.value,
                duration_seconds=time.time() - start_time,
                error=str(e),
            )

        duration = time.time() - start_time

        if result.returncode != 0:
            return DeploymentResult(
                success=False,
                provider="cloudflare",
                target=config.target.value,
                duration_seconds=duration,
                error=result.stderr,
                logs=result.stdout,
            )

        # Extract deployment URL
        url = self._extract_url(result.stdout)

        return DeploymentResult(
            success=True,
            provider="cloudflare",
            target=config.target.value,
            url=url,
            duration_seconds=duration,
            logs=result.stdout,
            metadata={"project_name": project_name, "branch": branch},
        )

    def verify(self, url: str, timeout: int = 30) -> dict[str, Any]:
        """
        Verify Cloudflare Pages deployment is healthy.

        Checks that the frontend loads and returns HTML.

        Args:
            url: Deployed application URL
            timeout: Health check timeout in seconds

        Returns:
            Dict with verification results
        """
        try:
            resp = httpx.get(url, timeout=timeout, follow_redirects=True)
            is_html = "<!DOCTYPE html>" in resp.text or "<html" in resp.text.lower()
            return {
                "healthy": resp.status_code == 200 and is_html,
                "status_code": resp.status_code,
                "url": url,
                "has_html": is_html,
                "error": None,
            }
        except Exception as e:
            return {
                "healthy": False,
                "status_code": None,
                "url": url,
                "error": str(e),
            }

    def get_status(self, config: DeploymentConfig) -> dict[str, Any]:
        """
        Get Cloudflare Pages deployment status.

        Uses `wrangler pages deployment list` command.

        Args:
            config: Deployment configuration

        Returns:
            Dict with deployment status
        """
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")

        if not token or not account_id:
            return {
                "status": "error",
                "provider": "cloudflare",
                "message": "Cloudflare credentials not configured",
            }

        project_name = self._get_project_name(config)

        try:
            result = self._run_command(
                ["wrangler", "pages", "deployment", "list", "--project-name", project_name],
                cwd=config.frontend_dir,
                env={
                    "CLOUDFLARE_API_TOKEN": token,
                    "CLOUDFLARE_ACCOUNT_ID": account_id,
                },
                timeout=30,
            )
        except Exception as e:
            return {
                "status": "error",
                "provider": "cloudflare",
                "message": str(e),
            }

        if result.returncode == 0:
            return {
                "status": "ok",
                "provider": "cloudflare",
                "project_name": project_name,
                "output": result.stdout,
            }
        else:
            return {
                "status": "error",
                "provider": "cloudflare",
                "message": result.stderr,
            }

    def _get_project_name(self, config: DeploymentConfig) -> str:
        """
        Get Cloudflare Pages project name.

        Priority:
        1. provider_options["project_name"]
        2. CLOUDFLARE_PROJECT_NAME env var
        3. Generated from project name

        Args:
            config: Deployment configuration

        Returns:
            Project name for Cloudflare Pages
        """
        return (
            config.provider_options.get("project_name")
            or os.environ.get("CLOUDFLARE_PROJECT_NAME")
            or f"{config.project.replace('-', '')}-app"
        )

    def _build_frontend(self, frontend_dir, timeout: int) -> dict[str, Any]:
        """
        Build frontend application.

        Args:
            frontend_dir: Path to frontend directory
            timeout: Build timeout in seconds

        Returns:
            Dict with build result
        """
        try:
            result = self._run_command(
                ["npm", "run", "build"],
                cwd=frontend_dir,
                timeout=timeout,
            )
            return {
                "success": result.returncode == 0,
                "error": result.stderr if result.returncode != 0 else None,
                "logs": result.stdout,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "logs": "",
            }

    def _extract_url(self, output: str) -> str | None:
        """
        Extract deployment URL from Wrangler output.

        Args:
            output: CLI stdout

        Returns:
            Deployment URL or None
        """
        for line in output.split("\n"):
            if "https://" in line:
                url_match = re.search(r"(https://[^\s]+\.pages\.dev[^\s]*)", line)
                if url_match:
                    return url_match.group(1)
        return None
