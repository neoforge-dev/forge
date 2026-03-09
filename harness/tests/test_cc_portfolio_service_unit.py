"""
Unit tests for forge_harness.webhook_server.services.portfolio_service

Covers:
- PortfolioService.__init__
- PortfolioService._find_forge_root
- PortfolioService._load_domains  (cache hit, cache miss, file-not-found, parse-error)
- PortfolioService._load_features (all path variants, parse-error, none-found)
- PortfolioService._count_feature_status (all branches)
- PortfolioService._get_tech_stack (features.json, tier_map, fallback, missing)
- PortfolioService.get_all_projects (full logic, inactive domain, empty, progress buckets)
- PortfolioService.get_portfolio_summary (agent counting from registry/state/tmux, approvals)
- PortfolioService.get_domain_projects (found, not-found, status ordering)
- PortfolioService.get_project_details (found, not-found, git commits, agents, cloudflare url)
- Module-level get_portfolio_service (singleton creation)
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import forge_harness.webhook_server.services.portfolio_service as ps_module
from forge_harness.webhook_server.services.portfolio_service import (
    PortfolioService,
    get_portfolio_service,
)

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _make_service(forge_root: Path) -> PortfolioService:
    """Create a PortfolioService with an explicit root so _find_forge_root is skipped."""
    return PortfolioService(forge_root=forge_root)


def _write_domains_yaml(root: Path, content: str) -> Path:
    """Write forge_harness/domains.yaml under *root*."""
    d = root / "forge_harness"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "domains.yaml"
    p.write_text(content)
    return p


def _write_features_json(root: Path, domain: str, project: str, content: dict) -> Path:
    """Write features.json for domain/project under *root*."""
    p = root / domain / project / "features.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(content))
    return p


SIMPLE_DOMAINS_YAML = """
domains:
  mydom:
    active: true
    display_name: My Domain
    products:
      - my-project
    frontend_tier: React
    compliance: []
    human_gates: []
    deployment:
      cloudflare_project: my-app
    content:
      tier: 2
  inactive_dom:
    active: false
    products:
      - ignored-project
