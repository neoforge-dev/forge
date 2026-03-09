"""
Learning Store for the FORGE Meta-Learning System.

Persists patterns and decisions to enable learning from past experiences.
Uses JSON files for storage with optional auto-persistence.

Includes threshold adjustment based on session success rates (loop-003):
- Success rates below 70% lower complexity thresholds
- Success rates above 95% raise complexity thresholds
- Thresholds persist across sessions
- Threshold history is logged for analysis
"""

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

# Type hint for tracker to avoid circular import
from typing import TYPE_CHECKING, Optional

from forge_harness.meta_learning.config import LearningStoreConfig
from forge_harness.meta_learning.schemas import (
    DecisionAction,
    DecisionRecord,
    PatternRecord,
)

if TYPE_CHECKING:
    from forge_harness.posthog_tracker import HarnessTracker

logger = logging.getLogger(__name__)


# =============================================================================
# Threshold Adjustment Types
# =============================================================================


@dataclass
class ThresholdConfig:
    """Configuration for adjustable thresholds."""

    # Complexity thresholds (higher = more permissive)
    complexity_threshold: float = 0.5
    max_file_changes: int = 20
    max_lines_per_change: int = 500

    # Decision thresholds
    block_threshold: float = 0.3
    human_review_threshold: float = 0.5
    caution_threshold: float = 0.7

    # Confidence requirements
    min_confidence: float = 0.6

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ThresholdConfig":
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ThresholdAdjustment:
    """Record of a threshold adjustment."""

    timestamp: datetime
    domain: str
    project: str
    success_rate: float
    previous_thresholds: dict
    new_thresholds: dict
    reason: str

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "domain": self.domain,
            "project": self.project,
            "success_rate": self.success_rate,
            "previous_thresholds": self.previous_thresholds,
            "new_thresholds": self.new_thresholds,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ThresholdAdjustment":
        """Create from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            domain=data["domain"],
            project=data["project"],
            success_rate=data["success_rate"],
            previous_thresholds=data["previous_thresholds"],
            new_thresholds=data["new_thresholds"],
            reason=data["reason"],
        )


class LearningStore:
    """
    Persistent store for patterns and decisions.

    Thread-safe JSON file-based storage with optional auto-persistence.

    Usage:
        store = LearningStore.from_config(config)
        store.record_pattern(pattern)
        store.record_decision(decision)

        # Query patterns
        patterns = store.get_patterns_by_context(context_signature)

        # Record outcomes
        store.record_outcome(decision_id, success=True)
    """

    def __init__(
        self,
        storage_path: Path,
        patterns_file: str = "patterns.json",
        decisions_file: str = "decisions.json",
        thresholds_file: str = "thresholds.json",
        threshold_history_file: str = "threshold_history.json",
        auto_persist: bool = True,
        max_records: int = 10000,
        tracker: Optional["HarnessTracker"] = None,
    ):
        self.storage_path = storage_path
        self.patterns_file = patterns_file
        self.decisions_file = decisions_file
        self.thresholds_file = thresholds_file
        self.threshold_history_file = threshold_history_file
        self.auto_persist = auto_persist
        self.max_records = max_records
        self.tracker = tracker

        self._patterns: dict[str, PatternRecord] = {}
        self._decisions: dict[str, DecisionRecord] = {}
        # Thresholds keyed by "domain/project"
        self._thresholds: dict[str, ThresholdConfig] = {}
        self._threshold_history: list[ThresholdAdjustment] = []
        self._lock = threading.RLock()

        # Ensure storage directory exists
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Load existing data
        self._load()

    @classmethod
    def from_config(cls, config: LearningStoreConfig) -> "LearningStore":
        """Create a LearningStore from configuration."""
        return cls(
            storage_path=config.storage_path,
            patterns_file=config.patterns_file,
            decisions_file=config.decisions_file,
            auto_persist=config.auto_persist,
            max_records=config.max_records,
        )

    def _load(self) -> None:
        """Load patterns, decisions, and thresholds from disk."""
        patterns_path = self.storage_path / self.patterns_file
        decisions_path = self.storage_path / self.decisions_file
        thresholds_path = self.storage_path / self.thresholds_file
        history_path = self.storage_path / self.threshold_history_file

        if patterns_path.exists():
            try:
                with open(patterns_path) as f:
                    data = json.load(f)
                    self._patterns = {k: PatternRecord.model_validate(v) for k, v in data.items()}
            except (json.JSONDecodeError, ValueError):
                self._patterns = {}

        if decisions_path.exists():
            try:
                with open(decisions_path) as f:
                    data = json.load(f)
                    self._decisions = {k: DecisionRecord.model_validate(v) for k, v in data.items()}
            except (json.JSONDecodeError, ValueError):
                self._decisions = {}

        # Load thresholds
        if thresholds_path.exists():
            try:
                with open(thresholds_path) as f:
                    data = json.load(f)
                    self._thresholds = {k: ThresholdConfig.from_dict(v) for k, v in data.items()}
            except (json.JSONDecodeError, ValueError, TypeError):
                self._thresholds = {}

        # Load threshold history
        if history_path.exists():
            try:
                with open(history_path) as f:
                    data = json.load(f)
                    self._threshold_history = [ThresholdAdjustment.from_dict(item) for item in data]
            except (json.JSONDecodeError, ValueError, TypeError):
                self._threshold_history = []

    def _persist(self) -> None:
        """Persist patterns, decisions, and thresholds to disk."""
        patterns_path = self.storage_path / self.patterns_file
        decisions_path = self.storage_path / self.decisions_file
        thresholds_path = self.storage_path / self.thresholds_file
        history_path = self.storage_path / self.threshold_history_file

        # Serialize with datetime handling
        def serialize(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

        with open(patterns_path, "w") as f:
            json.dump(
                {k: v.model_dump(mode="json") for k, v in self._patterns.items()},
                f,
                indent=2,
                default=serialize,
            )

        with open(decisions_path, "w") as f:
            json.dump(
                {k: v.model_dump(mode="json") for k, v in self._decisions.items()},
                f,
                indent=2,
                default=serialize,
            )

        # Persist thresholds
        with open(thresholds_path, "w") as f:
            json.dump(
                {k: v.to_dict() for k, v in self._thresholds.items()},
                f,
                indent=2,
            )

        # Persist threshold history
        with open(history_path, "w") as f:
            json.dump(
                [adj.to_dict() for adj in self._threshold_history],
                f,
                indent=2,
                default=serialize,
            )

    def persist(self) -> None:
        """Manually persist all data to disk."""
        with self._lock:
            self._persist()

    # =========================================================================
    # Context Signature Generation
    # =========================================================================

    @staticmethod
    def compute_context_signature(
        domain: str,
        project: str,
        file_paths: list[str],
        tags: list[str],
    ) -> str:
        """
        Compute a deterministic signature for a context.

        The signature is used to find similar past patterns/decisions.
        """
        # Sort for determinism
        sorted_paths = sorted(file_paths)
        sorted_tags = sorted(tags)

        # Create canonical representation
        canonical = json.dumps(
            {
                "domain": domain,
                "project": project,
                "file_paths": sorted_paths,
                "tags": sorted_tags,
            },
            sort_keys=True,
        )

        # Return SHA-256 hash (first 16 chars for readability)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    # =========================================================================
    # Pattern Management
    # =========================================================================

    def record_pattern(self, pattern: PatternRecord) -> None:
        """Record a new pattern or update an existing one."""
        with self._lock:
            self._patterns[pattern.pattern_id] = pattern

            # Enforce max records (remove oldest)
            if len(self._patterns) > self.max_records:
                self._trim_patterns()

            if self.auto_persist:
                self._persist()

    def _trim_patterns(self) -> None:
        """Remove oldest patterns to stay under max_records."""
        # Sort by last_applied, remove oldest
        sorted_patterns = sorted(
            self._patterns.items(),
            key=lambda x: x[1].last_applied,
        )
        to_remove = len(self._patterns) - self.max_records
        for pattern_id, _ in sorted_patterns[:to_remove]:
            del self._patterns[pattern_id]

    def get_pattern(self, pattern_id: str) -> PatternRecord | None:
        """Get a pattern by ID."""
        with self._lock:
            return self._patterns.get(pattern_id)

    def get_patterns_by_context(
        self,
        context_signature: str,
        min_effectiveness: float = 0.0,
        min_confidence: float = 0.0,
    ) -> list[PatternRecord]:
        """
        Get patterns matching a context signature.

        Filters by minimum effectiveness and confidence thresholds.
        """
        with self._lock:
            return [
                p
                for p in self._patterns.values()
                if p.context_signature == context_signature
                and p.effectiveness >= min_effectiveness
                and p.confidence >= min_confidence
            ]

    def get_patterns_by_type(self, pattern_type: str) -> list[PatternRecord]:
        """Get all patterns of a specific type."""
        with self._lock:
            return [p for p in self._patterns.values() if p.pattern_type == pattern_type]

    def update_pattern_outcome(
        self,
        pattern_id: str,
        success: bool,
    ) -> PatternRecord | None:
        """Update a pattern's success/failure count."""
        with self._lock:
            pattern = self._patterns.get(pattern_id)
            if pattern is None:
                return None

            # Create updated pattern
            updated = PatternRecord(
                pattern_id=pattern.pattern_id,
                context_signature=pattern.context_signature,
                pattern_type=pattern.pattern_type,
                total_applications=pattern.total_applications + 1,
                successful_applications=(pattern.successful_applications + (1 if success else 0)),
                last_applied=datetime.now(UTC),
                created_at=pattern.created_at,
            )

            self._patterns[pattern_id] = updated

            if self.auto_persist:
                self._persist()

            return updated

    # =========================================================================
    # Decision Management
    # =========================================================================

    def record_decision(self, decision: DecisionRecord) -> None:
        """Record a new decision."""
        with self._lock:
            self._decisions[decision.decision_id] = decision

            # Enforce max records (remove oldest)
            if len(self._decisions) > self.max_records:
                self._trim_decisions()

            if self.auto_persist:
                self._persist()

    def _trim_decisions(self) -> None:
        """Remove oldest decisions to stay under max_records."""
        sorted_decisions = sorted(
            self._decisions.items(),
            key=lambda x: x[1].created_at,
        )
        to_remove = len(self._decisions) - self.max_records
        for decision_id, _ in sorted_decisions[:to_remove]:
            del self._decisions[decision_id]

    def get_decision(self, decision_id: str) -> DecisionRecord | None:
        """Get a decision by ID."""
        with self._lock:
            return self._decisions.get(decision_id)

    def get_decisions_by_context(
        self,
        context_signature: str,
        with_outcome_only: bool = False,
    ) -> list[DecisionRecord]:
        """
        Get decisions matching a context signature.

        If with_outcome_only is True, only returns decisions with recorded outcomes.
        """
        with self._lock:
            decisions = [
                d for d in self._decisions.values() if d.context_signature == context_signature
            ]
            if with_outcome_only:
                decisions = [d for d in decisions if d.outcome_success is not None]
            return decisions

    def get_decisions_by_domain_project(
        self,
        domain: str,
        project: str,
    ) -> list[DecisionRecord]:
        """Get all decisions for a specific domain/project."""
        with self._lock:
            return [
                d for d in self._decisions.values() if d.domain == domain and d.project == project
            ]

    def record_outcome(
        self,
        decision_id: str,
        success: bool,
        actual_action: DecisionAction | None = None,
    ) -> DecisionRecord | None:
        """Record the outcome of a decision."""
        with self._lock:
            decision = self._decisions.get(decision_id)
            if decision is None:
                return None

            # Create updated decision
            updated = DecisionRecord(
                decision_id=decision.decision_id,
                context_signature=decision.context_signature,
                domain=decision.domain,
                project=decision.project,
                recommended_action=decision.recommended_action,
                actual_action=actual_action or decision.actual_action,
                outcome_success=success,
                created_at=decision.created_at,
                outcome_recorded_at=datetime.now(UTC),
            )

            self._decisions[decision_id] = updated

            if self.auto_persist:
                self._persist()

            # Track feedback in PostHog
            if self.tracker:
                self.tracker.feedback_recorded(
                    decision_id=decision_id,
                    outcome="success" if success else "failure",
                    feature_id="",  # Not available at this level
                    error_message=None,
                )

            return updated

    # =========================================================================
    # Analytics
    # =========================================================================

    def get_statistics(self) -> dict:
        """Get aggregate statistics about the learning store."""
        with self._lock:
            total_patterns = len(self._patterns)
            total_decisions = len(self._decisions)

            # Pattern stats
            effective_patterns = [
                p for p in self._patterns.values() if p.effectiveness >= 0.7 and p.confidence >= 0.5
            ]

            # Decision stats
            completed_decisions = [
                d for d in self._decisions.values() if d.outcome_success is not None
            ]
            successful_decisions = [d for d in completed_decisions if d.outcome_success]

            return {
                "total_patterns": total_patterns,
                "total_decisions": total_decisions,
                "effective_patterns": len(effective_patterns),
                "completed_decisions": len(completed_decisions),
                "successful_decisions": len(successful_decisions),
                "decision_success_rate": (
                    len(successful_decisions) / len(completed_decisions)
                    if completed_decisions
                    else 0.0
                ),
            }

    # =========================================================================
    # Threshold Management (loop-003)
    # =========================================================================

    @staticmethod
    def _make_threshold_key(domain: str, project: str) -> str:
        """Create a key for threshold storage."""
        return f"{domain}/{project}"

    def get_thresholds(
        self,
        domain: str,
        project: str,
    ) -> ThresholdConfig:
        """
        Get thresholds for a domain/project.

        Returns default thresholds if none are set.
        """
        with self._lock:
            key = self._make_threshold_key(domain, project)
            return self._thresholds.get(key, ThresholdConfig())

    def set_thresholds(
        self,
        domain: str,
        project: str,
        thresholds: ThresholdConfig,
    ) -> None:
        """Set thresholds for a domain/project."""
        with self._lock:
            key = self._make_threshold_key(domain, project)
            self._thresholds[key] = thresholds

            if self.auto_persist:
                self._persist()

    def calculate_success_rate(
        self,
        domain: str,
        project: str,
        min_decisions: int = 5,
    ) -> float | None:
        """
        Calculate success rate for a domain/project.

        Returns None if there are fewer than min_decisions with recorded outcomes.
        """
        with self._lock:
            decisions = [
                d
                for d in self._decisions.values()
                if d.domain == domain and d.project == project and d.outcome_success is not None
            ]

            if len(decisions) < min_decisions:
                return None

            successful = sum(1 for d in decisions if d.outcome_success)
            return successful / len(decisions)

    def adjust_thresholds(
        self,
        domain: str,
        project: str,
        force: bool = False,
    ) -> ThresholdAdjustment | None:
        """
        Automatically adjust thresholds based on session success rate.

        Rules (loop-003):
        - Success rates below 70% lower complexity thresholds (more restrictive)
        - Success rates above 95% raise complexity thresholds (more permissive)

        Args:
            domain: Domain name
            project: Project name
            force: If True, adjust even with few decisions

        Returns:
            ThresholdAdjustment if thresholds were changed, None otherwise.
        """
        with self._lock:
            # Calculate success rate
            min_decisions = 1 if force else 5
            success_rate = self.calculate_success_rate(domain, project, min_decisions=min_decisions)

            if success_rate is None:
                logger.debug("Not enough decisions to adjust thresholds for %s/%s", domain, project)
                return None

            current = self.get_thresholds(domain, project)
            previous_dict = current.to_dict()

            # Determine adjustment direction
            adjustment_factor = 0.0
            reason = ""

            if success_rate < 0.70:
                # Lower thresholds (more restrictive) - 10% decrease
                adjustment_factor = -0.10
                reason = f"Success rate {success_rate:.1%} below 70% - tightening thresholds"
            elif success_rate > 0.95:
                # Raise thresholds (more permissive) - 5% increase
                adjustment_factor = 0.05
                reason = f"Success rate {success_rate:.1%} above 95% - relaxing thresholds"
            else:
                logger.debug(
                    "Success rate %.1f%% in acceptable range for %s/%s",
                    success_rate * 100,
                    domain,
                    project,
                )
                return None

            # Apply adjustment
            new_thresholds = ThresholdConfig(
                complexity_threshold=max(
                    0.1, min(0.9, current.complexity_threshold * (1 + adjustment_factor))
                ),
                max_file_changes=max(5, int(current.max_file_changes * (1 + adjustment_factor))),
                max_lines_per_change=max(
                    50, int(current.max_lines_per_change * (1 + adjustment_factor))
                ),
                block_threshold=max(
                    0.1,
                    min(
                        0.5,
                        current.block_threshold * (1 - adjustment_factor),  # Inverse for block
                    ),
                ),
                human_review_threshold=max(
                    0.3,
                    min(
                        0.8,
                        current.human_review_threshold * (1 - adjustment_factor),  # Inverse
                    ),
                ),
                caution_threshold=max(
                    0.5, min(0.9, current.caution_threshold * (1 + adjustment_factor))
                ),
                min_confidence=max(
                    0.4,
                    min(
                        0.9,
                        current.min_confidence * (1 - adjustment_factor),  # Inverse
                    ),
                ),
            )

            # Record adjustment
            adjustment = ThresholdAdjustment(
                timestamp=datetime.now(UTC),
                domain=domain,
                project=project,
                success_rate=success_rate,
                previous_thresholds=previous_dict,
                new_thresholds=new_thresholds.to_dict(),
                reason=reason,
            )

            # Apply and persist
            key = self._make_threshold_key(domain, project)
            self._thresholds[key] = new_thresholds
            self._threshold_history.append(adjustment)

            logger.info("Adjusted thresholds for %s/%s: %s", domain, project, reason)

            if self.auto_persist:
                self._persist()

            return adjustment

    def get_threshold_history(
        self,
        domain: str | None = None,
        project: str | None = None,
        limit: int = 100,
    ) -> list[ThresholdAdjustment]:
        """
        Get threshold adjustment history.

        Args:
            domain: Filter by domain (optional)
            project: Filter by project (optional)
            limit: Maximum number of records to return

        Returns:
            List of ThresholdAdjustment records, newest first.
        """
        with self._lock:
            history = self._threshold_history

            # Filter if specified
            if domain is not None:
                history = [a for a in history if a.domain == domain]
            if project is not None:
                history = [a for a in history if a.project == project]

            # Sort by timestamp descending and limit
            history = sorted(history, key=lambda a: a.timestamp, reverse=True)
            return history[:limit]

    def clear(self) -> None:
        """Clear all stored data (for testing)."""
        with self._lock:
            self._patterns.clear()
            self._decisions.clear()
            self._thresholds.clear()
            self._threshold_history.clear()
            if self.auto_persist:
                self._persist()
