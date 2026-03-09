"""Tests for load_balancer.py — LoadBalancer Service (DF-2005).

Tests cover:
- LoadBalancer initialization
- Agent registration (new, re-register, validation errors)
- Agent load updates (happy path, validation errors, unregistered agent)
- Score computation helpers (capacity, budget, affinity, freshness)
- Recommendation with least_loaded strategy
- Recommendation with affinity_first strategy
- Recommendation with round_robin strategy
- Round-robin counter cycling and wrapping
- get_agent_stats snapshot with live heartbeat age
- Singleton helpers (get_load_balancer, reset_load_balancer)
- Thread-safety assertions (structural)
- Edge cases (empty registry, saturated agents, stale agents)
"""

from __future__ import annotations

import time
from threading import Thread
from typing import Any
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.models.agent_load import (
    AgentCapability,
    BalancingRecommendation,
    BalancingStrategy,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.load_balancer import (
    _FRESHNESS_FULL_SECONDS,
    _FRESHNESS_ZERO_SECONDS,
    _W_AFFINITY,
    _W_BUDGET,
    _W_CAPACITY,
    _W_FRESHNESS,
    _W_NODE,
    LoadBalancer,
    get_load_balancer,
    reset_load_balancer,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clean_singleton() -> Any:
    """Reset the global singleton before and after every test."""
    reset_load_balancer()
    yield
    reset_load_balancer()


@pytest.fixture
def lb() -> LoadBalancer:
    """Return a fresh LoadBalancer instance."""
    return LoadBalancer()


@pytest.fixture
def simple_lane() -> WorkCellLane:
    return WorkCellLane.api_simple


@pytest.fixture
def docs_lane() -> WorkCellLane:
    return WorkCellLane.docs


# =============================================================================
# LoadBalancer Initialization
# =============================================================================


class TestLoadBalancerInit:
    """Tests for LoadBalancer.__init__."""

    def test_starts_with_empty_agent_registry(self, lb: LoadBalancer) -> None:
        """A new LoadBalancer should have no registered agents."""
        stats = lb.get_agent_stats()
        assert stats == {}

    def test_starts_with_empty_rr_counters(self, lb: LoadBalancer) -> None:
        """Round-robin counters should be empty on init."""
        assert lb._rr_counters == {}

    def test_starts_with_empty_heartbeat_times(self, lb: LoadBalancer) -> None:
        """Heartbeat timestamp registry should be empty on init."""
        assert lb._heartbeat_times == {}

    def test_lock_is_rlock(self, lb: LoadBalancer) -> None:
        """_lock attribute should support re-entrant locking."""
        # RLock can be acquired twice from the same thread
        acquired = lb._lock.acquire()
        assert acquired is True
        # Second acquire from same thread should also succeed (re-entrant)
        acquired2 = lb._lock.acquire()
        assert acquired2 is True
        lb._lock.release()
        lb._lock.release()


# =============================================================================
# register_agent Tests
# =============================================================================


class TestRegisterAgent:
    """Tests for LoadBalancer.register_agent."""

    def test_register_single_agent_returns_capability(self, lb: LoadBalancer) -> None:
        """register_agent should return an AgentCapability with correct fields."""
        cap = lb.register_agent(
            "forge:nova",
            [WorkCellLane.api_simple, WorkCellLane.docs],
            max_concurrent=5,
        )

        assert isinstance(cap, AgentCapability)
        assert cap.agent_id == "forge:nova"
        assert WorkCellLane.api_simple in cap.supported_lanes
        assert WorkCellLane.docs in cap.supported_lanes
        assert cap.max_concurrent == 5
        assert cap.current_active == 0
        assert cap.context_budget_remaining_pct == 100.0

    def test_registered_agent_appears_in_stats(self, lb: LoadBalancer) -> None:
        """Registered agent should appear in get_agent_stats()."""
        lb.register_agent("agent-a", [WorkCellLane.research])
        stats = lb.get_agent_stats()
        assert "agent-a" in stats

    def test_default_max_concurrent_is_three(self, lb: LoadBalancer) -> None:
        """Default max_concurrent should be 3."""
        cap = lb.register_agent("agent-x", [WorkCellLane.docs])
        assert cap.max_concurrent == 3

    def test_register_with_empty_lanes(self, lb: LoadBalancer) -> None:
        """Agent with empty supported_lanes should be registered but never recommended."""
        cap = lb.register_agent("ghost", [])
        assert cap.supported_lanes == []

        recs = lb.recommend(WorkCellLane.api_simple)
        assert recs == []

    def test_reregister_preserves_active_count(self, lb: LoadBalancer) -> None:
        """Re-registering with the same max_concurrent preserves current_active."""
        lb.register_agent("agent-b", [WorkCellLane.docs])
        lb.update_agent_load("agent-b", current_active=2, context_budget_pct=80.0)

        # Re-register with same max_concurrent
        cap2 = lb.register_agent("agent-b", [WorkCellLane.docs, WorkCellLane.research])
        assert cap2.current_active == 2

    def test_reregister_different_max_concurrent_still_preserves_active(
        self, lb: LoadBalancer
    ) -> None:
        """Re-registering with a different max_concurrent still preserves active count."""
        lb.register_agent("agent-c", [WorkCellLane.docs], max_concurrent=3)
        lb.update_agent_load("agent-c", current_active=1, context_budget_pct=60.0)

        # Re-register with different max_concurrent
        cap2 = lb.register_agent("agent-c", [WorkCellLane.docs], max_concurrent=5)
        assert cap2.current_active == 1
        assert cap2.max_concurrent == 5

    def test_reregister_preserves_budget(self, lb: LoadBalancer) -> None:
        """Re-registering preserves context_budget_remaining_pct."""
        lb.register_agent("agent-d", [WorkCellLane.docs])
        lb.update_agent_load("agent-d", current_active=0, context_budget_pct=42.5)

        cap2 = lb.register_agent("agent-d", [WorkCellLane.docs])
        assert cap2.context_budget_remaining_pct == 42.5

    def test_reregister_replaces_supported_lanes(self, lb: LoadBalancer) -> None:
        """Re-registering overwrites the supported_lanes list."""
        lb.register_agent("agent-e", [WorkCellLane.docs])
        cap2 = lb.register_agent("agent-e", [WorkCellLane.refactor])

        assert WorkCellLane.refactor in cap2.supported_lanes
        assert WorkCellLane.docs not in cap2.supported_lanes

    def test_register_raises_on_max_concurrent_zero(self, lb: LoadBalancer) -> None:
        """max_concurrent < 1 must raise ValueError."""
        with pytest.raises(ValueError, match="max_concurrent must be >= 1"):
            lb.register_agent("bad-agent", [WorkCellLane.docs], max_concurrent=0)

    def test_register_raises_on_negative_max_concurrent(self, lb: LoadBalancer) -> None:
        """Negative max_concurrent must raise ValueError."""
        with pytest.raises(ValueError, match="max_concurrent must be >= 1"):
            lb.register_agent("bad-agent", [WorkCellLane.docs], max_concurrent=-1)

    def test_multiple_agents_all_stored(self, lb: LoadBalancer) -> None:
        """Multiple distinct agents should all appear in the registry."""
        lb.register_agent("a1", [WorkCellLane.docs])
        lb.register_agent("a2", [WorkCellLane.research])
        lb.register_agent("a3", [WorkCellLane.deployment])

        stats = lb.get_agent_stats()
        assert set(stats.keys()) == {"a1", "a2", "a3"}


# =============================================================================
# update_agent_load Tests
# =============================================================================


class TestUpdateAgentLoad:
    """Tests for LoadBalancer.update_agent_load."""

    def test_update_stores_new_values(self, lb: LoadBalancer) -> None:
        """update_agent_load should persist current_active and budget."""
        lb.register_agent("ag", [WorkCellLane.api_simple])
        lb.update_agent_load("ag", current_active=2, context_budget_pct=55.0)

        stats = lb.get_agent_stats()
        cap = stats["ag"]
        assert cap.current_active == 2
        assert cap.context_budget_remaining_pct == 55.0

    def test_update_resets_heartbeat_age(self, lb: LoadBalancer) -> None:
        """After update, heartbeat age should be near zero."""
        lb.register_agent("ag", [WorkCellLane.docs])
        lb.update_agent_load("ag", current_active=0, context_budget_pct=100.0)

        stats = lb.get_agent_stats()
        age = stats["ag"].last_heartbeat_age_seconds
        assert age < 1.0  # Should be essentially 0 immediately after update

    def test_update_raises_for_unregistered_agent(self, lb: LoadBalancer) -> None:
        """Updating an agent that was never registered must raise KeyError."""
        with pytest.raises(KeyError, match="not registered"):
            lb.update_agent_load("ghost", current_active=0, context_budget_pct=100.0)

    def test_update_raises_on_negative_active(self, lb: LoadBalancer) -> None:
        """current_active < 0 must raise ValueError."""
        lb.register_agent("ag", [WorkCellLane.docs])
        with pytest.raises(ValueError, match="current_active must be >= 0"):
            lb.update_agent_load("ag", current_active=-1, context_budget_pct=100.0)

    def test_update_raises_on_budget_above_100(self, lb: LoadBalancer) -> None:
        """context_budget_pct > 100 must raise ValueError."""
        lb.register_agent("ag", [WorkCellLane.docs])
        with pytest.raises(ValueError, match="context_budget_pct must be in"):
            lb.update_agent_load("ag", current_active=0, context_budget_pct=101.0)

    def test_update_raises_on_budget_below_0(self, lb: LoadBalancer) -> None:
        """context_budget_pct < 0 must raise ValueError."""
        lb.register_agent("ag", [WorkCellLane.docs])
        with pytest.raises(ValueError, match="context_budget_pct must be in"):
            lb.update_agent_load("ag", current_active=0, context_budget_pct=-0.1)

    def test_update_accepts_boundary_values(self, lb: LoadBalancer) -> None:
        """Boundary values 0.0 and 100.0 for budget should be accepted."""
        lb.register_agent("ag", [WorkCellLane.docs])
        lb.update_agent_load("ag", current_active=0, context_budget_pct=0.0)
        lb.update_agent_load("ag", current_active=0, context_budget_pct=100.0)

    def test_update_accepts_zero_active(self, lb: LoadBalancer) -> None:
        """current_active = 0 is a valid value."""
        lb.register_agent("ag", [WorkCellLane.docs])
        lb.update_agent_load("ag", current_active=0, context_budget_pct=100.0)
        stats = lb.get_agent_stats()
        assert stats["ag"].current_active == 0


# =============================================================================
# Score Computation Helpers
# =============================================================================


class TestCapacityScore:
    """Tests for LoadBalancer._compute_capacity_score."""

    def test_capacity_idle_agent(self) -> None:
        """Agent with 0 active tasks should have capacity score 1.0."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[WorkCellLane.docs],
            max_concurrent=3,
            current_active=0,
        )
        score = LoadBalancer._compute_capacity_score(cap)
        assert score == 1.0

    def test_capacity_half_loaded(self) -> None:
        """Agent at 50% load should have capacity score 0.5."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[WorkCellLane.docs],
            max_concurrent=4,
            current_active=2,
        )
        score = LoadBalancer._compute_capacity_score(cap)
        assert score == pytest.approx(0.5)

    def test_capacity_fully_saturated(self) -> None:
        """Agent at 100% load should have capacity score 0.0."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[WorkCellLane.docs],
            max_concurrent=3,
            current_active=3,
        )
        score = LoadBalancer._compute_capacity_score(cap)
        assert score == 0.0

    def test_capacity_over_saturated_clamps_to_zero(self) -> None:
        """Agent with active > max_concurrent should clamp to 0.0."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[WorkCellLane.docs],
            max_concurrent=2,
            current_active=5,
        )
        score = LoadBalancer._compute_capacity_score(cap)
        assert score == 0.0

    def test_capacity_one_slot_remaining(self) -> None:
        """Agent with 1 of 3 slots open should have capacity score 1/3."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[WorkCellLane.docs],
            max_concurrent=3,
            current_active=2,
        )
        score = LoadBalancer._compute_capacity_score(cap)
        assert score == pytest.approx(1 / 3)


class TestBudgetScore:
    """Tests for LoadBalancer._compute_budget_score."""

    def test_budget_full(self) -> None:
        """100% budget remaining → score 1.0."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[],
            context_budget_remaining_pct=100.0,
        )
        assert LoadBalancer._compute_budget_score(cap) == pytest.approx(1.0)

    def test_budget_zero(self) -> None:
        """0% budget remaining → score 0.0."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[],
            context_budget_remaining_pct=0.0,
        )
        assert LoadBalancer._compute_budget_score(cap) == pytest.approx(0.0)

    def test_budget_fifty_percent(self) -> None:
        """50% budget remaining → score 0.5."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[],
            context_budget_remaining_pct=50.0,
        )
        assert LoadBalancer._compute_budget_score(cap) == pytest.approx(0.5)

    def test_budget_arbitrary_value(self) -> None:
        """Budget score should equal pct / 100 for arbitrary values."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[],
            context_budget_remaining_pct=73.4,
        )
        assert LoadBalancer._compute_budget_score(cap) == pytest.approx(0.734)


class TestLaneAffinity:
    """Tests for LoadBalancer._compute_lane_affinity."""

    def test_affinity_when_lane_present(self) -> None:
        """Should return 1.0 if lane is in supported_lanes."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[WorkCellLane.docs, WorkCellLane.research],
        )
        score = LoadBalancer._compute_lane_affinity(cap, WorkCellLane.docs)
        assert score == 1.0

    def test_affinity_when_lane_absent(self) -> None:
        """Should return 0.0 if lane is not in supported_lanes."""
        cap = AgentCapability(
            agent_id="x",
            supported_lanes=[WorkCellLane.docs],
        )
        score = LoadBalancer._compute_lane_affinity(cap, WorkCellLane.security_change)
        assert score == 0.0

    def test_affinity_empty_supported_lanes(self) -> None:
        """Should return 0.0 for an agent with no lanes."""
        cap = AgentCapability(agent_id="x", supported_lanes=[])
        score = LoadBalancer._compute_lane_affinity(cap, WorkCellLane.docs)
        assert score == 0.0


