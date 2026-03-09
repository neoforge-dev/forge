"""Comprehensive tests for forge_harness/webhook_server/api/audit.py

Tests cover all 4 endpoints:
- GET  /api/audit/config   - Current audit sampler configuration
- POST /api/audit/config   - Update audit sampler configuration
- GET  /api/audit/queue    - List sampled audits by status
- POST /api/audit/sample   - Manually trigger audit sampling for a task

All external dependencies (AuditSampler, AuditLogger) are mocked.
Auth is bypassed via dependency_overrides.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers: mock sampler objects
# ---------------------------------------------------------------------------


def _make_sampling_decision(
    task_id: str = "task-001",
    lane: str = "manual",
    selected: bool = True,
    sample_rate: float = 0.125,
    random_value: float = 0.05,
    decision_id: str = "dec-001",
) -> Mock:
    """Return a mock SamplingDecision-like object."""
    decision = Mock()
    decision.task_id = task_id
    decision.lane = lane
    decision.selected = selected
    decision.sample_rate = sample_rate
    decision.random_value = random_value
    decision.decision_id = decision_id
    decision.timestamp = datetime.now(UTC)
    return decision


def _make_audit_record(
    task_id: str = "task-001",
    lane: str = "docs",
    status: str = "pending",
    selected: bool = True,
) -> Mock:
    """Return a mock AuditRecord-like object."""
    record = Mock()
    record.task_id = task_id
    record.lane = lane
    record.status = Mock(value=status)
    record.to_dict.return_value = {
        "task_id": task_id,
        "lane": lane,
        "selected": selected,
        "sample_rate": 0.125,
        "random_value": 0.05,
        "decision_id": "dec-001",
        "decision_timestamp": datetime.now(UTC).isoformat(),
        "status": status,
        "auditor_id": None,
        "passed": None,
        "notes": "",
        "reviewed_at": None,
    }
    return record


def _make_audit_summary() -> Mock:
    """Return a mock AuditSummary-like object."""
    summary = Mock()
    summary.to_dict.return_value = {
        "total_completions": 100,
        "total_sampled": 13,
        "total_reviewed": 10,
        "total_passed": 9,
        "total_failed": 1,
        "total_pending": 3,
        "effective_rate": 0.13,
        "pass_rate": 0.9,
        "period_start": None,
        "period_end": None,
    }
    return summary


def _make_sampler(
    sample_rate: float = 0.125,
    pending: list | None = None,
    records: list | None = None,
    decision: Mock | None = None,
) -> Mock:
    """Return a mock AuditSampler-like object."""
    sampler = Mock()
    sampler.sample_rate = sample_rate
    sampler.get_pending_audits.return_value = pending or []
    sampler.get_all_records.return_value = records or []
    sampler.get_daily_summary.return_value = _make_audit_summary()
    sampler.update_config.return_value = sample_rate
    sampler.should_audit.return_value = decision or _make_sampling_decision()
    return sampler


def _make_audit_logger() -> Mock:
    logger = Mock()
    logger.log_configuration_change = AsyncMock()
    return logger


# ---------------------------------------------------------------------------
# App factory with auth and sampler mocked
# ---------------------------------------------------------------------------


def _build_app(sampler: Mock, audit_logger: Mock | None = None) -> FastAPI:
    """Build a minimal FastAPI app with the audit router and auth bypassed."""
    from forge_harness.webhook_server.api import audit as audit_module
    from forge_harness.webhook_server.core import dependencies

    _logger = audit_logger or _make_audit_logger()

    app = FastAPI()
    app.include_router(audit_module.router)

    # Bypass auth
    _admin_user = {"sub": "test", "permissions": ["admin"]}
    app.dependency_overrides[dependencies.verify_auth] = lambda: _admin_user
    app.dependency_overrides[dependencies.verify_unified_auth] = lambda: _admin_user

    def _noop_require_permission(permission: str):
        async def _check():
            return _admin_user
        return _check

    app.dependency_overrides[dependencies.require_permission] = _noop_require_permission

    return app


def _client_with_patches(sampler: Mock, audit_logger: Mock | None = None) -> TestClient:
    """Build a TestClient with sampler and audit_logger patched at import level."""
    _logger = audit_logger or _make_audit_logger()
    with (
        patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ),
        patch(
            "forge_harness.webhook_server.api.audit.get_audit_logger",
            return_value=_logger,
        ),
    ):
        app = _build_app(sampler, _logger)
        return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/audit/config
# ---------------------------------------------------------------------------


class TestGetAuditConfig:
    """Tests for GET /api/audit/config."""

    def test_returns_200_with_config(self):
        sampler = _make_sampler(sample_rate=0.125)
        with (
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_sampler",
                return_value=sampler,
            ),
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/config")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "sample_rate" in data
        assert "min_sample_rate" in data
        assert "max_sample_rate" in data
        assert "pending_count" in data
        assert "total_records" in data
        assert "summary" in data

    def test_sample_rate_returned_correctly(self):
        sampler = _make_sampler(sample_rate=0.2)
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/config")

        assert resp.json()["data"]["sample_rate"] == 0.2

    def test_pending_count_reflects_pending_records(self):
        records = [_make_audit_record(status="pending") for _ in range(3)]
        sampler = _make_sampler(pending=records)
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/config")

        assert resp.json()["data"]["pending_count"] == 3

    def test_total_records_reflects_all_records(self):
        records = [_make_audit_record() for _ in range(7)]
        sampler = _make_sampler(records=records)
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/config")

        assert resp.json()["data"]["total_records"] == 7

    def test_min_max_sample_rate_constants_present(self):
        sampler = _make_sampler()
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            from forge_harness.dark_factory.audit_sampler import (
                MAX_SAMPLE_RATE,
                MIN_SAMPLE_RATE,
            )

            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/config")

        data = resp.json()["data"]
        assert data["min_sample_rate"] == MIN_SAMPLE_RATE
        assert data["max_sample_rate"] == MAX_SAMPLE_RATE


# ---------------------------------------------------------------------------
# POST /api/audit/config
# ---------------------------------------------------------------------------


class TestUpdateAuditConfig:
    """Tests for POST /api/audit/config."""

    def test_update_sample_rate_returns_200(self):
        sampler = _make_sampler(sample_rate=0.2)
        sampler.update_config.return_value = 0.2
        audit_logger = _make_audit_logger()

        with (
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_sampler",
                return_value=sampler,
            ),
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_logger",
                return_value=audit_logger,
            ),
        ):
            app = _build_app(sampler, audit_logger)
            client = TestClient(app)
            resp = client.post("/api/audit/config", json={"sample_rate": 0.2})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "sample_rate" in data
        assert "previous_sample_rate" in data

    def test_previous_sample_rate_captured(self):
        sampler = _make_sampler(sample_rate=0.125)
        sampler.update_config.return_value = 0.3
        audit_logger = _make_audit_logger()

        with (
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_sampler",
                return_value=sampler,
            ),
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_logger",
                return_value=audit_logger,
            ),
        ):
            app = _build_app(sampler, audit_logger)
            client = TestClient(app)
            resp = client.post("/api/audit/config", json={"sample_rate": 0.3})

        data = resp.json()["data"]
        assert data["previous_sample_rate"] == 0.125
        assert data["sample_rate"] == 0.3

    def test_audit_logger_called_on_update(self):
        sampler = _make_sampler(sample_rate=0.125)
        sampler.update_config.return_value = 0.5
        audit_logger = _make_audit_logger()

        with (
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_sampler",
                return_value=sampler,
            ),
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_logger",
                return_value=audit_logger,
            ),
        ):
            app = _build_app(sampler, audit_logger)
            client = TestClient(app)
            client.post("/api/audit/config", json={"sample_rate": 0.5})

        audit_logger.log_configuration_change.assert_called_once()

    def test_audit_logger_failure_does_not_block_response(self):
        """If audit logging fails, the API response should still succeed."""
        sampler = _make_sampler(sample_rate=0.125)
        sampler.update_config.return_value = 0.5
        audit_logger = _make_audit_logger()
        audit_logger.log_configuration_change = AsyncMock(
            side_effect=RuntimeError("logger unavailable")
        )

        with (
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_sampler",
                return_value=sampler,
            ),
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_logger",
                return_value=audit_logger,
            ),
        ):
            app = _build_app(sampler, audit_logger)
            client = TestClient(app)
            resp = client.post("/api/audit/config", json={"sample_rate": 0.5})

        assert resp.status_code == 200

    def test_sample_rate_too_low_returns_422(self):
        sampler = _make_sampler()
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            # Below MIN_SAMPLE_RATE=0.01
            resp = client.post("/api/audit/config", json={"sample_rate": 0.005})

        assert resp.status_code == 422

    def test_sample_rate_too_high_returns_422(self):
        sampler = _make_sampler()
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            # Above MAX_SAMPLE_RATE=1.0
            resp = client.post("/api/audit/config", json={"sample_rate": 1.1})

        assert resp.status_code == 422

    def test_missing_sample_rate_returns_422(self):
        sampler = _make_sampler()
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.post("/api/audit/config", json={})

        assert resp.status_code == 422

    def test_custom_operator_and_reason(self):
        sampler = _make_sampler(sample_rate=0.125)
        sampler.update_config.return_value = 0.2
        audit_logger = _make_audit_logger()

        with (
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_sampler",
                return_value=sampler,
            ),
            patch(
                "forge_harness.webhook_server.api.audit.get_audit_logger",
                return_value=audit_logger,
            ),
        ):
            app = _build_app(sampler, audit_logger)
            client = TestClient(app)
            resp = client.post(
                "/api/audit/config",
                json={"sample_rate": 0.2, "operator": "alice", "reason": "quality review"},
            )

        assert resp.status_code == 200
        # Audit logger context should include operator and reason
        call_kwargs = audit_logger.log_configuration_change.call_args
        assert call_kwargs.kwargs["actor"]["id"] == "alice"
        assert call_kwargs.kwargs["context"]["reason"] == "quality review"


# ---------------------------------------------------------------------------
# POST /api/audit/sample
# ---------------------------------------------------------------------------


class TestTriggerAuditSample:
    """Tests for POST /api/audit/sample."""

    def test_returns_200_with_decision(self):
        decision = _make_sampling_decision(selected=True)
        sampler = _make_sampler(decision=decision)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.post("/api/audit/sample", json={"task_id": "task-001"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "task_id" in data
        assert "selected" in data
        assert "decision_id" in data
        assert "random_value" in data
        assert "sample_rate" in data

    def test_task_id_in_response(self):
        decision = _make_sampling_decision(task_id="IS-999")
        sampler = _make_sampler(decision=decision)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.post("/api/audit/sample", json={"task_id": "IS-999"})

        data = resp.json()["data"]
        assert data["task_id"] == "IS-999"

    def test_selected_is_boolean(self):
        decision = _make_sampling_decision(selected=False)
        sampler = _make_sampler(decision=decision)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.post("/api/audit/sample", json={"task_id": "task-skip"})

        data = resp.json()["data"]
        assert data["selected"] is False

    def test_missing_task_id_returns_422(self):
        sampler = _make_sampler()
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.post("/api/audit/sample", json={})

        assert resp.status_code == 422

    def test_empty_task_id_returns_422(self):
        sampler = _make_sampler()
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.post("/api/audit/sample", json={"task_id": ""})

        assert resp.status_code == 422

    def test_custom_lane_passed_to_sampler(self):
        decision = _make_sampling_decision(lane="docs")
        sampler = _make_sampler(decision=decision)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            client.post(
                "/api/audit/sample",
                json={"task_id": "task-doc", "lane": "docs"},
            )

        sampler.should_audit.assert_called_once_with(task_id="task-doc", lane="docs")

    def test_default_lane_is_manual(self):
        sampler = _make_sampler()
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            client.post("/api/audit/sample", json={"task_id": "task-default"})

        sampler.should_audit.assert_called_once_with(
            task_id="task-default", lane="manual"
        )


# ---------------------------------------------------------------------------
# GET /api/audit/queue
# ---------------------------------------------------------------------------


class TestGetAuditQueue:
    """Tests for GET /api/audit/queue."""

    def test_returns_200_with_pending_by_default(self):
        records = [_make_audit_record(status="pending")]
        sampler = _make_sampler(records=records)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/queue")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "status" in data
        assert "count" in data
        assert "audits" in data
        assert data["status"] == "pending"

    def test_filters_by_status_passed(self):
        records = [
            _make_audit_record(task_id="t-1", status="passed"),
            _make_audit_record(task_id="t-2", status="pending"),
        ]
        sampler = _make_sampler(records=records)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/queue?status=passed")

        data = resp.json()["data"]
        assert data["status"] == "passed"
        assert all(a["status"] == "passed" for a in data["audits"])

    def test_filters_by_status_failed(self):
        records = [
            _make_audit_record(task_id="t-fail", status="failed"),
            _make_audit_record(task_id="t-pass", status="passed"),
        ]
        sampler = _make_sampler(records=records)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/queue?status=failed")

        data = resp.json()["data"]
        assert len(data["audits"]) == 1
        assert data["audits"][0]["status"] == "failed"

    def test_status_all_returns_all_records(self):
        records = [
            _make_audit_record(task_id="t-1", status="passed"),
            _make_audit_record(task_id="t-2", status="pending"),
            _make_audit_record(task_id="t-3", status="failed"),
        ]
        sampler = _make_sampler(records=records)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/queue?status=all")

        data = resp.json()["data"]
        assert data["count"] == 3

    def test_invalid_status_returns_400(self):
        sampler = _make_sampler()
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/queue?status=nonexistent_status")

        assert resp.status_code == 400

    def test_limit_param_respected(self):
        records = [_make_audit_record(task_id=f"t-{i}", status="pending") for i in range(10)]
        sampler = _make_sampler(records=records)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/queue?status=pending&limit=3")

        data = resp.json()["data"]
        assert data["count"] == 3
        assert len(data["audits"]) == 3

    def test_empty_queue_returns_count_zero(self):
        sampler = _make_sampler(records=[])
        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/queue?status=pending")

        data = resp.json()["data"]
        assert data["count"] == 0
        assert data["audits"] == []

    def test_skipped_status_filter(self):
        records = [
            _make_audit_record(task_id="t-skip", status="skipped"),
            _make_audit_record(task_id="t-done", status="passed"),
        ]
        sampler = _make_sampler(records=records)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            resp = client.get("/api/audit/queue?status=skipped")

        data = resp.json()["data"]
        assert len(data["audits"]) == 1
        assert data["audits"][0]["status"] == "skipped"

    def test_status_normalized_case_insensitive(self):
        records = [_make_audit_record(status="pending")]
        sampler = _make_sampler(records=records)

        with patch(
            "forge_harness.webhook_server.api.audit.get_audit_sampler",
            return_value=sampler,
        ):
            app = _build_app(sampler)
            client = TestClient(app)
            # Uppercase "PENDING" should be normalized to "pending"
            resp = client.get("/api/audit/queue?status=PENDING")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "pending"


# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestUpdateAuditConfigRequest:
    """Validate Pydantic model UpdateAuditConfigRequest."""

    def test_valid_sample_rate(self):
        from forge_harness.webhook_server.api.audit import UpdateAuditConfigRequest

        req = UpdateAuditConfigRequest(sample_rate=0.2)
        assert req.sample_rate == 0.2
        assert req.operator == "api"
        assert req.reason == ""

    def test_boundary_values(self):
        from forge_harness.dark_factory.audit_sampler import MAX_SAMPLE_RATE, MIN_SAMPLE_RATE
        from forge_harness.webhook_server.api.audit import UpdateAuditConfigRequest

        req_min = UpdateAuditConfigRequest(sample_rate=MIN_SAMPLE_RATE)
        assert req_min.sample_rate == MIN_SAMPLE_RATE

        req_max = UpdateAuditConfigRequest(sample_rate=MAX_SAMPLE_RATE)
        assert req_max.sample_rate == MAX_SAMPLE_RATE

    def test_below_minimum_raises(self):
        from forge_harness.webhook_server.api.audit import UpdateAuditConfigRequest

        with pytest.raises(ValidationError):
            UpdateAuditConfigRequest(sample_rate=0.001)

    def test_above_maximum_raises(self):
        from forge_harness.webhook_server.api.audit import UpdateAuditConfigRequest

        with pytest.raises(ValidationError):
            UpdateAuditConfigRequest(sample_rate=1.5)

    def test_custom_operator_and_reason(self):
        from forge_harness.webhook_server.api.audit import UpdateAuditConfigRequest

        req = UpdateAuditConfigRequest(sample_rate=0.5, operator="alice", reason="QA audit")
        assert req.operator == "alice"
        assert req.reason == "QA audit"


class TestManualAuditSampleRequest:
    """Validate Pydantic model ManualAuditSampleRequest."""

    def test_valid_minimal(self):
        from forge_harness.webhook_server.api.audit import ManualAuditSampleRequest

        req = ManualAuditSampleRequest(task_id="task-001")
        assert req.task_id == "task-001"
        assert req.lane == "manual"

    def test_custom_lane(self):
        from forge_harness.webhook_server.api.audit import ManualAuditSampleRequest

        req = ManualAuditSampleRequest(task_id="task-002", lane="docs")
        assert req.lane == "docs"

    def test_empty_task_id_raises(self):
        from forge_harness.webhook_server.api.audit import ManualAuditSampleRequest

        with pytest.raises(ValidationError):
            ManualAuditSampleRequest(task_id="")
