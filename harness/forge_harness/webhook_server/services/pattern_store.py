"""
Pattern Store Service

Manages pattern storage in .forge/learning/patterns.json.
Provides CRUD operations for patterns used in reinforcement learning.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Pattern:
    """Pattern for reinforcement learning."""

    id: str
    name: str
    category: str
    template: str
    variables: list[str]
    success_rate: float = 0.5  # Thompson Sampling prior
    uses: int = 0
    # Thompson Sampling parameters (Beta distribution)
    alpha: int = 1  # successes + 1 (prior)
    beta: int = 1  # failures + 1 (prior)
    version: int = 1  # Pattern version for tracking updates
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "pattern_id": self.id,  # For API compatibility
            "name": self.name,
            "category": self.category,
            "template": self.template,
            "variables": self.variables,
            "success_rate": self.success_rate,
            "total_uses": self.uses,
            "uses": self.uses,  # For backward compatibility
            "alpha": self.alpha,
            "beta": self.beta,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pattern":
        """Create from dictionary."""
        return cls(
            id=data.get("pattern_id") or data["id"],
            name=data["name"],
            category=data["category"],
            template=data["template"],
            variables=data.get("variables", []),
            success_rate=data.get("success_rate", 0.5),
            # Handle both uses and total_uses for API compatibility
            uses=data.get("total_uses", data.get("uses", 0)),
            alpha=data.get("alpha", 1),
            beta=data.get("beta", 1),
            version=data.get("version", 1),
            created_at=data.get("created_at", datetime.now(UTC).isoformat()),
            updated_at=data.get("updated_at", datetime.now(UTC).isoformat()),
        )


@dataclass
class PatternOutcome:
    """Individual outcome for a pattern usage."""

    id: str
    pattern_id: str
    success: bool
    variant: str | None = None  # For A/B testing
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "pattern_id": self.pattern_id,
            "success": self.success,
            "variant": self.variant,
            "context": self.context,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatternOutcome":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            pattern_id=data["pattern_id"],
            success=data["success"],
            variant=data.get("variant"),
            context=data.get("context", {}),
            timestamp=data.get("timestamp", datetime.now(UTC).isoformat()),
        )


class PatternStore:
    """Manages pattern storage in .forge/learning/patterns.json.

    Provides CRUD operations for patterns used in reinforcement learning.
    """

    def __init__(self, forge_root: Path | None = None):
        """Initialize pattern store.

        Args:
            forge_root: Root directory for FORGE. Auto-detected if not provided.
        """
        self._forge_root = forge_root
        self._patterns: dict[str, Pattern] = {}
        self._loaded = False

    def _get_patterns_path(self) -> Path:
        """Get path to patterns.json file."""
        if self._forge_root is None:
            # Try to find FORGE root
            cwd = Path.cwd()
            for parent in [cwd] + list(cwd.parents):
                if (parent / "domains.yaml").exists():
                    self._forge_root = parent
                    break
            if self._forge_root is None:
                self._forge_root = cwd

        patterns_dir = self._forge_root / ".forge/learning"
        patterns_dir.mkdir(parents=True, exist_ok=True)
        return patterns_dir / "patterns.json"

    def _load(self) -> None:
        """Load patterns from disk."""
        if self._loaded:
            return

        patterns_path = self._get_patterns_path()
        if patterns_path.exists():
            try:
                import json

                with open(patterns_path) as f:
                    data = json.load(f)
                for pattern_data in data.get("patterns", []):
                    pattern = Pattern.from_dict(pattern_data)
                    self._patterns[pattern.id] = pattern
                logger.debug(f"Loaded {len(self._patterns)} patterns from {patterns_path}")
            except Exception as e:
                logger.error(f"Failed to load patterns: {e}")
        self._loaded = True

    def _save(self) -> None:
        """Save patterns to disk."""

        patterns_path = self._get_patterns_path()
        data = {
            "version": "1.0",
            "patterns": [p.to_dict() for p in self._patterns.values()],
        }
        try:
            with open(patterns_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved {len(self._patterns)} patterns to {patterns_path}")
        except Exception as e:
            logger.error(f"Failed to save patterns: {e}")

    def list_patterns(self, category: str | None = None) -> list[Pattern]:
        """List all patterns, optionally filtered by category.

        Args:
            category: Optional category filter

        Returns:
            List of patterns
        """
        self._load()
        patterns = list(self._patterns.values())
        if category:
            patterns = [p for p in patterns if p.category == category]
        return patterns

    def get_pattern(self, pattern_id: str) -> Pattern | None:
        """Get a pattern by ID.

        Args:
            pattern_id: The pattern ID

        Returns:
            Pattern or None if not found
        """
        self._load()
        return self._patterns.get(pattern_id)

    def create_or_update(
        self,
        pattern_id: str | None,
        name: str,
        category: str,
        template: str,
        variables: list[str] | None = None,
    ) -> Pattern:
        """Create or update a pattern.

        Args:
            pattern_id: Pattern ID (generated if None)
            name: Pattern name
            category: Pattern category
            template: Pattern template
            variables: Template variables

        Returns:
            Created or updated pattern
        """
        self._load()

        if pattern_id and pattern_id in self._patterns:
            # Update existing
            pattern = self._patterns[pattern_id]
            pattern.name = name
            pattern.category = category
            pattern.template = template
            pattern.variables = variables or []
            pattern.updated_at = datetime.now(UTC).isoformat()
        else:
            # Create new
            pattern = Pattern(
                id=pattern_id or str(uuid.uuid4())[:8],
                name=name,
                category=category,
                template=template,
                variables=variables or [],
            )
            self._patterns[pattern.id] = pattern

        self._save()
        return pattern

    def delete_pattern(self, pattern_id: str) -> bool:
        """Delete a pattern.

        Args:
            pattern_id: The pattern ID

        Returns:
            True if deleted, False if not found
        """
        self._load()
        if pattern_id in self._patterns:
            del self._patterns[pattern_id]
            self._save()
            return True
        return False

    def _get_outcomes_path(self) -> Path:
        """Get path to pattern_outcomes.json file."""
        if self._forge_root is None:
            self._get_patterns_path()  # Ensures _forge_root is set

        outcomes_dir = self._forge_root / ".forge/learning"
        outcomes_dir.mkdir(parents=True, exist_ok=True)
        return outcomes_dir / "pattern_outcomes.json"

    def _load_outcomes(self) -> dict[str, list[PatternOutcome]]:
        """Load outcomes from disk.

        Returns:
            Dictionary mapping pattern_id to list of outcomes
        """
        outcomes_path = self._get_outcomes_path()
        outcomes: dict[str, list[PatternOutcome]] = {}

        if outcomes_path.exists():
            try:
                import json

                with open(outcomes_path) as f:
                    data = json.load(f)
                for pattern_id, outcome_list in data.get("outcomes", {}).items():
                    outcomes[pattern_id] = [PatternOutcome.from_dict(o) for o in outcome_list]
                logger.debug(f"Loaded outcomes for {len(outcomes)} patterns")
            except Exception as e:
                logger.error(f"Failed to load outcomes: {e}")

        return outcomes

    def _save_outcomes(self, outcomes: dict[str, list[PatternOutcome]]) -> None:
        """Save outcomes to disk.

        Args:
            outcomes: Dictionary mapping pattern_id to list of outcomes
        """

        outcomes_path = self._get_outcomes_path()
        data = {
            "version": "1.0",
            "outcomes": {
                pattern_id: [o.to_dict() for o in outcome_list]
                for pattern_id, outcome_list in outcomes.items()
            },
        }
        try:
            with open(outcomes_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved outcomes for {len(outcomes)} patterns")
        except Exception as e:
            logger.error(f"Failed to save outcomes: {e}")

    def record_outcome(
        self,
        pattern_id: str,
        success: bool,
        variant: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> PatternOutcome | None:
        """Record an outcome for a pattern and update Thompson Sampling.

        Args:
            pattern_id: The pattern ID
            success: Whether the pattern usage was successful
            variant: Optional A/B test variant identifier
            context: Optional context information

        Returns:
            The recorded outcome, or None if pattern not found
        """
        self._load()
        pattern = self._patterns.get(pattern_id)
        if pattern is None:
            return None

        # Create outcome record
        outcome = PatternOutcome(
            id=str(uuid.uuid4())[:8],
            pattern_id=pattern_id,
            success=success,
            variant=variant,
            context=context or {},
        )

        # Update Thompson Sampling parameters
        if success:
            pattern.alpha += 1
        else:
            pattern.beta += 1

        # Update success rate using Thompson Sampling mean: alpha / (alpha + beta)
        pattern.success_rate = pattern.alpha / (pattern.alpha + pattern.beta)
        pattern.uses += 1
        pattern.updated_at = datetime.now(UTC).isoformat()

        # Save pattern updates
        self._save()

        # Load, update, and save outcomes
        outcomes = self._load_outcomes()
        if pattern_id not in outcomes:
            outcomes[pattern_id] = []
        outcomes[pattern_id].append(outcome)
        self._save_outcomes(outcomes)

        logger.info(
            f"Recorded outcome for {pattern_id}: success={success}, "
            f"new_rate={pattern.success_rate:.3f}, uses={pattern.uses}"
        )

        return outcome

    def get_outcomes(self, pattern_id: str, limit: int | None = None) -> list[PatternOutcome]:
        """Get outcome history for a pattern.

        Args:
            pattern_id: The pattern ID
            limit: Optional limit on number of outcomes to return (most recent)

        Returns:
            List of outcomes, most recent first
        """
        outcomes = self._load_outcomes()
        pattern_outcomes = outcomes.get(pattern_id, [])

        # Sort by timestamp descending (most recent first)
        pattern_outcomes.sort(key=lambda o: o.timestamp, reverse=True)

        if limit:
            pattern_outcomes = pattern_outcomes[:limit]

        return pattern_outcomes

    def get_variant_stats(self, pattern_id: str) -> dict[str, dict[str, Any]]:
        """Get stats for A/B test variants."""
        outcomes = self.get_outcomes(pattern_id)
        variant_stats = {}

        for outcome in outcomes:
            variant_key = outcome.variant or "default"
            if variant_key not in variant_stats:
                variant_stats[variant_key] = {
                    "successes": 0,
                    "failures": 0,
                    "alpha": 1,
                    "beta": 1,
                }

            if outcome.success:
                variant_stats[variant_key]["successes"] += 1
                variant_stats[variant_key]["alpha"] += 1
            else:
                variant_stats[variant_key]["failures"] += 1
                variant_stats[variant_key]["beta"] += 1

        # Calculate success rates
        for variant_key, stats in variant_stats.items():
            stats["success_rate"] = stats["alpha"] / (stats["alpha"] + stats["beta"])
            stats["total"] = stats["successes"] + stats["failures"]

        return variant_stats


# Global pattern store
_pattern_store: PatternStore | None = None


def get_pattern_store() -> PatternStore:
    """Get or create global pattern store."""
    global _pattern_store
    if _pattern_store is None:
        _pattern_store = PatternStore()
    return _pattern_store
