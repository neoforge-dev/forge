"""Unit tests for forge_harness.webhook_server.services.scorecard_emitter.

Covers every public method, branching path, and singleton lifecycle:

- ScorecardEmitter.__init__:
    - explicit event_bus vs. get_event_bus() fallback
    - custom / default node_id
    - task_source stored correctly
    - _running_tasks initialised empty

- compute_and_emit:
    - happy path: "hourly" and "daily"
    - correct ScorecardEvent returned (type, scorecard, event_type)
    - task_source awaited exactly once
    - compute_scorecard called with correct args (tasks, period_start, period_end, node_id)
    - event_bus.publish called with correct event_type, data (dict), source
    - period window durations (hourly=1h, daily=24h)
    - scorecard carries correct node_id
    - with non-empty tasks -> total_tasks and metrics populated
    - default period is "hourly"
    - invalid period raises ValueError immediately
    - invalid period: task_source NOT awaited
    - invalid period: publish NOT called
    - ValueError message lists both valid periods
    - task_source raises: exception propagates
    - task_source raises: publish NOT called
    - task_source raises: different exception types (RuntimeError, IOError, ValueError)

- _safe_emit:
    - delegates to compute_and_emit with the correct period
    - swallows RuntimeError
    - swallows IOError
    - swallows generic Exception
    - logs error on exception
    - error log includes period string
    - error log includes exception string
    - does NOT log error on success

- schedule_hourly:
    - delegates to _run_schedule("hourly", ...)
    - correct default interval (3600.0)
    - correct default run_immediately (True)
    - forwards custom interval_seconds
    - forwards run_immediately=False

- schedule_daily:
    - delegates to _run_schedule("daily", ...)
    - correct default interval (86400.0)
    - correct default run_immediately (True)
    - forwards custom interval_seconds
    - forwards run_immediately=False

- _run_schedule:
    - run_immediately=True → emit before sleep
    - run_immediately=False → sleep before emit
    - asyncio.sleep called with interval_seconds
    - period forwarded to every _safe_emit call
    - loop continues (multiple sleep+emit iterations)
    - CancelledError propagates out of the infinite loop

- get_scorecard_emitter (singleton factory):
    - first call without task_source raises ValueError
    - first call with task_source returns ScorecardEmitter instance
    - second call returns same cached instance
    - second call ignores new task_source arg
    - node_id from first call is retained
    - event_bus from first call is retained

- reset_scorecard_emitter:
    - clears singleton so next call creates fresh instance
    - allows new node_id after reset
    - safe to call when no singleton exists (double-reset safe)
    - get_scorecard_emitter without task_source after reset raises ValueError

All external dependencies (EventBus, compute_scorecard, asyncio.sleep, logger)
are mocked. No real I/O, network, or file access occurs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Singleton isolation — reset before AND after every test
# ---------------------------------------------------------------------------


def _reset_emitter_singleton() -> None:
    import forge_harness.webhook_server.services.scorecard_emitter as _mod

    _mod._emitter_instance = None


@pytest.fixture(autouse=True)
def isolated_singleton():
    """Ensure a clean singleton state around every test in this file."""
    _reset_emitter_singleton()
    yield
    _reset_emitter_singleton()


# ---------------------------------------------------------------------------
# Imports under test (after reset guard is in place)
# ---------------------------------------------------------------------------

from forge_harness.webhook_server.models.scorecard import (  # noqa: E402
    DarkFactoryScorecard,
    ScorecardEvent,
)
from forge_harness.webhook_server.services.scorecard_emitter import (  # noqa: E402
    _DEFAULT_NODE_ID,
    _EVENT_TYPE_MAP,
    _PERIOD_DURATION,
    ScorecardEmitter,
    get_scorecard_emitter,
    reset_scorecard_emitter,
)

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _make_task_source(tasks: list | None = None) -> AsyncMock:
    """Async callable that resolves to *tasks* (default: empty list)."""
    return AsyncMock(return_value=tasks if tasks is not None else [])


def _make_event_bus() -> MagicMock:
    """Mock EventBus with an AsyncMock publish method."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _make_emitter(
    tasks: list | None = None,
    node_id: str = "forge:test",
    event_bus: MagicMock | None = None,
) -> tuple[ScorecardEmitter, AsyncMock, MagicMock]:
    """Return (emitter, task_source, event_bus) with sensible defaults."""
    source = _make_task_source(tasks)
    bus = event_bus if event_bus is not None else _make_event_bus()
    emitter = ScorecardEmitter(task_source=source, event_bus=bus, node_id=node_id)
    return emitter, source, bus


def _fake_scorecard(
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    node_id: str = "forge:test",
) -> DarkFactoryScorecard:
    now = datetime.now(UTC)
    return DarkFactoryScorecard(
        period_start=period_start or (now - timedelta(hours=1)),
        period_end=period_end or now,
        node_id=node_id,
    )


