"""DAG utilities for workflow dependency management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TaskNode:
    """A node in the workflow DAG representing a task."""

    id: str
    name: str
    agent_type: str | None = None
    agent_capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    retry_count: int = 3
    retry_delay: int = 5
    timeout: int = 300
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, waiting, running, completed, failed, skipped

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TaskNode):
            return False
        return self.id == other.id


class DAG:
    """Directed Acyclic Graph for workflow execution.

    Manages task dependencies and provides topological ordering for execution.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, TaskNode] = {}
        self._edges: dict[str, set[str]] = {}  # task_id -> set of dependent task_ids
        self._reverse_edges: dict[str, set[str]] = {}  # task_id -> set of dependency task_ids

    def add_node(self, node: TaskNode) -> None:
        """Add a task node to the DAG."""
        if node.id in self._nodes:
            raise ValueError(f"Node {node.id} already exists")
        self._nodes[node.id] = node
        self._edges[node.id] = set()
        self._reverse_edges[node.id] = set(node.dependencies)

        # Update edges for dependencies
        for dep_id in node.dependencies:
            if dep_id not in self._edges:
                self._edges[dep_id] = set()
            self._edges[dep_id].add(node.id)

    def add_edge(self, from_id: str, to_id: str) -> None:
        """Add a dependency edge from from_id to to_id (to_id depends on from_id)."""
        if from_id not in self._nodes:
            raise ValueError(f"Source node {from_id} does not exist")
        if to_id not in self._nodes:
            raise ValueError(f"Target node {to_id} does not exist")

        # Check for cycles
        if self._would_create_cycle(from_id, to_id):
            raise ValueError(f"Adding edge {from_id} -> {to_id} would create a cycle")

        self._edges[from_id].add(to_id)
        self._reverse_edges[to_id].add(from_id)
        self._nodes[to_id].dependencies.append(from_id)

    def _would_create_cycle(self, from_id: str, to_id: str) -> bool:
        """Check if adding edge from_id -> to_id would create a cycle."""
        # If to_id can reach from_id, adding edge would create cycle
        visited: set[str] = set()
        stack = [to_id]

        while stack:
            current = stack.pop()
            if current == from_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            # Check existing edges from current
            for dep in self._nodes[current].dependencies:
                stack.append(dep)

        return False

    def topological_sort(self) -> list[TaskNode]:
        """Return nodes in topological order (dependencies first)."""
        in_degree = {node_id: len(self._reverse_edges[node_id]) for node_id in self._nodes}
        queue = [node_id for node_id, degree in in_degree.items() if degree == 0]
        result: list[TaskNode] = []

        while queue:
            # Sort for deterministic ordering
            queue.sort()
            node_id = queue.pop(0)
            result.append(self._nodes[node_id])

            for dependent_id in sorted(self._edges[node_id]):
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    queue.append(dependent_id)

        if len(result) != len(self._nodes):
            raise ValueError("Cycle detected in DAG")

        return result

    def get_ready_tasks(self) -> list[TaskNode]:
        """Get tasks that are ready to run (all dependencies completed)."""
        ready = []
        for node in self._nodes.values():
            if node.status != "pending":
                continue

            # Check if all dependencies are completed
            deps_completed = all(
                self._nodes[dep_id].status in ("completed", "skipped")
                for dep_id in node.dependencies
            )
            if deps_completed:
                ready.append(node)

        return ready

    def get_parallel_groups(self) -> list[list[TaskNode]]:
        """Group tasks into parallel execution levels based on dependencies."""
        levels: list[list[TaskNode]] = []
        completed: set[str] = set()

        while len(completed) < len(self._nodes):
            # Find all tasks whose dependencies are completed
            current_level: list[TaskNode] = []
            for node in self._nodes.values():
                if node.id in completed:
                    continue
                if all(dep_id in completed for dep_id in node.dependencies):
                    current_level.append(node)

            if not current_level:
                raise ValueError("Cycle detected in DAG")

            levels.append(current_level)
            for node in current_level:
                completed.add(node.id)

        return levels

    def get_critical_path(self) -> list[TaskNode]:
        """Get the critical path (longest dependency chain)."""
        # Calculate longest path to each node
        longest_path: dict[str, int] = {node_id: 0 for node_id in self._nodes}
        topo_order = self.topological_sort()

        for node in topo_order:
            for dependent_id in self._edges[node.id]:
                longest_path[dependent_id] = max(
                    longest_path[dependent_id],
                    longest_path[node.id] + 1,
                )

        # Find the end node with longest path
        end_node_id = max(longest_path, key=lambda k: longest_path[k])

        # Backtrack to find the path
        path: list[TaskNode] = []
        current_id = end_node_id

        while current_id:
            path.append(self._nodes[current_id])
            # Find predecessor with longest path
            predecessors = [
                dep_id
                for dep_id in self._nodes[current_id].dependencies
                if longest_path[dep_id] == longest_path[current_id] - 1
            ]
            current_id = predecessors[0] if predecessors else None

        return list(reversed(path))

    def to_dict(self) -> dict[str, Any]:
        """Serialize DAG to dictionary."""
        return {
            "nodes": [
                {
                    "id": node.id,
                    "name": node.name,
                    "agent_type": node.agent_type,
                    "agent_capabilities": node.agent_capabilities,
                    "dependencies": node.dependencies,
                    "retry_count": node.retry_count,
                    "retry_delay": node.retry_delay,
                    "timeout": node.timeout,
                    "metadata": node.metadata,
                    "status": node.status,
                }
                for node in self._nodes.values()
            ],
            "edges": {k: list(v) for k, v in self._edges.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAG:
        """Deserialize DAG from dictionary."""
        dag = cls()
        for node_data in data.get("nodes", []):
            node = TaskNode(
                id=node_data["id"],
                name=node_data["name"],
                agent_type=node_data.get("agent_type"),
                agent_capabilities=node_data.get("agent_capabilities", []),
                dependencies=node_data.get("dependencies", []),
                retry_count=node_data.get("retry_count", 3),
                retry_delay=node_data.get("retry_delay", 5),
                timeout=node_data.get("timeout", 300),
                metadata=node_data.get("metadata", {}),
                status=node_data.get("status", "pending"),
            )
            dag.add_node(node)
        return dag
