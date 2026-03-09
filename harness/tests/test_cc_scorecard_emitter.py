"""Comprehensive tests for the ScorecardEmitter service.

Target module:
    forge_harness/webhook_server/services/scorecard_emitter.py

Coverage goals (100%):
  - ScorecardEmitter.__init__ with/without event_bus and custom node_id
  - compute_and_emit happy path for both "hourly" and "daily" periods
  - compute_and_emit raises ValueError for unknown period
  - compute_and_emit re-raises when task_source raises
  - compute_and_emit verifies event_bus.publish called with correct args
  - _safe_emit catches exceptions from compute_and_emit and logs them
  - schedule_hourly with run_immediately=True and False
  - schedule_daily with run_immediately=True and False
  - _run_schedule internal loop body (sleep -> safe_emit cycle)
  - get_scorecard_emitter singleton: first call requires task_source
  - get_scorecard_emitter returns same instance on subsequent calls
  - reset_scorecard_emitter clears the singleton
  - get_scorecard_emitter raises ValueError without task_source on first call
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Singleton isolation helpers
# ---------------------------------------------------------------------------


def _reset_emitter():
    """Reset the global ScorecardEmitter singleton."""
    import forge_harness.webhook_server.services.scorecard_emitter as mod

    mod._emitter_instance = None


@pytest.fixture(autouse=True)
def isolated_emitter():
    """Guarantee a clean singleton for every test."""
    _reset_emitter()
    yield
    _reset_emitter()


# ---------------------------------------------------------------------------
# Lazy imports (after reset guards)
# ---------------------------------------------------------------------------

from forge_harness.webhook_server.services.scorecard_emitter import (  # noqa: E402
    ScorecardEmitter,
    get_scorecard_emitter,
    reset_scorecard_emitter,
)

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _make_task_source(tasks: list | None = None) -> AsyncMock:
    """Return an async callable that yields *tasks* (default empty list)."""
    source = AsyncMock(return_value=tasks if tasks is not None else [])
    return source


def _make_event_bus() -> MagicMock:
    """Return a mock EventBus with an async publish method."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _fake_scorecard(period_start=None, period_end=None, node_id="forge:test"):
    """Build a minimal DarkFactoryScorecard for use in mocks."""
    from forge_harness.webhook_server.models.scorecard import DarkFactoryScorecard

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
    """Tests for ScorecardEmitter constructor behaviour."""

    def test_init_uses_provided_event_bus(self):
        """Constructor stores the supplied event_bus without calling get_event_bus."""
        bus = _make_event_bus()
        task_source = _make_task_source()

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.get_event_bus"
        ) as mock_get:
            emitter = ScorecardEmitter(task_source=task_source, event_bus=bus)

        mock_get.assert_not_called()
        assert emitter._event_bus is bus

    def test_init_calls_get_event_bus_when_no_bus_supplied(self):
        """Constructor falls back to get_event_bus() when event_bus=None."""
        bus = _make_event_bus()
        task_source = _make_task_source()

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.get_event_bus",
            return_value=bus,
        ) as mock_get:
            emitter = ScorecardEmitter(task_source=task_source)

        mock_get.assert_called_once()
        assert emitter._event_bus is bus

    def test_init_default_node_id(self):
        """node_id defaults to 'forge:unknown' when not supplied."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        assert emitter._node_id == "forge:unknown"

    def test_init_custom_node_id(self):
        """node_id is stored as provided."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(
            task_source=_make_task_source(), event_bus=bus, node_id="forge:nova"
        )
        assert emitter._node_id == "forge:nova"

    def test_init_stores_task_source(self):
        """_task_source reference is stored for later use."""
        bus = _make_event_bus()
        source = _make_task_source()
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)
        assert emitter._task_source is source

    def test_init_running_tasks_empty(self):
        """_running_tasks starts as an empty list."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        assert emitter._running_tasks == []


# ===========================================================================
# 2. compute_and_emit — happy path
# ===========================================================================


class TestComputeAndEmitHappyPath:
    """Tests for the core compute_and_emit coroutine in the success case."""

    @pytest.mark.asyncio
    async def test_compute_and_emit_hourly_returns_scorecard_event(self):
        """compute_and_emit('hourly') returns a ScorecardEvent."""
        from forge_harness.webhook_server.models.scorecard import ScorecardEvent

        bus = _make_event_bus()
        source = _make_task_source()
        emitter = ScorecardEmitter(task_source=source, event_bus=bus, node_id="forge:test")

        result = await emitter.compute_and_emit(period="hourly")

        assert isinstance(result, ScorecardEvent)

    @pytest.mark.asyncio
    async def test_compute_and_emit_daily_returns_scorecard_event(self):
        """compute_and_emit('daily') returns a ScorecardEvent."""
        from forge_harness.webhook_server.models.scorecard import ScorecardEvent

        bus = _make_event_bus()
        source = _make_task_source()
        emitter = ScorecardEmitter(task_source=source, event_bus=bus, node_id="forge:test")

        result = await emitter.compute_and_emit(period="daily")

        assert isinstance(result, ScorecardEvent)

    @pytest.mark.asyncio
    async def test_compute_and_emit_hourly_event_type(self):
        """Hourly period sets event_type='scorecard_hourly' on the returned event."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        event = await emitter.compute_and_emit(period="hourly")
        assert event.event_type == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_compute_and_emit_daily_event_type(self):
        """Daily period sets event_type='scorecard_daily' on the returned event."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        event = await emitter.compute_and_emit(period="daily")
        assert event.event_type == "scorecard_daily"

    @pytest.mark.asyncio
    async def test_compute_and_emit_calls_task_source_once(self):
        """task_source is awaited exactly once per compute_and_emit call."""
        bus = _make_event_bus()
        source = _make_task_source()
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)

        await emitter.compute_and_emit(period="hourly")

        source.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_compute_and_emit_calls_event_bus_publish(self):
        """event_bus.publish is awaited exactly once per compute_and_emit call."""
        bus = _make_event_bus()
        source = _make_task_source()
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)

        await emitter.compute_and_emit(period="hourly")

        bus.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_compute_and_emit_publishes_correct_event_type_hourly(self):
        """publish is called with event_type='scorecard_hourly' for hourly period."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

        await emitter.compute_and_emit(period="hourly")

        _kwargs = bus.publish.call_args
        assert _kwargs.kwargs.get("event_type") == "scorecard_hourly" or \
               _kwargs.args[0] == "scorecard_hourly" if _kwargs.args else False or \
               _kwargs.kwargs.get("event_type") == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_compute_and_emit_publishes_correct_event_type_daily(self):
        """publish is called with event_type='scorecard_daily' for daily period."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

        await emitter.compute_and_emit(period="daily")

        call_kwargs = bus.publish.call_args
        # Support both positional and keyword argument styles
        if call_kwargs.kwargs:
            assert call_kwargs.kwargs["event_type"] == "scorecard_daily"
        else:
            assert call_kwargs.args[0] == "scorecard_daily"

    @pytest.mark.asyncio
    async def test_compute_and_emit_publish_source_is_scorecard_emitter(self):
        """publish is called with source='scorecard-emitter'."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

        await emitter.compute_and_emit(period="hourly")

        call_kwargs = bus.publish.call_args
        assert call_kwargs.kwargs.get("source") == "scorecard-emitter"

    @pytest.mark.asyncio
    async def test_compute_and_emit_publish_data_is_dict(self):
        """publish receives a dict for the data argument."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

        await emitter.compute_and_emit(period="hourly")

        call_kwargs = bus.publish.call_args
        assert isinstance(call_kwargs.kwargs.get("data"), dict)

    @pytest.mark.asyncio
    async def test_compute_and_emit_calls_compute_scorecard(self):
        """compute_scorecard is called with tasks from task_source and the emitter's node_id."""
        bus = _make_event_bus()
        tasks = [{"dispatch_acked": True, "completed": True}]
        source = _make_task_source(tasks=tasks)
        emitter = ScorecardEmitter(task_source=source, event_bus=bus, node_id="forge:nova")

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.compute_scorecard",
            wraps=__import__(
                "forge_harness.webhook_server.models.scorecard", fromlist=["compute_scorecard"]
            ).compute_scorecard,
        ) as mock_cs:
            await emitter.compute_and_emit(period="hourly")

        mock_cs.assert_called_once()
        call_args = mock_cs.call_args
        assert call_args.kwargs.get("tasks") == tasks or call_args.args[0] == tasks
        assert call_args.kwargs.get("node_id") == "forge:nova" or \
               "forge:nova" in call_args.args

    @pytest.mark.asyncio
    async def test_compute_and_emit_default_period_is_hourly(self):
        """Default period when not specified produces a scorecard_hourly event."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

        event = await emitter.compute_and_emit()

        assert event.event_type == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_compute_and_emit_with_tasks_produces_non_zero_total(self):
        """When task_source returns tasks, scorecard.total_tasks is non-zero."""
        bus = _make_event_bus()
        tasks = [{"dispatch_acked": True}, {"dispatch_acked": False}]
        emitter = ScorecardEmitter(task_source=_make_task_source(tasks=tasks), event_bus=bus)

        event = await emitter.compute_and_emit(period="hourly")

        assert event.scorecard.total_tasks == 2


# ===========================================================================
# 3. compute_and_emit — invalid period (ValueError)
# ===========================================================================


class TestComputeAndEmitInvalidPeriod:
    """Tests that validate the period guard at the top of compute_and_emit."""

    @pytest.mark.asyncio
    async def test_invalid_period_raises_value_error(self):
        """An unrecognised period raises ValueError immediately."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

        with pytest.raises(ValueError, match="Unknown period"):
            await emitter.compute_and_emit(period="weekly")

    @pytest.mark.asyncio
    async def test_invalid_period_does_not_call_task_source(self):
        """task_source is never awaited when period is invalid."""
        bus = _make_event_bus()
        source = _make_task_source()
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)

        with pytest.raises(ValueError):
            await emitter.compute_and_emit(period="minutely")

        source.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_period_does_not_call_publish(self):
        """event_bus.publish is never called when period is invalid."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

        with pytest.raises(ValueError):
            await emitter.compute_and_emit(period="bad_period")

        bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_period_error_message_lists_valid_periods(self):
        """ValueError message lists the valid period names."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

        with pytest.raises(ValueError) as exc_info:
            await emitter.compute_and_emit(period="nope")

        error_text = str(exc_info.value)
        assert "hourly" in error_text
        assert "daily" in error_text


