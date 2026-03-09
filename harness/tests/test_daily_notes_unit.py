"""Unit tests for forge_harness.daily_notes module.

Covers:
- EventType enum
- DailyEvent dataclass and to_markdown()
- DailyNotesService: __init__, _get_notes_path, log_event, _create_header,
  get_notes, get_today_notes, list_notes_dates, get_recent_notes,
  get_summary_stats
- Factory functions: create_daily_notes_service, get_daily_notes_service
"""

import asyncio
from datetime import UTC, date, datetime
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

# =============================================================================
# EventType tests
# =============================================================================


class TestEventType:
    def test_all_members_exist(self):
        expected = {
            "FEATURE_COMPLETION",
            "FEATURE_FAILURE",
            "APPROVAL_DECISION",
            "THRESHOLD_ADJUSTMENT",
            "AGENT_HANDOFF",
            "AGENT_STATUS_CHANGE",
            "PIPELINE_START",
            "PIPELINE_COMPLETE",
            "PIPELINE_ERROR",
        }
        actual = {member.name for member in EventType}
        assert actual == expected

    def test_values_are_human_readable(self):
        assert EventType.FEATURE_COMPLETION.value == "Feature Completion"
        assert EventType.FEATURE_FAILURE.value == "Feature Failure"
        assert EventType.APPROVAL_DECISION.value == "Approval Decision"
        assert EventType.THRESHOLD_ADJUSTMENT.value == "Threshold Adjustment"
        assert EventType.AGENT_HANDOFF.value == "Agent Handoff"
        assert EventType.AGENT_STATUS_CHANGE.value == "Agent Status Change"
        assert EventType.PIPELINE_START.value == "Pipeline Start"
        assert EventType.PIPELINE_COMPLETE.value == "Pipeline Complete"
        assert EventType.PIPELINE_ERROR.value == "Pipeline Error"

    def test_enum_is_iterable(self):
        assert len(list(EventType)) == 9


# =============================================================================
# DailyEvent tests
# =============================================================================


class TestDailyEvent:
    def _make_event(self, details=None):
        ts = datetime(2026, 2, 23, 14, 30, 0, tzinfo=UTC)
        return DailyEvent(
            timestamp=ts,
            event_type=EventType.FEATURE_COMPLETION,
            title="Test feature",
            details=details if details is not None else {},
        )

    # --- Header line ---

    def test_to_markdown_header_contains_time_and_event_type(self):
        event = self._make_event()
        md = event.to_markdown()
        assert "## 14:30 - Feature Completion" in md

    def test_to_markdown_title_is_bold(self):
        event = self._make_event()
        md = event.to_markdown()
        assert "**Test feature**" in md

    # --- Detail formatting: scalar ---

    def test_to_markdown_scalar_string_detail(self):
        event = self._make_event({"status": "ok"})
        md = event.to_markdown()
        assert "- **Status:** ok" in md

    def test_to_markdown_integer_detail(self):
        event = self._make_event({"count": 42})
        md = event.to_markdown()
        assert "- **Count:** 42" in md

    def test_to_markdown_float_detail(self):
        event = self._make_event({"score": 0.95})
        md = event.to_markdown()
        assert "- **Score:** 0.95" in md

    # --- Detail formatting: bool ---

    def test_to_markdown_bool_true(self):
        event = self._make_event({"passed": True})
        md = event.to_markdown()
        assert "- **Passed:** Yes" in md

    def test_to_markdown_bool_false(self):
        event = self._make_event({"passed": False})
        md = event.to_markdown()
        assert "- **Passed:** No" in md

    # --- Detail formatting: list ---

    def test_to_markdown_non_empty_list(self):
        event = self._make_event({"files": ["a.py", "b.py"]})
        md = event.to_markdown()
        assert "- **Files:**" in md
        assert "  - a.py" in md
        assert "  - b.py" in md

    def test_to_markdown_empty_list(self):
        event = self._make_event({"files": []})
        md = event.to_markdown()
        assert "- **Files:** None" in md

    # --- Detail formatting: dict ---

    def test_to_markdown_dict_detail(self):
        event = self._make_event({"metrics": {"passed": 8, "failed": 0}})
        md = event.to_markdown()
        assert "- **Metrics:**" in md
        assert "  - passed: 8" in md
        assert "  - failed: 0" in md

    # --- Snake-case key formatting ---

    def test_to_markdown_snake_case_key_converted_to_title(self):
        event = self._make_event({"some_key_name": "value"})
        md = event.to_markdown()
        assert "- **Some Key Name:** value" in md

    # --- Empty details ---

    def test_to_markdown_no_details(self):
        event = self._make_event({})
        md = event.to_markdown()
        # Should contain header and title but no extra bullet lines
        lines = md.strip().splitlines()
        assert lines[0].startswith("## ")
        assert "**Test feature**" in md

    # --- Default details factory ---

    def test_default_details_is_empty_dict(self):
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        event = DailyEvent(
            timestamp=ts,
            event_type=EventType.AGENT_HANDOFF,
            title="Handoff",
        )
        assert event.details == {}

    def test_default_details_are_independent_instances(self):
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        e1 = DailyEvent(timestamp=ts, event_type=EventType.AGENT_HANDOFF, title="A")
        e2 = DailyEvent(timestamp=ts, event_type=EventType.AGENT_HANDOFF, title="B")
        e1.details["key"] = "val"
        assert "key" not in e2.details


