"""Comprehensive unit tests for lease_recovery_v2.py (CP-2007).

Covers:
- RecoveryStats model: record_recovery, record_failure, record_scan
- LeaseRecoveryWorker.__init__: parameter clamping, attribute initialisation
- LeaseRecoveryWorker.scan_expired: normal path, EXPIRED-state leases,
  is_expired leases, mixed, store exception handling
- LeaseRecoveryWorker.recover: success path (EXPIRED state), success path
  (ACTIVE state needing intermediate transition), LeaseTransitionError,
  unexpected exception, event payloads
- LeaseRecoveryWorker.run: stop-before-first-scan, single-scan-then-stop,
  stats accumulation, scan exception swallowed, recover exception swallowed,
  double-run guard
- LeaseRecoveryWorker.stop: sets stop event
- LeaseRecoveryWorker.get_stats: returns copy

All external I/O is mocked; no real network, DB, or filesystem calls.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from forge_harness.models.lease import (
    LeaseState,
    LeaseTransitionError,
    TaskLease,
)
from forge_harness.webhook_server.services.lease_recovery_v2 import (
    LeaseRecoveryWorker,
    RecoveryStats,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(
    *,
    state: LeaseState = LeaseState.EXPIRED,
    expires_at: datetime | None = None,
    owner_node: str = "nova",
    owner_agent: str = "forge:claude",
    path_lock: str | None = "/tmp/test.py",
    task_id: str = "task-001",
) -> TaskLease:
    """Create a TaskLease for testing."""
    return TaskLease(
        task_id=task_id,
        state=state,
        expires_at=expires_at,
        owner_node=owner_node,
        owner_agent=owner_agent,
        path_lock=path_lock,
    )


def _make_active_expired_lease(task_id: str = "task-active") -> TaskLease:
    """Active lease whose TTL has already elapsed (is_expired() == True)."""
    return TaskLease(
        task_id=task_id,
        state=LeaseState.ACTIVE,
        expires_at=datetime.now(UTC) - timedelta(seconds=10),
        owner_node="nova",
        owner_agent="forge:claude",
        path_lock="/tmp/active.py",
    )


def _make_fresh_lease(task_id: str = "task-fresh") -> TaskLease:
    """Active lease that has NOT expired yet."""
    return TaskLease(
        task_id=task_id,
        state=LeaseState.ACTIVE,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        owner_node="nova",
        owner_agent="forge:claude",
    )


def _make_mock_store(leases: list[TaskLease] | None = None) -> AsyncMock:
    store = AsyncMock()
    store.list_leases = AsyncMock(return_value=leases or [])
    store.save_lease = AsyncMock(return_value=None)
    return store


def _make_mock_bus() -> AsyncMock:
    bus = AsyncMock()
    bus.publish = AsyncMock(return_value=None)
    return bus


def _make_worker(
    leases: list[TaskLease] | None = None,
    scan_interval: float = 60.0,
) -> tuple[LeaseRecoveryWorker, AsyncMock, AsyncMock]:
    store = _make_mock_store(leases)
    bus = _make_mock_bus()
    worker = LeaseRecoveryWorker(store, bus, scan_interval_seconds=scan_interval)
    return worker, store, bus


# ===========================================================================
# RecoveryStats
# ===========================================================================


class TestRecoveryStats:
    def test_defaults(self):
        stats = RecoveryStats()
        assert stats.total_scans == 0
        assert stats.total_recovered == 0
        assert stats.total_failed == 0
        assert stats.last_scan_at is None
        assert stats.avg_recovery_time_ms is None

    def test_record_scan_increments_and_sets_timestamp(self):
        stats = RecoveryStats()
        t = datetime.now(UTC)
        stats.record_scan(t)
        assert stats.total_scans == 1
        assert stats.last_scan_at == t

    def test_record_scan_multiple(self):
        stats = RecoveryStats()
        t1 = datetime(2026, 1, 1, tzinfo=UTC)
        t2 = datetime(2026, 1, 2, tzinfo=UTC)
        stats.record_scan(t1)
        stats.record_scan(t2)
        assert stats.total_scans == 2
        assert stats.last_scan_at == t2

    def test_record_recovery_single(self):
        stats = RecoveryStats()
        stats.record_recovery(100.0)
        assert stats.total_recovered == 1
        assert stats.avg_recovery_time_ms == pytest.approx(100.0)

    def test_record_recovery_rolling_average(self):
        stats = RecoveryStats()
        stats.record_recovery(100.0)
        stats.record_recovery(200.0)
        stats.record_recovery(300.0)
        assert stats.total_recovered == 3
        assert stats.avg_recovery_time_ms == pytest.approx(200.0)

    def test_record_failure_increments(self):
        stats = RecoveryStats()
        stats.record_failure()
        stats.record_failure()
        assert stats.total_failed == 2

    def test_independent_counters(self):
        stats = RecoveryStats()
        stats.record_recovery(50.0)
        stats.record_failure()
        stats.record_scan(datetime.now(UTC))
        assert stats.total_recovered == 1
        assert stats.total_failed == 1
        assert stats.total_scans == 1


# ===========================================================================
# LeaseRecoveryWorker.__init__
# ===========================================================================


class TestWorkerInit:
    def test_scan_interval_clamped_to_minimum(self):
        worker, _, _ = _make_worker(scan_interval=0.0)
        assert worker._scan_interval == 1.0

    def test_scan_interval_clamped_negative(self):
        worker, _, _ = _make_worker(scan_interval=-100.0)
        assert worker._scan_interval == 1.0

    def test_scan_interval_valid(self):
        worker, _, _ = _make_worker(scan_interval=30.0)
        assert worker._scan_interval == 30.0

    def test_running_false_initially(self):
        worker, _, _ = _make_worker()
        assert worker.running is False

    def test_stats_initialised(self):
        worker, _, _ = _make_worker()
        s = worker.get_stats()
        assert isinstance(s, RecoveryStats)
        assert s.total_scans == 0

    def test_stop_event_not_set_initially(self):
        worker, _, _ = _make_worker()
        assert not worker._stop_event.is_set()


# ===========================================================================
# scan_expired
# ===========================================================================


@pytest.mark.asyncio
class TestScanExpired:
    async def test_returns_empty_when_no_leases(self):
        worker, _, _ = _make_worker(leases=[])
        result = await worker.scan_expired()
        assert result == []

    async def test_returns_lease_in_expired_state(self):
        lease = _make_lease(state=LeaseState.EXPIRED, expires_at=None)
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert result == [lease]

    async def test_returns_active_lease_past_ttl(self):
        lease = _make_active_expired_lease()
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert result == [lease]

    async def test_skips_fresh_active_lease(self):
        lease = _make_fresh_lease()
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert result == []

    async def test_mixed_leases(self):
        expired_state = _make_lease(state=LeaseState.EXPIRED, task_id="t-expired")
        expired_ttl = _make_active_expired_lease(task_id="t-ttl")
        fresh = _make_fresh_lease(task_id="t-fresh")
        unclaimed = _make_lease(
            state=LeaseState.UNCLAIMED, expires_at=None, task_id="t-unclaimed"
        )
        worker, _, _ = _make_worker(leases=[expired_state, expired_ttl, fresh, unclaimed])
        result = await worker.scan_expired()
        assert expired_state in result
        assert expired_ttl in result
        assert fresh not in result
        assert unclaimed not in result

    async def test_store_exception_returns_empty_list(self):
        store = _make_mock_store()
        store.list_leases = AsyncMock(side_effect=RuntimeError("db down"))
        bus = _make_mock_bus()
        worker = LeaseRecoveryWorker(store, bus, scan_interval_seconds=1.0)
        result = await worker.scan_expired()
        assert result == []

    async def test_lease_without_expires_at_not_expired(self):
        lease = _make_lease(state=LeaseState.CLAIMED, expires_at=None)
        worker, _, _ = _make_worker(leases=[lease])
        result = await worker.scan_expired()
        assert result == []


# ===========================================================================
# recover — success paths
# ===========================================================================


@pytest.mark.asyncio
class TestRecoverSuccess:
    async def test_recover_expired_state_lease(self):
        """Lease already in EXPIRED state should transition to REQUEUED."""
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker()

        result = await worker.recover(lease)

        assert result is True
        assert lease.state is LeaseState.REQUEUED
        assert lease.owner_node == ""
        assert lease.owner_agent == ""
        assert lease.path_lock is None
        store.save_lease.assert_awaited_once_with(lease)

    async def test_recover_active_expired_lease_two_step_transition(self):
        """ACTIVE lease with elapsed TTL should go ACTIVE -> EXPIRED -> REQUEUED."""
        lease = _make_active_expired_lease()
        worker, store, bus = _make_worker()

        result = await worker.recover(lease)

        assert result is True
        assert lease.state is LeaseState.REQUEUED
        store.save_lease.assert_awaited_once()

    async def test_recover_increments_stats(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, _, _ = _make_worker()

        await worker.recover(lease)

        stats = worker.get_stats()
        assert stats.total_recovered == 1
        assert stats.avg_recovery_time_ms is not None
        assert stats.avg_recovery_time_ms >= 0.0

    async def test_recover_publishes_expired_detected_event(self):
        lease = _make_lease(state=LeaseState.EXPIRED, task_id="t-pub")
        worker, _, bus = _make_worker()

        await worker.recover(lease)

        topics = [call.args[0] for call in bus.publish.await_args_list]
        assert "lease.expired.detected" in topics

    async def test_recover_publishes_recovered_event(self):
        lease = _make_lease(state=LeaseState.EXPIRED, task_id="t-rec")
        worker, _, bus = _make_worker()

        await worker.recover(lease)

        topics = [call.args[0] for call in bus.publish.await_args_list]
        assert "lease.recovered" in topics

    async def test_recover_expired_detected_payload_fields(self):
        lease = _make_lease(
            state=LeaseState.EXPIRED,
            task_id="t-payload",
            owner_node="prya",
            owner_agent="forge:glm",
            path_lock="/tmp/x.py",
        )
        worker, _, bus = _make_worker()

        await worker.recover(lease)

        detected_call = next(
            c for c in bus.publish.await_args_list if c.args[0] == "lease.expired.detected"
        )
        payload: dict[str, Any] = detected_call.args[1]
        assert payload["lease_id"] == lease.lease_id
        assert payload["task_id"] == "t-payload"
        assert "detected_at" in payload
        assert "expires_at" in payload

    async def test_recover_recovered_payload_fields(self):
        lease = _make_lease(state=LeaseState.EXPIRED, task_id="t-rpayload")
        worker, _, bus = _make_worker()

        await worker.recover(lease)

        recovered_call = next(
            c for c in bus.publish.await_args_list if c.args[0] == "lease.recovered"
        )
        payload: dict[str, Any] = recovered_call.args[1]
        assert payload["lease_id"] == lease.lease_id
        assert payload["task_id"] == "t-rpayload"
        assert "recovered_at" in payload
        assert "recovery_time_ms" in payload

    async def test_recover_clears_path_lock(self):
        lease = _make_lease(state=LeaseState.EXPIRED, path_lock="/src/api.py")
        worker, _, _ = _make_worker()

        await worker.recover(lease)

        assert lease.path_lock is None

    async def test_recover_renewing_state_success(self):
        """RENEWING state is allowed to transition to EXPIRED per contract."""
        lease = _make_lease(state=LeaseState.RENEWING)
        worker, store, bus = _make_worker()

        result = await worker.recover(lease)

        assert result is True
        assert lease.state is LeaseState.REQUEUED


# ===========================================================================
# recover — failure paths
# ===========================================================================


@pytest.mark.asyncio
class TestRecoverFailure:
    async def test_illegal_transition_returns_false(self):
        """UNCLAIMED → EXPIRED is not a valid transition; should return False."""
        lease = _make_lease(state=LeaseState.UNCLAIMED, expires_at=None)
        worker, store, bus = _make_worker()

        result = await worker.recover(lease)

        assert result is False
        store.save_lease.assert_not_awaited()

    async def test_illegal_transition_publishes_recovery_failed(self):
        lease = _make_lease(state=LeaseState.UNCLAIMED, expires_at=None)
        worker, _, bus = _make_worker()

        await worker.recover(lease)

        topics = [c.args[0] for c in bus.publish.await_args_list]
        assert "lease.recovery.failed" in topics

    async def test_illegal_transition_increments_failure_stats(self):
        lease = _make_lease(state=LeaseState.UNCLAIMED, expires_at=None)
        worker, _, _ = _make_worker()

        await worker.recover(lease)

        assert worker.get_stats().total_failed == 1
        assert worker.get_stats().total_recovered == 0

    async def test_save_lease_exception_reraises(self):
        """Unexpected exception from save_lease must propagate."""
        lease = _make_lease(state=LeaseState.EXPIRED)
        store = _make_mock_store()
        store.save_lease = AsyncMock(side_effect=OSError("disk full"))
        bus = _make_mock_bus()
        worker = LeaseRecoveryWorker(store, bus)

        with pytest.raises(IOError, match="disk full"):
            await worker.recover(lease)

    async def test_save_lease_exception_publishes_failed_event(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        store = _make_mock_store()
        store.save_lease = AsyncMock(side_effect=OSError("disk full"))
        bus = _make_mock_bus()
        worker = LeaseRecoveryWorker(store, bus)

        with pytest.raises(IOError):
            await worker.recover(lease)

        topics = [c.args[0] for c in bus.publish.await_args_list]
        assert "lease.recovery.failed" in topics

    async def test_save_lease_exception_increments_failure_stats(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        store = _make_mock_store()
        store.save_lease = AsyncMock(side_effect=OSError("disk full"))
        bus = _make_mock_bus()
        worker = LeaseRecoveryWorker(store, bus)

        with pytest.raises(IOError):
            await worker.recover(lease)

        assert worker.get_stats().total_failed == 1

    async def test_recovery_failed_payload_has_reason(self):
        lease = _make_lease(state=LeaseState.UNCLAIMED, expires_at=None)
        worker, _, bus = _make_worker()

        await worker.recover(lease)

        failed_call = next(
            c for c in bus.publish.await_args_list if c.args[0] == "lease.recovery.failed"
        )
        payload = failed_call.args[1]
        assert payload["reason"] == "invalid_state_transition"
        assert "detail" in payload
        assert "failed_at" in payload

    async def test_unexpected_error_payload_has_unexpected_reason(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        store = _make_mock_store()
        store.save_lease = AsyncMock(side_effect=ValueError("boom"))
        bus = _make_mock_bus()
        worker = LeaseRecoveryWorker(store, bus)

        with pytest.raises(ValueError):
            await worker.recover(lease)

        failed_call = next(
            c for c in bus.publish.await_args_list if c.args[0] == "lease.recovery.failed"
        )
        payload = failed_call.args[1]
        assert payload["reason"] == "unexpected_error"

    async def test_requeued_state_transition_illegal(self):
        """REQUEUED → EXPIRED is not valid; should return False gracefully."""
        lease = _make_lease(state=LeaseState.REQUEUED, expires_at=None)
        worker, store, _ = _make_worker()

        result = await worker.recover(lease)

        assert result is False
        store.save_lease.assert_not_awaited()


# ===========================================================================
# run — async loop
# ===========================================================================


@pytest.mark.asyncio
class TestRunLoop:
    async def test_stop_before_first_scan(self):
        """stop() during the initial sleep should exit without scanning."""
        worker, store, bus = _make_worker(scan_interval=60.0)

        async def _auto_stop():
            # Allow one event-loop tick then stop
            await asyncio.sleep(0)
            await worker.stop()

        await asyncio.gather(worker.run(), _auto_stop())

        assert not worker.running
        store.list_leases.assert_not_awaited()

    async def test_running_true_while_active(self):
        """running property should be True while the loop executes."""
        worker, store, bus = _make_worker(scan_interval=1.0)
        running_states: list[bool] = []

        async def _probe_then_stop():
            await asyncio.sleep(0)
            running_states.append(worker.running)
            await worker.stop()

        await asyncio.gather(worker.run(), _probe_then_stop())

        assert True in running_states
        assert not worker.running

    async def test_single_scan_then_stop(self):
        """Worker should call scan_expired once if stopped after the first scan."""
        expired = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker(leases=[expired], scan_interval=1.0)

        # Patch scan_interval to something tiny so the timeout fires fast
        worker._scan_interval = 0.05

        async def _stop_after_scan():
            # Wait long enough for one scan to complete
            await asyncio.sleep(0.3)
            await worker.stop()

        await asyncio.gather(worker.run(), _stop_after_scan())

        store.list_leases.assert_awaited()

    async def test_stats_updated_after_scan(self):
        """After a scan, total_scans should be incremented."""
        expired = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker(leases=[expired])
        worker._scan_interval = 0.05

        async def _stop():
            await asyncio.sleep(0.3)
            await worker.stop()

        await asyncio.gather(worker.run(), _stop())

        assert worker.get_stats().total_scans >= 1

    async def test_scan_exception_does_not_kill_loop(self):
        """An exception from scan_expired must be logged and the loop must continue."""
        store = _make_mock_store()
        # First call raises, second succeeds
        store.list_leases = AsyncMock(
            side_effect=[RuntimeError("db down"), []]
        )
        bus = _make_mock_bus()
        worker = LeaseRecoveryWorker(store, bus, scan_interval_seconds=1.0)
        worker._scan_interval = 0.05

        async def _stop():
            await asyncio.sleep(0.3)
            await worker.stop()

        # Should NOT raise
        await asyncio.gather(worker.run(), _stop())

    async def test_scan_expired_method_raise_handled_in_run(self):
        """If scan_expired() itself raises (not just the store), run() continues."""
        worker, store, bus = _make_worker()
        worker._scan_interval = 0.05

        # Patch scan_expired on the instance to raise on first call, then return []
        call_count = 0
        original_scan = worker.scan_expired

        async def _patched_scan():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("scan_expired internal failure")
            return []

        worker.scan_expired = _patched_scan  # type: ignore[method-assign]

        async def _stop():
            await asyncio.sleep(0.3)
            await worker.stop()

        # Should NOT raise; loop logs and continues
        await asyncio.gather(worker.run(), _stop())
        assert call_count >= 1

    async def test_recover_exception_swallowed_in_loop(self):
        """An exception inside recover() must not propagate out of run()."""
        expired = _make_lease(state=LeaseState.EXPIRED)
        worker, store, bus = _make_worker(leases=[expired])
        worker._scan_interval = 0.05

        # Make save_lease raise every time
        store.save_lease = AsyncMock(side_effect=RuntimeError("kaboom"))

        async def _stop():
            await asyncio.sleep(0.3)
            await worker.stop()

        # run() must not raise even though recover() raises
        await asyncio.gather(worker.run(), _stop())

    async def test_double_run_is_a_noop(self):
        """Calling run() while already running should return immediately."""
        worker, store, bus = _make_worker(scan_interval=1.0)

        # Patch _running to simulate already-running state
        worker._running = True

        # This call should return immediately without blocking
        await asyncio.wait_for(worker.run(), timeout=0.5)

        # _running stays True because we set it manually (run() returned early)
        assert worker._running is True

    async def test_running_false_after_stop(self):
        worker, store, bus = _make_worker(scan_interval=1.0)
        worker._scan_interval = 0.05

        async def _stop():
            await asyncio.sleep(0.1)
            await worker.stop()

        await asyncio.gather(worker.run(), _stop())

        assert worker.running is False


# ===========================================================================
# stop
# ===========================================================================


@pytest.mark.asyncio
class TestStop:
    async def test_stop_sets_event(self):
        worker, _, _ = _make_worker()
        assert not worker._stop_event.is_set()
        await worker.stop()
        assert worker._stop_event.is_set()

    async def test_stop_idempotent(self):
        worker, _, _ = _make_worker()
        await worker.stop()
        await worker.stop()  # Should not raise
        assert worker._stop_event.is_set()


# ===========================================================================
# get_stats
# ===========================================================================


class TestGetStats:
    def test_returns_copy(self):
        worker, _, _ = _make_worker()
        s1 = worker.get_stats()
        s2 = worker.get_stats()
        # Mutating one copy must not affect the other or the internal state
        s1.total_scans = 999
        assert worker.get_stats().total_scans == 0

    def test_returns_recovery_stats_instance(self):
        worker, _, _ = _make_worker()
        assert isinstance(worker.get_stats(), RecoveryStats)


# ===========================================================================
# Protocol compliance (structural typing sanity checks)
# ===========================================================================


class TestProtocolCompliance:
    def test_lease_store_protocol_requires_list_and_save(self):
        """runtime_checkable Protocol checks for attribute presence, not callability.
        Use a concrete class with the required attributes to satisfy isinstance."""
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseStore

        class ConcreteStore:
            async def list_leases(self):
                return []

            async def save_lease(self, lease):
                pass

        assert isinstance(ConcreteStore(), LeaseStore)

    def test_event_bus_protocol_requires_publish(self):
        from forge_harness.webhook_server.services.lease_recovery_v2 import EventBus

        class ConcreteBus:
            async def publish(self, topic, payload):
                pass

        assert isinstance(ConcreteBus(), EventBus)

    def test_object_missing_publish_not_eventbus(self):
        from forge_harness.webhook_server.services.lease_recovery_v2 import EventBus

        class NoBus:
            pass

        assert not isinstance(NoBus(), EventBus)

    def test_object_missing_save_lease_not_store(self):
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseStore

        class IncompleteStore:
            async def list_leases(self):
                return []
            # save_lease is intentionally absent

        assert not isinstance(IncompleteStore(), LeaseStore)


# ===========================================================================
# Edge-cases / guard rails
# ===========================================================================


@pytest.mark.asyncio
class TestEdgeCases:
    async def test_lease_with_none_expires_at_and_expired_state(self):
        """EXPIRED state with no expires_at should still be recovered."""
        lease = TaskLease(task_id="t-none", state=LeaseState.EXPIRED, expires_at=None)
        worker, store, bus = _make_worker()

        result = await worker.recover(lease)

        assert result is True

    async def test_recover_claimed_state_to_expired_then_requeued(self):
        """CLAIMED is a valid predecessor of EXPIRED per the state machine."""
        lease = _make_lease(state=LeaseState.CLAIMED, expires_at=None)
        worker, store, bus = _make_worker()

        result = await worker.recover(lease)

        assert result is True
        assert lease.state is LeaseState.REQUEUED

    async def test_multiple_leases_recovered_in_run(self):
        """All expired leases in a scan batch should be recovered."""
        leases = [
            _make_lease(state=LeaseState.EXPIRED, task_id=f"t-{i}") for i in range(5)
        ]
        worker, store, bus = _make_worker(leases=leases)
        worker._scan_interval = 0.05

        async def _stop():
            await asyncio.sleep(0.3)
            await worker.stop()

        await asyncio.gather(worker.run(), _stop())

        assert worker.get_stats().total_recovered >= 5

    async def test_recovery_time_ms_positive(self):
        lease = _make_lease(state=LeaseState.EXPIRED)
        worker, _, _ = _make_worker()

        await worker.recover(lease)

        stats = worker.get_stats()
        assert stats.avg_recovery_time_ms is not None
        assert stats.avg_recovery_time_ms >= 0.0

    async def test_scan_expired_calls_list_leases_once(self):
        worker, store, _ = _make_worker(leases=[])
        await worker.scan_expired()
        store.list_leases.assert_awaited_once()
