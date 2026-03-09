import yaml
import pytest
from pathlib import Path
from forge_harness.task_manager.scheduler import ResourceScheduler
from forge_harness.task_manager.database import TaskDatabase

@pytest.fixture
def scheduler_env(tmp_path):
    """Set up scheduler with temp config and DB."""
    config_path = tmp_path / "nodes.yaml"
    db_path = tmp_path / "state.db"
    db = TaskDatabase(db_path)
    
    config = {
        "nodes": {
            "prya": {
                "ram_gb": 16,
                "cpu_cores": 4,
                "allowed_agents": ["kimi", "gemini"],
                "forbidden_agents": ["opencode"],
                "max_concurrent_agents": 2
            },
            "sati": {
                "ram_gb": 64,
                "cpu_cores": 16,
                "allowed_agents": [],
                "forbidden_agents": [],
                "max_concurrent_agents": 10
            }
        },
        "agent_resources": {
            "kimi": {"ram_mb": 512},
            "gemini": {"ram_mb": 2048},
            "opencode": {"ram_mb": 8192}
        }
    }
    config_path.write_text(yaml.dump(config))
    
    scheduler = ResourceScheduler(config_path=config_path, node_name="sati")
    # Mock DB instance in scheduler
    scheduler.db = db
    
    return scheduler, db

def test_resource_constraints(scheduler_env):
    """Test node-specific agent constraints."""
    scheduler, db = scheduler_env
    
    # 1. Test forbidden agent on prya
    ok, reason = scheduler.validate_assignment("opencode", "prya")
    assert not ok
    assert "forbidden" in reason.lower()
    
    # 2. Test allowed agent on prya
    ok, reason = scheduler.validate_assignment("kimi", "prya")
    assert ok
    
    # 3. Test agent not in allowed list (if allowed list is defined)
    # We'll use a hypothetical agent 'minimax' which isn't in prya's allowed list
    ok, reason = scheduler.validate_assignment("minimax", "prya")
    assert not ok
    assert "not in allowed list" in reason.lower()

def test_ram_constraints(scheduler_env, tmp_path):
    """Test RAM-based scheduling."""
    # Create a config with very low RAM for a node
    config_path = tmp_path / "low_ram_nodes.yaml"
    config = {
        "nodes": {
            "tiny": {
                "ram_gb": 1,
                "cpu_cores": 1,
                "allowed_agents": [],
                "forbidden_agents": [],
                "max_concurrent_agents": 10
            }
        },
        "agent_resources": {
            "heavy": {"ram_mb": 2048}, # 2GB
            "light": {"ram_mb": 256}   # 0.25GB
        }
    }
    config_path.write_text(yaml.dump(config))
    
    scheduler = ResourceScheduler(config_path=config_path, node_name="tiny")
    scheduler.db = TaskDatabase(tmp_path / "tiny.db")
    
    # Heavy agent should fail (2GB > 1GB)
    ok, reason = scheduler.validate_assignment("heavy", "tiny")
    assert not ok
    assert "insufficient resources" in reason.lower()
    
    # Light agent should succeed
    ok, reason = scheduler.validate_assignment("light", "tiny")
    assert ok
