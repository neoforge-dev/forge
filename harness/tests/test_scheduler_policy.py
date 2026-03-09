"""Tests for SchedulerPolicy (CP-2004).

Coverage targets
----------------
- SchedulerRecommendation model validation
- Empty heartbeat store returns empty recommendations
- Single node recommendation (basic happy path)
- Multi-node ranking by composite score
- Path conflict detection lowers path_safety score
- Node exclusion (exclude_nodes parameter)
- Task type affinity matching (known / unknown / general)
- Stale nodes are excluded (they are filtered out by HeartbeatStore)
- Stats tracking (total_recommendations, avg_latency_ms)
- Singleton pattern (get_scheduler_policy / reset_scheduler_policy)
- Lease store integration (callable list_leases and iterable forms)
- Affinity score edge cases (no capabilities data, zero-capacity node)
- Weight composition arithmetic
- Thread safety of stats accumulation
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.models.lease import LeaseState, TaskLease
from forge_harness.webhook_server.services.heartbeat_store import (
    HeartbeatStore,
    reset_heartbeat_store,
)
from forge_harness.webhook_server.services.scheduler_policy import (
    _MAX_AGENTS_PER_NODE,
    _W_AFFINITY,
    _W_AVAILABILITY,
    _W_CAPACITY,
    _W_PATH_SAFETY,
    SchedulerPolicy,
    SchedulerRecommendation,
    get_scheduler_policy,
    reset_scheduler_policy,
)

# ===========================================================================
# Constants / shared helpers
# ===========================================================================

_NOW_ISO = datetime.now(UTC).isoformat()


def _make_store(tmp_path: Path) -> HeartbeatStore:
    """Return a fresh HeartbeatStore backed by a temp directory."""
    return HeartbeatStore(storage_path=tmp_path / "nodes.jsonl")


def _seed_node(
    store: HeartbeatStore,
    node_id: str,
    *,
    cpu: float = 20.0,
    mem: float = 30.0,
    active_agents: int = 2,
    active_leases: int = 0,
    hostname: str | None = None,
) -> None:
    """Record a fresh heartbeat for *node_id* in *store*."""
    store.record(
        node_id,
        {
            "hostname": hostname or f"{node_id}.local",
            "cpu_percent": cpu,
            "memory_percent": mem,
            "active_agents": active_agents,
            "active_leases": active_leases,
        },
    )


def _make_active_lease(
    task_id: str,
    owner_node: str,
    path_lock: str | None = None,
    state: LeaseState = LeaseState.ACTIVE,
) -> TaskLease:
    """Build a TaskLease in an active state with optional path_lock."""
    lease = TaskLease(task_id=task_id)
    lease.state = LeaseState.UNCLAIMED
    lease.claim(owner_node, f"forge:{owner_node}")
    # Move from CLAIMED → ACTIVE (valid transition)
    lease.state = LeaseState.ACTIVE
    if path_lock:
        lease.path_lock = path_lock
    # Allow overriding state for edge-case tests
    if state is not LeaseState.ACTIVE:
        lease.state = state
    return lease


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset both singletons before and after every test."""
    reset_scheduler_policy()
    reset_heartbeat_store()
    yield
    reset_scheduler_policy()
    reset_heartbeat_store()


@pytest.fixture()
def store(tmp_path: Path) -> HeartbeatStore:
    return _make_store(tmp_path)


@pytest.fixture()
def policy(store: HeartbeatStore) -> SchedulerPolicy:
    return SchedulerPolicy(heartbeat_store=store)


# ===========================================================================
# SchedulerRecommendation model tests
# ===========================================================================


class TestSchedulerRecommendationModel:
    def test_defaults(self):
        rec = SchedulerRecommendation(node_id="nova", score=0.75)
        assert rec.node_id == "nova"
        assert rec.score == 0.75
        assert rec.reason == ""
        assert rec.available_agents == 0
        assert rec.path_conflicts == []

    def test_score_clamp_at_boundaries(self):
        lo = SchedulerRecommendation(node_id="a", score=0.0)
        hi = SchedulerRecommendation(node_id="b", score=1.0)
        assert lo.score == 0.0
        assert hi.score == 1.0

    def test_score_out_of_range_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x", score=1.1)

        with pytest.raises(ValidationError):
            SchedulerRecommendation(node_id="x", score=-0.1)

    def test_path_conflicts_populated(self):
        rec = SchedulerRecommendation(
            node_id="nova",
            score=0.5,
            path_conflicts=["CP-001", "CP-002"],
        )
        assert len(rec.path_conflicts) == 2


# ===========================================================================
# Empty heartbeat store
# ===========================================================================


class TestEmptyStore:
    def test_empty_store_returns_no_recommendations(self, store: HeartbeatStore):
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend()
        assert recs == []

    def test_empty_store_stats_reflect_one_call(self, store: HeartbeatStore):
        policy = SchedulerPolicy(heartbeat_store=store)
        policy.recommend()
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 1
        assert stats["avg_latency_ms"] is not None


# ===========================================================================
# Single node recommendation
# ===========================================================================


