"""Tests for daily_notes.py - Daily Session Notes Service"""
import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.daily_notes import (
    DailyEvent,
    DailyNotesService,
    EventType,
    create_daily_notes_service,
    get_daily_notes_service,
)


class TestDailyEvent:
    """Tests for DailyEvent dataclass."""

    @pytest.fixture
    def sample_event(self):
        """Create a sample daily event."""
        return DailyEvent(
            timestamp=datetime(2026, 2, 16, 14, 30, tzinfo=UTC),
            event_type=EventType.FEATURE_COMPLETION,
            title="Feature CC-P1-001 Complete",
            details={"tests": "8/8 passing", "files": ["src/hooks/useDrag.ts"]},
        )

    def test_initialization(self, sample_event):
        """Should initialize with provided values."""
        assert sample_event.timestamp.hour == 14
        assert sample_event.timestamp.minute == 30
        assert sample_event.event_type == EventType.FEATURE_COMPLETION
        assert sample_event.title == "Feature CC-P1-001 Complete"
        assert sample_event.details["tests"] == "8/8 passing"

    def test_to_markdown(self, sample_event):
        """Should format as markdown correctly."""
        markdown = sample_event.to_markdown()

        assert "## 14:30 - Feature Completion" in markdown
        assert "**Feature CC-P1-001 Complete**" in markdown
        assert "**Tests:**" in markdown
        assert "**Files:**" in markdown
        assert "- src/hooks/useDrag.ts" in markdown

    def test_to_markdown_empty_list(self, sample_event):
        """Should handle empty list in details."""
        sample_event.details["empty_list"] = []
        markdown = sample_event.to_markdown()

        assert "**Empty List:** None" in markdown

    def test_to_markdown_dict_value(self, sample_event):
        """Should format dict values correctly."""
        sample_event.details["config"] = {"key1": "value1", "key2": "value2"}
        markdown = sample_event.to_markdown()

        assert "**Config:**" in markdown
        assert "  - key1: value1" in markdown

    def test_to_markdown_bool_value(self, sample_event):
        """Should format bool values correctly."""
        sample_event.details["success"] = True
        sample_event.details["failed"] = False
        markdown = sample_event.to_markdown()

        assert "**Success:** Yes" in markdown
        assert "**Failed:** No" in markdown


