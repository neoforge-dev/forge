"""
FORGE Deployment Provider Registry
===================================

Auto-discovery and management of deployment providers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DeploymentProvider, DeploymentTarget

if TYPE_CHECKING:
    from .base import ProviderCapabilities


class ProviderRegistry:
    """
    Registry for deployment providers.

    Provides auto-discovery of providers in the deployment package
    and lazy loading of provider instances.

    Usage:
        # Get available providers
        providers = ProviderRegistry.list_providers()

        # Get a specific provider
        railway = ProviderRegistry.get("railway")

        # Get providers for a target
        backend_providers = ProviderRegistry.for_target(DeploymentTarget.BACKEND)

        # Check configured providers
        configured = ProviderRegistry.configured_providers()
    """

    _providers: dict[str, type[DeploymentProvider]] = {}
    _instances: dict[str, DeploymentProvider] = {}
    _discovered: bool = False

    @classmethod
    def discover(cls) -> None:
        """
        Discover and register all providers in the deployment package.

        Scans the deployment package for modules containing DeploymentProvider
        subclasses and registers them.
        """
        if cls._discovered:
            return

        # Import the package
        from . import cloudflare, railway

        # Register built-in providers
        cls.register("railway", railway.RailwayProvider)
        cls.register("cloudflare", cloudflare.CloudflareProvider)

        cls._discovered = True

    @classmethod
    def register(cls, name: str, provider_class: type[DeploymentProvider]) -> None:
        """
        Register a deployment provider.

        Args:
            name: Provider identifier (e.g., "railway", "cloudflare")
            provider_class: DeploymentProvider subclass
        """
        cls._providers[name] = provider_class
        # Clear cached instance if exists
        if name in cls._instances:
            del cls._instances[name]

    @classmethod
    def unregister(cls, name: str) -> None:
        """
        Unregister a deployment provider.

        Args:
            name: Provider identifier
        """
        cls._providers.pop(name, None)
        cls._instances.pop(name, None)

    @classmethod
    def get(cls, name: str) -> DeploymentProvider:
        """
        Get a provider instance by name.

        Args:
            name: Provider identifier

        Returns:
            DeploymentProvider instance

        Raises:
            KeyError: If provider not found
        """
        cls.discover()

        if name not in cls._providers:
            available = ", ".join(cls._providers.keys())
            raise KeyError(f"Provider '{name}' not found. Available: {available}")

        # Lazy instantiation
        if name not in cls._instances:
            cls._instances[name] = cls._providers[name]()

        return cls._instances[name]

    @classmethod
    def list_providers(cls) -> list[str]:
        """
        List all registered provider names.

        Returns:
            List of provider identifiers
        """
        cls.discover()
        return list(cls._providers.keys())

    @classmethod
    def list_capabilities(cls) -> dict[str, ProviderCapabilities]:
        """
        List capabilities of all registered providers.

        Returns:
            Dict mapping provider name to capabilities
        """
        cls.discover()
        return {name: cls.get(name).capabilities for name in cls._providers}

    @classmethod
    def for_target(cls, target: DeploymentTarget) -> list[DeploymentProvider]:
        """
        Get all providers supporting a deployment target.

        Args:
            target: Deployment target (backend, frontend, etc.)

        Returns:
            List of provider instances supporting the target
        """
        cls.discover()
        return [
            cls.get(name) for name in cls._providers if target in cls.get(name).capabilities.targets
        ]

    @classmethod
    def configured_providers(cls) -> list[DeploymentProvider]:
        """
        Get all providers that are properly configured.

        Returns:
            List of configured provider instances
        """
        cls.discover()
        return [cls.get(name) for name in cls._providers if cls.get(name).is_configured()]

    @classmethod
    def unconfigured_providers(cls) -> dict[str, list[str]]:
        """
        Get providers that are missing configuration.

        Returns:
            Dict mapping provider name to list of missing env vars
        """
        cls.discover()
        result = {}
        for name in cls._providers:
            provider = cls.get(name)
            missing = provider.missing_config()
            if missing:
                result[name] = missing
        return result

    @classmethod
    def reset(cls) -> None:
        """
        Reset registry state.

        Clears all registered providers and cached instances.
        Used primarily for testing.
        """
        cls._providers.clear()
        cls._instances.clear()
        cls._discovered = False
