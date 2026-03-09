"""
Parallel Dispatch Module for Task Agents
========================================

Implements HRN-001: Task agent parallel dispatch. This module provides
functionality to dispatch multiple task agents concurrently, manage
their isolated execution, and collect results asynchronously.

Features:
- Dispatches 2-5 Task agents in parallel with separate contexts.
- Each agent operates within an isolated task definition and workspace.
- Collects results asynchronously with timeout handling.
- Ensures failed agents do not block the completion of successful ones.
- Enforces resource limits (maximum 5 concurrent agents by default).
"""

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Setup logging for the module
logger = logging.getLogger(__name__)


# =============================================================================
# Protocols & Data Models
# =============================================================================


@dataclass
class TaskDefinition:
    """Defines a single task to be dispatched to an agent."""

    task_id: str
    description: str
    context: dict[str, Any] = field(default_factory=dict)
    # Additional fields could include: agent_type, required_skills, etc.


@dataclass
class TaskResult:
    """Represents the outcome of a single task agent execution."""

    task_id: str
    success: bool
    output: str
    error: str | None = None
    workspace_path: Path | None = None  # Path to the agent's workspace
    duration_seconds: float = 0.0
    # Additional fields could include: log_files, generated_artifacts, etc.


class AgentExecutionFunc(Protocol):
    """
    Protocol for an agent execution function.

    This defines the expected signature for a callable that represents
    a task agent. It should take a TaskDefinition and a workspace Path,
    and return a TaskResult.
    """

    async def __call__(self, task_def: TaskDefinition, workspace: Path) -> TaskResult: ...


@dataclass
class DispatchResult:
    """Aggregated results from a parallel dispatch operation."""

    total_dispatched: int
    completed_tasks: list[TaskResult]
    failed_tasks: list[TaskResult]
    timed_out_tasks: list[TaskDefinition]
    # Optionally, metrics like total_duration, avg_duration


# =============================================================================
# Dispatcher Implementation
# =============================================================================


class ParallelDispatcher:
    """
    Dispatches tasks to agents concurrently, managing resources and results.
    """

    def __init__(self, max_concurrent_agents: int = 5):
        """
        Initializes the ParallelDispatcher.

        Args:
            max_concurrent_agents: The maximum number of agents that can run simultaneously.
                                   Clamped between 1 and 5 as per HRN-001.
        """
        self.max_concurrent_agents = max(1, min(max_concurrent_agents, 5))
        self.semaphore = asyncio.Semaphore(self.max_concurrent_agents)
        logger.info(
            f"ParallelDispatcher initialized with max {self.max_concurrent_agents} concurrent agents."
        )

    async def _run_single_agent_task(
        self,
        agent_func: AgentExecutionFunc,
        task_def: TaskDefinition,
        base_workspace: Path,
        timeout_seconds: int,
    ) -> TaskResult:
        """
        Runs a single agent task within its own isolated workspace.
        Handles workspace creation/cleanup and wraps the agent_func.
        """
        workspace_name = f"agent_workspace_{task_def.task_id}"
        agent_workspace = base_workspace / workspace_name

        # Create isolated workspace
        try:
            agent_workspace.mkdir(parents=True, exist_ok=False)
            logger.debug(
                f"Created isolated workspace for task {task_def.task_id}: {agent_workspace}"
            )
        except FileExistsError:
            logger.warning(
                f"Workspace {agent_workspace} already exists, reusing for task {task_def.task_id}"
            )
            # Ensure it's clean if reusing
            shutil.rmtree(agent_workspace)
            agent_workspace.mkdir(parents=True)
        except Exception as e:
            logger.error(f"Failed to create workspace for task {task_def.task_id}: {e}")
            return TaskResult(
                task_id=task_def.task_id,
                success=False,
                output="",
                error=f"Workspace creation failed: {e}",
                workspace_path=None,
                duration_seconds=0.0,
            )

        start_time = asyncio.get_event_loop().time()
        result: TaskResult

        try:
            async with self.semaphore:
                # Run the agent function with a timeout
                result = await asyncio.wait_for(
                    agent_func(task_def, agent_workspace), timeout=timeout_seconds
                )
        except TimeoutError:
            logger.warning(f"Task {task_def.task_id} timed out after {timeout_seconds} seconds.")
            result = TaskResult(
                task_id=task_def.task_id,
                success=False,
                output="",
                error=f"Task timed out after {timeout_seconds} seconds.",
                workspace_path=agent_workspace,
                duration_seconds=asyncio.get_event_loop().time() - start_time,
            )
        except Exception as e:
            logger.error(f"Agent for task {task_def.task_id} failed: {e}", exc_info=True)
            result = TaskResult(
                task_id=task_def.task_id,
                success=False,
                output="",
                error=f"Agent execution error: {e}",
                workspace_path=agent_workspace,
                duration_seconds=asyncio.get_event_loop().time() - start_time,
            )
        finally:
            # Calculate actual duration
            if result.duration_seconds == 0.0:  # Only if not already set by timeout/exception
                result.duration_seconds = asyncio.get_event_loop().time() - start_time

            # Cleanup workspace (optional, depending on debug needs)
            # For now, let's keep it for inspection upon failure/success
            logger.debug(f"Finished task {task_def.task_id}. Workspace: {agent_workspace}")
            # shutil.rmtree(agent_workspace) # Uncomment for automatic cleanup

        return result

    async def dispatch(
        self,
        tasks: list[TaskDefinition],
        agent_func: AgentExecutionFunc,
        timeout_seconds: int = 600,  # Default to 10 minutes per task
        temp_dir_base: Path | None = None,  # Base directory for temporary workspaces
    ) -> DispatchResult:
        """
        Dispatches a list of tasks to agents in parallel.

        Args:
            tasks: A list of TaskDefinition objects to execute.
            agent_func: An async callable representing the agent logic.
                        Signature: `async def agent_func(task_def: TaskDefinition, workspace: Path) -> TaskResult`.
            timeout_seconds: Maximum time in seconds to wait for each individual agent task.
            temp_dir_base: Optional base directory for creating temporary workspaces.
                           If None, a new temporary directory will be created and cleaned up.

        Returns:
            A DispatchResult object containing aggregated outcomes.
        """
        logger.info(f"Starting parallel dispatch for {len(tasks)} tasks.")

        # Determine base directory for agent workspaces
        if temp_dir_base:
            base_workspace = temp_dir_base
        else:
            base_workspace = Path(tempfile.mkdtemp(prefix="forge_dispatch_"))
        logger.debug(f"Using base workspace directory: {base_workspace}")

        futures: list[asyncio.Task[TaskResult]] = []
        for task_def in tasks:
            future = asyncio.create_task(
                self._run_single_agent_task(agent_func, task_def, base_workspace, timeout_seconds)
            )
            futures.append(future)

        completed_tasks: list[TaskResult] = []
        failed_tasks: list[TaskResult] = []
        timed_out_tasks: list[TaskDefinition] = []

        for future in asyncio.as_completed(futures):
            result: TaskResult = await future
            if result.success:
                completed_tasks.append(result)
            elif "timed out" in (result.error or ""):
                timed_out_tasks.append(next(t for t in tasks if t.task_id == result.task_id))
                failed_tasks.append(result)  # Timeouts are also failures
            else:
                failed_tasks.append(result)
            logger.debug(
                f"Task {result.task_id} finished. Success: {result.success}, Duration: {result.duration_seconds:.2f}s"
            )

        # Clean up the base temporary directory if created by the dispatcher
        # Commenting out for now to ensure workspaces persist after dispatch for inspection
        # if own_temp_dir and base_workspace.exists():
        #     try:
        #         shutil.rmtree(base_workspace)
        #         logger.debug(f"Cleaned up base workspace directory: {base_workspace}")
        #     except Exception as e:
        #         logger.warning(f"Failed to clean up base workspace {base_workspace}: {e}")

        logger.info(
            f"Parallel dispatch completed. {len(completed_tasks)} successful, "
            f"{len(failed_tasks)} failed, {len(timed_out_tasks)} timed out."
        )

        return DispatchResult(
            total_dispatched=len(tasks),
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            timed_out_tasks=timed_out_tasks,
        )


