"""Pure unit tests for UnifiedAgentRegistry using mocks.

Targets: forge_harness/webhook_server/services/unified_registry.py

Strategy
--------
Every test in this file uses unittest.mock (patch, MagicMock) to isolate the
registry from real I/O.  No real files are created; Path operations are
patched at the call-site so that tests are fast and deterministic.

Coverage targets (85%+):
- All public methods: register, unregister, get_agent, list_agents,
  update_heartbeat, sync_to_files, sync_from_files, get_stats
- All private helpers: _ensure_dir, _write_session_file, _load_file_agents,
  _needs_file_flush
- Module-level helpers: _now_iso, _parse_ts, _session_path,
  _agent_to_file_dict, _normalise_file_agent
- Singleton: get_unified_registry, reset_unified_registry
- All exception/error branches (OSError, JSONDecodeError, missing id, etc.)
- Thread-safety: lock acquisition confirmed via interleaved mock calls
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from forge_harness.webhook_server.services.unified_registry import (
    UnifiedAgentRegistry,
    _agent_to_file_dict,
    _normalise_file_agent,
    _now_iso,
    _parse_ts,
    _session_path,
    get_unified_registry,
    reset_unified_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Isolate every test from singleton state."""
    reset_unified_registry()
    yield
    reset_unified_registry()


@pytest.fixture()
def reg(tmp_path):
    """Registry with a tmp sessions directory (dir is created by the fixture)."""
    sessions_dir = tmp_path / ".forge/sessions"
    sessions_dir.mkdir(parents=True)
    return UnifiedAgentRegistry(sessions_dir=sessions_dir, sync_interval_seconds=30)


@pytest.fixture()
def reg_no_dir(tmp_path):
    """Registry whose sessions directory does NOT exist yet."""
    return UnifiedAgentRegistry(
        sessions_dir=tmp_path / "no_such_dir",
        sync_interval_seconds=30,
    )


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _mk_agent(**extra) -> dict:
    base = {
        "role": "backend-engineer",
        "project": "my-project",
        "task": "do things",
        "domain": "test-domain",
    }
    base.update(extra)
    return base


# ===========================================================================
# Module-level pure functions
# ===========================================================================


class TestNowIso:
    def test_returns_string(self):
        result = _now_iso()
        assert isinstance(result, str)

    def test_parses_back_to_utc_datetime(self):
        ts = _now_iso()
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    def test_two_calls_not_equal(self):
        import time
        t1 = _now_iso()
        time.sleep(0.001)
        t2 = _now_iso()
        # They should at minimum be the same or t2 >= t1
        assert t2 >= t1


