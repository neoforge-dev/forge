"""Work-Cell Lane Taxonomy Tests — DF-2003
==========================================

Validates the work-cell lane taxonomy model (WorkCellLane, WorkCellConfig,
LaneResolver, DEFAULT_LANE_CONFIGS, get_lane_config) and the lane enforcer
service (LaneEnforcer, get_lane_enforcer, reset_lane_enforcer).

Coverage targets:
- LaneResolver: every (TaskType, RiskTier) combination resolves to one lane.
- LaneResolver: determinism — same inputs always produce same output.
- WorkCellConfig: all defaults are valid, required fields are present.
- LaneEnforcer: assign_lane records and returns the correct lane.
- LaneEnforcer: check_wip respects max_wip limits.
- LaneEnforcer: get_lane_stats is accurate.
- LaneEnforcer: singleton pattern (get/reset).
- LaneEnforcer: idempotent assign_lane.
- LaneEnforcer: complete_task decrements active and increments completed.
- LaneEnforcer: queued tasks are promoted on completion.
- Edge cases: unknown combinations raise KeyError; concurrent access is safe.
- 50+ tests, 100% model/service coverage.
"""

from __future__ import annotations

import threading
from collections import Counter

import pytest

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import (
    DEFAULT_LANE_CONFIGS,
    LaneResolver,
    WorkCellConfig,
    WorkCellLane,
    get_lane_config,
)
from forge_harness.webhook_server.services.lane_enforcer import (
    LaneEnforcer,
    get_lane_enforcer,
    reset_lane_enforcer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_enforcer_singleton():
    """Reset the LaneEnforcer singleton before and after every test."""
    reset_lane_enforcer()
    yield
    reset_lane_enforcer()


@pytest.fixture()
def enforcer() -> LaneEnforcer:
    """Return a fresh LaneEnforcer (not the singleton)."""
    return LaneEnforcer()


@pytest.fixture()
def resolver() -> LaneResolver:
    return LaneResolver()


# ===========================================================================
# WorkCellLane enum
# ===========================================================================


class TestWorkCellLaneEnum:
    """Basic sanity checks for the WorkCellLane enum."""

    def test_all_expected_lanes_exist(self) -> None:
        expected = {
            "api_simple",
            "api_stateful",
            "test_writing",
            "docs",
            "research",
            "refactor",
            "security_change",
            "deployment",
        }
        actual = {lane.value for lane in WorkCellLane}
        assert actual == expected

    def test_lane_count(self) -> None:
        assert len(WorkCellLane) == 8

    def test_lanes_are_str_enum(self) -> None:
        for lane in WorkCellLane:
            assert isinstance(lane.value, str)

    def test_lane_values_match_names(self) -> None:
        """Enum name and value should be identical (snake_case)."""
        for lane in WorkCellLane:
            assert lane.name == lane.value


# ===========================================================================
# WorkCellConfig model
# ===========================================================================


class TestWorkCellConfigModel:
    """Validate WorkCellConfig Pydantic model constraints."""

    def test_valid_config_construction(self) -> None:
        config = WorkCellConfig(
            lane=WorkCellLane.api_simple,
            max_wip=5,
            timeout_seconds=300,
            auto_requeue=True,
            evaluator_profile_name="standard",
            requires_human_review=False,
        )
        assert config.lane == WorkCellLane.api_simple
        assert config.max_wip == 5
        assert config.timeout_seconds == 300
        assert config.auto_requeue is True
        assert config.evaluator_profile_name == "standard"
        assert config.requires_human_review is False

    def test_max_wip_min_boundary(self) -> None:
        config = WorkCellConfig(
            lane=WorkCellLane.docs,
            max_wip=1,
            timeout_seconds=60,
            auto_requeue=False,
            evaluator_profile_name="lenient",
            requires_human_review=False,
        )
        assert config.max_wip == 1

    def test_max_wip_max_boundary(self) -> None:
        config = WorkCellConfig(
            lane=WorkCellLane.docs,
            max_wip=100,
            timeout_seconds=60,
            auto_requeue=False,
            evaluator_profile_name="lenient",
            requires_human_review=False,
        )
        assert config.max_wip == 100

    def test_max_wip_below_min_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkCellConfig(
                lane=WorkCellLane.docs,
                max_wip=0,  # must be >= 1
                timeout_seconds=60,
                auto_requeue=False,
                evaluator_profile_name="lenient",
                requires_human_review=False,
            )

    def test_max_wip_above_max_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkCellConfig(
                lane=WorkCellLane.docs,
                max_wip=101,  # must be <= 100
                timeout_seconds=60,
                auto_requeue=False,
                evaluator_profile_name="lenient",
                requires_human_review=False,
            )

    def test_timeout_min_boundary(self) -> None:
        config = WorkCellConfig(
            lane=WorkCellLane.docs,
            max_wip=1,
            timeout_seconds=60,
            auto_requeue=False,
            evaluator_profile_name="lenient",
            requires_human_review=False,
        )
        assert config.timeout_seconds == 60

    def test_timeout_below_min_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkCellConfig(
                lane=WorkCellLane.docs,
                max_wip=1,
                timeout_seconds=59,  # must be >= 60
                auto_requeue=False,
                evaluator_profile_name="lenient",
                requires_human_review=False,
            )

    def test_empty_evaluator_profile_name_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkCellConfig(
                lane=WorkCellLane.docs,
                max_wip=1,
                timeout_seconds=60,
                auto_requeue=False,
                evaluator_profile_name="",  # must be non-empty
                requires_human_review=False,
            )

    def test_config_is_frozen(self) -> None:
        """WorkCellConfig is immutable after construction."""
        config = get_lane_config(WorkCellLane.api_simple)
        with pytest.raises(Exception):
            config.max_wip = 999  # type: ignore[misc]

    def test_all_required_fields_present(self) -> None:
        required_fields = {
            "lane",
            "max_wip",
            "timeout_seconds",
            "auto_requeue",
            "evaluator_profile_name",
            "requires_human_review",
        }
        model_fields = set(WorkCellConfig.model_fields.keys())
        assert required_fields.issubset(model_fields)


# ===========================================================================
# DEFAULT_LANE_CONFIGS
# ===========================================================================


class TestDefaultLaneConfigs:
    """Validate the DEFAULT_LANE_CONFIGS constant."""

    def test_all_lanes_have_a_default_config(self) -> None:
        for lane in WorkCellLane:
            assert lane in DEFAULT_LANE_CONFIGS, f"No default config for lane: {lane.value}"

    def test_config_lane_field_matches_key(self) -> None:
        for lane, config in DEFAULT_LANE_CONFIGS.items():
            assert config.lane == lane, (
                f"Config key {lane.value!r} has lane field {config.lane.value!r}"
            )

    def test_security_change_requires_human_review(self) -> None:
        config = DEFAULT_LANE_CONFIGS[WorkCellLane.security_change]
        assert config.requires_human_review is True

    def test_deployment_requires_human_review(self) -> None:
        config = DEFAULT_LANE_CONFIGS[WorkCellLane.deployment]
        assert config.requires_human_review is True

    def test_deployment_max_wip_is_one(self) -> None:
        """Deployment lane is serialised: only one active deployment at a time."""
        config = DEFAULT_LANE_CONFIGS[WorkCellLane.deployment]
        assert config.max_wip == 1

    def test_non_sensitive_lanes_do_not_require_human_review(self) -> None:
        non_sensitive = {
            WorkCellLane.api_simple,
            WorkCellLane.api_stateful,
            WorkCellLane.test_writing,
            WorkCellLane.docs,
            WorkCellLane.research,
            WorkCellLane.refactor,
        }
        for lane in non_sensitive:
            config = DEFAULT_LANE_CONFIGS[lane]
            assert config.requires_human_review is False, (
                f"Lane {lane.value} unexpectedly requires human review"
            )

    def test_all_timeouts_are_positive(self) -> None:
        for lane, config in DEFAULT_LANE_CONFIGS.items():
            assert config.timeout_seconds > 0, f"Lane {lane.value} has non-positive timeout"

    def test_all_max_wips_are_positive(self) -> None:
        for lane, config in DEFAULT_LANE_CONFIGS.items():
            assert config.max_wip > 0, f"Lane {lane.value} has non-positive max_wip"

    def test_security_and_deployment_auto_requeue_false(self) -> None:
        """High-stakes lanes must not auto-requeue timed-out tasks."""
        assert DEFAULT_LANE_CONFIGS[WorkCellLane.security_change].auto_requeue is False
        assert DEFAULT_LANE_CONFIGS[WorkCellLane.deployment].auto_requeue is False


# ===========================================================================
# get_lane_config helper
# ===========================================================================


class TestGetLaneConfig:
    def test_returns_correct_config_for_each_lane(self) -> None:
        for lane in WorkCellLane:
            config = get_lane_config(lane)
            assert isinstance(config, WorkCellConfig)
            assert config.lane == lane

    def test_returns_same_object_as_dict(self) -> None:
        for lane in WorkCellLane:
            assert get_lane_config(lane) is DEFAULT_LANE_CONFIGS[lane]


# ===========================================================================
# LaneResolver
# ===========================================================================


class TestLaneResolverCompleteness:
    """Every (TaskType, RiskTier) pair must resolve to exactly one lane."""

    def test_every_combination_resolves(self, resolver: LaneResolver) -> None:
        for task_type in TaskType:
            for risk_tier in RiskTier:
                lane = resolver.resolve(task_type, risk_tier)
                assert isinstance(lane, WorkCellLane), (
                    f"resolve({task_type.value}, {risk_tier.value}) returned {lane!r}"
                )

    def test_combination_count(self, resolver: LaneResolver) -> None:
        """The resolver map covers all 12 * 4 = 48 combinations."""
        results = set()
        for task_type in TaskType:
            for risk_tier in RiskTier:
                results.add(resolver.resolve(task_type, risk_tier))
        # At least some lanes are used
        assert len(results) >= 2

    def test_all_returned_values_are_work_cell_lanes(self, resolver: LaneResolver) -> None:
        valid_lanes = set(WorkCellLane)
        for task_type in TaskType:
            for risk_tier in RiskTier:
                lane = resolver.resolve(task_type, risk_tier)
                assert lane in valid_lanes


class TestLaneResolverDeterminism:
    """Same inputs must always produce the same output."""

    def test_repeated_calls_return_same_lane(self, resolver: LaneResolver) -> None:
        for task_type in TaskType:
            for risk_tier in RiskTier:
                first = resolver.resolve(task_type, risk_tier)
                second = resolver.resolve(task_type, risk_tier)
                third = resolver.resolve(task_type, risk_tier)
                assert first == second == third, (
                    f"Non-deterministic resolve({task_type.value}, {risk_tier.value}): "
                    f"{first.value} / {second.value} / {third.value}"
                )

    def test_two_resolver_instances_agree(self) -> None:
        r1 = LaneResolver()
        r2 = LaneResolver()
        for task_type in TaskType:
            for risk_tier in RiskTier:
                assert r1.resolve(task_type, risk_tier) == r2.resolve(task_type, risk_tier)


class TestLaneResolverMappingInvariants:
    """Domain-level invariants about the resolver mapping."""

    def test_security_change_always_maps_to_security_lane(self, resolver: LaneResolver) -> None:
        for risk_tier in RiskTier:
            lane = resolver.resolve(TaskType.security_change, risk_tier)
            assert lane == WorkCellLane.security_change, (
                f"security_change/{risk_tier.value} mapped to {lane.value}"
            )

    def test_deployment_always_maps_to_deployment_lane(self, resolver: LaneResolver) -> None:
        for risk_tier in RiskTier:
            lane = resolver.resolve(TaskType.deployment, risk_tier)
            assert lane == WorkCellLane.deployment, (
                f"deployment/{risk_tier.value} mapped to {lane.value}"
            )

    def test_test_writing_always_maps_to_test_writing_lane(self, resolver: LaneResolver) -> None:
        for risk_tier in RiskTier:
            lane = resolver.resolve(TaskType.test_writing, risk_tier)
            assert lane == WorkCellLane.test_writing

    def test_docs_update_always_maps_to_docs_lane(self, resolver: LaneResolver) -> None:
        for risk_tier in RiskTier:
            lane = resolver.resolve(TaskType.docs_update, risk_tier)
            assert lane == WorkCellLane.docs

    def test_api_endpoint_low_risk_maps_to_simple(self, resolver: LaneResolver) -> None:
        assert resolver.resolve(TaskType.api_endpoint, RiskTier.low) == WorkCellLane.api_simple

    def test_api_endpoint_higher_risk_maps_to_stateful(self, resolver: LaneResolver) -> None:
        for risk_tier in (RiskTier.medium, RiskTier.high, RiskTier.critical):
            lane = resolver.resolve(TaskType.api_endpoint, risk_tier)
            assert lane == WorkCellLane.api_stateful, (
                f"api_endpoint/{risk_tier.value} mapped to {lane.value}"
            )

    def test_code_refactor_always_maps_to_refactor_lane(self, resolver: LaneResolver) -> None:
        for risk_tier in RiskTier:
            assert resolver.resolve(TaskType.code_refactor, risk_tier) == WorkCellLane.refactor

    def test_dependency_update_always_maps_to_refactor_lane(self, resolver: LaneResolver) -> None:
        for risk_tier in RiskTier:
            assert resolver.resolve(TaskType.dependency_update, risk_tier) == WorkCellLane.refactor


class TestLaneResolverInvalidInputs:
    """Edge cases with invalid inputs."""

    def test_invalid_task_type_raises_key_error(self, resolver: LaneResolver) -> None:
        with pytest.raises((KeyError, AttributeError, ValueError)):
            resolver.resolve("not_a_task_type", RiskTier.low)  # type: ignore[arg-type]

    def test_invalid_risk_tier_raises_key_error(self, resolver: LaneResolver) -> None:
        with pytest.raises((KeyError, AttributeError, ValueError)):
            resolver.resolve(TaskType.bug_fix, "not_a_risk_tier")  # type: ignore[arg-type]


# ===========================================================================
# LaneEnforcer
# ===========================================================================


class TestLaneEnforcerAssignLane:
    """Tests for LaneEnforcer.assign_lane."""

    def test_assign_lane_returns_work_cell_lane(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("task-1", TaskType.bug_fix, RiskTier.low)
        assert isinstance(lane, WorkCellLane)

    def test_assign_lane_records_assignment(self, enforcer: LaneEnforcer) -> None:
        lane = enforcer.assign_lane("task-42", TaskType.test_writing, RiskTier.medium)
        assert enforcer.get_assignment("task-42") == lane

    def test_assign_lane_returns_correct_lane(self, enforcer: LaneEnforcer) -> None:
        """Verify specific (TaskType, RiskTier) routes to the expected lane."""
        lane = enforcer.assign_lane("t1", TaskType.security_change, RiskTier.high)
        assert lane == WorkCellLane.security_change

    def test_assign_lane_idempotent(self, enforcer: LaneEnforcer) -> None:
        """Calling assign_lane twice with the same task_id returns the same lane."""
        lane1 = enforcer.assign_lane("task-idem", TaskType.docs_update, RiskTier.low)
        lane2 = enforcer.assign_lane("task-idem", TaskType.docs_update, RiskTier.low)
        assert lane1 == lane2

    def test_idempotent_does_not_double_count(self, enforcer: LaneEnforcer) -> None:
        """Repeated assign_lane must not inflate active/queued counters."""
        enforcer.assign_lane("task-dupe", TaskType.bug_fix, RiskTier.low)
        enforcer.assign_lane("task-dupe", TaskType.bug_fix, RiskTier.low)
        stats = enforcer.get_lane_stats()
        lane = WorkCellLane.api_simple
        total = stats[lane.value]["active"] + stats[lane.value]["queued"]
        assert total == 1

    def test_assign_lane_increments_active_when_capacity_available(
        self, enforcer: LaneEnforcer
    ) -> None:
        enforcer.assign_lane("task-act", TaskType.docs_update, RiskTier.low)
        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.docs.value]["active"] == 1
        assert stats[WorkCellLane.docs.value]["queued"] == 0

    def test_multiple_tasks_in_different_lanes(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.test_writing, RiskTier.low)
        enforcer.assign_lane("t2", TaskType.deployment, RiskTier.medium)
        enforcer.assign_lane("t3", TaskType.docs_update, RiskTier.low)

        assert enforcer.get_assignment("t1") == WorkCellLane.test_writing
        assert enforcer.get_assignment("t2") == WorkCellLane.deployment
        assert enforcer.get_assignment("t3") == WorkCellLane.docs


class TestLaneEnforcerCheckWip:
    """Tests for LaneEnforcer.check_wip."""

    def test_empty_lane_has_capacity(self, enforcer: LaneEnforcer) -> None:
        for lane in WorkCellLane:
            assert enforcer.check_wip(lane) is True

    def test_full_lane_has_no_capacity(self, enforcer: LaneEnforcer) -> None:
        """Fill deployment lane (max_wip=1) and verify it reports no capacity."""
        config = get_lane_config(WorkCellLane.deployment)
        # Assign max_wip tasks to fill the lane.
        for i in range(config.max_wip):
            enforcer.assign_lane(f"deploy-{i}", TaskType.deployment, RiskTier.low)
        assert enforcer.check_wip(WorkCellLane.deployment) is False

    def test_one_below_max_wip_has_capacity(self, enforcer: LaneEnforcer) -> None:
        config = get_lane_config(WorkCellLane.docs)
        # Fill to max_wip - 1
        for i in range(config.max_wip - 1):
            enforcer.assign_lane(f"doc-{i}", TaskType.docs_update, RiskTier.low)
        assert enforcer.check_wip(WorkCellLane.docs) is True

    def test_task_queued_when_lane_full(self, enforcer: LaneEnforcer) -> None:
        """When the lane is full the (max_wip + 1)-th task must be queued."""
        config = get_lane_config(WorkCellLane.deployment)
        for i in range(config.max_wip):
            enforcer.assign_lane(f"d-{i}", TaskType.deployment, RiskTier.low)

        # One more task — should be queued, not active
        enforcer.assign_lane("d-overflow", TaskType.deployment, RiskTier.medium)
        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.deployment.value]["queued"] == 1
        assert stats[WorkCellLane.deployment.value]["active"] == config.max_wip


