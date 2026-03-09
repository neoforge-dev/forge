"""Comprehensive unit tests for LeaseRecoveryWorker v2.

Covers:
- RecoveryStats: record_recovery, record_failure, record_scan, rolling average
- LeaseStore / EventBus protocol compliance
- LeaseRecoveryWorker.__init__: defaults, scan_interval clamping
- LeaseRecoveryWorker.running property
- scan_expired: empty store, mixed leases, store exception, state=EXPIRED detection,
  is_expired() detection, both conditions combined
- recover: happy path (EXPIRED state), happy path (ACTIVE state needing intermediate),
  happy path (CLAIMED state), transition error (illegal state), unexpected exception,
  ownership field clearing, path_lock clearing, event payloads, stats updates
- run: stop before first scan, double-start guard, scan_expired exception swallowed,
  recover exception swallowed in run loop, stats.record_scan called
- stop: sets stop event, idempotent double-stop
- get_stats: returns copy not original reference
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from forge_harness.models.lease import (
    LeaseState,
    LeaseTransitionError,
    TaskLease,
)
from forge_harness.webhook_server.services.lease_recovery_v2 import (
    EventBus,
    LeaseRecoveryWorker,
    LeaseStore,
    RecoveryStats,
)

# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _make_lease(
    state: LeaseState = LeaseState.EXPIRED,
    expires_at: datetime | None = None,
    owner_node: str = "nova",
    owner_agent: str = "forge:claude",
    path_lock: str | None = "/src/api.py",
    task_id: str | None = None,
) -> TaskLease:
    """Build a TaskLease with sensible test defaults."""
    return TaskLease(
        task_id=task_id or str(uuid.uuid4()),
        state=state,
        owner_node=owner_node,
        owner_agent=owner_agent,
        path_lock=path_lock,
        expires_at=expires_at,
    )


def _expired_lease(**kwargs: Any) -> TaskLease:
    """Return a lease whose wall-clock TTL has elapsed (is_expired() == True)."""
    past = datetime.now(UTC) - timedelta(seconds=10)
    return _make_lease(state=LeaseState.ACTIVE, expires_at=past, **kwargs)


def _fresh_lease(**kwargs: Any) -> TaskLease:
    """Return a lease that is not yet expired."""
    future = datetime.now(UTC) + timedelta(seconds=300)
    return _make_lease(state=LeaseState.ACTIVE, expires_at=future, **kwargs)


def _make_store(leases: list[TaskLease] | None = None) -> AsyncMock:
    """Return a mock LeaseStore."""
    store = AsyncMock()
    store.list_leases = AsyncMock(return_value=leases or [])
    store.save_lease = AsyncMock(return_value=None)
    return store


def _make_bus() -> AsyncMock:
    """Return a mock EventBus."""
    bus = AsyncMock()
    bus.publish = AsyncMock(return_value=None)
    return bus


def _make_worker(
    leases: list[TaskLease] | None = None,
    scan_interval: float = 60.0,
) -> tuple[LeaseRecoveryWorker, AsyncMock, AsyncMock]:
    store = _make_store(leases)
    bus = _make_bus()
    worker = LeaseRecoveryWorker(
        lease_store=store,
        event_bus=bus,
        scan_interval_seconds=scan_interval,
    )
    return worker, store, bus


# ---------------------------------------------------------------------------
# RecoveryStats tests
# ---------------------------------------------------------------------------


class TestRecoveryStats:
    def test_initial_values(self):
        stats = RecoveryStats()
        assert stats.total_scans == 0
        assert stats.total_recovered == 0
        assert stats.total_failed == 0
        assert stats.last_scan_at is None
        assert stats.avg_recovery_time_ms is None

    def test_record_scan_increments_counter(self):
        stats = RecoveryStats()
        now = datetime.now(UTC)
        stats.record_scan(now)
        assert stats.total_scans == 1
        assert stats.last_scan_at == now

    def test_record_scan_multiple_calls(self):
        stats = RecoveryStats()
        t1 = datetime.now(UTC)
        t2 = datetime.now(UTC) + timedelta(seconds=60)
        stats.record_scan(t1)
        stats.record_scan(t2)
        assert stats.total_scans == 2
        # last_scan_at reflects the most recent call
        assert stats.last_scan_at == t2

    def test_record_failure_increments_counter(self):
        stats = RecoveryStats()
        stats.record_failure()
        stats.record_failure()
        assert stats.total_failed == 2

    def test_record_recovery_increments_total_recovered(self):
        stats = RecoveryStats()
        stats.record_recovery(50.0)
        assert stats.total_recovered == 1

    def test_record_recovery_sets_avg_on_first_sample(self):
        stats = RecoveryStats()
        stats.record_recovery(100.0)
        assert stats.avg_recovery_time_ms == 100.0

    def test_record_recovery_rolling_average(self):
        stats = RecoveryStats()
        stats.record_recovery(100.0)
        stats.record_recovery(200.0)
        # Average of 100 and 200
        assert stats.avg_recovery_time_ms == pytest.approx(150.0)

    def test_record_recovery_rolling_average_three_samples(self):
        stats = RecoveryStats()
        stats.record_recovery(10.0)
        stats.record_recovery(20.0)
        stats.record_recovery(30.0)
        assert stats.avg_recovery_time_ms == pytest.approx(20.0)

    def test_counters_independent(self):
        stats = RecoveryStats()
        stats.record_failure()
        stats.record_recovery(5.0)
        stats.record_scan(datetime.now(UTC))
        assert stats.total_failed == 1
        assert stats.total_recovered == 1
        assert stats.total_scans == 1


# ---------------------------------------------------------------------------
# Worker construction tests
# ---------------------------------------------------------------------------


class TestLeaseRecoveryWorkerInit:
    def test_defaults(self):
        store = _make_store()
        bus = _make_bus()
        worker = LeaseRecoveryWorker(store, bus)
        assert worker._scan_interval == 60.0
        assert worker.running is False

    def test_custom_scan_interval(self):
        store = _make_store()
        bus = _make_bus()
        worker = LeaseRecoveryWorker(store, bus, scan_interval_seconds=30.0)
        assert worker._scan_interval == 30.0

    def test_scan_interval_clamped_to_minimum_one_second(self):
        store = _make_store()
        bus = _make_bus()
        worker = LeaseRecoveryWorker(store, bus, scan_interval_seconds=0.0)
        assert worker._scan_interval == 1.0

    def test_scan_interval_negative_clamped(self):
        store = _make_store()
        bus = _make_bus()
        worker = LeaseRecoveryWorker(store, bus, scan_interval_seconds=-5.0)
        assert worker._scan_interval == 1.0

    def test_running_property_initially_false(self):
        worker, _, _ = _make_worker()
        assert worker.running is False

    def test_stats_initialized_empty(self):
        worker, _, _ = _make_worker()
        stats = worker.get_stats()
        assert stats.total_scans == 0
        assert stats.total_recovered == 0
        assert stats.total_failed == 0


# ---------------------------------------------------------------------------
# scan_expired tests
# ---------------------------------------------------------------------------


class TestScanExpired:
    @pytest.mark.asyncio
    async def test_empty_store_returns_empty_list(self):
        worker, _, _ = _make_worker(leases=[])
        result = await worker.scan_expired()
        assert result == []

    @pytest.mark.asyncio
    async def test_lease_in_expired_state_is_returned(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert lease in result

    @pytest.mark.asyncio
    async def test_lease_with_elapsed_expires_at_is_returned(self):
        lease = _expired_lease()
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert lease in result

    @pytest.mark.asyncio
    async def test_active_non_expired_lease_not_returned(self):
        lease = _fresh_lease()
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert result == []

    @pytest.mark.asyncio
    async def test_mixed_leases_only_expired_returned(self):
        expired = _make_lease(state=LeaseState.EXPIRED, path_lock=None, task_id="t1")
        expired_clock = _expired_lease(task_id="t2")
        fresh = _fresh_lease(task_id="t3")
        unclaimed = _make_lease(state=LeaseState.UNCLAIMED, task_id="t4")
        worker, _, _ = _make_worker(leases=[expired, expired_clock, fresh, unclaimed])
        result = await worker.scan_expired()
        assert expired in result
        assert expired_clock in result
        assert fresh not in result
        assert unclaimed not in result

    @pytest.mark.asyncio
    async def test_store_exception_returns_empty_list(self):
        store = _make_store()
        store.list_leases = AsyncMock(side_effect=RuntimeError("DB down"))
        bus = _make_bus()
        worker = LeaseRecoveryWorker(store, bus)
        # Should not raise — errors are swallowed and empty list returned
        result = await worker.scan_expired()
        assert result == []

    @pytest.mark.asyncio
    async def test_lease_with_no_expires_at_and_not_expired_state_excluded(self):
        lease = _make_lease(state=LeaseState.CLAIMED, expires_at=None)
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert result == []

    @pytest.mark.asyncio
    async def test_requeued_state_not_considered_expired(self):
        lease = _make_lease(state=LeaseState.REQUEUED)
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert result == []


# ---------------------------------------------------------------------------
# recover() tests
# ---------------------------------------------------------------------------


class TestRecover:
    @pytest.mark.asyncio
    async def test_recover_expired_lease_returns_true(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        result = await worker.recover(lease)
        assert result is True

    @pytest.mark.asyncio
    async def test_recover_sets_state_to_requeued(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        assert lease.state is LeaseState.REQUEUED

    @pytest.mark.asyncio
    async def test_recover_clears_owner_node(self):
        lease = _make_lease(state=LeaseState.EXPIRED, owner_node="nova")
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        assert lease.owner_node == ""

    @pytest.mark.asyncio
    async def test_recover_clears_owner_agent(self):
        lease = _make_lease(state=LeaseState.EXPIRED, owner_agent="forge:claude")
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        assert lease.owner_agent == ""

    @pytest.mark.asyncio
    async def test_recover_clears_path_lock(self):
        lease = _make_lease(state=LeaseState.EXPIRED, path_lock="/src/api.py")
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        assert lease.path_lock is None

    @pytest.mark.asyncio
    async def test_recover_persists_lease_via_store(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        store.save_lease.assert_awaited_once_with(lease)

    @pytest.mark.asyncio
    async def test_recover_publishes_expired_detected_event(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        topics = [c.args[0] for c in bus.publish.call_args_list]
        assert "lease.expired.detected" in topics

    @pytest.mark.asyncio
    async def test_recover_publishes_lease_recovered_event(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        topics = [c.args[0] for c in bus.publish.call_args_list]
        assert "lease.recovered" in topics

    @pytest.mark.asyncio
    async def test_recover_events_in_correct_order(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        topics = [c.args[0] for c in bus.publish.call_args_list]
        # expired.detected must precede lease.recovered
        assert topics.index("lease.expired.detected") < topics.index("lease.recovered")

    @pytest.mark.asyncio
    async def test_recover_updates_stats_on_success(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        stats = worker.get_stats()
        assert stats.total_recovered == 1
        assert stats.total_failed == 0

    @pytest.mark.asyncio
    async def test_recover_active_lease_with_past_expires_at(self):
        """ACTIVE lease whose TTL has elapsed: should go ACTIVE -> EXPIRED -> REQUEUED."""
        lease = _expired_lease()
        assert lease.state is LeaseState.ACTIVE
        worker, store, bus = _make_worker()
        result = await worker.recover(lease)
        assert result is True
        assert lease.state is LeaseState.REQUEUED

    @pytest.mark.asyncio
    async def test_recover_claimed_lease(self):
        """CLAIMED lease is also a valid source: CLAIMED -> EXPIRED -> REQUEUED."""
        lease = _make_lease(state=LeaseState.CLAIMED)
        worker, store, bus = _make_worker()
        result = await worker.recover(lease)
        assert result is True
        assert lease.state is LeaseState.REQUEUED

    @pytest.mark.asyncio
    async def test_recover_illegal_transition_returns_false(self):
        """A lease in UNCLAIMED state cannot go to EXPIRED — should return False."""
        lease = _make_lease(state=LeaseState.UNCLAIMED)
        worker, store, bus = _make_worker()
        result = await worker.recover(lease)
        assert result is False

    @pytest.mark.asyncio
    async def test_recover_illegal_transition_publishes_recovery_failed_event(self):
        lease = _make_lease(state=LeaseState.UNCLAIMED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        topics = [c.args[0] for c in bus.publish.call_args_list]
        assert "lease.recovery.failed" in topics

    @pytest.mark.asyncio
    async def test_recover_illegal_transition_increments_failed_stats(self):
        lease = _make_lease(state=LeaseState.UNCLAIMED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        stats = worker.get_stats()
        assert stats.total_failed == 1

    @pytest.mark.asyncio
    async def test_recover_save_error_publishes_recovery_failed_event(self):
        """Unexpected exception during save_lease triggers failure event and re-raises."""
        lease = _make_lease(state=LeaseState.EXPIRED)
        store = _make_store()
        store.save_lease = AsyncMock(side_effect=OSError("disk full"))
        bus = _make_bus()
        worker = LeaseRecoveryWorker(store, bus)
        with pytest.raises(IOError, match="disk full"):
            await worker.recover(lease)
        topics = [c.args[0] for c in bus.publish.call_args_list]
        assert "lease.recovery.failed" in topics

    @pytest.mark.asyncio
    async def test_recover_save_error_increments_failed_stats(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        store = _make_store()
        store.save_lease = AsyncMock(side_effect=RuntimeError("timeout"))
        bus = _make_bus()
        worker = LeaseRecoveryWorker(store, bus)
        with pytest.raises(RuntimeError):
            await worker.recover(lease)
        stats = worker.get_stats()
        assert stats.total_failed == 1

    @pytest.mark.asyncio
    async def test_recover_expired_detected_payload_contains_lease_id(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        detected_call = next(
            c for c in bus.publish.call_args_list if c.args[0] == "lease.expired.detected"
        )
        payload = detected_call.args[1]
        assert payload["lease_id"] == lease.lease_id

    @pytest.mark.asyncio
    async def test_recover_recovered_payload_contains_recovery_time_ms(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        recovered_call = next(
            c for c in bus.publish.call_args_list if c.args[0] == "lease.recovered"
        )
        payload = recovered_call.args[1]
        assert "recovery_time_ms" in payload
        assert isinstance(payload["recovery_time_ms"], float)

    @pytest.mark.asyncio
    async def test_recover_updates_updated_at_timestamp(self):
        before = datetime.now(UTC)
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        assert lease.updated_at >= before

    @pytest.mark.asyncio
    async def test_recovery_failed_payload_includes_reason_unexpected_error(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        store = _make_store()
        store.save_lease = AsyncMock(side_effect=ValueError("bad value"))
        bus = _make_bus()
        worker = LeaseRecoveryWorker(store, bus)
        with pytest.raises(ValueError):
            await worker.recover(lease)
        failed_call = next(
            c for c in bus.publish.call_args_list if c.args[0] == "lease.recovery.failed"
        )
        payload = failed_call.args[1]
        assert payload["reason"] == "unexpected_error"

    @pytest.mark.asyncio
    async def test_recovery_failed_payload_includes_reason_invalid_state_transition(self):
        lease = _make_lease(state=LeaseState.UNCLAIMED)
        worker, store, bus = _make_worker()
        await worker.recover(lease)
        failed_call = next(
            c for c in bus.publish.call_args_list if c.args[0] == "lease.recovery.failed"
        )
        payload = failed_call.args[1]
        assert payload["reason"] == "invalid_state_transition"


# ---------------------------------------------------------------------------
# run() / stop() tests
# ---------------------------------------------------------------------------


class TestRunStop:
    @pytest.mark.asyncio
    async def test_stop_before_first_scan_exits_cleanly(self):
        """Worker stopped immediately should exit without scanning.

        The run() loop waits ``scan_interval`` before the first scan.
        When stop() sets the internal event during that wait the loop
        detects the set event and exits without calling scan_expired.
        We use a short interval so the test terminates quickly but
        still give the loop enough time to enter wait_for before we
        signal stop.
        """
        worker, store, bus = _make_worker(scan_interval=0.2)
        task = asyncio.create_task(worker.run())
        # Yield control so the coroutine starts and enters wait_for
        await asyncio.sleep(0.05)
        # Now signal stop — the wait_for will resolve the stop event
        await worker.stop()
        # The loop should exit once the wait_for detects the event
        await asyncio.wait_for(task, timeout=5.0)
        assert not worker.running
        # No scan should have occurred (list_leases not called)
        store.list_leases.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_running_is_true_while_loop_active(self):
        worker, store, bus = _make_worker(scan_interval=100.0)
        task = asyncio.create_task(worker.run())
        # Give the coroutine a chance to set _running = True
        await asyncio.sleep(0)
        assert worker.running is True
        await worker.stop()
        await asyncio.wait_for(task, timeout=3.0)
        assert worker.running is False

    @pytest.mark.asyncio
    async def test_double_start_returns_early(self):
        """Calling run() while already running should return without a second loop."""
        worker, store, bus = _make_worker(scan_interval=100.0)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0)
        assert worker.running is True
        # Second call should return immediately
        await asyncio.wait_for(worker.run(), timeout=1.0)
        await worker.stop()
        await asyncio.wait_for(task, timeout=3.0)

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        worker, store, bus = _make_worker(scan_interval=100.0)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0)
        await worker.stop()
        await worker.stop()  # second stop should not raise
        await asyncio.wait_for(task, timeout=3.0)

    @pytest.mark.asyncio
    async def test_run_calls_record_scan_per_cycle(self):
        """After one scan interval completes, record_scan should be called once."""
        expired = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker(leases=[expired], scan_interval=0.05)

        # Patch asyncio.wait_for to simulate timeout immediately on first call,
        # then stop event set on second call.
        original_wait_for = asyncio.wait_for
        call_count = 0

        async def fake_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Discard the coroutine to avoid ResourceWarning
                coro.close()
                raise TimeoutError
            else:
                return await original_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            task = asyncio.create_task(worker.run())
            await asyncio.sleep(0.1)
            await worker.stop()
            await asyncio.wait_for(task, timeout=3.0)

        stats = worker.get_stats()
        assert stats.total_scans >= 1

    @pytest.mark.asyncio
    async def test_scan_expired_exception_does_not_crash_run_loop(self):
        """scan_expired raising should be caught and the loop should continue."""
        store = _make_store()
        store.list_leases = AsyncMock(side_effect=Exception("transient"))
        bus = _make_bus()
        worker = LeaseRecoveryWorker(store, bus, scan_interval_seconds=0.05)

        original_wait_for = asyncio.wait_for
        call_count = 0

        async def fake_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                coro.close()
                raise TimeoutError
            else:
                return await original_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            task = asyncio.create_task(worker.run())
            await asyncio.sleep(0.1)
            await worker.stop()
            await asyncio.wait_for(task, timeout=3.0)

        # Should still be alive and not raised
        assert not worker.running


# ---------------------------------------------------------------------------
# get_stats() tests
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_get_stats_returns_copy(self):
        worker, _, _ = _make_worker()
        stats1 = worker.get_stats()
        stats2 = worker.get_stats()
        # Mutating one should not affect the other
        stats1.total_scans = 999
        stats3 = worker.get_stats()
        assert stats3.total_scans == 0

    def test_get_stats_reflects_recorded_values(self):
        worker, _, _ = _make_worker()
        worker._stats.record_scan(datetime.now(UTC))
        worker._stats.record_recovery(25.0)
        worker._stats.record_failure()
        stats = worker.get_stats()
        assert stats.total_scans == 1
        assert stats.total_recovered == 1
        assert stats.total_failed == 1
        assert stats.avg_recovery_time_ms == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# Protocol compliance (structural check)
# ---------------------------------------------------------------------------


class TestProtocols:
    def test_mock_store_satisfies_lease_store_protocol(self):
        store = _make_store()
        assert isinstance(store, LeaseStore)

    def test_mock_bus_satisfies_event_bus_protocol(self):
        bus = _make_bus()
        assert isinstance(bus, EventBus)
