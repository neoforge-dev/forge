"""
Lane Policy Matrix Tests
========================

Validates every cell in the LANE_POLICY matrix and the public helper
functions get_lane() and requires_human_approval().

Coverage targets:
- Every (TaskType, RiskTier) combination in the 12x4 matrix.
- Policy invariants (security never autonomous at high/critical, etc.).
- Helper function behaviour for all three lane outcomes.
"""

import pytest

from forge_harness.webhook_server.models.lane_policy import (
    LANE_POLICY,
    Lane,
    RiskTier,
    TaskType,
    get_lane,
    requires_human_approval,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_lane(task_type: TaskType, risk_tier: RiskTier, expected: Lane) -> None:
    """Assert get_lane returns expected and the matrix cell matches."""
    result = get_lane(task_type, risk_tier)
    assert result == expected, (
        f"get_lane({task_type.value!r}, {risk_tier.value!r}) "
        f"returned {result.value!r}, expected {expected.value!r}"
    )
    # Also confirm the matrix lookup directly
    assert LANE_POLICY[(task_type, risk_tier)] == expected


# ---------------------------------------------------------------------------
# Matrix completeness
# ---------------------------------------------------------------------------


class TestMatrixCompleteness:
    """Verify the matrix covers every (TaskType, RiskTier) pair."""

    def test_matrix_has_all_combinations(self) -> None:
        expected = len(TaskType) * len(RiskTier)
        assert len(LANE_POLICY) == expected, (
            f"LANE_POLICY should have {expected} entries, got {len(LANE_POLICY)}"
        )

    def test_all_task_types_present(self) -> None:
        for tt in TaskType:
            for rt in RiskTier:
                assert (tt, rt) in LANE_POLICY, f"Missing policy entry for ({tt.value}, {rt.value})"

    def test_all_values_are_valid_lanes(self) -> None:
        valid = set(Lane)
        for (tt, rt), lane in LANE_POLICY.items():
            assert lane in valid, f"Invalid lane {lane!r} for ({tt.value}, {rt.value})"


# ---------------------------------------------------------------------------
# test-writing
# ---------------------------------------------------------------------------


class TestTestWritingLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.test_writing, RiskTier.low, Lane.autonomous)

    def test_medium(self) -> None:
        _assert_lane(TaskType.test_writing, RiskTier.medium, Lane.autonomous)

    def test_high(self) -> None:
        _assert_lane(TaskType.test_writing, RiskTier.high, Lane.human_review)

    def test_critical(self) -> None:
        _assert_lane(TaskType.test_writing, RiskTier.critical, Lane.human_review)


# ---------------------------------------------------------------------------
# docs-update
# ---------------------------------------------------------------------------


class TestDocsUpdateLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.docs_update, RiskTier.low, Lane.autonomous)

    def test_medium(self) -> None:
        _assert_lane(TaskType.docs_update, RiskTier.medium, Lane.autonomous)

    def test_high(self) -> None:
        _assert_lane(TaskType.docs_update, RiskTier.high, Lane.human_review)

    def test_critical(self) -> None:
        _assert_lane(TaskType.docs_update, RiskTier.critical, Lane.human_review)


# ---------------------------------------------------------------------------
# content-generation
# ---------------------------------------------------------------------------


class TestContentGenerationLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.content_generation, RiskTier.low, Lane.autonomous)

    def test_medium(self) -> None:
        _assert_lane(TaskType.content_generation, RiskTier.medium, Lane.autonomous)

    def test_high(self) -> None:
        _assert_lane(TaskType.content_generation, RiskTier.high, Lane.human_review)

    def test_critical(self) -> None:
        _assert_lane(TaskType.content_generation, RiskTier.critical, Lane.blocked)


# ---------------------------------------------------------------------------
# code-refactor
# ---------------------------------------------------------------------------


class TestCodeRefactorLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.code_refactor, RiskTier.low, Lane.autonomous)

    def test_medium(self) -> None:
        _assert_lane(TaskType.code_refactor, RiskTier.medium, Lane.human_review)

    def test_high(self) -> None:
        _assert_lane(TaskType.code_refactor, RiskTier.high, Lane.human_review)

    def test_critical(self) -> None:
        _assert_lane(TaskType.code_refactor, RiskTier.critical, Lane.blocked)


# ---------------------------------------------------------------------------
# api-endpoint
# ---------------------------------------------------------------------------


class TestApiEndpointLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.api_endpoint, RiskTier.low, Lane.autonomous)

    def test_medium(self) -> None:
        _assert_lane(TaskType.api_endpoint, RiskTier.medium, Lane.human_review)

    def test_high(self) -> None:
        _assert_lane(TaskType.api_endpoint, RiskTier.high, Lane.human_review)

    def test_critical(self) -> None:
        _assert_lane(TaskType.api_endpoint, RiskTier.critical, Lane.blocked)


# ---------------------------------------------------------------------------
# security-change
# ---------------------------------------------------------------------------


class TestSecurityChangeLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.security_change, RiskTier.low, Lane.human_review)

    def test_medium(self) -> None:
        _assert_lane(TaskType.security_change, RiskTier.medium, Lane.human_review)

    def test_high(self) -> None:
        _assert_lane(TaskType.security_change, RiskTier.high, Lane.blocked)

    def test_critical(self) -> None:
        _assert_lane(TaskType.security_change, RiskTier.critical, Lane.blocked)

    def test_never_autonomous_at_any_tier(self) -> None:
        """Security changes must never be autonomous at any risk tier."""
        for risk_tier in RiskTier:
            lane = get_lane(TaskType.security_change, risk_tier)
            assert lane != Lane.autonomous, (
                f"security-change must not be autonomous at risk_tier={risk_tier.value}"
            )

    def test_never_autonomous_at_high(self) -> None:
        lane = get_lane(TaskType.security_change, RiskTier.high)
        assert lane != Lane.autonomous

    def test_never_autonomous_at_critical(self) -> None:
        lane = get_lane(TaskType.security_change, RiskTier.critical)
        assert lane != Lane.autonomous


# ---------------------------------------------------------------------------
# deployment
# ---------------------------------------------------------------------------


class TestDeploymentLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.deployment, RiskTier.low, Lane.human_review)

    def test_medium(self) -> None:
        _assert_lane(TaskType.deployment, RiskTier.medium, Lane.human_review)

    def test_high(self) -> None:
        _assert_lane(TaskType.deployment, RiskTier.high, Lane.blocked)

    def test_critical(self) -> None:
        _assert_lane(TaskType.deployment, RiskTier.critical, Lane.blocked)

    def test_always_blocked_at_critical(self) -> None:
        """Production deployment is always blocked — no exceptions."""
        lane = get_lane(TaskType.deployment, RiskTier.critical)
        assert lane == Lane.blocked

    def test_never_autonomous(self) -> None:
        """Deployments are never autonomous regardless of risk tier."""
        for risk_tier in RiskTier:
            lane = get_lane(TaskType.deployment, risk_tier)
            assert lane != Lane.autonomous, (
                f"deployment must not be autonomous at risk_tier={risk_tier.value}"
            )


# ---------------------------------------------------------------------------
# database-migration
# ---------------------------------------------------------------------------


class TestDatabaseMigrationLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.database_migration, RiskTier.low, Lane.human_review)

    def test_medium(self) -> None:
        _assert_lane(TaskType.database_migration, RiskTier.medium, Lane.human_review)

    def test_high(self) -> None:
        _assert_lane(TaskType.database_migration, RiskTier.high, Lane.blocked)

    def test_critical(self) -> None:
        _assert_lane(TaskType.database_migration, RiskTier.critical, Lane.blocked)

    def test_blocked_at_high(self) -> None:
        lane = get_lane(TaskType.database_migration, RiskTier.high)
        assert lane == Lane.blocked

    def test_blocked_at_critical(self) -> None:
        lane = get_lane(TaskType.database_migration, RiskTier.critical)
        assert lane == Lane.blocked

    def test_never_autonomous(self) -> None:
        for risk_tier in RiskTier:
            lane = get_lane(TaskType.database_migration, risk_tier)
            assert lane != Lane.autonomous, (
                f"database-migration must not be autonomous at risk_tier={risk_tier.value}"
            )


# ---------------------------------------------------------------------------
# dependency-update
# ---------------------------------------------------------------------------


class TestDependencyUpdateLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.dependency_update, RiskTier.low, Lane.autonomous)

    def test_medium(self) -> None:
        _assert_lane(TaskType.dependency_update, RiskTier.medium, Lane.human_review)

    def test_high(self) -> None:
        _assert_lane(TaskType.dependency_update, RiskTier.high, Lane.human_review)

    def test_critical(self) -> None:
        _assert_lane(TaskType.dependency_update, RiskTier.critical, Lane.blocked)


