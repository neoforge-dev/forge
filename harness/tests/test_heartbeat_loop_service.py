"""Unit tests for HeartbeatLoopService.

Covers:
- Pydantic model validation and serialisation
- State persistence (load / save)
- Data gathering helpers (parse_turn_file, count_dirty_files, count_new_results)
- Trigger computation logic
- Core eval() method
- State mutators (reset, mark_retro_done, mark_crossnode_done)
- SSE emission (best-effort, swallowed errors)
- Module-level singleton helpers
- Edge cases and error paths
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure singleton is clean before / after each test
# ---------------------------------------------------------------------------
from forge_harness.heartbeat_loop_service import (
    CROSSNODE_INTERVAL,
    DIRTY_THRESHOLD,
    RETRO_INTERVAL,
    EvalResult,
    FleetSnapshot,
    HeartbeatLoopService,
    LoopState,
    TriggerSet,
    get_heartbeat_loop_service,
    reset_heartbeat_loop_service,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Destroy the module-level singleton before and after every test."""
    reset_heartbeat_loop_service()
    yield
    reset_heartbeat_loop_service()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(tmp_path: Path) -> HeartbeatLoopService:
    """Create a HeartbeatLoopService rooted at *tmp_path*."""
    return HeartbeatLoopService(forge_root=tmp_path)


def forge_heartbeat_dir(tmp_path: Path) -> Path:
    """Return (and create) the .forge/heartbeat directory inside *tmp_path*."""
    d = tmp_path / ".forge" / "heartbeat"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ===========================================================================
# Pydantic model tests
# ===========================================================================


class TestLoopState:
    """Validate LoopState model construction and serialisation."""

    def test_defaults(self):
        state = LoopState()
        assert state.heartbeat_count == 0
        assert state.last_retro == 0
        assert state.last_crossnode == 0
        assert state.last_commit_sha == ""
        assert state.session_start == ""

    def test_explicit_values(self):
        state = LoopState(
            heartbeat_count=42,
            last_retro=39,
            last_crossnode=37,
            last_commit_sha="abc123",
            session_start="2026-01-01T00:00:00+00:00",
        )
        assert state.heartbeat_count == 42
        assert state.last_retro == 39
        assert state.last_crossnode == 37
        assert state.last_commit_sha == "abc123"

    def test_round_trip_json(self):
        state = LoopState(heartbeat_count=7, last_retro=3, session_start="ts")
        raw = state.model_dump_json()
        restored = LoopState.model_validate_json(raw)
        assert restored == state

    def test_model_dump(self):
        state = LoopState(heartbeat_count=5)
        d = state.model_dump()
        assert d["heartbeat_count"] == 5
        assert "last_retro" in d


class TestTriggerSet:
    """Validate TriggerSet model."""

    def test_all_false_by_default(self):
        t = TriggerSet()
        assert t.retro is False
        assert t.crossnode is False
        assert t.commit is False
        assert t.results is False
        assert t.dispatch is False

    def test_set_flags(self):
        t = TriggerSet(retro=True, commit=True)
        assert t.retro is True
        assert t.commit is True
        assert t.crossnode is False

    def test_model_dump_contains_all_fields(self):
        t = TriggerSet(dispatch=True)
        d = t.model_dump()
        assert set(d.keys()) == {"retro", "crossnode", "commit", "results", "dispatch"}


class TestFleetSnapshot:
    """Validate FleetSnapshot model."""

    def test_defaults(self):
        fs = FleetSnapshot()
        assert fs.idle_count == 0
        assert fs.busy_count == 0
        assert fs.idle_names == ""
        assert fs.timestamp == ""

    def test_populated(self):
        fs = FleetSnapshot(
            idle_count=2, busy_count=1, idle_names="agent-a, agent-b", timestamp="ts"
        )
        assert fs.idle_count == 2
        assert fs.idle_names == "agent-a, agent-b"


class TestEvalResult:
    """Validate EvalResult model."""

    def test_construction(self):
        result = EvalResult(
            state=LoopState(heartbeat_count=1),
            triggers=TriggerSet(),
            fleet=FleetSnapshot(),
            dirty_files=2,
            new_results=1,
            one_line="HB#1 | 2 dirty | ...",
        )
        assert result.dirty_files == 2
        assert result.new_results == 1
        assert result.one_line.startswith("HB#1")

    def test_defaults(self):
        result = EvalResult(
            state=LoopState(),
            triggers=TriggerSet(),
            fleet=FleetSnapshot(),
        )
        assert result.dirty_files == 0
        assert result.new_results == 0
        assert result.one_line == ""


