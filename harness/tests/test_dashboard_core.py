"""
Comprehensive tests for forge_harness/dashboard_core.py

Covers:
- AGENT_STATUSES vocabulary constant
- format_age() utility function (all time-range branches)
- format_duration() utility function (all time-range branches)
- AgentState dataclass: defaults, full construction, to_dict()
- PipelineStatus dataclass
- ApprovalSummary dataclass
- RalphStatus dataclass
- MVPStatusSummary dataclass
- compute_fleet_health() - all health outcomes
- agent_state_to_dict() - backward-compat wrapper
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from forge_harness.dashboard_core import (
    AGENT_STATUSES,
    AgentState,
    ApprovalSummary,
    MVPStatusSummary,
    PipelineStatus,
    RalphStatus,
    agent_state_to_dict,
    compute_fleet_health,
    format_age,
    format_duration,
)

# ---------------------------------------------------------------------------
# AGENT_STATUSES vocabulary
# ---------------------------------------------------------------------------


class TestAgentStatusesVocabulary:
    """Tests for the canonical AGENT_STATUSES tuple."""

    def test_is_tuple(self):
        assert isinstance(AGENT_STATUSES, tuple)

    def test_contains_active(self):
        assert "active" in AGENT_STATUSES

    def test_contains_idle(self):
        assert "idle" in AGENT_STATUSES

    def test_contains_stale(self):
        assert "stale" in AGENT_STATUSES

    def test_contains_error(self):
        assert "error" in AGENT_STATUSES

    def test_contains_unknown(self):
        assert "unknown" in AGENT_STATUSES

    def test_length(self):
        assert len(AGENT_STATUSES) == 5

    def test_all_strings(self):
        for status in AGENT_STATUSES:
            assert isinstance(status, str)

    def test_immutable(self):
        # Tuples are immutable - verifying the contract isn't accidentally changed
        with pytest.raises(TypeError):
            AGENT_STATUSES[0] = "new_status"  # type: ignore[index]


# ---------------------------------------------------------------------------
# format_age()
# ---------------------------------------------------------------------------


class TestFormatAge:
    """Tests for format_age() — all branches of the time-range logic."""

    def _dt_ago(self, **kwargs) -> datetime:
        return datetime.now(UTC) - timedelta(**kwargs)

    # --- seconds branch ---

    def test_seconds_ago_zero(self):
        # Exact 'now' should report 0s ago (or very close)
        dt = datetime.now(UTC)
        result = format_age(dt)
        assert result.endswith("s ago")

    def test_seconds_ago_30(self):
        dt = self._dt_ago(seconds=30)
        result = format_age(dt)
        assert result.endswith("s ago")
        secs = int(result.replace("s ago", ""))
        assert 28 <= secs <= 35  # allow a small execution window

    def test_seconds_ago_59(self):
        dt = self._dt_ago(seconds=59)
        result = format_age(dt)
        assert result.endswith("s ago")

    # --- minutes branch ---

    def test_minutes_ago_1(self):
        dt = self._dt_ago(minutes=1)
        result = format_age(dt)
        assert result == "1m ago"

    def test_minutes_ago_5(self):
        dt = self._dt_ago(minutes=5)
        result = format_age(dt)
        assert result == "5m ago"

    def test_minutes_ago_59(self):
        dt = self._dt_ago(minutes=59)
        result = format_age(dt)
        assert result == "59m ago"

    # --- hours branch ---

    def test_hours_ago_1(self):
        dt = self._dt_ago(hours=1)
        result = format_age(dt)
        assert result == "1h ago"

    def test_hours_ago_2(self):
        dt = self._dt_ago(hours=2)
        result = format_age(dt)
        assert result == "2h ago"

    def test_hours_ago_23(self):
        dt = self._dt_ago(hours=23)
        result = format_age(dt)
        assert result == "23h ago"

    # --- days branch ---

    def test_days_ago_1(self):
        dt = self._dt_ago(days=1)
        result = format_age(dt)
        assert result == "1d ago"

    def test_days_ago_7(self):
        dt = self._dt_ago(days=7)
        result = format_age(dt)
        assert result == "7d ago"

    def test_days_ago_30(self):
        dt = self._dt_ago(days=30)
        result = format_age(dt)
        assert result == "30d ago"

    # --- timezone-naive input ---

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetimes must be handled without raising."""
        dt_naive = datetime.utcnow() - timedelta(minutes=3)
        result = format_age(dt_naive)
        # Should be in the "minutes" range
        assert result.endswith("m ago")

    def test_naive_datetime_seconds(self):
        dt_naive = datetime.utcnow() - timedelta(seconds=10)
        result = format_age(dt_naive)
        assert result.endswith("s ago")

    def test_naive_datetime_hours(self):
        dt_naive = datetime.utcnow() - timedelta(hours=5)
        result = format_age(dt_naive)
        assert result == "5h ago"

    # --- non-UTC aware timezone ---

    def test_non_utc_aware_timezone(self):
        """Aware datetimes in non-UTC zones should be handled correctly."""
        eastern = timezone(timedelta(hours=-5))
        dt = datetime.now(eastern) - timedelta(minutes=10)
        result = format_age(dt)
        assert result == "10m ago"


