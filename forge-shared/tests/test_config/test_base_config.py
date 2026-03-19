"""Tests for BaseConfig class."""

import os
import pytest
from pydantic import ValidationError

from forge_shared.config import BaseConfig, get_config, set_config


class TestBaseConfig:
    """Test BaseConfig functionality."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = BaseConfig()

        assert config.app_name == "forge-app"
        assert config.app_version == "0.1.0"
        assert config.environment == "development"
        assert config.debug is False
        assert config.host == "0.0.0.0"
        assert config.port == 8000
        assert config.jwt_algorithm == "HS256"
        assert config.jwt_expire_minutes == 30
        assert config.database_pool_size == 10
        assert config.redis_url == "redis://localhost:6379"
        assert config.posthog_host == "https://app.posthog.com"
        assert config.log_level == "INFO"
        assert config.log_format == "json"

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = BaseConfig(
            app_name="custom-app",
            app_version="2.0.0",
            environment="production",
            debug=True,
            port=9000,
        )

        assert config.app_name == "custom-app"
        assert config.app_version == "2.0.0"
        assert config.environment == "production"
        assert config.debug is True
        assert config.port == 9000

    def test_is_production(self) -> None:
        """Test is_production property."""
        dev_config = BaseConfig(environment="development")
        assert dev_config.is_production is False

        prod_config = BaseConfig(environment="production")
        assert prod_config.is_production is True

        staging_config = BaseConfig(environment="staging")
        assert staging_config.is_production is False

    def test_is_development(self) -> None:
        """Test is_development property."""
        dev_config = BaseConfig(environment="development")
        assert dev_config.is_development is True

        prod_config = BaseConfig(environment="production")
        assert prod_config.is_development is False

        staging_config = BaseConfig(environment="staging")
        assert staging_config.is_development is False

    def test_get_database_url_postgresql(self) -> None:
        """Test get_database_url with PostgreSQL."""
        config = BaseConfig(database_url="postgresql://user:pass@localhost/db")

        # Default to async
        url = config.get_database_url()
        assert url == "postgresql+asyncpg://user:pass@localhost/db"

        # Sync
        url = config.get_database_url(async_driver=False)
        assert url == "postgresql://user:pass@localhost/db"

    def test_get_database_url_mysql(self) -> None:
        """Test get_database_url with MySQL."""
        config = BaseConfig(database_url="mysql://user:pass@localhost/db")

        url = config.get_database_url()
        assert url == "mysql+aiomysql://user:pass@localhost/db"

    def test_get_database_url_sqlite(self) -> None:
        """Test get_database_url with SQLite."""
        config = BaseConfig(database_url="sqlite:///test.db")

        url = config.get_database_url()
        assert url == "sqlite+aiosqlite:///test.db"

    def test_get_database_url_already_async(self) -> None:
        """Test get_database_url with already async URL."""
        config = BaseConfig(database_url="postgresql+asyncpg://user:pass@localhost/db")

        url = config.get_database_url()
        assert url == "postgresql+asyncpg://user:pass@localhost/db"

    def test_get_database_url_not_configured(self) -> None:
        """Test get_database_url when not configured."""
        config = BaseConfig(database_url=None)

        with pytest.raises(ValueError, match="Database URL not configured"):
            config.get_database_url()

    def test_environment_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test loading from environment variables."""
        monkeypatch.setenv("APP_NAME", "env-app")
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("PORT", "9000")

        config = BaseConfig()

        assert config.app_name == "env-app"
        assert config.environment == "production"
        assert config.debug is True
        assert config.port == 9000

    def test_global_config_singleton(self) -> None:
        """Test global configuration singleton."""
        # Reset global config
        import forge_shared.config.base as config_module
        config_module._config = None

        config1 = get_config()
        config2 = get_config()

        assert config1 is config2

    def test_set_global_config(self) -> None:
        """Test setting global configuration."""
        custom_config = BaseConfig(app_name="custom")

        set_config(custom_config)
        retrieved_config = get_config()

        assert retrieved_config.app_name == "custom"

    def test_validation_error(self) -> None:
        """Test validation error for invalid values."""
        with pytest.raises(ValidationError):
            BaseConfig(port="invalid")

    def test_case_insensitive_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test case-insensitive environment variables."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")

        config = BaseConfig()
        assert config.database_url == "postgresql://localhost/db"