# ===========================================================================
# 4. compute_and_emit — task_source raises
# ===========================================================================


class TestComputeAndEmitTaskSourceRaises:
    """Tests that verify exceptions from task_source are re-raised."""

    @pytest.mark.asyncio
    async def test_task_source_exception_propagates(self):
        """Exception from task_source propagates out of compute_and_emit."""
        bus = _make_event_bus()
        source = AsyncMock(side_effect=RuntimeError("DB connection refused"))
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)

        with pytest.raises(RuntimeError, match="DB connection refused"):
            await emitter.compute_and_emit(period="hourly")

    @pytest.mark.asyncio
    async def test_task_source_exception_prevents_publish(self):
        """event_bus.publish is never called when task_source fails."""
        bus = _make_event_bus()
        source = AsyncMock(side_effect=OSError("disk error"))
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)

        with pytest.raises(IOError):
            await emitter.compute_and_emit(period="hourly")

        bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_source_value_error_propagates(self):
        """ValueError from task_source propagates (not swallowed by the period guard)."""
        bus = _make_event_bus()
        source = AsyncMock(side_effect=ValueError("malformed data"))
        emitter = ScorecardEmitter(task_source=source, event_bus=bus)

        with pytest.raises(ValueError, match="malformed data"):
            await emitter.compute_and_emit(period="hourly")


