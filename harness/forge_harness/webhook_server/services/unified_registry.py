"""UnifiedAgentRegistry - Combined In-Memory and File-Based Agent Registry
=========================================================================

Provides a single source of truth for agent state that is visible to both
the TUI dashboard (which reads .forge/sessions/*.json files) and the
Command Center API (which uses the in-memory registry).

Architecture:
    - In-memory store: fast lookups and updates during API calls
    - File store (.forge/sessions/session_{id}.json): persistent state read by TUI
    - Periodic sync: background sync flushes in-memory updates to disk
    - Startup hydration: registry re-loads file state after process restart

Data flow:
    API register  →  in-memory dict  +  immediate session file write
    API heartbeat →  in-memory dict  +  debounced file sync (every N seconds)
    TUI read      →  glob session files  (written by this registry)
    CLI script    →  writes session file  →  picked up by sync_from_files()

Thread safety: all public methods acquire a single re-entrant lock.

Usage::

    from forge_harness.webhook_server.services.unified_registry import (
        get_unified_registry,
        reset_unified_registry,
    )

    registry = get_unified_registry()
    agent_dict = registry.register({"role": "backend", "project": "foo", "task": "bar"})
    registry.update_heartbeat(agent_dict["id"], {"status": "active", "progress": 50})
    agents = registry.list_agents(status="active")
    stats = registry.get_stats()
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_STATUSES = frozenset(["active", "waiting", "idle", "completed", "failed", "paused", "unknown"])
_DEFAULT_SESSIONS_DIR = Path(".forge/sessions")
_SESSION_FILE_PREFIX = "session_"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO timestamp string into a timezone-aware datetime or return None."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts
    except (ValueError, TypeError):
        return None


def _session_path(sessions_dir: Path, agent_id: str) -> Path:
    return sessions_dir / f"{_SESSION_FILE_PREFIX}{agent_id}.json"


def _agent_to_file_dict(agent: dict[str, Any]) -> dict[str, Any]:
    """Convert in-memory agent record to the file representation."""
    record = dict(agent)
    # Ensure both id and session_id are present for cross-consumer compatibility
    record.setdefault("session_id", record.get("id", ""))
    record.setdefault("last_heartbeat", record.get("last_activity", _now_iso()))
    return record


def _normalise_file_agent(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise a raw dict loaded from a session file into the canonical shape
    expected by the unified registry.

    Handles both AgentSession (from webhook API) and bare session-file formats.
    """
    result = dict(raw)

    # Unify id / session_id
    if "id" not in result and "session_id" in result:
        result["id"] = result["session_id"]
    elif "session_id" not in result and "id" in result:
        result["session_id"] = result["id"]

    result.setdefault("role", "unknown")
    result.setdefault("project", "unknown")
    result.setdefault("task", "")
    result.setdefault("status", "unknown")
    result.setdefault("progress", 0)
    result.setdefault("files_modified", [])
    result.setdefault("token_usage", {})
    result.setdefault("skills", [])
    result.setdefault("children", [])
    result.setdefault("registered_at", _now_iso())
    result.setdefault("last_activity", _now_iso())
    result.setdefault("source", "file")

    return result


# ---------------------------------------------------------------------------
# UnifiedAgentRegistry
# ---------------------------------------------------------------------------


