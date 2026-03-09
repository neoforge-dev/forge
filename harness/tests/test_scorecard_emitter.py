"""
Tests for forge_harness/webhook_server/services/scorecard_emitter.py
=====================================================================

Coverage targets (90%+):
- compute_and_emit: nominal hourly/daily paths
- compute_and_emit: empty task list
- compute_and_emit: task source failure propagation
- compute_and_emit: invalid period argument
- EventBus.publish integration (mocked)
- schedule_hourly / schedule_daily convenience wrappers (_run_schedule)
- _safe_emit: swallows errors so the scheduler stays alive
- Singleton factory: get_scorecard_emitter, reset_scorecard_emitter
- Singleton factory: first-call without task_source raises ValueError
- Singleton factory: second call returns same instance
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.webhook_server.models.scorecard import (
    DarkFactoryScorecard,
    ScorecardEvent,
)
from forge_harness.webhook_server.services.scorecard_emitter import (
    _DEFAULT_NODE_ID,
    ScorecardEmitter,
    get_scorecard_emitter,
    reset_scorecard_emitter,
)

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2026, 2, 22, 0, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)


def _make_task(
    *,
    dispatch_acked: bool = True,
    evaluator_passed: bool = True,
    requeued: bool = False,
    human_touched: bool = False,
    escaped_defect: bool = False,
    completed: bool = True,
    cycle_time_minutes: float | None = 30.0,
) -> dict:
    """Minimal task dict compatible with compute_scorecard()."""
    return {
        "dispatch_acked": dispatch_acked,
        "evaluator_passed": evaluator_passed,
        "requeued": requeued,
        "human_touched": human_touched,
        "escaped_defect": escaped_defect,
        "completed": completed,
        "cycle_time_minutes": cycle_time_minutes,
    }


def _mock_event_bus() -> MagicMock:
    """Return a MagicMock EventBus with an async publish method."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _make_emitter(
    tasks: list[dict] | None = None,
    task_source_side_effect: Exception | None = None,
    node_id: str = "forge:test",
) -> tuple[ScorecardEmitter, MagicMock]:
    """Create an emitter with a mock event bus and controlled task source.

    Returns:
        (emitter, mock_bus)
    """
    bus = _mock_event_bus()

    if task_source_side_effect is not None:

        async def _failing_source() -> list[dict]:
            raise task_source_side_effect

        source = _failing_source
    else:
        _task_list = tasks if tasks is not None else []

        async def _source() -> list[dict]:
            return _task_list

        source = _source

    emitter = ScorecardEmitter(
        task_source=source,
        event_bus=bus,
        node_id=node_id,
    )
    return emitter, bus


# ---------------------------------------------------------------------------
# compute_and_emit — nominal paths
# ---------------------------------------------------------------------------


class TestComputeAndEmitHourly:
    """Tests for the hourly period path."""

    @pytest.mark.asyncio
    async def test_returns_scorecard_event(self):
        tasks = [_make_task() for _ in range(3)]
        emitter, _ = _make_emitter(tasks=tasks)

        result = await emitter.compute_and_emit(period="hourly")

        assert isinstance(result, ScorecardEvent)
        assert result.event_type == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_scorecard_fields_populated(self):
        tasks = [
            _make_task(dispatch_acked=True, evaluator_passed=True, completed=True),
            _make_task(
                dispatch_acked=True, evaluator_passed=False, human_touched=True, completed=False
            ),
        ]
        emitter, _ = _make_emitter(tasks=tasks, node_id="forge:nova")

        result = await emitter.compute_and_emit(period="hourly")

        sc = result.scorecard
        assert sc.total_tasks == 2
        assert sc.node_id == "forge:nova"
        assert 0.0 <= sc.dispatch_ack_rate <= 1.0
        assert 0.0 <= sc.evaluator_pass_rate <= 1.0

    @pytest.mark.asyncio
    async def test_event_bus_publish_called_once(self):
        tasks = [_make_task()]
        emitter, bus = _make_emitter(tasks=tasks)

        await emitter.compute_and_emit(period="hourly")

        bus.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_event_bus_publish_correct_event_type(self):
        tasks = [_make_task()]
        emitter, bus = _make_emitter(tasks=tasks)

        await emitter.compute_and_emit(period="hourly")

        call_kwargs = bus.publish.await_args.kwargs
        assert call_kwargs["event_type"] == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_event_bus_publish_source_identifier(self):
        tasks = [_make_task()]
        emitter, bus = _make_emitter(tasks=tasks)

        await emitter.compute_and_emit(period="hourly")

        call_kwargs = bus.publish.await_args.kwargs
        assert call_kwargs["source"] == "scorecard-emitter"

    @pytest.mark.asyncio
    async def test_event_bus_data_contains_scorecard(self):
        tasks = [_make_task(cycle_time_minutes=45.0)]
        emitter, bus = _make_emitter(tasks=tasks)

        await emitter.compute_and_emit(period="hourly")

        data = bus.publish.await_args.kwargs["data"]
        assert "scorecard" in data
        assert data["scorecard"]["total_tasks"] == 1
        assert data["scorecard"]["mean_cycle_time_minutes"] == pytest.approx(45.0)


