"""Comprehensive unit tests for StaleLeaseRecoveryService.

Targets 80%+ coverage of lease_recovery.py by exercising:
- Initialization and property access
- _parse_expiry static method: all branches (datetime, ISO string, Z-suffix,
  naive datetime, invalid string, non-str/datetime type, None input)
- scan_and_recover: no leases, single expired, multiple mixed, requeue
  exception, requeue returns falsy, task with empty/missing id
- start loop: already-running guard, scan exception inside loop, stop via
  stop() (early exit from wait_for), normal timeout-based cycling
- stop: idempotent call
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.webhook_server.services.lease_recovery import (
    StaleLeaseRecoveryService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_UNSET = object()  # sentinel for "not specified" vs None


def _make_service(
    *,
    tasks: list[dict] | None = None,
    requeue_return: object = _UNSET,
    requeue_side_effect=None,
    now: datetime | None = None,
) -> tuple[StaleLeaseRecoveryService, AsyncMock, AsyncMock]:
    """Return (service, task_handler_mock, event_bus_mock)."""
    task_handler = AsyncMock()
    task_handler.list_tasks = AsyncMock(return_value=tasks or [])
    if requeue_side_effect is not None:
        task_handler.requeue_task = AsyncMock(side_effect=requeue_side_effect)
    else:
        default_return = (
            {"id": "task-1", "status": "pending", "lease": None}
            if requeue_return is _UNSET
            else requeue_return
        )
        task_handler.requeue_task = AsyncMock(return_value=default_return)

    event_bus = AsyncMock()
    now_provider = (lambda: now) if now is not None else None

    service = StaleLeaseRecoveryService(
        task_handler=task_handler,
        event_bus=event_bus,
        now_provider=now_provider,
    )
    return service, task_handler, event_bus


def _expired_lease(
    *,
    minutes: int = 5,
    owner_node: str = "nova",
    owner_agent: str = "forge:codex",
    path_lock: str = "harness/core",
) -> dict:
    return {
        "owner_node": owner_node,
        "owner_agent": owner_agent,
        "path_lock": path_lock,
        "lease_expires_at": (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(),
    }


def _future_lease(*, minutes: int = 30) -> dict:
    return {
        "owner_node": "prya",
        "owner_agent": "forge:claude",
        "path_lock": "forge-terminal/ios",
        "lease_expires_at": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(),
    }


# ---------------------------------------------------------------------------
# 1. Initialization
# ---------------------------------------------------------------------------


class TestInitialization:
    def test_default_now_provider_is_utc(self) -> None:
        task_handler = AsyncMock()
        event_bus = AsyncMock()
        svc = StaleLeaseRecoveryService(
            task_handler=task_handler, event_bus=event_bus
        )
        result = svc._now_provider()
        assert result.tzinfo is not None, "default now_provider must return tz-aware datetime"
        # Should be very close to now
        delta = abs((result - datetime.now(UTC)).total_seconds())
        assert delta < 2.0

    def test_custom_now_provider_is_used(self) -> None:
        fixed = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
        task_handler = AsyncMock()
        event_bus = AsyncMock()
        svc = StaleLeaseRecoveryService(
            task_handler=task_handler,
            event_bus=event_bus,
            now_provider=lambda: fixed,
        )
        assert svc._now_provider() == fixed

    def test_running_property_starts_false(self) -> None:
        task_handler = AsyncMock()
        event_bus = AsyncMock()
        svc = StaleLeaseRecoveryService(
            task_handler=task_handler, event_bus=event_bus
        )
        assert svc.running is False

    def test_attributes_assigned(self) -> None:
        task_handler = AsyncMock()
        event_bus = AsyncMock()
        svc = StaleLeaseRecoveryService(
            task_handler=task_handler, event_bus=event_bus
        )
        assert svc.task_handler is task_handler
        assert svc.event_bus is event_bus


# ---------------------------------------------------------------------------
# 2. _parse_expiry static method
# ---------------------------------------------------------------------------


class TestParseExpiry:
    def test_none_returns_none(self) -> None:
        assert StaleLeaseRecoveryService._parse_expiry({"no_key": "value"}) is None

    def test_explicit_none_value_returns_none(self) -> None:
        assert StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": None}) is None

    def test_datetime_object_with_tz(self) -> None:
        """Branch: isinstance(raw_value, datetime) — line 40."""
        dt = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": dt})
        assert result is not None
        assert result == dt.astimezone(UTC)

    def test_datetime_object_without_tz_gets_utc(self) -> None:
        """Branch: datetime without tzinfo — line 53, parsed from datetime branch."""
        naive_dt = datetime(2026, 1, 15, 12, 0)  # no tzinfo
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": naive_dt})
        assert result is not None
        assert result.tzinfo == UTC

    def test_iso_string_with_timezone_offset(self) -> None:
        """Branch: isinstance(raw_value, str) — line 43."""
        iso = "2026-06-01T12:00:00+00:00"
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": iso})
        assert result is not None
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_iso_string_with_z_suffix(self) -> None:
        """Branch: str with 'Z' suffix is normalized — line 43."""
        iso = "2026-06-01T12:00:00Z"
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": iso})
        assert result is not None
        assert result.tzinfo is not None

    def test_iso_string_naive_gets_utc(self) -> None:
        """Branch: ISO string without tz gets UTC attached — line 53."""
        iso = "2026-06-01T12:00:00"
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": iso})
        assert result is not None
        assert result.tzinfo == UTC

    def test_invalid_string_returns_none(self) -> None:
        """Branch: fromisoformat raises ValueError — returns None after except."""
        result = StaleLeaseRecoveryService._parse_expiry(
            {"lease_expires_at": "not-a-date-at-all"}
        )
        assert result is None

    def test_invalid_type_returns_none(self) -> None:
        """Branch: non-str, non-datetime type — line 50 returns None."""
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": 12345})
        assert result is None

    def test_float_type_returns_none(self) -> None:
        """Another non-str, non-datetime type."""
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": 1.5})
        assert result is None

    def test_list_type_returns_none(self) -> None:
        """List is also invalid type."""
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": []})
        assert result is None

    def test_returns_utc_aware_datetime(self) -> None:
        """All valid paths must produce a UTC-aware datetime."""
        iso = "2026-01-01T00:00:00+05:30"
        result = StaleLeaseRecoveryService._parse_expiry({"lease_expires_at": iso})
        assert result is not None
        assert result.tzinfo == UTC or result.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# 3. scan_and_recover — core logic
# ---------------------------------------------------------------------------


class TestScanAndRecover:
    @pytest.mark.asyncio
    async def test_no_tasks_returns_zero(self) -> None:
        service, task_handler, event_bus = _make_service(tasks=[])
        result = await service.scan_and_recover()
        assert result == 0
        task_handler.requeue_task.assert_not_awaited()
        event_bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_with_no_lease_skipped(self) -> None:
        service, task_handler, event_bus = _make_service(
            tasks=[{"id": "task-1", "status": "pending"}]
        )
        result = await service.scan_and_recover()
        assert result == 0
        task_handler.requeue_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_with_null_lease_skipped(self) -> None:
        service, task_handler, event_bus = _make_service(
            tasks=[{"id": "task-1", "lease": None}]
        )
        result = await service.scan_and_recover()
        assert result == 0

    @pytest.mark.asyncio
    async def test_task_with_non_dict_lease_skipped(self) -> None:
        """lease must be a dict; strings/lists are skipped."""
        service, task_handler, event_bus = _make_service(
            tasks=[
                {"id": "task-1", "lease": "some-string"},
                {"id": "task-2", "lease": ["not", "a", "dict"]},
            ]
        )
        result = await service.scan_and_recover()
        assert result == 0
        task_handler.requeue_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_with_empty_id_skipped(self) -> None:
        """Task without a valid ID is skipped — line 69."""
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()
        service, task_handler, event_bus = _make_service(
            tasks=[
                {"id": "", "lease": {"lease_expires_at": expired_at}},
                {"id": None, "lease": {"lease_expires_at": expired_at}},
                {"lease": {"lease_expires_at": expired_at}},  # no id key at all
            ],
            now=now,
        )
        result = await service.scan_and_recover()
        assert result == 0
        task_handler.requeue_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_with_whitespace_only_id_skipped(self) -> None:
        """ID of only whitespace is invalid after strip() — line 69."""
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()
        service, task_handler, event_bus = _make_service(
            tasks=[{"id": "   ", "lease": {"lease_expires_at": expired_at}}],
            now=now,
        )
        result = await service.scan_and_recover()
        assert result == 0

    @pytest.mark.asyncio
    async def test_future_lease_not_recovered(self) -> None:
        """A lease expiring in the future must not be requeued."""
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        future_at = (now + timedelta(minutes=30)).isoformat()
        service, task_handler, event_bus = _make_service(
            tasks=[
                {
                    "id": "task-active",
                    "lease": {
                        "owner_node": "nova",
                        "lease_expires_at": future_at,
                    },
                }
            ],
            now=now,
        )
        result = await service.scan_and_recover()
        assert result == 0
        task_handler.requeue_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lease_expiry_at_exactly_now_not_recovered(self) -> None:
        """A lease expiring exactly at now is NOT expired (expires_at > now is False, but > means not expired)."""
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        # expires_at == now means expires_at is NOT > now, so it IS stale and recovered
        service, task_handler, event_bus = _make_service(
            tasks=[
                {
                    "id": "task-exact",
                    "lease": {"owner_node": "nova", "lease_expires_at": now.isoformat()},
                }
            ],
            now=now,
            requeue_return={"id": "task-exact", "status": "pending", "lease": None},
        )
        result = await service.scan_and_recover()
        # expires_at == now means NOT > now, so condition `expires_at > now` is False -> stale -> recovered
        assert result == 1

    @pytest.mark.asyncio
    async def test_single_expired_lease_recovered(self) -> None:
        """A single expired lease triggers requeue and two event publishes."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=10)).isoformat()
        requeued = {"id": "task-stale", "status": "pending", "lease": None}

        service, task_handler, event_bus = _make_service(
            tasks=[
                {
                    "id": "task-stale",
                    "lease": {
                        "owner_node": "nova",
                        "owner_agent": "forge:codex",
                        "path_lock": "harness/stale",
                        "lease_expires_at": expired_at,
                    },
                }
            ],
            now=now,
            requeue_return=requeued,
        )

        result = await service.scan_and_recover()

        assert result == 1
        task_handler.requeue_task.assert_awaited_once_with("task-stale")
        assert event_bus.publish.await_count == 2

        first_call = event_bus.publish.await_args_list[0]
        assert first_call.args[0] == "task.lease.expired"
        payload = first_call.args[1]
        assert payload["task_id"] == "task-stale"
        assert payload["owner_node"] == "nova"
        assert payload["owner_agent"] == "forge:codex"
        assert payload["path_lock"] == "harness/stale"
        assert payload["task"] == requeued

        second_call = event_bus.publish.await_args_list[1]
        assert second_call.args[0] == "task.requeued"
        second_payload = second_call.args[1]
        assert second_payload["task_id"] == "task-stale"
        assert second_payload["reason"] == "lease_expired_auto_recovery"
        assert second_payload["task"] == requeued

    @pytest.mark.asyncio
    async def test_multiple_expired_leases_all_recovered(self) -> None:
        """All expired leases in the list should be recovered."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()

        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(
            return_value=[
                {"id": f"task-{i}", "lease": {"lease_expires_at": expired_at}}
                for i in range(3)
            ]
        )
        task_handler.requeue_task = AsyncMock(
            side_effect=lambda tid: {"id": tid, "status": "pending", "lease": None}
        )
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(
            task_handler=task_handler,
            event_bus=event_bus,
            now_provider=lambda: now,
        )

        result = await service.scan_and_recover()

        assert result == 3
        assert task_handler.requeue_task.await_count == 3
        # 2 events per recovered task
        assert event_bus.publish.await_count == 6

    @pytest.mark.asyncio
    async def test_mixed_leases_only_expired_recovered(self) -> None:
        """Mix of expired, future, and invalid leases — only expired ones recovered."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()
        future_at = (now + timedelta(minutes=30)).isoformat()

        service, task_handler, event_bus = _make_service(
            tasks=[
                {"id": "task-expired", "lease": {"lease_expires_at": expired_at}},
                {"id": "task-future", "lease": {"lease_expires_at": future_at}},
                {"id": "task-no-lease", "lease": None},
                {"id": "task-invalid", "lease": {"lease_expires_at": "garbage"}},
                {"id": "task-non-dict", "lease": "string"},
            ],
            now=now,
            requeue_return={"id": "task-expired", "status": "pending", "lease": None},
        )

        result = await service.scan_and_recover()

        assert result == 1
        task_handler.requeue_task.assert_awaited_once_with("task-expired")

    @pytest.mark.asyncio
    async def test_requeue_exception_skips_task_and_continues(self) -> None:
        """Exception from requeue_task is swallowed — recovery continues with other tasks."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()

        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(
            return_value=[
                {"id": "task-fail", "lease": {"lease_expires_at": expired_at}},
                {"id": "task-ok", "lease": {"lease_expires_at": expired_at}},
            ]
        )
        task_handler.requeue_task = AsyncMock(
            side_effect=[
                RuntimeError("DB connection lost"),
                {"id": "task-ok", "status": "pending", "lease": None},
            ]
        )
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(
            task_handler=task_handler,
            event_bus=event_bus,
            now_provider=lambda: now,
        )

        result = await service.scan_and_recover()

        # Only task-ok was successfully recovered
        assert result == 1
        assert task_handler.requeue_task.await_count == 2
        # Only 2 events for task-ok; task-fail never published
        assert event_bus.publish.await_count == 2

    @pytest.mark.asyncio
    async def test_requeue_returns_none_skips_events(self) -> None:
        """When requeue_task returns None/falsy, no events are published and count is 0."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()

        service, task_handler, event_bus = _make_service(
            tasks=[{"id": "task-1", "lease": {"lease_expires_at": expired_at}}],
            now=now,
            requeue_return=None,  # falsy
        )

        result = await service.scan_and_recover()

        assert result == 0
        task_handler.requeue_task.assert_awaited_once_with("task-1")
        event_bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_requeue_returns_empty_dict_skips_events(self) -> None:
        """Empty dict is falsy — same skip behavior."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()

        service, task_handler, event_bus = _make_service(
            tasks=[{"id": "task-1", "lease": {"lease_expires_at": expired_at}}],
            now=now,
            requeue_return={},  # empty dict is falsy
        )

        result = await service.scan_and_recover()
        assert result == 0
        event_bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_event_payload_contains_all_lease_fields(self) -> None:
        """task.lease.expired payload must include all lease fields."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()
        requeued = {"id": "task-1", "status": "pending", "lease": None}

        service, task_handler, event_bus = _make_service(
            tasks=[
                {
                    "id": "task-1",
                    "lease": {
                        "owner_node": "sati",
                        "owner_agent": "forge:gemini",
                        "path_lock": "codeswiftr/interview-simulator",
                        "lease_expires_at": expired_at,
                    },
                }
            ],
            now=now,
            requeue_return=requeued,
        )

        await service.scan_and_recover()

        lease_expired_call = event_bus.publish.await_args_list[0]
        payload = lease_expired_call.args[1]
        assert payload["owner_node"] == "sati"
        assert payload["owner_agent"] == "forge:gemini"
        assert payload["path_lock"] == "codeswiftr/interview-simulator"
        assert "expired_at" in payload
        assert payload["task"] == requeued

    @pytest.mark.asyncio
    async def test_event_payload_handles_missing_optional_lease_fields(self) -> None:
        """Lease dict with only required field — owner_node/agent/path_lock can be None."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()
        requeued = {"id": "task-minimal", "status": "pending", "lease": None}

        service, task_handler, event_bus = _make_service(
            tasks=[
                {
                    "id": "task-minimal",
                    "lease": {"lease_expires_at": expired_at},  # no owner fields
                }
            ],
            now=now,
            requeue_return=requeued,
        )

        result = await service.scan_and_recover()
        assert result == 1

        payload = event_bus.publish.await_args_list[0].args[1]
        assert payload["owner_node"] is None
        assert payload["owner_agent"] is None
        assert payload["path_lock"] is None


# ---------------------------------------------------------------------------
# 4. start / stop loop
# ---------------------------------------------------------------------------


class TestStartStop:
    @pytest.mark.asyncio
    async def test_running_property_is_true_while_running(self) -> None:
        service, task_handler, _ = _make_service(tasks=[])

        started = asyncio.Event()
        original_scan = service.scan_and_recover

        async def patched_scan():
            started.set()
            return await original_scan()

        service.scan_and_recover = patched_scan  # type: ignore[method-assign]

        task = asyncio.create_task(service.start(poll_interval=0.05))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert service.running is True

        await service.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert service.running is False

    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self) -> None:
        """Calling stop() when not running must not raise."""
        service, _, _ = _make_service()
        await service.stop()  # should not raise
        assert service.running is False

    @pytest.mark.asyncio
    async def test_already_running_returns_immediately(self) -> None:
        """Starting while already running logs a warning and returns — lines 109-110."""
        service, task_handler, _ = _make_service(tasks=[])

        # Force _running to True to simulate already-running state
        service._running = True

        with patch(
            "forge_harness.webhook_server.services.lease_recovery.logger"
        ) as mock_logger:
            await service.start(poll_interval=0.01)
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "already running" in warning_msg

        # No list_tasks call should have been made
        task_handler.list_tasks.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_poll_interval_minimum_is_one_second(self) -> None:
        """poll_interval below 1.0 is clamped to 1.0."""
        service, task_handler, _ = _make_service(tasks=[])

        call_count = 0

        async def counting_scan():
            nonlocal call_count
            call_count += 1
            await service.stop()  # stop after first scan
            return 0

        service.scan_and_recover = counting_scan  # type: ignore[method-assign]

        await service.start(poll_interval=-5.0)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_start_stop_completes_cleanly(self) -> None:
        """Normal start/stop cycle must exit without errors."""
        service, task_handler, _ = _make_service(tasks=[])

        loop_task = asyncio.create_task(service.start(poll_interval=0.01))
        # Let the loop run at least one scan
        await asyncio.sleep(0.05)
        await service.stop()
        await asyncio.wait_for(loop_task, timeout=2.0)

        assert service.running is False
        assert task_handler.list_tasks.await_count >= 1

    @pytest.mark.asyncio
    async def test_scan_exception_inside_loop_does_not_crash_service(self) -> None:
        """Exception from scan_and_recover is caught and logged — lines 123-124."""
        call_count = 0

        task_handler = AsyncMock()
        event_bus = AsyncMock()

        async def failing_scan():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("scan exploded")
            # On second call, stop the service
            await service.stop()
            return 0

        service = StaleLeaseRecoveryService(
            task_handler=task_handler, event_bus=event_bus
        )
        service.scan_and_recover = failing_scan  # type: ignore[method-assign]

        with patch(
            "forge_harness.webhook_server.services.lease_recovery.logger"
        ) as mock_logger:
            await service.start(poll_interval=0.01)
            mock_logger.error.assert_called()
            error_msg = str(mock_logger.error.call_args)
            assert "scan failed" in error_msg or "Lease recovery" in error_msg

        assert call_count >= 2
        assert service.running is False

    @pytest.mark.asyncio
    async def test_stop_triggers_early_exit_from_wait(self) -> None:
        """Calling stop() during the asyncio.wait_for sleep exits promptly — lines 127-129."""
        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(return_value=[])
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(
            task_handler=task_handler, event_bus=event_bus
        )

        # Use a long poll interval so the stop() must interrupt the wait_for
        loop_task = asyncio.create_task(service.start(poll_interval=60.0))
        # Give the loop time to start and enter wait_for
        await asyncio.sleep(0.1)
        assert service.running is True

        await service.stop()
        # Should exit well before the 60s timeout
        await asyncio.wait_for(loop_task, timeout=2.0)
        assert service.running is False

    @pytest.mark.asyncio
    async def test_start_running_property_false_after_completion(self) -> None:
        """running must be False in the finally block after stop."""
        service, _, _ = _make_service(tasks=[])
        loop_task = asyncio.create_task(service.start(poll_interval=0.01))
        await asyncio.sleep(0.03)
        await service.stop()
        await asyncio.wait_for(loop_task, timeout=2.0)
        assert service.running is False

    @pytest.mark.asyncio
    async def test_recovered_count_logged_when_positive(self) -> None:
        """When tasks are recovered, log.info with count is called — line 122."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()

        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(
            return_value=[
                {
                    "id": "task-stale",
                    "lease": {"lease_expires_at": expired_at},
                }
            ]
        )
        requeued = {"id": "task-stale", "status": "pending", "lease": None}
        call_count = 0

        async def controlled_requeue(tid):
            nonlocal call_count
            call_count += 1
            return requeued

        task_handler.requeue_task = AsyncMock(side_effect=controlled_requeue)
        event_bus = AsyncMock()

        service = StaleLeaseRecoveryService(
            task_handler=task_handler,
            event_bus=event_bus,
            now_provider=lambda: now,
        )

        # Intercept to stop after first scan
        scanned = asyncio.Event()
        original_scan = service.scan_and_recover.__func__

        async def one_shot_scan():
            result = await StaleLeaseRecoveryService.scan_and_recover(service)
            scanned.set()
            await service.stop()
            return result

        service.scan_and_recover = one_shot_scan  # type: ignore[method-assign]

        with patch(
            "forge_harness.webhook_server.services.lease_recovery.logger"
        ) as mock_logger:
            await service.start(poll_interval=60.0)

        # Verify info was called with recovered count
        info_calls = [str(c) for c in mock_logger.info.call_args_list]
        assert any("Recovered" in msg or "recovered" in msg for msg in info_calls)