class UnifiedAgentRegistry:
    """Combined in-memory and file-based agent registry.

    Agents registered via the API are written to both the in-memory dict and
    a ``.forge/sessions/session_{id}.json`` file so the TUI dashboard (which
    reads those files) and the API (which reads from memory) always agree.

    Agents spawned by external scripts that write their own session files are
    surfaced through :meth:`sync_from_files`, so the API can also see them.
    """

    def __init__(
        self,
        sessions_dir: Path | str | None = None,
        sync_interval_seconds: int = 30,
    ) -> None:
        """Initialise the registry.

        Args:
            sessions_dir: Directory for ``.forge/sessions`` files.
                          Defaults to ``_DEFAULT_SESSIONS_DIR``.
            sync_interval_seconds: How often the background timer flushes all
                                   in-memory agents to disk (seconds).
        """
        self._sessions_dir: Path = (
            Path(sessions_dir) if sessions_dir is not None else _DEFAULT_SESSIONS_DIR
        )
        self._sync_interval: int = sync_interval_seconds
        self._agents: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._last_sync_at: datetime | None = None
        self._heartbeat_counter: dict[str, int] = {}  # tracks updates since last flush

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # register
    # ------------------------------------------------------------------

    def register(self, agent_data: dict[str, Any]) -> dict[str, Any]:
        """Register a new agent.

        Writes to in-memory store and immediately creates the session file.

        Args:
            agent_data: Dict containing at minimum ``role``, ``project``, and
                        ``task``.  An ``id`` is generated if not supplied.

        Returns:
            The normalised agent record (includes the generated ``id``).
        """
        with self._lock:
            agent_id = agent_data.get("id") or str(uuid.uuid4())[:8]
            now = _now_iso()

            record: dict[str, Any] = {
                "id": agent_id,
                "session_id": agent_id,
                "role": agent_data.get("role", "unknown"),
                "name": agent_data.get("name"),
                "domain": agent_data.get("domain"),
                "project": agent_data.get("project", "unknown"),
                "task": agent_data.get("task", ""),
                "parent_id": agent_data.get("parent_id"),
                "children": agent_data.get("children", []),
                "tmux_session": agent_data.get("tmux_session"),
                "skills": agent_data.get("skills", []),
                "status": agent_data.get("status", "active"),
                "progress": agent_data.get("progress", 0),
                "current_task": agent_data.get("current_task"),
                "files_modified": agent_data.get("files_modified", []),
                "token_usage": agent_data.get("token_usage", {}),
                "registered_at": agent_data.get("registered_at", now),
                "last_activity": agent_data.get("last_activity", now),
                "last_heartbeat": now,
                "source": "api",
            }

            self._agents[agent_id] = record
            self._heartbeat_counter[agent_id] = 0

            # Update parent's children list if parent exists in memory
            parent_id = record.get("parent_id")
            if parent_id and parent_id in self._agents:
                parent = self._agents[parent_id]
                if agent_id not in parent["children"]:
                    parent["children"].append(agent_id)

            # Persist to file immediately
            self._write_session_file(record)

            logger.info(
                "UnifiedRegistry: registered agent %s (%s) for %s/%s",
                agent_id,
                record["role"],
                record.get("domain"),
                record["project"],
            )
            return dict(record)

    # ------------------------------------------------------------------
    # unregister
    # ------------------------------------------------------------------

    def unregister(self, agent_id: str) -> bool:
        """Remove an agent from both in-memory store and file system.

        Args:
            agent_id: The agent ID to remove.

        Returns:
            True if the agent was found and removed, False otherwise.
        """
        with self._lock:
            removed = self._agents.pop(agent_id, None) is not None
            self._heartbeat_counter.pop(agent_id, None)

            # Remove session file
            file_path = _session_path(self._sessions_dir, agent_id)
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.debug("UnifiedRegistry: deleted session file for %s", agent_id)
                except OSError as exc:
                    logger.warning("UnifiedRegistry: could not delete file for %s: %s", agent_id, exc)

            if removed:
                logger.info("UnifiedRegistry: unregistered agent %s", agent_id)
            return removed

    # ------------------------------------------------------------------
    # get_agent
    # ------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Get a single agent by ID.

        Checks the in-memory store first; if not found, falls back to reading
        the session file from disk (covers agents spawned by scripts).

        Args:
            agent_id: The agent ID.

        Returns:
            Normalised agent dict or None if not found.
        """
        with self._lock:
            # In-memory lookup first (fast path)
            if agent_id in self._agents:
                return dict(self._agents[agent_id])

            # File fallback (agents spawned by external scripts)
            file_path = _session_path(self._sessions_dir, agent_id)
            if file_path.exists():
                try:
                    raw = json.loads(file_path.read_text())
                    if isinstance(raw, dict):
                        agent = _normalise_file_agent(raw)
                        # Load into memory for subsequent fast lookups
                        self._agents[agent_id] = agent
                        return dict(agent)
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("UnifiedRegistry: cannot read session file for %s: %s", agent_id, exc)

            return None

    # ------------------------------------------------------------------
    # list_agents
    # ------------------------------------------------------------------

    def list_agents(self, status: str | None = None) -> list[dict[str, Any]]:
        """Return a merged view of in-memory and file-based agents.

        File-only agents (those with a session file but not in memory) are
        discovered and included.  In-memory agents take precedence over their
        corresponding file record if both exist.

        Args:
            status: If provided, only return agents with this status.

        Returns:
            List of normalised agent dicts.
        """
        with self._lock:
            # Start from file-based agents so CLI-spawned agents are included
            file_agents = self._load_file_agents()

            # Merge: in-memory wins over file
            merged: dict[str, dict[str, Any]] = dict(file_agents)
            for aid, agent in self._agents.items():
                merged[aid] = agent

            agents = list(merged.values())

        if status is not None:
            agents = [a for a in agents if a.get("status") == status]

        return agents

    # ------------------------------------------------------------------
    # update_heartbeat
    # ------------------------------------------------------------------

    def update_heartbeat(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update a registered agent's heartbeat / state.

        Applies updates to the in-memory record and flushes to disk every
        ``sync_interval_seconds`` seconds (or if the record does not yet
        exist on disk).

        Args:
            agent_id: The agent to update.
            data: Partial update dict.  Keys that are not present are left
                  unchanged.  ``last_activity`` and ``last_heartbeat`` are
                  always updated.

        Returns:
            Updated agent dict or None if agent not found.
        """
        with self._lock:
            if agent_id not in self._agents:
                # Try to hydrate from file
                loaded = self.get_agent(agent_id)
                if loaded is None:
                    logger.debug("UnifiedRegistry: heartbeat for unknown agent %s", agent_id)
                    return None

            agent = self._agents[agent_id]
            now = _now_iso()

            # Merge mutable fields
            for key, value in data.items():
                if key not in ("id", "session_id", "registered_at"):
                    agent[key] = value

            agent["last_activity"] = now
            agent["last_heartbeat"] = now

            # Track heartbeat frequency and decide whether to flush to disk
            count = self._heartbeat_counter.get(agent_id, 0) + 1
            self._heartbeat_counter[agent_id] = count

            needs_flush = self._needs_file_flush()
            if needs_flush or not _session_path(self._sessions_dir, agent_id).exists():
                self._write_session_file(agent)
                if needs_flush:
                    self._last_sync_at = datetime.now(UTC)

            return dict(agent)

    # ------------------------------------------------------------------
    # sync_to_files
    # ------------------------------------------------------------------

    def sync_to_files(self) -> int:
        """Flush all in-memory agents to session files.

        Returns:
            Number of files written.
        """
        with self._lock:
            self._ensure_dir()
            count = 0
            for agent in self._agents.values():
                try:
                    self._write_session_file(agent)
                    count += 1
                except OSError as exc:
                    logger.error(
                        "UnifiedRegistry: failed to sync agent %s to file: %s",
                        agent.get("id"),
                        exc,
                    )
            self._last_sync_at = datetime.now(UTC)
            logger.debug("UnifiedRegistry: synced %d agents to files", count)
            return count

    # ------------------------------------------------------------------
    # sync_from_files
    # ------------------------------------------------------------------

    def sync_from_files(self) -> int:
        """Read session files and merge into in-memory store.

        In-memory records take precedence (last-writer-wins via timestamp
        comparison).  File-only agents are loaded into memory so subsequent
        API calls can find them.

        Returns:
            Number of agents loaded/updated from files.
        """
        with self._lock:
            file_agents = self._load_file_agents()
            count = 0
            for aid, file_agent in file_agents.items():
                if aid not in self._agents:
                    # New agent from file (e.g., spawned by CLI script)
                    self._agents[aid] = file_agent
                    count += 1
                else:
                    # Compare timestamps; file wins only if newer
                    mem_agent = self._agents[aid]
                    mem_ts = _parse_ts(mem_agent.get("last_activity"))
                    file_ts = _parse_ts(file_agent.get("last_activity"))
                    if file_ts and (mem_ts is None or file_ts > mem_ts):
                        self._agents[aid] = file_agent
                        count += 1
            logger.debug("UnifiedRegistry: loaded/updated %d agents from files", count)
            return count

    # ------------------------------------------------------------------
    # get_stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return registry statistics.

        Returns:
            Dict with ``total``, ``by_status`` (dict), and ``last_sync_at``.
        """
        agents = self.list_agents()
        by_status: dict[str, int] = {}
        for agent in agents:
            st = agent.get("status", "unknown")
            by_status[st] = by_status.get(st, 0) + 1

        return {
            "total": len(agents),
            "by_status": by_status,
            "last_sync_at": self._last_sync_at.isoformat() if self._last_sync_at else None,
            "sessions_dir": str(self._sessions_dir),
            "sync_interval_seconds": self._sync_interval,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_file_agents(self) -> dict[str, dict[str, Any]]:
        """Read all session_*.json files and return normalised agent dicts keyed by id."""
        result: dict[str, dict[str, Any]] = {}
        if not self._sessions_dir.exists():
            return result

        for file_path in sorted(self._sessions_dir.glob(f"{_SESSION_FILE_PREFIX}*.json")):
            try:
                raw = json.loads(file_path.read_text())
                if not isinstance(raw, dict):
                    continue
                agent = _normalise_file_agent(raw)
                aid = agent.get("id") or agent.get("session_id", "")
                if aid:
                    result[aid] = agent
            except json.JSONDecodeError as exc:
                logger.warning("UnifiedRegistry: corrupt session file %s: %s", file_path, exc)
            except OSError as exc:
                logger.warning("UnifiedRegistry: cannot read session file %s: %s", file_path, exc)

        return result

    def _write_session_file(self, agent: dict[str, Any]) -> None:
        """Write a single agent record to its session file."""
        self._ensure_dir()
        aid = agent.get("id") or agent.get("session_id", "")
        if not aid:
            logger.warning("UnifiedRegistry: cannot write file — agent has no id")
            return
        file_path = _session_path(self._sessions_dir, aid)
        try:
            file_path.write_text(json.dumps(_agent_to_file_dict(agent), indent=2, default=str))
        except OSError as exc:
            logger.error("UnifiedRegistry: failed to write session file %s: %s", file_path, exc)

    def _needs_file_flush(self) -> bool:
        """Return True if enough time has passed since the last sync."""
        if self._last_sync_at is None:
            return True
        elapsed = (datetime.now(UTC) - self._last_sync_at).total_seconds()
        return elapsed >= self._sync_interval


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_unified_registry: UnifiedAgentRegistry | None = None
_singleton_lock = threading.Lock()


def get_unified_registry(
    sessions_dir: Path | str | None = None,
    sync_interval_seconds: int = 30,
) -> UnifiedAgentRegistry:
    """Return the global UnifiedAgentRegistry singleton.

    Args:
        sessions_dir: Only used on first call to create the instance.
        sync_interval_seconds: Only used on first call.

    Returns:
        The singleton registry instance.
    """
    global _unified_registry
    with _singleton_lock:
        if _unified_registry is None:
            _unified_registry = UnifiedAgentRegistry(
                sessions_dir=sessions_dir,
                sync_interval_seconds=sync_interval_seconds,
            )
        return _unified_registry


def reset_unified_registry() -> None:
    """Reset the singleton (for testing / process restart scenarios)."""
    global _unified_registry
    with _singleton_lock:
        _unified_registry = None
