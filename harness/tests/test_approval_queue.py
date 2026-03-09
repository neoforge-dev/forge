"""Tests for approval_queue.py - Approval Queue Harness"""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalQueueHarness,
    ApprovalQueueStats,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
    JSONFileStorage,
    auto_classify_tier,
    create_approval_queue,
)


class TestApprovalRequest:
    """Tests for ApprovalRequest dataclass."""

    @pytest.fixture
    def sample_request(self):
        """Create a sample approval request."""
        return ApprovalRequest(
            id="apr_test123",
            type=ApprovalType.CONTENT,
            domain="codeswiftr-com",
            title="Test Content Review",
            description="Please review this content",
            metadata={"quality_score": 85},
            tags=["urgent"],
        )

    def test_default_values(self, sample_request):
        """Should initialize with correct defaults."""
        assert sample_request.status == ApprovalStatus.PENDING
        assert sample_request.priority == ApprovalPriority.NORMAL
        assert sample_request.tier == "PHONE"
        assert sample_request.risk_score == 0.5
        assert sample_request.approved_by is None
        assert sample_request.resolved_at is None

    def test_to_dict(self, sample_request):
        """Should convert to dict correctly."""
        data = sample_request.to_dict()

        assert data["id"] == "apr_test123"
        assert data["type"] == "content"
        assert data["domain"] == "codeswiftr-com"
        assert data["status"] == "pending"
        assert data["priority"] == "normal"
        assert data["metadata"]["quality_score"] == 85

    def test_from_dict(self):
        """Should create from dict correctly."""
        data = {
            "id": "apr_test456",
            "type": "deploy",
            "domain": "test-domain",
            "title": "Deploy to Prod",
            "description": "Deploy v1.2.3",
            "metadata": {},
            "status": "approved",
            "priority": "high",
            "tier": "DESKTOP",
            "risk_score": 0.8,
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": None,
            "approved_by": "@admin",
            "resolved_at": datetime.now(UTC).isoformat(),
            "resolution_reason": None,
            "workflow_checkpoint": None,
            "notification_ids": [],
            "tags": ["release"],
        }

        request = ApprovalRequest.from_dict(data)

        assert request.id == "apr_test456"
        assert request.type == ApprovalType.DEPLOY
        assert request.status == ApprovalStatus.APPROVED
        assert request.priority == ApprovalPriority.HIGH
        assert request.tier == "DESKTOP"
        assert request.approved_by == "@admin"

    def test_is_pending(self, sample_request):
        """Should check pending status correctly."""
        assert sample_request.is_pending is True

        sample_request.status = ApprovalStatus.APPROVED
        assert sample_request.is_pending is False

    def test_is_expired_with_expiration(self, sample_request):
        """Should check expiration correctly."""
        sample_request.expires_at = datetime.now(UTC) - timedelta(hours=1)
        assert sample_request.is_expired is True

    def test_is_not_expired(self, sample_request):
        """Should not be expired when within time."""
        sample_request.expires_at = datetime.now(UTC) + timedelta(hours=1)
        assert sample_request.is_expired is False

    def test_age_hours(self, sample_request):
        """Should calculate age in hours."""
        sample_request.created_at = datetime.now(UTC) - timedelta(hours=2)
        assert sample_request.age_hours == pytest.approx(2.0, abs=0.1)


class TestAutoClassifyTier:
    """Tests for auto_classify_tier function."""

    def test_security_always_desktop(self):
        """Security approvals should always be DESKTOP tier."""
        tier, risk = auto_classify_tier(
            ApprovalType.SECURITY, ApprovalPriority.NORMAL, {}
        )
        assert tier == "DESKTOP"
        assert risk >= 0.8

    def test_compliance_always_desktop(self):
        """Compliance approvals should always be DESKTOP tier."""
        tier, risk = auto_classify_tier(
            ApprovalType.COMPLIANCE, ApprovalPriority.NORMAL, {}
        )
        assert tier == "DESKTOP"
        assert risk >= 0.8

    def test_deploy_high_risk(self):
        """Deploy should be high risk."""
        tier, risk = auto_classify_tier(
            ApprovalType.DEPLOY, ApprovalPriority.NORMAL, {}
        )
        assert tier == "DESKTOP"
        assert risk > 0.6

    def test_content_low_risk(self):
        """Content should be low-medium risk."""
        tier, risk = auto_classify_tier(
            ApprovalType.CONTENT, ApprovalPriority.NORMAL, {}
        )
        assert tier == "PHONE"
        assert 0.3 <= risk < 0.6

    def test_priority_modifiers(self):
        """Priority should affect risk score."""
        _, risk_normal = auto_classify_tier(
            ApprovalType.FEATURE, ApprovalPriority.NORMAL, {}
        )
        _, risk_critical = auto_classify_tier(
            ApprovalType.FEATURE, ApprovalPriority.CRITICAL, {}
        )
        assert risk_critical > risk_normal

    def test_quality_score_adjustment(self):
        """High quality score should reduce risk for content."""
        _, risk_low = auto_classify_tier(
            ApprovalType.CONTENT, ApprovalPriority.NORMAL, {"quality_score": 50}
        )
        _, risk_high = auto_classify_tier(
            ApprovalType.CONTENT, ApprovalPriority.NORMAL, {"quality_score": 95}
        )
        assert risk_high < risk_low

    def test_lines_changed_adjustment(self):
        """Large changes should increase risk."""
        _, risk_small = auto_classify_tier(
            ApprovalType.FEATURE, ApprovalPriority.NORMAL, {"lines_changed": 100}
        )
        _, risk_large = auto_classify_tier(
            ApprovalType.FEATURE, ApprovalPriority.NORMAL, {"lines_changed": 1000}
        )
        assert risk_large > risk_small

    def test_production_environment(self):
        """Production environment should increase risk."""
        _, risk_dev = auto_classify_tier(
            ApprovalType.DEPLOY, ApprovalPriority.NORMAL, {"environment": "staging"}
        )
        _, risk_prod = auto_classify_tier(
            ApprovalType.DEPLOY, ApprovalPriority.NORMAL, {"environment": "production"}
        )
        assert risk_prod > risk_dev