class TestSingleNode:
    def test_single_node_returned(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "nova", cpu=20.0, mem=30.0, active_agents=2)
        recs = policy.recommend()
        assert len(recs) == 1
        assert recs[0].node_id == "nova"

    def test_single_node_score_in_range(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "nova", cpu=20.0, mem=30.0, active_agents=2)
        recs = policy.recommend()
        assert 0.0 <= recs[0].score <= 1.0

    def test_single_node_has_reason(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "nova", cpu=20.0, mem=30.0, active_agents=2)
        recs = policy.recommend()
        assert "availability=" in recs[0].reason
        assert "capacity=" in recs[0].reason
        assert "affinity=" in recs[0].reason
        assert "path_safety=" in recs[0].reason

    def test_single_node_available_agents(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "nova", active_agents=3)
        recs = policy.recommend()
        assert recs[0].available_agents == _MAX_AGENTS_PER_NODE - 3

    def test_single_node_no_path_conflicts(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "nova")
        recs = policy.recommend(path_lock="src/api/routes.py")
        assert recs[0].path_conflicts == []


# ===========================================================================
# Multi-node ranking
# ===========================================================================


class TestMultiNodeRanking:
    def test_idle_node_ranks_above_busy_node(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "idle", cpu=5.0, mem=10.0, active_agents=1)
        _seed_node(store, "busy", cpu=80.0, mem=85.0, active_agents=7)
        recs = policy.recommend()
        assert len(recs) == 2
        assert recs[0].node_id == "idle"
        assert recs[1].node_id == "busy"

    def test_scores_sorted_descending(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "a", cpu=10.0, mem=10.0, active_agents=0)
        _seed_node(store, "b", cpu=50.0, mem=50.0, active_agents=4)
        _seed_node(store, "c", cpu=90.0, mem=90.0, active_agents=7)
        recs = policy.recommend()
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_three_nodes_all_returned(self, store: HeartbeatStore, policy: SchedulerPolicy):
        for name in ("alpha", "beta", "gamma"):
            _seed_node(store, name)
        recs = policy.recommend()
        assert len(recs) == 3
        assert {r.node_id for r in recs} == {"alpha", "beta", "gamma"}

    def test_fully_loaded_node_scores_below_idle(
        self, store: HeartbeatStore, policy: SchedulerPolicy
    ):
        _seed_node(store, "full", cpu=100.0, mem=100.0, active_agents=8)
        _seed_node(store, "free", cpu=0.0, mem=0.0, active_agents=0)
        recs = policy.recommend()
        free_score = next(r.score for r in recs if r.node_id == "free")
        full_score = next(r.score for r in recs if r.node_id == "full")
        assert free_score > full_score


# ===========================================================================
# Path conflict detection
# ===========================================================================


