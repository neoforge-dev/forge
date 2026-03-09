"""
Unit Tests for Token Budget Tracker
=====================================

Tests for forge_harness.token_budget module.
Covers AgentBudget, TokenBudgetTracker, and create_tracker_from_env.
Targets 80%+ line coverage.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from forge_harness.token_budget import (
    AgentBudget,
    TokenBudgetTracker,
    create_tracker_from_env,
)

# ---------------------------------------------------------------------------
# AgentBudget tests
# ---------------------------------------------------------------------------


class TestAgentBudget:
    """Tests for the AgentBudget dataclass."""

    def test_default_field_values(self):
        """Newly created budget has sensible defaults."""
        budget = AgentBudget(agent_id="test-agent", hourly_limit=100_000)

        assert budget.agent_id == "test-agent"
        assert budget.hourly_limit == 100_000
        assert budget.tokens_used == 0
        assert budget.output_count == 0
        assert budget.last_output is None
        assert isinstance(budget.session_start, datetime)

    def test_reset_clears_usage(self):
        """reset() zeroes tokens/output_count and refreshes session_start."""
        budget = AgentBudget(agent_id="kimi", hourly_limit=250_000)
        budget.tokens_used = 50_000
        budget.output_count = 3
        budget.last_output = datetime.now()
        old_start = budget.session_start

        budget.reset()

        assert budget.tokens_used == 0
        assert budget.output_count == 0
        assert budget.last_output is None
        # session_start should be refreshed (>= old_start)
        assert budget.session_start >= old_start

    def test_reset_updates_session_start_to_now(self):
        """reset() sets session_start to approximately now."""
        budget = AgentBudget(agent_id="kimi", hourly_limit=250_000)
        before = datetime.now()
        budget.reset()
        after = datetime.now()

        assert before <= budget.session_start <= after

    def test_should_auto_reset_returns_false_for_fresh_budget(self):
        """A freshly created budget should NOT trigger auto-reset."""
        budget = AgentBudget(agent_id="kimi", hourly_limit=250_000)
        assert budget.should_auto_reset() is False

    def test_should_auto_reset_returns_true_after_one_hour(self):
        """A budget whose session_start is >1 hour ago should trigger reset."""
        budget = AgentBudget(agent_id="kimi", hourly_limit=250_000)
        budget.session_start = datetime.now() - timedelta(hours=1, seconds=1)
        assert budget.should_auto_reset() is True

    def test_should_auto_reset_false_within_hour(self):
        """A budget started 30 minutes ago should NOT trigger auto-reset."""
        budget = AgentBudget(
            agent_id="kimi",
            hourly_limit=250_000,
            session_start=datetime.now() - timedelta(minutes=30),
        )
        assert budget.should_auto_reset() is False


# ---------------------------------------------------------------------------
# TokenBudgetTracker initialisation tests
# ---------------------------------------------------------------------------


class TestTokenBudgetTrackerInit:
    """Tests for TokenBudgetTracker.__init__."""

    def test_default_initialisation(self):
        """Tracker starts with no budgets and auto_reset enabled."""
        tracker = TokenBudgetTracker()

        assert tracker.budgets == {}
        assert tracker.auto_reset is True
        assert tracker.custom_limits == {}

    def test_custom_limits_stored(self):
        """Custom limits passed at construction are retained."""
        custom = {"my-agent": 999_999}
        tracker = TokenBudgetTracker(custom_limits=custom)

        assert tracker.custom_limits == custom

    def test_auto_reset_disabled(self):
        """auto_reset=False disables automatic hourly resets."""
        tracker = TokenBudgetTracker(auto_reset=False)
        assert tracker.auto_reset is False

    def test_threshold_constants(self):
        """Class-level threshold constants are correct."""
        assert TokenBudgetTracker.WARNING_THRESHOLD == 0.75
        assert TokenBudgetTracker.CRITICAL_THRESHOLD == 0.90

    def test_default_limits_dict(self):
        """DEFAULT_LIMITS contains the expected agents with correct values."""
        assert TokenBudgetTracker.DEFAULT_LIMITS["kimi"] == 250_000
        assert TokenBudgetTracker.DEFAULT_LIMITS["codex"] == 500_000
        assert TokenBudgetTracker.DEFAULT_LIMITS["gemini"] == 1_000_000
        assert TokenBudgetTracker.DEFAULT_LIMITS["claude"] == 500_000
        assert TokenBudgetTracker.DEFAULT_LIMITS["gpt4"] == 500_000


# ---------------------------------------------------------------------------
# _get_or_create_budget tests
# ---------------------------------------------------------------------------


class TestGetOrCreateBudget:
    """Tests for _get_or_create_budget (exercised via public API)."""

    def test_known_agent_uses_default_limit(self):
        """Calling get_status on a known agent creates budget with default limit."""
        tracker = TokenBudgetTracker()
        budget = tracker._get_or_create_budget("kimi")

        assert budget.hourly_limit == 250_000

    def test_known_agents_all_default_limits(self):
        """All known agents have their expected default limits."""
        tracker = TokenBudgetTracker()
        expected = {
            "kimi": 250_000,
            "codex": 500_000,
            "gemini": 1_000_000,
            "claude": 500_000,
            "gpt4": 500_000,
        }
        for agent_id, limit in expected.items():
            budget = tracker._get_or_create_budget(agent_id)
            assert budget.hourly_limit == limit, f"Mismatch for {agent_id}"

    def test_unknown_agent_conservative_limit(self):
        """Unknown agents get the conservative 250K fallback."""
        tracker = TokenBudgetTracker()
        budget = tracker._get_or_create_budget("some-unknown-bot")

        assert budget.hourly_limit == 250_000

    def test_custom_limit_overrides_default(self):
        """custom_limits override DEFAULT_LIMITS for known agents."""
        tracker = TokenBudgetTracker(custom_limits={"kimi": 100_000})
        budget = tracker._get_or_create_budget("kimi")

        assert budget.hourly_limit == 100_000

    def test_custom_limit_for_unknown_agent(self):
        """custom_limits work for agents not in DEFAULT_LIMITS."""
        tracker = TokenBudgetTracker(custom_limits={"new-bot": 750_000})
        budget = tracker._get_or_create_budget("new-bot")

        assert budget.hourly_limit == 750_000

    def test_budget_created_once_reused(self):
        """Subsequent calls for the same agent return the same object."""
        tracker = TokenBudgetTracker()
        b1 = tracker._get_or_create_budget("kimi")
        b1.tokens_used = 50_000
        b2 = tracker._get_or_create_budget("kimi")

        assert b1 is b2
        assert b2.tokens_used == 50_000

    def test_auto_reset_triggers_when_window_expired(self):
        """When auto_reset=True and window has passed, tokens are cleared."""
        tracker = TokenBudgetTracker(auto_reset=True)
        tracker.record_output("kimi", tokens_used=50_000)

        # Age the session so should_auto_reset() fires
        tracker.budgets["kimi"].session_start = datetime.now() - timedelta(hours=2)

        # Next access should auto-reset
        budget = tracker._get_or_create_budget("kimi")
        assert budget.tokens_used == 0

    def test_auto_reset_disabled_preserves_usage(self):
        """When auto_reset=False, expired window does NOT reset the budget."""
        tracker = TokenBudgetTracker(auto_reset=False)
        tracker.record_output("kimi", tokens_used=50_000)
        tracker.budgets["kimi"].session_start = datetime.now() - timedelta(hours=2)

        budget = tracker._get_or_create_budget("kimi")
        assert budget.tokens_used == 50_000


# ---------------------------------------------------------------------------
# record_output tests
# ---------------------------------------------------------------------------


class TestRecordOutput:
    """Tests for TokenBudgetTracker.record_output."""

    def test_first_output_accumulates_tokens(self):
        """First call records tokens correctly."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=40_000)

        assert result["tokens_used"] == 40_000
        assert result["output_count"] == 1

    def test_multiple_outputs_accumulate(self):
        """Successive calls accumulate token counts."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=10_000)
        result = tracker.record_output("kimi", tokens_used=20_000)

        assert result["tokens_used"] == 30_000
        assert result["output_count"] == 2

    def test_tokens_remaining_correct(self):
        """tokens_remaining == limit - cumulative usage."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=50_000)

        assert result["tokens_remaining"] == 200_000

    def test_hourly_limit_in_result(self):
        """hourly_limit is always present in the result."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=1_000)

        assert result["hourly_limit"] == 250_000

    def test_status_ok_below_warning(self):
        """Status is OK below the 75% warning threshold."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=185_000)  # 74%

        assert result["status"] == "OK"
        assert result["should_handoff"] is False

    def test_status_warning_at_75_percent(self):
        """Status is WARNING at exactly 75% (187_500 tokens)."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=187_500)

        assert result["status"] == "WARNING"
        assert result["should_handoff"] is True

    def test_status_warning_between_thresholds(self):
        """Status is WARNING between 75% and 90%."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=200_000)  # 80%

        assert result["status"] == "WARNING"
        assert result["should_handoff"] is True

    def test_status_critical_at_90_percent(self):
        """Status is CRITICAL at exactly 90% (225_000 tokens)."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=225_000)

        assert result["status"] == "CRITICAL"
        assert result["should_handoff"] is True

    def test_status_critical_above_threshold(self):
        """Status is CRITICAL when usage exceeds 90%."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=250_000)

        assert result["status"] == "CRITICAL"
        assert result["should_handoff"] is True

    def test_utilization_pct_formatted(self):
        """utilization_pct is a percentage-formatted string."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=125_000)  # 50%

        assert result["utilization_pct"] == "50%"

    def test_utilization_float(self):
        """utilization is the raw ratio as a float."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=125_000)

        assert result["utilization"] == pytest.approx(0.5)

    def test_session_duration_non_negative(self):
        """session_duration_minutes is >= 0."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=1_000)

        assert result["session_duration_minutes"] >= 0.0

    def test_recommendation_ok_contains_agent(self):
        """OK recommendation mentions the agent and 'normally'."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("codex", tokens_used=1_000)

        assert "codex" in result["recommendation"]
        assert "normally" in result["recommendation"].lower()

    def test_recommendation_warning_message(self):
        """WARNING recommendation contains 'PREPARE HANDOFF'."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=200_000)

        assert "PREPARE HANDOFF" in result["recommendation"]

    def test_recommendation_critical_message(self):
        """CRITICAL recommendation contains 'HANDOFF NOW'."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=230_000)

        assert "HANDOFF NOW" in result["recommendation"]

    def test_last_output_timestamp_set(self):
        """last_output is updated on every call."""
        tracker = TokenBudgetTracker()
        before = datetime.now()
        tracker.record_output("kimi", tokens_used=1_000)
        after = datetime.now()

        assert before <= tracker.budgets["kimi"].last_output <= after

    def test_result_has_all_expected_keys(self):
        """Result dict contains every documented key."""
        expected_keys = {
            "status",
            "utilization",
            "utilization_pct",
            "tokens_used",
            "tokens_remaining",
            "hourly_limit",
            "output_count",
            "should_handoff",
            "recommendation",
            "session_duration_minutes",
        }
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=10_000)

        assert set(result.keys()) == expected_keys

    def test_multiple_agents_independent(self):
        """Different agents maintain independent budgets."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=100_000)
        tracker.record_output("codex", tokens_used=200_000)

        assert tracker.get_status("kimi")["tokens_used"] == 100_000
        assert tracker.get_status("codex")["tokens_used"] == 200_000

    def test_zero_tokens_ok(self):
        """Calling with tokens_used=0 returns OK and zero utilization."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=0)

        assert result["status"] == "OK"
        assert result["utilization"] == 0.0

    def test_tokens_exceeding_limit_returns_critical(self):
        """Going over the hourly limit is CRITICAL with negative tokens_remaining."""
        tracker = TokenBudgetTracker()
        result = tracker.record_output("kimi", tokens_used=300_000)

        assert result["status"] == "CRITICAL"
        assert result["tokens_remaining"] < 0


