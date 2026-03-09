"""Comprehensive tests for forge_harness.iteration.dispatch and aggregator.

Covers both modules with fresh test scenarios including:
- Constants and module-level structures
- All dataclass fields, defaults, and properties
- Tmux session management with subprocess mocking
- Task tracking (save/load/update roundtrips)
- Agent selection and fallback logic
- Prompt building
- Dispatch orchestration (single and batch)
- Result aggregation, deduplication, conflict detection
- Full dispatch → aggregate pipeline integration
- Boundary conditions and error paths
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from forge_harness.iteration.aggregator import (
    AgentResult,
    AggregatedReport,
    Conflict,
    ConflictSeverity,
    DuplicateFinding,
    ResultAggregator,
    ResultStatus,
)
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

# =============================================================================
# Shared helpers
# =============================================================================


def make_prioritized_task(
    task_id: str = "HRN-001",
    title: str = "Build backend API",
    metadata: dict | None = None,
    epic: str | None = "platform",
    acceptance_criteria: list[str] | None = None,
    test_command: str | None = None,
    status: str = "pending",
) -> PrioritizedTask:
    """Construct a PrioritizedTask with sensible defaults for testing."""
    return PrioritizedTask(
        task_id=task_id,
        feature_id=task_id,
        title=title,
        score=7.5,
        impact=4.0,
        urgency=3.0,
        effort=2.0,
        domain_weight=1.0,
        reasoning="test reasoning",
        status=status,
        epic=epic,
        acceptance_criteria=acceptance_criteria or [],
        test_command=test_command,
        metadata=metadata or {},
    )


def make_agent_result(
    agent_id: str = "agent-1",
    task_id: str = "TASK-001",
    status: ResultStatus | str = ResultStatus.SUCCESS,
    findings: list[str] | None = None,
    errors: list[str] | None = None,
    metadata: dict | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    output: str = "",
) -> AgentResult:
    """Convenience factory for AgentResult."""
    return AgentResult(
        agent_id=agent_id,
        task_id=task_id,
        status=status,
        findings=findings or [],
        errors=errors or [],
        metadata=metadata or {},
        started_at=started_at or datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        completed_at=completed_at,
        output=output,
    )


def make_features_json(tmp_path: Path, features: list[dict]) -> Path:
    """Write features data to a temporary features.json."""
    path = tmp_path / "features.json"
    path.write_text(json.dumps({"features": features}))
    return path


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def forge_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def dispatcher(forge_root: Path) -> TmuxDispatcher:
    return TmuxDispatcher(forge_root=forge_root)


@pytest.fixture
def tracker(tmp_path: Path) -> TaskTracker:
    td = tmp_path / ".forge/dispatch"
    return TaskTracker(tracking_dir=td)


@pytest.fixture
def aggregator() -> ResultAggregator:
    return ResultAggregator()


@pytest.fixture
def features_file(forge_root: Path) -> Path:
    return make_features_json(
        forge_root,
        [
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
                "title": "Create frontend component",
                "priority": 3,
                "status": "pending",
                "epic": None,
                "acceptance_criteria": [],
                "test_command": None,
            },
            {
                "id": "HRN-DEBUG-001",
                "title": "Debug memory leak",
                "priority": 8,
                "status": "pending",
                "epic": "stability",
                "acceptance_criteria": [],
                "test_command": None,
            },
        ],
    )


# =============================================================================
# AgentType — enum contract
# =============================================================================


class TestAgentTypeEnum:
    """Validate the AgentType enum values and str-enum behaviour."""

    def test_str_enum_inherits_str(self):
        assert isinstance(AgentType.BACKEND, str)

    def test_backend_value(self):
        assert AgentType.BACKEND.value == "backend-engineer"

    def test_frontend_value(self):
        assert AgentType.FRONTEND.value == "frontend-builder"

    def test_debug_value(self):
        assert AgentType.DEBUG.value == "debug-detective"

    def test_qa_value(self):
        assert AgentType.QA.value == "qa-tester"

    def test_content_value(self):
        assert AgentType.CONTENT.value == "content-writer"

    def test_external_codex(self):
        assert AgentType.EXTERNAL_CODEX.value == "codex"

    def test_external_opencode(self):
        assert AgentType.EXTERNAL_OPENCODE.value == "opencode"

    def test_external_gemini(self):
        assert AgentType.EXTERNAL_GEMINI.value == "gemini"

    def test_external_cursor(self):
        assert AgentType.EXTERNAL_CURSOR.value == "cursor"

    def test_external_amp(self):
        assert AgentType.EXTERNAL_AMP.value == "amp"

    def test_claude_value(self):
        assert AgentType.CLAUDE.value == "claude"

    def test_unknown_value(self):
        assert AgentType.UNKNOWN.value == "unknown"

    def test_round_trip_all_members(self):
        for member in AgentType:
            assert AgentType(member.value) is member

    def test_invalid_value_raises_value_error(self):
        with pytest.raises(ValueError):
            AgentType("not-a-real-agent")

    def test_equality_with_string(self):
        # str-enum allows comparison with plain str
        assert AgentType.BACKEND == "backend-engineer"


# =============================================================================
# AGENT_TYPE_ROUTING constant
# =============================================================================


class TestAgentTypeRouting:
    """Validate all routing keywords map to the correct AgentType."""

    # Backend group
    def test_backend_maps_to_backend(self):
        assert AGENT_TYPE_ROUTING["backend"] is AgentType.BACKEND

    def test_api_maps_to_backend(self):
        assert AGENT_TYPE_ROUTING["api"] is AgentType.BACKEND

    def test_database_maps_to_backend(self):
        assert AGENT_TYPE_ROUTING["database"] is AgentType.BACKEND

    def test_server_maps_to_backend(self):
        assert AGENT_TYPE_ROUTING["server"] is AgentType.BACKEND

    # Frontend group
    def test_frontend_maps_to_frontend(self):
        assert AGENT_TYPE_ROUTING["frontend"] is AgentType.FRONTEND

    def test_ui_maps_to_frontend(self):
        assert AGENT_TYPE_ROUTING["ui"] is AgentType.FRONTEND

    def test_component_maps_to_frontend(self):
        assert AGENT_TYPE_ROUTING["component"] is AgentType.FRONTEND

    def test_react_maps_to_frontend(self):
        assert AGENT_TYPE_ROUTING["react"] is AgentType.FRONTEND

    # QA group
    def test_test_maps_to_qa(self):
        assert AGENT_TYPE_ROUTING["test"] is AgentType.QA

    def test_e2e_maps_to_qa(self):
        assert AGENT_TYPE_ROUTING["e2e"] is AgentType.QA

    def test_integration_maps_to_qa(self):
        assert AGENT_TYPE_ROUTING["integration"] is AgentType.QA

    # Debug group
    def test_debug_maps_to_debug(self):
        assert AGENT_TYPE_ROUTING["debug"] is AgentType.DEBUG

    def test_fix_maps_to_debug(self):
        assert AGENT_TYPE_ROUTING["fix"] is AgentType.DEBUG

    def test_error_maps_to_debug(self):
        assert AGENT_TYPE_ROUTING["error"] is AgentType.DEBUG

    # Content group
    def test_content_maps_to_content(self):
        assert AGENT_TYPE_ROUTING["content"] is AgentType.CONTENT

    def test_docs_maps_to_content(self):
        assert AGENT_TYPE_ROUTING["docs"] is AgentType.CONTENT

    def test_blog_maps_to_content(self):
        assert AGENT_TYPE_ROUTING["blog"] is AgentType.CONTENT

    def test_all_values_are_agent_type_instances(self):
        for keyword, agent in AGENT_TYPE_ROUTING.items():
            assert isinstance(agent, AgentType), f"{keyword} does not map to AgentType"

    def test_routing_contains_at_least_14_entries(self):
        assert len(AGENT_TYPE_ROUTING) >= 14


# =============================================================================
# AGENT_FALLBACKS constant
# =============================================================================


class TestAgentFallbacks:
    """Validate the fallback chains."""

    def test_backend_fallback_first_is_opencode(self):
        assert AGENT_FALLBACKS[AgentType.BACKEND][0] is AgentType.EXTERNAL_OPENCODE

    def test_backend_fallback_includes_claude(self):
        assert AgentType.CLAUDE in AGENT_FALLBACKS[AgentType.BACKEND]

    def test_frontend_fallback_includes_cursor(self):
        assert AgentType.EXTERNAL_CURSOR in AGENT_FALLBACKS[AgentType.FRONTEND]

    def test_debug_first_fallback_is_backend(self):
        assert AGENT_FALLBACKS[AgentType.DEBUG][0] is AgentType.BACKEND

    def test_qa_first_fallback_is_backend(self):
        assert AGENT_FALLBACKS[AgentType.QA][0] is AgentType.BACKEND

    def test_content_fallback_includes_gemini_and_claude(self):
        fb = AGENT_FALLBACKS[AgentType.CONTENT]
        assert AgentType.EXTERNAL_GEMINI in fb
        assert AgentType.CLAUDE in fb

    def test_external_codex_has_backend_fallback(self):
        assert AgentType.BACKEND in AGENT_FALLBACKS[AgentType.EXTERNAL_CODEX]

    def test_external_opencode_has_backend_fallback(self):
        assert AgentType.BACKEND in AGENT_FALLBACKS[AgentType.EXTERNAL_OPENCODE]

    def test_external_gemini_falls_back_to_content(self):
        assert AGENT_FALLBACKS[AgentType.EXTERNAL_GEMINI][0] is AgentType.CONTENT

    def test_external_cursor_falls_back_to_frontend(self):
        assert AGENT_FALLBACKS[AgentType.EXTERNAL_CURSOR][0] is AgentType.FRONTEND

    def test_external_amp_falls_back_to_debug(self):
        assert AGENT_FALLBACKS[AgentType.EXTERNAL_AMP][0] is AgentType.DEBUG

    def test_all_fallback_entries_are_valid_agent_types(self):
        for agent, fallbacks in AGENT_FALLBACKS.items():
            for fb in fallbacks:
                assert isinstance(fb, AgentType)

    def test_unknown_not_in_fallbacks(self):
        assert AgentType.UNKNOWN not in AGENT_FALLBACKS

    def test_claude_not_in_fallbacks(self):
        assert AgentType.CLAUDE not in AGENT_FALLBACKS


# =============================================================================
# DispatchResult dataclass
# =============================================================================


class TestDispatchResult:
    """Tests for the DispatchResult dataclass and to_dict()."""

    def test_minimal_construction(self):
        r = DispatchResult(success=True, task_id="T-001")
        assert r.success is True
        assert r.task_id == "T-001"
        assert r.session_id is None
        assert r.agent_type is None
        assert r.status == "failed"
        assert r.error is None
        assert r.dispatched_at is None
        assert r.metadata == {}

    def test_full_construction(self):
        now = datetime.now(UTC)
        r = DispatchResult(
            success=True,
            task_id="HRN-001",
            session_id="forge-task-HRN-001",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            error=None,
            dispatched_at=now,
            metadata={"title": "Test"},
        )
        assert r.session_id == "forge-task-HRN-001"
        assert r.agent_type is AgentType.BACKEND
        assert r.status == "in_progress"
        assert r.dispatched_at == now

    def test_to_dict_success_true(self):
        r = DispatchResult(success=True, task_id="T")
        assert r.to_dict()["success"] is True

    def test_to_dict_success_false(self):
        r = DispatchResult(success=False, task_id="T")
        assert r.to_dict()["success"] is False

    def test_to_dict_agent_type_serialized_as_value(self):
        r = DispatchResult(
            success=True, task_id="T", agent_type=AgentType.QA
        )
        assert r.to_dict()["agent_type"] == "qa-tester"

    def test_to_dict_none_agent_type(self):
        r = DispatchResult(success=False, task_id="T")
        assert r.to_dict()["agent_type"] is None

    def test_to_dict_dispatched_at_isoformat(self):
        now = datetime(2026, 2, 22, 12, 0, 0, tzinfo=UTC)
        r = DispatchResult(success=True, task_id="T", dispatched_at=now)
        assert r.to_dict()["dispatched_at"] == now.isoformat()

    def test_to_dict_dispatched_at_none(self):
        r = DispatchResult(success=True, task_id="T")
        assert r.to_dict()["dispatched_at"] is None

    def test_to_dict_error_preserved(self):
        r = DispatchResult(success=False, task_id="T", error="tmux not found")
        assert r.to_dict()["error"] == "tmux not found"

    def test_to_dict_metadata_preserved(self):
        r = DispatchResult(success=True, task_id="T", metadata={"foo": "bar"})
        assert r.to_dict()["metadata"] == {"foo": "bar"}

    def test_metadata_independent_across_instances(self):
        r1 = DispatchResult(success=True, task_id="A")
        r2 = DispatchResult(success=True, task_id="B")
        r1.metadata["key"] = "value"
        assert "key" not in r2.metadata

    def test_all_status_strings_preserved(self):
        for status in ("dispatched", "failed", "unavailable", "in_progress", "not_found"):
            r = DispatchResult(success=True, task_id="T", status=status)
            assert r.to_dict()["status"] == status

    def test_all_agent_types_serialize(self):
        for agent in AgentType:
            r = DispatchResult(success=True, task_id="T", agent_type=agent)
            assert r.to_dict()["agent_type"] == agent.value


# =============================================================================
# DispatchStatus dataclass
# =============================================================================


class TestDispatchStatus:
    """Tests for the DispatchStatus dataclass."""

    def _make(self, **kwargs) -> DispatchStatus:
        defaults = {
            "task_id": "HRN-001",
            "session_id": "forge-task-HRN-001",
            "agent_type": AgentType.BACKEND,
            "status": "running",
            "dispatched_at": datetime.now(UTC),
        }
        defaults.update(kwargs)
        return DispatchStatus(**defaults)

    def test_basic_fields(self):
        s = self._make()
        assert s.task_id == "HRN-001"
        assert s.session_id == "forge-task-HRN-001"
        assert s.agent_type is AgentType.BACKEND
        assert s.status == "running"

    def test_output_size_defaults_to_zero(self):
        s = self._make()
        assert s.output_size == 0

    def test_last_activity_defaults_to_none(self):
        s = self._make()
        assert s.last_activity is None

    def test_metadata_defaults_to_empty_dict(self):
        s = self._make()
        assert s.metadata == {}

    def test_output_size_can_be_set(self):
        s = self._make(output_size=8192)
        assert s.output_size == 8192

    def test_last_activity_can_be_set(self):
        t = datetime.now(UTC)
        s = self._make(last_activity=t)
        assert s.last_activity == t

    def test_metadata_can_be_set(self):
        s = self._make(metadata={"prompt_chars": 1500})
        assert s.metadata["prompt_chars"] == 1500

    def test_all_agent_types_accepted(self):
        for agent in AgentType:
            s = self._make(agent_type=agent)
            assert s.agent_type is agent


# =============================================================================
# TmuxDispatcher
# =============================================================================


class TestTmuxDispatcher:
    """Tests for TmuxDispatcher with subprocess mocked out."""

    def test_init_with_explicit_root(self, forge_root):
        d = TmuxDispatcher(forge_root=forge_root)
        assert d.forge_root == forge_root

    def test_init_without_root_uses_cwd(self):
        d = TmuxDispatcher()
        assert d.forge_root == Path.cwd()

    def test_init_creates_tracking_dir(self, forge_root):
        d = TmuxDispatcher(forge_root=forge_root)
        assert d.tracking_dir.exists()
        assert d.tracking_dir == forge_root / ".forge/dispatch"

    # --- _session_exists ---

    def test_session_exists_returns_true_on_zero_returncode(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            assert dispatcher._session_exists("my-session") is True

    def test_session_exists_returns_false_on_nonzero_returncode(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            assert dispatcher._session_exists("my-session") is False

    def test_session_exists_handles_timeout(self, dispatcher):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)):
            assert dispatcher._session_exists("any") is False

    def test_session_exists_handles_file_not_found(self, dispatcher):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert dispatcher._session_exists("any") is False

    def test_session_exists_uses_has_session_command(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            dispatcher._session_exists("target-sess")
        cmd = mock_run.call_args.args[0]
        assert "has-session" in cmd
        assert "target-sess" in cmd

    # --- create_session ---

    def test_create_session_returns_session_name_on_success(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),  # has-session → not found
                MagicMock(returncode=0),  # new-session → ok
            ]
            result = dispatcher.create_session("HRN-001", AgentType.BACKEND)
        assert result == "forge-task-HRN-001"

    def test_create_session_returns_none_when_session_exists(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = dispatcher.create_session("HRN-001", AgentType.BACKEND)
        assert result is None

    def test_create_session_uses_default_working_dir(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),
                MagicMock(returncode=0),
            ]
            dispatcher.create_session("HRN-001", AgentType.BACKEND)
        # Second call is new-session; should include forge_root path
        new_session_cmd = mock_run.call_args_list[1].args[0]
        assert str(dispatcher.forge_root) in new_session_cmd

    def test_create_session_uses_custom_working_dir(self, dispatcher, forge_root):
        custom = forge_root / "subdir"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),
                MagicMock(returncode=0),
            ]
            dispatcher.create_session("HRN-001", AgentType.BACKEND, working_dir=custom)
        new_session_cmd = mock_run.call_args_list[1].args[0]
        assert str(custom) in new_session_cmd

    def test_create_session_returns_none_on_called_process_error(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),
                subprocess.CalledProcessError(1, "tmux", stderr=b"err"),
            ]
            result = dispatcher.create_session("T", AgentType.BACKEND)
        assert result is None

    def test_create_session_returns_none_on_called_process_error_no_stderr(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            err = subprocess.CalledProcessError(1, "tmux")
            err.stderr = None
            mock_run.side_effect = [MagicMock(returncode=1), err]
            result = dispatcher.create_session("T", AgentType.BACKEND)
        assert result is None

    def test_create_session_returns_none_on_timeout(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),
                subprocess.TimeoutExpired("tmux", 10),
            ]
            result = dispatcher.create_session("T", AgentType.BACKEND)
        assert result is None

    def test_create_session_returns_none_when_tmux_not_found(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),
                FileNotFoundError("tmux"),
            ]
            result = dispatcher.create_session("T", AgentType.BACKEND)
        assert result is None

    # --- send_command ---

    def test_send_command_returns_true_on_success(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            assert dispatcher.send_command("sess", "ls -la") is True

    def test_send_command_passes_correct_args(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            dispatcher.send_command("my-session", "echo hello")
        cmd = mock_run.call_args.args[0]
        assert cmd == ["tmux", "send-keys", "-t", "my-session", "echo hello", "Enter"]

    def test_send_command_returns_false_on_called_process_error(self, dispatcher):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "tmux", stderr=b"e")):
            assert dispatcher.send_command("sess", "cmd") is False

    def test_send_command_returns_false_on_called_process_error_no_stderr(self, dispatcher):
        err = subprocess.CalledProcessError(1, "tmux")
        err.stderr = None
        with patch("subprocess.run", side_effect=err):
            assert dispatcher.send_command("sess", "cmd") is False

    def test_send_command_returns_false_on_timeout(self, dispatcher):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)):
            assert dispatcher.send_command("sess", "cmd") is False

    # --- tag_session ---

    def test_tag_session_returns_true_with_tags(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = dispatcher.tag_session("sess", {"k": "v"})
        assert result is True

    def test_tag_session_returns_true_with_empty_tags(self, dispatcher):
        with patch("subprocess.run") as mock_run:
            result = dispatcher.tag_session("sess", {})
        assert result is True
        mock_run.assert_not_called()

    def test_tag_session_calls_set_option_per_tag(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            dispatcher.tag_session("sess", {"a": "1", "b": "2", "c": "3"})
        assert mock_run.call_count == 3

    def test_tag_session_sets_at_prefix_for_key(self, dispatcher):
        captured = []
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = lambda cmd, **kw: captured.append(cmd) or MagicMock(returncode=0)
            dispatcher.tag_session("s", {"my_key": "my_value"})
        set_option_cmd = [c for c in captured if "set-option" in c][0]
        assert "@my_key" in set_option_cmd
        assert "my_value" in set_option_cmd

    def test_tag_session_returns_false_on_called_process_error(self, dispatcher):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "tmux", stderr=b"e")):
            assert dispatcher.tag_session("sess", {"k": "v"}) is False

    def test_tag_session_returns_false_on_timeout(self, dispatcher):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)):
            assert dispatcher.tag_session("sess", {"k": "v"}) is False

    def test_tag_session_returns_false_on_called_process_error_no_stderr(self, dispatcher):
        err = subprocess.CalledProcessError(1, "tmux")
        err.stderr = None
        with patch("subprocess.run", side_effect=err):
            assert dispatcher.tag_session("sess", {"k": "v"}) is False

    # --- get_session_output ---

    def test_get_session_output_returns_stdout(self, dispatcher):
        mock_result = MagicMock(stdout="line1\nline2\n")
        with patch("subprocess.run", return_value=mock_result):
            output = dispatcher.get_session_output("sess")
        assert output == "line1\nline2\n"

    def test_get_session_output_default_100_lines(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(stdout="")) as mock_run:
            dispatcher.get_session_output("sess")
        cmd = mock_run.call_args.args[0]
        assert "-100" in cmd

    def test_get_session_output_custom_lines(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(stdout="")) as mock_run:
            dispatcher.get_session_output("sess", lines=50)
        cmd = mock_run.call_args.args[0]
        assert "-50" in cmd

    def test_get_session_output_returns_empty_on_error(self, dispatcher):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "tmux", stderr=b"e")):
            assert dispatcher.get_session_output("sess") == ""

    def test_get_session_output_returns_empty_on_timeout(self, dispatcher):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)):
            assert dispatcher.get_session_output("sess") == ""

    def test_get_session_output_returns_empty_on_no_stderr(self, dispatcher):
        err = subprocess.CalledProcessError(1, "tmux")
        err.stderr = None
        with patch("subprocess.run", side_effect=err):
            assert dispatcher.get_session_output("sess") == ""


# =============================================================================
# TaskTracker
# =============================================================================


class TestTaskTracker:
    """Tests for TaskTracker persistence layer."""

    def test_init_creates_tracking_dir(self, tmp_path):
        td = tmp_path / "new_dir"
        TaskTracker(tracking_dir=td)
        assert td.exists()

    def test_save_creates_json_file(self, tracker):
        result = DispatchResult(
            success=True,
            task_id="HRN-001",
            session_id="sess",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
        )
        tracker.save_dispatch("HRN-001", result)
        assert (tracker.tracking_dir / "HRN-001.json").exists()

    def test_save_writes_valid_json(self, tracker):
        result = DispatchResult(
            success=True,
            task_id="HRN-002",
            session_id="s2",
            agent_type=AgentType.QA,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
        )
        tracker.save_dispatch("HRN-002", result)
        raw = (tracker.tracking_dir / "HRN-002.json").read_text()
        data = json.loads(raw)
        assert data["task_id"] == "HRN-002"
        assert data["success"] is True

    def test_save_does_not_raise_on_io_error(self, tracker, monkeypatch):
        result = DispatchResult(success=True, task_id="T")
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=OSError("disk full")))
        tracker.save_dispatch("T", result)  # Must not raise

    def test_load_returns_none_when_file_missing(self, tracker):
        assert tracker.load_dispatch("NONEXISTENT") is None

    def test_load_roundtrip_preserves_all_fields(self, tracker):
        now = datetime(2026, 2, 22, 12, 0, 0, tzinfo=UTC)
        original = DispatchResult(
            success=True,
            task_id="HRN-010",
            session_id="forge-task-HRN-010",
            agent_type=AgentType.FRONTEND,
            status="in_progress",
            dispatched_at=now,
            metadata={"title": "UI component"},
        )
        tracker.save_dispatch("HRN-010", original)
        loaded = tracker.load_dispatch("HRN-010")

        assert loaded is not None
        assert loaded.success is True
        assert loaded.task_id == "HRN-010"
        assert loaded.session_id == "forge-task-HRN-010"
        assert loaded.agent_type is AgentType.FRONTEND
        assert loaded.status == "in_progress"
        assert loaded.metadata == {"title": "UI component"}

    def test_load_handles_none_agent_type(self, tracker):
        data = {
            "success": False,
            "task_id": "T",
            "session_id": None,
            "agent_type": None,
            "status": "failed",
            "error": "tmux failed",
            "dispatched_at": None,
            "metadata": {},
        }
        (tracker.tracking_dir / "T.json").write_text(json.dumps(data))
        loaded = tracker.load_dispatch("T")
        assert loaded is not None
        assert loaded.agent_type is None
        assert loaded.dispatched_at is None

    def test_load_handles_corrupt_json(self, tracker):
        (tracker.tracking_dir / "BAD.json").write_text("{bad")
        assert tracker.load_dispatch("BAD") is None

    def test_load_handles_missing_status_defaults_to_unknown(self, tracker):
        data = {"success": True, "task_id": "T"}
        (tracker.tracking_dir / "T.json").write_text(json.dumps(data))
        loaded = tracker.load_dispatch("T")
        assert loaded is not None
        assert loaded.status == "unknown"

    def test_load_handles_missing_metadata_defaults_to_empty(self, tracker):
        data = {
            "success": True,
            "task_id": "T",
            "session_id": None,
            "agent_type": None,
            "status": "in_progress",
            "error": None,
            "dispatched_at": None,
            # metadata intentionally missing
        }
        (tracker.tracking_dir / "T.json").write_text(json.dumps(data))
        loaded = tracker.load_dispatch("T")
        assert loaded.metadata == {}

    def test_update_status_persists_new_status(self, tracker):
        result = DispatchResult(
            success=True,
            task_id="UPD",
            session_id="s",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
        )
        tracker.save_dispatch("UPD", result)
        tracker.update_status("UPD", "completed")
        loaded = tracker.load_dispatch("UPD")
        assert loaded is not None
        assert loaded.status == "completed"

    def test_update_status_is_noop_when_task_not_found(self, tracker):
        # Must not raise
        tracker.update_status("GHOST", "completed")

    def test_multiple_tasks_saved_independently(self, tracker):
        for i in range(4):
            r = DispatchResult(
                success=True,
                task_id=f"T-{i:02d}",
                session_id=f"sess-{i}",
                agent_type=AgentType.BACKEND,
                status="in_progress",
                dispatched_at=datetime.now(UTC),
            )
            tracker.save_dispatch(f"T-{i:02d}", r)

        for i in range(4):
            loaded = tracker.load_dispatch(f"T-{i:02d}")
            assert loaded is not None
            assert loaded.task_id == f"T-{i:02d}"
            assert loaded.session_id == f"sess-{i}"

    def test_roundtrip_all_agent_types(self, tracker):
        for agent in AgentType:
            task_id = f"T-{agent.value.replace('-', '_')}"
            r = DispatchResult(
                success=True,
                task_id=task_id,
                agent_type=agent,
                status="in_progress",
                dispatched_at=datetime.now(UTC),
            )
            tracker.save_dispatch(task_id, r)
            loaded = tracker.load_dispatch(task_id)
            assert loaded is not None
            assert loaded.agent_type is agent


# =============================================================================
# select_agent_for_task
# =============================================================================


class TestSelectAgentForTask:
    """Tests for agent selection logic."""

    def test_explicit_agent_type_in_metadata(self):
        task = make_prioritized_task(metadata={"agent_type": "qa-tester"})
        assert select_agent_for_task(task) is AgentType.QA

    def test_explicit_external_opencode_in_metadata(self):
        task = make_prioritized_task(metadata={"agent_type": "opencode"})
        assert select_agent_for_task(task) is AgentType.EXTERNAL_OPENCODE

    def test_invalid_explicit_agent_type_falls_through_to_keyword(self):
        task = make_prioritized_task(
            title="backend service",
            metadata={"agent_type": "no-such-agent"},
        )
        assert select_agent_for_task(task) is AgentType.BACKEND

    def test_backend_keyword_in_title(self):
        task = make_prioritized_task(title="Implement backend service")
        assert select_agent_for_task(task) is AgentType.BACKEND

    def test_api_keyword_in_title(self):
        task = make_prioritized_task(title="Create API endpoint")
        assert select_agent_for_task(task) is AgentType.BACKEND

    def test_database_keyword_in_title(self):
        task = make_prioritized_task(title="Optimize database queries")
        assert select_agent_for_task(task) is AgentType.BACKEND

    def test_server_keyword_in_title(self):
        task = make_prioritized_task(title="Configure server routes")
        assert select_agent_for_task(task) is AgentType.BACKEND

    def test_frontend_keyword_in_title(self):
        task = make_prioritized_task(title="Build frontend dashboard")
        assert select_agent_for_task(task) is AgentType.FRONTEND

    def test_react_keyword_in_title(self):
        task = make_prioritized_task(title="React state management refactor")
        assert select_agent_for_task(task) is AgentType.FRONTEND

    def test_component_keyword_in_title(self):
        task = make_prioritized_task(title="Build component library")
        assert select_agent_for_task(task) is AgentType.FRONTEND

    def test_test_keyword_in_title(self):
        task = make_prioritized_task(title="Add test coverage", task_id="HRN-T")
        assert select_agent_for_task(task) is AgentType.QA

    def test_e2e_keyword_in_title(self):
        task = make_prioritized_task(title="E2e smoke checks")
        assert select_agent_for_task(task) is AgentType.QA

    def test_debug_keyword_in_title(self):
        task = make_prioritized_task(title="Debug session timeout issue")
        assert select_agent_for_task(task) is AgentType.DEBUG

    def test_fix_keyword_in_title(self):
        task = make_prioritized_task(title="Fix memory leak in handler")
        assert select_agent_for_task(task) is AgentType.DEBUG

    def test_content_keyword_in_title(self):
        task = make_prioritized_task(title="Write content for landing page")
        assert select_agent_for_task(task) is AgentType.CONTENT

    def test_docs_keyword_in_title(self):
        # Avoid "api" substring (maps to BACKEND first in iteration order)
        task = make_prioritized_task(title="Update docs for the project")
        assert select_agent_for_task(task) is AgentType.CONTENT

    def test_blog_keyword_in_title(self):
        task = make_prioritized_task(title="Create blog post for product launch")
        assert select_agent_for_task(task) is AgentType.CONTENT

    def test_keyword_matched_in_feature_id(self):
        task = make_prioritized_task(title="Generic work", task_id="HRN-TEST-007")
        assert select_agent_for_task(task) is AgentType.QA

    def test_no_keyword_match_defaults_to_backend(self):
        task = make_prioritized_task(title="Do something unusual", task_id="HRN-XYZ")
        assert select_agent_for_task(task) is AgentType.BACKEND

    def test_case_insensitive_matching(self):
        task = make_prioritized_task(title="BACKEND SERVICE LAYER")
        assert select_agent_for_task(task) is AgentType.BACKEND

    def test_fleet_status_passed_without_crash(self):
        from forge_harness.fleet.dashboard import FleetStatus
        fleet = FleetStatus(agents=[], timestamp=datetime.now(UTC))
        task = make_prioritized_task(title="backend task")
        result = select_agent_for_task(task, fleet_status=fleet)
        assert isinstance(result, AgentType)

    def test_empty_metadata_falls_through_to_keyword(self):
        task = make_prioritized_task(title="debug issue", metadata={})
        assert select_agent_for_task(task) is AgentType.DEBUG


# =============================================================================
# get_fallback_agent
# =============================================================================


class TestGetFallbackAgent:
    """Tests for fallback agent retrieval."""

    def test_backend_returns_opencode(self):
        assert get_fallback_agent(AgentType.BACKEND) is AgentType.EXTERNAL_OPENCODE

    def test_frontend_returns_cursor(self):
        assert get_fallback_agent(AgentType.FRONTEND) is AgentType.EXTERNAL_CURSOR

    def test_debug_returns_backend(self):
        assert get_fallback_agent(AgentType.DEBUG) is AgentType.BACKEND

    def test_qa_returns_backend(self):
        assert get_fallback_agent(AgentType.QA) is AgentType.BACKEND

    def test_content_returns_gemini(self):
        assert get_fallback_agent(AgentType.CONTENT) is AgentType.EXTERNAL_GEMINI

    def test_external_codex_returns_backend(self):
        assert get_fallback_agent(AgentType.EXTERNAL_CODEX) is AgentType.BACKEND

    def test_external_opencode_returns_backend(self):
        assert get_fallback_agent(AgentType.EXTERNAL_OPENCODE) is AgentType.BACKEND

    def test_external_gemini_returns_content(self):
        assert get_fallback_agent(AgentType.EXTERNAL_GEMINI) is AgentType.CONTENT

    def test_external_cursor_returns_frontend(self):
        assert get_fallback_agent(AgentType.EXTERNAL_CURSOR) is AgentType.FRONTEND

    def test_external_amp_returns_debug(self):
        assert get_fallback_agent(AgentType.EXTERNAL_AMP) is AgentType.DEBUG

    def test_unknown_returns_none(self):
        assert get_fallback_agent(AgentType.UNKNOWN) is None

    def test_claude_returns_none(self):
        assert get_fallback_agent(AgentType.CLAUDE) is None


# =============================================================================
# _build_task_prompt
# =============================================================================


class TestBuildTaskPrompt:
    """Tests for prompt construction helper."""

    def test_starts_with_implement_keyword(self):
        task = make_prioritized_task(task_id="T-001", title="My Feature")
        assert _build_task_prompt(task).startswith("Implement T-001: My Feature")

    def test_contains_feature_id(self):
        task = make_prioritized_task(task_id="HRN-042")
        assert "HRN-042" in _build_task_prompt(task)

    def test_contains_title(self):
        task = make_prioritized_task(title="Build the rocket ship")
        assert "Build the rocket ship" in _build_task_prompt(task)

    def test_acceptance_criteria_section_present(self):
        task = make_prioritized_task(acceptance_criteria=["AC1", "AC2"])
        prompt = _build_task_prompt(task)
        assert "Acceptance Criteria:" in prompt
        assert "1. AC1" in prompt
        assert "2. AC2" in prompt

    def test_empty_acceptance_criteria_omitted(self):
        task = make_prioritized_task(acceptance_criteria=[])
        assert "Acceptance Criteria:" not in _build_task_prompt(task)

    def test_test_command_section_present(self):
        task = make_prioritized_task(test_command="pytest tests/ -v")
        assert "Test Command: pytest tests/ -v" in _build_task_prompt(task)

    def test_no_test_command_section_omitted(self):
        task = make_prioritized_task(test_command=None)
        assert "Test Command:" not in _build_task_prompt(task)

    def test_epic_section_present(self):
        task = make_prioritized_task(epic="auth-platform")
        assert "Epic: auth-platform" in _build_task_prompt(task)

    def test_no_epic_section_omitted(self):
        task = make_prioritized_task(epic=None)
        assert "Epic:" not in _build_task_prompt(task)

    def test_many_acceptance_criteria_numbered_correctly(self):
        criteria = [f"AC{i}" for i in range(1, 11)]
        task = make_prioritized_task(acceptance_criteria=criteria)
        prompt = _build_task_prompt(task)
        assert "1. AC1" in prompt
        assert "10. AC10" in prompt

    def test_returns_string(self):
        task = make_prioritized_task()
        assert isinstance(_build_task_prompt(task), str)

    def test_empty_title_does_not_raise(self):
        task = make_prioritized_task(task_id="T", title="")
        prompt = _build_task_prompt(task)
        assert "T" in prompt

    def test_all_sections_combined(self):
        task = make_prioritized_task(
            task_id="FULL",
            title="Complete Feature",
            acceptance_criteria=["AC1"],
            test_command="pytest -x",
            epic="big-epic",
        )
        prompt = _build_task_prompt(task)
        assert "FULL" in prompt
        assert "Complete Feature" in prompt
        assert "Acceptance Criteria:" in prompt
        assert "Test Command: pytest -x" in prompt
        assert "Epic: big-epic" in prompt


# =============================================================================
# dispatch_task — high-level function
# =============================================================================


class TestDispatchTask:
    """Tests for dispatch_task() orchestration function."""

    @pytest.fixture
    def task_stub(self):
        stub = make_prioritized_task(
            task_id="HRN-001",
            title="Build backend API",
            acceptance_criteria=["AC1"],
            test_command="pytest",
            epic="core",
        )
        with patch("forge_harness.iteration.dispatch.PrioritizedTask", return_value=stub):
            yield stub

    def test_task_not_found_returns_failure(self, forge_root):
        with patch(
            "forge_harness.iteration.dispatch.load_features_from_file",
            return_value=[],
        ):
            result = dispatch_task(
                "MISSING",
                features_path=forge_root / "features.json",
                forge_root=forge_root,
            )
        assert result.success is False
        assert result.status == "not_found"
        assert "MISSING" in (result.error or "")

    def test_successful_dispatch_returns_in_progress(
        self, forge_root, features_file, task_stub
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
            result = dispatch_task("HRN-001", features_path=features_file, forge_root=forge_root)

        assert result.success is True
        assert result.status == "in_progress"
        assert result.session_id == "forge-task-HRN-001"
        assert result.task_id == "HRN-001"

    def test_force_agent_overrides_selection(
        self, forge_root, features_file, task_stub
    ):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=forge_root,
                force_agent=AgentType.CONTENT,
            )
        assert result.agent_type is AgentType.CONTENT

    def test_fleet_status_error_does_not_fail_dispatch(
        self, forge_root, features_file, task_stub
    ):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "backend task", "priority": 5}],
            ),
            patch(
                "forge_harness.iteration.dispatch.get_fleet_status",
                side_effect=RuntimeError("fleet down"),
            ),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task("HRN-001", features_path=features_file, forge_root=forge_root)
        assert result.success is True

    def test_session_creation_fails_uses_fallback(
        self, forge_root, features_file, task_stub
    ):
        attempts = {"n": 0}

        def create_side(task_id, agent_type, working_dir=None):
            attempts["n"] += 1
            return None if attempts["n"] == 1 else "fallback-sess"

        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "debug task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", side_effect=create_side),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=forge_root,
                force_agent=AgentType.DEBUG,
            )
        assert result.success is True
        assert result.session_id == "fallback-sess"

    def test_both_sessions_fail_returns_failure(
        self, forge_root, features_file, task_stub
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
                forge_root=forge_root,
                force_agent=AgentType.DEBUG,
            )
        assert result.success is False
        assert result.status == "failed"

    def test_send_command_failure_returns_failure(
        self, forge_root, features_file, task_stub
    ):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "backend API", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=False),
        ):
            result = dispatch_task("HRN-001", features_path=features_file, forge_root=forge_root)
        assert result.success is False
        assert result.session_id == "sess"

    def test_dispatch_saves_tracking_file(self, forge_root, features_file, task_stub):
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
            dispatch_task("HRN-001", features_path=features_file, forge_root=forge_root)

        assert (forge_root / ".forge/dispatch" / "HRN-001.json").exists()

    def test_dispatched_at_is_set_on_success(self, forge_root, features_file, task_stub):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task("HRN-001", features_path=features_file, forge_root=forge_root)
        assert result.dispatched_at is not None
        assert isinstance(result.dispatched_at, datetime)

    def test_no_fallback_for_claude_returns_failure(
        self, forge_root, features_file, task_stub
    ):
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
                forge_root=forge_root,
                force_agent=AgentType.CLAUDE,  # no fallback
            )
        assert result.success is False


# =============================================================================
# dispatch_tasks — batch function
# =============================================================================


class TestDispatchTasks:
    """Tests for dispatch_tasks() batch dispatching."""

    def _fake_dispatch(self, task_id, features_path=None, forge_root=None):
        return DispatchResult(success=True, task_id=task_id, status="in_progress")

    def test_dispatches_up_to_max_parallel(self, forge_root, features_file):
        dispatched = []

        def fake(task_id, features_path=None, forge_root=None):
            dispatched.append(task_id)
            return DispatchResult(success=True, task_id=task_id)

        with (
            patch("forge_harness.iteration.dispatch.dispatch_task", side_effect=fake),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["A", "B", "C", "D", "E"],
                features_path=features_file,
                forge_root=forge_root,
                max_parallel=3,
            )
        assert len(results) == 3
        assert dispatched == ["A", "B", "C"]

    def test_dispatches_fewer_tasks_than_max(self, forge_root, features_file):
        with (
            patch("forge_harness.iteration.dispatch.dispatch_task", side_effect=self._fake_dispatch),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["A", "B"],
                features_path=features_file,
                forge_root=forge_root,
                max_parallel=10,
            )
        assert len(results) == 2

    def test_empty_list_returns_empty(self, forge_root, features_file):
        with patch("forge_harness.iteration.dispatch.time.sleep"):
            results = dispatch_tasks([], features_path=features_file, forge_root=forge_root)
        assert results == []

    def test_sleep_called_between_each_dispatch(self, forge_root, features_file):
        with (
            patch(
                "forge_harness.iteration.dispatch.dispatch_task",
                return_value=DispatchResult(success=True, task_id="T"),
            ),
            patch("forge_harness.iteration.dispatch.time.sleep") as mock_sleep,
        ):
            dispatch_tasks(
                ["T1", "T2", "T3"],
                features_path=features_file,
                forge_root=forge_root,
                max_parallel=5,
            )
        assert mock_sleep.call_count == 3
        mock_sleep.assert_called_with(0.5)

    def test_result_order_matches_task_order(self, forge_root, features_file):
        with (
            patch("forge_harness.iteration.dispatch.dispatch_task", side_effect=self._fake_dispatch),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["ALPHA", "BETA", "GAMMA"],
                features_path=features_file,
                forge_root=forge_root,
                max_parallel=5,
            )
        assert [r.task_id for r in results] == ["ALPHA", "BETA", "GAMMA"]

    def test_failures_included_in_results(self, forge_root, features_file):
        def side_effect(task_id, features_path=None, forge_root=None):
            if task_id == "FAIL":
                return DispatchResult(success=False, task_id=task_id, status="failed")
            return DispatchResult(success=True, task_id=task_id)

        with (
            patch("forge_harness.iteration.dispatch.dispatch_task", side_effect=side_effect),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["OK", "FAIL"],
                features_path=features_file,
                forge_root=forge_root,
                max_parallel=5,
            )
        assert any(not r.success for r in results)

    def test_default_max_parallel_is_3(self, forge_root, features_file):
        dispatched = []

        def fake(task_id, features_path=None, forge_root=None):
            dispatched.append(task_id)
            return DispatchResult(success=True, task_id=task_id)

        with (
            patch("forge_harness.iteration.dispatch.dispatch_task", side_effect=fake),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            dispatch_tasks(
                ["1", "2", "3", "4", "5"],
                features_path=features_file,
                forge_root=forge_root,
            )
        assert dispatched == ["1", "2", "3"]


# =============================================================================
# get_dispatch_status
# =============================================================================


class TestGetDispatchStatus:
    """Tests for the get_dispatch_status() convenience function."""

    def test_returns_none_when_tracking_dir_does_not_exist(self, tmp_path):
        root = tmp_path / "new_root"
        root.mkdir()
        assert get_dispatch_status("HRN-999", forge_root=root) is None

    def test_returns_none_when_task_not_tracked(self, forge_root):
        assert get_dispatch_status("HRN-999", forge_root=forge_root) is None

    def test_returns_dispatch_result_when_found(self, forge_root):
        td = forge_root / ".forge/dispatch"
        td.mkdir(exist_ok=True)
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
        (td / "HRN-001.json").write_text(json.dumps(data))

        result = get_dispatch_status("HRN-001", forge_root=forge_root)
        assert result is not None
        assert result.task_id == "HRN-001"
        assert result.agent_type is AgentType.BACKEND
        assert result.status == "in_progress"

    def test_returns_none_for_corrupt_json(self, forge_root):
        td = forge_root / ".forge/dispatch"
        td.mkdir(exist_ok=True)
        (td / "BAD.json").write_text("{bad json}")
        assert get_dispatch_status("BAD", forge_root=forge_root) is None

    def test_default_forge_root_uses_cwd(self, forge_root, monkeypatch):
        monkeypatch.chdir(forge_root)
        # Should not raise even when nothing tracked
        assert get_dispatch_status("HRN-EMPTY") is None


# =============================================================================
# ResultStatus enum
# =============================================================================


class TestResultStatus:
    def test_all_values_are_strings(self):
        for member in ResultStatus:
            assert isinstance(member.value, str)

    def test_is_str_enum(self):
        assert ResultStatus.SUCCESS == "success"

    def test_success_value(self):
        assert ResultStatus.SUCCESS.value == "success"

    def test_failure_value(self):
        assert ResultStatus.FAILURE.value == "failure"

    def test_partial_value(self):
        assert ResultStatus.PARTIAL.value == "partial"

    def test_timeout_value(self):
        assert ResultStatus.TIMEOUT.value == "timeout"

    def test_error_value(self):
        assert ResultStatus.ERROR.value == "error"

    def test_unknown_value(self):
        assert ResultStatus.UNKNOWN.value == "unknown"

    def test_construction_from_string(self):
        assert ResultStatus("partial") is ResultStatus.PARTIAL

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ResultStatus("bogus")


# =============================================================================
# ConflictSeverity enum
# =============================================================================


class TestConflictSeverity:
    def test_critical_value(self):
        assert ConflictSeverity.CRITICAL.value == "critical"

    def test_warning_value(self):
        assert ConflictSeverity.WARNING.value == "warning"

    def test_info_value(self):
        assert ConflictSeverity.INFO.value == "info"

    def test_is_str_enum(self):
        assert ConflictSeverity.CRITICAL == "critical"

    def test_construction_from_string(self):
        assert ConflictSeverity("info") is ConflictSeverity.INFO

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ConflictSeverity("none")


# =============================================================================
# AgentResult dataclass
# =============================================================================


class TestAgentResult:
    """Tests for AgentResult properties, __post_init__, and to_dict()."""

    def test_minimal_construction(self):
        r = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        assert r.agent_id == "a"
        assert r.task_id == "t"
        assert r.status is ResultStatus.SUCCESS
        assert r.findings == []
        assert r.errors == []
        assert r.metadata == {}
        assert r.output == ""

    def test_post_init_converts_string_status(self):
        r = AgentResult(agent_id="a", task_id="t", status="failure")
        assert r.status is ResultStatus.FAILURE

    def test_post_init_keeps_enum_status(self):
        r = AgentResult(agent_id="a", task_id="t", status=ResultStatus.PARTIAL)
        assert r.status is ResultStatus.PARTIAL

    def test_post_init_invalid_status_raises(self):
        with pytest.raises(ValueError):
            AgentResult(agent_id="a", task_id="t", status="invalid")

    def test_duration_seconds_when_completed(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        r = make_agent_result(
            started_at=t,
            completed_at=t + timedelta(seconds=30),
        )
        assert r.duration_seconds == 30.0

    def test_duration_seconds_none_when_no_completion(self):
        r = make_agent_result(completed_at=None)
        assert r.duration_seconds is None

    def test_duration_seconds_zero_when_instant(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        r = make_agent_result(started_at=t, completed_at=t)
        assert r.duration_seconds == 0.0

    def test_is_successful_true(self):
        r = make_agent_result(status=ResultStatus.SUCCESS)
        assert r.is_successful is True

    def test_is_successful_false_for_failure(self):
        r = make_agent_result(status=ResultStatus.FAILURE)
        assert r.is_successful is False

    def test_is_successful_false_for_partial(self):
        r = make_agent_result(status=ResultStatus.PARTIAL)
        assert r.is_successful is False

    def test_is_successful_false_for_timeout(self):
        r = make_agent_result(status=ResultStatus.TIMEOUT)
        assert r.is_successful is False

    def test_has_errors_true(self):
        r = make_agent_result(errors=["err1"])
        assert r.has_errors is True

    def test_has_errors_false(self):
        r = make_agent_result(errors=[])
        assert r.has_errors is False

    def test_to_dict_all_keys(self):
        r = make_agent_result()
        d = r.to_dict()
        required = {
            "agent_id", "task_id", "status", "findings",
            "errors", "metadata", "started_at", "completed_at", "duration_seconds",
        }
        assert required.issubset(d.keys())

    def test_to_dict_status_as_string(self):
        r = make_agent_result(status=ResultStatus.PARTIAL)
        assert r.to_dict()["status"] == "partial"

    def test_to_dict_completed_at_isoformat(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        r = make_agent_result(completed_at=t)
        assert r.to_dict()["completed_at"] == t.isoformat()

    def test_to_dict_completed_at_none(self):
        r = make_agent_result(completed_at=None)
        assert r.to_dict()["completed_at"] is None

    def test_findings_preserved_in_to_dict(self):
        r = make_agent_result(findings=["f1", "f2"])
        assert r.to_dict()["findings"] == ["f1", "f2"]

    def test_default_started_at_is_not_none(self):
        r = AgentResult(agent_id="a", task_id="t", status=ResultStatus.SUCCESS)
        assert r.started_at is not None


# =============================================================================
# DuplicateFinding dataclass
# =============================================================================


class TestDuplicateFinding:
    def test_count_reflects_agent_ids_length(self):
        dup = DuplicateFinding(
            finding="test",
            agent_ids=["a", "b", "c"],
            similarity_score=0.9,
        )
        assert dup.count == 3

    def test_count_zero_for_empty_agent_ids(self):
        dup = DuplicateFinding(finding="test", agent_ids=[], similarity_score=0.0)
        assert dup.count == 0

    def test_original_texts_defaults_to_empty(self):
        dup = DuplicateFinding(finding="x", agent_ids=["a"], similarity_score=1.0)
        assert dup.original_texts == []

    def test_original_texts_can_be_set(self):
        dup = DuplicateFinding(
            finding="canonical",
            agent_ids=["a", "b"],
            similarity_score=0.85,
            original_texts=["text a", "text b"],
        )
        assert len(dup.original_texts) == 2

    def test_similarity_score_preserved(self):
        dup = DuplicateFinding(finding="x", agent_ids=["a"], similarity_score=0.77)
        assert dup.similarity_score == 0.77


# =============================================================================
# Conflict dataclass
# =============================================================================


class TestConflict:
    def test_basic_construction(self):
        c = Conflict(
            severity=ConflictSeverity.CRITICAL,
            description="Critical mismatch",
            agent_ids=["a", "b"],
        )
        assert c.severity is ConflictSeverity.CRITICAL
        assert c.description == "Critical mismatch"
        assert c.agent_ids == ["a", "b"]

    def test_post_init_converts_string_severity(self):
        c = Conflict(severity="warning", description="w", agent_ids=[])
        assert c.severity is ConflictSeverity.WARNING

    def test_post_init_keeps_enum_severity(self):
        c = Conflict(severity=ConflictSeverity.INFO, description="i", agent_ids=[])
        assert c.severity is ConflictSeverity.INFO

    def test_post_init_invalid_severity_raises(self):
        with pytest.raises(ValueError):
            Conflict(severity="unknown_severity", description="x", agent_ids=[])

    def test_details_defaults_to_empty(self):
        c = Conflict(severity=ConflictSeverity.INFO, description="d", agent_ids=[])
        assert c.details == {}

    def test_details_can_be_set(self):
        c = Conflict(
            severity=ConflictSeverity.WARNING,
            description="d",
            agent_ids=["a"],
            details={"key": "val"},
        )
        assert c.details == {"key": "val"}


# =============================================================================
# AggregatedReport
# =============================================================================


class TestAggregatedReport:
    """Tests for AggregatedReport properties and output methods."""

    def _make(self, **kwargs) -> AggregatedReport:
        defaults = {
            "task_id": "TASK-001",
            "total_agents": 2,
            "successful_agents": 2,
            "failed_agents": 0,
            "partial_agents": 0,
            "results_by_status": {},
            "common_findings": [],
            "unique_findings": {},
            "conflicts": [],
            "errors": [],
        }
        defaults.update(kwargs)
        return AggregatedReport(**defaults)

    def test_success_rate_100_when_all_succeed(self):
        r = self._make(total_agents=5, successful_agents=5)
        assert r.success_rate == 100.0

    def test_success_rate_0_when_none_succeed(self):
        r = self._make(total_agents=3, successful_agents=0)
        assert r.success_rate == 0.0

    def test_success_rate_0_when_no_agents(self):
        r = self._make(total_agents=0, successful_agents=0)
        assert r.success_rate == 0.0

    def test_success_rate_fractional(self):
        r = self._make(total_agents=3, successful_agents=1)
        assert abs(r.success_rate - 33.33) < 0.01

    def test_duration_seconds_calculated_correctly(self):
        t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        r = self._make(started_at=t, completed_at=t + timedelta(seconds=90))
        assert r.duration_seconds == 90.0

    def test_duration_seconds_none_when_no_start(self):
        t = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        r = self._make(started_at=None, completed_at=t)
        assert r.duration_seconds is None

    def test_duration_seconds_none_when_no_end(self):
        t = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        r = self._make(started_at=t, completed_at=None)
        assert r.duration_seconds is None

    def test_to_markdown_contains_task_id(self):
        r = self._make(task_id="HRN-999")
        assert "HRN-999" in r.to_markdown()

    def test_to_markdown_summary_section(self):
        r = self._make()
        md = r.to_markdown()
        assert "## Summary" in md
        assert "Total Agents" in md

    def test_to_markdown_includes_duration(self):
        t = datetime(2026, 1, 1, tzinfo=UTC)
        r = self._make(started_at=t, completed_at=t + timedelta(seconds=45))
        assert "45.0s" in r.to_markdown()

    def test_to_markdown_no_duration_when_missing(self):
        r = self._make(started_at=None, completed_at=None)
        assert "Duration" not in r.to_markdown()

    def test_to_markdown_results_by_status(self):
        t = datetime(2026, 1, 1, tzinfo=UTC)
        result = make_agent_result(
            agent_id="worker-1",
            task_id="TASK-001",
            status=ResultStatus.SUCCESS,
            findings=["All tests pass"],
            started_at=t,
            completed_at=t + timedelta(seconds=15),
        )
        r = self._make(results_by_status={ResultStatus.SUCCESS: [result]})
        md = r.to_markdown()
        assert "SUCCESS" in md
        assert "worker-1" in md
        assert "All tests pass" in md

    def test_to_markdown_common_findings(self):
        dup = DuplicateFinding(
            finding="Fixed the auth bug",
            agent_ids=["a1", "a2"],
            similarity_score=0.95,
        )
        r = self._make(common_findings=[dup])
        md = r.to_markdown()
        assert "Common Findings" in md
        assert "Fixed the auth bug" in md
        assert "95.00%" in md

    def test_to_markdown_common_findings_empty_omitted(self):
        r = self._make(common_findings=[])
        assert "Common Findings" not in r.to_markdown()

    def test_to_markdown_unique_findings(self):
        r = self._make(unique_findings={"agent-x": ["Unique insight"]})
        md = r.to_markdown()
        assert "Unique Findings" in md
        assert "agent-x" in md
        assert "Unique insight" in md

    def test_to_markdown_unique_findings_empty_omitted(self):
        r = self._make(unique_findings={})
        assert "Unique Findings" not in r.to_markdown()

    def test_to_markdown_critical_conflict(self):
        c = Conflict(
            severity=ConflictSeverity.CRITICAL,
            description="Contradictory results",
            agent_ids=["a", "b"],
        )
        r = self._make(conflicts=[c])
        md = r.to_markdown()
        assert "CRITICAL" in md
        assert "Contradictory results" in md

    def test_to_markdown_info_conflict(self):
        c = Conflict(
            severity=ConflictSeverity.INFO,
            description="Minor discrepancy",
            agent_ids=["a"],
        )
        r = self._make(conflicts=[c])
        md = r.to_markdown()
        assert "INFO" in md

    def test_to_markdown_conflict_with_details(self):
        c = Conflict(
            severity=ConflictSeverity.WARNING,
            description="Metadata mismatch",
            agent_ids=["a"],
            details={"field": "version"},
        )
        r = self._make(conflicts=[c])
        md = r.to_markdown()
        assert "field" in md
        assert "version" in md

    def test_to_markdown_conflicts_empty_omitted(self):
        r = self._make(conflicts=[])
        assert "Conflicts" not in r.to_markdown()

    def test_to_markdown_errors(self):
        r = self._make(errors=[{"agent_id": "bad-agent", "error": "Connection refused"}])
        md = r.to_markdown()
        assert "bad-agent" in md
        assert "Connection refused" in md

    def test_to_markdown_errors_empty_omitted(self):
        r = self._make(errors=[])
        assert "## Errors" not in r.to_markdown()

    def test_to_markdown_error_missing_agent_id(self):
        r = self._make(errors=[{"error": "Unknown agent error"}])
        md = r.to_markdown()
        assert "unknown" in md

    def test_to_markdown_common_findings_sorted_by_count_descending(self):
        low = DuplicateFinding(finding="Low count", agent_ids=["a"], similarity_score=0.9)
        high = DuplicateFinding(
            finding="High count", agent_ids=["a", "b", "c"], similarity_score=0.9
        )
        r = self._make(common_findings=[low, high])
        md = r.to_markdown()
        assert md.index("High count") < md.index("Low count")

    def test_to_dict_fields(self):
        t = datetime(2026, 1, 1, tzinfo=UTC)
        r = self._make(
            task_id="T-1",
            total_agents=4,
            successful_agents=3,
            failed_agents=1,
            started_at=t,
            completed_at=t + timedelta(seconds=60),
        )
        d = r.to_dict()
        assert d["task_id"] == "T-1"
        assert d["total_agents"] == 4
        assert d["successful_agents"] == 3
        assert d["failed_agents"] == 1
        assert d["duration_seconds"] == 60.0

    def test_to_dict_common_findings_count(self):
        dups = [
            DuplicateFinding(finding=f"f{i}", agent_ids=["a", "b"], similarity_score=0.9)
            for i in range(3)
        ]
        r = self._make(common_findings=dups)
        assert r.to_dict()["common_findings_count"] == 3

    def test_to_dict_conflicts_count(self):
        conflicts = [
            Conflict(severity=ConflictSeverity.INFO, description="x", agent_ids=["a"])
            for _ in range(2)
        ]
        r = self._make(conflicts=conflicts)
        assert r.to_dict()["conflicts_count"] == 2

    def test_to_dict_errors_count(self):
        r = self._make(errors=[{"agent_id": "a", "error": "e"} for _ in range(4)])
        assert r.to_dict()["errors_count"] == 4

    def test_to_dict_times_in_isoformat(self):
        t = datetime(2026, 1, 1, tzinfo=UTC)
        r = self._make(started_at=t, completed_at=t + timedelta(hours=1))
        d = r.to_dict()
        assert d["started_at"] == t.isoformat()

    def test_to_dict_none_times(self):
        r = self._make(started_at=None, completed_at=None)
        d = r.to_dict()
        assert d["started_at"] is None
        assert d["completed_at"] is None
        assert d["duration_seconds"] is None


# =============================================================================
# ResultAggregator — initialization
# =============================================================================


class TestResultAggregatorInit:
    def test_default_threshold(self):
        agg = ResultAggregator()
        assert agg.similarity_threshold == 0.75

    def test_custom_threshold(self):
        agg = ResultAggregator(similarity_threshold=0.9)
        assert agg.similarity_threshold == 0.9

    def test_conflict_detection_default_true(self):
        assert ResultAggregator().conflict_detection is True

    def test_conflict_detection_can_be_disabled(self):
        agg = ResultAggregator(conflict_detection=False)
        assert agg.conflict_detection is False

    def test_threshold_zero_valid(self):
        ResultAggregator(similarity_threshold=0.0)

    def test_threshold_one_valid(self):
        ResultAggregator(similarity_threshold=1.0)

    def test_threshold_below_zero_raises(self):
        with pytest.raises(ValueError, match="similarity_threshold"):
            ResultAggregator(similarity_threshold=-0.01)

    def test_threshold_above_one_raises(self):
        with pytest.raises(ValueError, match="similarity_threshold"):
            ResultAggregator(similarity_threshold=1.01)


# =============================================================================
# ResultAggregator.aggregate — validation
# =============================================================================


class TestResultAggregatorValidation:
    def test_empty_list_raises_value_error(self, aggregator):
        with pytest.raises(ValueError, match="empty"):
            aggregator.aggregate([])

    def test_inconsistent_task_ids_raises_value_error(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="TASK-001"),
            make_agent_result(agent_id="a2", task_id="TASK-002"),
        ]
        with pytest.raises(ValueError, match="inconsistent task IDs"):
            aggregator.aggregate(results)

    def test_single_result_valid(self, aggregator):
        report = aggregator.aggregate([make_agent_result()])
        assert report.total_agents == 1

    def test_task_id_from_first_result(self, aggregator):
        results = [make_agent_result(task_id="HRN-007")]
        assert aggregator.aggregate(results).task_id == "HRN-007"


# =============================================================================
# ResultAggregator.aggregate — status grouping
# =============================================================================


class TestResultAggregatorGrouping:
    def test_all_success_counted(self, aggregator):
        results = [
            make_agent_result(agent_id=f"a{i}", task_id="T", status=ResultStatus.SUCCESS)
            for i in range(4)
        ]
        report = aggregator.aggregate(results)
        assert report.total_agents == 4
        assert report.successful_agents == 4
        assert report.failed_agents == 0

    def test_mixed_statuses(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", status=ResultStatus.SUCCESS),
            make_agent_result(agent_id="a2", task_id="T", status=ResultStatus.FAILURE),
            make_agent_result(agent_id="a3", task_id="T", status=ResultStatus.PARTIAL),
        ]
        report = aggregator.aggregate(results)
        assert report.successful_agents == 1
        assert report.failed_agents == 1
        assert report.partial_agents == 1

    def test_timeout_status_in_results_by_status(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", status=ResultStatus.TIMEOUT),
        ]
        report = aggregator.aggregate(results)
        assert ResultStatus.TIMEOUT in report.results_by_status

    def test_error_status_in_results_by_status(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", status=ResultStatus.ERROR),
        ]
        report = aggregator.aggregate(results)
        assert ResultStatus.ERROR in report.results_by_status

    def test_success_rate_100(self, aggregator):
        results = [
            make_agent_result(agent_id=f"w{i}", task_id="T", status=ResultStatus.SUCCESS)
            for i in range(3)
        ]
        assert aggregator.aggregate(results).success_rate == 100.0

    def test_success_rate_zero(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", status=ResultStatus.FAILURE),
        ]
        assert aggregator.aggregate(results).success_rate == 0.0


# =============================================================================
# ResultAggregator — time bounds
# =============================================================================


class TestResultAggregatorTimeBounds:
    def test_started_at_is_earliest(self, aggregator):
        t_early = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        t_late = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        results = [
            make_agent_result(agent_id="a1", task_id="T", started_at=t_late),
            make_agent_result(agent_id="a2", task_id="T", started_at=t_early),
        ]
        assert aggregator.aggregate(results).started_at == t_early

    def test_completed_at_is_latest(self, aggregator):
        t = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        results = [
            make_agent_result(agent_id="a1", task_id="T", completed_at=t + timedelta(seconds=30)),
            make_agent_result(agent_id="a2", task_id="T", completed_at=t + timedelta(seconds=90)),
        ]
        assert aggregator.aggregate(results).completed_at == t + timedelta(seconds=90)

    def test_completed_at_none_when_all_incomplete(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", completed_at=None),
        ]
        assert aggregator.aggregate(results).completed_at is None


# =============================================================================
# ResultAggregator — error collection
# =============================================================================


class TestResultAggregatorErrorCollection:
    def test_errors_collected_with_attribution(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", errors=["E1", "E2"]),
            make_agent_result(agent_id="a2", task_id="T", errors=["E3"]),
        ]
        report = aggregator.aggregate(results)
        assert len(report.errors) == 3
        agent_ids = [e["agent_id"] for e in report.errors]
        assert agent_ids.count("a1") == 2
        assert agent_ids.count("a2") == 1

    def test_no_errors_when_clean(self, aggregator):
        results = [make_agent_result(agent_id=f"a{i}", task_id="T") for i in range(2)]
        assert aggregator.aggregate(results).errors == []

    def test_error_dict_has_correct_keys(self, aggregator):
        results = [make_agent_result(agent_id="a1", task_id="T", errors=["DB failed"])]
        entry = aggregator.aggregate(results).errors[0]
        assert entry["agent_id"] == "a1"
        assert entry["error"] == "DB failed"


# =============================================================================
# ResultAggregator — similarity and deduplication
# =============================================================================


class TestResultAggregatorSimilarity:
    def test_identical_strings_score_1(self, aggregator):
        assert aggregator._calculate_similarity("hello", "hello") == 1.0

    def test_empty_strings_score_1(self, aggregator):
        assert aggregator._calculate_similarity("", "") == 1.0

    def test_one_empty_score_0(self, aggregator):
        assert aggregator._calculate_similarity("", "text") == 0.0

    def test_case_insensitive(self, aggregator):
        assert aggregator._calculate_similarity("Hello World", "hello world") == 1.0

    def test_completely_different_low_score(self, aggregator):
        score = aggregator._calculate_similarity("aaa", "zzz")
        assert score < 0.5

    def test_symmetry(self, aggregator):
        s1 = aggregator._calculate_similarity("text one", "text two")
        s2 = aggregator._calculate_similarity("text two", "text one")
        assert s1 == pytest.approx(s2)

    def test_returns_float(self, aggregator):
        assert isinstance(aggregator._calculate_similarity("x", "y"), float)

    def test_score_in_range(self, aggregator):
        score = aggregator._calculate_similarity("something", "something else")
        assert 0.0 <= score <= 1.0


class TestResultAggregatorDeduplication:
    def test_identical_findings_are_common(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", findings=["Fixed auth bug"]),
            make_agent_result(agent_id="a2", task_id="T", findings=["Fixed auth bug"]),
        ]
        report = aggregator.aggregate(results)
        assert len(report.common_findings) == 1
        assert report.common_findings[0].count == 2

    def test_very_different_findings_are_unique(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", findings=["Added indexing"]),
            make_agent_result(agent_id="a2", task_id="T", findings=["Implemented OAuth2"]),
        ]
        report = aggregator.aggregate(results)
        assert len(report.common_findings) == 0
        assert "a1" in report.unique_findings
        assert "a2" in report.unique_findings

    def test_no_findings_returns_empty(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", findings=[]),
            make_agent_result(agent_id="a2", task_id="T", findings=[]),
        ]
        report = aggregator.aggregate(results)
        assert report.common_findings == []
        assert report.unique_findings == {}

    def test_high_threshold_produces_fewer_common(self):
        strict = ResultAggregator(similarity_threshold=0.99)
        results = [
            make_agent_result(agent_id="a1", task_id="T", findings=["Fixed the bug"]),
            make_agent_result(agent_id="a2", task_id="T", findings=["Fixed the buggy code"]),
        ]
        report = strict.aggregate(results)
        assert len(report.common_findings) == 0

    def test_low_threshold_produces_more_common(self):
        lenient = ResultAggregator(similarity_threshold=0.3)
        results = [
            make_agent_result(agent_id="a1", task_id="T", findings=["Fixed auth"]),
            make_agent_result(agent_id="a2", task_id="T", findings=["Fixed authentication"]),
        ]
        report = lenient.aggregate(results)
        assert len(report.common_findings) >= 1

    def test_canonical_finding_is_longest(self, aggregator):
        results = [
            make_agent_result(
                agent_id="x1", task_id="T-2",
                findings=["Fixed bug in authentication"],
            ),
            make_agent_result(
                agent_id="x2", task_id="T-2",
                findings=["Fixed bug in authentication module"],
            ),
        ]
        report = aggregator.aggregate(results)
        if report.common_findings:
            assert report.common_findings[0].finding == "Fixed bug in authentication module"

    def test_common_finding_not_in_unique(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", findings=["Shared finding"]),
            make_agent_result(agent_id="a2", task_id="T", findings=["Shared finding"]),
        ]
        report = aggregator.aggregate(results)
        all_unique = [f for fs in report.unique_findings.values() for f in fs]
        assert "Shared finding" not in all_unique

    def test_three_agents_common_finding(self, aggregator):
        results = [
            make_agent_result(agent_id=f"a{i}", task_id="T", findings=["Same finding"])
            for i in range(3)
        ]
        report = aggregator.aggregate(results)
        assert len(report.common_findings) == 1
        assert report.common_findings[0].count == 3


# =============================================================================
# ResultAggregator — conflict detection
# =============================================================================


class TestResultAggregatorConflicts:
    def test_success_and_failure_produces_critical(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", status=ResultStatus.SUCCESS),
            make_agent_result(agent_id="a2", task_id="T", status=ResultStatus.FAILURE),
        ]
        report = aggregator.aggregate(results)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) >= 1

    def test_all_success_no_critical(self, aggregator):
        results = [
            make_agent_result(agent_id=f"a{i}", task_id="T", status=ResultStatus.SUCCESS)
            for i in range(3)
        ]
        report = aggregator.aggregate(results)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) == 0

    def test_all_failure_no_critical(self, aggregator):
        results = [
            make_agent_result(agent_id=f"a{i}", task_id="T", status=ResultStatus.FAILURE)
            for i in range(2)
        ]
        critical = [
            c for c in aggregator.aggregate(results).conflicts
            if c.severity == ConflictSeverity.CRITICAL
        ]
        assert len(critical) == 0

    def test_metadata_conflict_produces_warning(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", metadata={"env": "staging"}),
            make_agent_result(agent_id="a2", task_id="T", metadata={"env": "production"}),
        ]
        warnings = [
            c for c in aggregator.aggregate(results).conflicts
            if c.severity == ConflictSeverity.WARNING
        ]
        assert len(warnings) >= 1

    def test_matching_metadata_no_conflict(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", metadata={"v": "1.0"}),
            make_agent_result(agent_id="a2", task_id="T", metadata={"v": "1.0"}),
        ]
        assert aggregator.aggregate(results).conflicts == []

    def test_conflict_detection_disabled_skips_all(self):
        agg = ResultAggregator(conflict_detection=False)
        results = [
            make_agent_result(agent_id="a1", task_id="T", status=ResultStatus.SUCCESS),
            make_agent_result(agent_id="a2", task_id="T", status=ResultStatus.FAILURE),
        ]
        assert agg.aggregate(results).conflicts == []

    def test_conflict_includes_both_agent_ids(self, aggregator):
        results = [
            make_agent_result(agent_id="winner", task_id="T", status=ResultStatus.SUCCESS),
            make_agent_result(agent_id="loser", task_id="T", status=ResultStatus.FAILURE),
        ]
        report = aggregator.aggregate(results)
        agent_ids = report.conflicts[0].agent_ids
        assert "winner" in agent_ids
        assert "loser" in agent_ids

    def test_metadata_list_values_do_not_raise(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", metadata={"tags": ["a", "b"]}),
            make_agent_result(agent_id="a2", task_id="T", metadata={"tags": ["c", "d"]}),
        ]
        report = aggregator.aggregate(results)
        assert isinstance(report.conflicts, list)

    def test_metadata_dict_values_do_not_raise(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", metadata={"opts": {"x": 1}}),
            make_agent_result(agent_id="a2", task_id="T", metadata={"opts": {"y": 2}}),
        ]
        report = aggregator.aggregate(results)
        assert isinstance(report.conflicts, list)

    def test_metadata_conflict_description_mentions_key(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", metadata={"coverage": "80%"}),
            make_agent_result(agent_id="a2", task_id="T", metadata={"coverage": "90%"}),
        ]
        warnings = [
            c for c in aggregator.aggregate(results).conflicts
            if c.severity == ConflictSeverity.WARNING
        ]
        assert "coverage" in warnings[0].description

    def test_partial_plus_timeout_no_critical_conflict(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", status=ResultStatus.PARTIAL),
            make_agent_result(agent_id="a2", task_id="T", status=ResultStatus.TIMEOUT),
        ]
        critical = [
            c for c in aggregator.aggregate(results).conflicts
            if c.severity == ConflictSeverity.CRITICAL
        ]
        assert len(critical) == 0


# =============================================================================
# ResultAggregator._group_by_status — direct method tests
# =============================================================================


class TestGroupByStatus:
    def test_groups_correctly(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", task_id="T", status=ResultStatus.SUCCESS),
            make_agent_result(agent_id="a2", task_id="T", status=ResultStatus.SUCCESS),
            make_agent_result(agent_id="a3", task_id="T", status=ResultStatus.FAILURE),
        ]
        grouped = aggregator._group_by_status(results)
        assert len(grouped[ResultStatus.SUCCESS]) == 2
        assert len(grouped[ResultStatus.FAILURE]) == 1

    def test_empty_list_returns_empty(self, aggregator):
        assert aggregator._group_by_status([]) == {}

    def test_single_result(self, aggregator):
        results = [make_agent_result(task_id="T", status=ResultStatus.PARTIAL)]
        grouped = aggregator._group_by_status(results)
        assert ResultStatus.PARTIAL in grouped


# =============================================================================
# ResultAggregator._collect_errors — direct method tests
# =============================================================================


class TestCollectErrors:
    def test_no_errors_returns_empty(self, aggregator):
        results = [make_agent_result(agent_id="a1", errors=[])]
        assert aggregator._collect_errors(results) == []

    def test_multiple_agents_multiple_errors(self, aggregator):
        results = [
            make_agent_result(agent_id="x", errors=["E1", "E2"]),
            make_agent_result(agent_id="y", errors=["E3"]),
        ]
        errors = aggregator._collect_errors(results)
        assert len(errors) == 3
        assert {"agent_id", "error"} == set(errors[0].keys())


# =============================================================================
# ResultAggregator._detect_metadata_conflicts — direct method tests
# =============================================================================


class TestDetectMetadataConflicts:
    def test_no_metadata_no_conflicts(self, aggregator):
        results = [make_agent_result(agent_id="a1"), make_agent_result(agent_id="a2")]
        assert aggregator._detect_metadata_conflicts(results) == []

    def test_same_metadata_no_conflict(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", metadata={"env": "prod"}),
            make_agent_result(agent_id="a2", metadata={"env": "prod"}),
        ]
        assert aggregator._detect_metadata_conflicts(results) == []

    def test_different_values_conflict(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", metadata={"build": "1.0"}),
            make_agent_result(agent_id="a2", metadata={"build": "2.0"}),
        ]
        conflicts = aggregator._detect_metadata_conflicts(results)
        assert len(conflicts) == 1
        assert conflicts[0].severity == ConflictSeverity.WARNING

    def test_partial_coverage_no_conflict(self, aggregator):
        """Only one agent has key — single value, no conflict."""
        results = [
            make_agent_result(agent_id="a1", metadata={"only_a1": "value"}),
            make_agent_result(agent_id="a2", metadata={}),
        ]
        assert aggregator._detect_metadata_conflicts(results) == []

    def test_conflict_details_has_key_and_values(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", metadata={"tier": "free"}),
            make_agent_result(agent_id="a2", metadata={"tier": "pro"}),
        ]
        conflict = aggregator._detect_metadata_conflicts(results)[0]
        assert "key" in conflict.details
        assert "values" in conflict.details

    def test_multiple_keys_multiple_conflicts(self, aggregator):
        results = [
            make_agent_result(agent_id="a1", metadata={"k1": "v1", "k2": "x"}),
            make_agent_result(agent_id="a2", metadata={"k1": "v2", "k2": "y"}),
        ]
        assert len(aggregator._detect_metadata_conflicts(results)) == 2


# =============================================================================
# Integration: dispatch → aggregate pipeline
# =============================================================================


class TestDispatchAggregatorIntegration:
    """End-to-end scenario: aggregate results from tasks that were dispatched."""

    def test_full_pipeline_markdown_and_dict(self):
        """Simulates aggregating results from two dispatched tasks."""
        agg = ResultAggregator()
        t = datetime(2026, 2, 22, 10, 0, tzinfo=UTC)

        results = [
            AgentResult(
                agent_id="backend-engineer",
                task_id="HRN-001",
                status=ResultStatus.SUCCESS,
                findings=["Implemented REST API", "Added unit tests"],
                started_at=t,
                completed_at=t + timedelta(seconds=120),
            ),
            AgentResult(
                agent_id="qa-tester",
                task_id="HRN-001",
                status=ResultStatus.SUCCESS,
                findings=["All tests pass", "Coverage 87%"],
                started_at=t,
                completed_at=t + timedelta(seconds=90),
            ),
        ]

        report = agg.aggregate(results)
        assert report.total_agents == 2
        assert report.successful_agents == 2
        assert report.success_rate == 100.0
        assert report.completed_at == t + timedelta(seconds=120)

        md = report.to_markdown()
        assert "HRN-001" in md
        assert "backend-engineer" in md

        d = report.to_dict()
        assert d["task_id"] == "HRN-001"
        assert d["success_rate"] == 100.0

    def test_partial_failure_pipeline(self):
        """One agent fails — should produce critical conflict and capture error."""
        agg = ResultAggregator()
        t = datetime(2026, 2, 22, 10, 0, tzinfo=UTC)

        results = [
            AgentResult(
                agent_id="primary",
                task_id="HRN-002",
                status=ResultStatus.SUCCESS,
                findings=["Feature complete"],
                metadata={"env": "staging"},
                started_at=t,
                completed_at=t + timedelta(seconds=60),
            ),
            AgentResult(
                agent_id="secondary",
                task_id="HRN-002",
                status=ResultStatus.FAILURE,
                errors=["OOM error during run"],
                metadata={"env": "production"},
                started_at=t,
                completed_at=t + timedelta(seconds=20),
            ),
        ]

        report = agg.aggregate(results)
        assert report.failed_agents == 1
        assert len(report.errors) == 1
        assert report.errors[0]["agent_id"] == "secondary"

        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) >= 1

        # Markdown should render without error
        md = report.to_markdown()
        assert "HRN-002" in md
        assert "OOM error" in md

    def test_dispatch_result_fed_to_tracker_then_retrieved(self, forge_root):
        """DispatchResult can be saved by TaskTracker and loaded back."""
        tracker = TaskTracker(tracking_dir=forge_root / ".forge/dispatch")
        now = datetime.now(UTC)
        result = DispatchResult(
            success=True,
            task_id="HRN-PIPE-001",
            session_id="forge-task-HRN-PIPE-001",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=now,
            metadata={"title": "Pipeline integration test"},
        )
        tracker.save_dispatch("HRN-PIPE-001", result)

        loaded = tracker.load_dispatch("HRN-PIPE-001")
        assert loaded is not None
        assert loaded.agent_type is AgentType.BACKEND
        assert loaded.metadata["title"] == "Pipeline integration test"

        tracker.update_status("HRN-PIPE-001", "completed")
        updated = tracker.load_dispatch("HRN-PIPE-001")
        assert updated.status == "completed"

    def test_prompt_feeds_into_task_metadata(self):
        """_build_task_prompt output is placed into DispatchResult metadata."""
        task = make_prioritized_task(
            task_id="HRN-PROMPT",
            title="Build REST API",
            acceptance_criteria=["Returns 200", "Authenticated"],
            test_command="pytest tests/api/",
            epic="api-platform",
        )
        prompt = _build_task_prompt(task)

        # The prompt should be embedded in dispatch result metadata
        result = DispatchResult(
            success=True,
            task_id="HRN-PROMPT",
            session_id="sess",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            metadata={"prompt": prompt},
        )

        assert "Returns 200" in result.metadata["prompt"]
        assert "pytest tests/api/" in result.metadata["prompt"]
        assert "api-platform" in result.metadata["prompt"]

    def test_five_agent_scenario(self):
        """Five agents with mixed outcomes — comprehensive aggregation check."""
        agg = ResultAggregator()
        t = datetime(2026, 2, 22, 8, 0, tzinfo=UTC)

        results = [
            AgentResult(
                agent_id=f"worker-{i}",
                task_id="BIG-001",
                status=ResultStatus.SUCCESS if i < 4 else ResultStatus.FAILURE,
                findings=["Shared insight", f"Unique finding {i}"],
                errors=[] if i < 4 else ["Timeout after 300s"],
                metadata={"build": "v2" if i < 4 else "v1"},
                started_at=t + timedelta(seconds=i * 5),
                completed_at=t + timedelta(seconds=i * 5 + 30),
            )
            for i in range(5)
        ]

        report = agg.aggregate(results)
        assert report.total_agents == 5
        assert report.successful_agents == 4
        assert report.failed_agents == 1
        assert report.success_rate == pytest.approx(80.0)
        assert len(report.errors) == 1
        assert len(report.common_findings) >= 1

        # Should have critical conflict (success + failure)
        critical = [c for c in report.conflicts if c.severity == ConflictSeverity.CRITICAL]
        assert len(critical) >= 1

        # Should have metadata warning (build: v2 vs v1)
        warnings = [c for c in report.conflicts if c.severity == ConflictSeverity.WARNING]
        assert len(warnings) >= 1

        # Should be serializable
        md = report.to_markdown()
        d = report.to_dict()
        assert "BIG-001" in md
        assert d["total_agents"] == 5
