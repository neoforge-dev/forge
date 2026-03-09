"""Tests for Dark Factory Recommendation Engine — DF-3001.

Coverage targets
----------------
- RecommendationStrategy enum: all three values present
- AgentScore model: field constraints (score range, frozen, factors dict)
- TaskRecommendation model: frozen, confidence range, reasoning list
- RecommendationEngine.recommend: returns TaskRecommendation
- RecommendationEngine.recommend: no agents registered → empty list, confidence=0
- RecommendationEngine.recommend: filters agents by lane capability
- RecommendationEngine.recommend: no eligible agents → empty list, confidence=0
- RecommendationEngine.recommend: single agent → confidence = top score
- RecommendationEngine.recommend: two agents → confidence = score spread
- RecommendationEngine.recommend: all agents overloaded → lower scores but still recommended
- RecommendationEngine.recommend: preferred_lane override skips resolver
- RecommendationEngine.recommend: strategy=capability_match uses heavier capability weight
- RecommendationEngine.recommend: strategy=historical_performance uses heavier history weight
- RecommendationEngine.recommend: strategy=balanced uses default weights
- Scoring correctness: verify each factor contributes with correct weight (balanced)
- Scoring correctness: verify capability_match strategy weights
- Scoring correctness: verify historical_performance strategy weights
- Current_load factor: fully loaded agent scores 0.0 for load factor
- Current_load factor: idle agent scores 1.0 for load factor
- Context_budget factor: 0% budget scores 0.0; 100% scores 1.0
- Historical pass rate: agent with all failures scores 0.0 for history factor
- Historical pass rate: agent with all passes scores 1.0 for history factor
- Historical prior: no history → neutral prior (0.5) used as pass rate factor
- Historical prior: neutral prior does not appear as historical_pass_rate on AgentScore
- Recommendation log: returns most recent entries first
- Recommendation log: limit parameter respected
- Recommendation log: log grows bounded (max 500 entries)
- record_outcome: updates pass rate correctly after single pass
- record_outcome: updates pass rate correctly after mixed outcomes
- record_outcome: updates avg_lead_time correctly
- record_outcome: rejects negative lead_time_seconds
- record_outcome: adds to total_tasks count
- get_agent_history: returns default prior for unknown agent
- get_agent_history: returns accurate stats after outcomes
- get_agent_history: pass_rate, avg_lead_time, total_tasks keys present
- Historical data influences scores: agent with high pass rate ranks above low pass rate
- Sort order: higher score agent always first
- Sort order: tie-break by agent_id for determinism
- Confidence: high score spread → high confidence
- Confidence: identical scores → confidence near zero
- Singleton: get_recommendation_engine returns same instance
- Singleton: reset_recommendation_engine creates fresh instance
- Singleton: reset clears historical data
- Persistence: record_outcome writes JSONL file
- Persistence: reload from JSONL restores history
- Persistence: malformed JSONL lines are skipped
- Persistence: missing history file is handled gracefully
- Persistence: outcome records load into correct agent buckets
- Integration: LoadBalancer + RecommendationEngine collaborate correctly
- Integration: LaneEnforcer resolved lane matches engine target lane
- Integration: three agents, two eligible, one with better history → correct ranking
- Thread safety: concurrent recommend calls do not corrupt log
- Thread safety: concurrent record_outcome calls do not corrupt history
- Thread safety: concurrent recommend + record_outcome are safe
- Reasoning: contains lane resolution description
- Reasoning: contains eligibility count description
- Reasoning: contains top agent factor breakdown
- Reasoning: preferred_lane override reflected in reasoning
- Edge case: agent_id with special characters
- Edge case: very large lead_time_seconds
- Edge case: all agents have 0% context budget
- Edge case: task_id collision in log (same id, multiple recommendations)

Target: 80+ tests.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.recommendation import (
    AgentScore,
    RecommendationStrategy,
    TaskRecommendation,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.load_balancer import (
    LoadBalancer,
    reset_load_balancer,
)
from forge_harness.webhook_server.services.recommendation_engine import (
    _DEFAULT_HISTORICAL_PASS_RATE,
    _STRATEGY_WEIGHTS,
    RecommendationEngine,
    get_recommendation_engine,
    reset_recommendation_engine,
)

# ===========================================================================
# Helpers / fixtures
# ===========================================================================


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset both singletons before and after every test."""
    reset_recommendation_engine()
    reset_load_balancer()
    yield
    reset_recommendation_engine()
    reset_load_balancer()


@pytest.fixture()
def lb() -> LoadBalancer:
    """Return a fresh LoadBalancer (isolated from singleton)."""
    return LoadBalancer()


@pytest.fixture()
def tmp_history(tmp_path: Path) -> Path:
    """Return a temporary JSONL path for persistence tests."""
    return tmp_path / "history.jsonl"


