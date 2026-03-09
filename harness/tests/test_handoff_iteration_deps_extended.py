"""Extended tests for handoff schema/manager, demo_aggregator, and dependencies gaps.

Target modules (closing coverage gaps):
- forge_harness/handoff/schema.py  (from_json, to_prompt branches)
- forge_harness/handoff/manager.py (None-return branches, wrong-agent raises, get_stats, create_handoff_manager)
- forge_harness/iteration/demo_aggregator.py  (all 5 demo functions + main)
- forge_harness/webhook_server/core/dependencies.py  (already 100%, defensive regression tests)

Run:
    uv run pytest tests/test_handoff_iteration_deps_extended.py -x --tb=short -q
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.handoff.manager import (
    _PRIORITY_ORDER,
    HandoffManager,
    create_handoff_manager,
)

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from forge_harness.handoff.schema import (
    Handoff,
    HandoffPriority,
    HandoffStatus,
)
from forge_harness.iteration.aggregator import (
    AgentResult,
    ResultStatus,
)

# ===========================================================================
# Section 1: forge_harness/handoff/schema.py
# ===========================================================================


class TestHandoffFromJson:
    """Tests for Handoff.from_json (line 133-134)."""

    def test_from_json_roundtrip(self):
        """to_json then from_json produces identical handoff."""
        original = Handoff.new(
            from_agent="alpha",
            to_agent="beta",
            task="Implement feature X",
            priority=HandoffPriority.HIGH,
            description="Detailed description",
        )
        json_str = original.to_json()
        restored = Handoff.from_json(json_str)

        assert restored.id == original.id
        assert restored.from_agent == original.from_agent
        assert restored.to_agent == original.to_agent
        assert restored.task == original.task
        assert restored.priority == original.priority
        assert restored.description == original.description
        assert restored.status == HandoffStatus.PENDING

    def test_from_json_preserves_enum_values(self):
        """from_json correctly converts string status/priority back to enums."""
        handoff = Handoff.new(from_agent="a", to_agent="b", task="t")
        handoff.status = HandoffStatus.COMPLETED
        handoff.priority = HandoffPriority.CRITICAL
        json_str = handoff.to_json()
        restored = Handoff.from_json(json_str)
        assert restored.status == HandoffStatus.COMPLETED
        assert restored.priority == HandoffPriority.CRITICAL

    def test_from_json_with_all_optional_fields(self):
        """from_json handles all optional fields correctly."""
        handoff = Handoff.new(
            from_agent="a",
            to_agent="b",
            task="complex task",
            files=["file1.py", "file2.py"],
            context={"key": "value", "count": 42},
            test_command="pytest tests/ -q",
            acceptance_criteria=["criterion 1", "criterion 2"],
            domain="my-domain",
            project="my-project",
            notes="Some notes here",
        )
        json_str = handoff.to_json()
        restored = Handoff.from_json(json_str)
        assert restored.files == ["file1.py", "file2.py"]
        assert restored.context == {"key": "value", "count": 42}
        assert restored.test_command == "pytest tests/ -q"
        assert restored.acceptance_criteria == ["criterion 1", "criterion 2"]
        assert restored.domain == "my-domain"
        assert restored.project == "my-project"
        assert restored.notes == "Some notes here"

    def test_from_json_minimal_dict(self):
        """from_json works with minimal JSON (only required fields)."""
        minimal = json.dumps({
            "id": "abc12345",
            "from_agent": "agent-a",
            "to_agent": "agent-b",
            "task": "do something",
        })
        handoff = Handoff.from_json(minimal)
        assert handoff.id == "abc12345"
        assert handoff.status == HandoffStatus.PENDING
        assert handoff.priority == HandoffPriority.MEDIUM

    def test_from_json_accepted_at_preserved(self):
        """from_json preserves accepted_at field."""
        ts = "2026-02-22T10:00:00+00:00"
        data = json.dumps({
            "id": "xyz",
            "from_agent": "a",
            "to_agent": "b",
            "task": "t",
            "status": "accepted",
            "priority": "high",
            "accepted_at": ts,
        })
        handoff = Handoff.from_json(data)
        assert handoff.accepted_at == ts
        assert handoff.status == HandoffStatus.ACCEPTED


class TestHandoffToPrompt:
    """Tests for Handoff.to_prompt (lines 136-179)."""

    def _minimal_handoff(self) -> Handoff:
        return Handoff.new(
            from_agent="forge-orchestrator",
            to_agent="backend-agent",
            task="Fix the auth bug",
        )

    def test_to_prompt_contains_header(self):
        """to_prompt always starts with the from_agent header."""
        h = self._minimal_handoff()
        prompt = h.to_prompt()
        assert "# Handoff from forge-orchestrator" in prompt

    def test_to_prompt_contains_task(self):
        h = self._minimal_handoff()
        prompt = h.to_prompt()
        assert "Fix the auth bug" in prompt

    def test_to_prompt_contains_priority(self):
        h = self._minimal_handoff()
        h.priority = HandoffPriority.HIGH
        prompt = h.to_prompt()
        assert "high" in prompt

    def test_to_prompt_contains_handoff_id(self):
        h = self._minimal_handoff()
        prompt = h.to_prompt()
        assert h.id in prompt

    def test_to_prompt_contains_accept_reject_commands(self):
        h = self._minimal_handoff()
        prompt = h.to_prompt()
        assert f"forge-harness handoff accept {h.id}" in prompt
        assert f"forge-harness handoff reject {h.id}" in prompt

    def test_to_prompt_no_description_section_when_empty(self):
        h = self._minimal_handoff()
        h.description = ""
        prompt = h.to_prompt()
        assert "## Description" not in prompt

    def test_to_prompt_description_section_when_present(self):
        """Lines 148-151: description block rendered when non-empty."""
        h = self._minimal_handoff()
        h.description = "This is an important task."
        prompt = h.to_prompt()
        assert "## Description" in prompt
        assert "This is an important task." in prompt

    def test_to_prompt_no_files_section_when_empty(self):
        h = self._minimal_handoff()
        h.files = []
        prompt = h.to_prompt()
        assert "## Files to Review" not in prompt

    def test_to_prompt_files_section_rendered(self):
        """Lines 153-157: files block rendered when files list is non-empty."""
        h = self._minimal_handoff()
        h.files = ["src/auth.py", "tests/test_auth.py"]
        prompt = h.to_prompt()
        assert "## Files to Review" in prompt
        assert "src/auth.py" in prompt
        assert "tests/test_auth.py" in prompt

    def test_to_prompt_no_context_section_when_empty(self):
        h = self._minimal_handoff()
        h.context = {}
        prompt = h.to_prompt()
        assert "## Context" not in prompt

    def test_to_prompt_context_section_rendered(self):
        """Lines 159-164: context block rendered when context dict is non-empty."""
        h = self._minimal_handoff()
        h.context = {"branch": "feat/auth-fix", "pr": 42}
        prompt = h.to_prompt()
        assert "## Context" in prompt
        assert "```json" in prompt
        assert "branch" in prompt

    def test_to_prompt_no_test_command_section_when_none(self):
        """Lines 166-171: test_command block skipped when None."""
        h = self._minimal_handoff()
        h.test_command = None
        prompt = h.to_prompt()
        assert "## Test Command" not in prompt

    def test_to_prompt_test_command_section_rendered(self):
        """Lines 166-171: test_command block rendered when set."""
        h = self._minimal_handoff()
        h.test_command = "uv run pytest tests/ -q"
        prompt = h.to_prompt()
        assert "## Test Command" in prompt
        assert "```bash" in prompt
        assert "uv run pytest tests/ -q" in prompt

    def test_to_prompt_no_acceptance_criteria_when_empty(self):
        """Lines 173-177: acceptance criteria skipped when list is empty."""
        h = self._minimal_handoff()
        h.acceptance_criteria = []
        prompt = h.to_prompt()
        assert "## Acceptance Criteria" not in prompt

    def test_to_prompt_acceptance_criteria_rendered(self):
        """Lines 173-177: acceptance criteria block rendered when non-empty."""
        h = self._minimal_handoff()
        h.acceptance_criteria = ["All tests pass", "No ruff errors", "Coverage > 80%"]
        prompt = h.to_prompt()
        assert "## Acceptance Criteria" in prompt
        assert "- [ ] All tests pass" in prompt
        assert "- [ ] No ruff errors" in prompt
        assert "- [ ] Coverage > 80%" in prompt

    def test_to_prompt_all_sections_rendered_together(self):
        """All optional sections appear when all fields are populated."""
        h = Handoff.new(
            from_agent="prya",
            to_agent="nova",
            task="Complete sprint 6 features",
            description="Deliver all sprint 6 items",
            files=["sprint6/auth.py"],
            context={"sprint": 6},
            test_command="pytest tests/ -v",
            acceptance_criteria=["Deploy succeeds", "Tests pass"],
            priority=HandoffPriority.CRITICAL,
        )
        prompt = h.to_prompt()
        assert "## Description" in prompt
        assert "## Files to Review" in prompt
        assert "## Context" in prompt
        assert "## Test Command" in prompt
        assert "## Acceptance Criteria" in prompt

    def test_to_prompt_returns_string(self):
        h = self._minimal_handoff()
        result = h.to_prompt()
        assert isinstance(result, str)
        assert len(result) > 0


# ===========================================================================
# Section 2: forge_harness/handoff/manager.py
# ===========================================================================


@pytest.fixture
def handoff_dir(tmp_path):
    return tmp_path / "handoffs"


@pytest.fixture
def manager(handoff_dir):
    return HandoffManager(handoff_dir)


@pytest.mark.asyncio
class TestHandoffManagerNoneReturns:
    """Tests for None-return branches (lines 41, 88, 104, 116, 132)."""

    async def test_get_nonexistent_returns_none(self, manager):
        """get() returns None when file does not exist (line 41)."""
        result = await manager.get("nonexistent-id")
        assert result is None

    async def test_accept_nonexistent_returns_none(self, manager):
        """accept() returns None when handoff not found (line 88)."""
        result = await manager.accept("no-such-id", "agent")
        assert result is None

    async def test_reject_nonexistent_returns_none(self, manager):
        """reject() returns None when handoff not found (line 104)."""
        result = await manager.reject("no-such-id", "agent")
        assert result is None

    async def test_start_nonexistent_returns_none(self, manager):
        """start() returns None when handoff not found (line 116)."""
        result = await manager.start("no-such-id")
        assert result is None

    async def test_complete_nonexistent_returns_none(self, manager):
        """complete() returns None when handoff not found (line 132)."""
        result = await manager.complete("no-such-id")
        assert result is None


@pytest.mark.asyncio
class TestHandoffManagerWrongAgent:
    """Tests for wrong-agent ValueError raises (lines 89-90, 105-106)."""

    async def test_accept_wrong_agent_raises_value_error(self, manager):
        """accept() raises ValueError when to_agent doesn't match (line 90)."""
        handoff = await manager.create(from_agent="a", to_agent="agent-x", task="task")
        with pytest.raises(ValueError, match="handoff not addressed to this agent"):
            await manager.accept(handoff.id, "wrong-agent")

    async def test_accept_not_pending_raises_value_error(self, manager):
        """accept() raises ValueError when status is not PENDING (line 92)."""
        handoff = await manager.create(from_agent="a", to_agent="agent-x", task="task")
        # Accept once to change status to ACCEPTED
        await manager.accept(handoff.id, "agent-x")
        # Trying to accept again when status is ACCEPTED should fail
        with pytest.raises(ValueError, match="handoff not pending"):
            await manager.accept(handoff.id, "agent-x")

    async def test_reject_wrong_agent_raises_value_error(self, manager):
        """reject() raises ValueError when to_agent doesn't match (line 106)."""
        handoff = await manager.create(from_agent="a", to_agent="agent-x", task="task")
        with pytest.raises(ValueError, match="handoff not addressed to this agent"):
            await manager.reject(handoff.id, "wrong-agent")

    async def test_accept_no_to_agent_allows_any_agent(self, manager):
        """accept() succeeds when to_agent is empty/None (broadcast handoff)."""
        handoff = await manager.create(from_agent="a", to_agent="", task="broadcast task")
        result = await manager.accept(handoff.id, "any-agent")
        assert result is not None
        assert result.status == HandoffStatus.ACCEPTED
        assert result.to_agent == "any-agent"

    async def test_reject_no_to_agent_allows_any_agent(self, manager):
        """reject() succeeds when to_agent is empty/None."""
        handoff = await manager.create(from_agent="a", to_agent="", task="broadcast task")
        result = await manager.reject(handoff.id, "any-agent", reason="Not my task")
        assert result is not None
        assert result.status == HandoffStatus.REJECTED
        assert result.notes == "Not my task"