# ===========================================================================
# 1. ScorecardEmitter.__init__
# ===========================================================================


class TestScorecardEmitterInit:
    """Construction behaviour of ScorecardEmitter."""

    def test_explicit_event_bus_stored(self):
        bus = _make_event_bus()
        source = _make_task_source()
        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.get_event_bus"
        ) as mock_geb:
            emitter = ScorecardEmitter(task_source=source, event_bus=bus)
        mock_geb.assert_not_called()
        assert emitter._event_bus is bus

    def test_none_event_bus_calls_get_event_bus(self):
        bus = _make_event_bus()
        source = _make_task_source()
        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.get_event_bus",
            return_value=bus,
        ) as mock_geb:
            emitter = ScorecardEmitter(task_source=source)
        mock_geb.assert_called_once()
        assert emitter._event_bus is bus

    def test_default_node_id_is_forge_unknown(self):
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        assert emitter._node_id == _DEFAULT_NODE_ID
        assert emitter._node_id == "forge:unknown"

    def test_custom_node_id_stored(self):
        bus = _make_event_bus()
        emitter = ScorecardEmitter(
            task_source=_make_task_source(), event_bus=bus, node_id="forge:nova"
        )
        assert emitter._node_id == "forge:nova"

    def test_task_source_stored(self):
        bus = _make_event_bus()
        source = _make_task_source()
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)
        assert emitter._task_source is source

    def test_running_tasks_initialised_as_empty_list(self):
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        assert emitter._running_tasks == []
        assert isinstance(emitter._running_tasks, list)

    def test_init_logs_info(self):
        bus = _make_event_bus()
        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            ScorecardEmitter(
                task_source=_make_task_source(), event_bus=bus, node_id="forge:x"
            )
        mock_logger.info.assert_called()


# ===========================================================================
# 2. compute_and_emit — happy paths
# ===========================================================================


