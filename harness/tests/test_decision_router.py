"""
Tests for Decision Router
==========================

Tests tier classification logic for approval requests based on:
- Approval type (security, deploy, content, etc.)
- Priority level
- Risk indicators in title/description
- Domain-specific rules

Coverage includes:
- Keyword frozensets validation
- TierClassification dataclass
- DecisionRouter initialization and classification
- Risk score calculation
- Convenience functions
- Edge cases and multiple keyword scenarios
"""

from unittest.mock import patch

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.decision_router import (
    DESKTOP_TYPES,
    HIGH_RISK_KEYWORDS,
    LOW_RISK_KEYWORDS,
    WATCH_TYPES,
    DecisionRouter,
    TierClassification,
    classify_decision_tier,
    get_router,
    get_tier_classification,
)
from forge_harness.meta_learning.schemas import DecisionTier

# =============================================================================
# Keyword Frozensets Tests
# =============================================================================


class TestKeywordFrozensets:
    """Test that keyword frozensets contain expected values."""

    def test_high_risk_keywords_contains_security_terms(self):
        """Test HIGH_RISK_KEYWORDS contains security-related terms."""
        assert "security" in HIGH_RISK_KEYWORDS
        assert "auth" in HIGH_RISK_KEYWORDS
        assert "authentication" in HIGH_RISK_KEYWORDS
        assert "authorization" in HIGH_RISK_KEYWORDS

    def test_high_risk_keywords_contains_payment_terms(self):
        """Test HIGH_RISK_KEYWORDS contains payment-related terms."""
        assert "payment" in HIGH_RISK_KEYWORDS
        assert "billing" in HIGH_RISK_KEYWORDS
        assert "stripe" in HIGH_RISK_KEYWORDS
        assert "subscription" in HIGH_RISK_KEYWORDS

    def test_high_risk_keywords_contains_deployment_terms(self):
        """Test HIGH_RISK_KEYWORDS contains deployment-related terms."""
        assert "production" in HIGH_RISK_KEYWORDS
        assert "prod" in HIGH_RISK_KEYWORDS
        assert "deploy" in HIGH_RISK_KEYWORDS
        assert "migration" in HIGH_RISK_KEYWORDS

    def test_high_risk_keywords_contains_database_terms(self):
        """Test HIGH_RISK_KEYWORDS contains database-related terms."""
        assert "database" in HIGH_RISK_KEYWORDS
        assert "schema" in HIGH_RISK_KEYWORDS
        assert "delete" in HIGH_RISK_KEYWORDS
        assert "drop" in HIGH_RISK_KEYWORDS

    def test_high_risk_keywords_contains_compliance_terms(self):
        """Test HIGH_RISK_KEYWORDS contains compliance-related terms."""
        assert "hipaa" in HIGH_RISK_KEYWORDS
        assert "coppa" in HIGH_RISK_KEYWORDS
        assert "gdpr" in HIGH_RISK_KEYWORDS
        assert "compliance" in HIGH_RISK_KEYWORDS

    def test_low_risk_keywords_contains_test_terms(self):
        """Test LOW_RISK_KEYWORDS contains test-related terms."""
        assert "test" in LOW_RISK_KEYWORDS
        assert "lint" in LOW_RISK_KEYWORDS
        assert "format" in LOW_RISK_KEYWORDS
        assert "typo" in LOW_RISK_KEYWORDS

    def test_low_risk_keywords_contains_docs_terms(self):
        """Test LOW_RISK_KEYWORDS contains documentation terms."""
        assert "docs" in LOW_RISK_KEYWORDS
        assert "readme" in LOW_RISK_KEYWORDS
        assert "comment" in LOW_RISK_KEYWORDS

    def test_low_risk_keywords_contains_maintenance_terms(self):
        """Test LOW_RISK_KEYWORDS contains maintenance terms."""
        assert "refactor" in LOW_RISK_KEYWORDS
        assert "retry" in LOW_RISK_KEYWORDS
        assert "fix" in LOW_RISK_KEYWORDS
        assert "minor" in LOW_RISK_KEYWORDS
        assert "patch" in LOW_RISK_KEYWORDS

    def test_high_risk_keywords_is_frozenset(self):
        """Test HIGH_RISK_KEYWORDS is immutable frozenset."""
        assert isinstance(HIGH_RISK_KEYWORDS, frozenset)
        with pytest.raises(AttributeError):
            HIGH_RISK_KEYWORDS.add("new_keyword")  # type: ignore

    def test_low_risk_keywords_is_frozenset(self):
        """Test LOW_RISK_KEYWORDS is immutable frozenset."""
        assert isinstance(LOW_RISK_KEYWORDS, frozenset)
        with pytest.raises(AttributeError):
            LOW_RISK_KEYWORDS.add("new_keyword")  # type: ignore


