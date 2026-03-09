"""
StateSynchronizer - Ensures consistent state across all FORGE components.
=========================================================================

Bridges file-based checkpoints, state_store, and in-memory registries for
real-time consistency between Dashboard TUI, Webhook API, and background processes.

Key responsibilities:
1. Watch checkpoint directories for changes (polling-based)
2. Sync changes to state_store when files update
3. Publish events via event_bus when state changes
4. Ensure Dashboard TUI and API always see consistent state
5. Detect stale/orphaned checkpoints and mark them accordingly

Usage:
    from forge_harness.state_synchronizer import get_synchronizer, StateSynchronizer

    # Get singleton instance
    sync = get_synchronizer()

    # Start background synchronization
    await sync.start(poll_interval=5.0)

    # Register callback for state changes
    sync.on_change(lambda event_type, data: print(f"{event_type}: {data}"))

    # Manual sync (for immediate updates)
    await sync.sync_all()

    # Get unified state snapshot
    state = sync.get_state_snapshot()

    # Stop synchronization
    await sync.stop()

Checkpoint Sources:
    - .forge/approvals/*.json - Approval requests
    - .forge/orchestration_checkpoints/*.json - Pipeline checkpoints
    - .forge/ralph_checkpoints/*.json - Ralph loop state
    - .forge/sessions/*.json - Session state files
    - .forge/mvp_status/*.json - MVP check results

Events Published:
    - approval.created, approval.updated, approval.expired
    - pipeline.started, pipeline.step_completed, pipeline.completed, pipeline.failed
    - ralph.started, ralph.iteration, ralph.completed, ralph.failed
    - session.registered, session.updated, session.heartbeat, session.stale
    - sync.complete - Emitted after each sync cycle
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Event Types
# =============================================================================


class SyncEventType(str, Enum):
    """Types of synchronization events."""

    # Approval events
    APPROVAL_CREATED = "approval.created"
    APPROVAL_UPDATED = "approval.updated"
    APPROVAL_EXPIRED = "approval.expired"

    # Pipeline events
    PIPELINE_STARTED = "pipeline.started"
    PIPELINE_STEP_COMPLETED = "pipeline.step_completed"
    PIPELINE_COMPLETED = "pipeline.completed"
    PIPELINE_FAILED = "pipeline.failed"
    PIPELINE_PAUSED = "pipeline.paused"

    # Ralph loop events
    RALPH_STARTED = "ralph.started"
    RALPH_ITERATION = "ralph.iteration"
    RALPH_COMPLETED = "ralph.completed"
    RALPH_FAILED = "ralph.failed"

    # Session events
    SESSION_REGISTERED = "session.registered"
    SESSION_UPDATED = "session.updated"
    SESSION_HEARTBEAT = "session.heartbeat"
    SESSION_STALE = "session.stale"
    SESSION_REMOVED = "session.removed"

    # Sync events
    SYNC_COMPLETE = "sync.complete"
    SYNC_ERROR = "sync.error"


@dataclass
class SyncEvent:
    """An event emitted during state synchronization."""

    event_type: SyncEventType
    timestamp: datetime
    source: str  # File path or source identifier
    data: dict[str, Any]
    previous_data: dict[str, Any] | None = None  # For detecting changes


# =============================================================================
# State Snapshots
# =============================================================================


@dataclass
class ApprovalState:
    """State of an approval request."""

    id: str
    type: str
    status: str
    title: str
    domain: str
    project: str | None
    priority: str
    tier: str
    risk_score: float
    created_at: datetime
    expires_at: datetime | None
    approved_by: str | None
    resolved_at: datetime | None


@dataclass
class PipelineState:
    """State of a pipeline execution."""

    checkpoint_id: str
    pipeline_name: str
    status: str
    current_step: str
    step_index: int
    total_steps: int
    started_at: datetime
    updated_at: datetime
    error: str | None


@dataclass
class RalphState:
    """State of the Ralph loop."""

    active: bool
    state: str
    current_feature: str | None
    iteration: int
    max_iterations: int
    failure_count: int
    started_at: datetime | None
    updated_at: datetime | None


@dataclass
class SessionState:
    """State of an agent session."""

    session_id: str
    agent_type: str
    agent_role: str
    domain: str
    project: str
    status: str
    current_task: str | None
    registered_at: datetime
    last_heartbeat: datetime
    is_stale: bool


@dataclass
class StateSnapshot:
    """Complete state snapshot at a point in time."""

    timestamp: datetime
    approvals: list[ApprovalState]
    pipelines: list[PipelineState]
    ralph: RalphState | None
    sessions: list[SessionState]

    # Summary stats
    pending_approvals: int = 0
    active_pipelines: int = 0
    active_sessions: int = 0
    stale_sessions: int = 0


# =============================================================================
# File Hash Tracking
# =============================================================================


@dataclass
class FileTracker:
    """Tracks file modification state to detect changes."""

    path: Path
    last_hash: str | None = None
    last_modified: float | None = None
    last_data: dict[str, Any] | None = None

    def compute_hash(self) -> str | None:
        """Compute hash of file contents."""
        if not self.path.exists():
            return None
        try:
            content = self.path.read_bytes()
            return hashlib.md5(content).hexdigest()
        except OSError as e:
            logger.debug(f"Error reading {self.path}: {e}")
            return None

    def has_changed(self) -> bool:
        """Check if file has changed since last check."""
        if not self.path.exists():
            # File deleted is a change if we had tracked it
            return self.last_hash is not None

        try:
            mtime = self.path.stat().st_mtime
            # Quick check: modification time
            if self.last_modified is not None and mtime == self.last_modified:
                return False

            # Full check: content hash
            current_hash = self.compute_hash()
            return current_hash != self.last_hash
        except OSError:
            return False

    def update(self, data: dict[str, Any] | None = None) -> None:
        """Update tracking state after processing."""
        if self.path.exists():
            try:
                self.last_modified = self.path.stat().st_mtime
                self.last_hash = self.compute_hash()
                if data is not None:
                    self.last_data = data
            except OSError:
                pass
        else:
            self.last_hash = None
            self.last_modified = None
            self.last_data = None


# =============================================================================
# Event Bus Protocol
# =============================================================================


class EventBusProtocol(Protocol):
    """Protocol for event bus implementations."""

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to subscribers."""
        ...


