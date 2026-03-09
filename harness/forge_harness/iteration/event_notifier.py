"""Event notifier for FORGE Harness.

Sends notifications for iteration events via webhooks.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class EventType(str, Enum):
    """Types of events that can be notified."""

    ITERATION_START = "iteration_start"
    ITERATION_COMPLETE = "iteration_complete"
    ITERATION_FAIL = "iteration_fail"
    FEATURE_START = "feature_start"
    FEATURE_COMPLETE = "feature_complete"
    FEATURE_FAIL = "feature_fail"
    LOOP_START = "loop_start"
    LOOP_COMPLETE = "loop_complete"


@dataclass
class Event:
    """An event to be notified."""

    type: EventType
    project: str
    timestamp: datetime = field(default_factory=datetime.now)
    iteration_number: int | None = None
    feature_id: str | None = None
    feature_title: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.type.value,
            "project": self.project,
            "timestamp": self.timestamp.isoformat(),
            "iteration_number": self.iteration_number,
            "feature_id": self.feature_id,
            "feature_title": self.feature_title,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class NotificationResult:
    """Result of sending a notification."""

    success: bool
    destination: str
    error: str | None = None
    response_code: int | None = None


@dataclass
class WebhookConfig:
    """Configuration for a webhook destination."""

    url: str
    name: str = "webhook"
    headers: dict[str, str] = field(default_factory=dict)
    events: list[EventType] = field(default_factory=list)  # Empty = all events
    format: str = "json"  # json or slack


class EventNotifier:
    """Sends event notifications to configured destinations."""

    def __init__(self, project_name: str):
        self.project_name = project_name
        self._webhooks: list[WebhookConfig] = []
        self._handlers: dict[EventType, list[Callable[[Event], None]]] = {}

    def add_webhook(self, config: WebhookConfig) -> None:
        """Add a webhook destination."""
        self._webhooks.append(config)

    def add_handler(self, event_type: EventType, handler: Callable[[Event], None]) -> None:
        """Add a custom event handler."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def _format_slack(self, event: Event) -> dict[str, Any]:
        """Format event for Slack."""
        emoji_map = {
            EventType.ITERATION_START: ":rocket:",
            EventType.ITERATION_COMPLETE: ":white_check_mark:",
            EventType.ITERATION_FAIL: ":x:",
            EventType.FEATURE_START: ":hammer:",
            EventType.FEATURE_COMPLETE: ":tada:",
            EventType.FEATURE_FAIL: ":warning:",
            EventType.LOOP_START: ":repeat:",
            EventType.LOOP_COMPLETE: ":checkered_flag:",
        }

        emoji = emoji_map.get(event.type, ":bell:")

        text = f"{emoji} *{event.type.value.replace('_', ' ').title()}*"
        if event.iteration_number:
            text += f" | Iteration {event.iteration_number}"
        if event.feature_title:
            text += f" | {event.feature_title}"

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]

        if event.message:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": event.message}})

        if event.details:
            fields = []
            for key, value in event.details.items():
                fields.append({"type": "mrkdwn", "text": f"*{key}:* {value}"})
            if fields:
                blocks.append(
                    {
                        "type": "section",
                        "fields": fields[:10],  # Slack limit
                    }
                )

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Project: {event.project} | {event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
                    }
                ],
            }
        )

        return {"blocks": blocks}

    def _send_webhook(self, webhook: WebhookConfig, event: Event) -> NotificationResult:
        """Send event to a webhook."""
        # Check if webhook should receive this event
        if webhook.events and event.type not in webhook.events:
            return NotificationResult(
                success=True, destination=webhook.name, error="Event type filtered"
            )

        try:
            # Format payload
            if webhook.format == "slack":
                payload = self._format_slack(event)
            else:
                payload = event.to_dict()

            data = json.dumps(payload).encode("utf-8")

            # Prepare request
            headers = {"Content-Type": "application/json", **webhook.headers}

            req = urllib.request.Request(webhook.url, data=data, headers=headers, method="POST")

            # Send request
            with urllib.request.urlopen(req, timeout=10) as response:
                return NotificationResult(
                    success=True, destination=webhook.name, response_code=response.getcode()
                )

        except urllib.error.HTTPError as e:
            return NotificationResult(
                success=False, destination=webhook.name, error=str(e), response_code=e.code
            )
        except Exception as e:
            return NotificationResult(success=False, destination=webhook.name, error=str(e))

    def notify(self, event: Event) -> list[NotificationResult]:
        """Send event to all configured destinations."""
        results = []

        # Send to webhooks
        for webhook in self._webhooks:
            result = self._send_webhook(webhook, event)
            results.append(result)

        # Call handlers
        if event.type in self._handlers:
            for handler in self._handlers[event.type]:
                try:
                    handler(event)
                except Exception as e:
                    logger.error(f"Handler error: {e}")

        return results

    def notify_iteration_start(self, iteration_number: int, **details) -> list[NotificationResult]:
        """Convenience method for iteration start."""
        event = Event(
            type=EventType.ITERATION_START,
            project=self.project_name,
            iteration_number=iteration_number,
            message=f"Starting iteration {iteration_number}",
            details=details,
        )
        return self.notify(event)

    def notify_iteration_complete(
        self, iteration_number: int, features: list[str], **details
    ) -> list[NotificationResult]:
        """Convenience method for iteration complete."""
        event = Event(
            type=EventType.ITERATION_COMPLETE,
            project=self.project_name,
            iteration_number=iteration_number,
            message=f"Completed iteration {iteration_number} with {len(features)} features",
            details={"features": features, **details},
        )
        return self.notify(event)

    def notify_iteration_fail(
        self, iteration_number: int, error: str, **details
    ) -> list[NotificationResult]:
        """Convenience method for iteration failure."""
        event = Event(
            type=EventType.ITERATION_FAIL,
            project=self.project_name,
            iteration_number=iteration_number,
            message=f"Iteration {iteration_number} failed: {error}",
            details={"error": error, **details},
        )
        return self.notify(event)

    def notify_feature_complete(
        self, feature_id: str, feature_title: str, **details
    ) -> list[NotificationResult]:
        """Convenience method for feature complete."""
        event = Event(
            type=EventType.FEATURE_COMPLETE,
            project=self.project_name,
            feature_id=feature_id,
            feature_title=feature_title,
            message=f"Completed feature: {feature_title}",
            details=details,
        )
        return self.notify(event)


def create_event_notifier(project_name: str) -> EventNotifier:
    """Factory function to create an EventNotifier."""
    return EventNotifier(project_name)