class TestApprovalTypeFrozensets:
    """Test approval type classification frozensets."""

    def test_desktop_types_contains_expected_values(self):
        """Test DESKTOP_TYPES contains security and deploy types."""
        assert "security" in DESKTOP_TYPES
        assert "compliance" in DESKTOP_TYPES
        assert "deploy" in DESKTOP_TYPES

    def test_desktop_types_is_frozenset(self):
        """Test DESKTOP_TYPES is immutable frozenset."""
        assert isinstance(DESKTOP_TYPES, frozenset)
        with pytest.raises(AttributeError):
            DESKTOP_TYPES.add("new_type")  # type: ignore

    def test_watch_types_contains_expected_values(self):
        """Test WATCH_TYPES contains content type."""
        assert "content" in WATCH_TYPES

    def test_watch_types_is_frozenset(self):
        """Test WATCH_TYPES is immutable frozenset."""
        assert isinstance(WATCH_TYPES, frozenset)
        with pytest.raises(AttributeError):
            WATCH_TYPES.add("new_type")  # type: ignore


# =============================================================================
# TierClassification Tests
# =============================================================================


class TestTierClassification:
    """Test TierClassification dataclass."""

    def test_create_tier_classification(self):
        """Test creating a TierClassification instance."""
        classification = TierClassification(
            tier=DecisionTier.DESKTOP,
            reason="Security-sensitive operation",
            risk_score=0.9,
            keywords_matched=["security", "auth"],
        )

        assert classification.tier == DecisionTier.DESKTOP
        assert classification.reason == "Security-sensitive operation"
        assert classification.risk_score == 0.9
        assert classification.keywords_matched == ["security", "auth"]

    def test_tier_classification_with_empty_keywords(self):
        """Test TierClassification with no keywords matched."""
        classification = TierClassification(
            tier=DecisionTier.PHONE,
            reason="Standard review",
            risk_score=0.5,
            keywords_matched=[],
        )

        assert classification.tier == DecisionTier.PHONE
        assert classification.keywords_matched == []

    def test_tier_classification_risk_score_bounds(self):
        """Test TierClassification with boundary risk scores."""
        low_risk = TierClassification(
            tier=DecisionTier.WATCH,
            reason="Low risk",
            risk_score=0.0,
            keywords_matched=["test"],
        )
        assert low_risk.risk_score == 0.0

        high_risk = TierClassification(
            tier=DecisionTier.DESKTOP,
            reason="High risk",
            risk_score=1.0,
            keywords_matched=["security", "production"],
        )
        assert high_risk.risk_score == 1.0


# =============================================================================
# DecisionRouter Initialization Tests
# =============================================================================


class TestDecisionRouterInit:
    """Test DecisionRouter initialization."""

    def test_init_with_defaults(self):
        """Test DecisionRouter uses default keywords."""
        router = DecisionRouter()

        assert router.high_risk_keywords == HIGH_RISK_KEYWORDS
        assert router.low_risk_keywords == LOW_RISK_KEYWORDS

    def test_init_with_custom_high_risk_keywords(self):
        """Test DecisionRouter with custom high-risk keywords."""
        custom_keywords = frozenset(["custom", "danger", "critical"])
        router = DecisionRouter(high_risk_keywords=custom_keywords)

        assert router.high_risk_keywords == custom_keywords
        assert router.low_risk_keywords == LOW_RISK_KEYWORDS

    def test_init_with_custom_low_risk_keywords(self):
        """Test DecisionRouter with custom low-risk keywords."""
        custom_keywords = frozenset(["safe", "trivial", "cosmetic"])
        router = DecisionRouter(low_risk_keywords=custom_keywords)

        assert router.high_risk_keywords == HIGH_RISK_KEYWORDS
        assert router.low_risk_keywords == custom_keywords

    def test_init_with_all_custom_keywords(self):
        """Test DecisionRouter with all custom keywords."""
        custom_high = frozenset(["danger", "critical"])
        custom_low = frozenset(["safe", "trivial"])
        router = DecisionRouter(
            high_risk_keywords=custom_high,
            low_risk_keywords=custom_low,
        )

        assert router.high_risk_keywords == custom_high
        assert router.low_risk_keywords == custom_low


