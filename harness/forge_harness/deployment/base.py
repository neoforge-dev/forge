"""
FORGE Deployment Provider Base
==============================

Abstract base classes and shared types for deployment providers.
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import ForgeSettings


class DeploymentTarget(Enum):
    """Supported deployment targets."""

    BACKEND = "backend"
    FRONTEND = "frontend"
    FULL_STACK = "full_stack"


@dataclass
class ProviderCapabilities:
    """Describes what a deployment provider can do."""

    name: str
    targets: list[DeploymentTarget]
    supports_rollback: bool = False
    supports_preview: bool = False
    supports_health_check: bool = True
    required_env_vars: list[str] = field(default_factory=list)
    optional_env_vars: list[str] = field(default_factory=list)


@dataclass
class DeploymentConfig:
    """Configuration for a deployment operation."""

    domain: str
    project: str
    project_dir: Path
    target: DeploymentTarget = DeploymentTarget.BACKEND
    dry_run: bool = False
    timeout: int = 300
    extra_env: dict[str, str] = field(default_factory=dict)

    # Provider-specific settings
    provider_options: dict[str, Any] = field(default_factory=dict)

    @property
    def backend_dir(self) -> Path:
        """Path to backend directory."""
        return self.project_dir / "backend"

    @property
    def frontend_dir(self) -> Path:
        """Path to frontend directory."""
        return self.project_dir / "frontend"

    @classmethod
    def from_settings(
        cls,
        settings: ForgeSettings,
        target: DeploymentTarget = DeploymentTarget.BACKEND,
    ) -> DeploymentConfig:
        """Create config from ForgeSettings."""
        return cls(
            domain=settings.domain or "",
            project=settings.project or "",
            project_dir=Path(settings.forge_root or "."),
            target=target,
            dry_run=settings.dry_run,
        )


@dataclass
class DeploymentResult:
    """Result of a deployment operation."""

    success: bool
    provider: str
    target: str
    url: str | None = None
    duration_seconds: float = 0.0
    error: str | None = None
    logs: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """Generate human-readable summary."""
        status = "SUCCESS" if self.success else "FAILED"
        url_info = f" -> {self.url}" if self.url else ""
        return f"[{self.provider}] {status}{url_info} ({self.duration_seconds:.1f}s)"


class DeploymentProvider(ABC):
    """
    Abstract base class for deployment providers.

    Subclasses must implement:
    - capabilities: Describe provider's capabilities
    - deploy(): Execute deployment
    - verify(): Verify deployment health

    Optional overrides:
    - rollback(): Roll back to previous version
    - get_status(): Get current deployment status
    """

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Return provider capabilities."""
        ...

    @abstractmethod
    def deploy(self, config: DeploymentConfig) -> DeploymentResult:
        """
        Execute deployment.

        Args:
            config: Deployment configuration

        Returns:
            DeploymentResult with success status and details
        """
        ...

    @abstractmethod
    def verify(self, url: str, timeout: int = 30) -> dict[str, Any]:
        """
        Verify deployment is healthy.

        Args:
            url: Deployed application URL
            timeout: Health check timeout in seconds

        Returns:
            Dict with verification results
        """
        ...

    def rollback(self, config: DeploymentConfig, version: str | None = None) -> DeploymentResult:
        """
        Roll back to previous version.

        Default implementation returns error if not supported.

        Args:
            config: Deployment configuration
            version: Specific version to roll back to (optional)

        Returns:
            DeploymentResult with rollback status
        """
        return DeploymentResult(
            success=False,
            provider=self.capabilities.name,
            target=config.target.value,
            error="Rollback not supported by this provider",
        )

    def get_status(self, config: DeploymentConfig) -> dict[str, Any]:
        """
        Get current deployment status.

        Default implementation returns unknown status.

        Args:
            config: Deployment configuration

        Returns:
            Dict with deployment status
        """
        return {
            "status": "unknown",
            "provider": self.capabilities.name,
            "message": "Status check not implemented",
        }

    def is_configured(self) -> bool:
        """
        Check if provider has required configuration.

        Returns:
            True if all required env vars are set
        """
        for var in self.capabilities.required_env_vars:
            if not os.environ.get(var):
                return False
        return True

    def missing_config(self) -> list[str]:
        """
        Get list of missing required configuration.

        Returns:
            List of missing environment variable names
        """
        return [var for var in self.capabilities.required_env_vars if not os.environ.get(var)]

    def _run_command(
        self,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess:
        """
        Run a shell command with proper environment.

        Args:
            cmd: Command and arguments
            cwd: Working directory
            env: Additional environment variables
            timeout: Command timeout in seconds

        Returns:
            CompletedProcess with stdout/stderr
        """
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
