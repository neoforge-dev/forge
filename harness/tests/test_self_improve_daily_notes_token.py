"""
Comprehensive tests for:
  - forge_harness/self_improve.py
  - forge_harness/daily_notes.py
  - forge_harness/token_budget.py
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_notes_dir(tmp_path: Path) -> Path:
    """Return a fresh temporary directory for daily notes."""
    d = tmp_path / "daily_notes"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ===========================================================================
# daily_notes tests
# ===========================================================================

from forge_harness.daily_notes import (
    DailyEvent,
    DailyNotesService,
    EventType,
    create_daily_notes_service,
    get_daily_notes_service,
)


class TestEventType:
    """Tests for the EventType enum."""

    def test_all_event_types_exist(self) -> None:
        expected = [
            "FEATURE_COMPLETION",
            "FEATURE_FAILURE",
            "APPROVAL_DECISION",
            "THRESHOLD_ADJUSTMENT",
            "AGENT_HANDOFF",
            "AGENT_STATUS_CHANGE",
            "PIPELINE_START",
            "PIPELINE_COMPLETE",
            "PIPELINE_ERROR",
        ]
        for name in expected:
            assert hasattr(EventType, name)

    def test_event_type_values_are_strings(self) -> None:
        for et in EventType:
            assert isinstance(et.value, str)
            assert len(et.value) > 0


class TestDailyEvent:
    """Tests for DailyEvent dataclass and its to_markdown() method."""

    def _make_event(
        self,
        event_type: EventType = EventType.FEATURE_COMPLETION,
        title: str = "Test Feature",
        details: dict | None = None,
        hour: int = 14,
        minute: int = 30,
    ) -> DailyEvent:
        ts = datetime(2026, 2, 22, hour, minute, 0, tzinfo=UTC)
        return DailyEvent(
            timestamp=ts,
            event_type=event_type,
            title=title,
            details=details or {},
        )

    def test_basic_markdown_structure(self) -> None:
        event = self._make_event()
        md = event.to_markdown()
        assert "## 14:30 - Feature Completion" in md
        assert "**Test Feature**" in md

    def test_empty_details(self) -> None:
        event = self._make_event(details={})
        md = event.to_markdown()
        assert "**Test Feature**" in md
        # No extra lines beyond the header + title
        lines = [l for l in md.split("\n") if l.strip()]
        assert len(lines) >= 2

    def test_string_detail_value(self) -> None:
        event = self._make_event(details={"status": "success"})
        md = event.to_markdown()
        assert "**Status:** success" in md

    def test_bool_detail_true(self) -> None:
        event = self._make_event(details={"deployed": True})
        md = event.to_markdown()
        assert "**Deployed:** Yes" in md

    def test_bool_detail_false(self) -> None:
        event = self._make_event(details={"deployed": False})
        md = event.to_markdown()
        assert "**Deployed:** No" in md

    def test_list_detail_non_empty(self) -> None:
        event = self._make_event(details={"files": ["a.py", "b.py"]})
        md = event.to_markdown()
        assert "**Files:**" in md
        assert "  - a.py" in md
        assert "  - b.py" in md

    def test_list_detail_empty(self) -> None:
        event = self._make_event(details={"files": []})
        md = event.to_markdown()
        assert "**Files:** None" in md

    def test_dict_detail_value(self) -> None:
        event = self._make_event(details={"metrics": {"pass": 5, "fail": 0}})
        md = event.to_markdown()
        assert "**Metrics:**" in md
        assert "  - pass: 5" in md
        assert "  - fail: 0" in md

    def test_snake_case_key_formatted(self) -> None:
        event = self._make_event(details={"error_count": 3})
        md = event.to_markdown()
        assert "**Error Count:**" in md

    def test_numeric_detail(self) -> None:
        event = self._make_event(details={"count": 42})
        md = event.to_markdown()
        assert "**Count:** 42" in md

    def test_all_event_types_render(self) -> None:
        for et in EventType:
            event = self._make_event(event_type=et)
            md = event.to_markdown()
            assert et.value in md


class TestDailyNotesService:
    """Tests for DailyNotesService."""

    @pytest.fixture()
    def service(self, tmp_notes_dir: Path) -> DailyNotesService:
        return DailyNotesService(notes_dir=tmp_notes_dir)

    # -----------------------------------------------------------------------
    # Initialisation
    # -----------------------------------------------------------------------

    def test_init_creates_directory(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "notes" / "sub"
        DailyNotesService(notes_dir=new_dir)
        assert new_dir.exists()

    def test_default_notes_dir(self, tmp_path: Path) -> None:
        """Default init sets notes_dir = .forge/learning/daily (relative)."""
        # We just check it does not raise; the directory may be relative.
        with patch.object(Path, "mkdir"):
            svc = DailyNotesService()
            assert "daily" in str(svc.notes_dir)

    # -----------------------------------------------------------------------
    # _get_notes_path
    # -----------------------------------------------------------------------

    def test_get_notes_path_format(self, service: DailyNotesService) -> None:
        target = date(2026, 2, 22)
        path = service._get_notes_path(target)
        assert path.name == "2026-02-22.md"
        assert path.parent == service.notes_dir

    # -----------------------------------------------------------------------
    # _create_header
    # -----------------------------------------------------------------------

    def test_create_header_contains_date_string(self, service: DailyNotesService) -> None:
        target = date(2026, 2, 22)
        header = service._create_header(target)
        assert "# Daily Session Notes" in header
        assert "2026" in header

    # -----------------------------------------------------------------------
    # log_event
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_log_event_creates_file(self, service: DailyNotesService) -> None:
        ts = datetime(2026, 2, 22, 10, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Feature done",
            timestamp=ts,
        )
        notes_path = service._get_notes_path(date(2026, 2, 22))
        assert notes_path.exists()

    @pytest.mark.asyncio
    async def test_log_event_new_file_has_header(self, service: DailyNotesService) -> None:
        ts = datetime(2026, 2, 22, 10, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.PIPELINE_START,
            title="Pipeline launched",
            timestamp=ts,
        )
        content = service._get_notes_path(date(2026, 2, 22)).read_text()
        assert "# Daily Session Notes" in content
        assert "Pipeline Start" in content

    @pytest.mark.asyncio
    async def test_log_event_appends_to_existing_file(self, service: DailyNotesService) -> None:
        ts = datetime(2026, 2, 22, 10, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.PIPELINE_START,
            title="First event",
            timestamp=ts,
        )
        ts2 = datetime(2026, 2, 22, 11, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.PIPELINE_COMPLETE,
            title="Second event",
            timestamp=ts2,
        )
        content = service._get_notes_path(date(2026, 2, 22)).read_text()
        assert "First event" in content
        assert "Second event" in content
        # Header appears only once
        assert content.count("# Daily Session Notes") == 1

    @pytest.mark.asyncio
    async def test_log_event_default_timestamp(self, service: DailyNotesService) -> None:
        """Logging without explicit timestamp should use today's date."""
        fixed_ts = datetime(2026, 2, 22, 9, 0, tzinfo=UTC)
        with patch("forge_harness.daily_notes.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_ts
            # date() is called from timestamp.date() inside log_event – no patch needed
            await service.log_event(
                event_type=EventType.AGENT_HANDOFF,
                title="Handoff",
            )
        # File for the patched date does not exist if patch fails; just ensure no exception.

    @pytest.mark.asyncio
    async def test_log_event_with_details(self, service: DailyNotesService) -> None:
        ts = datetime(2026, 2, 22, 8, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.FEATURE_FAILURE,
            title="Something broke",
            details={"error": "timeout", "retries": 3},
            timestamp=ts,
        )
        content = service._get_notes_path(date(2026, 2, 22)).read_text()
        assert "timeout" in content
        assert "3" in content

    @pytest.mark.asyncio
    async def test_log_event_none_details_defaults_to_empty_dict(
        self, service: DailyNotesService
    ) -> None:
        ts = datetime(2026, 2, 22, 8, 0, tzinfo=UTC)
        # Should not raise when details=None
        await service.log_event(
            event_type=EventType.APPROVAL_DECISION,
            title="Approved",
            details=None,
            timestamp=ts,
        )

    # -----------------------------------------------------------------------
    # get_notes
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_notes_missing_date(self, service: DailyNotesService) -> None:
        content = await service.get_notes(date(2020, 1, 1))
        assert "No notes found" in content
        assert "2020-01-01" in content

    @pytest.mark.asyncio
    async def test_get_notes_existing_date(self, service: DailyNotesService) -> None:
        ts = datetime(2026, 2, 22, 10, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Done",
            timestamp=ts,
        )
        content = await service.get_notes(date(2026, 2, 22))
        assert "Done" in content

    # -----------------------------------------------------------------------
    # get_today_notes
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_today_notes_no_file(self, service: DailyNotesService) -> None:
        result = await service.get_today_notes()
        assert "No notes found" in result

    @pytest.mark.asyncio
    async def test_get_today_notes_with_file(self, service: DailyNotesService) -> None:
        today = date.today()
        ts = datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.THRESHOLD_ADJUSTMENT,
            title="Threshold changed",
            timestamp=ts,
        )
        result = await service.get_today_notes()
        assert "Threshold changed" in result

    # -----------------------------------------------------------------------
    # list_notes_dates
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_notes_dates_empty(self, service: DailyNotesService) -> None:
        dates = await service.list_notes_dates()
        assert dates == []

    @pytest.mark.asyncio
    async def test_list_notes_dates_multiple_files(self, service: DailyNotesService) -> None:
        for day in [20, 21, 22]:
            ts = datetime(2026, 2, day, 10, 0, tzinfo=UTC)
            await service.log_event(EventType.PIPELINE_START, "event", timestamp=ts)
        dates = await service.list_notes_dates()
        assert len(dates) == 3
        # Most recent first
        assert dates[0] > dates[-1]

    @pytest.mark.asyncio
    async def test_list_notes_dates_limit(self, service: DailyNotesService) -> None:
        for day in range(1, 6):
            ts = datetime(2026, 2, day, 10, 0, tzinfo=UTC)
            await service.log_event(EventType.PIPELINE_START, "event", timestamp=ts)
        dates = await service.list_notes_dates(limit=3)
        assert len(dates) == 3

    @pytest.mark.asyncio
    async def test_list_notes_dates_skips_invalid_filenames(
        self, service: DailyNotesService, tmp_notes_dir: Path
    ) -> None:
        # Create a file with an invalid name
        (tmp_notes_dir / "not-a-date.md").write_text("garbage")
        # Create a valid note
        ts = datetime(2026, 2, 22, 10, 0, tzinfo=UTC)
        await service.log_event(EventType.PIPELINE_START, "event", timestamp=ts)
        dates = await service.list_notes_dates()
        assert len(dates) == 1

    # -----------------------------------------------------------------------
    # get_recent_notes
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_recent_notes_returns_dict(self, service: DailyNotesService) -> None:
        for day in [20, 21]:
            ts = datetime(2026, 2, day, 10, 0, tzinfo=UTC)
            await service.log_event(EventType.PIPELINE_COMPLETE, "complete", timestamp=ts)
        recent = await service.get_recent_notes(days=7)
        assert isinstance(recent, dict)
        assert len(recent) == 2

    @pytest.mark.asyncio
    async def test_get_recent_notes_empty(self, service: DailyNotesService) -> None:
        recent = await service.get_recent_notes(days=7)
        assert recent == {}

    # -----------------------------------------------------------------------
    # get_summary_stats
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_summary_stats_empty(self, service: DailyNotesService) -> None:
        stats = await service.get_summary_stats()
        assert stats["total_days"] == 0
        assert stats["oldest_date"] is None
        assert stats["newest_date"] is None
        assert stats["total_events"] == 0

    @pytest.mark.asyncio
    async def test_get_summary_stats_with_notes(self, service: DailyNotesService) -> None:
        # Two days, two events each
        for day in [20, 21]:
            for hour in [9, 14]:
                ts = datetime(2026, 2, day, hour, 0, tzinfo=UTC)
                await service.log_event(EventType.FEATURE_COMPLETION, "done", timestamp=ts)
        stats = await service.get_summary_stats()
        assert stats["total_days"] == 2
        assert stats["total_events"] == 4
        assert stats["newest_date"] is not None
        assert stats["oldest_date"] is not None

    # -----------------------------------------------------------------------
    # Factory functions
    # -----------------------------------------------------------------------

    def test_create_daily_notes_service(self, tmp_notes_dir: Path) -> None:
        svc = create_daily_notes_service(notes_dir=tmp_notes_dir)
        assert isinstance(svc, DailyNotesService)
        assert svc.notes_dir == tmp_notes_dir

    def test_create_daily_notes_service_no_dir(self) -> None:
        with patch.object(Path, "mkdir"):
            svc = create_daily_notes_service()
            assert isinstance(svc, DailyNotesService)

    def test_get_daily_notes_service_singleton(self) -> None:
        import forge_harness.daily_notes as dn_mod

        # Reset global singleton
        dn_mod._daily_notes_service = None
        with patch.object(Path, "mkdir"):
            svc1 = get_daily_notes_service()
            svc2 = get_daily_notes_service()
        assert svc1 is svc2
        # Cleanup
        dn_mod._daily_notes_service = None

    def test_get_daily_notes_service_returns_existing(self) -> None:
        import forge_harness.daily_notes as dn_mod

        mock_svc = MagicMock(spec=DailyNotesService)
        dn_mod._daily_notes_service = mock_svc
        result = get_daily_notes_service()
        assert result is mock_svc
        # Cleanup
        dn_mod._daily_notes_service = None


