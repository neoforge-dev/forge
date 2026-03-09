"""
Pure unit tests for forge_harness.webhook_server.services.task_decomposer

Covers:
- TaskDecomposer.__init__
- TaskDecomposer._ensure_loaded / _load (lazy loading, missing file, malformed lines)
- TaskDecomposer._rewrite / _persist (file writing, directory creation)
- TaskDecomposer.create_graph (happy path, duplicate rejection)
- TaskDecomposer.add_subtask (happy path, missing graph, unknown deps, cycle detection)
- TaskDecomposer.get_graph (found, not found, lazy load)
- TaskDecomposer.complete_subtask (happy path, missing graph, missing node, terminal state)
- TaskDecomposer.list_graphs (ordering, empty store)
- get_task_decomposer singleton factory (first call, second call reuse, path override)
- reset_task_decomposer (resets to None, allows fresh construction)
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.task_graph import SubtaskNode, TaskGraph
from forge_harness.webhook_server.services import task_decomposer as td_module
from forge_harness.webhook_server.services.task_decomposer import (
    _DEFAULT_GRAPHS_PATH,
    TaskDecomposer,
    get_task_decomposer,
    reset_task_decomposer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decomposer(tmp_path: Path) -> TaskDecomposer:
    """Return a fresh TaskDecomposer backed by a temp directory."""
    return TaskDecomposer(storage_path=tmp_path / "graphs.jsonl")


def _make_node(
    parent_id: str = "TASK-001",
    subtask_id: str = "sub-aaa111",
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
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# TestTaskDecomposerInit
# ---------------------------------------------------------------------------


class TestTaskDecomposerInit:
    """Tests for TaskDecomposer.__init__."""

    def test_stores_storage_path(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        assert svc._path == tmp_path / "graphs.jsonl"

    def test_starts_unloaded(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        assert svc._loaded is False

    def test_starts_with_empty_graph_cache(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        assert svc._graphs == {}

    def test_lock_is_rlock(self, tmp_path: Path) -> None:
        import threading
        svc = _make_decomposer(tmp_path)
        # RLock can be acquired multiple times from the same thread
        svc._lock.acquire()
        svc._lock.acquire()
        svc._lock.release()
        svc._lock.release()

    def test_default_path_constant(self) -> None:
        assert _DEFAULT_GRAPHS_PATH == Path(".forge/decomposition/graphs.jsonl")


# ---------------------------------------------------------------------------
# TestEnsureLoaded
# ---------------------------------------------------------------------------


class TestEnsureLoaded:
    """Tests for lazy loading behaviour (_ensure_loaded / _load)."""

    def test_load_called_on_first_access(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc._load = MagicMock()  # type: ignore[method-assign]
        svc._ensure_loaded()
        svc._load.assert_called_once()

    def test_load_not_called_when_already_loaded(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc._loaded = True
        svc._load = MagicMock()  # type: ignore[method-assign]
        svc._ensure_loaded()
        svc._load.assert_not_called()

    def test_missing_file_treated_as_empty_store(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        # Path does not exist — no graphs.jsonl created
        svc._load()
        assert svc._loaded is True
        assert svc._graphs == {}

    def test_valid_jsonl_file_is_parsed(self, tmp_path: Path) -> None:
        path = tmp_path / "graphs.jsonl"
        graph = TaskGraph(
            parent_id="TASK-001",
            parent_title="Test Task",
            created_at=datetime.now(UTC),
        )
        path.write_text(graph.model_dump_json() + "\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=path)
        svc._load()

        assert "TASK-001" in svc._graphs
        assert svc._graphs["TASK-001"].parent_title == "Test Task"

    def test_malformed_line_is_skipped_with_warning(self, tmp_path: Path) -> None:
        path = tmp_path / "graphs.jsonl"
        path.write_text("NOT_JSON\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=path)
        with patch.object(td_module.logger, "warning") as mock_warn:
            svc._load()
        mock_warn.assert_called_once()
        assert svc._loaded is True
        assert svc._graphs == {}

    def test_empty_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "graphs.jsonl"
        graph = TaskGraph(
            parent_id="TASK-002",
            parent_title="Another Task",
            created_at=datetime.now(UTC),
        )
        # Blank line before and after real content
        path.write_text("\n" + graph.model_dump_json() + "\n\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=path)
        svc._load()
        assert "TASK-002" in svc._graphs

    def test_multiple_graphs_loaded(self, tmp_path: Path) -> None:
        path = tmp_path / "graphs.jsonl"
        lines = []
        for i in range(3):
            g = TaskGraph(
                parent_id=f"TASK-{i:03d}",
                parent_title=f"Task {i}",
                created_at=datetime.now(UTC),
            )
            lines.append(g.model_dump_json())
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=path)
        svc._load()
        assert len(svc._graphs) == 3


# ---------------------------------------------------------------------------
# TestRewriteAndPersist
# ---------------------------------------------------------------------------


class TestRewriteAndPersist:
    """Tests for _rewrite and _persist helpers."""

    def test_rewrite_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "graphs.jsonl"
        svc = TaskDecomposer(storage_path=nested)
        svc._graphs = {}
        svc._rewrite()
        assert nested.parent.exists()

    def test_rewrite_writes_one_line_per_graph(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        for i in range(3):
            g = TaskGraph(
                parent_id=f"T-{i}",
                parent_title=f"Title {i}",
                created_at=datetime.now(UTC),
            )
            svc._graphs[g.parent_id] = g
        svc._rewrite()

        lines = [
            l
            for l in svc._path.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        assert len(lines) == 3

    def test_persist_updates_cache_and_calls_rewrite(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc._rewrite = MagicMock()  # type: ignore[method-assign]
        graph = TaskGraph(
            parent_id="TASK-X",
            parent_title="X",
            created_at=datetime.now(UTC),
        )
        svc._persist(graph)
        assert svc._graphs["TASK-X"] is graph
        svc._rewrite.assert_called_once()

    def test_rewrite_overwrites_stale_data(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        g1 = TaskGraph(parent_id="A", parent_title="Alpha", created_at=datetime.now(UTC))
        svc._graphs = {"A": g1}
        svc._rewrite()

        # Replace cache with only B
        g2 = TaskGraph(parent_id="B", parent_title="Beta", created_at=datetime.now(UTC))
        svc._graphs = {"B": g2}
        svc._rewrite()

        content = svc._path.read_text(encoding="utf-8")
        assert "Alpha" not in content
        assert "Beta" in content


# ---------------------------------------------------------------------------
# TestCreateGraph
# ---------------------------------------------------------------------------


class TestCreateGraph:
    """Tests for TaskDecomposer.create_graph."""

    def test_create_graph_returns_task_graph(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        graph = svc.create_graph("TASK-001", "Build auth module")
        assert isinstance(graph, TaskGraph)

    def test_create_graph_sets_parent_id(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        graph = svc.create_graph("TASK-001", "Build auth module")
        assert graph.parent_id == "TASK-001"

    def test_create_graph_sets_parent_title(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        graph = svc.create_graph("TASK-001", "Build auth module")
        assert graph.parent_title == "Build auth module"

    def test_create_graph_persisted_to_disk(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Build auth module")
        assert svc._path.exists()

    def test_create_graph_duplicate_raises_value_error(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Build auth module")
        with pytest.raises(ValueError, match="already exists"):
            svc.create_graph("TASK-001", "Duplicate")

    def test_create_graph_different_ids_allowed(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "First")
        svc.create_graph("TASK-002", "Second")
        assert len(svc._graphs) == 2

    def test_create_graph_stores_in_memory_cache(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Build auth module")
        assert "TASK-001" in svc._graphs


# ---------------------------------------------------------------------------
# TestAddSubtask
# ---------------------------------------------------------------------------


class TestAddSubtask:
    """Tests for TaskDecomposer.add_subtask."""

    def test_add_subtask_returns_subtask_node(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        node = svc.add_subtask(
            parent_id="TASK-001",
            title="Write tests",
            task_type=TaskType.test_writing,
            risk_tier=RiskTier.low,
        )
        assert isinstance(node, SubtaskNode)

    def test_add_subtask_id_has_sub_prefix(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        node = svc.add_subtask("TASK-001", "T", TaskType.bug_fix, RiskTier.low)
        assert node.subtask_id.startswith("sub-")

    def test_add_subtask_sets_correct_fields(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        node = svc.add_subtask(
            parent_id="TASK-001",
            title="Write tests",
            task_type=TaskType.test_writing,
            risk_tier=RiskTier.medium,
        )
        assert node.parent_id == "TASK-001"
        assert node.title == "Write tests"
        assert node.task_type == TaskType.test_writing
        assert node.risk_tier == RiskTier.medium
        assert node.status == "pending"

    def test_add_subtask_missing_graph_raises_key_error(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        with pytest.raises(KeyError, match="No graph found"):
            svc.add_subtask("NONEXISTENT", "T", TaskType.bug_fix, RiskTier.low)

    def test_add_subtask_unknown_dependency_raises_value_error(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        with pytest.raises(ValueError, match="Unknown dependency"):
            svc.add_subtask(
                parent_id="TASK-001",
                title="Impl",
                task_type=TaskType.api_endpoint,
                risk_tier=RiskTier.low,
                depends_on=["sub-ghost"],
            )

    def test_add_subtask_with_valid_dependency(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        node_a = svc.add_subtask("TASK-001", "First", TaskType.test_writing, RiskTier.low)
        node_b = svc.add_subtask(
            "TASK-001",
            "Second",
            TaskType.api_endpoint,
            RiskTier.low,
            depends_on=[node_a.subtask_id],
        )
        assert node_a.subtask_id in node_b.depends_on

    def test_add_subtask_cycle_raises_value_error(self, tmp_path: Path) -> None:
        """Manually construct a cycle via graph.nodes injection and confirm detection."""
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        node_a = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)

        # Introduce a cycle: manually add node_b that depends on node_a, then
        # patch node_a.depends_on so that it depends on node_b to form the cycle.
        # We bypass service to inject cycle directly.
        graph = svc._graphs["TASK-001"]
        node_b = SubtaskNode(
            subtask_id="sub-bbb222",
            parent_id="TASK-001",
            title="B",
            task_type=TaskType.bug_fix,
            risk_tier=RiskTier.low,
            status="pending",
            depends_on=[node_a.subtask_id],
            created_at=datetime.now(UTC),
        )
        graph.nodes[node_b.subtask_id] = node_b
        # Now create cycle: node_a depends on node_b
        graph.nodes[node_a.subtask_id].depends_on = [node_b.subtask_id]
        with pytest.raises(ValueError, match="Cycle detected"):
            graph.validate_no_cycles()

    def test_add_subtask_default_depends_on_is_empty(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        node = svc.add_subtask("TASK-001", "T", TaskType.bug_fix, RiskTier.low)
        assert node.depends_on == []

    def test_add_subtask_persisted_to_graph(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        node = svc.add_subtask("TASK-001", "T", TaskType.bug_fix, RiskTier.low)
        assert node.subtask_id in svc._graphs["TASK-001"].nodes

    def test_add_subtask_generates_unique_ids(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        ids = {
            svc.add_subtask("TASK-001", f"T{i}", TaskType.bug_fix, RiskTier.low).subtask_id
            for i in range(10)
        }
        assert len(ids) == 10


# ---------------------------------------------------------------------------
# TestGetGraph
# ---------------------------------------------------------------------------


class TestGetGraph:
    """Tests for TaskDecomposer.get_graph."""

    def test_get_graph_returns_task_graph_when_found(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        result = svc.get_graph("TASK-001")
        assert isinstance(result, TaskGraph)
        assert result.parent_id == "TASK-001"

    def test_get_graph_returns_none_when_not_found(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        assert svc.get_graph("NONEXISTENT") is None

    def test_get_graph_triggers_lazy_load(self, tmp_path: Path) -> None:
        # Persist a graph directly to disk, then read via fresh instance
        path = tmp_path / "graphs.jsonl"
        graph = TaskGraph(parent_id="TASK-X", parent_title="X", created_at=datetime.now(UTC))
        path.write_text(graph.model_dump_json() + "\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=path)
        assert svc._loaded is False
        result = svc.get_graph("TASK-X")
        assert result is not None
        assert svc._loaded is True


# ---------------------------------------------------------------------------
# TestCompleteSubtask
# ---------------------------------------------------------------------------


class TestCompleteSubtask:
    """Tests for TaskDecomposer.complete_subtask."""

    def _setup(self, tmp_path: Path):
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        node = svc.add_subtask("TASK-001", "Write tests", TaskType.test_writing, RiskTier.low)
        return svc, node

    def test_complete_subtask_returns_task_graph(self, tmp_path: Path) -> None:
        svc, node = self._setup(tmp_path)
        result = svc.complete_subtask("TASK-001", node.subtask_id)
        assert isinstance(result, TaskGraph)

    def test_complete_subtask_sets_status_completed(self, tmp_path: Path) -> None:
        svc, node = self._setup(tmp_path)
        svc.complete_subtask("TASK-001", node.subtask_id)
        assert svc._graphs["TASK-001"].nodes[node.subtask_id].status == "completed"

    def test_complete_subtask_sets_completed_at(self, tmp_path: Path) -> None:
        svc, node = self._setup(tmp_path)
        svc.complete_subtask("TASK-001", node.subtask_id)
        completed_at = svc._graphs["TASK-001"].nodes[node.subtask_id].completed_at
        assert completed_at is not None
        assert isinstance(completed_at, datetime)

    def test_complete_subtask_missing_graph_raises_key_error(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        with pytest.raises(KeyError, match="No graph found"):
            svc.complete_subtask("NONEXISTENT", "sub-xxx")

    def test_complete_subtask_missing_node_raises_key_error(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        with pytest.raises(KeyError, match="not found in graph"):
            svc.complete_subtask("TASK-001", "sub-ghost")

    def test_complete_subtask_already_completed_raises_value_error(self, tmp_path: Path) -> None:
        svc, node = self._setup(tmp_path)
        svc.complete_subtask("TASK-001", node.subtask_id)
        with pytest.raises(ValueError, match="terminal state"):
            svc.complete_subtask("TASK-001", node.subtask_id)

    def test_complete_subtask_failed_node_raises_value_error(self, tmp_path: Path) -> None:
        svc, node = self._setup(tmp_path)
        # Manually set node to failed
        svc._graphs["TASK-001"].nodes[node.subtask_id].status = "failed"
        with pytest.raises(ValueError, match="terminal state"):
            svc.complete_subtask("TASK-001", node.subtask_id)

    def test_complete_subtask_in_progress_allowed(self, tmp_path: Path) -> None:
        svc, node = self._setup(tmp_path)
        svc._graphs["TASK-001"].nodes[node.subtask_id].status = "in_progress"
        result = svc.complete_subtask("TASK-001", node.subtask_id)
        assert result.nodes[node.subtask_id].status == "completed"

    def test_complete_subtask_unlocks_dependent_ready_nodes(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "Auth")
        node_a = svc.add_subtask("TASK-001", "A", TaskType.test_writing, RiskTier.low)
        node_b = svc.add_subtask(
            "TASK-001", "B", TaskType.api_endpoint, RiskTier.low,
            depends_on=[node_a.subtask_id],
        )
        svc.complete_subtask("TASK-001", node_a.subtask_id)
        ready = svc.get_graph("TASK-001").get_ready_nodes()
        assert any(n.subtask_id == node_b.subtask_id for n in ready)


# ---------------------------------------------------------------------------
# TestListGraphs
# ---------------------------------------------------------------------------


class TestListGraphs:
    """Tests for TaskDecomposer.list_graphs."""

    def test_list_graphs_empty_store(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        assert svc.list_graphs() == []

    def test_list_graphs_returns_all(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "First")
        svc.create_graph("TASK-002", "Second")
        svc.create_graph("TASK-003", "Third")
        graphs = svc.list_graphs()
        assert len(graphs) == 3

    def test_list_graphs_ordered_by_created_at_ascending(self, tmp_path: Path) -> None:
        path = tmp_path / "graphs.jsonl"
        lines = []
        # Write graphs in reverse chronological order on disk
        timestamps = [
            datetime(2026, 1, 3, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        ]
        for i, ts in enumerate(timestamps):
            g = TaskGraph(parent_id=f"T-{i}", parent_title=f"Task {i}", created_at=ts)
            lines.append(g.model_dump_json())
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        svc = TaskDecomposer(storage_path=path)
        graphs = svc.list_graphs()
        dates = [g.created_at for g in graphs]
        assert dates == sorted(dates)

    def test_list_graphs_returns_task_graph_instances(self, tmp_path: Path) -> None:
        svc = _make_decomposer(tmp_path)
        svc.create_graph("TASK-001", "First")
        graphs = svc.list_graphs()
        assert all(isinstance(g, TaskGraph) for g in graphs)


# ---------------------------------------------------------------------------
# TestSingletonFactory
# ---------------------------------------------------------------------------


class TestSingletonFactory:
    """Tests for get_task_decomposer and reset_task_decomposer."""

    def setup_method(self) -> None:
        reset_task_decomposer()

    def teardown_method(self) -> None:
        reset_task_decomposer()

    def test_get_task_decomposer_returns_task_decomposer(self, tmp_path: Path) -> None:
        svc = get_task_decomposer(storage_path=tmp_path / "graphs.jsonl")
        assert isinstance(svc, TaskDecomposer)

    def test_get_task_decomposer_second_call_returns_same_instance(self, tmp_path: Path) -> None:
        path = tmp_path / "graphs.jsonl"
        svc1 = get_task_decomposer(storage_path=path)
        svc2 = get_task_decomposer(storage_path=path)
        assert svc1 is svc2

    def test_get_task_decomposer_second_call_ignores_new_path(self, tmp_path: Path) -> None:
        path_a = tmp_path / "a.jsonl"
        path_b = tmp_path / "b.jsonl"
        svc1 = get_task_decomposer(storage_path=path_a)
        svc2 = get_task_decomposer(storage_path=path_b)
        # Same instance — backed by path_a
        assert svc1 is svc2
        assert svc1._path == path_a

    def test_reset_task_decomposer_clears_singleton(self, tmp_path: Path) -> None:
        path = tmp_path / "graphs.jsonl"
        svc1 = get_task_decomposer(storage_path=path)
        reset_task_decomposer()
        svc2 = get_task_decomposer(storage_path=path)
        assert svc1 is not svc2

    def test_get_task_decomposer_uses_default_path_when_none(self) -> None:
        svc = get_task_decomposer()
        assert svc._path == _DEFAULT_GRAPHS_PATH

    def test_reset_sets_module_global_to_none(self) -> None:
        get_task_decomposer()
        reset_task_decomposer()
        assert td_module._decomposer_instance is None

    def test_get_task_decomposer_thread_safe(self, tmp_path: Path) -> None:
        """Multiple threads calling get_task_decomposer should all get same instance."""
        path = tmp_path / "graphs.jsonl"
        results: list[TaskDecomposer] = []
        errors: list[Exception] = []

        def _call() -> None:
            try:
                results.append(get_task_decomposer(storage_path=path))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        first = results[0]
        assert all(r is first for r in results)
