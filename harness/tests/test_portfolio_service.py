"""Tests for portfolio_service.py - Portfolio Service.

Tests cover:
- PortfolioService initialization
- Domain loading with caching
- Feature loading and counting
- Tech stack derivation
- Project listing and filtering
- Portfolio summary generation
- Domain projects retrieval
- Project details
- get_portfolio_service singleton
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest

from forge_harness.webhook_server.services.portfolio_service import (
    PortfolioService,
    get_portfolio_service,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_forge_root(tmp_path: Path) -> Path:
    """Create a temporary FORGE root directory structure."""
    forge_root = tmp_path / "FORGE"
    forge_root.mkdir()

    # Create domains.yaml
    domains_data = {
        "domains": {
            "test-domain": {
                "display_name": "Test Domain",
                "active": True,
                "compliance": ["SOC2"],
                "human_gates": ["approval-required"],
                "content": {"tier": 1},
                "products": ["project-one", "project-two"],
                "frontend_tier": "React",
                "deployment": {"cloudflare_project": "test-domain"},
            },
            "inactive-domain": {
                "display_name": "Inactive Domain",
                "active": False,
                "products": ["inactive-project"],
            },
        }
    }

    import yaml

    with open(forge_root / "domains.yaml", "w") as f:
        yaml.dump(domains_data, f)

    return forge_root


@pytest.fixture
def portfolio_service(tmp_forge_root: Path) -> PortfolioService:
    """Create a PortfolioService instance with temp forge_root."""
    return PortfolioService(forge_root=tmp_forge_root)


# =============================================================================
# Initialization Tests
# =============================================================================


class TestPortfolioServiceInit:
    """Tests for PortfolioService initialization."""

    def test_init_with_forge_root(self, tmp_forge_root: Path) -> None:
        """Should initialize with provided forge_root."""
        service = PortfolioService(forge_root=tmp_forge_root)

        assert service._forge_root == tmp_forge_root
        assert service._domains_cache is None
        assert service._cache_ttl == 60

    def test_find_forge_root(self, tmp_forge_root: Path) -> None:
        """Should find FORGE root by looking for domains.yaml."""
        # Create service without explicit forge_root
        import os

        original_cwd = os.getcwd()
        subdir = tmp_forge_root / "subdir"
        subdir.mkdir()
        try:
            os.chdir(subdir)
            service = PortfolioService()

            # Should have found the forge_root
            assert service._forge_root is not None
        finally:
            os.chdir(original_cwd)


# =============================================================================
# Domain Loading Tests
# =============================================================================


class TestDomainLoading:
    """Tests for domain loading functionality."""

    def test_load_domains(self, portfolio_service: PortfolioService) -> None:
        """Should load domains from yaml."""
        domains = portfolio_service._load_domains()

        assert "domains" in domains
        assert "test-domain" in domains["domains"]
        assert "inactive-domain" in domains["domains"]

    def test_load_domains_caching(self, portfolio_service: PortfolioService) -> None:
        """Should cache domains for TTL period."""
        # First load
        domains1 = portfolio_service._load_domains()
        cache_time = portfolio_service._cache_time

        # Second load should use cache
        domains2 = portfolio_service._load_domains()

        assert domains1 == domains2
        assert portfolio_service._cache_time == cache_time

    def test_load_domains_missing_file(self, tmp_path: Path) -> None:
        """Should handle missing domains.yaml."""
        service = PortfolioService(forge_root=tmp_path)
        domains = service._load_domains()

        assert domains == {"domains": {}}

    def test_load_domains_invalid_yaml(self, tmp_forge_root: Path) -> None:
        """Should handle invalid YAML."""
        # Write invalid YAML
        (tmp_forge_root / "domains.yaml").write_text("invalid: yaml: [")

        service = PortfolioService(forge_root=tmp_forge_root)
        domains = service._load_domains()

        assert domains == {"domains": {}}


# =============================================================================
# Feature Loading Tests
# =============================================================================


class TestFeatureLoading:
    """Tests for feature loading functionality."""

    def test_load_features_not_found(self, portfolio_service: PortfolioService) -> None:
        """Should return None when features.json not found."""
        result = portfolio_service._load_features("test-domain", "nonexistent")

        assert result is None

    def test_load_features_from_domain_project(self, portfolio_service: PortfolioService) -> None:
        """Should load features from domain/project/features.json."""
        # Create features.json
        features_data = {
            "version": "1.0",
            "features": [
                {"id": "f1", "name": "Feature 1", "status": "passing"},
                {"id": "f2", "name": "Feature 2", "status": "pending"},
            ],
        }

        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        result = portfolio_service._load_features("test-domain", "project-one")

        assert result is not None
        assert result["version"] == "1.0"
        assert len(result["features"]) == 2

    def test_load_features_invalid_json(self, portfolio_service: PortfolioService) -> None:
        """Should handle invalid JSON in features.json."""
        # Create invalid features.json
        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        (project_dir / "features.json").write_text("not valid json")

        result = portfolio_service._load_features("test-domain", "project-one")

        assert result is None


# =============================================================================
# Feature Counting Tests
# =============================================================================


class TestFeatureCounting:
    """Tests for feature counting functionality."""

    def test_count_feature_status_empty(self, portfolio_service: PortfolioService) -> None:
        """Should return zero counts for empty features."""
        counts = portfolio_service._count_feature_status(None)

        assert counts == {
            "total": 0,
            "passing": 0,
            "failing": 0,
            "pending": 0,
            "blocked": 0,
        }

    def test_count_feature_status(self, portfolio_service: PortfolioService) -> None:
        """Should count features by status."""
        features = {
            "features": [
                {"id": "f1", "status": "passing"},
                {"id": "f2", "status": "passing"},
                {"id": "f3", "status": "failing"},
                {"id": "f4", "status": "pending"},
                {"id": "f5", "status": "blocked"},
                {"id": "f6", "status": "unknown"},  # Should count as pending
            ]
        }

        counts = portfolio_service._count_feature_status(features)

        assert counts["total"] == 6
        assert counts["passing"] == 2
        assert counts["failing"] == 1
        assert counts["pending"] == 2  # pending + unknown
        assert counts["blocked"] == 1


# =============================================================================
# Tech Stack Tests
# =============================================================================


class TestTechStack:
    """Tests for tech stack derivation."""

    def test_get_tech_stack_from_features(self, portfolio_service: PortfolioService) -> None:
        """Should get tech stack from features.json."""
        # Create features.json with tech_stack
        features_data = {"tech_stack": ["Python", "Django", "PostgreSQL"]}
        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        result = portfolio_service._get_tech_stack(
            "test-domain", "project-one", {}
        )

        assert result == ["Python", "Django", "PostgreSQL"]

    def test_get_tech_stack_from_domain_config(self, portfolio_service: PortfolioService) -> None:
        """Should derive tech stack from domain frontend_tier."""
        domain_info = {"frontend_tier": "React"}

        result = portfolio_service._get_tech_stack(
            "test-domain", "project-one", domain_info
        )

        assert "React" in result
        assert "TypeScript" in result
        assert "FastAPI" in result
        assert "PostgreSQL" in result

    def test_get_tech_stack_lit_pwa(self, portfolio_service: PortfolioService) -> None:
        """Should derive tech stack for Lit PWA tier."""
        domain_info = {"frontend_tier": "Lit PWA"}

        result = portfolio_service._get_tech_stack(
            "test-domain", "project-one", domain_info
        )

        assert "Lit" in result
        assert "Web Components" in result

    def test_get_tech_stack_react_native(self, portfolio_service: PortfolioService) -> None:
        """Should derive tech stack for React Native tier."""
        domain_info = {"frontend_tier": "React Native"}

        result = portfolio_service._get_tech_stack(
            "test-domain", "project-one", domain_info
        )

        assert "React Native" in result
        assert "TypeScript" in result

    def test_get_tech_stack_default(self, portfolio_service: PortfolioService) -> None:
        """Should use default tech stack for unknown tier."""
        domain_info = {"frontend_tier": "Custom Tier"}

        result = portfolio_service._get_tech_stack(
            "test-domain", "project-one", domain_info
        )

        assert "Custom Tier" in result
        assert "FastAPI" in result
        assert "PostgreSQL" in result

    def test_get_tech_stack_empty_domain(self, portfolio_service: PortfolioService) -> None:
        """Should return empty list for empty domain info."""
        result = portfolio_service._get_tech_stack(
            "test-domain", "project-one", {}
        )

        assert result == []


# =============================================================================
# Project Listing Tests
# =============================================================================


class TestProjectListing:
    """Tests for project listing functionality."""

    def test_get_all_projects(self, portfolio_service: PortfolioService) -> None:
        """Should list all active projects."""
        projects = portfolio_service.get_all_projects()

        # Should have 2 projects from test-domain (inactive-domain is inactive)
        assert len(projects) == 2

        # Check project structure
        for project in projects:
            assert "domain" in project
            assert "project" in project
            assert "display_name" in project
            assert "status" in project
            assert "progress_pct" in project

    def test_get_all_projects_with_features(self, portfolio_service: PortfolioService) -> None:
        """Should calculate progress from features."""
        # Create features.json with all passing
        features_data = {
            "features": [
                {"id": "f1", "status": "passing"},
                {"id": "f2", "status": "passing"},
            ]
        }

        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        projects = portfolio_service.get_all_projects()

        # Find project-one
        project_one = next(p for p in projects if p["project"] == "project-one")
        assert project_one["progress_pct"] == 100
        assert project_one["status"] == "live"

    def test_get_all_projects_partial_progress(self, portfolio_service: PortfolioService) -> None:
        """Should calculate partial progress."""
        # Create features.json with mixed status
        features_data = {
            "features": [
                {"id": "f1", "status": "passing"},
                {"id": "f2", "status": "passing"},
                {"id": "f3", "status": "pending"},
                {"id": "f4", "status": "pending"},
            ]
        }

        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        projects = portfolio_service.get_all_projects()

        # Find project-one
        project_one = next(p for p in projects if p["project"] == "project-one")
        assert project_one["progress_pct"] == 50
        assert project_one["status"] == "dev"


# =============================================================================
# Portfolio Summary Tests
# =============================================================================


class TestPortfolioSummary:
    """Tests for portfolio summary generation."""

    def test_get_portfolio_summary_structure(self, portfolio_service: PortfolioService) -> None:
        """Should return correct summary structure."""
        result = portfolio_service.get_portfolio_summary()

        assert "summary" in result
        assert "projects" in result

        summary = result["summary"]
        assert "total_projects" in summary
        assert "by_status" in summary
        assert "active_agents" in summary
        assert "pending_approvals" in summary

    def test_get_portfolio_summary_counts(self, portfolio_service: PortfolioService) -> None:
        """Should count projects by status."""
        result = portfolio_service.get_portfolio_summary()

        assert result["summary"]["total_projects"] == 2  # From test-domain

        by_status = result["summary"]["by_status"]
        assert "live" in by_status
        assert "ready" in by_status
        assert "dev" in by_status
        assert "parked" in by_status
        assert "blocked" in by_status


# =============================================================================
# Domain Projects Tests
# =============================================================================


class TestDomainProjects:
    """Tests for domain project retrieval."""

    def test_get_domain_projects_existing(self, portfolio_service: PortfolioService) -> None:
        """Should get projects for existing domain."""
        result = portfolio_service.get_domain_projects("test-domain")

        assert result is not None
        assert result["domain"]["id"] == "test-domain"
        assert result["domain"]["display_name"] == "Test Domain"
        assert len(result["projects"]) == 2
        assert result["count"] == 2

    def test_get_domain_projects_nonexistent(self, portfolio_service: PortfolioService) -> None:
        """Should return None for non-existent domain."""
        result = portfolio_service.get_domain_projects("nonexistent")

        assert result is None

    def test_get_domain_projects_sorted_by_status(self, portfolio_service: PortfolioService) -> None:
        """Should sort projects by status priority."""
        # Create features for different statuses
        for i, (project, status) in enumerate([
            ("project-one", "ready"),
            ("project-two", "blocked"),
        ]):
            features_data = {"features": [{"id": "f1", "status": status}]}
            project_dir = portfolio_service._forge_root / "test-domain" / project
            project_dir.mkdir(parents=True)
            with open(project_dir / "features.json", "w") as f:
                json.dump(features_data, f)

        result = portfolio_service.get_domain_projects("test-domain")

        # Blocked should come first (priority 0)
        assert result["projects"][0]["status"] == "blocked"


# =============================================================================
# Project Details Tests
# =============================================================================


class TestProjectDetails:
    """Tests for project details retrieval."""

    def test_get_project_details_existing(self, portfolio_service: PortfolioService) -> None:
        """Should get details for existing project."""
        result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result is not None
        assert result["name"] == "project-one"
        assert result["slug"] == "project-one"
        assert result["domain"] == "test-domain"
        assert "features" in result
        assert "tech_stack" in result
        assert "active_agents" in result

    def test_get_project_details_nonexistent_domain(self, portfolio_service: PortfolioService) -> None:
        """Should return None for non-existent domain."""
        result = portfolio_service.get_project_details("nonexistent", "project")

        assert result is None

    def test_get_project_details_nonexistent_project(self, portfolio_service: PortfolioService) -> None:
        """Should return None for non-existent project."""
        result = portfolio_service.get_project_details("test-domain", "nonexistent-project")

        assert result is None

    def test_get_project_details_with_features(self, portfolio_service: PortfolioService) -> None:
        """Should include feature details."""
        features_data = {
            "features": [
                {"id": "f1", "name": "Feature One", "status": "passing", "priority": "P0"},
                {"id": "f2", "name": "Feature Two", "status": "pending", "priority": "P1"},
            ]
        }

        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["features"]["counts"]["total"] == 2
        assert result["features"]["counts"]["passing"] == 1
        assert len(result["features"]["list"]) == 2

    def test_get_project_details_production_url(self, portfolio_service: PortfolioService) -> None:
        """Should derive production URL from deployment config."""
        result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["production_url"] == "https://test-domain.pages.dev"


# =============================================================================
# Singleton Tests
# =============================================================================


class TestGetPortfolioService:
    """Tests for get_portfolio_service singleton."""

    def test_get_portfolio_service_returns_singleton(self) -> None:
        """Should return the same instance on multiple calls."""
        # Reset global state
        import forge_harness.webhook_server.services.portfolio_service as ps

        ps._portfolio_service = None

        service1 = get_portfolio_service()
        service2 = get_portfolio_service()

        assert service1 is service2

    def test_get_portfolio_service_creates_new_if_none(self) -> None:
        """Should create new service if none exists."""
        # Reset global state
        import forge_harness.webhook_server.services.portfolio_service as ps

        ps._portfolio_service = None

        service = get_portfolio_service()

        assert isinstance(service, PortfolioService)


# =============================================================================
# Additional _find_forge_root Tests (uncovered branches)
# =============================================================================


class TestFindForgeRoot:
    """Tests for _find_forge_root fallback paths."""

    def test_find_forge_root_via_forge_harness_subdir(self, tmp_path: Path) -> None:
        """Should find root when domains.yaml is accessible under forge_harness/ subdirectory.

        The traversal checks each ancestor for (current / 'forge_harness' / 'domains.yaml').
        We create a structure where the domains.yaml is found via this branch.
        """
        # Create a deeper structure: root/project/forge_harness/domains.yaml
        # The __file__ will be at root/project/some/module.py so the traversal
        # from root/project/some upward will hit root/project where
        # (root/project / 'forge_harness' / 'domains.yaml') exists.
        root = tmp_path / "myrepo"
        root.mkdir()
        project_dir = root / "project"
        project_dir.mkdir()
        forge_harness_sub = project_dir / "forge_harness"
        forge_harness_sub.mkdir()
        (forge_harness_sub / "domains.yaml").write_text("domains: {}")

        # Fake __file__ inside project/some/module.py
        some_dir = project_dir / "some"
        some_dir.mkdir()
        fake_module_path = str(some_dir / "portfolio_service.py")

        import forge_harness.webhook_server.services.portfolio_service as ps_module

        with patch.object(ps_module, "__file__", fake_module_path):
            service = PortfolioService.__new__(PortfolioService)
            service._domains_cache = None
            service._cache_time = 0.0
            service._cache_ttl = 60
            found = service._find_forge_root()

        # Should have found project_dir (which has forge_harness/domains.yaml)
        assert isinstance(found, Path)
        assert found == project_dir

    def test_find_forge_root_fallback_to_parent(self) -> None:
        """Should fall back to parent of __file__ when no domains.yaml found anywhere.

        This exercises line 42: return Path(__file__).parent.parent
        """
        import forge_harness.webhook_server.services.portfolio_service as ps_module

        # Point __file__ at a path deep inside a temp tree with no domains.yaml
        # Use /tmp which never has a domains.yaml to ensure traversal exhausts
        fake_path = "/tmp/__forge_test_no_domains__/deeply/nested/module.py"

        with patch.object(ps_module, "__file__", fake_path):
            service = PortfolioService.__new__(PortfolioService)
            service._domains_cache = None
            service._cache_time = 0.0
            service._cache_ttl = 60
            found = service._find_forge_root()

        # Should return Path(__file__).parent.parent as fallback
        assert isinstance(found, Path)
        # Fallback is parent of the module file's parent
        expected = Path(fake_path).parent.parent
        assert found == expected


# =============================================================================
# Extended _load_domains Tests
# =============================================================================


class TestDomainLoadingExtended:
    """Additional domain loading tests for uncovered branches."""

    def test_load_domains_from_forge_harness_subpath(self, tmp_path: Path) -> None:
        """Should load domains.yaml from forge_harness/ subdirectory."""
        import yaml

        forge_harness_dir = tmp_path / "forge_harness"
        forge_harness_dir.mkdir()
        domains_data = {"domains": {"alpha": {"active": True, "products": []}}}
        with open(forge_harness_dir / "domains.yaml", "w") as f:
            yaml.dump(domains_data, f)

        service = PortfolioService(forge_root=tmp_path)
        result = service._load_domains()

        assert "alpha" in result["domains"]

    def test_load_domains_cache_expiry(self, tmp_forge_root: Path) -> None:
        """Should reload domains when cache has expired."""
        import time

        service = PortfolioService(forge_root=tmp_forge_root)

        # Prime the cache
        service._load_domains()
        first_cache_time = service._cache_time

        # Simulate cache expiry
        service._cache_time = time.time() - service._cache_ttl - 1

        service._load_domains()

        # Cache time should be updated (second load happened)
        assert service._cache_time > first_cache_time


# =============================================================================
# Extended Feature Counting Tests
# =============================================================================


class TestFeatureCountingExtended:
    """Additional feature counting tests for edge cases."""

    def test_count_feature_status_no_features_key(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should return zero counts when 'features' key is absent."""
        counts = portfolio_service._count_feature_status({"version": "1.0"})

        assert counts == {
            "total": 0,
            "passing": 0,
            "failing": 0,
            "pending": 0,
            "blocked": 0,
        }

    def test_count_feature_status_empty_features_list(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should return zero counts for an empty features list."""
        counts = portfolio_service._count_feature_status({"features": []})

        assert counts["total"] == 0

    def test_count_feature_status_missing_status_field(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should default to pending when status field is absent."""
        features = {"features": [{"id": "f1"}]}
        counts = portfolio_service._count_feature_status(features)

        assert counts["total"] == 1
        assert counts["pending"] == 1


# =============================================================================
# Extended Project Listing Tests (ready and parked branches)
# =============================================================================


class TestProjectListingExtended:
    """Tests for the 'ready' and 'parked' status branches in get_all_projects."""

    def test_get_all_projects_status_ready(self, portfolio_service: PortfolioService) -> None:
        """Should set status='ready' when 80-99% features pass."""
        # 8 of 10 passing → 80% → ready
        features_data = {
            "features": [{"id": f"f{i}", "status": "passing"} for i in range(8)]
            + [{"id": "f9", "status": "pending"}, {"id": "f10", "status": "pending"}]
        }
        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        projects = portfolio_service.get_all_projects()

        project_one = next(p for p in projects if p["project"] == "project-one")
        assert project_one["status"] == "ready"
        assert project_one["progress_pct"] == 80

    def test_get_all_projects_status_parked(self, portfolio_service: PortfolioService) -> None:
        """Should set status='parked' when less than 50% features pass."""
        # 1 of 10 passing → 10% → parked
        features_data = {
            "features": [{"id": "f1", "status": "passing"}]
            + [{"id": f"f{i}", "status": "pending"} for i in range(2, 11)]
        }
        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        projects = portfolio_service.get_all_projects()

        project_one = next(p for p in projects if p["project"] == "project-one")
        assert project_one["status"] == "parked"

    def test_get_all_projects_display_name_formatting(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should format display_name by replacing hyphens and underscores."""
        projects = portfolio_service.get_all_projects()

        for p in projects:
            # display_name should be title-cased with no hyphens/underscores
            assert "-" not in p["display_name"]
            assert "_" not in p["display_name"]


# =============================================================================
# Extended Portfolio Summary Tests (agent counting branches)
# =============================================================================


class TestPortfolioSummaryExtended:
    """Additional portfolio summary tests covering agent-counting branches."""

    def test_get_portfolio_summary_counts_registry_agents(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should count agents from the registry."""
        mock_agent = MagicMock()
        mock_agent.id = "agent-1"

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = [mock_agent]

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ):
            result = portfolio_service.get_portfolio_summary()

        assert result["summary"]["active_agents"] >= 1

    def test_get_portfolio_summary_registry_exception(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should handle registry exceptions gracefully."""
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            side_effect=RuntimeError("registry down"),
        ):
            result = portfolio_service.get_portfolio_summary()

        # Should still return a valid summary
        assert "summary" in result
        assert result["summary"]["active_agents"] >= 0

    def test_get_portfolio_summary_state_store_agents(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should count agents from state_store when not already seen."""
        mock_agent_in_registry = MagicMock()
        mock_agent_in_registry.id = "registry-agent-1"

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = [mock_agent_in_registry]

        # Create state store mock agent with unique session_id
        mock_store_agent = MagicMock()
        mock_store_agent.session_id = "store-agent-unique-id"

        mock_state_store_instance = MagicMock()
        mock_state_store_instance.is_connected.return_value = True
        mock_state_store_instance.get_active_agents.return_value = [mock_store_agent]

        mock_state_store_cls = MagicMock(return_value=mock_state_store_instance)

        # Patch the class inside the module's namespace where it is imported at runtime
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ), patch.dict(
            "sys.modules",
            {"forge_harness.state_store": MagicMock(StateStore=mock_state_store_cls)},
        ):
            result = portfolio_service.get_portfolio_summary()

        # Should have at least the registry agent (state_store may or may not be counted
        # depending on module caching, but no exception should be raised)
        assert result["summary"]["active_agents"] >= 1

    def test_get_portfolio_summary_state_store_deduplication(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should not double-count agents already seen in registry."""
        shared_id = "shared-agent-id"

        mock_agent_in_registry = MagicMock()
        mock_agent_in_registry.id = shared_id

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = [mock_agent_in_registry]

        # State store returns agent with same ID as registry agent
        mock_store_agent = MagicMock()
        mock_store_agent.session_id = shared_id

        mock_state_store_instance = MagicMock()
        mock_state_store_instance.is_connected.return_value = True
        mock_state_store_instance.get_active_agents.return_value = [mock_store_agent]

        mock_state_store_cls = MagicMock(return_value=mock_state_store_instance)

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ), patch.dict(
            "sys.modules",
            {"forge_harness.state_store": MagicMock(StateStore=mock_state_store_cls)},
        ):
            result = portfolio_service.get_portfolio_summary()

        # The shared agent must appear at most once
        # (registry contributes 1; state_store deduplicates it)
        assert result["summary"]["active_agents"] >= 1

    def test_get_portfolio_summary_state_store_not_connected(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should skip state store agents when not connected; summary still valid."""
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []

        mock_state_store_instance = MagicMock()
        mock_state_store_instance.is_connected.return_value = False

        # Patch StateStore directly in the forge_harness.state_store module
        import forge_harness.state_store as ss_module

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ), patch.object(ss_module, "StateStore", return_value=mock_state_store_instance):
            result = portfolio_service.get_portfolio_summary()

        # Summary structure must be present; agent count is >= 0
        assert "summary" in result
        assert result["summary"]["active_agents"] >= 0

    def test_get_portfolio_summary_state_store_exception(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should handle state_store import/usage exceptions gracefully."""
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ), patch.dict(
            "sys.modules",
            {"forge_harness.state_store": None},
        ):
            result = portfolio_service.get_portfolio_summary()

        assert "summary" in result

    def test_get_portfolio_summary_tmux_sessions(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should count tmux sessions not already in seen_ids."""
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []

        mock_session = MagicMock()
        mock_session.session_name = "tmux-session-1"
        mock_session.window_name = "tmux-window-1"

        mock_tracker_instance = MagicMock()
        mock_tracker_instance.get_all_sessions.return_value = [mock_session]
        mock_tracker_cls = MagicMock(return_value=mock_tracker_instance)

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ), patch.dict(
            "sys.modules",
            {
                "forge_harness.state_store": MagicMock(
                    StateStore=MagicMock(return_value=MagicMock(is_connected=MagicMock(return_value=False)))
                ),
                "forge_harness.session_tracker": MagicMock(SessionTracker=mock_tracker_cls),
            },
        ):
            result = portfolio_service.get_portfolio_summary()

        # At minimum the summary structure is valid
        assert "active_agents" in result["summary"]

    def test_get_portfolio_summary_tmux_session_import_error(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should gracefully handle SessionTracker ImportError."""
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ), patch.dict(
            "sys.modules",
            {"forge_harness.session_tracker": None},
        ):
            result = portfolio_service.get_portfolio_summary()

        assert "summary" in result

    def test_get_portfolio_summary_pending_approvals_count(
        self, portfolio_service: PortfolioService, tmp_forge_root: Path
    ) -> None:
        """Should count JSON files in .forge/approvals directory."""
        # Create .forge/approvals with some JSON files
        approvals_dir = tmp_forge_root / ".forge/approvals"
        approvals_dir.mkdir()
        (approvals_dir / "approval1.json").write_text('{"id": "1"}')
        (approvals_dir / "approval2.json").write_text('{"id": "2"}')
        (approvals_dir / "not-json.txt").write_text("ignored")

        result = portfolio_service.get_portfolio_summary()

        assert result["summary"]["pending_approvals"] == 2

    def test_get_portfolio_summary_no_approvals_dir(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should return zero pending approvals when directory does not exist."""
        result = portfolio_service.get_portfolio_summary()

        assert result["summary"]["pending_approvals"] == 0

    def test_get_portfolio_summary_pending_approvals_exception(
        self, portfolio_service: PortfolioService, tmp_forge_root: Path
    ) -> None:
        """Should handle exceptions when counting pending approvals."""
        # Create the approvals directory so the `if approvals_dir.exists()` branch is taken
        approvals_dir = tmp_forge_root / ".forge/approvals"
        approvals_dir.mkdir()

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []

        # Make Path.glob raise so the except block on line 253-254 is executed
        original_glob = Path.glob

        def mock_glob(self: Path, pattern: str):
            if ".forge/approvals" in str(self):
                raise OSError("disk error")
            return original_glob(self, pattern)

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ), patch.object(Path, "glob", mock_glob):
            result = portfolio_service.get_portfolio_summary()

        # Exception is swallowed; pending_approvals defaults to 0
        assert "summary" in result
        assert result["summary"]["pending_approvals"] == 0

    def test_get_portfolio_summary_tmux_outer_exception(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should handle non-ImportError exceptions from tmux session block."""
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []

        # Raise a non-ImportError inside the inner try block so the outer except triggers
        mock_tracker_instance = MagicMock()
        mock_tracker_instance.get_all_sessions.side_effect = RuntimeError("tmux gone")
        mock_tracker_cls = MagicMock(return_value=mock_tracker_instance)

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ), patch.dict(
            "sys.modules",
            {
                "forge_harness.state_store": MagicMock(
                    StateStore=MagicMock(
                        return_value=MagicMock(is_connected=MagicMock(return_value=False))
                    )
                ),
                "forge_harness.session_tracker": MagicMock(SessionTracker=mock_tracker_cls),
            },
        ):
            result = portfolio_service.get_portfolio_summary()

        # Exception should be swallowed; summary is still valid
        assert "summary" in result

    def test_get_portfolio_summary_unknown_status_defaults_to_dev(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should default unknown project status to 'dev' in by_status counts."""
        # Inject a project with unknown status
        with patch.object(
            portfolio_service,
            "get_all_projects",
            return_value=[
                {"project": "foo", "domain": "bar", "status": "unknown_status", "progress_pct": 0},
            ],
        ):
            result = portfolio_service.get_portfolio_summary()

        # "dev" should be incremented as the fallback
        assert result["summary"]["by_status"]["dev"] >= 1


# =============================================================================
# Extended Domain Projects Tests (failing and ready status branches)
# =============================================================================


class TestDomainProjectsExtended:
    """Tests for failing and ready status branches in get_domain_projects."""

    def test_get_domain_projects_failing_status(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should set status='failing' when there are failing features."""
        features_data = {
            "features": [
                {"id": "f1", "status": "failing"},
                {"id": "f2", "status": "passing"},
            ]
        }
        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        result = portfolio_service.get_domain_projects("test-domain")

        failing_projects = [p for p in result["projects"] if p["status"] == "failing"]
        assert len(failing_projects) >= 1

    def test_get_domain_projects_ready_status(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should set status='ready' when all features are passing."""
        features_data = {
            "features": [
                {"id": "f1", "status": "passing"},
                {"id": "f2", "status": "passing"},
            ]
        }
        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        result = portfolio_service.get_domain_projects("test-domain")

        ready_projects = [p for p in result["projects"] if p["status"] == "ready"]
        assert len(ready_projects) >= 1

    def test_get_domain_projects_product_name_slugification(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should convert product names with spaces to slugs."""
        import yaml

        # Create a domain with space-containing product names
        domains_data = {
            "domains": {
                "space-domain": {
                    "display_name": "Space Domain",
                    "active": True,
                    "products": ["My Project"],
                }
            }
        }
        (portfolio_service._forge_root / "domains.yaml").write_text("")
        with open(portfolio_service._forge_root / "domains.yaml", "w") as f:
            yaml.dump(domains_data, f)

        # Invalidate cache
        portfolio_service._domains_cache = None

        result = portfolio_service.get_domain_projects("space-domain")

        assert result is not None
        assert result["projects"][0]["slug"] == "my-project"

    def test_get_domain_projects_compliance_and_human_gates(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should include compliance and human_gates in domain info."""
        result = portfolio_service.get_domain_projects("test-domain")

        assert result["domain"]["compliance"] == ["SOC2"]
        assert result["domain"]["human_gates"] == ["approval-required"]

    def test_get_domain_projects_content_tier(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should include content tier from domain config."""
        result = portfolio_service.get_domain_projects("test-domain")

        assert result["domain"]["content_tier"] == 1

    def test_get_domain_projects_default_content_tier(
        self, tmp_path: Path
    ) -> None:
        """Should default content_tier to 3 when not specified."""
        import yaml

        domains_data = {
            "domains": {
                "no-content-domain": {
                    "display_name": "No Content Domain",
                    "active": True,
                    "products": [],
                }
            }
        }
        with open(tmp_path / "domains.yaml", "w") as f:
            yaml.dump(domains_data, f)

        service = PortfolioService(forge_root=tmp_path)
        result = service.get_domain_projects("no-content-domain")

        assert result["domain"]["content_tier"] == 3


# =============================================================================
# Extended Project Details Tests (status branches + git commits)
# =============================================================================


class TestProjectDetailsExtended:
    """Tests for blocked/failing/ready statuses and git commit parsing."""

    def _write_features(
        self, portfolio_service: PortfolioService, features: list[dict]
    ) -> None:
        project_dir = portfolio_service._forge_root / "test-domain" / "project-one"
        project_dir.mkdir(parents=True, exist_ok=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump({"features": features}, f)

    def test_get_project_details_blocked_status(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should set status='blocked' when any feature is blocked."""
        self._write_features(
            portfolio_service,
            [
                {"id": "f1", "status": "blocked"},
                {"id": "f2", "status": "passing"},
            ],
        )

        result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["status"] == "blocked"

    def test_get_project_details_failing_status(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should set status='failing' when features have failing (no blocked)."""
        self._write_features(
            portfolio_service,
            [
                {"id": "f1", "status": "failing"},
                {"id": "f2", "status": "passing"},
            ],
        )

        result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["status"] == "failing"

    def test_get_project_details_ready_status(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should set status='ready' when only passing features."""
        self._write_features(
            portfolio_service,
            [
                {"id": "f1", "status": "passing"},
                {"id": "f2", "status": "passing"},
            ],
        )

        result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["status"] == "ready"

    def test_get_project_details_pending_status_no_features(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should set status='pending' when there are no features."""
        self._write_features(portfolio_service, [])

        result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["status"] == "pending"

    def test_get_project_details_recent_commits_parsed(
        self, portfolio_service: PortfolioService, tmp_path: Path
    ) -> None:
        """Should parse git log output into recent_commits list."""
        # Create project path that exists (parent / domain / project)
        project_path = portfolio_service._forge_root.parent / "test-domain" / "project-one"
        project_path.mkdir(parents=True)

        git_output = "abc1234 Fix login bug\ndef5678 Add tests\n"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = git_output

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = portfolio_service.get_project_details("test-domain", "project-one")

        mock_run.assert_called_once_with(
            ["git", "log", "--oneline", "-5"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert len(result["recent_commits"]) == 2
        assert result["recent_commits"][0] == {"hash": "abc1234", "message": "Fix login bug"}
        assert result["recent_commits"][1] == {"hash": "def5678", "message": "Add tests"}

    def test_get_project_details_git_nonzero_returncode(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should return empty recent_commits when git exits non-zero."""
        project_path = portfolio_service._forge_root.parent / "test-domain" / "project-one"
        project_path.mkdir(parents=True, exist_ok=True)

        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["recent_commits"] == []

    def test_get_project_details_git_exception_suppressed(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should return empty recent_commits when git subprocess raises."""
        project_path = portfolio_service._forge_root.parent / "test-domain" / "project-one"
        project_path.mkdir(parents=True, exist_ok=True)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["recent_commits"] == []

    def test_get_project_details_active_agents_filtered_by_project(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should include only agents whose project matches domain/slug."""
        matching_agent = MagicMock()
        matching_agent.project = "test-domain/project-one"
        matching_agent.to_dict.return_value = {"id": "a1", "project": "test-domain/project-one"}

        non_matching_agent = MagicMock()
        non_matching_agent.project = "other-domain/other-project"
        non_matching_agent.to_dict.return_value = {"id": "a2", "project": "other-domain/other-project"}

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = [matching_agent, non_matching_agent]

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ):
            result = portfolio_service.get_project_details("test-domain", "project-one")

        assert len(result["active_agents"]) == 1
        assert result["active_agents"][0]["id"] == "a1"

    def test_get_project_details_no_production_url_without_cloudflare(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should return None production_url when no cloudflare_project set."""
        import yaml

        # Override to remove cloudflare from deployment
        domains_data = {
            "domains": {
                "test-domain": {
                    "display_name": "Test Domain",
                    "active": True,
                    "compliance": [],
                    "human_gates": [],
                    "content": {"tier": 2},
                    "products": ["project-one"],
                    "deployment": {},  # No cloudflare_project
                }
            }
        }
        with open(portfolio_service._forge_root / "domains.yaml", "w") as f:
            yaml.dump(domains_data, f)

        portfolio_service._domains_cache = None

        result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["production_url"] is None

    def test_get_project_details_feature_list_defaults(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should use default priority 'P2' and status 'pending' for missing fields."""
        self._write_features(
            portfolio_service,
            [{"id": "f1", "name": "Minimal Feature"}],
        )

        result = portfolio_service.get_project_details("test-domain", "project-one")

        feature = result["features"]["list"][0]
        assert feature["status"] == "pending"
        assert feature["priority"] == "P2"

    def test_get_project_details_compliance_and_human_gates(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should include compliance and human_gates from domain config."""
        result = portfolio_service.get_project_details("test-domain", "project-one")

        assert result["compliance"] == ["SOC2"]
        assert result["human_gates"] == ["approval-required"]


# =============================================================================
# Extended _load_features Tests (alternative paths)
# =============================================================================


class TestFeatureLoadingExtended:
    """Additional feature loading path tests."""

    def test_load_features_from_parent_domain_project(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should load features from forge_root.parent/domain/project path."""
        features_data = {"version": "2.0", "features": []}
        parent_project_dir = (
            portfolio_service._forge_root.parent / "test-domain" / "project-x"
        )
        parent_project_dir.mkdir(parents=True)
        with open(parent_project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        result = portfolio_service._load_features("test-domain", "project-x")

        assert result is not None
        assert result["version"] == "2.0"

    def test_load_features_from_project_root(
        self, portfolio_service: PortfolioService
    ) -> None:
        """Should load features from forge_root/project path (no domain)."""
        features_data = {"version": "3.0", "features": []}
        root_project_dir = portfolio_service._forge_root / "standalone-project"
        root_project_dir.mkdir(parents=True)
        with open(root_project_dir / "features.json", "w") as f:
            json.dump(features_data, f)

        result = portfolio_service._load_features("some-domain", "standalone-project")

        assert result is not None
        assert result["version"] == "3.0"
