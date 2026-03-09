"""Integration tests for Dark Factory module (DF-4001 + DF-4002).

Tests the integration between LanePolicyEnforcer and HumanGatePolicy classes.

Coverage:
- LanePolicyEnforcer + HumanGatePolicy work together
- Low-risk lane auto-completes without human gate
- High-risk lane triggers human gate
- Lane risk level classification
- Policy configuration loading
- Override mechanism integration
- Audit log integration
"""

import pytest

from forge_harness.dark_factory import (
    GateResult,
    GateStatus,
    HumanGatePolicy,
    HumanGateViolationError,
    LanePolicy,
    LanePolicyEnforcer,
    PolicyDecision,
    get_human_gate_policy,
    get_lane_policy_enforcer,
    reset_human_gate_policy,
    reset_lane_policy_enforcer,
)
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import WorkCellLane


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset both singletons before each test."""
    reset_lane_policy_enforcer()
    reset_human_gate_policy()
    yield
    reset_lane_policy_enforcer()
    reset_human_gate_policy()


class TestLanePolicyEnforcerBasics:
    """Basic LanePolicyEnforcer functionality tests."""

    def test_can_auto_complete_docs_lane(self):
        """Docs lane should allow auto-complete."""
        enforcer = get_lane_policy_enforcer()
        task = {
            "task_type": TaskType.docs_update,
            "risk_tier": RiskTier.low,
        }
        assert enforcer.can_auto_complete(task) is True

    def test_can_auto_complete_test_lane(self):
        """Test writing lane should allow auto-complete for low risk."""
        enforcer = get_lane_policy_enforcer()
        task = {
            "task_type": TaskType.test_writing,
            "risk_tier": RiskTier.low,
        }
        assert enforcer.can_auto_complete(task) is True

    def test_requires_human_review_api_stateful(self):
        """API endpoint lane requires human review."""
        enforcer = get_lane_policy_enforcer()
        task = {
            "task_type": TaskType.api_endpoint,
            "risk_tier": RiskTier.medium,
        }
        assert enforcer.requires_human_review(task) is True

    def test_requires_human_review_research(self):
        """Refactor lane requires human review at medium risk."""
        enforcer = get_lane_policy_enforcer()
        task = {
            "task_type": TaskType.code_refactor,
            "risk_tier": RiskTier.medium,
        }
        assert enforcer.requires_human_review(task) is True

    def test_is_hard_gated_security_change(self):
        """Security change lane is hard-gated."""
        policy = enforcer = get_lane_policy_enforcer()
        lane = WorkCellLane.security_change
        policy = enforcer.get_lane_policy(lane)
        assert policy.is_hard_gated is True
        assert policy.requires_human_review is True

    def test_is_hard_gated_deployment(self):
        """Deployment lane is hard-gated."""
        enforcer = get_lane_policy_enforcer()
        lane = WorkCellLane.deployment
        policy = enforcer.get_lane_policy(lane)
        assert policy.is_hard_gated is True
        assert policy.requires_human_review is True

    def test_evaluate_returns_policy_decision(self):
        """Evaluate returns full PolicyDecision."""
        enforcer = get_lane_policy_enforcer()
        decision = enforcer.evaluate(
            task_id="test-task-1",
            task_type=TaskType.test_writing,
            risk_tier=RiskTier.low,
        )
        assert isinstance(decision, PolicyDecision)
        assert decision.task_id == "test-task-1"
        assert decision.can_auto_complete is True
        assert decision.is_hard_gated is False


class TestHumanGatePolicyBasics:
    """Basic HumanGatePolicy functionality tests."""

    def test_not_gated_lane_passes(self):
        """Non-gated lane passes without approval."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "test-1",
            "task_type": TaskType.test_writing,
            "risk_tier": RiskTier.low,
        }
        result = gate.enforce(task)
        assert result.status == GateStatus.not_gated
        assert result.may_proceed is True

    def test_gated_lane_without_approval_raises(self):
        """Gated lane without approval raises violation."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "test-2",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }
        with pytest.raises(HumanGateViolationError) as exc_info:
            gate.enforce(task)
        assert exc_info.value.task_id == "test-2"

    def test_gated_lane_with_approval_passes(self):
        """Gated lane with approval passes."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "test-3",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }
        result = gate.enforce(task, approved=True)
        assert result.status == GateStatus.approved
        assert result.may_proceed is True


