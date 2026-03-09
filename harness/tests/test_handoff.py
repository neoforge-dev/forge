"""Tests for FORGE agent handoff system."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from forge_harness.handoff import (
    Handoff,
    HandoffManager,
    HandoffPriority,
    HandoffStatus,
    create_handoff_manager,
)


class TestHandoffStatus:
    """Tests for HandoffStatus enum."""

    def test_all_status_values_exist(self):
        """HandoffStatus has all expected values."""
        assert HandoffStatus.PENDING == "pending"
        assert HandoffStatus.ACCEPTED == "accepted"
        assert HandoffStatus.IN_PROGRESS == "in_progress"
        assert HandoffStatus.COMPLETED == "completed"
        assert HandoffStatus.REJECTED == "rejected"
        assert HandoffStatus.EXPIRED == "expired"

    def test_status_enum_membership(self):
        """HandoffStatus values are enum members."""
        assert HandoffStatus.PENDING in HandoffStatus
        assert HandoffStatus.ACCEPTED in HandoffStatus
        assert HandoffStatus.IN_PROGRESS in HandoffStatus
        assert HandoffStatus.COMPLETED in HandoffStatus
        assert HandoffStatus.REJECTED in HandoffStatus
        assert HandoffStatus.EXPIRED in HandoffStatus

    def test_status_string_comparison(self):
        """HandoffStatus can be compared with strings."""
        assert HandoffStatus.PENDING == "pending"
        assert HandoffStatus.COMPLETED.value == "completed"

    def test_status_from_string(self):
        """HandoffStatus can be created from string."""
        status = HandoffStatus("pending")
        assert status == HandoffStatus.PENDING


class TestHandoffPriority:
    """Tests for HandoffPriority enum."""

    def test_all_priority_values_exist(self):
        """HandoffPriority has all expected values."""
        assert HandoffPriority.CRITICAL == "critical"
        assert HandoffPriority.HIGH == "high"
        assert HandoffPriority.MEDIUM == "medium"
        assert HandoffPriority.LOW == "low"

    def test_priority_enum_membership(self):
        """HandoffPriority values are enum members."""
        assert HandoffPriority.CRITICAL in HandoffPriority
        assert HandoffPriority.HIGH in HandoffPriority
        assert HandoffPriority.MEDIUM in HandoffPriority
        assert HandoffPriority.LOW in HandoffPriority

    def test_priority_ordering(self):
        """Priority levels have logical ordering."""
        priorities = [
            HandoffPriority.CRITICAL,
            HandoffPriority.HIGH,
            HandoffPriority.MEDIUM,
            HandoffPriority.LOW,
        ]
        # Ensure all values are unique
        assert len(set(p.value for p in priorities)) == 4


class TestHandoffDataclass:
    """Tests for Handoff dataclass."""

    def test_handoff_minimal_creation(self):
        """Handoff can be created with minimal required fields."""
        handoff = Handoff(
            id="test123",
            from_agent="agent-a",
            to_agent="agent-b",
            task="Fix the bug",
        )

        assert handoff.id == "test123"
        assert handoff.from_agent == "agent-a"
        assert handoff.to_agent == "agent-b"
        assert handoff.task == "Fix the bug"
        assert handoff.status == HandoffStatus.PENDING
        assert handoff.priority == HandoffPriority.MEDIUM

    def test_handoff_with_all_fields(self):
        """Handoff can be created with all fields."""
        handoff = Handoff(
            id="test456",
            from_agent="agent-a",
            to_agent="agent-b",
            task="Implement feature",
            status=HandoffStatus.ACCEPTED,
            priority=HandoffPriority.HIGH,
            description="Detailed description",
            files=["src/main.py", "tests/test_main.py"],
            context={"key": "value"},
            test_command="pytest tests/",
            acceptance_criteria=["Tests pass", "Code reviewed"],
            domain="test-domain",
            project="test-project",
            created_at="2025-01-01T00:00:00+00:00",
            accepted_at="2025-01-01T01:00:00+00:00",
            completed_at=None,
            expires_at="2025-01-02T00:00:00+00:00",
            result={"success": True},
            notes="Some notes",
        )

        assert handoff.id == "test456"
        assert handoff.status == HandoffStatus.ACCEPTED
        assert handoff.priority == HandoffPriority.HIGH
        assert handoff.description == "Detailed description"
        assert len(handoff.files) == 2
        assert handoff.context["key"] == "value"
        assert handoff.test_command == "pytest tests/"
        assert len(handoff.acceptance_criteria) == 2
        assert handoff.domain == "test-domain"
        assert handoff.project == "test-project"

    def test_handoff_defaults(self):
        """Handoff has correct default values."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
        )

        assert handoff.description == ""
        assert handoff.files == []
        assert handoff.context == {}
        assert handoff.test_command is None
        assert handoff.acceptance_criteria == []
        assert handoff.domain is None
        assert handoff.project is None
        assert handoff.accepted_at is None
        assert handoff.completed_at is None
        assert handoff.expires_at is None
        assert handoff.result is None
        assert handoff.notes == ""

    def test_handoff_created_at_auto_generated(self):
        """Handoff created_at is auto-generated if not provided."""
        before = datetime.now(UTC)
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
        )
        after = datetime.now(UTC)

        created_at = datetime.fromisoformat(handoff.created_at.replace("Z", "+00:00"))
        assert before <= created_at <= after