# ---------------------------------------------------------------------------
# config-change
# ---------------------------------------------------------------------------


class TestConfigChangeLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.config_change, RiskTier.low, Lane.autonomous)

    def test_medium(self) -> None:
        _assert_lane(TaskType.config_change, RiskTier.medium, Lane.human_review)

    def test_high(self) -> None:
        _assert_lane(TaskType.config_change, RiskTier.high, Lane.human_review)

    def test_critical(self) -> None:
        _assert_lane(TaskType.config_change, RiskTier.critical, Lane.blocked)


# ---------------------------------------------------------------------------
# new-feature
# ---------------------------------------------------------------------------


class TestNewFeatureLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.new_feature, RiskTier.low, Lane.autonomous)

    def test_medium(self) -> None:
        _assert_lane(TaskType.new_feature, RiskTier.medium, Lane.human_review)

    def test_high(self) -> None:
        _assert_lane(TaskType.new_feature, RiskTier.high, Lane.human_review)

    def test_critical(self) -> None:
        _assert_lane(TaskType.new_feature, RiskTier.critical, Lane.blocked)


# ---------------------------------------------------------------------------
# bug-fix
# ---------------------------------------------------------------------------


class TestBugFixLane:
    def test_low(self) -> None:
        _assert_lane(TaskType.bug_fix, RiskTier.low, Lane.autonomous)

    def test_medium(self) -> None:
        _assert_lane(TaskType.bug_fix, RiskTier.medium, Lane.human_review)

    def test_high(self) -> None:
        _assert_lane(TaskType.bug_fix, RiskTier.high, Lane.human_review)

    def test_critical(self) -> None:
        _assert_lane(TaskType.bug_fix, RiskTier.critical, Lane.blocked)


# ---------------------------------------------------------------------------
# Global invariants across the full matrix
# ---------------------------------------------------------------------------


class TestGlobalInvariants:
    """Cross-cutting policy rules that must hold for every task type."""

    def test_no_task_type_is_autonomous_at_critical(self) -> None:
        """Invariant 4: No task type may be autonomous at critical risk."""
        for task_type in TaskType:
            lane = get_lane(task_type, RiskTier.critical)
            assert lane != Lane.autonomous, (
                f"{task_type.value} must not be autonomous at critical risk"
            )

    def test_security_never_autonomous_at_high_or_critical(self) -> None:
        """Invariant 1: Security changes are never autonomous at high/critical."""
        for risk_tier in (RiskTier.high, RiskTier.critical):
            lane = get_lane(TaskType.security_change, risk_tier)
            assert lane != Lane.autonomous, (
                f"security-change must not be autonomous at {risk_tier.value}"
            )

    def test_deployment_always_blocked_at_critical(self) -> None:
        """Invariant 2: Deployment is always blocked at critical."""
        lane = get_lane(TaskType.deployment, RiskTier.critical)
        assert lane == Lane.blocked

    def test_database_migration_blocked_at_high_and_critical(self) -> None:
        """Invariant 3: DB migrations blocked at high and critical."""
        for risk_tier in (RiskTier.high, RiskTier.critical):
            lane = get_lane(TaskType.database_migration, risk_tier)
            assert lane == Lane.blocked, f"database-migration must be blocked at {risk_tier.value}"

    def test_low_risk_standard_tasks_are_autonomous(self) -> None:
        """Standard non-sensitive tasks at low risk should be autonomous."""
        autonomous_at_low = [
            TaskType.test_writing,
            TaskType.docs_update,
            TaskType.content_generation,
            TaskType.code_refactor,
            TaskType.api_endpoint,
            TaskType.dependency_update,
            TaskType.config_change,
            TaskType.new_feature,
            TaskType.bug_fix,
        ]
        for task_type in autonomous_at_low:
            lane = get_lane(task_type, RiskTier.low)
            assert lane == Lane.autonomous, (
                f"{task_type.value} at low risk should be autonomous, got {lane.value}"
            )

    def test_sensitive_task_types_never_autonomous_at_low(self) -> None:
        """Security, deployment, and database-migration are never autonomous."""
        sensitive = [
            TaskType.security_change,
            TaskType.deployment,
            TaskType.database_migration,
        ]
        for task_type in sensitive:
            for risk_tier in RiskTier:
                lane = get_lane(task_type, risk_tier)
                assert lane != Lane.autonomous, (
                    f"{task_type.value} must never be autonomous "
                    f"(was autonomous at {risk_tier.value})"
                )


