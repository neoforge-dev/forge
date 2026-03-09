"""Path Lock Registry Service — DF-3002
========================================

Thread-safe singleton registry that tracks which agents hold locks on
file-system (or logical resource) paths.  Path locks are consulted during the
recommendation stage so that agents with conflicting locks are excluded from
the candidate list before scoring — preventing wasted recommendations.

Lock semantics
--------------
- **Exclusive** lock: blocks all other locks (exclusive or shared) on the same
  path.  Only one agent may hold an exclusive lock at a time.
- **Shared** lock: allows other shared locks on the same path.  Blocked only
  when an exclusive lock from a *different* agent is already held.

Note: an agent that already holds any lock on a path (exclusive or shared) may
acquire additional locks on the same path (re-entrant by agent).

Persistence
-----------
Lock records are appended to a JSONL file at ``.forge/locks/path_locks.jsonl``
(relative to the current working directory) so they survive process restarts.
On startup the registry replays the file and discards records whose
``expires_at`` timestamp has already passed.

Singleton pattern
-----------------
Use :func:`get_path_lock_registry` to obtain the shared instance.  In tests
call :func:`reset_path_lock_registry` before each test to start from a clean
slate.

Usage
-----
::

    from forge_harness.webhook_server.services.path_lock_registry import (
        get_path_lock_registry,
        LockConflictError,
    )

    registry = get_path_lock_registry()

    lock = registry.acquire("forge:nova", "/src/api.py", ttl_seconds=300)
    # lock.lock_id  →  UUID4 string

    conflicts = registry.check_conflicts("/src/api.py", exclude_agent_id="forge:nova")
    # []  (nova holds the lock; excluded from conflict check)

    registry.release(lock.lock_id)

Reference
---------
forge_harness/webhook_server/models/path_lock.py  (PathLock, PathLockType, LockConflict)
forge_harness/webhook_server/services/recommendation_engine.py  (integration)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-3002)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.path_lock import (
    LockConflict,
    PathLock,
    PathLockType,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: JSONL persistence path (relative to cwd at registry creation time).
_LOCKS_JSONL_PATH: str = ".forge/locks/path_locks.jsonl"


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class LockConflictError(Exception):
    """Raised when a lock acquisition is blocked by an existing conflicting lock.

    Attributes:
        path:      The path for which acquisition was attempted.
        conflicts: List of :class:`~forge_harness.webhook_server.models
                   .path_lock.LockConflict` records describing each blocker.
    """

    def __init__(self, path: str, conflicts: list[LockConflict]) -> None:
        self.path = path
        self.conflicts = conflicts
        descriptions = "; ".join(
            f"{c.agent_id} ({c.lock_type_display if hasattr(c, 'lock_type_display') else c.conflict_reason})"
            for c in conflicts
        )
        super().__init__(
            f"Cannot acquire lock on '{path}': conflicting locks held by [{descriptions}]"
        )


# ---------------------------------------------------------------------------
# PathLockRegistry
# ---------------------------------------------------------------------------


class PathLockRegistry:
    """Thread-safe in-memory registry of path locks with JSONL persistence.

    Parameters
    ----------
    locks_path
        Path to the JSONL persistence file.  Defaults to
        ``.forge/locks/path_locks.jsonl``.

    Attributes
    ----------
    _lock
        Re-entrant lock guarding all mutable state.
    _locks
        Mapping of ``lock_id`` → :class:`PathLock`.
    _locks_path
        Path to the JSONL persistence file.
    """

    def __init__(self, locks_path: str | Path = _LOCKS_JSONL_PATH) -> None:
        self._lock: RLock = RLock()
        self._locks: dict[str, PathLock] = {}
        self._locks_path: Path = Path(locks_path)

        self._load_locks()
        logger.info("PathLockRegistry initialised (locks_path=%s)", self._locks_path)

    # ------------------------------------------------------------------
    # Public API — acquire / release
    # ------------------------------------------------------------------

    def acquire(
        self,
        agent_id: str,
        path: str,
        lock_type: PathLockType = PathLockType.exclusive,
        task_id: str | None = None,
        ttl_seconds: float | None = None,
    ) -> PathLock:
        """Acquire a lock on *path* for *agent_id*.

        Conflict rules
        ~~~~~~~~~~~~~~
        - An **exclusive** lock is rejected if any other agent holds any lock
          (exclusive or shared) on the same path.
        - A **shared** lock is rejected if any other agent holds an **exclusive**
          lock on the same path.
        - An agent may re-acquire a lock on a path it already holds (re-entrant).

        Args:
            agent_id:    Agent requesting the lock.
            path:        Path (or logical resource) to lock.
            lock_type:   Lock mode.  Defaults to :attr:`PathLockType.exclusive`.
            task_id:     Optional task identifier to associate with the lock.
            ttl_seconds: If provided, the lock expires this many seconds from
                         now and will be removed by :meth:`cleanup_expired`.

        Returns:
            The acquired :class:`PathLock`.

        Raises:
            LockConflictError: When an existing conflicting lock blocks
                               acquisition.
        """
        with self._lock:
            # Cleanup expired locks before checking conflicts
            self._cleanup_expired_locked()

            # Check for conflicts from OTHER agents
            conflicts = self._check_conflicts_locked(path, exclude_agent_id=agent_id)

            # Exclusive mode: blocked by any lock from another agent
            if lock_type == PathLockType.exclusive and conflicts:
                raise LockConflictError(path, conflicts)

            # Shared mode: blocked only by exclusive locks from another agent
            if lock_type == PathLockType.shared:
                exclusive_conflicts = [
                    c for c in conflicts
                    if "exclusive" in c.conflict_reason
                ]
                if exclusive_conflicts:
                    raise LockConflictError(path, exclusive_conflicts)

            # Compute expiry
            expires_at: datetime | None = None
            if ttl_seconds is not None:
                expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

            lock = PathLock(
                agent_id=agent_id,
                path=path,
                lock_type=lock_type,
                expires_at=expires_at,
                task_id=task_id,
            )
            self._locks[lock.lock_id] = lock

            logger.info(
                "PathLockRegistry.acquire: agent=%s path=%s type=%s lock_id=%s",
                agent_id,
                path,
                lock_type.value,
                lock.lock_id,
            )
            self._persist_lock(lock)
            return lock

    def release(self, lock_id: str) -> bool:
        """Release a specific lock by its identifier.

        Args:
            lock_id: Identifier of the lock to release.

        Returns:
            ``True`` if the lock was found and removed; ``False`` if no lock
            with the given *lock_id* existed.
        """
        with self._lock:
            lock = self._locks.pop(lock_id, None)
            if lock is None:
                logger.debug(
                    "PathLockRegistry.release: lock_id=%s not found", lock_id
                )
                return False

            logger.info(
                "PathLockRegistry.release: lock_id=%s agent=%s path=%s",
                lock_id,
                lock.agent_id,
                lock.path,
            )
            self._persist_release(lock_id)
            return True

    def release_by_agent(self, agent_id: str) -> int:
        """Release all locks held by *agent_id*.

        Args:
            agent_id: Agent whose locks should all be released.

        Returns:
            Number of locks released.
        """
        with self._lock:
            to_remove = [
                lock_id
                for lock_id, lock in self._locks.items()
                if lock.agent_id == agent_id
            ]
            for lock_id in to_remove:
                lock = self._locks.pop(lock_id)
                self._persist_release(lock_id)
                logger.debug(
                    "PathLockRegistry.release_by_agent: released lock_id=%s path=%s",
                    lock_id,
                    lock.path,
                )

            if to_remove:
                logger.info(
                    "PathLockRegistry.release_by_agent: agent=%s released %d lock(s)",
                    agent_id,
                    len(to_remove),
                )
            return len(to_remove)

    # ------------------------------------------------------------------
    # Public API — inspection
    # ------------------------------------------------------------------

    def check_conflicts(
        self,
        path: str,
        exclude_agent_id: str | None = None,
    ) -> list[LockConflict]:
        """Return all existing locks that would conflict with a new lock on *path*.

        This method does NOT distinguish between lock types for the purposes of
        the returned conflict list — it returns every lock held by other agents
        on the same path, so callers can apply their own semantics.

        Expired locks are cleaned up before the check.

        Args:
            path:              Path to check.
            exclude_agent_id:  If provided, locks held by this agent are
                               excluded from the result (used to allow
                               re-entrant acquisition checks).

        Returns:
            List of :class:`LockConflict` describing each conflicting lock.
            Empty list when no conflicts exist.
        """
        with self._lock:
            self._cleanup_expired_locked()
            return self._check_conflicts_locked(path, exclude_agent_id)

    def get_locks_for_agent(self, agent_id: str) -> list[PathLock]:
        """Return all non-expired locks currently held by *agent_id*.

        Args:
            agent_id: Agent to query.

        Returns:
            List of :class:`PathLock` for the given agent.
        """
        with self._lock:
            self._cleanup_expired_locked()
            return [
                lock
                for lock in self._locks.values()
                if lock.agent_id == agent_id
            ]

    def get_all_locks(self) -> list[PathLock]:
        """Return all currently held (non-expired) locks.

        Returns:
            List of :class:`PathLock` in insertion order.
        """
        with self._lock:
            self._cleanup_expired_locked()
            return list(self._locks.values())

    def cleanup_expired(self) -> int:
        """Remove all locks whose ``expires_at`` timestamp has passed.

        Returns:
            Number of expired locks removed.
        """
        with self._lock:
            return self._cleanup_expired_locked()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_conflicts_locked(
        self,
        path: str,
        exclude_agent_id: str | None,
    ) -> list[LockConflict]:
        """Return conflict records for *path* — must be called under ``_lock``.

        Args:
            path:              The path to check for conflicts.
            exclude_agent_id:  Agent to exclude from the conflict check.

        Returns:
            List of :class:`LockConflict` for each conflicting lock.
        """
        conflicts: list[LockConflict] = []
        now = datetime.now(UTC)

        for lock in self._locks.values():
            if lock.path != path:
                continue
            if exclude_agent_id is not None and lock.agent_id == exclude_agent_id:
                continue
            # Skip expired locks (should already be removed but guard anyway)
            if lock.expires_at is not None and lock.expires_at <= now:
                continue

            if lock.lock_type == PathLockType.exclusive:
                reason = (
                    f"exclusive lock held by {lock.agent_id} (lock_id={lock.lock_id})"
                )
            else:
                reason = (
                    f"shared lock held by {lock.agent_id} (lock_id={lock.lock_id})"
                )

            conflicts.append(
                LockConflict(
                    lock_id=lock.lock_id,
                    agent_id=lock.agent_id,
                    path=lock.path,
                    conflict_reason=reason,
                )
            )

        return conflicts

    def _cleanup_expired_locked(self) -> int:
        """Remove expired locks — must be called under ``_lock``.

        Returns:
            Number of expired locks removed.
        """
        now = datetime.now(UTC)
        expired = [
            lock_id
            for lock_id, lock in self._locks.items()
            if lock.expires_at is not None and lock.expires_at <= now
        ]
        for lock_id in expired:
            lock = self._locks.pop(lock_id)
            logger.debug(
                "PathLockRegistry.cleanup_expired: removed expired lock_id=%s "
                "agent=%s path=%s",
                lock_id,
                lock.agent_id,
                lock.path,
            )
        if expired:
            logger.info(
                "PathLockRegistry.cleanup_expired: removed %d expired lock(s)",
                len(expired),
            )
        return len(expired)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_locks(self) -> None:
        """Replay non-expired locks from the JSONL persistence file.

        Each line must be a JSON object that can be parsed into a
        :class:`PathLock`.  Lines that represent release events
        (``"type": "release"``) remove the corresponding lock from the
        in-memory registry.  Malformed or expired lines are silently skipped.
        """
        if not self._locks_path.exists():
            logger.debug(
                "PathLockRegistry._load_locks: no lock file at %s",
                self._locks_path,
            )
            return

        acquired: dict[str, dict[str, Any]] = {}
        released: set[str] = set()

        with self._locks_path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    record_type = record.get("type")
                    if record_type == "release":
                        released.add(record["lock_id"])
                    elif record_type == "lock":
                        acquired[record["lock_id"]] = record
                except (json.JSONDecodeError, KeyError, TypeError):
                    logger.debug(
                        "PathLockRegistry._load_locks: skipping malformed line: %r",
                        line,
                    )

        now = datetime.now(UTC)
        loaded = 0
        for lock_id, record in acquired.items():
            if lock_id in released:
                continue  # Already released before shutdown
            try:
                lock = PathLock.model_validate(record["lock"])
                # Skip expired
                if lock.expires_at is not None and lock.expires_at <= now:
                    continue
                self._locks[lock.lock_id] = lock
                loaded += 1
            except Exception:
                logger.debug(
                    "PathLockRegistry._load_locks: skipping invalid lock record %r",
                    record,
                )

        logger.info(
            "PathLockRegistry._load_locks: replayed %d active lock(s)", loaded
        )

    def _persist_lock(self, lock: PathLock) -> None:
        """Append an acquire event to the JSONL file.

        Args:
            lock: The lock that was acquired.
        """
        record = {
            "type": "lock",
            "lock_id": lock.lock_id,
            "lock": lock.model_dump(mode="json"),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        self._write_jsonl(record)

    def _persist_release(self, lock_id: str) -> None:
        """Append a release event to the JSONL file.

        Args:
            lock_id: Identifier of the released lock.
        """
        record = {
            "type": "release",
            "lock_id": lock_id,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        self._write_jsonl(record)

    def _write_jsonl(self, record: dict[str, Any]) -> None:
        """Append *record* as a JSON line to the persistence file.

        Creates parent directories if needed.  Write errors are logged but
        never re-raised so that the main lock flow is not interrupted.

        Args:
            record: Dict to serialise as a JSON line.
        """
        try:
            self._locks_path.parent.mkdir(parents=True, exist_ok=True)
            with self._locks_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning(
                "PathLockRegistry._write_jsonl: write failed: %s", exc
            )


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_registry: PathLockRegistry | None = None
_registry_lock: RLock = RLock()


def get_path_lock_registry() -> PathLockRegistry:
    """Return the global :class:`PathLockRegistry` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe.

    To replace the singleton (e.g. in tests) call
    :func:`reset_path_lock_registry` first.

    Returns:
        The singleton :class:`PathLockRegistry`.
    """
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = PathLockRegistry()
            logger.info("PathLockRegistry singleton created")
        return _registry


def reset_path_lock_registry() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to
    :func:`get_path_lock_registry` will create a fresh instance.
    """
    global _registry
    with _registry_lock:
        _registry = None
        logger.debug("PathLockRegistry singleton reset")
