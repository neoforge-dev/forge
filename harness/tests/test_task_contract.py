"""Tests for the Dark Factory task contract extension models.

Coverage targets (90%+):
- AcceptanceCheck: field validation, defaults, edge cases.
- EvaluatorProfile: checks list, defaults, max_retries bounds.
- TaskContractExtension: auto-computation of lane and requires_human_approval.
- All RiskTier x TaskType combinations produce the correct lane.
- Caller-supplied lane/requires_human_approval values are overwritten.
- Serialization round-trip (model_dump -> model_validate).
- Edge cases: None risk_tier, None task_type, one set but not the other.
- Public re-export via models.__init__.
"""

import json

import pytest
from pydantic import ValidationError

from forge_harness.webhook_server.models.lane_policy import (
    LANE_POLICY,
    Lane,
    RiskTier,
    TaskType,
    get_lane,
)
from forge_harness.webhook_server.models.task_contract import (
    AcceptanceCheck,
    EvaluatorProfile,
    TaskContractExtension,
)

# ===========================================================================
# AcceptanceCheck
# ===========================================================================


class TestAcceptanceCheckDefaults:
    def test_required_fields_set(self):
        check = AcceptanceCheck(name="unit-tests", command="uv run pytest tests/ -q")
        assert check.name == "unit-tests"
        assert check.command == "uv run pytest tests/ -q"

    def test_default_timeout(self):
        check = AcceptanceCheck(name="lint", command="ruff check .")
        assert check.timeout_seconds == 300

    def test_default_required_is_true(self):
        check = AcceptanceCheck(name="lint", command="ruff check .")
        assert check.required is True

    def test_explicit_optional_check(self):
        check = AcceptanceCheck(name="coverage", command="uv run pytest --cov", required=False)
        assert check.required is False

    def test_custom_timeout(self):
        check = AcceptanceCheck(name="e2e", command="playwright test", timeout_seconds=600)
        assert check.timeout_seconds == 600


class TestAcceptanceCheckValidation:
    def test_name_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            AcceptanceCheck(name="", command="uv run pytest")

    def test_command_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            AcceptanceCheck(name="tests", command="")

    def test_timeout_minimum_is_one(self):
        with pytest.raises(ValidationError):
            AcceptanceCheck(name="fast", command="echo ok", timeout_seconds=0)

    def test_timeout_maximum_is_86400(self):
        with pytest.raises(ValidationError):
            AcceptanceCheck(name="slow", command="sleep 99999", timeout_seconds=86401)

    def test_timeout_at_boundary_minimum(self):
        check = AcceptanceCheck(name="x", command="true", timeout_seconds=1)
        assert check.timeout_seconds == 1

    def test_timeout_at_boundary_maximum(self):
        check = AcceptanceCheck(name="x", command="true", timeout_seconds=86400)
        assert check.timeout_seconds == 86400


class TestAcceptanceCheckSerialization:
    def test_round_trip_via_model_dump(self):
        original = AcceptanceCheck(
            name="unit-tests",
            command="uv run pytest tests/ -x",
            timeout_seconds=120,
            required=True,
        )
        data = original.model_dump()
        restored = AcceptanceCheck.model_validate(data)
        assert restored == original

    def test_round_trip_via_json(self):
        original = AcceptanceCheck(name="lint", command="ruff check .", required=False)
        as_json = original.model_dump_json()
        restored = AcceptanceCheck.model_validate_json(as_json)
        assert restored == original

    def test_model_dump_contains_expected_keys(self):
        check = AcceptanceCheck(name="n", command="c")
        data = check.model_dump()
        assert set(data.keys()) == {"name", "command", "timeout_seconds", "required"}


# ===========================================================================
# EvaluatorProfile
# ===========================================================================


class TestEvaluatorProfileDefaults:
    def test_empty_profile(self):
        profile = EvaluatorProfile()
        assert profile.checks == []
        assert profile.auto_approve_on_pass is False
        assert profile.max_retries == 1

    def test_auto_approve_can_be_enabled(self):
        profile = EvaluatorProfile(auto_approve_on_pass=True)
        assert profile.auto_approve_on_pass is True

    def test_max_retries_custom(self):
        profile = EvaluatorProfile(max_retries=3)
        assert profile.max_retries == 3


