"""Tests for FORGE SessionStateManager - session persistence and handoffs."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forge_harness.session_state import (
    HumanDecision,
    SessionState,
    SessionStateManager,
    create_session_state_manager,
)


class TestHumanDecision:
    """Tests for HumanDecision dataclass."""

    def test_human_decision_creation(self):
        """HumanDecision can be created with required fields."""
        decision = HumanDecision(
            question="Which database to use?",
            decision="PostgreSQL",
        )
        assert decision.question == "Which database to use?"
        assert decision.decision == "PostgreSQL"
        assert decision.rationale is None
        assert decision.decided_by is None
        assert decision.decided_at is not None

    def test_human_decision_full(self):
        """HumanDecision accepts all attributes."""
        decision = HumanDecision(
            question="Which framework?",
            decision="FastAPI",
            rationale="Better async support",
            decided_by="@alice",
            context_key="framework",
        )
        assert decision.rationale == "Better async support"
        assert decision.decided_by == "@alice"
        assert decision.context_key == "framework"

    def test_human_decision_to_dict(self):
        """HumanDecision serializes to dict."""
        decision = HumanDecision(
            question="Test?",
            decision="Yes",
            rationale="Because",
        )
        data = decision.to_dict()

        assert data["question"] == "Test?"
        assert data["decision"] == "Yes"
        assert data["rationale"] == "Because"
        assert "decided_at" in data

    def test_human_decision_from_dict(self):
        """HumanDecision deserializes from dict."""
        data = {
            "question": "Test?",
            "decision": "Yes",
            "rationale": "Because",
            "decided_at": "2025-01-10T12:00:00+00:00",
            "decided_by": "@bob",
            "context_key": "answer",
        }
        decision = HumanDecision.from_dict(data)

        assert decision.question == "Test?"
        assert decision.decision == "Yes"
        assert decision.decided_by == "@bob"
        assert decision.decided_at.year == 2025


class TestSessionState:
    """Tests for SessionState dataclass."""

    @pytest.fixture
    def session_state(self):
        """Create a basic SessionState."""
        now = datetime.now(UTC)
        return SessionState(
            session_id="test-123",
            created_at=now,
            last_activity=now,
            active_domain="codeswiftr-com",
            active_project="interview-simulator",
        )

    def test_session_state_creation(self, session_state):
        """SessionState can be created with required fields."""
        assert session_state.session_id == "test-123"
        assert session_state.active_domain == "codeswiftr-com"
        assert session_state.completed_tasks == []
        assert session_state.pending_tasks == []

    def test_touch_updates_last_activity(self, session_state):
        """touch() updates last_activity timestamp."""
        old_activity = session_state.last_activity
        session_state.touch()
        assert session_state.last_activity >= old_activity

    def test_add_task_completed(self, session_state):
        """add_task_completed() moves task from pending to completed."""
        session_state.pending_tasks.append("setup_database")
        session_state.add_task_completed("setup_database")

        assert "setup_database" in session_state.completed_tasks
        assert "setup_database" not in session_state.pending_tasks

    def test_add_task_completed_not_duplicate(self, session_state):
        """add_task_completed() doesn't duplicate tasks."""
        session_state.add_task_completed("task1")
        session_state.add_task_completed("task1")

        assert session_state.completed_tasks.count("task1") == 1

    def test_add_task_pending(self, session_state):
        """add_task_pending() adds new pending task."""
        session_state.add_task_pending("new_task")
        assert "new_task" in session_state.pending_tasks

    def test_add_task_pending_not_if_completed(self, session_state):
        """add_task_pending() doesn't add if already completed."""
        session_state.completed_tasks.append("done_task")
        session_state.add_task_pending("done_task")

        assert "done_task" not in session_state.pending_tasks

    def test_add_decision(self, session_state):
        """add_decision() records human decision."""
        decision = session_state.add_decision(
            question="Use TypeScript?",
            decision="Yes",
            rationale="Better type safety",
            decided_by="@dev",
            context_key="use_typescript",
        )

        assert decision in session_state.human_decisions
        assert session_state.context_data["use_typescript"] == "Yes"

    def test_add_file_created(self, session_state):
        """add_file_created() records created file."""
        session_state.add_file_created("src/main.py")
        assert "src/main.py" in session_state.files_created

    def test_add_file_modified(self, session_state):
        """add_file_modified() records modified file."""
        session_state.add_file_modified("src/utils.py")
        assert "src/utils.py" in session_state.files_modified

    def test_add_file_modified_not_if_created(self, session_state):
        """add_file_modified() skips if file was created in session."""
        session_state.files_created.append("new_file.py")
        session_state.add_file_modified("new_file.py")

        assert "new_file.py" not in session_state.files_modified

    def test_get_summary(self, session_state):
        """get_summary() returns session summary."""
        session_state.completed_tasks = ["task1", "task2"]
        session_state.pending_tasks = ["task3"]
        session_state.files_created = ["file1.py"]

        summary = session_state.get_summary()

        assert summary["session_id"] == "test-123"
        assert summary["completed_tasks"] == 2
        assert summary["pending_tasks"] == 1
        assert summary["files_created"] == 1

    def test_to_dict(self, session_state):
        """to_dict() serializes entire state."""
        session_state.add_decision("Q?", "A", rationale="R")
        session_state.completed_tasks.append("task1")

        data = session_state.to_dict()

        assert data["session_id"] == "test-123"
        assert data["active_domain"] == "codeswiftr-com"
        assert len(data["human_decisions"]) == 1
        assert "task1" in data["completed_tasks"]

    def test_from_dict(self):
        """from_dict() deserializes entire state."""
        data = {
            "session_id": "abc-123",
            "created_at": "2025-01-10T12:00:00+00:00",
            "last_activity": "2025-01-10T14:00:00+00:00",
            "active_domain": "test-domain",
            "active_project": "test-project",
            "completed_tasks": ["task1"],
            "pending_tasks": ["task2"],
            "human_decisions": [
                {
                    "question": "Q?",
                    "decision": "A",
                    "rationale": None,
                    "decided_at": "2025-01-10T13:00:00+00:00",
                    "decided_by": None,
                    "context_key": None,
                },
            ],
            "files_created": ["file.py"],
            "context_data": {"key": "value"},
            "tags": ["tag1"],
        }

        state = SessionState.from_dict(data)

        assert state.session_id == "abc-123"
        assert state.active_domain == "test-domain"
        assert len(state.completed_tasks) == 1
        assert len(state.human_decisions) == 1
        assert state.tags == ["tag1"]


