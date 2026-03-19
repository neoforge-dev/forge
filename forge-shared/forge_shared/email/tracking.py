"""Email event tracking service."""

import logging
from datetime import datetime
from typing import Any

from .models import EmailEvent

logger = logging.getLogger(__name__)


class EmailTracker:
    """Tracks email delivery and interaction events."""

    def __init__(self):
        """Initialize tracker."""
        self._events: list[EmailEvent] = []
        self._event_index: dict[str, list[EmailEvent]] = {}

    async def track_event(self, event: EmailEvent) -> None:
        """Track an email event.

        Args:
            event: Event to track
        """
        self._events.append(event)

        # Index by email and sequence
        key = f"{event.subscriber_email}:{event.sequence_id}"
        if key not in self._event_index:
            self._event_index[key] = []
        self._event_index[key].append(event)

        logger.info(
            f"Tracked {event.event_type} for {event.subscriber_email} "
            f"in sequence {event.sequence_id}"
        )

    async def get_events(
        self,
        subscriber_email: str | None = None,
        sequence_id: str | None = None,
        event_type: str | None = None,
    ) -> list[EmailEvent]:
        """Get events matching criteria.

        Args:
            subscriber_email: Filter by email
            sequence_id: Filter by sequence
            event_type: Filter by event type

        Returns:
            List of matching events
        """
        results = self._events

        if subscriber_email:
            results = [e for e in results if e.subscriber_email == subscriber_email]

        if sequence_id:
            results = [e for e in results if e.sequence_id == sequence_id]

        if event_type:
            results = [e for e in results if e.event_type == event_type]

        return results

    async def get_sequence_analytics(self, sequence_id: str) -> dict[str, Any]:
        """Get analytics for a sequence.

        Args:
            sequence_id: Sequence ID

        Returns:
            Analytics dictionary
        """
        events = await self.get_events(sequence_id=sequence_id)

        if not events:
            return {
                "sequence_id": sequence_id,
                "total_events": 0,
                "sent": 0,
                "delivered": 0,
                "opened": 0,
                "clicked": 0,
                "bounced": 0,
                "unsubscribed": 0,
            }

        sent = len([e for e in events if e.event_type == "sent"])
        delivered = len([e for e in events if e.event_type == "delivered"])
        opened = len([e for e in events if e.event_type == "opened"])
        clicked = len([e for e in events if e.event_type == "clicked"])
        bounced = len([e for e in events if e.event_type == "bounced"])
        unsubscribed = len([e for e in events if e.event_type == "unsubscribed"])

        return {
            "sequence_id": sequence_id,
            "total_events": len(events),
            "sent": sent,
            "delivered": delivered,
            "opened": opened,
            "clicked": clicked,
            "bounced": bounced,
            "unsubscribed": unsubscribed,
            "open_rate": opened / sent if sent > 0 else 0,
            "click_rate": clicked / sent if sent > 0 else 0,
        }

    async def get_subscriber_events(
        self,
        subscriber_email: str,
        sequence_id: str | None = None,
    ) -> list[EmailEvent]:
        """Get events for a subscriber.

        Args:
            subscriber_email: Subscriber email
            sequence_id: Optional sequence filter

        Returns:
            List of events
        """
        return await self.get_events(
            subscriber_email=subscriber_email,
            sequence_id=sequence_id,
        )

    async def mark_unsubscribed(
        self,
        subscriber_email: str,
        sequence_id: str,
    ) -> None:
        """Mark subscriber as unsubscribed from a sequence.

        Args:
            subscriber_email: Subscriber email
            sequence_id: Sequence ID
        """
        event = EmailEvent(
            id=f"unsub_{subscriber_email}_{sequence_id}_{int(datetime.utcnow().timestamp())}",
            subscriber_email=subscriber_email,
            sequence_id=sequence_id,
            step_id="manual",
            event_type="unsubscribed",
        )
        await self.track_event(event)

    async def clear_events(self) -> None:
        """Clear all tracked events (for testing)."""
        self._events.clear()
        self._event_index.clear()
        logger.info("Cleared all tracked events")