class TestComputeAndEmitHappyPath:
    """compute_and_emit successful execution for both periods."""

    @pytest.mark.asyncio
    async def test_returns_scorecard_event_hourly(self):
        emitter, _, _ = _make_emitter()
        result = await emitter.compute_and_emit(period="hourly")
        assert isinstance(result, ScorecardEvent)

    @pytest.mark.asyncio
    async def test_returns_scorecard_event_daily(self):
        emitter, _, _ = _make_emitter()
        result = await emitter.compute_and_emit(period="daily")
        assert isinstance(result, ScorecardEvent)

    @pytest.mark.asyncio
    async def test_hourly_event_type_field(self):
        emitter, _, _ = _make_emitter()
        event = await emitter.compute_and_emit(period="hourly")
        assert event.event_type == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_daily_event_type_field(self):
        emitter, _, _ = _make_emitter()
        event = await emitter.compute_and_emit(period="daily")
        assert event.event_type == "scorecard_daily"

    @pytest.mark.asyncio
    async def test_event_type_map_consistency_hourly(self):
        assert _EVENT_TYPE_MAP["hourly"] == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_event_type_map_consistency_daily(self):
        assert _EVENT_TYPE_MAP["daily"] == "scorecard_daily"

    @pytest.mark.asyncio
    async def test_task_source_awaited_once(self):
        emitter, source, _ = _make_emitter()
        await emitter.compute_and_emit(period="hourly")
        source.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_called_once(self):
        emitter, _, bus = _make_emitter()
        await emitter.compute_and_emit(period="hourly")
        bus.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_event_type_hourly(self):
        emitter, _, bus = _make_emitter()
        await emitter.compute_and_emit(period="hourly")
        call_kwargs = bus.publish.call_args
        et = call_kwargs.kwargs.get("event_type") or (
            call_kwargs.args[0] if call_kwargs.args else None
        )
        assert et == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_publish_event_type_daily(self):
        emitter, _, bus = _make_emitter()
        await emitter.compute_and_emit(period="daily")
        call_kwargs = bus.publish.call_args
        et = call_kwargs.kwargs.get("event_type") or (
            call_kwargs.args[0] if call_kwargs.args else None
        )
        assert et == "scorecard_daily"

    @pytest.mark.asyncio
    async def test_publish_source_is_scorecard_emitter(self):
        emitter, _, bus = _make_emitter()
        await emitter.compute_and_emit(period="hourly")
        call_kwargs = bus.publish.call_args
        assert call_kwargs.kwargs.get("source") == "scorecard-emitter"

    @pytest.mark.asyncio
    async def test_publish_data_is_dict(self):
        emitter, _, bus = _make_emitter()
        await emitter.compute_and_emit(period="hourly")
        call_kwargs = bus.publish.call_args
        assert isinstance(call_kwargs.kwargs.get("data"), dict)

    @pytest.mark.asyncio
    async def test_default_period_is_hourly(self):
        emitter, _, _ = _make_emitter()
        event = await emitter.compute_and_emit()
        assert event.event_type == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_scorecard_node_id_matches_emitter_node_id(self):
        emitter, _, _ = _make_emitter(node_id="forge:sentinel")
        event = await emitter.compute_and_emit(period="hourly")
        assert event.scorecard.node_id == "forge:sentinel"

    @pytest.mark.asyncio
    async def test_scorecard_total_tasks_zero_with_empty_source(self):
        emitter, _, _ = _make_emitter(tasks=[])
        event = await emitter.compute_and_emit(period="hourly")
        assert event.scorecard.total_tasks == 0

    @pytest.mark.asyncio
    async def test_scorecard_total_tasks_populated_with_tasks(self):
        tasks = [{"dispatch_acked": True}, {"dispatch_acked": False}]
        emitter, _, _ = _make_emitter(tasks=tasks)
        event = await emitter.compute_and_emit(period="hourly")
        assert event.scorecard.total_tasks == 2

    @pytest.mark.asyncio
    async def test_scorecard_dispatch_ack_rate_computed(self):
        tasks = [
            {"dispatch_acked": True},
            {"dispatch_acked": True},
            {"dispatch_acked": False},
            {"dispatch_acked": False},
        ]
        emitter, _, _ = _make_emitter(tasks=tasks)
        event = await emitter.compute_and_emit(period="hourly")
        assert event.scorecard.dispatch_ack_rate == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_scorecard_autonomous_completions_computed(self):
        tasks = [
            {"completed": True, "human_touched": False},
            {"completed": True, "human_touched": True},
            {"completed": False},
        ]
        emitter, _, _ = _make_emitter(tasks=tasks)
        event = await emitter.compute_and_emit(period="hourly")
        assert event.scorecard.autonomous_completions == 1

    @pytest.mark.asyncio
    async def test_hourly_period_window_is_one_hour(self):
        """period_end - period_start should be ~1 hour for hourly period."""
        emitter, _, _ = _make_emitter()
        event = await emitter.compute_and_emit(period="hourly")
        delta = event.scorecard.period_end - event.scorecard.period_start
        assert abs(delta.total_seconds() - 3600) < 5  # within 5 seconds

    @pytest.mark.asyncio
    async def test_daily_period_window_is_24_hours(self):
        """period_end - period_start should be ~24 hours for daily period."""
        emitter, _, _ = _make_emitter()
        event = await emitter.compute_and_emit(period="daily")
        delta = event.scorecard.period_end - event.scorecard.period_start
        assert abs(delta.total_seconds() - 86400) < 5  # within 5 seconds

    @pytest.mark.asyncio
    async def test_compute_scorecard_called_with_tasks(self):
        tasks = [{"dispatch_acked": True, "completed": True}]
        emitter, _, _ = _make_emitter(tasks=tasks, node_id="forge:alpha")

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.compute_scorecard",
            wraps=__import__(
                "forge_harness.webhook_server.models.scorecard",
                fromlist=["compute_scorecard"],
            ).compute_scorecard,
        ) as mock_cs:
            await emitter.compute_and_emit(period="hourly")

        mock_cs.assert_called_once()
        bound = mock_cs.call_args
        # tasks arg can be positional or keyword
        call_tasks = bound.kwargs.get("tasks") or (bound.args[0] if bound.args else None)
        assert call_tasks == tasks

    @pytest.mark.asyncio
    async def test_compute_scorecard_called_with_node_id(self):
        emitter, _, _ = _make_emitter(node_id="forge:beta")

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.compute_scorecard",
            wraps=__import__(
                "forge_harness.webhook_server.models.scorecard",
                fromlist=["compute_scorecard"],
            ).compute_scorecard,
        ) as mock_cs:
            await emitter.compute_and_emit(period="daily")

        bound = mock_cs.call_args
        call_node_id = bound.kwargs.get("node_id") or (
            bound.args[3] if len(bound.args) > 3 else None
        )
        assert call_node_id == "forge:beta"

    @pytest.mark.asyncio
    async def test_scorecard_period_start_is_utc_aware(self):
        emitter, _, _ = _make_emitter()
        event = await emitter.compute_and_emit(period="hourly")
        assert event.scorecard.period_start.tzinfo is not None

    @pytest.mark.asyncio
    async def test_scorecard_period_end_is_utc_aware(self):
        emitter, _, _ = _make_emitter()
        event = await emitter.compute_and_emit(period="hourly")
        assert event.scorecard.period_end.tzinfo is not None

    @pytest.mark.asyncio
    async def test_scorecard_period_start_before_period_end(self):
        emitter, _, _ = _make_emitter()
        event = await emitter.compute_and_emit(period="hourly")
        assert event.scorecard.period_start < event.scorecard.period_end

    @pytest.mark.asyncio
    async def test_publish_data_contains_event_type_key(self):
        """The dict passed to publish should contain 'event_type' from ScorecardEvent."""
        emitter, _, bus = _make_emitter()
        await emitter.compute_and_emit(period="hourly")
        data = bus.publish.call_args.kwargs["data"]
        assert "event_type" in data

    @pytest.mark.asyncio
    async def test_publish_data_contains_scorecard_key(self):
        emitter, _, bus = _make_emitter()
        await emitter.compute_and_emit(period="hourly")
        data = bus.publish.call_args.kwargs["data"]
        assert "scorecard" in data

    @pytest.mark.asyncio
    async def test_returned_event_generated_at_is_utc_aware(self):
        emitter, _, _ = _make_emitter()
        event = await emitter.compute_and_emit(period="hourly")
        assert event.generated_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_task_source_return_value_fully_delegated_to_compute(self):
        """All task dicts from the source reach compute_scorecard unchanged."""
        tasks = [
            {"evaluator_passed": True, "completed": True, "cycle_time_minutes": 5.0},
            {"evaluator_passed": False, "requeued": True},
        ]
        emitter, _, _ = _make_emitter(tasks=tasks)
        event = await emitter.compute_and_emit(period="hourly")
        # 1 evaluator passed out of 2 → 0.5
        assert event.scorecard.evaluator_pass_rate == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_full_autonomous_run_metrics(self):
        """All tasks dispatched, evaluated, completed autonomously."""
        tasks = [
            {
                "dispatch_acked": True,
                "evaluator_passed": True,
                "requeued": False,
                "human_touched": False,
                "escaped_defect": False,
                "completed": True,
                "cycle_time_minutes": 10.0,
            }
        ]
        emitter, _, _ = _make_emitter(tasks=tasks)
        event = await emitter.compute_and_emit(period="daily")
        sc = event.scorecard
        assert sc.dispatch_ack_rate == pytest.approx(1.0)
        assert sc.evaluator_pass_rate == pytest.approx(1.0)
        assert sc.requeue_rate == pytest.approx(0.0)
        assert sc.human_touch_ratio == pytest.approx(0.0)
        assert sc.autonomous_completions == 1
        assert sc.mean_cycle_time_minutes == pytest.approx(10.0)