class TestEvaluatorProfileChecks:
    def test_single_check(self):
        check = AcceptanceCheck(name="lint", command="ruff check .")
        profile = EvaluatorProfile(checks=[check])
        assert len(profile.checks) == 1
        assert profile.checks[0].name == "lint"

    def test_multiple_checks_preserve_order(self):
        checks = [
            AcceptanceCheck(name="lint", command="ruff check ."),
            AcceptanceCheck(name="tests", command="uv run pytest"),
            AcceptanceCheck(name="types", command="mypy forge_harness/"),
        ]
        profile = EvaluatorProfile(checks=checks)
        assert [c.name for c in profile.checks] == ["lint", "tests", "types"]

    def test_check_with_optional_item(self):
        checks = [
            AcceptanceCheck(name="required-check", command="pytest", required=True),
            AcceptanceCheck(name="coverage-report", command="pytest --cov", required=False),
        ]
        profile = EvaluatorProfile(checks=checks)
        required = [c for c in profile.checks if c.required]
        optional = [c for c in profile.checks if not c.required]
        assert len(required) == 1
        assert len(optional) == 1


class TestEvaluatorProfileValidation:
    def test_max_retries_minimum_is_zero(self):
        with pytest.raises(ValidationError):
            EvaluatorProfile(max_retries=-1)

    def test_max_retries_maximum_is_ten(self):
        with pytest.raises(ValidationError):
            EvaluatorProfile(max_retries=11)

    def test_max_retries_at_zero(self):
        profile = EvaluatorProfile(max_retries=0)
        assert profile.max_retries == 0

    def test_max_retries_at_ten(self):
        profile = EvaluatorProfile(max_retries=10)
        assert profile.max_retries == 10


class TestEvaluatorProfileSerialization:
    def test_round_trip(self):
        check = AcceptanceCheck(name="unit-tests", command="pytest")
        original = EvaluatorProfile(checks=[check], auto_approve_on_pass=True, max_retries=2)
        data = original.model_dump()
        restored = EvaluatorProfile.model_validate(data)
        assert restored == original

    def test_json_round_trip(self):
        original = EvaluatorProfile(
            checks=[AcceptanceCheck(name="lint", command="ruff check .")],
            max_retries=0,
        )
        restored = EvaluatorProfile.model_validate_json(original.model_dump_json())
        assert restored == original


# ===========================================================================
# TaskContractExtension — edge cases (no risk tier / task type)
# ===========================================================================


class TestTaskContractExtensionDefaults:
    def test_fully_unclassified(self):
        ext = TaskContractExtension()
        assert ext.risk_tier is None
        assert ext.task_type is None
        assert ext.lane is None
        assert ext.requires_human_approval is False
        assert ext.acceptance_checks == []
        assert ext.evaluator_profile is None

    def test_only_risk_tier_set_lane_is_none(self):
        ext = TaskContractExtension(risk_tier=RiskTier.high)
        assert ext.lane is None
        assert ext.requires_human_approval is False

    def test_only_task_type_set_lane_is_none(self):
        ext = TaskContractExtension(task_type=TaskType.bug_fix)
        assert ext.lane is None
        assert ext.requires_human_approval is False


# ===========================================================================
# TaskContractExtension — lane computation
# ===========================================================================


class TestTaskContractExtensionLaneComputation:
    def test_autonomous_lane(self):
        ext = TaskContractExtension(
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
        )
        assert ext.lane == Lane.autonomous
        assert ext.requires_human_approval is False

    def test_human_review_lane(self):
        ext = TaskContractExtension(
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.medium,
        )
        assert ext.lane == Lane.human_review
        assert ext.requires_human_approval is True

    def test_blocked_lane(self):
        ext = TaskContractExtension(
            task_type=TaskType.deployment,
            risk_tier=RiskTier.critical,
        )
        assert ext.lane == Lane.blocked
        assert ext.requires_human_approval is True

    def test_caller_supplied_lane_is_overwritten(self):
        """The validator must overwrite any caller-supplied lane value."""
        ext = TaskContractExtension(
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            lane=Lane.blocked,  # caller tries to override — should be ignored
        )
        assert ext.lane == Lane.autonomous

    def test_caller_supplied_requires_human_approval_is_overwritten(self):
        """The validator must overwrite any caller-supplied requires_human_approval."""
        ext = TaskContractExtension(
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            requires_human_approval=True,  # caller tries to override — should be ignored
        )
        assert ext.requires_human_approval is False


