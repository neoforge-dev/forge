"""
Session State Manager for FORGE Workflows
==========================================

Persist and restore session context across conversations.

Enables seamless handoffs between sessions by tracking:
- Active domain/project/pipeline
- Completed and pending tasks
- Checkpoints from pipeline execution
- Human decisions and deferred questions
- Files created and modified

Usage:
    from forge_harness.session_state import (
        SessionStateManager,
        SessionState,
        HumanDecision,
        create_session_state_manager,
    )

    # Create manager
    manager = create_session_state_manager(
        storage_dir=Path(".forge/sessions"),
    )

    # Create new session
    state = manager.create_session(
        domain="codeswiftr-com",
        project="interview-simulator",
    )

    # Update state during work
    state.completed_tasks.append("setup_database")
    state.files_created.append("backend/models/user.py")

    # Save state
    await manager.save_state(state)

    # Load state in new session
    loaded = await manager.load_state(state.session_id)

    # Or load most recent session for a project
    recent = await manager.load_recent_session(
        domain="codeswiftr-com",
        project="interview-simulator",
    )
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class HumanDecision:
    """Record of a human decision made during session.

    Attributes:
        question: The question that was asked
        decision: The decision made
        rationale: Optional explanation for the decision
        decided_at: When the decision was made
        decided_by: Who made the decision (if known)
        context_key: Optional key to store in session context
    """

    question: str
    decision: str
    rationale: str | None = None
    decided_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decided_by: str | None = None
    context_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "question": self.question,
            "decision": self.decision,
            "rationale": self.rationale,
            "decided_at": self.decided_at.isoformat(),
            "decided_by": self.decided_by,
            "context_key": self.context_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HumanDecision":
        """Create from dict."""
        return cls(
            question=data["question"],
            decision=data["decision"],
            rationale=data.get("rationale"),
            decided_at=datetime.fromisoformat(data["decided_at"]),
            decided_by=data.get("decided_by"),
            context_key=data.get("context_key"),
        )


@dataclass
class SessionState:
    """Complete session state for persistence.

    Attributes:
        session_id: Unique session identifier
        created_at: When the session was created
        last_activity: When the session was last active

        active_domain: Current domain being worked on
        active_project: Current project being worked on
        active_pipeline: Current pipeline being executed

        completed_tasks: List of completed task descriptions
        pending_tasks: List of pending task descriptions
        checkpoints: Pipeline checkpoints by name

        human_decisions: Decisions made by humans during session
        deferred_questions: Questions deferred for later

        files_created: List of created file paths
        files_modified: List of modified file paths

        context_data: Additional context data
        tags: Tags for categorization
    """

    session_id: str
    created_at: datetime
    last_activity: datetime

    # Context
    active_domain: str | None = None
    active_project: str | None = None
    active_pipeline: str | None = None

    # Progress
    completed_tasks: list[str] = field(default_factory=list)
    pending_tasks: list[str] = field(default_factory=list)
    checkpoints: dict[str, Any] = field(default_factory=dict)

    # Decisions
    human_decisions: list[HumanDecision] = field(default_factory=list)
    deferred_questions: list[str] = field(default_factory=list)

    # Files modified
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)

    # Context data
    context_data: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def touch(self) -> None:
        """Update last_activity to now."""
        self.last_activity = datetime.now(UTC)

    def add_task_completed(self, task: str) -> None:
        """Mark a task as completed."""
        if task in self.pending_tasks:
            self.pending_tasks.remove(task)
        if task not in self.completed_tasks:
            self.completed_tasks.append(task)
        self.touch()

    def add_task_pending(self, task: str) -> None:
        """Add a pending task."""
        if task not in self.pending_tasks and task not in self.completed_tasks:
            self.pending_tasks.append(task)
        self.touch()

    def add_decision(
        self,
        question: str,
        decision: str,
        rationale: str | None = None,
        decided_by: str | None = None,
        context_key: str | None = None,
    ) -> HumanDecision:
        """Record a human decision."""
        hd = HumanDecision(
            question=question,
            decision=decision,
            rationale=rationale,
            decided_by=decided_by,
            context_key=context_key,
        )
        self.human_decisions.append(hd)

        # Also store in context_data if key provided
        if context_key:
            self.context_data[context_key] = decision

        self.touch()
        return hd

    def add_file_created(self, path: str) -> None:
        """Record a created file."""
        if path not in self.files_created:
            self.files_created.append(path)
        self.touch()

    def add_file_modified(self, path: str) -> None:
        """Record a modified file."""
        if path not in self.files_modified and path not in self.files_created:
            self.files_modified.append(path)
        self.touch()

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of session state."""
        return {
            "session_id": self.session_id,
            "domain": self.active_domain,
            "project": self.active_project,
            "pipeline": self.active_pipeline,
            "completed_tasks": len(self.completed_tasks),
            "pending_tasks": len(self.pending_tasks),
            "decisions_made": len(self.human_decisions),
            "files_created": len(self.files_created),
            "files_modified": len(self.files_modified),
            "last_activity": self.last_activity.isoformat(),
            "duration_hours": (self.last_activity - self.created_at).total_seconds() / 3600,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "active_domain": self.active_domain,
            "active_project": self.active_project,
            "active_pipeline": self.active_pipeline,
            "completed_tasks": self.completed_tasks,
            "pending_tasks": self.pending_tasks,
            "checkpoints": self.checkpoints,
            "human_decisions": [d.to_dict() for d in self.human_decisions],
            "deferred_questions": self.deferred_questions,
            "files_created": self.files_created,
            "files_modified": self.files_modified,
            "context_data": self.context_data,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        """Create from dict."""
        return cls(
            session_id=data["session_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_activity=datetime.fromisoformat(data["last_activity"]),
            active_domain=data.get("active_domain"),
            active_project=data.get("active_project"),
            active_pipeline=data.get("active_pipeline"),
            completed_tasks=data.get("completed_tasks", []),
            pending_tasks=data.get("pending_tasks", []),
            checkpoints=data.get("checkpoints", {}),
            human_decisions=[HumanDecision.from_dict(d) for d in data.get("human_decisions", [])],
            deferred_questions=data.get("deferred_questions", []),
            files_created=data.get("files_created", []),
            files_modified=data.get("files_modified", []),
            context_data=data.get("context_data", {}),
            tags=data.get("tags", []),
        )


class SessionStateManager:
    """Manage session state persistence.

    Provides file-based persistence of session state with support for:
    - Creating new sessions
    - Saving and loading sessions
    - Merging sessions for continuations
    - Finding recent sessions by project

    Usage:
        manager = SessionStateManager(storage_dir=Path(".forge/sessions"))

        # Create new session
        state = manager.create_session(domain="codeswiftr-com", project="test")

        # Work on tasks...
        state.add_task_completed("setup_database")

        # Save state
        await manager.save_state(state)

        # Load state later
        loaded = await manager.load_state(state.session_id)
    """

    def __init__(self, storage_dir: Path):
        """
        Initialize SessionStateManager.

        Args:
            storage_dir: Directory for storing session files
        """
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def create_session(
        self,
        domain: str | None = None,
        project: str | None = None,
        pipeline: str | None = None,
        tags: list[str] | None = None,
    ) -> SessionState:
        """
        Create a new session state.

        Args:
            domain: Active domain
            project: Active project
            pipeline: Active pipeline
            tags: Tags for categorization

        Returns:
            New SessionState instance
        """
        now = datetime.now(UTC)
        session_id = str(uuid.uuid4())[:8]

        state = SessionState(
            session_id=session_id,
            created_at=now,
            last_activity=now,
            active_domain=domain,
            active_project=project,
            active_pipeline=pipeline,
            tags=tags or [],
        )

        logger.info(f"Created session {session_id} for {domain}/{project}")
        return state

    def _get_session_path(self, session_id: str) -> Path:
        """Get path for session file."""
        return self.storage_dir / f"session_{session_id}.json"

    async def save_state(self, state: SessionState) -> Path:
        """
        Save session state to persistent storage.

        Args:
            state: SessionState to save

        Returns:
            Path to saved file
        """
        state.touch()  # Update last_activity
        path = self._get_session_path(state.session_id)

        data = state.to_dict()

        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.debug(f"Saved session state to {path}")
        return path

    async def load_state(self, session_id: str) -> SessionState | None:
        """
        Load previous session state.

        Args:
            session_id: ID of session to load

        Returns:
            SessionState if found, None otherwise
        """
        path = self._get_session_path(session_id)

        if not path.exists():
            logger.warning(f"Session not found: {session_id}")
            return None

        with open(path) as f:
            data = json.load(f)

        logger.debug(f"Loaded session state from {path}")
        return SessionState.from_dict(data)

    async def delete_state(self, session_id: str) -> bool:
        """
        Delete a session state file.

        Args:
            session_id: ID of session to delete

        Returns:
            True if deleted, False if not found
        """
        path = self._get_session_path(session_id)

        if not path.exists():
            return False

        path.unlink()
        logger.info(f"Deleted session {session_id}")
        return True

    async def merge_states(
        self,
        old: SessionState,
        new: SessionState,
    ) -> SessionState:
        """
        Merge old context with new updates.

        Creates a new session that combines:
        - New session's ID and timestamps
        - Union of completed tasks
        - New session's pending tasks (replacing old)
        - Combined checkpoints
        - Combined human decisions
        - Combined file lists

        Args:
            old: Previous session state
            new: New session state to merge

        Returns:
            Merged SessionState
        """
        merged = SessionState(
            session_id=new.session_id,
            created_at=old.created_at,
            last_activity=new.last_activity,
            active_domain=new.active_domain or old.active_domain,
            active_project=new.active_project or old.active_project,
            active_pipeline=new.active_pipeline or old.active_pipeline,
            completed_tasks=list(set(old.completed_tasks + new.completed_tasks)),
            pending_tasks=new.pending_tasks,  # Use new pending tasks
            checkpoints={**old.checkpoints, **new.checkpoints},
            human_decisions=old.human_decisions + new.human_decisions,
            deferred_questions=new.deferred_questions,  # Use new deferred
            files_created=list(set(old.files_created + new.files_created)),
            files_modified=list(set(old.files_modified + new.files_modified)),
            context_data={**old.context_data, **new.context_data},
            tags=list(set(old.tags + new.tags)),
        )

        logger.info(f"Merged sessions: {old.session_id} -> {new.session_id}")
        return merged

    async def list_sessions(
        self,
        domain: str | None = None,
        project: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        List all saved sessions.

        Args:
            domain: Filter by domain
            project: Filter by project
            limit: Maximum number to return

        Returns:
            List of session summaries, sorted by last_activity (most recent first)
        """
        sessions = []

        for path in self.storage_dir.glob("session_*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)

                # Filter by domain/project if specified
                if domain and data.get("active_domain") != domain:
                    continue
                if project and data.get("active_project") != project:
                    continue

                sessions.append(
                    {
                        "session_id": data["session_id"],
                        "domain": data.get("active_domain"),
                        "project": data.get("active_project"),
                        "pipeline": data.get("active_pipeline"),
                        "created_at": data["created_at"],
                        "last_activity": data["last_activity"],
                        "completed_tasks": len(data.get("completed_tasks", [])),
                        "pending_tasks": len(data.get("pending_tasks", [])),
                        "tags": data.get("tags", []),
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to parse session {path}: {e}")

        # Sort by last_activity descending
        sessions.sort(key=lambda x: x["last_activity"], reverse=True)

        return sessions[:limit]

    async def load_recent_session(
        self,
        domain: str | None = None,
        project: str | None = None,
    ) -> SessionState | None:
        """
        Load the most recent session for a domain/project.

        Args:
            domain: Domain to filter by
            project: Project to filter by

        Returns:
            Most recent SessionState if found, None otherwise
        """
        sessions = await self.list_sessions(domain=domain, project=project, limit=1)

        if not sessions:
            return None

        return await self.load_state(sessions[0]["session_id"])

    async def continue_session(
        self,
        session_id: str,
        new_session_id: str | None = None,
    ) -> SessionState | None:
        """
        Continue from a previous session.

        Loads the session and optionally creates a new session ID
        while preserving the history.

        Args:
            session_id: ID of session to continue
            new_session_id: Optional new session ID (generates one if not provided)

        Returns:
            SessionState ready for continuation, None if not found
        """
        old_state = await self.load_state(session_id)
        if not old_state:
            return None

        if new_session_id is None:
            new_session_id = str(uuid.uuid4())[:8]

        # Create continuation
        continued = SessionState(
            session_id=new_session_id,
            created_at=datetime.now(UTC),
            last_activity=datetime.now(UTC),
            active_domain=old_state.active_domain,
            active_project=old_state.active_project,
            active_pipeline=old_state.active_pipeline,
            completed_tasks=old_state.completed_tasks.copy(),
            pending_tasks=old_state.pending_tasks.copy(),
            checkpoints=old_state.checkpoints.copy(),
            human_decisions=old_state.human_decisions.copy(),
            deferred_questions=old_state.deferred_questions.copy(),
            files_created=old_state.files_created.copy(),
            files_modified=old_state.files_modified.copy(),
            context_data={
                **old_state.context_data,
                "continued_from": session_id,
            },
            tags=old_state.tags.copy(),
        )

        logger.info(f"Continued session {session_id} as {new_session_id}")
        return continued

    def generate_handoff_prompt(self, state: SessionState) -> str:
        """
        Generate a handoff prompt for the session.

        Creates a markdown-formatted summary of the session state
        that can be used to resume work in a new conversation.

        Args:
            state: SessionState to generate prompt from

        Returns:
            Markdown-formatted handoff prompt
        """
        parts = []

        # Header
        parts.append(f"# Session Handoff: {state.session_id}")
        parts.append(f"\n**Domain:** {state.active_domain or 'Not set'}")
        parts.append(f"**Project:** {state.active_project or 'Not set'}")
        if state.active_pipeline:
            parts.append(f"**Pipeline:** {state.active_pipeline}")

        # Progress
        if state.completed_tasks:
            parts.append("\n## Completed Tasks")
            for task in state.completed_tasks:
                parts.append(f"- [x] {task}")

        if state.pending_tasks:
            parts.append("\n## Pending Tasks")
            for task in state.pending_tasks:
                parts.append(f"- [ ] {task}")

        # Decisions
        if state.human_decisions:
            parts.append("\n## Key Decisions")
            for decision in state.human_decisions:
                parts.append(f"- **{decision.question}**: {decision.decision}")
                if decision.rationale:
                    parts.append(f"  - Rationale: {decision.rationale}")

        # Deferred questions
        if state.deferred_questions:
            parts.append("\n## Deferred Questions")
            for question in state.deferred_questions:
                parts.append(f"- {question}")

        # Files
        if state.files_created or state.files_modified:
            parts.append("\n## Files Changed")
            if state.files_created:
                parts.append("\n**Created:**")
                for path in state.files_created:
                    parts.append(f"- `{path}`")
            if state.files_modified:
                parts.append("\n**Modified:**")
                for path in state.files_modified:
                    parts.append(f"- `{path}`")

        # Context data
        important_context = {
            k: v for k, v in state.context_data.items() if k not in ("continued_from",)
        }
        if important_context:
            parts.append("\n## Context")
            for key, value in important_context.items():
                parts.append(f"- **{key}**: {value}")

        # Footer
        parts.append(f"\n---\n*Session created: {state.created_at.isoformat()}*")
        parts.append(f"*Last activity: {state.last_activity.isoformat()}*")

        return "\n".join(parts)


def create_session_state_manager(
    storage_dir: Path | str | None = None,
) -> SessionStateManager:
    """
    Factory function to create SessionStateManager.

    Args:
        storage_dir: Directory for storing session files
                    (defaults to ~/.forge/sessions)

    Returns:
        Configured SessionStateManager instance
    """
    if storage_dir is None:
        storage_dir = Path.home() / ".forge" / "sessions"
    else:
        storage_dir = Path(storage_dir)

    return SessionStateManager(storage_dir=storage_dir)