# ---------------------------------------------------------------------------
# estimate_remaining_outputs tests
# ---------------------------------------------------------------------------


class TestEstimateRemainingOutputs:
    """Tests for TokenBudgetTracker.estimate_remaining_outputs."""

    def test_returns_integer(self):
        """Return value is always an int."""
        tracker = TokenBudgetTracker()
        result = tracker.estimate_remaining_outputs("kimi", avg_tokens=40_000)

        assert isinstance(result, int)

    def test_full_budget_available(self):
        """With no usage, result = floor(250000 / (40000 * 1.1)) = 5."""
        tracker = TokenBudgetTracker()
        result = tracker.estimate_remaining_outputs("kimi", avg_tokens=40_000)

        expected = int(250_000 / (40_000 * 1.1))
        assert result == expected

    def test_returns_zero_when_budget_exhausted(self):
        """Returns 0 after all tokens consumed."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=250_000)

        assert tracker.estimate_remaining_outputs("kimi", avg_tokens=40_000) == 0

    def test_returns_zero_when_budget_exceeded(self):
        """Returns 0 when usage exceeds limit."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=300_000)

        assert tracker.estimate_remaining_outputs("kimi", avg_tokens=40_000) == 0

    def test_partial_usage_reduces_estimate(self):
        """Partial usage reduces the estimate correctly."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=50_000)

        result = tracker.estimate_remaining_outputs("kimi", avg_tokens=40_000)
        expected = int(200_000 / (40_000 * 1.1))
        assert result == expected

    def test_safety_margin_is_applied(self):
        """The 1.1 safety margin makes the estimate conservative."""
        tracker = TokenBudgetTracker()
        # 250000 / (50000 * 1.1) = 4.545… -> int = 4
        result = tracker.estimate_remaining_outputs("kimi", avg_tokens=50_000)

        assert result == int(250_000 / (50_000 * 1.1))

    def test_small_avg_tokens_gives_large_estimate(self):
        """Very small avg_tokens yields a large estimate."""
        tracker = TokenBudgetTracker()
        result = tracker.estimate_remaining_outputs("kimi", avg_tokens=1_000)

        assert result > 100

    def test_creates_budget_if_missing(self):
        """estimate_remaining_outputs creates a budget entry if needed."""
        tracker = TokenBudgetTracker()
        assert "fresh-agent" not in tracker.budgets

        tracker.estimate_remaining_outputs("fresh-agent", avg_tokens=10_000)

        assert "fresh-agent" in tracker.budgets


# ---------------------------------------------------------------------------
# should_handoff tests
# ---------------------------------------------------------------------------


class TestShouldHandoff:
    """Tests for TokenBudgetTracker.should_handoff."""

    def test_returns_false_below_warning(self):
        """Returns False below the 75% warning threshold."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=100_000)  # 40%

        assert tracker.should_handoff("kimi") is False

    def test_returns_true_at_warning_threshold(self):
        """Returns True at exactly the 75% threshold."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=187_500)  # 75%

        assert tracker.should_handoff("kimi") is True

    def test_returns_true_above_warning_threshold(self):
        """Returns True above the warning threshold."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=230_000)  # 92%

        assert tracker.should_handoff("kimi") is True

    def test_creates_budget_for_fresh_agent(self):
        """should_handoff creates a budget for an unknown agent."""
        tracker = TokenBudgetTracker()
        result = tracker.should_handoff("never-seen-before")

        assert result is False
        assert "never-seen-before" in tracker.budgets

    def test_return_type_is_bool(self):
        """should_handoff always returns a bool (not a truthy value)."""
        tracker = TokenBudgetTracker()
        result = tracker.should_handoff("kimi")

        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# get_status tests
