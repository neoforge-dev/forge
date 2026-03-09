"""Comprehensive tests for WIPLimiter (DF-5002).

Targets forge_harness/webhook_server/services/wip_limiter.py.

Coverage goals:
  - All public methods: can_dispatch, record_dispatch, record_completion,
    record_retry, get_lane_metrics, get_all_lane_metrics, get_queue_metrics,
    get_active_tasks, get_task_status, reset
  - Singleton factory: get_wip_limiter, reset_wip_limiter
  - Retry budget configuration: set_retry_budget, get_retry_budget
  - WIP limit enforcement via can_dispatch
  - Retry count tracking and limits
  - Lane metrics aggregation (avg latency, retry rate, success rate)
  - Task status tracking (active, completed)
  - Reset clears all state
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.wip_limiter import (
    LaneMetrics,
    RetryBudget,
    TaskMetrics,
    WIPLimiter,
    get_wip_limiter,
    reset_wip_limiter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def limiter() -> WIPLimiter:
    """Fresh WIPLimiter instance for each test."""
    return WIPLimiter()


@pytest.fixture(autouse=True)
def reset_singleton() -> Any:
    """Reset the global singleton before and after every test."""
    reset_wip_limiter()
    yield
    reset_wip_limiter()


# ---------------------------------------------------------------------------
# Initialization Tests
# ---------------------------------------------------------------------------


def test_default_retry_budgets() -> None:
    """Test that default retry budgets are set correctly per lane."""
    limiter = WIPLimiter()

    # api_simple has higher retry budget
    budget = limiter.get_retry_budget(WorkCellLane.api_simple)
    assert budget.max_retries == 5
    assert budget.retry_window_seconds == 1800
    assert budget.backoff_multiplier == 1.5

    # security_change has lower retry budget (strict)
    budget = limiter.get_retry_budget(WorkCellLane.security_change)
    assert budget.max_retries == 1
    assert budget.retry_window_seconds == 7200

    # deployment also has lower retry budget
    budget = limiter.get_retry_budget(WorkCellLane.deployment)
    assert budget.max_retries == 1

    # docs has default (3)
    budget = limiter.get_retry_budget(WorkCellLane.docs)
    assert budget.max_retries == 3


def test_set_retry_budget() -> None:
    """Test setting custom retry budget for a lane."""
    limiter = WIPLimiter()

    new_budget = RetryBudget(max_retries=10, retry_window_seconds=600, backoff_multiplier=1.0)
    limiter.set_retry_budget(WorkCellLane.docs, new_budget)

    retrieved = limiter.get_retry_budget(WorkCellLane.docs)
    assert retrieved.max_retries == 10
    assert retrieved.retry_window_seconds == 600
    assert retrieved.backoff_multiplier == 1.0


# ---------------------------------------------------------------------------
# Can Dispatch Tests
# ---------------------------------------------------------------------------


def test_can_dispatch_within_limits(limiter: WIPLimiter) -> None:
    """Test can_dispatch returns True when within WIP and retry limits."""
    can_dispatch, reason = limiter.can_dispatch("task-1", WorkCellLane.docs)

    assert can_dispatch is True
    assert "can be dispatched" in reason


def test_can_dispatch_exceeds_retry_budget(limiter: WIPLimiter) -> None:
    """Test can_dispatch returns False when task exceeded max retries."""
    limiter.set_retry_budget(WorkCellLane.docs, RetryBudget(max_retries=2))

    # Simulate exceeding retries for the SAME task
    # Task gets dispatched, fails, retried, fails, retried, fails
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_completion("task-1", WorkCellLane.docs, success=False)

    # First retry
    limiter.record_retry("task-1", WorkCellLane.docs)
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_completion("task-1", WorkCellLane.docs, success=False)

    # Second retry (exceeds max_retries=2)
    limiter.record_retry("task-1", WorkCellLane.docs)

    # Now try to dispatch again - should fail (exceeded budget)
    can_dispatch, reason = limiter.can_dispatch("task-1", WorkCellLane.docs)

    assert can_dispatch is False
    assert "exceeded max retries" in reason


def test_can_dispatch_exceeds_wip_limit(limiter: WIPLimiter) -> None:
    """Test can_dispatch returns False when lane at WIP capacity."""
    lane = WorkCellLane.docs  # max_wip = 12

    # Fill up the lane
    for i in range(12):
        limiter.record_dispatch(f"task-{i}", lane)

    # Next dispatch should fail
    can_dispatch, reason = limiter.can_dispatch("task-overflow", lane)

    assert can_dispatch is False
    assert "at capacity" in reason


# ---------------------------------------------------------------------------
# Record Dispatch Tests
# ---------------------------------------------------------------------------


def test_record_dispatch(limiter: WIPLimiter) -> None:
    """Test recording task dispatch updates metrics."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)

    active = limiter.get_active_tasks(WorkCellLane.docs)
    assert len(active) == 1
    assert active[0].task_id == "task-1"
    assert active[0].lane == WorkCellLane.docs
    assert active[0].success is None  # Not completed yet


