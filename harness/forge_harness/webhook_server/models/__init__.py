"""Webhook Server Domain Models.

Canonical data models for the FORGE Dark Factory task lifecycle.
"""

# ---------------------------------------------------------------------------
# Task lifecycle contract (always available)
# ---------------------------------------------------------------------------
# Import from shared models - task_lifecycle moved to avoid circular imports
from forge_harness.models.task_lifecycle import (
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    TaskLifecycleState,
    TransitionError,
    validate_transition,
)

__all__ = [
    # Task lifecycle
    "TaskLifecycleState",
    "VALID_TRANSITIONS",
    "TERMINAL_STATES",
    "TransitionError",
    "validate_transition",
]

# ---------------------------------------------------------------------------
# Lane policy (always available)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.lane_policy import (
    LANE_POLICY,
    Lane,
    RiskTier,
    TaskType,
    get_lane,
    requires_human_approval,
)

__all__ += [
    "LANE_POLICY",
    "Lane",
    "RiskTier",
    "TaskType",
    "get_lane",
    "requires_human_approval",
]

# ---------------------------------------------------------------------------
# Task contract extension (Dark Factory fields)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.task_contract import (
    AcceptanceCheck,
    EvaluatorProfile,
    TaskContractExtension,
)

__all__ += [
    "AcceptanceCheck",
    "EvaluatorProfile",
    "TaskContractExtension",
]

# ---------------------------------------------------------------------------
# Completion claims (evidence-based task verification)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.claim import (
    TERMINAL_CLAIM_STATUSES,
    VALID_CLAIM_TRANSITIONS,
    CheckResult,
    ClaimStatus,
    ClaimTransitionError,
    CompletionClaim,
    EvidenceManifest,
    validate_claim_transition,
)

__all__ += [
    "CheckResult",
    "ClaimStatus",
    "ClaimTransitionError",
    "CompletionClaim",
    "EvidenceManifest",
    "TERMINAL_CLAIM_STATUSES",
    "VALID_CLAIM_TRANSITIONS",
    "validate_claim_transition",
]

# ---------------------------------------------------------------------------
# Work-cell lane taxonomy (always available)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.work_cell import (
    DEFAULT_LANE_CONFIGS,
    LaneResolver,
    WorkCellConfig,
    WorkCellLane,
    get_lane_config,
)

__all__ += [
    "DEFAULT_LANE_CONFIGS",
    "LaneResolver",
    "WorkCellConfig",
    "WorkCellLane",
    "get_lane_config",
]

# ---------------------------------------------------------------------------
# Evaluator override protocol (DF-1006)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.evaluator_override import (
    EvaluatorOverride,
    OverridePolicy,
    OverriderRole,
    OverrideType,
    OverrideViolationError,
)

__all__ += [
    "EvaluatorOverride",
    "OverridePolicy",
    "OverriderRole",
    "OverrideType",
    "OverrideViolationError",
]

# ---------------------------------------------------------------------------
# Task decomposition graph (DF-2002)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.task_graph import (
    SubtaskNode,
    TaskGraph,
)

__all__ += [
    "SubtaskNode",
    "TaskGraph",
]

# ---------------------------------------------------------------------------
# Lane SLO models (always available)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.lane_slo import (
    DEFAULT_LANE_SLOS,
    LaneSLO,
    SLOCheckResult,
    SLOStatus,
)

__all__ += [
    "DEFAULT_LANE_SLOS",
    "LaneSLO",
    "SLOCheckResult",
    "SLOStatus",
]

# ---------------------------------------------------------------------------
# Agent load models (DF-2005)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.agent_load import (
    AgentCapability,
    BalancingRecommendation,
    BalancingStrategy,
)

__all__ += [
    "AgentCapability",
    "BalancingRecommendation",
    "BalancingStrategy",
]

# ---------------------------------------------------------------------------
# Recommendation models (DF-3001)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.recommendation import (
    AgentScore,
    RecommendationStrategy,
    TaskRecommendation,
)

__all__ += [
    "AgentScore",
    "RecommendationStrategy",
    "TaskRecommendation",
]

# ---------------------------------------------------------------------------
# Path lock models (DF-3002)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.path_lock import (
    LockConflict,
    PathLock,
    PathLockType,
)

__all__ += [
    "LockConflict",
    "PathLock",
    "PathLockType",
]

# ---------------------------------------------------------------------------
# Cost-aware routing models (DF-3003)
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.models.cost_routing import (
    COST_PROFILES,
    DEFAULT_ROUTING_POLICIES,
    CostProfile,
    ModelTier,
    RoutingDecision,
    RoutingPolicy,
)

__all__ += [
    "COST_PROFILES",
    "DEFAULT_ROUTING_POLICIES",
    "CostProfile",
    "ModelTier",
    "RoutingDecision",
    "RoutingPolicy",
]

# ---------------------------------------------------------------------------
# Scorecard (optional - module may not be present in all deployments)
# ---------------------------------------------------------------------------
try:
    from forge_harness.webhook_server.models.scorecard import (
        DarkFactoryScorecard,
        ScorecardEvent,
        compute_scorecard,
    )

    __all__ += ["DarkFactoryScorecard", "ScorecardEvent", "compute_scorecard"]
except ImportError:
    pass