# ---------------------------------------------------------------------------


class TestGetStatus:
    """Tests for TokenBudgetTracker.get_status."""

    def test_fresh_agent_ok_status(self):
        """Fresh agent reports OK with zero usage."""
        tracker = TokenBudgetTracker()
        status = tracker.get_status("kimi")

        assert status["status"] == "OK"
        assert status["utilization"] == 0.0
        assert status["tokens_used"] == 0
        assert status["output_count"] == 0
        assert status["last_output"] is None

    def test_all_expected_keys_present(self):
        """get_status returns every documented field."""
        expected_keys = {
            "agent_id",
            "status",
            "utilization",
            "utilization_pct",
            "tokens_used",
            "tokens_remaining",
            "hourly_limit",
            "output_count",
            "recommendation",
            "session_start",
            "session_duration_minutes",
            "last_output",
        }
        tracker = TokenBudgetTracker()
        status = tracker.get_status("kimi")

        assert set(status.keys()) == expected_keys

    def test_agent_id_echoed_in_response(self):
        """agent_id in the response matches the requested agent."""
        tracker = TokenBudgetTracker()
        status = tracker.get_status("codex")

        assert status["agent_id"] == "codex"

    def test_session_start_iso_string(self):
        """session_start is a parseable ISO datetime string."""
        tracker = TokenBudgetTracker()
        status = tracker.get_status("kimi")

        parsed = datetime.fromisoformat(status["session_start"])
        assert isinstance(parsed, datetime)

    def test_last_output_none_before_any_outputs(self):
        """last_output is None when no outputs have been recorded."""
        tracker = TokenBudgetTracker()
        status = tracker.get_status("kimi")

        assert status["last_output"] is None

    def test_last_output_iso_string_after_recording(self):
        """last_output is an ISO string after an output has been recorded."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=10_000)
        status = tracker.get_status("kimi")

        assert status["last_output"] is not None
        parsed = datetime.fromisoformat(status["last_output"])
        assert isinstance(parsed, datetime)

    def test_recommendation_ok(self):
        """Healthy budget recommendation says 'Continue'."""
        tracker = TokenBudgetTracker()
        status = tracker.get_status("kimi")

        assert "Continue" in status["recommendation"]

    def test_recommendation_warning(self):
        """Warning-level recommendation says 'PREPARE HANDOFF'."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=200_000)
        status = tracker.get_status("kimi")

        assert "PREPARE HANDOFF" in status["recommendation"]

    def test_recommendation_critical(self):
        """Critical-level recommendation says 'HANDOFF NOW'."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=230_000)
        status = tracker.get_status("kimi")

        assert "HANDOFF NOW" in status["recommendation"]

    def test_status_warning_in_range(self):
        """get_status shows WARNING between 75% and 90%."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=200_000)

        assert tracker.get_status("kimi")["status"] == "WARNING"

    def test_status_critical_above_90(self):
        """get_status shows CRITICAL above 90%."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=230_000)

        assert tracker.get_status("kimi")["status"] == "CRITICAL"

    def test_session_duration_non_negative(self):
        """session_duration_minutes is always non-negative."""
        tracker = TokenBudgetTracker()
        status = tracker.get_status("kimi")

        assert status["session_duration_minutes"] >= 0.0


# ---------------------------------------------------------------------------
# get_all_statuses tests
# ---------------------------------------------------------------------------


class TestGetAllStatuses:
    """Tests for TokenBudgetTracker.get_all_statuses."""

    def test_empty_when_no_agents(self):
        """Returns {} when no agents have been tracked."""
        tracker = TokenBudgetTracker()
        assert tracker.get_all_statuses() == {}

    def test_returns_one_entry_per_agent(self):
        """Returns one entry for each tracked agent."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=10_000)
        tracker.record_output("codex", tokens_used=20_000)

        all_statuses = tracker.get_all_statuses()

        assert set(all_statuses.keys()) == {"kimi", "codex"}

    def test_each_value_is_full_status_dict(self):
        """Each value contains agent_id and the full status fields."""
        tracker = TokenBudgetTracker()
        tracker.record_output("gemini", tokens_used=5_000)

        all_statuses = tracker.get_all_statuses()

        assert "agent_id" in all_statuses["gemini"]
        assert all_statuses["gemini"]["agent_id"] == "gemini"