# ---------------------------------------------------------------------------
# format_duration()
# ---------------------------------------------------------------------------


class TestFormatDuration:
    """Tests for format_duration() — all branches."""

    # --- sub-minute ---

    def test_zero_seconds(self):
        assert format_duration(0.0) == "0.0s"

    def test_fractional_seconds(self):
        result = format_duration(1.5)
        assert result == "1.5s"

    def test_exactly_59_seconds(self):
        result = format_duration(59.0)
        assert result == "59.0s"

    def test_sub_minute_decimal(self):
        result = format_duration(30.7)
        assert result == "30.7s"

    # --- minutes branch (< 1 hour) ---

    def test_exactly_60_seconds(self):
        result = format_duration(60.0)
        assert result == "1m 0s"

    def test_90_seconds(self):
        result = format_duration(90.0)
        assert result == "1m 30s"

    def test_5_minutes(self):
        result = format_duration(300.0)
        assert result == "5m 0s"

    def test_59_minutes_59_seconds(self):
        result = format_duration(59 * 60 + 59.0)
        assert result == "59m 59s"

    # --- hours branch ---

    def test_exactly_1_hour(self):
        result = format_duration(3600.0)
        assert result == "1h 0m"

    def test_1_hour_30_minutes(self):
        result = format_duration(3600.0 + 1800.0)
        assert result == "1h 30m"

    def test_2_hours(self):
        result = format_duration(7200.0)
        assert result == "2h 0m"

    def test_hours_with_minutes(self):
        result = format_duration(3 * 3600 + 45 * 60.0)
        assert result == "3h 45m"

    def test_large_duration(self):
        # 25 hours = 25h 0m
        result = format_duration(25 * 3600.0)
        assert result == "25h 0m"

    # --- integer seconds ---

    def test_integer_input_sub_minute(self):
        result = format_duration(45)
        assert result == "45.0s"

    def test_integer_input_minutes(self):
        result = format_duration(120)
        assert result == "2m 0s"


# ---------------------------------------------------------------------------
# AgentState
# ---------------------------------------------------------------------------