# ===========================================================================
# 5. _safe_emit — exception suppression
# ===========================================================================


class TestSafeEmit:
    """Tests for _safe_emit's error-suppression behaviour."""

    @pytest.mark.asyncio
    async def test_safe_emit_calls_compute_and_emit(self):
        """_safe_emit delegates to compute_and_emit."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter.compute_and_emit = AsyncMock(return_value=MagicMock())

        await emitter._safe_emit("hourly")

        emitter.compute_and_emit.assert_awaited_once_with("hourly")

    @pytest.mark.asyncio
    async def test_safe_emit_swallows_runtime_error(self):
        """_safe_emit does not propagate RuntimeError from compute_and_emit."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter.compute_and_emit = AsyncMock(side_effect=RuntimeError("boom"))

        # Should not raise
        await emitter._safe_emit("hourly")

    @pytest.mark.asyncio
    async def test_safe_emit_swallows_io_error(self):
        """_safe_emit does not propagate IOError from compute_and_emit."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter.compute_and_emit = AsyncMock(side_effect=OSError("network failure"))

        await emitter._safe_emit("hourly")

    @pytest.mark.asyncio
    async def test_safe_emit_logs_error_on_exception(self):
        """_safe_emit logs an ERROR when compute_and_emit raises."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter.compute_and_emit = AsyncMock(side_effect=Exception("any error"))

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            await emitter._safe_emit("daily")

        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_safe_emit_passes_period_to_logger_extra(self):
        """The error log includes the period that failed."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter.compute_and_emit = AsyncMock(side_effect=Exception("err"))

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            await emitter._safe_emit("daily")

        # At least one error call should reference the period
        error_calls = mock_logger.error.call_args_list
        assert any(
            "daily" in str(c) for c in error_calls
        ), "Expected 'daily' in logger.error call args"

    @pytest.mark.asyncio
    async def test_safe_emit_success_does_not_log_error(self):
        """_safe_emit does not log an error when compute_and_emit succeeds."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter.compute_and_emit = AsyncMock(return_value=MagicMock())

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.logger"
        ) as mock_logger:
            await emitter._safe_emit("hourly")

        mock_logger.error.assert_not_called()


