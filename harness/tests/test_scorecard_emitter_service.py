"""Tests for scorecard_emitter.py — ScorecardEmitter Service.

Tests cover:
- ScorecardEmitter.__init__ with custom and default arguments
- compute_and_emit — happy path (hourly and daily)
- compute_and_emit — invalid period raises ValueError
- compute_and_emit — task source exception propagates after logging
- compute_and_emit — empty task list produces a zero scorecard
- compute_and_emit — populated task list produces correct scorecard
- compute_and_emit — verifies EventBus.publish is called with correct payload
- _safe_emit — swallows exceptions so scheduler loop survives
- _run_schedule — run_immediately=True emits before first sleep
- _run_schedule — run_immediately=False skips the initial emission
- schedule_hourly / schedule_daily — delegate to _run_schedule with correct period
- get_scorecard_emitter — singleton creation, caching, reset
- get_scorecard_emitter — raises ValueError when task_source omitted on first call
- reset_scorecard_emitter — clears singleton
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.webhook_server.services.scorecard_emitter import (
    _DEFAULT_NODE_ID,
    _EVENT_TYPE_MAP,
    _PERIOD_DURATION,
    ScorecardEmitter,
    get_scorecard_emitter,
    reset_scorecard_emitter,
)

# =============================================================================
# Helpers / fixtures
# =============================================================================


async def _empty_source() -> list[dict[str, Any]]:
    """Async task source that returns no tasks."""
    return []


async def _simple_source() -> list[dict[str, Any]]:
    """Async task source with a few representative tasks."""
    return [
        {
            "dispatch_acked": True,
            "evaluator_passed": True,
            "requeued": False,
            "human_touched": False,
            "escaped_defect": False,
            "completed": True,
            "cycle_time_minutes": 10.0,
        },
        {
            "dispatch_acked": True,
            "evaluator_passed": False,
            "requeued": True,
            "human_touched": True,
            "escaped_defect": False,
            "completed": False,
        },
        {
            "dispatch_acked": False,
            "evaluator_passed": True,
            "requeued": False,
            "human_touched": False,
            "escaped_defect": True,
            "completed": True,
            "cycle_time_minutes": 5.0,
        },
    ]


async def _error_source() -> list[dict[str, Any]]:
    """Async task source that always raises."""
    raise RuntimeError("data store unavailable")


class _FakeEventBus:
    """Minimal EventBus stand-in that records publish calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def publish(
        self, event_type: str, data: dict[str, Any], source: str = "scorecard-emitter"
    ) -> None:
        self.calls.append({"event_type": event_type, "data": data, "source": source})


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """Ensure a clean singleton state around every test."""
    reset_scorecard_emitter()
    yield
    reset_scorecard_emitter()


@pytest.fixture
def fake_bus() -> _FakeEventBus:
    return _FakeEventBus()


@pytest.fixture
def emitter(fake_bus: _FakeEventBus) -> ScorecardEmitter:
    return ScorecardEmitter(
        task_source=_empty_source,
        event_bus=fake_bus,
        node_id="forge:test",
    )


# =============================================================================
# Module-level constants
# =============================================================================


class TestModuleConstants:
    def test_period_duration_has_hourly_and_daily(self) -> None:
        assert "hourly" in _PERIOD_DURATION
        assert "daily" in _PERIOD_DURATION

    def test_hourly_duration_is_one_hour(self) -> None:
        assert _PERIOD_DURATION["hourly"] == timedelta(hours=1)

    def test_daily_duration_is_24_hours(self) -> None:
        assert _PERIOD_DURATION["daily"] == timedelta(hours=24)

    def test_event_type_map_keys_match_period_duration_keys(self) -> None:
        assert set(_EVENT_TYPE_MAP.keys()) == set(_PERIOD_DURATION.keys())

    def test_event_type_map_values(self) -> None:
        assert _EVENT_TYPE_MAP["hourly"] == "scorecard_hourly"
        assert _EVENT_TYPE_MAP["daily"] == "scorecard_daily"

    def test_default_node_id_is_string(self) -> None:
        assert isinstance(_DEFAULT_NODE_ID, str)
        assert _DEFAULT_NODE_ID  # non-empty


# =============================================================================
# ScorecardEmitter.__init__
# =============================================================================