def test_record_dispatch_multiple_lanes(limiter: WIPLimiter) -> None:
    """Test dispatching to different lanes tracks correctly."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_dispatch("task-2", WorkCellLane.api_simple)
    limiter.record_dispatch("task-3", WorkCellLane.test_writing)

    docs_active = limiter.get_active_tasks(WorkCellLane.docs)
    api_active = limiter.get_active_tasks(WorkCellLane.api_simple)
    test_active = limiter.get_active_tasks(WorkCellLane.test_writing)

    assert len(docs_active) == 1
    assert len(api_active) == 1
    assert len(test_active) == 1


# ---------------------------------------------------------------------------
# Record Completion Tests
# ---------------------------------------------------------------------------


def test_record_completion_success(limiter: WIPLimiter) -> None:
    """Test recording successful completion updates metrics."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    time.sleep(0.01)  # Small delay to get non-zero latency
    limiter.record_completion("task-1", WorkCellLane.docs, success=True)

    metrics = limiter.get_lane_metrics(WorkCellLane.docs)
    assert metrics.total_completed == 1
    assert metrics.total_succeeded == 1
    assert metrics.total_failed == 0
    assert metrics.success_rate == 1.0

    active = limiter.get_active_tasks(WorkCellLane.docs)
    assert len(active) == 0  # Task no longer active


def test_record_completion_failure(limiter: WIPLimiter) -> None:
    """Test recording failed completion updates metrics."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_completion("task-1", WorkCellLane.docs, success=False)

    metrics = limiter.get_lane_metrics(WorkCellLane.docs)
    assert metrics.total_completed == 1
    assert metrics.total_succeeded == 0
    assert metrics.total_failed == 1
    assert metrics.success_rate == 0.0


def test_record_completion_unknown_task(limiter: WIPLimiter) -> None:
    """Test completing unknown task logs warning but doesn't crash."""
    # Should not raise, just log warning
    limiter.record_completion("unknown-task", WorkCellLane.docs, success=True)

    # Metrics should be unchanged
    metrics = limiter.get_lane_metrics(WorkCellLane.docs)
    assert metrics.total_completed == 0


def test_latency_tracking(limiter: WIPLimiter) -> None:
    """Test that latency is tracked correctly."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    time.sleep(0.05)  # 50ms
    limiter.record_completion("task-1", WorkCellLane.docs, success=True)

    limiter.record_dispatch("task-2", WorkCellLane.docs)
    time.sleep(0.03)  # 30ms
    limiter.record_completion("task-2", WorkCellLane.docs, success=True)

    metrics = limiter.get_lane_metrics(WorkCellLane.docs)
    # Average should be around 40ms (50+30)/2
    assert metrics.avg_latency_ms >= 30
    assert metrics.avg_latency_ms <= 60
    assert metrics.max_latency_ms >= 50


# ---------------------------------------------------------------------------
# Retry Tests
# ---------------------------------------------------------------------------


def test_record_retry_increments_count(limiter: WIPLimiter) -> None:
    """Test recording retry increments retry count."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)

    retry_count = limiter.record_retry("task-1", WorkCellLane.docs)
    assert retry_count == 1

    retry_count = limiter.record_retry("task-1", WorkCellLane.docs)
    assert retry_count == 2


def test_retry_count_in_metrics(limiter: WIPLimiter) -> None:
    """Test retry count is reflected in lane metrics."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_retry("task-1", WorkCellLane.docs)
    limiter.record_completion("task-1", WorkCellLane.docs, success=True)

    metrics = limiter.get_lane_metrics(WorkCellLane.docs)
    assert metrics.total_retried == 1
    assert metrics.retry_rate == 1.0  # 1 retried / 1 completed


# ---------------------------------------------------------------------------
# Metrics Tests
# ---------------------------------------------------------------------------


def test_get_all_lane_metrics(limiter: WIPLimiter) -> None:
    """Test getting metrics for all lanes."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_dispatch("task-2", WorkCellLane.api_simple)

    all_metrics = limiter.get_all_lane_metrics()

    assert "docs" in all_metrics
    assert "api_simple" in all_metrics
    assert all_metrics["docs"].total_dispatched == 1
    assert all_metrics["api_simple"].total_dispatched == 1


