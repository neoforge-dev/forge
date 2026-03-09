"""Work-Cell Lane Taxonomy Model
=================================

Defines the granular work-cell lane taxonomy that maps every queued task to
exactly one execution lane.  This layer sits *beneath* the three-lane Dark
Factory policy (autonomous / human_review / blocked) and specifies the
concrete work-cell queue a task is dispatched to.

The work-cell lanes drive WIP limits, timeout budgets, evaluator profiles, and
human-review gates at the scheduler level.

Classes
-------
WorkCellLane
    Enumeration of all valid work-cell lanes.
WorkCellConfig
    Pydantic model holding per-lane operational parameters.
LaneResolver
    Deterministic mapper from (TaskType, RiskTier) to WorkCellLane.

Constants
---------
DEFAULT_LANE_CONFIGS
    Dict mapping every WorkCellLane to its default WorkCellConfig.

Functions
---------
get_lane_config(lane)
    Retrieve the WorkCellConfig for a given lane (from DEFAULT_LANE_CONFIGS).

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2003)
forge_harness/webhook_server/models/lane_policy.py  (RiskTier, TaskType)
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType

# ---------------------------------------------------------------------------
# WorkCellLane enum
# ---------------------------------------------------------------------------


class WorkCellLane(str, Enum):
    """Granular work-cell lane taxonomy.

    Each lane maps to a distinct queue with its own WIP limit, timeout budget,
    evaluator profile, and human-review policy.

    Attributes:
        api_simple:      Stateless, read-only, or trivially additive API work.
                         No side-effects that modify persistent state.
        api_stateful:    API work that mutates state, coordinates transactions,
                         or integrates external services.
        test_writing:    Authoring or updating automated test suites.
        docs:            Documentation updates — runbooks, READMEs, guides,
                         API references, and content generation.
        research:        Exploratory spikes, feasibility investigations, and
                         dependency evaluations (no code shipped).
        refactor:        Code restructuring that does not change observable
                         behaviour; includes dependency updates.
        security_change: Any work touching authentication, authorisation,
                         encryption, secrets, or payment flows.
        deployment:      Releasing to staging or production environments,
                         including config-only releases.
    """

    api_simple = "api_simple"
    api_stateful = "api_stateful"
    test_writing = "test_writing"
    docs = "docs"
    research = "research"
    refactor = "refactor"
    security_change = "security_change"
    deployment = "deployment"


# ---------------------------------------------------------------------------
# WorkCellConfig model
# ---------------------------------------------------------------------------


class WorkCellConfig(BaseModel):
    """Operational parameters for a single work-cell lane.

    Attributes:
        lane:                    The work-cell lane this config applies to.
        max_wip:                 Maximum number of tasks that may be *active*
                                 simultaneously in this lane.  New tasks are
                                 queued when the limit is reached.
        timeout_seconds:         Wall-clock deadline for a single task in this
                                 lane.  Exceeded tasks are moved to
                                 ``timed_out`` state and may be re-queued.
        auto_requeue:            When ``True`` timed-out tasks are automatically
                                 re-enqueued once.  When ``False`` they require
                                 human intervention.
        evaluator_profile_name:  Name of the evaluator profile template to
                                 apply to tasks in this lane.  Maps to a
                                 registered EvaluatorProfile in the evaluator
                                 registry (e.g. ``"strict"``, ``"lenient"``).
        requires_human_review:   When ``True`` all tasks in this lane require a
                                 human approval record before they can reach
                                 the ``completed`` state, regardless of
                                 evaluator outcome.
    """

    lane: WorkCellLane = Field(
        ...,
        description="The work-cell lane these parameters apply to.",
    )
    max_wip: int = Field(
        ...,
        ge=1,
        le=100,
        description="Maximum simultaneously active tasks in this lane.",
    )
    timeout_seconds: int = Field(
        ...,
        ge=60,
        le=86400,
        description="Wall-clock timeout for a single task (seconds).",
    )
    auto_requeue: bool = Field(
        ...,
        description="Re-enqueue timed-out tasks automatically (one retry).",
    )
    evaluator_profile_name: str = Field(
        ...,
        min_length=1,
        description="Name of the evaluator profile template for this lane.",
    )
    requires_human_review: bool = Field(
        ...,
        description="All tasks in this lane require a human approval record.",
    )

    model_config = {"frozen": True, "populate_by_name": True}


# ---------------------------------------------------------------------------
# LaneResolver
# ---------------------------------------------------------------------------

# Compact aliases used only inside the mapping table below.
_SIMPLE = WorkCellLane.api_simple
_STATEFUL = WorkCellLane.api_stateful
_TEST = WorkCellLane.test_writing
_DOCS = WorkCellLane.docs
_RESEARCH = WorkCellLane.research
_REFACTOR = WorkCellLane.refactor
_SEC = WorkCellLane.security_change
_DEPLOY = WorkCellLane.deployment

#: Deterministic (TaskType, RiskTier) → WorkCellLane mapping.
#:
#: Design invariants:
#:   1. Every (TaskType, RiskTier) combination has exactly one entry.
#:   2. security_change always maps to security_change lane.
#:   3. deployment always maps to deployment lane.
#:   4. Higher risk tiers route to lanes with stricter controls.
_RESOLVER_MAP: dict[tuple[TaskType, RiskTier], WorkCellLane] = {
    # ------------------------------------------------------------------ #
    # test-writing  →  test_writing lane for all risk tiers
    # ------------------------------------------------------------------ #
    (TaskType.test_writing, RiskTier.low): _TEST,
    (TaskType.test_writing, RiskTier.medium): _TEST,
    (TaskType.test_writing, RiskTier.high): _TEST,
    (TaskType.test_writing, RiskTier.critical): _TEST,
    # ------------------------------------------------------------------ #
    # docs-update  →  docs lane for all risk tiers
    # ------------------------------------------------------------------ #
    (TaskType.docs_update, RiskTier.low): _DOCS,
    (TaskType.docs_update, RiskTier.medium): _DOCS,
    (TaskType.docs_update, RiskTier.high): _DOCS,
    (TaskType.docs_update, RiskTier.critical): _DOCS,
    # ------------------------------------------------------------------ #
    # content-generation  →  docs lane (treated as documentation work)
    # ------------------------------------------------------------------ #
    (TaskType.content_generation, RiskTier.low): _DOCS,
    (TaskType.content_generation, RiskTier.medium): _DOCS,
    (TaskType.content_generation, RiskTier.high): _DOCS,
    (TaskType.content_generation, RiskTier.critical): _DOCS,
    # ------------------------------------------------------------------ #
    # code-refactor  →  refactor lane for all risk tiers
    # ------------------------------------------------------------------ #
    (TaskType.code_refactor, RiskTier.low): _REFACTOR,
    (TaskType.code_refactor, RiskTier.medium): _REFACTOR,
    (TaskType.code_refactor, RiskTier.high): _REFACTOR,
    (TaskType.code_refactor, RiskTier.critical): _REFACTOR,
    # ------------------------------------------------------------------ #
    # api-endpoint  →  simple lane at low risk; stateful at medium+
    # ------------------------------------------------------------------ #
    (TaskType.api_endpoint, RiskTier.low): _SIMPLE,
    (TaskType.api_endpoint, RiskTier.medium): _STATEFUL,
    (TaskType.api_endpoint, RiskTier.high): _STATEFUL,
    (TaskType.api_endpoint, RiskTier.critical): _STATEFUL,
    # ------------------------------------------------------------------ #
    # security-change  →  security_change lane for all risk tiers
    # ------------------------------------------------------------------ #
    (TaskType.security_change, RiskTier.low): _SEC,
    (TaskType.security_change, RiskTier.medium): _SEC,
    (TaskType.security_change, RiskTier.high): _SEC,
    (TaskType.security_change, RiskTier.critical): _SEC,
    # ------------------------------------------------------------------ #
    # deployment  →  deployment lane for all risk tiers
    # ------------------------------------------------------------------ #
    (TaskType.deployment, RiskTier.low): _DEPLOY,
    (TaskType.deployment, RiskTier.medium): _DEPLOY,
    (TaskType.deployment, RiskTier.high): _DEPLOY,
    (TaskType.deployment, RiskTier.critical): _DEPLOY,
    # ------------------------------------------------------------------ #
    # database-migration  →  stateful (mutations) with higher stakes
    # ------------------------------------------------------------------ #
    (TaskType.database_migration, RiskTier.low): _STATEFUL,
    (TaskType.database_migration, RiskTier.medium): _STATEFUL,
    (TaskType.database_migration, RiskTier.high): _STATEFUL,
    (TaskType.database_migration, RiskTier.critical): _STATEFUL,
    # ------------------------------------------------------------------ #
    # dependency-update  →  refactor lane (structural, no new behaviour)
    # ------------------------------------------------------------------ #
    (TaskType.dependency_update, RiskTier.low): _REFACTOR,
    (TaskType.dependency_update, RiskTier.medium): _REFACTOR,
    (TaskType.dependency_update, RiskTier.high): _REFACTOR,
    (TaskType.dependency_update, RiskTier.critical): _REFACTOR,
    # ------------------------------------------------------------------ #
    # config-change  →  simple at low risk; stateful when higher stakes
    # ------------------------------------------------------------------ #
    (TaskType.config_change, RiskTier.low): _SIMPLE,
    (TaskType.config_change, RiskTier.medium): _STATEFUL,
    (TaskType.config_change, RiskTier.high): _STATEFUL,
    (TaskType.config_change, RiskTier.critical): _STATEFUL,
    # ------------------------------------------------------------------ #
    # new-feature  →  simple at low risk; stateful at medium+
    # ------------------------------------------------------------------ #
    (TaskType.new_feature, RiskTier.low): _SIMPLE,
    (TaskType.new_feature, RiskTier.medium): _STATEFUL,
    (TaskType.new_feature, RiskTier.high): _STATEFUL,
    (TaskType.new_feature, RiskTier.critical): _STATEFUL,
    # ------------------------------------------------------------------ #
    # bug-fix  →  simple at low risk; stateful at medium+
    # ------------------------------------------------------------------ #
    (TaskType.bug_fix, RiskTier.low): _SIMPLE,
    (TaskType.bug_fix, RiskTier.medium): _STATEFUL,
    (TaskType.bug_fix, RiskTier.high): _STATEFUL,
    (TaskType.bug_fix, RiskTier.critical): _STATEFUL,
}

# Validate completeness at import time — same pattern as lane_policy.py.
_expected_resolver_count = len(TaskType) * len(RiskTier)
assert len(_RESOLVER_MAP) == _expected_resolver_count, (
    f"_RESOLVER_MAP is incomplete: expected {_expected_resolver_count} entries, "
    f"got {len(_RESOLVER_MAP)}. Add missing (TaskType, RiskTier) combinations."
)


class LaneResolver:
    """Deterministic mapper from (TaskType, RiskTier) to WorkCellLane.

    Every call with the same inputs returns the same WorkCellLane.  There are
    no side-effects; the resolver is stateless and safe to share across threads.

    Example::

        resolver = LaneResolver()
        lane = resolver.resolve(TaskType.bug_fix, RiskTier.low)
        # WorkCellLane.api_simple

        lane = resolver.resolve(TaskType.security_change, RiskTier.high)
        # WorkCellLane.security_change
    """

    def resolve(self, task_type: TaskType, risk_tier: RiskTier) -> WorkCellLane:
        """Return the work-cell lane for a (task_type, risk_tier) pair.

        Args:
            task_type:  The canonical type of work being performed.
            risk_tier:  The assessed risk level of the specific task instance.

        Returns:
            The single WorkCellLane the task must execute in.

        Raises:
            KeyError: If the combination is absent from the resolver map.
                      This should never occur for valid enum values because the
                      map is validated complete at import time.
        """
        return _RESOLVER_MAP[(task_type, risk_tier)]


# ---------------------------------------------------------------------------
# Default lane configurations
# ---------------------------------------------------------------------------

#: Default operational parameters for every WorkCellLane.
#:
#: These values represent sensible production defaults.  Operators may
#: override them by replacing the relevant WorkCellConfig at runtime.
DEFAULT_LANE_CONFIGS: dict[WorkCellLane, WorkCellConfig] = {
    WorkCellLane.api_simple: WorkCellConfig(
        lane=WorkCellLane.api_simple,
        max_wip=10,
        timeout_seconds=600,  # 10 minutes — quick, stateless work
        auto_requeue=True,
        evaluator_profile_name="lenient",
        requires_human_review=False,
    ),
    WorkCellLane.api_stateful: WorkCellConfig(
        lane=WorkCellLane.api_stateful,
        max_wip=5,
        timeout_seconds=1800,  # 30 minutes — allows for DB round-trips
        auto_requeue=True,
        evaluator_profile_name="standard",
        requires_human_review=False,
    ),
    WorkCellLane.test_writing: WorkCellConfig(
        lane=WorkCellLane.test_writing,
        max_wip=8,
        timeout_seconds=900,  # 15 minutes
        auto_requeue=True,
        evaluator_profile_name="lenient",
        requires_human_review=False,
    ),
    WorkCellLane.docs: WorkCellConfig(
        lane=WorkCellLane.docs,
        max_wip=12,
        timeout_seconds=600,  # 10 minutes
        auto_requeue=True,
        evaluator_profile_name="lenient",
        requires_human_review=False,
    ),
    WorkCellLane.research: WorkCellConfig(
        lane=WorkCellLane.research,
        max_wip=4,
        timeout_seconds=3600,  # 60 minutes — exploratory work takes time
        auto_requeue=False,
        evaluator_profile_name="lenient",
        requires_human_review=False,
    ),
    WorkCellLane.refactor: WorkCellConfig(
        lane=WorkCellLane.refactor,
        max_wip=6,
        timeout_seconds=1800,  # 30 minutes
        auto_requeue=True,
        evaluator_profile_name="standard",
        requires_human_review=False,
    ),
    WorkCellLane.security_change: WorkCellConfig(
        lane=WorkCellLane.security_change,
        max_wip=2,
        timeout_seconds=3600,  # 60 minutes — careful review warranted
        auto_requeue=False,
        evaluator_profile_name="strict",
        requires_human_review=True,
    ),
    WorkCellLane.deployment: WorkCellConfig(
        lane=WorkCellLane.deployment,
        max_wip=1,
        timeout_seconds=7200,  # 2 hours — deployment pipelines can be slow
        auto_requeue=False,
        evaluator_profile_name="strict",
        requires_human_review=True,
    ),
}

# Validate every lane has a default config at import time.
_missing_configs = set(WorkCellLane) - set(DEFAULT_LANE_CONFIGS.keys())
assert not _missing_configs, "DEFAULT_LANE_CONFIGS is missing entries for: " + ", ".join(
    lane.value for lane in _missing_configs
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_lane_config(lane: WorkCellLane) -> WorkCellConfig:
    """Return the default WorkCellConfig for *lane*.

    Args:
        lane: The work-cell lane to look up.

    Returns:
        The :class:`WorkCellConfig` for the requested lane.

    Raises:
        KeyError: If *lane* is not present in DEFAULT_LANE_CONFIGS.  This
                  should never occur for valid WorkCellLane enum values.

    Example::

        config = get_lane_config(WorkCellLane.security_change)
        config.requires_human_review  # True
        config.max_wip                # 2
    """
    return DEFAULT_LANE_CONFIGS[lane]