class TestFreshnessScore:
    """Tests for LoadBalancer._compute_freshness_score."""

    def test_freshness_unknown_agent_returns_zero(self, lb: LoadBalancer) -> None:
        """Agent not in heartbeat registry should have freshness 0.0."""
        now = time.monotonic()
        score = lb._compute_freshness_score("unknown-agent", now)
        assert score == 0.0

    def test_freshness_just_registered_is_one(self, lb: LoadBalancer) -> None:
        """Freshly registered agent should have freshness score 1.0."""
        lb.register_agent("ag", [WorkCellLane.docs])
        now = time.monotonic()
        score = lb._compute_freshness_score("ag", now)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_freshness_within_full_window(self, lb: LoadBalancer) -> None:
        """Agent heartbeat within _FRESHNESS_FULL_SECONDS should score 1.0."""
        lb.register_agent("ag", [WorkCellLane.docs])
        # Fake heartbeat 30 seconds ago
        lb._heartbeat_times["ag"] = time.monotonic() - 30.0
        now = time.monotonic()
        score = lb._compute_freshness_score("ag", now)
        assert score == pytest.approx(1.0)

    def test_freshness_at_decay_boundary(self, lb: LoadBalancer) -> None:
        """Agent at exactly _FRESHNESS_FULL_SECONDS should still score 1.0."""
        lb.register_agent("ag", [WorkCellLane.docs])
        lb._heartbeat_times["ag"] = time.monotonic() - _FRESHNESS_FULL_SECONDS
        now = time.monotonic()
        score = lb._compute_freshness_score("ag", now)
        assert score == pytest.approx(1.0, abs=0.05)

    def test_freshness_past_stale_threshold_is_zero(self, lb: LoadBalancer) -> None:
        """Agent at or beyond _FRESHNESS_ZERO_SECONDS should score 0.0."""
        lb.register_agent("ag", [WorkCellLane.docs])
        lb._heartbeat_times["ag"] = time.monotonic() - _FRESHNESS_ZERO_SECONDS
        now = time.monotonic()
        score = lb._compute_freshness_score("ag", now)
        assert score == pytest.approx(0.0, abs=0.05)

    def test_freshness_very_old_heartbeat(self, lb: LoadBalancer) -> None:
        """A very old heartbeat (1 hour ago) should give 0.0."""
        lb.register_agent("ag", [WorkCellLane.docs])
        lb._heartbeat_times["ag"] = time.monotonic() - 3600.0
        now = time.monotonic()
        score = lb._compute_freshness_score("ag", now)
        assert score == 0.0

    def test_freshness_midpoint_decay(self, lb: LoadBalancer) -> None:
        """Midpoint between full and zero thresholds should give ~0.5."""
        lb.register_agent("ag", [WorkCellLane.docs])
        midpoint = (_FRESHNESS_FULL_SECONDS + _FRESHNESS_ZERO_SECONDS) / 2.0
        lb._heartbeat_times["ag"] = time.monotonic() - midpoint
        now = time.monotonic()
        score = lb._compute_freshness_score("ag", now)
        assert score == pytest.approx(0.5, abs=0.05)


