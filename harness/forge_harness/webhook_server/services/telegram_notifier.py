"""Telegram Notifier Service — pushes EventBus events to Telegram.

Subscribes to the EventBus and forwards relevant events to authorized
Telegram users via the TelegramBotHandler.

Events forwarded:
    - approval.created: New approval request needs attention
    - approval.resolved: Approval was approved/rejected
    - task.completed: A task finished
    - fleet.agent.stateChanged: Agent health status changed (critical only)
    - fleet.agent.contextWarning: Agent context > 50% — warning
    - fleet.agent.contextCritical: Agent context > 80% — critical, needs rotation

Usage:
    from forge_harness.webhook_server.services.telegram_notifier import (
        start_telegram_notifier,
        stop_telegram_notifier,
    )

    # In lifespan startup:
    await start_telegram_notifier()

    # In lifespan shutdown:
    await stop_telegram_notifier()
"""

from __future__ import annotations

import asyncio
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.services.event_bus import EventBus, SSEEvent, get_event_bus

logger = get_logger(__name__)

# Events we forward to Telegram
FORWARDED_EVENTS = {
    "approval.created",
    "approval.resolved",
    "task.completed",
    "openclaw.task.created",
    "fleet.agent.stateChanged",
    "fleet.agent.contextWarning",
    "fleet.agent.contextCritical",
}

# Only forward stateChanged for critical statuses
CRITICAL_STATUSES = {"stuck", "exhausted", "failed"}


