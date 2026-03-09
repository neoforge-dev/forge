"""Tests for Iteration Journal.

Validates journal entry logging, markdown formatting, and PROMPT.md writing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from forge_harness.iteration.journal import (
    EntryType,
    IterationJournal,
    JournalEntry,
)


class TestJournalEntry:
    """Test JournalEntry dataclass."""

    def test_decision_entry_creation(self):
        """Should create decision entry with metadata."""
        entry = JournalEntry(
            timestamp=datetime(2025, 1, 15, 10, 30),
            entry_type=EntryType.DECISION,
            content="Use Zustand: Better performance",
            metadata={"decision": "Use Zustand", "reasoning": "Better performance"},
        )

        assert entry.entry_type == EntryType.DECISION
        assert entry.content == "Use Zustand: Better performance"
        assert entry.metadata["decision"] == "Use Zustand"
        assert entry.metadata["reasoning"] == "Better performance"

    def test_pattern_entry_creation(self):
        """Should create pattern entry with outcome."""
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.PATTERN,
            content="TanStack Query pattern",
            metadata={"pattern": "TanStack Query", "outcome": "Extract data wrapper"},
        )

        assert entry.entry_type == EntryType.PATTERN
        assert "pattern" in entry.metadata
        assert "outcome" in entry.metadata

    def test_blocker_entry_with_resolution(self):
        """Should create blocker entry with resolution."""
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.BLOCKER,
            content="SSE reconnection loop",
            metadata={"blocker": "SSE reconnection loop", "resolution": "Use stable refs"},
        )

        assert entry.entry_type == EntryType.BLOCKER
        assert entry.metadata["blocker"] == "SSE reconnection loop"
        assert entry.metadata["resolution"] == "Use stable refs"

    def test_blocker_entry_without_resolution(self):
        """Should create blocker entry without resolution."""
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.BLOCKER,
            content="Database timeout",
            metadata={"blocker": "Database timeout", "resolution": None},
        )

        assert entry.entry_type == EntryType.BLOCKER
        assert entry.metadata["resolution"] is None

    def test_decision_to_markdown(self):
        """Should format decision entry as markdown."""
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.DECISION,
            content="",
            metadata={"decision": "Use Zustand", "reasoning": "Simpler API"},
        )

        md = entry.to_markdown()
        assert "**Decision**: Use Zustand" in md
        assert "**Why**: Simpler API" in md

    def test_pattern_to_markdown(self):
        """Should format pattern entry as markdown."""
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.PATTERN,
            content="",
            metadata={"pattern": "API calls", "outcome": "Use wrapper extraction"},
        )

        md = entry.to_markdown()
        assert "**Pattern**: API calls" in md
        assert "**Outcome**: Use wrapper extraction" in md

    def test_blocker_resolved_to_markdown(self):
        """Should format resolved blocker as markdown."""
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.BLOCKER,
            content="",
            metadata={"blocker": "SSE loop", "resolution": "Stable refs"},
        )

        md = entry.to_markdown()
        assert "**Blocker**: SSE loop" in md
        assert "**Resolution**: Stable refs" in md

    def test_blocker_unresolved_to_markdown(self):
        """Should format unresolved blocker as markdown."""
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.BLOCKER,
            content="",
            metadata={"blocker": "Database timeout", "resolution": None},
        )

        md = entry.to_markdown()
        assert "**Blocker**: Database timeout" in md
        assert "**Status**: Unresolved" in md

    def test_entry_to_dict(self):
        """Should convert entry to dictionary."""
        timestamp = datetime(2025, 1, 15, 10, 30)
        entry = JournalEntry(
            timestamp=timestamp,
            entry_type=EntryType.DECISION,
            content="Test",
            metadata={"key": "value"},
        )

        d = entry.to_dict()
        assert d["timestamp"] == timestamp.isoformat()
        assert d["entry_type"] == "decision"
        assert d["content"] == "Test"
        assert d["metadata"]["key"] == "value"


class TestIterationJournal:
    """Test IterationJournal class."""

    def test_initialization(self, tmp_path: Path):
        """Should initialize journal with project root."""
        journal = IterationJournal(project_root=tmp_path)

        assert journal.project_root == tmp_path
        assert journal.entries == []
        assert isinstance(journal.session_start, datetime)

    def test_initialization_defaults_to_cwd(self):
        """Should default to current working directory."""
        journal = IterationJournal()

        assert journal.project_root == Path.cwd()

    def test_log_decision(self, tmp_path: Path):
        """Should log decision entry."""
        journal = IterationJournal(project_root=tmp_path)

        journal.log_decision(
            decision="Use Zustand over Context",
            reasoning="Better performance and simpler API",
        )

        assert len(journal.entries) == 1
        entry = journal.entries[0]
        assert entry.entry_type == EntryType.DECISION
        assert entry.metadata["decision"] == "Use Zustand over Context"
        assert entry.metadata["reasoning"] == "Better performance and simpler API"

    def test_log_pattern(self, tmp_path: Path):
        """Should log pattern entry."""
        journal = IterationJournal(project_root=tmp_path)

        journal.log_pattern(
            pattern="TanStack Query with wrapper",
            outcome="Extract json.data for clean queries",
        )

        assert len(journal.entries) == 1
        entry = journal.entries[0]
        assert entry.entry_type == EntryType.PATTERN
        assert entry.metadata["pattern"] == "TanStack Query with wrapper"
        assert entry.metadata["outcome"] == "Extract json.data for clean queries"

    def test_log_blocker_with_resolution(self, tmp_path: Path):
        """Should log blocker with resolution."""
        journal = IterationJournal(project_root=tmp_path)

        journal.log_blocker(
            blocker="SSE reconnection loop in useEffect",
            resolution="Use stable refs to prevent dependency changes",
        )

        assert len(journal.entries) == 1
        entry = journal.entries[0]
        assert entry.entry_type == EntryType.BLOCKER
        assert entry.metadata["blocker"] == "SSE reconnection loop in useEffect"
        assert entry.metadata["resolution"] == "Use stable refs to prevent dependency changes"

    def test_log_blocker_without_resolution(self, tmp_path: Path):
        """Should log blocker without resolution."""
        journal = IterationJournal(project_root=tmp_path)

        journal.log_blocker(blocker="Database connection timeout")

        assert len(journal.entries) == 1
        entry = journal.entries[0]
        assert entry.entry_type == EntryType.BLOCKER
        assert entry.metadata["blocker"] == "Database connection timeout"
        assert entry.metadata["resolution"] is None

    def test_log_context(self, tmp_path: Path):
        """Should log general context."""
        journal = IterationJournal(project_root=tmp_path)

        journal.log_context("Using Vite 5 with React 18")

        assert len(journal.entries) == 1
        entry = journal.entries[0]
        assert entry.entry_type == EntryType.CONTEXT
        assert entry.content == "Using Vite 5 with React 18"

    def test_log_next_step(self, tmp_path: Path):
        """Should log next step."""
        journal = IterationJournal(project_root=tmp_path)

        journal.log_next_step("Implement drag-and-drop for task reordering")

        assert len(journal.entries) == 1
        entry = journal.entries[0]
        assert entry.entry_type == EntryType.NEXT_STEP
        assert entry.content == "Implement drag-and-drop for task reordering"

    def test_multiple_entries(self, tmp_path: Path):
        """Should log multiple entries in sequence."""
        journal = IterationJournal(project_root=tmp_path)

        journal.log_decision("Use TypeScript", "Type safety")
        journal.log_pattern("Zustand pattern", "Global state management")
        journal.log_blocker("Build timeout", "Increase memory limit")
        journal.log_next_step("Add E2E tests")

        assert len(journal.entries) == 4
        assert journal.entries[0].entry_type == EntryType.DECISION
        assert journal.entries[1].entry_type == EntryType.PATTERN
        assert journal.entries[2].entry_type == EntryType.BLOCKER
        assert journal.entries[3].entry_type == EntryType.NEXT_STEP

    def test_generate_summary_empty(self, tmp_path: Path):
        """Should return empty string for no entries."""
        journal = IterationJournal(project_root=tmp_path)

        summary = journal.generate_summary()

        assert summary == ""

    def test_generate_summary_with_entries(self, tmp_path: Path):
        """Should generate markdown summary with all entries."""
        journal = IterationJournal(project_root=tmp_path)
        journal.session_start = datetime(2025, 1, 15, 10, 30)

        journal.log_decision("Use Zustand", "Better performance")
        journal.log_pattern("TanStack Query", "Wrapper extraction")
        journal.log_blocker("SSE loop", "Stable refs")
        journal.log_next_step("Add tests")

        summary = journal.generate_summary()

        assert "[2025-01-15 10:30] Session Notes" in summary
        assert "### Decisions Made" in summary
        assert "**Decision**: Use Zustand" in summary
        assert "### Patterns Discovered" in summary
        assert "**Pattern**: TanStack Query" in summary
        assert "### Blockers Encountered" in summary
        assert "**Blocker**: SSE loop" in summary
        assert "### Next Steps" in summary
        assert "Add tests" in summary

    def test_generate_summary_sections_order(self, tmp_path: Path):
        """Should generate sections in correct order."""
        journal = IterationJournal(project_root=tmp_path)

        journal.log_next_step("Step 1")
        journal.log_blocker("Blocker 1")
        journal.log_pattern("Pattern 1", "Outcome 1")
        journal.log_decision("Decision 1", "Reasoning 1")

        summary = journal.generate_summary()
        lines = summary.split("\n")

        # Find section headers
        decisions_idx = next(i for i, line in enumerate(lines) if "Decisions Made" in line)
        patterns_idx = next(i for i, line in enumerate(lines) if "Patterns Discovered" in line)
        blockers_idx = next(i for i, line in enumerate(lines) if "Blockers Encountered" in line)
        next_steps_idx = next(i for i, line in enumerate(lines) if "Next Steps" in line)

        # Verify order
        assert decisions_idx < patterns_idx < blockers_idx < next_steps_idx

    def test_write_to_prompt_md_creates_file(self, tmp_path: Path):
        """Should create PROMPT.md if it doesn't exist."""
        journal = IterationJournal(project_root=tmp_path)
        journal.log_decision("Test decision", "Test reasoning")

        prompt_path = journal.write_to_prompt_md()

        assert prompt_path.exists()
        assert prompt_path == tmp_path / "docs" / "PROMPT.md"

        content = prompt_path.read_text()
        assert "Session Notes" in content
        assert "Test decision" in content

    def test_write_to_prompt_md_appends_to_existing(self, tmp_path: Path):
        """Should append to existing PROMPT.md."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        prompt_path = docs_dir / "PROMPT.md"
        prompt_path.write_text("# Existing Content\n\nSome text here.\n")

        journal = IterationJournal(project_root=tmp_path)
        journal.log_decision("New decision", "New reasoning")

        journal.write_to_prompt_md()

        content = prompt_path.read_text()
        assert "# Existing Content" in content
        assert "Some text here." in content
        assert "Session Notes" in content
        assert "New decision" in content

    def test_write_to_prompt_md_empty_journal(self, tmp_path: Path):
        """Should not write if journal is empty."""
        journal = IterationJournal(project_root=tmp_path)

        prompt_path = journal.write_to_prompt_md()

        # File shouldn't be created if no entries
        assert not prompt_path.exists() or prompt_path.read_text() == ""

    def test_clear(self, tmp_path: Path):
        """Should clear all entries."""
        journal = IterationJournal(project_root=tmp_path)
        journal.log_decision("Decision 1", "Reasoning 1")
        journal.log_pattern("Pattern 1", "Outcome 1")

        assert len(journal.entries) == 2

        journal.clear()

        assert len(journal.entries) == 0
        # Session start should be reset
        assert journal.session_start > datetime(2025, 1, 1)

    def test_get_entries_by_type(self, tmp_path: Path):
        """Should filter entries by type."""
        journal = IterationJournal(project_root=tmp_path)
        journal.log_decision("Decision 1", "Reasoning 1")
        journal.log_decision("Decision 2", "Reasoning 2")
        journal.log_pattern("Pattern 1", "Outcome 1")
        journal.log_blocker("Blocker 1")

        decisions = journal.get_entries_by_type(EntryType.DECISION)
        patterns = journal.get_entries_by_type(EntryType.PATTERN)
        blockers = journal.get_entries_by_type(EntryType.BLOCKER)

        assert len(decisions) == 2
        assert len(patterns) == 1
        assert len(blockers) == 1

    def test_get_unresolved_blockers(self, tmp_path: Path):
        """Should get only unresolved blockers."""
        journal = IterationJournal(project_root=tmp_path)
        journal.log_blocker("Blocker 1", "Fixed it")
        journal.log_blocker("Blocker 2")  # Unresolved
        journal.log_blocker("Blocker 3")  # Unresolved

        unresolved = journal.get_unresolved_blockers()

        assert len(unresolved) == 2
        assert all(e.metadata.get("resolution") is None for e in unresolved)

    def test_export_json(self, tmp_path: Path):
        """Should export journal to JSON format."""
        journal = IterationJournal(project_root=tmp_path)
        journal.log_decision("Decision 1", "Reasoning 1")
        journal.log_pattern("Pattern 1", "Outcome 1")

        data = journal.export_json()

        assert "session_start" in data
        assert data["project_root"] == str(tmp_path)
        assert data["entry_count"] == 2
        assert len(data["entries"]) == 2
        assert data["entries"][0]["entry_type"] == "decision"
        assert data["entries"][1]["entry_type"] == "pattern"

    def test_auto_write_mode(self, tmp_path: Path):
        """Should auto-write to PROMPT.md after each entry."""
        journal = IterationJournal(project_root=tmp_path, auto_write=True)
        prompt_path = tmp_path / "docs" / "PROMPT.md"

        journal.log_decision("Decision 1", "Reasoning 1")
        assert prompt_path.exists()

        initial_content = prompt_path.read_text()
        assert "Decision 1" in initial_content

        journal.log_pattern("Pattern 1", "Outcome 1")
        updated_content = prompt_path.read_text()
        assert "Pattern 1" in updated_content

    def test_auto_write_disabled_by_default(self, tmp_path: Path):
        """Should not auto-write by default."""
        journal = IterationJournal(project_root=tmp_path)
        prompt_path = tmp_path / "docs" / "PROMPT.md"

        journal.log_decision("Decision 1", "Reasoning 1")

        # File should not exist without explicit write
        assert not prompt_path.exists()

    def test_complex_workflow(self, tmp_path: Path):
        """Should handle a complex workflow with multiple entry types."""
        journal = IterationJournal(project_root=tmp_path)

        # Simulate a real session
        journal.log_context("Starting work on Command Center Phase 4")
        journal.log_decision("Use React DnD for drag-drop", "Most popular, good TypeScript support")
        journal.log_pattern("Zustand stores for UI state", "Clean separation from server state")
        journal.log_blocker("Build bundle too large", "Split code by route")
        journal.log_decision("Implement code splitting", "Use React.lazy and Suspense")
        journal.log_blocker("Build bundle too large")  # Mark as resolved
        journal.entries[-1].metadata["resolution"] = "Code splitting reduced by 40%"
        journal.log_next_step("Add E2E tests with Playwright")
        journal.log_next_step("Deploy to staging")

        # Verify all entries logged
        assert len(journal.entries) == 8

        # Verify unresolved blockers
        unresolved = journal.get_unresolved_blockers()
        assert len(unresolved) == 0  # Both blockers resolved

        # Generate summary
        summary = journal.generate_summary()
        assert "Starting work on Command Center Phase 4" in summary
        assert "Use React DnD" in summary
        assert "Add E2E tests with Playwright" in summary

        # Write to file
        prompt_path = journal.write_to_prompt_md()
        content = prompt_path.read_text()
        assert content == summary