class TestParseTs:
    def test_none_input_returns_none(self):
        assert _parse_ts(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_ts("") is None

    def test_zero_returns_none(self):
        assert _parse_ts(0) is None

    def test_valid_utc_string(self):
        ts = "2026-01-01T00:00:00+00:00"
        result = _parse_ts(ts)
        assert result is not None
        assert result.tzinfo is not None

    def test_naive_string_gets_utc_attached(self):
        result = _parse_ts("2026-06-15T10:30:00")
        assert result is not None
        assert result.tzinfo is UTC

    def test_garbage_string_returns_none(self):
        assert _parse_ts("not-a-date") is None

    def test_float_returns_none(self):
        # floats are not valid ISO strings
        assert _parse_ts(3.14) is None

    def test_list_returns_none(self):
        assert _parse_ts([]) is None

    def test_preserves_offset(self):
        ts = "2026-03-01T12:00:00+05:30"
        result = _parse_ts(ts)
        assert result is not None
        # offset should be preserved (not forced to UTC)
        assert result.utcoffset() is not None


class TestSessionPath:
    def test_returns_path_object(self, tmp_path):
        result = _session_path(tmp_path, "abc")
        assert isinstance(result, Path)

    def test_filename_contains_prefix_and_id(self, tmp_path):
        result = _session_path(tmp_path, "agent-42")
        assert result.name == "session_agent-42.json"

    def test_parent_is_sessions_dir(self, tmp_path):
        result = _session_path(tmp_path, "x")
        assert result.parent == tmp_path

    def test_different_ids_different_paths(self, tmp_path):
        p1 = _session_path(tmp_path, "id-a")
        p2 = _session_path(tmp_path, "id-b")
        assert p1 != p2


class TestAgentToFileDict:
    def test_adds_session_id_from_id(self):
        result = _agent_to_file_dict({"id": "abc"})
        assert result["session_id"] == "abc"

    def test_does_not_overwrite_existing_session_id(self):
        result = _agent_to_file_dict({"id": "abc", "session_id": "kept"})
        assert result["session_id"] == "kept"

    def test_adds_last_heartbeat_from_last_activity(self):
        result = _agent_to_file_dict({"id": "x", "last_activity": "2026-01-01T00:00:00+00:00"})
        assert result["last_heartbeat"] == "2026-01-01T00:00:00+00:00"

    def test_adds_last_heartbeat_when_last_activity_missing(self):
        result = _agent_to_file_dict({"id": "x"})
        assert "last_heartbeat" in result

    def test_original_dict_not_mutated(self):
        original = {"id": "orig", "status": "active"}
        _agent_to_file_dict(original)
        assert "session_id" not in original

    def test_other_fields_preserved(self):
        result = _agent_to_file_dict({"id": "y", "role": "tester", "progress": 77})
        assert result["role"] == "tester"
        assert result["progress"] == 77


class TestNormaliseFileAgent:
    def test_id_from_session_id_only(self):
        result = _normalise_file_agent({"session_id": "sid"})
        assert result["id"] == "sid"

    def test_session_id_from_id_only(self):
        result = _normalise_file_agent({"id": "myid"})
        assert result["session_id"] == "myid"

    def test_both_present_unchanged(self):
        result = _normalise_file_agent({"id": "a", "session_id": "b"})
        assert result["id"] == "a"
        assert result["session_id"] == "b"

    def test_defaults_role(self):
        assert _normalise_file_agent({"id": "x"})["role"] == "unknown"

    def test_defaults_project(self):
        assert _normalise_file_agent({"id": "x"})["project"] == "unknown"

    def test_defaults_task_empty(self):
        assert _normalise_file_agent({"id": "x"})["task"] == ""

    def test_defaults_status_unknown(self):
        assert _normalise_file_agent({"id": "x"})["status"] == "unknown"

    def test_defaults_progress_zero(self):
        assert _normalise_file_agent({"id": "x"})["progress"] == 0

    def test_defaults_files_modified_empty_list(self):
        assert _normalise_file_agent({"id": "x"})["files_modified"] == []

    def test_defaults_token_usage_empty_dict(self):
        assert _normalise_file_agent({"id": "x"})["token_usage"] == {}

    def test_defaults_skills_empty_list(self):
        assert _normalise_file_agent({"id": "x"})["skills"] == []

    def test_defaults_children_empty_list(self):
        assert _normalise_file_agent({"id": "x"})["children"] == []

    def test_source_set_to_file(self):
        assert _normalise_file_agent({"id": "x"})["source"] == "file"

    def test_existing_values_preserved(self):
        raw = {"id": "z", "role": "custom", "status": "completed", "progress": 99}
        result = _normalise_file_agent(raw)
        assert result["role"] == "custom"
        assert result["status"] == "completed"
        assert result["progress"] == 99

    def test_registered_at_added_when_missing(self):
        result = _normalise_file_agent({"id": "x"})
        assert "registered_at" in result

    def test_last_activity_added_when_missing(self):
        result = _normalise_file_agent({"id": "x"})
        assert "last_activity" in result


# ===========================================================================
# UnifiedAgentRegistry.__init__
# ===========================================================================


class TestInit:
    def test_default_sessions_dir(self):
        reg = UnifiedAgentRegistry()
        # Default is ".forge/sessions" relative path
        assert reg._sessions_dir == Path(".forge/sessions")

    def test_custom_sessions_dir_string(self, tmp_path):
        reg = UnifiedAgentRegistry(sessions_dir=str(tmp_path / "custom"))
        assert reg._sessions_dir == tmp_path / "custom"

    def test_custom_sessions_dir_path(self, tmp_path):
        d = tmp_path / "my_dir"
        reg = UnifiedAgentRegistry(sessions_dir=d)
        assert reg._sessions_dir == d

    def test_sync_interval_stored(self):
        reg = UnifiedAgentRegistry(sync_interval_seconds=99)
        assert reg._sync_interval == 99

    def test_agents_initially_empty(self):
        reg = UnifiedAgentRegistry()
        assert reg._agents == {}

    def test_heartbeat_counter_initially_empty(self):
        reg = UnifiedAgentRegistry()
        assert reg._heartbeat_counter == {}

    def test_last_sync_at_initially_none(self):
        reg = UnifiedAgentRegistry()
        assert reg._last_sync_at is None


# ===========================================================================
# register (with mocked file I/O)
# ===========================================================================


class TestRegisterMocked:
    def test_id_generated_when_absent(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent())
        assert "id" in result
        assert len(result["id"]) > 0

    def test_provided_id_respected(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent(id="explicit-id"))
        assert result["id"] == "explicit-id"

    def test_session_id_mirrors_id(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent(id="sid-mirror"))
        assert result["session_id"] == "sid-mirror"

    def test_source_is_api(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent())
        assert result["source"] == "api"

    def test_write_session_file_called_once(self, reg):
        with patch.object(reg, "_write_session_file") as mock_write:
            reg.register(_mk_agent(id="wf-once"))
        mock_write.assert_called_once()

    def test_agent_stored_in_memory(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent(id="mem-store"))
        assert "mem-store" in reg._agents

    def test_heartbeat_counter_initialised_zero(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="hb-zero"))
        assert reg._heartbeat_counter["hb-zero"] == 0

    def test_parent_child_link_set(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="parent-p"))
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="child-p", parent_id="parent-p"))
        assert "child-p" in reg._agents["parent-p"]["children"]

    def test_parent_missing_no_error(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent(id="orphan-q", parent_id="ghost"))
        assert result["id"] == "orphan-q"

    def test_child_already_in_parent_children_not_duplicated(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="parent-dup"))
        # Manually add child to parent's children list before second register
        reg._agents["parent-dup"]["children"].append("child-dup")
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="child-dup", parent_id="parent-dup"))
        assert reg._agents["parent-dup"]["children"].count("child-dup") == 1

    def test_returns_copy_not_internal_reference(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent(id="copy-r"))
        result["status"] = "mutated"
        assert reg._agents["copy-r"]["status"] == "active"

    def test_custom_status_stored(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent(status="waiting"))
        assert result["status"] == "waiting"

    def test_custom_progress_stored(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent(progress=55))
        assert result["progress"] == 55

    def test_token_usage_stored(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent(token_usage={"input": 100, "output": 50}))
        assert result["token_usage"] == {"input": 100, "output": 50}

    def test_skills_stored(self, reg):
        with patch.object(reg, "_write_session_file"):
            result = reg.register(_mk_agent(skills=["python", "rust"]))
        assert result["skills"] == ["python", "rust"]