# =============================================================================
# _build_recommendation Tests
# =============================================================================


class TestBuildRecommendation:
    """Tests for LoadBalancer._build_recommendation."""

    def test_returns_balancing_recommendation(self, lb: LoadBalancer) -> None:
        """Should return a BalancingRecommendation instance."""
        lb.register_agent("ag", [WorkCellLane.docs])
        cap = lb._agents["ag"]
        now = time.monotonic()
        rec = lb._build_recommendation(cap, WorkCellLane.docs, now)
        assert isinstance(rec, BalancingRecommendation)

    def test_recommendation_score_within_bounds(self, lb: LoadBalancer) -> None:
        """Composite score should be in [0.0, 1.0]."""
        lb.register_agent("ag", [WorkCellLane.docs])
        cap = lb._agents["ag"]
        now = time.monotonic()
        rec = lb._build_recommendation(cap, WorkCellLane.docs, now)
        assert 0.0 <= rec.score <= 1.0

    def test_recommendation_contains_four_reasons(self, lb: LoadBalancer) -> None:
        """Reasons list should contain one entry per score component (five total)."""
        lb.register_agent("ag", [WorkCellLane.docs])
        cap = lb._agents["ag"]
        now = time.monotonic()
        rec = lb._build_recommendation(cap, WorkCellLane.docs, now)
        assert len(rec.reasons) == 5

    def test_recommendation_agent_id_matches(self, lb: LoadBalancer) -> None:
        """agent_id on recommendation should match the capability's agent_id."""
        lb.register_agent("forge:nova", [WorkCellLane.api_simple])
        cap = lb._agents["forge:nova"]
        now = time.monotonic()
        rec = lb._build_recommendation(cap, WorkCellLane.api_simple, now)
        assert rec.agent_id == "forge:nova"

    def test_recommendation_lane_matches(self, lb: LoadBalancer) -> None:
        """lane on recommendation should match the requested lane."""
        lb.register_agent("ag", [WorkCellLane.research])
        cap = lb._agents["ag"]
        now = time.monotonic()
        rec = lb._build_recommendation(cap, WorkCellLane.research, now)
        assert rec.lane == WorkCellLane.research

    def test_full_fresh_idle_agent_score_formula(self, lb: LoadBalancer) -> None:
        """Perfect agent (idle, full budget, full freshness) should score near 1.0."""
        lb.register_agent("ag", [WorkCellLane.docs], max_concurrent=3)
        lb.update_agent_load("ag", current_active=0, context_budget_pct=100.0)

        cap = lb._agents["ag"]
        now = time.monotonic()
        rec = lb._build_recommendation(cap, WorkCellLane.docs, now)

        # capacity=1.0, budget=1.0, affinity=1.0, freshness=~1.0, node_score=1.0
        expected = (
            _W_CAPACITY * 1.0 + _W_BUDGET * 1.0 + _W_AFFINITY * 1.0
            + _W_FRESHNESS * 1.0 + _W_NODE * 1.0
        )
        assert rec.score == pytest.approx(expected, abs=0.01)

    def test_saturated_zero_budget_agent_score(self, lb: LoadBalancer) -> None:
        """Saturated agent with 0% budget should have low score (only affinity + freshness)."""
        lb.register_agent("ag", [WorkCellLane.docs], max_concurrent=3)
        lb.update_agent_load("ag", current_active=3, context_budget_pct=0.0)

        cap = lb._agents["ag"]
        now = time.monotonic()
        rec = lb._build_recommendation(cap, WorkCellLane.docs, now)

        # capacity=0.0, budget=0.0, affinity=1.0, freshness=~1.0, node_score=1.0
        expected = (
            _W_CAPACITY * 0.0 + _W_BUDGET * 0.0 + _W_AFFINITY * 1.0
            + _W_FRESHNESS * 1.0 + _W_NODE * 1.0
        )
        assert rec.score == pytest.approx(expected, abs=0.01)


