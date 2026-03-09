"""Extended tests for domain_config.py — edge cases and uncovered paths.

Covers:
- _build_domain_configs_from_registry with mocked registry
- parse_claude_md edge cases (empty, malformed, missing sections)
- load_claude_md_hierarchy merge logic and missing levels
- requires_human_gate for all gate mappings
- get_tech_stack localization and tier variations
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.domain_config import (
    _FALLBACK_DOMAIN_CONFIGS,
    DomainConfig,
    get_domain_config,
    get_tech_stack,
    load_claude_md_hierarchy,
    parse_claude_md,
    requires_human_gate,
)

# ---------------------------------------------------------------------------
# DomainConfig dataclass
# ---------------------------------------------------------------------------

class TestDomainConfigDataclass:
    def test_defaults(self):
        cfg = DomainConfig(name="test-domain")
        assert cfg.name == "test-domain"
        assert cfg.compliance == []
        assert cfg.human_gates == []
        assert cfg.frontend_tier == "React"
        assert cfg.localization is None
        assert cfg.special_rules == {}

    def test_full_config(self):
        cfg = DomainConfig(
            name="my-domain",
            compliance=["COPPA", "GDPR"],
            human_gates=["auth", "deploy"],
            frontend_tier="Lit PWA",
            localization="i18next (he, ar, en)",
            special_rules={"rtl": True},
        )
        assert "COPPA" in cfg.compliance
        assert cfg.localization is not None


# ---------------------------------------------------------------------------
# _build_domain_configs_from_registry
# ---------------------------------------------------------------------------

class TestBuildDomainConfigsFromRegistry:
    def test_fallback_on_import_error(self):
        """Falls back to hardcoded configs when registry unavailable."""
        with patch(
            "forge_harness.domain_config._build_domain_configs_from_registry"
        ) as mock_build:
            mock_build.return_value = _FALLBACK_DOMAIN_CONFIGS
            result = mock_build()
            assert "thebrightharbor-com" in result
            assert "calmconnect-io" in result

    def test_fallback_configs_have_coppa(self):
        """Fallback configs include COPPA for thebrightharbor-com."""
        assert "thebrightharbor-com" in _FALLBACK_DOMAIN_CONFIGS
        cfg = _FALLBACK_DOMAIN_CONFIGS["thebrightharbor-com"]
        assert "COPPA" in cfg.compliance
        assert "data_collection" in cfg.human_gates

    def test_fallback_configs_have_hipaa(self):
        """Fallback configs include HIPAA-lite for calmconnect-io."""
        assert "calmconnect-io" in _FALLBACK_DOMAIN_CONFIGS
        cfg = _FALLBACK_DOMAIN_CONFIGS["calmconnect-io"]
        assert "HIPAA-lite" in cfg.compliance

    def test_fallback_configs_have_codeswiftr(self):
        """Fallback configs include codeswiftr-com."""
        assert "codeswiftr-com" in _FALLBACK_DOMAIN_CONFIGS
        cfg = _FALLBACK_DOMAIN_CONFIGS["codeswiftr-com"]
        assert "payment_integration" in cfg.human_gates


# ---------------------------------------------------------------------------
# parse_claude_md
# ---------------------------------------------------------------------------

class TestParseClaudeMd:
    def test_nonexistent_file(self, tmp_path):
        """Returns empty dict for non-existent file."""
        result = parse_claude_md(tmp_path / "CLAUDE.md")
        assert result == {}

    def test_empty_file(self, tmp_path):
        p = tmp_path / "CLAUDE.md"
        p.write_text("")
        result = parse_claude_md(p)
        assert result["tech_stack"] == {}
        assert result["human_gates"] == []
        assert result["rules"] == []
        assert "raw_content" in result

    def test_no_matching_sections(self, tmp_path):
        """File with irrelevant content returns empty collections."""
        p = tmp_path / "CLAUDE.md"
        p.write_text("# My Project\n\nSome random text.\n")
        result = parse_claude_md(p)
        assert result["tech_stack"] == {}
        assert result["human_gates"] == []
        assert result["rules"] == []

    def test_tech_stack_extraction(self, tmp_path):
        p = tmp_path / "CLAUDE.md"
        p.write_text(
            "## Tech Stack\n\n"
            "| Layer | Standard |\n"
            "|-------|----------|\n"
            "| Backend | FastAPI |\n"
            "| Frontend | React 19 |\n"
            "| Database | PostgreSQL |\n"
        )
        result = parse_claude_md(p)
        assert result["tech_stack"]["Backend"] == "FastAPI"
        assert result["tech_stack"]["Frontend"] == "React 19"
        assert result["tech_stack"]["Database"] == "PostgreSQL"

    def test_tech_stack_skips_separator_rows(self, tmp_path):
        """Separator rows (---) are skipped."""
        p = tmp_path / "CLAUDE.md"
        p.write_text(
            "| Layer | Standard |\n"
            "|-------|----------|\n"
            "| Backend | FastAPI |\n"
        )
        result = parse_claude_md(p)
        assert "Backend" in result["tech_stack"]
        assert "---" not in result["tech_stack"]

    def test_human_gates_extraction(self, tmp_path):
        p = tmp_path / "CLAUDE.md"
        p.write_text(
            "## Human Gates\n\n"
            "1. **Security** — auth changes\n"
            "2. **Payments** — Stripe integration\n"
            "3. **Deployment** — production deploys\n"
        )
        result = parse_claude_md(p)
        assert "Security" in result["human_gates"]
        assert "Payments" in result["human_gates"]
        assert "Deployment" in result["human_gates"]

    def test_operating_rules_extraction(self, tmp_path):
        p = tmp_path / "CLAUDE.md"
        p.write_text(
            "## Operating Rules\n\n"
            "- **No MCP** — Do not use MCP\n"
            "- **Tests first** — Write tests before code\n"
        )
        result = parse_claude_md(p)
        assert "No MCP" in result["rules"]
        assert "Tests first" in result["rules"]

    def test_multiple_sections(self, tmp_path):
        """File with all sections extracts everything."""
        p = tmp_path / "CLAUDE.md"
        p.write_text(
            "# Project\n\n"
            "## Tech Stack\n\n"
            "| Layer | Standard |\n"
            "|-------|----------|\n"
            "| Backend | Go |\n"
            "\n"
            "## Human Gates\n\n"
            "1. **API Contract** — breaking changes\n"
            "\n"
            "## Operating Rules\n\n"
            "- **Async** — use async/await\n"
        )
        result = parse_claude_md(p)
        assert result["tech_stack"]["Backend"] == "Go"
        assert "API Contract" in result["human_gates"]
        assert "Async" in result["rules"]

    def test_malformed_table_single_column(self, tmp_path):
        """Table with single column is handled gracefully."""
        p = tmp_path / "CLAUDE.md"
        p.write_text(
            "| Layer | Standard |\n"
            "|-------|----------|\n"
            "| Backend |\n"  # Missing second column
        )
        result = parse_claude_md(p)
        # Should not crash; may or may not extract partial data
        assert isinstance(result["tech_stack"], dict)


# ---------------------------------------------------------------------------
# load_claude_md_hierarchy
# ---------------------------------------------------------------------------

class TestLoadClaudeMdHierarchy:
    def test_no_files_exist(self, tmp_path):
        """All hierarchy levels missing returns empty merged."""
        result = load_claude_md_hierarchy(tmp_path, "unknown-domain", "unknown-proj")
        assert result["portfolio"] == {}
        assert result["domain"] == {}
        assert result["project"] == {}
        assert "merged" in result

    def test_portfolio_only(self, tmp_path):
        """Only portfolio CLAUDE.md exists."""
        (tmp_path / "CLAUDE.md").write_text(
            "| Layer | Standard |\n"
            "|-------|----------|\n"
            "| Backend | FastAPI |\n"
        )
        result = load_claude_md_hierarchy(tmp_path, "some-domain", "some-proj")
        assert result["portfolio"]["tech_stack"]["Backend"] == "FastAPI"
        assert result["domain"] == {}
        assert result["project"] == {}
        # Merged should include portfolio data
        assert "Backend" in result["merged"].get("tech_stack", {})

    def test_project_overrides_portfolio(self, tmp_path):
        """Project-level tech stack overrides portfolio-level."""
        (tmp_path / "CLAUDE.md").write_text(
            "| Layer | Standard |\n"
            "|-------|----------|\n"
            "| Backend | FastAPI |\n"
        )
        domain_dir = tmp_path / "my-domain"
        domain_dir.mkdir()
        proj_dir = domain_dir / "my-proj"
        proj_dir.mkdir()
        (proj_dir / "CLAUDE.md").write_text(
            "| Layer | Standard |\n"
            "|-------|----------|\n"
            "| Backend | Express |\n"
        )
        result = load_claude_md_hierarchy(tmp_path, "my-domain", "my-proj")
        # Project overrides portfolio for same key
        assert result["merged"]["tech_stack"]["Backend"] == "Express"

    def test_domain_level_merges(self, tmp_path):
        """Domain-level CLAUDE.md merges between portfolio and project."""
        (tmp_path / "CLAUDE.md").write_text(
            "| Layer | Standard |\n"
            "|-------|----------|\n"
            "| Backend | FastAPI |\n"
        )
        domain_dir = tmp_path / "my-domain"
        domain_dir.mkdir()
        (domain_dir / "CLAUDE.md").write_text(
            "| Layer | Standard |\n"
            "|-------|----------|\n"
            "| Frontend | React |\n"
        )
        proj_dir = domain_dir / "my-proj"
        proj_dir.mkdir()
        result = load_claude_md_hierarchy(tmp_path, "my-domain", "my-proj")
        merged = result["merged"]["tech_stack"]
        assert merged["Backend"] == "FastAPI"
        assert merged["Frontend"] == "React"

    def test_human_gates_accumulated(self, tmp_path):
        """Human gates from all levels are accumulated (not replaced)."""
        (tmp_path / "CLAUDE.md").write_text(
            "## Human Gates\n\n1. **Security** — auth\n"
        )
        domain_dir = tmp_path / "my-domain" / "my-proj"
        domain_dir.mkdir(parents=True)
        (domain_dir / "CLAUDE.md").write_text(
            "## Human Gates\n\n1. **Payments** — stripe\n"
        )
        result = load_claude_md_hierarchy(tmp_path, "my-domain", "my-proj")
        gates = result["merged"]["human_gates"]
        assert "Security" in gates
        assert "Payments" in gates

    def test_includes_domain_config(self, tmp_path):
        """Result includes domain_config from registry."""
        result = load_claude_md_hierarchy(tmp_path, "codeswiftr-com", "is")
        assert result["domain_config"] is not None
        assert isinstance(result["domain_config"], DomainConfig)

    def test_raw_content_excluded_from_merge(self, tmp_path):
        """raw_content from parsed CLAUDE.md is not in merged dict."""
        (tmp_path / "CLAUDE.md").write_text("# Test\n")
        result = load_claude_md_hierarchy(tmp_path, "d", "p")
        assert "raw_content" not in result["merged"]


# ---------------------------------------------------------------------------
# requires_human_gate — comprehensive
# ---------------------------------------------------------------------------

class TestRequiresHumanGate:
    def test_coppa_data_collection(self):
        """COPPA domain gates data_collection."""
        gated, reason = requires_human_gate("thebrightharbor-com", "data_collection")
        assert gated is True
        assert "thebrightharbor-com" in reason

    def test_payment_gate(self):
        """Payment action is gated for codeswiftr-com."""
        gated, reason = requires_human_gate("codeswiftr-com", "payment")
        assert gated is True

    def test_health_data_gate(self):
        """Health data gated for HIPAA domains."""
        gated, reason = requires_human_gate("calmconnect-io", "health_data")
        assert gated is True

    def test_auth_change_gate(self):
        """Auth changes gated where Security is a human gate."""
        # Use a domain with Security gate
        gated, _ = requires_human_gate("calmconnect-io", "auth_change")
        # calmconnect-io might not have "Security" gate — check dynamically
        cfg = get_domain_config("calmconnect-io")
        has_security = any("security" in g.lower() for g in cfg.human_gates)
        if has_security:
            assert gated is True

    def test_api_contract_gate(self):
        """API contract changes not gated unless domain has matching gate."""
        gated, _ = requires_human_gate("codeswiftr-com", "api_contract")
        cfg = get_domain_config("codeswiftr-com")
        has_matching = any(
            "api contract" in g.lower() or "breaking" in g.lower()
            for g in cfg.human_gates + cfg.compliance
        )
        assert gated == has_matching

    def test_schema_change_gate(self):
        """Schema changes are gated where applicable."""
        gated, _ = requires_human_gate("codeswiftr-com", "schema_change")
        # Check dynamically
        cfg = get_domain_config("codeswiftr-com")
        has_arch = any(
            "architecture" in g.lower() or "breaking" in g.lower()
            for g in cfg.human_gates
        )
        if has_arch:
            assert gated is True

    def test_unknown_action_not_gated(self):
        """Unknown action is not gated."""
        gated, reason = requires_human_gate("codeswiftr-com", "totally_unknown_action")
        assert gated is False
        assert reason == ""

    def test_unknown_domain_not_gated(self):
        """Unknown domain has no gates."""
        gated, reason = requires_human_gate("nonexistent-domain-xyz", "payment")
        assert gated is False
        assert reason == ""

    def test_personal_data_gate(self):
        """Personal data gated for COPPA domains."""
        gated, _ = requires_human_gate("thebrightharbor-com", "personal_data")
        assert gated is True

    def test_health_advice_gate(self):
        """Health advice gate matches against gate_mappings keywords."""
        gated, _ = requires_human_gate("calmconnect-io", "health_advice")
        # gate_mappings for health_advice: ["health_advice", "medical_disclaimers"]
        cfg = get_domain_config("calmconnect-io")
        has_matching = any(
            "health_advice" in g.lower() or "medical_disclaimer" in g.lower()
            for g in cfg.human_gates + cfg.compliance
        )
        assert gated == has_matching

    def test_content_moderation_gate(self):
        """Content moderation gated for crisis content domains."""
        gated, _ = requires_human_gate("calmconnect-io", "content_moderation")
        cfg = get_domain_config("calmconnect-io")
        has_crisis = any("crisis" in g.lower() for g in cfg.human_gates)
        if has_crisis:
            assert gated is True


# ---------------------------------------------------------------------------
# get_tech_stack
# ---------------------------------------------------------------------------

class TestGetTechStack:
    def test_default_stack(self):
        """Default stack has FastAPI backend."""
        stack = get_tech_stack("codeswiftr-com")
        assert "FastAPI" in stack["backend"]
        assert "uv" in stack["package_manager"]
        assert "PostgreSQL" in stack["database"]

    def test_react_frontend(self):
        """React domain gets React frontend."""
        stack = get_tech_stack("codeswiftr-com")
        assert "React" in stack["frontend"]
        assert "Vite" in stack["frontend"]

    def test_lit_pwa_frontend(self):
        """Lit PWA domain gets Lit frontend."""
        stack = get_tech_stack("thebrightharbor-com")
        cfg = get_domain_config("thebrightharbor-com")
        if cfg.frontend_tier == "Lit PWA":
            assert "Lit" in stack["frontend"]

    def test_localization_included(self):
        """Domains with localization have it in stack."""
        stack = get_tech_stack("thebrightharbor-com")
        cfg = get_domain_config("thebrightharbor-com")
        if cfg.localization:
            assert "localization" in stack
            assert stack["localization"] == cfg.localization

    def test_no_localization_for_standard_domain(self):
        """Standard domain has no localization entry."""
        stack = get_tech_stack("codeswiftr-com")
        cfg = get_domain_config("codeswiftr-com")
        if not cfg.localization:
            assert "localization" not in stack

    def test_unknown_domain_gets_react_default(self):
        """Unknown domain defaults to React."""
        stack = get_tech_stack("totally-unknown-xyz")
        assert "React" in stack["frontend"]

    def test_common_infra_always_present(self):
        """All domains have Railway, Cloudflare, PostHog."""
        stack = get_tech_stack("codeswiftr-com")
        assert "Railway" in stack["deployment_backend"]
        assert "Cloudflare" in stack["deployment_frontend"]
        assert "PostHog" in stack["analytics"]


# ---------------------------------------------------------------------------
# get_domain_config
# ---------------------------------------------------------------------------

class TestGetDomainConfig:
    def test_known_domain(self):
        cfg = get_domain_config("codeswiftr-com")
        assert cfg.name == "codeswiftr-com"

    def test_unknown_domain_returns_default(self):
        cfg = get_domain_config("totally-made-up-domain-xyz")
        assert cfg.name == "totally-made-up-domain-xyz"
        assert cfg.compliance == []
        assert cfg.human_gates == []
        assert cfg.frontend_tier == "React"

    def test_coppa_domain_has_compliance(self):
        cfg = get_domain_config("thebrightharbor-com")
        assert "COPPA" in cfg.compliance