# ===========================================================================
# 6. schedule_hourly
# ===========================================================================


class TestScheduleHourly:
    """Tests for schedule_hourly delegation to _run_schedule."""

    @pytest.mark.asyncio
    async def test_schedule_hourly_delegates_to_run_schedule(self):
        """schedule_hourly calls _run_schedule with period='hourly'."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter._run_schedule = AsyncMock()

        await emitter.schedule_hourly(interval_seconds=10.0, run_immediately=False)

        emitter._run_schedule.assert_awaited_once_with(
            "hourly", interval_seconds=10.0, run_immediately=False
        )

    @pytest.mark.asyncio
    async def test_schedule_hourly_default_interval(self):
        """schedule_hourly passes 3600.0 as the default interval_seconds."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter._run_schedule = AsyncMock()

        await emitter.schedule_hourly(run_immediately=False)

        emitter._run_schedule.assert_awaited_once_with(
            "hourly", interval_seconds=3600.0, run_immediately=False
        )

    @pytest.mark.asyncio
    async def test_schedule_hourly_run_immediately_true(self):
        """schedule_hourly with run_immediately=True passes that flag through."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter._run_schedule = AsyncMock()

        await emitter.schedule_hourly(run_immediately=True)

        emitter._run_schedule.assert_awaited_once_with(
            "hourly", interval_seconds=3600.0, run_immediately=True
        )

    @pytest.mark.asyncio
    async def test_schedule_hourly_default_run_immediately_is_true(self):
        """The default value of run_immediately for schedule_hourly is True."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter._run_schedule = AsyncMock()

        await emitter.schedule_hourly()

        call_kwargs = emitter._run_schedule.call_args.kwargs
        assert call_kwargs["run_immediately"] is True


