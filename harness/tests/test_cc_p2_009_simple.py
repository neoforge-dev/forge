"""
Test CC-P2-009: Approval Context Summary (Simplified)

Verifies that approval serialization includes context summary fields.
"""

from datetime import UTC, datetime

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.webhook_server import ApprovalQueueHandler


def test_serialize_request_includes_context():
    """Test that _serialize_request includes context field with summary."""

    # Create a mock approval request
    request = ApprovalRequest(
        id="test-123",
        type=ApprovalType.FEATURE,
        domain="test-domain",
        title="Test Feature",
        description="Test description",
        metadata={"files_changed": 5, "complexity": "medium"},
        priority=ApprovalPriority.HIGH,
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(UTC),
    )

    # Create handler (no dependencies needed for serialization)
    handler = ApprovalQueueHandler(
        approval_queue=None,
        human_gate=None,
        orchestrator=None,
    )

    # Serialize the request
    serialized = handler._serialize_request(request)

    # Verify context field exists (not metadata)
    assert "context" in serialized, "Response should have 'context' field"
    assert "metadata" not in serialized or serialized.get("metadata") is None, (
        "Response should not have 'metadata' field (renamed to 'context')"
    )

    # Verify context summary fields
    context = serialized["context"]
    assert "files_affected" in context, "Context should include files_affected"
    assert "risk_level" in context, "Context should include risk_level"
    assert "estimated_impact" in context, "Context should include estimated_impact"

    # Verify values
    assert context["files_affected"] == 5, "files_affected should match files_changed"
    assert context["risk_level"] == "medium", "HIGH priority should map to medium risk"
    assert context["estimated_impact"] == 4, "HIGH priority should map to impact 4"


def test_risk_level_mapping():
    """Test that risk levels are correctly derived from priority."""
    handler = ApprovalQueueHandler()

    test_cases = [
        (ApprovalPriority.CRITICAL, "high"),
        (ApprovalPriority.HIGH, "medium"),
        (ApprovalPriority.NORMAL, "low"),
        (ApprovalPriority.LOW, "low"),
    ]

    for priority, expected_risk in test_cases:
        request = ApprovalRequest(
            id=f"test-{priority.value}",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            priority=priority,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        serialized = handler._serialize_request(request)
        assert serialized["context"]["risk_level"] == expected_risk, (
            f"Priority {priority.value} should map to risk {expected_risk}"
        )


def test_estimated_impact_mapping():
    """Test that estimated impact is correctly derived from priority."""
    handler = ApprovalQueueHandler()

    test_cases = [
        (ApprovalPriority.CRITICAL, 5),
        (ApprovalPriority.HIGH, 4),
        (ApprovalPriority.NORMAL, 3),
        (ApprovalPriority.LOW, 2),
    ]

    for priority, expected_impact in test_cases:
        request = ApprovalRequest(
            id=f"test-{priority.value}",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            priority=priority,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        serialized = handler._serialize_request(request)
        assert serialized["context"]["estimated_impact"] == expected_impact, (
            f"Priority {priority.value} should map to impact {expected_impact}"
        )


def test_files_affected_defaults_to_zero():
    """Test that files_affected defaults to 0 when not provided."""
    handler = ApprovalQueueHandler()

    request = ApprovalRequest(
        id="test-no-files",
        type=ApprovalType.CONTENT,
        domain="test",
        title="Test",
        description="Test",
        metadata={},  # No files_changed
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(UTC),
    )

    serialized = handler._serialize_request(request)
    assert serialized["context"]["files_affected"] == 0, (
        "files_affected should default to 0 when not provided"
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