# ===========================================================================
# TaskContractExtension — full RiskTier x TaskType matrix
# ===========================================================================


class TestTaskContractExtensionFullMatrix:
    """Every (TaskType, RiskTier) cell must produce the same lane as LANE_POLICY."""

    @pytest.mark.parametrize(
        "task_type,risk_tier,expected_lane",
        [(tt, rt, lane) for (tt, rt), lane in LANE_POLICY.items()],
    )
    def test_matrix_cell(self, task_type: TaskType, risk_tier: RiskTier, expected_lane: Lane):
        ext = TaskContractExtension(task_type=task_type, risk_tier=risk_tier)
        assert ext.lane == expected_lane, (
            f"Expected lane={expected_lane.value!r} for "
            f"({task_type.value!r}, {risk_tier.value!r}), got {ext.lane!r}"
        )

    @pytest.mark.parametrize(
        "task_type,risk_tier",
        [(tt, rt) for tt in TaskType for rt in RiskTier],
    )
    def test_requires_human_approval_consistent_with_lane(
        self, task_type: TaskType, risk_tier: RiskTier
    ):
        ext = TaskContractExtension(task_type=task_type, risk_tier=risk_tier)
        expected = ext.lane != Lane.autonomous
        assert ext.requires_human_approval is expected, (
            f"requires_human_approval mismatch for ({task_type.value}, {risk_tier.value}): "
            f"lane={ext.lane}, requires_human_approval={ext.requires_human_approval}"
        )


# ===========================================================================
# TaskContractExtension — policy invariants
# ===========================================================================


class TestTaskContractExtensionInvariants:
    """Key policy invariants must hold when expressed through the contract model."""

    def test_no_task_type_autonomous_at_critical(self):
        for task_type in TaskType:
            ext = TaskContractExtension(task_type=task_type, risk_tier=RiskTier.critical)
            assert ext.lane != Lane.autonomous, (
                f"{task_type.value} must not be autonomous at critical risk"
            )

    def test_security_change_never_autonomous(self):
        for risk_tier in RiskTier:
            ext = TaskContractExtension(task_type=TaskType.security_change, risk_tier=risk_tier)
            assert ext.lane != Lane.autonomous, (
                f"security-change must not be autonomous at {risk_tier.value}"
            )

    def test_deployment_always_blocked_at_critical(self):
        ext = TaskContractExtension(task_type=TaskType.deployment, risk_tier=RiskTier.critical)
        assert ext.lane == Lane.blocked

    def test_database_migration_blocked_at_high_and_critical(self):
        for risk_tier in (RiskTier.high, RiskTier.critical):
            ext = TaskContractExtension(task_type=TaskType.database_migration, risk_tier=risk_tier)
            assert ext.lane == Lane.blocked, (
                f"database-migration must be blocked at {risk_tier.value}"
            )


# ===========================================================================
# TaskContractExtension — acceptance_checks and evaluator_profile
# ===========================================================================


class TestTaskContractExtensionChecks:
    def test_acceptance_checks_stored(self):
        checks = [
            AcceptanceCheck(name="lint", command="ruff check ."),
            AcceptanceCheck(name="tests", command="pytest"),
        ]
        ext = TaskContractExtension(
            task_type=TaskType.test_writing,
            risk_tier=RiskTier.low,
            acceptance_checks=checks,
        )
        assert len(ext.acceptance_checks) == 2

    def test_evaluator_profile_stored(self):
        profile = EvaluatorProfile(
            checks=[AcceptanceCheck(name="unit-tests", command="pytest -q")],
            auto_approve_on_pass=True,
            max_retries=2,
        )
        ext = TaskContractExtension(
            task_type=TaskType.api_endpoint,
            risk_tier=RiskTier.low,
            evaluator_profile=profile,
        )
        assert ext.evaluator_profile is not None
        assert ext.evaluator_profile.auto_approve_on_pass is True
        assert ext.evaluator_profile.max_retries == 2

    def test_both_acceptance_checks_and_profile(self):
        flat_checks = [AcceptanceCheck(name="smoke", command="curl http://localhost/health")]
        profile = EvaluatorProfile(
            checks=[AcceptanceCheck(name="unit-tests", command="pytest")],
        )
        ext = TaskContractExtension(
            task_type=TaskType.new_feature,
            risk_tier=RiskTier.medium,
            acceptance_checks=flat_checks,
            evaluator_profile=profile,
        )
        assert len(ext.acceptance_checks) == 1
        assert ext.evaluator_profile is not None
        assert len(ext.evaluator_profile.checks) == 1


