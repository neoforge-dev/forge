"""Unit tests for forge_harness.webhook_server.services.lane_enforcer.

Coverage targets: LaneEnforcer (all public methods + edge cases),
get_lane_enforcer singleton, reset_lane_enforcer singleton utility.

All external dependencies (LaneResolver, get_lane_config, logger) are mocked
where needed to isolate the enforcer logic from the underlying models.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from unittest.mock import MagicMock, call, patch

import pytest

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import (
    DEFAULT_LANE_CONFIGS,
    WorkCellConfig,
    WorkCellLane,
)
from forge_harness.webhook_server.services import lane_enforcer as _module
from forge_harness.webhook_server.services.lane_enforcer import (
    LaneEnforcer,
    get_lane_enforcer,
    reset_lane_enforcer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(max_wip: int = 3) -> WorkCellConfig:
    """Return a minimal WorkCellConfig with the given max_wip."""
    return WorkCellConfig(
        lane=WorkCellLane.api_simple,
        max_wip=max_wip,
        timeout_seconds=600,
        auto_requeue=True,
        evaluator_profile_name="lenient",
        requires_human_review=False,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Guarantee a clean singleton before and after every test."""
    reset_lane_enforcer()
    yield
    reset_lane_enforcer()


@pytest.fixture()
def enforcer() -> LaneEnforcer:
    """Return a fresh LaneEnforcer instance (not the singleton)."""
    return LaneEnforcer()


# ---------------------------------------------------------------------------
# TestLaneEnforcerInit
# ---------------------------------------------------------------------------

class TestLaneEnforcerInit:
    """Verify that a freshly constructed LaneEnforcer starts with clean state."""

    def test_assignments_empty(self, enforcer: LaneEnforcer):
        assert enforcer._assignments == {}

    def test_active_is_defaultdict(self, enforcer: LaneEnforcer):
        # Accessing a new key should return 0 (defaultdict(int)).
        assert enforcer._active[WorkCellLane.api_simple] == 0

    def test_queued_is_defaultdict(self, enforcer: LaneEnforcer):
        assert enforcer._queued[WorkCellLane.api_simple] == 0

    def test_completed_is_defaultdict(self, enforcer: LaneEnforcer):
        assert enforcer._completed[WorkCellLane.api_simple] == 0

    def test_resolver_created(self, enforcer: LaneEnforcer):
        from forge_harness.webhook_server.models.work_cell import LaneResolver
        assert isinstance(enforcer._resolver, LaneResolver)

    def test_lock_is_rlock(self, enforcer: LaneEnforcer):
        # RLock objects don't expose a public class name directly; we
        # verify it is reentrant by acquiring it twice from the same thread.
        acquired = []
        with enforcer._lock:
            with enforcer._lock:
                acquired.append(True)
        assert acquired == [True]


# ---------------------------------------------------------------------------
# TestCheckWip
# ---------------------------------------------------------------------------