@pytest.mark.asyncio
class TestHandoffManagerStart:
    """Tests for start() non-ACCEPTED branch (line 118)."""

    async def test_start_pending_handoff_returns_unchanged(self, manager):
        """start() on PENDING handoff returns handoff without changing status."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        # PENDING != ACCEPTED, so start returns the handoff unchanged
        result = await manager.start(handoff.id)
        assert result is not None
        assert result.status == HandoffStatus.PENDING

    async def test_start_accepted_handoff_advances_to_in_progress(self, manager):
        """start() on ACCEPTED handoff moves it to IN_PROGRESS."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        await manager.accept(handoff.id, "b")
        result = await manager.start(handoff.id)
        assert result is not None
        assert result.status == HandoffStatus.IN_PROGRESS

    async def test_start_completed_handoff_returns_unchanged(self, manager):
        """start() on COMPLETED handoff returns it without changing status."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        await manager.accept(handoff.id, "b")
        await manager.start(handoff.id)
        await manager.complete(handoff.id)
        # Now COMPLETED, start should return without change
        result = await manager.start(handoff.id)
        assert result is not None
        assert result.status == HandoffStatus.COMPLETED


@pytest.mark.asyncio
class TestHandoffManagerComplete:
    """Tests for complete() optional branches (lines 135-138)."""

    async def test_complete_with_result_and_notes(self, manager):
        """complete() sets result and notes when provided."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        result = await manager.complete(
            handoff.id,
            result={"output": "done", "tests_passed": 42},
            notes="Completed without issues",
        )
        assert result is not None
        assert result.status == HandoffStatus.COMPLETED
        assert result.result == {"output": "done", "tests_passed": 42}
        assert result.notes == "Completed without issues"
        assert result.completed_at is not None

    async def test_complete_without_result_or_notes(self, manager):
        """complete() works when result and notes are omitted."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        result = await manager.complete(handoff.id)
        assert result is not None
        assert result.status == HandoffStatus.COMPLETED
        assert result.result is None
        assert result.notes is None

    async def test_complete_with_only_notes(self, manager):
        """complete() sets notes but not result."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        result = await manager.complete(handoff.id, notes="Work done")
        assert result.notes == "Work done"
        assert result.result is None

    async def test_complete_with_only_result(self, manager):
        """complete() sets result but not notes."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        result = await manager.complete(handoff.id, result={"files_changed": 3})
        assert result.result == {"files_changed": 3}
        assert result.notes is None


@pytest.mark.asyncio
class TestHandoffManagerGetStats:
    """Tests for get_stats() including empty dir and completion time calc (lines 142-178)."""

    async def test_get_stats_empty_dir(self, manager):
        """get_stats() on empty directory returns zeros (line 145-150)."""
        stats = await manager.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["by_priority"] == {}
        assert stats["avg_completion_time_hours"] == 0

    async def test_get_stats_counts_handoffs(self, manager):
        """get_stats() reports correct total count."""
        await manager.create(from_agent="a", to_agent="b", task="t1")
        await manager.create(from_agent="a", to_agent="c", task="t2")
        stats = await manager.get_stats()
        assert stats["total"] == 2

    async def test_get_stats_by_status(self, manager):
        """get_stats() correctly counts by status."""
        h1 = await manager.create(from_agent="a", to_agent="b", task="t1")
        h2 = await manager.create(from_agent="a", to_agent="c", task="t2")
        await manager.accept(h1.id, "b")
        # h1 is ACCEPTED, h2 is PENDING
        stats = await manager.get_stats()
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["accepted"] == 1

    async def test_get_stats_by_priority(self, manager):
        """get_stats() correctly counts by priority."""
        await manager.create(from_agent="a", to_agent="b", task="t1", priority=HandoffPriority.HIGH)
        await manager.create(from_agent="a", to_agent="b", task="t2", priority=HandoffPriority.HIGH)
        await manager.create(from_agent="a", to_agent="b", task="t3", priority=HandoffPriority.LOW)
        stats = await manager.get_stats()
        assert stats["by_priority"]["high"] == 2
        assert stats["by_priority"]["low"] == 1

    async def test_get_stats_avg_completion_time(self, manager):
        """get_stats() computes avg completion time for completed handoffs (lines 162-166)."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        # Manually set created_at and completed_at to control the duration
        # 1 hour apart
        created = datetime(2026, 2, 22, 10, 0, 0, tzinfo=UTC)
        completed = datetime(2026, 2, 22, 11, 0, 0, tzinfo=UTC)
        handoff.created_at = created.isoformat()
        handoff.completed_at = completed.isoformat()
        handoff.status = HandoffStatus.COMPLETED
        await manager.update(handoff)

        stats = await manager.get_stats()
        assert stats["avg_completion_time_hours"] == pytest.approx(1.0, abs=0.01)

    async def test_get_stats_no_completion_time_for_pending(self, manager):
        """get_stats() skips completion time for handoffs without completed_at."""
        await manager.create(from_agent="a", to_agent="b", task="task")
        stats = await manager.get_stats()
        # No completed_at → avg should be 0
        assert stats["avg_completion_time_hours"] == 0

    async def test_get_stats_invalid_datetime_handled_gracefully(self, manager):
        """get_stats() skips malformed datetime strings (ValueError catch line 167)."""
        handoff = await manager.create(from_agent="a", to_agent="b", task="task")
        handoff.completed_at = "not-a-datetime"
        handoff.created_at = "also-not-a-datetime"
        handoff.status = HandoffStatus.COMPLETED
        await manager.update(handoff)
        # Should not raise
        stats = await manager.get_stats()
        assert stats["avg_completion_time_hours"] == 0