# ---------------------------------------------------------------------------
# requires_human_approval
# ---------------------------------------------------------------------------


class TestRequiresHumanApproval:
    """Validate the requires_human_approval convenience function."""

    def test_autonomous_returns_false(self) -> None:
        assert requires_human_approval(TaskType.bug_fix, RiskTier.low) is False

    def test_human_review_returns_true(self) -> None:
        assert requires_human_approval(TaskType.bug_fix, RiskTier.medium) is True

    def test_blocked_returns_true(self) -> None:
        assert requires_human_approval(TaskType.deployment, RiskTier.critical) is True

    def test_security_change_always_requires_approval(self) -> None:
        for risk_tier in RiskTier:
            result = requires_human_approval(TaskType.security_change, risk_tier)
            assert result is True, (
                f"security-change at {risk_tier.value} should require human approval"
            )

    def test_deployment_always_requires_approval(self) -> None:
        for risk_tier in RiskTier:
            result = requires_human_approval(TaskType.deployment, risk_tier)
            assert result is True, f"deployment at {risk_tier.value} should require human approval"

    def test_database_migration_always_requires_approval(self) -> None:
        for risk_tier in RiskTier:
            result = requires_human_approval(TaskType.database_migration, risk_tier)
            assert result is True, (
                f"database-migration at {risk_tier.value} should require human approval"
            )

    def test_low_risk_standard_tasks_do_not_require_approval(self) -> None:
        low_risk_autonomous = [
            TaskType.test_writing,
            TaskType.docs_update,
            TaskType.content_generation,
            TaskType.code_refactor,
            TaskType.api_endpoint,
            TaskType.dependency_update,
            TaskType.config_change,
            TaskType.new_feature,
            TaskType.bug_fix,
        ]
        for task_type in low_risk_autonomous:
            result = requires_human_approval(task_type, RiskTier.low)
            assert result is False, (
                f"{task_type.value} at low risk should not require human approval"
            )

    def test_requires_approval_consistent_with_lane(self) -> None:
        """requires_human_approval must be False iff lane is autonomous."""
        for task_type in TaskType:
            for risk_tier in RiskTier:
                lane = get_lane(task_type, risk_tier)
                needs_approval = requires_human_approval(task_type, risk_tier)
                if lane == Lane.autonomous:
                    assert needs_approval is False, (
                        f"({task_type.value}, {risk_tier.value}) is autonomous "
                        f"but requires_human_approval returned True"
                    )
                else:
                    assert needs_approval is True, (
                        f"({task_type.value}, {risk_tier.value}) lane={lane.value} "
                        f"but requires_human_approval returned False"
                    )


# ---------------------------------------------------------------------------
# Enum value round-trips
# ---------------------------------------------------------------------------


class TestEnumValues:
    """Ensure string values round-trip correctly (used in JSON payloads)."""

    def test_risk_tier_string_values(self) -> None:
        assert RiskTier.low.value == "low"
        assert RiskTier.medium.value == "medium"
        assert RiskTier.high.value == "high"
        assert RiskTier.critical.value == "critical"

    def test_lane_string_values(self) -> None:
        assert Lane.autonomous.value == "autonomous"
        assert Lane.human_review.value == "human-review"
        assert Lane.blocked.value == "blocked"

    def test_task_type_string_values(self) -> None:
        assert TaskType.test_writing.value == "test-writing"
        assert TaskType.docs_update.value == "docs-update"
        assert TaskType.security_change.value == "security-change"
        assert TaskType.database_migration.value == "database-migration"
        assert TaskType.dependency_update.value == "dependency-update"
        assert TaskType.config_change.value == "config-change"
        assert TaskType.new_feature.value == "new-feature"
        assert TaskType.bug_fix.value == "bug-fix"

    def test_risk_tier_from_string(self) -> None:
        assert RiskTier("low") == RiskTier.low
        assert RiskTier("critical") == RiskTier.critical

    def test_lane_from_string(self) -> None:
        assert Lane("autonomous") == Lane.autonomous
        assert Lane("human-review") == Lane.human_review
        assert Lane("blocked") == Lane.blocked

    def test_task_type_from_string(self) -> None:
        assert TaskType("security-change") == TaskType.security_change
        assert TaskType("deployment") == TaskType.deployment
