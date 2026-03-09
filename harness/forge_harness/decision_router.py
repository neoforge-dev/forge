"""
Decision Router for FORGE Approval Tier Classification
=======================================================

Classifies approval requests into decision tiers based on:
- Approval type (security, deploy, content, etc.)
- Priority level
- Risk indicators in metadata
- Domain-specific rules

Tiers:
- WATCH: Binary approve/reject, minimal context (Apple Watch)
- PHONE: Summary context needed (iPhone/Android)
- DESKTOP: Full review required (laptop/desktop)

Usage:
    from forge_harness.decision_router import DecisionRouter, classify_decision_tier

    router = DecisionRouter()
    tier = router.classify(approval_request)

    # Or use the convenience function
    tier = classify_decision_tier(approval_request)
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .logging_config import get_logger
from .meta_learning.schemas import DecisionTier

if TYPE_CHECKING:
    from .approval_queue import ApprovalRequest

logger = get_logger(__name__)


# =============================================================================
# Risk Indicators
# =============================================================================

# Keywords in title/description that indicate higher risk
HIGH_RISK_KEYWORDS = frozenset(
    [
        "security",
        "auth",
        "authentication",
        "authorization",
        "payment",
        "billing",
        "stripe",
        "subscription",
        "production",
        "prod",
        "deploy",
        "migration",
        "database",
        "schema",
        "breaking",
        "api change",
        "delete",
        "remove",
        "drop",
        "sensitive",
        "credential",
        "secret",
        "key",
        "token",
        "hipaa",
        "coppa",
        "gdpr",
        "compliance",
    ]
)

LOW_RISK_KEYWORDS = frozenset(
    [
        "test",
        "lint",
        "format",
        "typo",
        "docs",
        "readme",
        "comment",
        "style",
        "refactor",
        "retry",
        "fix",
        "minor",
        "patch",
    ]
)

# Approval types that always require desktop review
DESKTOP_TYPES = frozenset(
    [
        "security",
        "compliance",
        "deploy",
    ]
)

# Approval types suitable for watch-level decisions
WATCH_TYPES = frozenset(
    [
        "content",  # Simple content approvals
    ]
)


@dataclass
class TierClassification:
    """Result of tier classification with explanation."""

    tier: DecisionTier
    reason: str
    risk_score: float  # 0.0 = low risk, 1.0 = high risk
    keywords_matched: list[str]


class DecisionRouter:
    """Routes approval requests to appropriate decision tiers."""

    def __init__(
        self,
        high_risk_keywords: frozenset[str] | None = None,
        low_risk_keywords: frozenset[str] | None = None,
    ):
        """Initialize the decision router.

        Args:
            high_risk_keywords: Keywords indicating high-risk decisions
            low_risk_keywords: Keywords indicating low-risk decisions
        """
        self.high_risk_keywords = high_risk_keywords or HIGH_RISK_KEYWORDS
        self.low_risk_keywords = low_risk_keywords or LOW_RISK_KEYWORDS

    def classify(self, request: "ApprovalRequest") -> TierClassification:
        """Classify an approval request into a decision tier.

        Args:
            request: The approval request to classify

        Returns:
            TierClassification with tier, reason, and risk score
        """
        # Collect text to analyze
        text_to_analyze = f"{request.title} {request.description}".lower()

        # Check for keyword matches
        high_matches = [kw for kw in self.high_risk_keywords if kw in text_to_analyze]
        low_matches = [kw for kw in self.low_risk_keywords if kw in text_to_analyze]

        # Calculate base risk score
        risk_score = self._calculate_risk_score(request, high_matches, low_matches)

        # Determine tier based on rules
        tier, reason = self._determine_tier(request, high_matches, low_matches, risk_score)

        classification = TierClassification(
            tier=tier,
            reason=reason,
            risk_score=risk_score,
            keywords_matched=high_matches + low_matches,
        )

        logger.debug(
            f"Classified {request.id} as {tier.value}",
            extra={
                "request_id": request.id,
                "tier": tier.value,
                "risk_score": risk_score,
                "reason": reason,
            },
        )

        return classification

    def _calculate_risk_score(
        self,
        request: "ApprovalRequest",
        high_matches: list[str],
        low_matches: list[str],
    ) -> float:
        """Calculate risk score from 0.0 (low) to 1.0 (high)."""
        score = 0.5  # Start at medium

        # Priority adjustments
        priority_scores = {
            "critical": 0.3,
            "high": 0.2,
            "normal": 0.0,
            "low": -0.2,
        }
        score += priority_scores.get(request.priority.value, 0.0)

        # Keyword adjustments
        score += len(high_matches) * 0.1
        score -= len(low_matches) * 0.1

        # Type adjustments
        if request.type.value in DESKTOP_TYPES:
            score += 0.3
        elif request.type.value in WATCH_TYPES:
            score -= 0.2

        # Clamp to [0.0, 1.0]
        return max(0.0, min(1.0, score))

    def _determine_tier(
        self,
        request: "ApprovalRequest",
        high_matches: list[str],
        low_matches: list[str],
        risk_score: float,
    ) -> tuple[DecisionTier, str]:
        """Determine the appropriate tier based on analysis."""

        # Rule 1: Security and compliance always go to desktop
        if request.type.value in ("security", "compliance"):
            return DecisionTier.DESKTOP, f"Type '{request.type.value}' requires full review"

        # Rule 2: Critical priority always goes to desktop
        if request.priority.value == "critical":
            return DecisionTier.DESKTOP, "Critical priority requires full review"

        # Rule 3: Deploy type goes to desktop
        if request.type.value == "deploy":
            return DecisionTier.DESKTOP, "Deployment requires full review"

        # Rule 4: High risk score goes to desktop
        if risk_score >= 0.7:
            keywords_str = ", ".join(high_matches[:3]) if high_matches else "various factors"
            return (
                DecisionTier.DESKTOP,
                f"High risk score ({risk_score:.2f}) due to: {keywords_str}",
            )

        # Rule 5: Very low risk goes to watch
        if risk_score <= 0.3:
            if request.priority.value == "low" and low_matches:
                return (
                    DecisionTier.WATCH,
                    f"Low risk ({risk_score:.2f}): {', '.join(low_matches[:2])}",
                )
            if request.type.value == "content" and request.priority.value in ("low", "normal"):
                return DecisionTier.WATCH, "Routine content approval"

        # Rule 6: Check for specific watch-level patterns
        if self._is_watch_pattern(request):
            return DecisionTier.WATCH, "Matches low-risk pattern"

        # Default: Phone tier for moderate decisions
        return DecisionTier.PHONE, f"Standard review (risk: {risk_score:.2f})"

    def _is_watch_pattern(self, request: "ApprovalRequest") -> bool:
        """Check if request matches watch-level patterns."""
        title_lower = request.title.lower()

        # Test retry patterns
        if "retry" in title_lower and request.type.value == "feature":
            return True

        # Lint/format fixes
        if any(kw in title_lower for kw in ("lint", "format", "typo")):
            return True

        # Documentation only
        if "docs" in title_lower and request.type.value == "content":
            return True

        return False


# =============================================================================
# Convenience Functions
# =============================================================================

# Module-level router instance
_router: DecisionRouter | None = None


def get_router() -> DecisionRouter:
    """Get or create the module-level router instance."""
    global _router
    if _router is None:
        _router = DecisionRouter()
    return _router


def classify_decision_tier(request: "ApprovalRequest") -> DecisionTier:
    """Classify an approval request into a decision tier.

    Convenience function using the module-level router.

    Args:
        request: The approval request to classify

    Returns:
        DecisionTier (WATCH, PHONE, or DESKTOP)
    """
    return get_router().classify(request).tier


def get_tier_classification(request: "ApprovalRequest") -> TierClassification:
    """Get full tier classification with explanation.

    Args:
        request: The approval request to classify

    Returns:
        TierClassification with tier, reason, risk score, and matched keywords
    """
    return get_router().classify(request)
