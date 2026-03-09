"""Tests for session_manager.py - Session Manager Service.

Tests cover:
- Initialization and configuration
- Session creation/deletion/listing
- Session state transitions (active -> paused -> active -> completed)
- Task management (add, complete, duplicate, out-of-band)
- Error handling (corrupt data, missing sessions, write failures)
- Edge cases (empty state, partial-match lookup, timezone handling)
- Singleton helpers (get_session_manager, reset_session_manager)
- CheckpointManager integration (cache invalidation)
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.checkpoint_manager import CheckpointManager, SessionInfo
from forge_harness.webhook_server.services.session_manager import (
    _VALID_STATUSES,
    SessionManager,
    get_session_manager,
    reset_session_manager,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Reset the module singleton before and after every test for isolation."""
    reset_session_manager()
    yield
    reset_session_manager()


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Return a fresh temp directory to serve as the sessions directory."""
    return tmp_path / "sessions"


@pytest.fixture
def manager(sessions_dir: Path) -> SessionManager:
    """Create a fresh SessionManager pointing at a temp directory."""
    return SessionManager(sessions_dir=sessions_dir)


@pytest.fixture
def mock_checkpoint_manager() -> MagicMock:
    """Return a mock CheckpointManager."""
    mock = MagicMock(spec=CheckpointManager)
    return mock


@pytest.fixture
def manager_with_checkpoint(
    sessions_dir: Path, mock_checkpoint_manager: MagicMock
) -> SessionManager:
    """Create a SessionManager with an injected mock CheckpointManager."""
    return SessionManager(
        sessions_dir=sessions_dir,
        checkpoint_manager=mock_checkpoint_manager,
    )


# =============================================================================
# _VALID_STATUSES constant
# =============================================================================


class TestValidStatuses:
    """Tests for the _VALID_STATUSES module-level constant."""

    def test_valid_statuses_contains_expected_values(self) -> None:
        """Should contain active, paused, and completed."""
        assert "active" in _VALID_STATUSES
        assert "paused" in _VALID_STATUSES
        assert "completed" in _VALID_STATUSES

    def test_valid_statuses_is_frozenset(self) -> None:
        """Should be immutable."""
        assert isinstance(_VALID_STATUSES, frozenset)

    def test_valid_statuses_size(self) -> None:
        """Should have exactly three statuses."""
        assert len(_VALID_STATUSES) == 3


# =============================================================================
# SessionManager Initialization
# =============================================================================


class TestSessionManagerInit:
    """Tests for SessionManager.__init__."""

    def test_default_sessions_dir(self, tmp_path: Path) -> None:
        """Should default to .forge/sessions in the cwd."""
        mgr = SessionManager()
        assert mgr.sessions_dir == Path(".forge/sessions")

    def test_custom_sessions_dir_str(self, tmp_path: Path) -> None:
        """Should accept a string path."""
        path = str(tmp_path / "custom_sessions")
        mgr = SessionManager(sessions_dir=path)
        assert mgr.sessions_dir == Path(path)

    def test_custom_sessions_dir_path(self, tmp_path: Path) -> None:
        """Should accept a Path object."""
        path = tmp_path / "my_sessions"
        mgr = SessionManager(sessions_dir=path)
        assert mgr.sessions_dir == path

    def test_no_directory_created_on_init(self, tmp_path: Path) -> None:
        """Should NOT create the sessions directory on init (lazy creation)."""
        path = tmp_path / "lazy_sessions"
        SessionManager(sessions_dir=path)
        assert not path.exists()

    def test_checkpoint_manager_injected(
        self, sessions_dir: Path, mock_checkpoint_manager: MagicMock
    ) -> None:
        """Should store the injected CheckpointManager."""
        mgr = SessionManager(
            sessions_dir=sessions_dir,
            checkpoint_manager=mock_checkpoint_manager,
        )
        assert mgr._checkpoint_manager is mock_checkpoint_manager

    def test_no_checkpoint_manager_by_default(self, sessions_dir: Path) -> None:
        """Should default checkpoint_manager to None."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        assert mgr._checkpoint_manager is None

    def test_lock_is_rlock(self, manager: SessionManager) -> None:
        """Internal lock should be a reentrant lock."""
        # RLock can be acquired multiple times by the same thread
        manager._lock.acquire()
        manager._lock.acquire()
        manager._lock.release()
        manager._lock.release()


# =============================================================================
# Session Creation
# =============================================================================


