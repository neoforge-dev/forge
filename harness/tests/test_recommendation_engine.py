"""Comprehensive tests for the RecommendationEngine service (DF-3001).

Coverage targets:
- Singleton pattern: get_recommendation_engine / reset_recommendation_engine
- RecommendationEngine.__init__ (history loading, injected vs default deps)
- recommend() — all 6 steps, including all 3 strategies
- record_outcome() — happy path, negative lead_time raises ValueError
- get_agent_history() — with and without history
- get_recommendation_log() — ordering, limit clamping, log cap
- _filter_by_path_locks() — no conflicts, partial conflicts, all filtered
- _AgentHistory — pass_rate, avg_lead_time_seconds properties
- _load_history — missing file, valid records, corrupted records, empty lines
- _persist_outcome — success and OSError path
- Thread-safety smoke test
- Score formula validation for each strategy weight set
"""

from __future__ import annotations

import json
import threading
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from forge_harness.webhook_server.models.agent_load import AgentCapability
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.path_lock import LockConflict
from forge_harness.webhook_server.models.recommendation import (
    AgentScore,
    RecommendationStrategy,
    TaskRecommendation,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane
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
# Helpers — build cheap mock LoadBalancer and PathLockRegistry
# ---------------------------------------------------------------------------


def _make_capability(
    agent_id: str,
    lanes: list[WorkCellLane],
    *,
    max_concurrent: int = 4,
    current_active: int = 0,
    context_budget_remaining_pct: float = 100.0,
    last_heartbeat_age_seconds: float = 5.0,
) -> AgentCapability:
    """Return an AgentCapability with sensible defaults."""
    return AgentCapability(
        agent_id=agent_id,
        supported_lanes=lanes,
        max_concurrent=max_concurrent,
        current_active=current_active,
        context_budget_remaining_pct=context_budget_remaining_pct,
        last_heartbeat_age_seconds=last_heartbeat_age_seconds,
    )


def _make_mock_lb(agents: dict[str, AgentCapability]) -> MagicMock:
    """Return a mock LoadBalancer whose get_agent_stats() returns *agents*."""
    lb = MagicMock()
    lb.get_agent_stats.return_value = agents
    return lb


def _make_mock_plr(conflicts_by_path: dict[str, list[LockConflict]] | None = None) -> MagicMock:
    """Return a mock PathLockRegistry.

    Args:
        conflicts_by_path: Mapping of path → list of LockConflict objects to
            return from check_conflicts().  Defaults to empty (no conflicts).
    """
    plr = MagicMock()
    mapping = conflicts_by_path or {}
    plr.check_conflicts.side_effect = lambda path: mapping.get(path, [])
    return plr


def _make_conflict(agent_id: str, path: str, reason: str = "exclusive lock") -> LockConflict:
    return LockConflict(
        lock_id="lock-abc",
        agent_id=agent_id,
        path=path,
        conflict_reason=reason,
    )


def _make_engine(
    agents: dict[str, AgentCapability] | None = None,
    conflicts: dict[str, list[LockConflict]] | None = None,
    history_path: str = "/dev/null",
) -> RecommendationEngine:
    """Build an isolated RecommendationEngine with mocked dependencies."""
    lb = _make_mock_lb(agents or {})
    plr = _make_mock_plr(conflicts)
    return RecommendationEngine(
        load_balancer=lb,
        history_path=history_path,
        path_lock_registry=plr,
    )


# ---------------------------------------------------------------------------
# Tests — _AgentHistory
# ---------------------------------------------------------------------------


class TestAgentHistory:
    """Unit tests for the internal _AgentHistory state container."""

    def test_initial_state(self):
        h = _AgentHistory()
        assert h.total_tasks == 0
        assert h.passed_tasks == 0
        assert h.total_lead_time == 0.0

    def test_pass_rate_no_data_returns_neutral_prior(self):
        h = _AgentHistory()
        assert h.pass_rate == _DEFAULT_HISTORICAL_PASS_RATE

    def test_pass_rate_all_passed(self):
        h = _AgentHistory()
        h.record(passed=True, lead_time_seconds=10.0)
        h.record(passed=True, lead_time_seconds=20.0)
        assert h.pass_rate == 1.0

    def test_pass_rate_none_passed(self):
        h = _AgentHistory()
        h.record(passed=False, lead_time_seconds=5.0)
        assert h.pass_rate == 0.0

    def test_pass_rate_partial(self):
        h = _AgentHistory()
        h.record(passed=True, lead_time_seconds=10.0)
        h.record(passed=False, lead_time_seconds=10.0)
        assert h.pass_rate == 0.5

    def test_avg_lead_time_no_data_returns_none(self):
        h = _AgentHistory()
        assert h.avg_lead_time_seconds is None

    def test_avg_lead_time_single_record(self):
        h = _AgentHistory()
        h.record(passed=True, lead_time_seconds=42.0)
        assert h.avg_lead_time_seconds == 42.0

    def test_avg_lead_time_multiple_records(self):
        h = _AgentHistory()
        h.record(passed=True, lead_time_seconds=10.0)
        h.record(passed=False, lead_time_seconds=30.0)
        assert h.avg_lead_time_seconds == 20.0

    def test_record_increments_total_tasks(self):
        h = _AgentHistory()
        for i in range(5):
            h.record(passed=True, lead_time_seconds=1.0)
        assert h.total_tasks == 5

    def test_record_false_does_not_increment_passed(self):
        h = _AgentHistory()
        h.record(passed=False, lead_time_seconds=1.0)
        assert h.passed_tasks == 0
        assert h.total_tasks == 1

    def test_accumulates_lead_time(self):
        h = _AgentHistory()
        h.record(passed=True, lead_time_seconds=5.0)
        h.record(passed=True, lead_time_seconds=15.0)
        assert h.total_lead_time == 20.0


# ---------------------------------------------------------------------------
# Tests — singleton pattern
# ---------------------------------------------------------------------------


class TestSingletonPattern:
    """Verify get_recommendation_engine and reset_recommendation_engine."""

    def setup_method(self):
        reset_recommendation_engine()

    def teardown_method(self):
        reset_recommendation_engine()

    def test_get_returns_same_instance(self):
        with patch(
            "forge_harness.webhook_server.services.recommendation_engine.get_load_balancer"
        ) as mock_lb, patch(
            "forge_harness.webhook_server.services.recommendation_engine.get_path_lock_registry"
        ) as mock_plr:
            mock_lb.return_value = _make_mock_lb({})
            mock_plr.return_value = _make_mock_plr()
            a = get_recommendation_engine()
            b = get_recommendation_engine()
            assert a is b

    def test_reset_clears_singleton(self):
        with patch(
            "forge_harness.webhook_server.services.recommendation_engine.get_load_balancer"
        ) as mock_lb, patch(
            "forge_harness.webhook_server.services.recommendation_engine.get_path_lock_registry"
        ) as mock_plr:
            mock_lb.return_value = _make_mock_lb({})
            mock_plr.return_value = _make_mock_plr()
            a = get_recommendation_engine()
            reset_recommendation_engine()
            b = get_recommendation_engine()
            assert a is not b

    def test_reset_idempotent_when_no_singleton(self):
        # Should not raise even if called before any singleton exists
        reset_recommendation_engine()
        reset_recommendation_engine()

    def test_singleton_is_thread_safe(self):
        """Multiple threads calling get_recommendation_engine get the same object."""
        instances: list[RecommendationEngine] = []
        lock = threading.Lock()

        with patch(
            "forge_harness.webhook_server.services.recommendation_engine.get_load_balancer"
        ) as mock_lb, patch(
            "forge_harness.webhook_server.services.recommendation_engine.get_path_lock_registry"
        ) as mock_plr:
            mock_lb.return_value = _make_mock_lb({})
            mock_plr.return_value = _make_mock_plr()

            def _grab():
                inst = get_recommendation_engine()
                with lock:
                    instances.append(inst)

            threads = [threading.Thread(target=_grab) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(instances) == 8
        assert all(i is instances[0] for i in instances)


# ---------------------------------------------------------------------------
# Tests — __init__ and dependency injection
# ---------------------------------------------------------------------------


class TestInit:
    """Test constructor behaviour with injected and default dependencies."""

    def test_accepts_explicit_load_balancer(self):
        lb = _make_mock_lb({})
        plr = _make_mock_plr()
        engine = RecommendationEngine(
            load_balancer=lb, history_path="/dev/null", path_lock_registry=plr
        )
        assert engine._lb is lb

    def test_accepts_explicit_path_lock_registry(self):
        lb = _make_mock_lb({})
        plr = _make_mock_plr()
        engine = RecommendationEngine(
            load_balancer=lb, history_path="/dev/null", path_lock_registry=plr
        )
        assert engine._plr is plr

    def test_uses_global_lb_when_none_passed(self):
        with patch(
            "forge_harness.webhook_server.services.recommendation_engine.get_load_balancer"
        ) as mock_get_lb, patch(
            "forge_harness.webhook_server.services.recommendation_engine.get_path_lock_registry"
        ) as mock_get_plr:
            fake_lb = _make_mock_lb({})
            fake_plr = _make_mock_plr()
            mock_get_lb.return_value = fake_lb
            mock_get_plr.return_value = fake_plr
            engine = RecommendationEngine(history_path="/dev/null")
            assert engine._lb is fake_lb

    def test_history_path_stored_as_path_object(self):
        engine = _make_engine(history_path="/tmp/test_history.jsonl")
        assert isinstance(engine._history_path, Path)
        assert str(engine._history_path) == "/tmp/test_history.jsonl"

    def test_empty_registry_on_new_instance(self):
        engine = _make_engine()
        assert engine._history == {}

    def test_log_deque_empty_on_new_instance(self):
        engine = _make_engine()
        assert len(engine._log) == 0

    def test_log_max_len_respects_constant(self):
        engine = _make_engine()
        assert engine._log.maxlen == _MAX_LOG_ENTRIES


# ---------------------------------------------------------------------------
# Tests — _load_history
# ---------------------------------------------------------------------------


class TestLoadHistory:
    """Verify JSONL history replay on startup."""

    def test_no_file_does_not_raise(self):
        # /dev/null exists but is empty; a random path that doesn't exist
        engine = _make_engine(history_path="/tmp/nonexistent_forge_history_xyz.jsonl")
        assert engine._history == {}

    def test_valid_records_loaded(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        records = [
            {"type": "outcome", "task_id": "t1", "agent_id": "forge:nova", "passed": True, "lead_time_seconds": 10.0},
            {"type": "outcome", "task_id": "t2", "agent_id": "forge:nova", "passed": False, "lead_time_seconds": 5.0},
            {"type": "outcome", "task_id": "t3", "agent_id": "forge:sati", "passed": True, "lead_time_seconds": 20.0},
        ]
        history_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        engine = _make_engine(history_path=str(history_file))

        nova_hist = engine._history.get("forge:nova")
        assert nova_hist is not None
        assert nova_hist.total_tasks == 2
        assert nova_hist.passed_tasks == 1

        sati_hist = engine._history.get("forge:sati")
        assert sati_hist is not None
        assert sati_hist.total_tasks == 1
        assert sati_hist.pass_rate == 1.0

    def test_corrupted_lines_skipped(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        history_file.write_text(
            "not valid json\n"
            '{"broken": true}\n'
            '{"type": "outcome", "agent_id": "forge:nova", "passed": true, "lead_time_seconds": 7.0}\n'
        )
        engine = _make_engine(history_path=str(history_file))
        # Only the valid outcome record should be loaded
        assert "forge:nova" in engine._history
        assert engine._history["forge:nova"].total_tasks == 1

    def test_empty_lines_skipped(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        history_file.write_text("\n\n\n")
        engine = _make_engine(history_path=str(history_file))
        assert engine._history == {}

    def test_records_missing_agent_id_skipped(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        history_file.write_text(
            '{"type": "outcome", "passed": true, "lead_time_seconds": 5.0}\n'
        )
        engine = _make_engine(history_path=str(history_file))
        assert engine._history == {}

    def test_records_missing_passed_field_skipped(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        history_file.write_text(
            '{"type": "outcome", "agent_id": "forge:nova", "lead_time_seconds": 5.0}\n'
        )
        engine = _make_engine(history_path=str(history_file))
        assert engine._history == {}

    def test_missing_lead_time_defaults_to_zero(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        history_file.write_text(
            '{"type": "outcome", "agent_id": "forge:nova", "passed": true}\n'
        )
        engine = _make_engine(history_path=str(history_file))
        assert engine._history["forge:nova"].total_lead_time == 0.0

    def test_mixed_valid_and_invalid_lines(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        history_file.write_text(
            '{"type": "outcome", "agent_id": "forge:a", "passed": true, "lead_time_seconds": 1.0}\n'
            "GARBAGE LINE\n"
            '{"type": "outcome", "agent_id": "forge:b", "passed": false, "lead_time_seconds": 2.0}\n'
        )
        engine = _make_engine(history_path=str(history_file))
        assert "forge:a" in engine._history
        assert "forge:b" in engine._history


# ---------------------------------------------------------------------------
# Tests — recommend() — no agents / all filtered
# ---------------------------------------------------------------------------


class TestRecommendEmptyCases:
    """Edge cases where recommend() returns an empty recommendation."""

    def test_no_agents_registered(self):
        engine = _make_engine(agents={})
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert isinstance(rec, TaskRecommendation)
        assert rec.task_id == "t1"
        assert rec.recommended_agents == []
        assert rec.confidence == 0.0
        assert any("No agents registered" in r for r in rec.reasoning)

    def test_no_agents_support_target_lane(self):
        agents = {
            "forge:nova": _make_capability(
                "forge:nova", [WorkCellLane.docs]
            )
        }
        engine = _make_engine(agents=agents)
        # bug_fix + low → api_simple lane; docs lane agent won't match
        rec = engine.recommend("t2", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents == []
        assert rec.confidence == 0.0
        assert any("No agents support lane" in r for r in rec.reasoning)

    def test_all_agents_filtered_by_path_locks(self):
        agents = {
            "forge:nova": _make_capability(
                "forge:nova", [WorkCellLane.api_simple]
            )
        }
        conflicts = {
            "/src/main.py": [_make_conflict("forge:nova", "/src/main.py")]
        }
        engine = _make_engine(agents=agents, conflicts=conflicts)
        rec = engine.recommend(
            "t3",
            TaskType.bug_fix,
            RiskTier.low,
            task_paths=["/src/main.py"],
        )
        assert rec.recommended_agents == []
        assert rec.confidence == 0.0
        assert any("conflicting path locks" in r for r in rec.reasoning)

    def test_empty_recommendation_appended_to_log(self):
        engine = _make_engine(agents={})
        engine.recommend("t-empty", TaskType.docs_update, RiskTier.low)
        log = engine.get_recommendation_log(limit=1)
        assert len(log) == 1
        assert log[0].task_id == "t-empty"


# ---------------------------------------------------------------------------
# Tests — recommend() — happy path with one agent
# ---------------------------------------------------------------------------


class TestRecommendSingleAgent:
    """Verify scoring when exactly one eligible agent is present."""

    def _engine_with_one_agent(self, **cap_kwargs) -> RecommendationEngine:
        agents = {
            "forge:nova": _make_capability(
                "forge:nova", [WorkCellLane.api_simple], **cap_kwargs
            )
        }
        return _make_engine(agents=agents)

    def test_returns_task_recommendation(self):
        engine = self._engine_with_one_agent()
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert isinstance(rec, TaskRecommendation)

    def test_single_agent_is_recommended(self):
        engine = self._engine_with_one_agent()
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert len(rec.recommended_agents) == 1
        assert rec.recommended_agents[0].agent_id == "forge:nova"

    def test_confidence_equals_top_score_when_one_agent(self):
        engine = self._engine_with_one_agent(
            max_concurrent=4, current_active=0, context_budget_remaining_pct=100.0
        )
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.confidence == rec.recommended_agents[0].score

    def test_score_clamped_to_zero_one(self):
        engine = self._engine_with_one_agent(
            max_concurrent=4, current_active=0, context_budget_remaining_pct=100.0
        )
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        score = rec.recommended_agents[0].score
        assert 0.0 <= score <= 1.0

    def test_factors_dict_contains_all_keys(self):
        engine = self._engine_with_one_agent()
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        factors = rec.recommended_agents[0].factors
        assert "capability_match" in factors
        assert "historical_pass_rate" in factors
        assert "current_load" in factors
        assert "context_budget" in factors

    def test_capability_match_factor_is_1(self):
        engine = self._engine_with_one_agent()
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["capability_match"] == 1.0

    def test_historical_pass_rate_factor_is_neutral_prior_when_no_history(self):
        engine = self._engine_with_one_agent()
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["historical_pass_rate"] == _DEFAULT_HISTORICAL_PASS_RATE

    def test_historical_pass_rate_is_none_when_no_history(self):
        engine = self._engine_with_one_agent()
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].historical_pass_rate is None

    def test_historical_pass_rate_present_after_recording_outcome(self):
        engine = self._engine_with_one_agent()
        engine.record_outcome("t0", "forge:nova", passed=True, lead_time_seconds=10.0)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].historical_pass_rate == 1.0

    def test_current_load_factor_zero_when_at_capacity(self):
        engine = self._engine_with_one_agent(max_concurrent=4, current_active=4)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["current_load"] == 0.0

    def test_current_load_factor_one_when_idle(self):
        engine = self._engine_with_one_agent(max_concurrent=4, current_active=0)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["current_load"] == 1.0

    def test_context_budget_factor_proportional(self):
        engine = self._engine_with_one_agent(context_budget_remaining_pct=50.0)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["context_budget"] == pytest.approx(0.5)

    def test_avg_lead_time_none_when_no_history(self):
        engine = self._engine_with_one_agent()
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].avg_lead_time_seconds is None

    def test_avg_lead_time_set_after_recording_outcome(self):
        engine = self._engine_with_one_agent()
        engine.record_outcome("t0", "forge:nova", passed=True, lead_time_seconds=42.0)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].avg_lead_time_seconds == 42.0

    def test_reasoning_contains_top_agent_info(self):
        engine = self._engine_with_one_agent()
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert any("forge:nova" in r for r in rec.reasoning)


# ---------------------------------------------------------------------------
# Tests — recommend() — multiple agents, ordering, confidence
# ---------------------------------------------------------------------------


class TestRecommendMultipleAgents:
    """Verify ranking, confidence calculation, and tie-breaking."""

    def test_agents_sorted_by_score_descending(self):
        agents = {
            "forge:low": _make_capability(
                "forge:low",
                [WorkCellLane.api_simple],
                current_active=3,
                max_concurrent=4,
                context_budget_remaining_pct=10.0,
            ),
            "forge:high": _make_capability(
                "forge:high",
                [WorkCellLane.api_simple],
                current_active=0,
                max_concurrent=4,
                context_budget_remaining_pct=100.0,
            ),
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].agent_id == "forge:high"
        assert rec.recommended_agents[1].agent_id == "forge:low"

    def test_confidence_is_score_spread(self):
        agents = {
            "forge:a": _make_capability(
                "forge:a",
                [WorkCellLane.api_simple],
                current_active=0,
                max_concurrent=4,
                context_budget_remaining_pct=100.0,
            ),
            "forge:b": _make_capability(
                "forge:b",
                [WorkCellLane.api_simple],
                current_active=3,
                max_concurrent=4,
                context_budget_remaining_pct=10.0,
            ),
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        expected_confidence = round(
            rec.recommended_agents[0].score - rec.recommended_agents[1].score, 6
        )
        assert rec.confidence == pytest.approx(expected_confidence, abs=1e-5)

    def test_confidence_clamped_to_zero_one(self):
        agents = {
            "forge:a": _make_capability("forge:a", [WorkCellLane.api_simple]),
            "forge:b": _make_capability("forge:b", [WorkCellLane.api_simple]),
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert 0.0 <= rec.confidence <= 1.0

    def test_tie_broken_by_agent_id_alphabetically(self):
        """When scores are equal, agents are ordered alphabetically by agent_id."""
        agents = {
            "forge:zzz": _make_capability(
                "forge:zzz",
                [WorkCellLane.api_simple],
                current_active=0,
                max_concurrent=4,
                context_budget_remaining_pct=100.0,
            ),
            "forge:aaa": _make_capability(
                "forge:aaa",
                [WorkCellLane.api_simple],
                current_active=0,
                max_concurrent=4,
                context_budget_remaining_pct=100.0,
            ),
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        # Same score → alphabetical tie-break
        assert rec.recommended_agents[0].agent_id == "forge:aaa"

    def test_only_lane_eligible_agents_scored(self):
        agents = {
            "forge:docs": _make_capability("forge:docs", [WorkCellLane.docs]),
            "forge:api": _make_capability("forge:api", [WorkCellLane.api_simple]),
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        # Only forge:api supports api_simple
        assert len(rec.recommended_agents) == 1
        assert rec.recommended_agents[0].agent_id == "forge:api"


# ---------------------------------------------------------------------------
# Tests — recommend() — preferred_lane override
# ---------------------------------------------------------------------------


class TestRecommendPreferredLane:
    def test_preferred_lane_bypasses_resolver(self):
        """When preferred_lane is supplied, the normal resolver is bypassed."""
        # Use docs lane directly even though bug_fix+low normally → api_simple
        agents = {
            "forge:docs": _make_capability("forge:docs", [WorkCellLane.docs]),
            "forge:api": _make_capability("forge:api", [WorkCellLane.api_simple]),
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend(
            "t1",
            TaskType.bug_fix,
            RiskTier.low,
            preferred_lane=WorkCellLane.docs,
        )
        assert len(rec.recommended_agents) == 1
        assert rec.recommended_agents[0].agent_id == "forge:docs"

    def test_preferred_lane_appears_in_reasoning(self):
        agents = {
            "forge:docs": _make_capability("forge:docs", [WorkCellLane.docs]),
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend(
            "t1",
            TaskType.bug_fix,
            RiskTier.low,
            preferred_lane=WorkCellLane.docs,
        )
        assert any("overridden" in r.lower() for r in rec.reasoning)

    def test_resolver_lane_appears_in_reasoning_when_no_override(self):
        agents = {
            "forge:api": _make_capability("forge:api", [WorkCellLane.api_simple]),
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend("t1", TaskType.bug_fix, RiskTier.low)
        assert any("resolved" in r.lower() for r in rec.reasoning)


# ---------------------------------------------------------------------------
# Tests — recommend() — all 3 scoring strategies
# ---------------------------------------------------------------------------


class TestRecommendStrategies:
    """Verify each strategy uses its declared weight set."""

    def _score_with_strategy(
        self,
        strategy: RecommendationStrategy,
        current_active: int = 2,
        max_concurrent: int = 4,
        context_budget: float = 60.0,
        pass_rate: float = 1.0,
    ) -> float:
        agents = {
            "forge:nova": _make_capability(
                "forge:nova",
                [WorkCellLane.api_simple],
                current_active=current_active,
                max_concurrent=max_concurrent,
                context_budget_remaining_pct=context_budget,
            )
        }
        engine = _make_engine(agents=agents)
        # Seed history to get a known pass rate
        for _ in range(10):
            engine.record_outcome(
                "seed", "forge:nova", passed=(pass_rate == 1.0), lead_time_seconds=1.0
            )
        with patch.object(engine, "_persist_outcome"):
            pass  # already called above; just suppress more
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low, strategy=strategy)
        return rec.recommended_agents[0].score

    def test_balanced_strategy_uses_default_weights(self):
        agents = {
            "forge:nova": _make_capability(
                "forge:nova",
                [WorkCellLane.api_simple],
                current_active=0,
                max_concurrent=4,
                context_budget_remaining_pct=100.0,
            )
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend(
            "t", TaskType.bug_fix, RiskTier.low, strategy=RecommendationStrategy.balanced
        )
        score = rec.recommended_agents[0].score
        # With all factors at max: 0.30 + 0.25*0.5 + 0.25 + 0.20 = 0.875
        # (neutral prior since no history)
        expected = 0.30 * 1.0 + 0.25 * 0.5 + 0.25 * 1.0 + 0.20 * 1.0
        assert score == pytest.approx(expected, abs=1e-4)

    def test_capability_match_strategy_weights_capability_heavily(self):
        """Capability weight should be 0.60 for capability_match strategy."""
        w_cap, w_hist, w_load, w_budget = _STRATEGY_WEIGHTS[RecommendationStrategy.capability_match]
        assert w_cap == pytest.approx(0.60)

    def test_historical_performance_strategy_weights_history_heavily(self):
        """Historical weight should be 0.50 for historical_performance strategy."""
        _, w_hist, _, _ = _STRATEGY_WEIGHTS[RecommendationStrategy.historical_performance]
        assert w_hist == pytest.approx(0.50)

    def test_all_strategy_weights_sum_to_one(self):
        for strategy, weights in _STRATEGY_WEIGHTS.items():
            assert sum(weights) == pytest.approx(1.0), f"{strategy} weights don't sum to 1.0"

    def test_capability_match_strategy_returns_recommendation(self):
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple])
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend(
            "t",
            TaskType.bug_fix,
            RiskTier.low,
            strategy=RecommendationStrategy.capability_match,
        )
        assert len(rec.recommended_agents) == 1

    def test_historical_performance_strategy_returns_recommendation(self):
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple])
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend(
            "t",
            TaskType.bug_fix,
            RiskTier.low,
            strategy=RecommendationStrategy.historical_performance,
        )
        assert len(rec.recommended_agents) == 1

    def test_strategy_affects_score(self):
        """Different strategies should produce different scores for unequal factor values."""
        agents = {
            "forge:nova": _make_capability(
                "forge:nova",
                [WorkCellLane.api_simple],
                current_active=2,
                max_concurrent=4,
                context_budget_remaining_pct=40.0,
            )
        }
        engine = _make_engine(agents=agents)
        engine.record_outcome("seed", "forge:nova", passed=True, lead_time_seconds=1.0)

        scores = {}
        for s in RecommendationStrategy:
            rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low, strategy=s)
            scores[s] = rec.recommended_agents[0].score

        # Scores should differ because factor values are not equal
        assert len(set(scores.values())) > 1, "All strategies produced identical scores"


# ---------------------------------------------------------------------------
# Tests — recommend() — path lock filtering
# ---------------------------------------------------------------------------


class TestRecommendPathLockFiltering:
    def test_task_paths_none_skips_filter(self):
        """Passing no task_paths should not call check_conflicts at all."""
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple])
        }
        plr = _make_mock_plr()
        lb = _make_mock_lb(agents)
        engine = RecommendationEngine(
            load_balancer=lb, history_path="/dev/null", path_lock_registry=plr
        )
        engine.recommend("t", TaskType.bug_fix, RiskTier.low, task_paths=None)
        plr.check_conflicts.assert_not_called()

    def test_empty_task_paths_skips_filter(self):
        """Passing an empty task_paths list should not call check_conflicts."""
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple])
        }
        plr = _make_mock_plr()
        lb = _make_mock_lb(agents)
        engine = RecommendationEngine(
            load_balancer=lb, history_path="/dev/null", path_lock_registry=plr
        )
        engine.recommend("t", TaskType.bug_fix, RiskTier.low, task_paths=[])
        plr.check_conflicts.assert_not_called()

    def test_conflicting_agent_excluded(self):
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple]),
            "forge:sati": _make_capability("forge:sati", [WorkCellLane.api_simple]),
        }
        # nova holds a lock on the target path
        conflicts = {
            "/src/app.py": [_make_conflict("forge:nova", "/src/app.py")]
        }
        engine = _make_engine(agents=agents, conflicts=conflicts)
        rec = engine.recommend(
            "t", TaskType.bug_fix, RiskTier.low, task_paths=["/src/app.py"]
        )
        agent_ids = [a.agent_id for a in rec.recommended_agents]
        assert "forge:nova" not in agent_ids
        assert "forge:sati" in agent_ids

    def test_excluded_agent_in_reasoning(self):
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple]),
        }
        conflicts = {
            "/src/app.py": [_make_conflict("forge:nova", "/src/app.py")]
        }
        engine = _make_engine(agents=agents, conflicts=conflicts)
        rec = engine.recommend(
            "t", TaskType.bug_fix, RiskTier.low, task_paths=["/src/app.py"]
        )
        assert any("forge:nova" in r and "excluded" in r for r in rec.reasoning)

    def test_non_conflicting_paths_preserved(self):
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple]),
        }
        # The path in task_paths has no conflict
        conflicts: dict[str, list[LockConflict]] = {}
        engine = _make_engine(agents=agents, conflicts=conflicts)
        rec = engine.recommend(
            "t", TaskType.bug_fix, RiskTier.low, task_paths=["/src/unrelated.py"]
        )
        assert len(rec.recommended_agents) == 1
        assert any("no conflicts found" in r.lower() for r in rec.reasoning)

    def test_multiple_paths_each_checked(self):
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple])
        }
        plr = _make_mock_plr()
        lb = _make_mock_lb(agents)
        engine = RecommendationEngine(
            load_balancer=lb, history_path="/dev/null", path_lock_registry=plr
        )
        task_paths = ["/a.py", "/b.py", "/c.py"]
        engine.recommend("t", TaskType.bug_fix, RiskTier.low, task_paths=task_paths)
        assert plr.check_conflicts.call_count == 3

    def test_agent_with_lock_not_in_eligible_not_excluded(self):
        """Conflicts from agents not in the eligible set are silently ignored."""
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple])
        }
        # The conflict is from an agent that isn't in eligible (wrong lane)
        conflicts = {
            "/src/app.py": [_make_conflict("forge:other", "/src/app.py")]
        }
        engine = _make_engine(agents=agents, conflicts=conflicts)
        rec = engine.recommend(
            "t", TaskType.bug_fix, RiskTier.low, task_paths=["/src/app.py"]
        )
        # forge:nova should still be recommended; conflict is from non-eligible agent
        assert len(rec.recommended_agents) == 1
        assert rec.recommended_agents[0].agent_id == "forge:nova"


