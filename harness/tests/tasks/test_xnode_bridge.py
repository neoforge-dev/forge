"""Tests for XNode Bridge."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.task_manager.database import TaskDatabase
from forge_harness.task_manager.xnode_bridge import XNodeBridge, XNodeMessage


@pytest.fixture
def temp_forge_root(tmp_path):
    """Create temporary forge root."""
    return tmp_path


@pytest.fixture
def temp_db(tmp_path):
    """Create temporary database."""
    db = TaskDatabase(db_path=tmp_path / "test.db")
    yield db
    if db.db_path.exists():
        db.db_path.unlink()


@pytest.fixture
def xnode_bridge(temp_forge_root, temp_db):
    """Create XNode bridge with temp directories."""
    bridge = XNodeBridge(forge_root=temp_forge_root, poll_interval=0.1)
    bridge.db = temp_db
    return bridge


@pytest.fixture
def sample_xnode_message():
    """Sample XNode message."""
    return {
        "id": "msg-001",
        "schema_version": "1.0",
        "message_id": "msg-001",
        "correlation_id": "corr-001",
        "idempotency_key": "idem-001",
        "sent_at": "2026-03-04T10:00:00Z",
        "expires_at": None,
        "source": {"node": "nova", "agent": "claude"},
        "target": {"node": "prya", "agent": "kimi"},
        "priority": "high",
        "type": "lead.send",
        "requires_ack": True,
        "payload": {"summary": "Test task from XNode", "task_id": "IS-001"},
    }


def test_xnode_message_from_file(temp_forge_root, sample_xnode_message):
    """Test parsing XNode message from JSON file."""
    msg_file = temp_forge_root / "test_msg.json"
    msg_file.write_text(json.dumps(sample_xnode_message))

    message = XNodeMessage.from_file(msg_file)

    assert message is not None
    assert message.message_id == "msg-001"
    assert message.source_node == "nova"
    assert message.target_node == "prya"
    assert message.priority == "high"
    assert message.payload["summary"] == "Test task from XNode"


def test_xnode_message_invalid_file(temp_forge_root):
    """Test parsing invalid file returns None."""
    msg_file = temp_forge_root / "invalid.json"
    msg_file.write_text("{invalid json}")

    message = XNodeMessage.from_file(msg_file)
    assert message is None


def test_bridge_process_inbox_file(xnode_bridge, temp_forge_root, sample_xnode_message):
    """Test processing inbox file creates task in database."""
    inbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-inbox"

    msg_file = inbox_dir / "test_msg.json"
    msg_file.write_text(json.dumps(sample_xnode_message))

    result = xnode_bridge._process_inbox_file(msg_file)

    assert result is True

    tasks = xnode_bridge.db.get_pending_tasks()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Test task from XNode"
    assert tasks[0]["priority"] == "high"
    assert "xnode_message_id" in tasks[0]["metadata"]


def test_bridge_duplicate_message(xnode_bridge, temp_forge_root, sample_xnode_message):
    """Test skipping already processed messages."""
    inbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-inbox"

    msg_file = inbox_dir / "test_msg.json"
    msg_file.write_text(json.dumps(sample_xnode_message))

    xnode_bridge._process_inbox_file(msg_file)
    xnode_bridge._process_inbox_file(msg_file)

    tasks = xnode_bridge.db.get_pending_tasks()
    assert len(tasks) == 1


def test_bridge_outbox_write(xnode_bridge, temp_forge_root, temp_db):
    """Test writing V3 tasks to outbox."""
    outbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-outbox"

    task_id = temp_db.create_task(
        title="Test Task",
        description="Test",
        priority="medium",
        metadata={"needs_xnode_dispatch": True, "target_node": "prya"},
    )
    temp_db.claim_task(task_id, "kimi")

    xnode_bridge._write_outbox()

    outbox_file = outbox_dir / "prya.jsonl"
    assert outbox_file.exists()

    content = outbox_file.read_text()
    assert "v3-" in content
    assert "Test Task" in content


def test_bridge_scan_inbox_jsonl(xnode_bridge, temp_forge_root, sample_xnode_message):
    """Test scanning inbox for JSONL files."""
    inbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-inbox"

    jsonl_file = inbox_dir / "nova.jsonl"
    with open(jsonl_file, "w") as f:
        f.write(json.dumps(sample_xnode_message) + "\n")

    xnode_bridge._scan_inbox()

    tasks = xnode_bridge.db.get_pending_tasks()
    assert len(tasks) == 1


def test_bridge_process_once(xnode_bridge, temp_forge_root, sample_xnode_message):
    """Test process_once cycles."""
    inbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-inbox"

    msg_file = inbox_dir / "test_msg.json"
    msg_file.write_text(json.dumps(sample_xnode_message))

    xnode_bridge.process_once()

    tasks = xnode_bridge.db.get_pending_tasks()
    assert len(tasks) == 1


def test_bridge_is_running(xnode_bridge):
    """Test is_running returns correct status."""
    assert xnode_bridge.is_running() is False

    xnode_bridge.start()
    assert xnode_bridge.is_running() is True

    xnode_bridge.stop()
    assert xnode_bridge.is_running() is False