class TestIntegrationLanePolicyAndHumanGate:
    """Integration tests between LanePolicyEnforcer and HumanGatePolicy."""

    def test_low_risk_autonomous_flow(self):
        """Low risk task in autonomous lane completes without human gate."""
        enforcer = get_lane_policy_enforcer()
        gate = get_human_gate_policy()

        task = {
            "task_id": "int-1",
            "task_type": TaskType.test_writing,
            "risk_tier": RiskTier.low,
        }

        can_auto = enforcer.can_auto_complete(task)
        assert can_auto is True

        result = gate.enforce(task)
        assert result.status == GateStatus.not_gated
        assert result.may_proceed is True

    def test_medium_risk_human_review_flow(self):
        """Medium risk task requires human review but is not hard-gated."""
        enforcer = get_lane_policy_enforcer()
        gate = get_human_gate_policy()

        task = {
            "task_id": "int-2",
            "task_type": TaskType.api_endpoint,
            "risk_tier": RiskTier.medium,
        }

        requires_review = enforcer.requires_human_review(task)
        assert requires_review is True

        result = gate.enforce(task)
        assert result.status == GateStatus.not_gated
        assert result.may_proceed is True

    def test_high_risk_hard_gated_blocks_without_override(self):
        """High risk in gated lane blocks without override."""
        enforcer = get_lane_policy_enforcer()
        gate = get_human_gate_policy()

        task = {
            "task_id": "int-3",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }

        decision = enforcer.evaluate(
            task_id="int-3",
            task_type=TaskType.security_change,
            risk_tier=RiskTier.high,
        )
        assert decision.is_hard_gated is True

        with pytest.raises(HumanGateViolationError):
            gate.enforce(task)

    def test_security_change_hard_gated_blocks_without_approval(self):
        """Security change lane blocks without approval."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "sec-1",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.low,
        }
        with pytest.raises(HumanGateViolationError):
            gate.enforce(task)

    def test_security_change_with_approval_passes(self):
        """Security change with approval passes."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "sec-2",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.low,
        }
        result = gate.enforce(task, approved=True)
        assert result.status == GateStatus.approved


class TestOverrideMechanism:
    """Tests for override mechanism integration."""

    def test_override_valid_reason_permitted(self):
        """Valid override with proper reason is permitted."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "override-1",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }
        result = gate.enforce(
            task,
            force_override=True,
            override_reason="Time-critical security patch required by CISO",
            overrider_id="alice@example.com",
        )
        assert result.status == GateStatus.override_permitted
        assert result.may_proceed is True

    def test_override_short_reason_rejected(self):
        """Override with too short reason is rejected."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "override-2",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                task,
                force_override=True,
                override_reason="Too short",
                overrider_id="bob@example.com",
            )

    def test_override_missing_reason_rejected(self):
        """Override without reason is rejected."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "override-3",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                task,
                force_override=True,
                override_reason="",
                overrider_id="carol@example.com",
            )

    def test_override_missing_overrider_rejected(self):
        """Override without overrider_id is rejected."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "override-4",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                task,
                force_override=True,
                override_reason="Emergency fix required for production issue",
                overrider_id="",
            )