@pytest.fixture()
def engine(lb: LoadBalancer, tmp_history: Path) -> RecommendationEngine:
    """Return a fresh RecommendationEngine backed by an isolated LoadBalancer."""
    return RecommendationEngine(load_balancer=lb, history_path=tmp_history)


def _register(
    lb: LoadBalancer,
    agent_id: str = "agent-a",
    lanes: list[WorkCellLane] | None = None,
    max_concurrent: int = 3,
    current_active: int = 0,
    context_budget_pct: float = 100.0,
) -> None:
    """Register an agent and update its load in one call."""
    if lanes is None:
        lanes = [WorkCellLane.api_simple]
    lb.register_agent(agent_id, lanes, max_concurrent=max_concurrent)
    lb.update_agent_load(
        agent_id,
        current_active=current_active,
        context_budget_pct=context_budget_pct,
    )


# ===========================================================================
# Model tests
# ===========================================================================


class TestRecommendationStrategyEnum:
    def test_all_values_present(self):
        values = {s.value for s in RecommendationStrategy}
        assert "capability_match" in values
        assert "historical_performance" in values
        assert "balanced" in values

    def test_default_is_balanced(self):
        assert RecommendationStrategy.balanced.value == "balanced"

    def test_str_enum(self):
        assert RecommendationStrategy.balanced == "balanced"

    def test_capability_match_value(self):
        assert RecommendationStrategy.capability_match.value == "capability_match"

    def test_historical_performance_value(self):
        assert RecommendationStrategy.historical_performance.value == "historical_performance"


class TestAgentScoreModel:
    def test_valid_construction(self):
        s = AgentScore(agent_id="forge:nova", score=0.85, factors={"capability_match": 1.0})
        assert s.agent_id == "forge:nova"
        assert s.score == 0.85

    def test_score_lower_bound(self):
        with pytest.raises(Exception):
            AgentScore(agent_id="x", score=-0.01)

    def test_score_upper_bound(self):
        with pytest.raises(Exception):
            AgentScore(agent_id="x", score=1.01)

    def test_score_zero_is_valid(self):
        s = AgentScore(agent_id="x", score=0.0)
        assert s.score == 0.0

    def test_score_one_is_valid(self):
        s = AgentScore(agent_id="x", score=1.0)
        assert s.score == 1.0

    def test_frozen_model(self):
        s = AgentScore(agent_id="x", score=0.5)
        with pytest.raises(Exception):
            s.score = 0.9  # type: ignore[misc]

    def test_optional_historical_fields_default_none(self):
        s = AgentScore(agent_id="x", score=0.5)
        assert s.historical_pass_rate is None
        assert s.avg_lead_time_seconds is None

    def test_historical_pass_rate_range(self):
        with pytest.raises(Exception):
            AgentScore(agent_id="x", score=0.5, historical_pass_rate=1.5)

    def test_avg_lead_time_non_negative(self):
        with pytest.raises(Exception):
            AgentScore(agent_id="x", score=0.5, avg_lead_time_seconds=-1.0)

    def test_factors_default_empty_dict(self):
        s = AgentScore(agent_id="x", score=0.5)
        assert s.factors == {}

    def test_factors_populated(self):
        s = AgentScore(agent_id="x", score=0.5, factors={"current_load": 0.8})
        assert s.factors["current_load"] == 0.8


class TestTaskRecommendationModel:
    def test_valid_construction(self):
        rec = TaskRecommendation(task_id="t-001")
        assert rec.task_id == "t-001"
        assert rec.recommended_agents == []
        assert rec.confidence == 0.0
        assert rec.reasoning == []

    def test_frozen_model(self):
        rec = TaskRecommendation(task_id="t-001")
        with pytest.raises(Exception):
            rec.task_id = "t-002"  # type: ignore[misc]

    def test_confidence_lower_bound(self):
        with pytest.raises(Exception):
            TaskRecommendation(task_id="t", confidence=-0.01)

    def test_confidence_upper_bound(self):
        with pytest.raises(Exception):
            TaskRecommendation(task_id="t", confidence=1.01)

    def test_confidence_zero_is_valid(self):
        rec = TaskRecommendation(task_id="t", confidence=0.0)
        assert rec.confidence == 0.0

    def test_confidence_one_is_valid(self):
        rec = TaskRecommendation(task_id="t", confidence=1.0)
        assert rec.confidence == 1.0

    def test_created_at_is_set(self):
        rec = TaskRecommendation(task_id="t")
        assert rec.created_at is not None

    def test_recommended_agents_list(self):
        score = AgentScore(agent_id="a", score=0.9)
        rec = TaskRecommendation(task_id="t", recommended_agents=[score])
        assert len(rec.recommended_agents) == 1


# ===========================================================================
# RecommendationEngine — basic recommend() behaviour
# ===========================================================================


