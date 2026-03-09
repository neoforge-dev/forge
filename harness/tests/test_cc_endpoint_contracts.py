"""
Command Center API Endpoint Contract Tests (CP-P4-1)

Verifies response contracts (field names, types, structure) for 8 stable
API endpoints. Uses FastAPI TestClient with auth disabled so tests are
hermetic — no live server or Redis required.

Endpoints covered:
  GET  /health                    - Basic health check
  GET  /api/health                - Health check (alias)
  GET  /api/dashboard             - Dashboard summary
  GET  /api/dashboard/agents      - Agent list (dashboard service)
  GET  /api/dashboard/throughput  - Task throughput metrics
  GET  /api/agents                - Agent list (agent registry)
  GET  /api/quality               - Quality metrics
  POST /api/nodes/heartbeat       - Publish node heartbeat
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_or_none(value: Any) -> bool:
    """Return True if value is None or a parseable ISO-8601 string."""
    if value is None:
        return True
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def _assert_standard_envelope(data: dict, *, expect_success: bool = True) -> None:
    """Assert that a response matches the standard {success, data, error, timestamp} envelope."""
    assert "success" in data, "Response missing 'success' field"
    assert isinstance(data["success"], bool), "'success' must be a bool"
    assert "timestamp" in data, "Response missing 'timestamp' field"
    assert _iso_or_none(data["timestamp"]), "'timestamp' must be ISO-8601"

    if expect_success:
        assert data["success"] is True, f"Expected success=True, got: {data}"
        assert "data" in data, "Success response missing 'data' field"
        assert data.get("error") is None, "Success response should have error=None"
    else:
        assert data["success"] is False, f"Expected success=False, got: {data}"
        assert "error" in data, "Error response missing 'error' field"
        assert isinstance(data["error"], dict), "'error' must be a dict"
        assert "code" in data["error"], "Error dict missing 'code'"
        assert "message" in data["error"], "Error dict missing 'message'"


# ---------------------------------------------------------------------------
# App fixture — creates the FastAPI app with auth disabled
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(monkeypatch):
    """Create the FORGE webhook server FastAPI application with auth and rate limiting disabled.

    Auth is disabled by setting ``require_auth=False`` on AuthConfig.
    Rate limiting is disabled via the ``FORGE_RATE_LIMIT_ENABLED=false`` env var,
    which affects both the global limiter passed to ``create_app`` AND the per-path
    ``WebhookRateLimitMiddleware`` which reads env vars when creating per-path buckets.

    The fixture is function-scoped so each test gets a fresh app instance (and fresh
    rate-limiter state), preventing burst exhaustion when many tests hit the same
    IP address (``testclient``).
    """
    try:
        from forge_harness.webhook_server import AuthConfig, create_app
        from forge_harness.webhook_server.infrastructure.rate_limiter import RateLimitConfig
    except ImportError:
        pytest.skip("forge_harness not importable in this environment")

    # Disable rate limiting globally so tests are not blocked by burst limits.
    monkeypatch.setenv("FORGE_RATE_LIMIT_ENABLED", "false")

    auth_cfg = AuthConfig(require_auth=False, allow_localhost=True)
    rate_cfg = RateLimitConfig(enabled=False)
    application = create_app(auth_config=auth_cfg, rate_limit_config=rate_cfg)
    if application is None:
        pytest.skip("create_app returned None — FastAPI not available")
    return application


@pytest.fixture()
def client(app):
    """Synchronous TestClient wrapping the FastAPI app."""
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi.testclient not available")

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# DashboardService isolation — use a fresh in-memory DB for each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_dashboard_service(tmp_path):
    """Reset the DashboardService singleton before/after each test so state
    created by one test does not leak into the next."""
    try:
        import forge_harness.webhook_server.services.dashboard_service as _ds_mod
    except ImportError:
        yield
        return

    _ds_mod._dashboard_service = None
    # Pre-create a fresh service pointing at tmp_path so the singleton is
    # initialised with an empty SQLite database.
    from forge_harness.webhook_server.services.dashboard_service import DashboardService

    _ds_mod._dashboard_service = DashboardService(
        metrics_db_path=tmp_path / "contract_test.db",
        snapshots_path=tmp_path / "contract_snapshots.json",
    )
    yield
    _ds_mod._dashboard_service = None


# ===========================================================================
# 1. GET /health
# ===========================================================================


class TestHealthEndpointContract:
    """Contract tests for GET /health."""

    def test_returns_200(self, client):
        """Health endpoint must return HTTP 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_required_fields_present(self, client):
        """Response must contain 'status', 'service', and 'timestamp'."""
        data = client.get("/health").json()
        assert "status" in data, "Missing 'status' field"
        assert "service" in data, "Missing 'service' field"
        assert "timestamp" in data, "Missing 'timestamp' field"

    def test_status_is_string(self, client):
        """'status' must be a non-empty string."""
        data = client.get("/health").json()
        assert isinstance(data["status"], str), "'status' must be str"
        assert data["status"], "'status' must not be empty"

    def test_service_is_string(self, client):
        """'service' must be a non-empty string."""
        data = client.get("/health").json()
        assert isinstance(data["service"], str), "'service' must be str"
        assert data["service"], "'service' must not be empty"

    def test_timestamp_is_iso8601(self, client):
        """'timestamp' must be a valid ISO-8601 datetime string."""
        data = client.get("/health").json()
        assert _iso_or_none(data["timestamp"]), (
            f"'timestamp' must be ISO-8601, got: {data['timestamp']!r}"
        )

    def test_optional_fields_correct_types(self, client):
        """Optional fields, when present, must have the correct types."""
        data = client.get("/health").json()

        # redis_status: str or absent
        if "redis_status" in data:
            assert isinstance(data["redis_status"], (str, type(None)))

        # store_type: str or absent
        if "store_type" in data:
            assert isinstance(data["store_type"], (str, type(None)))

        # synchronizer_status: str or absent
        if "synchronizer_status" in data:
            assert isinstance(data["synchronizer_status"], (str, type(None)))

        # synchronizer_sync_count: int or absent
        if "synchronizer_sync_count" in data and data["synchronizer_sync_count"] is not None:
            assert isinstance(data["synchronizer_sync_count"], int)

        # synchronizer_errors: int or absent
        if "synchronizer_errors" in data and data["synchronizer_errors"] is not None:
            assert isinstance(data["synchronizer_errors"], int)


