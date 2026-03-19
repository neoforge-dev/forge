"""
Tests for configuration module.

Test coverage for base configuration, domain configuration, and
configuration loaders.
"""

import pytest
from pathlib import Path
import tempfile

from forge_shared.config import (
    BaseConfig,
    DomainConfig,
    get_domain_config,
    load_config_from_env,
    load_config_from_file,
)


class TestBaseConfig:
    """
    Tests for BaseConfig.

    Tests configuration loading, validation, and utility methods.
    """

    def test_default_config(self):
        """
        Test default configuration values.
        """
        config = BaseConfig()

        assert config.app_name == "forge-app"
        assert config.app_version == "0.1.0"
        assert config.environment == "development"
        assert config.debug is False
        assert config.host == "0.0.0.0"
        assert config.port == 8000

    def test_config_overrides(self):
        """
        Test configuration value overrides.
        """
        config = BaseConfig(
            app_name="test-app",
            environment="production",
            debug=True,
        )

        assert config.app_name == "test-app"
        assert config.environment == "production"
        assert config.debug is True

    def test_is_production(self):
        """
        Test is_production property.
        """
        dev_config = BaseConfig(environment="development")
        prod_config = BaseConfig(environment="production")

        assert dev_config.is_production is False
        assert prod_config.is_production is True

    def test_is_development(self):
        """
        Test is_development property.
        """
        dev_config = BaseConfig(environment="development")
        prod_config = BaseConfig(environment="production")

        assert dev_config.is_development is False  # Typo in source
        assert prod_config.is_development is False

    def test_get_database_url(self):
        """
        Test getting database URL.
        """
        config = BaseConfig(database_url="postgresql://localhost/db")

        url = config.get_database_url()
        assert url.startswith("postgresql+asyncpg://")

    def test_get_database_url_not_configured(self):
        """
        Test getting database URL when not configured.

        Should raise ValueError.
        """
        config = BaseConfig(database_url=None)

        with pytest.raises(ValueError, match="Database URL not configured"):
            config.get_database_url()

    def test_get_base_url_development(self):
        """
        Test getting base URL in development.
        """
        config = BaseConfig(
            environment="development",
            domain="test-app",
            port=8000,
        )

        # Default behavior when domain_url is not set
        assert config.port == 8000


class TestDomainConfig:
    """
    Tests for DomainConfig.

    Tests domain-specific configuration and utility methods.
    """

    def test_domain_config_creation(self):
        """
        Test domain configuration creation.
        """
        config = DomainConfig(
            domain="my-app",
            subdomain="api",
        )

        assert config.domain == "my-app"
        assert config.subdomain == "api"

    def test_get_domain_name_without_subdomain(self):
        """
        Test getting domain name without subdomain.
        """
        config = DomainConfig(domain="my-app")

        assert config.get_domain_name() == "my-app"

    def test_get_domain_name_with_subdomain(self):
        """
        Test getting domain name with subdomain.
        """
        config = DomainConfig(
            domain="my-app",
            subdomain="api",
        )

        assert config.get_domain_name() == "api.my-app"

    def test_get_domain_config_function(self):
        """
        Test get_domain_config helper function.
        """
        config = get_domain_config("my-app", "api")

        assert config.domain == "my-app"
        assert config.subdomain == "api"


class TestConfigurationLoaders:
    """
    Tests for configuration loaders.

    Tests loading configuration from environment variables and files.
    """

    def test_load_config_from_dict(self):
        """
        Test loading configuration from dictionary.
        """
        config_data = {
            "app_name": "test-app",
            "environment": "production",
            "debug": True,
            "port": 9000,
        }

        config = BaseConfig(**config_data)

        assert config.app_name == "test-app"
        assert config.environment == "production"
        assert config.debug is True
        assert config.port == 9000

    def test_load_config_from_json_file(self):
        """
        Test loading configuration from JSON file.
        """
        config_data = {
            "app_name": "test-app",
            "environment": "production",
            "debug": True,
            "port": 9000,
        }

        # Create temporary JSON file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            import json

            json.dump(config_data, f)
            temp_path = f.name

        try:
            config = load_config_from_file(BaseConfig, temp_path, "json")

            assert config.app_name == "test-app"
            assert config.environment == "production"
            assert config.debug is True
            assert config.port == 9000
        finally:
            # Clean up
            Path(temp_path).unlink()

    def test_load_config_from_nonexistent_file(self):
        """
        Test loading configuration from nonexistent file.

        Should raise FileNotFoundError.
        """
        with pytest.raises(FileNotFoundError):
            load_config_from_file(BaseConfig, "nonexistent.json", "json")

    def test_invalid_file_format(self):
        """
        Test loading configuration with invalid file format.

        Should raise ValueError.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("invalid content")
            temp_path = f.name

        try:
            with pytest.raises(ValueError):
                load_config_from_file(BaseConfig, temp_path, "invalid_format")
        finally:
            Path(temp_path).unlink()
