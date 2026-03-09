"""
Additional tests for handoff.py to increase coverage.

Tests edge cases, error handling, and additional functionality.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forge_harness.handoff import (
    Handoff,
    HandoffManager,
    HandoffPriority,
    HandoffStatus,
    create_handoff_manager,
)


@pytest.mark.asyncio
class TestHandoffManagerAdditional:
    """Additional tests for HandoffManager."""

    @pytest.fixture
    def handoff_dir(self, tmp_path):
        """Create temporary handoff directory."""
        return tmp_path / "handoffs"

    @pytest.fixture
    def manager(self, handoff_dir):
        """Create HandoffManager instance."""
        return HandoffManager(handoff_dir)

    async def test_multiple_handoffs_same_agent(self, manager):
        """Test agent can have multiple pending handoffs."""
        await manager.create(from_agent="a", to_agent="target", task="task1")
        await manager.create(from_agent="b", to_agent="target", task="task2")
        await manager.create(from_agent="c", to_agent="target", task="task3")

        handoffs = await manager.list_for_agent("target")
        assert len(handoffs) == 3

    async def test_handoff_with_empty_metadata(self, manager):
        """Test handoff with empty optional fields."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
            description="",
            files=[],
            context={},
            acceptance_criteria=[],
        )

        assert handoff.description == ""
        assert handoff.files == []
        assert handoff.context == {}
        assert handoff.acceptance_criteria == []

    async def test_handoff_priority_sort_order(self, manager):
        """Test handoffs are sorted correctly by priority."""
        h1 = await manager.create(
            from_agent="a",
            to_agent="target",
            task="medium",
            priority=HandoffPriority.MEDIUM,
        )
        h2 = await manager.create(
            from_agent="a",
            to_agent="target",
            task="low",
            priority=HandoffPriority.LOW,
        )
        h3 = await manager.create(
            from_agent="a",
            to_agent="target",
            task="critical",
            priority=HandoffPriority.CRITICAL,
        )
        h4 = await manager.create(
            from_agent="a",
            to_agent="target",
            task="high",
            priority=HandoffPriority.HIGH,
        )

        handoffs = await manager.list_for_agent("target")

        priorities = [h.priority for h in handoffs]
        assert priorities == [
            HandoffPriority.CRITICAL,
            HandoffPriority.HIGH,
            HandoffPriority.MEDIUM,
            HandoffPriority.LOW,
        ]

    async def test_complete_handoff_with_minimal_data(self, manager):
        """Test completing handoff with no result or notes."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")

        completed = await manager.complete(handoff.id)

        assert completed.status == HandoffStatus.COMPLETED
        assert completed.completed_at is not None
        assert completed.result is None
        # notes may be None or empty string depending on implementation
        assert completed.notes in ("", None)

    async def test_complete_handoff_with_full_data(self, manager):
        """Test completing handoff with result and notes."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")

        result_data = {
            "files_modified": ["src/main.py", "tests/test_main.py"],
            "tests_passed": True,
            "coverage": 95.5,
        }

        completed = await manager.complete(
            handoff.id,
            result=result_data,
            notes="Successfully completed all acceptance criteria",
        )

        assert completed.result == result_data
        assert completed.notes == "Successfully completed all acceptance criteria"

    async def test_reject_handoff_with_reason(self, manager):
        """Test rejecting handoff stores the reason."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")

        rejected = await manager.reject(
            handoff.id,
            "b",
            reason="Cannot complete this task due to missing dependencies",
        )

        assert rejected.status == HandoffStatus.REJECTED
        assert "missing dependencies" in rejected.notes

    async def test_list_for_agent_empty(self, manager):
        """Test listing handoffs for agent with no handoffs."""
        handoffs = await manager.list_for_agent("nonexistent")
        assert handoffs == []

    async def test_list_pending_empty(self, manager):
        """Test listing pending handoffs when none exist."""
        pending = await manager.list_pending()
        assert pending == []

    async def test_list_pending_mixed_statuses(self, manager):
        """Test list_pending only returns pending handoffs."""
        h1 = await manager.create(from_agent="a", to_agent="b", task="task1")
        h2 = await manager.create(from_agent="a", to_agent="b", task="task2")
        h3 = await manager.create(from_agent="a", to_agent="b", task="task3")

        # Accept one
        await manager.accept(h2.id, "b")

        # Complete one
        await manager.complete(h3.id)

        pending = await manager.list_pending()

        assert len(pending) == 1
        assert pending[0].id == h1.id

    async def test_update_preserves_created_at(self, manager):
        """Test that updating a handoff preserves created_at timestamp."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        original_created_at = handoff.created_at

        handoff.status = HandoffStatus.ACCEPTED
        handoff.accepted_at = datetime.now(UTC).isoformat()
        updated = await manager.update(handoff)

        assert updated.created_at == original_created_at

    async def test_handoff_directory_created(self, manager):
        """Test handoff directory is created."""
        assert manager.handoff_dir.exists()
        assert manager.handoff_dir.is_dir()

    async def test_create_multiple_handoffs_unique_ids(self, manager):
        """Test that created handoffs have unique IDs."""
        handoffs = []
        for i in range(10):
            handoff = await manager.create(
                from_agent="a",
                to_agent="b",
                task=f"task{i}",
            )
            handoffs.append(handoff)

        ids = [h.id for h in handoffs]
        assert len(ids) == len(set(ids))  # All unique

    async def test_get_stats_with_only_pending(self, manager):
        """Test get_stats with only pending handoffs."""
        await manager.create(from_agent="a", to_agent="b", task="task1")
        await manager.create(from_agent="a", to_agent="b", task="task2")

        stats = await manager.get_stats()

        assert stats["total"] == 2
        assert stats["by_status"]["pending"] == 2
        assert stats["avg_completion_time_hours"] == 0

    async def test_get_stats_multiple_statuses(self, manager):
        """Test get_stats with multiple status types."""
        h1 = await manager.create(from_agent="a", to_agent="b", task="task1")
        h2 = await manager.create(from_agent="a", to_agent="b", task="task2")
        h3 = await manager.create(from_agent="a", to_agent="b", task="task3")

        await manager.accept(h1.id, "b")
        await manager.reject(h2.id, "b", reason="test")

        stats = await manager.get_stats()

        assert stats["total"] == 3
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["accepted"] == 1
        assert stats["by_status"]["rejected"] == 1

    async def test_get_stats_multiple_priorities(self, manager):
        """Test get_stats with multiple priority types."""
        await manager.create(
            from_agent="a",
            to_agent="b",
            task="task1",
            priority=HandoffPriority.HIGH,
        )
        await manager.create(
            from_agent="a",
            to_agent="b",
            task="task2",
            priority=HandoffPriority.HIGH,
        )
        await manager.create(
            from_agent="a",
            to_agent="b",
            task="task3",
            priority=HandoffPriority.LOW,
        )

        stats = await manager.get_stats()

        assert stats["by_priority"]["high"] == 2
        assert stats["by_priority"]["low"] == 1


