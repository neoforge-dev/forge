"""
Lane Policy Matrix
==================

Implements the Dark Factory lane policy for FORGE task routing.

Every task is classified by (task_type, risk_tier) and assigned to one of
three execution lanes:

- autonomous:    Agent completes; evaluator verifies; no human touch required.
- human_review:  Agent completes; evaluator runs; human must approve before done.
- blocked:       Requires direct human action; agents may not apply changes.

Reference: docs/runbooks/LANE_POLICY_MATRIX.md
Plan:      docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md (DF-0005)
"""

from __future__ import annotations

from enum import Enum

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RiskTier(str, Enum):
    """Risk classification for a task.

    Attributes:
        low:      No prod impact, no auth/security, no data changes. Fully
                  reversible. All changes covered by automated tests.
        medium:   Affects existing behavior; has test coverage; does not touch
                  security or payments. Changes are staged or reversible.
        high:     Touches auth, payments, PII, or production behavior. Hard to
                  reverse or affects user-visible behavior.
        critical: Irreversible or directly production-impacting. A mistake
                  requires manual incident response.
    """

    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Lane(str, Enum):
    """Execution lane for a task.

    Attributes:
        autonomous:    Agent completes and evaluator verifies. No human touch.
        human_review:  Agent completes, evaluator runs, human must approve.
        blocked:       Cannot be executed by agents. Requires human action.
    """

    autonomous = "autonomous"
    human_review = "human-review"
    blocked = "blocked"


class TaskType(str, Enum):
    """Canonical task type taxonomy for FORGE Dark Factory routing.

    Attributes:
        test_writing:        Adding or updating automated tests.
        docs_update:         Documentation changes (runbooks, READMEs, guides).
        content_generation:  Marketing copy, blog posts, SEO content.
        code_refactor:       Restructuring existing code without feature changes.
        api_endpoint:        Adding or modifying REST/GraphQL API endpoints.
        security_change:     Auth, JWT, password, encryption, API key changes.
        deployment:          Releasing to a staging or production environment.
        database_migration:  Schema changes, data migrations, index changes.
        dependency_update:   Updating package dependencies.
        config_change:       Environment config, CORS, rate limits, flags.
        new_feature:         Implementing a new user-facing capability.
        bug_fix:             Correcting a defect in existing behavior.
    """

    test_writing = "test-writing"
    docs_update = "docs-update"
    content_generation = "content-generation"
    code_refactor = "code-refactor"
    api_endpoint = "api-endpoint"
    security_change = "security-change"
    deployment = "deployment"
    database_migration = "database-migration"
    dependency_update = "dependency-update"
    config_change = "config-change"
    new_feature = "new-feature"
    bug_fix = "bug-fix"


# ---------------------------------------------------------------------------
# Policy Matrix
# ---------------------------------------------------------------------------

# Short aliases used only inside this module to keep the table readable.
_A = Lane.autonomous
_H = Lane.human_review
_B = Lane.blocked

