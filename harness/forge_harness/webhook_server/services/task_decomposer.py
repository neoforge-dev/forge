"""Task Decomposition Service for the FORGE Dark Factory.

Provides a file-backed, thread-safe singleton service for creating and
managing task decomposition DAGs.  Each parent task can be broken into a
directed acyclic graph (DAG) of subtasks with explicit dependency tracking
and completion reporting.

Usage
-----
Inject the singleton into application code::

    from forge_harness.webhook_server.services.task_decomposer import (
        get_task_decomposer,
    )

    svc = get_task_decomposer()
    graph = svc.create_graph("TASK-001", "Build authentication module")

    node_a = svc.add_subtask(
        parent_id="TASK-001",
        title="Write unit tests",
        task_type=TaskType.test_writing,
        risk_tier=RiskTier.low,
    )
    node_b = svc.add_subtask(
        parent_id="TASK-001",
        title="Implement login endpoint",
        task_type=TaskType.api_endpoint,
        risk_tier=RiskTier.medium,
        depends_on=[node_a.subtask_id],
    )

    svc.complete_subtask("TASK-001", node_a.subtask_id)
    ready = svc.get_graph("TASK-001").get_ready_nodes()  # [node_b]

To replace the singleton in tests call :func:`reset_task_decomposer` first.

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2002)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.task_graph import SubtaskNode, TaskGraph

logger = get_logger(__name__)

# Default JSONL storage path relative to FORGE repo root.
_DEFAULT_GRAPHS_PATH = Path(".forge/decomposition/graphs.jsonl")


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class TaskDecomposer:
    """File-backed service for managing task decomposition DAGs.

    All graphs are persisted to a JSONL file; each line is a
    JSON-serialised :class:`TaskGraph`.  The in-memory cache (``_graphs``)
    mirrors the file and is rebuilt lazily on first access.

    Thread safety is provided by a single :class:`threading.RLock` that
    guards both the in-memory cache and file I/O.  An RLock (reentrant
    lock) is used instead of a plain Lock so that public methods can call
    each other without deadlocking.

    Args:
        storage_path: Path to the JSONL backing file.  The parent directory
            is created automatically on first write.  Defaults to
            ``Path(".forge/decomposition/graphs.jsonl")``.
    """

    def __init__(self, storage_path: Path = _DEFAULT_GRAPHS_PATH) -> None:
        self._path = storage_path
        self._lock: RLock = RLock()
        self._graphs: dict[str, TaskGraph] = {}
        self._loaded: bool = False
        logger.info(
            "TaskDecomposer initialised",
            extra={"storage_path": str(self._path)},
        )

    # ------------------------------------------------------------------
    # Internal persistence helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load graphs from disk if not already loaded (lazy init)."""
        if self._loaded:
            return
        self._load()

    def _load(self) -> None:
        """Read all graphs from the JSONL file into the in-memory cache.

        Missing file is treated as an empty store (not an error).
        """
        if not self._path.exists():
            self._loaded = True
            return

        loaded: dict[str, TaskGraph] = {}
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    graph = TaskGraph.model_validate(data)
                    loaded[graph.parent_id] = graph
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Skipping malformed graph on line %d: %s",
                        lineno,
                        exc,
                        extra={"path": str(self._path), "line_number": lineno},
                    )

        self._graphs = loaded
        self._loaded = True
        logger.debug(
            "Loaded %d graphs from disk",
            len(loaded),
            extra={"storage_path": str(self._path)},
        )

    def _rewrite(self) -> None:
        """Rewrite the full JSONL file from the in-memory cache.

        Called after every mutating operation to keep the file consistent
        with the in-memory state.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            for graph in self._graphs.values():
                fh.write(graph.model_dump_json() + "\n")

    def _persist(self, graph: TaskGraph) -> None:
        """Update cache entry and rewrite backing file."""
        self._graphs[graph.parent_id] = graph
        self._rewrite()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_graph(self, parent_id: str, parent_title: str) -> TaskGraph:
        """Create an empty task graph for a parent task.

        Args:
            parent_id:    Unique identifier of the parent task.
            parent_title: Human-readable title of the parent task.

        Returns:
            The newly created (empty) :class:`TaskGraph`.

        Raises:
            ValueError: If a graph for ``parent_id`` already exists.
        """
        with self._lock:
            self._ensure_loaded()
            if parent_id in self._graphs:
                raise ValueError(
                    f"Graph for parent_id {parent_id!r} already exists. "
                    "Call get_graph() to retrieve it."
                )
            graph = TaskGraph(
                parent_id=parent_id,
                parent_title=parent_title,
                created_at=datetime.now(UTC),
            )
            self._persist(graph)

        logger.info(
            "Task graph created",
            extra={"parent_id": parent_id, "parent_title": parent_title},
        )
        return graph

    def add_subtask(
        self,
        parent_id: str,
        title: str,
        task_type: TaskType,
        risk_tier: RiskTier,
        depends_on: list[str] | None = None,
    ) -> SubtaskNode:
        """Add a new subtask node to an existing graph.

        A unique ``subtask_id`` (UUID4 prefixed with ``sub-``) is generated
        automatically.

        Args:
            parent_id:  The parent task whose graph will receive the subtask.
            title:      Human-readable description of the subtask.
            task_type:  Canonical work category for Dark Factory routing.
            risk_tier:  Risk classification for this subtask.
            depends_on: List of subtask_ids that must complete before this
                        subtask becomes ready.  Defaults to ``[]``.

        Returns:
            The newly created :class:`SubtaskNode`.

        Raises:
            KeyError:   If no graph exists for ``parent_id``.
            ValueError: If any ``depends_on`` id does not reference a known
                        node in the graph, or if adding this node would
                        introduce a cycle.
        """
        if depends_on is None:
            depends_on = []

        with self._lock:
            self._ensure_loaded()
            graph = self._graphs.get(parent_id)
            if graph is None:
                raise KeyError(
                    f"No graph found for parent_id {parent_id!r}. "
                    "Call create_graph() first."
                )

            # Validate all dependency ids reference existing nodes.
            unknown_deps = [dep for dep in depends_on if dep not in graph.nodes]
            if unknown_deps:
                raise ValueError(
                    f"Unknown dependency subtask_ids: {unknown_deps!r}. "
                    "All depends_on entries must reference existing nodes."
                )

            subtask_id = f"sub-{uuid.uuid4().hex[:12]}"
            node = SubtaskNode(
                subtask_id=subtask_id,
                parent_id=parent_id,
                title=title,
                task_type=task_type,
                risk_tier=risk_tier,
                status="pending",
                depends_on=depends_on,
                created_at=datetime.now(UTC),
            )
            graph.add_node(node)

            # Validate no cycles were introduced.
            graph.validate_no_cycles()

            self._persist(graph)

        logger.info(
            "Subtask added",
            extra={
                "parent_id": parent_id,
                "subtask_id": subtask_id,
                "title": title,
                "depends_on": depends_on,
            },
        )
        return node

    def get_graph(self, parent_id: str) -> TaskGraph | None:
        """Return the :class:`TaskGraph` for the given parent task, or None.

        Args:
            parent_id: Identifier of the parent task.

        Returns:
            :class:`TaskGraph` when found, ``None`` otherwise.
        """
        with self._lock:
            self._ensure_loaded()
            return self._graphs.get(parent_id)

    def complete_subtask(self, parent_id: str, subtask_id: str) -> TaskGraph:
        """Mark a subtask as completed and return the updated graph.

        The subtask's ``status`` is set to ``completed`` and its
        ``completed_at`` timestamp is set to the current UTC time.

        Args:
            parent_id:  Identifier of the parent task's graph.
            subtask_id: Identifier of the subtask to mark complete.

        Returns:
            The updated :class:`TaskGraph` after the status change.

        Raises:
            KeyError:   If no graph exists for ``parent_id``, or if
                        ``subtask_id`` does not exist in the graph.
            ValueError: If the subtask is not in ``pending`` or
                        ``in_progress`` status (already terminal).
        """
        with self._lock:
            self._ensure_loaded()
            graph = self._graphs.get(parent_id)
            if graph is None:
                raise KeyError(f"No graph found for parent_id {parent_id!r}.")

            node = graph.nodes.get(subtask_id)
            if node is None:
                raise KeyError(
                    f"Subtask {subtask_id!r} not found in graph {parent_id!r}."
                )

            if node.status in ("completed", "failed"):
                raise ValueError(
                    f"Subtask {subtask_id!r} is already in terminal state "
                    f"{node.status!r} and cannot be completed again."
                )

            node.status = "completed"
            node.completed_at = datetime.now(UTC)
            self._persist(graph)

        logger.info(
            "Subtask completed",
            extra={"parent_id": parent_id, "subtask_id": subtask_id},
        )
        return graph

    def list_graphs(self) -> list[TaskGraph]:
        """Return all task graphs, ordered by ``created_at`` ascending.

        Returns:
            List of :class:`TaskGraph` objects.
        """
        with self._lock:
            self._ensure_loaded()
            graphs = list(self._graphs.values())
        graphs.sort(key=lambda g: g.created_at)
        return graphs


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_decomposer_instance: TaskDecomposer | None = None
_decomposer_lock: RLock = RLock()


def get_task_decomposer(
    storage_path: Path | None = None,
) -> TaskDecomposer:
    """Return the global :class:`TaskDecomposer` singleton.

    On the first call the singleton is constructed with ``storage_path``
    (defaulting to ``Path(".forge/decomposition/graphs.jsonl")``).
    Subsequent calls return the cached instance and ignore ``storage_path``.

    To replace the singleton (e.g. in tests) call
    :func:`reset_task_decomposer` first.

    Args:
        storage_path: Override the JSONL backing file path.  Only honoured
            on the first call.

    Returns:
        The singleton :class:`TaskDecomposer`.
    """
    global _decomposer_instance
    with _decomposer_lock:
        if _decomposer_instance is None:
            path = (
                storage_path if storage_path is not None else _DEFAULT_GRAPHS_PATH
            )
            _decomposer_instance = TaskDecomposer(storage_path=path)
        return _decomposer_instance


def reset_task_decomposer() -> None:
    """Reset the global singleton — intended for use in tests only."""
    global _decomposer_instance
    with _decomposer_lock:
        _decomposer_instance = None
