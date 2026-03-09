"""Comprehensive unit tests for forge_harness.webhook_server.services.scheduler_policy.

Target module: forge_harness/webhook_server/services/scheduler_policy.py
Coverage goal: All public functions and classes exhaustively tested.

Test groups
-----------
1.  SchedulerRecommendation – Pydantic model validation
2.  SchedulerPolicy._capacity_score – static method edge cases
3.  SchedulerPolicy._affinity_score – static method, all capability-map keys
4.  SchedulerPolicy._path_safety_score – conflict matrix
5.  SchedulerPolicy._collect_active_leases – various lease-store shapes
6.  SchedulerPolicy._score_node – composite arithmetic
7.  SchedulerPolicy.recommend – public API, sorting, filtering, logging
8.  SchedulerPolicy.get_stats / _record_stats – counters, latency averaging
9.  get_scheduler_policy / reset_scheduler_policy – singleton lifecycle
10. Module-level constants – sanity checks
11. Thread safety – concurrent recommend() calls
12. Error resilience – broken lease stores, malformed node dicts
"""

from __future__ import annotations

import time
from threading import Thread
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from forge_harness.models.lease import LeaseState, TaskLease
from forge_harness.webhook_server.services.scheduler_policy import (
    _MAX_AGENTS_PER_NODE,
    _TASK_TYPE_CAPABILITY_MAP,
    _W_AFFINITY,
    _W_AVAILABILITY,
    _W_CAPACITY,
    _W_PATH_SAFETY,
    SchedulerPolicy,
    SchedulerRecommendation,
    get_scheduler_policy,
    reset_scheduler_policy,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mock_store(ranked_nodes: list[dict] | None = None) -> MagicMock:
    """Return a MagicMock HeartbeatStore whose scheduler view holds *ranked_nodes*."""
    store = MagicMock()
    store.get_scheduler_view.return_value = {"ranked_nodes": ranked_nodes or []}
    return store


def _node(
    node_id: str = "node-1",
    availability_score: float = 0.80,
    active_agents: int = 2,
    capabilities: dict | None = None,
) -> dict:
    """Build a ranked-node dict as emitted by HeartbeatStore.get_scheduler_view."""
    entry: dict = {
        "node_id": node_id,
        "availability_score": availability_score,
        "active_agents": active_agents,
    }
    if capabilities is not None:
        entry["capabilities"] = capabilities
    return entry


def _lease(
    task_id: str = "task-001",
    owner_node: str = "node-1",
    path_lock: str | None = "src/main.py",
    state: LeaseState = LeaseState.ACTIVE,
) -> TaskLease:
    """Build a TaskLease with the given parameters."""
    return TaskLease(
        task_id=task_id,
        owner_node=owner_node,
        path_lock=path_lock,
        state=state,
    )


def _policy(
    ranked_nodes: list[dict] | None = None,
    lease_store=None,
) -> SchedulerPolicy:
    """Convenience factory for SchedulerPolicy backed by a mock HeartbeatStore."""
    return SchedulerPolicy(
        heartbeat_store=_mock_store(ranked_nodes),
        lease_store=lease_store,
    )


# ---------------------------------------------------------------------------
# Autouse fixture: reset singleton before and after every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_scheduler_policy()
    yield
    reset_scheduler_policy()


# ===========================================================================
# 1. SchedulerRecommendation – Pydantic model
# ===========================================================================


class TestSchedulerRecommendationModel:
    """Validate the SchedulerRecommendation Pydantic model constraints."""

    def test_defaults_set_correctly(self):
        rec = SchedulerRecommendation(node_id="n1", score=0.5)
        assert rec.node_id == "n1"
        assert rec.score == 0.5
        assert rec.reason == ""
        assert rec.available_agents == 0
        assert rec.path_conflicts == []

    def test_all_fields_accepted(self):
        rec = SchedulerRecommendation(
            node_id="n2",
            score=0.99,
            reason="test reason",
            available_agents=5,
            path_conflicts=["t1", "t2"],
        )
        assert rec.reason == "test reason"
        assert rec.available_agents == 5
        assert rec.path_conflicts == ["t1", "t2"]

    def test_score_zero_boundary_valid(self):
        rec = SchedulerRecommendation(node_id="x", score=0.0)
        assert rec.score == 0.0

    def test_score_one_boundary_valid(self):
        rec = SchedulerRecommendation(node_id="x", score=1.0)
        assert rec.score == 1.0

    def test_score_negative_rejected(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x", score=-0.001)

    def test_score_above_one_rejected(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x", score=1.001)

    def test_available_agents_negative_rejected(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x", score=0.5, available_agents=-1)

    def test_node_id_required(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(score=0.5)  # type: ignore[call-arg]

    def test_score_required(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x")  # type: ignore[call-arg]

    def test_path_conflicts_empty_list_default(self):
        rec = SchedulerRecommendation(node_id="x", score=0.5)
        # Each instance gets its own list (not a shared default)
        rec2 = SchedulerRecommendation(node_id="y", score=0.3)
        rec.path_conflicts.append("foo")
        assert rec2.path_conflicts == []

    def test_available_agents_zero_valid(self):
        rec = SchedulerRecommendation(node_id="x", score=0.5, available_agents=0)
        assert rec.available_agents == 0


# ===========================================================================
# 2. SchedulerPolicy._capacity_score
# ===========================================================================


class TestCapacityScore:
    """Static method _capacity_score — boundaries and proportional values."""

    def test_zero_agents_available_gives_zero_score(self):
        assert SchedulerPolicy._capacity_score(0) == 0.0

    def test_max_agents_available_gives_one(self):
        assert SchedulerPolicy._capacity_score(_MAX_AGENTS_PER_NODE) == 1.0

    def test_half_agents_available_gives_half(self):
        half = _MAX_AGENTS_PER_NODE // 2
        assert SchedulerPolicy._capacity_score(half) == pytest.approx(0.5, abs=1e-9)

    def test_over_capacity_capped_at_one(self):
        assert SchedulerPolicy._capacity_score(_MAX_AGENTS_PER_NODE + 100) == 1.0

    def test_one_agent_available_proportional(self):
        expected = 1.0 / _MAX_AGENTS_PER_NODE
        assert SchedulerPolicy._capacity_score(1) == pytest.approx(expected, abs=1e-9)

    def test_seven_agents_available(self):
        expected = 7.0 / _MAX_AGENTS_PER_NODE
        assert SchedulerPolicy._capacity_score(7) == pytest.approx(expected, abs=1e-9)

    def test_return_value_is_float(self):
        result = SchedulerPolicy._capacity_score(4)
        assert isinstance(result, float)

    def test_result_always_between_zero_and_one(self):
        for n in range(_MAX_AGENTS_PER_NODE + 5):
            score = SchedulerPolicy._capacity_score(n)
            assert 0.0 <= score <= 1.0, f"Out of range for n={n}: {score}"


# ===========================================================================
# 3. SchedulerPolicy._affinity_score
# ===========================================================================


class TestAffinityScore:
    """Static method _affinity_score — task type mapping and capability lookup."""

    def test_general_task_type_returns_one(self):
        assert SchedulerPolicy._affinity_score("general", {}) == 1.0

    def test_empty_string_task_type_returns_one(self):
        assert SchedulerPolicy._affinity_score("", {}) == 1.0

    def test_no_capabilities_key_returns_neutral(self):
        # Node entry has no 'capabilities' key at all
        assert SchedulerPolicy._affinity_score("python", {}) == 0.5

    def test_empty_capabilities_dict_returns_neutral(self):
        assert SchedulerPolicy._affinity_score("python", {"capabilities": {}}) == 0.5

    def test_capability_present_true_returns_one(self):
        node = {"capabilities": {"python": True}}
        assert SchedulerPolicy._affinity_score("python", node) == 1.0

    def test_capability_present_false_returns_zero(self):
        node = {"capabilities": {"python": False}}
        assert SchedulerPolicy._affinity_score("python", node) == 0.0

    def test_ios_maps_to_ios_simulator_key(self):
        node = {"capabilities": {"ios_simulator": True}}
        assert SchedulerPolicy._affinity_score("ios", node) == 1.0

    def test_ios_missing_ios_simulator_key_returns_zero(self):
        node = {"capabilities": {"ios": True}}  # wrong key
        assert SchedulerPolicy._affinity_score("ios", node) == 0.0

    def test_all_known_task_types_map_correctly(self):
        """Every entry in _TASK_TYPE_CAPABILITY_MAP resolves to the correct cap key."""
        for task_type, cap_key in _TASK_TYPE_CAPABILITY_MAP.items():
            node = {"capabilities": {cap_key: True}}
            score = SchedulerPolicy._affinity_score(task_type, node)
            assert score == 1.0, (
                f"Expected 1.0 for task_type={task_type!r} / cap_key={cap_key!r}, got {score}"
            )

    def test_unknown_task_type_falls_back_to_itself_as_key(self):
        node = {"capabilities": {"exotic_ai": True}}
        assert SchedulerPolicy._affinity_score("exotic_ai", node) == 1.0

    def test_unknown_task_type_absent_capability_returns_zero(self):
        node = {"capabilities": {"other_key": True}}
        assert SchedulerPolicy._affinity_score("exotic_ai", node) == 0.0

    def test_general_with_capabilities_still_returns_one(self):
        node = {"capabilities": {"python": True}}
        assert SchedulerPolicy._affinity_score("general", node) == 1.0

    def test_capability_value_none_treated_as_false(self):
        node = {"capabilities": {"docker": None}}
        # None is falsy — capability should be considered absent
        assert SchedulerPolicy._affinity_score("docker", node) == 0.0


# ===========================================================================
# 4. SchedulerPolicy._path_safety_score
# ===========================================================================


class TestPathSafetyScore:
    """Instance method _path_safety_score — conflict detection logic."""

    def setup_method(self):
        self.policy = _policy()

    def test_no_path_lock_returns_one_zero_conflicts(self):
        score, conflicts = self.policy._path_safety_score("node-1", None, [])
        assert score == 1.0
        assert conflicts == []

    def test_no_path_lock_ignores_existing_leases(self):
        existing = _lease(owner_node="node-1", path_lock="src/main.py")
        score, conflicts = self.policy._path_safety_score("node-1", None, [existing])
        assert score == 1.0
        assert conflicts == []

    def test_path_lock_no_leases_returns_one(self):
        score, conflicts = self.policy._path_safety_score("node-1", "src/main.py", [])
        assert score == 1.0
        assert conflicts == []

    def test_path_lock_conflict_on_same_node_returns_zero(self):
        conflict_lease = _lease(task_id="conflict-task", owner_node="node-1", path_lock="src/main.py")
        score, conflicts = self.policy._path_safety_score("node-1", "src/main.py", [conflict_lease])
        assert score == 0.0
        assert "conflict-task" in conflicts

    def test_path_lock_conflict_on_different_node_does_not_penalise(self):
        lease_on_other = _lease(task_id="t-other", owner_node="node-2", path_lock="src/main.py")
        score, conflicts = self.policy._path_safety_score("node-1", "src/main.py", [lease_on_other])
        assert score == 1.0
        assert conflicts == []

    def test_multiple_conflicts_all_returned(self):
        leases = [
            _lease(task_id=f"t-{i}", owner_node="node-1", path_lock="src/file.py")
            for i in range(4)
        ]
        score, conflicts = self.policy._path_safety_score("node-1", "src/file.py", leases)
        assert score == 0.0
        assert len(conflicts) == 4

    def test_different_path_no_conflict(self):
        lease_different = _lease(owner_node="node-1", path_lock="src/other.py")
        score, conflicts = self.policy._path_safety_score("node-1", "src/main.py", [lease_different])
        assert score == 1.0
        assert conflicts == []

    def test_empty_path_lock_string_treated_as_no_lock(self):
        # An empty string path_lock is falsy → treated the same as None
        lease = _lease(owner_node="node-1", path_lock="src/main.py")
        score, conflicts = self.policy._path_safety_score("node-1", "", [lease])
        assert score == 1.0
        assert conflicts == []

    def test_mixed_node_leases_only_matching_node_counted(self):
        lease_node1 = _lease(task_id="t-n1", owner_node="node-1", path_lock="src/shared.py")
        lease_node2 = _lease(task_id="t-n2", owner_node="node-2", path_lock="src/shared.py")
        score, conflicts = self.policy._path_safety_score(
            "node-1", "src/shared.py", [lease_node1, lease_node2]
        )
        assert score == 0.0
        assert conflicts == ["t-n1"]


# ===========================================================================
# 5. SchedulerPolicy._collect_active_leases
# ===========================================================================


class TestCollectActiveLeases:
    """_collect_active_leases — lease-store protocol negotiation and filtering."""

    def test_none_lease_store_returns_empty_list(self):
        policy = _policy(lease_store=None)
        assert policy._collect_active_leases() == []

    def test_list_store_active_lease_included(self):
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/x.py")
        policy = _policy(lease_store=[active])
        result = policy._collect_active_leases()
        assert active in result

    def test_list_store_claimed_lease_included(self):
        claimed = _lease(task_id="t-c", state=LeaseState.CLAIMED, path_lock="src/f.py")
        policy = _policy(lease_store=[claimed])
        assert claimed in policy._collect_active_leases()

    def test_list_store_renewing_lease_included(self):
        renewing = _lease(task_id="t-r", state=LeaseState.RENEWING, path_lock="src/f.py")
        policy = _policy(lease_store=[renewing])
        assert renewing in policy._collect_active_leases()

    def test_list_store_releasing_lease_excluded(self):
        releasing = _lease(task_id="t-rel", state=LeaseState.RELEASING, path_lock="src/f.py")
        policy = _policy(lease_store=[releasing])
        assert policy._collect_active_leases() == []

    def test_list_store_expired_lease_excluded(self):
        expired = _lease(task_id="t-exp", state=LeaseState.EXPIRED, path_lock="src/f.py")
        policy = _policy(lease_store=[expired])
        assert policy._collect_active_leases() == []

    def test_list_store_unclaimed_lease_excluded(self):
        unclaimed = _lease(task_id="t-unc", state=LeaseState.UNCLAIMED, path_lock="src/f.py")
        policy = _policy(lease_store=[unclaimed])
        assert policy._collect_active_leases() == []

    def test_list_store_lease_without_path_lock_excluded(self):
        no_lock = _lease(state=LeaseState.ACTIVE, path_lock=None)
        policy = _policy(lease_store=[no_lock])
        assert policy._collect_active_leases() == []

    def test_list_store_filters_non_task_lease_objects(self):
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/x.py")
        policy = _policy(lease_store=[active, "garbage", 99, None])
        result = policy._collect_active_leases()
        assert result == [active]

    def test_callable_list_leases_method_used(self):
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/y.py")
        store = MagicMock()
        store.list_leases.return_value = [active]
        policy = _policy(lease_store=store)
        result = policy._collect_active_leases()
        store.list_leases.assert_called_once()
        assert active in result

    def test_callable_list_leases_called_not_iterated(self):
        """When list_leases() exists, the store is NOT iterated directly."""
        store = MagicMock()
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/z.py")
        store.list_leases.return_value = [active]
        policy = _policy(lease_store=store)
        policy._collect_active_leases()
        # __iter__ must NOT be called since list_leases takes precedence
        store.__iter__.assert_not_called()

    def test_exception_from_list_leases_returns_empty(self):
        store = MagicMock()
        store.list_leases.side_effect = RuntimeError("database down")
        policy = _policy(lease_store=store)
        result = policy._collect_active_leases()
        assert result == []

    def test_exception_from_iteration_returns_empty(self):
        class BreakOnIter:
            def __iter__(self):
                raise ValueError("iteration broken")

        policy = _policy(lease_store=BreakOnIter())
        result = policy._collect_active_leases()
        assert result == []

    def test_mixed_states_only_active_states_returned(self):
        leases = [
            _lease(task_id="t-a", state=LeaseState.ACTIVE, path_lock="src/a.py"),
            _lease(task_id="t-c", state=LeaseState.CLAIMED, path_lock="src/c.py"),
            _lease(task_id="t-r", state=LeaseState.RENEWING, path_lock="src/r.py"),
            _lease(task_id="t-rel", state=LeaseState.RELEASING, path_lock="src/rel.py"),
            _lease(task_id="t-exp", state=LeaseState.EXPIRED, path_lock="src/exp.py"),
            _lease(task_id="t-unc", state=LeaseState.UNCLAIMED, path_lock="src/unc.py"),
        ]
        policy = _policy(lease_store=leases)
        result = policy._collect_active_leases()
        returned_ids = {l.task_id for l in result}
        assert returned_ids == {"t-a", "t-c", "t-r"}


# ===========================================================================
# 6. SchedulerPolicy._score_node
# ===========================================================================


class TestScoreNode:
    """Internal _score_node — composite arithmetic verification."""

    def setup_method(self):
        self.policy = _policy()

    def test_perfect_node_scores_one(self):
        node = _node(availability_score=1.0, active_agents=0, capabilities={"python": True})
        composite, reason, avail_agents, conflicts = self.policy._score_node(
            node, "python", None, []
        )
        assert composite == pytest.approx(1.0, abs=1e-4)

    def test_zero_node_scores_zero(self):
        # availability=0, all agents used (capacity=0), unknown type (affinity=0), conflict (safety=0)
        conflict_lease = _lease(owner_node="node-1", path_lock="src/main.py")
        node = _node(availability_score=0.0, active_agents=_MAX_AGENTS_PER_NODE, capabilities={"python": False})
        composite, _, _, _ = self.policy._score_node(node, "python", "src/main.py", [conflict_lease])
        assert composite == pytest.approx(0.0, abs=1e-4)

    def test_available_agents_calculation(self):
        node = _node(active_agents=3)
        _, _, avail_agents, _ = self.policy._score_node(node, "general", None, [])
        assert avail_agents == _MAX_AGENTS_PER_NODE - 3

    def test_active_agents_exceeding_max_clamped_to_zero(self):
        node = _node(active_agents=_MAX_AGENTS_PER_NODE + 10)
        _, _, avail_agents, _ = self.policy._score_node(node, "general", None, [])
        assert avail_agents == 0

    def test_reason_contains_all_four_components(self):
        node = _node()
        _, reason, _, _ = self.policy._score_node(node, "general", None, [])
        assert "availability=" in reason
        assert "capacity=" in reason
        assert "affinity=" in reason
        assert "path_safety=" in reason

    def test_reason_contains_path_conflict_annotation(self):
        conflict_lease = _lease(task_id="t-conflict", owner_node="node-1", path_lock="src/x.py")
        node = _node()
        _, reason, _, conflicts = self.policy._score_node(
            node, "general", "src/x.py", [conflict_lease]
        )
        assert "PATH CONFLICT" in reason
        assert "t-conflict" in reason

    def test_composite_score_rounded_to_four_decimals(self):
        node = _node(availability_score=0.333, active_agents=3)
        composite, _, _, _ = self.policy._score_node(node, "general", None, [])
        # Round-trip: score rounded to 4 decimal places
        assert composite == round(composite, 4)

    def test_weight_components_sum_gives_composite(self):
        """Manually verify the weighted formula using a known node."""
        node = _node(availability_score=0.6, active_agents=4, capabilities={"docker": True})
        composite, _, avail, _ = self.policy._score_node(node, "docker", None, [])
        cap_score = avail / _MAX_AGENTS_PER_NODE  # 4/8 = 0.5
        expected = round(
            _W_AVAILABILITY * 0.6
            + _W_CAPACITY * cap_score
            + _W_AFFINITY * 1.0
            + _W_PATH_SAFETY * 1.0,
            4,
        )
        assert composite == pytest.approx(expected, abs=1e-4)


# ===========================================================================
# 7. SchedulerPolicy.recommend – public API
# ===========================================================================


class TestRecommend:
    """Public recommend() method — sorting, filtering, path locking, affinity."""

    def test_empty_store_returns_empty_list(self):
        policy = _policy(ranked_nodes=[])
        assert policy.recommend() == []

    def test_single_node_returns_one_recommendation(self):
        policy = _policy(ranked_nodes=[_node()])
        recs = policy.recommend()
        assert len(recs) == 1
        assert recs[0].node_id == "node-1"

    def test_recommendations_sorted_descending_by_score(self):
        low = _node(node_id="low", availability_score=0.1, active_agents=7)
        high = _node(node_id="high", availability_score=0.95, active_agents=0)
        policy = _policy(ranked_nodes=[low, high])
        recs = policy.recommend()
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_tie_break_by_node_id_alphabetical(self):
        # Both nodes identical parameters → same score → tie-break on node_id
        node_z = _node(node_id="zzz", availability_score=0.5, active_agents=4)
        node_a = _node(node_id="aaa", availability_score=0.5, active_agents=4)
        policy = _policy(ranked_nodes=[node_z, node_a])
        recs = policy.recommend()
        assert recs[0].node_id == "aaa"

    def test_exclude_nodes_removes_specified_node(self):
        n1 = _node(node_id="node-1")
        n2 = _node(node_id="node-2")
        policy = _policy(ranked_nodes=[n1, n2])
        recs = policy.recommend(exclude_nodes=["node-1"])
        ids = [r.node_id for r in recs]
        assert "node-1" not in ids
        assert "node-2" in ids

    def test_exclude_all_nodes_returns_empty(self):
        n1 = _node(node_id="node-1")
        policy = _policy(ranked_nodes=[n1])
        recs = policy.recommend(exclude_nodes=["node-1"])
        assert recs == []

    def test_exclude_none_equivalent_to_empty_list(self):
        n1 = _node(node_id="node-1")
        policy = _policy(ranked_nodes=[n1])
        recs_none = policy.recommend(exclude_nodes=None)
        recs_empty = policy.recommend(exclude_nodes=[])
        assert [r.node_id for r in recs_none] == [r.node_id for r in recs_empty]

    def test_exclude_nonexistent_node_is_noop(self):
        n1 = _node(node_id="node-1")
        policy = _policy(ranked_nodes=[n1])
        recs = policy.recommend(exclude_nodes=["phantom-99"])
        assert len(recs) == 1
        assert recs[0].node_id == "node-1"

    def test_path_lock_conflict_reduces_score(self):
        n1 = _node(node_id="node-1", availability_score=0.9, active_agents=0)
        conflict = _lease(task_id="t-c", owner_node="node-1", path_lock="src/foo.py")
        policy_clean = _policy(ranked_nodes=[n1], lease_store=None)
        policy_conflict = _policy(ranked_nodes=[n1], lease_store=[conflict])
        score_clean = policy_clean.recommend(path_lock="src/foo.py")[0].score
        score_conflict = policy_conflict.recommend(path_lock="src/foo.py")[0].score
        assert score_clean > score_conflict

    def test_path_conflicts_populated_when_conflict_exists(self):
        n1 = _node(node_id="node-1")
        conflict = _lease(task_id="conflicting-task", owner_node="node-1", path_lock="src/bar.py")
        policy = _policy(ranked_nodes=[n1], lease_store=[conflict])
        recs = policy.recommend(path_lock="src/bar.py")
        assert "conflicting-task" in recs[0].path_conflicts

    def test_path_conflicts_empty_when_no_conflict(self):
        n1 = _node(node_id="node-1")
        policy = _policy(ranked_nodes=[n1], lease_store=None)
        recs = policy.recommend(path_lock="src/any.py")
        assert recs[0].path_conflicts == []

    def test_available_agents_correct_in_recommendation(self):
        n1 = _node(node_id="node-1", active_agents=3)
        policy = _policy(ranked_nodes=[n1])
        recs = policy.recommend()
        assert recs[0].available_agents == _MAX_AGENTS_PER_NODE - 3

    def test_general_task_type_default(self):
        n1 = _node(node_id="node-1", capabilities={})
        policy = _policy(ranked_nodes=[n1])
        # Default is "general" — should still return recommendation
        recs = policy.recommend()
        assert len(recs) == 1

    def test_specific_task_type_affects_score(self):
        # Node with matching capability should outscore node without
        n_cap = _node(node_id="capable", capabilities={"claude": True})
        n_nocap = _node(node_id="incapable", capabilities={"claude": False})
        policy = _policy(ranked_nodes=[n_cap, n_nocap])
        recs = policy.recommend(task_type="claude")
        cap_rec = next(r for r in recs if r.node_id == "capable")
        nocap_rec = next(r for r in recs if r.node_id == "incapable")
        assert cap_rec.score > nocap_rec.score

    def test_recommendation_score_in_valid_range(self):
        n1 = _node(availability_score=0.75, active_agents=3)
        policy = _policy(ranked_nodes=[n1])
        recs = policy.recommend(task_type="python", path_lock=None)
        assert 0.0 <= recs[0].score <= 1.0

    def test_reason_string_present_in_recommendation(self):
        n1 = _node()
        policy = _policy(ranked_nodes=[n1])
        recs = policy.recommend()
        assert recs[0].reason != ""

    def test_multiple_nodes_all_returned(self):
        nodes = [_node(node_id=f"n{i}") for i in range(5)]
        policy = _policy(ranked_nodes=nodes)
        recs = policy.recommend()
        assert len(recs) == 5

    def test_scheduler_view_get_called_once_per_recommend(self):
        store = _mock_store([_node()])
        policy = SchedulerPolicy(heartbeat_store=store)
        policy.recommend()
        assert store.get_scheduler_view.call_count == 1

    def test_recommend_with_no_ranked_nodes_key_in_view(self):
        """Defensive: scheduler view missing 'ranked_nodes' key returns empty list."""
        store = MagicMock()
        store.get_scheduler_view.return_value = {}  # no 'ranked_nodes' key
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend()
        assert recs == []


# ===========================================================================
# 8. SchedulerPolicy.get_stats / _record_stats
# ===========================================================================


class TestStatsTracking:
    """Stats counters, latency averaging, and initial state."""

    def test_initial_state_all_zero(self):
        policy = _policy()
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 0
        assert stats["cache_hits"] == 0
        assert stats["avg_latency_ms"] is None

    def test_stats_keys_present(self):
        policy = _policy()
        stats = policy.get_stats()
        assert {"total_recommendations", "cache_hits", "avg_latency_ms"} <= stats.keys()

    def test_total_increments_after_each_recommend(self):
        policy = _policy(ranked_nodes=[_node()])
        for i in range(1, 6):
            policy.recommend()
            assert policy.get_stats()["total_recommendations"] == i

    def test_avg_latency_is_none_before_first_call(self):
        policy = _policy()
        assert policy.get_stats()["avg_latency_ms"] is None

    def test_avg_latency_non_none_after_first_call(self):
        policy = _policy(ranked_nodes=[_node()])
        policy.recommend()
        avg = policy.get_stats()["avg_latency_ms"]
        assert avg is not None

    def test_avg_latency_non_negative(self):
        policy = _policy(ranked_nodes=[_node()])
        policy.recommend()
        policy.recommend()
        avg = policy.get_stats()["avg_latency_ms"]
        assert isinstance(avg, float)
        assert avg >= 0.0

    def test_empty_candidates_still_increments_total(self):
        policy = _policy(ranked_nodes=[])
        policy.recommend()
        assert policy.get_stats()["total_recommendations"] == 1

    def test_record_stats_directly_with_known_elapsed(self):
        """_record_stats with a synthetic t0 produces a measurable latency sample."""
        policy = _policy()
        synthetic_elapsed_ms = 50  # ms
        t0 = time.monotonic() - synthetic_elapsed_ms / 1000.0
        policy._record_stats(t0)
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 1
        assert stats["avg_latency_ms"] is not None
        assert stats["avg_latency_ms"] >= synthetic_elapsed_ms * 0.9  # allow 10% tolerance

    def test_cache_hits_starts_at_zero_and_stays(self):
        """cache_hits is a placeholder — it should never be auto-incremented."""
        policy = _policy(ranked_nodes=[_node()])
        policy.recommend()
        policy.recommend()
        assert policy.get_stats()["cache_hits"] == 0

    def test_thread_safety_of_stats_counter(self):
        """20 concurrent recommend() calls must all increment total_recommendations."""
        nodes = [_node(node_id=f"n{i}") for i in range(4)]
        policy = _policy(ranked_nodes=nodes)
        threads = [Thread(target=policy.recommend) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert policy.get_stats()["total_recommendations"] == 20


# ===========================================================================
# 9. get_scheduler_policy / reset_scheduler_policy – singleton lifecycle
# ===========================================================================


class TestSingletonLifecycle:
    """get_scheduler_policy and reset_scheduler_policy behaviour."""

    def test_get_returns_scheduler_policy_instance(self):
        store = _mock_store()
        policy = get_scheduler_policy(heartbeat_store=store)
        assert isinstance(policy, SchedulerPolicy)

    def test_subsequent_calls_return_same_instance(self):
        store = _mock_store()
        p1 = get_scheduler_policy(heartbeat_store=store)
        p2 = get_scheduler_policy(heartbeat_store=_mock_store())  # 2nd arg ignored
        assert p1 is p2

    def test_second_call_ignores_new_heartbeat_store(self):
        store_a = _mock_store()
        store_b = _mock_store()
        p1 = get_scheduler_policy(heartbeat_store=store_a)
        p2 = get_scheduler_policy(heartbeat_store=store_b)
        assert p2._hb_store is store_a  # store_b ignored

    def test_reset_clears_singleton(self):
        import forge_harness.webhook_server.services.scheduler_policy as mod

        get_scheduler_policy(heartbeat_store=_mock_store())
        reset_scheduler_policy()
        assert mod._policy_instance is None

    def test_reset_allows_fresh_instance(self):
        store = _mock_store()
        p1 = get_scheduler_policy(heartbeat_store=store)
        reset_scheduler_policy()
        p2 = get_scheduler_policy(heartbeat_store=store)
        assert p1 is not p2

    def test_lease_store_forwarded_to_policy(self):
        store = _mock_store()
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/x.py")
        policy = get_scheduler_policy(heartbeat_store=store, lease_store=[active])
        assert policy._lease_store == [active]

    def test_no_heartbeat_store_uses_default_via_import(self):
        """When heartbeat_store=None, get_scheduler_policy imports get_heartbeat_store."""
        mock_store = _mock_store()
        import forge_harness.webhook_server.services.heartbeat_store as hb_mod

        original = hb_mod.get_heartbeat_store
        hb_mod.get_heartbeat_store = lambda: mock_store
        try:
            policy = get_scheduler_policy()
            assert isinstance(policy, SchedulerPolicy)
            assert policy._hb_store is mock_store
        finally:
            hb_mod.get_heartbeat_store = original

    def test_reset_idempotent_when_already_none(self):
        """Calling reset when no singleton exists should not raise."""
        import forge_harness.webhook_server.services.scheduler_policy as mod

        assert mod._policy_instance is None
        reset_scheduler_policy()  # should not raise
        assert mod._policy_instance is None


# ===========================================================================
# 10. Module-level constants
# ===========================================================================


class TestModuleConstants:
    """Sanity checks on the scoring constants defined at module level."""

    def test_weights_sum_to_one(self):
        total = _W_AVAILABILITY + _W_CAPACITY + _W_AFFINITY + _W_PATH_SAFETY
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_max_agents_per_node_positive(self):
        assert _MAX_AGENTS_PER_NODE > 0

    def test_task_type_capability_map_non_empty(self):
        assert len(_TASK_TYPE_CAPABILITY_MAP) > 0

    def test_all_known_task_types_have_string_capability_key(self):
        for task_type, cap_key in _TASK_TYPE_CAPABILITY_MAP.items():
            assert isinstance(task_type, str) and task_type
            assert isinstance(cap_key, str) and cap_key

    def test_individual_weights_positive(self):
        for w in (_W_AVAILABILITY, _W_CAPACITY, _W_AFFINITY, _W_PATH_SAFETY):
            assert w > 0.0

    def test_individual_weights_less_than_one(self):
        for w in (_W_AVAILABILITY, _W_CAPACITY, _W_AFFINITY, _W_PATH_SAFETY):
            assert w < 1.0


# ===========================================================================
# 11. Error resilience
# ===========================================================================


class TestErrorResilience:
    """The policy should degrade gracefully under failure conditions."""

    def test_broken_lease_store_does_not_prevent_recommendations(self):
        store = MagicMock()
        store.list_leases.side_effect = RuntimeError("DB offline")
        n1 = _node(node_id="node-1")
        policy = SchedulerPolicy(
            heartbeat_store=_mock_store([n1]),
            lease_store=store,
        )
        recs = policy.recommend(path_lock="src/main.py")
        assert len(recs) == 1
        # path_safety defaults to 1.0 when lease data is unavailable
        assert recs[0].path_conflicts == []

    def test_lease_store_raising_on_iteration_falls_back_gracefully(self):
        class BadIter:
            def __iter__(self):
                raise OSError("disk error")

        n1 = _node()
        policy = SchedulerPolicy(
            heartbeat_store=_mock_store([n1]),
            lease_store=BadIter(),
        )
        recs = policy.recommend()
        assert len(recs) == 1

    def test_missing_availability_score_defaults_to_zero(self):
        """Node without 'availability_score' key uses default 0.0."""
        n1 = {"node_id": "node-1", "active_agents": 0}  # no availability_score key
        policy = _policy(ranked_nodes=[n1])
        recs = policy.recommend()
        assert len(recs) == 1
        # availability contribution = 0 → score composed from other weights only
        assert recs[0].score >= 0.0

    def test_missing_active_agents_defaults_gracefully(self):
        """Node without 'active_agents' key is treated as 0 active agents."""
        n1 = {"node_id": "node-1", "availability_score": 0.9}  # no active_agents key
        policy = _policy(ranked_nodes=[n1])
        recs = policy.recommend()
        assert len(recs) == 1
        # With 0 active_agents → full capacity (available_agents = _MAX_AGENTS_PER_NODE)
        assert recs[0].available_agents == _MAX_AGENTS_PER_NODE
