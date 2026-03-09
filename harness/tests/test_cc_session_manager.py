"""Comprehensive tests for SessionManager (Command Center service layer).

Targets forge_harness/webhook_server/services/session_manager.py.

Coverage goals (70%+ per task spec):
  - All public methods: create_session, get_session, end_session, pause_session,
    resume_session, add_task, complete_task, list_sessions, get_stats
  - Private helpers: _ensure_dir, _session_path, _read_session_file, _write_session,
    _load_session
  - Singleton helpers: get_session_manager, reset_session_manager
  - Error / exception paths:
      * Corrupt JSON session file (JSONDecodeError)
      * OSError on write
      * Session not found paths for every mutating method
      * Missing sessions directory for list/stats
      * Partial-match fallback in _load_session
  - Thread-safety assertions
  - Edge cases: duplicate tasks, out-of-band completion, timezone handling,
    missing timezone info, metadata merging, agent_id in context
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

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


@pytest.fixture()
def sessions_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".forge/sessions"
    d.mkdir()
    return d


@pytest.fixture()
def mgr(sessions_dir: Path) -> SessionManager:
    """Fresh SessionManager backed by a temp directory."""
    return SessionManager(sessions_dir=sessions_dir)


@pytest.fixture(autouse=True)
def reset_singleton_before_after():
    """Ensure the global singleton is reset for every test."""
    reset_session_manager()
    yield
    reset_session_manager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_file(sessions_dir: Path, session_id: str, **fields) -> dict:
    """Write a minimal session JSON file directly to disk."""
    now = datetime.now(UTC).isoformat()
    data: dict = {
        "session_id": session_id,
        "domain": fields.get("domain", "test-domain"),
        "project": fields.get("project", "test-project"),
        "status": fields.get("status", "active"),
        "started_at": fields.get("started_at", now),
        "last_activity": fields.get("last_activity", now),
        "completed_tasks": fields.get("completed_tasks", []),
        "pending_tasks": fields.get("pending_tasks", []),
        "context": fields.get("context", {}),
    }
    data.update({k: v for k, v in fields.items() if k not in data})
    (sessions_dir / f"session_{session_id}.json").write_text(json.dumps(data, indent=2))
    return data


# ===========================================================================
# SessionManager.__init__
# ===========================================================================


class TestInit:
    def test_default_sessions_dir_is_forge_sessions(self, tmp_path: Path):
        mgr = SessionManager(sessions_dir=tmp_path / ".forge/sessions")
        assert mgr.sessions_dir == tmp_path / ".forge/sessions"

    def test_accepts_string_path(self, tmp_path: Path):
        mgr = SessionManager(sessions_dir=str(tmp_path / "my_sessions"))
        assert mgr.sessions_dir == tmp_path / "my_sessions"

    def test_checkpoint_manager_stored(self):
        mock_cm = MagicMock()
        mgr = SessionManager(checkpoint_manager=mock_cm)
        assert mgr._checkpoint_manager is mock_cm

    def test_no_checkpoint_manager_by_default(self, sessions_dir: Path):
        mgr = SessionManager(sessions_dir=sessions_dir)
        assert mgr._checkpoint_manager is None

    def test_lock_is_rlock(self, sessions_dir: Path):
        """Verify internal lock is an RLock (re-entrant)."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        # RLock supports re-entrant acquisition
        with mgr._lock:
            with mgr._lock:
                pass  # No deadlock = RLock confirmed


# ===========================================================================
# create_session
# ===========================================================================


