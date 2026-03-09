"""
State Sync - Bi-directional Agent State Integration Layer
=========================================================

Bridges the file-based session state (.forge/sessions/) with the
API-backed in-memory AgentRegistry so both surfaces see consistent data.

This module provides the integration layer called for in CC Orchestration
Plan Phase 1.2. The file-based state is the persistent truth; the
in-memory AgentRegistry is the live truth. This module reconciles them.

Responsibilities:
    sync_agents_to_files  – Push live agent registry data into .forge/sessions/
    sync_files_to_store   – Read persisted sessions and return normalised agent data
    diff                  – Detect added / updated / removed agents between two states
    reconcile             – Merge two states with last-writer-wins semantics

Usage:
    from forge_harness.state_sync import StateSynchronizer, SyncResult

    syncer = StateSynchronizer(sessions_dir=Path(".forge/sessions"))

    # Push live registry into files
    result = syncer.sync_agents_to_files(registry.list_active())

    # Pull files back into normalised dict
    normalised = syncer.sync_files_to_store(syncer.sessions_dir)

    # Compare two states
    changes = syncer.diff(file_state, store_state)

    # Merge with last-writer-wins
    merged = syncer.reconcile(file_state, store_state)
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """
    Result of a synchronisation operation.

    Attributes:
        added:     Agent IDs that are new in the destination.
        updated:   Agent IDs whose data changed.
        removed:   Agent IDs that were removed from the destination.
        conflicts: Agent IDs where both sources changed since last sync.
        errors:    Per-agent error messages (agent_id → message).
        timestamp: When the sync completed.
        success:   True unless a fatal error prevented the sync.
    """

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    success: bool = True

    # ---- convenience properties ------------------------------------------

    @property
    def total_changes(self) -> int:
        """Total number of items that changed."""
        return len(self.added) + len(self.updated) + len(self.removed)

    @property
    def has_conflicts(self) -> bool:
        """True if any conflicts were detected."""
        return bool(self.conflicts)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "conflicts": self.conflicts,
            "errors": self.errors,
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
            "total_changes": self.total_changes,
        }


# ---------------------------------------------------------------------------
# Agent normalisation helpers
# ---------------------------------------------------------------------------

_NORMALISED_FIELDS = frozenset(
    [
        "id",
        "session_id",
        "role",
        "name",
        "domain",
        "project",
        "task",
        "status",
        "progress",
        "current_task",
        "files_modified",
        "token_usage",
        "registered_at",
        "last_activity",
        "last_heartbeat",
        "is_stale",
    ]
)


def _normalise_agent(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise an agent dict to a canonical key set.

    Handles both AgentSession (webhook_server) and session file formats.
    Always returns a dict with an "id" field (preferred) or "session_id".
    """
    result: dict[str, Any] = dict(raw)

    # Unify id/session_id
    if "session_id" in raw and "id" not in raw:
        result["id"] = raw["session_id"]
    elif "id" in raw and "session_id" not in raw:
        result["session_id"] = raw["id"]

    # Ensure mandatory fields have defaults
    result.setdefault("status", "unknown")
    result.setdefault("progress", 0)
    result.setdefault("files_modified", [])
    result.setdefault("token_usage", {})
    result.setdefault("is_stale", False)

    return result


def _agent_id(agent: dict[str, Any]) -> str:
    """Extract canonical agent identifier (prefers 'id' over 'session_id')."""
    return agent.get("id") or agent.get("session_id") or ""


def _session_filename(agent_id: str) -> str:
    """Return the session filename for a given agent ID."""
    return f"session_{agent_id}.json"


# ---------------------------------------------------------------------------
# StateSynchronizer
# ---------------------------------------------------------------------------


