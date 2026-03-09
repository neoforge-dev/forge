"""Core Workflow Engine - Orchestrates DAG execution."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from forge_harness.logging_config import get_logger

from .dag import DAG, TaskNode
from .state import FleetStateManager

logger = get_logger(__name__)


@dataclass
class WorkflowDefinition:
    """Definition of a workflow."""

    id: str
    name: str
    description: str = ""
    dag: DAG = field(default_factory=DAG)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_yaml(cls, path: Path) -> WorkflowDefinition:
        """Load workflow from YAML file."""
        data = yaml.safe_load(path.read_text())

        dag = DAG()
        for task_data in data.get("tasks", []):
            node = TaskNode(
                id=task_data["id"],
                name=task_data["name"],
                agent_type=task_data.get("agent_type"),
                agent_capabilities=task_data.get("capabilities", []),
                dependencies=task_data.get("depends_on", []),
                retry_count=task_data.get("retry", 3),
                retry_delay=task_data.get("retry_delay", 5),
                timeout=task_data.get("timeout", 300),
                metadata=task_data.get("metadata", {}),
            )
            dag.add_node(node)

        return cls(
            id=data.get("id", path.stem),
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            dag=dag,
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, path: Path) -> WorkflowDefinition:
        """Load workflow from JSON file."""
        data = json.loads(path.read_text())

        dag = DAG()
        for task_data in data.get("tasks", []):
            node = TaskNode(
                id=task_data["id"],
                name=task_data["name"],
                agent_type=task_data.get("agent_type"),
                agent_capabilities=task_data.get("capabilities", []),
                dependencies=task_data.get("depends_on", []),
                retry_count=task_data.get("retry", 3),
                retry_delay=task_data.get("retry_delay", 5),
                timeout=task_data.get("timeout", 300),
                metadata=task_data.get("metadata", {}),
            )
            dag.add_node(node)

        return cls(
            id=data.get("id", path.stem),
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            dag=dag,
            metadata=data.get("metadata", {}),
        )


@dataclass
class WorkflowExecution:
    """Represents a running or completed workflow execution."""

    execution_id: str
    workflow_id: str
    status: str = "pending"  # pending, running, paused, completed, failed, cancelled
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    task_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_tasks: dict[str, str] = field(default_factory=dict)  # task_id -> agent_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "task_results": self.task_results,
            "current_tasks": self.current_tasks,
        }


class WorkflowEngine:
    """Engine for executing workflows."""

    def __init__(
        self,
        state_manager: FleetStateManager | None = None,
        max_parallel: int = 5,
    ) -> None:
        self.state_manager = state_manager or FleetStateManager()
        self.max_parallel = max_parallel
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._executions: dict[str, WorkflowExecution] = {}
        self._task_handlers: dict[str, Callable[[TaskNode], asyncio.Future[Any]]] = {}
        self._running = False

    def register_workflow(self, workflow: WorkflowDefinition) -> None:
        """Register a workflow definition."""
        self._workflows[workflow.id] = workflow
        logger.info("Registered workflow: %s", workflow.id)

    def register_task_handler(
        self,
        task_type: str,
        handler: Callable[[TaskNode], asyncio.Future[Any]],
    ) -> None:
        """Register a handler for a task type."""
        self._task_handlers[task_type] = handler
        logger.info("Registered task handler for type: %s", task_type)

    async def start_execution(
        self,
        workflow_id: str,
        execution_id: str | None = None,
    ) -> WorkflowExecution:
        """Start a new workflow execution."""
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow = self._workflows[workflow_id]
        execution = WorkflowExecution(
            execution_id=execution_id or f"{workflow_id}-{datetime.now(UTC).isoformat()}",
            workflow_id=workflow_id,
            status="running",
        )
        self._executions[execution.execution_id] = execution

        logger.info("Starting workflow execution: %s", execution.execution_id)

        # Start execution in background
        asyncio.create_task(self._run_execution(execution, workflow))

        return execution

    async def _run_execution(
        self,
        execution: WorkflowExecution,
        workflow: WorkflowDefinition,
    ) -> None:
        """Run a workflow execution."""
        dag = workflow.dag

        try:
            while execution.status == "running":
                # Get tasks ready to run
                ready_tasks = dag.get_ready_tasks()

                if not ready_tasks:
                    # Check if all tasks are done
                    all_done = all(
                        node.status in ("completed", "skipped", "failed")
                        for node in dag._nodes.values()
                    )
                    if all_done:
                        break
                    # Wait a bit and check again
                    await asyncio.sleep(1)
                    continue

                # Start tasks up to max_parallel limit
                running_count = len(execution.current_tasks)
                available_slots = self.max_parallel - running_count

                for task in ready_tasks[:available_slots]:
                    if execution.status != "running":
                        break
                    asyncio.create_task(self._execute_task(execution, workflow, task))

                await asyncio.sleep(0.5)

            # Determine final status
            failed_tasks = [
                task_id for task_id, result in execution.task_results.items()
                if result.get("status") == "failed"
            ]

            if failed_tasks:
                execution.status = "failed"
            else:
                execution.status = "completed"

        except Exception as exc:
            logger.error("Workflow execution failed: %s", exc)
            execution.status = "failed"

        execution.completed_at = datetime.now(UTC)
        logger.info(
            "Workflow execution %s finished with status: %s",
            execution.execution_id,
            execution.status,
        )

    async def _execute_task(
        self,
        execution: WorkflowExecution,
        workflow: WorkflowDefinition,
        task: TaskNode,
    ) -> None:
        """Execute a single task."""
        task.status = "running"

        # Find an available agent
        available = self.state_manager.get_available_agents(task.agent_capabilities)
        if not available:
            logger.warning("No available agents for task %s", task.id)
            task.status = "pending"
            return

        agent = available[0]

        # Assign task to agent
        if not self.state_manager.assign_task(agent.agent_id, task.id, execution.execution_id):
            logger.error("Failed to assign task %s to agent %s", task.id, agent.agent_id)
            task.status = "pending"
            return

        execution.current_tasks[task.id] = agent.agent_id

        # Execute with retries
        attempt = 0
        max_retries = task.retry_count
        last_error: Exception | None = None

        while attempt <= max_retries:
            try:
                logger.info(
                    "Executing task %s (attempt %d/%d) on agent %s",
                    task.id,
                    attempt + 1,
                    max_retries + 1,
                    agent.agent_id,
                )

                # Get task handler
                handler = self._task_handlers.get(task.agent_type or "default")
                if not handler:
                    raise ValueError(f"No handler for task type: {task.agent_type}")

                # Execute with timeout
                result = await asyncio.wait_for(handler(task), timeout=task.timeout)

                # Success
                execution.task_results[task.id] = {
                    "status": "completed",
                    "agent_id": agent.agent_id,
                    "result": result,
                    "attempts": attempt + 1,
                    "completed_at": datetime.now(UTC).isoformat(),
                }
                task.status = "completed"
                break

            except TimeoutError:
                last_error = Exception("Task timed out")
                logger.warning("Task %s timed out on attempt %d", task.id, attempt + 1)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Task %s failed on attempt %d: %s",
                    task.id,
                    attempt + 1,
                    exc,
                )

            attempt += 1
            if attempt <= max_retries:
                # Exponential backoff
                delay = task.retry_delay * (2 ** (attempt - 1))
                logger.info("Retrying task %s in %d seconds...", task.id, delay)
                await asyncio.sleep(delay)

        else:
            # All retries exhausted
            logger.error("Task %s failed after %d attempts", task.id, attempt)
            execution.task_results[task.id] = {
                "status": "failed",
                "agent_id": agent.agent_id,
                "error": str(last_error) if last_error else "Unknown error",
                "attempts": attempt,
                "failed_at": datetime.now(UTC).isoformat(),
            }
            task.status = "failed"

        # Release agent
        final_status = execution.task_results[task.id]["status"]
        self.state_manager.release_agent(agent.agent_id, final_status)
        del execution.current_tasks[task.id]

    def get_execution(self, execution_id: str) -> WorkflowExecution | None:
        """Get workflow execution by ID."""
        return self._executions.get(execution_id)

    def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running workflow execution."""
        execution = self._executions.get(execution_id)
        if not execution:
            return False

        if execution.status != "running":
            return False

        execution.status = "cancelled"
        logger.info("Cancelled workflow execution: %s", execution_id)
        return True

    def get_execution_status(self, execution_id: str) -> dict[str, Any] | None:
        """Get detailed execution status."""
        execution = self._executions.get(execution_id)
        if not execution:
            return None

        workflow = self._workflows.get(execution.workflow_id)
        if not workflow:
            return None

        return {
            "execution": execution.to_dict(),
            "tasks": {
                node.id: {
                    "id": node.id,
                    "name": node.name,
                    "status": node.status,
                    "dependencies": node.dependencies,
                }
                for node in workflow.dag._nodes.values()
            },
        }
