"""Tests for forge recommend CLI commands.

Covers forge_harness/cli_v2/recommend.py:
    - forge recommend task <task_type> <risk_tier>
    - forge recommend explain <task_id>
    - forge recommend log
    - forge recommend contention

Strategy:
    - CliRunner.invoke via Click testing harness for all paths.
    - RecommendationEngine, LoadBalancer, LaneEnforcer, SLOMonitor singletons
      are reset before every test for isolation; data is injected directly
      into the singletons to avoid real subprocesses.
    - --json output is validated against the FORGE envelope schema.

Coverage target: 90%+
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

from forge_harness.webhook_server.models.agent_load import AgentCapability
from forge_harness.webhook_server.models.lane_slo import SLOCheckResult, SLOStatus
from forge_harness.webhook_server.models.recommendation import (
    AgentScore,
    RecommendationStrategy,
    TaskRecommendation,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.lane_enforcer import (
    get_lane_enforcer,
    reset_lane_enforcer,
)
from forge_harness.webhook_server.services.load_balancer import (
    get_load_balancer,
    reset_load_balancer,
)
from forge_harness.webhook_server.services.recommendation_engine import (
    get_recommendation_engine,
    reset_recommendation_engine,
)
from forge_harness.webhook_server.services.slo_monitor import (
    get_slo_monitor,
    reset_slo_monitor,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_all_singletons():
    """Reset all relevant singletons before each test."""
    reset_recommendation_engine()
    reset_load_balancer()
    reset_lane_enforcer()
    reset_slo_monitor()
    yield
    reset_recommendation_engine()
    reset_load_balancer()
    reset_lane_enforcer()
    reset_slo_monitor()


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Helpers: inject data into singletons
# ---------------------------------------------------------------------------


def _make_agent_score(
    agent_id: str = "forge:nova",
    score: float = 0.85,
    capability_match: float = 1.0,
    historical_pass_rate_factor: float = 0.75,
    current_load: float = 0.8,
    context_budget: float = 0.9,
    historical_pass_rate: float | None = 0.75,
    avg_lead_time_seconds: float | None = 30.0,
) -> AgentScore:
    return AgentScore(
        agent_id=agent_id,
        score=score,
        factors={
            "capability_match": capability_match,
            "historical_pass_rate": historical_pass_rate_factor,
            "current_load": current_load,
            "context_budget": context_budget,
        },
        historical_pass_rate=historical_pass_rate,
        avg_lead_time_seconds=avg_lead_time_seconds,
    )


def _make_task_recommendation(
    task_id: str = "task-001",
    agents: list[AgentScore] | None = None,
    confidence: float = 0.25,
    reasoning: list[str] | None = None,
) -> TaskRecommendation:
    if agents is None:
        agents = [_make_agent_score()]
    if reasoning is None:
        reasoning = [
            "Lane resolved from task_type=bug-fix risk_tier=low → api_simple",
            "Eligible agents: 1/1",
            "Top agent: forge:nova score=0.8500",
            "Confidence: 0.2500",
        ]
    return TaskRecommendation(
        task_id=task_id,
        recommended_agents=agents,
        confidence=confidence,
        reasoning=reasoning,
    )


def _inject_recommendation(rec: TaskRecommendation) -> None:
    """Push *rec* directly into the engine's in-memory log."""
    engine = get_recommendation_engine()
    engine._log.append(rec)


def _register_agent(
    agent_id: str = "forge:nova",
    lanes: list[WorkCellLane] | None = None,
    current_active: int = 0,
    max_concurrent: int = 3,
    budget_pct: float = 90.0,
) -> None:
    """Register an agent with the load balancer."""
    if lanes is None:
        lanes = [WorkCellLane.api_simple, WorkCellLane.docs]
    lb = get_load_balancer()
    lb.register_agent(agent_id, lanes, max_concurrent)
    lb.update_agent_load(agent_id, current_active, budget_pct)


# ---------------------------------------------------------------------------
# Invoke helper
# ---------------------------------------------------------------------------


def _invoke(runner: CliRunner, args: list[str]):
    """Invoke the forge CLI with isolated filesystem."""
    with runner.isolated_filesystem():
        return runner.invoke(cli, args)


# ===========================================================================
# Registration smoke tests
# ===========================================================================


