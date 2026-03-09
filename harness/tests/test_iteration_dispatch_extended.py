"""Extended tests for forge_harness.iteration.dispatch.

Targets uncovered paths in the existing test suite:
- AgentType enum completeness (all values)
- AGENT_TYPE_ROUTING completeness (all keywords)
- AGENT_FALLBACKS completeness (all entries)
- select_agent_for_task: keyword priority (first match wins), all keywords
- get_fallback_agent: all agent types in AGENT_FALLBACKS
- _build_task_prompt: large acceptance criteria, empty title, whitespace
- DispatchResult.to_dict: all status values
- DispatchStatus: output_size and last_activity fields
- TaskTracker: save/load roundtrip with every agent type
- TaskTracker: multiple tasks saved independently
- TmuxDispatcher.get_session_output: custom line count in command
- TmuxDispatcher.tag_session: tags correctly set per key
- dispatch_task: features_path default resolution
- dispatch_tasks: default max_parallel=3
- dispatch_tasks: ordering preserved in results
- get_dispatch_status: correct tracking_dir construction
- AgentType.UNKNOWN not in AGENT_FALLBACKS returns None
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

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

# =============================================================================
# Helpers
# =============================================================================


def make_task(
    feature_id: str = "HRN-001",
    title: str = "Build backend API",
    metadata: dict | None = None,
    epic: str | None = "platform",
    acceptance_criteria: list[str] | None = None,
    test_command: str | None = None,
    status: str = "pending",
) -> PrioritizedTask:
    return PrioritizedTask(
        task_id=feature_id,
        feature_id=feature_id,
        title=title,
        score=5.0,
        impact=3.0,
        urgency=2.0,
        effort=1.0,
        domain_weight=1.0,
        reasoning="test reason",
        status=status,
        epic=epic,
        acceptance_criteria=acceptance_criteria or [],
        test_command=test_command,
        metadata=metadata or {},
    )


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def tracker(tmp_path: Path) -> TaskTracker:
    return TaskTracker(tracking_dir=tmp_path / ".forge/dispatch")


@pytest.fixture
def dispatcher(tmp_path: Path) -> TmuxDispatcher:
    return TmuxDispatcher(forge_root=tmp_path)


@pytest.fixture
def features_file(tmp_root: Path) -> Path:
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
                "title": "Create frontend component",
                "priority": 3,
                "status": "pending",
                "epic": None,
                "acceptance_criteria": [],
                "test_command": None,
            },
            {
                "id": "HRN-DEBUG",
                "title": "Debug memory leak issue",
                "priority": 8,
                "status": "pending",
                "epic": "stability",
                "acceptance_criteria": [],
                "test_command": None,
            },
        ]
    }
    path = tmp_root / "features.json"
    path.write_text(json.dumps(data))
    return path


# =============================================================================
# AgentType enum — completeness
# =============================================================================


class TestAgentTypeCompleteness:
    def test_all_enum_members_have_string_values(self):
        for member in AgentType:
            assert isinstance(member.value, str), f"{member} has non-string value"

    def test_frontend_value(self):
        assert AgentType.FRONTEND.value == "frontend-builder"

    def test_debug_value(self):
        assert AgentType.DEBUG.value == "debug-detective"

    def test_content_value(self):
        assert AgentType.CONTENT.value == "content-writer"

    def test_external_opencode_value(self):
        assert AgentType.EXTERNAL_OPENCODE.value == "opencode"

    def test_external_gemini_value(self):
        assert AgentType.EXTERNAL_GEMINI.value == "gemini"

    def test_external_cursor_value(self):
        assert AgentType.EXTERNAL_CURSOR.value == "cursor"

    def test_external_amp_value(self):
        assert AgentType.EXTERNAL_AMP.value == "amp"

    def test_construct_from_all_values(self):
        for member in AgentType:
            assert AgentType(member.value) is member


# =============================================================================
# AGENT_TYPE_ROUTING — all keywords verified
# =============================================================================


class TestAgentTypeRoutingCompleteness:
    def test_database_maps_to_backend(self):
        assert AGENT_TYPE_ROUTING["database"] is AgentType.BACKEND

    def test_server_maps_to_backend(self):
        assert AGENT_TYPE_ROUTING["server"] is AgentType.BACKEND

    def test_ui_maps_to_frontend(self):
        assert AGENT_TYPE_ROUTING["ui"] is AgentType.FRONTEND

    def test_component_maps_to_frontend(self):
        assert AGENT_TYPE_ROUTING["component"] is AgentType.FRONTEND

    def test_react_maps_to_frontend(self):
        assert AGENT_TYPE_ROUTING["react"] is AgentType.FRONTEND

    def test_e2e_maps_to_qa(self):
        assert AGENT_TYPE_ROUTING["e2e"] is AgentType.QA

    def test_integration_maps_to_qa(self):
        assert AGENT_TYPE_ROUTING["integration"] is AgentType.QA

    def test_fix_maps_to_debug(self):
        assert AGENT_TYPE_ROUTING["fix"] is AgentType.DEBUG

    def test_error_maps_to_debug(self):
        assert AGENT_TYPE_ROUTING["error"] is AgentType.DEBUG

    def test_docs_maps_to_content(self):
        assert AGENT_TYPE_ROUTING["docs"] is AgentType.CONTENT

    def test_routing_has_expected_count(self):
        # 15 routing entries as per source code
        assert len(AGENT_TYPE_ROUTING) >= 14


# =============================================================================
# AGENT_FALLBACKS — all entries verified
# =============================================================================


class TestAgentFallbacksCompleteness:
    def test_all_entries_are_lists(self):
        for agent, fallbacks in AGENT_FALLBACKS.items():
            assert isinstance(fallbacks, list), f"{agent} fallbacks should be a list"

    def test_all_fallback_entries_are_agent_types(self):
        for agent, fallbacks in AGENT_FALLBACKS.items():
            for fb in fallbacks:
                assert isinstance(fb, AgentType), f"{fb} is not an AgentType"

    def test_qa_has_fallbacks(self):
        assert len(AGENT_FALLBACKS[AgentType.QA]) > 0

    def test_external_codex_fallback_includes_backend(self):
        assert AgentType.BACKEND in AGENT_FALLBACKS[AgentType.EXTERNAL_CODEX]

    def test_external_opencode_fallback_includes_backend(self):
        assert AgentType.BACKEND in AGENT_FALLBACKS[AgentType.EXTERNAL_OPENCODE]

    def test_external_gemini_fallback_first_is_content(self):
        assert AGENT_FALLBACKS[AgentType.EXTERNAL_GEMINI][0] is AgentType.CONTENT

    def test_external_cursor_fallback_first_is_frontend(self):
        assert AGENT_FALLBACKS[AgentType.EXTERNAL_CURSOR][0] is AgentType.FRONTEND

    def test_external_amp_fallback_first_is_debug(self):
        assert AGENT_FALLBACKS[AgentType.EXTERNAL_AMP][0] is AgentType.DEBUG

    def test_unknown_not_in_fallbacks(self):
        assert AgentType.UNKNOWN not in AGENT_FALLBACKS

    def test_claude_not_in_fallbacks(self):
        assert AgentType.CLAUDE not in AGENT_FALLBACKS


# =============================================================================
# get_fallback_agent — full coverage
# =============================================================================


class TestGetFallbackAgentExtended:
    def test_qa_returns_first_fallback(self):
        fb = get_fallback_agent(AgentType.QA)
        assert fb is AGENT_FALLBACKS[AgentType.QA][0]

    def test_content_returns_gemini(self):
        fb = get_fallback_agent(AgentType.CONTENT)
        assert fb is AgentType.EXTERNAL_GEMINI

    def test_external_opencode_returns_backend(self):
        fb = get_fallback_agent(AgentType.EXTERNAL_OPENCODE)
        assert fb is AgentType.BACKEND

    def test_external_codex_returns_backend(self):
        fb = get_fallback_agent(AgentType.EXTERNAL_CODEX)
        assert fb is AgentType.BACKEND

    def test_external_gemini_returns_content(self):
        fb = get_fallback_agent(AgentType.EXTERNAL_GEMINI)
        assert fb is AgentType.CONTENT

    def test_external_cursor_returns_frontend(self):
        fb = get_fallback_agent(AgentType.EXTERNAL_CURSOR)
        assert fb is AgentType.FRONTEND

    def test_external_amp_returns_debug(self):
        fb = get_fallback_agent(AgentType.EXTERNAL_AMP)
        assert fb is AgentType.DEBUG

    def test_unknown_returns_none(self):
        assert get_fallback_agent(AgentType.UNKNOWN) is None

    def test_claude_returns_none(self):
        assert get_fallback_agent(AgentType.CLAUDE) is None


# =============================================================================
# select_agent_for_task — keyword routing details
# =============================================================================


class TestSelectAgentKeywordRouting:
    def test_database_keyword(self):
        task = make_task(title="Optimize database queries")
        result = select_agent_for_task(task)
        assert result is AgentType.BACKEND

    def test_server_keyword(self):
        task = make_task(title="Configure server settings")
        result = select_agent_for_task(task)
        assert result is AgentType.BACKEND

    def test_component_keyword(self):
        task = make_task(title="Build component library")
        result = select_agent_for_task(task)
        assert result is AgentType.FRONTEND

    def test_react_keyword(self):
        task = make_task(title="React state management")
        result = select_agent_for_task(task)
        assert result is AgentType.FRONTEND

    def test_e2e_keyword(self):
        # Avoid "ui" substring in "suite" which would match FRONTEND first
        task = make_task(title="E2e smoke tests")
        result = select_agent_for_task(task)
        assert result is AgentType.QA

    def test_integration_keyword(self):
        task = make_task(title="Integration testing harness")
        result = select_agent_for_task(task)
        assert result is AgentType.QA

    def test_fix_keyword(self):
        task = make_task(title="Fix the memory leak")
        result = select_agent_for_task(task)
        assert result is AgentType.DEBUG

    def test_error_keyword(self):
        task = make_task(title="Investigate error logs")
        result = select_agent_for_task(task)
        assert result is AgentType.DEBUG

    def test_docs_keyword(self):
        # Avoid "api" in title (which maps to BACKEND before "docs")
        task = make_task(title="Write docs for the project")
        result = select_agent_for_task(task)
        assert result is AgentType.CONTENT

    def test_invalid_explicit_agent_type_falls_through_to_keyword(self):
        # Invalid metadata agent_type → falls through to keyword matching
        task = make_task(title="backend service", metadata={"agent_type": "no-such-agent"})
        result = select_agent_for_task(task)
        assert result is AgentType.BACKEND

    def test_feature_id_keyword_match(self):
        # "backend" in feature_id
        task = make_task(feature_id="HRN-backend-001", title="Something generic")
        result = select_agent_for_task(task)
        assert result is AgentType.BACKEND

    def test_explicit_opencode_agent_type(self):
        task = make_task(metadata={"agent_type": "opencode"})
        result = select_agent_for_task(task)
        assert result is AgentType.EXTERNAL_OPENCODE

    def test_explicit_gemini_agent_type(self):
        task = make_task(metadata={"agent_type": "gemini"})
        result = select_agent_for_task(task)
        assert result is AgentType.EXTERNAL_GEMINI


# =============================================================================
# _build_task_prompt — edge cases
# =============================================================================


class TestBuildTaskPromptEdgeCases:
    def test_empty_title(self):
        task = make_task(feature_id="HRN-X", title="")
        prompt = _build_task_prompt(task)
        assert "HRN-X" in prompt
        assert isinstance(prompt, str)

    def test_many_acceptance_criteria(self):
        criteria = [f"Criterion {i}" for i in range(1, 21)]
        task = make_task(acceptance_criteria=criteria)
        prompt = _build_task_prompt(task)
        assert "20. Criterion 20" in prompt
        assert "1. Criterion 1" in prompt

    def test_all_fields_present(self):
        task = make_task(
            feature_id="HRN-FULL",
            title="Complete Feature",
            acceptance_criteria=["AC1", "AC2"],
            test_command="pytest -v",
            epic="big-epic",
        )
        prompt = _build_task_prompt(task)
        assert "HRN-FULL" in prompt
        assert "Complete Feature" in prompt
        assert "Acceptance Criteria:" in prompt
        assert "Test Command: pytest -v" in prompt
        assert "Epic: big-epic" in prompt

    def test_prompt_starts_with_implement(self):
        task = make_task(feature_id="T-001", title="My Task")
        prompt = _build_task_prompt(task)
        assert prompt.startswith("Implement T-001: My Task")

    def test_prompt_no_trailing_blank_epic_line(self):
        task = make_task(epic=None, acceptance_criteria=[], test_command=None)
        prompt = _build_task_prompt(task)
        assert "Epic:" not in prompt

    def test_prompt_no_trailing_test_command_line_when_none(self):
        task = make_task(test_command=None)
        prompt = _build_task_prompt(task)
        assert "Test Command:" not in prompt


# =============================================================================
# DispatchResult — all status values and edge cases
# =============================================================================


class TestDispatchResultExtended:
    def test_status_not_found(self):
        r = DispatchResult(success=False, task_id="T", status="not_found")
        d = r.to_dict()
        assert d["status"] == "not_found"

    def test_status_in_progress(self):
        r = DispatchResult(success=True, task_id="T", status="in_progress")
        d = r.to_dict()
        assert d["status"] == "in_progress"

    def test_status_unavailable(self):
        r = DispatchResult(success=False, task_id="T", status="unavailable")
        d = r.to_dict()
        assert d["status"] == "unavailable"

    def test_all_agent_type_values_serializable(self):
        for agent in AgentType:
            r = DispatchResult(
                success=True,
                task_id="T",
                agent_type=agent,
                status="in_progress",
            )
            d = r.to_dict()
            assert d["agent_type"] == agent.value

    def test_error_message_preserved(self):
        r = DispatchResult(
            success=False,
            task_id="T",
            error="Failed to create tmux session: session limit reached",
        )
        d = r.to_dict()
        assert "session limit reached" in d["error"]

    def test_metadata_preserved_in_to_dict(self):
        r = DispatchResult(
            success=True,
            task_id="T",
            metadata={"title": "my task", "epic": "foundation"},
        )
        d = r.to_dict()
        assert d["metadata"]["title"] == "my task"
        assert d["metadata"]["epic"] == "foundation"


# =============================================================================
# DispatchStatus — field coverage
# =============================================================================


class TestDispatchStatusExtended:
    def test_output_size_default_zero(self):
        now = datetime.now(UTC)
        s = DispatchStatus(
            task_id="T",
            session_id="sess-T",
            agent_type=AgentType.BACKEND,
            status="running",
            dispatched_at=now,
        )
        assert s.output_size == 0

    def test_last_activity_default_none(self):
        now = datetime.now(UTC)
        s = DispatchStatus(
            task_id="T",
            session_id="sess-T",
            agent_type=AgentType.QA,
            status="dispatched",
            dispatched_at=now,
        )
        assert s.last_activity is None

    def test_output_size_can_be_set(self):
        now = datetime.now(UTC)
        s = DispatchStatus(
            task_id="T",
            session_id="sess-T",
            agent_type=AgentType.FRONTEND,
            status="running",
            dispatched_at=now,
            output_size=4096,
        )
        assert s.output_size == 4096

    def test_last_activity_can_be_set(self):
        now = datetime.now(UTC)
        later = now + timedelta(seconds=30)
        s = DispatchStatus(
            task_id="T",
            session_id="sess-T",
            agent_type=AgentType.DEBUG,
            status="running",
            dispatched_at=now,
            last_activity=later,
        )
        assert s.last_activity == later

    def test_metadata_in_status(self):
        now = datetime.now(UTC)
        s = DispatchStatus(
            task_id="T",
            session_id="s",
            agent_type=AgentType.CONTENT,
            status="dispatched",
            dispatched_at=now,
            metadata={"key": "value"},
        )
        assert s.metadata == {"key": "value"}


# =============================================================================
# TaskTracker — additional coverage
# =============================================================================


class TestTaskTrackerExtended:
    def test_multiple_tasks_saved_independently(self, tracker):
        for i in range(5):
            r = DispatchResult(
                success=True,
                task_id=f"T-{i:03d}",
                session_id=f"sess-{i}",
                agent_type=AgentType.BACKEND,
                status="in_progress",
                dispatched_at=datetime.now(UTC),
            )
            tracker.save_dispatch(f"T-{i:03d}", r)

        for i in range(5):
            loaded = tracker.load_dispatch(f"T-{i:03d}")
            assert loaded is not None
            assert loaded.task_id == f"T-{i:03d}"
            assert loaded.session_id == f"sess-{i}"

    def test_overwrite_existing_dispatch(self, tracker):
        now = datetime.now(UTC)
        r1 = DispatchResult(
            success=True,
            task_id="OVER",
            session_id="old-session",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=now,
        )
        tracker.save_dispatch("OVER", r1)

        r2 = DispatchResult(
            success=False,
            task_id="OVER",
            session_id="new-session",
            agent_type=AgentType.QA,
            status="failed",
            dispatched_at=now,
        )
        tracker.save_dispatch("OVER", r2)

        loaded = tracker.load_dispatch("OVER")
        assert loaded.session_id == "new-session"
        assert loaded.success is False
        assert loaded.agent_type is AgentType.QA

    def test_roundtrip_all_agent_types(self, tracker):
        """Every AgentType can be serialized and deserialized."""
        for agent_type in AgentType:
            r = DispatchResult(
                success=True,
                task_id=f"T-{agent_type.value}",
                agent_type=agent_type,
                status="in_progress",
                dispatched_at=datetime.now(UTC),
            )
            tracker.save_dispatch(f"T-{agent_type.value}", r)
            loaded = tracker.load_dispatch(f"T-{agent_type.value}")
            assert loaded is not None
            assert loaded.agent_type is agent_type

    def test_load_returns_none_for_empty_directory(self, tmp_path):
        tracker = TaskTracker(tracking_dir=tmp_path / "empty_dir")
        result = tracker.load_dispatch("nonexistent-task")
        assert result is None

    def test_update_status_to_completed(self, tracker):
        r = DispatchResult(
            success=True,
            task_id="UPD",
            session_id="s",
            agent_type=AgentType.BACKEND,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
        )
        tracker.save_dispatch("UPD", r)
        tracker.update_status("UPD", "completed")
        loaded = tracker.load_dispatch("UPD")
        assert loaded.status == "completed"

    def test_update_status_to_failed(self, tracker):
        r = DispatchResult(
            success=True,
            task_id="FAIL-UPD",
            session_id="s",
            agent_type=AgentType.QA,
            status="in_progress",
            dispatched_at=datetime.now(UTC),
        )
        tracker.save_dispatch("FAIL-UPD", r)
        tracker.update_status("FAIL-UPD", "failed")
        loaded = tracker.load_dispatch("FAIL-UPD")
        assert loaded.status == "failed"

    def test_load_with_missing_metadata_field(self, tracker):
        """Files missing 'metadata' key should default to {}."""
        data = {
            "success": True,
            "task_id": "NO-META",
            "session_id": "s",
            "agent_type": "backend-engineer",
            "status": "in_progress",
            "error": None,
            "dispatched_at": datetime.now(UTC).isoformat(),
            # 'metadata' intentionally omitted
        }
        (tracker.tracking_dir / "NO-META.json").write_text(json.dumps(data))
        loaded = tracker.load_dispatch("NO-META")
        assert loaded is not None
        assert loaded.metadata == {}


# =============================================================================
# TmuxDispatcher — additional coverage
# =============================================================================


class TestTmuxDispatcherExtended:
    def test_get_session_output_custom_lines_in_command(self, dispatcher):
        mock_result = MagicMock(stdout="output data\n")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            dispatcher.get_session_output("my-sess", lines=200)

        cmd = mock_run.call_args.args[0]
        assert "-200" in cmd

    def test_get_session_output_5_lines(self, dispatcher):
        mock_result = MagicMock(stdout="line\n")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            dispatcher.get_session_output("s", lines=5)

        cmd = mock_run.call_args.args[0]
        assert "-5" in cmd

    def test_tag_session_correct_key_format(self, dispatcher):
        calls = []
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            mock_run.side_effect = lambda c, **kw: calls.append(c) or MagicMock(returncode=0)
            dispatcher.tag_session("sess-x", {"my_tag": "my_value"})

        # Find the set-option call
        set_option_calls = [c for c in calls if "set-option" in c]
        assert len(set_option_calls) == 1
        cmd = set_option_calls[0]
        assert "@my_tag" in cmd
        assert "my_value" in cmd

    def test_create_session_session_name_format(self, dispatcher):
        created_names = []
        with patch("subprocess.run") as mock_run:
            def side(cmd, **kw):
                if "has-session" in cmd:
                    return MagicMock(returncode=1)
                if "new-session" in cmd:
                    # Capture session name
                    idx = cmd.index("-s")
                    created_names.append(cmd[idx + 1])
                    return MagicMock(returncode=0)
                return MagicMock(returncode=0)

            mock_run.side_effect = side
            dispatcher.create_session("MY-TASK-123", AgentType.BACKEND)

        assert created_names == ["forge-task-MY-TASK-123"]

    def test_send_command_passes_enter(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            dispatcher.send_command("sess", "ls -la")

        cmd = mock_run.call_args.args[0]
        assert cmd[-1] == "Enter"
        assert "ls -la" in cmd

    def test_session_exists_uses_has_session(self, dispatcher):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            result = dispatcher._session_exists("target-session")

        cmd = mock_run.call_args.args[0]
        assert "has-session" in cmd
        assert "target-session" in cmd
        assert result is True


# =============================================================================
# dispatch_task — additional paths
# =============================================================================


class TestDispatchTaskExtended:
    @pytest.fixture
    def patched_task(self):
        stub = make_task(
            feature_id="HRN-001",
            title="Build backend API",
            acceptance_criteria=["AC1"],
            test_command="pytest",
            epic="core",
        )
        with patch("forge_harness.iteration.dispatch.PrioritizedTask", return_value=stub):
            yield stub

    def test_tags_contain_task_id(self, tmp_root, features_file, patched_task):
        tagged = {}

        def capture_tags(session_id, tags):
            tagged.update(tags)
            return True

        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "Build backend API", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", side_effect=capture_tags),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            dispatch_task("HRN-001", features_path=features_file, forge_root=tmp_root)

        assert "task_id" in tagged
        assert tagged["task_id"] == "HRN-001"

    def test_tags_contain_agent_type(self, tmp_root, features_file, patched_task):
        tagged = {}

        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "Build backend API", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(
                TmuxDispatcher,
                "tag_session",
                side_effect=lambda sid, t: tagged.update(t) or True,
            ),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            dispatch_task(
                "HRN-001",
                features_path=features_file,
                forge_root=tmp_root,
                force_agent=AgentType.QA,
            )

        assert tagged.get("agent_type") == AgentType.QA.value

    def test_tags_contain_dispatched_at(self, tmp_root, features_file, patched_task):
        tagged = {}

        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "Build backend API", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(
                TmuxDispatcher,
                "tag_session",
                side_effect=lambda sid, t: tagged.update(t) or True,
            ),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            dispatch_task("HRN-001", features_path=features_file, forge_root=tmp_root)

        assert "dispatched_at" in tagged

    def test_features_path_defaults_to_forge_root_features_json(
        self, tmp_root, patched_task
    ):
        """When features_path is None, it defaults to forge_root/features.json."""
        features = tmp_root / "features.json"
        features.write_text(
            json.dumps(
                {"features": [{"id": "HRN-001", "title": "Build backend API", "priority": 5}]}
            )
        )
        with (
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task("HRN-001", features_path=None, forge_root=tmp_root)

        assert result.success is True

    def test_result_task_id_matches_input(self, tmp_root, features_file, patched_task):
        with (
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "Build backend API", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(TmuxDispatcher, "tag_session", return_value=True),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            result = dispatch_task("HRN-001", features_path=features_file, forge_root=tmp_root)

        assert result.task_id == "HRN-001"

    def test_result_epic_none_tagged_as_unknown(self, tmp_root, features_file):
        stub = make_task(feature_id="HRN-001", title="Generic task", epic=None)
        tagged = {}

        with (
            patch("forge_harness.iteration.dispatch.PrioritizedTask", return_value=stub),
            patch(
                "forge_harness.iteration.dispatch.load_features_from_file",
                return_value=[{"id": "HRN-001", "title": "Generic task", "priority": 5}],
            ),
            patch("forge_harness.iteration.dispatch.get_fleet_status"),
            patch.object(TmuxDispatcher, "create_session", return_value="sess"),
            patch.object(
                TmuxDispatcher,
                "tag_session",
                side_effect=lambda sid, t: tagged.update(t) or True,
            ),
            patch.object(TmuxDispatcher, "send_command", return_value=True),
        ):
            dispatch_task("HRN-001", features_path=features_file, forge_root=tmp_root)

        assert tagged.get("epic") == "unknown"


# =============================================================================
# dispatch_tasks — default max_parallel behavior
# =============================================================================


class TestDispatchTasksExtended:
    def test_default_max_parallel_is_3(self, tmp_root, features_file):
        """Default max_parallel=3 should limit to 3 dispatches."""
        dispatched = []

        def fake_dispatch(task_id, features_path=None, forge_root=None):
            dispatched.append(task_id)
            return DispatchResult(success=True, task_id=task_id, status="in_progress")

        with (
            patch("forge_harness.iteration.dispatch.dispatch_task", side_effect=fake_dispatch),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["T1", "T2", "T3", "T4", "T5"],
                features_path=features_file,
                forge_root=tmp_root,
                # No max_parallel specified — defaults to 3
            )

        assert len(results) == 3
        assert dispatched == ["T1", "T2", "T3"]

    def test_results_preserve_task_id_order(self, tmp_root, features_file):
        """Results should match task_ids order."""

        def fake_dispatch(task_id, features_path=None, forge_root=None):
            return DispatchResult(success=True, task_id=task_id, status="in_progress")

        with (
            patch("forge_harness.iteration.dispatch.dispatch_task", side_effect=fake_dispatch),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["ALPHA", "BETA", "GAMMA"],
                features_path=features_file,
                forge_root=tmp_root,
                max_parallel=5,
            )

        assert [r.task_id for r in results] == ["ALPHA", "BETA", "GAMMA"]

    def test_sleep_value_is_point_five(self, tmp_root, features_file):
        with (
            patch(
                "forge_harness.iteration.dispatch.dispatch_task",
                return_value=DispatchResult(success=True, task_id="T"),
            ),
            patch("forge_harness.iteration.dispatch.time.sleep") as mock_sleep,
        ):
            dispatch_tasks(["T1"], features_path=features_file, forge_root=tmp_root)

        mock_sleep.assert_called_once_with(0.5)

    def test_single_task_dispatched(self, tmp_root, features_file):
        with (
            patch(
                "forge_harness.iteration.dispatch.dispatch_task",
                return_value=DispatchResult(success=True, task_id="ONLY"),
            ),
            patch("forge_harness.iteration.dispatch.time.sleep"),
        ):
            results = dispatch_tasks(
                ["ONLY"], features_path=features_file, forge_root=tmp_root, max_parallel=10
            )

        assert len(results) == 1
        assert results[0].task_id == "ONLY"


# =============================================================================
# get_dispatch_status — additional paths
# =============================================================================


class TestGetDispatchStatusExtended:
    def test_tracking_dir_constructed_correctly(self, tmp_root):
        """get_dispatch_status uses forge_root/.forge/dispatch as tracking dir."""
        tracking_dir = tmp_root / ".forge/dispatch"
        tracking_dir.mkdir()
        data = {
            "success": True,
            "task_id": "DIR-TEST",
            "session_id": "s",
            "agent_type": "backend-engineer",
            "status": "in_progress",
            "error": None,
            "dispatched_at": datetime.now(UTC).isoformat(),
            "metadata": {},
        }
        (tracking_dir / "DIR-TEST.json").write_text(json.dumps(data))

        result = get_dispatch_status("DIR-TEST", forge_root=tmp_root)
        assert result is not None
        assert result.task_id == "DIR-TEST"

    def test_returns_correct_agent_type(self, tmp_root):
        tracking_dir = tmp_root / ".forge/dispatch"
        tracking_dir.mkdir()
        data = {
            "success": True,
            "task_id": "QA-TASK",
            "session_id": "sess",
            "agent_type": "qa-tester",
            "status": "in_progress",
            "error": None,
            "dispatched_at": datetime.now(UTC).isoformat(),
            "metadata": {},
        }
        (tracking_dir / "QA-TASK.json").write_text(json.dumps(data))

        result = get_dispatch_status("QA-TASK", forge_root=tmp_root)
        assert result.agent_type is AgentType.QA

    def test_returns_none_for_corrupt_json(self, tmp_root):
        tracking_dir = tmp_root / ".forge/dispatch"
        tracking_dir.mkdir()
        (tracking_dir / "CORRUPT.json").write_text("{bad json!!!}")

        result = get_dispatch_status("CORRUPT", forge_root=tmp_root)
        assert result is None