# =============================================================================
# Example Agent (for testing/demonstration)
# =============================================================================


async def example_agent(task_def: TaskDefinition, workspace: Path) -> TaskResult:
    """
    An example asynchronous agent function for demonstration and testing.
    It simulates work by writing to a file and optionally failing or timing out.
    """
    logger.debug(f"Agent for task {task_def.task_id} started in {workspace}")

    output_file = workspace / "agent_output.txt"
    error_file = workspace / "agent_error.log"

    try:
        # Simulate agent working
        await asyncio.sleep(task_def.context.get("delay", 0.1))

        if task_def.context.get("should_fail"):
            error_message = f"Simulated failure for {task_def.task_id}"
            error_file.write_text(error_message)
            logger.warning(error_message)
            return TaskResult(
                task_id=task_def.task_id,
                success=False,
                output=f"Task {task_def.task_id} failed.",
                error=error_message,
                workspace_path=workspace,
            )

        if task_def.context.get("should_timeout"):
            # Simulate an infinite loop to trigger timeout
            logger.info(f"Task {task_def.task_id} simulating timeout...")
            while True:
                await asyncio.sleep(100)  # Keep event loop busy

        # Write some output to its workspace
        output_content = f"Agent {task_def.task_id} successfully processed: {task_def.description}"
        output_file.write_text(output_content)

        # Read from workspace (simulated artifact)
        read_content = output_file.read_text()

        logger.debug(f"Agent for task {task_def.task_id} finished.")
        return TaskResult(
            task_id=task_def.task_id, success=True, output=read_content, workspace_path=workspace
        )
    except asyncio.CancelledError:
        # This will be caught by _run_single_agent_task as a TimeoutError
        logger.warning(f"Agent {task_def.task_id} was cancelled (likely due to timeout).")
        raise  # Re-raise to be handled by the caller
    except Exception as e:
        error_message = f"Agent {task_def.task_id} encountered an unexpected error: {e}"
        error_file.write_text(error_message)
        logger.error(error_message, exc_info=True)
        return TaskResult(
            task_id=task_def.task_id,
            success=False,
            output="",
            error=error_message,
            workspace_path=workspace,
        )