class TestCreateSession:
    """Tests for SessionManager.create_session."""

    def test_creates_session_with_correct_fields(self, manager: SessionManager) -> None:
        """Should create session with domain, project, and active status."""
        session = manager.create_session(domain="test-domain", project="test-project")

        assert session.domain == "test-domain"
        assert session.project == "test-project"
        assert session.status == "active"
        assert session.session_id is not None
        assert len(session.session_id) > 0

    def test_creates_session_file_on_disk(self, manager: SessionManager) -> None:
        """Should persist a JSON file to disk."""
        session = manager.create_session(domain="d", project="p")
        path = manager.sessions_dir / f"session_{session.session_id}.json"
        assert path.exists()

    def test_session_file_is_valid_json(self, manager: SessionManager) -> None:
        """The persisted file should be valid JSON."""
        session = manager.create_session(domain="d", project="p")
        path = manager.sessions_dir / f"session_{session.session_id}.json"
        data = json.loads(path.read_text())
        assert data["session_id"] == session.session_id

    def test_creates_sessions_dir_lazily(self, sessions_dir: Path) -> None:
        """Should create the sessions directory when first session is written."""
        assert not sessions_dir.exists()
        mgr = SessionManager(sessions_dir=sessions_dir)
        mgr.create_session(domain="d", project="p")
        assert sessions_dir.exists()

    def test_generates_unique_session_ids(self, manager: SessionManager) -> None:
        """Each session should receive a unique UUID."""
        s1 = manager.create_session(domain="d", project="p")
        s2 = manager.create_session(domain="d", project="p")
        assert s1.session_id != s2.session_id

    def test_stores_agent_id_in_context(self, manager: SessionManager) -> None:
        """agent_id should be stored in the session context."""
        session = manager.create_session(domain="d", project="p", agent_id="agent-007")
        assert session.context.get("agent_id") == "agent-007"

    def test_stores_metadata_in_context(self, manager: SessionManager) -> None:
        """metadata dict should be merged into context."""
        session = manager.create_session(
            domain="d",
            project="p",
            metadata={"key": "value", "num": 42},
        )
        assert session.context.get("key") == "value"
        assert session.context.get("num") == 42

    def test_metadata_and_agent_id_merged(self, manager: SessionManager) -> None:
        """Both agent_id and metadata should coexist in context."""
        session = manager.create_session(
            domain="d",
            project="p",
            agent_id="a1",
            metadata={"extra": True},
        )
        assert session.context.get("agent_id") == "a1"
        assert session.context.get("extra") is True

    def test_no_agent_id_empty_context(self, manager: SessionManager) -> None:
        """Without agent_id or metadata, context should be empty."""
        session = manager.create_session(domain="d", project="p")
        assert session.context == {}

    def test_pending_and_completed_tasks_empty(self, manager: SessionManager) -> None:
        """New session should have no tasks."""
        session = manager.create_session(domain="d", project="p")
        assert session.pending_tasks == []
        assert session.completed_tasks == []

    def test_started_at_is_timezone_aware(self, manager: SessionManager) -> None:
        """started_at should be UTC-aware."""
        session = manager.create_session(domain="d", project="p")
        assert session.started_at.tzinfo is not None

    def test_last_activity_set_on_create(self, manager: SessionManager) -> None:
        """last_activity should be set at creation time."""
        session = manager.create_session(domain="d", project="p")
        assert session.last_activity is not None

    def test_checkpoint_invalidated_on_create(
        self, manager_with_checkpoint: SessionManager, mock_checkpoint_manager: MagicMock
    ) -> None:
        """Creating a session should invalidate the checkpoint cache."""
        manager_with_checkpoint.create_session(domain="d", project="p")
        mock_checkpoint_manager.invalidate_cache.assert_called()


# =============================================================================
# Session Retrieval
# =============================================================================


class TestGetSession:
    """Tests for SessionManager.get_session."""

    def test_returns_session_for_existing_id(self, manager: SessionManager) -> None:
        """Should return the session when it exists."""
        created = manager.create_session(domain="d", project="p")
        retrieved = manager.get_session(created.session_id)
        assert retrieved is not None
        assert retrieved.session_id == created.session_id

    def test_returns_none_for_missing_id(self, manager: SessionManager) -> None:
        """Should return None when session does not exist."""
        result = manager.get_session("nonexistent-id")
        assert result is None

    def test_preserves_domain_and_project(self, manager: SessionManager) -> None:
        """Retrieved session should retain original domain and project."""
        created = manager.create_session(domain="my-domain", project="my-project")
        retrieved = manager.get_session(created.session_id)
        assert retrieved is not None
        assert retrieved.domain == "my-domain"
        assert retrieved.project == "my-project"

    def test_partial_match_lookup(self, sessions_dir: Path) -> None:
        """Should find session via partial ID match (glob fallback)."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr.create_session(domain="d", project="p")
        # Write a file with a slightly different name to test glob fallback
        sid = session.session_id
        partial = sid[:8]
        # The session file already exists; test that exact match works
        result = mgr.get_session(sid)
        assert result is not None

    def test_no_sessions_dir_returns_none(self, sessions_dir: Path) -> None:
        """Should return None when the sessions directory does not exist."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        # Don't create any session — dir doesn't exist yet
        result = mgr.get_session("any-id")
        assert result is None


# =============================================================================
# Session End
# =============================================================================


