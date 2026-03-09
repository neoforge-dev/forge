"""
Webhook Server Core Models
===========================

Pydantic and dataclass models for webhook payloads and responses.
Extracted from webhook_server.py for better modularity.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


class PatternUpdateRequest(BaseModel):
    """Request body for updating a pattern (partial updates)."""

    name: str | None = None
    category: str | None = None
    template: str | None = None
    variables: list[str] | None = None


@dataclass
class WebhookPayload:
    """Parsed webhook payload.

    Attributes:
        source: Webhook source ("slack", "github")
        event_type: Type of event ("button_click", "issue_comment", etc.)
        notification_id: ID of the notification being responded to
        response_type: Type of response ("approved", "rejected", "comment")
        responder: User who responded
        message: Optional message/comment
        raw_payload: Original webhook payload
        received_at: When webhook was received
    """

    source: str
    event_type: str
    notification_id: str
    response_type: str
    responder: str
    message: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class WebhookResponse:
    """Response to webhook sender.

    Attributes:
        status: Response status ("received", "processed", "error")
        notification_id: ID that was processed
        message: Optional status message
    """

    status: str
    notification_id: str
    message: str | None = None
