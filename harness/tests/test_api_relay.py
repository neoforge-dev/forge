"""Tests for /api/relay endpoints.

Covers:
- GET /api/relay/deliveries          list with optional filters (state, agent, limit)
- GET /api/relay/deliveries/{id}     single record lookup + 404 path
- GET /api/relay/stats               histogram and aggregate statistics
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_harness.models.delivery import DeliveryRecord, DeliveryState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    message_id: str,
    state: DeliveryState = DeliveryState.QUEUED,
    target_agent: str | None = "gemini",
    task_id: str | None = None,
) -> DeliveryRecord:
    """Build a minimal DeliveryRecord for use in tests."""
    return DeliveryRecord(
        message_id=message_id,
        task_id=task_id or message_id,
        source_node="prya",
        target_node="prya",
        target_agent=target_agent,
        state=state,
        created_at=datetime.now(UTC),
        payload_summary="Test dispatch content",
    )


def _write_jsonl(path: Path, records: list[DeliveryRecord]) -> None:
    """Append each record as a JSON line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(rec.model_dump_json() + "\n")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a test client with the relay router mounted."""
    from forge_harness.webhook_server.api.relay import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    """Test client with auth dependency bypassed (operator-level)."""
    from forge_harness.webhook_server.core.dependencies import verify_unified_auth

    client.app.dependency_overrides[verify_unified_auth] = lambda: {
        "sub": "test-operator",
        "permissions": ["operator"],
    }
    yield client
    client.app.dependency_overrides.clear()


@pytest.fixture
def sample_log(tmp_path) -> Path:
    """JSONL log file containing a set of delivery records."""
    log = tmp_path / "delivery_log.jsonl"
    records = [
        _make_record("msg_001", DeliveryState.QUEUED, target_agent="gemini"),
        _make_record("msg_002", DeliveryState.DISPATCHED, target_agent="kimi"),
        _make_record("msg_003", DeliveryState.DELIVERED, target_agent="gemini"),
        _make_record("msg_003", DeliveryState.ACKED, target_agent="gemini"),  # second entry for msg_003
        _make_record("msg_004", DeliveryState.FAILED, target_agent="minimax"),
    ]
    _write_jsonl(log, records)
    return log


# ---------------------------------------------------------------------------
# Tests: GET /api/relay/deliveries
# ---------------------------------------------------------------------------


class TestListDeliveries:
    """Tests for the deliveries list endpoint."""

    def test_no_auth_returns_401(self, client):
        response = client.get("/api/relay/deliveries")
        assert response.status_code == 401

    def test_empty_log_returns_empty_list(self, authed_client, tmp_path):
        log = tmp_path / "empty_log.jsonl"
        # Log does not exist — should return empty result gracefully
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            log,
        ):
            response = authed_client.get("/api/relay/deliveries")

        assert response.status_code == 200
        data = response.json()
        assert data["deliveries"] == []
        assert data["total"] == 0
        assert data["filtered"] == 0

    def test_returns_deduplicated_records(self, authed_client, sample_log):
        """msg_003 has two entries; only latest should appear."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries")

        assert response.status_code == 200
        data = response.json()
        # 4 unique message_ids (msg_001, 002, 003, 004)
        assert data["total"] == 4
        ids = {d["message_id"] for d in data["deliveries"]}
        assert "msg_003" in ids
        # msg_003 should be in ACKED state (latest wins)
        msg_003 = next(d for d in data["deliveries"] if d["message_id"] == "msg_003")
        assert msg_003["state"] == "acked"

    def test_filter_by_state(self, authed_client, sample_log):
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries?state=queued")

        assert response.status_code == 200
        data = response.json()
        assert data["filtered"] == 1
        assert all(d["state"] == "queued" for d in data["deliveries"])

    def test_filter_by_agent(self, authed_client, sample_log):
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries?agent=kimi")

        assert response.status_code == 200
        data = response.json()
        assert data["filtered"] == 1
        assert data["deliveries"][0]["target_agent"] == "kimi"

    def test_filter_by_state_and_agent(self, authed_client, sample_log):
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries?state=queued&agent=gemini")

        assert response.status_code == 200
        data = response.json()
        assert data["filtered"] == 1
        assert data["deliveries"][0]["message_id"] == "msg_001"

    def test_invalid_state_filter_returns_422(self, authed_client, sample_log):
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries?state=invalid_state")

        assert response.status_code == 422

    def test_limit_is_respected(self, authed_client, tmp_path):
        """Verify the limit query parameter truncates results."""
        log = tmp_path / "big_log.jsonl"
        records = [
            _make_record(f"msg_{i:03d}", DeliveryState.QUEUED) for i in range(20)
        ]
        _write_jsonl(log, records)

        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            log,
        ):
            response = authed_client.get("/api/relay/deliveries?limit=5")

        assert response.status_code == 200
        data = response.json()
        assert len(data["deliveries"]) == 5
        assert data["total"] == 20
        assert data["filtered"] == 20

    def test_malformed_lines_are_skipped(self, authed_client, tmp_path):
        """The endpoint should not crash on malformed JSONL lines."""
        log = tmp_path / "bad_log.jsonl"
        with log.open("w") as fh:
            fh.write('{"this": "is garbage\n')
            fh.write(_make_record("msg_good", DeliveryState.DELIVERED).model_dump_json() + "\n")
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            log,
        ):
            response = authed_client.get("/api/relay/deliveries")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["deliveries"][0]["message_id"] == "msg_good"