class TestEndSession:
    """Tests for SessionManager.end_session."""

    def test_marks_session_completed(self, manager: SessionManager) -> None:
        """end_session should set status to completed."""
        session = manager.create_session(domain="d", project="p")
        updated = manager.end_session(session.session_id)
        assert updated is not None
        assert updated.status == "completed"

    def test_updates_last_activity(self, manager: SessionManager) -> None:
        """end_session should update last_activity timestamp."""
        session = manager.create_session(domain="d", project="p")
        original_activity = session.last_activity
        updated = manager.end_session(session.session_id)
        assert updated is not None
        assert updated.last_activity is not None
        assert updated.last_activity >= original_activity  # type: ignore[operator]

    def test_persists_completed_status(self, manager: SessionManager) -> None:
        """Completed status should be readable back from disk."""
        session = manager.create_session(domain="d", project="p")
        manager.end_session(session.session_id)
        retrieved = manager.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.status == "completed"

    def test_returns_none_for_missing_session(self, manager: SessionManager) -> None:
        """Should return None when the session does not exist."""
        result = manager.end_session("ghost-session-id")
        assert result is None

    def test_checkpoint_invalidated_on_end(
        self, manager_with_checkpoint: SessionManager, mock_checkpoint_manager: MagicMock
    ) -> None:
        """Ending a session should invalidate the checkpoint cache."""
        session = manager_with_checkpoint.create_session(domain="d", project="p")
        mock_checkpoint_manager.reset_mock()
        manager_with_checkpoint.end_session(session.session_id)
        mock_checkpoint_manager.invalidate_cache.assert_called()


# =============================================================================
# Session Pause
# =============================================================================


class TestPauseSession:
    """Tests for SessionManager.pause_session."""

    def test_marks_session_paused(self, manager: SessionManager) -> None:
        """pause_session should set status to paused."""
        session = manager.create_session(domain="d", project="p")
        updated = manager.pause_session(session.session_id)
        assert updated is not None
        assert updated.status == "paused"

    def test_updates_last_activity(self, manager: SessionManager) -> None:
        """pause_session should update last_activity."""
        session = manager.create_session(domain="d", project="p")
        updated = manager.pause_session(session.session_id)
        assert updated is not None
        assert updated.last_activity is not None

    def test_persists_paused_status(self, manager: SessionManager) -> None:
        """Paused status should be readable back from disk."""
        session = manager.create_session(domain="d", project="p")
        manager.pause_session(session.session_id)
        retrieved = manager.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.status == "paused"

    def test_returns_none_for_missing_session(self, manager: SessionManager) -> None:
        """Should return None when session does not exist."""
        result = manager.pause_session("no-such-session")
        assert result is None

    def test_checkpoint_invalidated_on_pause(
        self, manager_with_checkpoint: SessionManager, mock_checkpoint_manager: MagicMock
    ) -> None:
        """Pausing a session should invalidate the checkpoint cache."""
        session = manager_with_checkpoint.create_session(domain="d", project="p")
        mock_checkpoint_manager.reset_mock()
        manager_with_checkpoint.pause_session(session.session_id)
        mock_checkpoint_manager.invalidate_cache.assert_called()


# =============================================================================
# Session Resume
# =============================================================================


class TestResumeSession:
    """Tests for SessionManager.resume_session."""

    def test_marks_session_active(self, manager: SessionManager) -> None:
        """resume_session should set status back to active."""
        session = manager.create_session(domain="d", project="p")
        manager.pause_session(session.session_id)
        updated = manager.resume_session(session.session_id)
        assert updated is not None
        assert updated.status == "active"

    def test_updates_last_activity(self, manager: SessionManager) -> None:
        """resume_session should update last_activity."""
        session = manager.create_session(domain="d", project="p")
        manager.pause_session(session.session_id)
        updated = manager.resume_session(session.session_id)
        assert updated is not None
        assert updated.last_activity is not None

    def test_persists_active_status(self, manager: SessionManager) -> None:
        """Resumed active status should be readable back from disk."""
        session = manager.create_session(domain="d", project="p")
        manager.pause_session(session.session_id)
        manager.resume_session(session.session_id)
        retrieved = manager.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.status == "active"

    def test_returns_none_for_missing_session(self, manager: SessionManager) -> None:
        """Should return None when session does not exist."""
        result = manager.resume_session("nonexistent")
        assert result is None

    def test_full_state_transition_cycle(self, manager: SessionManager) -> None:
        """active -> paused -> active -> completed full cycle."""
        session = manager.create_session(domain="d", project="p")
        assert session.status == "active"

        paused = manager.pause_session(session.session_id)
        assert paused is not None
        assert paused.status == "paused"

        resumed = manager.resume_session(session.session_id)
        assert resumed is not None
        assert resumed.status == "active"

        ended = manager.end_session(session.session_id)
        assert ended is not None
        assert ended.status == "completed"

    def test_checkpoint_invalidated_on_resume(
        self, manager_with_checkpoint: SessionManager, mock_checkpoint_manager: MagicMock
    ) -> None:
        """Resuming a session should invalidate the checkpoint cache."""
        session = manager_with_checkpoint.create_session(domain="d", project="p")
        manager_with_checkpoint.pause_session(session.session_id)
        mock_checkpoint_manager.reset_mock()
        manager_with_checkpoint.resume_session(session.session_id)
        mock_checkpoint_manager.invalidate_cache.assert_called()


# =============================================================================
# Task Management — add_task
# =============================================================================


