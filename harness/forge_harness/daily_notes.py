"""
Daily Session Notes Service for FORGE Agents
============================================

Maintains a running log of key events across each day's work, providing:
- Running context for what happened during the day
- Easy reference for handoffs between sessions
- Historical record of feature completions, failures, and decisions

Events logged:
- Feature completions (from ralph_loop)
- Failed iterations (with error summary)
- Decisions requiring human review (from approval_queue)
- Threshold adjustments (from meta_learning)
- Agent handoffs

Usage:
    from forge_harness.daily_notes import DailyNotesService, EventType

    # Create service
    notes = DailyNotesService(notes_dir=Path(".forge/learning/daily"))

    # Log an event
    await notes.log_event(
        event_type=EventType.FEATURE_COMPLETION,
        title="Feature CC-P4-001 Drag-and-drop task reordering",
        details={
            "tests": "8/8 passing",
            "files": ["src/hooks/useDragAndDrop.ts"],
        }
    )

    # View today's notes
    content = await notes.get_today_notes()
    print(content)

    # View specific date
    content = await notes.get_notes(date(2026, 1, 27))
    print(content)
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


class EventType(Enum):
    """Types of events to log in daily notes."""

    FEATURE_COMPLETION = "Feature Completion"
    FEATURE_FAILURE = "Feature Failure"
    APPROVAL_DECISION = "Approval Decision"
    THRESHOLD_ADJUSTMENT = "Threshold Adjustment"
    AGENT_HANDOFF = "Agent Handoff"
    AGENT_STATUS_CHANGE = "Agent Status Change"
    PIPELINE_START = "Pipeline Start"
    PIPELINE_COMPLETE = "Pipeline Complete"
    PIPELINE_ERROR = "Pipeline Error"


@dataclass
class DailyEvent:
    """A single event in the daily notes."""

    timestamp: datetime
    event_type: EventType
    title: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Format event as markdown section."""
        time_str = self.timestamp.strftime("%H:%M")
        lines = [f"## {time_str} - {self.event_type.value}", f"**{self.title}**", ""]

        # Format details as bullet points
        for key, value in self.details.items():
            # Format key as human-readable (snake_case -> Title Case)
            key_formatted = key.replace("_", " ").title()

            # Handle different value types
            if isinstance(value, list):
                if value:
                    lines.append(f"- **{key_formatted}:**")
                    for item in value:
                        lines.append(f"  - {item}")
                else:
                    lines.append(f"- **{key_formatted}:** None")
            elif isinstance(value, dict):
                lines.append(f"- **{key_formatted}:**")
                for k, v in value.items():
                    lines.append(f"  - {k}: {v}")
            elif isinstance(value, bool):
                lines.append(f"- **{key_formatted}:** {'Yes' if value else 'No'}")
            else:
                lines.append(f"- **{key_formatted}:** {value}")

        return "\n".join(lines)