# ===========================================================================
# 2. GET /api/health
# ===========================================================================


class TestApiHealthEndpointContract:
    """Contract tests for GET /api/health (alias of /health)."""

    def test_returns_200(self, client):
        """GET /api/health must return HTTP 200."""
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_matches_health_endpoint(self, client):
        """GET /api/health must return the same shape as GET /health."""
        health_data = client.get("/health").json()
        api_health_data = client.get("/api/health").json()

        # Both must have the same keys (values may differ due to timing)
        assert set(health_data.keys()) == set(api_health_data.keys()), (
            "/api/health keys differ from /health keys"
        )

    def test_required_fields_present(self, client):
        """Required fields must be present."""
        data = client.get("/api/health").json()
        for field in ("status", "service", "timestamp"):
            assert field in data, f"Missing required field: {field!r}"

    def test_status_valid_value(self, client):
        """'status' must be one of the known values."""
        data = client.get("/api/health").json()
        valid_statuses = {"ok", "healthy", "degraded", "error", "unhealthy"}
        assert data["status"] in valid_statuses, (
            f"'status' must be one of {valid_statuses}, got: {data['status']!r}"
        )


# ===========================================================================
# 3. GET /api/dashboard
# ===========================================================================


class TestDashboardSummaryEndpointContract:
    """Contract tests for GET /api/dashboard."""

    def test_returns_200(self, client):
        """GET /api/dashboard must return HTTP 200."""
        response = client.get("/api/dashboard")
        assert response.status_code == 200

    def test_top_level_fields_present(self, client):
        """All required top-level fields must be present."""
        data = client.get("/api/dashboard").json()
        required_fields = [
            "total_agents",
            "active_agents_count",
            "idle_agents_count",
            "error_agents_count",
            "paused_agents_count",
            "agents",
            "task_throughput",
            "system_status",
            "last_updated",
        ]
        for field in required_fields:
            assert field in data, f"Dashboard response missing required field: {field!r}"

    def test_agent_counts_are_integers(self, client):
        """Agent count fields must all be non-negative integers."""
        data = client.get("/api/dashboard").json()
        count_fields = [
            "total_agents",
            "active_agents_count",
            "idle_agents_count",
            "error_agents_count",
            "paused_agents_count",
        ]
        for field in count_fields:
            assert isinstance(data[field], int), f"'{field}' must be int, got {type(data[field])}"
            assert data[field] >= 0, f"'{field}' must be >= 0, got {data[field]}"

    def test_agents_is_list(self, client):
        """'agents' must be a list."""
        data = client.get("/api/dashboard").json()
        assert isinstance(data["agents"], list), "'agents' must be a list"

    def test_task_throughput_contract(self, client):
        """'task_throughput' must contain all required sub-fields."""
        data = client.get("/api/dashboard").json()
        tp = data["task_throughput"]
        assert isinstance(tp, dict), "'task_throughput' must be a dict"

        required_tp_fields = {
            "total_today": int,
            "completed_today": int,
            "failed_today": int,
            "in_progress_today": int,
            "avg_duration_seconds": (int, float),
            "success_rate": (int, float),
            "by_type": dict,
        }
        for field, expected_type in required_tp_fields.items():
            assert field in tp, f"'task_throughput' missing field: {field!r}"
            assert isinstance(tp[field], expected_type), (
                f"'task_throughput.{field}' must be {expected_type}, got {type(tp[field])}"
            )

    def test_task_throughput_values_in_range(self, client):
        """success_rate must be between 0.0 and 1.0."""
        data = client.get("/api/dashboard").json()
        tp = data["task_throughput"]
        assert 0.0 <= tp["success_rate"] <= 1.0, (
            f"success_rate must be in [0, 1], got {tp['success_rate']}"
        )

    def test_system_status_is_string(self, client):
        """'system_status' must be a non-empty string."""
        data = client.get("/api/dashboard").json()
        assert isinstance(data["system_status"], str), "'system_status' must be str"
        assert data["system_status"], "'system_status' must not be empty"

    def test_last_updated_is_iso8601(self, client):
        """'last_updated' must be a valid ISO-8601 datetime."""
        data = client.get("/api/dashboard").json()
        assert _iso_or_none(data["last_updated"]), (
            f"'last_updated' must be ISO-8601, got: {data['last_updated']!r}"
        )

    def test_total_agents_consistent_with_counts(self, client):
        """total_agents must equal sum of status-specific counts."""
        data = client.get("/api/dashboard").json()
        computed = (
            data["active_agents_count"]
            + data["idle_agents_count"]
            + data["error_agents_count"]
            + data["paused_agents_count"]
        )
        assert data["total_agents"] == computed, (
            f"total_agents ({data['total_agents']}) != sum of counts ({computed})"
        )

    def test_agent_item_contract_when_present(self, client):
        """Each item in 'agents' must satisfy the AgentMetricsResponse contract."""
        # Register an agent first so the list is non-empty
        import forge_harness.webhook_server.services.dashboard_service as _ds_mod

        svc = _ds_mod._dashboard_service
        svc.register_agent(
            agent_id="contract-agent-1",
            role="backend-engineer",
            project="test-project",
            domain="test-domain",
        )

        data = client.get("/api/dashboard").json()
        agents = data["agents"]
        assert len(agents) >= 1, "Expected at least one agent after registration"

        agent = agents[0]
        required_agent_fields = {
            "id": str,
            "role": str,
            "status": str,
            "progress": int,
            "is_stale": bool,
            "token_usage": dict,
            "messages_count": int,
            "focus_tags": list,
        }
        for field, expected_type in required_agent_fields.items():
            assert field in agent, f"Agent item missing field: {field!r}"
            assert isinstance(agent[field], expected_type), (
                f"Agent.{field} must be {expected_type}, got {type(agent[field])}"
            )

        # Optional nullable fields
        nullable_fields = ("project", "task", "last_activity", "domain")
        for field in nullable_fields:
            assert field in agent, f"Agent item missing optional field: {field!r}"
            assert isinstance(agent[field], (str, type(None))), (
                f"Agent.{field} must be str or None"
            )

        # Range checks
        assert 0 <= agent["progress"] <= 100, f"Agent.progress out of range: {agent['progress']}"
        assert agent["messages_count"] >= 0
        assert _iso_or_none(agent["last_activity"])


