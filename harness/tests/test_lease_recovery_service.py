"""Tests for stale lease auto-recovery service."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from forge_harness.webhook_server.services.lease_recovery import (
    StaleLeaseRecoveryService,
)

# ---------------------------------------------------------------------------
# _parse_expiry – private static method edge cases
# ---------------------------------------------------------------------------


class TestParseExpiry:
    """Unit tests for StaleLeaseRecoveryService._parse_expiry."""

    def _parse(self, lease: dict) -> datetime | None:
        return StaleLeaseRecoveryService._parse_expiry(lease)

    # --- None / missing ---

    def test_none_value_returns_none(self):
        """Missing lease_expires_at key returns None."""
        assert self._parse({}) is None

    def test_explicit_none_value_returns_none(self):
        """Explicit None value returns None."""
        assert self._parse({"lease_expires_at": None}) is None

    # --- datetime branch (line 43) ---

    def test_datetime_object_aware_returned_as_utc(self):
        """An aware datetime object is converted to UTC and returned."""
        dt = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        result = self._parse({"lease_expires_at": dt})
        assert result == dt.astimezone(UTC)

    def test_datetime_object_naive_gets_utc_tzinfo(self):
        """A naive datetime is treated as UTC (line 54)."""
        naive_dt = datetime(2026, 1, 15, 10, 0)
        result = self._parse({"lease_expires_at": naive_dt})
        assert result is not None
        assert result.tzinfo is not None
        # Should equal the same moment in UTC
        assert result.replace(tzinfo=None) == naive_dt

    def test_datetime_with_non_utc_timezone_converted(self):
        """A datetime with a non-UTC timezone is still returned as UTC."""
        eastern = timezone(timedelta(hours=-5))
        dt_eastern = datetime(2026, 1, 15, 10, 0, tzinfo=eastern)
        result = self._parse({"lease_expires_at": dt_eastern})
        assert result is not None
        # UTC equivalent is 15:00
        assert result.hour == 15

    # --- string branch (lines 45-48) ---

    def test_iso_string_parsed_correctly(self):
        """An ISO 8601 string is parsed and returned as UTC datetime."""
        iso_str = "2026-02-20T12:00:00+00:00"
        result = self._parse({"lease_expires_at": iso_str})
        assert result is not None
        assert result.year == 2026
        assert result.month == 2

    def test_z_suffix_replaced_correctly(self):
        """Strings ending with 'Z' are handled via .replace('Z', '+00:00')."""
        iso_str = "2026-03-01T09:30:00Z"
        result = self._parse({"lease_expires_at": iso_str})
        assert result is not None
        assert result.tzinfo is not None

    def test_invalid_string_returns_none(self):
        """An un-parseable string returns None (line 49 – ValueError path)."""
        result = self._parse({"lease_expires_at": "not-a-date"})
        assert result is None

    def test_empty_string_returns_none(self):
        """An empty string is not a valid ISO date and returns None."""
        result = self._parse({"lease_expires_at": ""})
        assert result is None

    # --- unknown type branch (line 51) ---

    def test_integer_value_returns_none(self):
        """An integer timestamp is not handled and returns None (line 51)."""
        result = self._parse({"lease_expires_at": 1708430400})
        assert result is None

    def test_list_value_returns_none(self):
        """A list value returns None."""
        result = self._parse({"lease_expires_at": [2026, 1, 1]})
        assert result is None

    # --- naive string (no tz info) -> get utc tzinfo applied (line 54) ---

    def test_naive_iso_string_gets_utc(self):
        """ISO string without timezone info is treated as UTC."""
        iso_str = "2026-02-20T12:00:00"
        result = self._parse({"lease_expires_at": iso_str})
        assert result is not None
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# scan_and_recover – core logic
# ---------------------------------------------------------------------------


class TestScanAndRecover:
    """Tests for StaleLeaseRecoveryService.scan_and_recover."""

    def _make_service(self, tasks, requeue_return=None, now=None):
        now = now or datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(return_value=tasks)
        task_handler.requeue_task = AsyncMock(return_value=requeue_return)
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(
            task_handler=task_handler,
            event_bus=event_bus,
            now_provider=lambda: now,
        )
        return service, task_handler, event_bus

    @pytest.mark.asyncio
    async def test_requeues_expired_lease(self):
        """Expired lease triggers requeue and two event publications."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=5)).isoformat()
        tasks = [
            {
                "id": "task-1",
                "lease": {
                    "owner_node": "nova",
                    "owner_agent": "forge:codex",
                    "path_lock": "harness/",
                    "lease_expires_at": expired,
                },
            }
        ]
        requeued = {"id": "task-1", "status": "pending", "lease": None}
        service, task_handler, event_bus = self._make_service(tasks, requeued, now)

        count = await service.scan_and_recover()

        assert count == 1
        task_handler.requeue_task.assert_awaited_once_with("task-1")
        assert event_bus.publish.await_count == 2

    @pytest.mark.asyncio
    async def test_skips_task_without_lease_dict(self):
        """Tasks whose lease is not a dict are skipped entirely."""
        tasks = [
            {"id": "t1", "lease": None},
            {"id": "t2", "lease": "string-not-dict"},
            {"id": "t3"},
        ]
        service, task_handler, event_bus = self._make_service(tasks)

        count = await service.scan_and_recover()

        assert count == 0
        task_handler.requeue_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_task_with_empty_id(self):
        """Tasks where id is empty string or None are skipped (line 70)."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        tasks = [
            {"id": "", "lease": {"lease_expires_at": expired}},
            {"id": None, "lease": {"lease_expires_at": expired}},
            {"lease": {"lease_expires_at": expired}},  # no 'id' key at all
        ]
        service, task_handler, event_bus = self._make_service(tasks, now=now)

        count = await service.scan_and_recover()

        assert count == 0
        task_handler.requeue_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_task_with_unparseable_expiry(self):
        """Tasks with invalid expiry strings are skipped."""
        tasks = [
            {"id": "t1", "lease": {"lease_expires_at": "garbage"}},
        ]
        service, task_handler, event_bus = self._make_service(tasks)

        count = await service.scan_and_recover()
        assert count == 0

    @pytest.mark.asyncio
    async def test_skips_future_expiry(self):
        """Tasks whose lease has not yet expired are skipped."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        future = (now + timedelta(hours=1)).isoformat()
        tasks = [{"id": "t1", "lease": {"lease_expires_at": future}}]
        service, task_handler, event_bus = self._make_service(tasks, now=now)

        count = await service.scan_and_recover()
        assert count == 0

    @pytest.mark.asyncio
    async def test_requeue_exception_continues_to_next_task(self):
        """If requeue_task raises, the exception is caught and iteration continues (lines 78-80)."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        tasks = [
            {"id": "fail-task", "lease": {"lease_expires_at": expired}},
            {"id": "ok-task", "lease": {"lease_expires_at": expired}},
        ]
        ok_result = {"id": "ok-task", "status": "pending"}

        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(return_value=tasks)
        task_handler.requeue_task = AsyncMock(
            side_effect=[RuntimeError("requeue failed"), ok_result]
        )
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(
            task_handler=task_handler,
            event_bus=event_bus,
            now_provider=lambda: now,
        )

        count = await service.scan_and_recover()

        # The failing task is skipped; the OK task is recovered
        assert count == 1
        assert task_handler.requeue_task.await_count == 2

    @pytest.mark.asyncio
    async def test_falsy_requeue_result_not_counted(self):
        """If requeue_task returns None or falsy, recovered count stays at 0 (line 83)."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        tasks = [{"id": "task-x", "lease": {"lease_expires_at": expired}}]
        service, task_handler, event_bus = self._make_service(tasks, None, now)

        count = await service.scan_and_recover()

        assert count == 0
        task_handler.requeue_task.assert_awaited_once_with("task-x")
        event_bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_event_payload_contains_lease_metadata(self):
        """Published event payload includes owner_node, owner_agent, path_lock."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=2)).isoformat()
        tasks = [
            {
                "id": "t-meta",
                "lease": {
                    "owner_node": "prya",
                    "owner_agent": "forge:ralph",
                    "path_lock": "/some/path",
                    "lease_expires_at": expired,
                },
            }
        ]
        requeued = {"id": "t-meta", "status": "pending"}
        service, task_handler, event_bus = self._make_service(tasks, requeued, now)

        await service.scan_and_recover()

        first_call = event_bus.publish.await_args_list[0]
        payload = first_call.args[1]
        assert payload["owner_node"] == "prya"
        assert payload["owner_agent"] == "forge:ralph"
        assert payload["path_lock"] == "/some/path"
        assert payload["task_id"] == "t-meta"

    @pytest.mark.asyncio
    async def test_multiple_expired_tasks_recovered(self):
        """Multiple expired tasks are all recovered."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        tasks = [{"id": f"t{i}", "lease": {"lease_expires_at": expired}} for i in range(4)]
        requeued = {"id": "tx", "status": "pending"}
        service, task_handler, event_bus = self._make_service(tasks, requeued, now)

        count = await service.scan_and_recover()

        assert count == 4
        assert event_bus.publish.await_count == 8  # 2 events per task

    @pytest.mark.asyncio
    async def test_empty_task_list_returns_zero(self):
        """An empty task list results in zero recoveries."""
        service, _, _ = self._make_service([])
        count = await service.scan_and_recover()
        assert count == 0