@pytest.mark.asyncio
class TestHandoffManagerListForAgent:
    """Tests for list_for_agent with as_sender and status filters."""

    async def test_list_for_agent_as_sender(self, manager):
        """list_for_agent with as_sender=True filters by from_agent."""
        await manager.create(from_agent="sender", to_agent="receiver", task="t1")
        await manager.create(from_agent="other", to_agent="receiver", task="t2")
        handoffs = await manager.list_for_agent("sender", as_sender=True)
        assert len(handoffs) == 1
        assert handoffs[0].from_agent == "sender"

    async def test_list_for_agent_status_filter(self, manager):
        """list_for_agent filters by status when provided."""
        h1 = await manager.create(from_agent="a", to_agent="target", task="t1")
        await manager.create(from_agent="b", to_agent="target", task="t2")
        await manager.accept(h1.id, "target")

        pending_only = await manager.list_for_agent("target", status=HandoffStatus.PENDING)
        assert len(pending_only) == 1
        assert pending_only[0].status == HandoffStatus.PENDING

    async def test_list_for_agent_sorted_by_priority(self, manager):
        """list_for_agent returns handoffs sorted by priority (critical first)."""
        await manager.create(from_agent="a", to_agent="t", task="low", priority=HandoffPriority.LOW)
        await manager.create(from_agent="a", to_agent="t", task="critical", priority=HandoffPriority.CRITICAL)
        await manager.create(from_agent="a", to_agent="t", task="medium", priority=HandoffPriority.MEDIUM)
        handoffs = await manager.list_for_agent("t")
        priorities = [h.priority for h in handoffs]
        assert priorities[0] == HandoffPriority.CRITICAL

    async def test_list_pending_sorted_by_priority(self, manager):
        """list_pending returns only PENDING handoffs sorted by priority."""
        h1 = await manager.create(from_agent="a", to_agent="b", task="t1", priority=HandoffPriority.LOW)
        await manager.create(from_agent="a", to_agent="c", task="t2", priority=HandoffPriority.CRITICAL)
        # Accept h1 so it becomes non-pending
        await manager.accept(h1.id, "b")
        pending = await manager.list_pending()
        assert len(pending) == 1
        assert pending[0].priority == HandoffPriority.CRITICAL

    async def test_list_for_agent_excludes_wrong_receiver(self, manager):
        """list_for_agent(agent) skips handoffs addressed to a different agent (line 67 coverage)."""
        # Create handoff addressed to "agent-a" and another to "agent-b"
        await manager.create(from_agent="sender", to_agent="agent-a", task="for a")
        await manager.create(from_agent="sender", to_agent="agent-b", task="for b")
        await manager.create(from_agent="sender", to_agent="agent-a", task="also for a")

        handoffs_for_a = await manager.list_for_agent("agent-a")
        assert len(handoffs_for_a) == 2
        assert all(h.to_agent == "agent-a" for h in handoffs_for_a)

        handoffs_for_b = await manager.list_for_agent("agent-b")
        assert len(handoffs_for_b) == 1
        assert handoffs_for_b[0].to_agent == "agent-b"