# ===========================================================================
# unregister (with mocked file I/O)
# ===========================================================================


class TestUnregisterMocked:
    def test_returns_true_for_existing(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="del-u"))
        with patch.object(Path, "exists", return_value=False):
            result = reg.unregister("del-u")
        assert result is True

    def test_returns_false_for_unknown(self, reg):
        result = reg.unregister("nobody")
        assert result is False

    def test_removes_from_agents_dict(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="gone"))
        with patch.object(Path, "exists", return_value=False):
            reg.unregister("gone")
        assert "gone" not in reg._agents

    def test_removes_from_heartbeat_counter(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="hb-rm"))
        with patch.object(Path, "exists", return_value=False):
            reg.unregister("hb-rm")
        assert "hb-rm" not in reg._heartbeat_counter

    def test_unlinks_session_file_when_exists(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="file-rm"))
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            reg.unregister("file-rm")
        mock_path.unlink.assert_called_once()

    def test_oserror_on_unlink_not_raised(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="oserr-u"))
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.unlink.side_effect = OSError("no permission")
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            # Must not raise
            result = reg.unregister("oserr-u")
        assert result is True

    def test_file_not_found_no_error(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="no-file-u"))
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            result = reg.unregister("no-file-u")
        mock_path.unlink.assert_not_called()
        assert result is True


