"""Fleet Workflow Tests - Comprehensive test suite for fleet workflow orchestrator.

Tests cover:
- DAG operations (topological sort, parallel groups, cycle detection)
- State management (transitions, persistence)
- Workflow engine (execution, retry, timeout)
- Recovery manager (health checks, incident tracking)
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from .dag import DAG, TaskNode
from .engine import WorkflowDefinition, WorkflowEngine
from .recovery import HealthIncident, RecoveryConfig, RecoveryManager
from .state import VALID_TRANSITIONS, AgentState, AgentStateRecord, FleetStateManager

# =============================================================================
# DAG Tests
# =============================================================================


class TestDAG:
    """Test DAG operations."""

    def test_add_node(self):
        """Test adding nodes to DAG."""
        dag = DAG()
        node = TaskNode(id="task1", name="Test Task")
        dag.add_node(node)

        assert "task1" in dag._nodes
        assert dag._nodes["task1"].name == "Test Task"

    def test_add_duplicate_node_raises(self):
        """Test that adding duplicate node raises error."""
        dag = DAG()
        node = TaskNode(id="task1", name="Test Task")
        dag.add_node(node)

        with pytest.raises(ValueError, match="already exists"):
            dag.add_node(node)

    def test_add_edge(self):
        """Test adding edges between nodes."""
        dag = DAG()
        dag.add_node(TaskNode(id="a", name="Task A"))
        dag.add_node(TaskNode(id="b", name="Task B", dependencies=["a"]))

        assert "b" in dag._edges["a"]
        assert "a" in dag._reverse_edges["b"]

    def test_cycle_detection(self):
        """Test that cycles are detected and rejected."""
        dag = DAG()
        dag.add_node(TaskNode(id="a", name="Task A"))
        dag.add_node(TaskNode(id="b", name="Task B"))

        # This would create a cycle: a -> b -> a
        dag.add_edge("a", "b")
        with pytest.raises(ValueError, match="cycle"):
            dag.add_edge("b", "a")

    def test_topological_sort_linear(self):
        """Test topological sort with linear dependencies."""
        dag = DAG()
        dag.add_node(TaskNode(id="a", name="Task A"))
        dag.add_node(TaskNode(id="b", name="Task B", dependencies=["a"]))
        dag.add_node(TaskNode(id="c", name="Task C", dependencies=["b"]))

        sorted_nodes = dag.topological_sort()
        ids = [n.id for n in sorted_nodes]

        assert ids == ["a", "b", "c"]

    def test_topological_sort_diamond(self):
        """Test topological sort with diamond dependency pattern."""
        dag = DAG()
        dag.add_node(TaskNode(id="a", name="Task A"))
        dag.add_node(TaskNode(id="b", name="Task B", dependencies=["a"]))
        dag.add_node(TaskNode(id="c", name="Task C", dependencies=["a"]))
        dag.add_node(TaskNode(id="d", name="Task D", dependencies=["b", "c"]))

        sorted_nodes = dag.topological_sort()
        ids = [n.id for n in sorted_nodes]

        assert ids[0] == "a"  # First
        assert ids[-1] == "d"  # Last
        assert set(ids[1:3]) == {"b", "c"}  # Middle in any order

    def test_topological_sort_cycle_raises(self):
        """Test that topological sort detects cycles."""
        dag = DAG()
        # Create a manual cycle by manipulating internal structures
        dag.add_node(TaskNode(id="a", name="Task A", dependencies=["b"]))
        dag.add_node(TaskNode(id="b", name="Task B", dependencies=["a"]))

        # Force add to bypass cycle detection
        dag._edges["a"] = {"b"}
        dag._edges["b"] = {"a"}

        with pytest.raises(ValueError, match="Cycle"):
            dag.topological_sort()

    def test_get_ready_tasks(self):
        """Test getting tasks ready for execution."""
        dag = DAG()
        dag.add_node(TaskNode(id="a", name="Task A"))
        dag.add_node(TaskNode(id="b", name="Task B", dependencies=["a"]))
        dag.add_node(TaskNode(id="c", name="Task C"))

        ready = dag.get_ready_tasks()
        ids = {n.id for n in ready}

        assert ids == {"a", "c"}  # No dependencies
        assert "b" not in ids  # Depends on a

    def test_get_ready_tasks_with_completed_deps(self):
        """Test ready tasks after completing dependencies."""
        dag = DAG()
        dag.add_node(TaskNode(id="a", name="Task A"))
        dag.add_node(TaskNode(id="b", name="Task B", dependencies=["a"]))

        # Mark a as completed
        dag._nodes["a"].status = "completed"

        ready = dag.get_ready_tasks()
        ids = {n.id for n in ready}

        assert "b" in ids  # Now ready since a is completed

    def test_get_parallel_groups(self):
        """Test parallel execution groups."""
        dag = DAG()
        dag.add_node(TaskNode(id="a", name="Task A"))
        dag.add_node(TaskNode(id="b", name="Task B"))
        dag.add_node(TaskNode(id="c", name="Task C", dependencies=["a", "b"]))
        dag.add_node(TaskNode(id="d", name="Task D", dependencies=["c"]))

        groups = dag.get_parallel_groups()
        group_ids = [[n.id for n in group] for group in groups]

        assert len(groups) == 3
        assert set(group_ids[0]) == {"a", "b"}  # Level 1: parallel
        assert group_ids[1] == ["c"]  # Level 2: sequential
        assert group_ids[2] == ["d"]  # Level 3: sequential

    def test_get_critical_path(self):
        """Test critical path calculation."""
        dag = DAG()
        dag.add_node(TaskNode(id="a", name="Task A"))
        dag.add_node(TaskNode(id="b", name="Task B", dependencies=["a"]))
        dag.add_node(TaskNode(id="c", name="Task C", dependencies=["a"]))
        dag.add_node(TaskNode(id="d", name="Task D", dependencies=["b"]))
        dag.add_node(TaskNode(id="e", name="Task E", dependencies=["c"]))

        critical = dag.get_critical_path()
        ids = [n.id for n in critical]

        assert ids[0] == "a"
        assert ids[-1] in {"d", "e"}
        assert len(ids) == 3  # a -> b/c -> d/e

    def test_dag_serialization(self):
        """Test DAG serialization and deserialization."""
        dag = DAG()
        dag.add_node(TaskNode(
            id="task1",
            name="Test Task",
            agent_type="claude",
            agent_capabilities=["processing"],
            retry_count=5,
        ))

        data = dag.to_dict()
        restored = DAG.from_dict(data)

        assert "task1" in restored._nodes
        assert restored._nodes["task1"].agent_type == "claude"
        assert restored._nodes["task1"].retry_count == 5


# =============================================================================
# State Management Tests
# =============================================================================


class TestAgentStateRecord:
    """Test agent state record operations."""

    def test_valid_transitions(self):
        """Test valid state transitions."""
        record = AgentStateRecord(agent_id="test-agent")

        assert record.state == AgentState.IDLE
        assert record.can_transition_to(AgentState.ASSIGNED)
        assert record.can_transition_to(AgentState.PAUSED)
        assert not record.can_transition_to(AgentState.WORKING)  # Must go through ASSIGNED

    def test_transition_success(self):
        """Test successful state transition."""
        record = AgentStateRecord(agent_id="test-agent")

        success = record.transition(AgentState.ASSIGNED, "Task assigned")

        assert success
        assert record.state == AgentState.ASSIGNED
        assert len(record.state_history) == 1
        assert record.state_history[0]["from"] == "idle"
        assert record.state_history[0]["to"] == "assigned"

    def test_transition_failure(self):
        """Test invalid state transition."""
        record = AgentStateRecord(agent_id="test-agent")

        success = record.transition(AgentState.COMPLETED)  # Can't go idle -> completed

        assert not success
        assert record.state == AgentState.IDLE

    def test_all_valid_transitions_defined(self):
        """Test that all states have transition definitions."""
        for state in AgentState:
            assert state in VALID_TRANSITIONS, f"Missing transitions for {state}"

    def test_serialization(self):
        """Test state record serialization."""
        record = AgentStateRecord(
            agent_id="test-agent",
            state=AgentState.WORKING,
            current_task="task-123",
            capabilities=["processing", "analysis"],
        )
        record.last_heartbeat = datetime.now(UTC)

        data = record.to_dict()

        assert data["agent_id"] == "test-agent"
        assert data["state"] == "working"
        assert data["current_task"] == "task-123"
        assert data["capabilities"] == ["processing", "analysis"]

    def test_deserialization(self):
        """Test state record deserialization."""
        data = {
            "agent_id": "test-agent",
            "state": "working",
            "current_task": "task-123",
            "capabilities": ["processing"],
            "last_heartbeat": datetime.now(UTC).isoformat(),
            "last_state_change": datetime.now(UTC).isoformat(),
            "state_history": [],
            "metadata": {},
        }

        record = AgentStateRecord.from_dict(data)

        assert record.agent_id == "test-agent"
        assert record.state == AgentState.WORKING
        assert record.last_heartbeat is not None


class TestFleetStateManager:
    """Test fleet state manager operations."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for state persistence."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def state_manager(self, temp_dir):
        """Create state manager with temp directory."""
        return FleetStateManager(state_dir=temp_dir)

    def test_register_agent(self, state_manager):
        """Test agent registration."""
        agent = state_manager.register_agent("agent-1", capabilities=["processing"])

        assert agent.agent_id == "agent-1"
        assert agent.state == AgentState.IDLE
        assert agent.capabilities == ["processing"]

    def test_register_duplicate_returns_existing(self, state_manager):
        """Test that registering duplicate returns existing agent."""
        agent1 = state_manager.register_agent("agent-1")
        agent2 = state_manager.register_agent("agent-1")

        assert agent1 is agent2

    def test_get_agent(self, state_manager):
        """Test getting agent by ID."""
        state_manager.register_agent("agent-1")

        agent = state_manager.get_agent("agent-1")
        assert agent is not None
        assert agent.agent_id == "agent-1"

    def test_get_nonexistent_agent(self, state_manager):
        """Test getting non-existent agent returns None."""
        agent = state_manager.get_agent("nonexistent")
        assert agent is None

    def test_get_agents_by_state(self, state_manager):
        """Test filtering agents by state."""
        state_manager.register_agent("agent-1")
        state_manager.register_agent("agent-2")

        idle_agents = state_manager.get_agents_by_state(AgentState.IDLE)

        assert len(idle_agents) == 2

    def test_get_available_agents(self, state_manager):
        """Test getting available agents."""
        state_manager.register_agent("agent-1", capabilities=["processing"])
        state_manager.register_agent("agent-2", capabilities=["analysis"])

        available = state_manager.get_available_agents(["processing"])

        assert len(available) == 1
        assert available[0].agent_id == "agent-1"

    def test_transition_agent(self, state_manager):
        """Test agent state transition."""
        state_manager.register_agent("agent-1")

        success = state_manager.transition_agent("agent-1", AgentState.ASSIGNED)

        assert success
        assert state_manager.get_agent("agent-1").state == AgentState.ASSIGNED

    def test_assign_task(self, state_manager):
        """Test task assignment."""
        state_manager.register_agent("agent-1")

        success = state_manager.assign_task("agent-1", "task-123", "workflow-1")

        assert success
        agent = state_manager.get_agent("agent-1")
        assert agent.state == AgentState.ASSIGNED
        assert agent.current_task == "task-123"

    def test_assign_task_to_busy_agent_fails(self, state_manager):
        """Test that assigning to busy agent fails."""
        state_manager.register_agent("agent-1")
        state_manager.assign_task("agent-1", "task-1")

        success = state_manager.assign_task("agent-1", "task-2")

        assert not success

    def test_release_agent(self, state_manager):
        """Test releasing agent from task."""
        state_manager.register_agent("agent-1")
        state_manager.assign_task("agent-1", "task-123")

        success = state_manager.release_agent("agent-1", "completed")

        assert success
        agent = state_manager.get_agent("agent-1")
        assert agent.state == AgentState.COMPLETED
        assert agent.current_task is None

    def test_update_heartbeat(self, state_manager):
        """Test heartbeat update."""
        state_manager.register_agent("agent-1")

        state_manager.update_heartbeat("agent-1", context_percentage=50)

        agent = state_manager.get_agent("agent-1")
        assert agent.context_percentage == 50
        assert agent.last_heartbeat is not None

    def test_unregister_agent(self, state_manager):
        """Test agent unregistration."""
        state_manager.register_agent("agent-1")

        state_manager.unregister_agent("agent-1")

        assert state_manager.get_agent("agent-1") is None

    def test_persistence(self, temp_dir):
        """Test state persistence to disk."""
        manager1 = FleetStateManager(state_dir=temp_dir)
        manager1.register_agent("agent-1", capabilities=["processing"])
        manager1.transition_agent("agent-1", AgentState.ASSIGNED)

        # Create new manager instance (simulates restart)
        manager2 = FleetStateManager(state_dir=temp_dir)

        agent = manager2.get_agent("agent-1")
        assert agent is not None
        assert agent.agent_id == "agent-1"
        assert agent.state == AgentState.ASSIGNED

    def test_fleet_summary(self, state_manager):
        """Test fleet summary generation."""
        state_manager.register_agent("agent-1")
        state_manager.register_agent("agent-2")
        state_manager.assign_task("agent-1", "task-1")

        summary = state_manager.get_fleet_summary()

        assert summary["total_agents"] == 2
        assert summary["by_state"]["idle"] == 1
        assert summary["by_state"]["assigned"] == 1
        assert summary["available"] == 1


