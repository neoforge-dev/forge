"""
Verification test for CC-P2-009: Approval Context Summary

Tests that approval cards show context summary (files_affected, risk_level, estimated_impact)
without needing to expand full details.
"""

from datetime import UTC, datetime

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.webhook_server import ApprovalQueueHandler


@pytest.fixture
def mock_approval_request():
    """Create a mock approval request with metadata."""
    return ApprovalRequest(
        id="test-approval-001",
        type=ApprovalType.FEATURE,
        domain="codeswiftr-com",
        title="Test Feature Approval",
        description="Test feature requiring approval",
        metadata={
            "files_changed": 5,
            "project": "interview-simulator",
        },
        status=ApprovalStatus.PENDING,
        priority=ApprovalPriority.HIGH,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def approval_handler():
    """Create ApprovalQueueHandler instance."""
    return ApprovalQueueHandler(
        approval_queue=None,
        human_gate=None,
        orchestrator=None,
    )


def test_serialize_request_includes_context_summary(approval_handler, mock_approval_request):
    """Test that _serialize_request enriches metadata with context summary fields."""
    serialized = approval_handler._serialize_request(mock_approval_request)

    # Verify basic fields
    assert serialized["request_id"] == "test-approval-001"
    assert serialized["title"] == "Test Feature Approval"
    assert serialized["priority"] == "high"

    # Verify context summary fields in metadata
    metadata = serialized["metadata"]
    assert "files_affected" in metadata
    assert "risk_level" in metadata
    assert "estimated_impact" in metadata


def test_files_affected_count(approval_handler, mock_approval_request):
    """Test that files_affected is derived from files_changed."""
    serialized = approval_handler._serialize_request(mock_approval_request)
    metadata = serialized["metadata"]

    # Should use files_changed value
    assert metadata["files_affected"] == 5


def test_risk_level_derivation(approval_handler):
    """Test risk_level derivation from tier and priority."""
    # Critical priority -> high risk
    request = ApprovalRequest(
        id="test-001",
        type=ApprovalType.SECURITY,
        domain="test",
        title="Critical Security",
        description="Test",
        priority=ApprovalPriority.CRITICAL,
        created_at=datetime.now(UTC),
    )
    serialized = approval_handler._serialize_request(request)
    assert serialized["metadata"]["risk_level"] == "high"

    # High priority -> medium risk
    request.priority = ApprovalPriority.HIGH
    serialized = approval_handler._serialize_request(request)
    assert serialized["metadata"]["risk_level"] == "medium"

    # Low priority -> low risk
    request.priority = ApprovalPriority.LOW
    request.type = ApprovalType.CONTENT
    serialized = approval_handler._serialize_request(request)
    assert serialized["metadata"]["risk_level"] == "low"


def test_estimated_impact_scale(approval_handler):
    """Test estimated_impact is on 1-5 scale based on priority."""
    test_cases = [
        (ApprovalPriority.CRITICAL, 5),
        (ApprovalPriority.HIGH, 4),
        (ApprovalPriority.NORMAL, 3),
        (ApprovalPriority.LOW, 2),
    ]

    for priority, expected_impact in test_cases:
        request = ApprovalRequest(
            id="test-001",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            priority=priority,
            created_at=datetime.now(UTC),
        )
        serialized = approval_handler._serialize_request(request)
        assert serialized["metadata"]["estimated_impact"] == expected_impact


def test_context_fields_not_overwritten(approval_handler):
    """Test that existing context fields are preserved."""
    request = ApprovalRequest(
        id="test-001",
        type=ApprovalType.FEATURE,
        domain="test",
        title="Test",
        description="Test",
        metadata={
            "files_affected": 10,  # Pre-existing value
            "risk_level": "high",  # Pre-existing value
            "estimated_impact": 5,  # Pre-existing value
            "custom_field": "custom_value",
        },
        priority=ApprovalPriority.LOW,
        created_at=datetime.now(UTC),
    )
    serialized = approval_handler._serialize_request(request)
    metadata = serialized["metadata"]

    # Should preserve existing values
    assert metadata["files_affected"] == 10
    assert metadata["risk_level"] == "high"
    assert metadata["estimated_impact"] == 5
    assert metadata["custom_field"] == "custom_value"


def test_context_summary_visible_without_expand(approval_handler, mock_approval_request):
    """
    Test that context summary is visible in list response.
    This ensures operators can see key info without opening details.
    """
    serialized = approval_handler._serialize_request(mock_approval_request)

    # All context fields should be in top-level metadata (no nested expansion needed)
    metadata = serialized["metadata"]

    # Files affected count
    assert isinstance(metadata["files_affected"], int)
    assert metadata["files_affected"] >= 0

    # Risk level badge
    assert metadata["risk_level"] in ["low", "medium", "high", "critical"]

    # Estimated impact (1-5 scale)
    assert isinstance(metadata["estimated_impact"], int)
    assert 1 <= metadata["estimated_impact"] <= 5


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