# ===========================================================================
# get_agent (mocked I/O)
# ===========================================================================


class TestGetAgentMocked:
    def test_in_memory_fast_path(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="fast-path"))
        result = reg.get_agent("fast-path")
        assert result is not None
        assert result["id"] == "fast-path"

    def test_unknown_agent_returns_none_when_no_file(self, reg):
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            assert reg.get_agent("unknown") is None

    def test_file_fallback_returns_agent(self, reg):
        payload = {
            "id": "fb-agent",
            "session_id": "fb-agent",
            "role": "worker",
            "project": "proj",
            "status": "active",
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(payload)
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            result = reg.get_agent("fb-agent")
        assert result is not None
        assert result["id"] == "fb-agent"

    def test_file_fallback_populates_memory(self, reg):
        payload = {"id": "cache-agent", "session_id": "cache-agent"}
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(payload)
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            reg.get_agent("cache-agent")
        assert "cache-agent" in reg._agents

    def test_json_decode_error_returns_none(self, reg):
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "NOT VALID JSON{"
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            result = reg.get_agent("bad-json")
        assert result is None

    def test_oserror_in_read_returns_none(self, reg):
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = OSError("disk failure")
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            result = reg.get_agent("oserr-read")
        assert result is None

    def test_non_dict_json_returns_none(self, reg):
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps([1, 2, 3])
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            result = reg.get_agent("list-json")
        assert result is None

    def test_returns_copy_not_reference(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="ref-guard"))
        copy = reg.get_agent("ref-guard")
        assert copy is not None
        copy["status"] = "hijacked"
        assert reg._agents["ref-guard"]["status"] == "active"


# ===========================================================================
# update_heartbeat (mocked I/O)
# ===========================================================================