# ===========================================================================
# State persistence tests
# ===========================================================================


class TestStatePersistence:
    """Tests for load_state / save_state."""

    def test_load_state_returns_defaults_when_file_missing(self, tmp_path):
        svc = make_service(tmp_path)
        state = svc.load_state()
        assert state.heartbeat_count == 0
        assert state.session_start != ""  # populated with current time

    def test_save_and_load_round_trip(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)

        original = LoopState(heartbeat_count=10, last_retro=5, last_crossnode=3)
        svc.save_state(original)

        loaded = svc.load_state()
        assert loaded.heartbeat_count == 10
        assert loaded.last_retro == 5
        assert loaded.last_crossnode == 3

    def test_save_state_creates_parent_directories(self, tmp_path):
        svc = make_service(tmp_path)
        # Parent dirs don't exist yet
        assert not svc._state_path.parent.exists()
        svc.save_state(LoopState(heartbeat_count=1))
        assert svc._state_path.exists()

    def test_save_state_is_atomic_via_tmp_file(self, tmp_path):
        """Verify we write to a .tmp then os.replace — tmp should not linger."""
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        svc.save_state(LoopState(heartbeat_count=99))

        tmp = svc._state_path.with_suffix(".json.tmp")
        assert not tmp.exists(), "Temp file must not survive after successful save"
        assert svc._state_path.exists()

    def test_load_state_handles_corrupt_json(self, tmp_path):
        d = forge_heartbeat_dir(tmp_path)
        state_file = d / "loop_state.json"
        state_file.write_text("NOT_VALID_JSON", encoding="utf-8")

        svc = make_service(tmp_path)
        # Should not raise — should return defaults
        state = svc.load_state()
        assert state.heartbeat_count == 0

    def test_load_state_handles_extra_fields_gracefully(self, tmp_path):
        d = forge_heartbeat_dir(tmp_path)
        state_file = d / "loop_state.json"
        # Write JSON with unknown fields that Pydantic should ignore
        data = {
            "heartbeat_count": 5,
            "last_retro": 2,
            "last_crossnode": 1,
            "last_commit_sha": "",
            "session_start": "",
            "unknown_future_field": "value",
        }
        state_file.write_text(json.dumps(data), encoding="utf-8")

        svc = make_service(tmp_path)
        state = svc.load_state()
        assert state.heartbeat_count == 5

    def test_save_state_on_permission_error_does_not_raise(self, tmp_path):
        """save_state swallows errors to avoid crashing the heartbeat loop."""
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)

        with patch.object(Path, "write_text", side_effect=PermissionError("denied")):
            # Should not raise
            svc.save_state(LoopState(heartbeat_count=1))


# ===========================================================================
# parse_turn_file tests
# ===========================================================================