# ---------------------------------------------------------------------------
# Tests — record_outcome()
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    def test_records_outcome_in_history(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        engine = _make_engine(history_path=str(history_file))
        engine.record_outcome("t1", "forge:nova", passed=True, lead_time_seconds=15.0)
        hist = engine._history.get("forge:nova")
        assert hist is not None
        assert hist.total_tasks == 1
        assert hist.pass_rate == 1.0

    def test_multiple_outcomes_accumulate(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        engine = _make_engine(history_path=str(history_file))
        engine.record_outcome("t1", "forge:nova", passed=True, lead_time_seconds=10.0)
        engine.record_outcome("t2", "forge:nova", passed=False, lead_time_seconds=5.0)
        engine.record_outcome("t3", "forge:nova", passed=True, lead_time_seconds=15.0)
        hist = engine._history["forge:nova"]
        assert hist.total_tasks == 3
        assert hist.passed_tasks == 2

    def test_negative_lead_time_raises_value_error(self):
        engine = _make_engine()
        with pytest.raises(ValueError, match="lead_time_seconds must be >= 0"):
            engine.record_outcome("t1", "forge:nova", passed=True, lead_time_seconds=-1.0)

    def test_zero_lead_time_is_accepted(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        engine = _make_engine(history_path=str(history_file))
        engine.record_outcome("t1", "forge:nova", passed=True, lead_time_seconds=0.0)
        assert engine._history["forge:nova"].total_lead_time == 0.0

    def test_creates_new_history_entry_for_new_agent(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        engine = _make_engine(history_path=str(history_file))
        assert "forge:brand-new" not in engine._history
        engine.record_outcome("t1", "forge:brand-new", passed=True, lead_time_seconds=5.0)
        assert "forge:brand-new" in engine._history

    def test_persists_to_jsonl_file(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        engine = _make_engine(history_path=str(history_file))
        engine.record_outcome("t1", "forge:nova", passed=True, lead_time_seconds=7.0)
        content = history_file.read_text()
        record = json.loads(content.strip())
        assert record["agent_id"] == "forge:nova"
        assert record["passed"] is True
        assert record["lead_time_seconds"] == 7.0
        assert record["task_id"] == "t1"

    def test_persist_oserror_does_not_raise(self):
        """Write failures are swallowed — record_outcome must not re-raise."""
        engine = _make_engine()
        # Patch the open call on the history_path to raise OSError; the
        # Path.parent.mkdir step is harder to intercept, so we patch the
        # _persist_outcome method's internal open call via builtins.
        with patch(
            "forge_harness.webhook_server.services.recommendation_engine.Path.open",
            side_effect=OSError("disk full"),
        ):
            # Should not raise despite I/O failure
            engine.record_outcome("t1", "forge:nova", passed=True, lead_time_seconds=1.0)
        # History must still be updated in memory even if persistence failed
        assert "forge:nova" in engine._history

    def test_multiple_agents_tracked_independently(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        engine = _make_engine(history_path=str(history_file))
        engine.record_outcome("t1", "forge:nova", passed=True, lead_time_seconds=10.0)
        engine.record_outcome("t2", "forge:sati", passed=False, lead_time_seconds=5.0)
        assert engine._history["forge:nova"].pass_rate == 1.0
        assert engine._history["forge:sati"].pass_rate == 0.0


# ---------------------------------------------------------------------------
# Tests — get_agent_history()
# ---------------------------------------------------------------------------


class TestGetAgentHistory:
    def test_unknown_agent_returns_defaults(self):
        engine = _make_engine()
        result = engine.get_agent_history("forge:unknown")
        assert result["pass_rate"] == _DEFAULT_HISTORICAL_PASS_RATE
        assert result["avg_lead_time"] is None
        assert result["total_tasks"] == 0

    def test_known_agent_returns_recorded_stats(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        engine = _make_engine(history_path=str(history_file))
        engine.record_outcome("t1", "forge:nova", passed=True, lead_time_seconds=20.0)
        engine.record_outcome("t2", "forge:nova", passed=False, lead_time_seconds=10.0)
        result = engine.get_agent_history("forge:nova")
        assert result["pass_rate"] == 0.5
        assert result["avg_lead_time"] == 15.0
        assert result["total_tasks"] == 2

    def test_returns_dict_with_expected_keys(self):
        engine = _make_engine()
        result = engine.get_agent_history("forge:x")
        assert set(result.keys()) == {"pass_rate", "avg_lead_time", "total_tasks"}

    def test_avg_lead_time_none_when_no_tasks(self):
        engine = _make_engine()
        result = engine.get_agent_history("forge:nobody")
        assert result["avg_lead_time"] is None


# ---------------------------------------------------------------------------
# Tests — get_recommendation_log()
# ---------------------------------------------------------------------------


class TestGetRecommendationLog:
    def test_empty_log_returns_empty_list(self):
        engine = _make_engine()
        assert engine.get_recommendation_log() == []

    def test_entries_returned_most_recent_first(self):
        engine = _make_engine(agents={})
        engine.recommend("t1", TaskType.docs_update, RiskTier.low)
        engine.recommend("t2", TaskType.docs_update, RiskTier.low)
        engine.recommend("t3", TaskType.docs_update, RiskTier.low)
        log = engine.get_recommendation_log()
        task_ids = [r.task_id for r in log]
        assert task_ids == ["t3", "t2", "t1"]

    def test_limit_parameter_respected(self):
        engine = _make_engine(agents={})
        for i in range(10):
            engine.recommend(f"t{i}", TaskType.docs_update, RiskTier.low)
        log = engine.get_recommendation_log(limit=3)
        assert len(log) == 3

    def test_default_limit_is_20(self):
        engine = _make_engine(agents={})
        for i in range(30):
            engine.recommend(f"t{i}", TaskType.docs_update, RiskTier.low)
        log = engine.get_recommendation_log()
        assert len(log) == 20

    def test_log_cap_at_max_log_entries(self):
        engine = _make_engine(agents={})
        for i in range(_MAX_LOG_ENTRIES + 10):
            engine.recommend(f"t{i}", TaskType.docs_update, RiskTier.low)
        # Deque maxlen silently discards oldest entries
        assert len(engine._log) == _MAX_LOG_ENTRIES

    def test_limit_larger_than_log_returns_all(self):
        engine = _make_engine(agents={})
        engine.recommend("t1", TaskType.docs_update, RiskTier.low)
        log = engine.get_recommendation_log(limit=100)
        assert len(log) == 1

    def test_returned_entries_are_task_recommendation_instances(self):
        engine = _make_engine(agents={})
        engine.recommend("t1", TaskType.docs_update, RiskTier.low)
        log = engine.get_recommendation_log()
        assert all(isinstance(r, TaskRecommendation) for r in log)


# ---------------------------------------------------------------------------
# Tests — lane resolution
# ---------------------------------------------------------------------------


class TestLaneResolution:
    """Verify lane resolver integration within recommend()."""

    def _engine_with_lane(self, lane: WorkCellLane) -> RecommendationEngine:
        agents = {
            "forge:nova": _make_capability("forge:nova", [lane])
        }
        return _make_engine(agents=agents)

    def test_bug_fix_low_resolves_to_api_simple(self):
        engine = self._engine_with_lane(WorkCellLane.api_simple)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert len(rec.recommended_agents) == 1

    def test_bug_fix_medium_resolves_to_api_stateful(self):
        engine = self._engine_with_lane(WorkCellLane.api_stateful)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.medium)
        assert len(rec.recommended_agents) == 1

    def test_security_change_resolves_to_security_change_lane(self):
        engine = self._engine_with_lane(WorkCellLane.security_change)
        rec = engine.recommend("t", TaskType.security_change, RiskTier.low)
        assert len(rec.recommended_agents) == 1

    def test_deployment_resolves_to_deployment_lane(self):
        engine = self._engine_with_lane(WorkCellLane.deployment)
        rec = engine.recommend("t", TaskType.deployment, RiskTier.high)
        assert len(rec.recommended_agents) == 1

    def test_test_writing_resolves_to_test_writing_lane(self):
        engine = self._engine_with_lane(WorkCellLane.test_writing)
        rec = engine.recommend("t", TaskType.test_writing, RiskTier.critical)
        assert len(rec.recommended_agents) == 1

    def test_docs_update_resolves_to_docs_lane(self):
        engine = self._engine_with_lane(WorkCellLane.docs)
        rec = engine.recommend("t", TaskType.docs_update, RiskTier.low)
        assert len(rec.recommended_agents) == 1


# ---------------------------------------------------------------------------
# Tests — thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Concurrent operations should not corrupt engine state."""

    def test_concurrent_record_outcome_is_safe(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        engine = _make_engine(history_path=str(history_file))
        errors: list[Exception] = []

        def _record(i: int):
            try:
                engine.record_outcome(
                    f"task-{i}", "forge:concurrent", passed=True, lead_time_seconds=float(i)
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_record, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety errors: {errors}"
        assert engine._history["forge:concurrent"].total_tasks == 50

    def test_concurrent_recommend_and_record_outcome(self):
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple])
        }
        engine = _make_engine(agents=agents)
        errors: list[Exception] = []

        def _recommend():
            try:
                engine.recommend("t", TaskType.bug_fix, RiskTier.low)
            except Exception as exc:
                errors.append(exc)

        def _record(i: int):
            try:
                engine.record_outcome(f"r{i}", "forge:nova", passed=True, lead_time_seconds=1.0)
            except Exception as exc:
                errors.append(exc)

        threads = (
            [threading.Thread(target=_recommend) for _ in range(20)]
            + [threading.Thread(target=_record, args=(i,)) for i in range(20)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety errors: {errors}"

    def test_concurrent_get_recommendation_log(self):
        engine = _make_engine(agents={})
        errors: list[Exception] = []

        def _log():
            try:
                engine.get_recommendation_log()
            except Exception as exc:
                errors.append(exc)

        def _recommend():
            try:
                engine.recommend("t", TaskType.docs_update, RiskTier.low)
            except Exception as exc:
                errors.append(exc)

        threads = (
            [threading.Thread(target=_log) for _ in range(15)]
            + [threading.Thread(target=_recommend) for _ in range(15)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety errors: {errors}"


# ---------------------------------------------------------------------------
# Tests — history survives restart (persistence round-trip)
# ---------------------------------------------------------------------------


class TestPersistenceRoundTrip:
    def test_outcomes_reloaded_on_new_engine_instance(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        engine1 = _make_engine(history_path=str(history_file))
        engine1.record_outcome("t1", "forge:nova", passed=True, lead_time_seconds=10.0)
        engine1.record_outcome("t2", "forge:nova", passed=False, lead_time_seconds=5.0)

        # Create a fresh engine pointing at the same file
        engine2 = _make_engine(history_path=str(history_file))
        hist = engine2.get_agent_history("forge:nova")
        assert hist["total_tasks"] == 2
        assert hist["pass_rate"] == 0.5

    def test_reloaded_history_affects_scoring(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        agents = {
            "forge:nova": _make_capability("forge:nova", [WorkCellLane.api_simple])
        }
        engine1 = _make_engine(agents=agents, history_path=str(history_file))
        # Seed a perfect pass rate
        for _ in range(5):
            engine1.record_outcome("seed", "forge:nova", passed=True, lead_time_seconds=1.0)

        engine2 = _make_engine(agents=agents, history_path=str(history_file))
        rec = engine2.recommend("t", TaskType.bug_fix, RiskTier.low)
        # The reloaded pass rate of 1.0 should make the score higher
        # than it would be with the neutral prior of 0.5
        assert rec.recommended_agents[0].historical_pass_rate == 1.0


# ---------------------------------------------------------------------------
# Preserve existing _normalize_priority tests (analytics module)
# ---------------------------------------------------------------------------

# NOTE: The tests below target `forge_harness.analytics.recommendation_engine`
# which is a different class than the webhook_server RecommendationEngine above.
# They are preserved verbatim from the original test file.


class TestNormalizePriority:
    """Test _normalize_priority handles all input types."""

    @staticmethod
    def _engine():
        from forge_harness.analytics.recommendation_engine import (
            RecommendationEngine as AnalyticsRE,
        )
        return AnalyticsRE

    def test_int_passthrough(self):
        assert self._engine()._normalize_priority(3) == 3

    def test_string_numeric(self):
        assert self._engine()._normalize_priority("5") == 5

    def test_string_named_high(self):
        assert self._engine()._normalize_priority("high") == 3

    def test_string_named_medium(self):
        assert self._engine()._normalize_priority("medium") == 2

    def test_string_named_low(self):
        assert self._engine()._normalize_priority("low") == 1

    def test_string_named_critical(self):
        assert self._engine()._normalize_priority("critical") == 4

    def test_string_named_case_insensitive(self):
        assert self._engine()._normalize_priority("HIGH") == 3

    def test_none_returns_zero(self):
        assert self._engine()._normalize_priority(None) == 0

    def test_unknown_string_returns_zero(self):
        assert self._engine()._normalize_priority("unknown") == 0

    def test_empty_string_returns_zero(self):
        assert self._engine()._normalize_priority("") == 0

    def test_float_returns_zero(self):
        assert self._engine()._normalize_priority(3.5) == 0


class TestGenerateRecommendationsTypeSafety:
    """Test that generate_recommendations handles mixed priority types."""

    def test_string_priority_no_typeerror(self):
        """Regression test: string priority should not raise TypeError."""
        from forge_harness.analytics.recommendation_engine import (
            RecommendationEngine as AnalyticsRE,
        )
        engine = AnalyticsRE()

        tasks = [
            {
                "id": "1",
                "subject": "Task A",
                "description": "Desc",
                "priority": "3",
                "status": "pending",
            },
            {
                "id": "2",
                "subject": "Task B",
                "description": "Desc",
                "priority": 2,
                "status": "pending",
            },
            {
                "id": "3",
                "subject": "Task C",
                "description": "Desc",
                "priority": "high",
                "status": "pending",
            },
        ]

        result = engine.generate_recommendations(tasks=tasks, agents=[], min_priority=1)
        assert result is not None
        assert result.meta["total_tasks"] == 3

    def test_none_priority_filtered_out(self):
        """Tasks with None priority (normalized to 0) should be filtered by min_priority=1."""
        from forge_harness.analytics.recommendation_engine import (
            RecommendationEngine as AnalyticsRE,
        )
        engine = AnalyticsRE()

        tasks = [
            {
                "id": "1",
                "subject": "No priority",
                "description": "Desc",
                "priority": None,
                "status": "pending",
            },
        ]

        result = engine.generate_recommendations(tasks=tasks, agents=[], min_priority=1)
        assert result.meta["recommended_count"] == 0


# ---------------------------------------------------------------------------
# Tests — _filter_by_path_locks (direct method tests)
# ---------------------------------------------------------------------------


class TestFilterByPathLocks:
    """Direct unit tests of the _filter_by_path_locks helper."""

    def _make_engine_with_plr(self, conflicts: dict[str, list[LockConflict]]) -> RecommendationEngine:
        agents = {
            "forge:a": _make_capability("forge:a", [WorkCellLane.api_simple]),
            "forge:b": _make_capability("forge:b", [WorkCellLane.api_simple]),
        }
        return _make_engine(agents=agents, conflicts=conflicts)

    def test_no_conflicts_returns_all_eligible(self):
        engine = self._make_engine_with_plr({})
        eligible = {
            "forge:a": _make_capability("forge:a", [WorkCellLane.api_simple]),
            "forge:b": _make_capability("forge:b", [WorkCellLane.api_simple]),
        }
        reasoning: list[str] = []
        result = engine._filter_by_path_locks(eligible, ["/src/x.py"], reasoning)
        assert set(result.keys()) == {"forge:a", "forge:b"}
        assert any("no conflicts found" in r.lower() for r in reasoning)

    def test_conflicting_agent_removed(self):
        conflicts = {
            "/src/x.py": [_make_conflict("forge:a", "/src/x.py")]
        }
        engine = self._make_engine_with_plr(conflicts)
        eligible = {
            "forge:a": _make_capability("forge:a", [WorkCellLane.api_simple]),
            "forge:b": _make_capability("forge:b", [WorkCellLane.api_simple]),
        }
        reasoning: list[str] = []
        result = engine._filter_by_path_locks(eligible, ["/src/x.py"], reasoning)
        assert "forge:a" not in result
        assert "forge:b" in result

    def test_reasoning_records_exclusion(self):
        conflicts = {
            "/src/x.py": [_make_conflict("forge:a", "/src/x.py")]
        }
        engine = self._make_engine_with_plr(conflicts)
        eligible = {
            "forge:a": _make_capability("forge:a", [WorkCellLane.api_simple]),
        }
        reasoning: list[str] = []
        engine._filter_by_path_locks(eligible, ["/src/x.py"], reasoning)
        assert any("forge:a" in r and "excluded" in r for r in reasoning)

    def test_all_eligible_removed_returns_empty_dict(self):
        conflicts = {
            "/src/x.py": [
                _make_conflict("forge:a", "/src/x.py"),
                _make_conflict("forge:b", "/src/x.py"),
            ]
        }
        engine = self._make_engine_with_plr(conflicts)
        eligible = {
            "forge:a": _make_capability("forge:a", [WorkCellLane.api_simple]),
            "forge:b": _make_capability("forge:b", [WorkCellLane.api_simple]),
        }
        reasoning: list[str] = []
        result = engine._filter_by_path_locks(eligible, ["/src/x.py"], reasoning)
        assert result == {}

    def test_multiple_paths_accumulate_exclusions(self):
        conflicts = {
            "/a.py": [_make_conflict("forge:a", "/a.py")],
            "/b.py": [_make_conflict("forge:b", "/b.py")],
        }
        engine = self._make_engine_with_plr(conflicts)
        eligible = {
            "forge:a": _make_capability("forge:a", [WorkCellLane.api_simple]),
            "forge:b": _make_capability("forge:b", [WorkCellLane.api_simple]),
            "forge:c": _make_capability("forge:c", [WorkCellLane.api_simple]),
        }
        reasoning: list[str] = []
        result = engine._filter_by_path_locks(eligible, ["/a.py", "/b.py"], reasoning)
        assert "forge:a" not in result
        assert "forge:b" not in result
        assert "forge:c" in result

    def test_conflict_from_agent_not_in_eligible_ignored(self):
        conflicts = {
            "/src/x.py": [_make_conflict("forge:outside", "/src/x.py")]
        }
        engine = self._make_engine_with_plr(conflicts)
        eligible = {
            "forge:a": _make_capability("forge:a", [WorkCellLane.api_simple]),
        }
        reasoning: list[str] = []
        result = engine._filter_by_path_locks(eligible, ["/src/x.py"], reasoning)
        # forge:outside not in eligible, so no agents should be removed
        assert "forge:a" in result

    def test_remaining_count_in_reasoning(self):
        conflicts = {
            "/src/x.py": [_make_conflict("forge:a", "/src/x.py")]
        }
        engine = self._make_engine_with_plr(conflicts)
        eligible = {
            "forge:a": _make_capability("forge:a", [WorkCellLane.api_simple]),
            "forge:b": _make_capability("forge:b", [WorkCellLane.api_simple]),
        }
        reasoning: list[str] = []
        engine._filter_by_path_locks(eligible, ["/src/x.py"], reasoning)
        assert any("1 eligible agent" in r.lower() for r in reasoning)


# ---------------------------------------------------------------------------
# Tests — score formula correctness (whitebox)
# ---------------------------------------------------------------------------


class TestScoreFormula:
    """Verify the composite score matches the documented formula exactly."""

    def test_balanced_score_formula_all_factors_known(self):
        """
        With balanced strategy and deterministic inputs:
        f_capability = 1.0 (always, because lane filter ensures support)
        f_historical = 0.5 (neutral prior, no history)
        f_load       = 1 - 2/4 = 0.5
        f_budget     = 75 / 100 = 0.75

        balanced weights: (0.30, 0.25, 0.25, 0.20)
        expected = 0.30*1.0 + 0.25*0.5 + 0.25*0.5 + 0.20*0.75
                 = 0.30 + 0.125 + 0.125 + 0.15
                 = 0.70
        """
        agents = {
            "forge:nova": _make_capability(
                "forge:nova",
                [WorkCellLane.api_simple],
                current_active=2,
                max_concurrent=4,
                context_budget_remaining_pct=75.0,
            )
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend(
            "t", TaskType.bug_fix, RiskTier.low, strategy=RecommendationStrategy.balanced
        )
        assert rec.recommended_agents[0].score == pytest.approx(0.70, abs=1e-5)

    def test_capability_match_strategy_formula(self):
        """
        capability_match weights: (0.60, 0.125, 0.125, 0.15)
        f_capability = 1.0, f_historical = 0.5 (neutral), f_load = 0.5, f_budget = 0.75

        expected = 0.60*1.0 + 0.125*0.5 + 0.125*0.5 + 0.15*0.75
                 = 0.60 + 0.0625 + 0.0625 + 0.1125
                 = 0.8375
        """
        agents = {
            "forge:nova": _make_capability(
                "forge:nova",
                [WorkCellLane.api_simple],
                current_active=2,
                max_concurrent=4,
                context_budget_remaining_pct=75.0,
            )
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend(
            "t", TaskType.bug_fix, RiskTier.low, strategy=RecommendationStrategy.capability_match
        )
        assert rec.recommended_agents[0].score == pytest.approx(0.8375, abs=1e-5)

    def test_historical_performance_strategy_formula(self):
        """
        historical_performance weights: (0.15, 0.50, 0.20, 0.15)
        f_capability = 1.0, f_historical = 0.5 (neutral), f_load = 0.5, f_budget = 0.75

        expected = 0.15*1.0 + 0.50*0.5 + 0.20*0.5 + 0.15*0.75
                 = 0.15 + 0.25 + 0.10 + 0.1125
                 = 0.6125
        """
        agents = {
            "forge:nova": _make_capability(
                "forge:nova",
                [WorkCellLane.api_simple],
                current_active=2,
                max_concurrent=4,
                context_budget_remaining_pct=75.0,
            )
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend(
            "t",
            TaskType.bug_fix,
            RiskTier.low,
            strategy=RecommendationStrategy.historical_performance,
        )
        assert rec.recommended_agents[0].score == pytest.approx(0.6125, abs=1e-5)

    def test_overloaded_agent_clamps_load_factor_to_zero(self):
        """current_active >= max_concurrent → f_load = max(1 - 1.0, 0) = 0.0."""
        agents = {
            "forge:nova": _make_capability(
                "forge:nova",
                [WorkCellLane.api_simple],
                current_active=10,
                max_concurrent=4,
                context_budget_remaining_pct=100.0,
            )
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["current_load"] == 0.0

    def test_full_context_budget_factor_is_one(self):
        agents = {
            "forge:nova": _make_capability(
                "forge:nova",
                [WorkCellLane.api_simple],
                context_budget_remaining_pct=100.0,
            )
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["context_budget"] == pytest.approx(1.0)

    def test_zero_context_budget_factor_is_zero(self):
        agents = {
            "forge:nova": _make_capability(
                "forge:nova",
                [WorkCellLane.api_simple],
                context_budget_remaining_pct=0.0,
            )
        }
        engine = _make_engine(agents=agents)
        rec = engine.recommend("t", TaskType.bug_fix, RiskTier.low)
        assert rec.recommended_agents[0].factors["context_budget"] == pytest.approx(0.0)