class TestScorecardEmitterInit:
    def test_init_stores_node_id(self, fake_bus: _FakeEventBus) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:nova",
        )
        assert emitter._node_id == "forge:nova"

    def test_init_default_node_id(self, fake_bus: _FakeEventBus) -> None:
        emitter = ScorecardEmitter(task_source=_empty_source, event_bus=fake_bus)
        assert emitter._node_id == _DEFAULT_NODE_ID

    def test_init_stores_task_source(self, fake_bus: _FakeEventBus) -> None:
        emitter = ScorecardEmitter(task_source=_empty_source, event_bus=fake_bus)
        assert emitter._task_source is _empty_source

    def test_init_stores_event_bus(self, fake_bus: _FakeEventBus) -> None:
        emitter = ScorecardEmitter(task_source=_empty_source, event_bus=fake_bus)
        assert emitter._event_bus is fake_bus

    def test_init_uses_global_event_bus_when_none_provided(self) -> None:
        """When event_bus=None the global singleton is used."""
        from forge_harness.webhook_server.services.event_bus import get_event_bus

        emitter = ScorecardEmitter(task_source=_empty_source)
        assert emitter._event_bus is get_event_bus()

    def test_running_tasks_list_starts_empty(self, fake_bus: _FakeEventBus) -> None:
        emitter = ScorecardEmitter(task_source=_empty_source, event_bus=fake_bus)
        assert emitter._running_tasks == []


# =============================================================================
# compute_and_emit — invalid period
# =============================================================================


class TestComputeAndEmitInvalidPeriod:
    @pytest.mark.asyncio
    async def test_unknown_period_raises_value_error(
        self, emitter: ScorecardEmitter
    ) -> None:
        with pytest.raises(ValueError, match="Unknown period"):
            await emitter.compute_and_emit(period="weekly")

    @pytest.mark.asyncio
    async def test_empty_string_period_raises_value_error(
        self, emitter: ScorecardEmitter
    ) -> None:
        with pytest.raises(ValueError):
            await emitter.compute_and_emit(period="")


# =============================================================================
# compute_and_emit — task source failure
# =============================================================================