class TelegramNotifier:
    """Listens to EventBus and pushes notifications to Telegram."""

    def __init__(self, event_bus: EventBus | None = None):
        self._event_bus = event_bus or get_event_bus()
        self._queue: asyncio.Queue[SSEEvent | None] | None = None
        self._task: asyncio.Task[Any] | None = None
        self._handler = None  # Lazy-loaded TelegramBotHandler
        self._running = False

    def _get_handler(self):
        """Lazy-load TelegramBotHandler to avoid import cycles."""
        if self._handler is None:
            from forge_harness.telegram_bot import TelegramBotHandler

            self._handler = TelegramBotHandler.from_env()
        return self._handler

    async def start(self) -> None:
        """Subscribe to EventBus and start processing events."""
        if self._running:
            return

        handler = self._get_handler()
        if not handler.is_configured():
            logger.info("Telegram not configured — notifier will not start")
            return

        self._queue = self._event_bus.subscribe()
        self._running = True
        self._task = asyncio.create_task(self._process_events())
        logger.info("Telegram notifier started")

    async def stop(self) -> None:
        """Unsubscribe and stop processing."""
        self._running = False
        if self._queue is not None:
            self._event_bus.unsubscribe(self._queue)
            try:
                self._queue.put_nowait(None)  # Signal to stop
            except asyncio.QueueFull:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Only close the handler if it was actually loaded
        if self._handler is not None:
            await self._handler.close()
        logger.info("Telegram notifier stopped")

    async def _process_events(self) -> None:
        """Main event processing loop."""
        while self._running and self._queue is not None:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=30.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if event is None:
                break

            if not isinstance(event, SSEEvent):
                continue

            if event.event not in FORWARDED_EVENTS:
                continue

            # Filter fleet state changes to critical only
            if event.event == "fleet.agent.stateChanged":
                status = event.data.get("status", "").lower()
                if status not in CRITICAL_STATUSES:
                    continue

            try:
                await self._forward_event(event)
            except Exception as e:
                logger.error(f"Failed to forward event to Telegram: {e}")

    async def _forward_event(self, event: SSEEvent) -> None:
        """Format and send an event to all authorized Telegram users."""
        text, reply_markup = self._format_event_with_markup(event)
        if not text:
            return

        await self._send_notification(text, reply_markup=reply_markup)

    async def _send_notification(self, text: str, reply_markup: dict | None = None) -> None:
        """Send notification to all configured Telegram recipients."""
        handler = self._get_handler()
        for chat_id in handler.allowed_users:
            try:
                await handler.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                )
            except Exception as e:
                logger.warning(f"Failed to send Telegram notification to {chat_id}: {e}")

    def _format_event_with_markup(
        self, event: SSEEvent
    ) -> tuple[str | None, dict | None]:
        """Format an SSEEvent as a Telegram HTML message, returning (text, reply_markup).

        Returns a 2-tuple of (text, reply_markup).  Either element may be None.
        reply_markup is non-None only for events that include an inline keyboard.
        """
        data = event.data or {}

        if event.event == "fleet.agent.contextWarning":
            agent_id = data.get("agent_id", "unknown")
            context_pct = data.get("context_percent", 0)
            node = data.get("node", "?")
            text = (
                f"\u26a0\ufe0f <b>Context Warning</b>\n\n"
                f"<b>Agent:</b> {agent_id}\n"
                f"<b>Node:</b> {node}\n"
                f"<b>Context:</b> {context_pct}%\n\n"
                f"Consider running /rotate {agent_id}"
            )
            return text, None

        if event.event == "fleet.agent.contextCritical":
            agent_id = data.get("agent_id", "unknown")
            context_pct = data.get("context_percent", 0)
            node = data.get("node", "?")
            text = (
                f"\U0001f534 <b>Context CRITICAL</b>\n\n"
                f"<b>Agent:</b> {agent_id}\n"
                f"<b>Node:</b> {node}\n"
                f"<b>Context:</b> {context_pct}%\n\n"
                f"Rotation needed! Use /rotate {agent_id}"
            )
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "\U0001f504 Rotate Now", "callback_data": f"rotate:{agent_id}"}]
                ]
            }
            return text, reply_markup

        # For all other event types, delegate to _format_event (no reply_markup).
        return self._format_event(event), None

    def _format_event(self, event: SSEEvent) -> str | None:
        """Format an SSEEvent as a Telegram HTML message."""
        data = event.data or {}

        if event.event == "approval.created":
            title = data.get("title", "New Approval")
            request_id = data.get("request_id", "?")
            return (
                f"\U0001f7e0 <b>Approval Required</b>\n\n"
                f"{title}\n"
                f"ID: <code>{request_id}</code>\n\n"
                f"Reply /approve {request_id} to approve"
            )

        if event.event == "approval.resolved":
            request_id = data.get("request_id", "?")
            action = data.get("action", "resolved")
            emoji = "\u2705" if action == "approved" else "\u274c"
            return f"{emoji} Approval <code>{request_id}</code> {action}"

        if event.event in ("task.completed", "openclaw.task.created"):
            task_id = data.get("task_id", "?")
            if event.event == "task.completed":
                title = data.get("title", "Task completed")
                return f"\u2705 <b>Task Complete</b>\n{title}\nID: <code>{task_id}</code>"
            else:
                priority = data.get("priority", "medium")
                return f"\U0001f4cb <b>New Task</b>\nPriority: {priority}\nID: <code>{task_id}</code>"

        if event.event == "fleet.agent.stateChanged":
            agent = data.get("agent_id", data.get("agent", "?"))
            status = data.get("status", "?")
            reason = data.get("reason", "")
            emoji = {"stuck": "\U0001f7e1", "exhausted": "\U0001f534", "failed": "\u274c"}.get(
                status, "\u26a0\ufe0f"
            )
            text = f"{emoji} <b>Agent Alert: {agent}</b>\nStatus: {status}"
            if reason:
                text += f"\nReason: {reason}"
            return text

        return None


# Module-level singleton
_telegram_notifier: TelegramNotifier | None = None


def get_telegram_notifier() -> TelegramNotifier | None:
    """Return the current module-level TelegramNotifier singleton, or None."""
    return _telegram_notifier


async def start_telegram_notifier() -> TelegramNotifier | None:
    """Create and start the module-level TelegramNotifier singleton.

    Returns:
        The running TelegramNotifier instance, or None if Telegram is not
        configured (missing token / allowed users).
    """
    global _telegram_notifier
    if _telegram_notifier is not None:
        return _telegram_notifier

    _telegram_notifier = TelegramNotifier()
    await _telegram_notifier.start()
    return _telegram_notifier


async def stop_telegram_notifier() -> None:
    """Stop and destroy the module-level TelegramNotifier singleton."""
    global _telegram_notifier
    if _telegram_notifier is not None:
        await _telegram_notifier.stop()
        _telegram_notifier = None