# =============================================================================
# Workflow Engine Tests
# =============================================================================


class TestWorkflowEngine:
    """Test workflow engine operations."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def engine(self, temp_dir):
        """Create workflow engine."""
        state_manager = FleetStateManager(state_dir=temp_dir)
        return WorkflowEngine(state_manager=state_manager, max_parallel=2)

    @pytest.fixture
    def simple_workflow(self):
        """Create simple workflow definition."""
        dag = DAG()
        dag.add_node(TaskNode(id="task1", name="Task 1"))
        dag.add_node(TaskNode(id="task2", name="Task 2", dependencies=["task1"]))

        return WorkflowDefinition(
            id="test-workflow",
            name="Test Workflow",
            dag=dag,
        )

    def test_register_workflow(self, engine, simple_workflow):
        """Test workflow registration."""
        engine.register_workflow(simple_workflow)

        assert "test-workflow" in engine._workflows

    @pytest.mark.asyncio
    async def test_start_execution(self, engine, simple_workflow):
        """Test starting workflow execution."""
        engine.register_workflow(simple_workflow)

        execution = await engine.start_execution("test-workflow")

        assert execution.execution_id is not None
        assert execution.workflow_id == "test-workflow"
        assert execution.status == "running"

    @pytest.mark.asyncio
    async def test_start_nonexistent_workflow_raises(self, engine):
        """Test that starting nonexistent workflow raises error."""
        with pytest.raises(ValueError, match="not found"):
            await engine.start_execution("nonexistent")

    @pytest.mark.asyncio
    async def test_get_execution(self, engine, simple_workflow):
        """Test getting execution by ID."""
        engine.register_workflow(simple_workflow)
        execution = await engine.start_execution("test-workflow")

        retrieved = engine.get_execution(execution.execution_id)

        assert retrieved is not None
        assert retrieved.execution_id == execution.execution_id

    @pytest.mark.asyncio
    async def test_cancel_execution(self, engine, simple_workflow):
        """Test cancelling execution."""
        engine.register_workflow(simple_workflow)
        execution = await engine.start_execution("test-workflow")

        success = engine.cancel_execution(execution.execution_id)

        assert success
        assert execution.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_non_running_execution_fails(self, engine, simple_workflow):
        """Test that cancelling non-running execution fails."""
        engine.register_workflow(simple_workflow)
        execution = await engine.start_execution("test-workflow")
        engine.cancel_execution(execution.execution_id)

        success = engine.cancel_execution(execution.execution_id)

        assert not success

    def test_register_task_handler(self, engine):
        """Test task handler registration."""
        async def handler(task):
            return {"result": "success"}

        engine.register_task_handler("test", handler)

        assert "test" in engine._task_handlers


# =============================================================================
# Recovery Manager Tests
# =============================================================================


class TestRecoveryManager:
    """Test recovery manager operations."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def recovery_manager(self, temp_dir):
        """Create recovery manager."""
        state_manager = FleetStateManager(state_dir=temp_dir)
        config = RecoveryConfig(
            stale_threshold_minutes=30,
            health_check_interval_seconds=1,
        )
        return RecoveryManager(state_manager, config)

    def test_is_stale(self, recovery_manager):
        """Test stale agent detection."""
        # Fresh heartbeat
        fresh = datetime.now(UTC)
        assert not recovery_manager._is_stale(fresh)

        # Old heartbeat
        old = datetime.now(UTC) - timedelta(minutes=45)
        assert recovery_manager._is_stale(old)

        # No heartbeat
        assert recovery_manager._is_stale(None)

    def test_get_incidents_empty(self, recovery_manager):
        """Test getting incidents when none exist."""
        incidents = recovery_manager.get_incidents()

        assert incidents == []

    def test_get_incidents_filtered(self, recovery_manager):
        """Test filtering incidents."""
        recovery_manager._incidents.append(
            HealthIncident(agent_id="agent-1", incident_type="stale")
        )
        recovery_manager._incidents.append(
            HealthIncident(agent_id="agent-2", incident_type="overloaded")
        )

        stale_incidents = recovery_manager.get_incidents(incident_type="stale")

        assert len(stale_incidents) == 1
        assert stale_incidents[0].agent_id == "agent-1"

    def test_get_recovery_summary(self, recovery_manager, temp_dir):
        """Test recovery summary."""
        state_manager = FleetStateManager(state_dir=temp_dir)
        state_manager.register_agent("agent-1")

        summary = recovery_manager.get_recovery_summary()

        assert "total_incidents" in summary
        assert "unresolved_incidents" in summary
        assert "monitoring_active" in summary

    @pytest.mark.asyncio
    async def test_can_restart_rate_limiting(self, recovery_manager):
        """Test restart rate limiting."""
        # First restart should be allowed
        assert await recovery_manager._can_restart("agent-1")

        # Record some restarts
        now = datetime.now(UTC)
        recovery_manager._restart_counts["agent-1"] = [now] * 5

        # Should be blocked now
        assert not await recovery_manager._can_restart("agent-1")

    @pytest.mark.asyncio
    async def test_manual_recover_nonexistent_agent(self, recovery_manager):
        """Test manual recovery for nonexistent agent."""
        success = await recovery_manager.manual_recover("nonexistent")

        assert not success


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_full_workflow_execution():
    """Integration test: Full workflow execution with all components."""
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)

        # Initialize components
        state_manager = FleetStateManager(state_dir=state_dir)
        engine = WorkflowEngine(state_manager=state_manager, max_parallel=2)

        # Register agents
        state_manager.register_agent("agent-1", capabilities=["processing"])
        state_manager.register_agent("agent-2", capabilities=["processing"])

        # Create workflow
        dag = DAG()
        dag.add_node(TaskNode(id="task1", name="Task 1", agent_capabilities=["processing"]))
        dag.add_node(TaskNode(id="task2", name="Task 2", dependencies=["task1"]))

        workflow = WorkflowDefinition(
            id="integration-test",
            name="Integration Test",
            dag=dag,
        )
        engine.register_workflow(workflow)

        # Register mock task handler
        async def mock_handler(task):
            await asyncio.sleep(0.1)
            return {"status": "completed"}

        engine.register_task_handler("default", mock_handler)

        # Execute workflow
        execution = await engine.start_execution("integration-test")

        # Wait for completion (with timeout)
        for _ in range(50):  # 5 seconds max
            if execution.status != "running":
                break
            await asyncio.sleep(0.1)

        # Verify execution completed
        assert execution.status in ("completed", "failed")


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_dag_with_no_nodes(self):
        """Test empty DAG."""
        dag = DAG()
        assert dag.topological_sort() == []
        assert dag.get_ready_tasks() == []

    def test_dag_single_node(self):
        """Test DAG with single node."""
        dag = DAG()
        dag.add_node(TaskNode(id="only", name="Only Task"))

        assert len(dag.topological_sort()) == 1
        assert len(dag.get_ready_tasks()) == 1

    def test_state_transition_all_paths(self):
        """Test all valid state transition paths."""
        for start_state in AgentState:
            for end_state in VALID_TRANSITIONS[start_state]:
                record = AgentStateRecord(agent_id="test", state=start_state)
                success = record.transition(end_state)
                assert success, f"Failed: {start_state} -> {end_state}"

    def test_workflow_with_long_dependency_chain(self):
        """Test workflow with long dependency chain."""
        dag = DAG()
        prev_id = None

        # Create chain of 10 tasks
        for i in range(10):
            task_id = f"task-{i}"
            deps = [prev_id] if prev_id else []
            dag.add_node(TaskNode(id=task_id, name=f"Task {i}", dependencies=deps))
            prev_id = task_id

        groups = dag.get_parallel_groups()
        assert len(groups) == 10  # Each task is its own level

    def test_concurrent_state_transitions(self, temp_dir):
        """Test handling of concurrent state transitions."""
        state_manager = FleetStateManager(state_dir=temp_dir)
        state_manager.register_agent("agent-1")

        # Both try to transition the same agent
        success1 = state_manager.transition_agent("agent-1", AgentState.ASSIGNED)
        success2 = state_manager.transition_agent("agent-1", AgentState.PAUSED)

        # Second should fail since agent is now ASSIGNED
        assert success1
        # PAUSED is not a valid transition from ASSIGNED
        assert not success2


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