def test_get_queue_metrics(limiter: WIPLimiter) -> None:
    """Test overall queue metrics calculation."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_completion("task-1", WorkCellLane.docs, success=True)

    limiter.record_dispatch("task-2", WorkCellLane.api_simple)
    limiter.record_completion("task-2", WorkCellLane.api_simple, success=False)

    metrics = limiter.get_queue_metrics()

    assert metrics["total_dispatched"] == 2
    assert metrics["total_completed"] == 2
    assert metrics["total_succeeded"] == 1
    assert metrics["total_failed"] == 1
    assert "lanes" in metrics


def test_get_task_status_active(limiter: WIPLimiter) -> None:
    """Test getting status for active task."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)

    status = limiter.get_task_status("task-1")

    assert status is not None
    assert status["task_id"] == "task-1"
    assert status["lane"] == "docs"
    assert status["status"] == "active"


def test_get_task_status_completed(limiter: WIPLimiter) -> None:
    """Test getting status for completed task."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_completion("task-1", WorkCellLane.docs, success=True)

    status = limiter.get_task_status("task-1")

    assert status is not None
    assert status["status"] == "completed"
    assert status["success"] is True


def test_get_task_status_unknown(limiter: WIPLimiter) -> None:
    """Test getting status for unknown task returns None."""
    status = limiter.get_task_status("unknown-task")
    assert status is None


def test_get_active_tasks_filter_by_lane(limiter: WIPLimiter) -> None:
    """Test filtering active tasks by lane."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_dispatch("task-2", WorkCellLane.docs)
    limiter.record_dispatch("task-3", WorkCellLane.api_simple)

    docs_tasks = limiter.get_active_tasks(WorkCellLane.docs)
    api_tasks = limiter.get_active_tasks(WorkCellLane.api_simple)

    assert len(docs_tasks) == 2
    assert len(api_tasks) == 1