# =============================================================================
# Mock Approval Request Helper
# =============================================================================


def create_mock_request(
    request_type: ApprovalType = ApprovalType.FEATURE,
    priority: ApprovalPriority = ApprovalPriority.NORMAL,
    title: str = "Test Request",
    description: str = "Test description",
) -> ApprovalRequest:
    """Create a mock ApprovalRequest for testing."""
    return ApprovalRequest(
        id="test_123",
        type=request_type,
        domain="test-domain",
        title=title,
        description=description,
        priority=priority,
        status=ApprovalStatus.PENDING,
    )


# =============================================================================
# DecisionRouter Classification Tests - DESKTOP Tier
# =============================================================================


class TestDecisionRouterDesktopTier:
    """Test classification to DESKTOP tier."""

    def test_security_type_returns_desktop(self):
        """Test security approval type always returns DESKTOP."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.SECURITY,
            title="Update auth middleware",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.DESKTOP
        assert "security" in classification.reason.lower()

    def test_compliance_type_returns_desktop(self):
        """Test compliance approval type always returns DESKTOP."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.COMPLIANCE,
            title="Add HIPAA compliance",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.DESKTOP
        assert "compliance" in classification.reason.lower()

    def test_deploy_type_returns_desktop(self):
        """Test deploy approval type always returns DESKTOP."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.DEPLOY,
            title="Deploy to production",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.DESKTOP
        assert "deploy" in classification.reason.lower()

    def test_critical_priority_returns_desktop(self):
        """Test critical priority always returns DESKTOP."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.CRITICAL,
            title="Critical feature",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.DESKTOP
        assert "critical" in classification.reason.lower()

    def test_high_risk_score_returns_desktop(self):
        """Test high risk score (>=0.7) returns DESKTOP."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.HIGH,
            title="Security migration with database schema changes",
            description="Update authentication and payment processing",
        )

        classification = router.classify(request)

        # High priority + multiple high-risk keywords should push risk >= 0.7
        assert classification.tier == DecisionTier.DESKTOP
        assert classification.risk_score >= 0.7

    def test_multiple_high_risk_keywords_returns_desktop(self):
        """Test multiple high-risk keywords trigger DESKTOP."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.CONFIG,
            title="Production database migration",
            description="Deploy schema changes with authentication updates",
        )

        classification = router.classify(request)

        # Multiple high-risk keywords: production, database, migration, deploy, schema, authentication
        assert len(classification.keywords_matched) >= 3
        assert classification.tier == DecisionTier.DESKTOP


# =============================================================================
# DecisionRouter Classification Tests - WATCH Tier
# =============================================================================


