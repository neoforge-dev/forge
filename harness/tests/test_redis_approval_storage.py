"""
Tests for Redis-based approval storage.

Requires Redis to be running on localhost:6379.
Tests will be skipped if Redis is unavailable.
"""

from datetime import UTC, datetime

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
    JSONFileStorage,
    RedisApprovalStorage,
    create_approval_queue_from_env,
)


# Skip all tests if Redis is not available
@pytest.fixture
def redis_available():
    """Check if Redis is available."""
    try:
        import redis

        client = redis.from_url("redis://localhost:6379/0", socket_connect_timeout=1)
        client.ping()
        client.close()
        return True
    except Exception:
        pytest.skip("Redis not available")


@pytest.fixture
async def redis_storage(tmp_path, redis_available):
    """Create RedisApprovalStorage with file backup for testing."""
    local_storage = JSONFileStorage(tmp_path / "approvals")
    storage = RedisApprovalStorage(
        redis_url="redis://localhost:6379/0",
        local_storage=local_storage,
    )
    storage.connect()
    yield storage

    # Cleanup: delete all test approvals
    if storage.is_connected():
        requests = await storage.list_all()
        for request in requests:
            if request.domain == "test-domain":
                await storage.delete(request.id)


@pytest.fixture
def sample_request():
    """Create a sample approval request."""
    return ApprovalRequest(
        id="test_apr_123",
        type=ApprovalType.FEATURE,
        domain="test-domain",
        title="Test Request",
        description="Test description",
        status=ApprovalStatus.PENDING,
        priority=ApprovalPriority.NORMAL,
        created_at=datetime.now(UTC),
        metadata={"test": True, "score": 95},
        tags=["backend", "test"],
    )


class TestRedisApprovalStorage:
    """Test Redis-based approval storage."""

    async def test_connect(self, redis_available):
        """Test Redis connection."""
        storage = RedisApprovalStorage(redis_url="redis://localhost:6379/0")
        assert storage.connect()
        assert storage.is_connected()

    async def test_save_and_load(self, redis_storage, sample_request):
        """Test saving and loading approval request."""
        # Save request
        await redis_storage.save(sample_request)

        # Load request
        loaded = await redis_storage.load(sample_request.id)

        assert loaded is not None
        assert loaded.id == sample_request.id
        assert loaded.title == sample_request.title
        assert loaded.metadata == sample_request.metadata
        assert loaded.tags == sample_request.tags
        assert loaded.status == sample_request.status

    async def test_save_with_file_backup(self, tmp_path, redis_available, sample_request):
        """Test that save creates file backup."""
        local_storage = JSONFileStorage(tmp_path / "approvals")
        storage = RedisApprovalStorage(
            redis_url="redis://localhost:6379/0",
            local_storage=local_storage,
        )
        storage.connect()

        # Save request
        await storage.save(sample_request)

        # Verify file backup exists
        files = list((tmp_path / "approvals").glob(f"*{sample_request.id}*.json"))
        assert len(files) > 0

        # Cleanup
        await storage.delete(sample_request.id)

    async def test_list_all(self, redis_storage, sample_request):
        """Test listing all approval requests."""
        # Create multiple requests
        request1 = sample_request
        request2 = ApprovalRequest(
            id="test_apr_456",
            type=ApprovalType.DEPLOY,
            domain="test-domain",
            title="Deploy Request",
            description="Deploy to prod",
            status=ApprovalStatus.PENDING,
            priority=ApprovalPriority.HIGH,
        )

        await redis_storage.save(request1)
        await redis_storage.save(request2)

        # List all
        all_requests = await redis_storage.list_all()

        # Should contain our test requests (and possibly others from other tests)
        test_requests = [r for r in all_requests if r.domain == "test-domain"]
        assert len(test_requests) >= 2

        # Cleanup
        await redis_storage.delete(request1.id)
        await redis_storage.delete(request2.id)

    async def test_delete(self, redis_storage, sample_request):
        """Test deleting approval request."""
        # Save request
        await redis_storage.save(sample_request)

        # Verify it exists
        loaded = await redis_storage.load(sample_request.id)
        assert loaded is not None

        # Delete
        success = await redis_storage.delete(sample_request.id)
        assert success

        # Verify it's gone
        loaded = await redis_storage.load(sample_request.id)
        assert loaded is None

    async def test_fallback_to_file_when_redis_unavailable(self, tmp_path):
        """Test fallback to file storage when Redis is unavailable."""
        local_storage = JSONFileStorage(tmp_path / "approvals")
        storage = RedisApprovalStorage(
            redis_url="redis://invalid-host:6379/0",
            local_storage=local_storage,
        )

        # Redis connection should fail
        assert not storage.connect()

        # But file storage should still work
        request = ApprovalRequest(
            id="test_fallback",
            type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Fallback Test",
            description="Testing fallback",
        )

        await storage.save(request)
        loaded = await storage.load(request.id)

        assert loaded is not None
        assert loaded.id == request.id

    async def test_complex_metadata_serialization(self, redis_storage):
        """Test that complex metadata is properly serialized."""
        request = ApprovalRequest(
            id="test_metadata",
            type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Metadata Test",
            description="Testing metadata serialization",
            metadata={
                "nested": {"key": "value"},
                "list": [1, 2, 3],
                "string": "test",
                "number": 42,
                "bool": True,
            },
            tags=["tag1", "tag2", "tag3"],
        )

        await redis_storage.save(request)
        loaded = await redis_storage.load(request.id)

        assert loaded is not None
        assert loaded.metadata == request.metadata
        assert loaded.tags == request.tags

        # Cleanup
        await redis_storage.delete(request.id)