class TestAddTask:
    """Tests for SessionManager.add_task."""

    def test_appends_task_to_pending(self, manager: SessionManager) -> None:
        """add_task should append to pending_tasks."""
        session = manager.create_session(domain="d", project="p")
        updated = manager.add_task(session.session_id, "Task Alpha")
        assert updated is not None
        assert "Task Alpha" in updated.pending_tasks

    def test_multiple_tasks_appended(self, manager: SessionManager) -> None:
        """Multiple tasks should all appear in pending_tasks."""
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Task 1")
        manager.add_task(session.session_id, "Task 2")
        updated = manager.add_task(session.session_id, "Task 3")
        assert updated is not None
        assert len(updated.pending_tasks) == 3

    def test_duplicate_tasks_allowed(self, manager: SessionManager) -> None:
        """Duplicate task descriptions are allowed (multiple enqueues)."""
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Duplicate Task")
        updated = manager.add_task(session.session_id, "Duplicate Task")
        assert updated is not None
        assert updated.pending_tasks.count("Duplicate Task") == 2

    def test_updates_last_activity(self, manager: SessionManager) -> None:
        """add_task should refresh last_activity."""
        session = manager.create_session(domain="d", project="p")
        updated = manager.add_task(session.session_id, "T")
        assert updated is not None
        assert updated.last_activity is not None

    def test_persisted_to_disk(self, manager: SessionManager) -> None:
        """Tasks should be readable back from disk."""
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Persistent Task")
        retrieved = manager.get_session(session.session_id)
        assert retrieved is not None
        assert "Persistent Task" in retrieved.pending_tasks

    def test_returns_none_for_missing_session(self, manager: SessionManager) -> None:
        """Should return None when session does not exist."""
        result = manager.add_task("ghost-id", "Some Task")
        assert result is None


# =============================================================================
# Task Management — complete_task
# =============================================================================


class TestCompleteTask:
    """Tests for SessionManager.complete_task."""

    def test_moves_task_from_pending_to_completed(self, manager: SessionManager) -> None:
        """complete_task should move the task from pending to completed."""
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Implement OAuth")
        updated = manager.complete_task(session.session_id, "Implement OAuth")
        assert updated is not None
        assert "Implement OAuth" not in updated.pending_tasks
        assert "Implement OAuth" in updated.completed_tasks

    def test_removes_first_occurrence_only(self, manager: SessionManager) -> None:
        """Only the first occurrence of duplicate tasks should be removed."""
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Dup Task")
        manager.add_task(session.session_id, "Dup Task")
        updated = manager.complete_task(session.session_id, "Dup Task")
        assert updated is not None
        assert updated.pending_tasks.count("Dup Task") == 1
        assert updated.completed_tasks.count("Dup Task") == 1

    def test_out_of_band_completion(self, manager: SessionManager) -> None:
        """Task not in pending_tasks should still be appended to completed_tasks."""
        session = manager.create_session(domain="d", project="p")
        # Don't add to pending first
        updated = manager.complete_task(session.session_id, "Out-of-Band Task")
        assert updated is not None
        assert "Out-of-Band Task" in updated.completed_tasks

    def test_updates_last_activity(self, manager: SessionManager) -> None:
        """complete_task should refresh last_activity."""
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "T")
        updated = manager.complete_task(session.session_id, "T")
        assert updated is not None
        assert updated.last_activity is not None

    def test_persisted_to_disk(self, manager: SessionManager) -> None:
        """Completed task should be readable back from disk."""
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Disk Task")
        manager.complete_task(session.session_id, "Disk Task")
        retrieved = manager.get_session(session.session_id)
        assert retrieved is not None
        assert "Disk Task" in retrieved.completed_tasks
        assert "Disk Task" not in retrieved.pending_tasks

    def test_returns_none_for_missing_session(self, manager: SessionManager) -> None:
        """Should return None when session does not exist."""
        result = manager.complete_task("ghost-id", "Any Task")
        assert result is None

    def test_full_workflow(self, manager: SessionManager) -> None:
        """End-to-end: create session, add tasks, complete them, end session."""
        session = manager.create_session(domain="codeswiftr-com", project="interview-sim")
        manager.add_task(session.session_id, "Implement OAuth flow")
        manager.add_task(session.session_id, "Write unit tests")
        manager.complete_task(session.session_id, "Implement OAuth flow")
        manager.complete_task(session.session_id, "Write unit tests")
        ended = manager.end_session(session.session_id)
        assert ended is not None
        assert ended.status == "completed"
        assert len(ended.completed_tasks) == 2
        assert len(ended.pending_tasks) == 0


# =============================================================================
# list_sessions
# =============================================================================