class StateSynchronizer:
    """
    Bi-directional synchronizer between file-based and API-backed agent state.

    All public methods are thread-safe.

    Attributes:
        sessions_dir: Directory containing session_*.json files.
    """

    def __init__(self, sessions_dir: Path | str | None = None) -> None:
        """
        Initialise the synchronizer.

        Args:
            sessions_dir: Path to the .forge/sessions directory.
                          If None, defaults to ``.forge/sessions`` in the
                          current working directory.
        """
        if sessions_dir is None:
            sessions_dir = Path(".forge/sessions")
        self.sessions_dir = Path(sessions_dir)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _ensure_sessions_dir(self) -> None:
        """Create sessions directory if it does not exist."""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # sync_agents_to_files
    # ------------------------------------------------------------------

    def sync_agents_to_files(
        self,
        agent_registry_data: list[dict[str, Any]],
    ) -> SyncResult:
        """
        Write live agent registry data into .forge/sessions/ as JSON files.

        Each agent is written to ``session_{id}.json``.  Agents that existed
        on disk but are absent from ``agent_registry_data`` are *not* deleted;
        only present agents are updated/created.

        Args:
            agent_registry_data: List of agent dicts from AgentRegistry.list_active()
                                 or equivalent.  Each dict must contain at least
                                 one of ``id`` / ``session_id``.

        Returns:
            SyncResult describing what was added, updated, or errored.
        """
        result = SyncResult()

        with self._lock:
            self._ensure_sessions_dir()

            # Build a set of IDs already on disk
            existing_ids: set[str] = set()
            for fp in self.sessions_dir.glob("session_*.json"):
                try:
                    data = json.loads(fp.read_text())
                    aid = _agent_id(data)
                    if aid:
                        existing_ids.add(aid)
                except (json.JSONDecodeError, OSError):
                    pass

            for raw_agent in agent_registry_data:
                agent = _normalise_agent(raw_agent)
                aid = _agent_id(agent)

                if not aid:
                    logger.warning("Agent data has no id/session_id — skipping")
                    continue

                # Merge in file timestamp if already present
                file_path = self.sessions_dir / _session_filename(aid)
                if file_path.exists():
                    try:
                        existing = json.loads(file_path.read_text())
                        # Preserve registered_at from original file if not in registry data
                        if "registered_at" not in agent and "registered_at" in existing:
                            agent["registered_at"] = existing["registered_at"]
                    except (json.JSONDecodeError, OSError):
                        pass

                # Stamp with last_heartbeat if not present
                agent.setdefault(
                    "last_heartbeat",
                    agent.get("last_activity", datetime.now(UTC).isoformat()),
                )
                agent.setdefault("registered_at", datetime.now(UTC).isoformat())

                try:
                    file_path.write_text(json.dumps(agent, indent=2, default=str))
                    if aid in existing_ids:
                        result.updated.append(aid)
                    else:
                        result.added.append(aid)
                except OSError as exc:
                    result.errors[aid] = str(exc)
                    result.success = False
                    logger.error(f"Failed to write session file for {aid}: {exc}")

        return result

    # ------------------------------------------------------------------
    # sync_files_to_store
    # ------------------------------------------------------------------

    def sync_files_to_store(
        self,
        sessions_dir: Path | str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Read all session files and return normalised agent data keyed by agent ID.

        Silently skips corrupt or unreadable files, logging a warning for each.

        Args:
            sessions_dir: Directory to scan.  Defaults to ``self.sessions_dir``.

        Returns:
            Dict mapping agent_id → normalised agent dict.
        """
        target_dir = Path(sessions_dir) if sessions_dir is not None else self.sessions_dir

        result: dict[str, dict[str, Any]] = {}

        if not target_dir.exists():
            return result

        with self._lock:
            for file_path in sorted(target_dir.glob("session_*.json")):
                try:
                    raw = json.loads(file_path.read_text())
                    if not isinstance(raw, dict):
                        logger.warning(f"Session file {file_path} is not a JSON object — skipping")
                        continue
                    agent = _normalise_agent(raw)
                    aid = _agent_id(agent)
                    if aid:
                        result[aid] = agent
                    else:
                        logger.warning(f"Session file {file_path} has no id/session_id — skipping")
                except json.JSONDecodeError as exc:
                    logger.warning(f"Corrupt session file {file_path}: {exc}")
                except OSError as exc:
                    logger.warning(f"Cannot read session file {file_path}: {exc}")

        return result

    # ------------------------------------------------------------------
    # diff
    # ------------------------------------------------------------------

    def diff(
        self,
        file_state: dict[str, dict[str, Any]],
        store_state: dict[str, dict[str, Any]],
    ) -> SyncResult:
        """
        Compute the difference between file-based and store-based agent state.

        The *file_state* is treated as the baseline.  The *store_state* is
        the proposed new state.  The result describes what would need to
        happen to bring *file_state* in line with *store_state*.

        Args:
            file_state:  Agent dict keyed by agent_id (from files).
            store_state: Agent dict keyed by agent_id (from API/registry).

        Returns:
            SyncResult where:
            - added   = IDs present in store_state but not file_state
            - updated = IDs present in both but with different content
            - removed = IDs present in file_state but not store_state
        """
        result = SyncResult()
        file_ids = set(file_state.keys())
        store_ids = set(store_state.keys())

        # Added in store
        result.added = sorted(store_ids - file_ids)

        # Removed from store
        result.removed = sorted(file_ids - store_ids)

        # Updated (content differs)
        for aid in file_ids & store_ids:
            if self._agents_differ(file_state[aid], store_state[aid]):
                result.updated.append(aid)
        result.updated.sort()

        return result

    # ------------------------------------------------------------------
    # reconcile
    # ------------------------------------------------------------------

    def reconcile(
        self,
        file_state: dict[str, dict[str, Any]],
        store_state: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """
        Merge file-based and store-based states using last-writer-wins semantics.

        For each agent ID that appears in both states the record with the
        more recent ``last_heartbeat`` / ``last_activity`` timestamp wins.
        Agents only in one state are included as-is.  Agents where neither
        record has a comparable timestamp default to the store-side record.

        Conflict detection: an agent is flagged as a conflict when both
        sides have valid timestamps and neither is clearly newer (equal
        timestamps where the content differs).

        Args:
            file_state:  Agent dict keyed by agent_id (from files).
            store_state: Agent dict keyed by agent_id (from API/registry).

        Returns:
            Merged dict keyed by agent_id.  The ``_conflict`` key is added
            to any record that was flagged as a conflict.
        """
        merged: dict[str, dict[str, Any]] = {}
        file_ids = set(file_state.keys())
        store_ids = set(store_state.keys())

        # Agents only in file
        for aid in file_ids - store_ids:
            merged[aid] = dict(file_state[aid])

        # Agents only in store
        for aid in store_ids - file_ids:
            merged[aid] = dict(store_state[aid])

        # Agents in both — last-writer-wins
        for aid in file_ids & store_ids:
            file_agent = file_state[aid]
            store_agent = store_state[aid]

            file_ts = self._parse_timestamp(file_agent)
            store_ts = self._parse_timestamp(store_agent)

            if file_ts is None and store_ts is None:
                # No timestamps — prefer store
                merged[aid] = dict(store_agent)
            elif file_ts is None:
                merged[aid] = dict(store_agent)
            elif store_ts is None:
                merged[aid] = dict(file_agent)
            elif store_ts > file_ts:
                merged[aid] = dict(store_agent)
            elif file_ts > store_ts:
                merged[aid] = dict(file_agent)
            else:
                # Same timestamp but possibly different content
                if self._agents_differ(file_agent, store_agent):
                    # True conflict — prefer store and flag
                    record = dict(store_agent)
                    record["_conflict"] = True
                    merged[aid] = record
                else:
                    merged[aid] = dict(store_agent)

        return merged

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_timestamp(agent: dict[str, Any]) -> datetime | None:
        """
        Extract the best available timestamp from an agent dict.

        Checks ``last_heartbeat``, ``last_activity``, and ``registered_at``
        in that order of preference.
        """
        for field_name in ("last_heartbeat", "last_activity", "registered_at"):
            value = agent.get(field_name)
            if value:
                try:
                    ts = datetime.fromisoformat(str(value))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    return ts
                except (ValueError, TypeError):
                    continue
        return None

    @staticmethod
    def _agents_differ(a: dict[str, Any], b: dict[str, Any]) -> bool:
        """
        Return True if two agent dicts differ in any meaningful field.

        Comparison ignores internal bookkeeping keys (``_conflict``).
        """
        compare_keys = _NORMALISED_FIELDS | {
            "id",
            "session_id",
            "agent_type",
            "agent_role",
            "capabilities",
            "preferences",
        }
        for key in compare_keys:
            if a.get(key) != b.get(key):
                return True
        return False
