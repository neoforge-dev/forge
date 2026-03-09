"""Tests for orchestrator task delegation behavior."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_orchestrator_module():
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    module_path = scripts_dir / "orchestrator_loop.py"
    spec = importlib.util.spec_from_file_location("orchestrator_loop", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load orchestrator_loop from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    orchestrator_loop = _load_orchestrator_module()
    OrchestratorIteration = orchestrator_loop.OrchestratorIteration
    Task = orchestrator_loop.Task
    _ORCHESTRATOR_AVAILABLE = True
except (ImportError, FileNotFoundError, AttributeError):
    orchestrator_loop = None
    OrchestratorIteration = None
    Task = None
    _ORCHESTRATOR_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _ORCHESTRATOR_AVAILABLE,
    reason="scripts/orchestrator_loop.py not found — tests need rewrite for cli_v2",
)


class TestDispatcher:
    """Test delegation logic in OrchestratorIteration."""

    def _make_task(self, task_id: str, project_path: Path, priority: int = 1) -> Task:
        return Task(
            id=task_id,
            description=f"Task {task_id}",
            agent_type="backend-engineer",
            project_path=project_path,
            priority=priority,
            metadata={"index": task_id},
        )

    def test_delegate_dry_run_does_not_write_files(self, tmp_path):
        """Dry run should return ids without creating files."""
        iteration = OrchestratorIteration(forge_root=tmp_path, dry_run=True)
        tasks = [
            self._make_task("task-1", tmp_path),
            self._make_task("task-2", tmp_path),
        ]

        dispatched = iteration.delegate(tasks)

        assert dispatched == ["task-1", "task-2"]
        assert list(iteration.task_results_dir.glob("*.json")) == []

    def test_delegate_writes_files_and_limits_parallel(self, tmp_path):
        """Delegate should write files and respect max_parallel."""
        iteration = OrchestratorIteration(forge_root=tmp_path, max_parallel=2, dry_run=False)
        tasks = [
            self._make_task("task-1", tmp_path, priority=10),
            self._make_task("task-2", tmp_path, priority=5),
            self._make_task("task-3", tmp_path, priority=1),
        ]

        dispatched = iteration.delegate(tasks)

        assert dispatched == ["task-1", "task-2"]
        task_files = sorted(iteration.task_results_dir.glob("task-*.json"))
        assert [f.stem for f in task_files] == ["task-1", "task-2"]

        for task_id in dispatched:
            payload = json.loads((iteration.task_results_dir / f"{task_id}.json").read_text())
            assert payload["id"] == task_id
            assert payload["status"] == "dispatched"
            assert payload["priority"] in (10, 5)
            assert payload["metadata"]["index"] == task_id
            assert "dispatched_at" in payload

        assert not (iteration.task_results_dir / "task-3.json").exists()

    def test_delegate_empty_list(self, tmp_path):
        """Delegate should handle empty input gracefully."""
        iteration = OrchestratorIteration(forge_root=tmp_path, dry_run=False)

        dispatched = iteration.delegate([])

        assert dispatched == []
        assert list(iteration.task_results_dir.glob("*.json")) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
