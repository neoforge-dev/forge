"""
Comprehensive tests for forge_harness.webhook_server.services.session_manager.

Coverage targets:
- create_session   - creates file on disk with correct fields
- end_session      - sets status to "completed", stamps last_activity
- pause_session    - sets status to "paused"
- resume_session   - sets status back to "active"
- add_task         - appends to pending_tasks
- complete_task    - moves from pending to completed; handles missing task
- list_sessions    - filter by status / domain / limit; sort order
- get_session      - hit and miss
- get_stats        - correct counts across statuses
- persistence      - data survives a new SessionManager instance
- concurrent       - multiple threads do not corrupt state
- singleton        - get_session_manager / reset_session_manager
- checkpoint_manager integration - cache invalidated on write
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from forge_harness.checkpoint_manager import SessionInfo
from forge_harness.webhook_server.services.session_manager import (
    SessionManager,
    get_session_manager,
    reset_session_manager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the singleton is cleared before and after every test."""
    reset_session_manager()
    yield
    reset_session_manager()


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for session files."""
    d = tmp_path / ".forge/sessions"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def manager(sessions_dir: Path) -> SessionManager:
    """Return a fresh SessionManager bound to a temp directory."""
    return SessionManager(sessions_dir=sessions_dir)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_session_file(sessions_dir: Path, session_id: str) -> dict:
    """Read the raw JSON for a session from disk."""
    path = sessions_dir / f"session_{session_id}.json"
    return json.loads(path.read_text())


# ===========================================================================
# create_session
# ===========================================================================


class TestCreateSession:
    def test_returns_session_info(self, manager: SessionManager):
        session = manager.create_session(domain="codeswiftr-com", project="interview-sim")
        assert isinstance(session, SessionInfo)

    def test_status_is_active(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        assert session.status == "active"

    def test_file_written_to_disk(self, manager: SessionManager, sessions_dir: Path):
        session = manager.create_session(domain="d", project="p")
        path = sessions_dir / f"session_{session.session_id}.json"
        assert path.exists(), "Session file must be written to disk"

    def test_file_content_matches_session(self, manager: SessionManager, sessions_dir: Path):
        session = manager.create_session(domain="forge", project="cli")
        data = _raw_session_file(sessions_dir, session.session_id)
        assert data["session_id"] == session.session_id
        assert data["domain"] == "forge"
        assert data["project"] == "cli"
        assert data["status"] == "active"
        assert data["pending_tasks"] == []
        assert data["completed_tasks"] == []

    def test_agent_id_stored_in_context(self, manager: SessionManager, sessions_dir: Path):
        session = manager.create_session(domain="d", project="p", agent_id="agent-42")
        data = _raw_session_file(sessions_dir, session.session_id)
        assert data["context"]["agent_id"] == "agent-42"

    def test_metadata_merged_into_context(self, manager: SessionManager, sessions_dir: Path):
        session = manager.create_session(
            domain="d",
            project="p",
            metadata={"sprint": 6, "tier": "gold"},
        )
        data = _raw_session_file(sessions_dir, session.session_id)
        assert data["context"]["sprint"] == 6
        assert data["context"]["tier"] == "gold"

    def test_unique_ids_per_call(self, manager: SessionManager):
        s1 = manager.create_session(domain="d", project="p")
        s2 = manager.create_session(domain="d", project="p")
        assert s1.session_id != s2.session_id

    def test_sessions_dir_created_if_missing(self, tmp_path: Path):
        nonexistent = tmp_path / "no_such_dir" / "sessions"
        mgr = SessionManager(sessions_dir=nonexistent)
        session = mgr.create_session(domain="d", project="p")
        assert nonexistent.exists()
        assert (nonexistent / f"session_{session.session_id}.json").exists()


# ===========================================================================
# get_session
# ===========================================================================


class TestGetSession:
    def test_returns_session_info_for_existing_id(self, manager: SessionManager):
        created = manager.create_session(domain="d", project="p")
        fetched = manager.get_session(created.session_id)
        assert fetched is not None
        assert fetched.session_id == created.session_id

    def test_returns_none_for_unknown_id(self, manager: SessionManager):
        result = manager.get_session("does-not-exist-xxxx")
        assert result is None

    def test_returns_none_when_dir_empty(self, sessions_dir: Path):
        mgr = SessionManager(sessions_dir=sessions_dir)
        assert mgr.get_session("any-id") is None

    def test_all_fields_correct(self, manager: SessionManager):
        created = manager.create_session(domain="forge", project="harness", agent_id="ag-1")
        fetched = manager.get_session(created.session_id)
        assert fetched is not None
        assert fetched.domain == "forge"
        assert fetched.project == "harness"
        assert fetched.context["agent_id"] == "ag-1"


# ===========================================================================
# end_session
# ===========================================================================


class TestEndSession:
    def test_status_becomes_completed(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        updated = manager.end_session(session.session_id)
        assert updated is not None
        assert updated.status == "completed"

    def test_last_activity_updated(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        before = session.last_activity
        time.sleep(0.01)
        updated = manager.end_session(session.session_id)
        assert updated is not None
        assert updated.last_activity is not None
        assert updated.last_activity >= before  # type: ignore[operator]

    def test_persisted_to_disk(self, manager: SessionManager, sessions_dir: Path):
        session = manager.create_session(domain="d", project="p")
        manager.end_session(session.session_id)
        data = _raw_session_file(sessions_dir, session.session_id)
        assert data["status"] == "completed"

    def test_returns_none_for_unknown_id(self, manager: SessionManager):
        result = manager.end_session("ghost-session")
        assert result is None


# ===========================================================================
# pause_session
# ===========================================================================


class TestPauseSession:
    def test_status_becomes_paused(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        updated = manager.pause_session(session.session_id)
        assert updated is not None
        assert updated.status == "paused"

    def test_last_activity_updated(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        before = session.last_activity
        time.sleep(0.01)
        updated = manager.pause_session(session.session_id)
        assert updated is not None
        assert updated.last_activity >= before  # type: ignore[operator]

    def test_persisted_to_disk(self, manager: SessionManager, sessions_dir: Path):
        session = manager.create_session(domain="d", project="p")
        manager.pause_session(session.session_id)
        data = _raw_session_file(sessions_dir, session.session_id)
        assert data["status"] == "paused"

    def test_returns_none_for_unknown_id(self, manager: SessionManager):
        result = manager.pause_session("ghost")
        assert result is None


# ===========================================================================
# resume_session
# ===========================================================================


class TestResumeSession:
    def test_status_becomes_active(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        manager.pause_session(session.session_id)
        updated = manager.resume_session(session.session_id)
        assert updated is not None
        assert updated.status == "active"

    def test_last_activity_updated(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        manager.pause_session(session.session_id)
        time.sleep(0.01)
        updated = manager.resume_session(session.session_id)
        assert updated is not None
        assert updated.last_activity is not None

    def test_persisted_to_disk(self, manager: SessionManager, sessions_dir: Path):
        session = manager.create_session(domain="d", project="p")
        manager.pause_session(session.session_id)
        manager.resume_session(session.session_id)
        data = _raw_session_file(sessions_dir, session.session_id)
        assert data["status"] == "active"

    def test_returns_none_for_unknown_id(self, manager: SessionManager):
        result = manager.resume_session("ghost")
        assert result is None


# ===========================================================================
# Full lifecycle: active -> paused -> active -> completed
# ===========================================================================


class TestSessionLifecycle:
    def test_full_lifecycle(self, manager: SessionManager):
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

    def test_re_open_completed_session(self, manager: SessionManager):
        """resume_session on a completed session transitions it back to active."""
        session = manager.create_session(domain="d", project="p")
        manager.end_session(session.session_id)
        reopened = manager.resume_session(session.session_id)
        assert reopened is not None
        assert reopened.status == "active"


# ===========================================================================
# add_task
# ===========================================================================


class TestAddTask:
    def test_task_appears_in_pending(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        updated = manager.add_task(session.session_id, "Implement OAuth")
        assert updated is not None
        assert "Implement OAuth" in updated.pending_tasks

    def test_task_persisted_to_disk(self, manager: SessionManager, sessions_dir: Path):
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Write tests")
        data = _raw_session_file(sessions_dir, session.session_id)
        assert "Write tests" in data["pending_tasks"]

    def test_multiple_tasks_accumulate(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Task A")
        manager.add_task(session.session_id, "Task B")
        updated = manager.add_task(session.session_id, "Task C")
        assert updated is not None
        assert updated.pending_tasks == ["Task A", "Task B", "Task C"]

    def test_duplicate_task_allowed(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Retry build")
        updated = manager.add_task(session.session_id, "Retry build")
        assert updated is not None
        assert updated.pending_tasks.count("Retry build") == 2

    def test_returns_none_for_unknown_id(self, manager: SessionManager):
        result = manager.add_task("ghost", "some task")
        assert result is None


# ===========================================================================
# complete_task
# ===========================================================================


class TestCompleteTask:
    def test_moves_task_from_pending_to_completed(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Implement OAuth")
        updated = manager.complete_task(session.session_id, "Implement OAuth")
        assert updated is not None
        assert "Implement OAuth" not in updated.pending_tasks
        assert "Implement OAuth" in updated.completed_tasks

    def test_persisted_to_disk(self, manager: SessionManager, sessions_dir: Path):
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Deploy")
        manager.complete_task(session.session_id, "Deploy")
        data = _raw_session_file(sessions_dir, session.session_id)
        assert "Deploy" not in data["pending_tasks"]
        assert "Deploy" in data["completed_tasks"]

    def test_removes_only_first_occurrence(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        manager.add_task(session.session_id, "Retry")
        manager.add_task(session.session_id, "Retry")
        updated = manager.complete_task(session.session_id, "Retry")
        assert updated is not None
        assert updated.pending_tasks == ["Retry"]
        assert updated.completed_tasks == ["Retry"]

    def test_out_of_band_completion(self, manager: SessionManager):
        """Completing a task not in pending_tasks still adds it to completed."""
        session = manager.create_session(domain="d", project="p")
        updated = manager.complete_task(session.session_id, "Unregistered task")
        assert updated is not None
        assert "Unregistered task" in updated.completed_tasks

    def test_returns_none_for_unknown_id(self, manager: SessionManager):
        result = manager.complete_task("ghost", "some task")
        assert result is None


# ===========================================================================
# list_sessions
# ===========================================================================


class TestListSessions:
    def _create_sessions(self, manager: SessionManager):
        """Create 3 active + 1 paused + 1 completed session across 2 domains.

        forge domain:     harness (active), cli (active), dashboard (paused) = 3 total
        codeswiftr domain: is (active), voice (completed) = 2 total
        """
        a1 = manager.create_session(domain="forge", project="harness")
        a2 = manager.create_session(domain="forge", project="cli")
        a3 = manager.create_session(domain="codeswiftr", project="is")
        p1 = manager.create_session(domain="forge", project="dashboard")
        c1 = manager.create_session(domain="codeswiftr", project="voice")
        manager.pause_session(p1.session_id)
        manager.end_session(c1.session_id)
        return a1, a2, a3, p1, c1

    def test_returns_all_sessions_by_default(self, manager: SessionManager):
        self._create_sessions(manager)
        sessions = manager.list_sessions()
        assert len(sessions) == 5

    def test_filter_by_active_status(self, manager: SessionManager):
        self._create_sessions(manager)
        sessions = manager.list_sessions(status="active")
        assert len(sessions) == 3
        assert all(s.status == "active" for s in sessions)

    def test_filter_by_paused_status(self, manager: SessionManager):
        self._create_sessions(manager)
        sessions = manager.list_sessions(status="paused")
        assert len(sessions) == 1
        assert sessions[0].status == "paused"

    def test_filter_by_completed_status(self, manager: SessionManager):
        self._create_sessions(manager)
        sessions = manager.list_sessions(status="completed")
        assert len(sessions) == 1
        assert sessions[0].status == "completed"

    def test_filter_by_domain(self, manager: SessionManager):
        self._create_sessions(manager)
        sessions = manager.list_sessions(domain="forge")
        # harness (active) + cli (active) + dashboard (paused) = 3
        assert len(sessions) == 3
        assert all(s.domain == "forge" for s in sessions)

    def test_filter_by_domain_and_status(self, manager: SessionManager):
        self._create_sessions(manager)
        # forge + active: harness, cli = 2
        sessions = manager.list_sessions(status="active", domain="forge")
        assert len(sessions) == 2

    def test_limit_respected(self, manager: SessionManager):
        for _ in range(10):
            manager.create_session(domain="d", project="p")
        sessions = manager.list_sessions(limit=3)
        assert len(sessions) == 3

    def test_default_limit_is_50(self, manager: SessionManager):
        for _ in range(55):
            manager.create_session(domain="d", project="p")
        sessions = manager.list_sessions()
        assert len(sessions) == 50

    def test_sorted_most_recent_first(self, manager: SessionManager):
        s1 = manager.create_session(domain="d", project="p")
        time.sleep(0.02)
        s2 = manager.create_session(domain="d", project="p")
        sessions = manager.list_sessions()
        ids = [s.session_id for s in sessions]
        assert ids[0] == s2.session_id
        assert ids[1] == s1.session_id

    def test_empty_when_dir_missing(self, tmp_path: Path):
        mgr = SessionManager(sessions_dir=tmp_path / "no_such_dir")
        assert mgr.list_sessions() == []


# ===========================================================================
# get_stats
# ===========================================================================


class TestGetStats:
    def test_all_zeros_when_empty(self, manager: SessionManager):
        stats = manager.get_stats()
        assert stats == {"active": 0, "paused": 0, "completed": 0, "total": 0}

    def test_counts_active_sessions(self, manager: SessionManager):
        manager.create_session(domain="d", project="p")
        manager.create_session(domain="d", project="p")
        stats = manager.get_stats()
        assert stats["active"] == 2
        assert stats["total"] == 2

    def test_counts_all_statuses(self, manager: SessionManager):
        s1 = manager.create_session(domain="d", project="p")
        s2 = manager.create_session(domain="d", project="p")
        s3 = manager.create_session(domain="d", project="p")
        manager.pause_session(s2.session_id)
        manager.end_session(s3.session_id)

        stats = manager.get_stats()
        assert stats["active"] == 1
        assert stats["paused"] == 1
        assert stats["completed"] == 1
        assert stats["total"] == 3

    def test_zeros_when_dir_missing(self, tmp_path: Path):
        mgr = SessionManager(sessions_dir=tmp_path / "no_such_dir")
        stats = mgr.get_stats()
        assert stats["total"] == 0

    def test_unknown_status_not_counted_in_named_keys(self, sessions_dir: Path):
        """Files with unknown status contribute to total but not named keys."""
        path = sessions_dir / "session_weird.json"
        path.write_text(
            json.dumps(
                {
                    "session_id": "weird",
                    "domain": "d",
                    "project": "p",
                    "status": "zombie",
                    "started_at": datetime.now(UTC).isoformat(),
                }
            )
        )
        mgr = SessionManager(sessions_dir=sessions_dir)
        stats = mgr.get_stats()
        assert stats["total"] == 1
        assert stats["active"] == 0
        assert stats["paused"] == 0
        assert stats["completed"] == 0


# ===========================================================================
# Persistence across instances
# ===========================================================================


class TestPersistenceAcrossInstances:
    def test_data_survives_new_instance(self, sessions_dir: Path):
        mgr1 = SessionManager(sessions_dir=sessions_dir)
        s = mgr1.create_session(domain="persist-domain", project="persist-proj")
        mgr1.add_task(s.session_id, "Write docs")
        mgr1.pause_session(s.session_id)

        mgr2 = SessionManager(sessions_dir=sessions_dir)
        loaded = mgr2.get_session(s.session_id)

        assert loaded is not None
        assert loaded.session_id == s.session_id
        assert loaded.domain == "persist-domain"
        assert loaded.status == "paused"
        assert "Write docs" in loaded.pending_tasks

    def test_completed_tasks_persist(self, sessions_dir: Path):
        mgr1 = SessionManager(sessions_dir=sessions_dir)
        s = mgr1.create_session(domain="d", project="p")
        mgr1.add_task(s.session_id, "Ship it")
        mgr1.complete_task(s.session_id, "Ship it")

        mgr2 = SessionManager(sessions_dir=sessions_dir)
        loaded = mgr2.get_session(s.session_id)
        assert loaded is not None
        assert "Ship it" in loaded.completed_tasks
        assert "Ship it" not in loaded.pending_tasks

    def test_list_sessions_across_instances(self, sessions_dir: Path):
        mgr1 = SessionManager(sessions_dir=sessions_dir)
        for i in range(3):
            mgr1.create_session(domain="d", project=f"p{i}")

        mgr2 = SessionManager(sessions_dir=sessions_dir)
        sessions = mgr2.list_sessions()
        assert len(sessions) == 3

    def test_stats_across_instances(self, sessions_dir: Path):
        mgr1 = SessionManager(sessions_dir=sessions_dir)
        s = mgr1.create_session(domain="d", project="p")
        mgr1.end_session(s.session_id)

        mgr2 = SessionManager(sessions_dir=sessions_dir)
        stats = mgr2.get_stats()
        assert stats["completed"] == 1
        assert stats["total"] == 1


# ===========================================================================
# Concurrent access
# ===========================================================================


class TestConcurrentAccess:
    def test_concurrent_creates_no_corruption(self, sessions_dir: Path):
        """Multiple threads creating sessions must not corrupt each other."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        ids: list[str] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker():
            try:
                s = mgr.create_session(domain="d", project="p")
                with lock:
                    ids.append(s.session_id)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(ids) == 20
        assert len(set(ids)) == 20, "All session IDs must be unique"

    def test_concurrent_add_and_complete_tasks(self, sessions_dir: Path):
        """Concurrent add_task and complete_task must not corrupt task lists."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr.create_session(domain="d", project="p")
        sid = session.session_id

        # Pre-populate tasks
        for i in range(10):
            mgr.add_task(sid, f"task-{i}")

        errors: list[Exception] = []
        lock = threading.Lock()

        def completer(task_name: str):
            try:
                mgr.complete_task(sid, task_name)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=completer, args=(f"task-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

        final = mgr.get_session(sid)
        assert final is not None
        assert len(final.completed_tasks) == 10
        assert final.pending_tasks == []

    def test_concurrent_status_transitions(self, sessions_dir: Path):
        """Multiple threads performing status transitions must not corrupt files."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        sessions = [mgr.create_session(domain="d", project="p") for _ in range(5)]
        errors: list[Exception] = []
        lock = threading.Lock()

        def transition(sid: str):
            try:
                mgr.pause_session(sid)
                mgr.resume_session(sid)
                mgr.end_session(sid)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=transition, args=(s.session_id,)) for s in sessions]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        for s in sessions:
            final = mgr.get_session(s.session_id)
            assert final is not None
            assert final.status == "completed"