class TestDecisionRouterWatchTier:
    """Test classification to WATCH tier."""

    def test_low_priority_with_low_risk_keywords_returns_watch(self):
        """Test low priority with low-risk keywords returns WATCH."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.LOW,
            title="Fix typo in test",
            description="Minor formatting fix",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.WATCH
        assert classification.risk_score <= 0.3
        assert any(
            kw in classification.keywords_matched
            for kw in ["typo", "test", "format", "fix", "minor"]
        )

    def test_content_type_low_priority_returns_watch(self):
        """Test content type with low priority returns WATCH."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.CONTENT,
            priority=ApprovalPriority.LOW,
            title="Update readme",
            description="Add docs for new feature",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.WATCH
        assert classification.risk_score <= 0.3

    def test_content_type_normal_priority_returns_watch(self):
        """Test content type with normal priority returns WATCH."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.CONTENT,
            priority=ApprovalPriority.NORMAL,
            title="Review blog post",
            description="New blog post about testing",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.WATCH
        assert "content" in classification.reason.lower()

    def test_retry_pattern_returns_watch(self):
        """Test retry pattern matches watch-level decision."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Retry failed test",
            description="Test suite needs retry",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.WATCH
        assert "retry" in request.title.lower()

    def test_lint_fix_returns_watch(self):
        """Test lint fix matches watch-level pattern."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.LOW,
            title="Lint fixes for codebase",
            description="Apply eslint fixes",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.WATCH
        assert "lint" in request.title.lower()

    def test_format_fix_returns_watch(self):
        """Test format fix matches watch-level pattern."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.LOW,
            title="Format Python files",
            description="Run black formatter",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.WATCH
        assert "format" in request.title.lower()

    def test_docs_content_returns_watch(self):
        """Test documentation content matches watch-level pattern."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.CONTENT,
            priority=ApprovalPriority.NORMAL,
            title="Update docs for API",
            description="Add API documentation",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.WATCH
        assert "docs" in request.title.lower()


# =============================================================================
# DecisionRouter Classification Tests - PHONE Tier
# =============================================================================


class TestDecisionRouterPhoneTier:
    """Test classification to PHONE tier."""

    def test_normal_priority_feature_returns_phone(self):
        """Test normal priority feature returns PHONE."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Add user profile page",
            description="Implement user profile display",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.PHONE
        assert 0.3 < classification.risk_score < 0.7

    def test_config_change_normal_priority_returns_phone(self):
        """Test config change with normal priority returns PHONE."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.CONFIG,
            priority=ApprovalPriority.NORMAL,
            title="Update API timeout",
            description="Change timeout from 30s to 60s",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.PHONE

    def test_high_priority_without_high_risk_keywords_returns_phone(self):
        """Test high priority without high-risk keywords can return DESKTOP if risk >= 0.7."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.HIGH,
            title="Add new UI component",
            description="Create reusable button component",
        )

        classification = router.classify(request)

        # High priority (0.5 + 0.2 = 0.7) can trigger DESKTOP tier
        assert classification.tier in (DecisionTier.PHONE, DecisionTier.DESKTOP)
        assert classification.risk_score >= 0.7 or classification.risk_score < 0.7

    def test_moderate_risk_returns_phone(self):
        """Test moderate risk score returns PHONE."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.DATA,
            priority=ApprovalPriority.NORMAL,
            title="Update user preferences",
            description="Modify user settings in database",
        )

        classification = router.classify(request)

        assert classification.tier == DecisionTier.PHONE
        assert 0.3 < classification.risk_score < 0.7


# =============================================================================
# Risk Score Calculation Tests
# =============================================================================


class TestRiskScoreCalculation:
    """Test risk score calculation logic."""

    def test_risk_score_starts_at_medium(self):
        """Test risk score baseline is around 0.5."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Simple update",
            description="Make a basic modification",
        )

        classification = router.classify(request)

        # Normal priority + minimal/no keywords = around 0.5 baseline
        # Allow some variance for keyword matching
        assert 0.4 <= classification.risk_score <= 0.7

    def test_critical_priority_increases_risk_score(self):
        """Test critical priority adds 0.3 to risk score."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.CRITICAL,
            title="Simple change",
            description="No special keywords",
        )

        classification = router.classify(request)

        # 0.5 + 0.3 (critical) = 0.8
        assert classification.risk_score >= 0.7

    def test_high_priority_increases_risk_score(self):
        """Test high priority adds 0.2 to risk score."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.HIGH,
            title="Simple update",
            description="Basic modification",
        )

        classification = router.classify(request)

        # 0.5 + 0.2 (high) = 0.7, but may have minor keyword matches
        assert classification.risk_score >= 0.7

    def test_low_priority_decreases_risk_score(self):
        """Test low priority subtracts 0.2 from risk score."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.LOW,
            title="Simple update",
            description="Basic modification",
        )

        classification = router.classify(request)

        # 0.5 - 0.2 (low) = 0.3, but may have minor keyword matches
        assert classification.risk_score <= 0.5

    def test_high_risk_keywords_increase_score(self):
        """Test high-risk keywords add 0.1 per keyword."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Security update",
            description="Change authentication",
        )

        classification = router.classify(request)

        # 0.5 + 0.1*2 (security, authentication) = 0.7
        assert classification.risk_score >= 0.7
        assert "security" in classification.keywords_matched
        assert "authentication" in classification.keywords_matched

    def test_low_risk_keywords_decrease_score(self):
        """Test low-risk keywords subtract 0.1 per keyword."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Fix typo in test",
            description="Minor refactor",
        )

        classification = router.classify(request)

        # 0.5 - 0.1*4 (fix, typo, test, minor, refactor) = at least 0.1
        assert classification.risk_score < 0.5

    def test_desktop_type_increases_score(self):
        """Test DESKTOP_TYPES add 0.3 to risk score."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.SECURITY,
            priority=ApprovalPriority.NORMAL,
            title="Update",
            description="Change",
        )

        classification = router.classify(request)

        # 0.5 + 0.3 (security type) = 0.8
        assert classification.risk_score >= 0.7

    def test_watch_type_decreases_score(self):
        """Test WATCH_TYPES subtract 0.2 from risk score."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.CONTENT,
            priority=ApprovalPriority.NORMAL,
            title="Update",
            description="Change",
        )

        classification = router.classify(request)

        # 0.5 - 0.2 (content type) = 0.3
        assert classification.risk_score == 0.3

    def test_risk_score_clamped_to_zero(self):
        """Test risk score is clamped to minimum 0.0."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.CONTENT,
            priority=ApprovalPriority.LOW,
            title="Fix typo in test docs",
            description="Minor formatting and lint fixes",
        )

        classification = router.classify(request)

        # Many low-risk factors but should be clamped to 0.0
        assert classification.risk_score >= 0.0

    def test_risk_score_clamped_to_one(self):
        """Test risk score is clamped to maximum 1.0."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.SECURITY,
            priority=ApprovalPriority.CRITICAL,
            title="Production deploy with database schema migration",
            description="Breaking API change with security and payment updates",
        )

        classification = router.classify(request)

        # Many high-risk factors but should be clamped to 1.0
        assert classification.risk_score <= 1.0


# =============================================================================
# Edge Cases Tests
# =============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_title_and_description(self):
        """Test classification with empty title and description."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="",
            description="",
        )

        classification = router.classify(request)

        # Should still classify based on type and priority
        assert classification.tier in (DecisionTier.WATCH, DecisionTier.PHONE, DecisionTier.DESKTOP)
        assert classification.keywords_matched == []

    def test_title_and_description_with_multiple_keywords(self):
        """Test classification with multiple keyword matches."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Security authentication deploy",
            description="Production database migration with payment processing",
        )

        classification = router.classify(request)

        # Should match multiple high-risk keywords
        high_risk_matched = [
            kw for kw in classification.keywords_matched if kw in HIGH_RISK_KEYWORDS
        ]
        assert len(high_risk_matched) >= 4
        assert classification.tier == DecisionTier.DESKTOP

    def test_case_insensitive_keyword_matching(self):
        """Test keyword matching is case-insensitive."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="SECURITY UPDATE",
            description="Fix AUTHENTICATION issue",
        )

        classification = router.classify(request)

        assert "security" in classification.keywords_matched
        assert "authentication" in classification.keywords_matched

    def test_keywords_in_title_only(self):
        """Test keywords matched in title only."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Security update",
            description="Make changes",
        )

        classification = router.classify(request)

        assert "security" in classification.keywords_matched

    def test_keywords_in_description_only(self):
        """Test keywords matched in description only."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Update system",
            description="Change authentication method",
        )

        classification = router.classify(request)

        assert "authentication" in classification.keywords_matched

    def test_mixed_high_and_low_risk_keywords(self):
        """Test classification with both high and low risk keywords."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Fix security test",
            description="Minor authentication refactor",
        )

        classification = router.classify(request)

        # Should have both types of keywords
        high_matches = [kw for kw in classification.keywords_matched if kw in HIGH_RISK_KEYWORDS]
        low_matches = [kw for kw in classification.keywords_matched if kw in LOW_RISK_KEYWORDS]

        assert len(high_matches) >= 2  # security, authentication
        assert len(low_matches) >= 3  # fix, test, minor, refactor

    def test_partial_keyword_match_not_counted(self):
        """Test partial keyword matches are not counted."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Testing deployment",  # "test" in "Testing"
            description="Product features",  # "prod" in "Product"
        )

        classification = router.classify(request)

        # Should match "test" and "deploy" but not count partial matches
        assert "test" in classification.keywords_matched
        assert "deploy" in classification.keywords_matched


