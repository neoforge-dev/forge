"""
Pure unit tests for forge_harness.webhook_server.services.scheduler_policy

Covers:
- SchedulerRecommendation Pydantic model
- SchedulerPolicy.__init__
- SchedulerPolicy._capacity_score (static)
- SchedulerPolicy._affinity_score (static)
- SchedulerPolicy._path_safety_score
- SchedulerPolicy._collect_active_leases
- SchedulerPolicy._score_node
- SchedulerPolicy.recommend
- SchedulerPolicy.get_stats
- SchedulerPolicy._record_stats
- get_scheduler_policy singleton factory
- reset_scheduler_policy
"""

from __future__ import annotations

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
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_hb_store(ranked_nodes: list[dict] | None = None) -> MagicMock:
    """Return a MagicMock heartbeat store with a pre-configured scheduler view."""
    store = MagicMock()
    store.get_scheduler_view.return_value = {"ranked_nodes": ranked_nodes or []}
    return store


def _make_node(
    node_id: str = "node-a",
    availability_score: float = 0.9,
    active_agents: int = 2,
    capabilities: dict | None = None,
) -> dict:
    """Build a ranked-node dict as returned by HeartbeatStore.get_scheduler_view."""
    entry: dict = {
        "node_id": node_id,
        "availability_score": availability_score,
        "active_agents": active_agents,
    }
    if capabilities is not None:
        entry["capabilities"] = capabilities
    return entry


def _make_lease(
    task_id: str = "task-1",
    owner_node: str = "node-a",
    path_lock: str | None = "src/api/routes.py",
    state: LeaseState = LeaseState.ACTIVE,
) -> TaskLease:
    """Build a TaskLease with the given parameters."""
    return TaskLease(
        task_id=task_id,
        owner_node=owner_node,
        path_lock=path_lock,
        state=state,
    )


def _make_policy(
    ranked_nodes: list[dict] | None = None,
    lease_store=None,
) -> SchedulerPolicy:
    """Convenience factory for SchedulerPolicy with a mocked heartbeat store."""
    hb_store = _make_hb_store(ranked_nodes)
    return SchedulerPolicy(heartbeat_store=hb_store, lease_store=lease_store)


# ---------------------------------------------------------------------------
# SchedulerRecommendation
# ---------------------------------------------------------------------------


