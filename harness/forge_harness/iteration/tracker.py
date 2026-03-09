"""Iteration tracker for FORGE Harness.

Tracks iteration history, progress metrics, and provides completion summaries.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class IterationRecord:
    """Record of a single iteration."""

    iteration_number: int
    started_at: datetime
    completed_at: datetime | None = None
    features_completed: list[str] = field(default_factory=list)
    tests_added: int = 0
    lines_added: int = 0
    commit_hash: str | None = None
    duration_seconds: float = 0.0
    status: str = "in_progress"  # in_progress, completed, failed
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration_number": self.iteration_number,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "features_completed": self.features_completed,
            "tests_added": self.tests_added,
            "lines_added": self.lines_added,
            "commit_hash": self.commit_hash,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IterationRecord":
        return cls(
            iteration_number=data["iteration_number"],
            started_at=datetime.fromisoformat(data["started_at"]),
            completed_at=datetime.fromisoformat(data["completed_at"])
            if data.get("completed_at")
            else None,
            features_completed=data.get("features_completed", []),
            tests_added=data.get("tests_added", 0),
            lines_added=data.get("lines_added", 0),
            commit_hash=data.get("commit_hash"),
            duration_seconds=data.get("duration_seconds", 0.0),
            status=data.get("status", "in_progress"),
            notes=data.get("notes", ""),
        )


@dataclass
class TrackerStats:
    """Statistics from iteration tracking."""

    total_iterations: int = 0
    completed_iterations: int = 0
    failed_iterations: int = 0
    total_features: int = 0
    total_tests: int = 0
    total_lines: int = 0
    avg_duration: float = 0.0


class IterationTracker:
    """Tracks iteration history and progress."""

    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or Path(".forge/learning/iteration_history.json")
        self._history: list[IterationRecord] = []
        self._current: IterationRecord | None = None
        self._load_history()

    def _load_history(self):
        """Load iteration history from storage."""
        if self.storage_path.exists():
            try:
                data = json.loads(self.storage_path.read_text())
                self._history = [IterationRecord.from_dict(r) for r in data.get("iterations", [])]
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load history: {e}")

    def _save_history(self):
        """Save iteration history to storage."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "iterations": [r.to_dict() for r in self._history],
            "last_updated": datetime.now().isoformat(),
        }
        self.storage_path.write_text(json.dumps(data, indent=2))

    def start_iteration(self, iteration_number: int) -> IterationRecord:
        """Start tracking a new iteration."""
        if self._current and self._current.status == "in_progress":
            logger.warning("Previous iteration still in progress, marking as failed")
            self._current.status = "failed"
            self._history.append(self._current)

        self._current = IterationRecord(
            iteration_number=iteration_number, started_at=datetime.now()
        )
        return self._current

    def complete_iteration(
        self,
        features: list[str],
        tests_added: int = 0,
        lines_added: int = 0,
        commit_hash: str | None = None,
        notes: str = "",
    ) -> IterationRecord:
        """Mark current iteration as complete."""
        if not self._current:
            raise RuntimeError("No iteration in progress")

        self._current.completed_at = datetime.now()
        self._current.features_completed = features
        self._current.tests_added = tests_added
        self._current.lines_added = lines_added
        self._current.commit_hash = commit_hash
        self._current.status = "completed"
        self._current.notes = notes
        self._current.duration_seconds = (
            self._current.completed_at - self._current.started_at
        ).total_seconds()

        self._history.append(self._current)
        self._save_history()

        result = self._current
        self._current = None
        return result

    def fail_iteration(self, reason: str = "") -> IterationRecord:
        """Mark current iteration as failed."""
        if not self._current:
            raise RuntimeError("No iteration in progress")

        self._current.completed_at = datetime.now()
        self._current.status = "failed"
        self._current.notes = reason
        self._current.duration_seconds = (
            self._current.completed_at - self._current.started_at
        ).total_seconds()

        self._history.append(self._current)
        self._save_history()

        result = self._current
        self._current = None
        return result

    def get_stats(self) -> TrackerStats:
        """Get statistics from iteration history."""
        completed = [r for r in self._history if r.status == "completed"]
        failed = [r for r in self._history if r.status == "failed"]

        total_duration = sum(r.duration_seconds for r in completed)

        return TrackerStats(
            total_iterations=len(self._history),
            completed_iterations=len(completed),
            failed_iterations=len(failed),
            total_features=sum(len(r.features_completed) for r in completed),
            total_tests=sum(r.tests_added for r in completed),
            total_lines=sum(r.lines_added for r in completed),
            avg_duration=total_duration / len(completed) if completed else 0.0,
        )

    def get_recent(self, count: int = 10) -> list[IterationRecord]:
        """Get recent iteration records."""
        return self._history[-count:]

    def get_current(self) -> IterationRecord | None:
        """Get current iteration if in progress."""
        return self._current


def create_tracker(storage_path: Path | None = None) -> IterationTracker:
    """Factory function to create an IterationTracker."""
    return IterationTracker(storage_path)