class TestCheckWip:
    """Tests for LaneEnforcer.check_wip."""

    def test_returns_true_when_no_active_tasks(self, enforcer: LaneEnforcer):
        # By default active count is 0, max_wip for api_simple is 10.
        assert enforcer.check_wip(WorkCellLane.api_simple) is True

    def test_returns_true_below_max_wip(self, enforcer: LaneEnforcer):
        enforcer._active[WorkCellLane.api_simple] = 9  # max_wip = 10
        assert enforcer.check_wip(WorkCellLane.api_simple) is True

    def test_returns_false_at_max_wip(self, enforcer: LaneEnforcer):
        config = DEFAULT_LANE_CONFIGS[WorkCellLane.api_simple]
        enforcer._active[WorkCellLane.api_simple] = config.max_wip
        assert enforcer.check_wip(WorkCellLane.api_simple) is False

    def test_returns_false_above_max_wip(self, enforcer: LaneEnforcer):
        config = DEFAULT_LANE_CONFIGS[WorkCellLane.api_simple]
        enforcer._active[WorkCellLane.api_simple] = config.max_wip + 5
        assert enforcer.check_wip(WorkCellLane.api_simple) is False

    def test_deployment_lane_max_wip_is_one(self, enforcer: LaneEnforcer):
        # deployment has max_wip=1; one active task should block.
        enforcer._active[WorkCellLane.deployment] = 0
        assert enforcer.check_wip(WorkCellLane.deployment) is True
        enforcer._active[WorkCellLane.deployment] = 1
        assert enforcer.check_wip(WorkCellLane.deployment) is False

    def test_security_change_lane_max_wip_is_two(self, enforcer: LaneEnforcer):
        # security_change has max_wip=2
        enforcer._active[WorkCellLane.security_change] = 1
        assert enforcer.check_wip(WorkCellLane.security_change) is True
        enforcer._active[WorkCellLane.security_change] = 2
        assert enforcer.check_wip(WorkCellLane.security_change) is False

    def test_check_wip_uses_get_lane_config(self, enforcer: LaneEnforcer):
        """check_wip must delegate to get_lane_config for the WIP limit."""
        fake_config = _make_config(max_wip=1)
        with patch(
            "forge_harness.webhook_server.services.lane_enforcer.get_lane_config",
            return_value=fake_config,
        ) as mock_cfg:
            enforcer._active[WorkCellLane.api_simple] = 0
            result = enforcer.check_wip(WorkCellLane.api_simple)
            mock_cfg.assert_called_once_with(WorkCellLane.api_simple)
            assert result is True

    def test_check_wip_all_lanes_with_zero_active(self, enforcer: LaneEnforcer):
        """All lanes should report capacity when active count is 0."""
        for lane in WorkCellLane:
            assert enforcer.check_wip(lane) is True


# ---------------------------------------------------------------------------
# TestAssignLane
# ---------------------------------------------------------------------------

