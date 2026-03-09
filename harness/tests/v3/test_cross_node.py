import json
import pytest
from pathlib import Path
from forge_harness.task_manager.database import TaskDatabase
from forge_harness.task_manager.xnode_bridge import XNodeBridge

@pytest.fixture
def cross_node_env(tmp_path):
    """Set up multi-node test environment."""
    sati_root = tmp_path / "sati"
    sati_root.mkdir()
    
    db_path = sati_root / "sati.db"
    db = TaskDatabase(db_path)
    
    bridge = XNodeBridge(forge_root=sati_root)
    bridge.db = db
    
    return sati_root, db, bridge

def test_cross_node_dispatch(cross_node_env):
    """Test sati -> prya task dispatch."""
    sati_root, db, bridge = cross_node_env
    
    # 1. Create task on sati targeting prya
    task_id = db.create_task(
        title="Cross-node work",
        metadata={
            "needs_xnode_dispatch": True,
            "target_node": "prya"
        }
    )
    # Task must be assigned to be dispatched via XNode
    db.claim_task(task_id, "kimi")
    
    # 2. Run bridge cycle
    bridge.process_once()
    
    # 3. Verify bridge writes to outbox
    outbox_file = bridge.outbox_dir / "prya.jsonl"
    assert outbox_file.exists()
    
    lines = outbox_file.read_text().strip().split("\n")
    assert len(lines) == 1
    
    data = json.loads(lines[0])
    assert data["type"] == "task.dispatch"
    assert data["target"]["node"] == "prya"
    assert data["payload"]["task_id"] == task_id
    
    # 4. Verify DB marked as dispatched
    with db._connection() as conn:
        row = conn.execute("SELECT metadata FROM tasks WHERE id = ?", (task_id,)).fetchone()
        metadata = json.loads(row[0])
        assert metadata.get("xnode_dispatched") is True
        assert metadata.get("needs_xnode_dispatch") is None # Should be replaced