# ===========================================================================
# TaskContractExtension — serialization round-trip
# ===========================================================================


class TestTaskContractExtensionSerialization:
    def test_round_trip_fully_classified(self):
        original = TaskContractExtension(
            risk_tier=RiskTier.medium,
            task_type=TaskType.api_endpoint,
            acceptance_checks=[
                AcceptanceCheck(name="lint", command="ruff check .", required=True),
                AcceptanceCheck(name="tests", command="pytest -q", required=True),
            ],
            evaluator_profile=EvaluatorProfile(
                checks=[AcceptanceCheck(name="all-tests", command="pytest")],
                auto_approve_on_pass=False,
                max_retries=1,
            ),
        )
        data = original.model_dump()
        restored = TaskContractExtension.model_validate(data)

        assert restored.risk_tier == original.risk_tier
        assert restored.task_type == original.task_type
        assert restored.lane == original.lane
        assert restored.requires_human_approval == original.requires_human_approval
        assert len(restored.acceptance_checks) == len(original.acceptance_checks)
        assert restored.evaluator_profile is not None

    def test_round_trip_unclassified(self):
        original = TaskContractExtension()
        data = original.model_dump()
        restored = TaskContractExtension.model_validate(data)
        assert restored.lane is None
        assert restored.requires_human_approval is False

    def test_json_string_round_trip(self):
        original = TaskContractExtension(
            risk_tier=RiskTier.high,
            task_type=TaskType.security_change,
        )
        json_str = original.model_dump_json()
        restored = TaskContractExtension.model_validate_json(json_str)
        assert restored.lane == Lane.blocked
        assert restored.requires_human_approval is True

    def test_model_dump_mode_json(self):
        ext = TaskContractExtension(
            risk_tier=RiskTier.low,
            task_type=TaskType.test_writing,
        )
        data = ext.model_dump(mode="json")
        # Enum values should be their string equivalents
        assert data["risk_tier"] == "low"
        assert data["task_type"] == "test-writing"
        assert data["lane"] == "autonomous"
        assert data["requires_human_approval"] is False

    def test_serialized_lane_is_string_value(self):
        """JSON output must use Lane string values, not enum member names."""
        ext = TaskContractExtension(
            risk_tier=RiskTier.medium,
            task_type=TaskType.deployment,
        )
        data = ext.model_dump(mode="json")
        assert data["lane"] == "human-review"

    def test_computed_fields_survive_revalidation(self):
        """Computed fields must remain correct after a second model_validate call."""
        ext = TaskContractExtension(
            task_type=TaskType.database_migration,
            risk_tier=RiskTier.high,
        )
        # Serialize and re-validate twice to catch any caching or mutation bugs.
        for _ in range(2):
            data = ext.model_dump()
            ext = TaskContractExtension.model_validate(data)

        assert ext.lane == Lane.blocked
        assert ext.requires_human_approval is True


# ===========================================================================
# Public re-export via models.__init__
# ===========================================================================


class TestPublicReExport:
    """Verify the three classes are accessible from the package __init__."""

    def test_acceptance_check_importable_from_models(self):
        from forge_harness.webhook_server.models import AcceptanceCheck as AcceptanceCheckAlias

        assert AcceptanceCheckAlias is AcceptanceCheck

    def test_evaluator_profile_importable_from_models(self):
        from forge_harness.webhook_server.models import EvaluatorProfile as EvaluatorProfileAlias

        assert EvaluatorProfileAlias is EvaluatorProfile

    def test_task_contract_extension_importable_from_models(self):
        from forge_harness.webhook_server.models import (
            TaskContractExtension as TaskContractExtensionAlias,
        )

        assert TaskContractExtensionAlias is TaskContractExtension

    def test_all_in_models_all(self):
        import forge_harness.webhook_server.models as m

        assert "AcceptanceCheck" in m.__all__
        assert "EvaluatorProfile" in m.__all__
        assert "TaskContractExtension" in m.__all__