# ===========================================================================
# 3. compute_and_emit — invalid period
# ===========================================================================


class TestComputeAndEmitInvalidPeriod:
    """Period validation guard at the top of compute_and_emit."""

    @pytest.mark.asyncio
    async def test_unknown_period_raises_value_error(self):
        emitter, _, _ = _make_emitter()
        with pytest.raises(ValueError, match="Unknown period"):
            await emitter.compute_and_emit(period="weekly")

    @pytest.mark.asyncio
    async def test_empty_string_period_raises_value_error(self):
        emitter, _, _ = _make_emitter()
        with pytest.raises(ValueError):
            await emitter.compute_and_emit(period="")

    @pytest.mark.asyncio
    async def test_invalid_period_task_source_not_called(self):
        emitter, source, _ = _make_emitter()
        with pytest.raises(ValueError):
            await emitter.compute_and_emit(period="minutely")
        source.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_period_publish_not_called(self):
        emitter, _, bus = _make_emitter()
        with pytest.raises(ValueError):
            await emitter.compute_and_emit(period="bad_period")
        bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_error_message_lists_hourly(self):
        emitter, _, _ = _make_emitter()
        with pytest.raises(ValueError) as exc_info:
            await emitter.compute_and_emit(period="nope")
        assert "hourly" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_error_message_lists_daily(self):
        emitter, _, _ = _make_emitter()
        with pytest.raises(ValueError) as exc_info:
            await emitter.compute_and_emit(period="nope")
        assert "daily" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_case_sensitive_period_validation(self):
        """Period validation is case-sensitive: 'Hourly' must fail."""
        emitter, _, _ = _make_emitter()
        with pytest.raises(ValueError):
            await emitter.compute_and_emit(period="Hourly")

    @pytest.mark.asyncio
    async def test_whitespace_period_raises_value_error(self):
        emitter, _, _ = _make_emitter()
        with pytest.raises(ValueError):
            await emitter.compute_and_emit(period=" hourly")


# ===========================================================================
# 4. compute_and_emit — task_source raises
# ===========================================================================