# ---------------------------------------------------------------------------
# reset_agent tests
# ---------------------------------------------------------------------------


class TestResetAgent:
    """Tests for TokenBudgetTracker.reset_agent."""

    def test_reset_clears_usage(self):
        """reset_agent zeroes the specified agent's usage."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=100_000)
        tracker.reset_agent("kimi")

        status = tracker.get_status("kimi")
        assert status["tokens_used"] == 0
        assert status["output_count"] == 0

    def test_reset_does_not_affect_others(self):
        """reset_agent only touches the specified agent."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=100_000)
        tracker.record_output("codex", tokens_used=200_000)

        tracker.reset_agent("kimi")

        assert tracker.get_status("codex")["tokens_used"] == 200_000

    def test_reset_unknown_agent_no_raise(self):
        """reset_agent on an unknown agent logs a warning but does not raise."""
        tracker = TokenBudgetTracker()
        tracker.reset_agent("ghost-agent")  # Should not raise

    def test_reset_unknown_agent_does_not_create_budget(self):
        """reset_agent on an unknown agent does not create a budget entry."""
        tracker = TokenBudgetTracker()
        tracker.reset_agent("ghost-agent")

        assert "ghost-agent" not in tracker.budgets


# ---------------------------------------------------------------------------
# reset_all tests
# ---------------------------------------------------------------------------


