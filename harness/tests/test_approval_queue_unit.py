"""
Pure unit tests for forge_harness.approval_queue

Covers:
- ApprovalRequest dataclass initialization and methods
- ApprovalQueueStats dataclass
- auto_classify_tier() tier classification
- ApprovalStorage abstract interface
- JSONFileStorage implementation
- ApprovalQueueHarness create/approve/reject/cancel
- Pending request listing and filtering
- Request expiration handling
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalQueueHarness,
    ApprovalQueueStats,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStorage,
    ApprovalType,
    JSONFileStorage,
    auto_classify_tier,
)


class TestApprovalRequest:
    """Tests for ApprovalRequest dataclass."""

    def test_init_defaults(self):
        """Test default initialization."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Test Request",
            description="Test description",
        )

        assert request.id == "test_123"
        assert request.type == ApprovalType.FEATURE
        assert request.domain == "test-domain"
        assert request.status == ApprovalStatus.PENDING
        assert request.priority == ApprovalPriority.NORMAL
        assert request.tier == "PHONE"
        assert request.risk_score == 0.5
        assert isinstance(request.created_at, datetime)
        assert request.metadata == {}
        assert request.tags == []

    def test_to_dict(self):
        """Test conversion to dictionary."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Test Request",
            description="Test description",
            status=ApprovalStatus.APPROVED,
            approved_by="@user",
        )

        data = request.to_dict()

        assert data["id"] == "test_123"
        assert data["type"] == "feature"
        assert data["domain"] == "test-domain"
        assert data["status"] == "approved"
        assert data["approved_by"] == "@user"
        assert "created_at" in data

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "id": "test_123",
            "type": "feature",
            "domain": "test-domain",
            "title": "Test Request",
            "description": "Test description",
            "status": "pending",
            "priority": "high",
            "created_at": datetime.now(UTC).isoformat(),
            "metadata": {"key": "value"},
        }

        request = ApprovalRequest.from_dict(data)

        assert request.id == "test_123"
        assert request.type == ApprovalType.FEATURE
        assert request.priority == ApprovalPriority.HIGH
        assert request.metadata == {"key": "value"}

    def test_is_pending_true(self):
        """Test is_pending when status is PENDING."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        assert request.is_pending is True

    def test_is_pending_false(self):
        """Test is_pending when status is not PENDING."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.APPROVED,
        )
        assert request.is_pending is False

    def test_is_expired_with_expiration(self):
        """Test is_expired with past expiration."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        assert request.is_expired is True

    def test_is_expired_no_expiration(self):
        """Test is_expired without expiration."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            expires_at=None,
        )
        assert request.is_expired is False

    def test_is_expired_future(self):
        """Test is_expired with future expiration."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        assert request.is_expired is False

    def test_age_hours(self):
        """Test age calculation in hours."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            created_at=datetime.now(UTC) - timedelta(hours=2, minutes=30),
        )

        age = request.age_hours

        assert 2.4 <= age <= 2.6  # Approximately 2.5 hours


class TestAutoClassifyTier:
    """Tests for auto_classify_tier function."""

    def test_security_is_desktop(self):
        """Test SECURITY type is always DESKTOP."""
        tier, risk = auto_classify_tier(
            ApprovalType.SECURITY,
            ApprovalPriority.NORMAL,
            {}
        )
        assert tier == "DESKTOP"
        assert risk >= 0.8

    def test_compliance_is_desktop(self):
        """Test COMPLIANCE type is always DESKTOP."""
        tier, risk = auto_classify_tier(
            ApprovalType.COMPLIANCE,
            ApprovalPriority.NORMAL,
            {}
        )
        assert tier == "DESKTOP"
        assert risk >= 0.8

    def test_deploy_high_risk(self):
        """Test DEPLOY type has high risk."""
        tier, risk = auto_classify_tier(
            ApprovalType.DEPLOY,
            ApprovalPriority.NORMAL,
            {}
        )
        assert risk == 0.8

    def test_content_low_risk(self):
        """Test CONTENT type has lower risk."""
        tier, risk = auto_classify_tier(
            ApprovalType.CONTENT,
            ApprovalPriority.NORMAL,
            {}
        )
        assert 0.2 <= risk <= 0.4  # Content has base 0.3 but may be adjusted

    def test_critical_priority_increases_risk(self):
        """Test critical priority increases risk."""
        _, risk_normal = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.NORMAL,
            {}
        )
        _, risk_critical = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.CRITICAL,
            {}
        )
        assert risk_critical > risk_normal

    def test_low_priority_decreases_risk(self):
        """Test low priority decreases risk."""
        _, risk_normal = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.NORMAL,
            {}
        )
        _, risk_low = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.LOW,
            {}
        )
        assert risk_low < risk_normal

    def test_high_quality_content_reduces_risk(self):
        """Test high quality score reduces content risk."""
        _, risk_low_quality = auto_classify_tier(
            ApprovalType.CONTENT,
            ApprovalPriority.NORMAL,
            {"quality_score": 50}
        )
        _, risk_high_quality = auto_classify_tier(
            ApprovalType.CONTENT,
            ApprovalPriority.NORMAL,
            {"quality_score": 95}
        )
        assert risk_high_quality < risk_low_quality

    def test_large_changes_increase_risk(self):
        """Test large line changes increase risk."""
        _, risk_small = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.NORMAL,
            {"lines_changed": 100}
        )
        _, risk_large = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.NORMAL,
            {"lines_changed": 1000}
        )
        assert risk_large > risk_small

    def test_production_deploy_increases_risk(self):
        """Test production environment increases risk."""
        _, risk_staging = auto_classify_tier(
            ApprovalType.DEPLOY,
            ApprovalPriority.NORMAL,
            {"environment": "staging"}
        )
        _, risk_prod = auto_classify_tier(
            ApprovalType.DEPLOY,
            ApprovalPriority.NORMAL,
            {"environment": "production"}
        )
        assert risk_prod > risk_staging

    def test_low_coverage_increases_risk(self):
        """Test low test coverage increases feature risk."""
        _, risk_good_coverage = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.NORMAL,
            {"test_coverage": 95}
        )
        _, risk_low_coverage = auto_classify_tier(
            ApprovalType.FEATURE,
            ApprovalPriority.NORMAL,
            {"test_coverage": 50}
        )
        assert risk_low_coverage > risk_good_coverage

    def test_risk_capped_at_one(self):
        """Test risk score is capped at 1.0."""
        tier, risk = auto_classify_tier(
            ApprovalType.SECURITY,
            ApprovalPriority.CRITICAL,
            {
                "environment": "production",
                "lines_changed": 10000,
            }
        )
        assert risk == 1.0


class TestApprovalStorage:
    """Tests for ApprovalStorage abstract class."""

    def test_abstract_methods_exist(self):
        """Test that ApprovalStorage defines abstract methods."""
        # Check that the class has the expected abstract methods
        assert hasattr(ApprovalStorage, 'save')
        assert hasattr(ApprovalStorage, 'load')
        assert hasattr(ApprovalStorage, 'list_all')
        assert hasattr(ApprovalStorage, 'delete')


class TestJSONFileStorage:
    """Tests for JSONFileStorage implementation."""

    @pytest.fixture
    def temp_storage(self, tmp_path):
        """Create temporary storage for testing."""
        return JSONFileStorage(tmp_path / "approvals")

    @pytest.mark.asyncio
    async def test_save_and_load(self, temp_storage):
        """Test saving and loading a request."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
        )

        await temp_storage.save(request)
        loaded = await temp_storage.load("test_123")

        assert loaded is not None
        assert loaded.id == "test_123"
        assert loaded.title == "Test"

    @pytest.mark.asyncio
    async def test_load_nonexistent(self, temp_storage):
        """Test loading non-existent request."""
        loaded = await temp_storage.load("nonexistent")
        assert loaded is None

    @pytest.mark.asyncio
    async def test_list_all(self, temp_storage):
        """Test listing all requests."""
        request1 = ApprovalRequest(
            id="test_1",
            type=ApprovalType.FEATURE,
            domain="domain1",
            title="Test 1",
            description="Test",
        )
        request2 = ApprovalRequest(
            id="test_2",
            type=ApprovalType.CONTENT,
            domain="domain2",
            title="Test 2",
            description="Test",
        )

        await temp_storage.save(request1)
        await temp_storage.save(request2)

        all_requests = await temp_storage.list_all()

        assert len(all_requests) == 2
        ids = {r.id for r in all_requests}
        assert ids == {"test_1", "test_2"}

    @pytest.mark.asyncio
    async def test_delete(self, temp_storage):
        """Test deleting a request."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
        )

        await temp_storage.save(request)
        result = await temp_storage.delete("test_123")
        loaded = await temp_storage.load("test_123")

        assert result is True
        assert loaded is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, temp_storage):
        """Test deleting non-existent request."""
        result = await temp_storage.delete("nonexistent")
        assert result is False

    def test_load_index_rebuild(self, tmp_path):
        """Test index rebuild when file is corrupted."""
        storage_dir = tmp_path / "approvals"
        storage_dir.mkdir()

        # Create corrupted index
        index_path = storage_dir / "_index.json"
        index_path.write_text("not valid json")

        # Should not raise, should rebuild index
        storage = JSONFileStorage(storage_dir)
        assert storage._index == {}


class TestApprovalQueueHarness:
    """Tests for ApprovalQueueHarness."""

    @pytest.fixture
    def mock_storage(self):
        """Create mock storage."""
        storage = AsyncMock()
        storage.save = AsyncMock()
        storage.load = AsyncMock(return_value=None)
        storage.list_all = AsyncMock(return_value=[])
        return storage

    @pytest.fixture
    def harness(self, mock_storage):
        """Create harness with mock storage."""
        return ApprovalQueueHarness(
            storage=mock_storage,
            default_expiry_hours=72.0,
        )

    @pytest.mark.asyncio
    async def test_create_request(self, harness, mock_storage):
        """Test creating an approval request."""
        request = await harness.create_request(
            type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Test Feature",
            description="Test description",
        )

        assert request.id.startswith("apr_")
        assert request.type == ApprovalType.FEATURE
        assert request.domain == "test-domain"
        assert request.status == ApprovalStatus.PENDING
        assert mock_storage.save.called

    @pytest.mark.asyncio
    async def test_create_request_with_expiry(self, harness, mock_storage):
        """Test creating request with custom expiry."""
        request = await harness.create_request(
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            expiry_hours=24.0,
        )

        assert request.expires_at is not None
        # Should be approximately 24 hours from now
        delta = request.expires_at - datetime.now(UTC)
        assert 23 < delta.total_seconds() / 3600 < 25

    @pytest.mark.asyncio
    async def test_create_request_no_expiry(self, harness, mock_storage):
        """Test creating request with no expiry."""
        request = await harness.create_request(
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            expiry_hours=0,
        )

        assert request.expires_at is None

    @pytest.mark.asyncio
    async def test_create_request_auto_classifies_tier(self, harness, mock_storage):
        """Test tier is auto-classified on creation."""
        request = await harness.create_request(
            type=ApprovalType.SECURITY,
            domain="test",
            title="Security Fix",
            description="Test",
        )

        assert request.tier == "DESKTOP"
        assert request.risk_score >= 0.8

    @pytest.mark.asyncio
    async def test_approve_success(self, harness, mock_storage):
        """Test approving a pending request."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        mock_storage.load.return_value = request

        result = await harness.approve("test_123", approver="@user", comment="LGTM")

        assert result.status == ApprovalStatus.APPROVED
        assert result.approved_by == "@user"
        assert result.resolution_reason == "LGTM"
        assert result.resolved_at is not None
        assert mock_storage.save.called

    @pytest.mark.asyncio
    async def test_approve_not_found(self, harness, mock_storage):
        """Test approving non-existent request."""
        mock_storage.load.return_value = None

        with pytest.raises(ValueError, match="not found"):
            await harness.approve("nonexistent", approver="@user")

    @pytest.mark.asyncio
    async def test_approve_not_pending(self, harness, mock_storage):
        """Test approving non-pending request."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.APPROVED,
        )
        mock_storage.load.return_value = request

        with pytest.raises(ValueError, match="not pending"):
            await harness.approve("test_123", approver="@user")

    @pytest.mark.asyncio
    async def test_reject_success(self, harness, mock_storage):
        """Test rejecting a pending request."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        mock_storage.load.return_value = request

        result = await harness.reject("test_123", approver="@user", reason="Needs work")

        assert result.status == ApprovalStatus.REJECTED
        assert result.approved_by == "@user"
        assert result.resolution_reason == "Needs work"
        assert result.resolved_at is not None

    @pytest.mark.asyncio
    async def test_cancel_success(self, harness, mock_storage):
        """Test cancelling a pending request."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        mock_storage.load.return_value = request

        result = await harness.cancel("test_123", reason="No longer needed")

        assert result.status == ApprovalStatus.CANCELLED
        assert result.resolution_reason == "No longer needed"

    @pytest.mark.asyncio
    async def test_get_request(self, harness, mock_storage):
        """Test getting a request by ID."""
        request = ApprovalRequest(
            id="test_123",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
        )
        mock_storage.load.return_value = request

        result = await harness.get_request("test_123")

        assert result is request

    @pytest.mark.asyncio
    async def test_list_pending(self, harness, mock_storage):
        """Test listing pending requests."""
        pending = ApprovalRequest(
            id="pending_1",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Pending",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        approved = ApprovalRequest(
            id="approved_1",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Approved",
            description="Test",
            status=ApprovalStatus.APPROVED,
        )
        mock_storage.list_all.return_value = [pending, approved]

        results = await harness.list_pending()

        assert len(results) == 1
        assert results[0].id == "pending_1"

    @pytest.mark.asyncio
    async def test_list_pending_filters_expired(self, harness, mock_storage):
        """Test listing pending excludes expired by default."""
        expired = ApprovalRequest(
            id="expired_1",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Expired",
            description="Test",
            status=ApprovalStatus.PENDING,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        current = ApprovalRequest(
            id="current_1",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Current",
            description="Test",
            status=ApprovalStatus.PENDING,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        mock_storage.list_all.return_value = [expired, current]

        results = await harness.list_pending()

        assert len(results) == 1
        assert results[0].id == "current_1"

    @pytest.mark.asyncio
    async def test_list_pending_by_domain(self, harness, mock_storage):
        """Test filtering pending by domain."""
        domain1 = ApprovalRequest(
            id="req_1",
            type=ApprovalType.FEATURE,
            domain="domain1",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        domain2 = ApprovalRequest(
            id="req_2",
            type=ApprovalType.FEATURE,
            domain="domain2",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        mock_storage.list_all.return_value = [domain1, domain2]

        results = await harness.list_pending(domain="domain1")

        assert len(results) == 1
        assert results[0].domain == "domain1"

    @pytest.mark.asyncio
    async def test_list_pending_by_type(self, harness, mock_storage):
        """Test filtering pending by type."""
        feature = ApprovalRequest(
            id="req_1",
            type=ApprovalType.FEATURE,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        content = ApprovalRequest(
            id="req_2",
            type=ApprovalType.CONTENT,
            domain="test",
            title="Test",
            description="Test",
            status=ApprovalStatus.PENDING,
        )
        mock_storage.list_all.return_value = [feature, content]

        results = await harness.list_pending(type=ApprovalType.FEATURE)

        assert len(results) == 1
        assert results[0].type == ApprovalType.FEATURE


class TestApprovalEnums:
    """Tests for approval enums."""

    def test_approval_type_values(self):
        """Test approval type enum values."""
        assert ApprovalType.CONTENT.value == "content"
        assert ApprovalType.DEPLOY.value == "deploy"
        assert ApprovalType.FEATURE.value == "feature"
        assert ApprovalType.CONFIG.value == "config"
        assert ApprovalType.DATA.value == "data"
        assert ApprovalType.SECURITY.value == "security"
        assert ApprovalType.COMPLIANCE.value == "compliance"

    def test_approval_status_values(self):
        """Test approval status enum values."""
        assert ApprovalStatus.PENDING.value == "pending"
        assert ApprovalStatus.APPROVED.value == "approved"
        assert ApprovalStatus.REJECTED.value == "rejected"
        assert ApprovalStatus.EXPIRED.value == "expired"
        assert ApprovalStatus.CANCELLED.value == "cancelled"

    def test_approval_priority_values(self):
        """Test approval priority enum values."""
        assert ApprovalPriority.LOW.value == "low"
        assert ApprovalPriority.NORMAL.value == "normal"
        assert ApprovalPriority.HIGH.value == "high"
        assert ApprovalPriority.CRITICAL.value == "critical"