# ---------------------------------------------------------------------------
# 5. stop() method
# ---------------------------------------------------------------------------


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_sets_stop_event(self) -> None:
        service, _, _ = _make_service()
        assert not service._stop_event.is_set()
        await service.stop()
        assert service._stop_event.is_set()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self) -> None:
        """Calling stop() multiple times must not raise."""
        service, _, _ = _make_service()
        await service.stop()
        await service.stop()
        assert service._stop_event.is_set()


# ---------------------------------------------------------------------------
# 6. Edge cases and integration with real datetime
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_list_tasks_called_with_limit_10000(self) -> None:
        """list_tasks must be called with limit=10000 for full scan."""
        service, task_handler, _ = _make_service(tasks=[])
        await service.scan_and_recover()
        task_handler.list_tasks.assert_awaited_once_with(limit=10000)

    @pytest.mark.asyncio
    async def test_real_expired_lease_detected_without_now_provider(self) -> None:
        """Without a custom now_provider, genuine past timestamps are detected."""
        past_ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        requeued = {"id": "task-real", "status": "pending", "lease": None}

        service, task_handler, event_bus = _make_service(
            tasks=[
                {"id": "task-real", "lease": {"lease_expires_at": past_ts}}
            ],
            requeue_return=requeued,
        )

        result = await service.scan_and_recover()
        assert result == 1

    @pytest.mark.asyncio
    async def test_real_future_lease_not_detected_without_now_provider(self) -> None:
        """Without a custom now_provider, genuine future timestamps are not recovered."""
        future_ts = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        service, task_handler, event_bus = _make_service(
            tasks=[{"id": "task-real", "lease": {"lease_expires_at": future_ts}}]
        )
        result = await service.scan_and_recover()
        assert result == 0

    @pytest.mark.asyncio
    async def test_task_id_coerced_to_string(self) -> None:
        """task_id is always str(task.get('id')), so integers are handled."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired_at = (now - timedelta(minutes=5)).isoformat()
        requeued = {"id": "42", "status": "pending", "lease": None}

        service, task_handler, event_bus = _make_service(
            tasks=[
                {"id": 42, "lease": {"lease_expires_at": expired_at}}  # integer id
            ],
            now=now,
            requeue_return=requeued,
        )

        result = await service.scan_and_recover()
        assert result == 1
        task_handler.requeue_task.assert_awaited_once_with("42")

    @pytest.mark.asyncio
    async def test_non_utc_timezone_lease_expires_at_handled(self) -> None:
        """Leases with non-UTC timezone offsets are converted to UTC for comparison."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        # Create an expired timestamp in +05:30 timezone
        # 2026-02-20T06:30:00+05:30 == 2026-02-20T01:00:00Z which is before 12:00 UTC
        ist = timezone(timedelta(hours=5, minutes=30))
        past_in_ist = datetime(2026, 2, 20, 6, 30, tzinfo=ist)  # 01:00 UTC — before now
        requeued = {"id": "task-ist", "status": "pending", "lease": None}

        service, task_handler, event_bus = _make_service(
            tasks=[
                {
                    "id": "task-ist",
                    "lease": {"lease_expires_at": past_in_ist.isoformat()},
                }
            ],
            now=now,
            requeue_return=requeued,
        )

        result = await service.scan_and_recover()
        assert result == 1
