"""
Feature Flag System for FORGE v2/v3 Toggle

Provides capability to run v2 and v3 in parallel during transition.
"""

import os
from pathlib import Path
from typing import Any

import yaml


class FeatureFlagManager:
    """Manages feature flags for v2/v3 transition."""

    DEFAULT_CONFIG_PATH = Path(".forge/config/features.yaml")

    def __init__(self, config_path: Path | None = None):
        """Initialize feature flag manager.

        Args:
            config_path: Path to features.yaml. Defaults to .forge/config/features.yaml
        """
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._flags: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load feature flags from YAML file."""
        if self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    data = yaml.safe_load(f) or {}
                    self._flags = data.get('features', {})
            except Exception as e:
                print(f"Warning: Failed to load feature flags: {e}")
                self._flags = {}
        else:
            # Use defaults if no config file
            self._flags = self._get_defaults()

    def _get_defaults(self) -> dict[str, Any]:
        """Get default feature flag values."""
        return {
            'v3_orchestrator_enabled': False,
            'v3_task_queue_enabled': False,
            'v3_dark_factory_enabled': False,
            'v3_websocket_enabled': False,
            'v3_metrics_enabled': False,
        }

    def get(self, flag: str, default: Any = False) -> Any:
        """Get feature flag value.

        Args:
            flag: Feature flag name
            default: Default value if flag not found

        Returns:
            Flag value or default
        """
        # Environment variable override: FORGE_FF_<flag_name>
        env_var = f"FORGE_FF_{flag.upper()}"
        if env_var in os.environ:
            return os.environ[env_var].lower() in ('true', '1', 'yes', 'on')

        return self._flags.get(flag, default)

    def is_enabled(self, flag: str) -> bool:
        """Check if a feature flag is enabled.

        Args:
            flag: Feature flag name

        Returns:
            True if flag is enabled, False otherwise
        """
        return bool(self.get(flag, False))

    def set(self, flag: str, value: Any) -> None:
        """Set feature flag value (in-memory only).

        Args:
            flag: Feature flag name
            value: Flag value
        """
        self._flags[flag] = value

    def save(self) -> None:
        """Save current feature flags to config file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
            yaml.dump({'features': self._flags}, f, default_flow_style=False)

    def all_flags(self) -> dict[str, Any]:
        """Get all feature flags.

        Returns:
            Dictionary of all feature flags
        """
        return self._flags.copy()


# Global instance for convenience
_ff_manager: FeatureFlagManager | None = None


def get_feature_flags() -> FeatureFlagManager:
    """Get global feature flag manager instance.

    Returns:
        FeatureFlagManager singleton
    """
    global _ff_manager
    if _ff_manager is None:
        _ff_manager = FeatureFlagManager()
    return _ff_manager


def is_v3_enabled() -> bool:
    """Quick check if v3 orchestrator is enabled.

    Returns:
        True if v3 orchestrator is enabled
    """
    return get_feature_flags().is_enabled('v3_orchestrator_enabled')


def should_use_v3_api() -> bool:
    """Determine if v3 API should be used.

    Returns:
        True if v3 API should be used instead of v2
    """
    return is_v3_enabled()


# Decorator for v2/v3 routing
def v3_fallback(v3_func, v2_func):
    """Decorator to route calls based on v3 feature flag.

    Args:
        v3_func: Function to call if v3 is enabled
        v2_func: Function to call if v3 is disabled

    Returns:
        Wrapped function that routes appropriately
    """
    def wrapper(*args, **kwargs):
        if is_v3_enabled():
            return v3_func(*args, **kwargs)
        return v2_func(*args, **kwargs)
    return wrapper
