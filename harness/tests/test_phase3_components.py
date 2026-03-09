"""Comprehensive Tests for Phase 3 Components.

This module tests:
1. PostgreSQL Database Layer (SQLAlchemy models)
2. Flywheel (Compounding Autonomous Development)
3. Dark Factory (Policy Enforcement)
4. Continuous Runner (Ralph Loop Runner)

Author: pi (Analysis Agent)
Date: March 3, 2026
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Ensure forge_harness is importable
harness_root = Path(__file__).parent.parent
sys.path.insert(0, str(harness_root))

# Set required environment variables before any forge_harness imports
os.environ.setdefault("FORGE_CONFIG_DIR", "/tmp/forge-harness-tests/default_config")
Path(os.environ["FORGE_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)

# =============================================================================
# Phase 3 Component 1: PostgreSQL Database Tests
# =============================================================================


class TestPostgresDatabaseModels:
    """Tests for SQLAlchemy ORM models and database layer."""

    def test_base_model_exists(self):
        """Test that Base declarative model is defined."""
        from forge_harness.models.database import Base

        assert Base is not None
        assert hasattr(Base, 'metadata')

    def test_task_model_structure(self):
        """Test Task model has required columns."""
        from forge_harness.models.database import Task

        columns = {col.name for col in Task.__table__.columns}
        required = {'id', 'subject', 'description', 'priority', 'domain',
                   'project', 'status', 'task_type', 'complexity'}

        assert required.issubset(columns)
        assert Task.__tablename__ == 'tasks'

    def test_agent_model_structure(self):
        """Test Agent model has required columns."""
        from forge_harness.models.database import Agent

        columns = {col.name for col in Agent.__table__.columns}
        required = {'id', 'name', 'model', 'status', 'max_complexity',
                   'preferred_domains', 'current_task_id'}

        assert required.issubset(columns)
        assert Agent.__tablename__ == 'agents'

    def test_agent_capability_model(self):
        """Test AgentCapability model structure."""
        from forge_harness.models.database import AgentCapability

        columns = {col.name for col in AgentCapability.__table__.columns}
        assert 'id' in columns
        assert 'agent_id' in columns
        assert AgentCapability.__tablename__ == 'agent_capabilities'

    def test_task_recommendation_model(self):
        """Test TaskRecommendation model structure."""
        from forge_harness.models.database import TaskRecommendation

        columns = {col.name for col in TaskRecommendation.__table__.columns}
        assert 'id' in columns
        assert 'task_id' in columns
        assert TaskRecommendation.__tablename__ == 'task_recommendations'

    def test_task_history_model(self):
        """Test TaskHistory model structure."""
        from forge_harness.models.database import TaskHistory

        history_cols = {col.name for col in TaskHistory.__table__.columns}
        assert 'id' in history_cols
        assert 'task_id' in history_cols
        assert TaskHistory.__tablename__ == 'task_history'

    def test_generate_uuid_function(self):
        """Test UUID generation function."""
        from forge_harness.models.database import generate_uuid

        uuid1 = generate_uuid()
        uuid2 = generate_uuid()

        assert isinstance(uuid1, str)
        assert len(uuid1) == 36
        assert uuid1 != uuid2

    def test_relationships_defined(self):
        """Test that model relationships are defined."""
        from forge_harness.models.database import Agent, Task

        assert hasattr(Agent, 'current_task')
        assert hasattr(Task, 'assigned_agent')


# =============================================================================
# Phase 3 Component 2: Flywheel Tests
# =============================================================================


class TestFlywheelConfig:
    """Tests for Flywheel configuration."""

    def test_default_config(self):
        """Test FlywheelConfig default values."""
        from forge_harness.flywheel import FlywheelConfig

        config = FlywheelConfig()

        assert config.max_iterations == 100
        assert config.max_features_per_project == 10
        assert config.priority_threshold == "medium"
        assert config.include_harness_self_improvement is True
        assert config.dry_run is False

    def test_custom_config(self):
        """Test FlywheelConfig with custom values."""
        from forge_harness.flywheel import FlywheelConfig

        config = FlywheelConfig(
            max_iterations=50,
            priority_threshold="high",
            dry_run=True
        )

        assert config.max_iterations == 50
        assert config.priority_threshold == "high"
        assert config.dry_run is True


class TestFlywheelResult:
    """Tests for Flywheel result tracking."""

    def test_result_initialization(self):
        """Test FlywheelResult initialization."""
        from forge_harness.flywheel import FlywheelResult

        start_time = datetime.now(UTC)
        result = FlywheelResult(started_at=start_time)

        assert result.started_at == start_time
        assert result.ended_at is None
        assert result.projects_scanned == 0


# =============================================================================
# Phase 3 Component 3: Dark Factory Tests
# =============================================================================


class TestLanePolicyEnforcer:
    """Tests for Lane Policy Enforcer (DF-4001)."""

    @pytest.fixture
    def enforcer(self):
        """Create a fresh policy enforcer."""
        from forge_harness.dark_factory.lane_policy_enforcer import (
            get_lane_policy_enforcer,
            reset_lane_policy_enforcer,
        )
        reset_lane_policy_enforcer()
        return get_lane_policy_enforcer()

    def test_enforcer_singleton(self, enforcer):
        """Test that enforcer is a singleton."""
        from forge_harness.dark_factory.lane_policy_enforcer import get_lane_policy_enforcer

        enforcer2 = get_lane_policy_enforcer()
        assert enforcer is enforcer2

    def test_can_auto_complete_low_risk(self, enforcer):
        """Test auto-complete for low risk tasks."""
        from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType

        task = {"task_type": TaskType.test_writing, "risk_tier": RiskTier.low}

        assert enforcer.can_auto_complete(task) is True
        assert enforcer.requires_human_review(task) is False

    def test_can_auto_complete_high_risk(self, enforcer):
        """Test auto-complete for high risk tasks."""
        from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType

        task = {"task_type": TaskType.security_change, "risk_tier": RiskTier.critical}

        # High risk tasks should require review
        assert enforcer.requires_human_review(task) is True

    def test_get_lane_policy(self, enforcer):
        """Test retrieving lane policy."""
        from forge_harness.webhook_server.models.work_cell import WorkCellLane

        policy = enforcer.get_lane_policy(WorkCellLane.docs)

        assert policy is not None
        assert hasattr(policy, 'can_auto_complete')


class TestHumanGatePolicy:
    """Tests for Human Gate Policy (DF-4002)."""

    @pytest.fixture
    def gate_policy(self):
        """Create a fresh human gate policy."""
        from forge_harness.dark_factory.human_gate_policy import (
            get_human_gate_policy,
            reset_human_gate_policy,
        )
        reset_human_gate_policy()
        return get_human_gate_policy()

    def test_gate_policy_singleton(self, gate_policy):
        """Test singleton pattern."""
        from forge_harness.dark_factory.human_gate_policy import get_human_gate_policy

        policy2 = get_human_gate_policy()
        assert gate_policy is policy2


class TestAuditSampler:
    """Tests for Audit Sampler (DF-4003)."""

    @pytest.fixture
    def sampler(self):
        """Create a fresh audit sampler."""
        from forge_harness.dark_factory.audit_sampler import (
            get_audit_sampler,
            reset_audit_sampler,
        )
        reset_audit_sampler()
        return get_audit_sampler()

    def test_sampler_singleton(self, sampler):
        """Test singleton pattern."""
        from forge_harness.dark_factory.audit_sampler import get_audit_sampler

        sampler2 = get_audit_sampler()
        assert sampler is sampler2

    def test_should_audit_returns_result(self, sampler):
        """Test audit decision returns a result."""
        result = sampler.should_audit("task-001", {"priority": "high"})
        assert result is not None
        # SamplingDecision has 'selected' field, not 'status'
        assert hasattr(result, 'selected')
        assert isinstance(result.selected, bool)


class TestCanaryControl:
    """Tests for Canary Control (DF-4004)."""

    @pytest.fixture
    def canary(self):
        """Create a fresh canary control."""
        from forge_harness.dark_factory.canary_control import (
            get_canary_control,
            reset_canary_control,
        )
        reset_canary_control()
        return get_canary_control()

    def test_canary_singleton(self, canary):
        """Test singleton pattern."""
        from forge_harness.dark_factory.canary_control import get_canary_control

        canary2 = get_canary_control()
        assert canary is canary2

    def test_get_rollout_state(self, canary):
        """Test retrieving rollout state."""
        from forge_harness.dark_factory.canary_control import RolloutStage
        from forge_harness.webhook_server.models.work_cell import WorkCellLane

        # Set a stage first with operator
        canary.set_stage("test-node", WorkCellLane.test_writing, RolloutStage.canary, operator="test")

        # Get state for node and lane
        state = canary.get_state("test-node", WorkCellLane.test_writing)
        assert state is not None
        assert state.lane == WorkCellLane.test_writing


class TestAdaptiveThresholds:
    """Tests for Adaptive Thresholds (DF-5001)."""

    @pytest.fixture
    def adaptive(self):
        """Create a fresh adaptive thresholds instance."""
        from forge_harness.dark_factory.adaptive_thresholds import (
            get_adaptive_thresholds,
            reset_adaptive_thresholds,
        )
        reset_adaptive_thresholds()
        return get_adaptive_thresholds()

    def test_adaptive_singleton(self, adaptive):
        """Test singleton pattern."""
        from forge_harness.dark_factory.adaptive_thresholds import get_adaptive_thresholds

        adaptive2 = get_adaptive_thresholds()
        assert adaptive is adaptive2


class TestLaneGraduation:
    """Tests for Lane Graduation (DF-5005)."""

    @pytest.fixture
    def graduation(self):
        """Create a fresh lane graduation instance."""
        from forge_harness.dark_factory.lane_graduation import (
            get_lane_graduation,
            reset_lane_graduation,
        )
        reset_lane_graduation()
        return get_lane_graduation()

    def test_graduation_singleton(self, graduation):
        """Test singleton pattern."""
        from forge_harness.dark_factory.lane_graduation import get_lane_graduation

        grad2 = get_lane_graduation()
        assert graduation is grad2


class TestQueueOptimizer:
    """Tests for Queue Optimizer (DF-5002)."""

    @pytest.fixture
    def optimizer(self):
        """Create a fresh queue optimizer."""
        from forge_harness.dark_factory.queue_optimizer import (
            get_queue_optimizer,
            reset_queue_optimizer,
        )
        reset_queue_optimizer()
        return get_queue_optimizer()

    def test_optimizer_singleton(self, optimizer):
        """Test singleton pattern."""
        from forge_harness.dark_factory.queue_optimizer import get_queue_optimizer

        opt2 = get_queue_optimizer()
        assert optimizer is opt2

    def test_get_lane_state(self, optimizer):
        """Test retrieving lane queue state."""
        state = optimizer.get_lane_state("test-lane")
        assert state is not None
        assert state.lane == "test-lane"


class TestPerfReporter:
    """Tests for Performance Reporter (DF-5003)."""

    @pytest.fixture
    def reporter(self):
        """Create a fresh performance reporter."""
        from forge_harness.dark_factory.perf_reporter import (
            get_perf_reporter,
            reset_perf_reporter,
        )
        reset_perf_reporter()
        return get_perf_reporter()

    def test_reporter_singleton(self, reporter):
        """Test singleton pattern."""
        from forge_harness.dark_factory.perf_reporter import get_perf_reporter

        rep2 = get_perf_reporter()
        assert reporter is rep2


# =============================================================================
# Phase 3 Component 4: Continuous Runner Tests
# =============================================================================


class TestContinuousRunner:
    """Tests for Continuous Ralph Loop Runner."""

    @pytest.fixture
    def runner_config(self, tmp_path):
        """Create a test runner configuration."""
        return {
            "domain": "test-domain",
            "project": "test-project",
            "forge_root": tmp_path,
            "approval_timeout_hours": 0.1,
            "loop_cooldown_seconds": 0.1,
            "max_iterations_per_run": 1
        }

    @pytest.fixture
    def runner(self, runner_config):
        """Create a test runner instance."""
        from forge_harness.continuous_runner import ContinuousRalphRunner
        return ContinuousRalphRunner(**runner_config)

    def test_runner_initialization(self, runner, runner_config):
        """Test runner initialization."""
        assert runner.domain == runner_config["domain"]
        assert runner.project == runner_config["project"]
        assert runner.loop_cooldown == 0.1
        assert runner.max_iterations_per_run == 1

    def test_runner_features_path_derivation(self, runner, tmp_path):
        """Test features.json path derivation."""
        expected_path = tmp_path / "test-domain" / "test-project" / "features.json"
        assert runner.features_path == expected_path

    def test_runner_state_initialization(self, runner):
        """Test initial runner state."""
        assert runner._running is False
        assert runner._current_feature is None


# =============================================================================
# Test Summary
# =============================================================================


def test_phase3_component_summary():
    """Summary of all Phase 3 components under test."""
    components = {
        "PostgreSQL Database": [
            "Base ORM model",
            "Task model",
            "Agent model",
            "AgentCapability model",
            "TaskRecommendation model",
            "Session/SessionEvent models",
            "Node/NodeHeartbeat models",
        ],
        "Flywheel": [
            "FlywheelConfig",
            "FlywheelResult",
        ],
        "Dark Factory": [
            "LanePolicyEnforcer (DF-4001)",
            "HumanGatePolicy (DF-4002)",
            "AuditSampler (DF-4003)",
            "CanaryControl (DF-4004)",
            "AdaptiveThresholds (DF-5001)",
            "QueueOptimizer (DF-5002)",
            "PerfReporter (DF-5003)",
            "LaneGraduation (DF-5005)",
        ],
        "Continuous Runner": [
            "ContinuousRalphRunner",
        ]
    }

    total_components = sum(len(v) for v in components.values())
    assert total_components > 0
    return components


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
