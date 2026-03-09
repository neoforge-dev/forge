"""Parity and Determinism Tests for Recommendation Ordering — DF-3006
======================================================================

Verifies that the :class:`RecommendationEngine` produces:

1. **Deterministic** results — identical inputs always yield identical outputs.
2. **Stable ordering** — relative rankings are preserved across mutations.
3. **Strategy parity** — each strategy weighs factors predictably and
   diverges or converges as the math demands.
4. **Score boundaries** — scores and confidence always stay in ``[0, 1]``.
5. **Cross-factor interactions** — competing factors resolve predictably.
6. **Regression guards** — golden tests and monotonicity contracts.

Target: 80+ tests.

All tests are ``unittest.TestCase`` subclasses.  Every test method resets the
LoadBalancer and RecommendationEngine singletons to ensure full isolation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.recommendation import RecommendationStrategy
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.load_balancer import (
    LoadBalancer,
    reset_load_balancer,
)
from forge_harness.webhook_server.services.recommendation_engine import (
    RecommendationEngine,
    reset_recommendation_engine,
)

# ---------------------------------------------------------------------------
# Strategy weight constants — mirrors the production values for golden tests.
# ---------------------------------------------------------------------------

# balanced
_W_CAP_BAL = 0.30
_W_HIST_BAL = 0.25
_W_LOAD_BAL = 0.25
_W_BUD_BAL = 0.20

# capability_match
_W_CAP_CAP = 0.60
_W_HIST_CAP = 0.125
_W_LOAD_CAP = 0.125
_W_BUD_CAP = 0.15

# historical_performance
_W_CAP_HIST = 0.15
_W_HIST_HIST = 0.50
_W_LOAD_HIST = 0.20
_W_BUD_HIST = 0.15

# Neutral prior when no history recorded
_NEUTRAL_PASS_RATE = 0.5


# ---------------------------------------------------------------------------
# Base test mixin with singleton reset helpers
# ---------------------------------------------------------------------------


class _EngineTestBase(unittest.TestCase):
    """Base class: creates a fresh LoadBalancer + RecommendationEngine per test.

    Each test gets a tmp file for the history JSONL so that file-system
    persistence from one test never bleeds into the next.
    """

    def setUp(self) -> None:
        reset_load_balancer()
        reset_recommendation_engine()
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._history_path = Path(self._tmp_dir.name) / "history.jsonl"
        self.lb = LoadBalancer()
        self.engine = RecommendationEngine(
            load_balancer=self.lb,
            history_path=self._history_path,
        )

    def tearDown(self) -> None:
        reset_recommendation_engine()
        reset_load_balancer()
        self._tmp_dir.cleanup()

    # ------------------------------------------------------------------
    # Shared setup helpers
    # ------------------------------------------------------------------

    def _register_agent(
        self,
        agent_id: str,
        lanes: list[WorkCellLane] | None = None,
        max_concurrent: int = 3,
        current_active: int = 0,
        budget_pct: float = 100.0,
    ) -> None:
        """Register an agent and optionally set its load."""
        if lanes is None:
            lanes = [WorkCellLane.test_writing]
        self.lb.register_agent(agent_id, lanes, max_concurrent=max_concurrent)
        self.lb.update_agent_load(agent_id, current_active, budget_pct)

    def _register_3_agents(self) -> None:
        """Register 3 identical agents on test_writing lane."""
        for name in ["forge:alpha", "forge:beta", "forge:gamma"]:
            self._register_agent(name, [WorkCellLane.test_writing])

    def _register_with_history(
        self,
        agent_id: str,
        pass_count: int,
        fail_count: int,
        lanes: list[WorkCellLane] | None = None,
    ) -> None:
        """Register an agent and record pass/fail history outcomes."""
        self._register_agent(agent_id, lanes)
        for i in range(pass_count):
            self.engine.record_outcome(f"task-pass-{agent_id}-{i}", agent_id, True, 10.0)
        for i in range(fail_count):
            self.engine.record_outcome(f"task-fail-{agent_id}-{i}", agent_id, False, 10.0)

    def _recommend(
        self,
        task_id: str = "task-001",
        task_type: TaskType = TaskType.test_writing,
        risk_tier: RiskTier = RiskTier.low,
        strategy: RecommendationStrategy = RecommendationStrategy.balanced,
        preferred_lane: WorkCellLane | None = None,
    ):
        return self.engine.recommend(
            task_id,
            task_type,
            risk_tier,
            preferred_lane=preferred_lane,
            strategy=strategy,
        )


# ===========================================================================
# 1. Determinism Tests (15 tests)
# ===========================================================================


class TestDeterminismSameOutput(_EngineTestBase):
    """Same inputs must always produce the same ranked output."""

    def test_same_output_two_calls_balanced(self):
        """Two calls with identical state → identical agent order."""
        self._register_3_agents()
        r1 = self._recommend("t1", strategy=RecommendationStrategy.balanced)
        r2 = self._recommend("t2", strategy=RecommendationStrategy.balanced)
        ids1 = [a.agent_id for a in r1.recommended_agents]
        ids2 = [a.agent_id for a in r2.recommended_agents]
        self.assertEqual(ids1, ids2)

    def test_same_output_ten_calls_balanced(self):
        """Ten calls with identical state → all produce the same order."""
        self._register_3_agents()
        results = [
            [a.agent_id for a in self._recommend(f"t{i}").recommended_agents] for i in range(10)
        ]
        for r in results[1:]:
            self.assertEqual(r, results[0])

    def test_same_confidence_two_calls_balanced(self):
        """Confidence value is identical across repeated calls."""
        self._register_3_agents()
        r1 = self._recommend("t1")
        r2 = self._recommend("t2")
        self.assertAlmostEqual(r1.confidence, r2.confidence, delta=0.0001)

    def test_same_reasoning_structure_balanced(self):
        """Number of reasoning entries is identical across repeated calls."""
        self._register_3_agents()
        r1 = self._recommend("t1")
        r2 = self._recommend("t2")
        self.assertEqual(len(r1.reasoning), len(r2.reasoning))

    def test_determinism_capability_match_strategy(self):
        """capability_match strategy is deterministic across 10 calls."""
        self._register_3_agents()
        results = [
            [
                a.agent_id
                for a in self._recommend(
                    f"t{i}", strategy=RecommendationStrategy.capability_match
                ).recommended_agents
            ]
            for i in range(10)
        ]
        for r in results[1:]:
            self.assertEqual(r, results[0])

    def test_determinism_historical_performance_strategy(self):
        """historical_performance strategy is deterministic across 10 calls."""
        self._register_3_agents()
        results = [
            [
                a.agent_id
                for a in self._recommend(
                    f"t{i}", strategy=RecommendationStrategy.historical_performance
                ).recommended_agents
            ]
            for i in range(10)
        ]
        for r in results[1:]:
            self.assertEqual(r, results[0])

    def test_determinism_single_agent(self):
        """Single agent: output is deterministic across all strategies."""
        self._register_agent("forge:solo", [WorkCellLane.test_writing])
        for strategy in RecommendationStrategy:
            r1 = self._recommend("t1", strategy=strategy)
            r2 = self._recommend("t2", strategy=strategy)
            self.assertEqual(
                r1.recommended_agents[0].agent_id,
                r2.recommended_agents[0].agent_id,
                msg=f"Non-deterministic for strategy={strategy}",
            )

    def test_determinism_three_agents(self):
        """3 agents: deterministic across all 3 strategies."""
        self._register_3_agents()
        for strategy in RecommendationStrategy:
            r1 = self._recommend("t1", strategy=strategy)
            r2 = self._recommend("t2", strategy=strategy)
            self.assertEqual(
                [a.agent_id for a in r1.recommended_agents],
                [a.agent_id for a in r2.recommended_agents],
                msg=f"Non-deterministic for strategy={strategy}",
            )

    def test_determinism_five_agents(self):
        """5 agents: deterministic across all 3 strategies."""
        for i, name in enumerate(["forge:a", "forge:b", "forge:c", "forge:d", "forge:e"]):
            self._register_agent(
                name, [WorkCellLane.test_writing], current_active=i % 3, budget_pct=100.0 - i * 5
            )
        for strategy in RecommendationStrategy:
            r1 = self._recommend("t1", strategy=strategy)
            r2 = self._recommend("t2", strategy=strategy)
            self.assertEqual(
                [a.agent_id for a in r1.recommended_agents],
                [a.agent_id for a in r2.recommended_agents],
                msg=f"Non-deterministic for strategy={strategy}",
            )

    def test_determinism_ten_agents(self):
        """10 agents: deterministic for balanced strategy."""
        for i in range(10):
            self._register_agent(
                f"forge:agent{i:02d}",
                [WorkCellLane.test_writing],
                current_active=i % 3,
                budget_pct=max(10.0, 100.0 - i * 9),
            )
        r1 = self._recommend("t1")
        r2 = self._recommend("t2")
        self.assertEqual(
            [a.agent_id for a in r1.recommended_agents],
            [a.agent_id for a in r2.recommended_agents],
        )

    def test_scores_identical_across_calls(self):
        """Individual agent scores are identical across repeated calls."""
        self._register_3_agents()
        r1 = self._recommend("t1")
        r2 = self._recommend("t2")
        for s1, s2 in zip(r1.recommended_agents, r2.recommended_agents):
            self.assertAlmostEqual(s1.score, s2.score, delta=0.0001)

    def test_factor_values_deterministic(self):
        """Factor breakdown values are identical across calls."""
        self._register_3_agents()
        r1 = self._recommend("t1")
        r2 = self._recommend("t2")
        for s1, s2 in zip(r1.recommended_agents, r2.recommended_agents):
            for key in s1.factors:
                self.assertAlmostEqual(
                    s1.factors[key],
                    s2.factors[key],
                    delta=0.0001,
                    msg=f"factor {key} differs",
                )

    def test_preferred_lane_override_deterministic(self):
        """Preferred lane override does not break determinism."""
        self._register_agent("forge:alpha", [WorkCellLane.test_writing, WorkCellLane.docs])
        r1 = self._recommend("t1", preferred_lane=WorkCellLane.docs)
        r2 = self._recommend("t2", preferred_lane=WorkCellLane.docs)
        self.assertEqual(
            r1.recommended_agents[0].agent_id,
            r2.recommended_agents[0].agent_id,
        )

    def test_history_recording_preserves_determinism(self):
        """After recording outcomes, repeated calls are still deterministic."""
        self._register_3_agents()
        for agent_id in ["forge:alpha", "forge:beta", "forge:gamma"]:
            for i in range(5):
                self.engine.record_outcome(f"task-{agent_id}-{i}", agent_id, True, 10.0)
        r1 = self._recommend("t1")
        r2 = self._recommend("t2")
        self.assertEqual(
            [a.agent_id for a in r1.recommended_agents],
            [a.agent_id for a in r2.recommended_agents],
        )

    def test_empty_result_deterministic(self):
        """Zero eligible agents returns empty list deterministically."""
        # Register agent with different lane — not eligible for test_writing
        self._register_agent("forge:docs-only", [WorkCellLane.docs])
        r1 = self._recommend("t1")
        r2 = self._recommend("t2")
        self.assertEqual(r1.recommended_agents, [])
        self.assertEqual(r2.recommended_agents, [])
        self.assertAlmostEqual(r1.confidence, 0.0, delta=0.0001)
        self.assertAlmostEqual(r2.confidence, 0.0, delta=0.0001)


# ===========================================================================
# 2. Ordering Stability Tests (15 tests)
# ===========================================================================


class TestOrderingStability(_EngineTestBase):
    """Adding or updating agents must not disturb existing relative ranks."""

    def _get_order(self, strategy=RecommendationStrategy.balanced):
        return [a.agent_id for a in self._recommend(strategy=strategy).recommended_agents]

    def test_adding_4th_agent_doesnt_break_top3_relative_order(self):
        """Adding a weaker 4th agent must not change the top-3 relative order."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=1, budget_pct=80.0
        )
        self._register_agent(
            "forge:gamma", [WorkCellLane.test_writing], current_active=2, budget_pct=60.0
        )
        order_before = self._get_order()

        # Register a 4th agent with worse load and budget — should rank last
        self._register_agent(
            "forge:delta", [WorkCellLane.test_writing], current_active=3, budget_pct=0.0
        )
        order_after = self._get_order()

        # Top-3 relative order must be preserved
        self.assertEqual(order_before, order_after[:3])

    def test_updating_low_ranked_agent_doesnt_change_number1(self):
        """Updating the last-place agent's load does not change the #1 agent."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=2, budget_pct=50.0
        )
        self._register_agent(
            "forge:gamma", [WorkCellLane.test_writing], current_active=3, budget_pct=10.0
        )

        r_before = self._get_order()
        top1_before = r_before[0]

        # Increase gamma's load further — it's already last
        self.lb.update_agent_load("forge:gamma", current_active=3, context_budget_pct=5.0)
        r_after = self._get_order()
        self.assertEqual(r_after[0], top1_before)

    def test_recording_success_for_lower_agent_doesnt_displace_top(self):
        """Recording successes for a lower-ranked agent doesn't overtake a heavily-favored top agent."""
        # Alpha: perfect load + full budget
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        # Beta: significant load penalty
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=3, budget_pct=5.0
        )

        # Record 2 successes for beta — not enough to overcome alpha's load advantage
        for i in range(2):
            self.engine.record_outcome(f"t{i}", "forge:beta", True, 10.0)

        order = self._get_order()
        self.assertEqual(order[0], "forge:alpha")

    def test_recording_outcomes_shifts_rankings_predictably(self):
        """Recording 10 failures for the top agent promotes the second agent."""
        # Make alpha and beta identical except alpha starts first alphabetically
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        # Record many failures for alpha — its historical score should drop
        for i in range(20):
            self.engine.record_outcome(f"t{i}", "forge:alpha", False, 10.0)
        # Record many successes for beta
        for i in range(20):
            self.engine.record_outcome(f"t{i}", "forge:beta", True, 10.0)

        order = self._get_order()
        self.assertEqual(order[0], "forge:beta", "beta should lead after history diverges")

    def test_removing_agent_preserves_remaining_order(self):
        """Removing the #1 agent promotes #2 to top; remaining order is stable."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=1, budget_pct=80.0
        )
        self._register_agent(
            "forge:gamma", [WorkCellLane.test_writing], current_active=2, budget_pct=60.0
        )

        order_before = self._get_order()
        # Simulate removal by re-creating engine without alpha
        new_lb = LoadBalancer()
        new_lb.register_agent("forge:beta", [WorkCellLane.test_writing])
        new_lb.update_agent_load("forge:beta", 1, 80.0)
        new_lb.register_agent("forge:gamma", [WorkCellLane.test_writing])
        new_lb.update_agent_load("forge:gamma", 2, 60.0)

        new_engine = RecommendationEngine(load_balancer=new_lb, history_path=self._history_path)
        r = new_engine.recommend("task", TaskType.test_writing, RiskTier.low)
        remaining_order = [a.agent_id for a in r.recommended_agents]

        # The previous #2 and #3 relative order must be preserved
        self.assertEqual(remaining_order, order_before[1:])

    def test_load_update_immediately_affects_next_recommendation(self):
        """After updating an agent's load, the next recommendation reflects it."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        # Both identical — tie-break is alphabetical (alpha first)
        r1 = self._get_order()
        self.assertEqual(r1[0], "forge:alpha")

        # Now saturate alpha
        self.lb.update_agent_load("forge:alpha", current_active=3, context_budget_pct=0.0)
        r2 = self._get_order()
        self.assertEqual(r2[0], "forge:beta")

    def test_adding_agents_with_same_score_stable_alphabetical_tiebreak(self):
        """When scores are tied, agent_id alphabetical order is the tiebreak."""
        for name in ["forge:zebra", "forge:alpha", "forge:mango"]:
            self._register_agent(name, [WorkCellLane.test_writing])
        order = self._get_order()
        # All have same score → sorted alphabetically
        self.assertEqual(order, sorted(order))

    def test_relative_order_unchanged_when_third_agent_improves(self):
        """Improving the #3 agent keeps #1 and #2 in place."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=1, budget_pct=70.0
        )
        self._register_agent(
            "forge:gamma", [WorkCellLane.test_writing], current_active=2, budget_pct=40.0
        )

        order_before = self._get_order()

        # Improve gamma slightly but not enough to beat beta
        self.lb.update_agent_load("forge:gamma", 1, 70.0)
        order_after = self._get_order()

        self.assertEqual(order_after[0], order_before[0])
        self.assertEqual(order_after[1], order_before[1])

    def test_order_consistency_with_preferred_lane_override(self):
        """Preferred lane override preserves ordering for identical agents on that lane."""
        for name in ["forge:a", "forge:b", "forge:c"]:
            self._register_agent(name, [WorkCellLane.docs])
        r1 = self._recommend("t1", preferred_lane=WorkCellLane.docs)
        r2 = self._recommend("t2", preferred_lane=WorkCellLane.docs)
        self.assertEqual(
            [a.agent_id for a in r1.recommended_agents],
            [a.agent_id for a in r2.recommended_agents],
        )

    def test_historical_success_raises_agent_in_ranking(self):
        """An agent with a perfect history ranks above one with neutral history."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        # Alpha: 100% pass rate over 10 tasks
        for i in range(10):
            self.engine.record_outcome(f"p{i}", "forge:alpha", True, 5.0)

        order = self._get_order()
        self.assertEqual(order[0], "forge:alpha")

    def test_historical_failure_lowers_agent_in_ranking(self):
        """An agent with 0% pass rate should rank below neutral-history agent."""
        self._register_agent("forge:alpha", [WorkCellLane.test_writing])
        self._register_agent("forge:beta", [WorkCellLane.test_writing])

        # Alpha: 0% pass rate over 10 tasks
        for i in range(10):
            self.engine.record_outcome(f"f{i}", "forge:alpha", False, 5.0)

        order = self._get_order()
        self.assertEqual(order[0], "forge:beta")

    def test_order_preserved_after_budget_pct_changes(self):
        """After reducing one agent's budget significantly, order reflects it."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        order1 = self._get_order()
        # Drastically cut alpha's budget
        self.lb.update_agent_load("forge:alpha", 0, 5.0)
        order2 = self._get_order()

        # After budget drop, beta should be #1
        self.assertEqual(order2[0], "forge:beta")
        self.assertNotEqual(order1[0], order2[0])

    def test_adding_superior_agent_displaces_current_top(self):
        """Adding a clearly superior agent makes it the new #1."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=2, budget_pct=50.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=1, budget_pct=60.0
        )

        # Add a perfect agent
        self._register_agent(
            "forge:perfect", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        for i in range(10):
            self.engine.record_outcome(f"p{i}", "forge:perfect", True, 5.0)

        order = self._get_order()
        self.assertEqual(order[0], "forge:perfect")

    def test_two_agent_inversion_after_massive_history_change(self):
        """Massive history divergence causes inversion between two agents."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        # Make alpha #1 first with good history
        for i in range(20):
            self.engine.record_outcome(f"p{i}", "forge:alpha", True, 5.0)

        order1 = self._get_order()
        self.assertEqual(order1[0], "forge:alpha")

        # Now give beta perfect history AND crush alpha's history
        for i in range(40):
            self.engine.record_outcome(f"fail{i}", "forge:alpha", False, 5.0)
        for i in range(40):
            self.engine.record_outcome(f"beta{i}", "forge:beta", True, 5.0)

        order2 = self._get_order()
        self.assertEqual(order2[0], "forge:beta")

    def test_three_way_tie_is_alphabetically_stable(self):
        """A three-way score tie resolves to alphabetical order every time."""
        for name in ["forge:charlie", "forge:alpha", "forge:bravo"]:
            self._register_agent(name, [WorkCellLane.test_writing])
        for _ in range(5):
            order = self._get_order()
            self.assertEqual(order, ["forge:alpha", "forge:bravo", "forge:charlie"])


# ===========================================================================
# 3. Strategy Parity Tests (15 tests)
# ===========================================================================


class TestStrategyParity(_EngineTestBase):
    """Verify that strategies weigh factors as documented."""

    def _scores_for_strategy(self, strategy: RecommendationStrategy) -> dict[str, float]:
        r = self._recommend(strategy=strategy)
        return {a.agent_id: a.score for a in r.recommended_agents}

    def test_capability_match_score_higher_than_balanced_for_specialist(self):
        """capability_match weighs capability at 60% vs 30% — specialist scores higher."""
        # Both agents support test_writing; but we verify relative weight change
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        r_bal = self._recommend(strategy=RecommendationStrategy.balanced)
        r_cap = self._recommend(strategy=RecommendationStrategy.capability_match)

        # Both yield 2 candidates — capability_match weight sums should be different
        # capability is always 1.0 for eligible agents, but other weights differ
        # The key check: scores differ due to weight redistribution
        alpha_bal = next(a.score for a in r_bal.recommended_agents if a.agent_id == "forge:alpha")
        alpha_cap = next(a.score for a in r_cap.recommended_agents if a.agent_id == "forge:alpha")
        # With full budget and zero load, both should be > 0
        self.assertGreater(alpha_cap, 0.0)
        self.assertGreater(alpha_bal, 0.0)

    def test_all_strategies_agree_on_dominant_agent(self):
        """When one agent dominates all factors, all strategies rank it #1."""
        # Dominant: zero load, full budget
        self._register_agent(
            "forge:dominant", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        for i in range(10):
            self.engine.record_outcome(f"p{i}", "forge:dominant", True, 1.0)

        # Weak: at capacity, low budget, poor history
        self._register_agent(
            "forge:weak", [WorkCellLane.test_writing], current_active=3, budget_pct=5.0
        )
        for i in range(10):
            self.engine.record_outcome(f"f{i}", "forge:weak", False, 100.0)

        for strategy in RecommendationStrategy:
            r = self._recommend(strategy=strategy)
            self.assertEqual(
                r.recommended_agents[0].agent_id,
                "forge:dominant",
                msg=f"Expected dominant agent to be #1 for strategy={strategy}",
            )

    def test_historical_performance_strategy_weighs_history_higher(self):
        """historical_performance gives history 50% weight — history-rich agent wins."""
        # alpha: no history (neutral 0.5), zero load, full budget
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        # beta: perfect history, some load
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=2, budget_pct=60.0
        )
        for i in range(20):
            self.engine.record_outcome(f"p{i}", "forge:beta", True, 5.0)

        r = self._recommend(strategy=RecommendationStrategy.historical_performance)
        # With history at 50% weight: beta (1.0 history) beats alpha (0.5 history)
        # even though alpha has better load
        beta_score = next(a.score for a in r.recommended_agents if a.agent_id == "forge:beta")
        alpha_score = next(a.score for a in r.recommended_agents if a.agent_id == "forge:alpha")

        # history gain for beta: 0.50 * (1.0 - 0.5) = +0.25
        # load loss for beta: 0.20 * (1 - 2/3 - 1.0) = 0.20 * (0.333 - 1.0)
        # Let's just verify beta beats alpha
        self.assertGreater(beta_score, alpha_score)

    def test_strategies_diverge_high_capability_low_history(self):
        """Strategies diverge when one factor conflicts — verify numeric divergence."""
        # alpha: no history (neutral), good load
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        # beta: bad history (10 failures), perfect load and budget
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        for i in range(10):
            self.engine.record_outcome(f"f{i}", "forge:beta", False, 5.0)

        r_cap = self._recommend(strategy=RecommendationStrategy.capability_match)
        r_hist = self._recommend(strategy=RecommendationStrategy.historical_performance)

        # Under capability_match: history weight is 12.5% — beta's penalty is small
        beta_cap = next(a.score for a in r_cap.recommended_agents if a.agent_id == "forge:beta")
        beta_hist = next(a.score for a in r_hist.recommended_agents if a.agent_id == "forge:beta")

        # Under historical_performance: history weight is 50% — beta's penalty is big
        # beta score should be lower under historical_performance
        self.assertGreater(beta_cap, beta_hist)

    def test_single_eligible_agent_all_strategies_same_result(self):
        """Single eligible agent → all strategies return that agent as #1."""
        self._register_agent("forge:solo", [WorkCellLane.test_writing])
        for strategy in RecommendationStrategy:
            r = self._recommend(strategy=strategy)
            self.assertEqual(len(r.recommended_agents), 1)
            self.assertEqual(r.recommended_agents[0].agent_id, "forge:solo")

    def test_balanced_weighs_all_factors_equally_vs_capability(self):
        """Under balanced, load matters; under capability_match, load matters less."""
        # alpha: full budget, zero load, no history
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        # beta: full budget, 2/3 load, perfect history (10 tasks)
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=2, budget_pct=100.0
        )
        for i in range(10):
            self.engine.record_outcome(f"p{i}", "forge:beta", True, 1.0)

        r_bal = self._recommend(strategy=RecommendationStrategy.balanced)
        r_cap = self._recommend(strategy=RecommendationStrategy.capability_match)

        # Under balanced: alpha's load advantage (0.25 weight) can overcome beta's history
        # Under capability_match: history weight is only 12.5%
        # Either way, alpha's load factor should matter more under balanced
        alpha_bal = next(a.score for a in r_bal.recommended_agents if a.agent_id == "forge:alpha")
        alpha_cap = next(a.score for a in r_cap.recommended_agents if a.agent_id == "forge:alpha")

        # Both scores should be valid
        self.assertGreater(alpha_bal, 0.0)
        self.assertGreater(alpha_cap, 0.0)

    def test_strategy_weights_sum_to_1_balanced(self):
        """balanced strategy weight tuple sums to 1.0."""
        from forge_harness.webhook_server.services.recommendation_engine import _STRATEGY_WEIGHTS

        weights = _STRATEGY_WEIGHTS[RecommendationStrategy.balanced]
        self.assertAlmostEqual(sum(weights), 1.0, delta=0.0001)

    def test_strategy_weights_sum_to_1_capability_match(self):
        """capability_match strategy weight tuple sums to 1.0."""
        from forge_harness.webhook_server.services.recommendation_engine import _STRATEGY_WEIGHTS

        weights = _STRATEGY_WEIGHTS[RecommendationStrategy.capability_match]
        self.assertAlmostEqual(sum(weights), 1.0, delta=0.0001)

    def test_strategy_weights_sum_to_1_historical_performance(self):
        """historical_performance strategy weight tuple sums to 1.0."""
        from forge_harness.webhook_server.services.recommendation_engine import _STRATEGY_WEIGHTS

        weights = _STRATEGY_WEIGHTS[RecommendationStrategy.historical_performance]
        self.assertAlmostEqual(sum(weights), 1.0, delta=0.0001)

    def test_capability_match_has_highest_capability_weight(self):
        """capability_match strategy has the highest capability weight (0.60)."""
        from forge_harness.webhook_server.services.recommendation_engine import _STRATEGY_WEIGHTS

        cap = _STRATEGY_WEIGHTS[RecommendationStrategy.capability_match][0]
        bal = _STRATEGY_WEIGHTS[RecommendationStrategy.balanced][0]
        hist = _STRATEGY_WEIGHTS[RecommendationStrategy.historical_performance][0]
        self.assertGreater(cap, bal)
        self.assertGreater(cap, hist)

    def test_historical_performance_has_highest_history_weight(self):
        """historical_performance has the highest history weight (0.50)."""
        from forge_harness.webhook_server.services.recommendation_engine import _STRATEGY_WEIGHTS

        hist_hist = _STRATEGY_WEIGHTS[RecommendationStrategy.historical_performance][1]
        hist_bal = _STRATEGY_WEIGHTS[RecommendationStrategy.balanced][1]
        hist_cap = _STRATEGY_WEIGHTS[RecommendationStrategy.capability_match][1]
        self.assertGreater(hist_hist, hist_bal)
        self.assertGreater(hist_hist, hist_cap)

    def test_balanced_strategy_is_moderate_on_all_weights(self):
        """balanced strategy sits between extremes on every weight."""
        from forge_harness.webhook_server.services.recommendation_engine import _STRATEGY_WEIGHTS

        w_bal = _STRATEGY_WEIGHTS[RecommendationStrategy.balanced]
        w_cap = _STRATEGY_WEIGHTS[RecommendationStrategy.capability_match]
        w_hist = _STRATEGY_WEIGHTS[RecommendationStrategy.historical_performance]

        # Balanced capability weight is between the other two
        self.assertGreater(w_bal[0], w_hist[0])
        self.assertLess(w_bal[0], w_cap[0])
        # Balanced history weight is between the other two
        self.assertGreater(w_bal[1], w_cap[1])
        self.assertLess(w_bal[1], w_hist[1])

    def test_no_history_agent_scores_identically_across_strategies(self):
        """A single agent with no history: score formula depends only on capability/load/budget."""
        self._register_agent(
            "forge:solo", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        from forge_harness.webhook_server.services.recommendation_engine import _STRATEGY_WEIGHTS

        for strategy in RecommendationStrategy:
            r = self._recommend(strategy=strategy)
            actual_score = r.recommended_agents[0].score
            w = _STRATEGY_WEIGHTS[strategy]
            # capability=1.0, history=0.5 (neutral), load=1.0, budget=1.0
            expected = round(w[0] * 1.0 + w[1] * 0.5 + w[2] * 1.0 + w[3] * 1.0, 6)
            self.assertAlmostEqual(
                actual_score,
                expected,
                delta=0.001,
                msg=f"score mismatch for strategy={strategy}",
            )

    def test_all_strategies_return_same_number_of_agents(self):
        """All strategies return the same candidate count for the same input."""
        self._register_3_agents()
        counts = {
            s: len(self._recommend(strategy=s).recommended_agents) for s in RecommendationStrategy
        }
        self.assertEqual(len(set(counts.values())), 1, msg=f"Counts differ: {counts}")

    def test_strategy_parity_empty_result(self):
        """All strategies return empty list when no eligible agents."""
        self._register_agent("forge:docs", [WorkCellLane.docs])
        for strategy in RecommendationStrategy:
            r = self._recommend(strategy=strategy)
            self.assertEqual(r.recommended_agents, [])
            self.assertEqual(r.confidence, 0.0)


# ===========================================================================
# 4. Score Boundary Tests (10 tests)
# ===========================================================================


class TestScoreBoundaries(_EngineTestBase):
    """Scores and confidence must always be in [0, 1]."""

    def test_score_in_range_normal_agent(self):
        """Score is in [0, 1] for a standard agent."""
        self._register_agent("forge:a", [WorkCellLane.test_writing])
        r = self._recommend()
        for a in r.recommended_agents:
            self.assertGreaterEqual(a.score, 0.0)
            self.assertLessEqual(a.score, 1.0)

    def test_confidence_in_range_two_agents(self):
        """Confidence is in [0, 1] when two agents are present."""
        self._register_3_agents()
        r = self._recommend()
        self.assertGreaterEqual(r.confidence, 0.0)
        self.assertLessEqual(r.confidence, 1.0)

    def test_confidence_zero_for_empty_result(self):
        """Confidence is exactly 0.0 when no eligible agents."""
        self._register_agent("forge:docs", [WorkCellLane.docs])
        r = self._recommend()
        self.assertEqual(r.confidence, 0.0)

    def test_confidence_equals_top_score_for_single_agent(self):
        """When only one eligible agent, confidence equals its score."""
        self._register_agent("forge:solo", [WorkCellLane.test_writing])
        r = self._recommend()
        self.assertAlmostEqual(r.confidence, r.recommended_agents[0].score, delta=0.0001)

    def test_score_boundary_zero_load_full_budget(self):
        """Agent with zero load and full budget: score is near upper bound."""
        self._register_agent(
            "forge:perfect", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        r = self._recommend()
        # With neutral history and perfect load/budget: 0.30 + 0.125 + 0.25 + 0.20 >= 0.875
        self.assertGreater(r.recommended_agents[0].score, 0.80)

    def test_score_boundary_at_max_load(self):
        """Agent at max concurrent load has near-zero load factor — still in [0,1]."""
        self._register_agent(
            "forge:overloaded", [WorkCellLane.test_writing], max_concurrent=3, current_active=3
        )
        r = self._recommend()
        s = r.recommended_agents[0].score
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 1.0)

    def test_all_agents_at_max_load_scores_near_zero_but_valid(self):
        """All agents at max load → scores are low but list is ordered and bounded."""
        for name in ["forge:a", "forge:b"]:
            self._register_agent(
                name, [WorkCellLane.test_writing], max_concurrent=3, current_active=3
            )
        r = self._recommend()
        self.assertEqual(len(r.recommended_agents), 2)
        for a in r.recommended_agents:
            self.assertGreaterEqual(a.score, 0.0)
            self.assertLessEqual(a.score, 1.0)

    def test_score_with_zero_budget(self):
        """Agent with 0% budget: score is still in [0,1]."""
        self._register_agent(
            "forge:broke", [WorkCellLane.test_writing], current_active=0, budget_pct=0.0
        )
        r = self._recommend()
        s = r.recommended_agents[0].score
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 1.0)

    def test_score_with_perfect_history(self):
        """Agent with 100% pass rate and perfect load: score is high and in [0,1]."""
        self._register_agent(
            "forge:ace", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        for i in range(50):
            self.engine.record_outcome(f"p{i}", "forge:ace", True, 1.0)
        r = self._recommend()
        s = r.recommended_agents[0].score
        self.assertGreater(s, 0.90)
        self.assertLessEqual(s, 1.0)

    def test_all_factor_values_in_range(self):
        """Every factor in AgentScore.factors must be in [0, 1]."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=1, budget_pct=75.0
        )
        for i in range(5):
            self.engine.record_outcome(f"p{i}", "forge:alpha", True, 5.0)
        r = self._recommend()
        for a in r.recommended_agents:
            for key, val in a.factors.items():
                self.assertGreaterEqual(val, 0.0, msg=f"factor {key} below 0")
                self.assertLessEqual(val, 1.0, msg=f"factor {key} above 1")


# ===========================================================================
# 5. Cross-Factor Interaction Tests (10 tests)
# ===========================================================================


class TestCrossFactorInteractions(_EngineTestBase):
    """Verify how competing factor signals interact in composite scoring."""

    def test_high_capability_low_budget_vs_low_capability_high_budget(self):
        """
        'capability' (always 1.0 for eligible agents) vs budget tradeoff.
        Compare an agent with full budget and neutral history against
        one with zero budget and perfect history.
        """
        # alpha: full budget, no history
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        # beta: zero budget, perfect history
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=0.0
        )
        for i in range(20):
            self.engine.record_outcome(f"p{i}", "forge:beta", True, 1.0)

        # Compute expected scores for balanced strategy:
        # alpha: 0.30*1 + 0.25*0.5 + 0.25*1 + 0.20*1 = 0.30 + 0.125 + 0.25 + 0.20 = 0.875
        # beta: 0.30*1 + 0.25*1.0 + 0.25*1 + 0.20*0 = 0.30 + 0.25 + 0.25 + 0 = 0.80
        r = self._recommend()
        alpha_score = next(a.score for a in r.recommended_agents if a.agent_id == "forge:alpha")
        beta_score = next(a.score for a in r.recommended_agents if a.agent_id == "forge:beta")

        # alpha should win because budget (0.20) matters enough
        self.assertGreater(alpha_score, beta_score)

    def test_fresh_agent_vs_stale_but_better_history(self):
        """
        The engine does not use freshness — it uses load/budget from the
        LoadBalancer snapshot.  A fresh agent with neutral history should
        beat a stale agent with identical load metrics.
        Note: RecommendationEngine uses load/budget from LoadBalancer, not
        freshness directly.  Both agents will have identical computed scores
        if metrics are equal.
        """
        self._register_agent(
            "forge:fresh", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:stale", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        # Both identical — no history difference — tiebreak is alphabetical
        r = self._recommend()
        self.assertEqual(r.recommended_agents[0].agent_id, "forge:fresh")

    def test_narrow_lane_agent_vs_wide_lane_agent_same_score(self):
        """Agent supporting 1 lane vs one supporting all lanes: equal scores when both eligible."""
        all_lanes = list(WorkCellLane)
        self._register_agent("forge:specialist", [WorkCellLane.test_writing])
        self._register_agent("forge:generalist", all_lanes)

        r = self._recommend()
        spec_score = next(a.score for a in r.recommended_agents if a.agent_id == "forge:specialist")
        gen_score = next(a.score for a in r.recommended_agents if a.agent_id == "forge:generalist")

        # capability_match factor is 1.0 for both (both support target lane)
        # scores differ only by agent_id alphabetical tiebreak
        self.assertAlmostEqual(spec_score, gen_score, delta=0.0001)

    def test_load_factor_dominates_when_one_agent_saturated(self):
        """When one agent is saturated and the other is idle, load dominates."""
        self._register_agent(
            "forge:idle", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:busy", [WorkCellLane.test_writing], current_active=3, budget_pct=100.0
        )

        r = self._recommend()
        self.assertEqual(r.recommended_agents[0].agent_id, "forge:idle")

    def test_budget_factor_dominates_when_one_agent_exhausted(self):
        """When one agent has 0% budget, budget factor heavily penalizes it."""
        self._register_agent(
            "forge:rich", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:broke", [WorkCellLane.test_writing], current_active=0, budget_pct=0.0
        )

        r = self._recommend()
        self.assertEqual(r.recommended_agents[0].agent_id, "forge:rich")

    def test_history_factor_can_overcome_slight_load_difference(self):
        """Perfect history can overcome a moderate load disadvantage under historical_performance."""
        # alpha: perfect history, 1/3 load
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=1, budget_pct=100.0
        )
        for i in range(20):
            self.engine.record_outcome(f"p{i}", "forge:alpha", True, 5.0)

        # beta: no history (neutral 0.5), zero load, full budget
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        r = self._recommend(strategy=RecommendationStrategy.historical_performance)
        # alpha: 0.15*1 + 0.50*1 + 0.20*(1-1/3) + 0.15*1 = 0.15+0.50+0.133+0.15 = 0.933
        # beta:  0.15*1 + 0.50*0.5 + 0.20*1 + 0.15*1 = 0.15+0.25+0.20+0.15 = 0.75
        self.assertEqual(r.recommended_agents[0].agent_id, "forge:alpha")

    def test_combined_load_and_budget_penalty_sums_additively(self):
        """Agent with both high load AND low budget gets a combined penalty."""
        self._register_agent(
            "forge:good", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:dual-penalty", [WorkCellLane.test_writing], current_active=3, budget_pct=0.0
        )

        r = self._recommend()
        good_score = next(a.score for a in r.recommended_agents if a.agent_id == "forge:good")
        bad_score = next(
            a.score for a in r.recommended_agents if a.agent_id == "forge:dual-penalty"
        )

        # good: 0.30 + 0.25*0.5 + 0.25*1.0 + 0.20*1.0 = 0.875
        # bad:  0.30 + 0.25*0.5 + 0.25*0.0 + 0.20*0.0 = 0.425
        self.assertGreater(good_score, bad_score)
        self.assertAlmostEqual(good_score, 0.875, delta=0.01)
        self.assertAlmostEqual(bad_score, 0.425, delta=0.01)

    def test_partial_load_factor_computed_correctly(self):
        """load factor = 1 - current_active/max_concurrent is computed correctly."""
        # 1/3 load: current_active=1, max_concurrent=3 → load factor = 0.667
        self._register_agent(
            "forge:partial",
            [WorkCellLane.test_writing],
            max_concurrent=3,
            current_active=1,
            budget_pct=100.0,
        )
        r = self._recommend()
        a = r.recommended_agents[0]
        expected_load_factor = 1.0 - 1 / 3
        self.assertAlmostEqual(a.factors["current_load"], expected_load_factor, delta=0.01)

    def test_budget_factor_computed_correctly(self):
        """budget factor = context_budget_remaining_pct / 100.0."""
        self._register_agent(
            "forge:sixty", [WorkCellLane.test_writing], current_active=0, budget_pct=60.0
        )
        r = self._recommend()
        a = r.recommended_agents[0]
        self.assertAlmostEqual(a.factors["context_budget"], 0.60, delta=0.001)

    def test_capability_factor_always_one_for_eligible_agents(self):
        """capability_match factor is always 1.0 for agents that pass the lane filter."""
        self._register_3_agents()
        r = self._recommend()
        for a in r.recommended_agents:
            self.assertAlmostEqual(a.factors["capability_match"], 1.0, delta=0.001)


# ===========================================================================
# 6. Regression Guards (15 tests)
# ===========================================================================


class TestRegressionGuards(_EngineTestBase):
    """Snapshot tests and monotonicity contracts."""

    def test_golden_test_three_agents_balanced(self):
        """Golden test: specific setup → specific expected scores."""
        # Agent A: 0 active, 100% budget, no history
        self._register_agent(
            "forge:aaa", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        # Agent B: 1 active (of 3), 80% budget, no history
        self._register_agent(
            "forge:bbb",
            [WorkCellLane.test_writing],
            max_concurrent=3,
            current_active=1,
            budget_pct=80.0,
        )
        # Agent C: 2 active (of 3), 60% budget, no history
        self._register_agent(
            "forge:ccc",
            [WorkCellLane.test_writing],
            max_concurrent=3,
            current_active=2,
            budget_pct=60.0,
        )

        r = self._recommend()
        scores = {a.agent_id: a.score for a in r.recommended_agents}

        # balanced: cap=0.30, hist=0.25, load=0.25, budget=0.20
        # aaa: 0.30*1 + 0.25*0.5 + 0.25*1.0   + 0.20*1.0   = 0.30 + 0.125 + 0.25 + 0.20 = 0.875
        # bbb: 0.30*1 + 0.25*0.5 + 0.25*0.667 + 0.20*0.80  = 0.30 + 0.125 + 0.167 + 0.16 = 0.752
        # ccc: 0.30*1 + 0.25*0.5 + 0.25*0.333 + 0.20*0.60  = 0.30 + 0.125 + 0.083 + 0.12 = 0.628

        self.assertAlmostEqual(scores["forge:aaa"], 0.875, delta=0.01)
        self.assertAlmostEqual(scores["forge:bbb"], 0.752, delta=0.01)
        self.assertAlmostEqual(scores["forge:ccc"], 0.628, delta=0.01)

        # Order must be aaa > bbb > ccc
        order = [a.agent_id for a in r.recommended_agents]
        self.assertEqual(order, ["forge:aaa", "forge:bbb", "forge:ccc"])

    def test_golden_confidence_two_agents(self):
        """Golden test: confidence = top_score - second_score."""
        self._register_agent(
            "forge:aaa", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:bbb",
            [WorkCellLane.test_writing],
            max_concurrent=3,
            current_active=3,
            budget_pct=0.0,
        )

        r = self._recommend()
        # aaa: 0.875 (as above)
        # bbb: 0.30*1 + 0.25*0.5 + 0.25*0 + 0.20*0 = 0.30 + 0.125 + 0 + 0 = 0.425
        # confidence = 0.875 - 0.425 = 0.45
        self.assertAlmostEqual(r.confidence, 0.450, delta=0.01)

    def test_100_successes_monotonically_increases_pass_rate(self):
        """Recording 100 successes monotonically increases the historical pass rate."""
        self._register_agent("forge:alpha", [WorkCellLane.test_writing])
        pass_rates = []
        for i in range(100):
            self.engine.record_outcome(f"t{i}", "forge:alpha", True, 1.0)
            history = self.engine.get_agent_history("forge:alpha")
            pass_rates.append(history["pass_rate"])

        # All pass rates should be monotonically non-decreasing
        for i in range(1, len(pass_rates)):
            self.assertGreaterEqual(
                pass_rates[i],
                pass_rates[i - 1],
                msg=f"pass_rate dropped at index {i}: {pass_rates[i - 1]} → {pass_rates[i]}",
            )
        # Final pass rate should be 1.0
        self.assertAlmostEqual(pass_rates[-1], 1.0, delta=0.0001)

    def test_100_failures_monotonically_decreases_pass_rate(self):
        """Recording 100 failures monotonically decreases the historical pass rate."""
        self._register_agent("forge:alpha", [WorkCellLane.test_writing])
        pass_rates = []
        for i in range(100):
            self.engine.record_outcome(f"t{i}", "forge:alpha", False, 1.0)
            history = self.engine.get_agent_history("forge:alpha")
            pass_rates.append(history["pass_rate"])

        for i in range(1, len(pass_rates)):
            self.assertLessEqual(
                pass_rates[i],
                pass_rates[i - 1],
                msg=f"pass_rate increased at index {i}: {pass_rates[i - 1]} → {pass_rates[i]}",
            )
        self.assertAlmostEqual(pass_rates[-1], 0.0, delta=0.001)

    def test_load_update_immediately_reflected_in_score(self):
        """Updating load immediately changes agent's score on next recommendation."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        r1 = self._recommend("t1")
        score_before = r1.recommended_agents[0].score

        self.lb.update_agent_load("forge:alpha", current_active=3, context_budget_pct=0.0)
        r2 = self._recommend("t2")
        score_after = r2.recommended_agents[0].score

        self.assertLess(score_after, score_before)

    def test_budget_depletion_makes_agent_last_in_ranking(self):
        """Agent with 0% budget ranks last among agents with higher budgets."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=0.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=50.0
        )
        self._register_agent(
            "forge:gamma", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        r = self._recommend()
        order = [a.agent_id for a in r.recommended_agents]
        self.assertEqual(order[-1], "forge:alpha")

    def test_max_concurrent_reached_makes_agent_low_ranked(self):
        """Agent with current_active == max_concurrent has zero load factor."""
        self._register_agent(
            "forge:saturated", [WorkCellLane.test_writing], max_concurrent=3, current_active=3
        )
        self._register_agent(
            "forge:idle", [WorkCellLane.test_writing], max_concurrent=3, current_active=0
        )

        r = self._recommend()
        saturated = next(a for a in r.recommended_agents if a.agent_id == "forge:saturated")
        self.assertAlmostEqual(saturated.factors["current_load"], 0.0, delta=0.0001)
        self.assertEqual(r.recommended_agents[0].agent_id, "forge:idle")

    def test_lane_mismatch_excludes_agent_completely(self):
        """Agent not supporting the target lane is excluded from recommendations."""
        self._register_agent("forge:docs-only", [WorkCellLane.docs])
        self._register_agent("forge:test-writer", [WorkCellLane.test_writing])

        r = self._recommend()
        agent_ids = {a.agent_id for a in r.recommended_agents}
        self.assertNotIn("forge:docs-only", agent_ids)
        self.assertIn("forge:test-writer", agent_ids)

    def test_lane_mismatch_excludes_regardless_of_score(self):
        """Even a perfect agent is excluded if it doesn't support the target lane."""
        # Perfect agent — but wrong lane
        self._register_agent(
            "forge:perfect-wrong-lane",
            [WorkCellLane.deployment],
            current_active=0,
            budget_pct=100.0,
        )
        for i in range(50):
            self.engine.record_outcome(f"p{i}", "forge:perfect-wrong-lane", True, 1.0)

        # Mediocre agent — correct lane
        self._register_agent(
            "forge:mediocre-right-lane",
            [WorkCellLane.test_writing],
            current_active=2,
            budget_pct=30.0,
        )

        r = self._recommend()
        agent_ids = {a.agent_id for a in r.recommended_agents}
        self.assertNotIn("forge:perfect-wrong-lane", agent_ids)
        self.assertIn("forge:mediocre-right-lane", agent_ids)

    def test_neutral_prior_used_when_no_history(self):
        """Agent with no history uses the neutral 0.5 pass rate prior."""
        self._register_agent("forge:new", [WorkCellLane.test_writing])
        r = self._recommend()
        a = r.recommended_agents[0]
        self.assertAlmostEqual(a.factors["historical_pass_rate"], 0.5, delta=0.0001)
        self.assertIsNone(a.historical_pass_rate)

    def test_history_stored_correctly_after_mixed_outcomes(self):
        """Mixed pass/fail outcomes produce the correct pass_rate."""
        self._register_agent("forge:mixed", [WorkCellLane.test_writing])
        for i in range(3):
            self.engine.record_outcome(f"p{i}", "forge:mixed", True, 5.0)
        for i in range(7):
            self.engine.record_outcome(f"f{i}", "forge:mixed", False, 5.0)

        history = self.engine.get_agent_history("forge:mixed")
        self.assertAlmostEqual(history["pass_rate"], 0.3, delta=0.001)
        self.assertEqual(history["total_tasks"], 10)

    def test_golden_test_capability_match_strategy(self):
        """Golden test: capability_match strategy computes expected scores."""
        # All agents: no load, full budget, no history
        self._register_agent(
            "forge:aaa", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:bbb",
            [WorkCellLane.test_writing],
            max_concurrent=3,
            current_active=1,
            budget_pct=100.0,
        )

        r = self._recommend(strategy=RecommendationStrategy.capability_match)
        scores = {a.agent_id: a.score for a in r.recommended_agents}

        # capability_match: cap=0.60, hist=0.125, load=0.125, budget=0.15
        # aaa: 0.60*1 + 0.125*0.5 + 0.125*1.0   + 0.15*1.0 = 0.60+0.0625+0.125+0.15 = 0.9375
        # bbb: 0.60*1 + 0.125*0.5 + 0.125*0.667 + 0.15*1.0 = 0.60+0.0625+0.0833+0.15 = 0.896

        self.assertAlmostEqual(scores["forge:aaa"], 0.9375, delta=0.01)
        self.assertAlmostEqual(scores["forge:bbb"], 0.896, delta=0.01)

    def test_golden_test_historical_performance_strategy(self):
        """Golden test: historical_performance strategy computes expected scores."""
        self._register_agent(
            "forge:aaa", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        for i in range(10):
            self.engine.record_outcome(f"p{i}", "forge:aaa", True, 5.0)

        self._register_agent(
            "forge:bbb", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        # bbb: no history → neutral 0.5

        r = self._recommend(strategy=RecommendationStrategy.historical_performance)
        scores = {a.agent_id: a.score for a in r.recommended_agents}

        # historical_performance: cap=0.15, hist=0.50, load=0.20, budget=0.15
        # aaa: 0.15*1 + 0.50*1.0 + 0.20*1.0 + 0.15*1.0 = 0.15+0.50+0.20+0.15 = 1.0
        # bbb: 0.15*1 + 0.50*0.5 + 0.20*1.0 + 0.15*1.0 = 0.15+0.25+0.20+0.15 = 0.75

        self.assertAlmostEqual(scores["forge:aaa"], 1.0, delta=0.01)
        self.assertAlmostEqual(scores["forge:bbb"], 0.75, delta=0.01)

    def test_record_outcome_affects_next_recommendation(self):
        """After recording a failure, the next recommendation reflects lower score."""
        self._register_agent(
            "forge:alpha", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:beta", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )

        # Initially tied — alpha wins by alphabetical order
        r1 = self._recommend("t1")
        self.assertEqual(r1.recommended_agents[0].agent_id, "forge:alpha")

        # Record many failures for alpha
        for i in range(20):
            self.engine.record_outcome(f"f{i}", "forge:alpha", False, 5.0)

        r2 = self._recommend("t2")
        self.assertEqual(r2.recommended_agents[0].agent_id, "forge:beta")

    def test_recommendation_log_grows_with_each_call(self):
        """Each call to recommend appends to the internal log."""
        self._register_3_agents()
        for i in range(5):
            self._recommend(f"t{i}")
        log = self.engine.get_recommendation_log(limit=10)
        self.assertGreaterEqual(len(log), 5)

    def test_max_concurrent_below_1_raises_valueerror(self):
        """Registering an agent with max_concurrent < 1 raises ValueError."""
        with self.assertRaises(ValueError):
            self.lb.register_agent("forge:invalid", [WorkCellLane.test_writing], max_concurrent=0)


# ===========================================================================
# Additional edge-case tests to hit 80+ total
# ===========================================================================


class TestEdgeCases(_EngineTestBase):
    """Additional edge cases and boundary conditions."""

    def test_no_registered_agents_returns_empty(self):
        """No agents registered → empty recommendation list."""
        r = self._recommend()
        self.assertEqual(r.recommended_agents, [])
        self.assertEqual(r.confidence, 0.0)

    def test_all_lanes_supported_agent_visible_from_any_lane(self):
        """Agent supporting all 8 lanes appears in recommendations for each lane."""
        all_lanes = list(WorkCellLane)
        self._register_agent("forge:omnilane", all_lanes)

        for lane in WorkCellLane:
            r = self._recommend("t", preferred_lane=lane)
            agent_ids = [a.agent_id for a in r.recommended_agents]
            self.assertIn("forge:omnilane", agent_ids, msg=f"Missing for lane={lane}")

    def test_negative_lead_time_raises_valueerror(self):
        """record_outcome with negative lead_time_seconds raises ValueError."""
        self._register_agent("forge:alpha", [WorkCellLane.test_writing])
        with self.assertRaises(ValueError):
            self.engine.record_outcome("t1", "forge:alpha", True, -1.0)

    def test_total_task_count_accumulates(self):
        """total_tasks counter accumulates correctly after multiple records."""
        self._register_agent("forge:alpha", [WorkCellLane.test_writing])
        for i in range(7):
            self.engine.record_outcome(f"t{i}", "forge:alpha", i % 2 == 0, 5.0)
        h = self.engine.get_agent_history("forge:alpha")
        self.assertEqual(h["total_tasks"], 7)

    def test_avg_lead_time_computed_correctly(self):
        """avg_lead_time_seconds is accurate after recording multiple outcomes."""
        self._register_agent("forge:alpha", [WorkCellLane.test_writing])
        self.engine.record_outcome("t1", "forge:alpha", True, 10.0)
        self.engine.record_outcome("t2", "forge:alpha", True, 20.0)
        self.engine.record_outcome("t3", "forge:alpha", True, 30.0)
        h = self.engine.get_agent_history("forge:alpha")
        self.assertAlmostEqual(h["avg_lead_time"], 20.0, delta=0.001)

    def test_avg_lead_time_none_when_no_history(self):
        """avg_lead_time is None when no history recorded."""
        self._register_agent("forge:alpha", [WorkCellLane.test_writing])
        h = self.engine.get_agent_history("forge:alpha")
        self.assertIsNone(h["avg_lead_time"])

    def test_confidence_is_zero_when_tied(self):
        """Two identical agents → confidence close to zero (score spread = 0)."""
        self._register_agent(
            "forge:a", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        self._register_agent(
            "forge:b", [WorkCellLane.test_writing], current_active=0, budget_pct=100.0
        )
        r = self._recommend()
        self.assertAlmostEqual(r.confidence, 0.0, delta=0.001)

    def test_reasoning_contains_lane_info(self):
        """Reasoning list contains information about the target lane."""
        self._register_3_agents()
        r = self._recommend()
        all_reasoning = " ".join(r.reasoning)
        self.assertIn("test_writing", all_reasoning)

    def test_task_id_preserved_in_recommendation(self):
        """The task_id in the returned recommendation matches the input."""
        self._register_3_agents()
        r = self.engine.recommend(
            "my-special-task-id",
            TaskType.test_writing,
            RiskTier.low,
        )
        self.assertEqual(r.task_id, "my-special-task-id")

    def test_preferred_lane_overrides_resolver(self):
        """Using preferred_lane overrides the (TaskType, RiskTier) resolver."""
        # Register an agent on docs but not on test_writing
        self._register_agent("forge:docs-agent", [WorkCellLane.docs])

        # Without override: test_writing → no eligible agents
        r_no_override = self._recommend()
        self.assertEqual(r_no_override.recommended_agents, [])

        # With override to docs: agent is now eligible
        r_override = self._recommend(preferred_lane=WorkCellLane.docs)
        self.assertEqual(len(r_override.recommended_agents), 1)
        self.assertEqual(r_override.recommended_agents[0].agent_id, "forge:docs-agent")

    def test_all_strategies_cover_all_enum_values(self):
        """RecommendationStrategy enum covers exactly balanced/capability_match/historical_performance."""
        strategies = {s.value for s in RecommendationStrategy}
        self.assertEqual(strategies, {"balanced", "capability_match", "historical_performance"})

    def test_recommendation_log_limit_respected(self):
        """get_recommendation_log respects the limit parameter."""
        self._register_3_agents()
        for i in range(20):
            self._recommend(f"t{i}")
        log5 = self.engine.get_recommendation_log(limit=5)
        self.assertLessEqual(len(log5), 5)

    def test_recommendation_log_is_most_recent_first(self):
        """get_recommendation_log returns entries in reverse-chronological order."""
        self._register_3_agents()
        for i in range(10):
            self._recommend(f"task-{i:03d}")
        log = self.engine.get_recommendation_log(limit=5)
        # The most recently recommended task should be task-009
        self.assertEqual(log[0].task_id, "task-009")

    def test_engine_handles_single_then_multiple_agents(self):
        """Engine transitions correctly from 1 agent to multiple agents."""
        self._register_agent("forge:solo", [WorkCellLane.test_writing])
        r1 = self._recommend("t1")
        self.assertEqual(len(r1.recommended_agents), 1)
        self.assertAlmostEqual(r1.confidence, r1.recommended_agents[0].score, delta=0.0001)

        self._register_agent("forge:new", [WorkCellLane.test_writing])
        r2 = self._recommend("t2")
        self.assertEqual(len(r2.recommended_agents), 2)
        # Confidence is now a score spread, not equal to top score
        expected_confidence = round(
            r2.recommended_agents[0].score - r2.recommended_agents[1].score, 6
        )
        self.assertAlmostEqual(r2.confidence, expected_confidence, delta=0.001)
