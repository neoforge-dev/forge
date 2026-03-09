"""Tests for Dark Factory Phase 5 — QueueOptimizer (DF-5002).

Coverage:
- QueueOptimizer init with defaults and custom config
- can_dispatch(): allowed / blocked at lane WIP / blocked at global WIP
- task_started() / task_completed(): counter tracking, floor at 0
- task_queued() / task_dequeued(): queue depth tracking, floor at 0
- can_retry(): allowed within budget, blocked when exhausted, exhausted_tasks
- record_retry(): counter increment
- update_wip_limit() / update_retry_budget() / update_global_wip()
- get_lane_state(): known and unknown lanes
- get_summary(): totals, utilization, lane_states
- LaneQueueState: available_slots, utilization (including 0 wip_limit), to_dict
- DispatchDecision / RetryDecision / QueueSummary: to_dict
- Singleton: get_queue_optimizer / reset_queue_optimizer
- Edge cases: unknown lane defaults, task_completed on empty lane
"""

from __future__ import annotations

import pytest

from forge_harness.dark_factory.queue_optimizer import (
    DEFAULT_GLOBAL_WIP,
    DEFAULT_RETRY_BUDGETS,
    DEFAULT_WIP_LIMITS,
    DispatchDecision,
    LaneQueueState,
    QueueOptimizer,
    QueueSummary,
    RetryDecision,
    get_queue_optimizer,
    reset_queue_optimizer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the singleton before and after every test."""
    reset_queue_optimizer()
    yield
    reset_queue_optimizer()


@pytest.fixture()
def optimizer():
    return QueueOptimizer()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestQueueOptimizerInit:
    def test_defaults_applied(self):
        opt = QueueOptimizer()
        # Internal structures reflect DEFAULT_* constants
        assert opt._wip_limits == DEFAULT_WIP_LIMITS
        assert opt._retry_budgets == DEFAULT_RETRY_BUDGETS
        assert opt._global_wip == DEFAULT_GLOBAL_WIP

    def test_custom_wip_limits(self):
        custom = {"alpha": 7, "beta": 2}
        opt = QueueOptimizer(wip_limits=custom)
        assert opt._wip_limits == custom

    def test_custom_retry_budgets(self):
        custom = {"alpha": 5}
        opt = QueueOptimizer(retry_budgets=custom)
        assert opt._retry_budgets == custom

    def test_custom_global_wip(self):
        opt = QueueOptimizer(global_wip=42)
        assert opt._global_wip == 42

    def test_initial_totals_zero(self):
        opt = QueueOptimizer()
        assert opt._total_active == 0
        assert opt._lanes == {}


# ---------------------------------------------------------------------------
# can_dispatch
# ---------------------------------------------------------------------------


class TestCanDispatch:
    def test_dispatch_allowed_when_under_limits(self, optimizer):
        decision = optimizer.can_dispatch("task-1", "docs")
        assert decision.allowed is True
        assert decision.task_id == "task-1"
        assert decision.lane == "docs"
        assert decision.reason == ""

    def test_dispatch_blocked_at_lane_wip(self):
        opt = QueueOptimizer(wip_limits={"work": 2})
        # Fill lane to limit
        opt.task_started("t1", "work")
        opt.task_started("t2", "work")
        decision = opt.can_dispatch("t3", "work")
        assert decision.allowed is False
        assert "WIP limit reached" in decision.reason
        assert decision.queue_position >= 1

    def test_dispatch_blocked_at_global_wip(self):
        opt = QueueOptimizer(wip_limits={"work": 100}, global_wip=2)
        opt.task_started("t1", "work")
        opt.task_started("t2", "work")
        decision = opt.can_dispatch("t3", "work")
        assert decision.allowed is False
        assert "Global WIP limit reached" in decision.reason

    def test_dispatch_global_checked_before_lane(self):
        # Global WIP = 1, lane wip_limit = 10 → global triggers first
        opt = QueueOptimizer(wip_limits={"work": 10}, global_wip=1)
        opt.task_started("t1", "work")
        decision = opt.can_dispatch("t2", "work")
        assert decision.allowed is False
        assert "Global WIP" in decision.reason

    def test_queue_position_reflects_queued_count(self):
        opt = QueueOptimizer(wip_limits={"work": 1})
        opt.task_started("t1", "work")
        opt.task_queued("t2", "work")
        opt.task_queued("t3", "work")
        decision = opt.can_dispatch("t4", "work")
        assert decision.queue_position == 3  # 2 already queued + 1

    def test_dispatch_returns_dispatch_decision(self, optimizer):
        result = optimizer.can_dispatch("x", "docs")
        assert isinstance(result, DispatchDecision)


# ---------------------------------------------------------------------------
# task_started / task_completed
# ---------------------------------------------------------------------------


class TestTaskStartedCompleted:
    def test_task_started_increments_active_count(self, optimizer):
        optimizer.task_started("t1", "docs")
        state = optimizer.get_lane_state("docs")
        assert state.active_count == 1
        assert optimizer._total_active == 1

    def test_multiple_tasks_accumulate(self, optimizer):
        optimizer.task_started("t1", "docs")
        optimizer.task_started("t2", "docs")
        optimizer.task_started("t3", "test_writing")
        assert optimizer._total_active == 3
        assert optimizer.get_lane_state("docs").active_count == 2

    def test_task_completed_decrements(self, optimizer):
        optimizer.task_started("t1", "docs")
        optimizer.task_started("t2", "docs")
        optimizer.task_completed("t1", "docs")
        state = optimizer.get_lane_state("docs")
        assert state.active_count == 1
        assert optimizer._total_active == 1

    def test_task_completed_removes_retry_count(self, optimizer):
        optimizer.task_started("t1", "docs")
        optimizer.record_retry("t1", "docs")
        optimizer.task_completed("t1", "docs")
        state = optimizer.get_lane_state("docs")
        assert "t1" not in state.retry_counts

    def test_task_completed_never_goes_below_zero_lane(self, optimizer):
        # Complete without ever starting — floor at 0
        optimizer.task_completed("ghost", "docs")
        assert optimizer.get_lane_state("docs").active_count == 0

    def test_task_completed_never_goes_below_zero_global(self, optimizer):
        optimizer.task_completed("ghost", "docs")
        assert optimizer._total_active == 0

    def test_task_started_registers_retry_entry(self, optimizer):
        optimizer.task_started("t1", "docs")
        state = optimizer.get_lane_state("docs")
        assert "t1" in state.retry_counts
        assert state.retry_counts["t1"] == 0

    def test_task_started_does_not_overwrite_existing_retry(self, optimizer):
        optimizer.task_started("t1", "docs")
        optimizer.record_retry("t1", "docs")
        # Start same task again (e.g. after re-queue) — count must not reset
        optimizer.task_started("t1", "docs")
        state = optimizer.get_lane_state("docs")
        assert state.retry_counts["t1"] == 1


# ---------------------------------------------------------------------------
# task_queued / task_dequeued
# ---------------------------------------------------------------------------


class TestTaskQueuedDequeued:
    def test_queued_increments_count(self, optimizer):
        optimizer.task_queued("t1", "docs")
        assert optimizer.get_lane_state("docs").queued_count == 1

    def test_multiple_queued(self, optimizer):
        optimizer.task_queued("t1", "docs")
        optimizer.task_queued("t2", "docs")
        assert optimizer.get_lane_state("docs").queued_count == 2

    def test_dequeued_decrements(self, optimizer):
        optimizer.task_queued("t1", "docs")
        optimizer.task_queued("t2", "docs")
        optimizer.task_dequeued("t1", "docs")
        assert optimizer.get_lane_state("docs").queued_count == 1

    def test_dequeued_never_below_zero(self, optimizer):
        optimizer.task_dequeued("ghost", "docs")
        assert optimizer.get_lane_state("docs").queued_count == 0


# ---------------------------------------------------------------------------
# can_retry / record_retry
# ---------------------------------------------------------------------------


class TestRetry:
    def test_can_retry_allowed_within_budget(self, optimizer):
        # "docs" has retry_budget=3
        optimizer.task_started("t1", "docs")
        decision = optimizer.can_retry("t1", "docs")
        assert decision.allowed is True
        assert decision.attempt == 1
        assert decision.max_attempts == DEFAULT_RETRY_BUDGETS["docs"]

    def test_can_retry_blocked_when_exhausted(self):
        opt = QueueOptimizer(retry_budgets={"work": 1})
        opt.task_started("t1", "work")
        opt.record_retry("t1", "work")  # 1/1 used
        decision = opt.can_retry("t1", "work")
        assert decision.allowed is False
        assert "exhausted" in decision.reason.lower()

    def test_can_retry_adds_to_exhausted_tasks(self):
        opt = QueueOptimizer(retry_budgets={"work": 0})
        decision = opt.can_retry("t1", "work")
        assert decision.allowed is False
        state = opt.get_lane_state("work")
        assert "t1" in state.exhausted_tasks

    def test_can_retry_exhausted_task_not_duplicated(self):
        opt = QueueOptimizer(retry_budgets={"work": 0})
        opt.can_retry("t1", "work")
        opt.can_retry("t1", "work")
        state = opt.get_lane_state("work")
        assert state.exhausted_tasks.count("t1") == 1

    def test_record_retry_increments_counter(self, optimizer):
        optimizer.task_started("t1", "docs")
        count = optimizer.record_retry("t1", "docs")
        assert count == 1
        count = optimizer.record_retry("t1", "docs")
        assert count == 2

    def test_record_retry_without_prior_start(self, optimizer):
        # record_retry should initialise count from 0
        count = optimizer.record_retry("fresh", "docs")
        assert count == 1

    def test_can_retry_returns_retry_decision(self, optimizer):
        result = optimizer.can_retry("t1", "docs")
        assert isinstance(result, RetryDecision)

    def test_deployment_lane_has_zero_budget(self):
        opt = QueueOptimizer()
        decision = opt.can_retry("deploy-1", "deployment")
        assert decision.allowed is False
        assert decision.max_attempts == 0


# ---------------------------------------------------------------------------
# update_wip_limit / update_retry_budget / update_global_wip
# ---------------------------------------------------------------------------


class TestUpdates:
    def test_update_wip_limit(self, optimizer):
        optimizer.update_wip_limit("docs", 99)
        state = optimizer.get_lane_state("docs")
        assert state.wip_limit == 99
        assert optimizer._wip_limits["docs"] == 99

    def test_update_wip_limit_new_lane(self, optimizer):
        optimizer.update_wip_limit("custom_lane", 7)
        state = optimizer.get_lane_state("custom_lane")
        assert state.wip_limit == 7

    def test_update_retry_budget(self, optimizer):
        optimizer.update_retry_budget("docs", 10)
        state = optimizer.get_lane_state("docs")
        assert state.retry_budget == 10
        assert optimizer._retry_budgets["docs"] == 10

    def test_update_global_wip(self, optimizer):
        optimizer.update_global_wip(50)
        assert optimizer._global_wip == 50

    def test_update_wip_limit_affects_can_dispatch(self):
        opt = QueueOptimizer(wip_limits={"work": 2})
        opt.task_started("t1", "work")
        opt.task_started("t2", "work")
        # At limit — should be blocked
        assert opt.can_dispatch("t3", "work").allowed is False
        # Raise limit — should be allowed now
        opt.update_wip_limit("work", 5)
        assert opt.can_dispatch("t3", "work").allowed is True

    def test_update_global_wip_affects_can_dispatch(self):
        opt = QueueOptimizer(global_wip=1)
        opt.task_started("t1", "docs")
        assert opt.can_dispatch("t2", "docs").allowed is False
        opt.update_global_wip(10)
        assert opt.can_dispatch("t2", "docs").allowed is True


# ---------------------------------------------------------------------------
# get_lane_state
# ---------------------------------------------------------------------------


class TestGetLaneState:
    def test_known_lane_returns_state(self, optimizer):
        state = optimizer.get_lane_state("docs")
        assert isinstance(state, LaneQueueState)
        assert state.lane == "docs"
        assert state.wip_limit == DEFAULT_WIP_LIMITS["docs"]

    def test_unknown_lane_creates_with_defaults(self, optimizer):
        state = optimizer.get_lane_state("totally_unknown")
        assert state.lane == "totally_unknown"
        assert state.wip_limit == 3   # fallback default
        assert state.retry_budget == 2  # fallback default

    def test_lane_state_active_count_zero_initially(self, optimizer):
        state = optimizer.get_lane_state("docs")
        assert state.active_count == 0
        assert state.queued_count == 0


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------


class TestGetSummary:
    def test_empty_summary(self, optimizer):
        summary = optimizer.get_summary()
        assert isinstance(summary, QueueSummary)
        assert summary.total_active == 0
        assert summary.total_queued == 0
        assert summary.total_exhausted == 0
        assert summary.global_wip_limit == DEFAULT_GLOBAL_WIP
        assert summary.global_utilization == 0.0

    def test_summary_totals(self):
        opt = QueueOptimizer(wip_limits={"a": 5, "b": 5}, global_wip=20)
        opt.task_started("t1", "a")
        opt.task_started("t2", "a")
        opt.task_started("t3", "b")
        opt.task_queued("tq1", "a")
        summary = opt.get_summary()
        assert summary.total_active == 3
        assert summary.total_queued == 1

    def test_summary_exhausted_count(self):
        opt = QueueOptimizer(retry_budgets={"a": 0})
        opt.can_retry("t1", "a")
        opt.can_retry("t2", "a")
        summary = opt.get_summary()
        assert summary.total_exhausted == 2

    def test_summary_utilization(self):
        opt = QueueOptimizer(global_wip=10)
        opt.task_started("t1", "docs")
        opt.task_started("t2", "docs")
        summary = opt.get_summary()
        assert abs(summary.global_utilization - 0.2) < 1e-6

    def test_summary_lane_states_included(self):
        opt = QueueOptimizer()
        opt.task_started("t1", "docs")
        opt.task_started("t2", "research")
        summary = opt.get_summary()
        lanes = {s["lane"] for s in summary.lane_states}
        assert "docs" in lanes
        assert "research" in lanes

    def test_summary_to_dict(self, optimizer):
        d = optimizer.get_summary().to_dict()
        required = {
            "total_active", "total_queued", "total_exhausted",
            "global_wip_limit", "global_utilization", "lane_count", "lane_states",
        }
        assert required.issubset(d.keys())


# ---------------------------------------------------------------------------
# LaneQueueState
# ---------------------------------------------------------------------------


class TestLaneQueueState:
    def _make(self, **kwargs):
        defaults = {
            "lane": "test",
            "wip_limit": 4,
            "retry_budget": 2,
            "active_count": 0,
        }
        defaults.update(kwargs)
        return LaneQueueState(**defaults)

    def test_available_slots_full_capacity(self):
        state = self._make(wip_limit=4, active_count=0)
        assert state.available_slots == 4

    def test_available_slots_partial(self):
        state = self._make(wip_limit=4, active_count=2)
        assert state.available_slots == 2

    def test_available_slots_at_limit(self):
        state = self._make(wip_limit=4, active_count=4)
        assert state.available_slots == 0

    def test_available_slots_over_limit_clamped(self):
        # active > wip should still give 0, not negative
        state = self._make(wip_limit=2, active_count=3)
        assert state.available_slots == 0

    def test_utilization_normal(self):
        state = self._make(wip_limit=4, active_count=2)
        assert abs(state.utilization - 0.5) < 1e-9

    def test_utilization_at_limit(self):
        state = self._make(wip_limit=4, active_count=4)
        assert state.utilization == 1.0

    def test_utilization_zero_wip_limit(self):
        # Division guard: wip_limit=0 → utilization=1.0
        state = self._make(wip_limit=0, active_count=0)
        assert state.utilization == 1.0

    def test_to_dict_keys(self):
        state = self._make(wip_limit=3, active_count=1, queued_count=2)
        d = state.to_dict()
        expected_keys = {
            "lane", "wip_limit", "retry_budget", "active_count",
            "queued_count", "available_slots", "utilization",
            "total_retries", "exhausted_tasks",
        }
        assert expected_keys.issubset(d.keys())

    def test_to_dict_values_match(self):
        state = self._make(wip_limit=5, active_count=3, queued_count=1)
        state.retry_counts["t1"] = 2
        state.retry_counts["t2"] = 1
        d = state.to_dict()
        assert d["available_slots"] == 2
        assert d["total_retries"] == 3
        assert abs(d["utilization"] - 0.6) < 0.01


# ---------------------------------------------------------------------------
# DispatchDecision
# ---------------------------------------------------------------------------


class TestDispatchDecision:
    def test_to_dict_allowed(self):
        dd = DispatchDecision(task_id="t1", lane="docs", allowed=True)
        d = dd.to_dict()
        assert d["task_id"] == "t1"
        assert d["lane"] == "docs"
        assert d["allowed"] is True
        assert d["reason"] == ""
        assert d["queue_position"] == 0

    def test_to_dict_blocked(self):
        dd = DispatchDecision(
            task_id="t2", lane="api_simple", allowed=False,
            reason="WIP limit reached", queue_position=3,
        )
        d = dd.to_dict()
        assert d["allowed"] is False
        assert d["reason"] == "WIP limit reached"
        assert d["queue_position"] == 3


# ---------------------------------------------------------------------------
# RetryDecision
# ---------------------------------------------------------------------------


class TestRetryDecision:
    def test_to_dict_allowed(self):
        rd = RetryDecision(
            task_id="t1", lane="docs", allowed=True, attempt=1, max_attempts=3,
        )
        d = rd.to_dict()
        assert d["task_id"] == "t1"
        assert d["allowed"] is True
        assert d["attempt"] == 1
        assert d["max_attempts"] == 3
        assert d["reason"] == ""

    def test_to_dict_blocked(self):
        rd = RetryDecision(
            task_id="t2", lane="docs", allowed=False, attempt=4, max_attempts=3,
            reason="budget exhausted",
        )
        d = rd.to_dict()
        assert d["allowed"] is False
        assert d["reason"] == "budget exhausted"


# ---------------------------------------------------------------------------
# QueueSummary
# ---------------------------------------------------------------------------


class TestQueueSummary:
    def test_to_dict_structure(self):
        qs = QueueSummary(
            total_active=3,
            total_queued=1,
            total_exhausted=2,
            global_wip_limit=15,
            global_utilization=0.2,
            lane_states=({"lane": "docs"},),
        )
        d = qs.to_dict()
        assert d["total_active"] == 3
        assert d["total_queued"] == 1
        assert d["total_exhausted"] == 2
        assert d["global_wip_limit"] == 15
        assert d["lane_count"] == 1
        assert isinstance(d["lane_states"], list)

    def test_utilization_rounded(self):
        qs = QueueSummary(
            total_active=1, total_queued=0, total_exhausted=0,
            global_wip_limit=3, global_utilization=1 / 3,
            lane_states=(),
        )
        d = qs.to_dict()
        assert d["global_utilization"] == round(1 / 3, 2)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_queue_optimizer_returns_instance(self):
        opt = get_queue_optimizer()
        assert isinstance(opt, QueueOptimizer)

    def test_get_queue_optimizer_same_instance(self):
        a = get_queue_optimizer()
        b = get_queue_optimizer()
        assert a is b

    def test_reset_queue_optimizer_creates_new_instance(self):
        a = get_queue_optimizer()
        reset_queue_optimizer()
        b = get_queue_optimizer()
        assert a is not b

    def test_reset_queue_optimizer_clears_none(self):
        # Reset when no instance yet — should not raise
        reset_queue_optimizer()
        reset_queue_optimizer()  # Second call is a no-op, not an error

    def test_singleton_state_persists(self):
        opt = get_queue_optimizer()
        opt.task_started("t1", "docs")
        opt2 = get_queue_optimizer()
        assert opt2._total_active == 1

    def test_reset_clears_state(self):
        opt = get_queue_optimizer()
        opt.task_started("t1", "docs")
        reset_queue_optimizer()
        fresh = get_queue_optimizer()
        assert fresh._total_active == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unknown_lane_defaults_wip_limit_3(self, optimizer):
        state = optimizer.get_lane_state("mystery_lane")
        assert state.wip_limit == 3

    def test_unknown_lane_defaults_retry_budget_2(self, optimizer):
        state = optimizer.get_lane_state("mystery_lane")
        assert state.retry_budget == 2

    def test_task_completed_on_empty_lane_no_error(self, optimizer):
        # Must not raise even when lane has never had a task
        optimizer.task_completed("ghost", "never_used")
        state = optimizer.get_lane_state("never_used")
        assert state.active_count == 0

    def test_can_dispatch_after_completion_frees_slot(self):
        opt = QueueOptimizer(wip_limits={"work": 1})
        opt.task_started("t1", "work")
        assert opt.can_dispatch("t2", "work").allowed is False
        opt.task_completed("t1", "work")
        assert opt.can_dispatch("t2", "work").allowed is True

    def test_retry_decision_attempt_on_fresh_task(self, optimizer):
        # task not yet started — retry count defaults to 0
        decision = optimizer.can_retry("new_task", "docs")
        assert decision.attempt == 1

    def test_global_wip_zero_blocks_all_dispatch(self):
        opt = QueueOptimizer(global_wip=0)
        decision = opt.can_dispatch("t1", "docs")
        assert decision.allowed is False

    def test_reset_clears_lanes_and_totals(self, optimizer):
        optimizer.task_started("t1", "docs")
        optimizer.task_queued("t2", "research")
        optimizer.reset()
        assert optimizer._total_active == 0
        assert optimizer._lanes == {}

    def test_reset_restores_default_limits(self, optimizer):
        optimizer.update_wip_limit("docs", 99)
        optimizer.reset()
        # After reset _wip_limits is DEFAULT_WIP_LIMITS again
        assert optimizer._wip_limits["docs"] == DEFAULT_WIP_LIMITS["docs"]