class TestDailyNotesService:
    """Tests for DailyNotesService."""

    @pytest.fixture
    async def service(self, tmp_path):
        """Create service with temp directory."""
        return DailyNotesService(notes_dir=tmp_path / "daily")

    @pytest.mark.asyncio
    async def test_initialization_creates_directory(self, tmp_path):
        """Should create notes directory on init."""
        notes_dir = tmp_path / "new_daily"
        service = DailyNotesService(notes_dir=notes_dir)

        assert notes_dir.exists()

    @pytest.mark.asyncio
    async def test_default_notes_directory(self):
        """Should use default directory when none provided."""
        service = DailyNotesService()

        assert service.notes_dir == Path(".forge/learning/daily")

    @pytest.mark.asyncio
    async def test_log_event_creates_new_file(self, tmp_path):
        """Should create new notes file for first event."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Test Feature",
            details={"status": "complete"},
        )

        today = date.today()
        notes_file = tmp_path / "daily" / f"{today.isoformat()}.md"
        assert notes_file.exists()

    @pytest.mark.asyncio
    async def test_log_event_appends_to_existing(self, tmp_path):
        """Should append to existing notes file."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION, title="Feature 1", details={}
        )
        await service.log_event(
            event_type=EventType.FEATURE_FAILURE, title="Feature 2", details={}
        )

        content = await service.get_today_notes()
        assert "Feature Completion" in content
        assert "Feature Failure" in content

    @pytest.mark.asyncio
    async def test_log_event_with_custom_timestamp(self, tmp_path):
        """Should use provided timestamp."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        custom_time = datetime(2026, 2, 15, 10, 30, tzinfo=UTC)

        await service.log_event(
            event_type=EventType.PIPELINE_START,
            title="Pipeline Started",
            details={},
            timestamp=custom_time,
        )

        content = await service.get_notes(date(2026, 2, 15))
        assert "10:30" in content

    @pytest.mark.asyncio
    async def test_get_notes_existing_file(self, tmp_path):
        """Should read existing notes file."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        target_date = date(2026, 2, 15)

        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Test",
            details={},
            timestamp=datetime(2026, 2, 15, 12, 0, tzinfo=UTC),
        )

        content = await service.get_notes(target_date)

        assert "Daily Session Notes" in content
        assert "Test" in content

    @pytest.mark.asyncio
    async def test_get_notes_nonexistent(self, tmp_path):
        """Should return message for non-existent date."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        target_date = date(2025, 1, 1)

        content = await service.get_notes(target_date)

        assert "No notes found" in content
        assert "2025-01-01" in content

    @pytest.mark.asyncio
    async def test_get_today_notes(self, tmp_path):
        """Should get notes for today."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION, title="Today's Work", details={}
        )

        content = await service.get_today_notes()

        assert "Today's Work" in content

    @pytest.mark.asyncio
    async def test_list_notes_dates(self, tmp_path):
        """Should list dates with notes files."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        # Create notes for different dates
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Day 1",
            details={},
            timestamp=datetime(2026, 2, 14, 12, 0, tzinfo=UTC),
        )
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Day 2",
            details={},
            timestamp=datetime(2026, 2, 15, 12, 0, tzinfo=UTC),
        )

        dates = await service.list_notes_dates()

        assert len(dates) == 2
        assert dates[0] == date(2026, 2, 15)  # Most recent first
        assert dates[1] == date(2026, 2, 14)

    @pytest.mark.asyncio
    async def test_list_notes_dates_with_limit(self, tmp_path):
        """Should respect limit parameter."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        # Create notes for 5 different dates
        for i in range(5):
            await service.log_event(
                event_type=EventType.FEATURE_COMPLETION,
                title=f"Day {i}",
                details={},
                timestamp=datetime(2026, 2, 10 + i, 12, 0, tzinfo=UTC),
            )

        dates = await service.list_notes_dates(limit=3)

        assert len(dates) == 3

    @pytest.mark.asyncio
    async def test_list_notes_dates_invalid_filename(self, tmp_path):
        """Should skip invalid filenames gracefully."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        # Create an invalid file
        (tmp_path / "daily" / "invalid_file.md").write_text("invalid")

        dates = await service.list_notes_dates()

        assert len(dates) == 0  # Should skip invalid file

    @pytest.mark.asyncio
    async def test_get_recent_notes(self, tmp_path):
        """Should get notes from recent days."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Recent 1",
            details={},
            timestamp=datetime(2026, 2, 15, 12, 0, tzinfo=UTC),
        )
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Recent 2",
            details={},
            timestamp=datetime(2026, 2, 14, 12, 0, tzinfo=UTC),
        )

        recent = await service.get_recent_notes(days=7)

        assert len(recent) == 2
        assert date(2026, 2, 15) in recent
        assert date(2026, 2, 14) in recent

    @pytest.mark.asyncio
    async def test_get_summary_stats(self, tmp_path):
        """Should calculate summary statistics."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        # Create notes with multiple events
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Event 1",
            details={},
            timestamp=datetime(2026, 2, 15, 12, 0, tzinfo=UTC),
        )
        await service.log_event(
            event_type=EventType.FEATURE_FAILURE,
            title="Event 2",
            details={},
            timestamp=datetime(2026, 2, 15, 13, 0, tzinfo=UTC),
        )
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Event 3",
            details={},
            timestamp=datetime(2026, 2, 14, 12, 0, tzinfo=UTC),
        )

        stats = await service.get_summary_stats()

        assert stats["total_days"] == 2
        assert stats["total_events"] == 3
        assert stats["newest_date"] == "2026-02-15"
        assert stats["oldest_date"] == "2026-02-14"

    @pytest.mark.asyncio
    async def test_get_summary_stats_empty(self, tmp_path):
        """Should handle empty directory."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        stats = await service.get_summary_stats()

        assert stats["total_days"] == 0
        assert stats["total_events"] == 0
        assert stats["newest_date"] is None
        assert stats["oldest_date"] is None

    @pytest.mark.asyncio
    async def test_concurrent_access(self, tmp_path):
        """Should handle concurrent writes safely."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")

        async def log_event(i):
            await service.log_event(
                event_type=EventType.FEATURE_COMPLETION,
                title=f"Event {i}",
                details={},
            )

        # Log 10 events concurrently
        await asyncio.gather(*[log_event(i) for i in range(10)])

        content = await service.get_today_notes()
        # Should have all 10 events
        assert content.count("Event ") == 10


class TestCreateDailyNotesService:
    """Tests for create_daily_notes_service factory."""

    def test_create_with_custom_dir(self, tmp_path):
        """Should create with custom directory."""
        service = create_daily_notes_service(notes_dir=tmp_path / "custom")

        assert service.notes_dir == tmp_path / "custom"

    def test_create_with_default_dir(self):
        """Should create with default directory."""
        service = create_daily_notes_service()

        assert service.notes_dir == Path(".forge/learning/daily")


class TestGetDailyNotesService:
    """Tests for get_daily_notes_service singleton."""

    def test_singleton_instance(self):
        """Should return same instance on multiple calls."""
        service1 = get_daily_notes_service()
        service2 = get_daily_notes_service()

        assert service1 is service2

    def test_singleton_is_configured(self):
        """Should return configured service."""
        service = get_daily_notes_service()

        assert isinstance(service, DailyNotesService)


# =============================================================================
# Additional EventType Tests
# =============================================================================


class TestEventTypeEnum:
    """Extended tests for EventType enum values and membership."""

    def test_event_type_count(self):
        """EventType has exactly 9 members."""
        assert len(EventType) == 9

    def test_all_event_type_values(self):
        """All EventType members have expected string values."""
        expected = {
            EventType.FEATURE_COMPLETION: "Feature Completion",
            EventType.FEATURE_FAILURE: "Feature Failure",
            EventType.APPROVAL_DECISION: "Approval Decision",
            EventType.THRESHOLD_ADJUSTMENT: "Threshold Adjustment",
            EventType.AGENT_HANDOFF: "Agent Handoff",
            EventType.AGENT_STATUS_CHANGE: "Agent Status Change",
            EventType.PIPELINE_START: "Pipeline Start",
            EventType.PIPELINE_COMPLETE: "Pipeline Complete",
            EventType.PIPELINE_ERROR: "Pipeline Error",
        }
        for event_type, value in expected.items():
            assert event_type.value == value

    def test_event_type_lookup_by_value(self):
        """EventType can be looked up by its string value."""
        result = EventType("Agent Handoff")
        assert result is EventType.AGENT_HANDOFF

    def test_event_type_is_iterable(self):
        """All EventType members are accessible via iteration."""
        members = list(EventType)
        assert EventType.FEATURE_COMPLETION in members
        assert EventType.PIPELINE_ERROR in members


# =============================================================================
# DailyEvent Extended Tests
# =============================================================================


class TestDailyEventExtended:
    """Additional edge case tests for DailyEvent.to_markdown."""

    def test_to_markdown_midnight_timestamp(self):
        """to_markdown formats a midnight timestamp as 00:00."""
        event = DailyEvent(
            timestamp=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            event_type=EventType.PIPELINE_START,
            title="Midnight Start",
            details={},
        )
        md = event.to_markdown()
        assert "## 00:00 - Pipeline Start" in md

    def test_to_markdown_end_of_day_timestamp(self):
        """to_markdown formats 23:59 correctly."""
        event = DailyEvent(
            timestamp=datetime(2026, 12, 31, 23, 59, 0, tzinfo=UTC),
            event_type=EventType.PIPELINE_COMPLETE,
            title="Year End",
            details={},
        )
        md = event.to_markdown()
        assert "## 23:59 - Pipeline Complete" in md

    def test_to_markdown_integer_detail_value(self):
        """to_markdown handles integer detail values without error."""
        event = DailyEvent(
            timestamp=datetime(2026, 2, 22, 10, 0, tzinfo=UTC),
            event_type=EventType.THRESHOLD_ADJUSTMENT,
            title="Raise Threshold",
            details={"retries": 5},
        )
        md = event.to_markdown()
        assert "- **Retries:** 5" in md

    def test_to_markdown_snake_case_key_converted(self):
        """to_markdown converts snake_case keys to Title Case for display."""
        event = DailyEvent(
            timestamp=datetime(2026, 2, 22, 10, 0, tzinfo=UTC),
            event_type=EventType.FEATURE_FAILURE,
            title="Failed",
            details={"error_message": "connection_refused"},
        )
        md = event.to_markdown()
        assert "**Error Message:**" in md

    def test_to_markdown_multiple_details_all_rendered(self):
        """to_markdown renders all detail keys when multiple are present."""
        event = DailyEvent(
            timestamp=datetime(2026, 2, 22, 10, 0, tzinfo=UTC),
            event_type=EventType.AGENT_STATUS_CHANGE,
            title="Agent Up",
            details={"agent": "kimi", "status": "online", "score": 99},
        )
        md = event.to_markdown()
        assert "**Agent:**" in md
        assert "**Status:**" in md
        assert "**Score:**" in md

    def test_to_markdown_no_details_minimal_output(self):
        """to_markdown with no details has header line, title line, and no detail bullets."""
        event = DailyEvent(
            timestamp=datetime(2026, 2, 22, 8, 0, tzinfo=UTC),
            event_type=EventType.AGENT_HANDOFF,
            title="Handoff",
            details={},
        )
        md = event.to_markdown()
        lines = [l for l in md.splitlines() if l]  # non-empty lines only
        assert len(lines) == 2
        assert lines[0].startswith("## ")
        assert lines[1].startswith("**")


# =============================================================================
# DailyNotesService _get_notes_path Tests
# =============================================================================


class TestGetNotesPath:
    """Tests for DailyNotesService._get_notes_path."""

    def test_path_correct_iso_format(self, tmp_path):
        """_get_notes_path produces YYYY-MM-DD.md filename."""
        service = DailyNotesService(notes_dir=tmp_path)
        result = service._get_notes_path(date(2026, 2, 22))
        assert result == tmp_path / "2026-02-22.md"

    def test_path_zero_pads_month_and_day(self, tmp_path):
        """_get_notes_path zero-pads single-digit month and day."""
        service = DailyNotesService(notes_dir=tmp_path)
        result = service._get_notes_path(date(2026, 1, 5))
        assert result.name == "2026-01-05.md"

    def test_path_december_31(self, tmp_path):
        """_get_notes_path handles December 31."""
        service = DailyNotesService(notes_dir=tmp_path)
        result = service._get_notes_path(date(2025, 12, 31))
        assert result.name == "2025-12-31.md"

    def test_path_is_inside_notes_dir(self, tmp_path):
        """_get_notes_path returns a path inside notes_dir."""
        service = DailyNotesService(notes_dir=tmp_path)
        result = service._get_notes_path(date(2026, 6, 15))
        assert result.parent == tmp_path


# =============================================================================
# _create_header Tests
# =============================================================================


class TestCreateHeader:
    """Tests for DailyNotesService._create_header."""

    def test_header_starts_with_h1(self, tmp_path):
        """_create_header returns a string starting with '# '."""
        service = DailyNotesService(notes_dir=tmp_path)
        header = service._create_header(date(2026, 2, 22))
        assert header.startswith("# Daily Session Notes - ")

    def test_header_contains_full_date(self, tmp_path):
        """_create_header includes the full human-readable date."""
        service = DailyNotesService(notes_dir=tmp_path)
        header = service._create_header(date(2026, 2, 22))
        # February 22, 2026 is a Sunday
        assert "Sunday, February 22, 2026" in header

    def test_header_contains_key_events_line(self, tmp_path):
        """_create_header includes a contextual 'Key events and progress' line."""
        service = DailyNotesService(notes_dir=tmp_path)
        header = service._create_header(date(2026, 2, 22))
        assert "Key events and progress" in header

    def test_header_different_dates(self, tmp_path):
        """_create_header uses the correct date string for any given date."""
        service = DailyNotesService(notes_dir=tmp_path)
        header = service._create_header(date(2026, 1, 1))
        assert "January 01, 2026" in header


# =============================================================================
# log_event edge cases
# =============================================================================


class TestLogEventEdgeCases:
    """Edge case tests for log_event."""

    @pytest.mark.asyncio
    async def test_log_event_header_appears_once_for_multiple_events(self, tmp_path):
        """log_event writes header only once even with many events on same day."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        ts_base = datetime(2026, 2, 22, 8, 0, tzinfo=UTC)
        for i in range(5):
            await service.log_event(
                event_type=EventType.FEATURE_COMPLETION,
                title=f"Event {i}",
                details={},
                timestamp=datetime(2026, 2, 22, 8 + i, 0, tzinfo=UTC),
            )
        content = await service.get_notes(date(2026, 2, 22))
        assert content.count("# Daily Session Notes") == 1

    @pytest.mark.asyncio
    async def test_log_event_creates_separate_files_per_day(self, tmp_path):
        """log_event creates one file per unique date."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        await service.log_event(
            EventType.PIPELINE_START,
            "Start Day 1",
            timestamp=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
        )
        await service.log_event(
            EventType.PIPELINE_COMPLETE,
            "End Day 2",
            timestamp=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
        )
        assert (tmp_path / "daily" / "2026-02-20.md").exists()
        assert (tmp_path / "daily" / "2026-02-21.md").exists()

    @pytest.mark.asyncio
    async def test_log_event_none_details_defaults_to_empty(self, tmp_path):
        """log_event treats None details as empty dict (no KeyError)."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        # Must not raise
        await service.log_event(
            event_type=EventType.APPROVAL_DECISION,
            title="Approved",
            details=None,
            timestamp=datetime(2026, 2, 22, 12, 0, tzinfo=UTC),
        )
        content = await service.get_notes(date(2026, 2, 22))
        assert "**Approved**" in content

    @pytest.mark.asyncio
    async def test_log_event_unicode_content(self, tmp_path):
        """log_event handles unicode characters in title and details."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Feature: データ処理 & Ünïcödé",
            details={"note": "Résumé complet"},
            timestamp=datetime(2026, 2, 22, 9, 0, tzinfo=UTC),
        )
        content = await service.get_notes(date(2026, 2, 22))
        assert "データ処理" in content
        assert "Ünïcödé" in content

    @pytest.mark.asyncio
    async def test_log_event_january_first(self, tmp_path):
        """log_event correctly handles January 1st boundary."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        await service.log_event(
            event_type=EventType.PIPELINE_START,
            title="New Year Start",
            timestamp=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
        assert (tmp_path / "daily" / "2026-01-01.md").exists()

    @pytest.mark.asyncio
    async def test_log_event_december_31(self, tmp_path):
        """log_event correctly handles December 31st boundary."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        await service.log_event(
            event_type=EventType.PIPELINE_COMPLETE,
            title="Year End",
            timestamp=datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC),
        )
        assert (tmp_path / "daily" / "2025-12-31.md").exists()

    @pytest.mark.asyncio
    async def test_log_event_with_list_details(self, tmp_path):
        """log_event stores list details and renders them as sub-bullets."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Multi-file Feature",
            details={"files": ["app.py", "models.py", "views.py"]},
            timestamp=datetime(2026, 2, 22, 10, 0, tzinfo=UTC),
        )
        content = await service.get_notes(date(2026, 2, 22))
        assert "app.py" in content
        assert "models.py" in content
        assert "views.py" in content

    @pytest.mark.asyncio
    async def test_log_event_uses_utcnow_when_no_timestamp(self, tmp_path):
        """log_event defaults to current UTC time when timestamp is omitted."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        fixed_now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        with patch("forge_harness.daily_notes.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            await service.log_event(
                event_type=EventType.PIPELINE_START,
                title="Auto Time",
            )
        assert (tmp_path / "daily" / "2026-03-15.md").exists()


# =============================================================================
# get_notes edge cases
# =============================================================================


class TestGetNotesEdgeCases:
    """Edge cases for get_notes and get_today_notes."""

    @pytest.mark.asyncio
    async def test_get_notes_returns_complete_content(self, tmp_path):
        """get_notes returns full file content including all events."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        for i in range(3):
            await service.log_event(
                EventType.FEATURE_COMPLETION,
                f"Feature {i}",
                timestamp=datetime(2026, 2, 22, 10 + i, 0, tzinfo=UTC),
            )
        content = await service.get_notes(date(2026, 2, 22))
        for i in range(3):
            assert f"Feature {i}" in content

    @pytest.mark.asyncio
    async def test_get_notes_wrong_date_returns_not_found(self, tmp_path):
        """get_notes returns 'No notes found' for a date with no matching file."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        await service.log_event(
            EventType.FEATURE_COMPLETION,
            "Day A",
            timestamp=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
        )
        result = await service.get_notes(date(2026, 2, 21))
        assert "No notes found" in result

    @pytest.mark.asyncio
    async def test_get_today_notes_mocked_date(self, tmp_path):
        """get_today_notes reads the file for the mocked today date."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        fixed_today = date(2026, 2, 22)
        notes_file = tmp_path / "daily" / "2026-02-22.md"
        notes_file.write_text("Mocked today content", encoding="utf-8")
        with patch("forge_harness.daily_notes.date") as mock_date:
            mock_date.today.return_value = fixed_today
            result = await service.get_today_notes()
        assert result == "Mocked today content"