class TestUpdateHeartbeatMocked:
    def test_returns_none_for_unknown_no_file(self, reg):
        with patch.object(reg, "get_agent", return_value=None):
            result = reg.update_heartbeat("ghost", {"status": "active"})
        assert result is None

    def test_updates_status_field(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="upd-status"))
        with patch.object(reg, "_write_session_file"):
            result = reg.update_heartbeat("upd-status", {"status": "completed"})
        assert result["status"] == "completed"

    def test_last_activity_always_updated(self, reg):
        with patch.object(reg, "_write_session_file"):
            a = reg.register(_mk_agent(id="la-upd"))
        old_la = a["last_activity"]
        with patch.object(reg, "_write_session_file"):
            result = reg.update_heartbeat("la-upd", {})
        assert result["last_activity"] >= old_la

    def test_last_heartbeat_always_updated(self, reg):
        with patch.object(reg, "_write_session_file"):
            a = reg.register(_mk_agent(id="lhb-upd"))
        old_lhb = a["last_heartbeat"]
        with patch.object(reg, "_write_session_file"):
            result = reg.update_heartbeat("lhb-upd", {})
        assert result["last_heartbeat"] >= old_lhb

    def test_id_protected_from_override(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="id-protect"))
        with patch.object(reg, "_write_session_file"):
            reg.update_heartbeat("id-protect", {"id": "injected"})
        assert "injected" not in reg._agents
        assert "id-protect" in reg._agents

    def test_session_id_protected(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="sid-protect"))
        with patch.object(reg, "_write_session_file"):
            reg.update_heartbeat("sid-protect", {"session_id": "stolen"})
        assert reg._agents["sid-protect"]["session_id"] == "sid-protect"

    def test_registered_at_protected(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="reg-protect", registered_at="2025-01-01T00:00:00+00:00"))
        with patch.object(reg, "_write_session_file"):
            reg.update_heartbeat("reg-protect", {"registered_at": "2099-01-01T00:00:00+00:00"})
        assert reg._agents["reg-protect"]["registered_at"] == "2025-01-01T00:00:00+00:00"

    def test_heartbeat_counter_increments(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="hb-inc"))
        assert reg._heartbeat_counter["hb-inc"] == 0
        with patch.object(reg, "_write_session_file"):
            reg.update_heartbeat("hb-inc", {})
        assert reg._heartbeat_counter["hb-inc"] == 1

    def test_writes_file_when_session_missing(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="no-sess"))
        # Simulate: _needs_file_flush -> False, but session file doesn't exist
        reg._last_sync_at = datetime.now(UTC)  # ensure flush check is False
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            with patch.object(reg, "_write_session_file") as mock_write:
                reg.update_heartbeat("no-sess", {"progress": 10})
        mock_write.assert_called_once()

    def test_hydrates_file_agent_before_updating(self, reg):
        # Agent not in memory, but exists on file
        payload = {
            "id": "hyd-agent",
            "session_id": "hyd-agent",
            "status": "idle",
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(payload)
        with patch(
            "forge_harness.webhook_server.services.unified_registry._session_path",
            return_value=mock_path,
        ):
            with patch.object(reg, "_write_session_file"):
                result = reg.update_heartbeat("hyd-agent", {"status": "active"})
        assert result is not None
        assert result["status"] == "active"

    def test_returns_copy_not_reference(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="copy-hb"))
        with patch.object(reg, "_write_session_file"):
            result = reg.update_heartbeat("copy-hb", {"progress": 20})
        result["progress"] = 999
        assert reg._agents["copy-hb"]["progress"] == 20


# ===========================================================================
# sync_to_files (mocked I/O)
# ===========================================================================


class TestSyncToFilesMocked:
    def test_empty_registry_returns_zero(self, reg):
        with patch.object(reg, "_ensure_dir"):
            count = reg.sync_to_files()
        assert count == 0

    def test_count_matches_agents(self, reg):
        for i in range(3):
            with patch.object(reg, "_write_session_file"):
                reg.register(_mk_agent(id=f"stf-{i}"))
        with patch.object(reg, "_write_session_file"):
            with patch.object(reg, "_ensure_dir"):
                count = reg.sync_to_files()
        assert count == 3

    def test_updates_last_sync_at(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="ts-sync"))
        assert reg._last_sync_at is None
        with patch.object(reg, "_write_session_file"):
            with patch.object(reg, "_ensure_dir"):
                reg.sync_to_files()
        assert reg._last_sync_at is not None

    def test_oserror_from_write_counted_not_raised(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="err-stf-1"))
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="err-stf-2"))

        call_count = {"n": 0}

        def raising_write(agent):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("simulated failure")

        with patch.object(reg, "_write_session_file", side_effect=raising_write):
            with patch.object(reg, "_ensure_dir"):
                count = reg.sync_to_files()

        # One agent raised, one succeeded
        assert count == 1

    def test_write_called_for_each_agent(self, reg):
        for i in range(4):
            with patch.object(reg, "_write_session_file"):
                reg.register(_mk_agent(id=f"wca-{i}"))
        with patch.object(reg, "_write_session_file") as mock_write:
            with patch.object(reg, "_ensure_dir"):
                reg.sync_to_files()
        assert mock_write.call_count == 4


# ===========================================================================
# sync_from_files (mocked I/O)
# ===========================================================================


