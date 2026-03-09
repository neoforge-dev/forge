import sqlite3
import pytest
from forge_harness.task_manager.database import TaskDatabase

@pytest.fixture
def db(tmp_path):
    """Set up test database."""
    db_path = tmp_path / "test_tasks.db"
    return TaskDatabase(db_path)

def test_task_lifecycle(db):
    """Test full task flow: create -> claim -> complete."""
    # 1. Create
    task_id = db.create_task(
        title="Integration Test Task",
        description="Verify the lifecycle works",
        priority="high"
    )
    assert task_id.startswith("TC-")
    
    # Verify pending
    tasks = db.get_pending_tasks()
    assert any(t["id"] == task_id for t in tasks)
    
    # 2. Claim
    success = db.claim_task(task_id, "kimi")
    assert success is True
    
    # Verify assigned
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, assigned_agent FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert row["status"] == "assigned"
        assert row["assigned_agent"] == "kimi"
        
        # Verify dispatch record
        dispatch = conn.execute("SELECT status FROM dispatches WHERE task_id = ?", (task_id,)).fetchone()
        assert dispatch["status"] == "claimed"
        
        # Verify agent state
        agent = conn.execute("SELECT status FROM agent_state WHERE agent = ?", ("kimi",)).fetchone()
        assert agent["status"] == "busy"
    
    # 3. Complete
    success = db.complete_task(task_id, "kimi", "All clear")
    assert success is True
    
    # 4. Verify completed
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, completed_at FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert row["status"] == "completed"
        assert row["completed_at"] is not None
        
        # Agent should be idle
        agent = conn.execute("SELECT status FROM agent_state WHERE agent = ?", ("kimi",)).fetchone()
        assert agent["status"] == "idle"

def test_invalid_claims(db):
    """Test claiming unavailable tasks."""
    task_id = db.create_task("Test")
    
    # Claim successfully
    assert db.claim_task(task_id, "agent1") is True
    
    # Try to claim again (should fail as it's already assigned)
    assert db.claim_task(task_id, "agent2") is False
    
    # Try to claim non-existent task
    assert db.claim_task("TC-fake", "agent1") is False