class TestSessionStateManager:
    """Tests for SessionStateManager class."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create SessionStateManager with temp directory."""
        return SessionStateManager(storage_dir=tmp_path)

    def test_create_session(self, manager):
        """create_session() creates new SessionState."""
        state = manager.create_session(
            domain="codeswiftr-com",
            project="interview-simulator",
            tags=["sprint1"],
        )

        assert state.session_id is not None
        assert state.active_domain == "codeswiftr-com"
        assert state.active_project == "interview-simulator"
        assert "sprint1" in state.tags

    def test_create_session_generates_id(self, manager):
        """create_session() generates unique session IDs."""
        state1 = manager.create_session()
        state2 = manager.create_session()

        assert state1.session_id != state2.session_id

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, manager):
        """save_state() and load_state() round-trip correctly."""
        state = manager.create_session(
            domain="test-domain",
            project="test-project",
        )
        state.completed_tasks.append("task1")
        state.add_decision("Q?", "A")

        await manager.save_state(state)
        loaded = await manager.load_state(state.session_id)

        assert loaded is not None
        assert loaded.session_id == state.session_id
        assert loaded.active_domain == "test-domain"
        assert "task1" in loaded.completed_tasks
        assert len(loaded.human_decisions) == 1

    @pytest.mark.asyncio
    async def test_load_state_not_found(self, manager):
        """load_state() returns None for unknown session."""
        result = await manager.load_state("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_state(self, manager):
        """delete_state() removes session file."""
        state = manager.create_session()
        await manager.save_state(state)

        deleted = await manager.delete_state(state.session_id)
        assert deleted is True

        loaded = await manager.load_state(state.session_id)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_delete_state_not_found(self, manager):
        """delete_state() returns False for unknown session."""
        deleted = await manager.delete_state("nonexistent-id")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_merge_states(self, manager):
        """merge_states() combines old and new sessions."""
        old = manager.create_session(domain="domain1")
        old.completed_tasks = ["task1", "task2"]
        old.checkpoints = {"checkpoint1": {"step": 1}}
        old.files_created = ["file1.py"]
        old.add_decision("Old Q?", "Old A")

        new = manager.create_session(domain="domain2", project="new-project")
        new.completed_tasks = ["task3"]
        new.pending_tasks = ["task4"]
        new.checkpoints = {"checkpoint2": {"step": 2}}
        new.files_created = ["file2.py"]
        new.add_decision("New Q?", "New A")

        merged = await manager.merge_states(old, new)

        # Uses new session's ID
        assert merged.session_id == new.session_id

        # Uses new values for active context
        assert merged.active_domain == "domain2"
        assert merged.active_project == "new-project"

        # Combines completed tasks
        assert "task1" in merged.completed_tasks
        assert "task2" in merged.completed_tasks
        assert "task3" in merged.completed_tasks

        # Uses new pending tasks
        assert merged.pending_tasks == ["task4"]

        # Combines checkpoints
        assert "checkpoint1" in merged.checkpoints
        assert "checkpoint2" in merged.checkpoints

        # Combines decisions
        assert len(merged.human_decisions) == 2

        # Combines files
        assert "file1.py" in merged.files_created
        assert "file2.py" in merged.files_created

    @pytest.mark.asyncio
    async def test_list_sessions(self, manager):
        """list_sessions() returns saved sessions."""
        state1 = manager.create_session(domain="domain1", project="proj1")
        state2 = manager.create_session(domain="domain2", project="proj2")

        await manager.save_state(state1)
        await manager.save_state(state2)

        sessions = await manager.list_sessions()

        assert len(sessions) == 2
        session_ids = [s["session_id"] for s in sessions]
        assert state1.session_id in session_ids
        assert state2.session_id in session_ids

    @pytest.mark.asyncio
    async def test_list_sessions_filter_by_domain(self, manager):
        """list_sessions() filters by domain."""
        state1 = manager.create_session(domain="domain1")
        state2 = manager.create_session(domain="domain2")

        await manager.save_state(state1)
        await manager.save_state(state2)

        sessions = await manager.list_sessions(domain="domain1")

        assert len(sessions) == 1
        assert sessions[0]["session_id"] == state1.session_id

    @pytest.mark.asyncio
    async def test_list_sessions_filter_by_project(self, manager):
        """list_sessions() filters by project."""
        state1 = manager.create_session(domain="domain", project="proj1")
        state2 = manager.create_session(domain="domain", project="proj2")

        await manager.save_state(state1)
        await manager.save_state(state2)

        sessions = await manager.list_sessions(project="proj1")

        assert len(sessions) == 1
        assert sessions[0]["session_id"] == state1.session_id

    @pytest.mark.asyncio
    async def test_list_sessions_limit(self, manager):
        """list_sessions() respects limit parameter."""
        for i in range(5):
            state = manager.create_session()
            await manager.save_state(state)

        sessions = await manager.list_sessions(limit=3)
        assert len(sessions) == 3

    @pytest.mark.asyncio
    async def test_list_sessions_sorted_by_activity(self, manager):
        """list_sessions() returns most recent first."""
        state1 = manager.create_session()
        await manager.save_state(state1)

        state2 = manager.create_session()
        await manager.save_state(state2)

        sessions = await manager.list_sessions()

        # Most recent (state2) should be first
        assert sessions[0]["session_id"] == state2.session_id

    @pytest.mark.asyncio
    async def test_load_recent_session(self, manager):
        """load_recent_session() returns most recent for project."""
        state1 = manager.create_session(domain="domain", project="proj")
        await manager.save_state(state1)

        state2 = manager.create_session(domain="domain", project="proj")
        await manager.save_state(state2)

        recent = await manager.load_recent_session(domain="domain", project="proj")

        assert recent is not None
        assert recent.session_id == state2.session_id

    @pytest.mark.asyncio
    async def test_load_recent_session_not_found(self, manager):
        """load_recent_session() returns None when no sessions exist."""
        recent = await manager.load_recent_session(domain="nonexistent")
        assert recent is None

    @pytest.mark.asyncio
    async def test_continue_session(self, manager):
        """continue_session() creates continuation from old session."""
        original = manager.create_session(domain="domain", project="proj")
        original.completed_tasks = ["task1"]
        original.pending_tasks = ["task2"]
        original.add_decision("Q?", "A")
        await manager.save_state(original)

        continued = await manager.continue_session(original.session_id)

        assert continued is not None
        assert continued.session_id != original.session_id
        assert continued.active_domain == "domain"
        assert "task1" in continued.completed_tasks
        assert "task2" in continued.pending_tasks
        assert continued.context_data["continued_from"] == original.session_id

    @pytest.mark.asyncio
    async def test_continue_session_with_custom_id(self, manager):
        """continue_session() accepts custom session ID."""
        original = manager.create_session()
        await manager.save_state(original)

        continued = await manager.continue_session(
            original.session_id,
            new_session_id="custom-id",
        )

        assert continued.session_id == "custom-id"

    @pytest.mark.asyncio
    async def test_continue_session_not_found(self, manager):
        """continue_session() returns None for unknown session."""
        continued = await manager.continue_session("nonexistent")
        assert continued is None


class TestHandoffPromptGeneration:
    """Tests for handoff prompt generation."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create SessionStateManager."""
        return SessionStateManager(storage_dir=tmp_path)

    def test_generate_handoff_prompt_basic(self, manager):
        """generate_handoff_prompt() creates basic prompt."""
        state = manager.create_session(
            domain="codeswiftr-com",
            project="interview-simulator",
        )

        prompt = manager.generate_handoff_prompt(state)

        assert "Session Handoff" in prompt
        assert "codeswiftr-com" in prompt
        assert "interview-simulator" in prompt

    def test_generate_handoff_prompt_with_tasks(self, manager):
        """generate_handoff_prompt() includes tasks."""
        state = manager.create_session()
        state.completed_tasks = ["setup_db", "create_models"]
        state.pending_tasks = ["write_tests", "deploy"]

        prompt = manager.generate_handoff_prompt(state)

        assert "## Completed Tasks" in prompt
        assert "[x] setup_db" in prompt
        assert "## Pending Tasks" in prompt
        assert "[ ] write_tests" in prompt

    def test_generate_handoff_prompt_with_decisions(self, manager):
        """generate_handoff_prompt() includes decisions."""
        state = manager.create_session()
        state.add_decision(
            question="Database choice?",
            decision="PostgreSQL",
            rationale="Better performance",
        )

        prompt = manager.generate_handoff_prompt(state)

        assert "## Key Decisions" in prompt
        assert "Database choice?" in prompt
        assert "PostgreSQL" in prompt
        assert "Better performance" in prompt

    def test_generate_handoff_prompt_with_files(self, manager):
        """generate_handoff_prompt() includes file changes."""
        state = manager.create_session()
        state.files_created = ["src/new_file.py"]
        state.files_modified = ["src/existing.py"]

        prompt = manager.generate_handoff_prompt(state)

        assert "## Files Changed" in prompt
        assert "`src/new_file.py`" in prompt
        assert "`src/existing.py`" in prompt

    def test_generate_handoff_prompt_with_deferred(self, manager):
        """generate_handoff_prompt() includes deferred questions."""
        state = manager.create_session()
        state.deferred_questions = [
            "Should we add caching?",
            "What about rate limiting?",
        ]

        prompt = manager.generate_handoff_prompt(state)

        assert "## Deferred Questions" in prompt
        assert "Should we add caching?" in prompt

    def test_generate_handoff_prompt_with_context(self, manager):
        """generate_handoff_prompt() includes context data."""
        state = manager.create_session()
        state.context_data = {
            "api_version": "v2",
            "continued_from": "old-session",  # Should be excluded
        }

        prompt = manager.generate_handoff_prompt(state)

        assert "## Context" in prompt
        assert "api_version" in prompt
        assert "continued_from" not in prompt


class TestFactoryFunction:
    """Tests for create_session_state_manager factory."""

    def test_factory_with_path(self, tmp_path):
        """Factory creates manager with specified path."""
        manager = create_session_state_manager(storage_dir=tmp_path / "sessions")

        assert manager.storage_dir == tmp_path / "sessions"
        assert manager.storage_dir.exists()

    def test_factory_with_string_path(self, tmp_path):
        """Factory accepts string path."""
        manager = create_session_state_manager(storage_dir=str(tmp_path / "sessions"))

        assert manager.storage_dir == tmp_path / "sessions"

    def test_factory_default_path(self, monkeypatch, tmp_path):
        """Factory uses default home directory path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        manager = create_session_state_manager()

        assert manager.storage_dir == tmp_path / ".forge" / "sessions"


class TestErrorRecovery:
    """Tests for error handling and state recovery."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create SessionStateManager with temp directory."""
        return SessionStateManager(storage_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_list_sessions_with_corrupted_file(self, manager):
        """list_sessions() skips corrupted session files."""
        # Create a valid session
        state = manager.create_session(domain="valid-domain")
        await manager.save_state(state)

        # Create a corrupted JSON file
        corrupted_path = manager.storage_dir / "session_corrupted.json"
        corrupted_path.write_text("{invalid json content")

        # Should return only the valid session
        sessions = await manager.list_sessions()

        assert len(sessions) == 1
        assert sessions[0]["session_id"] == state.session_id

    @pytest.mark.asyncio
    async def test_save_state_updates_last_activity(self, manager):
        """save_state() automatically updates last_activity."""
        state = manager.create_session()
        original_activity = state.last_activity

        # Wait a tiny bit to ensure timestamp difference
        import time

        time.sleep(0.01)

        await manager.save_state(state)

        # last_activity should be updated
        assert state.last_activity > original_activity

    @pytest.mark.asyncio
    async def test_save_state_creates_directory(self, tmp_path):
        """save_state() creates storage directory if missing."""
        storage_dir = tmp_path / "new_sessions"
        manager = SessionStateManager(storage_dir=storage_dir)

        # Directory should be created by __init__
        assert storage_dir.exists()

        state = manager.create_session()
        path = await manager.save_state(state)

        assert path.exists()
        assert path.parent == storage_dir

    @pytest.mark.asyncio
    async def test_merge_states_with_empty_old_state(self, manager):
        """merge_states() handles empty old state."""
        old = manager.create_session()
        new = manager.create_session(domain="new-domain", project="new-proj")
        new.completed_tasks = ["task1"]

        merged = await manager.merge_states(old, new)

        assert merged.active_domain == "new-domain"
        assert "task1" in merged.completed_tasks

    @pytest.mark.asyncio
    async def test_merge_states_preserves_old_created_at(self, manager):
        """merge_states() uses old session's created_at."""
        old = manager.create_session()
        old_created = old.created_at

        # Wait to ensure different timestamp
        import time

        time.sleep(0.01)

        new = manager.create_session()

        merged = await manager.merge_states(old, new)

        assert merged.created_at == old_created
        assert merged.created_at != new.created_at


class TestSessionStateEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.fixture
    def session_state(self):
        """Create a basic SessionState."""
        now = datetime.now(UTC)
        return SessionState(
            session_id="test-123",
            created_at=now,
            last_activity=now,
        )

    def test_add_task_pending_duplicate(self, session_state):
        """add_task_pending() doesn't add duplicate pending tasks."""
        session_state.add_task_pending("task1")
        session_state.add_task_pending("task1")

        assert session_state.pending_tasks.count("task1") == 1

    def test_add_file_created_duplicate(self, session_state):
        """add_file_created() doesn't add duplicate files."""
        session_state.add_file_created("file.py")
        session_state.add_file_created("file.py")

        assert session_state.files_created.count("file.py") == 1

    def test_add_file_modified_duplicate(self, session_state):
        """add_file_modified() doesn't add duplicate files."""
        session_state.add_file_modified("file.py")
        session_state.add_file_modified("file.py")

        assert session_state.files_modified.count("file.py") == 1

    def test_add_decision_without_context_key(self, session_state):
        """add_decision() works without context_key."""
        decision = session_state.add_decision(
            question="Q?",
            decision="A",
        )

        assert decision in session_state.human_decisions
        assert len(session_state.context_data) == 0

    def test_get_summary_calculates_duration(self, session_state):
        """get_summary() calculates duration_hours."""
        # Simulate 2 hour session
        session_state.created_at = datetime.now(UTC) - timedelta(hours=2)
        session_state.last_activity = datetime.now(UTC)

        summary = session_state.get_summary()

        # Should be approximately 2 hours (with small tolerance)
        assert 1.9 < summary["duration_hours"] < 2.1

    def test_from_dict_with_minimal_data(self):
        """from_dict() handles minimal required data."""
        data = {
            "session_id": "min-123",
            "created_at": "2025-01-10T12:00:00+00:00",
            "last_activity": "2025-01-10T12:00:00+00:00",
        }

        state = SessionState.from_dict(data)

        assert state.session_id == "min-123"
        assert state.active_domain is None
        assert state.completed_tasks == []
        assert state.human_decisions == []


class TestHandoffPromptEdgeCases:
    """Tests for handoff prompt generation edge cases."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create SessionStateManager."""
        return SessionStateManager(storage_dir=tmp_path)

    def test_generate_handoff_prompt_with_pipeline(self, manager):
        """generate_handoff_prompt() includes pipeline when set."""
        state = manager.create_session(
            domain="test-domain",
            project="test-project",
            pipeline="test-pipeline",
        )

        prompt = manager.generate_handoff_prompt(state)

        assert "**Pipeline:** test-pipeline" in prompt

    def test_generate_handoff_prompt_without_pipeline(self, manager):
        """generate_handoff_prompt() omits pipeline when not set."""
        state = manager.create_session(
            domain="test-domain",
            project="test-project",
        )

        prompt = manager.generate_handoff_prompt(state)

        assert "Pipeline:" not in prompt

    def test_generate_handoff_prompt_empty_state(self, manager):
        """generate_handoff_prompt() handles empty state."""
        state = manager.create_session()

        prompt = manager.generate_handoff_prompt(state)

        # Should have basic structure
        assert "Session Handoff" in prompt
        assert "Not set" in prompt
        # Should not have task sections
        assert "## Completed Tasks" not in prompt
        assert "## Pending Tasks" not in prompt


class TestCheckpointManagement:
    """Tests for checkpoint storage and retrieval."""

    @pytest.fixture
    def session_state(self):
        """Create a basic SessionState."""
        now = datetime.now(UTC)
        return SessionState(
            session_id="test-123",
            created_at=now,
            last_activity=now,
        )

    def test_checkpoint_storage(self, session_state):
        """Checkpoints can be stored and retrieved."""
        checkpoint_data = {
            "step": 5,
            "completed": ["step1", "step2"],
            "context": {"key": "value"},
        }

        session_state.checkpoints["pipeline1"] = checkpoint_data

        assert "pipeline1" in session_state.checkpoints
        assert session_state.checkpoints["pipeline1"]["step"] == 5

    def test_checkpoint_serialization(self, session_state):
        """Checkpoints survive serialization round-trip."""
        session_state.checkpoints["test"] = {"data": "value"}

        data = session_state.to_dict()
        restored = SessionState.from_dict(data)

        assert "test" in restored.checkpoints
        assert restored.checkpoints["test"]["data"] == "value"


class TestConcurrentSessionOperations:
    """Tests for handling concurrent session operations."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create SessionStateManager."""
        return SessionStateManager(storage_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_multiple_sessions_same_domain(self, manager):
        """Multiple sessions can exist for same domain."""
        state1 = manager.create_session(domain="domain", project="proj1")
        state2 = manager.create_session(domain="domain", project="proj2")

        await manager.save_state(state1)
        await manager.save_state(state2)

        sessions = await manager.list_sessions(domain="domain")

        assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_session_file_path_generation(self, manager):
        """_get_session_path generates correct paths."""
        state = manager.create_session()
        path = manager._get_session_path(state.session_id)

        assert path.parent == manager.storage_dir
        assert path.name.startswith("session_")
        assert path.name.endswith(".json")
        assert state.session_id in path.name


class TestTagsAndContextData:
    """Tests for tags and context_data functionality."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create SessionStateManager."""
        return SessionStateManager(storage_dir=tmp_path)

    def test_create_session_with_tags(self, manager):
        """create_session() accepts and stores tags."""
        state = manager.create_session(tags=["sprint1", "feature-x"])

        assert "sprint1" in state.tags
        assert "feature-x" in state.tags

    @pytest.mark.asyncio
    async def test_tags_persist_across_save_load(self, manager):
        """Tags survive save/load cycle."""
        state = manager.create_session(tags=["tag1", "tag2"])
        await manager.save_state(state)

        loaded = await manager.load_state(state.session_id)

        assert loaded.tags == ["tag1", "tag2"]

    @pytest.mark.asyncio
    async def test_merge_combines_tags(self, manager):
        """merge_states() combines tags from both sessions."""
        old = manager.create_session(tags=["tag1", "tag2"])
        new = manager.create_session(tags=["tag2", "tag3"])

        merged = await manager.merge_states(old, new)

        # Should deduplicate
        assert "tag1" in merged.tags
        assert "tag2" in merged.tags
        assert "tag3" in merged.tags

    def test_context_data_storage(self, manager):
        """context_data can store arbitrary key-value pairs."""
        state = manager.create_session()
        state.context_data["api_version"] = "v2"
        state.context_data["feature_flags"] = ["flag1", "flag2"]

        assert state.context_data["api_version"] == "v2"
        assert len(state.context_data["feature_flags"]) == 2

    @pytest.mark.asyncio
    async def test_context_data_persists(self, manager):
        """context_data survives save/load cycle."""
        state = manager.create_session()
        state.context_data["key"] = "value"
        await manager.save_state(state)

        loaded = await manager.load_state(state.session_id)

        assert loaded.context_data["key"] == "value"


class TestDeferredQuestions:
    """Tests for deferred questions functionality."""

    @pytest.fixture
    def session_state(self):
        """Create a basic SessionState."""
        now = datetime.now(UTC)
        return SessionState(
            session_id="test-123",
            created_at=now,
            last_activity=now,
        )

    def test_deferred_questions_storage(self, session_state):
        """Deferred questions can be added and retrieved."""
        session_state.deferred_questions.append("Question 1?")
        session_state.deferred_questions.append("Question 2?")

        assert len(session_state.deferred_questions) == 2
        assert "Question 1?" in session_state.deferred_questions

    @pytest.mark.asyncio
    async def test_deferred_questions_persist(self, tmp_path):
        """Deferred questions survive save/load cycle."""
        manager = SessionStateManager(storage_dir=tmp_path)
        state = manager.create_session()
        state.deferred_questions = ["Q1?", "Q2?"]

        await manager.save_state(state)
        loaded = await manager.load_state(state.session_id)

        assert loaded.deferred_questions == ["Q1?", "Q2?"]

    @pytest.mark.asyncio
    async def test_merge_uses_new_deferred_questions(self, tmp_path):
        """merge_states() uses new session's deferred questions."""
        manager = SessionStateManager(storage_dir=tmp_path)
        old = manager.create_session()
        old.deferred_questions = ["Old Q1?"]

        new = manager.create_session()
        new.deferred_questions = ["New Q1?", "New Q2?"]

        merged = await manager.merge_states(old, new)

        # Should use new questions, not merge
        assert merged.deferred_questions == ["New Q1?", "New Q2?"]
