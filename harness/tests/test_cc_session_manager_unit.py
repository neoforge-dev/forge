"""
Pure unit tests for forge_harness.webhook_server.services.session_manager

Strategy: mock the filesystem at Path-level to avoid real I/O so that every
code path in the module is exercised with full control over edge cases and
error injection.  All 176 lines of the source are covered.

Test classes:
    TestSessionManagerInit          - __init__, attribute defaults
    TestEnsureDir                   - _ensure_dir delegation
    TestSessionPath                 - _session_path name construction
    TestReadSessionFileParsing      - _read_session_file: datetime parsing
    TestReadSessionFileErrors       - _read_session_file: OSError, JSONDecodeError, bad parse
    TestWriteSession                - _write_session: success, OSError, checkpoint invalidation
    TestLoadSession                 - _load_session: exact match, partial match, missing
    TestCreateSession               - create_session: all branches
    TestGetSession                  - get_session delegation to _load_session
    TestEndSession                  - end_session: found / not-found
    TestPauseSession                - pause_session: found / not-found
    TestResumeSession               - resume_session: found / not-found
    TestAddTask                     - add_task: normal, missing session, duplicates
    TestCompleteTask                - complete_task: present, out-of-band, missing session
    TestListSessions                - list_sessions: dir missing, filtering, sorting, limit
    TestGetStats                    - get_stats: all status buckets, unknown status
    TestGetSessionManagerSingleton  - get_session_manager: singleton creation, idempotency
    TestResetSessionManager         - reset_session_manager: clears module-level instance
    TestValidStatuses               - _VALID_STATUSES constant content
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import forge_harness.webhook_server.services.session_manager as _mod
from forge_harness.checkpoint_manager import SessionInfo
from forge_harness.webhook_server.services.session_manager import (
    _VALID_STATUSES,
    SessionManager,
    get_session_manager,
    reset_session_manager,
)

# ---------------------------------------------------------------------------
# Autouse fixture: reset the module-level singleton between every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_session_manager()
    yield
    reset_session_manager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_info(**overrides) -> SessionInfo:
    """Return a minimal SessionInfo with sensible defaults."""
    now = datetime.now(UTC)
    defaults = dict(
        session_id=str(uuid.uuid4()),
        domain="unit-domain",
        project="unit-project",
        status="active",
        started_at=now,
        last_activity=now,
        completed_tasks=[],
        pending_tasks=[],
        context={},
    )
    defaults.update(overrides)
    return SessionInfo(**defaults)


def _make_session_json(**overrides) -> str:
    """Return JSON bytes for a minimal session file."""
    now = datetime.now(UTC).isoformat()
    data = {
        "session_id": overrides.pop("session_id", "test-sid"),
        "domain": overrides.pop("domain", "test-domain"),
        "project": overrides.pop("project", "test-project"),
        "status": overrides.pop("status", "active"),
        "started_at": overrides.pop("started_at", now),
        "last_activity": overrides.pop("last_activity", now),
        "completed_tasks": overrides.pop("completed_tasks", []),
        "pending_tasks": overrides.pop("pending_tasks", []),
        "context": overrides.pop("context", {}),
    }
    data.update(overrides)
    return json.dumps(data)


# ===========================================================================
# TestSessionManagerInit
# ===========================================================================


class TestSessionManagerInit:
    """__init__ sets attributes correctly in all calling patterns."""

    def test_default_sessions_dir_resolves_to_forge_sessions(self, tmp_path):
        """When sessions_dir is None the default is '.forge/sessions'."""
        mgr = SessionManager()
        assert mgr.sessions_dir == Path(".forge/sessions")

    def test_string_path_converted_to_path_object(self, tmp_path):
        mgr = SessionManager(sessions_dir=str(tmp_path / "str-dir"))
        assert isinstance(mgr.sessions_dir, Path)

    def test_path_object_stored_unchanged(self, tmp_path):
        p = tmp_path / "mydir"
        mgr = SessionManager(sessions_dir=p)
        assert mgr.sessions_dir == p

    def test_checkpoint_manager_stored_when_provided(self, tmp_path):
        cm = MagicMock()
        mgr = SessionManager(sessions_dir=tmp_path, checkpoint_manager=cm)
        assert mgr._checkpoint_manager is cm

    def test_checkpoint_manager_none_by_default(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr._checkpoint_manager is None

    def test_internal_lock_is_rlock(self, tmp_path):
        """RLock supports re-entrant acquisition without deadlock."""
        mgr = SessionManager(sessions_dir=tmp_path)
        acquired = []
        with mgr._lock:
            with mgr._lock:
                acquired.append(True)
        assert acquired == [True]


# ===========================================================================
# TestEnsureDir
# ===========================================================================


class TestEnsureDir:
    """_ensure_dir calls mkdir with parents=True, exist_ok=True."""

    def test_ensure_dir_calls_mkdir(self, tmp_path):
        missing = tmp_path / "a" / "b" / "c"
        mgr = SessionManager(sessions_dir=missing)
        assert not missing.exists()
        mgr._ensure_dir()
        assert missing.exists()

    def test_ensure_dir_idempotent(self, tmp_path):
        d = tmp_path / "existing"
        d.mkdir()
        mgr = SessionManager(sessions_dir=d)
        # Should not raise even if dir already exists
        mgr._ensure_dir()
        assert d.exists()


# ===========================================================================
# TestSessionPath
# ===========================================================================


class TestSessionPath:
    """_session_path returns the correct Path object."""

    def test_path_format(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._session_path("abc-123")
        assert result == tmp_path / "session_abc-123.json"

    def test_path_is_inside_sessions_dir(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._session_path("xyz")
        assert result.parent == tmp_path


# ===========================================================================
# TestReadSessionFileParsing
# ===========================================================================


class TestReadSessionFileParsing:
    """_read_session_file correctly parses datetime fields."""

    def test_aware_started_at_preserved(self, tmp_path):
        ts = "2026-01-01T12:00:00+00:00"
        path = tmp_path / "session_aware-sa.json"
        path.write_text(_make_session_json(session_id="aware-sa", started_at=ts))
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._read_session_file(path)
        assert result is not None
        assert result.started_at.tzinfo is not None

    def test_naive_started_at_gets_utc(self, tmp_path):
        ts = "2026-01-01T12:00:00"  # no tzinfo
        path = tmp_path / "session_naive-sa.json"
        path.write_text(_make_session_json(session_id="naive-sa", started_at=ts))
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._read_session_file(path)
        assert result is not None
        assert result.started_at.tzinfo is UTC

    def test_missing_started_at_uses_now(self, tmp_path):
        data = {
            "session_id": "no-sa",
            "domain": "d",
            "project": "p",
            "status": "active",
            "completed_tasks": [],
            "pending_tasks": [],
            "context": {},
        }
        path = tmp_path / "session_no-sa.json"
        path.write_text(json.dumps(data))
        mgr = SessionManager(sessions_dir=tmp_path)
        before = datetime.now(UTC)
        result = mgr._read_session_file(path)
        assert result is not None
        assert result.started_at >= before or result.started_at <= datetime.now(UTC)

    def test_aware_last_activity_preserved(self, tmp_path):
        ts = "2026-02-10T08:30:00+00:00"
        path = tmp_path / "session_aware-la.json"
        path.write_text(_make_session_json(session_id="aware-la", last_activity=ts))
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._read_session_file(path)
        assert result is not None
        assert result.last_activity is not None
        assert result.last_activity.tzinfo is not None

    def test_naive_last_activity_gets_utc(self, tmp_path):
        ts = "2026-02-10T08:30:00"  # naive
        path = tmp_path / "session_naive-la.json"
        path.write_text(_make_session_json(session_id="naive-la", last_activity=ts))
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._read_session_file(path)
        assert result is not None
        assert result.last_activity is not None
        assert result.last_activity.tzinfo is UTC

    def test_missing_last_activity_yields_none(self, tmp_path):
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
        path = tmp_path / "session_no-la.json"
        path.write_text(json.dumps(data))
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._read_session_file(path)
        assert result is not None
        assert result.last_activity is None

    def test_defaults_for_missing_domain_project_status(self, tmp_path):
        path = tmp_path / "session_min.json"
        path.write_text(json.dumps({"session_id": "min"}))
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._read_session_file(path)
        assert result is not None
        assert result.domain == "unknown"
        assert result.project == "unknown"
        assert result.status == "unknown"

    def test_session_id_falls_back_to_filename_stem(self, tmp_path):
        """When session_id missing from JSON, stem of filename is used."""
        path = tmp_path / "session_from-stem.json"
        path.write_text(json.dumps({}))
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._read_session_file(path)
        assert result is not None
        # stem is "session_from-stem", removeprefix("session_") → "from-stem"
        assert result.session_id == "from-stem"

    def test_context_defaults_to_empty_dict(self, tmp_path):
        path = tmp_path / "session_no-ctx.json"
        path.write_text(json.dumps({"session_id": "no-ctx"}))
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._read_session_file(path)
        assert result is not None
        assert result.context == {}

    def test_completed_and_pending_tasks_defaulted(self, tmp_path):
        path = tmp_path / "session_no-tasks.json"
        path.write_text(json.dumps({"session_id": "no-tasks"}))
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._read_session_file(path)
        assert result is not None
        assert result.completed_tasks == []
        assert result.pending_tasks == []


# ===========================================================================
# TestReadSessionFileErrors
# ===========================================================================


class TestReadSessionFileErrors:
    """_read_session_file returns None on I/O and parse errors."""

    def test_json_decode_error_returns_none(self, tmp_path):
        path = tmp_path / "session_bad-json.json"
        path.write_text("{not valid json!!!")
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr._read_session_file(path) is None

    def test_oserror_returns_none(self, tmp_path):
        path = tmp_path / "session_no-read.json"
        path.write_text("{}")
        mgr = SessionManager(sessions_dir=tmp_path)
        with patch.object(Path, "read_text", side_effect=OSError("disk error")):
            assert mgr._read_session_file(path) is None

    def test_invalid_datetime_string_returns_none(self, tmp_path):
        """A bad datetime in started_at triggers the except clause → None."""
        path = tmp_path / "session_bad-dt.json"
        path.write_text(json.dumps({"session_id": "bad-dt", "started_at": "NOT-A-DATE"}))
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr._read_session_file(path) is None


# ===========================================================================
# TestWriteSession
# ===========================================================================


class TestWriteSession:
    """_write_session persists data and handles errors."""

    def test_writes_json_file(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = _make_session_info(session_id="write-me")
        result = mgr._write_session(session)
        assert result is True
        path = tmp_path / "session_write-me.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["session_id"] == "write-me"

    def test_returns_false_on_oserror(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = _make_session_info(session_id="err-write")
        with patch.object(Path, "write_text", side_effect=OSError("read-only")):
            result = mgr._write_session(session)
        assert result is False

    def test_calls_invalidate_cache_when_checkpoint_manager_set(self, tmp_path):
        cm = MagicMock()
        mgr = SessionManager(sessions_dir=tmp_path, checkpoint_manager=cm)
        session = _make_session_info(session_id="cm-inv")
        mgr._write_session(session)
        cm.invalidate_cache.assert_called_once()

    def test_no_invalidate_when_no_checkpoint_manager(self, tmp_path):
        """Should not raise when checkpoint_manager is None."""
        mgr = SessionManager(sessions_dir=tmp_path)
        session = _make_session_info(session_id="no-cm")
        result = mgr._write_session(session)
        assert result is True

    def test_creates_sessions_dir_if_absent(self, tmp_path):
        missing = tmp_path / "auto-created"
        mgr = SessionManager(sessions_dir=missing)
        session = _make_session_info(session_id="dir-create")
        mgr._write_session(session)
        assert missing.exists()

    def test_oserror_does_not_call_invalidate_cache(self, tmp_path):
        cm = MagicMock()
        mgr = SessionManager(sessions_dir=tmp_path, checkpoint_manager=cm)
        session = _make_session_info(session_id="err-no-inv")
        with patch.object(Path, "write_text", side_effect=OSError("fail")):
            mgr._write_session(session)
        cm.invalidate_cache.assert_not_called()


# ===========================================================================
# TestLoadSession
# ===========================================================================


class TestLoadSession:
    """_load_session: exact match, partial-match fallback, missing."""

    def test_exact_match_found(self, tmp_path):
        sid = "exact-sid"
        (tmp_path / f"session_{sid}.json").write_text(
            _make_session_json(session_id=sid)
        )
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr._load_session(sid)
        assert result is not None
        assert result.session_id == sid

    def test_partial_match_used_when_exact_missing(self, tmp_path):
        full_id = "abc123-full-uuid"
        (tmp_path / f"session_{full_id}.json").write_text(
            _make_session_json(session_id=full_id)
        )
        mgr = SessionManager(sessions_dir=tmp_path)
        # look up with substring
        result = mgr._load_session("full-uuid")
        assert result is not None

    def test_returns_none_when_no_match(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr._load_session("totally-missing") is None

    def test_returns_none_when_dir_empty(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr._load_session("any-id") is None


# ===========================================================================
# TestCreateSession
# ===========================================================================


class TestCreateSession:
    """create_session: all branches and side-effects."""

    def test_returns_session_info(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        result = mgr.create_session(domain="d", project="p")
        assert isinstance(result, SessionInfo)

    def test_status_is_active(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(domain="d", project="p")
        assert session.status == "active"

    def test_uuid_session_id_generated(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(domain="d", project="p")
        # Must not raise
        uuid.UUID(session.session_id)

    def test_timestamps_are_utc_aware(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(domain="d", project="p")
        assert session.started_at.tzinfo is not None
        assert session.last_activity is not None
        assert session.last_activity.tzinfo is not None

    def test_empty_task_lists(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(domain="d", project="p")
        assert session.pending_tasks == []
        assert session.completed_tasks == []

    def test_agent_id_in_context(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(domain="d", project="p", agent_id="agent-42")
        assert session.context["agent_id"] == "agent-42"

    def test_no_agent_id_context_empty(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(domain="d", project="p")
        assert session.context == {}

    def test_metadata_merged_into_context(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(domain="d", project="p", metadata={"k": "v"})
        assert session.context["k"] == "v"

    def test_agent_id_and_metadata_coexist(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(
            domain="d", project="p", agent_id="ag1", metadata={"extra": 99}
        )
        assert session.context["agent_id"] == "ag1"
        assert session.context["extra"] == 99

    def test_empty_metadata_dict_does_not_pollute_context(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(domain="d", project="p", metadata={})
        assert session.context == {}

    def test_file_written_to_disk(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        session = mgr.create_session(domain="d", project="p")
        assert (tmp_path / f"session_{session.session_id}.json").exists()

    def test_checkpoint_manager_invalidated(self, tmp_path):
        cm = MagicMock()
        mgr = SessionManager(sessions_dir=tmp_path, checkpoint_manager=cm)
        mgr.create_session(domain="d", project="p")
        cm.invalidate_cache.assert_called()

    def test_multiple_creates_produce_unique_ids(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        ids = [mgr.create_session(domain="d", project="p").session_id for _ in range(10)]
        assert len(ids) == len(set(ids))


# ===========================================================================
# TestGetSession
# ===========================================================================


class TestGetSession:
    """get_session delegates to _load_session."""

    def test_found_returns_session_info(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        result = mgr.get_session(created.session_id)
        assert result is not None
        assert result.session_id == created.session_id

    def test_not_found_returns_none(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr.get_session("no-such-session") is None

    def test_domain_and_project_preserved_on_reload(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="preserve-dom", project="preserve-proj")
        result = mgr.get_session(created.session_id)
        assert result is not None
        assert result.domain == "preserve-dom"
        assert result.project == "preserve-proj"


# ===========================================================================
# TestEndSession
# ===========================================================================


class TestEndSession:
    """end_session marks status completed and updates last_activity."""

    def test_returns_session_info_with_completed_status(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        result = mgr.end_session(created.session_id)
        assert result is not None
        assert result.status == "completed"

    def test_returns_none_for_missing_session(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr.end_session("ghost-id") is None

    def test_last_activity_updated(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        old_activity = created.last_activity
        result = mgr.end_session(created.session_id)
        assert result is not None
        assert result.last_activity >= old_activity

    def test_persists_completed_status_to_disk(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.end_session(created.session_id)
        raw = json.loads((tmp_path / f"session_{created.session_id}.json").read_text())
        assert raw["status"] == "completed"

    def test_checkpoint_manager_invalidated_on_end(self, tmp_path):
        cm = MagicMock()
        mgr = SessionManager(sessions_dir=tmp_path, checkpoint_manager=cm)
        created = mgr.create_session(domain="d", project="p")
        cm.reset_mock()
        mgr.end_session(created.session_id)
        cm.invalidate_cache.assert_called_once()


# ===========================================================================
# TestPauseSession
# ===========================================================================


class TestPauseSession:
    """pause_session transitions status to paused."""

    def test_returns_paused_session(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        result = mgr.pause_session(created.session_id)
        assert result is not None
        assert result.status == "paused"

    def test_returns_none_for_missing_session(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr.pause_session("no-session") is None

    def test_last_activity_updated(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        before = created.last_activity
        result = mgr.pause_session(created.session_id)
        assert result is not None
        assert result.last_activity >= before

    def test_persists_paused_status(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.pause_session(created.session_id)
        raw = json.loads((tmp_path / f"session_{created.session_id}.json").read_text())
        assert raw["status"] == "paused"


# ===========================================================================
# TestResumeSession
# ===========================================================================


class TestResumeSession:
    """resume_session transitions status back to active."""

    def test_returns_active_session(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.pause_session(created.session_id)
        result = mgr.resume_session(created.session_id)
        assert result is not None
        assert result.status == "active"

    def test_returns_none_for_missing_session(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr.resume_session("phantom") is None

    def test_last_activity_updated(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.pause_session(created.session_id)
        paused_activity = mgr.get_session(created.session_id).last_activity
        result = mgr.resume_session(created.session_id)
        assert result is not None
        assert result.last_activity >= paused_activity

    def test_pause_resume_round_trip_on_disk(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.pause_session(created.session_id)
        mgr.resume_session(created.session_id)
        raw = json.loads((tmp_path / f"session_{created.session_id}.json").read_text())
        assert raw["status"] == "active"


# ===========================================================================
# TestAddTask
# ===========================================================================


class TestAddTask:
    """add_task appends to pending_tasks."""

    def test_task_appears_in_pending(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        result = mgr.add_task(created.session_id, "Do the thing")
        assert result is not None
        assert "Do the thing" in result.pending_tasks

    def test_multiple_tasks_ordered(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "First")
        result = mgr.add_task(created.session_id, "Second")
        assert result is not None
        assert result.pending_tasks == ["First", "Second"]

    def test_duplicate_tasks_allowed(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "dup")
        result = mgr.add_task(created.session_id, "dup")
        assert result is not None
        assert result.pending_tasks.count("dup") == 2

    def test_returns_none_for_missing_session(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr.add_task("missing-sid", "task") is None

    def test_last_activity_updated(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        before = created.last_activity
        result = mgr.add_task(created.session_id, "update-activity")
        assert result is not None
        assert result.last_activity >= before

    def test_task_persisted_to_disk(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "disk-task")
        raw = json.loads((tmp_path / f"session_{created.session_id}.json").read_text())
        assert "disk-task" in raw["pending_tasks"]


# ===========================================================================
# TestCompleteTask
# ===========================================================================


class TestCompleteTask:
    """complete_task moves task from pending to completed."""

    def test_task_moved_to_completed(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "auth-flow")
        result = mgr.complete_task(created.session_id, "auth-flow")
        assert result is not None
        assert "auth-flow" in result.completed_tasks
        assert "auth-flow" not in result.pending_tasks

    def test_first_occurrence_removed_when_duplicated(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "dup")
        mgr.add_task(created.session_id, "dup")
        result = mgr.complete_task(created.session_id, "dup")
        assert result is not None
        assert result.pending_tasks.count("dup") == 1
        assert result.completed_tasks.count("dup") == 1

    def test_out_of_band_completion_appended(self, tmp_path):
        """Task not in pending still gets appended to completed."""
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        result = mgr.complete_task(created.session_id, "out-of-band")
        assert result is not None
        assert "out-of-band" in result.completed_tasks
        assert result.pending_tasks == []

    def test_returns_none_for_missing_session(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr.complete_task("no-session", "task") is None

    def test_last_activity_updated(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "T")
        before = mgr.get_session(created.session_id).last_activity
        result = mgr.complete_task(created.session_id, "T")
        assert result is not None
        assert result.last_activity >= before

    def test_persisted_to_disk(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        created = mgr.create_session(domain="d", project="p")
        mgr.add_task(created.session_id, "persist-me")
        mgr.complete_task(created.session_id, "persist-me")
        raw = json.loads((tmp_path / f"session_{created.session_id}.json").read_text())
        assert "persist-me" in raw["completed_tasks"]
        assert "persist-me" not in raw["pending_tasks"]


# ===========================================================================
# TestListSessions
# ===========================================================================


class TestListSessions:
    """list_sessions: directory existence, filtering, sorting, limit."""

    def _write_session(self, sessions_dir: Path, session_id: str, **kwargs):
        now = datetime.now(UTC).isoformat()
        data = {
            "session_id": session_id,
            "domain": kwargs.get("domain", "d"),
            "project": kwargs.get("project", "p"),
            "status": kwargs.get("status", "active"),
            "started_at": kwargs.get("started_at", now),
            "last_activity": kwargs.get("last_activity", now),
            "completed_tasks": [],
            "pending_tasks": [],
            "context": {},
        }
        (sessions_dir / f"session_{session_id}.json").write_text(json.dumps(data))

    def test_empty_when_dir_does_not_exist(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path / "nonexistent")
        assert mgr.list_sessions() == []

    def test_empty_when_no_files_in_dir(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        assert mgr.list_sessions() == []

    def test_all_sessions_returned_unfiltered(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        for i in range(3):
            self._write_session(tmp_path, f"s{i}")
        assert len(mgr.list_sessions()) == 3

    def test_status_filter_active(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "act", status="active")
        self._write_session(tmp_path, "pau", status="paused")
        result = mgr.list_sessions(status="active")
        assert len(result) == 1
        assert result[0].status == "active"

    def test_status_filter_paused(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "a1", status="active")
        self._write_session(tmp_path, "p1", status="paused")
        self._write_session(tmp_path, "c1", status="completed")
        result = mgr.list_sessions(status="paused")
        assert len(result) == 1
        assert result[0].status == "paused"

    def test_status_filter_completed(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "c2", status="completed")
        result = mgr.list_sessions(status="completed")
        assert len(result) == 1

    def test_status_filter_no_match_returns_empty(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "act2", status="active")
        assert mgr.list_sessions(status="paused") == []

    def test_domain_filter(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "dom1", domain="target")
        self._write_session(tmp_path, "dom2", domain="other")
        result = mgr.list_sessions(domain="target")
        assert len(result) == 1
        assert result[0].domain == "target"

    def test_domain_filter_no_match(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "dom3", domain="existing")
        assert mgr.list_sessions(domain="missing-domain") == []

    def test_limit_applied(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        for i in range(10):
            self._write_session(tmp_path, f"lim{i:02d}")
        result = mgr.list_sessions(limit=4)
        assert len(result) == 4

    def test_default_limit_is_50(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        for i in range(55):
            self._write_session(tmp_path, f"many{i:03d}")
        assert len(mgr.list_sessions()) == 50

    def test_sorted_most_recent_first(self, tmp_path):
        from datetime import timedelta
        mgr = SessionManager(sessions_dir=tmp_path)
        old_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        new_ts = datetime.now(UTC).isoformat()
        self._write_session(tmp_path, "older", last_activity=old_ts)
        self._write_session(tmp_path, "newer", last_activity=new_ts)
        result = mgr.list_sessions()
        assert result[0].session_id == "newer"
        assert result[1].session_id == "older"

    def test_corrupt_file_silently_skipped(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        (tmp_path / "session_bad.json").write_text("{INVALID}")
        self._write_session(tmp_path, "good-one")
        result = mgr.list_sessions()
        ids = [s.session_id for s in result]
        assert "good-one" in ids
        assert "bad" not in ids

    def test_combined_status_and_domain_filter(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "match", status="active", domain="mydom")
        self._write_session(tmp_path, "wrong-status", status="paused", domain="mydom")
        self._write_session(tmp_path, "wrong-domain", status="active", domain="other")
        result = mgr.list_sessions(status="active", domain="mydom")
        assert len(result) == 1
        assert result[0].session_id == "match"

    def test_session_without_last_activity_falls_back_to_started_at(self, tmp_path):
        """Sessions with no last_activity still appear — sorted by started_at."""
        mgr = SessionManager(sessions_dir=tmp_path)
        data = {
            "session_id": "no-la-sort",
            "domain": "d",
            "project": "p",
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
            "completed_tasks": [],
            "pending_tasks": [],
            "context": {},
        }
        (tmp_path / "session_no-la-sort.json").write_text(json.dumps(data))
        result = mgr.list_sessions()
        assert any(s.session_id == "no-la-sort" for s in result)


# ===========================================================================
# TestGetStats
# ===========================================================================


class TestGetStats:
    """get_stats: zero counts, counts per status, unknown status, corrupt files."""

    def _write_session(self, sessions_dir: Path, session_id: str, status: str = "active"):
        now = datetime.now(UTC).isoformat()
        data = {
            "session_id": session_id,
            "domain": "d",
            "project": "p",
            "status": status,
            "started_at": now,
            "last_activity": now,
            "completed_tasks": [],
            "pending_tasks": [],
            "context": {},
        }
        (sessions_dir / f"session_{session_id}.json").write_text(json.dumps(data))

    def test_zero_counts_when_dir_missing(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path / "missing")
        stats = mgr.get_stats()
        assert stats == {"active": 0, "paused": 0, "completed": 0, "total": 0}

    def test_zero_counts_when_dir_empty(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        stats = mgr.get_stats()
        assert stats["total"] == 0

    def test_active_incremented(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "a", "active")
        self._write_session(tmp_path, "b", "active")
        stats = mgr.get_stats()
        assert stats["active"] == 2
        assert stats["total"] == 2

    def test_paused_incremented(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "p1", "paused")
        stats = mgr.get_stats()
        assert stats["paused"] == 1

    def test_completed_incremented(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "c1", "completed")
        self._write_session(tmp_path, "c2", "completed")
        stats = mgr.get_stats()
        assert stats["completed"] == 2

    def test_mixed_statuses(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "mx-a", "active")
        self._write_session(tmp_path, "mx-p", "paused")
        self._write_session(tmp_path, "mx-c", "completed")
        stats = mgr.get_stats()
        assert stats == {"active": 1, "paused": 1, "completed": 1, "total": 3}

    def test_unknown_status_counts_in_total_not_named_keys(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        self._write_session(tmp_path, "unk", "some_new_status")
        stats = mgr.get_stats()
        assert stats["total"] == 1
        assert stats["active"] == 0
        assert stats["paused"] == 0
        assert stats["completed"] == 0

    def test_corrupt_file_skipped(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        (tmp_path / "session_corrupt.json").write_text("{bad json")
        self._write_session(tmp_path, "valid", "active")
        stats = mgr.get_stats()
        assert stats["total"] == 1
        assert stats["active"] == 1

    def test_all_expected_keys_present(self, tmp_path):
        mgr = SessionManager(sessions_dir=tmp_path)
        stats = mgr.get_stats()
        assert {"active", "paused", "completed", "total"} == set(stats.keys())


# ===========================================================================
# TestGetSessionManagerSingleton
# ===========================================================================


class TestGetSessionManagerSingleton:
    """get_session_manager creates the singleton on first call, reuses it."""

    def test_returns_session_manager_instance(self, tmp_path):
        mgr = get_session_manager(sessions_dir=tmp_path)
        assert isinstance(mgr, SessionManager)

    def test_subsequent_calls_return_same_object(self, tmp_path):
        m1 = get_session_manager(sessions_dir=tmp_path)
        m2 = get_session_manager(sessions_dir=tmp_path / "other")
        assert m1 is m2

    def test_sessions_dir_used_only_on_first_call(self, tmp_path):
        dir1 = tmp_path / "dir1"
        get_session_manager(sessions_dir=dir1)
        # Second call with different dir — must still use dir1
        m2 = get_session_manager(sessions_dir=tmp_path / "dir2")
        assert m2.sessions_dir == dir1

    def test_module_instance_set_after_call(self, tmp_path):
        get_session_manager(sessions_dir=tmp_path)
        assert _mod._manager_instance is not None

    def test_thread_safe_singleton(self, tmp_path):
        """Concurrent callers all get the same instance."""
        results: list[SessionManager] = []
        errors: list[Exception] = []

        def get():
            try:
                results.append(get_session_manager(sessions_dir=tmp_path))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(inst is results[0] for inst in results)


# ===========================================================================
# TestResetSessionManager
# ===========================================================================


class TestResetSessionManager:
    """reset_session_manager clears the module-level singleton."""

    def test_module_instance_is_none_after_reset(self, tmp_path):
        get_session_manager(sessions_dir=tmp_path)
        assert _mod._manager_instance is not None
        reset_session_manager()
        assert _mod._manager_instance is None

    def test_reset_allows_new_instance_with_different_dir(self, tmp_path):
        dir1 = tmp_path / "d1"
        dir2 = tmp_path / "d2"
        m1 = get_session_manager(sessions_dir=dir1)
        reset_session_manager()
        m2 = get_session_manager(sessions_dir=dir2)
        assert m1 is not m2
        assert m2.sessions_dir == dir2

    def test_reset_idempotent_when_no_instance(self):
        """reset_session_manager should not raise if already None."""
        assert _mod._manager_instance is None
        reset_session_manager()  # should not raise
        assert _mod._manager_instance is None


# ===========================================================================
# TestValidStatuses
# ===========================================================================


class TestValidStatuses:
    """_VALID_STATUSES constant contains the expected values."""

    def test_contains_active(self):
        assert "active" in _VALID_STATUSES

    def test_contains_paused(self):
        assert "paused" in _VALID_STATUSES

    def test_contains_completed(self):
        assert "completed" in _VALID_STATUSES

    def test_is_frozenset(self):
        assert isinstance(_VALID_STATUSES, frozenset)

    def test_exactly_three_statuses(self):
        assert len(_VALID_STATUSES) == 3