# ===========================================================================
# 4. GET /api/dashboard/agents
# ===========================================================================


class TestDashboardAgentsEndpointContract:
    """Contract tests for GET /api/dashboard/agents."""

    def test_returns_200(self, client):
        """GET /api/dashboard/agents must return HTTP 200."""
        response = client.get("/api/dashboard/agents")
        assert response.status_code == 200

    def test_response_is_list(self, client):
        """Response body must be a JSON array."""
        data = client.get("/api/dashboard/agents").json()
        assert isinstance(data, list), "Response must be a list"

    def test_empty_list_when_no_agents(self, client):
        """Empty list is a valid response when no agents are registered."""
        data = client.get("/api/dashboard/agents").json()
        assert isinstance(data, list)
        # When freshly initialised (no agents), we expect an empty list
        assert data == [], f"Expected [], got: {data}"

    def test_agent_item_fields_when_populated(self, client):
        """Each agent item must conform to the AgentMetricsResponse contract."""
        import forge_harness.webhook_server.services.dashboard_service as _ds_mod

        svc = _ds_mod._dashboard_service
        svc.register_agent(
            agent_id="contract-da-1",
            role="frontend-builder",
            project="test-proj",
            domain="test-dom",
        )

        data = client.get("/api/dashboard/agents").json()
        assert isinstance(data, list)
        assert len(data) >= 1

        agent = data[0]
        required_fields = {
            "id": str,
            "role": str,
            "status": str,
            "progress": int,
            "is_stale": bool,
            "token_usage": dict,
            "messages_count": int,
            "focus_tags": list,
        }
        for field, expected_type in required_fields.items():
            assert field in agent, f"Agent missing field: {field!r}"
            assert isinstance(agent[field], expected_type), (
                f"agent.{field} wrong type: expected {expected_type}, got {type(agent[field])}"
            )

    def test_status_filter_returns_subset(self, client):
        """?status_filter=idle should return only idle agents."""
        import forge_harness.webhook_server.services.dashboard_service as _ds_mod

        svc = _ds_mod._dashboard_service
        svc.register_agent(agent_id="da-idle-1", role="qa", project="p")
        svc.update_agent("da-idle-1", status="idle")
        svc.register_agent(agent_id="da-active-1", role="eng", project="p")
        svc.update_agent("da-active-1", status="active")

        data = client.get("/api/dashboard/agents?status_filter=idle").json()
        assert isinstance(data, list)
        for agent in data:
            assert agent["status"] == "idle", (
                f"status_filter=idle returned agent with status={agent['status']!r}"
            )

    def test_include_stale_param_accepted(self, client):
        """?include_stale=true must be accepted without error."""
        response = client.get("/api/dashboard/agents?include_stale=true")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