class TestComputeAndEmitTaskSourceFailure:
    @pytest.mark.asyncio
    async def test_task_source_exception_propagates(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_error_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        with pytest.raises(RuntimeError, match="data store unavailable"):
            await emitter.compute_and_emit(period="hourly")

    @pytest.mark.asyncio
    async def test_task_source_exception_does_not_publish(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_error_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        try:
            await emitter.compute_and_emit(period="hourly")
        except RuntimeError:
            pass
        assert len(fake_bus.calls) == 0


# =============================================================================
# compute_and_emit — happy path (hourly)
# =============================================================================


class TestComputeAndEmitHourly:
    @pytest.mark.asyncio
    async def test_returns_scorecard_event(self, emitter: ScorecardEmitter) -> None:
        from forge_harness.webhook_server.models.scorecard import ScorecardEvent

        result = await emitter.compute_and_emit(period="hourly")
        assert isinstance(result, ScorecardEvent)

    @pytest.mark.asyncio
    async def test_event_type_is_scorecard_hourly(
        self, emitter: ScorecardEmitter
    ) -> None:
        result = await emitter.compute_and_emit(period="hourly")
        assert result.event_type == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_publishes_to_event_bus_once(
        self, emitter: ScorecardEmitter, fake_bus: _FakeEventBus
    ) -> None:
        await emitter.compute_and_emit(period="hourly")
        assert len(fake_bus.calls) == 1

    @pytest.mark.asyncio
    async def test_published_event_type_matches(
        self, emitter: ScorecardEmitter, fake_bus: _FakeEventBus
    ) -> None:
        await emitter.compute_and_emit(period="hourly")
        assert fake_bus.calls[0]["event_type"] == "scorecard_hourly"

    @pytest.mark.asyncio
    async def test_published_source_is_scorecard_emitter(
        self, emitter: ScorecardEmitter, fake_bus: _FakeEventBus
    ) -> None:
        await emitter.compute_and_emit(period="hourly")
        assert fake_bus.calls[0]["source"] == "scorecard-emitter"

    @pytest.mark.asyncio
    async def test_empty_task_source_produces_zero_scorecard(
        self, emitter: ScorecardEmitter
    ) -> None:
        result = await emitter.compute_and_emit(period="hourly")
        sc = result.scorecard
        assert sc.total_tasks == 0
        assert sc.dispatch_ack_rate == 0.0
        assert sc.evaluator_pass_rate == 0.0
        assert sc.human_touch_ratio == 0.0
        assert sc.escaped_defects == 0


# =============================================================================
# compute_and_emit — happy path (daily)
# =============================================================================


class TestComputeAndEmitDaily:
    @pytest.mark.asyncio
    async def test_event_type_is_scorecard_daily(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        result = await emitter.compute_and_emit(period="daily")
        assert result.event_type == "scorecard_daily"

    @pytest.mark.asyncio
    async def test_publishes_daily_event_type_to_bus(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        await emitter.compute_and_emit(period="daily")
        assert fake_bus.calls[0]["event_type"] == "scorecard_daily"


# =============================================================================
# compute_and_emit — non-empty task source
# =============================================================================


class TestComputeAndEmitWithTasks:
    @pytest.mark.asyncio
    async def test_total_tasks_matches_source_count(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_simple_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        result = await emitter.compute_and_emit(period="hourly")
        assert result.scorecard.total_tasks == 3

    @pytest.mark.asyncio
    async def test_node_id_is_propagated_to_scorecard(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_simple_source,
            event_bus=fake_bus,
            node_id="forge:nova",
        )
        result = await emitter.compute_and_emit(period="hourly")
        assert result.scorecard.node_id == "forge:nova"

    @pytest.mark.asyncio
    async def test_dispatch_ack_rate_computed_correctly(
        self, fake_bus: _FakeEventBus
    ) -> None:
        # 2 out of 3 tasks have dispatch_acked=True
        emitter = ScorecardEmitter(
            task_source=_simple_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        result = await emitter.compute_and_emit(period="hourly")
        assert abs(result.scorecard.dispatch_ack_rate - 2 / 3) < 1e-9

    @pytest.mark.asyncio
    async def test_escaped_defects_counted_correctly(
        self, fake_bus: _FakeEventBus
    ) -> None:
        # 1 out of 3 tasks has escaped_defect=True
        emitter = ScorecardEmitter(
            task_source=_simple_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        result = await emitter.compute_and_emit(period="hourly")
        assert result.scorecard.escaped_defects == 1

    @pytest.mark.asyncio
    async def test_autonomous_completions_excludes_human_touched(
        self, fake_bus: _FakeEventBus
    ) -> None:
        """Tasks completed=True and human_touched=False count as autonomous."""
        emitter = ScorecardEmitter(
            task_source=_simple_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        result = await emitter.compute_and_emit(period="hourly")
        # task[0]: completed=True, human_touched=False → autonomous
        # task[1]: completed=False → not autonomous
        # task[2]: completed=True, human_touched=False → autonomous
        assert result.scorecard.autonomous_completions == 2

    @pytest.mark.asyncio
    async def test_period_window_for_hourly(self, fake_bus: _FakeEventBus) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        before = datetime.now(UTC)
        result = await emitter.compute_and_emit(period="hourly")
        after = datetime.now(UTC)

        sc = result.scorecard
        # period_end should be approximately "now"
        assert before <= sc.period_end <= after
        # period window should be ~1 hour
        window = sc.period_end - sc.period_start
        assert abs(window.total_seconds() - 3600) < 2  # within 2 s


# =============================================================================
# _safe_emit
# =============================================================================


class TestSafeEmit:
    @pytest.mark.asyncio
    async def test_safe_emit_succeeds_silently(
        self, emitter: ScorecardEmitter
    ) -> None:
        # Should not raise
        await emitter._safe_emit("hourly")

    @pytest.mark.asyncio
    async def test_safe_emit_swallows_compute_error(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_error_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        # Should NOT raise despite task source failure
        await emitter._safe_emit("hourly")
        assert len(fake_bus.calls) == 0

    @pytest.mark.asyncio
    async def test_safe_emit_daily_swallows_error(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_error_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        await emitter._safe_emit("daily")
        assert len(fake_bus.calls) == 0


# =============================================================================
# _run_schedule
# =============================================================================


class TestRunSchedule:
    @pytest.mark.asyncio
    async def test_run_immediately_true_calls_emit_before_sleep(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )

        # Cancel the schedule after a short timeout to avoid infinite loop
        async def run_with_timeout() -> None:
            task = asyncio.create_task(
                emitter._run_schedule(
                    "hourly",
                    interval_seconds=9999.0,
                    run_immediately=True,
                )
            )
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_with_timeout()
        # At least one publish call should have happened (the immediate emission)
        assert len(fake_bus.calls) >= 1

    @pytest.mark.asyncio
    async def test_run_immediately_false_does_not_emit_before_sleep(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )

        async def run_with_timeout() -> None:
            task = asyncio.create_task(
                emitter._run_schedule(
                    "hourly",
                    interval_seconds=9999.0,
                    run_immediately=False,
                )
            )
            # Very brief wait — the sleep(9999) should not have fired
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_with_timeout()
        # No emission should have occurred before the first interval fires
        assert len(fake_bus.calls) == 0


# =============================================================================
# schedule_hourly / schedule_daily
# =============================================================================


class TestScheduleHelpers:
    @pytest.mark.asyncio
    async def test_schedule_hourly_delegates_to_run_schedule(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        with patch.object(emitter, "_run_schedule", new_callable=AsyncMock) as mock_run:
            await emitter.schedule_hourly(interval_seconds=60.0, run_immediately=False)
            mock_run.assert_awaited_once_with(
                "hourly",
                interval_seconds=60.0,
                run_immediately=False,
            )

    @pytest.mark.asyncio
    async def test_schedule_daily_delegates_to_run_schedule(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        with patch.object(emitter, "_run_schedule", new_callable=AsyncMock) as mock_run:
            await emitter.schedule_daily(interval_seconds=86400.0, run_immediately=True)
            mock_run.assert_awaited_once_with(
                "daily",
                interval_seconds=86400.0,
                run_immediately=True,
            )

    @pytest.mark.asyncio
    async def test_schedule_hourly_default_interval(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        with patch.object(emitter, "_run_schedule", new_callable=AsyncMock) as mock_run:
            await emitter.schedule_hourly()
            _, kwargs = mock_run.call_args
            assert kwargs["interval_seconds"] == 3600.0
            assert kwargs["run_immediately"] is True

    @pytest.mark.asyncio
    async def test_schedule_daily_default_interval(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        with patch.object(emitter, "_run_schedule", new_callable=AsyncMock) as mock_run:
            await emitter.schedule_daily()
            _, kwargs = mock_run.call_args
            assert kwargs["interval_seconds"] == 86400.0
            assert kwargs["run_immediately"] is True


# =============================================================================
# get_scorecard_emitter — singleton
# =============================================================================


class TestGetScorecardEmitterSingleton:
    def test_raises_when_no_task_source_on_first_call(self) -> None:
        with pytest.raises(ValueError, match="task_source is required"):
            get_scorecard_emitter()

    def test_creates_instance_on_first_call_with_source(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = get_scorecard_emitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        assert isinstance(emitter, ScorecardEmitter)

    def test_returns_same_instance_on_subsequent_calls(
        self, fake_bus: _FakeEventBus
    ) -> None:
        e1 = get_scorecard_emitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        e2 = get_scorecard_emitter()
        assert e1 is e2

    def test_second_call_ignores_new_arguments(
        self, fake_bus: _FakeEventBus
    ) -> None:
        e1 = get_scorecard_emitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:node1",
        )

        async def other_source() -> list[dict[str, Any]]:
            return []

        e2 = get_scorecard_emitter(
            task_source=other_source,
            node_id="forge:node2",
        )
        assert e1 is e2
        assert e2._node_id == "forge:node1"

    def test_node_id_stored_correctly(self, fake_bus: _FakeEventBus) -> None:
        emitter = get_scorecard_emitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:sati",
        )
        assert emitter._node_id == "forge:sati"


# =============================================================================
# reset_scorecard_emitter
# =============================================================================


class TestResetScorecardEmitter:
    def test_reset_allows_new_singleton_creation(
        self, fake_bus: _FakeEventBus
    ) -> None:
        e1 = get_scorecard_emitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        reset_scorecard_emitter()
        e2 = get_scorecard_emitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        assert e1 is not e2

    def test_reset_idempotent(self) -> None:
        """Calling reset twice should not raise."""
        reset_scorecard_emitter()
        reset_scorecard_emitter()

    def test_after_reset_no_task_source_raises_value_error(self) -> None:
        reset_scorecard_emitter()
        with pytest.raises(ValueError):
            get_scorecard_emitter()


# =============================================================================
# End-to-end: emitter publishes a serialisable payload
# =============================================================================


class TestEndToEndPayload:
    @pytest.mark.asyncio
    async def test_published_payload_is_json_serialisable(
        self, fake_bus: _FakeEventBus
    ) -> None:
        import json

        emitter = ScorecardEmitter(
            task_source=_simple_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        await emitter.compute_and_emit(period="hourly")
        assert len(fake_bus.calls) == 1
        # model_dump(mode="json") should have produced a json-safe dict
        payload = fake_bus.calls[0]["data"]
        serialised = json.dumps(payload)
        assert isinstance(serialised, str)

    @pytest.mark.asyncio
    async def test_payload_contains_scorecard_key(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_simple_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        await emitter.compute_and_emit(period="hourly")
        payload = fake_bus.calls[0]["data"]
        assert "scorecard" in payload

    @pytest.mark.asyncio
    async def test_payload_contains_event_type_key(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        await emitter.compute_and_emit(period="daily")
        payload = fake_bus.calls[0]["data"]
        assert "event_type" in payload
        assert payload["event_type"] == "scorecard_daily"

    @pytest.mark.asyncio
    async def test_multiple_periods_produce_different_event_types(
        self, fake_bus: _FakeEventBus
    ) -> None:
        emitter = ScorecardEmitter(
            task_source=_empty_source,
            event_bus=fake_bus,
            node_id="forge:test",
        )
        await emitter.compute_and_emit(period="hourly")
        await emitter.compute_and_emit(period="daily")
        assert fake_bus.calls[0]["event_type"] == "scorecard_hourly"
        assert fake_bus.calls[1]["event_type"] == "scorecard_daily"