class TestComputeAndEmitDaily:
    """Tests for the daily period path."""

    @pytest.mark.asyncio
    async def test_returns_daily_event_type(self):
        emitter, _ = _make_emitter(tasks=[_make_task()])

        result = await emitter.compute_and_emit(period="daily")

        assert result.event_type == "scorecard_daily"

    @pytest.mark.asyncio
    async def test_period_window_is_24_hours(self):
        """The scorecard's period_end - period_start must be ~24 h."""
        emitter, _ = _make_emitter(tasks=[_make_task()])

        result = await emitter.compute_and_emit(period="daily")

        sc = result.scorecard
        delta = sc.period_end - sc.period_start
        # Allow 1 second of clock drift in CI
        assert abs(delta.total_seconds() - 86400) < 1

    @pytest.mark.asyncio
    async def test_event_bus_publish_daily_event_type(self):
        emitter, bus = _make_emitter(tasks=[_make_task()])

        await emitter.compute_and_emit(period="daily")

        call_kwargs = bus.publish.await_args.kwargs
        assert call_kwargs["event_type"] == "scorecard_daily"


# ---------------------------------------------------------------------------
# compute_and_emit — empty task list
# ---------------------------------------------------------------------------


class TestComputeAndEmitEmptyTasks:
    @pytest.mark.asyncio
    async def test_empty_tasks_returns_zero_scorecard(self):
        emitter, _ = _make_emitter(tasks=[])

        result = await emitter.compute_and_emit(period="hourly")

        sc = result.scorecard
        assert sc.total_tasks == 0
        assert sc.autonomous_completions == 0
        assert sc.dispatch_ack_rate == 0.0
        assert sc.evaluator_pass_rate == 0.0
        assert sc.mean_cycle_time_minutes == 0.0

    @pytest.mark.asyncio
    async def test_empty_tasks_still_publishes(self):
        emitter, bus = _make_emitter(tasks=[])

        await emitter.compute_and_emit(period="hourly")

        bus.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_tasks_event_type_preserved(self):
        emitter, bus = _make_emitter(tasks=[])

        await emitter.compute_and_emit(period="daily")

        call_kwargs = bus.publish.await_args.kwargs
        assert call_kwargs["event_type"] == "scorecard_daily"


# ---------------------------------------------------------------------------
# compute_and_emit — error handling
# ---------------------------------------------------------------------------


class TestComputeAndEmitErrors:
    @pytest.mark.asyncio
    async def test_invalid_period_raises_value_error(self):
        emitter, _ = _make_emitter(tasks=[])

        with pytest.raises(ValueError, match="Unknown period"):
            await emitter.compute_and_emit(period="weekly")

    @pytest.mark.asyncio
    async def test_task_source_failure_propagates(self):
        emitter, bus = _make_emitter(task_source_side_effect=RuntimeError("DB connection lost"))

        with pytest.raises(RuntimeError, match="DB connection lost"):
            await emitter.compute_and_emit(period="hourly")

    @pytest.mark.asyncio
    async def test_task_source_failure_does_not_publish(self):
        emitter, bus = _make_emitter(task_source_side_effect=RuntimeError("DB connection lost"))

        with pytest.raises(RuntimeError):
            await emitter.compute_and_emit(period="hourly")

        bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_period_does_not_publish(self):
        emitter, bus = _make_emitter(tasks=[_make_task()])

        with pytest.raises(ValueError):
            await emitter.compute_and_emit(period="bad_period")

        bus.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# _safe_emit — swallows errors for the scheduler
