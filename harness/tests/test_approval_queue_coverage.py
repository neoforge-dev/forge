"""
Coverage Tests for Approval Queue Module
========================================

Tests for forge_harness/approval_queue.py to improve coverage.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalQueueHarness,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStorage,
    ApprovalType,
    JSONFileStorage,
    auto_classify_tier,
    create_approval_queue,
    create_approval_queue_from_env,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_storage_dir(tmp_path: Path) -> Path:
    """Create a temporary storage directory."""
    storage_dir = tmp_path / "approvals"
    storage_dir.mkdir()
    return storage_dir


@pytest.fixture
def json_storage(temp_storage_dir: Path) -> JSONFileStorage:
    """Create a JSONFileStorage instance."""
    return JSONFileStorage(temp_storage_dir)


@pytest.fixture
def approval_queue(json_storage: JSONFileStorage) -> ApprovalQueueHarness:
    """Create an ApprovalQueueHarness instance."""
    return ApprovalQueueHarness(
        storage=json_storage,
        default_expiry_hours=24.0,
    )


# =============================================================================
# Tests for ApprovalRequest Model
# =============================================================================


class TestApprovalRequest:
    """Tests for ApprovalRequest model."""

    def test_approval_request_creation(self) -> None:
        """Test basic ApprovalRequest creation."""
        request = ApprovalRequest(
            id="test_001",
            type=ApprovalType.CONTENT,
            domain="codeswiftr-com",
            title="Test Approval",
            description="Test description",
        )
        assert request.id == "test_001"
        assert request.type == ApprovalType.CONTENT
        assert request.status == ApprovalStatus.PENDING
        assert request.priority == ApprovalPriority.NORMAL
        assert request.tier == "PHONE"
        assert request.risk_score == 0.5

    def test_to_dict(self) -> None:
        """Test converting ApprovalRequest to dict."""
        request = ApprovalRequest(
            id="test_001",
            type=ApprovalType.DEPLOY,
            domain="test-domain",
            title="Test",
            description="Description",
            priority=ApprovalPriority.HIGH,
            tier="DESKTOP",
            risk_score=0.8,
        )
        data = request.to_dict()
        assert data["id"] == "test_001"
        assert data["type"] == "deploy"
        assert data["priority"] == "high"
        assert data["tier"] == "DESKTOP"
        assert data["risk_score"] == 0.8
        assert "created_at" in data

    def test_from_dict(self) -> None:
        """Test creating ApprovalRequest from dict."""
        data = {
            "id": "test_001",
            "type": "content",
            "domain": "test-domain",
            "title": "Test",
            "description": "Description",
            "status": "pending",
            "priority": "normal",
            "tier": "WATCH",
            "risk_score": 0.2,
            "created_at": "2025-01-01T00:00:00+00:00",
            "metadata": {},
            "notification_ids": [],
            "tags": [],
        }
        request = ApprovalRequest.from_dict(data)
        assert request.id == "test_001"
        assert request.type == ApprovalType.CONTENT
        assert request.status == ApprovalStatus.PENDING
        assert request.priority == ApprovalPriority.NORMAL

    def test_is_pending(self) -> None:
        """Test is_pending property."""
        request = ApprovalRequest(
            id="test",
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        assert request.is_pending is True
        request.status = ApprovalStatus.APPROVED
        assert request.is_pending is False

    def test_is_expired(self) -> None:
        """Test is_expired property."""
        # Not expired - no expiry set
        request = ApprovalRequest(
            id="test",
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
        )
        assert request.is_expired is False

        # Expired
        request.expires_at = datetime.now(UTC) - timedelta(hours=1)
        assert request.is_expired is True

        # Not expired yet
        request.expires_at = datetime.now(UTC) + timedelta(hours=1)
        assert request.is_expired is False

    def test_age_hours(self) -> None:
        """Test age_hours property."""
        request = ApprovalRequest(
            id="test",
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )
        assert 1.9 <= request.age_hours <= 2.1


# =============================================================================
# Tests for auto_classify_tier
# =============================================================================


class TestAutoClassifyTier:
    """Tests for auto_classify_tier function."""

    def test_security_always_desktop(self) -> None:
        """Test that SECURITY type always gets DESKTOP tier."""
        tier, risk = auto_classify_tier(
            ApprovalType.SECURITY,
            ApprovalPriority.NORMAL,
            {},
        )
        assert tier == "DESKTOP"
        assert risk >= 0.8

    def test_compliance_always_desktop(self) -> None:
        """Test that COMPLIANCE type always gets DESKTOP tier."""
        tier, risk = auto_classify_tier(
            ApprovalType.COMPLIANCE,
            ApprovalPriority.NORMAL,
            {},
        )
        assert tier == "DESKTOP"
        assert risk >= 0.8

    def test_deploy_high_risk(self) -> None:
        """Test that DEPLOY gets high risk."""
        tier, risk = auto_classify_tier(
            ApprovalType.DEPLOY,
            ApprovalPriority.NORMAL,
            {},
        )
        assert tier == "DESKTOP"
        assert risk >= 0.7

    def test_content_low_risk(self) -> None:
        """Test that CONTENT gets lower risk."""
        tier, risk = auto_classify_tier(
            ApprovalType.CONTENT,
            ApprovalPriority.NORMAL,
            {"quality_score": 95},
        )
        assert tier in ("WATCH", "PHONE")

    def test_content_high_quality_watch(self) -> None:
        """Test that high quality content can be WATCH tier."""
        tier, risk = auto_classify_tier(
            ApprovalType.CONTENT,
            ApprovalPriority.LOW,
            {"quality_score": 95},
        )
        assert tier == "WATCH"

    def test_priority_modifiers(self) -> None:
        """Test priority affects risk score."""
        _, risk_normal = auto_classify_tier(
            ApprovalType.CONTENT,
            ApprovalPriority.NORMAL,
            {},
        )
        _, risk_critical = auto_classify_tier(
            ApprovalType.CONTENT,
            ApprovalPriority.CRITICAL,
            {},
        )
        assert risk_critical > risk_normal

    def test_lines_changed_modifier(self) -> None:
        """Test lines_changed affects risk."""
        _, risk_small = auto_classify_tier(
            ApprovalType.CONTENT,
            ApprovalPriority.NORMAL,
            {"lines_changed": 50},
        )
        _, risk_large = auto_classify_tier(
            ApprovalType.CONTENT,
            ApprovalPriority.NORMAL,
            {"lines_changed": 600},
        )
        assert risk_large > risk_small

    def test_production_environment(self) -> None:
        """Test production environment increases risk."""
        _, risk_dev = auto_classify_tier(
            ApprovalType.CONFIG,
            ApprovalPriority.NORMAL,
            {"environment": "development"},
        )
        _, risk_prod = auto_classify_tier(
            ApprovalType.CONFIG,
            ApprovalPriority.NORMAL,
            {"environment": "production"},
        )
        assert risk_prod > risk_dev

    def test_test_coverage_modifier(self) -> None:
        """Test test_coverage affects risk for features."""
        _, risk_low_coverage = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.NORMAL,
            {"test_coverage": 50},
        )
        _, risk_high_coverage = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.NORMAL,
            {"test_coverage": 95},
        )
        assert risk_low_coverage > risk_high_coverage


# =============================================================================
# Tests for JSONFileStorage
# =============================================================================


class TestJSONFileStorage:
    """Tests for JSONFileStorage."""

    async def test_save_and_load(self, json_storage: JSONFileStorage) -> None:
        """Test saving and loading a request."""
        request = ApprovalRequest(
            id="test_001",
            type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Test",
            description="Test description",
        )
        await json_storage.save(request)

        loaded = await json_storage.load("test_001")
        assert loaded is not None
        assert loaded.id == request.id
        assert loaded.title == request.title

    async def test_load_nonexistent(self, json_storage: JSONFileStorage) -> None:
        """Test loading a nonexistent request."""
        result = await json_storage.load("nonexistent")
        assert result is None

    async def test_list_all(self, json_storage: JSONFileStorage) -> None:
        """Test listing all requests."""
        # Create multiple requests
        for i in range(3):
            request = ApprovalRequest(
                id=f"test_{i:03d}",
                type=ApprovalType.CONTENT,
                domain="test-domain",
                title=f"Test {i}",
                description=f"Description {i}",
            )
            await json_storage.save(request)

        requests = await json_storage.list_all()
        assert len(requests) == 3
        # Should be sorted newest first
        assert requests[0].id == "test_002"

    async def test_delete(self, json_storage: JSONFileStorage) -> None:
        """Test deleting a request."""
        request = ApprovalRequest(
            id="test_001",
            type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Test",
            description="Test",
        )
        await json_storage.save(request)

        result = await json_storage.delete("test_001")
        assert result is True

        loaded = await json_storage.load("test_001")
        assert loaded is None

    async def test_delete_nonexistent(self, json_storage: JSONFileStorage) -> None:
        """Test deleting a nonexistent request."""
        result = await json_storage.delete("nonexistent")
        assert result is False


# =============================================================================
# Tests for ApprovalQueueHarness
# =============================================================================


class TestApprovalQueueHarness:
    """Tests for ApprovalQueueHarness."""

    async def test_create_request(self, approval_queue: ApprovalQueueHarness) -> None:
        """Test creating an approval request."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="codeswiftr-com",
            title="Test Content",
            description="Test description",
            priority=ApprovalPriority.HIGH,
        )
        assert request.id.startswith("apr_")
        assert request.type == ApprovalType.CONTENT
        assert request.priority == ApprovalPriority.HIGH

    async def test_create_request_with_custom_tier(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test creating request with custom tier."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
            tier="WATCH",
            risk_score=0.1,
        )
        assert request.tier == "WATCH"
        assert request.risk_score == 0.1

    async def test_approve(self, approval_queue: ApprovalQueueHarness) -> None:
        """Test approving a request."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
        )
        approved = await approval_queue.approve(request.id, approver="test_user")
        assert approved.status == ApprovalStatus.APPROVED
        assert approved.approved_by == "test_user"
        assert approved.resolved_at is not None

    async def test_approve_nonpending_fails(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test that approving non-pending request fails."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
        )
        await approval_queue.approve(request.id, approver="test_user")

        with pytest.raises(ValueError, match="not pending"):
            await approval_queue.approve(request.id, approver="test_user")

    async def test_approve_nonexistent_fails(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test that approving nonexistent request fails."""
        with pytest.raises(ValueError, match="not found"):
            await approval_queue.approve("nonexistent", approver="user")

    async def test_reject(self, approval_queue: ApprovalQueueHarness) -> None:
        """Test rejecting a request."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
        )
        rejected = await approval_queue.reject(
            request.id, approver="test_user", reason="Needs work"
        )
        assert rejected.status == ApprovalStatus.REJECTED
        assert rejected.resolution_reason == "Needs work"

    async def test_reject_nonpending_fails(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test that rejecting non-pending request fails."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
        )
        await approval_queue.reject(request.id, approver="user", reason="reason")

        with pytest.raises(ValueError, match="not pending"):
            await approval_queue.reject(request.id, approver="user", reason="reason")

    async def test_cancel(self, approval_queue: ApprovalQueueHarness) -> None:
        """Test cancelling a request."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
        )
        cancelled = await approval_queue.cancel(request.id, reason="No longer needed")
        assert cancelled.status == ApprovalStatus.CANCELLED

    async def test_get_request(self, approval_queue: ApprovalQueueHarness) -> None:
        """Test getting a request."""
        created = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
        )
        retrieved = await approval_queue.get_request(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id

    async def test_get_request_nonexistent(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test getting nonexistent request."""
        result = await approval_queue.get_request("nonexistent")
        assert result is None


# =============================================================================
# Tests for list_pending
# =============================================================================


class TestListPending:
    """Tests for list_pending method."""

    async def test_list_pending_empty(self, approval_queue: ApprovalQueueHarness) -> None:
        """Test listing pending with none."""
        pending = await approval_queue.list_pending()
        assert pending == []

    async def test_list_pending_filters_by_domain(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test filtering by domain."""
        await approval_queue.create_request(
            type=ApprovalType.CONTENT, domain="domain1", title="Test1", description="Test"
        )
        await approval_queue.create_request(
            type=ApprovalType.CONTENT, domain="domain2", title="Test2", description="Test"
        )

        pending = await approval_queue.list_pending(domain="domain1")
        assert len(pending) == 1
        assert pending[0].domain == "domain1"

    async def test_list_pending_filters_by_type(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test filtering by type."""
        await approval_queue.create_request(
            type=ApprovalType.CONTENT, domain="test", title="Test1", description="Test"
        )
        await approval_queue.create_request(
            type=ApprovalType.DEPLOY, domain="test", title="Test2", description="Test"
        )

        pending = await approval_queue.list_pending(type=ApprovalType.CONTENT)
        assert len(pending) == 1
        assert pending[0].type == ApprovalType.CONTENT

    async def test_list_pending_filters_by_priority(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test filtering by priority."""
        await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test1",
            description="Test",
            priority=ApprovalPriority.LOW,
        )
        await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test2",
            description="Test",
            priority=ApprovalPriority.CRITICAL,
        )

        pending = await approval_queue.list_pending(priority=ApprovalPriority.CRITICAL)
        assert len(pending) == 1

    async def test_list_pending_sorts_by_priority(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test that pending is sorted by priority."""
        await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Low",
            description="Test",
            priority=ApprovalPriority.LOW,
        )
        await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Critical",
            description="Test",
            priority=ApprovalPriority.CRITICAL,
        )

        pending = await approval_queue.list_pending()
        assert pending[0].priority == ApprovalPriority.CRITICAL


# =============================================================================
# Tests for list_resolved
# =============================================================================


class TestListResolved:
    """Tests for list_resolved method."""

    async def test_list_resolved(self, approval_queue: ApprovalQueueHarness) -> None:
        """Test listing resolved requests."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT, domain="test", title="Test", description="Test"
        )
        await approval_queue.approve(request.id, approver="user")

        resolved = await approval_queue.list_resolved()
        assert len(resolved) == 1
        assert resolved[0].status == ApprovalStatus.APPROVED


# =============================================================================
# Tests for stats
# =============================================================================


class TestStats:
    """Tests for get_stats method."""

    async def test_get_stats_empty(self, approval_queue: ApprovalQueueHarness) -> None:
        """Test stats with no requests."""
        stats = await approval_queue.get_stats()
        assert stats.total_requests == 0
        assert stats.pending_count == 0

    async def test_get_stats_with_requests(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test stats with requests."""
        await approval_queue.create_request(
            type=ApprovalType.CONTENT, domain="test", title="Test1", description="Test"
        )
        request = await approval_queue.create_request(
            type=ApprovalType.DEPLOY, domain="test", title="Test2", description="Test"
        )
        await approval_queue.approve(request.id, approver="user")

        stats = await approval_queue.get_stats()
        assert stats.total_requests == 2
        assert stats.pending_count == 1
        assert stats.approved_count == 1


# =============================================================================
# Tests for expire_old_requests
# =============================================================================


class TestExpireOldRequests:
    """Tests for expire_old_requests method."""

    async def test_expire_old_requests(self, approval_queue: ApprovalQueueHarness) -> None:
        """Test expiring old requests."""
        # Create request with expiry in the past
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
        )
        # Manually set expires_at to past
        request.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await approval_queue.storage.save(request)

        expired = await approval_queue.expire_old_requests()
        assert len(expired) == 1
        assert expired[0].status == ApprovalStatus.EXPIRED


# =============================================================================
# Tests for cleanup_old_requests
# =============================================================================


class TestCleanupOldRequests:
    """Tests for cleanup_old_requests method."""

    async def test_cleanup_old_resolved(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test cleaning up old resolved requests."""
        # Create a resolved request directly with old resolved_at
        request = ApprovalRequest(
            id="old_resolved_001",
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.APPROVED,
            resolved_at=datetime.now(UTC) - timedelta(days=40),
            approved_by="test_user",
        )
        await approval_queue.storage.save(request)

        deleted = await approval_queue.cleanup_old_requests(days=30)
        assert deleted == 1


# =============================================================================
# Tests for factory functions
# =============================================================================


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_approval_queue(self, temp_storage_dir: Path) -> None:
        """Test create_approval_queue factory."""
        queue = create_approval_queue(storage_dir=temp_storage_dir)
        assert queue is not None
        assert queue.default_expiry_hours == 72.0

    @patch.dict("os.environ", {"FORGE_APPROVALS_DIR": "/tmp/test_approvals"})
    def test_create_approval_queue_from_env(self) -> None:
        """Test create_approval_queue_from_env factory."""
        queue = create_approval_queue_from_env()
        assert queue is not None


# =============================================================================
# Tests for edge cases
# =============================================================================


class TestEdgeCases:
    """Edge case tests."""

    async def test_create_with_workflow_checkpoint(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test creating request with workflow checkpoint."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
            workflow_checkpoint=Path("/tmp/checkpoint.json"),
        )
        assert request.workflow_checkpoint == "/tmp/checkpoint.json"

    async def test_create_with_tags(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test creating request with tags."""
        request = await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
            tags=["urgent", "review"],
        )
        assert "urgent" in request.tags
        assert "review" in request.tags

    async def test_list_pending_with_tier_filter(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test filtering by tier."""
        await approval_queue.create_request(
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
            tier="WATCH",
        )
        await approval_queue.create_request(
            type=ApprovalType.SECURITY,
            domain="test",
            title="Test",
            description="Test",
            tier="DESKTOP",
        )

        pending_watch = await approval_queue.list_pending(tier="WATCH")
        assert len(pending_watch) == 1
        assert pending_watch[0].tier == "WATCH"

    async def test_list_resolved_with_limit(
        self, approval_queue: ApprovalQueueHarness
    ) -> None:
        """Test resolved list with limit."""
        for i in range(5):
            request = await approval_queue.create_request(
                type=ApprovalType.CONTENT,
                domain="test",
                title=f"Test {i}",
                description="Test",
            )
            await approval_queue.approve(request.id, approver="user")

        resolved = await approval_queue.list_resolved(limit=2)
        assert len(resolved) == 2