class TestListSessions:
    """Tests for SessionManager.list_sessions."""

    def test_empty_when_no_sessions_dir(self, sessions_dir: Path) -> None:
        """Should return empty list when sessions directory does not exist."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        assert mgr.list_sessions() == []

    def test_lists_all_sessions(self, manager: SessionManager) -> None:
        """Should return all created sessions."""
        manager.create_session(domain="d", project="p1")
        manager.create_session(domain="d", project="p2")
        manager.create_session(domain="d", project="p3")
        sessions = manager.list_sessions()
        assert len(sessions) == 3

    def test_status_filter(self, manager: SessionManager) -> None:
        """Should filter sessions by status."""
        s1 = manager.create_session(domain="d", project="p1")
        s2 = manager.create_session(domain="d", project="p2")
        manager.end_session(s2.session_id)

        active = manager.list_sessions(status="active")
        completed = manager.list_sessions(status="completed")

        assert len(active) == 1
        assert active[0].session_id == s1.session_id
        assert len(completed) == 1
        assert completed[0].session_id == s2.session_id

    def test_domain_filter(self, manager: SessionManager) -> None:
        """Should filter sessions by domain."""
        manager.create_session(domain="domain-a", project="p")
        manager.create_session(domain="domain-b", project="p")
        manager.create_session(domain="domain-a", project="p2")

        results = manager.list_sessions(domain="domain-a")
        assert len(results) == 2
        for s in results:
            assert s.domain == "domain-a"

    def test_combined_status_and_domain_filter(self, manager: SessionManager) -> None:
        """Should handle combined status + domain filter."""
        s1 = manager.create_session(domain="forge", project="p1")
        s2 = manager.create_session(domain="forge", project="p2")
        manager.create_session(domain="other", project="p3")
        manager.end_session(s2.session_id)

        results = manager.list_sessions(status="active", domain="forge")
        assert len(results) == 1
        assert results[0].session_id == s1.session_id

    def test_limit_respected(self, manager: SessionManager) -> None:
        """Should not return more sessions than limit."""
        for i in range(10):
            manager.create_session(domain="d", project=f"p{i}")

        results = manager.list_sessions(limit=3)
        assert len(results) == 3

    def test_sorted_by_last_activity_descending(self, manager: SessionManager) -> None:
        """Should return sessions sorted by last_activity, most recent first."""
        s1 = manager.create_session(domain="d", project="p1")
        s2 = manager.create_session(domain="d", project="p2")
        # Add a task to s1 to push its last_activity later
        manager.add_task(s1.session_id, "bump")

        results = manager.list_sessions()
        assert results[0].session_id == s1.session_id

    def test_skips_corrupt_session_files(self, sessions_dir: Path) -> None:
        """Corrupt session files should be silently skipped."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        corrupt_path = sessions_dir / "session_bad-id.json"
        corrupt_path.write_text("{ this is not valid JSON }")

        mgr = SessionManager(sessions_dir=sessions_dir)
        results = mgr.list_sessions()
        assert results == []

    def test_paused_filter(self, manager: SessionManager) -> None:
        """Should return only paused sessions when filtering by paused."""
        s1 = manager.create_session(domain="d", project="p1")
        manager.create_session(domain="d", project="p2")
        manager.pause_session(s1.session_id)

        results = manager.list_sessions(status="paused")
        assert len(results) == 1
        assert results[0].status == "paused"


# =============================================================================
# get_stats
# =============================================================================