class TestCreateSession:
    def test_returns_session_info(self, mgr: SessionManager):
        session = mgr.create_session(domain="d", project="p")
        assert isinstance(session, SessionInfo)

    def test_status_is_active(self, mgr: SessionManager):
        session = mgr.create_session(domain="d", project="p")
        assert session.status == "active"

    def test_domain_and_project_stored(self, mgr: SessionManager):
        session = mgr.create_session(domain="mydom", project="myproj")
        assert session.domain == "mydom"
        assert session.project == "myproj"

    def test_session_id_is_uuid_string(self, mgr: SessionManager):
        import uuid
        session = mgr.create_session(domain="d", project="p")
        # Should not raise
        uuid.UUID(session.session_id)

    def test_started_at_is_utc_aware(self, mgr: SessionManager):
        session = mgr.create_session(domain="d", project="p")
        assert session.started_at.tzinfo is not None

    def test_last_activity_is_utc_aware(self, mgr: SessionManager):
        session = mgr.create_session(domain="d", project="p")
        assert session.last_activity is not None
        assert session.last_activity.tzinfo is not None

    def test_pending_tasks_empty(self, mgr: SessionManager):
        session = mgr.create_session(domain="d", project="p")
        assert session.pending_tasks == []

    def test_completed_tasks_empty(self, mgr: SessionManager):
        session = mgr.create_session(domain="d", project="p")
        assert session.completed_tasks == []

    def test_agent_id_stored_in_context(self, mgr: SessionManager):
        session = mgr.create_session(domain="d", project="p", agent_id="agent-007")
        assert session.context.get("agent_id") == "agent-007"

    def test_metadata_merged_into_context(self, mgr: SessionManager):
        session = mgr.create_session(
            domain="d", project="p", metadata={"foo": "bar", "num": 42}
        )
        assert session.context.get("foo") == "bar"
        assert session.context.get("num") == 42

    def test_agent_id_and_metadata_coexist(self, mgr: SessionManager):
        session = mgr.create_session(
            domain="d", project="p", agent_id="a1", metadata={"key": "val"}
        )
        assert session.context["agent_id"] == "a1"
        assert session.context["key"] == "val"

    def test_no_metadata_context_empty(self, mgr: SessionManager):
        session = mgr.create_session(domain="d", project="p")
        assert session.context == {}

    def test_session_file_written_to_disk(self, mgr: SessionManager, sessions_dir: Path):
        session = mgr.create_session(domain="d", project="p")
        expected_path = sessions_dir / f"session_{session.session_id}.json"
        assert expected_path.exists()

    def test_session_file_contains_valid_json(self, mgr: SessionManager, sessions_dir: Path):
        session = mgr.create_session(domain="mydom", project="myproj")
        path = sessions_dir / f"session_{session.session_id}.json"
        data = json.loads(path.read_text())
        assert data["domain"] == "mydom"
        assert data["project"] == "myproj"
        assert data["status"] == "active"

    def test_multiple_creates_unique_ids(self, mgr: SessionManager):
        ids = [mgr.create_session(domain="d", project="p").session_id for _ in range(20)]
        assert len(set(ids)) == 20

    def test_checkpoint_manager_invalidated_on_create(self, sessions_dir: Path):
        mock_cm = MagicMock()
        mgr = SessionManager(sessions_dir=sessions_dir, checkpoint_manager=mock_cm)
        mgr.create_session(domain="d", project="p")
        mock_cm.invalidate_cache.assert_called_once()

    def test_creates_sessions_dir_if_missing(self, tmp_path: Path):
        missing = tmp_path / "new_sessions"
        mgr = SessionManager(sessions_dir=missing)
        session = mgr.create_session(domain="d", project="p")
        assert missing.exists()
        assert (missing / f"session_{session.session_id}.json").exists()


# ===========================================================================
# get_session
# ===========================================================================


