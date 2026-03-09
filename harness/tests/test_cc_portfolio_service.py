"""Comprehensive tests for PortfolioService.

Targets:
    forge_harness/webhook_server/services/portfolio_service.py

Coverage goals (70%+ per task spec):
  - _find_forge_root          – path resolution fallback
  - _load_domains             – happy path, cache hit, missing file, YAML error
  - _load_features            – multiple path probing, missing, JSON error
  - _count_feature_status     – all branches (None, empty, known/unknown status)
  - _get_tech_stack           – features.json wins, tier_map lookup, fallback
  - get_all_projects          – active/inactive domains, progress thresholds
  - get_portfolio_summary     – agent counting from registry / state_store / tmux,
                                pending approvals, unknown status fallback
  - get_domain_projects       – missing domain, status priority sort, all statuses
  - get_project_details       – missing domain, missing product, git log, agents,
                                production URL, deployment, tech stack
  - get_portfolio_service     – singleton behaviour
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from forge_harness.webhook_server.services.portfolio_service import (
    PortfolioService,
    get_portfolio_service,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

DOMAINS_YAML_CONTENT = {
    "domains": {
        "brandfocus-ai": {
            "active": True,
            "display_name": "BrandFocus AI",
            "frontend_tier": "React",
            "products": ["voice-coach", "brand-pulse"],
            "compliance": ["GDPR"],
            "human_gates": ["deploy"],
            "content": {"tier": 1},
            "deployment": {"cloudflare_project": "brandfocus-voice-coach"},
        },
        "codeswiftr-com": {
            "active": True,
            "display_name": "CodeSwiftr",
            "frontend_tier": "Lit PWA",
            "products": ["interview-simulator"],
            "compliance": [],
            "human_gates": [],
            "content": {"tier": 2},
            "deployment": {},
        },
        "inactive-domain": {
            "active": False,
            "products": ["some-project"],
        },
    }
}

FEATURES_JSON_CONTENT = {
    "features": [
        {"id": "F001", "name": "Auth", "status": "passing", "priority": "P0"},
        {"id": "F002", "name": "Dashboard", "status": "failing", "priority": "P1"},
        {"id": "F003", "name": "Settings", "status": "pending", "priority": "P2"},
        {"id": "F004", "name": "Export", "status": "blocked", "priority": "P2"},
    ]
}

ALL_PASSING_FEATURES = {
    "features": [
        {"id": "F001", "name": "Auth", "status": "passing", "priority": "P0"},
        {"id": "F002", "name": "Dashboard", "status": "passing", "priority": "P1"},
    ]
}

EIGHTY_PCT_FEATURES = {
    "features": [
        {"id": f"F{i:03d}", "name": f"Feature {i}", "status": "passing", "priority": "P2"}
        for i in range(8)
    ]
    + [
        {"id": "F008", "name": "Feature 8", "status": "pending", "priority": "P2"},
        {"id": "F009", "name": "Feature 9", "status": "pending", "priority": "P2"},
    ]
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def forge_root(tmp_path: Path) -> Path:
    """Minimal FORGE root with forge_harness/domains.yaml."""
    fh = tmp_path / "forge_harness"
    fh.mkdir()
    return tmp_path


@pytest.fixture()
def service(forge_root: Path) -> PortfolioService:
    """PortfolioService backed by a temp directory (no real domains.yaml yet)."""
    return PortfolioService(forge_root=forge_root)


@pytest.fixture()
def service_with_domains(forge_root: Path) -> PortfolioService:
    """PortfolioService with a valid domains.yaml written."""
    import yaml

    domains_path = forge_root / "forge_harness" / "domains.yaml"
    domains_path.write_text(yaml.dump(DOMAINS_YAML_CONTENT))
    return PortfolioService(forge_root=forge_root)


# ---------------------------------------------------------------------------
# _find_forge_root
# ---------------------------------------------------------------------------


class TestFindForgeRoot:
    def test_returns_path_when_domains_yaml_exists(self, tmp_path: Path):
        """Should find root when domains.yaml is present at the top level."""
        (tmp_path / "domains.yaml").touch()
        svc = PortfolioService.__new__(PortfolioService)
        # Monkey-patch __file__ resolution via a subpath
        with patch.object(Path, "exists", return_value=True):
            # Just verify the method returns a Path
            result = svc._find_forge_root()
        assert isinstance(result, Path)

    def test_returns_path_when_forge_harness_domains_yaml_exists(self, tmp_path: Path):
        """Should detect root via forge_harness/domains.yaml."""
        sub = tmp_path / "forge_harness"
        sub.mkdir()
        (sub / "domains.yaml").touch()
        svc = PortfolioService.__new__(PortfolioService)
        result = svc._find_forge_root()
        assert isinstance(result, Path)

    def test_falls_back_to_parent_when_no_domains_yaml(self):
        """Should fall back to parent of __file__ when no domains.yaml found."""
        svc = PortfolioService.__new__(PortfolioService)
        with patch.object(Path, "exists", return_value=False):
            result = svc._find_forge_root()
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# _load_domains
# ---------------------------------------------------------------------------


class TestLoadDomains:
    def test_returns_empty_dict_when_no_file(self, service: PortfolioService):
        """Missing domains.yaml returns {"domains": {}}."""
        result = service._load_domains()
        assert result == {"domains": {}}

    def test_loads_valid_yaml(self, service_with_domains: PortfolioService):
        """Valid domains.yaml is parsed correctly."""
        result = service_with_domains._load_domains()
        assert "domains" in result
        assert "brandfocus-ai" in result["domains"]

    def test_cache_is_used_on_second_call(self, service_with_domains: PortfolioService):
        """Second call within TTL returns cached data without re-reading."""
        first = service_with_domains._load_domains()
        # Corrupt the file — cache should still return original data
        domains_path = service_with_domains._forge_root / "forge_harness" / "domains.yaml"
        domains_path.write_text("INVALID: [[[")
        second = service_with_domains._load_domains()
        assert first is second  # Same object from cache

    def test_cache_expires_after_ttl(self, service_with_domains: PortfolioService):
        """After TTL expires, domains.yaml is re-read."""
        import yaml

        service_with_domains._load_domains()  # Prime cache
        service_with_domains._cache_time = 0  # Expire cache
        # Update file with different content
        updated = {"domains": {"new-domain": {"active": True, "products": []}}}
        domains_path = service_with_domains._forge_root / "forge_harness" / "domains.yaml"
        domains_path.write_text(yaml.dump(updated))
        result = service_with_domains._load_domains()
        assert "new-domain" in result["domains"]

    def test_falls_back_to_root_domains_yaml(self, tmp_path: Path):
        """Falls back to <forge_root>/domains.yaml when forge_harness subdir missing."""
        import yaml

        # No forge_harness subdirectory, put it at root
        domains_path = tmp_path / "domains.yaml"
        domains_path.write_text(yaml.dump({"domains": {"root-domain": {"active": True}}}))
        svc = PortfolioService(forge_root=tmp_path)
        result = svc._load_domains()
        assert "root-domain" in result["domains"]

    def test_yaml_parse_error_returns_empty(self, forge_root: Path):
        """Corrupt YAML returns {"domains": {}} without raising."""
        domains_path = forge_root / "forge_harness" / "domains.yaml"
        domains_path.write_text("key: [invalid")
        svc = PortfolioService(forge_root=forge_root)
        result = svc._load_domains()
        assert result == {"domains": {}}


# ---------------------------------------------------------------------------
# _load_features
# ---------------------------------------------------------------------------


class TestLoadFeatures:
    def _write_features(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_returns_none_when_no_file(self, service: PortfolioService):
        result = service._load_features("brandfocus-ai", "voice-coach")
        assert result is None

    def test_loads_features_from_primary_path(self, forge_root: Path):
        features_path = forge_root / "brandfocus-ai" / "voice-coach" / "features.json"
        features_path.parent.mkdir(parents=True, exist_ok=True)
        features_path.write_text(json.dumps(FEATURES_JSON_CONTENT))
        svc = PortfolioService(forge_root=forge_root)
        result = svc._load_features("brandfocus-ai", "voice-coach")
        assert result is not None
        assert len(result["features"]) == 4

    def test_loads_features_from_parent_path(self, tmp_path: Path):
        """Checks second candidate: forge_root.parent / domain / project."""
        forge_root = tmp_path / "harness"
        forge_root.mkdir()
        (forge_root / "forge_harness").mkdir()
        # Put features at parent level
        features_path = tmp_path / "brandfocus-ai" / "voice-coach" / "features.json"
        features_path.parent.mkdir(parents=True, exist_ok=True)
        features_path.write_text(json.dumps(ALL_PASSING_FEATURES))
        svc = PortfolioService(forge_root=forge_root)
        result = svc._load_features("brandfocus-ai", "voice-coach")
        assert result is not None

    def test_loads_features_from_project_only_path(self, forge_root: Path):
        """Checks third candidate: forge_root / project."""
        features_path = forge_root / "voice-coach" / "features.json"
        features_path.parent.mkdir(parents=True, exist_ok=True)
        features_path.write_text(json.dumps(ALL_PASSING_FEATURES))
        svc = PortfolioService(forge_root=forge_root)
        result = svc._load_features("brandfocus-ai", "voice-coach")
        assert result is not None

    def test_json_parse_error_returns_none(self, forge_root: Path):
        features_path = forge_root / "brandfocus-ai" / "voice-coach" / "features.json"
        features_path.parent.mkdir(parents=True, exist_ok=True)
        features_path.write_text("{INVALID JSON")
        svc = PortfolioService(forge_root=forge_root)
        result = svc._load_features("brandfocus-ai", "voice-coach")
        assert result is None


# ---------------------------------------------------------------------------
# _count_feature_status
# ---------------------------------------------------------------------------


class TestCountFeatureStatus:
    def test_none_input_returns_zeros(self, service: PortfolioService):
        result = service._count_feature_status(None)
        assert result == {"total": 0, "passing": 0, "failing": 0, "pending": 0, "blocked": 0}

    def test_empty_features_key_returns_zeros(self, service: PortfolioService):
        result = service._count_feature_status({})
        assert result == {"total": 0, "passing": 0, "failing": 0, "pending": 0, "blocked": 0}

    def test_empty_features_list_returns_zeros(self, service: PortfolioService):
        result = service._count_feature_status({"features": []})
        assert result == {"total": 0, "passing": 0, "failing": 0, "pending": 0, "blocked": 0}

    def test_counts_all_known_statuses(self, service: PortfolioService):
        result = service._count_feature_status(FEATURES_JSON_CONTENT)
        assert result["total"] == 4
        assert result["passing"] == 1
        assert result["failing"] == 1
        assert result["pending"] == 1
        assert result["blocked"] == 1

    def test_unknown_status_increments_pending(self, service: PortfolioService):
        data = {"features": [{"status": "unknown_status"}, {"status": "PASSING"}]}
        result = service._count_feature_status(data)
        # "unknown_status" → pending; "PASSING" (lowered) → passing
        assert result["total"] == 2
        assert result["pending"] == 1
        assert result["passing"] == 1

    def test_missing_status_field_defaults_to_pending(self, service: PortfolioService):
        data = {"features": [{"name": "no-status"}]}
        result = service._count_feature_status(data)
        assert result["pending"] == 1

    def test_all_passing(self, service: PortfolioService):
        result = service._count_feature_status(ALL_PASSING_FEATURES)
        assert result["passing"] == 2
        assert result["total"] == 2
        assert result["failing"] == 0


# ---------------------------------------------------------------------------
# _get_tech_stack
# ---------------------------------------------------------------------------


class TestGetTechStack:
    def test_uses_features_json_tech_stack_when_available(self, service: PortfolioService):
        with patch.object(
            service, "_load_features", return_value={"tech_stack": ["Flutter", "Dart"]}
        ):
            result = service._get_tech_stack("brandfocus-ai", "voice-coach", {})
        assert result == ["Flutter", "Dart"]

    def test_falls_back_to_tier_map_react(self, service: PortfolioService):
        with patch.object(service, "_load_features", return_value=None):
            result = service._get_tech_stack(
                "brandfocus-ai", "voice-coach", {"frontend_tier": "React"}
            )
        assert "React" in result
        assert "TypeScript" in result

    def test_falls_back_to_tier_map_lit_pwa(self, service: PortfolioService):
        with patch.object(service, "_load_features", return_value=None):
            result = service._get_tech_stack(
                "codeswiftr-com", "interview-simulator", {"frontend_tier": "Lit PWA"}
            )
        assert "Lit" in result
        assert "Web Components" in result

    def test_falls_back_to_tier_map_react_native(self, service: PortfolioService):
        with patch.object(service, "_load_features", return_value=None):
            result = service._get_tech_stack("domain", "project", {"frontend_tier": "React Native"})
        assert "React Native" in result

    def test_unknown_frontend_tier_returns_list_with_tier_name(self, service: PortfolioService):
        with patch.object(service, "_load_features", return_value=None):
            result = service._get_tech_stack("domain", "project", {"frontend_tier": "Vue"})
        assert "Vue" in result
        assert "FastAPI" in result

    def test_empty_frontend_tier_returns_empty_list(self, service: PortfolioService):
        with patch.object(service, "_load_features", return_value=None):
            result = service._get_tech_stack("domain", "project", {"frontend_tier": ""})
        assert result == []

    def test_missing_frontend_tier_returns_empty_list(self, service: PortfolioService):
        with patch.object(service, "_load_features", return_value=None):
            result = service._get_tech_stack("domain", "project", {})
        assert result == []

    def test_features_with_non_list_tech_stack_falls_back_to_tier(
        self, service: PortfolioService
    ):
        """When features.json has tech_stack but it's not a list, fall back to tier map."""
        with patch.object(
            service, "_load_features", return_value={"tech_stack": "React"}  # string, not list
        ):
            result = service._get_tech_stack(
                "domain", "project", {"frontend_tier": "Lit PWA"}
            )
        assert "Lit" in result