class TestRecommendRegistration:
    """Verify the recommend group is properly wired into the CLI."""

    def test_recommend_in_root_help(self, runner):
        result = _invoke(runner, ["--help"])
        assert result.exit_code == 0
        assert "recommend" in result.output

    def test_recommend_group_help(self, runner):
        result = _invoke(runner, ["recommend", "--help"])
        assert result.exit_code == 0
        assert "task" in result.output
        assert "explain" in result.output
        assert "log" in result.output
        assert "contention" in result.output

    def test_recommend_task_help(self, runner):
        result = _invoke(runner, ["recommend", "task", "--help"])
        assert result.exit_code == 0
        assert "task_type" in result.output.lower() or "TASK_TYPE" in result.output

    def test_recommend_explain_help(self, runner):
        result = _invoke(runner, ["recommend", "explain", "--help"])
        assert result.exit_code == 0

    def test_recommend_log_help(self, runner):
        result = _invoke(runner, ["recommend", "log", "--help"])
        assert result.exit_code == 0
        assert "--limit" in result.output

    def test_recommend_contention_help(self, runner):
        result = _invoke(runner, ["recommend", "contention", "--help"])
        assert result.exit_code == 0


# ===========================================================================
# forge recommend task
# ===========================================================================


class TestRecommendTask:
    """Tests for 'forge recommend task <task_type> <risk_tier>'."""

    def _mock_engine(self, rec: TaskRecommendation):
        """Return a context manager that patches the engine singleton."""
        mock_engine = MagicMock()
        mock_engine.recommend.return_value = rec
        return patch(
            "forge_harness.cli_v2.recommend._get_engine",
            return_value=mock_engine,
        )

    def test_task_no_agents_human_readable(self, runner):
        """Empty recommendation prints a 'no eligible agents' message."""
        rec = TaskRecommendation(
            task_id="hyp-001",
            recommended_agents=[],
            confidence=0.0,
            reasoning=["No agents registered."],
        )
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        assert "No eligible agents" in result.output

    def test_task_no_agents_json(self, runner):
        """Empty recommendation in JSON mode returns success envelope with empty agents."""
        rec = TaskRecommendation(
            task_id="hyp-001",
            recommended_agents=[],
            confidence=0.0,
            reasoning=["No agents registered."],
        )
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["data"]["recommended_agents"] == []
        assert payload["data"]["confidence"] == 0.0

    def test_task_single_agent_human_readable(self, runner):
        rec = _make_task_recommendation(
            task_id="hyp-single",
            agents=[_make_agent_score("forge:nova", score=0.85)],
            confidence=0.85,
        )
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        assert "forge:nova" in result.output
        assert "0.8500" in result.output

    def test_task_single_agent_json(self, runner):
        rec = _make_task_recommendation(
            task_id="hyp-single",
            agents=[_make_agent_score("forge:nova", score=0.85)],
            confidence=0.85,
        )
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        data = payload["data"]
        assert len(data["recommended_agents"]) == 1
        assert data["recommended_agents"][0]["agent_id"] == "forge:nova"
        assert data["recommended_agents"][0]["score"] == 0.85
        assert data["confidence"] == 0.85
        assert data["task_type"] == "bug-fix"
        assert data["risk_tier"] == "low"

    def test_task_multiple_agents_json(self, runner):
        agents = [
            _make_agent_score("forge:nova", score=0.85),
            _make_agent_score("forge:prya", score=0.72),
            _make_agent_score("forge:dev", score=0.55),
        ]
        rec = _make_task_recommendation(agents=agents, confidence=0.13)
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        data = payload["data"]
        assert len(data["recommended_agents"]) == 3
        assert data["recommended_agents"][0]["agent_id"] == "forge:nova"
        assert data["recommended_agents"][1]["agent_id"] == "forge:prya"
        assert data["confidence"] == 0.13

    def test_task_multiple_agents_human_readable(self, runner):
        agents = [
            _make_agent_score("forge:nova", score=0.85),
            _make_agent_score("forge:prya", score=0.72),
        ]
        rec = _make_task_recommendation(agents=agents, confidence=0.13)
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        assert "forge:nova" in result.output
        assert "forge:prya" in result.output

    def test_task_strategy_balanced(self, runner):
        rec = _make_task_recommendation()
        mock_engine = MagicMock()
        mock_engine.recommend.return_value = rec
        with patch("forge_harness.cli_v2.recommend._get_engine", return_value=mock_engine):
            result = _invoke(
                runner,
                ["recommend", "task", "bug-fix", "low", "--strategy", "balanced"],
            )
        assert result.exit_code == 0
        mock_engine.recommend.assert_called_once()
        call_kwargs = mock_engine.recommend.call_args
        assert call_kwargs.kwargs["strategy"] == RecommendationStrategy.balanced

    def test_task_strategy_capability_match(self, runner):
        rec = _make_task_recommendation()
        mock_engine = MagicMock()
        mock_engine.recommend.return_value = rec
        with patch("forge_harness.cli_v2.recommend._get_engine", return_value=mock_engine):
            _invoke(
                runner,
                ["recommend", "task", "bug-fix", "low", "--strategy", "capability_match"],
            )
        call_kwargs = mock_engine.recommend.call_args
        assert call_kwargs.kwargs["strategy"] == RecommendationStrategy.capability_match

    def test_task_strategy_historical_performance(self, runner):
        rec = _make_task_recommendation()
        mock_engine = MagicMock()
        mock_engine.recommend.return_value = rec
        with patch("forge_harness.cli_v2.recommend._get_engine", return_value=mock_engine):
            _invoke(
                runner,
                [
                    "recommend",
                    "task",
                    "bug-fix",
                    "low",
                    "--strategy",
                    "historical_performance",
                ],
            )
        call_kwargs = mock_engine.recommend.call_args
        assert call_kwargs.kwargs["strategy"] == RecommendationStrategy.historical_performance

    def test_task_invalid_task_type_exits_9(self, runner):
        result = _invoke(runner, ["recommend", "task", "invalid-type", "low"])
        assert result.exit_code == 9

    def test_task_invalid_task_type_json(self, runner):
        result = _invoke(runner, ["recommend", "--json", "task", "invalid-type", "low"])
        assert result.exit_code == 9
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_task_invalid_risk_tier_exits_9(self, runner):
        result = _invoke(runner, ["recommend", "task", "bug-fix", "extreme"])
        assert result.exit_code == 9

    def test_task_invalid_risk_tier_json(self, runner):
        result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", "extreme"])
        assert result.exit_code == 9
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_task_invalid_strategy_exits_9(self, runner):
        result = _invoke(
            runner, ["recommend", "task", "bug-fix", "low", "--strategy", "unknown_algo"]
        )
        assert result.exit_code == 9

    def test_task_invalid_strategy_json(self, runner):
        result = _invoke(
            runner,
            ["recommend", "--json", "task", "bug-fix", "low", "--strategy", "unknown_algo"],
        )
        assert result.exit_code == 9
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_task_shows_reasoning(self, runner):
        rec = _make_task_recommendation(reasoning=["Lane resolved: api_simple", "Confidence: 0.25"])
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        assert "Lane resolved" in result.output
        assert "Confidence" in result.output

    def test_task_json_includes_reasoning(self, runner):
        rec = _make_task_recommendation(reasoning=["step 1", "step 2"])
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", "low"])
        payload = json.loads(result.output)
        assert "reasoning" in payload["data"]
        assert "step 1" in payload["data"]["reasoning"]

    def test_task_json_includes_factor_breakdown(self, runner):
        rec = _make_task_recommendation(
            agents=[
                _make_agent_score(
                    "forge:nova",
                    score=0.80,
                    capability_match=1.0,
                    historical_pass_rate_factor=0.75,
                    current_load=0.66,
                    context_budget=0.90,
                )
            ]
        )
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", "low"])
        payload = json.loads(result.output)
        agent = payload["data"]["recommended_agents"][0]
        assert "factors" in agent
        assert "capability_match" in agent["factors"]
        assert "historical_pass_rate" in agent["factors"]
        assert "current_load" in agent["factors"]
        assert "context_budget" in agent["factors"]

    def test_task_json_includes_strategy(self, runner):
        rec = _make_task_recommendation()
        mock_engine = MagicMock()
        mock_engine.recommend.return_value = rec
        with patch("forge_harness.cli_v2.recommend._get_engine", return_value=mock_engine):
            result = _invoke(
                runner,
                ["recommend", "--json", "task", "bug-fix", "low", "--strategy", "balanced"],
            )
        payload = json.loads(result.output)
        assert payload["data"]["strategy"] == "balanced"

    def test_task_high_confidence_label(self, runner):
        rec = _make_task_recommendation(confidence=0.75)
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        assert "high" in result.output

    def test_task_medium_confidence_label(self, runner):
        rec = _make_task_recommendation(confidence=0.30)
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        assert "medium" in result.output

    def test_task_low_confidence_label(self, runner):
        rec = _make_task_recommendation(confidence=0.05)
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "task", "bug-fix", "low"])
        assert result.exit_code == 0
        assert "low" in result.output

    def test_task_json_confidence_label(self, runner):
        rec = _make_task_recommendation(confidence=0.75)
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", "low"])
        payload = json.loads(result.output)
        assert payload["data"]["confidence_label"] == "high"

    def test_task_all_valid_task_types(self, runner):
        """All canonical TaskType values should be accepted without error."""
        valid_types = [
            "test-writing",
            "docs-update",
            "content-generation",
            "code-refactor",
            "api-endpoint",
            "security-change",
            "deployment",
            "database-migration",
            "dependency-update",
            "config-change",
            "new-feature",
            "bug-fix",
        ]
        rec = _make_task_recommendation(agents=[], confidence=0.0)
        mock_engine = MagicMock()
        mock_engine.recommend.return_value = rec
        with patch("forge_harness.cli_v2.recommend._get_engine", return_value=mock_engine):
            for tt in valid_types:
                result = _invoke(runner, ["recommend", "--json", "task", tt, "low"])
                payload = json.loads(result.output)
                assert payload["success"] is True, f"task_type={tt!r} failed"

    def test_task_all_valid_risk_tiers(self, runner):
        """All canonical RiskTier values should be accepted without error."""
        rec = _make_task_recommendation(agents=[], confidence=0.0)
        mock_engine = MagicMock()
        mock_engine.recommend.return_value = rec
        with patch("forge_harness.cli_v2.recommend._get_engine", return_value=mock_engine):
            for tier in ["low", "medium", "high", "critical"]:
                result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", tier])
                payload = json.loads(result.output)
                assert payload["success"] is True, f"risk_tier={tier!r} failed"

    def test_task_engine_error_exits_1(self, runner):
        mock_engine = MagicMock()
        mock_engine.recommend.side_effect = RuntimeError("engine down")
        with patch("forge_harness.cli_v2.recommend._get_engine", return_value=mock_engine):
            result = _invoke(runner, ["recommend", "task", "bug-fix", "low"])
        assert result.exit_code == 1

    def test_task_engine_error_json_well_formed(self, runner):
        mock_engine = MagicMock()
        mock_engine.recommend.side_effect = RuntimeError("boom")
        with patch("forge_harness.cli_v2.recommend._get_engine", return_value=mock_engine):
            result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", "low"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_task_json_envelope_has_timestamp(self, runner):
        rec = _make_task_recommendation()
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "--json", "task", "bug-fix", "low"])
        payload = json.loads(result.output)
        assert "timestamp" in payload
        assert "command" in payload
        assert payload["command"] == "forge recommend task"

    def test_task_json_flag_on_subcommand(self, runner):
        """--json on the subcommand also works (backward compat pattern)."""
        rec = _make_task_recommendation()
        with self._mock_engine(rec):
            result = _invoke(runner, ["recommend", "task", "bug-fix", "low", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True


# ===========================================================================
# forge recommend explain
# ===========================================================================


class TestRecommendExplain:
    """Tests for 'forge recommend explain <task_id>'."""

    def test_explain_not_found_exits_6(self, runner):
        result = _invoke(runner, ["recommend", "explain", "nonexistent-task"])
        assert result.exit_code == 6

    def test_explain_not_found_json(self, runner):
        result = _invoke(runner, ["recommend", "--json", "explain", "nonexistent-task"])
        assert result.exit_code == 6
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_explain_found_human_readable(self, runner):
        rec = _make_task_recommendation(
            task_id="task-xyz",
            agents=[_make_agent_score("forge:nova", score=0.85)],
            confidence=0.85,
        )
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "explain", "task-xyz"])
        assert result.exit_code == 0
        assert "task-xyz" in result.output
        assert "forge:nova" in result.output
        assert "0.8500" in result.output

    def test_explain_found_json(self, runner):
        rec = _make_task_recommendation(
            task_id="task-json",
            agents=[_make_agent_score("forge:nova", score=0.85)],
            confidence=0.85,
        )
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "--json", "explain", "task-json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        data = payload["data"]
        assert data["task_id"] == "task-json"
        assert data["confidence"] == 0.85
        assert len(data["recommended_agents"]) == 1
        assert data["recommended_agents"][0]["agent_id"] == "forge:nova"

    def test_explain_shows_factor_breakdown(self, runner):
        agent = _make_agent_score(
            "forge:nova",
            score=0.85,
            capability_match=1.0,
            historical_pass_rate_factor=0.75,
        )
        rec = _make_task_recommendation(task_id="task-factors", agents=[agent])
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "explain", "task-factors"])
        assert result.exit_code == 0
        assert "forge:nova" in result.output

    def test_explain_shows_historical_data(self, runner):
        agent = _make_agent_score(
            "forge:nova",
            historical_pass_rate=0.82,
            avg_lead_time_seconds=25.5,
        )
        rec = _make_task_recommendation(task_id="task-hist", agents=[agent])
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "explain", "task-hist"])
        assert result.exit_code == 0

    def test_explain_no_history_data(self, runner):
        """Agents with no history show 'no data' for pass_rate and lead_time."""
        agent = _make_agent_score(
            "forge:nova",
            historical_pass_rate=None,
            avg_lead_time_seconds=None,
        )
        rec = _make_task_recommendation(task_id="task-nodata", agents=[agent])
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "explain", "task-nodata"])
        assert result.exit_code == 0
        assert "no data" in result.output

    def test_explain_multiple_agents(self, runner):
        agents = [
            _make_agent_score("forge:nova", score=0.85),
            _make_agent_score("forge:prya", score=0.72),
        ]
        rec = _make_task_recommendation(task_id="task-multi", agents=agents, confidence=0.13)
        _inject_recommendation(rec)
        # Check via JSON to avoid Rich table truncation in narrow test TTY
        result = _invoke(runner, ["recommend", "--json", "explain", "task-multi"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        agent_ids = [a["agent_id"] for a in payload["data"]["recommended_agents"]]
        assert "forge:nova" in agent_ids
        assert "forge:prya" in agent_ids

    def test_explain_zero_agents(self, runner):
        """A task with no eligible agents renders a no-agents message."""
        rec = _make_task_recommendation(
            task_id="task-empty",
            agents=[],
            confidence=0.0,
            reasoning=["No agents eligible."],
        )
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "explain", "task-empty"])
        assert result.exit_code == 0
        assert "No agents" in result.output

    def test_explain_zero_agents_json(self, runner):
        rec = _make_task_recommendation(
            task_id="task-empty-json",
            agents=[],
            confidence=0.0,
        )
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "--json", "explain", "task-empty-json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["data"]["recommended_agents"] == []

    def test_explain_returns_most_recent(self, runner):
        """When multiple entries exist for the same task_id, return the newest."""
        rec_old = _make_task_recommendation(
            task_id="task-dup",
            agents=[_make_agent_score("forge:old", score=0.50)],
            confidence=0.50,
        )
        rec_new = _make_task_recommendation(
            task_id="task-dup",
            agents=[_make_agent_score("forge:new", score=0.90)],
            confidence=0.90,
        )
        _inject_recommendation(rec_old)
        _inject_recommendation(rec_new)

        result = _invoke(runner, ["recommend", "--json", "explain", "task-dup"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        # Most recent has forge:new as top agent
        assert payload["data"]["recommended_agents"][0]["agent_id"] == "forge:new"

    def test_explain_shows_reasoning(self, runner):
        rec = _make_task_recommendation(
            task_id="task-reason",
            reasoning=["Lane resolved: api_simple", "Top agent: forge:nova"],
        )
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "explain", "task-reason"])
        assert result.exit_code == 0
        assert "Lane resolved" in result.output

    def test_explain_json_includes_reasoning(self, runner):
        rec = _make_task_recommendation(
            task_id="task-reason-json",
            reasoning=["step A", "step B"],
        )
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "--json", "explain", "task-reason-json"])
        payload = json.loads(result.output)
        assert "reasoning" in payload["data"]
        assert "step A" in payload["data"]["reasoning"]

    def test_explain_json_includes_timestamp(self, runner):
        rec = _make_task_recommendation(task_id="task-ts")
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "--json", "explain", "task-ts"])
        payload = json.loads(result.output)
        assert "created_at" in payload["data"]

    def test_explain_confidence_label_in_json(self, runner):
        rec = _make_task_recommendation(task_id="task-conf", confidence=0.60)
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "--json", "explain", "task-conf"])
        payload = json.loads(result.output)
        assert payload["data"]["confidence_label"] == "high"

    def test_explain_error_json_well_formed(self, runner):
        with patch(
            "forge_harness.cli_v2.recommend._get_engine",
            side_effect=RuntimeError("crash"),
        ):
            result = _invoke(runner, ["recommend", "--json", "explain", "task-boom"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_explain_json_flag_on_subcommand(self, runner):
        rec = _make_task_recommendation(task_id="task-sub-flag")
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "explain", "task-sub-flag", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True


# ===========================================================================
# forge recommend log
# ===========================================================================


class TestRecommendLog:
    """Tests for 'forge recommend log'."""

    def test_log_empty_human_readable(self, runner):
        result = _invoke(runner, ["recommend", "log"])
        assert result.exit_code == 0
        assert "No recommendation history" in result.output

    def test_log_empty_json(self, runner):
        result = _invoke(runner, ["recommend", "--json", "log"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["data"]["count"] == 0
        assert payload["data"]["entries"] == []

    def test_log_with_one_entry(self, runner):
        rec = _make_task_recommendation(task_id="task-001")
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "log"])
        assert result.exit_code == 0
        assert "task-001" in result.output

    def test_log_with_multiple_entries(self, runner):
        for i in range(5):
            _inject_recommendation(_make_task_recommendation(task_id=f"task-{i:03d}"))
        result = _invoke(runner, ["recommend", "log"])
        assert result.exit_code == 0
        # At least the most recent task should appear
        assert "task-004" in result.output

    def test_log_json_with_multiple_entries(self, runner):
        for i in range(3):
            _inject_recommendation(_make_task_recommendation(task_id=f"task-{i}"))
        result = _invoke(runner, ["recommend", "--json", "log"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["data"]["count"] == 3
        assert len(payload["data"]["entries"]) == 3

    def test_log_limit_default_20(self, runner):
        """Default limit is 20; with 25 entries only 20 are returned."""
        for i in range(25):
            _inject_recommendation(_make_task_recommendation(task_id=f"task-{i:03d}"))
        result = _invoke(runner, ["recommend", "--json", "log"])
        payload = json.loads(result.output)
        assert payload["data"]["count"] == 20

    def test_log_limit_option(self, runner):
        """--limit 5 caps the result at 5 entries."""
        for i in range(10):
            _inject_recommendation(_make_task_recommendation(task_id=f"task-{i:02d}"))
        result = _invoke(runner, ["recommend", "--json", "log", "--limit", "5"])
        payload = json.loads(result.output)
        assert payload["data"]["count"] == 5

    def test_log_limit_1(self, runner):
        """--limit 1 returns exactly one entry."""
        for i in range(5):
            _inject_recommendation(_make_task_recommendation(task_id=f"task-{i}"))
        result = _invoke(runner, ["recommend", "--json", "log", "--limit", "1"])
        payload = json.loads(result.output)
        assert payload["data"]["count"] == 1

    def test_log_json_entry_structure(self, runner):
        """Each log entry in JSON mode must have expected keys."""
        rec = _make_task_recommendation(task_id="task-struct")
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "--json", "log"])
        payload = json.loads(result.output)
        entry = payload["data"]["entries"][0]
        assert "task_id" in entry
        assert "confidence" in entry
        assert "confidence_label" in entry
        assert "recommended_agents" in entry
        assert "created_at" in entry
        assert "reasoning" in entry

    def test_log_json_most_recent_first(self, runner):
        """Log is returned most-recent-first (newest task_id appears first)."""
        for i in range(3):
            _inject_recommendation(_make_task_recommendation(task_id=f"task-{i}"))
        result = _invoke(runner, ["recommend", "--json", "log"])
        payload = json.loads(result.output)
        first_task = payload["data"]["entries"][0]["task_id"]
        # The most recently injected is task-2
        assert first_task == "task-2"

    def test_log_shows_top_agent(self, runner):
        rec = _make_task_recommendation(
            task_id="task-top",
            agents=[_make_agent_score("forge:nova", score=0.85)],
        )
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "log"])
        assert result.exit_code == 0
        assert "forge:nova" in result.output

    def test_log_no_agents_in_entry(self, runner):
        """Entries with no agents do not crash the log command."""
        rec = _make_task_recommendation(task_id="task-noagent", agents=[], confidence=0.0)
        _inject_recommendation(rec)
        result = _invoke(runner, ["recommend", "log"])
        assert result.exit_code == 0
        assert "task-noagent" in result.output

    def test_log_json_flag_on_subcommand(self, runner):
        result = _invoke(runner, ["recommend", "log", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True

    def test_log_error_json_well_formed(self, runner):
        with patch(
            "forge_harness.cli_v2.recommend._get_engine",
            side_effect=RuntimeError("boom"),
        ):
            result = _invoke(runner, ["recommend", "--json", "log"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_log_error_exits_1(self, runner):
        with patch(
            "forge_harness.cli_v2.recommend._get_engine",
            side_effect=RuntimeError("boom"),
        ):
            result = _invoke(runner, ["recommend", "log"])
        assert result.exit_code == 1

    def test_log_json_command_field(self, runner):
        result = _invoke(runner, ["recommend", "--json", "log"])
        payload = json.loads(result.output)
        assert payload["command"] == "forge recommend log"


# ===========================================================================
# forge recommend contention
# ===========================================================================


class TestRecommendContention:
    """Tests for 'forge recommend contention'."""

    def _mock_contention(
        self,
        lane_stats: dict | None = None,
        agent_caps: dict | None = None,
        breaches: list | None = None,
    ):
        """Return patch context managers for the three service singletons."""
        mock_enforcer = MagicMock()
        mock_enforcer.get_lane_stats.return_value = lane_stats or {}

        mock_lb = MagicMock()
        mock_lb.get_agent_stats.return_value = agent_caps or {}

        mock_slo = MagicMock()
        mock_slo.get_breaches.return_value = breaches or []

        return (
            patch("forge_harness.cli_v2.recommend._get_lane_enforcer", return_value=mock_enforcer),
            patch("forge_harness.cli_v2.recommend._get_load_balancer", return_value=mock_lb),
            patch("forge_harness.cli_v2.recommend._get_slo_monitor", return_value=mock_slo),
        )

    def test_contention_empty_system_human_readable(self, runner):
        enforcer_p, lb_p, slo_p = self._mock_contention()
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "contention"])
        assert result.exit_code == 0
        assert "No overloaded agents" in result.output
        assert "No active SLO breaches" in result.output

    def test_contention_empty_system_json(self, runner):
        enforcer_p, lb_p, slo_p = self._mock_contention()
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "--json", "contention"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        data = payload["data"]
        assert data["overloaded_agents"] == []
        assert data["slo_breaches"] == []

    def test_contention_with_lane_stats(self, runner):
        lane_stats = {
            "api_simple": {"active": 3, "queued": 0, "completed": 10, "max_wip": 10},
            "security_change": {"active": 2, "queued": 1, "completed": 4, "max_wip": 2},
        }
        enforcer_p, lb_p, slo_p = self._mock_contention(lane_stats=lane_stats)
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "contention"])
        assert result.exit_code == 0
        assert "api_simple" in result.output
        assert "security_change" in result.output

    def test_contention_with_lane_stats_json(self, runner):
        lane_stats = {
            "api_simple": {"active": 2, "queued": 0, "completed": 8, "max_wip": 10},
        }
        enforcer_p, lb_p, slo_p = self._mock_contention(lane_stats=lane_stats)
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "--json", "contention"])
        payload = json.loads(result.output)
        assert "lane_wip" in payload["data"]
        assert "api_simple" in payload["data"]["lane_wip"]

    def test_contention_overloaded_agent(self, runner):
        """An agent at max_concurrent is reported as overloaded."""
        cap = AgentCapability(
            agent_id="forge:nova",
            supported_lanes=[WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=3,
            context_budget_remaining_pct=85.0,
        )
        enforcer_p, lb_p, slo_p = self._mock_contention(agent_caps={"forge:nova": cap})
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "contention"])
        assert result.exit_code == 0
        assert "forge:nova" in result.output

    def test_contention_overloaded_agent_json(self, runner):
        cap = AgentCapability(
            agent_id="forge:nova",
            supported_lanes=[WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=3,
            context_budget_remaining_pct=85.0,
        )
        enforcer_p, lb_p, slo_p = self._mock_contention(agent_caps={"forge:nova": cap})
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "--json", "contention"])
        payload = json.loads(result.output)
        data = payload["data"]
        assert data["overloaded_count"] == 1
        assert data["overloaded_agents"][0]["agent_id"] == "forge:nova"

    def test_contention_non_overloaded_agent_not_listed(self, runner):
        cap = AgentCapability(
            agent_id="forge:nova",
            supported_lanes=[WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=1,
            context_budget_remaining_pct=90.0,
        )
        enforcer_p, lb_p, slo_p = self._mock_contention(agent_caps={"forge:nova": cap})
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "--json", "contention"])
        payload = json.loads(result.output)
        assert payload["data"]["overloaded_count"] == 0

    def test_contention_slo_breach(self, runner):
        breach = SLOCheckResult(
            lane=WorkCellLane.api_simple,
            status=SLOStatus.breached,
            current_lead_time_seconds=600.0,
            current_pass_rate=0.60,
            requeue_count=5,
        )
        enforcer_p, lb_p, slo_p = self._mock_contention(breaches=[breach])
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "contention"])
        assert result.exit_code == 0
        assert "breached" in result.output
        assert "api_simple" in result.output

    def test_contention_slo_breach_json(self, runner):
        breach = SLOCheckResult(
            lane=WorkCellLane.api_simple,
            status=SLOStatus.breached,
            current_lead_time_seconds=400.0,
            current_pass_rate=0.70,
            requeue_count=3,
        )
        enforcer_p, lb_p, slo_p = self._mock_contention(breaches=[breach])
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "--json", "contention"])
        payload = json.loads(result.output)
        data = payload["data"]
        assert data["breach_count"] == 1
        assert data["slo_breaches"][0]["lane"] == "api_simple"
        assert data["slo_breaches"][0]["status"] == "breached"

    def test_contention_no_slo_breaches_message(self, runner):
        enforcer_p, lb_p, slo_p = self._mock_contention(breaches=[])
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "contention"])
        assert result.exit_code == 0
        assert "No active SLO breaches" in result.output

    def test_contention_multiple_breaches(self, runner):
        breaches = [
            SLOCheckResult(
                lane=WorkCellLane.api_simple,
                status=SLOStatus.breached,
                current_lead_time_seconds=600.0,
                current_pass_rate=0.60,
                requeue_count=5,
            ),
            SLOCheckResult(
                lane=WorkCellLane.docs,
                status=SLOStatus.breached,
                current_lead_time_seconds=800.0,
                current_pass_rate=0.50,
                requeue_count=10,
            ),
        ]
        enforcer_p, lb_p, slo_p = self._mock_contention(breaches=breaches)
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "--json", "contention"])
        payload = json.loads(result.output)
        assert payload["data"]["breach_count"] == 2

    def test_contention_multiple_agents(self, runner):
        caps = {
            f"forge:agent{i}": AgentCapability(
                agent_id=f"forge:agent{i}",
                supported_lanes=[WorkCellLane.api_simple],
                max_concurrent=3,
                current_active=3,
                context_budget_remaining_pct=80.0,
            )
            for i in range(3)
        }
        enforcer_p, lb_p, slo_p = self._mock_contention(agent_caps=caps)
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "--json", "contention"])
        payload = json.loads(result.output)
        assert payload["data"]["overloaded_count"] == 3
        assert payload["data"]["total_agents"] == 3

    def test_contention_low_budget_agent_flagged(self, runner):
        """Agent with very low context budget is shown in overloaded list."""
        cap = AgentCapability(
            agent_id="forge:exhausted",
            supported_lanes=[WorkCellLane.api_simple],
            max_concurrent=3,
            current_active=3,
            context_budget_remaining_pct=5.0,
        )
        enforcer_p, lb_p, slo_p = self._mock_contention(agent_caps={"forge:exhausted": cap})
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "--json", "contention"])
        payload = json.loads(result.output)
        overloaded = payload["data"]["overloaded_agents"]
        assert len(overloaded) == 1
        assert overloaded[0]["context_budget_pct"] == 5.0

    def test_contention_json_flag_on_subcommand(self, runner):
        enforcer_p, lb_p, slo_p = self._mock_contention()
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "contention", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True

    def test_contention_error_exits_1(self, runner):
        with patch(
            "forge_harness.cli_v2.recommend._get_lane_enforcer",
            side_effect=RuntimeError("db error"),
        ):
            result = _invoke(runner, ["recommend", "contention"])
        assert result.exit_code == 1

    def test_contention_error_json_well_formed(self, runner):
        with patch(
            "forge_harness.cli_v2.recommend._get_lane_enforcer",
            side_effect=RuntimeError("db error"),
        ):
            result = _invoke(runner, ["recommend", "--json", "contention"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_contention_json_command_field(self, runner):
        enforcer_p, lb_p, slo_p = self._mock_contention()
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "--json", "contention"])
        payload = json.loads(result.output)
        assert payload["command"] == "forge recommend contention"

    def test_contention_slo_null_lead_time(self, runner):
        """Breach with None lead_time should render as N/A."""
        breach = SLOCheckResult(
            lane=WorkCellLane.api_simple,
            status=SLOStatus.breached,
            current_lead_time_seconds=None,
            current_pass_rate=None,
            requeue_count=2,
        )
        enforcer_p, lb_p, slo_p = self._mock_contention(breaches=[breach])
        with enforcer_p, lb_p, slo_p:
            result = _invoke(runner, ["recommend", "contention"])
        assert result.exit_code == 0
        assert "N/A" in result.output


