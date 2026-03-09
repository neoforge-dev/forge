"""Tests for Resource Scheduler."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from forge_harness.task_manager.scheduler import NodeResources, ResourceScheduler, get_scheduler


@pytest.fixture
def temp_config(tmp_path):
    """Create temporary config file."""
    config = {
        "nodes": {
            "prya": {
                "ram_gb": 16,
                "cpu_cores": 4,
                "allowed_agents": ["kimi", "glm", "minimax"],
                "forbidden_agents": ["opencode", "claude"],
                "max_concurrent_agents": 2,
            },
            "nova": {
                "ram_gb": 48,
                "cpu_cores": 16,
                "allowed_agents": ["claude", "gemini", "opencode"],
                "forbidden_agents": [],
                "max_concurrent_agents": 4,
            },
            "sati": {
                "ram_gb": 64,
                "cpu_cores": 32,
                "allowed_agents": [],
                "forbidden_agents": [],
                "max_concurrent_agents": 8,
            },
        },
        "agent_resources": {
            "kimi": {"ram_mb": 4096},
            "glm": {"ram_mb": 2048},
            "opencode": {"ram_mb": 8192},
            "claude": {"ram_mb": 8192},
            "minimax": {"ram_mb": 2048},
        },
    }
    config_path = tmp_path / "nodes.yaml"
    config_path.write_text(yaml.dump(config))
    return config_path


@pytest.fixture
def scheduler(temp_config, tmp_path):
    """Create scheduler with temp config."""
    sched = ResourceScheduler(config_path=temp_config, node_name="prya")
    sched.db = MagicMock()
    return sched


def test_node_resources_can_accept_agent():
    """Test NodeResources.can_accept_agent."""
    node = NodeResources(
        host="prya",
        ram_gb=16,
        cpu_cores=4,
        allowed_agents=["kimi", "glm"],
        forbidden_agents=["opencode"],
        max_concurrent_agents=2,
        current_agents=1,
        used_ram_gb=4.0,
    )

    assert node.can_accept_agent("kimi", 2048) is True
    assert node.can_accept_agent("opencode", 2048) is False
    assert node.can_accept_agent("minimax", 2048) is False
    assert node.can_accept_agent("glm", 8192) is True


def test_scheduler_validate_assignment_forbidden(scheduler):
    """Test validation forbids heavy agents on prya."""
    ok, reason = scheduler.validate_assignment("opencode", "prya")

    assert ok is False
    assert "forbidden" in reason.lower()


def test_scheduler_validate_assignment_allowed(scheduler):
    """Test validation allows light agents on prya."""
    scheduler.get_node_resources = MagicMock(
        return_value=NodeResources(
            host="prya",
            ram_gb=16,
            cpu_cores=4,
            allowed_agents=["kimi", "glm"],
            forbidden_agents=["opencode"],
            max_concurrent_agents=2,
            current_agents=0,
            used_ram_gb=0,
        )
    )

    ok, reason = scheduler.validate_assignment("kimi", "prya")

    assert ok is True


def test_scheduler_validate_assignment_not_in_allowed(scheduler):
    """Test validation fails for agent not in allowed list."""
    scheduler.get_node_resources = MagicMock(
        return_value=NodeResources(
            host="prya",
            ram_gb=16,
            cpu_cores=4,
            allowed_agents=["kimi", "glm"],
            forbidden_agents=[],
            max_concurrent_agents=2,
            current_agents=2,
            used_ram_gb=14,
        )
    )

    ok, reason = scheduler.validate_assignment("minimax", "prya")

    assert ok is False
    assert "insufficient resources" in reason.lower()


def test_scheduler_validate_assignment_insufficient_resources(scheduler):
    """Test validation fails with insufficient resources."""
    scheduler.get_node_resources = MagicMock(
        return_value=NodeResources(
            host="prya",
            ram_gb=16,
            cpu_cores=4,
            allowed_agents=["kimi", "glm"],
            forbidden_agents=[],
            max_concurrent_agents=2,
            current_agents=2,
            used_ram_gb=14,
        )
    )

    ok, reason = scheduler.validate_assignment("kimi", "prya")

    assert ok is False
    assert "insufficient resources" in reason.lower()


def test_scheduler_get_node_resources(scheduler):
    """Test getting node resources."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)
    mock_conn.execute.return_value = mock_cursor
    scheduler.db.db_path = Path("/fake/db")

    with patch("sqlite3.connect") as mock_connect:
        mock_connect.return_value = mock_conn
        mock_cursor.fetchall.return_value = []
        resources = scheduler.get_node_resources("prya")

    assert resources.host == "prya"
    assert resources.ram_gb == 16
    assert resources.max_concurrent_agents == 2


def test_scheduler_find_best_agent(scheduler):
    """Test finding best agent for task."""
    scheduler.get_node_resources = MagicMock(
        return_value=NodeResources(
            host="prya",
            ram_gb=16,
            cpu_cores=4,
            allowed_agents=["kimi", "glm", "minimax"],
            forbidden_agents=["opencode"],
            max_concurrent_agents=2,
            current_agents=0,
            used_ram_gb=2,
        )
    )

    agent = scheduler.find_best_agent("TC-001", preferred_agent="glm")

    assert agent == "glm"


def test_scheduler_get_agent_for_node(scheduler):
    """Test getting allowed agents for node."""
    agents = scheduler.get_agent_for_node("prya")

    assert "kimi" in agents
    assert "glm" in agents
    assert "opencode" not in agents


def test_scheduler_get_optimal_node_for_agent(scheduler):
    """Test finding optimal node for agent."""
    scheduler.get_node_resources = MagicMock(
        side_effect=[
            NodeResources("prya", 16, 4, ["kimi", "glm", "minimax"], [], 2, 1, 8),
            NodeResources("nova", 48, 16, ["claude", "gemini", "opencode"], [], 4, 2, 32),
            NodeResources("sati", 64, 32, [], [], 8, 0, 0),
        ]
    )

    node = scheduler.get_optimal_node_for_agent("claude")

    assert node == "sati"


def test_scheduler_default_config(tmp_path):
    """Test scheduler with default config when file doesn't exist."""
    sched = ResourceScheduler(config_path=tmp_path / "nonexistent.yaml", node_name="sati")

    resources = sched.get_node_resources()

    assert resources.ram_gb == 64
    assert resources.max_concurrent_agents == 4


def test_get_scheduler_singleton():
    """Test get_scheduler returns singleton."""
    global _scheduler_instance
    _scheduler_instance = None

    sched1 = get_scheduler()
    sched2 = get_scheduler()

    assert sched1 is sched2