# ---------------------------------------------------------------------------
# Tests: GET /api/relay/deliveries/{message_id}
# ---------------------------------------------------------------------------


class TestGetDelivery:
    """Tests for the single-record lookup endpoint."""

    def test_no_auth_returns_401(self, client):
        response = client.get("/api/relay/deliveries/msg_001")
        assert response.status_code == 401

    def test_returns_latest_state_for_known_id(self, authed_client, sample_log):
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries/msg_003")

        assert response.status_code == 200
        data = response.json()
        assert data["delivery"]["message_id"] == "msg_003"
        assert data["delivery"]["state"] == "acked"

    def test_includes_full_history(self, authed_client, sample_log):
        """msg_003 has two entries; both should appear in history."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries/msg_003")

        data = response.json()
        assert len(data["history"]) == 2
        states = [h["state"] for h in data["history"]]
        assert "delivered" in states
        assert "acked" in states

    def test_unknown_message_id_returns_404(self, authed_client, sample_log):
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries/msg_nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_unknown_id_on_empty_log_returns_404(self, authed_client, tmp_path):
        missing = tmp_path / "missing.jsonl"  # does not exist
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            missing,
        ):
            response = authed_client.get("/api/relay/deliveries/msg_any")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/relay/stats
# ---------------------------------------------------------------------------


class TestGetStats:
    """Tests for the stats endpoint."""

    def test_no_auth_returns_401(self, client):
        response = client.get("/api/relay/stats")
        assert response.status_code == 401

    def test_empty_log_returns_zero_stats(self, authed_client, tmp_path):
        missing = tmp_path / "no_log.jsonl"
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            missing,
        ):
            response = authed_client.get("/api/relay/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["by_state"] == {}
        assert data["mean_delivery_seconds"] is None

    def test_returns_counts_per_state(self, authed_client, sample_log):
        """The stats endpoint should aggregate by latest state per message."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/stats")

        assert response.status_code == 200
        data = response.json()
        # 4 unique messages: queued=1, dispatched=1, acked=1 (msg_003 latest), failed=1
        assert data["total"] == 4
        by_state = data["by_state"]
        assert by_state.get("queued", 0) == 1
        assert by_state.get("dispatched", 0) == 1
        assert by_state.get("acked", 0) == 1
        assert by_state.get("failed", 0) == 1

    def test_includes_delivery_log_path(self, authed_client, sample_log):
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/stats")

        data = response.json()
        assert "delivery_log_path" in data
        assert isinstance(data["delivery_log_path"], str)

    def test_mean_delivery_seconds_computed_when_acked(self, authed_client, tmp_path):
        """Mean delivery seconds should be non-None when acked records exist."""
        log = tmp_path / "acked_log.jsonl"
        now = datetime.now(UTC)

        from datetime import timedelta

        rec = DeliveryRecord(
            message_id="msg_ack",
            task_id="task-ack",
            source_node="prya",
            target_node="prya",
            state=DeliveryState.ACKED,
            created_at=now - timedelta(seconds=30),
            acked_at=now,
            payload_summary="acked message",
        )
        _write_jsonl(log, [rec])

        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            log,
        ):
            response = authed_client.get("/api/relay/stats")

        data = response.json()
        assert data["total"] == 1
        assert data["mean_delivery_seconds"] is not None
        assert data["mean_delivery_seconds"] >= 0

    def test_stats_resilient_when_relay_worker_not_running(self, authed_client, sample_log):
        """poll_interval and max_retries may be None if worker is not initialised."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ), patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker" if False else
            "forge_harness.webhook_server.services.relay_worker.get_relay_worker",
            side_effect=RuntimeError("worker not running"),
        ):
            response = authed_client.get("/api/relay/stats")

        # Should still succeed — worker config fields are optional
        assert response.status_code == 200