class TestResetAll:
    """Tests for TokenBudgetTracker.reset_all."""

    def test_clears_all_agents(self):
        """reset_all zeroes every tracked agent's usage."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=100_000)
        tracker.record_output("codex", tokens_used=200_000)
        tracker.record_output("gemini", tokens_used=300_000)

        tracker.reset_all()

        for agent_id in ("kimi", "codex", "gemini"):
            assert tracker.get_status(agent_id)["tokens_used"] == 0

    def test_empty_tracker_no_raise(self):
        """reset_all on an empty tracker does not raise."""
        tracker = TokenBudgetTracker()
        tracker.reset_all()

    def test_budget_entries_still_present_after_reset(self):
        """Budget entries are retained (just values zeroed)."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=100_000)
        tracker.reset_all()

        assert "kimi" in tracker.budgets


# ---------------------------------------------------------------------------
# create_tracker_from_env tests
# ---------------------------------------------------------------------------


class TestCreateTrackerFromEnv:
    """Tests for the create_tracker_from_env factory function."""

    def test_returns_token_budget_tracker(self):
        """Factory returns a TokenBudgetTracker instance."""
        tracker = create_tracker_from_env()
        assert isinstance(tracker, TokenBudgetTracker)

    def test_no_env_vars_empty_custom_limits(self):
        """With no TOKEN_LIMIT_* vars, custom_limits is empty."""
        # Ensure none of the known vars are set
        env_clean = {
            k: v for k, v in os.environ.items() if not k.startswith("TOKEN_LIMIT_")
        }
        with patch.dict("os.environ", env_clean, clear=True):
            tracker = create_tracker_from_env()

        assert tracker.custom_limits == {}

    def test_env_var_overrides_kimi_limit(self):
        """TOKEN_LIMIT_KIMI overrides the default kimi limit."""
        with patch.dict("os.environ", {"TOKEN_LIMIT_KIMI": "300000"}):
            tracker = create_tracker_from_env()

        assert tracker.custom_limits["kimi"] == 300_000
        assert tracker.get_status("kimi")["hourly_limit"] == 300_000

    def test_multiple_env_vars_all_applied(self):
        """Multiple TOKEN_LIMIT_* vars are all applied."""
        env = {
            "TOKEN_LIMIT_KIMI": "111111",
            "TOKEN_LIMIT_CODEX": "222222",
            "TOKEN_LIMIT_GEMINI": "333333",
        }
        with patch.dict("os.environ", env):
            tracker = create_tracker_from_env()

        assert tracker.custom_limits["kimi"] == 111_111
        assert tracker.custom_limits["codex"] == 222_222
        assert tracker.custom_limits["gemini"] == 333_333

    def test_invalid_env_var_skipped(self):
        """An unparseable TOKEN_LIMIT_* value is silently ignored."""
        with patch.dict("os.environ", {"TOKEN_LIMIT_KIMI": "not-a-number"}):
            tracker = create_tracker_from_env()

        assert "kimi" not in tracker.custom_limits
        # Default limit should still apply
        assert tracker.get_status("kimi")["hourly_limit"] == 250_000

    def test_claude_env_var_recognised(self):
        """TOKEN_LIMIT_CLAUDE is processed."""
        with patch.dict("os.environ", {"TOKEN_LIMIT_CLAUDE": "400000"}):
            tracker = create_tracker_from_env()

        assert tracker.custom_limits["claude"] == 400_000

    def test_gpt4_env_var_recognised(self):
        """TOKEN_LIMIT_GPT4 is processed."""
        with patch.dict("os.environ", {"TOKEN_LIMIT_GPT4": "600000"}):
            tracker = create_tracker_from_env()

        assert tracker.custom_limits["gpt4"] == 600_000