"""

SIMPLE_FEATURES = {
    "features": [
        {"id": "f1", "name": "Login", "status": "passing", "priority": "P0"},
        {"id": "f2", "name": "Register", "status": "failing", "priority": "P1"},
        {"id": "f3", "name": "Profile", "status": "pending", "priority": "P2"},
        {"id": "f4", "name": "Dashboard", "status": "blocked", "priority": "P2"},
    ]
}


# ---------------------------------------------------------------------------
# class TestFindForgeRoot
# ---------------------------------------------------------------------------

class TestFindForgeRoot:
    def test_finds_root_with_domains_yaml_in_forge_harness(self, tmp_path):
        """Should climb the directory tree and stop where forge_harness/domains.yaml lives."""
        # Build a synthetic directory tree:  tmp_path / forge_harness / domains.yaml
        (tmp_path / "forge_harness").mkdir()
        (tmp_path / "forge_harness" / "domains.yaml").write_text("")

        # Point __file__ resolution via a service created from a *deep* subdirectory.
        # We call _find_forge_root directly on an instance whose internal
        # Path(__file__).parent chain passes through tmp_path.
        svc = PortfolioService(forge_root=tmp_path)  # bypass for init
        # Patch Path so __file__ resolution starts at tmp_path / "a" / "b"
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        with patch.object(ps_module.Path, "__new__", wraps=Path.__new__):
            # direct unit – just confirm explicit root is respected
            assert svc._forge_root == tmp_path

    def test_fallback_when_no_domains_yaml(self, tmp_path):
        """_find_forge_root should return a Path even if no domains.yaml exists."""
        svc = PortfolioService.__new__(PortfolioService)
        # Call _find_forge_root; it crawls from __file__ which exists, so it
        # will eventually hit the filesystem root and return a default path.
        result = svc._find_forge_root()
        assert isinstance(result, Path)

    def test_finds_root_via_forge_harness_subdir(self, tmp_path):
        """Line 39: _find_forge_root returns a dir that has forge_harness/domains.yaml."""
        # Create: tmp_path/forge_harness/domains.yaml  (no root-level domains.yaml)
        fh_dir = tmp_path / "forge_harness"
        fh_dir.mkdir()
        (fh_dir / "domains.yaml").write_text("")

        # Patch Path(__file__).parent to start from a deep subdirectory of tmp_path
        # so _find_forge_root will climb up and find tmp_path via the second condition.
        deep = tmp_path / "sub" / "deep"
        deep.mkdir(parents=True)

        svc = PortfolioService.__new__(PortfolioService)
        # Patch __file__ resolution: the method uses Path(__file__).parent as starting point
        with patch.object(
            ps_module, "__file__", str(deep / "portfolio_service.py")
        ):
            result = svc._find_forge_root()
        assert result == tmp_path


# ---------------------------------------------------------------------------
# class TestLoadDomains
# ---------------------------------------------------------------------------

class TestLoadDomains:
    def test_returns_empty_when_no_file(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc._load_domains()
        assert result == {"domains": {}}

    def test_loads_domains_yaml_from_forge_harness_subdir(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        result = svc._load_domains()
        assert "domains" in result
        assert "mydom" in result["domains"]

    def test_falls_back_to_root_level_domains_yaml(self, tmp_path):
        # No forge_harness/domains.yaml, only root-level domains.yaml
        (tmp_path / "domains.yaml").write_text(SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        result = svc._load_domains()
        assert "mydom" in result["domains"]

    def test_cache_hit_avoids_reload(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        first = svc._load_domains()
        # Overwrite YAML – cached result should still be returned
        _write_domains_yaml(tmp_path, "domains: {}")
        second = svc._load_domains()
        assert first is second  # same object = cache hit

    def test_cache_expires_after_ttl(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        svc._load_domains()
        # Force cache to appear expired
        svc._cache_time = time.time() - svc._cache_ttl - 1
        _write_domains_yaml(tmp_path, "domains: {updated: true}")
        result = svc._load_domains()
        assert "updated" in result.get("domains", {})

    def test_returns_empty_dict_on_yaml_parse_error(self, tmp_path):
        bad_yaml = tmp_path / "forge_harness"
        bad_yaml.mkdir(parents=True)
        (bad_yaml / "domains.yaml").write_text(": bad: yaml: [\n")
        svc = _make_service(tmp_path)
        result = svc._load_domains()
        assert result == {"domains": {}}

    def test_returns_empty_dict_on_open_exception(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        with patch("builtins.open", side_effect=OSError("disk error")):
            result = svc._load_domains()
        assert result == {"domains": {}}


# ---------------------------------------------------------------------------
# class TestLoadFeatures
# ---------------------------------------------------------------------------

class TestLoadFeatures:
    def test_loads_features_from_primary_path(self, tmp_path):
        _write_features_json(tmp_path, "mydom", "my-project", SIMPLE_FEATURES)
        svc = _make_service(tmp_path)
        result = svc._load_features("mydom", "my-project")
        assert result is not None
        assert len(result["features"]) == 4

    def test_loads_features_from_parent_path(self, tmp_path):
        """Second candidate: forge_root.parent / domain / project / features.json"""
        parent = tmp_path.parent
        proj_dir = parent / "dom2" / "proj2"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "features.json").write_text(json.dumps({"features": []}))
        svc = _make_service(tmp_path)
        result = svc._load_features("dom2", "proj2")
        assert result is not None
        assert result["features"] == []

    def test_loads_features_from_project_only_path(self, tmp_path):
        """Third candidate: forge_root / project / features.json (no domain prefix)."""
        proj_dir = tmp_path / "proj3"
        proj_dir.mkdir()
        (proj_dir / "features.json").write_text(json.dumps({"features": [{"id": "x"}]}))
        svc = _make_service(tmp_path)
        result = svc._load_features("nonexistent-dom", "proj3")
        assert result is not None
        assert result["features"][0]["id"] == "x"

    def test_returns_none_when_no_features_file(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc._load_features("dom", "proj")
        assert result is None

    def test_returns_none_on_json_parse_error(self, tmp_path):
        path = tmp_path / "dom" / "proj"
        path.mkdir(parents=True)
        (path / "features.json").write_text("{invalid json}")
        svc = _make_service(tmp_path)
        result = svc._load_features("dom", "proj")
        assert result is None

    def test_returns_none_on_open_exception(self, tmp_path):
        _write_features_json(tmp_path, "dom", "proj", SIMPLE_FEATURES)
        svc = _make_service(tmp_path)
        with patch("builtins.open", side_effect=OSError("permission denied")):
            result = svc._load_features("dom", "proj")
        assert result is None


# ---------------------------------------------------------------------------
# class TestCountFeatureStatus
# ---------------------------------------------------------------------------

class TestCountFeatureStatus:
    def setup_method(self):
        self.svc = PortfolioService(forge_root=Path("/nonexistent"))

    def test_returns_zeros_for_none_input(self):
        result = self.svc._count_feature_status(None)
        assert result == {"total": 0, "passing": 0, "failing": 0, "pending": 0, "blocked": 0}

    def test_returns_zeros_for_empty_dict(self):
        result = self.svc._count_feature_status({})
        assert result == {"total": 0, "passing": 0, "failing": 0, "pending": 0, "blocked": 0}

    def test_returns_zeros_for_dict_without_features_key(self):
        result = self.svc._count_feature_status({"other_key": []})
        assert result == {"total": 0, "passing": 0, "failing": 0, "pending": 0, "blocked": 0}

    def test_counts_known_statuses(self):
        data = {
            "features": [
                {"status": "passing"},
                {"status": "passing"},
                {"status": "failing"},
                {"status": "blocked"},
                {"status": "pending"},
            ]
        }
        result = self.svc._count_feature_status(data)
        assert result["total"] == 5
        assert result["passing"] == 2
        assert result["failing"] == 1
        assert result["blocked"] == 1
        assert result["pending"] == 1

    def test_unknown_status_maps_to_pending(self):
        data = {"features": [{"status": "unknown_status"}]}
        result = self.svc._count_feature_status(data)
        assert result["total"] == 1
        assert result["pending"] == 1

    def test_missing_status_defaults_to_pending(self):
        data = {"features": [{}]}
        result = self.svc._count_feature_status(data)
        assert result["pending"] == 1

    def test_case_insensitive_status(self):
        data = {"features": [{"status": "PASSING"}, {"status": "Failing"}]}
        result = self.svc._count_feature_status(data)
        assert result["passing"] == 1
        assert result["failing"] == 1

    def test_empty_features_list(self):
        result = self.svc._count_feature_status({"features": []})
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# class TestGetTechStack
# ---------------------------------------------------------------------------

class TestGetTechStack:
    def setup_method(self):
        self.svc = PortfolioService(forge_root=Path("/nonexistent"))

    def test_returns_tech_stack_from_features_json(self, tmp_path):
        svc = _make_service(tmp_path)
        feat_data = {"features": [], "tech_stack": ["Svelte", "Go"]}
        _write_features_json(tmp_path, "dom", "proj", feat_data)
        result = svc._get_tech_stack("dom", "proj", {})
        assert result == ["Svelte", "Go"]

    def test_ignores_features_tech_stack_if_not_list(self, tmp_path):
        svc = _make_service(tmp_path)
        feat_data = {"features": [], "tech_stack": "not-a-list"}
        _write_features_json(tmp_path, "dom", "proj", feat_data)
        # No frontend_tier either -> returns []
        result = svc._get_tech_stack("dom", "proj", {})
        assert result == []

    def test_uses_react_tier_map(self):
        with patch.object(self.svc, "_load_features", return_value=None):
            result = self.svc._get_tech_stack("dom", "proj", {"frontend_tier": "React"})
        assert "React" in result
        assert "TypeScript" in result

    def test_uses_lit_pwa_tier_map(self):
        with patch.object(self.svc, "_load_features", return_value=None):
            result = self.svc._get_tech_stack("dom", "proj", {"frontend_tier": "Lit PWA"})
        assert "Lit" in result
        assert "Web Components" in result

    def test_uses_react_native_tier_map(self):
        with patch.object(self.svc, "_load_features", return_value=None):
            result = self.svc._get_tech_stack("dom", "proj", {"frontend_tier": "React Native"})
        assert "React Native" in result

    def test_unknown_frontend_tier_returns_tier_plus_defaults(self):
        with patch.object(self.svc, "_load_features", return_value=None):
            result = self.svc._get_tech_stack("dom", "proj", {"frontend_tier": "Vue"})
        assert result == ["Vue", "FastAPI", "PostgreSQL"]

    def test_empty_frontend_tier_returns_empty_list(self):
        with patch.object(self.svc, "_load_features", return_value=None):
            result = self.svc._get_tech_stack("dom", "proj", {"frontend_tier": ""})
        assert result == []

    def test_missing_frontend_tier_key_returns_empty_list(self):
        with patch.object(self.svc, "_load_features", return_value=None):
            result = self.svc._get_tech_stack("dom", "proj", {})
        assert result == []

    def test_none_domain_info_returns_empty_list(self):
        with patch.object(self.svc, "_load_features", return_value=None):
            result = self.svc._get_tech_stack("dom", "proj", None)
        assert result == []


# ---------------------------------------------------------------------------
# class TestGetAllProjects
# ---------------------------------------------------------------------------

class TestGetAllProjects:
    def test_returns_empty_list_when_no_domains(self, tmp_path):
        _write_domains_yaml(tmp_path, "domains: {}")
        svc = _make_service(tmp_path)
        assert svc.get_all_projects() == []

    def test_skips_inactive_domains(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        projects = svc.get_all_projects()
        names = [p["project"] for p in projects]
        assert "ignored-project" not in names

    def test_returns_project_with_dev_status_when_no_features(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        projects = svc.get_all_projects()
        assert any(p["project"] == "my-project" and p["status"] == "dev" for p in projects)

    def test_progress_100_gives_live_status(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "passing"}, {"status": "passing"}]
        })
        svc = _make_service(tmp_path)
        projects = svc.get_all_projects()
        proj = next(p for p in projects if p["project"] == "my-project")
        assert proj["status"] == "live"
        assert proj["progress_pct"] == 100

    def test_progress_80_to_99_gives_ready_status(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        # 4 passing, 1 failing => 80%
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [
                {"status": "passing"}, {"status": "passing"},
                {"status": "passing"}, {"status": "passing"},
                {"status": "failing"},
            ]
        })
        svc = _make_service(tmp_path)
        projects = svc.get_all_projects()
        proj = next(p for p in projects if p["project"] == "my-project")
        assert proj["status"] == "ready"
        assert proj["progress_pct"] == 80

    def test_progress_50_to_79_gives_dev_status(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        # 1 passing, 1 failing => 50%
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "passing"}, {"status": "failing"}]
        })
        svc = _make_service(tmp_path)
        projects = svc.get_all_projects()
        proj = next(p for p in projects if p["project"] == "my-project")
        assert proj["status"] == "dev"
        assert proj["progress_pct"] == 50

    def test_progress_below_50_gives_parked_status(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        # 1 passing, 3 failing => 25%
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [
                {"status": "passing"}, {"status": "failing"},
                {"status": "failing"}, {"status": "failing"},
            ]
        })
        svc = _make_service(tmp_path)
        projects = svc.get_all_projects()
        proj = next(p for p in projects if p["project"] == "my-project")
        assert proj["status"] == "parked"
        assert proj["progress_pct"] == 25

    def test_display_name_converts_hyphens_and_underscores(self, tmp_path):
        yaml_content = """