# ===========================================================================
# Helper function unit tests
# ===========================================================================


class TestHelperFunctions:
    """Unit tests for internal helper functions."""

    def test_confidence_label_high(self):
        from forge_harness.cli_v2.recommend import _confidence_label

        assert _confidence_label(0.5) == "high"
        assert _confidence_label(0.75) == "high"
        assert _confidence_label(1.0) == "high"

    def test_confidence_label_medium(self):
        from forge_harness.cli_v2.recommend import _confidence_label

        assert _confidence_label(0.2) == "medium"
        assert _confidence_label(0.35) == "medium"
        assert _confidence_label(0.49) == "medium"

    def test_confidence_label_low(self):
        from forge_harness.cli_v2.recommend import _confidence_label

        assert _confidence_label(0.0) == "low"
        assert _confidence_label(0.1) == "low"
        assert _confidence_label(0.19) == "low"

    def test_parse_task_type_valid(self):
        from forge_harness.cli_v2.recommend import _parse_task_type

        result = _parse_task_type("bug-fix")
        assert result is not None
        assert result.value == "bug-fix"

    def test_parse_task_type_invalid(self):
        from forge_harness.cli_v2.recommend import _parse_task_type

        assert _parse_task_type("not-a-type") is None
        assert _parse_task_type("") is None

    def test_parse_risk_tier_valid(self):
        from forge_harness.cli_v2.recommend import _parse_risk_tier

        result = _parse_risk_tier("high")
        assert result is not None
        assert result.value == "high"

    def test_parse_risk_tier_invalid(self):
        from forge_harness.cli_v2.recommend import _parse_risk_tier

        assert _parse_risk_tier("extreme") is None

    def test_parse_strategy_valid(self):
        from forge_harness.cli_v2.recommend import _parse_strategy

        result = _parse_strategy("balanced")
        assert result is not None
        assert result.value == "balanced"

    def test_parse_strategy_invalid(self):
        from forge_harness.cli_v2.recommend import _parse_strategy

        assert _parse_strategy("unknown") is None

    def test_agent_score_to_dict(self):
        from forge_harness.cli_v2.recommend import _agent_score_to_dict

        agent = _make_agent_score("forge:nova", score=0.85)
        d = _agent_score_to_dict(agent)
        assert d["agent_id"] == "forge:nova"
        assert d["score"] == 0.85
        assert "factors" in d
        assert "historical_pass_rate" in d
        assert "avg_lead_time_seconds" in d

    def test_recommendation_to_dict(self):
        from forge_harness.cli_v2.recommend import _recommendation_to_dict

        rec = _make_task_recommendation(task_id="t-dict", confidence=0.55)
        d = _recommendation_to_dict(rec)
        assert d["task_id"] == "t-dict"
        assert d["confidence"] == 0.55
        assert d["confidence_label"] == "high"
        assert "created_at" in d
        assert "recommended_agents" in d
        assert "reasoning" in d
