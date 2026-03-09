"""
Tests for FORGE Harness Iteration Journal
==========================================

Tests journal entry generation, decision extraction, pattern identification,
blocker recording, and PROMPT.md writing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from forge_harness.iteration.journal import (
    Blocker,
    Decision,
    JournalEntry,
    Pattern,
    TaskResult,
    _extract_blockers,
    _extract_decisions,
    _generate_next_steps,
    _generate_summary,
    _identify_patterns,
    generate_journal,
    write_to_prompt_md,
)

# =============================================================================
# Test Data Classes
# =============================================================================


def test_task_result_dataclass():
    """TaskResult has correct structure and serialization."""
    task = TaskResult(
        task_id="HRN-001",
        title="Test task",
        status="completed",
        agent_type="codex",
        duration_seconds=45.2,
        error_message=None,
        files_changed=["file1.py", "file2.py"],
    )

    assert task.task_id == "HRN-001"
    assert task.title == "Test task"
    assert task.status == "completed"
    assert task.agent_type == "codex"
    assert task.duration_seconds == 45.2
    assert task.error_message is None
    assert task.files_changed == ["file1.py", "file2.py"]

    # Test serialization
    data = task.to_dict()
    assert data["task_id"] == "HRN-001"
    assert data["duration_seconds"] == 45.2

    # Test deserialization
    restored = TaskResult.from_dict(data)
    assert restored.task_id == task.task_id
    assert restored.title == task.title


def test_decision_dataclass():
    """Decision has correct structure and serialization."""
    decision = Decision(
        description="Use word-boundary matching",
        rationale="Avoids false matches",
        impact="high",
    )

    assert decision.description == "Use word-boundary matching"
    assert decision.rationale == "Avoids false matches"
    assert decision.impact == "high"

    # Test serialization
    data = decision.to_dict()
    assert data["description"] == "Use word-boundary matching"

    # Test deserialization
    restored = Decision.from_dict(data)
    assert restored.description == decision.description


def test_pattern_dataclass():
    """Pattern has correct structure and serialization."""
    pattern = Pattern(
        description="Backend tasks use OpenCode",
        occurrences=3,
        recommendation="Continue using OpenCode for backend",
    )

    assert pattern.description == "Backend tasks use OpenCode"
    assert pattern.occurrences == 3
    assert pattern.recommendation == "Continue using OpenCode for backend"

    # Test serialization
    data = pattern.to_dict()
    assert data["occurrences"] == 3

    # Test deserialization
    restored = Pattern.from_dict(data)
    assert restored.description == pattern.description


def test_blocker_dataclass():
    """Blocker has correct structure and serialization."""
    blocker = Blocker(
        description="Timeout on pytest",
        resolved=True,
        resolution="Increased timeout to 60s",
    )

    assert blocker.description == "Timeout on pytest"
    assert blocker.resolved is True
    assert blocker.resolution == "Increased timeout to 60s"

    # Test serialization
    data = blocker.to_dict()
    assert data["resolved"] is True

    # Test deserialization
    restored = Blocker.from_dict(data)
    assert restored.description == blocker.description


# =============================================================================
# Test Journal Entry
# =============================================================================


def test_journal_entry_dataclass():
    """JournalEntry has correct structure and serialization."""
    entry = JournalEntry(
        timestamp=datetime(2026, 1, 29, 14, 30, tzinfo=UTC),
        iteration_num=5,
        tasks_completed=[
            TaskResult(
                task_id="HRN-001",
                title="Test task",
                status="completed",
                agent_type="codex",
                duration_seconds=45.2,
            )
        ],
        decisions=[
            Decision(
                description="Use codex",
                rationale="Most versatile",
            )
        ],
        patterns=[
            Pattern(
                description="Test pattern",
                occurrences=2,
            )
        ],
        blockers=[
            Blocker(
                description="Test blocker",
                resolved=True,
            )
        ],
        next_steps=["Continue testing"],
        summary="Test summary",
    )

    assert entry.iteration_num == 5
    assert len(entry.tasks_completed) == 1
    assert len(entry.decisions) == 1
    assert len(entry.patterns) == 1
    assert len(entry.blockers) == 1
    assert entry.summary == "Test summary"

    # Test serialization
    data = entry.to_dict()
    assert data["iteration_num"] == 5
    assert len(data["tasks_completed"]) == 1

    # Test deserialization
    restored = JournalEntry.from_dict(data)
    assert restored.iteration_num == entry.iteration_num
    assert restored.summary == entry.summary


def test_journal_entry_to_markdown():
    """JournalEntry.to_markdown produces correct format."""
    entry = JournalEntry(
        timestamp=datetime(2026, 1, 29, 14, 30, tzinfo=UTC),
        iteration_num=5,
        tasks_completed=[
            TaskResult(
                task_id="HRN-001",
                title="Test task",
                status="completed",
                agent_type="codex",
                duration_seconds=45.2,
            ),
            TaskResult(
                task_id="HRN-002",
                title="Failed task",
                status="failed",
                agent_type="opencode",
                duration_seconds=10.0,
                error_message="Timeout error",
            ),
        ],
        decisions=[
            Decision(
                description="Use word-boundary matching",
                rationale="Avoids false matches",
                impact="high",
            )
        ],
        patterns=[
            Pattern(
                description="Backend tasks use OpenCode",
                occurrences=3,
                recommendation="Continue using OpenCode",
            )
        ],
        blockers=[
            Blocker(
                description="Timeout on pytest",
                resolved=True,
                resolution="Increased timeout to 60s",
            ),
            Blocker(
                description="Missing dependency",
                resolved=False,
            ),
        ],
        next_steps=["Continue testing", "Review coverage"],
        summary="Test iteration",
    )

    markdown = entry.to_markdown()

    # Check structure
    assert "## [2026-01-29 14:30] Iteration 5 - Test iteration" in markdown
    assert "Completed 1/2 tasks" in markdown
    assert "### Completed" in markdown
    assert "HRN-001: Test task" in markdown
    assert "HRN-002: Failed task" in markdown
    assert "Error: Timeout error" in markdown
    assert "### Decisions" in markdown
    assert "Use word-boundary matching" in markdown
    assert "Rationale: Avoids false matches" in markdown
    assert "### Patterns" in markdown
    assert "Backend tasks use OpenCode (3 occurrences)" in markdown
    assert "### Blockers" in markdown
    assert "✅ Timeout on pytest" in markdown
    assert "Resolved: Increased timeout to 60s" in markdown
    assert "⬜ Missing dependency" in markdown
    assert "### Next" in markdown
    assert "Continue testing" in markdown
    assert "---" in markdown


# =============================================================================
# Test Helper Functions
# =============================================================================


def test_extract_decisions():
    """_extract_decisions finds decisions in prompts."""
    results = [
        {
            "task_id": "HRN-001",
            "prompt": "Task: Implement feature using codex because it's most versatile.",
            "description": "We chose to use word-boundary matching for accuracy.",
        },
        {
            "task_id": "HRN-002",
            "prompt": "Decided to implement with OpenCode for backend API.",
            "description": "Selected pytest for testing framework.",
        },
    ]

    decisions = _extract_decisions(results)

    # Should extract decisions from keywords
    assert len(decisions) > 0
    assert any(
        "codex" in d.description.lower() or "word-boundary" in d.description.lower()
        for d in decisions
    )


def test_identify_patterns():
    """_identify_patterns finds repeated outcomes."""
    results = [
        {
            "task_id": "HRN-001",
            "agent_type": "opencode",
            "status": "completed",
            "duration_seconds": 90.0,
        },
        {
            "task_id": "HRN-002",
            "agent_type": "opencode",
            "status": "completed",
            "duration_seconds": 85.0,
        },
        {
            "task_id": "HRN-003",
            "agent_type": "opencode",
            "status": "completed",
            "duration_seconds": 95.0,
        },
        {
            "task_id": "HRN-004",
            "agent_type": "codex",
            "status": "completed",
            "duration_seconds": 30.0,
        },
    ]

    patterns = _identify_patterns(results)

    # Should identify dominant agent pattern
    assert len(patterns) > 0
    opencode_pattern = next((p for p in patterns if "opencode" in p.description.lower()), None)
    assert opencode_pattern is not None
    assert opencode_pattern.occurrences == 3


def test_identify_patterns_failures():
    """_identify_patterns finds failure patterns."""
    results = [
        {
            "task_id": "HRN-001",
            "agent_type": "codex",
            "status": "failed",
            "error_message": "Timeout: Connection timed out",
            "duration_seconds": 60.0,
        },
        {
            "task_id": "HRN-002",
            "agent_type": "codex",
            "status": "failed",
            "error_message": "Timeout: Request timed out",
            "duration_seconds": 60.0,
        },
    ]

    patterns = _identify_patterns(results)

    # Should identify timeout failure pattern
    timeout_pattern = next((p for p in patterns if "timeout" in p.description.lower()), None)
    assert timeout_pattern is not None
    assert timeout_pattern.occurrences == 2


def test_extract_blockers():
    """_extract_blockers finds errors in results."""
    results = [
        {
            "task_id": "HRN-001",
            "status": "failed",
            "error_message": "Timeout: pytest command timed out after 30 seconds",
        },
        {
            "task_id": "HRN-002",
            "status": "failed",
            "error_message": "Connection error: Failed to connect to server",
        },
        {
            "task_id": "HRN-003",
            "status": "completed",
            "error_message": "Timeout: initially timed out but retried successfully",
        },
    ]

    blockers = _extract_blockers(results)

    # Should extract blockers from error messages
    assert len(blockers) >= 2

    # Check for timeout blocker
    timeout_blocker = next((b for b in blockers if "timeout" in b.description.lower()), None)
    assert timeout_blocker is not None

    # Check for connection blocker
    connection_blocker = next((b for b in blockers if "connection" in b.description.lower()), None)
    assert connection_blocker is not None

    # Check resolved status
    resolved_blockers = [b for b in blockers if b.resolved]
    assert len(resolved_blockers) >= 1  # At least one resolved


def test_generate_next_steps():
    """_generate_next_steps creates actionable next steps."""
    results = [
        {
            "task_id": "HRN-001",
            "title": "Implement feature",
            "status": "failed",
        },
        {
            "task_id": "HRN-002",
            "title": "Write tests",
            "status": "partial",
        },
        {
            "task_id": "HRN-003",
            "title": "Complete docs",
            "status": "completed",
        },
    ]

    next_steps = _generate_next_steps(results)

    # Should generate next steps for incomplete tasks
    assert len(next_steps) >= 2
    assert any("HRN-001" in step for step in next_steps)
    assert any("HRN-002" in step for step in next_steps)
    assert any("Retry" in step for step in next_steps)
    assert any("Complete" in step for step in next_steps)


def test_generate_summary():
    """_generate_summary creates appropriate summary."""
    entry = JournalEntry(
        timestamp=datetime.now(UTC),
        iteration_num=1,
        tasks_completed=[
            TaskResult(
                task_id="HRN-001",
                title="Task 1",
                status="completed",
                agent_type="opencode",
                duration_seconds=45.0,
            ),
            TaskResult(
                task_id="HRN-002",
                title="Task 2",
                status="completed",
                agent_type="opencode",
                duration_seconds=50.0,
            ),
            TaskResult(
                task_id="HRN-003",
                title="Task 3",
                status="failed",
                agent_type="codex",
                duration_seconds=30.0,
            ),
        ],
        blockers=[
            Blocker(
                description="Test blocker",
                resolved=True,
                resolution="Fixed",
            )
        ],
    )

    summary = _generate_summary(entry)

    # Should mention completion ratio
    assert "2/3" in summary

    # Should mention dominant agent
    assert "opencode" in summary.lower()

    # Should mention resolved blocker
    assert "blocker" in summary.lower()
    assert "resolved" in summary.lower()


# =============================================================================
# Test Main Functions
# =============================================================================


def test_generate_journal():
    """generate_journal creates valid JournalEntry."""
    results = [
        {
            "task_id": "HRN-001",
            "title": "Test task 1",
            "status": "completed",
            "agent_type": "codex",
            "duration_seconds": 45.2,
            "prompt": "Implement feature using codex",
            "files_changed": ["file1.py"],
        },
        {
            "task_id": "HRN-002",
            "title": "Test task 2",
            "status": "failed",
            "agent_type": "opencode",
            "duration_seconds": 30.0,
            "prompt": "Create API endpoint",
            "error_message": "Timeout: Request timed out",
            "files_changed": [],
        },
    ]

    entry = generate_journal(results, iteration_num=1)

    # Check basic structure
    assert isinstance(entry, JournalEntry)
    assert entry.iteration_num == 1
    assert len(entry.tasks_completed) == 2

    # Check tasks
    assert entry.tasks_completed[0].task_id == "HRN-001"
    assert entry.tasks_completed[0].status == "completed"
    assert entry.tasks_completed[1].task_id == "HRN-002"
    assert entry.tasks_completed[1].status == "failed"

    # Check summary was generated
    assert entry.summary != ""
    assert "1/2" in entry.summary  # 1 completed out of 2

    # Check next steps were generated (failed task should create retry step)
    assert len(entry.next_steps) > 0
    assert any("HRN-002" in step for step in entry.next_steps)


def test_generate_journal_empty_results():
    """generate_journal handles empty results gracefully."""
    entry = generate_journal([], iteration_num=1)

    assert isinstance(entry, JournalEntry)
    assert entry.iteration_num == 1
    assert len(entry.tasks_completed) == 0
    assert entry.summary != ""


def test_write_to_prompt_md(tmp_path):
    """write_to_prompt_md appends to file."""
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("# FORGE Session Notes\n\n## Old Entry\nOld content\n")

    entry = JournalEntry(
        timestamp=datetime(2026, 1, 29, 14, 30, tzinfo=UTC),
        iteration_num=1,
        tasks_completed=[
            TaskResult(
                task_id="HRN-001",
                title="Test task",
                status="completed",
                agent_type="codex",
                duration_seconds=10.0,
            )
        ],
        next_steps=["Continue testing"],
        summary="Test iteration",
    )

    result = write_to_prompt_md(entry, prompt_path)

    assert result is True
    content = prompt_path.read_text()

    # Check new entry was added
    assert "Iteration 1" in content
    assert "HRN-001" in content
    assert "Test task" in content

    # Check old content was preserved
    assert "Old Entry" in content
    assert "Old content" in content

    # Check new entry is before old entry (inserted at top)
    iteration_1_pos = content.index("Iteration 1")
    old_entry_pos = content.index("Old Entry")
    assert iteration_1_pos < old_entry_pos


def test_write_to_prompt_md_new_file(tmp_path):
    """write_to_prompt_md creates new file if not exists."""
    prompt_path = tmp_path / "PROMPT.md"

    entry = JournalEntry(
        timestamp=datetime(2026, 1, 29, 14, 30, tzinfo=UTC),
        iteration_num=1,
        tasks_completed=[
            TaskResult(
                task_id="HRN-001",
                title="Test task",
                status="completed",
                agent_type="codex",
                duration_seconds=10.0,
            )
        ],
        next_steps=["Continue testing"],
        summary="Test iteration",
    )

    result = write_to_prompt_md(entry, prompt_path)

    assert result is True
    assert prompt_path.exists()

    content = prompt_path.read_text()
    assert "# FORGE Session Notes" in content
    assert "Iteration 1" in content


def test_write_to_prompt_md_multiple_entries(tmp_path):
    """write_to_prompt_md maintains order of multiple entries."""
    prompt_path = tmp_path / "PROMPT.md"

    # Write first entry
    entry1 = JournalEntry(
        timestamp=datetime(2026, 1, 29, 14, 0, tzinfo=UTC),
        iteration_num=1,
        tasks_completed=[],
        summary="First iteration",
    )
    write_to_prompt_md(entry1, prompt_path)

    # Write second entry
    entry2 = JournalEntry(
        timestamp=datetime(2026, 1, 29, 15, 0, tzinfo=UTC),
        iteration_num=2,
        tasks_completed=[],
        summary="Second iteration",
    )
    write_to_prompt_md(entry2, prompt_path)

    content = prompt_path.read_text()

    # Second entry should appear before first entry (newest first)
    iteration_2_pos = content.index("Iteration 2")
    iteration_1_pos = content.index("Iteration 1")
    assert iteration_2_pos < iteration_1_pos


# =============================================================================
# Integration Tests
# =============================================================================


def test_full_journal_workflow(tmp_path):
    """Test complete workflow from results to PROMPT.md."""
    prompt_path = tmp_path / "PROMPT.md"

    # Simulate iteration results
    results = [
        {
            "task_id": "HRN-001",
            "title": "Implement dispatcher",
            "status": "completed",
            "agent_type": "codex",
            "duration_seconds": 120.5,
            "prompt": "Create task dispatcher using tmux and decided to use word-boundary matching",
            "files_changed": ["dispatcher.py"],
        },
        {
            "task_id": "HRN-002",
            "title": "Add tests",
            "status": "completed",
            "agent_type": "codex",
            "duration_seconds": 45.0,
            "prompt": "Write pytest tests for dispatcher",
            "files_changed": ["test_dispatcher.py"],
        },
        {
            "task_id": "HRN-003",
            "title": "OpenCode integration",
            "status": "failed",
            "agent_type": "opencode",
            "duration_seconds": 60.0,
            "prompt": "Integrate OpenCode backend",
            "error_message": "Timeout: Command timed out after 60 seconds",
            "files_changed": [],
        },
    ]

    # Generate journal entry
    entry = generate_journal(results, iteration_num=5)

    # Verify entry content
    assert entry.iteration_num == 5
    assert len(entry.tasks_completed) == 3
    assert entry.summary != ""

    # Write to PROMPT.md
    success = write_to_prompt_md(entry, prompt_path)
    assert success

    # Verify file content
    content = prompt_path.read_text()
    assert "Iteration 5" in content
    assert "HRN-001" in content
    assert "HRN-002" in content
    assert "HRN-003" in content
    assert "Timeout" in content
    assert "Completed 2/3 tasks" in content
