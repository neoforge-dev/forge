"""Comprehensive tests for LaneEnforcer service — DF-2003
=========================================================

Tests focus exclusively on the lane_enforcer.py service module:
  - LaneEnforcer class (all public methods)
  - Lane assignment and enforcement logic
  - WIP limit enforcement (active vs queued bucketing)
  - Task completion and queued-task promotion
  - Idempotent assign_lane behaviour
  - LaneEnforcer.reset() clears all state
  - get_lane_stats() accuracy
  - get_assignment() lookup
  - Singleton: get_lane_enforcer / reset_lane_enforcer
  - Thread-safety under concurrent load
  - Edge cases: unknown tasks, empty inputs, counter clamping at zero

Coverage target: 90%+ of lane_enforcer.py (60+ tests).

Layout
------
  - Fixtures
  - TestLaneEnforcerInit
  - TestAssignLane_HappyPath
  - TestAssignLane_Idempotency
  - TestAssignLane_WIPBucketing
  - TestCheckWip
  - TestCompleteTask_HappyPath
  - TestCompleteTask_QueuePromotion
  - TestCompleteTask_EdgeCases
  - TestGetLaneStats
  - TestGetAssignment
  - TestReset
  - TestLaneEnforcerAllLanes (parametrised matrix sweep)
  - TestSingletonGetLaneEnforcer
  - TestSingletonResetLaneEnforcer
  - TestConcurrentAccess
  - TestLoggingInteractions (verify no exceptions with real logger)
  - TestStatsCounting (multi-task lifecycle scenarios)
  - TestWipClamping (active never goes below zero)
"""

from __future__ import annotations

