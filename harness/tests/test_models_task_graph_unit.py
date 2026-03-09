"""Comprehensive unit tests for forge_harness.webhook_server.models.task_graph.

Tests cover:
- SubtaskNode construction, field validation, defaults, and serialization
- TaskGraph construction, field validation, defaults, and serialization
- TaskGraph.add_node — happy path, duplicate-id error, mismatched parent_id error
- TaskGraph.get_ready_nodes — ordering, dependency states, mixed statuses
- TaskGraph.is_complete — empty graph, all-complete, all-failed, mixed, in-progress
- TaskGraph.completion_pct — empty graph, partial, full, mixed terminal states
- TaskGraph.validate_no_cycles — acyclic graph, single cycle, multiple paths,
  external dependency reference, disconnected components
- SubtaskStatus literal type values
- Edge cases: empty depends_on, None completed_at, single-node graph
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.task_graph import (
    SubtaskNode,
    SubtaskStatus,
    TaskGraph,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_PARENT_ID = "parent-task-001"
_PARENT_TITLE = "Build the Death Star"


def _make_node(
    subtask_id: str = "st-1",
    parent_id: str = _PARENT_ID,
    title: str = "Some subtask",
    task_type: TaskType = TaskType.bug_fix,
    risk_tier: RiskTier = RiskTier.low,
    status: SubtaskStatus = "pending",
    depends_on: list[str] | None = None,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> SubtaskNode:
    """Minimal factory to construct a SubtaskNode with sensible defaults."""
    kwargs: dict = dict(
        subtask_id=subtask_id,
        parent_id=parent_id,
        title=title,
        task_type=task_type,
        risk_tier=risk_tier,
        status=status,
        depends_on=depends_on if depends_on is not None else [],
    )
    if created_at is not None:
        kwargs["created_at"] = created_at
    if completed_at is not None:
        kwargs["completed_at"] = completed_at
    return SubtaskNode(**kwargs)


def _make_graph(
    parent_id: str = _PARENT_ID,
    parent_title: str = _PARENT_TITLE,
    nodes: dict | None = None,
) -> TaskGraph:
    kwargs: dict = dict(parent_id=parent_id, parent_title=parent_title)
    if nodes is not None:
        kwargs["nodes"] = nodes
    return TaskGraph(**kwargs)


# ---------------------------------------------------------------------------
# SubtaskNode construction tests
# ---------------------------------------------------------------------------


class TestSubtaskNodeConstruction:
    def test_minimal_valid_node(self):
        node = _make_node()
        assert node.subtask_id == "st-1"
        assert node.parent_id == _PARENT_ID
        assert node.title == "Some subtask"
        assert node.task_type == TaskType.bug_fix
        assert node.risk_tier == RiskTier.low
        assert node.status == "pending"
        assert node.depends_on == []
        assert node.completed_at is None
        assert isinstance(node.created_at, datetime)

    def test_created_at_is_utc_aware(self):
        node = _make_node()
        assert node.created_at.tzinfo is not None

    def test_status_default_is_pending(self):
        node = SubtaskNode(
            subtask_id="st-x",
            parent_id=_PARENT_ID,
            title="title",
            task_type=TaskType.docs_update,
            risk_tier=RiskTier.medium,
        )
        assert node.status == "pending"

    def test_depends_on_default_is_empty_list(self):
        node = SubtaskNode(
            subtask_id="st-x",
            parent_id=_PARENT_ID,
            title="title",
            task_type=TaskType.docs_update,
            risk_tier=RiskTier.medium,
        )
        assert node.depends_on == []

    def test_completed_at_can_be_set(self):
        ts = datetime.now(UTC)
        node = _make_node(completed_at=ts, status="completed")
        assert node.completed_at == ts

    def test_all_statuses_accepted(self):
        for status in ("pending", "in_progress", "completed", "failed"):
            node = _make_node(status=status)
            assert node.status == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            SubtaskNode(
                subtask_id="st-1",
                parent_id=_PARENT_ID,
                title="title",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                status="unknown_status",  # type: ignore[arg-type]
            )

    def test_empty_subtask_id_raises(self):
        with pytest.raises(ValidationError):
            SubtaskNode(
                subtask_id="",
                parent_id=_PARENT_ID,
                title="title",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
            )

    def test_empty_parent_id_raises(self):
        with pytest.raises(ValidationError):
            SubtaskNode(
                subtask_id="st-1",
                parent_id="",
                title="title",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
            )

    def test_empty_title_raises(self):
        with pytest.raises(ValidationError):
            SubtaskNode(
                subtask_id="st-1",
                parent_id=_PARENT_ID,
                title="",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
            )

    def test_depends_on_with_values(self):
        node = _make_node(depends_on=["st-0", "st-00"])
        assert node.depends_on == ["st-0", "st-00"]

    def test_node_is_mutable(self):
        node = _make_node()
        node.status = "completed"
        assert node.status == "completed"

    def test_all_task_types_accepted(self):
        for tt in TaskType:
            node = _make_node(task_type=tt)
            assert node.task_type == tt

    def test_all_risk_tiers_accepted(self):
        for rt in RiskTier:
            node = _make_node(risk_tier=rt)
            assert node.risk_tier == rt


# ---------------------------------------------------------------------------
# SubtaskNode serialization
# ---------------------------------------------------------------------------


class TestSubtaskNodeSerialization:
    def test_model_dump_contains_all_fields(self):
        node = _make_node()
        data = node.model_dump()
        expected_keys = {
            "subtask_id",
            "parent_id",
            "title",
            "task_type",
            "risk_tier",
            "status",
            "depends_on",
            "created_at",
            "completed_at",
        }
        assert expected_keys == set(data.keys())

    def test_model_dump_status_value(self):
        node = _make_node(status="in_progress")
        data = node.model_dump()
        assert data["status"] == "in_progress"

    def test_model_dump_completed_at_none(self):
        node = _make_node()
        data = node.model_dump()
        assert data["completed_at"] is None

    def test_round_trip_via_model_validate(self):
        original = _make_node(status="failed", depends_on=["dep-1"])
        dumped = original.model_dump()
        restored = SubtaskNode.model_validate(dumped)
        assert restored.subtask_id == original.subtask_id
        assert restored.status == original.status
        assert restored.depends_on == original.depends_on


# ---------------------------------------------------------------------------
# TaskGraph construction tests
# ---------------------------------------------------------------------------


class TestTaskGraphConstruction:
    def test_minimal_valid_graph(self):
        g = _make_graph()
        assert g.parent_id == _PARENT_ID
        assert g.parent_title == _PARENT_TITLE
        assert g.nodes == {}
        assert isinstance(g.created_at, datetime)

    def test_created_at_is_utc_aware(self):
        g = _make_graph()
        assert g.created_at.tzinfo is not None

    def test_empty_parent_id_raises(self):
        with pytest.raises(ValidationError):
            TaskGraph(parent_id="", parent_title=_PARENT_TITLE)

    def test_empty_parent_title_raises(self):
        with pytest.raises(ValidationError):
            TaskGraph(parent_id=_PARENT_ID, parent_title="")

    def test_nodes_default_is_empty_dict(self):
        g = TaskGraph(parent_id=_PARENT_ID, parent_title=_PARENT_TITLE)
        assert g.nodes == {}

    def test_graph_is_mutable(self):
        g = _make_graph()
        node = _make_node()
        g.nodes[node.subtask_id] = node
        assert "st-1" in g.nodes


# ---------------------------------------------------------------------------
# TaskGraph serialization
# ---------------------------------------------------------------------------


class TestTaskGraphSerialization:
    def test_model_dump_contains_all_fields(self):
        g = _make_graph()
        data = g.model_dump()
        assert set(data.keys()) == {"parent_id", "parent_title", "nodes", "created_at"}

    def test_round_trip_empty_graph(self):
        g = _make_graph()
        data = g.model_dump()
        restored = TaskGraph.model_validate(data)
        assert restored.parent_id == g.parent_id
        assert restored.nodes == {}

    def test_round_trip_with_nodes(self):
        g = _make_graph()
        node = _make_node()
        g.add_node(node)
        data = g.model_dump()
        restored = TaskGraph.model_validate(data)
        assert "st-1" in restored.nodes
        assert restored.nodes["st-1"].subtask_id == "st-1"


# ---------------------------------------------------------------------------
# TaskGraph.add_node
# ---------------------------------------------------------------------------


class TestAddNode:
    def test_add_single_node(self):
        g = _make_graph()
        node = _make_node()
        g.add_node(node)
        assert "st-1" in g.nodes
        assert g.nodes["st-1"] is node

    def test_add_multiple_nodes(self):
        g = _make_graph()
        for i in range(5):
            g.add_node(_make_node(subtask_id=f"st-{i}"))
        assert len(g.nodes) == 5

    def test_duplicate_subtask_id_raises(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-dup"))
        with pytest.raises(ValueError, match="already exists"):
            g.add_node(_make_node(subtask_id="st-dup"))

    def test_mismatched_parent_id_raises(self):
        g = _make_graph(parent_id="parent-A")
        node = _make_node(subtask_id="st-1", parent_id="parent-B")
        with pytest.raises(ValueError, match="does not match"):
            g.add_node(node)

    def test_error_message_contains_ids(self):
        g = _make_graph(parent_id="graph-parent")
        node = _make_node(subtask_id="st-1", parent_id="different-parent")
        with pytest.raises(ValueError) as exc_info:
            g.add_node(node)
        msg = str(exc_info.value)
        assert "different-parent" in msg
        assert "graph-parent" in msg

    def test_duplicate_error_message_contains_subtask_id(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-dup"))
        with pytest.raises(ValueError) as exc_info:
            g.add_node(_make_node(subtask_id="st-dup"))
        assert "st-dup" in str(exc_info.value)

    def test_add_node_with_dependency(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0"))
        g.add_node(_make_node(subtask_id="st-1", depends_on=["st-0"]))
        assert g.nodes["st-1"].depends_on == ["st-0"]


# ---------------------------------------------------------------------------
# TaskGraph.get_ready_nodes
# ---------------------------------------------------------------------------


class TestGetReadyNodes:
    def test_empty_graph_returns_empty_list(self):
        g = _make_graph()
        assert g.get_ready_nodes() == []

    def test_single_pending_no_deps_is_ready(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-1"))
        ready = g.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].subtask_id == "st-1"

    def test_in_progress_node_not_in_ready(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-1", status="in_progress"))
        assert g.get_ready_nodes() == []

    def test_completed_node_not_in_ready(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-1", status="completed"))
        assert g.get_ready_nodes() == []

    def test_failed_node_not_in_ready(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-1", status="failed"))
        assert g.get_ready_nodes() == []

    def test_pending_node_with_incomplete_dep_not_ready(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="dep-1", status="pending"))
        g.add_node(_make_node(subtask_id="st-1", depends_on=["dep-1"]))
        ready = g.get_ready_nodes()
        # Only dep-1 is ready (no deps); st-1 must wait
        ids = {n.subtask_id for n in ready}
        assert "dep-1" in ids
        assert "st-1" not in ids

    def test_pending_node_with_completed_dep_is_ready(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="dep-1", status="completed"))
        g.add_node(_make_node(subtask_id="st-1", depends_on=["dep-1"]))
        ready = g.get_ready_nodes()
        ids = {n.subtask_id for n in ready}
        assert "st-1" in ids
        assert "dep-1" not in ids  # completed, not pending

    def test_dep_in_progress_blocks_child(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="dep-1", status="in_progress"))
        g.add_node(_make_node(subtask_id="st-1", depends_on=["dep-1"]))
        ready = g.get_ready_nodes()
        ids = {n.subtask_id for n in ready}
        assert "st-1" not in ids

    def test_dep_failed_blocks_child(self):
        # A failed dependency is NOT "completed", so the child remains blocked
        g = _make_graph()
        g.add_node(_make_node(subtask_id="dep-1", status="failed"))
        g.add_node(_make_node(subtask_id="st-1", depends_on=["dep-1"]))
        ready = g.get_ready_nodes()
        ids = {n.subtask_id for n in ready}
        assert "st-1" not in ids

    def test_external_dependency_not_in_graph_blocks_node(self):
        # depends_on refs a node NOT present in the graph.
        # completed_ids only contains nodes that are IN the graph AND completed,
        # so "external-99" is absent from completed_ids, and the all() check
        # fails — the node is NOT ready.
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-1", depends_on=["external-99"]))
        ready = g.get_ready_nodes()
        ids = {n.subtask_id for n in ready}
        assert "st-1" not in ids

    def test_ordering_by_created_at_ascending(self):
        g = _make_graph()
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(3):
            g.add_node(
                _make_node(
                    subtask_id=f"st-{i}",
                    created_at=base_time + timedelta(minutes=i * 10),
                )
            )
        ready = g.get_ready_nodes()
        assert [n.subtask_id for n in ready] == ["st-0", "st-1", "st-2"]

    def test_ordering_reversed_created_at_still_ascending(self):
        g = _make_graph()
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        # Add nodes in reverse chronological order
        for i in reversed(range(3)):
            g.add_node(
                _make_node(
                    subtask_id=f"st-{i}",
                    created_at=base_time + timedelta(minutes=i * 10),
                )
            )
        ready = g.get_ready_nodes()
        assert [n.subtask_id for n in ready] == ["st-0", "st-1", "st-2"]

    def test_multiple_deps_all_completed(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="dep-a", status="completed"))
        g.add_node(_make_node(subtask_id="dep-b", status="completed"))
        g.add_node(_make_node(subtask_id="st-child", depends_on=["dep-a", "dep-b"]))
        ready = g.get_ready_nodes()
        ids = {n.subtask_id for n in ready}
        assert "st-child" in ids

    def test_multiple_deps_one_incomplete(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="dep-a", status="completed"))
        g.add_node(_make_node(subtask_id="dep-b", status="pending"))
        g.add_node(_make_node(subtask_id="st-child", depends_on=["dep-a", "dep-b"]))
        ready = g.get_ready_nodes()
        ids = {n.subtask_id for n in ready}
        assert "st-child" not in ids


# ---------------------------------------------------------------------------
# TaskGraph.is_complete
# ---------------------------------------------------------------------------


class TestIsComplete:
    def test_empty_graph_is_complete(self):
        g = _make_graph()
        assert g.is_complete() is True

    def test_all_completed_is_complete(self):
        g = _make_graph()
        for i in range(3):
            g.add_node(_make_node(subtask_id=f"st-{i}", status="completed"))
        assert g.is_complete() is True

    def test_all_failed_is_complete(self):
        g = _make_graph()
        for i in range(3):
            g.add_node(_make_node(subtask_id=f"st-{i}", status="failed"))
        assert g.is_complete() is True

    def test_mixed_completed_failed_is_complete(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", status="completed"))
        g.add_node(_make_node(subtask_id="st-1", status="failed"))
        assert g.is_complete() is True

    def test_pending_node_makes_incomplete(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", status="completed"))
        g.add_node(_make_node(subtask_id="st-1", status="pending"))
        assert g.is_complete() is False

    def test_in_progress_node_makes_incomplete(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", status="completed"))
        g.add_node(_make_node(subtask_id="st-1", status="in_progress"))
        assert g.is_complete() is False

    def test_single_pending_node_is_incomplete(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", status="pending"))
        assert g.is_complete() is False

    def test_single_completed_node_is_complete(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", status="completed"))
        assert g.is_complete() is True


# ---------------------------------------------------------------------------
# TaskGraph.completion_pct
# ---------------------------------------------------------------------------


class TestCompletionPct:
    def test_empty_graph_returns_zero(self):
        g = _make_graph()
        assert g.completion_pct() == 0.0

    def test_all_pending_returns_zero(self):
        g = _make_graph()
        for i in range(4):
            g.add_node(_make_node(subtask_id=f"st-{i}", status="pending"))
        assert g.completion_pct() == 0.0

    def test_all_completed_returns_100(self):
        g = _make_graph()
        for i in range(4):
            g.add_node(_make_node(subtask_id=f"st-{i}", status="completed"))
        assert g.completion_pct() == 100.0

    def test_all_failed_returns_100(self):
        g = _make_graph()
        for i in range(4):
            g.add_node(_make_node(subtask_id=f"st-{i}", status="failed"))
        assert g.completion_pct() == 100.0

    def test_half_completed_returns_50(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", status="completed"))
        g.add_node(_make_node(subtask_id="st-1", status="pending"))
        assert g.completion_pct() == 50.0

    def test_one_of_four_completed_returns_25(self):
        g = _make_graph()
        statuses = ["completed", "pending", "pending", "pending"]
        for i, s in enumerate(statuses):
            g.add_node(_make_node(subtask_id=f"st-{i}", status=s))
        assert g.completion_pct() == 25.0

    def test_mixed_terminal_and_active(self):
        g = _make_graph()
        # 3 terminal out of 4
        g.add_node(_make_node(subtask_id="st-0", status="completed"))
        g.add_node(_make_node(subtask_id="st-1", status="failed"))
        g.add_node(_make_node(subtask_id="st-2", status="completed"))
        g.add_node(_make_node(subtask_id="st-3", status="in_progress"))
        assert g.completion_pct() == 75.0

    def test_return_type_is_float(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", status="completed"))
        assert isinstance(g.completion_pct(), float)

    def test_single_node_completed_is_100(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", status="completed"))
        assert g.completion_pct() == 100.0

    def test_single_node_pending_is_0(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", status="pending"))
        assert g.completion_pct() == 0.0


# ---------------------------------------------------------------------------
# TaskGraph.validate_no_cycles
# ---------------------------------------------------------------------------


class TestValidateNoCycles:
    def test_empty_graph_no_cycle(self):
        g = _make_graph()
        g.validate_no_cycles()  # should not raise

    def test_single_node_no_cycle(self):
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0"))
        g.validate_no_cycles()  # should not raise

    def test_linear_chain_no_cycle(self):
        # st-0 <- st-1 <- st-2 (st-2 depends on st-1 depends on st-0)
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0"))
        g.add_node(_make_node(subtask_id="st-1", depends_on=["st-0"]))
        g.add_node(_make_node(subtask_id="st-2", depends_on=["st-1"]))
        g.validate_no_cycles()  # should not raise

    def test_diamond_no_cycle(self):
        # st-0 <- st-1, st-2 <- st-3 (diamond; no cycle)
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0"))
        g.add_node(_make_node(subtask_id="st-1", depends_on=["st-0"]))
        g.add_node(_make_node(subtask_id="st-2", depends_on=["st-0"]))
        g.add_node(_make_node(subtask_id="st-3", depends_on=["st-1", "st-2"]))
        g.validate_no_cycles()  # should not raise

    def test_direct_self_cycle_raises(self):
        g = _make_graph()
        # Manually bypass add_node to plant the cycle
        node = SubtaskNode(
            subtask_id="st-loop",
            parent_id=_PARENT_ID,
            title="Self-referencing node",
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            depends_on=["st-loop"],
        )
        g.nodes["st-loop"] = node
        with pytest.raises(ValueError, match="Cycle detected"):
            g.validate_no_cycles()

    def test_two_node_cycle_raises(self):
        g = _make_graph()
        node_a = SubtaskNode(
            subtask_id="st-a",
            parent_id=_PARENT_ID,
            title="A",
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            depends_on=["st-b"],
        )
        node_b = SubtaskNode(
            subtask_id="st-b",
            parent_id=_PARENT_ID,
            title="B",
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            depends_on=["st-a"],
        )
        g.nodes["st-a"] = node_a
        g.nodes["st-b"] = node_b
        with pytest.raises(ValueError, match="Cycle detected"):
            g.validate_no_cycles()

    def test_three_node_cycle_raises(self):
        g = _make_graph()
        # st-a -> st-b -> st-c -> st-a
        for nid, dep in [("st-a", "st-c"), ("st-b", "st-a"), ("st-c", "st-b")]:
            g.nodes[nid] = SubtaskNode(
                subtask_id=nid,
                parent_id=_PARENT_ID,
                title=nid,
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                depends_on=[dep],
            )
        with pytest.raises(ValueError, match="Cycle detected"):
            g.validate_no_cycles()

    def test_cycle_error_message_contains_parent_id(self):
        g = _make_graph(parent_id="my-parent")
        g.nodes["st-loop"] = SubtaskNode(
            subtask_id="st-loop",
            parent_id="my-parent",
            title="loop",
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            depends_on=["st-loop"],
        )
        with pytest.raises(ValueError) as exc_info:
            g.validate_no_cycles()
        assert "my-parent" in str(exc_info.value)

    def test_external_dependency_not_in_graph_ignored(self):
        # A node depends on an id that does not exist in the graph.
        # validate_no_cycles should silently skip the unknown dep.
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0", depends_on=["ghost-node"]))
        g.validate_no_cycles()  # should not raise

    def test_disconnected_components_no_cycle(self):
        g = _make_graph()
        # Component 1: st-0 <- st-1
        g.add_node(_make_node(subtask_id="st-0"))
        g.add_node(_make_node(subtask_id="st-1", depends_on=["st-0"]))
        # Component 2: st-2 (independent)
        g.add_node(_make_node(subtask_id="st-2"))
        g.validate_no_cycles()  # should not raise

    def test_already_black_node_not_revisited(self):
        # Both st-1 and st-2 depend on st-0; DFS will mark st-0 black the
        # first time, then skip it on the second visit. No false cycle error.
        g = _make_graph()
        g.add_node(_make_node(subtask_id="st-0"))
        g.add_node(_make_node(subtask_id="st-1", depends_on=["st-0"]))
        g.add_node(_make_node(subtask_id="st-2", depends_on=["st-0"]))
        g.validate_no_cycles()  # should not raise


# ---------------------------------------------------------------------------
# SubtaskStatus type alias sanity checks
# ---------------------------------------------------------------------------


class TestSubtaskStatusLiteral:
    """Ensure the literal type values are exactly as documented."""

    VALID_STATUSES = ("pending", "in_progress", "completed", "failed")

    def test_valid_statuses_accepted_by_node(self):
        for s in self.VALID_STATUSES:
            node = _make_node(status=s)
            assert node.status == s

    def test_invalid_status_rejected(self):
        for bad in ("PENDING", "done", "", "cancelled", None):
            with pytest.raises((ValidationError, Exception)):
                SubtaskNode(
                    subtask_id="st-x",
                    parent_id=_PARENT_ID,
                    title="title",
                    task_type=TaskType.bug_fix,
                    risk_tier=RiskTier.low,
                    status=bad,  # type: ignore[arg-type]
                )


# ---------------------------------------------------------------------------
# Integration-style: full lifecycle scenario
# ---------------------------------------------------------------------------


class TestFullLifecycleScenario:
    """Simulate a realistic multi-step task graph lifecycle."""

    def _build_graph(self) -> TaskGraph:
        g = _make_graph()
        base = datetime(2026, 2, 1, tzinfo=UTC)
        # st-setup has no deps
        g.add_node(_make_node("st-setup", created_at=base))
        # st-impl depends on st-setup
        g.add_node(_make_node("st-impl", depends_on=["st-setup"], created_at=base + timedelta(minutes=1)))
        # st-test depends on st-impl
        g.add_node(_make_node("st-test", depends_on=["st-impl"], created_at=base + timedelta(minutes=2)))
        # st-deploy depends on st-impl AND st-test
        g.add_node(
            _make_node(
                "st-deploy",
                depends_on=["st-impl", "st-test"],
                created_at=base + timedelta(minutes=3),
            )
        )
        return g

    def test_initial_state_only_setup_is_ready(self):
        g = self._build_graph()
        ready = g.get_ready_nodes()
        assert [n.subtask_id for n in ready] == ["st-setup"]

    def test_initial_not_complete(self):
        g = self._build_graph()
        assert g.is_complete() is False

    def test_initial_completion_pct_is_zero(self):
        g = self._build_graph()
        assert g.completion_pct() == 0.0

    def test_after_setup_completes_impl_is_ready(self):
        g = self._build_graph()
        g.nodes["st-setup"].status = "completed"
        ready = g.get_ready_nodes()
        ids = {n.subtask_id for n in ready}
        assert "st-impl" in ids
        assert "st-test" not in ids
        assert "st-deploy" not in ids

    def test_after_impl_completes_test_is_ready(self):
        g = self._build_graph()
        g.nodes["st-setup"].status = "completed"
        g.nodes["st-impl"].status = "completed"
        ready = g.get_ready_nodes()
        ids = {n.subtask_id for n in ready}
        assert "st-test" in ids
        assert "st-deploy" not in ids

    def test_after_all_deps_deploy_is_ready(self):
        g = self._build_graph()
        g.nodes["st-setup"].status = "completed"
        g.nodes["st-impl"].status = "completed"
        g.nodes["st-test"].status = "completed"
        ready = g.get_ready_nodes()
        ids = {n.subtask_id for n in ready}
        assert "st-deploy" in ids

    def test_all_terminal_graph_complete(self):
        g = self._build_graph()
        for node in g.nodes.values():
            node.status = "completed"
        assert g.is_complete() is True
        assert g.completion_pct() == 100.0

    def test_partial_progress_completion_pct(self):
        g = self._build_graph()
        g.nodes["st-setup"].status = "completed"
        g.nodes["st-impl"].status = "completed"
        # 2 of 4 = 50%
        assert g.completion_pct() == 50.0

    def test_validate_no_cycles_acyclic_graph(self):
        g = self._build_graph()
        g.validate_no_cycles()  # should not raise
