"""
FORGE Deployment Providers
==========================

Pluggable deployment provider system for FORGE projects.

Supported Providers:
- Railway: Backend deployments (Python/Node.js services)
- Cloudflare Pages: Frontend deployments (static sites, SPAs)

Usage:
    from forge_harness.deployment import ProviderRegistry, DeploymentProvider

    # Get a specific provider
    railway = ProviderRegistry.get("railway")
    result = await railway.deploy(config)

    # Or use ForgeDeployer for full pipeline
    from forge_harness.deployment import ForgeDeployer

    deployer = ForgeDeployer(domain="example", project="myapp", project_dir=Path("."))
    results = deployer.full_deploy()
"""

from .base import (
    DeploymentConfig,
    DeploymentProvider,
    DeploymentResult,
    ProviderCapabilities,
)
from .registry import ProviderRegistry

# Re-export from legacy module for backward compatibility
# Optional: httpx may not be installed in minimal environments
try:
    from ..deployment_legacy import (
        ForgeDeployer,
        QualityGateResult,
        SmokeTestResult,
    )

    _LEGACY_AVAILABLE = True
except ImportError:
    # httpx or other dependencies not available
    ForgeDeployer = None  # type: ignore[misc, assignment]
    QualityGateResult = None  # type: ignore[misc, assignment]
    SmokeTestResult = None  # type: ignore[misc, assignment]
    _LEGACY_AVAILABLE = False

__all__ = [
    # New abstracted types
    "DeploymentConfig",
    "DeploymentProvider",
    "DeploymentResult",
    "ProviderCapabilities",
    "ProviderRegistry",
    # Legacy exports for backward compatibility (may be None if httpx unavailable)
    "ForgeDeployer",
    "QualityGateResult",
    "SmokeTestResult",
    "_LEGACY_AVAILABLE",
]