class TestComputeAndEmitTaskSourceRaises:
    """Exception propagation when task_source fails."""

    @pytest.mark.asyncio
    async def test_runtime_error_propagates(self):
        bus = _make_event_bus()
        source = AsyncMock(side_effect=RuntimeError("DB refused"))
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)
        with pytest.raises(RuntimeError, match="DB refused"):
            await emitter.compute_and_emit(period="hourly")

    @pytest.mark.asyncio
    async def test_io_error_propagates(self):
        bus = _make_event_bus()
        source = AsyncMock(side_effect=OSError("disk error"))
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)
        with pytest.raises(IOError):
            await emitter.compute_and_emit(period="daily")

    @pytest.mark.asyncio
    async def test_value_error_from_source_propagates_not_swallowed_by_period_guard(self):
        """ValueError from the *source* (not the period guard) must propagate."""
        bus = _make_event_bus()
        source = AsyncMock(side_effect=ValueError("malformed data"))
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)
        with pytest.raises(ValueError, match="malformed data"):
            await emitter.compute_and_emit(period="hourly")

    @pytest.mark.asyncio
    async def test_task_source_exception_prevents_publish(self):
        bus = _make_event_bus()
        source = AsyncMock(side_effect=RuntimeError("boom"))
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)
        with pytest.raises(RuntimeError):
            await emitter.compute_and_emit(period="hourly")
        bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_source_exception_is_logged(self):
        bus = _make_event_bus()
        source = AsyncMock(side_effect=RuntimeError("source failed"))
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)
        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            with pytest.raises(RuntimeError):
                await emitter.compute_and_emit(period="hourly")
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_exception_object_reference_preserved(self):
        """The exact exception instance propagates unchanged."""
        bus = _make_event_bus()
        original_exc = RuntimeError("unique-sentinel-12345")
        source = AsyncMock(side_effect=original_exc)
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)
        with pytest.raises(RuntimeError, match="unique-sentinel-12345"):
            await emitter.compute_and_emit(period="daily")


# ===========================================================================
# 5. _safe_emit — exception swallowing and logging
# ===========================================================================