class TestCreateHandoffManager:
    """Tests for create_handoff_manager factory (lines 181-186)."""

    def test_creates_manager_with_forge_root_env(self, tmp_path, monkeypatch):
        """create_handoff_manager uses FORGE_ROOT env var when set (line 184)."""
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        manager = create_handoff_manager()
        assert isinstance(manager, HandoffManager)
        expected_dir = tmp_path / ".forge/handoffs"
        assert manager.handoff_dir == expected_dir
        assert expected_dir.exists()

    def test_creates_manager_with_explicit_root(self, tmp_path):
        """create_handoff_manager uses provided forge_root."""
        manager = create_handoff_manager(forge_root=tmp_path)
        assert isinstance(manager, HandoffManager)
        assert manager.handoff_dir == tmp_path / ".forge/handoffs"

    def test_creates_manager_with_no_root_uses_cwd(self, monkeypatch, tmp_path):
        """create_handoff_manager falls back to cwd when FORGE_ROOT not set (line 184)."""
        monkeypatch.delenv("FORGE_ROOT", raising=False)
        # Patch Path.cwd() to return tmp_path so we don't pollute the real cwd
        with patch("forge_harness.handoff.manager.Path.cwd", return_value=tmp_path):
            manager = create_handoff_manager()
        assert isinstance(manager, HandoffManager)

    def test_priority_order_constant(self):
        """_PRIORITY_ORDER maps priorities correctly."""
        assert _PRIORITY_ORDER[HandoffPriority.CRITICAL] == 0
        assert _PRIORITY_ORDER[HandoffPriority.HIGH] == 1
        assert _PRIORITY_ORDER[HandoffPriority.MEDIUM] == 2
        assert _PRIORITY_ORDER[HandoffPriority.LOW] == 3


