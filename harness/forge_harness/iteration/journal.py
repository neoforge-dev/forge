"""Iteration Journal for FORGE Harness.

Auto-generates session notes capturing decisions, patterns, and learnings.
Writes to docs/PROMPT.md with timestamps for session continuity.

Usage:
    from forge_harness.iteration.journal import IterationJournal

    journal = IterationJournal(project_root=Path("/path/to/project"))

    # Log during iteration
    journal.log_decision(
        decision="Use Zustand over Context API",
        reasoning="Simpler API, less re-renders, better devtools"
    )

    journal.log_pattern(
        pattern="API calls with TanStack Query",
        outcome="Extract data from json.data.tasks wrapper"
    )

    journal.log_blocker(
        blocker="SSE reconnection loop",
        resolution="Use stable refs in useEffect dependencies"
    )

    # Generate and write summary
    journal.write_to_prompt_md()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class EntryType(str, Enum):
    """Type of journal entry."""

    DECISION = "decision"
    PATTERN = "pattern"
    BLOCKER = "blocker"
    CONTEXT = "context"
    NEXT_STEP = "next_step"


@dataclass
class TaskResult:
    """Task execution result used in iteration journal."""

    task_id: str
    title: str
    status: str
    agent_type: str
    duration_seconds: float
    error_message: str | None = None
    files_changed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "agent_type": self.agent_type,
            "duration_seconds": self.duration_seconds,
            "error_message": self.error_message,
            "files_changed": self.files_changed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskResult:
        return cls(
            task_id=data.get("task_id", ""),
            title=data.get("title", ""),
            status=data.get("status", ""),
            agent_type=data.get("agent_type", ""),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            error_message=data.get("error_message"),
            files_changed=list(data.get("files_changed", [])),
        )


@dataclass
class Decision:
    description: str
    rationale: str
    impact: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return {"description": self.description, "rationale": self.rationale, "impact": self.impact}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decision:
        return cls(
            description=data.get("description", ""),
            rationale=data.get("rationale", ""),
            impact=data.get("impact", "medium"),
        )


@dataclass
class Pattern:
    description: str
    occurrences: int
    recommendation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "occurrences": self.occurrences,
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pattern:
        return cls(
            description=data.get("description", ""),
            occurrences=int(data.get("occurrences", 0)),
            recommendation=data.get("recommendation"),
        )


@dataclass
class Blocker:
    description: str
    resolved: bool = False
    resolution: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "resolved": self.resolved,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Blocker:
        return cls(
            description=data.get("description", ""),
            resolved=bool(data.get("resolved", False)),
            resolution=data.get("resolution"),
        )


@dataclass
class JournalEntry:
    """Journal entry that supports both event logs and iteration summaries."""

    timestamp: datetime
    entry_type: EntryType | None = None
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    iteration_num: int | None = None
    tasks_completed: list[TaskResult] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    summary: str = ""

    def to_markdown(self) -> str:
        """Format entry as markdown."""
        if self.entry_type is not None:
            if self.entry_type == EntryType.DECISION:
                decision = self.metadata.get("decision", "")
                reasoning = self.metadata.get("reasoning", "")
                return f"- **Decision**: {decision}\n  - **Why**: {reasoning}"
            if self.entry_type == EntryType.PATTERN:
                pattern = self.metadata.get("pattern", "")
                outcome = self.metadata.get("outcome", "")
                return f"- **Pattern**: {pattern}\n  - **Outcome**: {outcome}"
            if self.entry_type == EntryType.BLOCKER:
                blocker = self.metadata.get("blocker", "")
                resolution = self.metadata.get("resolution")
                if resolution:
                    return f"- **Blocker**: {blocker}\n  - **Resolution**: {resolution}"
                return f"- **Blocker**: {blocker}\n  - **Status**: Unresolved"
            if self.entry_type == EntryType.CONTEXT:
                return f"- {self.content}"
            if self.entry_type == EntryType.NEXT_STEP:
                return f"- {self.content}"
            return f"- {self.content}"

        ts = self.timestamp.strftime("%Y-%m-%d %H:%M")
        iteration = self.iteration_num or 0
        lines = [f"## [{ts}] Iteration {iteration} - {self.summary}"]
        completed = len([t for t in self.tasks_completed if t.status == "completed"])
        total = len(self.tasks_completed)
        lines.append(f"Completed {completed}/{total} tasks")
        lines.append("")
        lines.append("### Completed")
        for task in self.tasks_completed:
            lines.append(f"{task.task_id}: {task.title}")
            if task.error_message:
                lines.append(f"Error: {task.error_message}")
        if self.decisions:
            lines.append("")
            lines.append("### Decisions")
            for decision in self.decisions:
                lines.append(f"- {decision.description}")
                lines.append(f"  - Rationale: {decision.rationale}")
        if self.patterns:
            lines.append("")
            lines.append("### Patterns")
            for pattern in self.patterns:
                lines.append(f"- {pattern.description} ({pattern.occurrences} occurrences)")
        if self.blockers:
            lines.append("")
            lines.append("### Blockers")
            for blocker in self.blockers:
                icon = "✅" if blocker.resolved else "⬜"
                lines.append(f"{icon} {blocker.description}")
                if blocker.resolution:
                    lines.append(f"Resolved: {blocker.resolution}")
        if self.next_steps:
            lines.append("")
            lines.append("### Next")
            for step in self.next_steps:
                lines.append(f"- {step}")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "entry_type": self.entry_type.value if self.entry_type else None,
            "content": self.content,
            "metadata": self.metadata,
            "iteration_num": self.iteration_num,
            "tasks_completed": [t.to_dict() for t in self.tasks_completed],
            "decisions": [d.__dict__ for d in self.decisions],
            "patterns": [p.__dict__ for p in self.patterns],
            "blockers": [b.__dict__ for b in self.blockers],
            "next_steps": list(self.next_steps),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JournalEntry:
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            entry_type=EntryType(data["entry_type"]) if data.get("entry_type") else None,
            content=data.get("content"),
            metadata=data.get("metadata", {}),
            iteration_num=data.get("iteration_num"),
            tasks_completed=[TaskResult.from_dict(t) for t in data.get("tasks_completed", [])],
            decisions=[Decision.from_dict(d) for d in data.get("decisions", [])],
            patterns=[Pattern.from_dict(p) for p in data.get("patterns", [])],
            blockers=[Blocker.from_dict(b) for b in data.get("blockers", [])],
            next_steps=list(data.get("next_steps", [])),
            summary=data.get("summary", ""),
        )


class IterationJournal:
    """Journal for capturing iteration decisions, patterns, and learnings.

    Provides structured logging of session activities and writes
    formatted summaries to docs/PROMPT.md for continuation.
    """

    def __init__(
        self,
        project_root: Path | None = None,
        auto_write: bool = False,
    ):
        """Initialize journal.

        Args:
            project_root: Root directory of the project (for PROMPT.md location).
                         Defaults to current working directory.
            auto_write: If True, automatically write to PROMPT.md after each entry.
        """
        self.project_root = project_root or Path.cwd()
        self.auto_write = auto_write
        self.entries: list[JournalEntry] = []
        self.session_start = datetime.now()

        logger.info(
            f"Initialized iteration journal for {self.project_root}",
            extra={"session_start": self.session_start.isoformat()},
        )

    def log_decision(self, decision: str, reasoning: str) -> None:
        """Log a decision made during iteration.

        Args:
            decision: What decision was made
            reasoning: Why this decision was made
        """
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.DECISION,
            content=f"{decision}: {reasoning}",
            metadata={"decision": decision, "reasoning": reasoning},
        )
        self.entries.append(entry)

        logger.info(
            f"Logged decision: {decision}",
            extra={"reasoning": reasoning},
        )

        if self.auto_write:
            self.write_to_prompt_md()

    def log_pattern(self, pattern: str, outcome: str) -> None:
        """Log a pattern discovered from task outcomes.

        Args:
            pattern: Pattern or approach used
            outcome: Result or insight gained
        """
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.PATTERN,
            content=f"{pattern}: {outcome}",
            metadata={"pattern": pattern, "outcome": outcome},
        )
        self.entries.append(entry)

        logger.info(
            f"Logged pattern: {pattern}",
            extra={"outcome": outcome},
        )

        if self.auto_write:
            self.write_to_prompt_md()

    def log_blocker(self, blocker: str, resolution: str | None = None) -> None:
        """Log a blocker encountered and its resolution (if any).

        Args:
            blocker: Description of the blocker
            resolution: How the blocker was resolved (optional)
        """
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.BLOCKER,
            content=blocker if not resolution else f"{blocker}: {resolution}",
            metadata={"blocker": blocker, "resolution": resolution},
        )
        self.entries.append(entry)

        logger.warning(
            f"Logged blocker: {blocker}",
            extra={"resolution": resolution or "Unresolved"},
        )

        if self.auto_write:
            self.write_to_prompt_md()

    def log_context(self, context: str) -> None:
        """Log general context or notes.

        Args:
            context: General context or observation
        """
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.CONTEXT,
            content=context,
        )
        self.entries.append(entry)

        logger.debug(f"Logged context: {context}")

        if self.auto_write:
            self.write_to_prompt_md()

    def log_next_step(self, step: str) -> None:
        """Log a next step for continuation.

        Args:
            step: Next step to take
        """
        entry = JournalEntry(
            timestamp=datetime.now(),
            entry_type=EntryType.NEXT_STEP,
            content=step,
        )
        self.entries.append(entry)

        logger.info(f"Logged next step: {step}")

        if self.auto_write:
            self.write_to_prompt_md()

    def generate_summary(self) -> str:
        """Generate markdown summary of all journal entries.

        Returns:
            Formatted markdown string with all entries
        """
        if not self.entries:
            return ""

        # Group entries by type
        decisions = [e for e in self.entries if e.entry_type == EntryType.DECISION]
        patterns = [e for e in self.entries if e.entry_type == EntryType.PATTERN]
        blockers = [e for e in self.entries if e.entry_type == EntryType.BLOCKER]
        context = [e for e in self.entries if e.entry_type == EntryType.CONTEXT]
        next_steps = [e for e in self.entries if e.entry_type == EntryType.NEXT_STEP]

        # Build markdown sections
        timestamp_str = self.session_start.strftime("%Y-%m-%d %H:%M")
        lines = [f"## [{timestamp_str}] Session Notes\n"]

        if decisions:
            lines.append("### Decisions Made")
            for entry in decisions:
                lines.append(entry.to_markdown())
            lines.append("")

        if patterns:
            lines.append("### Patterns Discovered")
            for entry in patterns:
                lines.append(entry.to_markdown())
            lines.append("")

        if blockers:
            lines.append("### Blockers Encountered")
            for entry in blockers:
                lines.append(entry.to_markdown())
            lines.append("")

        if context:
            lines.append("### Context Notes")
            for entry in context:
                lines.append(entry.to_markdown())
            lines.append("")

        if next_steps:
            lines.append("### Next Steps")
            for entry in next_steps:
                lines.append(entry.to_markdown())
            lines.append("")

        return "\n".join(lines)

    def write_to_prompt_md(self) -> Path:
        """Write journal summary to docs/PROMPT.md.

        Appends timestamped section to the end of PROMPT.md.
        Creates the file if it doesn't exist.

        Returns:
            Path to the updated PROMPT.md file
        """
        prompt_path = self.project_root / "docs" / "PROMPT.md"

        # Ensure docs directory exists
        prompt_path.parent.mkdir(parents=True, exist_ok=True)

        summary = self.generate_summary()
        if not summary:
            logger.warning("No journal entries to write")
            return prompt_path

        # Read existing content
        existing_content = ""
        if prompt_path.exists():
            existing_content = prompt_path.read_text()

        # Append new section
        separator = "\n---\n\n" if existing_content and not existing_content.endswith("\n") else ""
        updated_content = existing_content + separator + summary

        # Write to file
        prompt_path.write_text(updated_content)

        logger.info(
            f"Wrote journal summary to {prompt_path}",
            extra={"entry_count": len(self.entries)},
        )

        return prompt_path

    def clear(self) -> None:
        """Clear all journal entries."""
        entry_count = len(self.entries)
        self.entries.clear()
        self.session_start = datetime.now()

        logger.info(f"Cleared {entry_count} journal entries")

    def get_entries_by_type(self, entry_type: EntryType) -> list[JournalEntry]:
        """Get all entries of a specific type.

        Args:
            entry_type: Type of entries to retrieve

        Returns:
            List of matching entries
        """
        return [e for e in self.entries if e.entry_type == entry_type]

    def get_unresolved_blockers(self) -> list[JournalEntry]:
        """Get all blockers without resolutions.

        Returns:
            List of unresolved blocker entries
        """
        return [
            e
            for e in self.entries
            if e.entry_type == EntryType.BLOCKER and not e.metadata.get("resolution")
        ]

    def export_json(self) -> dict[str, Any]:
        """Export journal to JSON format.

        Returns:
            Dictionary with session metadata and all entries
        """
        return {
            "session_start": self.session_start.isoformat(),
            "project_root": str(self.project_root),
            "entry_count": len(self.entries),
            "entries": [e.to_dict() for e in self.entries],
        }


def _extract_decisions(results: list[dict[str, Any]]) -> list[Decision]:
    """Extract decisions from task results."""
    decisions: list[Decision] = []
    for result in results:
        text_blob = " ".join(
            [
                str(result.get("prompt", "")),
                str(result.get("description", "")),
            ]
        ).lower()
        if any(keyword in text_blob for keyword in ["decid", "chose", "selected", "because"]):
            description = result.get("description") or result.get("prompt") or ""
            decisions.append(
                Decision(description=description, rationale="Extracted from task output")
            )
    return decisions


def _identify_patterns(results: list[dict[str, Any]]) -> list[Pattern]:
    """Identify common patterns across results."""
    patterns: list[Pattern] = []
    agent_counts: dict[str, int] = {}
    for result in results:
        agent = result.get("agent_type")
        if not agent:
            continue
        agent_counts[agent] = agent_counts.get(agent, 0) + 1
    for agent, count in agent_counts.items():
        if count >= 2:
            patterns.append(Pattern(description=f"Backend tasks use {agent}", occurrences=count))

    timeout_count = sum(
        1
        for r in results
        if r.get("status") == "failed" and "timeout" in str(r.get("error_message", "")).lower()
    )
    if timeout_count >= 2:
        patterns.append(
            Pattern(description="Timeout errors recurring in tasks", occurrences=timeout_count)
        )
    return patterns


def _extract_blockers(results: list[dict[str, Any]]) -> list[Blocker]:
    """Extract blockers from task results."""
    blockers: list[Blocker] = []
    for result in results:
        error = result.get("error_message")
        if not error:
            continue
        desc = str(error)
        resolved = result.get("status") != "failed" or "retry" in desc.lower()
        resolution = "Retried successfully" if resolved and "retry" in desc.lower() else None
        blockers.append(Blocker(description=desc, resolved=resolved, resolution=resolution))
    return blockers


def _generate_next_steps(results: list[dict[str, Any]]) -> list[str]:
    """Generate next steps for incomplete tasks."""
    steps: list[str] = []
    for result in results:
        status = result.get("status", "")
        task_id = result.get("task_id", "")
        title = result.get("title", "")
        if status == "failed":
            steps.append(f"Retry {task_id}: {title}")
        elif status in {"partial", "in_progress"}:
            steps.append(f"Complete {task_id}: {title}")
    return steps


def _generate_summary(entry: JournalEntry) -> str:
    """Generate summary string for journal entry."""
    total = len(entry.tasks_completed)
    completed = len([t for t in entry.tasks_completed if t.status == "completed"])
    agent_counts: dict[str, int] = {}
    for task in entry.tasks_completed:
        agent_counts[task.agent_type] = agent_counts.get(task.agent_type, 0) + 1
    dominant_agent = max(agent_counts, key=agent_counts.get) if agent_counts else "unknown"
    resolved = any(b.resolved for b in entry.blockers)
    summary = f"Completed {completed}/{total} tasks"
    summary += f" | Dominant agent: {dominant_agent}"
    if resolved:
        summary += " | Blocker resolved"
    return summary


def generate_journal(results: list[dict[str, Any]], iteration_num: int) -> JournalEntry:
    """Generate a JournalEntry from task results."""
    tasks = [
        TaskResult(
            task_id=result.get("task_id", ""),
            title=result.get("title", ""),
            status=result.get("status", ""),
            agent_type=result.get("agent_type", ""),
            duration_seconds=float(result.get("duration_seconds", 0.0)),
            error_message=result.get("error_message"),
            files_changed=list(result.get("files_changed", [])),
        )
        for result in results
    ]
    entry = JournalEntry(
        timestamp=datetime.now(),
        iteration_num=iteration_num,
        tasks_completed=tasks,
        decisions=_extract_decisions(results),
        patterns=_identify_patterns(results),
        blockers=_extract_blockers(results),
        next_steps=_generate_next_steps(results),
    )
    entry.summary = _generate_summary(entry) if results else "No tasks completed"
    return entry


def write_to_prompt_md(entry: JournalEntry, prompt_path: Path) -> bool:
    """Append a journal entry to PROMPT.md."""
    if not prompt_path.exists():
        prompt_path.write_text("# FORGE Session Notes\n\n")
    content = prompt_path.read_text()
    new_content = entry.to_markdown().rstrip() + "\n\n" + content.lstrip()
    prompt_path.write_text(new_content)
    return True
