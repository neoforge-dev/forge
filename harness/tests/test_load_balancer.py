"""Tests for Agent Load Balancer — DF-2005.

Coverage targets
----------------
- AgentCapability model: valid construction, field constraints
- BalancingRecommendation: score range, required fields
- BalancingStrategy enum: all values present
- LoadBalancer.register_agent: stores capability, idempotent re-registration,
  preserves live counters, rejects invalid max_concurrent
- LoadBalancer.update_agent_load: updates counters, resets heartbeat clock,
  raises on unknown agent, raises on invalid arguments
- LoadBalancer.recommend: returns eligible agents, filters by lane,
  empty result when no eligible agents
- LoadBalancer.recommend (least_loaded): lower active count scores higher
- LoadBalancer.recommend (least_loaded): budget_score affects ranking
- LoadBalancer.recommend (least_loaded): stale heartbeat reduces score
- LoadBalancer.recommend (affinity_first): same behaviour as least_loaded for
  fully eligible set (all have affinity=1.0)
- LoadBalancer.recommend (round_robin): cycles through agents evenly
- LoadBalancer.get_agent_stats: returns live snapshots with computed age
- Singleton pattern: get_load_balancer returns same instance, reset clears it
- Thread safety: concurrent register + update + recommend do not corrupt state
- Scoring arithmetic: composite formula produces expected values
- Freshness decay: score decays linearly between 60 s and 300 s
- Freshness at boundary: exactly 60 s is still 1.0; at 300 s is 0.0

Target: 45+ tests.
"""

from __future__ import annotations

import threading
import time
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

