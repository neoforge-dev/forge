"""Tests for forge_harness.webhook_server.services.telegram_notifier.

Covers:
- TelegramNotifier creation
- start() when Telegram is not configured — no subscription
- start() when Telegram is configured — subscribes to event bus
- stop() — unsubscribes, cancels the background task, closes handler
- _format_event for each forwarded event type
- _process_events filters events not in FORWARDED_EVENTS
- _process_events filters fleet.agent.stateChanged for non-critical statuses
- _forward_event broadcasts to all allowed users
- Singleton helpers: start_telegram_notifier / stop_telegram_notifier

All external I/O is mocked — no network or filesystem calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.webhook_server.services.event_bus import EventBus, SSEEvent
from forge_harness.webhook_server.services.telegram_notifier import (
    CRITICAL_STATUSES,
    FORWARDED_EVENTS,
    TelegramNotifier,
    get_telegram_notifier,
    start_telegram_notifier,
    stop_telegram_notifier,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: str, data: dict | None = None) -> SSEEvent:
    """Return a minimal SSEEvent for the given event type."""
    return SSEEvent(id="1", event=event_type, data=data or {})


def _make_configured_handler(allowed_users: set[str] | None = None) -> MagicMock:
    """Return a mock TelegramBotHandler that reports as configured."""
    handler = MagicMock()
    handler.is_configured.return_value = True
    handler.allowed_users = allowed_users or {"111", "222"}
    handler.send_message = AsyncMock(return_value={"ok": True})
    handler.close = AsyncMock()
    return handler


def _make_unconfigured_handler() -> MagicMock:
    """Return a mock TelegramBotHandler that is NOT configured."""
    handler = MagicMock()
    handler.is_configured.return_value = False
    handler.allowed_users = set()
    handler.send_message = AsyncMock()
    handler.close = AsyncMock()
    return handler


def _fresh_event_bus() -> EventBus:
    """Return a brand-new EventBus without touching the singleton."""
    with EventBus._lock:
        EventBus._instance = None
    return EventBus()


# ---------------------------------------------------------------------------
# Reset the module-level singleton before each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the module-level notifier singleton is cleared between tests."""
    import forge_harness.webhook_server.services.telegram_notifier as mod

    mod._telegram_notifier = None
    yield
    mod._telegram_notifier = None


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestTelegramNotifierConstruction:
    def test_default_construction_uses_global_event_bus(self):
        """TelegramNotifier() should attach to the global event bus."""
        bus = _fresh_event_bus()
        with patch(
            "forge_harness.webhook_server.services.telegram_notifier.get_event_bus",
            return_value=bus,
        ):
            notifier = TelegramNotifier()
        assert notifier._event_bus is bus
        assert notifier._running is False
        assert notifier._task is None
        assert notifier._queue is None

    def test_explicit_event_bus_is_used(self):
        """Passing an explicit event bus should bypass get_event_bus()."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        assert notifier._event_bus is bus


# ---------------------------------------------------------------------------
# start() — unconfigured Telegram
# ---------------------------------------------------------------------------


class TestTelegramNotifierStartUnconfigured:
    @pytest.mark.asyncio
    async def test_start_does_not_subscribe_when_unconfigured(self):
        """start() should no-op if Telegram bot is not configured."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)

        handler = _make_unconfigured_handler()
        notifier._handler = handler  # inject directly

        await notifier.start()

        assert notifier._running is False
        assert notifier._queue is None
        assert notifier._task is None

    @pytest.mark.asyncio
    async def test_start_idempotent_when_already_running(self):
        """Calling start() a second time while running must be a no-op."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        notifier._running = True  # simulate already started

        handler = _make_configured_handler()
        notifier._handler = handler

        subscribe_calls_before = len(bus._subscribers)
        await notifier.start()
        assert len(bus._subscribers) == subscribe_calls_before


# ---------------------------------------------------------------------------
# start() — configured Telegram
# ---------------------------------------------------------------------------


class TestTelegramNotifierStartConfigured:
    @pytest.mark.asyncio
    async def test_start_subscribes_and_creates_task(self):
        """start() should subscribe to the bus and launch a background task."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)

        handler = _make_configured_handler()
        notifier._handler = handler

        await notifier.start()

        assert notifier._running is True
        assert notifier._queue is not None
        assert notifier._queue in bus._subscribers
        assert notifier._task is not None
        assert not notifier._task.done()

        # Cleanup
        await notifier.stop()


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