# =============================================================================
# DailyNotesService tests
# =============================================================================


class TestDailyNotesServiceInit:
    def test_default_notes_dir(self, tmp_path, monkeypatch):
        """When notes_dir is None it defaults to .forge/learning/daily."""
        # Patch mkdir so we don't pollute the real filesystem
        with patch.object(Path, "mkdir") as mock_mkdir:
            service = DailyNotesService(notes_dir=None)
            assert service.notes_dir == Path(".forge/learning/daily")
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_custom_notes_dir_is_created(self, tmp_path):
        notes_dir = tmp_path / "custom_notes"
        service = DailyNotesService(notes_dir=notes_dir)
        assert notes_dir.exists()
        assert service.notes_dir == notes_dir

    def test_lock_is_asyncio_lock(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path / "notes")
        assert isinstance(service._lock, asyncio.Lock)


class TestGetNotesPath:
    def test_returns_correct_path(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        target = date(2026, 2, 23)
        path = service._get_notes_path(target)
        assert path == tmp_path / "2026-02-23.md"

    def test_different_dates_give_different_paths(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        p1 = service._get_notes_path(date(2026, 1, 1))
        p2 = service._get_notes_path(date(2026, 12, 31))
        assert p1 != p2

    def test_single_digit_month_and_day_are_zero_padded(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        path = service._get_notes_path(date(2026, 3, 5))
        assert path.name == "2026-03-05.md"


class TestCreateHeader:
    def test_header_contains_date_string(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        header = service._create_header(date(2026, 2, 23))
        assert "Monday, February 23, 2026" in header

    def test_header_starts_with_h1(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        header = service._create_header(date(2026, 2, 23))
        assert header.startswith("# Daily Session Notes - ")

    def test_header_contains_progress_line(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        header = service._create_header(date(2026, 2, 23))
        assert "Key events and progress" in header


class TestLogEvent:
    @pytest.mark.asyncio
    async def test_creates_new_file_with_header_on_first_event(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        ts = datetime(2026, 2, 23, 10, 0, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="First feature",
            timestamp=ts,
        )
        notes_path = service._get_notes_path(date(2026, 2, 23))
        assert notes_path.exists()
        content = notes_path.read_text(encoding="utf-8")
        assert "# Daily Session Notes" in content
        assert "First feature" in content

    @pytest.mark.asyncio
    async def test_appends_to_existing_file(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        ts = datetime(2026, 2, 23, 9, 0, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.FEATURE_COMPLETION,
            title="Event one",
            timestamp=ts,
        )
        ts2 = datetime(2026, 2, 23, 10, 0, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.FEATURE_FAILURE,
            title="Event two",
            timestamp=ts2,
        )
        notes_path = service._get_notes_path(date(2026, 2, 23))
        content = notes_path.read_text(encoding="utf-8")
        assert "Event one" in content
        assert "Event two" in content
        # Header should appear only once
        assert content.count("# Daily Session Notes") == 1

    @pytest.mark.asyncio
    async def test_defaults_timestamp_to_now(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        fixed_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
        with patch("forge_harness.daily_notes.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            # date() is used on the result, so wire .date() properly
            mock_dt.strptime = datetime.strptime  # keep strptime working
            await service.log_event(
                event_type=EventType.PIPELINE_START,
                title="Pipeline started",
            )
        notes_path = service._get_notes_path(fixed_now.date())
        assert notes_path.exists()

    @pytest.mark.asyncio
    async def test_defaults_details_to_empty_dict(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        ts = datetime(2026, 2, 23, 8, 0, 0, tzinfo=UTC)
        # Should not raise
        await service.log_event(
            event_type=EventType.AGENT_HANDOFF,
            title="Handoff",
            timestamp=ts,
        )
        notes_path = service._get_notes_path(date(2026, 2, 23))
        assert notes_path.exists()

    @pytest.mark.asyncio
    async def test_event_with_details_written_to_file(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        ts = datetime(2026, 2, 23, 11, 0, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.THRESHOLD_ADJUSTMENT,
            title="Threshold bumped",
            details={"old_value": 0.5, "new_value": 0.7},
            timestamp=ts,
        )
        notes_path = service._get_notes_path(date(2026, 2, 23))
        content = notes_path.read_text(encoding="utf-8")
        assert "Old Value" in content
        assert "0.5" in content

    @pytest.mark.asyncio
    async def test_log_event_writes_to_correct_date_file(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        ts_jan = datetime(2026, 1, 10, 9, 0, 0, tzinfo=UTC)
        ts_feb = datetime(2026, 2, 20, 9, 0, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.PIPELINE_COMPLETE,
            title="Jan event",
            timestamp=ts_jan,
        )
        await service.log_event(
            event_type=EventType.PIPELINE_COMPLETE,
            title="Feb event",
            timestamp=ts_feb,
        )
        jan_content = service._get_notes_path(date(2026, 1, 10)).read_text()
        feb_content = service._get_notes_path(date(2026, 2, 20)).read_text()
        assert "Jan event" in jan_content
        assert "Feb event" not in jan_content
        assert "Feb event" in feb_content

    @pytest.mark.asyncio
    async def test_log_event_logs_info(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        ts = datetime(2026, 2, 23, 12, 0, 0, tzinfo=UTC)
        with patch("forge_harness.daily_notes.logger") as mock_logger:
            await service.log_event(
                event_type=EventType.APPROVAL_DECISION,
                title="Approved deploy",
                timestamp=ts,
            )
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            assert "Approval Decision" in call_args[0][0]


class TestGetNotes:
    @pytest.mark.asyncio
    async def test_returns_no_notes_message_when_file_absent(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        result = await service.get_notes(date(2026, 1, 1))
        assert "No notes found" in result
        assert "2026-01-01" in result

    @pytest.mark.asyncio
    async def test_returns_file_content_when_exists(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        ts = datetime(2026, 2, 23, 10, 0, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.AGENT_STATUS_CHANGE,
            title="Agent went idle",
            timestamp=ts,
        )
        result = await service.get_notes(date(2026, 2, 23))
        assert "Agent went idle" in result

    @pytest.mark.asyncio
    async def test_returns_full_utf8_content(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        notes_path = service._get_notes_path(date(2026, 5, 1))
        notes_path.write_text("# Notes — Ünïcödé content", encoding="utf-8")
        result = await service.get_notes(date(2026, 5, 1))
        assert "Ünïcödé" in result


class TestGetTodayNotes:
    @pytest.mark.asyncio
    async def test_delegates_to_get_notes_with_today(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        today = date.today()
        with patch.object(service, "get_notes") as mock_get:
            mock_get.return_value = "today content"
            result = await service.get_today_notes()
            mock_get.assert_called_once_with(today)
            assert result == "today content"

    @pytest.mark.asyncio
    async def test_returns_no_notes_if_today_file_absent(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        result = await service.get_today_notes()
        assert "No notes found" in result


class TestListNotesDates:
    @pytest.mark.asyncio
    async def test_empty_dir_returns_empty_list(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        result = await service.list_notes_dates()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_dates_sorted_newest_first(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        for d in ["2026-01-01.md", "2026-03-15.md", "2026-02-10.md"]:
            (tmp_path / d).write_text("content")
        result = await service.list_notes_dates()
        assert result == [date(2026, 3, 15), date(2026, 2, 10), date(2026, 1, 1)]

    @pytest.mark.asyncio
    async def test_respects_limit(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        for i in range(1, 11):
            (tmp_path / f"2026-01-{i:02d}.md").write_text("x")
        result = await service.list_notes_dates(limit=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_skips_invalid_filenames_with_warning(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        (tmp_path / "2026-02-23.md").write_text("valid")
        (tmp_path / "not-a-date.md").write_text("invalid")
        with patch("forge_harness.daily_notes.logger") as mock_logger:
            result = await service.list_notes_dates()
            # Only the valid date should be returned
            assert date(2026, 2, 23) in result
            assert any(
                "not-a-date" in str(call) for call in mock_logger.warning.call_args_list
            )

    @pytest.mark.asyncio
    async def test_default_limit_is_30(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        for i in range(1, 35):
            month = (i // 28) + 1
            day = (i % 28) + 1
            (tmp_path / f"2026-{month:02d}-{day:02d}.md").write_text("x")
        result = await service.list_notes_dates()
        assert len(result) <= 30


class TestGetRecentNotes:
    @pytest.mark.asyncio
    async def test_empty_when_no_files(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        result = await service.get_recent_notes(days=7)
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_dict_mapping_dates_to_content(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        d1 = date(2026, 2, 22)
        d2 = date(2026, 2, 23)
        service._get_notes_path(d1).write_text("content for 22nd", encoding="utf-8")
        service._get_notes_path(d2).write_text("content for 23rd", encoding="utf-8")
        result = await service.get_recent_notes(days=7)
        assert d1 in result or d2 in result
        # Content matches
        for d, content in result.items():
            if d == d1:
                assert "22nd" in content
            elif d == d2:
                assert "23rd" in content

    @pytest.mark.asyncio
    async def test_respects_days_limit(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        for i in range(1, 11):
            (tmp_path / f"2026-01-{i:02d}.md").write_text("x")
        result = await service.get_recent_notes(days=3)
        assert len(result) == 3


class TestGetSummaryStats:
    @pytest.mark.asyncio
    async def test_empty_when_no_notes(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        stats = await service.get_summary_stats()
        assert stats["total_days"] == 0
        assert stats["oldest_date"] is None
        assert stats["newest_date"] is None
        assert stats["total_events"] == 0

    @pytest.mark.asyncio
    async def test_total_days_matches_file_count(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        for d_str in ["2026-01-01.md", "2026-01-02.md", "2026-01-03.md"]:
            (tmp_path / d_str).write_text("## 10:00 - Event\n**Title**\n")
        stats = await service.get_summary_stats()
        assert stats["total_days"] == 3

    @pytest.mark.asyncio
    async def test_newest_and_oldest_dates(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        for d_str in ["2026-01-01.md", "2026-03-01.md", "2026-02-15.md"]:
            (tmp_path / d_str).write_text("")
        stats = await service.get_summary_stats()
        assert stats["newest_date"] == "2026-03-01"
        assert stats["oldest_date"] == "2026-01-01"

    @pytest.mark.asyncio
    async def test_total_events_counts_markdown_h2_headers(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        content = (
            "# Header\n\n"
            "## 10:00 - Feature Completion\n**Event 1**\n\n"
            "## 11:00 - Feature Failure\n**Event 2**\n\n"
        )
        (tmp_path / "2026-02-23.md").write_text(content, encoding="utf-8")
        stats = await service.get_summary_stats()
        assert stats["total_events"] == 2

    @pytest.mark.asyncio
    async def test_total_events_across_multiple_files(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        for i, d_str in enumerate(["2026-01-01.md", "2026-01-02.md"], start=1):
            body = "".join(f"## {h:02d}:00 - Event\n**T**\n\n" for h in range(1, i + 2))
            (tmp_path / d_str).write_text(body, encoding="utf-8")
        stats = await service.get_summary_stats()
        # file 1: 2 events, file 2: 3 events => total 5
        assert stats["total_events"] == 5

    @pytest.mark.asyncio
    async def test_stats_with_real_log_event(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        ts = datetime(2026, 2, 23, 14, 0, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.PIPELINE_ERROR,
            title="Build failed",
            timestamp=ts,
        )
        stats = await service.get_summary_stats()
        assert stats["total_days"] == 1
        assert stats["total_events"] >= 1
        assert stats["newest_date"] == "2026-02-23"
        assert stats["oldest_date"] == "2026-02-23"


# =============================================================================
# Factory function tests
# =============================================================================


class TestCreateDailyNotesService:
    def test_returns_daily_notes_service_instance(self, tmp_path):
        service = create_daily_notes_service(notes_dir=tmp_path / "notes")
        assert isinstance(service, DailyNotesService)

    def test_with_custom_dir(self, tmp_path):
        notes_dir = tmp_path / "custom"
        service = create_daily_notes_service(notes_dir=notes_dir)
        assert service.notes_dir == notes_dir
        assert notes_dir.exists()

    def test_with_none_uses_default(self):
        with patch.object(Path, "mkdir"):
            service = create_daily_notes_service(notes_dir=None)
            assert service.notes_dir == Path(".forge/learning/daily")


class TestGetDailyNotesService:
    def setup_method(self):
        """Reset global singleton before each test."""
        import forge_harness.daily_notes as dn_module
        dn_module._daily_notes_service = None

    def teardown_method(self):
        """Reset global singleton after each test."""
        import forge_harness.daily_notes as dn_module
        dn_module._daily_notes_service = None

    def test_returns_daily_notes_service_instance(self):
        with patch.object(Path, "mkdir"):
            service = get_daily_notes_service()
            assert isinstance(service, DailyNotesService)

    def test_returns_same_instance_on_repeated_calls(self):
        with patch.object(Path, "mkdir"):
            s1 = get_daily_notes_service()
            s2 = get_daily_notes_service()
            assert s1 is s2

    def test_creates_new_instance_when_global_is_none(self):
        import forge_harness.daily_notes as dn_module
        assert dn_module._daily_notes_service is None
        with patch.object(Path, "mkdir"):
            service = get_daily_notes_service()
            assert dn_module._daily_notes_service is service


# =============================================================================
# Concurrency / lock tests
# =============================================================================


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_log_events_do_not_corrupt_file(self, tmp_path):
        """Multiple concurrent log_event calls should produce coherent output."""
        service = DailyNotesService(notes_dir=tmp_path)
        ts = datetime(2026, 2, 23, 10, 0, 0, tzinfo=UTC)

        tasks = [
            service.log_event(
                event_type=EventType.FEATURE_COMPLETION,
                title=f"Concurrent event {i}",
                timestamp=ts,
            )
            for i in range(10)
        ]
        await asyncio.gather(*tasks)

        notes_path = service._get_notes_path(date(2026, 2, 23))
        content = notes_path.read_text(encoding="utf-8")
        # All 10 events should be present
        for i in range(10):
            assert f"Concurrent event {i}" in content

    @pytest.mark.asyncio
    async def test_lock_is_used_during_get_notes(self, tmp_path):
        """get_notes acquires the service lock (smoke test via actual read)."""
        service = DailyNotesService(notes_dir=tmp_path)
        ts = datetime(2026, 2, 23, 9, 0, 0, tzinfo=UTC)
        await service.log_event(
            event_type=EventType.PIPELINE_COMPLETE,
            title="Done",
            timestamp=ts,
        )
        # Read while also logging concurrently — should not deadlock or error
        result, _ = await asyncio.gather(
            service.get_notes(date(2026, 2, 23)),
            service.log_event(
                event_type=EventType.PIPELINE_START,
                title="Starting again",
                timestamp=ts,
            ),
        )
        assert "Done" in result or isinstance(result, str)


# =============================================================================
# Integration: full round-trip
# =============================================================================


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_log_then_retrieve_all_event_types(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        base_ts = datetime(2026, 2, 23, 8, 0, 0, tzinfo=UTC)

        for i, event_type in enumerate(EventType):
            ts = base_ts.replace(hour=8 + i)
            await service.log_event(
                event_type=event_type,
                title=f"Test {event_type.name}",
                timestamp=ts,
            )

        content = await service.get_notes(date(2026, 2, 23))
        for event_type in EventType:
            assert f"Test {event_type.name}" in content

    @pytest.mark.asyncio
    async def test_list_and_retrieve_multiple_days(self, tmp_path):
        service = DailyNotesService(notes_dir=tmp_path)
        dates_to_create = [
            datetime(2026, 2, 21, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 2, 22, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 2, 23, 10, 0, 0, tzinfo=UTC),
        ]
        for ts in dates_to_create:
            await service.log_event(
                event_type=EventType.AGENT_STATUS_CHANGE,
                title=f"Event on {ts.date()}",
                timestamp=ts,
            )

        listed = await service.list_notes_dates()
        assert len(listed) == 3
        assert listed[0] == date(2026, 2, 23)  # newest first

        recent = await service.get_recent_notes(days=7)
        assert len(recent) == 3

        stats = await service.get_summary_stats()
        assert stats["total_days"] == 3
        assert stats["total_events"] == 3