# ===========================================================================
# Section 3: forge_harness/iteration/demo_aggregator.py
# ===========================================================================


class TestDemoSuccessfulAggregation:
    """Tests for demo_successful_aggregation() function."""

    def test_runs_without_error(self, capsys):
        """demo_successful_aggregation runs and prints output."""
        from forge_harness.iteration.demo_aggregator import demo_successful_aggregation
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "DEMO 1" in captured.out
        assert "Successful Aggregation" in captured.out

    def test_prints_success_rate(self, capsys):
        """Output contains success rate."""
        from forge_harness.iteration.demo_aggregator import demo_successful_aggregation
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "Success Rate" in captured.out

    def test_prints_common_findings(self, capsys):
        """Output contains common findings count."""
        from forge_harness.iteration.demo_aggregator import demo_successful_aggregation
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "Common Findings" in captured.out

    def test_prints_task_id(self, capsys):
        """Output contains task ID."""
        from forge_harness.iteration.demo_aggregator import demo_successful_aggregation
        demo_successful_aggregation()
        captured = capsys.readouterr()
        assert "HRN-005" in captured.out

    def test_prints_markdown_report(self, capsys):
        """Output contains markdown report."""
        from forge_harness.iteration.demo_aggregator import demo_successful_aggregation
        demo_successful_aggregation()
        captured = capsys.readouterr()
        # to_markdown() produces a heading
        assert "# Aggregated Report" in captured.out