class TestAuditLogIntegration:
    """Tests for audit log integration between components."""

    def test_audit_log_records_not_gated(self):
        """Audit log records non-gated lane access."""
        gate = get_human_gate_policy()
        task = {"task_id": "audit-1", "task_type": TaskType.docs_update, "risk_tier": RiskTier.low}
        gate.enforce(task)

        log = gate.get_audit_log()
        assert len(log) == 1
        assert log[0].task_id == "audit-1"
        assert log[0].status == GateStatus.not_gated

    def test_audit_log_records_approved(self):
        """Audit log records approval."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "audit-2",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }
        gate.enforce(task, approved=True)

        log = gate.get_audit_log()
        assert len(log) == 1
        assert log[0].status == GateStatus.approved

    def test_audit_log_records_override(self):
        """Audit log records override."""
        gate = get_human_gate_policy()
        task = {
            "task_id": "audit-3",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }
        gate.enforce(
            task,
            force_override=True,
            override_reason="Emergency hotfix for critical bug",
            overrider_id="dave@example.com",
        )

        log = gate.get_audit_log()
        assert len(log) == 1
        assert log[0].status == GateStatus.override_permitted
        assert log[0].override_reason == "Emergency hotfix for critical bug"
        assert log[0].overrider_id == "dave@example.com"

    def test_get_audit_log_for_task_filters(self):
        """get_audit_log_for_task filters correctly."""
        gate = get_human_gate_policy()
        task1 = {
            "task_id": "audit-filter-1",
            "task_type": TaskType.docs_update,
            "risk_tier": RiskTier.low,
        }
        task2 = {
            "task_id": "audit-filter-2",
            "task_type": TaskType.test_writing,
            "risk_tier": RiskTier.low,
        }

        gate.enforce(task1)
        gate.enforce(task2)

        task1_log = gate.get_audit_log_for_task("audit-filter-1")
        assert len(task1_log) == 1
        assert task1_log[0].task_id == "audit-filter-1"


class TestPolicyConfiguration:
    """Tests for policy configuration loading and updates."""

    def test_default_config_loaded(self):
        """Default config is loaded on initialization."""
        enforcer = get_lane_policy_enforcer()
        config = enforcer.get_config_snapshot()

        assert WorkCellLane.docs.value in config
        assert WorkCellLane.security_change.value in config
        assert config[WorkCellLane.docs.value]["can_auto_complete"] is True

    def test_update_config_modifies_policy(self):
        """update_config modifies policy correctly."""
        enforcer = get_lane_policy_enforcer()

        original_policy = enforcer.get_lane_policy(WorkCellLane.test_writing)
        assert original_policy.can_auto_complete is True

        enforcer.update_config(
            {
                WorkCellLane.test_writing.value: {
                    "can_auto_complete": False,
                    "requires_human_review": True,
                    "is_hard_gated": False,
                }
            }
        )

        new_policy = enforcer.get_lane_policy(WorkCellLane.test_writing)
        assert new_policy.can_auto_complete is False
        assert new_policy.requires_human_review is True

    def test_reset_restores_default_config(self):
        """reset restores default configuration."""
        enforcer = get_lane_policy_enforcer()
        enforcer.update_config(
            {
                WorkCellLane.docs.value: {
                    "can_auto_complete": False,
                    "requires_human_review": True,
                    "is_hard_gated": True,
                }
            }
        )

        enforcer.reset()

        policy = enforcer.get_lane_policy(WorkCellLane.docs)
        assert policy.can_auto_complete is True


class TestLaneRiskClassification:
    """Tests for lane risk level classification."""

    def test_low_risk_autonomous_classification(self):
        """Low risk tasks are classified as autonomous."""
        enforcer = get_lane_policy_enforcer()

        for task_type in [TaskType.docs_update, TaskType.test_writing, TaskType.api_endpoint]:
            task = {"task_type": task_type, "risk_tier": RiskTier.low}
            assert enforcer.can_auto_complete(task) is True

    def test_medium_risk_human_review_classification(self):
        """Medium risk tasks require human review."""
        enforcer = get_lane_policy_enforcer()

        for task_type in [TaskType.api_endpoint, TaskType.code_refactor]:
            task = {"task_type": task_type, "risk_tier": RiskTier.medium}
            assert enforcer.requires_human_review(task) is True

    def test_high_risk_hard_gated_classification(self):
        """High risk tasks are hard-gated."""
        enforcer = get_lane_policy_enforcer()

        decision = enforcer.evaluate(
            task_id="risk-1",
            task_type=TaskType.security_change,
            risk_tier=RiskTier.high,
        )
        assert decision.is_hard_gated is True


class TestWorkCellLaneResolution:
    """Tests for WorkCellLane resolution."""

    def test_resolve_docs_lane(self):
        """Docs task resolves to docs lane."""
        enforcer = get_lane_policy_enforcer()
        decision = enforcer.evaluate(
            task_id="resolve-1",
            task_type=TaskType.docs_update,
            risk_tier=RiskTier.low,
        )
        assert decision.work_cell_lane == WorkCellLane.docs

    def test_resolve_test_writing_lane(self):
        """Test writing task resolves to test_writing lane."""
        enforcer = get_lane_policy_enforcer()
        decision = enforcer.evaluate(
            task_id="resolve-2",
            task_type=TaskType.test_writing,
            risk_tier=RiskTier.low,
        )
        assert decision.work_cell_lane == WorkCellLane.test_writing

    def test_resolve_api_simple_lane(self):
        """API endpoint task resolves to api_simple lane for low risk."""
        enforcer = get_lane_policy_enforcer()
        decision = enforcer.evaluate(
            task_id="resolve-3",
            task_type=TaskType.api_endpoint,
            risk_tier=RiskTier.low,
        )
        assert decision.work_cell_lane == WorkCellLane.api_simple


class TestCustomGatedLanes:
    """Tests for custom hard-gated lane configuration."""

    def test_add_gated_lane(self):
        """Can add a lane to hard-gated set."""
        gate = get_human_gate_policy()
        gate.add_gated_lane(WorkCellLane.api_simple)

        assert gate.is_lane_gated(WorkCellLane.api_simple) is True

        task = {
            "task_id": "custom-1",
            "task_type": TaskType.api_endpoint,
            "risk_tier": RiskTier.low,
        }
        with pytest.raises(HumanGateViolationError):
            gate.enforce(task)

    def test_remove_gated_lane(self):
        """Can remove a lane from hard-gated set."""
        gate = get_human_gate_policy()
        gate.remove_gated_lane(WorkCellLane.security_change)

        assert gate.is_lane_gated(WorkCellLane.security_change) is False


class TestEndToEndScenarios:
    """End-to-end scenario tests."""

    def test_complete_autonomous_workflow(self):
        """Complete workflow for autonomous task."""
        enforcer = get_lane_policy_enforcer()
        gate = get_human_gate_policy()

        task = {
            "task_id": "e2e-1",
            "task_type": TaskType.test_writing,
            "risk_tier": RiskTier.low,
        }

        can_auto = enforcer.can_auto_complete(task)
        assert can_auto is True

        gate_result = gate.enforce(task)
        assert gate_result.may_proceed is True

        decision = enforcer.evaluate(
            task_id="e2e-1",
            task_type=TaskType.test_writing,
            risk_tier=RiskTier.low,
        )
        assert decision.can_auto_complete is True
        assert decision.is_hard_gated is False

    def test_complete_human_review_workflow(self):
        """Complete workflow for human-review task."""
        enforcer = get_lane_policy_enforcer()
        gate = get_human_gate_policy()

        task = {
            "task_id": "e2e-2",
            "task_type": TaskType.api_endpoint,
            "risk_tier": RiskTier.medium,
        }

        requires_review = enforcer.requires_human_review(task)
        assert requires_review is True

        gate_result = gate.enforce(task, approved=True)
        assert gate_result.may_proceed is True

    def test_security_release_workflow(self):
        """Complete workflow for security release."""
        enforcer = get_lane_policy_enforcer()
        gate = get_human_gate_policy()

        task = {
            "task_id": "e2e-3",
            "task_type": TaskType.security_change,
            "risk_tier": RiskTier.high,
        }

        decision = enforcer.evaluate(
            task_id="e2e-3",
            task_type=TaskType.security_change,
            risk_tier=RiskTier.high,
        )
        assert decision.is_hard_gated is True

        with pytest.raises(HumanGateViolationError):
            gate.enforce(task)

        approved_result = gate.enforce(task, approved=True)
        assert approved_result.may_proceed is True