class TestAssignLane:
    """Tests for LaneEnforcer.assign_lane."""

    def test_returns_correct_lane_for_bug_fix_low(self, enforcer: LaneEnforcer):
        lane = enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_returns_correct_lane_for_security_change(self, enforcer: LaneEnforcer):
        lane = enforcer.assign_lane("t1", TaskType.security_change, RiskTier.high)
        assert lane == WorkCellLane.security_change

    def test_records_assignment(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("task-abc", TaskType.test_writing, RiskTier.low)
        assert enforcer._assignments["task-abc"] == WorkCellLane.test_writing

    def test_increments_active_when_capacity_available(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        # api_simple lane, max_wip=10 — should be active
        assert enforcer._active[WorkCellLane.api_simple] == 1
        assert enforcer._queued[WorkCellLane.api_simple] == 0

    def test_increments_queued_when_lane_full(self, enforcer: LaneEnforcer):
        # Fill up the deployment lane (max_wip=1).
        enforcer._active[WorkCellLane.deployment] = 1
        enforcer.assign_lane("t1", TaskType.deployment, RiskTier.low)
        assert enforcer._queued[WorkCellLane.deployment] == 1
        assert enforcer._active[WorkCellLane.deployment] == 1  # unchanged

    def test_idempotent_for_same_task_id(self, enforcer: LaneEnforcer):
        lane1 = enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        lane2 = enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        assert lane1 == lane2
        # Counter must NOT be incremented a second time.
        assert enforcer._active[WorkCellLane.api_simple] == 1

    def test_idempotent_does_not_change_counters_on_second_call(
        self, enforcer: LaneEnforcer
    ):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        active_before = enforcer._active[WorkCellLane.api_simple]
        queued_before = enforcer._queued[WorkCellLane.api_simple]
        # Second call — same task_id, different type/tier should not matter.
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.high)
        assert enforcer._active[WorkCellLane.api_simple] == active_before
        assert enforcer._queued[WorkCellLane.api_simple] == queued_before

    def test_multiple_tasks_same_lane(self, enforcer: LaneEnforcer):
        for i in range(5):
            enforcer.assign_lane(f"task-{i}", TaskType.bug_fix, RiskTier.low)
        # api_simple max_wip=10, all 5 should be active
        assert enforcer._active[WorkCellLane.api_simple] == 5
        assert enforcer._queued[WorkCellLane.api_simple] == 0

    def test_tasks_beyond_max_wip_are_queued(self, enforcer: LaneEnforcer):
        # deployment lane max_wip=1
        enforcer.assign_lane("t1", TaskType.deployment, RiskTier.low)  # active
        enforcer.assign_lane("t2", TaskType.deployment, RiskTier.low)  # queued
        enforcer.assign_lane("t3", TaskType.deployment, RiskTier.low)  # queued
        assert enforcer._active[WorkCellLane.deployment] == 1
        assert enforcer._queued[WorkCellLane.deployment] == 2

    def test_different_tasks_different_lanes(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t-doc", TaskType.docs_update, RiskTier.low)
        enforcer.assign_lane("t-sec", TaskType.security_change, RiskTier.medium)
        assert enforcer._assignments["t-doc"] == WorkCellLane.docs
        assert enforcer._assignments["t-sec"] == WorkCellLane.security_change

    def test_resolver_called_with_correct_args(self, enforcer: LaneEnforcer):
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = WorkCellLane.api_simple
        enforcer._resolver = mock_resolver
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        mock_resolver.resolve.assert_called_once_with(TaskType.bug_fix, RiskTier.low)

    def test_resolver_not_called_on_idempotent_second_call(
        self, enforcer: LaneEnforcer
    ):
        # First assign
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        mock_resolver = MagicMock()
        enforcer._resolver = mock_resolver
        # Second call with same task_id must short-circuit before resolve.
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        mock_resolver.resolve.assert_not_called()


# ---------------------------------------------------------------------------
# TestCompleteTask
# ---------------------------------------------------------------------------

class TestCompleteTask:
    """Tests for LaneEnforcer.complete_task."""

    def test_returns_none_for_unknown_task(self, enforcer: LaneEnforcer):
        result = enforcer.complete_task("nonexistent-task")
        assert result is None

    def test_returns_lane_for_known_task(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        result = enforcer.complete_task("t1")
        assert result == WorkCellLane.api_simple

    def test_decrements_active_counter(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        assert enforcer._active[WorkCellLane.api_simple] == 1
        enforcer.complete_task("t1")
        assert enforcer._active[WorkCellLane.api_simple] == 0

    def test_increments_completed_counter(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.complete_task("t1")
        assert enforcer._completed[WorkCellLane.api_simple] == 1

    def test_active_never_goes_below_zero(self, enforcer: LaneEnforcer):
        # Manually assign without incrementing active counter.
        enforcer._assignments["t1"] = WorkCellLane.api_simple
        # active is 0 — complete should clamp, not underflow.
        enforcer.complete_task("t1")
        assert enforcer._active[WorkCellLane.api_simple] == 0

    def test_promotes_queued_task_when_capacity_freed(self, enforcer: LaneEnforcer):
        # deployment max_wip=1: first task active, second queued.
        enforcer.assign_lane("t1", TaskType.deployment, RiskTier.low)
        enforcer.assign_lane("t2", TaskType.deployment, RiskTier.low)
        assert enforcer._active[WorkCellLane.deployment] == 1
        assert enforcer._queued[WorkCellLane.deployment] == 1

        enforcer.complete_task("t1")
        # The queued task should now be promoted to active.
        assert enforcer._active[WorkCellLane.deployment] == 1
        assert enforcer._queued[WorkCellLane.deployment] == 0
        assert enforcer._completed[WorkCellLane.deployment] == 1

    def test_no_promotion_when_no_queued_tasks(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.complete_task("t1")
        # No queued tasks to promote.
        assert enforcer._active[WorkCellLane.api_simple] == 0
        assert enforcer._queued[WorkCellLane.api_simple] == 0

    def test_multiple_completions_accumulate_completed_count(
        self, enforcer: LaneEnforcer
    ):
        for i in range(4):
            enforcer.assign_lane(f"t{i}", TaskType.docs_update, RiskTier.low)
        for i in range(4):
            enforcer.complete_task(f"t{i}")
        assert enforcer._completed[WorkCellLane.docs] == 4

    def test_completing_unknown_task_twice_returns_none_both_times(
        self, enforcer: LaneEnforcer
    ):
        assert enforcer.complete_task("ghost") is None
        assert enforcer.complete_task("ghost") is None

    def test_complete_task_promotes_only_one_queued_at_a_time(
        self, enforcer: LaneEnforcer
    ):
        # Fill deployment (max_wip=1) + add 3 queued.
        enforcer.assign_lane("t0", TaskType.deployment, RiskTier.low)  # active
        enforcer.assign_lane("t1", TaskType.deployment, RiskTier.low)  # queued
        enforcer.assign_lane("t2", TaskType.deployment, RiskTier.low)  # queued
        enforcer.assign_lane("t3", TaskType.deployment, RiskTier.low)  # queued

        assert enforcer._active[WorkCellLane.deployment] == 1
        assert enforcer._queued[WorkCellLane.deployment] == 3

        enforcer.complete_task("t0")
        # Exactly one queued task should be promoted.
        assert enforcer._active[WorkCellLane.deployment] == 1
        assert enforcer._queued[WorkCellLane.deployment] == 2

    def test_complete_task_does_not_promote_when_still_at_max_wip(
        self, enforcer: LaneEnforcer
    ):
        # security_change max_wip=2; place 2 active + 1 queued
        # We need 2 active in security_change lane.
        enforcer.assign_lane("t1", TaskType.security_change, RiskTier.low)
        enforcer.assign_lane("t2", TaskType.security_change, RiskTier.low)
        # Manually push a third into queued (lane is now full).
        enforcer._assignments["t3"] = WorkCellLane.security_change
        enforcer._queued[WorkCellLane.security_change] = 1

        # Complete t1 — active goes from 2 to 1; capacity opens up (1 < 2).
        enforcer.complete_task("t1")
        # One queued task should be promoted.
        assert enforcer._active[WorkCellLane.security_change] == 2
        assert enforcer._queued[WorkCellLane.security_change] == 0


# ---------------------------------------------------------------------------
# TestGetLaneStats
# ---------------------------------------------------------------------------

class TestGetLaneStats:
    """Tests for LaneEnforcer.get_lane_stats."""

    def test_returns_all_lanes(self, enforcer: LaneEnforcer):
        stats = enforcer.get_lane_stats()
        for lane in WorkCellLane:
            assert lane.value in stats

    def test_initial_stats_all_zero(self, enforcer: LaneEnforcer):
        stats = enforcer.get_lane_stats()
        for lane in WorkCellLane:
            entry = stats[lane.value]
            assert entry["active"] == 0
            assert entry["queued"] == 0
            assert entry["completed"] == 0

    def test_max_wip_matches_default_config(self, enforcer: LaneEnforcer):
        stats = enforcer.get_lane_stats()
        for lane in WorkCellLane:
            expected_max_wip = DEFAULT_LANE_CONFIGS[lane].max_wip
            assert stats[lane.value]["max_wip"] == expected_max_wip

    def test_stats_reflect_active_task(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.api_simple.value]["active"] == 1
        assert stats[WorkCellLane.api_simple.value]["queued"] == 0

    def test_stats_reflect_queued_task(self, enforcer: LaneEnforcer):
        enforcer._active[WorkCellLane.deployment] = 1  # fill the lane
        enforcer.assign_lane("t1", TaskType.deployment, RiskTier.low)
        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.deployment.value]["queued"] == 1

    def test_stats_reflect_completed_task(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.complete_task("t1")
        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.api_simple.value]["completed"] == 1
        assert stats[WorkCellLane.api_simple.value]["active"] == 0

    def test_stats_keys_are_strings(self, enforcer: LaneEnforcer):
        stats = enforcer.get_lane_stats()
        for key in stats:
            assert isinstance(key, str)

    def test_stats_snapshot_is_independent_of_later_mutations(
        self, enforcer: LaneEnforcer
    ):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        snapshot = enforcer.get_lane_stats()
        api_simple_active_before = snapshot[WorkCellLane.api_simple.value]["active"]

        # Mutate state after snapshot.
        enforcer.assign_lane("t2", TaskType.bug_fix, RiskTier.low)
        # Snapshot should not have changed.
        assert snapshot[WorkCellLane.api_simple.value]["active"] == api_simple_active_before

    def test_stats_entries_have_required_keys(self, enforcer: LaneEnforcer):
        stats = enforcer.get_lane_stats()
        required_keys = {"active", "queued", "completed", "max_wip"}
        for lane_stats in stats.values():
            assert required_keys == set(lane_stats.keys())


# ---------------------------------------------------------------------------
# TestGetAssignment
# ---------------------------------------------------------------------------

class TestGetAssignment:
    """Tests for LaneEnforcer.get_assignment."""

    def test_returns_none_for_unknown_task(self, enforcer: LaneEnforcer):
        assert enforcer.get_assignment("no-such-task") is None

    def test_returns_lane_for_assigned_task(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.test_writing, RiskTier.medium)
        assert enforcer.get_assignment("t1") == WorkCellLane.test_writing

    def test_returns_none_after_reset(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        assert enforcer.get_assignment("t1") is None

    def test_returns_correct_lane_for_multiple_tasks(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.assign_lane("t2", TaskType.security_change, RiskTier.high)
        assert enforcer.get_assignment("t1") == WorkCellLane.api_simple
        assert enforcer.get_assignment("t2") == WorkCellLane.security_change


# ---------------------------------------------------------------------------
# TestReset
# ---------------------------------------------------------------------------

class TestReset:
    """Tests for LaneEnforcer.reset."""

    def test_clears_assignments(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        assert enforcer._assignments == {}

    def test_clears_active(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        assert dict(enforcer._active) == {}

    def test_clears_queued(self, enforcer: LaneEnforcer):
        enforcer._active[WorkCellLane.deployment] = 1
        enforcer.assign_lane("t1", TaskType.deployment, RiskTier.low)
        enforcer.reset()
        assert dict(enforcer._queued) == {}

    def test_clears_completed(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.complete_task("t1")
        enforcer.reset()
        assert dict(enforcer._completed) == {}

    def test_enforcer_works_normally_after_reset(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        lane = enforcer.assign_lane("t2", TaskType.docs_update, RiskTier.low)
        assert lane == WorkCellLane.docs
        assert enforcer._active[WorkCellLane.docs] == 1

    def test_reset_is_idempotent(self, enforcer: LaneEnforcer):
        enforcer.reset()
        enforcer.reset()
        assert enforcer._assignments == {}


# ---------------------------------------------------------------------------
# TestGetLaneEnforcer (singleton)
# ---------------------------------------------------------------------------

class TestGetLaneEnforcer:
    """Tests for the get_lane_enforcer singleton factory."""

    def test_returns_lane_enforcer_instance(self):
        instance = get_lane_enforcer()
        assert isinstance(instance, LaneEnforcer)

    def test_returns_same_instance_on_subsequent_calls(self):
        instance1 = get_lane_enforcer()
        instance2 = get_lane_enforcer()
        assert instance1 is instance2

    def test_new_instance_after_reset(self):
        instance1 = get_lane_enforcer()
        reset_lane_enforcer()
        instance2 = get_lane_enforcer()
        assert instance1 is not instance2

    def test_singleton_is_globally_shared(self):
        inst_a = get_lane_enforcer()
        inst_a.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        inst_b = get_lane_enforcer()
        # Both references point to the same object with the same state.
        assert inst_b.get_assignment("t1") == WorkCellLane.api_simple

    def test_thread_safety_of_singleton_creation(self):
        """Multiple threads calling get_lane_enforcer() should all get the same instance."""
        instances = []
        barrier = threading.Barrier(5)

        def _acquire():
            barrier.wait()  # align all threads to fire simultaneously
            instances.append(get_lane_enforcer())

        threads = [threading.Thread(target=_acquire) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads must have received the exact same object.
        assert all(inst is instances[0] for inst in instances)


# ---------------------------------------------------------------------------
# TestResetLaneEnforcer (singleton utility)
# ---------------------------------------------------------------------------

class TestResetLaneEnforcer:
    """Tests for the reset_lane_enforcer utility function."""

    def test_sets_global_to_none(self):
        get_lane_enforcer()  # ensure it exists
        reset_lane_enforcer()
        assert _module._enforcer_instance is None

    def test_idempotent_reset(self):
        reset_lane_enforcer()
        reset_lane_enforcer()  # should not raise
        assert _module._enforcer_instance is None

    def test_get_after_reset_creates_fresh_instance(self):
        old = get_lane_enforcer()
        old.assign_lane("stale-task", TaskType.bug_fix, RiskTier.low)
        reset_lane_enforcer()
        fresh = get_lane_enforcer()
        assert fresh.get_assignment("stale-task") is None


# ---------------------------------------------------------------------------
# TestThreadSafety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """Verify that concurrent access to LaneEnforcer is free of data races."""

    def test_concurrent_assign_lane(self, enforcer: LaneEnforcer):
        """Multiple threads assigning unique task IDs must not lose increments."""
        n = 50
        barrier = threading.Barrier(n)
        errors: list[Exception] = []

        def _work(idx: int):
            try:
                barrier.wait()
                enforcer.assign_lane(
                    f"task-{idx}", TaskType.bug_fix, RiskTier.low
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_work, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # api_simple max_wip=10 → 10 active, 40 queued (n - max_wip)
        active = enforcer._active[WorkCellLane.api_simple]
        queued = enforcer._queued[WorkCellLane.api_simple]
        assert active + queued == n
        assert active <= DEFAULT_LANE_CONFIGS[WorkCellLane.api_simple].max_wip

    def test_concurrent_complete_task(self, enforcer: LaneEnforcer):
        """Completing tasks from many threads should not corrupt counters."""
        n = 20
        for i in range(n):
            enforcer.assign_lane(f"task-{i}", TaskType.docs_update, RiskTier.low)

        barrier = threading.Barrier(n)
        errors: list[Exception] = []

        def _complete(idx: int):
            try:
                barrier.wait()
                enforcer.complete_task(f"task-{idx}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_complete, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # docs max_wip=12 → min(n, max_wip) were active, rest queued
        # After all completions, completed == n
        total = enforcer._completed[WorkCellLane.docs]
        assert total == n


# ---------------------------------------------------------------------------
# TestAssignLaneReentrantLock
# ---------------------------------------------------------------------------

class TestAssignLaneReentrantLock:
    """assign_lane calls check_wip internally — verify RLock enables this."""

    def test_assign_lane_can_call_check_wip_under_same_lock(
        self, enforcer: LaneEnforcer
    ):
        """This would deadlock with a plain Lock but works fine with RLock."""
        # Simply ensure assign_lane completes without deadlock.
        result = enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        assert result is not None

    def test_complete_task_can_call_check_wip_under_same_lock(
        self, enforcer: LaneEnforcer
    ):
        enforcer.assign_lane("t1", TaskType.deployment, RiskTier.low)
        enforcer.assign_lane("t2", TaskType.deployment, RiskTier.low)
        # complete_task calls check_wip internally when promoting queued tasks.
        result = enforcer.complete_task("t1")
        assert result == WorkCellLane.deployment


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Miscellaneous edge cases and invariant checks."""

    def test_empty_task_id_string_is_valid(self, enforcer: LaneEnforcer):
        # Empty string is a valid dict key — the enforcer should handle it.
        lane = enforcer.assign_lane("", TaskType.bug_fix, RiskTier.low)
        assert lane == WorkCellLane.api_simple
        assert enforcer.get_assignment("") == WorkCellLane.api_simple

    def test_very_long_task_id(self, enforcer: LaneEnforcer):
        long_id = "x" * 10_000
        lane = enforcer.assign_lane(long_id, TaskType.docs_update, RiskTier.low)
        assert lane == WorkCellLane.docs

    def test_complete_task_after_reset_returns_none(self, enforcer: LaneEnforcer):
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        assert enforcer.complete_task("t1") is None

    def test_get_lane_stats_after_mixed_operations(self, enforcer: LaneEnforcer):
        # api_simple: 2 active, 0 queued, 1 completed
        for i in range(3):
            enforcer.assign_lane(f"api-{i}", TaskType.bug_fix, RiskTier.low)
        enforcer.complete_task("api-0")

        # deployment: 1 active, 1 queued, 0 completed
        enforcer.assign_lane("dep-0", TaskType.deployment, RiskTier.low)
        enforcer.assign_lane("dep-1", TaskType.deployment, RiskTier.low)

        stats = enforcer.get_lane_stats()
        api_s = stats[WorkCellLane.api_simple.value]
        dep_s = stats[WorkCellLane.deployment.value]

        assert api_s["active"] == 2
        assert api_s["completed"] == 1
        assert api_s["queued"] == 0

        assert dep_s["active"] == 1
        assert dep_s["queued"] == 1
        assert dep_s["completed"] == 0

    def test_assign_all_task_type_risk_tier_combinations(self, enforcer: LaneEnforcer):
        """Every (TaskType, RiskTier) pair should produce a valid WorkCellLane."""
        task_id = 0
        for tt in TaskType:
            for rt in RiskTier:
                lane = enforcer.assign_lane(str(task_id), tt, rt)
                assert isinstance(lane, WorkCellLane)
                task_id += 1
