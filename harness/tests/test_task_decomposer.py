"""Tests for the Task Decomposition Service (DF-2002).

Coverage areas:
- SubtaskNode: valid construction, field defaults, edge cases
- TaskGraph: add_node, get_ready_nodes (respects dependencies), is_complete,
             completion_pct, cycle detection
- TaskDecomposer: create_graph, add_subtask, complete_subtask flow
- TaskDecomposer: get_ready_nodes after partial completion
- TaskDecomposer: list_graphs
- Persistence: graphs survive restart (JSONL round-trip)
- Singleton pattern: get_task_decomposer / reset_task_decomposer
- Edge cases: empty graph, single node, diamond dependency
- Thread safety: concurrent writes do not corrupt state
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.task_graph import SubtaskNode, TaskGraph
from forge_harness.webhook_server.services.task_decomposer import (
    TaskDecomposer,
    get_task_decomposer,
    reset_task_decomposer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    subtask_id: str = "sub-001",
    parent_id: str = "TASK-001",
    title: str = "Write tests",
    task_type: TaskType = TaskType.test_writing,
    risk_tier: RiskTier = RiskTier.low,
    status: str = "pending",
    depends_on: list[str] | None = None,
) -> SubtaskNode:
    return SubtaskNode(
        subtask_id=subtask_id,
        parent_id=parent_id,
        title=title,
        task_type=task_type,
        risk_tier=risk_tier,
        status=status,  # type: ignore[arg-type]
        depends_on=depends_on or [],
    )


def _make_graph(
    parent_id: str = "TASK-001",
    parent_title: str = "Build Auth Module",
) -> TaskGraph:
    return TaskGraph(parent_id=parent_id, parent_title=parent_title)


def _make_service(tmp_path: Path) -> TaskDecomposer:
    return TaskDecomposer(storage_path=tmp_path / "graphs.jsonl")


# ===========================================================================
# 1. SubtaskNode — model construction and defaults
# ===========================================================================


class TestSubtaskNodeConstruction:
    """SubtaskNode must construct correctly and apply proper defaults."""

    def test_valid_construction(self):
        node = _make_node()
        assert node.subtask_id == "sub-001"
        assert node.parent_id == "TASK-001"
        assert node.title == "Write tests"
        assert node.task_type == TaskType.test_writing
        assert node.risk_tier == RiskTier.low

    def test_default_status_is_pending(self):
        node = _make_node()
        assert node.status == "pending"

    def test_default_depends_on_is_empty(self):
        node = _make_node()
        assert node.depends_on == []

    def test_default_completed_at_is_none(self):
        node = _make_node()
        assert node.completed_at is None

    def test_created_at_is_set_automatically(self):
        before = datetime.now(UTC)
        node = _make_node()
        after = datetime.now(UTC)
        assert before <= node.created_at <= after

    def test_explicit_status_in_progress(self):
        node = _make_node(status="in_progress")
        assert node.status == "in_progress"

    def test_explicit_status_completed(self):
        node = _make_node(status="completed")
        assert node.status == "completed"

    def test_explicit_status_failed(self):
        node = _make_node(status="failed")
        assert node.status == "failed"

    def test_depends_on_populated(self):
        node = _make_node(depends_on=["sub-000", "sub-001"])
        assert node.depends_on == ["sub-000", "sub-001"]

    def test_subtask_id_required(self):
        with pytest.raises(ValidationError):
            SubtaskNode(
                subtask_id="",
                parent_id="TASK-001",
                title="title",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
            )

    def test_parent_id_required(self):
        with pytest.raises(ValidationError):
            SubtaskNode(
                subtask_id="sub-001",
                parent_id="",
                title="title",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
            )

    def test_title_required(self):
        with pytest.raises(ValidationError):
            SubtaskNode(
                subtask_id="sub-001",
                parent_id="TASK-001",
                title="",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
            )

    def test_serialization_round_trip(self):
        node = _make_node(depends_on=["sub-000"])
        data = node.model_dump()
        restored = SubtaskNode.model_validate(data)
        assert restored.subtask_id == node.subtask_id
        assert restored.depends_on == node.depends_on

    def test_json_round_trip(self):
        node = _make_node()
        restored = SubtaskNode.model_validate_json(node.model_dump_json())
        assert restored == node


# ===========================================================================
# 2. TaskGraph — structural operations
# ===========================================================================


class TestTaskGraphAddNode:
    """TaskGraph.add_node must enforce invariants."""

    def test_add_node_populates_nodes_dict(self):
        graph = _make_graph()
        node = _make_node()
        graph.add_node(node)
        assert "sub-001" in graph.nodes

    def test_add_multiple_nodes(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001"))
        graph.add_node(_make_node("sub-002", title="Implement endpoint"))
        assert len(graph.nodes) == 2

    def test_add_node_duplicate_id_raises(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001"))
        with pytest.raises(ValueError, match="already exists"):
            graph.add_node(_make_node("sub-001"))

    def test_add_node_wrong_parent_raises(self):
        graph = _make_graph(parent_id="TASK-001")
        node = _make_node(parent_id="TASK-999")
        with pytest.raises(ValueError, match="parent_id"):
            graph.add_node(node)


class TestTaskGraphGetReadyNodes:
    """get_ready_nodes must respect dependency constraints."""

    def test_empty_graph_returns_no_ready_nodes(self):
        graph = _make_graph()
        assert graph.get_ready_nodes() == []

    def test_single_node_no_deps_is_ready(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001"))
        ready = graph.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].subtask_id == "sub-001"

    def test_node_with_uncompleted_dep_is_not_ready(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001"))
        graph.add_node(_make_node("sub-002", depends_on=["sub-001"]))
        ready = graph.get_ready_nodes()
        # sub-002 depends on sub-001 which is still pending
        ready_ids = [n.subtask_id for n in ready]
        assert "sub-001" in ready_ids
        assert "sub-002" not in ready_ids

    def test_node_becomes_ready_after_dep_completes(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001"))
        graph.add_node(_make_node("sub-002", depends_on=["sub-001"]))
        # Mark sub-001 completed
        graph.nodes["sub-001"].status = "completed"
        ready_ids = [n.subtask_id for n in graph.get_ready_nodes()]
        assert "sub-002" in ready_ids
        assert "sub-001" not in ready_ids  # completed, not pending

    def test_in_progress_nodes_not_returned(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001", status="in_progress"))
        assert graph.get_ready_nodes() == []

    def test_diamond_dependency_resolves_correctly(self):
        """A -> B, A -> C, B -> D, C -> D (diamond pattern)."""
        graph = _make_graph()
        graph.add_node(_make_node("A"))
        graph.add_node(_make_node("B", depends_on=["A"]))
        graph.add_node(_make_node("C", depends_on=["A"]))
        graph.add_node(_make_node("D", depends_on=["B", "C"]))

        # Initially only A is ready
        ready = [n.subtask_id for n in graph.get_ready_nodes()]
        assert ready == ["A"]

        # After A completes, B and C become ready
        graph.nodes["A"].status = "completed"
        ready = sorted(n.subtask_id for n in graph.get_ready_nodes())
        assert ready == ["B", "C"]

        # After B and C complete, D becomes ready
        graph.nodes["B"].status = "completed"
        graph.nodes["C"].status = "completed"
        ready = graph.get_ready_nodes()
        assert [n.subtask_id for n in ready] == ["D"]

    def test_ready_nodes_ordered_by_created_at(self):
        t1 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
        t2 = datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC)
        graph = _make_graph()
        node_b = _make_node("B", title="Second")
        node_b.created_at = t2
        node_a = _make_node("A", title="First")
        node_a.created_at = t1
        # Add in reverse order
        graph.add_node(node_b)
        graph.add_node(node_a)
        ready = graph.get_ready_nodes()
        assert [n.subtask_id for n in ready] == ["A", "B"]


class TestTaskGraphIsComplete:
    """is_complete must return correct boolean for various graph states."""

    def test_empty_graph_is_complete(self):
        graph = _make_graph()
        assert graph.is_complete() is True

    def test_single_pending_node_not_complete(self):
        graph = _make_graph()
        graph.add_node(_make_node())
        assert graph.is_complete() is False

    def test_single_completed_node_is_complete(self):
        graph = _make_graph()
        graph.add_node(_make_node(status="completed"))
        assert graph.is_complete() is True

    def test_single_failed_node_is_complete(self):
        graph = _make_graph()
        graph.add_node(_make_node(status="failed"))
        assert graph.is_complete() is True

    def test_mixed_terminal_states_complete(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001", status="completed"))
        graph.add_node(_make_node("sub-002", status="failed"))
        assert graph.is_complete() is True

    def test_one_pending_makes_not_complete(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001", status="completed"))
        graph.add_node(_make_node("sub-002", status="pending"))
        assert graph.is_complete() is False

    def test_in_progress_makes_not_complete(self):
        graph = _make_graph()
        graph.add_node(_make_node(status="in_progress"))
        assert graph.is_complete() is False


class TestTaskGraphCompletionPct:
    """completion_pct must return accurate percentages."""

    def test_empty_graph_returns_zero(self):
        graph = _make_graph()
        assert graph.completion_pct() == 0.0

    def test_all_pending_returns_zero(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001"))
        graph.add_node(_make_node("sub-002"))
        assert graph.completion_pct() == 0.0

    def test_all_completed_returns_hundred(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001", status="completed"))
        graph.add_node(_make_node("sub-002", status="completed"))
        assert graph.completion_pct() == 100.0

    def test_half_completed(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001", status="completed"))
        graph.add_node(_make_node("sub-002", status="pending"))
        assert graph.completion_pct() == 50.0

    def test_failed_counts_as_terminal(self):
        graph = _make_graph()
        graph.add_node(_make_node("sub-001", status="completed"))
        graph.add_node(_make_node("sub-002", status="failed"))
        graph.add_node(_make_node("sub-003", status="pending"))
        # 2 of 3 are terminal -> 66.66...%
        assert abs(graph.completion_pct() - 66.666) < 0.01

    def test_single_node_completed(self):
        graph = _make_graph()
        graph.add_node(_make_node(status="completed"))
        assert graph.completion_pct() == 100.0


class TestTaskGraphCycleDetection:
    """validate_no_cycles must detect circular dependency chains."""

    def test_linear_chain_no_cycle(self):
        graph = _make_graph()
        graph.add_node(_make_node("A"))
        graph.add_node(_make_node("B", depends_on=["A"]))
        graph.add_node(_make_node("C", depends_on=["B"]))
        # Should not raise
        graph.validate_no_cycles()

    def test_simple_cycle_a_depends_on_b_b_depends_on_a(self):
        graph = _make_graph()
        # Manually insert nodes with circular deps (bypassing add_node validation)
        node_a = _make_node("A", depends_on=["B"])
        node_b = _make_node("B", depends_on=["A"])
        graph.nodes["A"] = node_a
        graph.nodes["B"] = node_b
        with pytest.raises(ValueError, match="[Cc]ycle"):
            graph.validate_no_cycles()

    def test_three_node_cycle_a_b_c_a(self):
        graph = _make_graph()
        node_a = _make_node("A", depends_on=["C"])
        node_b = _make_node("B", depends_on=["A"])
        node_c = _make_node("C", depends_on=["B"])
        graph.nodes["A"] = node_a
        graph.nodes["B"] = node_b
        graph.nodes["C"] = node_c
        with pytest.raises(ValueError, match="[Cc]ycle"):
            graph.validate_no_cycles()

    def test_self_loop_cycle(self):
        graph = _make_graph()
        node_a = _make_node("A", depends_on=["A"])
        graph.nodes["A"] = node_a
        with pytest.raises(ValueError, match="[Cc]ycle"):
            graph.validate_no_cycles()

    def test_diamond_no_cycle(self):
        """Diamond dependencies are valid (A -> B, A -> C, B -> D, C -> D)."""
        graph = _make_graph()
        graph.add_node(_make_node("A"))
        graph.add_node(_make_node("B", depends_on=["A"]))
        graph.add_node(_make_node("C", depends_on=["A"]))
        graph.add_node(_make_node("D", depends_on=["B", "C"]))
        graph.validate_no_cycles()  # Should not raise

    def test_external_dep_not_in_graph_is_skipped(self):
        """A dependency on a node not in the graph does not cause a crash."""
        graph = _make_graph()
        node_a = _make_node("A", depends_on=["EXTERNAL-ID"])
        graph.add_node(node_a)
        # Should not raise — external deps are silently ignored
        graph.validate_no_cycles()


# ===========================================================================
# 3. TaskDecomposer — service operations
# ===========================================================================


class TestTaskDecomposerCreateGraph:
    """create_graph must create and persist empty graphs."""

    def test_creates_graph_with_correct_fields(self, tmp_path):
        svc = _make_service(tmp_path)
        graph = svc.create_graph("TASK-001", "Build auth module")
        assert graph.parent_id == "TASK-001"
        assert graph.parent_title == "Build auth module"
        assert graph.nodes == {}

    def test_created_graph_is_persisted(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        # Reload from disk
        svc2 = _make_service(tmp_path)
        graph = svc2.get_graph("TASK-001")
        assert graph is not None
        assert graph.parent_id == "TASK-001"

    def test_duplicate_parent_id_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        with pytest.raises(ValueError, match="already exists"):
            svc.create_graph("TASK-001", "Different title")

    def test_multiple_graphs_coexist(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Task one")
        svc.create_graph("TASK-002", "Task two")
        assert svc.get_graph("TASK-001") is not None
        assert svc.get_graph("TASK-002") is not None


class TestTaskDecomposerAddSubtask:
    """add_subtask must create nodes with generated IDs and validate deps."""

    def test_add_subtask_returns_node(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node = svc.add_subtask(
            parent_id="TASK-001",
            title="Write unit tests",
            task_type=TaskType.test_writing,
            risk_tier=RiskTier.low,
        )
        assert node.parent_id == "TASK-001"
        assert node.title == "Write unit tests"
        assert node.status == "pending"

    def test_generated_subtask_id_starts_with_sub(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node = svc.add_subtask("TASK-001", "T", TaskType.bug_fix, RiskTier.low)
        assert node.subtask_id.startswith("sub-")

    def test_generated_ids_are_unique(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        ids = {
            svc.add_subtask("TASK-001", f"Task {i}", TaskType.bug_fix, RiskTier.low).subtask_id
            for i in range(10)
        }
        assert len(ids) == 10

    def test_add_subtask_to_missing_graph_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(KeyError, match="TASK-999"):
            svc.add_subtask("TASK-999", "Title", TaskType.bug_fix, RiskTier.low)

    def test_add_subtask_with_valid_dep(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node_a = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        node_b = svc.add_subtask(
            "TASK-001",
            "B",
            TaskType.api_endpoint,
            RiskTier.low,
            depends_on=[node_a.subtask_id],
        )
        assert node_a.subtask_id in node_b.depends_on

    def test_add_subtask_with_unknown_dep_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        with pytest.raises(ValueError, match="Unknown dependency"):
            svc.add_subtask(
                "TASK-001",
                "B",
                TaskType.bug_fix,
                RiskTier.low,
                depends_on=["nonexistent-id"],
            )

    def test_add_subtask_persisted_in_graph(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node = svc.add_subtask("TASK-001", "Node A", TaskType.bug_fix, RiskTier.low)
        graph = svc.get_graph("TASK-001")
        assert node.subtask_id in graph.nodes

    def test_add_subtask_default_depends_on_is_empty(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node = svc.add_subtask("TASK-001", "Node", TaskType.docs_update, RiskTier.low)
        assert node.depends_on == []


class TestTaskDecomposerCompleteSubtask:
    """complete_subtask must transition status and set completed_at."""

    def test_complete_subtask_sets_status(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        graph = svc.complete_subtask("TASK-001", node.subtask_id)
        assert graph.nodes[node.subtask_id].status == "completed"

    def test_complete_subtask_sets_completed_at(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        before = datetime.now(UTC)
        node = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        svc.complete_subtask("TASK-001", node.subtask_id)
        after = datetime.now(UTC)
        graph = svc.get_graph("TASK-001")
        ts = graph.nodes[node.subtask_id].completed_at
        assert ts is not None
        assert before <= ts <= after

    def test_complete_already_completed_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        svc.complete_subtask("TASK-001", node.subtask_id)
        with pytest.raises(ValueError, match="terminal"):
            svc.complete_subtask("TASK-001", node.subtask_id)

    def test_complete_missing_graph_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(KeyError, match="TASK-999"):
            svc.complete_subtask("TASK-999", "sub-001")

    def test_complete_missing_subtask_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        with pytest.raises(KeyError, match="sub-nonexistent"):
            svc.complete_subtask("TASK-001", "sub-nonexistent")

    def test_complete_subtask_unlocks_dependent(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node_a = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        svc.add_subtask(
            "TASK-001",
            "B",
            TaskType.api_endpoint,
            RiskTier.low,
            depends_on=[node_a.subtask_id],
        )
        # Before completing A, only A should be ready
        graph = svc.get_graph("TASK-001")
        assert len(graph.get_ready_nodes()) == 1

        # After completing A, B becomes ready
        svc.complete_subtask("TASK-001", node_a.subtask_id)
        graph = svc.get_graph("TASK-001")
        ready = graph.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].title == "B"

    def test_complete_subtask_persisted_to_disk(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        svc.complete_subtask("TASK-001", node.subtask_id)
        # Reload service from disk and verify status persisted
        svc2 = _make_service(tmp_path)
        graph = svc2.get_graph("TASK-001")
        assert graph.nodes[node.subtask_id].status == "completed"


class TestTaskDecomposerGetReadyNodesFlow:
    """End-to-end ready nodes tracking across partial completion."""

    def test_linear_chain_ready_progression(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Linear chain")
        a = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        b = svc.add_subtask(
            "TASK-001",
            "B",
            TaskType.api_endpoint,
            RiskTier.low,
            depends_on=[a.subtask_id],
        )
        c = svc.add_subtask(
            "TASK-001",
            "C",
            TaskType.docs_update,
            RiskTier.low,
            depends_on=[b.subtask_id],
        )

        graph = svc.get_graph("TASK-001")

        # Step 1: only A is ready
        ready = [n.subtask_id for n in graph.get_ready_nodes()]
        assert ready == [a.subtask_id]

        # Step 2: complete A, B becomes ready
        svc.complete_subtask("TASK-001", a.subtask_id)
        graph = svc.get_graph("TASK-001")
        ready = [n.subtask_id for n in graph.get_ready_nodes()]
        assert ready == [b.subtask_id]

        # Step 3: complete B, C becomes ready
        svc.complete_subtask("TASK-001", b.subtask_id)
        graph = svc.get_graph("TASK-001")
        ready = [n.subtask_id for n in graph.get_ready_nodes()]
        assert ready == [c.subtask_id]

        # Step 4: complete C, no more ready nodes
        svc.complete_subtask("TASK-001", c.subtask_id)
        graph = svc.get_graph("TASK-001")
        assert graph.get_ready_nodes() == []
        assert graph.is_complete() is True

    def test_diamond_ready_progression(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Diamond")
        a = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        b = svc.add_subtask(
            "TASK-001", "B", TaskType.bug_fix, RiskTier.low, depends_on=[a.subtask_id]
        )
        c = svc.add_subtask(
            "TASK-001", "C", TaskType.docs_update, RiskTier.low, depends_on=[a.subtask_id]
        )
        d = svc.add_subtask(
            "TASK-001",
            "D",
            TaskType.api_endpoint,
            RiskTier.low,
            depends_on=[b.subtask_id, c.subtask_id],
        )

        svc.complete_subtask("TASK-001", a.subtask_id)
        graph = svc.get_graph("TASK-001")
        ready_ids = sorted(n.subtask_id for n in graph.get_ready_nodes())
        assert ready_ids == sorted([b.subtask_id, c.subtask_id])

        svc.complete_subtask("TASK-001", b.subtask_id)
        graph = svc.get_graph("TASK-001")
        # C still needs completing before D is ready
        ready_ids = [n.subtask_id for n in graph.get_ready_nodes()]
        assert c.subtask_id in ready_ids
        assert d.subtask_id not in ready_ids

        svc.complete_subtask("TASK-001", c.subtask_id)
        graph = svc.get_graph("TASK-001")
        ready_ids = [n.subtask_id for n in graph.get_ready_nodes()]
        assert ready_ids == [d.subtask_id]


class TestTaskDecomposerListGraphs:
    """list_graphs must return all graphs ordered by created_at."""

    def test_empty_returns_empty_list(self, tmp_path):
        svc = _make_service(tmp_path)
        assert svc.list_graphs() == []

    def test_returns_all_created_graphs(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "First")
        svc.create_graph("TASK-002", "Second")
        svc.create_graph("TASK-003", "Third")
        graphs = svc.list_graphs()
        assert len(graphs) == 3

    def test_ordered_by_created_at(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "First")
        svc.create_graph("TASK-002", "Second")
        graphs = svc.list_graphs()
        assert graphs[0].parent_id == "TASK-001"
        assert graphs[1].parent_id == "TASK-002"

    def test_list_graphs_parent_ids(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-A", "Alpha")
        svc.create_graph("TASK-B", "Beta")
        ids = {g.parent_id for g in svc.list_graphs()}
        assert ids == {"TASK-A", "TASK-B"}


# ===========================================================================
# 4. Persistence — JSONL round-trip
# ===========================================================================


class TestPersistence:
    """Graphs must survive a service restart (reload from JSONL)."""

    def test_empty_graph_survives_restart(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Persisted title")
        svc2 = _make_service(tmp_path)
        graph = svc2.get_graph("TASK-001")
        assert graph is not None
        assert graph.parent_title == "Persisted title"

    def test_subtasks_survive_restart(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node = svc.add_subtask("TASK-001", "Node A", TaskType.bug_fix, RiskTier.low)
        svc2 = _make_service(tmp_path)
        graph = svc2.get_graph("TASK-001")
        assert node.subtask_id in graph.nodes

    def test_completed_status_survives_restart(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        node = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        svc.complete_subtask("TASK-001", node.subtask_id)
        svc2 = _make_service(tmp_path)
        graph = svc2.get_graph("TASK-001")
        assert graph.nodes[node.subtask_id].status == "completed"

    def test_jsonl_file_is_valid_jsonl(self, tmp_path):
        """Each line in the backing file must be valid JSON."""
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Alpha")
        svc.create_graph("TASK-002", "Beta")
        jsonl_path = tmp_path / "graphs.jsonl"
        with jsonl_path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    assert "parent_id" in data

    def test_malformed_line_in_jsonl_is_skipped(self, tmp_path):
        """A corrupted line must not crash the loader."""
        jsonl_path = tmp_path / "graphs.jsonl"
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        good_graph = TaskGraph(parent_id="TASK-001", parent_title="Good")
        with jsonl_path.open("w") as fh:
            fh.write("{not valid json}\n")
            fh.write(good_graph.model_dump_json() + "\n")
        svc = _make_service(tmp_path)
        assert svc.get_graph("TASK-001") is not None

    def test_empty_lines_in_jsonl_are_skipped(self, tmp_path):
        """Empty lines or lines with only whitespace must not crash the loader."""
        jsonl_path = tmp_path / "graphs.jsonl"
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        good_graph = TaskGraph(parent_id="TASK-001", parent_title="Good")
        with jsonl_path.open("w") as fh:
            fh.write("\n")
            fh.write("   \n")
            fh.write(good_graph.model_dump_json() + "\n")
        svc = _make_service(tmp_path)
        assert svc.get_graph("TASK-001") is not None

    def test_multiple_complete_subtasks_persist(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Title")
        a = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        b = svc.add_subtask("TASK-001", "B", TaskType.docs_update, RiskTier.low)
        svc.complete_subtask("TASK-001", a.subtask_id)
        svc.complete_subtask("TASK-001", b.subtask_id)
        svc2 = _make_service(tmp_path)
        graph = svc2.get_graph("TASK-001")
        assert graph.is_complete() is True
        assert graph.completion_pct() == 100.0


# ===========================================================================
# 5. Singleton pattern
# ===========================================================================


class TestSingletonPattern:
    """get_task_decomposer must return the same instance; reset clears it."""

    def setup_method(self):
        reset_task_decomposer()

    def teardown_method(self):
        reset_task_decomposer()

    def test_get_returns_same_instance(self, tmp_path):
        svc1 = get_task_decomposer(storage_path=tmp_path / "g.jsonl")
        svc2 = get_task_decomposer(storage_path=tmp_path / "other.jsonl")
        assert svc1 is svc2

    def test_reset_allows_new_instance(self, tmp_path):
        svc1 = get_task_decomposer(storage_path=tmp_path / "g.jsonl")
        reset_task_decomposer()
        svc2 = get_task_decomposer(storage_path=tmp_path / "g2.jsonl")
        assert svc1 is not svc2

    def test_singleton_path_respected_on_first_call(self, tmp_path):
        custom_path = tmp_path / "custom" / "graphs.jsonl"
        svc = get_task_decomposer(storage_path=custom_path)
        assert svc._path == custom_path

    def test_reset_resets_to_none(self, tmp_path):
        get_task_decomposer(storage_path=tmp_path / "g.jsonl")
        reset_task_decomposer()
        # After reset, can create with different path
        svc = get_task_decomposer(storage_path=tmp_path / "new.jsonl")
        assert svc._path == tmp_path / "new.jsonl"


# ===========================================================================
# 6. Thread safety
# ===========================================================================


class TestThreadSafety:
    """Concurrent graph creation and subtask completion must not corrupt state."""

    def test_concurrent_create_graphs(self, tmp_path):
        svc = _make_service(tmp_path)
        errors: list[Exception] = []

        def create_graph(idx: int) -> None:
            try:
                svc.create_graph(f"TASK-{idx:04d}", f"Task {idx}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=create_graph, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        graphs = svc.list_graphs()
        assert len(graphs) == 20

    def test_concurrent_add_subtasks(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.create_graph("TASK-001", "Concurrent test")
        errors: list[Exception] = []
        results: list[SubtaskNode] = []
        lock = threading.Lock()

        def add_subtask(idx: int) -> None:
            try:
                node = svc.add_subtask(
                    "TASK-001",
                    f"Subtask {idx}",
                    TaskType.test_writing,
                    RiskTier.low,
                )
                with lock:
                    results.append(node)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=add_subtask, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        graph = svc.get_graph("TASK-001")
        assert len(graph.nodes) == 10


# ===========================================================================
# 7. Public re-export via models.__init__
# ===========================================================================


class TestPublicReExport:
    """SubtaskNode and TaskGraph must be importable from the models package."""

    def test_subtask_node_importable_from_models(self):
        from forge_harness.webhook_server.models import SubtaskNode as SubtaskNodeAlias

        assert SubtaskNodeAlias is SubtaskNode

    def test_task_graph_importable_from_models(self):
        from forge_harness.webhook_server.models import TaskGraph as TaskGraphAlias

        assert TaskGraphAlias is TaskGraph

    def test_subtask_node_in_all(self):
        import forge_harness.webhook_server.models as m

        assert "SubtaskNode" in m.__all__

    def test_task_graph_in_all(self):
        import forge_harness.webhook_server.models as m

        assert "TaskGraph" in m.__all__