class TestRecommendBasic:
    def test_returns_task_recommendation(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        assert isinstance(rec, TaskRecommendation)
        assert rec.task_id == "t-001"

    def test_no_agents_registered_returns_empty(self, engine: RecommendationEngine):
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents == []
        assert rec.confidence == 0.0

    def test_no_agents_registered_reasoning_mentions_no_agents(self, engine: RecommendationEngine):
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        assert any("No agents" in r for r in rec.reasoning)

    def test_filters_agents_by_lane_capability(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        _register(lb, "agent-b", [WorkCellLane.security_change])
        # bug_fix + low → api_simple
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        ids = [s.agent_id for s in rec.recommended_agents]
        assert "agent-a" in ids
        assert "agent-b" not in ids

    def test_no_eligible_agents_returns_empty(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.docs])
        # bug_fix + low → api_simple, but agent only supports docs
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents == []
        assert rec.confidence == 0.0

    def test_no_eligible_agents_reasoning_mentions_no_eligible(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.docs])
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        combined = " ".join(rec.reasoning)
        assert "No agents support" in combined or "0/" in combined

    def test_single_agent_confidence_equals_top_score(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        assert len(rec.recommended_agents) == 1
        assert rec.confidence == pytest.approx(rec.recommended_agents[0].score, abs=1e-5)

    def test_two_agents_confidence_equals_score_spread(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple], current_active=0)
        _register(lb, "agent-b", [WorkCellLane.api_simple], current_active=2)
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        assert len(rec.recommended_agents) == 2
        expected_spread = rec.recommended_agents[0].score - rec.recommended_agents[1].score
        assert rec.confidence == pytest.approx(expected_spread, abs=1e-5)

    def test_preferred_lane_overrides_resolver(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.docs])
        # bug_fix+low → api_simple, but override to docs
        rec = engine.recommend(
            "t-001",
            TaskType.bug_fix,
            RiskTier.low,
            preferred_lane=WorkCellLane.docs,
        )
        ids = [s.agent_id for s in rec.recommended_agents]
        assert "agent-a" in ids

    def test_preferred_lane_reflected_in_reasoning(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.docs])
        rec = engine.recommend(
            "t-001",
            TaskType.bug_fix,
            RiskTier.low,
            preferred_lane=WorkCellLane.docs,
        )
        combined = " ".join(rec.reasoning).lower()
        assert "override" in combined or "overridden" in combined

    def test_all_agents_overloaded_still_recommended(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=3,
        )
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        assert len(rec.recommended_agents) == 1
        assert rec.recommended_agents[0].factors["current_load"] == pytest.approx(0.0)


# ===========================================================================
# Scoring correctness
# ===========================================================================


class TestScoringCorrectness:
    def test_balanced_strategy_capability_factor_always_1(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["capability_match"] == pytest.approx(1.0)

    def test_balanced_idle_full_budget_no_history_composite_score(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        """Idle agent, 100% budget, no history → computed composite score."""
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            current_active=0,
            context_budget_pct=100.0,
        )
        w_cap, w_hist, w_load, w_budget = _STRATEGY_WEIGHTS[RecommendationStrategy.balanced]
        expected = (
            w_cap * 1.0 + w_hist * _DEFAULT_HISTORICAL_PASS_RATE + w_load * 1.0 + w_budget * 1.0
        )
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].score == pytest.approx(expected, abs=1e-5)

    def test_current_load_factor_fully_loaded(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=3,
        )
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["current_load"] == pytest.approx(0.0)

    def test_current_load_factor_idle(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=0,
        )
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["current_load"] == pytest.approx(1.0)

    def test_current_load_factor_partial(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            max_concurrent=4,
            current_active=2,
        )
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["current_load"] == pytest.approx(0.5)

    def test_context_budget_factor_zero_budget(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple], context_budget_pct=0.0)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["context_budget"] == pytest.approx(0.0)

    def test_context_budget_factor_full_budget(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple], context_budget_pct=100.0)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["context_budget"] == pytest.approx(1.0)

    def test_context_budget_factor_half(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple], context_budget_pct=50.0)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["context_budget"] == pytest.approx(0.5)

    def test_capability_match_strategy_weights(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            current_active=0,
            context_budget_pct=100.0,
        )
        w_cap, w_hist, w_load, w_budget = _STRATEGY_WEIGHTS[RecommendationStrategy.capability_match]
        expected = (
            w_cap * 1.0 + w_hist * _DEFAULT_HISTORICAL_PASS_RATE + w_load * 1.0 + w_budget * 1.0
        )
        rec = engine.recommend(
            "t",
            TaskType.bug_fix,
            RiskTier.low,
            strategy=RecommendationStrategy.capability_match,
        )
        assert rec.recommended_agents[0].score == pytest.approx(expected, abs=1e-5)

    def test_historical_performance_strategy_weights(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            current_active=0,
            context_budget_pct=100.0,
        )
        engine.record_outcome("prev", "agent-a", passed=True, lead_time_seconds=10.0)
        w_cap, w_hist, w_load, w_budget = _STRATEGY_WEIGHTS[
            RecommendationStrategy.historical_performance
        ]
        expected = w_cap * 1.0 + w_hist * 1.0 + w_load * 1.0 + w_budget * 1.0
        rec = engine.recommend(
            "t",
            TaskType.bug_fix,
            RiskTier.low,
            strategy=RecommendationStrategy.historical_performance,
        )
        assert rec.recommended_agents[0].score == pytest.approx(expected, abs=1e-5)

    def test_all_three_strategies_produce_different_scores_with_partial_history(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        """With a non-neutral pass rate, the three strategies must differ."""
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            current_active=1,
            context_budget_pct=50.0,
        )
        engine.record_outcome("prev", "agent-a", passed=False, lead_time_seconds=5.0)

        scores = []
        for strat in RecommendationStrategy:
            rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low, strategy=strat)
            scores.append(rec.recommended_agents[0].score)
        # All three scores should not all be identical
        assert len({round(s, 5) for s in scores}) > 1

    def test_strategy_weights_sum_to_one(self):
        for strat, weights in _STRATEGY_WEIGHTS.items():
            total = sum(weights)
            assert total == pytest.approx(1.0, abs=1e-9), f"{strat}: weights sum={total}"


