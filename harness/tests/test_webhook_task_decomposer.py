"""Unit tests for forge_harness.webhook_server.services.task_decomposer.

Target: 70%+ coverage of task_decomposer.py (113 lines).

Covers:
- TaskDecomposer.__init__
- _ensure_loaded / _load (lazy init, missing file, valid JSONL, malformed lines)
- _rewrite / _persist (creates parent dirs, produces valid JSONL, updates cache)
- create_graph (happy path, duplicate raises ValueError)
- add_subtask (happy path, missing graph KeyError, unknown deps ValueError, cycle)
- get_graph (found, not found, triggers lazy load)
- complete_subtask (pending→completed, in_progress→completed, already terminal raises)
- list_graphs (empty, ordering by created_at)
- get_task_decomposer singleton (first call, cached, storage_path override)
- reset_task_decomposer
- Thread safety
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

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


def _make(tmp_path: Path) -> TaskDecomposer:
    return TaskDecomposer(storage_path=tmp_path / "graphs.jsonl")


def _subtask(
    svc: TaskDecomposer,
    parent_id: str,
    title: str = "Do something",
    task_type: TaskType = TaskType.test_writing,
    risk_tier: RiskTier = RiskTier.low,
    depends_on: list[str] | None = None,
) -> SubtaskNode:
    return svc.add_subtask(
        parent_id=parent_id,
        title=title,
        task_type=task_type,
        risk_tier=risk_tier,
        depends_on=depends_on,
    )


@pytest.fixture(autouse=True)
def reset_singleton():
    reset_task_decomposer()
    yield
    reset_task_decomposer()


# ===========================================================================
# __init__
# ===========================================================================


class TestInit:
    def test_default_path_when_no_arg(self):
        svc = TaskDecomposer()
        assert svc._path == _DEFAULT_GRAPHS_PATH

    def test_custom_path_stored(self, tmp_path: Path):
        custom = tmp_path / "custom.jsonl"
        svc = TaskDecomposer(storage_path=custom)
        assert svc._path == custom

    def test_not_loaded_on_init(self, tmp_path: Path):
        svc = _make(tmp_path)
        assert svc._loaded is False

    def test_graphs_empty_on_init(self, tmp_path: Path):
        svc = _make(tmp_path)
        assert svc._graphs == {}


# ===========================================================================
# _ensure_loaded / _load
# ===========================================================================


class TestLoad:
    def test_missing_file_treated_as_empty(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc._ensure_loaded()
        assert svc._loaded is True
        assert svc._graphs == {}

    def test_load_called_only_once(self, tmp_path: Path):
        from unittest.mock import MagicMock

        svc = _make(tmp_path)
        original_load = svc._load
        call_count = [0]

        def counting_load():
            call_count[0] += 1
            original_load()

        svc._load = counting_load  # type: ignore[method-assign]
        svc._ensure_loaded()
        svc._ensure_loaded()
        assert call_count[0] == 1

    def test_load_reads_valid_jsonl(self, tmp_path: Path):
        storage = tmp_path / "graphs.jsonl"
        g = TaskGraph(parent_id="T-001", parent_title="Test Graph", created_at=datetime.now(UTC))
        storage.write_text(g.model_dump_json() + "\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=storage)
        svc._ensure_loaded()
        assert "T-001" in svc._graphs
        assert svc._graphs["T-001"].parent_title == "Test Graph"

    def test_load_skips_malformed_lines(self, tmp_path: Path):
        storage = tmp_path / "graphs.jsonl"
        good = TaskGraph(parent_id="T-002", parent_title="Good", created_at=datetime.now(UTC))
        storage.write_text('{"totally": "broken"}\n' + good.model_dump_json() + "\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=storage)
        svc._ensure_loaded()
        assert "T-002" in svc._graphs
        assert len(svc._graphs) == 1

    def test_load_skips_empty_lines(self, tmp_path: Path):
        storage = tmp_path / "graphs.jsonl"
        g = TaskGraph(parent_id="T-003", parent_title="Graph", created_at=datetime.now(UTC))
        storage.write_text("\n\n" + g.model_dump_json() + "\n\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=storage)
        svc._ensure_loaded()
        assert len(svc._graphs) == 1

    def test_load_last_writer_wins_for_duplicate_parent_ids(self, tmp_path: Path):
        storage = tmp_path / "graphs.jsonl"
        g1 = TaskGraph(parent_id="DUP", parent_title="First", created_at=datetime.now(UTC))
        g2 = TaskGraph(parent_id="DUP", parent_title="Second", created_at=datetime.now(UTC))
        storage.write_text(
            g1.model_dump_json() + "\n" + g2.model_dump_json() + "\n", encoding="utf-8"
        )

        svc = TaskDecomposer(storage_path=storage)
        svc._ensure_loaded()
        assert svc._graphs["DUP"].parent_title == "Second"

    def test_loaded_flag_set_after_load(self, tmp_path: Path):
        svc = _make(tmp_path)
        assert not svc._loaded
        svc._ensure_loaded()
        assert svc._loaded


# ===========================================================================
# _rewrite / _persist
# ===========================================================================


class TestPersistence:
    def test_rewrite_creates_nested_parent_dirs(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "c" / "graphs.jsonl"
        svc = TaskDecomposer(storage_path=nested)
        svc._graphs["T-010"] = TaskGraph(
            parent_id="T-010", parent_title="Nested", created_at=datetime.now(UTC)
        )
        svc._rewrite()
        assert nested.exists()

    def test_rewrite_produces_valid_jsonl(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("T-100", "Persistence Test")
        lines = svc._path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["parent_id"] == "T-100"

    def test_persist_updates_cache_and_file(self, tmp_path: Path):
        svc = _make(tmp_path)
        graph = TaskGraph(parent_id="T-200", parent_title="Persist Me", created_at=datetime.now(UTC))
        svc._graphs = {}
        svc._loaded = True
        svc._persist(graph)
        assert "T-200" in svc._graphs
        assert "T-200" in svc._path.read_text(encoding="utf-8")

    def test_rewrite_overwrites_on_second_call(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("T-001", "First")
        svc.create_graph("T-002", "Second")
        lines = svc._path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2


# ===========================================================================
# create_graph
# ===========================================================================


class TestCreateGraph:
    def test_returns_task_graph(self, tmp_path: Path):
        svc = _make(tmp_path)
        graph = svc.create_graph("TASK-001", "Build auth")
        assert isinstance(graph, TaskGraph)
        assert graph.parent_id == "TASK-001"

    def test_empty_nodes_on_creation(self, tmp_path: Path):
        svc = _make(tmp_path)
        graph = svc.create_graph("TASK-002", "Empty graph")
        assert graph.nodes == {}

    def test_stored_in_memory_cache(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("TASK-003", "Store me")
        assert "TASK-003" in svc._graphs

    def test_persisted_to_file(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("TASK-004", "Persist me")
        assert "TASK-004" in svc._path.read_text(encoding="utf-8")

    def test_duplicate_parent_id_raises_value_error(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("TASK-DUP", "Original")
        with pytest.raises(ValueError, match="already exists"):
            svc.create_graph("TASK-DUP", "Duplicate")

    def test_created_at_is_utc(self, tmp_path: Path):
        svc = _make(tmp_path)
        graph = svc.create_graph("TASK-005", "UTC Check")
        assert graph.created_at.tzinfo is not None

    def test_multiple_graphs_created_independently(self, tmp_path: Path):
        svc = _make(tmp_path)
        for i in range(3):
            svc.create_graph(f"T-{i}", f"Graph {i}")
        assert len(svc._graphs) == 3


# ===========================================================================
# add_subtask
# ===========================================================================


class TestAddSubtask:
    def test_returns_subtask_node(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-001", "Parent")
        node = _subtask(svc, "P-001")
        assert isinstance(node, SubtaskNode)

    def test_subtask_id_has_sub_prefix(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-002", "Parent")
        node = _subtask(svc, "P-002")
        assert node.subtask_id.startswith("sub-")

    def test_subtask_stored_in_graph_nodes(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-003", "Parent")
        node = _subtask(svc, "P-003")
        graph = svc.get_graph("P-003")
        assert graph is not None
        assert node.subtask_id in graph.nodes

    def test_initial_status_is_pending(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-004", "Parent")
        node = _subtask(svc, "P-004")
        assert node.status == "pending"

    def test_task_type_and_risk_tier_stored(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-005", "Parent")
        node = svc.add_subtask(
            parent_id="P-005",
            title="API work",
            task_type=TaskType.api_endpoint,
            risk_tier=RiskTier.high,
        )
        assert node.task_type == TaskType.api_endpoint
        assert node.risk_tier == RiskTier.high

    def test_missing_graph_raises_key_error(self, tmp_path: Path):
        svc = _make(tmp_path)
        with pytest.raises(KeyError, match="No graph found"):
            _subtask(svc, "NONEXISTENT")

    def test_unknown_dependency_raises_value_error(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-006", "Parent")
        with pytest.raises(ValueError, match="Unknown dependency"):
            _subtask(svc, "P-006", depends_on=["sub-doesnotexist"])

    def test_valid_dependency_chain_accepted(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-007", "Parent")
        node_a = _subtask(svc, "P-007", title="A")
        node_b = _subtask(svc, "P-007", title="B", depends_on=[node_a.subtask_id])
        assert node_b.depends_on == [node_a.subtask_id]

    def test_cycle_detection_raises_value_error(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-CYCLE", "Cycle")
        node_a = _subtask(svc, "P-CYCLE", title="A")
        node_b = _subtask(svc, "P-CYCLE", title="B", depends_on=[node_a.subtask_id])
        # Introduce cycle manually
        svc._graphs["P-CYCLE"].nodes[node_a.subtask_id].depends_on = [node_b.subtask_id]
        with pytest.raises(ValueError, match="Cycle detected"):
            svc._graphs["P-CYCLE"].validate_no_cycles()

    def test_none_depends_on_defaults_to_empty(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-008", "Parent")
        node = svc.add_subtask(
            parent_id="P-008",
            title="No deps",
            task_type=TaskType.docs_update,
            risk_tier=RiskTier.low,
            depends_on=None,
        )
        assert node.depends_on == []

    def test_multiple_subtasks_added_to_same_graph(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-009", "Parent")
        for i in range(5):
            _subtask(svc, "P-009", title=f"Task {i}")
        graph = svc.get_graph("P-009")
        assert graph is not None
        assert len(graph.nodes) == 5

    def test_subtask_persisted_to_file(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("P-010", "Parent")
        node = _subtask(svc, "P-010")
        assert node.subtask_id in svc._path.read_text(encoding="utf-8")


# ===========================================================================
# get_graph
# ===========================================================================


class TestGetGraph:
    def test_returns_graph_when_found(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("G-001", "Found")
        graph = svc.get_graph("G-001")
        assert graph is not None
        assert graph.parent_id == "G-001"

    def test_returns_none_when_not_found(self, tmp_path: Path):
        svc = _make(tmp_path)
        assert svc.get_graph("MISSING") is None

    def test_triggers_lazy_load(self, tmp_path: Path):
        storage = tmp_path / "graphs.jsonl"
        g = TaskGraph(parent_id="G-002", parent_title="On Disk", created_at=datetime.now(UTC))
        storage.write_text(g.model_dump_json() + "\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=storage)
        assert not svc._loaded
        result = svc.get_graph("G-002")
        assert result is not None
        assert svc._loaded


# ===========================================================================
# complete_subtask
# ===========================================================================


class TestCompleteSubtask:
    def _setup(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("C-001", "Complete me")
        node = _subtask(svc, "C-001")
        return svc, node

    def test_returns_updated_graph(self, tmp_path: Path):
        svc, node = self._setup(tmp_path)
        graph = svc.complete_subtask("C-001", node.subtask_id)
        assert isinstance(graph, TaskGraph)

    def test_node_status_set_to_completed(self, tmp_path: Path):
        svc, node = self._setup(tmp_path)
        svc.complete_subtask("C-001", node.subtask_id)
        graph = svc.get_graph("C-001")
        assert graph is not None
        assert graph.nodes[node.subtask_id].status == "completed"

    def test_completed_at_timestamp_set(self, tmp_path: Path):
        svc, node = self._setup(tmp_path)
        svc.complete_subtask("C-001", node.subtask_id)
        graph = svc.get_graph("C-001")
        assert graph is not None
        completed_node = graph.nodes[node.subtask_id]
        assert completed_node.completed_at is not None
        assert completed_node.completed_at.tzinfo is not None

    def test_missing_graph_raises_key_error(self, tmp_path: Path):
        svc = _make(tmp_path)
        with pytest.raises(KeyError, match="No graph found"):
            svc.complete_subtask("NONEXISTENT", "sub-abc")

    def test_missing_subtask_raises_key_error(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("C-002", "Graph")
        with pytest.raises(KeyError, match="not found in graph"):
            svc.complete_subtask("C-002", "sub-doesnotexist")

    def test_already_completed_raises_value_error(self, tmp_path: Path):
        svc, node = self._setup(tmp_path)
        svc.complete_subtask("C-001", node.subtask_id)
        with pytest.raises(ValueError, match="terminal state"):
            svc.complete_subtask("C-001", node.subtask_id)

    def test_failed_node_raises_value_error(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("C-003", "Graph")
        node = _subtask(svc, "C-003")
        svc._graphs["C-003"].nodes[node.subtask_id].status = "failed"
        with pytest.raises(ValueError, match="terminal state"):
            svc.complete_subtask("C-003", node.subtask_id)

    def test_in_progress_can_be_completed(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("C-004", "Graph")
        node = _subtask(svc, "C-004")
        svc._graphs["C-004"].nodes[node.subtask_id].status = "in_progress"
        graph = svc.complete_subtask("C-004", node.subtask_id)
        assert graph.nodes[node.subtask_id].status == "completed"

    def test_completion_persisted_to_file(self, tmp_path: Path):
        svc, node = self._setup(tmp_path)
        svc.complete_subtask("C-001", node.subtask_id)
        svc2 = TaskDecomposer(storage_path=svc._path)
        graph = svc2.get_graph("C-001")
        assert graph is not None
        assert graph.nodes[node.subtask_id].status == "completed"

    def test_dependency_chain_unblocked_after_completion(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("C-005", "Chain")
        node_a = _subtask(svc, "C-005", title="A")
        node_b = _subtask(svc, "C-005", title="B", depends_on=[node_a.subtask_id])

        graph = svc.get_graph("C-005")
        assert graph is not None
        ready_ids = {n.subtask_id for n in graph.get_ready_nodes()}
        assert node_a.subtask_id in ready_ids
        assert node_b.subtask_id not in ready_ids

        svc.complete_subtask("C-005", node_a.subtask_id)
        graph = svc.get_graph("C-005")
        assert graph is not None
        ready_ids = {n.subtask_id for n in graph.get_ready_nodes()}
        assert node_b.subtask_id in ready_ids


# ===========================================================================
# list_graphs
# ===========================================================================


class TestListGraphs:
    def test_empty_when_no_graphs(self, tmp_path: Path):
        svc = _make(tmp_path)
        assert svc.list_graphs() == []

    def test_returns_all_graphs(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("L-001", "Alpha")
        svc.create_graph("L-002", "Beta")
        svc.create_graph("L-003", "Gamma")
        ids = {g.parent_id for g in svc.list_graphs()}
        assert ids == {"L-001", "L-002", "L-003"}

    def test_ordered_by_created_at_ascending(self, tmp_path: Path):
        svc = _make(tmp_path)
        svc.create_graph("L-010", "First")
        svc.create_graph("L-011", "Second")
        svc.create_graph("L-012", "Third")
        timestamps = [g.created_at for g in svc.list_graphs()]
        assert timestamps == sorted(timestamps)

    def test_returns_list_type(self, tmp_path: Path):
        svc = _make(tmp_path)
        assert isinstance(svc.list_graphs(), list)


# ===========================================================================
# Singleton: get_task_decomposer
# ===========================================================================


class TestGetTaskDecomposer:
    def test_returns_task_decomposer_instance(self, tmp_path: Path):
        svc = get_task_decomposer(storage_path=tmp_path / "graphs.jsonl")
        assert isinstance(svc, TaskDecomposer)

    def test_same_instance_on_repeated_calls(self, tmp_path: Path):
        svc1 = get_task_decomposer(storage_path=tmp_path / "graphs.jsonl")
        svc2 = get_task_decomposer(storage_path=tmp_path / "other.jsonl")
        assert svc1 is svc2

    def test_storage_path_applied_on_first_call(self, tmp_path: Path):
        custom = tmp_path / "custom.jsonl"
        svc = get_task_decomposer(storage_path=custom)
        assert svc._path == custom

    def test_storage_path_ignored_on_subsequent_calls(self, tmp_path: Path):
        first = tmp_path / "first.jsonl"
        second = tmp_path / "second.jsonl"
        svc1 = get_task_decomposer(storage_path=first)
        svc2 = get_task_decomposer(storage_path=second)
        assert svc2._path == first

    def test_none_storage_path_uses_default(self):
        svc = get_task_decomposer(storage_path=None)
        assert svc._path == _DEFAULT_GRAPHS_PATH


# ===========================================================================
# Singleton: reset_task_decomposer
# ===========================================================================


class TestResetTaskDecomposer:
    def test_reset_allows_new_instance(self, tmp_path: Path):
        svc1 = get_task_decomposer(storage_path=tmp_path / "a.jsonl")
        reset_task_decomposer()
        svc2 = get_task_decomposer(storage_path=tmp_path / "b.jsonl")
        assert svc1 is not svc2

    def test_reset_is_idempotent(self, tmp_path: Path):
        reset_task_decomposer()
        reset_task_decomposer()
        svc = get_task_decomposer(storage_path=tmp_path / "graphs.jsonl")
        assert isinstance(svc, TaskDecomposer)

    def test_new_singleton_uses_new_path(self, tmp_path: Path):
        get_task_decomposer(storage_path=tmp_path / "old.jsonl")
        reset_task_decomposer()
        new_path = tmp_path / "new.jsonl"
        svc = get_task_decomposer(storage_path=new_path)
        assert svc._path == new_path


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_create_graph_no_errors(self, tmp_path: Path):
        svc = _make(tmp_path)
        errors: list[Exception] = []

        def create(idx: int) -> None:
            try:
                svc.create_graph(f"THREAD-{idx}", f"Graph {idx}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(svc._graphs) == 10

    def test_singleton_creation_thread_safe(self, tmp_path: Path):
        results: list[TaskDecomposer] = []
        lock = threading.Lock()

        def fetch() -> None:
            svc = get_task_decomposer(storage_path=tmp_path / "graphs.jsonl")
            with lock:
                results.append(svc)

        threads = [threading.Thread(target=fetch) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is results[0] for r in results)


# ===========================================================================
# Round-trip persistence (end-to-end)
# ===========================================================================


class TestRoundTrip:
    def test_full_lifecycle_survives_reload(self, tmp_path: Path):
        storage = tmp_path / "graphs.jsonl"
        svc = TaskDecomposer(storage_path=storage)
        svc.create_graph("RT-001", "Round Trip")
        node_a = _subtask(svc, "RT-001", title="A")
        node_b = _subtask(svc, "RT-001", title="B", depends_on=[node_a.subtask_id])
        svc.complete_subtask("RT-001", node_a.subtask_id)

        svc2 = TaskDecomposer(storage_path=storage)
        graph = svc2.get_graph("RT-001")
        assert graph is not None
        assert graph.nodes[node_a.subtask_id].status == "completed"
        assert graph.nodes[node_b.subtask_id].status == "pending"

    def test_multiple_graphs_survive_reload(self, tmp_path: Path):
        storage = tmp_path / "graphs.jsonl"
        svc = TaskDecomposer(storage_path=storage)
        for i in range(3):
            svc.create_graph(f"MG-{i}", f"Graph {i}")

        svc2 = TaskDecomposer(storage_path=storage)
        assert len(svc2.list_graphs()) == 3