# =============================================================================
# recommend — least_loaded Strategy
# =============================================================================


class TestRecommendLeastLoaded:
    """Tests for recommend() with BalancingStrategy.least_loaded (default)."""

    def test_no_eligible_agents_returns_empty(self, lb: LoadBalancer) -> None:
        """When no agents support the lane, recommend should return []."""
        lb.register_agent("ag", [WorkCellLane.docs])
        recs = lb.recommend(WorkCellLane.deployment)
        assert recs == []

    def test_single_eligible_agent_recommended(self, lb: LoadBalancer) -> None:
        """A single eligible agent should be the only recommendation."""
        lb.register_agent("ag", [WorkCellLane.api_simple])
        recs = lb.recommend(WorkCellLane.api_simple)
        assert len(recs) == 1
        assert recs[0].agent_id == "ag"

    def test_multiple_agents_sorted_by_score(self, lb: LoadBalancer) -> None:
        """Agents should be sorted best-first (highest score first)."""
        lb.register_agent("heavy", [WorkCellLane.docs], max_concurrent=3)
        lb.register_agent("light", [WorkCellLane.docs], max_concurrent=3)

        lb.update_agent_load("heavy", current_active=3, context_budget_pct=100.0)
        lb.update_agent_load("light", current_active=0, context_budget_pct=100.0)

        recs = lb.recommend(WorkCellLane.docs)
        assert len(recs) == 2
        assert recs[0].agent_id == "light"
        assert recs[1].agent_id == "heavy"

    def test_scores_descending_order(self, lb: LoadBalancer) -> None:
        """Recommendation scores should be non-increasing."""
        for i in range(4):
            lb.register_agent(f"agent-{i}", [WorkCellLane.research], max_concurrent=4)
            lb.update_agent_load(f"agent-{i}", current_active=i, context_budget_pct=100.0)

        recs = lb.recommend(WorkCellLane.research)
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_default_strategy_is_least_loaded(self, lb: LoadBalancer) -> None:
        """Default strategy should equal BalancingStrategy.least_loaded."""
        lb.register_agent("ag", [WorkCellLane.docs])
        recs_default = lb.recommend(WorkCellLane.docs)
        recs_explicit = lb.recommend(WorkCellLane.docs, BalancingStrategy.least_loaded)
        assert [r.agent_id for r in recs_default] == [r.agent_id for r in recs_explicit]

    def test_ties_broken_by_agent_id(self, lb: LoadBalancer) -> None:
        """When two agents have identical scores, they should be sorted by agent_id."""
        lb.register_agent("zzz", [WorkCellLane.docs], max_concurrent=3)
        lb.register_agent("aaa", [WorkCellLane.docs], max_concurrent=3)

        # Both agents: same max_concurrent, 0 active, full budget, freshly registered
        recs = lb.recommend(WorkCellLane.docs)
        assert len(recs) == 2
        # aaa < zzz alphabetically → aaa should come first on tie
        assert recs[0].agent_id == "aaa"
        assert recs[1].agent_id == "zzz"

    def test_only_eligible_lane_agents_included(self, lb: LoadBalancer) -> None:
        """Agents not supporting the requested lane must not appear in results."""
        lb.register_agent("docs-agent", [WorkCellLane.docs])
        lb.register_agent("research-agent", [WorkCellLane.research])

        recs = lb.recommend(WorkCellLane.docs)
        agent_ids = [r.agent_id for r in recs]
        assert "docs-agent" in agent_ids
        assert "research-agent" not in agent_ids

    def test_recommendation_has_expected_fields(self, lb: LoadBalancer) -> None:
        """Each BalancingRecommendation should carry agent_id, lane, score, reasons."""
        lb.register_agent("ag", [WorkCellLane.api_simple])
        recs = lb.recommend(WorkCellLane.api_simple)

        rec = recs[0]
        assert rec.agent_id == "ag"
        assert rec.lane == WorkCellLane.api_simple
        assert 0.0 <= rec.score <= 1.0
        assert len(rec.reasons) == 5  # five components: capacity, budget, affinity, freshness, node


