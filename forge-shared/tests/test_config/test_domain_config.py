"""Tests for DomainConfig class."""

import pytest

from forge_shared.config import DomainConfig, get_domain_config


class TestDomainConfig:
    """Test DomainConfig functionality."""

    def test_default_values(self) -> None:
        """Test default domain configuration values."""
        config = DomainConfig()

        assert config.domain == "forge"
        assert config.subdomain is None
        assert config.domain_url is None
        assert config.cdn_url is None
        assert config.api_url is None

    def test_custom_domain(self) -> None:
        """Test custom domain configuration."""
        config = DomainConfig(domain="example.com")

        assert config.domain == "example.com"

    def test_with_subdomain(self) -> None:
        """Test domain with subdomain."""
        config = DomainConfig(
            domain="example.com",
            subdomain="api",
        )

        assert config.domain == "example.com"
        assert config.subdomain == "api"

    def test_get_domain_name_without_subdomain(self) -> None:
        """Test get_domain_name without subdomain."""
        config = DomainConfig(domain="example.com")

        assert config.get_domain_name() == "example.com"

    def test_get_domain_name_with_subdomain(self) -> None:
        """Test get_domain_name with subdomain."""
        config = DomainConfig(
            domain="example.com",
            subdomain="api",
        )

        assert config.get_domain_name() == "api.example.com"

    def test_get_base_url_with_custom_url(self) -> None:
        """Test get_base_url with custom domain URL."""
        config = DomainConfig(
            domain="example.com",
            domain_url="https://custom.example.com",
        )

        assert config.get_base_url() == "https://custom.example.com"

    def test_get_base_url_production(self) -> None:
        """Test get_base_url in production."""
        config = DomainConfig(
            domain="example.com",
            environment="production",
        )

        assert config.get_base_url() == "https://example.com"

    def test_get_base_url_development(self) -> None:
        """Test get_base_url in development."""
        config = DomainConfig(
            domain="example.com",
            environment="development",
            port=8000,
        )

        assert config.get_base_url() == "http://example.com:8000"

    def test_get_base_url_with_subdomain(self) -> None:
        """Test get_base_url with subdomain in production."""
        config = DomainConfig(
            domain="example.com",
            subdomain="api",
            environment="production",
        )

        assert config.get_base_url() == "https://api.example.com"

    def test_get_domain_config_function(self) -> None:
        """Test get_domain_config helper function."""
        config = get_domain_config("example.com", "api")

        assert config.domain == "example.com"
        assert config.subdomain == "api"

    def test_inherits_base_config(self) -> None:
        """Test that DomainConfig inherits BaseConfig properties."""
        config = DomainConfig(
            domain="example.com",
            environment="production",
            debug=True,
        )

        assert config.is_production is True
        assert config.debug is True

    def test_database_url_in_domain_config(self) -> None:
        """Test database URL functionality in DomainConfig."""
        config = DomainConfig(
            domain="example.com",
            database_url="postgresql://localhost/db",
        )

        url = config.get_database_url()
        assert url == "postgresql+asyncpg://localhost/db"