class TestDemoPartialFailure:
    """Tests for demo_partial_failure() function."""

    def test_runs_without_error(self, capsys):
        """demo_partial_failure runs and prints output."""
        from forge_harness.iteration.demo_aggregator import demo_partial_failure
        demo_partial_failure()
        captured = capsys.readouterr()
        assert "DEMO 2" in captured.out

    def test_prints_agent_counts(self, capsys):
        """Output contains agent counts."""
        from forge_harness.iteration.demo_aggregator import demo_partial_failure
        demo_partial_failure()
        captured = capsys.readouterr()
        assert "Total Agents" in captured.out
        assert "Successful" in captured.out
        assert "Failed" in captured.out
        assert "Partial" in captured.out

    def test_prints_errors_collected(self, capsys):
        """Output includes error count."""
        from forge_harness.iteration.demo_aggregator import demo_partial_failure
        demo_partial_failure()
        captured = capsys.readouterr()
        assert "Errors Collected" in captured.out

    def test_shows_partial_agents(self, capsys):
        """Output correctly shows partial agent count."""
        from forge_harness.iteration.demo_aggregator import demo_partial_failure
        demo_partial_failure()
        captured = capsys.readouterr()
        # 1 SUCCESS, 1 FAILURE, 1 PARTIAL → Partial: 1
        assert "Partial: 1" in captured.out

    def test_shows_success_rate_below_100(self, capsys):
        """Success rate should not be 100% for partial failure scenario."""
        from forge_harness.iteration.demo_aggregator import demo_partial_failure
        demo_partial_failure()
        captured = capsys.readouterr()
        # 1 of 3 agents succeeded → 33.3%
        assert "33.3%" in captured.out


class TestDemoConflictDetection:
    """Tests for demo_conflict_detection() function."""

    def test_runs_without_error(self, capsys):
        """demo_conflict_detection runs and prints output."""
        from forge_harness.iteration.demo_aggregator import demo_conflict_detection
        demo_conflict_detection()
        captured = capsys.readouterr()
        assert "DEMO 3" in captured.out

    def test_prints_conflicts_detected(self, capsys):
        """Output contains conflicts detected count."""
        from forge_harness.iteration.demo_aggregator import demo_conflict_detection
        demo_conflict_detection()
        captured = capsys.readouterr()
        assert "Conflicts Detected" in captured.out

    def test_prints_conflict_severity(self, capsys):
        """Output contains conflict severity information."""
        from forge_harness.iteration.demo_aggregator import demo_conflict_detection
        demo_conflict_detection()
        captured = capsys.readouterr()
        # There should be at least one conflict (SUCCESS vs FAILURE)
        assert "Severity" in captured.out

    def test_prints_conflict_description(self, capsys):
        """Output contains conflict description."""
        from forge_harness.iteration.demo_aggregator import demo_conflict_detection
        demo_conflict_detection()
        captured = capsys.readouterr()
        assert "Description" in captured.out

    def test_prints_conflict_agents(self, capsys):
        """Output contains agent IDs involved in conflict."""
        from forge_harness.iteration.demo_aggregator import demo_conflict_detection
        demo_conflict_detection()
        captured = capsys.readouterr()
        assert "Agents" in captured.out

    def test_conflict_details_shown(self, capsys):
        """Conflict details are printed when present."""
        from forge_harness.iteration.demo_aggregator import demo_conflict_detection
        demo_conflict_detection()
        captured = capsys.readouterr()
        # coverage: coverage has different values (95.0 vs 60.0) → metadata conflict
        assert "Details" in captured.out


class TestDemoDeduplication:
    """Tests for demo_deduplication() function."""

    def test_runs_without_error(self, capsys):
        """demo_deduplication runs and prints output."""
        from forge_harness.iteration.demo_aggregator import demo_deduplication
        demo_deduplication()
        captured = capsys.readouterr()
        assert "DEMO 4" in captured.out

    def test_tests_multiple_thresholds(self, capsys):
        """Output contains all three similarity thresholds."""
        from forge_harness.iteration.demo_aggregator import demo_deduplication
        demo_deduplication()
        captured = capsys.readouterr()
        assert "0.6" in captured.out
        assert "0.75" in captured.out
        assert "0.9" in captured.out

    def test_prints_common_and_unique_findings(self, capsys):
        """Output shows both common and unique findings."""
        from forge_harness.iteration.demo_aggregator import demo_deduplication
        demo_deduplication()
        captured = capsys.readouterr()
        assert "Common Findings" in captured.out
        assert "Unique Findings" in captured.out

    def test_prints_similarity_scores(self, capsys):
        """Similarity scores are shown for common findings."""
        from forge_harness.iteration.demo_aggregator import demo_deduplication
        demo_deduplication()
        captured = capsys.readouterr()
        assert "Similarity" in captured.out

    def test_prints_agent_count_per_finding(self, capsys):
        """Agent count per finding is shown."""
        from forge_harness.iteration.demo_aggregator import demo_deduplication
        demo_deduplication()
        captured = capsys.readouterr()
        # e.g. "(3 agents)" or "(2 agents)"
        assert "agents" in captured.out


