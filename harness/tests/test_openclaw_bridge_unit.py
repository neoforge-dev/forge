"""Unit tests for OpenClaw Bridge."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.task_manager.database import TaskDatabase
from forge_harness.task_manager.openclaw_bridge import OpenClawBridge


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield TaskDatabase(db_path=db_path)


@pytest.fixture
def bridge(temp_db):
    """Create bridge with mock database."""
    with patch("forge_harness.task_manager.openclaw_bridge.get_task_db") as mock_get_db:
        mock_get_db.return_value = temp_db
        yield OpenClawBridge()


class TestTelegramHandler:
    """Tests for Telegram message handling."""

    def test_handle_task_simple(self, bridge, temp_db):
        """Test creating task from /task command."""
        bridge.db = temp_db

        message = {
            "message": {
                "text": "/task Fix login bug",
                "chat": {"id": 123456},
                "from": {"username": "testuser"},
                "message_id": 42
            }
        }

        result = bridge.handle_telegram(message)
        assert result["ok"] is True
        assert "task_id" in result

    def test_handle_task_with_description(self, bridge, temp_db):
        """Test creating task with description."""
        bridge.db = temp_db

        message = {
            "message": {
                "text": "/task Fix login bug - Button not responding",
                "chat": {"id": 123456},
                "from": {"username": "testuser"},
                "message_id": 42
            }
        }

        result = bridge.handle_telegram(message)
        assert result["ok"] is True
        assert "Fix login bug" in result["title"]

    def test_handle_bug_command(self, bridge, temp_db):
        """Test /bug command creates high priority task."""
        bridge.db = temp_db

        message = {
            "message": {
                "text": "/bug Critical error - System crash",
                "chat": {"id": 123456},
                "from": {"username": "testuser"},
                "message_id": 42
            }
        }

        result = bridge.handle_telegram(message)
        assert result["ok"] is True
        assert result["priority"] == "high"

    def test_handle_feature_command(self, bridge, temp_db):
        """Test /feature command creates medium priority task."""
        bridge.db = temp_db

        message = {
            "message": {
                "text": "/feature Add dark mode",
                "chat": {"id": 123456},
                "from": {"username": "testuser"},
                "message_id": 42
            }
        }

        result = bridge.handle_telegram(message)
        assert result["ok"] is True
        assert result["priority"] == "medium"

    def test_handle_critical_command(self, bridge, temp_db):
        """Test /critical command creates critical priority task."""
        bridge.db = temp_db

        message = {
            "message": {
                "text": "/critical Payment failing",
                "chat": {"id": 123456},
                "from": {"username": "testuser"},
                "message_id": 42
            }
        }

        result = bridge.handle_telegram(message)
        assert result["ok"] is True
        assert result["priority"] == "critical"

    def test_handle_non_command(self, bridge, temp_db):
        """Test handling non-command text."""
        bridge.db = temp_db

        message = {
            "message": {
                "text": "Just some text",
                "chat": {"id": 123456}
            }
        }

        result = bridge.handle_telegram(message)
        assert result["ok"] is False

    def test_handle_empty_command(self, bridge, temp_db):
        """Test handling empty /task command."""
        bridge.db = temp_db

        message = {
            "message": {
                "text": "/task",
                "chat": {"id": 123456}
            }
        }

        result = bridge.handle_telegram(message)
        assert result["ok"] is False


class TestSlackHandler:
    """Tests for Slack command handling."""

    def test_handle_valid_command(self, bridge, temp_db):
        """Test creating task from valid Slack command."""
        bridge.db = temp_db

        payload = {
            "command": "/forge-task",
            "text": "Fix login bug - Button not responding",
            "user_id": "U123456",
            "user_name": "testuser",
            "channel_id": "C123456"
        }

        result = bridge.handle_slack(payload)
        assert result["ok"] is True
        # Slack returns task_id in the text and attachments
        assert "TC-" in result["text"] or any(
            "TC-" in str(f.get("value", ""))
            for a in result.get("attachments", [])
            for f in a.get("fields", [])
        )

    def test_handle_empty_command(self, bridge, temp_db):
        """Test handling empty Slack command."""
        bridge.db = temp_db

        payload = {
            "command": "/forge-task",
            "text": "",
            "user_id": "U123456"
        }

        result = bridge.handle_slack(payload)
        assert result["ok"] is False

    def test_handle_priority_detection(self, bridge, temp_db):
        """Test automatic priority detection from title."""
        bridge.db = temp_db

        # Bug keywords should set high priority
        payload = {
            "command": "/forge-task",
            "text": "Fix bug in login",
            "user_id": "U123456",
            "user_name": "testuser"
        }

        result = bridge.handle_slack(payload)
        assert result["ok"] is True
        # Priority is in the text
        assert "high" in result["text"].lower()

    def test_handle_critical_detection(self, bridge, temp_db):
        """Test automatic critical priority detection."""
        bridge.db = temp_db

        payload = {
            "command": "/forge-task",
            "text": "Critical crash in payment",
            "user_id": "U123456",
            "user_name": "testuser"
        }

        result = bridge.handle_slack(payload)
        assert result["ok"] is True
        # Priority is in the text
        assert "critical" in result["text"].lower()


class TestTaskStatus:
    """Tests for task status queries."""

    def test_get_task_status_not_found(self, bridge, temp_db):
        """Test status for non-existent task."""
        bridge.db = temp_db

        result = bridge.get_task_status_for_user("TC-99999999")
        assert "not found" in result

    def test_get_task_status_found(self, bridge, temp_db):
        """Test status for existing task."""
        bridge.db = temp_db

        # Create a task first
        task_id = temp_db.create_task(
            title="Test Task",
            description="Test",
            priority="medium"
        )

        result = bridge.get_task_status_for_user(task_id)
        assert task_id in result
        assert "pending" in result


class TestWebhookRoutes:
    """Tests for webhook route creation."""

    def test_create_webhook_routes(self):
        """Test that webhook routes can be created."""
        # Just verify the function exists and is callable
        from forge_harness.task_manager.openclaw_bridge import create_webhook_routes
        assert callable(create_webhook_routes)