import threading
from collections import defaultdict
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import (
    DEFAULT_LANE_CONFIGS,
    WorkCellConfig,
    WorkCellLane,
    get_lane_config,
)
from forge_harness.webhook_server.services.lane_enforcer import (
    LaneEnforcer,
    get_lane_enforcer,
    reset_lane_enforcer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_TASK_TYPES = list(TaskType)
_ALL_RISK_TIERS = list(RiskTier)


def _make_task_id(prefix: str, n: int) -> str:
    return f"{prefix}-{n}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Destroy the global singleton before *and* after every test."""
    reset_lane_enforcer()
    yield
    reset_lane_enforcer()


@pytest.fixture()
def enforcer() -> LaneEnforcer:
    """Return a fresh, isolated LaneEnforcer instance (not the singleton)."""
    return LaneEnforcer()


# ---------------------------------------------------------------------------
# TestLaneEnforcerInit
# ---------------------------------------------------------------------------


class TestLaneEnforcerInit:
    """LaneEnforcer has the expected initial state after construction."""

    def test_initial_assignments_are_empty(self, enforcer: LaneEnforcer) -> None:
        assert enforcer.get_assignment("any-task") is None

    def test_initial_stats_all_zero_active(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            assert lane_stats["active"] == 0

    def test_initial_stats_all_zero_queued(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            assert lane_stats["queued"] == 0

    def test_initial_stats_all_zero_completed(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            assert lane_stats["completed"] == 0

    def test_check_wip_all_lanes_available_on_fresh_instance(
        self, enforcer: LaneEnforcer
    ) -> None:
        for lane in WorkCellLane:
            assert enforcer.check_wip(lane) is True

    def test_two_independent_instances_do_not_share_state(self) -> None:
        e1 = LaneEnforcer()
        e2 = LaneEnforcer()
        e1.assign_lane("task-e1", TaskType.docs_update, RiskTier.low)
        # e2 must not see e1's assignment
        assert e2.get_assignment("task-e1") is None

    def test_enforcer_has_resolver_attribute(self, enforcer: LaneEnforcer) -> None:
        assert hasattr(enforcer, "_resolver")

    def test_enforcer_has_lock_attribute(self, enforcer: LaneEnforcer) -> None:
        assert hasattr(enforcer, "_lock")


# ---------------------------------------------------------------------------
# TestAssignLane_HappyPath
# ---------------------------------------------------------------------------


class TestAssignLaneHappyPath:
    """assign_lane returns the correct lane and records the assignment."""

    def test_returns_work_cell_lane_instance(self, enforcer: LaneEnforcer) -> None:
        result = enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        assert isinstance(result, WorkCellLane)

    def test_bug_fix_low_maps_to_api_simple(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_bug_fix_medium_maps_to_api_stateful(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.medium)
        assert lane == WorkCellLane.api_stateful

    def test_security_change_always_maps_to_security_lane(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i, risk in enumerate(RiskTier):
            lane = enforcer.assign_lane(f"sec-{i}", TaskType.security_change, risk)
            assert lane == WorkCellLane.security_change

    def test_deployment_always_maps_to_deployment_lane(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i, risk in enumerate(RiskTier):
            lane = enforcer.assign_lane(f"dep-{i}", TaskType.deployment, risk)
            assert lane == WorkCellLane.deployment

    def test_test_writing_always_maps_to_test_writing_lane(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i, risk in enumerate(RiskTier):
            lane = enforcer.assign_lane(f"tw-{i}", TaskType.test_writing, risk)
            assert lane == WorkCellLane.test_writing

    def test_docs_update_always_maps_to_docs_lane(self, enforcer: LaneEnforcer) -> None:
        for i, risk in enumerate(RiskTier):
            lane = enforcer.assign_lane(f"doc-{i}", TaskType.docs_update, risk)
            assert lane == WorkCellLane.docs

    def test_content_generation_always_maps_to_docs_lane(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i, risk in enumerate(RiskTier):
            lane = enforcer.assign_lane(f"cg-{i}", TaskType.content_generation, risk)
            assert lane == WorkCellLane.docs

    def test_code_refactor_always_maps_to_refactor_lane(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i, risk in enumerate(RiskTier):
            lane = enforcer.assign_lane(f"rf-{i}", TaskType.code_refactor, risk)
            assert lane == WorkCellLane.refactor

    def test_dependency_update_always_maps_to_refactor_lane(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i, risk in enumerate(RiskTier):
            lane = enforcer.assign_lane(f"du-{i}", TaskType.dependency_update, risk)
            assert lane == WorkCellLane.refactor

    def test_api_endpoint_low_maps_to_api_simple(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("t1", TaskType.api_endpoint, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_api_endpoint_medium_maps_to_api_stateful(
        self, enforcer: LaneEnforcer
    ) -> None:
        lane = enforcer.assign_lane("t1", TaskType.api_endpoint, RiskTier.medium)
        assert lane == WorkCellLane.api_stateful

    def test_new_feature_low_maps_to_api_simple(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("t1", TaskType.new_feature, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_database_migration_maps_to_api_stateful(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i, risk in enumerate(RiskTier):
            lane = enforcer.assign_lane(f"db-{i}", TaskType.database_migration, risk)
            assert lane == WorkCellLane.api_stateful

    def test_config_change_low_maps_to_api_simple(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("t1", TaskType.config_change, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_assign_lane_stores_the_assignment(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("store-me", TaskType.test_writing, RiskTier.low)
        assert enforcer.get_assignment("store-me") == lane

    def test_multiple_distinct_tasks_all_stored(self, enforcer: LaneEnforcer) -> None:
        task_ids = [f"multi-{i}" for i in range(10)]
        for tid in task_ids:
            enforcer.assign_lane(tid, TaskType.test_writing, RiskTier.low)
        for tid in task_ids:
            assert enforcer.get_assignment(tid) is not None


# ---------------------------------------------------------------------------
# TestAssignLane_Idempotency
# ---------------------------------------------------------------------------


class TestAssignLaneIdempotency:
    """Calling assign_lane with the same task_id more than once is safe."""

    def test_same_lane_returned_on_second_call(self, enforcer: LaneEnforcer) -> None:
        lane1 = enforcer.assign_lane("idem", TaskType.docs_update, RiskTier.low)
        lane2 = enforcer.assign_lane("idem", TaskType.docs_update, RiskTier.low)
        assert lane1 == lane2

    def test_repeated_call_does_not_increment_active(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.assign_lane("idem2", TaskType.docs_update, RiskTier.low)
        enforcer.assign_lane("idem2", TaskType.docs_update, RiskTier.low)
        enforcer.assign_lane("idem2", TaskType.docs_update, RiskTier.low)
        stats = enforcer.get_lane_stats()[WorkCellLane.docs.value]
        assert stats["active"] + stats["queued"] == 1

    def test_repeated_call_does_not_increment_queued(
        self, enforcer: LaneEnforcer
    ) -> None:
        """Even when lane is full, re-assigning the same task must not queue it again."""
        # Fill deployment lane (max_wip=1)
        enforcer.assign_lane("first-deploy", TaskType.deployment, RiskTier.low)
        # Assign a second task (goes to queue)
        enforcer.assign_lane("second-deploy", TaskType.deployment, RiskTier.low)
        # Re-assign the queued task — queue count must NOT grow
        enforcer.assign_lane("second-deploy", TaskType.deployment, RiskTier.low)
        stats = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert stats["queued"] == 1

    def test_different_task_types_do_not_affect_idempotency(
        self, enforcer: LaneEnforcer
    ) -> None:
        """The stored lane type from the first call must be returned even if
        a subsequent call passes different (task_type, risk_tier) arguments.
        The first assignment wins."""
        lane1 = enforcer.assign_lane("mixed", TaskType.docs_update, RiskTier.low)
        # Second call with totally different type — must return first assignment
        lane2 = enforcer.assign_lane("mixed", TaskType.security_change, RiskTier.high)
        assert lane1 == lane2


# ---------------------------------------------------------------------------
# TestAssignLane_WIPBucketing
# ---------------------------------------------------------------------------


class TestAssignLaneWipBucketing:
    """assign_lane correctly routes tasks to active vs queued based on WIP."""

    def test_first_task_goes_to_active(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.deployment, RiskTier.low)
        stats = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert stats["active"] == 1
        assert stats["queued"] == 0

    def test_task_beyond_max_wip_goes_to_queued(self, enforcer: LaneEnforcer) -> None:
        # deployment max_wip == 1
        enforcer.assign_lane("d1", TaskType.deployment, RiskTier.low)
        enforcer.assign_lane("d2", TaskType.deployment, RiskTier.low)
        stats = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert stats["active"] == 1
        assert stats["queued"] == 1

    def test_multiple_tasks_beyond_max_wip_all_queued(
        self, enforcer: LaneEnforcer
    ) -> None:
        # security_change max_wip == 2
        for i in range(5):
            enforcer.assign_lane(f"sec-{i}", TaskType.security_change, RiskTier.low)
        stats = enforcer.get_lane_stats()[WorkCellLane.security_change.value]
        assert stats["active"] == 2
        assert stats["queued"] == 3

    def test_active_tasks_count_equals_max_wip_when_saturated(
        self, enforcer: LaneEnforcer
    ) -> None:
        max_wip = get_lane_config(WorkCellLane.research).max_wip  # == 4
        for i in range(max_wip + 2):
            enforcer.assign_lane(
                f"res-{i}", TaskType.new_feature, RiskTier.high
            )
        # new_feature high -> api_stateful (max_wip=5), not research. Test research via docs.
        # Use a lane we can fill predictably: deployment (max_wip=1)
        enforcer2 = LaneEnforcer()
        for i in range(3):
            enforcer2.assign_lane(f"dep-{i}", TaskType.deployment, RiskTier.low)
        stats = enforcer2.get_lane_stats()[WorkCellLane.deployment.value]
        assert stats["active"] == 1
        assert stats["queued"] == 2

    def test_tasks_in_different_lanes_do_not_affect_each_other(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.assign_lane("docs1", TaskType.docs_update, RiskTier.low)
        enforcer.assign_lane("sec1", TaskType.security_change, RiskTier.low)
        enforcer.assign_lane("test1", TaskType.test_writing, RiskTier.low)

        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.docs.value]["active"] == 1
        assert stats[WorkCellLane.security_change.value]["active"] == 1
        assert stats[WorkCellLane.test_writing.value]["active"] == 1


# ---------------------------------------------------------------------------
# TestCheckWip
# ---------------------------------------------------------------------------


class TestCheckWip:
    """check_wip returns True iff active < max_wip for the lane."""

    def test_empty_lane_always_has_capacity(self, enforcer: LaneEnforcer) -> None:
        for lane in WorkCellLane:
            assert enforcer.check_wip(lane) is True

    def test_lane_at_max_wip_has_no_capacity(self, enforcer: LaneEnforcer) -> None:
        # deployment max_wip=1: fill it with one task
        enforcer.assign_lane("d1", TaskType.deployment, RiskTier.low)
        assert enforcer.check_wip(WorkCellLane.deployment) is False

    def test_lane_one_below_max_wip_has_capacity(self, enforcer: LaneEnforcer) -> None:
        # security_change max_wip=2: add one task
        enforcer.assign_lane("s1", TaskType.security_change, RiskTier.low)
        assert enforcer.check_wip(WorkCellLane.security_change) is True

    def test_lane_exactly_at_max_wip_reports_false(self, enforcer: LaneEnforcer) -> None:
        # security_change max_wip=2
        enforcer.assign_lane("s1", TaskType.security_change, RiskTier.low)
        enforcer.assign_lane("s2", TaskType.security_change, RiskTier.medium)
        assert enforcer.check_wip(WorkCellLane.security_change) is False

    def test_check_wip_after_complete_restores_capacity(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.assign_lane("d1", TaskType.deployment, RiskTier.low)
        assert enforcer.check_wip(WorkCellLane.deployment) is False
        enforcer.complete_task("d1")
        assert enforcer.check_wip(WorkCellLane.deployment) is True

    def test_check_wip_is_reentrant_safe(self, enforcer: LaneEnforcer) -> None:
        """check_wip is called inside assign_lane; the RLock must allow this."""
        # assign_lane calls check_wip internally — would deadlock with a plain Lock
        lane = enforcer.assign_lane("reent", TaskType.docs_update, RiskTier.low)
        # No deadlock means re-entrancy is working
        assert lane == WorkCellLane.docs


# ---------------------------------------------------------------------------
# TestCompleteTask_HappyPath
# ---------------------------------------------------------------------------


class TestCompleteTaskHappyPath:
    """complete_task moves active tasks to completed and frees WIP."""

    def test_returns_the_assigned_lane(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.test_writing, RiskTier.low)
        result = enforcer.complete_task("t1")
        assert result == WorkCellLane.test_writing

    def test_active_count_decrements_on_complete(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.low)
        enforcer.assign_lane("t2", TaskType.docs_update, RiskTier.low)
        enforcer.complete_task("t1")
        stats = enforcer.get_lane_stats()[WorkCellLane.docs.value]
        assert stats["active"] == 1

    def test_completed_count_increments_on_complete(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.test_writing, RiskTier.low)
        enforcer.complete_task("t1")
        stats = enforcer.get_lane_stats()[WorkCellLane.test_writing.value]
        assert stats["completed"] == 1

    def test_multiple_completions_accumulate_completed_count(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i in range(5):
            enforcer.assign_lane(f"t{i}", TaskType.test_writing, RiskTier.low)
        for i in range(5):
            enforcer.complete_task(f"t{i}")
        stats = enforcer.get_lane_stats()[WorkCellLane.test_writing.value]
        assert stats["completed"] == 5
        assert stats["active"] == 0

    def test_active_never_goes_negative_after_complete(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.low)
        enforcer.complete_task("t1")
        # Force _active to 0 and call complete again (via manual manipulation).
        # Instead, just complete a task whose active is 0 by resetting.
        enforcer.reset()
        # Manually set completed entry without active — use a distinct instance.
        e2 = LaneEnforcer()
        e2._assignments["ghost"] = WorkCellLane.docs
        # _active["docs"] is 0 (defaultdict). complete_task should not go negative.
        e2.complete_task("ghost")
        assert e2._active[WorkCellLane.docs] >= 0


# ---------------------------------------------------------------------------
# TestCompleteTask_QueuePromotion
# ---------------------------------------------------------------------------


class TestCompleteTaskQueuePromotion:
    """When a task completes and there are queued tasks, one is promoted."""

    def test_queued_task_is_promoted_when_slot_freed(
        self, enforcer: LaneEnforcer
    ) -> None:
        # Fill deployment (max_wip=1) and queue a second
        enforcer.assign_lane("d1", TaskType.deployment, RiskTier.low)
        enforcer.assign_lane("d2", TaskType.deployment, RiskTier.low)

        before = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert before["active"] == 1
        assert before["queued"] == 1

        enforcer.complete_task("d1")
        after = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert after["active"] == 1
        assert after["queued"] == 0
        assert after["completed"] == 1

    def test_active_stays_at_max_wip_after_promotion(
        self, enforcer: LaneEnforcer
    ) -> None:
        # security_change max_wip=2
        enforcer.assign_lane("s1", TaskType.security_change, RiskTier.low)
        enforcer.assign_lane("s2", TaskType.security_change, RiskTier.medium)
        enforcer.assign_lane("s3", TaskType.security_change, RiskTier.low)  # queued

        enforcer.complete_task("s1")
        stats = enforcer.get_lane_stats()[WorkCellLane.security_change.value]
        assert stats["active"] == 2  # s2 + promoted s3
        assert stats["queued"] == 0
        assert stats["completed"] == 1

    def test_multiple_queued_tasks_promoted_one_at_a_time(
        self, enforcer: LaneEnforcer
    ) -> None:
        # deployment max_wip=1; queue 2 extra tasks
        for i in range(3):
            enforcer.assign_lane(f"d{i}", TaskType.deployment, RiskTier.low)

        stats = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert stats["active"] == 1
        assert stats["queued"] == 2

        enforcer.complete_task("d0")
        stats = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert stats["active"] == 1
        assert stats["queued"] == 1  # one still waiting
        assert stats["completed"] == 1

    def test_no_promotion_when_queue_is_empty(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.low)
        enforcer.complete_task("t1")
        stats = enforcer.get_lane_stats()[WorkCellLane.docs.value]
        assert stats["queued"] == 0

    def test_no_promotion_from_other_lanes_queue(self, enforcer: LaneEnforcer) -> None:
        """Completing a docs task must not promote queued deployment tasks."""
        enforcer.assign_lane("d1", TaskType.deployment, RiskTier.low)
        enforcer.assign_lane("d2", TaskType.deployment, RiskTier.low)  # queued
        enforcer.assign_lane("docs1", TaskType.docs_update, RiskTier.low)
        enforcer.complete_task("docs1")

        # deployment queue should still have 1 queued
        stats = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert stats["queued"] == 1


# ---------------------------------------------------------------------------
# TestCompleteTask_EdgeCases
# ---------------------------------------------------------------------------


class TestCompleteTaskEdgeCases:
    """complete_task handles unknown task IDs gracefully."""

    def test_unknown_task_id_returns_none(self, enforcer: LaneEnforcer) -> None:
        assert enforcer.complete_task("never-assigned") is None

    def test_unknown_task_does_not_alter_any_counters(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.low)
        before_stats = enforcer.get_lane_stats()

        enforcer.complete_task("ghost-task")

        after_stats = enforcer.get_lane_stats()
        assert before_stats == after_stats

    def test_completing_same_task_twice_is_safe(self, enforcer: LaneEnforcer) -> None:
        """Second complete call returns None without crashing or corrupting state."""
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.low)
        result1 = enforcer.complete_task("t1")
        result2 = enforcer.complete_task("t1")
        # First call returns the lane; second call returns None (task still in
        # assignments but active is already 0 — it will not go negative).
        assert result1 == WorkCellLane.docs
        # Stats must be sane (not negative)
        stats = enforcer.get_lane_stats()[WorkCellLane.docs.value]
        assert stats["active"] >= 0


# ---------------------------------------------------------------------------
# TestGetLaneStats
# ---------------------------------------------------------------------------


class TestGetLaneStats:
    """get_lane_stats returns an accurate snapshot of all lanes."""

    def test_all_lanes_present_in_stats(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane in WorkCellLane:
            assert lane.value in stats

    def test_stats_dict_has_expected_keys(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        expected_keys = {"active", "queued", "completed", "max_wip"}
        for lane_stats in stats.values():
            assert expected_keys.issubset(set(lane_stats.keys()))

    def test_max_wip_matches_default_config(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane in WorkCellLane:
            expected_max_wip = DEFAULT_LANE_CONFIGS[lane].max_wip
            assert stats[lane.value]["max_wip"] == expected_max_wip

    def test_stats_are_serialisable_to_dict(self, enforcer: LaneEnforcer) -> None:
        """get_lane_stats should return plain Python dicts suitable for JSON."""
        stats = enforcer.get_lane_stats()
        assert isinstance(stats, dict)
        for key, val in stats.items():
            assert isinstance(key, str)
            assert isinstance(val, dict)

    def test_stats_snapshot_is_independent_of_later_changes(
        self, enforcer: LaneEnforcer
    ) -> None:
        """get_lane_stats returns a snapshot; modifying the enforcer after the
        call must not retroactively alter the returned dict."""
        stats_before = enforcer.get_lane_stats()
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.low)
        # stats_before must not have been mutated
        assert stats_before[WorkCellLane.docs.value]["active"] == 0

    def test_stats_keys_are_lane_values(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        valid_lane_values = {lane.value for lane in WorkCellLane}
        assert set(stats.keys()) == valid_lane_values

    def test_stats_counts_are_integers(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.low)
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            for count_key in ("active", "queued", "completed", "max_wip"):
                assert isinstance(lane_stats[count_key], int)


# ---------------------------------------------------------------------------
# TestGetAssignment
# ---------------------------------------------------------------------------


class TestGetAssignment:
    """get_assignment returns the correct lane or None."""

    def test_unknown_task_returns_none(self, enforcer: LaneEnforcer) -> None:
        assert enforcer.get_assignment("not-there") is None

    def test_assigned_task_returns_correct_lane(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("ga-task", TaskType.security_change, RiskTier.low)
        assert enforcer.get_assignment("ga-task") == WorkCellLane.security_change

    def test_completed_task_assignment_still_present(
        self, enforcer: LaneEnforcer
    ) -> None:
        """Completing a task does not remove it from the assignment index."""
        enforcer.assign_lane("done", TaskType.docs_update, RiskTier.low)
        enforcer.complete_task("done")
        assert enforcer.get_assignment("done") == WorkCellLane.docs

    def test_empty_string_task_id_returns_none(self, enforcer: LaneEnforcer) -> None:
        assert enforcer.get_assignment("") is None

    def test_get_assignment_does_not_mutate_state(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.low)
        before = enforcer.get_lane_stats()
        enforcer.get_assignment("t1")
        after = enforcer.get_lane_stats()
        assert before == after


# ---------------------------------------------------------------------------
# TestReset
# ---------------------------------------------------------------------------


class TestReset:
    """LaneEnforcer.reset() clears all mutable state."""

    def test_reset_clears_all_assignments(self, enforcer: LaneEnforcer) -> None:
        for i in range(5):
            enforcer.assign_lane(f"t{i}", TaskType.docs_update, RiskTier.low)
        enforcer.reset()
        for i in range(5):
            assert enforcer.get_assignment(f"t{i}") is None

    def test_reset_zeroes_active_counters(self, enforcer: LaneEnforcer) -> None:
        for i in range(3):
            enforcer.assign_lane(f"t{i}", TaskType.docs_update, RiskTier.low)
        enforcer.reset()
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            assert lane_stats["active"] == 0

    def test_reset_zeroes_queued_counters(self, enforcer: LaneEnforcer) -> None:
        # Fill deployment + overflow to create queued tasks
        for i in range(3):
            enforcer.assign_lane(f"d{i}", TaskType.deployment, RiskTier.low)
        enforcer.reset()
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            assert lane_stats["queued"] == 0

    def test_reset_zeroes_completed_counters(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.docs_update, RiskTier.low)
        enforcer.complete_task("t1")
        enforcer.reset()
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            assert lane_stats["completed"] == 0

    def test_reset_allows_fresh_assignment_of_same_task_id(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.assign_lane("reusable", TaskType.docs_update, RiskTier.low)
        enforcer.reset()
        # Re-assigning should now increment active (not be idempotent skip)
        enforcer.assign_lane("reusable", TaskType.security_change, RiskTier.low)
        # After reset the new assignment wins
        assert enforcer.get_assignment("reusable") == WorkCellLane.security_change

    def test_reset_can_be_called_multiple_times_safely(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.reset()
        enforcer.reset()
        # No exception and state is still clean
        assert enforcer.get_assignment("any") is None

    def test_check_wip_returns_true_for_all_lanes_after_reset(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i in range(3):
            enforcer.assign_lane(f"d{i}", TaskType.deployment, RiskTier.low)
        enforcer.reset()
        for lane in WorkCellLane:
            assert enforcer.check_wip(lane) is True


# ---------------------------------------------------------------------------
# TestLaneEnforcerAllLanes (parametrised)
# ---------------------------------------------------------------------------


_LANE_TASK_TYPE_MAP = {
    WorkCellLane.docs: TaskType.docs_update,
    WorkCellLane.test_writing: TaskType.test_writing,
    WorkCellLane.security_change: TaskType.security_change,
    WorkCellLane.deployment: TaskType.deployment,
    WorkCellLane.refactor: TaskType.code_refactor,
}


@pytest.mark.parametrize("lane,task_type", list(_LANE_TASK_TYPE_MAP.items()))
class TestAllLanesBasicLifecycle:
    """Parametrised tests exercising the assign → check → complete lifecycle
    for each major lane."""

    def test_assign_and_complete_lifecycle(
        self, enforcer: LaneEnforcer, lane: WorkCellLane, task_type: TaskType
    ) -> None:
        tid = f"lifecycle-{lane.value}"
        assigned_lane = enforcer.assign_lane(tid, task_type, RiskTier.low)
        assert assigned_lane == lane
        stats_after_assign = enforcer.get_lane_stats()[lane.value]
        assert stats_after_assign["active"] == 1

        result = enforcer.complete_task(tid)
        assert result == lane
        stats_after_complete = enforcer.get_lane_stats()[lane.value]
        assert stats_after_complete["active"] == 0
        assert stats_after_complete["completed"] == 1

    def test_wip_check_before_and_after_filling(
        self, enforcer: LaneEnforcer, lane: WorkCellLane, task_type: TaskType
    ) -> None:
        config = get_lane_config(lane)
        assert enforcer.check_wip(lane) is True
        for i in range(config.max_wip):
            enforcer.assign_lane(f"{lane.value}-fill-{i}", task_type, RiskTier.low)
        assert enforcer.check_wip(lane) is False


# ---------------------------------------------------------------------------
# TestSingletonGetLaneEnforcer
# ---------------------------------------------------------------------------


class TestSingletonGetLaneEnforcer:
    """get_lane_enforcer() always returns the same shared instance."""

    def test_returns_lane_enforcer_instance(self) -> None:
        instance = get_lane_enforcer()
        assert isinstance(instance, LaneEnforcer)

    def test_same_instance_on_repeated_calls(self) -> None:
        a = get_lane_enforcer()
        b = get_lane_enforcer()
        c = get_lane_enforcer()
        assert a is b
        assert b is c

    def test_state_persists_between_get_calls(self) -> None:
        e1 = get_lane_enforcer()
        e1.assign_lane("singleton-persist", TaskType.docs_update, RiskTier.low)
        e2 = get_lane_enforcer()
        assert e2.get_assignment("singleton-persist") == WorkCellLane.docs

    def test_different_instances_share_state_via_singleton(self) -> None:
        e1 = get_lane_enforcer()
        e2 = get_lane_enforcer()
        e1.assign_lane("share-task", TaskType.test_writing, RiskTier.low)
        assert e2.get_assignment("share-task") == WorkCellLane.test_writing

    def test_singleton_is_not_a_bare_lane_enforcer_direct(self) -> None:
        singleton = get_lane_enforcer()
        fresh = LaneEnforcer()
        assert singleton is not fresh


# ---------------------------------------------------------------------------
# TestSingletonResetLaneEnforcer
# ---------------------------------------------------------------------------


class TestSingletonResetLaneEnforcer:
    """reset_lane_enforcer() destroys the singleton so the next get creates a new one."""

    def test_reset_causes_new_instance_on_next_get(self) -> None:
        first = get_lane_enforcer()
        reset_lane_enforcer()
        second = get_lane_enforcer()
        assert first is not second

    def test_state_does_not_carry_over_after_reset(self) -> None:
        e = get_lane_enforcer()
        e.assign_lane("carry-task", TaskType.docs_update, RiskTier.low)
        reset_lane_enforcer()
        fresh = get_lane_enforcer()
        assert fresh.get_assignment("carry-task") is None

    def test_reset_is_idempotent(self) -> None:
        reset_lane_enforcer()
        reset_lane_enforcer()  # second reset must not crash
        instance = get_lane_enforcer()
        assert isinstance(instance, LaneEnforcer)

    def test_singleton_thread_safety_on_first_creation(self) -> None:
        """Multiple threads calling get_lane_enforcer() concurrently must all
        receive the same object."""
        results: list[LaneEnforcer] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(20)

        def _get() -> None:
            try:
                barrier.wait()
                results.append(get_lane_enforcer())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # All threads should have obtained the same instance
        assert all(r is results[0] for r in results)


# ---------------------------------------------------------------------------
# TestConcurrentAccess
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    """Verify RLock-based thread-safety under concurrent workloads."""

    def test_concurrent_assign_no_lost_assignments(
        self, enforcer: LaneEnforcer
    ) -> None:
        n = 100
        errors: list[Exception] = []

        def _work(i: int) -> None:
            try:
                enforcer.assign_lane(f"conc-{i}", TaskType.test_writing, RiskTier.low)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_work, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assigned = sum(
            1 for i in range(n) if enforcer.get_assignment(f"conc-{i}") is not None
        )
        assert assigned == n

    def test_concurrent_assign_stats_consistency(self, enforcer: LaneEnforcer) -> None:
        n = 60
        barrier = threading.Barrier(n)

        def _work(i: int) -> None:
            barrier.wait()
            enforcer.assign_lane(f"barrier-{i}", TaskType.test_writing, RiskTier.low)

        threads = [threading.Thread(target=_work, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = enforcer.get_lane_stats()[WorkCellLane.test_writing.value]
        total = stats["active"] + stats["queued"]
        assert total == n

    def test_concurrent_complete_no_negative_active(
        self, enforcer: LaneEnforcer
    ) -> None:
        # Assign tasks, then complete them concurrently
        n = 40
        for i in range(n):
            enforcer.assign_lane(f"comp-{i}", TaskType.docs_update, RiskTier.low)

        errors: list[Exception] = []

        def _complete(i: int) -> None:
            try:
                enforcer.complete_task(f"comp-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_complete, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = enforcer.get_lane_stats()[WorkCellLane.docs.value]
        assert stats["active"] >= 0  # must never go negative

    def test_concurrent_read_write_no_exceptions(self, enforcer: LaneEnforcer) -> None:
        """Simultaneous readers (get_lane_stats) and writers (assign_lane) must
        not raise any exceptions."""
        errors: list[Exception] = []
        stop_event = threading.Event()

        def _writer(i: int) -> None:
            j = 0
            while not stop_event.is_set():
                try:
                    enforcer.assign_lane(f"rw-{i}-{j}", TaskType.docs_update, RiskTier.low)
                    j += 1
                except Exception as exc:
                    errors.append(exc)

        def _reader() -> None:
            for _ in range(30):
                try:
                    enforcer.get_lane_stats()
                except Exception as exc:
                    errors.append(exc)

        writers = [threading.Thread(target=_writer, args=(i,)) for i in range(3)]
        readers = [threading.Thread(target=_reader) for _ in range(5)]

        for t in writers:
            t.start()
        for t in readers:
            t.start()
        for t in readers:
            t.join()
        stop_event.set()
        for t in writers:
            t.join()

        assert not errors, f"Errors under concurrent read/write: {errors}"

    def test_concurrent_mixed_operations_no_corruption(
        self, enforcer: LaneEnforcer
    ) -> None:
        """assign, complete, and get_assignment run concurrently without error."""
        errors: list[Exception] = []
        n = 30
        for i in range(n):
            enforcer.assign_lane(f"mix-{i}", TaskType.test_writing, RiskTier.low)

        def _mixed(i: int) -> None:
            try:
                enforcer.assign_lane(f"mix-new-{i}", TaskType.test_writing, RiskTier.low)
                enforcer.complete_task(f"mix-{i}")
                enforcer.get_assignment(f"mix-new-{i}")
                enforcer.get_lane_stats()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_mixed, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


# ---------------------------------------------------------------------------
# TestLoggingInteractions
# ---------------------------------------------------------------------------


class TestLoggingInteractions:
    """Verify the service doesn't raise when logger calls are made."""

    def test_assign_new_task_logs_info_without_error(self, enforcer: LaneEnforcer) -> None:
        # Should not raise regardless of logging configuration
        enforcer.assign_lane("log-new", TaskType.docs_update, RiskTier.low)

    def test_assign_existing_task_logs_debug_without_error(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.assign_lane("log-dup", TaskType.docs_update, RiskTier.low)
        enforcer.assign_lane("log-dup", TaskType.docs_update, RiskTier.low)

    def test_complete_unknown_logs_warning_without_error(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.complete_task("log-unknown")

    def test_complete_known_logs_info_without_error(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.assign_lane("log-known", TaskType.test_writing, RiskTier.low)
        enforcer.complete_task("log-known")


# ---------------------------------------------------------------------------
# TestStatsCounting — full multi-task lifecycle scenarios
# ---------------------------------------------------------------------------


class TestStatsCounting:
    """End-to-end counting accuracy across multi-task lifecycle scenarios."""

    def test_sequential_assign_complete_cycle(self, enforcer: LaneEnforcer) -> None:
        """Assign n tasks, complete all, stats must reflect counts accurately."""
        n = 8
        for i in range(n):
            enforcer.assign_lane(f"sc-{i}", TaskType.test_writing, RiskTier.low)
        for i in range(n):
            enforcer.complete_task(f"sc-{i}")

        stats = enforcer.get_lane_stats()[WorkCellLane.test_writing.value]
        assert stats["active"] == 0
        assert stats["completed"] == n
        assert stats["queued"] == 0

    def test_partial_complete_leaves_remaining_active(
        self, enforcer: LaneEnforcer
    ) -> None:
        for i in range(6):
            enforcer.assign_lane(f"pc-{i}", TaskType.test_writing, RiskTier.low)
        for i in range(3):
            enforcer.complete_task(f"pc-{i}")

        stats = enforcer.get_lane_stats()[WorkCellLane.test_writing.value]
        assert stats["active"] == 3
        assert stats["completed"] == 3

    def test_overflow_queue_then_drain_completely(self, enforcer: LaneEnforcer) -> None:
        """Fill past max_wip, then complete everything; all counters should settle."""
        # deployment max_wip=1, add 3
        for i in range(3):
            enforcer.assign_lane(f"ov-{i}", TaskType.deployment, RiskTier.low)

        stats_filled = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert stats_filled["active"] == 1
        assert stats_filled["queued"] == 2

        # Complete d0 — promotes d1 from queue
        enforcer.complete_task("ov-0")
        mid = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert mid["active"] == 1
        assert mid["queued"] == 1
        assert mid["completed"] == 1

        # Complete d1 — promotes d2
        enforcer.complete_task("ov-1")
        mid2 = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert mid2["active"] == 1
        assert mid2["queued"] == 0
        assert mid2["completed"] == 2

        # Complete d2 — lane empty
        enforcer.complete_task("ov-2")
        final = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert final["active"] == 0
        assert final["queued"] == 0
        assert final["completed"] == 3

    def test_cross_lane_stats_are_isolated(self, enforcer: LaneEnforcer) -> None:
        """Work in one lane must not bleed into another lane's stats."""
        for i in range(5):
            enforcer.assign_lane(f"d{i}", TaskType.docs_update, RiskTier.low)
        for i in range(3):
            enforcer.assign_lane(f"s{i}", TaskType.security_change, RiskTier.low)

        enforcer.complete_task("d0")
        enforcer.complete_task("s0")

        docs_stats = enforcer.get_lane_stats()[WorkCellLane.docs.value]
        sec_stats = enforcer.get_lane_stats()[WorkCellLane.security_change.value]

        assert docs_stats["active"] == 4
        assert docs_stats["completed"] == 1
        assert sec_stats["active"] == 2  # security max_wip=2; s0 complete → s2 promoted? no s2 not assigned
        assert sec_stats["completed"] == 1

    def test_total_active_plus_queued_always_equals_assigned_minus_completed(
        self, enforcer: LaneEnforcer
    ) -> None:
        """For the docs lane: active + queued + completed == total assignments."""
        n = 15
        for i in range(n):
            enforcer.assign_lane(f"inv-{i}", TaskType.docs_update, RiskTier.low)
        for i in range(6):
            enforcer.complete_task(f"inv-{i}")

        stats = enforcer.get_lane_stats()[WorkCellLane.docs.value]
        assert stats["active"] + stats["completed"] == n  # queued==0 since max_wip=12 > 9
