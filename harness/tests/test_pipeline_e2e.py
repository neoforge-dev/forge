"""
End-to-end tests for harness pipeline execution.

Tests cover:
1. MVP Launch Pipeline - full lifecycle with quality gates
2. Pipeline Failure Scenarios - failure handling and recovery
3. Approval Flow - human gate integration and decision handling
4. Checkpoint Recovery - resume from saved state
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_harness.orchestration_harness import (
    HumanGateConfig,
    Pipeline,
    PipelineStep,
    StepStatus,
    create_orchestration_harness,
)
from forge_harness.pipeline_callbacks import (
    EventType,
    PipelineCallback,
    PipelineEvent,
)

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def checkpoint_dir(tmp_path):
    """Create temporary checkpoint directory."""
    cp_dir = tmp_path / "checkpoints"
    cp_dir.mkdir()
    return cp_dir


@pytest.fixture
def orchestrator(checkpoint_dir):
    """Create orchestrator with mock harnesses."""
    mock_harnesses = {
        "preflight": MagicMock(),
        "deployment": MagicMock(),
        "analytics": MagicMock(),
        "human_gate": MagicMock(),
        "growth": MagicMock(),
        "content": MagicMock(),
        "notion": MagicMock(),
    }
    return create_orchestration_harness(
        checkpoint_dir=checkpoint_dir,
        harnesses=mock_harnesses,
    )


@pytest.fixture
def simple_pipeline():
    """Create a simple test pipeline."""
    return Pipeline(
        name="simple_test",
        description="Simple test pipeline",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="run_checks",
                inputs={"domain": "test"},
                outputs=["passed"],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step2",
                harness="deployment",
                method="check_quality_gates",
                inputs={"passed": "{{ steps.step1.passed }}"},
                outputs=["coverage"],
                timeout_seconds=10,
            ),
        ],
        context={"domain": "test"},
    )


@pytest.fixture
def mvp_launch_pipeline():
    """Create MVP launch pipeline from YAML-like structure."""
    return Pipeline(
        name="mvp_launch",
        description="Full MVP launch sequence with quality gates",
        steps=[
            PipelineStep(
                name="preflight",
                harness="preflight",
                method="run_checks",
                inputs={"domain": "{{ context.domain }}"},
                outputs=["passed", "warnings"],
                on_failure="abort",
                timeout_seconds=300,
            ),
            PipelineStep(
                name="quality_gates",
                harness="deployment",
                method="check_quality_gates",
                inputs={"project_dir": "{{ context.project_dir }}"},
                outputs=["coverage", "tests_passed"],
                on_failure="abort",
                timeout_seconds=600,
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
                on_failure="abort",
                timeout_seconds=1800,
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
                on_failure="human_gate",
                timeout_seconds=300,
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
                timeout_seconds=600,
            ),
        ],
        context={
            "domain": "test-domain",
            "project": "test-project",
            "project_dir": "/test/path",
            "dry_run": False,
        },
        human_gates={
            "smoke_failure": HumanGateConfig(
                timeout_hours=4,
                notification_channels=["slack"],
                on_timeout="abort",
                message="Smoke tests failed",
            ),
        },
    )


@pytest.fixture
def callback_tracker():
    """Create a test callback to track events."""

    class TestCallback(PipelineCallback):
        def __init__(self):
            self.events = []

        async def on_event(self, event: PipelineEvent) -> None:
            self.events.append(event)

    return TestCallback()


# ============================================================================
# TESTS: MVP LAUNCH PIPELINE
# ============================================================================


@pytest.mark.asyncio
async def test_mvp_launch_pipeline_success(orchestrator, mvp_launch_pipeline, callback_tracker):
    """Test successful MVP launch pipeline execution."""
    orchestrator.register_callback(callback_tracker)

    # Mock all step methods to succeed
    orchestrator.harnesses["preflight"].run_checks = AsyncMock(
        return_value={"passed": True, "warnings": []}
    )
    orchestrator.harnesses["deployment"].check_quality_gates = AsyncMock(
        return_value={"coverage": 85.5, "tests_passed": True}
    )
    orchestrator.harnesses["deployment"].full_deploy = AsyncMock(
        return_value={
            "backend_url": "https://backend.test.com",
            "frontend_url": "https://frontend.test.com",
        }
    )
    orchestrator.harnesses["deployment"].run_smoke_tests = AsyncMock(
        return_value={"all_passed": True}
    )
    orchestrator.harnesses["analytics"].create_mvp_dashboard = AsyncMock(
        return_value={"dashboard_id": "dash-123", "dashboard_url": "https://dash.test.com"}
    )

    # Execute pipeline
    result = await orchestrator.execute(mvp_launch_pipeline)

    # Assertions
    assert result.success is True
    assert result.pipeline_name == "mvp_launch"
    assert len(result.step_results) == 5
    assert all(sr.status == StepStatus.COMPLETED for sr in result.step_results)
    assert result.error is None
    assert result.duration_seconds > 0

    # Verify all steps executed in order
    assert result.step_results[0].name == "preflight"
    assert result.step_results[1].name == "quality_gates"
    assert result.step_results[2].name == "deploy"
    assert result.step_results[3].name == "smoke_tests"
    assert result.step_results[4].name == "create_dashboard"

    # Verify outputs were captured
    assert result.step_results[0].outputs["passed"] is True
    assert result.step_results[2].outputs["backend_url"] == "https://backend.test.com"

    # Verify callbacks were fired
    event_types = [e.event_type for e in callback_tracker.events]
    assert EventType.PIPELINE_STARTED in event_types
    assert EventType.PIPELINE_COMPLETED in event_types
    assert event_types.count(EventType.STEP_STARTED) == 5
    assert event_types.count(EventType.STEP_COMPLETED) == 5


@pytest.mark.asyncio
async def test_mvp_launch_pipeline_initialization(mvp_launch_pipeline):
    """Test MVP pipeline initializes with correct structure."""
    assert mvp_launch_pipeline.name == "mvp_launch"
    assert len(mvp_launch_pipeline.steps) == 5
    assert mvp_launch_pipeline.context["domain"] == "test-domain"
    assert mvp_launch_pipeline.context["project_dir"] == "/test/path"

    # Verify step order
    step_names = [s.name for s in mvp_launch_pipeline.steps]
    assert step_names == ["preflight", "quality_gates", "deploy", "smoke_tests", "create_dashboard"]

    # Verify failure policies
    assert mvp_launch_pipeline.steps[0].on_failure == "abort"  # preflight
    assert mvp_launch_pipeline.steps[3].on_failure == "human_gate"  # smoke_tests
    assert mvp_launch_pipeline.steps[4].on_failure == "skip"  # create_dashboard


@pytest.mark.asyncio
async def test_mvp_launch_step_order_execution(orchestrator, mvp_launch_pipeline):
    """Test that steps execute in correct order with proper context."""
    call_order = []

    async def track_call_preflight(**kw):
        call_order.append(("preflight", kw))
        return {"passed": True, "warnings": []}

    async def track_call_quality_gates(**kw):
        call_order.append(("quality_gates", kw))
        return {"coverage": 85, "tests_passed": True}

    async def track_call_deploy(**kw):
        call_order.append(("deploy", kw))
        return {"backend_url": "http://backend", "frontend_url": "http://frontend"}

    async def track_call_smoke_tests(**kw):
        call_order.append(("smoke_tests", kw))
        return {"all_passed": True}

    async def track_call_dashboard(**kw):
        call_order.append(("dashboard", kw))
        return {"dashboard_id": "123", "dashboard_url": "http://dash"}

    orchestrator.harnesses["preflight"].run_checks = AsyncMock(side_effect=track_call_preflight)
    orchestrator.harnesses["deployment"].check_quality_gates = AsyncMock(
        side_effect=track_call_quality_gates
    )
    orchestrator.harnesses["deployment"].full_deploy = AsyncMock(side_effect=track_call_deploy)
    orchestrator.harnesses["deployment"].run_smoke_tests = AsyncMock(
        side_effect=track_call_smoke_tests
    )
    orchestrator.harnesses["analytics"].create_mvp_dashboard = AsyncMock(
        side_effect=track_call_dashboard
    )

    result = await orchestrator.execute(mvp_launch_pipeline)

    assert result.success is True
    # Verify execution order
    assert call_order[0][0] == "preflight"
    assert call_order[1][0] == "quality_gates"
    assert call_order[2][0] == "deploy"
    assert call_order[3][0] == "smoke_tests"
    assert call_order[4][0] == "dashboard"


@pytest.mark.asyncio
async def test_mvp_launch_checkpoint_creation(orchestrator, mvp_launch_pipeline, checkpoint_dir):
    """Test checkpoint creation after each step."""
    orchestrator.harnesses["preflight"].run_checks = AsyncMock(
        return_value={"passed": True, "warnings": []}
    )
    orchestrator.harnesses["deployment"].check_quality_gates = AsyncMock(
        return_value={"coverage": 85, "tests_passed": True}
    )
    orchestrator.harnesses["deployment"].full_deploy = AsyncMock(
        return_value={"backend_url": "http://backend", "frontend_url": "http://frontend"}
    )
    orchestrator.harnesses["deployment"].run_smoke_tests = AsyncMock(
        return_value={"all_passed": True}
    )
    orchestrator.harnesses["analytics"].create_mvp_dashboard = AsyncMock(
        return_value={"dashboard_id": "123", "dashboard_url": "http://dash"}
    )

    result = await orchestrator.execute(mvp_launch_pipeline)

    # Check that checkpoints were created (one per completed step)
    checkpoint_files = list(checkpoint_dir.glob("*.json"))
    assert len(checkpoint_files) >= 1  # At least one checkpoint per step

    # Verify checkpoint contents
    with open(checkpoint_files[-1]) as f:
        last_checkpoint = json.load(f)
    assert last_checkpoint["pipeline_name"] == "mvp_launch"
    assert len(last_checkpoint["completed_steps"]) == 5
    assert last_checkpoint["current_step_index"] == 5


@pytest.mark.asyncio
async def test_mvp_launch_approval_gate_handling(orchestrator, mvp_launch_pipeline, checkpoint_dir):
    """Test approval gate handling when smoke tests fail."""
    # Setup mocks - smoke tests fail
    orchestrator.harnesses["preflight"].run_checks = AsyncMock(
        return_value={"passed": True, "warnings": []}
    )
    orchestrator.harnesses["deployment"].check_quality_gates = AsyncMock(
        return_value={"coverage": 85, "tests_passed": True}
    )
    orchestrator.harnesses["deployment"].full_deploy = AsyncMock(
        return_value={"backend_url": "http://backend", "frontend_url": "http://frontend"}
    )
    orchestrator.harnesses["deployment"].run_smoke_tests = AsyncMock(
        side_effect=Exception("Smoke tests failed: API timeout")
    )
    orchestrator.harnesses["human_gate"].create_approval = AsyncMock(
        return_value=MagicMock(request_id="approval-123")
    )

    result = await orchestrator.execute(mvp_launch_pipeline)

    # Should pause at smoke_tests with human_gate policy
    assert result.success is False
    assert len(result.step_results) == 4  # Completed through deploy
    assert result.step_results[3].name == "smoke_tests"
    assert result.step_results[3].status == StepStatus.FAILED
    assert "awaiting human approval" in result.error

    # Verify approval request was created
    orchestrator.harnesses["human_gate"].create_approval.assert_called_once()
    call_args = orchestrator.harnesses["human_gate"].create_approval.call_args
    assert "Pipeline Failure" in call_args.kwargs["title"]
    assert result.checkpoint_path is not None


# ============================================================================
# TESTS: PIPELINE FAILURE SCENARIOS
# ============================================================================


@pytest.mark.asyncio
async def test_step_failure_with_abort_policy(orchestrator, simple_pipeline):
    """Test step failure with abort policy."""
    orchestrator.harnesses["preflight"].run_checks = AsyncMock(
        side_effect=Exception("Preflight check failed")
    )

    result = await orchestrator.execute(simple_pipeline)

    assert result.success is False
    assert len(result.step_results) == 1
    assert result.step_results[0].status == StepStatus.FAILED
    assert "Preflight check failed" in result.step_results[0].error
    assert result.error is not None


@pytest.mark.asyncio
async def test_step_failure_with_retry_policy(orchestrator, checkpoint_dir):
    """Test step failure with retry policy."""
    pipeline = Pipeline(
        name="retry_test",
        steps=[
            PipelineStep(
                name="flaky_step",
                harness="deployment",
                method="flaky_method",
                inputs={},
                outputs=[],
                on_failure="retry",
                retry_count=3,
                timeout_seconds=10,
            ),
        ],
    )

    attempt_count = [0]

    async def flaky_method(**kwargs):
        attempt_count[0] += 1
        if attempt_count[0] < 3:
            raise Exception("Temporary failure")
        return {"success": True}

    orchestrator.harnesses["deployment"].flaky_method = AsyncMock(side_effect=flaky_method)

    result = await orchestrator.execute(pipeline)

    assert result.success is True
    assert result.step_results[0].status == StepStatus.COMPLETED
    assert result.step_results[0].retries == 2
    assert orchestrator.harnesses["deployment"].flaky_method.call_count == 3


@pytest.mark.asyncio
async def test_step_failure_with_skip_policy(orchestrator, simple_pipeline):
    """Test step failure with skip policy."""
    pipeline = Pipeline(
        name="skip_test",
        steps=[
            PipelineStep(
                name="optional_step",
                harness="analytics",
                method="optional_method",
                inputs={},
                outputs=[],
                on_failure="skip",
                timeout_seconds=10,
            ),
            PipelineStep(
                name="required_step",
                harness="deployment",
                method="required_method",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    orchestrator.harnesses["analytics"].optional_method = AsyncMock(
        side_effect=Exception("Analytics failed")
    )
    orchestrator.harnesses["deployment"].required_method = AsyncMock(
        return_value={"result": "success"}
    )

    result = await orchestrator.execute(pipeline)

    assert result.success is True
    assert len(result.step_results) == 2
    # First step shows in results even though skipped
    assert result.step_results[0].status == StepStatus.FAILED
    # Second step still executed
    assert result.step_results[1].status == StepStatus.COMPLETED


@pytest.mark.asyncio
async def test_step_timeout_failure(orchestrator):
    """Test step timeout handling."""
    pipeline = Pipeline(
        name="timeout_test",
        steps=[
            PipelineStep(
                name="slow_step",
                harness="deployment",
                method="slow_method",
                inputs={},
                outputs=[],
                timeout_seconds=1,
            ),
        ],
    )

    async def slow_method(**kwargs):
        await asyncio.sleep(2)  # Sleep longer than timeout
        return {"result": "success"}

    orchestrator.harnesses["deployment"].slow_method = AsyncMock(side_effect=slow_method)

    result = await orchestrator.execute(pipeline)

    assert result.success is False
    assert result.step_results[0].status == StepStatus.FAILED
    assert "timed out" in result.step_results[0].error.lower()


@pytest.mark.asyncio
async def test_harness_not_found_failure(orchestrator):
    """Test failure when harness doesn't exist."""
    pipeline = Pipeline(
        name="bad_harness_test",
        steps=[
            PipelineStep(
                name="bad_step",
                harness="nonexistent_harness",
                method="method",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    result = await orchestrator.execute(pipeline)

    assert result.success is False
    assert "Harness not found" in result.step_results[0].error


@pytest.mark.asyncio
async def test_method_not_found_failure(orchestrator):
    """Test failure when method doesn't exist on harness."""
    pipeline = Pipeline(
        name="bad_method_test",
        steps=[
            PipelineStep(
                name="bad_step",
                harness="deployment",
                method="nonexistent_method",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    result = await orchestrator.execute(pipeline)

    assert result.success is False
    # Check for either "Method not found" or the error from getattr returning None
    assert (
        "Method not found" in result.step_results[0].error
        or "can't be used in 'await'" in result.step_results[0].error
    )


@pytest.mark.asyncio
async def test_rollback_behavior_on_failure(orchestrator, checkpoint_dir):
    """Test checkpoint state on failure for recovery."""
    pipeline = Pipeline(
        name="rollback_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="step1",
                inputs={},
                outputs=["value1"],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step2",
                harness="deployment",
                method="step2",
                inputs={"v": "{{ steps.step1.value1 }}"},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    orchestrator.harnesses["preflight"].step1 = AsyncMock(return_value={"value1": "data"})
    orchestrator.harnesses["deployment"].step2 = AsyncMock(side_effect=Exception("Step 2 failed"))

    result = await orchestrator.execute(pipeline)

    # Check that we have a checkpoint at the failure point
    assert not result.success
    checkpoint_files = list(checkpoint_dir.glob("*.json"))
    assert len(checkpoint_files) >= 1

    # Last checkpoint should have completed step1 but not step2
    with open(checkpoint_files[-1]) as f:
        checkpoint = json.load(f)
    assert "step1" in checkpoint["completed_steps"]


@pytest.mark.asyncio
async def test_error_logging_on_failure(orchestrator, checkpoint_dir):
    """Test error details are logged and captured."""
    pipeline = Pipeline(
        name="error_logging_test",
        steps=[
            PipelineStep(
                name="step_with_details",
                harness="deployment",
                method="detailed_error",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    error_msg = "Database connection failed: connection refused on localhost:5432"
    orchestrator.harnesses["deployment"].detailed_error = AsyncMock(
        side_effect=ValueError(error_msg)
    )

    result = await orchestrator.execute(pipeline)

    assert result.success is False
    assert error_msg in result.step_results[0].error
    assert "ValueError" in result.step_results[0].error


# ============================================================================
# TESTS: APPROVAL FLOW
# ============================================================================


@pytest.mark.asyncio
async def test_approval_request_creation(orchestrator):
    """Test approval request is created on human_gate failure."""
    pipeline = Pipeline(
        name="approval_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="deployment",
                method="step1",
                inputs={},
                outputs=[],
                on_failure="human_gate",
                timeout_seconds=10,
            ),
        ],
        context={"domain": "test-domain"},
    )

    orchestrator.harnesses["deployment"].step1 = AsyncMock(side_effect=Exception("Step failed"))
    orchestrator.harnesses["human_gate"].create_approval = AsyncMock(
        return_value=MagicMock(request_id="approval-456")
    )

    result = await orchestrator.execute(pipeline)

    # Verify approval was requested
    orchestrator.harnesses["human_gate"].create_approval.assert_called_once()
    args = orchestrator.harnesses["human_gate"].create_approval.call_args

    # Check approval details
    assert args.kwargs["domain"] == "test-domain"
    assert "step1" in args.kwargs["title"]
    assert "Step failed" in args.kwargs["description"]


@pytest.mark.asyncio
async def test_approval_waiting_state(orchestrator, callback_tracker):
    """Test pipeline enters waiting state for approval."""
    orchestrator.register_callback(callback_tracker)

    pipeline = Pipeline(
        name="approval_wait_test",
        steps=[
            PipelineStep(
                name="critical_step",
                harness="deployment",
                method="critical",
                inputs={},
                outputs=[],
                on_failure="human_gate",
                timeout_seconds=10,
            ),
        ],
    )

    orchestrator.harnesses["deployment"].critical = AsyncMock(
        side_effect=Exception("Critical error")
    )
    orchestrator.harnesses["human_gate"].create_approval = AsyncMock(
        return_value=MagicMock(request_id="approval-789")
    )

    result = await orchestrator.execute(pipeline)

    # Pipeline should return with waiting status
    assert not result.success
    assert "awaiting human approval" in result.error

    # Verify human gate waiting event was fired
    event_types = [e.event_type for e in callback_tracker.events]
    assert EventType.HUMAN_GATE_WAITING in event_types


@pytest.mark.asyncio
async def test_approval_checkpoint_saved(orchestrator, checkpoint_dir):
    """Test checkpoint is saved when pausing for approval."""
    pipeline = Pipeline(
        name="approval_checkpoint_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="step1",
                inputs={},
                outputs=["data1"],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step2",
                harness="deployment",
                method="step2",
                inputs={"data": "{{ steps.step1.data1 }}"},
                outputs=[],
                on_failure="human_gate",
                timeout_seconds=10,
            ),
        ],
    )

    orchestrator.harnesses["preflight"].step1 = AsyncMock(return_value={"data1": "important"})
    orchestrator.harnesses["deployment"].step2 = AsyncMock(side_effect=Exception("Step 2 failed"))
    orchestrator.harnesses["human_gate"].create_approval = AsyncMock(
        return_value=MagicMock(request_id="approval-101")
    )

    result = await orchestrator.execute(pipeline)

    assert result.checkpoint_path is not None
    checkpoint_path = Path(result.checkpoint_path)
    assert checkpoint_path.exists()

    # Verify checkpoint has correct state
    with open(checkpoint_path) as f:
        checkpoint = json.load(f)
    assert checkpoint["pipeline_name"] == "approval_checkpoint_test"
    assert "step1" in checkpoint["completed_steps"]
    # The pipeline context should have data1 from step1 outputs
    assert checkpoint["context"]["data1"] == "important"


@pytest.mark.asyncio
async def test_approval_rejection_handling(orchestrator):
    """Test pipeline handles approval rejection appropriately."""
    # This is more of an integration test - the orchestrator pauses,
    # and rejection would be handled in the approval queue
    pipeline = Pipeline(
        name="rejection_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="deployment",
                method="step1",
                inputs={},
                outputs=[],
                on_failure="human_gate",
                timeout_seconds=10,
            ),
        ],
    )

    orchestrator.harnesses["deployment"].step1 = AsyncMock(side_effect=Exception("Failed"))
    orchestrator.harnesses["human_gate"].create_approval = AsyncMock(
        return_value=MagicMock(request_id="approval-102")
    )

    result = await orchestrator.execute(pipeline)

    # The orchestrator creates approval request but doesn't handle decision
    # That's handled by the approval_queue module
    assert not result.success
    assert result.checkpoint_path is not None


@pytest.mark.asyncio
async def test_approval_timeout_behavior(orchestrator, checkpoint_dir):
    """Test timeout handling for approval gates."""
    pipeline = Pipeline(
        name="timeout_gate_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="deployment",
                method="step1",
                inputs={},
                outputs=[],
                on_failure="human_gate",
                timeout_seconds=10,
            ),
        ],
        human_gates={
            "test_gate": HumanGateConfig(
                timeout_hours=0.001,  # Very short timeout for testing
                on_timeout="abort",
            ),
        },
    )

    orchestrator.harnesses["deployment"].step1 = AsyncMock(side_effect=Exception("Failed"))
    orchestrator.harnesses["human_gate"].create_approval = AsyncMock(
        return_value=MagicMock(request_id="approval-103")
    )

    result = await orchestrator.execute(pipeline)

    # Approval was created, timeout is handled by approval_queue
    assert not result.success
    orchestrator.harnesses["human_gate"].create_approval.assert_called_once()


# ============================================================================
# TESTS: CHECKPOINT RECOVERY
# ============================================================================


@pytest.mark.asyncio
async def test_checkpoint_resume_from_saved_state(orchestrator, checkpoint_dir):
    """Test resuming pipeline from saved checkpoint."""
    # First execution - succeed on step1, fail on step2
    pipeline = Pipeline(
        name="resume_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="step1",
                inputs={},
                outputs=["data"],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step2",
                harness="deployment",
                method="step2",
                inputs={"data": "{{ steps.step1.data }}"},
                outputs=[],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step3",
                harness="analytics",
                method="step3",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    orchestrator.harnesses["preflight"].step1 = AsyncMock(return_value={"data": "test_value"})
    orchestrator.harnesses["deployment"].step2 = AsyncMock(side_effect=Exception("Step 2 failed"))
    orchestrator.harnesses["analytics"].step3 = AsyncMock(return_value={"status": "ok"})

    # First execution - fails at step2
    result1 = await orchestrator.execute(pipeline)
    assert not result1.success

    # Get checkpoint
    checkpoint_files = list(checkpoint_dir.glob("*.json"))
    assert len(checkpoint_files) == 1
    checkpoint_path = checkpoint_files[0]

    # Fix step2 and resume
    orchestrator.harnesses["deployment"].step2 = AsyncMock(return_value={"step2": "success"})

    # Resume from checkpoint
    result2 = await orchestrator.resume(checkpoint_path)

    assert result2 is not None
    assert result2.success is True
    # All 3 steps should be in results after resume
    assert len(result2.step_results) >= 2  # At least step2 and step3


@pytest.mark.asyncio
async def test_skip_completed_steps_on_resume(orchestrator, checkpoint_dir):
    """Test that completed steps are skipped when resuming."""
    pipeline = Pipeline(
        name="skip_completed_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="step1",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step2",
                harness="deployment",
                method="step2",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    step1_calls = []
    step2_calls = []

    async def step1_impl(**kwargs):
        step1_calls.append(1)
        return {"result": "ok"}

    async def step2_impl(**kwargs):
        step2_calls.append(1)
        return {"result": "ok"}

    orchestrator.harnesses["preflight"].step1 = AsyncMock(side_effect=step1_impl)
    orchestrator.harnesses["deployment"].step2 = AsyncMock(side_effect=step2_impl)

    # Execute full pipeline
    result1 = await orchestrator.execute(pipeline)
    assert result1.success is True

    # Get checkpoint
    checkpoint_files = list(checkpoint_dir.glob("*.json"))
    checkpoint_path = checkpoint_files[-1]

    # Reset call trackers
    step1_calls.clear()
    step2_calls.clear()

    # Resume from checkpoint
    result2 = await orchestrator.resume(checkpoint_path)

    # Step1 should not have been called again (already completed)
    assert len(step1_calls) == 0
    assert len(step2_calls) == 0  # Both steps already completed


@pytest.mark.asyncio
async def test_state_restoration_on_resume(orchestrator, checkpoint_dir):
    """Test that context and outputs are restored correctly."""
    pipeline = Pipeline(
        name="state_restore_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="step1",
                inputs={},
                outputs=["config"],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step2",
                harness="deployment",
                method="step2",
                inputs={"config": "{{ steps.step1.config }}"},
                outputs=["deployed"],
                timeout_seconds=10,
            ),
        ],
        context={"env": "staging"},
    )

    orchestrator.harnesses["preflight"].step1 = AsyncMock(
        return_value={"config": {"setting1": "value1"}}
    )
    orchestrator.harnesses["deployment"].step2 = AsyncMock(side_effect=Exception("Deploy failed"))

    # First execution fails at step2
    result1 = await orchestrator.execute(pipeline)
    assert not result1.success

    # Get checkpoint
    checkpoint_files = list(checkpoint_dir.glob("*.json"))
    checkpoint_path = checkpoint_files[0]

    # Verify checkpoint has correct state
    with open(checkpoint_path) as f:
        checkpoint_data = json.load(f)

    assert checkpoint_data["context"]["env"] == "staging"
    assert checkpoint_data["step_outputs"]["step1"]["config"]["setting1"] == "value1"

    # Fix step2 and resume
    orchestrator.harnesses["deployment"].step2 = AsyncMock(return_value={"deployed": True})

    result2 = await orchestrator.resume(checkpoint_path)

    assert result2 is not None
    # Context should be preserved
    assert "env" in result2.context


@pytest.mark.asyncio
async def test_checkpoint_with_partial_step_outputs(orchestrator, checkpoint_dir):
    """Test checkpoint handles steps with partial outputs correctly."""
    pipeline = Pipeline(
        name="partial_output_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="step1",
                inputs={},
                outputs=["key1", "key2"],  # Declares 2 outputs
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step2",
                harness="deployment",
                method="step2",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    # Step1 returns only one of the declared outputs
    orchestrator.harnesses["preflight"].step1 = AsyncMock(return_value={"key1": "value1"})
    orchestrator.harnesses["deployment"].step2 = AsyncMock(return_value={"status": "ok"})

    result = await orchestrator.execute(pipeline)

    assert result.success is True
    # Verify checkpoint was created and has the partial outputs
    checkpoint_files = list(checkpoint_dir.glob("*.json"))
    with open(checkpoint_files[-1]) as f:
        checkpoint_data = json.load(f)

    assert "step1" in checkpoint_data["step_outputs"]
    assert checkpoint_data["step_outputs"]["step1"]["key1"] == "value1"


@pytest.mark.asyncio
async def test_resume_from_nonexistent_checkpoint(orchestrator):
    """Test graceful handling of nonexistent checkpoint path."""
    nonexistent_path = Path("/nonexistent/checkpoint.json")
    result = await orchestrator.resume(nonexistent_path)

    assert result is None


@pytest.mark.asyncio
async def test_resume_with_invalid_checkpoint_data(orchestrator, checkpoint_dir):
    """Test handling of corrupted checkpoint file."""
    # Create invalid checkpoint
    invalid_path = checkpoint_dir / "invalid.json"
    with open(invalid_path, "w") as f:
        f.write("{invalid json content")

    result = await orchestrator.resume(invalid_path)
    assert result is None


# ============================================================================
# TESTS: INTEGRATION & EDGE CASES
# ============================================================================


@pytest.mark.asyncio
async def test_pipeline_context_passing_between_steps(orchestrator):
    """Test context is properly passed and updated between steps."""
    pipeline = Pipeline(
        name="context_passing_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="step1",
                inputs={"initial": "{{ context.initial_value }}"},
                outputs=["result1"],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step2",
                harness="deployment",
                method="step2",
                inputs={"from_step1": "{{ steps.step1.result1 }}"},
                outputs=["result2"],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step3",
                harness="analytics",
                method="step3",
                inputs={
                    "from_step2": "{{ steps.step2.result2 }}",
                    "initial": "{{ context.initial_value }}",
                },
                outputs=[],
                timeout_seconds=10,
            ),
        ],
        context={"initial_value": "setup"},
    )

    # Track what each step receives
    inputs_received = {}

    async def make_step(name):
        async def step_impl(**kwargs):
            inputs_received[name] = kwargs
            return {f"result_{name[-1]}": f"output_from_{name}"}

        return step_impl

    orchestrator.harnesses["preflight"].step1 = AsyncMock(side_effect=await make_step("step1"))
    orchestrator.harnesses["deployment"].step2 = AsyncMock(side_effect=await make_step("step2"))
    orchestrator.harnesses["analytics"].step3 = AsyncMock(side_effect=await make_step("step3"))

    result = await orchestrator.execute(pipeline)

    assert result.success is True
    # Verify context was passed correctly
    assert inputs_received["step1"]["initial"] == "setup"


@pytest.mark.asyncio
async def test_multiple_callbacks_on_events(orchestrator):
    """Test multiple callbacks receive all events."""

    class CallbackA(PipelineCallback):
        def __init__(self):
            self.events = []

        async def on_event(self, event: PipelineEvent) -> None:
            self.events.append(event)

    class CallbackB(PipelineCallback):
        def __init__(self):
            self.events = []

        async def on_event(self, event: PipelineEvent) -> None:
            self.events.append(event)

    callback_a = CallbackA()
    callback_b = CallbackB()

    orchestrator.register_callback(callback_a)
    orchestrator.register_callback(callback_b)

    pipeline = Pipeline(
        name="multi_callback_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="step1",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    orchestrator.harnesses["preflight"].step1 = AsyncMock(return_value={"ok": True})

    result = await orchestrator.execute(pipeline)

    assert result.success is True
    assert len(callback_a.events) > 0
    assert len(callback_b.events) > 0
    assert len(callback_a.events) == len(callback_b.events)


@pytest.mark.asyncio
async def test_pipeline_duration_tracking(orchestrator):
    """Test pipeline and step duration is accurately tracked."""

    pipeline = Pipeline(
        name="duration_test",
        steps=[
            PipelineStep(
                name="step1",
                harness="preflight",
                method="step1",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
            PipelineStep(
                name="step2",
                harness="deployment",
                method="step2",
                inputs={},
                outputs=[],
                timeout_seconds=10,
            ),
        ],
    )

    async def slow_step(**kwargs):
        await asyncio.sleep(0.1)
        return {"ok": True}

    orchestrator.harnesses["preflight"].step1 = AsyncMock(side_effect=slow_step)
    orchestrator.harnesses["deployment"].step2 = AsyncMock(side_effect=slow_step)

    result = await orchestrator.execute(pipeline)

    assert result.success is True
    assert result.duration_seconds > 0.2  # Total time for both steps
    assert result.step_results[0].duration_seconds > 0
    assert result.step_results[1].duration_seconds > 0


@pytest.mark.asyncio
async def test_empty_step_outputs_handling(orchestrator):
    """Test steps that return no outputs."""
    pipeline = Pipeline(
        name="empty_output_test",
        steps=[
            PipelineStep(
                name="step_no_output",
                harness="analytics",
                method="fire_and_forget",
                inputs={},
                outputs=[],  # No outputs declared
                timeout_seconds=10,
            ),
        ],
    )

    orchestrator.harnesses["analytics"].fire_and_forget = AsyncMock(return_value=None)

    result = await orchestrator.execute(pipeline)

    assert result.success is True
    assert result.step_results[0].outputs == {} or result.step_results[0].outputs is None


@pytest.mark.asyncio
async def test_yaml_pipeline_loading(orchestrator, tmp_path):
    """Test loading pipeline from YAML."""
    yaml_content = """
name: yaml_test_pipeline
description: Test loading from YAML
steps:
  - name: step1
    harness: preflight
    method: run_checks
    inputs:
      domain: "test"
    outputs:
      - passed
  - name: step2
    harness: deployment
    method: verify
    inputs:
      passed: "{{ steps.step1.passed }}"
    outputs: []
context:
  domain: yaml-test
"""
    yaml_file = tmp_path / "test_pipeline.yaml"
    yaml_file.write_text(yaml_content)

    pipeline = orchestrator.load_pipeline_from_yaml(yaml_file)

    assert pipeline.name == "yaml_test_pipeline"
    assert len(pipeline.steps) == 2
    assert pipeline.steps[0].name == "step1"
    assert pipeline.context["domain"] == "yaml-test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
