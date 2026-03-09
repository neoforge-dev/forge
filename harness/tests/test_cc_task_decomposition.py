"""Command Center tests for the Task Decomposition Service (DF-2002).

Complements test_task_decomposer.py with:
- Additional SubtaskNode field validation and serialisation
- TaskGraph: wide fan-out (many parallel roots), deep linear chain
- TaskGraph: completion_pct with only failed nodes
- TaskGraph.validate_no_cycles: isolated subgraphs, wide diamond patterns
- TaskDecomposer: in_progress subtask completion path
- TaskDecomposer: JSONL file created on first write (parent dir auto-creation)
- TaskDecomposer: _load called lazily on first access (not at construction)
- TaskDecomposer: list_graphs with graphs created out of time order
- Persistence: multiple rewrites maintain JSONL consistency
- Singleton: default storage path is used when None is passed
- Thread safety: concurrent complete_subtask calls on different subtasks
- Integration: full multi-step workflow with DAG progression
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
    _DEFAULT_GRAPHS_PATH,
    TaskDecomposer,
    get_task_decomposer,
    reset_task_decomposer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc(tmp_path: Path) -> TaskDecomposer:
    return TaskDecomposer(storage_path=tmp_path / "graphs.jsonl")


def _node(
    subtask_id: str = "sub-001",
    parent_id: str = "TASK-001",
    title: str = "Do work",
    task_type: TaskType = TaskType.bug_fix,
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


# ---------------------------------------------------------------------------
# Autouse reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_task_decomposer()
    yield
    reset_task_decomposer()


# ===========================================================================
# SubtaskNode — extended field validation
# ===========================================================================


class TestSubtaskNodeExtended:
    """Edge cases for SubtaskNode field constraints and serialisation."""

    def test_all_task_types_are_valid(self):
        for task_type in TaskType:
            n = _node(task_type=task_type)
            assert n.task_type == task_type

    def test_all_risk_tiers_are_valid(self):
        for risk_tier in RiskTier:
            n = _node(risk_tier=risk_tier)
            assert n.risk_tier == risk_tier

    def test_all_valid_statuses(self):
        for status in ("pending", "in_progress", "completed", "failed"):
            n = _node(status=status)
            assert n.status == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            _node(status="unknown_status")  # type: ignore[arg-type]

    def test_depends_on_can_hold_many_entries(self):
        deps = [f"sub-{i:04d}" for i in range(50)]
        n = _node(depends_on=deps)
        assert len(n.depends_on) == 50

    def test_completed_at_can_be_set(self):
        ts = datetime.now(UTC)
        n = SubtaskNode(
            subtask_id="sub-x",
            parent_id="TASK-001",
            title="T",
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            completed_at=ts,
        )
        assert n.completed_at == ts

    def test_model_dump_includes_all_fields(self):
        n = _node()
        data = n.model_dump()
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
        assert expected_keys.issubset(data.keys())

    def test_json_serialisation_preserves_datetime(self):
        n = _node()
        restored = SubtaskNode.model_validate_json(n.model_dump_json())
        assert restored.created_at == n.created_at

    def test_subtask_id_cannot_be_empty_string(self):
        """min_length=1 is enforced — empty string must raise ValidationError."""
        with pytest.raises(ValidationError):
            SubtaskNode(
                subtask_id="",
                parent_id="TASK-001",
                title="T",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
            )


# ===========================================================================
# TaskGraph — extended structural tests
# ===========================================================================


class TestTaskGraphExtended:
    """Additional TaskGraph behaviours not covered by the unit suite."""

    # --- add_node ---

    def test_add_node_stores_reference_not_copy(self):
        """Nodes added to the graph are the exact same object."""
        graph = TaskGraph(parent_id="P", parent_title="T")
        n = _node(parent_id="P")
        graph.add_node(n)
        assert graph.nodes["sub-001"] is n

    def test_graph_with_no_nodes_has_empty_nodes_dict(self):
        graph = TaskGraph(parent_id="P", parent_title="T")
        assert graph.nodes == {}

    # --- get_ready_nodes ---

    def test_wide_fan_out_all_roots_ready(self):
        """10 nodes with no dependencies are all immediately ready."""
        graph = TaskGraph(parent_id="P", parent_title="T")
        for i in range(10):
            graph.add_node(_node(f"sub-{i:03d}", parent_id="P", title=f"T{i}"))
        ready = graph.get_ready_nodes()
        assert len(ready) == 10

    def test_deep_linear_chain_only_head_ready(self):
        """A chain A->B->C->D->E should have only A ready initially."""
        graph = TaskGraph(parent_id="P", parent_title="T")
        chain = ["A", "B", "C", "D", "E"]
        graph.add_node(_node("A", parent_id="P", title="A"))
        for prev, curr in zip(chain, chain[1:]):
            graph.add_node(_node(curr, parent_id="P", title=curr, depends_on=[prev]))
        ready = graph.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].subtask_id == "A"

    def test_failed_node_deps_still_block_successors(self):
        """A failed node's successors remain blocked (dependency is not 'completed')."""
        graph = TaskGraph(parent_id="P", parent_title="T")
        graph.add_node(_node("A", parent_id="P", title="A", status="failed"))
        graph.add_node(_node("B", parent_id="P", title="B", depends_on=["A"]))
        # B's dep A is failed, not completed → B is not ready
        ready_ids = [n.subtask_id for n in graph.get_ready_nodes()]
        assert "B" not in ready_ids

    # --- is_complete ---

    def test_all_failed_graph_is_complete(self):
        graph = TaskGraph(parent_id="P", parent_title="T")
        for i in range(3):
            graph.add_node(_node(f"sub-{i}", parent_id="P", title=f"T{i}", status="failed"))
        assert graph.is_complete() is True

    def test_single_in_progress_node_is_not_complete(self):
        graph = TaskGraph(parent_id="P", parent_title="T")
        graph.add_node(_node(status="in_progress", parent_id="P"))
        assert graph.is_complete() is False

    # --- completion_pct ---

    def test_completion_pct_all_failed(self):
        graph = TaskGraph(parent_id="P", parent_title="T")
        for i in range(4):
            graph.add_node(_node(f"sub-{i}", parent_id="P", title=f"T{i}", status="failed"))
        assert graph.completion_pct() == 100.0

    def test_completion_pct_one_of_four_complete(self):
        graph = TaskGraph(parent_id="P", parent_title="T")
        graph.add_node(_node("sub-0", parent_id="P", title="A", status="completed"))
        for i in range(1, 4):
            graph.add_node(_node(f"sub-{i}", parent_id="P", title=f"T{i}"))
        assert graph.completion_pct() == pytest.approx(25.0)

    def test_completion_pct_in_progress_not_counted(self):
        graph = TaskGraph(parent_id="P", parent_title="T")
        graph.add_node(_node("sub-0", parent_id="P", title="A", status="in_progress"))
        assert graph.completion_pct() == 0.0

    # --- validate_no_cycles ---

    def test_two_isolated_subgraphs_no_cycle(self):
        """Two completely separate dependency chains must not trigger cycle detection."""
        graph = TaskGraph(parent_id="P", parent_title="T")
        graph.add_node(_node("A1", parent_id="P", title="A1"))
        graph.add_node(_node("A2", parent_id="P", title="A2", depends_on=["A1"]))
        graph.add_node(_node("B1", parent_id="P", title="B1"))
        graph.add_node(_node("B2", parent_id="P", title="B2", depends_on=["B1"]))
        graph.validate_no_cycles()  # must not raise

    def test_wide_diamond_no_cycle(self):
        """Root fans out to 5 nodes which all converge into a final node."""
        graph = TaskGraph(parent_id="P", parent_title="T")
        graph.add_node(_node("root", parent_id="P", title="Root"))
        branches = [f"br-{i}" for i in range(5)]
        for br in branches:
            graph.add_node(_node(br, parent_id="P", title=br, depends_on=["root"]))
        graph.add_node(_node("leaf", parent_id="P", title="Leaf", depends_on=branches))
        graph.validate_no_cycles()  # must not raise

    def test_indirect_cycle_three_nodes(self):
        """A -> B -> C -> A is a cycle."""
        graph = TaskGraph(parent_id="P", parent_title="T")
        graph.nodes["A"] = _node("A", parent_id="P", title="A", depends_on=["C"])
        graph.nodes["B"] = _node("B", parent_id="P", title="B", depends_on=["A"])
        graph.nodes["C"] = _node("C", parent_id="P", title="C", depends_on=["B"])
        with pytest.raises(ValueError, match="[Cc]ycle"):
            graph.validate_no_cycles()

    def test_graph_serialisation_preserves_nodes(self):
        graph = TaskGraph(parent_id="P", parent_title="T")
        for i in range(3):
            graph.add_node(_node(f"sub-{i}", parent_id="P", title=f"T{i}"))
        restored = TaskGraph.model_validate_json(graph.model_dump_json())
        assert set(restored.nodes.keys()) == set(graph.nodes.keys())


