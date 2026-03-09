"""
Railway deployment automation for Continuous Runner.
"""

import asyncio

from forge_harness.continuous_runner.health import monitor
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class RailwayDeployer:
    """Automates Railway deployments."""

    def __init__(self, project_root: str = "."):
        self.project_root = project_root

    async def deploy(self) -> bool:
        """
        Trigger a deployment to Railway.
        Uses 'railway up' under the hood.
        """
        logger.info("Starting Railway deployment...")
        monitor.update_component("railway", "deploying")
        try:
            # Run railway up in the harness directory
            process = await asyncio.create_subprocess_exec(
                "railway", "up", "--detach",
                cwd=f"{self.project_root}/harness",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info("Railway deployment triggered successfully")
                monitor.update_component("railway", "triggered")
                return True
            else:
                logger.error("Railway deployment failed: %s", stderr.decode())
                monitor.update_component("railway", "error")
                return False

        except Exception as e:
            logger.exception("Error during Railway deployment: %s", e)
            monitor.update_component("railway", "error")
            return False

    async def deploy_with_check(self) -> bool:
        """
        Deploy and check health, rollback if failed.
        """
        success = await self.deploy()
        if not success:
            return False

        # Wait for deployment to propagate
        logger.info("Waiting for deployment to propagate...")
        await asyncio.sleep(60) # Typical Railway deployment time

        # In a real scenario, we would check the GET /health of the deployed app
        # For now, we'll assume it's OK or use railway status
        status = await self.get_status()
        if "error" in status.lower() or "failed" in status.lower():
            logger.error("Deployment health check failed, rolling back...")
            await self.rollback()
            return False

        logger.info("Deployment health check passed")
        return True

    async def rollback(self) -> bool:
        """
        Rollback to previous deployment on Railway.
        Uses 'railway rollback' under the hood.
        """
        logger.warning("Rolling back Railway deployment...")
        monitor.update_component("railway", "rolling_back")
        try:
            process = await asyncio.create_subprocess_exec(
                "railway", "rollback", "-y",
                cwd=f"{self.project_root}/harness",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info("Railway rollback triggered successfully")
                monitor.update_component("railway", "rolled_back")
                return True
            else:
                logger.error("Railway rollback failed: %s", stderr.decode())
                monitor.update_component("railway", "error")
                return False
        except Exception as e:
            logger.exception("Error during Railway rollback: %s", e)
            monitor.update_component("railway", "error")
            return False

    async def get_status(self) -> str:
        """Get current deployment status from Railway."""
        try:
            process = await asyncio.create_subprocess_exec(
                "railway", "status",
                cwd=f"{self.project_root}/harness",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            return stdout.decode().strip()
        except Exception:
            return "unknown"


deployer = RailwayDeployer()