# ===========================================================================
# 7. schedule_daily
# ===========================================================================


class TestScheduleDaily:
    """Tests for schedule_daily delegation to _run_schedule."""

    @pytest.mark.asyncio
    async def test_schedule_daily_delegates_to_run_schedule(self):
        """schedule_daily calls _run_schedule with period='daily'."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter._run_schedule = AsyncMock()

        await emitter.schedule_daily(interval_seconds=60.0, run_immediately=False)

        emitter._run_schedule.assert_awaited_once_with(
            "daily", interval_seconds=60.0, run_immediately=False
        )

    @pytest.mark.asyncio
    async def test_schedule_daily_default_interval(self):
        """schedule_daily passes 86400.0 as the default interval_seconds."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter._run_schedule = AsyncMock()

        await emitter.schedule_daily(run_immediately=False)

        emitter._run_schedule.assert_awaited_once_with(
            "daily", interval_seconds=86400.0, run_immediately=False
        )

    @pytest.mark.asyncio
    async def test_schedule_daily_run_immediately_true(self):
        """schedule_daily with run_immediately=True passes that flag through."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter._run_schedule = AsyncMock()

        await emitter.schedule_daily(run_immediately=True)

        emitter._run_schedule.assert_awaited_once_with(
            "daily", interval_seconds=86400.0, run_immediately=True
        )

    @pytest.mark.asyncio
    async def test_schedule_daily_default_run_immediately_is_true(self):
        """The default value of run_immediately for schedule_daily is True."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter._run_schedule = AsyncMock()

        await emitter.schedule_daily()

        call_kwargs = emitter._run_schedule.call_args.kwargs
        assert call_kwargs["run_immediately"] is True


# ===========================================================================
# 8. _run_schedule — loop control
# ===========================================================================


class TestRunSchedule:
    """Tests for _run_schedule internal loop behaviour."""

    @pytest.mark.asyncio
    async def test_run_immediately_true_emits_before_sleep(self):
        """When run_immediately=True, _safe_emit is called before the first sleep."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

        call_order: list[str] = []

        async def mock_safe_emit(period: str) -> None:
            call_order.append(f"emit:{period}")

        async def mock_sleep(seconds: float) -> None:
            call_order.append("sleep")
            # Raise to break the infinite while True loop
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

        assert call_order[0] == "emit:hourly", "emit should happen before sleep"
        assert "sleep" in call_order

    @pytest.mark.asyncio
    async def test_run_immediately_false_sleeps_before_first_emit(self):
        """When run_immediately=False, sleep occurs before the first _safe_emit."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

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

        assert call_order[0] == "sleep", "sleep should happen before first emit"

    @pytest.mark.asyncio
    async def test_run_schedule_passes_interval_seconds_to_sleep(self):
        """asyncio.sleep is called with the supplied interval_seconds value."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
        emitter._safe_emit = AsyncMock()

        sleep_calls: list[float] = []

        async def capturing_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            raise asyncio.CancelledError

        with patch(
            "forge_harness.webhook_server.services.scorecard_emitter.asyncio.sleep",
            side_effect=capturing_sleep,
        ):
            with pytest.raises(asyncio.CancelledError):
                await emitter._run_schedule(
                    "hourly", interval_seconds=999.0, run_immediately=False
                )

        assert sleep_calls == [999.0]

    @pytest.mark.asyncio
    async def test_run_schedule_loops_multiple_iterations(self):
        """After the first sleep+emit, _run_schedule repeats the cycle."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)

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

        # 3 sleeps means 2 emits completed (3rd sleep raises before 3rd emit)
        assert sleep_count == 3
        assert emit_count == 2

    @pytest.mark.asyncio
    async def test_run_schedule_period_forwarded_to_safe_emit(self):
        """_run_schedule passes the period string to each _safe_emit call."""
        bus = _make_event_bus()
        emitter = ScorecardEmitter(task_source=_make_task_source(), event_bus=bus)
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


