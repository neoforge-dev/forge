"""Tests for node-level awareness in LoadBalancer — DF-2005.

Coverage targets
----------------
- test_register_agent_with_node         — node registered, count increments
- test_register_agent_unknown_node      — unknown node does not crash, no count
- test_register_agent_no_node           — backward compat: empty node still works
- test_register_reregisters_same_node   — re-registering same node is idempotent
- test_register_moves_between_nodes     — moving agent across nodes adjusts counts
- test_recommend_filters_full_node      — agents on a full node are excluded
- test_recommend_full_node_returns_empty — only agent is on a full node → []
- test_recommend_penalizes_busy_node    — agents on >75% utilisation score lower
- test_node_score_at_threshold          — 75% utilisation → node_score == 1.0
- test_node_score_above_threshold       — linear decay above 75%
- test_node_score_at_full              — 100% utilisation → node_score == 0.0
- test_node_score_no_node              — empty node → node_score == 1.0
- test_node_score_unknown_node         — unknown node → node_score == 1.0
- test_node_score_in_composite         — 5-component scoring arithmetic
- test_update_node_state               — updating node state changes recommendations
- test_update_node_state_unknown_node  — unknown node is silently ignored
- test_update_node_state_negative_raises — negative count raises ValueError
- test_get_node_stats                  — returns correct utilisation data
- test_get_node_stats_reflects_updates — updated count appears in stats
- test_default_node_budgets            — DEFAULT_NODE_BUDGETS has correct values
- test_agent_without_node_works        — backward compat: no-node agents still recommended
- test_mixed_node_and_no_node_agents   — agents with and without nodes coexist
- test_thread_safety_node_updates      — concurrent update_node_state is safe
- test_node_is_full_empty_node         — _node_is_full("") is False
- test_node_is_full_known_at_limit     — _node_is_full returns True at capacity
- test_node_is_full_unknown_node       — _node_is_full unknown → False
- test_weights_still_sum_to_one        — five-component weights sum to 1.0
- test_reasons_includes_node_score     — recommendation reasons list has node_score

Target: 28 tests.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.models.agent_load import (
    AgentCapability,
    BalancingStrategy,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.load_balancer import (
    _NODE_PENALTY_THRESHOLD,
    _W_AFFINITY,
    _W_BUDGET,
    _W_CAPACITY,
    _W_FRESHNESS,
    _W_NODE,
    DEFAULT_NODE_BUDGETS,
    LoadBalancer,
    NodeBudget,
    get_load_balancer,
    reset_load_balancer,
)

# ===========================================================================
# Helpers / fixtures
# ===========================================================================

_LANE = WorkCellLane.api_simple


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


def _fresh_lb_at_time(t: float) -> LoadBalancer:
    """Return a LoadBalancer whose internal monotonic clock is frozen at *t*."""
    # We can't patch at fixture time without a context manager, so callers
    # create one manually inside a patched block when needed.
    return LoadBalancer()


# ===========================================================================
# 1. register_agent — node field handling
# ===========================================================================


class TestRegisterAgentWithNode:
    def test_register_agent_with_node(self, lb):
        """Registering an agent with a known node increments the node count."""
        lb.register_agent("glm", [_LANE], node="prya")
        stats = lb.get_node_stats()
        assert stats["prya"]["current_agent_count"] == 1

    def test_register_agent_unknown_node(self, lb):
        """Registering an agent on an unknown node does not crash."""
        # "phantom" is not in DEFAULT_NODE_BUDGETS
        cap = lb.register_agent("agent-x", [_LANE], node="phantom")
        assert cap.node == "phantom"
        # Node stats should not have a phantom entry
        assert "phantom" not in lb.get_node_stats()

    def test_register_agent_no_node(self, lb):
        """Registering without a node leaves all node counts at 0."""
        lb.register_agent("legacy-agent", [_LANE])
        stats = lb.get_node_stats()
        # All nodes should remain at their default 0
        for node_stat in stats.values():
            assert node_stat["current_agent_count"] == 0

    def test_register_reregisters_same_node(self, lb):
        """Re-registering the same agent on the same node does not double-count."""
        lb.register_agent("glm", [_LANE], node="prya")
        lb.register_agent("glm", [_LANE], node="prya")
        stats = lb.get_node_stats()
        assert stats["prya"]["current_agent_count"] == 1

    def test_register_moves_between_nodes(self, lb):
        """Moving an agent from prya to sati decrements prya and increments sati."""
        lb.register_agent("glm", [_LANE], node="prya")
        lb.register_agent("glm", [_LANE], node="sati")
        stats = lb.get_node_stats()
        assert stats["prya"]["current_agent_count"] == 0
        assert stats["sati"]["current_agent_count"] == 1

    def test_capability_stores_node(self, lb):
        """The returned AgentCapability stores the node name."""
        cap = lb.register_agent("glm", [_LANE], node="nova")
        assert cap.node == "nova"

    def test_capability_no_node_defaults_empty(self, lb):
        """AgentCapability.node defaults to empty string when omitted."""
        cap = lb.register_agent("legacy", [_LANE])
        assert cap.node == ""


# ===========================================================================
# 2. recommend — node-based eligibility filtering
# ===========================================================================


class TestRecommendFiltersFullNode:
    def test_recommend_filters_full_node(self, lb):
        """Agents on a full node are excluded; agents on other nodes are returned."""
        # prya has max_agents=2; fill it up via update_node_state
        lb.update_node_state("prya", current_agent_count=2)
        lb.register_agent("prya-agent", [_LANE], node="prya")
        lb.register_agent("sati-agent", [_LANE], node="sati")

        recs = lb.recommend(_LANE)
        agent_ids = {r.agent_id for r in recs}
        assert "prya-agent" not in agent_ids
        assert "sati-agent" in agent_ids

    def test_recommend_full_node_returns_empty(self, lb):
        """When the only eligible agent is on a full node, recommend returns []."""
        lb.update_node_state("prya", current_agent_count=2)  # prya max=2
        lb.register_agent("only-agent", [_LANE], node="prya")

        recs = lb.recommend(_LANE)
        assert recs == []

    def test_recommend_full_node_exact_boundary(self, lb):
        """Node is considered full when count == max_agents (not just >)."""
        # nova max_agents=4; set to exactly 4
        lb.update_node_state("nova", current_agent_count=4)
        lb.register_agent("nova-agent", [_LANE], node="nova")

        recs = lb.recommend(_LANE)
        assert recs == []

    def test_recommend_not_full_one_below_max(self, lb):
        """Node with count == max_agents - 1 is NOT full; agent should be eligible.

        Register 3 agents on nova (max=4) — 3 < 4, so the node is NOT full and
        all three should be returned as eligible candidates.
        """
        # nova max_agents=4; three registered agents = 3/4, one slot remaining
        lb.register_agent("nova-agent-a", [_LANE], node="nova")
        lb.register_agent("nova-agent-b", [_LANE], node="nova")
        lb.register_agent("nova-agent-c", [_LANE], node="nova")

        recs = lb.recommend(_LANE)
        # All three should be returned — node is NOT full (3 < 4)
        assert len(recs) == 3
        agent_ids = {r.agent_id for r in recs}
        assert "nova-agent-a" in agent_ids
        assert "nova-agent-b" in agent_ids
        assert "nova-agent-c" in agent_ids


# ===========================================================================
# 3. Node score computation
# ===========================================================================


class TestNodeScoreComputation:
    def test_node_score_at_threshold(self, lb):
        """Utilisation exactly at 75% produces node_score == 1.0."""
        # sati max=6; 75% = 4.5 → floor to 4 (integer counts)
        # Use a node with max=4 so 3 agents = 75% exactly
        lb.update_node_state("nova", current_agent_count=3)  # nova max=4 → 75%
        score = lb._compute_node_score("nova")
        assert score == 1.0

    def test_node_score_above_threshold(self, lb):
        """Utilisation above 75% reduces node_score below 1.0."""
        # nova max=4; 4 agents = 100% but that triggers node_is_full.
        # Use 3 agents on prya (max=2 → 150%) — but that's already full.
        # Use sati (max=6); 5 agents = 83.3%
        lb.update_node_state("sati", current_agent_count=5)
        score = lb._compute_node_score("sati")
        utilization = 5 / 6
        expected = max(
            1.0 - (utilization - _NODE_PENALTY_THRESHOLD) / (1.0 - _NODE_PENALTY_THRESHOLD),
            0.0,
        )
        assert abs(score - expected) < 1e-6

    def test_node_score_at_full(self, lb):
        """Utilisation at 100% produces node_score == 0.0."""
        lb.update_node_state("prya", current_agent_count=2)  # prya max=2 → 100%
        score = lb._compute_node_score("prya")
        assert score == 0.0

    def test_node_score_no_node(self, lb):
        """Empty node string produces node_score == 1.0 (no penalty)."""
        score = lb._compute_node_score("")
        assert score == 1.0

    def test_node_score_unknown_node(self, lb):
        """Unknown node name produces node_score == 1.0 (permissive default)."""
        score = lb._compute_node_score("atlantis")
        assert score == 1.0

    def test_node_score_below_threshold(self, lb):
        """Utilisation well below 75% still returns 1.0."""
        lb.update_node_state("sati", current_agent_count=2)  # 2/6 ≈ 33%
        score = lb._compute_node_score("sati")
        assert score == 1.0


# ===========================================================================
# 4. Five-component scoring arithmetic
# ===========================================================================


class TestNodeScoreInComposite:
    def test_node_score_in_composite(self, lb):
        """The composite score correctly incorporates all five weighted components.

        Strategy: register the agent first (count becomes 1), then push the
        node state to 5 via update_node_state so utilisation = 5/6 ≈ 83% and
        node_score < 1.0.  The agent is NOT filtered out because 5 < 6.
        """
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mt:
            mt.monotonic.return_value = 100.0
            lb2 = LoadBalancer()
            lb2.register_agent("agent-a", [_LANE], max_concurrent=4, node="sati")
            lb2.update_agent_load("agent-a", current_active=1, context_budget_pct=80.0)
            # Override node state after registration: 5 agents on sati (max=6)
            lb2.update_node_state("sati", current_agent_count=5)
            recs = lb2.recommend(_LANE)

        assert len(recs) == 1
        rec = recs[0]

        capacity = 1.0 - 1 / 4  # 0.75
        budget = 80.0 / 100.0  # 0.80
        affinity = 1.0
        freshness = 1.0  # age = 0 → 1.0
        utilization = 5 / 6
        node_s = max(
            1.0 - (utilization - _NODE_PENALTY_THRESHOLD) / (1.0 - _NODE_PENALTY_THRESHOLD),
            0.0,
        )

        expected = round(
            _W_CAPACITY * capacity
            + _W_BUDGET * budget
            + _W_AFFINITY * affinity
            + _W_FRESHNESS * freshness
            + _W_NODE * node_s,
            4,
        )
        assert abs(rec.score - expected) < 0.001

    def test_weights_still_sum_to_one(self):
        """The five scoring weights must sum to exactly 1.0."""
        total = _W_CAPACITY + _W_BUDGET + _W_AFFINITY + _W_FRESHNESS + _W_NODE
        assert abs(total - 1.0) < 1e-9

    def test_reasons_includes_node_score(self, lb):
        """Recommendation reasons list must contain a node_score entry."""
        lb.register_agent("agent-a", [_LANE], node="sati")
        recs = lb.recommend(_LANE)
        assert len(recs) == 1
        reasons_text = " ".join(recs[0].reasons)
        assert "node_score" in reasons_text


# ===========================================================================
# 5. recommend — node penalty causes lower score
# ===========================================================================


class TestRecommendPenalizesBusyNode:
    def test_recommend_penalizes_busy_node(self, lb):
        """An agent on a >75% utilised node scores lower than one on a healthy node.

        Register both agents first (each increments its node's count by 1),
        then override the node states so sati is at 83% and nova at 25%.
        The sati-agent must still be eligible (5 < 6) but penalised.
        """
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mt:
            mt.monotonic.return_value = 100.0
            lb2 = LoadBalancer()
            lb2.register_agent("sati-agent", [_LANE], node="sati")
            lb2.register_agent("nova-agent", [_LANE], node="nova")
            lb2.update_agent_load("sati-agent", current_active=0, context_budget_pct=100.0)
            lb2.update_agent_load("nova-agent", current_active=0, context_budget_pct=100.0)
            # Override node states: sati 5/6 ≈ 83% (penalty zone), nova 1/4=25% (healthy)
            lb2.update_node_state("sati", current_agent_count=5)
            lb2.update_node_state("nova", current_agent_count=1)

            recs = lb2.recommend(_LANE)

        agent_map = {r.agent_id: r.score for r in recs}
        assert "sati-agent" in agent_map
        assert "nova-agent" in agent_map
        assert agent_map["nova-agent"] > agent_map["sati-agent"]

    def test_recommend_prefers_healthier_node_agent(self, lb):
        """When all other factors equal, the agent on the healthier node is ranked first."""
        with patch("forge_harness.webhook_server.services.load_balancer.time") as mt:
            mt.monotonic.return_value = 100.0
            lb2 = LoadBalancer()
            lb2.register_agent("prya-agent", [_LANE], node="prya")
            lb2.register_agent("sati-agent", [_LANE], node="sati")
            lb2.update_agent_load("prya-agent", current_active=0, context_budget_pct=100.0)
            lb2.update_agent_load("sati-agent", current_active=0, context_budget_pct=100.0)
            # Override: prya 1/2 = 50% (healthy), sati 5/6 ≈ 83% (penalty)
            lb2.update_node_state("prya", current_agent_count=1)
            lb2.update_node_state("sati", current_agent_count=5)
            recs = lb2.recommend(_LANE)

        assert len(recs) == 2
        assert recs[0].agent_id == "prya-agent"


# ===========================================================================
# 6. update_node_state
# ===========================================================================


class TestUpdateNodeState:
    def test_update_node_state(self, lb):
        """update_node_state changes the live count and affects recommendations."""
        lb.register_agent("prya-agent-1", [_LANE], node="prya")
        lb.register_agent("prya-agent-2", [_LANE], node="prya")

        # Both agents on prya (max=2); set count to 2 → full
        lb.update_node_state("prya", current_agent_count=2)
        recs_full = lb.recommend(_LANE)
        assert recs_full == []

        # Now drop count to 1 → not full → both agents eligible again
        lb.update_node_state("prya", current_agent_count=1)
        recs_open = lb.recommend(_LANE)
        assert len(recs_open) == 2

    def test_update_node_state_unknown_node(self, lb):
        """Calling update_node_state on an unknown node is silently ignored."""
        lb.update_node_state("atlantis", current_agent_count=99)
        # No exception; node stats are unchanged
        stats = lb.get_node_stats()
        assert "atlantis" not in stats

    def test_update_node_state_negative_raises(self, lb):
        """Negative agent count raises ValueError."""
        with pytest.raises(ValueError, match="current_agent_count"):
            lb.update_node_state("prya", current_agent_count=-1)

    def test_update_node_state_zero_resets(self, lb):
        """Setting count to 0 makes a previously full node open again."""
        lb.update_node_state("prya", current_agent_count=2)  # full
        lb.update_node_state("prya", current_agent_count=0)
        stats = lb.get_node_stats()
        assert stats["prya"]["current_agent_count"] == 0
        assert stats["prya"]["utilization"] == 0.0


# ===========================================================================
# 7. get_node_stats
# ===========================================================================


class TestGetNodeStats:
    def test_get_node_stats(self, lb):
        """get_node_stats returns correct utilisation data for all default nodes."""
        stats = lb.get_node_stats()
        # All five default nodes must be present
        for node_name in ("prya", "sati", "nova", "vega", "gaea"):
            assert node_name in stats
            node = stats[node_name]
            assert "name" in node
            assert "max_agents" in node
            assert "ram_gb" in node
            assert "current_agent_count" in node
            assert "utilization" in node

    def test_get_node_stats_reflects_updates(self, lb):
        """Stats reflect the count set via update_node_state."""
        lb.update_node_state("sati", current_agent_count=3)
        stats = lb.get_node_stats()
        assert stats["sati"]["current_agent_count"] == 3
        assert abs(stats["sati"]["utilization"] - 3 / 6) < 1e-9

    def test_get_node_stats_reflects_register_agent(self, lb):
        """Stats reflect agent registration with node parameter."""
        lb.register_agent("agent-1", [_LANE], node="nova")
        stats = lb.get_node_stats()
        assert stats["nova"]["current_agent_count"] == 1
        assert abs(stats["nova"]["utilization"] - 1 / 4) < 1e-9

    def test_get_node_stats_utilization_at_full(self, lb):
        """Utilisation == 1.0 when count == max_agents."""
        lb.update_node_state("prya", current_agent_count=2)  # prya max=2
        stats = lb.get_node_stats()
        assert stats["prya"]["utilization"] == 1.0

    def test_get_node_stats_initial_zero(self, lb):
        """All utilisation values start at 0.0."""
        stats = lb.get_node_stats()
        for node_stat in stats.values():
            assert node_stat["current_agent_count"] == 0
            assert node_stat["utilization"] == 0.0


# ===========================================================================
# 8. DEFAULT_NODE_BUDGETS correctness
# ===========================================================================


class TestDefaultNodeBudgets:
    def test_default_node_budgets(self):
        """DEFAULT_NODE_BUDGETS contains the five known nodes with correct values."""
        assert "prya" in DEFAULT_NODE_BUDGETS
        assert "sati" in DEFAULT_NODE_BUDGETS
        assert "nova" in DEFAULT_NODE_BUDGETS
        assert "vega" in DEFAULT_NODE_BUDGETS
        assert "gaea" in DEFAULT_NODE_BUDGETS

    def test_prya_budget(self):
        nb = DEFAULT_NODE_BUDGETS["prya"]
        assert nb.max_agents == 2
        assert nb.ram_gb == 16
        assert nb.current_agent_count == 0

    def test_sati_budget(self):
        nb = DEFAULT_NODE_BUDGETS["sati"]
        assert nb.max_agents == 6
        assert nb.ram_gb == 64
        assert nb.current_agent_count == 0

    def test_nova_budget(self):
        nb = DEFAULT_NODE_BUDGETS["nova"]
        assert nb.max_agents == 4
        assert nb.ram_gb == 48
        assert nb.current_agent_count == 0

    def test_vega_budget(self):
        nb = DEFAULT_NODE_BUDGETS["vega"]
        assert nb.max_agents == 2
        assert nb.ram_gb == 16
        assert nb.current_agent_count == 0

    def test_gaea_budget(self):
        nb = DEFAULT_NODE_BUDGETS["gaea"]
        assert nb.max_agents == 3
        assert nb.ram_gb == 16
        assert nb.current_agent_count == 0

    def test_default_budgets_not_mutated_by_instances(self):
        """Creating a LoadBalancer must not mutate DEFAULT_NODE_BUDGETS."""
        lb1 = LoadBalancer()
        lb1.register_agent("x", [_LANE], node="prya")
        assert DEFAULT_NODE_BUDGETS["prya"].current_agent_count == 0

    def test_node_budget_dataclass_fields(self):
        """NodeBudget is a dataclass with expected fields."""
        nb = NodeBudget(name="test", max_agents=5, ram_gb=32)
        assert nb.name == "test"
        assert nb.max_agents == 5
        assert nb.ram_gb == 32
        assert nb.current_agent_count == 0  # default


# ===========================================================================
# 9. Backward compatibility — agents without node
# ===========================================================================


class TestAgentWithoutNodeWorks:
    def test_agent_without_node_works(self, lb):
        """Agents with no node are always eligible and score correctly."""
        lb.register_agent("legacy", [_LANE])  # no node
        recs = lb.recommend(_LANE)
        assert len(recs) == 1
        assert recs[0].agent_id == "legacy"

    def test_mixed_node_and_no_node_agents(self, lb):
        """Agents with and without nodes coexist; node-less agents are always eligible."""
        lb.update_node_state("prya", current_agent_count=2)  # prya full
        lb.register_agent("prya-agent", [_LANE], node="prya")
        lb.register_agent("nodeless-agent", [_LANE])  # no node → always eligible

        recs = lb.recommend(_LANE)
        agent_ids = {r.agent_id for r in recs}
        assert "prya-agent" not in agent_ids
        assert "nodeless-agent" in agent_ids

    def test_no_node_agent_gets_full_node_score(self, lb):
        """A no-node agent should have node_score == 1.0 in its reasons."""
        lb.register_agent("legacy", [_LANE])
        recs = lb.recommend(_LANE)
        reasons_text = " ".join(recs[0].reasons)
        # node_score should be 1.000 for no-node agents
        assert "node_score=1.000" in reasons_text


# ===========================================================================
# 10. _node_is_full helper edge cases
# ===========================================================================


class TestNodeIsFull:
    def test_node_is_full_empty_node(self, lb):
        """_node_is_full('') always returns False regardless of budget state."""
        lb.update_node_state("prya", current_agent_count=999)
        assert lb._node_is_full("") is False

    def test_node_is_full_known_at_limit(self, lb):
        """_node_is_full returns True when count == max_agents."""
        lb.update_node_state("prya", current_agent_count=2)  # prya max=2
        assert lb._node_is_full("prya") is True

    def test_node_is_full_known_over_limit(self, lb):
        """_node_is_full returns True when count > max_agents."""
        lb.update_node_state("prya", current_agent_count=3)  # prya max=2
        assert lb._node_is_full("prya") is True

    def test_node_is_full_known_below_limit(self, lb):
        """_node_is_full returns False when count < max_agents."""
        lb.update_node_state("prya", current_agent_count=1)  # prya max=2
        assert lb._node_is_full("prya") is False

    def test_node_is_full_unknown_node(self, lb):
        """_node_is_full returns False for unregistered nodes."""
        assert lb._node_is_full("atlantis") is False


# ===========================================================================
# 11. Thread safety
# ===========================================================================


class TestThreadSafetyNodeUpdates:
    def test_thread_safety_node_updates(self, lb):
        """Concurrent update_node_state calls must not corrupt the registry."""
        errors: list[Exception] = []

        def update(count: int) -> None:
            try:
                for _ in range(50):
                    lb.update_node_state("sati", current_agent_count=count)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=update, args=(i % 7,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # After all threads finish the count must be a non-negative integer
        stats = lb.get_node_stats()
        assert stats["sati"]["current_agent_count"] >= 0

    def test_thread_safety_register_and_recommend(self, lb):
        """Concurrent register_agent with node and recommend must not raise."""
        errors: list[Exception] = []

        def register_agents() -> None:
            try:
                for i in range(10):
                    lb.register_agent(f"agent-{i}", [_LANE], node="sati")
            except Exception as exc:
                errors.append(exc)

        def recommend_agents() -> None:
            try:
                for _ in range(30):
                    lb.recommend(_LANE)
            except Exception as exc:
                errors.append(exc)

        threads: list[threading.Thread] = []
        threads.append(threading.Thread(target=register_agents))
        for _ in range(5):
            threads.append(threading.Thread(target=recommend_agents))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