# ===========================================================================
# 5. GET /api/dashboard/throughput
# ===========================================================================


class TestDashboardThroughputEndpointContract:
    """Contract tests for GET /api/dashboard/throughput."""

    def test_returns_200(self, client):
        """GET /api/dashboard/throughput must return HTTP 200."""
        response = client.get("/api/dashboard/throughput")
        assert response.status_code == 200

    def test_required_fields_present(self, client):
        """All throughput fields must be present."""
        data = client.get("/api/dashboard/throughput").json()
        required_fields = [
            "total_today",
            "completed_today",
            "failed_today",
            "in_progress_today",
            "avg_duration_seconds",
            "success_rate",
            "by_type",
        ]
        for field in required_fields:
            assert field in data, f"Throughput response missing field: {field!r}"

    def test_integer_count_fields(self, client):
        """Count fields must be non-negative integers."""
        data = client.get("/api/dashboard/throughput").json()
        int_fields = ["total_today", "completed_today", "failed_today", "in_progress_today"]
        for field in int_fields:
            assert isinstance(data[field], int), f"'{field}' must be int"
            assert data[field] >= 0, f"'{field}' must be >= 0"

    def test_float_metric_fields(self, client):
        """Float metric fields must be numeric and in valid ranges."""
        data = client.get("/api/dashboard/throughput").json()
        assert isinstance(data["avg_duration_seconds"], (int, float))
        assert data["avg_duration_seconds"] >= 0.0

        assert isinstance(data["success_rate"], (int, float))
        assert 0.0 <= data["success_rate"] <= 1.0, (
            f"success_rate must be in [0, 1], got {data['success_rate']}"
        )

    def test_by_type_is_dict(self, client):
        """'by_type' must be a dict mapping task type names to integer counts."""
        data = client.get("/api/dashboard/throughput").json()
        assert isinstance(data["by_type"], dict), "'by_type' must be a dict"
        for key, val in data["by_type"].items():
            assert isinstance(key, str), f"by_type key must be str, got {type(key)}"
            assert isinstance(val, int), f"by_type value must be int, got {type(val)}"

    def test_hours_param_accepted(self, client):
        """?hours= query parameter must be accepted and not cause an error."""
        for hours in (1, 24, 168):
            response = client.get(f"/api/dashboard/throughput?hours={hours}")
            assert response.status_code == 200, (
                f"Expected 200 for hours={hours}, got {response.status_code}"
            )

    def test_hours_out_of_range_returns_422(self, client):
        """hours outside the valid range [1, 720] must return HTTP 422."""
        for bad_hours in (0, 721, -1):
            response = client.get(f"/api/dashboard/throughput?hours={bad_hours}")
            assert response.status_code == 422, (
                f"Expected 422 for hours={bad_hours}, got {response.status_code}"
            )