class TestTelegramNotifierStop:
    @pytest.mark.asyncio
    async def test_stop_unsubscribes_queue(self):
        """stop() should remove the queue from the event bus."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        await notifier.start()
        assert notifier._queue in bus._subscribers

        await notifier.stop()
        assert notifier._queue not in bus._subscribers

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        """stop() should cancel the background processing task."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        await notifier.start()
        task = notifier._task
        assert task is not None and not task.done()

        await notifier.stop()
        assert notifier._task is None

    @pytest.mark.asyncio
    async def test_stop_closes_handler(self):
        """stop() should call close() on the handler."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        await notifier.start()
        await notifier.stop()

        handler.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self):
        """stop() called without start() must not raise."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        # No handler loaded, no queue, no task — should be a clean no-op
        await notifier.stop()

    @pytest.mark.asyncio
    async def test_stop_resets_running_flag(self):
        """stop() should leave _running == False."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        await notifier.start()
        await notifier.stop()

        assert notifier._running is False


# ---------------------------------------------------------------------------
# _format_event — per event type
# ---------------------------------------------------------------------------


class TestFormatEvent:
    def _notifier(self) -> TelegramNotifier:
        return TelegramNotifier(event_bus=_fresh_event_bus())

    # -- approval.created ----------------------------------------------------

    def test_format_approval_created(self):
        notifier = self._notifier()
        event = _make_event(
            "approval.created",
            {"title": "Deploy to prod", "request_id": "req-abc"},
        )
        text = notifier._format_event(event)
        assert text is not None
        assert "Approval Required" in text
        assert "Deploy to prod" in text
        assert "req-abc" in text
        assert "/approve req-abc" in text

    def test_format_approval_created_defaults(self):
        """Missing title/request_id fields should fall back to safe defaults."""
        notifier = self._notifier()
        event = _make_event("approval.created", {})
        text = notifier._format_event(event)
        assert text is not None
        assert "New Approval" in text
        assert "?" in text

    # -- approval.resolved ---------------------------------------------------

    def test_format_approval_resolved_approved(self):
        notifier = self._notifier()
        event = _make_event(
            "approval.resolved",
            {"request_id": "req-001", "action": "approved"},
        )
        text = notifier._format_event(event)
        assert text is not None
        assert "req-001" in text
        assert "approved" in text
        # Approved path should use the checkmark emoji codepoint
        assert "\u2705" in text

    def test_format_approval_resolved_rejected(self):
        notifier = self._notifier()
        event = _make_event(
            "approval.resolved",
            {"request_id": "req-002", "action": "rejected"},
        )
        text = notifier._format_event(event)
        assert text is not None
        assert "req-002" in text
        assert "rejected" in text
        assert "\u274c" in text

    # -- task.completed ------------------------------------------------------

    def test_format_task_completed(self):
        notifier = self._notifier()
        event = _make_event(
            "task.completed",
            {"task_id": "task-99", "title": "Build API"},
        )
        text = notifier._format_event(event)
        assert text is not None
        assert "Task Complete" in text
        assert "Build API" in text
        assert "task-99" in text

    def test_format_task_completed_defaults(self):
        notifier = self._notifier()
        event = _make_event("task.completed", {})
        text = notifier._format_event(event)
        assert text is not None
        assert "Task Complete" in text
        assert "Task completed" in text  # default title

    # -- openclaw.task.created -----------------------------------------------

    def test_format_openclaw_task_created(self):
        notifier = self._notifier()
        event = _make_event(
            "openclaw.task.created",
            {"task_id": "oc-55", "priority": "high"},
        )
        text = notifier._format_event(event)
        assert text is not None
        assert "New Task" in text
        assert "high" in text
        assert "oc-55" in text

    def test_format_openclaw_task_created_defaults(self):
        notifier = self._notifier()
        event = _make_event("openclaw.task.created", {})
        text = notifier._format_event(event)
        assert text is not None
        assert "medium" in text  # default priority

    # -- fleet.agent.stateChanged --------------------------------------------

    def test_format_fleet_agent_stuck(self):
        notifier = self._notifier()
        event = _make_event(
            "fleet.agent.stateChanged",
            {"agent_id": "glm", "status": "stuck", "reason": "timeout"},
        )
        text = notifier._format_event(event)
        assert text is not None
        assert "glm" in text
        assert "stuck" in text
        assert "timeout" in text

    def test_format_fleet_agent_exhausted(self):
        notifier = self._notifier()
        event = _make_event(
            "fleet.agent.stateChanged",
            {"agent": "kimi", "status": "exhausted"},
        )
        text = notifier._format_event(event)
        assert text is not None
        assert "kimi" in text
        assert "exhausted" in text

    def test_format_fleet_agent_failed(self):
        notifier = self._notifier()
        event = _make_event(
            "fleet.agent.stateChanged",
            {"agent_id": "minimax", "status": "failed"},
        )
        text = notifier._format_event(event)
        assert text is not None
        assert "minimax" in text
        assert "failed" in text

    def test_format_fleet_agent_no_reason_omits_reason_line(self):
        """When reason is absent the 'Reason:' line must not appear."""
        notifier = self._notifier()
        event = _make_event(
            "fleet.agent.stateChanged",
            {"agent_id": "pi", "status": "stuck"},
        )
        text = notifier._format_event(event)
        assert text is not None
        assert "Reason:" not in text

    # -- unknown event -------------------------------------------------------

    def test_format_unknown_event_returns_none(self):
        notifier = self._notifier()
        event = _make_event("some.unknown.event", {"foo": "bar"})
        result = notifier._format_event(event)
        assert result is None


# ---------------------------------------------------------------------------
# _process_events — filtering
# ---------------------------------------------------------------------------


class TestProcessEventsFiltering:
    @pytest.mark.asyncio
    async def test_non_forwarded_events_are_dropped(self):
        """Events not in FORWARDED_EVENTS must not call _forward_event."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        forward_mock = AsyncMock()
        notifier._forward_event = forward_mock

        await notifier.start()

        # Publish a non-forwarded event
        await bus.publish("some.irrelevant.event", {"x": 1})
        # Give the loop one iteration
        await asyncio.sleep(0.05)

        forward_mock.assert_not_awaited()
        await notifier.stop()

    @pytest.mark.asyncio
    async def test_forwarded_events_are_dispatched(self):
        """Events in FORWARDED_EVENTS should be passed to _forward_event."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        forward_mock = AsyncMock()
        notifier._forward_event = forward_mock

        await notifier.start()

        await bus.publish("task.completed", {"task_id": "t1", "title": "done"})
        await asyncio.sleep(0.05)

        forward_mock.assert_awaited_once()
        await notifier.stop()

    @pytest.mark.asyncio
    async def test_fleet_state_non_critical_is_dropped(self):
        """fleet.agent.stateChanged with non-critical status must be ignored."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        forward_mock = AsyncMock()
        notifier._forward_event = forward_mock

        await notifier.start()

        # "active" is NOT in CRITICAL_STATUSES
        await bus.publish("fleet.agent.stateChanged", {"agent_id": "glm", "status": "active"})
        await asyncio.sleep(0.05)

        forward_mock.assert_not_awaited()
        await notifier.stop()

    @pytest.mark.asyncio
    async def test_fleet_state_critical_is_forwarded(self):
        """fleet.agent.stateChanged with a critical status must reach Telegram."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        forward_mock = AsyncMock()
        notifier._forward_event = forward_mock

        await notifier.start()

        for status in CRITICAL_STATUSES:
            await bus.publish(
                "fleet.agent.stateChanged", {"agent_id": "glm", "status": status}
            )

        await asyncio.sleep(0.05)

        assert forward_mock.await_count == len(CRITICAL_STATUSES)
        await notifier.stop()

    @pytest.mark.asyncio
    async def test_process_events_swallows_forward_errors(self):
        """Errors in _forward_event must not crash the processing loop."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        forward_mock = AsyncMock(side_effect=RuntimeError("network failure"))
        notifier._forward_event = forward_mock

        await notifier.start()

        await bus.publish("task.completed", {"task_id": "t1"})
        await asyncio.sleep(0.05)

        # Loop must still be running
        assert notifier._running is True
        await notifier.stop()