# =============================================================================
# recommend — affinity_first Strategy
# =============================================================================


class TestRecommendAffinityFirst:
    """Tests for recommend() with BalancingStrategy.affinity_first."""

    def test_affinity_first_returns_eligible_agents(self, lb: LoadBalancer) -> None:
        """affinity_first should still filter and return eligible agents."""
        lb.register_agent("ag", [WorkCellLane.refactor])
        recs = lb.recommend(WorkCellLane.refactor, BalancingStrategy.affinity_first)
        assert len(recs) == 1
        assert recs[0].agent_id == "ag"

    def test_affinity_first_empty_when_no_eligible(self, lb: LoadBalancer) -> None:
        """affinity_first returns [] when no agents support the lane."""
        lb.register_agent("ag", [WorkCellLane.docs])
        recs = lb.recommend(WorkCellLane.deployment, BalancingStrategy.affinity_first)
        assert recs == []

    def test_affinity_first_sorted_by_score_desc(self, lb: LoadBalancer) -> None:
        """affinity_first recommendations should be sorted by score descending."""
        lb.register_agent("low-budget", [WorkCellLane.refactor], max_concurrent=3)
        lb.register_agent("high-budget", [WorkCellLane.refactor], max_concurrent=3)

        lb.update_agent_load("low-budget", current_active=0, context_budget_pct=10.0)
        lb.update_agent_load("high-budget", current_active=0, context_budget_pct=90.0)

        recs = lb.recommend(WorkCellLane.refactor, BalancingStrategy.affinity_first)
        assert recs[0].agent_id == "high-budget"
        assert recs[1].agent_id == "low-budget"


# =============================================================================
# recommend — round_robin Strategy
# =============================================================================