class TestPathConflicts:
    def test_no_path_lock_no_conflicts(self, store: HeartbeatStore):
        lease = _make_active_lease("CP-001", "nova", path_lock="src/api/routes.py")
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        _seed_node(store, "nova")
        recs = policy.recommend()  # no path_lock requested
        assert recs[0].path_conflicts == []

    def test_conflicting_path_reduces_score(self, store: HeartbeatStore):
        _seed_node(store, "nova", cpu=10.0, mem=10.0)
        lease = _make_active_lease("CP-001", "nova", path_lock="src/api/routes.py")
        policy_without_conflict = SchedulerPolicy(heartbeat_store=store)
        policy_with_conflict = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])

        recs_clean = policy_without_conflict.recommend(path_lock="src/api/routes.py")
        recs_conflict = policy_with_conflict.recommend(path_lock="src/api/routes.py")

        assert recs_clean[0].score > recs_conflict[0].score

    def test_conflict_appears_in_path_conflicts_list(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        lease = _make_active_lease("CP-001", "nova", path_lock="src/api/routes.py")
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/api/routes.py")
        assert "CP-001" in recs[0].path_conflicts

    def test_different_path_no_conflict(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        lease = _make_active_lease("CP-001", "nova", path_lock="src/other.py")
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/api/routes.py")
        assert recs[0].path_conflicts == []

    def test_conflict_on_different_node_does_not_penalise_clean_node(self, store: HeartbeatStore):
        _seed_node(store, "nova", cpu=10.0, mem=10.0)
        _seed_node(store, "prya", cpu=10.0, mem=10.0)
        lease = _make_active_lease("CP-001", "prya", path_lock="src/api/routes.py")
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/api/routes.py")

        nova_rec = next(r for r in recs if r.node_id == "nova")
        prya_rec = next(r for r in recs if r.node_id == "prya")
        assert nova_rec.path_conflicts == []
        assert "CP-001" in prya_rec.path_conflicts
        # nova should outscore prya due to clean path
        assert nova_rec.score > prya_rec.score

    def test_multiple_conflicts_on_same_node(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        leases = [
            _make_active_lease("CP-001", "nova", path_lock="src/api/routes.py"),
            _make_active_lease("CP-002", "nova", path_lock="src/api/routes.py"),
        ]
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=leases)
        recs = policy.recommend(path_lock="src/api/routes.py")
        assert set(recs[0].path_conflicts) == {"CP-001", "CP-002"}

    def test_unclaimed_lease_path_not_considered_conflict(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        # Unclaimed leases don't hold a path lock in active sense
        lease = TaskLease(task_id="CP-003")
        # State is UNCLAIMED by default — should not count as conflict
        lease.path_lock = "src/api/routes.py"
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/api/routes.py")
        assert recs[0].path_conflicts == []

    def test_callable_lease_store_list_leases(self, store: HeartbeatStore):
        """Lease store with a synchronous list_leases() method is supported."""
        _seed_node(store, "nova")
        lease = _make_active_lease("CP-005", "nova", path_lock="src/api/routes.py")

        class FakeLeaseStore:
            def list_leases(self) -> list[TaskLease]:
                return [lease]

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=FakeLeaseStore())
        recs = policy.recommend(path_lock="src/api/routes.py")
        assert "CP-005" in recs[0].path_conflicts


# ===========================================================================
# Node exclusion
# ===========================================================================


class TestNodeExclusion:
    def test_excluded_node_not_in_results(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "nova")
        _seed_node(store, "prya")
        recs = policy.recommend(exclude_nodes=["nova"])
        node_ids = [r.node_id for r in recs]
        assert "nova" not in node_ids
        assert "prya" in node_ids

    def test_exclude_all_nodes_returns_empty(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "nova")
        _seed_node(store, "prya")
        recs = policy.recommend(exclude_nodes=["nova", "prya"])
        assert recs == []

    def test_exclude_nonexistent_node_is_no_op(
        self, store: HeartbeatStore, policy: SchedulerPolicy
    ):
        _seed_node(store, "nova")
        recs = policy.recommend(exclude_nodes=["phantom"])
        assert len(recs) == 1
        assert recs[0].node_id == "nova"

    def test_exclude_none_equivalent_to_empty_list(
        self, store: HeartbeatStore, policy: SchedulerPolicy
    ):
        _seed_node(store, "nova")
        recs_none = policy.recommend(exclude_nodes=None)
        recs_empty = policy.recommend(exclude_nodes=[])
        assert [r.node_id for r in recs_none] == [r.node_id for r in recs_empty]


# ===========================================================================
# Task type affinity
# ===========================================================================


class TestTaskTypeAffinity:
    def test_general_task_type_max_affinity(self, store: HeartbeatStore):
        """'general' task type should award max affinity regardless of capabilities."""
        policy = SchedulerPolicy(heartbeat_store=store)
        _seed_node(store, "nova", cpu=50.0, mem=50.0, active_agents=4)
        recs_general = policy.recommend(task_type="general")
        recs_python = policy.recommend(task_type="python")
        # general should not be penalised compared to a node with no capability data
        assert recs_general[0].score >= recs_python[0].score

    def test_empty_task_type_treated_as_general(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(task_type="")
        assert len(recs) == 1
        # Affinity should be full (1.0) for empty string
        assert "affinity=1.000" in recs[0].reason

    def test_unknown_task_type_neutral_affinity(self, store: HeartbeatStore):
        """Unknown task types with no capability data receive 0.5 affinity."""
        # Seed a node with no capabilities dict in heartbeat
        _seed_node(store, "nova", cpu=0.0, mem=0.0, active_agents=0)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(task_type="quantum_compute")
        # affinity should be 0.5 (neutral) — node has no capabilities data
        assert "affinity=0.500" in recs[0].reason

    def test_node_with_matching_capability_scores_higher(self, store: HeartbeatStore):
        """Node that reports capability for the requested task type scores higher."""
        # Two identical nodes; inject capability into scheduler view for one
        _seed_node(store, "ios_node", cpu=30.0, mem=30.0, active_agents=2)
        _seed_node(store, "generic", cpu=30.0, mem=30.0, active_agents=2)

        # Patch get_scheduler_view to inject capability data for ios_node
        original_view = store.get_scheduler_view()
        for node in original_view["ranked_nodes"]:
            if node["node_id"] == "ios_node":
                node["capabilities"] = {"ios_simulator": True}
            else:
                node["capabilities"] = {"ios_simulator": False}

        with patch.object(store, "get_scheduler_view", return_value=original_view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend(task_type="ios")

        ios_score = next(r.score for r in recs if r.node_id == "ios_node")
        generic_score = next(r.score for r in recs if r.node_id == "generic")
        assert ios_score > generic_score


# ===========================================================================
# Stale nodes excluded
# ===========================================================================


class TestStaleNodesExcluded:
    def test_stale_node_not_in_recommendations(self, tmp_path: Path):
        """Nodes whose heartbeat is beyond TTL are excluded by HeartbeatStore.

        We seed a node with a ``last_seen`` already in the past (via patching)
        so that it is immediately stale without needing a real sleep.
        """
        store = _make_store(tmp_path)

        # Seed stale_node with a last_seen 200 seconds in the past (beyond default 120s TTL)
        from datetime import UTC, timedelta

        past_iso = (datetime.now(UTC) - timedelta(seconds=200)).isoformat()

        # Directly inject a record with a past timestamp into the store's cache
        from forge_harness.webhook_server.services.heartbeat_store import HeartbeatRecord

        stale_record = HeartbeatRecord(
            node_id="stale_node",
            hostname="stale.local",
            cpu_percent=5.0,
            memory_percent=5.0,
            active_agents=0,
            active_leases=0,
            last_seen=past_iso,
            ttl_seconds=120,
            status="healthy",
        )
        with store._lock:
            store._ensure_loaded()
            store._records["stale_node"] = stale_record

        _seed_node(store, "fresh_node")  # default 120s TTL, fresh timestamp

        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend()
        node_ids = {r.node_id for r in recs}
        assert "stale_node" not in node_ids
        assert "fresh_node" in node_ids


# ===========================================================================
# Stats tracking
# ===========================================================================


class TestStatsTracking:
    def test_total_recommendations_increments(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "nova")
        policy.recommend()
        policy.recommend()
        policy.recommend()
        assert policy.get_stats()["total_recommendations"] == 3

    def test_avg_latency_ms_is_float(self, store: HeartbeatStore, policy: SchedulerPolicy):
        _seed_node(store, "nova")
        policy.recommend()
        avg = policy.get_stats()["avg_latency_ms"]
        assert isinstance(avg, float)
        assert avg >= 0.0

    def test_avg_latency_none_before_any_call(self, store: HeartbeatStore):
        policy = SchedulerPolicy(heartbeat_store=store)
        # No calls yet — avg_latency_ms should be None
        stats = policy.get_stats()
        assert stats["avg_latency_ms"] is None

    def test_cache_hits_key_present(self, store: HeartbeatStore, policy: SchedulerPolicy):
        stats = policy.get_stats()
        assert "cache_hits" in stats

    def test_get_stats_returns_dict(self, store: HeartbeatStore, policy: SchedulerPolicy):
        stats = policy.get_stats()
        assert isinstance(stats, dict)
        expected_keys = {"total_recommendations", "cache_hits", "avg_latency_ms"}
        assert expected_keys.issubset(stats.keys())

    def test_stats_thread_safety(self, store: HeartbeatStore):
        """Concurrent recommend() calls must not corrupt stats counters."""
        _seed_node(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store)
        n_threads = 20

        def worker():
            for _ in range(5):
                policy.recommend()

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = policy.get_stats()["total_recommendations"]
        assert total == n_threads * 5

    def test_record_stats_internal_direct(self, policy: SchedulerPolicy):
        """Directly test the _record_stats internal helper."""
        t0 = time.monotonic() - 0.05  # 50ms ago
        policy._record_stats(t0)
        stats = policy.get_stats()
        assert stats["total_recommendations"] == 1
        assert stats["avg_latency_ms"] >= 50.0


# ===========================================================================
# Singleton pattern
# ===========================================================================


class TestSingletonPattern:
    def test_get_scheduler_policy_returns_same_instance(self, store: HeartbeatStore):
        p1 = get_scheduler_policy(heartbeat_store=store)
        p2 = get_scheduler_policy(heartbeat_store=store)
        assert p1 is p2

    def test_reset_allows_new_singleton(self, store: HeartbeatStore):
        p1 = get_scheduler_policy(heartbeat_store=store)
        reset_scheduler_policy()
        p2 = get_scheduler_policy(heartbeat_store=store)
        assert p1 is not p2

    def test_singleton_ignores_second_heartbeat_store(self, tmp_path: Path):
        store_a = _make_store(tmp_path / "a")
        store_b = _make_store(tmp_path / "b")

        p1 = get_scheduler_policy(heartbeat_store=store_a)
        p2 = get_scheduler_policy(heartbeat_store=store_b)
        # Both calls return the same instance; store_b is ignored
        assert p1 is p2
        assert p1._hb_store is store_a

    def test_reset_scheduler_policy_clears_instance(self, store: HeartbeatStore):
        get_scheduler_policy(heartbeat_store=store)
        reset_scheduler_policy()

        import forge_harness.webhook_server.services.scheduler_policy as mod

        assert mod._policy_instance is None

    def test_get_scheduler_policy_no_args_uses_default_store(self):
        """When called with no heartbeat_store it imports get_heartbeat_store."""
        mock_store = MagicMock(spec=HeartbeatStore)
        mock_store.get_scheduler_view.return_value = {
            "ranked_nodes": [],
            "recommended_node_id": None,
        }
        # get_heartbeat_store is lazily imported inside the function body, so we
        # patch it on the source module where it is defined.
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.get_heartbeat_store",
            return_value=mock_store,
        ):
            # Also patch the lazy import that happens inside get_scheduler_policy()
            import forge_harness.webhook_server.services.heartbeat_store as hb_mod

            original = hb_mod.get_heartbeat_store

            def fake_get():
                return mock_store

            hb_mod.get_heartbeat_store = fake_get
            try:
                policy = get_scheduler_policy()
                assert policy._hb_store is mock_store
            finally:
                hb_mod.get_heartbeat_store = original


# ===========================================================================
# Weight composition arithmetic
# ===========================================================================


class TestWeightComposition:
    def test_weights_sum_to_one(self):
        total = _W_AVAILABILITY + _W_CAPACITY + _W_AFFINITY + _W_PATH_SAFETY
        assert abs(total - 1.0) < 1e-9

    def test_perfect_node_score_close_to_one(self, store: HeartbeatStore):
        """Fully idle node with general task type and no path conflict should score near 1."""
        _seed_node(store, "perfect", cpu=0.0, mem=0.0, active_agents=0)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(task_type="general")
        # availability=1.0(40%) + capacity=1.0(30%) + affinity=1.0(20%) + path_safety=1.0(10%) = 1.0
        assert recs[0].score == pytest.approx(1.0, abs=1e-3)

    def test_saturated_node_general_task_score(self, store: HeartbeatStore):
        """Saturated node: availability=0, capacity=0, affinity=1(general), safety=1 → 0.30."""
        _seed_node(store, "sat", cpu=100.0, mem=100.0, active_agents=8)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(task_type="general")
        # 0*0.4 + 0*0.3 + 1*0.2 + 1*0.1 = 0.30
        assert recs[0].score == pytest.approx(0.30, abs=1e-3)


# ===========================================================================
# Lease store error resilience
# ===========================================================================


class TestLeaseStoreErrors:
    def test_lease_store_exception_degrades_gracefully(self, store: HeartbeatStore):
        """If lease_store.list_leases() raises, recommendations still work."""

        class BrokenLeaseStore:
            def list_leases(self) -> list[TaskLease]:
                raise RuntimeError("DB connection failed")

        _seed_node(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=BrokenLeaseStore())
        recs = policy.recommend(path_lock="src/api/routes.py")
        # Should still return a recommendation (path safety defaults to 1.0)
        assert len(recs) == 1
        assert recs[0].path_conflicts == []

    def test_none_lease_store_no_conflict_data(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=None)
        recs = policy.recommend(path_lock="src/api/routes.py")
        assert recs[0].path_conflicts == []

    def test_releasing_state_lease_not_conflict(self, store: HeartbeatStore):
        """Leases in RELEASING state should not count as path conflicts."""
        _seed_node(store, "nova")
        # Create a releasing lease — it's giving up the lock
        lease = TaskLease(task_id="CP-REL")
        lease.state = LeaseState.UNCLAIMED
        lease.claim("nova", "forge:nova")
        lease.state = LeaseState.ACTIVE
        lease.path_lock = "src/api/routes.py"
        lease.state = LeaseState.RELEASING

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/api/routes.py")
        assert recs[0].path_conflicts == []

    def test_iterable_lease_store(self, store: HeartbeatStore):
        """Lease store that is a plain iterable (not callable) is supported."""
        _seed_node(store, "nova")
        lease = _make_active_lease("CP-IT", "nova", path_lock="src/foo.py")
        # Pass a plain list (iterable, not callable with list_leases)
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/foo.py")
        assert "CP-IT" in recs[0].path_conflicts


# ===========================================================================
# Capacity score unit tests
# ===========================================================================


class TestCapacityScore:
    def test_zero_agents_full_capacity(self):
        assert SchedulerPolicy._capacity_score(0) == 0.0

    def test_max_agents_capacity_one(self):
        assert SchedulerPolicy._capacity_score(_MAX_AGENTS_PER_NODE) == pytest.approx(1.0)

    def test_half_agents_half_capacity(self):
        half = _MAX_AGENTS_PER_NODE // 2
        assert SchedulerPolicy._capacity_score(half) == pytest.approx(0.5)

    def test_over_max_agents_clamped_to_one(self):
        # More agents than max should clamp at 1.0
        assert SchedulerPolicy._capacity_score(_MAX_AGENTS_PER_NODE + 10) == pytest.approx(1.0)

    def test_one_available_agent(self):
        score = SchedulerPolicy._capacity_score(1)
        assert 0.0 < score <= 1.0
        assert score == pytest.approx(1 / _MAX_AGENTS_PER_NODE)


# ===========================================================================
# Affinity score unit tests
# ===========================================================================


class TestAffinityScore:
    def test_general_task_type_returns_one(self):
        assert SchedulerPolicy._affinity_score("general", {}) == 1.0

    def test_empty_string_task_type_returns_one(self):
        assert SchedulerPolicy._affinity_score("", {}) == 1.0

    def test_no_capabilities_returns_neutral(self):
        assert SchedulerPolicy._affinity_score("python", {}) == 0.5

    def test_empty_capabilities_dict_returns_neutral(self):
        assert SchedulerPolicy._affinity_score("python", {"capabilities": {}}) == 0.5

    def test_matching_capability_returns_one(self):
        node = {"capabilities": {"python": True}}
        assert SchedulerPolicy._affinity_score("python", node) == 1.0

    def test_explicit_false_capability_returns_zero(self):
        node = {"capabilities": {"python": False}}
        assert SchedulerPolicy._affinity_score("python", node) == 0.0

    def test_ios_capability_key_mapping(self):
        node = {"capabilities": {"ios_simulator": True}}
        assert SchedulerPolicy._affinity_score("ios", node) == 1.0

    def test_docker_capability_key_mapping(self):
        node = {"capabilities": {"docker": True}}
        assert SchedulerPolicy._affinity_score("docker", node) == 1.0

    def test_claude_capability_key_mapping(self):
        node = {"capabilities": {"claude": True}}
        assert SchedulerPolicy._affinity_score("claude", node) == 1.0

    def test_ollama_capability_key_mapping(self):
        node = {"capabilities": {"ollama": True}}
        assert SchedulerPolicy._affinity_score("ollama", node) == 1.0

    def test_gemini_capability_key_mapping(self):
        node = {"capabilities": {"gemini": True}}
        assert SchedulerPolicy._affinity_score("gemini", node) == 1.0

    def test_kimi_capability_key_mapping(self):
        node = {"capabilities": {"kimi": True}}
        assert SchedulerPolicy._affinity_score("kimi", node) == 1.0

    def test_opencode_capability_key_mapping(self):
        node = {"capabilities": {"opencode": True}}
        assert SchedulerPolicy._affinity_score("opencode", node) == 1.0

    def test_codex_capability_key_mapping(self):
        node = {"capabilities": {"codex": True}}
        assert SchedulerPolicy._affinity_score("codex", node) == 1.0

    def test_unknown_task_type_with_unrelated_capabilities_returns_neutral(self):
        # Node has capabilities but not for the requested type
        node = {"capabilities": {"python": True, "docker": True}}
        # "quantum" is not in the map, so it looks for key "quantum" in capabilities
        result = SchedulerPolicy._affinity_score("quantum", node)
        assert result == 0.0  # capability key not found → False → 0.0

    def test_task_type_not_in_map_uses_task_type_as_key(self):
        # A type not in _TASK_TYPE_CAPABILITY_MAP falls back to the raw type as key
        node = {"capabilities": {"custom_model": True}}
        assert SchedulerPolicy._affinity_score("custom_model", node) == 1.0


# ===========================================================================
# Path safety score unit tests
# ===========================================================================


class TestPathSafetyScore:
    def _make_policy(self, store: HeartbeatStore) -> SchedulerPolicy:
        return SchedulerPolicy(heartbeat_store=store)

    def test_no_path_lock_requested_returns_one(self, store: HeartbeatStore):
        policy = self._make_policy(store)
        score, conflicts = policy._path_safety_score("nova", None, [])
        assert score == 1.0
        assert conflicts == []

    def test_empty_path_lock_string_returns_one(self, store: HeartbeatStore):
        policy = self._make_policy(store)
        score, conflicts = policy._path_safety_score("nova", "", [])
        assert score == 1.0
        assert conflicts == []

    def test_no_leases_returns_safe(self, store: HeartbeatStore):
        policy = self._make_policy(store)
        score, conflicts = policy._path_safety_score("nova", "src/foo.py", [])
        assert score == 1.0
        assert conflicts == []

    def test_conflict_on_same_node_returns_zero(self, store: HeartbeatStore):
        policy = self._make_policy(store)
        lease = _make_active_lease("T-1", "nova", path_lock="src/foo.py")
        score, conflicts = policy._path_safety_score("nova", "src/foo.py", [lease])
        assert score == 0.0
        assert "T-1" in conflicts

    def test_conflict_on_different_node_not_penalised(self, store: HeartbeatStore):
        policy = self._make_policy(store)
        lease = _make_active_lease("T-1", "prya", path_lock="src/foo.py")
        score, conflicts = policy._path_safety_score("nova", "src/foo.py", [lease])
        assert score == 1.0
        assert conflicts == []

    def test_multiple_conflicts_all_returned(self, store: HeartbeatStore):
        policy = self._make_policy(store)
        leases = [
            _make_active_lease("T-1", "nova", path_lock="src/foo.py"),
            _make_active_lease("T-2", "nova", path_lock="src/foo.py"),
            _make_active_lease("T-3", "nova", path_lock="src/bar.py"),  # different path
        ]
        score, conflicts = policy._path_safety_score("nova", "src/foo.py", leases)
        assert score == 0.0
        assert set(conflicts) == {"T-1", "T-2"}
        assert "T-3" not in conflicts


# ===========================================================================
# Active lease states tested individually
# ===========================================================================


class TestActiveLeaseStateFiltering:
    def test_claimed_state_counts_as_active_conflict(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        lease = _make_active_lease("CP-CLAIMED", "nova", path_lock="p.py", state=LeaseState.CLAIMED)
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="p.py")
        assert "CP-CLAIMED" in recs[0].path_conflicts

    def test_renewing_state_counts_as_active_conflict(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        lease = _make_active_lease("CP-RENEW", "nova", path_lock="p.py", state=LeaseState.RENEWING)
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="p.py")
        assert "CP-RENEW" in recs[0].path_conflicts

    def test_expired_state_not_a_conflict(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        lease = _make_active_lease("CP-EXP", "nova", path_lock="p.py")
        lease.state = LeaseState.EXPIRED
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="p.py")
        assert recs[0].path_conflicts == []

    def test_requeued_state_not_a_conflict(self, store: HeartbeatStore):
        _seed_node(store, "nova")
        lease = _make_active_lease("CP-REQ", "nova", path_lock="p.py")
        lease.state = LeaseState.REQUEUED
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="p.py")
        assert recs[0].path_conflicts == []

    def test_lease_without_path_lock_ignored_in_conflict_check(self, store: HeartbeatStore):
        """An active lease with no path_lock should not affect path safety scoring."""
        _seed_node(store, "nova")
        lease = _make_active_lease("CP-NOPATH", "nova", path_lock=None)
        # Override path_lock to ensure it remains None
        lease.path_lock = None
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/api.py")
        assert recs[0].path_conflicts == []


# ===========================================================================
# Score node internal method
# ===========================================================================


class TestScoreNodeInternal:
    def test_score_node_returns_four_tuple(self, store: HeartbeatStore):
        policy = SchedulerPolicy(heartbeat_store=store)
        node_entry = {
            "node_id": "test_node",
            "availability_score": 0.8,
            "active_agents": 2,
        }
        composite, reason, available, conflicts = policy._score_node(
            node_entry, "general", None, []
        )
        assert isinstance(composite, float)
        assert isinstance(reason, str)
        assert isinstance(available, int)
        assert isinstance(conflicts, list)

    def test_score_node_path_conflict_appears_in_reason(self, store: HeartbeatStore):
        policy = SchedulerPolicy(heartbeat_store=store)
        node_entry = {"node_id": "nova", "availability_score": 0.9, "active_agents": 1}
        lease = _make_active_lease("CONF-01", "nova", path_lock="src/x.py")
        _, reason, _, conflicts = policy._score_node(
            node_entry, "general", "src/x.py", [lease]
        )
        assert "PATH CONFLICT" in reason
        assert "CONF-01" in reason
        assert "CONF-01" in conflicts

    def test_score_node_no_active_agents_key_defaults_to_zero(self, store: HeartbeatStore):
        policy = SchedulerPolicy(heartbeat_store=store)
        node_entry = {"node_id": "bare_node", "availability_score": 0.5}
        # active_agents key missing — should default to 0, giving max available slots
        _, _, available_agents, _ = policy._score_node(node_entry, "general", None, [])
        assert available_agents == _MAX_AGENTS_PER_NODE

    def test_score_node_overloaded_clamps_available_to_zero(self, store: HeartbeatStore):
        policy = SchedulerPolicy(heartbeat_store=store)
        node_entry = {
            "node_id": "overloaded",
            "availability_score": 0.0,
            "active_agents": _MAX_AGENTS_PER_NODE + 5,
        }
        _, _, available_agents, _ = policy._score_node(node_entry, "general", None, [])
        assert available_agents == 0

    def test_score_node_composite_within_bounds(self, store: HeartbeatStore):
        policy = SchedulerPolicy(heartbeat_store=store)
        for avail in [0.0, 0.5, 1.0]:
            for agents in [0, 4, 8]:
                node_entry = {
                    "node_id": "n",
                    "availability_score": avail,
                    "active_agents": agents,
                }
                composite, _, _, _ = policy._score_node(node_entry, "general", None, [])
                assert 0.0 <= composite <= 1.0


# ===========================================================================
# Tie-breaking determinism
# ===========================================================================


class TestTieBreaking:
    def test_equal_score_nodes_sorted_by_node_id(self, store: HeartbeatStore):
        """When two nodes have identical scores they should be ordered alphabetically."""
        # Use identical heartbeat data so scores are equal
        for name in ("zebra", "alpha", "mango"):
            _seed_node(store, name, cpu=30.0, mem=30.0, active_agents=3)

        original_view = store.get_scheduler_view()
        # Force identical availability_score and capability data
        for node in original_view["ranked_nodes"]:
            node["availability_score"] = 0.5
            node["capabilities"] = {}

        with patch.object(store, "get_scheduler_view", return_value=original_view):
            policy = SchedulerPolicy(heartbeat_store=store)
            recs = policy.recommend(task_type="general")

        node_ids = [r.node_id for r in recs]
        assert node_ids == sorted(node_ids)


# ===========================================================================
# Collect active leases internal helper
# ===========================================================================


class TestCollectActiveLeases:
    def test_none_lease_store_returns_empty(self, store: HeartbeatStore):
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=None)
        assert policy._collect_active_leases() == []

    def test_iterable_store_filters_to_active(self, store: HeartbeatStore):
        active_lease = _make_active_lease("ACT-1", "nova", path_lock="a.py")
        unclaimed_lease = TaskLease(task_id="UNCL-1")
        unclaimed_lease.path_lock = "b.py"
        # UNCLAIMED has no path_lock filtering needed — it's already filtered by state
        policy = SchedulerPolicy(
            heartbeat_store=store, lease_store=[active_lease, unclaimed_lease]
        )
        result = policy._collect_active_leases()
        task_ids = {l.task_id for l in result}
        assert "ACT-1" in task_ids
        assert "UNCL-1" not in task_ids

    def test_callable_store_filters_to_active(self, store: HeartbeatStore):
        active_lease = _make_active_lease("ACT-C", "nova", path_lock="c.py")
        expired_lease = _make_active_lease("EXP-C", "nova", path_lock="c.py")
        expired_lease.state = LeaseState.EXPIRED

        class FakeStore:
            def list_leases(self):
                return [active_lease, expired_lease]

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=FakeStore())
        result = policy._collect_active_leases()
        task_ids = {l.task_id for l in result}
        assert "ACT-C" in task_ids
        assert "EXP-C" not in task_ids

    def test_exception_in_list_leases_returns_empty(self, store: HeartbeatStore):
        class ErrorStore:
            def list_leases(self):
                raise ConnectionError("timeout")

        policy = SchedulerPolicy(heartbeat_store=store, lease_store=ErrorStore())
        result = policy._collect_active_leases()
        assert result == []

    def test_lease_without_path_lock_excluded_from_active(self, store: HeartbeatStore):
        """Active leases without a path_lock are filtered out of conflict tracking."""
        lease = _make_active_lease("NO-PATH", "nova")
        # Make sure path_lock is None (no path lock requested)
        lease.path_lock = None
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        result = policy._collect_active_leases()
        # Should not be included since path_lock is falsy
        assert result == []


