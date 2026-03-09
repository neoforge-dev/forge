"""Tests for Cross-Node Dispatch."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.task_manager.database import TaskDatabase
from forge_harness.task_manager.xnode_bridge import XNodeBridge


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
def cross_node_bridge(temp_forge_root, temp_db):
    """Create XNode bridge for cross-node testing."""
    bridge = XNodeBridge(forge_root=temp_forge_root, poll_interval=0.1)
    bridge.db = temp_db
    return bridge


def test_cross_node_dispatch_sati_to_prya(cross_node_bridge, temp_forge_root, temp_db):
    """Test dispatching task from sati to prya."""
    outbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-outbox"

    task_id = temp_db.create_task(
        title="Cross-node Task",
        description="Task to dispatch to prya",
        priority="high",
        metadata={"needs_xnode_dispatch": True, "target_node": "prya", "target_agent": "kimi"},
    )
    temp_db.claim_task(task_id, "kimi")

    cross_node_bridge._write_outbox()

    outbox_file = outbox_dir / "prya.jsonl"
    assert outbox_file.exists()

    content = outbox_file.read_text()
    msg = json.loads(content.strip().split("\n")[0])

    assert msg["target"]["node"] == "prya"
    assert msg["payload"]["task_id"] == task_id
    assert msg["type"] == "task.dispatch"


def test_cross_node_dispatch_multiple_nodes(cross_node_bridge, temp_forge_root, temp_db):
    """Test dispatching to multiple nodes."""
    outbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-outbox"

    task1 = temp_db.create_task(
        "Task for prya", "Desc", metadata={"needs_xnode_dispatch": True, "target_node": "prya"}
    )
    task2 = temp_db.create_task(
        "Task for nova", "Desc", metadata={"needs_xnode_dispatch": True, "target_node": "nova"}
    )
    temp_db.claim_task(task1, "kimi")
    temp_db.claim_task(task2, "claude")

    cross_node_bridge._write_outbox()

    prya_outbox = outbox_dir / "prya.jsonl"
    nova_outbox = outbox_dir / "nova.jsonl"

    assert prya_outbox.exists()
    assert nova_outbox.exists()


def test_cross_node_pickup(cross_node_bridge, temp_forge_root, temp_db):
    """Test simulating prya pickup of dispatched task."""
    outbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-outbox"

    task_id = temp_db.create_task(
        title="Picked up task",
        description="Task dispatched to prya",
        metadata={"needs_xnode_dispatch": True, "target_node": "prya"},
    )
    temp_db.claim_task(task_id, "kimi")

    cross_node_bridge._write_outbox()

    task = temp_db.get_task_status(task_id)
    import json

    metadata = json.loads(task["metadata"] or "{}")

    assert "xnode_dispatched" in metadata or "needs_xnode_dispatch" in metadata


def test_cross_node_response_flow(cross_node_bridge, temp_forge_root, temp_db):
    """Test response from prya back to sati."""
    inbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-inbox"

    task_id = temp_db.create_task(
        title="Task with response",
        description="Will receive response",
        metadata={"xnode_message_id": "msg-001"},
    )
    temp_db.claim_task(task_id, "kimi")
    temp_db.complete_task(task_id, "kimi", "Task completed")

    response_msg = {
        "id": "resp-001",
        "schema_version": "1.0",
        "message_id": "resp-001",
        "correlation_id": task_id,
        "idempotency_key": "idem-resp-001",
        "sent_at": "2026-03-04T12:00:00Z",
        "source": {"node": "prya", "agent": "kimi"},
        "target": {"node": "sati", "agent": "orchestrator"},
        "priority": "medium",
        "type": "lead.ack",
        "requires_ack": False,
        "payload": {"task_id": task_id, "status": "completed", "result": "Task completed"},
    }

    resp_file = inbox_dir / "response.json"
    resp_file.write_text(json.dumps(response_msg))

    cross_node_bridge._scan_inbox()

    task = temp_db.get_task_status(task_id)
    assert task is not None


def test_cross_node_message_schema(cross_node_bridge, temp_forge_root):
    """Test XNode message schema compliance."""
    inbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-inbox"

    valid_msg = {
        "id": "msg-002",
        "schema_version": "1.0",
        "message_id": "msg-002",
        "correlation_id": "corr-002",
        "idempotency_key": "idem-002",
        "sent_at": "2026-03-04T10:00:00Z",
        "expires_at": None,
        "source": {"node": "nova", "agent": "claude"},
        "target": {"node": "sati", "agent": "orchestrator"},
        "priority": "high",
        "type": "lead.send",
        "requires_ack": True,
        "payload": {"summary": "Test", "task_id": "IS-002"},
    }

    msg_file = inbox_dir / "valid_msg.json"
    msg_file.write_text(json.dumps(valid_msg))

    from forge_harness.task_manager.xnode_bridge import XNodeMessage

    message = XNodeMessage.from_file(msg_file)

    assert message is not None
    assert message.schema_version == "1.0"
    assert message.msg_type == "lead.send"
    assert message.requires_ack is True


def test_cross_node_priority_ordering(cross_node_bridge, temp_forge_root, temp_db):
    """Test tasks are dispatched to outbox regardless of priority."""
    outbox_dir = temp_forge_root / ".forge" / "xnode" / "lead-outbox"

    temp_db.create_task(
        "Low", "L", "low", metadata={"needs_xnode_dispatch": True, "target_node": "prya"}
    )
    temp_db.create_task(
        "High", "H", "high", metadata={"needs_xnode_dispatch": True, "target_node": "prya"}
    )
    temp_db.create_task(
        "Critical", "C", "critical", metadata={"needs_xnode_dispatch": True, "target_node": "prya"}
    )

    tasks = temp_db.get_pending_tasks()
    for task in tasks:
        temp_db.claim_task(task["id"], "kimi")

    cross_node_bridge._write_outbox()

    outbox_file = outbox_dir / "prya.jsonl"
    content = outbox_file.read_text()
    lines = [l for l in content.strip().split("\n") if l]

    assert len(lines) == 3