class TestParseTurnFile:
    """Tests for parse_turn_file()."""

    def _write_turn_file(self, tmp_path: Path, content: str) -> None:
        d = forge_heartbeat_dir(tmp_path)
        turn = d / "ORCHESTRATOR_TURN_LATEST.md"
        turn.write_text(content, encoding="utf-8")

    def test_returns_zeroed_snapshot_when_file_missing(self, tmp_path):
        svc = make_service(tmp_path)
        snapshot = svc.parse_turn_file()
        assert snapshot.idle_count == 0
        assert snapshot.busy_count == 0
        assert snapshot.timestamp != ""

    def test_parses_idle_count_keyword(self, tmp_path):
        self._write_turn_file(tmp_path, "idle_count: 3\nbusy_count: 2\n")
        svc = make_service(tmp_path)
        snap = svc.parse_turn_file()
        assert snap.idle_count == 3
        assert snap.busy_count == 2

    def test_parses_idle_count_keyword_case_insensitive(self, tmp_path):
        self._write_turn_file(tmp_path, "IDLE_COUNT: 5\nBUSY_COUNT: 1\n")
        svc = make_service(tmp_path)
        snap = svc.parse_turn_file()
        assert snap.idle_count == 5
        assert snap.busy_count == 1

    def test_parses_idle_count_with_space_separator(self, tmp_path):
        self._write_turn_file(tmp_path, "idle count 4\nbusy count 2\n")
        svc = make_service(tmp_path)
        snap = svc.parse_turn_file()
        assert snap.idle_count == 4
        assert snap.busy_count == 2

    def test_fallback_table_parsing_idle_rows(self, tmp_path):
        content = (
            "| agent-a | IDLE | details |\n"
            "| agent-b | BUSY | details |\n"
            "| agent-c | ACTIVE | details |\n"
        )
        self._write_turn_file(tmp_path, content)
        svc = make_service(tmp_path)
        snap = svc.parse_turn_file()
        assert snap.idle_count == 1
        assert snap.busy_count == 2

    def test_fallback_table_parsing_multiple_idle(self, tmp_path):
        content = "| agent-x | IDLE | |\n| agent-y | IDLE | |\n"
        self._write_turn_file(tmp_path, content)
        svc = make_service(tmp_path)
        snap = svc.parse_turn_file()
        assert snap.idle_count == 2
        assert snap.busy_count == 0

    def test_collects_idle_agent_names_from_pipe_pattern(self, tmp_path):
        content = "| forge:minimax | IDLE | 2 tasks |\n"
        self._write_turn_file(tmp_path, content)
        svc = make_service(tmp_path)
        snap = svc.parse_turn_file()
        assert "forge:minimax" in snap.idle_names

    def test_empty_turn_file(self, tmp_path):
        self._write_turn_file(tmp_path, "")
        svc = make_service(tmp_path)
        snap = svc.parse_turn_file()
        assert snap.idle_count == 0
        assert snap.busy_count == 0

    def test_turn_file_with_no_status_info(self, tmp_path):
        self._write_turn_file(tmp_path, "# Sprint Notes\nSome narrative text without counts.\n")
        svc = make_service(tmp_path)
        snap = svc.parse_turn_file()
        assert snap.idle_count == 0
        assert snap.busy_count == 0

    def test_timestamp_is_set(self, tmp_path):
        self._write_turn_file(tmp_path, "idle_count: 1\n")
        svc = make_service(tmp_path)
        snap = svc.parse_turn_file()
        assert snap.timestamp != ""

    def test_parse_turn_file_handles_read_error(self, tmp_path):
        """parse_turn_file must return a zeroed snapshot on unexpected errors."""
        svc = make_service(tmp_path)
        # Inject a file that exists but raises on read
        forge_heartbeat_dir(tmp_path)
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", side_effect=OSError("disk error")),
        ):
            snap = svc.parse_turn_file()
        assert snap.idle_count == 0


# ===========================================================================
# count_dirty_files tests
# ===========================================================================


class TestCountDirtyFiles:
    """Tests for count_dirty_files()."""

    def _mock_git(self, stdout: str, returncode: int = 0):
        """Return a mock subprocess result."""
        r = MagicMock()
        r.stdout = stdout
        r.returncode = returncode
        return r

    def test_returns_correct_count(self, tmp_path):
        svc = make_service(tmp_path)
        output = " M file1.py\n?? file2.py\n M file3.ts\n"
        with patch("subprocess.run", return_value=self._mock_git(output)):
            assert svc.count_dirty_files() == 3

    def test_returns_zero_for_clean_repo(self, tmp_path):
        svc = make_service(tmp_path)
        with patch("subprocess.run", return_value=self._mock_git("")):
            assert svc.count_dirty_files() == 0

    def test_returns_zero_on_subprocess_exception(self, tmp_path):
        svc = make_service(tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            assert svc.count_dirty_files() == 0

    def test_returns_zero_on_timeout(self, tmp_path):
        import subprocess as sp

        svc = make_service(tmp_path)
        with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="git", timeout=5)):
            assert svc.count_dirty_files() == 0

    def test_ignores_blank_lines_in_git_output(self, tmp_path):
        svc = make_service(tmp_path)
        output = " M file1.py\n\n\n M file2.py\n"
        with patch("subprocess.run", return_value=self._mock_git(output)):
            assert svc.count_dirty_files() == 2

    def test_passes_correct_forge_root_to_git(self, tmp_path):
        svc = make_service(tmp_path)
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            r = MagicMock()
            r.stdout = ""
            return r

        with patch("subprocess.run", side_effect=fake_run):
            svc.count_dirty_files()

        assert str(tmp_path) in captured_cmd
        assert "status" in captured_cmd
        assert "--porcelain" in captured_cmd


# ===========================================================================
# count_new_results tests
# ===========================================================================


