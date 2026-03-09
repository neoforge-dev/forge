"""
Workflow Harness for FORGE Automation
=====================================

Orchestrates multi-step workflows with:
- Checkpoint save/resume for reliability
- Step dependencies (DAG execution)
- Retry with exponential backoff
- Timeout handling
- PostHog tracking integration

Usage:
    harness = WorkflowHarness(
        workflow_name="content-pipeline",
        checkpoint_dir=Path(".workflow_checkpoints"),
    )

    steps = [
        WorkflowStep(
            name="populate",
            handler=populate_content_library,
        ),
        WorkflowStep(
            name="generate",
            handler=generate_drafts,
            depends_on=["populate"],
        ),
        WorkflowStep(
            name="deploy",
            handler=deploy_marketing_site,
            depends_on=["generate"],
            skip_on_failure=True,
        ),
    ]

    result = await harness.execute(
        steps=steps,
        context={"domain": "thebrightharbor-com", "limit": 10},
    )

    # Resume from checkpoint
    result = await harness.execute(
        steps=steps,
        context={},
        resume_from=Path(".workflow_checkpoints/workflow_abc123.json"),
    )
"""

import asyncio
import json
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .models.workflow import StepStatus
from .posthog_tracker import HarnessTracker, NoOpTracker


@dataclass
class WorkflowStep:
    """A single step in a workflow.

    Attributes:
        name: Unique identifier for the step
        handler: Async function to execute (takes context dict, returns result dict)
        depends_on: List of step names that must complete first
        retry_count: Max retries on failure (default 3)
        timeout_seconds: Max execution time (default 600s / 10min)
        skip_on_failure: If True, continue workflow even if this step fails
        description: Human-readable description of what this step does
    """

    name: str
    handler: Callable[[dict], Awaitable[dict]]
    depends_on: list[str] = field(default_factory=list)
    retry_count: int = 3
    timeout_seconds: int = 600
    skip_on_failure: bool = False
    description: str = ""


@dataclass
class StepResult:
    """Result of executing a single step."""

    name: str
    status: StepStatus
    started_at: datetime
    completed_at: datetime | None = None
    result: dict = field(default_factory=dict)
    error: str | None = None
    retries_used: int = 0
    duration_seconds: float = 0.0


@dataclass
class WorkflowCheckpoint:
    """Checkpoint for workflow resume.

    Saved to disk after each step completes to enable resume on failure.
    """

    workflow_id: str
    workflow_name: str
    completed_steps: list[str]
    step_results: dict[str, dict]  # step_name -> StepResult as dict
    context: dict
    created_at: str  # ISO format
    updated_at: str  # ISO format

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowCheckpoint":
        """Create from dictionary."""
        return cls(**data)


@dataclass
class WorkflowResult:
    """Result of workflow execution."""

    success: bool
    workflow_id: str
    workflow_name: str
    steps_completed: list[str]
    steps_failed: list[str]
    steps_skipped: list[str]
    step_results: dict[str, StepResult]
    total_duration_seconds: float
    errors: list[str]
    checkpoint_path: Path | None = None