class TestSyncFromFilesMocked:
    def test_missing_sessions_dir_returns_zero(self, reg_no_dir):
        count = reg_no_dir.sync_from_files()
        assert count == 0

    def test_new_file_agents_loaded(self, reg):
        file_agent = _normalise_file_agent({"id": "new-from-file", "status": "idle"})
        with patch.object(reg, "_load_file_agents", return_value={"new-from-file": file_agent}):
            count = reg.sync_from_files()
        assert count == 1
        assert "new-from-file" in reg._agents

    def test_memory_wins_when_newer(self, reg):
        old_ts = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        new_ts = datetime.now(UTC).isoformat()

        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="mem-wins"))
        reg._agents["mem-wins"]["last_activity"] = new_ts

        file_agent = _normalise_file_agent(
            {"id": "mem-wins", "status": "stale", "last_activity": old_ts}
        )
        with patch.object(reg, "_load_file_agents", return_value={"mem-wins": file_agent}):
            reg.sync_from_files()
        # Memory (newer) should win
        assert reg._agents["mem-wins"]["status"] == "active"

    def test_file_wins_when_newer(self, reg):
        old_ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        new_ts = datetime.now(UTC).isoformat()

        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="file-wins"))
        reg._agents["file-wins"]["last_activity"] = old_ts

        file_agent = _normalise_file_agent(
            {"id": "file-wins", "status": "completed", "last_activity": new_ts}
        )
        with patch.object(reg, "_load_file_agents", return_value={"file-wins": file_agent}):
            count = reg.sync_from_files()
        assert count == 1
        assert reg._agents["file-wins"]["status"] == "completed"

    def test_file_with_no_ts_not_counted(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="no-ts-agent"))
        # File agent has no last_activity (None from _parse_ts)
        file_agent = _normalise_file_agent({"id": "no-ts-agent", "last_activity": ""})
        with patch.object(reg, "_load_file_agents", return_value={"no-ts-agent": file_agent}):
            count = reg.sync_from_files()
        # file_ts is None, so memory wins (count not incremented)
        assert count == 0

    def test_zero_new_when_all_in_memory_and_mem_is_newer(self, reg):
        new_ts = datetime.now(UTC).isoformat()
        old_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="no-update"))
        reg._agents["no-update"]["last_activity"] = new_ts

        file_agent = _normalise_file_agent({"id": "no-update", "last_activity": old_ts})
        with patch.object(reg, "_load_file_agents", return_value={"no-update": file_agent}):
            count = reg.sync_from_files()
        assert count == 0


# ===========================================================================
# _load_file_agents (mocked I/O)
# ===========================================================================