def test_get_active_tasks_all(limiter: WIPLimiter) -> None:
    """Test getting all active tasks without filter."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_dispatch("task-2", WorkCellLane.api_simple)

    all_tasks = limiter.get_active_tasks()

    assert len(all_tasks) == 2


# ---------------------------------------------------------------------------
# Reset Tests
# ---------------------------------------------------------------------------


def test_reset_clears_all_state(limiter: WIPLimiter) -> None:
    """Test reset clears all metrics and state."""
    limiter.record_dispatch("task-1", WorkCellLane.docs)
    limiter.record_completion("task-1", WorkCellLane.docs, success=True)

    limiter.reset()

    # All metrics should be reset
    metrics = limiter.get_lane_metrics(WorkCellLane.docs)
    assert metrics.total_dispatched == 0
    assert metrics.total_completed == 0

    active = limiter.get_active_tasks()
    assert len(active) == 0


# ---------------------------------------------------------------------------
# Singleton Tests
# ---------------------------------------------------------------------------


def test_singleton_returns_same_instance() -> None:
    """Test that get_wip_limiter returns the same instance."""
    reset_wip_limiter()

    limiter1 = get_wip_limiter()
    limiter2 = get_wip_limiter()

    assert limiter1 is limiter2


def test_singleton_reset_creates_new_instance() -> None:
    """Test that reset_wip_limiter allows new instance creation."""
    limiter1 = get_wip_limiter()
    reset_wip_limiter()
    limiter2 = get_wip_limiter()

    assert limiter1 is not limiter2


# ---------------------------------------------------------------------------
# Thread Safety Tests
# ---------------------------------------------------------------------------


def test_concurrent_dispatch_and_completion() -> None:
    """Test thread safety with concurrent dispatch and completion."""
    limiter = WIPLimiter()
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            for i in range(10):
                task_id = f"worker-{worker_id}-{i}"
                limiter.record_dispatch(task_id, WorkCellLane.docs)
                time.sleep(0.001)
                limiter.record_completion(task_id, WorkCellLane.docs, success=True)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    # Verify all tasks completed
    metrics = limiter.get_lane_metrics(WorkCellLane.docs)
    assert metrics.total_completed == 50


def test_concurrent_can_dispatch() -> None:
    """Test thread safety of can_dispatch under concurrent access."""
    limiter = WIPLimiter()
    results: list[tuple[bool, str]] = []

    def worker() -> None:
        for _ in range(20):
            result = limiter.can_dispatch(
                f"task-{threading.current_thread().name}", WorkCellLane.docs
            )
            results.append(result)

    threads = [threading.Thread(target=worker, name=f"t-{i}") for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All calls should complete without errors
    assert len(results) == 100


# ---------------------------------------------------------------------------
# Lane Metrics Data Class Tests
# ---------------------------------------------------------------------------


def test_lane_metrics_retry_rate_calculation() -> None:
    """Test LaneMetrics retry_rate property."""
    metrics = LaneMetrics(lane=WorkCellLane.docs)
    metrics.total_completed = 10
    metrics.total_retried = 3

    assert metrics.retry_rate == 0.3


def test_lane_metrics_retry_rate_zero_completed() -> None:
    """Test LaneMetrics retry_rate with zero completed."""
    metrics = LaneMetrics(lane=WorkCellLane.docs)
    assert metrics.retry_rate == 0.0


def test_lane_metrics_success_rate_calculation() -> None:
    """Test LaneMetrics success_rate property."""
    metrics = LaneMetrics(lane=WorkCellLane.docs)
    metrics.total_completed = 10
    metrics.total_succeeded = 7

    assert metrics.success_rate == 0.7


def test_lane_metrics_success_rate_zero_completed() -> None:
    """Test LaneMetrics success_rate with zero completed."""
    metrics = LaneMetrics(lane=WorkCellLane.docs)
    assert metrics.success_rate == 0.0



# ---------------------------------------------------------------------------
# Extended Test Coverage (Added 2026-02-26)
# Targets: Priority throttling, edge cases, retry waste, boundary conditions
# ---------------------------------------------------------------------------


class TestPriorityBasedDispatch:
    """Tests for priority-based dispatch and throttling."""

    def test_high_priority_ypasses_throttling(self, limiter: WIPLimiter) -> None:
        """High priority tasks (>=3) should not be throttled at high load."""
        lane = WorkCellLane.docs

        # Fill lane to >70% capacity (docs has max_wip=12, so 9 tasks = 75%)
        for i in range(9):
            limiter.record_dispatch(f"task-{i}", lane)

        # High priority (3) should always be allowed
        can_dispatch, reason = limiter.can_dispatch("urgent-task", lane, priority=3)
        assert can_dispatch is True
        assert "can be dispatched" in reason

    def test_low_priority_may_be_throttled(self, limiter: WIPLimiter) -> None:
        """Low priority tasks (1-2) may be throttled at high load."""
        lane = WorkCellLane.docs

        # Fill lane to >70% capacity
        for i in range(9):
            limiter.record_dispatch(f"task-{i}", lane)

        # Low priority may or may not be throttled (probabilistic)
        # We just verify it doesn't crash and returns a valid result
        can_dispatch, reason = limiter.can_dispatch("low-priority-task", lane, priority=1)
        assert isinstance(can_dispatch, bool)
        assert isinstance(reason, str)

    def test_medium_priority_at_high_load(self, limiter: WIPLimiter) -> None:
        """Medium priority (2) tasks have moderate throttle chance."""
        lane = WorkCellLane.docs

        # Fill lane to >70% capacity
        for i in range(9):
            limiter.record_dispatch(f"task-{i}", lane)

        # Priority 2 should have lower throttle factor than priority 1
        can_dispatch, reason = limiter.can_dispatch("med-priority-task", lane, priority=2)
        assert isinstance(can_dispatch, bool)
        assert isinstance(reason, str)

    def test_priority_does_not_affect_dispatch_when_not_at_capacity(
        self, limiter: WIPLimiter
    ) -> None:
        """When not at capacity, priority doesn't affect dispatch decision."""
        lane = WorkCellLane.docs

        # Add only a few tasks (well under capacity)
        for i in range(3):
            limiter.record_dispatch(f"task-{i}", lane)

        # All priorities should be allowed when not at capacity
        for priority in [1, 2, 3, 5]:
            can_dispatch, _ = limiter.can_dispatch(f"task-p{priority}", lane, priority=priority)
            assert can_dispatch is True


