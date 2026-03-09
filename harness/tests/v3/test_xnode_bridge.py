import json
import sqlite3
from pathlib import Path
import pytest
from forge_harness.task_manager.xnode_bridge import XNodeBridge, XNodeMessage
from forge_harness.task_manager.database import TaskDatabase

@pytest.fixture
def test_env(tmp_path):
    """Set up test environment with temp dirs and DB."""
    forge_root = tmp_path / "forge"
    forge_root.mkdir()
    
    db_path = forge_root / "state.db"
    db = TaskDatabase(db_path)
    
    # Create audit_log table if it doesn't exist (it should be in TaskDatabase._init_database)
    # But let's be sure
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, event_type TEXT, details JSON)")
    
    bridge = XNodeBridge(forge_root=forge_root)
    # Mock the db instance in bridge to use our test db
    bridge.db = db
    
    return forge_root, db, bridge

def test_xnode_bridge_sync(test_env):
    """Test file -> SQLite sync."""
    forge_root, db, bridge = test_env
    
    # 1. Write XNode message to inbox
    message_id = "test-msg-123"
    message = {
        "id": "1",
        "message_id": message_id,
        "correlation_id": "corr-123",
        "type": "task.dispatch",
        "priority": "high",
        "source": {"node": "sati", "agent": "user"},
        "target": {"node": "nova", "agent": "kimi"},
        "payload": {
            "summary": "Test XNode Task"
        }
    }
    
    inbox_file = bridge.inbox_dir / f"{message_id}.json"
    inbox_file.write_text(json.dumps(message))
    
    # 2. Process once
    bridge.process_once()
    
    # 3. Verify task created in SQLite
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE metadata LIKE ?", (f'%"{message_id}"%',)).fetchone()
        assert row is not None
        assert row["title"] == "Test XNode Task"
        assert row["priority"] == "high"
        
        # 4. Complete task in DB
        task_id = row["id"]
        db.claim_task(task_id, "kimi")
        db.complete_task(task_id, "kimi", "Work done")
        
    # 5. Verify file was archived
    assert not inbox_file.exists()
    assert (bridge.inbox_dir / "archive" / f"{message_id}.json").exists()

def test_write_outbox(test_env):
    """Test SQLite -> file sync."""
    forge_root, db, bridge = test_env
    
    # 1. Create task needing dispatch in DB
    task_id = db.create_task(
        title="Cross-node task",
        metadata={
            "needs_xnode_dispatch": True,
            "target_node": "prya"
        }
    )
    db.claim_task(task_id, "kimi")
    
    # 2. Process once
    bridge.process_once()
    
    # 3. Verify outbox has file
    outbox_file = bridge.outbox_dir / "prya.jsonl"
    assert outbox_file.exists()
    
    content = outbox_file.read_text().strip()
    data = json.loads(content)
    assert data["correlation_id"] == task_id
    assert data["target"]["node"] == "prya"
    assert data["payload"]["task_id"] == task_id
