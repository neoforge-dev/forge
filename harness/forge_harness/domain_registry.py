"""
Domain Registry
===============

Centralized registry of all FORGE portfolio domains.
Single source of truth loaded from domains.yaml.

Usage:
    from forge_harness.domain_registry import DomainRegistry

    DomainRegistry.load()  # Load once at startup
    domain = DomainRegistry.get("thebrightharbor-com")
    tier_1_domains = DomainRegistry.by_tier(1)
    coppa_domains = DomainRegistry.by_compliance("COPPA")
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ContentConfig:
    """Content generation configuration for a domain."""

    tier: int  # 1 (60%), 2 (25%), 3 (15%)
    target_audience: str
    voice_tone: str  # professional, friendly, technical, empathetic, etc.
    content_strategy_path: str | None = None  # relative to domain
    pillars: list[str] = field(default_factory=list)


@dataclass
class DeploymentConfig:
    """Deployment targets for a domain."""

    railway_service: str | None = None
    cloudflare_project: str | None = None
    marketing_site_path: str | None = None  # relative to FORGE root


@dataclass
class NotionConfig:
    """Notion integration configuration for a domain."""

    data_source_id: str | None = None
    linked_database_id: str | None = None


@dataclass
class DomainEntry:
    """Complete configuration for a FORGE domain."""

    key: str  # e.g., "thebrightharbor-com"
    display_name: str  # e.g., "The Bright Harbor"

    # Compliance and gates
    compliance: list[str] = field(default_factory=list)
    human_gates: list[str] = field(default_factory=list)

    # Tech stack
    frontend_tier: str = "React"  # React | Lit PWA | React Native
    localization: str | None = None  # ISO language code

    # Domain-specific rules
    special_rules: dict[str, Any] = field(default_factory=dict)

    # Nested configs
    content: ContentConfig | None = None
    deployment: DeploymentConfig | None = None
    notion: NotionConfig | None = None

    # Products and status
    products: list[str] = field(default_factory=list)
    active: bool = True

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> "DomainEntry":
        """Create DomainEntry from YAML dict."""
        # Parse nested configs
        content_data = data.get("content", {})
        content = ContentConfig(
            tier=content_data.get("tier", 3),
            target_audience=content_data.get("target_audience", ""),
            voice_tone=content_data.get("voice_tone", "professional"),
            content_strategy_path=content_data.get("content_strategy_path"),
            pillars=content_data.get("pillars", []),
        )

        deployment_data = data.get("deployment", {})
        deployment = DeploymentConfig(
            railway_service=deployment_data.get("railway_service"),
            cloudflare_project=deployment_data.get("cloudflare_project"),
            marketing_site_path=deployment_data.get("marketing_site_path"),
        )

        notion_data = data.get("notion", {})
        notion = NotionConfig(
            data_source_id=notion_data.get("data_source_id"),
            linked_database_id=notion_data.get("linked_database_id"),
        )

        return cls(
            key=key,
            display_name=data.get("display_name", key),
            compliance=data.get("compliance", []),
            human_gates=data.get("human_gates", []),
            frontend_tier=data.get("frontend_tier", "React"),
            localization=data.get("localization"),
            special_rules=data.get("special_rules", {}),
            content=content,
            deployment=deployment,
            notion=notion,
            products=data.get("products", []),
            active=data.get("active", True),
        )


class DomainRegistry:
    """
    Centralized registry of all FORGE domains.

    Load from domains.yaml once at startup, then query as needed.
    """

    _domains: dict[str, DomainEntry] = {}
    _loaded: bool = False
    _yaml_path: Path | None = None

    @classmethod
    def load(cls, path: Path | str | None = None) -> None:
        """
        Load domain registry from YAML file.

        Args:
            path: Path to domains.yaml. If None, uses default location.
        """
        if path is None:
            # Default to domains.yaml in same directory as this module
            path = Path(__file__).parent / "domains.yaml"
        else:
            path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Domain registry not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        cls._domains = {}
        for key, domain_data in data.get("domains", {}).items():
            cls._domains[key] = DomainEntry.from_dict(key, domain_data)

        cls._loaded = True
        cls._yaml_path = path

    @classmethod
    def _ensure_loaded(cls) -> None:
        """Ensure registry is loaded, loading with defaults if not."""
        if not cls._loaded:
            cls.load()

    @classmethod
    def get(cls, domain_key: str) -> DomainEntry:
        """
        Get configuration for a specific domain.

        Args:
            domain_key: Domain key (e.g., "thebrightharbor-com")

        Returns:
            DomainEntry for the domain

        Raises:
            KeyError: If domain not found
        """
        cls._ensure_loaded()
        if domain_key not in cls._domains:
            raise KeyError(f"Unknown domain: {domain_key}")
        return cls._domains[domain_key]

    @classmethod
    def get_or_none(cls, domain_key: str) -> DomainEntry | None:
        """Get domain config or None if not found."""
        cls._ensure_loaded()
        return cls._domains.get(domain_key)

    @classmethod
    def all(cls) -> list[DomainEntry]:
        """Get all registered domains."""
        cls._ensure_loaded()
        return list(cls._domains.values())

    @classmethod
    def keys(cls) -> list[str]:
        """Get all domain keys."""
        cls._ensure_loaded()
        return list(cls._domains.keys())

    @classmethod
    def active(cls) -> list[DomainEntry]:
        """Get all active domains."""
        cls._ensure_loaded()
        return [d for d in cls._domains.values() if d.active]

    @classmethod
    def by_tier(cls, tier: int) -> list[DomainEntry]:
        """
        Get domains by content tier.

        Args:
            tier: Content tier (1, 2, or 3)

        Returns:
            List of domains in that tier
        """
        cls._ensure_loaded()
        return [d for d in cls._domains.values() if d.content and d.content.tier == tier]

    @classmethod
    def by_compliance(cls, compliance: str) -> list[DomainEntry]:
        """
        Get domains with specific compliance requirement.

        Args:
            compliance: Compliance requirement (e.g., "COPPA", "HIPAA-lite")

        Returns:
            List of domains with that compliance requirement
        """
        cls._ensure_loaded()
        return [d for d in cls._domains.values() if compliance in d.compliance]

    @classmethod
    def by_human_gate(cls, gate: str) -> list[DomainEntry]:
        """
        Get domains requiring specific human gate.

        Args:
            gate: Human gate (e.g., "payment_integration", "health_data")

        Returns:
            List of domains requiring that gate
        """
        cls._ensure_loaded()
        return [d for d in cls._domains.values() if gate in d.human_gates]

    @classmethod
    def with_notion(cls) -> list[DomainEntry]:
        """Get domains with Notion integration configured."""
        cls._ensure_loaded()
        return [d for d in cls._domains.values() if d.notion and d.notion.data_source_id]

    @classmethod
    def with_deployment(cls) -> list[DomainEntry]:
        """Get domains with deployment targets configured."""
        cls._ensure_loaded()
        return [
            d
            for d in cls._domains.values()
            if d.deployment and (d.deployment.railway_service or d.deployment.cloudflare_project)
        ]

    @classmethod
    def validate(cls, use_schema: bool = True) -> list[str]:
        """
        Validate registry for common issues.

        Args:
            use_schema: If True, also validates against JSON Schema (requires jsonschema)

        Returns:
            List of validation errors (empty if valid)
        """
        cls._ensure_loaded()
        errors = []

        # JSON Schema validation (optional)
        if use_schema:
            schema_path = Path(__file__).parent / "schemas" / "domains.schema.json"
            if schema_path.exists():
                try:
                    import json

                    import jsonschema

                    # Load schema
                    with open(schema_path) as f:
                        schema = json.load(f)

                    # Load raw YAML data for schema validation
                    if cls._yaml_path and cls._yaml_path.exists():
                        with open(cls._yaml_path) as f:
                            data = yaml.safe_load(f)
                        try:
                            jsonschema.validate(data, schema)
                        except jsonschema.ValidationError as e:
                            errors.append(f"Schema validation error: {e.message}")
                            # Add path to error location
                            if e.absolute_path:
                                path = ".".join(str(p) for p in e.absolute_path)
                                errors.append(f"  at: {path}")
                except ImportError:
                    pass  # jsonschema not installed, skip schema validation

        # Additional semantic validation
        for key, domain in cls._domains.items():
            # Check required fields
            if not domain.display_name:
                errors.append(f"{key}: missing display_name")

            # Check content config
            if domain.content:
                if domain.content.tier not in [1, 2, 3]:
                    errors.append(f"{key}: invalid content tier {domain.content.tier}")
                if not domain.content.target_audience:
                    errors.append(f"{key}: missing content.target_audience")

            # Check frontend tier
            valid_tiers = ["React", "Lit PWA", "React Native", "basic", "interactive", "advanced"]
            if domain.frontend_tier not in valid_tiers:
                errors.append(f"{key}: invalid frontend_tier '{domain.frontend_tier}'")

            # Check Notion config consistency
            if domain.notion:
                has_source = bool(domain.notion.data_source_id)
                has_db = bool(domain.notion.linked_database_id)
                if has_source != has_db:
                    errors.append(f"{key}: Notion config incomplete (need both IDs)")

        return errors

    @classmethod
    def reload(cls) -> None:
        """Force reload from YAML file."""
        cls._loaded = False
        if cls._yaml_path:
            cls.load(cls._yaml_path)
        else:
            cls.load()

    @classmethod
    def summary(cls) -> dict[str, Any]:
        """Get summary statistics of the registry."""
        cls._ensure_loaded()

        active = [d for d in cls._domains.values() if d.active]
        with_notion = [d for d in cls._domains.values() if d.notion and d.notion.data_source_id]

        return {
            "total_domains": len(cls._domains),
            "active_domains": len(active),
            "tier_1_count": len(cls.by_tier(1)),
            "tier_2_count": len(cls.by_tier(2)),
            "tier_3_count": len(cls.by_tier(3)),
            "with_notion": len(with_notion),
            "compliance_domains": {
                "COPPA": len(cls.by_compliance("COPPA")),
                "HIPAA-lite": len(cls.by_compliance("HIPAA-lite")),
                "GDPR": len(cls.by_compliance("GDPR")),
            },
        }


# Convenience function for backward compatibility with domain_config.py
def get_domain_from_registry(domain: str) -> DomainEntry | None:
    """Get domain from registry, returning None if not found."""
    try:
        return DomainRegistry.get(domain)
    except KeyError:
        return None