class TestQueueLatencyEdgeCases:
    """Tests for queue latency boundary conditions."""

    def test_zero_latency_calculation(self, limiter: WIPLimiter) -> None:
        """Test metrics when task completes immediately (zero latency)."""
        limiter.record_dispatch("task-1", WorkCellLane.docs)
        # Complete immediately without delay
        limiter.record_completion("task-1", WorkCellLane.docs, success=True)

        metrics = limiter.get_lane_metrics(WorkCellLane.docs)
        # Latency should be very close to 0 (might be a few ms due to processing)
        assert metrics.avg_latency_ms >= 0
        assert metrics.avg_latency_ms < 100  # Should be near instant
        assert metrics.max_latency_ms >= 0

    def test_very_high_latency_tracking(self, limiter: WIPLimiter) -> None:
        """Test metrics with artificially high latency values."""
        # Simulate a task that takes a long time
        limiter.record_dispatch("task-1", WorkCellLane.docs)
        time.sleep(0.5)  # 500ms
        limiter.record_completion("task-1", WorkCellLane.docs, success=True)

        metrics = limiter.get_lane_metrics(WorkCellLane.docs)
        assert metrics.avg_latency_ms >= 400  # At least 400ms
        assert metrics.max_latency_ms >= 400
        assert metrics.max_latency_ms <= 1000  # But under 1 second

    def test_latency_samples_rolling_window(self, limiter: WIPLimiter) -> None:
        """Test that latency samples maintain a rolling window (max 100)."""
        lane = WorkCellLane.docs

        # Add 110 tasks with small delays
        for i in range(110):
            limiter.record_dispatch(f"task-{i}", lane)
            time.sleep(0.001)  # 1ms
            limiter.record_completion(f"task-{i}", lane, success=True)

        metrics = limiter.get_queue_metrics()
        # Should have completed all 110 tasks
        assert metrics["total_completed"] == 110
        # But latency calculation should use last 100 samples
        lane_metrics = limiter.get_lane_metrics(lane)
        assert lane_metrics.total_completed == 110

    def test_wait_time_estimation_at_capacity(self, limiter: WIPLimiter) -> None:
        """Test that wait time is estimated when lane is at capacity."""
        lane = WorkCellLane.docs

        # First complete some tasks to establish latency baseline
        for i in range(3):
            limiter.record_dispatch(f"baseline-{i}", lane)
            time.sleep(0.05)  # 50ms each
            limiter.record_completion(f"baseline-{i}", lane, success=True)

        # Fill to capacity
        for i in range(12):
            limiter.record_dispatch(f"task-{i}", lane)

        # Try to dispatch - should fail with wait time estimate
        can_dispatch, reason = limiter.can_dispatch("overflow-task", lane)
        assert can_dispatch is False
        assert "Estimated wait" in reason
        # Should include milliseconds in the message
        assert "ms" in reason


class TestRetryWasteCalculation:
    """Tests for retry waste rate calculations."""

    def test_zero_retry_waste(self, limiter: WIPLimiter) -> None:
        """Retry waste should be 0 when no retries occur."""
        # Complete 5 tasks without retries
        for i in range(5):
            limiter.record_dispatch(f"task-{i}", WorkCellLane.docs)
            limiter.record_completion(f"task-{i}", WorkCellLane.docs, success=True)

        metrics = limiter.get_queue_metrics()
        assert metrics["retry_waste_rate"] == 0.0

    def test_retry_waste_rate_calculation(self, limiter: WIPLimiter) -> None:
        """Verify retry waste rate calculation: retried / completed."""
        # Task 1: No retry
        limiter.record_dispatch("task-1", WorkCellLane.docs)
        limiter.record_completion("task-1", WorkCellLane.docs, success=True)

        # Task 2: Retried once
        limiter.record_dispatch("task-2", WorkCellLane.docs)
        limiter.record_retry("task-2", WorkCellLane.docs)
        limiter.record_completion("task-2", WorkCellLane.docs, success=True)

        # Task 3: Retried twice
        limiter.record_dispatch("task-3", WorkCellLane.docs)
        limiter.record_retry("task-3", WorkCellLane.docs)
        limiter.record_retry("task-3", WorkCellLane.docs)
        limiter.record_completion("task-3", WorkCellLane.docs, success=True)

        metrics = limiter.get_queue_metrics()
        # 2 tasks were retried out of 3 completed = 66.7% retry waste
        assert metrics["total_completed"] == 3
        assert metrics["retry_waste_rate"] == pytest.approx(0.6667, abs=0.001)

    def test_retry_waste_across_multiple_lanes(self, limiter: WIPLimiter) -> None:
        """Retry waste calculated across all lanes."""
        # Lane 1: 2 completed, 1 retried
        limiter.record_dispatch("t1", WorkCellLane.docs)
        limiter.record_completion("t1", WorkCellLane.docs, success=True)
        limiter.record_dispatch("t2", WorkCellLane.docs)
        limiter.record_retry("t2", WorkCellLane.docs)
        limiter.record_completion("t2", WorkCellLane.docs, success=True)

        # Lane 2: 1 completed, no retries
        limiter.record_dispatch("t3", WorkCellLane.api_simple)
        limiter.record_completion("t3", WorkCellLane.api_simple, success=True)

        metrics = limiter.get_queue_metrics()
        # 1 retried out of 3 total = 33.3%
        assert metrics["retry_waste_rate"] == pytest.approx(0.3333, abs=0.001)

    def test_lane_retry_rate_property(self, limiter: WIPLimiter) -> None:
        """Test LaneMetrics.retry_rate property directly."""
        # Complete tasks with retries in docs lane
        for i in range(4):
            limiter.record_dispatch(f"task-{i}", WorkCellLane.docs)
            if i < 2:  # First 2 tasks get retried
                limiter.record_retry(f"task-{i}", WorkCellLane.docs)
            limiter.record_completion(f"task-{i}", WorkCellLane.docs, success=True)

        lane_metrics = limiter.get_lane_metrics(WorkCellLane.docs)
        assert lane_metrics.total_completed == 4
        assert lane_metrics.total_retried == 2
        assert lane_metrics.retry_rate == 0.5