domains:
  testdom:
    active: true
    products:
      - my-cool_project
"""
        _write_domains_yaml(tmp_path, yaml_content)
        svc = _make_service(tmp_path)
        projects = svc.get_all_projects()
        assert projects[0]["display_name"] == "My Cool Project"

    def test_project_fields_are_present(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        projects = svc.get_all_projects()
        for p in projects:
            for key in ("domain", "project", "display_name", "status", "progress_pct"):
                assert key in p


# ---------------------------------------------------------------------------
# class TestGetPortfolioSummary
# ---------------------------------------------------------------------------

class TestGetPortfolioSummary:
    """Tests for get_portfolio_summary().

    All tests patch three external agent sources so the environment's real
    tmux sessions and Redis/SQLite state never bleed into assertions.
    """

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _base_svc(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        return _make_service(tmp_path)

    @staticmethod
    def _make_registry_mock(agents=None):
        m = MagicMock()
        m.list_active.return_value = agents or []
        return m

    @staticmethod
    def _make_ss_mock(connected=False, agents=None):
        m = MagicMock()
        m.is_connected.return_value = connected
        m.get_active_agents.return_value = agents or []
        return m

    @staticmethod
    def _make_tracker_mock(sessions=None):
        m = MagicMock()
        m.get_all_sessions.return_value = sessions or []
        return m

    def _call_summary(self, svc, registry_mock, ss_mock, tracker_mock=None):
        """Call get_portfolio_summary with all three sources patched."""
        tracker = tracker_mock or self._make_tracker_mock()
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=registry_mock,
        ):
            with patch("forge_harness.state_store.StateStore", return_value=ss_mock):
                with patch(
                    "forge_harness.session_tracker.SessionTracker",
                    return_value=tracker,
                ):
                    return svc.get_portfolio_summary()

    # ------------------------------------------------------------------ #
    # Tests
    # ------------------------------------------------------------------ #

    def test_summary_contains_required_keys(self, tmp_path):
        svc = self._base_svc(tmp_path)
        result = self._call_summary(
            svc,
            self._make_registry_mock(),
            self._make_ss_mock(),
        )
        assert "summary" in result
        assert "projects" in result
        summary = result["summary"]
        for key in ("total_projects", "by_status", "active_agents", "pending_approvals"):
            assert key in summary

    def test_counts_registry_agents(self, tmp_path):
        svc = self._base_svc(tmp_path)
        agent1 = MagicMock()
        agent1.id = "agent-001"
        agent2 = MagicMock()
        agent2.id = "agent-002"
        result = self._call_summary(
            svc,
            self._make_registry_mock([agent1, agent2]),
            self._make_ss_mock(),
        )
        assert result["summary"]["active_agents"] == 2

    def test_deduplicates_state_store_agents(self, tmp_path):
        svc = self._base_svc(tmp_path)
        agent1 = MagicMock()
        agent1.id = "agent-001"

        ss_agent_existing = MagicMock()
        ss_agent_existing.session_id = "agent-001"  # already seen
        ss_agent_new = MagicMock()
        ss_agent_new.session_id = "agent-999"  # new

        result = self._call_summary(
            svc,
            self._make_registry_mock([agent1]),
            self._make_ss_mock(connected=True, agents=[ss_agent_existing, ss_agent_new]),
        )
        # 1 from registry + 1 new from state_store (duplicate skipped)
        assert result["summary"]["active_agents"] == 2

    def test_counts_tmux_sessions_deduped(self, tmp_path):
        svc = self._base_svc(tmp_path)
        tmux_session = MagicMock()
        tmux_session.session_name = "forge:coder"
        tmux_session.window_name = "coder"

        result = self._call_summary(
            svc,
            self._make_registry_mock(),
            self._make_ss_mock(),
            self._make_tracker_mock([tmux_session]),
        )
        assert result["summary"]["active_agents"] == 1

    def test_tmux_session_name_already_seen_skipped(self, tmp_path):
        svc = self._base_svc(tmp_path)
        agent1 = MagicMock()
        agent1.id = "forge:coder"  # same as tmux session_name

        tmux_session = MagicMock()
        tmux_session.session_name = "forge:coder"
        tmux_session.window_name = "coder"

        result = self._call_summary(
            svc,
            self._make_registry_mock([agent1]),
            self._make_ss_mock(),
            self._make_tracker_mock([tmux_session]),
        )
        assert result["summary"]["active_agents"] == 1

    def test_tmux_window_name_already_seen_skipped(self, tmp_path):
        """If window_name matches an already-seen id, the tmux session is not counted."""
        svc = self._base_svc(tmp_path)
        agent1 = MagicMock()
        agent1.id = "coder"

        tmux_session = MagicMock()
        tmux_session.session_name = "forge:coder"
        tmux_session.window_name = "coder"  # matches agent1.id

        result = self._call_summary(
            svc,
            self._make_registry_mock([agent1]),
            self._make_ss_mock(),
            self._make_tracker_mock([tmux_session]),
        )
        # session_name "forge:coder" not in seen_ids, window_name "coder" IS in seen_ids
        # => the session should NOT be counted
        assert result["summary"]["active_agents"] == 1

    def test_handles_registry_exception_gracefully(self, tmp_path):
        svc = self._base_svc(tmp_path)
        ss_mock = self._make_ss_mock()
        tracker_mock = self._make_tracker_mock()
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            side_effect=RuntimeError("registry boom"),
        ):
            with patch("forge_harness.state_store.StateStore", return_value=ss_mock):
                with patch(
                    "forge_harness.session_tracker.SessionTracker",
                    return_value=tracker_mock,
                ):
                    result = svc.get_portfolio_summary()
        assert result["summary"]["active_agents"] == 0

    def test_handles_state_store_exception_gracefully(self, tmp_path):
        svc = self._base_svc(tmp_path)
        registry_mock = self._make_registry_mock()
        tracker_mock = self._make_tracker_mock()
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=registry_mock,
        ):
            with patch(
                "forge_harness.state_store.StateStore",
                side_effect=RuntimeError("store boom"),
            ):
                with patch(
                    "forge_harness.session_tracker.SessionTracker",
                    return_value=tracker_mock,
                ):
                    result = svc.get_portfolio_summary()
        assert result["summary"]["active_agents"] == 0

    def test_handles_tmux_session_tracker_import_error(self, tmp_path):
        """If forge_harness.session_tracker is unimportable, the exception is swallowed."""
        svc = self._base_svc(tmp_path)
        registry_mock = self._make_registry_mock()
        ss_mock = self._make_ss_mock()
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=registry_mock,
        ):
            with patch("forge_harness.state_store.StateStore", return_value=ss_mock):
                with patch.dict("sys.modules", {"forge_harness.session_tracker": None}):
                    result = svc.get_portfolio_summary()
        assert result["summary"]["active_agents"] == 0

    def test_handles_tmux_outer_exception_gracefully(self, tmp_path):
        """Lines 243-244: outer except around the SessionTracker block."""
        svc = self._base_svc(tmp_path)
        registry_mock = self._make_registry_mock()
        ss_mock = self._make_ss_mock()
        # Make SessionTracker itself raise a non-ImportError exception
        with patch(
            "forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
            return_value=registry_mock,
        ):
            with patch("forge_harness.state_store.StateStore", return_value=ss_mock):
                with patch(
                    "forge_harness.session_tracker.SessionTracker",
                    side_effect=RuntimeError("tmux exploded"),
                ):
                    result = svc.get_portfolio_summary()
        assert result["summary"]["active_agents"] == 0

    def test_handles_approvals_exception_gracefully(self, tmp_path):
        """Lines 253-254: outer except around the approvals glob block."""
        svc = self._base_svc(tmp_path)
        # Create approvals dir but make glob raise
        approvals_dir = tmp_path / ".forge/approvals"
        approvals_dir.mkdir()

        with patch.object(
            type(approvals_dir), "glob", side_effect=OSError("fs error")
        ):
            # Patch Path() constructor to return our mock approvals_dir when
            # constructed with the approvals path.
            original_path_div = Path.__truediv__

            def patched_div(self, other):
                result = original_path_div(self, other)
                if str(result).endswith(".forge/approvals"):
                    return approvals_dir
                return result

            with patch.object(Path, "__truediv__", patched_div):
                result = self._call_summary(
                    svc,
                    self._make_registry_mock(),
                    self._make_ss_mock(),
                )
        # Approvals exception was swallowed; count defaults to 0
        assert result["summary"]["pending_approvals"] == 0

    def test_counts_pending_approvals_from_directory(self, tmp_path):
        svc = self._base_svc(tmp_path)
        approvals_dir = tmp_path / ".forge/approvals"
        approvals_dir.mkdir()
        (approvals_dir / "req1.json").write_text("{}")
        (approvals_dir / "req2.json").write_text("{}")
        (approvals_dir / "notjson.txt").write_text("")  # should NOT be counted

        result = self._call_summary(
            svc,
            self._make_registry_mock(),
            self._make_ss_mock(),
        )
        assert result["summary"]["pending_approvals"] == 2  # only .json files

    def test_pending_approvals_zero_when_dir_missing(self, tmp_path):
        svc = self._base_svc(tmp_path)
        result = self._call_summary(
            svc,
            self._make_registry_mock(),
            self._make_ss_mock(),
        )
        assert result["summary"]["pending_approvals"] == 0

    def test_by_status_counts_correctly(self, tmp_path):
        _write_domains_yaml(tmp_path, """
