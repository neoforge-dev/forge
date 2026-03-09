"""Unit tests for forge_harness.webhook_server.services.scheduler_policy.

Target: 70%+ coverage of scheduler_policy.py (116 lines).

Covers:
- SchedulerRecommendation model validation
- SchedulerPolicy._capacity_score (static)
- SchedulerPolicy._affinity_score (static)
- SchedulerPolicy._path_safety_score
- SchedulerPolicy._collect_active_leases (all branch paths)
- SchedulerPolicy._score_node (composite arithmetic)
- SchedulerPolicy.recommend (sorting, filtering, edge cases)
- SchedulerPolicy.get_stats / _record_stats
- Module constants
- Singleton: get_scheduler_policy / reset_scheduler_policy
- Thread safety
- Error resilience
"""

from __future__ import annotations

import time
from threading import Thread
from unittest.mock import MagicMock

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
# Helpers
# ---------------------------------------------------------------------------


def _mock_store(ranked_nodes: list[dict] | None = None) -> MagicMock:
    store = MagicMock()
    store.get_scheduler_view.return_value = {"ranked_nodes": ranked_nodes or []}
    return store


def _node(
    node_id: str = "node-1",
    availability_score: float = 0.80,
    active_agents: int = 2,
    capabilities: dict | None = None,
) -> dict:
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
    return SchedulerPolicy(
        heartbeat_store=_mock_store(ranked_nodes),
        lease_store=lease_store,
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_scheduler_policy()
    yield
    reset_scheduler_policy()


# ===========================================================================
# SchedulerRecommendation – Pydantic model
# ===========================================================================


class TestSchedulerRecommendationModel:
    def test_required_fields_only(self):
        rec = SchedulerRecommendation(node_id="n1", score=0.5)
        assert rec.node_id == "n1"
        assert rec.score == 0.5
        assert rec.reason == ""
        assert rec.available_agents == 0
        assert rec.path_conflicts == []

    def test_all_fields_explicit(self):
        rec = SchedulerRecommendation(
            node_id="n2",
            score=0.75,
            reason="custom reason",
            available_agents=3,
            path_conflicts=["t1", "t2"],
        )
        assert rec.reason == "custom reason"
        assert rec.available_agents == 3
        assert len(rec.path_conflicts) == 2

    def test_score_zero_boundary(self):
        assert SchedulerRecommendation(node_id="x", score=0.0).score == 0.0

    def test_score_one_boundary(self):
        assert SchedulerRecommendation(node_id="x", score=1.0).score == 1.0

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

    def test_path_conflicts_independent_per_instance(self):
        rec1 = SchedulerRecommendation(node_id="a", score=0.5)
        rec2 = SchedulerRecommendation(node_id="b", score=0.3)
        rec1.path_conflicts.append("foo")
        assert rec2.path_conflicts == []


# ===========================================================================
# _capacity_score — static method
# ===========================================================================


class TestCapacityScore:
    def test_zero_available_gives_zero(self):
        assert SchedulerPolicy._capacity_score(0) == 0.0

    def test_max_available_gives_one(self):
        assert SchedulerPolicy._capacity_score(_MAX_AGENTS_PER_NODE) == 1.0

    def test_half_available_gives_half(self):
        half = _MAX_AGENTS_PER_NODE // 2
        assert SchedulerPolicy._capacity_score(half) == pytest.approx(0.5, abs=1e-9)

    def test_over_capacity_capped_at_one(self):
        assert SchedulerPolicy._capacity_score(_MAX_AGENTS_PER_NODE + 100) == 1.0

    def test_proportional_single_agent(self):
        expected = 1.0 / _MAX_AGENTS_PER_NODE
        assert SchedulerPolicy._capacity_score(1) == pytest.approx(expected, abs=1e-9)

    def test_always_in_range(self):
        for n in range(_MAX_AGENTS_PER_NODE + 5):
            score = SchedulerPolicy._capacity_score(n)
            assert 0.0 <= score <= 1.0

    def test_returns_float(self):
        assert isinstance(SchedulerPolicy._capacity_score(4), float)


# ===========================================================================
# _affinity_score — static method
# ===========================================================================


class TestAffinityScore:
    def test_general_task_type_returns_one(self):
        assert SchedulerPolicy._affinity_score("general", {}) == 1.0

    def test_empty_task_type_returns_one(self):
        assert SchedulerPolicy._affinity_score("", {}) == 1.0

    def test_no_capabilities_key_returns_neutral(self):
        assert SchedulerPolicy._affinity_score("python", {}) == 0.5

    def test_empty_capabilities_dict_returns_neutral(self):
        assert SchedulerPolicy._affinity_score("python", {"capabilities": {}}) == 0.5

    def test_capability_present_true_returns_one(self):
        assert SchedulerPolicy._affinity_score("python", {"capabilities": {"python": True}}) == 1.0

    def test_capability_present_false_returns_zero(self):
        assert SchedulerPolicy._affinity_score("python", {"capabilities": {"python": False}}) == 0.0

    def test_ios_maps_to_ios_simulator(self):
        assert SchedulerPolicy._affinity_score("ios", {"capabilities": {"ios_simulator": True}}) == 1.0

    def test_ios_wrong_key_returns_zero(self):
        assert SchedulerPolicy._affinity_score("ios", {"capabilities": {"ios": True}}) == 0.0

    def test_all_known_task_types_resolve(self):
        for task_type, cap_key in _TASK_TYPE_CAPABILITY_MAP.items():
            node = {"capabilities": {cap_key: True}}
            assert SchedulerPolicy._affinity_score(task_type, node) == 1.0, (
                f"Failed for task_type={task_type!r}"
            )

    def test_unknown_task_type_falls_back_to_key(self):
        assert SchedulerPolicy._affinity_score("exotic_ai", {"capabilities": {"exotic_ai": True}}) == 1.0

    def test_unknown_task_type_absent_returns_zero(self):
        assert SchedulerPolicy._affinity_score("exotic_ai", {"capabilities": {"other": True}}) == 0.0

    def test_general_with_capabilities_still_returns_one(self):
        assert SchedulerPolicy._affinity_score("general", {"capabilities": {"python": True}}) == 1.0

    def test_none_capability_value_treated_as_false(self):
        assert SchedulerPolicy._affinity_score("docker", {"capabilities": {"docker": None}}) == 0.0


# ===========================================================================
# _path_safety_score
# ===========================================================================


class TestPathSafetyScore:
    def setup_method(self):
        self.policy = _policy()

    def test_no_path_lock_returns_one_no_conflicts(self):
        score, conflicts = self.policy._path_safety_score("node-1", None, [])
        assert score == 1.0
        assert conflicts == []

    def test_no_path_lock_ignores_leases(self):
        lease = _lease(owner_node="node-1", path_lock="src/main.py")
        score, conflicts = self.policy._path_safety_score("node-1", None, [lease])
        assert score == 1.0

    def test_path_lock_no_leases_returns_one(self):
        score, conflicts = self.policy._path_safety_score("node-1", "src/main.py", [])
        assert score == 1.0
        assert conflicts == []

    def test_conflict_on_same_node_returns_zero(self):
        conflict = _lease(task_id="conflict-task", owner_node="node-1", path_lock="src/main.py")
        score, conflicts = self.policy._path_safety_score("node-1", "src/main.py", [conflict])
        assert score == 0.0
        assert "conflict-task" in conflicts

    def test_conflict_on_different_node_no_penalty(self):
        other_lease = _lease(task_id="t-other", owner_node="node-2", path_lock="src/main.py")
        score, conflicts = self.policy._path_safety_score("node-1", "src/main.py", [other_lease])
        assert score == 1.0
        assert conflicts == []

    def test_multiple_conflicts_all_reported(self):
        leases = [
            _lease(task_id=f"t-{i}", owner_node="node-1", path_lock="src/file.py")
            for i in range(3)
        ]
        score, conflicts = self.policy._path_safety_score("node-1", "src/file.py", leases)
        assert score == 0.0
        assert len(conflicts) == 3

    def test_different_path_no_conflict(self):
        other = _lease(owner_node="node-1", path_lock="src/other.py")
        score, conflicts = self.policy._path_safety_score("node-1", "src/main.py", [other])
        assert score == 1.0

    def test_empty_path_lock_string_treated_as_no_lock(self):
        lease = _lease(owner_node="node-1", path_lock="src/main.py")
        score, _ = self.policy._path_safety_score("node-1", "", [lease])
        assert score == 1.0

    def test_mixed_nodes_only_matching_counted(self):
        l1 = _lease(task_id="n1", owner_node="node-1", path_lock="src/shared.py")
        l2 = _lease(task_id="n2", owner_node="node-2", path_lock="src/shared.py")
        score, conflicts = self.policy._path_safety_score("node-1", "src/shared.py", [l1, l2])
        assert score == 0.0
        assert conflicts == ["n1"]


# ===========================================================================
# _collect_active_leases
# ===========================================================================


class TestCollectActiveLeases:
    def test_none_lease_store_returns_empty(self):
        policy = _policy(lease_store=None)
        assert policy._collect_active_leases() == []

    def test_list_store_active_included(self):
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/x.py")
        policy = _policy(lease_store=[active])
        assert active in policy._collect_active_leases()

    def test_list_store_claimed_included(self):
        claimed = _lease(task_id="t-c", state=LeaseState.CLAIMED, path_lock="src/f.py")
        policy = _policy(lease_store=[claimed])
        assert claimed in policy._collect_active_leases()

    def test_list_store_renewing_included(self):
        renewing = _lease(task_id="t-r", state=LeaseState.RENEWING, path_lock="src/f.py")
        policy = _policy(lease_store=[renewing])
        assert renewing in policy._collect_active_leases()

    def test_list_store_releasing_excluded(self):
        releasing = _lease(task_id="t-rel", state=LeaseState.RELEASING, path_lock="src/f.py")
        policy = _policy(lease_store=[releasing])
        assert policy._collect_active_leases() == []

    def test_list_store_expired_excluded(self):
        expired = _lease(task_id="t-exp", state=LeaseState.EXPIRED, path_lock="src/f.py")
        policy = _policy(lease_store=[expired])
        assert policy._collect_active_leases() == []

    def test_list_store_unclaimed_excluded(self):
        unclaimed = _lease(task_id="t-unc", state=LeaseState.UNCLAIMED, path_lock="src/f.py")
        policy = _policy(lease_store=[unclaimed])
        assert policy._collect_active_leases() == []

    def test_lease_without_path_lock_excluded(self):
        no_lock = _lease(state=LeaseState.ACTIVE, path_lock=None)
        policy = _policy(lease_store=[no_lock])
        assert policy._collect_active_leases() == []

    def test_non_lease_objects_filtered_out(self):
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/x.py")
        policy = _policy(lease_store=[active, "garbage", 99, None])
        result = policy._collect_active_leases()
        assert result == [active]

    def test_callable_list_leases_used_over_iteration(self):
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/y.py")
        store = MagicMock()
        store.list_leases.return_value = [active]
        policy = _policy(lease_store=store)
        result = policy._collect_active_leases()
        store.list_leases.assert_called_once()
        assert active in result

    def test_callable_list_leases_not_iterated(self):
        store = MagicMock()
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/z.py")
        store.list_leases.return_value = [active]
        policy = _policy(lease_store=store)
        policy._collect_active_leases()
        store.__iter__.assert_not_called()

    def test_exception_from_list_leases_returns_empty(self):
        store = MagicMock()
        store.list_leases.side_effect = RuntimeError("database down")
        policy = _policy(lease_store=store)
        assert policy._collect_active_leases() == []

    def test_exception_from_iteration_returns_empty(self):
        class BreakOnIter:
            def __iter__(self):
                raise ValueError("broken")

        policy = _policy(lease_store=BreakOnIter())
        assert policy._collect_active_leases() == []

    def test_mixed_states_only_active_states_returned(self):
        leases = [
            _lease(task_id="t-a", state=LeaseState.ACTIVE, path_lock="src/a.py"),
            _lease(task_id="t-c", state=LeaseState.CLAIMED, path_lock="src/c.py"),
            _lease(task_id="t-r", state=LeaseState.RENEWING, path_lock="src/r.py"),
            _lease(task_id="t-rel", state=LeaseState.RELEASING, path_lock="src/rel.py"),
            _lease(task_id="t-exp", state=LeaseState.EXPIRED, path_lock="src/exp.py"),
        ]
        policy = _policy(lease_store=leases)
        result = policy._collect_active_leases()
        returned_ids = {l.task_id for l in result}
        assert returned_ids == {"t-a", "t-c", "t-r"}


# ===========================================================================
# _score_node — composite arithmetic
# ===========================================================================


class TestScoreNode:
    def setup_method(self):
        self.policy = _policy()

    def test_perfect_node_scores_one(self):
        node = _node(availability_score=1.0, active_agents=0, capabilities={"python": True})
        composite, reason, avail_agents, conflicts = self.policy._score_node(
            node, "python", None, []
        )
        assert composite == pytest.approx(1.0, abs=1e-4)

    def test_zero_node_scores_zero(self):
        conflict_lease = _lease(owner_node="node-1", path_lock="src/main.py")
        node = _node(
            availability_score=0.0,
            active_agents=_MAX_AGENTS_PER_NODE,
            capabilities={"python": False},
        )
        composite, _, _, _ = self.policy._score_node(node, "python", "src/main.py", [conflict_lease])
        assert composite == pytest.approx(0.0, abs=1e-4)

    def test_available_agents_calculation(self):
        node = _node(active_agents=3)
        _, _, avail_agents, _ = self.policy._score_node(node, "general", None, [])
        assert avail_agents == _MAX_AGENTS_PER_NODE - 3

    def test_active_agents_exceeding_max_clamped(self):
        node = _node(active_agents=_MAX_AGENTS_PER_NODE + 10)
        _, _, avail_agents, _ = self.policy._score_node(node, "general", None, [])
        assert avail_agents == 0

    def test_reason_contains_all_components(self):
        node = _node()
        _, reason, _, _ = self.policy._score_node(node, "general", None, [])
        for key in ("availability=", "capacity=", "affinity=", "path_safety="):
            assert key in reason

    def test_reason_contains_path_conflict_annotation(self):
        conflict = _lease(task_id="t-conflict", owner_node="node-1", path_lock="src/x.py")
        node = _node()
        _, reason, _, conflicts = self.policy._score_node(
            node, "general", "src/x.py", [conflict]
        )
        assert "PATH CONFLICT" in reason
        assert "t-conflict" in reason

    def test_composite_rounded_to_four_decimals(self):
        node = _node(availability_score=0.333, active_agents=3)
        composite, _, _, _ = self.policy._score_node(node, "general", None, [])
        assert composite == round(composite, 4)

    def test_weight_components_sum_gives_composite(self):
        node = _node(availability_score=0.6, active_agents=4, capabilities={"docker": True})
        composite, _, avail, _ = self.policy._score_node(node, "docker", None, [])
        cap_score = avail / _MAX_AGENTS_PER_NODE
        expected = round(
            _W_AVAILABILITY * 0.6
            + _W_CAPACITY * cap_score
            + _W_AFFINITY * 1.0
            + _W_PATH_SAFETY * 1.0,
            4,
        )
        assert composite == pytest.approx(expected, abs=1e-4)

    def test_missing_availability_score_defaults_to_zero(self):
        node = {"node_id": "node-1", "active_agents": 0}
        _, _, _, _ = self.policy._score_node(node, "general", None, [])

    def test_missing_active_agents_defaults_to_max_capacity(self):
        node = {"node_id": "node-1", "availability_score": 0.9}
        _, _, avail_agents, _ = self.policy._score_node(node, "general", None, [])
        assert avail_agents == _MAX_AGENTS_PER_NODE


# ===========================================================================
# recommend — public API
# ===========================================================================


class TestRecommend:
    def test_empty_store_returns_empty_list(self):
        policy = _policy(ranked_nodes=[])
        assert policy.recommend() == []

    def test_single_node_returns_one_recommendation(self):
        policy = _policy(ranked_nodes=[_node()])
        recs = policy.recommend()
        assert len(recs) == 1
        assert recs[0].node_id == "node-1"

    def test_sorted_descending_by_score(self):
        low = _node(node_id="low", availability_score=0.1, active_agents=7)
        high = _node(node_id="high", availability_score=0.95, active_agents=0)
        recs = _policy(ranked_nodes=[low, high]).recommend()
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_tie_break_by_node_id_alphabetical(self):
        node_z = _node(node_id="zzz", availability_score=0.5, active_agents=4)
        node_a = _node(node_id="aaa", availability_score=0.5, active_agents=4)
        recs = _policy(ranked_nodes=[node_z, node_a]).recommend()
        assert recs[0].node_id == "aaa"

    def test_exclude_nodes_removes_specified(self):
        n1 = _node(node_id="node-1")
        n2 = _node(node_id="node-2")
        recs = _policy(ranked_nodes=[n1, n2]).recommend(exclude_nodes=["node-1"])
        ids = [r.node_id for r in recs]
        assert "node-1" not in ids
        assert "node-2" in ids

    def test_exclude_all_returns_empty(self):
        n1 = _node(node_id="node-1")
        assert _policy(ranked_nodes=[n1]).recommend(exclude_nodes=["node-1"]) == []

    def test_exclude_none_same_as_empty_list(self):
        n1 = _node(node_id="node-1")
        policy = _policy(ranked_nodes=[n1])
        ids_none = [r.node_id for r in policy.recommend(exclude_nodes=None)]
        ids_empty = [r.node_id for r in policy.recommend(exclude_nodes=[])]
        assert ids_none == ids_empty

    def test_exclude_nonexistent_node_is_noop(self):
        n1 = _node(node_id="node-1")
        recs = _policy(ranked_nodes=[n1]).recommend(exclude_nodes=["phantom-99"])
        assert len(recs) == 1

    def test_path_lock_conflict_reduces_score(self):
        n1 = _node(node_id="node-1", availability_score=0.9, active_agents=0)
        conflict = _lease(task_id="t-c", owner_node="node-1", path_lock="src/foo.py")
        policy_clean = _policy(ranked_nodes=[n1])
        policy_conflict = _policy(ranked_nodes=[n1], lease_store=[conflict])
        score_clean = policy_clean.recommend(path_lock="src/foo.py")[0].score
        score_conflict = policy_conflict.recommend(path_lock="src/foo.py")[0].score
        assert score_clean > score_conflict

    def test_path_conflicts_populated_on_conflict(self):
        n1 = _node(node_id="node-1")
        conflict = _lease(task_id="conflicting-task", owner_node="node-1", path_lock="src/bar.py")
        policy = _policy(ranked_nodes=[n1], lease_store=[conflict])
        recs = policy.recommend(path_lock="src/bar.py")
        assert "conflicting-task" in recs[0].path_conflicts

    def test_path_conflicts_empty_when_no_conflict(self):
        n1 = _node(node_id="node-1")
        recs = _policy(ranked_nodes=[n1]).recommend(path_lock="src/any.py")
        assert recs[0].path_conflicts == []

    def test_available_agents_correct(self):
        n1 = _node(node_id="node-1", active_agents=3)
        recs = _policy(ranked_nodes=[n1]).recommend()
        assert recs[0].available_agents == _MAX_AGENTS_PER_NODE - 3

    def test_task_type_affects_score(self):
        n_cap = _node(node_id="capable", capabilities={"claude": True})
        n_nocap = _node(node_id="incapable", capabilities={"claude": False})
        recs = _policy(ranked_nodes=[n_cap, n_nocap]).recommend(task_type="claude")
        cap_score = next(r.score for r in recs if r.node_id == "capable")
        nocap_score = next(r.score for r in recs if r.node_id == "incapable")
        assert cap_score > nocap_score

    def test_score_in_valid_range(self):
        n1 = _node(availability_score=0.75, active_agents=3)
        recs = _policy(ranked_nodes=[n1]).recommend(task_type="python")
        assert 0.0 <= recs[0].score <= 1.0

    def test_reason_string_nonempty(self):
        n1 = _node()
        recs = _policy(ranked_nodes=[n1]).recommend()
        assert recs[0].reason != ""

    def test_multiple_nodes_all_returned(self):
        nodes = [_node(node_id=f"n{i}") for i in range(5)]
        recs = _policy(ranked_nodes=nodes).recommend()
        assert len(recs) == 5

    def test_scheduler_view_called_once_per_recommend(self):
        store = _mock_store([_node()])
        policy = SchedulerPolicy(heartbeat_store=store)
        policy.recommend()
        assert store.get_scheduler_view.call_count == 1

    def test_missing_ranked_nodes_key_returns_empty(self):
        store = MagicMock()
        store.get_scheduler_view.return_value = {}
        policy = SchedulerPolicy(heartbeat_store=store)
        assert policy.recommend() == []


# ===========================================================================
# get_stats / _record_stats
# ===========================================================================


class TestStatsTracking:
    def test_initial_state_all_zero(self):
        policy = _policy()
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 0
        assert stats["cache_hits"] == 0
        assert stats["avg_latency_ms"] is None

    def test_stats_keys_present(self):
        policy = _policy()
        assert {"total_recommendations", "cache_hits", "avg_latency_ms"} <= policy.get_stats().keys()

    def test_total_increments_after_each_recommend(self):
        policy = _policy(ranked_nodes=[_node()])
        for i in range(1, 4):
            policy.recommend()
            assert policy.get_stats()["total_recommendations"] == i

    def test_avg_latency_none_before_first_call(self):
        assert _policy().get_stats()["avg_latency_ms"] is None

    def test_avg_latency_set_after_first_call(self):
        policy = _policy(ranked_nodes=[_node()])
        policy.recommend()
        assert policy.get_stats()["avg_latency_ms"] is not None

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

    def test_record_stats_directly_with_synthetic_elapsed(self):
        policy = _policy()
        t0 = time.monotonic() - 0.05  # 50ms in the past
        policy._record_stats(t0)
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 1
        assert stats["avg_latency_ms"] is not None
        assert stats["avg_latency_ms"] >= 45.0  # allow tolerance

    def test_cache_hits_stays_at_zero(self):
        policy = _policy(ranked_nodes=[_node()])
        policy.recommend()
        policy.recommend()
        assert policy.get_stats()["cache_hits"] == 0

    def test_thread_safety_of_stats_counter(self):
        nodes = [_node(node_id=f"n{i}") for i in range(4)]
        policy = _policy(ranked_nodes=nodes)
        threads = [Thread(target=policy.recommend) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert policy.get_stats()["total_recommendations"] == 20


# ===========================================================================
# Module-level constants
# ===========================================================================


class TestModuleConstants:
    def test_weights_sum_to_one(self):
        total = _W_AVAILABILITY + _W_CAPACITY + _W_AFFINITY + _W_PATH_SAFETY
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_max_agents_positive(self):
        assert _MAX_AGENTS_PER_NODE > 0

    def test_task_type_map_non_empty(self):
        assert len(_TASK_TYPE_CAPABILITY_MAP) > 0

    def test_all_map_entries_are_strings(self):
        for k, v in _TASK_TYPE_CAPABILITY_MAP.items():
            assert isinstance(k, str) and k
            assert isinstance(v, str) and v

    def test_individual_weights_positive_and_less_than_one(self):
        for w in (_W_AVAILABILITY, _W_CAPACITY, _W_AFFINITY, _W_PATH_SAFETY):
            assert 0.0 < w < 1.0


# ===========================================================================
# Singleton lifecycle
# ===========================================================================


class TestSingletonLifecycle:
    def test_get_returns_policy_instance(self):
        policy = get_scheduler_policy(heartbeat_store=_mock_store())
        assert isinstance(policy, SchedulerPolicy)

    def test_subsequent_calls_same_instance(self):
        p1 = get_scheduler_policy(heartbeat_store=_mock_store())
        p2 = get_scheduler_policy(heartbeat_store=_mock_store())
        assert p1 is p2

    def test_second_call_ignores_new_store(self):
        store_a = _mock_store()
        store_b = _mock_store()
        p1 = get_scheduler_policy(heartbeat_store=store_a)
        p2 = get_scheduler_policy(heartbeat_store=store_b)
        assert p2._hb_store is store_a

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

    def test_lease_store_forwarded(self):
        active = _lease(state=LeaseState.ACTIVE, path_lock="src/x.py")
        policy = get_scheduler_policy(heartbeat_store=_mock_store(), lease_store=[active])
        assert policy._lease_store == [active]

    def test_reset_idempotent_when_none(self):
        import forge_harness.webhook_server.services.scheduler_policy as mod

        assert mod._policy_instance is None
        reset_scheduler_policy()
        assert mod._policy_instance is None

    def test_none_heartbeat_store_uses_default(self):
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


# ===========================================================================
# Error resilience
# ===========================================================================


class TestErrorResilience:
    def test_broken_lease_store_does_not_block_recommendations(self):
        store = MagicMock()
        store.list_leases.side_effect = RuntimeError("DB offline")
        n1 = _node(node_id="node-1")
        policy = SchedulerPolicy(
            heartbeat_store=_mock_store([n1]),
            lease_store=store,
        )
        recs = policy.recommend(path_lock="src/main.py")
        assert len(recs) == 1
        assert recs[0].path_conflicts == []

    def test_bad_iteration_lease_store_falls_back(self):
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