# ---------------------------------------------------------------------------
# _forward_event — broadcasts to all allowed users
# ---------------------------------------------------------------------------


class TestForwardEvent:
    @pytest.mark.asyncio
    async def test_broadcasts_to_all_allowed_users(self):
        """_forward_event must call send_message once per allowed user."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler(allowed_users={"111", "222", "333"})
        notifier._handler = handler

        event = _make_event("task.completed", {"task_id": "t1", "title": "Done"})
        await notifier._forward_event(event)

        assert handler.send_message.await_count == 3
        called_ids = {call.kwargs["chat_id"] for call in handler.send_message.call_args_list}
        assert called_ids == {"111", "222", "333"}

    @pytest.mark.asyncio
    async def test_skips_send_when_format_returns_none(self):
        """_forward_event must not call send_message if _format_event returns None."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler()
        notifier._handler = handler

        event = _make_event("some.unknown.event.type", {})
        await notifier._forward_event(event)

        handler.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_individual_send_failure_does_not_abort_broadcast(self):
        """A send_message failure for one user must not stop delivery to others."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)

        handler = _make_configured_handler(allowed_users={"111", "222"})
        # Make the first call fail, second succeed
        handler.send_message = AsyncMock(
            side_effect=[RuntimeError("Telegram down"), {"ok": True}]
        )
        notifier._handler = handler

        event = _make_event("task.completed", {"task_id": "t1", "title": "X"})
        # Must not raise
        await notifier._forward_event(event)

        assert handler.send_message.await_count == 2


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


class TestSingletonHelpers:
    @pytest.mark.asyncio
    async def test_start_telegram_notifier_creates_singleton(self):
        """start_telegram_notifier() should populate the module singleton."""
        bus = _fresh_event_bus()
        handler = _make_unconfigured_handler()

        with (
            patch(
                "forge_harness.webhook_server.services.telegram_notifier.get_event_bus",
                return_value=bus,
            ),
            patch(
                "forge_harness.telegram_bot.TelegramBotHandler.from_env",
                return_value=handler,
            ),
        ):
            result = await start_telegram_notifier()

        assert result is not None
        assert get_telegram_notifier() is result

    @pytest.mark.asyncio
    async def test_start_telegram_notifier_is_idempotent(self):
        """Calling start_telegram_notifier() twice must return the same instance."""
        bus = _fresh_event_bus()
        handler = _make_unconfigured_handler()

        with (
            patch(
                "forge_harness.webhook_server.services.telegram_notifier.get_event_bus",
                return_value=bus,
            ),
            patch(
                "forge_harness.telegram_bot.TelegramBotHandler.from_env",
                return_value=handler,
            ),
        ):
            first = await start_telegram_notifier()
            second = await start_telegram_notifier()

        assert first is second

    @pytest.mark.asyncio
    async def test_stop_telegram_notifier_clears_singleton(self):
        """stop_telegram_notifier() should set the module singleton to None."""
        bus = _fresh_event_bus()
        handler = _make_configured_handler()

        with (
            patch(
                "forge_harness.webhook_server.services.telegram_notifier.get_event_bus",
                return_value=bus,
            ),
            patch(
                "forge_harness.telegram_bot.TelegramBotHandler.from_env",
                return_value=handler,
            ),
        ):
            await start_telegram_notifier()
            assert get_telegram_notifier() is not None

            await stop_telegram_notifier()

        assert get_telegram_notifier() is None

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_safe(self):
        """stop_telegram_notifier() with no active singleton must be a no-op."""
        assert get_telegram_notifier() is None
        await stop_telegram_notifier()  # must not raise

    def test_get_telegram_notifier_returns_none_before_start(self):
        """get_telegram_notifier() returns None when singleton is uninitialised."""
        assert get_telegram_notifier() is None


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


class TestConstants:
    def test_forwarded_events_set(self):
        assert "approval.created" in FORWARDED_EVENTS
        assert "approval.resolved" in FORWARDED_EVENTS
        assert "task.completed" in FORWARDED_EVENTS
        assert "openclaw.task.created" in FORWARDED_EVENTS
        assert "fleet.agent.stateChanged" in FORWARDED_EVENTS

    def test_critical_statuses_set(self):
        assert "stuck" in CRITICAL_STATUSES
        assert "exhausted" in CRITICAL_STATUSES
        assert "failed" in CRITICAL_STATUSES
        # Non-critical statuses must NOT appear
        assert "active" not in CRITICAL_STATUSES
        assert "idle" not in CRITICAL_STATUSES


# ---------------------------------------------------------------------------
# Context alert events
# ---------------------------------------------------------------------------


class TestContextAlertEvents:
    """Tests for fleet.agent.contextWarning and fleet.agent.contextCritical events."""

    def _notifier(self) -> TelegramNotifier:
        return TelegramNotifier(event_bus=_fresh_event_bus())

    # -- fleet.agent.contextWarning ------------------------------------------

    def test_context_warning_event(self):
        """fleet.agent.contextWarning sends a formatted warning message."""
        notifier = self._notifier()
        event = _make_event(
            "fleet.agent.contextWarning",
            {"agent_id": "glm", "context_percent": 55, "node": "prya"},
        )
        text, reply_markup = notifier._format_event_with_markup(event)
        assert text is not None
        assert "Context Warning" in text
        assert "glm" in text
        assert "prya" in text
        assert "55%" in text
        assert "/rotate glm" in text
        # Warning does not include an inline keyboard
        assert reply_markup is None

    def test_context_warning_defaults(self):
        """Missing fields fall back to safe defaults."""
        notifier = self._notifier()
        event = _make_event("fleet.agent.contextWarning", {})
        text, reply_markup = notifier._format_event_with_markup(event)
        assert text is not None
        assert "Context Warning" in text
        assert "unknown" in text
        assert "0%" in text
        assert reply_markup is None

    def test_context_warning_in_forwarded_events(self):
        """fleet.agent.contextWarning must be in FORWARDED_EVENTS."""
        assert "fleet.agent.contextWarning" in FORWARDED_EVENTS

    # -- fleet.agent.contextCritical -----------------------------------------

    def test_context_critical_event(self):
        """fleet.agent.contextCritical sends a critical alert message."""
        notifier = self._notifier()
        event = _make_event(
            "fleet.agent.contextCritical",
            {"agent_id": "kimi", "context_percent": 85, "node": "sati"},
        )
        text, reply_markup = notifier._format_event_with_markup(event)
        assert text is not None
        assert "Context CRITICAL" in text
        assert "kimi" in text
        assert "sati" in text
        assert "85%" in text
        assert "/rotate kimi" in text

    def test_context_critical_includes_reply_markup(self):
        """fleet.agent.contextCritical must include an inline keyboard with a rotate button."""
        notifier = self._notifier()
        event = _make_event(
            "fleet.agent.contextCritical",
            {"agent_id": "minimax", "context_percent": 90, "node": "prya"},
        )
        text, reply_markup = notifier._format_event_with_markup(event)
        assert reply_markup is not None
        keyboard = reply_markup.get("inline_keyboard")
        assert keyboard is not None
        assert len(keyboard) >= 1
        # First row must contain a rotate button referencing the agent
        first_row = keyboard[0]
        assert len(first_row) >= 1
        button = first_row[0]
        assert "rotate" in button.get("callback_data", "").lower()
        assert "minimax" in button.get("callback_data", "")

    def test_context_critical_defaults(self):
        """Missing fields fall back to safe defaults."""
        notifier = self._notifier()
        event = _make_event("fleet.agent.contextCritical", {})
        text, reply_markup = notifier._format_event_with_markup(event)
        assert text is not None
        assert "Context CRITICAL" in text
        assert "unknown" in text
        assert reply_markup is not None

    def test_context_critical_in_forwarded_events(self):
        """fleet.agent.contextCritical must be in FORWARDED_EVENTS."""
        assert "fleet.agent.contextCritical" in FORWARDED_EVENTS

    # -- Integration: _forward_event passes reply_markup ---------------------

    @pytest.mark.asyncio
    async def test_context_critical_forward_passes_reply_markup(self):
        """_forward_event for contextCritical must pass reply_markup to send_message."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler(allowed_users={"111"})
        notifier._handler = handler

        event = _make_event(
            "fleet.agent.contextCritical",
            {"agent_id": "glm", "context_percent": 82, "node": "prya"},
        )
        await notifier._forward_event(event)

        handler.send_message.assert_awaited_once()
        call_kwargs = handler.send_message.call_args[1]
        assert call_kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_context_warning_forward_no_reply_markup(self):
        """_forward_event for contextWarning must pass reply_markup=None to send_message."""
        bus = _fresh_event_bus()
        notifier = TelegramNotifier(event_bus=bus)
        handler = _make_configured_handler(allowed_users={"111"})
        notifier._handler = handler

        event = _make_event(
            "fleet.agent.contextWarning",
            {"agent_id": "glm", "context_percent": 55, "node": "prya"},
        )
        await notifier._forward_event(event)

        handler.send_message.assert_awaited_once()
        call_kwargs = handler.send_message.call_args[1]
        # reply_markup may be absent or explicitly None
        assert call_kwargs.get("reply_markup") is None
