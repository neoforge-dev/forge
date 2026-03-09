"""Tests for token_budget.py - Token Budget Tracker"""
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from forge_harness.token_budget import (
    AgentBudget,
    TokenBudgetTracker,
    create_tracker_from_env,
)


class TestAgentBudget:
    """Tests for AgentBudget dataclass."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        budget = AgentBudget(agent_id="kimi", hourly_limit=250000)

        assert budget.agent_id == "kimi"
        assert budget.hourly_limit == 250000
        assert budget.tokens_used == 0
        assert budget.output_count == 0
        assert budget.last_output is None
        assert isinstance(budget.session_start, datetime)

    def test_reset(self):
        """Should reset all counters."""
        budget = AgentBudget(agent_id="kimi", hourly_limit=250000)
        budget.tokens_used = 1000
        budget.output_count = 5
        budget.last_output = datetime.now()

        budget.reset()

        assert budget.tokens_used == 0
        assert budget.output_count == 0
        assert budget.last_output is None
        assert isinstance(budget.session_start, datetime)

    def test_should_auto_reset_after_hour(self):
        """Should auto-reset after 1 hour."""
        budget = AgentBudget(agent_id="kimi", hourly_limit=250000)
        budget.session_start = datetime.now() - timedelta(hours=2)

        assert budget.should_auto_reset() is True

    def test_should_not_auto_reset_within_hour(self):
        """Should not auto-reset within 1 hour."""
        budget = AgentBudget(agent_id="kimi", hourly_limit=250000)
        budget.session_start = datetime.now() - timedelta(minutes=30)

        assert budget.should_auto_reset() is False


class TestTokenBudgetTracker:
    """Tests for TokenBudgetTracker."""

    @pytest.fixture
    def tracker(self):
        """Create a fresh tracker for testing."""
        return TokenBudgetTracker(auto_reset=False)

    def test_initialization(self, tracker):
        """Should initialize with correct thresholds."""
        assert tracker.WARNING_THRESHOLD == 0.75
        assert tracker.CRITICAL_THRESHOLD == 0.90
        assert tracker.auto_reset is False
        assert tracker.budgets == {}

    def test_default_limits(self, tracker):
        """Should have default limits for known agents."""
        assert tracker.DEFAULT_LIMITS["kimi"] == 250000
        assert tracker.DEFAULT_LIMITS["codex"] == 500000
        assert tracker.DEFAULT_LIMITS["gemini"] == 1000000

    def test_record_output_ok_status(self, tracker):
        """Should return OK status when under threshold."""
        result = tracker.record_output("kimi", tokens_used=10000)

        assert result["status"] == "OK"
        assert result["should_handoff"] is False
        assert "operating normally" in result["recommendation"]
        assert result["tokens_used"] == 10000
        assert result["output_count"] == 1

    def test_record_output_warning_status(self, tracker):
        """Should return WARNING status at 75% threshold."""
        # 75% of 250000 = 187500
        result = tracker.record_output("kimi", tokens_used=187500)

        assert result["status"] == "WARNING"
        assert result["should_handoff"] is True
        assert "PREPARE HANDOFF" in result["recommendation"]
        assert result["utilization"] == 0.75

    def test_record_output_critical_status(self, tracker):
        """Should return CRITICAL status at 90% threshold."""
        # 90% of 250000 = 225000
        result = tracker.record_output("kimi", tokens_used=225000)

        assert result["status"] == "CRITICAL"
        assert result["should_handoff"] is True
        assert "HANDOFF NOW" in result["recommendation"]
        assert result["utilization"] == 0.90

    def test_record_output_multiple(self, tracker):
        """Should accumulate tokens across multiple outputs."""
        tracker.record_output("kimi", tokens_used=50000)
        tracker.record_output("kimi", tokens_used=50000)
        result = tracker.record_output("kimi", tokens_used=50000)

        assert result["tokens_used"] == 150000
        assert result["output_count"] == 3

    def test_get_or_create_budget_unknown_agent(self, tracker):
        """Should use conservative limit for unknown agents."""
        budget = tracker._get_or_create_budget("unknown_agent")

        assert budget.agent_id == "unknown_agent"
        assert budget.hourly_limit == 250000  # Conservative default

    def test_get_or_create_budget_custom_limits(self):
        """Should use custom limits when provided."""
        tracker = TokenBudgetTracker(
            custom_limits={"custom_agent": 500000}, auto_reset=False
        )
        budget = tracker._get_or_create_budget("custom_agent")

        assert budget.hourly_limit == 500000

    def test_estimate_remaining_outputs(self, tracker):
        """Should estimate remaining outputs correctly."""
        # Use 100k tokens, 150k remaining
        tracker.record_output("kimi", tokens_used=100000)

        # With 40k avg tokens and 1.1 safety margin:
        # remaining = 150000 / (40000 * 1.1) = 3.4 -> 3
        estimate = tracker.estimate_remaining_outputs("kimi", avg_tokens=40000)

        assert estimate == 3

    def test_estimate_remaining_outputs_zero_budget(self, tracker):
        """Should return 0 when budget exhausted."""
        tracker.record_output("kimi", tokens_used=250000)

        estimate = tracker.estimate_remaining_outputs("kimi", avg_tokens=1000)

        assert estimate == 0

    def test_should_handoff_below_threshold(self, tracker):
        """Should not handoff below warning threshold."""
        tracker.record_output("kimi", tokens_used=100000)  # 40%

        assert tracker.should_handoff("kimi") is False

    def test_should_handoff_at_threshold(self, tracker):
        """Should handoff at warning threshold."""
        tracker.record_output("kimi", tokens_used=187500)  # 75%

        assert tracker.should_handoff("kimi") is True

    def test_get_status(self, tracker):
        """Should return complete status dict."""
        tracker.record_output("kimi", tokens_used=100000)
        status = tracker.get_status("kimi")

        assert status["agent_id"] == "kimi"
        assert status["status"] == "OK"
        assert status["utilization"] == 0.4
        assert status["utilization_pct"] == "40%"
        assert status["tokens_used"] == 100000
        assert status["tokens_remaining"] == 150000
        assert status["hourly_limit"] == 250000
        assert status["output_count"] == 1
        assert "session_start" in status
        assert "session_duration_minutes" in status

    def test_get_all_statuses(self, tracker):
        """Should return status for all tracked agents."""
        tracker.record_output("kimi", tokens_used=10000)
        tracker.record_output("codex", tokens_used=20000)

        all_statuses = tracker.get_all_statuses()

        assert len(all_statuses) == 2
        assert "kimi" in all_statuses
        assert "codex" in all_statuses

    def test_reset_agent(self, tracker):
        """Should reset specific agent budget."""
        tracker.record_output("kimi", tokens_used=100000)
        tracker.reset_agent("kimi")

        status = tracker.get_status("kimi")
        assert status["tokens_used"] == 0
        assert status["output_count"] == 0

    def test_reset_agent_unknown(self, tracker):
        """Should handle reset of unknown agent gracefully."""
        # Should not raise
        tracker.reset_agent("unknown")

    def test_reset_all(self, tracker):
        """Should reset all agent budgets."""
        tracker.record_output("kimi", tokens_used=100000)
        tracker.record_output("codex", tokens_used=200000)
        tracker.reset_all()

        assert tracker.get_status("kimi")["tokens_used"] == 0
        assert tracker.get_status("codex")["tokens_used"] == 0

    def test_auto_reset_on_hourly_window(self):
        """Should auto-reset when hourly window passes."""
        tracker = TokenBudgetTracker(auto_reset=True)
        tracker.record_output("kimi", tokens_used=100000)

        # Simulate time passing
        budget = tracker.budgets["kimi"]
        budget.session_start = datetime.now() - timedelta(hours=2)

        # Next record should trigger auto-reset
        result = tracker.record_output("kimi", tokens_used=50000)

        assert result["tokens_used"] == 50000  # Reset, not accumulated
        assert result["output_count"] == 1


class TestCreateTrackerFromEnv:
    """Tests for create_tracker_from_env factory function."""

    def test_create_tracker_with_env_vars(self):
        """Should create tracker with limits from environment."""
        env_vars = {
            "TOKEN_LIMIT_KIMI": "300000",
            "TOKEN_LIMIT_CODEX": "600000",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            tracker = create_tracker_from_env()

        budget = tracker._get_or_create_budget("kimi")
        assert budget.hourly_limit == 300000

    def test_create_tracker_with_invalid_env_var(self):
        """Should handle invalid env var values gracefully."""
        env_vars = {"TOKEN_LIMIT_KIMI": "invalid"}

        with patch.dict(os.environ, env_vars, clear=False):
            tracker = create_tracker_from_env()

        # Should fall back to default
        budget = tracker._get_or_create_budget("kimi")
        assert budget.hourly_limit == 250000

    def test_create_tracker_without_env_vars(self):
        """Should create tracker with defaults when no env vars."""
        tracker = create_tracker_from_env()

        budget = tracker._get_or_create_budget("kimi")
        assert budget.hourly_limit == 250000