# ===========================================================================
# Additional recommend edge cases
# ===========================================================================


class TestRecommendEdgeCases:
    def test_recommend_records_stats_on_empty_candidates(self, store: HeartbeatStore):
        """Even with no candidates, recommend() still increments stats counter."""
        policy = SchedulerPolicy(heartbeat_store=store)
        policy.recommend()
        assert policy.get_stats()["total_recommendations"] == 1

    def test_recommend_with_task_type_all_mapped_types(self, store: HeartbeatStore):
        """Calling recommend() with each known task type should not raise."""
        _seed_node(store, "nova")
        policy = SchedulerPolicy(heartbeat_store=store)
        task_types = ["python", "ios", "docker", "claude", "codex", "gemini", "kimi", "opencode", "ollama"]
        for tt in task_types:
            recs = policy.recommend(task_type=tt)
            assert len(recs) == 1, f"Expected 1 recommendation for task_type={tt!r}"

    def test_recommend_reason_contains_conflict_notice(self, store: HeartbeatStore):
        """When a conflict exists, the reason string advertises it."""
        _seed_node(store, "nova")
        lease = _make_active_lease("TASK-X", "nova", path_lock="src/conflict.py")
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=[lease])
        recs = policy.recommend(path_lock="src/conflict.py")
        assert "PATH CONFLICT" in recs[0].reason
        assert "TASK-X" in recs[0].reason

    def test_recommend_many_nodes_sorted_correctly(self, store: HeartbeatStore):
        """Large candidate pool is still ranked correctly."""
        for i in range(10):
            _seed_node(store, f"node-{i:02d}", cpu=float(i * 10), mem=float(i * 5), active_agents=i)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend()
        assert len(recs) == 10
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_recommend_exclude_subset_of_nodes(self, store: HeartbeatStore):
        """Partial exclusion leaves remaining nodes available."""
        for name in ("a", "b", "c", "d"):
            _seed_node(store, name)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend(exclude_nodes=["b", "d"])
        node_ids = {r.node_id for r in recs}
        assert node_ids == {"a", "c"}

    def test_recommend_path_lock_none_no_conflicts_regardless_of_leases(self, store: HeartbeatStore):
        """When no path_lock requested, conflicts are always empty even with active leases."""
        _seed_node(store, "nova")
        leases = [
            _make_active_lease(f"T-{i}", "nova", path_lock="src/x.py")
            for i in range(5)
        ]
        policy = SchedulerPolicy(heartbeat_store=store, lease_store=leases)
        recs = policy.recommend(path_lock=None)
        assert all(r.path_conflicts == [] for r in recs)

    def test_recommend_node_with_all_agents_busy_has_zero_available(self, store: HeartbeatStore):
        """A node with active_agents == _MAX_AGENTS_PER_NODE shows 0 available slots."""
        _seed_node(store, "full", active_agents=_MAX_AGENTS_PER_NODE)
        policy = SchedulerPolicy(heartbeat_store=store)
        recs = policy.recommend()
        assert recs[0].available_agents == 0


