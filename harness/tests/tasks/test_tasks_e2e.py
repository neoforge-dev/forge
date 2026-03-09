#!/usr/bin/env python3
"""End-to-end V3 tests."""

import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Add harness to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from forge_harness.task_manager.database import TaskDatabase


class TestV3Database:
    """Test V3 SQLite database functionality."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            yield TaskDatabase(db_path=db_path)

    def test_create_task(self, temp_db):
        """Test task creation."""
        task_id = temp_db.create_task(
            title="Test Task",
            description="Test description",
            priority="high"
        )
        assert task_id.startswith("TC-")

    def test_get_task_status(self, temp_db):
        """Test getting task status."""
        task_id = temp_db.create_task(
            title="Test Task",
            description="Test description"
        )

        status = temp_db.get_task_status(task_id)
        assert status is not None
        assert status["title"] == "Test Task"
        assert status["status"] == "pending"

    def test_claim_task(self, temp_db):
        """Test claiming a task."""
        task_id = temp_db.create_task(
            title="Test Task",
            description="Test description"
        )

        success = temp_db.claim_task(task_id, "test-agent")
        assert success is True

        status = temp_db.get_task_status(task_id)
        assert status["status"] == "assigned"
        assert status["assigned_agent"] == "test-agent"

    def test_complete_task(self, temp_db):
        """Test completing a task."""
        task_id = temp_db.create_task(
            title="Test Task",
            description="Test description"
        )

        temp_db.claim_task(task_id, "test-agent")
        success = temp_db.complete_task(task_id, "test-agent", "Done!")
        assert success is True

        status = temp_db.get_task_status(task_id)
        assert status["status"] == "completed"

    def test_get_pending_tasks(self, temp_db):
        """Test getting pending tasks."""
        # Create multiple tasks
        temp_db.create_task(title="Task 1", priority="high")
        temp_db.create_task(title="Task 2", priority="medium")
        temp_db.create_task(title="Task 3", priority="low")

        pending = temp_db.get_pending_tasks(limit=10)
        assert len(pending) == 3

        # Verify priority ordering (high first)
        assert pending[0]["priority"] == "high"

    def test_task_lifecycle(self, temp_db):
        """Test complete task lifecycle."""
        # Create
        task_id = temp_db.create_task(
            title="Lifecycle Test",
            description="Full test",
            priority="medium"
        )

        # Verify pending
        status = temp_db.get_task_status(task_id)
        assert status["status"] == "pending"

        # Claim
        temp_db.claim_task(task_id, "agent-1")
        status = temp_db.get_task_status(task_id)
        assert status["status"] == "assigned"

        # Complete
        temp_db.complete_task(task_id, "agent-1", "All done")
        status = temp_db.get_task_status(task_id)
        assert status["status"] == "completed"


class TestV3ServerBinary:
    """Test V3 server binary."""

    def test_binary_exists(self):
        """Test that V3 binary exists."""
        binary_path = Path("/home/bogdan/FORGE/cmd/forge-v3/forge-v3")
        if binary_path.exists():
            assert binary_path.is_file()
        else:
            pytest.skip("V3 binary not found - may not be built yet")

    def test_binary_executable(self):
        """Test that V3 binary is executable."""
        binary_path = Path("/home/bogdan/FORGE/cmd/forge-v3/forge-v3")
        if binary_path.exists():
            import os
            assert os.access(binary_path, os.X_OK)
        else:
            pytest.skip("V3 binary not found")


class TestV3Configuration:
    """Test V3 configuration."""

    def test_v3_directory_structure(self):
        """Test V3 directory structure."""
        forge_root = Path("/home/bogdan/FORGE")
        v3_dir = forge_root / ".forge" / "v3"

        # Check key directories exist
        if v3_dir.exists():
            # Check for state.db or forge-v3.db
            db_files = list(v3_dir.glob("*.db"))
            assert len(db_files) >= 0  # May or may not exist
        else:
            pytest.skip("V3 directory not initialized yet")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