class TestSafeEmit:
    """_safe_emit absorbs all exceptions so the scheduler loop keeps running."""

    @pytest.mark.asyncio
    async def test_delegates_to_compute_and_emit(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(return_value=MagicMock())
        await emitter._safe_emit("hourly")
        emitter.compute_and_emit.assert_awaited_once_with("hourly")

    @pytest.mark.asyncio
    async def test_delegates_correct_period_daily(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(return_value=MagicMock())
        await emitter._safe_emit("daily")
        emitter.compute_and_emit.assert_awaited_once_with("daily")

    @pytest.mark.asyncio
    async def test_swallows_runtime_error(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(side_effect=RuntimeError("boom"))
        # Must not raise
        await emitter._safe_emit("hourly")

    @pytest.mark.asyncio
    async def test_swallows_io_error(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(side_effect=OSError("network"))
        await emitter._safe_emit("daily")

    @pytest.mark.asyncio
    async def test_swallows_generic_exception(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(side_effect=Exception("anything"))
        await emitter._safe_emit("hourly")

    @pytest.mark.asyncio
    async def test_swallows_value_error(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(side_effect=ValueError("bad"))
        await emitter._safe_emit("daily")

    @pytest.mark.asyncio
    async def test_logs_error_on_exception(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(side_effect=Exception("fail"))
        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            await emitter._safe_emit("hourly")
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_error_log_includes_period(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(side_effect=Exception("err"))
        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            await emitter._safe_emit("daily")
        all_calls = str(mock_logger.error.call_args_list)
        assert "daily" in all_calls

    @pytest.mark.asyncio
    async def test_error_log_includes_exception_message(self):
        emitter, _, _ = _make_emitter()
        sentinel_msg = "sentinel-error-xyz"
        emitter.compute_and_emit = AsyncMock(side_effect=Exception(sentinel_msg))
        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            await emitter._safe_emit("hourly")
        all_calls = str(mock_logger.error.call_args_list)
        assert sentinel_msg in all_calls

    @pytest.mark.asyncio
    async def test_no_error_logged_on_success(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(return_value=MagicMock())
        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            await emitter._safe_emit("hourly")
        mock_logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_return_value_is_none_on_success(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(return_value=MagicMock())
        result = await emitter._safe_emit("hourly")
        assert result is None

    @pytest.mark.asyncio
    async def test_return_value_is_none_on_exception(self):
        emitter, _, _ = _make_emitter()
        emitter.compute_and_emit = AsyncMock(side_effect=RuntimeError("x"))
        result = await emitter._safe_emit("daily")
        assert result is None


# ===========================================================================
# 6. schedule_hourly
# ===========================================================================


class TestScheduleHourly:
    """schedule_hourly delegates correctly to _run_schedule."""

    @pytest.mark.asyncio
    async def test_delegates_to_run_schedule_with_hourly_period(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_hourly(interval_seconds=60.0, run_immediately=False)
        emitter._run_schedule.assert_awaited_once_with(
            "hourly", interval_seconds=60.0, run_immediately=False
        )

    @pytest.mark.asyncio
    async def test_default_interval_is_3600(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_hourly(run_immediately=False)
        call_kwargs = emitter._run_schedule.call_args.kwargs
        assert call_kwargs["interval_seconds"] == 3600.0

    @pytest.mark.asyncio
    async def test_default_run_immediately_is_true(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_hourly()
        call_kwargs = emitter._run_schedule.call_args.kwargs
        assert call_kwargs["run_immediately"] is True

    @pytest.mark.asyncio
    async def test_custom_interval_forwarded(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_hourly(interval_seconds=120.0, run_immediately=False)
        assert emitter._run_schedule.call_args.kwargs["interval_seconds"] == 120.0

    @pytest.mark.asyncio
    async def test_run_immediately_false_forwarded(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_hourly(run_immediately=False)
        assert emitter._run_schedule.call_args.kwargs["run_immediately"] is False

    @pytest.mark.asyncio
    async def test_period_arg_is_hourly(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_hourly(run_immediately=False)
        assert emitter._run_schedule.call_args.args[0] == "hourly"


# ===========================================================================
# 7. schedule_daily
# ===========================================================================


class TestScheduleDaily:
    """schedule_daily delegates correctly to _run_schedule."""

    @pytest.mark.asyncio
    async def test_delegates_to_run_schedule_with_daily_period(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_daily(interval_seconds=60.0, run_immediately=False)
        emitter._run_schedule.assert_awaited_once_with(
            "daily", interval_seconds=60.0, run_immediately=False
        )

    @pytest.mark.asyncio
    async def test_default_interval_is_86400(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_daily(run_immediately=False)
        call_kwargs = emitter._run_schedule.call_args.kwargs
        assert call_kwargs["interval_seconds"] == 86400.0

    @pytest.mark.asyncio
    async def test_default_run_immediately_is_true(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_daily()
        call_kwargs = emitter._run_schedule.call_args.kwargs
        assert call_kwargs["run_immediately"] is True

    @pytest.mark.asyncio
    async def test_custom_interval_forwarded(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_daily(interval_seconds=300.0, run_immediately=False)
        assert emitter._run_schedule.call_args.kwargs["interval_seconds"] == 300.0

    @pytest.mark.asyncio
    async def test_run_immediately_false_forwarded(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_daily(run_immediately=False)
        assert emitter._run_schedule.call_args.kwargs["run_immediately"] is False

    @pytest.mark.asyncio
    async def test_period_arg_is_daily(self):
        emitter, _, _ = _make_emitter()
        emitter._run_schedule = AsyncMock()
        await emitter.schedule_daily(run_immediately=False)
        assert emitter._run_schedule.call_args.args[0] == "daily"


# ===========================================================================
# 8. _run_schedule — loop control
# ===========================================================================


class TestRunSchedule:
    """Internal scheduler loop: ordering, interval, period forwarding."""

    @pytest.mark.asyncio
    async def test_run_immediately_true_emits_before_sleep(self):
        emitter, _, _ = _make_emitter()
        call_order: list[str] = []

        async def mock_safe_emit(period: str) -> None:
            call_order.append(f"emit:{period}")

        async def mock_sleep(seconds: float) -> None:
            call_order.append("sleep")
            raise asyncio.CancelledError

        emitter._safe_emit = mock_safe_emit  # type: ignore[method-assign]

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.asyncio.sleep",
            side_effect=mock_sleep,
        ):
            with pytest.raises(asyncio.CancelledError):
                await emitter._run_schedule(
                    "hourly", interval_seconds=5.0, run_immediately=True
                )

        assert call_order[0] == "emit:hourly", "Emit must come before sleep"
        assert "sleep" in call_order

    @pytest.mark.asyncio
    async def test_run_immediately_false_sleeps_before_emit(self):
        emitter, _, _ = _make_emitter()
        call_order: list[str] = []

        async def mock_safe_emit(period: str) -> None:
            call_order.append(f"emit:{period}")

        async def mock_sleep(seconds: float) -> None:
            call_order.append("sleep")
            raise asyncio.CancelledError

        emitter._safe_emit = mock_safe_emit  # type: ignore[method-assign]

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.asyncio.sleep",
            side_effect=mock_sleep,
        ):
            with pytest.raises(asyncio.CancelledError):
                await emitter._run_schedule(
                    "daily", interval_seconds=10.0, run_immediately=False
                )

        assert call_order[0] == "sleep", "Sleep must come before first emit"
        # With CancelledError raised on sleep, emit never runs
        assert "emit:daily" not in call_order

    @pytest.mark.asyncio
    async def test_interval_seconds_passed_to_sleep(self):
        emitter, _, _ = _make_emitter()
        emitter._safe_emit = AsyncMock()
        sleep_args: list[float] = []

        async def capturing_sleep(seconds: float) -> None:
            sleep_args.append(seconds)
            raise asyncio.CancelledError

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.asyncio.sleep",
            side_effect=capturing_sleep,
        ):
            with pytest.raises(asyncio.CancelledError):
                await emitter._run_schedule(
                    "hourly", interval_seconds=777.5, run_immediately=False
                )

        assert sleep_args == [777.5]

    @pytest.mark.asyncio
    async def test_period_forwarded_to_safe_emit(self):
        emitter, _, _ = _make_emitter()
        emitter._safe_emit = AsyncMock()

        async def one_shot_sleep(seconds: float) -> None:
            raise asyncio.CancelledError

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.asyncio.sleep",
            side_effect=one_shot_sleep,
        ):
            with pytest.raises(asyncio.CancelledError):
                await emitter._run_schedule(
                    "daily", interval_seconds=1.0, run_immediately=True
                )

        for c in emitter._safe_emit.call_args_list:
            assert c.args[0] == "daily"

    @pytest.mark.asyncio
    async def test_loop_iterates_multiple_times(self):
        emitter, _, _ = _make_emitter()
        emit_count = 0

        async def mock_safe_emit(period: str) -> None:
            nonlocal emit_count
            emit_count += 1

        emitter._safe_emit = mock_safe_emit  # type: ignore[method-assign]

        sleep_count = 0

        async def counting_sleep(seconds: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.asyncio.sleep",
            side_effect=counting_sleep,
        ):
            with pytest.raises(asyncio.CancelledError):
                await emitter._run_schedule(
                    "hourly", interval_seconds=1.0, run_immediately=False
                )

        # 3 sleeps: after sleep 1 → emit 1, after sleep 2 → emit 2, sleep 3 → raises
        assert sleep_count == 3
        assert emit_count == 2

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        emitter, _, _ = _make_emitter()
        emitter._safe_emit = AsyncMock()

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.asyncio.sleep",
            side_effect=asyncio.CancelledError,
        ):
            with pytest.raises(asyncio.CancelledError):
                await emitter._run_schedule(
                    "daily", interval_seconds=1.0, run_immediately=False
                )

    @pytest.mark.asyncio
    async def test_run_schedule_logs_startup_info(self):
        emitter, _, _ = _make_emitter()
        emitter._safe_emit = AsyncMock()

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            with patch(
                "forge_harness.webhook_server.services.scorecard_emitter.asyncio.sleep",
                side_effect=asyncio.CancelledError,
            ):
                with pytest.raises(asyncio.CancelledError):
                    await emitter._run_schedule(
                        "hourly", interval_seconds=3600.0, run_immediately=False
                    )

        mock_logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_run_immediately_true_emit_count_then_sleep_then_emit(self):
        """With run_immediately=True: emit → sleep → emit (2 emits for 1 sleep)."""
        emitter, _, _ = _make_emitter()
        emit_count = 0

        async def mock_safe_emit(period: str) -> None:
            nonlocal emit_count
            emit_count += 1

        emitter._safe_emit = mock_safe_emit  # type: ignore[method-assign]

        sleep_count = 0

        async def one_sleep(seconds: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 1:
                raise asyncio.CancelledError

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.asyncio.sleep",
            side_effect=one_sleep,
        ):
            with pytest.raises(asyncio.CancelledError):
                await emitter._run_schedule(
                    "daily", interval_seconds=1.0, run_immediately=True
                )

        # immediate emit (before sleep) + sleep cancels before loop emit
        assert emit_count == 1
        assert sleep_count == 1


# ===========================================================================
# 9. get_scorecard_emitter — singleton factory
# ===========================================================================


class TestGetScorecardEmitter:
    """Singleton factory: first-call requirements, caching, thread safety."""

    def test_first_call_without_task_source_raises_value_error(self):
        with pytest.raises(ValueError, match="task_source is required"):
            get_scorecard_emitter()

    def test_first_call_with_task_source_returns_scorecard_emitter(self):
        bus = _make_event_bus()
        emitter = get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)
        assert isinstance(emitter, ScorecardEmitter)

    def test_second_call_returns_same_instance(self):
        bus = _make_event_bus()
        source = _make_task_source()
        a = get_scorecard_emitter(task_source=source, event_bus=bus)
        b = get_scorecard_emitter()
        assert a is b

    def test_second_call_ignores_different_task_source(self):
        bus = _make_event_bus()
        source_a = _make_task_source()
        source_b = _make_task_source()
        a = get_scorecard_emitter(task_source=source_a, event_bus=bus)
        b = get_scorecard_emitter(task_source=source_b, event_bus=bus)
        assert a is b
        # Original task_source must still be stored
        assert a._task_source is source_a

    def test_first_call_node_id_stored_in_singleton(self):
        bus = _make_event_bus()
        emitter = get_scorecard_emitter(
            task_source=_make_task_source(), event_bus=bus, node_id="forge:gamma"
        )
        assert emitter._node_id == "forge:gamma"

    def test_first_call_event_bus_stored_in_singleton(self):
        bus = _make_event_bus()
        emitter = get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)
        assert emitter._event_bus is bus

    def test_repeated_calls_with_different_args_return_same_instance(self):
        bus_a = _make_event_bus()
        bus_b = _make_event_bus()
        a = get_scorecard_emitter(
            task_source=_make_task_source(), event_bus=bus_a, node_id="forge:x"
        )
        b = get_scorecard_emitter(
            task_source=_make_task_source(), event_bus=bus_b, node_id="forge:y"
        )
        assert a is b

    def test_three_consecutive_calls_all_same_instance(self):
        bus = _make_event_bus()
        a = get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)
        b = get_scorecard_emitter()
        c = get_scorecard_emitter()
        assert a is b is c

    def test_default_node_id_when_not_specified(self):
        bus = _make_event_bus()
        emitter = get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)
        assert emitter._node_id == _DEFAULT_NODE_ID


# ===========================================================================
# 10. reset_scorecard_emitter
# ===========================================================================


class TestResetScorecardEmitter:
    """Singleton teardown and re-initialisation after reset."""

    def test_reset_clears_singleton_new_instance_created(self):
        bus = _make_event_bus()
        a = get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)
        reset_scorecard_emitter()
        b = get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)
        assert a is not b

    def test_reset_allows_new_node_id(self):
        bus = _make_event_bus()
        get_scorecard_emitter(
            task_source=_make_task_source(), event_bus=bus, node_id="forge:alpha"
        )
        reset_scorecard_emitter()
        emitter = get_scorecard_emitter(
            task_source=_make_task_source(), event_bus=bus, node_id="forge:beta"
        )
        assert emitter._node_id == "forge:beta"

    def test_reset_allows_new_event_bus(self):
        bus_a = _make_event_bus()
        bus_b = _make_event_bus()
        get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus_a)
        reset_scorecard_emitter()
        emitter = get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus_b)
        assert emitter._event_bus is bus_b

    def test_reset_allows_new_task_source(self):
        bus = _make_event_bus()
        source_a = _make_task_source()
        source_b = _make_task_source()
        get_scorecard_emitter(task_source=source_a, event_bus=bus)
        reset_scorecard_emitter()
        emitter = get_scorecard_emitter(task_source=source_b, event_bus=bus)
        assert emitter._task_source is source_b

    def test_reset_without_prior_init_is_safe(self):
        reset_scorecard_emitter()  # No singleton exists yet — must not raise.

    def test_double_reset_is_safe(self):
        bus = _make_event_bus()
        get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)
        reset_scorecard_emitter()
        reset_scorecard_emitter()  # Second reset must not raise.

    def test_get_after_reset_requires_task_source(self):
        bus = _make_event_bus()
        get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)
        reset_scorecard_emitter()
        with pytest.raises(ValueError, match="task_source is required"):
            get_scorecard_emitter()

    def test_module_level_variable_cleared(self):
        """Internal _emitter_instance should be None after reset."""
        import forge_harness.webhook_server.services.scorecard_emitter as _mod

        bus = _make_event_bus()
        get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)
        assert _mod._emitter_instance is not None

        reset_scorecard_emitter()
        assert _mod._emitter_instance is None


# ===========================================================================
# 11. Module-level constants
# ===========================================================================


class TestModuleLevelConstants:
    """Verify that module-level mappings are correct."""

    def test_period_duration_keys(self):
        assert set(_PERIOD_DURATION.keys()) == {"hourly", "daily"}

    def test_hourly_duration_is_one_hour(self):
        assert _PERIOD_DURATION["hourly"] == timedelta(hours=1)

    def test_daily_duration_is_24_hours(self):
        assert _PERIOD_DURATION["daily"] == timedelta(hours=24)

    def test_event_type_map_keys(self):
        assert set(_EVENT_TYPE_MAP.keys()) == {"hourly", "daily"}

    def test_event_type_map_values(self):
        assert _EVENT_TYPE_MAP["hourly"] == "scorecard_hourly"
        assert _EVENT_TYPE_MAP["daily"] == "scorecard_daily"

    def test_default_node_id_value(self):
        assert _DEFAULT_NODE_ID == "forge:unknown"