# ===========================================================================
# 9. get_scorecard_emitter singleton
# ===========================================================================


class TestGetScorecardEmitter:
    """Tests for the get_scorecard_emitter singleton factory."""

    def test_first_call_without_task_source_raises_value_error(self):
        """ValueError is raised when called the first time without task_source."""
        with pytest.raises(ValueError, match="task_source is required"):
            get_scorecard_emitter()

    def test_first_call_with_task_source_returns_emitter(self):
        """First call with a task_source returns a ScorecardEmitter."""
        bus = _make_event_bus()
        source = _make_task_source()

        emitter = get_scorecard_emitter(task_source=source, event_bus=bus)

        assert isinstance(emitter, ScorecardEmitter)

    def test_subsequent_call_returns_same_instance(self):
        """Second call (without args) returns the cached singleton."""
        bus = _make_event_bus()
        source = _make_task_source()

        emitter_a = get_scorecard_emitter(task_source=source, event_bus=bus)
        emitter_b = get_scorecard_emitter()

        assert emitter_a is emitter_b

    def test_subsequent_call_ignores_new_task_source(self):
        """A different task_source on a second call is ignored — same instance returned."""
        bus = _make_event_bus()
        source_a = _make_task_source()
        source_b = _make_task_source()

        emitter_a = get_scorecard_emitter(task_source=source_a, event_bus=bus)
        emitter_b = get_scorecard_emitter(task_source=source_b, event_bus=bus)

        assert emitter_a is emitter_b
        assert emitter_a._task_source is source_a

    def test_first_call_stores_node_id(self):
        """node_id supplied on first call is stored in the singleton."""
        bus = _make_event_bus()
        source = _make_task_source()

        emitter = get_scorecard_emitter(
            task_source=source, event_bus=bus, node_id="forge:sentinel"
        )

        assert emitter._node_id == "forge:sentinel"

    def test_first_call_stores_event_bus(self):
        """event_bus supplied on first call is stored in the singleton."""
        bus = _make_event_bus()
        source = _make_task_source()

        emitter = get_scorecard_emitter(task_source=source, event_bus=bus)

        assert emitter._event_bus is bus


# ===========================================================================
# 10. reset_scorecard_emitter
# ===========================================================================


class TestResetScorecardEmitter:
    """Tests for the reset_scorecard_emitter() test helper."""

    def test_reset_clears_singleton(self):
        """After reset, the next call to get_scorecard_emitter creates a fresh instance."""
        bus = _make_event_bus()
        source_a = _make_task_source()
        source_b = _make_task_source()

        emitter_a = get_scorecard_emitter(task_source=source_a, event_bus=bus)

        reset_scorecard_emitter()

        emitter_b = get_scorecard_emitter(task_source=source_b, event_bus=bus)

        assert emitter_a is not emitter_b

    def test_reset_allows_new_node_id(self):
        """After reset, a new node_id can be provided to the singleton."""
        bus = _make_event_bus()

        get_scorecard_emitter(
            task_source=_make_task_source(), event_bus=bus, node_id="forge:alpha"
        )

        reset_scorecard_emitter()

        emitter = get_scorecard_emitter(
            task_source=_make_task_source(), event_bus=bus, node_id="forge:beta"
        )

        assert emitter._node_id == "forge:beta"

    def test_reset_without_prior_init_is_safe(self):
        """reset_scorecard_emitter() is safe to call even if no singleton exists yet."""
        reset_scorecard_emitter()  # Should not raise
        reset_scorecard_emitter()  # Double reset also safe

    def test_get_after_reset_requires_task_source_again(self):
        """After reset, calling get_scorecard_emitter() without task_source raises ValueError."""
        bus = _make_event_bus()
        get_scorecard_emitter(task_source=_make_task_source(), event_bus=bus)

        reset_scorecard_emitter()

        with pytest.raises(ValueError, match="task_source is required"):
            get_scorecard_emitter()