# ---------------------------------------------------------------------------
# get_all_projects
# ---------------------------------------------------------------------------


class TestGetAllProjects:
    def test_skips_inactive_domains(self, service_with_domains: PortfolioService):
        with patch.object(service_with_domains, "_load_features", return_value=None):
            projects = service_with_domains.get_all_projects()
        domain_ids = {p["domain"] for p in projects}
        assert "inactive-domain" not in domain_ids

    def test_returns_projects_from_active_domains(self, service_with_domains: PortfolioService):
        with patch.object(service_with_domains, "_load_features", return_value=None):
            projects = service_with_domains.get_all_projects()
        assert len(projects) >= 3  # voice-coach, brand-pulse, interview-simulator

    def test_project_status_live_when_100_pct(self, service_with_domains: PortfolioService):
        with patch.object(
            service_with_domains, "_load_features", return_value=ALL_PASSING_FEATURES
        ):
            projects = service_with_domains.get_all_projects()
        assert any(p["status"] == "live" for p in projects)
        assert all(p["progress_pct"] == 100 for p in projects if p["status"] == "live")

    def test_project_status_ready_when_80_pct(self, service_with_domains: PortfolioService):
        with patch.object(
            service_with_domains, "_load_features", return_value=EIGHTY_PCT_FEATURES
        ):
            projects = service_with_domains.get_all_projects()
        assert any(p["status"] == "ready" for p in projects)

    def test_project_status_dev_when_50_to_79_pct(self, service_with_domains: PortfolioService):
        features = {
            "features": [{"status": "passing"}] * 5 + [{"status": "pending"}] * 5
        }
        with patch.object(service_with_domains, "_load_features", return_value=features):
            projects = service_with_domains.get_all_projects()
        assert any(p["status"] == "dev" for p in projects)

    def test_project_status_parked_when_below_50_pct(
        self, service_with_domains: PortfolioService
    ):
        features = {
            "features": [{"status": "passing"}] * 1 + [{"status": "pending"}] * 9
        }
        with patch.object(service_with_domains, "_load_features", return_value=features):
            projects = service_with_domains.get_all_projects()
        assert any(p["status"] == "parked" for p in projects)

    def test_project_status_dev_when_no_features(self, service_with_domains: PortfolioService):
        with patch.object(service_with_domains, "_load_features", return_value=None):
            projects = service_with_domains.get_all_projects()
        assert all(p["status"] == "dev" for p in projects)
        assert all(p["progress_pct"] == 0 for p in projects)

    def test_display_name_title_case(self, service_with_domains: PortfolioService):
        with patch.object(service_with_domains, "_load_features", return_value=None):
            projects = service_with_domains.get_all_projects()
        vc = next((p for p in projects if p["project"] == "voice-coach"), None)
        assert vc is not None
        assert vc["display_name"] == "Voice Coach"

    def test_returns_empty_list_when_no_domains(self, service: PortfolioService):
        projects = service.get_all_projects()
        assert projects == []

    def test_progress_pct_zero_when_no_passing(self, service_with_domains: PortfolioService):
        features = {"features": [{"status": "pending"}, {"status": "pending"}]}
        with patch.object(service_with_domains, "_load_features", return_value=features):
            projects = service_with_domains.get_all_projects()
        assert all(p["progress_pct"] == 0 for p in projects)


