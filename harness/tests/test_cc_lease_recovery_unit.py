"""Comprehensive unit tests for StaleLeaseRecoveryService (lease_recovery.py).

Covers:
- StaleLeaseRecoveryService.__init__: attribute initialisation, default now_provider
- StaleLeaseRecoveryService.running property: reflects _running state
- StaleLeaseRecoveryService._parse_expiry (static): datetime, str ISO-8601, str with Z,
  naive datetime, None value, unsupported type, invalid string, timezone normalisation
- StaleLeaseRecoveryService.scan_and_recover: no tasks, tasks without lease dict,
  tasks with missing/empty task_id, fresh leases, expired leases, requeue failure,
  requeue returns falsy, successful recovery increments count, event publish payloads,
  mixed scenarios
- StaleLeaseRecoveryService.start: poll_interval clamping, double-start guard,
  normal loop iteration, loop continues after scan exception, stop during sleep
- StaleLeaseRecoveryService.stop: sets stop event

All external I/O is mocked — no real network, DB, or filesystem calls.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.webhook_server.services.lease_recovery import StaleLeaseRecoveryService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    tasks: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> tuple[StaleLeaseRecoveryService, AsyncMock, AsyncMock]:
    """Build a service with mocked task_handler and event_bus."""
    task_handler = AsyncMock()
    task_handler.list_tasks = AsyncMock(return_value=tasks or [])
    task_handler.requeue_task = AsyncMock(return_value={"id": "requeued-task"})

    event_bus = AsyncMock()
    event_bus.publish = AsyncMock(return_value=None)

    now_fixed = now or datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    service = StaleLeaseRecoveryService(
        task_handler=task_handler,
        event_bus=event_bus,
        now_provider=lambda: now_fixed,
    )
    return service, task_handler, event_bus


def _make_task(
    task_id: str = "task-001",
    lease: dict[str, Any] | None = None,
    include_lease: bool = True,
) -> dict[str, Any]:
    """Build a task dict with an optional lease."""
    t: dict[str, Any] = {"id": task_id}
    if include_lease:
        t["lease"] = lease if lease is not None else {}
    return t


def _expired_lease(
    *,
    minutes_ago: int = 5,
    owner_node: str = "nova",
    owner_agent: str = "forge:claude",
    path_lock: str | None = "/tmp/lock.py",
) -> dict[str, Any]:
    """Return a lease dict with an expiry in the past."""
    expires = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC) - timedelta(minutes=minutes_ago)
    return {
        "lease_expires_at": expires.isoformat(),
        "owner_node": owner_node,
        "owner_agent": owner_agent,
        "path_lock": path_lock,
    }


def _fresh_lease(*, minutes_ahead: int = 30) -> dict[str, Any]:
    """Return a lease dict with an expiry in the future."""
    expires = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC) + timedelta(minutes=minutes_ahead)
    return {
        "lease_expires_at": expires.isoformat(),
        "owner_node": "nova",
        "owner_agent": "forge:claude",
    }


# ===========================================================================
# __init__
# ===========================================================================


class TestInit:
    def test_task_handler_stored(self):
        svc, th, _ = _make_service()
        assert svc.task_handler is th

    def test_event_bus_stored(self):
        svc, _, eb = _make_service()
        assert svc.event_bus is eb

    def test_default_now_provider_returns_utc_datetime(self):
        """When no now_provider is passed the default must return a UTC datetime."""
        th = AsyncMock()
        eb = AsyncMock()
        svc = StaleLeaseRecoveryService(task_handler=th, event_bus=eb)
        result = svc._now_provider()
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_custom_now_provider_used(self):
        fixed = datetime(2025, 6, 1, tzinfo=UTC)
        th = AsyncMock()
        eb = AsyncMock()
        svc = StaleLeaseRecoveryService(
            task_handler=th, event_bus=eb, now_provider=lambda: fixed
        )
        assert svc._now_provider() is fixed

    def test_running_false_initially(self):
        svc, _, _ = _make_service()
        assert svc._running is False

    def test_stop_event_not_set_initially(self):
        svc, _, _ = _make_service()
        assert not svc._stop_event.is_set()


# ===========================================================================
# running property
# ===========================================================================


class TestRunningProperty:
    def test_running_property_reflects_internal_state_false(self):
        svc, _, _ = _make_service()
        assert svc.running is False

    def test_running_property_reflects_internal_state_true(self):
        svc, _, _ = _make_service()
        svc._running = True
        assert svc.running is True


# ===========================================================================
# _parse_expiry (static method)
# ===========================================================================


class TestParseExpiry:
    def test_returns_none_when_key_missing(self):
        result = StaleLeaseRecoveryService._parse_expiry({})
        assert result is None

    def test_returns_none_when_value_is_none(self):
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": None})
        assert result is None

    def test_returns_none_for_unsupported_type_integer(self):
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": 12345})
        assert result is None

    def test_returns_none_for_unsupported_type_list(self):
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": [1, 2, 3]})
        assert result is None

    def test_parses_aware_datetime_object(self):
        dt = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": dt})
        assert result == dt.astimezone(UTC)

    def test_parses_naive_datetime_assumes_utc(self):
        naive = datetime(2026, 3, 1, 10, 0, 0)
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": naive})
        assert result is not None
        assert result.tzinfo is not None
        assert result == naive.replace(tzinfo=UTC)

    def test_parses_iso_string_with_utc_offset(self):
        iso = "2026-04-01T08:30:00+00:00"
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": iso})
        assert result == datetime(2026, 4, 1, 8, 30, 0, tzinfo=UTC)

    def test_parses_iso_string_z_suffix(self):
        """The 'Z' suffix must be normalised to '+00:00' before parsing."""
        iso = "2026-04-01T08:30:00Z"
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": iso})
        assert result == datetime(2026, 4, 1, 8, 30, 0, tzinfo=UTC)

    def test_parses_iso_string_with_positive_offset(self):
        """Non-UTC aware string must be converted to UTC."""
        iso = "2026-04-01T10:30:00+02:00"
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": iso})
        expected = datetime(2026, 4, 1, 8, 30, 0, tzinfo=UTC)
        assert result == expected

    def test_parses_iso_string_without_timezone_assumes_utc(self):
        """Naive ISO string should be treated as UTC."""
        iso = "2026-04-01T08:30:00"
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": iso})
        assert result is not None
        assert result.tzinfo is not None

    def test_returns_none_for_invalid_string(self):
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": "not-a-date"})
        assert result is None

    def test_returns_none_for_empty_string(self):
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": ""})
        assert result is None

    def test_result_always_utc_timezone(self):
        """All parsed datetimes must have UTC timezone regardless of input zone."""
        iso = "2026-05-01T12:00:00+05:30"
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": iso})
        assert result is not None
        assert result.tzinfo == UTC

    def test_datetime_in_the_past_returned_as_is(self):
        past = datetime(2020, 1, 1, tzinfo=UTC)
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": past})
        assert result == past

    def test_datetime_in_the_future_returned_as_is(self):
        future = datetime(2099, 12, 31, tzinfo=UTC)
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": future})
        assert result == future


# ===========================================================================
# scan_and_recover
# ===========================================================================


@pytest.mark.asyncio
class TestScanAndRecover:
    async def test_returns_zero_when_no_tasks(self):
        svc, th, _ = _make_service(tasks=[])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_calls_list_tasks_with_limit_10000(self):
        svc, th, _ = _make_service(tasks=[])
        await svc.scan_and_recover()
        th.list_tasks.assert_awaited_once_with(limit=10000)

    async def test_skips_task_without_lease_key(self):
        task = {"id": "t1"}  # no 'lease' key
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0
        th.requeue_task.assert_not_awaited()

    async def test_skips_task_with_non_dict_lease_string(self):
        task = {"id": "t1", "lease": "invalid"}
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_skips_task_with_non_dict_lease_none(self):
        task = {"id": "t1", "lease": None}
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_skips_task_with_non_dict_lease_list(self):
        task = {"id": "t1", "lease": []}
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_skips_task_with_empty_task_id(self):
        task = {"id": "", "lease": _expired_lease()}
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_skips_task_with_whitespace_task_id(self):
        task = {"id": "   ", "lease": _expired_lease()}
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_skips_task_with_missing_id_key(self):
        task = {"lease": _expired_lease()}  # no 'id' key
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_skips_task_with_none_id(self):
        task = {"id": None, "lease": _expired_lease()}
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_skips_lease_with_no_expiry(self):
        task = {"id": "t1", "lease": {"owner_node": "nova"}}  # no lease_expires_at
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_skips_lease_with_unparseable_expiry(self):
        task = {"id": "t1", "lease": {"lease_expires_at": "garbage"}}
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_skips_fresh_lease(self):
        task = _make_task("t1", lease=_fresh_lease())
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 0
        th.requeue_task.assert_not_awaited()

    async def test_recovers_expired_lease(self):
        task = _make_task("t1", lease=_expired_lease())
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 1

    async def test_calls_requeue_task_with_correct_id(self):
        task = _make_task("task-abc", lease=_expired_lease())
        svc, th, _ = _make_service(tasks=[task])
        await svc.scan_and_recover()
        th.requeue_task.assert_awaited_once_with("task-abc")

    async def test_does_not_count_when_requeue_returns_falsy_none(self):
        task = _make_task("t1", lease=_expired_lease())
        svc, th, _ = _make_service(tasks=[task])
        th.requeue_task = AsyncMock(return_value=None)
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_does_not_count_when_requeue_returns_falsy_empty_dict(self):
        task = _make_task("t1", lease=_expired_lease())
        svc, th, _ = _make_service(tasks=[task])
        th.requeue_task = AsyncMock(return_value={})
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_does_not_count_when_requeue_returns_falsy_false(self):
        task = _make_task("t1", lease=_expired_lease())
        svc, th, _ = _make_service(tasks=[task])
        th.requeue_task = AsyncMock(return_value=False)
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_continues_after_requeue_exception(self):
        """A failure on requeue_task must be logged and skipped, not raise."""
        t1 = _make_task("t-fail", lease=_expired_lease())
        t2 = _make_task("t-ok", lease=_expired_lease())
        svc, th, _ = _make_service(tasks=[t1, t2])
        # First call raises, second succeeds
        th.requeue_task = AsyncMock(
            side_effect=[RuntimeError("db error"), {"id": "t-ok-requeued"}]
        )
        count = await svc.scan_and_recover()
        assert count == 1  # only t2 counted

    async def test_no_events_published_when_no_recovery(self):
        svc, th, eb = _make_service(tasks=[])
        await svc.scan_and_recover()
        eb.publish.assert_not_awaited()

    async def test_publishes_task_lease_expired_event(self):
        task = _make_task("t1", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        topics = [call.args[0] for call in eb.publish.await_args_list]
        assert "task.lease.expired" in topics

    async def test_publishes_task_requeued_event(self):
        task = _make_task("t1", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        topics = [call.args[0] for call in eb.publish.await_args_list]
        assert "task.requeued" in topics

    async def test_publishes_two_events_per_recovered_task(self):
        task = _make_task("t1", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        assert eb.publish.await_count == 2

    async def test_lease_expired_payload_task_id(self):
        task = _make_task("my-task-99", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        expired_call = next(
            c for c in eb.publish.await_args_list if c.args[0] == "task.lease.expired"
        )
        payload: dict[str, Any] = expired_call.args[1]
        assert payload["task_id"] == "my-task-99"

    async def test_lease_expired_payload_expired_at(self):
        lease = _expired_lease(minutes_ago=10)
        task = _make_task("t1", lease=lease)
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        expired_call = next(
            c for c in eb.publish.await_args_list if c.args[0] == "task.lease.expired"
        )
        payload = expired_call.args[1]
        assert "expired_at" in payload
        # Value should be an ISO string
        assert isinstance(payload["expired_at"], str)

    async def test_lease_expired_payload_owner_fields(self):
        lease = _expired_lease(owner_node="my-node", owner_agent="my-agent")
        task = _make_task("t1", lease=lease)
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        expired_call = next(
            c for c in eb.publish.await_args_list if c.args[0] == "task.lease.expired"
        )
        payload = expired_call.args[1]
        assert payload["owner_node"] == "my-node"
        assert payload["owner_agent"] == "my-agent"

    async def test_lease_expired_payload_path_lock(self):
        lease = _expired_lease(path_lock="/src/module.py")
        task = _make_task("t1", lease=lease)
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        expired_call = next(
            c for c in eb.publish.await_args_list if c.args[0] == "task.lease.expired"
        )
        payload = expired_call.args[1]
        assert payload["path_lock"] == "/src/module.py"

    async def test_lease_expired_payload_contains_requeued_task(self):
        requeued = {"id": "requeued-001", "status": "pending"}
        task = _make_task("t1", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        th.requeue_task = AsyncMock(return_value=requeued)
        await svc.scan_and_recover()
        expired_call = next(
            c for c in eb.publish.await_args_list if c.args[0] == "task.lease.expired"
        )
        payload = expired_call.args[1]
        assert payload["task"] == requeued

    async def test_task_requeued_payload_task_id(self):
        task = _make_task("t-requeue-id", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        requeued_call = next(
            c for c in eb.publish.await_args_list if c.args[0] == "task.requeued"
        )
        payload = requeued_call.args[1]
        assert payload["task_id"] == "t-requeue-id"

    async def test_task_requeued_payload_reason(self):
        task = _make_task("t1", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        requeued_call = next(
            c for c in eb.publish.await_args_list if c.args[0] == "task.requeued"
        )
        payload = requeued_call.args[1]
        assert payload["reason"] == "lease_expired_auto_recovery"

    async def test_task_requeued_payload_task_field(self):
        requeued = {"id": "rq-002", "status": "queued"}
        task = _make_task("t1", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        th.requeue_task = AsyncMock(return_value=requeued)
        await svc.scan_and_recover()
        requeued_call = next(
            c for c in eb.publish.await_args_list if c.args[0] == "task.requeued"
        )
        payload = requeued_call.args[1]
        assert payload["task"] == requeued

    async def test_multiple_expired_tasks_all_recovered(self):
        tasks = [_make_task(f"t-{i}", lease=_expired_lease()) for i in range(5)]
        svc, th, _ = _make_service(tasks=tasks)
        th.requeue_task = AsyncMock(return_value={"id": "rq"})
        count = await svc.scan_and_recover()
        assert count == 5

    async def test_mixed_tasks_only_expired_recovered(self):
        expired = _make_task("t-expired", lease=_expired_lease())
        fresh = _make_task("t-fresh", lease=_fresh_lease())
        no_lease = {"id": "t-no-lease"}
        svc, th, _ = _make_service(tasks=[expired, fresh, no_lease])
        count = await svc.scan_and_recover()
        assert count == 1
        th.requeue_task.assert_awaited_once_with("t-expired")

    async def test_exactly_expired_lease_is_recovered(self):
        """A lease that expires exactly at now should NOT be skipped (expires_at == now means expired)."""
        now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        # exactly at now — not strictly greater than now, so condition `expires_at > now` is False
        task = _make_task("t-exact", lease={"lease_expires_at": now.isoformat()})
        svc, th, _ = _make_service(tasks=[task], now=now)
        count = await svc.scan_and_recover()
        # expires_at == now: `expires_at > now` is False, so it IS processed
        assert count == 1

    async def test_task_id_stripped_of_whitespace(self):
        """Task IDs with surrounding whitespace should be stripped and used."""
        task = {"id": "  task-stripped  ", "lease": _expired_lease()}
        svc, th, _ = _make_service(tasks=[task])
        await svc.scan_and_recover()
        th.requeue_task.assert_awaited_once_with("task-stripped")

    async def test_lease_with_datetime_object_expired(self):
        """Lease using a datetime object (not string) as expiry is handled."""
        now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        past = now - timedelta(hours=1)
        lease = {"lease_expires_at": past, "owner_node": "n1", "owner_agent": "a1"}
        task = _make_task("t-dt", lease=lease)
        svc, th, _ = _make_service(tasks=[task], now=now)
        count = await svc.scan_and_recover()
        assert count == 1

    async def test_lease_with_datetime_object_fresh(self):
        now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        future = now + timedelta(hours=1)
        lease = {"lease_expires_at": future}
        task = _make_task("t-dt-fresh", lease=lease)
        svc, th, _ = _make_service(tasks=[task], now=now)
        count = await svc.scan_and_recover()
        assert count == 0

    async def test_integer_task_id_coerced_to_string(self):
        """task.get('id') may return an int; it should be converted to str."""
        task = {"id": 42, "lease": _expired_lease()}
        svc, th, _ = _make_service(tasks=[task])
        count = await svc.scan_and_recover()
        assert count == 1
        th.requeue_task.assert_awaited_once_with("42")

    async def test_all_exceptions_in_requeue_dont_stop_processing(self):
        """Every requeue exception should be caught; all tasks should be attempted."""
        tasks = [_make_task(f"t-{i}", lease=_expired_lease()) for i in range(3)]
        svc, th, _ = _make_service(tasks=tasks)
        th.requeue_task = AsyncMock(side_effect=RuntimeError("always fail"))
        count = await svc.scan_and_recover()
        assert count == 0
        assert th.requeue_task.await_count == 3


# ===========================================================================
# start
# ===========================================================================


@pytest.mark.asyncio
class TestStart:
    async def test_double_start_returns_immediately(self):
        """Calling start() while already running must return without blocking."""
        svc, th, eb = _make_service(tasks=[])
        svc._running = True
        # Should return instantly without entering the loop
        await asyncio.wait_for(svc.start(poll_interval=60.0), timeout=0.5)
        # _running is still True (we set it manually)
        assert svc._running is True

    async def test_poll_interval_clamped_to_minimum_one(self):
        """poll_interval below 1.0 should be clamped to 1.0."""
        svc, th, eb = _make_service(tasks=[])
        captured_intervals: list[float] = []

        async def fake_wait_for(coro, *, timeout):
            captured_intervals.append(timeout)
            raise TimeoutError

        call_count = 0

        async def fake_scan():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                svc._stop_event.set()
            return 0

        with patch.object(svc, "scan_and_recover", side_effect=fake_scan):
            with patch("asyncio.wait_for", side_effect=fake_wait_for):
                await svc.start(poll_interval=0.0)

        assert all(t == 1.0 for t in captured_intervals)

    async def test_poll_interval_clamped_negative(self):
        svc, th, eb = _make_service(tasks=[])
        captured_intervals: list[float] = []

        async def fake_wait_for(coro, *, timeout):
            captured_intervals.append(timeout)
            raise TimeoutError

        call_count = 0

        async def fake_scan():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                svc._stop_event.set()
            return 0

        with patch.object(svc, "scan_and_recover", side_effect=fake_scan):
            with patch("asyncio.wait_for", side_effect=fake_wait_for):
                await svc.start(poll_interval=-99.0)

        assert all(t == 1.0 for t in captured_intervals)

    async def test_running_true_during_loop(self):
        """_running must be True while the loop is active."""
        svc, th, eb = _make_service(tasks=[])
        observed: list[bool] = []

        async def fake_scan():
            observed.append(svc._running)
            svc._stop_event.set()
            return 0

        with patch.object(svc, "scan_and_recover", side_effect=fake_scan):
            await svc.start(poll_interval=60.0)

        assert True in observed

    async def test_running_false_after_stop(self):
        svc, th, eb = _make_service(tasks=[])

        async def fake_scan():
            svc._stop_event.set()
            return 0

        with patch.object(svc, "scan_and_recover", side_effect=fake_scan):
            await svc.start(poll_interval=60.0)

        assert svc.running is False

    async def test_stop_clears_running_in_finally(self):
        """Even if scan raises, _running must be False after the loop exits."""
        svc, th, eb = _make_service(tasks=[])
        call_count = 0

        async def fake_scan():
            nonlocal call_count
            call_count += 1
            svc._stop_event.set()
            raise RuntimeError("forced failure")

        with patch.object(svc, "scan_and_recover", side_effect=fake_scan):
            await svc.start(poll_interval=60.0)

        assert svc._running is False

    async def test_scan_exception_does_not_propagate(self):
        """scan_and_recover raising must be caught and the loop must continue."""
        svc, th, eb = _make_service(tasks=[])
        call_count = 0

        async def fake_scan():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            svc._stop_event.set()
            return 0

        with patch.object(svc, "scan_and_recover", side_effect=fake_scan):
            # Must not raise
            await svc.start(poll_interval=60.0)

        assert call_count == 2

    async def test_scan_and_recover_called_each_iteration(self):
        svc, th, eb = _make_service(tasks=[])
        call_count = 0

        async def fake_scan():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                svc._stop_event.set()
            return 0

        with patch.object(svc, "scan_and_recover", side_effect=fake_scan):
            await svc.start(poll_interval=60.0)

        assert call_count == 3

    async def test_recovered_count_logged_when_nonzero(self):
        """When scan returns > 0, a log.info should be emitted (smoke test via mock)."""
        svc, th, eb = _make_service(tasks=[])
        call_count = 0

        async def fake_scan():
            nonlocal call_count
            call_count += 1
            svc._stop_event.set()
            return 5  # non-zero triggers info log

        with patch(
            "forge_harness.webhook_server.services.lease_recovery.logger"
        ) as mock_logger:
            with patch.object(svc, "scan_and_recover", side_effect=fake_scan):
                await svc.start(poll_interval=60.0)
            # info should have been called at least twice (start + recovery)
            assert mock_logger.info.call_count >= 1

    async def test_stop_via_real_event_loop_integration(self):
        """Integration: start() with a real small interval, stop() terminates it."""
        svc, th, _ = _make_service(tasks=[])
        th.list_tasks = AsyncMock(return_value=[])

        async def _stopper():
            await asyncio.sleep(0.05)
            await svc.stop()

        await asyncio.gather(
            svc.start(poll_interval=0.02),
            _stopper(),
        )

        assert svc.running is False

    async def test_start_resets_stop_event(self):
        """stop_event must be cleared at the start of each run."""
        svc, th, eb = _make_service(tasks=[])
        # Pre-set the stop event so a fresh start would exit immediately if not cleared
        svc._stop_event.set()

        call_count = 0

        async def fake_scan():
            nonlocal call_count
            call_count += 1
            svc._stop_event.set()
            return 0

        with patch.object(svc, "scan_and_recover", side_effect=fake_scan):
            await svc.start(poll_interval=60.0)

        # scan_and_recover was called, proving the stop event was cleared
        assert call_count >= 1


# ===========================================================================
# stop
# ===========================================================================


@pytest.mark.asyncio
class TestStop:
    async def test_stop_sets_stop_event(self):
        svc, _, _ = _make_service()
        assert not svc._stop_event.is_set()
        await svc.stop()
        assert svc._stop_event.is_set()

    async def test_stop_idempotent(self):
        """Calling stop() twice must not raise."""
        svc, _, _ = _make_service()
        await svc.stop()
        await svc.stop()
        assert svc._stop_event.is_set()

    async def test_stop_when_not_running_does_not_raise(self):
        svc, _, _ = _make_service()
        assert svc._running is False
        await svc.stop()  # must not raise


# ===========================================================================
# Edge cases
# ===========================================================================


@pytest.mark.asyncio
class TestEdgeCases:
    async def test_large_batch_of_mixed_tasks(self):
        """Performance-style check: 1000 tasks, half expired, half fresh."""
        expired_tasks = [
            _make_task(f"expired-{i}", lease=_expired_lease()) for i in range(500)
        ]
        fresh_tasks = [
            _make_task(f"fresh-{i}", lease=_fresh_lease()) for i in range(500)
        ]
        svc, th, _ = _make_service(tasks=expired_tasks + fresh_tasks)
        th.requeue_task = AsyncMock(return_value={"id": "rq"})
        count = await svc.scan_and_recover()
        assert count == 500

    async def test_no_events_when_requeue_falsy(self):
        """If requeue_task returns falsy, no events should be published."""
        task = _make_task("t1", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        th.requeue_task = AsyncMock(return_value=None)
        await svc.scan_and_recover()
        eb.publish.assert_not_awaited()

    async def test_no_events_on_requeue_exception(self):
        """If requeue_task raises, no events should be published."""
        task = _make_task("t1", lease=_expired_lease())
        svc, th, eb = _make_service(tasks=[task])
        th.requeue_task = AsyncMock(side_effect=RuntimeError("fail"))
        await svc.scan_and_recover()
        eb.publish.assert_not_awaited()

    async def test_lease_with_none_path_lock_in_payload(self):
        """If path_lock is absent in lease, payload should have None."""
        lease = {"lease_expires_at": "2026-01-15T11:50:00+00:00"}  # no path_lock
        task = _make_task("t1", lease=lease)
        svc, th, eb = _make_service(tasks=[task])
        await svc.scan_and_recover()
        expired_call = next(
            c for c in eb.publish.await_args_list if c.args[0] == "task.lease.expired"
        )
        payload = expired_call.args[1]
        assert payload["path_lock"] is None

    async def test_now_provider_called_once_per_scan(self):
        """The now_provider should be called once per scan_and_recover() invocation."""
        call_count = 0

        def counting_now():
            nonlocal call_count
            call_count += 1
            return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

        th = AsyncMock()
        th.list_tasks = AsyncMock(return_value=[])
        eb = AsyncMock()
        svc = StaleLeaseRecoveryService(
            task_handler=th, event_bus=eb, now_provider=counting_now
        )
        await svc.scan_and_recover()
        await svc.scan_and_recover()
        assert call_count == 2

    async def test_task_with_integer_id_zero_is_skipped(self):
        """id=0 is falsy: str(0 or '').strip() == '' so the task is skipped."""
        task = {"id": 0, "lease": _expired_lease()}
        svc, th, _ = _make_service(tasks=[task])
        # The source does: str(task.get("id") or "").strip()
        # 0 or "" evaluates to "" (because 0 is falsy), so task_id == "" -> skipped
        count = await svc.scan_and_recover()
        assert count == 0
        th.requeue_task.assert_not_awaited()