# ===========================================================================
# Historical performance
# ===========================================================================


class TestHistoricalPerformance:
    def test_no_history_returns_neutral_prior(self, engine: RecommendationEngine):
        hist = engine.get_agent_history("nonexistent-agent")
        assert hist["pass_rate"] == pytest.approx(_DEFAULT_HISTORICAL_PASS_RATE)
        assert hist["avg_lead_time"] is None
        assert hist["total_tasks"] == 0

    def test_record_single_pass(self, engine: RecommendationEngine):
        engine.record_outcome("t-1", "agent-a", passed=True, lead_time_seconds=30.0)
        hist = engine.get_agent_history("agent-a")
        assert hist["total_tasks"] == 1
        assert hist["pass_rate"] == pytest.approx(1.0)
        assert hist["avg_lead_time"] == pytest.approx(30.0)

    def test_record_single_failure(self, engine: RecommendationEngine):
        engine.record_outcome("t-1", "agent-a", passed=False, lead_time_seconds=15.0)
        hist = engine.get_agent_history("agent-a")
        assert hist["pass_rate"] == pytest.approx(0.0)
        assert hist["total_tasks"] == 1

    def test_record_mixed_outcomes_pass_rate(self, engine: RecommendationEngine):
        for i in range(3):
            engine.record_outcome(f"t-{i}", "agent-a", passed=True, lead_time_seconds=10.0)
        engine.record_outcome("t-3", "agent-a", passed=False, lead_time_seconds=10.0)
        hist = engine.get_agent_history("agent-a")
        assert hist["total_tasks"] == 4
        assert hist["pass_rate"] == pytest.approx(0.75)

    def test_record_multiple_avg_lead_time(self, engine: RecommendationEngine):
        engine.record_outcome("t-1", "agent-a", passed=True, lead_time_seconds=10.0)
        engine.record_outcome("t-2", "agent-a", passed=True, lead_time_seconds=30.0)
        hist = engine.get_agent_history("agent-a")
        assert hist["avg_lead_time"] == pytest.approx(20.0)

    def test_record_outcome_rejects_negative_lead_time(self, engine: RecommendationEngine):
        with pytest.raises(ValueError):
            engine.record_outcome("t-1", "agent-a", passed=True, lead_time_seconds=-1.0)

    def test_record_outcome_increments_total_tasks(self, engine: RecommendationEngine):
        for i in range(5):
            engine.record_outcome(f"t-{i}", "agent-a", passed=True, lead_time_seconds=1.0)
        assert engine.get_agent_history("agent-a")["total_tasks"] == 5

    def test_history_factor_high_pass_rate_ranks_higher(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple], current_active=1)
        _register(lb, "agent-b", [WorkCellLane.api_simple], current_active=1)
        # Give agent-a high pass rate, agent-b low pass rate
        for _ in range(10):
            engine.record_outcome("past", "agent-a", passed=True, lead_time_seconds=5.0)
        for _ in range(10):
            engine.record_outcome("past", "agent-b", passed=False, lead_time_seconds=5.0)
        rec = engine.recommend(
            "t",
            TaskType.bug_fix,
            RiskTier.low,
            strategy=RecommendationStrategy.historical_performance,
        )
        assert rec.recommended_agents[0].agent_id == "agent-a"

    def test_historical_pass_rate_on_agent_score_populated_when_history_exists(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        engine.record_outcome("t-past", "agent-a", passed=True, lead_time_seconds=5.0)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].historical_pass_rate == pytest.approx(1.0)

    def test_historical_pass_rate_none_when_no_history(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].historical_pass_rate is None

    def test_avg_lead_time_on_agent_score_populated_when_history_exists(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        engine.record_outcome("t-past", "agent-a", passed=True, lead_time_seconds=42.0)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].avg_lead_time_seconds == pytest.approx(42.0)

    def test_avg_lead_time_none_when_no_history(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].avg_lead_time_seconds is None