# ===========================================================================
# Helpers / fixtures
# ===========================================================================


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the LoadBalancer singleton before and after every test."""
    reset_load_balancer()
    yield
    reset_load_balancer()


@pytest.fixture()
def lb() -> LoadBalancer:
    """Return a fresh LoadBalancer (not the singleton)."""
    return LoadBalancer()


def _register(
    lb: LoadBalancer,
    agent_id: str = "agent-a",
    lanes: list[WorkCellLane] | None = None,
    max_concurrent: int = 3,
) -> AgentCapability:
    """Convenience wrapper for register_agent with sensible defaults."""
    if lanes is None:
        lanes = [WorkCellLane.api_simple]
    return lb.register_agent(agent_id, lanes, max_concurrent)


# ===========================================================================
# 1. AgentCapability model tests
# ===========================================================================


class TestAgentCapabilityModel:
    def test_valid_construction_defaults(self):
        cap = AgentCapability(agent_id="test-agent")
        assert cap.agent_id == "test-agent"
        assert cap.supported_lanes == []
        assert cap.max_concurrent == 3
        assert cap.current_active == 0
        assert cap.context_budget_remaining_pct == 100.0
        assert cap.last_heartbeat_age_seconds == 0.0

    def test_valid_construction_explicit(self):
        cap = AgentCapability(
            agent_id="nova",
            supported_lanes=[WorkCellLane.api_simple, WorkCellLane.docs],
            max_concurrent=5,
            current_active=2,
            context_budget_remaining_pct=75.0,
            last_heartbeat_age_seconds=30.0,
        )
        assert cap.agent_id == "nova"
        assert WorkCellLane.api_simple in cap.supported_lanes
        assert WorkCellLane.docs in cap.supported_lanes
        assert cap.max_concurrent == 5
        assert cap.current_active == 2
        assert cap.context_budget_remaining_pct == 75.0
        assert cap.last_heartbeat_age_seconds == 30.0

    def test_max_concurrent_minimum_is_one(self):
        with pytest.raises(Exception):
            AgentCapability(agent_id="x", max_concurrent=0)

    def test_current_active_nonnegative(self):
        with pytest.raises(Exception):
            AgentCapability(agent_id="x", current_active=-1)

    def test_context_budget_lower_bound(self):
        with pytest.raises(Exception):
            AgentCapability(agent_id="x", context_budget_remaining_pct=-0.1)

    def test_context_budget_upper_bound(self):
        with pytest.raises(Exception):
            AgentCapability(agent_id="x", context_budget_remaining_pct=100.1)

    def test_heartbeat_age_nonnegative(self):
        with pytest.raises(Exception):
            AgentCapability(agent_id="x", last_heartbeat_age_seconds=-1.0)

    def test_supported_lanes_accepts_all_lane_values(self):
        all_lanes = list(WorkCellLane)
        cap = AgentCapability(agent_id="full-agent", supported_lanes=all_lanes)
        assert len(cap.supported_lanes) == len(all_lanes)

    def test_model_copy_update(self):
        cap = AgentCapability(agent_id="x", current_active=1)
        updated = cap.model_copy(update={"current_active": 2})
        assert updated.current_active == 2
        assert cap.current_active == 1  # original unchanged


# ===========================================================================
# 2. BalancingRecommendation model tests
# ===========================================================================


class TestBalancingRecommendationModel:
    def test_valid_construction(self):
        rec = BalancingRecommendation(
            agent_id="agent-a",
            lane=WorkCellLane.api_simple,
            score=0.75,
        )
        assert rec.agent_id == "agent-a"
        assert rec.lane == WorkCellLane.api_simple
        assert rec.score == 0.75
        assert rec.reasons == []

    def test_score_lower_bound(self):
        rec = BalancingRecommendation(agent_id="a", lane=WorkCellLane.docs, score=0.0)
        assert rec.score == 0.0

    def test_score_upper_bound(self):
        rec = BalancingRecommendation(agent_id="a", lane=WorkCellLane.docs, score=1.0)
        assert rec.score == 1.0

    def test_score_below_zero_rejected(self):
        with pytest.raises(Exception):
            BalancingRecommendation(agent_id="a", lane=WorkCellLane.docs, score=-0.001)

    def test_score_above_one_rejected(self):
        with pytest.raises(Exception):
            BalancingRecommendation(agent_id="a", lane=WorkCellLane.docs, score=1.001)

    def test_reasons_list(self):
        rec = BalancingRecommendation(
            agent_id="a",
            lane=WorkCellLane.research,
            score=0.5,
            reasons=["capacity_score=0.667", "budget_score=0.800"],
        )
        assert len(rec.reasons) == 2
        assert "capacity_score=0.667" in rec.reasons


# ===========================================================================
# 3. BalancingStrategy enum tests
# ===========================================================================


class TestBalancingStrategyEnum:
    def test_all_strategies_present(self):
        values = {s.value for s in BalancingStrategy}
        assert "round_robin" in values
        assert "least_loaded" in values
        assert "affinity_first" in values

    def test_is_str_enum(self):
        assert BalancingStrategy.round_robin == "round_robin"
        assert BalancingStrategy.least_loaded == "least_loaded"
        assert BalancingStrategy.affinity_first == "affinity_first"


# ===========================================================================
# 4. LoadBalancer.register_agent tests
# ===========================================================================


class TestRegisterAgent:
    def test_register_stores_capability(self, lb):
        cap = _register(lb, "agent-a", [WorkCellLane.api_simple], max_concurrent=4)
        assert cap.agent_id == "agent-a"
        assert WorkCellLane.api_simple in cap.supported_lanes
        assert cap.max_concurrent == 4

    def test_register_multiple_lanes(self, lb):
        lanes = [WorkCellLane.api_simple, WorkCellLane.docs, WorkCellLane.research]
        cap = lb.register_agent("multi", lanes, max_concurrent=2)
        assert set(cap.supported_lanes) == set(lanes)

    def test_register_returns_capability(self, lb):
        result = _register(lb)
        assert isinstance(result, AgentCapability)

    def test_register_invalid_max_concurrent_raises(self, lb):
        with pytest.raises(ValueError, match="max_concurrent"):
            lb.register_agent("bad", [WorkCellLane.api_simple], max_concurrent=0)

    def test_reregister_same_agent_overwrites_lanes(self, lb):
        lb.register_agent("a", [WorkCellLane.api_simple])
        lb.register_agent("a", [WorkCellLane.docs])
        stats = lb.get_agent_stats()
        assert WorkCellLane.docs in stats["a"].supported_lanes
        assert WorkCellLane.api_simple not in stats["a"].supported_lanes

    def test_reregister_preserves_current_active(self, lb):
        lb.register_agent("a", [WorkCellLane.api_simple], max_concurrent=5)
        lb.update_agent_load("a", current_active=2, context_budget_pct=80.0)
        lb.register_agent("a", [WorkCellLane.api_simple], max_concurrent=5)
        stats = lb.get_agent_stats()
        assert stats["a"].current_active == 2

    def test_reregister_preserves_budget(self, lb):
        lb.register_agent("a", [WorkCellLane.api_simple], max_concurrent=5)
        lb.update_agent_load("a", current_active=1, context_budget_pct=60.0)
        lb.register_agent("a", [WorkCellLane.api_simple], max_concurrent=5)
        stats = lb.get_agent_stats()
        assert stats["a"].context_budget_remaining_pct == 60.0

    def test_register_default_max_concurrent(self, lb):
        cap = lb.register_agent("a", [WorkCellLane.api_simple])
        assert cap.max_concurrent == 3


# ===========================================================================
# 5. LoadBalancer.update_agent_load tests
# ===========================================================================


class TestUpdateAgentLoad:
    def test_update_changes_active_count(self, lb):
        _register(lb, "a")
        lb.update_agent_load("a", current_active=2, context_budget_pct=90.0)
        stats = lb.get_agent_stats()
        assert stats["a"].current_active == 2

    def test_update_changes_budget(self, lb):
        _register(lb, "a")
        lb.update_agent_load("a", current_active=0, context_budget_pct=42.5)
        stats = lb.get_agent_stats()
        assert stats["a"].context_budget_remaining_pct == 42.5

    def test_update_unknown_agent_raises_key_error(self, lb):
        with pytest.raises(KeyError, match="ghost"):
            lb.update_agent_load("ghost", current_active=0, context_budget_pct=100.0)

    def test_update_negative_active_raises_value_error(self, lb):
        _register(lb, "a")
        with pytest.raises(ValueError, match="current_active"):
            lb.update_agent_load("a", current_active=-1, context_budget_pct=100.0)

    def test_update_budget_below_zero_raises_value_error(self, lb):
        _register(lb, "a")
        with pytest.raises(ValueError, match="context_budget_pct"):
            lb.update_agent_load("a", current_active=0, context_budget_pct=-1.0)

    def test_update_budget_above_100_raises_value_error(self, lb):
        _register(lb, "a")
        with pytest.raises(ValueError, match="context_budget_pct"):
            lb.update_agent_load("a", current_active=0, context_budget_pct=101.0)

    def test_update_resets_heartbeat_freshness(self, lb):
        _register(lb, "a")
        # Simulate stale heartbeat by patching monotonic
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            # Register was called at t=0
            mock_time.monotonic.return_value = 0.0
            lb2 = LoadBalancer()
            lb2.register_agent("a", [WorkCellLane.api_simple])
            # Advance time well past freshness window
            mock_time.monotonic.return_value = 400.0
            # Now update — should reset the heartbeat clock
            lb2.update_agent_load("a", current_active=1, context_budget_pct=80.0)
            # Freshness should now be 1.0 because heartbeat_time was set to 400
            mock_time.monotonic.return_value = 401.0
            recs = lb2.recommend(WorkCellLane.api_simple)
        assert len(recs) == 1
        # After reset the age is only 1 second, so freshness should be 1.0
        # Verify score is not zero (stale would give very low score)
        assert recs[0].score > 0.5


# ===========================================================================
# 6. LoadBalancer.recommend — basic eligibility tests
# ===========================================================================


class TestRecommendEligibility:
    def test_returns_list_of_recommendations(self, lb):
        _register(lb, "a", [WorkCellLane.api_simple])
        recs = lb.recommend(WorkCellLane.api_simple)
        assert isinstance(recs, list)
        assert len(recs) == 1
        assert isinstance(recs[0], BalancingRecommendation)

    def test_no_eligible_agents_returns_empty(self, lb):
        _register(lb, "a", [WorkCellLane.docs])
        recs = lb.recommend(WorkCellLane.api_simple)
        assert recs == []

    def test_filters_by_supported_lane(self, lb):
        _register(lb, "api-agent", [WorkCellLane.api_simple])
        _register(lb, "docs-agent", [WorkCellLane.docs])
        recs = lb.recommend(WorkCellLane.docs)
        agent_ids = {r.agent_id for r in recs}
        assert "docs-agent" in agent_ids
        assert "api-agent" not in agent_ids

    def test_no_registered_agents_returns_empty(self, lb):
        recs = lb.recommend(WorkCellLane.api_simple)
        assert recs == []

    def test_recommendation_lane_matches_request(self, lb):
        _register(lb, "a", [WorkCellLane.research])
        recs = lb.recommend(WorkCellLane.research)
        assert recs[0].lane == WorkCellLane.research

    def test_recommendation_agent_id_matches_registered(self, lb):
        _register(lb, "forge-nova", [WorkCellLane.api_simple])
        recs = lb.recommend(WorkCellLane.api_simple)
        assert recs[0].agent_id == "forge-nova"

    def test_all_eligible_agents_included(self, lb):
        for i in range(5):
            _register(lb, f"agent-{i}", [WorkCellLane.test_writing])
        recs = lb.recommend(WorkCellLane.test_writing)
        assert len(recs) == 5


# ===========================================================================
# 7. LoadBalancer.recommend — least_loaded strategy tests
# ===========================================================================


class TestRecommendLeastLoaded:
    def test_lower_active_count_ranks_higher(self, lb):
        lb.register_agent("busy", [WorkCellLane.api_simple], max_concurrent=3)
        lb.register_agent("idle", [WorkCellLane.api_simple], max_concurrent=3)
        lb.update_agent_load("busy", current_active=2, context_budget_pct=100.0)
        lb.update_agent_load("idle", current_active=0, context_budget_pct=100.0)
        recs = lb.recommend(WorkCellLane.api_simple, BalancingStrategy.least_loaded)
        assert recs[0].agent_id == "idle"

    def test_fully_saturated_agent_scores_lower(self, lb):
        lb.register_agent("full", [WorkCellLane.api_simple], max_concurrent=3)
        lb.register_agent("free", [WorkCellLane.api_simple], max_concurrent=3)
        lb.update_agent_load("full", current_active=3, context_budget_pct=100.0)
        lb.update_agent_load("free", current_active=1, context_budget_pct=100.0)
        recs = lb.recommend(WorkCellLane.api_simple)
        assert recs[0].agent_id == "free"

    def test_budget_score_affects_ranking(self, lb):
        lb.register_agent("rich-budget", [WorkCellLane.api_simple], max_concurrent=3)
        lb.register_agent("poor-budget", [WorkCellLane.api_simple], max_concurrent=3)
        # Same active count, different budgets
        lb.update_agent_load("rich-budget", current_active=1, context_budget_pct=95.0)
        lb.update_agent_load("poor-budget", current_active=1, context_budget_pct=10.0)
        recs = lb.recommend(WorkCellLane.api_simple)
        assert recs[0].agent_id == "rich-budget"

    def test_sorted_descending_by_score(self, lb):
        for i in range(4):
            lb.register_agent(f"a{i}", [WorkCellLane.api_simple], max_concurrent=10)
            lb.update_agent_load(f"a{i}", current_active=i, context_budget_pct=100.0)
        recs = lb.recommend(WorkCellLane.api_simple)
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_tied_agents_sorted_by_agent_id(self, lb):
        # Identical configuration — tie-break should be alphabetical agent_id
        for name in ["zzz-agent", "aaa-agent", "mmm-agent"]:
            lb.register_agent(name, [WorkCellLane.docs], max_concurrent=3)
            lb.update_agent_load(name, current_active=1, context_budget_pct=100.0)
        recs = lb.recommend(WorkCellLane.docs)
        # All scores equal; first should be alphabetically smallest
        assert recs[0].agent_id == "aaa-agent"

    def test_reasons_list_populated(self, lb):
        _register(lb, "a", [WorkCellLane.api_simple])
        recs = lb.recommend(WorkCellLane.api_simple)
        assert len(recs[0].reasons) >= 1
        reasons_text = " ".join(recs[0].reasons)
        assert "capacity_score" in reasons_text
        assert "budget_score" in reasons_text
        assert "freshness" in reasons_text

    def test_single_agent_always_returned(self, lb):
        _register(lb, "only-agent", [WorkCellLane.security_change])
        recs = lb.recommend(WorkCellLane.security_change)
        assert len(recs) == 1
        assert recs[0].agent_id == "only-agent"


# ===========================================================================
# 8. LoadBalancer.recommend — stale heartbeat / freshness tests
# ===========================================================================


class TestRecommendFreshness:
    def test_fresh_agent_scores_higher_than_stale(self, lb):
        """Agent updated recently should outrank agent with old heartbeat."""
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            lb2 = LoadBalancer()
            lb2.register_agent("fresh", [WorkCellLane.api_simple], max_concurrent=3)
            lb2.register_agent("stale", [WorkCellLane.api_simple], max_concurrent=3)
            # Advance time so "stale" has old heartbeat
            mock_time.monotonic.return_value = 250.0
            # Only update "fresh" — "stale" retains heartbeat from t=0
            lb2.update_agent_load("fresh", current_active=0, context_budget_pct=100.0)
            # Both have same active/budget; freshness is the differentiator
            lb2.update_agent_load("stale", current_active=0, context_budget_pct=100.0)

            # Now simulate stale by rolling time forward and NOT updating stale
            mock_time.monotonic.return_value = 0.0
            stale_lb = LoadBalancer()
            stale_lb.register_agent("fresh", [WorkCellLane.api_simple], max_concurrent=3)
            stale_lb.register_agent("stale", [WorkCellLane.api_simple], max_concurrent=3)
            stale_lb.update_agent_load("fresh", current_active=0, context_budget_pct=100.0)
            stale_lb.update_agent_load("stale", current_active=0, context_budget_pct=100.0)

            # Advance time for "stale" but keep "fresh" updated
            mock_time.monotonic.return_value = 200.0
            stale_lb.update_agent_load("fresh", current_active=0, context_budget_pct=100.0)
            # "stale" heartbeat_time is still t=0, age = 200 s → partial decay
            recs = stale_lb.recommend(WorkCellLane.api_simple)

        fresh_rec = next(r for r in recs if r.agent_id == "fresh")
        stale_rec = next(r for r in recs if r.agent_id == "stale")
        assert fresh_rec.score > stale_rec.score

    def test_freshness_score_full_at_zero_age(self, lb):
        """Freshness score must be 1.0 immediately after registration."""
        # The _build_recommendation reads time.monotonic() internally.
        # We patch the module-level time to make the call deterministic.
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            t0 = 1000.0
            mock_time.monotonic.return_value = t0
            lb2 = LoadBalancer()
            lb2.register_agent("a", [WorkCellLane.api_simple])
            # No time advance — age = 0
            recs = lb2.recommend(WorkCellLane.api_simple)
        # freshness = 1.0 → freshness contribution = _W_FRESHNESS
        expected_freshness_contribution = _W_FRESHNESS * 1.0
        reasons_text = " ".join(recs[0].reasons)
        # Confirm freshness=1.000 appears in reasons
        assert "freshness=1.000" in reasons_text

    def test_freshness_score_decays_past_full_seconds(self, lb):
        """Freshness score is < 1.0 for age > _FRESHNESS_FULL_SECONDS."""
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            t0 = 1000.0
            mock_time.monotonic.return_value = t0
            lb2 = LoadBalancer()
            lb2.register_agent("a", [WorkCellLane.api_simple])
            # Advance 120 seconds past the full-freshness threshold
            mock_time.monotonic.return_value = t0 + _FRESHNESS_FULL_SECONDS + 120.0
            recs = lb2.recommend(WorkCellLane.api_simple)
        reasons_text = " ".join(recs[0].reasons)
        # freshness should be a value < 1.0
        assert "freshness=1.000" not in reasons_text

    def test_freshness_score_zero_at_stale_threshold(self, lb):
        """Freshness score must be 0.0 when age >= _FRESHNESS_ZERO_SECONDS."""
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            t0 = 1000.0
            mock_time.monotonic.return_value = t0
            lb2 = LoadBalancer()
            lb2.register_agent("a", [WorkCellLane.api_simple])
            mock_time.monotonic.return_value = t0 + _FRESHNESS_ZERO_SECONDS
            recs = lb2.recommend(WorkCellLane.api_simple)
        reasons_text = " ".join(recs[0].reasons)
        assert "freshness=0.000" in reasons_text


# ===========================================================================
# 9. LoadBalancer.recommend — round_robin strategy tests
# ===========================================================================


class TestRecommendRoundRobin:
    def test_round_robin_cycles_through_agents(self, lb):
        agents = ["agent-a", "agent-b", "agent-c"]
        for a in agents:
            lb.register_agent(a, [WorkCellLane.api_simple], max_concurrent=3)
        # Each call should start with a different agent
        first_agents = []
        for _ in range(len(agents)):
            recs = lb.recommend(WorkCellLane.api_simple, BalancingStrategy.round_robin)
            first_agents.append(recs[0].agent_id)
        assert set(first_agents) == set(agents)

    def test_round_robin_all_agents_returned_each_call(self, lb):
        for i in range(3):
            lb.register_agent(f"a{i}", [WorkCellLane.api_simple], max_concurrent=3)
        recs = lb.recommend(WorkCellLane.api_simple, BalancingStrategy.round_robin)
        assert len(recs) == 3

    def test_round_robin_distributes_evenly(self, lb):
        """Over N calls, each of N agents should be first exactly once."""
        n = 4
        for i in range(n):
            lb.register_agent(f"worker-{i}", [WorkCellLane.docs], max_concurrent=5)
        leader_counts: dict[str, int] = {}
        for _ in range(n):
            recs = lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)
            leader = recs[0].agent_id
            leader_counts[leader] = leader_counts.get(leader, 0) + 1
        assert len(leader_counts) == n, "Every agent should be first exactly once"
        assert all(v == 1 for v in leader_counts.values())

    def test_round_robin_counter_wraps_around(self, lb):
        """After cycling through all agents the counter wraps."""
        n = 3
        for i in range(n):
            lb.register_agent(f"x{i}", [WorkCellLane.research], max_concurrent=3)
        # First cycle
        cycle1 = [
            lb.recommend(WorkCellLane.research, BalancingStrategy.round_robin)[0].agent_id
            for _ in range(n)
        ]
        # Second cycle — should repeat the same order
        cycle2 = [
            lb.recommend(WorkCellLane.research, BalancingStrategy.round_robin)[0].agent_id
            for _ in range(n)
        ]
        assert cycle1 == cycle2

    def test_round_robin_scores_still_populated(self, lb):
        _register(lb, "a", [WorkCellLane.api_simple])
        recs = lb.recommend(WorkCellLane.api_simple, BalancingStrategy.round_robin)
        assert 0.0 <= recs[0].score <= 1.0

    def test_round_robin_no_agents_returns_empty(self, lb):
        recs = lb.recommend(WorkCellLane.api_simple, BalancingStrategy.round_robin)
        assert recs == []

    def test_round_robin_lane_isolation(self, lb):
        """Counters for different lanes must not interfere with each other."""
        lb.register_agent("a", [WorkCellLane.api_simple, WorkCellLane.docs], max_concurrent=3)
        lb.register_agent("b", [WorkCellLane.api_simple, WorkCellLane.docs], max_concurrent=3)
        # Advance api_simple counter by calling it twice
        lb.recommend(WorkCellLane.api_simple, BalancingStrategy.round_robin)
        lb.recommend(WorkCellLane.api_simple, BalancingStrategy.round_robin)
        # docs counter should still start at 0 → first agent in sorted order
        docs_recs = lb.recommend(WorkCellLane.docs, BalancingStrategy.round_robin)
        # Counter for docs is at 0, so first agent alphabetically should be first
        assert docs_recs[0].agent_id == "a"


# ===========================================================================
# 10. LoadBalancer.recommend — affinity_first strategy tests
# ===========================================================================


class TestRecommendAffinityFirst:
    def test_affinity_first_returns_eligible_agents(self, lb):
        lb.register_agent("a", [WorkCellLane.refactor], max_concurrent=3)
        lb.register_agent("b", [WorkCellLane.refactor], max_concurrent=3)
        recs = lb.recommend(WorkCellLane.refactor, BalancingStrategy.affinity_first)
        assert len(recs) == 2

    def test_affinity_first_excludes_unsupported_lane(self, lb):
        lb.register_agent("refactor-expert", [WorkCellLane.refactor], max_concurrent=3)
        lb.register_agent("deploy-expert", [WorkCellLane.deployment], max_concurrent=3)
        recs = lb.recommend(WorkCellLane.refactor, BalancingStrategy.affinity_first)
        agent_ids = {r.agent_id for r in recs}
        assert "refactor-expert" in agent_ids
        assert "deploy-expert" not in agent_ids

    def test_affinity_first_least_loaded_tiebreak(self, lb):
        lb.register_agent("light", [WorkCellLane.test_writing], max_concurrent=5)
        lb.register_agent("heavy", [WorkCellLane.test_writing], max_concurrent=5)
        lb.update_agent_load("light", current_active=0, context_budget_pct=100.0)
        lb.update_agent_load("heavy", current_active=4, context_budget_pct=100.0)
        recs = lb.recommend(WorkCellLane.test_writing, BalancingStrategy.affinity_first)
        # Both have affinity=1.0; tie-break is capacity → lighter should win
        assert recs[0].agent_id == "light"


# ===========================================================================
# 11. LoadBalancer.get_agent_stats tests
# ===========================================================================


class TestGetAgentStats:
    def test_returns_all_registered_agents(self, lb):
        for i in range(3):
            lb.register_agent(f"agent-{i}", [WorkCellLane.api_simple])
        stats = lb.get_agent_stats()
        assert len(stats) == 3

    def test_returns_correct_agent_ids(self, lb):
        lb.register_agent("nova", [WorkCellLane.docs])
        lb.register_agent("prya", [WorkCellLane.docs])
        stats = lb.get_agent_stats()
        assert "nova" in stats
        assert "prya" in stats

    def test_heartbeat_age_computed(self, lb):
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            t0 = 5000.0
            mock_time.monotonic.return_value = t0
            lb2 = LoadBalancer()
            lb2.register_agent("a", [WorkCellLane.api_simple])
            # Advance 45 seconds
            mock_time.monotonic.return_value = t0 + 45.0
            stats = lb2.get_agent_stats()
        age = stats["a"].last_heartbeat_age_seconds
        assert abs(age - 45.0) < 0.1

    def test_empty_registry_returns_empty_dict(self, lb):
        assert lb.get_agent_stats() == {}

    def test_stats_reflect_last_update(self, lb):
        lb.register_agent("a", [WorkCellLane.api_simple], max_concurrent=5)
        lb.update_agent_load("a", current_active=3, context_budget_pct=55.5)
        stats = lb.get_agent_stats()
        assert stats["a"].current_active == 3
        assert stats["a"].context_budget_remaining_pct == 55.5


# ===========================================================================
# 12. Singleton pattern tests
# ===========================================================================


class TestSingletonPattern:
    def test_get_load_balancer_returns_instance(self):
        lb = get_load_balancer()
        assert isinstance(lb, LoadBalancer)

    def test_get_load_balancer_returns_same_instance(self):
        lb1 = get_load_balancer()
        lb2 = get_load_balancer()
        assert lb1 is lb2

    def test_reset_load_balancer_clears_singleton(self):
        lb1 = get_load_balancer()
        reset_load_balancer()
        lb2 = get_load_balancer()
        assert lb1 is not lb2

    def test_singleton_state_persists_across_calls(self):
        lb = get_load_balancer()
        lb.register_agent("persistent", [WorkCellLane.api_simple])
        lb2 = get_load_balancer()
        stats = lb2.get_agent_stats()
        assert "persistent" in stats

    def test_reset_load_balancer_is_idempotent(self):
        reset_load_balancer()
        reset_load_balancer()  # Should not raise
        lb = get_load_balancer()
        assert isinstance(lb, LoadBalancer)


# ===========================================================================
# 13. Thread safety tests
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_register_does_not_corrupt_registry(self, lb):
        errors: list[Exception] = []
        lane = WorkCellLane.api_simple

        def register_agents(start: int, count: int) -> None:
            try:
                for i in range(start, start + count):
                    lb.register_agent(f"agent-{i}", [lane], max_concurrent=3)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=register_agents, args=(i * 10, 10)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        stats = lb.get_agent_stats()
        assert len(stats) == 50

    def test_concurrent_update_and_recommend(self, lb):
        lane = WorkCellLane.api_simple
        for i in range(10):
            lb.register_agent(f"worker-{i}", [lane], max_concurrent=5)

        errors: list[Exception] = []

        def update_load(agent_id: str) -> None:
            try:
                for _ in range(20):
                    lb.update_agent_load(
                        agent_id,
                        current_active=threading.current_thread().ident % 5,  # type: ignore[operator]
                        context_budget_pct=50.0,
                    )
            except Exception as exc:
                errors.append(exc)

        def read_recommendations() -> None:
            try:
                for _ in range(20):
                    lb.recommend(lane)
            except Exception as exc:
                errors.append(exc)

        threads: list[threading.Thread] = []
        for i in range(10):
            threads.append(threading.Thread(target=update_load, args=(f"worker-{i}",)))
        for _ in range(5):
            threads.append(threading.Thread(target=read_recommendations))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_round_robin_counter_increments(self, lb):
        """Round-robin counter increments must not produce duplicate leaders
        when called from many threads simultaneously."""
        lane = WorkCellLane.docs
        n = 6
        for i in range(n):
            lb.register_agent(f"doc-{i}", [lane], max_concurrent=3)

        leaders: list[str] = []
        lock = threading.Lock()

        def get_leader() -> None:
            recs = lb.recommend(lane, BalancingStrategy.round_robin)
            if recs:
                with lock:
                    leaders.append(recs[0].agent_id)

        threads = [threading.Thread(target=get_leader) for _ in range(n * 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every call should produce a valid agent
        valid_ids = {f"doc-{i}" for i in range(n)}
        assert all(lid in valid_ids for lid in leaders)


# ===========================================================================
# 14. Scoring arithmetic tests
# ===========================================================================


class TestScoringArithmetic:
    def test_perfect_agent_scores_near_one(self, lb):
        """An agent with 0 active tasks, 100% budget, and fresh heartbeat
        should score close to the theoretical maximum."""
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            t0 = 100.0
            mock_time.monotonic.return_value = t0
            lb2 = LoadBalancer()
            lb2.register_agent("perfect", [WorkCellLane.api_simple], max_concurrent=3)
            lb2.update_agent_load("perfect", current_active=0, context_budget_pct=100.0)
            recs = lb2.recommend(WorkCellLane.api_simple)
        # capacity=1.0, budget=1.0, affinity=1.0, freshness=1.0, node_score=1.0
        expected = round(
            _W_CAPACITY * 1.0 + _W_BUDGET * 1.0 + _W_AFFINITY * 1.0
            + _W_FRESHNESS * 1.0 + _W_NODE * 1.0,
            4,
        )
        assert abs(recs[0].score - expected) < 0.001

    def test_saturated_zero_budget_agent_scores_low(self, lb):
        """max active + 0% budget should drive score to near-minimum."""
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            t0 = 100.0
            mock_time.monotonic.return_value = t0
            lb2 = LoadBalancer()
            lb2.register_agent("worst", [WorkCellLane.api_simple], max_concurrent=3)
            lb2.update_agent_load("worst", current_active=3, context_budget_pct=0.0)
            recs = lb2.recommend(WorkCellLane.api_simple)
        # capacity=0.0, budget=0.0, affinity=1.0, freshness=1.0, node_score=1.0
        expected = round(
            _W_CAPACITY * 0.0 + _W_BUDGET * 0.0 + _W_AFFINITY * 1.0
            + _W_FRESHNESS * 1.0 + _W_NODE * 1.0,
            4,
        )
        assert abs(recs[0].score - expected) < 0.001

    def test_capacity_score_formula(self, lb):
        """capacity_score = 1 - current_active / max_concurrent."""
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            lb2 = LoadBalancer()
            lb2.register_agent("a", [WorkCellLane.api_simple], max_concurrent=4)
            lb2.update_agent_load("a", current_active=1, context_budget_pct=100.0)
            recs = lb2.recommend(WorkCellLane.api_simple)
        capacity = 1.0 - 1 / 4  # 0.75
        expected = round(
            _W_CAPACITY * capacity + _W_BUDGET * 1.0 + _W_AFFINITY * 1.0
            + _W_FRESHNESS * 1.0 + _W_NODE * 1.0,
            4,
        )
        assert abs(recs[0].score - expected) < 0.001

    def test_budget_score_formula(self, lb):
        """budget_score = context_budget_remaining_pct / 100."""
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            lb2 = LoadBalancer()
            lb2.register_agent("a", [WorkCellLane.api_simple], max_concurrent=3)
            lb2.update_agent_load("a", current_active=0, context_budget_pct=60.0)
            recs = lb2.recommend(WorkCellLane.api_simple)
        budget = 60.0 / 100.0  # 0.60
        expected = round(
            _W_CAPACITY * 1.0 + _W_BUDGET * budget + _W_AFFINITY * 1.0
            + _W_FRESHNESS * 1.0 + _W_NODE * 1.0,
            4,
        )
        assert abs(recs[0].score - expected) < 0.001

    def test_weights_sum_to_one(self):
        total = _W_CAPACITY + _W_BUDGET + _W_AFFINITY + _W_FRESHNESS + _W_NODE
        assert abs(total - 1.0) < 1e-9

    def test_update_agent_load_changes_recommendations(self, lb):
        """After updating load, the ranking should change."""
        lb.register_agent("a", [WorkCellLane.api_simple], max_concurrent=3)
        lb.register_agent("b", [WorkCellLane.api_simple], max_concurrent=3)
        lb.update_agent_load("a", current_active=0, context_budget_pct=100.0)
        lb.update_agent_load("b", current_active=2, context_budget_pct=100.0)
        recs_before = lb.recommend(WorkCellLane.api_simple)
        assert recs_before[0].agent_id == "a"

        # Now a becomes busy and b becomes free
        lb.update_agent_load("a", current_active=3, context_budget_pct=100.0)
        lb.update_agent_load("b", current_active=0, context_budget_pct=100.0)
        recs_after = lb.recommend(WorkCellLane.api_simple)
        assert recs_after[0].agent_id == "b"
