"""Task Decomposition Graph Model for the FORGE Dark Factory.

Defines the data models for representing a parent task broken into a directed
acyclic graph (DAG) of subtasks with explicit dependency tracking.

Classes
-------
SubtaskNode
    Pydantic model representing a single subtask within a DAG.
TaskGraph
    Pydantic model representing a parent task's full subtask DAG with
    dependency resolution, cycle detection, and completion tracking.

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2002)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType

# ---------------------------------------------------------------------------
# Status literal type
# ---------------------------------------------------------------------------

SubtaskStatus = Literal["pending", "in_progress", "completed", "failed"]

# ---------------------------------------------------------------------------
# SubtaskNode model
# ---------------------------------------------------------------------------


class SubtaskNode(BaseModel):
    """A single node in a task decomposition DAG.

    Attributes:
        subtask_id:   Unique identifier for this subtask (within the graph).
        parent_id:    Identifier of the parent task that owns this graph.
        title:        Human-readable description of the subtask.
        task_type:    Canonical work category (used for lane routing).
        risk_tier:    Risk classification for this subtask.
        status:       Current execution state. One of: pending, in_progress,
                      completed, failed.
        depends_on:   List of subtask_ids that must be completed before this
                      node becomes eligible for execution.  Empty means no
                      dependencies (immediately ready).
        created_at:   UTC timestamp when this node was created.
        completed_at: UTC timestamp when the node reached the completed or
                      failed terminal state.  ``None`` while still active.
    """

    subtask_id: str = Field(
        ...,
        min_length=1,
        description="Unique identifier for this subtask within the parent graph.",
    )
    parent_id: str = Field(
        ...,
        min_length=1,
        description="Identifier of the parent task that owns this graph.",
    )
    title: str = Field(
        ...,
        min_length=1,
        description="Human-readable description of the subtask.",
    )
    task_type: TaskType = Field(
        ...,
        description="Canonical work category for Dark Factory routing.",
    )
    risk_tier: RiskTier = Field(
        ...,
        description="Risk classification for this subtask instance.",
    )
    status: SubtaskStatus = Field(
        default="pending",
        description="Current execution state of the subtask.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="subtask_ids that must complete before this node can start.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when this node was created.",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when this node reached a terminal state.",
    )

    model_config = {"frozen": False, "populate_by_name": True}


# ---------------------------------------------------------------------------
# TaskGraph model
# ---------------------------------------------------------------------------


class TaskGraph(BaseModel):
    """Directed acyclic graph of subtasks decomposed from a parent task.

    The graph is keyed by ``subtask_id`` so that dependency lookups are O(1).
    All mutation methods (``add_node``) maintain the in-memory graph and
    delegate persistence to the calling service.

    Attributes:
        parent_id:    Identifier of the parent task.
        parent_title: Human-readable title of the parent task.
        nodes:        Dict mapping ``subtask_id`` to :class:`SubtaskNode`.
        created_at:   UTC timestamp when the graph was created.
    """

    parent_id: str = Field(
        ...,
        min_length=1,
        description="Identifier of the parent task.",
    )
    parent_title: str = Field(
        ...,
        min_length=1,
        description="Human-readable title of the parent task.",
    )
    nodes: dict[str, SubtaskNode] = Field(
        default_factory=dict,
        description="Mapping of subtask_id -> SubtaskNode.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when this graph was created.",
    )

    model_config = {"frozen": False, "populate_by_name": True}

    # ------------------------------------------------------------------
    # Graph mutation
    # ------------------------------------------------------------------

    def add_node(self, node: SubtaskNode) -> None:
        """Add a subtask node to the graph.

        Args:
            node: The :class:`SubtaskNode` to add.  Its ``subtask_id`` must
                  be unique within this graph.

        Raises:
            ValueError: If a node with the same ``subtask_id`` already exists,
                        or if ``node.parent_id`` does not match this graph's
                        ``parent_id``.
        """
        if node.parent_id != self.parent_id:
            raise ValueError(
                f"Node parent_id {node.parent_id!r} does not match graph "
                f"parent_id {self.parent_id!r}"
            )
        if node.subtask_id in self.nodes:
            raise ValueError(f"Node {node.subtask_id!r} already exists in graph {self.parent_id!r}")
        self.nodes[node.subtask_id] = node

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    def get_ready_nodes(self) -> list[SubtaskNode]:
        """Return nodes whose dependency constraints are satisfied.

        A node is *ready* when:
        - Its status is ``pending``.
        - Every subtask_id listed in ``depends_on`` maps to a node whose
          status is ``completed``.

        Returns:
            List of :class:`SubtaskNode` objects eligible for immediate
            dispatch, ordered by ``created_at`` ascending.
        """
        completed_ids: set[str] = {nid for nid, n in self.nodes.items() if n.status == "completed"}
        ready = [
            node
            for node in self.nodes.values()
            if node.status == "pending" and all(dep in completed_ids for dep in node.depends_on)
        ]
        ready.sort(key=lambda n: n.created_at)
        return ready

    # ------------------------------------------------------------------
    # Completion tracking
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return True when every node in the graph has reached a terminal state.

        A graph with no nodes is considered complete (vacuously true).
        Terminal states are ``completed`` and ``failed``.

        Returns:
            ``True`` when all nodes are in ``completed`` or ``failed`` status.
        """
        if not self.nodes:
            return True
        return all(n.status in ("completed", "failed") for n in self.nodes.values())

    def completion_pct(self) -> float:
        """Return the fraction of nodes in a terminal state as a percentage.

        Returns 0.0 for an empty graph (no nodes to complete).

        Returns:
            Float in the range [0.0, 100.0] representing completion percentage.
        """
        if not self.nodes:
            return 0.0
        terminal_count = sum(1 for n in self.nodes.values() if n.status in ("completed", "failed"))
        return (terminal_count / len(self.nodes)) * 100.0

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def validate_no_cycles(self) -> None:
        """Assert that the dependency graph contains no cycles.

        Uses iterative depth-first search with a three-colour marking scheme
        (white / grey / black) to detect back-edges.

        Raises:
            ValueError: If any cycle is detected.  The error message names
                        the subtask_id where the cycle was discovered.
        """
        white, grey, black = 0, 1, 2
        colour: dict[str, int] = {nid: white for nid in self.nodes}

        def _dfs(nid: str) -> None:
            colour[nid] = grey
            node = self.nodes[nid]
            for dep in node.depends_on:
                if dep not in colour:
                    # Dependency references a node not in this graph — skip.
                    continue
                if colour[dep] == grey:
                    raise ValueError(
                        f"Cycle detected in task graph {self.parent_id!r}: "
                        f"subtask {nid!r} depends on {dep!r} which is already "
                        f"in the current DFS path."
                    )
                if colour[dep] == white:
                    _dfs(dep)
            colour[nid] = black

        for nid in list(self.nodes):
            if colour[nid] == white:
                _dfs(nid)
