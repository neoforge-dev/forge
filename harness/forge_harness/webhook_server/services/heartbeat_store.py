"""Heartbeat Store Service
==========================

Persists node heartbeat telemetry to a JSONL backing file and exposes a
scheduler-facing view that ranks live nodes by available capacity.

Usage
-----
Inject the singleton into application code::

    from forge_harness.webhook_server.services.heartbeat_store import (
        get_heartbeat_store,
    )

    store = get_heartbeat_store()
    store.record("nova", {
        "hostname": "nova.local",
        "cpu_percent": 32.0,
        "memory_percent": 55.0,
        "active_agents": 4,
        "active_leases": 2,
    })

    view = store.get_scheduler_view()
    # view["ranked_nodes"][0] is the best node for new task assignment

To replace the singleton in tests call :func:`reset_heartbeat_store` first.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, Field

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default TTL for a heartbeat record before it is considered stale.
_DEFAULT_TTL_SECONDS: int = 120

#: Default JSONL backing file (relative to FORGE repo root).
_DEFAULT_STORE_PATH = Path(".forge/heartbeats/nodes.jsonl")

#: Node status literals used in HeartbeatRecord.
NodeStatus = Literal["healthy", "stale", "offline"]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class HeartbeatRecord(BaseModel):
    """A single node heartbeat snapshot persisted by the store.

    Fields
    ------
    node_id
        Unique node identifier (e.g. ``"nova"``).
    hostname
        Human-readable hostname reported by the node.
    cpu_percent
        CPU utilisation percentage (0–100).  Sourced from the node's
        telemetry; may be ``0.0`` when not reported.
    memory_percent
        RAM utilisation percentage (0–100).
    active_agents
        Number of active Claude/Codex/etc. agent processes on this node.
    active_leases
        Number of active task leases held by agents on this node.
    last_seen
        UTC ISO-8601 timestamp of the most recent heartbeat.
    ttl_seconds
        Age threshold (seconds) beyond which this record becomes *stale*.
    status
        Freshness status: ``healthy`` (within TTL), ``stale`` (past TTL
        but record exists), or ``offline`` (placeholder — set externally).
    """

    node_id: str = Field(..., description="Unique node identifier.")
    hostname: str = Field(default="", description="Reported hostname.")
    cpu_percent: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="CPU utilisation percentage.",
    )
    memory_percent: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="RAM utilisation percentage.",
    )
    active_agents: int = Field(
        default=0,
        ge=0,
        description="Number of active agents on this node.",
    )
    active_leases: int = Field(
        default=0,
        ge=0,
        description="Number of active task leases held on this node.",
    )
    last_seen: str = Field(
        default="",
        description="UTC ISO-8601 timestamp of most recent heartbeat.",
    )
    ttl_seconds: int = Field(
        default=_DEFAULT_TTL_SECONDS,
        gt=0,
        description="Seconds before this record becomes stale.",
    )
    status: NodeStatus = Field(
        default="healthy",
        description="Freshness status: healthy | stale | offline.",
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def age_seconds(self) -> float:
        """Return age in seconds since ``last_seen``.

        Returns ``float('inf')`` when ``last_seen`` is missing or
        unparseable so the caller can treat the record as maximally stale.
        """
        if not self.last_seen:
            return float("inf")
        try:
            ts = datetime.fromisoformat(self.last_seen)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return max((datetime.now(UTC) - ts).total_seconds(), 0.0)
        except Exception:
            return float("inf")

    def is_fresh(self, ttl_seconds: int | None = None) -> bool:
        """Return ``True`` when the record is within its TTL window.

        Args:
            ttl_seconds: Override the record's own ``ttl_seconds`` field.
        """
        threshold = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        return self.age_seconds() <= threshold

    def availability_score(self) -> float:
        """Compute a 0.0–1.0 availability score for scheduler ranking.

        Formula:
            - CPU component:    (100 - cpu_percent) / 100
            - Memory component: (100 - memory_percent) / 100
            - Combined:         0.5 * cpu + 0.5 * memory

        A score of ``1.0`` means the node is completely idle; ``0.0``
        means it is fully saturated.  Stale nodes always score ``0.0``
        so they sort to the bottom of the candidate list.
        """
        if self.status != "healthy":
            return 0.0
        cpu_score = (100.0 - min(self.cpu_percent, 100.0)) / 100.0
        mem_score = (100.0 - min(self.memory_percent, 100.0)) / 100.0
        return round(0.5 * cpu_score + 0.5 * mem_score, 4)


# ---------------------------------------------------------------------------
# Store class
# ---------------------------------------------------------------------------


class HeartbeatStore:
    """File-backed store for node heartbeat records.

    Each heartbeat received via :meth:`record` is merged into an in-memory
    cache keyed by ``node_id`` and the cache is flushed to a JSONL backing
    file after every write (one JSON object per line, last-write-wins per
    node).  On next startup the cache is rebuilt from the JSONL file.

    Thread safety is provided by a single :class:`threading.Lock` guarding
    all reads and writes.

    Args:
        storage_path: Path to the JSONL backing file.  The parent directory
            is created automatically on first write.  Defaults to
            ``Path(".forge/heartbeats/nodes.jsonl")``.
    """

    def __init__(self, storage_path: Path = _DEFAULT_STORE_PATH) -> None:
        self._path = storage_path
        self._lock: Lock = Lock()
        self._records: dict[str, HeartbeatRecord] = {}
        self._loaded: bool = False
        logger.info(
            "HeartbeatStore initialised",
            extra={"storage_path": str(self._path)},
        )

    # ------------------------------------------------------------------
    # Internal persistence
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Lazy-load records from disk on first access."""
        if self._loaded:
            return
        self._load()

    def _load(self) -> None:
        """Read all records from the JSONL file into the in-memory cache.

        Missing file is treated as an empty store — not an error.
        Malformed lines are skipped with a warning.
        """
        if not self._path.exists():
            self._loaded = True
            return

        loaded: dict[str, HeartbeatRecord] = {}
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    record = HeartbeatRecord.model_validate(data)
                    loaded[record.node_id] = record
                except Exception as exc:
                    logger.warning(
                        "Skipping malformed heartbeat on line %d: %s",
                        lineno,
                        exc,
                        extra={"path": str(self._path), "line_number": lineno},
                    )

        self._records = loaded
        self._loaded = True
        logger.debug(
            "Loaded %d heartbeat records from disk",
            len(loaded),
            extra={"storage_path": str(self._path)},
        )

    def _rewrite(self) -> None:
        """Rewrite the JSONL file from the in-memory cache (full rewrite).

        One record per line; last-write-wins per ``node_id`` is ensured
        because the cache itself de-duplicates by key.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            for rec in self._records.values():
                fh.write(rec.model_dump_json() + "\n")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        node_id: str,
        data: dict[str, Any],
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> HeartbeatRecord:
        """Upsert a heartbeat record for *node_id* and persist to disk.

        *data* is merged into a :class:`HeartbeatRecord` with
        ``last_seen`` set to the current UTC time.  Any key in *data* that
        matches a ``HeartbeatRecord`` field is accepted; unknown keys are
        silently ignored by Pydantic.

        Args:
            node_id:     Unique node identifier.
            data:        Telemetry dict from the node's heartbeat payload.
            ttl_seconds: Freshness TTL for this record (defaults to
                         :const:`_DEFAULT_TTL_SECONDS`).

        Returns:
            The upserted :class:`HeartbeatRecord`.
        """
        now_iso = datetime.now(UTC).isoformat()
        payload: dict[str, Any] = {
            **data,
            "node_id": node_id,
            "last_seen": now_iso,
            "ttl_seconds": ttl_seconds,
            "status": "healthy",
        }
        record = HeartbeatRecord.model_validate(payload)

        with self._lock:
            self._ensure_loaded()
            self._records[node_id] = record
            self._rewrite()

        logger.debug(
            "Heartbeat recorded for node %s",
            node_id,
            extra={
                "node_id": node_id,
                "cpu_percent": record.cpu_percent,
                "memory_percent": record.memory_percent,
                "active_agents": record.active_agents,
                "active_leases": record.active_leases,
            },
        )
        return record

    def get(self, node_id: str) -> HeartbeatRecord | None:
        """Return the heartbeat record for *node_id*, or ``None``.

        The returned record's :attr:`~HeartbeatRecord.status` reflects the
        live freshness at call time (computed from ``last_seen`` and
        ``ttl_seconds``) — it is NOT mutated on disk.

        Args:
            node_id: Node identifier to look up.

        Returns:
            :class:`HeartbeatRecord` when found; ``None`` otherwise.
        """
        with self._lock:
            self._ensure_loaded()
            record = self._records.get(node_id)

        if record is None:
            return None

        # Compute live status without mutating the cached copy
        if not record.is_fresh():
            record = record.model_copy(update={"status": "stale"})
        return record

    def list_active(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> list[HeartbeatRecord]:
        """Return all records whose ``last_seen`` is within *ttl_seconds*.

        Records that have gone stale are excluded.  The returned list is
        sorted by :meth:`~HeartbeatRecord.availability_score` descending
        (highest availability first).

        Args:
            ttl_seconds: Maximum age (seconds) for a record to be
                         considered active.

        Returns:
            List of fresh :class:`HeartbeatRecord` objects, sorted by
            availability score descending.
        """
        with self._lock:
            self._ensure_loaded()
            all_records = list(self._records.values())

        active = [r for r in all_records if r.is_fresh(ttl_seconds)]
        active.sort(key=lambda r: r.availability_score(), reverse=True)
        return active

    def get_scheduler_view(
        self, ttl_seconds: int = _DEFAULT_TTL_SECONDS
    ) -> dict[str, Any]:
        """Return a structured dict the scheduler can use for task placement.

        Schema
        ------
        .. code-block:: json

            {
              "generated_at": "<ISO-8601 UTC>",
              "ttl_seconds": 120,
              "total_nodes": 3,
              "active_nodes": 2,
              "stale_nodes": 1,
              "ranked_nodes": [
                {
                  "node_id": "nova",
                  "hostname": "nova.local",
                  "availability_score": 0.72,
                  "cpu_percent": 28.0,
                  "memory_percent": 56.0,
                  "active_agents": 3,
                  "active_leases": 1,
                  "age_seconds": 14.2,
                  "status": "healthy"
                },
                ...
              ],
              "node_loads": {
                "nova":  {"cpu_percent": 28.0, "memory_percent": 56.0},
                "prya":  {"cpu_percent": 61.0, "memory_percent": 72.0}
              },
              "capacities": {
                "nova":  {"available_agents": 5, "availability_score": 0.72},
                "prya":  {"available_agents": 2, "availability_score": 0.33}
              },
              "recommended_node_id": "nova"
            }

        Only **fresh** nodes appear in ``ranked_nodes``.  The top-ranked
        node is also exposed as ``recommended_node_id`` for convenience.

        Args:
            ttl_seconds: TTL threshold forwarded to :meth:`list_active`.

        Returns:
            Scheduler view dict.
        """
        active = self.list_active(ttl_seconds=ttl_seconds)

        with self._lock:
            self._ensure_loaded()
            all_records = list(self._records.values())

        total_nodes = len(all_records)
        active_node_ids = {r.node_id for r in active}
        stale_nodes = total_nodes - len(active_node_ids)

        ranked_nodes: list[dict[str, Any]] = []
        node_loads: dict[str, dict[str, float]] = {}
        capacities: dict[str, dict[str, Any]] = {}

        for rec in active:
            score = rec.availability_score()
            age = round(rec.age_seconds(), 1)
            ranked_nodes.append(
                {
                    "node_id": rec.node_id,
                    "hostname": rec.hostname,
                    "availability_score": score,
                    "cpu_percent": rec.cpu_percent,
                    "memory_percent": rec.memory_percent,
                    "active_agents": rec.active_agents,
                    "active_leases": rec.active_leases,
                    "age_seconds": age,
                    "status": rec.status,
                }
            )
            node_loads[rec.node_id] = {
                "cpu_percent": rec.cpu_percent,
                "memory_percent": rec.memory_percent,
            }
            # Rough available_agents headroom: assume max 8 agents per node
            _max_agents_per_node = 8
            available = max(_max_agents_per_node - rec.active_agents, 0)
            capacities[rec.node_id] = {
                "available_agents": available,
                "availability_score": score,
            }

        recommended_node_id: str | None = ranked_nodes[0]["node_id"] if ranked_nodes else None

        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "ttl_seconds": ttl_seconds,
            "total_nodes": total_nodes,
            "active_nodes": len(active),
            "stale_nodes": stale_nodes,
            "ranked_nodes": ranked_nodes,
            "node_loads": node_loads,
            "capacities": capacities,
            "recommended_node_id": recommended_node_id,
        }

    def mark_stale(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> list[str]:
        """Mark nodes past their TTL as ``stale`` and persist the change.

        This method mutates the in-memory cache and rewrites the JSONL file
        so that any process reading the file directly sees up-to-date
        statuses.

        Args:
            ttl_seconds: Age threshold; nodes older than this are marked
                         stale.

        Returns:
            List of ``node_id`` values that were transitioned to ``stale``.
        """
        marked: list[str] = []
        with self._lock:
            self._ensure_loaded()
            changed = False
            for node_id, record in self._records.items():
                if not record.is_fresh(ttl_seconds) and record.status == "healthy":
                    self._records[node_id] = record.model_copy(update={"status": "stale"})
                    marked.append(node_id)
                    changed = True
            if changed:
                self._rewrite()

        if marked:
            logger.info(
                "Marked %d node(s) as stale: %s",
                len(marked),
                ", ".join(marked),
                extra={"stale_nodes": marked, "ttl_seconds": ttl_seconds},
            )
        return marked


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_store_instance: HeartbeatStore | None = None
_store_lock: Lock = Lock()


def get_heartbeat_store(
    storage_path: Path | None = None,
) -> HeartbeatStore:
    """Return the global :class:`HeartbeatStore` singleton.

    On the first call the singleton is constructed with *storage_path*
    (defaulting to ``Path(".forge/heartbeats/nodes.jsonl")``).  Subsequent
    calls return the cached instance and ignore *storage_path*.

    To replace the singleton (e.g. in tests) call
    :func:`reset_heartbeat_store` first.

    Args:
        storage_path: Override the JSONL backing file path.  Only honoured
            on the first call.

    Returns:
        The singleton :class:`HeartbeatStore`.
    """
    global _store_instance
    with _store_lock:
        if _store_instance is None:
            path = storage_path if storage_path is not None else _DEFAULT_STORE_PATH
            _store_instance = HeartbeatStore(storage_path=path)
        return _store_instance


def reset_heartbeat_store() -> None:
    """Reset the global singleton — intended for use in tests only."""
    global _store_instance
    with _store_lock:
        _store_instance = None
