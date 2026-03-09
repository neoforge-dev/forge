"""Tests for domain configuration module."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

# Import directly to avoid agent.py which requires claude_code_sdk
sys.path.insert(0, str(Path(__file__).parent.parent / "forge_harness"))

from domain_config import (
    get_domain_config,
    get_tech_stack,
    load_claude_md_hierarchy,
    parse_claude_md,
    requires_human_gate,
)
from domain_registry import DomainRegistry


@pytest.fixture(autouse=True)
def load_domain_registry():
    """Ensure domain registry is loaded before tests."""
    # Reset first to ensure clean state
    DomainRegistry._domains = {}
    DomainRegistry._loaded = False
    DomainRegistry._yaml_path = None
    # Then load
    DomainRegistry.load()
    yield
    # No need to reset after - next fixture will reset


class TestDomainConfig:
    """Tests for DomainConfig dataclass and domain configurations."""

    def test_all_domains_via_registry(self):
        """Verify all 11 FORGE domains are available via registry."""
        expected_domains = {
            "thebrightharbor-com",
            "calmconnect-io",
            "babybit-es",
            "codeswiftr-com",
            "leanvibe-dev",
            "neoforge-dev",
            "brandfocus-ai",
            "adguild-io",
            "mumchef-io",
            "bogdanveliscu-com",
            "solodevhq-com",
        }
        assert set(DomainRegistry.keys()) == expected_domains

    def test_coppa_domain_has_compliance(self):
        """thebrightharbor-com should have COPPA compliance."""
        config = get_domain_config("thebrightharbor-com")
        assert "COPPA" in config.compliance
        assert "age_verification" in config.human_gates
        assert config.special_rules.get("parental_gate_required") is True

    def test_hipaa_domain_has_compliance(self):
        """calmconnect-io should have HIPAA-lite compliance."""
        config = get_domain_config("calmconnect-io")
        assert "HIPAA-lite" in config.compliance
        assert "health_data" in config.human_gates
        assert config.special_rules.get("anonymized_data_only") is True

    def test_localization_domain(self):
        """babybit-es should have Spanish localization."""
        config = get_domain_config("babybit-es")
        assert config.localization == "es"
        assert "GDPR" in config.compliance
        assert config.frontend_tier == "React Native"

    def test_frontend_tiers(self):
        """Verify frontend tier assignments."""
        assert get_domain_config("codeswiftr-com").frontend_tier == "React"
        assert get_domain_config("thebrightharbor-com").frontend_tier == "Lit PWA"
        assert get_domain_config("babybit-es").frontend_tier == "React Native"


class TestGetDomainConfig:
    """Tests for get_domain_config function."""

    def test_known_domain(self):
        """get_domain_config returns correct config for known domains."""
        config = get_domain_config("codeswiftr-com")
        assert config.name == "codeswiftr-com"
        assert "stripe_integration" in config.special_rules

    def test_unknown_domain(self):
        """get_domain_config returns default for unknown domains."""
        config = get_domain_config("unknown-domain")
        assert config.name == "unknown-domain"
        assert config.compliance == []
        assert config.human_gates == []
        assert config.frontend_tier == "React"


class TestParseClaudeMd:
    """Tests for CLAUDE.md parsing."""

    def test_parse_nonexistent_file(self):
        """Parsing nonexistent file returns empty dict."""
        result = parse_claude_md(Path("/nonexistent/CLAUDE.md"))
        assert result == {}

    def test_parse_tech_stack_table(self):
        """Parse tech stack from CLAUDE.md table."""
        with TemporaryDirectory() as tmpdir:
            claude_md = Path(tmpdir) / "CLAUDE.md"
            claude_md.write_text("""
# Project CLAUDE.md

## Tech Stack