# ---------------------------------------------------------------------------
# get_portfolio_summary
# ---------------------------------------------------------------------------


class TestGetPortfolioSummary:
    def _mock_registry(self, agents: list[Any] | None = None):
        """Build a mock agent registry."""
        registry = MagicMock()
        registry.list_active.return_value = agents or []
        return registry

    def test_summary_keys_present(self, service_with_domains: PortfolioService):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry(),
            ),
        ):
            result = service_with_domains.get_portfolio_summary()
        assert "summary" in result
        assert "projects" in result
        summary = result["summary"]
        assert "total_projects" in summary
        assert "by_status" in summary
        assert "active_agents" in summary
        assert "pending_approvals" in summary

    def test_total_projects_matches_projects_list(self, service_with_domains: PortfolioService):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry(),
            ),
        ):
            result = service_with_domains.get_portfolio_summary()
        assert result["summary"]["total_projects"] == len(result["projects"])

    def test_by_status_counts_are_correct(self, service_with_domains: PortfolioService):
        """All projects should be tallied under by_status."""
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry(),
            ),
        ):
            result = service_with_domains.get_portfolio_summary()
        summary = result["summary"]
        total_by_status = sum(summary["by_status"].values())
        assert total_by_status == summary["total_projects"]

    def _no_tmux(self):
        """Return a patch context that makes SessionTracker return zero sessions."""
        mock_tracker = MagicMock()
        mock_tracker.get_all_sessions.return_value = []
        return patch("forge_harness.session_tracker.SessionTracker", return_value=mock_tracker)

    def _no_state_store(self):
        """Return a patch context where StateStore is not connected."""
        mock_ss = MagicMock()
        mock_ss.is_connected.return_value = False
        return patch("forge_harness.state_store.StateStore", return_value=mock_ss)

    def test_active_agents_from_registry(self, service_with_domains: PortfolioService):
        agent = MagicMock()
        agent.id = "agent-001"
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry([agent]),
            ),
            self._no_state_store(),
            self._no_tmux(),
        ):
            result = service_with_domains.get_portfolio_summary()
        assert result["summary"]["active_agents"] == 1

    def test_active_agents_deduped_between_registry_and_state_store(
        self, service_with_domains: PortfolioService
    ):
        """An agent in both registry and state_store should be counted once."""
        agent = MagicMock()
        agent.id = "shared-id"
        store_agent = MagicMock()
        store_agent.session_id = "shared-id"  # Same ID — dedup should apply

        mock_state_store = MagicMock()
        mock_state_store.is_connected.return_value = True
        mock_state_store.get_active_agents.return_value = [store_agent]

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry([agent]),
            ),
            patch(
                "forge_harness.state_store.StateStore",
                return_value=mock_state_store,
            ),
            self._no_tmux(),
        ):
            result = service_with_domains.get_portfolio_summary()
        # Should be 1, not 2
        assert result["summary"]["active_agents"] == 1

    def test_new_state_store_agent_adds_to_count(self, service_with_domains: PortfolioService):
        """A state_store agent with unique ID increments the count."""
        agent = MagicMock()
        agent.id = "registry-agent"
        store_agent = MagicMock()
        store_agent.session_id = "unique-state-store-agent"

        mock_state_store = MagicMock()
        mock_state_store.is_connected.return_value = True
        mock_state_store.get_active_agents.return_value = [store_agent]

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry([agent]),
            ),
            patch(
                "forge_harness.state_store.StateStore",
                return_value=mock_state_store,
            ),
            self._no_tmux(),
        ):
            result = service_with_domains.get_portfolio_summary()
        assert result["summary"]["active_agents"] == 2

    def test_state_store_connect_failure_handled(self, service_with_domains: PortfolioService):
        mock_state_store = MagicMock()
        mock_state_store.connect.side_effect = Exception("connection refused")

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry(),
            ),
            patch(
                "forge_harness.state_store.StateStore",
                return_value=mock_state_store,
            ),
            self._no_tmux(),
        ):
            result = service_with_domains.get_portfolio_summary()
        # Should not raise
        assert "summary" in result

    def test_state_store_not_connected_skips_agents(self, service_with_domains: PortfolioService):
        mock_state_store = MagicMock()
        mock_state_store.is_connected.return_value = False

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry(),
            ),
            patch(
                "forge_harness.state_store.StateStore",
                return_value=mock_state_store,
            ),
            self._no_tmux(),
        ):
            result = service_with_domains.get_portfolio_summary()
        assert result["summary"]["active_agents"] == 0

    def test_registry_exception_handled_gracefully(self, service_with_domains: PortfolioService):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                side_effect=Exception("registry down"),
            ),
            self._no_state_store(),
            self._no_tmux(),
        ):
            result = service_with_domains.get_portfolio_summary()
        assert "summary" in result
        assert result["summary"]["active_agents"] == 0

    def test_pending_approvals_counted_from_directory(
        self, service_with_domains: PortfolioService
    ):
        approvals_dir = service_with_domains._forge_root / ".forge/approvals"
        approvals_dir.mkdir()
        (approvals_dir / "approval1.json").write_text("{}")
        (approvals_dir / "approval2.json").write_text("{}")
        (approvals_dir / "not-json.txt").write_text("ignore me")

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry(),
            ),
        ):
            result = service_with_domains.get_portfolio_summary()
        assert result["summary"]["pending_approvals"] == 2

    def test_pending_approvals_zero_when_no_directory(
        self, service_with_domains: PortfolioService
    ):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry(),
            ),
        ):
            result = service_with_domains.get_portfolio_summary()
        assert result["summary"]["pending_approvals"] == 0

    def test_tmux_sessions_counted_when_not_seen(self, service_with_domains: PortfolioService):
        """Unique tmux sessions are added to the agent count."""
        session = MagicMock()
        session.session_name = "tmux-session-unique"
        session.window_name = "window-unique"

        mock_tracker = MagicMock()
        mock_tracker.get_all_sessions.return_value = [session]

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry(),
            ),
            self._no_state_store(),
            # Patch at the source module so the local `from … import SessionTracker` resolves
            # to our mock at import time during the method call.
            patch("forge_harness.session_tracker.SessionTracker", return_value=mock_tracker),
        ):
            result = service_with_domains.get_portfolio_summary()
        # The tmux session contributes 1 agent (registry returns 0)
        assert result["summary"]["active_agents"] == 1
        assert "summary" in result

    def test_unknown_status_defaults_to_dev_in_by_status(
        self, service_with_domains: PortfolioService
    ):
        """Projects with an unrecognised status are counted under 'dev'."""
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._mock_registry(),
            ),
            patch.object(
                service_with_domains,
                "get_all_projects",
                return_value=[{"status": "exotic_status"}],
            ),
        ):
            result = service_with_domains.get_portfolio_summary()
        assert result["summary"]["by_status"]["dev"] >= 1


