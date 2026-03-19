"""Timing helpers used by FORGE CLIs."""

from datetime import UTC, datetime


def iso_utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with Z suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def duration_ms(start_time: datetime | None) -> int:
    """Compute elapsed time in milliseconds from a UTC datetime."""
    if start_time is None:
        return 0
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    return int((datetime.now(UTC) - start_time).total_seconds() * 1000)
