"""Tests for Feature Progress API endpoints.

Tests for:
    GET  /api/features                       - List features (filters, empty case)
    GET  /api/features/stats                 - Feature statistics
    GET  /api/features/{feature_id}          - Get single feature
    PUT  /api/features/{feature_id}          - Update progress/status
    POST /api/features/{feature_id}/assign   - Assign agent
    POST /api/features/{feature_id}/link-task - Link task

Coverage targets: ~90%+ of api/features.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Make sure harness root is importable
harness_root = Path(__file__).parent.parent
sys.path.insert(0, str(harness_root))

from forge_harness.webhook_server.api.features import router
from forge_harness.webhook_server.services.feature_tracker import (
    FeatureProgress,
    FeatureTracker,
    reset_feature_tracker,
)

# ---------------------------------------------------------------------------
# Test app wiring
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the singleton is cleared before and after every test."""
    reset_feature_tracker()
    yield
    reset_feature_tracker()


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """Build a minimal FastAPI app with the features router and no-auth state."""
    _app = FastAPI()

    # Disable auth — inject allow_localhost flag via app state
    from dataclasses import dataclass

    @dataclass
    class _AuthConfig:
        require_auth: bool = False
        allow_localhost: bool = True

    _app.state.auth_config = _AuthConfig()
    _app.include_router(router)
    return _app


@pytest.fixture
def tracker(tmp_path: Path) -> FeatureTracker:
    """Create a FeatureTracker using temp dirs and register it as the singleton."""
    from forge_harness.webhook_server.services.feature_tracker import (
        _tracker_lock,
        get_feature_tracker,
    )

    t = FeatureTracker(
        features_dir=tmp_path / "portfolio",
        progress_file=tmp_path / ".forge" / "features" / "progress.jsonl",
    )
    # Inject as singleton so the API's _get_tracker() returns it
    import forge_harness.webhook_server.services.feature_tracker as _mod

    with _tracker_lock:
        _mod._tracker_instance = t
    return t


@pytest.fixture
def client(app: FastAPI, tracker: FeatureTracker) -> TestClient:
    """Return a TestClient for the features router."""
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_feature(
    tracker: FeatureTracker,
    feature_id: str = "feat-001",
    name: str = "Login Flow",
    domain: str = "codeswiftr-com",
    project: str = "interview-sim",
    completion_pct: float = 0.0,
    status: str = "planned",
) -> FeatureProgress:
    return tracker.update_progress(
        feature_id=feature_id,
        completion_pct=completion_pct,
        status=status,
        name=name,
        domain=domain,
        project=project,
    )


# ===========================================================================
# GET /api/features
# ===========================================================================


