"""Session Manager Service

Provides lifecycle management for agent work sessions with JSONL-backed
persistence and thread-safe singleton access.

The SessionManager owns the authoritative read/write path for
.forge/sessions/session_*.json files, delegating to CheckpointManager for
reads when available.

Usage:
    from forge_harness.webhook_server.services.session_manager import (
        get_session_manager,
        reset_session_manager,
    )

    mgr = get_session_manager()

    session = mgr.create_session(domain="codeswiftr-com", project="interview-sim")
    mgr.add_task(session.session_id, "Implement OAuth flow")
    mgr.complete_task(session.session_id, "Implement OAuth flow")
    mgr.end_session(session.session_id)

    stats = mgr.get_stats()
    # {"active": 0, "paused": 0, "completed": 1, "total": 1}
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge_harness.checkpoint_manager import CheckpointManager, SessionInfo
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager_instance: SessionManager | None = None
_manager_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Valid session statuses
# ---------------------------------------------------------------------------

_VALID_STATUSES = frozenset(["active", "paused", "completed"])


class SessionManager:
    """
    Lifecycle manager for agent work sessions.

    Persists each session as an individual JSON file under *sessions_dir*
    (``session_{id}.json``).  An optional CheckpointManager can be injected
    for augmented read paths; the SessionManager always writes directly.

    All public methods are thread-safe via an internal RLock.

    Attributes:
        sessions_dir: Directory where session files are stored.
    """

    def __init__(
        self,
        sessions_dir: Path | str | None = None,
        checkpoint_manager: CheckpointManager | None = None,
    ) -> None:
        """
        Initialise the SessionManager.

        Args:
            sessions_dir: Directory for session JSON files.
                          Defaults to ``.forge/sessions`` in the current
                          working directory.
            checkpoint_manager: Optional CheckpointManager for augmented
                                reads.  When provided its cache is
                                invalidated on every write.
        """
        if sessions_dir is None:
            sessions_dir = Path(".forge/sessions")
        self.sessions_dir = Path(sessions_dir)
        self._checkpoint_manager = checkpoint_manager
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        """Create sessions directory if absent."""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"session_{session_id}.json"

    def _read_session_file(self, path: Path) -> SessionInfo | None:
        """Read and deserialise one session file.  Returns None on error."""
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Cannot read session file {path}: {exc}")
            return None

        try:
            started_at = datetime.now(UTC)
            if raw := data.get("started_at"):
                started_at = datetime.fromisoformat(raw)
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)

            last_activity: datetime | None = None
            if raw := data.get("last_activity"):
                last_activity = datetime.fromisoformat(raw)
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=UTC)

            return SessionInfo(
                session_id=data.get("session_id", path.stem.removeprefix("session_")),
                domain=data.get("domain", "unknown"),
                project=data.get("project", "unknown"),
                status=data.get("status", "unknown"),
                started_at=started_at,
                last_activity=last_activity,
                completed_tasks=data.get("completed_tasks", []),
                pending_tasks=data.get("pending_tasks", []),
                context=data.get("context", {}),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to parse session {path}: {exc}")
            return None

    def _write_session(self, session: SessionInfo) -> bool:
        """Serialise and write one session to disk.  Returns True on success."""
        self._ensure_dir()
        path = self._session_path(session.session_id)
        try:
            path.write_text(json.dumps(session.to_dict(), indent=2, default=str))
            if self._checkpoint_manager is not None:
                self._checkpoint_manager.invalidate_cache()
            return True
        except OSError as exc:
            logger.error(f"Failed to write session {session.session_id}: {exc}")
            return False

    def _load_session(self, session_id: str) -> SessionInfo | None:
        """Load a single session by ID from disk (no cache)."""
        path = self._session_path(session_id)
        if not path.exists():
            # Partial-match fallback (mirrors CheckpointManager behaviour)
            matches = list(self.sessions_dir.glob(f"*{session_id}*.json"))
            if not matches:
                return None
            path = matches[0]
        return self._read_session_file(path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(
        self,
        domain: str,
        project: str,
        agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionInfo:
        """
        Create and persist a new session.

        Args:
            domain:    Domain identifier (e.g. ``"codeswiftr-com"``).
            project:   Project identifier (e.g. ``"interview-sim"``).
            agent_id:  Optional agent identifier stored in context.
            metadata:  Optional extra context fields merged into context.

        Returns:
            The newly created SessionInfo with status ``"active"``.
        """
        with self._lock:
            session_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            context: dict[str, Any] = {}
            if agent_id is not None:
                context["agent_id"] = agent_id
            if metadata:
                context.update(metadata)

            session = SessionInfo(
                session_id=session_id,
                domain=domain,
                project=project,
                status="active",
                started_at=now,
                last_activity=now,
                completed_tasks=[],
                pending_tasks=[],
                context=context,
            )
            self._write_session(session)
            logger.info(f"Created session {session_id} for {domain}/{project}")
            return session

    def get_session(self, session_id: str) -> SessionInfo | None:
        """
        Retrieve a session by ID.

        Args:
            session_id: The session UUID string.

        Returns:
            SessionInfo if found, None otherwise.
        """
        with self._lock:
            return self._load_session(session_id)

    def end_session(self, session_id: str) -> SessionInfo | None:
        """
        Mark a session as completed and stamp last_activity.

        Args:
            session_id: The session UUID string.

        Returns:
            Updated SessionInfo, or None if the session does not exist.
        """
        with self._lock:
            session = self._load_session(session_id)
            if session is None:
                logger.warning(f"end_session: session {session_id} not found")
                return None
            session.status = "completed"
            session.last_activity = datetime.now(UTC)
            self._write_session(session)
            logger.info(f"Ended session {session_id}")
            return session

    def pause_session(self, session_id: str) -> SessionInfo | None:
        """
        Pause an active session.

        Args:
            session_id: The session UUID string.

        Returns:
            Updated SessionInfo, or None if the session does not exist.
        """
        with self._lock:
            session = self._load_session(session_id)
            if session is None:
                logger.warning(f"pause_session: session {session_id} not found")
                return None
            session.status = "paused"
            session.last_activity = datetime.now(UTC)
            self._write_session(session)
            logger.info(f"Paused session {session_id}")
            return session

    def resume_session(self, session_id: str) -> SessionInfo | None:
        """
        Resume a paused session (sets status back to active).

        Args:
            session_id: The session UUID string.

        Returns:
            Updated SessionInfo, or None if the session does not exist.
        """
        with self._lock:
            session = self._load_session(session_id)
            if session is None:
                logger.warning(f"resume_session: session {session_id} not found")
                return None
            session.status = "active"
            session.last_activity = datetime.now(UTC)
            self._write_session(session)
            logger.info(f"Resumed session {session_id}")
            return session

    def add_task(self, session_id: str, task_description: str) -> SessionInfo | None:
        """
        Append a task to the session's pending_tasks list.

        Duplicate descriptions are allowed (the same task may be enqueued
        multiple times).

        Args:
            session_id:       The session UUID string.
            task_description: Human-readable task description.

        Returns:
            Updated SessionInfo, or None if the session does not exist.
        """
        with self._lock:
            session = self._load_session(session_id)
            if session is None:
                logger.warning(f"add_task: session {session_id} not found")
                return None
            session.pending_tasks.append(task_description)
            session.last_activity = datetime.now(UTC)
            self._write_session(session)
            logger.debug(f"Added task to session {session_id}: {task_description!r}")
            return session

    def complete_task(self, session_id: str, task_description: str) -> SessionInfo | None:
        """
        Move a task from pending_tasks to completed_tasks.

        Removes the *first* occurrence of *task_description* from
        pending_tasks and appends it to completed_tasks.  If the task is
        not in pending_tasks it is appended to completed_tasks anyway (to
        handle out-of-band completion).

        Args:
            session_id:       The session UUID string.
            task_description: Task description to complete.

        Returns:
            Updated SessionInfo, or None if the session does not exist.
        """
        with self._lock:
            session = self._load_session(session_id)
            if session is None:
                logger.warning(f"complete_task: session {session_id} not found")
                return None
            try:
                session.pending_tasks.remove(task_description)
            except ValueError:
                logger.debug(
                    f"complete_task: {task_description!r} not in pending_tasks for {session_id}"
                )
            session.completed_tasks.append(task_description)
            session.last_activity = datetime.now(UTC)
            self._write_session(session)
            logger.debug(f"Completed task in session {session_id}: {task_description!r}")
            return session

    def list_sessions(
        self,
        status: str | None = None,
        domain: str | None = None,
        limit: int = 50,
    ) -> list[SessionInfo]:
        """
        Return a filtered, sorted list of sessions.

        Args:
            status: Optional status filter (``"active"``, ``"paused"``,
                    ``"completed"``).  None returns all statuses.
            domain: Optional domain filter.
            limit:  Maximum number of results (default 50).

        Returns:
            List of SessionInfo sorted by last_activity descending
            (most recent first).  Sessions whose files are unreadable
            are silently skipped.
        """
        with self._lock:
            if not self.sessions_dir.exists():
                return []

            sessions: list[SessionInfo] = []
            for path in sorted(self.sessions_dir.glob("session_*.json")):
                session = self._read_session_file(path)
                if session is None:
                    continue
                if status is not None and session.status != status:
                    continue
                if domain is not None and session.domain != domain:
                    continue
                sessions.append(session)

            # Sort by last_activity descending; fall back to started_at
            sessions.sort(
                key=lambda s: s.last_activity or s.started_at,
                reverse=True,
            )

            return sessions[:limit]

    def get_stats(self) -> dict[str, int]:
        """
        Return aggregate counts by status.

        Returns:
            Dictionary with keys ``"active"``, ``"paused"``,
            ``"completed"``, and ``"total"``.
        """
        with self._lock:
            counts: dict[str, int] = {"active": 0, "paused": 0, "completed": 0, "total": 0}

            if not self.sessions_dir.exists():
                return counts

            for path in self.sessions_dir.glob("session_*.json"):
                session = self._read_session_file(path)
                if session is None:
                    continue
                counts["total"] += 1
                if session.status in counts:
                    counts[session.status] += 1

            return counts


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


def get_session_manager(
    sessions_dir: Path | str | None = None,
    checkpoint_manager: CheckpointManager | None = None,
) -> SessionManager:
    """
    Return the module-level SessionManager singleton.

    Args:
        sessions_dir:        Passed only on the *first* call; ignored
                             thereafter.
        checkpoint_manager:  Passed only on the *first* call; ignored
                             thereafter.

    Returns:
        The singleton SessionManager instance.
    """
    global _manager_instance

    with _manager_lock:
        if _manager_instance is None:
            _manager_instance = SessionManager(
                sessions_dir=sessions_dir,
                checkpoint_manager=checkpoint_manager,
            )
        return _manager_instance


def reset_session_manager() -> None:
    """
    Destroy the singleton instance.

    Primarily useful in tests to ensure isolation between test cases.
    """
    global _manager_instance
    with _manager_lock:
        _manager_instance = None
