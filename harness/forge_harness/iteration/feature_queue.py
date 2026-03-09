"""Feature queue manager for FORGE Harness.

Manages features.json programmatically for the iteration loop.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class FeatureStatus(str, Enum):
    """Feature status values."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class Priority(str, Enum):
    """Feature priority levels."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Feature:
    """A single feature from the queue."""

    id: str
    title: str
    description: str = ""
    status: FeatureStatus = FeatureStatus.PENDING
    priority: Priority = Priority.MEDIUM
    epic: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    test_command: str = ""
    assigned_to: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "epic": self.epic,
            "acceptance_criteria": self.acceptance_criteria,
            "test_command": self.test_command,
            "assigned_to": self.assigned_to,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Feature":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            status=FeatureStatus(data.get("status", "pending")),
            priority=Priority(data.get("priority", "medium")),
            epic=data.get("epic", ""),
            acceptance_criteria=data.get("acceptance_criteria", []),
            test_command=data.get("test_command", ""),
            assigned_to=data.get("assigned_to"),
            started_at=datetime.fromisoformat(data["started_at"])
            if data.get("started_at")
            else None,
            completed_at=datetime.fromisoformat(data["completed_at"])
            if data.get("completed_at")
            else None,
        )


@dataclass
class QueueStats:
    """Statistics about the feature queue."""

    total: int = 0
    pending: int = 0
    in_progress: int = 0
    completed: int = 0
    blocked: int = 0
    by_priority: dict[str, int] = field(default_factory=dict)
    by_epic: dict[str, int] = field(default_factory=dict)


class FeatureQueue:
    """Manages the feature queue from features.json."""

    def __init__(self, features_path: Path | None = None):
        self.features_path = features_path or Path("features.json")
        self._features: list[Feature] = []
        self._metadata: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load features from JSON file."""
        if not self.features_path.exists():
            logger.warning(f"Features file not found: {self.features_path}")
            return

        try:
            data = json.loads(self.features_path.read_text())
            self._features = [Feature.from_dict(f) for f in data.get("features", [])]
            self._metadata = data.get("metadata", {})
            logger.info(f"Loaded {len(self._features)} features")
        except Exception as e:
            logger.error(f"Failed to load features: {e}")

    def _save(self) -> None:
        """Save features to JSON file."""
        data = {
            "version": self._metadata.get("version", "1.0"),
            "description": self._metadata.get("description", ""),
            "features": [f.to_dict() for f in self._features],
            "metadata": {
                **self._metadata,
                "updated_at": datetime.now().isoformat(),
                "total_features": len(self._features),
            },
        }
        self.features_path.write_text(json.dumps(data, indent=2))

    def get_all(self) -> list[Feature]:
        """Get all features."""
        return self._features.copy()

    def get_by_id(self, feature_id: str) -> Feature | None:
        """Get a feature by ID."""
        for f in self._features:
            if f.id == feature_id:
                return f
        return None

    def get_pending(self, limit: int = 10) -> list[Feature]:
        """Get pending features, sorted by priority."""
        pending = [f for f in self._features if f.status == FeatureStatus.PENDING]
        # Sort by priority (high first)
        priority_order = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
        pending.sort(key=lambda f: priority_order.get(f.priority, 1))
        return pending[:limit]

    def get_by_status(self, status: FeatureStatus) -> list[Feature]:
        """Get features by status."""
        return [f for f in self._features if f.status == status]

    def get_by_epic(self, epic: str) -> list[Feature]:
        """Get features by epic."""
        return [f for f in self._features if f.epic == epic]

    def get_by_priority(self, priority: Priority) -> list[Feature]:
        """Get features by priority."""
        return [f for f in self._features if f.priority == priority]

    def start_feature(self, feature_id: str, assigned_to: str | None = None) -> bool:
        """Mark a feature as in progress."""
        feature = self.get_by_id(feature_id)
        if not feature:
            return False

        feature.status = FeatureStatus.IN_PROGRESS
        feature.started_at = datetime.now()
        feature.assigned_to = assigned_to
        self._save()
        return True

    def complete_feature(self, feature_id: str) -> bool:
        """Mark a feature as completed."""
        feature = self.get_by_id(feature_id)
        if not feature:
            return False

        feature.status = FeatureStatus.COMPLETED
        feature.completed_at = datetime.now()
        self._save()
        return True

    def block_feature(self, feature_id: str, reason: str = "") -> bool:
        """Mark a feature as blocked."""
        feature = self.get_by_id(feature_id)
        if not feature:
            return False

        feature.status = FeatureStatus.BLOCKED
        if reason:
            feature.description = f"{feature.description}\n\nBlocked: {reason}"
        self._save()
        return True

    def add_feature(self, feature: Feature) -> bool:
        """Add a new feature to the queue."""
        if self.get_by_id(feature.id):
            return False  # ID already exists

        self._features.append(feature)
        self._save()
        return True

    def remove_feature(self, feature_id: str) -> bool:
        """Remove a feature from the queue."""
        feature = self.get_by_id(feature_id)
        if not feature:
            return False

        self._features.remove(feature)
        self._save()
        return True

    def get_stats(self) -> QueueStats:
        """Get queue statistics."""
        stats = QueueStats(total=len(self._features))

        for f in self._features:
            if f.status == FeatureStatus.PENDING:
                stats.pending += 1
            elif f.status == FeatureStatus.IN_PROGRESS:
                stats.in_progress += 1
            elif f.status == FeatureStatus.COMPLETED:
                stats.completed += 1
            elif f.status == FeatureStatus.BLOCKED:
                stats.blocked += 1

            # Count by priority
            stats.by_priority[f.priority.value] = stats.by_priority.get(f.priority.value, 0) + 1

            # Count by epic
            if f.epic:
                stats.by_epic[f.epic] = stats.by_epic.get(f.epic, 0) + 1

        return stats

    def get_next_feature(self) -> Feature | None:
        """Get the next feature to work on."""
        pending = self.get_pending(limit=1)
        return pending[0] if pending else None


def create_feature_queue(features_path: Path | None = None) -> FeatureQueue:
    """Factory function to create a FeatureQueue."""
    return FeatureQueue(features_path)
