"""Unit tests for RecommendationEngine (DF-3001).

Covers:
- _AgentHistory: pass_rate, avg_lead_time_seconds, record()
- RecommendationEngine.__init__: dependency injection, history loading
- RecommendationEngine.recommend: lane resolution, agent filtering, scoring,
  confidence, path-lock filtering, preferred_lane override, strategies,
  empty agent registry, no eligible agents, all agents locked out
- RecommendationEngine.record_outcome: happy path, negative lead time raises,
  first-time agent creation, persistence delegation
- RecommendationEngine.get_agent_history: unknown agent, known agent
- RecommendationEngine.get_recommendation_log: limit, reverse-chronological order
- RecommendationEngine._filter_by_path_locks: no conflicts, partial conflicts, full lockout
- RecommendationEngine._load_history: missing file, valid lines, malformed lines, skip non-outcome lines
- RecommendationEngine._persist_outcome: happy path, OSError swallowed
- RecommendationEngine._append_log: bounded deque (maxlen respected)
- get_recommendation_engine: singleton creation, reuse
- reset_recommendation_engine: singleton destruction
- scoring weight correctness per strategy
- tie-breaking by agent_id when scores are equal
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from threading import RLock
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

from forge_harness.webhook_server.models.agent_load import AgentCapability
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.recommendation import (
    AgentScore,
    RecommendationStrategy,
    TaskRecommendation,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane

# Module under test
from forge_harness.webhook_server.services.recommendation_engine import (
    _DEFAULT_HISTORICAL_PASS_RATE,
    _MAX_LOG_ENTRIES,
    _STRATEGY_WEIGHTS,
    RecommendationEngine,
    _AgentHistory,
    get_recommendation_engine,
    reset_recommendation_engine,
)

# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _make_capability(
    agent_id: str,
    supported_lanes: list[WorkCellLane],
    max_concurrent: int = 3,
    current_active: int = 0,
    context_budget_remaining_pct: float = 100.0,
) -> AgentCapability:
    return AgentCapability(
        agent_id=agent_id,
        supported_lanes=supported_lanes,
        max_concurrent=max_concurrent,
        current_active=current_active,
        context_budget_remaining_pct=context_budget_remaining_pct,
    )


def _make_conflict(agent_id: str, conflict_reason: str = "exclusive lock") -> MagicMock:
    """Return a minimal LockConflict-like mock."""
    c = MagicMock()
    c.agent_id = agent_id
    c.conflict_reason = conflict_reason
    return c


def _make_engine(
    agents: dict[str, AgentCapability] | None = None,
    history_path: str = "/tmp/test_re_history.jsonl",
) -> tuple[RecommendationEngine, MagicMock, MagicMock]:
    """Build a RecommendationEngine with fully-mocked external dependencies.

    Returns (engine, mock_lb, mock_plr).
    """
    mock_lb = MagicMock()
    mock_lb.get_agent_stats.return_value = agents or {}

    mock_plr = MagicMock()
    mock_plr.check_conflicts.return_value = []  # no conflicts by default

    # Patch Path.exists so _load_history finds no file
    with patch("pathlib.Path.exists", return_value=False):
        engine = RecommendationEngine(
            load_balancer=mock_lb,
            history_path=history_path,
            path_lock_registry=mock_plr,
        )
    return engine, mock_lb, mock_plr


# ===========================================================================
# _AgentHistory
# ===========================================================================


class TestAgentHistory:
    """Tests for the _AgentHistory internal statistics accumulator."""

    def test_initial_state(self):
        hist = _AgentHistory()
        assert hist.total_tasks == 0
        assert hist.passed_tasks == 0
        assert hist.total_lead_time == 0.0

    def test_pass_rate_no_data_returns_prior(self):
        hist = _AgentHistory()
        assert hist.pass_rate == _DEFAULT_HISTORICAL_PASS_RATE

    def test_avg_lead_time_no_data_returns_none(self):
        hist = _AgentHistory()
        assert hist.avg_lead_time_seconds is None

    def test_record_passed_task(self):
        hist = _AgentHistory()
        hist.record(passed=True, lead_time_seconds=10.0)
        assert hist.total_tasks == 1
        assert hist.passed_tasks == 1
        assert hist.total_lead_time == 10.0

    def test_record_failed_task(self):
        hist = _AgentHistory()
        hist.record(passed=False, lead_time_seconds=5.0)
        assert hist.total_tasks == 1
        assert hist.passed_tasks == 0
        assert hist.total_lead_time == 5.0

    def test_pass_rate_all_passed(self):
        hist = _AgentHistory()
        for _ in range(4):
            hist.record(passed=True, lead_time_seconds=1.0)
        assert hist.pass_rate == 1.0

    def test_pass_rate_none_passed(self):
        hist = _AgentHistory()
        for _ in range(3):
            hist.record(passed=False, lead_time_seconds=1.0)
        assert hist.pass_rate == 0.0

    def test_pass_rate_mixed(self):
        hist = _AgentHistory()
        hist.record(passed=True, lead_time_seconds=1.0)
        hist.record(passed=False, lead_time_seconds=1.0)
        hist.record(passed=True, lead_time_seconds=1.0)
        assert hist.pass_rate == pytest.approx(2 / 3)

    def test_avg_lead_time_with_data(self):
        hist = _AgentHistory()
        hist.record(passed=True, lead_time_seconds=10.0)
        hist.record(passed=True, lead_time_seconds=20.0)
        assert hist.avg_lead_time_seconds == pytest.approx(15.0)

    def test_accumulates_multiple_records(self):
        hist = _AgentHistory()
        for i in range(5):
            hist.record(passed=i % 2 == 0, lead_time_seconds=float(i))
        assert hist.total_tasks == 5
        assert hist.total_lead_time == pytest.approx(0 + 1 + 2 + 3 + 4)


# ===========================================================================
# RecommendationEngine — construction
# ===========================================================================


class TestRecommendationEngineInit:
    """Tests for RecommendationEngine.__init__."""

    def test_uses_provided_load_balancer(self):
        mock_lb = MagicMock()
        mock_plr = MagicMock()
        with patch("pathlib.Path.exists", return_value=False):
            engine = RecommendationEngine(
                load_balancer=mock_lb,
                history_path="/tmp/noop.jsonl",
                path_lock_registry=mock_plr,
            )
        assert engine._lb is mock_lb

    def test_uses_provided_path_lock_registry(self):
        mock_lb = MagicMock()
        mock_plr = MagicMock()
        with patch("pathlib.Path.exists", return_value=False):
            engine = RecommendationEngine(
                load_balancer=mock_lb,
                history_path="/tmp/noop.jsonl",
                path_lock_registry=mock_plr,
            )
        assert engine._plr is mock_plr

    def test_history_path_is_path_object(self):
        mock_lb = MagicMock()
        mock_plr = MagicMock()
        with patch("pathlib.Path.exists", return_value=False):
            engine = RecommendationEngine(
                load_balancer=mock_lb,
                history_path="/tmp/custom.jsonl",
                path_lock_registry=mock_plr,
            )
        assert engine._history_path == Path("/tmp/custom.jsonl")

    def test_starts_with_empty_history(self):
        engine, _, _ = _make_engine()
        assert engine._history == {}

    def test_starts_with_empty_log(self):
        engine, _, _ = _make_engine()
        assert len(engine._log) == 0

    def test_log_has_correct_maxlen(self):
        engine, _, _ = _make_engine()
        assert engine._log.maxlen == _MAX_LOG_ENTRIES

    def test_falls_back_to_get_load_balancer_when_none(self):
        mock_lb = MagicMock()
        mock_plr = MagicMock()
        with (
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_load_balancer",
                return_value=mock_lb,
            ),
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_path_lock_registry",
                return_value=mock_plr,
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            engine = RecommendationEngine(history_path="/tmp/noop.jsonl")
        assert engine._lb is mock_lb

    def test_falls_back_to_get_path_lock_registry_when_none(self):
        mock_lb = MagicMock()
        mock_plr = MagicMock()
        with (
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_load_balancer",
                return_value=mock_lb,
            ),
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_path_lock_registry",
                return_value=mock_plr,
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            engine = RecommendationEngine(history_path="/tmp/noop.jsonl")
        assert engine._plr is mock_plr


# ===========================================================================
# RecommendationEngine — recommend()
# ===========================================================================


class TestRecommendEngineRecommend:
    """Tests for RecommendationEngine.recommend()."""

    def _single_agent_engine(
        self,
        lane: WorkCellLane = WorkCellLane.api_simple,
        max_concurrent: int = 3,
        current_active: int = 0,
        context_budget: float = 100.0,
    ) -> tuple[RecommendationEngine, MagicMock]:
        cap = _make_capability(
            "forge:alpha",
            [lane],
            max_concurrent=max_concurrent,
            current_active=current_active,
            context_budget_remaining_pct=context_budget,
        )
        engine, mock_lb, _ = _make_engine(agents={"forge:alpha": cap})
        return engine, mock_lb

    # --- empty registry ---

    def test_no_agents_returns_empty_recommendation(self):
        engine, _, _ = _make_engine(agents={})
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert isinstance(rec, TaskRecommendation)
        assert rec.task_id == "t1"
        assert rec.recommended_agents == []
        assert rec.confidence == 0.0

    def test_no_agents_logs_to_log(self):
        engine, _, _ = _make_engine(agents={})
        engine.recommend("t2", TaskType.bug_fix, RiskTier.low)
        assert len(engine.get_recommendation_log()) == 1

    # --- lane filtering ---

    def test_agent_with_wrong_lane_excluded(self):
        # Agent only supports security_change; task resolves to api_simple
        cap = _make_capability("forge:sec", [WorkCellLane.security_change])
        engine, _, _ = _make_engine(agents={"forge:sec": cap})
        rec = engine.recommend("t3", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents == []
        assert rec.confidence == 0.0

    def test_agent_with_matching_lane_included(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        rec = engine.recommend("t4", TaskType.bug_fix, RiskTier.low)
        assert len(rec.recommended_agents) == 1
        assert rec.recommended_agents[0].agent_id == "forge:alpha"

    # --- preferred_lane override ---

    def test_preferred_lane_skips_resolver(self):
        cap = _make_capability("forge:alpha", [WorkCellLane.docs])
        engine, _, _ = _make_engine(agents={"forge:alpha": cap})
        # Without override, bug_fix+low → api_simple, which docs doesn't support
        rec_no_override = engine.recommend("t5a", TaskType.bug_fix, RiskTier.low)
        assert rec_no_override.recommended_agents == []
        # With override to docs, agent IS eligible
        rec_override = engine.recommend(
            "t5b", TaskType.bug_fix, RiskTier.low,
            preferred_lane=WorkCellLane.docs,
        )
        assert len(rec_override.recommended_agents) == 1

    def test_preferred_lane_appears_in_reasoning(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        rec = engine.recommend(
            "t6", TaskType.bug_fix, RiskTier.low,
            preferred_lane=WorkCellLane.api_simple,
        )
        assert any("overridden" in r.lower() for r in rec.reasoning)

    # --- scoring correctness ---

    def test_score_is_in_unit_interval(self):
        engine, _ = self._single_agent_engine(
            lane=WorkCellLane.api_simple,
            max_concurrent=3,
            current_active=1,
            context_budget=75.0,
        )
        rec = engine.recommend("t7", TaskType.bug_fix, RiskTier.low)
        score = rec.recommended_agents[0].score
        assert 0.0 <= score <= 1.0

    def test_score_factors_present(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        rec = engine.recommend("t8", TaskType.bug_fix, RiskTier.low)
        factors = rec.recommended_agents[0].factors
        assert "capability_match" in factors
        assert "historical_pass_rate" in factors
        assert "current_load" in factors
        assert "context_budget" in factors

    def test_full_capacity_agent_has_zero_load_factor(self):
        engine, _ = self._single_agent_engine(
            lane=WorkCellLane.api_simple,
            max_concurrent=3,
            current_active=3,  # fully loaded
        )
        rec = engine.recommend("t9", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["current_load"] == 0.0

    def test_zero_context_budget_is_reflected_in_score(self):
        engine, _ = self._single_agent_engine(
            lane=WorkCellLane.api_simple,
            context_budget=0.0,
        )
        rec = engine.recommend("t10", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["context_budget"] == 0.0

    def test_capability_match_factor_always_one(self):
        """Agents that pass the lane filter always have capability_match == 1.0."""
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        rec = engine.recommend("t11", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["capability_match"] == 1.0

    # --- historical pass rate in scoring ---

    def test_no_history_uses_neutral_prior(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        rec = engine.recommend("t12", TaskType.bug_fix, RiskTier.low)
        agent_score = rec.recommended_agents[0]
        assert agent_score.historical_pass_rate is None
        assert agent_score.factors["historical_pass_rate"] == _DEFAULT_HISTORICAL_PASS_RATE

    def test_recorded_history_feeds_into_score(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        engine.record_outcome("prev", "forge:alpha", passed=True, lead_time_seconds=5.0)
        engine.record_outcome("prev", "forge:alpha", passed=True, lead_time_seconds=5.0)
        rec = engine.recommend("t13", TaskType.bug_fix, RiskTier.low)
        agent_score = rec.recommended_agents[0]
        assert agent_score.historical_pass_rate == 1.0
        assert agent_score.factors["historical_pass_rate"] == 1.0

    # --- confidence ---

    def test_single_agent_confidence_equals_score(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        rec = engine.recommend("t14", TaskType.bug_fix, RiskTier.low)
        assert rec.confidence == pytest.approx(rec.recommended_agents[0].score)

    def test_two_agents_confidence_is_score_spread(self):
        cap_a = _make_capability("forge:alpha", [WorkCellLane.api_simple], current_active=0)
        cap_b = _make_capability("forge:beta", [WorkCellLane.api_simple], current_active=2)
        engine, _, _ = _make_engine(agents={"forge:alpha": cap_a, "forge:beta": cap_b})
        rec = engine.recommend("t15", TaskType.bug_fix, RiskTier.low)
        assert len(rec.recommended_agents) >= 2
        expected = round(
            rec.recommended_agents[0].score - rec.recommended_agents[1].score, 6
        )
        assert rec.confidence == pytest.approx(expected)

    def test_confidence_clamped_to_unit_interval(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        rec = engine.recommend("t16", TaskType.bug_fix, RiskTier.low)
        assert 0.0 <= rec.confidence <= 1.0

    # --- sorting ---

    def test_agents_sorted_descending_by_score(self):
        cap_a = _make_capability("forge:alpha", [WorkCellLane.api_simple], current_active=2)
        cap_b = _make_capability("forge:beta", [WorkCellLane.api_simple], current_active=0)
        engine, _, _ = _make_engine(agents={"forge:alpha": cap_a, "forge:beta": cap_b})
        rec = engine.recommend("t17", TaskType.bug_fix, RiskTier.low)
        scores = [s.score for s in rec.recommended_agents]
        assert scores == sorted(scores, reverse=True)

    def test_tie_break_by_agent_id_alphabetical(self):
        """When scores are identical, lower agent_id comes first (alphabetical)."""
        # Identical capabilities → identical scores
        cap_z = _make_capability("forge:zeta", [WorkCellLane.api_simple])
        cap_a = _make_capability("forge:aaa", [WorkCellLane.api_simple])
        engine, _, _ = _make_engine(agents={"forge:zeta": cap_z, "forge:aaa": cap_a})
        rec = engine.recommend("t18", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].agent_id == "forge:aaa"

    # --- strategies ---

    def test_capability_match_strategy_accepted(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        rec = engine.recommend(
            "t19", TaskType.bug_fix, RiskTier.low,
            strategy=RecommendationStrategy.capability_match,
        )
        assert len(rec.recommended_agents) == 1

    def test_historical_performance_strategy_accepted(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        rec = engine.recommend(
            "t20", TaskType.bug_fix, RiskTier.low,
            strategy=RecommendationStrategy.historical_performance,
        )
        assert len(rec.recommended_agents) == 1

    def test_balanced_strategy_produces_correct_weight_sum(self):
        """Balanced weights must sum to 1.0."""
        weights = _STRATEGY_WEIGHTS[RecommendationStrategy.balanced]
        assert sum(weights) == pytest.approx(1.0)

    def test_capability_match_strategy_weights_sum_to_one(self):
        weights = _STRATEGY_WEIGHTS[RecommendationStrategy.capability_match]
        assert sum(weights) == pytest.approx(1.0)

    def test_historical_performance_strategy_weights_sum_to_one(self):
        weights = _STRATEGY_WEIGHTS[RecommendationStrategy.historical_performance]
        assert sum(weights) == pytest.approx(1.0)

    # --- path lock filtering ---

    def test_task_paths_with_no_conflicts_keeps_all_agents(self):
        cap = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        engine, _, mock_plr = _make_engine(agents={"forge:alpha": cap})
        mock_plr.check_conflicts.return_value = []
        rec = engine.recommend(
            "t21", TaskType.bug_fix, RiskTier.low,
            task_paths=["/src/api.py"],
        )
        assert len(rec.recommended_agents) == 1

    def test_task_paths_with_conflict_excludes_locked_agent(self):
        cap = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        engine, _, mock_plr = _make_engine(agents={"forge:alpha": cap})
        mock_plr.check_conflicts.return_value = [_make_conflict("forge:alpha")]
        rec = engine.recommend(
            "t22", TaskType.bug_fix, RiskTier.low,
            task_paths=["/src/api.py"],
        )
        assert rec.recommended_agents == []
        assert rec.confidence == 0.0

    def test_task_paths_excludes_only_conflicting_agent(self):
        cap_a = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        cap_b = _make_capability("forge:beta", [WorkCellLane.api_simple])
        engine, _, mock_plr = _make_engine(
            agents={"forge:alpha": cap_a, "forge:beta": cap_b}
        )
        # alpha has a conflict, beta does not
        mock_plr.check_conflicts.return_value = [_make_conflict("forge:alpha")]
        rec = engine.recommend(
            "t23", TaskType.bug_fix, RiskTier.low,
            task_paths=["/src/api.py"],
        )
        agent_ids = [s.agent_id for s in rec.recommended_agents]
        assert "forge:beta" in agent_ids
        assert "forge:alpha" not in agent_ids

    def test_path_lock_conflict_adds_reasoning(self):
        cap = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        engine, _, mock_plr = _make_engine(agents={"forge:alpha": cap})
        mock_plr.check_conflicts.return_value = [_make_conflict("forge:alpha")]
        rec = engine.recommend(
            "t24", TaskType.bug_fix, RiskTier.low,
            task_paths=["/src/api.py"],
        )
        assert any("excluded" in r for r in rec.reasoning)

    def test_all_agents_locked_out_returns_empty(self):
        cap = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        engine, _, mock_plr = _make_engine(agents={"forge:alpha": cap})
        mock_plr.check_conflicts.return_value = [_make_conflict("forge:alpha")]
        rec = engine.recommend(
            "t25", TaskType.bug_fix, RiskTier.low,
            task_paths=["/src/conflict.py"],
        )
        assert rec.recommended_agents == []

    # --- recommendation is logged ---

    def test_successful_recommend_is_logged(self):
        engine, _ = self._single_agent_engine(lane=WorkCellLane.api_simple)
        engine.recommend("t26", TaskType.bug_fix, RiskTier.low)
        log = engine.get_recommendation_log()
        assert len(log) == 1
        assert log[0].task_id == "t26"

    # --- lane resolution from TaskType+RiskTier ---

    def test_security_change_resolves_to_security_change_lane(self):
        cap = _make_capability("forge:alpha", [WorkCellLane.security_change])
        engine, _, _ = _make_engine(agents={"forge:alpha": cap})
        rec = engine.recommend("t27", TaskType.security_change, RiskTier.low)
        assert len(rec.recommended_agents) == 1

    def test_test_writing_resolves_to_test_writing_lane(self):
        cap = _make_capability("forge:alpha", [WorkCellLane.test_writing])
        engine, _, _ = _make_engine(agents={"forge:alpha": cap})
        rec = engine.recommend("t28", TaskType.test_writing, RiskTier.medium)
        assert len(rec.recommended_agents) == 1


# ===========================================================================
# RecommendationEngine — record_outcome()
# ===========================================================================


class TestRecordOutcome:
    """Tests for RecommendationEngine.record_outcome()."""

    def test_negative_lead_time_raises_value_error(self):
        engine, _, _ = _make_engine()
        with pytest.raises(ValueError, match="lead_time_seconds"):
            engine.record_outcome("t1", "forge:alpha", passed=True, lead_time_seconds=-1.0)

    def test_first_outcome_creates_history_entry(self):
        engine, _, _ = _make_engine()
        engine.record_outcome("t1", "forge:alpha", passed=True, lead_time_seconds=10.0)
        assert "forge:alpha" in engine._history

    def test_outcome_updates_pass_rate(self):
        engine, _, _ = _make_engine()
        engine.record_outcome("t1", "forge:alpha", passed=True, lead_time_seconds=5.0)
        engine.record_outcome("t2", "forge:alpha", passed=False, lead_time_seconds=5.0)
        assert engine._history["forge:alpha"].pass_rate == pytest.approx(0.5)

    def test_zero_lead_time_accepted(self):
        engine, _, _ = _make_engine()
        engine.record_outcome("t1", "forge:alpha", passed=True, lead_time_seconds=0.0)
        assert engine._history["forge:alpha"].total_lead_time == 0.0

    def test_outcome_calls_persist(self):
        engine, _, _ = _make_engine()
        with patch.object(engine, "_persist_outcome") as mock_persist:
            engine.record_outcome("t1", "forge:alpha", passed=True, lead_time_seconds=7.0)
            mock_persist.assert_called_once_with("t1", "forge:alpha", True, 7.0)

    def test_multiple_agents_tracked_independently(self):
        engine, _, _ = _make_engine()
        engine.record_outcome("t1", "forge:alpha", passed=True, lead_time_seconds=5.0)
        engine.record_outcome("t2", "forge:beta", passed=False, lead_time_seconds=3.0)
        assert engine._history["forge:alpha"].pass_rate == 1.0
        assert engine._history["forge:beta"].pass_rate == 0.0


# ===========================================================================
# RecommendationEngine — get_agent_history()
# ===========================================================================


class TestGetAgentHistory:
    """Tests for RecommendationEngine.get_agent_history()."""

    def test_unknown_agent_returns_defaults(self):
        engine, _, _ = _make_engine()
        result = engine.get_agent_history("forge:unknown")
        assert result["pass_rate"] == _DEFAULT_HISTORICAL_PASS_RATE
        assert result["avg_lead_time"] is None
        assert result["total_tasks"] == 0

    def test_known_agent_returns_stats(self):
        engine, _, _ = _make_engine()
        engine.record_outcome("t1", "forge:alpha", passed=True, lead_time_seconds=20.0)
        engine.record_outcome("t2", "forge:alpha", passed=False, lead_time_seconds=10.0)
        result = engine.get_agent_history("forge:alpha")
        assert result["total_tasks"] == 2
        assert result["pass_rate"] == pytest.approx(0.5)
        assert result["avg_lead_time"] == pytest.approx(15.0)

    def test_returns_dict_with_expected_keys(self):
        engine, _, _ = _make_engine()
        result = engine.get_agent_history("forge:new")
        assert set(result.keys()) == {"pass_rate", "avg_lead_time", "total_tasks"}


# ===========================================================================
# RecommendationEngine — get_recommendation_log()
# ===========================================================================


class TestGetRecommendationLog:
    """Tests for RecommendationEngine.get_recommendation_log()."""

    def test_empty_log_returns_empty_list(self):
        engine, _, _ = _make_engine()
        assert engine.get_recommendation_log() == []

    def test_log_returned_in_reverse_chronological_order(self):
        engine, _, _ = _make_engine(agents={})
        for i in range(3):
            engine.recommend(f"task-{i}", TaskType.bug_fix, RiskTier.low)
        log = engine.get_recommendation_log()
        # Most recent should be task-2
        assert log[0].task_id == "task-2"
        assert log[-1].task_id == "task-0"

    def test_limit_parameter_respected(self):
        engine, _, _ = _make_engine(agents={})
        for i in range(10):
            engine.recommend(f"task-{i}", TaskType.bug_fix, RiskTier.low)
        log = engine.get_recommendation_log(limit=3)
        assert len(log) == 3

    def test_default_limit_is_twenty(self):
        engine, _, _ = _make_engine(agents={})
        for i in range(25):
            engine.recommend(f"task-{i}", TaskType.bug_fix, RiskTier.low)
        log = engine.get_recommendation_log()
        assert len(log) == 20

    def test_log_bounded_by_max_log_entries(self):
        """Deque maxlen is enforced — oldest entries are dropped."""
        engine, _, _ = _make_engine(agents={})
        for i in range(_MAX_LOG_ENTRIES + 10):
            engine.recommend(f"task-{i}", TaskType.bug_fix, RiskTier.low)
        log = engine.get_recommendation_log(limit=_MAX_LOG_ENTRIES + 10)
        assert len(log) == _MAX_LOG_ENTRIES


# ===========================================================================
# RecommendationEngine — _load_history()
# ===========================================================================


class TestLoadHistory:
    """Tests for the _load_history persistence replay."""

    def _build_engine_with_history_file(
        self, lines: list[str]
    ) -> RecommendationEngine:
        """Return an engine whose _load_history reads the given JSONL lines."""
        mock_lb = MagicMock()
        mock_lb.get_agent_stats.return_value = {}
        mock_plr = MagicMock()

        file_content = "\n".join(lines) + "\n"
        m = mock_open(read_data=file_content)
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.open", m),
        ):
            engine = RecommendationEngine(
                load_balancer=mock_lb,
                history_path="/tmp/fake.jsonl",
                path_lock_registry=mock_plr,
            )
        return engine

    def test_missing_file_produces_empty_history(self):
        engine, _, _ = _make_engine()
        assert engine._history == {}

    def test_valid_outcome_record_loaded(self):
        record = json.dumps(
            {"agent_id": "forge:nova", "passed": True, "lead_time_seconds": 12.0}
        )
        engine = self._build_engine_with_history_file([record])
        assert "forge:nova" in engine._history
        assert engine._history["forge:nova"].total_tasks == 1

    def test_multiple_records_accumulated(self):
        records = [
            json.dumps({"agent_id": "forge:nova", "passed": True, "lead_time_seconds": 5.0}),
            json.dumps({"agent_id": "forge:nova", "passed": False, "lead_time_seconds": 3.0}),
        ]
        engine = self._build_engine_with_history_file(records)
        assert engine._history["forge:nova"].total_tasks == 2
        assert engine._history["forge:nova"].pass_rate == pytest.approx(0.5)

    def test_malformed_json_line_skipped(self):
        lines = [
            "not-json-at-all",
            json.dumps({"agent_id": "forge:nova", "passed": True, "lead_time_seconds": 1.0}),
        ]
        engine = self._build_engine_with_history_file(lines)
        assert engine._history["forge:nova"].total_tasks == 1

    def test_missing_agent_id_skipped(self):
        lines = [
            json.dumps({"passed": True, "lead_time_seconds": 5.0}),  # no agent_id
        ]
        engine = self._build_engine_with_history_file(lines)
        assert engine._history == {}

    def test_missing_passed_field_skipped(self):
        lines = [
            json.dumps({"agent_id": "forge:nova", "lead_time_seconds": 5.0}),  # no passed
        ]
        engine = self._build_engine_with_history_file(lines)
        assert engine._history == {}

    def test_blank_lines_ignored(self):
        lines = [
            "",
            json.dumps({"agent_id": "forge:nova", "passed": True, "lead_time_seconds": 2.0}),
            "   ",
        ]
        engine = self._build_engine_with_history_file(lines)
        assert engine._history["forge:nova"].total_tasks == 1

    def test_missing_lead_time_defaults_to_zero(self):
        lines = [
            json.dumps({"agent_id": "forge:nova", "passed": True}),  # no lead_time_seconds
        ]
        engine = self._build_engine_with_history_file(lines)
        assert engine._history["forge:nova"].total_lead_time == 0.0


# ===========================================================================
# RecommendationEngine — _persist_outcome()
# ===========================================================================


class TestPersistOutcome:
    """Tests for the _persist_outcome filesystem write helper."""

    def test_outcome_written_as_valid_json(self):
        engine, _, _ = _make_engine()
        written_data: list[str] = []

        m = mock_open()
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.open", m),
        ):
            engine._persist_outcome("t1", "forge:nova", True, 42.5)
            handle = m()
            written_data = [c.args[0] for c in handle.write.call_args_list]

        # Should have written exactly one JSON line ending with \n
        assert len(written_data) == 1
        line = written_data[0]
        assert line.endswith("\n")
        record = json.loads(line.strip())
        assert record["task_id"] == "t1"
        assert record["agent_id"] == "forge:nova"
        assert record["passed"] is True
        assert record["lead_time_seconds"] == 42.5
        assert record["type"] == "outcome"

    def test_os_error_is_swallowed(self):
        engine, _, _ = _make_engine()
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.open", side_effect=OSError("disk full")),
        ):
            # Must not raise
            engine._persist_outcome("t1", "forge:nova", True, 5.0)


# ===========================================================================
# RecommendationEngine — _append_log()
# ===========================================================================


class TestAppendLog:
    """Tests for the bounded in-memory recommendation log."""

    def test_append_increments_log_size(self):
        engine, _, _ = _make_engine(agents={})
        engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert len(engine._log) == 1

    def test_log_drops_oldest_when_full(self):
        """Filling past maxlen should evict the oldest entry."""
        engine, _, _ = _make_engine(agents={})
        # Directly fill the deque beyond maxlen
        dummy_rec = TaskRecommendation(task_id="dummy", recommended_agents=[], confidence=0.0)
        for _ in range(_MAX_LOG_ENTRIES + 5):
            engine._append_log(dummy_rec)
        assert len(engine._log) == _MAX_LOG_ENTRIES


# ===========================================================================
# _filter_by_path_locks()
# ===========================================================================


class TestFilterByPathLocks:
    """Tests for RecommendationEngine._filter_by_path_locks()."""

    def _engine_with_plr(self) -> tuple[RecommendationEngine, MagicMock]:
        _, _, mock_plr = _make_engine()
        # Re-create engine to get a reference to plr
        mock_lb = MagicMock()
        mock_lb.get_agent_stats.return_value = {}
        with patch("pathlib.Path.exists", return_value=False):
            engine = RecommendationEngine(
                load_balancer=mock_lb,
                history_path="/tmp/noop.jsonl",
                path_lock_registry=mock_plr,
            )
        return engine, mock_plr

    def test_no_conflicts_returns_all_agents(self):
        engine, mock_plr = self._engine_with_plr()
        mock_plr.check_conflicts.return_value = []
        cap_a = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        eligible = {"forge:alpha": cap_a}
        reasoning: list[str] = []
        result = engine._filter_by_path_locks(eligible, ["/src/a.py"], reasoning)
        assert "forge:alpha" in result

    def test_no_conflicts_adds_no_conflict_reasoning(self):
        engine, mock_plr = self._engine_with_plr()
        mock_plr.check_conflicts.return_value = []
        cap_a = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        reasoning: list[str] = []
        engine._filter_by_path_locks({"forge:alpha": cap_a}, ["/src/a.py"], reasoning)
        assert any("no conflicts" in r.lower() for r in reasoning)

    def test_conflict_removes_agent_from_eligible(self):
        engine, mock_plr = self._engine_with_plr()
        mock_plr.check_conflicts.return_value = [_make_conflict("forge:alpha")]
        cap_a = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        cap_b = _make_capability("forge:beta", [WorkCellLane.api_simple])
        eligible = {"forge:alpha": cap_a, "forge:beta": cap_b}
        reasoning: list[str] = []
        result = engine._filter_by_path_locks(eligible, ["/src/a.py"], reasoning)
        assert "forge:alpha" not in result
        assert "forge:beta" in result

    def test_conflict_from_non_eligible_agent_ignored(self):
        """Conflict from an agent NOT in eligible dict should not affect results."""
        engine, mock_plr = self._engine_with_plr()
        # Only forge:gamma is in eligible; conflict is for forge:delta which is not
        mock_plr.check_conflicts.return_value = [_make_conflict("forge:delta")]
        cap = _make_capability("forge:gamma", [WorkCellLane.api_simple])
        eligible = {"forge:gamma": cap}
        reasoning: list[str] = []
        result = engine._filter_by_path_locks(eligible, ["/src/a.py"], reasoning)
        assert "forge:gamma" in result

    def test_reasoning_includes_excluded_agent_name(self):
        engine, mock_plr = self._engine_with_plr()
        mock_plr.check_conflicts.return_value = [_make_conflict("forge:alpha")]
        cap = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        reasoning: list[str] = []
        engine._filter_by_path_locks({"forge:alpha": cap}, ["/src/a.py"], reasoning)
        assert any("forge:alpha" in r for r in reasoning)

    def test_multiple_paths_checked(self):
        engine, mock_plr = self._engine_with_plr()
        mock_plr.check_conflicts.return_value = []
        cap = _make_capability("forge:alpha", [WorkCellLane.api_simple])
        engine._filter_by_path_locks(
            {"forge:alpha": cap}, ["/src/a.py", "/src/b.py"], []
        )
        assert mock_plr.check_conflicts.call_count == 2


# ===========================================================================
# Singleton helpers
# ===========================================================================


class TestSingletonHelpers:
    """Tests for get_recommendation_engine and reset_recommendation_engine."""

    def setup_method(self):
        reset_recommendation_engine()

    def teardown_method(self):
        reset_recommendation_engine()

    def test_get_recommendation_engine_returns_instance(self):
        mock_lb = MagicMock()
        mock_plr = MagicMock()
        with (
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_load_balancer",
                return_value=mock_lb,
            ),
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_path_lock_registry",
                return_value=mock_plr,
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            engine = get_recommendation_engine()
        assert isinstance(engine, RecommendationEngine)

    def test_get_recommendation_engine_returns_same_instance(self):
        mock_lb = MagicMock()
        mock_plr = MagicMock()
        with (
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_load_balancer",
                return_value=mock_lb,
            ),
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_path_lock_registry",
                return_value=mock_plr,
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            e1 = get_recommendation_engine()
            e2 = get_recommendation_engine()
        assert e1 is e2

    def test_reset_destroys_singleton(self):
        mock_lb = MagicMock()
        mock_plr = MagicMock()
        with (
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_load_balancer",
                return_value=mock_lb,
            ),
            patch(
                "forge_harness.webhook_server.services.recommendation_engine.get_path_lock_registry",
                return_value=mock_plr,
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            e1 = get_recommendation_engine()
            reset_recommendation_engine()
            e2 = get_recommendation_engine()
        assert e1 is not e2

    def test_reset_when_no_singleton_does_not_raise(self):
        """Calling reset before any singleton is created should be a no-op."""
        reset_recommendation_engine()  # already None
        reset_recommendation_engine()  # double reset — should not raise


# ===========================================================================
# Strategy weight verification
# ===========================================================================


class TestStrategyWeights:
    """Verify the _STRATEGY_WEIGHTS constants are internally consistent."""

    def test_all_three_strategies_present(self):
        assert RecommendationStrategy.balanced in _STRATEGY_WEIGHTS
        assert RecommendationStrategy.capability_match in _STRATEGY_WEIGHTS
        assert RecommendationStrategy.historical_performance in _STRATEGY_WEIGHTS

    def test_balanced_capability_weight_is_thirty_pct(self):
        w_cap, _, _, _ = _STRATEGY_WEIGHTS[RecommendationStrategy.balanced]
        assert w_cap == pytest.approx(0.30)

    def test_capability_match_strategy_doubles_capability_weight(self):
        bal_cap, *_ = _STRATEGY_WEIGHTS[RecommendationStrategy.balanced]
        cm_cap, *_ = _STRATEGY_WEIGHTS[RecommendationStrategy.capability_match]
        assert cm_cap == pytest.approx(bal_cap * 2)

    def test_historical_performance_strategy_has_highest_history_weight(self):
        _, bal_hist, _, _ = _STRATEGY_WEIGHTS[RecommendationStrategy.balanced]
        _, hp_hist, _, _ = _STRATEGY_WEIGHTS[RecommendationStrategy.historical_performance]
        assert hp_hist > bal_hist