# ===========================================================================
# Weekly performance report trigger (async)
# ===========================================================================


class TestWeeklyPerformanceReportTrigger:
    @pytest.mark.asyncio
    async def test_trigger_calls_schedule_weekly_aligned(self):
        """run_weekly_performance_report_trigger delegates to PerformanceReporter.

        The function does a lazy import of get_performance_reporter inside the
        function body, so we patch it on the performance_reporter module directly.
        """
        from forge_harness.webhook_server.services.scheduler_policy import (
            run_weekly_performance_report_trigger,
        )

        mock_reporter = MagicMock()

        async def fake_schedule():
            pass

        mock_reporter.schedule_weekly_aligned = MagicMock(side_effect=fake_schedule)

        # The lazy import happens inside the function body:
        #   from forge_harness.webhook_server.services.performance_reporter import get_performance_reporter
        # Patch it on the source module where it lives.
        with patch(
            "forge_harness.webhook_server.services.performance_reporter.get_performance_reporter",
            return_value=mock_reporter,
        ):
            # Also need to patch the symbol that gets imported at call time
            import forge_harness.webhook_server.services.performance_reporter as perf_mod

            original_fn = perf_mod.get_performance_reporter

            def fake_get():
                return mock_reporter

            perf_mod.get_performance_reporter = fake_get
            try:
                await run_weekly_performance_report_trigger()
            finally:
                perf_mod.get_performance_reporter = original_fn

        mock_reporter.schedule_weekly_aligned.assert_called_once()