class TestRecommendRoundRobin:
    """Tests for recommend() with BalancingStrategy.round_robin."""

    def test_round_robin_returns_all_eligible(self, lb: LoadBalancer) -> None:
        """round_robin should return all eligible agents (reordered)."""
        lb.register_agent("a1", [WorkCellLane.test_writing])
        lb.register_agent("a2", [WorkCellLane.test_writing])
        lb.register_agent("a3", [WorkCellLane.test_writing])

        recs = lb.recommend(WorkCellLane.test_writing, BalancingStrategy.round_robin)
        assert len(recs) == 3

    def test_round_robin_first_call_starts_at_pos_zero(self, lb: LoadBalancer) -> None:
        """First round-robin call should start at the first alphabetically sorted agent."""
        lb.register_agent("alpha", [WorkCellLane.docs])
        lb.register_agent("beta", [WorkCellLane.docs])
        lb.register_agent("gamma", [WorkCellLane.docs])

        recs = lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)
        # Sorted alphabetically: alpha, beta, gamma → first call starts at pos 0
        assert recs[0].agent_id == "alpha"

    def test_round_robin_advances_position_on_each_call(self, lb: LoadBalancer) -> None:
        """Successive calls should cycle through agents."""
        lb.register_agent("ag-a", [WorkCellLane.docs])
        lb.register_agent("ag-b", [WorkCellLane.docs])

        call1 = lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)
        call2 = lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)

        # First call: ag-a first; second call: ag-b first
        assert call1[0].agent_id != call2[0].agent_id

    def test_round_robin_wraps_around(self, lb: LoadBalancer) -> None:
        """After cycling through all agents, the counter should wrap back to the start."""
        lb.register_agent("x1", [WorkCellLane.research])
        lb.register_agent("x2", [WorkCellLane.research])

        c1 = lb.recommend(WorkCellLane.research, BalancingStrategy.round_robin)
        c2 = lb.recommend(WorkCellLane.research, BalancingStrategy.round_robin)
        c3 = lb.recommend(WorkCellLane.research, BalancingStrategy.round_robin)

        # With 2 agents: positions cycle 0 → 1 → 0 → 1...
        assert c1[0].agent_id == c3[0].agent_id

    def test_round_robin_counters_per_lane(self, lb: LoadBalancer) -> None:
        """Round-robin counters should be independent per lane."""
        lb.register_agent("m1", [WorkCellLane.docs, WorkCellLane.research])
        lb.register_agent("m2", [WorkCellLane.docs, WorkCellLane.research])

        # Advance docs lane counter twice
        lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)
        lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)

        # research lane counter should still be at position 0
        research_recs = lb.recommend(WorkCellLane.research, BalancingStrategy.round_robin)
        assert research_recs[0].agent_id == "m1"  # first alphabetically

    def test_round_robin_recommendations_have_scores(self, lb: LoadBalancer) -> None:
        """Even round-robin results should carry composite scores."""
        lb.register_agent("ag", [WorkCellLane.docs])
        recs = lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)
        assert 0.0 <= recs[0].score <= 1.0

    def test_round_robin_single_agent(self, lb: LoadBalancer) -> None:
        """Single-agent pool should always return that agent."""
        lb.register_agent("solo", [WorkCellLane.deployment])
        for _ in range(5):
            recs = lb.recommend(WorkCellLane.deployment, BalancingStrategy.round_robin)
            assert recs[0].agent_id == "solo"

    def test_round_robin_empty_when_no_eligible(self, lb: LoadBalancer) -> None:
        """round_robin returns [] when no agents support the lane."""
        lb.register_agent("ag", [WorkCellLane.docs])
        recs = lb.recommend(WorkCellLane.research, BalancingStrategy.round_robin)
        assert recs == []


# =============================================================================
# get_agent_stats Tests
# =============================================================================


class TestGetAgentStats:
    """Tests for LoadBalancer.get_agent_stats."""

    def test_empty_registry_returns_empty_dict(self, lb: LoadBalancer) -> None:
        """get_agent_stats on fresh balancer should return {}."""
        assert lb.get_agent_stats() == {}

    def test_stats_includes_all_agents(self, lb: LoadBalancer) -> None:
        """Stats snapshot should contain all registered agents."""
        lb.register_agent("ag1", [WorkCellLane.docs])
        lb.register_agent("ag2", [WorkCellLane.research])

        stats = lb.get_agent_stats()
        assert set(stats.keys()) == {"ag1", "ag2"}

    def test_stats_heartbeat_age_non_negative(self, lb: LoadBalancer) -> None:
        """Heartbeat age in stats should never be negative."""
        lb.register_agent("ag", [WorkCellLane.docs])
        stats = lb.get_agent_stats()
        assert stats["ag"].last_heartbeat_age_seconds >= 0.0

    def test_stats_heartbeat_age_after_delay(self, lb: LoadBalancer) -> None:
        """After faking an old heartbeat, age should reflect elapsed time."""
        lb.register_agent("ag", [WorkCellLane.docs])
        # Manually backdate the heartbeat by 120 seconds
        lb._heartbeat_times["ag"] = time.monotonic() - 120.0
        stats = lb.get_agent_stats()
        age = stats["ag"].last_heartbeat_age_seconds
        assert 119.0 <= age <= 125.0  # Allow 5 second tolerance

    def test_stats_returns_copy_not_live_reference(self, lb: LoadBalancer) -> None:
        """Modifying the returned dict should not affect internal state."""
        lb.register_agent("ag", [WorkCellLane.docs])
        stats = lb.get_agent_stats()
        # Remove from the returned dict
        del stats["ag"]
        # Internal registry should be untouched
        assert "ag" in lb._agents

    def test_stats_preserves_supported_lanes(self, lb: LoadBalancer) -> None:
        """Stats snapshot should carry the correct supported_lanes."""
        lb.register_agent("ag", [WorkCellLane.security_change, WorkCellLane.docs])
        stats = lb.get_agent_stats()
        lanes = stats["ag"].supported_lanes
        assert WorkCellLane.security_change in lanes
        assert WorkCellLane.docs in lanes

    def test_stats_after_load_update(self, lb: LoadBalancer) -> None:
        """Stats should reflect the most recently applied load update."""
        lb.register_agent("ag", [WorkCellLane.docs])
        lb.update_agent_load("ag", current_active=2, context_budget_pct=65.0)
        stats = lb.get_agent_stats()
        assert stats["ag"].current_active == 2
        assert stats["ag"].context_budget_remaining_pct == pytest.approx(65.0)


# =============================================================================
# Singleton Factory Tests
# =============================================================================


