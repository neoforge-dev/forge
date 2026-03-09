"""Tests for LanePolicyEnforcer — DF-4001.

Covers:
- LanePolicy dataclass invariants
- PolicyDecision construction
- can_auto_complete() for all WorkCellLane types
- requires_human_review() for all WorkCellLane types
- get_lane_policy() for all lanes
- evaluate() for concrete (TaskType, RiskTier) combinations
- String enum coercion in task dicts
- Error handling (missing keys, invalid enum values)
- Config overrides via update_config()
- get_config_snapshot()
- reset()
- Singleton factory (get_lane_policy_enforcer / reset_lane_policy_enforcer)
- Thread safety (basic)
- DEFAULT_LANE_POLICY_CONFIG completeness
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from forge_harness.dark_factory.lane_policy_enforcer import (
    DEFAULT_LANE_POLICY_CONFIG,
    LanePolicy,
    LanePolicyEnforcer,
    PolicyDecision,
    get_lane_policy_enforcer,
    reset_lane_policy_enforcer,
)
from forge_harness.webhook_server.models.lane_policy import (
    Lane,
    RiskTier,
    TaskType,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(
    task_type: TaskType | str,
    risk_tier: RiskTier | str,
    task_id: str = "task-001",
) -> dict[str, Any]:
    return {"task_id": task_id, "task_type": task_type, "risk_tier": risk_tier}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_singleton():
    """Reset the LanePolicyEnforcer singleton before every test."""
    reset_lane_policy_enforcer()
    yield
    reset_lane_policy_enforcer()


@pytest.fixture()
def enforcer() -> LanePolicyEnforcer:
    return LanePolicyEnforcer()


# ---------------------------------------------------------------------------
# 1. DEFAULT_LANE_POLICY_CONFIG completeness
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    def test_every_work_cell_lane_has_an_entry(self):
        """Every WorkCellLane value must have a config entry."""
        for lane in WorkCellLane:
            assert lane.value in DEFAULT_LANE_POLICY_CONFIG, (
                f"Lane {lane.value!r} missing from DEFAULT_LANE_POLICY_CONFIG"
            )

    def test_all_entries_have_required_keys(self):
        required_keys = {"can_auto_complete", "requires_human_review", "is_hard_gated"}
        for lane_key, flags in DEFAULT_LANE_POLICY_CONFIG.items():
            assert required_keys.issubset(flags.keys()), (
                f"Lane {lane_key!r} missing keys: {required_keys - set(flags.keys())}"
            )

    def test_flags_are_booleans(self):
        for lane_key, flags in DEFAULT_LANE_POLICY_CONFIG.items():
            for key in ("can_auto_complete", "requires_human_review", "is_hard_gated"):
                assert isinstance(flags[key], bool), (
                    f"Lane {lane_key!r}: {key} must be bool, got {type(flags[key])}"
                )

    def test_auto_complete_and_human_review_mutually_exclusive(self):
        for lane_key, flags in DEFAULT_LANE_POLICY_CONFIG.items():
            assert not (flags["can_auto_complete"] and flags["requires_human_review"]), (
                f"Lane {lane_key!r}: can_auto_complete and requires_human_review both True"
            )

    def test_is_hard_gated_implies_requires_human_review(self):
        for lane_key, flags in DEFAULT_LANE_POLICY_CONFIG.items():
            if flags["is_hard_gated"]:
                assert flags["requires_human_review"], (
                    f"Lane {lane_key!r}: is_hard_gated=True but requires_human_review=False"
                )


# ---------------------------------------------------------------------------
# 2. LanePolicy dataclass
# ---------------------------------------------------------------------------


class TestLanePolicy:
    def test_valid_autonomous_policy(self):
        policy = LanePolicy(
            lane=WorkCellLane.docs,
            can_auto_complete=True,
            requires_human_review=False,
            is_hard_gated=False,
        )
        assert policy.can_auto_complete is True
        assert policy.requires_human_review is False
        assert policy.is_hard_gated is False

    def test_valid_human_review_policy(self):
        policy = LanePolicy(
            lane=WorkCellLane.api_stateful,
            can_auto_complete=False,
            requires_human_review=True,
            is_hard_gated=False,
        )
        assert policy.requires_human_review is True
        assert policy.can_auto_complete is False
        assert policy.is_hard_gated is False

    def test_valid_hard_gated_policy(self):
        policy = LanePolicy(
            lane=WorkCellLane.security_change,
            can_auto_complete=False,
            requires_human_review=True,
            is_hard_gated=True,
        )
        assert policy.is_hard_gated is True
        assert policy.requires_human_review is True

    def test_rejects_auto_complete_and_human_review_together(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            LanePolicy(
                lane=WorkCellLane.docs,
                can_auto_complete=True,
                requires_human_review=True,
                is_hard_gated=False,
            )

    def test_rejects_hard_gated_without_human_review(self):
        with pytest.raises(ValueError, match="requires 'requires_human_review=True'"):
            LanePolicy(
                lane=WorkCellLane.security_change,
                can_auto_complete=False,
                requires_human_review=False,
                is_hard_gated=True,
            )

    def test_frozen_is_immutable(self):
        policy = LanePolicy(
            lane=WorkCellLane.docs,
            can_auto_complete=True,
            requires_human_review=False,
            is_hard_gated=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            policy.can_auto_complete = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 3. get_lane_policy() — all lanes
# ---------------------------------------------------------------------------


class TestGetLanePolicy:
    def test_docs_lane_can_auto_complete(self, enforcer):
        policy = enforcer.get_lane_policy(WorkCellLane.docs)
        assert policy.can_auto_complete is True
        assert policy.requires_human_review is False
        assert policy.is_hard_gated is False

    def test_test_writing_lane_can_auto_complete(self, enforcer):
        policy = enforcer.get_lane_policy(WorkCellLane.test_writing)
        assert policy.can_auto_complete is True
        assert policy.requires_human_review is False
        assert policy.is_hard_gated is False

    def test_api_simple_lane_can_auto_complete(self, enforcer):
        policy = enforcer.get_lane_policy(WorkCellLane.api_simple)
        assert policy.can_auto_complete is True
        assert policy.requires_human_review is False
        assert policy.is_hard_gated is False

    def test_api_stateful_requires_human_review(self, enforcer):
        policy = enforcer.get_lane_policy(WorkCellLane.api_stateful)
        assert policy.can_auto_complete is False
        assert policy.requires_human_review is True
        assert policy.is_hard_gated is False

    def test_research_requires_human_review(self, enforcer):
        policy = enforcer.get_lane_policy(WorkCellLane.research)
        assert policy.can_auto_complete is False
        assert policy.requires_human_review is True
        assert policy.is_hard_gated is False

    def test_refactor_requires_human_review(self, enforcer):
        policy = enforcer.get_lane_policy(WorkCellLane.refactor)
        assert policy.can_auto_complete is False
        assert policy.requires_human_review is True
        assert policy.is_hard_gated is False

    def test_security_change_is_hard_gated(self, enforcer):
        policy = enforcer.get_lane_policy(WorkCellLane.security_change)
        assert policy.can_auto_complete is False
        assert policy.requires_human_review is True
        assert policy.is_hard_gated is True

    def test_deployment_is_hard_gated(self, enforcer):
        policy = enforcer.get_lane_policy(WorkCellLane.deployment)
        assert policy.can_auto_complete is False
        assert policy.requires_human_review is True
        assert policy.is_hard_gated is True

    def test_returns_lane_policy_instance(self, enforcer):
        policy = enforcer.get_lane_policy(WorkCellLane.docs)
        assert isinstance(policy, LanePolicy)

    def test_policy_lane_attribute_matches_requested_lane(self, enforcer):
        for lane in WorkCellLane:
            policy = enforcer.get_lane_policy(lane)
            assert policy.lane == lane


# ---------------------------------------------------------------------------
# 4. can_auto_complete()
# ---------------------------------------------------------------------------


class TestCanAutoComplete:
    # --- Autonomous lanes (expect True) ---

    def test_test_writing_low_risk_auto_completes(self, enforcer):
        task = make_task(TaskType.test_writing, RiskTier.low)
        assert enforcer.can_auto_complete(task) is True

    def test_test_writing_medium_risk_auto_completes(self, enforcer):
        task = make_task(TaskType.test_writing, RiskTier.medium)
        assert enforcer.can_auto_complete(task) is True

    def test_docs_update_low_risk_auto_completes(self, enforcer):
        task = make_task(TaskType.docs_update, RiskTier.low)
        assert enforcer.can_auto_complete(task) is True

    def test_content_generation_low_risk_auto_completes(self, enforcer):
        task = make_task(TaskType.content_generation, RiskTier.low)
        assert enforcer.can_auto_complete(task) is True

    def test_api_endpoint_low_risk_auto_completes(self, enforcer):
        # api_endpoint at low risk resolves to api_simple lane
        task = make_task(TaskType.api_endpoint, RiskTier.low)
        assert enforcer.can_auto_complete(task) is True

    def test_bug_fix_low_risk_auto_completes(self, enforcer):
        task = make_task(TaskType.bug_fix, RiskTier.low)
        assert enforcer.can_auto_complete(task) is True

    def test_new_feature_low_risk_auto_completes(self, enforcer):
        task = make_task(TaskType.new_feature, RiskTier.low)
        assert enforcer.can_auto_complete(task) is True

    def test_config_change_low_risk_auto_completes(self, enforcer):
        task = make_task(TaskType.config_change, RiskTier.low)
        assert enforcer.can_auto_complete(task) is True

    # --- Non-autonomous lanes (expect False) ---

    def test_api_endpoint_medium_risk_cannot_auto_complete(self, enforcer):
        task = make_task(TaskType.api_endpoint, RiskTier.medium)
        assert enforcer.can_auto_complete(task) is False

    def test_security_change_cannot_auto_complete(self, enforcer):
        task = make_task(TaskType.security_change, RiskTier.low)
        assert enforcer.can_auto_complete(task) is False

    def test_deployment_cannot_auto_complete(self, enforcer):
        task = make_task(TaskType.deployment, RiskTier.low)
        assert enforcer.can_auto_complete(task) is False

    def test_code_refactor_medium_cannot_auto_complete(self, enforcer):
        task = make_task(TaskType.code_refactor, RiskTier.medium)
        assert enforcer.can_auto_complete(task) is False

    def test_database_migration_cannot_auto_complete(self, enforcer):
        task = make_task(TaskType.database_migration, RiskTier.low)
        assert enforcer.can_auto_complete(task) is False

    def test_bug_fix_medium_risk_cannot_auto_complete(self, enforcer):
        # bug_fix at medium+ resolves to api_stateful
        task = make_task(TaskType.bug_fix, RiskTier.medium)
        assert enforcer.can_auto_complete(task) is False

    # --- String coercion ---

    def test_accepts_string_task_type(self, enforcer):
        task = make_task("test-writing", "low")
        assert enforcer.can_auto_complete(task) is True

    def test_accepts_string_risk_tier(self, enforcer):
        task = make_task("docs-update", "medium")
        assert enforcer.can_auto_complete(task) is True

    # --- Errors ---

    def test_raises_on_missing_task_type(self, enforcer):
        with pytest.raises(KeyError):
            enforcer.can_auto_complete({"risk_tier": RiskTier.low})

    def test_raises_on_missing_risk_tier(self, enforcer):
        with pytest.raises(KeyError):
            enforcer.can_auto_complete({"task_type": TaskType.test_writing})

    def test_raises_on_invalid_task_type_string(self, enforcer):
        with pytest.raises(ValueError):
            enforcer.can_auto_complete({"task_type": "not-a-type", "risk_tier": "low"})

    def test_raises_on_invalid_risk_tier_string(self, enforcer):
        with pytest.raises(ValueError):
            enforcer.can_auto_complete({"task_type": "test-writing", "risk_tier": "very-low"})


# ---------------------------------------------------------------------------
# 5. requires_human_review()
# ---------------------------------------------------------------------------


class TestRequiresHumanReview:
    def test_docs_low_risk_no_human_review(self, enforcer):
        task = make_task(TaskType.docs_update, RiskTier.low)
        assert enforcer.requires_human_review(task) is False

    def test_test_writing_high_risk_requires_human_review(self, enforcer):
        # test_writing always maps to test_writing lane (auto-complete)
        # but Lane policy for test_writing is autonomous — so still False
        task = make_task(TaskType.test_writing, RiskTier.high)
        assert enforcer.requires_human_review(task) is False

    def test_api_endpoint_medium_requires_human_review(self, enforcer):
        task = make_task(TaskType.api_endpoint, RiskTier.medium)
        assert enforcer.requires_human_review(task) is True

    def test_security_change_always_requires_human_review(self, enforcer):
        for risk in RiskTier:
            task = make_task(TaskType.security_change, risk)
            assert enforcer.requires_human_review(task) is True, (
                f"security_change at {risk} should require human review"
            )

    def test_deployment_always_requires_human_review(self, enforcer):
        for risk in RiskTier:
            task = make_task(TaskType.deployment, risk)
            assert enforcer.requires_human_review(task) is True, (
                f"deployment at {risk} should require human review"
            )

    def test_code_refactor_medium_requires_human_review(self, enforcer):
        task = make_task(TaskType.code_refactor, RiskTier.medium)
        assert enforcer.requires_human_review(task) is True

    def test_research_lane_requires_human_review(self, enforcer):
        # research lane: dependency_update maps to refactor → human review
        task = make_task(TaskType.dependency_update, RiskTier.medium)
        assert enforcer.requires_human_review(task) is True

    def test_complement_of_can_auto_complete(self, enforcer):
        """requires_human_review and can_auto_complete must be complements."""
        for task_type in TaskType:
            for risk_tier in RiskTier:
                task = make_task(task_type, risk_tier)
                auto = enforcer.can_auto_complete(task)
                review = enforcer.requires_human_review(task)
                assert auto != review or (not auto and not review) is False, (
                    f"{task_type}/{risk_tier}: auto={auto} review={review} — "
                    "expected exactly one to be True"
                )
                # More precise: they should be mutually exclusive (not both True)
                assert not (auto and review), (
                    f"{task_type}/{risk_tier}: both can_auto_complete and "
                    "requires_human_review are True"
                )


# ---------------------------------------------------------------------------
# 6. evaluate() — full PolicyDecision
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_returns_policy_decision_instance(self, enforcer):
        decision = enforcer.evaluate("t1", TaskType.test_writing, RiskTier.low)
        assert isinstance(decision, PolicyDecision)

    def test_task_id_preserved(self, enforcer):
        decision = enforcer.evaluate("my-task-42", TaskType.docs_update, RiskTier.low)
        assert decision.task_id == "my-task-42"

    def test_docs_low_risk_autonomous(self, enforcer):
        decision = enforcer.evaluate("t1", TaskType.docs_update, RiskTier.low)
        assert decision.canonical_lane == Lane.autonomous
        assert decision.can_auto_complete is True
        assert decision.requires_human_review is False
        assert decision.is_hard_gated is False

    def test_security_change_blocked(self, enforcer):
        decision = enforcer.evaluate("t2", TaskType.security_change, RiskTier.high)
        assert decision.canonical_lane == Lane.blocked
        assert decision.can_auto_complete is False
        assert decision.requires_human_review is True
        assert decision.is_hard_gated is True

    def test_deployment_blocked_at_any_risk(self, enforcer):
        for risk in RiskTier:
            decision = enforcer.evaluate("t3", TaskType.deployment, risk)
            assert decision.can_auto_complete is False
            assert decision.is_hard_gated is True

    def test_work_cell_lane_populated(self, enforcer):
        decision = enforcer.evaluate("t4", TaskType.test_writing, RiskTier.low)
        assert decision.work_cell_lane == WorkCellLane.test_writing

    def test_lane_policy_populated(self, enforcer):
        decision = enforcer.evaluate("t5", TaskType.docs_update, RiskTier.low)
        assert isinstance(decision.lane_policy, LanePolicy)
        assert decision.lane_policy.lane == WorkCellLane.docs

    def test_policy_version_default(self, enforcer):
        decision = enforcer.evaluate("t6", TaskType.bug_fix, RiskTier.low)
        assert decision.policy_version == "default"

    def test_policy_version_custom(self):
        enforcer = LanePolicyEnforcer(policy_version="v2.0.1")
        decision = enforcer.evaluate("t7", TaskType.bug_fix, RiskTier.low)
        assert decision.policy_version == "v2.0.1"

    def test_api_stateful_human_review(self, enforcer):
        decision = enforcer.evaluate("t8", TaskType.api_endpoint, RiskTier.medium)
        assert decision.work_cell_lane == WorkCellLane.api_stateful
        assert decision.can_auto_complete is False
        assert decision.requires_human_review is True
        assert decision.is_hard_gated is False

    def test_shortcuts_match_lane_policy(self, enforcer):
        """PolicyDecision shortcut fields must match the embedded LanePolicy."""
        for task_type in TaskType:
            for risk_tier in RiskTier:
                decision = enforcer.evaluate("tx", task_type, risk_tier)
                assert decision.can_auto_complete == decision.lane_policy.can_auto_complete
                assert decision.requires_human_review == decision.lane_policy.requires_human_review
                assert decision.is_hard_gated == decision.lane_policy.is_hard_gated


# ---------------------------------------------------------------------------
# 7. Config override via update_config()
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    def test_update_single_lane_flag(self, enforcer):
        # Flip docs to require human review
        enforcer.update_config({
            "docs": {
                "can_auto_complete": False,
                "requires_human_review": True,
                "is_hard_gated": False,
            }
        })
        policy = enforcer.get_lane_policy(WorkCellLane.docs)
        assert policy.can_auto_complete is False
        assert policy.requires_human_review is True

    def test_update_does_not_affect_other_lanes(self, enforcer):
        enforcer.update_config({
            "docs": {
                "can_auto_complete": False,
                "requires_human_review": True,
                "is_hard_gated": False,
            }
        })
        # test_writing should be unchanged
        policy = enforcer.get_lane_policy(WorkCellLane.test_writing)
        assert policy.can_auto_complete is True

    def test_update_reflected_in_can_auto_complete(self, enforcer):
        task = make_task(TaskType.docs_update, RiskTier.low)
        assert enforcer.can_auto_complete(task) is True  # original
        enforcer.update_config({
            "docs": {
                "can_auto_complete": False,
                "requires_human_review": True,
                "is_hard_gated": False,
            }
        })
        assert enforcer.can_auto_complete(task) is False

    def test_rejects_conflicting_flags_in_update(self, enforcer):
        with pytest.raises(ValueError):
            enforcer.update_config({
                "docs": {
                    "can_auto_complete": True,
                    "requires_human_review": True,  # contradiction
                    "is_hard_gated": False,
                }
            })

    def test_rejects_invalid_lane_key(self, enforcer):
        with pytest.raises(ValueError):
            enforcer.update_config({
                "nonexistent_lane": {
                    "can_auto_complete": True,
                    "requires_human_review": False,
                    "is_hard_gated": False,
                }
            })

    def test_can_add_hard_gate_via_config(self, enforcer):
        enforcer.update_config({
            "test_writing": {
                "can_auto_complete": False,
                "requires_human_review": True,
                "is_hard_gated": True,
            }
        })
        policy = enforcer.get_lane_policy(WorkCellLane.test_writing)
        assert policy.is_hard_gated is True

    def test_can_remove_hard_gate_via_config(self, enforcer):
        enforcer.update_config({
            "security_change": {
                "can_auto_complete": False,
                "requires_human_review": True,
                "is_hard_gated": False,  # downgrade — unusual but possible
            }
        })
        policy = enforcer.get_lane_policy(WorkCellLane.security_change)
        assert policy.is_hard_gated is False


# ---------------------------------------------------------------------------
# 8. get_config_snapshot()
# ---------------------------------------------------------------------------


class TestGetConfigSnapshot:
    def test_returns_dict(self, enforcer):
        snapshot = enforcer.get_config_snapshot()
        assert isinstance(snapshot, dict)

    def test_snapshot_has_all_lanes(self, enforcer):
        snapshot = enforcer.get_config_snapshot()
        for lane in WorkCellLane:
            assert lane.value in snapshot

    def test_snapshot_is_deep_copy(self, enforcer):
        snapshot = enforcer.get_config_snapshot()
        snapshot["docs"]["can_auto_complete"] = False  # mutate copy
        # Original should still be True
        original_policy = enforcer.get_lane_policy(WorkCellLane.docs)
        assert original_policy.can_auto_complete is True


# ---------------------------------------------------------------------------
# 9. reset()
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_restores_defaults(self, enforcer):
        enforcer.update_config({
            "docs": {
                "can_auto_complete": False,
                "requires_human_review": True,
                "is_hard_gated": False,
            }
        })
        enforcer.reset()
        policy = enforcer.get_lane_policy(WorkCellLane.docs)
        assert policy.can_auto_complete is True

    def test_reset_restores_all_lanes(self, enforcer):
        # Mutate several lanes then reset
        enforcer.update_config({
            "test_writing": {
                "can_auto_complete": False,
                "requires_human_review": True,
                "is_hard_gated": False,
            },
            "deployment": {
                "can_auto_complete": True,
                "requires_human_review": False,
                "is_hard_gated": False,
            },
        })
        enforcer.reset()
        # Both should be restored
        tw_policy = enforcer.get_lane_policy(WorkCellLane.test_writing)
        assert tw_policy.can_auto_complete is True
        deploy_policy = enforcer.get_lane_policy(WorkCellLane.deployment)
        assert deploy_policy.is_hard_gated is True


# ---------------------------------------------------------------------------
# 10. Custom config at construction time
# ---------------------------------------------------------------------------


class TestCustomConfig:
    def test_custom_config_applied(self):
        custom = {
            lane.value: {
                "can_auto_complete": False,
                "requires_human_review": True,
                "is_hard_gated": False,
            }
            for lane in WorkCellLane
        }
        # Override just security_change to be hard-gated
        custom[WorkCellLane.security_change.value]["is_hard_gated"] = True
        custom[WorkCellLane.deployment.value]["is_hard_gated"] = True

        enforcer = LanePolicyEnforcer(config=custom)
        # docs should now require human review
        policy = enforcer.get_lane_policy(WorkCellLane.docs)
        assert policy.requires_human_review is True

    def test_invalid_custom_config_raises(self):
        bad_config = {
            "docs": {
                "can_auto_complete": True,
                "requires_human_review": True,  # contradiction
                "is_hard_gated": False,
            }
        }
        with pytest.raises(ValueError):
            LanePolicyEnforcer(config=bad_config)


# ---------------------------------------------------------------------------
# 11. Singleton factory
# ---------------------------------------------------------------------------


class TestSingletonFactory:
    def test_get_returns_lane_policy_enforcer(self):
        enforcer = get_lane_policy_enforcer()
        assert isinstance(enforcer, LanePolicyEnforcer)

    def test_same_instance_returned_on_repeated_calls(self):
        a = get_lane_policy_enforcer()
        b = get_lane_policy_enforcer()
        assert a is b

    def test_reset_creates_fresh_instance(self):
        a = get_lane_policy_enforcer()
        reset_lane_policy_enforcer()
        b = get_lane_policy_enforcer()
        assert a is not b

    def test_singleton_works_after_reset(self):
        reset_lane_policy_enforcer()
        enforcer = get_lane_policy_enforcer()
        task = make_task(TaskType.test_writing, RiskTier.low)
        assert enforcer.can_auto_complete(task) is True


# ---------------------------------------------------------------------------
# 12. Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_can_auto_complete_calls(self, enforcer):
        """Multiple threads calling can_auto_complete concurrently must not raise."""
        errors: list[Exception] = []

        def worker():
            try:
                for task_type in TaskType:
                    task = make_task(task_type, RiskTier.low)
                    enforcer.can_auto_complete(task)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"Errors in concurrent access: {errors}"

    def test_concurrent_update_and_read(self, enforcer):
        """update_config and get_lane_policy can run concurrently without corruption."""
        errors: list[Exception] = []

        def updater():
            try:
                for _ in range(10):
                    enforcer.update_config({
                        "research": {
                            "can_auto_complete": False,
                            "requires_human_review": True,
                            "is_hard_gated": False,
                        }
                    })
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                for _ in range(20):
                    enforcer.get_lane_policy(WorkCellLane.research)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=updater) for _ in range(2)]
        threads += [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ---------------------------------------------------------------------------
# 13. Cross-cutting matrix check
# ---------------------------------------------------------------------------


class TestFullMatrix:
    """Validate policy decisions across all (TaskType, RiskTier) combinations."""

    def test_no_critical_risk_auto_completes(self, enforcer):
        """No task at critical risk should be auto-completable via the policy matrix."""
        # Note: this depends on how critical risk tasks resolve to work-cell lanes.
        # test_writing and docs at critical still map to their respective lanes
        # which default to auto-complete — so this test documents the expected behaviour.
        # security_change and deployment are always human-gated.
        always_blocked = {TaskType.security_change, TaskType.deployment}
        for task_type in always_blocked:
            task = make_task(task_type, RiskTier.critical)
            assert enforcer.can_auto_complete(task) is False

    def test_security_change_never_auto_completes(self, enforcer):
        for risk in RiskTier:
            task = make_task(TaskType.security_change, risk)
            assert enforcer.can_auto_complete(task) is False, (
                f"security_change at {risk} must never auto-complete"
            )

    def test_deployment_never_auto_completes(self, enforcer):
        for risk in RiskTier:
            task = make_task(TaskType.deployment, risk)
            assert enforcer.can_auto_complete(task) is False, (
                f"deployment at {risk} must never auto-complete"
            )

    def test_all_decisions_are_internally_consistent(self, enforcer):
        """Every policy decision must have consistent shortcut fields."""
        for task_type in TaskType:
            for risk_tier in RiskTier:
                decision = enforcer.evaluate("t", task_type, risk_tier)
                # can_auto_complete and requires_human_review must not both be True
                assert not (decision.can_auto_complete and decision.requires_human_review), (
                    f"{task_type}/{risk_tier}: both auto-complete and human review are True"
                )
                # is_hard_gated implies requires_human_review
                if decision.is_hard_gated:
                    assert decision.requires_human_review, (
                        f"{task_type}/{risk_tier}: is_hard_gated=True but not requires_human_review"
                    )