# ===========================================================================
# TaskDecomposer — additional service behaviours
# ===========================================================================


class TestDecomposerLazyLoad:
    """Verify lazy loading mechanics of _ensure_loaded."""

    def test_loaded_flag_false_before_first_access(self, tmp_path):
        svc = _svc(tmp_path)
        assert svc._loaded is False

    def test_loaded_flag_true_after_get_graph(self, tmp_path):
        svc = _svc(tmp_path)
        svc.get_graph("TASK-NONEXISTENT")
        assert svc._loaded is True

    def test_loaded_flag_true_after_list_graphs(self, tmp_path):
        svc = _svc(tmp_path)
        svc.list_graphs()
        assert svc._loaded is True

    def test_no_file_is_not_an_error(self, tmp_path):
        path = tmp_path / "nonexistent" / "graphs.jsonl"
        svc = TaskDecomposer(storage_path=path)
        # Should load cleanly with empty store
        assert svc.list_graphs() == []

    def test_storage_path_attribute_is_set(self, tmp_path):
        path = tmp_path / "custom.jsonl"
        svc = TaskDecomposer(storage_path=path)
        assert svc._path == path


class TestDecomposerFileCreation:
    """Verify that the backing file and parent directories are created on write."""

    def test_nested_parent_dirs_created_on_first_create_graph(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "graphs.jsonl"
        svc = TaskDecomposer(storage_path=deep_path)
        svc.create_graph("T", "Title")
        assert deep_path.exists()

    def test_jsonl_file_contains_one_line_per_graph(self, tmp_path):
        svc = _svc(tmp_path)
        for i in range(3):
            svc.create_graph(f"T-{i}", f"Title {i}")
        path = tmp_path / "graphs.jsonl"
        lines = [l for l in path.read_text().splitlines() if l.strip()]
        assert len(lines) == 3

    def test_rewrite_after_subtask_add_keeps_one_line_per_graph(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T-1", "Title 1")
        svc.create_graph("T-2", "Title 2")
        svc.add_subtask("T-1", "Subtask", TaskType.bug_fix, RiskTier.low)
        path = tmp_path / "graphs.jsonl"
        lines = [l for l in path.read_text().splitlines() if l.strip()]
        # Rewrite should produce exactly 2 lines (one per graph)
        assert len(lines) == 2


class TestDecomposerCompleteInProgress:
    """complete_subtask must accept in_progress nodes (not just pending)."""

    def test_complete_in_progress_node_succeeds(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        node = svc.add_subtask("T", "Work", TaskType.bug_fix, RiskTier.low)
        # Manually advance to in_progress
        graph = svc.get_graph("T")
        graph.nodes[node.subtask_id].status = "in_progress"
        svc._persist(graph)

        result = svc.complete_subtask("T", node.subtask_id)
        assert result.nodes[node.subtask_id].status == "completed"

    def test_complete_failed_node_raises(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        node = svc.add_subtask("T", "Work", TaskType.bug_fix, RiskTier.low)
        graph = svc.get_graph("T")
        graph.nodes[node.subtask_id].status = "failed"
        svc._persist(graph)

        with pytest.raises(ValueError, match="terminal"):
            svc.complete_subtask("T", node.subtask_id)


class TestDecomposerListGraphsOrdering:
    """list_graphs must respect created_at ascending order across restarts."""

    def test_ordering_preserved_after_restart(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T-ALPHA", "Alpha")
        svc.create_graph("T-BETA", "Beta")
        svc.create_graph("T-GAMMA", "Gamma")

        svc2 = _svc(tmp_path)
        graphs = svc2.list_graphs()
        ids = [g.parent_id for g in graphs]
        assert ids == ["T-ALPHA", "T-BETA", "T-GAMMA"]

    def test_list_graphs_is_sorted_ascending(self, tmp_path):
        svc = _svc(tmp_path)
        for i in range(5):
            svc.create_graph(f"T-{i}", f"Task {i}")
        graphs = svc.list_graphs()
        timestamps = [g.created_at for g in graphs]
        assert timestamps == sorted(timestamps)

    def test_single_graph_list_returns_list_of_one(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("ONLY", "Only one")
        graphs = svc.list_graphs()
        assert len(graphs) == 1
        assert graphs[0].parent_id == "ONLY"


class TestDecomposerGetGraph:
    """get_graph must return None for missing and correct graph for existing."""

    def test_get_nonexistent_returns_none(self, tmp_path):
        svc = _svc(tmp_path)
        assert svc.get_graph("MISSING") is None

    def test_get_after_create_returns_graph(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        graph = svc.get_graph("T")
        assert graph is not None
        assert graph.parent_id == "T"

    def test_get_graph_returns_updated_after_subtask_add(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        node = svc.add_subtask("T", "Sub", TaskType.docs_update, RiskTier.low)
        graph = svc.get_graph("T")
        assert node.subtask_id in graph.nodes


class TestDecomposerAddSubtaskEdgeCases:
    """Edge cases for add_subtask not covered in the unit suite."""

    def test_multiple_deps_all_valid(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        a = svc.add_subtask("T", "A", TaskType.test_writing, RiskTier.low)
        b = svc.add_subtask("T", "B", TaskType.docs_update, RiskTier.low)
        c = svc.add_subtask(
            "T",
            "C",
            TaskType.api_endpoint,
            RiskTier.medium,
            depends_on=[a.subtask_id, b.subtask_id],
        )
        assert a.subtask_id in c.depends_on
        assert b.subtask_id in c.depends_on

    def test_add_subtask_does_not_cycle_check_cross_graph(self, tmp_path):
        """Dependencies referencing nodes in other graphs are silently ignored by cycle check."""
        svc = _svc(tmp_path)
        svc.create_graph("T-A", "Graph A")
        svc.create_graph("T-B", "Graph B")
        na = svc.add_subtask("T-A", "Node in A", TaskType.bug_fix, RiskTier.low)
        # add a node to T-B that declares a dep on a node from T-A
        # The dep won't exist in T-B's graph so it is treated as unknown
        with pytest.raises(ValueError, match="Unknown dependency"):
            svc.add_subtask(
                "T-B",
                "Node in B",
                TaskType.bug_fix,
                RiskTier.low,
                depends_on=[na.subtask_id],
            )

    def test_add_subtask_high_risk_accepted(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        node = svc.add_subtask("T", "High risk work", TaskType.security_change, RiskTier.high)
        assert node.risk_tier == RiskTier.high

    def test_add_subtask_critical_risk_accepted(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        node = svc.add_subtask(
            "T", "Critical work", TaskType.deployment, RiskTier.critical
        )
        assert node.risk_tier == RiskTier.critical

    def test_subtask_id_has_sub_prefix_and_12_hex_chars(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        node = svc.add_subtask("T", "Node", TaskType.bug_fix, RiskTier.low)
        # Format: "sub-" + 12 hex chars
        assert node.subtask_id.startswith("sub-")
        suffix = node.subtask_id[4:]
        assert len(suffix) == 12
        assert all(c in "0123456789abcdef" for c in suffix)


# ===========================================================================
# Persistence — additional JSONL consistency checks
# ===========================================================================


class TestPersistenceExtended:
    """Additional persistence scenarios beyond basic round-trip."""

    def test_jsonl_valid_after_multiple_rewrites(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        for i in range(5):
            svc.add_subtask("T", f"Subtask {i}", TaskType.bug_fix, RiskTier.low)
        path = tmp_path / "graphs.jsonl"
        lines = [l for l in path.read_text().splitlines() if l.strip()]
        for line in lines:
            data = json.loads(line)
            assert "parent_id" in data
            assert "nodes" in data

    def test_graph_with_multiple_completed_subtasks_survives_restart(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Title")
        nodes = [
            svc.add_subtask("T", f"Task {i}", TaskType.test_writing, RiskTier.low)
            for i in range(4)
        ]
        for node in nodes:
            svc.complete_subtask("T", node.subtask_id)

        svc2 = _svc(tmp_path)
        graph = svc2.get_graph("T")
        assert graph.is_complete() is True
        assert graph.completion_pct() == 100.0

    def test_malformed_json_line_does_not_corrupt_good_data(self, tmp_path):
        """A bad line in the JSONL file is skipped; valid graphs still load."""
        path = tmp_path / "graphs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        good = TaskGraph(parent_id="GOOD", parent_title="Good graph")
        with path.open("w") as fh:
            fh.write("NOT JSON AT ALL\n")
            fh.write(good.model_dump_json() + "\n")
            fh.write('{"parent_id": "INCOMPLETE"\n')  # truncated JSON

        svc = TaskDecomposer(storage_path=path)
        assert svc.get_graph("GOOD") is not None
        assert svc.get_graph("INCOMPLETE") is None

    def test_storage_survives_interleaved_creates_and_subtasks(self, tmp_path):
        svc = _svc(tmp_path)
        # Interleave graph creation and subtask addition
        for i in range(3):
            svc.create_graph(f"T-{i}", f"Task {i}")
            svc.add_subtask(f"T-{i}", "Sub A", TaskType.bug_fix, RiskTier.low)
            svc.add_subtask(f"T-{i}", "Sub B", TaskType.docs_update, RiskTier.low)

        svc2 = _svc(tmp_path)
        graphs = svc2.list_graphs()
        assert len(graphs) == 3
        for g in graphs:
            assert len(g.nodes) == 2


# ===========================================================================
# Singleton — default path and reset behaviour
# ===========================================================================


class TestSingletonExtended:
    """Additional singleton boundary conditions."""

    def test_default_storage_path_constant(self):
        """_DEFAULT_GRAPHS_PATH should match the documented default."""
        assert str(_DEFAULT_GRAPHS_PATH).endswith("graphs.jsonl")
        assert ".forge" in str(_DEFAULT_GRAPHS_PATH)

    def test_get_with_none_path_uses_default(self, tmp_path):
        svc = get_task_decomposer(storage_path=None)
        assert svc is not None
        # Default path is _DEFAULT_GRAPHS_PATH
        assert svc._path == _DEFAULT_GRAPHS_PATH

    def test_second_call_ignores_new_path(self, tmp_path):
        path_a = tmp_path / "a.jsonl"
        path_b = tmp_path / "b.jsonl"
        svc1 = get_task_decomposer(storage_path=path_a)
        svc2 = get_task_decomposer(storage_path=path_b)
        assert svc1 is svc2
        assert svc2._path == path_a

    def test_reset_clears_global_instance(self, tmp_path):
        import forge_harness.webhook_server.services.task_decomposer as mod

        get_task_decomposer(storage_path=tmp_path / "g.jsonl")
        assert mod._decomposer_instance is not None
        reset_task_decomposer()
        assert mod._decomposer_instance is None

    def test_get_after_reset_creates_fresh_instance(self, tmp_path):
        path_a = tmp_path / "a.jsonl"
        svc1 = get_task_decomposer(storage_path=path_a)
        svc1.create_graph("G1", "Graph One")
        reset_task_decomposer()

        path_b = tmp_path / "b.jsonl"
        svc2 = get_task_decomposer(storage_path=path_b)
        # Fresh instance on empty path — no graphs
        assert svc2.list_graphs() == []
        assert svc2._path == path_b


# ===========================================================================
# Thread safety — concurrent complete_subtask on different nodes
# ===========================================================================


class TestConcurrentSubtaskCompletion:
    """Concurrent completion of distinct subtasks must not corrupt graph state."""

    def test_concurrent_completions_of_distinct_subtasks(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Concurrent test")
        nodes = [
            svc.add_subtask("T", f"Node {i}", TaskType.test_writing, RiskTier.low)
            for i in range(20)
        ]

        errors: list[Exception] = []

        def complete(node_id: str) -> None:
            try:
                svc.complete_subtask("T", node_id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=complete, args=(n.subtask_id,)) for n in nodes
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors: {errors}"
        graph = svc.get_graph("T")
        completed = [n for n in graph.nodes.values() if n.status == "completed"]
        assert len(completed) == 20

    def test_concurrent_get_graph_is_safe(self, tmp_path):
        svc = _svc(tmp_path)
        svc.create_graph("T", "Read test")
        results: list[TaskGraph | None] = []
        lock = threading.Lock()

        def read():
            g = svc.get_graph("T")
            with lock:
                results.append(g)

        threads = [threading.Thread(target=read) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is not None for r in results)
        assert all(r.parent_id == "T" for r in results)  # type: ignore[union-attr]


# ===========================================================================
# Integration — realistic full workflow
# ===========================================================================


class TestFullWorkflowIntegration:
    """End-to-end integration scenarios for Command Center task dispatch."""

    def test_feature_implementation_workflow(self, tmp_path):
        """Simulate: tests first, then implementation, then docs."""
        svc = _svc(tmp_path)
        svc.create_graph("FEAT-001", "Implement OAuth2 login")

        tests = svc.add_subtask(
            "FEAT-001", "Write auth unit tests", TaskType.test_writing, RiskTier.low
        )
        impl = svc.add_subtask(
            "FEAT-001",
            "Implement login endpoint",
            TaskType.api_endpoint,
            RiskTier.medium,
            depends_on=[tests.subtask_id],
        )
        docs = svc.add_subtask(
            "FEAT-001",
            "Update API documentation",
            TaskType.docs_update,
            RiskTier.low,
            depends_on=[impl.subtask_id],
        )

        graph = svc.get_graph("FEAT-001")

        # Phase 1: only tests is ready
        ready = graph.get_ready_nodes()
        assert [n.subtask_id for n in ready] == [tests.subtask_id]
        assert graph.completion_pct() == 0.0
        assert not graph.is_complete()

        # Phase 2: complete tests → impl becomes ready
        svc.complete_subtask("FEAT-001", tests.subtask_id)
        graph = svc.get_graph("FEAT-001")
        ready = graph.get_ready_nodes()
        assert [n.subtask_id for n in ready] == [impl.subtask_id]
        assert graph.completion_pct() == pytest.approx(100.0 / 3, abs=0.01)

        # Phase 3: complete impl → docs becomes ready
        svc.complete_subtask("FEAT-001", impl.subtask_id)
        graph = svc.get_graph("FEAT-001")
        ready = graph.get_ready_nodes()
        assert [n.subtask_id for n in ready] == [docs.subtask_id]

        # Phase 4: complete docs → all done
        svc.complete_subtask("FEAT-001", docs.subtask_id)
        graph = svc.get_graph("FEAT-001")
        assert graph.is_complete() is True
        assert graph.completion_pct() == 100.0
        assert graph.get_ready_nodes() == []

    def test_parallel_bug_fixes_workflow(self, tmp_path):
        """Three independent bug fixes can be dispatched in parallel."""
        svc = _svc(tmp_path)
        svc.create_graph("BUG-BATCH", "Fix three independent bugs")

        fixes = [
            svc.add_subtask("BUG-BATCH", f"Fix bug #{i}", TaskType.bug_fix, RiskTier.low)
            for i in range(3)
        ]

        graph = svc.get_graph("BUG-BATCH")
        ready = graph.get_ready_nodes()
        assert len(ready) == 3

        # Complete two of three
        svc.complete_subtask("BUG-BATCH", fixes[0].subtask_id)
        svc.complete_subtask("BUG-BATCH", fixes[1].subtask_id)

        graph = svc.get_graph("BUG-BATCH")
        assert graph.completion_pct() == pytest.approx(200.0 / 3, abs=0.01)
        assert not graph.is_complete()

        # Complete last
        svc.complete_subtask("BUG-BATCH", fixes[2].subtask_id)
        graph = svc.get_graph("BUG-BATCH")
        assert graph.is_complete() is True

    def test_multi_graph_isolation(self, tmp_path):
        """Operations on one graph do not affect another."""
        svc = _svc(tmp_path)
        svc.create_graph("T-A", "Graph A")
        svc.create_graph("T-B", "Graph B")

        na = svc.add_subtask("T-A", "Work A", TaskType.bug_fix, RiskTier.low)
        nb = svc.add_subtask("T-B", "Work B", TaskType.bug_fix, RiskTier.low)

        svc.complete_subtask("T-A", na.subtask_id)

        graph_a = svc.get_graph("T-A")
        graph_b = svc.get_graph("T-B")

        assert graph_a.is_complete() is True
        assert graph_b.is_complete() is False
        assert graph_b.nodes[nb.subtask_id].status == "pending"

    def test_large_dag_cycle_detection_does_not_raise_for_valid_dag(self, tmp_path):
        """A large but valid DAG (20 nodes, tree-shaped) must not falsely detect a cycle."""
        svc = _svc(tmp_path)
        svc.create_graph("LARGE", "Large valid DAG")

        # Build a binary tree: node i has children 2i+1, 2i+2 (if < 20)
        nodes = [
            svc.add_subtask("LARGE", f"Node {i}", TaskType.bug_fix, RiskTier.low)
            for i in range(20)
        ]
        # Re-create with parent deps (can't retroactively add deps via the service,
        # so we validate directly on the graph model)
        graph = svc.get_graph("LARGE")
        # Manually build tree-shaped deps in graph.nodes
        for i, node in enumerate(nodes):
            parent_idx = (i - 1) // 2
            if i > 0:
                graph.nodes[node.subtask_id].depends_on = [nodes[parent_idx].subtask_id]
        # Should not raise
        graph.validate_no_cycles()
