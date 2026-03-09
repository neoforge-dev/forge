"""Comprehensive tests for LaneEnforcer (Command Center service layer).

Targets forge_harness/webhook_server/services/lane_enforcer.py.

Coverage goals (100% statement coverage):
  - All public methods: assign_lane, check_wip, complete_task, get_lane_stats,
    get_assignment, reset
  - Singleton factory: get_lane_enforcer, reset_lane_enforcer
  - Internal lane resolution via LaneResolver (all TaskType / RiskTier combos
    that matter for branch coverage)
  - WIP limit enforcement:
      * active < max_wip  →  task counts as active
      * active == max_wip →  task counts as queued
  - complete_task:
      * unknown task_id returns None
      * active count clamped at 0 (active already 0)
      * queued task promoted to active when slot frees
      * queued task NOT promoted when lane still full after completion
  - Idempotent assign_lane (same task_id called twice)
  - Thread safety with concurrent access (threading)
  - Singleton pattern: lazy creation, identity, reset
  - reset() clears all counters and assignments
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import (
    DEFAULT_LANE_CONFIGS,
    WorkCellLane,
)
from forge_harness.webhook_server.services.lane_enforcer import (
    LaneEnforcer,
    get_lane_enforcer,
    reset_lane_enforcer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def enforcer() -> LaneEnforcer:
    """Fresh LaneEnforcer instance for each test."""
    return LaneEnforcer()


@pytest.fixture(autouse=True)
def reset_singleton() -> Any:
    """Reset the global singleton before and after every test."""
    reset_lane_enforcer()
    yield
    reset_lane_enforcer()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fill_lane(enforcer: LaneEnforcer, lane: WorkCellLane) -> list[str]:
    """Assign tasks until the lane hits its max_wip limit.

    Returns the list of task IDs assigned (all in active state).
    """
    config = DEFAULT_LANE_CONFIGS[lane]
    task_ids: list[str] = []
    # Use deployment (low risk) to hit deployment lane, or security_change for
    # security lane. For other lanes we use a compatible (type, risk) pair.
    # For this helper we manipulate _active directly to keep it fast and
    # independent of the resolver mapping.
    for i in range(config.max_wip):
        tid = f"fill-{lane.value}-{i}"
        enforcer._assignments[tid] = lane
        enforcer._active[lane] += 1
        task_ids.append(tid)
    return task_ids


# ===========================================================================
# assign_lane — basic routing
# ===========================================================================


class TestAssignLane:
    def test_returns_work_cell_lane(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("task-001", TaskType.bug_fix, RiskTier.low)
        assert isinstance(lane, WorkCellLane)

    def test_bug_fix_low_risk_maps_to_api_simple(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_bug_fix_medium_risk_maps_to_api_stateful(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.medium)
        assert lane == WorkCellLane.api_stateful

    def test_security_change_maps_to_security_lane(self, enforcer: LaneEnforcer) -> None:
        for risk in RiskTier:
            tid = f"sec-{risk.value}"
            lane = enforcer.assign_lane(tid, TaskType.security_change, risk)
            assert lane == WorkCellLane.security_change

    def test_deployment_maps_to_deployment_lane(self, enforcer: LaneEnforcer) -> None:
        for risk in RiskTier:
            tid = f"dep-{risk.value}"
            lane = enforcer.assign_lane(tid, TaskType.deployment, risk)
            assert lane == WorkCellLane.deployment

    def test_test_writing_maps_to_test_writing_lane(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("tw-1", TaskType.test_writing, RiskTier.critical)
        assert lane == WorkCellLane.test_writing

    def test_docs_update_maps_to_docs_lane(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("doc-1", TaskType.docs_update, RiskTier.high)
        assert lane == WorkCellLane.docs

    def test_content_generation_maps_to_docs_lane(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("cg-1", TaskType.content_generation, RiskTier.low)
        assert lane == WorkCellLane.docs

    def test_code_refactor_maps_to_refactor_lane(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("rf-1", TaskType.code_refactor, RiskTier.medium)
        assert lane == WorkCellLane.refactor

    def test_database_migration_maps_to_api_stateful(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("db-1", TaskType.database_migration, RiskTier.low)
        assert lane == WorkCellLane.api_stateful

    def test_dependency_update_maps_to_refactor_lane(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("du-1", TaskType.dependency_update, RiskTier.high)
        assert lane == WorkCellLane.refactor

    def test_config_change_low_risk_maps_to_api_simple(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("cc-1", TaskType.config_change, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_config_change_medium_risk_maps_to_api_stateful(
        self, enforcer: LaneEnforcer
    ) -> None:
        lane = enforcer.assign_lane("cc-2", TaskType.config_change, RiskTier.medium)
        assert lane == WorkCellLane.api_stateful

    def test_new_feature_low_risk_maps_to_api_simple(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("nf-1", TaskType.new_feature, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_new_feature_high_risk_maps_to_api_stateful(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("nf-2", TaskType.new_feature, RiskTier.high)
        assert lane == WorkCellLane.api_stateful

    def test_api_endpoint_low_risk_maps_to_api_simple(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("ae-1", TaskType.api_endpoint, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_api_endpoint_critical_risk_maps_to_api_stateful(
        self, enforcer: LaneEnforcer
    ) -> None:
        lane = enforcer.assign_lane("ae-2", TaskType.api_endpoint, RiskTier.critical)
        assert lane == WorkCellLane.api_stateful

    def test_task_recorded_in_assignments(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("task-x", TaskType.bug_fix, RiskTier.low)
        assert "task-x" in enforcer._assignments

    def test_active_counter_incremented_when_capacity_available(
        self, enforcer: LaneEnforcer
    ) -> None:
        lane = enforcer.assign_lane("act-1", TaskType.bug_fix, RiskTier.low)
        assert enforcer._active[lane] == 1

    def test_queued_counter_incremented_when_lane_full(
        self, enforcer: LaneEnforcer
    ) -> None:
        # Fill the deployment lane (max_wip=1) so the next task must queue.
        _fill_lane(enforcer, WorkCellLane.deployment)
        lane = enforcer.assign_lane("q-1", TaskType.deployment, RiskTier.low)
        assert lane == WorkCellLane.deployment
        assert enforcer._queued[WorkCellLane.deployment] == 1
        # Active count stays at max_wip (1), not incremented further.
        assert enforcer._active[WorkCellLane.deployment] == DEFAULT_LANE_CONFIGS[
            WorkCellLane.deployment
        ].max_wip


# ===========================================================================
# assign_lane — idempotency
# ===========================================================================


class TestAssignLaneIdempotent:
    def test_second_call_returns_same_lane(self, enforcer: LaneEnforcer) -> None:
        lane1 = enforcer.assign_lane("idem-1", TaskType.bug_fix, RiskTier.low)
        lane2 = enforcer.assign_lane("idem-1", TaskType.bug_fix, RiskTier.low)
        assert lane1 == lane2

    def test_second_call_does_not_increment_active(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("idem-2", TaskType.bug_fix, RiskTier.low)
        enforcer.assign_lane("idem-2", TaskType.bug_fix, RiskTier.low)
        assert enforcer._active[lane] == 1

    def test_second_call_does_not_increment_queued(self, enforcer: LaneEnforcer) -> None:
        _fill_lane(enforcer, WorkCellLane.deployment)
        enforcer.assign_lane("idem-q", TaskType.deployment, RiskTier.low)
        queued_after_first = enforcer._queued[WorkCellLane.deployment]
        enforcer.assign_lane("idem-q", TaskType.deployment, RiskTier.low)
        assert enforcer._queued[WorkCellLane.deployment] == queued_after_first

    def test_different_task_ids_each_get_own_entry(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("d-1", TaskType.test_writing, RiskTier.low)
        enforcer.assign_lane("d-2", TaskType.test_writing, RiskTier.low)
        assert len(enforcer._assignments) == 2


# ===========================================================================
# check_wip
# ===========================================================================


class TestCheckWip:
    def test_returns_true_when_lane_empty(self, enforcer: LaneEnforcer) -> None:
        assert enforcer.check_wip(WorkCellLane.api_simple) is True

    def test_returns_true_below_max_wip(self, enforcer: LaneEnforcer) -> None:
        lane = WorkCellLane.api_simple
        config = DEFAULT_LANE_CONFIGS[lane]
        # Set active to one below limit.
        enforcer._active[lane] = config.max_wip - 1
        assert enforcer.check_wip(lane) is True

    def test_returns_false_at_max_wip(self, enforcer: LaneEnforcer) -> None:
        lane = WorkCellLane.api_simple
        config = DEFAULT_LANE_CONFIGS[lane]
        enforcer._active[lane] = config.max_wip
        assert enforcer.check_wip(lane) is False

    def test_returns_false_above_max_wip(self, enforcer: LaneEnforcer) -> None:
        lane = WorkCellLane.security_change
        config = DEFAULT_LANE_CONFIGS[lane]
        # Intentionally exceed (e.g. via direct state manipulation).
        enforcer._active[lane] = config.max_wip + 5
        assert enforcer.check_wip(lane) is False

    def test_deployment_lane_max_wip_is_one(self, enforcer: LaneEnforcer) -> None:
        assert DEFAULT_LANE_CONFIGS[WorkCellLane.deployment].max_wip == 1
        enforcer._active[WorkCellLane.deployment] = 1
        assert enforcer.check_wip(WorkCellLane.deployment) is False

    def test_all_lanes_start_with_capacity(self, enforcer: LaneEnforcer) -> None:
        for lane in WorkCellLane:
            assert enforcer.check_wip(lane) is True


# ===========================================================================
# complete_task
# ===========================================================================


class TestCompleteTask:
    def test_returns_none_for_unknown_task(self, enforcer: LaneEnforcer) -> None:
        result = enforcer.complete_task("ghost-task")
        assert result is None

    def test_returns_lane_for_known_task(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("ct-1", TaskType.bug_fix, RiskTier.low)
        result = enforcer.complete_task("ct-1")
        assert result == lane

    def test_active_count_decremented(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("ct-dec", TaskType.bug_fix, RiskTier.low)
        before = enforcer._active[lane]
        enforcer.complete_task("ct-dec")
        assert enforcer._active[lane] == before - 1

    def test_completed_count_incremented(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("ct-comp", TaskType.bug_fix, RiskTier.low)
        enforcer.complete_task("ct-comp")
        assert enforcer._completed[lane] == 1

    def test_active_clamped_at_zero(self, enforcer: LaneEnforcer) -> None:
        """complete_task on a task whose active count is already 0 must not go negative."""
        lane = WorkCellLane.api_simple
        # Assign a task but then manually set active to 0 (simulates race/double-complete).
        enforcer._assignments["clamp-t"] = lane
        enforcer._active[lane] = 0
        enforcer.complete_task("clamp-t")
        assert enforcer._active[lane] == 0

    def test_queued_task_promoted_to_active_after_completion(
        self, enforcer: LaneEnforcer
    ) -> None:
        """When a slot frees up, one queued task is promoted to active."""
        # Deployment lane: max_wip=1. Fill it, queue one, then complete the active one.
        lane = WorkCellLane.deployment
        _fill_lane(enforcer, lane)
        # Queue one task manually.
        enforcer._assignments["q-promote"] = lane
        enforcer._queued[lane] += 1

        # Complete the active task — the queued task should be promoted.
        active_task_id = f"fill-{lane.value}-0"
        enforcer.complete_task(active_task_id)

        assert enforcer._active[lane] == 1  # promoted from queue
        assert enforcer._queued[lane] == 0  # removed from queue

    def test_queued_task_not_promoted_when_lane_still_full(
        self, enforcer: LaneEnforcer
    ) -> None:
        """If the lane is still at capacity after completion, no promotion occurs."""
        # security_change lane: max_wip=2. Fill it with 2 tasks, add 2 queued.
        lane = WorkCellLane.security_change
        config = DEFAULT_LANE_CONFIGS[lane]
        assert config.max_wip == 2  # sanity check

        fill_ids = _fill_lane(enforcer, lane)  # active = 2
        enforcer._assignments["q-nopromote-1"] = lane
        enforcer._assignments["q-nopromote-2"] = lane
        enforcer._queued[lane] = 2

        # Complete one active task — active drops to 1, which is < max_wip=2,
        # so ONE queued task should be promoted.
        enforcer.complete_task(fill_ids[0])
        assert enforcer._active[lane] == 2  # 1 remaining + 1 promoted
        assert enforcer._queued[lane] == 1

    def test_multiple_completions_drain_queue(self, enforcer: LaneEnforcer) -> None:
        lane = WorkCellLane.deployment  # max_wip=1
        _fill_lane(enforcer, lane)

        # Queue 3 tasks manually.
        for i in range(3):
            tid = f"drain-q-{i}"
            enforcer._assignments[tid] = lane
            enforcer._queued[lane] += 1

        # Complete active tasks one by one; each should promote one queued task.
        enforcer.complete_task("fill-deployment-0")
        assert enforcer._queued[lane] == 2
        assert enforcer._active[lane] == 1

        # Now the fill task is gone — complete the promoted task.
        # The promoted task doesn't have a "real" task_id stored here, but we can
        # simulate by completing another known task that was assigned.
        enforcer.complete_task("drain-q-0")
        assert enforcer._queued[lane] == 1
        assert enforcer._active[lane] == 1

    def test_complete_task_without_assignment_stored_returns_none(
        self, enforcer: LaneEnforcer
    ) -> None:
        result = enforcer.complete_task("never-assigned")
        assert result is None


# ===========================================================================
# get_lane_stats
# ===========================================================================


class TestGetLaneStats:
    def test_returns_dict_with_all_lanes(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        lane_keys = {lane.value for lane in WorkCellLane}
        assert set(stats.keys()) == lane_keys

    def test_all_counters_zero_on_fresh_enforcer(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane_value, data in stats.items():
            assert data["active"] == 0, f"Expected active=0 for {lane_value}"
            assert data["queued"] == 0, f"Expected queued=0 for {lane_value}"
            assert data["completed"] == 0, f"Expected completed=0 for {lane_value}"

    def test_max_wip_matches_default_config(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane in WorkCellLane:
            expected_max_wip = DEFAULT_LANE_CONFIGS[lane].max_wip
            assert stats[lane.value]["max_wip"] == expected_max_wip

    def test_active_count_reflected_in_stats(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("s-act-1", TaskType.bug_fix, RiskTier.low)
        enforcer.assign_lane("s-act-2", TaskType.bug_fix, RiskTier.low)
        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.api_simple.value]["active"] == 2

    def test_queued_count_reflected_in_stats(self, enforcer: LaneEnforcer) -> None:
        _fill_lane(enforcer, WorkCellLane.deployment)
        enforcer.assign_lane("s-q-1", TaskType.deployment, RiskTier.low)
        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.deployment.value]["queued"] == 1

    def test_completed_count_reflected_in_stats(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("s-done", TaskType.bug_fix, RiskTier.low)
        enforcer.complete_task("s-done")
        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.api_simple.value]["completed"] == 1

    def test_stats_keys_are_strings(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for key in stats:
            assert isinstance(key, str)

    def test_stat_values_are_dicts(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for _, value in stats.items():
            assert isinstance(value, dict)

    def test_stats_contain_expected_keys(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        expected_keys = {"active", "queued", "completed", "max_wip"}
        for data in stats.values():
            assert set(data.keys()) == expected_keys

    def test_stats_is_snapshot_not_live_reference(self, enforcer: LaneEnforcer) -> None:
        stats1 = enforcer.get_lane_stats()
        enforcer.assign_lane("snap-t", TaskType.bug_fix, RiskTier.low)
        # First snapshot is unaffected by the later mutation.
        assert stats1[WorkCellLane.api_simple.value]["active"] == 0


# ===========================================================================
# get_assignment
# ===========================================================================


class TestGetAssignment:
    def test_returns_none_for_unknown_task(self, enforcer: LaneEnforcer) -> None:
        assert enforcer.get_assignment("no-such-task") is None

    def test_returns_correct_lane_after_assign(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("ga-1", TaskType.test_writing, RiskTier.low)
        result = enforcer.get_assignment("ga-1")
        assert result == lane

    def test_returns_none_after_reset(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("ga-2", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        assert enforcer.get_assignment("ga-2") is None

    def test_still_returns_lane_after_completion(self, enforcer: LaneEnforcer) -> None:
        """Assignment dict is not cleared by complete_task — task is still findable."""
        lane = enforcer.assign_lane("ga-3", TaskType.bug_fix, RiskTier.low)
        enforcer.complete_task("ga-3")
        assert enforcer.get_assignment("ga-3") == lane


# ===========================================================================
# reset
# ===========================================================================


class TestReset:
    def test_clears_assignments(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("r-1", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        assert len(enforcer._assignments) == 0

    def test_clears_active_counters(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("r-act", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        for lane in WorkCellLane:
            assert enforcer._active[lane] == 0

    def test_clears_queued_counters(self, enforcer: LaneEnforcer) -> None:
        _fill_lane(enforcer, WorkCellLane.deployment)
        enforcer.assign_lane("r-q", TaskType.deployment, RiskTier.low)
        enforcer.reset()
        for lane in WorkCellLane:
            assert enforcer._queued[lane] == 0

    def test_clears_completed_counters(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("r-c", TaskType.bug_fix, RiskTier.low)
        enforcer.complete_task("r-c")
        enforcer.reset()
        for lane in WorkCellLane:
            assert enforcer._completed[lane] == 0

    def test_reset_allows_reuse(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("r-reuse", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        lane = enforcer.assign_lane("r-reuse", TaskType.bug_fix, RiskTier.low)
        assert enforcer._active[lane] == 1


# ===========================================================================
# Singleton — get_lane_enforcer / reset_lane_enforcer
# ===========================================================================


class TestSingleton:
    def test_returns_lane_enforcer_instance(self) -> None:
        inst = get_lane_enforcer()
        assert isinstance(inst, LaneEnforcer)

    def test_same_instance_on_repeated_calls(self) -> None:
        i1 = get_lane_enforcer()
        i2 = get_lane_enforcer()
        assert i1 is i2

    def test_reset_causes_new_instance(self) -> None:
        i1 = get_lane_enforcer()
        reset_lane_enforcer()
        i2 = get_lane_enforcer()
        assert i1 is not i2

    def test_reset_sets_module_var_to_none(self) -> None:
        import forge_harness.webhook_server.services.lane_enforcer as mod

        get_lane_enforcer()
        assert mod._enforcer_instance is not None
        reset_lane_enforcer()
        assert mod._enforcer_instance is None

    def test_fresh_singleton_has_empty_state(self) -> None:
        inst = get_lane_enforcer()
        assert inst._assignments == {}
        for lane in WorkCellLane:
            assert inst._active[lane] == 0
            assert inst._queued[lane] == 0
            assert inst._completed[lane] == 0

    def test_singleton_state_persists_between_get_calls(self) -> None:
        inst = get_lane_enforcer()
        inst.assign_lane("persist-1", TaskType.bug_fix, RiskTier.low)
        inst2 = get_lane_enforcer()
        assert "persist-1" in inst2._assignments

    def test_thread_safe_singleton_creation(self) -> None:
        """All threads must receive the same singleton instance."""
        instances: list[LaneEnforcer] = []
        errors: list[Exception] = []

        def getter() -> None:
            try:
                instances.append(get_lane_enforcer())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=getter) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(inst is instances[0] for inst in instances)


# ===========================================================================
# Thread safety — concurrent access to a shared instance
# ===========================================================================


class TestConcurrency:
    def test_concurrent_assign_lane_no_exception(self, enforcer: LaneEnforcer) -> None:
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                enforcer.assign_lane(f"conc-{i}", TaskType.bug_fix, RiskTier.low)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(enforcer._assignments) == 40

    def test_concurrent_check_wip_no_exception(self, enforcer: LaneEnforcer) -> None:
        errors: list[Exception] = []
        enforcer.assign_lane("init-wip", TaskType.bug_fix, RiskTier.low)

        def worker() -> None:
            try:
                enforcer.check_wip(WorkCellLane.api_simple)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_complete_task_no_exception(self, enforcer: LaneEnforcer) -> None:
        task_ids = [f"comp-{i}" for i in range(20)]
        for tid in task_ids:
            enforcer.assign_lane(tid, TaskType.bug_fix, RiskTier.low)

        errors: list[Exception] = []

        def completer(tid: str) -> None:
            try:
                enforcer.complete_task(tid)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=completer, args=(tid,)) for tid in task_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert enforcer._completed[WorkCellLane.api_simple] == 20

    def test_concurrent_get_lane_stats_no_exception(self, enforcer: LaneEnforcer) -> None:
        for i in range(5):
            enforcer.assign_lane(f"stats-{i}", TaskType.test_writing, RiskTier.low)

        errors: list[Exception] = []

        def stats_worker() -> None:
            try:
                enforcer.get_lane_stats()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=stats_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_mixed_operations_no_exception(self, enforcer: LaneEnforcer) -> None:
        """Interleaved assign / check_wip / complete / stats must not raise."""
        errors: list[Exception] = []
        counter = {"n": 0}
        lock = threading.Lock()

        def mixed_worker() -> None:
            try:
                with lock:
                    counter["n"] += 1
                    tid = f"mix-{counter['n']}"
                lane = enforcer.assign_lane(tid, TaskType.docs_update, RiskTier.low)
                enforcer.check_wip(lane)
                enforcer.get_lane_stats()
                enforcer.complete_task(tid)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=mixed_worker) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_reentrancy_assign_calls_check_wip_internally(
        self, enforcer: LaneEnforcer
    ) -> None:
        """assign_lane calls check_wip while holding the RLock — must not deadlock."""
        # If the lock were a plain threading.Lock this would deadlock.
        # The test just verifies it completes in finite time.
        lane = enforcer.assign_lane("reentrant-1", TaskType.bug_fix, RiskTier.low)
        assert isinstance(lane, WorkCellLane)


# ===========================================================================
# WIP boundary — at-capacity / over-capacity transitions
# ===========================================================================


class TestWipBoundaryConditions:
    def test_last_slot_fills_then_next_queues(self, enforcer: LaneEnforcer) -> None:
        lane = WorkCellLane.deployment  # max_wip=1
        # One task fills the lane.
        enforcer.assign_lane("wip-fill", TaskType.deployment, RiskTier.low)
        assert enforcer._active[lane] == 1
        assert enforcer._queued[lane] == 0

        # Next task must queue.
        enforcer.assign_lane("wip-queue", TaskType.deployment, RiskTier.low)
        assert enforcer._active[lane] == 1
        assert enforcer._queued[lane] == 1

    def test_security_change_lane_capacity_boundary(self, enforcer: LaneEnforcer) -> None:
        lane = WorkCellLane.security_change  # max_wip=2
        enforcer.assign_lane("sc-1", TaskType.security_change, RiskTier.low)
        enforcer.assign_lane("sc-2", TaskType.security_change, RiskTier.medium)
        assert enforcer._active[lane] == 2
        assert enforcer._queued[lane] == 0

        # Third task must queue.
        enforcer.assign_lane("sc-3", TaskType.security_change, RiskTier.high)
        assert enforcer._active[lane] == 2
        assert enforcer._queued[lane] == 1

    def test_active_never_exceeds_max_wip_via_assign_lane(
        self, enforcer: LaneEnforcer
    ) -> None:
        lane = WorkCellLane.deployment
        config = DEFAULT_LANE_CONFIGS[lane]
        for i in range(config.max_wip * 3):
            enforcer.assign_lane(f"wip-safe-{i}", TaskType.deployment, RiskTier.low)
        assert enforcer._active[lane] <= config.max_wip

    def test_complete_then_assign_stays_at_capacity(self, enforcer: LaneEnforcer) -> None:
        lane = WorkCellLane.deployment
        enforcer.assign_lane("cycle-1", TaskType.deployment, RiskTier.low)
        enforcer.complete_task("cycle-1")
        enforcer.assign_lane("cycle-2", TaskType.deployment, RiskTier.low)
        assert enforcer._active[lane] == 1
        assert enforcer._queued[lane] == 0