# ===========================================================================
# 6. GET /api/agents
# ===========================================================================


class TestAgentsEndpointContract:
    """Contract tests for GET /api/agents."""

    def test_returns_200(self, client):
        """GET /api/agents must return HTTP 200."""
        response = client.get("/api/agents")
        assert response.status_code == 200

    def test_response_is_standard_envelope(self, client):
        """GET /api/agents must return the standard {success, data, ...} envelope."""
        data = client.get("/api/agents").json()
        _assert_standard_envelope(data, expect_success=True)

    def test_data_contains_agents_and_total(self, client):
        """data must contain an 'agents' list and a 'total' integer."""
        data = client.get("/api/agents").json()
        payload = data["data"]
        assert "agents" in payload, "data missing 'agents' key"
        assert "total" in payload, "data missing 'total' key"
        assert isinstance(payload["agents"], list), "'agents' must be a list"
        assert isinstance(payload["total"], int), "'total' must be an int"
        assert payload["total"] >= 0

    def test_total_consistent_with_agents_length(self, client):
        """'total' must match len(agents)."""
        data = client.get("/api/agents").json()
        payload = data["data"]
        assert payload["total"] == len(payload["agents"]), (
            f"total ({payload['total']}) != len(agents) ({len(payload['agents'])})"
        )

    def test_agent_item_contract_fields(self, client):
        """Each agent item must satisfy the normalize_agent_dict contract."""
        # Register an agent via the dashboard service so the registry has content
        import forge_harness.webhook_server.services.dashboard_service as _ds_mod

        svc = _ds_mod._dashboard_service
        svc.register_agent(
            agent_id="agent-api-test-1",
            role="test-engineer",
            project="contract-project",
            domain="test-domain",
        )

        data = client.get("/api/agents").json()
        agents = data["data"]["agents"]

        if len(agents) == 0:
            pytest.skip("No agents available from the registry — skipping field contract check")

        agent = agents[0]

        # Fields defined by normalize_agent_dict in agents.py
        required_fields = {
            "session_id": str,
            "agent_role": str,
            "status": str,
            "progress_pct": (int, float),
            "tasks_completed": int,
            "tasks_remaining": int,
            "token_usage": (int, float),
            "api_calls": int,
            "focus_tags": list,
            "children": list,
        }
        for field, expected_type in required_fields.items():
            assert field in agent, f"Agent item missing field: {field!r}"
            assert isinstance(agent[field], expected_type), (
                f"agent.{field} wrong type: expected {expected_type}, got {type(agent[field])}"
            )

        # Nullable string fields
        nullable_str_fields = ("agent_name", "domain", "project", "current_task",
                               "started_at", "last_activity", "pending_approval_id")
        for field in nullable_str_fields:
            assert field in agent, f"Agent item missing nullable field: {field!r}"
            assert isinstance(agent[field], (str, type(None))), (
                f"agent.{field} must be str or None"
            )

        # Range checks
        assert 0.0 <= agent["progress_pct"] <= 100.0
        assert agent["tasks_completed"] >= 0
        assert agent["tasks_remaining"] >= 0
        assert agent["token_usage"] >= 0
        assert agent["api_calls"] >= 0

    def test_session_id_never_empty(self, client):
        """session_id must never be an empty string for any agent."""
        data = client.get("/api/agents").json()
        for agent in data["data"]["agents"]:
            assert agent.get("session_id"), f"session_id is empty for agent: {agent}"