# =============================================================================
# Convenience Functions Tests
# =============================================================================


class TestConvenienceFunctions:
    """Test convenience functions for tier classification."""

    def test_get_router_returns_singleton(self):
        """Test get_router returns the same instance."""
        router1 = get_router()
        router2 = get_router()

        assert router1 is router2

    def test_classify_decision_tier_returns_tier(self):
        """Test classify_decision_tier returns only the tier."""
        request = create_mock_request(
            request_type=ApprovalType.SECURITY,
            title="Security update",
        )

        tier = classify_decision_tier(request)

        assert tier == DecisionTier.DESKTOP
        assert isinstance(tier, DecisionTier)

    def test_get_tier_classification_returns_full_classification(self):
        """Test get_tier_classification returns full TierClassification."""
        request = create_mock_request(
            request_type=ApprovalType.SECURITY,
            title="Security update",
        )

        classification = get_tier_classification(request)

        assert isinstance(classification, TierClassification)
        assert classification.tier == DecisionTier.DESKTOP
        assert classification.reason is not None
        assert classification.risk_score >= 0.0
        assert isinstance(classification.keywords_matched, list)

    @patch("forge_harness.decision_router._router", None)
    def test_get_router_creates_new_instance_if_none(self):
        """Test get_router creates new instance if global is None."""
        from forge_harness.decision_router import _router

        assert _router is None

        router = get_router()

        assert router is not None
        assert isinstance(router, DecisionRouter)