class TestDemoMultipleAgentsParallel:
    """Tests for demo_multiple_agents_parallel() function."""

    def test_runs_without_error(self, capsys):
        """demo_multiple_agents_parallel runs and prints output."""
        from forge_harness.iteration.demo_aggregator import demo_multiple_agents_parallel
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "DEMO 5" in captured.out

    def test_prints_10_agents(self, capsys):
        """Output shows total of 10 agents."""
        from forge_harness.iteration.demo_aggregator import demo_multiple_agents_parallel
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Total Agents: 10" in captured.out

    def test_prints_correct_success_failure_counts(self, capsys):
        """Output shows 8 successful and 2 failed agents."""
        from forge_harness.iteration.demo_aggregator import demo_multiple_agents_parallel
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Successful: 8" in captured.out
        assert "Failed: 2" in captured.out

    def test_prints_success_rate(self, capsys):
        """Output shows 80% success rate (8/10)."""
        from forge_harness.iteration.demo_aggregator import demo_multiple_agents_parallel
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "80.0%" in captured.out

    def test_prints_top_common_findings(self, capsys):
        """Output shows top common findings section."""
        from forge_harness.iteration.demo_aggregator import demo_multiple_agents_parallel
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Top Common Findings" in captured.out

    def test_prints_reported_by_count(self, capsys):
        """Each top finding shows how many agents reported it."""
        from forge_harness.iteration.demo_aggregator import demo_multiple_agents_parallel
        demo_multiple_agents_parallel()
        captured = capsys.readouterr()
        assert "Reported by" in captured.out
        assert "agents" in captured.out


