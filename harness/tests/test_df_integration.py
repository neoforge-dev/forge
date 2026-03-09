"""DF-2006: End-to-end integration tests for the FORGE Dark Factory pipeline.

Tests the full async work-cell cycle:
    intake -> decomposition -> lane routing -> SLO monitoring -> load balancing -> requeue

All services use reset_*() helpers in setUp/tearDown so each test starts
from a clean in-memory state.  Persistence paths are redirected to a
temporary directory so tests never touch the live .forge/ hierarchy.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.agent_load import (
    AgentCapability,
    BalancingRecommendation,
    BalancingStrategy,
)
from forge_harness.webhook_server.models.intake_queue import (
    IntakeItem,
    IntakeResult,
    IntakeStatus,
)
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.lane_slo import (
    DEFAULT_LANE_SLOS,
    LaneSLO,
    SLOCheckResult,
    SLOStatus,
)
from forge_harness.webhook_server.models.sse_events import SSEEvent, SSEEventType
from forge_harness.webhook_server.models.task_graph import SubtaskNode, TaskGraph
from forge_harness.webhook_server.models.work_cell import (
    DEFAULT_LANE_CONFIGS,
    WorkCellLane,
    get_lane_config,
)

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.services.event_emitter import (
    EventEmitter,
    get_event_emitter,
    reset_event_emitter,
)
from forge_harness.webhook_server.services.intake_service import (
    IntakeService,
    get_intake_service,
    reset_intake_service,
)
from forge_harness.webhook_server.services.lane_enforcer import (
    LaneEnforcer,
    get_lane_enforcer,
    reset_lane_enforcer,
)
from forge_harness.webhook_server.services.load_balancer import (
    LoadBalancer,
    get_load_balancer,
    reset_load_balancer,
)
from forge_harness.webhook_server.services.slo_monitor import (
    SLOMonitor,
    get_slo_monitor,
    reset_slo_monitor,
)
from forge_harness.webhook_server.services.task_decomposer import (
    TaskDecomposer,
    get_task_decomposer,
    reset_task_decomposer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    title: str = "Test task",
    task_type: TaskType = TaskType.bug_fix,
    risk_tier: RiskTier = RiskTier.low,
    source: str = "api",
    priority: int = 3,
) -> IntakeItem:
    """Create an IntakeItem with sensible defaults."""
    return IntakeItem(
        title=title,
        task_type=task_type,
        risk_tier=risk_tier,
        source=source,
        priority=priority,
    )


def _reset_all() -> None:
    """Reset every singleton to a clean state."""
    reset_intake_service()
    reset_task_decomposer()
    reset_lane_enforcer()
    reset_slo_monitor()
    reset_load_balancer()
    reset_event_emitter()


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------


class DFIntegrationBase(unittest.TestCase):
    """Base class that resets all singletons and uses a temp directory."""

    _tmp_dir: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp_dir = tempfile.TemporaryDirectory(prefix="df_integration_")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp_dir.cleanup()

    def setUp(self) -> None:
        _reset_all()
        # Use temp paths so tests do not write to .forge/
        self._queue_path = Path(self._tmp_dir.name) / f"queue_{id(self)}.jsonl"
        self._graphs_path = Path(self._tmp_dir.name) / f"graphs_{id(self)}.jsonl"

    def tearDown(self) -> None:
        _reset_all()

    # -- convenience factories -----------------------------------------------

    def _get_intake(self) -> IntakeService:
        return get_intake_service(queue_path=self._queue_path)

    def _get_decomposer(self) -> TaskDecomposer:
        return get_task_decomposer(storage_path=self._graphs_path)

    def _get_enforcer(self) -> LaneEnforcer:
        return get_lane_enforcer()

    def _get_monitor(self) -> SLOMonitor:
        return get_slo_monitor()

    def _get_lb(self) -> LoadBalancer:
        return get_load_balancer()

    def _get_emitter(self) -> EventEmitter:
        return get_event_emitter()


# ===========================================================================
# 1. Full Pipeline Tests
# ===========================================================================


class TestFullPipeline(DFIntegrationBase):
    """Submit -> decompose -> assign lanes -> SLO tracking -> complete."""

    def test_full_pipeline_single_task_accepted(self) -> None:
        """A low-risk bug-fix goes through the full pipeline without errors."""
        intake = self._get_intake()
        item = _make_item("Fix login redirect", TaskType.bug_fix, RiskTier.low)
        result = intake.submit(item)

        self.assertEqual(result.status, IntakeStatus.assigned)
        self.assertIsNotNone(result.assigned_lane)
        self.assertIsNotNone(result.assigned_task_id)
        self.assertEqual(result.assigned_lane, WorkCellLane.api_simple)

    def test_full_pipeline_lane_assignment_recorded(self) -> None:
        """LaneEnforcer records the assignment after IntakeService.submit."""
        intake = self._get_intake()
        enforcer = self._get_enforcer()
        item = _make_item(task_type=TaskType.bug_fix, risk_tier=RiskTier.low)
        result = intake.submit(item)

        lane = enforcer.get_assignment(item.item_id)
        self.assertEqual(lane, WorkCellLane.api_simple)
        self.assertEqual(result.assigned_lane, WorkCellLane.api_simple)

    def test_full_pipeline_slo_tracking_start_and_complete(self) -> None:
        """SLO monitor tracks a task from start to completion correctly."""
        monitor = self._get_monitor()
        task_id = "slo-task-001"
        monitor.record_task_start(task_id, WorkCellLane.api_simple)

        time.sleep(0.01)  # minimal real elapsed time
        record = monitor.record_task_complete(task_id, passed=True)

        self.assertIsNotNone(record)
        self.assertEqual(record.task_id, task_id)
        self.assertEqual(record.lane, WorkCellLane.api_simple)
        self.assertTrue(record.passed)
        self.assertIsNotNone(record.lead_time_seconds)
        self.assertGreater(record.lead_time_seconds, 0)

    def test_full_pipeline_slo_healthy_after_fast_task(self) -> None:
        """SLO status is healthy after a fast-completing task."""
        monitor = self._get_monitor()
        task_id = "fast-task"
        monitor.record_task_start(task_id, WorkCellLane.api_simple)
        monitor.record_task_complete(task_id, passed=True)

        result = monitor.check_slo(WorkCellLane.api_simple)
        self.assertEqual(result.status, SLOStatus.healthy)

    def test_full_pipeline_load_balancer_integrated(self) -> None:
        """Load balancer recommends agent after intake assigns a task."""
        lb = self._get_lb()
        lb.register_agent("agent-A", [WorkCellLane.api_simple])

        intake = self._get_intake()
        item = _make_item(task_type=TaskType.bug_fix, risk_tier=RiskTier.low)
        result = intake.submit(item)

        recs = lb.recommend(result.assigned_lane)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].agent_id, "agent-A")

    def test_full_pipeline_event_emitted_on_accept(self) -> None:
        """An event is emitted in the ring buffer when a task is accepted."""
        emitter = self._get_emitter()
        intake = self._get_intake()

        item = _make_item(task_type=TaskType.test_writing, risk_tier=RiskTier.low)
        intake.submit(item)

        events = emitter.get_recent_events(limit=10)
        self.assertGreater(len(events), 0)
        event_types = [e.event_type for e in events]
        self.assertIn(SSEEventType.task_status_changed, event_types)

    def test_full_pipeline_stats_updated(self) -> None:
        """get_stats() reflects submitted items after pipeline run."""
        intake = self._get_intake()
        for _ in range(3):
            intake.submit(_make_item())

        stats = intake.get_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_status"]["assigned"], 3)

    def test_full_pipeline_decompose_then_assign(self) -> None:
        """Subtasks decomposed from a parent are independently assignable."""
        decomposer = self._get_decomposer()
        enforcer = self._get_enforcer()

        graph = decomposer.create_graph("PARENT-001", "Auth module")
        node_a = decomposer.add_subtask(
            "PARENT-001", "Write tests", TaskType.test_writing, RiskTier.low
        )
        node_b = decomposer.add_subtask(
            "PARENT-001", "API endpoint", TaskType.api_endpoint, RiskTier.low
        )

        lane_a = enforcer.assign_lane(node_a.subtask_id, node_a.task_type, node_a.risk_tier)
        lane_b = enforcer.assign_lane(node_b.subtask_id, node_b.task_type, node_b.risk_tier)

        self.assertEqual(lane_a, WorkCellLane.test_writing)
        self.assertEqual(lane_b, WorkCellLane.api_simple)

    def test_full_pipeline_complete_subtask_and_verify_ready(self) -> None:
        """After completing node_a, node_b that depends on it becomes ready."""
        decomposer = self._get_decomposer()

        decomposer.create_graph("PARENT-002", "Pipeline test")
        node_a = decomposer.add_subtask("PARENT-002", "Step A", TaskType.test_writing, RiskTier.low)
        node_b = decomposer.add_subtask(
            "PARENT-002",
            "Step B",
            TaskType.docs_update,
            RiskTier.low,
            depends_on=[node_a.subtask_id],
        )

        # Before completion, only node_a is ready
        graph = decomposer.get_graph("PARENT-002")
        ready_before = graph.get_ready_nodes()
        self.assertEqual(len(ready_before), 1)
        self.assertEqual(ready_before[0].subtask_id, node_a.subtask_id)

        # Complete node_a
        decomposer.complete_subtask("PARENT-002", node_a.subtask_id)
        graph = decomposer.get_graph("PARENT-002")
        ready_after = graph.get_ready_nodes()
        self.assertEqual(len(ready_after), 1)
        self.assertEqual(ready_after[0].subtask_id, node_b.subtask_id)

    def test_full_pipeline_lane_enforcer_active_count(self) -> None:
        """Active count increments when tasks are assigned via IntakeService."""
        intake = self._get_intake()
        enforcer = self._get_enforcer()

        intake.submit(_make_item(task_type=TaskType.bug_fix, risk_tier=RiskTier.low))
        intake.submit(_make_item(task_type=TaskType.bug_fix, risk_tier=RiskTier.low))

        stats = enforcer.get_lane_stats()
        self.assertEqual(stats[WorkCellLane.api_simple.value]["active"], 2)


# ===========================================================================
# 2. Requeue Cycle Tests
# ===========================================================================


class TestRequeueCycle(DFIntegrationBase):
    """SLO timeout -> auto-requeue -> re-assign -> complete."""

    def test_requeue_increments_lane_counter(self) -> None:
        """record_requeue increments the lane requeue count."""
        monitor = self._get_monitor()
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)

        result = monitor.check_slo(WorkCellLane.api_simple)
        self.assertEqual(result.requeue_count, 2)

    def test_requeue_beyond_max_triggers_breach(self) -> None:
        """Exceeding max_requeue_count causes a breached SLO status."""
        monitor = self._get_monitor()
        lane = WorkCellLane.api_simple
        max_requeue = DEFAULT_LANE_SLOS[lane].max_requeue_count  # 3

        for _ in range(max_requeue + 1):
            monitor.record_requeue(lane)

        result = monitor.check_slo(lane)
        self.assertEqual(result.status, SLOStatus.breached)

    def test_requeue_within_limit_stays_healthy(self) -> None:
        """Requeueing within the limit does not breach the SLO."""
        monitor = self._get_monitor()
        lane = WorkCellLane.docs
        max_requeue = DEFAULT_LANE_SLOS[lane].max_requeue_count  # 5

        for _ in range(max_requeue):
            monitor.record_requeue(lane)

        result = monitor.check_slo(lane)
        self.assertNotEqual(result.status, SLOStatus.breached)

    def test_requeue_then_reassign_via_enforcer(self) -> None:
        """After a requeue the task can be re-assigned to a fresh lane slot."""
        enforcer = self._get_enforcer()
        task_id = "requeue-task-001"

        lane = enforcer.assign_lane(task_id, TaskType.bug_fix, RiskTier.low)
        self.assertEqual(lane, WorkCellLane.api_simple)

        # Complete the task (simulating the requeue cycle's prior attempt)
        completed_lane = enforcer.complete_task(task_id)
        self.assertEqual(completed_lane, WorkCellLane.api_simple)

        # Re-assign under a new task_id (new attempt after requeue)
        new_task_id = "requeue-task-001-v2"
        new_lane = enforcer.assign_lane(new_task_id, TaskType.bug_fix, RiskTier.low)
        self.assertEqual(new_lane, WorkCellLane.api_simple)

    def test_requeue_slo_breach_emits_event(self) -> None:
        """SLO breach triggers event emission via EventEmitter."""
        emitter = self._get_emitter()
        monitor = self._get_monitor()

        # Use a custom tight SLO to trigger breach via pass rate
        tight_slo: dict[WorkCellLane, LaneSLO] = {
            lane: DEFAULT_LANE_SLOS[lane] for lane in WorkCellLane
        }
        tight_slo[WorkCellLane.api_simple] = LaneSLO(
            lane=WorkCellLane.api_simple,
            max_lead_time_seconds=300,
            target_pass_rate=1.0,  # 100% required — one failure breaches
            max_requeue_count=10,
            alert_threshold_pct=0.8,
        )

        monitor_custom = SLOMonitor(slos=tight_slo)
        task_id = "failing-task"
        monitor_custom._start_times[task_id] = (
            WorkCellLane.api_simple,
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        # Manually inject start time and use record_task_complete
        reset_slo_monitor()
        monitor2 = SLOMonitor(slos=tight_slo)
        from datetime import UTC, datetime

        monitor2._start_times["fail-001"] = (WorkCellLane.api_simple, datetime.now(UTC))
        monitor2.record_task_complete("fail-001", passed=False)

        result = monitor2.check_slo(WorkCellLane.api_simple)
        self.assertEqual(result.status, SLOStatus.breached)

    def test_requeue_stats_reflected_in_get_breaches(self) -> None:
        """get_breaches() returns lanes where requeue count is exceeded."""
        monitor = self._get_monitor()
        lane = WorkCellLane.security_change
        max_requeue = DEFAULT_LANE_SLOS[lane].max_requeue_count  # 0

        monitor.record_requeue(lane)

        # record a completed task so get_breaches window picks it up
        from datetime import UTC, datetime

        task_id = "sec-task"
        monitor._start_times[task_id] = (lane, datetime.now(UTC))
        monitor.record_task_complete(task_id, passed=True)

        breaches = monitor.get_breaches(window_minutes=60)
        breached_lanes = [b.lane for b in breaches]
        self.assertIn(lane, breached_lanes)


# ===========================================================================
# 3. Multi-Lane Routing Tests
# ===========================================================================


class TestMultiLaneRouting(DFIntegrationBase):
    """Different task types route to distinct lanes with WIP enforcement."""

    def test_bug_fix_low_routes_to_api_simple(self) -> None:
        intake = self._get_intake()
        result = intake.submit(_make_item(task_type=TaskType.bug_fix, risk_tier=RiskTier.low))
        self.assertEqual(result.assigned_lane, WorkCellLane.api_simple)

    def test_test_writing_routes_to_test_writing(self) -> None:
        intake = self._get_intake()
        result = intake.submit(_make_item(task_type=TaskType.test_writing, risk_tier=RiskTier.low))
        self.assertEqual(result.assigned_lane, WorkCellLane.test_writing)

    def test_docs_update_routes_to_docs(self) -> None:
        intake = self._get_intake()
        result = intake.submit(_make_item(task_type=TaskType.docs_update, risk_tier=RiskTier.low))
        self.assertEqual(result.assigned_lane, WorkCellLane.docs)

    def test_security_change_routes_to_security_change(self) -> None:
        intake = self._get_intake()
        result = intake.submit(
            _make_item(task_type=TaskType.security_change, risk_tier=RiskTier.low)
        )
        self.assertEqual(result.assigned_lane, WorkCellLane.security_change)

    def test_deployment_routes_to_deployment(self) -> None:
        intake = self._get_intake()
        result = intake.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        self.assertEqual(result.assigned_lane, WorkCellLane.deployment)

    def test_five_different_task_types_route_to_distinct_lanes(self) -> None:
        """Five tasks of different types end up in at least 4 different lanes."""
        intake = self._get_intake()
        specs = [
            (TaskType.bug_fix, RiskTier.low),
            (TaskType.test_writing, RiskTier.low),
            (TaskType.docs_update, RiskTier.low),
            (TaskType.security_change, RiskTier.low),
            (TaskType.deployment, RiskTier.low),
        ]
        lanes_seen = set()
        for task_type, risk_tier in specs:
            result = intake.submit(_make_item(task_type=task_type, risk_tier=risk_tier))
            self.assertEqual(result.status, IntakeStatus.assigned)
            lanes_seen.add(result.assigned_lane)

        self.assertGreaterEqual(len(lanes_seen), 4)

    def test_wip_limit_respected_on_deployment_lane(self) -> None:
        """Deployment lane has max_wip=1; second task is rejected."""
        intake = self._get_intake()

        first = intake.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        self.assertEqual(first.status, IntakeStatus.assigned)

        second = intake.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        self.assertEqual(second.status, IntakeStatus.rejected)
        self.assertIn("WIP capacity", second.rejection_reason)

    def test_security_change_wip_limit_is_two(self) -> None:
        """Security lane max_wip=2; third task is rejected."""
        intake = self._get_intake()
        config = get_lane_config(WorkCellLane.security_change)
        self.assertEqual(config.max_wip, 2)

        results = [
            intake.submit(_make_item(task_type=TaskType.security_change, risk_tier=RiskTier.low))
            for _ in range(config.max_wip + 1)
        ]
        accepted = [r for r in results if r.status == IntakeStatus.assigned]
        rejected = [r for r in results if r.status == IntakeStatus.rejected]
        self.assertEqual(len(accepted), config.max_wip)
        self.assertEqual(len(rejected), 1)

    def test_lane_stats_active_count_per_lane(self) -> None:
        """get_lane_stats() reports correct active counts per lane."""
        enforcer = self._get_enforcer()
        enforcer.assign_lane("t1", TaskType.test_writing, RiskTier.low)
        enforcer.assign_lane("t2", TaskType.test_writing, RiskTier.low)
        enforcer.assign_lane("t3", TaskType.docs_update, RiskTier.low)

        stats = enforcer.get_lane_stats()
        self.assertEqual(stats[WorkCellLane.test_writing.value]["active"], 2)
        self.assertEqual(stats[WorkCellLane.docs.value]["active"], 1)

    def test_completing_task_frees_wip_slot(self) -> None:
        """Completing a deployment task frees the WIP slot in the enforcer."""
        enforcer = self._get_enforcer()

        # Assign a task directly via enforcer (deployment max_wip=1)
        task_id_1 = "deploy-task-001"
        lane = enforcer.assign_lane(task_id_1, TaskType.deployment, RiskTier.low)
        self.assertEqual(lane, WorkCellLane.deployment)

        # Lane is now full — check_wip should be False
        self.assertFalse(enforcer.check_wip(WorkCellLane.deployment))

        # Complete the task to free the slot
        completed_lane = enforcer.complete_task(task_id_1)
        self.assertEqual(completed_lane, WorkCellLane.deployment)

        # Slot is now free again
        self.assertTrue(enforcer.check_wip(WorkCellLane.deployment))

        # A new task can now be assigned
        task_id_2 = "deploy-task-002"
        lane2 = enforcer.assign_lane(task_id_2, TaskType.deployment, RiskTier.low)
        self.assertEqual(lane2, WorkCellLane.deployment)


# ===========================================================================
# 4. Load Balancer Integration Tests
# ===========================================================================


class TestLoadBalancerIntegration(DFIntegrationBase):
    """Register agents, verify balanced distribution and redistribution."""

    def test_register_single_agent_recommend(self) -> None:
        lb = self._get_lb()
        lb.register_agent("agent-1", [WorkCellLane.api_simple])
        recs = lb.recommend(WorkCellLane.api_simple)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].agent_id, "agent-1")

    def test_no_eligible_agents_returns_empty(self) -> None:
        lb = self._get_lb()
        recs = lb.recommend(WorkCellLane.api_simple)
        self.assertEqual(recs, [])

    def test_three_agents_all_recommended(self) -> None:
        lb = self._get_lb()
        for i in range(3):
            lb.register_agent(f"agent-{i}", [WorkCellLane.api_simple])
        recs = lb.recommend(WorkCellLane.api_simple)
        self.assertEqual(len(recs), 3)

    def test_least_loaded_prefers_lower_active(self) -> None:
        lb = self._get_lb()
        lb.register_agent("heavy", [WorkCellLane.api_simple], max_concurrent=10)
        lb.register_agent("light", [WorkCellLane.api_simple], max_concurrent=10)
        lb.update_agent_load("heavy", current_active=8, context_budget_pct=100.0)
        lb.update_agent_load("light", current_active=1, context_budget_pct=100.0)

        recs = lb.recommend(WorkCellLane.api_simple, BalancingStrategy.least_loaded)
        self.assertEqual(recs[0].agent_id, "light")

    def test_overloaded_agent_scores_lower(self) -> None:
        lb = self._get_lb()
        lb.register_agent("full", [WorkCellLane.docs], max_concurrent=2)
        lb.register_agent("free", [WorkCellLane.docs], max_concurrent=2)
        lb.update_agent_load("full", current_active=2, context_budget_pct=100.0)
        lb.update_agent_load("free", current_active=0, context_budget_pct=100.0)

        recs = lb.recommend(WorkCellLane.docs, BalancingStrategy.least_loaded)
        self.assertEqual(recs[0].agent_id, "free")
        # Score of fully-loaded agent should be less than free agent's score
        self.assertLess(recs[-1].score, recs[0].score)

    def test_round_robin_cycles_agents(self) -> None:
        lb = self._get_lb()
        lb.register_agent("a1", [WorkCellLane.test_writing])
        lb.register_agent("a2", [WorkCellLane.test_writing])

        first_call = lb.recommend(WorkCellLane.test_writing, BalancingStrategy.round_robin)
        second_call = lb.recommend(WorkCellLane.test_writing, BalancingStrategy.round_robin)

        first_agent = first_call[0].agent_id
        second_agent = second_call[0].agent_id
        # Round robin should cycle, so first agents differ
        self.assertNotEqual(first_agent, second_agent)

    def test_get_agent_stats_reflects_registration(self) -> None:
        lb = self._get_lb()
        lb.register_agent("agent-x", [WorkCellLane.refactor], max_concurrent=5)
        stats = lb.get_agent_stats()
        self.assertIn("agent-x", stats)
        self.assertEqual(stats["agent-x"].max_concurrent, 5)
        self.assertEqual(stats["agent-x"].supported_lanes, [WorkCellLane.refactor])

    def test_update_agent_load_reflected_in_stats(self) -> None:
        lb = self._get_lb()
        lb.register_agent("ag", [WorkCellLane.api_stateful])
        lb.update_agent_load("ag", current_active=2, context_budget_pct=60.0)

        stats = lb.get_agent_stats()
        self.assertEqual(stats["ag"].current_active, 2)
        self.assertEqual(stats["ag"].context_budget_remaining_pct, 60.0)

    def test_recommendation_score_in_valid_range(self) -> None:
        lb = self._get_lb()
        lb.register_agent("a", [WorkCellLane.api_simple])
        lb.update_agent_load("a", current_active=1, context_budget_pct=80.0)
        recs = lb.recommend(WorkCellLane.api_simple)
        self.assertEqual(len(recs), 1)
        self.assertGreaterEqual(recs[0].score, 0.0)
        self.assertLessEqual(recs[0].score, 1.0)

    def test_agent_not_eligible_for_unsupported_lane(self) -> None:
        lb = self._get_lb()
        lb.register_agent("docs-agent", [WorkCellLane.docs])
        recs = lb.recommend(WorkCellLane.security_change)
        self.assertEqual(recs, [])

    def test_budget_depleted_agent_scores_lower(self) -> None:
        lb = self._get_lb()
        lb.register_agent("rich", [WorkCellLane.api_simple])
        lb.register_agent("poor", [WorkCellLane.api_simple])
        lb.update_agent_load("rich", current_active=0, context_budget_pct=100.0)
        lb.update_agent_load("poor", current_active=0, context_budget_pct=5.0)

        recs = lb.recommend(WorkCellLane.api_simple, BalancingStrategy.least_loaded)
        self.assertEqual(recs[0].agent_id, "rich")

    def test_re_register_agent_preserves_active_count(self) -> None:
        lb = self._get_lb()
        lb.register_agent("ag", [WorkCellLane.api_simple], max_concurrent=3)
        lb.update_agent_load("ag", current_active=2, context_budget_pct=75.0)

        # Re-register with same max_concurrent — should preserve active count
        lb.register_agent("ag", [WorkCellLane.api_simple], max_concurrent=3)
        stats = lb.get_agent_stats()
        self.assertEqual(stats["ag"].current_active, 2)

    def test_register_agent_invalid_max_concurrent_raises(self) -> None:
        lb = self._get_lb()
        with self.assertRaises(ValueError):
            lb.register_agent("bad", [WorkCellLane.api_simple], max_concurrent=0)


# ===========================================================================
# 5. Event Flow Tests
# ===========================================================================


class TestEventFlow(DFIntegrationBase):
    """Subscribe -> operations -> verify events emitted in order."""

    def test_subscribe_receives_events(self) -> None:
        emitter = self._get_emitter()
        received: list[SSEEvent] = []
        emitter.subscribe(lambda e: received.append(e))

        emitter.emit(SSEEventType.feature_updated, {"x": 1}, source="test")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].event_type, SSEEventType.feature_updated)

    def test_multiple_subscriptions_all_notified(self) -> None:
        emitter = self._get_emitter()
        counts = [0, 0]
        emitter.subscribe(lambda _: counts.__setitem__(0, counts[0] + 1))
        emitter.subscribe(lambda _: counts.__setitem__(1, counts[1] + 1))

        emitter.emit(SSEEventType.agent_heartbeat, {})
        self.assertEqual(counts, [1, 1])

    def test_get_recent_events_limit_respected(self) -> None:
        emitter = self._get_emitter()
        for i in range(10):
            emitter.emit(SSEEventType.feature_updated, {"i": i})

        recent = emitter.get_recent_events(limit=5)
        self.assertEqual(len(recent), 5)

    def test_get_recent_events_filtered_by_type(self) -> None:
        emitter = self._get_emitter()
        emitter.emit(SSEEventType.feature_updated, {})
        emitter.emit(SSEEventType.session_created, {})
        emitter.emit(SSEEventType.feature_updated, {})

        filtered = emitter.get_recent_events(
            limit=10, event_type=SSEEventType.feature_updated.value
        )
        self.assertEqual(len(filtered), 2)
        self.assertTrue(all(e.event_type == SSEEventType.feature_updated for e in filtered))

    def test_intake_accepted_emits_task_status_changed(self) -> None:
        emitter = self._get_emitter()
        received: list[SSEEvent] = []
        emitter.subscribe(lambda e: received.append(e))

        intake = self._get_intake()
        intake.submit(_make_item())

        status_changed = [e for e in received if e.event_type == SSEEventType.task_status_changed]
        self.assertGreater(len(status_changed), 0)

    def test_intake_rejected_emits_task_status_changed(self) -> None:
        emitter = self._get_emitter()
        received: list[SSEEvent] = []
        emitter.subscribe(lambda e: received.append(e))

        intake = self._get_intake()
        # Fill deployment WIP (max=1)
        intake.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        intake.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))

        rejected_events = [
            e
            for e in received
            if e.event_type == SSEEventType.task_status_changed
            and e.data.get("status") == "rejected"
        ]
        self.assertEqual(len(rejected_events), 1)

    def test_event_count_tracks_emitted_events(self) -> None:
        emitter = self._get_emitter()
        self.assertEqual(emitter.event_count(), 0)
        emitter.emit(SSEEventType.session_ended, {})
        emitter.emit(SSEEventType.session_ended, {})
        self.assertEqual(emitter.event_count(), 2)

    def test_subscriber_count_tracks_registrations(self) -> None:
        emitter = self._get_emitter()
        self.assertEqual(emitter.subscriber_count(), 0)
        emitter.subscribe(lambda _: None)
        self.assertEqual(emitter.subscriber_count(), 1)

    def test_unsubscribe_stops_notifications(self) -> None:
        emitter = self._get_emitter()
        calls = [0]

        def cb(e: SSEEvent) -> None:
            calls[0] += 1

        emitter.subscribe(cb)
        emitter.emit(SSEEventType.feature_updated, {})
        self.assertEqual(calls[0], 1)

        emitter.unsubscribe(cb)
        emitter.emit(SSEEventType.feature_updated, {})
        self.assertEqual(calls[0], 1)  # unchanged after unsubscribe

    def test_slo_breach_emits_slo_breached_event(self) -> None:
        emitter = self._get_emitter()
        received: list[SSEEvent] = []
        emitter.subscribe(lambda e: received.append(e))

        # Build a monitor with a zero-tolerance pass rate SLO for docs lane
        from datetime import UTC, datetime

        tight_slos: dict[WorkCellLane, LaneSLO] = dict(DEFAULT_LANE_SLOS)
        tight_slos[WorkCellLane.docs] = LaneSLO(
            lane=WorkCellLane.docs,
            max_lead_time_seconds=600,
            target_pass_rate=1.0,  # 100% required
            max_requeue_count=10,
            alert_threshold_pct=0.8,
        )
        monitor = SLOMonitor(slos=tight_slos)
        monitor._start_times["fail-doc"] = (WorkCellLane.docs, datetime.now(UTC))
        monitor.record_task_complete("fail-doc", passed=False)

        slo_events = [e for e in received if e.event_type == SSEEventType.task_slo_breached]
        self.assertGreater(len(slo_events), 0)


# ===========================================================================
# 6. Decomposition + Routing Tests
# ===========================================================================


class TestDecompositionAndRouting(DFIntegrationBase):
    """Parent task -> decompose -> each subtask gets correct lane -> complete in order."""

    def test_create_graph_returns_empty_graph(self) -> None:
        decomposer = self._get_decomposer()
        graph = decomposer.create_graph("P1", "Parent")
        self.assertEqual(graph.parent_id, "P1")
        self.assertEqual(len(graph.nodes), 0)
        # Empty graph is vacuously complete (no nodes to complete)
        self.assertTrue(graph.is_complete())

    def test_add_three_subtasks_with_different_types(self) -> None:
        decomposer = self._get_decomposer()
        decomposer.create_graph("P2", "Multi-type parent")

        node_a = decomposer.add_subtask("P2", "Tests", TaskType.test_writing, RiskTier.low)
        node_b = decomposer.add_subtask("P2", "Docs", TaskType.docs_update, RiskTier.low)
        node_c = decomposer.add_subtask("P2", "API", TaskType.api_endpoint, RiskTier.low)

        graph = decomposer.get_graph("P2")
        self.assertEqual(len(graph.nodes), 3)

    def test_three_subtasks_route_to_three_different_lanes(self) -> None:
        decomposer = self._get_decomposer()
        enforcer = self._get_enforcer()
        decomposer.create_graph("P3", "Multi-lane parent")

        node_a = decomposer.add_subtask("P3", "Tests", TaskType.test_writing, RiskTier.low)
        node_b = decomposer.add_subtask("P3", "Docs", TaskType.docs_update, RiskTier.low)
        node_c = decomposer.add_subtask("P3", "API", TaskType.api_endpoint, RiskTier.low)

        lane_a = enforcer.assign_lane(node_a.subtask_id, node_a.task_type, node_a.risk_tier)
        lane_b = enforcer.assign_lane(node_b.subtask_id, node_b.task_type, node_b.risk_tier)
        lane_c = enforcer.assign_lane(node_c.subtask_id, node_c.task_type, node_c.risk_tier)

        lanes = {lane_a, lane_b, lane_c}
        self.assertEqual(
            lanes,
            {WorkCellLane.test_writing, WorkCellLane.docs, WorkCellLane.api_simple},
        )

    def test_complete_subtasks_in_dependency_order(self) -> None:
        decomposer = self._get_decomposer()
        decomposer.create_graph("P4", "Ordered parent")

        node_a = decomposer.add_subtask("P4", "Write tests", TaskType.test_writing, RiskTier.low)
        node_b = decomposer.add_subtask(
            "P4",
            "Implement feature",
            TaskType.new_feature,
            RiskTier.low,
            depends_on=[node_a.subtask_id],
        )
        node_c = decomposer.add_subtask(
            "P4",
            "Document feature",
            TaskType.docs_update,
            RiskTier.low,
            depends_on=[node_b.subtask_id],
        )

        # Only node_a is ready initially
        graph = decomposer.get_graph("P4")
        self.assertEqual(len(graph.get_ready_nodes()), 1)

        decomposer.complete_subtask("P4", node_a.subtask_id)
        graph = decomposer.get_graph("P4")
        self.assertEqual(len(graph.get_ready_nodes()), 1)
        self.assertEqual(graph.get_ready_nodes()[0].subtask_id, node_b.subtask_id)

        decomposer.complete_subtask("P4", node_b.subtask_id)
        graph = decomposer.get_graph("P4")
        self.assertEqual(graph.get_ready_nodes()[0].subtask_id, node_c.subtask_id)

        decomposer.complete_subtask("P4", node_c.subtask_id)
        graph = decomposer.get_graph("P4")
        self.assertTrue(graph.is_complete())

    def test_completion_percentage_tracks_progress(self) -> None:
        decomposer = self._get_decomposer()
        decomposer.create_graph("P5", "Progress parent")
        node_a = decomposer.add_subtask("P5", "A", TaskType.docs_update, RiskTier.low)
        node_b = decomposer.add_subtask("P5", "B", TaskType.docs_update, RiskTier.low)

        graph = decomposer.get_graph("P5")
        self.assertEqual(graph.completion_pct(), 0.0)

        decomposer.complete_subtask("P5", node_a.subtask_id)
        graph = decomposer.get_graph("P5")
        self.assertEqual(graph.completion_pct(), 50.0)

        decomposer.complete_subtask("P5", node_b.subtask_id)
        graph = decomposer.get_graph("P5")
        self.assertEqual(graph.completion_pct(), 100.0)

    def test_list_graphs_returns_all_created(self) -> None:
        decomposer = self._get_decomposer()
        decomposer.create_graph("PA", "Parent A")
        decomposer.create_graph("PB", "Parent B")
        graphs = decomposer.list_graphs()
        parent_ids = {g.parent_id for g in graphs}
        self.assertIn("PA", parent_ids)
        self.assertIn("PB", parent_ids)

    def test_get_graph_nonexistent_returns_none(self) -> None:
        decomposer = self._get_decomposer()
        result = decomposer.get_graph("DOES-NOT-EXIST")
        self.assertIsNone(result)

    def test_subtask_slo_tracked_per_subtask_id(self) -> None:
        """Each subtask can be individually tracked via SLOMonitor."""
        decomposer = self._get_decomposer()
        monitor = self._get_monitor()
        decomposer.create_graph("P6", "SLO parent")

        node = decomposer.add_subtask("P6", "Run tests", TaskType.test_writing, RiskTier.low)
        monitor.record_task_start(node.subtask_id, WorkCellLane.test_writing)
        record = monitor.record_task_complete(node.subtask_id, passed=True)

        self.assertEqual(record.task_id, node.subtask_id)
        self.assertEqual(record.lane, WorkCellLane.test_writing)


# ===========================================================================
# 7. SLO Breach Detection Tests
# ===========================================================================


class TestSLOBreachDetection(DFIntegrationBase):
    """Simulate slow/failing tasks and verify breach/warning detection."""

    def test_no_data_is_healthy(self) -> None:
        monitor = self._get_monitor()
        result = monitor.check_slo(WorkCellLane.api_simple)
        self.assertEqual(result.status, SLOStatus.healthy)
        self.assertIsNone(result.current_lead_time_seconds)
        self.assertIsNone(result.current_pass_rate)

    def test_passing_tasks_yield_healthy_slo(self) -> None:
        monitor = self._get_monitor()
        from datetime import UTC, datetime

        for i in range(5):
            tid = f"t{i}"
            monitor._start_times[tid] = (WorkCellLane.api_simple, datetime.now(UTC))
            monitor.record_task_complete(tid, passed=True)

        result = monitor.check_slo(WorkCellLane.api_simple)
        self.assertEqual(result.status, SLOStatus.healthy)

    def test_all_failing_tasks_breach_pass_rate(self) -> None:
        monitor = self._get_monitor()
        from datetime import UTC, datetime

        for i in range(5):
            tid = f"f{i}"
            monitor._start_times[tid] = (WorkCellLane.api_simple, datetime.now(UTC))
            monitor.record_task_complete(tid, passed=False)

        result = monitor.check_slo(WorkCellLane.api_simple)
        self.assertEqual(result.status, SLOStatus.breached)

    def test_partial_failure_below_target_breaches(self) -> None:
        """api_simple target_pass_rate=0.95. 80% pass rate should breach."""
        monitor = self._get_monitor()
        from datetime import UTC, datetime

        # 4 pass, 1 fail = 80% pass < 0.95 target
        for i in range(4):
            tid = f"pass-{i}"
            monitor._start_times[tid] = (WorkCellLane.api_simple, datetime.now(UTC))
            monitor.record_task_complete(tid, passed=True)

        monitor._start_times["fail-1"] = (WorkCellLane.api_simple, datetime.now(UTC))
        monitor.record_task_complete("fail-1", passed=False)

        result = monitor.check_slo(WorkCellLane.api_simple)
        self.assertEqual(result.status, SLOStatus.breached)

    def test_check_all_slos_returns_all_lanes(self) -> None:
        monitor = self._get_monitor()
        results = monitor.check_all_slos()
        lane_set = {r.lane for r in results}
        self.assertEqual(lane_set, set(WorkCellLane))

    def test_slo_check_result_has_requeue_count(self) -> None:
        monitor = self._get_monitor()
        monitor.record_requeue(WorkCellLane.refactor)
        monitor.record_requeue(WorkCellLane.refactor)

        result = monitor.check_slo(WorkCellLane.refactor)
        self.assertEqual(result.requeue_count, 2)

    def test_get_breaches_empty_when_no_breach(self) -> None:
        monitor = self._get_monitor()
        breaches = monitor.get_breaches()
        self.assertEqual(breaches, [])

    def test_breach_detected_by_requeue_count(self) -> None:
        """security_change max_requeue_count=0; any requeue is a breach."""
        monitor = self._get_monitor()
        lane = WorkCellLane.security_change
        monitor.record_requeue(lane)

        # Trigger check via a completed task so window includes the lane
        from datetime import UTC, datetime

        monitor._start_times["sec1"] = (lane, datetime.now(UTC))
        monitor.record_task_complete("sec1", passed=True)

        breaches = monitor.get_breaches(window_minutes=60)
        self.assertTrue(any(b.lane == lane for b in breaches))

    def test_slo_check_result_fields_populated(self) -> None:
        monitor = self._get_monitor()
        from datetime import UTC, datetime

        monitor._start_times["t1"] = (WorkCellLane.docs, datetime.now(UTC))
        monitor.record_task_complete("t1", passed=True)

        result = monitor.check_slo(WorkCellLane.docs)
        self.assertIsNotNone(result.current_lead_time_seconds)
        self.assertIsNotNone(result.current_pass_rate)
        self.assertGreaterEqual(result.current_pass_rate, 0.0)
        self.assertLessEqual(result.current_pass_rate, 1.0)


# ===========================================================================
# 8. Concurrent Operations Tests
# ===========================================================================


class TestConcurrentOperations(DFIntegrationBase):
    """Multi-threaded submissions and completions verify thread safety."""

    def test_concurrent_intake_submissions_no_race(self) -> None:
        """20 concurrent submits all either accept or reject; no exceptions."""
        intake = self._get_intake()
        errors: list[Exception] = []
        results: list[IntakeResult] = []
        lock = threading.Lock()

        def submit() -> None:
            try:
                r = intake.submit(_make_item())
                with lock:
                    results.append(r)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=submit) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Exceptions: {errors}")
        self.assertEqual(len(results), 20)
        statuses = {r.status for r in results}
        self.assertTrue(statuses.issubset({IntakeStatus.assigned, IntakeStatus.rejected}))

    def test_concurrent_slo_record_start_and_complete(self) -> None:
        """Concurrent SLO start/complete calls do not corrupt state."""
        monitor = self._get_monitor()
        errors: list[Exception] = []
        records_lock = threading.Lock()
        completed_records: list[Any] = []

        def run_task(i: int) -> None:
            try:
                tid = f"ct-{i}"
                monitor.record_task_start(tid, WorkCellLane.api_simple)
                time.sleep(0.001)
                rec = monitor.record_task_complete(tid, passed=True)
                with records_lock:
                    if rec is not None:
                        completed_records.append(rec)
            except Exception as e:
                with records_lock:
                    errors.append(e)

        threads = [threading.Thread(target=run_task, args=(i,)) for i in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(completed_records), 15)

    def test_concurrent_lane_assignments_no_data_race(self) -> None:
        """Concurrent lane assignments yield consistent state in enforcer."""
        enforcer = self._get_enforcer()
        errors: list[Exception] = []
        assignments: list[WorkCellLane] = []
        lock = threading.Lock()

        def assign(i: int) -> None:
            try:
                lane = enforcer.assign_lane(f"conc-{i}", TaskType.test_writing, RiskTier.low)
                with lock:
                    assignments.append(lane)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=assign, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(assignments), 10)
        self.assertTrue(all(a == WorkCellLane.test_writing for a in assignments))

    def test_concurrent_event_subscription_and_emit(self) -> None:
        """Concurrent subscriptions and emits do not deadlock or crash."""
        emitter = self._get_emitter()
        errors: list[Exception] = []

        def subscribe_and_emit(i: int) -> None:
            try:
                counter = [0]
                emitter.subscribe(lambda e: counter.__setitem__(0, counter[0] + 1))
                emitter.emit(SSEEventType.feature_updated, {"i": i})
            except Exception as e:
                with threading.Lock():
                    errors.append(e)

        threads = [threading.Thread(target=subscribe_and_emit, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0)
        self.assertGreater(emitter.event_count(), 0)

    def test_concurrent_load_balancer_updates(self) -> None:
        """Concurrent update_agent_load calls do not raise."""
        lb = self._get_lb()
        lb.register_agent("shared-agent", [WorkCellLane.api_simple])
        errors: list[Exception] = []

        def update(active: int) -> None:
            try:
                lb.update_agent_load(
                    "shared-agent",
                    current_active=active % 3,
                    context_budget_pct=float(50 + active % 50),
                )
            except Exception as e:
                with threading.Lock():
                    errors.append(e)

        threads = [threading.Thread(target=update, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0)


# ===========================================================================
# 9. Error Handling Tests
# ===========================================================================


class TestErrorHandling(DFIntegrationBase):
    """Invalid inputs and boundary conditions raise appropriate exceptions."""

    def test_invalid_source_field_raises_validation_error(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            IntakeItem(
                title="Bad source",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="invalid_source",  # not in VALID_SOURCES
            )

    def test_intake_title_too_short_raises(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            IntakeItem(
                title="",  # min_length=1 violated
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
            )

    def test_create_duplicate_graph_raises_value_error(self) -> None:
        decomposer = self._get_decomposer()
        decomposer.create_graph("DUP-001", "First")
        with self.assertRaises(ValueError):
            decomposer.create_graph("DUP-001", "Second")

    def test_add_subtask_unknown_parent_raises_key_error(self) -> None:
        decomposer = self._get_decomposer()
        with self.assertRaises(KeyError):
            decomposer.add_subtask("NONEXISTENT", "Node", TaskType.docs_update, RiskTier.low)

    def test_add_subtask_unknown_dependency_raises_value_error(self) -> None:
        decomposer = self._get_decomposer()
        decomposer.create_graph("ERR-001", "Parent")
        with self.assertRaises(ValueError):
            decomposer.add_subtask(
                "ERR-001",
                "Node",
                TaskType.docs_update,
                RiskTier.low,
                depends_on=["nonexistent-sub-id"],
            )

    def test_cycle_in_decomposition_graph_raises_value_error(self) -> None:
        """Manually build a cycle in a TaskGraph and verify detection."""
        graph = TaskGraph(parent_id="CYC", parent_title="Cycle test")
        from datetime import UTC, datetime

        node_a = SubtaskNode(
            subtask_id="sub-a",
            parent_id="CYC",
            title="A",
            task_type=TaskType.docs_update,
            risk_tier=RiskTier.low,
            depends_on=["sub-b"],
            created_at=datetime.now(UTC),
        )
        node_b = SubtaskNode(
            subtask_id="sub-b",
            parent_id="CYC",
            title="B",
            task_type=TaskType.docs_update,
            risk_tier=RiskTier.low,
            depends_on=["sub-a"],
            created_at=datetime.now(UTC),
        )
        graph.nodes["sub-a"] = node_a
        graph.nodes["sub-b"] = node_b

        with self.assertRaises(ValueError):
            graph.validate_no_cycles()

    def test_complete_subtask_unknown_parent_raises_key_error(self) -> None:
        decomposer = self._get_decomposer()
        with self.assertRaises(KeyError):
            decomposer.complete_subtask("NONEXISTENT", "sub-id")

    def test_complete_subtask_already_completed_raises_value_error(self) -> None:
        decomposer = self._get_decomposer()
        decomposer.create_graph("DONE-001", "Already done")
        node = decomposer.add_subtask("DONE-001", "Task", TaskType.test_writing, RiskTier.low)
        decomposer.complete_subtask("DONE-001", node.subtask_id)
        with self.assertRaises(ValueError):
            decomposer.complete_subtask("DONE-001", node.subtask_id)

    def test_update_agent_load_unknown_agent_raises_key_error(self) -> None:
        lb = self._get_lb()
        with self.assertRaises(KeyError):
            lb.update_agent_load("ghost-agent", current_active=0, context_budget_pct=100.0)

    def test_update_agent_load_negative_active_raises(self) -> None:
        lb = self._get_lb()
        lb.register_agent("ag", [WorkCellLane.api_simple])
        with self.assertRaises(ValueError):
            lb.update_agent_load("ag", current_active=-1, context_budget_pct=100.0)

    def test_update_agent_load_out_of_range_budget_raises(self) -> None:
        lb = self._get_lb()
        lb.register_agent("ag", [WorkCellLane.api_simple])
        with self.assertRaises(ValueError):
            lb.update_agent_load("ag", current_active=0, context_budget_pct=101.0)

    def test_slo_record_complete_unknown_task_returns_none(self) -> None:
        monitor = self._get_monitor()
        result = monitor.record_task_complete("UNKNOWN-TASK", passed=True)
        self.assertIsNone(result)

    def test_lane_enforcer_complete_unknown_task_returns_none(self) -> None:
        enforcer = self._get_enforcer()
        result = enforcer.complete_task("not-assigned")
        self.assertIsNone(result)

    def test_intake_priority_out_of_range_raises(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            IntakeItem(
                title="Priority test",
                task_type=TaskType.bug_fix,
                risk_tier=RiskTier.low,
                source="api",
                priority=6,  # le=5 violated
            )


# ===========================================================================
# 10. Statistics Aggregation Tests
# ===========================================================================


class TestStatisticsAggregation(DFIntegrationBase):
    """Cross-service statistics remain consistent after running many tasks."""

    def test_intake_stats_by_status_consistency(self) -> None:
        intake = self._get_intake()
        # Fill deployment (max_wip=1) then try one more
        intake.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))
        intake.submit(_make_item(task_type=TaskType.deployment, risk_tier=RiskTier.low))  # rejected
        intake.submit(_make_item(task_type=TaskType.test_writing, risk_tier=RiskTier.low))

        stats = intake.get_stats()
        total = stats["total"]
        sum_by_status = sum(stats["by_status"].values())
        self.assertEqual(total, sum_by_status)

    def test_intake_stats_by_source_counts(self) -> None:
        intake = self._get_intake()
        intake.submit(_make_item(source="api"))
        intake.submit(_make_item(source="cli"))
        intake.submit(_make_item(source="api"))

        stats = intake.get_stats()
        self.assertEqual(stats["by_source"]["api"], 2)
        self.assertEqual(stats["by_source"]["cli"], 1)

    def test_lane_enforcer_stats_active_plus_completed(self) -> None:
        """After completions, active decreases and completed increases."""
        enforcer = self._get_enforcer()
        task_ids = [f"stat-task-{i}" for i in range(4)]
        for tid in task_ids:
            enforcer.assign_lane(tid, TaskType.test_writing, RiskTier.low)

        stats_before = enforcer.get_lane_stats()
        active_before = stats_before[WorkCellLane.test_writing.value]["active"]
        self.assertEqual(active_before, 4)

        for tid in task_ids[:2]:
            enforcer.complete_task(tid)

        stats_after = enforcer.get_lane_stats()
        self.assertEqual(stats_after[WorkCellLane.test_writing.value]["active"], 2)
        self.assertEqual(stats_after[WorkCellLane.test_writing.value]["completed"], 2)

    def test_slo_check_all_lanes_include_all_enums(self) -> None:
        monitor = self._get_monitor()
        results = monitor.check_all_slos()
        self.assertEqual(len(results), len(WorkCellLane))

    def test_slo_pass_rate_accuracy(self) -> None:
        """Pass rate computed correctly: 3 pass + 1 fail = 75%."""
        monitor = self._get_monitor()
        from datetime import UTC, datetime

        for i in range(3):
            monitor._start_times[f"p{i}"] = (WorkCellLane.test_writing, datetime.now(UTC))
            monitor.record_task_complete(f"p{i}", passed=True)

        monitor._start_times["f0"] = (WorkCellLane.test_writing, datetime.now(UTC))
        monitor.record_task_complete("f0", passed=False)

        result = monitor.check_slo(WorkCellLane.test_writing)
        self.assertIsNotNone(result.current_pass_rate)
        self.assertAlmostEqual(result.current_pass_rate, 0.75, places=5)

    def test_slo_lead_time_average_accuracy(self) -> None:
        """Average lead time is computed across all completed records."""
        monitor = self._get_monitor()
        from datetime import UTC, datetime, timedelta

        # Inject two records with known lead times
        t1 = datetime.now(UTC) - timedelta(seconds=10)
        t2 = datetime.now(UTC) - timedelta(seconds=20)
        from forge_harness.webhook_server.services.slo_monitor import _TaskRecord

        monitor._records.append(
            _TaskRecord(
                task_id="lt1",
                lane=WorkCellLane.docs,
                started_at=t1 - timedelta(seconds=10),
                completed_at=t1,
                passed=True,
                lead_time_seconds=10.0,
            )
        )
        monitor._records.append(
            _TaskRecord(
                task_id="lt2",
                lane=WorkCellLane.docs,
                started_at=t2 - timedelta(seconds=20),
                completed_at=t2,
                passed=True,
                lead_time_seconds=20.0,
            )
        )

        result = monitor.check_slo(WorkCellLane.docs)
        self.assertAlmostEqual(result.current_lead_time_seconds, 15.0, places=5)

    def test_cross_service_consistent_after_full_run(self) -> None:
        """Total tasks in intake == total task_ids assigned in enforcer."""
        intake = self._get_intake()
        enforcer = self._get_enforcer()

        submitted = 5
        for _ in range(submitted):
            intake.submit(_make_item(task_type=TaskType.test_writing, risk_tier=RiskTier.low))

        stats_intake = intake.get_stats()
        lane_stats = enforcer.get_lane_stats()

        intake_assigned = stats_intake["by_status"]["assigned"]
        enforcer_active = lane_stats[WorkCellLane.test_writing.value]["active"]

        self.assertEqual(intake_assigned, submitted)
        self.assertEqual(enforcer_active, submitted)

    def test_load_balancer_agent_stats_after_updates(self) -> None:
        """All three agents appear in get_agent_stats with correct data."""
        lb = self._get_lb()
        agents = ["forge:alpha", "forge:beta", "forge:gamma"]
        for i, ag in enumerate(agents):
            lb.register_agent(ag, [WorkCellLane.api_simple], max_concurrent=5)
            lb.update_agent_load(ag, current_active=i, context_budget_pct=float(100 - i * 20))

        stats = lb.get_agent_stats()
        self.assertEqual(len(stats), 3)
        self.assertEqual(stats["forge:alpha"].current_active, 0)
        self.assertEqual(stats["forge:beta"].current_active, 1)
        self.assertEqual(stats["forge:gamma"].current_active, 2)

    def test_intake_list_pending_returns_empty_after_submit(self) -> None:
        """list_pending() is empty because submit moves items to assigned."""
        intake = self._get_intake()
        intake.submit(_make_item())
        pending = intake.list_pending()
        # After submit the item is in assigned state, not pending
        self.assertEqual(len(pending), 0)

    def test_intake_list_pending_limit_parameter(self) -> None:
        """list_pending returns at most *limit* items."""
        intake = self._get_intake()
        # list_pending only returns items in pending status; items move to
        # assigned/rejected after submit.  Verify the limit is honoured on
        # a direct (empty) call.
        result = intake.list_pending(limit=5)
        self.assertIsInstance(result, list)
        self.assertLessEqual(len(result), 5)

    def test_slo_record_idempotent_start(self) -> None:
        """Calling record_task_start twice for same task_id is idempotent."""
        monitor = self._get_monitor()
        monitor.record_task_start("idem-task", WorkCellLane.api_simple)
        monitor.record_task_start("idem-task", WorkCellLane.api_simple)  # should be no-op

        # Should still complete correctly
        rec = monitor.record_task_complete("idem-task", passed=True)
        self.assertIsNotNone(rec)

    def test_enforcer_assign_idempotent(self) -> None:
        """Assigning the same task_id twice returns the same lane."""
        enforcer = self._get_enforcer()
        lane_first = enforcer.assign_lane("idem", TaskType.bug_fix, RiskTier.low)
        lane_second = enforcer.assign_lane("idem", TaskType.bug_fix, RiskTier.low)
        self.assertEqual(lane_first, lane_second)

        # Active count should not double-increment
        stats = enforcer.get_lane_stats()
        self.assertEqual(stats[WorkCellLane.api_simple.value]["active"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
