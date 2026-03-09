"""
CheckpointManager - Unified Access to Checkpoint Files
======================================================

Provides unified access to checkpoint files for both Dashboard TUI and API.
Centralizes all checkpoint reading/writing logic with caching and error handling.

Usage:
    from forge_harness.checkpoint_manager import get_checkpoint_manager

    # Get singleton instance
    manager = get_checkpoint_manager()

    # Read checkpoint data
    pipelines = manager.get_active_pipelines()
    ralph_state = manager.get_ralph_state()
    approvals = manager.get_pending_approvals()
    sessions = manager.get_active_sessions()

    # Save session state
    manager.save_session(session_data)
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)

# Singleton instance
_manager_instance: CheckpointManager | None = None
_manager_lock = threading.Lock()


@dataclass
class PipelineCheckpoint:
    """Checkpoint data for an active pipeline."""

    checkpoint_id: str
    name: str
    status: str
    current_step: str
    step_index: int
    total_steps: int
    started_at: datetime | None
    error: str | None = None
    domain: str | None = None
    project: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "name": self.name,
            "status": self.status,
            "current_step": self.current_step,
            "step_index": self.step_index,
            "total_steps": self.total_steps,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "error": self.error,
            "domain": self.domain,
            "project": self.project,
            "context": self.context,
        }


@dataclass
class RalphState:
    """State of the Ralph autonomous loop."""

    active: bool
    state: str
    current_feature: str | None
    iteration: int
    max_iterations: int
    failure_count: int
    started_at: datetime | None
    domain: str | None = None
    project: str | None = None
    checkpoint_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "active": self.active,
            "state": self.state,
            "current_feature": self.current_feature,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "failure_count": self.failure_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "domain": self.domain,
            "project": self.project,
            "checkpoint_file": self.checkpoint_file,
        }


@dataclass
class ApprovalRequest:
    """Pending approval request."""

    id: str
    type: str
    title: str
    description: str
    domain: str
    priority: str
    status: str
    tier: str
    risk_score: float
    created_at: datetime
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "domain": self.domain,
            "priority": self.priority,
            "status": self.status,
            "tier": self.tier,
            "risk_score": self.risk_score,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": self.metadata,
            "tags": self.tags,
        }


@dataclass
class SessionInfo:
    """Information about an active or recent session."""

    session_id: str
    domain: str
    project: str
    status: str
    started_at: datetime
    last_activity: datetime | None = None
    completed_tasks: list[str] = field(default_factory=list)
    pending_tasks: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "session_id": self.session_id,
            "domain": self.domain,
            "project": self.project,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "completed_tasks": self.completed_tasks,
            "pending_tasks": self.pending_tasks,
            "context": self.context,
        }


class CheckpointManager:
    """
    Unified manager for reading and writing checkpoint files.

    Provides caching to avoid repeated file system reads during
    dashboard refresh cycles.
    """

    # Cache TTL in seconds
    DEFAULT_CACHE_TTL = 5.0

    def __init__(
        self,
        forge_root: Path | str | None = None,
        cache_ttl: float = DEFAULT_CACHE_TTL,
    ) -> None:
        """
        Initialize checkpoint manager.

        Args:
            forge_root: Root directory of the FORGE project.
                       Defaults to FORGE_ROOT env var or current directory.
            cache_ttl: Time-to-live for cached data in seconds.
        """
        if forge_root is None:
            forge_root = os.environ.get("FORGE_ROOT", ".")
        self.forge_root = Path(forge_root).resolve()

        # Directory paths
        self.orchestration_dir = self.forge_root / ".forge/orchestration_checkpoints"
        self.ralph_dir = self.forge_root / ".forge/ralph_checkpoints"
        self.approvals_dir = self.forge_root / ".forge/approvals"
        self.sessions_dir = self.forge_root / ".forge/sessions"

        # Cache configuration
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()

    def _get_cached(self, key: str) -> Any | None:
        """Get cached value if still valid."""
        with self._cache_lock:
            if key in self._cache:
                timestamp, value = self._cache[key]
                if time.time() - timestamp < self._cache_ttl:
                    return value
        return None

    def _set_cached(self, key: str, value: Any) -> None:
        """Set cached value with current timestamp."""
        with self._cache_lock:
            self._cache[key] = (time.time(), value)

    def invalidate_cache(self, key: str | None = None) -> None:
        """
        Invalidate cache entries.

        Args:
            key: Specific key to invalidate, or None to clear all.
        """
        with self._cache_lock:
            if key is None:
                self._cache.clear()
            elif key in self._cache:
                del self._cache[key]

    # =========================================================================
    # Pipeline Checkpoints
    # =========================================================================

    def get_active_pipelines(self, include_completed: bool = False) -> list[PipelineCheckpoint]:
        """
        Get all active pipeline checkpoints.

        Args:
            include_completed: If True, also include completed pipelines.

        Returns:
            List of active pipeline checkpoints sorted by start time.
        """
        cache_key = f"pipelines:{include_completed}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        pipelines: list[PipelineCheckpoint] = []

        if not self.orchestration_dir.exists():
            return pipelines

        active_statuses = {"running", "paused", "waiting_human_gate"}
        if include_completed:
            active_statuses.add("completed")

        for file_path in self.orchestration_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text())
                status = data.get("status", "unknown")

                if status not in active_statuses:
                    continue

                pipeline_data = data.get("pipeline", {})
                steps = pipeline_data.get("steps", [])
                step_results = data.get("step_results", {})

                # Find current step
                current_step = "completed" if status == "completed" else "unknown"
                step_index = len(steps)
                for i, step in enumerate(steps):
                    step_name = step.get("name", f"step_{i}")
                    if step_name not in step_results:
                        current_step = step_name
                        step_index = i
                        break

                started_at = None
                if started_str := data.get("started_at"):
                    try:
                        started_at = datetime.fromisoformat(started_str)
                    except ValueError:
                        pass

                pipelines.append(
                    PipelineCheckpoint(
                        checkpoint_id=file_path.stem,
                        name=pipeline_data.get("name", file_path.stem),
                        status=status,
                        current_step=current_step,
                        step_index=step_index,
                        total_steps=len(steps),
                        started_at=started_at,
                        error=data.get("error"),
                        domain=data.get("domain"),
                        project=data.get("project"),
                        context=data.get("context", {}),
                    )
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"Failed to load checkpoint {file_path}: {e}")

        # Sort by start time (most recent first)
        pipelines.sort(
            key=lambda p: p.started_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

        self._set_cached(cache_key, pipelines)
        return pipelines

    def get_pipeline(self, checkpoint_id: str) -> PipelineCheckpoint | None:
        """Get a specific pipeline checkpoint by ID."""
        file_path = self.orchestration_dir / f"{checkpoint_id}.json"
        if not file_path.exists():
            return None

        try:
            data = json.loads(file_path.read_text())
            pipeline_data = data.get("pipeline", {})
            steps = pipeline_data.get("steps", [])
            step_results = data.get("step_results", {})

            current_step = "unknown"
            step_index = len(steps)
            for i, step in enumerate(steps):
                step_name = step.get("name", f"step_{i}")
                if step_name not in step_results:
                    current_step = step_name
                    step_index = i
                    break

            started_at = None
            if started_str := data.get("started_at"):
                try:
                    started_at = datetime.fromisoformat(started_str)
                except ValueError:
                    pass

            return PipelineCheckpoint(
                checkpoint_id=checkpoint_id,
                name=pipeline_data.get("name", checkpoint_id),
                status=data.get("status", "unknown"),
                current_step=current_step,
                step_index=step_index,
                total_steps=len(steps),
                started_at=started_at,
                error=data.get("error"),
                domain=data.get("domain"),
                project=data.get("project"),
                context=data.get("context", {}),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load pipeline checkpoint {checkpoint_id}: {e}")
            return None

    # =========================================================================
    # Ralph Loop State
    # =========================================================================

    def get_ralph_state(self) -> RalphState:
        """
        Get the current Ralph loop state.

        Returns:
            RalphState with current loop information, or idle state if not running.
        """
        cache_key = "ralph_state"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        default_state = RalphState(
            active=False,
            state="idle",
            current_feature=None,
            iteration=0,
            max_iterations=0,
            failure_count=0,
            started_at=None,
        )

        if not self.ralph_dir.exists():
            return default_state

        # Find most recent checkpoint file
        checkpoints = sorted(
            self.ralph_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not checkpoints:
            return default_state

        try:
            data = json.loads(checkpoints[0].read_text())

            started_at = None
            if started_str := data.get("started_at"):
                try:
                    started_at = datetime.fromisoformat(started_str)
                except ValueError:
                    pass

            state_value = data.get("state", "idle")
            active_states = {"running", "implementing", "testing", "waiting_approval"}

            ralph_state = RalphState(
                active=state_value in active_states,
                state=state_value,
                current_feature=data.get("current_feature"),
                iteration=data.get("iteration", 0),
                max_iterations=data.get("max_iterations", 100),
                failure_count=data.get("failure_count", 0),
                started_at=started_at,
                domain=data.get("domain"),
                project=data.get("project"),
                checkpoint_file=checkpoints[0].name,
            )

            self._set_cached(cache_key, ralph_state)
            return ralph_state

        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Failed to load Ralph checkpoint: {e}")
            return default_state

    # =========================================================================
    # Approval Requests
    # =========================================================================

    def get_pending_approvals(
        self,
        status_filter: str | None = "pending",
        limit: int | None = None,
    ) -> list[ApprovalRequest]:
        """
        Get approval requests.

        Args:
            status_filter: Filter by status ("pending", "approved", "rejected", or None for all).
            limit: Maximum number of results to return.

        Returns:
            List of approval requests sorted by priority then creation time.
        """
        cache_key = f"approvals:{status_filter}:{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        approvals: list[ApprovalRequest] = []

        if not self.approvals_dir.exists():
            return approvals

        for file_path in self.approvals_dir.glob("approval_*.json"):
            try:
                data = json.loads(file_path.read_text())

                # Skip if status doesn't match filter
                request_status = data.get("status", "pending")
                if status_filter and request_status != status_filter:
                    continue

                created_at = datetime.now(UTC)
                if created_str := data.get("created_at"):
                    try:
                        created_at = datetime.fromisoformat(created_str)
                    except ValueError:
                        pass

                expires_at = None
                if expires_str := data.get("expires_at"):
                    try:
                        expires_at = datetime.fromisoformat(expires_str)
                    except ValueError:
                        pass

                approvals.append(
                    ApprovalRequest(
                        id=data.get("id", file_path.stem),
                        type=data.get("type", "unknown"),
                        title=data.get("title", "Untitled"),
                        description=data.get("description", ""),
                        domain=data.get("domain", "unknown"),
                        priority=data.get("priority", "normal"),
                        status=request_status,
                        tier=data.get("tier", "PHONE"),
                        risk_score=data.get("risk_score", 0.5),
                        created_at=created_at,
                        expires_at=expires_at,
                        metadata=data.get("metadata", {}),
                        tags=data.get("tags", []),
                    )
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"Failed to load approval {file_path}: {e}")

        # Sort by priority (critical > high > normal > low) then by creation time
        priority_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        approvals.sort(key=lambda a: (priority_order.get(a.priority, 2), a.created_at))

        if limit:
            approvals = approvals[:limit]

        self._set_cached(cache_key, approvals)
        return approvals

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        """Get a specific approval request by ID."""
        # Search for the approval file
        for file_path in self.approvals_dir.glob(f"*{approval_id}*.json"):
            try:
                data = json.loads(file_path.read_text())
                if data.get("id") == approval_id:
                    created_at = datetime.now(UTC)
                    if created_str := data.get("created_at"):
                        try:
                            created_at = datetime.fromisoformat(created_str)
                        except ValueError:
                            pass

                    expires_at = None
                    if expires_str := data.get("expires_at"):
                        try:
                            expires_at = datetime.fromisoformat(expires_str)
                        except ValueError:
                            pass

                    return ApprovalRequest(
                        id=data.get("id", approval_id),
                        type=data.get("type", "unknown"),
                        title=data.get("title", "Untitled"),
                        description=data.get("description", ""),
                        domain=data.get("domain", "unknown"),
                        priority=data.get("priority", "normal"),
                        status=data.get("status", "pending"),
                        tier=data.get("tier", "PHONE"),
                        risk_score=data.get("risk_score", 0.5),
                        created_at=created_at,
                        expires_at=expires_at,
                        metadata=data.get("metadata", {}),
                        tags=data.get("tags", []),
                    )
            except (json.JSONDecodeError, OSError):
                continue
        return None

    def get_approval_count(self, status: str = "pending") -> int:
        """Get count of approvals with given status."""
        approvals = self.get_pending_approvals(status_filter=status)
        return len(approvals)

    # =========================================================================
    # Sessions
    # =========================================================================

    def get_active_sessions(
        self,
        domain: str | None = None,
        project: str | None = None,
        include_recent: bool = True,
        recent_hours: int = 24,
    ) -> list[SessionInfo]:
        """
        Get active and optionally recent sessions.

        Args:
            domain: Filter by domain.
            project: Filter by project.
            include_recent: Include sessions from the last `recent_hours`.
            recent_hours: How many hours back to look for recent sessions.

        Returns:
            List of sessions sorted by last activity time.
        """
        cache_key = f"sessions:{domain}:{project}:{include_recent}:{recent_hours}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        sessions: list[SessionInfo] = []

        if not self.sessions_dir.exists():
            return sessions

        cutoff_time = datetime.now(UTC) - timedelta(hours=recent_hours) if include_recent else None

        for file_path in self.sessions_dir.glob("session_*.json"):
            try:
                data = json.loads(file_path.read_text())

                # Apply filters
                if domain and data.get("domain") != domain:
                    continue
                if project and data.get("project") != project:
                    continue

                started_at = datetime.now(UTC)
                if started_str := data.get("started_at"):
                    try:
                        started_at = datetime.fromisoformat(started_str)
                    except ValueError:
                        pass

                last_activity = None
                if activity_str := data.get("last_activity"):
                    try:
                        last_activity = datetime.fromisoformat(activity_str)
                    except ValueError:
                        pass

                status = data.get("status", "unknown")

                # Filter by activity time for non-active sessions
                if status != "active" and cutoff_time:
                    activity_time = last_activity or started_at
                    if activity_time < cutoff_time:
                        continue

                sessions.append(
                    SessionInfo(
                        session_id=data.get("session_id", file_path.stem),
                        domain=data.get("domain", "unknown"),
                        project=data.get("project", "unknown"),
                        status=status,
                        started_at=started_at,
                        last_activity=last_activity,
                        completed_tasks=data.get("completed_tasks", []),
                        pending_tasks=data.get("pending_tasks", []),
                        context=data.get("context", {}),
                    )
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"Failed to load session {file_path}: {e}")

        # Sort by last activity (most recent first)
        sessions.sort(
            key=lambda s: s.last_activity or s.started_at,
            reverse=True,
        )

        self._set_cached(cache_key, sessions)
        return sessions

    def get_session(self, session_id: str) -> SessionInfo | None:
        """Get a specific session by ID."""
        file_path = self.sessions_dir / f"session_{session_id}.json"
        if not file_path.exists():
            # Try finding by partial match
            for fp in self.sessions_dir.glob(f"*{session_id}*.json"):
                file_path = fp
                break
            else:
                return None

        try:
            data = json.loads(file_path.read_text())

            started_at = datetime.now(UTC)
            if started_str := data.get("started_at"):
                try:
                    started_at = datetime.fromisoformat(started_str)
                except ValueError:
                    pass

            last_activity = None
            if activity_str := data.get("last_activity"):
                try:
                    last_activity = datetime.fromisoformat(activity_str)
                except ValueError:
                    pass

            return SessionInfo(
                session_id=data.get("session_id", session_id),
                domain=data.get("domain", "unknown"),
                project=data.get("project", "unknown"),
                status=data.get("status", "unknown"),
                started_at=started_at,
                last_activity=last_activity,
                completed_tasks=data.get("completed_tasks", []),
                pending_tasks=data.get("pending_tasks", []),
                context=data.get("context", {}),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load session {session_id}: {e}")
            return None

    def save_session(self, session: SessionInfo) -> bool:
        """
        Save or update a session.

        Args:
            session: Session information to save.

        Returns:
            True if saved successfully, False otherwise.
        """
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.sessions_dir / f"session_{session.session_id}.json"

        try:
            data = session.to_dict()
            file_path.write_text(json.dumps(data, indent=2, default=str))

            # Invalidate session cache
            self.invalidate_cache()

            logger.debug(f"Saved session {session.session_id}")
            return True
        except OSError as e:
            logger.error(f"Failed to save session {session.session_id}: {e}")
            return False

    # =========================================================================
    # Summary Statistics
    # =========================================================================

    def get_summary(self) -> dict[str, Any]:
        """
        Get a summary of all checkpoint data.

        Returns:
            Dictionary with counts and status information.
        """
        cache_key = "summary"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        pipelines = self.get_active_pipelines()
        ralph_state = self.get_ralph_state()
        pending_approvals = self.get_pending_approvals(status_filter="pending")
        active_sessions = self.get_active_sessions()

        summary = {
            "pipelines": {
                "active": len([p for p in pipelines if p.status == "running"]),
                "paused": len([p for p in pipelines if p.status == "paused"]),
                "waiting_gate": len([p for p in pipelines if p.status == "waiting_human_gate"]),
                "total": len(pipelines),
            },
            "ralph": {
                "active": ralph_state.active,
                "state": ralph_state.state,
                "current_feature": ralph_state.current_feature,
                "iteration": ralph_state.iteration,
                "failure_count": ralph_state.failure_count,
            },
            "approvals": {
                "pending": len(pending_approvals),
                "critical": len([a for a in pending_approvals if a.priority == "critical"]),
                "high": len([a for a in pending_approvals if a.priority == "high"]),
            },
            "sessions": {
                "active": len([s for s in active_sessions if s.status == "active"]),
                "recent": len(active_sessions),
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }

        self._set_cached(cache_key, summary)
        return summary


def get_checkpoint_manager(
    forge_root: Path | str | None = None,
    cache_ttl: float = CheckpointManager.DEFAULT_CACHE_TTL,
) -> CheckpointManager:
    """
    Get the singleton CheckpointManager instance.

    Args:
        forge_root: Root directory (only used on first call).
        cache_ttl: Cache TTL in seconds (only used on first call).

    Returns:
        The singleton CheckpointManager instance.
    """
    global _manager_instance

    with _manager_lock:
        if _manager_instance is None:
            _manager_instance = CheckpointManager(
                forge_root=forge_root,
                cache_ttl=cache_ttl,
            )
        return _manager_instance


def reset_checkpoint_manager() -> None:
    """Reset the singleton instance (useful for testing)."""
    global _manager_instance
    with _manager_lock:
        _manager_instance = None