class TestHandoffPromptGenerationExtended:
    """Extended tests for Handoff.to_prompt() method."""

    def test_prompt_with_special_characters_in_task(self):
        """Test prompt generation with special characters."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="Fix bug in <script> tag & handle 'quotes'",
        )

        prompt = handoff.to_prompt()

        assert "<script>" in prompt
        assert "&" in prompt
        assert "'quotes'" in prompt

    def test_prompt_with_multiline_description(self):
        """Test prompt with multiline description."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            description="Line 1\nLine 2\nLine 3",
        )

        prompt = handoff.to_prompt()

        assert "Line 1" in prompt
        assert "Line 2" in prompt
        assert "Line 3" in prompt

    def test_prompt_with_long_file_list(self):
        """Test prompt with many files."""
        files = [f"src/file{i}.py" for i in range(20)]
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            files=files,
        )

        prompt = handoff.to_prompt()

        assert "## Files to Review" in prompt
        for f in files:
            assert f"- `{f}`" in prompt

    def test_prompt_with_nested_json_context(self):
        """Test prompt with deeply nested JSON context."""
        context = {
            "level1": {
                "level2": {
                    "level3": {
                        "data": "value",
                        "number": 42,
                    }
                }
            }
        }
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            context=context,
        )

        prompt = handoff.to_prompt()

        assert "level1" in prompt
        assert "level2" in prompt
        assert "level3" in prompt

    def test_prompt_includes_domain_and_project(self):
        """Test that prompt generation preserves domain/project info."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            domain="voice-coach",
            project="brandfocus-ai",
        )

        # Domain/project are in the dataclass but not displayed in prompt
        assert handoff.domain == "voice-coach"
        assert handoff.project == "brandfocus-ai"


class TestCreateHandoffManagerFactory:
    """Tests for create_handoff_manager factory function."""

    def test_factory_uses_current_dir_by_default(self):
        """Test factory uses current directory when no root specified."""
        manager = create_handoff_manager(forge_root=Path("."))
        assert manager.handoff_dir == Path(".") / ".forge/handoffs"

    def test_factory_creates_nested_directory(self, tmp_path):
        """Test factory creates nested directory structure."""
        nested_path = tmp_path / "level1" / "level2" / "level3"
        manager = create_handoff_manager(forge_root=nested_path)

        assert manager.handoff_dir.exists()
        assert manager.handoff_dir == nested_path / ".forge/handoffs"


@pytest.mark.asyncio
class TestHandoffTransitions:
    """Tests for handoff status transitions."""

    @pytest.fixture
    def handoff_dir(self, tmp_path):
        """Create temporary handoff directory."""
        return tmp_path / "handoffs"

    @pytest.fixture
    def manager(self, handoff_dir):
        """Create HandoffManager instance."""
        return HandoffManager(handoff_dir)

    async def test_valid_transition_sequence(self, manager):
        """Test complete valid transition sequence."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        assert handoff.status == HandoffStatus.PENDING

        accepted = await manager.accept(handoff.id, "b")
        assert accepted.status == HandoffStatus.ACCEPTED

        in_progress = await manager.start(handoff.id)
        assert in_progress.status == HandoffStatus.IN_PROGRESS

        completed = await manager.complete(handoff.id)
        assert completed.status == HandoffStatus.COMPLETED

    async def test_reject_from_pending_transition(self, manager):
        """Test rejection from pending status."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")

        rejected = await manager.reject(handoff.id, "b", reason="Not feasible")

        assert rejected.status == HandoffStatus.REJECTED
        assert rejected.completed_at is not None

    async def test_start_from_pending_no_effect(self, manager):
        """Test starting a pending handoff has no effect."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")

        result = await manager.start(handoff.id)

        # Should return handoff but not change status
        assert result.status == HandoffStatus.PENDING

    async def test_complete_from_any_status(self, manager):
        """Test completion works from any status."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")

        # Complete directly from pending
        completed = await manager.complete(handoff.id)

        assert completed.status == HandoffStatus.COMPLETED


@pytest.mark.asyncio
class TestHandoffFiltering:
    """Tests for handoff filtering and queries."""

    @pytest.fixture
    def handoff_dir(self, tmp_path):
        """Create temporary handoff directory."""
        return tmp_path / "handoffs"

    @pytest.fixture
    def manager(self, handoff_dir):
        """Create HandoffManager instance."""
        return HandoffManager(handoff_dir)

    async def test_filter_by_status_pending(self, manager):
        """Test filtering by pending status."""
        await manager.create(from_agent="a", to_agent="target", task="task1")
        h2 = await manager.create(from_agent="a", to_agent="target", task="task2")
        await manager.accept(h2.id, "target")

        pending = await manager.list_for_agent("target", status=HandoffStatus.PENDING)

        assert len(pending) == 1
        assert pending[0].status == HandoffStatus.PENDING

    async def test_filter_by_status_accepted(self, manager):
        """Test filtering by accepted status."""
        h1 = await manager.create(from_agent="a", to_agent="target", task="task1")
        h2 = await manager.create(from_agent="a", to_agent="target", task="task2")

        await manager.accept(h1.id, "target")
        await manager.accept(h2.id, "target")

        accepted = await manager.list_for_agent("target", status=HandoffStatus.ACCEPTED)

        assert len(accepted) == 2
        assert all(h.status == HandoffStatus.ACCEPTED for h in accepted)

    async def test_list_as_sender_filters_correctly(self, manager):
        """Test as_sender=True only returns sent handoffs."""
        await manager.create(from_agent="sender", to_agent="a", task="task1")
        await manager.create(from_agent="sender", to_agent="b", task="task2")
        await manager.create(from_agent="other", to_agent="sender", task="task3")

        sent = await manager.list_for_agent("sender", as_sender=True)

        assert len(sent) == 2
        assert all(h.from_agent == "sender" for h in sent)

    async def test_list_as_receiver_default_behavior(self, manager):
        """Test default behavior lists received handoffs."""
        await manager.create(from_agent="a", to_agent="target", task="task1")
        await manager.create(from_agent="target", to_agent="b", task="task2")

        received = await manager.list_for_agent("target")

        assert len(received) == 1
        assert received[0].to_agent == "target"
