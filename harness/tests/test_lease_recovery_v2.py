"""Comprehensive tests for LeaseRecoveryWorker v2 (CP-2007).

Coverage targets (90%+):
- RecoveryStats model: default values, record_recovery, record_failure, record_scan
- LeaseRecoveryWorker.__init__: default and custom scan interval clamping
- LeaseRecoveryWorker.running property
- scan_expired(): empty store, single expired by is_expired(), single in EXPIRED state,
  mixed healthy+expired, store raises exception
- recover(): happy path (EXPIRED→REQUEUED), pre-EXPIRED normalization (ACTIVE→EXPIRED→REQUEUED),
  illegal state (UNCLAIMED→REQUEUED raises LeaseTransitionError, returns False),
  unexpected store exception (re-raises), ownership fields cleared, path_lock cleared,
  event publication sequence (detected, recovered, recovery.failed)
- run() loop: stop before first scan, single scan cycle, multiple expired in one scan,
  scan_expired raises inside loop (loop continues), recover raises inside loop (loop continues),
  graceful shutdown, already-running guard
- stop(): idempotent, sets stop_event
- get_stats(): returns copy, does not mutate internal state
- Event payload correctness for all three event topics
- Stats accumulation across multiple scans
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.models.lease import LeaseState, LeaseTransitionError, TaskLease
from forge_harness.webhook_server.services.lease_recovery_v2 import (
    LeaseRecoveryWorker,
    RecoveryStats,
)

# ---------------------------------------------------------------------------
# Test helpers / factories
# ---------------------------------------------------------------------------


def _make_lease(
    *,
    task_id: str = "task-001",
    state: LeaseState = LeaseState.EXPIRED,
    owner_node: str = "nova",
    owner_agent: str = "forge:codex",
    path_lock: str | None = "src/api/routes.py",
    expires_at: datetime | None = None,
    minutes_expired: int | None = 5,
) -> TaskLease:
    """Return a minimal TaskLease with sensible defaults for test use."""
    if expires_at is None and minutes_expired is not None:
        expires_at = datetime.utcnow() - timedelta(minutes=minutes_expired)

    return TaskLease(
        task_id=task_id,
        state=state,
        owner_node=owner_node,
        owner_agent=owner_agent,
        path_lock=path_lock,
        expires_at=expires_at,
    )


def _make_worker(
    *,
    leases: list[TaskLease] | None = None,
    save_side_effect: Exception | None = None,
    scan_interval: float = 0.05,
) -> tuple[LeaseRecoveryWorker, AsyncMock, AsyncMock]:
    """Return (worker, lease_store_mock, event_bus_mock)."""
    lease_store = AsyncMock()
    lease_store.list_leases = AsyncMock(return_value=leases or [])
    if save_side_effect is not None:
        lease_store.save_lease = AsyncMock(side_effect=save_side_effect)
    else:
        lease_store.save_lease = AsyncMock(return_value=None)

    event_bus = AsyncMock()
    event_bus.publish = AsyncMock(return_value=None)

    worker = LeaseRecoveryWorker(
        lease_store=lease_store,
        event_bus=event_bus,
        scan_interval_seconds=scan_interval,
    )
    return worker, lease_store, event_bus


# ---------------------------------------------------------------------------
# 1. RecoveryStats model
# ---------------------------------------------------------------------------


class TestRecoveryStats:
    def test_default_values(self) -> None:
        stats = RecoveryStats()
        assert stats.total_scans == 0
        assert stats.total_recovered == 0
        assert stats.total_failed == 0
        assert stats.last_scan_at is None
        assert stats.avg_recovery_time_ms is None

    def test_record_recovery_increments_counter(self) -> None:
        stats = RecoveryStats()
        stats.record_recovery(elapsed_ms=42.5)
        assert stats.total_recovered == 1
        assert stats.avg_recovery_time_ms == pytest.approx(42.5)

    def test_record_recovery_rolling_average(self) -> None:
        stats = RecoveryStats()
        stats.record_recovery(10.0)
        stats.record_recovery(20.0)
        stats.record_recovery(30.0)
        assert stats.total_recovered == 3
        assert stats.avg_recovery_time_ms == pytest.approx(20.0)

    def test_record_failure_increments_counter(self) -> None:
        stats = RecoveryStats()
        stats.record_failure()
        stats.record_failure()
        assert stats.total_failed == 2
        assert stats.total_recovered == 0

    def test_record_scan_increments_counter_and_sets_timestamp(self) -> None:
        stats = RecoveryStats()
        ts = datetime(2026, 2, 22, 10, 0, 0, tzinfo=UTC)
        stats.record_scan(ts)
        assert stats.total_scans == 1
        assert stats.last_scan_at == ts

    def test_record_scan_overwrites_last_scan_at(self) -> None:
        stats = RecoveryStats()
        t1 = datetime(2026, 2, 22, 10, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 2, 22, 11, 0, 0, tzinfo=UTC)
        stats.record_scan(t1)
        stats.record_scan(t2)
        assert stats.total_scans == 2
        assert stats.last_scan_at == t2

    def test_independent_instances_do_not_share_state(self) -> None:
        """Each RecoveryStats instance must have its own sample list."""
        s1 = RecoveryStats()
        s2 = RecoveryStats()
        s1.record_recovery(100.0)
        s2.record_recovery(200.0)
        assert s1.total_recovered == 1
        assert s2.total_recovered == 1
        assert s1.avg_recovery_time_ms == pytest.approx(100.0)
        assert s2.avg_recovery_time_ms == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# 2. LeaseRecoveryWorker.__init__ and properties
# ---------------------------------------------------------------------------


class TestWorkerInit:
    def test_default_scan_interval_clamped_at_minimum(self) -> None:
        worker = LeaseRecoveryWorker(AsyncMock(), AsyncMock(), scan_interval_seconds=0)
        assert worker._scan_interval == 1.0

    def test_negative_scan_interval_clamped(self) -> None:
        worker = LeaseRecoveryWorker(AsyncMock(), AsyncMock(), scan_interval_seconds=-100)
        assert worker._scan_interval == 1.0

    def test_custom_scan_interval_preserved(self) -> None:
        worker = LeaseRecoveryWorker(AsyncMock(), AsyncMock(), scan_interval_seconds=120.0)
        assert worker._scan_interval == 120.0

    def test_running_property_starts_false(self) -> None:
        worker, _, _ = _make_worker()
        assert worker.running is False

    def test_store_and_bus_assigned(self) -> None:
        store = AsyncMock()
        bus = AsyncMock()
        worker = LeaseRecoveryWorker(store, bus, scan_interval_seconds=5)
        assert worker._lease_store is store
        assert worker._event_bus is bus

    def test_initial_stats_are_zeroed(self) -> None:
        worker, _, _ = _make_worker()
        stats = worker.get_stats()
        assert stats.total_scans == 0
        assert stats.total_recovered == 0
        assert stats.total_failed == 0


# ---------------------------------------------------------------------------
# 3. scan_expired()
# ---------------------------------------------------------------------------


class TestScanExpired:
    @pytest.mark.asyncio
    async def test_empty_store_returns_empty_list(self) -> None:
        worker, _, _ = _make_worker(leases=[])
        result = await worker.scan_expired()
        assert result == []

    @pytest.mark.asyncio
    async def test_lease_in_expired_state_detected(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED, minutes_expired=None, expires_at=None)
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert len(result) == 1
        assert result[0].lease_id == lease.lease_id

    @pytest.mark.asyncio
    async def test_lease_with_past_expires_at_detected_via_is_expired(self) -> None:
        # Lease is in ACTIVE state but TTL has elapsed
        lease = _make_lease(
            state=LeaseState.ACTIVE,
            expires_at=datetime.utcnow() - timedelta(minutes=10),
            minutes_expired=None,
        )
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_healthy_lease_not_detected(self) -> None:
        lease = _make_lease(
            state=LeaseState.ACTIVE,
            expires_at=datetime.utcnow() + timedelta(minutes=30),
            minutes_expired=None,
        )
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert result == []

    @pytest.mark.asyncio
    async def test_unclaimed_lease_with_no_expiry_not_detected(self) -> None:
        lease = _make_lease(
            state=LeaseState.UNCLAIMED,
            expires_at=None,
            minutes_expired=None,
        )
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert result == []

    @pytest.mark.asyncio
    async def test_mixed_leases_only_expired_returned(self) -> None:
        expired_state = _make_lease(state=LeaseState.EXPIRED, task_id="t-expired-state")
        expired_ttl = _make_lease(
            state=LeaseState.ACTIVE,
            task_id="t-expired-ttl",
            expires_at=datetime.utcnow() - timedelta(seconds=1),
            minutes_expired=None,
        )
        healthy = _make_lease(
            state=LeaseState.ACTIVE,
            task_id="t-healthy",
            expires_at=datetime.utcnow() + timedelta(hours=1),
            minutes_expired=None,
        )
        unclaimed = _make_lease(
            state=LeaseState.UNCLAIMED,
            task_id="t-unclaimed",
            expires_at=None,
            minutes_expired=None,
        )
        worker, _, _ = _make_worker(leases=[expired_state, expired_ttl, healthy, unclaimed])
        result = await worker.scan_expired()
        result_ids = {l.task_id for l in result}
        assert result_ids == {"t-expired-state", "t-expired-ttl"}

    @pytest.mark.asyncio
    async def test_store_exception_returns_empty_list(self) -> None:
        worker, lease_store, _ = _make_worker()
        lease_store.list_leases = AsyncMock(side_effect=RuntimeError("DB offline"))
        result = await worker.scan_expired()
        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_expired_leases_all_returned(self) -> None:
        leases = [_make_lease(task_id=f"task-{i}") for i in range(5)]
        worker, _, _ = _make_worker(leases=leases)
        result = await worker.scan_expired()
        assert len(result) == 5


# ---------------------------------------------------------------------------
# 4. recover()
# ---------------------------------------------------------------------------


class TestRecover:
    @pytest.mark.asyncio
    async def test_happy_path_expired_to_requeued(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, lease_store, event_bus = _make_worker()

        result = await worker.recover(lease)

        assert result is True
        assert lease.state is LeaseState.REQUEUED
        lease_store.save_lease.assert_awaited_once_with(lease)

    @pytest.mark.asyncio
    async def test_ownership_fields_cleared_after_recovery(self) -> None:
        lease = _make_lease(
            state=LeaseState.EXPIRED,
            owner_node="nova",
            owner_agent="forge:codex",
            path_lock="src/api/routes.py",
        )
        worker, _, _ = _make_worker()
        await worker.recover(lease)

        assert lease.owner_node == ""
        assert lease.owner_agent == ""
        assert lease.path_lock is None

    @pytest.mark.asyncio
    async def test_path_lock_cleared_after_recovery(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED, path_lock="some/locked/file.py")
        worker, _, _ = _make_worker()
        await worker.recover(lease)
        assert lease.path_lock is None

    @pytest.mark.asyncio
    async def test_pre_expired_normalization_active_to_requeued(self) -> None:
        """ACTIVE lease with elapsed TTL: ACTIVE→EXPIRED→REQUEUED."""
        lease = _make_lease(
            state=LeaseState.ACTIVE,
            expires_at=datetime.utcnow() - timedelta(minutes=5),
            minutes_expired=None,
        )
        worker, _, _ = _make_worker()
        result = await worker.recover(lease)
        assert result is True
        assert lease.state is LeaseState.REQUEUED

    @pytest.mark.asyncio
    async def test_pre_expired_normalization_claimed_to_requeued(self) -> None:
        """CLAIMED lease: CLAIMED→EXPIRED→REQUEUED."""
        lease = _make_lease(
            state=LeaseState.CLAIMED,
            expires_at=datetime.utcnow() - timedelta(minutes=1),
            minutes_expired=None,
        )
        worker, _, _ = _make_worker()
        result = await worker.recover(lease)
        assert result is True
        assert lease.state is LeaseState.REQUEUED

    @pytest.mark.asyncio
    async def test_illegal_state_transition_returns_false(self) -> None:
        """UNCLAIMED cannot go to EXPIRED — LeaseTransitionError → return False."""
        lease = _make_lease(
            state=LeaseState.UNCLAIMED,
            expires_at=None,
            minutes_expired=None,
        )
        worker, lease_store, event_bus = _make_worker()
        result = await worker.recover(lease)

        assert result is False
        lease_store.save_lease.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_illegal_state_publishes_recovery_failed_event(self) -> None:
        """LeaseTransitionError should emit lease.recovery.failed."""
        lease = _make_lease(
            state=LeaseState.UNCLAIMED,
            expires_at=None,
            minutes_expired=None,
        )
        worker, _, event_bus = _make_worker()
        await worker.recover(lease)

        published_topics = [call.args[0] for call in event_bus.publish.await_args_list]
        assert "lease.recovery.failed" in published_topics

    @pytest.mark.asyncio
    async def test_store_exception_re_raises_after_failed_event(self) -> None:
        """If save_lease raises, recover() re-raises and emits lease.recovery.failed."""
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, _, event_bus = _make_worker(save_side_effect=OSError("disk full"))

        with pytest.raises(OSError, match="disk full"):
            await worker.recover(lease)

        published_topics = [call.args[0] for call in event_bus.publish.await_args_list]
        assert "lease.recovery.failed" in published_topics

    @pytest.mark.asyncio
    async def test_store_exception_increments_failed_stat(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, _, _ = _make_worker(save_side_effect=RuntimeError("timeout"))

        with pytest.raises(RuntimeError):
            await worker.recover(lease)

        assert worker.get_stats().total_failed == 1

    @pytest.mark.asyncio
    async def test_illegal_state_increments_failed_stat(self) -> None:
        lease = _make_lease(state=LeaseState.UNCLAIMED, expires_at=None, minutes_expired=None)
        worker, _, _ = _make_worker()
        await worker.recover(lease)
        assert worker.get_stats().total_failed == 1

    @pytest.mark.asyncio
    async def test_recovery_increments_recovered_stat(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, _, _ = _make_worker()
        await worker.recover(lease)
        assert worker.get_stats().total_recovered == 1
        assert worker.get_stats().total_failed == 0

    @pytest.mark.asyncio
    async def test_avg_recovery_time_ms_is_set_after_recovery(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, _, _ = _make_worker()
        await worker.recover(lease)
        stats = worker.get_stats()
        assert stats.avg_recovery_time_ms is not None
        assert stats.avg_recovery_time_ms >= 0.0

    # --- Event payload correctness ---

    @pytest.mark.asyncio
    async def test_expired_detected_event_published_first(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED, task_id="test-task")
        worker, _, event_bus = _make_worker()
        await worker.recover(lease)

        first_call = event_bus.publish.await_args_list[0]
        assert first_call.args[0] == "lease.expired.detected"
        payload = first_call.args[1]
        assert payload["task_id"] == "test-task"
        assert payload["lease_id"] == lease.lease_id
        assert "detected_at" in payload

    @pytest.mark.asyncio
    async def test_recovered_event_published_on_success(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED, task_id="test-task")
        worker, _, event_bus = _make_worker()
        await worker.recover(lease)

        topics = [call.args[0] for call in event_bus.publish.await_args_list]
        assert "lease.recovered" in topics

    @pytest.mark.asyncio
    async def test_recovered_event_payload_structure(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED, task_id="task-xyz")
        worker, _, event_bus = _make_worker()
        await worker.recover(lease)

        recovered_call = next(
            call for call in event_bus.publish.await_args_list if call.args[0] == "lease.recovered"
        )
        payload = recovered_call.args[1]
        assert payload["lease_id"] == lease.lease_id
        assert payload["task_id"] == "task-xyz"
        assert "recovered_at" in payload
        assert "recovery_time_ms" in payload

    @pytest.mark.asyncio
    async def test_recovery_failed_event_payload_structure(self) -> None:
        lease = _make_lease(state=LeaseState.UNCLAIMED, expires_at=None, minutes_expired=None)
        worker, _, event_bus = _make_worker()
        await worker.recover(lease)

        failed_call = next(
            call
            for call in event_bus.publish.await_args_list
            if call.args[0] == "lease.recovery.failed"
        )
        payload = failed_call.args[1]
        assert payload["lease_id"] == lease.lease_id
        assert payload["task_id"] == lease.task_id
        assert "reason" in payload
        assert "detail" in payload
        assert "failed_at" in payload

    @pytest.mark.asyncio
    async def test_expired_detected_event_includes_state_value(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, _, event_bus = _make_worker()
        await worker.recover(lease)

        first = event_bus.publish.await_args_list[0]
        assert first.args[1]["state"] == "expired"

    @pytest.mark.asyncio
    async def test_requeued_lease_not_double_recovered(self) -> None:
        """A lease already in REQUEUED state cannot go to EXPIRED, returns False."""
        lease = _make_lease(state=LeaseState.REQUEUED, expires_at=None, minutes_expired=None)
        worker, lease_store, _ = _make_worker()
        result = await worker.recover(lease)
        assert result is False
        lease_store.save_lease.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. run() loop
# ---------------------------------------------------------------------------


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_stop_before_first_scan_exits_cleanly(self) -> None:
        """Calling stop() immediately prevents any scan from running."""
        worker, lease_store, _ = _make_worker(scan_interval=60.0)

        loop_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.02)
        assert worker.running is True

        await worker.stop()
        await asyncio.wait_for(loop_task, timeout=2.0)

        assert worker.running is False
        # scan interval is 60s, stop fires before interval elapses → no scan
        lease_store.list_leases.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_running_property_true_while_loop_active(self) -> None:
        worker, _, _ = _make_worker(scan_interval=60.0)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.02)
        assert worker.running is True
        await worker.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert worker.running is False

    @pytest.mark.asyncio
    async def test_single_scan_cycle_recovers_expired_leases(self) -> None:
        """After one interval, worker scans and recovers an expired lease."""
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, lease_store, event_bus = _make_worker(leases=[lease], scan_interval=0.05)

        scan_done = asyncio.Event()
        original_scan = worker.scan_expired

        async def patched_scan():
            result = await original_scan()
            scan_done.set()
            await worker.stop()
            return result

        worker.scan_expired = patched_scan

        await worker.run()

        lease_store.save_lease.assert_awaited()
        topics = [call.args[0] for call in event_bus.publish.await_args_list]
        assert "lease.recovered" in topics

    @pytest.mark.asyncio
    async def test_multiple_expired_leases_in_single_scan(self) -> None:
        """All expired leases found in one scan should all be recovered."""
        leases = [_make_lease(task_id=f"task-{i}") for i in range(4)]
        worker, lease_store, event_bus = _make_worker(leases=leases, scan_interval=0.05)

        original_scan = worker.scan_expired

        async def patched_scan():
            result = await original_scan()
            await worker.stop()
            return result

        worker.scan_expired = patched_scan

        await worker.run()

        assert lease_store.save_lease.await_count == 4

    @pytest.mark.asyncio
    async def test_scan_exception_does_not_crash_loop(self) -> None:
        """Exception inside scan_expired is caught and the loop continues."""
        call_count = 0

        worker, _, _ = _make_worker(scan_interval=0.05)

        async def failing_scan():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("scan explosion")
            await worker.stop()
            return []

        worker.scan_expired = failing_scan

        with patch("forge_harness.webhook_server.services.lease_recovery_v2.logger"):
            await worker.run()

        assert call_count >= 2
        assert worker.running is False

    @pytest.mark.asyncio
    async def test_recover_exception_does_not_crash_loop(self) -> None:
        """Exception inside recover() for one lease does not abort the loop or other leases."""
        leases = [
            _make_lease(task_id="task-fail"),
            _make_lease(task_id="task-ok"),
        ]
        worker, lease_store, _ = _make_worker(leases=leases, scan_interval=0.05)

        original_recover = worker.recover
        recover_call_count = 0

        async def patched_recover(lease: TaskLease) -> bool:
            nonlocal recover_call_count
            recover_call_count += 1
            if lease.task_id == "task-fail":
                raise RuntimeError("transient failure")
            return await original_recover(lease)

        worker.recover = patched_recover  # type: ignore[method-assign]

        original_scan = worker.scan_expired

        async def one_shot_scan():
            result = await original_scan()
            await worker.stop()
            return result

        worker.scan_expired = one_shot_scan

        await worker.run()

        # Both leases were attempted
        assert recover_call_count == 2
        # task-ok was saved despite task-fail raising
        assert lease_store.save_lease.await_count == 1

    @pytest.mark.asyncio
    async def test_already_running_returns_immediately(self) -> None:
        worker, lease_store, _ = _make_worker(scan_interval=60.0)
        worker._running = True

        with patch("forge_harness.webhook_server.services.lease_recovery_v2.logger") as mock_log:
            await worker.run()
            mock_log.warning.assert_called()

        lease_store.list_leases.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stats_accumulate_across_scans(self) -> None:
        """Stats must grow correctly over multiple scan cycles."""
        scan_count = 0

        worker, _, _ = _make_worker(scan_interval=0.05)

        async def counting_scan():
            nonlocal scan_count
            scan_count += 1
            if scan_count >= 3:
                await worker.stop()
            return []

        worker.scan_expired = counting_scan

        await worker.run()

        stats = worker.get_stats()
        assert stats.total_scans >= 2

    @pytest.mark.asyncio
    async def test_graceful_shutdown_via_stop(self) -> None:
        """stop() causes run() to exit cleanly with running=False."""
        worker, _, _ = _make_worker(scan_interval=60.0)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        await worker.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert worker.running is False


# ---------------------------------------------------------------------------
# 6. stop()
# ---------------------------------------------------------------------------


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_sets_stop_event(self) -> None:
        worker, _, _ = _make_worker()
        assert not worker._stop_event.is_set()
        await worker.stop()
        assert worker._stop_event.is_set()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self) -> None:
        worker, _, _ = _make_worker()
        await worker.stop()
        await worker.stop()
        assert worker._stop_event.is_set()

    @pytest.mark.asyncio
    async def test_stop_before_run_is_safe(self) -> None:
        worker, _, _ = _make_worker()
        await worker.stop()
        assert worker.running is False


# ---------------------------------------------------------------------------
# 7. get_stats()
# ---------------------------------------------------------------------------


class TestGetStats:
    @pytest.mark.asyncio
    async def test_returns_copy_not_reference(self) -> None:
        """Mutating the returned stats must not affect internal state."""
        worker, _, _ = _make_worker()
        stats_a = worker.get_stats()
        stats_a.total_scans = 9999

        stats_b = worker.get_stats()
        assert stats_b.total_scans == 0

    @pytest.mark.asyncio
    async def test_stats_reflect_successful_recoveries(self) -> None:
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, _, _ = _make_worker()
        await worker.recover(lease)
        stats = worker.get_stats()
        assert stats.total_recovered == 1
        assert stats.total_failed == 0

    @pytest.mark.asyncio
    async def test_stats_reflect_failed_recoveries(self) -> None:
        lease = _make_lease(state=LeaseState.UNCLAIMED, expires_at=None, minutes_expired=None)
        worker, _, _ = _make_worker()
        await worker.recover(lease)
        stats = worker.get_stats()
        assert stats.total_failed == 1
        assert stats.total_recovered == 0


# ---------------------------------------------------------------------------
# 8. Integration — end-to-end recovery workflow
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_recovery_workflow_state_machine(self) -> None:
        """Full path: expired lease → scan_expired → recover → REQUEUED state."""
        lease = _make_lease(
            task_id="integration-task",
            state=LeaseState.EXPIRED,
            owner_node="prya",
            owner_agent="forge:claude",
            path_lock="src/main.py",
        )
        worker, lease_store, event_bus = _make_worker(leases=[lease])

        # Run one scan cycle manually
        expired = await worker.scan_expired()
        assert len(expired) == 1

        result = await worker.recover(expired[0])

        assert result is True
        assert lease.state is LeaseState.REQUEUED
        assert lease.path_lock is None
        lease_store.save_lease.assert_awaited_once_with(lease)

        topics = [call.args[0] for call in event_bus.publish.await_args_list]
        assert topics[0] == "lease.expired.detected"
        assert "lease.recovered" in topics
        assert "lease.recovery.failed" not in topics

    @pytest.mark.asyncio
    async def test_event_sequence_for_invalid_state(self) -> None:
        """For an invalid transition: only detected + failed events, no recovered."""
        lease = _make_lease(
            state=LeaseState.UNCLAIMED,
            expires_at=None,
            minutes_expired=None,
        )
        worker, _, event_bus = _make_worker()
        await worker.recover(lease)

        topics = [call.args[0] for call in event_bus.publish.await_args_list]
        assert "lease.expired.detected" in topics
        assert "lease.recovery.failed" in topics
        assert "lease.recovered" not in topics

    @pytest.mark.asyncio
    async def test_multiple_scans_accumulate_recovery_count(self) -> None:
        """Run multiple manual scan+recover cycles and verify stat growth."""
        worker, _, _ = _make_worker()

        for i in range(5):
            lease = _make_lease(task_id=f"task-{i}", state=LeaseState.EXPIRED)
            await worker.recover(lease)

        stats = worker.get_stats()
        assert stats.total_recovered == 5
        assert stats.avg_recovery_time_ms is not None

    @pytest.mark.asyncio
    async def test_renewing_lease_normalized_to_requeued(self) -> None:
        """RENEWING lease with elapsed TTL: RENEWING→EXPIRED→REQUEUED."""
        lease = _make_lease(
            state=LeaseState.RENEWING,
            expires_at=datetime.utcnow() - timedelta(seconds=30),
            minutes_expired=None,
        )
        worker, _, _ = _make_worker()
        result = await worker.recover(lease)
        assert result is True
        assert lease.state is LeaseState.REQUEUED

    @pytest.mark.asyncio
    async def test_releasing_lease_cannot_be_recovered_as_expired(self) -> None:
        """RELEASING → EXPIRED is not a valid transition; recovery should fail gracefully."""
        lease = _make_lease(
            state=LeaseState.RELEASING,
            expires_at=datetime.utcnow() - timedelta(seconds=5),
            minutes_expired=None,
        )
        worker, lease_store, _ = _make_worker()
        result = await worker.recover(lease)
        # RELEASING is not allowed to go to EXPIRED per transition table
        assert result is False
        lease_store.save_lease.assert_not_awaited()