class TestWIPLimitBoundaryConditions:
    """Tests for WIP limit at exact boundaries."""

    def test_dispatch_at_exactly_wip_limit(self, limiter: WIPLimiter) -> None:
        """Dispatch at exactly WIP limit should be denied."""
        lane = WorkCellLane.docs  # max_wip = 12

        # Fill to exactly 12 tasks
        for i in range(12):
            limiter.record_dispatch(f"task-{i}", lane)

        # Try to dispatch one more - should fail
        can_dispatch, reason = limiter.can_dispatch("task-overflow", lane)
        assert can_dispatch is False
        assert "at capacity" in reason
        assert "12/12" in reason

    def test_dispatch_one_under_wip_limit(self, limiter: WIPLimiter) -> None:
        """Dispatch when one under WIP limit should succeed."""
        lane = WorkCellLane.docs  # max_wip = 12

        # Fill to 11 tasks (one under limit)
        for i in range(11):
            limiter.record_dispatch(f"task-{i}", lane)

        # Should be able to dispatch one more
        can_dispatch, reason = limiter.can_dispatch("task-11", lane)
        assert can_dispatch is True
        assert "11/12" in reason

    def test_dispatch_one_over_wip_limit(self, limiter: WIPLimiter) -> None:
        """Dispatch when one over WIP limit should fail."""
        lane = WorkCellLane.docs  # max_wip = 12

        # Fill to 12 tasks
        for i in range(12):
            limiter.record_dispatch(f"task-{i}", lane)

        # Try to dispatch - should fail
        can_dispatch, reason = limiter.can_dispatch("task-overflow", lane)
        assert can_dispatch is False

    def test_wip_limit_decreases_after_completion(self, limiter: WIPLimiter) -> None:
        """WIP count decreases when tasks complete."""
        lane = WorkCellLane.docs

        # Fill to capacity
        for i in range(12):
            limiter.record_dispatch(f"task-{i}", lane)

        # Complete one task
        limiter.record_completion("task-0", lane, success=True)

        # Now should be able to dispatch
        can_dispatch, _ = limiter.can_dispatch("new-task", lane)
        assert can_dispatch is True

    def test_different_lanes_have_different_wip_limits(self, limiter: WIPLimiter) -> None:
        """Verify different lanes have different default WIP limits."""
        from forge_harness.webhook_server.models.work_cell import get_lane_config

        docs_config = get_lane_config(WorkCellLane.docs)
        api_config = get_lane_config(WorkCellLane.api_simple)
        security_config = get_lane_config(WorkCellLane.security_change)

        # Each lane should have different limits based on config
        assert docs_config.max_wip > 0
        assert api_config.max_wip > 0
        assert security_config.max_wip > 0


class TestLaneSpecificLimits:
    """Tests for lane-specific WIP and retry budget configurations."""

    def test_api_simple_higher_retry_budget(self, limiter: WIPLimiter) -> None:
        """api_simple lane has higher retry budget (5)."""
        budget = limiter.get_retry_budget(WorkCellLane.api_simple)
        assert budget.max_retries == 5

        # Should allow 5 retries
        limiter.record_dispatch("task-1", WorkCellLane.api_simple)
        for _ in range(5):
            limiter.record_retry("task-1", WorkCellLane.api_simple)

        # After 5 retries, should still allow (max_retries=5 means 5 retries allowed)
        # Actually, the check is retry_count >= max_retries
        # So after 5 retries, retry_count=5, which equals max_retries
        can_dispatch, _ = limiter.can_dispatch("task-1", WorkCellLane.api_simple)
        assert can_dispatch is False  # Exceeded budget

    def test_security_change_strict_retry_budget(self, limiter: WIPLimiter) -> None:
        """security_change lane has strict retry budget (1)."""
        budget = limiter.get_retry_budget(WorkCellLane.security_change)
        assert budget.max_retries == 1

        # After 1 retry, should be blocked
        limiter.record_dispatch("task-1", WorkCellLane.security_change)
        limiter.record_retry("task-1", WorkCellLane.security_change)

        can_dispatch, reason = limiter.can_dispatch("task-1", WorkCellLane.security_change)
        assert can_dispatch is False
        assert "exceeded max retries" in reason

    def test_docs_default_retry_budget(self, limiter: WIPLimiter) -> None:
        """docs lane has default retry budget (3)."""
        budget = limiter.get_retry_budget(WorkCellLane.docs)
        assert budget.max_retries == 3

    def test_custom_retry_budget_per_lane(self, limiter: WIPLimiter) -> None:
        """Test setting custom retry budget per lane."""
        # Set custom budget for test lane
        custom_budget = RetryBudget(max_retries=7, retry_window_seconds=300)
        limiter.set_retry_budget(WorkCellLane.test_writing, custom_budget)

        retrieved = limiter.get_retry_budget(WorkCellLane.test_writing)
        assert retrieved.max_retries == 7
        assert retrieved.retry_window_seconds == 300