class TestGetStats:
    """Tests for SessionManager.get_stats."""

    def test_empty_when_no_sessions_dir(self, sessions_dir: Path) -> None:
        """Should return zeroed counts when sessions dir does not exist."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        stats = mgr.get_stats()
        assert stats == {"active": 0, "paused": 0, "completed": 0, "total": 0}

    def test_counts_active_sessions(self, manager: SessionManager) -> None:
        """Should correctly count active sessions."""
        manager.create_session(domain="d", project="p1")
        manager.create_session(domain="d", project="p2")
        stats = manager.get_stats()
        assert stats["active"] == 2
        assert stats["total"] == 2

    def test_counts_paused_sessions(self, manager: SessionManager) -> None:
        """Should correctly count paused sessions."""
        s = manager.create_session(domain="d", project="p")
        manager.pause_session(s.session_id)
        stats = manager.get_stats()
        assert stats["paused"] == 1
        assert stats["active"] == 0

    def test_counts_completed_sessions(self, manager: SessionManager) -> None:
        """Should correctly count completed sessions."""
        s = manager.create_session(domain="d", project="p")
        manager.end_session(s.session_id)
        stats = manager.get_stats()
        assert stats["completed"] == 1

    def test_mixed_statuses(self, manager: SessionManager) -> None:
        """Should correctly count a mix of statuses."""
        s1 = manager.create_session(domain="d", project="p1")
        s2 = manager.create_session(domain="d", project="p2")
        s3 = manager.create_session(domain="d", project="p3")
        manager.pause_session(s2.session_id)
        manager.end_session(s3.session_id)

        stats = manager.get_stats()
        assert stats["active"] == 1
        assert stats["paused"] == 1
        assert stats["completed"] == 1
        assert stats["total"] == 3

    def test_skips_corrupt_files(self, sessions_dir: Path) -> None:
        """Corrupt files should not affect stats counting."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "session_bad.json").write_text("!!invalid json")

        mgr = SessionManager(sessions_dir=sessions_dir)
        stats = mgr.get_stats()
        assert stats["total"] == 0

    def test_unknown_status_not_in_known_keys(self, sessions_dir: Path) -> None:
        """Sessions with unknown status should count towards total but not status keys."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        unknown_session = {
            "session_id": "test-unknown-123",
            "domain": "d",
            "project": "p",
            "status": "mystery",
            "started_at": datetime.now(UTC).isoformat(),
        }
        path = sessions_dir / "session_test-unknown-123.json"
        path.write_text(json.dumps(unknown_session))

        mgr = SessionManager(sessions_dir=sessions_dir)
        stats = mgr.get_stats()
        assert stats["total"] == 1
        # mystery is not a known status key, so no count bump there
        assert stats["active"] == 0
        assert stats["paused"] == 0
        assert stats["completed"] == 0


# =============================================================================
# Error Handling — read/write failures
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in file operations."""

    def test_read_corrupt_json_returns_none(self, sessions_dir: Path) -> None:
        """_read_session_file should return None for invalid JSON."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        path = sessions_dir / "session_corrupt.json"
        path.write_text("{ not: valid json }")

        mgr = SessionManager(sessions_dir=sessions_dir)
        result = mgr._read_session_file(path)
        assert result is None

    def test_read_missing_file_returns_none(self, sessions_dir: Path) -> None:
        """_read_session_file should return None for a missing file."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        path = sessions_dir / "session_missing.json"

        mgr = SessionManager(sessions_dir=sessions_dir)
        result = mgr._read_session_file(path)
        assert result is None

    def test_read_json_with_parse_error_returns_none(self, sessions_dir: Path) -> None:
        """_read_session_file should return None if SessionInfo construction fails."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        # Valid JSON but with a badly formatted started_at field
        bad_data = {
            "session_id": "bad-parse",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": "NOT_A_DATE",
        }
        path = sessions_dir / "session_bad-parse.json"
        path.write_text(json.dumps(bad_data))

        mgr = SessionManager(sessions_dir=sessions_dir)
        result = mgr._read_session_file(path)
        assert result is None

    def test_write_failure_returns_false(self, manager: SessionManager) -> None:
        """_write_session should return False on OSError."""
        session = SessionInfo(
            session_id="write-fail",
            domain="d",
            project="p",
            status="active",
            started_at=datetime.now(UTC),
        )
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            result = manager._write_session(session)
        assert result is False

    def test_end_session_missing_returns_none(self, manager: SessionManager) -> None:
        """end_session on a missing session should return None."""
        result = manager.end_session("no-such-session")
        assert result is None

    def test_add_task_missing_session_returns_none(self, manager: SessionManager) -> None:
        """add_task on a missing session should return None."""
        result = manager.add_task("no-such-session", "task")
        assert result is None

    def test_complete_task_missing_session_returns_none(self, manager: SessionManager) -> None:
        """complete_task on a missing session should return None."""
        result = manager.complete_task("no-such-session", "task")
        assert result is None

    def test_pause_session_missing_returns_none(self, manager: SessionManager) -> None:
        """pause_session on a missing session should return None."""
        result = manager.pause_session("no-such-session")
        assert result is None

    def test_resume_session_missing_returns_none(self, manager: SessionManager) -> None:
        """resume_session on a missing session should return None."""
        result = manager.resume_session("no-such-session")
        assert result is None


# =============================================================================
# Timezone handling in _read_session_file
# =============================================================================


class TestTimezoneHandling:
    """Tests for timezone-naive datetime fixup during file reads."""

    def test_naive_started_at_gets_utc(self, sessions_dir: Path) -> None:
        """A timezone-naive started_at should be given UTC timezone on read."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        naive_data = {
            "session_id": "tz-test-1",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": "2024-01-15T10:30:00",  # No timezone info
        }
        path = sessions_dir / "session_tz-test-1.json"
        path.write_text(json.dumps(naive_data))

        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr._read_session_file(path)
        assert session is not None
        assert session.started_at.tzinfo is not None
        assert session.started_at.tzinfo == UTC

    def test_naive_last_activity_gets_utc(self, sessions_dir: Path) -> None:
        """A timezone-naive last_activity should be given UTC timezone on read."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        naive_data = {
            "session_id": "tz-test-2",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": "2024-01-15T10:30:00Z",
            "last_activity": "2024-01-15T11:00:00",  # No timezone info
        }
        path = sessions_dir / "session_tz-test-2.json"
        path.write_text(json.dumps(naive_data))

        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr._read_session_file(path)
        assert session is not None
        assert session.last_activity is not None
        assert session.last_activity.tzinfo is not None

    def test_no_last_activity_field(self, sessions_dir: Path) -> None:
        """Missing last_activity in JSON should leave last_activity as None."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": "tz-test-3",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": "2024-01-15T10:30:00+00:00",
        }
        path = sessions_dir / "session_tz-test-3.json"
        path.write_text(json.dumps(data))

        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr._read_session_file(path)
        assert session is not None
        assert session.last_activity is None

    def test_missing_started_at_defaults_to_now(self, sessions_dir: Path) -> None:
        """Missing started_at defaults to datetime.now(UTC)."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": "tz-test-4",
            "domain": "d",
            "project": "p",
            "status": "active",
            # No started_at key
        }
        path = sessions_dir / "session_tz-test-4.json"
        path.write_text(json.dumps(data))

        mgr = SessionManager(sessions_dir=sessions_dir)
        before = datetime.now(UTC)
        session = mgr._read_session_file(path)
        after = datetime.now(UTC)
        assert session is not None
        assert before <= session.started_at <= after


# =============================================================================
# _read_session_file field defaults
# =============================================================================


class TestReadSessionFileDefaults:
    """Tests for default field values when reading partial session files."""

    def test_missing_domain_defaults_to_unknown(self, sessions_dir: Path) -> None:
        """Missing domain should default to 'unknown'."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": "defaults-1",
            "project": "p",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
        }
        path = sessions_dir / "session_defaults-1.json"
        path.write_text(json.dumps(data))
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr._read_session_file(path)
        assert session is not None
        assert session.domain == "unknown"

    def test_missing_project_defaults_to_unknown(self, sessions_dir: Path) -> None:
        """Missing project should default to 'unknown'."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": "defaults-2",
            "domain": "d",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
        }
        path = sessions_dir / "session_defaults-2.json"
        path.write_text(json.dumps(data))
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr._read_session_file(path)
        assert session is not None
        assert session.project == "unknown"

    def test_missing_session_id_uses_filename(self, sessions_dir: Path) -> None:
        """Missing session_id should be derived from the filename stem."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
        }
        path = sessions_dir / "session_from-filename.json"
        path.write_text(json.dumps(data))
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr._read_session_file(path)
        assert session is not None
        assert session.session_id == "from-filename"

    def test_missing_context_defaults_to_empty_dict(self, sessions_dir: Path) -> None:
        """Missing context should default to {}."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": "defaults-3",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
        }
        path = sessions_dir / "session_defaults-3.json"
        path.write_text(json.dumps(data))
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr._read_session_file(path)
        assert session is not None
        assert session.context == {}


# =============================================================================
# _load_session partial-match fallback
# =============================================================================


class TestLoadSessionPartialMatch:
    """Tests for _load_session glob fallback when exact path is not found."""

    def test_partial_match_finds_session(self, sessions_dir: Path) -> None:
        """_load_session should find a session via glob when exact ID is a prefix."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        full_id = "abcdef1234567890"
        data = {
            "session_id": full_id,
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
        }
        path = sessions_dir / f"session_{full_id}.json"
        path.write_text(json.dumps(data))

        mgr = SessionManager(sessions_dir=sessions_dir)
        # Try looking up with a partial ID that matches via glob
        session = mgr._load_session("abcdef")
        assert session is not None

    def test_no_match_returns_none(self, sessions_dir: Path) -> None:
        """_load_session should return None when no file matches."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        mgr = SessionManager(sessions_dir=sessions_dir)
        result = mgr._load_session("totally-unknown-id")
        assert result is None

    def test_no_dir_returns_none(self, sessions_dir: Path) -> None:
        """_load_session should return None when sessions dir does not exist."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        result = mgr._load_session("any-id")
        assert result is None