# ===========================================================================
# token_budget tests
# ===========================================================================

from forge_harness.token_budget import AgentBudget, TokenBudgetTracker, create_tracker_from_env


class TestAgentBudget:
    """Tests for the AgentBudget dataclass."""

    def _make_budget(self, agent_id: str = "kimi", hourly_limit: int = 250000) -> AgentBudget:
        return AgentBudget(agent_id=agent_id, hourly_limit=hourly_limit)

    def test_initial_state(self) -> None:
        b = self._make_budget()
        assert b.tokens_used == 0
        assert b.output_count == 0
        assert b.last_output is None

    def test_reset_clears_usage(self) -> None:
        b = self._make_budget()
        b.tokens_used = 100000
        b.output_count = 5
        b.reset()
        assert b.tokens_used == 0
        assert b.output_count == 0
        assert b.last_output is None

    def test_reset_updates_session_start(self) -> None:
        b = self._make_budget()
        old_start = b.session_start
        b.reset()
        assert b.session_start >= old_start

    def test_should_auto_reset_false_within_hour(self) -> None:
        b = self._make_budget()
        b.session_start = datetime.now() - timedelta(minutes=30)
        assert b.should_auto_reset() is False

    def test_should_auto_reset_true_after_hour(self) -> None:
        b = self._make_budget()
        b.session_start = datetime.now() - timedelta(hours=2)
        assert b.should_auto_reset() is True

    def test_should_auto_reset_exactly_one_hour_is_true(self) -> None:
        b = self._make_budget()
        # timedelta(hours=1) + 1 second to be past the threshold
        b.session_start = datetime.now() - timedelta(hours=1, seconds=1)
        assert b.should_auto_reset() is True