# ---------------------------------------------------------------------------
# get_domain_projects
# ---------------------------------------------------------------------------


class TestGetDomainProjects:
    def test_returns_none_for_unknown_domain(self, service_with_domains: PortfolioService):
        with patch.object(service_with_domains, "_load_features", return_value=None):
            result = service_with_domains.get_domain_projects("no-such-domain")
        assert result is None

    def test_returns_domain_metadata(self, service_with_domains: PortfolioService):
        with patch.object(service_with_domains, "_load_features", return_value=None):
            result = service_with_domains.get_domain_projects("brandfocus-ai")
        assert result is not None
        assert result["domain"]["id"] == "brandfocus-ai"
        assert result["domain"]["display_name"] == "BrandFocus AI"
        assert result["domain"]["compliance"] == ["GDPR"]
        assert result["count"] == 2  # voice-coach + brand-pulse

    def test_project_slug_conversion(self, service_with_domains: PortfolioService):
        with patch.object(service_with_domains, "_load_features", return_value=None):
            result = service_with_domains.get_domain_projects("brandfocus-ai")
        slugs = {p["slug"] for p in result["projects"]}
        assert "voice-coach" in slugs

    def test_status_blocked_when_blocked_features(self, service_with_domains: PortfolioService):
        blocked_features = {"features": [{"status": "blocked"}]}
        with patch.object(service_with_domains, "_load_features", return_value=blocked_features):
            result = service_with_domains.get_domain_projects("brandfocus-ai")
        assert all(p["status"] == "blocked" for p in result["projects"])

    def test_status_failing_when_failing_features(self, service_with_domains: PortfolioService):
        failing_features = {"features": [{"status": "failing"}]}
        with patch.object(service_with_domains, "_load_features", return_value=failing_features):
            result = service_with_domains.get_domain_projects("brandfocus-ai")
        assert all(p["status"] == "failing" for p in result["projects"])

    def test_status_dev_when_pending_features(self, service_with_domains: PortfolioService):
        pending_features = {"features": [{"status": "passing"}, {"status": "pending"}]}
        with patch.object(service_with_domains, "_load_features", return_value=pending_features):
            result = service_with_domains.get_domain_projects("codeswiftr-com")
        assert all(p["status"] == "dev" for p in result["projects"])

    def test_status_ready_when_all_passing(self, service_with_domains: PortfolioService):
        with patch.object(
            service_with_domains, "_load_features", return_value=ALL_PASSING_FEATURES
        ):
            result = service_with_domains.get_domain_projects("codeswiftr-com")
        assert all(p["status"] == "ready" for p in result["projects"])

    def test_status_pending_when_no_features(self, service_with_domains: PortfolioService):
        with patch.object(service_with_domains, "_load_features", return_value=None):
            result = service_with_domains.get_domain_projects("codeswiftr-com")
        assert all(p["status"] == "pending" for p in result["projects"])

    def test_projects_sorted_by_status_priority(self, service_with_domains: PortfolioService):
        """blocked < failing < dev < pending < ready order must be maintained."""
        # Give different features to each product so they get different statuses
        brandfocus_products = ["voice-coach", "brand-pulse"]

        def fake_load_features(domain: str, project: str) -> dict | None:
            if project == "voice-coach":
                return {"features": [{"status": "blocked"}]}
            if project == "brand-pulse":
                return ALL_PASSING_FEATURES
            return None

        with patch.object(service_with_domains, "_load_features", side_effect=fake_load_features):
            result = service_with_domains.get_domain_projects("brandfocus-ai")

        statuses = [p["status"] for p in result["projects"]]
        priority = {"blocked": 0, "failing": 1, "dev": 2, "pending": 3, "ready": 4}
        ranks = [priority[s] for s in statuses]
        assert ranks == sorted(ranks)

    def test_features_counts_included_in_project(self, service_with_domains: PortfolioService):
        with patch.object(
            service_with_domains, "_load_features", return_value=FEATURES_JSON_CONTENT
        ):
            result = service_with_domains.get_domain_projects("brandfocus-ai")
        project = result["projects"][0]
        assert "features" in project
        assert project["features"]["total"] == 4

    def test_content_tier_defaults_to_3_when_missing(self, forge_root: Path):
        import yaml

        domains_no_content = {
            "domains": {
                "no-content-domain": {
                    "active": True,
                    "display_name": "No Content Domain",
                    "products": ["project-a"],
                    "compliance": [],
                    "human_gates": [],
                }
            }
        }
        (forge_root / "forge_harness" / "domains.yaml").write_text(yaml.dump(domains_no_content))
        svc = PortfolioService(forge_root=forge_root)
        with patch.object(svc, "_load_features", return_value=None):
            result = svc.get_domain_projects("no-content-domain")
        assert result["domain"]["content_tier"] == 3


