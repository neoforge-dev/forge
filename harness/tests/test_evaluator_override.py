"""
Evaluator Override Protocol Tests
==================================

Validates:
- EvaluatorOverride model field validation and defaults.
- OverriderRole and OverrideType enum round-trips.
- OverridePolicy enforcement rules for each role/tier/lane combination.
- Two-person approval (2PA) enforcement for critical-risk overrides.
- Blocked-lane role restrictions.
- Audit trail completeness (all required fields present and serialisable).
- Edge cases: missing fields, invalid enum values, risk_acknowledged=False.

Target: 40+ tests, 100% model and policy coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest
from pydantic import ValidationError

from forge_harness.webhook_server.models.evaluator_override import (
    EvaluatorOverride,
    OverridePolicy,
    OverriderRole,
    OverrideType,
    OverrideViolationError,
)
from forge_harness.webhook_server.models.lane_policy import Lane, RiskTier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REASON = "Evaluator check failed due to known CI outage (INC-1234). Output verified manually."
_SHORT_REASON = "Too short"  # < 20 chars — must fail


def _make_override(**kwargs) -> EvaluatorOverride:
    """Build a minimal valid EvaluatorOverride, merging *kwargs* over defaults."""
    defaults = dict(
        task_id="task-001",
        overrider_id="ops-alice",
        reason=_REASON,
        risk_acknowledged=True,
        override_type=OverrideType.force_complete,
        lane_at_time=Lane.autonomous.value,
        role=OverriderRole.operator,
        task_risk_tier=RiskTier.low,
    )
    defaults.update(kwargs)
    return EvaluatorOverride(**defaults)


# ---------------------------------------------------------------------------
# 1. Enum value tests
# ---------------------------------------------------------------------------


class TestOverrideTypeEnum:
    def test_force_complete_value(self) -> None:
        assert OverrideType.force_complete.value == "force_complete"

    def test_force_requeue_value(self) -> None:
        assert OverrideType.force_requeue.value == "force_requeue"

    def test_bypass_checks_value(self) -> None:
        assert OverrideType.bypass_checks.value == "bypass_checks"

    def test_all_three_types_present(self) -> None:
        values = {t.value for t in OverrideType}
        assert values == {"force_complete", "force_requeue", "bypass_checks"}

    def test_from_string_force_complete(self) -> None:
        assert OverrideType("force_complete") == OverrideType.force_complete

    def test_from_string_force_requeue(self) -> None:
        assert OverrideType("force_requeue") == OverrideType.force_requeue

    def test_invalid_override_type_raises(self) -> None:
        with pytest.raises(ValueError):
            OverrideType("invalid_type")


class TestOverriderRoleEnum:
    def test_operator_value(self) -> None:
        assert OverriderRole.operator.value == "operator"

    def test_lead_agent_value(self) -> None:
        assert OverriderRole.lead_agent.value == "lead-agent"

    def test_senior_dev_value(self) -> None:
        assert OverriderRole.senior_dev.value == "senior-dev"

    def test_all_three_roles_present(self) -> None:
        values = {r.value for r in OverriderRole}
        assert values == {"operator", "lead-agent", "senior-dev"}

    def test_from_string_operator(self) -> None:
        assert OverriderRole("operator") == OverriderRole.operator

    def test_from_string_lead_agent(self) -> None:
        assert OverriderRole("lead-agent") == OverriderRole.lead_agent

    def test_from_string_senior_dev(self) -> None:
        assert OverriderRole("senior-dev") == OverriderRole.senior_dev


# ---------------------------------------------------------------------------
# 2. EvaluatorOverride model — valid construction
# ---------------------------------------------------------------------------


class TestEvaluatorOverrideValidConstruction:
    def test_minimal_valid_override(self) -> None:
        ov = _make_override()
        assert ov.task_id == "task-001"
        assert ov.overrider_id == "ops-alice"
        assert ov.risk_acknowledged is True
        assert ov.override_type == OverrideType.force_complete
        assert ov.lane_at_time == "autonomous"
        assert ov.role == OverriderRole.operator

    def test_timestamp_defaults_to_utc_now(self) -> None:
        before = datetime.now(UTC)
        ov = _make_override()
        after = datetime.now(UTC)
        assert before <= ov.timestamp <= after

    def test_explicit_timestamp_accepted(self) -> None:
        ts = datetime(2026, 2, 22, 10, 0, 0, tzinfo=UTC)
        ov = _make_override(timestamp=ts)
        assert ov.timestamp == ts

    def test_optional_fields_default_to_none(self) -> None:
        ov = _make_override()
        assert ov.cosigner_id is None
        assert ov.ticket_ref is None

    def test_cosigner_id_accepted(self) -> None:
        ov = _make_override(
            task_risk_tier=RiskTier.critical,
            role=OverriderRole.senior_dev,
            cosigner_id="sdev-bob",
        )
        assert ov.cosigner_id == "sdev-bob"

    def test_ticket_ref_accepted(self) -> None:
        ov = _make_override(ticket_ref="FORGE#999")
        assert ov.ticket_ref == "FORGE#999"

    def test_task_risk_tier_optional(self) -> None:
        ov = _make_override(task_risk_tier=None)
        assert ov.task_risk_tier is None

    def test_all_override_types_accepted(self) -> None:
        for ot in OverrideType:
            ov = _make_override(override_type=ot)
            assert ov.override_type == ot

    def test_all_roles_accepted(self) -> None:
        for role in OverriderRole:
            ov = _make_override(role=role)
            assert ov.role == role

    def test_all_valid_lanes_accepted(self) -> None:
        for lane in Lane:
            ov = _make_override(lane_at_time=lane.value)
            assert ov.lane_at_time == lane.value

    def test_model_dump_produces_json_safe_dict(self) -> None:
        ov = _make_override()
        data = ov.model_dump(mode="json")
        assert isinstance(data, dict)
        assert data["task_id"] == "task-001"
        assert data["risk_acknowledged"] is True
        assert data["override_type"] == "force_complete"


# ---------------------------------------------------------------------------
# 3. EvaluatorOverride model — validation failures
# ---------------------------------------------------------------------------


class TestEvaluatorOverrideValidationFailures:
    def test_missing_task_id_raises(self) -> None:
        with pytest.raises(ValidationError, match="task_id"):
            EvaluatorOverride(
                overrider_id="alice",
                reason=_REASON,
                risk_acknowledged=True,
                override_type=OverrideType.force_complete,
                lane_at_time="autonomous",
                role=OverriderRole.operator,
            )

    def test_missing_overrider_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            EvaluatorOverride(
                task_id="t-1",
                reason=_REASON,
                risk_acknowledged=True,
                override_type=OverrideType.force_complete,
                lane_at_time="autonomous",
                role=OverriderRole.operator,
            )

    def test_missing_reason_raises(self) -> None:
        with pytest.raises(ValidationError):
            EvaluatorOverride(
                task_id="t-1",
                overrider_id="alice",
                risk_acknowledged=True,
                override_type=OverrideType.force_complete,
                lane_at_time="autonomous",
                role=OverriderRole.operator,
            )

    def test_reason_too_short_raises(self) -> None:
        with pytest.raises(ValidationError, match="20"):
            _make_override(reason=_SHORT_REASON)

    def test_risk_acknowledged_false_raises(self) -> None:
        with pytest.raises(ValidationError, match="risk_acknowledged"):
            _make_override(risk_acknowledged=False)

    def test_invalid_lane_at_time_raises(self) -> None:
        with pytest.raises(ValidationError, match="lane"):
            _make_override(lane_at_time="not-a-lane")

    def test_invalid_override_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_override(override_type="teleport")

    def test_invalid_role_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_override(role="god-mode")

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_override(task_id="")

    def test_empty_overrider_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_override(overrider_id="")

    def test_critical_risk_without_cosigner_raises(self) -> None:
        """Model-level 2PA enforcement for critical risk."""
        with pytest.raises(ValidationError, match="co-signer|cosigner"):
            _make_override(
                task_risk_tier=RiskTier.critical,
                role=OverriderRole.senior_dev,
                cosigner_id=None,
            )

    def test_missing_override_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            EvaluatorOverride(
                task_id="t-1",
                overrider_id="alice",
                reason=_REASON,
                risk_acknowledged=True,
                lane_at_time="autonomous",
                role=OverriderRole.operator,
            )


# ---------------------------------------------------------------------------
# 4. OverridePolicy — operator role
# ---------------------------------------------------------------------------


class TestOverridePolicyOperator:
    def test_operator_low_risk_autonomous_allowed(self) -> None:
        ov = _make_override(role=OverriderRole.operator, task_risk_tier=RiskTier.low)
        OverridePolicy.validate_override(ov)  # Must not raise

    def test_operator_low_risk_human_review_allowed(self) -> None:
        ov = _make_override(
            role=OverriderRole.operator,
            task_risk_tier=RiskTier.low,
            lane_at_time=Lane.human_review.value,
        )
        OverridePolicy.validate_override(ov)

    def test_operator_medium_risk_rejected(self) -> None:
        ov = _make_override(role=OverriderRole.operator, task_risk_tier=RiskTier.medium)
        with pytest.raises(OverrideViolationError, match="operator"):
            OverridePolicy.validate_override(ov)

    def test_operator_high_risk_rejected(self) -> None:
        ov = _make_override(role=OverriderRole.operator, task_risk_tier=RiskTier.high)
        with pytest.raises(OverrideViolationError):
            OverridePolicy.validate_override(ov)

    def test_operator_blocked_lane_rejected(self) -> None:
        ov = _make_override(
            role=OverriderRole.operator,
            task_risk_tier=RiskTier.low,
            lane_at_time=Lane.blocked.value,
        )
        with pytest.raises(OverrideViolationError, match="blocked"):
            OverridePolicy.validate_override(ov)

    def test_operator_is_permitted_returns_false_for_medium(self) -> None:
        ov = _make_override(role=OverriderRole.operator, task_risk_tier=RiskTier.medium)
        assert OverridePolicy.is_permitted(ov) is False

    def test_operator_is_permitted_returns_true_for_low(self) -> None:
        ov = _make_override(role=OverriderRole.operator, task_risk_tier=RiskTier.low)
        assert OverridePolicy.is_permitted(ov) is True


# ---------------------------------------------------------------------------
# 5. OverridePolicy — lead-agent role
# ---------------------------------------------------------------------------


class TestOverridePolicyLeadAgent:
    def test_lead_agent_low_risk_allowed(self) -> None:
        ov = _make_override(role=OverriderRole.lead_agent, task_risk_tier=RiskTier.low)
        OverridePolicy.validate_override(ov)

    def test_lead_agent_medium_risk_allowed(self) -> None:
        ov = _make_override(role=OverriderRole.lead_agent, task_risk_tier=RiskTier.medium)
        OverridePolicy.validate_override(ov)

    def test_lead_agent_high_risk_rejected(self) -> None:
        ov = _make_override(role=OverriderRole.lead_agent, task_risk_tier=RiskTier.high)
        with pytest.raises(OverrideViolationError, match="lead-agent"):
            OverridePolicy.validate_override(ov)

    def test_lead_agent_critical_risk_rejected(self) -> None:
        ov = _make_override(
            role=OverriderRole.lead_agent,
            task_risk_tier=RiskTier.critical,
            cosigner_id="sdev-bob",  # Has cosigner but wrong role
        )
        with pytest.raises(OverrideViolationError):
            OverridePolicy.validate_override(ov)

    def test_lead_agent_blocked_lane_rejected(self) -> None:
        ov = _make_override(
            role=OverriderRole.lead_agent,
            task_risk_tier=RiskTier.low,
            lane_at_time=Lane.blocked.value,
        )
        with pytest.raises(OverrideViolationError, match="blocked"):
            OverridePolicy.validate_override(ov)

    def test_lead_agent_blocked_lane_medium_risk_rejected(self) -> None:
        ov = _make_override(
            role=OverriderRole.lead_agent,
            task_risk_tier=RiskTier.medium,
            lane_at_time=Lane.blocked.value,
        )
        with pytest.raises(OverrideViolationError):
            OverridePolicy.validate_override(ov)


# ---------------------------------------------------------------------------
# 6. OverridePolicy — senior-dev role
# ---------------------------------------------------------------------------


class TestOverridePolicySeniorDev:
    def test_senior_dev_low_risk_autonomous_allowed(self) -> None:
        ov = _make_override(role=OverriderRole.senior_dev, task_risk_tier=RiskTier.low)
        OverridePolicy.validate_override(ov)

    def test_senior_dev_medium_risk_allowed(self) -> None:
        ov = _make_override(role=OverriderRole.senior_dev, task_risk_tier=RiskTier.medium)
        OverridePolicy.validate_override(ov)

    def test_senior_dev_high_risk_allowed(self) -> None:
        ov = _make_override(role=OverriderRole.senior_dev, task_risk_tier=RiskTier.high)
        OverridePolicy.validate_override(ov)

    def test_senior_dev_critical_risk_with_cosigner_allowed(self) -> None:
        ov = _make_override(
            role=OverriderRole.senior_dev,
            task_risk_tier=RiskTier.critical,
            cosigner_id="sdev-carol",
        )
        OverridePolicy.validate_override(ov)

    def test_senior_dev_critical_risk_without_cosigner_rejected_by_policy(self) -> None:
        """Policy check for 2PA — model validator already catches this but policy is defence-in-depth."""
        # We test directly on the policy method to ensure it also enforces 2PA
        # We bypass model validation by using object.__setattr__
        ov = _make_override(
            role=OverriderRole.senior_dev,
            task_risk_tier=RiskTier.critical,
            cosigner_id="sdev-carol",
        )
        # Simulate a case where cosigner was removed after model validation
        object.__setattr__(ov, "cosigner_id", None)
        with pytest.raises(OverrideViolationError, match="2PA|co-signer|cosigner"):
            OverridePolicy.validate_override(ov)

    def test_senior_dev_blocked_lane_high_risk_allowed(self) -> None:
        ov = _make_override(
            role=OverriderRole.senior_dev,
            task_risk_tier=RiskTier.high,
            lane_at_time=Lane.blocked.value,
        )
        OverridePolicy.validate_override(ov)

    def test_senior_dev_blocked_lane_low_risk_allowed(self) -> None:
        ov = _make_override(
            role=OverriderRole.senior_dev,
            task_risk_tier=RiskTier.low,
            lane_at_time=Lane.blocked.value,
        )
        OverridePolicy.validate_override(ov)

    def test_senior_dev_blocked_lane_critical_risk_with_cosigner_allowed(self) -> None:
        ov = _make_override(
            role=OverriderRole.senior_dev,
            task_risk_tier=RiskTier.critical,
            lane_at_time=Lane.blocked.value,
            cosigner_id="sdev-dave",
        )
        OverridePolicy.validate_override(ov)


# ---------------------------------------------------------------------------
# 7. OverridePolicy — no task_risk_tier (unclassified tasks)
# ---------------------------------------------------------------------------


class TestOverridePolicyNoRiskTier:
    def test_operator_no_risk_tier_allowed(self) -> None:
        """When task_risk_tier is None the tier check is skipped."""
        ov = _make_override(role=OverriderRole.operator, task_risk_tier=None)
        OverridePolicy.validate_override(ov)

    def test_lead_agent_no_risk_tier_allowed(self) -> None:
        ov = _make_override(role=OverriderRole.lead_agent, task_risk_tier=None)
        OverridePolicy.validate_override(ov)

    def test_no_risk_tier_blocked_lane_operator_still_rejected(self) -> None:
        """No risk tier does not bypass the blocked-lane role restriction."""
        ov = _make_override(
            role=OverriderRole.operator,
            task_risk_tier=None,
            lane_at_time=Lane.blocked.value,
        )
        with pytest.raises(OverrideViolationError, match="blocked"):
            OverridePolicy.validate_override(ov)


# ---------------------------------------------------------------------------
# 8. OverridePolicy — risk_acknowledged defence-in-depth
# ---------------------------------------------------------------------------


class TestOverridePolicyRiskAcknowledged:
    def test_risk_not_acknowledged_rejected_by_policy(self) -> None:
        """Policy re-checks risk_acknowledged even though model also enforces it."""
        ov = _make_override()
        # Bypass model validator
        object.__setattr__(ov, "risk_acknowledged", False)
        with pytest.raises(OverrideViolationError, match="risk_acknowledged"):
            OverridePolicy.validate_override(ov)


# ---------------------------------------------------------------------------
# 9. Audit trail completeness
# ---------------------------------------------------------------------------


class TestAuditTrailCompleteness:
    def test_model_dump_contains_all_required_audit_fields(self) -> None:
        ov = _make_override()
        data = ov.model_dump(mode="json")
        required_fields = {
            "task_id",
            "overrider_id",
            "reason",
            "risk_acknowledged",
            "override_type",
            "lane_at_time",
            "timestamp",
            "role",
        }
        for field in required_fields:
            assert field in data, f"Missing required audit field: {field}"

    def test_model_dump_timestamp_is_string_in_json_mode(self) -> None:
        ov = _make_override()
        data = ov.model_dump(mode="json")
        assert isinstance(data["timestamp"], str)

    def test_model_dump_override_type_is_string(self) -> None:
        ov = _make_override()
        data = ov.model_dump(mode="json")
        assert data["override_type"] == "force_complete"

    def test_model_dump_role_is_string(self) -> None:
        ov = _make_override()
        data = ov.model_dump(mode="json")
        assert data["role"] == "operator"

    def test_audit_fields_preserved_with_optional_fields(self) -> None:
        ov = _make_override(
            cosigner_id="sdev-x",
            ticket_ref="INC-007",
            task_risk_tier=RiskTier.high,
            role=OverriderRole.senior_dev,
        )
        data = ov.model_dump(mode="json")
        assert data["cosigner_id"] == "sdev-x"
        assert data["ticket_ref"] == "INC-007"
        assert data["task_risk_tier"] == "high"

    def test_model_round_trips_via_json(self) -> None:
        """EvaluatorOverride can be serialised and deserialised losslessly."""
        ov = _make_override(ticket_ref="FORGE#42")
        data = ov.model_dump(mode="json")
        restored = EvaluatorOverride.model_validate(data)
        assert restored.task_id == ov.task_id
        assert restored.override_type == ov.override_type
        assert restored.role == ov.role
        assert restored.ticket_ref == ov.ticket_ref


# ---------------------------------------------------------------------------
# 10. OverrideViolationError exception
# ---------------------------------------------------------------------------


class TestOverrideViolationError:
    def test_violation_str_contains_task_id(self) -> None:
        ov = _make_override(role=OverriderRole.operator, task_risk_tier=RiskTier.medium)
        try:
            OverridePolicy.validate_override(ov)
        except OverrideViolationError as exc:
            assert "task-001" in str(exc)

    def test_violation_exposes_override_attribute(self) -> None:
        ov = _make_override(role=OverriderRole.operator, task_risk_tier=RiskTier.medium)
        try:
            OverridePolicy.validate_override(ov)
        except OverrideViolationError as exc:
            assert exc.override is ov

    def test_violation_exposes_violated_rule(self) -> None:
        ov = _make_override(role=OverriderRole.operator, task_risk_tier=RiskTier.medium)
        try:
            OverridePolicy.validate_override(ov)
        except OverrideViolationError as exc:
            assert isinstance(exc.violated_rule, str)
            assert len(exc.violated_rule) > 0

    def test_violation_is_subclass_of_value_error(self) -> None:
        ov = _make_override(
            role=OverriderRole.operator,
            task_risk_tier=RiskTier.low,
            lane_at_time=Lane.blocked.value,
        )
        with pytest.raises(ValueError):
            OverridePolicy.validate_override(ov)


# ---------------------------------------------------------------------------
# 11. is_permitted boolean helper
# ---------------------------------------------------------------------------


class TestIspermittedHelper:
    def test_returns_true_when_valid(self) -> None:
        ov = _make_override(role=OverriderRole.senior_dev, task_risk_tier=RiskTier.high)
        assert OverridePolicy.is_permitted(ov) is True

    def test_returns_false_when_invalid(self) -> None:
        ov = _make_override(
            role=OverriderRole.operator,
            task_risk_tier=RiskTier.low,
            lane_at_time=Lane.blocked.value,
        )
        assert OverridePolicy.is_permitted(ov) is False

    def test_never_raises_on_valid_override(self) -> None:
        """is_permitted returns bool and never raises, even for a permitted override."""
        ov = _make_override(role=OverriderRole.senior_dev, task_risk_tier=RiskTier.high)
        result = OverridePolicy.is_permitted(ov)
        assert isinstance(result, bool)

    def test_never_raises_on_policy_violation(self) -> None:
        """is_permitted returns False (not raise) when policy is violated."""
        ov = _make_override(
            role=OverriderRole.operator,
            task_risk_tier=RiskTier.low,
            lane_at_time=Lane.blocked.value,
        )
        # Force a policy violation that is NOT caught at the model level
        result = OverridePolicy.is_permitted(ov)
        assert result is False


# ---------------------------------------------------------------------------
# 12. Models __init__ exports
# ---------------------------------------------------------------------------


class TestModelsInitExports:
    def test_evaluator_override_importable_from_models(self) -> None:
        from forge_harness.webhook_server.models import EvaluatorOverride as EvalOverride

        assert EvalOverride is EvaluatorOverride

    def test_override_policy_importable_from_models(self) -> None:
        from forge_harness.webhook_server.models import OverridePolicy as OvPolicy

        assert OvPolicy is OverridePolicy

    def test_override_violation_importable_from_models(self) -> None:
        from forge_harness.webhook_server.models import OverrideViolationError as OvViolation

        assert OvViolation is OverrideViolationError

    def test_overrider_role_importable_from_models(self) -> None:
        from forge_harness.webhook_server.models import OverriderRole as OvRole

        assert OvRole is OverriderRole

    def test_override_type_importable_from_models(self) -> None:
        from forge_harness.webhook_server.models import OverrideType as OvType

        assert OvType is OverrideType
