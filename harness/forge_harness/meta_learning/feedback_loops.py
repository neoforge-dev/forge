"""Feedback loops for continuous improvement.

This module provides hooks for post-session learning:
1. post_session_hook: Auto-index sessions to Code Atlas
2. generate_debt_features: Create features from TechDiligence findings
3. human_gate_optimizer: Adjust confidence thresholds based on outcomes
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bridges.code_atlas import CodeAtlasBridge
    from .bridges.tech_diligence import TechDiligenceBridge
    from .learning_store import LearningStore

logger = logging.getLogger(__name__)


@dataclass
class FeedbackConfig:
    """Configuration for feedback loops.

    Attributes:
        auto_index_enabled: Enable automatic Code Atlas indexing
        debt_features_enabled: Enable tech debt feature generation
        threshold_optimization_enabled: Enable dynamic threshold adjustment
        min_decisions_for_optimization: Minimum decisions before adjusting thresholds
        optimization_interval_hours: Hours between optimization runs
        debt_scan_interval_hours: Hours between tech debt scans
    """

    auto_index_enabled: bool = True
    debt_features_enabled: bool = True
    threshold_optimization_enabled: bool = True
    min_decisions_for_optimization: int = 20
    optimization_interval_hours: float = 24.0
    debt_scan_interval_hours: float = 168.0  # Weekly


@dataclass
class SessionSummary:
    """Summary of a Ralph Loop session for indexing.

    Attributes:
        session_id: Unique session identifier
        domain: Domain name
        project: Project name
        started_at: Session start time
        ended_at: Session end time
        features_completed: Number of features completed
        features_blocked: Number of features blocked
        total_decisions: Total decisions made
        success_rate: Percentage of successful implementations
        files_modified: List of modified files
        patterns_learned: Number of new patterns recorded
    """

    session_id: str
    domain: str
    project: str
    started_at: datetime
    ended_at: datetime
    features_completed: int = 0
    features_blocked: int = 0
    total_decisions: int = 0
    success_rate: float = 0.0
    files_modified: list[str] = field(default_factory=list)
    patterns_learned: int = 0


@dataclass
class DebtFeature:
    """A feature generated from tech debt finding.

    Attributes:
        id: Feature identifier
        name: Feature name
        description: Feature description
        priority: Priority (critical, high, medium, low)
        finding_id: Original TechDiligence finding ID
        category: Finding category
        estimated_effort: Estimated effort in hours
    """

    id: str
    name: str
    description: str
    priority: str
    finding_id: str
    category: str
    estimated_effort: float = 4.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for features.json."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": "pending",
            "priority": self.priority,
            "acceptance_criteria": [
                f"Finding {self.finding_id} is resolved",
                "Tests pass after fix",
            ],
            "metadata": {
                "source": "tech_diligence",
                "finding_id": self.finding_id,
                "category": self.category,
            },
        }


async def post_session_hook(
    session: SessionSummary,
    code_atlas: CodeAtlasBridge | None = None,
    learning_store: LearningStore | None = None,
    config: FeedbackConfig | None = None,
) -> dict[str, Any]:
    """Post-session hook to index session to Code Atlas.

    This hook should be called after each Ralph Loop session completes.
    It indexes the session summary and any new patterns to Code Atlas
    for future retrieval.

    Args:
        session: Session summary data
        code_atlas: Optional Code Atlas bridge (graceful degradation if None)
        learning_store: Optional learning store for patterns
        config: Feedback configuration

    Returns:
        Dict with indexing results
    """
    config = config or FeedbackConfig()
    result = {
        "indexed": False,
        "session_id": session.session_id,
        "patterns_indexed": 0,
        "errors": [],
    }

    if not config.auto_index_enabled:
        result["skipped"] = "auto_index_disabled"
        return result

    if not code_atlas:
        logger.warning("No Code Atlas bridge - skipping session indexing")
        result["skipped"] = "no_code_atlas"
        return result

    try:
        # Prepare session document for indexing
        session_doc = {
            "type": "session_summary",
            "session_id": session.session_id,
            "domain": session.domain,
            "project": session.project,
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat(),
            "features_completed": session.features_completed,
            "features_blocked": session.features_blocked,
            "total_decisions": session.total_decisions,
            "success_rate": session.success_rate,
            "files_modified": session.files_modified,
            "patterns_learned": session.patterns_learned,
        }

        # Index session to Code Atlas
        index_result = await code_atlas.index_session(session_summary=session_doc)
        result["indexed"] = index_result.get("indexed", False)
        result["index_details"] = index_result

        if result["indexed"]:
            logger.info(
                f"Session {session.session_id} indexed to Code Atlas",
                extra={"index_result": index_result},
            )
        else:
            logger.warning(
                f"Failed to index session {session.session_id}",
                extra={"index_result": index_result},
            )

        # Index new patterns if learning store available
        if learning_store:
            # Get recently learned patterns
            patterns = learning_store.get_patterns_by_context(
                context_signature=f"{session.domain}/{session.project}"
            )
            result["patterns_indexed"] = len(patterns)

        logger.info(
            f"Post-session hook completed for {session.session_id}",
            extra=result,
        )

    except Exception as e:
        logger.error(f"Post-session hook error: {e}")
        result["errors"].append(str(e))

    return result


async def generate_debt_features(
    domain: str,
    project: str,
    tech_diligence: TechDiligenceBridge | None = None,
    config: FeedbackConfig | None = None,
    max_features: int = 10,
) -> list[DebtFeature]:
    """Generate features from tech debt findings.

    Scans TechDiligence for blocking issues and generates
    feature specs that can be added to a features.json file
    for automated remediation.

    Args:
        domain: Domain name
        project: Project name
        tech_diligence: TechDiligence bridge
        config: Feedback configuration
        max_features: Maximum features to generate

    Returns:
        List of generated DebtFeature objects
    """
    config = config or FeedbackConfig()
    features: list[DebtFeature] = []

    if not config.debt_features_enabled:
        logger.info("Tech debt feature generation disabled")
        return features

    if not tech_diligence:
        logger.warning("No TechDiligence bridge - skipping debt scan")
        return features

    try:
        # Get signals from TechDiligence
        signals = await tech_diligence.get_signals(
            domain=domain,
            project=project,
            file_paths=[],
        )

        # Process blocking issues
        for i, finding in enumerate(signals.blocking_issues[:max_features]):
            priority = _severity_to_priority(finding.severity.value)
            feature = DebtFeature(
                id=f"DEBT-{finding.finding_id[:8]}",
                name=f"Fix: {finding.title[:50]}",
                description=f"""