class TestAgentStateDefaults:
    """Test AgentState default field values."""

    def test_minimal_construction(self):
        agent = AgentState(id="agent-1")
        assert agent.id == "agent-1"

    def test_default_name_is_none(self):
        agent = AgentState(id="a")
        assert agent.name is None

    def test_default_source_is_unknown(self):
        agent = AgentState(id="a")
        assert agent.source == "unknown"

    def test_default_status_is_unknown(self):
        agent = AgentState(id="a")
        assert agent.status == "unknown"

    def test_default_last_activity_is_none(self):
        agent = AgentState(id="a")
        assert agent.last_activity is None

    def test_default_is_stale_false(self):
        agent = AgentState(id="a")
        assert agent.is_stale is False

    def test_default_role_is_none(self):
        agent = AgentState(id="a")
        assert agent.role is None

    def test_default_project_is_none(self):
        agent = AgentState(id="a")
        assert agent.project is None

    def test_default_task_is_none(self):
        agent = AgentState(id="a")
        assert agent.task is None

    def test_default_progress_is_zero(self):
        agent = AgentState(id="a")
        assert agent.progress == 0

    def test_default_context_estimate_is_zero(self):
        agent = AgentState(id="a")
        assert agent.context_estimate == 0

    def test_default_session_id_is_none(self):
        agent = AgentState(id="a")
        assert agent.session_id is None

    def test_default_window_name_is_none(self):
        agent = AgentState(id="a")
        assert agent.window_name is None

    def test_default_agent_type_is_none(self):
        agent = AgentState(id="a")
        assert agent.agent_type is None

    def test_default_domain_is_none(self):
        agent = AgentState(id="a")
        assert agent.domain is None

    def test_default_token_usage_empty_dict(self):
        agent = AgentState(id="a")
        assert agent.token_usage == {}

    def test_default_messages_count_zero(self):
        agent = AgentState(id="a")
        assert agent.messages_count == 0

    def test_default_focus_tags_empty_list(self):
        agent = AgentState(id="a")
        assert agent.focus_tags == []

    def test_token_usage_not_shared_between_instances(self):
        """Mutable default should not be shared across instances."""
        a1 = AgentState(id="a1")
        a2 = AgentState(id="a2")
        a1.token_usage["input"] = 100
        assert a2.token_usage == {}

    def test_focus_tags_not_shared_between_instances(self):
        a1 = AgentState(id="a1")
        a2 = AgentState(id="a2")
        a1.focus_tags.append("tag")
        assert a2.focus_tags == []


class TestAgentStateFullConstruction:
    """Test AgentState with all fields explicitly set."""

    def _make_full(self) -> AgentState:
        return AgentState(
            id="agent-42",
            name="kimi",
            source="api",
            status="active",
            last_activity=datetime(2026, 2, 21, 12, 0, 0, tzinfo=UTC),
            is_stale=False,
            role="backend-engineer",
            project="interview-simulator",
            task="Add auth tests",
            progress=75,
            context_estimate=40,
            session_id="sess-abc",
            window_name="kimi:0",
            agent_type="claude",
            domain="codeswiftr-com",
            token_usage={"input": 1000, "output": 500},
            messages_count=12,
            focus_tags=["auth", "tests"],
        )

    def test_id(self):
        assert self._make_full().id == "agent-42"

    def test_name(self):
        assert self._make_full().name == "kimi"

    def test_source(self):
        assert self._make_full().source == "api"

    def test_status(self):
        assert self._make_full().status == "active"

    def test_last_activity(self):
        assert self._make_full().last_activity == datetime(2026, 2, 21, 12, 0, 0, tzinfo=UTC)

    def test_is_stale(self):
        assert self._make_full().is_stale is False

    def test_role(self):
        assert self._make_full().role == "backend-engineer"

    def test_project(self):
        assert self._make_full().project == "interview-simulator"

    def test_task(self):
        assert self._make_full().task == "Add auth tests"

    def test_progress(self):
        assert self._make_full().progress == 75

    def test_context_estimate(self):
        assert self._make_full().context_estimate == 40

    def test_session_id(self):
        assert self._make_full().session_id == "sess-abc"

    def test_window_name(self):
        assert self._make_full().window_name == "kimi:0"

    def test_agent_type(self):
        assert self._make_full().agent_type == "claude"

    def test_domain(self):
        assert self._make_full().domain == "codeswiftr-com"

    def test_token_usage(self):
        assert self._make_full().token_usage == {"input": 1000, "output": 500}

    def test_messages_count(self):
        assert self._make_full().messages_count == 12

    def test_focus_tags(self):
        assert self._make_full().focus_tags == ["auth", "tests"]


