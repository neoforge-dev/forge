"""Tests for the FORGE Dark Factory Phase 2 REST API endpoints.

Covers:
  - Intake Queue API     (/api/intake/*)
  - Task Decomposition   (/api/decomposition/*)
  - SLO Monitoring       (/api/slo/*)
  - Load Balancer        (/api/balancer/*)

Each test class uses a minimal FastAPI app that includes only the routers
under test.  Singletons are reset in setUp / tearDown so tests are isolated.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase

from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(*routers) -> FastAPI:
    """Create a minimal FastAPI app with the given routers included."""
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return app


# ===========================================================================
# Intake Queue API tests
# ===========================================================================


class TestIntakeAPI(TestCase):
    """Tests for POST/GET /api/intake endpoints."""

    def setUp(self):
        # Reset singleton and use a temporary queue file
        from forge_harness.webhook_server.services.intake_service import (
            reset_intake_service,
        )

        reset_intake_service()

        self._tmpdir = tempfile.TemporaryDirectory()
        self._queue_path = Path(self._tmpdir.name) / "queue.jsonl"

        # Pre-create singleton with isolated path
        from forge_harness.webhook_server.services.intake_service import (
            get_intake_service,
        )

        get_intake_service(queue_path=self._queue_path)

        from forge_harness.webhook_server.api.intake import router

        app = _build_app(router)
        self.client = TestClient(app)

    def tearDown(self):
        from forge_harness.webhook_server.services.intake_service import (
            reset_intake_service,
        )
        from forge_harness.webhook_server.services.lane_enforcer import (
            reset_lane_enforcer,
        )

        reset_intake_service()
        reset_lane_enforcer()
        self._tmpdir.cleanup()

    # -----------------------------------------------------------------------
    # GET /api/intake/stats
    # -----------------------------------------------------------------------

    def test_stats_returns_200(self):
        resp = self.client.get("/api/intake/stats")
        assert resp.status_code == 200

    def test_stats_has_required_keys(self):
        resp = self.client.get("/api/intake/stats")
        data = resp.json()["data"]
        assert "by_status" in data
        assert "by_source" in data
        assert "total" in data

    def test_stats_empty_queue(self):
        resp = self.client.get("/api/intake/stats")
        data = resp.json()["data"]
        assert data["total"] == 0

    # -----------------------------------------------------------------------
    # GET /api/intake (list)
    # -----------------------------------------------------------------------

    def test_list_returns_200(self):
        resp = self.client.get("/api/intake")
        assert resp.status_code == 200

    def test_list_returns_items_and_count(self):
        resp = self.client.get("/api/intake")
        data = resp.json()["data"]
        assert "items" in data
        assert "count" in data

    def test_list_empty_when_no_items(self):
        resp = self.client.get("/api/intake")
        assert resp.json()["data"]["count"] == 0

    def test_list_with_invalid_status_returns_422(self):
        resp = self.client.get("/api/intake?status=nonexistent")
        assert resp.status_code == 422

    def test_list_with_valid_status_filter(self):
        resp = self.client.get("/api/intake?status=pending")
        assert resp.status_code == 200

    def test_list_with_assigned_status_filter(self):
        resp = self.client.get("/api/intake?status=assigned")
        assert resp.status_code == 200

    def test_list_with_rejected_status_filter(self):
        resp = self.client.get("/api/intake?status=rejected")
        assert resp.status_code == 200

    # -----------------------------------------------------------------------
    # POST /api/intake (submit)
    # -----------------------------------------------------------------------

    def test_submit_valid_item_returns_201(self):
        payload = {
            "title": "Fix auth bug",
            "task_type": "bug-fix",
            "risk_tier": "low",
            "source": "api",
        }
        resp = self.client.post("/api/intake", json=payload)
        assert resp.status_code == 201

    def test_submit_returns_result_with_status(self):
        payload = {
            "title": "Add API endpoint",
            "task_type": "api-endpoint",
            "risk_tier": "low",
            "source": "api",
        }
        resp = self.client.post("/api/intake", json=payload)
        data = resp.json()["data"]
        assert "item_id" in data
        assert "status" in data

    def test_submit_with_all_fields(self):
        payload = {
            "title": "Write tests",
            "description": "Unit tests for auth module",
            "task_type": "test-writing",
            "risk_tier": "low",
            "priority": 4,
            "source": "cli",
            "metadata": {"ticket": "JIRA-123"},
        }
        resp = self.client.post("/api/intake", json=payload)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["item_id"] is not None

    def test_submit_invalid_task_type_returns_422(self):
        payload = {
            "title": "Bad task",
            "task_type": "not-a-valid-type",
            "risk_tier": "low",
            "source": "api",
        }
        resp = self.client.post("/api/intake", json=payload)
        assert resp.status_code == 422

    def test_submit_invalid_risk_tier_returns_422(self):
        payload = {
            "title": "Bad task",
            "task_type": "bug-fix",
            "risk_tier": "ultra-high",
            "source": "api",
        }
        resp = self.client.post("/api/intake", json=payload)
        assert resp.status_code == 422

    def test_submit_invalid_source_returns_422(self):
        payload = {
            "title": "Bad task",
            "task_type": "bug-fix",
            "risk_tier": "low",
            "source": "unknown-source",
        }
        resp = self.client.post("/api/intake", json=payload)
        assert resp.status_code == 422

    def test_submit_missing_title_returns_422(self):
        payload = {
            "task_type": "bug-fix",
            "risk_tier": "low",
            "source": "api",
        }
        resp = self.client.post("/api/intake", json=payload)
        assert resp.status_code == 422

    def test_submit_increases_total_count(self):
        self.client.post(
            "/api/intake",
            json={"title": "Task A", "task_type": "bug-fix", "risk_tier": "low", "source": "api"},
        )
        resp = self.client.get("/api/intake/stats")
        assert resp.json()["data"]["total"] == 1

    def test_submit_multiple_items(self):
        for i in range(3):
            self.client.post(
                "/api/intake",
                json={
                    "title": f"Task {i}",
                    "task_type": "bug-fix",
                    "risk_tier": "low",
                    "source": "api",
                },
            )
        resp = self.client.get("/api/intake/stats")
        assert resp.json()["data"]["total"] == 3

    # -----------------------------------------------------------------------
    # GET /api/intake/{item_id}
    # -----------------------------------------------------------------------

    def test_get_item_returns_200(self):
        submit = self.client.post(
            "/api/intake",
            json={"title": "Task X", "task_type": "bug-fix", "risk_tier": "low", "source": "api"},
        )
        item_id = submit.json()["data"]["item_id"]
        resp = self.client.get(f"/api/intake/{item_id}")
        assert resp.status_code == 200

    def test_get_item_returns_correct_title(self):
        submit = self.client.post(
            "/api/intake",
            json={
                "title": "My Important Task",
                "task_type": "bug-fix",
                "risk_tier": "low",
                "source": "api",
            },
        )
        item_id = submit.json()["data"]["item_id"]
        resp = self.client.get(f"/api/intake/{item_id}")
        assert resp.json()["data"]["title"] == "My Important Task"

    def test_get_nonexistent_item_returns_404(self):
        resp = self.client.get("/api/intake/nonexistent-item-id")
        assert resp.status_code == 404

    def test_get_item_has_current_status(self):
        submit = self.client.post(
            "/api/intake",
            json={
                "title": "Status Task",
                "task_type": "bug-fix",
                "risk_tier": "low",
                "source": "api",
            },
        )
        item_id = submit.json()["data"]["item_id"]
        resp = self.client.get(f"/api/intake/{item_id}")
        assert "current_status" in resp.json()["data"]


# ===========================================================================
# Task Decomposition API tests
# ===========================================================================


class TestDecompositionAPI(TestCase):
    """Tests for /api/decomposition/graphs endpoints."""

    def setUp(self):
        from forge_harness.webhook_server.services.task_decomposer import (
            reset_task_decomposer,
        )

        reset_task_decomposer()

        self._tmpdir = tempfile.TemporaryDirectory()
        storage_path = Path(self._tmpdir.name) / "graphs.jsonl"

        from forge_harness.webhook_server.services.task_decomposer import (
            get_task_decomposer,
        )

        get_task_decomposer(storage_path=storage_path)

        from forge_harness.webhook_server.api.decomposition import router

        app = _build_app(router)
        self.client = TestClient(app)

    def tearDown(self):
        from forge_harness.webhook_server.services.task_decomposer import (
            reset_task_decomposer,
        )

        reset_task_decomposer()
        self._tmpdir.cleanup()

    # -----------------------------------------------------------------------
    # POST /api/decomposition/graphs (create)
    # -----------------------------------------------------------------------

    def test_create_graph_returns_201(self):
        resp = self.client.post(
            "/api/decomposition/graphs",
            json={"parent_task_id": "TASK-001", "title": "Build auth module"},
        )
        assert resp.status_code == 201

    def test_create_graph_returns_parent_id(self):
        resp = self.client.post(
            "/api/decomposition/graphs",
            json={"parent_task_id": "TASK-100", "title": "My Parent Task"},
        )
        data = resp.json()["data"]
        assert data["parent_id"] == "TASK-100"
        assert data["parent_title"] == "My Parent Task"

    def test_create_graph_has_empty_nodes(self):
        resp = self.client.post(
            "/api/decomposition/graphs",
            json={"parent_task_id": "TASK-200", "title": "Empty Task"},
        )
        data = resp.json()["data"]
        assert data["nodes"] == {}

    def test_create_duplicate_graph_returns_409(self):
        payload = {"parent_task_id": "TASK-DUP", "title": "Duplicate Task"}
        self.client.post("/api/decomposition/graphs", json=payload)
        resp = self.client.post("/api/decomposition/graphs", json=payload)
        assert resp.status_code == 409

    def test_create_graph_missing_title_returns_422(self):
        resp = self.client.post(
            "/api/decomposition/graphs",
            json={"parent_task_id": "TASK-NOTITLE"},
        )
        assert resp.status_code == 422

    def test_create_graph_missing_parent_id_returns_422(self):
        resp = self.client.post(
            "/api/decomposition/graphs",
            json={"title": "No Parent"},
        )
        assert resp.status_code == 422

    # -----------------------------------------------------------------------
    # GET /api/decomposition/graphs (list)
    # -----------------------------------------------------------------------

    def test_list_graphs_returns_200(self):
        resp = self.client.get("/api/decomposition/graphs")
        assert resp.status_code == 200

    def test_list_graphs_returns_graphs_and_count(self):
        resp = self.client.get("/api/decomposition/graphs")
        data = resp.json()["data"]
        assert "graphs" in data
        assert "count" in data

    def test_list_graphs_empty_initially(self):
        resp = self.client.get("/api/decomposition/graphs")
        assert resp.json()["data"]["count"] == 0

    def test_list_graphs_shows_created_graphs(self):
        self.client.post(
            "/api/decomposition/graphs",
            json={"parent_task_id": "TASK-LIST-1", "title": "List Task 1"},
        )
        self.client.post(
            "/api/decomposition/graphs",
            json={"parent_task_id": "TASK-LIST-2", "title": "List Task 2"},
        )
        resp = self.client.get("/api/decomposition/graphs")
        assert resp.json()["data"]["count"] == 2

    # -----------------------------------------------------------------------
    # GET /api/decomposition/graphs/{graph_id}
    # -----------------------------------------------------------------------

    def test_get_graph_returns_200(self):
        self.client.post(
            "/api/decomposition/graphs",
            json={"parent_task_id": "TASK-GET", "title": "Get Test Task"},
        )
        resp = self.client.get("/api/decomposition/graphs/TASK-GET")
        assert resp.status_code == 200

    def test_get_graph_returns_correct_data(self):
        self.client.post(
            "/api/decomposition/graphs",
            json={"parent_task_id": "TASK-DATA", "title": "Data Task"},
        )
        resp = self.client.get("/api/decomposition/graphs/TASK-DATA")
        data = resp.json()["data"]
        assert data["parent_id"] == "TASK-DATA"
        assert data["parent_title"] == "Data Task"

    def test_get_nonexistent_graph_returns_404(self):
        resp = self.client.get("/api/decomposition/graphs/NONEXISTENT-TASK")
        assert resp.status_code == 404

    # -----------------------------------------------------------------------
    # POST /api/decomposition/graphs/{graph_id}/subtasks
    # -----------------------------------------------------------------------

    def _create_graph(self, task_id: str = "TASK-SUB", title: str = "Subtask Parent"):
        self.client.post(
            "/api/decomposition/graphs",
            json={"parent_task_id": task_id, "title": title},
        )

    def test_add_subtask_returns_201(self):
        self._create_graph()
        resp = self.client.post(
            "/api/decomposition/graphs/TASK-SUB/subtasks",
            json={"title": "Write unit tests", "task_type": "test-writing", "risk_tier": "low"},
        )
        assert resp.status_code == 201

    def test_add_subtask_returns_node_data(self):
        self._create_graph()
        resp = self.client.post(
            "/api/decomposition/graphs/TASK-SUB/subtasks",
            json={"title": "Implement login", "task_type": "api-endpoint", "risk_tier": "medium"},
        )
        data = resp.json()["data"]
        assert data["title"] == "Implement login"
        assert data["parent_id"] == "TASK-SUB"
        assert "subtask_id" in data
        assert data["status"] == "pending"

    def test_add_subtask_with_depends_on(self):
        self._create_graph("TASK-DEP", "Dependency Task")
        # Create first subtask
        first = self.client.post(
            "/api/decomposition/graphs/TASK-DEP/subtasks",
            json={"title": "First task", "task_type": "test-writing", "risk_tier": "low"},
        )
        first_id = first.json()["data"]["subtask_id"]

        # Create second subtask depending on first
        resp = self.client.post(
            "/api/decomposition/graphs/TASK-DEP/subtasks",
            json={
                "title": "Second task",
                "task_type": "api-endpoint",
                "risk_tier": "low",
                "depends_on": [first_id],
            },
        )
        assert resp.status_code == 201
        assert first_id in resp.json()["data"]["depends_on"]

    def test_add_subtask_to_nonexistent_graph_returns_404(self):
        resp = self.client.post(
            "/api/decomposition/graphs/TASK-MISSING/subtasks",
            json={"title": "Orphan task", "task_type": "bug-fix", "risk_tier": "low"},
        )
        assert resp.status_code == 404

    def test_add_subtask_invalid_task_type_returns_422(self):
        self._create_graph("TASK-BAD-TYPE", "Bad Type Task")
        resp = self.client.post(
            "/api/decomposition/graphs/TASK-BAD-TYPE/subtasks",
            json={"title": "Bad type", "task_type": "invalid-type", "risk_tier": "low"},
        )
        assert resp.status_code == 422

    def test_add_subtask_invalid_risk_tier_returns_422(self):
        self._create_graph("TASK-BAD-RISK", "Bad Risk Task")
        resp = self.client.post(
            "/api/decomposition/graphs/TASK-BAD-RISK/subtasks",
            json={"title": "Bad risk", "task_type": "bug-fix", "risk_tier": "ultra-high"},
        )
        assert resp.status_code == 422

    def test_add_subtask_unknown_dependency_returns_422(self):
        self._create_graph("TASK-UNK-DEP", "Unknown Dep Task")
        resp = self.client.post(
            "/api/decomposition/graphs/TASK-UNK-DEP/subtasks",
            json={
                "title": "Bad dep",
                "task_type": "bug-fix",
                "risk_tier": "low",
                "depends_on": ["sub-nonexistent"],
            },
        )
        assert resp.status_code == 422

    # -----------------------------------------------------------------------
    # PUT /api/decomposition/graphs/{graph_id}/subtasks/{subtask_id}/complete
    # -----------------------------------------------------------------------

    def test_complete_subtask_returns_200(self):
        self._create_graph("TASK-COMPLETE", "Complete Task")
        add = self.client.post(
            "/api/decomposition/graphs/TASK-COMPLETE/subtasks",
            json={"title": "Do work", "task_type": "bug-fix", "risk_tier": "low"},
        )
        subtask_id = add.json()["data"]["subtask_id"]
        resp = self.client.put(
            f"/api/decomposition/graphs/TASK-COMPLETE/subtasks/{subtask_id}/complete"
        )
        assert resp.status_code == 200

    def test_complete_subtask_updates_status(self):
        self._create_graph("TASK-STATUS", "Status Task")
        add = self.client.post(
            "/api/decomposition/graphs/TASK-STATUS/subtasks",
            json={"title": "Status work", "task_type": "bug-fix", "risk_tier": "low"},
        )
        subtask_id = add.json()["data"]["subtask_id"]
        self.client.put(f"/api/decomposition/graphs/TASK-STATUS/subtasks/{subtask_id}/complete")
        # Verify graph now shows subtask as completed
        graph_resp = self.client.get("/api/decomposition/graphs/TASK-STATUS")
        node = graph_resp.json()["data"]["nodes"][subtask_id]
        assert node["status"] == "completed"

    def test_complete_subtask_nonexistent_graph_returns_404(self):
        resp = self.client.put("/api/decomposition/graphs/TASK-NOPE/subtasks/sub-abc123/complete")
        assert resp.status_code == 404

    def test_complete_nonexistent_subtask_returns_404(self):
        self._create_graph("TASK-NOSUB", "No Sub Task")
        resp = self.client.put(
            "/api/decomposition/graphs/TASK-NOSUB/subtasks/sub-nonexistent/complete"
        )
        assert resp.status_code == 404

    def test_complete_already_completed_subtask_returns_422(self):
        self._create_graph("TASK-DOUBLE", "Double Complete")
        add = self.client.post(
            "/api/decomposition/graphs/TASK-DOUBLE/subtasks",
            json={"title": "One-shot work", "task_type": "bug-fix", "risk_tier": "low"},
        )
        subtask_id = add.json()["data"]["subtask_id"]
        self.client.put(f"/api/decomposition/graphs/TASK-DOUBLE/subtasks/{subtask_id}/complete")
        # Second completion should fail
        resp = self.client.put(
            f"/api/decomposition/graphs/TASK-DOUBLE/subtasks/{subtask_id}/complete"
        )
        assert resp.status_code == 422

    # -----------------------------------------------------------------------
    # GET /api/decomposition/graphs/{graph_id}/ready
    # -----------------------------------------------------------------------

    def test_get_ready_nodes_returns_200(self):
        self._create_graph("TASK-READY", "Ready Task")
        resp = self.client.get("/api/decomposition/graphs/TASK-READY/ready")
        assert resp.status_code == 200

    def test_get_ready_nodes_returns_nodes_and_count(self):
        self._create_graph("TASK-READY2", "Ready Task 2")
        resp = self.client.get("/api/decomposition/graphs/TASK-READY2/ready")
        data = resp.json()["data"]
        assert "nodes" in data
        assert "count" in data

    def test_get_ready_nodes_shows_pending_no_deps(self):
        self._create_graph("TASK-READY3", "Ready Task 3")
        self.client.post(
            "/api/decomposition/graphs/TASK-READY3/subtasks",
            json={"title": "No dep work", "task_type": "bug-fix", "risk_tier": "low"},
        )
        resp = self.client.get("/api/decomposition/graphs/TASK-READY3/ready")
        assert resp.json()["data"]["count"] == 1

    def test_get_ready_nodes_excludes_blocked_by_deps(self):
        self._create_graph("TASK-BLOCKED", "Blocked Task")
        first = self.client.post(
            "/api/decomposition/graphs/TASK-BLOCKED/subtasks",
            json={"title": "First", "task_type": "bug-fix", "risk_tier": "low"},
        )
        first_id = first.json()["data"]["subtask_id"]
        self.client.post(
            "/api/decomposition/graphs/TASK-BLOCKED/subtasks",
            json={
                "title": "Second",
                "task_type": "bug-fix",
                "risk_tier": "low",
                "depends_on": [first_id],
            },
        )
        # Only first should be ready
        resp = self.client.get("/api/decomposition/graphs/TASK-BLOCKED/ready")
        assert resp.json()["data"]["count"] == 1

    def test_get_ready_nodes_nonexistent_graph_returns_404(self):
        resp = self.client.get("/api/decomposition/graphs/TASK-NOPE/ready")
        assert resp.status_code == 404


# ===========================================================================
# SLO Monitoring API tests
# ===========================================================================


class TestSLOAPI(TestCase):
    """Tests for /api/slo/* endpoints."""

    def setUp(self):
        from forge_harness.webhook_server.services.slo_monitor import reset_slo_monitor

        reset_slo_monitor()

        from forge_harness.webhook_server.api.slo import router

        app = _build_app(router)
        self.client = TestClient(app)

    def tearDown(self):
        from forge_harness.webhook_server.services.slo_monitor import reset_slo_monitor

        reset_slo_monitor()

    # -----------------------------------------------------------------------
    # GET /api/slo (all SLOs)
    # -----------------------------------------------------------------------

    def test_check_all_slos_returns_200(self):
        resp = self.client.get("/api/slo")
        assert resp.status_code == 200

    def test_check_all_slos_returns_list(self):
        resp = self.client.get("/api/slo")
        data = resp.json()["data"]
        assert "slos" in data
        assert "count" in data
        assert isinstance(data["slos"], list)

    def test_check_all_slos_covers_all_lanes(self):
        from forge_harness.webhook_server.models.work_cell import WorkCellLane

        resp = self.client.get("/api/slo")
        count = resp.json()["data"]["count"]
        assert count == len(list(WorkCellLane))

    def test_check_all_slos_result_has_required_fields(self):
        resp = self.client.get("/api/slo")
        slo = resp.json()["data"]["slos"][0]
        assert "lane" in slo
        assert "status" in slo
        assert "requeue_count" in slo
        assert "checked_at" in slo

    def test_check_all_slos_initially_healthy(self):
        resp = self.client.get("/api/slo")
        for slo in resp.json()["data"]["slos"]:
            assert slo["status"] == "healthy"

    # -----------------------------------------------------------------------
    # GET /api/slo/{lane}
    # -----------------------------------------------------------------------

    def test_check_slo_for_lane_returns_200(self):
        resp = self.client.get("/api/slo/api_simple")
        assert resp.status_code == 200

    def test_check_slo_for_lane_returns_correct_lane(self):
        resp = self.client.get("/api/slo/api_simple")
        assert resp.json()["data"]["lane"] == "api_simple"

    def test_check_slo_all_valid_lanes(self):
        from forge_harness.webhook_server.models.work_cell import WorkCellLane

        for lane in WorkCellLane:
            resp = self.client.get(f"/api/slo/{lane.value}")
            assert resp.status_code == 200, f"Expected 200 for lane {lane.value}"

    def test_check_slo_invalid_lane_returns_404(self):
        resp = self.client.get("/api/slo/invalid_lane_xyz")
        assert resp.status_code == 404

    # -----------------------------------------------------------------------
    # GET /api/slo/breaches
    # -----------------------------------------------------------------------

    def test_get_breaches_returns_200(self):
        resp = self.client.get("/api/slo/breaches")
        assert resp.status_code == 200

    def test_get_breaches_returns_empty_initially(self):
        resp = self.client.get("/api/slo/breaches")
        data = resp.json()["data"]
        assert "breaches" in data
        assert data["count"] == 0

    def test_get_breaches_with_window_minutes(self):
        resp = self.client.get("/api/slo/breaches?window_minutes=30")
        assert resp.status_code == 200
        assert resp.json()["data"]["window_minutes"] == 30

    def test_get_breaches_invalid_window_returns_422(self):
        resp = self.client.get("/api/slo/breaches?window_minutes=0")
        assert resp.status_code == 422

    # -----------------------------------------------------------------------
    # POST /api/slo/tasks/{task_id}/start
    # -----------------------------------------------------------------------

    def test_record_task_start_returns_200(self):
        resp = self.client.post(
            "/api/slo/tasks/task-001/start",
            json={"lane": "api_simple"},
        )
        assert resp.status_code == 200

    def test_record_task_start_returns_confirmation(self):
        resp = self.client.post(
            "/api/slo/tasks/task-002/start",
            json={"lane": "docs"},
        )
        data = resp.json()["data"]
        assert data["task_id"] == "task-002"
        assert data["lane"] == "docs"
        assert data["recorded"] == "start"

    def test_record_task_start_invalid_lane_returns_404(self):
        resp = self.client.post(
            "/api/slo/tasks/task-003/start",
            json={"lane": "not_a_real_lane"},
        )
        assert resp.status_code == 404

    # -----------------------------------------------------------------------
    # POST /api/slo/tasks/{task_id}/complete
    # -----------------------------------------------------------------------

    def test_record_task_complete_after_start_returns_200(self):
        self.client.post("/api/slo/tasks/task-010/start", json={"lane": "api_simple"})
        resp = self.client.post(
            "/api/slo/tasks/task-010/complete",
            json={"passed": True},
        )
        assert resp.status_code == 200

    def test_record_task_complete_returns_lead_time(self):
        self.client.post("/api/slo/tasks/task-011/start", json={"lane": "api_simple"})
        resp = self.client.post(
            "/api/slo/tasks/task-011/complete",
            json={"passed": True},
        )
        data = resp.json()["data"]
        assert "lead_time_seconds" in data
        assert data["lead_time_seconds"] >= 0

    def test_record_task_complete_without_start_returns_404(self):
        resp = self.client.post(
            "/api/slo/tasks/task-never-started/complete",
            json={"passed": True},
        )
        assert resp.status_code == 404

    def test_record_task_complete_with_passed_false(self):
        self.client.post("/api/slo/tasks/task-fail/start", json={"lane": "test_writing"})
        resp = self.client.post(
            "/api/slo/tasks/task-fail/complete",
            json={"passed": False},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["passed"] is False

    def test_slo_check_after_complete_task(self):
        self.client.post("/api/slo/tasks/task-slo/start", json={"lane": "api_simple"})
        self.client.post("/api/slo/tasks/task-slo/complete", json={"passed": True})
        resp = self.client.get("/api/slo/api_simple")
        data = resp.json()["data"]
        assert data["current_pass_rate"] is not None


# ===========================================================================
# Load Balancer API tests
# ===========================================================================


class TestBalancerAPI(TestCase):
    """Tests for /api/balancer/* endpoints."""

    def setUp(self):
        from forge_harness.webhook_server.services.load_balancer import reset_load_balancer

        reset_load_balancer()

        from forge_harness.webhook_server.api.balancer import router

        app = _build_app(router)
        self.client = TestClient(app)

    def tearDown(self):
        from forge_harness.webhook_server.services.load_balancer import reset_load_balancer

        reset_load_balancer()

    # -----------------------------------------------------------------------
    # GET /api/balancer/agents (list)
    # -----------------------------------------------------------------------

    def test_list_agents_returns_200(self):
        resp = self.client.get("/api/balancer/agents")
        assert resp.status_code == 200

    def test_list_agents_returns_agents_and_count(self):
        resp = self.client.get("/api/balancer/agents")
        data = resp.json()["data"]
        assert "agents" in data
        assert "count" in data

    def test_list_agents_empty_initially(self):
        resp = self.client.get("/api/balancer/agents")
        assert resp.json()["data"]["count"] == 0

    # -----------------------------------------------------------------------
    # POST /api/balancer/agents (register)
    # -----------------------------------------------------------------------

    def test_register_agent_returns_201(self):
        resp = self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:nova", "capabilities": ["api_simple", "docs"]},
        )
        assert resp.status_code == 201

    def test_register_agent_returns_capability(self):
        resp = self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:kimi", "capabilities": ["research"]},
        )
        data = resp.json()["data"]
        assert data["agent_id"] == "forge:kimi"
        assert "supported_lanes" in data
        assert "max_concurrent" in data

    def test_register_agent_with_custom_max_concurrent(self):
        resp = self.client.post(
            "/api/balancer/agents",
            json={
                "agent_id": "forge:prya",
                "capabilities": ["api_simple", "api_stateful"],
                "max_concurrent": 5,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["max_concurrent"] == 5

    def test_register_agent_invalid_lane_returns_422(self):
        resp = self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:bad", "capabilities": ["not_a_real_lane"]},
        )
        assert resp.status_code == 422

    def test_register_agent_empty_capabilities(self):
        resp = self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:idle", "capabilities": []},
        )
        assert resp.status_code == 201

    def test_register_agent_increments_count(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:agent1", "capabilities": ["docs"]},
        )
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:agent2", "capabilities": ["docs"]},
        )
        resp = self.client.get("/api/balancer/agents")
        assert resp.json()["data"]["count"] == 2

    # -----------------------------------------------------------------------
    # GET /api/balancer/agents/{agent_id}
    # -----------------------------------------------------------------------

    def test_get_agent_stats_returns_200(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:target", "capabilities": ["api_simple"]},
        )
        resp = self.client.get("/api/balancer/agents/forge:target")
        assert resp.status_code == 200

    def test_get_agent_stats_returns_correct_id(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:check", "capabilities": ["docs"]},
        )
        resp = self.client.get("/api/balancer/agents/forge:check")
        assert resp.json()["data"]["agent_id"] == "forge:check"

    def test_get_agent_stats_nonexistent_returns_404(self):
        resp = self.client.get("/api/balancer/agents/forge:nonexistent")
        assert resp.status_code == 404

    def test_get_agent_stats_has_required_fields(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:fields", "capabilities": ["api_simple"]},
        )
        resp = self.client.get("/api/balancer/agents/forge:fields")
        data = resp.json()["data"]
        assert "agent_id" in data
        assert "supported_lanes" in data
        assert "max_concurrent" in data
        assert "current_active" in data
        assert "context_budget_remaining_pct" in data
        assert "last_heartbeat_age_seconds" in data

    # -----------------------------------------------------------------------
    # POST /api/balancer/recommend
    # -----------------------------------------------------------------------

    def test_recommend_with_no_agents_returns_empty(self):
        resp = self.client.post(
            "/api/balancer/recommend",
            json={"task_type": "bug-fix", "risk_tier": "low"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["count"] == 0
        assert data["recommendations"] == []

    def test_recommend_returns_lane_info(self):
        resp = self.client.post(
            "/api/balancer/recommend",
            json={"task_type": "bug-fix", "risk_tier": "low"},
        )
        data = resp.json()["data"]
        assert "lane" in data
        assert "strategy" in data

    def test_recommend_with_eligible_agent(self):
        # Register agent that supports api_simple (bug-fix + low routes here)
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:worker", "capabilities": ["api_simple"]},
        )
        resp = self.client.post(
            "/api/balancer/recommend",
            json={"task_type": "bug-fix", "risk_tier": "low"},
        )
        data = resp.json()["data"]
        assert data["count"] == 1
        assert data["recommendations"][0]["agent_id"] == "forge:worker"

    def test_recommend_invalid_task_type_returns_422(self):
        resp = self.client.post(
            "/api/balancer/recommend",
            json={"task_type": "not-valid", "risk_tier": "low"},
        )
        assert resp.status_code == 422

    def test_recommend_invalid_risk_tier_returns_422(self):
        resp = self.client.post(
            "/api/balancer/recommend",
            json={"task_type": "bug-fix", "risk_tier": "super-high"},
        )
        assert resp.status_code == 422

    def test_recommend_invalid_strategy_returns_422(self):
        resp = self.client.post(
            "/api/balancer/recommend",
            json={"task_type": "bug-fix", "risk_tier": "low", "strategy": "random_pick"},
        )
        assert resp.status_code == 422

    def test_recommend_round_robin_strategy(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:rr1", "capabilities": ["api_simple"]},
        )
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:rr2", "capabilities": ["api_simple"]},
        )
        resp = self.client.post(
            "/api/balancer/recommend",
            json={"task_type": "bug-fix", "risk_tier": "low", "strategy": "round_robin"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 2

    def test_recommend_result_has_score(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:scored", "capabilities": ["api_simple"]},
        )
        resp = self.client.post(
            "/api/balancer/recommend",
            json={"task_type": "bug-fix", "risk_tier": "low"},
        )
        rec = resp.json()["data"]["recommendations"][0]
        assert "score" in rec
        assert 0.0 <= rec["score"] <= 1.0

    # -----------------------------------------------------------------------
    # PUT /api/balancer/agents/{agent_id}/load
    # -----------------------------------------------------------------------

    def test_update_agent_load_returns_200(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:updatable", "capabilities": ["docs"]},
        )
        resp = self.client.put(
            "/api/balancer/agents/forge:updatable/load",
            json={"current_active": 2, "context_budget_pct": 75.0},
        )
        assert resp.status_code == 200

    def test_update_agent_load_reflects_changes(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:loaded", "capabilities": ["docs"]},
        )
        self.client.put(
            "/api/balancer/agents/forge:loaded/load",
            json={"current_active": 1, "context_budget_pct": 50.0},
        )
        resp = self.client.get("/api/balancer/agents/forge:loaded")
        data = resp.json()["data"]
        assert data["current_active"] == 1
        assert data["context_budget_remaining_pct"] == 50.0

    def test_update_agent_load_nonexistent_returns_404(self):
        resp = self.client.put(
            "/api/balancer/agents/forge:ghost/load",
            json={"current_active": 0, "context_budget_pct": 100.0},
        )
        assert resp.status_code == 404

    def test_update_agent_load_negative_active_returns_422(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:neg", "capabilities": ["docs"]},
        )
        resp = self.client.put(
            "/api/balancer/agents/forge:neg/load",
            json={"current_active": -1, "context_budget_pct": 100.0},
        )
        assert resp.status_code == 422

    def test_update_agent_load_budget_over_100_returns_422(self):
        self.client.post(
            "/api/balancer/agents",
            json={"agent_id": "forge:over", "capabilities": ["docs"]},
        )
        resp = self.client.put(
            "/api/balancer/agents/forge:over/load",
            json={"current_active": 0, "context_budget_pct": 150.0},
        )
        assert resp.status_code == 422
