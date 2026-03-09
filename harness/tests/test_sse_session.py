"""Unit tests for SSE session token store."""

import time
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.infrastructure.sse_session import (
    SessionEntry,
    SSESessionStore,
    get_sse_session_store,
)


class TestSessionEntry:
    """Tests for SessionEntry dataclass."""

    def test_session_entry_creation(self):
        """Test creating a session entry with default values."""
        entry = SessionEntry(token="test_token_123", created_at=1000.0)

        assert entry.token == "test_token_123"
        assert entry.created_at == 1000.0
        assert entry.ttl_seconds == 300

    def test_session_entry_custom_ttl(self):
        """Test creating a session entry with custom TTL."""
        entry = SessionEntry(token="test_token", ttl_seconds=600)
        assert entry.ttl_seconds == 600

    def test_is_valid_fresh_entry(self):
        """Test is_valid returns True for fresh entry."""
        entry = SessionEntry(token="test_token", created_at=1000.0)

        # Mock time as 1050.0 (50 seconds later, within 300s TTL)
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1050.0):
            assert entry.is_valid() is True

    def test_is_valid_expired_entry(self):
        """Test is_valid returns False for expired entry."""
        entry = SessionEntry(token="test_token", created_at=1000.0, ttl_seconds=300)

        # Mock time as 1301.0 (301 seconds later, beyond 300s TTL)
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1301.0):
            assert entry.is_valid() is False

    def test_is_valid_exact_boundary(self):
        """Test is_valid at exact TTL boundary."""
        entry = SessionEntry(token="test_token", created_at=1000.0, ttl_seconds=300)

        # Mock time as exactly 1300.0 (exactly 300 seconds later)
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1300.0):
            # Should return False (not strictly less than)
            assert entry.is_valid() is False

    def test_is_valid_just_before_expiry(self):
        """Test is_valid just before expiry."""
        entry = SessionEntry(token="test_token", created_at=1000.0, ttl_seconds=300)

        # Mock time as 1299.9 (just before expiry)
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1299.9):
            assert entry.is_valid() is True


class TestSSESessionStore:
    """Tests for SSESessionStore class."""

    @pytest.fixture
    def store(self):
        """Create a fresh SSESessionStore for each test."""
        return SSESessionStore(ttl_seconds=300)

    def test_store_creation_default_ttl(self):
        """Test creating store with default TTL."""
        store = SSESessionStore()
        assert store._ttl == 300
        assert len(store._sessions) == 0

    def test_store_creation_custom_ttl(self):
        """Test creating store with custom TTL."""
        store = SSESessionStore(ttl_seconds=600)
        assert store._ttl == 600

    def test_create_session_token(self, store):
        """Test creating a session token."""
        bearer = "test_bearer_token"
        session_token = store.create(bearer)

        assert session_token is not None
        assert len(session_token) > 0
        assert session_token in store._sessions

    def test_create_generates_unique_tokens(self, store):
        """Test that create generates unique tokens."""
        tokens = set()
        for _ in range(100):
            token = store.create("bearer_token")
            tokens.add(token)

        # All tokens should be unique
        assert len(tokens) == 100

    def test_create_token_uses_store_ttl(self, store):
        """Test that created token uses store's TTL."""
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1000.0):
            token = store.create("bearer")

        entry = store._sessions[token]
        assert entry.ttl_seconds == 300

    def test_validate_valid_token(self, store):
        """Test validating a valid token."""
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1000.0):
            token = store.create("bearer")

        # 50 seconds later, still valid
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1050.0):
            assert store.validate(token) is True

        # Token should still be in store
        assert token in store._sessions

    def test_validate_nonexistent_token(self, store):
        """Test validating a token that doesn't exist."""
        assert store.validate("nonexistent_token") is False

    def test_validate_expired_token_removes_it(self, store):
        """Test that validating expired token removes it from store."""
        token = store.create("bearer")
        # Manually set created_at to test expiry
        store._sessions[token].created_at = 1000.0
        assert token in store._sessions

        # 301 seconds later, expired
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1301.0):
            assert store.validate(token) is False
            # Token should be removed from store
            assert token not in store._sessions

    def test_revoke_existing_token(self, store):
        """Test revoking an existing token."""
        token = store.create("bearer")
        assert token in store._sessions

        store.revoke(token)
        assert token not in store._sessions

    def test_revoke_nonexistent_token(self, store):
        """Test revoking a token that doesn't exist (should not error)."""
        # Should not raise exception
        store.revoke("nonexistent_token")

    def test_revoke_idempotent(self, store):
        """Test that revoke is idempotent."""
        token = store.create("bearer")
        store.revoke(token)
        store.revoke(token)  # Should not error
        assert token not in store._sessions

    def test_cleanup_expired_removes_expired(self, store):
        """Test cleanup_expired removes only expired tokens."""
        token1 = store.create("bearer1")
        token2 = store.create("bearer2")
        token3 = store.create("bearer3")

        # Set all tokens to same creation time
        for token in [token1, token2, token3]:
            store._sessions[token].created_at = 1000.0

        # 100 seconds later (all still valid)
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1100.0):
            count = store.cleanup_expired()
            assert count == 0
            assert len(store._sessions) == 3

        # 350 seconds from original creation (all expired)
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1350.0):
            count = store.cleanup_expired()
            assert count == 3
            assert len(store._sessions) == 0

    def test_cleanup_expired_mixed_validity(self, store):
        """Test cleanup_expired with mix of valid and expired tokens."""
        token1 = store.create("bearer1")
        token2 = store.create("bearer2")
        token3 = store.create("bearer3")

        # Manually set creation times: token1, token2 old; token3 fresh
        store._sessions[token1].created_at = 1000.0
        store._sessions[token2].created_at = 1000.0
        store._sessions[token3].created_at = 1250.0

        # 350 seconds from start (token1, token2 expired; token3 valid)
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1350.0):
            count = store.cleanup_expired()
            assert count == 2
            assert token1 not in store._sessions
            assert token2 not in store._sessions
            assert token3 in store._sessions

    def test_cleanup_expired_returns_zero_when_empty(self, store):
        """Test cleanup_expired returns 0 when store is empty."""
        count = store.cleanup_expired()
        assert count == 0

    def test_cleanup_expired_returns_zero_when_all_valid(self, store):
        """Test cleanup_expired returns 0 when all tokens are valid."""
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1000.0):
            store.create("bearer1")
            store.create("bearer2")

        # 50 seconds later, all still valid
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1050.0):
            count = store.cleanup_expired()
            assert count == 0
            assert len(store._sessions) == 2