# =============================================================================
# State Synchronizer
# =============================================================================


class StateSynchronizer:
    """
    Synchronizes state between file-based checkpoints and state_store.

    Provides a unified view of all FORGE state sources for consistent
    access by Dashboard TUI, Webhook API, and background processes.
    """

    # Staleness threshold for sessions (5 minutes)
    STALE_THRESHOLD_SECONDS = 300

    def __init__(
        self,
        forge_root: Path,
        state_store: Any | None = None,
        event_bus: EventBusProtocol | None = None,
    ):
        """
        Initialize StateSynchronizer.

        Args:
            forge_root: FORGE project root directory
            state_store: Optional state store instance (auto-created if None)
            event_bus: Optional event bus for publishing events
        """
        self.forge_root = Path(forge_root)

        # Checkpoint directories
        self.approval_dir = self.forge_root / ".forge/approvals"
        self.pipeline_dir = self.forge_root / ".forge/orchestration_checkpoints"
        self.ralph_dir = self.forge_root / ".forge/ralph_checkpoints"
        self.session_dir = self.forge_root / ".forge/sessions"
        self.mvp_status_dir = self.forge_root / ".forge/mvp_status"

        # State store (lazy-loaded)
        self._state_store = state_store
        self._state_store_connected = False

        # Event bus for publishing changes
        self._event_bus = event_bus

        # File tracking for change detection
        self._file_trackers: dict[str, FileTracker] = {}

        # Callback registry
        self._callbacks: list[Callable[[SyncEventType, dict[str, Any]], None]] = []
        self._async_callbacks: list[Callable[[SyncEventType, dict[str, Any]], Any]] = []

        # Running state
        self._running = False
        self._sync_task: asyncio.Task | None = None
        self._sync_lock = asyncio.Lock()

        # Cached state snapshot
        self._cached_snapshot: StateSnapshot | None = None
        self._snapshot_timestamp: datetime | None = None

        # Pending state store syncs (populated by blocking methods, consumed async)
        self._pending_approval_syncs: list[dict[str, Any]] = []
        self._pending_session_syncs: list[dict[str, Any]] = []

        # Statistics
        self._sync_count = 0
        self._last_sync_duration: float = 0.0
        self._errors: list[tuple[datetime, str]] = []

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self, poll_interval: float = 5.0) -> None:
        """
        Start background synchronization loop.

        Args:
            poll_interval: Seconds between sync cycles
        """
        if self._running:
            logger.warning("StateSynchronizer already running")
            return

        self._running = True
        self._ensure_directories()
        await self._connect_state_store()

        logger.info(
            f"Starting StateSynchronizer with poll_interval={poll_interval}s",
            extra={"forge_root": str(self.forge_root)},
        )

        self._sync_task = asyncio.create_task(
            self._sync_loop(poll_interval),
            name="state_synchronizer_loop",
        )

    async def stop(self) -> None:
        """Stop synchronization loop."""
        self._running = False

        if self._sync_task is not None:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        logger.info("StateSynchronizer stopped")

    def _ensure_directories(self) -> None:
        """Ensure checkpoint directories exist."""
        for dir_path in [
            self.approval_dir,
            self.pipeline_dir,
            self.ralph_dir,
            self.session_dir,
            self.mvp_status_dir,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)

    async def _connect_state_store(self) -> None:
        """Connect to state store if available."""
        if self._state_store is not None:
            self._state_store_connected = True
            return

        try:
            from .state_store import StateStore

            self._state_store = StateStore()
            store_type = self._state_store.connect()
            self._state_store_connected = store_type != "none"
            logger.info(f"State store connected: {store_type}")
        except ImportError:
            logger.debug("State store not available")
            self._state_store_connected = False
        except Exception as e:
            logger.warning(f"Failed to connect state store: {e}")
            self._state_store_connected = False

    # =========================================================================
    # Sync Loop
    # =========================================================================

    async def _sync_loop(self, poll_interval: float) -> None:
        """Background sync loop."""
        while self._running:
            try:
                await self.sync_all()
            except Exception as e:
                logger.error(f"Sync error: {e}")
                self._add_error(f"Sync loop error: {e}")

            await asyncio.sleep(poll_interval)

    async def sync_all(self) -> StateSnapshot:
        """
        Perform full synchronization of all checkpoint sources.

        All file I/O (glob, read, stat, hash) is offloaded to a thread
        so the async event loop stays responsive for HTTP and SSE traffic.

        Returns:
            StateSnapshot with current unified state
        """
        async with self._sync_lock:
            start_time = asyncio.get_event_loop().time()
            events: list[SyncEvent] = []

            try:
                # Offload blocking file I/O to a thread
                approval_events = await asyncio.to_thread(self._sync_approvals_blocking)
                events.extend(approval_events)

                pipeline_events = await asyncio.to_thread(self._sync_pipelines_blocking)
                events.extend(pipeline_events)

                ralph_events = await asyncio.to_thread(self._sync_ralph_blocking)
                events.extend(ralph_events)

                session_events = await asyncio.to_thread(self._sync_sessions_blocking)
                events.extend(session_events)

                # Drain deferred state store syncs (async-safe)
                if self._state_store_connected and self._state_store:
                    for approval_data in self._pending_approval_syncs:
                        await self._sync_approval_to_store(approval_data)
                    for session_data in self._pending_session_syncs:
                        await self._sync_session_to_store(session_data)
                self._pending_approval_syncs.clear()
                self._pending_session_syncs.clear()

                # Build state snapshot
                snapshot = self._build_snapshot()
                self._cached_snapshot = snapshot
                self._snapshot_timestamp = datetime.now(UTC)

                # Publish events
                await self._publish_events(events)

                # Emit sync complete event
                await self._emit_event(
                    SyncEventType.SYNC_COMPLETE,
                    {
                        "events_count": len(events),
                        "pending_approvals": snapshot.pending_approvals,
                        "active_pipelines": snapshot.active_pipelines,
                        "active_sessions": snapshot.active_sessions,
                    },
                )

                # Update stats
                self._sync_count += 1
                self._last_sync_duration = asyncio.get_event_loop().time() - start_time

                return snapshot

            except Exception as e:
                logger.error(f"Sync failed: {e}")
                self._add_error(str(e))
                await self._emit_event(
                    SyncEventType.SYNC_ERROR,
                    {"error": str(e)},
                )
                raise

    # =========================================================================
    # Async wrappers (used by tests and external callers)
    # =========================================================================

    async def _sync_approvals(self) -> list[SyncEvent]:
        """Async wrapper — offloads to thread."""
        return await asyncio.to_thread(self._sync_approvals_blocking)

    async def _sync_pipelines(self) -> list[SyncEvent]:
        """Async wrapper — offloads to thread."""
        return await asyncio.to_thread(self._sync_pipelines_blocking)

    async def _sync_ralph(self) -> list[SyncEvent]:
        """Async wrapper — offloads to thread."""
        return await asyncio.to_thread(self._sync_ralph_blocking)

    async def _sync_sessions(self) -> list[SyncEvent]:
        """Async wrapper — offloads to thread."""
        return await asyncio.to_thread(self._sync_sessions_blocking)

    # =========================================================================
    # Approval Synchronization
    # =========================================================================

    def _sync_approvals_blocking(self) -> list[SyncEvent]:
        """Sync approval checkpoint files (blocking — runs in thread)."""
        events: list[SyncEvent] = []

        if not self.approval_dir.exists():
            return events

        # Track current files
        current_files = set()

        for file_path in self.approval_dir.glob("approval_*.json"):
            current_files.add(str(file_path))
            tracker_key = f"approval:{file_path}"

            # Get or create tracker
            tracker = self._file_trackers.get(tracker_key)
            if tracker is None:
                tracker = FileTracker(path=file_path)
                self._file_trackers[tracker_key] = tracker

            # Check for changes
            if not tracker.has_changed():
                continue

            try:
                data = json.loads(file_path.read_text())
                previous_data = tracker.last_data

                # Determine event type
                if previous_data is None:
                    event_type = SyncEventType.APPROVAL_CREATED
                elif data.get("status") != previous_data.get("status"):
                    if data.get("status") == "expired":
                        event_type = SyncEventType.APPROVAL_EXPIRED
                    else:
                        event_type = SyncEventType.APPROVAL_UPDATED
                else:
                    event_type = SyncEventType.APPROVAL_UPDATED

                event = SyncEvent(
                    event_type=event_type,
                    timestamp=datetime.now(UTC),
                    source=str(file_path),
                    data=data,
                    previous_data=previous_data,
                )
                events.append(event)

                # State store sync deferred to caller (needs async)
                self._pending_approval_syncs.append(data)

                tracker.update(data)

            except (OSError, json.JSONDecodeError) as e:
                logger.debug(f"Error reading approval {file_path}: {e}")

        # Detect deleted files
        for tracker_key in list(self._file_trackers.keys()):
            if tracker_key.startswith("approval:"):
                file_path = tracker_key.replace("approval:", "")
                if file_path not in current_files and self._file_trackers[tracker_key].last_data:
                    # File was deleted - emit event
                    events.append(
                        SyncEvent(
                            event_type=SyncEventType.APPROVAL_UPDATED,
                            timestamp=datetime.now(UTC),
                            source=file_path,
                            data={"status": "deleted"},
                            previous_data=self._file_trackers[tracker_key].last_data,
                        )
                    )
                    del self._file_trackers[tracker_key]

        return events

    async def _sync_approval_to_store(self, data: dict[str, Any]) -> None:
        """Sync approval data to state store."""
        # State store integration - publish status change
        if self._state_store and hasattr(self._state_store, "publish_status_change"):
            try:
                self._state_store.publish_status_change(
                    f"approval:{data.get('id')}",
                    {
                        "type": "approval",
                        "status": data.get("status"),
                        "id": data.get("id"),
                    },
                )
            except Exception as e:
                logger.debug(f"Failed to publish approval to store: {e}")

    # =========================================================================
    # Pipeline Synchronization
    # =========================================================================

    def _sync_pipelines_blocking(self) -> list[SyncEvent]:
        """Sync pipeline checkpoint files (blocking — runs in thread)."""
        events: list[SyncEvent] = []

        if not self.pipeline_dir.exists():
            return events

        for file_path in self.pipeline_dir.glob("*.json"):
            tracker_key = f"pipeline:{file_path}"

            tracker = self._file_trackers.get(tracker_key)
            if tracker is None:
                tracker = FileTracker(path=file_path)
                self._file_trackers[tracker_key] = tracker

            if not tracker.has_changed():
                continue

            try:
                data = json.loads(file_path.read_text())
                previous_data = tracker.last_data

                # Determine event type based on status change
                current_status = data.get("status", "unknown")
                previous_data.get("status") if previous_data else None

                if previous_data is None:
                    event_type = SyncEventType.PIPELINE_STARTED
                elif current_status == "completed":
                    event_type = SyncEventType.PIPELINE_COMPLETED
                elif current_status == "failed":
                    event_type = SyncEventType.PIPELINE_FAILED
                elif current_status in ("paused", "waiting_human_gate"):
                    event_type = SyncEventType.PIPELINE_PAUSED
                elif data.get("current_step_index") != previous_data.get("current_step_index"):
                    event_type = SyncEventType.PIPELINE_STEP_COMPLETED
                else:
                    event_type = SyncEventType.PIPELINE_STEP_COMPLETED

                event = SyncEvent(
                    event_type=event_type,
                    timestamp=datetime.now(UTC),
                    source=str(file_path),
                    data=data,
                    previous_data=previous_data,
                )
                events.append(event)

                tracker.update(data)

            except (OSError, json.JSONDecodeError) as e:
                logger.debug(f"Error reading pipeline {file_path}: {e}")

        return events

    # =========================================================================
    # Ralph Loop Synchronization
    # =========================================================================

    def _sync_ralph_blocking(self) -> list[SyncEvent]:
        """Sync Ralph loop checkpoint files (blocking — runs in thread)."""
        events: list[SyncEvent] = []

        if not self.ralph_dir.exists():
            return events

        # Find most recent checkpoint
        checkpoints = sorted(
            self.ralph_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )

        if not checkpoints:
            return events

        file_path = checkpoints[0]
        tracker_key = "ralph:latest"

        tracker = self._file_trackers.get(tracker_key)
        if tracker is None:
            tracker = FileTracker(path=file_path)
            self._file_trackers[tracker_key] = tracker
        else:
            # Update path in case newest checkpoint changed
            tracker.path = file_path

        if not tracker.has_changed():
            return events

        try:
            data = json.loads(file_path.read_text())
            previous_data = tracker.last_data

            # Determine event type
            current_state = data.get("state", "idle")
            previous_state = previous_data.get("state") if previous_data else None

            if previous_data is None or previous_state == "idle" and current_state != "idle":
                event_type = SyncEventType.RALPH_STARTED
            elif current_state == "completed":
                event_type = SyncEventType.RALPH_COMPLETED
            elif current_state == "failed":
                event_type = SyncEventType.RALPH_FAILED
            elif data.get("iteration", 0) != (
                previous_data.get("iteration", 0) if previous_data else 0
            ):
                event_type = SyncEventType.RALPH_ITERATION
            else:
                event_type = SyncEventType.RALPH_ITERATION

            event = SyncEvent(
                event_type=event_type,
                timestamp=datetime.now(UTC),
                source=str(file_path),
                data=data,
                previous_data=previous_data,
            )
            events.append(event)

            tracker.update(data)

        except (OSError, json.JSONDecodeError) as e:
            logger.debug(f"Error reading Ralph checkpoint {file_path}: {e}")

        return events

    # =========================================================================
    # Session Synchronization
    # =========================================================================

    def _sync_sessions_blocking(self) -> list[SyncEvent]:
        """Sync session state files and detect stale sessions (blocking — runs in thread)."""
        events: list[SyncEvent] = []

        if not self.session_dir.exists():
            return events

        now = datetime.now(UTC)
        current_files = set()

        for file_path in self.session_dir.glob("*.json"):
            current_files.add(str(file_path))
            tracker_key = f"session:{file_path}"

            tracker = self._file_trackers.get(tracker_key)
            if tracker is None:
                tracker = FileTracker(path=file_path)
                self._file_trackers[tracker_key] = tracker

            try:
                data = json.loads(file_path.read_text())
                previous_data = tracker.last_data

                # Check staleness
                last_heartbeat_str = data.get("last_heartbeat")
                if last_heartbeat_str:
                    last_heartbeat = datetime.fromisoformat(last_heartbeat_str)
                    if last_heartbeat.tzinfo is None:
                        last_heartbeat = last_heartbeat.replace(tzinfo=UTC)
                    age_seconds = (now - last_heartbeat).total_seconds()
                    is_stale = age_seconds > self.STALE_THRESHOLD_SECONDS
                else:
                    is_stale = True

                # Update data with staleness flag
                data["is_stale"] = is_stale

                # Determine event type
                if not tracker.has_changed() and previous_data:
                    # Check if staleness changed
                    was_stale = previous_data.get("is_stale", False)
                    if is_stale and not was_stale:
                        event = SyncEvent(
                            event_type=SyncEventType.SESSION_STALE,
                            timestamp=now,
                            source=str(file_path),
                            data=data,
                            previous_data=previous_data,
                        )
                        events.append(event)
                        tracker.update(data)
                    continue

                if previous_data is None:
                    event_type = SyncEventType.SESSION_REGISTERED
                elif is_stale and not previous_data.get("is_stale", False):
                    event_type = SyncEventType.SESSION_STALE
                elif data.get("status") != previous_data.get("status"):
                    event_type = SyncEventType.SESSION_UPDATED
                else:
                    event_type = SyncEventType.SESSION_HEARTBEAT

                event = SyncEvent(
                    event_type=event_type,
                    timestamp=now,
                    source=str(file_path),
                    data=data,
                    previous_data=previous_data,
                )
                events.append(event)

                # State store sync deferred to caller (needs async)
                self._pending_session_syncs.append(data)

                tracker.update(data)

            except (OSError, json.JSONDecodeError) as e:
                logger.debug(f"Error reading session {file_path}: {e}")

        # Detect removed sessions
        for tracker_key in list(self._file_trackers.keys()):
            if tracker_key.startswith("session:"):
                file_path = tracker_key.replace("session:", "")
                if file_path not in current_files and self._file_trackers[tracker_key].last_data:
                    events.append(
                        SyncEvent(
                            event_type=SyncEventType.SESSION_REMOVED,
                            timestamp=now,
                            source=file_path,
                            data={"status": "removed"},
                            previous_data=self._file_trackers[tracker_key].last_data,
                        )
                    )
                    del self._file_trackers[tracker_key]

        return events

    async def _sync_session_to_store(self, data: dict[str, Any]) -> None:
        """Sync session data to state store."""
        if not self._state_store:
            return

        try:
            from .state_store import AgentRole, AgentSession, AgentStatus, AgentType

            session = AgentSession(
                session_id=data.get("session_id", ""),
                agent_type=AgentType(data.get("agent_type", "other")),
                agent_role=AgentRole(data.get("agent_role", "builder")),
                domain=data.get("domain", ""),
                project=data.get("project", ""),
                status=AgentStatus(data.get("status", "active")),
                current_task=data.get("current_task"),
                registered_at=datetime.fromisoformat(
                    data.get("registered_at", datetime.now(UTC).isoformat())
                ),
                last_heartbeat=datetime.fromisoformat(
                    data.get("last_heartbeat", datetime.now(UTC).isoformat())
                ),
            )
            self._state_store.register_agent(session)
        except Exception as e:
            logger.debug(f"Failed to sync session to store: {e}")

    # =========================================================================
    # Event Publishing
    # =========================================================================

    async def _publish_events(self, events: list[SyncEvent]) -> None:
        """Publish events to event bus and callbacks."""
        for event in events:
            await self._emit_event(event.event_type, event.data)

    async def _emit_event(
        self,
        event_type: SyncEventType,
        data: dict[str, Any],
    ) -> None:
        """Emit event to all registered handlers."""
        event_type_str = event_type.value

        # Publish to event bus
        if self._event_bus is not None:
            try:
                await self._event_bus.publish(event_type_str, data)
            except Exception as e:
                logger.debug(f"Failed to publish to event bus: {e}")

        # Call sync callbacks
        for callback in self._callbacks:
            try:
                callback(event_type, data)
            except Exception as e:
                logger.debug(f"Callback error: {e}")

        # Call async callbacks
        for callback in self._async_callbacks:
            try:
                result = callback(event_type, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.debug(f"Async callback error: {e}")

    # =========================================================================
    # State Snapshot
    # =========================================================================

    def _build_snapshot(self) -> StateSnapshot:
        """Build current state snapshot from tracked files."""
        now = datetime.now(UTC)
        approvals: list[ApprovalState] = []
        pipelines: list[PipelineState] = []
        sessions: list[SessionState] = []
        ralph: RalphState | None = None

        # Build approvals list
        for tracker_key, tracker in self._file_trackers.items():
            if tracker_key.startswith("approval:") and tracker.last_data:
                data = tracker.last_data
                if data.get("status") == "pending":
                    created_at_str = data.get("created_at")
                    created_at = datetime.fromisoformat(created_at_str) if created_at_str else now
                    expires_at_str = data.get("expires_at")
                    expires_at = datetime.fromisoformat(expires_at_str) if expires_at_str else None

                    approvals.append(
                        ApprovalState(
                            id=data.get("id", ""),
                            type=data.get("type", "unknown"),
                            status=data.get("status", "pending"),
                            title=data.get("title", ""),
                            domain=data.get("domain", ""),
                            project=data.get("metadata", {}).get("project"),
                            priority=data.get("priority", "normal"),
                            tier=data.get("tier", "PHONE"),
                            risk_score=data.get("risk_score", 0.5),
                            created_at=created_at,
                            expires_at=expires_at,
                            approved_by=data.get("approved_by"),
                            resolved_at=None,
                        )
                    )

        # Build pipelines list
        for tracker_key, tracker in self._file_trackers.items():
            if tracker_key.startswith("pipeline:") and tracker.last_data:
                data = tracker.last_data
                status = data.get("status", "unknown")
                if status in ("running", "paused", "waiting_human_gate"):
                    pipeline_data = data.get("pipeline", {})
                    steps = pipeline_data.get("steps", [])
                    step_results = data.get("step_results", {})

                    # Find current step
                    current_step = "unknown"
                    step_index = 0
                    for i, step in enumerate(steps):
                        step_name = step.get("name", f"step_{i}")
                        if step_name not in step_results:
                            current_step = step_name
                            step_index = i
                            break

                    started_at_str = data.get("started_at")
                    started_at = datetime.fromisoformat(started_at_str) if started_at_str else now
                    updated_at_str = data.get("updated_at")
                    updated_at = (
                        datetime.fromisoformat(updated_at_str) if updated_at_str else started_at
                    )

                    pipelines.append(
                        PipelineState(
                            checkpoint_id=data.get("checkpoint_id", tracker.path.stem),
                            pipeline_name=pipeline_data.get("name", "unknown"),
                            status=status,
                            current_step=current_step,
                            step_index=step_index,
                            total_steps=len(steps),
                            started_at=started_at,
                            updated_at=updated_at,
                            error=data.get("error"),
                        )
                    )

        # Build Ralph state
        ralph_tracker = self._file_trackers.get("ralph:latest")
        if ralph_tracker and ralph_tracker.last_data:
            data = ralph_tracker.last_data
            started_at_str = data.get("started_at")
            started_at = datetime.fromisoformat(started_at_str) if started_at_str else None
            updated_at_str = data.get("updated_at")
            updated_at = datetime.fromisoformat(updated_at_str) if updated_at_str else None

            state = data.get("state", "idle")
            ralph = RalphState(
                active=state in ("running", "implementing", "testing"),
                state=state,
                current_feature=data.get("current_feature"),
                iteration=data.get("iteration", 0),
                max_iterations=data.get("max_iterations", 100),
                failure_count=data.get("failure_count", 0),
                started_at=started_at,
                updated_at=updated_at,
            )

        # Build sessions list
        for tracker_key, tracker in self._file_trackers.items():
            if tracker_key.startswith("session:") and tracker.last_data:
                data = tracker.last_data
                registered_at_str = data.get("registered_at")
                registered_at = (
                    datetime.fromisoformat(registered_at_str) if registered_at_str else now
                )
                last_heartbeat_str = data.get("last_heartbeat")
                last_heartbeat = (
                    datetime.fromisoformat(last_heartbeat_str)
                    if last_heartbeat_str
                    else registered_at
                )

                sessions.append(
                    SessionState(
                        session_id=data.get("session_id", ""),
                        agent_type=data.get("agent_type", "unknown"),
                        agent_role=data.get("agent_role", "unknown"),
                        domain=data.get("domain", ""),
                        project=data.get("project", ""),
                        status=data.get("status", "unknown"),
                        current_task=data.get("current_task"),
                        registered_at=registered_at,
                        last_heartbeat=last_heartbeat,
                        is_stale=data.get("is_stale", False),
                    )
                )

        # Calculate summary stats
        pending_approvals = len([a for a in approvals if a.status == "pending"])
        active_pipelines = len(pipelines)
        active_sessions = len([s for s in sessions if not s.is_stale])
        stale_sessions = len([s for s in sessions if s.is_stale])

        return StateSnapshot(
            timestamp=now,
            approvals=approvals,
            pipelines=pipelines,
            ralph=ralph,
            sessions=sessions,
            pending_approvals=pending_approvals,
            active_pipelines=active_pipelines,
            active_sessions=active_sessions,
            stale_sessions=stale_sessions,
        )

    def get_state_snapshot(self) -> StateSnapshot:
        """Get current cached state snapshot or build new one."""
        if self._cached_snapshot is None:
            self._cached_snapshot = self._build_snapshot()
            self._snapshot_timestamp = datetime.now(UTC)
        return self._cached_snapshot

    # =========================================================================
    # Callback Registration
    # =========================================================================

    def on_change(
        self,
        callback: Callable[[SyncEventType, dict[str, Any]], None],
    ) -> None:
        """
        Register callback for state changes.

        Args:
            callback: Function called with (event_type, data) on each change
        """
        self._callbacks.append(callback)

    def on_change_async(
        self,
        callback: Callable[[SyncEventType, dict[str, Any]], Any],
    ) -> None:
        """
        Register async callback for state changes.

        Args:
            callback: Async function called with (event_type, data) on each change
        """
        self._async_callbacks.append(callback)

    def remove_callback(
        self,
        callback: Callable[[SyncEventType, dict[str, Any]], None],
    ) -> None:
        """Remove a registered callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
        if callback in self._async_callbacks:
            self._async_callbacks.remove(callback)

    # =========================================================================
    # Error Tracking
    # =========================================================================

    def _add_error(self, error: str) -> None:
        """Add error to recent errors list."""
        self._errors.append((datetime.now(UTC), error))
        # Keep only last 50 errors
        if len(self._errors) > 50:
            self._errors = self._errors[-50:]

    def get_errors(self, limit: int = 10) -> list[tuple[datetime, str]]:
        """Get recent errors."""
        return self._errors[-limit:]

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict[str, Any]:
        """Get synchronizer statistics."""
        return {
            "running": self._running,
            "sync_count": self._sync_count,
            "last_sync_duration_ms": self._last_sync_duration * 1000,
            "tracked_files": len(self._file_trackers),
            "state_store_connected": self._state_store_connected,
            "callback_count": len(self._callbacks) + len(self._async_callbacks),
            "error_count": len(self._errors),
            "snapshot_age_seconds": (
                (datetime.now(UTC) - self._snapshot_timestamp).total_seconds()
                if self._snapshot_timestamp
                else None
            ),
        }

    # =========================================================================
    # Direct State Access (for Dashboard/API)
    # =========================================================================

    def get_pending_approvals(self) -> list[ApprovalState]:
        """Get list of pending approvals."""
        snapshot = self.get_state_snapshot()
        return [a for a in snapshot.approvals if a.status == "pending"]

    def get_active_pipelines(self) -> list[PipelineState]:
        """Get list of active pipelines."""
        snapshot = self.get_state_snapshot()
        return snapshot.pipelines

    def get_ralph_status(self) -> RalphState | None:
        """Get Ralph loop status."""
        snapshot = self.get_state_snapshot()
        return snapshot.ralph

    def get_active_sessions(self, include_stale: bool = False) -> list[SessionState]:
        """Get list of active sessions."""
        snapshot = self.get_state_snapshot()
        if include_stale:
            return snapshot.sessions
        return [s for s in snapshot.sessions if not s.is_stale]


# =============================================================================
# Global Singleton
# =============================================================================

_synchronizer: StateSynchronizer | None = None


def get_synchronizer(forge_root: Path | None = None) -> StateSynchronizer:
    """
    Get or create the global StateSynchronizer instance.

    Args:
        forge_root: FORGE root directory (default: current directory)

    Returns:
        StateSynchronizer singleton instance
    """
    global _synchronizer

    if _synchronizer is None:
        root = forge_root or Path.cwd()
        # Check for FORGE_ROOT environment variable
        env_root = os.environ.get("FORGE_ROOT")
        if env_root:
            root = Path(env_root)
        _synchronizer = StateSynchronizer(root)

    return _synchronizer


def get_state_synchronizer(forge_root: Path | None = None) -> StateSynchronizer:
    """Alias for get_synchronizer (used by health API).

    Args:
        forge_root: FORGE root directory (default: current directory)

    Returns:
        StateSynchronizer singleton instance
    """
    return get_synchronizer(forge_root)


def reset_synchronizer() -> None:
    """Reset the global synchronizer instance (mainly for testing)."""
    global _synchronizer
    _synchronizer = None


# =============================================================================
# Factory Functions
# =============================================================================


def create_synchronizer(
    forge_root: Path | None = None,
    state_store: Any | None = None,
    event_bus: EventBusProtocol | None = None,
) -> StateSynchronizer:
    """
    Create a new StateSynchronizer instance.

    Args:
        forge_root: FORGE root directory
        state_store: Optional state store instance
        event_bus: Optional event bus for publishing events

    Returns:
        New StateSynchronizer instance
    """
    root = forge_root or Path.cwd()
    return StateSynchronizer(root, state_store=state_store, event_bus=event_bus)


async def start_background_sync(
    poll_interval: float = 5.0,
    forge_root: Path | None = None,
) -> StateSynchronizer:
    """
    Start background synchronization with the global synchronizer.

    Args:
        poll_interval: Seconds between sync cycles
        forge_root: FORGE root directory

    Returns:
        Running StateSynchronizer instance
    """
    sync = get_synchronizer(forge_root)
    await sync.start(poll_interval=poll_interval)
    return sync