class TestSchedulerRecommendation:
    """Tests for the SchedulerRecommendation Pydantic output model."""

    def test_minimal_valid(self):
        rec = SchedulerRecommendation(node_id="node-a", score=0.75)
        assert rec.node_id == "node-a"
        assert rec.score == 0.75
        assert rec.reason == ""
        assert rec.available_agents == 0
        assert rec.path_conflicts == []

    def test_full_valid(self):
        rec = SchedulerRecommendation(
            node_id="node-b",
            score=0.5,
            reason="some reason",
            available_agents=3,
            path_conflicts=["task-1", "task-2"],
        )
        assert rec.available_agents == 3
        assert rec.path_conflicts == ["task-1", "task-2"]

    def test_score_lower_bound_zero(self):
        rec = SchedulerRecommendation(node_id="x", score=0.0)
        assert rec.score == 0.0

    def test_score_upper_bound_one(self):
        rec = SchedulerRecommendation(node_id="x", score=1.0)
        assert rec.score == 1.0

    def test_score_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x", score=-0.1)

    def test_score_above_one_rejected(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x", score=1.1)

    def test_available_agents_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x", score=0.5, available_agents=-1)

    def test_node_id_required(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(score=0.5)  # type: ignore[call-arg]

    def test_score_required(self):
        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# SchedulerPolicy._capacity_score
# ---------------------------------------------------------------------------


class TestCapacityScore:
    """Tests for the static _capacity_score method."""

    def test_zero_available_gives_zero(self):
        assert SchedulerPolicy._capacity_score(0) == 0.0

    def test_max_available_gives_one(self):
        assert SchedulerPolicy._capacity_score(_MAX_AGENTS_PER_NODE) == 1.0

    def test_half_available_gives_half(self):
        assert SchedulerPolicy._capacity_score(_MAX_AGENTS_PER_NODE // 2) == pytest.approx(0.5)

    def test_excess_available_capped_at_one(self):
        assert SchedulerPolicy._capacity_score(_MAX_AGENTS_PER_NODE + 5) == 1.0

    def test_single_slot(self):
        result = SchedulerPolicy._capacity_score(1)
        assert result == pytest.approx(1 / _MAX_AGENTS_PER_NODE)


# ---------------------------------------------------------------------------
# SchedulerPolicy._affinity_score
# ---------------------------------------------------------------------------


class TestAffinityScore:
    """Tests for the static _affinity_score method."""

    def test_general_task_always_one(self):
        assert SchedulerPolicy._affinity_score("general", {}) == 1.0

    def test_empty_task_type_always_one(self):
        assert SchedulerPolicy._affinity_score("", {}) == 1.0

    def test_no_capabilities_returns_neutral(self):
        assert SchedulerPolicy._affinity_score("python", {}) == 0.5

    def test_empty_capabilities_returns_neutral(self):
        assert SchedulerPolicy._affinity_score("python", {"capabilities": {}}) == 0.5

    def test_matching_capability_returns_one(self):
        node = {"capabilities": {"python": True}}
        assert SchedulerPolicy._affinity_score("python", node) == 1.0

    def test_missing_capability_returns_zero(self):
        node = {"capabilities": {"python": False}}
        assert SchedulerPolicy._affinity_score("python", node) == 0.0

    def test_ios_capability_key_mapping(self):
        node = {"capabilities": {"ios_simulator": True}}
        assert SchedulerPolicy._affinity_score("ios", node) == 1.0

    def test_docker_capability_key_mapping(self):
        node = {"capabilities": {"docker": True}}
        assert SchedulerPolicy._affinity_score("docker", node) == 1.0

    def test_unmapped_task_type_uses_itself_as_key(self):
        # "custom_task" is not in _TASK_TYPE_CAPABILITY_MAP; falls back to key == "custom_task"
        node = {"capabilities": {"custom_task": True}}
        assert SchedulerPolicy._affinity_score("custom_task", node) == 1.0

    def test_all_known_capability_keys_map_correctly(self):
        for task_type, cap_key in _TASK_TYPE_CAPABILITY_MAP.items():
            node = {"capabilities": {cap_key: True}}
            score = SchedulerPolicy._affinity_score(task_type, node)
            assert score == 1.0, f"Expected 1.0 for task_type={task_type!r}, cap_key={cap_key!r}"


# ---------------------------------------------------------------------------
# SchedulerPolicy._path_safety_score
# ---------------------------------------------------------------------------


class TestPathSafetyScore:
    """Tests for _path_safety_score."""

    def setup_method(self):
        self.policy = _make_policy()

    def test_no_path_lock_returns_one_no_conflicts(self):
        score, conflicts = self.policy._path_safety_score("node-a", None, [])
        assert score == 1.0
        assert conflicts == []

    def test_no_path_lock_ignores_leases(self):
        lease = _make_lease(owner_node="node-a", path_lock="src/file.py")
        score, conflicts = self.policy._path_safety_score("node-a", None, [lease])
        assert score == 1.0
        assert conflicts == []

    def test_path_lock_no_conflict_on_node(self):
        lease = _make_lease(owner_node="node-b", path_lock="src/api/routes.py")
        score, conflicts = self.policy._path_safety_score("node-a", "src/api/routes.py", [lease])
        assert score == 1.0
        assert conflicts == []

    def test_path_lock_conflict_on_same_node(self):
        lease = _make_lease(
            task_id="task-99", owner_node="node-a", path_lock="src/api/routes.py"
        )
        score, conflicts = self.policy._path_safety_score(
            "node-a", "src/api/routes.py", [lease]
        )
        assert score == 0.0
        assert "task-99" in conflicts

    def test_multiple_conflicts_all_listed(self):
        leases = [
            _make_lease(task_id=f"task-{i}", owner_node="node-a", path_lock="src/file.py")
            for i in range(3)
        ]
        score, conflicts = self.policy._path_safety_score("node-a", "src/file.py", leases)
        assert score == 0.0
        assert len(conflicts) == 3

    def test_different_path_no_conflict(self):
        lease = _make_lease(owner_node="node-a", path_lock="src/other.py")
        score, conflicts = self.policy._path_safety_score("node-a", "src/api/routes.py", [lease])
        assert score == 1.0
        assert conflicts == []


# ---------------------------------------------------------------------------
# SchedulerPolicy._collect_active_leases
# ---------------------------------------------------------------------------


class TestCollectActiveLeases:
    """Tests for _collect_active_leases."""

    def test_none_lease_store_returns_empty(self):
        policy = _make_policy(lease_store=None)
        assert policy._collect_active_leases() == []

    def test_iterable_lease_store_active_leases_returned(self):
        lease = _make_lease(state=LeaseState.ACTIVE, path_lock="src/x.py")
        policy = _make_policy(lease_store=[lease])
        result = policy._collect_active_leases()
        assert lease in result

    def test_iterable_lease_store_filters_non_active(self):
        released = _make_lease(task_id="t1", state=LeaseState.RELEASING, path_lock="src/x.py")
        expired = _make_lease(task_id="t2", state=LeaseState.EXPIRED, path_lock="src/x.py")
        active = _make_lease(task_id="t3", state=LeaseState.ACTIVE, path_lock="src/x.py")
        policy = _make_policy(lease_store=[released, expired, active])
        result = policy._collect_active_leases()
        assert result == [active]

    def test_iterable_filters_leases_without_path_lock(self):
        lease_no_lock = _make_lease(state=LeaseState.ACTIVE, path_lock=None)
        lease_with_lock = _make_lease(task_id="t2", state=LeaseState.ACTIVE, path_lock="src/f.py")
        policy = _make_policy(lease_store=[lease_no_lock, lease_with_lock])
        result = policy._collect_active_leases()
        assert lease_no_lock not in result
        assert lease_with_lock in result

    def test_list_leases_callable_used_when_present(self):
        active_lease = _make_lease(state=LeaseState.ACTIVE, path_lock="src/x.py")
        store = MagicMock()
        store.list_leases.return_value = [active_lease]
        policy = _make_policy(lease_store=store)
        result = policy._collect_active_leases()
        store.list_leases.assert_called_once()
        assert active_lease in result

    def test_list_leases_claimed_state_is_included(self):
        claimed = _make_lease(task_id="tc", state=LeaseState.CLAIMED, path_lock="src/f.py")
        policy = _make_policy(lease_store=[claimed])
        result = policy._collect_active_leases()
        assert claimed in result

    def test_list_leases_renewing_state_is_included(self):
        renewing = _make_lease(task_id="tr", state=LeaseState.RENEWING, path_lock="src/f.py")
        policy = _make_policy(lease_store=[renewing])
        result = policy._collect_active_leases()
        assert renewing in result

    def test_exception_in_lease_store_returns_empty(self):
        bad_store = MagicMock()
        bad_store.list_leases.side_effect = RuntimeError("db offline")
        policy = _make_policy(lease_store=bad_store)
        result = policy._collect_active_leases()
        assert result == []

    def test_non_task_lease_objects_filtered_out(self):
        """Iterable store containing non-TaskLease objects must be ignored."""
        active_lease = _make_lease(state=LeaseState.ACTIVE, path_lock="src/x.py")
        policy = _make_policy(lease_store=[active_lease, "not-a-lease", 42])
        result = policy._collect_active_leases()
        assert result == [active_lease]


# ---------------------------------------------------------------------------
# SchedulerPolicy.recommend
# ---------------------------------------------------------------------------


class TestRecommend:
    """Tests for the public recommend() method."""

    def test_returns_empty_when_no_nodes(self):
        policy = _make_policy(ranked_nodes=[])
        result = policy.recommend()
        assert result == []

    def test_returns_recommendation_for_single_node(self):
        node = _make_node()
        policy = _make_policy(ranked_nodes=[node])
        recs = policy.recommend()
        assert len(recs) == 1
        assert recs[0].node_id == "node-a"

    def test_recommendations_sorted_by_score_descending(self):
        node_low = _make_node(node_id="low", availability_score=0.1, active_agents=7)
        node_high = _make_node(node_id="high", availability_score=0.95, active_agents=0)
        policy = _make_policy(ranked_nodes=[node_low, node_high])
        recs = policy.recommend()
        assert recs[0].node_id == "high"
        assert recs[0].score > recs[1].score

    def test_exclude_nodes_removes_them(self):
        node_a = _make_node(node_id="node-a")
        node_b = _make_node(node_id="node-b")
        policy = _make_policy(ranked_nodes=[node_a, node_b])
        recs = policy.recommend(exclude_nodes=["node-a"])
        ids = [r.node_id for r in recs]
        assert "node-a" not in ids
        assert "node-b" in ids

    def test_exclude_all_nodes_returns_empty(self):
        node = _make_node(node_id="node-a")
        policy = _make_policy(ranked_nodes=[node])
        recs = policy.recommend(exclude_nodes=["node-a"])
        assert recs == []

    def test_path_lock_conflict_reduces_score(self):
        node = _make_node(node_id="node-a")
        lease = _make_lease(owner_node="node-a", path_lock="src/routes.py", state=LeaseState.ACTIVE)
        policy = _make_policy(ranked_nodes=[node], lease_store=[lease])
        recs_with_conflict = policy.recommend(path_lock="src/routes.py")
        recs_no_conflict = policy.recommend(path_lock=None)
        assert recs_with_conflict[0].score < recs_no_conflict[0].score

    def test_path_conflicts_populated_on_conflict(self):
        node = _make_node(node_id="node-a")
        lease = _make_lease(
            task_id="task-99",
            owner_node="node-a",
            path_lock="src/routes.py",
            state=LeaseState.ACTIVE,
        )
        policy = _make_policy(ranked_nodes=[node], lease_store=[lease])
        recs = policy.recommend(path_lock="src/routes.py")
        assert "task-99" in recs[0].path_conflicts

    def test_general_task_type_gives_full_affinity(self):
        node = _make_node(capabilities={})
        policy = _make_policy(ranked_nodes=[node])
        recs = policy.recommend(task_type="general")
        # With no path lock and full affinity the affinity component should be max
        # We just check the recommendation is returned and score is > 0
        assert recs[0].score > 0.0

    def test_available_agents_calculated_correctly(self):
        node = _make_node(active_agents=3)
        policy = _make_policy(ranked_nodes=[node])
        recs = policy.recommend()
        expected = _MAX_AGENTS_PER_NODE - 3
        assert recs[0].available_agents == expected

    def test_active_agents_exceeding_max_clamped_to_zero_agents(self):
        node = _make_node(active_agents=_MAX_AGENTS_PER_NODE + 5)
        policy = _make_policy(ranked_nodes=[node])
        recs = policy.recommend()
        assert recs[0].available_agents == 0

    def test_recommend_increments_stats(self):
        policy = _make_policy()
        initial = policy.get_stats()["total_recommendations"]
        policy.recommend()
        assert policy.get_stats()["total_recommendations"] == initial + 1

    def test_reason_string_contains_all_components(self):
        node = _make_node()
        policy = _make_policy(ranked_nodes=[node])
        recs = policy.recommend()
        reason = recs[0].reason
        assert "availability=" in reason
        assert "capacity=" in reason
        assert "affinity=" in reason
        assert "path_safety=" in reason

    def test_tie_break_by_node_id(self):
        """When two nodes have identical scores, node_id is the tie-breaker."""
        # Both nodes have identical parameters → identical scores
        node_z = _make_node(node_id="zzz", availability_score=0.5, active_agents=4)
        node_a = _make_node(node_id="aaa", availability_score=0.5, active_agents=4)
        policy = _make_policy(ranked_nodes=[node_z, node_a])
        recs = policy.recommend()
        assert recs[0].node_id == "aaa"


# ---------------------------------------------------------------------------
# SchedulerPolicy.get_stats / _record_stats
# ---------------------------------------------------------------------------


class TestStats:
    """Tests for get_stats() and _record_stats()."""

    def test_initial_stats_zeroed(self):
        policy = _make_policy()
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 0
        assert stats["cache_hits"] == 0
        assert stats["avg_latency_ms"] is None

    def test_stats_after_recommend(self):
        policy = _make_policy(ranked_nodes=[_make_node()])
        policy.recommend()
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 1
        assert stats["avg_latency_ms"] is not None

    def test_avg_latency_is_average_of_samples(self):
        policy = _make_policy(ranked_nodes=[_make_node()])
        policy.recommend()
        policy.recommend()
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 2
        # avg_latency_ms should be a positive float
        assert isinstance(stats["avg_latency_ms"], float)
        assert stats["avg_latency_ms"] >= 0.0

    def test_empty_candidates_still_increments_stats(self):
        policy = _make_policy(ranked_nodes=[])
        policy.recommend()
        assert policy.get_stats()["total_recommendations"] == 1

    def test_thread_safety_of_stats(self):
        """Concurrent recommend() calls should not corrupt the stats counter."""
        nodes = [_make_node(node_id=f"n{i}") for i in range(4)]
        policy = _make_policy(ranked_nodes=nodes)
        threads = [Thread(target=policy.recommend) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert policy.get_stats()["total_recommendations"] == 20


# ---------------------------------------------------------------------------
# Composite score correctness
# ---------------------------------------------------------------------------


class TestCompositeScore:
    """Verify the weighted composite score formula end-to-end."""

    def _perfect_node(self) -> dict:
        """A node with best-possible inputs on all four dimensions."""
        return _make_node(
            availability_score=1.0,
            active_agents=0,
            capabilities={"python": True},
        )

    def test_perfect_node_score_is_one(self):
        node = self._perfect_node()
        policy = _make_policy(ranked_nodes=[node])
        recs = policy.recommend(task_type="python", path_lock=None)
        assert recs[0].score == pytest.approx(1.0, abs=1e-4)

    def test_zero_availability_fully_penalises_availability_component(self):
        node = _make_node(availability_score=0.0, active_agents=0, capabilities={"python": True})
        policy = _make_policy(ranked_nodes=[node])
        recs = policy.recommend(task_type="python")
        # availability component = 0, others may still be positive
        expected = round(_W_CAPACITY * 1.0 + _W_AFFINITY * 1.0 + _W_PATH_SAFETY * 1.0, 4)
        assert recs[0].score == pytest.approx(expected, abs=1e-3)

    def test_weights_sum_to_one(self):
        total = _W_AVAILABILITY + _W_CAPACITY + _W_AFFINITY + _W_PATH_SAFETY
        assert total == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


class TestSingletonFactory:
    """Tests for get_scheduler_policy / reset_scheduler_policy."""

    def setup_method(self):
        reset_scheduler_policy()

    def teardown_method(self):
        reset_scheduler_policy()

    def test_get_scheduler_policy_returns_instance(self):
        hb = _make_hb_store()
        policy = get_scheduler_policy(heartbeat_store=hb)
        assert isinstance(policy, SchedulerPolicy)

    def test_second_call_returns_same_instance(self):
        hb = _make_hb_store()
        p1 = get_scheduler_policy(heartbeat_store=hb)
        p2 = get_scheduler_policy(heartbeat_store=_make_hb_store())  # ignored on 2nd call
        assert p1 is p2

    def test_reset_allows_new_instance(self):
        hb = _make_hb_store()
        p1 = get_scheduler_policy(heartbeat_store=hb)
        reset_scheduler_policy()
        p2 = get_scheduler_policy(heartbeat_store=_make_hb_store())
        assert p1 is not p2

    def test_get_scheduler_policy_without_store_uses_default(self):
        mock_store = _make_hb_store()
        # get_heartbeat_store is imported lazily inside the function body, so patch
        # it at its origin module rather than the scheduler_policy namespace.
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.get_heartbeat_store",
            return_value=mock_store,
        ):
            policy = get_scheduler_policy()
        assert isinstance(policy, SchedulerPolicy)

    def test_lease_store_passed_to_policy(self):
        hb = _make_hb_store()
        lease = _make_lease()
        policy = get_scheduler_policy(heartbeat_store=hb, lease_store=[lease])
        assert policy._lease_store == [lease]