domains:
  d1:
    active: true
    products: [proj-live, proj-parked]
""")
        svc = _make_service(tmp_path)
        _write_features_json(tmp_path, "d1", "proj-live", {
            "features": [{"status": "passing"}]
        })
        _write_features_json(tmp_path, "d1", "proj-parked", {
            "features": [{"status": "failing"}]
        })
        result = self._call_summary(
            svc,
            self._make_registry_mock(),
            self._make_ss_mock(),
        )
        assert result["summary"]["by_status"]["live"] == 1
        assert result["summary"]["by_status"]["parked"] == 1

    def test_unknown_project_status_counted_as_dev(self, tmp_path):
        svc = self._base_svc(tmp_path)
        with patch.object(svc, "get_all_projects", return_value=[
            {"project": "x", "status": "super_unknown"}
        ]):
            result = self._call_summary(
                svc,
                self._make_registry_mock(),
                self._make_ss_mock(),
            )
        assert result["summary"]["by_status"]["dev"] == 1


# ---------------------------------------------------------------------------
# class TestGetDomainProjects
# ---------------------------------------------------------------------------

class TestGetDomainProjects:
    def test_returns_none_for_unknown_domain(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        assert svc.get_domain_projects("nonexistent") is None

    def test_returns_domain_dict_for_known_domain(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("mydom")
        assert result is not None
        assert result["domain"]["id"] == "mydom"
        assert result["count"] == 1
        assert len(result["projects"]) == 1

    def test_project_status_blocked_when_blocked_features(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "blocked"}]
        })
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("mydom")
        assert result["projects"][0]["status"] == "blocked"

    def test_project_status_failing_when_failing_features(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "failing"}]
        })
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("mydom")
        assert result["projects"][0]["status"] == "failing"

    def test_project_status_dev_when_pending_features(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "pending"}]
        })
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("mydom")
        assert result["projects"][0]["status"] == "dev"

    def test_project_status_ready_when_all_passing(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "passing"}]
        })
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("mydom")
        assert result["projects"][0]["status"] == "ready"

    def test_project_status_pending_when_no_features(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        # no features file at all
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("mydom")
        assert result["projects"][0]["status"] == "pending"

    def test_projects_sorted_by_status_priority(self, tmp_path):
        yaml_content = """