class TestSingletonFactory:
    """Tests for get_load_balancer and reset_load_balancer."""

    def test_get_load_balancer_returns_load_balancer_instance(self) -> None:
        """get_load_balancer() should return a LoadBalancer."""
        lb = get_load_balancer()
        assert isinstance(lb, LoadBalancer)

    def test_get_load_balancer_returns_same_instance_on_repeat_calls(self) -> None:
        """Repeated calls to get_load_balancer() must return the same object."""
        lb1 = get_load_balancer()
        lb2 = get_load_balancer()
        assert lb1 is lb2

    def test_reset_clears_singleton(self) -> None:
        """After reset_load_balancer(), get_load_balancer() returns a new object."""
        lb1 = get_load_balancer()
        reset_load_balancer()
        lb2 = get_load_balancer()
        assert lb1 is not lb2

    def test_get_load_balancer_after_reset_starts_fresh(self) -> None:
        """New singleton after reset should have an empty agent registry."""
        lb1 = get_load_balancer()
        lb1.register_agent("persist-agent", [WorkCellLane.docs])

        reset_load_balancer()
        lb2 = get_load_balancer()

        assert "persist-agent" not in lb2.get_agent_stats()

    def test_get_load_balancer_is_thread_safe(self) -> None:
        """Concurrent calls to get_load_balancer() should return the same instance."""
        instances: list[LoadBalancer] = []
        errors: list[Exception] = []

        def grab() -> None:
            try:
                instances.append(get_load_balancer())
            except Exception as exc:
                errors.append(exc)

        threads = [Thread(target=grab) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(instances) == 10
        # All instances should be the same object
        first = instances[0]
        assert all(inst is first for inst in instances)

    def test_reset_load_balancer_is_idempotent(self) -> None:
        """Calling reset_load_balancer() multiple times should not raise."""
        reset_load_balancer()
        reset_load_balancer()
        reset_load_balancer()  # Should not raise


# =============================================================================
# Edge Cases and Advanced Scenarios
# =============================================================================


class TestEdgeCases:
    """Edge case tests for unusual but valid inputs."""

    def test_recommend_with_no_agents_registered(self, lb: LoadBalancer) -> None:
        """recommend on an empty registry should return []."""
        recs = lb.recommend(WorkCellLane.docs)
        assert recs == []

    def test_recommend_with_all_agents_supporting_different_lane(
        self, lb: LoadBalancer
    ) -> None:
        """If all agents support a different lane, recommend returns []."""
        lb.register_agent("a1", [WorkCellLane.docs])
        lb.register_agent("a2", [WorkCellLane.docs])
        recs = lb.recommend(WorkCellLane.security_change)
        assert recs == []

    def test_all_three_strategies_return_results_for_single_agent(
        self, lb: LoadBalancer
    ) -> None:
        """All strategies should return recommendations for a single agent pool."""
        lb.register_agent("solo", [WorkCellLane.refactor])
        for strategy in BalancingStrategy:
            recs = lb.recommend(WorkCellLane.refactor, strategy)
            assert len(recs) == 1
            assert recs[0].agent_id == "solo"

    def test_multi_lane_agent_is_recommended_for_each_supported_lane(
        self, lb: LoadBalancer
    ) -> None:
        """An agent supporting multiple lanes should appear in each lane's recommendations."""
        lb.register_agent(
            "versatile",
            [WorkCellLane.docs, WorkCellLane.test_writing, WorkCellLane.research],
        )
        for lane in [WorkCellLane.docs, WorkCellLane.test_writing, WorkCellLane.research]:
            recs = lb.recommend(lane)
            assert any(r.agent_id == "versatile" for r in recs)

    def test_max_concurrent_one_saturated_agent_still_scores(
        self, lb: LoadBalancer
    ) -> None:
        """An agent with max_concurrent=1 that is fully loaded should still appear."""
        lb.register_agent("tight", [WorkCellLane.deployment], max_concurrent=1)
        lb.update_agent_load("tight", current_active=1, context_budget_pct=80.0)
        recs = lb.recommend(WorkCellLane.deployment)
        assert len(recs) == 1
        # capacity score = 0.0, but affinity and freshness still contribute
        assert recs[0].score > 0.0

    def test_score_weights_sum_to_one(self) -> None:
        """Confirm the documented weights sum to exactly 1.0."""
        total = _W_CAPACITY + _W_BUDGET + _W_AFFINITY + _W_FRESHNESS + _W_NODE
        assert total == pytest.approx(1.0)

    def test_register_agent_max_concurrent_one(self, lb: LoadBalancer) -> None:
        """max_concurrent=1 should be accepted as a valid minimum."""
        cap = lb.register_agent("tight", [WorkCellLane.docs], max_concurrent=1)
        assert cap.max_concurrent == 1

    def test_large_number_of_agents(self, lb: LoadBalancer) -> None:
        """LoadBalancer should handle a larger agent pool without errors."""
        for i in range(50):
            lb.register_agent(f"agent-{i:03d}", [WorkCellLane.docs])
        recs = lb.recommend(WorkCellLane.docs)
        assert len(recs) == 50

    def test_recommend_all_lanes(self, lb: LoadBalancer) -> None:
        """register an agent for all lanes and verify recommend works for each."""
        all_lanes = list(WorkCellLane)
        lb.register_agent("omni", all_lanes)
        for lane in all_lanes:
            recs = lb.recommend(lane)
            assert len(recs) == 1
            assert recs[0].agent_id == "omni"


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Structural thread-safety tests for concurrent registration and updates."""

    def test_concurrent_registrations_do_not_corrupt_registry(
        self, lb: LoadBalancer
    ) -> None:
        """Concurrent register_agent calls should not corrupt the agent registry."""
        errors: list[Exception] = []

        def register(agent_id: str) -> None:
            try:
                lb.register_agent(agent_id, [WorkCellLane.docs])
            except Exception as exc:
                errors.append(exc)

        threads = [Thread(target=register, args=(f"concurrent-{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = lb.get_agent_stats()
        assert len(stats) == 20

    def test_concurrent_updates_do_not_raise(self, lb: LoadBalancer) -> None:
        """Concurrent update_agent_load calls should not raise."""
        lb.register_agent("shared", [WorkCellLane.docs])
        errors: list[Exception] = []

        def update(active: int) -> None:
            try:
                lb.update_agent_load("shared", current_active=active, context_budget_pct=50.0)
            except Exception as exc:
                errors.append(exc)

        threads = [Thread(target=update, args=(i % 3,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_concurrent_recommend_calls_do_not_raise(self, lb: LoadBalancer) -> None:
        """Concurrent recommend calls should return consistently without errors."""
        lb.register_agent("ag1", [WorkCellLane.research])
        lb.register_agent("ag2", [WorkCellLane.research])
        errors: list[Exception] = []
        results: list[list[BalancingRecommendation]] = []

        def recommend() -> None:
            try:
                results.append(lb.recommend(WorkCellLane.research))
            except Exception as exc:
                errors.append(exc)

        threads = [Thread(target=recommend) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 20
        for recs in results:
            assert len(recs) == 2


# =============================================================================
# Integration Tests — full workflow
# =============================================================================


class TestFullWorkflow:
    """End-to-end workflow tests combining multiple operations."""

    def test_register_update_and_recommend_workflow(self, lb: LoadBalancer) -> None:
        """Full workflow: register, update load, then recommend."""
        lb.register_agent("forge:nova", [WorkCellLane.api_simple, WorkCellLane.docs])
        lb.register_agent("forge:sati", [WorkCellLane.api_simple, WorkCellLane.research])

        lb.update_agent_load("forge:nova", current_active=1, context_budget_pct=88.5)
        lb.update_agent_load("forge:sati", current_active=0, context_budget_pct=95.0)

        recs = lb.recommend(WorkCellLane.api_simple)

        assert len(recs) == 2
        # sati has more headroom → should be first
        assert recs[0].agent_id == "forge:sati"
        assert recs[1].agent_id == "forge:nova"

    def test_singleton_shared_state_across_callers(self) -> None:
        """Agents registered through get_load_balancer() persist across calls."""
        lb1 = get_load_balancer()
        lb1.register_agent("global-agent", [WorkCellLane.docs])

        lb2 = get_load_balancer()
        stats = lb2.get_agent_stats()
        assert "global-agent" in stats

    def test_rebalancing_after_load_change(self, lb: LoadBalancer) -> None:
        """Changing an agent's load should update recommendation order."""
        lb.register_agent("a", [WorkCellLane.docs], max_concurrent=3)
        lb.register_agent("b", [WorkCellLane.docs], max_concurrent=3)

        lb.update_agent_load("a", current_active=0, context_budget_pct=100.0)
        lb.update_agent_load("b", current_active=3, context_budget_pct=100.0)

        # Initially 'a' should be first
        recs1 = lb.recommend(WorkCellLane.docs)
        assert recs1[0].agent_id == "a"

        # Now saturate 'a' and free 'b'
        lb.update_agent_load("a", current_active=3, context_budget_pct=100.0)
        lb.update_agent_load("b", current_active=0, context_budget_pct=100.0)

        recs2 = lb.recommend(WorkCellLane.docs)
        assert recs2[0].agent_id == "b"

    def test_round_robin_cycle_across_multiple_lanes(self, lb: LoadBalancer) -> None:
        """Round-robin state per lane should be independent across two lanes."""
        lb.register_agent("worker1", [WorkCellLane.docs, WorkCellLane.research])
        lb.register_agent("worker2", [WorkCellLane.docs, WorkCellLane.research])

        # Advance docs RR twice — research should be unaffected
        lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)
        lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)

        # Research should still start from position 0
        research_first = lb.recommend(WorkCellLane.research, BalancingStrategy.round_robin)
        docs_third = lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)

        # research counter = 0 (first call), docs counter = 2 → should differ if 2 agents
        # (with 2 agents: 0,1,0,1... so docs 3rd call is at pos 0 → same as research 1st call)
        # Both should return valid recommendations
        assert len(research_first) == 2
        assert len(docs_third) == 2

    def test_stale_agent_lower_score_than_fresh(self, lb: LoadBalancer) -> None:
        """An agent with a stale heartbeat should score lower than a fresh one."""
        lb.register_agent("fresh", [WorkCellLane.docs], max_concurrent=3)
        lb.register_agent("stale", [WorkCellLane.docs], max_concurrent=3)

        # Both idle and full budget
        lb.update_agent_load("fresh", current_active=0, context_budget_pct=100.0)
        lb.update_agent_load("stale", current_active=0, context_budget_pct=100.0)

        # Backdate stale agent heartbeat well beyond the zero threshold
        lb._heartbeat_times["stale"] = time.monotonic() - (_FRESHNESS_ZERO_SECONDS + 100.0)

        recs = lb.recommend(WorkCellLane.docs)
        fresh_rec = next(r for r in recs if r.agent_id == "fresh")
        stale_rec = next(r for r in recs if r.agent_id == "stale")

        assert fresh_rec.score > stale_rec.score
        assert recs[0].agent_id == "fresh"

    def test_use_case_from_module_docstring(self) -> None:
        """Reproduce the exact usage example from the module docstring."""
        lb_doc = get_load_balancer()
        lb_doc.register_agent("forge:nova", [WorkCellLane.api_simple, WorkCellLane.docs])
        lb_doc.update_agent_load("forge:nova", current_active=1, context_budget_pct=88.5)

        recs = lb_doc.recommend(WorkCellLane.api_simple)
        best = recs[0]

        assert best.agent_id == "forge:nova"
        assert best.lane == WorkCellLane.api_simple
        assert 0.0 <= best.score <= 1.0