class WorkflowHarness:
    """Orchestrates multi-step workflows with checkpoints and tracking.

    Features:
    - DAG-based step execution (respects depends_on)
    - Checkpoint save after each step for resume on failure
    - Retry with exponential backoff
    - Timeout handling per step
    - PostHog event tracking
    """

    def __init__(
        self,
        workflow_name: str,
        checkpoint_dir: Path | None = None,
        tracker: HarnessTracker | None = None,
    ):
        """
        Initialize workflow harness.

        Args:
            workflow_name: Name of the workflow (used in checkpoints/tracking)
            checkpoint_dir: Directory to store checkpoints (default: .workflow_checkpoints)
            tracker: Optional PostHog tracker for metrics
        """
        self.workflow_name = workflow_name
        self.checkpoint_dir = checkpoint_dir or Path(".workflow_checkpoints")
        self.tracker = tracker or NoOpTracker(domain="", project="", session_id="workflow")

        # Ensure checkpoint directory exists
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _log(self, message: str) -> None:
        """Log a message."""
        print(f"[WORKFLOW:{self.workflow_name}] {message}")

    def _generate_workflow_id(self) -> str:
        """Generate a unique workflow ID."""
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        return f"{self.workflow_name}_{timestamp}_{short_uuid}"

    async def execute(
        self,
        steps: list[WorkflowStep],
        context: dict,
        resume_from: Path | None = None,
        dry_run: bool = False,
    ) -> WorkflowResult:
        """
        Execute a workflow with the given steps.

        Args:
            steps: List of workflow steps to execute
            context: Initial context dictionary passed to all steps
            resume_from: Optional checkpoint path to resume from
            dry_run: If True, log what would be done without executing

        Returns:
            WorkflowResult with execution details
        """
        start_time = datetime.now(UTC)

        # Initialize from checkpoint or fresh
        if resume_from and resume_from.exists():
            checkpoint = self._load_checkpoint(resume_from)
            workflow_id = checkpoint.workflow_id
            completed_steps = set(checkpoint.completed_steps)
            step_results = {
                name: StepResult(
                    name=name,
                    status=StepStatus.COMPLETED,
                    started_at=datetime.fromisoformat(
                        data.get("started_at", start_time.isoformat())
                    ),
                    completed_at=datetime.fromisoformat(
                        data.get("completed_at", start_time.isoformat())
                    ),
                    result=data.get("result", {}),
                    duration_seconds=data.get("duration_seconds", 0.0),
                )
                for name, data in checkpoint.step_results.items()
            }
            # Merge checkpoint context with provided context (provided takes precedence)
            context = {**checkpoint.context, **context}
            self._log(f"Resuming workflow {workflow_id} from checkpoint")
            self._log(f"Already completed: {completed_steps}")
        else:
            workflow_id = self._generate_workflow_id()
            completed_steps: set[str] = set()
            step_results: dict[str, StepResult] = {}
            self._log(f"Starting new workflow {workflow_id}")

        # Track workflow start
        self.tracker.track_event(
            "workflow_started",
            {
                "workflow_id": workflow_id,
                "workflow_name": self.workflow_name,
                "step_count": len(steps),
                "resumed": resume_from is not None,
                "dry_run": dry_run,
            },
        )

        # Build step map for dependency resolution
        step_map = {step.name: step for step in steps}

        # Validate dependencies
        for step in steps:
            for dep in step.depends_on:
                if dep not in step_map:
                    raise ValueError(f"Step '{step.name}' depends on unknown step '{dep}'")

        # Execute steps in dependency order
        failed_steps: list[str] = []
        skipped_steps: list[str] = []
        errors: list[str] = []

        # Topologically sort steps based on dependencies
        execution_order = self._resolve_execution_order(steps)

        for step in execution_order:
            # Skip if already completed (from checkpoint)
            if step.name in completed_steps:
                self._log(f"Skipping {step.name} (already completed)")
                continue

            # Check if dependencies are satisfied
            deps_satisfied = all(
                dep in completed_steps or dep in skipped_steps for dep in step.depends_on
            )

            # Check if any dependency failed (and not skip_on_failure)
            deps_failed = any(dep in failed_steps for dep in step.depends_on)

            if deps_failed and not step.skip_on_failure:
                self._log(f"Skipping {step.name} (dependency failed)")
                skipped_steps.append(step.name)
                step_results[step.name] = StepResult(
                    name=step.name,
                    status=StepStatus.SKIPPED,
                    started_at=datetime.now(UTC),
                    error="Dependency failed",
                )
                continue

            if not deps_satisfied:
                self._log(f"Skipping {step.name} (dependencies not met)")
                skipped_steps.append(step.name)
                continue

            # Execute the step
            if dry_run:
                self._log(f"[DRY RUN] Would execute: {step.name}")
                step_results[step.name] = StepResult(
                    name=step.name,
                    status=StepStatus.COMPLETED,
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                    result={"dry_run": True},
                )
                completed_steps.add(step.name)
            else:
                result = await self._execute_step(step, context, step_results)
                step_results[step.name] = result

                if result.status == StepStatus.COMPLETED:
                    completed_steps.add(step.name)
                    # Update context with step result for downstream steps
                    context[f"step_{step.name}_result"] = result.result
                elif result.status == StepStatus.FAILED:
                    failed_steps.append(step.name)
                    if result.error:
                        errors.append(f"{step.name}: {result.error}")

                # Save checkpoint after each step
                checkpoint = self._save_checkpoint(
                    workflow_id=workflow_id,
                    completed=list(completed_steps),
                    results={
                        name: {
                            "started_at": r.started_at.isoformat(),
                            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                            "result": r.result,
                            "status": r.status.value,
                            "duration_seconds": r.duration_seconds,
                        }
                        for name, r in step_results.items()
                    },
                    context=context,
                )

        # Calculate total duration
        end_time = datetime.now(UTC)
        total_duration = (end_time - start_time).total_seconds()

        # Determine success
        success = len(failed_steps) == 0

        # Track workflow completion
        self.tracker.track_event(
            "workflow_completed",
            {
                "workflow_id": workflow_id,
                "workflow_name": self.workflow_name,
                "success": success,
                "steps_completed": len(completed_steps),
                "steps_failed": len(failed_steps),
                "steps_skipped": len(skipped_steps),
                "total_duration_seconds": total_duration,
            },
        )

        self._log(f"Workflow {'completed' if success else 'failed'} in {total_duration:.1f}s")

        return WorkflowResult(
            success=success,
            workflow_id=workflow_id,
            workflow_name=self.workflow_name,
            steps_completed=list(completed_steps),
            steps_failed=failed_steps,
            steps_skipped=skipped_steps,
            step_results=step_results,
            total_duration_seconds=total_duration,
            errors=errors,
            checkpoint_path=self.checkpoint_dir / f"{workflow_id}.json",
        )

    async def _execute_step(
        self,
        step: WorkflowStep,
        context: dict,
        results: dict[str, StepResult],
    ) -> StepResult:
        """
        Execute a single step with retry and timeout.

        Args:
            step: The step to execute
            context: Current workflow context
            results: Results from previous steps

        Returns:
            StepResult with execution details
        """
        self._log(f"Executing: {step.name}")
        if step.description:
            self._log(f"  → {step.description}")

        start_time = datetime.now(UTC)
        retries_used = 0
        last_error: str | None = None

        # Track step start
        self.tracker.track_event(
            "workflow_step_started",
            {
                "workflow_name": self.workflow_name,
                "step_name": step.name,
                "retry_count": step.retry_count,
                "timeout_seconds": step.timeout_seconds,
            },
        )

        for attempt in range(step.retry_count + 1):
            try:
                # Execute with timeout
                result = await asyncio.wait_for(
                    step.handler(context),
                    timeout=step.timeout_seconds,
                )

                end_time = datetime.now(UTC)
                duration = (end_time - start_time).total_seconds()

                self._log(f"  ✓ {step.name} completed in {duration:.1f}s")

                # Track success
                self.tracker.track_event(
                    "workflow_step_completed",
                    {
                        "workflow_name": self.workflow_name,
                        "step_name": step.name,
                        "duration_seconds": duration,
                        "retries_used": retries_used,
                    },
                )

                return StepResult(
                    name=step.name,
                    status=StepStatus.COMPLETED,
                    started_at=start_time,
                    completed_at=end_time,
                    result=result or {},
                    retries_used=retries_used,
                    duration_seconds=duration,
                )

            except TimeoutError:
                last_error = f"Timeout after {step.timeout_seconds}s"
                retries_used = attempt + 1
                self._log(f"  ⚠ {step.name} timed out (attempt {attempt + 1})")

            except Exception as e:
                last_error = str(e)
                retries_used = attempt + 1
                self._log(f"  ⚠ {step.name} failed (attempt {attempt + 1}): {e}")
                # Log full traceback for debugging
                if attempt == step.retry_count:
                    self._log(f"  Traceback: {traceback.format_exc()}")

            # Exponential backoff before retry
            if attempt < step.retry_count:
                backoff = 2**attempt  # 1s, 2s, 4s, ...
                self._log(f"  Retrying in {backoff}s...")
                await asyncio.sleep(backoff)

        # All retries exhausted
        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()

        self._log(f"  ✗ {step.name} failed after {retries_used} attempts")

        # Track failure
        self.tracker.track_event(
            "workflow_step_failed",
            {
                "workflow_name": self.workflow_name,
                "step_name": step.name,
                "error": last_error,
                "retries_used": retries_used,
                "duration_seconds": duration,
            },
        )

        return StepResult(
            name=step.name,
            status=StepStatus.FAILED,
            started_at=start_time,
            completed_at=end_time,
            error=last_error,
            retries_used=retries_used,
            duration_seconds=duration,
        )

    def _resolve_execution_order(self, steps: list[WorkflowStep]) -> list[WorkflowStep]:
        """
        Topologically sort steps based on dependencies.

        Uses Kahn's algorithm for topological sorting.

        Args:
            steps: List of workflow steps

        Returns:
            Steps in execution order (dependencies first)

        Raises:
            ValueError: If circular dependency detected
        """
        step_map = {step.name: step for step in steps}

        # Build in-degree map
        in_degree = {step.name: len(step.depends_on) for step in steps}

        # Start with steps that have no dependencies
        queue = [name for name, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            # Process next step with no remaining dependencies
            current_name = queue.pop(0)
            result.append(step_map[current_name])

            # Reduce in-degree for dependent steps
            for step in steps:
                if current_name in step.depends_on:
                    in_degree[step.name] -= 1
                    if in_degree[step.name] == 0:
                        queue.append(step.name)

        # Check for circular dependencies
        if len(result) != len(steps):
            remaining = [name for name, degree in in_degree.items() if degree > 0]
            raise ValueError(f"Circular dependency detected involving: {remaining}")

        return result

    def _save_checkpoint(
        self,
        workflow_id: str,
        completed: list[str],
        results: dict[str, dict],
        context: dict,
    ) -> Path:
        """
        Save workflow checkpoint to disk.

        Args:
            workflow_id: Unique workflow ID
            completed: List of completed step names
            results: Step results as dicts
            context: Current workflow context

        Returns:
            Path to checkpoint file
        """
        now = datetime.now(UTC).isoformat()
        checkpoint = WorkflowCheckpoint(
            workflow_id=workflow_id,
            workflow_name=self.workflow_name,
            completed_steps=completed,
            step_results=results,
            context=context,
            created_at=now,
            updated_at=now,
        )

        checkpoint_path = self.checkpoint_dir / f"{workflow_id}.json"
        checkpoint_path.write_text(json.dumps(checkpoint.to_dict(), indent=2))

        return checkpoint_path

    def _load_checkpoint(self, path: Path) -> WorkflowCheckpoint:
        """
        Load workflow checkpoint from disk.

        Args:
            path: Path to checkpoint file

        Returns:
            WorkflowCheckpoint instance
        """
        data = json.loads(path.read_text())
        return WorkflowCheckpoint.from_dict(data)

    def list_checkpoints(self) -> list[tuple[Path, WorkflowCheckpoint]]:
        """
        List all available checkpoints.

        Returns:
            List of (path, checkpoint) tuples sorted by update time
        """
        checkpoints = []
        for path in self.checkpoint_dir.glob("*.json"):
            try:
                checkpoint = self._load_checkpoint(path)
                checkpoints.append((path, checkpoint))
            except Exception:
                continue

        # Sort by updated_at descending
        checkpoints.sort(key=lambda x: x[1].updated_at, reverse=True)
        return checkpoints

    def cleanup_checkpoints(self, keep_latest: int = 5) -> int:
        """
        Remove old checkpoints, keeping only the most recent.

        Args:
            keep_latest: Number of recent checkpoints to keep

        Returns:
            Number of checkpoints removed
        """
        checkpoints = self.list_checkpoints()
        removed = 0

        for path, _ in checkpoints[keep_latest:]:
            try:
                path.unlink()
                removed += 1
            except Exception:
                continue

        return removed


def create_workflow_harness(
    workflow_name: str,
    checkpoint_dir: Path | None = None,
    tracker: HarnessTracker | None = None,
) -> WorkflowHarness:
    """
    Factory function to create a WorkflowHarness.

    Args:
        workflow_name: Name of the workflow
        checkpoint_dir: Directory for checkpoints (optional)
        tracker: PostHog tracker (optional)

    Returns:
        Configured WorkflowHarness instance
    """
    return WorkflowHarness(
        workflow_name=workflow_name,
        checkpoint_dir=checkpoint_dir,
        tracker=tracker,
    )
