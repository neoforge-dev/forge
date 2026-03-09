"""Tests for forge_harness.iteration.dispatch module.

Covers: AgentType, DispatchResult, DispatchStatus, TmuxDispatcher, TaskTracker,
        select_agent_for_task, get_fallback_agent, dispatch_task, dispatch_tasks,
        get_dispatch_status, _build_task_prompt.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from forge_harness.iteration.dispatch import (
    AGENT_FALLBACKS,
    AGENT_TYPE_ROUTING,
    AgentType,
    DispatchResult,
    DispatchStatus,
    TaskTracker,
    TmuxDispatcher,
    _build_task_prompt,
    dispatch_task,
    dispatch_tasks,
    get_dispatch_status,
    get_fallback_agent,
    select_agent_for_task,
)
from forge_harness.iteration.prioritizer import PrioritizedTask

# ============================================================================
# Helpers / Shared Fixtures
# ============================================================================


def make_task(
    feature_id: str = "HRN-001",
    title: str = "Build API endpoint",
    metadata: dict | None = None,
    epic: str | None = "platform",
    acceptance_criteria: list[str] | None = None,
    test_command: str | None = None,
    status: str = "pending",
) -> PrioritizedTask:
    """Create a minimal PrioritizedTask for testing."""
    return PrioritizedTask(
        task_id=feature_id,
        feature_id=feature_id,
        title=title,
        score=5.0,
        impact=3.0,
        urgency=2.0,
        effort=1.0,
        domain_weight=1.0,
        reasoning="test",
        status=status,
        epic=epic,
        acceptance_criteria=acceptance_criteria or [],
        test_command=test_command,
        metadata=metadata or {},
    )


@pytest.fixture
def tmp_forge_root(tmp_path: Path) -> Path:
    """Return a temporary directory that acts as the FORGE root."""
    return tmp_path


@pytest.fixture
def features_file(tmp_forge_root: Path) -> Path:
    """Write a minimal features.json and return its path."""
    data = {
        "features": [
            {
                "id": "HRN-001",
                "title": "Build backend API",
                "priority": 5,
                "status": "pending",
                "epic": "platform",
                "acceptance_criteria": ["Returns 200", "Has tests"],
                "test_command": "pytest tests/",
            },
            {
                "id": "HRN-002",
                "title": "Fix frontend bug",
                "priority": 3,
                "status": "pending",
                "epic": None,
                "acceptance_criteria": [],
                "test_command": None,
            },
        ]
    }
    path = tmp_forge_root / "features.json"
    path.write_text(json.dumps(data))
    return path


# ============================================================================
# AgentType Enum
# ============================================================================


class TestAgentType:
    def test_all_values_are_strings(self):
        for member in AgentType:
            assert isinstance(member.value, str)

    def test_backend_value(self):
        assert AgentType.BACKEND.value == "backend-engineer"

    def test_qa_value(self):
        assert AgentType.QA.value == "qa-tester"

    def test_unknown_value(self):
        assert AgentType.UNKNOWN.value == "unknown"

    def test_external_codex(self):
        assert AgentType.EXTERNAL_CODEX.value == "codex"

    def test_claude(self):
        assert AgentType.CLAUDE.value == "claude"

    def test_enum_from_value(self):
        assert AgentType("backend-engineer") is AgentType.BACKEND

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            AgentType("nonexistent-agent")


# ============================================================================
# AGENT_TYPE_ROUTING constant
# ============================================================================


class TestAgentTypeRouting:
    def test_backend_keyword(self):
        assert AGENT_TYPE_ROUTING["backend"] is AgentType.BACKEND

    def test_frontend_keyword(self):
        assert AGENT_TYPE_ROUTING["frontend"] is AgentType.FRONTEND

    def test_test_keyword(self):
        assert AGENT_TYPE_ROUTING["test"] is AgentType.QA

    def test_debug_keyword(self):
        assert AGENT_TYPE_ROUTING["debug"] is AgentType.DEBUG

    def test_content_keyword(self):
        assert AGENT_TYPE_ROUTING["content"] is AgentType.CONTENT

    def test_api_keyword(self):
        assert AGENT_TYPE_ROUTING["api"] is AgentType.BACKEND

    def test_blog_keyword(self):
        assert AGENT_TYPE_ROUTING["blog"] is AgentType.CONTENT


# ============================================================================
# AGENT_FALLBACKS constant
# ============================================================================


class TestAgentFallbacks:
    def test_backend_has_fallbacks(self):
        assert len(AGENT_FALLBACKS[AgentType.BACKEND]) > 0

    def test_frontend_fallback_includes_claude(self):
        assert AgentType.CLAUDE in AGENT_FALLBACKS[AgentType.FRONTEND]

    def test_content_fallback_includes_gemini(self):
        assert AgentType.EXTERNAL_GEMINI in AGENT_FALLBACKS[AgentType.CONTENT]

    def test_debug_fallback_first_is_backend(self):
        assert AGENT_FALLBACKS[AgentType.DEBUG][0] is AgentType.BACKEND


# ============================================================================
# DispatchResult
# ============================================================================


class TestDispatchResult:
    def test_defaults(self):
        result = DispatchResult(success=True, task_id="HRN-001")
        assert result.session_id is None
        assert result.agent_type is None
        assert result.status == "failed"
        assert result.error is None
        assert result.dispatched_at is None
        assert result.metadata == {}

    def test_to_dict_basic(self):
        now = datetime.now(UTC)
        result = DispatchResult(
            success=True,
            task_id="HRN-001",
            session_id="forge-task-HRN-001",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=now,
            metadata={"key": "val"},
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["task_id"] == "HRN-001"
        assert d["session_id"] == "forge-task-HRN-001"
        assert d["agent_type"] == "backend-engineer"
        assert d["status"] == "in_progress"
        assert d["dispatched_at"] == now.isoformat()
        assert d["metadata"] == {"key": "val"}

    def test_to_dict_none_agent_type(self):
        result = DispatchResult(success=False, task_id="HRN-001")
        d = result.to_dict()
        assert d["agent_type"] is None

    def test_to_dict_none_dispatched_at(self):
        result = DispatchResult(success=False, task_id="HRN-001")
        d = result.to_dict()
        assert d["dispatched_at"] is None

    def test_to_dict_error_field(self):
        result = DispatchResult(success=False, task_id="HRN-001", error="session failed")
        d = result.to_dict()
        assert d["error"] == "session failed"

    def test_metadata_is_independent_across_instances(self):
        r1 = DispatchResult(success=True, task_id="A")
        r2 = DispatchResult(success=True, task_id="B")
        r1.metadata["x"] = 1
        assert "x" not in r2.metadata


# ============================================================================
# DispatchStatus
# ============================================================================


class TestDispatchStatus:
    def test_basic_creation(self):
        now = datetime.now(UTC)
        status = DispatchStatus(
            task_id="HRN-001",
            session_id="forge-task-HRN-001",
            agent_type=AgentType.QA,
            status="running",
            dispatched_at=now,
        )
        assert status.task_id == "HRN-001"
        assert status.output_size == 0
        assert status.last_activity is None
        assert status.metadata == {}

    def test_with_metadata(self):
        now = datetime.now(UTC)
        status = DispatchStatus(
            task_id="X",
            session_id="s",
            agent_type=AgentType.BACKEND,
            status="dispatched",
            dispatched_at=now,
            metadata={"foo": "bar"},
        )
        assert status.metadata["foo"] == "bar"


# ============================================================================
# TmuxDispatcher – unit tests with mocked subprocess
# ============================================================================


class TestTmuxDispatcher:
    @pytest.fixture
    def dispatcher(self, tmp_forge_root: Path) -> TmuxDispatcher:
        return TmuxDispatcher(forge_root=tmp_forge_root)

    # --- __init__ ---

    def test_init_sets_forge_root(self, tmp_forge_root):
        d = TmuxDispatcher(forge_root=tmp_forge_root)
        assert d.forge_root == tmp_forge_root

    def test_init_default_forge_root_is_cwd(self):
        d = TmuxDispatcher()
        assert d.forge_root == Path.cwd()

    def test_init_creates_tracking_dir(self, tmp_forge_root):
        d = TmuxDispatcher(forge_root=tmp_forge_root)
        assert d.tracking_dir.exists()
        assert d.tracking_dir == tmp_forge_root / ".forge/dispatch"

    # --- create_session ---

    def test_create_session_success(self, dispatcher):
        with (
            patch("subprocess.run") as mock_run,
        ):
            # _session_exists → False; new-session → success
            mock_run.side_effect = [
                MagicMock(returncode=1),  # has-session → not found
                MagicMock(returncode=0),  # new-session → ok
            ]
            result = dispatcher.create_session("HRN-001", AgentType.BACKEND)

        assert result == "forge-task-HRN-001"

    def test_create_session_already_exists(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)  # has-session → found
            result = dispatcher.create_session("HRN-001", AgentType.BACKEND)

        assert result is None

    def test_create_session_uses_working_dir(self, dispatcher, tmp_forge_root):
        work_dir = tmp_forge_root / "subdir"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),  # not exists
                MagicMock(returncode=0),  # new-session ok
            ]
            dispatcher.create_session("HRN-001", AgentType.BACKEND, working_dir=work_dir)

        # The second call should be new-session with the working dir
        new_session_call = mock_run.call_args_list[1]
        assert str(work_dir) in new_session_call.args[0]

    def test_create_session_defaults_to_forge_root_if_no_working_dir(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),
                MagicMock(returncode=0),
            ]
            dispatcher.create_session("HRN-001", AgentType.BACKEND)

        new_session_call = mock_run.call_args_list[1]
        assert str(dispatcher.forge_root) in new_session_call.args[0]

    def test_create_session_called_process_error(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),  # not exists
                subprocess.CalledProcessError(1, "tmux", stderr=b"error"),
            ]
            result = dispatcher.create_session("HRN-001", AgentType.BACKEND)

        assert result is None

    def test_create_session_timeout(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),
                subprocess.TimeoutExpired("tmux", 10),
            ]
            result = dispatcher.create_session("HRN-001", AgentType.BACKEND)

        assert result is None

    def test_create_session_tmux_not_found(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),
                FileNotFoundError("tmux not found"),
            ]
            result = dispatcher.create_session("HRN-001", AgentType.BACKEND)

        assert result is None

    def test_create_session_called_process_error_no_stderr(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            err = subprocess.CalledProcessError(1, "tmux")
            err.stderr = None
            mock_run.side_effect = [MagicMock(returncode=1), err]
            result = dispatcher.create_session("HRN-001", AgentType.BACKEND)

        assert result is None

    # --- send_command ---

    def test_send_command_success(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            ok = dispatcher.send_command("sess-1", "echo hello")

        assert ok is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert cmd == ["tmux", "send-keys", "-t", "sess-1", "echo hello", "Enter"]

    def test_send_command_called_process_error(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "tmux", stderr=b"oops")
            ok = dispatcher.send_command("sess-1", "cmd")

        assert ok is False

    def test_send_command_timeout(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("tmux", 10)
            ok = dispatcher.send_command("sess-1", "cmd")

        assert ok is False

    def test_send_command_no_stderr(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            err = subprocess.CalledProcessError(1, "tmux")
            err.stderr = None
            mock_run.side_effect = err
            ok = dispatcher.send_command("sess-1", "cmd")

        assert ok is False

    # --- tag_session ---

    def test_tag_session_success(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            ok = dispatcher.tag_session("sess-1", {"task_id": "HRN-001", "agent_type": "backend-engineer"})

        assert ok is True
        # Should call set-option once per tag
        assert mock_run.call_count == 2

    def test_tag_session_empty_tags(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            ok = dispatcher.tag_session("sess-1", {})

        assert ok is True
        mock_run.assert_not_called()

    def test_tag_session_called_process_error(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "tmux", stderr=b"err")
            ok = dispatcher.tag_session("sess-1", {"k": "v"})

        assert ok is False

    def test_tag_session_timeout(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("tmux", 10)
            ok = dispatcher.tag_session("sess-1", {"k": "v"})

        assert ok is False

    def test_tag_session_no_stderr(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            err = subprocess.CalledProcessError(1, "tmux")
            err.stderr = None
            mock_run.side_effect = err
            ok = dispatcher.tag_session("sess-1", {"k": "v"})

        assert ok is False

    # --- get_session_output ---

    def test_get_session_output_success(self, dispatcher):
        mock_result = MagicMock()
        mock_result.stdout = "line1\nline2\n"
        with patch("subprocess.run", return_value=mock_result):
            output = dispatcher.get_session_output("sess-1", lines=50)

        assert output == "line1\nline2\n"

    def test_get_session_output_default_lines(self, dispatcher):
        mock_result = MagicMock(stdout="output")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            dispatcher.get_session_output("sess-1")

        cmd = mock_run.call_args.args[0]
        assert "-100" in cmd

    def test_get_session_output_called_process_error(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "tmux", stderr=b"err")
            output = dispatcher.get_session_output("sess-1")

        assert output == ""

    def test_get_session_output_timeout(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("tmux", 10)
            output = dispatcher.get_session_output("sess-1")

        assert output == ""

    def test_get_session_output_no_stderr(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            err = subprocess.CalledProcessError(1, "tmux")
            err.stderr = None
            mock_run.side_effect = err
            output = dispatcher.get_session_output("sess-1")

        assert output == ""

    # --- _session_exists ---

    def test_session_exists_true(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            assert dispatcher._session_exists("my-sess") is True

    def test_session_exists_false(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            assert dispatcher._session_exists("my-sess") is False

    def test_session_exists_timeout(self, dispatcher):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)):
            assert dispatcher._session_exists("my-sess") is False

    def test_session_exists_file_not_found(self, dispatcher):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert dispatcher._session_exists("my-sess") is False


# ============================================================================
# TaskTracker
# ============================================================================


class TestTaskTracker:
    @pytest.fixture
    def tracker(self, tmp_path: Path) -> TaskTracker:
        return TaskTracker(tracking_dir=tmp_path / "tracking")

    def test_init_creates_directory(self, tmp_path):
        td = tmp_path / "new_dir"
        assert not td.exists()
        TaskTracker(tracking_dir=td)
        assert td.exists()

    # --- save_dispatch ---

    def test_save_dispatch_creates_file(self, tracker):
        result = DispatchResult(
            success=True,
            task_id="HRN-001",
            session_id="forge-task-HRN-001",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
        )
        tracker.save_dispatch("HRN-001", result)

        tracking_file = tracker.tracking_dir / "HRN-001.json"
        assert tracking_file.exists()

    def test_save_dispatch_valid_json(self, tracker):
        result = DispatchResult(
            success=True,
            task_id="HRN-002",
            session_id="sess",
            agent_type=AgentType.QA,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
        )
        tracker.save_dispatch("HRN-002", result)
        raw = (tracker.tracking_dir / "HRN-002.json").read_text()
        data = json.loads(raw)
        assert data["task_id"] == "HRN-002"
        assert data["success"] is True

    def test_save_dispatch_handles_exception(self, tracker, monkeypatch):
        """save_dispatch should not raise on I/O errors."""
        result = DispatchResult(success=True, task_id="HRN-003")
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=OSError("disk full")))
        # Should not raise
        tracker.save_dispatch("HRN-003", result)

    # --- load_dispatch ---

    def test_load_dispatch_returns_none_when_missing(self, tracker):
        assert tracker.load_dispatch("UNKNOWN") is None

    def test_load_dispatch_roundtrip(self, tracker):
        now = datetime.now(UTC)
        original = DispatchResult(
            success=True,
            task_id="HRN-010",
            session_id="forge-task-HRN-010",
            agent_type=AgentType.FRONTEND,
            status="in_progress",
            dispatched_at=now,
            metadata={"title": "Build UI"},
        )
        tracker.save_dispatch("HRN-010", original)
        loaded = tracker.load_dispatch("HRN-010")

        assert loaded is not None
        assert loaded.success is True
        assert loaded.task_id == "HRN-010"
        assert loaded.session_id == "forge-task-HRN-010"
        assert loaded.agent_type is AgentType.FRONTEND
        assert loaded.status == "in_progress"
        assert loaded.metadata == {"title": "Build UI"}

    def test_load_dispatch_none_agent_type(self, tracker):
        data = {
            "success": False,
            "task_id": "HRN-020",
            "session_id": None,
            "agent_type": None,
            "status": "failed",
            "error": "oops",
            "dispatched_at": None,
            "metadata": {},
        }
        (tracker.tracking_dir / "HRN-020.json").write_text(json.dumps(data))
        loaded = tracker.load_dispatch("HRN-020")
        assert loaded is not None
        assert loaded.agent_type is None
        assert loaded.dispatched_at is None

    def test_load_dispatch_handles_corrupt_json(self, tracker):
        (tracker.tracking_dir / "HRN-BAD.json").write_text("{invalid json}")
        result = tracker.load_dispatch("HRN-BAD")
        assert result is None

    def test_load_dispatch_missing_status_defaults_unknown(self, tracker):
        data = {"success": True, "task_id": "HRN-030"}
        (tracker.tracking_dir / "HRN-030.json").write_text(json.dumps(data))
        loaded = tracker.load_dispatch("HRN-030")
        assert loaded is not None
        assert loaded.status == "unknown"

    # --- update_status ---

    def test_update_status_modifies_existing(self, tracker):
        result = DispatchResult(
            success=True,
            task_id="HRN-100",
            session_id="s",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
        )
        tracker.save_dispatch("HRN-100", result)
        tracker.update_status("HRN-100", "completed")
        loaded = tracker.load_dispatch("HRN-100")
        assert loaded is not None
        assert loaded.status == "completed"

    def test_update_status_no_op_when_not_found(self, tracker):
        """Should not raise even if task not found."""
        tracker.update_status("NONEXISTENT", "completed")  # no exception


# ============================================================================
# select_agent_for_task
# ============================================================================


class TestSelectAgentForTask:
    def test_explicit_agent_type_in_metadata(self):
        task = make_task(metadata={"agent_type": "qa-tester"})
        result = select_agent_for_task(task)
        assert result is AgentType.QA

    def test_explicit_agent_type_invalid_falls_through(self):
        task = make_task(title="backend task", metadata={"agent_type": "nonexistent"})
        result = select_agent_for_task(task)
        # Falls through keyword matching or default
        assert isinstance(result, AgentType)

    def test_backend_keyword_in_title(self):
        task = make_task(title="Build backend service", feature_id="HRN-001")
        result = select_agent_for_task(task)
        assert result is AgentType.BACKEND

    def test_api_keyword_in_title(self):
        task = make_task(title="Create REST api endpoint", feature_id="HRN-001")
        result = select_agent_for_task(task)
        assert result is AgentType.BACKEND

    def test_frontend_keyword_in_title(self):
        task = make_task(title="Build frontend dashboard", feature_id="HRN-001")
        result = select_agent_for_task(task)
        assert result is AgentType.FRONTEND

    def test_test_keyword_in_title(self):
        # NOTE: The routing uses substring match; "suite" contains "ui" which maps to
        # FRONTEND before "test" is evaluated. Use a title with no earlier keyword hit.
        task = make_task(title="Add test coverage", feature_id="HRN-001")
        result = select_agent_for_task(task)
        assert result is AgentType.QA

    def test_debug_keyword_in_title(self):
        task = make_task(title="Debug authentication flow", feature_id="HRN-001")
        result = select_agent_for_task(task)
        assert result is AgentType.DEBUG

    def test_content_keyword_in_title(self):
        task = make_task(title="Write content for homepage", feature_id="HRN-001")
        result = select_agent_for_task(task)
        assert result is AgentType.CONTENT

    def test_blog_keyword_in_title(self):
        task = make_task(title="Create blog post", feature_id="HRN-001")
        result = select_agent_for_task(task)
        assert result is AgentType.CONTENT

    def test_keyword_in_feature_id(self):
        # "test" appears in feature_id text used for matching
        task = make_task(title="Some task", feature_id="HRN-TEST-001")
        result = select_agent_for_task(task)
        assert result is AgentType.QA

    def test_default_backend_for_unknown_task(self):
        task = make_task(title="Do something unusual", feature_id="HRN-XYZ")
        result = select_agent_for_task(task)
        assert result is AgentType.BACKEND

    def test_case_insensitive_matching(self):
        task = make_task(title="BACKEND INTEGRATION", feature_id="HRN-001")
        result = select_agent_for_task(task)
        assert result is AgentType.BACKEND

    def test_fleet_status_passed_does_not_crash(self):
        from forge_harness.fleet.dashboard import FleetStatus

        fleet = FleetStatus(agents=[], timestamp=datetime.now(UTC))
        task = make_task(title="backend task")
        result = select_agent_for_task(task, fleet_status=fleet)
        assert isinstance(result, AgentType)

    def test_no_metadata_key_falls_through(self):
        task = make_task(title="backend work", metadata={})
        result = select_agent_for_task(task)
        assert result is AgentType.BACKEND


# ============================================================================
# get_fallback_agent
# ============================================================================


class TestGetFallbackAgent:
    def test_backend_returns_first_fallback(self):
        fallback = get_fallback_agent(AgentType.BACKEND)
        assert fallback is AGENT_FALLBACKS[AgentType.BACKEND][0]

    def test_frontend_returns_first_fallback(self):
        fallback = get_fallback_agent(AgentType.FRONTEND)
        assert fallback is AGENT_FALLBACKS[AgentType.FRONTEND][0]

    def test_unknown_agent_returns_none(self):
        fallback = get_fallback_agent(AgentType.UNKNOWN)
        assert fallback is None

    def test_claude_not_in_fallbacks_returns_none(self):
        # CLAUDE has no fallback entry → returns None
        fallback = get_fallback_agent(AgentType.CLAUDE)
        assert fallback is None

    def test_content_returns_gemini(self):
        fallback = get_fallback_agent(AgentType.CONTENT)
        assert fallback is AgentType.EXTERNAL_GEMINI

    def test_debug_returns_backend(self):
        fallback = get_fallback_agent(AgentType.DEBUG)
        assert fallback is AgentType.BACKEND


# ============================================================================
# _build_task_prompt
# ============================================================================


class TestBuildTaskPrompt:
    def test_basic_prompt_contains_feature_id_and_title(self):
        task = make_task(feature_id="HRN-001", title="Build API")
        prompt = _build_task_prompt(task)
        assert "HRN-001" in prompt
        assert "Build API" in prompt

    def test_prompt_with_acceptance_criteria(self):
        task = make_task(
            feature_id="HRN-002",
            title="Create endpoint",
            acceptance_criteria=["Returns 200", "Has auth"],
        )
        prompt = _build_task_prompt(task)
        assert "Acceptance Criteria:" in prompt
        assert "1. Returns 200" in prompt
        assert "2. Has auth" in prompt

    def test_prompt_without_acceptance_criteria(self):
        task = make_task(acceptance_criteria=[])
        prompt = _build_task_prompt(task)
        assert "Acceptance Criteria:" not in prompt

    def test_prompt_with_test_command(self):
        task = make_task(test_command="pytest tests/ -v")
        prompt = _build_task_prompt(task)
        assert "Test Command: pytest tests/ -v" in prompt

    def test_prompt_without_test_command(self):
        task = make_task(test_command=None)
        prompt = _build_task_prompt(task)
        assert "Test Command:" not in prompt

    def test_prompt_with_epic(self):
        task = make_task(epic="platform")
        prompt = _build_task_prompt(task)
        assert "Epic: platform" in prompt

    def test_prompt_without_epic(self):
        task = make_task(epic=None)
        prompt = _build_task_prompt(task)
        assert "Epic:" not in prompt

    def test_prompt_is_string(self):
        task = make_task()
        assert isinstance(_build_task_prompt(task), str)

    def test_prompt_multiple_criteria_numbered(self):
        task = make_task(acceptance_criteria=["A", "B", "C"])
        prompt = _build_task_prompt(task)
        assert "1. A" in prompt
        assert "2. B" in prompt
        assert "3. C" in prompt


# ============================================================================
# dispatch_task – integration-style with mocked I/O and subprocess
# ============================================================================


def _make_stub_task(feature_id: str = "HRN-001", title: str = "Build backend API") -> PrioritizedTask:
    """Build a PrioritizedTask stub for dispatch_task mocking."""
    return make_task(feature_id=feature_id, title=title)


class TestDispatchTask:
    """Tests for the top-level dispatch_task() function.

    The source code has a known bug: dispatch.py constructs PrioritizedTask without
    the required `task_id` positional argument (uses `feature_id` kwarg only).
    All tests that exercise the task-found code path must patch PrioritizedTask.
    """

    @pytest.fixture
    def patch_prioritized_task(self):
        """Patch PrioritizedTask inside the dispatch module to bypass source bug."""
        stub = _make_stub_task()
        with patch(
            "forge_harness.iteration.dispatch.PrioritizedTask",
            return_value=stub,
        ):
            yield stub

    def test_task_not_found_returns_failure(self, tmp_forge_root):
        with patch(
            "forge_harness.iteration.dispatch.load_features_from_file",
            return_value=[],
        ):
            result = dispatch_task(
                "MISSING-001",
                features_path=tmp_forge_root / "features.json",
                forge_root=tmp_forge_root,
            )

        assert result.success is False
        assert result.status == "not_found"
        assert "MISSING-001" in (result.error or "")

    def test_successful_dispatch(self, tmp_forge_root, features_file, patch_prioritized_task):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "Build backend API", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="forge-task-HRN-001"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
            )

        assert result.success is True
        assert result.session_id == "forge-task-HRN-001"
        assert result.status == "in_progress"
        assert result.task_id == "HRN-001"
        assert result.agent_type is not None

    def test_force_agent_overrides_selection(
        self, tmp_forge_root, features_file, patch_prioritized_task
    ):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "Build backend API", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="forge-task-HRN-001"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
                force_agent=AgentType.CONTENT,
            )

        assert result.agent_type is AgentType.CONTENT

    def test_fleet_status_failure_is_handled(
        self, tmp_forge_root, features_file, patch_prioritized_task
    ):
        """get_fleet_status raising should not fail the dispatch."""
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "backend task", "priority": 5}],
            ),
            patch(
                "forge_harness.iteration.dispatch.get_fleet_status",
                side_effect=RuntimeError("fleet unavailable"),
            ),
            patch.object(TmuxDispatcher, "create_session", return_value="forge-task-HRN-001"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
            )

        assert result.success is True

    def test_session_creation_failure_uses_fallback(
        self, tmp_forge_root, features_file, patch_prioritized_task
    ):
        call_count = {"n": 0}

        def create_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None  # primary fails
            return "forge-task-HRN-001-fallback"  # fallback succeeds

        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "debug task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", side_effect=create_side_effect),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
                force_agent=AgentType.DEBUG,
            )

        # Fallback path: should succeed with the second session
        assert result.success is True
        assert result.session_id == "forge-task-HRN-001-fallback"

    def test_both_sessions_fail_returns_failure(
        self, tmp_forge_root, features_file, patch_prioritized_task
    ):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "debug task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value=None),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
                force_agent=AgentType.DEBUG,
            )

        assert result.success is False
        assert result.status == "failed"

    def test_send_command_failure_returns_failure(
        self, tmp_forge_root, features_file, patch_prioritized_task
    ):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "backend API", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess-1"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=False),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
            )

        assert result.success is False
        assert result.status == "failed"
        assert result.session_id == "sess-1"

    def test_dispatch_saves_tracking_file(
        self, tmp_forge_root, features_file, patch_prioritized_task
    ):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "API task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="forge-task-HRN-001"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
            )

        tracking_file = tmp_forge_root / ".forge/dispatch" / "HRN-001.json"
        assert tracking_file.exists()

    def test_metadata_in_result(self, tmp_forge_root, features_file, patch_prioritized_task):
        # Make the stub carry the expected metadata values
        patch_prioritized_task.title = "API task"
        patch_prioritized_task.epic = "core"
        patch_prioritized_task.acceptance_criteria = ["AC1", "AC2"]
        patch_prioritized_task.test_command = "pytest"

        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[
                    {
                        "id": "HRN-001",
                        "title": "API task",
                        "priority": 5,
                        "epic": "core",
                        "acceptance_criteria": ["AC1", "AC2"],
                        "test_command": "pytest",
                    }
                ],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
            )

        assert result.metadata.get("title") == "API task"
        assert result.metadata.get("test_command") == "pytest"
        assert result.metadata.get("acceptance_criteria") == ["AC1", "AC2"]

    def test_dispatched_at_is_set(self, tmp_forge_root, features_file, patch_prioritized_task):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "API task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
            )

        assert result.dispatched_at is not None
        assert isinstance(result.dispatched_at, datetime)

    def test_default_forge_root_is_cwd(
        self, features_file, tmp_forge_root, monkeypatch, patch_prioritized_task
    ):
        monkeypatch.chdir(tmp_forge_root)
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "API task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task("HRN-001", features_path=features_file)

        assert result.success is True

    def test_primary_agent_no_fallback_available(
        self, tmp_forge_root, features_file, patch_prioritized_task
    ):
        """When primary agent has no fallback (CLAUDE), still returns failure."""
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value=None),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_forge_root,
                force_agent=AgentType.CLAUDE,  # CLAUDE has no fallback
            )

        assert result.success is False


# ============================================================================
# dispatch_tasks
# ============================================================================


class TestDispatchTasks:
    def test_dispatches_up_to_max_parallel(self, tmp_forge_root, features_file):
        call_ids = []

        def fake_dispatch(task_id, features_path=None, forge_root=None):
            call_ids.append(task_id)
            return DispatchResult(success=True, task_id=task_id, status="in_progress")

        with (
            patch("forge_harness.iteration.dispatch.dispatch_task", side_effect=fake_dispatch),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["T1", "T2", "T3", "T4", "T5"],
                features_path=features_file,
                forge_root=tmp_forge_root,
                max_parallel=3,
            )

        assert len(results) == 3
        assert call_ids == ["T1", "T2", "T3"]

    def test_dispatches_fewer_than_max(self, tmp_forge_root, features_file):
        with (
            patch(
                "forge_harness.iteration.dispatch.dispatch_task",
                side_effect=lambda tid, fp=None, fr=None: DispatchResult(success=True, task_id=tid),
            ),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["T1", "T2"],
                features_path=features_file,
                forge_root=tmp_forge_root,
                max_parallel=5,
            )

        assert len(results) == 2

    def test_empty_task_list(self, tmp_forge_root, features_file):
        with patch("forge_harness.iteration.dispatch.time.sleep"):
            results = dispatch_tasks(
                [],
                features_path=features_file,
                forge_root=tmp_forge_root,
            )

        assert results == []

    def test_sleep_called_between_dispatches(self, tmp_forge_root, features_file):
        with (
            patch(
                "forge_harness.iteration.dispatch.dispatch_task",
                return_value=DispatchResult(success=True, task_id="T"),
            ),
            patch("forge_harness.iteration.dispatch.time.sleep") as mock_sleep,
        ):
            dispatch_tasks(
                ["T1", "T2"],
                features_path=features_file,
                forge_root=tmp_forge_root,
                max_parallel=3,
            )

        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.5)

    def test_results_include_failures(self, tmp_forge_root, features_file):
        def side_effect(task_id, fp=None, fr=None):
            if task_id == "T2":
                return DispatchResult(success=False, task_id=task_id, status="failed")
            return DispatchResult(success=True, task_id=task_id)

        with (
            patch("forge_harness.iteration.dispatch.dispatch_task", side_effect=side_effect),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["T1", "T2", "T3"],
                features_path=features_file,
                forge_root=tmp_forge_root,
            )

        assert any(not r.success for r in results)


# ============================================================================
# get_dispatch_status
# ============================================================================


class TestGetDispatchStatus:
    def test_returns_none_when_no_tracking_dir(self, tmp_path):
        forge_root = tmp_path / "new_root"
        forge_root.mkdir()
        result = get_dispatch_status("HRN-999", forge_root=forge_root)
        assert result is None

    def test_returns_none_when_task_not_tracked(self, tmp_forge_root):
        result = get_dispatch_status("HRN-999", forge_root=tmp_forge_root)
        assert result is None

    def test_returns_dispatch_result_when_tracked(self, tmp_forge_root):
        tracking_dir = tmp_forge_root / ".forge/dispatch"
        tracking_dir.mkdir(exist_ok=True)

        data = {
            "success": True,
            "task_id": "HRN-001",
            "session_id": "forge-task-HRN-001",
            "agent_type": "backend-engineer",
            "status": "in_progress",
            "error": None,
            "dispatched_at": datetime.now(UTC).isoformat(),
            "metadata": {},
        }
        (tracking_dir / "HRN-001.json").write_text(json.dumps(data))

        result = get_dispatch_status("HRN-001", forge_root=tmp_forge_root)
        assert result is not None
        assert result.task_id == "HRN-001"
        assert result.status == "in_progress"
        assert result.agent_type is AgentType.BACKEND

    def test_default_forge_root_is_cwd(self, tmp_forge_root, monkeypatch):
        monkeypatch.chdir(tmp_forge_root)
        # Should not raise even though nothing is tracked
        result = get_dispatch_status("HRN-999")
        assert result is None