class TestHandoffSerialization:
    """Tests for Handoff serialization methods."""

    def test_to_dict_basic(self):
        """Handoff.to_dict() converts to dictionary."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
        )

        data = handoff.to_dict()

        assert isinstance(data, dict)
        assert data["id"] == "test"
        assert data["from_agent"] == "a"
        assert data["to_agent"] == "b"
        assert data["task"] == "task"

    def test_to_dict_with_enums(self):
        """Handoff.to_dict() properly serializes enums."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            status=HandoffStatus.COMPLETED,
            priority=HandoffPriority.HIGH,
        )

        data = handoff.to_dict()

        assert data["status"] == HandoffStatus.COMPLETED
        assert data["priority"] == HandoffPriority.HIGH

    def test_to_dict_with_complex_types(self):
        """Handoff.to_dict() handles complex types."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            files=["file1.py", "file2.py"],
            context={"nested": {"key": "value"}},
            acceptance_criteria=["criterion1", "criterion2"],
        )

        data = handoff.to_dict()

        assert data["files"] == ["file1.py", "file2.py"]
        assert data["context"]["nested"]["key"] == "value"
        assert len(data["acceptance_criteria"]) == 2

    def test_from_dict_basic(self):
        """Handoff.from_dict() creates from dictionary."""
        data = {
            "id": "test",
            "from_agent": "a",
            "to_agent": "b",
            "task": "task",
        }

        handoff = Handoff.from_dict(data)

        assert handoff.id == "test"
        assert handoff.from_agent == "a"
        assert handoff.to_agent == "b"
        assert handoff.task == "task"

    def test_from_dict_with_string_enums(self):
        """Handoff.from_dict() converts string enums."""
        data = {
            "id": "test",
            "from_agent": "a",
            "to_agent": "b",
            "task": "task",
            "status": "completed",
            "priority": "high",
        }

        handoff = Handoff.from_dict(data)

        assert handoff.status == HandoffStatus.COMPLETED
        assert handoff.priority == HandoffPriority.HIGH

    def test_from_dict_with_enum_objects(self):
        """Handoff.from_dict() handles existing enum objects."""
        data = {
            "id": "test",
            "from_agent": "a",
            "to_agent": "b",
            "task": "task",
            "status": HandoffStatus.IN_PROGRESS,
            "priority": HandoffPriority.CRITICAL,
        }

        handoff = Handoff.from_dict(data)

        assert handoff.status == HandoffStatus.IN_PROGRESS
        assert handoff.priority == HandoffPriority.CRITICAL

    def test_round_trip_serialization(self):
        """Handoff survives round-trip serialization."""
        original = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            status=HandoffStatus.ACCEPTED,
            priority=HandoffPriority.HIGH,
            description="desc",
            files=["file.py"],
            context={"key": "value"},
            test_command="pytest",
            acceptance_criteria=["criterion"],
            domain="domain",
            project="project",
        )

        data = original.to_dict()
        restored = Handoff.from_dict(data)

        assert restored.id == original.id
        assert restored.from_agent == original.from_agent
        assert restored.to_agent == original.to_agent
        assert restored.task == original.task
        assert restored.status == original.status
        assert restored.priority == original.priority
        assert restored.description == original.description
        assert restored.files == original.files
        assert restored.context == original.context
        assert restored.test_command == original.test_command
        assert restored.acceptance_criteria == original.acceptance_criteria
        assert restored.domain == original.domain
        assert restored.project == original.project


class TestHandoffPromptGeneration:
    """Tests for Handoff.to_prompt() method."""

    def test_to_prompt_minimal(self):
        """to_prompt() generates basic prompt."""
        handoff = Handoff(
            id="test123",
            from_agent="agent-a",
            to_agent="agent-b",
            task="Fix the bug",
        )

        prompt = handoff.to_prompt()

        assert "# Handoff from agent-a" in prompt
        assert "**Task:** Fix the bug" in prompt
        assert "**Priority:** medium" in prompt
        assert "Handoff ID: `test123`" in prompt
        assert "forge-harness handoff accept test123" in prompt
        assert "forge-harness handoff reject test123" in prompt

    def test_to_prompt_with_description(self):
        """to_prompt() includes description."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            description="This is a detailed description",
        )

        prompt = handoff.to_prompt()

        assert "## Description" in prompt
        assert "This is a detailed description" in prompt

    def test_to_prompt_with_files(self):
        """to_prompt() includes files to review."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            files=["src/main.py", "tests/test_main.py"],
        )

        prompt = handoff.to_prompt()

        assert "## Files to Review" in prompt
        assert "- `src/main.py`" in prompt
        assert "- `tests/test_main.py`" in prompt

    def test_to_prompt_with_context(self):
        """to_prompt() includes JSON context."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            context={"key": "value", "nested": {"data": 123}},
        )

        prompt = handoff.to_prompt()

        assert "## Context" in prompt
        assert "```json" in prompt
        assert '"key": "value"' in prompt
        assert '"nested"' in prompt

    def test_to_prompt_with_test_command(self):
        """to_prompt() includes test command."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            test_command="pytest tests/ -v",
        )

        prompt = handoff.to_prompt()

        assert "## Test Command" in prompt
        assert "```bash" in prompt
        assert "pytest tests/ -v" in prompt

    def test_to_prompt_with_acceptance_criteria(self):
        """to_prompt() includes acceptance criteria as checklist."""
        handoff = Handoff(
            id="test",
            from_agent="a",
            to_agent="b",
            task="task",
            acceptance_criteria=[
                "All tests pass",
                "Code is reviewed",
                "Documentation updated",
            ],
        )

        prompt = handoff.to_prompt()

        assert "## Acceptance Criteria" in prompt
        assert "- [ ] All tests pass" in prompt
        assert "- [ ] Code is reviewed" in prompt
        assert "- [ ] Documentation updated" in prompt

    def test_to_prompt_with_all_sections(self):
        """to_prompt() includes all sections when provided."""
        handoff = Handoff(
            id="test123",
            from_agent="agent-a",
            to_agent="agent-b",
            task="Complete feature",
            priority=HandoffPriority.CRITICAL,
            description="Feature description",
            files=["file.py"],
            context={"key": "value"},
            test_command="pytest",
            acceptance_criteria=["Tests pass"],
        )

        prompt = handoff.to_prompt()

        # Check all sections are present
        assert "# Handoff from agent-a" in prompt
        assert "## Description" in prompt
        assert "## Files to Review" in prompt
        assert "## Context" in prompt
        assert "## Test Command" in prompt
        assert "## Acceptance Criteria" in prompt


@pytest.mark.asyncio
class TestHandoffManager:
    """Tests for HandoffManager class."""

    @pytest.fixture
    def handoff_dir(self, tmp_path):
        """Create temporary handoff directory."""
        return tmp_path / "handoffs"

    @pytest.fixture
    def manager(self, handoff_dir):
        """Create HandoffManager instance."""
        return HandoffManager(handoff_dir)

    async def test_manager_creates_directory(self, tmp_path):
        """HandoffManager creates directory if it doesn't exist."""
        handoff_dir = tmp_path / "new_handoffs"
        assert not handoff_dir.exists()

        manager = HandoffManager(handoff_dir)

        assert handoff_dir.exists()
        assert handoff_dir.is_dir()

    async def test_create_handoff(self, manager):
        """HandoffManager.create() creates new handoff."""
        handoff = await manager.create(
            from_agent="agent-a",
            to_agent="agent-b",
            task="Fix the bug",
        )

        assert handoff.id is not None
        assert len(handoff.id) == 8  # UUID truncated to 8 chars
        assert handoff.from_agent == "agent-a"
        assert handoff.to_agent == "agent-b"
        assert handoff.task == "Fix the bug"
        assert handoff.status == HandoffStatus.PENDING

    async def test_create_handoff_with_kwargs(self, manager):
        """HandoffManager.create() accepts additional kwargs."""
        handoff = await manager.create(
            from_agent="agent-a",
            to_agent="agent-b",
            task="task",
            priority=HandoffPriority.HIGH,
            description="desc",
            files=["file.py"],
            domain="test-domain",
        )

        assert handoff.priority == HandoffPriority.HIGH
        assert handoff.description == "desc"
        assert handoff.files == ["file.py"]
        assert handoff.domain == "test-domain"

    async def test_create_handoff_persists_to_disk(self, manager, handoff_dir):
        """HandoffManager.create() persists handoff to disk."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
        )

        handoff_path = handoff_dir / f"{handoff.id}.json"
        assert handoff_path.exists()

        # Verify file content
        with open(handoff_path) as f:
            data = json.load(f)
        assert data["id"] == handoff.id
        assert data["from_agent"] == "a"

    async def test_get_existing_handoff(self, manager):
        """HandoffManager.get() retrieves existing handoff."""
        created = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
        )

        retrieved = await manager.get(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.from_agent == created.from_agent
        assert retrieved.to_agent == created.to_agent
        assert retrieved.task == created.task

    async def test_get_nonexistent_handoff(self, manager):
        """HandoffManager.get() returns None for nonexistent handoff."""
        result = await manager.get("nonexistent")

        assert result is None

    async def test_update_handoff(self, manager):
        """HandoffManager.update() updates existing handoff."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
        )

        handoff.status = HandoffStatus.COMPLETED
        handoff.notes = "Done!"
        updated = await manager.update(handoff)

        # Verify update
        retrieved = await manager.get(handoff.id)
        assert retrieved.status == HandoffStatus.COMPLETED
        assert retrieved.notes == "Done!"

    async def test_list_for_agent_as_receiver(self, manager):
        """HandoffManager.list_for_agent() lists handoffs for receiver."""
        await manager.create(from_agent="a", to_agent="target", task="task1")
        await manager.create(from_agent="b", to_agent="target", task="task2")
        await manager.create(from_agent="c", to_agent="other", task="task3")

        handoffs = await manager.list_for_agent("target")

        assert len(handoffs) == 2
        assert all(h.to_agent == "target" for h in handoffs)

    async def test_list_for_agent_as_sender(self, manager):
        """HandoffManager.list_for_agent() lists handoffs sent by agent."""
        await manager.create(from_agent="sender", to_agent="a", task="task1")
        await manager.create(from_agent="sender", to_agent="b", task="task2")
        await manager.create(from_agent="other", to_agent="c", task="task3")

        handoffs = await manager.list_for_agent("sender", as_sender=True)

        assert len(handoffs) == 2
        assert all(h.from_agent == "sender" for h in handoffs)

    async def test_list_for_agent_with_status_filter(self, manager):
        """HandoffManager.list_for_agent() filters by status."""
        h1 = await manager.create(from_agent="a", to_agent="target", task="task1")
        h2 = await manager.create(from_agent="b", to_agent="target", task="task2")
        h3 = await manager.create(from_agent="c", to_agent="target", task="task3")

        # Update statuses
        h2.status = HandoffStatus.COMPLETED
        await manager.update(h2)

        # List only pending
        pending = await manager.list_for_agent("target", status=HandoffStatus.PENDING)

        assert len(pending) == 2
        assert all(h.status == HandoffStatus.PENDING for h in pending)

    async def test_list_for_agent_priority_ordering(self, manager):
        """HandoffManager.list_for_agent() orders by priority."""
        await manager.create(
            from_agent="a",
            to_agent="target",
            task="low",
            priority=HandoffPriority.LOW,
        )
        await manager.create(
            from_agent="a",
            to_agent="target",
            task="critical",
            priority=HandoffPriority.CRITICAL,
        )
        await manager.create(
            from_agent="a",
            to_agent="target",
            task="high",
            priority=HandoffPriority.HIGH,
        )
        await manager.create(
            from_agent="a",
            to_agent="target",
            task="medium",
            priority=HandoffPriority.MEDIUM,
        )

        handoffs = await manager.list_for_agent("target")

        # Should be ordered: critical, high, medium, low
        assert handoffs[0].priority == HandoffPriority.CRITICAL
        assert handoffs[1].priority == HandoffPriority.HIGH
        assert handoffs[2].priority == HandoffPriority.MEDIUM
        assert handoffs[3].priority == HandoffPriority.LOW

    async def test_list_pending(self, manager):
        """HandoffManager.list_pending() lists all pending handoffs."""
        h1 = await manager.create(from_agent="a", to_agent="b", task="task1")
        h2 = await manager.create(from_agent="c", to_agent="d", task="task2")
        h3 = await manager.create(from_agent="e", to_agent="f", task="task3")

        # Mark one as accepted
        h2.status = HandoffStatus.ACCEPTED
        await manager.update(h2)

        pending = await manager.list_pending()

        assert len(pending) == 2
        assert all(h.status == HandoffStatus.PENDING for h in pending)

    async def test_accept_handoff(self, manager):
        """HandoffManager.accept() accepts a handoff."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="target",
            task="task",
        )

        accepted = await manager.accept(handoff.id, "target")

        assert accepted.status == HandoffStatus.ACCEPTED
        assert accepted.accepted_at is not None

        # Verify persistence
        retrieved = await manager.get(handoff.id)
        assert retrieved.status == HandoffStatus.ACCEPTED

    async def test_accept_handoff_wrong_agent(self, manager):
        """HandoffManager.accept() raises error for wrong agent."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="target",
            task="task",
        )

        with pytest.raises(ValueError, match="not addressed to"):
            await manager.accept(handoff.id, "wrong-agent")

    async def test_accept_handoff_wrong_status(self, manager):
        """HandoffManager.accept() raises error if not pending."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="target",
            task="task",
        )

        # Accept once
        await manager.accept(handoff.id, "target")

        # Try to accept again
        with pytest.raises(ValueError, match="not pending"):
            await manager.accept(handoff.id, "target")

    async def test_accept_nonexistent_handoff(self, manager):
        """HandoffManager.accept() returns None for nonexistent handoff."""
        result = await manager.accept("nonexistent", "agent")

        assert result is None

    async def test_reject_handoff(self, manager):
        """HandoffManager.reject() rejects a handoff."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="target",
            task="task",
        )

        rejected = await manager.reject(handoff.id, "target", reason="Not feasible")

        assert rejected.status == HandoffStatus.REJECTED
        assert rejected.notes == "Not feasible"
        assert rejected.completed_at is not None

        # Verify persistence
        retrieved = await manager.get(handoff.id)
        assert retrieved.status == HandoffStatus.REJECTED

    async def test_reject_handoff_wrong_agent(self, manager):
        """HandoffManager.reject() raises error for wrong agent."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="target",
            task="task",
        )

        with pytest.raises(ValueError, match="not addressed to"):
            await manager.reject(handoff.id, "wrong-agent")

    async def test_reject_nonexistent_handoff(self, manager):
        """HandoffManager.reject() returns None for nonexistent handoff."""
        result = await manager.reject("nonexistent", "agent")

        assert result is None

    async def test_start_handoff(self, manager):
        """HandoffManager.start() marks handoff as in progress."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
        )

        # Accept first
        await manager.accept(handoff.id, "b")

        # Then start
        started = await manager.start(handoff.id)

        assert started.status == HandoffStatus.IN_PROGRESS

        # Verify persistence
        retrieved = await manager.get(handoff.id)
        assert retrieved.status == HandoffStatus.IN_PROGRESS

    async def test_start_handoff_wrong_status(self, manager):
        """HandoffManager.start() only works for accepted handoffs."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
        )

        # Try to start pending handoff
        result = await manager.start(handoff.id)

        # Should return handoff but not change status
        assert result.status == HandoffStatus.PENDING

    async def test_complete_handoff(self, manager):
        """HandoffManager.complete() marks handoff as completed."""
        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
        )

        completed = await manager.complete(
            handoff.id,
            result={"success": True, "files": ["file.py"]},
            notes="All done!",
        )

        assert completed.status == HandoffStatus.COMPLETED
        assert completed.completed_at is not None
        assert completed.result == {"success": True, "files": ["file.py"]}
        assert completed.notes == "All done!"

        # Verify persistence
        retrieved = await manager.get(handoff.id)
        assert retrieved.status == HandoffStatus.COMPLETED
        assert retrieved.result is not None

    async def test_complete_nonexistent_handoff(self, manager):
        """HandoffManager.complete() returns None for nonexistent handoff."""
        result = await manager.complete("nonexistent")

        assert result is None

    async def test_get_stats_empty(self, manager):
        """HandoffManager.get_stats() handles empty state."""
        stats = await manager.get_stats()

        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["by_priority"] == {}
        assert stats["avg_completion_time_hours"] == 0

    async def test_get_stats_with_handoffs(self, manager):
        """HandoffManager.get_stats() computes statistics."""
        # Create handoffs with different statuses and priorities
        await manager.create(
            from_agent="a",
            to_agent="b",
            task="task1",
            priority=HandoffPriority.HIGH,
        )
        h2 = await manager.create(
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

        # Complete one handoff
        h2.status = HandoffStatus.COMPLETED
        h2.completed_at = datetime.now(UTC).isoformat()
        await manager.update(h2)

        stats = await manager.get_stats()

        assert stats["total"] == 3
        assert stats["by_status"]["pending"] == 2
        assert stats["by_status"]["completed"] == 1
        assert stats["by_priority"]["high"] == 2
        assert stats["by_priority"]["low"] == 1

    async def test_get_stats_completion_time(self, manager):
        """HandoffManager.get_stats() calculates average completion time."""
        # Create handoff with known timestamps
        now = datetime.now(UTC)
        one_hour_ago = now - timedelta(hours=1)

        h1 = await manager.create(from_agent="a", to_agent="b", task="task1")
        h1.created_at = one_hour_ago.isoformat()
        h1.completed_at = now.isoformat()
        h1.status = HandoffStatus.COMPLETED
        await manager.update(h1)

        two_hours_ago = now - timedelta(hours=2)
        h2 = await manager.create(from_agent="a", to_agent="b", task="task2")
        h2.created_at = two_hours_ago.isoformat()
        h2.completed_at = now.isoformat()
        h2.status = HandoffStatus.COMPLETED
        await manager.update(h2)

        stats = await manager.get_stats()

        # Average of 1 hour and 2 hours = 1.5 hours
        assert 1.4 <= stats["avg_completion_time_hours"] <= 1.6

    async def test_concurrent_operations(self, manager):
        """HandoffManager handles concurrent operations safely."""
        import asyncio

        # Create multiple handoffs concurrently
        tasks = [manager.create(from_agent="a", to_agent="b", task=f"task{i}") for i in range(10)]
        handoffs = await asyncio.gather(*tasks)

        # All handoffs should be created successfully
        assert len(handoffs) == 10
        assert len(set(h.id for h in handoffs)) == 10  # All unique IDs

        # Verify all are persisted
        for handoff in handoffs:
            retrieved = await manager.get(handoff.id)
            assert retrieved is not None


class TestHandoffManagerFactory:
    """Tests for create_handoff_manager factory function."""

    def test_create_handoff_manager_default_root(self, tmp_path, monkeypatch):
        """create_handoff_manager uses FORGE_ROOT from environment."""
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))

        manager = create_handoff_manager()

        assert manager.handoff_dir == tmp_path / ".forge/handoffs"

    def test_create_handoff_manager_explicit_root(self, tmp_path):
        """create_handoff_manager accepts explicit forge_root."""
        manager = create_handoff_manager(forge_root=tmp_path)

        assert manager.handoff_dir == tmp_path / ".forge/handoffs"

    def test_create_handoff_manager_creates_directory(self, tmp_path):
        """create_handoff_manager creates handoff directory."""
        handoff_dir = tmp_path / ".forge/handoffs"
        assert not handoff_dir.exists()

        manager = create_handoff_manager(forge_root=tmp_path)

        assert handoff_dir.exists()


@pytest.mark.asyncio
class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.fixture
    def handoff_dir(self, tmp_path):
        """Create temporary handoff directory."""
        return tmp_path / "handoffs"

    @pytest.fixture
    def manager(self, handoff_dir):
        """Create HandoffManager instance."""
        return HandoffManager(handoff_dir)

    async def test_handoff_with_empty_strings(self, manager):
        """Handoff handles empty strings gracefully."""
        handoff = await manager.create(
            from_agent="",
            to_agent="",
            task="",
        )

        assert handoff.from_agent == ""
        assert handoff.to_agent == ""
        assert handoff.task == ""

    async def test_handoff_with_long_strings(self, manager):
        """Handoff handles very long strings."""
        long_text = "x" * 10000

        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task=long_text,
            description=long_text,
        )

        assert len(handoff.task) == 10000
        assert len(handoff.description) == 10000

        # Verify persistence
        retrieved = await manager.get(handoff.id)
        assert len(retrieved.task) == 10000

    async def test_handoff_with_special_characters(self, manager):
        """Handoff handles special characters in strings."""
        special_text = "Hello 🚀 World! @#$%^&*() <script>alert('xss')</script>"

        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task=special_text,
        )

        retrieved = await manager.get(handoff.id)
        assert retrieved.task == special_text

    async def test_handoff_with_large_context(self, manager):
        """Handoff handles large context dictionaries."""
        large_context = {f"key{i}": f"value{i}" for i in range(1000)}

        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
            context=large_context,
        )

        retrieved = await manager.get(handoff.id)
        assert len(retrieved.context) == 1000

    async def test_handoff_with_deeply_nested_context(self, manager):
        """Handoff handles deeply nested context."""
        nested = {"level": 1}
        current = nested
        for i in range(2, 10):
            current["nested"] = {"level": i}
            current = current["nested"]

        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
            context=nested,
        )

        retrieved = await manager.get(handoff.id)
        assert retrieved.context["level"] == 1

    async def test_list_with_corrupted_file(self, manager, handoff_dir):
        """list_for_agent raises error with corrupted JSON files."""
        # Create valid handoff
        await manager.create(from_agent="a", to_agent="target", task="task")

        # Create corrupted file
        corrupted_path = handoff_dir / "corrupted.json"
        with open(corrupted_path, "w") as f:
            f.write("not valid json {{{")

        # Should raise JSONDecodeError when encountering corrupt file
        with pytest.raises(json.JSONDecodeError):
            await manager.list_for_agent("target")

    async def test_expired_handoff_detection(self, manager):
        """Handoff can detect if it's expired."""
        past = datetime.now(UTC) - timedelta(hours=1)

        handoff = await manager.create(
            from_agent="a",
            to_agent="b",
            task="task",
            expires_at=past.isoformat(),
        )

        # Expired handoffs could be detected in application logic
        assert handoff.expires_at < datetime.now(UTC).isoformat()

    async def test_handoff_status_transitions(self, manager):
        """Test valid handoff status transitions."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")

        # PENDING -> ACCEPTED
        accepted = await manager.accept(handoff.id, "b")
        assert accepted.status == HandoffStatus.ACCEPTED

        # ACCEPTED -> IN_PROGRESS
        in_progress = await manager.start(handoff.id)
        assert in_progress.status == HandoffStatus.IN_PROGRESS

        # IN_PROGRESS -> COMPLETED
        completed = await manager.complete(handoff.id)
        assert completed.status == HandoffStatus.COMPLETED

    async def test_handoff_rejection_from_pending(self, manager):
        """Handoff can be rejected from pending state."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")

        rejected = await manager.reject(handoff.id, "b", reason="Cannot do this")

        assert rejected.status == HandoffStatus.REJECTED
        assert rejected.notes == "Cannot do this"
