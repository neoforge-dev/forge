"""
Configuration module for application settings.

Provides hierarchical configuration management with environment variable
support, domain-specific configs, and type-safe settings.

Example:
    ```python
    from forge_shared.config import BaseConfig, Field

    class AppConfig(BaseConfig):
        database_url: str = Field(..., description="Database URL")
        debug: bool = Field(default=False)

    config = AppConfig()
    ```
"""

from forge_shared.config.base import BaseConfig, get_config, set_config
from forge_shared.config.domain import DomainConfig, get_domain_config
from forge_shared.config.loaders import (
    load_config_from_dict,
    load_config_from_env,
    load_config_from_file,
)

__all__ = [
    "BaseConfig",
    "DomainConfig",
    "get_domain_config",
    "get_config",
    "set_config",
    "load_config_from_dict",
    "load_config_from_env",
    "load_config_from_file",
]