class TestGetSSESessionStore:
    """Tests for get_sse_session_store singleton function."""

    def test_get_sse_session_store_returns_instance(self):
        """Test that get_sse_session_store returns a SSESessionStore instance."""
        # Reset global singleton
        import forge_harness.webhook_server.infrastructure.sse_session as module

        module._sse_session_store = None

        store = get_sse_session_store()
        assert isinstance(store, SSESessionStore)
        assert store._ttl == 300

    def test_get_sse_session_store_singleton_behavior(self):
        """Test that get_sse_session_store returns same instance on subsequent calls."""
        import forge_harness.webhook_server.infrastructure.sse_session as module

        module._sse_session_store = None

        store1 = get_sse_session_store()
        store2 = get_sse_session_store()

        assert store1 is store2

    def test_singleton_preserves_state(self):
        """Test that singleton preserves state across get calls."""
        import forge_harness.webhook_server.infrastructure.sse_session as module

        module._sse_session_store = None

        store1 = get_sse_session_store()
        token = store1.create("bearer")

        store2 = get_sse_session_store()
        assert store2.validate(token) is True


class TestIntegration:
    """Integration tests for SSESessionStore."""

    def test_full_session_lifecycle(self):
        """Test complete session lifecycle: create, validate, revoke."""
        store = SSESessionStore(ttl_seconds=300)

        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1000.0):
            token = store.create("test_bearer")

        # Validate immediately
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1000.0):
            assert store.validate(token) is True

        # Validate 100 seconds later
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1100.0):
            assert store.validate(token) is True

        # Revoke
        store.revoke(token)
        assert store.validate(token) is False

    def test_multiple_concurrent_sessions(self):
        """Test multiple concurrent sessions."""
        store = SSESessionStore(ttl_seconds=300)

        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1000.0):
            tokens = [store.create(f"bearer_{i}") for i in range(10)]

        # All should be valid
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1100.0):
            for token in tokens:
                assert store.validate(token) is True

        # Revoke half
        for token in tokens[:5]:
            store.revoke(token)

        # Check state
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1100.0):
            for token in tokens[:5]:
                assert store.validate(token) is False
            for token in tokens[5:]:
                assert store.validate(token) is True

    def test_cleanup_during_active_usage(self):
        """Test cleanup doesn't affect valid sessions."""
        store = SSESessionStore(ttl_seconds=300)

        old_tokens = [store.create(f"old_{i}") for i in range(5)]
        new_tokens = [store.create(f"new_{i}") for i in range(5)]

        # Manually set creation times
        for token in old_tokens:
            store._sessions[token].created_at = 1000.0

        for token in new_tokens:
            store._sessions[token].created_at = 1350.0

        # Cleanup expired (old_tokens expired by 1400; new_tokens valid until 1650)
        with patch("forge_harness.webhook_server.infrastructure.sse_session.time.time", return_value=1400.0):
            count = store.cleanup_expired()
            assert count == 5

            # Old tokens invalid
            for token in old_tokens:
                assert store.validate(token) is False

            # New tokens still valid (created at 1350, valid until 1650)
            for token in new_tokens:
                assert store.validate(token) is True