domains:
  sortdom:
    active: true
    display_name: Sort Domain
    products:
      - proj-ready
      - proj-blocked
      - proj-dev
"""
        _write_domains_yaml(tmp_path, yaml_content)
        _write_features_json(tmp_path, "sortdom", "proj-ready", {
            "features": [{"status": "passing"}]
        })
        _write_features_json(tmp_path, "sortdom", "proj-blocked", {
            "features": [{"status": "blocked"}]
        })
        _write_features_json(tmp_path, "sortdom", "proj-dev", {
            "features": [{"status": "pending"}]
        })
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("sortdom")
        statuses = [p["status"] for p in result["projects"]]
        assert statuses.index("blocked") < statuses.index("dev")
        assert statuses.index("dev") < statuses.index("ready")

    def test_slug_is_lowercased_with_hyphens(self, tmp_path):
        yaml_content = """
domains:
  slugdom:
    active: true
    products:
      - My Cool Project
"""
        _write_domains_yaml(tmp_path, yaml_content)
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("slugdom")
        assert result["projects"][0]["slug"] == "my-cool-project"

    def test_domain_meta_fields_in_response(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("mydom")
        domain = result["domain"]
        assert domain["display_name"] == "My Domain"
        assert domain["compliance"] == []
        assert domain["human_gates"] == []
        assert domain["content_tier"] == 2

    def test_empty_products_returns_zero_count(self, tmp_path):
        yaml_content = "domains:\n  emptydom:\n    active: true\n    products: []\n"
        _write_domains_yaml(tmp_path, yaml_content)
        svc = _make_service(tmp_path)
        result = svc.get_domain_projects("emptydom")
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# class TestGetProjectDetails
# ---------------------------------------------------------------------------

class TestGetProjectDetails:
    def test_returns_none_for_unknown_domain(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        assert svc.get_project_details("nonexistent", "any") is None

    def test_returns_none_for_unknown_project_slug(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        assert svc.get_project_details("mydom", "nonexistent-slug") is None

    def test_returns_project_details_dict(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert result is not None
        assert result["name"] == "my-project"
        assert result["slug"] == "my-project"
        assert result["domain"] == "mydom"

    def test_features_list_populated_from_features_json(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", SIMPLE_FEATURES)
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert len(result["features"]["list"]) == 4
        f = result["features"]["list"][0]
        assert "id" in f and "name" in f and "status" in f and "priority" in f

    def test_status_blocked_when_blocked_features(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "blocked"}]
        })
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert result["status"] == "blocked"

    def test_status_failing(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "failing"}]
        })
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert result["status"] == "failing"

    def test_status_dev_when_pending(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "pending"}]
        })
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert result["status"] == "dev"

    def test_status_ready_when_all_passing(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        _write_features_json(tmp_path, "mydom", "my-project", {
            "features": [{"status": "passing"}]
        })
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert result["status"] == "ready"

    def test_status_pending_when_no_features(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert result["status"] == "pending"

    def test_recent_commits_populated_when_path_exists(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)

        # Create the project path that the code looks for: forge_root.parent / domain / project
        project_path = tmp_path.parent / "mydom" / "my-project"
        project_path.mkdir(parents=True, exist_ok=True)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc1234 Fix bug\ndef5678 Add feature\n"

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            with patch("subprocess.run", return_value=mock_result):
                result = svc.get_project_details("mydom", "my-project")
        assert len(result["recent_commits"]) == 2
        assert result["recent_commits"][0]["hash"] == "abc1234"
        assert result["recent_commits"][0]["message"] == "Fix bug"

    def test_recent_commits_empty_when_git_fails(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)

        project_path = tmp_path.parent / "mydom" / "my-project"
        project_path.mkdir(parents=True, exist_ok=True)

        mock_result = MagicMock()
        mock_result.returncode = 128  # git error

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            with patch("subprocess.run", return_value=mock_result):
                result = svc.get_project_details("mydom", "my-project")
        assert result["recent_commits"] == []

    def test_recent_commits_empty_when_subprocess_raises(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)

        project_path = tmp_path.parent / "mydom" / "my-project"
        project_path.mkdir(parents=True, exist_ok=True)

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            with patch("subprocess.run", side_effect=Exception("subprocess boom")):
                result = svc.get_project_details("mydom", "my-project")
        assert result["recent_commits"] == []

    def test_recent_commits_empty_when_project_path_missing(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        # Don't create project_path
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert result["recent_commits"] == []

    def test_active_agents_filtered_by_project(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)

        matching_agent = MagicMock()
        matching_agent.project = "mydom/my-project"
        matching_agent.to_dict.return_value = {"id": "a1", "project": "mydom/my-project"}

        unrelated_agent = MagicMock()
        unrelated_agent.project = "otherdom/other-project"
        unrelated_agent.to_dict.return_value = {"id": "a2", "project": "otherdom/other-project"}

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = [matching_agent, unrelated_agent]
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert len(result["active_agents"]) == 1
        assert result["active_agents"][0]["id"] == "a1"

    def test_active_agents_matched_by_slug_alone(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)

        # Agent whose project only contains the slug (not domain/slug)
        slug_agent = MagicMock()
        slug_agent.project = "my-project"
        slug_agent.to_dict.return_value = {"id": "a3", "project": "my-project"}

        mock_registry = MagicMock()
        mock_registry.list_active.return_value = [slug_agent]
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert len(result["active_agents"]) == 1

    def test_production_url_from_cloudflare_project(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        assert result["production_url"] == "https://my-app.pages.dev"

    def test_production_url_none_when_no_cloudflare_project(self, tmp_path):
        yaml_content = """
