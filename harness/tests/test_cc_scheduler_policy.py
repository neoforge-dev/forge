"""Command Center tests for SchedulerPolicy (CP-2004).

Complements the unit-level test_scheduler_policy.py with:
- Integration scenarios across multiple task types and node configurations
- Behavioral property tests (score monotonicity, ordering stability)
- Affinity coverage for every known task type in the capability map
- Over-capacity node boundary conditions
- LeaseState.CLAIMED and LeaseState.RENEWING conflict detection
- Concurrent singleton creation under lock contention
- Deterministic tie-breaking by node_id
- Scheduler view passthrough verification
- Stats accumulation under repeated no-candidate calls
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.models.lease import LeaseState, TaskLease
from forge_harness.webhook_server.services.heartbeat_store import (
    HeartbeatRecord,
    HeartbeatStore,
    reset_heartbeat_store,
)
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


def _fresh_store(tmp_path: Path) -> HeartbeatStore:
    return HeartbeatStore(storage_path=tmp_path / "hb.jsonl")


def _seed(
    store: HeartbeatStore,
    node_id: str,
    *,
    cpu: float = 10.0,
    mem: float = 10.0,
    active_agents: int = 1,
    capabilities: dict[str, bool] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "hostname": f"{node_id}.local",
        "cpu_percent": cpu,
        "memory_percent": mem,
        "active_agents": active_agents,
        "active_leases": 0,
    }
    if capabilities is not None:
        payload["capabilities"] = capabilities
    store.record(node_id, payload)


def _active_lease(
    task_id: str,
    owner_node: str,
    path_lock: str | None = None,
    state: LeaseState = LeaseState.ACTIVE,
) -> TaskLease:
    lease = TaskLease(task_id=task_id)
    lease.state = LeaseState.UNCLAIMED
    lease.claim(owner_node, f"forge:{owner_node}")
    lease.state = LeaseState.ACTIVE
    if path_lock:
        lease.path_lock = path_lock
    if state is not LeaseState.ACTIVE:
        lease.state = state
    return lease


# ---------------------------------------------------------------------------
# Autouse fixture — always reset singletons
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    reset_scheduler_policy()
    reset_heartbeat_store()
    yield
    reset_scheduler_policy()
    reset_heartbeat_store()


# ===========================================================================
# SchedulerRecommendation — additional model validation
# ===========================================================================


class TestRecommendationModelExtended:
    """Additional Pydantic model edge cases not covered by the unit suite."""

    def test_available_agents_cannot_be_negative(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="n", score=0.5, available_agents=-1)

    def test_reason_field_defaults_to_empty_string(self):
        rec = SchedulerRecommendation(node_id="n", score=0.5)
        assert rec.reason == ""

    def test_path_conflicts_default_to_empty_list(self):
        rec = SchedulerRecommendation(node_id="n", score=0.3)
        assert isinstance(rec.path_conflicts, list)
        assert len(rec.path_conflicts) == 0

    def test_full_construction_round_trip(self):
        rec = SchedulerRecommendation(
            node_id="sati",
            score=0.85,
            reason="test reason",
            available_agents=5,
            path_conflicts=["T-001"],
        )
        data = rec.model_dump()
        restored = SchedulerRecommendation.model_validate(data)
        assert restored == rec

    def test_score_zero_is_valid(self):
        rec = SchedulerRecommendation(node_id="n", score=0.0)
        assert rec.score == 0.0

    def test_score_one_is_valid(self):
        rec = SchedulerRecommendation(node_id="n", score=1.0)
        assert rec.score == 1.0

    def test_multiple_path_conflicts_stored_correctly(self):
        conflicts = [f"T-{i:03d}" for i in range(5)]
        rec = SchedulerRecommendation(node_id="n", score=0.1, path_conflicts=conflicts)
        assert rec.path_conflicts == conflicts


# ===========================================================================
# Capacity scoring — boundary conditions
# ===========================================================================


class TestCapacityScoringBoundaries:
    """Verify _capacity_score arithmetic at edge agent counts."""

    def test_zero_agents_gives_full_capacity(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "idle", active_agents=0)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(task_type="general")
        # capacity = min(8/8, 1.0) = 1.0; full score = 1.0
        assert "capacity=1.000" in recs[0].reason

    def test_max_agents_gives_zero_capacity(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "full", active_agents=_MAX_AGENTS_PER_NODE, cpu=0.0, mem=0.0)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(task_type="general")
        assert "capacity=0.000" in recs[0].reason

    def test_half_agents_gives_half_capacity(self, tmp_path):
        store = _fresh_store(tmp_path)
        half = _MAX_AGENTS_PER_NODE // 2
        _seed(store, "half", active_agents=half, cpu=0.0, mem=0.0)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(task_type="general")
        expected = half / _MAX_AGENTS_PER_NODE
        assert recs[0].available_agents == half

    def test_over_capacity_clamped_to_zero_available(self, tmp_path):
        """Nodes reporting more agents than MAX_AGENTS must not give negative available."""
        store = _fresh_store(tmp_path)
        # Inject a record with active_agents > MAX_AGENTS_PER_NODE directly
        record = HeartbeatRecord(
            node_id="overloaded",
            hostname="overloaded.local",
            cpu_percent=95.0,
            memory_percent=95.0,
            active_agents=_MAX_AGENTS_PER_NODE + 3,
            active_leases=0,
            last_seen=datetime.now(UTC).isoformat(),
            ttl_seconds=120,
            status="healthy",
        )
        with store._lock:
            store._ensure_loaded()
            store._records["overloaded"] = record
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend()
        assert recs[0].available_agents == 0


# ===========================================================================
# Affinity scoring — every known task type
# ===========================================================================


class TestAffinityAllTaskTypes:
    """Verify affinity score for every entry in _TASK_TYPE_CAPABILITY_MAP."""

    @pytest.mark.parametrize("task_type,capability_key", list(_TASK_TYPE_CAPABILITY_MAP.items()))
    def test_node_with_capability_scores_full_affinity(
        self, task_type: str, capability_key: str, tmp_path: Path
    ):
        store = _fresh_store(tmp_path)
        _seed(store, "capable", capabilities={capability_key: True})
        _seed(store, "incapable", capabilities={capability_key: False})
        view = store.get_scheduler_view()
        for node in view["ranked_nodes"]:
            if node["node_id"] == "capable":
                node["capabilities"] = {capability_key: True}
            else:
                node["capabilities"] = {capability_key: False}

        with patch.object(store, "get_scheduler_view", return_value=view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend(task_type=task_type)

        capable_score = next(r for r in recs if r.node_id == "capable").score
        incapable_score = next(r for r in recs if r.node_id == "incapable").score
        # capable must score higher due to affinity=1.0 vs 0.0
        assert capable_score > incapable_score

    def test_unmapped_task_type_falls_back_to_neutral(self, tmp_path):
        """Task types not in the capability map default to 0.5 when no capabilities dict."""
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(task_type="totally_unknown_type_xyz")
        # No capabilities dict in heartbeat → neutral 0.5 affinity
        assert "affinity=0.500" in recs[0].reason

    def test_general_task_type_always_max_affinity(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova", capabilities={"some_cap": False})
        view = store.get_scheduler_view()
        for node in view["ranked_nodes"]:
            node["capabilities"] = {"some_cap": False}
        with patch.object(store, "get_scheduler_view", return_value=view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend(task_type="general")
        # general always scores 1.0 affinity regardless of capabilities
        assert "affinity=1.000" in recs[0].reason


# ===========================================================================
# Path safety — CLAIMED and RENEWING states
# ===========================================================================


class TestLeaseStateConflicts:
    """Leases in CLAIMED and RENEWING states should also be path conflicts."""

    def test_claimed_lease_with_path_lock_is_conflict(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        claimed_lease = TaskLease(task_id="T-CLAIMED")
        claimed_lease.state = LeaseState.UNCLAIMED
        claimed_lease.claim("nova", "forge:nova")
        # State is now CLAIMED — do not advance to ACTIVE
        claimed_lease.path_lock = "src/conflict.py"

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[claimed_lease])
        recs = policy.recommend(path_lock="src/conflict.py")
        assert "T-CLAIMED" in recs[0].path_conflicts

    def test_renewing_lease_with_path_lock_is_conflict(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        renewing_lease = _active_lease("T-RENEW", "nova", path_lock="src/conflict.py")
        renewing_lease.state = LeaseState.RENEWING

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[renewing_lease])
        recs = policy.recommend(path_lock="src/conflict.py")
        assert "T-RENEW" in recs[0].path_conflicts

    def test_expired_lease_not_a_conflict(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        expired_lease = TaskLease(task_id="T-EXP")
        expired_lease.state = LeaseState.UNCLAIMED
        expired_lease.claim("nova", "forge:nova")
        expired_lease.state = LeaseState.ACTIVE
        expired_lease.path_lock = "src/conflict.py"
        expired_lease.state = LeaseState.EXPIRED

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[expired_lease])
        recs = policy.recommend(path_lock="src/conflict.py")
        assert recs[0].path_conflicts == []

    def test_requeued_lease_not_a_conflict(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        requeued_lease = TaskLease(task_id="T-REQ")
        requeued_lease.state = LeaseState.UNCLAIMED
        requeued_lease.claim("nova", "forge:nova")
        requeued_lease.state = LeaseState.ACTIVE
        requeued_lease.path_lock = "src/conflict.py"
        requeued_lease.state = LeaseState.RELEASING
        requeued_lease.state = LeaseState.REQUEUED

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[requeued_lease])
        recs = policy.recommend(path_lock="src/conflict.py")
        assert recs[0].path_conflicts == []

    def test_lease_without_path_lock_never_conflicts(self, tmp_path):
        """Active leases that hold no path_lock must not block any path request."""
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        no_lock_lease = _active_lease("T-NOLOCK", "nova", path_lock=None)

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[no_lock_lease])
        recs = policy.recommend(path_lock="src/any/path.py")
        assert recs[0].path_conflicts == []

    def test_generator_lease_store_iterable(self, tmp_path):
        """A generator (one-time iterable) is consumed correctly."""
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        lease = _active_lease("T-GEN", "nova", path_lock="src/gen.py")

        def _lease_generator():
            yield lease

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=_lease_generator())
        recs = policy.recommend(path_lock="src/gen.py")
        assert "T-GEN" in recs[0].path_conflicts


# ===========================================================================
# Multi-node behavioral properties
# ===========================================================================


class TestMultiNodeBehavioralProperties:
    """Property-level tests across node configurations."""

    def test_adding_idle_node_increases_candidate_count(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "node-a")
        policy = SchedulerPolicy(heartbeat_store=store)

        count_before = len(policy.recommend())
        _seed(store, "node-b")
        count_after = len(policy.recommend())

        assert count_after == count_before + 1

    def test_score_ordering_is_stable_across_calls(self, tmp_path):
        store = _fresh_store(tmp_path)
        for i in range(5):
            _seed(store, f"node-{i}", cpu=float(i * 10), active_agents=i)
        policy = SchedulerPolicy(heartbeat_store=store)

        recs_1 = [r.node_id for r in policy.recommend()]
        recs_2 = [r.node_id for r in policy.recommend()]
        assert recs_1 == recs_2

    def test_tie_break_by_node_id_is_deterministic(self, tmp_path):
        """Nodes with identical scores must be ranked alphabetically by node_id."""
        store = _fresh_store(tmp_path)
        # Identical load on all nodes → ties broken by node_id
        for name in ("zeta", "alpha", "mu"):
            _seed(store, name, cpu=0.0, mem=0.0, active_agents=0)

        policy = SchedulerPolicy(heartbeat_store=store)
        # Force identical scheduler view scores by patching
        view = store.get_scheduler_view()
        for node in view["ranked_nodes"]:
            node["availability_score"] = 1.0
            node["active_agents"] = 0

        with patch.object(store, "get_scheduler_view", return_value=view):
            recs = policy.recommend(task_type="general")

        node_ids = [r.node_id for r in recs]
        # All scores equal → sorted ascending by node_id
        tied_ids = [r.node_id for r in recs if r.score == recs[0].score]
        assert tied_ids == sorted(tied_ids)

    def test_path_conflict_penalty_moves_node_to_bottom(self, tmp_path):
        """A node with a path conflict must rank below clean nodes of similar load."""
        store = _fresh_store(tmp_path)
        _seed(store, "clean-a", cpu=5.0, mem=5.0, active_agents=1)
        _seed(store, "conflicted", cpu=5.0, mem=5.0, active_agents=1)
        _seed(store, "clean-b", cpu=5.0, mem=5.0, active_agents=1)

        lease = _active_lease("T-X", "conflicted", path_lock="src/shared.py")
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/shared.py")

        conflicted_rank = next(i for i, r in enumerate(recs) if r.node_id == "conflicted")
        clean_ranks = [i for i, r in enumerate(recs) if r.node_id != "conflicted"]
        assert all(cr < conflicted_rank for cr in clean_ranks)

    def test_recommend_all_task_types_returns_results(self, tmp_path):
        """Calling recommend() with every known task type must not raise."""
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store)
        known_types = list(_TASK_TYPE_CAPABILITY_MAP.keys()) + ["general", ""]
        for task_type in known_types:
            result = policy.recommend(task_type=task_type)
            assert len(result) == 1, f"Expected 1 rec for task_type={task_type!r}"


# ===========================================================================
# Stats — additional accumulation scenarios
# ===========================================================================


class TestStatsAccumulation:
    """Cover stats accumulation paths not exercised by the unit suite."""

    def test_empty_store_calls_still_accumulate_stats(self, tmp_path):
        store = _fresh_store(tmp_path)
        policy = SchedulerPolicy(heartbeat_store=store)
        for _ in range(5):
            policy.recommend()
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 5
        assert stats["avg_latency_ms"] is not None

    def test_latency_samples_are_non_negative(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store)
        for _ in range(10):
            policy.recommend()
        assert policy.get_stats()["avg_latency_ms"] >= 0.0

    def test_cache_hits_always_zero_in_current_impl(self, tmp_path):
        """cache_hits is a placeholder — must exist and be 0."""
        store = _fresh_store(tmp_path)
        policy = SchedulerPolicy(heartbeat_store=store)
        _seed(store, "nova")
        policy.recommend()
        assert policy.get_stats()["cache_hits"] == 0

    def test_multiple_policy_instances_have_independent_stats(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        p1 = SchedulerPolicy(heartbeat_store=store)
        p2 = SchedulerPolicy(heartbeat_store=store)
        p1.recommend()
        p1.recommend()
        p2.recommend()
        assert p1.get_stats()["total_recommendations"] == 2
        assert p2.get_stats()["total_recommendations"] == 1


# ===========================================================================
# Singleton — concurrent creation
# ===========================================================================


class TestSingletonConcurrency:
    """Verify that concurrent get_scheduler_policy() calls return one instance."""

    def test_concurrent_get_returns_same_instance(self, tmp_path):
        store = _fresh_store(tmp_path)
        results: list[SchedulerPolicy] = []
        lock = threading.Lock()

        def _get():
            p = get_scheduler_policy(heartbeat_store=store)
            with lock:
                results.append(p)

        threads = [threading.Thread(target=_get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20
        assert all(p is results[0] for p in results)

    def test_reset_then_concurrent_get_creates_one_new_instance(self, tmp_path):
        store = _fresh_store(tmp_path)
        # Create initial singleton
        initial = get_scheduler_policy(heartbeat_store=store)
        reset_scheduler_policy()

        new_instances: list[SchedulerPolicy] = []
        lock = threading.Lock()

        def _get():
            p = get_scheduler_policy(heartbeat_store=store)
            with lock:
                new_instances.append(p)

        threads = [threading.Thread(target=_get) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get the same new instance, distinct from the initial
        assert all(p is not initial for p in new_instances)
        assert len({id(p) for p in new_instances}) == 1


# ===========================================================================
# Score composition — arithmetic verification
# ===========================================================================


class TestScoreCompositionArithmetic:
    """Verify the weighted arithmetic composites by constructing known inputs."""

    def test_half_availability_half_capacity_general_clean_path(self, tmp_path):
        """availability=0.5, capacity=0.5, affinity=1.0, path_safety=1.0.

        Expected: 0.5*0.4 + 0.5*0.3 + 1.0*0.2 + 1.0*0.1 = 0.20+0.15+0.20+0.10 = 0.65
        """
        store = _fresh_store(tmp_path)
        # cpu=50%, mem=50% → availability ~0.5 (exact depends on HeartbeatRecord formula)
        # active_agents=4 → capacity=4/8=0.5
        _seed(store, "mid", cpu=50.0, mem=50.0, active_agents=4)
        view = store.get_scheduler_view()
        for node in view["ranked_nodes"]:
            if node["node_id"] == "mid":
                node["availability_score"] = 0.5

        with patch.object(store, "get_scheduler_view", return_value=view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend(task_type="general")

        expected = round(0.4 * 0.5 + 0.3 * 0.5 + 0.2 * 1.0 + 0.1 * 1.0, 4)
        assert recs[0].score == pytest.approx(expected, abs=1e-4)

    def test_path_conflict_reduces_score_by_path_safety_weight(self, tmp_path):
        """Verify the path_safety component is exactly _W_PATH_SAFETY when conflict exists."""
        store = _fresh_store(tmp_path)
        _seed(store, "nova", cpu=0.0, mem=0.0, active_agents=0)

        lease = _active_lease("T-X", "nova", path_lock="src/foo.py")

        view = store.get_scheduler_view()
        for node in view["ranked_nodes"]:
            node["availability_score"] = 1.0

        with patch.object(store, "get_scheduler_view", return_value=view):
            policy_clean = SchedulerPolicy(heartbeat_store=store)
            recs_clean = policy_clean.recommend(task_type="general", path_lock="src/foo.py")
            score_clean = recs_clean[0].score

            reset_scheduler_policy()
            policy_conflict = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
            recs_conflict = policy_conflict.recommend(task_type="general", path_lock="src/foo.py")
            score_conflict = recs_conflict[0].score

        penalty = score_clean - score_conflict
        assert penalty == pytest.approx(_W_PATH_SAFETY, abs=1e-4)

    def test_score_is_capped_at_one(self, tmp_path):
        """No combination of inputs should produce a score > 1.0."""
        store = _fresh_store(tmp_path)
        _seed(store, "perfect", cpu=0.0, mem=0.0, active_agents=0)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(task_type="general")
        assert recs[0].score <= 1.0

    def test_score_is_floored_at_zero(self, tmp_path):
        """No combination of inputs should produce a score < 0.0."""
        store = _fresh_store(tmp_path)
        _seed(store, "worst", cpu=100.0, mem=100.0, active_agents=_MAX_AGENTS_PER_NODE)
        lease = _active_lease("T-WORST", "worst", path_lock="src/x.py")
        view = store.get_scheduler_view()
        for node in view["ranked_nodes"]:
            node["availability_score"] = 0.0
        with patch.object(store, "get_scheduler_view", return_value=view):
            policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
            recs = policy.recommend(task_type="ios", path_lock="src/x.py")
        assert recs[0].score >= 0.0


# ===========================================================================
# Scheduler view passthrough
# ===========================================================================


class TestSchedulerViewPassthrough:
    """Verify that the policy uses exactly the data from get_scheduler_view()."""

    def test_policy_uses_ranked_nodes_from_view(self, tmp_path):
        store = _fresh_store(tmp_path)
        mock_view = {
            "ranked_nodes": [
                {
                    "node_id": "injected-node",
                    "availability_score": 0.9,
                    "active_agents": 2,
                }
            ],
            "recommended_node_id": "injected-node",
        }
        with patch.object(store, "get_scheduler_view", return_value=mock_view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend()

        assert len(recs) == 1
        assert recs[0].node_id == "injected-node"

    def test_empty_ranked_nodes_in_view_returns_empty(self, tmp_path):
        store = _fresh_store(tmp_path)
        mock_view = {"ranked_nodes": [], "recommended_node_id": None}
        with patch.object(store, "get_scheduler_view", return_value=mock_view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend()
        assert recs == []

    def test_missing_ranked_nodes_key_returns_empty(self, tmp_path):
        """Scheduler view missing 'ranked_nodes' must not raise."""
        store = _fresh_store(tmp_path)
        mock_view: dict[str, Any] = {}
        with patch.object(store, "get_scheduler_view", return_value=mock_view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend()
        assert recs == []

    def test_node_with_missing_active_agents_defaults_to_max_slots(self, tmp_path):
        """Nodes without 'active_agents' in the view dict get 0 active agents assumed."""
        store = _fresh_store(tmp_path)
        mock_view = {
            "ranked_nodes": [
                {
                    "node_id": "sparse-node",
                    "availability_score": 1.0,
                    # no 'active_agents' key
                }
            ]
        }
        with patch.object(store, "get_scheduler_view", return_value=mock_view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend()
        # active_agents defaults to 0 → all slots available
        assert recs[0].available_agents == _MAX_AGENTS_PER_NODE


# ===========================================================================
# Reason string — content verification
# ===========================================================================


class TestReasonStringContent:
    """Verify the human-readable reason string format and conflict annotation."""

    def test_reason_includes_all_four_components(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend()
        reason = recs[0].reason
        assert "availability=" in reason
        assert "capacity=" in reason
        assert "affinity=" in reason
        assert "path_safety=" in reason

    def test_reason_includes_weight_percentages(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend()
        reason = recs[0].reason
        assert "40%" in reason  # availability weight
        assert "30%" in reason  # capacity weight
        assert "20%" in reason  # affinity weight
        assert "10%" in reason  # path_safety weight

    def test_reason_includes_path_conflict_annotation(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        lease = _active_lease("T-ANNOT", "nova", path_lock="src/annotated.py")
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/annotated.py")
        assert "PATH CONFLICT" in recs[0].reason
        assert "T-ANNOT" in recs[0].reason

    def test_reason_no_conflict_annotation_when_clean(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(path_lock="src/clean.py")
        assert "PATH CONFLICT" not in recs[0].reason


# ===========================================================================
# Integration — realistic fleet dispatch scenario
# ===========================================================================


class TestFleetDispatchScenario:
    """End-to-end scenario modelling a realistic fleet dispatch decision."""

    def test_selects_least_loaded_capable_node(self, tmp_path):
        """sati (capable, low load) must beat prya (capable, high load) for python tasks."""
        store = _fresh_store(tmp_path)
        # prya: capable but highly loaded
        view_data: dict[str, Any] = {}

        _seed(store, "prya", cpu=80.0, mem=75.0, active_agents=6)
        _seed(store, "sati", cpu=15.0, mem=20.0, active_agents=1)
        _seed(store, "nova", cpu=40.0, mem=40.0, active_agents=3)

        view = store.get_scheduler_view()
        for node in view["ranked_nodes"]:
            node["capabilities"] = {"python": True}

        with patch.object(store, "get_scheduler_view", return_value=view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend(task_type="python")

        assert recs[0].node_id == "sati"

    def test_path_conflict_on_best_node_promotes_second_best(self, tmp_path):
        """If top node has a path conflict it must drop below the next best node.

        The penalty for a path conflict is _W_PATH_SAFETY (0.10).  To ensure
        the conflicted node drops below the clean node we seed both nodes with
        near-identical load so the path-safety delta flips the ordering.
        """
        store = _fresh_store(tmp_path)
        # Both nodes nearly identical load — path conflict will decide the rank
        _seed(store, "almost-best", cpu=0.0, mem=0.0, active_agents=0)
        _seed(store, "clean-twin", cpu=0.0, mem=0.0, active_agents=0)

        view = store.get_scheduler_view()
        # Force identical availability_score so only path safety differentiates
        for node in view["ranked_nodes"]:
            node["availability_score"] = 1.0
            node["active_agents"] = 0

        lease = _active_lease("T-LOCK", "almost-best", path_lock="src/shared/auth.py")

        with patch.object(store, "get_scheduler_view", return_value=view):
            policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
            recs = policy.recommend(task_type="general", path_lock="src/shared/auth.py")

        conflicted_rec = next(r for r in recs if r.node_id == "almost-best")
        clean_rec = next(r for r in recs if r.node_id == "clean-twin")
        assert clean_rec.score > conflicted_rec.score
        assert recs[0].node_id == "clean-twin"

    def test_excluded_top_node_promotes_next_in_line(self, tmp_path):
        store = _fresh_store(tmp_path)
        _seed(store, "top", cpu=0.0, mem=0.0, active_agents=0)
        _seed(store, "runner-up", cpu=30.0, mem=30.0, active_agents=3)

        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(exclude_nodes=["top"])

        assert recs[0].node_id == "runner-up"

    def test_all_nodes_excluded_fleet_gracefully_empty(self, tmp_path):
        store = _fresh_store(tmp_path)
        for name in ("prya", "sati", "nova", "vega"):
            _seed(store, name)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(exclude_nodes=["prya", "sati", "nova", "vega"])
        assert recs == []