# ---------------------------------------------------------------------------
# running property
# ---------------------------------------------------------------------------


class TestRunningProperty:
    """Tests for the running property."""

    def test_running_is_false_initially(self):
        service = StaleLeaseRecoveryService(task_handler=AsyncMock(), event_bus=AsyncMock())
        assert service.running is False


# ---------------------------------------------------------------------------
# start / stop loop
# ---------------------------------------------------------------------------


class TestStartStop:
    """Tests for the start/stop loop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_and_stop_clean(self):
        """start() sets running=True, stop() sets it back to False."""
        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(return_value=[])
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(task_handler=task_handler, event_bus=event_bus)

        run_task = asyncio.create_task(service.start(poll_interval=0.01))
        await asyncio.sleep(0)  # yield so loop starts
        assert service.running is True

        await service.stop()
        await asyncio.wait_for(run_task, timeout=2.0)
        assert service.running is False

    @pytest.mark.asyncio
    async def test_start_twice_logs_warning_and_returns(self):
        """Calling start() while already running logs and returns immediately (lines 110-111)."""
        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(return_value=[])
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(task_handler=task_handler, event_bus=event_bus)

        run_task = asyncio.create_task(service.start(poll_interval=0.01))
        await asyncio.sleep(0)

        # Second call should return without error
        with patch("forge_harness.webhook_server.services.lease_recovery.logger") as mock_logger:
            await service.start(poll_interval=0.01)
            mock_logger.warning.assert_called_once()
            assert "already running" in mock_logger.warning.call_args[0][0]

        await service.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_poll_interval_minimum_enforced(self):
        """Poll interval below 1.0 is clamped to 1.0."""
        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(return_value=[])
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(task_handler=task_handler, event_bus=event_bus)

        with patch("forge_harness.webhook_server.services.lease_recovery.logger") as mock_logger:
            run_task = asyncio.create_task(service.start(poll_interval=0.001))
            await asyncio.sleep(0)
            await service.stop()
            await asyncio.wait_for(run_task, timeout=2.0)

        # The info log should mention 1.0 seconds (clamped)
        info_calls = [str(c) for c in mock_logger.info.call_args_list]
        assert any("1.0" in c for c in info_calls)

    @pytest.mark.asyncio
    async def test_scan_exception_logged_and_continues(self):
        """If scan_and_recover raises, the error is logged and the loop continues (lines 124-125, 129-130)."""
        call_count = 0

        async def flaky_list_tasks(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient DB error")
            return []

        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(side_effect=flaky_list_tasks)
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(task_handler=task_handler, event_bus=event_bus)

        with patch("forge_harness.webhook_server.services.lease_recovery.logger") as mock_logger:
            run_task = asyncio.create_task(service.start(poll_interval=0.01))
            # Let at least 2 iterations run
            await asyncio.sleep(0.05)
            await service.stop()
            await asyncio.wait_for(run_task, timeout=2.0)

        # Error should have been logged for the first failing iteration
        mock_logger.error.assert_called()
        error_msg = mock_logger.error.call_args[0][0]
        assert "Lease recovery scan failed" in error_msg

    @pytest.mark.asyncio
    async def test_recovered_tasks_logged(self):
        """When tasks are recovered, an info log is emitted (lines 123-125)."""
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        expired = (now - timedelta(minutes=1)).isoformat()
        call_count = 0

        async def list_tasks(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"id": "t1", "lease": {"lease_expires_at": expired}}]
            return []

        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(side_effect=list_tasks)
        task_handler.requeue_task = AsyncMock(return_value={"id": "t1", "status": "pending"})
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(
            task_handler=task_handler,
            event_bus=event_bus,
            now_provider=lambda: now,
        )

        with patch("forge_harness.webhook_server.services.lease_recovery.logger") as mock_logger:
            run_task = asyncio.create_task(service.start(poll_interval=0.01))
            await asyncio.sleep(0.05)
            await service.stop()
            await asyncio.wait_for(run_task, timeout=2.0)

        # Info log should mention "Recovered"
        info_calls = [str(c) for c in mock_logger.info.call_args_list]
        assert any("Recovered" in c for c in info_calls)

    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self):
        """Calling stop before start should not raise."""
        service = StaleLeaseRecoveryService(task_handler=AsyncMock(), event_bus=AsyncMock())
        await service.stop()  # Should not raise
        assert service.running is False

    @pytest.mark.asyncio
    async def test_scan_executes_at_least_once_during_run(self):
        """At least one scan occurs during a start/stop cycle."""
        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(return_value=[])
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(task_handler=task_handler, event_bus=event_bus)

        run_task = asyncio.create_task(service.start(poll_interval=0.01))
        await asyncio.sleep(0.02)
        await service.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

        assert task_handler.list_tasks.await_count >= 1

    @pytest.mark.asyncio
    async def test_timeout_error_triggers_continue_for_next_poll(self):
        """The TimeoutError branch (lines 129-130) is exercised when the poll interval expires.

        The loop uses asyncio.wait_for with a 1-second timeout. We run the service
        for 1.1 seconds so that one full timeout cycle fires (hitting the
        except TimeoutError: continue branch) and a second scan iteration begins.
        """
        task_handler = AsyncMock()
        task_handler.list_tasks = AsyncMock(return_value=[])
        event_bus = AsyncMock()
        service = StaleLeaseRecoveryService(
            task_handler=task_handler, event_bus=event_bus
        )

        run_task = asyncio.create_task(service.start(poll_interval=1.0))
        # 1.1 s ensures the 1.0 s poll timeout fires at least once (TimeoutError path)
        # and then a second scan begins before we stop.
        await asyncio.sleep(1.1)
        await service.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

        # Two scans confirm the TimeoutError -> continue path ran at least once
        assert task_handler.list_tasks.await_count >= 2
        assert service.running is False


# ---------------------------------------------------------------------------
# Previously existing tests (preserved for continuity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_and_recover_requeues_expired_leases() -> None:
    """Expired lease entries should be requeued automatically."""
    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    task_handler = AsyncMock()
    task_handler.list_tasks = AsyncMock(
        return_value=[
            {
                "id": "task-expired",
                "status": "assigned",
                "claimed_by": "forge:codex",
                "lease": {
                    "owner_node": "nova",
                    "owner_agent": "forge:codex",
                    "path_lock": "harness/forge_harness",
                    "lease_expires_at": (now - timedelta(minutes=3)).isoformat(),
                },
            },
            {
                "id": "task-active",
                "status": "assigned",
                "claimed_by": "forge:claude",
                "lease": {
                    "owner_node": "prya",
                    "owner_agent": "forge:claude",
                    "path_lock": "forge-terminal/ios",
                    "lease_expires_at": (now + timedelta(minutes=3)).isoformat(),
                },
            },
        ]
    )
    task_handler.requeue_task = AsyncMock(
        return_value={
            "id": "task-expired",
            "status": "pending",
            "claimed_by": "",
            "lease": None,
        }
    )

    event_bus = AsyncMock()
    service = StaleLeaseRecoveryService(
        task_handler=task_handler,
        event_bus=event_bus,
        now_provider=lambda: now,
    )

    recovered = await service.scan_and_recover()

    assert recovered == 1
    task_handler.requeue_task.assert_awaited_once_with("task-expired")
    assert event_bus.publish.await_count == 2

    first_event = event_bus.publish.await_args_list[0]
    assert first_event.args[0] == "task.lease.expired"
    assert first_event.args[1]["task_id"] == "task-expired"
    assert first_event.args[1]["owner_node"] == "nova"

    second_event = event_bus.publish.await_args_list[1]
    assert second_event.args[0] == "task.requeued"
    assert second_event.args[1]["reason"] == "lease_expired_auto_recovery"


@pytest.mark.asyncio
async def test_scan_and_recover_skips_non_expired_or_invalid_leases() -> None:
    """Only valid expired leases should trigger requeue."""
    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    task_handler = AsyncMock()
    task_handler.list_tasks = AsyncMock(
        return_value=[
            {"id": "task-no-lease", "lease": None},
            {"id": "task-invalid", "lease": {"lease_expires_at": "not-a-date"}},
            {
                "id": "task-future",
                "lease": {"lease_expires_at": (now + timedelta(minutes=1)).isoformat()},
            },
        ]
    )
    task_handler.requeue_task = AsyncMock()

    event_bus = AsyncMock()
    service = StaleLeaseRecoveryService(
        task_handler=task_handler,
        event_bus=event_bus,
        now_provider=lambda: now,
    )

    recovered = await service.scan_and_recover()

    assert recovered == 0
    task_handler.requeue_task.assert_not_awaited()
    event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_and_stop_recovery_loop() -> None:
    """Service should run scan loop and stop cleanly."""
    task_handler = AsyncMock()
    task_handler.list_tasks = AsyncMock(return_value=[])
    task_handler.requeue_task = AsyncMock()
    event_bus = AsyncMock()

    service = StaleLeaseRecoveryService(task_handler=task_handler, event_bus=event_bus)

    run_task = asyncio.create_task(service.start(poll_interval=0.01))
    await asyncio.sleep(0)
    await service.stop()
    await asyncio.wait_for(run_task, timeout=1.0)

    assert task_handler.list_tasks.await_count >= 1
    assert service.running is False


@pytest.mark.asyncio
async def test_webhook_main_starts_and_stops_lease_recovery(monkeypatch) -> None:
    """Webhook main lifecycle helpers should manage recovery worker state."""
    import forge_harness.webhook_server.services.lease_recovery as lease_module
    import forge_harness.webhook_server_main as webhook_main

    class DummyLeaseRecoveryService:
        def __init__(self, *, task_handler, event_bus) -> None:
            self.task_handler = task_handler
            self.event_bus = event_bus
            self.started = asyncio.Event()
            self.stopped = False

        async def start(self, poll_interval: float = 30.0) -> None:
            self.poll_interval = poll_interval
            self.started.set()
            while not self.stopped:
                await asyncio.sleep(0.01)

        async def stop(self) -> None:
            self.stopped = True

    await webhook_main.stop_lease_recovery_service()
    monkeypatch.setenv("FORGE_TASK_LEASE_RECOVERY_POLL_INTERVAL_SECONDS", "7")
    monkeypatch.setattr(lease_module, "StaleLeaseRecoveryService", DummyLeaseRecoveryService)

    service = await webhook_main.start_lease_recovery_service()
    assert isinstance(service, DummyLeaseRecoveryService)
    await asyncio.wait_for(service.started.wait(), timeout=1.0)
    assert service.poll_interval == 7.0
    assert webhook_main.get_lease_recovery_service() is service

    await webhook_main.stop_lease_recovery_service()
    assert webhook_main.get_lease_recovery_service() is None