class DailyNotesService:
    """Service for managing daily session notes."""

    def __init__(self, notes_dir: Path | None = None):
        """Initialize the daily notes service.

        Args:
            notes_dir: Directory to store daily notes files.
                      Defaults to .forge/learning/daily
        """
        if notes_dir is None:
            notes_dir = Path(".forge/learning/daily")

        self.notes_dir = notes_dir
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _get_notes_path(self, target_date: date) -> Path:
        """Get path to notes file for a given date.

        Args:
            target_date: Date for the notes file

        Returns:
            Path to notes file (YYYY-MM-DD.md)
        """
        filename = target_date.strftime("%Y-%m-%d.md")
        return self.notes_dir / filename

    async def log_event(
        self,
        event_type: EventType,
        title: str,
        details: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Log an event to today's daily notes.

        Args:
            event_type: Type of event being logged
            title: Brief title/summary of the event
            details: Additional structured details about the event
            timestamp: Timestamp of the event (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now(UTC)

        if details is None:
            details = {}

        event = DailyEvent(
            timestamp=timestamp,
            event_type=event_type,
            title=title,
            details=details,
        )

        target_date = timestamp.date()
        notes_path = self._get_notes_path(target_date)

        async with self._lock:
            # Create or append to notes file
            if not notes_path.exists():
                # Create new file with header
                header = self._create_header(target_date)
                content = header + "\n\n" + event.to_markdown() + "\n\n"
            else:
                # Append to existing file
                content = event.to_markdown() + "\n\n"

            # Append event
            with open(notes_path, "a", encoding="utf-8") as f:
                f.write(content)

        logger.info(
            f"Logged {event_type.value}: {title}",
            extra={"event_type": event_type.value, "date": target_date.isoformat()},
        )

    def _create_header(self, target_date: date) -> str:
        """Create header for a new daily notes file.

        Args:
            target_date: Date for the notes file

        Returns:
            Markdown header
        """
        date_str = target_date.strftime("%A, %B %d, %Y")
        return f"# Daily Session Notes - {date_str}\n\nKey events and progress for {date_str}."

    async def get_notes(self, target_date: date) -> str:
        """Get daily notes for a specific date.

        Args:
            target_date: Date to retrieve notes for

        Returns:
            Markdown content of the notes file, or message if no notes exist
        """
        notes_path = self._get_notes_path(target_date)

        if not notes_path.exists():
            return f"No notes found for {target_date.isoformat()}"

        async with self._lock:
            with open(notes_path, encoding="utf-8") as f:
                return f.read()

    async def get_today_notes(self) -> str:
        """Get daily notes for today.

        Returns:
            Markdown content of today's notes file
        """
        today = date.today()
        return await self.get_notes(today)

    async def list_notes_dates(self, limit: int = 30) -> list[date]:
        """List dates that have notes files, most recent first.

        Args:
            limit: Maximum number of dates to return

        Returns:
            List of dates with notes files
        """
        notes_files = sorted(self.notes_dir.glob("*.md"), reverse=True)
        dates = []

        for notes_file in notes_files[:limit]:
            try:
                # Parse date from filename (YYYY-MM-DD.md)
                date_str = notes_file.stem
                parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                dates.append(parsed_date)
            except ValueError:
                logger.warning(f"Invalid notes filename: {notes_file.name}")
                continue

        return dates

    async def get_recent_notes(self, days: int = 7) -> dict[date, str]:
        """Get notes from the last N days.

        Args:
            days: Number of recent days to retrieve

        Returns:
            Dictionary mapping dates to notes content
        """
        dates = await self.list_notes_dates(limit=days)
        notes = {}

        for target_date in dates:
            content = await self.get_notes(target_date)
            notes[target_date] = content

        return notes

    async def get_summary_stats(self) -> dict[str, Any]:
        """Get summary statistics about daily notes.

        Returns:
            Dictionary with stats like total notes files, date range, etc.
        """
        dates = await self.list_notes_dates(limit=1000)

        if not dates:
            return {
                "total_days": 0,
                "oldest_date": None,
                "newest_date": None,
                "total_events": 0,
            }

        # Count total events across all files
        total_events = 0
        for target_date in dates:
            notes_path = self._get_notes_path(target_date)
            if notes_path.exists():
                with open(notes_path, encoding="utf-8") as f:
                    content = f.read()
                    # Count markdown headers (## HH:MM - Event Type)
                    total_events += content.count("## ")

        return {
            "total_days": len(dates),
            "oldest_date": dates[-1].isoformat() if dates else None,
            "newest_date": dates[0].isoformat() if dates else None,
            "total_events": total_events,
        }


# =============================================================================
# Factory Functions
# =============================================================================


def create_daily_notes_service(notes_dir: Path | None = None) -> DailyNotesService:
    """Create a DailyNotesService instance.

    Args:
        notes_dir: Directory to store daily notes files

    Returns:
        Configured DailyNotesService
    """
    return DailyNotesService(notes_dir=notes_dir)


# =============================================================================
# Global Instance
# =============================================================================

_daily_notes_service: DailyNotesService | None = None


def get_daily_notes_service() -> DailyNotesService:
    """Get the global daily notes service instance.

    Returns:
        Global DailyNotesService instance
    """
    global _daily_notes_service
    if _daily_notes_service is None:
        _daily_notes_service = create_daily_notes_service()
    return _daily_notes_service