class TestRedisApprovalQueueIntegration:
    """Test integration with ApprovalQueueHarness."""

    async def test_create_from_env_with_redis(self, redis_available, tmp_path, monkeypatch):
        """Test creating approval queue from environment with Redis."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("USE_REDIS_APPROVALS", "true")
        monkeypatch.setenv("FORGE_APPROVALS_DIR", str(tmp_path / "approvals"))

        queue = create_approval_queue_from_env()

        # Should use Redis storage
        assert isinstance(queue.storage, RedisApprovalStorage)
        assert queue.storage.is_connected()

        # Test creating an approval
        request = await queue.create_request(
            type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Integration Test",
            description="Testing Redis integration",
        )

        # Verify it's in Redis
        loaded = await queue.storage.load(request.id)
        assert loaded is not None
        assert loaded.id == request.id

        # Cleanup
        await queue.storage.delete(request.id)

    async def test_create_from_env_fallback_to_files(self, tmp_path, monkeypatch):
        """Test fallback to file storage when Redis is unavailable."""
        monkeypatch.setenv("REDIS_URL", "redis://invalid-host:6379/0")
        monkeypatch.setenv("USE_REDIS_APPROVALS", "true")
        monkeypatch.setenv("FORGE_APPROVALS_DIR", str(tmp_path / "approvals"))

        queue = create_approval_queue_from_env()

        # Should fall back to file storage
        assert isinstance(queue.storage, JSONFileStorage)

    async def test_create_from_env_disabled_redis(self, redis_available, tmp_path, monkeypatch):
        """Test that Redis can be disabled via environment."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("USE_REDIS_APPROVALS", "false")
        monkeypatch.setenv("FORGE_APPROVALS_DIR", str(tmp_path / "approvals"))

        queue = create_approval_queue_from_env()

        # Should use file storage even with Redis available
        assert isinstance(queue.storage, JSONFileStorage)


class TestRedisApprovalIndexes:
    """Test Redis indexing for efficient queries."""

    async def test_pending_index(self, redis_storage, sample_request):
        """Test that pending requests are properly indexed."""
        await redis_storage.save(sample_request)

        # Check that ID is in pending index
        if redis_storage.is_connected():
            pending_ids = redis_storage._client.smembers("approval:index:pending")
            assert sample_request.id in pending_ids

        # Update to approved
        sample_request.status = ApprovalStatus.APPROVED
        await redis_storage.save(sample_request)

        # Should be removed from pending index
        if redis_storage.is_connected():
            pending_ids = redis_storage._client.smembers("approval:index:pending")
            assert sample_request.id not in pending_ids

        # Cleanup
        await redis_storage.delete(sample_request.id)

    async def test_domain_index(self, redis_storage, sample_request):
        """Test that requests are indexed by domain."""
        await redis_storage.save(sample_request)

        if redis_storage.is_connected():
            domain_ids = redis_storage._client.smembers(
                f"approval:index:domain:{sample_request.domain}"
            )
            assert sample_request.id in domain_ids

        # Cleanup
        await redis_storage.delete(sample_request.id)

    async def test_type_index(self, redis_storage, sample_request):
        """Test that requests are indexed by type."""
        await redis_storage.save(sample_request)

        if redis_storage.is_connected():
            type_ids = redis_storage._client.smembers(
                f"approval:index:type:{sample_request.type.value}"
            )
            assert sample_request.id in type_ids

        # Cleanup
        await redis_storage.delete(sample_request.id)
