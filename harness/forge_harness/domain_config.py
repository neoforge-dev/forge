"""
Domain Configuration
====================

Domain-specific rules, compliance requirements, and CLAUDE.md hierarchy loading.
Each of FORGE's 11 domains has unique constraints that autonomous agents must respect.

Note: This module provides backward compatibility. For new code, prefer using
DomainRegistry from domain_registry.py which loads from domains.yaml.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DomainConfig:
    """Configuration for a FORGE domain."""

    name: str
    compliance: list[str] = field(default_factory=list)
    human_gates: list[str] = field(default_factory=list)
    frontend_tier: str = "React"  # React, Lit PWA, React Native
    localization: str | None = None
    special_rules: dict[str, Any] = field(default_factory=dict)


def _build_domain_configs_from_registry() -> dict[str, DomainConfig]:
    """
    Build DOMAIN_CONFIGS dict from DomainRegistry.

    This provides backward compatibility - existing code using DOMAIN_CONFIGS
    will continue to work, but the source of truth is now domains.yaml.
    """
    try:
        from .domain_registry import DomainRegistry

        DomainRegistry.load()
        configs = {}

        for entry in DomainRegistry.all():
            configs[entry.key] = DomainConfig(
                name=entry.key,
                compliance=entry.compliance,
                human_gates=entry.human_gates,
                frontend_tier=entry.frontend_tier,
                localization=entry.localization,
                special_rules=entry.special_rules,
            )

        return configs
    except Exception:
        # Fallback to hardcoded if registry fails (e.g., during testing)
        return _FALLBACK_DOMAIN_CONFIGS


# Fallback configs for when registry isn't available
_FALLBACK_DOMAIN_CONFIGS: dict[str, DomainConfig] = {
    "thebrightharbor-com": DomainConfig(
        name="thebrightharbor-com",
        compliance=["COPPA"],
        human_gates=["age_verification", "data_collection", "parental_consent"],
        frontend_tier="Lit PWA",
        special_rules={
            "age_adaptive_ui": True,
            "no_personal_data_collection": True,
            "parental_gate_required": True,
        },
    ),
    "calmconnect-io": DomainConfig(
        name="calmconnect-io",
        compliance=["HIPAA-lite"],
        human_gates=["health_data", "anonymization", "crisis_content"],
        frontend_tier="React",
        special_rules={
            "anonymized_data_only": True,
            "no_diagnosis_claims": True,
            "crisis_resources_required": True,
        },
    ),
    "codeswiftr-com": DomainConfig(
        name="codeswiftr-com",
        compliance=[],
        human_gates=["payment_integration", "api_keys"],
        frontend_tier="React",
        special_rules={
            "stripe_integration": True,
            "api_rate_limiting": True,
        },
    ),
}

# Load from registry (falls back to hardcoded if needed)
DOMAIN_CONFIGS: dict[str, DomainConfig] = _build_domain_configs_from_registry()


def get_domain_config(domain: str) -> DomainConfig:
    """
    Get configuration for a specific domain.

    Loads from DomainRegistry (domains.yaml) with fallback to defaults.
    """
    # Always try registry first (it's the source of truth)
    try:
        # Try relative import first (when used as package)
        try:
            from .domain_registry import DomainRegistry
        except ImportError:
            # Fall back to absolute import (when used standalone)
            from domain_registry import DomainRegistry  # type: ignore

        DomainRegistry._ensure_loaded()
        entry = DomainRegistry.get_or_none(domain)
        if entry:
            return DomainConfig(
                name=entry.key,
                compliance=entry.compliance,
                human_gates=entry.human_gates,
                frontend_tier=entry.frontend_tier,
                localization=entry.localization,
                special_rules=entry.special_rules,
            )
    except Exception:
        pass

    # Fallback to DOMAIN_CONFIGS (for backward compat)
    if domain in DOMAIN_CONFIGS:
        return DOMAIN_CONFIGS[domain]

    # Return default config for unknown domains
    return DomainConfig(
        name=domain,
        compliance=[],
        human_gates=[],
        frontend_tier="React",
    )


def parse_claude_md(file_path: Path) -> dict[str, Any]:
    """
    Parse a CLAUDE.md file and extract key directives.

    Returns dict with:
    - tech_stack: Dict of layer -> technology
    - human_gates: List of gate descriptions
    - quick_commands: Dict of command -> description
    - rules: List of operating rules
    """
    if not file_path.exists():
        return {}

    content = file_path.read_text()
    result: dict[str, Any] = {
        "tech_stack": {},
        "human_gates": [],
        "quick_commands": {},
        "rules": [],
        "raw_content": content,
    }

    # Extract tech stack table
    tech_match = re.search(
        r"\|\s*Layer\s*\|\s*Standard\s*\|.*?\n((?:\|.*?\|.*?\|\n)+)",
        content,
        re.IGNORECASE,
    )
    if tech_match:
        for line in tech_match.group(1).strip().split("\n"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2 and not parts[0].startswith("-"):
                result["tech_stack"][parts[0]] = parts[1]

    # Extract human gates
    gates_match = re.search(
        r"## Human Gates.*?\n((?:[\d]+\..*?\n)+)",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if gates_match:
        for line in gates_match.group(1).strip().split("\n"):
            gate = re.sub(r"^\d+\.\s*\*\*([^*]+)\*\*.*", r"\1", line).strip()
            if gate and gate != line:
                result["human_gates"].append(gate)

    # Extract operating rules
    rules_match = re.search(
        r"## Operating Rules.*?\n((?:-\s+\*\*.*?\n)+)",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if rules_match:
        for line in rules_match.group(1).strip().split("\n"):
            rule = re.sub(r"^-\s+\*\*([^*]+)\*\*.*", r"\1", line).strip()
            if rule and rule != line:
                result["rules"].append(rule)

    return result


def load_claude_md_hierarchy(
    forge_root: Path,
    domain: str,
    project: str,
) -> dict[str, Any]:
    """
    Load CLAUDE.md files in hierarchy order: portfolio -> domain -> project.

    Later files override earlier ones for conflicting keys.
    """
    hierarchy: dict[str, Any] = {
        "portfolio": {},
        "domain": {},
        "project": {},
        "domain_config": get_domain_config(domain),
        "merged": {},
    }

    # 1. Portfolio-level CLAUDE.md
    portfolio_claude = forge_root / "CLAUDE.md"
    if portfolio_claude.exists():
        hierarchy["portfolio"] = parse_claude_md(portfolio_claude)

    # 2. Domain-level CLAUDE.md (if exists)
    domain_claude = forge_root / domain / "CLAUDE.md"
    if domain_claude.exists():
        hierarchy["domain"] = parse_claude_md(domain_claude)

    # 3. Project-level CLAUDE.md (if exists)
    project_claude = forge_root / domain / project / "CLAUDE.md"
    if project_claude.exists():
        hierarchy["project"] = parse_claude_md(project_claude)

    # Merge with later levels overriding earlier
    merged = {}
    for level in ["portfolio", "domain", "project"]:
        level_data = hierarchy[level]
        for key, value in level_data.items():
            if key == "raw_content":
                continue
            if isinstance(value, dict):
                merged.setdefault(key, {}).update(value)
            elif isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            else:
                merged[key] = value

    # Add domain config
    domain_cfg = hierarchy["domain_config"]
    merged["compliance"] = domain_cfg.compliance
    merged["human_gates"] = list(set(merged.get("human_gates", []) + domain_cfg.human_gates))
    merged["frontend_tier"] = domain_cfg.frontend_tier
    merged["localization"] = domain_cfg.localization
    merged["special_rules"] = domain_cfg.special_rules

    hierarchy["merged"] = merged
    return hierarchy


def requires_human_gate(domain: str, action: str) -> tuple[bool, str]:
    """
    Check if an action requires human review for a specific domain.

    Args:
        domain: The FORGE domain name
        action: The action being performed (e.g., "auth_change", "data_collection")

    Returns:
        (requires_gate, reason) tuple
    """
    config = get_domain_config(domain)

    # Check domain-specific gates
    gate_mappings = {
        # Auth/Security
        "auth_change": ["Security", "auth"],
        "api_key": ["api_keys", "Security"],
        "payment": ["payment_integration", "Payments"],
        # Data/Privacy
        "data_collection": ["data_collection", "COPPA", "GDPR"],
        "health_data": ["health_data", "HIPAA-lite"],
        "personal_data": ["data_collection", "GDPR"],
        # Content
        "health_advice": ["health_advice", "medical_disclaimers"],
        "content_moderation": ["content_moderation", "crisis_content"],
        # Architecture
        "schema_change": ["Architecture", "Breaking Changes"],
        "api_contract": ["API Contract", "Breaking Changes"],
    }

    action_gates = gate_mappings.get(action, [])

    for gate in config.human_gates + config.compliance:
        if any(ag.lower() in gate.lower() for ag in action_gates):
            return True, f"Domain {domain} requires human review for: {gate}"

    return False, ""


def get_tech_stack(domain: str) -> dict[str, str]:
    """
    Get the recommended tech stack for a domain.

    Returns dict with backend, frontend, database, etc.
    """
    config = get_domain_config(domain)

    base_stack = {
        "backend": "FastAPI + SQLModel + PostgreSQL",
        "package_manager": "Astral uv (MANDATORY)",
        "database": "PostgreSQL 15+ (prod), SQLite (dev)",
        "cache": "Redis 7+",
        "deployment_backend": "Railway",
        "deployment_frontend": "Cloudflare Pages",
        "analytics": "PostHog",
    }

    # Set frontend based on tier
    frontend_stacks = {
        "React": "React 19 + Vite + TailwindCSS v4",
        "Lit PWA": "Lit + Vite + TailwindCSS",
        "React Native": "React Native + Expo",
    }
    base_stack["frontend"] = frontend_stacks.get(config.frontend_tier, frontend_stacks["React"])

    # Add localization if needed
    if config.localization:
        base_stack["localization"] = config.localization

    return base_stack