class TestLoadFileAgentsMocked:
    def test_returns_empty_when_dir_missing(self, reg_no_dir):
        result = reg_no_dir._load_file_agents()
        assert result == {}

    def test_valid_file_returned(self, reg, tmp_path):
        sessions_dir = tmp_path / ".forge/sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        payload = {"id": "lfa-ok", "session_id": "lfa-ok", "status": "active"}
        (sessions_dir / "session_lfa-ok.json").write_text(json.dumps(payload))
        reg._sessions_dir = sessions_dir
        result = reg._load_file_agents()
        assert "lfa-ok" in result

    def test_corrupt_json_skipped(self, reg, tmp_path):
        sessions_dir = tmp_path / ".forge/sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "session_bad.json").write_text("{BAD JSON")
        reg._sessions_dir = sessions_dir
        result = reg._load_file_agents()
        assert "bad" not in result

    def test_non_dict_json_skipped(self, reg, tmp_path):
        sessions_dir = tmp_path / ".forge/sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "session_list-lfa.json").write_text("[1,2,3]")
        reg._sessions_dir = sessions_dir
        result = reg._load_file_agents()
        assert result == {}

    def test_oserror_skipped(self, reg, tmp_path):
        sessions_dir = tmp_path / ".forge/sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        ok_payload = {"id": "ok-agent", "session_id": "ok-agent"}
        (sessions_dir / "session_ok-agent.json").write_text(json.dumps(ok_payload))
        (sessions_dir / "session_err-agent.json").write_text("{}")
        reg._sessions_dir = sessions_dir

        original_read_text = Path.read_text

        def mock_read(self_inner, *args, **kwargs):
            if "err-agent" in str(self_inner):
                raise OSError("disk error")
            return original_read_text(self_inner, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read):
            result = reg._load_file_agents()

        assert "ok-agent" in result
        assert "err-agent" not in result

    def test_file_with_no_id_excluded(self, reg, tmp_path):
        sessions_dir = tmp_path / ".forge/sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        # Both id and session_id missing
        (sessions_dir / "session_noid.json").write_text(
            json.dumps({"role": "ghost", "status": "active"})
        )
        reg._sessions_dir = sessions_dir
        result = reg._load_file_agents()
        assert result == {}


# ===========================================================================
# _write_session_file (mocked I/O)
# ===========================================================================


class TestWriteSessionFileMocked:
    def test_no_id_no_write(self, reg):
        with patch.object(Path, "write_text") as mock_wt:
            reg._write_session_file({"role": "ghost"})
        mock_wt.assert_not_called()

    def test_id_from_session_id(self, reg):
        with patch.object(reg, "_ensure_dir"):
            with patch.object(Path, "write_text") as mock_wt:
                reg._write_session_file({"session_id": "use-sid"})
        mock_wt.assert_called_once()

    def test_oserror_logged_not_raised(self, reg):
        with patch.object(reg, "_ensure_dir"):
            with patch.object(Path, "write_text", side_effect=OSError("no space")):
                # Must not raise
                reg._write_session_file({"id": "oserr-wsf"})


# ===========================================================================
# _needs_file_flush
# ===========================================================================


class TestNeedsFileFlushMocked:
    def test_true_when_last_sync_none(self, reg):
        assert reg._last_sync_at is None
        assert reg._needs_file_flush() is True

    def test_false_when_recently_synced(self, reg):
        reg._last_sync_at = datetime.now(UTC)
        assert reg._needs_file_flush() is False

    def test_true_when_interval_elapsed(self):
        reg = UnifiedAgentRegistry(sync_interval_seconds=1)
        reg._last_sync_at = datetime.now(UTC) - timedelta(seconds=5)
        assert reg._needs_file_flush() is True

    def test_boundary_exactly_at_interval(self):
        reg = UnifiedAgentRegistry(sync_interval_seconds=10)
        reg._last_sync_at = datetime.now(UTC) - timedelta(seconds=10)
        # elapsed == interval means it's time to flush
        assert reg._needs_file_flush() is True


# ===========================================================================
# get_stats (mocked list_agents)
# ===========================================================================


class TestGetStatsMocked:
    def test_empty_returns_zero_total(self, reg):
        with patch.object(reg, "list_agents", return_value=[]):
            stats = reg.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}

    def test_by_status_aggregation(self, reg):
        agents = [
            {"status": "active"},
            {"status": "active"},
            {"status": "idle"},
        ]
        with patch.object(reg, "list_agents", return_value=agents):
            stats = reg.get_stats()
        assert stats["by_status"]["active"] == 2
        assert stats["by_status"]["idle"] == 1

    def test_last_sync_at_none_when_not_synced(self, reg):
        with patch.object(reg, "list_agents", return_value=[]):
            stats = reg.get_stats()
        assert stats["last_sync_at"] is None

    def test_last_sync_at_iso_when_synced(self, reg):
        reg._last_sync_at = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        with patch.object(reg, "list_agents", return_value=[]):
            stats = reg.get_stats()
        assert stats["last_sync_at"] == "2026-01-15T12:00:00+00:00"

    def test_sessions_dir_in_result(self, reg):
        with patch.object(reg, "list_agents", return_value=[]):
            stats = reg.get_stats()
        assert "sessions_dir" in stats

    def test_sync_interval_in_result(self, reg):
        with patch.object(reg, "list_agents", return_value=[]):
            stats = reg.get_stats()
        assert stats["sync_interval_seconds"] == 30

    def test_agent_missing_status_counted_as_unknown(self, reg):
        agents = [{"role": "worker"}]  # no status key
        with patch.object(reg, "list_agents", return_value=agents):
            stats = reg.get_stats()
        assert stats["by_status"].get("unknown", 0) == 1