# ---------------------------------------------------------------------------
# Integration / simulation tests
# ---------------------------------------------------------------------------


class TestMarathonSimulation:
    """Integration tests simulating real marathon documentation sessions."""

    def test_kimi_marathon_status_progression(self):
        """Kimi marathon eventually hits CRITICAL status."""
        tracker = TokenBudgetTracker()
        outputs = [
            ("DATABASE_PATTERNS.md", 44_000),
            ("TESTING_PATTERNS.md", 35_000),
            ("LOGGING_PATTERNS.md", 42_000),
            ("API_VERSIONING.md", 38_000),
            ("FILE_UPLOAD.md", 45_000),
            ("CORS_PATTERNS.md", 40_000),  # total = 244_000 → 97.6%
        ]

        statuses = []
        cumulative = 0
        for _name, tokens in outputs:
            result = tracker.record_output("kimi", tokens_used=tokens)
            statuses.append(result["status"])
            cumulative += tokens

        assert statuses[-1] == "CRITICAL"
        assert tracker.get_status("kimi")["tokens_used"] == cumulative

    def test_estimate_decreases_with_usage(self):
        """Remaining output estimate decreases as tokens are consumed."""
        tracker = TokenBudgetTracker()
        avg = 40_000
        estimates = []

        for _ in range(4):
            estimates.append(tracker.estimate_remaining_outputs("kimi", avg_tokens=avg))
            tracker.record_output("kimi", tokens_used=avg)

        for i in range(1, len(estimates)):
            assert estimates[i] <= estimates[i - 1]

    def test_should_handoff_transitions_across_threshold(self):
        """should_handoff flips False → True as usage crosses 75%."""
        tracker = TokenBudgetTracker()

        # 74% — not yet at warning
        tracker.record_output("kimi", tokens_used=185_000)
        assert tracker.should_handoff("kimi") is False

        # Cross 75%: 185_000 + 3_000 = 188_000 / 250_000 = 75.2%
        tracker.record_output("kimi", tokens_used=3_000)
        assert tracker.should_handoff("kimi") is True

    def test_reset_restores_full_capacity(self):
        """After reset, estimate returns full-budget value."""
        tracker = TokenBudgetTracker()
        tracker.record_output("kimi", tokens_used=200_000)

        tracker.reset_agent("kimi")

        full = tracker.estimate_remaining_outputs("kimi", avg_tokens=40_000)
        expected = int(250_000 / (40_000 * 1.1))
        assert full == expected

    def test_full_session_lifecycle(self):
        """Complete lifecycle: create, record, warn, reset, reuse."""
        tracker = TokenBudgetTracker()

        # Phase 1: normal operation
        r1 = tracker.record_output("kimi", tokens_used=100_000)
        assert r1["status"] == "OK"

        # Phase 2: approaching warning
        r2 = tracker.record_output("kimi", tokens_used=90_000)  # total 190K = 76%
        assert r2["status"] == "WARNING"

        # Phase 3: reset and start fresh
        tracker.reset_agent("kimi")
        r3 = tracker.record_output("kimi", tokens_used=10_000)
        assert r3["status"] == "OK"
        assert r3["tokens_used"] == 10_000
