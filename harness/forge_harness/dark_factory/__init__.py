"""Dark Factory — Phases 4-5: Policy Enforcement & Production Readiness.

This package implements the policy layer that sits above the Lane taxonomy
defined in Phases 0-3.  It answers key operational questions for every task:

1. **Can this task auto-complete?** — answered by :class:`LanePolicyEnforcer`
2. **Is this task hard-gated?** — answered by :class:`HumanGatePolicy`
3. **Should this task be audited?** — answered by :class:`AuditSampler`
4. **Is autonomy enabled for this node/lane?** — answered by :class:`CanaryControl`
5. **Is this lane ready for promotion?** — answered by :class:`LaneGraduation`
6. **Should thresholds be adjusted?** — answered by :class:`AdaptiveThresholds`
7. **What's the weekly quality trend?** — answered by :class:`PerfReporter`
8. **Can this task be dispatched/retried?** — answered by :class:`QueueOptimizer`

Module map
----------
::

    dark_factory/
    ├── lane_policy_enforcer.py   ← DF-4001: can_auto_complete / requires_human_review
    ├── human_gate_policy.py      ← DF-4002: hard gates, override mechanism, audit
    ├── audit_sampler.py          ← DF-4003: random audit sampling (10-15%)
    ├── canary_control.py         ← DF-4004: node/lane rollout controls
    ├── adaptive_thresholds.py    ← DF-5001: threshold tuning engine
    ├── queue_optimizer.py        ← DF-5002: WIP limits + retry budgets
    ├── perf_reporter.py          ← DF-5003: weekly performance reviews
    └── lane_graduation.py        ← DF-5005: lane promotion safety gates
"""

from forge_harness.dark_factory.adaptive_thresholds import (
    AdaptiveThresholds,
    AdjustmentRecord,
    ObservedMetrics,
    ThresholdAdjustment,
    get_adaptive_thresholds,
    reset_adaptive_thresholds,
)
from forge_harness.dark_factory.audit_sampler import (
    AuditRecord,
    AuditSampler,
    AuditStatus,
    AuditSummary,
    SamplingDecision,
    get_audit_sampler,
    reset_audit_sampler,
)
from forge_harness.dark_factory.canary_control import (
    CanaryControl,
    RolloutStage,
    RolloutState,
    RolloutSummary,
    RolloutTransition,
    get_canary_control,
    reset_canary_control,
)
from forge_harness.dark_factory.human_gate_policy import (
    GateResult,
    GateStatus,
    HumanGatePolicy,
    HumanGateViolationError,
    get_human_gate_policy,
    reset_human_gate_policy,
)
from forge_harness.dark_factory.lane_graduation import (
    CriterionResult,
    GraduationReport,
    LaneGraduation,
    LaneMetrics,
    get_lane_graduation,
    reset_lane_graduation,
)
from forge_harness.dark_factory.lane_policy_enforcer import (
    LanePolicy,
    LanePolicyEnforcer,
    PolicyDecision,
    get_lane_policy_enforcer,
    reset_lane_policy_enforcer,
)
from forge_harness.dark_factory.perf_reporter import (
    MetricTrend,
    PerfReporter,
    Trend,
    WeeklyMetrics,
    WeeklyReport,
    get_perf_reporter,
    reset_perf_reporter,
)
from forge_harness.dark_factory.queue_optimizer import (
    DispatchDecision,
    LaneQueueState,
    QueueOptimizer,
    QueueSummary,
    RetryDecision,
    get_queue_optimizer,
    reset_queue_optimizer,
)

__all__ = [
    # DF-5001: Adaptive Thresholds
    "AdaptiveThresholds",
    "AdjustmentRecord",
    "ObservedMetrics",
    "ThresholdAdjustment",
    "get_adaptive_thresholds",
    "reset_adaptive_thresholds",
    # DF-4003: Audit Sampler
    "AuditRecord",
    "AuditSampler",
    "AuditStatus",
    "AuditSummary",
    "SamplingDecision",
    "get_audit_sampler",
    "reset_audit_sampler",
    # DF-4004: Canary Control
    "CanaryControl",
    "RolloutStage",
    "RolloutState",
    "RolloutSummary",
    "RolloutTransition",
    "get_canary_control",
    "reset_canary_control",
    # DF-4002: Human Gate Policy
    "GateResult",
    "GateStatus",
    "HumanGatePolicy",
    "HumanGateViolationError",
    "get_human_gate_policy",
    "reset_human_gate_policy",
    # DF-5005: Lane Graduation
    "CriterionResult",
    "GraduationReport",
    "LaneGraduation",
    "LaneMetrics",
    "get_lane_graduation",
    "reset_lane_graduation",
    # DF-4001: Lane Policy Enforcer
    "LanePolicy",
    "LanePolicyEnforcer",
    "PolicyDecision",
    "get_lane_policy_enforcer",
    "reset_lane_policy_enforcer",
    # DF-5003: Performance Reporter
    "MetricTrend",
    "PerfReporter",
    "Trend",
    "WeeklyMetrics",
    "WeeklyReport",
    "get_perf_reporter",
    "reset_perf_reporter",
    # DF-5002: Queue Optimizer
    "DispatchDecision",
    "LaneQueueState",
    "QueueOptimizer",
    "QueueSummary",
    "RetryDecision",
    "get_queue_optimizer",
    "reset_queue_optimizer",
]