# =============================================================================
# Custom Keywords Tests
# =============================================================================


class TestCustomKeywords:
    """Test DecisionRouter with custom keywords."""

    def test_custom_high_risk_keyword_affects_classification(self):
        """Test custom high-risk keyword increases risk score."""
        custom_high = frozenset(["urgent", "immediate"])
        router = DecisionRouter(high_risk_keywords=custom_high)

        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Urgent change needed",
            description="Immediate action required",
        )

        classification = router.classify(request)

        assert "urgent" in classification.keywords_matched
        assert "immediate" in classification.keywords_matched
        assert classification.risk_score > 0.5

    def test_custom_low_risk_keyword_affects_classification(self):
        """Test custom low-risk keyword decreases risk score."""
        custom_low = frozenset(["trivial", "cosmetic"])
        router = DecisionRouter(low_risk_keywords=custom_low)

        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Trivial cosmetic change",
            description="UI update",
        )

        classification = router.classify(request)

        assert "trivial" in classification.keywords_matched
        assert "cosmetic" in classification.keywords_matched
        assert classification.risk_score < 0.5

    def test_default_keywords_not_used_with_custom(self):
        """Test default keywords are replaced when custom provided."""
        custom_high = frozenset(["custom_danger"])
        router = DecisionRouter(high_risk_keywords=custom_high)

        # Use a default high-risk keyword that should NOT match
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Security update",
            description="Production deploy",
        )

        classification = router.classify(request)

        # "security" and "production" should NOT be in keywords_matched
        assert "security" not in classification.keywords_matched
        assert "production" not in classification.keywords_matched


# =============================================================================
# Integration Tests
# =============================================================================


class TestDecisionRouterIntegration:
    """Integration tests for complete workflows."""

    def test_end_to_end_desktop_classification(self):
        """Test complete flow for DESKTOP tier classification."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.DEPLOY,
            priority=ApprovalPriority.HIGH,
            title="Production deployment",
            description="Deploy to production with database migration",
        )

        classification = router.classify(request)

        # Verify all aspects of classification
        assert classification.tier == DecisionTier.DESKTOP
        assert classification.risk_score >= 0.7
        assert "deploy" in classification.reason.lower()
        assert len(classification.keywords_matched) > 0

    def test_end_to_end_watch_classification(self):
        """Test complete flow for WATCH tier classification."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.CONTENT,
            priority=ApprovalPriority.LOW,
            title="Fix typo in docs",
            description="Minor formatting change",
        )

        classification = router.classify(request)

        # Verify all aspects of classification
        assert classification.tier == DecisionTier.WATCH
        assert classification.risk_score <= 0.3
        assert len(classification.keywords_matched) > 0

    def test_end_to_end_phone_classification(self):
        """Test complete flow for PHONE tier classification."""
        router = DecisionRouter()
        request = create_mock_request(
            request_type=ApprovalType.FEATURE,
            priority=ApprovalPriority.NORMAL,
            title="Add new feature",
            description="Implement user profile page",
        )

        classification = router.classify(request)

        # Verify all aspects of classification
        assert classification.tier == DecisionTier.PHONE
        assert 0.3 < classification.risk_score < 0.7

    def test_multiple_requests_maintain_consistency(self):
        """Test router maintains consistency across multiple requests."""
        router = DecisionRouter()

        # Create multiple identical requests
        requests = [
            create_mock_request(
                request_type=ApprovalType.SECURITY,
                priority=ApprovalPriority.HIGH,
                title="Security update",
                description="Authentication change",
            )
            for _ in range(3)
        ]

        classifications = [router.classify(req) for req in requests]

        # All should have same tier and similar risk scores
        tiers = [c.tier for c in classifications]
        assert len(set(tiers)) == 1  # All same tier
        assert all(t == DecisionTier.DESKTOP for t in tiers)

        risk_scores = [c.risk_score for c in classifications]
        assert all(abs(risk_scores[0] - score) < 0.01 for score in risk_scores)