# ===========================================================================
# 7. GET /api/quality
# ===========================================================================


class TestQualityEndpointContract:
    """Contract tests for GET /api/quality."""

    def test_returns_200(self, client):
        """GET /api/quality must return HTTP 200."""
        response = client.get("/api/quality")
        assert response.status_code == 200

    def test_response_is_standard_envelope(self, client):
        """GET /api/quality must return the standard {success, data, ...} envelope."""
        data = client.get("/api/quality").json()
        _assert_standard_envelope(data, expect_success=True)

    def test_data_contains_projects_and_summary(self, client):
        """data must contain 'projects' list and 'summary' dict."""
        data = client.get("/api/quality").json()
        payload = data["data"]
        assert "projects" in payload, "data missing 'projects'"
        assert "summary" in payload, "data missing 'summary'"
        assert isinstance(payload["projects"], list), "'projects' must be a list"
        assert isinstance(payload["summary"], dict), "'summary' must be a dict"

    def test_summary_fields_present_and_typed(self, client):
        """'summary' must contain required aggregate fields with correct types."""
        summary = client.get("/api/quality").json()["data"]["summary"]
        required = {
            "total_projects": int,
            "total_tests": int,
            "total_ruff_errors": int,
            "total_mypy_errors": int,
            "projects_at_zero_lint": int,
        }
        for field, expected_type in required.items():
            assert field in summary, f"summary missing field: {field!r}"
            assert isinstance(summary[field], expected_type), (
                f"summary.{field} must be {expected_type}, got {type(summary[field])}"
            )

        # average_coverage is int/float or None
        assert "average_coverage" in summary
        if summary["average_coverage"] is not None:
            assert isinstance(summary["average_coverage"], (int, float))

    def test_project_item_contract(self, client):
        """Each project entry must have the expected fields and types."""
        data = client.get("/api/quality").json()
        projects = data["data"]["projects"]
        assert len(projects) > 0, "Quality endpoint returned zero projects"

        # Validate a sample of entries (all if reasonable)
        for project in projects[:5]:
            assert "project" in project, "project item missing 'project' field"
            assert "domain" in project, "project item missing 'domain' field"
            assert isinstance(project["project"], str)
            assert isinstance(project["domain"], str)

            # tests, coverage, ruff_errors, mypy_errors may be None
            for nullable_int_field in ("tests", "coverage", "ruff_errors", "mypy_errors"):
                assert nullable_int_field in project, (
                    f"project item missing '{nullable_int_field}'"
                )
                val = project[nullable_int_field]
                if val is not None:
                    assert isinstance(val, (int, float)), (
                        f"project.{nullable_int_field} must be numeric or None"
                    )

            # source field
            assert "source" in project
            assert project["source"] in ("scorecard", "cache"), (
                f"project.source must be 'scorecard' or 'cache', got {project['source']!r}"
            )

    def test_summary_counts_are_non_negative(self, client):
        """All summary count fields must be >= 0."""
        summary = client.get("/api/quality").json()["data"]["summary"]
        for field in ("total_projects", "total_tests", "total_ruff_errors",
                      "total_mypy_errors", "projects_at_zero_lint"):
            assert summary[field] >= 0, f"summary.{field} must be >= 0"


# ===========================================================================
# 8. POST /api/nodes/heartbeat
# ===========================================================================


