"""
Domain-specific configuration management.

Provides domain-specific configuration with inheritance and overrides
for multi-domain applications.

Example:
    ```python
    from forge_shared.config import DomainConfig

    class MyAppConfig(DomainConfig):
        api_key: str

    config = MyAppConfig(domain="my-app")
    ```
"""

from typing import Optional
from pydantic import Field

from forge_shared.config.base import BaseConfig


class DomainConfig(BaseConfig):
    """
    Domain-specific configuration class.

    Extends base configuration with domain-specific settings and
    environment-based overrides.

    Attributes:
        domain: Domain name for this configuration
        subdomain: Optional subdomain name

    Example:
        ```python
        class MyAppConfig(DomainConfig):
            api_key: str = Field(..., description="API key")

        config = MyAppConfig(domain="my-app")
        ```
    """

    domain: str = Field(default="forge", description="Domain name")
    subdomain: Optional[str] = Field(None, description="Subdomain name")

    # Domain-specific settings
    domain_url: Optional[str] = Field(None, description="Domain URL")
    cdn_url: Optional[str] = Field(None, description="CDN URL")
    api_url: Optional[str] = Field(None, description="API URL")

    def get_domain_name(self) -> str:
        """
        Get full domain name including subdomain.

        Returns:
            Full domain name
        """
        if self.subdomain:
            return f"{self.subdomain}.{self.domain}"
        return self.domain

    def get_base_url(self) -> str:
        """
        Get base URL for this domain.

        Returns:
            Base URL or default localhost
        """
        if self.domain_url:
            return self.domain_url
        if self.is_production:
            return f"https://{self.get_domain_name()}"
        return f"http://{self.get_domain_name()}:{self.port}"


def get_domain_config(domain: str, subdomain: Optional[str] = None) -> DomainConfig:
    """
    Get domain-specific configuration.

    Args:
        domain: Domain name
        subdomain: Optional subdomain name

    Returns:
        Domain configuration instance
    """
    return DomainConfig(domain=domain, subdomain=subdomain)
