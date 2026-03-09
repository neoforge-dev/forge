"""Unit tests for forge_harness.handoff.triggers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from forge_harness.handoff.triggers import TriggerResult, _parse_iso, evaluate_session

# ---------------------------------------------------------------------------
# TriggerResult dataclass
# ---------------------------------------------------------------------------


class TestTriggerResult:
    def test_defaults(self):
        result = TriggerResult(should_create=True)
        assert result.should_create is True
        assert result.reason is None

    def test_with_reason(self):
        result = TriggerResult(should_create=False, reason="some reason")
        assert result.should_create is False
        assert result.reason == "some reason"

    def test_equality(self):
        a = TriggerResult(True, "x")
        b = TriggerResult(True, "x")
        assert a == b

    def test_inequality_on_flag(self):
        assert TriggerResult(True) != TriggerResult(False)

    def test_inequality_on_reason(self):
        assert TriggerResult(True, "a") != TriggerResult(True, "b")


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


class TestParseIso:
    def test_none_returns_none(self):
        assert _parse_iso(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_iso("") is None

    def test_valid_iso_without_tz(self):
        result = _parse_iso("2025-01-15T10:30:00")
        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30

    def test_valid_iso_with_utc_offset(self):
        result = _parse_iso("2025-06-01T12:00:00+00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_invalid_string_returns_none(self):
        assert _parse_iso("not-a-date") is None

    def test_partial_date_string_returns_none(self):
        # Partial strings like "2025-13-45" are invalid
        assert _parse_iso("2025-13-45T00:00:00") is None

    def test_whitespace_only_returns_none(self):
        # whitespace is falsy in Python, so treated as no value
        assert _parse_iso("   ") is None  # non-empty but not parseable → None via ValueError


# ---------------------------------------------------------------------------
# evaluate_session — context_percentage branch
# ---------------------------------------------------------------------------


class TestEvaluateSessionContextPercentage:
    def test_context_at_threshold_triggers(self):
        result = evaluate_session({"context_percentage": 50})
        assert result.should_create is True
        assert "50" in result.reason

    def test_context_above_threshold_triggers(self):
        result = evaluate_session({"context_percentage": 99})
        assert result.should_create is True

    def test_context_below_threshold_does_not_trigger(self):
        result = evaluate_session({"context_percentage": 49})
        assert result.should_create is False

    def test_context_as_string_at_threshold(self):
        result = evaluate_session({"context_percentage": "50"})
        assert result.should_create is True
        assert "50" in result.reason

    def test_context_as_string_above_threshold(self):
        result = evaluate_session({"context_percentage": "75"})
        assert result.should_create is True

    def test_context_as_string_below_threshold(self):
        result = evaluate_session({"context_percentage": "30"})
        assert result.should_create is False

    def test_invalid_string_context_skips_branch(self):
        # Non-numeric string → context_pct becomes None → branch skipped
        result = evaluate_session({"context_percentage": "high"})
        # Falls through to idle/status checks; with no activity or error, False
        assert result.should_create is False

    def test_context_none_skips_branch(self):
        result = evaluate_session({"context_percentage": None})
        assert result.should_create is False

    def test_custom_threshold_respected(self):
        result = evaluate_session({"context_percentage": 80}, context_threshold=90)
        assert result.should_create is False

    def test_custom_threshold_at_boundary(self):
        result = evaluate_session({"context_percentage": 90}, context_threshold=90)
        assert result.should_create is True

    def test_context_reason_includes_threshold(self):
        result = evaluate_session({"context_percentage": 60}, context_threshold=55)
        assert "55" in result.reason

    def test_context_as_float_is_not_int_skips_branch(self):
        # float is not isinstance(..., int) — branch is skipped
        result = evaluate_session({"context_percentage": 75.5})
        assert result.should_create is False

    def test_context_zero_does_not_trigger(self):
        result = evaluate_session({"context_percentage": 0})
        assert result.should_create is False


# ---------------------------------------------------------------------------
# evaluate_session — idle / last_activity branch
# ---------------------------------------------------------------------------


class TestEvaluateSessionIdle:
    def _now_minus(self, minutes: float) -> str:
        """Return an ISO timestamp that is `minutes` ago from now (UTC)."""
        ts = datetime.now(UTC) - timedelta(minutes=minutes)
        return ts.isoformat()

    def test_idle_at_threshold_triggers(self):
        ts = self._now_minus(5)
        result = evaluate_session({"last_activity": ts})
        assert result.should_create is True
        assert "idle" in result.reason

    def test_idle_above_threshold_triggers(self):
        ts = self._now_minus(60)
        result = evaluate_session({"last_activity": ts})
        assert result.should_create is True

    def test_idle_below_threshold_does_not_trigger(self):
        ts = self._now_minus(1)
        result = evaluate_session({"last_activity": ts})
        assert result.should_create is False

    def test_idle_reason_includes_minutes(self):
        ts = self._now_minus(10)
        result = evaluate_session({"last_activity": ts}, idle_minutes=5)
        assert "5m" in result.reason

    def test_custom_idle_threshold_respected(self):
        ts = self._now_minus(3)
        result = evaluate_session({"last_activity": ts}, idle_minutes=10)
        assert result.should_create is False

    def test_custom_idle_threshold_at_boundary(self):
        ts = self._now_minus(10)
        result = evaluate_session({"last_activity": ts}, idle_minutes=10)
        assert result.should_create is True

    def test_naive_datetime_receives_utc(self):
        # Naive ISO string (no tz) should be treated as UTC internally
        naive_ts = (datetime.now(UTC) - timedelta(minutes=10)).replace(tzinfo=None).isoformat()
        result = evaluate_session({"last_activity": naive_ts})
        assert result.should_create is True

    def test_invalid_last_activity_skips_branch(self):
        result = evaluate_session({"last_activity": "not-a-date"})
        assert result.should_create is False

    def test_none_last_activity_skips_branch(self):
        result = evaluate_session({"last_activity": None})
        assert result.should_create is False

    def test_missing_last_activity_key_skips_branch(self):
        result = evaluate_session({})
        assert result.should_create is False

    def test_context_takes_priority_over_idle(self):
        # Both context and idle conditions met — context branch fires first
        ts = self._now_minus(60)
        result = evaluate_session({"context_percentage": 80, "last_activity": ts})
        assert "context" in result.reason


# ---------------------------------------------------------------------------
# evaluate_session — status / error branch
# ---------------------------------------------------------------------------


class TestEvaluateSessionStatus:
    def test_status_error_triggers(self):
        result = evaluate_session({"status": "error"})
        assert result.should_create is True
        assert result.reason == "session error"

    def test_status_failed_triggers(self):
        result = evaluate_session({"status": "failed"})
        assert result.should_create is True
        assert result.reason == "session error"

    def test_status_running_does_not_trigger(self):
        result = evaluate_session({"status": "running"})
        assert result.should_create is False

    def test_status_success_does_not_trigger(self):
        result = evaluate_session({"status": "success"})
        assert result.should_create is False

    def test_status_none_does_not_trigger(self):
        result = evaluate_session({"status": None})
        assert result.should_create is False

    def test_status_empty_string_does_not_trigger(self):
        result = evaluate_session({"status": ""})
        assert result.should_create is False

    def test_status_case_sensitive_upper_does_not_trigger(self):
        # "Error" != "error" — set membership is case-sensitive
        result = evaluate_session({"status": "Error"})
        assert result.should_create is False

    def test_context_takes_priority_over_status(self):
        result = evaluate_session({"context_percentage": 60, "status": "error"})
        assert "context" in result.reason

    def test_idle_takes_priority_over_status(self):
        ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        result = evaluate_session({"last_activity": ts, "status": "error"})
        assert "idle" in result.reason


# ---------------------------------------------------------------------------
# evaluate_session — no-trigger / default path
# ---------------------------------------------------------------------------


class TestEvaluateSessionNoTrigger:
    def test_empty_dict_returns_false(self):
        result = evaluate_session({})
        assert result.should_create is False
        assert result.reason is None

    def test_unrelated_keys_ignored(self):
        result = evaluate_session({"agent": "kimi", "project": "voice-coach"})
        assert result.should_create is False

    def test_all_conditions_absent_returns_false(self):
        result = evaluate_session(
            {
                "context_percentage": 10,
                "last_activity": datetime.now(UTC).isoformat(),
                "status": "running",
            }
        )
        assert result.should_create is False
        assert result.reason is None


# ---------------------------------------------------------------------------
# evaluate_session — datetime.now mock to control "wall clock"
# ---------------------------------------------------------------------------


class TestEvaluateSessionClockMocked:
    """Use unittest.mock.patch to freeze time and verify boundary precision."""

    FROZEN = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

    def test_exactly_at_idle_threshold_triggers(self):
        activity = self.FROZEN - timedelta(minutes=5)
        with patch("forge_harness.handoff.triggers.datetime") as mock_dt:
            mock_dt.now.return_value = self.FROZEN
            mock_dt.fromisoformat.side_effect = datetime.fromisoformat
            result = evaluate_session({"last_activity": activity.isoformat()})
        assert result.should_create is True

    def test_one_second_before_threshold_does_not_trigger(self):
        activity = self.FROZEN - timedelta(minutes=5) + timedelta(seconds=1)
        with patch("forge_harness.handoff.triggers.datetime") as mock_dt:
            mock_dt.now.return_value = self.FROZEN
            mock_dt.fromisoformat.side_effect = datetime.fromisoformat
            result = evaluate_session({"last_activity": activity.isoformat()})
        assert result.should_create is False
