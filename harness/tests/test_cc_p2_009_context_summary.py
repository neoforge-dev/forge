"""
Test CC-P2-009: Approval Context Summary

Verifies that approval cards display context summary fields:
- files_affected count
- risk_level badge
- estimated_impact (1-5)
"""

import tempfile
from pathlib import Path

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalQueueHarness,
    ApprovalType,
    JSONFileStorage,
)
from forge_harness.webhook_server import ApprovalQueueHandler


@pytest.fixture
def approval_queue():
    """Create a temporary approval queue for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        queue_dir = Path(tmpdir) / ".forge/approvals"
        queue_dir.mkdir(exist_ok=True)

        # Create JSONFileStorage and pass it to ApprovalQueueHarness
        storage = JSONFileStorage(storage_dir=queue_dir)
        harness = ApprovalQueueHarness(
            storage=storage,
            notification_harness=None,
        )
        yield harness


@pytest.fixture
def handler(approval_queue):
    """Create approval handler with temporary queue."""
    handler = ApprovalQueueHandler(
        approval_queue=approval_queue,
        human_gate=None,
        orchestrator=None,
    )
    return handler


@pytest.mark.asyncio
async def test_context_summary_in_serialized_request(handler, approval_queue):
    """Test that serialized requests include context summary fields."""

    # Submit a test approval with metadata
    request = await approval_queue.create_request(
        type=ApprovalType.FEATURE,
        title="Test Feature Complete",
        description="Test feature ready for review",
        domain="test-domain",
        metadata={
            "files_changed": 5,
            "complexity": "medium",
        },
        priority=ApprovalPriority.HIGH,
    )

    # Serialize the request
    serialized = handler._serialize_request(request)

    # Verify the response has context field (not metadata)
    assert "context" in serialized, "Response should have 'context' field"

    # Verify context summary fields are present
    context = serialized["context"]
    assert "files_affected" in context, "Context should include files_affected"
    assert "risk_level" in context, "Context should include risk_level"
    assert "estimated_impact" in context, "Context should include estimated_impact"

    # Verify values are correct
    assert context["files_affected"] == 5, "files_affected should match files_changed"
    assert context["risk_level"] in ["low", "medium", "high"], "risk_level should be valid"
    assert isinstance(context["estimated_impact"], int), "estimated_impact should be integer"
    assert 1 <= context["estimated_impact"] <= 5, "estimated_impact should be 1-5"


@pytest.mark.asyncio
async def test_risk_level_derives_from_priority_and_tier(handler, approval_queue):
    """Test that risk_level is correctly derived from priority and tier."""

    # Test critical priority -> high risk
    request_critical = await approval_queue.create_request(
        type=ApprovalType.DEPLOY,
        title="Critical Deploy",
        description="Production deployment",
        domain="test-domain",
        priority=ApprovalPriority.CRITICAL,
    )
    serialized = handler._serialize_request(request_critical)
    assert serialized["context"]["risk_level"] == "high"

    # Test high priority -> medium risk
    request_high = await approval_queue.create_request(
        type=ApprovalType.FEATURE,
        title="High Priority Feature",
        description="Important feature",
        domain="test-domain",
        priority=ApprovalPriority.HIGH,
    )
    serialized = handler._serialize_request(request_high)
    assert serialized["context"]["risk_level"] == "medium"

    # Test normal priority -> low risk
    request_normal = await approval_queue.create_request(
        type=ApprovalType.CONTENT,
        title="Normal Content",
        description="Regular content",
        domain="test-domain",
        priority=ApprovalPriority.NORMAL,
    )
    serialized = handler._serialize_request(request_normal)
    assert serialized["context"]["risk_level"] == "low"


@pytest.mark.asyncio
async def test_estimated_impact_derives_from_priority(handler, approval_queue):
    """Test that estimated_impact is correctly derived from priority."""

    priorities_and_impacts = [
        (ApprovalPriority.CRITICAL, 5),
        (ApprovalPriority.HIGH, 4),
        (ApprovalPriority.NORMAL, 3),
        (ApprovalPriority.LOW, 2),
    ]

    for priority, expected_impact in priorities_and_impacts:
        request = await approval_queue.create_request(
            type=ApprovalType.FEATURE,
            title=f"Test {priority.value} priority",
            description=f"Testing {priority.value} priority impact",
            domain="test-domain",
            priority=priority,
        )
        serialized = handler._serialize_request(request)
        assert serialized["context"]["estimated_impact"] == expected_impact, (
            f"Priority {priority.value} should have impact {expected_impact}"
        )


@pytest.mark.asyncio
async def test_files_affected_from_multiple_sources(handler, approval_queue):
    """Test that files_affected can be extracted from different metadata formats."""

    # Test with files_changed (number)
    request1 = await approval_queue.create_request(
        type=ApprovalType.FEATURE,
        title="Test with files_changed",
        description="Testing files_changed",
        domain="test-domain",
        metadata={"files_changed": 10},
    )
    serialized1 = handler._serialize_request(request1)
    assert serialized1["context"]["files_affected"] == 10

    # Test with no file metadata (should default to 0)
    request2 = await approval_queue.create_request(
        type=ApprovalType.CONTENT,
        title="Test with no files",
        description="Testing no files",
        domain="test-domain",
        metadata={},
    )
    serialized2 = handler._serialize_request(request2)
    assert serialized2["context"]["files_affected"] == 0


@pytest.mark.asyncio
async def test_list_approvals_includes_context_summary(handler, approval_queue):
    """Test that list_pending returns approvals with context summary."""

    # Submit multiple approvals
    await approval_queue.create_request(
        type=ApprovalType.FEATURE,
        title="Feature 1",
        description="Test feature 1",
        domain="test-domain",
        metadata={"files_changed": 3},
        priority=ApprovalPriority.HIGH,
    )

    await approval_queue.create_request(
        type=ApprovalType.DEPLOY,
        title="Deploy 1",
        description="Test deployment",
        domain="test-domain",
        metadata={"files_changed": 1},
        priority=ApprovalPriority.CRITICAL,
    )

    # List approvals
    approvals = await handler.list_pending()

    # Verify both approvals have context summary
    assert len(approvals) == 2
    for approval in approvals:
        assert "context" in approval
        context = approval["context"]
        assert "files_affected" in context
        assert "risk_level" in context
        assert "estimated_impact" in context


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