class TestNodesHeartbeatEndpointContract:
    """Contract tests for POST /api/nodes/heartbeat."""

    _VALID_PAYLOAD: dict = {
        "node_id": "contract-test-node",
        "status": "online",
        "timestamp": datetime.now(UTC).isoformat(),
        "cpu_load": 12,
        "ram_usage": 45,
        "agents": [],
    }

    def test_returns_200_for_valid_payload(self, client, tmp_path, monkeypatch):
        """Valid heartbeat must return HTTP 200."""
        # Patch the heartbeat directory to a tmp location so we don't touch real FS
        monkeypatch.setattr(
            "forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR",
            tmp_path / "heartbeat_nodes",
        )
        response = client.post("/api/nodes/heartbeat", json=self._VALID_PAYLOAD)
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

    def test_response_fields_present(self, client, tmp_path, monkeypatch):
        """Successful heartbeat response must include 'success', 'node_id', 'received_at'."""
        monkeypatch.setattr(
            "forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR",
            tmp_path / "heartbeat_nodes",
        )
        data = client.post("/api/nodes/heartbeat", json=self._VALID_PAYLOAD).json()
        assert "success" in data, "Response missing 'success'"
        assert data["success"] is True
        assert "node_id" in data, "Response missing 'node_id'"
        assert "received_at" in data, "Response missing 'received_at'"

    def test_node_id_is_normalized(self, client, tmp_path, monkeypatch):
        """node_id in response must be normalized (lowercase, hyphens)."""
        monkeypatch.setattr(
            "forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR",
            tmp_path / "heartbeat_nodes",
        )
        payload = {**self._VALID_PAYLOAD, "node_id": "My_Node NAME"}
        data = client.post("/api/nodes/heartbeat", json=payload).json()
        assert data["node_id"] == "my-node-name", (
            f"node_id was not normalized correctly: {data['node_id']!r}"
        )

    def test_received_at_is_iso8601(self, client, tmp_path, monkeypatch):
        """'received_at' in response must be a valid ISO-8601 datetime."""
        monkeypatch.setattr(
            "forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR",
            tmp_path / "heartbeat_nodes",
        )
        data = client.post("/api/nodes/heartbeat", json=self._VALID_PAYLOAD).json()
        assert _iso_or_none(data["received_at"]), (
            f"'received_at' must be ISO-8601, got: {data['received_at']!r}"
        )

    def test_missing_node_id_returns_422(self, client):
        """Omitting 'node_id' must return HTTP 422 (unprocessable entity)."""
        payload = {k: v for k, v in self._VALID_PAYLOAD.items() if k != "node_id"}
        response = client.post("/api/nodes/heartbeat", json=payload)
        assert response.status_code == 422, (
            f"Expected 422 for missing node_id, got {response.status_code}"
        )

    def test_empty_node_id_returns_422(self, client):
        """An empty string 'node_id' must return HTTP 422."""
        payload = {**self._VALID_PAYLOAD, "node_id": ""}
        response = client.post("/api/nodes/heartbeat", json=payload)
        assert response.status_code == 422, (
            f"Expected 422 for empty node_id, got {response.status_code}"
        )

    def test_node_entry_included_in_response(self, client, tmp_path, monkeypatch):
        """Successful response must include a 'node' object with standard fields."""
        monkeypatch.setattr(
            "forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR",
            tmp_path / "heartbeat_nodes",
        )
        data = client.post("/api/nodes/heartbeat", json=self._VALID_PAYLOAD).json()
        assert "node" in data, "Response missing 'node' field"
        node = data["node"]
        assert isinstance(node, dict), "'node' must be a dict"
        # node_id must be present inside the node object
        assert "node_id" in node, "node object missing 'node_id'"
        assert isinstance(node["node_id"], str)


# ===========================================================================
# Cross-cutting: error envelope shape
# ===========================================================================


class TestErrorResponseEnvelopeContract:
    """Verify error responses follow the standard envelope format."""

    def test_dashboard_agent_not_found_returns_error_envelope(self, client):
        """GET /api/dashboard/agents/{id} for unknown agent must return 404 error envelope."""
        response = client.get("/api/dashboard/agents/nonexistent-agent-999")
        assert response.status_code == 404
        data = response.json()
        _assert_standard_envelope(data, expect_success=False)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_throughput_hours_too_large_returns_422(self, client):
        """hours > 720 must return 422 with validation error."""
        response = client.get("/api/dashboard/throughput?hours=9999")
        assert response.status_code == 422