# ---------------------------------------------------------------------------
# get_project_details
# ---------------------------------------------------------------------------


class TestGetProjectDetails:
    def _make_registry_mock(self, agents: list[Any] | None = None):
        registry = MagicMock()
        registry.list_active.return_value = agents or []
        return registry

    def test_returns_none_for_unknown_domain(self, service_with_domains: PortfolioService):
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=self._make_registry_mock(),
        ):
            result = service_with_domains.get_project_details("no-domain", "voice-coach")
        assert result is None

    def test_returns_none_for_unknown_product(self, service_with_domains: PortfolioService):
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=self._make_registry_mock(),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "no-such-project")
        assert result is None

    def test_returns_project_details_structure(self, service_with_domains: PortfolioService):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result is not None
        required_keys = {
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
        }
        assert required_keys.issubset(result.keys())

    def test_production_url_built_from_cloudflare_project(
        self, service_with_domains: PortfolioService
    ):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["production_url"] == "https://brandfocus-voice-coach.pages.dev"

    def test_production_url_none_when_no_cloudflare_project(
        self, service_with_domains: PortfolioService
    ):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details(
                "codeswiftr-com", "interview-simulator"
            )
        assert result["production_url"] is None

    def test_features_list_built_from_features_json(self, service_with_domains: PortfolioService):
        with (
            patch.object(
                service_with_domains, "_load_features", return_value=FEATURES_JSON_CONTENT
            ),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert len(result["features"]["list"]) == 4
        f = result["features"]["list"][0]
        assert "id" in f and "name" in f and "status" in f and "priority" in f

    def test_status_blocked_with_blocked_feature(self, service_with_domains: PortfolioService):
        with (
            patch.object(
                service_with_domains,
                "_load_features",
                return_value={"features": [{"status": "blocked"}]},
            ),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["status"] == "blocked"

    def test_status_failing_with_failing_feature(self, service_with_domains: PortfolioService):
        with (
            patch.object(
                service_with_domains,
                "_load_features",
                return_value={"features": [{"status": "failing"}]},
            ),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["status"] == "failing"

    def test_status_dev_with_pending_feature(self, service_with_domains: PortfolioService):
        with (
            patch.object(
                service_with_domains,
                "_load_features",
                return_value={"features": [{"status": "passing"}, {"status": "pending"}]},
            ),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["status"] == "dev"

    def test_status_ready_with_all_passing(self, service_with_domains: PortfolioService):
        with (
            patch.object(
                service_with_domains, "_load_features", return_value=ALL_PASSING_FEATURES
            ),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["status"] == "ready"

    def test_status_pending_with_no_features(self, service_with_domains: PortfolioService):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["status"] == "pending"

    def test_recent_commits_parsed_from_git_log(self, service_with_domains: PortfolioService):
        project_path = service_with_domains._forge_root.parent / "brandfocus-ai" / "voice-coach"
        project_path.mkdir(parents=True, exist_ok=True)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc1234 Initial commit\ndef5678 Add feature\n"

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")

        assert len(result["recent_commits"]) == 2
        assert result["recent_commits"][0]["hash"] == "abc1234"
        assert result["recent_commits"][0]["message"] == "Initial commit"

    def test_recent_commits_empty_when_git_fails(self, service_with_domains: PortfolioService):
        project_path = service_with_domains._forge_root.parent / "brandfocus-ai" / "voice-coach"
        project_path.mkdir(parents=True, exist_ok=True)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["recent_commits"] == []

    def test_recent_commits_empty_when_project_path_missing(
        self, tmp_path: Path
    ):
        """When the project directory does not exist, subprocess is not called."""
        import yaml

        # Use a completely fresh, isolated root so the project path cannot exist
        isolated_root = tmp_path / "isolated_forge"
        (isolated_root / "forge_harness").mkdir(parents=True)
        domains_path = isolated_root / "forge_harness" / "domains.yaml"
        domains_path.write_text(yaml.dump(DOMAINS_YAML_CONTENT))

        svc = PortfolioService(forge_root=isolated_root)
        # Confirm the project path really does not exist
        project_path = isolated_root.parent / "brandfocus-ai" / "voice-coach"
        assert not project_path.exists(), "Test pre-condition: project dir must be absent"

        with (
            patch.object(svc, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
            patch("subprocess.run") as mock_run,
        ):
            result = svc.get_project_details("brandfocus-ai", "voice-coach")
        mock_run.assert_not_called()
        assert result["recent_commits"] == []

    def test_active_agents_filtered_by_project(self, service_with_domains: PortfolioService):
        agent_match = MagicMock()
        agent_match.project = "brandfocus-ai/voice-coach"
        agent_match.to_dict.return_value = {"id": "a1", "project": "brandfocus-ai/voice-coach"}

        agent_other = MagicMock()
        agent_other.project = "other-domain/other-project"
        agent_other.to_dict.return_value = {"id": "a2"}

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock([agent_match, agent_other]),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert len(result["active_agents"]) == 1
        assert result["active_agents"][0]["id"] == "a1"

    def test_compliance_and_human_gates_from_domain(
        self, service_with_domains: PortfolioService
    ):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["compliance"] == ["GDPR"]
        assert result["human_gates"] == ["deploy"]

    def test_tech_stack_delegated_to_get_tech_stack(self, service_with_domains: PortfolioService):
        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch.object(
                service_with_domains, "_get_tech_stack", return_value=["Go", "PostgreSQL"]
            ),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["tech_stack"] == ["Go", "PostgreSQL"]

    def test_git_subprocess_exception_handled(self, service_with_domains: PortfolioService):
        project_path = service_with_domains._forge_root.parent / "brandfocus-ai" / "voice-coach"
        project_path.mkdir(parents=True, exist_ok=True)

        with (
            patch.object(service_with_domains, "_load_features", return_value=None),
            patch(
                "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                return_value=self._make_registry_mock(),
            ),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5)),
        ):
            result = service_with_domains.get_project_details("brandfocus-ai", "voice-coach")
        assert result["recent_commits"] == []


# ---------------------------------------------------------------------------
# get_portfolio_service  (singleton)
# ---------------------------------------------------------------------------


class TestGetPortfolioService:
    def test_returns_portfolio_service_instance(self):
        from forge_harness.webhook_server.services import portfolio_service as ps_mod

        # Reset global singleton
        ps_mod._portfolio_service = None
        svc = get_portfolio_service()
        assert isinstance(svc, PortfolioService)

    def test_returns_same_instance_on_repeated_calls(self):
        from forge_harness.webhook_server.services import portfolio_service as ps_mod

        ps_mod._portfolio_service = None
        svc1 = get_portfolio_service()
        svc2 = get_portfolio_service()
        assert svc1 is svc2

    def test_existing_instance_is_reused(self):
        from forge_harness.webhook_server.services import portfolio_service as ps_mod

        existing = PortfolioService(forge_root=Path("/tmp"))
        ps_mod._portfolio_service = existing
        result = get_portfolio_service()
        assert result is existing
        # Cleanup
        ps_mod._portfolio_service = None