# ===========================================================================
# Singleton pattern
# ===========================================================================


class TestSingletonPattern:
    def test_get_session_manager_returns_same_instance(self, sessions_dir: Path):
        mgr1 = get_session_manager(sessions_dir=sessions_dir)
        mgr2 = get_session_manager(sessions_dir=sessions_dir)
        assert mgr1 is mgr2

    def test_reset_clears_singleton(self, sessions_dir: Path):
        mgr1 = get_session_manager(sessions_dir=sessions_dir)
        reset_session_manager()
        mgr2 = get_session_manager(sessions_dir=sessions_dir)
        assert mgr1 is not mgr2

    def test_second_call_ignores_new_kwargs(self, tmp_path: Path):
        """After the first call, new constructor args are silently ignored."""
        dir_a = tmp_path / "sessions_a"
        dir_b = tmp_path / "sessions_b"
        dir_a.mkdir()
        dir_b.mkdir()

        mgr1 = get_session_manager(sessions_dir=dir_a)
        mgr2 = get_session_manager(sessions_dir=dir_b)  # should be ignored

        assert mgr1 is mgr2
        assert mgr1.sessions_dir == dir_a

    def test_singleton_thread_safe(self, sessions_dir: Path):
        """Concurrent get_session_manager calls return the same object."""
        results: list[SessionManager] = []
        lock = threading.Lock()

        def getter():
            mgr = get_session_manager(sessions_dir=sessions_dir)
            with lock:
                results.append(mgr)

        threads = [threading.Thread(target=getter) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(m is results[0] for m in results), "All threads must share one instance"


# ===========================================================================
# CheckpointManager integration
# ===========================================================================


class TestCheckpointManagerIntegration:
    def test_cache_invalidated_on_create(self, sessions_dir: Path):
        mock_cp = MagicMock()
        mgr = SessionManager(sessions_dir=sessions_dir, checkpoint_manager=mock_cp)
        mgr.create_session(domain="d", project="p")
        mock_cp.invalidate_cache.assert_called()

    def test_cache_invalidated_on_end(self, sessions_dir: Path):
        mock_cp = MagicMock()
        mgr = SessionManager(sessions_dir=sessions_dir, checkpoint_manager=mock_cp)
        s = mgr.create_session(domain="d", project="p")
        mock_cp.reset_mock()
        mgr.end_session(s.session_id)
        mock_cp.invalidate_cache.assert_called()

    def test_cache_invalidated_on_pause(self, sessions_dir: Path):
        mock_cp = MagicMock()
        mgr = SessionManager(sessions_dir=sessions_dir, checkpoint_manager=mock_cp)
        s = mgr.create_session(domain="d", project="p")
        mock_cp.reset_mock()
        mgr.pause_session(s.session_id)
        mock_cp.invalidate_cache.assert_called()

    def test_cache_invalidated_on_resume(self, sessions_dir: Path):
        mock_cp = MagicMock()
        mgr = SessionManager(sessions_dir=sessions_dir, checkpoint_manager=mock_cp)
        s = mgr.create_session(domain="d", project="p")
        mock_cp.reset_mock()
        mgr.resume_session(s.session_id)
        mock_cp.invalidate_cache.assert_called()

    def test_cache_invalidated_on_add_task(self, sessions_dir: Path):
        mock_cp = MagicMock()
        mgr = SessionManager(sessions_dir=sessions_dir, checkpoint_manager=mock_cp)
        s = mgr.create_session(domain="d", project="p")
        mock_cp.reset_mock()
        mgr.add_task(s.session_id, "task")
        mock_cp.invalidate_cache.assert_called()

    def test_cache_invalidated_on_complete_task(self, sessions_dir: Path):
        mock_cp = MagicMock()
        mgr = SessionManager(sessions_dir=sessions_dir, checkpoint_manager=mock_cp)
        s = mgr.create_session(domain="d", project="p")
        mgr.add_task(s.session_id, "task")
        mock_cp.reset_mock()
        mgr.complete_task(s.session_id, "task")
        mock_cp.invalidate_cache.assert_called()

    def test_no_crash_without_checkpoint_manager(self, sessions_dir: Path):
        """SessionManager must work correctly when no CheckpointManager is injected."""
        mgr = SessionManager(sessions_dir=sessions_dir, checkpoint_manager=None)
        s = mgr.create_session(domain="d", project="p")
        mgr.end_session(s.session_id)
        loaded = mgr.get_session(s.session_id)
        assert loaded is not None
        assert loaded.status == "completed"


# ===========================================================================
# Edge cases and error handling
# ===========================================================================


class TestEdgeCases:
    def test_corrupt_file_skipped_in_list(self, sessions_dir: Path):
        """Unreadable JSON is silently skipped in list_sessions."""
        (sessions_dir / "session_corrupt.json").write_text("NOT_JSON{{{{")
        mgr = SessionManager(sessions_dir=sessions_dir)
        mgr.create_session(domain="d", project="p")
        sessions = mgr.list_sessions()
        # Only the valid session should appear
        assert len(sessions) == 1

    def test_corrupt_file_skipped_in_stats(self, sessions_dir: Path):
        (sessions_dir / "session_corrupt.json").write_text("{broken")
        mgr = SessionManager(sessions_dir=sessions_dir)
        stats = mgr.get_stats()
        assert stats["total"] == 0

    def test_get_session_handles_corrupt_file(self, sessions_dir: Path):
        (sessions_dir / "session_bad-id.json").write_text("{bad json}")
        mgr = SessionManager(sessions_dir=sessions_dir)
        result = mgr.get_session("bad-id")
        assert result is None

    def test_sessions_dir_created_on_create(self, tmp_path: Path):
        d = tmp_path / "deep" / "nested" / "sessions"
        mgr = SessionManager(sessions_dir=d)
        mgr.create_session(domain="d", project="p")
        assert d.exists()

    def test_context_defaults_to_empty_dict(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        assert isinstance(session.context, dict)

    def test_pending_tasks_defaults_to_empty_list(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        assert session.pending_tasks == []

    def test_completed_tasks_defaults_to_empty_list(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        assert session.completed_tasks == []

    def test_timezone_aware_timestamps(self, manager: SessionManager):
        session = manager.create_session(domain="d", project="p")
        assert session.started_at.tzinfo is not None
        assert session.last_activity is not None
        assert session.last_activity.tzinfo is not None

    def test_default_sessions_dir_when_none_given(self):
        """SessionManager(sessions_dir=None) defaults to a relative .forge/sessions path."""
        mgr = SessionManager(sessions_dir=None)
        assert mgr.sessions_dir == Path(".forge/sessions")

    def test_naive_datetime_in_file_gets_utc_tz(self, sessions_dir: Path):
        """ISO timestamps without timezone info are treated as UTC."""
        path = sessions_dir / "session_naive-dt.json"
        # Write a file with timezone-naive timestamps
        path.write_text(
            json.dumps(
                {
                    "session_id": "naive-dt",
                    "domain": "d",
                    "project": "p",
                    "status": "active",
                    "started_at": "2026-01-15T10:00:00",  # no tzinfo
                    "last_activity": "2026-01-15T11:00:00",  # no tzinfo
                }
            )
        )
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr.get_session("naive-dt")
        assert session is not None
        assert session.started_at.tzinfo is not None
        assert session.last_activity is not None
        assert session.last_activity.tzinfo is not None

    def test_partial_match_fallback_in_load_session(self, sessions_dir: Path):
        """_load_session falls back to glob matching when exact file not found."""
        # Write a file whose name contains the ID but not in exact format
        path = sessions_dir / "session_partial-abc123-extra.json"
        path.write_text(
            json.dumps(
                {
                    "session_id": "partial-abc123-extra",
                    "domain": "d",
                    "project": "p",
                    "status": "active",
                    "started_at": datetime.now(UTC).isoformat(),
                }
            )
        )
        mgr = SessionManager(sessions_dir=sessions_dir)
        # Query by the partial ID that matches via glob
        session = mgr.get_session("abc123")
        assert session is not None

    def test_write_failure_returns_false(self, sessions_dir: Path):
        """_write_session returns False when OSError is raised (mocked)."""
        from unittest.mock import patch

        mgr = SessionManager(sessions_dir=sessions_dir)
        s = mgr.create_session(domain="d", project="p")
        s.status = "paused"

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = mgr._write_session(s)

        assert result is False

    def test_parse_error_in_read_session_file_returns_none(self, sessions_dir: Path):
        """The outer except in _read_session_file catches unexpected parse errors."""
        path = sessions_dir / "session_bad-types.json"
        # Write valid JSON but with a field type that causes datetime.fromisoformat to fail
        path.write_text(
            json.dumps(
                {
                    "session_id": "bad-types",
                    "domain": "d",
                    "project": "p",
                    "status": "active",
                    "started_at": 12345,  # int, not a string — fromisoformat will fail
                }
            )
        )
        mgr = SessionManager(sessions_dir=sessions_dir)
        # get_session or list_sessions should not raise; it returns None / skips
        result = mgr.get_session("bad-types")
        assert result is None