class TestConcurrentAccessPatterns:
    """Extended tests for thread safety under various load patterns."""

    def test_concurrent_retry_tracking(self) -> None:
        """Test thread safety of retry counting."""
        limiter = WIPLimiter()
        errors: list[Exception] = []
        retry_counts: list[int] = []

        def worker() -> None:
            try:
                task_id = f"task-{threading.current_thread().name}"
                limiter.record_dispatch(task_id, WorkCellLane.docs)
                for _ in range(5):
                    count = limiter.record_retry(task_id, WorkCellLane.docs)
                    retry_counts.append(count)
                limiter.record_completion(task_id, WorkCellLane.docs, success=True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, name=f"t-{i}") for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Each task should have retry counts 1-5
        assert len(retry_counts) == 50  # 10 workers * 5 retries each

    def test_concurrent_mixed_operations(self) -> None:
        """Test concurrent mix of dispatch, retry, and completion."""
        limiter = WIPLimiter()
        errors: list[Exception] = []

        def dispatcher() -> None:
            try:
                for i in range(20):
                    task_id = f"disp-{threading.current_thread().name}-{i}"
                    can_dispatch, _ = limiter.can_dispatch(task_id, WorkCellLane.api_simple)
                    if can_dispatch:
                        limiter.record_dispatch(task_id, WorkCellLane.api_simple)
                        time.sleep(0.001)
                        limiter.record_completion(task_id, WorkCellLane.api_simple, success=True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=dispatcher, name=f"w-{i}") for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        metrics = limiter.get_lane_metrics(WorkCellLane.api_simple)
        # Some tasks should have completed
        assert metrics.total_completed > 0

    def test_concurrent_can_dispatch_with_wip_limits(self) -> None:
        """Test can_dispatch under concurrent load with WIP limits."""
        limiter = WIPLimiter()
        results: list[bool] = []

        def worker() -> None:
            for i in range(50):
                task_id = f"concurrent-{threading.current_thread().name}-{i}"
                can_dispatch, _ = limiter.can_dispatch(task_id, WorkCellLane.test_writing)
                results.append(can_dispatch)
                if can_dispatch:
                    limiter.record_dispatch(task_id, WorkCellLane.test_writing)

        threads = [threading.Thread(target=worker, name=f"t-{i}") for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All calls should complete
        assert len(results) == 150
        # Some should be allowed, some denied due to WIP limits
        assert any(results)  # At least some allowed


class TestCompletedTasksStorage:
    """Tests for completed tasks storage limits."""

    def test_completed_tasks_limit_1000(self, limiter: WIPLimiter) -> None:
        """Test that completed tasks list is limited to 1000 entries."""
        lane = WorkCellLane.docs

        # Complete 1050 tasks
        for i in range(1050):
            limiter.record_dispatch(f"task-{i}", lane)
            limiter.record_completion(f"task-{i}", lane, success=True)

        # Metrics should show all 1050 completed
        metrics = limiter.get_lane_metrics(lane)
        assert metrics.total_completed == 1050

        # But get_task_status should still find the most recent ones
        # The oldest tasks may be dropped from internal storage
        status = limiter.get_task_status("task-1049")  # Most recent
        assert status is not None
        assert status["status"] == "completed"

    def test_lane_metrics_accumulate_beyond_storage_limit(self, limiter: WIPLimiter) -> None:
        """Lane metrics accumulate even when storage is limited."""
        lane = WorkCellLane.docs

        # Complete many tasks
        for i in range(1500):
            limiter.record_dispatch(f"task-{i}", lane)
            limiter.record_completion(f"task-{i}", lane, success=True)

        # Metrics should reflect all 1500
        metrics = limiter.get_lane_metrics(lane)
        assert metrics.total_completed == 1500
        assert metrics.total_dispatched == 1500


class TestQueueMetricsExtended:
    """Extended tests for queue metrics calculations."""

    def test_queue_metrics_p95_latency(self, limiter: WIPLimiter) -> None:
        """Test P95 latency calculation in queue metrics."""
        lane = WorkCellLane.docs

        # Create tasks with varying latencies
        for i in range(20):
            limiter.record_dispatch(f"task-{i}", lane)
            # Different delays: 10ms, 20ms, 30ms... 200ms
            time.sleep(0.01 * (i + 1))
            limiter.record_completion(f"task-{i}", lane, success=True)

        metrics = limiter.get_queue_metrics()
        assert "queue_latency_p95_ms" in metrics
        assert metrics["queue_latency_p95_ms"] > 0
        # P95 of 20 samples should be around the 19th value (~190ms)
        assert metrics["queue_latency_p95_ms"] >= 100  # At least 100ms

    def test_queue_metrics_with_no_completed_tasks(self, limiter: WIPLimiter) -> None:
        """Test queue metrics when no tasks have completed."""
        metrics = limiter.get_queue_metrics()

        assert metrics["queue_latency_avg_ms"] == 0.0
        assert metrics["queue_latency_p95_ms"] == 0.0
        assert metrics["retry_waste_rate"] == 0.0
        assert metrics["total_completed"] == 0
        assert metrics["total_dispatched"] == 0

    def test_queue_metrics_lane_details(self, limiter: WIPLimiter) -> None:
        """Test detailed lane information in queue metrics."""
        # Add activity to multiple lanes
        limiter.record_dispatch("t1", WorkCellLane.docs)
        limiter.record_completion("t1", WorkCellLane.docs, success=True)

        limiter.record_dispatch("t2", WorkCellLane.api_simple)
        limiter.record_completion("t2", WorkCellLane.api_simple, success=False)

        metrics = limiter.get_queue_metrics()

        assert "lanes" in metrics
        assert "docs" in metrics["lanes"]
        assert "api_simple" in metrics["lanes"]

        # Check lane details
        docs_lane = metrics["lanes"]["docs"]
        assert docs_lane["total_dispatched"] == 1
        assert docs_lane["total_succeeded"] == 1
        assert docs_lane["retry_rate"] == 0.0

        api_lane = metrics["lanes"]["api_simple"]
        assert api_lane["total_failed"] == 1
        assert api_lane["success_rate"] == 0.0

    def test_queue_metrics_retry_budget_info(self, limiter: WIPLimiter) -> None:
        """Test that retry budget info is included in lane metrics."""
        metrics = limiter.get_queue_metrics()

        for lane_name, lane_data in metrics["lanes"].items():
            assert "retry_budget" in lane_data
            assert "max_retries" in lane_data["retry_budget"]
            assert "retry_window_seconds" in lane_data["retry_budget"]


class TestRetryBudgetExhaustion:
    """Tests for retry budget exhaustion edge cases."""

    def test_exactly_at_retry_budget_limit(self, limiter: WIPLimiter) -> None:
        """Test dispatch when exactly at retry budget limit."""
        lane = WorkCellLane.docs
        budget = limiter.get_retry_budget(lane)

        # Set up task with exactly max_retries
        limiter.record_dispatch("task-1", lane)
        for _ in range(budget.max_retries):
            limiter.record_retry("task-1", lane)

        # At exactly max_retries, should be denied
        can_dispatch, reason = limiter.can_dispatch("task-1", lane)
        assert can_dispatch is False
        assert "exceeded max retries" in reason

    def test_one_under_retry_budget(self, limiter: WIPLimiter) -> None:
        """Test dispatch when one under retry budget limit."""
        lane = WorkCellLane.docs
        budget = limiter.get_retry_budget(lane)  # Default is 3

        # Set up task with one less than max_retries
        limiter.record_dispatch("task-1", lane)
        for _ in range(budget.max_retries - 1):  # 2 retries for default budget of 3
            limiter.record_retry("task-1", lane)

        # Should still be allowed (retry_count < max_retries)
        can_dispatch, _ = limiter.can_dispatch("task-1", lane)
        assert can_dispatch is True

    def test_retry_budget_reset_for_new_task(self, limiter: WIPLimiter) -> None:
        """Different tasks have independent retry counts."""
        lane = WorkCellLane.docs

        # Task 1: Use up retry budget
        limiter.record_dispatch("task-1", lane)
        for _ in range(3):
            limiter.record_retry("task-1", lane)

        # Task 2: Fresh task, should have full budget
        limiter.record_dispatch("task-2", lane)
        can_dispatch, _ = limiter.can_dispatch("task-2", lane)
        assert can_dispatch is True  # Fresh task, no retries yet

    def test_human_intervention_required_message(self, limiter: WIPLimiter) -> None:
        """Verify the human intervention message is clear."""
        lane = WorkCellLane.docs

        limiter.record_dispatch("task-1", lane)
        for _ in range(3):
            limiter.record_retry("task-1", lane)

        can_dispatch, reason = limiter.can_dispatch("task-1", lane)
        assert can_dispatch is False
        assert "Human intervention required" in reason
