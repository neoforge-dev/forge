"""
Integration test for Redis state store connection.

Verifies that the Redis connection is working properly
and that the state store can be used for basic operations.
"""

import os
import unittest

from forge_harness.state_store import AgentRole, AgentSession, AgentStatus, AgentType, StateStore


class TestRedisIntegration(unittest.TestCase):
    """Test Redis integration for state store."""

    def setUp(self):
        """Set up test environment."""
        # Use the default Redis URL or environment variable
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.state_store = StateStore(redis_url=self.redis_url)

    def test_redis_connection(self):
        """Test connecting to Redis."""
        store_type = self.state_store.connect()
        self.assertIn(store_type, ["redis", "sqlite"])
        self.assertTrue(self.state_store.is_connected())
        self.assertEqual(store_type, self.state_store.get_store_type())

    def test_agent_session_crud(self):
        """Test CRUD operations for agent sessions."""
        # Skip if not using Redis
        if self.state_store.get_store_type() != "redis":
            self.skipTest("Not using Redis")

        # Create test session
        session = AgentSession(
            session_id="test-session",
            agent_type=AgentType.CLAUDE_CODE,
            agent_role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
            status=AgentStatus.ACTIVE,
        )

        # Register session
        result = self.state_store.register_agent(session)
        self.assertTrue(result)

        # Retrieve session
        retrieved = self.state_store.get_agent("test-session")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.session_id, "test-session")
        self.assertEqual(retrieved.agent_type, AgentType.CLAUDE_CODE)
        self.assertEqual(retrieved.agent_role, AgentRole.BUILDER)
        self.assertEqual(retrieved.domain, "test-domain")
        self.assertEqual(retrieved.project, "test-project")
        self.assertEqual(retrieved.status, AgentStatus.ACTIVE)

        # Update status
        result = self.state_store.update_agent_status(
            session_id="test-session",
            status=AgentStatus.WAITING_HUMAN,
            current_task="waiting for approval",
        )
        self.assertTrue(result)

        # Verify update
        retrieved = self.state_store.get_agent("test-session")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.status, AgentStatus.WAITING_HUMAN)
        self.assertEqual(retrieved.current_task, "waiting for approval")

    def test_work_lock(self):
        """Test work lock operations."""
        # Skip if not using Redis
        if self.state_store.get_store_type() != "redis":
            self.skipTest("Not using Redis")

        # Acquire lock
        result = self.state_store.acquire_work_lock(
            task_id="test-task", session_id="test-session", ttl_seconds=60
        )
        self.assertTrue(result)

        # Check lock status
        is_locked = self.state_store.is_work_locked("test-task")
        self.assertTrue(is_locked)

        # Try to acquire again (should fail)
        result = self.state_store.acquire_work_lock(
            task_id="test-task", session_id="other-session", ttl_seconds=60
        )
        self.assertFalse(result)

        # Get lock holder
        holder = self.state_store.get_work_lock_holder("test-task")
        self.assertEqual(holder, "test-session")

        # Release lock
        result = self.state_store.release_work_lock("test-task", "test-session")
        self.assertTrue(result)

        # Verify released
        is_locked = self.state_store.is_work_locked("test-task")
        self.assertFalse(is_locked)

    def tearDown(self):
        """Clean up test environment."""
        # Disconnect from Redis
        self.state_store.disconnect()


if __name__ == "__main__":
    unittest.main()