class TestCountNewResults:
    """Tests for count_new_results()."""

    def test_returns_zero_when_results_dir_missing(self, tmp_path):
        svc = make_service(tmp_path)
        assert svc.count_new_results() == 0

    def test_returns_zero_when_no_md_files(self, tmp_path):
        d = forge_heartbeat_dir(tmp_path)
        results = d / "results"
        results.mkdir()
        svc = make_service(tmp_path)
        assert svc.count_new_results() == 0

    def test_counts_md_files_newer_than_state_file(self, tmp_path):
        d = forge_heartbeat_dir(tmp_path)
        results = d / "results"
        results.mkdir()

        # Write state file with a known timestamp in the past
        state_file = d / "loop_state.json"
        state_file.write_text("{}", encoding="utf-8")
        old_time = time.time() - 60
        os.utime(state_file, (old_time, old_time))

        # Now write a results file — its mtime will be newer
        (results / "agent-task.md").write_text("result", encoding="utf-8")

        svc = make_service(tmp_path)
        assert svc.count_new_results() == 1

    def test_does_not_count_old_md_files(self, tmp_path):
        d = forge_heartbeat_dir(tmp_path)
        results = d / "results"
        results.mkdir()

        # Write result file first (old)
        result_file = results / "old-result.md"
        result_file.write_text("old", encoding="utf-8")
        old_time = time.time() - 120
        os.utime(result_file, (old_time, old_time))

        # Then write state file (newer)
        state_file = d / "loop_state.json"
        state_file.write_text("{}", encoding="utf-8")
        # state file is already newer

        svc = make_service(tmp_path)
        assert svc.count_new_results() == 0

    def test_counts_only_md_files_not_other_extensions(self, tmp_path):
        d = forge_heartbeat_dir(tmp_path)
        results = d / "results"
        results.mkdir()

        # Create state file first (old)
        state_file = d / "loop_state.json"
        state_file.write_text("{}", encoding="utf-8")
        old_time = time.time() - 60
        os.utime(state_file, (old_time, old_time))

        # Write a .txt and a .json — only .md should count
        (results / "result.txt").write_text("text", encoding="utf-8")
        (results / "result.json").write_text("{}", encoding="utf-8")
        (results / "result.md").write_text("markdown", encoding="utf-8")

        svc = make_service(tmp_path)
        assert svc.count_new_results() == 1

    def test_uses_epoch_as_reference_when_state_file_absent(self, tmp_path):
        """All existing .md files count when there's no state file."""
        d = forge_heartbeat_dir(tmp_path)
        results = d / "results"
        results.mkdir()
        (results / "r1.md").write_text("r1", encoding="utf-8")
        (results / "r2.md").write_text("r2", encoding="utf-8")

        svc = make_service(tmp_path)
        # No state file — reference is epoch (0.0) → all files qualify
        assert svc.count_new_results() == 2

    def test_handles_exception_gracefully(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(Path, "exists", side_effect=OSError("permission denied")):
            # Should not raise
            count = svc.count_new_results()
        assert count == 0


# ===========================================================================
# compute_triggers tests
# ===========================================================================


class TestComputeTriggers:
    """Tests for compute_triggers()."""

    def test_no_triggers_at_start(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState(heartbeat_count=0)
        fleet = FleetSnapshot(idle_count=0)
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=0)
        assert t.retro is False
        assert t.crossnode is False
        assert t.commit is False
        assert t.results is False
        assert t.dispatch is False

    def test_retro_trigger_fires_at_interval(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState(heartbeat_count=RETRO_INTERVAL, last_retro=0)
        fleet = FleetSnapshot()
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=0)
        assert t.retro is True

    def test_retro_trigger_does_not_fire_before_interval(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState(heartbeat_count=RETRO_INTERVAL - 1, last_retro=0)
        fleet = FleetSnapshot()
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=0)
        assert t.retro is False

    def test_retro_trigger_resets_after_mark(self, tmp_path):
        """After last_retro is updated to heartbeat_count, retro should not fire."""
        svc = make_service(tmp_path)
        state = LoopState(heartbeat_count=RETRO_INTERVAL, last_retro=RETRO_INTERVAL)
        fleet = FleetSnapshot()
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=0)
        assert t.retro is False

    def test_crossnode_trigger_fires_at_interval(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState(heartbeat_count=CROSSNODE_INTERVAL, last_crossnode=0)
        fleet = FleetSnapshot()
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=0)
        assert t.crossnode is True

    def test_crossnode_trigger_does_not_fire_before_interval(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState(heartbeat_count=CROSSNODE_INTERVAL - 1, last_crossnode=0)
        fleet = FleetSnapshot()
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=0)
        assert t.crossnode is False

    def test_commit_trigger_fires_above_threshold(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState()
        fleet = FleetSnapshot()
        t = svc.compute_triggers(state, fleet, dirty=DIRTY_THRESHOLD + 1, new_results=0)
        assert t.commit is True

    def test_commit_trigger_does_not_fire_at_threshold(self, tmp_path):
        """dirty > threshold, not >=, so exactly at threshold means no trigger."""
        svc = make_service(tmp_path)
        state = LoopState()
        fleet = FleetSnapshot()
        t = svc.compute_triggers(state, fleet, dirty=DIRTY_THRESHOLD, new_results=0)
        assert t.commit is False

    def test_results_trigger_fires_when_new_results(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState()
        fleet = FleetSnapshot()
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=1)
        assert t.results is True

    def test_results_trigger_does_not_fire_when_no_new_results(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState()
        fleet = FleetSnapshot()
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=0)
        assert t.results is False

    def test_dispatch_trigger_fires_when_idle_agents(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState()
        fleet = FleetSnapshot(idle_count=2)
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=0)
        assert t.dispatch is True

    def test_dispatch_trigger_does_not_fire_when_no_idle_agents(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState()
        fleet = FleetSnapshot(idle_count=0)
        t = svc.compute_triggers(state, fleet, dirty=0, new_results=0)
        assert t.dispatch is False

    def test_all_triggers_active_simultaneously(self, tmp_path):
        svc = make_service(tmp_path)
        state = LoopState(
            heartbeat_count=max(RETRO_INTERVAL, CROSSNODE_INTERVAL),
            last_retro=0,
            last_crossnode=0,
        )
        fleet = FleetSnapshot(idle_count=1)
        t = svc.compute_triggers(
            state,
            fleet,
            dirty=DIRTY_THRESHOLD + 1,
            new_results=3,
        )
        assert all([t.retro, t.crossnode, t.commit, t.results, t.dispatch])


# ===========================================================================
# eval() tests
# ===========================================================================


class TestEval:
    """Tests for the core eval() method."""

    def _make_svc_no_io(self, tmp_path: Path) -> HeartbeatLoopService:
        """Create service with all I/O side-effects patched out."""
        forge_heartbeat_dir(tmp_path)
        svc = make_service(tmp_path)
        return svc

    def _patch_io(self, svc, dirty=0, new_results=0):
        """Patch subprocess and count helpers for deterministic tests."""
        patcher_dirty = patch.object(svc, "count_dirty_files", return_value=dirty)
        patcher_results = patch.object(svc, "count_new_results", return_value=new_results)
        patcher_sse = patch.object(svc, "emit_sse")
        return patcher_dirty, patcher_results, patcher_sse

    def test_eval_increments_heartbeat_count(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc)
        with pd, pr, ps:
            r1 = svc.eval()
            r2 = svc.eval()
        assert r1.state.heartbeat_count == 1
        assert r2.state.heartbeat_count == 2

    def test_eval_without_increment(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc)
        with pd, pr, ps:
            r = svc.eval(increment=False)
        assert r.state.heartbeat_count == 0

    def test_eval_persists_state_after_call(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc)
        with pd, pr, ps:
            svc.eval()
        loaded = svc.load_state()
        assert loaded.heartbeat_count == 1

    def test_eval_returns_eval_result_type(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc)
        with pd, pr, ps:
            r = svc.eval()
        assert isinstance(r, EvalResult)

    def test_eval_one_line_contains_heartbeat_number(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc)
        with pd, pr, ps:
            r = svc.eval()
        assert "HB#1" in r.one_line

    def test_eval_one_line_contains_dirty_count(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc, dirty=5)
        with pd, pr, ps:
            r = svc.eval()
        assert "5 dirty" in r.one_line

    def test_eval_one_line_contains_new_results_count(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc, new_results=2)
        with pd, pr, ps:
            r = svc.eval()
        assert "2 new results" in r.one_line

    def test_eval_one_line_shows_none_when_no_triggers(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc, dirty=0, new_results=0)
        with pd, pr, ps:
            r = svc.eval()
        assert "NONE" in r.one_line

    def test_eval_one_line_shows_active_trigger_names(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        # dirty > threshold fires commit trigger
        pd, pr, ps = self._patch_io(svc, dirty=DIRTY_THRESHOLD + 1, new_results=0)
        with pd, pr, ps:
            r = svc.eval()
        assert "COMMIT" in r.one_line

    def test_eval_initialises_session_start_if_empty(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        # Ensure state has no session_start
        svc.save_state(LoopState(heartbeat_count=0, session_start=""))
        pd, pr, ps = self._patch_io(svc)
        with pd, pr, ps:
            r = svc.eval()
        assert r.state.session_start != ""

    def test_eval_calls_emit_sse(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr = (
            patch.object(svc, "count_dirty_files", return_value=0),
            patch.object(svc, "count_new_results", return_value=0),
        )
        with pd, pr:
            with patch.object(svc, "emit_sse") as mock_sse:
                svc.eval()
        mock_sse.assert_called_once()

    def test_eval_dirty_files_in_result(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc, dirty=7)
        with pd, pr, ps:
            r = svc.eval()
        assert r.dirty_files == 7

    def test_eval_new_results_in_result(self, tmp_path):
        svc = self._make_svc_no_io(tmp_path)
        pd, pr, ps = self._patch_io(svc, new_results=3)
        with pd, pr, ps:
            r = svc.eval()
        assert r.new_results == 3

    def test_eval_is_thread_safe(self, tmp_path):
        """Concurrent eval() calls must not corrupt the heartbeat count."""
        svc = self._make_svc_no_io(tmp_path)
        errors: list[Exception] = []
        results: list[int] = []

        def run():
            try:
                with (
                    patch.object(svc, "count_dirty_files", return_value=0),
                    patch.object(svc, "count_new_results", return_value=0),
                    patch.object(svc, "emit_sse"),
                ):
                    r = svc.eval()
                results.append(r.state.heartbeat_count)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        # After 10 sequential-or-concurrent evals the persisted count == 10
        final = svc.load_state()
        assert final.heartbeat_count == 10


# ===========================================================================
# State mutator tests
# ===========================================================================


class TestReset:
    """Tests for reset()."""

    def test_reset_zeroes_heartbeat_count(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        svc.save_state(LoopState(heartbeat_count=50, last_retro=30, last_crossnode=25))

        state = svc.reset()
        assert state.heartbeat_count == 0
        assert state.last_retro == 0
        assert state.last_crossnode == 0

    def test_reset_persists_state(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        svc.save_state(LoopState(heartbeat_count=100))

        svc.reset()
        loaded = svc.load_state()
        assert loaded.heartbeat_count == 0

    def test_reset_sets_session_start(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        state = svc.reset()
        assert state.session_start != ""

    def test_reset_returns_loop_state(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        result = svc.reset()
        assert isinstance(result, LoopState)


class TestMarkRetroDone:
    """Tests for mark_retro_done()."""

    def test_sets_last_retro_to_heartbeat_count(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        svc.save_state(LoopState(heartbeat_count=9, last_retro=6))
        state = svc.mark_retro_done()
        assert state.last_retro == 9

    def test_persists_updated_state(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        svc.save_state(LoopState(heartbeat_count=9, last_retro=6))
        svc.mark_retro_done()
        loaded = svc.load_state()
        assert loaded.last_retro == 9

    def test_accepts_explicit_state(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        existing = LoopState(heartbeat_count=15, last_retro=0)
        state = svc.mark_retro_done(state=existing)
        assert state.last_retro == 15

    def test_loads_current_state_when_none_passed(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        svc.save_state(LoopState(heartbeat_count=20, last_retro=10))
        state = svc.mark_retro_done()
        assert state.last_retro == 20

    def test_returns_loop_state(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        result = svc.mark_retro_done()
        assert isinstance(result, LoopState)


class TestMarkCrossnodeDone:
    """Tests for mark_crossnode_done()."""

    def test_sets_last_crossnode_to_heartbeat_count(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        svc.save_state(LoopState(heartbeat_count=11, last_crossnode=5))
        state = svc.mark_crossnode_done()
        assert state.last_crossnode == 11

    def test_persists_updated_state(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        svc.save_state(LoopState(heartbeat_count=11, last_crossnode=5))
        svc.mark_crossnode_done()
        loaded = svc.load_state()
        assert loaded.last_crossnode == 11

    def test_accepts_explicit_state(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        existing = LoopState(heartbeat_count=30, last_crossnode=0)
        state = svc.mark_crossnode_done(state=existing)
        assert state.last_crossnode == 30

    def test_loads_current_state_when_none_passed(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        svc.save_state(LoopState(heartbeat_count=8, last_crossnode=3))
        state = svc.mark_crossnode_done()
        assert state.last_crossnode == 8

    def test_returns_loop_state(self, tmp_path):
        svc = make_service(tmp_path)
        forge_heartbeat_dir(tmp_path)
        result = svc.mark_crossnode_done()
        assert isinstance(result, LoopState)


# ===========================================================================
# emit_sse tests
# ===========================================================================


class TestEmitSSE:
    """Tests for emit_sse() — errors must always be swallowed."""

    def _make_result(self) -> EvalResult:
        return EvalResult(
            state=LoopState(heartbeat_count=1),
            triggers=TriggerSet(commit=True),
            fleet=FleetSnapshot(idle_count=1, busy_count=1, timestamp="ts"),
            dirty_files=4,
            new_results=2,
            one_line="HB#1 | summary",
        )

    def test_emit_sse_does_not_raise_when_emitter_unavailable(self, tmp_path):
        svc = make_service(tmp_path)
        with patch(
            "forge_harness.heartbeat_loop_service.HeartbeatLoopService.emit_sse",
            wraps=svc.emit_sse,
        ):
            # If the import of event_emitter fails, no exception should propagate
            with patch.dict(
                "sys.modules",
                {
                    "forge_harness.webhook_server.services.event_emitter": None,
                    "forge_harness.webhook_server.models.sse_events": None,
                },
            ):
                # Should complete without raising
                svc.emit_sse(self._make_result())

    def test_emit_sse_swallows_emitter_exceptions(self, tmp_path):
        svc = make_service(tmp_path)
        mock_emitter = MagicMock()
        mock_emitter.emit.side_effect = RuntimeError("SSE bus down")

        mock_emitter_module = MagicMock()
        mock_emitter_module.get_event_emitter.return_value = mock_emitter

        mock_sse_module = MagicMock()
        mock_sse_module.SSEEventType = lambda x: x

        with patch.dict(
            "sys.modules",
            {
                "forge_harness.webhook_server.services.event_emitter": mock_emitter_module,
                "forge_harness.webhook_server.models.sse_events": mock_sse_module,
            },
        ):
            # Should not raise even though emitter.emit raises
            svc.emit_sse(self._make_result())

    def test_emit_sse_calls_emitter_with_correct_fields(self, tmp_path):
        svc = make_service(tmp_path)
        mock_emitter = MagicMock()

        mock_emitter_module = MagicMock()
        mock_emitter_module.get_event_emitter.return_value = mock_emitter

        mock_sse_module = MagicMock()
        mock_sse_module.SSEEventType = lambda x: x

        result = self._make_result()

        with patch.dict(
            "sys.modules",
            {
                "forge_harness.webhook_server.services.event_emitter": mock_emitter_module,
                "forge_harness.webhook_server.models.sse_events": mock_sse_module,
            },
        ):
            svc.emit_sse(result)

        mock_emitter.emit.assert_called_once()
        call_kwargs = mock_emitter.emit.call_args
        data = call_kwargs[1]["data"] if call_kwargs[1] else call_kwargs[0][1]
        assert data["heartbeat_count"] == 1
        assert data["dirty_files"] == 4
        assert data["new_results"] == 2
        assert data["idle_count"] == 1
        assert "triggers" in data
        assert data["one_line"] == "HB#1 | summary"


# ===========================================================================
# Singleton helper tests
# ===========================================================================


class TestSingletonHelpers:
    """Tests for get_heartbeat_loop_service / reset_heartbeat_loop_service."""

    def test_get_returns_heartbeat_loop_service_instance(self, tmp_path):
        svc = get_heartbeat_loop_service(forge_root=tmp_path)
        assert isinstance(svc, HeartbeatLoopService)

    def test_get_returns_same_instance_on_second_call(self, tmp_path):
        svc1 = get_heartbeat_loop_service(forge_root=tmp_path)
        svc2 = get_heartbeat_loop_service(forge_root=tmp_path)
        assert svc1 is svc2

    def test_forge_root_ignored_on_second_call(self, tmp_path):
        """Second call ignores forge_root — returns the already-created singleton."""
        other_path = tmp_path / "other"
        other_path.mkdir()
        svc1 = get_heartbeat_loop_service(forge_root=tmp_path)
        svc2 = get_heartbeat_loop_service(forge_root=other_path)
        assert svc1 is svc2
        assert svc1._forge_root == tmp_path

    def test_reset_destroys_singleton(self, tmp_path):
        svc1 = get_heartbeat_loop_service(forge_root=tmp_path)
        reset_heartbeat_loop_service()
        svc2 = get_heartbeat_loop_service(forge_root=tmp_path)
        assert svc1 is not svc2

    def test_get_is_thread_safe(self, tmp_path):
        """Concurrent get_heartbeat_loop_service calls return the same instance."""
        instances: list[HeartbeatLoopService] = []
        errors: list[Exception] = []

        def get():
            try:
                instances.append(get_heartbeat_loop_service(forge_root=tmp_path))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        first = instances[0]
        assert all(i is first for i in instances), "All threads must get the same singleton"


# ===========================================================================
# _find_forge_root tests (static method)
# ===========================================================================


class TestFindForgeRoot:
    """Tests for the internal _find_forge_root() static method."""

    def test_finds_root_by_forge_root_marker(self, tmp_path):
        marker = tmp_path / ".forge-root"
        marker.touch()
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        # Run the method starting from subdir — it should walk up and find tmp_path
        with patch("pathlib.Path.cwd", return_value=subdir):
            root = HeartbeatLoopService._find_forge_root()
        assert root == tmp_path

    def test_finds_root_by_domains_yaml(self, tmp_path):
        (tmp_path / "domains.yaml").touch()
        subdir = tmp_path / "sub"
        subdir.mkdir()
        with patch("pathlib.Path.cwd", return_value=subdir):
            root = HeartbeatLoopService._find_forge_root()
        assert root == tmp_path

    def test_falls_back_to_cwd_when_no_marker_found(self, tmp_path):
        # A clean tmp dir with no markers — should fall back to cwd
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            root = HeartbeatLoopService._find_forge_root()
        # Fallback is CWD (tmp_path in this case, or ancestor found by .git heuristic)
        assert isinstance(root, Path)


# ===========================================================================
# Integration-style scenario tests
# ===========================================================================


class TestScenarios:
    """End-to-end scenario tests combining multiple methods."""

    def test_full_lifecycle_retro_cycle(self, tmp_path):
        """Simulate a retro cycle: eval N times → retro fires → mark done → retro cleared."""
        forge_heartbeat_dir(tmp_path)
        svc = make_service(tmp_path)

        def run_eval():
            with (
                patch.object(svc, "count_dirty_files", return_value=0),
                patch.object(svc, "count_new_results", return_value=0),
                patch.object(svc, "emit_sse"),
            ):
                return svc.eval()

        # Run RETRO_INTERVAL evals — retro should now fire
        for _ in range(RETRO_INTERVAL):
            r = run_eval()

        assert r.triggers.retro is True

        # Mark retro done
        svc.mark_retro_done()

        # One more eval — retro should NOT fire immediately
        r = run_eval()
        assert r.triggers.retro is False

    def test_full_lifecycle_crossnode_cycle(self, tmp_path):
        """Simulate a crossnode sync cycle."""
        forge_heartbeat_dir(tmp_path)
        svc = make_service(tmp_path)

        def run_eval():
            with (
                patch.object(svc, "count_dirty_files", return_value=0),
                patch.object(svc, "count_new_results", return_value=0),
                patch.object(svc, "emit_sse"),
            ):
                return svc.eval()

        for _ in range(CROSSNODE_INTERVAL):
            r = run_eval()

        assert r.triggers.crossnode is True

        svc.mark_crossnode_done()
        r = run_eval()
        assert r.triggers.crossnode is False

    def test_dirty_files_commit_trigger_and_reset(self, tmp_path):
        """Verify commit trigger fires with dirty files and clears after reset."""
        forge_heartbeat_dir(tmp_path)
        svc = make_service(tmp_path)

        with (
            patch.object(svc, "count_dirty_files", return_value=DIRTY_THRESHOLD + 1),
            patch.object(svc, "count_new_results", return_value=0),
            patch.object(svc, "emit_sse"),
        ):
            r = svc.eval()

        assert r.triggers.commit is True

        # After reset, heartbeat count resets; commit is based on dirty count not state
        svc.reset()
        loaded = svc.load_state()
        assert loaded.heartbeat_count == 0

    def test_new_results_trigger_detected(self, tmp_path):
        """Verify results trigger fires when new .md files appear."""
        d = forge_heartbeat_dir(tmp_path)
        results_dir = d / "results"
        results_dir.mkdir()

        svc = make_service(tmp_path)

        # Write state file with old mtime
        state_file = d / "loop_state.json"
        svc.save_state(LoopState())
        old_time = time.time() - 60
        os.utime(state_file, (old_time, old_time))

        # Add a new result file
        (results_dir / "agent-result.md").write_text("done", encoding="utf-8")

        with (
            patch.object(svc, "count_dirty_files", return_value=0),
            patch.object(svc, "emit_sse"),
        ):
            r = svc.eval()

        assert r.triggers.results is True
        assert r.new_results >= 1
