"""Auto-handoff triggers based on session telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class TriggerResult:
    should_create: bool
    reason: str | None = None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def evaluate_session(
    session_data: dict[str, Any],
    context_threshold: int = 50,
    idle_minutes: int = 5,
) -> TriggerResult:
    """Evaluate session metadata for auto-handoff conditions."""
    context_pct = session_data.get("context_percentage")
    if isinstance(context_pct, str):
        try:
            context_pct = int(context_pct)
        except ValueError:
            context_pct = None

    if isinstance(context_pct, int) and context_pct >= context_threshold:
        return TriggerResult(True, f"context >= {context_threshold}%")

    last_activity = _parse_iso(session_data.get("last_activity"))
    if last_activity:
        now = datetime.now(UTC)
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=UTC)
        if now - last_activity >= timedelta(minutes=idle_minutes):
            return TriggerResult(True, f"idle >= {idle_minutes}m")

    status = session_data.get("status")
    if status in {"error", "failed"}:
        return TriggerResult(True, "session error")

    return TriggerResult(False, None)