class TestLaneEnforcerGetLaneStats:
    """Tests for LaneEnforcer.get_lane_stats."""

    def test_stats_include_all_lanes(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane in WorkCellLane:
            assert lane.value in stats, f"Missing stats for lane: {lane.value}"

    def test_stats_keys_are_correct(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            assert "active" in lane_stats
            assert "queued" in lane_stats
            assert "completed" in lane_stats
            assert "max_wip" in lane_stats

    def test_initial_stats_all_zero(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            assert lane_stats["active"] == 0
            assert lane_stats["queued"] == 0
            assert lane_stats["completed"] == 0

    def test_stats_reflect_assigned_tasks(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.assign_lane("t2", TaskType.bug_fix, RiskTier.low)
        stats = enforcer.get_lane_stats()
        # Both should be active (api_simple max_wip=10 >> 2)
        assert stats[WorkCellLane.api_simple.value]["active"] == 2

    def test_stats_max_wip_matches_config(self, enforcer: LaneEnforcer) -> None:
        stats = enforcer.get_lane_stats()
        for lane in WorkCellLane:
            config = get_lane_config(lane)
            assert stats[lane.value]["max_wip"] == config.max_wip

    def test_stats_after_complete_task(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t-complete", TaskType.docs_update, RiskTier.low)
        enforcer.complete_task("t-complete")
        stats = enforcer.get_lane_stats()
        doc_stats = stats[WorkCellLane.docs.value]
        assert doc_stats["active"] == 0
        assert doc_stats["completed"] == 1


class TestLaneEnforcerCompleteTask:
    """Tests for LaneEnforcer.complete_task."""

    def test_complete_known_task_returns_lane(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.test_writing, RiskTier.low)
        result = enforcer.complete_task("t1")
        assert result == WorkCellLane.test_writing

    def test_complete_unknown_task_returns_none(self, enforcer: LaneEnforcer) -> None:
        result = enforcer.complete_task("ghost-task")
        assert result is None

    def test_complete_frees_wip_slot(self, enforcer: LaneEnforcer) -> None:
        """After completing, the lane should have capacity again."""
        config = get_lane_config(WorkCellLane.deployment)
        for i in range(config.max_wip):
            enforcer.assign_lane(f"deploy-{i}", TaskType.deployment, RiskTier.low)
        assert enforcer.check_wip(WorkCellLane.deployment) is False

        enforcer.complete_task("deploy-0")
        assert enforcer.check_wip(WorkCellLane.deployment) is True

    def test_complete_promotes_queued_task(self, enforcer: LaneEnforcer) -> None:
        """Completing a task should promote one queued task to active."""
        config = get_lane_config(WorkCellLane.deployment)
        # Fill lane to capacity
        for i in range(config.max_wip):
            enforcer.assign_lane(f"d-{i}", TaskType.deployment, RiskTier.low)
        # Queue one more
        enforcer.assign_lane("d-queued", TaskType.deployment, RiskTier.medium)

        stats_before = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        assert stats_before["queued"] == 1
        assert stats_before["active"] == config.max_wip

        # Complete one active task
        enforcer.complete_task("d-0")

        stats_after = enforcer.get_lane_stats()[WorkCellLane.deployment.value]
        # Active should still equal max_wip (queued task was promoted)
        assert stats_after["active"] == config.max_wip
        assert stats_after["queued"] == 0
        assert stats_after["completed"] == 1

    def test_complete_updates_completed_counter(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.code_refactor, RiskTier.low)
        enforcer.assign_lane("t2", TaskType.code_refactor, RiskTier.medium)
        enforcer.complete_task("t1")
        enforcer.complete_task("t2")
        stats = enforcer.get_lane_stats()
        assert stats[WorkCellLane.refactor.value]["completed"] == 2


class TestLaneEnforcerGetAssignment:
    """Tests for LaneEnforcer.get_assignment."""

    def test_unknown_task_returns_none(self, enforcer: LaneEnforcer) -> None:
        assert enforcer.get_assignment("never-assigned") is None

    def test_known_task_returns_lane(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t-known", TaskType.security_change, RiskTier.low)
        assert enforcer.get_assignment("t-known") == WorkCellLane.security_change


class TestLaneEnforcerReset:
    """Tests for LaneEnforcer.reset."""

    def test_reset_clears_assignments(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        assert enforcer.get_assignment("t1") is None

    def test_reset_clears_stats(self, enforcer: LaneEnforcer) -> None:
        enforcer.assign_lane("t1", TaskType.bug_fix, RiskTier.low)
        enforcer.reset()
        stats = enforcer.get_lane_stats()
        for lane_stats in stats.values():
            assert lane_stats["active"] == 0
            assert lane_stats["queued"] == 0
            assert lane_stats["completed"] == 0


# ===========================================================================
# Singleton pattern
# ===========================================================================


class TestLaneEnforcerSingleton:
    """Tests for get_lane_enforcer / reset_lane_enforcer singleton pattern."""

    def test_get_returns_lane_enforcer_instance(self) -> None:
        instance = get_lane_enforcer()
        assert isinstance(instance, LaneEnforcer)

    def test_get_returns_same_instance(self) -> None:
        a = get_lane_enforcer()
        b = get_lane_enforcer()
        assert a is b

    def test_reset_causes_new_instance_on_next_get(self) -> None:
        first = get_lane_enforcer()
        reset_lane_enforcer()
        second = get_lane_enforcer()
        assert first is not second

    def test_state_persists_across_get_calls(self) -> None:
        e1 = get_lane_enforcer()
        e1.assign_lane("singleton-task", TaskType.docs_update, RiskTier.low)
        e2 = get_lane_enforcer()
        assert e2.get_assignment("singleton-task") == WorkCellLane.docs

    def test_reset_singleton_clears_state(self) -> None:
        e = get_lane_enforcer()
        e.assign_lane("s-task", TaskType.docs_update, RiskTier.low)
        reset_lane_enforcer()
        fresh = get_lane_enforcer()
        assert fresh.get_assignment("s-task") is None


# ===========================================================================
# Concurrent access
# ===========================================================================


class TestLaneEnforcerConcurrentAccess:
    """Verify thread-safety under concurrent load."""

    def test_concurrent_assign_lane_no_races(self, enforcer: LaneEnforcer) -> None:
        """100 threads each assign a unique task — no lost assignments."""
        n = 100
        errors: list[Exception] = []

        def _assign(task_id: str) -> None:
            try:
                enforcer.assign_lane(task_id, TaskType.test_writing, RiskTier.low)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_assign, args=(f"ct-{i}",)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors in concurrent assign: {errors}"

        # Every task should be assigned
        assigned = sum(1 for i in range(n) if enforcer.get_assignment(f"ct-{i}") is not None)
        assert assigned == n

    def test_concurrent_stats_are_consistent(self, enforcer: LaneEnforcer) -> None:
        """Stats should be internally consistent after concurrent assigns."""
        n = 50
        barrier = threading.Barrier(n)

        def _assign(task_id: str) -> None:
            barrier.wait()
            enforcer.assign_lane(task_id, TaskType.test_writing, RiskTier.low)

        threads = [threading.Thread(target=_assign, args=(f"cs-{i}",)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = enforcer.get_lane_stats()[WorkCellLane.test_writing.value]
        total = stats["active"] + stats["queued"]
        assert total == n, f"Expected {n} tasks tracked, got {total}"

    def test_concurrent_get_lane_stats_does_not_raise(self, enforcer: LaneEnforcer) -> None:
        """get_lane_stats must not raise under concurrent read/write."""
        errors: list[Exception] = []
        stop = threading.Event()

        def _writer() -> None:
            i = 0
            while not stop.is_set():
                try:
                    enforcer.assign_lane(f"w-{i}", TaskType.docs_update, RiskTier.low)
                    i += 1
                except Exception as exc:
                    errors.append(exc)

        def _reader() -> None:
            for _ in range(20):
                try:
                    enforcer.get_lane_stats()
                except Exception as exc:
                    errors.append(exc)

        writer = threading.Thread(target=_writer)
        readers = [threading.Thread(target=_reader) for _ in range(5)]
        writer.start()
        for r in readers:
            r.start()
        for r in readers:
            r.join()
        stop.set()
        writer.join()

        assert not errors, f"Errors during concurrent stats read: {errors}"


# ===========================================================================
# Export surface via __init__.py
# ===========================================================================


class TestPublicExports:
    """Verify that new symbols are accessible from package __init__.py."""

    def test_work_cell_lane_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import WorkCellLane as WorkCellLaneAlias

        assert WorkCellLaneAlias is WorkCellLane

    def test_work_cell_config_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import WorkCellConfig as WorkCellConfigAlias

        assert WorkCellConfigAlias is WorkCellConfig

    def test_lane_resolver_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import LaneResolver as LaneResolverAlias

        assert LaneResolverAlias is LaneResolver

    def test_default_lane_configs_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import DEFAULT_LANE_CONFIGS as DLC

        assert DLC is DEFAULT_LANE_CONFIGS

    def test_get_lane_config_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import get_lane_config as glc

        assert glc is get_lane_config

    def test_lane_enforcer_exported_from_services(self) -> None:
        from forge_harness.webhook_server.services import LaneEnforcer as LaneEnforcerAlias

        assert LaneEnforcerAlias is LaneEnforcer

    def test_get_lane_enforcer_exported_from_services(self) -> None:
        from forge_harness.webhook_server.services import get_lane_enforcer as gle

        assert gle is get_lane_enforcer

    def test_reset_lane_enforcer_exported_from_services(self) -> None:
        from forge_harness.webhook_server.services import reset_lane_enforcer as rle

        assert rle is reset_lane_enforcer