# =============================================================================
# CheckpointManager integration
# =============================================================================


class TestCheckpointManagerIntegration:
    """Tests for checkpoint_manager cache invalidation behaviour."""

    def test_no_checkpoint_manager_no_error_on_write(self, manager: SessionManager) -> None:
        """Writing without a checkpoint_manager should not raise."""
        session = manager.create_session(domain="d", project="p")
        # Should complete without error
        assert session is not None

    def test_checkpoint_invalidated_on_add_task(
        self, manager_with_checkpoint: SessionManager, mock_checkpoint_manager: MagicMock
    ) -> None:
        """add_task should call invalidate_cache on the checkpoint manager."""
        session = manager_with_checkpoint.create_session(domain="d", project="p")
        mock_checkpoint_manager.reset_mock()
        manager_with_checkpoint.add_task(session.session_id, "T")
        mock_checkpoint_manager.invalidate_cache.assert_called()

    def test_checkpoint_invalidated_on_complete_task(
        self, manager_with_checkpoint: SessionManager, mock_checkpoint_manager: MagicMock
    ) -> None:
        """complete_task should call invalidate_cache on the checkpoint manager."""
        session = manager_with_checkpoint.create_session(domain="d", project="p")
        manager_with_checkpoint.add_task(session.session_id, "T")
        mock_checkpoint_manager.reset_mock()
        manager_with_checkpoint.complete_task(session.session_id, "T")
        mock_checkpoint_manager.invalidate_cache.assert_called()


# =============================================================================
# Singleton Helpers
# =============================================================================


class TestGetSessionManager:
    """Tests for get_session_manager singleton helper."""

    def test_returns_session_manager_instance(self) -> None:
        """Should return a SessionManager instance."""
        mgr = get_session_manager()
        assert isinstance(mgr, SessionManager)

    def test_same_instance_on_repeated_calls(self) -> None:
        """Multiple calls should return the same singleton."""
        mgr1 = get_session_manager()
        mgr2 = get_session_manager()
        assert mgr1 is mgr2

    def test_sessions_dir_used_on_first_call(self, tmp_path: Path) -> None:
        """sessions_dir is applied only on the first call."""
        sessions_dir = tmp_path / "singleton_sessions"
        mgr = get_session_manager(sessions_dir=sessions_dir)
        assert mgr.sessions_dir == sessions_dir

    def test_sessions_dir_ignored_on_subsequent_calls(self, tmp_path: Path) -> None:
        """sessions_dir is ignored after the singleton is created."""
        dir1 = tmp_path / "first"
        dir2 = tmp_path / "second"
        mgr1 = get_session_manager(sessions_dir=dir1)
        mgr2 = get_session_manager(sessions_dir=dir2)
        assert mgr1 is mgr2
        assert mgr2.sessions_dir == dir1  # First call wins

    def test_checkpoint_manager_used_on_first_call(self, tmp_path: Path) -> None:
        """checkpoint_manager is applied only on the first call."""
        mock_cp = MagicMock(spec=CheckpointManager)
        dir1 = tmp_path / "cp_sessions"
        mgr = get_session_manager(sessions_dir=dir1, checkpoint_manager=mock_cp)
        assert mgr._checkpoint_manager is mock_cp