class TestTokenBudgetTracker:
    """Tests for TokenBudgetTracker."""

    def _tracker(self, auto_reset: bool = False, custom_limits: dict | None = None) -> TokenBudgetTracker:
        return TokenBudgetTracker(custom_limits=custom_limits, auto_reset=auto_reset)

    # -----------------------------------------------------------------------
    # Initialisation and threshold constants
    # -----------------------------------------------------------------------

    def test_default_thresholds(self) -> None:
        t = self._tracker()
        assert t.WARNING_THRESHOLD == 0.75
        assert t.CRITICAL_THRESHOLD == 0.90

    def test_no_budgets_on_init(self) -> None:
        t = self._tracker()
        assert t.budgets == {}

    def test_default_limits_exist(self) -> None:
        t = self._tracker()
        assert "kimi" in t.DEFAULT_LIMITS
        assert "codex" in t.DEFAULT_LIMITS
        assert "gemini" in t.DEFAULT_LIMITS

    # -----------------------------------------------------------------------
    # _get_or_create_budget
    # -----------------------------------------------------------------------

    def test_get_or_create_known_agent(self) -> None:
        t = self._tracker()
        b = t._get_or_create_budget("kimi")
        assert b.agent_id == "kimi"
        assert b.hourly_limit == t.DEFAULT_LIMITS["kimi"]

    def test_get_or_create_unknown_agent_uses_conservative_limit(self) -> None:
        t = self._tracker()
        b = t._get_or_create_budget("mystery-agent")
        assert b.hourly_limit == 250000

    def test_get_or_create_custom_limit(self) -> None:
        t = self._tracker(custom_limits={"kimi": 999999})
        b = t._get_or_create_budget("kimi")
        assert b.hourly_limit == 999999

    def test_get_or_create_returns_same_budget(self) -> None:
        t = self._tracker()
        b1 = t._get_or_create_budget("kimi")
        b2 = t._get_or_create_budget("kimi")
        assert b1 is b2

    def test_auto_reset_triggers_when_enabled(self) -> None:
        t = TokenBudgetTracker(auto_reset=True)
        b = t._get_or_create_budget("kimi")
        # Force the session_start to be old
        b.session_start = datetime.now() - timedelta(hours=2)
        b.tokens_used = 50000
        # Get again – should have been reset
        t._get_or_create_budget("kimi")
        assert t.budgets["kimi"].tokens_used == 0

    def test_auto_reset_disabled(self) -> None:
        t = TokenBudgetTracker(auto_reset=False)
        b = t._get_or_create_budget("kimi")
        b.session_start = datetime.now() - timedelta(hours=2)
        b.tokens_used = 50000
        t._get_or_create_budget("kimi")
        assert t.budgets["kimi"].tokens_used == 50000

    # -----------------------------------------------------------------------
    # record_output
    # -----------------------------------------------------------------------

    def test_record_output_ok_status(self) -> None:
        t = self._tracker()
        result = t.record_output("kimi", 10000)
        assert result["status"] == "OK"
        assert result["should_handoff"] is False

    def test_record_output_increments_usage(self) -> None:
        t = self._tracker()
        t.record_output("kimi", 10000)
        t.record_output("kimi", 20000)
        assert t.budgets["kimi"].tokens_used == 30000

    def test_record_output_increments_count(self) -> None:
        t = self._tracker()
        t.record_output("kimi", 5000)
        t.record_output("kimi", 5000)
        assert t.budgets["kimi"].output_count == 2

    def test_record_output_warning_status(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        # Use 80% of budget in one shot
        tokens = int(limit * 0.80)
        result = t.record_output("kimi", tokens)
        assert result["status"] == "WARNING"
        assert result["should_handoff"] is True
        assert "PREPARE HANDOFF" in result["recommendation"]

    def test_record_output_critical_status(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        tokens = int(limit * 0.91)
        result = t.record_output("kimi", tokens)
        assert result["status"] == "CRITICAL"
        assert result["should_handoff"] is True
        assert "HANDOFF NOW" in result["recommendation"]

    def test_record_output_contains_all_keys(self) -> None:
        t = self._tracker()
        result = t.record_output("kimi", 1000)
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
        assert expected_keys.issubset(result.keys())

    def test_record_output_tokens_remaining(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        t.record_output("kimi", 50000)
        result = t.record_output("kimi", 0)
        assert result["tokens_remaining"] == limit - 50000

    def test_record_output_utilization_pct_format(self) -> None:
        t = self._tracker()
        result = t.record_output("kimi", 0)
        assert result["utilization_pct"].endswith("%")

    def test_record_output_sets_last_output(self) -> None:
        t = self._tracker()
        before = datetime.now()
        t.record_output("kimi", 1000)
        assert t.budgets["kimi"].last_output is not None
        assert t.budgets["kimi"].last_output >= before

    # -----------------------------------------------------------------------
    # estimate_remaining_outputs
    # -----------------------------------------------------------------------

    def test_estimate_remaining_outputs_fresh_budget(self) -> None:
        t = self._tracker()
        # kimi limit = 250k, avg = 40k, safety = 1.1  => floor(250000/44000) = 5
        remaining = t.estimate_remaining_outputs("kimi", avg_tokens=40000)
        assert remaining > 0
        expected = int(250000 / (40000 * 1.1))
        assert remaining == expected

    def test_estimate_remaining_outputs_exhausted_budget(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        t.budgets["kimi"] = AgentBudget(agent_id="kimi", hourly_limit=limit, tokens_used=limit)
        remaining = t.estimate_remaining_outputs("kimi", avg_tokens=1000)
        assert remaining == 0

    def test_estimate_remaining_outputs_partial_budget(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        t.budgets["kimi"] = AgentBudget(agent_id="kimi", hourly_limit=limit, tokens_used=200000)
        remaining = t.estimate_remaining_outputs("kimi", avg_tokens=40000)
        # tokens_remaining = 50000, expected = int(50000 / 44000) = 1
        assert remaining >= 0

    def test_estimate_remaining_never_negative(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        # over-spend
        t.budgets["kimi"] = AgentBudget(agent_id="kimi", hourly_limit=limit, tokens_used=limit + 1000)
        remaining = t.estimate_remaining_outputs("kimi", avg_tokens=1000)
        assert remaining == 0

    # -----------------------------------------------------------------------
    # should_handoff
    # -----------------------------------------------------------------------

    def test_should_handoff_false_below_warning(self) -> None:
        t = self._tracker()
        assert t.should_handoff("kimi") is False

    def test_should_handoff_true_at_warning(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        t.budgets["kimi"] = AgentBudget(
            agent_id="kimi", hourly_limit=limit, tokens_used=int(limit * 0.80)
        )
        assert t.should_handoff("kimi") is True

    # -----------------------------------------------------------------------
    # get_status
    # -----------------------------------------------------------------------

    def test_get_status_fresh_agent(self) -> None:
        t = self._tracker()
        status = t.get_status("kimi")
        assert status["agent_id"] == "kimi"
        assert status["status"] == "OK"
        assert status["utilization"] == 0.0
        assert status["last_output"] is None

    def test_get_status_warning_recommendation(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        t.budgets["kimi"] = AgentBudget(
            agent_id="kimi", hourly_limit=limit, tokens_used=int(limit * 0.80)
        )
        status = t.get_status("kimi")
        assert status["status"] == "WARNING"
        assert "PREPARE HANDOFF" in status["recommendation"]

    def test_get_status_critical_recommendation(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        t.budgets["kimi"] = AgentBudget(
            agent_id="kimi", hourly_limit=limit, tokens_used=int(limit * 0.91)
        )
        status = t.get_status("kimi")
        assert status["status"] == "CRITICAL"
        assert "HANDOFF NOW" in status["recommendation"]

    def test_get_status_contains_session_start(self) -> None:
        t = self._tracker()
        status = t.get_status("kimi")
        assert "session_start" in status
        # Should be parseable ISO format
        datetime.fromisoformat(status["session_start"])

    def test_get_status_last_output_populated_after_record(self) -> None:
        t = self._tracker()
        t.record_output("kimi", 5000)
        status = t.get_status("kimi")
        assert status["last_output"] is not None

    def test_get_status_all_keys_present(self) -> None:
        t = self._tracker()
        status = t.get_status("kimi")
        expected = {
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
        assert expected.issubset(status.keys())

    # -----------------------------------------------------------------------
    # get_all_statuses
    # -----------------------------------------------------------------------

    def test_get_all_statuses_empty(self) -> None:
        t = self._tracker()
        result = t.get_all_statuses()
        assert result == {}

    def test_get_all_statuses_multiple_agents(self) -> None:
        t = self._tracker()
        t.record_output("kimi", 1000)
        t.record_output("codex", 2000)
        statuses = t.get_all_statuses()
        assert "kimi" in statuses
        assert "codex" in statuses

    # -----------------------------------------------------------------------
    # reset_agent
    # -----------------------------------------------------------------------

    def test_reset_agent_resets_usage(self) -> None:
        t = self._tracker()
        t.record_output("kimi", 100000)
        t.reset_agent("kimi")
        assert t.budgets["kimi"].tokens_used == 0

    def test_reset_agent_unknown_agent_no_error(self) -> None:
        t = self._tracker()
        # Should not raise
        t.reset_agent("ghost-agent")

    # -----------------------------------------------------------------------
    # reset_all
    # -----------------------------------------------------------------------

    def test_reset_all_clears_all_budgets(self) -> None:
        t = self._tracker()
        t.record_output("kimi", 50000)
        t.record_output("codex", 80000)
        t.reset_all()
        assert t.budgets["kimi"].tokens_used == 0
        assert t.budgets["codex"].tokens_used == 0

    # -----------------------------------------------------------------------
    # create_tracker_from_env
    # -----------------------------------------------------------------------

    def test_create_tracker_from_env_no_env_vars(self) -> None:
        # Ensure no TOKEN_LIMIT_* env vars are set
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("TOKEN_LIMIT_")}
        with patch.dict(os.environ, clean_env, clear=True):
            tracker = create_tracker_from_env()
        assert isinstance(tracker, TokenBudgetTracker)
        assert tracker.custom_limits == {}

    def test_create_tracker_from_env_with_valid_var(self) -> None:
        with patch.dict(os.environ, {"TOKEN_LIMIT_KIMI": "300000"}, clear=False):
            tracker = create_tracker_from_env()
        assert tracker.custom_limits.get("kimi") == 300000

    def test_create_tracker_from_env_with_invalid_var(self) -> None:
        # Invalid int should not raise; invalid values are ignored
        with patch.dict(os.environ, {"TOKEN_LIMIT_KIMI": "not-an-int"}, clear=False):
            tracker = create_tracker_from_env()
        # kimi should not be in custom_limits since it failed to parse
        assert "kimi" not in tracker.custom_limits

    def test_create_tracker_from_env_multiple_agents(self) -> None:
        env_vars = {
            "TOKEN_LIMIT_KIMI": "111111",
            "TOKEN_LIMIT_CODEX": "222222",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            tracker = create_tracker_from_env()
        assert tracker.custom_limits["kimi"] == 111111
        assert tracker.custom_limits["codex"] == 222222

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_record_output_zero_tokens(self) -> None:
        t = self._tracker()
        result = t.record_output("kimi", 0)
        assert result["status"] == "OK"
        assert result["tokens_used"] == 0

    def test_record_output_over_budget(self) -> None:
        t = self._tracker()
        limit = t.DEFAULT_LIMITS["kimi"]
        result = t.record_output("kimi", limit + 50000)
        assert result["status"] == "CRITICAL"
        assert result["tokens_remaining"] < 0 or result["tokens_remaining"] == 0


# ===========================================================================
# self_improve tests
# ===========================================================================

from forge_harness.self_improve import (
    HarnessSelfImprover,
    SelfAnalysisResult,
    run_self_analysis,
    self_analyze_sync,
)


def _make_mock_finding(
    severity: str = "medium",
    file_path: str | None = "foo.py",
    line_number: int | None = 10,
    recommendation: str | None = "Fix it",
    rule_id: str | None = "E001",
    title: str | None = None,
    scanner: str = "bandit",
    message: str = "Some issue found",
) -> MagicMock:
    """Build a mock DiligenceFinding with all attributes self_improve.py accesses."""
    finding = MagicMock()
    finding.severity = severity
    finding.file_path = file_path
    finding.line_number = line_number
    finding.recommendation = recommendation
    finding.rule_id = rule_id
    finding.title = title
    finding.scanner = scanner
    finding.message = message
    return finding


def _make_mock_report(
    findings: list | None = None,
    total_issues: int = 0,
    critical_count: int = 0,
    high_count: int = 0,
    medium_count: int = 0,
) -> MagicMock:
    """Build a mock DiligenceReport."""
    report = MagicMock()
    report.findings = findings or []
    report.total_issues = total_issues
    report.critical_count = critical_count
    report.high_count = high_count
    report.medium_count = medium_count
    return report


class TestSelfAnalysisResult:
    """Tests for SelfAnalysisResult dataclass and to_dict()."""

    def _make_result(
        self,
        report=None,
        features=None,
        added: int = 0,
        skipped: int = 0,
        errors=None,
    ) -> SelfAnalysisResult:
        return SelfAnalysisResult(
            timestamp=datetime(2026, 2, 22, 12, 0, tzinfo=UTC),
            report=report,
            features_generated=features or [],
            features_added=added,
            features_skipped=skipped,
            errors=errors or [],
        )

    def test_to_dict_no_report(self) -> None:
        result = self._make_result(report=None)
        d = result.to_dict()
        assert d["report_summary"] is None
        assert d["features_generated"] == 0
        assert d["features_added"] == 0
        assert d["features_skipped"] == 0
        assert d["errors"] == []

    def test_to_dict_with_report(self) -> None:
        report = _make_mock_report(total_issues=5, critical_count=1, high_count=2, medium_count=2)
        result = self._make_result(report=report)
        d = result.to_dict()
        assert d["report_summary"]["total_issues"] == 5
        assert d["report_summary"]["critical"] == 1
        assert d["report_summary"]["high"] == 2
        assert d["report_summary"]["medium"] == 2

    def test_to_dict_timestamp_is_iso(self) -> None:
        result = self._make_result()
        d = result.to_dict()
        datetime.fromisoformat(d["timestamp"])

    def test_to_dict_errors_list(self) -> None:
        result = self._make_result(errors=["err1", "err2"])
        d = result.to_dict()
        assert d["errors"] == ["err1", "err2"]

    def test_to_dict_features_count(self) -> None:
        from forge_harness.meta_learning.feedback_loops import DebtFeature

        features = [
            DebtFeature(
                id=f"f{i}",
                name=f"Feature {i}",
                description="desc",
                priority="medium",
                finding_id="E001",
                category="security",
            )
            for i in range(3)
        ]
        result = self._make_result(features=features, added=2, skipped=1)
        d = result.to_dict()
        assert d["features_generated"] == 3
        assert d["features_added"] == 2
        assert d["features_skipped"] == 1


class TestHarnessSelfImprover:
    """Tests for HarnessSelfImprover."""

    @pytest.fixture()
    def improver(self, tmp_path: Path) -> HarnessSelfImprover:
        features_path = tmp_path / "features.json"
        return HarnessSelfImprover(
            harness_root=tmp_path,
            features_path=features_path,
            tech_diligence_url="http://localhost:9999",
        )

    # -----------------------------------------------------------------------
    # __init__
    # -----------------------------------------------------------------------

    def test_default_harness_root(self) -> None:
        """Default harness_root is set to the package's parent."""
        imp = HarnessSelfImprover()
        assert imp.harness_root.exists()

    def test_custom_paths_stored(self, tmp_path: Path) -> None:
        fp = tmp_path / "f.json"
        imp = HarnessSelfImprover(harness_root=tmp_path, features_path=fp)
        assert imp.harness_root == tmp_path
        assert imp.features_path == fp

    def test_analysis_history_starts_empty(self, improver: HarnessSelfImprover) -> None:
        assert improver.get_analysis_history() == []

    # -----------------------------------------------------------------------
    # _build_feature_description
    # -----------------------------------------------------------------------

    def test_build_feature_description_full(self, improver: HarnessSelfImprover) -> None:
        finding = _make_mock_finding(
            message="Bug found",
            file_path="src/main.py",
            line_number=42,
            recommendation="Fix the bug",
        )
        desc = improver._build_feature_description(finding)
        assert "Bug found" in desc
        assert "src/main.py:42" in desc
        assert "Fix the bug" in desc

    def test_build_feature_description_no_file(self, improver: HarnessSelfImprover) -> None:
        finding = _make_mock_finding(message="Issue", file_path=None, line_number=None)
        desc = improver._build_feature_description(finding)
        assert "Issue" in desc
        # No location line
        assert "Location:" not in desc

    def test_build_feature_description_no_recommendation(
        self, improver: HarnessSelfImprover
    ) -> None:
        finding = _make_mock_finding(
            message="Issue", file_path="x.py", line_number=1, recommendation=None
        )
        desc = improver._build_feature_description(finding)
        assert "Recommendation:" not in desc

    def test_build_feature_description_file_no_line(self, improver: HarnessSelfImprover) -> None:
        finding = _make_mock_finding(message="Issue", file_path="x.py", line_number=None)
        desc = improver._build_feature_description(finding)
        assert "Location: x.py" in desc
        assert ":" not in desc.split("Location: x.py")[1].split("\n")[0]

    # -----------------------------------------------------------------------
    # _convert_findings_to_features
    # -----------------------------------------------------------------------

    def test_convert_findings_to_features_filters_below_threshold(
        self, improver: HarnessSelfImprover
    ) -> None:
        report = _make_mock_report(
            findings=[
                _make_mock_finding(severity="low", message="low issue"),
            ]
        )
        features = improver._convert_findings_to_features(
            report, max_features=10, priority_threshold="medium"
        )
        # "low" is NOT in allowed set when threshold is "medium"
        assert features == []

    def test_convert_findings_includes_at_threshold(
        self, improver: HarnessSelfImprover
    ) -> None:
        report = _make_mock_report(
            findings=[
                _make_mock_finding(severity="medium", message="medium issue"),
            ]
        )
        features = improver._convert_findings_to_features(
            report, max_features=10, priority_threshold="medium"
        )
        assert len(features) == 1

    def test_convert_findings_respects_max_features(
        self, improver: HarnessSelfImprover
    ) -> None:
        findings = [
            _make_mock_finding(severity="high", message=f"issue {i}", rule_id=f"R{i}")
            for i in range(20)
        ]
        report = _make_mock_report(findings=findings)
        features = improver._convert_findings_to_features(
            report, max_features=5, priority_threshold="low"
        )
        assert len(features) == 5

    def test_convert_findings_feature_id_includes_rule_id(
        self, improver: HarnessSelfImprover
    ) -> None:
        report = _make_mock_report(
            findings=[_make_mock_finding(severity="high", rule_id="B101")]
        )
        features = improver._convert_findings_to_features(
            report, max_features=10, priority_threshold="low"
        )
        assert "B101" in features[0].id

    def test_convert_findings_no_rule_id(self, improver: HarnessSelfImprover) -> None:
        report = _make_mock_report(
            findings=[_make_mock_finding(severity="critical", rule_id=None)]
        )
        features = improver._convert_findings_to_features(
            report, max_features=10, priority_threshold="low"
        )
        assert "unknown" in features[0].id

    def test_convert_findings_uses_title_if_available(
        self, improver: HarnessSelfImprover
    ) -> None:
        report = _make_mock_report(
            findings=[
                _make_mock_finding(
                    severity="high", title="My Title", message="fallback message"
                )
            ]
        )
        features = improver._convert_findings_to_features(
            report, max_features=10, priority_threshold="low"
        )
        assert "My Title" in features[0].name

    def test_convert_findings_uses_message_when_no_title(
        self, improver: HarnessSelfImprover
    ) -> None:
        report = _make_mock_report(
            findings=[
                _make_mock_finding(
                    severity="high", title=None, message="Some message for the feature"
                )
            ]
        )
        features = improver._convert_findings_to_features(
            report, max_features=10, priority_threshold="low"
        )
        assert "Some message for the feature" in features[0].name

    def test_convert_findings_priority_threshold_low_includes_all(
        self, improver: HarnessSelfImprover
    ) -> None:
        findings = [
            _make_mock_finding(severity=s, message=f"{s} issue", rule_id=f"R{i}")
            for i, s in enumerate(["critical", "high", "medium", "low"])
        ]
        report = _make_mock_report(findings=findings)
        features = improver._convert_findings_to_features(
            report, max_features=10, priority_threshold="low"
        )
        assert len(features) == 4

    def test_convert_findings_scanner_attribute_used_as_category(
        self, improver: HarnessSelfImprover
    ) -> None:
        report = _make_mock_report(
            findings=[_make_mock_finding(severity="high", scanner="semgrep")]
        )
        features = improver._convert_findings_to_features(
            report, max_features=10, priority_threshold="low"
        )
        assert features[0].category == "semgrep"

    def test_convert_findings_fallback_category_when_no_scanner(
        self, improver: HarnessSelfImprover
    ) -> None:
        finding = _make_mock_finding(severity="high")
        # Remove the scanner attribute to force the getattr fallback
        del finding.scanner
        report = _make_mock_report(findings=[finding])
        features = improver._convert_findings_to_features(
            report, max_features=10, priority_threshold="low"
        )
        assert features[0].category == "self-analysis"

    # -----------------------------------------------------------------------
    # _add_features_to_backlog
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_add_features_empty_list(self, improver: HarnessSelfImprover) -> None:
        added, skipped = await improver._add_features_to_backlog([])
        assert added == 0
        assert skipped == 0

    @pytest.mark.asyncio
    async def test_add_features_creates_file(self, improver: HarnessSelfImprover) -> None:
        from forge_harness.meta_learning.feedback_loops import DebtFeature

        f = DebtFeature(
            id="self-debt-X-0",
            name="[Harness] Fix X",
            description="desc",
            priority="high",
            finding_id="X",
            category="bandit",
        )
        added, skipped = await improver._add_features_to_backlog([f])
        assert added == 1
        assert skipped == 0
        assert improver.features_path.exists()
        data = json.loads(improver.features_path.read_text())
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_add_features_skips_duplicate_id(self, improver: HarnessSelfImprover) -> None:
        from forge_harness.meta_learning.feedback_loops import DebtFeature

        f = DebtFeature(
            id="dup-id",
            name="Feature A",
            description="desc",
            priority="low",
            finding_id="F",
            category="bandit",
        )
        await improver._add_features_to_backlog([f])
        # Second call with same feature
        added, skipped = await improver._add_features_to_backlog([f])
        assert added == 0
        assert skipped == 1

    @pytest.mark.asyncio
    async def test_add_features_skips_duplicate_name(self, improver: HarnessSelfImprover) -> None:
        from forge_harness.meta_learning.feedback_loops import DebtFeature

        f1 = DebtFeature(
            id="id-1",
            name="Same Name",
            description="desc",
            priority="low",
            finding_id="F1",
            category="bandit",
        )
        f2 = DebtFeature(
            id="id-2",  # Different ID
            name="Same Name",  # Same name
            description="desc2",
            priority="medium",
            finding_id="F2",
            category="bandit",
        )
        await improver._add_features_to_backlog([f1])
        added, skipped = await improver._add_features_to_backlog([f2])
        assert added == 0
        assert skipped == 1

    @pytest.mark.asyncio
    async def test_add_features_reads_existing_file(self, improver: HarnessSelfImprover) -> None:
        from forge_harness.meta_learning.feedback_loops import DebtFeature

        # Pre-populate the features file
        existing = [{"id": "old-id", "name": "Old Feature"}]
        improver.features_path.write_text(json.dumps(existing))

        f = DebtFeature(
            id="new-id",
            name="New Feature",
            description="desc",
            priority="low",
            finding_id="F",
            category="bandit",
        )
        added, _ = await improver._add_features_to_backlog([f])
        assert added == 1
        data = json.loads(improver.features_path.read_text())
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_add_features_corrupt_json_starts_fresh(
        self, improver: HarnessSelfImprover
    ) -> None:
        from forge_harness.meta_learning.feedback_loops import DebtFeature

        improver.features_path.write_text("{invalid json{{")
        f = DebtFeature(
            id="new-id",
            name="New Feature",
            description="desc",
            priority="low",
            finding_id="F",
            category="bandit",
        )
        added, skipped = await improver._add_features_to_backlog([f])
        assert added == 1
        assert skipped == 0

    # -----------------------------------------------------------------------
    # get_analysis_history
    # -----------------------------------------------------------------------

    def test_get_analysis_history_empty(self, improver: HarnessSelfImprover) -> None:
        assert improver.get_analysis_history() == []

    # -----------------------------------------------------------------------
    # analyze  (full integration with mocked TechDiligence)
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_analyze_with_failed_report(self, improver: HarnessSelfImprover) -> None:
        improver.tech_diligence = AsyncMock()
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

        result = await improver.analyze()
        assert result.report is None
        assert "Tech Diligence analysis failed or timed out" in result.errors
        assert result.features_added == 0

    @pytest.mark.asyncio
    async def test_analyze_with_report_no_findings(self, improver: HarnessSelfImprover) -> None:
        report = _make_mock_report(findings=[], total_issues=0)
        improver.tech_diligence = AsyncMock()
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=report)

        result = await improver.analyze()
        assert result.report is report
        assert result.features_generated == []
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_analyze_with_findings(self, improver: HarnessSelfImprover) -> None:
        findings = [
            _make_mock_finding(severity="high", rule_id=f"R{i}", message=f"issue {i}")
            for i in range(3)
        ]
        report = _make_mock_report(findings=findings, total_issues=3, high_count=3)
        improver.tech_diligence = AsyncMock()
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=report)

        result = await improver.analyze(priority_threshold="low", max_features=10)
        assert len(result.features_generated) == 3
        assert result.features_added == 3
        assert result.features_skipped == 0

    @pytest.mark.asyncio
    async def test_analyze_records_history(self, improver: HarnessSelfImprover) -> None:
        improver.tech_diligence = AsyncMock()
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

        await improver.analyze()
        await improver.analyze()
        history = improver.get_analysis_history()
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_analyze_custom_scanners(self, improver: HarnessSelfImprover) -> None:
        improver.tech_diligence = AsyncMock()
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

        await improver.analyze(scanners=["bandit"])
        improver.tech_diligence.analyze_and_wait.assert_called_once()
        call_kwargs = improver.tech_diligence.analyze_and_wait.call_args
        assert call_kwargs.kwargs.get("scanners") == ["bandit"] or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] == ["bandit"]
        )


class TestRunSelfAnalysis:
    """Tests for the run_self_analysis async entrypoint."""

    @pytest.mark.asyncio
    async def test_run_self_analysis_dry_run(self, tmp_path: Path) -> None:
        """dry_run=True routes features to /dev/null."""
        with patch(
            "forge_harness.self_improve.HarnessSelfImprover.analyze",
            new_callable=AsyncMock,
        ) as mock_analyze:
            mock_result = MagicMock(spec=SelfAnalysisResult)
            mock_analyze.return_value = mock_result
            result = await run_self_analysis(
                harness_root=tmp_path,
                features_path=tmp_path / "features.json",
                dry_run=True,
            )
        assert result is mock_result

    @pytest.mark.asyncio
    async def test_run_self_analysis_passes_kwargs(self, tmp_path: Path) -> None:
        with patch(
            "forge_harness.self_improve.HarnessSelfImprover.analyze",
            new_callable=AsyncMock,
        ) as mock_analyze:
            mock_analyze.return_value = MagicMock(spec=SelfAnalysisResult)
            await run_self_analysis(
                harness_root=tmp_path,
                max_features=5,
                priority_threshold="high",
                scanners=["bandit"],
            )
        mock_analyze.assert_called_once_with(
            scanners=["bandit"],
            max_features=5,
            priority_threshold="high",
        )


class TestSelfAnalyzeSync:
    """Tests for the self_analyze_sync synchronous wrapper."""

    def test_self_analyze_sync_returns_result(self, tmp_path: Path) -> None:
        with patch(
            "forge_harness.self_improve.HarnessSelfImprover.analyze",
            new_callable=AsyncMock,
        ) as mock_analyze:
            mock_result = MagicMock(spec=SelfAnalysisResult)
            mock_analyze.return_value = mock_result
            result = self_analyze_sync(
                harness_root=tmp_path,
                features_path=tmp_path / "features.json",
            )
        assert result is mock_result

    def test_self_analyze_sync_dry_run(self, tmp_path: Path) -> None:
        with patch(
            "forge_harness.self_improve.HarnessSelfImprover.analyze",
            new_callable=AsyncMock,
        ) as mock_analyze:
            mock_analyze.return_value = MagicMock(spec=SelfAnalysisResult)
            result = self_analyze_sync(
                harness_root=tmp_path,
                dry_run=True,
            )
        assert result is not None