# =============================================================================
# list_notes_dates extended edge cases
# =============================================================================


class TestListNotesDatesEdgeCases:
    """Extended edge case tests for list_notes_dates."""

    @pytest.mark.asyncio
    async def test_only_md_files_returned(self, tmp_path):
        """list_notes_dates ignores non-.md files (e.g. .txt, .json)."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        (tmp_path / "daily" / "2026-02-22.md").write_text("valid")
        (tmp_path / "daily" / "2026-02-21.txt").write_text("ignored")
        (tmp_path / "daily" / "2026-02-20.json").write_text("{}")
        dates = await service.list_notes_dates()
        assert dates == [date(2026, 2, 22)]

    @pytest.mark.asyncio
    async def test_default_limit_is_30(self, tmp_path):
        """list_notes_dates default limit is 30."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        # Use two months to get 35 valid files: Jan 1-31 + Feb 1-4
        for i in range(1, 32):
            (tmp_path / "daily" / f"2026-01-{i:02d}.md").write_text("x")
        for i in range(1, 5):
            (tmp_path / "daily" / f"2026-02-{i:02d}.md").write_text("x")
        result = await service.list_notes_dates()
        assert len(result) == 30

    @pytest.mark.asyncio
    async def test_partially_invalid_filenames_skipped(self, tmp_path):
        """list_notes_dates skips files that fail date parsing but keeps valid ones."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        (tmp_path / "daily" / "2026-02-22.md").write_text("valid")
        (tmp_path / "daily" / "notes.md").write_text("no date")
        (tmp_path / "daily" / "README.md").write_text("readme")
        dates = await service.list_notes_dates()
        assert len(dates) == 1
        assert dates[0] == date(2026, 2, 22)


# =============================================================================
# get_summary_stats edge cases
# =============================================================================


class TestGetSummaryStatsEdgeCases:
    """Edge case tests for get_summary_stats."""

    @pytest.mark.asyncio
    async def test_stats_file_with_no_events(self, tmp_path):
        """get_summary_stats counts 0 events for a file with no '## ' headers."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        (tmp_path / "daily" / "2026-02-22.md").write_text(
            "# Header Only\nNo event sections.\n"
        )
        stats = await service.get_summary_stats()
        assert stats["total_events"] == 0
        assert stats["total_days"] == 1

    @pytest.mark.asyncio
    async def test_stats_single_day_oldest_equals_newest(self, tmp_path):
        """get_summary_stats reports identical oldest and newest for a single day."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        await service.log_event(
            EventType.FEATURE_COMPLETION,
            "Only Day",
            timestamp=datetime(2026, 2, 22, 10, 0, tzinfo=UTC),
        )
        stats = await service.get_summary_stats()
        assert stats["oldest_date"] == stats["newest_date"] == "2026-02-22"

    @pytest.mark.asyncio
    async def test_stats_counts_all_events_across_days(self, tmp_path):
        """get_summary_stats aggregates total events across multiple days."""
        service = DailyNotesService(notes_dir=tmp_path / "daily")
        # 2 events on day 1, 3 events on day 2
        for event_hour in [10, 11]:
            await service.log_event(
                EventType.FEATURE_COMPLETION,
                "Day1 Event",
                timestamp=datetime(2026, 2, 20, event_hour, 0, tzinfo=UTC),
            )
        for event_hour in [9, 10, 11]:
            await service.log_event(
                EventType.PIPELINE_START,
                "Day2 Event",
                timestamp=datetime(2026, 2, 21, event_hour, 0, tzinfo=UTC),
            )
        stats = await service.get_summary_stats()
        assert stats["total_days"] == 2
        assert stats["total_events"] == 5