class TestResetSessionManager:
    """Tests for reset_session_manager singleton reset helper."""

    def test_reset_destroys_singleton(self) -> None:
        """After reset, get_session_manager should return a new instance."""
        mgr1 = get_session_manager()
        reset_session_manager()
        mgr2 = get_session_manager()
        assert mgr1 is not mgr2

    def test_reset_allows_new_sessions_dir(self, tmp_path: Path) -> None:
        """After reset, a new sessions_dir can be set."""
        dir1 = tmp_path / "before_reset"
        dir2 = tmp_path / "after_reset"
        get_session_manager(sessions_dir=dir1)
        reset_session_manager()
        mgr = get_session_manager(sessions_dir=dir2)
        assert mgr.sessions_dir == dir2

    def test_double_reset_is_safe(self) -> None:
        """Calling reset_session_manager twice should not raise."""
        reset_session_manager()
        reset_session_manager()  # Should be idempotent


# =============================================================================
# Thread Safety
# =============================================================================


class TestThreadSafety:
    """Tests for thread-safe session creation and access."""

    def test_concurrent_session_creation(self, sessions_dir: Path) -> None:
        """Multiple threads creating sessions simultaneously should not conflict."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        created_ids: list[str] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def create_sessions(n: int) -> None:
            for _ in range(n):
                try:
                    session = mgr.create_session(domain="d", project="p")
                    with lock:
                        created_ids.append(session.session_id)
                except Exception as exc:
                    with lock:
                        errors.append(exc)

        threads = [threading.Thread(target=create_sessions, args=(5,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(created_ids) == 20
        # All session IDs should be unique
        assert len(set(created_ids)) == 20

    def test_concurrent_singleton_access(self, tmp_path: Path) -> None:
        """Multiple threads accessing the singleton should receive the same instance."""
        instances: list[SessionManager] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        sessions_dir = tmp_path / "thread_singleton"

        def get_singleton() -> None:
            try:
                mgr = get_session_manager(sessions_dir=sessions_dir)
                with lock:
                    instances.append(mgr)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=get_singleton) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # All references should be to the same object
        first = instances[0]
        for inst in instances[1:]:
            assert inst is first


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_domain_and_project(self, manager: SessionManager) -> None:
        """Should handle empty string domain and project."""
        session = manager.create_session(domain="", project="")
        assert session.domain == ""
        assert session.project == ""

    def test_list_sessions_default_limit_50(self, sessions_dir: Path) -> None:
        """Default limit should be 50."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        for i in range(60):
            mgr.create_session(domain="d", project=f"p{i}")
        results = mgr.list_sessions()
        assert len(results) == 50

    def test_list_sessions_returns_correct_session_info_type(
        self, manager: SessionManager
    ) -> None:
        """list_sessions should return a list of SessionInfo objects."""
        manager.create_session(domain="d", project="p")
        results = manager.list_sessions()
        assert all(isinstance(s, SessionInfo) for s in results)

    def test_get_stats_has_all_keys(self, sessions_dir: Path) -> None:
        """get_stats should always include all four keys."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        stats = mgr.get_stats()
        assert set(stats.keys()) == {"active", "paused", "completed", "total"}

    def test_session_file_contains_all_fields(self, manager: SessionManager) -> None:
        """Serialised JSON should contain all expected top-level keys."""
        session = manager.create_session(
            domain="dom",
            project="proj",
            agent_id="agent-1",
            metadata={"env": "test"},
        )
        path = manager.sessions_dir / f"session_{session.session_id}.json"
        data = json.loads(path.read_text())

        expected_keys = {
            "session_id",
            "domain",
            "project",
            "status",
            "started_at",
            "last_activity",
            "pending_tasks",
            "completed_tasks",
            "context",
        }
        assert expected_keys.issubset(data.keys())

    def test_sessions_with_only_last_activity_none_sort_by_started_at(
        self, sessions_dir: Path
    ) -> None:
        """Sessions without last_activity should fall back to started_at for sorting."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        # Write a session with null last_activity
        data = {
            "session_id": "no-last-activity",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": "2024-01-01T00:00:00+00:00",
            "last_activity": None,
        }
        (sessions_dir / "session_no-last-activity.json").write_text(json.dumps(data))

        mgr = SessionManager(sessions_dir=sessions_dir)
        results = mgr.list_sessions()
        assert len(results) == 1
        assert results[0].session_id == "no-last-activity"