#: The authoritative lane policy matrix.
#:
#: Key:   (TaskType, RiskTier)
#: Value: Lane
#:
#: Policy invariants enforced here:
#:   1. security_change is never autonomous at high or critical risk.
#:   2. deployment is always blocked at critical risk.
#:   3. database_migration is blocked at high and critical risk.
#:   4. No task type is autonomous at critical risk.
#:   5. All blocked tasks generate a human-actionable artifact.
LANE_POLICY: dict[tuple[TaskType, RiskTier], Lane] = {
    # ------------------------------------------------------------------ #
    # test-writing
    # ------------------------------------------------------------------ #
    (TaskType.test_writing, RiskTier.low): _A,
    (TaskType.test_writing, RiskTier.medium): _A,
    (TaskType.test_writing, RiskTier.high): _H,
    (TaskType.test_writing, RiskTier.critical): _H,
    # ------------------------------------------------------------------ #
    # docs-update
    # ------------------------------------------------------------------ #
    (TaskType.docs_update, RiskTier.low): _A,
    (TaskType.docs_update, RiskTier.medium): _A,
    (TaskType.docs_update, RiskTier.high): _H,
    (TaskType.docs_update, RiskTier.critical): _H,
    # ------------------------------------------------------------------ #
    # content-generation
    # ------------------------------------------------------------------ #
    (TaskType.content_generation, RiskTier.low): _A,
    (TaskType.content_generation, RiskTier.medium): _A,
    (TaskType.content_generation, RiskTier.high): _H,
    (TaskType.content_generation, RiskTier.critical): _B,
    # ------------------------------------------------------------------ #
    # code-refactor
    # ------------------------------------------------------------------ #
    (TaskType.code_refactor, RiskTier.low): _A,
    (TaskType.code_refactor, RiskTier.medium): _H,
    (TaskType.code_refactor, RiskTier.high): _H,
    (TaskType.code_refactor, RiskTier.critical): _B,
    # ------------------------------------------------------------------ #
    # api-endpoint
    # ------------------------------------------------------------------ #
    (TaskType.api_endpoint, RiskTier.low): _A,
    (TaskType.api_endpoint, RiskTier.medium): _H,
    (TaskType.api_endpoint, RiskTier.high): _H,
    (TaskType.api_endpoint, RiskTier.critical): _B,
    # ------------------------------------------------------------------ #
    # security-change (invariant: never autonomous at high/critical)
    # ------------------------------------------------------------------ #
    (TaskType.security_change, RiskTier.low): _H,
    (TaskType.security_change, RiskTier.medium): _H,
    (TaskType.security_change, RiskTier.high): _B,
    (TaskType.security_change, RiskTier.critical): _B,
    # ------------------------------------------------------------------ #
    # deployment (invariant: always blocked at critical)
    # ------------------------------------------------------------------ #
    (TaskType.deployment, RiskTier.low): _H,
    (TaskType.deployment, RiskTier.medium): _H,
    (TaskType.deployment, RiskTier.high): _B,
    (TaskType.deployment, RiskTier.critical): _B,
    # ------------------------------------------------------------------ #
    # database-migration (invariant: blocked at high and critical)
    # ------------------------------------------------------------------ #
    (TaskType.database_migration, RiskTier.low): _H,
    (TaskType.database_migration, RiskTier.medium): _H,
    (TaskType.database_migration, RiskTier.high): _B,
    (TaskType.database_migration, RiskTier.critical): _B,
    # ------------------------------------------------------------------ #
    # dependency-update
    # ------------------------------------------------------------------ #
    (TaskType.dependency_update, RiskTier.low): _A,
    (TaskType.dependency_update, RiskTier.medium): _H,
    (TaskType.dependency_update, RiskTier.high): _H,
    (TaskType.dependency_update, RiskTier.critical): _B,
    # ------------------------------------------------------------------ #
    # config-change
    # ------------------------------------------------------------------ #
    (TaskType.config_change, RiskTier.low): _A,
    (TaskType.config_change, RiskTier.medium): _H,
    (TaskType.config_change, RiskTier.high): _H,
    (TaskType.config_change, RiskTier.critical): _B,
    # ------------------------------------------------------------------ #
    # new-feature
    # ------------------------------------------------------------------ #
    (TaskType.new_feature, RiskTier.low): _A,
    (TaskType.new_feature, RiskTier.medium): _H,
    (TaskType.new_feature, RiskTier.high): _H,
    (TaskType.new_feature, RiskTier.critical): _B,
    # ------------------------------------------------------------------ #
    # bug-fix
    # ------------------------------------------------------------------ #
    (TaskType.bug_fix, RiskTier.low): _A,
    (TaskType.bug_fix, RiskTier.medium): _H,
    (TaskType.bug_fix, RiskTier.high): _H,
    (TaskType.bug_fix, RiskTier.critical): _B,
}

# Verify the matrix is complete at import time (catches accidental gaps).
_expected_count = len(TaskType) * len(RiskTier)
assert len(LANE_POLICY) == _expected_count, (
    f"LANE_POLICY is incomplete: expected {_expected_count} entries, "
    f"got {len(LANE_POLICY)}. Add missing (TaskType, RiskTier) combinations."
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_lane(task_type: TaskType, risk_tier: RiskTier) -> Lane:
    """Return the execution lane for a (task_type, risk_tier) combination.

    Args:
        task_type:  The canonical type of work being performed.
        risk_tier:  The assessed risk level of the specific task instance.

    Returns:
        The Lane the task must execute in.

    Raises:
        KeyError: If the combination is not present in the policy matrix.
                  This should never happen when valid enum values are passed,
                  because the matrix is validated complete at import time.

    Example::

        lane = get_lane(TaskType.bug_fix, RiskTier.low)
        # Lane.autonomous

        lane = get_lane(TaskType.deployment, RiskTier.critical)
        # Lane.blocked
    """
    return LANE_POLICY[(task_type, risk_tier)]


def requires_human_approval(task_type: TaskType, risk_tier: RiskTier) -> bool:
    """Return True if this task requires a human to approve before completion.

    Both ``human_review`` and ``blocked`` lanes require human involvement.
    Only ``autonomous`` tasks can complete without a human approval record.

    Args:
        task_type:  The canonical type of work being performed.
        risk_tier:  The assessed risk level of the specific task instance.

    Returns:
        True when a human approval record must exist before the task can
        reach the ``completed`` state.

    Example::

        requires_human_approval(TaskType.bug_fix, RiskTier.low)
        # False

        requires_human_approval(TaskType.security_change, RiskTier.medium)
        # True

        requires_human_approval(TaskType.deployment, RiskTier.critical)
        # True  (blocked tasks also require human action)
    """
    lane = get_lane(task_type, risk_tier)
    return lane != Lane.autonomous
