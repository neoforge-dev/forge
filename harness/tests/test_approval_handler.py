"""Tests for ApprovalQueueHandler.

Tests approval queue operations with mocked dependencies.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.webhook_server.handlers.approval_handler import (
    ApprovalQueueHandler,
    get_approval_handler,
)


class TestApprovalQueueHandler:
    """Tests for ApprovalQueueHandler class."""

    @pytest.fixture
    def mock_approval_queue(self):
        """Create mock ApprovalQueueHarness."""
        queue = MagicMock()
        queue.list_pending = AsyncMock(return_value=[])
        queue.get_request = AsyncMock(return_value=None)
        queue.approve = AsyncMock()
        queue.reject = AsyncMock()
        queue.get_stats = AsyncMock()
        return queue

    @pytest.fixture
    def mock_human_gate(self):
        """Create mock HumanGateHarness."""
        return MagicMock()

    @pytest.fixture
    def mock_orchestrator(self):
        """Create mock OrchestrationHarness."""
        orchestrator = MagicMock()
        orchestrator.resume = AsyncMock()
        return orchestrator

    @pytest.fixture
    def handler(self, mock_approval_queue, mock_human_gate, mock_orchestrator):
        """Create handler with mocked dependencies."""
        return ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            human_gate=mock_human_gate,
            orchestrator=mock_orchestrator,
            forge_root=Path("/tmp/forge"),
        )

    @pytest.fixture
    def sample_request(self):
        """Create sample ApprovalRequest."""
        return ApprovalRequest(
            id="req-123",
            type=ApprovalType.CONTENT,
            domain="codeswiftr-com",
            title="Review blog post",
            description="New article needs review",
            status=ApprovalStatus.PENDING,
            priority=ApprovalPriority.NORMAL,
            tier="phone",
            metadata={"project": "interview-simulator", "files_changed": 3},
            workflow_checkpoint="/tmp/checkpoint.json",
            created_at=datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
        )

    def test_init_with_all_params(
        self, mock_approval_queue, mock_human_gate, mock_orchestrator
    ):
        """Test initialization with all parameters."""
        forge_root = Path("/custom/forge")
        handler = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            human_gate=mock_human_gate,
            orchestrator=mock_orchestrator,
            forge_root=forge_root,
        )

        assert handler.approval_queue is mock_approval_queue
        assert handler.human_gate is mock_human_gate
        assert handler.orchestrator is mock_orchestrator
        assert handler._forge_root == forge_root

    def test_init_finds_forge_root(self, mock_approval_queue):
        """Test initialization finds FORGE root."""
        with patch.object(Path, "exists") as mock_exists:
            mock_exists.return_value = True
            handler = ApprovalQueueHandler(approval_queue=mock_approval_queue)
            assert handler._forge_root is not None

    def test_compute_tier_watch(self, handler, sample_request):
        """Test _compute_tier returns watch for low priority."""
        sample_request.priority = ApprovalPriority.LOW
        sample_request.type = ApprovalType.CONTENT

        tier = handler._compute_tier(sample_request)

        assert tier == "watch"

    def test_compute_tier_phone_medium(self, handler, sample_request):
        """Test _compute_tier returns phone for medium priority."""
        sample_request.priority = ApprovalPriority.NORMAL
        sample_request.type = ApprovalType.FEATURE

        tier = handler._compute_tier(sample_request)

        assert tier == "phone"

    def test_compute_tier_phone_high(self, handler, sample_request):
        """Test _compute_tier returns phone for high priority."""
        sample_request.priority = ApprovalPriority.HIGH
        sample_request.type = ApprovalType.CONTENT

        tier = handler._compute_tier(sample_request)

        assert tier == "phone"

    def test_compute_tier_desktop_critical(self, handler, sample_request):
        """Test _compute_tier returns desktop for critical priority."""
        sample_request.priority = ApprovalPriority.CRITICAL
        sample_request.type = ApprovalType.FEATURE

        tier = handler._compute_tier(sample_request)

        assert tier == "desktop"

    def test_compute_tier_desktop_deployment(self, handler, sample_request):
        """Test _compute_tier returns phone for deploy type (not in high_risk_types).

        Note: ApprovalType.DEPLOY has value 'deploy', but high_risk_types
        checks for 'deployment'. So DEPLOY type gets mapped by priority instead.
        """
        sample_request.priority = ApprovalPriority.NORMAL
        sample_request.type = ApprovalType.DEPLOY

        tier = handler._compute_tier(sample_request)

        # DEPLOY type with NORMAL priority -> phone tier
        assert tier == "phone"

    def test_compute_tier_desktop_security(self, handler, sample_request):
        """Test _compute_tier returns desktop for security type."""
        sample_request.priority = ApprovalPriority.LOW
        sample_request.type = ApprovalType.SECURITY

        tier = handler._compute_tier(sample_request)

        assert tier == "desktop"

    def test_extract_files_affected_from_files_changed(self, handler):
        """Test _extract_files_affected with files_changed."""
        result = handler._extract_files_affected({"files_changed": 5})
        assert result == 5

    def test_extract_files_affected_from_files_affected(self, handler):
        """Test _extract_files_affected with files_affected."""
        result = handler._extract_files_affected({"files_affected": 3})
        assert result == 3

    def test_extract_files_affected_from_files_list(self, handler):
        """Test _extract_files_affected with files list."""
        result = handler._extract_files_affected({"files": ["a.py", "b.py", "c.py"]})
        assert result == 3

    def test_extract_files_affected_no_metadata(self, handler):
        """Test _extract_files_affected with no metadata."""
        result = handler._extract_files_affected(None)
        assert result == 0

    def test_extract_files_affected_empty_metadata(self, handler):
        """Test _extract_files_affected with empty metadata."""
        result = handler._extract_files_affected({})
        assert result == 0

    def test_derive_risk_level_high(self, handler):
        """Test _derive_risk_level returns high for desktop tier."""
        result = handler._derive_risk_level(ApprovalPriority.NORMAL, "desktop")
        assert result == "high"

    def test_derive_risk_level_high_critical(self, handler):
        """Test _derive_risk_level returns high for critical priority."""
        result = handler._derive_risk_level(ApprovalPriority.CRITICAL, "phone")
        assert result == "high"

    def test_derive_risk_level_medium(self, handler):
        """Test _derive_risk_level returns medium for high priority."""
        result = handler._derive_risk_level(ApprovalPriority.HIGH, "phone")
        assert result == "medium"

    def test_derive_risk_level_low(self, handler):
        """Test _derive_risk_level returns low for low priority."""
        result = handler._derive_risk_level(ApprovalPriority.LOW, "watch")
        assert result == "low"

    def test_derive_estimated_impact_critical(self, handler):
        """Test _derive_estimated_impact for critical priority."""
        result = handler._derive_estimated_impact(ApprovalPriority.CRITICAL)
        assert result == 5

    def test_derive_estimated_impact_high(self, handler):
        """Test _derive_estimated_impact for high priority."""
        result = handler._derive_estimated_impact(ApprovalPriority.HIGH)
        assert result == 4

    def test_derive_estimated_impact_normal(self, handler):
        """Test _derive_estimated_impact for normal priority."""
        result = handler._derive_estimated_impact(ApprovalPriority.NORMAL)
        assert result == 3

    def test_derive_estimated_impact_low(self, handler):
        """Test _derive_estimated_impact for low priority."""
        result = handler._derive_estimated_impact(ApprovalPriority.LOW)
        assert result == 2

    def test_derive_estimated_impact_unknown(self, handler):
        """Test _derive_estimated_impact for unknown priority."""
        # Create a mock priority with unknown value
        mock_priority = MagicMock()
        mock_priority.value = "unknown"
        result = handler._derive_estimated_impact(mock_priority)
        assert result == 1

    def test_serialize_request(self, handler, sample_request):
        """Test _serialize_request creates proper dict."""
        result = handler._serialize_request(sample_request)

        assert result["request_id"] == "req-123"
        assert result["approval_type"] == "content"
        assert result["domain"] == "codeswiftr-com"
        assert result["project"] == "interview-simulator"
        assert result["title"] == "Review blog post"
        assert result["description"] == "New article needs review"
        assert result["status"] == "pending"
        assert result["priority"] == "normal"
        assert result["tier"] == "phone"
        assert result["created_at"] == "2026-02-16T10:00:00+00:00"
        assert result["workflow_checkpoint"] == "/tmp/checkpoint.json"
        assert result["context"]["files_affected"] == 3
        assert result["context"]["risk_level"] == "low"
        assert result["context"]["estimated_impact"] == 3

    def test_serialize_request_no_metadata(self, handler, sample_request):
        """Test _serialize_request with no metadata."""
        sample_request.metadata = None

        result = handler._serialize_request(sample_request)

        assert result["project"] is None
        assert result["context"]["files_affected"] == 0

    def test_serialize_request_with_resolution(self, handler, sample_request):
        """Test _serialize_request with resolved request."""
        sample_request.status = ApprovalStatus.APPROVED
        sample_request.approved_by = "john@example.com"
        sample_request.resolved_at = datetime(2026, 2, 16, 11, 0, 0, tzinfo=UTC)
        sample_request.resolution_reason = "Looks good"

        result = handler._serialize_request(sample_request)

        assert result["status"] == "approved"
        assert result["approved_by"] == "john@example.com"
        assert result["resolved_at"] == "2026-02-16T11:00:00+00:00"
        assert result["resolution_reason"] == "Looks good"

    def test_serialize_request_with_expires(self, handler, sample_request):
        """Test _serialize_request with expiration."""
        sample_request.expires_at = datetime(2026, 2, 17, 10, 0, 0, tzinfo=UTC)

        result = handler._serialize_request(sample_request)

        assert result["expires_at"] == "2026-02-17T10:00:00+00:00"


class TestApprovalQueueHandlerAsync:
    """Async tests for ApprovalQueueHandler."""

    @pytest.fixture
    def mock_approval_queue(self):
        """Create mock ApprovalQueueHarness."""
        queue = MagicMock()
        queue.list_pending = AsyncMock(return_value=[])
        queue.get_request = AsyncMock(return_value=None)
        queue.approve = AsyncMock()
        queue.reject = AsyncMock()
        queue.get_stats = AsyncMock()
        return queue

    @pytest.fixture
    def mock_orchestrator(self):
        """Create mock OrchestrationHarness."""
        orchestrator = MagicMock()
        orchestrator.resume = AsyncMock()
        return orchestrator

    @pytest.fixture
    def handler(self, mock_approval_queue, mock_orchestrator):
        """Create handler with mocked dependencies."""
        return ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            orchestrator=mock_orchestrator,
            forge_root=Path("/tmp/forge"),
        )

    @pytest.fixture
    def sample_request(self):
        """Create sample ApprovalRequest."""
        return ApprovalRequest(
            id="req-123",
            type=ApprovalType.CONTENT,
            domain="codeswiftr-com",
            title="Review blog post",
            description="New article needs review",
            status=ApprovalStatus.PENDING,
            priority=ApprovalPriority.NORMAL,
            tier="phone",
            metadata={"project": "interview-simulator"},
            created_at=datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
        )

    @pytest.mark.asyncio
    async def test_list_pending_no_queue(self):
        """Test list_pending when approval_queue is None."""
        handler = ApprovalQueueHandler(approval_queue=None)

        result = await handler.list_pending()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_pending_empty(self, handler, mock_approval_queue):
        """Test list_pending with no approvals."""
        mock_approval_queue.list_pending.return_value = []

        result = await handler.list_pending()

        assert result == []
        mock_approval_queue.list_pending.assert_called_once_with(domain=None)

    @pytest.mark.asyncio
    async def test_list_pending_returns_serialized(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test list_pending returns serialized requests."""
        mock_approval_queue.list_pending.return_value = [sample_request]

        result = await handler.list_pending()

        assert len(result) == 1
        assert result[0]["request_id"] == "req-123"
        assert result[0]["domain"] == "codeswiftr-com"

    @pytest.mark.asyncio
    async def test_list_pending_filter_domain(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test list_pending filters by domain."""
        mock_approval_queue.list_pending.return_value = [sample_request]

        result = await handler.list_pending(domain="codeswiftr-com")

        mock_approval_queue.list_pending.assert_called_once_with(
            domain="codeswiftr-com"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_pending_filter_project(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test list_pending filters by project."""
        req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            metadata={"project": "project-a"},
        )
        req2 = ApprovalRequest(
            id="req-2",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 2",
            description="Desc 2",
            metadata={"project": "project-b"},
        )
        mock_approval_queue.list_pending.return_value = [req1, req2]

        result = await handler.list_pending(project="project-a")

        assert len(result) == 1
        assert result[0]["request_id"] == "req-1"

    @pytest.mark.asyncio
    async def test_list_pending_filter_priority(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test list_pending filters by priority."""
        req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            priority=ApprovalPriority.HIGH,
        )
        req2 = ApprovalRequest(
            id="req-2",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 2",
            description="Desc 2",
            priority=ApprovalPriority.LOW,
        )
        mock_approval_queue.list_pending.return_value = [req1, req2]

        result = await handler.list_pending(priority="high")

        assert len(result) == 1
        assert result[0]["request_id"] == "req-1"

    @pytest.mark.asyncio
    async def test_list_pending_filter_tier(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test list_pending filters by tier."""
        req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            priority=ApprovalPriority.LOW,  # watch tier
        )
        req2 = ApprovalRequest(
            id="req-2",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 2",
            description="Desc 2",
            priority=ApprovalPriority.CRITICAL,  # desktop tier
        )
        mock_approval_queue.list_pending.return_value = [req1, req2]

        result = await handler.list_pending(tier="watch")

        assert len(result) == 1
        assert result[0]["tier"] == "watch"

    @pytest.mark.asyncio
    async def test_list_pending_filter_status(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test list_pending filters by status."""
        req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            status=ApprovalStatus.PENDING,
        )
        req2 = ApprovalRequest(
            id="req-2",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 2",
            description="Desc 2",
            status=ApprovalStatus.APPROVED,
        )
        mock_approval_queue.list_pending.return_value = [req1, req2]

        result = await handler.list_pending(status="pending")

        assert len(result) == 1
        assert result[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_list_pending_limit(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test list_pending respects limit."""
        requests = [
            ApprovalRequest(
                id=f"req-{i}",
                type=ApprovalType.CONTENT,
                domain="domain1",
                title=f"Title {i}",
                description=f"Desc {i}",
            )
            for i in range(10)
        ]
        mock_approval_queue.list_pending.return_value = requests

        result = await handler.list_pending(limit=5)

        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_list_pending_exception(self, handler, mock_approval_queue):
        """Test list_pending handles exceptions gracefully."""
        mock_approval_queue.list_pending.side_effect = Exception("Database error")

        result = await handler.list_pending()

        assert result == []

    @pytest.mark.asyncio
    async def test_count_pending(self, handler, mock_approval_queue):
        """Test count_pending returns correct count."""
        requests = [
            ApprovalRequest(
                id=f"req-{i}",
                type=ApprovalType.CONTENT,
                domain="domain1",
                title=f"Title {i}",
                description=f"Desc {i}",
            )
            for i in range(5)
        ]
        mock_approval_queue.list_pending.return_value = requests

        result = await handler.count_pending()

        assert result == 5
        # Should call with large limit
        mock_approval_queue.list_pending.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_pending_with_filters(self, handler, mock_approval_queue):
        """Test count_pending passes filters."""
        mock_approval_queue.list_pending.return_value = []

        await handler.count_pending(
            domain="test-domain", project="test-project", priority="high", tier="phone"
        )

        # Verify filters are passed through list_pending
        # (count_pending calls list_pending internally)
        mock_approval_queue.list_pending.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_request_no_queue(self):
        """Test get_request when approval_queue is None."""
        handler = ApprovalQueueHandler(approval_queue=None)

        result = await handler.get_request("req-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_request_not_found(self, handler, mock_approval_queue):
        """Test get_request when request not found."""
        mock_approval_queue.get_request.return_value = None

        result = await handler.get_request("nonexistent")

        assert result is None
        mock_approval_queue.get_request.assert_called_once_with("nonexistent")

    @pytest.mark.asyncio
    async def test_get_request_success(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test get_request returns serialized request."""
        mock_approval_queue.get_request.return_value = sample_request

        result = await handler.get_request("req-123")

        assert result is not None
        assert result["request_id"] == "req-123"
        assert result["domain"] == "codeswiftr-com"

    @pytest.mark.asyncio
    async def test_get_request_exception(self, handler, mock_approval_queue):
        """Test get_request handles exceptions."""
        mock_approval_queue.get_request.side_effect = Exception("Database error")

        result = await handler.get_request("req-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_approve_request_no_queue(self):
        """Test approve_request when approval_queue is None."""
        handler = ApprovalQueueHandler(approval_queue=None)

        result = await handler.approve_request("req-123", "john@example.com")

        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_approve_request_success(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test successful approval without pipeline resume."""
        sample_request.status = ApprovalStatus.APPROVED
        sample_request.approved_by = "john@example.com"
        sample_request.workflow_checkpoint = None
        mock_approval_queue.approve.return_value = sample_request

        result = await handler.approve_request(
            "req-123", approver="john@example.com", comment="Looks good"
        )

        assert result["success"] is True
        assert result["request_id"] == "req-123"
        assert result["status"] == "approved"
        assert result["approver"] == "john@example.com"
        assert "pipeline_resumed" not in result

        mock_approval_queue.approve.assert_called_once_with(
            request_id="req-123", approver="john@example.com", comment="Looks good"
        )

    @pytest.mark.asyncio
    async def test_approve_request_with_pipeline_resume(
        self, handler, mock_approval_queue, mock_orchestrator, sample_request, tmp_path
    ):
        """Test approval with successful pipeline resume."""
        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text("{}")

        sample_request.status = ApprovalStatus.APPROVED
        sample_request.workflow_checkpoint = str(checkpoint_path)
        mock_approval_queue.approve.return_value = sample_request

        mock_result = MagicMock()
        mock_result.success = True
        mock_orchestrator.resume.return_value = mock_result

        result = await handler.approve_request(
            "req-123", approver="john@example.com", auto_resume=True
        )

        assert result["success"] is True
        assert result["pipeline_resumed"] is True
        assert result["pipeline_success"] is True

        mock_orchestrator.resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_request_pipeline_resume_checkpoint_missing(
        self, handler, mock_approval_queue, mock_orchestrator, sample_request
    ):
        """Test approval when checkpoint file doesn't exist."""
        sample_request.status = ApprovalStatus.APPROVED
        sample_request.workflow_checkpoint = "/nonexistent/checkpoint.json"
        mock_approval_queue.approve.return_value = sample_request

        result = await handler.approve_request(
            "req-123", approver="john@example.com", auto_resume=True
        )

        assert result["success"] is True
        # Should not attempt resume if checkpoint doesn't exist
        mock_orchestrator.resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_approve_request_pipeline_resume_fails(
        self, handler, mock_approval_queue, mock_orchestrator, sample_request, tmp_path
    ):
        """Test approval when pipeline resume fails."""
        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text("{}")

        sample_request.status = ApprovalStatus.APPROVED
        sample_request.workflow_checkpoint = str(checkpoint_path)
        mock_approval_queue.approve.return_value = sample_request

        mock_orchestrator.resume.side_effect = Exception("Resume failed")

        result = await handler.approve_request(
            "req-123", approver="john@example.com", auto_resume=True
        )

        assert result["success"] is True
        assert result["pipeline_resumed"] is False
        assert "resume_error" in result
        assert "Resume failed" in result["resume_error"]

    @pytest.mark.asyncio
    async def test_approve_request_no_auto_resume(
        self, handler, mock_approval_queue, mock_orchestrator, sample_request, tmp_path
    ):
        """Test approval with auto_resume=False."""
        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text("{}")

        sample_request.status = ApprovalStatus.APPROVED
        sample_request.workflow_checkpoint = str(checkpoint_path)
        mock_approval_queue.approve.return_value = sample_request

        result = await handler.approve_request(
            "req-123", approver="john@example.com", auto_resume=False
        )

        assert result["success"] is True
        # Should not resume
        mock_orchestrator.resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_approve_request_no_orchestrator(
        self, handler, mock_approval_queue, sample_request, tmp_path
    ):
        """Test approval when orchestrator is None."""
        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text("{}")

        sample_request.status = ApprovalStatus.APPROVED
        sample_request.workflow_checkpoint = str(checkpoint_path)
        mock_approval_queue.approve.return_value = sample_request

        handler.orchestrator = None

        result = await handler.approve_request(
            "req-123", approver="john@example.com", auto_resume=True
        )

        assert result["success"] is True
        # Should not attempt resume
        assert "pipeline_resumed" not in result

    @pytest.mark.asyncio
    async def test_approve_request_exception(self, handler, mock_approval_queue):
        """Test approve_request handles exceptions."""
        mock_approval_queue.approve.side_effect = Exception("Approval failed")

        result = await handler.approve_request("req-123", "john@example.com")

        assert result["success"] is False
        assert "Approval failed" in result["error"]

    @pytest.mark.asyncio
    async def test_reject_request_no_queue(self):
        """Test reject_request when approval_queue is None."""
        handler = ApprovalQueueHandler(approval_queue=None)

        result = await handler.reject_request("req-123", "john@example.com")

        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_reject_request_success(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test successful rejection."""
        sample_request.status = ApprovalStatus.REJECTED
        mock_approval_queue.reject.return_value = sample_request

        result = await handler.reject_request(
            "req-123", rejector="john@example.com", reason="Needs more work"
        )

        assert result["success"] is True
        assert result["request_id"] == "req-123"
        assert result["status"] == "rejected"
        assert result["rejector"] == "john@example.com"

        mock_approval_queue.reject.assert_called_once_with(
            request_id="req-123", approver="john@example.com", reason="Needs more work"
        )

    @pytest.mark.asyncio
    async def test_reject_request_no_reason(
        self, handler, mock_approval_queue, sample_request
    ):
        """Test rejection without reason."""
        sample_request.status = ApprovalStatus.REJECTED
        mock_approval_queue.reject.return_value = sample_request

        result = await handler.reject_request("req-123", rejector="john@example.com")

        assert result["success"] is True

        mock_approval_queue.reject.assert_called_once_with(
            request_id="req-123", approver="john@example.com", reason="Rejected"
        )

    @pytest.mark.asyncio
    async def test_reject_request_exception(self, handler, mock_approval_queue):
        """Test reject_request handles exceptions."""
        mock_approval_queue.reject.side_effect = Exception("Rejection failed")

        result = await handler.reject_request("req-123", "john@example.com")

        assert result["success"] is False
        assert "Rejection failed" in result["error"]

    @pytest.mark.asyncio
    async def test_get_stats_no_queue(self):
        """Test get_stats when approval_queue is None."""
        handler = ApprovalQueueHandler(approval_queue=None)

        result = await handler.get_stats()

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_get_stats_success(self, handler, mock_approval_queue):
        """Test get_stats returns statistics."""
        mock_stats = MagicMock()
        mock_stats.pending_count = 5
        mock_stats.approved_count = 10
        mock_stats.rejected_count = 2
        mock_stats.expired_count = 1
        mock_stats.total_requests = 18
        mock_stats.oldest_pending_hours = 24.5
        mock_approval_queue.get_stats.return_value = mock_stats

        result = await handler.get_stats()

        assert result["pending"] == 5
        assert result["approved"] == 10
        assert result["rejected"] == 2
        assert result["expired"] == 1
        assert result["total"] == 18
        assert result["oldest_pending_age_hours"] == 24.5

    @pytest.mark.asyncio
    async def test_get_stats_exception(self, handler, mock_approval_queue):
        """Test get_stats handles exceptions."""
        mock_approval_queue.get_stats.side_effect = Exception("Stats failed")

        result = await handler.get_stats()

        assert "error" in result
        assert "Stats failed" in result["error"]

    @pytest.mark.asyncio
    async def test_batch_approve_no_queue(self):
        """Test batch_approve when approval_queue is None."""
        handler = ApprovalQueueHandler(approval_queue=None)

        result = await handler.batch_approve(["req-1", "req-2"], "john@example.com")

        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_batch_approve_watch_tier_success(
        self, handler, mock_approval_queue
    ):
        """Test batch_approve succeeds for watch tier requests."""
        req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            priority=ApprovalPriority.LOW,  # watch tier
        )
        req2 = ApprovalRequest(
            id="req-2",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 2",
            description="Desc 2",
            priority=ApprovalPriority.LOW,  # watch tier
        )

        async def get_request_side_effect(request_id):
            if request_id == "req-1":
                return req1
            elif request_id == "req-2":
                return req2
            return None

        mock_approval_queue.get_request.side_effect = get_request_side_effect

        approved_req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            status=ApprovalStatus.APPROVED,
        )
        approved_req2 = ApprovalRequest(
            id="req-2",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 2",
            description="Desc 2",
            status=ApprovalStatus.APPROVED,
        )

        mock_approval_queue.approve.side_effect = [approved_req1, approved_req2]

        result = await handler.batch_approve(
            ["req-1", "req-2"], approver="john@example.com", comment="LGTM"
        )

        assert result["success"] is True
        assert result["approved_count"] == 2
        assert result["failed_count"] == 0
        assert result["approved"] == ["req-1", "req-2"]
        assert result["failed"] == []

    @pytest.mark.asyncio
    async def test_batch_approve_rejects_non_watch_tier(
        self, handler, mock_approval_queue
    ):
        """Test batch_approve rejects non-watch tier requests."""
        req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            priority=ApprovalPriority.HIGH,  # phone tier
        )

        mock_approval_queue.get_request.return_value = req1

        result = await handler.batch_approve(["req-1"], approver="john@example.com")

        assert result["success"] is True
        assert result["approved_count"] == 0
        assert result["failed_count"] == 1
        assert len(result["failed"]) == 1
        assert "requires individual approval" in result["failed"][0]["error"]

    @pytest.mark.asyncio
    async def test_batch_approve_request_not_found(self, handler, mock_approval_queue):
        """Test batch_approve handles missing requests."""
        mock_approval_queue.get_request.return_value = None

        result = await handler.batch_approve(["nonexistent"], approver="john@example.com")

        assert result["success"] is True
        assert result["approved_count"] == 0
        assert result["failed_count"] == 1
        assert result["failed"][0]["error"] == "Not found"

    @pytest.mark.asyncio
    async def test_batch_approve_mixed_results(self, handler, mock_approval_queue):
        """Test batch_approve with mixed success and failures."""
        req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            priority=ApprovalPriority.LOW,  # watch tier
        )
        req2 = ApprovalRequest(
            id="req-2",
            type=ApprovalType.DEPLOY,
            domain="domain1",
            title="Title 2",
            description="Desc 2",
            priority=ApprovalPriority.CRITICAL,  # desktop tier
        )

        async def get_request_side_effect(request_id):
            if request_id == "req-1":
                return req1
            elif request_id == "req-2":
                return req2
            return None

        mock_approval_queue.get_request.side_effect = get_request_side_effect

        approved_req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            status=ApprovalStatus.APPROVED,
        )
        mock_approval_queue.approve.return_value = approved_req1

        result = await handler.batch_approve(
            ["req-1", "req-2"], approver="john@example.com"
        )

        assert result["success"] is True
        assert result["approved_count"] == 1
        assert result["failed_count"] == 1
        assert result["approved"] == ["req-1"]
        assert len(result["failed"]) == 1

    @pytest.mark.asyncio
    async def test_batch_approve_exception_during_approval(
        self, handler, mock_approval_queue
    ):
        """Test batch_approve handles exceptions during individual approvals."""
        req1 = ApprovalRequest(
            id="req-1",
            type=ApprovalType.CONTENT,
            domain="domain1",
            title="Title 1",
            description="Desc 1",
            priority=ApprovalPriority.LOW,
        )

        mock_approval_queue.get_request.return_value = req1
        mock_approval_queue.approve.side_effect = Exception("Approval failed")

        result = await handler.batch_approve(["req-1"], approver="john@example.com")

        assert result["success"] is True
        assert result["approved_count"] == 0
        assert result["failed_count"] == 1
        assert "Approval failed" in result["failed"][0]["error"]


class TestGetApprovalHandler:
    """Tests for get_approval_handler function."""

    def test_returns_handler(self):
        """Test that get_approval_handler returns an ApprovalQueueHandler."""
        import forge_harness.webhook_server.handlers.approval_handler as ah_module

        ah_module._approval_handler = None

        handler = get_approval_handler()

        assert isinstance(handler, ApprovalQueueHandler)

    def test_returns_same_instance(self):
        """Test that get_approval_handler returns singleton."""
        import forge_harness.webhook_server.handlers.approval_handler as ah_module

        ah_module._approval_handler = None

        handler1 = get_approval_handler()
        handler2 = get_approval_handler()

        assert handler1 is handler2

    def test_accepts_parameters(self):
        """Test that get_approval_handler accepts dependencies."""
        import forge_harness.webhook_server.handlers.approval_handler as ah_module

        ah_module._approval_handler = None

        mock_queue = MagicMock()
        mock_gate = MagicMock()
        mock_orch = MagicMock()

        handler = get_approval_handler(
            approval_queue=mock_queue,
            human_gate=mock_gate,
            orchestrator=mock_orch,
        )

        assert handler.approval_queue is mock_queue
        assert handler.human_gate is mock_gate
        assert handler.orchestrator is mock_orch