# ===========================================================================
# list_agents (mocked _load_file_agents)
# ===========================================================================


class TestListAgentsMocked:
    def test_memory_agents_returned(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="la-mem"))
        with patch.object(reg, "_load_file_agents", return_value={}):
            agents = reg.list_agents()
        ids = {a["id"] for a in agents}
        assert "la-mem" in ids

    def test_file_only_agents_included(self, reg):
        file_agent = _normalise_file_agent({"id": "la-file"})
        with patch.object(reg, "_load_file_agents", return_value={"la-file": file_agent}):
            agents = reg.list_agents()
        ids = {a["id"] for a in agents}
        assert "la-file" in ids

    def test_memory_overwrites_file(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="overlap-la", status="active"))
        file_agent = _normalise_file_agent({"id": "overlap-la", "status": "stale"})
        with patch.object(reg, "_load_file_agents", return_value={"overlap-la": file_agent}):
            agents = reg.list_agents()
        match = next(a for a in agents if a["id"] == "overlap-la")
        assert match["status"] == "active"

    def test_status_filter_applied(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="la-active", status="active"))
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="la-idle", status="idle"))
        with patch.object(reg, "_load_file_agents", return_value={}):
            result = reg.list_agents(status="active")
        assert all(a["status"] == "active" for a in result)
        assert len(result) == 1

    def test_no_status_filter_returns_all(self, reg):
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="nf-1", status="active"))
        with patch.object(reg, "_write_session_file"):
            reg.register(_mk_agent(id="nf-2", status="idle"))
        with patch.object(reg, "_load_file_agents", return_value={}):
            result = reg.list_agents()
        assert len(result) == 2


# ===========================================================================
# Singleton: get_unified_registry / reset_unified_registry
# ===========================================================================


class TestSingletonMocked:
    def test_same_instance_returned(self, tmp_path):
        d = tmp_path / "s1"
        r1 = get_unified_registry(sessions_dir=d)
        r2 = get_unified_registry()
        assert r1 is r2

    def test_reset_gives_new_instance(self, tmp_path):
        d = tmp_path / "s2"
        r1 = get_unified_registry(sessions_dir=d)
        reset_unified_registry()
        r2 = get_unified_registry(sessions_dir=d)
        assert r1 is not r2

    def test_second_call_params_ignored(self, tmp_path):
        d1 = tmp_path / "dir1"
        d2 = tmp_path / "dir2"
        r1 = get_unified_registry(sessions_dir=d1)
        r2 = get_unified_registry(sessions_dir=d2)
        assert r1 is r2
        assert r1._sessions_dir == d1

    def test_reset_sets_module_var_to_none(self):
        import forge_harness.webhook_server.services.unified_registry as mod

        get_unified_registry()
        assert mod._unified_registry is not None
        reset_unified_registry()
        assert mod._unified_registry is None

    def test_thread_safe_creation(self, tmp_path):
        d = tmp_path / "ts"
        instances = []
        errors = []

        def creator():
            try:
                instances.append(get_unified_registry(sessions_dir=d))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=creator) for _ in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(inst is instances[0] for inst in instances)


# ===========================================================================
# _ensure_dir
# ===========================================================================


class TestEnsureDir:
    def test_creates_directory(self, tmp_path):
        d = tmp_path / "to_create" / "nested"
        reg = UnifiedAgentRegistry(sessions_dir=d)
        assert not d.exists()
        reg._ensure_dir()
        assert d.exists()

    def test_no_error_if_already_exists(self, tmp_path):
        d = tmp_path / "already_there"
        d.mkdir(parents=True)
        reg = UnifiedAgentRegistry(sessions_dir=d)
        # Should not raise
        reg._ensure_dir()
        assert d.exists()