class TestGetSession:
    def test_found_returns_session_info(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        result = mgr.get_session(created.session_id)
        assert result is not None
        assert result.session_id == created.session_id

    def test_not_found_returns_none(self, mgr: SessionManager):
        assert mgr.get_session("nonexistent-id") is None

    def test_domain_and_project_preserved(self, mgr: SessionManager):
        created = mgr.create_session(domain="dom-x", project="proj-y")
        result = mgr.get_session(created.session_id)
        assert result is not None
        assert result.domain == "dom-x"
        assert result.project == "proj-y"

    def test_reads_from_disk(self, mgr: SessionManager, sessions_dir: Path):
        session_id = "disk-read-test"
        _make_session_file(sessions_dir, session_id, domain="from-disk", project="ptest")
        result = mgr.get_session(session_id)
        assert result is not None
        assert result.domain == "from-disk"

    def test_partial_match_fallback(self, mgr: SessionManager, sessions_dir: Path):
        """If exact file missing but a partial-match file exists, load it."""
        full_id = "abc123-full-uuid"
        _make_session_file(sessions_dir, full_id, domain="partial", project="test")
        # Look up with partial string contained in session_id
        result = mgr.get_session("full-uuid")
        assert result is not None

    def test_returns_none_for_missing_file_and_no_partial_match(
        self, mgr: SessionManager, sessions_dir: Path
    ):
        result = mgr.get_session("completely-missing-xyz")
        assert result is None


# ===========================================================================
# _read_session_file (tested via get_session / list_sessions)
# ===========================================================================


class TestReadSessionFile:
    def test_json_decode_error_returns_none(self, mgr: SessionManager, sessions_dir: Path):
        (sessions_dir / "session_bad-json.json").write_text("{broken json!!!")
        result = mgr.get_session("bad-json")
        assert result is None

    def test_oserror_returns_none(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "oserr-read")
        original_read_text = Path.read_text

        def mock_read_text(self_inner, *args, **kwargs):
            if "oserr-read" in str(self_inner):
                raise OSError("disk failure")
            return original_read_text(self_inner, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            result = mgr.get_session("oserr-read")

        assert result is None

    def test_naive_started_at_gets_utc_tzinfo(self, mgr: SessionManager, sessions_dir: Path):
        """Naive datetimes in the JSON file get UTC timezone attached."""
        data = {
            "session_id": "naive-ts",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": "2026-01-15T10:00:00",  # no tzinfo
            "completed_tasks": [],
            "pending_tasks": [],
            "context": {},
        }
        (sessions_dir / "session_naive-ts.json").write_text(json.dumps(data))
        result = mgr.get_session("naive-ts")
        assert result is not None
        assert result.started_at.tzinfo is UTC

    def test_naive_last_activity_gets_utc_tzinfo(self, mgr: SessionManager, sessions_dir: Path):
        data = {
            "session_id": "naive-la",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": "2026-01-15T10:00:00+00:00",
            "last_activity": "2026-01-15T11:00:00",  # no tzinfo
            "completed_tasks": [],
            "pending_tasks": [],
            "context": {},
        }
        (sessions_dir / "session_naive-la.json").write_text(json.dumps(data))
        result = mgr.get_session("naive-la")
        assert result is not None
        assert result.last_activity is not None
        assert result.last_activity.tzinfo is UTC

    def test_missing_fields_use_defaults(self, mgr: SessionManager, sessions_dir: Path):
        """Minimal file with only session_id — defaults fill the rest."""
        data = {"session_id": "minimal"}
        (sessions_dir / "session_minimal.json").write_text(json.dumps(data))
        result = mgr.get_session("minimal")
        assert result is not None
        assert result.domain == "unknown"
        assert result.project == "unknown"
        assert result.status == "unknown"

    def test_parse_exception_returns_none(self, mgr: SessionManager, sessions_dir: Path):
        """Any unexpected parse error returns None without raising."""
        # Write malformed JSON that passes JSON decode but breaks SessionInfo construction
        data = {"session_id": "bad-parse", "started_at": "not-a-valid-datetime"}
        (sessions_dir / "session_bad-parse.json").write_text(json.dumps(data))
        result = mgr.get_session("bad-parse")
        assert result is None


# ===========================================================================
# _write_session
# ===========================================================================


class TestWriteSession:
    def test_oserror_returns_false(self, mgr: SessionManager, sessions_dir: Path):
        """_write_session returns False and logs when OSError occurs."""
        session = mgr.create_session(domain="d", project="p")
        original_write_text = Path.write_text

        def mock_write_text(self_inner, data, *args, **kwargs):
            if session.session_id in str(self_inner) and "mode" not in str(kwargs):
                raise OSError("read-only filesystem")
            return original_write_text(self_inner, data, *args, **kwargs)

        with patch.object(Path, "write_text", mock_write_text):
            result = mgr._write_session(session)

        assert result is False

    def test_success_returns_true(self, mgr: SessionManager, sessions_dir: Path):
        session = mgr.create_session(domain="d", project="p")
        # Write again — should succeed
        assert mgr._write_session(session) is True

    def test_checkpoint_manager_invalidated_on_write(self, sessions_dir: Path):
        mock_cm = MagicMock()
        mgr = SessionManager(sessions_dir=sessions_dir, checkpoint_manager=mock_cm)
        session = mgr.create_session(domain="d", project="p")
        # invalidate_cache called once per write — create_session already called it once
        assert mock_cm.invalidate_cache.call_count >= 1

    def test_no_checkpoint_manager_no_error(self, mgr: SessionManager):
        session = mgr.create_session(domain="d", project="p")
        # Should not raise when checkpoint_manager is None
        assert mgr._write_session(session) is True


# ===========================================================================
# end_session
# ===========================================================================


class TestEndSession:
    def test_returns_updated_session(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        result = mgr.end_session(created.session_id)
        assert result is not None
        assert result.session_id == created.session_id

    def test_status_becomes_completed(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        result = mgr.end_session(created.session_id)
        assert result is not None
        assert result.status == "completed"

    def test_last_activity_updated(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        before = created.last_activity
        time.sleep(0.01)
        result = mgr.end_session(created.session_id)
        assert result is not None
        assert result.last_activity > before

    def test_returns_none_for_missing_session(self, mgr: SessionManager):
        assert mgr.end_session("no-such-session") is None

    def test_persists_completed_status(self, mgr: SessionManager, sessions_dir: Path):
        created = mgr.create_session(domain="d", project="p")
        mgr.end_session(created.session_id)
        # Read raw file
        data = json.loads(
            (sessions_dir / f"session_{created.session_id}.json").read_text()
        )
        assert data["status"] == "completed"


# ===========================================================================
# pause_session
# ===========================================================================


class TestPauseSession:
    def test_returns_updated_session(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        result = mgr.pause_session(created.session_id)
        assert result is not None
        assert result.session_id == created.session_id

    def test_status_becomes_paused(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        result = mgr.pause_session(created.session_id)
        assert result is not None
        assert result.status == "paused"

    def test_last_activity_updated(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        before = created.last_activity
        time.sleep(0.01)
        result = mgr.pause_session(created.session_id)
        assert result is not None
        assert result.last_activity > before

    def test_returns_none_for_missing_session(self, mgr: SessionManager):
        assert mgr.pause_session("ghost-session") is None

    def test_persists_paused_status(self, mgr: SessionManager, sessions_dir: Path):
        created = mgr.create_session(domain="d", project="p")
        mgr.pause_session(created.session_id)
        data = json.loads(
            (sessions_dir / f"session_{created.session_id}.json").read_text()
        )
        assert data["status"] == "paused"


# ===========================================================================
# resume_session
# ===========================================================================


class TestResumeSession:
    def test_returns_updated_session(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        mgr.pause_session(created.session_id)
        result = mgr.resume_session(created.session_id)
        assert result is not None
        assert result.session_id == created.session_id

    def test_status_becomes_active(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        mgr.pause_session(created.session_id)
        result = mgr.resume_session(created.session_id)
        assert result is not None
        assert result.status == "active"

    def test_last_activity_updated(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        mgr.pause_session(created.session_id)
        before = datetime.now(UTC)
        time.sleep(0.01)
        result = mgr.resume_session(created.session_id)
        assert result is not None
        assert result.last_activity >= before

    def test_returns_none_for_missing_session(self, mgr: SessionManager):
        assert mgr.resume_session("phantom-session") is None

    def test_pause_then_resume_cycle(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        assert mgr.pause_session(created.session_id).status == "paused"
        assert mgr.resume_session(created.session_id).status == "active"

    def test_persists_active_status(self, mgr: SessionManager, sessions_dir: Path):
        created = mgr.create_session(domain="d", project="p")
        mgr.pause_session(created.session_id)
        mgr.resume_session(created.session_id)
        data = json.loads(
            (sessions_dir / f"session_{created.session_id}.json").read_text()
        )
        assert data["status"] == "active"


# ===========================================================================
# add_task
# ===========================================================================


class TestAddTask:
    def test_returns_updated_session(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        result = mgr.add_task(created.session_id, "Write tests")
        assert result is not None

    def test_task_appears_in_pending_tasks(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        result = mgr.add_task(created.session_id, "Implement OAuth")
        assert result is not None
        assert "Implement OAuth" in result.pending_tasks

    def test_multiple_tasks_appended_in_order(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "Task A")
        result = mgr.add_task(created.session_id, "Task B")
        assert result is not None
        assert result.pending_tasks == ["Task A", "Task B"]

    def test_duplicate_task_allowed(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "Duplicate")
        result = mgr.add_task(created.session_id, "Duplicate")
        assert result is not None
        assert result.pending_tasks.count("Duplicate") == 2

    def test_last_activity_updated(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        before = created.last_activity
        time.sleep(0.01)
        result = mgr.add_task(created.session_id, "New task")
        assert result is not None
        assert result.last_activity > before

    def test_returns_none_for_missing_session(self, mgr: SessionManager):
        assert mgr.add_task("nonexistent", "Some task") is None

    def test_task_persisted_to_disk(self, mgr: SessionManager, sessions_dir: Path):
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "Persisted task")
        data = json.loads(
            (sessions_dir / f"session_{created.session_id}.json").read_text()
        )
        assert "Persisted task" in data["pending_tasks"]


# ===========================================================================
# complete_task
# ===========================================================================


class TestCompleteTask:
    def test_returns_updated_session(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "Do the thing")
        result = mgr.complete_task(created.session_id, "Do the thing")
        assert result is not None

    def test_task_moved_from_pending_to_completed(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "Auth flow")
        result = mgr.complete_task(created.session_id, "Auth flow")
        assert result is not None
        assert "Auth flow" not in result.pending_tasks
        assert "Auth flow" in result.completed_tasks

    def test_first_occurrence_removed_from_pending(self, mgr: SessionManager):
        """When a task appears twice in pending, only the first is removed."""
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "dup")
        mgr.add_task(created.session_id, "dup")
        result = mgr.complete_task(created.session_id, "dup")
        assert result is not None
        assert result.pending_tasks.count("dup") == 1
        assert result.completed_tasks.count("dup") == 1

    def test_out_of_band_completion_still_appended(self, mgr: SessionManager):
        """Task not in pending_tasks is appended to completed anyway."""
        created = mgr.create_session(domain="d", project="p")
        result = mgr.complete_task(created.session_id, "Out of band")
        assert result is not None
        assert "Out of band" in result.completed_tasks
        assert result.pending_tasks == []

    def test_last_activity_updated(self, mgr: SessionManager):
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "T1")
        before = datetime.now(UTC)
        time.sleep(0.01)
        result = mgr.complete_task(created.session_id, "T1")
        assert result is not None
        assert result.last_activity >= before

    def test_returns_none_for_missing_session(self, mgr: SessionManager):
        assert mgr.complete_task("ghost", "some task") is None

    def test_persisted_to_disk(self, mgr: SessionManager, sessions_dir: Path):
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "disk-task")
        mgr.complete_task(created.session_id, "disk-task")
        data = json.loads(
            (sessions_dir / f"session_{created.session_id}.json").read_text()
        )
        assert "disk-task" in data["completed_tasks"]
        assert "disk-task" not in data["pending_tasks"]


# ===========================================================================
# list_sessions
# ===========================================================================


class TestListSessions:
    def test_empty_when_no_sessions_dir(self, tmp_path: Path):
        mgr = SessionManager(sessions_dir=tmp_path / "nonexistent")
        assert mgr.list_sessions() == []

    def test_empty_when_no_files(self, mgr: SessionManager):
        assert mgr.list_sessions() == []

    def test_returns_all_sessions(self, mgr: SessionManager):
        mgr.create_session(domain="d", project="p1")
        mgr.create_session(domain="d", project="p2")
        result = mgr.list_sessions()
        assert len(result) == 2

    def test_sorted_by_last_activity_descending(self, mgr: SessionManager, sessions_dir: Path):
        s1_id = "older-session"
        s2_id = "newer-session"
        old_ts = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        new_ts = datetime.now(UTC).isoformat()
        _make_session_file(sessions_dir, s1_id, last_activity=old_ts)
        _make_session_file(sessions_dir, s2_id, last_activity=new_ts)
        result = mgr.list_sessions()
        ids = [s.session_id for s in result]
        assert ids.index(s2_id) < ids.index(s1_id)

    def test_status_filter_active(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "active-1", status="active")
        _make_session_file(sessions_dir, "paused-1", status="paused")
        _make_session_file(sessions_dir, "completed-1", status="completed")
        result = mgr.list_sessions(status="active")
        assert all(s.status == "active" for s in result)
        assert len(result) == 1

    def test_status_filter_paused(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "paused-A", status="paused")
        _make_session_file(sessions_dir, "active-A", status="active")
        result = mgr.list_sessions(status="paused")
        assert len(result) == 1
        assert result[0].status == "paused"

    def test_status_filter_no_match_returns_empty(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "active-only", status="active")
        result = mgr.list_sessions(status="paused")
        assert result == []

    def test_domain_filter(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "dom-A", domain="target-domain")
        _make_session_file(sessions_dir, "dom-B", domain="other-domain")
        result = mgr.list_sessions(domain="target-domain")
        assert len(result) == 1
        assert result[0].domain == "target-domain"

    def test_domain_filter_no_match(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "dom-C", domain="domain-x")
        result = mgr.list_sessions(domain="domain-y")
        assert result == []

    def test_limit_applied(self, mgr: SessionManager, sessions_dir: Path):
        for i in range(10):
            _make_session_file(sessions_dir, f"lim-{i}")
        result = mgr.list_sessions(limit=3)
        assert len(result) == 3

    def test_default_limit_is_50(self, mgr: SessionManager, sessions_dir: Path):
        for i in range(60):
            _make_session_file(sessions_dir, f"many-{i:02d}")
        result = mgr.list_sessions()
        assert len(result) == 50

    def test_corrupt_files_silently_skipped(self, mgr: SessionManager, sessions_dir: Path):
        (sessions_dir / "session_bad.json").write_text("{BROKEN JSON")
        _make_session_file(sessions_dir, "good-session")
        result = mgr.list_sessions()
        ids = [s.session_id for s in result]
        assert "good-session" in ids
        assert "bad" not in ids

    def test_combined_status_and_domain_filter(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "match", status="active", domain="target")
        _make_session_file(sessions_dir, "wrong-status", status="paused", domain="target")
        _make_session_file(sessions_dir, "wrong-domain", status="active", domain="other")
        result = mgr.list_sessions(status="active", domain="target")
        assert len(result) == 1
        assert result[0].session_id == "match"

    def test_fallback_to_started_at_when_no_last_activity(
        self, mgr: SessionManager, sessions_dir: Path
    ):
        """Sessions without last_activity fall back to started_at for sorting."""
        data = {
            "session_id": "no-la",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
            "completed_tasks": [],
            "pending_tasks": [],
            "context": {},
        }
        (sessions_dir / "session_no-la.json").write_text(json.dumps(data))
        result = mgr.list_sessions()
        assert any(s.session_id == "no-la" for s in result)


# ===========================================================================
# get_stats
# ===========================================================================


class TestGetStats:
    def test_empty_when_no_dir(self, tmp_path: Path):
        mgr = SessionManager(sessions_dir=tmp_path / "nonexistent")
        stats = mgr.get_stats()
        assert stats == {"active": 0, "paused": 0, "completed": 0, "total": 0}

    def test_empty_when_no_files(self, mgr: SessionManager):
        stats = mgr.get_stats()
        assert stats["total"] == 0
        assert stats["active"] == 0
        assert stats["paused"] == 0
        assert stats["completed"] == 0

    def test_active_count(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "a1", status="active")
        _make_session_file(sessions_dir, "a2", status="active")
        stats = mgr.get_stats()
        assert stats["active"] == 2
        assert stats["total"] == 2

    def test_paused_count(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "p1", status="paused")
        stats = mgr.get_stats()
        assert stats["paused"] == 1

    def test_completed_count(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "c1", status="completed")
        _make_session_file(sessions_dir, "c2", status="completed")
        stats = mgr.get_stats()
        assert stats["completed"] == 2

    def test_total_count_all_statuses(self, mgr: SessionManager, sessions_dir: Path):
        _make_session_file(sessions_dir, "s-act", status="active")
        _make_session_file(sessions_dir, "s-pau", status="paused")
        _make_session_file(sessions_dir, "s-com", status="completed")
        stats = mgr.get_stats()
        assert stats["total"] == 3
        assert stats["active"] == 1
        assert stats["paused"] == 1
        assert stats["completed"] == 1

    def test_unknown_status_not_counted_in_named_keys(
        self, mgr: SessionManager, sessions_dir: Path
    ):
        _make_session_file(sessions_dir, "unknown-status", status="unknown_custom")
        stats = mgr.get_stats()
        assert stats["total"] == 1
        assert stats["active"] == 0  # unknown not in the named keys

    def test_corrupt_files_skipped(self, mgr: SessionManager, sessions_dir: Path):
        (sessions_dir / "session_corrupt-stat.json").write_text("{bad}")
        _make_session_file(sessions_dir, "valid-stat", status="active")
        stats = mgr.get_stats()
        assert stats["total"] == 1
        assert stats["active"] == 1

    def test_all_keys_present(self, mgr: SessionManager):
        stats = mgr.get_stats()
        assert "active" in stats
        assert "paused" in stats
        assert "completed" in stats
        assert "total" in stats


# ===========================================================================
# Full round-trip: create → add tasks → complete → end → re-read
# ===========================================================================


class TestRoundTrip:
    def test_full_lifecycle(self, mgr: SessionManager, sessions_dir: Path):
        s = mgr.create_session(domain="code", project="auth")
        mgr.add_task(s.session_id, "Implement JWT")
        mgr.add_task(s.session_id, "Write tests")
        mgr.complete_task(s.session_id, "Implement JWT")
        mgr.end_session(s.session_id)

        result = mgr.get_session(s.session_id)
        assert result is not None
        assert result.status == "completed"
        assert "Implement JWT" in result.completed_tasks
        assert "Write tests" in result.pending_tasks

    def test_pause_resume_then_complete(self, mgr: SessionManager):
        s = mgr.create_session(domain="d", project="p")
        mgr.add_task(s.session_id, "T1")
        mgr.pause_session(s.session_id)
        mgr.resume_session(s.session_id)
        mgr.complete_task(s.session_id, "T1")
        result = mgr.get_session(s.session_id)
        assert result is not None
        assert result.status == "active"  # not ended — still active
        assert "T1" in result.completed_tasks

    def test_data_survives_new_manager_instance(self, sessions_dir: Path):
        mgr1 = SessionManager(sessions_dir=sessions_dir)
        s = mgr1.create_session(domain="survive", project="test")
        mgr1.add_task(s.session_id, "Durable task")
        mgr1.end_session(s.session_id)

        # Create a fresh manager pointing to the same directory
        mgr2 = SessionManager(sessions_dir=sessions_dir)
        result = mgr2.get_session(s.session_id)
        assert result is not None
        assert result.domain == "survive"
        assert result.status == "completed"
        assert "Durable task" in result.pending_tasks


# ===========================================================================
# Singleton helpers
# ===========================================================================


class TestSingleton:
    def test_returns_same_instance(self, sessions_dir: Path):
        r1 = get_session_manager(sessions_dir=sessions_dir)
        r2 = get_session_manager()
        assert r1 is r2

    def test_reset_creates_new_instance(self, sessions_dir: Path):
        r1 = get_session_manager(sessions_dir=sessions_dir)
        reset_session_manager()
        r2 = get_session_manager(sessions_dir=sessions_dir)
        assert r1 is not r2

    def test_params_ignored_on_subsequent_calls(self, tmp_path: Path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        r1 = get_session_manager(sessions_dir=dir1)
        r2 = get_session_manager(sessions_dir=dir2)
        # Second call params ignored — same instance
        assert r1 is r2
        assert r1.sessions_dir == dir1

    def test_reset_clears_singleton(self):
        import forge_harness.webhook_server.services.session_manager as mod
        get_session_manager()
        assert mod._manager_instance is not None
        reset_session_manager()
        assert mod._manager_instance is None

    def test_thread_safe_singleton_creation(self, sessions_dir: Path):
        instances: list[SessionManager] = []
        errors: list[Exception] = []

        def getter():
            try:
                instances.append(get_session_manager(sessions_dir=sessions_dir))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=getter) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(inst is instances[0] for inst in instances)


# ===========================================================================
# Thread safety
# ===========================================================================


class TestConcurrency:
    def test_concurrent_create_session_unique_ids(self, mgr: SessionManager):
        results: list = []
        errors: list = []

        def creator():
            try:
                results.append(mgr.create_session(domain="d", project="p"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=creator) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        ids = [s.session_id for s in results]
        assert len(ids) == len(set(ids))

    def test_concurrent_add_task_no_exception(self, mgr: SessionManager):
        s = mgr.create_session(domain="d", project="p")
        errors: list = []

        def adder(i: int):
            try:
                mgr.add_task(s.session_id, f"task-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=adder, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        result = mgr.get_session(s.session_id)
        assert result is not None
        assert len(result.pending_tasks) == 20

    def test_concurrent_list_sessions_no_exception(self, mgr: SessionManager, sessions_dir: Path):
        for i in range(5):
            _make_session_file(sessions_dir, f"conc-{i}")
        errors: list = []

        def lister():
            try:
                mgr.list_sessions()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=lister) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_concurrent_get_stats_no_exception(self, mgr: SessionManager, sessions_dir: Path):
        for i in range(5):
            _make_session_file(sessions_dir, f"stat-{i}", status="active")
        errors: list = []

        def statter():
            try:
                mgr.get_stats()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=statter) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