| Layer | Standard |
|-------|----------|
| Backend | FastAPI |
| Frontend | React |
| Database | PostgreSQL |
""")
            result = parse_claude_md(claude_md)
            assert result["tech_stack"]["Backend"] == "FastAPI"
            assert result["tech_stack"]["Frontend"] == "React"
            assert result["tech_stack"]["Database"] == "PostgreSQL"

    def test_parse_human_gates(self):
        """Parse human gates from CLAUDE.md."""
        with TemporaryDirectory() as tmpdir:
            claude_md = Path(tmpdir) / "CLAUDE.md"
            claude_md.write_text("""
# CLAUDE.md

## Human Gates

1. **Security** - Require review for auth changes
2. **Payments** - Require review for billing changes
""")
            result = parse_claude_md(claude_md)
            assert "Security" in result["human_gates"]
            assert "Payments" in result["human_gates"]


class TestLoadClaudeMdHierarchy:
    """Tests for CLAUDE.md hierarchy loading."""

    def test_hierarchy_loading(self):
        """Test loading CLAUDE.md files in hierarchy."""
        with TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            # Create portfolio CLAUDE.md
            portfolio_claude = forge_root / "CLAUDE.md"
            portfolio_claude.write_text("""
# Portfolio CLAUDE.md

## Tech Stack

| Layer | Standard |
|-------|----------|
| Backend | FastAPI |
""")

            # Create domain and project directories
            domain_dir = forge_root / "test-domain"
            project_dir = domain_dir / "test-project"
            project_dir.mkdir(parents=True)

            # Create domain CLAUDE.md
            domain_claude = domain_dir / "CLAUDE.md"
            domain_claude.write_text("""
# Domain CLAUDE.md

## Tech Stack

| Layer | Standard |
|-------|----------|
| Frontend | React |
""")

            # Load hierarchy
            result = load_claude_md_hierarchy(forge_root, "test-domain", "test-project")

            assert "portfolio" in result
            assert "domain" in result
            assert "project" in result
            assert "merged" in result

            # Merged should have both tech stack entries
            assert result["merged"]["tech_stack"]["Backend"] == "FastAPI"
            assert result["merged"]["tech_stack"]["Frontend"] == "React"


class TestRequiresHumanGate:
    """Tests for human gate checking."""

    def test_coppa_data_collection_gate(self):
        """COPPA domain should require human gate for data collection."""
        requires, reason = requires_human_gate("thebrightharbor-com", "data_collection")
        assert requires is True
        assert "COPPA" in reason or "data_collection" in reason

    def test_payment_gate(self):
        """Domains with payment gates should require review."""
        requires, reason = requires_human_gate("codeswiftr-com", "payment")
        assert requires is True
        assert "payment" in reason.lower()

    def test_no_gate_needed(self):
        """Some actions should not require gates."""
        requires, reason = requires_human_gate("bogdanveliscu-com", "styling")
        assert requires is False
        assert reason == ""


class TestGetTechStack:
    """Tests for tech stack retrieval."""

    def test_react_domain(self):
        """React domains should have React frontend stack."""
        stack = get_tech_stack("codeswiftr-com")
        assert "React" in stack["frontend"]
        assert "FastAPI" in stack["backend"]

    def test_lit_pwa_domain(self):
        """Lit PWA domains should have Lit frontend stack."""
        stack = get_tech_stack("thebrightharbor-com")
        assert "Lit" in stack["frontend"]

    def test_react_native_domain(self):
        """React Native domains should have mobile frontend stack."""
        stack = get_tech_stack("babybit-es")
        assert "React Native" in stack["frontend"]

    def test_common_backend_stack(self):
        """All domains should have common backend stack."""
        for domain in ["codeswiftr-com", "thebrightharbor-com", "calmconnect-io"]:
            stack = get_tech_stack(domain)
            assert "FastAPI" in stack["backend"]
            assert "PostgreSQL" in stack["backend"]
            assert stack["package_manager"] == "Astral uv (MANDATORY)"

    def test_localization_in_stack(self):
        """Localized domains should have localization in stack."""
        stack = get_tech_stack("babybit-es")
        assert stack.get("localization") == "es"