# ---------------------------------------------------------------------------


class TestSafeEmit:
    @pytest.mark.asyncio
    async def test_safe_emit_does_not_raise_on_source_error(self):
        """_safe_emit must not propagate so the scheduler loop survives."""
        emitter, _ = _make_emitter(task_source_side_effect=OSError("disk full"))

        # Should complete without raising
        await emitter._safe_emit("hourly")

    @pytest.mark.asyncio
    async def test_safe_emit_success_calls_publish(self):
        emitter, bus = _make_emitter(tasks=[_make_task()])

        await emitter._safe_emit("hourly")

        bus.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_safe_emit_swallows_invalid_period(self):
        emitter, bus = _make_emitter(tasks=[])

        # ValueError from bad period — must be swallowed
        await emitter._safe_emit("not_a_real_period")

        bus.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# schedule_hourly / schedule_daily
# ---------------------------------------------------------------------------


class TestScheduleMethods:
    """The schedule_* coroutines loop forever; we cancel them promptly."""

    @pytest.mark.asyncio
    async def test_schedule_hourly_emits_immediately(self):
        emitter, bus = _make_emitter(tasks=[_make_task()])

        task = asyncio.create_task(
            emitter.schedule_hourly(interval_seconds=9999, run_immediately=True)
        )
        # Allow one iteration
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Must have published at least once (the run_immediately emission)
        bus.publish.assert_awaited()

    @pytest.mark.asyncio
    async def test_schedule_hourly_skip_immediate_does_not_emit_before_sleep(self):
        emitter, bus = _make_emitter(tasks=[_make_task()])

        task = asyncio.create_task(
            emitter.schedule_hourly(interval_seconds=9999, run_immediately=False)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_schedule_daily_emits_immediately(self):
        emitter, bus = _make_emitter(tasks=[_make_task()])

        task = asyncio.create_task(
            emitter.schedule_daily(interval_seconds=9999, run_immediately=True)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        bus.publish.assert_awaited()

    @pytest.mark.asyncio
    async def test_schedule_daily_event_type_is_daily(self):
        emitter, bus = _make_emitter(tasks=[_make_task()])

        task = asyncio.create_task(
            emitter.schedule_daily(interval_seconds=9999, run_immediately=True)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        call_kwargs = bus.publish.await_args.kwargs
        assert call_kwargs["event_type"] == "scorecard_daily"

    @pytest.mark.asyncio
    async def test_schedule_hourly_event_type_is_hourly(self):
        emitter, bus = _make_emitter(tasks=[_make_task()])

        task = asyncio.create_task(
            emitter.schedule_hourly(interval_seconds=9999, run_immediately=True)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        call_kwargs = bus.publish.await_args.kwargs
        assert call_kwargs["event_type"] == "scorecard_hourly"


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


class TestSingletonFactory:
    def setup_method(self):
        """Ensure a clean singleton before every test."""
        reset_scorecard_emitter()

    def teardown_method(self):
        """Leave a clean singleton after every test."""
        reset_scorecard_emitter()

    def test_first_call_without_task_source_raises(self):
        with pytest.raises(ValueError, match="task_source is required"):
            get_scorecard_emitter()

    def test_first_call_with_task_source_returns_emitter(self):
        async def source() -> list[dict]:
            return []

        emitter = get_scorecard_emitter(task_source=source)
        assert isinstance(emitter, ScorecardEmitter)

    def test_second_call_returns_same_instance(self):
        async def source() -> list[dict]:
            return []

        first = get_scorecard_emitter(task_source=source)
        second = get_scorecard_emitter()  # no task_source required
        assert first is second

    def test_second_call_ignores_new_task_source(self):
        async def source_a() -> list[dict]:
            return []

        async def source_b() -> list[dict]:
            return [_make_task()]

        first = get_scorecard_emitter(task_source=source_a)
        second = get_scorecard_emitter(task_source=source_b)

        # Same instance — source_b is ignored
        assert first is second
        assert first._task_source is source_a

    def test_reset_allows_new_singleton(self):
        async def source_a() -> list[dict]:
            return []

        async def source_b() -> list[dict]:
            return [_make_task()]

        first = get_scorecard_emitter(task_source=source_a)
        reset_scorecard_emitter()
        second = get_scorecard_emitter(task_source=source_b)

        assert first is not second
        assert second._task_source is source_b

    def test_custom_node_id_applied(self):
        async def source() -> list[dict]:
            return []

        emitter = get_scorecard_emitter(task_source=source, node_id="forge:custom-node")
        assert emitter._node_id == "forge:custom-node"

    def test_default_node_id(self):
        async def source() -> list[dict]:
            return []

        emitter = get_scorecard_emitter(task_source=source)
        assert emitter._node_id == _DEFAULT_NODE_ID

    def test_custom_event_bus_applied(self):
        async def source() -> list[dict]:
            return []

        custom_bus = _mock_event_bus()
        emitter = get_scorecard_emitter(task_source=source, event_bus=custom_bus)
        assert emitter._event_bus is custom_bus


# ---------------------------------------------------------------------------
# Services __init__ export check
# ---------------------------------------------------------------------------


class TestServicesInit:
    def test_scorecard_emitter_exported(self):
        from forge_harness.webhook_server.services import ScorecardEmitter as ScorecardEmitterAlias

        assert ScorecardEmitterAlias is ScorecardEmitter

    def test_get_scorecard_emitter_exported(self):
        from forge_harness.webhook_server.services import get_scorecard_emitter as gse

        assert gse is get_scorecard_emitter

    def test_reset_scorecard_emitter_exported(self):
        from forge_harness.webhook_server.services import reset_scorecard_emitter as rse

        assert rse is reset_scorecard_emitter


# ---------------------------------------------------------------------------
# Metric accuracy spot checks
# ---------------------------------------------------------------------------


class TestMetricAccuracy:
    @pytest.mark.asyncio
    async def test_dispatch_ack_rate_is_correct(self):
        """3 out of 4 tasks acked → dispatch_ack_rate = 0.75."""
        tasks = [
            _make_task(dispatch_acked=True),
            _make_task(dispatch_acked=True),
            _make_task(dispatch_acked=True),
            _make_task(dispatch_acked=False),
        ]
        emitter, _ = _make_emitter(tasks=tasks)

        result = await emitter.compute_and_emit(period="hourly")

        assert result.scorecard.dispatch_ack_rate == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_human_touch_ratio_is_correct(self):
        """1 out of 5 tasks human-touched → human_touch_ratio = 0.2."""
        tasks = [_make_task(human_touched=False)] * 4 + [_make_task(human_touched=True)]
        emitter, _ = _make_emitter(tasks=tasks)

        result = await emitter.compute_and_emit(period="hourly")

        assert result.scorecard.human_touch_ratio == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_autonomous_completions_excludes_human_touched(self):
        """Completed + human_touched tasks do NOT count as autonomous."""
        tasks = [
            _make_task(completed=True, human_touched=False),
            _make_task(completed=True, human_touched=False),
            _make_task(completed=True, human_touched=True),  # not autonomous
        ]
        emitter, _ = _make_emitter(tasks=tasks)

        result = await emitter.compute_and_emit(period="hourly")

        assert result.scorecard.autonomous_completions == 2

    @pytest.mark.asyncio
    async def test_mean_cycle_time_only_completed(self):
        """Only completed tasks contribute to mean_cycle_time_minutes."""
        tasks = [
            _make_task(completed=True, cycle_time_minutes=10.0),
            _make_task(completed=True, cycle_time_minutes=20.0),
            _make_task(completed=False, cycle_time_minutes=999.0),  # should not count
        ]
        emitter, _ = _make_emitter(tasks=tasks)

        result = await emitter.compute_and_emit(period="hourly")

        assert result.scorecard.mean_cycle_time_minutes == pytest.approx(15.0)

    @pytest.mark.asyncio
    async def test_escaped_defects_counted(self):
        tasks = [
            _make_task(escaped_defect=True),
            _make_task(escaped_defect=True),
            _make_task(escaped_defect=False),
        ]
        emitter, _ = _make_emitter(tasks=tasks)

        result = await emitter.compute_and_emit(period="hourly")

        assert result.scorecard.escaped_defects == 2
