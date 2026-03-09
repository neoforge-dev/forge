"""Extended tests for portfolio_service.py.

Focuses on edge cases, integration scenarios, and deeper coverage of:
- Cache invalidation timing
- Multi-domain aggregation
- Feature status edge cases
- Agent counting deduplication logic
- Subprocess git interaction
- Production URL derivation
- Pending approval counting
- Singleton re-initialization
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from forge_harness.webhook_server.services.portfolio_service import (
    PortfolioService,
    get_portfolio_service,
)

# =============================================================================
# Shared Fixtures
# =============================================================================


@pytest.fixture
def forge_root(tmp_path: Path) -> Path:
    """Create a minimal FORGE root with one active domain and two products."""
    root = tmp_path / "FORGE"
    root.mkdir()

    domains = {
        "domains": {
            "alpha-domain": {
                "display_name": "Alpha Domain",
                "active": True,
                "compliance": ["HIPAA"],
                "human_gates": ["security-review"],
                "content": {"tier": 2},
                "products": ["alpha-project", "beta-project"],
                "frontend_tier": "React",
                "deployment": {"cloudflare_project": "alpha-domain-pages"},
            },
            "inactive-domain": {
                "display_name": "Inactive",
                "active": False,
                "products": ["hidden-project"],
            },
        }
    }
    with open(root / "domains.yaml", "w") as f:
        yaml.dump(domains, f)

    return root


@pytest.fixture
def svc(forge_root: Path) -> PortfolioService:
    """PortfolioService with controlled forge_root."""
    return PortfolioService(forge_root=forge_root)


def _write_features(root: Path, domain: str, project: str, feature_list: list[dict]) -> None:
    """Helper: write a features.json file for a given domain/project."""
    project_dir = root / domain / project
    project_dir.mkdir(parents=True, exist_ok=True)
    with open(project_dir / "features.json", "w") as f:
        json.dump({"features": feature_list}, f)


# =============================================================================
# 1. Cache Invalidation
# =============================================================================


class TestCacheInvalidation:
    """Verify cache TTL and invalidation behavior."""

    def test_cache_is_primed_after_first_load(self, svc: PortfolioService) -> None:
        svc._load_domains()
        assert svc._domains_cache is not None
        assert svc._cache_time > 0.0

    def test_cache_returns_same_object_before_ttl(self, svc: PortfolioService) -> None:
        d1 = svc._load_domains()
        d2 = svc._load_domains()
        assert d1 is d2  # Same object from cache

    def test_cache_reloads_after_ttl_expires(self, svc: PortfolioService, forge_root: Path) -> None:
        svc._load_domains()
        original_time = svc._cache_time

        # Force cache expiry
        svc._cache_time = time.time() - svc._cache_ttl - 1

        svc._load_domains()
        assert svc._cache_time > original_time

    def test_manual_cache_invalidation(self, svc: PortfolioService) -> None:
        svc._load_domains()
        svc._domains_cache = None  # manual invalidation
        result = svc._load_domains()
        assert result is not None

    def test_cache_isolates_between_service_instances(
        self, forge_root: Path
    ) -> None:
        svc1 = PortfolioService(forge_root=forge_root)
        svc2 = PortfolioService(forge_root=forge_root)
        d1 = svc1._load_domains()
        d2 = svc2._load_domains()
        # Different instances, different cache objects but same content
        assert d1 == d2
        assert d1 is not d2


# =============================================================================
# 2. Multi-Domain and Product Aggregation
# =============================================================================


class TestMultiDomainAggregation:
    """Tests for multi-domain data aggregation."""

    def test_inactive_domains_excluded_from_all_projects(self, svc: PortfolioService) -> None:
        projects = svc.get_all_projects()
        domains = {p["domain"] for p in projects}
        assert "inactive-domain" not in domains

    def test_active_domain_products_all_included(self, svc: PortfolioService) -> None:
        projects = svc.get_all_projects()
        project_names = [p["project"] for p in projects]
        assert "alpha-project" in project_names
        assert "beta-project" in project_names

    def test_two_active_domains_aggregate_correctly(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "FORGE"
        root.mkdir()
        domains = {
            "domains": {
                "dom-a": {
                    "display_name": "A",
                    "active": True,
                    "products": ["p1", "p2"],
                },
                "dom-b": {
                    "display_name": "B",
                    "active": True,
                    "products": ["p3"],
                },
            }
        }
        with open(root / "domains.yaml", "w") as f:
            yaml.dump(domains, f)

        service = PortfolioService(forge_root=root)
        projects = service.get_all_projects()
        assert len(projects) == 3

    def test_domain_with_no_products(self, tmp_path: Path) -> None:
        root = tmp_path / "FORGE"
        root.mkdir()
        domains = {
            "domains": {
                "empty-dom": {
                    "display_name": "Empty",
                    "active": True,
                    "products": [],
                }
            }
        }
        with open(root / "domains.yaml", "w") as f:
            yaml.dump(domains, f)

        service = PortfolioService(forge_root=root)
        projects = service.get_all_projects()
        assert projects == []

    def test_portfolio_summary_total_projects_matches_all_projects(
        self, svc: PortfolioService
    ) -> None:
        all_projects = svc.get_all_projects()
        summary = svc.get_portfolio_summary()
        assert summary["summary"]["total_projects"] == len(all_projects)


# =============================================================================
# 3. Feature Status Edge Cases
# =============================================================================


class TestFeatureStatusEdgeCases:
    """Deep edge-case tests for feature counting and status derivation."""

    def test_all_features_blocking_yields_blocked_status(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [
                {"id": "f1", "status": "blocked"},
                {"id": "f2", "status": "blocked"},
            ],
        )
        result = svc.get_domain_projects("alpha-domain")
        blocked = [p for p in result["projects"] if p["name"] == "alpha-project"]
        assert blocked[0]["status"] == "blocked"

    def test_blocked_takes_priority_over_failing(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [
                {"id": "f1", "status": "blocked"},
                {"id": "f2", "status": "failing"},
            ],
        )
        result = svc.get_domain_projects("alpha-domain")
        project = next(p for p in result["projects"] if p["name"] == "alpha-project")
        assert project["status"] == "blocked"

    def test_failing_takes_priority_over_pending(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [
                {"id": "f1", "status": "failing"},
                {"id": "f2", "status": "pending"},
            ],
        )
        result = svc.get_domain_projects("alpha-domain")
        project = next(p for p in result["projects"] if p["name"] == "alpha-project")
        assert project["status"] == "failing"

    def test_pending_takes_priority_over_passing(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [
                {"id": "f1", "status": "passing"},
                {"id": "f2", "status": "pending"},
            ],
        )
        result = svc.get_domain_projects("alpha-domain")
        project = next(p for p in result["projects"] if p["name"] == "alpha-project")
        assert project["status"] == "dev"

    def test_all_passing_gives_ready(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [{"id": f"f{i}", "status": "passing"} for i in range(5)],
        )
        result = svc.get_domain_projects("alpha-domain")
        project = next(p for p in result["projects"] if p["name"] == "alpha-project")
        assert project["status"] == "ready"

    def test_zero_features_yields_pending(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        _write_features(forge_root, "alpha-domain", "alpha-project", [])
        result = svc.get_domain_projects("alpha-domain")
        project = next(p for p in result["projects"] if p["name"] == "alpha-project")
        assert project["status"] == "pending"

    def test_get_all_projects_exactly_80_pct_is_ready(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # Exactly 80 of 100 passing = 80% → "ready"
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [{"id": f"f{i}", "status": "passing"} for i in range(80)]
            + [{"id": f"f{i+80}", "status": "pending"} for i in range(20)],
        )
        projects = svc.get_all_projects()
        project = next(p for p in projects if p["project"] == "alpha-project")
        assert project["status"] == "ready"
        assert project["progress_pct"] == 80

    def test_get_all_projects_51_pct_is_dev(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # 51 of 100 passing = 51% → "dev"
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [{"id": f"f{i}", "status": "passing"} for i in range(51)]
            + [{"id": f"f{i+51}", "status": "pending"} for i in range(49)],
        )
        projects = svc.get_all_projects()
        project = next(p for p in projects if p["project"] == "alpha-project")
        assert project["status"] == "dev"
        assert project["progress_pct"] == 51

    def test_get_all_projects_49_pct_is_parked(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # 49 of 100 passing = 49% → "parked"
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [{"id": f"f{i}", "status": "passing"} for i in range(49)]
            + [{"id": f"f{i+49}", "status": "pending"} for i in range(51)],
        )
        projects = svc.get_all_projects()
        project = next(p for p in projects if p["project"] == "alpha-project")
        assert project["status"] == "parked"

    def test_feature_count_with_unknown_statuses_as_pending(
        self, svc: PortfolioService
    ) -> None:
        features = {
            "features": [
                {"id": "f1", "status": "UNKNOWN"},
                {"id": "f2", "status": "in_review"},
                {"id": "f3"},  # No status key at all
            ]
        }
        counts = svc._count_feature_status(features)
        # All three unknown/missing statuses default to pending
        assert counts["total"] == 3
        assert counts["pending"] == 3

    def test_feature_count_with_uppercase_status(self, svc: PortfolioService) -> None:
        # status is lowercased before lookup
        features = {"features": [{"id": "f1", "status": "PASSING"}]}
        counts = svc._count_feature_status(features)
        # "passing" is recognized after lower()
        assert counts["passing"] == 1


# =============================================================================
# 4. Tech Stack Derivation
# =============================================================================


class TestTechStackEdgeCases:
    """Additional tech stack derivation tests."""

    def test_features_tech_stack_takes_precedence_over_domain_tier(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # features.json has tech_stack; domain has frontend_tier React
        project_dir = forge_root / "alpha-domain" / "alpha-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump({"tech_stack": ["Vue", "GraphQL"]}, f)

        domain_info = {"frontend_tier": "React"}
        result = svc._get_tech_stack("alpha-domain", "alpha-project", domain_info)
        assert result == ["Vue", "GraphQL"]

    def test_non_list_tech_stack_in_features_falls_back_to_domain(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # tech_stack is a dict (not a list) → should ignore and use domain tier
        project_dir = forge_root / "alpha-domain" / "alpha-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        with open(project_dir / "features.json", "w") as f:
            json.dump({"tech_stack": {"name": "React"}}, f)

        domain_info = {"frontend_tier": "React Native"}
        result = svc._get_tech_stack("alpha-domain", "alpha-project", domain_info)
        assert "React Native" in result

    def test_none_domain_info_returns_empty(self, svc: PortfolioService) -> None:
        result = svc._get_tech_stack("alpha-domain", "no-features-project", None)
        assert result == []

    def test_empty_frontend_tier_returns_empty(self, svc: PortfolioService) -> None:
        domain_info = {"frontend_tier": ""}
        result = svc._get_tech_stack("alpha-domain", "no-features-project", domain_info)
        assert result == []

    def test_custom_frontend_tier_includes_fastapi_postgres(
        self, svc: PortfolioService
    ) -> None:
        domain_info = {"frontend_tier": "Svelte"}
        result = svc._get_tech_stack("alpha-domain", "no-features-project", domain_info)
        assert "Svelte" in result
        assert "FastAPI" in result
        assert "PostgreSQL" in result


# =============================================================================
# 5. Project Details — git Subprocess Scenarios
# =============================================================================


class TestProjectDetailsGitSubprocess:
    """Test git subprocess handling in get_project_details."""

    def _setup_product(self, forge_root: Path, domain: str, product: str) -> Path:
        """Ensure a matching product exists in domains.yaml."""
        domains_data = {
            "domains": {
                domain: {
                    "display_name": domain.title(),
                    "active": True,
                    "products": [product],
                    "deployment": {"cloudflare_project": f"{domain}-cf"},
                }
            }
        }
        with open(forge_root / "domains.yaml", "w") as f:
            yaml.dump(domains_data, f)
        return forge_root

    def test_git_output_with_multiple_commits(self, svc: PortfolioService, forge_root: Path) -> None:
        # Re-write domains.yaml so project-one is listed
        self._setup_product(forge_root, "alpha-domain", "alpha-project")
        svc._domains_cache = None

        project_path = forge_root.parent / "alpha-domain" / "alpha-project"
        project_path.mkdir(parents=True, exist_ok=True)

        git_out = (
            "aaa1111 First commit\n"
            "bbb2222 Second commit\n"
            "ccc3333 Third commit\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = git_out

        with patch("subprocess.run", return_value=mock_result):
            result = svc.get_project_details("alpha-domain", "alpha-project")

        assert len(result["recent_commits"]) == 3
        assert result["recent_commits"][0]["hash"] == "aaa1111"
        assert result["recent_commits"][2]["message"] == "Third commit"

    def test_git_output_empty_string_produces_no_commits(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        self._setup_product(forge_root, "alpha-domain", "alpha-project")
        svc._domains_cache = None

        project_path = forge_root.parent / "alpha-domain" / "alpha-project"
        project_path.mkdir(parents=True, exist_ok=True)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = svc.get_project_details("alpha-domain", "alpha-project")

        assert result["recent_commits"] == []

    def test_git_timeout_exception_gives_empty_commits(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        self._setup_product(forge_root, "alpha-domain", "alpha-project")
        svc._domains_cache = None

        project_path = forge_root.parent / "alpha-domain" / "alpha-project"
        project_path.mkdir(parents=True, exist_ok=True)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            result = svc.get_project_details("alpha-domain", "alpha-project")

        assert result["recent_commits"] == []

    def test_git_os_error_gives_empty_commits(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        self._setup_product(forge_root, "alpha-domain", "alpha-project")
        svc._domains_cache = None

        project_path = forge_root.parent / "alpha-domain" / "alpha-project"
        project_path.mkdir(parents=True, exist_ok=True)

        with patch("subprocess.run", side_effect=OSError("no git")):
            result = svc.get_project_details("alpha-domain", "alpha-project")

        assert result["recent_commits"] == []

    def test_project_path_missing_skips_git(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        self._setup_product(forge_root, "alpha-domain", "alpha-project")
        svc._domains_cache = None

        # Do NOT create the project path — the code checks .exists() first
        with patch("subprocess.run") as mock_run:
            result = svc.get_project_details("alpha-domain", "alpha-project")

        mock_run.assert_not_called()
        assert result["recent_commits"] == []


# =============================================================================
# 6. Production URL Derivation
# =============================================================================


class TestProductionUrlDerivation:
    """Test production URL logic."""

    def test_cloudflare_project_generates_pages_url(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        result = svc.get_project_details("alpha-domain", "alpha-project")
        assert result is not None
        assert result["production_url"] == "https://alpha-domain-pages.pages.dev"

    def test_missing_deployment_key_returns_none_url(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "FORGE"
        root.mkdir()
        domains = {
            "domains": {
                "no-deploy-domain": {
                    "display_name": "No Deploy",
                    "active": True,
                    "products": ["my-project"],
                    # No "deployment" key at all
                }
            }
        }
        with open(root / "domains.yaml", "w") as f:
            yaml.dump(domains, f)

        service = PortfolioService(forge_root=root)
        result = service.get_project_details("no-deploy-domain", "my-project")
        assert result is not None
        assert result["production_url"] is None

    def test_deployment_without_cloudflare_project_key_returns_none_url(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "FORGE"
        root.mkdir()
        domains = {
            "domains": {
                "d": {
                    "display_name": "D",
                    "active": True,
                    "products": ["p"],
                    "deployment": {"heroku_app": "my-heroku-app"},  # no cloudflare_project
                }
            }
        }
        with open(root / "domains.yaml", "w") as f:
            yaml.dump(domains, f)

        service = PortfolioService(forge_root=root)
        result = service.get_project_details("d", "p")
        assert result is not None
        assert result["production_url"] is None


# =============================================================================
# 7. Pending Approvals Counting
# =============================================================================


class TestPendingApprovalsCounting:
    """Tests for pending approval counting in portfolio summary."""

    def test_approvals_directory_with_json_and_non_json_files(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        approvals_dir = forge_root / ".forge/approvals"
        approvals_dir.mkdir()
        for i in range(3):
            (approvals_dir / f"approval-{i}.json").write_text("{}")
        (approvals_dir / "readme.md").write_text("# ignored")
        (approvals_dir / "approval-4.txt").write_text("also ignored")

        result = svc.get_portfolio_summary()
        # Only .json files count
        assert result["summary"]["pending_approvals"] == 3

    def test_empty_approvals_directory(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        approvals_dir = forge_root / ".forge/approvals"
        approvals_dir.mkdir()

        result = svc.get_portfolio_summary()
        assert result["summary"]["pending_approvals"] == 0

    def test_no_approvals_directory(self, svc: PortfolioService) -> None:
        result = svc.get_portfolio_summary()
        assert result["summary"]["pending_approvals"] == 0


# =============================================================================
# 8. Agent Counting in Portfolio Summary
# =============================================================================


class TestAgentCountingInSummary:
    """Test agent counting logic within get_portfolio_summary."""

    def test_registry_agents_are_counted(self, svc: PortfolioService) -> None:
        mock_agent = MagicMock()
        mock_agent.id = "reg-agent-001"
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = [mock_agent]

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ):
            result = svc.get_portfolio_summary()

        assert result["summary"]["active_agents"] >= 1

    def test_multiple_registry_agents_counted(self, svc: PortfolioService) -> None:
        agents = [MagicMock(id=f"agent-{i}") for i in range(5)]
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = agents

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ):
            result = svc.get_portfolio_summary()

        assert result["summary"]["active_agents"] >= 5

    def test_registry_exception_does_not_break_summary(self, svc: PortfolioService) -> None:
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            side_effect=Exception("registry unavailable"),
        ):
            result = svc.get_portfolio_summary()

        assert "summary" in result
        assert "active_agents" in result["summary"]

    def test_state_store_exception_does_not_break_summary(
        self, svc: PortfolioService
    ) -> None:
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ), patch.dict("sys.modules", {"forge_harness.state_store": None}):
            result = svc.get_portfolio_summary()

        assert "summary" in result

    def test_tmux_exception_does_not_break_summary(self, svc: PortfolioService) -> None:
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []

        mock_tracker = MagicMock()
        mock_tracker.get_all_sessions.side_effect = RuntimeError("tmux error")
        mock_tracker_cls = MagicMock(return_value=mock_tracker)

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
            result = svc.get_portfolio_summary()

        assert "summary" in result


# =============================================================================
# 9. Portfolio Summary by_status Counts
# =============================================================================


class TestPortfolioByStatusCounts:
    """Verify by_status aggregation in portfolio summary."""

    def test_summary_by_status_has_all_keys(self, svc: PortfolioService) -> None:
        result = svc.get_portfolio_summary()
        by_status = result["summary"]["by_status"]
        for key in ("live", "ready", "dev", "parked", "blocked"):
            assert key in by_status

    def test_summary_by_status_sums_match_total(self, svc: PortfolioService) -> None:
        result = svc.get_portfolio_summary()
        by_status = result["summary"]["by_status"]
        total = result["summary"]["total_projects"]
        assert sum(by_status.values()) == total

    def test_live_project_counted_in_live(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # 100% passing → live
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [{"id": "f1", "status": "passing"}],
        )
        result = svc.get_portfolio_summary()
        assert result["summary"]["by_status"]["live"] >= 1

    def test_parked_project_counted_in_parked(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # 0% passing (all pending) → parked
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [{"id": "f1", "status": "pending"}, {"id": "f2", "status": "pending"}],
        )
        result = svc.get_portfolio_summary()
        assert result["summary"]["by_status"]["parked"] >= 1


# =============================================================================
# 10. Domain Projects Sorting and Structure
# =============================================================================


class TestDomainProjectsSortingAndStructure:
    """Test sorting and data structure of get_domain_projects."""

    def test_status_priority_order(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # alpha-project gets 'blocked'; beta-project gets 'ready'
        _write_features(
            forge_root, "alpha-domain", "alpha-project",
            [{"id": "f1", "status": "blocked"}],
        )
        _write_features(
            forge_root, "alpha-domain", "beta-project",
            [{"id": "f1", "status": "passing"}],
        )
        result = svc.get_domain_projects("alpha-domain")
        statuses = [p["status"] for p in result["projects"]]
        # blocked (priority 0) must appear before ready (priority 4)
        assert statuses.index("blocked") < statuses.index("ready")

    def test_projects_include_feature_counts(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        _write_features(
            forge_root, "alpha-domain", "alpha-project",
            [
                {"id": "f1", "status": "passing"},
                {"id": "f2", "status": "failing"},
                {"id": "f3", "status": "pending"},
            ],
        )
        result = svc.get_domain_projects("alpha-domain")
        project = next(p for p in result["projects"] if p["name"] == "alpha-project")
        assert project["features"]["total"] == 3
        assert project["features"]["passing"] == 1
        assert project["features"]["failing"] == 1
        assert project["features"]["pending"] == 1

    def test_domain_info_includes_compliance_human_gates_tier(
        self, svc: PortfolioService
    ) -> None:
        result = svc.get_domain_projects("alpha-domain")
        domain_info = result["domain"]
        assert domain_info["compliance"] == ["HIPAA"]
        assert domain_info["human_gates"] == ["security-review"]
        assert domain_info["content_tier"] == 2

    def test_domain_count_field_matches_projects_list(
        self, svc: PortfolioService
    ) -> None:
        result = svc.get_domain_projects("alpha-domain")
        assert result["count"] == len(result["projects"])

    def test_nonexistent_domain_returns_none(self, svc: PortfolioService) -> None:
        result = svc.get_domain_projects("does-not-exist")
        assert result is None


# =============================================================================
# 11. Project Details — Active Agents Filtering
# =============================================================================


class TestProjectDetailsActiveAgents:
    """Test active agents filtering in get_project_details."""

    def _write_single_product_domain(self, forge_root: Path) -> None:
        domains = {
            "domains": {
                "alpha-domain": {
                    "display_name": "Alpha",
                    "active": True,
                    "products": ["alpha-project"],
                    "deployment": {},
                }
            }
        }
        with open(forge_root / "domains.yaml", "w") as f:
            yaml.dump(domains, f)

    def test_only_matching_agents_included(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        self._write_single_product_domain(forge_root)
        svc._domains_cache = None

        matching = MagicMock()
        matching.project = "alpha-domain/alpha-project"
        matching.to_dict.return_value = {"id": "m1"}

        non_matching = MagicMock()
        non_matching.project = "other-domain/other-project"
        non_matching.to_dict.return_value = {"id": "nm1"}

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = [matching, non_matching]

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ):
            result = svc.get_project_details("alpha-domain", "alpha-project")

        assert len(result["active_agents"]) == 1
        assert result["active_agents"][0]["id"] == "m1"

    def test_agent_matching_by_slug_only(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        self._write_single_product_domain(forge_root)
        svc._domains_cache = None

        # project contains slug but not full domain/slug path
        slug_match = MagicMock()
        slug_match.project = "alpha-project"  # slug-only match
        slug_match.to_dict.return_value = {"id": "slug-agent"}

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = [slug_match]

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ):
            result = svc.get_project_details("alpha-domain", "alpha-project")

        assert len(result["active_agents"]) == 1

    def test_no_agents_returns_empty_list(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        self._write_single_product_domain(forge_root)
        svc._domains_cache = None

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []

        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=mock_registry,
        ):
            result = svc.get_project_details("alpha-domain", "alpha-project")

        assert result["active_agents"] == []


# =============================================================================
# 12. Project Details — Field Completeness
# =============================================================================


class TestProjectDetailsFieldCompleteness:
    """Verify all expected fields are present in project detail responses."""

    EXPECTED_FIELDS = [
        "name",
        "slug",
        "domain",
        "status",
        "features",
        "recent_commits",
        "active_agents",
        "pending_approvals_count",
        "production_url",
        "compliance",
        "human_gates",
        "tech_stack",
    ]

    def test_all_fields_present(self, svc: PortfolioService) -> None:
        result = svc.get_project_details("alpha-domain", "alpha-project")
        assert result is not None
        for field in self.EXPECTED_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_features_has_counts_and_list(self, svc: PortfolioService) -> None:
        result = svc.get_project_details("alpha-domain", "alpha-project")
        assert "counts" in result["features"]
        assert "list" in result["features"]

    def test_feature_list_items_have_required_keys(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [{"id": "f1", "name": "My Feature", "status": "passing", "priority": "P0"}],
        )
        result = svc.get_project_details("alpha-domain", "alpha-project")
        for item in result["features"]["list"]:
            assert "id" in item
            assert "name" in item
            assert "status" in item
            assert "priority" in item

    def test_feature_defaults_when_fields_missing(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        _write_features(
            forge_root,
            "alpha-domain",
            "alpha-project",
            [{"id": "f1"}],  # name, status, priority all missing
        )
        result = svc.get_project_details("alpha-domain", "alpha-project")
        item = result["features"]["list"][0]
        assert item["status"] == "pending"
        assert item["priority"] == "P2"


# =============================================================================
# 13. Display Name Formatting
# =============================================================================


class TestDisplayNameFormatting:
    """Test display name transformations."""

    def test_hyphen_replaced_with_space(self, svc: PortfolioService) -> None:
        projects = svc.get_all_projects()
        # "alpha-project" should become "Alpha Project"
        project = next(p for p in projects if p["project"] == "alpha-project")
        assert project["display_name"] == "Alpha Project"

    def test_underscore_replaced_with_space(self, tmp_path: Path) -> None:
        root = tmp_path / "FORGE"
        root.mkdir()
        domains = {
            "domains": {
                "u-domain": {
                    "display_name": "U Domain",
                    "active": True,
                    "products": ["my_cool_app"],
                }
            }
        }
        with open(root / "domains.yaml", "w") as f:
            yaml.dump(domains, f)

        service = PortfolioService(forge_root=root)
        projects = service.get_all_projects()
        project = next(p for p in projects if p["project"] == "my_cool_app")
        assert project["display_name"] == "My Cool App"

    def test_title_case_applied(self, svc: PortfolioService) -> None:
        projects = svc.get_all_projects()
        for p in projects:
            assert p["display_name"] == p["display_name"].title() or (
                p["display_name"][0].isupper()
            )


# =============================================================================
# 14. Singleton Behavior
# =============================================================================


class TestSingletonBehavior:
    """Test get_portfolio_service singleton lifecycle."""

    def test_singleton_returns_same_instance(self) -> None:
        import forge_harness.webhook_server.services.portfolio_service as ps_mod
        ps_mod._portfolio_service = None

        svc1 = get_portfolio_service()
        svc2 = get_portfolio_service()
        assert svc1 is svc2

    def test_singleton_is_portfolio_service_instance(self) -> None:
        import forge_harness.webhook_server.services.portfolio_service as ps_mod
        ps_mod._portfolio_service = None

        svc = get_portfolio_service()
        assert isinstance(svc, PortfolioService)

    def test_singleton_reset_creates_new_instance(self) -> None:
        import forge_harness.webhook_server.services.portfolio_service as ps_mod
        ps_mod._portfolio_service = None
        svc1 = get_portfolio_service()

        ps_mod._portfolio_service = None
        svc2 = get_portfolio_service()

        assert svc1 is not svc2

    def test_existing_singleton_is_reused(self) -> None:
        import forge_harness.webhook_server.services.portfolio_service as ps_mod
        # Pre-set a sentinel service
        sentinel = PortfolioService(forge_root=Path("/tmp"))
        ps_mod._portfolio_service = sentinel

        result = get_portfolio_service()
        assert result is sentinel


# =============================================================================
# 15. Load Features — All Three Path Variants
# =============================================================================


class TestLoadFeaturesPathResolution:
    """Tests that cover all three possible features.json lookup paths."""

    def test_primary_path_domain_project(self, svc: PortfolioService, forge_root: Path) -> None:
        project_dir = forge_root / "alpha-domain" / "alpha-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "features.json").write_text('{"features": [], "source": "primary"}')

        result = svc._load_features("alpha-domain", "alpha-project")
        assert result is not None
        assert result["source"] == "primary"

    def test_secondary_path_parent_domain_project(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # forge_root.parent / domain / project
        secondary_dir = forge_root.parent / "alpha-domain" / "sec-project"
        secondary_dir.mkdir(parents=True, exist_ok=True)
        (secondary_dir / "features.json").write_text('{"features": [], "source": "secondary"}')

        result = svc._load_features("alpha-domain", "sec-project")
        assert result is not None
        assert result["source"] == "secondary"

    def test_tertiary_path_project_root(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        # forge_root / project (no domain)
        root_project_dir = forge_root / "standalone-only"
        root_project_dir.mkdir(parents=True, exist_ok=True)
        (root_project_dir / "features.json").write_text('{"features": [], "source": "tertiary"}')

        result = svc._load_features("any-domain", "standalone-only")
        assert result is not None
        assert result["source"] == "tertiary"

    def test_none_returned_when_no_path_exists(
        self, svc: PortfolioService
    ) -> None:
        result = svc._load_features("no-domain", "no-project-anywhere")
        assert result is None

    def test_invalid_json_in_primary_path_returns_none(
        self, svc: PortfolioService, forge_root: Path
    ) -> None:
        project_dir = forge_root / "alpha-domain" / "bad-json-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "features.json").write_text("{ bad json }")

        result = svc._load_features("alpha-domain", "bad-json-project")
        assert result is None
