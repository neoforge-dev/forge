"""
Tests for Memory Flush Module
==============================

Tests pre-compaction context preservation based on MoltBot research.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from forge_harness.memory_flush import (
    ContextMetrics,
    ContextMonitor,
    MemoryFlushManager,
)


class TestContextMetrics:
    """Test context metrics calculation."""

    def test_metrics_from_usage_below_threshold(self):
        """Test metrics when usage is below threshold."""
        metrics = ContextMetrics.from_usage(
            current_tokens=50000,
            max_tokens=100000,
            threshold=0.7,
        )

        assert metrics.current_tokens == 50000
        assert metrics.max_tokens == 100000
        assert metrics.usage_percent == 0.5
        assert metrics.threshold == 0.7
        assert metrics.should_flush is False

    def test_metrics_from_usage_above_threshold(self):
        """Test metrics when usage is above threshold."""
        metrics = ContextMetrics.from_usage(
            current_tokens=80000,
            max_tokens=100000,
            threshold=0.7,
        )

        assert metrics.current_tokens == 80000
        assert metrics.max_tokens == 100000
        assert metrics.usage_percent == 0.8
        assert metrics.threshold == 0.7
        assert metrics.should_flush is True

    def test_metrics_from_usage_at_threshold(self):
        """Test metrics when usage is exactly at threshold."""
        metrics = ContextMetrics.from_usage(
            current_tokens=70000,
            max_tokens=100000,
            threshold=0.7,
        )

        assert metrics.usage_percent == 0.7
        assert metrics.should_flush is True

    def test_metrics_zero_max_tokens(self):
        """Test metrics with zero max tokens."""
        metrics = ContextMetrics.from_usage(
            current_tokens=1000,
            max_tokens=0,
            threshold=0.7,
        )

        assert metrics.usage_percent == 0.0
        assert metrics.should_flush is False


class TestContextMonitor:
    """Test context monitoring and flush triggering."""

    def test_initialization_valid_threshold(self):
        """Test monitor initialization with valid threshold."""
        monitor = ContextMonitor(threshold=0.7)
        assert monitor.threshold == 0.7
        assert monitor._last_flush is None
        assert monitor._flush_count == 0

    def test_initialization_invalid_threshold(self):
        """Test monitor initialization with invalid threshold."""
        with pytest.raises(ValueError, match="Threshold must be between"):
            ContextMonitor(threshold=1.5)

        with pytest.raises(ValueError, match="Threshold must be between"):
            ContextMonitor(threshold=-0.1)

    def test_should_flush_below_threshold(self):
        """Test should_flush when usage is below threshold."""
        monitor = ContextMonitor(threshold=0.7)

        assert monitor.should_flush(50000, 100000) is False

    def test_should_flush_above_threshold(self):
        """Test should_flush when usage is above threshold."""
        monitor = ContextMonitor(threshold=0.7)

        assert monitor.should_flush(80000, 100000) is True

    def test_should_flush_respects_minimum_interval(self):
        """Test that flush respects minimum interval between flushes."""
        monitor = ContextMonitor(threshold=0.7)

        # First flush should be allowed
        assert monitor.should_flush(80000, 100000, min_interval_minutes=30) is True
        monitor.record_flush()

        # Second flush immediately after should be blocked
        assert monitor.should_flush(80000, 100000, min_interval_minutes=30) is False

    def test_record_flush_updates_state(self):
        """Test that record_flush updates internal state."""
        monitor = ContextMonitor(threshold=0.7)

        assert monitor._flush_count == 0
        assert monitor._last_flush is None

        monitor.record_flush()

        assert monitor._flush_count == 1
        assert monitor._last_flush is not None
        assert isinstance(monitor._last_flush, datetime)


class TestMemoryFlushManager:
    """Test memory flush management and session notes."""

    def test_initialization_creates_directory(self, tmp_path):
        """Test that initialization creates session notes directory."""
        manager = MemoryFlushManager(forge_root=tmp_path)

        assert manager.session_notes_dir.exists()
        assert manager.session_notes_dir.is_dir()
        assert manager.session_notes_dir == tmp_path / ".forge/learning" / "session_notes"

    def test_get_session_note_path_default_date(self, tmp_path):
        """Test session note path generation with default date."""
        manager = MemoryFlushManager(forge_root=tmp_path)

        path = manager._get_session_note_path()

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        assert path.name == f"{today}.md"
        assert path.parent == manager.session_notes_dir

    def test_get_session_note_path_custom_date(self, tmp_path):
        """Test session note path generation with custom date."""
        manager = MemoryFlushManager(forge_root=tmp_path)
        custom_date = datetime(2025, 1, 15, tzinfo=UTC)

        path = manager._get_session_note_path(date=custom_date)

        assert path.name == "2025-01-15.md"

    def test_build_memory_prompt(self, tmp_path):
        """Test memory prompt generation."""
        manager = MemoryFlushManager(forge_root=tmp_path)
        context = "Working on feature XYZ"

        prompt = manager._build_memory_prompt(context)

        assert "Memory Save Request" in prompt
        assert "Working on feature XYZ" in prompt
        assert "Key Decisions Made" in prompt
        assert "Patterns Discovered" in prompt
        assert "Technical Context" in prompt
        assert "Next Steps" in prompt

    @pytest.mark.asyncio
    async def test_save_session_note_creates_new_file(self, tmp_path):
        """Test saving session note creates new file."""
        manager = MemoryFlushManager(forge_root=tmp_path)

        await manager._save_session_note(
            memory_summary="Test summary",
            context="Test context",
            domain="test-domain",
            project="test-project",
            feature_id="TEST-001",
        )

        note_path = manager._get_session_note_path()
        assert note_path.exists()

        content = note_path.read_text()
        assert "# Session Notes" in content
        assert "Test summary" in content
        assert "test-domain" in content
        assert "test-project" in content
        assert "TEST-001" in content

    @pytest.mark.asyncio
    async def test_save_session_note_appends_to_existing(self, tmp_path):
        """Test saving session note appends to existing file."""
        manager = MemoryFlushManager(forge_root=tmp_path)

        # Save first note
        await manager._save_session_note(
            memory_summary="First summary",
            context="First context",
        )

        # Save second note
        await manager._save_session_note(
            memory_summary="Second summary",
            context="Second context",
        )

        note_path = manager._get_session_note_path()
        content = note_path.read_text()

        assert "First summary" in content
        assert "Second summary" in content
        assert content.count("---") >= 2  # Multiple separators

    @pytest.mark.asyncio
    async def test_save_fallback_note(self, tmp_path):
        """Test saving fallback note when LLM fails."""
        manager = MemoryFlushManager(forge_root=tmp_path)

        await manager._save_fallback_note(
            context="Test context for fallback",
            error="LLM query failed",
        )

        note_path = manager._get_session_note_path()
        content = note_path.read_text()

        assert "Memory Flush FAILED" in content
        assert "LLM query failed" in content
        assert "Test context for fallback" in content

    def test_get_recent_notes(self, tmp_path):
        """Test retrieving recent session notes."""
        manager = MemoryFlushManager(forge_root=tmp_path)

        # Create some test notes
        for i in range(3):
            date = datetime.now(UTC) - __import__("datetime").timedelta(days=i)
            note_path = manager._get_session_note_path(date)
            note_path.write_text(f"Note from {i} days ago")

        # Retrieve recent notes
        notes = manager.get_recent_notes(days=7)

        assert len(notes) == 3
        assert all(isinstance(date, datetime) for date, _ in notes)
        assert all(isinstance(content, str) for _, content in notes)

    @pytest.mark.asyncio
    async def test_trigger_flush_success(self, tmp_path):
        """Test successful memory flush trigger."""
        manager = MemoryFlushManager(forge_root=tmp_path)

        # Mock provider with query method
        mock_provider = Mock()
        mock_provider.query = AsyncMock(return_value="Memory summary from LLM")

        summary = await manager.trigger_flush(
            context="Test context",
            provider=mock_provider,
            domain="test-domain",
            project="test-project",
            feature_id="TEST-001",
        )

        assert summary == "Memory summary from LLM"
        mock_provider.query.assert_called_once()

        # Check note was saved
        note_path = manager._get_session_note_path()
        assert note_path.exists()
        content = note_path.read_text()
        assert "Memory summary from LLM" in content

    @pytest.mark.asyncio
    async def test_trigger_flush_provider_failure(self, tmp_path):
        """Test memory flush when provider fails."""
        manager = MemoryFlushManager(forge_root=tmp_path)

        # Mock provider that raises exception
        mock_provider = Mock()
        mock_provider.query = AsyncMock(side_effect=Exception("Provider error"))
        mock_provider.run_coding_session = None  # Doesn't have this method

        summary = await manager.trigger_flush(
            context="Test context",
            provider=mock_provider,
        )

        assert "Memory flush failed" in summary

        # Check fallback note was saved
        note_path = manager._get_session_note_path()
        assert note_path.exists()
        content = note_path.read_text()
        assert "Memory Flush FAILED" in content

    @pytest.mark.asyncio
    async def test_trigger_flush_with_streaming_provider(self, tmp_path):
        """Test memory flush with streaming provider."""
        manager = MemoryFlushManager(forge_root=tmp_path)

        # Mock streaming provider
        async def mock_streaming(prompt):
            for chunk in ["Part 1\n", "Part 2\n", "Part 3\n"]:
                mock_message = Mock()
                mock_message.content = chunk
                yield mock_message

        mock_provider = Mock()
        mock_provider.query = None  # Doesn't have query method
        mock_provider.run_coding_session = mock_streaming

        summary = await manager.trigger_flush(
            context="Test context",
            provider=mock_provider,
        )

        assert "Part 1" in summary
        assert "Part 2" in summary
        assert "Part 3" in summary


class TestIntegration:
    """Integration tests for memory flush workflow."""

    @pytest.mark.asyncio
    async def test_full_flush_workflow(self, tmp_path):
        """Test complete memory flush workflow."""
        # Setup
        monitor = ContextMonitor(threshold=0.7)
        manager = MemoryFlushManager(forge_root=tmp_path)
        mock_provider = Mock()
        mock_provider.query = AsyncMock(return_value="Full workflow test summary")

        # Check context - should not trigger yet
        assert monitor.should_flush(50000, 100000) is False

        # Increase context usage - should trigger
        assert monitor.should_flush(80000, 100000) is True

        # Trigger flush
        summary = await manager.trigger_flush(
            context="Integration test context",
            provider=mock_provider,
            domain="test-domain",
            project="test-project",
        )

        # Record flush
        monitor.record_flush()

        # Verify results
        assert summary == "Full workflow test summary"
        assert monitor._flush_count == 1
        assert monitor._last_flush is not None

        # Verify note saved
        notes = manager.get_recent_notes(days=1)
        assert len(notes) == 1
        assert "Full workflow test summary" in notes[0][1]

        # Verify minimum interval is respected
        assert monitor.should_flush(80000, 100000, min_interval_minutes=30) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
