"""
Continuous Runner module for 24/7 autonomous operation.
"""

# Re-exports for backward compatibility with tests and old usage
import asyncio

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalType,
    create_approval_queue_from_env,
)
from forge_harness.continuous_runner.config import settings
from forge_harness.continuous_runner.deployer import RailwayDeployer, deployer
from forge_harness.continuous_runner.health import HealthMonitor, monitor
from forge_harness.continuous_runner.runner import ContinuousRalphRunner, main
from forge_harness.meta_learning.decision_engine import DecisionEngine
from forge_harness.notification_harness import create_notification_harness
from forge_harness.ralph_loop import (
    FeatureStore,
    LoopConfig,
    RalphLoopHarness,
)

__all__ = [
    "settings",
    "ContinuousRalphRunner",
    "main",
    "monitor",
    "HealthMonitor",
    "deployer",
    "RailwayDeployer",
    "asyncio",
    "ApprovalPriority",
    "ApprovalType",
    "create_approval_queue_from_env",
    "create_notification_harness",
    "DecisionEngine",
    "FeatureStore",
    "LoopConfig",
    "RalphLoopHarness",
]