class TestListFeatures:
    def test_empty_returns_empty_list(self, client: TestClient):
        resp = client.get("/api/features")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["features"] == []
        assert data["data"]["count"] == 0

    def test_returns_all_features(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        _seed_feature(tracker, "f2")
        resp = client.get("/api/features")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["count"] == 2

    def test_filter_by_domain(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1", domain="codeswiftr-com")
        _seed_feature(tracker, "f2", domain="brandfocus-ai")
        resp = client.get("/api/features?domain=codeswiftr-com")
        data = resp.json()["data"]
        assert data["count"] == 1
        assert data["features"][0]["feature_id"] == "f1"

    def test_filter_by_project(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1", project="interview-sim")
        _seed_feature(tracker, "f2", project="voice-coach")
        resp = client.get("/api/features?project=interview-sim")
        data = resp.json()["data"]
        assert data["count"] == 1
        assert data["features"][0]["feature_id"] == "f1"

    def test_filter_by_status(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1", status="planned")
        _seed_feature(tracker, "f2", status="in_progress", completion_pct=50.0)
        resp = client.get("/api/features?status=in_progress")
        data = resp.json()["data"]
        assert data["count"] == 1
        assert data["features"][0]["feature_id"] == "f2"

    def test_filter_no_match_returns_empty(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1", domain="codeswiftr-com")
        resp = client.get("/api/features?domain=nonexistent-domain")
        data = resp.json()["data"]
        assert data["count"] == 0
        assert data["features"] == []

    def test_response_envelope_fields(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        resp = client.get("/api/features")
        body = resp.json()
        assert "success" in body
        assert "data" in body
        assert "error" in body
        assert "timestamp" in body

    def test_feature_dict_has_expected_keys(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        resp = client.get("/api/features")
        feature = resp.json()["data"]["features"][0]
        expected_keys = {
            "feature_id",
            "name",
            "domain",
            "project",
            "status",
            "completion_pct",
            "assigned_agent",
            "tasks",
            "started_at",
            "updated_at",
        }
        assert expected_keys.issubset(feature.keys())


# ===========================================================================
# GET /api/features/stats
# ===========================================================================


class TestGetStats:
    def test_empty_stats(self, client: TestClient):
        resp = client.get("/api/features/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["by_status"]["planned"] == 0
        assert data["by_domain"] == {}

    def test_stats_with_features(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1", domain="d1", status="planned")
        _seed_feature(tracker, "f2", domain="d1", status="in_progress", completion_pct=50.0)
        _seed_feature(tracker, "f3", domain="d2", status="done", completion_pct=100.0)

        resp = client.get("/api/features/stats")
        data = resp.json()["data"]
        assert data["total"] == 3
        assert data["by_status"]["planned"] == 1
        assert data["by_status"]["in_progress"] == 1
        assert data["by_status"]["done"] == 1
        assert data["by_domain"]["d1"] == 2
        assert data["by_domain"]["d2"] == 1

    def test_stats_not_shadowed_by_feature_id_route(self, client: TestClient):
        """Ensure /api/features/stats is not treated as GET /api/features/{feature_id}."""
        resp = client.get("/api/features/stats")
        # Should succeed with stats structure, not a 404
        assert resp.status_code == 200
        body = resp.json()
        assert "by_status" in body["data"]


# ===========================================================================
# GET /api/features/{feature_id}
# ===========================================================================


class TestGetFeature:
    def test_returns_feature_for_existing_id(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "feat-auth", name="Auth", domain="d", project="p")
        resp = client.get("/api/features/feat-auth")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_id"] == "feat-auth"
        assert data["name"] == "Auth"

    def test_returns_404_for_missing_feature(self, client: TestClient):
        resp = client.get("/api/features/does-not-exist")
        assert resp.status_code == 404
        body = resp.json()
        # FastAPI wraps HTTPException detail in {"detail": ...}
        assert "detail" in body or "error" in body

    def test_returned_feature_has_all_fields(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1", completion_pct=42.0, status="in_progress")
        resp = client.get("/api/features/f1")
        data = resp.json()["data"]
        assert data["completion_pct"] == 42.0
        assert data["status"] == "in_progress"


# ===========================================================================
# PUT /api/features/{feature_id}
# ===========================================================================


class TestUpdateFeature:
    def test_update_completion_pct(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        resp = client.put("/api/features/f1", json={"completion_pct": 75.0})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["completion_pct"] == 75.0

    def test_update_status_only(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1", completion_pct=50.0)
        resp = client.put("/api/features/f1", json={"status": "testing"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "testing"
        # Completion pct should be preserved
        assert data["completion_pct"] == 50.0

    def test_update_both_pct_and_status(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        resp = client.put(
            "/api/features/f1",
            json={"completion_pct": 90.0, "status": "testing"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["completion_pct"] == 90.0
        assert data["status"] == "testing"

    def test_creates_new_record_if_missing(self, client: TestClient):
        resp = client.put(
            "/api/features/brand-new",
            json={"completion_pct": 10.0, "name": "New Feature"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_id"] == "brand-new"

    def test_422_when_no_fields_provided(self, client: TestClient):
        resp = client.put("/api/features/f1", json={})
        assert resp.status_code == 422

    def test_invalid_status_returns_400(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        resp = client.put("/api/features/f1", json={"status": "not_a_real_status"})
        assert resp.status_code == 400

    def test_pct_above_100_fails_validation(self, client: TestClient):
        resp = client.put("/api/features/f1", json={"completion_pct": 150.0})
        # Pydantic field validation rejects at 422
        assert resp.status_code == 422

    def test_pct_below_0_fails_validation(self, client: TestClient):
        resp = client.put("/api/features/f1", json={"completion_pct": -5.0})
        assert resp.status_code == 422

    def test_status_only_preserves_existing_pct_for_new_record(self, client: TestClient):
        """Updating status on a non-existent feature creates with pct=0."""
        resp = client.put("/api/features/new-feat", json={"status": "in_progress"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["completion_pct"] == 0.0

    def test_update_sets_domain_and_project(self, client: TestClient):
        resp = client.put(
            "/api/features/f-new",
            json={
                "completion_pct": 20.0,
                "domain": "brandfocus-ai",
                "project": "voice-coach",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["domain"] == "brandfocus-ai"
        assert data["project"] == "voice-coach"


# ===========================================================================
# POST /api/features/{feature_id}/assign
# ===========================================================================


class TestAssignAgent:
    def test_assigns_agent_to_feature(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        resp = client.post(
            "/api/features/f1/assign",
            json={"agent_id": "forge:opencode"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["assigned_agent"] == "forge:opencode"

    def test_transitions_planned_to_in_progress(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1", status="planned")
        resp = client.post("/api/features/f1/assign", json={"agent_id": "forge:codex"})
        data = resp.json()["data"]
        assert data["status"] == "in_progress"

    def test_creates_record_if_not_exists(self, client: TestClient):
        resp = client.post(
            "/api/features/brand-new/assign",
            json={"agent_id": "forge:gemini"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_id"] == "brand-new"
        assert data["assigned_agent"] == "forge:gemini"

    def test_missing_agent_id_returns_422(self, client: TestClient):
        resp = client.post("/api/features/f1/assign", json={})
        assert resp.status_code == 422

    def test_reassign_overwrites_agent(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        client.post("/api/features/f1/assign", json={"agent_id": "forge:opencode"})
        resp = client.post("/api/features/f1/assign", json={"agent_id": "forge:codex"})
        data = resp.json()["data"]
        assert data["assigned_agent"] == "forge:codex"


# ===========================================================================
# POST /api/features/{feature_id}/link-task
# ===========================================================================


class TestLinkTask:
    def test_links_task_to_feature(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        resp = client.post(
            "/api/features/f1/link-task",
            json={"task_id": "task-abc123"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "task-abc123" in data["tasks"]

    def test_duplicate_task_not_added_twice(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        client.post("/api/features/f1/link-task", json={"task_id": "task-dup"})
        resp = client.post("/api/features/f1/link-task", json={"task_id": "task-dup"})
        data = resp.json()["data"]
        assert data["tasks"].count("task-dup") == 1

    def test_multiple_different_tasks(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        client.post("/api/features/f1/link-task", json={"task_id": "t1"})
        client.post("/api/features/f1/link-task", json={"task_id": "t2"})
        resp = client.post("/api/features/f1/link-task", json={"task_id": "t3"})
        data = resp.json()["data"]
        assert set(data["tasks"]) == {"t1", "t2", "t3"}

    def test_creates_record_if_not_exists(self, client: TestClient):
        resp = client.post(
            "/api/features/new-feat/link-task",
            json={"task_id": "task-xyz"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_id"] == "new-feat"
        assert "task-xyz" in data["tasks"]

    def test_missing_task_id_returns_422(self, client: TestClient):
        resp = client.post("/api/features/f1/link-task", json={})
        assert resp.status_code == 422

    def test_feature_updated_at_refreshed(self, client: TestClient, tracker: FeatureTracker):
        fp = _seed_feature(tracker, "f1")
        old_ts = fp.updated_at.isoformat()
        import time

        time.sleep(0.02)
        resp = client.post("/api/features/f1/link-task", json={"task_id": "t1"})
        data = resp.json()["data"]
        assert data["updated_at"] >= old_ts


# ===========================================================================
# Response envelope consistency
# ===========================================================================


class TestResponseEnvelope:
    """All successful responses must follow the canonical success envelope."""

    def _assert_success_envelope(self, resp):
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["error"] is None
        assert "timestamp" in body
        assert "data" in body

    def test_list_features_envelope(self, client: TestClient):
        self._assert_success_envelope(client.get("/api/features"))

    def test_get_stats_envelope(self, client: TestClient):
        self._assert_success_envelope(client.get("/api/features/stats"))

    def test_get_feature_envelope(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        self._assert_success_envelope(client.get("/api/features/f1"))

    def test_put_feature_envelope(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        self._assert_success_envelope(client.put("/api/features/f1", json={"completion_pct": 50.0}))

    def test_assign_agent_envelope(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        self._assert_success_envelope(
            client.post("/api/features/f1/assign", json={"agent_id": "forge:opencode"})
        )

    def test_link_task_envelope(self, client: TestClient, tracker: FeatureTracker):
        _seed_feature(tracker, "f1")
        self._assert_success_envelope(
            client.post("/api/features/f1/link-task", json={"task_id": "task-1"})
        )


# ===========================================================================
# Service layer mock tests (isolates API from real tracker)
# ===========================================================================


class TestAPIWithMockedTracker:
    """Test the API layer in isolation using a mocked FeatureTracker."""

    @pytest.fixture
    def mock_tracker(self):
        """Return a MagicMock configured as FeatureTracker."""
        return MagicMock(spec=FeatureTracker)

    @pytest.fixture
    def client_with_mock(self, app: FastAPI, mock_tracker: MagicMock) -> TestClient:
        """Client with the tracker replaced by a mock."""
        import forge_harness.webhook_server.api.features as _api_mod
        import forge_harness.webhook_server.services.feature_tracker as _mod

        original = _mod._tracker_instance
        _mod._tracker_instance = mock_tracker
        yield TestClient(app, raise_server_exceptions=True)
        _mod._tracker_instance = original

    def _make_fp(self, feature_id: str = "f1") -> FeatureProgress:
        return FeatureProgress(
            feature_id=feature_id,
            name="Test Feature",
            domain="d",
            project="p",
            status="planned",
            completion_pct=0.0,
        )

    def test_list_features_calls_load_features(
        self, client_with_mock: TestClient, mock_tracker: MagicMock
    ):
        mock_tracker.load_features.return_value = [self._make_fp("f1")]
        resp = client_with_mock.get("/api/features")
        assert resp.status_code == 200
        mock_tracker.load_features.assert_called_once()

    def test_list_features_passes_domain_filter(
        self, client_with_mock: TestClient, mock_tracker: MagicMock
    ):
        mock_tracker.load_features.return_value = []
        client_with_mock.get("/api/features?domain=codeswiftr-com")
        mock_tracker.load_features.assert_called_once_with(domain="codeswiftr-com", project=None)

    def test_get_feature_calls_get_feature(
        self, client_with_mock: TestClient, mock_tracker: MagicMock
    ):
        mock_tracker.get_feature.return_value = self._make_fp("f1")
        resp = client_with_mock.get("/api/features/f1")
        assert resp.status_code == 200
        mock_tracker.get_feature.assert_called_once_with("f1")

    def test_get_feature_404_when_none(self, client_with_mock: TestClient, mock_tracker: MagicMock):
        mock_tracker.get_feature.return_value = None
        resp = client_with_mock.get("/api/features/missing")
        assert resp.status_code == 404

    def test_get_stats_calls_get_stats(self, client_with_mock: TestClient, mock_tracker: MagicMock):
        mock_tracker.get_stats.return_value = {
            "total": 0,
            "by_status": {},
            "by_domain": {},
        }
        resp = client_with_mock.get("/api/features/stats")
        assert resp.status_code == 200
        mock_tracker.get_stats.assert_called_once()

    def test_assign_agent_calls_assign_agent(
        self, client_with_mock: TestClient, mock_tracker: MagicMock
    ):
        mock_tracker.assign_agent.return_value = self._make_fp("f1")
        resp = client_with_mock.post(
            "/api/features/f1/assign",
            json={"agent_id": "forge:opencode"},
        )
        assert resp.status_code == 200
        mock_tracker.assign_agent.assert_called_once_with(
            feature_id="f1", agent_id="forge:opencode"
        )

    def test_link_task_calls_link_task(self, client_with_mock: TestClient, mock_tracker: MagicMock):
        mock_tracker.link_task.return_value = self._make_fp("f1")
        resp = client_with_mock.post(
            "/api/features/f1/link-task",
            json={"task_id": "task-123"},
        )
        assert resp.status_code == 200
        mock_tracker.link_task.assert_called_once_with(feature_id="f1", task_id="task-123")