class TestJSONFileStorage:
    """Tests for JSONFileStorage."""

    @pytest.fixture
    async def storage(self, tmp_path):
        """Create temporary storage."""
        storage_dir = tmp_path / "approvals"
        return JSONFileStorage(storage_dir)

    @pytest.mark.asyncio
    async def test_save_and_load(self, tmp_path):
        """Should save and load approval request."""
        storage = JSONFileStorage(tmp_path / "approvals")
        request = ApprovalRequest(
            id="apr_test001",
            type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Test",
            description="Test description",
        )

        await storage.save(request)
        loaded = await storage.load("apr_test001")

        assert loaded is not None
        assert loaded.id == "apr_test001"
        assert loaded.title == "Test"

    @pytest.mark.asyncio
    async def test_load_nonexistent(self, tmp_path):
        """Should return None for non-existent request."""
        storage = JSONFileStorage(tmp_path / "approvals")

        loaded = await storage.load("nonexistent")

        assert loaded is None

    @pytest.mark.asyncio
    async def test_list_all(self, tmp_path):
        """Should list all approval requests."""
        storage = JSONFileStorage(tmp_path / "approvals")

        request1 = ApprovalRequest(
            id="apr_001", type=ApprovalType.CONTENT, domain="d1", title="T1", description="D1"
        )
        request2 = ApprovalRequest(
            id="apr_002", type=ApprovalType.DEPLOY, domain="d2", title="T2", description="D2"
        )

        await storage.save(request1)
        await storage.save(request2)

        all_requests = await storage.list_all()

        assert len(all_requests) == 2

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path):
        """Should delete approval request."""
        storage = JSONFileStorage(tmp_path / "approvals")
        request = ApprovalRequest(
            id="apr_delete", type=ApprovalType.CONTENT, domain="d", title="T", description="D"
        )

        await storage.save(request)
        deleted = await storage.delete("apr_delete")

        assert deleted is True
        assert await storage.load("apr_delete") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, tmp_path):
        """Should return False for non-existent delete."""
        storage = JSONFileStorage(tmp_path / "approvals")

        deleted = await storage.delete("nonexistent")

        assert deleted is False


