"""
Decision Engine for the FORGE Meta-Learning System.

The central intelligence that aggregates signals from Code Atlas and Tech Diligence,
applies learned patterns, and generates recommendations for autonomous operations.
"""

import logging
import uuid

# Type hint for tracker to avoid circular import
from typing import TYPE_CHECKING, Optional

from forge_harness.meta_learning.bridges.code_atlas import CodeAtlasBridge
from forge_harness.meta_learning.bridges.tech_diligence import TechDiligenceBridge
from forge_harness.meta_learning.config import DecisionEngineConfig, MetaLearningConfig
from forge_harness.meta_learning.learning_store import LearningStore
from forge_harness.meta_learning.schemas import (
    AtlasSignals,
    ConfidenceLevel,
    DecisionAction,
    DecisionContext,
    DecisionOutcome,
    DecisionRecommendation,
    DecisionRecord,
    DecisionTier,
    DiligenceSignals,
    ScoreCard,
    Warning,
)

if TYPE_CHECKING:
    from forge_harness.posthog_tracker import HarnessTracker

logger = logging.getLogger(__name__)


class DecisionEngine:
    """
    Central decision-making engine for autonomous development.

    Aggregates signals from Code Atlas and Tech Diligence, applies learned patterns,
    and generates recommendations. Works with any subset of bridges (graceful degradation).

    Usage:
        # Create with all bridges
        engine = DecisionEngine(
            atlas_bridge=atlas,
            diligence_bridge=diligence,
            learning_store=store,
            config=config,
        )

        # Create decision context
        context = DecisionContext(
            domain="codeswiftr-com",
            project="interview-simulator",
            feature_id="IS-042",
            file_paths=["src/auth.py"],
            tags=["security"],
        )

        # Get recommendation
        recommendation = await engine.get_recommendation(context)

        # Later, record outcome
        await engine.record_outcome(recommendation.decision_id, success=True)
    """

    def __init__(
        self,
        atlas_bridge: CodeAtlasBridge | None = None,
        diligence_bridge: TechDiligenceBridge | None = None,
        learning_store: LearningStore | None = None,
        config: DecisionEngineConfig | None = None,
        tracker: Optional["HarnessTracker"] = None,
    ):
        self.atlas_bridge = atlas_bridge
        self.diligence_bridge = diligence_bridge
        self.learning_store = learning_store
        self.config = config or DecisionEngineConfig()
        self.tracker = tracker

        self._decision_cache: dict[str, DecisionRecord] = {}

    @classmethod
    def from_config(
        cls,
        config: MetaLearningConfig,
        atlas_bridge: CodeAtlasBridge | None = None,
        diligence_bridge: TechDiligenceBridge | None = None,
        learning_store: LearningStore | None = None,
    ) -> "DecisionEngine":
        """Create a DecisionEngine from configuration."""
        return cls(
            atlas_bridge=atlas_bridge,
            diligence_bridge=diligence_bridge,
            learning_store=learning_store,
            config=config.decision_engine,
        )

    # =========================================================================
    # Signal Gathering
    # =========================================================================

    async def _get_atlas_signals(
        self,
        context: DecisionContext,
    ) -> AtlasSignals:
        """Get signals from Code Atlas, or empty signals if unavailable."""
        if not self.atlas_bridge:
            return AtlasSignals()

        return await self.atlas_bridge.get_signals(
            domain=context.domain,
            project=context.project,
            file_paths=context.file_paths,
            tags=context.tags,
        )

    async def _get_diligence_signals(
        self,
        context: DecisionContext,
    ) -> DiligenceSignals:
        """Get signals from Tech Diligence, or empty signals if unavailable."""
        if not self.diligence_bridge:
            return DiligenceSignals()

        return await self.diligence_bridge.get_signals(
            domain=context.domain,
            project=context.project,
        )

    # =========================================================================
    # Score Calculation
    # =========================================================================

    def _calculate_risk_score(
        self,
        atlas: AtlasSignals,
        diligence: DiligenceSignals,
        context: DecisionContext,
    ) -> float:
        """
        Calculate risk score (0.0 = no risk, 1.0 = maximum risk).

        Factors:
        - Tech Diligence findings (critical/high issues)
        - Recurring problems from Code Atlas
        - File sensitivity (security/auth tags increase risk)
        """
        risk = 0.0

        # Diligence findings
        if diligence.risk_level == "critical":
            risk += 0.6
        elif diligence.risk_level == "high":
            risk += 0.4
        elif diligence.risk_level == "medium":
            risk += 0.2

        # Blocking issues add risk
        risk += min(0.3, len(diligence.blocking_issues) * 0.1)

        # Recurring problems add risk
        if atlas.recurring_problems:
            risk += min(0.2, len(atlas.recurring_problems) * 0.05)

        # Sensitive tags increase risk
        sensitive_tags = {"security", "auth", "payment", "pii", "credential"}
        matching_tags = set(context.tags) & sensitive_tags
        risk += min(0.2, len(matching_tags) * 0.1)

        return min(1.0, risk)

    def _calculate_confidence_score(
        self,
        atlas: AtlasSignals,
        diligence: DiligenceSignals,
        context: DecisionContext,
    ) -> float:
        """
        Calculate confidence score (0.0 = unknown, 1.0 = high confidence).

        Factors:
        - Number of related patterns from Code Atlas
        - Similar past decisions
        - Precedent in learning store
        """
        confidence = 0.3  # Base confidence

        # Related patterns increase confidence
        if atlas.related_patterns:
            avg_pattern_confidence = sum(p.confidence for p in atlas.related_patterns) / len(
                atlas.related_patterns
            )
            confidence += avg_pattern_confidence * 0.3

        # Similar decisions increase confidence
        if atlas.similar_decisions:
            confidence += min(0.3, len(atlas.similar_decisions) * 0.1)

        # Check learning store for precedent (closes confidence feedback loop)
        if self.learning_store:
            ctx_signature = LearningStore.compute_context_signature(
                domain=context.domain,
                project=context.project,
                file_paths=context.file_paths,
                tags=context.tags,
            )

            # Weight confidence by decision success rate
            past_decisions = self.learning_store.get_decisions_by_context(
                ctx_signature,
                with_outcome_only=True,
            )
            if past_decisions:
                # Calculate success rate and use it to weight confidence
                successful = sum(1 for d in past_decisions if d.outcome_success)
                decision_success_rate = successful / len(past_decisions)

                # High success rate boosts confidence, low rate reduces it
                # Range: -0.15 (0% success) to +0.25 (100% success)
                confidence_adjustment = (decision_success_rate - 0.5) * 0.4
                confidence += confidence_adjustment

                # Also add small boost for having precedent at all
                confidence += min(0.1, len(past_decisions) * 0.02)

            # Weight confidence by pattern success rate
            patterns = self.learning_store.get_patterns_by_context(
                ctx_signature, min_effectiveness=0.0
            )
            if patterns:
                total_apps = sum(p.total_applications for p in patterns)
                successful_apps = sum(p.successful_applications for p in patterns)
                if total_apps > 0:
                    pattern_success_rate = successful_apps / total_apps
                    # High pattern success boosts confidence, low rate reduces it
                    # Range: -0.1 (0% success) to +0.15 (100% success)
                    pattern_adjustment = (pattern_success_rate - 0.5) * 0.25
                    confidence += pattern_adjustment

        return min(1.0, max(0.0, confidence))  # Clamp to [0, 1]

    def _calculate_complexity_score(
        self,
        context: DecisionContext,
        atlas: AtlasSignals,
    ) -> float:
        """
        Calculate complexity score (0.0 = simple, 1.0 = very complex).

        Factors:
        - Number of files being modified
        - Presence of complex tags
        - Lack of related patterns (unknown territory)
        """
        complexity = 0.0

        # More files = more complex
        complexity += min(0.4, len(context.file_paths) * 0.1)

        # Complex tags
        complex_tags = {"refactor", "migration", "breaking-change", "architecture"}
        matching_tags = set(context.tags) & complex_tags
        complexity += min(0.3, len(matching_tags) * 0.15)

        # No related patterns = unknown territory = higher complexity
        if not atlas.related_patterns:
            complexity += 0.2

        return min(1.0, complexity)

    def _calculate_precedent_score(
        self,
        atlas: AtlasSignals,
        context: DecisionContext,
    ) -> float:
        """
        Calculate precedent score (0.0 = no precedent, 1.0 = strong precedent).

        Based on similar past decisions and patterns.
        """
        precedent = 0.0

        # Related patterns
        if atlas.related_patterns:
            effective_patterns = [p for p in atlas.related_patterns if p.confidence >= 0.5]
            precedent += min(0.5, len(effective_patterns) * 0.15)

        # Similar decisions
        if atlas.similar_decisions:
            precedent += min(0.3, len(atlas.similar_decisions) * 0.1)

        # Check learning store
        if self.learning_store:
            ctx_signature = LearningStore.compute_context_signature(
                domain=context.domain,
                project=context.project,
                file_paths=context.file_paths,
                tags=context.tags,
            )
            past_patterns = self.learning_store.get_patterns_by_context(
                ctx_signature,
                min_effectiveness=0.6,
            )
            precedent += min(0.2, len(past_patterns) * 0.1)

        return min(1.0, precedent)

    def _calculate_scores(
        self,
        atlas: AtlasSignals,
        diligence: DiligenceSignals,
        context: DecisionContext,
    ) -> ScoreCard:
        """Calculate all scores and return a ScoreCard."""
        return ScoreCard(
            risk_score=self._calculate_risk_score(atlas, diligence, context),
            confidence_score=self._calculate_confidence_score(atlas, diligence, context),
            complexity_score=self._calculate_complexity_score(context, atlas),
            precedent_score=self._calculate_precedent_score(atlas, context),
        )

    # =========================================================================
    # Recommendation Generation
    # =========================================================================

    def _determine_action(
        self,
        scores: ScoreCard,
        diligence: DiligenceSignals,
        context: "DecisionContext | None" = None,
    ) -> tuple[DecisionAction, str | None]:
        """Determine the recommended action based on scores.

        Returns:
            Tuple of (action, pattern_block_reason) where pattern_block_reason
            is set if the action is BLOCK due to low pattern success rate.
        """
        aggregate = scores.aggregate_score

        # Critical blocking issues always require human review
        if diligence.risk_level == "critical":
            return DecisionAction.BLOCK, None

        # High-risk issues with blocking findings require review
        if diligence.blocking_issues:
            return DecisionAction.HUMAN_REVIEW_REQUIRED, None

        # Check pattern success rate for context-based blocking (closes feedback loop)
        if context and self.learning_store:
            pattern_block_reason = self._check_pattern_success_rate(context)
            if pattern_block_reason:
                return DecisionAction.BLOCK, pattern_block_reason

        # Score-based thresholds
        if aggregate < self.config.block_threshold:
            return DecisionAction.BLOCK, None
        if aggregate < self.config.human_review_threshold:
            return DecisionAction.HUMAN_REVIEW_REQUIRED, None
        if aggregate < self.config.caution_threshold:
            return DecisionAction.PROCEED_WITH_CAUTION, None

        # Check confidence requirement
        if scores.confidence_score < self.config.min_confidence_for_proceed:
            return DecisionAction.PROCEED_WITH_CAUTION, None

        return DecisionAction.PROCEED, None

    def _check_pattern_success_rate(self, context: "DecisionContext") -> str | None:
        """Check if patterns for this context have low success rate.

        Returns:
            Blocking reason if success rate < 30%, None otherwise.
        """
        if not self.learning_store:
            return None

        ctx_signature = LearningStore.compute_context_signature(
            domain=context.domain,
            project=context.project,
            file_paths=context.file_paths,
            tags=context.tags,
        )

        # Get all patterns for this context (no effectiveness filter)
        patterns = self.learning_store.get_patterns_by_context(ctx_signature, min_effectiveness=0.0)

        if not patterns:
            return None

        # Calculate aggregate success rate across patterns
        total_applications = sum(p.total_applications for p in patterns)
        successful_applications = sum(p.successful_applications for p in patterns)

        # Need sufficient data (at least 3 applications) to make a blocking decision
        if total_applications < 3:
            return None

        success_rate = successful_applications / total_applications

        if success_rate < 0.3:
            return (
                f"Pattern history shows {success_rate:.0%} success rate "
                f"({successful_applications}/{total_applications} successes). "
                f"Blocking to prevent repeated failures in similar contexts."
            )

        return None

    def _determine_confidence_level(self, scores: ScoreCard) -> ConfidenceLevel:
        """Determine confidence level from score."""
        if scores.confidence_score >= 0.8:
            return ConfidenceLevel.HIGH
        if scores.confidence_score >= 0.5:
            return ConfidenceLevel.MEDIUM
        if scores.confidence_score >= 0.2:
            return ConfidenceLevel.LOW
        return ConfidenceLevel.UNKNOWN

    def classify_tier(
        self,
        action: DecisionAction,
        scores: ScoreCard,
        context: DecisionContext,
        diligence: DiligenceSignals,
    ) -> DecisionTier:
        """
        Classify decision into tier for notification format.

        Tier classification determines how the decision is presented:
        - WATCH: Minimal context, binary approve/reject (Apple Watch friendly)
        - PHONE: Summary context, can decide on mobile
        - DESKTOP: Full context required, needs detailed review

        Classification factors:
        - Risk level (high risk → DESKTOP)
        - Decision action (BLOCK → DESKTOP)
        - Sensitive tags (security, payment → DESKTOP)
        - Complexity score (high complexity → PHONE/DESKTOP)
        - Precedent (high precedent + low risk → WATCH)
        """
        # DESKTOP: Any blocked or high-risk decision
        if action == DecisionAction.BLOCK:
            return DecisionTier.DESKTOP

        if diligence.risk_level in ("critical", "high"):
            return DecisionTier.DESKTOP

        if scores.risk_score > 0.6:
            return DecisionTier.DESKTOP

        # DESKTOP: Sensitive tags require full review
        sensitive_tags = {
            "security",
            "auth",
            "payment",
            "pii",
            "credential",
            "deploy",
            "production",
        }
        if set(context.tags) & sensitive_tags:
            return DecisionTier.DESKTOP

        # DESKTOP: Complex changes (many files, breaking changes)
        complex_tags = {"breaking-change", "migration", "architecture"}
        if set(context.tags) & complex_tags:
            return DecisionTier.DESKTOP

        if scores.complexity_score > 0.7:
            return DecisionTier.DESKTOP

        # WATCH: Low-risk, high-precedent, simple decisions
        watch_eligible_tags = {
            "test",
            "lint",
            "format",
            "deps",
            "docs",
            "typo",
            "retry",
            "rerun",
            "minor",
            "patch",
        }
        if (
            action == DecisionAction.PROCEED_WITH_CAUTION
            and scores.risk_score < 0.3
            and scores.precedent_score > 0.5
            and set(context.tags) & watch_eligible_tags
        ):
            return DecisionTier.WATCH

        # WATCH: Proceed decisions with high confidence and low risk
        if (
            action == DecisionAction.PROCEED
            and scores.confidence_score > 0.7
            and scores.risk_score < 0.2
        ):
            return DecisionTier.WATCH

        # PHONE: Everything else requiring human review
        # Middle ground: needs context but manageable on mobile
        return DecisionTier.PHONE

    def _generate_warnings(
        self,
        scores: ScoreCard,
        atlas: AtlasSignals,
        diligence: DiligenceSignals,
    ) -> list[Warning]:
        """Generate warnings based on signals."""
        warnings = []

        # High risk warning
        if scores.risk_score > 0.6:
            warnings.append(
                Warning(
                    code="HIGH_RISK",
                    message=f"Risk score is high ({scores.risk_score:.2f})",
                    severity="high",
                )
            )

        # Low confidence warning
        if scores.confidence_score < 0.4:
            warnings.append(
                Warning(
                    code="LOW_CONFIDENCE",
                    message="Limited precedent for this type of change",
                    severity="medium",
                )
            )

        # High complexity warning
        if scores.complexity_score > 0.7:
            warnings.append(
                Warning(
                    code="HIGH_COMPLEXITY",
                    message="This change involves multiple files and complex patterns",
                    severity="medium",
                )
            )

        # Recurring problems warning
        if atlas.recurring_problems:
            warnings.append(
                Warning(
                    code="RECURRING_PROBLEMS",
                    message=f"{len(atlas.recurring_problems)} known issues in related code",
                    severity="medium",
                )
            )

        # Diligence warnings
        for finding in diligence.warnings[:3]:  # Limit to top 3
            warnings.append(
                Warning(
                    code=f"DILIGENCE_{finding.category.value.upper()}",
                    message=finding.title,
                    severity="low" if finding.severity.value == "low" else "medium",
                )
            )

        return warnings

    def _generate_reasoning(
        self,
        action: DecisionAction,
        scores: ScoreCard,
        atlas: AtlasSignals,
        diligence: DiligenceSignals,
        pattern_block_reason: str | None = None,
    ) -> str:
        """Generate human-readable reasoning for the recommendation."""
        reasons = []

        if action == DecisionAction.PROCEED:
            reasons.append("All signals indicate this change is safe to proceed.")
            if atlas.related_patterns:
                reasons.append(
                    f"Found {len(atlas.related_patterns)} successful patterns from past sessions."
                )

        elif action == DecisionAction.PROCEED_WITH_CAUTION:
            if scores.confidence_score < self.config.min_confidence_for_proceed:
                reasons.append("Proceeding with caution due to limited precedent.")
            else:
                reasons.append("Minor concerns detected but change can proceed.")

        elif action == DecisionAction.HUMAN_REVIEW_REQUIRED:
            if diligence.blocking_issues:
                reasons.append(
                    f"{len(diligence.blocking_issues)} blocking issues require human review."
                )
            if scores.risk_score > 0.5:
                reasons.append("High risk score requires human oversight.")

        elif action == DecisionAction.BLOCK:
            # Pattern-based blocking takes precedence in reasoning
            if pattern_block_reason:
                reasons.append(pattern_block_reason)
            elif diligence.risk_level == "critical":
                reasons.append("Critical security/quality issues detected.")
            else:
                reasons.append("Aggregate score below blocking threshold.")

        # Add score summary
        reasons.append(
            f"Scores: risk={scores.risk_score:.2f}, confidence={scores.confidence_score:.2f}, "
            f"complexity={scores.complexity_score:.2f}, precedent={scores.precedent_score:.2f}"
        )

        return " ".join(reasons)

    # =========================================================================
    # Main API
    # =========================================================================

    async def get_recommendation(
        self,
        context: DecisionContext,
    ) -> DecisionRecommendation:
        """
        Get a recommendation for a decision context.

        Gathers signals from all available bridges, calculates scores,
        and generates a recommendation with reasoning.
        """
        decision_id = str(uuid.uuid4())

        # Gather signals (graceful degradation if bridges unavailable)
        atlas = await self._get_atlas_signals(context)
        diligence = await self._get_diligence_signals(context)

        # Calculate scores
        scores = self._calculate_scores(atlas, diligence, context)

        # Determine action (includes pattern-based blocking)
        action, pattern_block_reason = self._determine_action(scores, diligence, context)
        confidence = self._determine_confidence_level(scores)

        # Generate warnings and reasoning
        warnings = self._generate_warnings(scores, atlas, diligence)
        reasoning = self._generate_reasoning(action, scores, atlas, diligence, pattern_block_reason)

        recommendation = DecisionRecommendation(
            action=action,
            confidence=confidence,
            scores=scores,
            reasoning=reasoning,
            warnings=warnings,
            suggested_human_reviewers=[],  # Could be populated from team config
        )

        # Record decision for learning
        if self.config.enable_decision_recording and self.learning_store:
            ctx_signature = LearningStore.compute_context_signature(
                domain=context.domain,
                project=context.project,
                file_paths=context.file_paths,
                tags=context.tags,
            )
            record = DecisionRecord(
                decision_id=decision_id,
                context_signature=ctx_signature,
                domain=context.domain,
                project=context.project,
                recommended_action=action,
            )
            self.learning_store.record_decision(record)
            self._decision_cache[decision_id] = record

            logger.info(
                "Decision %s: %s (confidence=%s, aggregate=%.2f)",
                decision_id[:8],
                action.value,
                confidence.value,
                scores.aggregate_score,
            )

        # Track decision in PostHog
        if self.tracker:
            self.tracker.decision_made(
                decision_id=decision_id,
                action=action.value,
                confidence=scores.aggregate_score,
                feature_id=context.feature_id or "",
                reasoning=reasoning,
            )

        return recommendation

    async def record_outcome(
        self,
        decision_id: str,
        success: bool,
        actual_action: DecisionAction | None = None,
    ) -> DecisionOutcome | None:
        """
        Record the outcome of a decision for learning.

        Call this after a decision has been acted upon to improve future recommendations.
        """
        if not self.learning_store:
            return None

        # Try to find the decision record
        record = self._decision_cache.get(decision_id)
        if not record:
            record = self.learning_store.get_decision(decision_id)

        if not record:
            logger.warning("Decision %s not found", decision_id)
            return None

        # Update the record
        updated = self.learning_store.record_outcome(
            decision_id=decision_id,
            success=success,
            actual_action=actual_action,
        )

        if updated:
            logger.info(
                "Outcome recorded for %s: success=%s",
                decision_id[:8],
                success,
            )

        return DecisionOutcome(
            decision_id=decision_id,
            success=success,
            actual_action=actual_action or record.recommended_action,
            outcome_description=None,
        )

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_statistics(self) -> dict:
        """Get decision engine statistics."""
        stats = {
            "has_atlas_bridge": self.atlas_bridge is not None,
            "has_diligence_bridge": self.diligence_bridge is not None,
            "has_learning_store": self.learning_store is not None,
            "cached_decisions": len(self._decision_cache),
        }

        if self.learning_store:
            store_stats = self.learning_store.get_statistics()
            stats.update(
                {
                    "total_decisions_recorded": store_stats["total_decisions"],
                    "decision_success_rate": store_stats["decision_success_rate"],
                }
            )

        return stats
