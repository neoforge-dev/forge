"""Flywheel Configuration and Result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class FlywheelConfig:
    """Configuration for the flywheel.

    Attributes:
        max_iterations: Maximum Ralph loop iterations per run
        max_features_per_project: Max features to generate per project scan
        priority_threshold: Minimum priority to include (critical, high, medium, low)
        include_harness_self_improvement: Also scan harness itself
        dry_run: Preview mode - don't write features or implement
        auto_commit: Automatically commit successful changes
        test_command: Override test command (default: pytest)
    """

    max_iterations: int = 100
    max_features_per_project: int = 10
    priority_threshold: str = "medium"
    include_harness_self_improvement: bool = True
    test_command: str | None = None
    working_dir: Path | None = None  # CWD for test runs (e.g. harness for CC)
    dry_run: bool = False
    auto_commit: bool = False


@dataclass
class FlywheelResult:
    """Result of a flywheel run.

    Attributes:
        started_at: When the flywheel started
        ended_at: When the flywheel ended
        projects_scanned: Number of projects scanned
        features_generated: Total features generated
        features_implemented: Features successfully implemented
        features_blocked: Features that got blocked
        patterns_learned: New patterns added to learning store
        sessions_indexed: Sessions indexed to Code Atlas
        errors: Any errors encountered
    """

    started_at: datetime
    ended_at: datetime | None = None
    projects_scanned: int = 0
    features_generated: int = 0
    features_implemented: int = 0
    features_blocked: int = 0
    patterns_learned: int = 0
    sessions_indexed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": (
                (self.ended_at - self.started_at).total_seconds() if self.ended_at else None
            ),
            "projects_scanned": self.projects_scanned,
            "features_generated": self.features_generated,
            "features_implemented": self.features_implemented,
            "features_blocked": self.features_blocked,
            "patterns_learned": self.patterns_learned,
            "sessions_indexed": self.sessions_indexed,
            "errors": self.errors,
        }
