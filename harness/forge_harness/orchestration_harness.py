"""
Orchestration Harness for FORGE Cross-Harness Workflows
========================================================

Coordinates multi-harness workflows with declarative pipelines,
context passing, human gates, and checkpoint/resume support.

This is the **highest-level orchestration layer** that coordinates
between content_harness, notion_storage, workflow_harness, deployment,
and other harnesses.

Usage:
    from forge_harness.orchestration_harness import (
        OrchestrationHarness,
        Pipeline,
        PipelineStep,
        create_orchestration_harness,
    )

    # Create orchestrator with harness instances
    orchestrator = create_orchestration_harness(
        checkpoint_dir=Path(".forge/orchestration_checkpoints"),
    )

    # Define pipeline
    pipeline = Pipeline(
        name="content_to_publish",
        steps=[
            PipelineStep(
                name="generate",
                harness="content",
                method="generate_content_library",
                inputs={"domain": "{{ context.domain }}"},
                outputs=["items", "briefs"],
            ),
            PipelineStep(
                name="save_to_notion",
                harness="notion",
                method="save_batch",
                inputs={"items": "{{ steps.generate.items }}"},
                outputs=["page_ids"],
            ),
        ],
        context={"domain": "codeswiftr-com"},
    )

    # Execute pipeline
    result = await orchestrator.execute(pipeline)

    # Resume from checkpoint
    result = await orchestrator.resume(checkpoint_path)
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .logging_config import get_logger
from .models.workflow import StepStatus
from .pipeline_callbacks import (
    CallbackManager,
    EventType,
    PipelineCallback,
    PipelineEvent,
)

logger = get_logger(__name__)


@dataclass
class PipelineStep:
    """A single step in an orchestration pipeline.

    Attributes:
        name: Unique identifier for the step
        harness: Name of the harness to use (e.g., "content", "notion", "deployment")
        method: Method name to call on the harness
        inputs: Dict of input parameters (supports {{ context.key }} templates)
        outputs: List of output keys to capture in context
        on_failure: Action on failure - "abort", "retry", "skip", "human_gate"
        retry_count: Max retries (only used if on_failure="retry")
        timeout_seconds: Max execution time for this step
        description: Human-readable description
    """

    name: str
    harness: str
    method: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)
    on_failure: str = "abort"  # "abort", "retry", "skip", "human_gate"
    retry_count: int = 3
    timeout_seconds: int = 600
    description: str = ""


@dataclass
class StepResult:
    """Result of executing a single step."""

    name: str
    status: StepStatus
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_seconds: float = 0.0
    retries: int = 0


@dataclass
class HumanGateConfig:
    """Configuration for human gate checkpoints.

    Attributes:
        timeout_hours: How long to wait for human response
        notification_channels: Where to send notifications (slack, email, etc.)
        on_timeout: Action if no response - "skip", "abort", "escalate"
        message: Custom message to display to human
    """

    timeout_hours: float = 24.0
    notification_channels: list[str] = field(default_factory=lambda: ["slack"])
    on_timeout: str = "skip"
    message: str = ""


@dataclass
class Pipeline:
    """Declarative pipeline definition.

    Attributes:
        name: Unique pipeline name
        steps: Ordered list of pipeline steps
        context: Initial context values
        human_gates: Named human gate configurations
        description: Human-readable description
    """

    name: str
    steps: list[PipelineStep]
    context: dict[str, Any] = field(default_factory=dict)
    human_gates: dict[str, HumanGateConfig] = field(default_factory=dict)
    description: str = ""


@dataclass
class PipelineResult:
    """Result of pipeline execution."""

    pipeline_name: str
    success: bool
    step_results: list[StepResult] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    checkpoint_path: str | None = None


@dataclass
class Checkpoint:
    """Serializable checkpoint for pipeline state."""

    pipeline_name: str
    pipeline_steps: list[dict]
    context: dict[str, Any]
    step_outputs: dict[str, dict]
    completed_steps: list[str]
    current_step_index: int
    created_at: str
    human_gates: dict[str, dict] = field(default_factory=dict)


class OrchestrationHarness:
    """
    Orchestrates cross-harness workflows with declarative pipelines.

    Features:
    - Template-based context passing between steps
    - Checkpoint/resume for reliability
    - Human gate integration
    - Multiple failure modes (abort, retry, skip, human_gate)
    - Built-in pipeline templates
    """

    # Built-in pipeline templates
    BUILTIN_PIPELINES: dict[str, Pipeline] = {}

    def __init__(
        self,
        harnesses: dict[str, Any],
        checkpoint_dir: Path,
        enable_checkpoints: bool = True,
    ):
        """
        Initialize OrchestrationHarness.

        Args:
            harnesses: Dict mapping harness names to harness instances
            checkpoint_dir: Directory for checkpoint files
            enable_checkpoints: Whether to save checkpoints after each step
        """
        self.harnesses = harnesses
        self.checkpoint_dir = Path(checkpoint_dir)
        self.enable_checkpoints = enable_checkpoints
        self._callback_manager = CallbackManager()

        # Ensure checkpoint directory exists
        if self.enable_checkpoints:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Initialize built-in pipelines
        self._init_builtin_pipelines()

    def register_callback(self, callback: PipelineCallback) -> None:
        """Register a callback to receive pipeline events.

        Args:
            callback: Callback implementing PipelineCallback protocol
        """
        self._callback_manager.register(callback)

    def unregister_callback(self, callback: PipelineCallback) -> None:
        """Unregister a callback.

        Args:
            callback: Callback to remove
        """
        self._callback_manager.unregister(callback)

    async def _emit_event(
        self,
        event_type: str,
        pipeline_name: str,
        step_name: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Emit a pipeline event to all callbacks.

        Args:
            event_type: Type of event
            pipeline_name: Name of the pipeline
            step_name: Name of the step (optional)
            data: Additional event data
        """
        event = PipelineEvent(
            event_type=event_type,
            pipeline_name=pipeline_name,
            step_name=step_name,
            data=data or {},
        )
        await self._callback_manager.emit(event)

    def _init_builtin_pipelines(self) -> None:
        """Initialize built-in pipeline templates."""
        # Content-to-publish pipeline
        self.BUILTIN_PIPELINES["content_to_publish"] = Pipeline(
            name="content_to_publish",
            description="Generate content, save to Notion, await approval, publish",
            steps=[
                PipelineStep(
                    name="generate",
                    harness="content",
                    method="generate_content_library",
                    inputs={
                        "domain": "{{ context.domain }}",
                        "project": "{{ context.project }}",
                    },
                    outputs=["items", "briefs"],
                    description="Generate content from living-docs context",
                ),
                PipelineStep(
                    name="save_to_notion",
                    harness="notion",
                    method="save_batch",
                    inputs={"items": "{{ steps.generate.items }}"},
                    outputs=["page_ids"],
                    description="Save generated content to Notion",
                ),
                PipelineStep(
                    name="await_approval",
                    harness="human_gate",
                    method="await_feedback",
                    inputs={"page_ids": "{{ steps.save_to_notion.page_ids }}"},
                    outputs=["approved_ids"],
                    on_failure="human_gate",
                    timeout_seconds=86400,  # 24 hours
                    description="Wait for human approval in Notion",
                ),
                PipelineStep(
                    name="track_analytics",
                    harness="posthog",
                    method="track_batch",
                    inputs={
                        "events": ["content_published"],
                        "count": "{{ context.published_count }}",
                    },
                    outputs=[],
                    on_failure="skip",
                    description="Track publishing analytics",
                ),
            ],
            context={},
        )

        # MVP launch pipeline
        self.BUILTIN_PIPELINES["mvp_launch"] = Pipeline(
            name="mvp_launch",
            description="Full MVP launch sequence with quality gates",
            steps=[
                PipelineStep(
                    name="preflight",
                    harness="preflight",
                    method="run_checks",
                    inputs={"domain": "{{ context.domain }}"},
                    outputs=["passed", "warnings"],
                    description="Run pre-flight checks",
                ),
                PipelineStep(
                    name="quality_gates",
                    harness="deployment",
                    method="check_quality_gates",
                    inputs={"project_dir": "{{ context.project_dir }}"},
                    outputs=["coverage", "tests_passed"],
                    description="Verify quality gates",
                ),
                PipelineStep(
                    name="deploy",
                    harness="deployment",
                    method="full_deploy",
                    inputs={
                        "project_dir": "{{ context.project_dir }}",
                        "dry_run": "{{ context.dry_run }}",
                    },
                    outputs=["backend_url", "frontend_url"],
                    description="Deploy to production",
                ),
                PipelineStep(
                    name="smoke_tests",
                    harness="deployment",
                    method="run_smoke_tests",
                    inputs={
                        "backend_url": "{{ steps.deploy.backend_url }}",
                        "frontend_url": "{{ steps.deploy.frontend_url }}",
                    },
                    outputs=["all_passed"],
                    description="Run smoke tests",
                ),
                PipelineStep(
                    name="create_dashboard",
                    harness="analytics",
                    method="create_mvp_dashboard",
                    inputs={
                        "domain": "{{ context.domain }}",
                        "project": "{{ context.project }}",
                    },
                    outputs=["dashboard_id", "dashboard_url"],
                    on_failure="skip",
                    description="Create analytics dashboard",
                ),
            ],
            context={},
        )

        # Growth experiment pipeline
        self.BUILTIN_PIPELINES["growth_experiment"] = Pipeline(
            name="growth_experiment",
            description="Full A/B test lifecycle from setup to analysis",
            steps=[
                PipelineStep(
                    name="setup_experiment",
                    harness="growth",
                    method="create_experiment",
                    inputs={
                        "name": "{{ context.experiment_name }}",
                        "variants": "{{ context.variants }}",
                        "feature_flag": "{{ context.feature_flag }}",
                    },
                    outputs=["experiment_id"],
                    description="Create A/B experiment in PostHog",
                ),
                PipelineStep(
                    name="enable_feature_flag",
                    harness="growth",
                    method="update_feature_flag",
                    inputs={
                        "flag_key": "{{ context.feature_flag }}",
                        "rollout_percentage": "{{ context.rollout_percentage }}",
                    },
                    outputs=["flag_id"],
                    description="Enable feature flag for experiment",
                ),
                PipelineStep(
                    name="await_data",
                    harness="growth",
                    method="await_min_sample_size",
                    inputs={
                        "experiment_id": "{{ steps.setup_experiment.experiment_id }}",
                        "min_samples": "{{ context.min_samples }}",
                    },
                    outputs=["current_samples"],
                    on_failure="skip",
                    timeout_seconds=604800,  # 7 days
                    description="Wait for minimum sample size",
                ),
                PipelineStep(
                    name="check_significance",
                    harness="growth",
                    method="check_results",
                    inputs={
                        "experiment_id": "{{ steps.setup_experiment.experiment_id }}",
                    },
                    outputs=["winner", "confidence", "is_significant"],
                    description="Check statistical significance",
                ),
                PipelineStep(
                    name="conclude_experiment",
                    harness="growth",
                    method="conclude_experiment",
                    inputs={
                        "experiment_id": "{{ steps.setup_experiment.experiment_id }}",
                        "winner": "{{ steps.check_significance.winner }}",
                    },
                    outputs=["concluded"],
                    description="Conclude experiment with winner",
                ),
            ],
            context={},
        )

        # Weekly content calendar pipeline
        self.BUILTIN_PIPELINES["weekly_content_calendar"] = Pipeline(
            name="weekly_content_calendar",
            description="Generate and schedule a week's worth of content",
            steps=[
                PipelineStep(
                    name="audit_existing",
                    harness="repurpose",
                    method="audit_content",
                    inputs={
                        "statuses": ["Published ✨", "Ready"],
                        "min_score": 50,
                    },
                    outputs=["audit_results", "top_candidates"],
                    on_failure="skip",
                    description="Audit existing content for repurposing",
                ),
                PipelineStep(
                    name="generate_calendar",
                    harness="content",
                    method="generate_content_calendar",
                    inputs={
                        "domain": "{{ context.domain }}",
                        "project": "{{ context.project }}",
                        "weeks": 1,
                    },
                    outputs=["calendar_entries", "content_items"],
                    description="Generate content calendar for next week",
                ),
                PipelineStep(
                    name="create_notion_pages",
                    harness="notion",
                    method="create_calendar_batch",
                    inputs={
                        "entries": "{{ steps.generate_calendar.calendar_entries }}",
                    },
                    outputs=["page_ids"],
                    description="Create Notion pages for scheduled content",
                ),
                PipelineStep(
                    name="notify_stakeholders",
                    harness="notification",
                    method="notify",
                    inputs={
                        "channels": ["slack"],
                        "message": "Weekly content calendar ready for review",
                        "page_ids": "{{ steps.create_notion_pages.page_ids }}",
                    },
                    outputs=["notification_sent"],
                    on_failure="skip",
                    description="Notify stakeholders about new content",
                ),
            ],
            context={},
        )

    def get_builtin_pipeline(self, name: str) -> Pipeline | None:
        """Get a built-in pipeline by name."""
        return self.BUILTIN_PIPELINES.get(name)

    def list_builtin_pipelines(self) -> list[dict[str, str]]:
        """List all available built-in pipelines.

        Returns:
            List of dicts with 'name' and 'description' for each pipeline
        """
        return [
            {"name": name, "description": pipeline.description}
            for name, pipeline in self.BUILTIN_PIPELINES.items()
        ]

    def load_pipeline_from_yaml(
        self,
        path: Path | str,
        context_overrides: dict[str, Any] | None = None,
    ) -> Pipeline:
        """
        Load a pipeline definition from a YAML file.

        Args:
            path: Path to YAML file
            context_overrides: Values to override/merge into pipeline context

        Returns:
            Pipeline instance ready for execution

        Raises:
            FileNotFoundError: If YAML file doesn't exist
            ValueError: If YAML is invalid or missing required fields
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Pipeline YAML not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        return self._parse_pipeline_yaml(data, context_overrides)

    def load_pipeline_from_string(
        self,
        yaml_content: str,
        context_overrides: dict[str, Any] | None = None,
    ) -> Pipeline:
        """
        Load a pipeline definition from a YAML string.

        Args:
            yaml_content: YAML content as string
            context_overrides: Values to override/merge into pipeline context

        Returns:
            Pipeline instance ready for execution

        Raises:
            ValueError: If YAML is invalid or missing required fields
        """
        data = yaml.safe_load(yaml_content)
        return self._parse_pipeline_yaml(data, context_overrides)

    def _parse_pipeline_yaml(
        self,
        data: dict[str, Any],
        context_overrides: dict[str, Any] | None = None,
    ) -> Pipeline:
        """
        Parse pipeline YAML data into Pipeline instance.

        Args:
            data: Parsed YAML data
            context_overrides: Values to override/merge into pipeline context

        Returns:
            Pipeline instance

        Raises:
            ValueError: If data is invalid or missing required fields
        """
        errors = self._validate_pipeline_yaml(data)
        if errors:
            raise ValueError(f"Invalid pipeline YAML: {'; '.join(errors)}")

        # Parse steps
        steps = []
        for step_data in data.get("steps", []):
            step = PipelineStep(
                name=step_data["name"],
                harness=step_data["harness"],
                method=step_data["method"],
                inputs=step_data.get("inputs", {}),
                outputs=step_data.get("outputs", []),
                on_failure=step_data.get("on_failure", "abort"),
                retry_count=step_data.get("retry_count", 3),
                timeout_seconds=step_data.get("timeout_seconds", 600),
                description=step_data.get("description", ""),
            )
            steps.append(step)

        # Parse human gates
        human_gates = {}
        for gate_name, gate_data in data.get("human_gates", {}).items():
            human_gates[gate_name] = HumanGateConfig(
                timeout_hours=gate_data.get("timeout_hours", 24.0),
                notification_channels=gate_data.get("notification_channels", ["slack"]),
                on_timeout=gate_data.get("on_timeout", "skip"),
                message=gate_data.get("message", ""),
            )

        # Build context with overrides
        context = dict(data.get("context", {}))
        if context_overrides:
            context.update(context_overrides)

        return Pipeline(
            name=data["name"],
            steps=steps,
            context=context,
            human_gates=human_gates,
            description=data.get("description", ""),
        )

    def _validate_pipeline_yaml(self, data: dict[str, Any]) -> list[str]:
        """
        Validate pipeline YAML data.

        Args:
            data: Parsed YAML data

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Check required root fields
        if not isinstance(data, dict):
            return ["Pipeline must be a YAML mapping"]

        if "name" not in data:
            errors.append("Missing required field: name")
        elif not isinstance(data["name"], str):
            errors.append("Field 'name' must be a string")
        elif not re.match(r"^[a-z][a-z0-9_]*$", data["name"]):
            errors.append("Field 'name' must match pattern ^[a-z][a-z0-9_]*$")

        if "steps" not in data:
            errors.append("Missing required field: steps")
        elif not isinstance(data["steps"], list):
            errors.append("Field 'steps' must be a list")
        elif len(data["steps"]) == 0:
            errors.append("Pipeline must have at least one step")
        else:
            # Validate each step
            step_names = set()
            for i, step in enumerate(data["steps"]):
                step_errors = self._validate_step_yaml(step, i)
                errors.extend(step_errors)

                # Check for duplicate step names
                if "name" in step and isinstance(step["name"], str):
                    if step["name"] in step_names:
                        errors.append(f"Duplicate step name: {step['name']}")
                    step_names.add(step["name"])

        # Validate human gates if present
        if "human_gates" in data:
            if not isinstance(data["human_gates"], dict):
                errors.append("Field 'human_gates' must be a mapping")
            else:
                for gate_name, gate_data in data["human_gates"].items():
                    gate_errors = self._validate_human_gate_yaml(gate_name, gate_data)
                    errors.extend(gate_errors)

        return errors

    def _validate_step_yaml(self, step: Any, index: int) -> list[str]:
        """Validate a single step definition."""
        errors = []
        prefix = f"Step {index + 1}"

        if not isinstance(step, dict):
            return [f"{prefix}: must be a mapping"]

        # Required fields
        if "name" not in step:
            errors.append(f"{prefix}: missing required field 'name'")
        elif not isinstance(step["name"], str):
            errors.append(f"{prefix}: field 'name' must be a string")

        if "harness" not in step:
            errors.append(f"{prefix}: missing required field 'harness'")
        elif not isinstance(step["harness"], str):
            errors.append(f"{prefix}: field 'harness' must be a string")

        if "method" not in step:
            errors.append(f"{prefix}: missing required field 'method'")
        elif not isinstance(step["method"], str):
            errors.append(f"{prefix}: field 'method' must be a string")

        # Optional fields with type validation
        if "on_failure" in step:
            valid_modes = ["abort", "retry", "skip", "human_gate"]
            if step["on_failure"] not in valid_modes:
                errors.append(f"{prefix}: 'on_failure' must be one of {valid_modes}")

        if "retry_count" in step:
            if not isinstance(step["retry_count"], int) or step["retry_count"] < 1:
                errors.append(f"{prefix}: 'retry_count' must be a positive integer")

        if "timeout_seconds" in step:
            if not isinstance(step["timeout_seconds"], int) or step["timeout_seconds"] < 1:
                errors.append(f"{prefix}: 'timeout_seconds' must be a positive integer")

        if "outputs" in step:
            if not isinstance(step["outputs"], list):
                errors.append(f"{prefix}: 'outputs' must be a list")
            elif not all(isinstance(o, str) for o in step["outputs"]):
                errors.append(f"{prefix}: all 'outputs' must be strings")

        return errors

    def _validate_human_gate_yaml(self, name: str, gate: Any) -> list[str]:
        """Validate a human gate configuration."""
        errors = []
        prefix = f"Human gate '{name}'"

        if not isinstance(gate, dict):
            return [f"{prefix}: must be a mapping"]

        if "timeout_hours" in gate:
            if not isinstance(gate["timeout_hours"], (int, float)) or gate["timeout_hours"] <= 0:
                errors.append(f"{prefix}: 'timeout_hours' must be a positive number")

        if "notification_channels" in gate:
            if not isinstance(gate["notification_channels"], list):
                errors.append(f"{prefix}: 'notification_channels' must be a list")

        if "on_timeout" in gate:
            valid_actions = ["skip", "abort", "escalate"]
            if gate["on_timeout"] not in valid_actions:
                errors.append(f"{prefix}: 'on_timeout' must be one of {valid_actions}")

        return errors

    def list_yaml_pipelines(self, pipeline_dir: Path | str | None = None) -> list[dict[str, Any]]:
        """
        List all YAML pipelines in a directory.

        Args:
            pipeline_dir: Directory to search (defaults to package pipelines dir)

        Returns:
            List of dicts with 'path', 'name', and 'description' for each pipeline
        """
        if pipeline_dir is None:
            pipeline_dir = Path(__file__).parent / "pipelines"
        else:
            pipeline_dir = Path(pipeline_dir)

        if not pipeline_dir.exists():
            return []

        pipelines = []
        for path in sorted(pipeline_dir.glob("*.yaml")):
            # Skip schema file
            if path.name == "schema.yaml":
                continue

            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict) and "name" in data:
                    pipelines.append(
                        {
                            "path": str(path),
                            "name": data["name"],
                            "description": data.get("description", ""),
                            "version": data.get("version", "1.0.0"),
                        }
                    )
            except Exception as e:
                logger.warning(f"Failed to parse pipeline {path}: {e}")

        return pipelines

    async def execute(self, pipeline: Pipeline) -> PipelineResult:
        """
        Execute a pipeline from start to finish.

        Args:
            pipeline: Pipeline definition to execute

        Returns:
            PipelineResult with success status and step results
        """
        started_at = datetime.now(UTC)
        context = dict(pipeline.context)
        step_outputs: dict[str, dict] = {}
        step_results: list[StepResult] = []

        logger.info(f"Starting pipeline: {pipeline.name}")

        # Emit pipeline started event
        await self._emit_event(
            EventType.PIPELINE_STARTED,
            pipeline.name,
            data={"step_count": len(pipeline.steps)},
        )

        for i, step in enumerate(pipeline.steps):
            logger.info(f"Executing step {i + 1}/{len(pipeline.steps)}: {step.name}")

            # Emit step started event
            await self._emit_event(
                EventType.STEP_STARTED,
                pipeline.name,
                step_name=step.name,
                data={"step_index": i, "total_steps": len(pipeline.steps)},
            )

            step_result = await self._execute_step(step, context, step_outputs)
            step_results.append(step_result)

            if step_result.status == StepStatus.COMPLETED:
                # Emit step completed event
                await self._emit_event(
                    EventType.STEP_COMPLETED,
                    pipeline.name,
                    step_name=step.name,
                    data={"duration": step_result.duration_seconds},
                )

                # Store outputs in step_outputs for future reference
                step_outputs[step.name] = step_result.outputs

                # Also merge outputs into context
                for key in step.outputs:
                    if key in step_result.outputs:
                        context[key] = step_result.outputs[key]

                # Save checkpoint
                if self.enable_checkpoints:
                    checkpoint_path = self._save_checkpoint(
                        pipeline,
                        context,
                        step_outputs,
                        [s.name for s in step_results if s.status == StepStatus.COMPLETED],
                        i + 1,
                    )
                    # Emit checkpoint saved event
                    await self._emit_event(
                        EventType.CHECKPOINT_SAVED,
                        pipeline.name,
                        data={"checkpoint_path": str(checkpoint_path)},
                    )

            elif step_result.status == StepStatus.FAILED:
                # Emit step failed event
                await self._emit_event(
                    EventType.STEP_FAILED,
                    pipeline.name,
                    step_name=step.name,
                    data={"error": step_result.error, "duration": step_result.duration_seconds},
                )

                if step.on_failure == "abort":
                    logger.error(f"Step {step.name} failed, aborting pipeline")
                    # Emit pipeline failed event
                    await self._emit_event(
                        EventType.PIPELINE_FAILED,
                        pipeline.name,
                        data={"error": step_result.error, "failed_step": step.name},
                    )
                    return PipelineResult(
                        pipeline_name=pipeline.name,
                        success=False,
                        step_results=step_results,
                        context=context,
                        error=step_result.error,
                        started_at=started_at.isoformat(),
                        completed_at=datetime.now(UTC).isoformat(),
                        duration_seconds=(datetime.now(UTC) - started_at).total_seconds(),
                    )
                elif step.on_failure == "skip":
                    logger.warning(f"Step {step.name} failed, skipping and continuing")
                    # Emit step skipped event
                    await self._emit_event(
                        EventType.STEP_SKIPPED,
                        pipeline.name,
                        step_name=step.name,
                        data={"reason": "failure with skip policy"},
                    )
                    # Continue to next step
                elif step.on_failure == "human_gate":
                    logger.info(f"Step {step.name} requires human intervention")

                    # Save checkpoint before pausing
                    checkpoint_path = None
                    if self.enable_checkpoints:
                        checkpoint_path = self._save_checkpoint(
                            pipeline,
                            context,
                            step_outputs,
                            [s.name for s in step_results if s.status == StepStatus.COMPLETED],
                            i,  # Stay at current step for retry after approval
                        )

                    # Try to create approval request via human_gate harness
                    approval_request_id = None
                    human_gate = self.harnesses.get("human_gate")
                    if human_gate is not None:
                        try:
                            from .approval_queue import ApprovalPriority, ApprovalType

                            # Get domain from context if available
                            domain = context.get("domain", "unknown")

                            # Create approval request
                            approval_result = await human_gate.create_approval(
                                approval_type=ApprovalType.FEATURE,
                                domain=domain,
                                title=f"Pipeline Failure: {pipeline.name}/{step.name}",
                                description=f"Step '{step.name}' failed and requires human approval to continue.\n\nError: {step_result.error}",
                                metadata={
                                    "pipeline_name": pipeline.name,
                                    "step_name": step.name,
                                    "error": step_result.error,
                                },
                                priority=ApprovalPriority.HIGH,
                                checkpoint_path=checkpoint_path,
                            )
                            approval_request_id = approval_result.request_id
                            logger.info(
                                f"Created approval request {approval_request_id} for human gate"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to create approval request: {e}")

                    # Emit human gate waiting event
                    await self._emit_event(
                        EventType.HUMAN_GATE_WAITING,
                        pipeline.name,
                        step_name=step.name,
                        data={
                            "reason": "step failed with human_gate policy",
                            "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
                            "approval_request_id": approval_request_id,
                        },
                    )

                    # Return paused result - pipeline will be resumed via approve/resume
                    return PipelineResult(
                        pipeline_name=pipeline.name,
                        success=False,  # Not complete, waiting for human
                        step_results=step_results,
                        context=context,
                        error=f"Paused at step '{step.name}' - awaiting human approval",
                        started_at=started_at.isoformat(),
                        completed_at=datetime.now(UTC).isoformat(),
                        duration_seconds=(datetime.now(UTC) - started_at).total_seconds(),
                        checkpoint_path=str(checkpoint_path) if checkpoint_path else None,
                    )

        completed_at = datetime.now(UTC)

        # Emit pipeline completed event
        await self._emit_event(
            EventType.PIPELINE_COMPLETED,
            pipeline.name,
            data={"duration": (completed_at - started_at).total_seconds()},
        )

        return PipelineResult(
            pipeline_name=pipeline.name,
            success=True,
            step_results=step_results,
            context=context,
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
            duration_seconds=(completed_at - started_at).total_seconds(),
        )

    async def _execute_step(
        self,
        step: PipelineStep,
        context: dict[str, Any],
        step_outputs: dict[str, dict],
    ) -> StepResult:
        """Execute a single pipeline step."""
        import asyncio
        import time

        start_time = time.time()
        retries = 0

        while retries <= step.retry_count:
            try:
                # Get harness
                harness = self.harnesses.get(step.harness)
                if harness is None:
                    raise ValueError(f"Harness not found: {step.harness}")

                # Get method
                method = getattr(harness, step.method, None)
                if method is None:
                    raise ValueError(f"Method not found: {step.harness}.{step.method}")

                # Resolve inputs
                resolved_inputs = {}
                for key, value in step.inputs.items():
                    resolved_inputs[key] = self._resolve_template(value, context, step_outputs)

                # Execute with timeout
                try:
                    result = await asyncio.wait_for(
                        method(**resolved_inputs),
                        timeout=step.timeout_seconds,
                    )
                except TimeoutError:
                    raise TimeoutError(f"Step timed out after {step.timeout_seconds}s")

                # Extract outputs
                outputs = {}
                if isinstance(result, dict):
                    for key in step.outputs:
                        if key in result:
                            outputs[key] = result[key]
                    # Also capture all result keys
                    outputs.update(result)

                duration = time.time() - start_time
                return StepResult(
                    name=step.name,
                    status=StepStatus.COMPLETED,
                    outputs=outputs,
                    duration_seconds=duration,
                    retries=retries,
                )

            except Exception as e:
                retries += 1
                error_msg = f"{type(e).__name__}: {e}"
                logger.warning(f"Step {step.name} failed (attempt {retries}): {error_msg}")

                if retries > step.retry_count or step.on_failure != "retry":
                    duration = time.time() - start_time
                    return StepResult(
                        name=step.name,
                        status=StepStatus.FAILED,
                        error=error_msg,
                        duration_seconds=duration,
                        retries=retries - 1,
                    )

                # Exponential backoff
                await asyncio.sleep(2 ** (retries - 1))

        # Should not reach here
        return StepResult(
            name=step.name,
            status=StepStatus.FAILED,
            error="Max retries exceeded",
            duration_seconds=time.time() - start_time,
            retries=retries,
        )

    def _resolve_template(
        self,
        value: Any,
        context: dict[str, Any],
        step_outputs: dict[str, dict],
    ) -> Any:
        """
        Resolve template strings like {{ context.key }} or {{ steps.step_name.key }}.

        Args:
            value: Value to resolve (may contain templates)
            context: Current pipeline context
            step_outputs: Outputs from previous steps

        Returns:
            Resolved value
        """
        if not isinstance(value, str):
            return value

        # Check for template pattern
        pattern = r"\{\{\s*(\w+(?:\.\w+)*)\s*\}\}"
        match = re.match(f"^{pattern}$", value.strip())

        if not match:
            # No template or partial template - return as-is
            return value

        path = match.group(1)
        parts = path.split(".")

        if parts[0] == "context":
            # Resolve from context
            return self._get_nested(context, parts[1:])
        elif parts[0] == "steps":
            # Resolve from step outputs
            if len(parts) >= 3:
                step_name = parts[1]
                ".".join(parts[2:])
                if step_name in step_outputs:
                    return self._get_nested(step_outputs[step_name], parts[2:])
            return None
        else:
            return value

    def _get_nested(self, obj: dict, keys: list[str]) -> Any:
        """Get nested value from dict using key path."""
        current = obj
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def _save_checkpoint(
        self,
        pipeline: Pipeline,
        context: dict[str, Any],
        step_outputs: dict[str, dict],
        completed_steps: list[str],
        current_step_index: int,
    ) -> Path:
        """Save checkpoint to disk."""
        checkpoint = Checkpoint(
            pipeline_name=pipeline.name,
            pipeline_steps=[asdict(s) for s in pipeline.steps],
            context=context,
            step_outputs=step_outputs,
            completed_steps=completed_steps,
            current_step_index=current_step_index,
            created_at=datetime.now(UTC).isoformat(),
            human_gates={k: asdict(v) for k, v in pipeline.human_gates.items()},
        )

        filename = f"{pipeline.name}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
        checkpoint_path = self.checkpoint_dir / filename

        with open(checkpoint_path, "w") as f:
            json.dump(asdict(checkpoint), f, indent=2, default=str)

        logger.debug(f"Checkpoint saved: {checkpoint_path}")
        return checkpoint_path

    async def resume(self, checkpoint_path: Path) -> PipelineResult | None:
        """
        Resume pipeline from checkpoint.

        Args:
            checkpoint_path: Path to checkpoint file

        Returns:
            PipelineResult if resumed successfully, None if checkpoint invalid
        """
        try:
            with open(checkpoint_path) as f:
                data = json.load(f)

            checkpoint = Checkpoint(**data)

            # Reconstruct pipeline
            steps = [PipelineStep(**s) for s in checkpoint.pipeline_steps]
            pipeline = Pipeline(
                name=checkpoint.pipeline_name,
                steps=steps,
                context=checkpoint.context,
            )

            # Skip completed steps
            remaining_steps = steps[checkpoint.current_step_index :]

            if not remaining_steps:
                logger.info("All steps already completed")
                return PipelineResult(
                    pipeline_name=pipeline.name,
                    success=True,
                    step_results=[],
                    context=checkpoint.context,
                )

            # Create partial pipeline with remaining steps
            partial_pipeline = Pipeline(
                name=pipeline.name,
                steps=remaining_steps,
                context=checkpoint.context,
            )

            # Execute remaining steps
            return await self.execute(partial_pipeline)

        except Exception as e:
            logger.error(f"Failed to resume from checkpoint: {e}")
            return None

    async def resume_from_approval(self, approval_request_id: str) -> PipelineResult | None:
        """
        Resume pipeline from an approved request.

        Looks up the approval request, verifies it's approved, and resumes
        the associated pipeline from its checkpoint.

        Args:
            approval_request_id: The approval request ID

        Returns:
            PipelineResult if resumed successfully, None if not possible
        """
        # Get the human_gate harness to check approval status
        human_gate = self.harnesses.get("human_gate")
        if human_gate is None:
            logger.error("No human_gate harness configured - cannot resume from approval")
            return None

        try:
            # Check approval status
            from .human_gate_harness import GateStatus

            result = await human_gate.check_status(approval_request_id)

            if result.gate_status != GateStatus.APPROVED:
                logger.warning(
                    f"Cannot resume: approval {approval_request_id} is {result.gate_status.value}, not approved"
                )
                return None

            # Get checkpoint path from approval
            if not result.checkpoint_path:
                logger.error(f"Approval {approval_request_id} has no checkpoint path")
                return None

            checkpoint_path = Path(result.checkpoint_path)
            if not checkpoint_path.exists():
                logger.error(f"Checkpoint file not found: {checkpoint_path}")
                return None

            logger.info(f"Resuming pipeline from approval {approval_request_id}")
            return await self.resume(checkpoint_path)

        except Exception as e:
            logger.error(f"Failed to resume from approval {approval_request_id}: {e}")
            return None


def create_orchestration_harness(
    checkpoint_dir: Path,
    harnesses: dict[str, Any] | None = None,
) -> OrchestrationHarness:
    """
    Factory function to create OrchestrationHarness.

    Args:
        checkpoint_dir: Directory for checkpoint files
        harnesses: Dict of harness instances (optional, can add later)

    Returns:
        Configured OrchestrationHarness instance
    """
    return OrchestrationHarness(
        harnesses=harnesses or {},
        checkpoint_dir=checkpoint_dir,
    )


def create_orchestration_harness_from_registry(
    domain: str | None = None,
    project: str | None = None,
    checkpoint_dir: Path | None = None,
) -> OrchestrationHarness:
    """
    Create OrchestrationHarness with full harness wiring from registry.

    This is the recommended way to create an OrchestrationHarness,
    as it automatically wires up all harness dependencies.

    Args:
        domain: Domain name (e.g., "codeswiftr-com")
        project: Project name (e.g., "interview-simulator")
        checkpoint_dir: Optional override for checkpoint directory

    Returns:
        OrchestrationHarness with all harnesses wired

    Example:
        orchestrator = create_orchestration_harness_from_registry(
            domain="codeswiftr-com",
            project="interview-simulator",
        )
        result = await orchestrator.execute(pipeline)
    """
    from .harness_registry import HarnessConfig, HarnessRegistry

    # Create config
    config = HarnessConfig.from_env(domain=domain, project=project)
    if checkpoint_dir:
        config.checkpoint_dir = checkpoint_dir

    # Create registry and get harnesses
    registry = HarnessRegistry(config)
    harnesses = registry.get_for_orchestration()

    return OrchestrationHarness(
        harnesses=harnesses,
        checkpoint_dir=config.checkpoint_dir,
    )