class TestApprovalQueueHarness:
    """Tests for ApprovalQueueHarness."""

    @pytest.fixture
    async def harness(self, tmp_path):
        """Create harness with temp storage."""
        storage = JSONFileStorage(tmp_path / "approvals")
        return ApprovalQueueHarness(storage)

    @pytest.mark.asyncio
    async def test_create_request(self, tmp_path):
        """Should create approval request."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        request = await harness.create_request(
            type=ApprovalType.CONTENT,
            domain="codeswiftr-com",
            title="Content Review",
            description="Please review",
            metadata={"quality_score": 90},
        )

        assert request.id.startswith("apr_")
        assert request.type == ApprovalType.CONTENT
        assert request.domain == "codeswiftr-com"
        assert request.status == ApprovalStatus.PENDING
        assert request.metadata["quality_score"] == 90

    @pytest.mark.asyncio
    async def test_create_request_auto_classifies_tier(self, tmp_path):
        """Should auto-classify tier on creation."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        request = await harness.create_request(
            type=ApprovalType.SECURITY,
            domain="test",
            title="Security Review",
            description="Security change",
        )

        assert request.tier == "DESKTOP"
        assert request.risk_score >= 0.8

    @pytest.mark.asyncio
    async def test_approve_request(self, tmp_path):
        """Should approve pending request."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        request = await harness.create_request(
            type=ApprovalType.CONTENT, domain="d", title="T", description="D"
        )

        approved = await harness.approve(request.id, approver="@admin", comment="LGTM")

        assert approved.status == ApprovalStatus.APPROVED
        assert approved.approved_by == "@admin"
        assert approved.resolution_reason == "LGTM"
        assert approved.resolved_at is not None

    @pytest.mark.asyncio
    async def test_approve_nonexistent_raises(self, tmp_path):
        """Should raise error for non-existent request."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        with pytest.raises(ValueError, match="not found"):
            await harness.approve("nonexistent", approver="@admin")

    @pytest.mark.asyncio
    async def test_approve_non_pending_raises(self, tmp_path):
        """Should raise error if not pending."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        request = await harness.create_request(
            type=ApprovalType.CONTENT, domain="d", title="T", description="D"
        )
        await harness.approve(request.id, approver="@admin")

        with pytest.raises(ValueError, match="not pending"):
            await harness.approve(request.id, approver="@admin")

    @pytest.mark.asyncio
    async def test_reject_request(self, tmp_path):
        """Should reject pending request."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        request = await harness.create_request(
            type=ApprovalType.CONTENT, domain="d", title="T", description="D"
        )

        rejected = await harness.reject(request.id, approver="@admin", reason="Needs work")

        assert rejected.status == ApprovalStatus.REJECTED
        assert rejected.approved_by == "@admin"
        assert rejected.resolution_reason == "Needs work"

    @pytest.mark.asyncio
    async def test_cancel_request(self, tmp_path):
        """Should cancel pending request."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        request = await harness.create_request(
            type=ApprovalType.CONTENT, domain="d", title="T", description="D"
        )

        cancelled = await harness.cancel(request.id, reason="No longer needed")

        assert cancelled.status == ApprovalStatus.CANCELLED
        assert cancelled.resolution_reason == "No longer needed"

    @pytest.mark.asyncio
    async def test_get_request(self, tmp_path):
        """Should get request by ID."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        created = await harness.create_request(
            type=ApprovalType.CONTENT, domain="d", title="T", description="D"
        )

        fetched = await harness.get_request(created.id)

        assert fetched is not None
        assert fetched.id == created.id

    @pytest.mark.asyncio
    async def test_list_pending(self, tmp_path):
        """Should list pending requests."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        await harness.create_request(
            type=ApprovalType.CONTENT, domain="d1", title="T1", description="D1"
        )
        await harness.create_request(
            type=ApprovalType.DEPLOY, domain="d2", title="T2", description="D2"
        )

        pending = await harness.list_pending()

        assert len(pending) == 2

    @pytest.mark.asyncio
    async def test_list_pending_filtered_by_domain(self, tmp_path):
        """Should filter pending by domain."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        await harness.create_request(
            type=ApprovalType.CONTENT, domain="domain-a", title="T1", description="D1"
        )
        await harness.create_request(
            type=ApprovalType.CONTENT, domain="domain-b", title="T2", description="D2"
        )

        pending = await harness.list_pending(domain="domain-a")

        assert len(pending) == 1
        assert pending[0].domain == "domain-a"

    @pytest.mark.asyncio
    async def test_list_pending_filtered_by_type(self, tmp_path):
        """Should filter pending by type."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        await harness.create_request(
            type=ApprovalType.CONTENT, domain="d", title="T1", description="D1"
        )
        await harness.create_request(
            type=ApprovalType.DEPLOY, domain="d", title="T2", description="D2"
        )

        pending = await harness.list_pending(type=ApprovalType.CONTENT)

        assert len(pending) == 1
        assert pending[0].type == ApprovalType.CONTENT

    @pytest.mark.asyncio
    async def test_list_pending_excludes_expired(self, tmp_path):
        """Should exclude expired requests by default."""
        storage = JSONFileStorage(tmp_path / "approvals")
        harness = ApprovalQueueHarness(storage)

        request = await harness.create_request(
            type=ApprovalType.CONTENT, domain="d", title="T", description="D", expiry_hours=0.001
        )
        # Make it expired
        request.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await storage.save(request)

        pending = await harness.list_pending()

        assert len(pending) == 0


class TestCreateApprovalQueue:
    """Tests for create_approval_queue factory."""

    def test_create_with_defaults(self, tmp_path):
        """Should create with default settings."""
        queue = create_approval_queue(storage_dir=tmp_path / "approvals")

        assert isinstance(queue, ApprovalQueueHarness)
        assert queue.default_expiry_hours == 72.0

    def test_create_with_custom_expiry(self, tmp_path):
        """Should create with custom expiry."""
        queue = create_approval_queue(
            storage_dir=tmp_path / "approvals", default_expiry_hours=48.0
        )

        assert queue.default_expiry_hours == 48.0
