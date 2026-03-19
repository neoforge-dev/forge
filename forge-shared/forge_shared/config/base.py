"""
Base configuration class for application settings.

Provides type-safe configuration management with environment variable
support and validation.

Example:
    ```python
    from forge_shared.config import BaseConfig, Field

    class AppConfig(BaseConfig):
        database_url: str = Field(..., description="Database connection URL")
        redis_url: str = Field(default="redis://localhost:6379")
        debug: bool = Field(default=False)

    config = AppConfig()
    ```
"""

from typing import Any, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseConfig(BaseSettings):
    """
    Base configuration class for application settings.

    Provides type-safe configuration management with environment variable
    support, validation, and defaults.

    Attributes:
        model_config: Pydantic settings configuration

    Example:
        ```python
        class AppConfig(BaseConfig):
            database_url: str = Field(..., description="Database URL")
            debug: bool = Field(default=False)

        config = AppConfig()
        ```
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Common settings
    app_name: str = Field(default="forge-app", description="Application name")
    app_version: str = Field(default="0.1.0", description="Application version")
    environment: str = Field(default="development", description="Environment name")
    debug: bool = Field(default=False, description="Debug mode")

    # Server settings
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")

    # Security settings
    secret_key: str = Field(default="change-me", description="Secret key for signing")
    jwt_algorithm: str = Field(default="HS256", description="JWT algorithm")
    jwt_expire_minutes: int = Field(default=30, description="JWT expiration minutes")

    # Database settings
    database_url: Optional[str] = Field(None, description="Database connection URL")
    database_pool_size: int = Field(default=10, description="Database pool size")

    # Redis settings
    redis_url: str = Field(default="redis://localhost:6379", description="Redis URL")

    # Analytics settings
    posthog_api_key: Optional[str] = Field(None, description="PostHog API key")
    posthog_host: str = Field(
        default="https://app.posthog.com", description="PostHog host"
    )

    # Logging settings
    log_level: str = Field(default="INFO", description="Log level")
    log_format: str = Field(default="json", description="Log format (json or text)")

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development."""
        return self.environment == "development"

    def get_database_url(
        self,
        async_driver: bool = True,
    ) -> str:
        """
        Get database URL with appropriate driver.

        Args:
            async_driver: Use async driver

        Returns:
            Database URL

        Raises:
            ValueError: If database URL is not configured
        """
        if not self.database_url:
            raise ValueError("Database URL not configured")

        if async_driver and not self.database_url.startswith(
            ("postgresql+asyncpg://", "mysql+aiomysql://", "sqlite+aiosqlite://")
        ):
            # Convert to async driver
            if self.database_url.startswith("postgresql://"):
                return self.database_url.replace(
                    "postgresql://", "postgresql+asyncpg://", 1
                )
            elif self.database_url.startswith("mysql://"):
                return self.database_url.replace("mysql://", "mysql+aiomysql://", 1)
            elif self.database_url.startswith("sqlite://"):
                return self.database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

        return self.database_url


# Global config instance
_config: Optional[BaseConfig] = None


def get_config(config_class: type[BaseConfig] = BaseConfig) -> BaseConfig:
    """
    Get the global configuration instance.

    Args:
        config_class: Configuration class to instantiate

    Returns:
        Configuration instance
    """
    global _config
    if _config is None:
        _config = config_class()
    return _config


def set_config(config: BaseConfig) -> None:
    """
    Set the global configuration instance.

    Args:
        config: Configuration instance
    """
    global _config
    _config = config
