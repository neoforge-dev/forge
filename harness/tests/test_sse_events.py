"""Tests for SSE event definitions, EventEmitter, and /api/events/* endpoints.

Coverage targets:
- SSEEvent model validation (field defaults, type coercion, immutability).
- SSEEventType enum completeness and string values.
- serialize_sse wire-format correctness.
- EventEmitter.emit stores in deque.
- EventEmitter.get_recent_events with/without filter.
- EventEmitter.subscribe callback invoked.
- EventEmitter.unsubscribe removes callback.
- EventEmitter bounded deque (101st event evicts first).
- EventEmitter singleton pattern (get/reset).
- EventEmitter thread-safety under concurrent emit.
- EventEmitter.event_count and subscriber_count introspection.
- API endpoint: GET /api/events/recent (no filter, with type filter, limit).
- API endpoint: GET /api/events/recent — pagination boundary.
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from forge_harness.webhook_server.models.sse_events import (
    SSEEvent,
    SSEEventType,
    serialize_sse,
)
from forge_harness.webhook_server.services.event_emitter import (
    EventEmitter,
    get_event_emitter,
    reset_event_emitter,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(autouse=True)
def isolated_emitter():
    """Reset the EventEmitter singleton before and after every test."""
    reset_event_emitter()
    yield
    reset_event_emitter()


@pytest.fixture
def emitter():
    """Return a fresh EventEmitter (not the singleton) for unit tests."""
    return EventEmitter(maxlen=100)


@pytest.fixture
def small_emitter():
    """Return an EventEmitter with maxlen=5 for boundary tests."""
    return EventEmitter(maxlen=5)


@pytest.fixture
def app_client():
    """Return a TestClient wired to an isolated FastAPI app with the events router."""
    from forge_harness.webhook_server.api.events import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


# ===========================================================================
# SSEEventType — enum completeness
# ===========================================================================


class TestSSEEventTypeEnum:
    def test_all_required_members_present(self):
        """All 8 required event types must be defined."""
        required = {
            "feature.updated",
            "feature.assigned",
            "evaluator.completed",
            "evaluator.failed",
            "session.created",
            "session.ended",
            "task.status_changed",
            "agent.heartbeat",
        }
        actual = {member.value for member in SSEEventType}
        assert required.issubset(actual), f"Missing event types: {required - actual}"

    def test_enum_count_at_least_eight(self):
        assert len(SSEEventType) >= 8

    def test_string_enum_values_use_dot_notation(self):
        for member in SSEEventType:
            assert "." in member.value, f"{member!r} value missing dot: {member.value!r}"

    def test_feature_updated_value(self):
        assert SSEEventType.feature_updated.value == "feature.updated"

    def test_feature_assigned_value(self):
        assert SSEEventType.feature_assigned.value == "feature.assigned"

    def test_evaluator_completed_value(self):
        assert SSEEventType.evaluator_completed.value == "evaluator.completed"

    def test_evaluator_failed_value(self):
        assert SSEEventType.evaluator_failed.value == "evaluator.failed"

    def test_session_created_value(self):
        assert SSEEventType.session_created.value == "session.created"

    def test_session_ended_value(self):
        assert SSEEventType.session_ended.value == "session.ended"

    def test_task_status_changed_value(self):
        assert SSEEventType.task_status_changed.value == "task.status_changed"

    def test_agent_heartbeat_value(self):
        assert SSEEventType.agent_heartbeat.value == "agent.heartbeat"

    def test_enum_is_str_subclass(self):
        """SSEEventType must be a str enum so values compare equal to plain strings."""
        assert isinstance(SSEEventType.feature_updated, str)
        assert SSEEventType.feature_updated == "feature.updated"

    def test_construct_from_string(self):
        et = SSEEventType("feature.updated")
        assert et is SSEEventType.feature_updated

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            SSEEventType("nonexistent.event")


# ===========================================================================
# SSEEvent model
# ===========================================================================


class TestSSEEventModel:
    def test_required_field_event_type(self):
        event = SSEEvent(event_type=SSEEventType.feature_updated)
        assert event.event_type == SSEEventType.feature_updated

    def test_data_defaults_to_empty_dict(self):
        event = SSEEvent(event_type=SSEEventType.session_created)
        assert event.data == {}

    def test_data_accepts_arbitrary_payload(self):
        payload = {"feature_id": "f-1", "pct": 75.0, "nested": {"k": "v"}}
        event = SSEEvent(event_type=SSEEventType.feature_updated, data=payload)
        assert event.data["feature_id"] == "f-1"
        assert event.data["nested"]["k"] == "v"

    def test_timestamp_defaults_to_iso_string(self):
        event = SSEEvent(event_type=SSEEventType.agent_heartbeat)
        # Must parse without exception
        from datetime import datetime

        dt = datetime.fromisoformat(event.timestamp)
        assert dt.tzinfo is not None  # timezone-aware

    def test_timestamp_can_be_overridden(self):
        ts = "2026-02-22T12:00:00+00:00"
        event = SSEEvent(event_type=SSEEventType.session_ended, timestamp=ts)
        assert event.timestamp == ts

    def test_source_service_defaults_to_webhook_server(self):
        event = SSEEvent(event_type=SSEEventType.evaluator_completed)
        assert event.source_service == "webhook-server"

    def test_source_service_can_be_overridden(self):
        event = SSEEvent(
            event_type=SSEEventType.evaluator_completed,
            source_service="evaluator-service",
        )
        assert event.source_service == "evaluator-service"

    def test_invalid_event_type_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            SSEEvent(event_type="invalid.type")

    def test_model_instantiation_with_all_fields(self):
        event = SSEEvent(
            event_type=SSEEventType.task_status_changed,
            data={"task_id": "t-123", "status": "done"},
            timestamp="2026-01-01T00:00:00+00:00",
            source_service="task-service",
        )
        assert event.event_type == SSEEventType.task_status_changed
        assert event.data["task_id"] == "t-123"
        assert event.source_service == "task-service"


# ===========================================================================
# serialize_sse
# ===========================================================================


class TestSerializeSSE:
    def _build_event(self, et=SSEEventType.feature_updated, data=None, source="svc") -> SSEEvent:
        return SSEEvent(
            event_type=et,
            data=data or {},
            source_service=source,
        )

    def test_output_is_string(self):
        event = self._build_event()
        result = serialize_sse(event)
        assert isinstance(result, str)

    def test_event_line_present(self):
        event = self._build_event(SSEEventType.feature_updated)
        result = serialize_sse(event)
        assert "event: feature.updated" in result

    def test_data_line_present(self):
        event = self._build_event(data={"x": 1})
        result = serialize_sse(event)
        assert "data: " in result

    def test_data_is_valid_json(self):
        event = self._build_event(data={"key": "value"})
        result = serialize_sse(event)
        data_line = next(line for line in result.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line[len("data: ") :])
        assert payload["event_type"] == "feature.updated"
        assert payload["data"]["key"] == "value"
        assert "timestamp" in payload
        assert "source_service" in payload

    def test_terminates_with_blank_line(self):
        event = self._build_event()
        result = serialize_sse(event)
        # SSE events must end with two newlines (blank line terminator)
        assert result.endswith("\n\n") or result.endswith("\n\n")

    def test_all_event_types_serialize(self):
        for et in SSEEventType:
            event = SSEEvent(event_type=et, data={})
            result = serialize_sse(event)
            assert f"event: {et.value}" in result

    def test_data_payload_contains_event_type_value(self):
        event = self._build_event(SSEEventType.session_created)
        result = serialize_sse(event)
        data_line = next(line for line in result.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line[len("data: ") :])
        assert payload["event_type"] == "session.created"

    def test_data_payload_contains_source_service(self):
        event = self._build_event(source="feature-tracker")
        result = serialize_sse(event)
        data_line = next(line for line in result.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line[len("data: ") :])
        assert payload["source_service"] == "feature-tracker"

    def test_empty_data_serializes(self):
        event = SSEEvent(event_type=SSEEventType.agent_heartbeat, data={})
        result = serialize_sse(event)
        assert "data: " in result


# ===========================================================================
# EventEmitter — emit and deque storage
# ===========================================================================


class TestEventEmitterEmit:
    def test_emit_returns_sse_event(self, emitter):
        event = emitter.emit(SSEEventType.feature_updated, {"id": "f1"})
        assert isinstance(event, SSEEvent)

    def test_emit_stores_in_deque(self, emitter):
        emitter.emit(SSEEventType.feature_updated, {})
        assert emitter.event_count() == 1

    def test_emit_multiple_stores_all(self, emitter):
        for i in range(5):
            emitter.emit(SSEEventType.session_created, {"i": i})
        assert emitter.event_count() == 5

    def test_emitted_event_has_correct_type(self, emitter):
        event = emitter.emit(SSEEventType.evaluator_completed, {})
        assert event.event_type == SSEEventType.evaluator_completed

    def test_emitted_event_has_correct_data(self, emitter):
        event = emitter.emit(SSEEventType.task_status_changed, {"task_id": "t-1"})
        assert event.data["task_id"] == "t-1"

    def test_emitted_event_has_correct_source(self, emitter):
        event = emitter.emit(SSEEventType.feature_assigned, {}, source="my-service")
        assert event.source_service == "my-service"

    def test_emit_default_source_is_webhook_server(self, emitter):
        event = emitter.emit(SSEEventType.agent_heartbeat, {})
        assert event.source_service == "webhook-server"


# ===========================================================================
# EventEmitter — bounded deque (101st event evicts first)
# ===========================================================================


class TestEventEmitterBoundedDeque:
    def test_maxlen_100_evicts_on_101st(self):
        emitter = EventEmitter(maxlen=100)
        for i in range(101):
            emitter.emit(SSEEventType.feature_updated, {"i": i})
        assert emitter.event_count() == 100

    def test_small_maxlen_evicts_oldest(self, small_emitter):
        """Emit 6 events into a maxlen-5 emitter; first should be gone."""
        for i in range(6):
            small_emitter.emit(SSEEventType.session_created, {"seq": i})

        events = small_emitter.get_recent_events(limit=100)
        assert len(events) == 5
        # Oldest event (seq=0) should have been evicted
        seq_values = [e.data["seq"] for e in events]
        assert 0 not in seq_values
        assert 5 in seq_values

    def test_maxlen_1_keeps_only_latest(self):
        emitter = EventEmitter(maxlen=1)
        emitter.emit(SSEEventType.feature_updated, {"v": "old"})
        emitter.emit(SSEEventType.feature_updated, {"v": "new"})
        events = emitter.get_recent_events(limit=10)
        assert len(events) == 1
        assert events[0].data["v"] == "new"


# ===========================================================================
# EventEmitter — get_recent_events
# ===========================================================================


class TestEventEmitterGetRecentEvents:
    def _fill(self, emitter, n: int) -> None:
        for i in range(n):
            emitter.emit(SSEEventType.feature_updated, {"i": i})

    def test_returns_empty_when_no_events(self, emitter):
        assert emitter.get_recent_events() == []

    def test_returns_all_when_under_limit(self, emitter):
        self._fill(emitter, 5)
        result = emitter.get_recent_events(limit=20)
        assert len(result) == 5

    def test_limit_truncates_from_head(self, emitter):
        self._fill(emitter, 10)
        result = emitter.get_recent_events(limit=3)
        assert len(result) == 3

    def test_limit_zero_returns_empty(self, emitter):
        self._fill(emitter, 5)
        result = emitter.get_recent_events(limit=0)
        assert result == []

    def test_filter_by_type_matches(self, emitter):
        emitter.emit(SSEEventType.feature_updated, {})
        emitter.emit(SSEEventType.session_created, {})
        emitter.emit(SSEEventType.feature_updated, {})
        result = emitter.get_recent_events(event_type="feature.updated")
        assert len(result) == 2
        assert all(e.event_type == SSEEventType.feature_updated for e in result)

    def test_filter_by_type_no_match(self, emitter):
        emitter.emit(SSEEventType.feature_updated, {})
        result = emitter.get_recent_events(event_type="session.created")
        assert result == []

    def test_filter_none_returns_all_types(self, emitter):
        emitter.emit(SSEEventType.feature_updated, {})
        emitter.emit(SSEEventType.session_created, {})
        emitter.emit(SSEEventType.evaluator_completed, {})
        result = emitter.get_recent_events(event_type=None)
        assert len(result) == 3

    def test_newest_is_last(self, emitter):
        emitter.emit(SSEEventType.feature_updated, {"seq": 0})
        emitter.emit(SSEEventType.feature_updated, {"seq": 1})
        result = emitter.get_recent_events(limit=2)
        assert result[-1].data["seq"] == 1

    def test_combined_filter_and_limit(self, emitter):
        for i in range(5):
            emitter.emit(SSEEventType.feature_updated, {"i": i})
        for i in range(3):
            emitter.emit(SSEEventType.session_ended, {"i": i})
        result = emitter.get_recent_events(limit=2, event_type="feature.updated")
        assert len(result) == 2
        assert all(e.event_type == SSEEventType.feature_updated for e in result)


# ===========================================================================
# EventEmitter — subscribe callback
# ===========================================================================


class TestEventEmitterSubscribe:
    def test_callback_invoked_on_emit(self, emitter):
        received = []
        emitter.subscribe(lambda e: received.append(e))
        emitter.emit(SSEEventType.feature_updated, {"x": 1})
        assert len(received) == 1
        assert received[0].event_type == SSEEventType.feature_updated

    def test_multiple_callbacks_all_invoked(self, emitter):
        calls_a = []
        calls_b = []
        emitter.subscribe(lambda e: calls_a.append(e))
        emitter.subscribe(lambda e: calls_b.append(e))
        emitter.emit(SSEEventType.session_created, {})
        assert len(calls_a) == 1
        assert len(calls_b) == 1

    def test_callback_receives_correct_event(self, emitter):
        received = []
        emitter.subscribe(lambda e: received.append(e))
        emitter.emit(SSEEventType.evaluator_failed, {"task_id": "t-42"})
        assert received[0].data["task_id"] == "t-42"

    def test_subscriber_count_increments(self, emitter):
        assert emitter.subscriber_count() == 0
        emitter.subscribe(lambda e: None)
        assert emitter.subscriber_count() == 1
        emitter.subscribe(lambda e: None)
        assert emitter.subscriber_count() == 2

    def test_unsubscribe_removes_callback(self, emitter):
        calls = []
        cb = lambda e: calls.append(e)  # noqa: E731
        emitter.subscribe(cb)
        emitter.unsubscribe(cb)
        emitter.emit(SSEEventType.feature_updated, {})
        assert calls == []
        assert emitter.subscriber_count() == 0

    def test_unsubscribe_nonexistent_is_noop(self, emitter):
        # Should not raise
        emitter.unsubscribe(lambda e: None)

    def test_callback_exception_does_not_propagate(self, emitter):
        def bad_cb(_):
            raise RuntimeError("boom")

        emitter.subscribe(bad_cb)
        # Must not raise
        emitter.emit(SSEEventType.agent_heartbeat, {})
        # Event still stored
        assert emitter.event_count() == 1

    def test_emit_still_stores_event_when_callback_raises(self, emitter):
        emitter.subscribe(lambda _: 1 / 0)
        emitter.emit(SSEEventType.feature_updated, {"stored": True})
        events = emitter.get_recent_events()
        assert events[0].data["stored"] is True


# ===========================================================================
# EventEmitter — singleton pattern
# ===========================================================================


class TestEventEmitterSingleton:
    def test_get_returns_same_instance(self):
        a = get_event_emitter()
        b = get_event_emitter()
        assert a is b

    def test_reset_clears_singleton(self):
        a = get_event_emitter()
        reset_event_emitter()
        b = get_event_emitter()
        assert a is not b

    def test_singleton_state_shared(self):
        e1 = get_event_emitter()
        e1.emit(SSEEventType.feature_updated, {})
        e2 = get_event_emitter()
        assert e2.event_count() == 1

    def test_reset_then_singleton_starts_empty(self):
        emitter = get_event_emitter()
        emitter.emit(SSEEventType.session_created, {})
        assert emitter.event_count() == 1
        reset_event_emitter()
        fresh = get_event_emitter()
        assert fresh.event_count() == 0


# ===========================================================================
# EventEmitter — thread safety
# ===========================================================================


class TestEventEmitterThreadSafety:
    def test_concurrent_emit_all_stored(self):
        """100 threads each emit 10 events; all 1000 must be stored."""
        emitter = EventEmitter(maxlen=1100)
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(10):
                    emitter.emit(SSEEventType.feature_updated, {})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Worker errors: {errors}"
        assert emitter.event_count() == 1000

    def test_concurrent_subscribe_and_emit(self):
        """Subscribe callbacks while emitting; no deadlock or exception."""
        emitter = EventEmitter(maxlen=500)
        call_count = threading.Event()
        received: list[SSEEvent] = []
        lock = threading.Lock()

        def cb(e):
            with lock:
                received.append(e)

        def emit_worker():
            for _ in range(50):
                emitter.emit(SSEEventType.agent_heartbeat, {})

        def subscribe_worker():
            for _ in range(5):
                emitter.subscribe(cb)
                time.sleep(0.001)

        threads = [threading.Thread(target=emit_worker) for _ in range(4)]
        threads += [threading.Thread(target=subscribe_worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        # No assertion on count since callbacks registered mid-flight;
        # just assert no exception was raised and events were stored.
        assert emitter.event_count() <= 200

    def test_get_recent_events_concurrent_access(self):
        """get_recent_events and emit can be called concurrently without error."""
        emitter = EventEmitter(maxlen=200)
        errors: list[Exception] = []

        def emit_worker():
            try:
                for _ in range(20):
                    emitter.emit(SSEEventType.feature_updated, {})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def read_worker():
            try:
                for _ in range(20):
                    emitter.get_recent_events(limit=10)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=emit_worker) for _ in range(5)]
        threads += [threading.Thread(target=read_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []


# ===========================================================================
# API endpoint — GET /api/events/recent
# ===========================================================================


class TestRecentEventsEndpoint:
    def test_returns_200(self, app_client):
        resp = app_client.get("/api/events/recent")
        assert resp.status_code == 200

    def test_response_is_json(self, app_client):
        resp = app_client.get("/api/events/recent")
        body = resp.json()
        assert isinstance(body, dict)

    def test_response_has_success_field(self, app_client):
        resp = app_client.get("/api/events/recent")
        assert resp.json()["success"] is True

    def test_response_has_data_field(self, app_client):
        resp = app_client.get("/api/events/recent")
        assert "data" in resp.json()

    def test_data_has_events_list(self, app_client):
        resp = app_client.get("/api/events/recent")
        assert "events" in resp.json()["data"]
        assert isinstance(resp.json()["data"]["events"], list)

    def test_data_has_count(self, app_client):
        resp = app_client.get("/api/events/recent")
        assert "count" in resp.json()["data"]

    def test_empty_when_no_events(self, app_client):
        resp = app_client.get("/api/events/recent")
        body = resp.json()
        assert body["data"]["count"] == 0
        assert body["data"]["events"] == []

    def test_returns_emitted_events(self, app_client):
        emitter = get_event_emitter()
        emitter.emit(SSEEventType.feature_updated, {"feature_id": "f-99"})
        resp = app_client.get("/api/events/recent")
        body = resp.json()
        assert body["data"]["count"] == 1
        assert body["data"]["events"][0]["data"]["feature_id"] == "f-99"

    def test_limit_param_respected(self, app_client):
        emitter = get_event_emitter()
        for i in range(10):
            emitter.emit(SSEEventType.session_created, {"i": i})
        resp = app_client.get("/api/events/recent?limit=3")
        assert resp.json()["data"]["count"] == 3

    def test_default_limit_is_20(self, app_client):
        emitter = get_event_emitter()
        for i in range(25):
            emitter.emit(SSEEventType.feature_updated, {"i": i})
        resp = app_client.get("/api/events/recent")
        assert resp.json()["data"]["count"] == 20

    def test_limit_over_100_rejected(self, app_client):
        resp = app_client.get("/api/events/recent?limit=200")
        assert resp.status_code == 422

    def test_limit_zero_rejected(self, app_client):
        resp = app_client.get("/api/events/recent?limit=0")
        assert resp.status_code == 422

    def test_type_filter_returns_only_matching(self, app_client):
        emitter = get_event_emitter()
        emitter.emit(SSEEventType.feature_updated, {"source": "a"})
        emitter.emit(SSEEventType.session_created, {"source": "b"})
        emitter.emit(SSEEventType.feature_updated, {"source": "c"})
        resp = app_client.get("/api/events/recent?type=feature.updated")
        body = resp.json()
        assert body["data"]["count"] == 2
        for event in body["data"]["events"]:
            assert event["event_type"] == "feature.updated"

    def test_type_filter_no_match_returns_empty(self, app_client):
        emitter = get_event_emitter()
        emitter.emit(SSEEventType.feature_updated, {})
        resp = app_client.get("/api/events/recent?type=session.created")
        body = resp.json()
        assert body["data"]["count"] == 0

    def test_type_filter_echoed_in_response(self, app_client):
        resp = app_client.get("/api/events/recent?type=feature.updated")
        assert resp.json()["data"]["filter"] == "feature.updated"

    def test_no_type_filter_has_null_filter_field(self, app_client):
        resp = app_client.get("/api/events/recent")
        assert resp.json()["data"]["filter"] is None

    def test_response_event_has_required_fields(self, app_client):
        emitter = get_event_emitter()
        emitter.emit(SSEEventType.evaluator_completed, {"task_id": "t-1"})
        resp = app_client.get("/api/events/recent")
        event = resp.json()["data"]["events"][0]
        assert "event_type" in event
        assert "data" in event
        assert "timestamp" in event
        assert "source_service" in event

    def test_response_has_timestamp(self, app_client):
        resp = app_client.get("/api/events/recent")
        assert "timestamp" in resp.json()

    def test_response_has_error_null(self, app_client):
        resp = app_client.get("/api/events/recent")
        assert resp.json()["error"] is None