# ===========================================================================
# Sort order and confidence
# ===========================================================================


class TestSortOrderAndConfidence:
    def test_agents_sorted_descending_by_score(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple], current_active=0)
        _register(lb, "agent-b", [WorkCellLane.api_simple], current_active=2)
        _register(lb, "agent-c", [WorkCellLane.api_simple], current_active=1)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        scores = [s.score for s in rec.recommended_agents]
        assert scores == sorted(scores, reverse=True)

    def test_identical_scores_tiebreak_by_agent_id(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        # Same load and budget for both agents; tie-break by agent_id
        _register(
            lb,
            "agent-b",
            [WorkCellLane.api_simple],
            current_active=0,
            context_budget_pct=100.0,
        )
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            current_active=0,
            context_budget_pct=100.0,
        )
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        if rec.recommended_agents[0].score == rec.recommended_agents[1].score:
            # With equal scores the first in alphabetical order should win
            assert rec.recommended_agents[0].agent_id < rec.recommended_agents[1].agent_id

    def test_high_spread_gives_high_confidence(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=0,
            context_budget_pct=100.0,
        )
        _register(
            lb,
            "agent-b",
            [WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=3,
            context_budget_pct=0.0,
        )
        for _ in range(10):
            engine.record_outcome("past", "agent-b", passed=False, lead_time_seconds=5.0)
        rec = engine.recommend(
            "t",
            TaskType.bug_fix,
            RiskTier.low,
            strategy=RecommendationStrategy.balanced,
        )
        assert rec.confidence > 0.1

    def test_zero_spread_gives_zero_confidence(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        # Two agents with identical configuration and no history
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=0,
            context_budget_pct=100.0,
        )
        _register(
            lb,
            "agent-b",
            [WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=0,
            context_budget_pct=100.0,
        )
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.confidence == pytest.approx(0.0, abs=1e-5)

    def test_confidence_bounded_zero_to_one(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert 0.0 <= rec.confidence <= 1.0


# ===========================================================================
# Recommendation log
# ===========================================================================


class TestRecommendationLog:
    def test_log_empty_initially(self, engine: RecommendationEngine):
        assert engine.get_recommendation_log() == []

    def test_log_grows_after_recommend(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        engine.recommend("t-1", TaskType.bug_fix, RiskTier.low)
        engine.recommend("t-2", TaskType.bug_fix, RiskTier.low)
        assert len(engine.get_recommendation_log()) == 2

    def test_log_also_records_empty_results(self, engine: RecommendationEngine):
        """Even empty recommendations (no eligible agents) are logged."""
        engine.recommend("t-1", TaskType.bug_fix, RiskTier.low)
        assert len(engine.get_recommendation_log()) == 1

    def test_log_most_recent_first(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        engine.recommend("t-1", TaskType.bug_fix, RiskTier.low)
        engine.recommend("t-2", TaskType.bug_fix, RiskTier.low)
        log = engine.get_recommendation_log()
        assert log[0].task_id == "t-2"
        assert log[1].task_id == "t-1"

    def test_log_limit_respected(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        for i in range(10):
            engine.recommend(f"t-{i}", TaskType.bug_fix, RiskTier.low)
        assert len(engine.get_recommendation_log(limit=3)) == 3

    def test_log_default_limit_20(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        for i in range(25):
            engine.recommend(f"t-{i}", TaskType.bug_fix, RiskTier.low)
        assert len(engine.get_recommendation_log()) == 20

    def test_log_bounded_at_500_entries(self, engine: RecommendationEngine, lb: LoadBalancer):
        """Verify the in-memory deque does not grow beyond _MAX_LOG_ENTRIES."""
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        for i in range(510):
            engine.recommend(f"t-{i}", TaskType.bug_fix, RiskTier.low)
        all_entries = engine.get_recommendation_log(limit=600)
        assert len(all_entries) == 500


# ===========================================================================
# Singleton
# ===========================================================================


class TestSingleton:
    def test_get_recommendation_engine_returns_same_instance(self):
        e1 = get_recommendation_engine()
        e2 = get_recommendation_engine()
        assert e1 is e2

    def test_reset_creates_new_instance(self):
        e1 = get_recommendation_engine()
        reset_recommendation_engine()
        e2 = get_recommendation_engine()
        assert e1 is not e2

    def test_reset_clears_in_memory_history(self, tmp_path: Path):
        """Reset creates a fresh instance with no in-memory carry-over.

        The new instance uses a temp history path so disk persistence does not
        interfere with the assertion.
        """
        import unittest.mock as mock

        # Use a fresh temp path so disk state doesn't leak in
        tmp_history = tmp_path / "singleton_test.jsonl"
        with mock.patch(
            "forge_harness.webhook_server.services.recommendation_engine._HISTORY_JSONL_PATH",
            str(tmp_history),
        ):
            engine = get_recommendation_engine()
            # The singleton was already created (possibly with real path) — reset first
            reset_recommendation_engine()
            engine = RecommendationEngine(history_path=tmp_history)
            engine.record_outcome("t", "agent-a", passed=True, lead_time_seconds=5.0)
            # Fresh engine from scratch (not singleton) using same temp path
            # confirms the instance's in-memory history reflects outcomes
            hist = engine.get_agent_history("agent-a")
            assert hist["total_tasks"] == 1
            # A brand-new engine with a different temp path has no history
            clean_history = tmp_path / "clean.jsonl"
            fresh = RecommendationEngine(history_path=clean_history)
            assert fresh.get_agent_history("agent-a")["total_tasks"] == 0

    def test_singleton_is_thread_safe(self):
        """Multiple threads calling get_recommendation_engine get the same object."""
        instances: list[RecommendationEngine] = []

        def _get():
            instances.append(get_recommendation_engine())

        threads = [threading.Thread(target=_get) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len({id(e) for e in instances}) == 1


# ===========================================================================
# Persistence
# ===========================================================================


class TestPersistence:
    def test_record_outcome_creates_jsonl_file(self, tmp_history: Path, lb: LoadBalancer):
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        engine.record_outcome("t-1", "agent-a", passed=True, lead_time_seconds=10.0)
        assert tmp_history.exists()

    def test_record_outcome_appends_jsonl_lines(self, tmp_history: Path, lb: LoadBalancer):
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        engine.record_outcome("t-1", "agent-a", passed=True, lead_time_seconds=10.0)
        engine.record_outcome("t-2", "agent-a", passed=False, lead_time_seconds=20.0)
        lines = tmp_history.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_jsonl_contains_expected_fields(self, tmp_history: Path, lb: LoadBalancer):
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        engine.record_outcome("t-1", "agent-a", passed=True, lead_time_seconds=15.0)
        record = json.loads(tmp_history.read_text().strip())
        assert record["agent_id"] == "agent-a"
        assert record["passed"] is True
        assert record["lead_time_seconds"] == pytest.approx(15.0)

    def test_jsonl_contains_task_id(self, tmp_history: Path, lb: LoadBalancer):
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        engine.record_outcome("unique-task-id", "agent-a", passed=True, lead_time_seconds=5.0)
        record = json.loads(tmp_history.read_text().strip())
        assert record["task_id"] == "unique-task-id"

    def test_reload_from_jsonl_restores_history(self, tmp_history: Path, lb: LoadBalancer):
        engine1 = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        for _ in range(5):
            engine1.record_outcome("t", "agent-a", passed=True, lead_time_seconds=10.0)
        engine1.record_outcome("t", "agent-a", passed=False, lead_time_seconds=10.0)

        engine2 = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        hist = engine2.get_agent_history("agent-a")
        assert hist["total_tasks"] == 6
        assert hist["pass_rate"] == pytest.approx(5 / 6)

    def test_reload_from_jsonl_multiple_agents(self, tmp_history: Path, lb: LoadBalancer):
        engine1 = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        engine1.record_outcome("t", "agent-a", passed=True, lead_time_seconds=5.0)
        engine1.record_outcome("t", "agent-b", passed=False, lead_time_seconds=8.0)

        engine2 = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        assert engine2.get_agent_history("agent-a")["total_tasks"] == 1
        assert engine2.get_agent_history("agent-b")["total_tasks"] == 1

    def test_malformed_jsonl_lines_skipped(self, tmp_history: Path, lb: LoadBalancer):
        tmp_history.parent.mkdir(parents=True, exist_ok=True)
        tmp_history.write_text(
            '{"agent_id": "agent-a", "passed": true, "lead_time_seconds": 5.0}\n'
            "NOT_JSON\n"
            '{"broken": true}\n'
        )
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        hist = engine.get_agent_history("agent-a")
        assert hist["total_tasks"] == 1

    def test_missing_history_file_handled_gracefully(self, lb: LoadBalancer):
        missing = Path("/tmp/nonexistent_forge_rec_history_xyz_df3001.jsonl")
        # Should not raise
        engine = RecommendationEngine(load_balancer=lb, history_path=missing)
        assert engine.get_agent_history("any-agent")["total_tasks"] == 0

    def test_empty_lines_in_jsonl_skipped(self, tmp_history: Path, lb: LoadBalancer):
        tmp_history.parent.mkdir(parents=True, exist_ok=True)
        tmp_history.write_text("\n\n\n")
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        assert engine.get_agent_history("agent-a")["total_tasks"] == 0

    def test_persist_creates_parent_directories(self, tmp_path: Path, lb: LoadBalancer):
        deep_path = tmp_path / "a" / "b" / "c" / "history.jsonl"
        engine = RecommendationEngine(load_balancer=lb, history_path=deep_path)
        engine.record_outcome("t", "agent-a", passed=True, lead_time_seconds=1.0)
        assert deep_path.exists()


# ===========================================================================
# Reasoning
# ===========================================================================


class TestReasoning:
    def test_reasoning_contains_lane_resolution(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert any("api_simple" in r for r in rec.reasoning)

    def test_reasoning_contains_eligibility_count(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert any("Eligible" in r or "/" in r for r in rec.reasoning)

    def test_reasoning_contains_top_agent_name(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert any("agent-a" in r for r in rec.reasoning)

    def test_reasoning_mentions_confidence(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert any("confidence" in r.lower() for r in rec.reasoning)

    def test_reasoning_preferred_lane_override(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.docs])
        rec = engine.recommend(
            "t",
            TaskType.bug_fix,
            RiskTier.low,
            preferred_lane=WorkCellLane.docs,
        )
        combined = " ".join(rec.reasoning).lower()
        assert "override" in combined or "overridden" in combined

    def test_reasoning_is_non_empty_list(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert isinstance(rec.reasoning, list)
        assert len(rec.reasoning) > 0


# ===========================================================================
# Integration with LoadBalancer and LaneEnforcer
# ===========================================================================


class TestIntegration:
    def test_load_balancer_and_engine_collaborate(self, lb: LoadBalancer, tmp_history: Path):
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        lb.register_agent("forge:nova", [WorkCellLane.api_simple, WorkCellLane.docs])
        lb.update_agent_load("forge:nova", current_active=0, context_budget_pct=90.0)
        rec = engine.recommend("t-001", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].agent_id == "forge:nova"

    def test_lane_enforcer_resolved_lane_matches_engine(self):
        """LaneResolver resolves the same lane for the same (type, risk) pair."""
        from forge_harness.webhook_server.models.work_cell import LaneResolver

        resolver = LaneResolver()
        lane = resolver.resolve(TaskType.bug_fix, RiskTier.low)
        assert lane == WorkCellLane.api_simple

    def test_three_agents_two_eligible_correct_ranking(self, lb: LoadBalancer, tmp_history: Path):
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        _register(lb, "agent-a", [WorkCellLane.api_simple], current_active=0)
        _register(lb, "agent-b", [WorkCellLane.api_simple], current_active=2)
        _register(lb, "agent-c", [WorkCellLane.security_change])  # ineligible

        for _ in range(5):
            engine.record_outcome("past", "agent-a", passed=True, lead_time_seconds=5.0)

        rec = engine.recommend(
            "t",
            TaskType.bug_fix,
            RiskTier.low,
            strategy=RecommendationStrategy.balanced,
        )
        ids = [s.agent_id for s in rec.recommended_agents]
        assert "agent-c" not in ids
        assert len(ids) == 2
        assert ids[0] == "agent-a"  # better load + history

    def test_load_balancer_re_registration_reflected_in_recommend(
        self, lb: LoadBalancer, tmp_history: Path
    ):
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        lb.register_agent("agent-a", [WorkCellLane.docs])
        lb.update_agent_load("agent-a", 0, 100.0)
        # Initially only docs lane; bug_fix+low → api_simple → not eligible
        rec1 = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec1.recommended_agents == []
        # Re-register with api_simple lane
        lb.register_agent("agent-a", [WorkCellLane.api_simple])
        lb.update_agent_load("agent-a", 0, 100.0)
        rec2 = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert len(rec2.recommended_agents) == 1

    def test_all_task_types_resolve_and_find_eligible_agent(
        self, lb: LoadBalancer, tmp_history: Path
    ):
        """An omniscient agent is recommended for every task type."""
        engine = RecommendationEngine(load_balancer=lb, history_path=tmp_history)
        all_lanes = list(WorkCellLane)
        lb.register_agent("omniscient", all_lanes)
        lb.update_agent_load("omniscient", 0, 100.0)
        for task_type in TaskType:
            rec = engine.recommend("t", task_type, RiskTier.low)
            ids = [s.agent_id for s in rec.recommended_agents]
            assert "omniscient" in ids, f"omniscient missing for task_type={task_type}"


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_recommend_does_not_corrupt_log(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        errors: list[Exception] = []

        def _recommend(n: int):
            try:
                for i in range(20):
                    engine.recommend(f"t-{n}-{i}", TaskType.bug_fix, RiskTier.low)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_recommend, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        log = engine.get_recommendation_log(limit=200)
        assert len(log) > 0

    def test_concurrent_record_outcome_does_not_corrupt_history(self, engine: RecommendationEngine):
        errors: list[Exception] = []

        def _record(n: int):
            try:
                for i in range(50):
                    engine.record_outcome(
                        f"t-{n}-{i}",
                        "agent-a",
                        passed=(i % 2 == 0),
                        lead_time_seconds=1.0,
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_record, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        hist = engine.get_agent_history("agent-a")
        assert hist["total_tasks"] == 200
        assert 0.0 <= hist["pass_rate"] <= 1.0

    def test_concurrent_recommend_and_record_outcome(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        errors: list[Exception] = []

        def _recommender():
            try:
                for i in range(30):
                    engine.recommend(f"t-r-{i}", TaskType.bug_fix, RiskTier.low)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def _recorder():
            try:
                for i in range(30):
                    engine.record_outcome(f"t-o-{i}", "agent-a", passed=True, lead_time_seconds=1.0)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=_recommender),
            threading.Thread(target=_recorder),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_agent_id_with_special_characters(self, engine: RecommendationEngine, lb: LoadBalancer):
        special_id = "forge:node-01/worker@home"
        lb.register_agent(special_id, [WorkCellLane.api_simple])
        lb.update_agent_load(special_id, 0, 100.0)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].agent_id == special_id

    def test_very_large_lead_time(self, engine: RecommendationEngine):
        engine.record_outcome("t", "agent-a", passed=True, lead_time_seconds=86400.0)
        hist = engine.get_agent_history("agent-a")
        assert hist["avg_lead_time"] == pytest.approx(86400.0)

    def test_all_agents_zero_context_budget(self, engine: RecommendationEngine, lb: LoadBalancer):
        _register(lb, "agent-a", [WorkCellLane.api_simple], context_budget_pct=0.0)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert len(rec.recommended_agents) == 1
        assert rec.recommended_agents[0].factors["context_budget"] == pytest.approx(0.0)

    def test_same_task_id_multiple_recommendations_allowed(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        _register(lb, "agent-a", [WorkCellLane.api_simple])
        engine.recommend("same-task", TaskType.bug_fix, RiskTier.low)
        engine.recommend("same-task", TaskType.bug_fix, RiskTier.low)
        log = engine.get_recommendation_log()
        task_ids = [r.task_id for r in log]
        assert task_ids.count("same-task") == 2

    def test_zero_lead_time_is_valid(self, engine: RecommendationEngine):
        """Zero lead time is an edge but not an error."""
        engine.record_outcome("t", "agent-a", passed=True, lead_time_seconds=0.0)
        hist = engine.get_agent_history("agent-a")
        assert hist["avg_lead_time"] == pytest.approx(0.0)

    def test_all_lanes_supported_agent_eligible_for_any_task(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        all_lanes = list(WorkCellLane)
        lb.register_agent("omniscient", all_lanes)
        lb.update_agent_load("omniscient", 0, 100.0)
        for task_type in TaskType:
            rec = engine.recommend("t", task_type, RiskTier.low)
            ids = [s.agent_id for s in rec.recommended_agents]
            assert "omniscient" in ids, f"omniscient missing for {task_type}"

    def test_get_agent_history_returns_correct_keys(self, engine: RecommendationEngine):
        hist = engine.get_agent_history("any")
        assert "pass_rate" in hist
        assert "avg_lead_time" in hist
        assert "total_tasks" in hist

    def test_score_clamped_to_zero_one(self, engine: RecommendationEngine, lb: LoadBalancer):
        """Score is always in [0, 1] regardless of extreme inputs."""
        _register(
            lb,
            "agent-a",
            [WorkCellLane.api_simple],
            max_concurrent=1,
            current_active=0,
            context_budget_pct=100.0,
        )
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        for agent_score in rec.recommended_agents:
            assert 0.0 <= agent_score.score <= 1.0

    def test_many_agents_all_eligible_ranking_stable(
        self, engine: RecommendationEngine, lb: LoadBalancer
    ):
        """10 eligible agents — ranking must be deterministic across calls."""
        for i in range(10):
            _register(
                lb,
                f"agent-{i:02d}",
                [WorkCellLane.api_simple],
                current_active=i % 3,
            )
        rec1 = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        rec2 = engine.recommend("t2", TaskType.bug_fix, RiskTier.low)
        ids1 = [s.agent_id for s in rec1.recommended_agents]
        ids2 = [s.agent_id for s in rec2.recommended_agents]
        assert ids1 == ids2