class TestDemoMain:
    """Tests for the main() entry point."""

    def test_main_runs_all_demos(self, capsys):
        """main() executes all 5 demos without error."""
        from forge_harness.iteration.demo_aggregator import main
        main()
        captured = capsys.readouterr()
        assert "DEMO 1" in captured.out
        assert "DEMO 2" in captured.out
        assert "DEMO 3" in captured.out
        assert "DEMO 4" in captured.out
        assert "DEMO 5" in captured.out

    def test_main_prints_completion_banner(self, capsys):
        """main() prints the completion banner."""
        from forge_harness.iteration.demo_aggregator import main
        main()
        captured = capsys.readouterr()
        assert "All demos completed successfully" in captured.out

    def test_main_prints_header(self, capsys):
        """main() prints the ResultAggregator Demo header."""
        from forge_harness.iteration.demo_aggregator import main
        main()
        captured = capsys.readouterr()
        assert "ResultAggregator Demo" in captured.out

    def test_main_logs_error_on_exception(self):
        """main() catches exceptions, logs them, and re-raises."""
        from forge_harness.iteration.demo_aggregator import main
        with patch(
            "forge_harness.iteration.demo_aggregator.demo_successful_aggregation",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                main()

    def test_main_can_be_called_multiple_times(self, capsys):
        """main() is idempotent — can be called multiple times safely."""
        from forge_harness.iteration.demo_aggregator import main
        main()
        main()
        captured = capsys.readouterr()
        # Both calls should have completed
        assert captured.out.count("All demos completed successfully") == 2


# ===========================================================================
# Section 4: Regression/defensive tests for dependencies.py (100% already)
# ===========================================================================


class TestDependenciesRegression:
    """Lightweight regression tests to guard against regressions on dependencies.py."""

    @pytest.fixture(autouse=True)
    def reset_auth_config(self):
        import forge_harness.webhook_server.core.dependencies as dep
        dep._auth_config = None
        yield
        dep._auth_config = None

    def test_get_auth_config_returns_expected_structure(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        assert isinstance(cfg, dict)
        required_keys = {
            "jwt_secret", "jwt_algorithm", "token_expire_minutes",
            "allow_localhost", "required_permissions", "forge_user_class",
        }
        assert required_keys.issubset(cfg.keys())

    def test_forge_user_dataclass_has_required_fields(self):
        import dataclasses

        from forge_harness.webhook_server.core.dependencies import get_auth_config
        ForgeUser = get_auth_config()["forge_user_class"]
        field_names = {f.name for f in dataclasses.fields(ForgeUser)}
        assert "user_id" in field_names
        assert "permissions" in field_names
        assert "authenticated" in field_names
        assert "token_hash" in field_names
        assert "client_ip" in field_names
        assert "timestamp" in field_names

    @pytest.mark.asyncio
    async def test_check_rate_limit_is_noop(self):
        from forge_harness.webhook_server.core.dependencies import check_rate_limit
        req = MagicMock()
        result = await check_rate_limit(req)
        assert result is None

    def test_require_permission_returns_callable(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        fn = require_permission("read")
        assert callable(fn)

    def test_require_jwt_permission_returns_callable(self):
        from forge_harness.webhook_server.core.dependencies import require_jwt_permission
        fn = require_jwt_permission("write")
        assert callable(fn)

    @pytest.mark.asyncio
    async def test_require_permission_admin_passes_all(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker = require_permission("anything")
        user = {"sub": "x", "permissions": ["admin"]}
        result = await checker(user)
        assert result is user

    @pytest.mark.asyncio
    async def test_require_jwt_permission_admin_not_bypassed(self):
        """require_jwt_permission does NOT give admin bypass."""
        from fastapi import HTTPException

        from forge_harness.webhook_server.core.dependencies import require_jwt_permission
        checker = require_jwt_permission("deploy")
        user = {"sub": "x", "permissions": ["admin"]}
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert exc_info.value.status_code == 403


# ===========================================================================
# Section 5: Integration smoke tests
# ===========================================================================


@pytest.mark.asyncio
class TestHandoffFullLifecycle:
    """Full lifecycle integration tests for HandoffManager."""

    async def test_full_lifecycle_pending_to_completed(self, tmp_path):
        """Full lifecycle: create -> accept -> start -> complete."""
        manager = HandoffManager(tmp_path / "handoffs")
        handoff = await manager.create(
            from_agent="orchestrator",
            to_agent="worker",
            task="Implement feature Y",
            priority=HandoffPriority.HIGH,
            description="Detailed description of feature Y",
            acceptance_criteria=["Tests pass", "Coverage > 80%"],
        )

        assert handoff.status == HandoffStatus.PENDING

        # Accept
        accepted = await manager.accept(handoff.id, "worker")
        assert accepted.status == HandoffStatus.ACCEPTED
        assert accepted.accepted_at is not None

        # Start
        started = await manager.start(handoff.id)
        assert started.status == HandoffStatus.IN_PROGRESS

        # Complete
        completed = await manager.complete(
            handoff.id,
            result={"tests": 42, "coverage": 85},
            notes="Feature implemented successfully",
        )
        assert completed.status == HandoffStatus.COMPLETED
        assert completed.completed_at is not None
        assert completed.result["tests"] == 42

        # Verify persisted
        reloaded = await manager.get(handoff.id)
        assert reloaded.status == HandoffStatus.COMPLETED
        assert reloaded.result["tests"] == 42

    async def test_full_lifecycle_pending_to_rejected(self, tmp_path):
        """Full lifecycle: create -> reject with reason."""
        manager = HandoffManager(tmp_path / "handoffs")
        handoff = await manager.create(
            from_agent="a", to_agent="b", task="task"
        )

        rejected = await manager.reject(handoff.id, "b", reason="Task out of scope")
        assert rejected.status == HandoffStatus.REJECTED
        assert rejected.notes == "Task out of scope"
        assert rejected.completed_at is not None

    async def test_get_stats_with_mixed_statuses(self, tmp_path):
        """get_stats() with a mix of statuses and priorities."""
        manager = HandoffManager(tmp_path / "handoffs")

        h1 = await manager.create(
            from_agent="a", to_agent="b", task="t1", priority=HandoffPriority.CRITICAL
        )
        h2 = await manager.create(
            from_agent="a", to_agent="c", task="t2", priority=HandoffPriority.LOW
        )
        await manager.accept(h1.id, "b")
        await manager.start(h1.id)
        await manager.complete(h1.id, notes="done")

        stats = await manager.get_stats()
        assert stats["total"] == 2
        assert "completed" in stats["by_status"]
        assert "pending" in stats["by_status"]
        assert stats["by_priority"]["critical"] == 1
        assert stats["by_priority"]["low"] == 1
        assert stats["avg_completion_time_hours"] >= 0


class TestHandoffSchemaRoundtrip:
    """Additional schema round-trip tests for robustness."""

    def test_to_dict_from_dict_roundtrip(self):
        """to_dict / from_dict roundtrip preserves all fields."""
        original = Handoff.new(
            from_agent="a",
            to_agent="b",
            task="t",
            priority=HandoffPriority.CRITICAL,
            description="desc",
            files=["f.py"],
            context={"x": 1},
            test_command="pytest",
            acceptance_criteria=["AC1"],
            domain="dom",
            project="proj",
            notes="note",
            result={"output": "ok"},
        )
        original.status = HandoffStatus.COMPLETED
        original.accepted_at = datetime.now(UTC).isoformat()
        original.completed_at = datetime.now(UTC).isoformat()
        original.expires_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()

        restored = Handoff.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.status == original.status
        assert restored.priority == original.priority
        assert restored.files == original.files
        assert restored.context == original.context
        assert restored.result == original.result
        assert restored.domain == original.domain
        assert restored.project == original.project
        assert restored.notes == original.notes

    def test_to_json_is_valid_json(self):
        """to_json produces valid JSON."""
        h = Handoff.new(from_agent="a", to_agent="b", task="t")
        json_str = h.to_json()
        parsed = json.loads(json_str)
        assert parsed["status"] == "pending"
        assert parsed["priority"] == "medium"

    def test_from_dict_with_enum_status_not_string(self):
        """from_dict works when status is already a HandoffStatus enum."""
        data = {
            "id": "abc",
            "from_agent": "a",
            "to_agent": "b",
            "task": "t",
            "status": HandoffStatus.ACCEPTED,  # already an enum, not str
            "priority": HandoffPriority.HIGH,  # already an enum
        }
        h = Handoff.from_dict(data)
        assert h.status == HandoffStatus.ACCEPTED
        assert h.priority == HandoffPriority.HIGH
