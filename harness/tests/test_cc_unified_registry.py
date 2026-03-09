"""Comprehensive tests for UnifiedAgentRegistry (Command Center service layer).

Targets forge_harness/webhook_server/services/unified_registry.py.

Coverage goals (70%+ per task spec, but these tests push 95%+):
  - All public methods: register, unregister, get_agent, list_agents,
    update_heartbeat, sync_to_files, sync_from_files, get_stats
  - Module-level helpers: _parse_ts, _normalise_file_agent, _session_path,
    _agent_to_file_dict
  - Singleton: get_unified_registry, reset_unified_registry
  - Error / exception paths:
      * OSError on file unlink (unregister)
      * OSError / JSONDecodeError in get_agent file fallback
      * OSError in _load_file_agents (load_file_agents loop)
      * OSError in _write_session_file
      * OSError in sync_to_files (propagated from _write_session_file)
      * Agent dict with no id / session_id in _write_session_file
  - Thread-safety assertions
  - Edge cases: empty registry, non-existent sessions dir, corrupt JSON,
    non-dict JSON, missing parent agent
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

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


@pytest.fixture()
def sessions_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".forge/sessions"
    d.mkdir()
    return d


@pytest.fixture()
def reg(sessions_dir: Path) -> UnifiedAgentRegistry:
    """Fresh registry backed by a temp directory."""
    return UnifiedAgentRegistry(sessions_dir=sessions_dir, sync_interval_seconds=30)


@pytest.fixture(autouse=True)
def reset_singleton_before_after():
    """Ensure the global singleton is reset for every test."""
    reset_unified_registry()
    yield
    reset_unified_registry()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent(**overrides: object) -> dict:
    base: dict = {
        "role": "backend-engineer",
        "project": "voice-coach",
        "task": "write tests",
        "domain": "brandfocus-ai",
    }
    base.update(overrides)
    return base


def _write_session(sessions_dir: Path, agent_id: str, **fields: object) -> dict:
    """Write a minimal session JSON file, simulating an externally-spawned agent."""
    data: dict = {
        "id": agent_id,
        "session_id": agent_id,
        "role": fields.get("role", "script-agent"),
        "project": fields.get("project", "project-x"),
        "task": fields.get("task", "background"),
        "status": fields.get("status", "active"),
        "progress": fields.get("progress", 0),
        "registered_at": fields.get("registered_at", _now_iso()),
        "last_activity": fields.get("last_activity", _now_iso()),
    }
    data.update({k: v for k, v in fields.items() if k not in data})
    (sessions_dir / f"session_{agent_id}.json").write_text(json.dumps(data, indent=2))
    return data


# ===========================================================================
# Module-level helper functions
# ===========================================================================


class TestHelperFunctions:
    """Tests for private module helpers exposed via import."""

    # --- _parse_ts ---

    def test_parse_ts_utc_aware_string(self):
        ts = "2026-01-15T12:00:00+00:00"
        result = _parse_ts(ts)
        assert result is not None
        assert result.tzinfo is not None

    def test_parse_ts_naive_string_gets_utc(self):
        result = _parse_ts("2026-06-01T00:00:00")
        assert result is not None
        assert result.tzinfo is UTC

    def test_parse_ts_none_returns_none(self):
        assert _parse_ts(None) is None

    def test_parse_ts_empty_string_returns_none(self):
        assert _parse_ts("") is None

    def test_parse_ts_garbage_string_returns_none(self):
        assert _parse_ts("not-a-timestamp") is None

    def test_parse_ts_integer_returns_none(self):
        # integers are not valid ISO strings
        assert _parse_ts(12345) is None

    def test_parse_ts_preserves_timezone_info(self):
        ts = datetime.now(UTC)
        result = _parse_ts(ts.isoformat())
        assert result is not None
        assert result.tzinfo is not None

    # --- _normalise_file_agent ---

    def test_normalise_adds_all_defaults(self):
        result = _normalise_file_agent({"id": "x"})
        assert result["role"] == "unknown"
        assert result["project"] == "unknown"
        assert result["task"] == ""
        assert result["status"] == "unknown"
        assert result["progress"] == 0
        assert result["files_modified"] == []
        assert result["token_usage"] == {}
        assert result["skills"] == []
        assert result["children"] == []
        assert result["source"] == "file"
        assert "registered_at" in result
        assert "last_activity" in result

    def test_normalise_id_from_session_id(self):
        result = _normalise_file_agent({"session_id": "abc"})
        assert result["id"] == "abc"
        assert result["session_id"] == "abc"

    def test_normalise_session_id_from_id(self):
        result = _normalise_file_agent({"id": "xyz"})
        assert result["session_id"] == "xyz"

    def test_normalise_both_present_kept(self):
        result = _normalise_file_agent({"id": "a", "session_id": "b"})
        assert result["id"] == "a"
        assert result["session_id"] == "b"

    def test_normalise_preserves_existing_fields(self):
        raw = {
            "id": "keep",
            "role": "custom-role",
            "status": "completed",
            "progress": 99,
        }
        result = _normalise_file_agent(raw)
        assert result["role"] == "custom-role"
        assert result["status"] == "completed"
        assert result["progress"] == 99

    # --- _agent_to_file_dict ---

    def test_agent_to_file_dict_adds_session_id(self):
        agent = {"id": "a1", "status": "active"}
        result = _agent_to_file_dict(agent)
        assert result["session_id"] == "a1"

    def test_agent_to_file_dict_adds_last_heartbeat(self):
        agent = {"id": "a1", "last_activity": "2026-01-01T00:00:00+00:00"}
        result = _agent_to_file_dict(agent)
        assert "last_heartbeat" in result

    def test_agent_to_file_dict_does_not_mutate_original(self):
        agent = {"id": "orig", "status": "active"}
        _agent_to_file_dict(agent)
        assert "session_id" not in agent

    # --- _session_path ---

    def test_session_path_uses_prefix(self, sessions_dir: Path):
        path = _session_path(sessions_dir, "myagent")
        assert path == sessions_dir / "session_myagent.json"

    def test_session_path_different_ids(self, sessions_dir: Path):
        p1 = _session_path(sessions_dir, "id-one")
        p2 = _session_path(sessions_dir, "id-two")
        assert p1 != p2


# ===========================================================================
# UnifiedAgentRegistry — register
# ===========================================================================


class TestRegister:
    def test_returns_dict_with_id(self, reg: UnifiedAgentRegistry):
        result = reg.register(_agent())
        assert "id" in result
        assert result["id"]

    def test_custom_id_is_respected(self, reg: UnifiedAgentRegistry):
        result = reg.register(_agent(id="custom-007"))
        assert result["id"] == "custom-007"

    def test_defaults_set_correctly(self, reg: UnifiedAgentRegistry):
        result = reg.register(_agent())
        assert result["status"] == "active"
        assert result["progress"] == 0
        assert result["source"] == "api"
        assert result["children"] == []
        assert result["skills"] == []

    def test_agent_is_in_memory(self, reg: UnifiedAgentRegistry):
        result = reg.register(_agent(id="mem-test"))
        assert "mem-test" in reg._agents

    def test_session_file_created(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        result = reg.register(_agent(id="file-test"))
        assert (sessions_dir / "session_file-test.json").exists()

    def test_session_file_valid_json(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        result = reg.register(_agent(id="json-test", role="tester"))
        data = json.loads((sessions_dir / "session_json-test.json").read_text())
        assert data["id"] == "json-test"
        assert data["role"] == "tester"
        assert data["session_id"] == "json-test"

    def test_optional_fields_stored(self, reg: UnifiedAgentRegistry):
        result = reg.register(
            _agent(
                id="opt-test",
                name="Optimus",
                tmux_session="forge:opt",
                parent_id=None,
                skills=["rust", "python"],
                token_usage={"input": 100},
            )
        )
        assert result["name"] == "Optimus"
        assert result["tmux_session"] == "forge:opt"
        assert result["skills"] == ["rust", "python"]
        assert result["token_usage"] == {"input": 100}

    def test_parent_children_updated(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="parent-x"))
        reg.register(_agent(id="child-x", parent_id="parent-x"))
        assert "child-x" in reg._agents["parent-x"]["children"]

    def test_parent_missing_does_not_raise(self, reg: UnifiedAgentRegistry):
        # parent_id points to a non-existent agent — should silently skip
        result = reg.register(_agent(id="orphan", parent_id="nonexistent-parent"))
        assert result["id"] == "orphan"

    def test_multiple_registrations_unique_ids(self, reg: UnifiedAgentRegistry):
        ids = [reg.register(_agent())["id"] for _ in range(10)]
        assert len(set(ids)) == 10

    def test_custom_registered_at_preserved(self, reg: UnifiedAgentRegistry):
        custom_ts = "2025-12-31T23:59:59+00:00"
        result = reg.register(_agent(registered_at=custom_ts))
        assert result["registered_at"] == custom_ts

    def test_heartbeat_counter_initialised_to_zero(self, reg: UnifiedAgentRegistry):
        result = reg.register(_agent(id="hb-counter"))
        assert reg._heartbeat_counter["hb-counter"] == 0


# ===========================================================================
# UnifiedAgentRegistry — unregister
# ===========================================================================


class TestUnregister:
    def test_returns_true_when_found(self, reg: UnifiedAgentRegistry):
        a = reg.register(_agent(id="del-me"))
        assert reg.unregister("del-me") is True

    def test_returns_false_when_not_found(self, reg: UnifiedAgentRegistry):
        assert reg.unregister("ghost") is False

    def test_removed_from_memory(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="rm-mem"))
        reg.unregister("rm-mem")
        assert "rm-mem" not in reg._agents

    def test_session_file_deleted(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        reg.register(_agent(id="rm-file"))
        assert (sessions_dir / "session_rm-file.json").exists()
        reg.unregister("rm-file")
        assert not (sessions_dir / "session_rm-file.json").exists()

    def test_file_already_missing_no_error(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        reg.register(_agent(id="no-file"))
        (sessions_dir / "session_no-file.json").unlink()
        # Should complete without raising
        assert reg.unregister("no-file") is True

    def test_heartbeat_counter_cleared(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="hb-del"))
        reg.unregister("hb-del")
        assert "hb-del" not in reg._heartbeat_counter

    def test_oserror_on_unlink_logged_not_raised(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        """If unlink raises OSError, unregister logs a warning but does NOT re-raise."""
        reg.register(_agent(id="oserr-unlink"))
        file_path = sessions_dir / "session_oserr-unlink.json"
        assert file_path.exists()

        with patch.object(file_path.__class__, "unlink", side_effect=OSError("permission denied")):
            with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
                # The unlink is called on a Path instance — mock at the instance level
                original_unlink = Path.unlink

                def mock_unlink(self_inner, missing_ok=False):
                    if "oserr-unlink" in str(self_inner):
                        raise OSError("permission denied")
                    return original_unlink(self_inner, missing_ok=missing_ok)

                with patch.object(Path, "unlink", mock_unlink):
                    result = reg.unregister("oserr-unlink")

        # Agent removed from memory even though file delete failed
        assert result is True
        assert "oserr-unlink" not in reg._agents


# ===========================================================================
# UnifiedAgentRegistry — get_agent
# ===========================================================================


class TestGetAgent:
    def test_found_in_memory(self, reg: UnifiedAgentRegistry):
        a = reg.register(_agent(id="in-mem"))
        result = reg.get_agent("in-mem")
        assert result is not None
        assert result["id"] == "in-mem"

    def test_not_found_returns_none(self, reg: UnifiedAgentRegistry):
        assert reg.get_agent("nobody") is None

    def test_fallback_reads_session_file(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        _write_session(sessions_dir, "file-only", role="scriptor")
        result = reg.get_agent("file-only")
        assert result is not None
        assert result["id"] == "file-only"
        assert result["role"] == "scriptor"

    def test_fallback_populates_memory(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        _write_session(sessions_dir, "cache-it")
        reg.get_agent("cache-it")
        assert "cache-it" in reg._agents

    def test_returns_copy_not_reference(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="copy-test"))
        copy = reg.get_agent("copy-test")
        assert copy is not None
        copy["status"] = "mutated"
        assert reg._agents["copy-test"]["status"] == "active"

    def test_fallback_handles_json_decode_error(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        """Corrupt session file in get_agent fallback logs warning, returns None."""
        (sessions_dir / "session_corrupt-get.json").write_text("{bad json")
        result = reg.get_agent("corrupt-get")
        assert result is None

    def test_fallback_handles_oserror(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        """OSError reading session file in get_agent fallback logs warning, returns None."""
        _write_session(sessions_dir, "oserr-get")

        original_read_text = Path.read_text

        def mock_read_text(self_inner, *args, **kwargs):
            if "oserr-get" in str(self_inner):
                raise OSError("disk error")
            return original_read_text(self_inner, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            result = reg.get_agent("oserr-get")

        assert result is None

    def test_file_contains_non_dict_returns_none(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        """Session file containing a JSON list is treated as missing."""
        (sessions_dir / "session_list-content.json").write_text("[1, 2, 3]")
        result = reg.get_agent("list-content")
        assert result is None


# ===========================================================================
# UnifiedAgentRegistry — list_agents
# ===========================================================================


class TestListAgents:
    def test_empty_registry_returns_empty_list(self, reg: UnifiedAgentRegistry):
        assert reg.list_agents() == []

    def test_returns_all_memory_agents(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="la-1"))
        reg.register(_agent(id="la-2"))
        ids = {a["id"] for a in reg.list_agents()}
        assert ids == {"la-1", "la-2"}

    def test_includes_file_only_agents(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        _write_session(sessions_dir, "file-agent-A")
        reg.register(_agent(id="api-agent-B"))
        ids = {a["id"] for a in reg.list_agents()}
        assert "file-agent-A" in ids
        assert "api-agent-B" in ids

    def test_memory_wins_over_file(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        _write_session(sessions_dir, "overlap", status="idle", progress=0)
        reg.register(_agent(id="overlap", status="active", progress=90))
        agents = reg.list_agents()
        match = next(a for a in agents if a["id"] == "overlap")
        assert match["status"] == "active"
        assert match["progress"] == 90

    def test_status_filter_active(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="s-act", status="active"))
        reg.register(_agent(id="s-idle", status="idle"))
        reg.register(_agent(id="s-done", status="completed"))
        active = reg.list_agents(status="active")
        assert all(a["status"] == "active" for a in active)
        assert len(active) == 1

    def test_status_filter_no_match_returns_empty(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="only-active", status="active"))
        result = reg.list_agents(status="failed")
        assert result == []

    def test_status_filter_works_with_file_agents(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        _write_session(sessions_dir, "f-active", status="active")
        _write_session(sessions_dir, "f-idle", status="idle")
        result = reg.list_agents(status="active")
        ids = {a["id"] for a in result}
        assert "f-active" in ids
        assert "f-idle" not in ids

    def test_no_sessions_dir_still_returns_memory_agents(self, tmp_path: Path):
        reg2 = UnifiedAgentRegistry(sessions_dir=tmp_path / "nonexistent")
        reg2.register(_agent(id="mem-only"))
        agents = reg2.list_agents()
        assert len(agents) == 1
        assert agents[0]["id"] == "mem-only"

    def test_corrupt_file_skipped_valid_file_included(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        (sessions_dir / "session_bad-list.json").write_text("INVALID")
        _write_session(sessions_dir, "good-list")
        agents = reg.list_agents()
        ids = {a["id"] for a in agents}
        assert "good-list" in ids
        assert "bad-list" not in ids

    def test_oserror_in_load_file_agents_skipped(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        """OSError while reading a session file in _load_file_agents is silently skipped."""
        _write_session(sessions_dir, "ok-agent")
        _write_session(sessions_dir, "err-agent")

        original_read_text = Path.read_text

        def mock_read_text(self_inner, *args, **kwargs):
            if "err-agent" in str(self_inner):
                raise OSError("simulated disk error")
            return original_read_text(self_inner, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            agents = reg.list_agents()

        ids = {a["id"] for a in agents}
        assert "ok-agent" in ids
        assert "err-agent" not in ids


# ===========================================================================
# UnifiedAgentRegistry — update_heartbeat
# ===========================================================================


class TestUpdateHeartbeat:
    def test_updates_status(self, reg: UnifiedAgentRegistry):
        a = reg.register(_agent(id="hb-status"))
        result = reg.update_heartbeat("hb-status", {"status": "waiting"})
        assert result is not None
        assert result["status"] == "waiting"

    def test_updates_progress(self, reg: UnifiedAgentRegistry):
        a = reg.register(_agent(id="hb-prog"))
        result = reg.update_heartbeat("hb-prog", {"progress": 77})
        assert result["progress"] == 77

    def test_updates_last_activity(self, reg: UnifiedAgentRegistry):
        a = reg.register(_agent(id="hb-ts"))
        before = a["last_activity"]
        time.sleep(0.01)
        result = reg.update_heartbeat("hb-ts", {})
        assert result["last_activity"] >= before

    def test_updates_last_heartbeat(self, reg: UnifiedAgentRegistry):
        a = reg.register(_agent(id="hb-lhb"))
        before = a["last_heartbeat"]
        time.sleep(0.01)
        result = reg.update_heartbeat("hb-lhb", {})
        assert result["last_heartbeat"] >= before

    def test_id_is_immutable(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="hb-id-guard"))
        reg.update_heartbeat("hb-id-guard", {"id": "hacked"})
        assert "hb-id-guard" in reg._agents
        assert "hacked" not in reg._agents

    def test_session_id_is_immutable(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="hb-sid-guard"))
        reg.update_heartbeat("hb-sid-guard", {"session_id": "hijacked"})
        assert reg._agents["hb-sid-guard"]["session_id"] == "hb-sid-guard"

    def test_registered_at_is_immutable(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="hb-reg-guard", registered_at="2025-01-01T00:00:00+00:00"))
        reg.update_heartbeat("hb-reg-guard", {"registered_at": "2099-01-01T00:00:00+00:00"})
        assert reg._agents["hb-reg-guard"]["registered_at"] == "2025-01-01T00:00:00+00:00"

    def test_returns_none_for_unknown_agent(self, reg: UnifiedAgentRegistry):
        assert reg.update_heartbeat("ghost-id", {"status": "active"}) is None

    def test_hydrates_file_agent(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        _write_session(sessions_dir, "hb-file-agent", status="idle")
        result = reg.update_heartbeat("hb-file-agent", {"status": "active", "progress": 50})
        assert result is not None
        assert result["status"] == "active"
        assert "hb-file-agent" in reg._agents

    def test_writes_file_when_none_exists(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        reg.register(_agent(id="hb-nofile"))
        (sessions_dir / "session_hb-nofile.json").unlink()
        reg.update_heartbeat("hb-nofile", {"progress": 5})
        assert (sessions_dir / "session_hb-nofile.json").exists()

    def test_returns_copy_not_reference(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="hb-copy"))
        result = reg.update_heartbeat("hb-copy", {"progress": 10})
        assert result is not None
        result["progress"] = 999
        assert reg._agents["hb-copy"]["progress"] == 10

    def test_heartbeat_counter_incremented(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="hb-cnt"))
        reg.update_heartbeat("hb-cnt", {})
        assert reg._heartbeat_counter.get("hb-cnt", 0) > 0


# ===========================================================================
# UnifiedAgentRegistry — sync_to_files
# ===========================================================================


class TestSyncToFiles:
    def test_writes_all_in_memory_agents(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        reg.register(_agent(id="stf-1"))
        reg.register(_agent(id="stf-2"))
        for f in sessions_dir.glob("*.json"):
            f.unlink()
        count = reg.sync_to_files()
        assert count == 2
        written = {f.stem.replace("session_", "") for f in sessions_dir.glob("session_*.json")}
        assert written == {"stf-1", "stf-2"}

    def test_returns_count_of_written_files(self, reg: UnifiedAgentRegistry):
        for i in range(4):
            reg.register(_agent(id=f"stf-cnt-{i}"))
        count = reg.sync_to_files()
        assert count == 4

    def test_empty_registry_returns_zero(self, reg: UnifiedAgentRegistry):
        assert reg.sync_to_files() == 0

    def test_updates_last_sync_at(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="stf-ts"))
        assert reg._last_sync_at is None
        reg.sync_to_files()
        assert reg._last_sync_at is not None

    def test_creates_sessions_dir_if_missing(self, tmp_path: Path):
        missing = tmp_path / "new_dir"
        reg2 = UnifiedAgentRegistry(sessions_dir=missing, sync_interval_seconds=30)
        reg2.register(_agent(id="dir-create"))
        import shutil
        shutil.rmtree(missing)
        count = reg2.sync_to_files()
        assert count == 1
        assert missing.exists()

    def test_overwrites_stale_files(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        reg.register(_agent(id="stf-overwrite"))
        (sessions_dir / "session_stf-overwrite.json").write_text(
            json.dumps({"id": "stf-overwrite", "status": "stale"})
        )
        reg._agents["stf-overwrite"]["status"] = "fresh"
        reg.sync_to_files()
        data = json.loads((sessions_dir / "session_stf-overwrite.json").read_text())
        assert data["status"] == "fresh"

    def test_oserror_in_write_session_file_is_logged_not_raised(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        """If _write_session_file raises OSError, sync_to_files logs it and continues."""
        reg.register(_agent(id="stf-err"))

        original_write_text = Path.write_text

        def mock_write_text(self_inner, data, *args, **kwargs):
            if "stf-err" in str(self_inner):
                raise OSError("read-only filesystem")
            return original_write_text(self_inner, data, *args, **kwargs)

        with patch.object(Path, "write_text", mock_write_text):
            # Should not raise; OSError is caught internally
            count = reg.sync_to_files()

        # count may be 0 (error path) but no exception propagated
        assert isinstance(count, int)

    def test_sync_to_files_except_oserror_branch_via_mocked_write(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        """Cover the except-OSError branch in sync_to_files by mocking _write_session_file.

        _write_session_file itself swallows OSError internally, so the only way to
        trigger the except clause in sync_to_files is to mock _write_session_file to
        raise OSError directly.  This verifies the outer error-handling branch (lines
        401-402) is exercised and that sync_to_files does not propagate the exception.
        """
        reg.register(_agent(id="stf-mock-err-1"))
        reg.register(_agent(id="stf-mock-err-2"))

        call_count = {"n": 0}

        def mock_write(agent):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("simulated write failure")

        with patch.object(reg, "_write_session_file", side_effect=mock_write):
            # Must not raise even when _write_session_file raises OSError
            count = reg.sync_to_files()

        # First agent raised OSError (count not incremented), second succeeded
        assert count == 1


# ===========================================================================
# UnifiedAgentRegistry — sync_from_files
# ===========================================================================


class TestSyncFromFiles:
    def test_loads_file_only_agents(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        _write_session(sessions_dir, "sff-1")
        _write_session(sessions_dir, "sff-2")
        count = reg.sync_from_files()
        assert count == 2
        assert "sff-1" in reg._agents
        assert "sff-2" in reg._agents

    def test_memory_wins_over_older_file(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        old_ts = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _write_session(sessions_dir, "sff-old-file", status="idle", last_activity=old_ts)
        reg.register(_agent(id="sff-old-file", status="active"))
        reg.sync_from_files()
        assert reg._agents["sff-old-file"]["status"] == "active"

    def test_newer_file_wins_over_older_memory(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        old_ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        reg.register(_agent(id="sff-old-mem", status="active"))
        reg._agents["sff-old-mem"]["last_activity"] = old_ts

        new_ts = datetime.now(UTC).isoformat()
        _write_session(sessions_dir, "sff-old-mem", status="completed", last_activity=new_ts)
        reg.sync_from_files()
        assert reg._agents["sff-old-mem"]["status"] == "completed"

    def test_missing_dir_returns_zero(self, tmp_path: Path):
        reg2 = UnifiedAgentRegistry(sessions_dir=tmp_path / "missing")
        assert reg2.sync_from_files() == 0

    def test_corrupt_json_skipped(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        (sessions_dir / "session_corrupt-sff.json").write_text("{bad json!!!")
        _write_session(sessions_dir, "valid-sff")
        count = reg.sync_from_files()
        assert count >= 1
        assert "valid-sff" in reg._agents

    def test_non_dict_json_skipped(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        (sessions_dir / "session_list-sff.json").write_text("[1,2,3]")
        count = reg.sync_from_files()
        assert count == 0

    def test_restart_recovery(self, sessions_dir: Path):
        """After a simulated process restart a new registry recovers agents from files."""
        first = UnifiedAgentRegistry(sessions_dir=sessions_dir)
        first.register(_agent(id="persist-A", status="active"))
        first.register(_agent(id="persist-B", status="waiting"))
        first.sync_to_files()

        second = UnifiedAgentRegistry(sessions_dir=sessions_dir)
        assert len(second._agents) == 0
        count = second.sync_from_files()
        assert count == 2
        assert "persist-A" in second._agents
        assert "persist-B" in second._agents

    def test_file_with_no_valid_id_excluded(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        """Session file missing both id and session_id is excluded from results."""
        (sessions_dir / "session_noid.json").write_text(
            json.dumps({"role": "ghost", "status": "active"})
        )
        count = reg.sync_from_files()
        assert count == 0


# ===========================================================================
# UnifiedAgentRegistry — get_stats
# ===========================================================================


class TestGetStats:
    def test_empty_registry(self, reg: UnifiedAgentRegistry):
        stats = reg.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["last_sync_at"] is None

    def test_total_count(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="gs-1"))
        reg.register(_agent(id="gs-2"))
        assert reg.get_stats()["total"] == 2

    def test_by_status_breakdown(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="gs-act-1", status="active"))
        reg.register(_agent(id="gs-act-2", status="active"))
        reg.register(_agent(id="gs-idle", status="idle"))
        stats = reg.get_stats()
        assert stats["by_status"]["active"] == 2
        assert stats["by_status"]["idle"] == 1

    def test_last_sync_at_after_sync(self, reg: UnifiedAgentRegistry):
        reg.register(_agent(id="gs-sync"))
        reg.sync_to_files()
        stats = reg.get_stats()
        assert stats["last_sync_at"] is not None

    def test_includes_file_only_agents(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        _write_session(sessions_dir, "gs-file", status="completed")
        stats = reg.get_stats()
        assert stats["total"] >= 1
        assert stats["by_status"].get("completed", 0) >= 1

    def test_sessions_dir_in_stats(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        assert reg.get_stats()["sessions_dir"] == str(sessions_dir)

    def test_sync_interval_in_stats(self, sessions_dir: Path):
        reg2 = UnifiedAgentRegistry(sessions_dir=sessions_dir, sync_interval_seconds=120)
        assert reg2.get_stats()["sync_interval_seconds"] == 120


# ===========================================================================
# _write_session_file — agent with no id
# ===========================================================================


class TestWriteSessionFileNoId:
    def test_no_id_no_crash(self, reg: UnifiedAgentRegistry):
        """_write_session_file with an agent dict that has no id / session_id logs warning."""
        # Call internal method directly with a no-id agent
        reg._write_session_file({"role": "ghost", "status": "active"})
        # No exception raised; no file written (nothing to assert beyond no-crash)

    def test_no_id_no_file_created(self, reg: UnifiedAgentRegistry, sessions_dir: Path):
        reg._write_session_file({"role": "invisible"})
        assert list(sessions_dir.glob("session_*.json")) == []

    def test_oserror_on_write_logged_not_raised(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        """_write_session_file OSError path is caught and logged."""
        original_write_text = Path.write_text

        def mock_write_text(self_inner, data, *args, **kwargs):
            if "session_wse-err" in str(self_inner):
                raise OSError("write error")
            return original_write_text(self_inner, data, *args, **kwargs)

        with patch.object(Path, "write_text", mock_write_text):
            reg._write_session_file({"id": "wse-err", "status": "active"})
        # No exception raised


# ===========================================================================
# Singleton — get_unified_registry / reset_unified_registry
# ===========================================================================


class TestSingleton:
    def test_returns_same_instance(self, sessions_dir: Path):
        r1 = get_unified_registry(sessions_dir=sessions_dir)
        r2 = get_unified_registry()
        assert r1 is r2

    def test_reset_creates_new_instance(self, sessions_dir: Path):
        r1 = get_unified_registry(sessions_dir=sessions_dir)
        reset_unified_registry()
        r2 = get_unified_registry(sessions_dir=sessions_dir)
        assert r1 is not r2

    def test_initial_state_after_reset(self, sessions_dir: Path):
        r = get_unified_registry(sessions_dir=sessions_dir)
        assert r._agents == {}
        assert r._heartbeat_counter == {}

    def test_params_used_only_on_first_call(self, tmp_path: Path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        r1 = get_unified_registry(sessions_dir=dir1, sync_interval_seconds=10)
        r2 = get_unified_registry(sessions_dir=dir2, sync_interval_seconds=99)
        # Second call params are ignored; first instance is reused
        assert r1 is r2
        assert str(r1._sessions_dir) == str(dir1)

    def test_thread_safe_singleton(self, sessions_dir: Path):
        instances: list[UnifiedAgentRegistry] = []
        errors: list[Exception] = []

        def getter():
            try:
                instances.append(get_unified_registry(sessions_dir=sessions_dir))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=getter) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(inst is instances[0] for inst in instances)

    def test_reset_to_none(self, sessions_dir: Path):
        import forge_harness.webhook_server.services.unified_registry as mod

        get_unified_registry(sessions_dir=sessions_dir)
        assert mod._unified_registry is not None
        reset_unified_registry()
        assert mod._unified_registry is None


# ===========================================================================
# Concurrency — thread-safety under load
# ===========================================================================


class TestConcurrency:
    def test_concurrent_register_unique_ids(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        results: list[dict] = []
        errors: list[Exception] = []

        def worker():
            try:
                results.append(reg.register(_agent()))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        ids = [r["id"] for r in results]
        assert len(ids) == len(set(ids))

    def test_concurrent_heartbeat_no_exception(self, reg: UnifiedAgentRegistry):
        a = reg.register(_agent(id="conc-hb"))
        errors: list[Exception] = []

        def hb_worker():
            for _ in range(20):
                try:
                    reg.update_heartbeat(a["id"], {"progress": 50})
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=hb_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_list_and_register(self, reg: UnifiedAgentRegistry):
        errors: list[Exception] = []

        def list_worker():
            for _ in range(15):
                try:
                    reg.list_agents()
                except Exception as exc:
                    errors.append(exc)

        def reg_worker():
            for _ in range(5):
                try:
                    reg.register(_agent())
                except Exception as exc:
                    errors.append(exc)

        threads = [
            threading.Thread(target=list_worker),
            threading.Thread(target=reg_worker),
            threading.Thread(target=list_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_sync_to_files_no_exception(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        for i in range(5):
            reg.register(_agent(id=f"csf-{i}"))
        errors: list[Exception] = []

        def sync_worker():
            try:
                reg.sync_to_files()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=sync_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ===========================================================================
# _needs_file_flush
# ===========================================================================


class TestNeedsFileFlush:
    def test_true_when_last_sync_is_none(self, reg: UnifiedAgentRegistry):
        assert reg._last_sync_at is None
        assert reg._needs_file_flush() is True

    def test_false_before_interval_elapses(self, reg: UnifiedAgentRegistry):
        reg._last_sync_at = datetime.now(UTC)
        assert reg._needs_file_flush() is False

    def test_true_after_interval_elapses(self, sessions_dir: Path):
        reg2 = UnifiedAgentRegistry(sessions_dir=sessions_dir, sync_interval_seconds=1)
        reg2._last_sync_at = datetime.now(UTC) - timedelta(seconds=2)
        assert reg2._needs_file_flush() is True


# ===========================================================================
# Round-trip: register -> sync_to_files -> sync_from_files -> verify
# ===========================================================================


class TestRoundTrip:
    def test_full_round_trip(self, sessions_dir: Path):
        original = UnifiedAgentRegistry(sessions_dir=sessions_dir)
        original.register(_agent(id="rt-1", status="active", progress=42))
        original.register(_agent(id="rt-2", status="idle", progress=0))
        original.sync_to_files()

        recovered = UnifiedAgentRegistry(sessions_dir=sessions_dir)
        recovered.sync_from_files()

        for aid, expected_status in [("rt-1", "active"), ("rt-2", "idle")]:
            agent = recovered.get_agent(aid)
            assert agent is not None, f"Agent {aid} not recovered"
            assert agent["status"] == expected_status

    def test_file_data_survives_json_serialisation(
        self, reg: UnifiedAgentRegistry, sessions_dir: Path
    ):
        reg.register(
            _agent(
                id="json-rt",
                skills=["python", "sql"],
                token_usage={"input": 500, "output": 200},
                files_modified=["app.py"],
            )
        )
        raw = json.loads((sessions_dir / "session_json-rt.json").read_text())
        assert raw["skills"] == ["python", "sql"]
        assert raw["token_usage"] == {"input": 500, "output": 200}
        assert raw["files_modified"] == ["app.py"]