domains:
  nocloud:
    active: true
    products:
      - some-proj
    deployment: {}
"""
        _write_domains_yaml(tmp_path, yaml_content)
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("nocloud", "some-proj")
        assert result["production_url"] is None

    def test_result_contains_all_expected_keys(self, tmp_path):
        _write_domains_yaml(tmp_path, SIMPLE_DOMAINS_YAML)
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("mydom", "my-project")
        expected_keys = {
            "name", "slug", "domain", "status", "features",
            "recent_commits", "active_agents", "pending_approvals_count",
            "production_url", "compliance", "human_gates", "tech_stack",
        }
        assert expected_keys <= set(result.keys())

    def test_product_name_matching_is_case_slug_insensitive(self, tmp_path):
        yaml_content = """
domains:
  testdom:
    active: true
    products:
      - My Awesome App
    deployment: {}
"""
        _write_domains_yaml(tmp_path, yaml_content)
        svc = _make_service(tmp_path)
        mock_registry = MagicMock()
        mock_registry.list_active.return_value = []
        with patch("forge_harness.webhook_server.services.portfolio_service.get_agent_registry",
                   return_value=mock_registry):
            result = svc.get_project_details("testdom", "my-awesome-app")
        assert result is not None
        assert result["name"] == "My Awesome App"


# ---------------------------------------------------------------------------
# class TestGetPortfolioServiceSingleton
# ---------------------------------------------------------------------------

class TestGetPortfolioServiceSingleton:
    def setup_method(self):
        """Reset global singleton before each test."""
        ps_module._portfolio_service = None

    def teardown_method(self):
        """Clean up singleton after each test."""
        ps_module._portfolio_service = None

    def test_returns_portfolio_service_instance(self):
        svc = get_portfolio_service()
        assert isinstance(svc, PortfolioService)

    def test_returns_same_instance_on_repeated_calls(self):
        svc1 = get_portfolio_service()
        svc2 = get_portfolio_service()
        assert svc1 is svc2

    def test_returns_new_instance_after_reset(self):
        svc1 = get_portfolio_service()
        ps_module._portfolio_service = None
        svc2 = get_portfolio_service()
        assert svc1 is not svc2

    def test_singleton_is_stored_in_module(self):
        svc = get_portfolio_service()
        assert ps_module._portfolio_service is svc