class TestAgentStateToDict:
    """Test AgentState.to_dict() serialization."""

    def test_returns_dict(self):
        agent = AgentState(id="x")
        assert isinstance(agent.to_dict(), dict)

    def test_id_in_dict(self):
        agent = AgentState(id="my-id")
        assert agent.to_dict()["id"] == "my-id"

    def test_name_none_when_not_set(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["name"] is None

    def test_name_present_when_set(self):
        agent = AgentState(id="x", name="kimi")
        assert agent.to_dict()["name"] == "kimi"

    def test_status_default_unknown(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["status"] == "unknown"

    def test_last_activity_none(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["last_activity"] is None

    def test_last_activity_iso_format(self):
        dt = datetime(2026, 2, 21, 10, 30, 0, tzinfo=UTC)
        agent = AgentState(id="x", last_activity=dt)
        result = agent.to_dict()["last_activity"]
        # Should be ISO 8601 string ending with "Z"
        assert isinstance(result, str)
        assert result.endswith("Z")
        assert "2026-02-21" in result
        assert "10:30:00" in result

    def test_last_activity_iso_z_suffix(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        agent = AgentState(id="x", last_activity=dt)
        assert agent.to_dict()["last_activity"].endswith("Z")

    def test_is_stale_false_by_default(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["is_stale"] is False

    def test_is_stale_true(self):
        agent = AgentState(id="x", is_stale=True)
        assert agent.to_dict()["is_stale"] is True

    def test_progress_default_zero(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["progress"] == 0

    def test_progress_custom_value(self):
        agent = AgentState(id="x", progress=50)
        assert agent.to_dict()["progress"] == 50

    def test_context_estimate_default_zero(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["context_estimate"] == 0

    def test_source_default_unknown(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["source"] == "unknown"

    def test_source_custom(self):
        agent = AgentState(id="x", source="tmux")
        assert agent.to_dict()["source"] == "tmux"

    def test_session_id_none(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["session_id"] is None

    def test_session_id_present(self):
        agent = AgentState(id="x", session_id="s123")
        assert agent.to_dict()["session_id"] == "s123"

    def test_window_name_none(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["window_name"] is None

    def test_agent_type_none(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["agent_type"] is None

    def test_domain_none(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["domain"] is None

    def test_domain_present(self):
        agent = AgentState(id="x", domain="voice-coach")
        assert agent.to_dict()["domain"] == "voice-coach"

    def test_token_usage_empty_dict(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["token_usage"] == {}

    def test_token_usage_populated(self):
        agent = AgentState(id="x", token_usage={"input": 200, "output": 100})
        assert agent.to_dict()["token_usage"] == {"input": 200, "output": 100}

    def test_messages_count_zero(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["messages_count"] == 0

    def test_messages_count_nonzero(self):
        agent = AgentState(id="x", messages_count=7)
        assert agent.to_dict()["messages_count"] == 7

    def test_focus_tags_empty(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["focus_tags"] == []

    def test_focus_tags_populated(self):
        agent = AgentState(id="x", focus_tags=["auth", "deploy"])
        assert agent.to_dict()["focus_tags"] == ["auth", "deploy"]

    def test_all_expected_keys_present(self):
        agent = AgentState(id="x")
        d = agent.to_dict()
        expected_keys = {
            "id", "name", "role", "project", "task", "status",
            "progress", "last_activity", "is_stale", "context_estimate",
            "source", "session_id", "window_name", "agent_type",
            "domain", "token_usage", "messages_count", "focus_tags",
        }
        assert expected_keys == set(d.keys())

    def test_role_none_default(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["role"] is None

    def test_role_present(self):
        agent = AgentState(id="x", role="backend-engineer")
        assert agent.to_dict()["role"] == "backend-engineer"

    def test_project_none_default(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["project"] is None

    def test_task_none_default(self):
        agent = AgentState(id="x")
        assert agent.to_dict()["task"] is None


# ---------------------------------------------------------------------------
# PipelineStatus
# ---------------------------------------------------------------------------


class TestPipelineStatus:
    """Test PipelineStatus dataclass construction and field access."""

    def _make(self, **overrides) -> PipelineStatus:
        base = dict(
            name="content_to_publish",
            current_step="generate",
            step_index=2,
            total_steps=5,
            status="running",
            started_at=datetime(2026, 2, 21, 9, 0, 0, tzinfo=UTC),
        )
        base.update(overrides)
        return PipelineStatus(**base)

    def test_name(self):
        assert self._make().name == "content_to_publish"

    def test_current_step(self):
        assert self._make().current_step == "generate"

    def test_step_index(self):
        assert self._make().step_index == 2

    def test_total_steps(self):
        assert self._make().total_steps == 5

    def test_status(self):
        assert self._make().status == "running"

    def test_started_at(self):
        ps = self._make()
        assert ps.started_at == datetime(2026, 2, 21, 9, 0, 0, tzinfo=UTC)

    def test_error_default_none(self):
        ps = self._make()
        assert ps.error is None

    def test_error_set(self):
        ps = self._make(error="Timeout on step 2")
        assert ps.error == "Timeout on step 2"

    def test_step_index_zero(self):
        ps = self._make(step_index=0)
        assert ps.step_index == 0

    def test_total_steps_one(self):
        ps = self._make(total_steps=1)
        assert ps.total_steps == 1

    def test_different_statuses(self):
        for status in ("running", "paused", "failed", "completed"):
            ps = self._make(status=status)
            assert ps.status == status


# ---------------------------------------------------------------------------
# ApprovalSummary
# ---------------------------------------------------------------------------


class TestApprovalSummary:
    """Test ApprovalSummary dataclass construction and field access."""

    def _make(self, **overrides) -> ApprovalSummary:
        base = dict(
            id="req-001",
            type="deployment",
            title="Deploy voice-coach v1.2",
            domain="brandfocus-ai",
            priority="high",
            tier="desktop",
            risk_score=0.7,
            created_at=datetime(2026, 2, 21, 8, 0, 0, tzinfo=UTC),
        )
        base.update(overrides)
        return ApprovalSummary(**base)

    def test_id(self):
        assert self._make().id == "req-001"

    def test_type(self):
        assert self._make().type == "deployment"

    def test_title(self):
        assert self._make().title == "Deploy voice-coach v1.2"

    def test_domain(self):
        assert self._make().domain == "brandfocus-ai"

    def test_priority(self):
        assert self._make().priority == "high"

    def test_tier(self):
        assert self._make().tier == "desktop"

    def test_risk_score(self):
        assert self._make().risk_score == pytest.approx(0.7)

    def test_created_at(self):
        a = self._make()
        assert a.created_at == datetime(2026, 2, 21, 8, 0, 0, tzinfo=UTC)

    def test_risk_score_zero(self):
        a = self._make(risk_score=0.0)
        assert a.risk_score == pytest.approx(0.0)

    def test_risk_score_one(self):
        a = self._make(risk_score=1.0)
        assert a.risk_score == pytest.approx(1.0)

    def test_tier_watch(self):
        a = self._make(tier="watch")
        assert a.tier == "watch"

    def test_tier_phone(self):
        a = self._make(tier="phone")
        assert a.tier == "phone"

    def test_priority_critical(self):
        a = self._make(priority="critical")
        assert a.priority == "critical"

    def test_priority_low(self):
        a = self._make(priority="low")
        assert a.priority == "low"


# ---------------------------------------------------------------------------
# RalphStatus
# ---------------------------------------------------------------------------


class TestRalphStatus:
    """Test RalphStatus dataclass construction and field access."""

    def _make(self, **overrides) -> RalphStatus:
        base = dict(
            active=True,
            current_feature="IS-042 Auth",
            iteration=3,
            max_iterations=10,
            failure_count=1,
            state="running",
            started_at=datetime(2026, 2, 21, 7, 0, 0, tzinfo=UTC),
        )
        base.update(overrides)
        return RalphStatus(**base)

    def test_active_true(self):
        assert self._make().active is True

    def test_active_false(self):
        assert self._make(active=False).active is False

    def test_current_feature(self):
        assert self._make().current_feature == "IS-042 Auth"

    def test_current_feature_none(self):
        r = self._make(current_feature=None)
        assert r.current_feature is None

    def test_iteration(self):
        assert self._make().iteration == 3

    def test_max_iterations(self):
        assert self._make().max_iterations == 10

    def test_failure_count(self):
        assert self._make().failure_count == 1

    def test_failure_count_zero(self):
        r = self._make(failure_count=0)
        assert r.failure_count == 0

    def test_state(self):
        assert self._make().state == "running"

    def test_started_at(self):
        r = self._make()
        assert r.started_at == datetime(2026, 2, 21, 7, 0, 0, tzinfo=UTC)

    def test_started_at_none(self):
        r = self._make(started_at=None)
        assert r.started_at is None

    def test_iteration_zero(self):
        r = self._make(iteration=0)
        assert r.iteration == 0

    def test_different_states(self):
        for state in ("running", "idle", "failed", "completed"):
            r = self._make(state=state)
            assert r.state == state


# ---------------------------------------------------------------------------
# MVPStatusSummary
# ---------------------------------------------------------------------------


class TestMVPStatusSummary:
    """Test MVPStatusSummary dataclass construction and field access."""

    def _make(self, **overrides) -> MVPStatusSummary:
        base = dict(
            domain="codeswiftr-com",
            project="interview-simulator",
            status="green",
            timestamp=datetime(2026, 2, 21, 6, 0, 0, tzinfo=UTC),
            missing_env_vars=0,
        )
        base.update(overrides)
        return MVPStatusSummary(**base)

    def test_domain(self):
        assert self._make().domain == "codeswiftr-com"

    def test_project(self):
        assert self._make().project == "interview-simulator"

    def test_status_green(self):
        assert self._make().status == "green"

    def test_status_red(self):
        m = self._make(status="red")
        assert m.status == "red"

    def test_timestamp(self):
        m = self._make()
        assert m.timestamp == datetime(2026, 2, 21, 6, 0, 0, tzinfo=UTC)

    def test_missing_env_vars_zero(self):
        assert self._make().missing_env_vars == 0

    def test_missing_env_vars_nonzero(self):
        m = self._make(missing_env_vars=3)
        assert m.missing_env_vars == 3

    def test_status_yellow(self):
        m = self._make(status="yellow")
        assert m.status == "yellow"


# ---------------------------------------------------------------------------
# compute_fleet_health()
# ---------------------------------------------------------------------------


class TestComputeFleetHealth:
    """Test compute_fleet_health() — all health outcome branches."""

    def _make_agent(self, is_stale: bool = False) -> AgentState:
        return AgentState(id="a", is_stale=is_stale)

    # --- critical: empty list ---

    def test_empty_fleet_is_critical(self):
        assert compute_fleet_health([]) == "critical"

    # --- healthy: no stale agents ---

    def test_all_fresh_is_healthy(self):
        agents = [self._make_agent(is_stale=False) for _ in range(5)]
        assert compute_fleet_health(agents) == "healthy"

    def test_single_fresh_agent_healthy(self):
        assert compute_fleet_health([self._make_agent(is_stale=False)]) == "healthy"

    # --- degraded: < 50% stale ---

    def test_one_of_three_stale_is_degraded(self):
        # 1/3 ≈ 33% stale → degraded
        agents = [
            self._make_agent(is_stale=True),
            self._make_agent(is_stale=False),
            self._make_agent(is_stale=False),
        ]
        assert compute_fleet_health(agents) == "degraded"

    def test_less_than_half_stale_is_degraded(self):
        # 2/5 = 40% stale → degraded
        agents = [
            self._make_agent(is_stale=True),
            self._make_agent(is_stale=True),
            self._make_agent(is_stale=False),
            self._make_agent(is_stale=False),
            self._make_agent(is_stale=False),
        ]
        assert compute_fleet_health(agents) == "degraded"

    # --- critical: >= 50% stale ---

    def test_exactly_half_stale_is_critical(self):
        # 1/2 = 50% → NOT < 0.5 → critical
        agents = [
            self._make_agent(is_stale=True),
            self._make_agent(is_stale=False),
        ]
        assert compute_fleet_health(agents) == "critical"

    def test_majority_stale_is_critical(self):
        # 3/4 = 75% stale → critical
        agents = [
            self._make_agent(is_stale=True),
            self._make_agent(is_stale=True),
            self._make_agent(is_stale=True),
            self._make_agent(is_stale=False),
        ]
        assert compute_fleet_health(agents) == "critical"

    def test_all_stale_is_critical(self):
        agents = [self._make_agent(is_stale=True) for _ in range(4)]
        assert compute_fleet_health(agents) == "critical"

    def test_single_stale_agent_is_critical(self):
        # 1/1 = 100% stale → critical
        agents = [self._make_agent(is_stale=True)]
        assert compute_fleet_health(agents) == "critical"

    def test_return_type_is_str(self):
        result = compute_fleet_health([])
        assert isinstance(result, str)

    def test_valid_health_values(self):
        valid = {"healthy", "degraded", "critical"}
        for n_stale in range(5):
            agents = [self._make_agent(is_stale=(i < n_stale)) for i in range(5)]
            result = compute_fleet_health(agents)
            assert result in valid

    def test_large_fleet_mostly_healthy(self):
        # 1 stale out of 100 → degraded
        agents = [self._make_agent(is_stale=(i == 0)) for i in range(100)]
        assert compute_fleet_health(agents) == "degraded"


# ---------------------------------------------------------------------------
# agent_state_to_dict() — backward-compat wrapper
# ---------------------------------------------------------------------------


class TestAgentStateToDictFunction:
    """Test the module-level agent_state_to_dict() convenience function."""

    def test_returns_dict(self):
        agent = AgentState(id="x")
        result = agent_state_to_dict(agent)
        assert isinstance(result, dict)

    def test_matches_method_output(self):
        agent = AgentState(
            id="z",
            name="codex",
            status="active",
            progress=60,
        )
        assert agent_state_to_dict(agent) == agent.to_dict()

    def test_id_present(self):
        agent = AgentState(id="wrapped-agent")
        assert agent_state_to_dict(agent)["id"] == "wrapped-agent"

    def test_status_present(self):
        agent = AgentState(id="x", status="idle")
        assert agent_state_to_dict(agent)["status"] == "idle"

    def test_token_usage_empty_by_default(self):
        agent = AgentState(id="x")
        assert agent_state_to_dict(agent)["token_usage"] == {}

    def test_full_agent_serialized(self):
        dt = datetime(2026, 2, 21, 12, 0, 0, tzinfo=UTC)
        agent = AgentState(
            id="full-agent",
            name="kimi",
            source="api",
            status="active",
            last_activity=dt,
            is_stale=False,
            role="senior-engineer",
            project="vc",
            task="Add tests",
            progress=90,
            context_estimate=55,
            session_id="s-xyz",
            window_name="win:1",
            agent_type="claude",
            domain="brandfocus-ai",
            token_usage={"input": 5000},
            messages_count=3,
            focus_tags=["tests"],
        )
        d = agent_state_to_dict(agent)
        assert d["id"] == "full-agent"
        assert d["name"] == "kimi"
        assert d["source"] == "api"
        assert d["status"] == "active"
        assert d["last_activity"].endswith("Z")
        assert d["is_stale"] is False
        assert d["role"] == "senior-engineer"
        assert d["project"] == "vc"
        assert d["task"] == "Add tests"
        assert d["progress"] == 90
        assert d["context_estimate"] == 55
        assert d["session_id"] == "s-xyz"
        assert d["window_name"] == "win:1"
        assert d["agent_type"] == "claude"
        assert d["domain"] == "brandfocus-ai"
        assert d["token_usage"] == {"input": 5000}
        assert d["messages_count"] == 3
        assert d["focus_tags"] == ["tests"]