Tech debt remediation from TechDiligence scan.

**Finding**: {finding.title}
**Category**: {finding.category.value}
**Severity**: {finding.severity.value}
**File**: {finding.file_path or "N/A"}

**Fix Suggestion**: {finding.fix_suggestion or "Address the finding"}
""".strip(),
                priority=priority,
                finding_id=finding.finding_id,
                category=finding.category.value,
                estimated_effort=_estimate_effort(finding.severity.value),
            )
            features.append(feature)

        # Process warnings if room for more
        remaining = max_features - len(features)
        for warning in signals.warnings[:remaining]:
            priority = _severity_to_priority(warning.severity.value)
            feature = DebtFeature(
                id=f"DEBT-{warning.finding_id[:8]}",
                name=f"Address: {warning.title[:50]}",
                description=f"""
Tech debt remediation (warning level).

**Finding**: {warning.title}
**Category**: {warning.category.value}
**Severity**: {warning.severity.value}
**File**: {warning.file_path or "N/A"}

**Fix Suggestion**: {warning.fix_suggestion or "Address this finding"}
""".strip(),
                priority=priority,
                finding_id=warning.finding_id,
                category=warning.category.value,
                estimated_effort=_estimate_effort(warning.severity.value),
            )
            features.append(feature)

        logger.info(f"Generated {len(features)} debt features for {domain}/{project}")

    except Exception as e:
        logger.error(f"Failed to generate debt features: {e}")

    return features


def _severity_to_priority(severity: str) -> str:
    """Convert TechDiligence severity to feature priority."""
    mapping = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
        "info": "low",
    }
    return mapping.get(severity.lower(), "medium")


def _estimate_effort(severity: str) -> float:
    """Estimate effort in hours based on severity."""
    estimates = {
        "critical": 8.0,
        "high": 4.0,
        "medium": 2.0,
        "low": 1.0,
        "info": 0.5,
    }
    return estimates.get(severity.lower(), 2.0)


async def human_gate_optimizer(
    learning_store: LearningStore,
    config: FeedbackConfig | None = None,
) -> dict[str, Any]:
    """Optimize human review thresholds based on outcomes.

    Analyzes past decisions and their outcomes to recommend
    adjusted confidence thresholds for human review gates.

    The goal is to minimize unnecessary human reviews while
    maintaining quality gates for truly risky operations.

    Args:
        learning_store: Learning store with decision history
        config: Feedback configuration

    Returns:
        Dict with optimization recommendations
    """
    config = config or FeedbackConfig()
    result = {
        "optimized": False,
        "current_thresholds": {},
        "recommended_thresholds": {},
        "analysis": {},
    }

    if not config.threshold_optimization_enabled:
        result["skipped"] = "optimization_disabled"
        return result

    try:
        stats = learning_store.get_statistics()
        total_decisions = stats.get("total_decisions", 0)

        if total_decisions < config.min_decisions_for_optimization:
            result["skipped"] = "insufficient_data"
            result["decisions_needed"] = config.min_decisions_for_optimization - total_decisions
            return result

        # Analyze decisions by action
        decisions_by_action = stats.get("decisions_by_action", {})
        success_rate = stats.get("decision_success_rate", 0)

        # Calculate human review rate
        human_reviews = decisions_by_action.get("human_review_required", 0)
        human_review_rate = human_reviews / total_decisions if total_decisions > 0 else 0

        result["analysis"] = {
            "total_decisions": total_decisions,
            "success_rate": success_rate,
            "human_review_rate": human_review_rate,
            "decisions_by_action": decisions_by_action,
        }

        # Recommend threshold adjustments
        # If success rate is high and human review rate is high,
        # we can raise the threshold (fewer human reviews)
        if success_rate > 0.9 and human_review_rate > 0.3:
            result["recommended_thresholds"]["risk_threshold"] = 0.7  # Up from 0.6
            result["recommended_thresholds"]["confidence_threshold"] = 0.5  # Down from 0.6
            result["recommendation"] = "raise_thresholds"
        elif success_rate < 0.7:
            result["recommended_thresholds"]["risk_threshold"] = 0.5  # Down from 0.6
            result["recommended_thresholds"]["confidence_threshold"] = 0.7  # Up from 0.6
            result["recommendation"] = "lower_thresholds"
        else:
            result["recommendation"] = "maintain_thresholds"

        result["optimized"] = True
        logger.info(
            f"Human gate optimizer: {result['recommendation']}",
            extra=result["analysis"],
        )

    except Exception as e:
        logger.error(f"Human gate optimization error: {e}")
        result["error"] = str(e)

    return result


class FeedbackLoopManager:
    """Manages feedback loops for continuous learning.

    This class coordinates all feedback mechanisms:
    - Post-session indexing
    - Tech debt feature generation
    - Threshold optimization
    """

    def __init__(
        self,
        learning_store: LearningStore,
        code_atlas: CodeAtlasBridge | None = None,
        tech_diligence: TechDiligenceBridge | None = None,
        config: FeedbackConfig | None = None,
    ):
        """Initialize feedback loop manager.

        Args:
            learning_store: Learning store for patterns/decisions
            code_atlas: Optional Code Atlas bridge
            tech_diligence: Optional TechDiligence bridge
            config: Feedback configuration
        """
        self.learning_store = learning_store
        self.code_atlas = code_atlas
        self.tech_diligence = tech_diligence
        self.config = config or FeedbackConfig()
        self._last_optimization: datetime | None = None
        self._last_debt_scan: datetime | None = None

    async def on_session_complete(self, session: SessionSummary) -> dict[str, Any]:
        """Handle session completion.

        Args:
            session: Completed session summary

        Returns:
            Combined results from all hooks
        """
        results = {
            "session_id": session.session_id,
            "hooks": {},
        }

        # Run post-session hook
        results["hooks"]["post_session"] = await post_session_hook(
            session=session,
            code_atlas=self.code_atlas,
            learning_store=self.learning_store,
            config=self.config,
        )

        # Check if optimization is due
        if self._should_optimize():
            results["hooks"]["optimization"] = await human_gate_optimizer(
                learning_store=self.learning_store,
                config=self.config,
            )
            self._last_optimization = datetime.now(UTC)

        return results

    async def scan_for_debt(
        self, domain: str, project: str, max_features: int = 10
    ) -> list[DebtFeature]:
        """Scan for tech debt and generate features.

        Args:
            domain: Domain name
            project: Project name
            max_features: Maximum features to generate

        Returns:
            List of generated debt features
        """
        if not self._should_scan_debt():
            logger.info("Debt scan not due yet")
            return []

        features = await generate_debt_features(
            domain=domain,
            project=project,
            tech_diligence=self.tech_diligence,
            config=self.config,
            max_features=max_features,
        )

        self._last_debt_scan = datetime.now(UTC)
        return features

    def _should_optimize(self) -> bool:
        """Check if threshold optimization is due."""
        if not self._last_optimization:
            return True

        elapsed = datetime.now(UTC) - self._last_optimization
        return elapsed.total_seconds() / 3600 >= self.config.optimization_interval_hours

    def _should_scan_debt(self) -> bool:
        """Check if debt scan is due."""
        if not self._last_debt_scan:
            return True

        elapsed = datetime.now(UTC) - self._last_debt_scan
        return elapsed.total_seconds() / 3600 >= self.config.debt_scan_interval_hours
