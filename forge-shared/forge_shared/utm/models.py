"""
UTM parameter models for attribution tracking.
"""

from datetime import datetime

from pydantic import BaseModel


class UTMParams(BaseModel):
    """UTM parameters for attribution tracking."""

    source: str | None = None
    medium: str | None = None
    campaign: str | None = None
    content: str | None = None
    term: str | None = None

    landing_page: str | None = None
    referrer: str | None = None
    timestamp: datetime | None = None

    def is_empty(self) -> bool:
        """Check if all core UTM params are empty."""
        return all(v is None for v in [self.source, self.medium, self.campaign])

    def to_posthog_properties(self) -> dict:
        """Convert to PostHog event properties."""
        return {
            "utm_source": self.source,
            "utm_medium": self.medium,
            "utm_campaign": self.campaign,
            "utm_content": self.content,
            "utm_term": self.term,
            "$referrer": self.referrer,
        }

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "source": self.source,
            "medium": self.medium,
            "campaign": self.campaign,
            "content": self.content,
            "term": self.term,
            "landing_page": self.landing_page,
            "referrer": self.referrer,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UTMParams":
        """Create from dictionary."""
        if data.get("timestamp") and isinstance(data["timestamp"], str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


class AttributionEvent(BaseModel):
    """Attribution event for revenue tracking."""

    user_id: str
    product: str
    event_type: str
    value_usd: float = 0.0
    utm: UTMParams
    timestamp: datetime
