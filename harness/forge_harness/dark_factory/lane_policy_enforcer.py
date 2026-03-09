"""Lane Policy Enforcer — DF-4001
=================================

Implements the policy layer that determines whether a task may auto-complete
or whether a human must be involved before the task can reach a terminal state.

The enforcer consults two sources of truth:

1. **LANE_POLICY matrix** (``webhook_server/models/lane_policy.py``) — maps
   ``(TaskType, RiskTier)`` to one of the three canonical lanes:
   ``autonomous``, ``human_review``, or ``blocked``.

2. **WorkCellLane taxonomy** (``webhook_server/models/work_cell.py``) — maps
   the same ``(TaskType, RiskTier)`` to a granular work-cell queue.

The enforcer's :class:`LanePolicy` dataclass enriches both layers with a
config-driven policy that can be overridden at runtime via a JSON-compatible
dict, which allows operators to tighten or relax defaults without a code
change.

Design principles
-----------------
* **Defence-in-depth**: the enforcer never trusts caller-supplied lane strings.
  It re-derives the lane from canonical enum values.
* **Config-driven**: ``LANE_POLICY_CONFIG`` can be replaced at runtime via
  :meth:`LanePolicyEnforcer.update_config`.
* **Immutable defaults**: the default config is defined as a module-level
  constant; :meth:`LanePolicyEnforcer.reset` restores it.
* **Singleton**: use :func:`get_lane_policy_enforcer` and
  :func:`reset_lane_policy_enforcer` (tests).

Usage
-----
::

    from forge_harness.dark_factory.lane_policy_enforcer import (
        get_lane_policy_enforcer,
    )
    from forge_harness.webhook_server.models.lane_policy import TaskType, RiskTier

    enforcer = get_lane_policy_enforcer()

    task = {"task_type": TaskType.test_writing, "risk_tier": RiskTier.low}
    enforcer.can_auto_complete(task)          # True
    enforcer.requires_human_review(task)      # False

    policy = enforcer.get_lane_policy(WorkCellLane.docs)
    policy.can_auto_complete                  # True
    policy.requires_human_review              # False
    policy.is_hard_gated                      # False

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-4001)
forge_harness/webhook_server/models/lane_policy.py
forge_harness/webhook_server/models/work_cell.py
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.lane_policy import (
    Lane,
    RiskTier,
    TaskType,
    get_lane,
)
from forge_harness.webhook_server.models.work_cell import (
    LaneResolver,
    WorkCellLane,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Default WIP limits (per lane)
# ----------------------------------------------------------------------------

# Default WIP limits per lane. These can be overridden at runtime.
DEFAULT_WIP_LIMITS: dict[str, int] = {
    WorkCellLane.api_simple.value: 10,
    WorkCellLane.api_stateful.value: 5,
    WorkCellLane.test_writing.value: 8,
    WorkCellLane.docs.value: 12,
    WorkCellLane.research.value: 4,
    WorkCellLane.refactor.value: 6,
    WorkCellLane.security_change.value: 2,
    WorkCellLane.deployment.value: 2,
}

# Default total node-level concurrency ceiling.
DEFAULT_NODE_CONCURRENCY_CEILING: int = 25

# ---------------------------------------------------------------------------
# Default policy config
# ---------------------------------------------------------------------------

# WorkCellLane → policy flags.
#
# Each entry controls:
#   can_auto_complete    — agents may mark the task completed without human sign-off.
#   requires_human_review — a human approval record must exist before completion.
#   is_hard_gated        — HumanGatePolicy treats this lane as a hard gate.
#
# The three-way split maps directly to the canonical lane policy:
#   autonomous → can_auto_complete=True,  requires_human_review=False, is_hard_gated=False
#   human_review → can_auto_complete=False, requires_human_review=True, is_hard_gated=False
#   blocked    → can_auto_complete=False, requires_human_review=True, is_hard_gated=True
#
# Config is intentionally structured as a plain dict so it can be serialised
# to JSON, stored in a config file, and hot-reloaded without restart.
DEFAULT_LANE_POLICY_CONFIG: dict[str, dict[str, bool]] = {
    # ------------------------------------------------------------------ #
    # AUTO-COMPLETE lanes (low risk, fully autonomous)
    # ------------------------------------------------------------------ #
    WorkCellLane.docs.value: {
        "can_auto_complete": True,
        "requires_human_review": False,
        "is_hard_gated": False,
    },
    WorkCellLane.test_writing.value: {
        "can_auto_complete": True,
        "requires_human_review": False,
        "is_hard_gated": False,
    },
    WorkCellLane.api_simple.value: {
        "can_auto_complete": True,
        "requires_human_review": False,
        "is_hard_gated": False,
    },
    # ------------------------------------------------------------------ #
    # HUMAN-REVIEW lanes (medium risk — agent completes, human approves)
    # ------------------------------------------------------------------ #
    WorkCellLane.api_stateful.value: {
        "can_auto_complete": False,
        "requires_human_review": True,
        "is_hard_gated": False,
    },
    WorkCellLane.research.value: {
        "can_auto_complete": False,
        "requires_human_review": True,
        "is_hard_gated": False,
    },
    WorkCellLane.refactor.value: {
        "can_auto_complete": False,
        "requires_human_review": True,
        "is_hard_gated": False,
    },
    # ------------------------------------------------------------------ #
    # HARD-GATED lanes (high risk — always require human; agents may NOT bypass)
    # ------------------------------------------------------------------ #
    WorkCellLane.security_change.value: {
        "can_auto_complete": False,
        "requires_human_review": True,
        "is_hard_gated": True,
    },
    WorkCellLane.deployment.value: {
        "can_auto_complete": False,
        "requires_human_review": True,
        "is_hard_gated": True,
    },
}

# Verify the config is complete: every WorkCellLane must have an entry.
_missing_config_lanes = {lane.value for lane in WorkCellLane} - set(DEFAULT_LANE_POLICY_CONFIG)
assert not _missing_config_lanes, (
    f"DEFAULT_LANE_POLICY_CONFIG is missing entries for: {_missing_config_lanes}"
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LanePolicy:
    """Immutable policy descriptor for a single work-cell lane.

    Attributes:
        lane:                  The work-cell lane this policy describes.
        can_auto_complete:     Agents may mark tasks completed without human
                               approval.  True only for low-risk autonomous
                               lanes (``docs``, ``test_writing``,
                               ``api_simple``).
        requires_human_review: A human approval record must exist before the
                               task can reach the ``completed`` state.  True
                               for all lanes where ``can_auto_complete`` is
                               False.
        is_hard_gated:         The lane is subject to a hard human gate.
                               Agents may NOT apply any terminal state
                               transition.  True for ``security_change`` and
                               ``deployment`` lanes.
        wip_limit:             Maximum concurrent tasks allowed in this lane.
    """

    lane: WorkCellLane
    can_auto_complete: bool
    requires_human_review: bool
    is_hard_gated: bool
    wip_limit: int = 10

    def __post_init__(self) -> None:
        """Enforce internal consistency of the policy flags.

        Invariants:
            - ``can_auto_complete`` and ``requires_human_review`` must be
              mutually exclusive (a task cannot both auto-complete and
              require review at the same time).
            - ``is_hard_gated`` implies ``requires_human_review``.
            - ``wip_limit`` must be positive.
        """
        if self.can_auto_complete and self.requires_human_review:
            raise ValueError(
                f"LanePolicy for {self.lane.value!r}: "
                "'can_auto_complete' and 'requires_human_review' are mutually exclusive."
            )
        if self.is_hard_gated and not self.requires_human_review:
            raise ValueError(
                f"LanePolicy for {self.lane.value!r}: "
                "'is_hard_gated=True' requires 'requires_human_review=True'."
            )
        if self.wip_limit <= 0:
            raise ValueError(
                f"LanePolicy for {self.lane.value!r}: "
                f"'wip_limit' must be positive, got {self.wip_limit}"
            )


@dataclass(frozen=True)
class PolicyDecision:
    """Result of a policy evaluation for a concrete task.

    Attributes:
        task_id:               Unique identifier of the evaluated task.
        task_type:             Canonical type of work (from TaskType enum).
        risk_tier:             Assessed risk level (from RiskTier enum).
        canonical_lane:        The three-lane policy result: ``autonomous``,
                               ``human_review``, or ``blocked``.
        work_cell_lane:        The granular work-cell lane the task is routed to.
        lane_policy:           The :class:`LanePolicy` for ``work_cell_lane``.
        can_auto_complete:     Convenience shortcut from ``lane_policy``.
        requires_human_review: Convenience shortcut from ``lane_policy``.
        is_hard_gated:         Convenience shortcut from ``lane_policy``.
        policy_version:        The version string of the config that produced
                               this decision (useful for audit trails).
    """

    task_id: str
    task_type: TaskType
    risk_tier: RiskTier
    canonical_lane: Lane
    work_cell_lane: WorkCellLane
    lane_policy: LanePolicy
    can_auto_complete: bool
    requires_human_review: bool
    is_hard_gated: bool
    policy_version: str = "default"


# ---------------------------------------------------------------------------
# LanePolicyEnforcer
# ---------------------------------------------------------------------------


class LanePolicyEnforcer:
    """Policy layer that determines autonomous vs. human-gated task completion.

    This class sits above the raw lane taxonomy and answers two operational
    questions for every task:

    1. **Can this task auto-complete?** (:meth:`can_auto_complete`)
    2. **Does this task require human review?** (:meth:`requires_human_review`)

    It also exposes the full :class:`LanePolicy` for a work-cell lane via
    :meth:`get_lane_policy`, and produces a fully-populated
    :class:`PolicyDecision` for a concrete task via :meth:`evaluate`.

    The internal config is a plain dict that can be updated at runtime via
    :meth:`update_config`.  This allows operators to tighten or relax policy
    without a code change.

    Thread Safety
    -------------
    All mutable state is guarded by an :class:`threading.RLock`.

    Args:
        config:          Optional override for the default lane policy config.
                         Defaults to :data:`DEFAULT_LANE_POLICY_CONFIG`.
        policy_version:  Version string included in :class:`PolicyDecision`
                         objects for audit trail purposes.
    """

    def __init__(
        self,
        config: dict[str, dict[str, bool]] | None = None,
        policy_version: str = "default",
    ) -> None:
        self._lock: RLock = RLock()
        self._resolver: LaneResolver = LaneResolver()
        self._policy_version: str = policy_version

        # Deep-copy the default so mutations never bleed into the module constant.
        self._config: dict[str, dict[str, bool]] = {
            k: dict(v) for k, v in (config or DEFAULT_LANE_POLICY_CONFIG).items()
        }

        # WIP tracking state (per-lane and node total)
        self._wip_limits: dict[str, int] = dict(DEFAULT_WIP_LIMITS)
        self._lane_wip: dict[str, int] = defaultdict(int)  # Current WIP per lane
        self._node_concurrency_ceiling: int = DEFAULT_NODE_CONCURRENCY_CEILING
        self._total_node_wip: int = 0  # Total tasks across all lanes

        self._validate_config(self._config)
        logger.info(
            "LanePolicyEnforcer initialised",
            extra={"policy_version": policy_version, "lanes": list(self._config.keys())},
        )

    # ------------------------------------------------------------------
    # Primary query methods
    # ------------------------------------------------------------------

    def can_acquire_wip_slot(self, lane: WorkCellLane) -> tuple[bool, str]:
        """Check if a WIP slot can be acquired for the given lane.

        This method checks both lane-level WIP limit and node-level concurrency
        ceiling before allowing a task to proceed.

        Args:
            lane: The :class:`WorkCellLane` to check.

        Returns:
            A tuple of (allowed: bool, reason: str).
            - allowed=True when both lane and node have capacity.
            - allowed=False with reason when at capacity.
        """
        with self._lock:
            lane_key = lane.value
            current_lane_wip = self._lane_wip[lane_key]
            lane_limit = self._wip_limits.get(lane_key, DEFAULT_WIP_LIMITS.get(lane_key, 10))

            # Check lane-level limit
            if current_lane_wip >= lane_limit:
                return False, f"lane_wip_limit_reached:{current_lane_wip}/{lane_limit}"

            # Check node-level ceiling
            if self._total_node_wip >= self._node_concurrency_ceiling:
                return (
                    False,
                    f"node_concurrency_ceiling_reached:{self._total_node_wip}/{self._node_concurrency_ceiling}",
                )

            return True, "ok"

    def acquire_wip_slot(self, lane: WorkCellLane, task_id: str) -> bool:
        """Acquire a WIP slot for a task in the given lane.

        Thread-safe. Returns True if slot was acquired, False if at capacity.

        Args:
            lane: The :class:`WorkCellLane` to acquire a slot in.
            task_id: Identifier of the task (used in log messages).

        Returns:
            True if the WIP slot was acquired, False if at capacity.
        """
        with self._lock:
            allowed, reason = self.can_acquire_wip_slot(lane)
            if not allowed:
                logger.warning(
                    "LanePolicyEnforcer.acquire_wip_slot: denied task_id=%s lane=%s reason=%s",
                    task_id,
                    lane.value,
                    reason,
                )
                return False

            lane_key = lane.value
            self._lane_wip[lane_key] += 1
            self._total_node_wip += 1

            logger.debug(
                "LanePolicyEnforcer.acquire_wip_slot: acquired task_id=%s lane=%s "
                "lane_wip=%s node_wip=%s",
                task_id,
                lane_key,
                self._lane_wip[lane_key],
                self._total_node_wip,
            )
            return True

    def release_wip_slot(self, lane: WorkCellLane, task_id: str) -> None:
        """Release a WIP slot for a completed task.

        Thread-safe. Decrements both lane-level and node-level counters.

        Args:
            lane: The :class:`WorkCellLane` to release a slot from.
            task_id: Identifier of the task (used in log messages).
        """
        with self._lock:
            lane_key = lane.value
            if self._lane_wip[lane_key] > 0:
                self._lane_wip[lane_key] -= 1
            if self._total_node_wip > 0:
                self._total_node_wip -= 1

            logger.debug(
                "LanePolicyEnforcer.release_wip_slot: released task_id=%s lane=%s "
                "lane_wip=%s node_wip=%s",
                task_id,
                lane_key,
                self._lane_wip[lane_key],
                self._total_node_wip,
            )

    def get_concurrency_metrics(self) -> dict[str, Any]:
        """Return current concurrency metrics for monitoring.

        Thread-safe. Returns a snapshot of all WIP counters and limits.

        Returns:
            Dict containing:
            - per_lane: dict mapping lane value to {current, limit}
            - node_total: current total WIP across all lanes
            - node_ceiling: configured node concurrency ceiling
            - utilization_pct: node utilization as percentage
        """
        with self._lock:
            per_lane = {}
            for lane_key, current in self._lane_wip.items():
                limit = self._wip_limits.get(lane_key, DEFAULT_WIP_LIMITS.get(lane_key, 10))
                per_lane[lane_key] = {
                    "current": current,
                    "limit": limit,
                    "available": max(0, limit - current),
                }

            # Include lanes with no current WIP but with configured limits
            for lane_key, limit in self._wip_limits.items():
                if lane_key not in per_lane:
                    per_lane[lane_key] = {
                        "current": 0,
                        "limit": limit,
                        "available": limit,
                    }

            utilization = (
                (self._total_node_wip / self._node_concurrency_ceiling * 100)
                if self._node_concurrency_ceiling > 0
                else 0
            )

            return {
                "per_lane": per_lane,
                "node_total": self._total_node_wip,
                "node_ceiling": self._node_concurrency_ceiling,
                "node_available": max(0, self._node_concurrency_ceiling - self._total_node_wip),
                "utilization_pct": round(utilization, 2),
            }

    def update_wip_limit(self, lane: WorkCellLane, limit: int) -> None:
        """Update the WIP limit for a specific lane.

        Thread-safe.

        Args:
            lane: The :class:`WorkCellLane` to update.
            limit: New WIP limit (must be positive).

        Raises:
            ValueError: When limit is not positive.
        """
        if limit <= 0:
            raise ValueError(f"WIP limit must be positive, got {limit}")

        with self._lock:
            self._wip_limits[lane.value] = limit
            logger.info(
                "LanePolicyEnforcer.update_wip_limit: lane=%s new_limit=%s",
                lane.value,
                limit,
            )

    def update_node_concurrency_ceiling(self, ceiling: int) -> None:
        """Update the node-level concurrency ceiling.

        Thread-safe.

        Args:
            ceiling: New node concurrency ceiling (must be positive).

        Raises:
            ValueError: When ceiling is not positive.
        """
        if ceiling <= 0:
            raise ValueError(f"Node concurrency ceiling must be positive, got {ceiling}")

        with self._lock:
            old_ceiling = self._node_concurrency_ceiling
            self._node_concurrency_ceiling = ceiling
            logger.info(
                "LanePolicyEnforcer.update_node_concurrency_ceiling: old=%s new=%s",
                old_ceiling,
                ceiling,
            )

    def can_auto_complete(self, task: dict[str, Any]) -> bool:
        """Return True if the task's lane allows autonomous completion.

        A task can auto-complete only when all of the following hold:

        * The canonical lane is ``autonomous``.
        * The work-cell lane policy has ``can_auto_complete=True``.
        * The lane is not hard-gated.

        Args:
            task: A dict with at least two keys:
                  - ``"task_type"``: :class:`TaskType` enum value (or str).
                  - ``"risk_tier"``: :class:`RiskTier` enum value (or str).
                  Optional:
                  - ``"task_id"``: str identifier (used in log messages).

        Returns:
            True when the task may complete without human involvement.

        Raises:
            KeyError:   When ``task_type`` or ``risk_tier`` is absent.
            ValueError: When an unrecognised enum string is passed.
        """
        task_type, risk_tier = self._extract_task_fields(task)
        lane_policy = self._get_policy_for_task(task_type, risk_tier)
        result = lane_policy.can_auto_complete

        logger.debug(
            "LanePolicyEnforcer.can_auto_complete: task_id=%s type=%s risk=%s → %s",
            task.get("task_id", "<unknown>"),
            task_type.value,
            risk_tier.value,
            result,
        )
        return result

    def requires_human_review(self, task: dict[str, Any]) -> bool:
        """Return True if the task must have human approval before completion.

        Both ``human_review`` and ``blocked`` lanes require human involvement.
        Only ``autonomous`` tasks can reach the ``completed`` state without a
        human approval record.

        Args:
            task: Same dict structure as :meth:`can_auto_complete`.

        Returns:
            True when a human approval record must exist before the task can
            reach the ``completed`` state.

        Raises:
            KeyError:   When ``task_type`` or ``risk_tier`` is absent.
            ValueError: When an unrecognised enum string is passed.
        """
        task_type, risk_tier = self._extract_task_fields(task)
        lane_policy = self._get_policy_for_task(task_type, risk_tier)
        result = lane_policy.requires_human_review

        logger.debug(
            "LanePolicyEnforcer.requires_human_review: task_id=%s type=%s risk=%s → %s",
            task.get("task_id", "<unknown>"),
            task_type.value,
            risk_tier.value,
            result,
        )
        return result

    def get_lane_policy(self, lane: WorkCellLane) -> LanePolicy:
        """Return the :class:`LanePolicy` for a given work-cell lane.

        Args:
            lane: The :class:`WorkCellLane` to look up.

        Returns:
            The :class:`LanePolicy` describing the completion rules for the
            requested lane.

        Raises:
            KeyError: When the lane has no entry in the current config.
                      This should not occur for valid :class:`WorkCellLane`
                      values when using the default config.
        """
        with self._lock:
            entry = self._config[lane.value]
            wip_limit = self._wip_limits.get(lane.value, DEFAULT_WIP_LIMITS.get(lane.value, 10))
            return LanePolicy(
                lane=lane,
                can_auto_complete=entry["can_auto_complete"],
                requires_human_review=entry["requires_human_review"],
                is_hard_gated=entry["is_hard_gated"],
                wip_limit=wip_limit,
            )

    def evaluate(
        self,
        task_id: str,
        task_type: TaskType,
        risk_tier: RiskTier,
    ) -> PolicyDecision:
        """Produce a fully-populated :class:`PolicyDecision` for a task.

        This is the authoritative method for callers that need the complete
        policy picture in a single call (e.g. the scheduler or evaluator).

        Args:
            task_id:   Unique identifier of the task (used in logs and the
                       returned decision object).
            task_type: Canonical type of work.
            risk_tier: Assessed risk level.

        Returns:
            A :class:`PolicyDecision` with all fields populated.
        """
        with self._lock:
            canonical_lane = get_lane(task_type, risk_tier)
            work_cell_lane = self._resolver.resolve(task_type, risk_tier)
            lane_policy = self.get_lane_policy(work_cell_lane)

            decision = PolicyDecision(
                task_id=task_id,
                task_type=task_type,
                risk_tier=risk_tier,
                canonical_lane=canonical_lane,
                work_cell_lane=work_cell_lane,
                lane_policy=lane_policy,
                can_auto_complete=lane_policy.can_auto_complete,
                requires_human_review=lane_policy.requires_human_review,
                is_hard_gated=lane_policy.is_hard_gated,
                policy_version=self._policy_version,
            )

        logger.info(
            "LanePolicyEnforcer.evaluate: task_id=%s type=%s risk=%s "
            "canonical=%s cell=%s auto=%s human=%s gated=%s",
            task_id,
            task_type.value,
            risk_tier.value,
            canonical_lane.value,
            work_cell_lane.value,
            decision.can_auto_complete,
            decision.requires_human_review,
            decision.is_hard_gated,
        )
        return decision

    # ------------------------------------------------------------------
    # Config management
    # ------------------------------------------------------------------

    def update_config(self, updates: dict[str, dict[str, bool]]) -> None:
        """Merge *updates* into the current policy config.

        Only the lanes present in *updates* are changed; all other lanes
        keep their current config.  The merged result is validated before
        being committed so a bad update cannot leave the enforcer in an
        inconsistent state.

        Args:
            updates: A dict mapping ``WorkCellLane.value`` strings to new
                     policy flag dicts.  Each value dict should contain one
                     or more of: ``can_auto_complete``, ``requires_human_review``,
                     ``is_hard_gated``.

        Raises:
            ValueError: When the resulting config violates policy invariants.
        """
        with self._lock:
            merged = {k: dict(v) for k, v in self._config.items()}
            for lane_key, new_flags in updates.items():
                if lane_key not in merged:
                    merged[lane_key] = {}
                merged[lane_key].update(new_flags)
            self._validate_config(merged)
            self._config = merged
            logger.info(
                "LanePolicyEnforcer.update_config: updated lanes=%s",
                list(updates.keys()),
            )

    def get_config_snapshot(self) -> dict[str, dict[str, bool]]:
        """Return a deep copy of the current policy config.

        Returns:
            Dict mapping lane value strings to their policy flag dicts.
        """
        with self._lock:
            return {k: dict(v) for k, v in self._config.items()}

    def reset(self) -> None:
        """Restore the default policy config — intended for use in tests only.

        After calling ``reset()`` the enforcer uses :data:`DEFAULT_LANE_POLICY_CONFIG`.
        Also resets WIP limits and clears all active WIP counters.
        """
        with self._lock:
            self._config = {k: dict(v) for k, v in DEFAULT_LANE_POLICY_CONFIG.items()}
            self._policy_version = "default"
            self._wip_limits = dict(DEFAULT_WIP_LIMITS)
            self._lane_wip = defaultdict(int)
            self._total_node_wip = 0
            logger.debug("LanePolicyEnforcer reset to defaults")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_task_fields(task: dict[str, Any]) -> tuple[TaskType, RiskTier]:
        """Extract and coerce ``task_type`` and ``risk_tier`` from a task dict.

        Accepts both enum instances and their string values.

        Args:
            task: Dict with ``task_type`` and ``risk_tier`` keys.

        Returns:
            Tuple of (TaskType, RiskTier).

        Raises:
            KeyError:   When a required key is absent.
            ValueError: When an unrecognised string value is passed.
        """
        raw_type = task["task_type"]
        raw_risk = task["risk_tier"]

        task_type = raw_type if isinstance(raw_type, TaskType) else TaskType(raw_type)
        risk_tier = raw_risk if isinstance(raw_risk, RiskTier) else RiskTier(raw_risk)

        return task_type, risk_tier

    def _get_policy_for_task(self, task_type: TaskType, risk_tier: RiskTier) -> LanePolicy:
        """Internal: resolve work-cell lane and return its policy.

        Args:
            task_type: Resolved TaskType enum.
            risk_tier: Resolved RiskTier enum.

        Returns:
            The :class:`LanePolicy` for the resolved work-cell lane.
        """
        with self._lock:
            work_cell_lane = self._resolver.resolve(task_type, risk_tier)
            return self.get_lane_policy(work_cell_lane)

    @staticmethod
    def _validate_config(config: dict[str, dict[str, bool]]) -> None:
        """Validate all entries in *config* against policy invariants.

        Args:
            config: The full lane policy config to validate.

        Raises:
            ValueError: When any entry violates an invariant.
        """
        required_keys = {"can_auto_complete", "requires_human_review", "is_hard_gated"}
        for lane_key, flags in config.items():
            missing = required_keys - set(flags.keys())
            if missing:
                raise ValueError(f"Lane {lane_key!r} config is missing required keys: {missing}")
            # Probe the invariants by constructing a LanePolicy (raises on violation).
            try:
                lane_enum = WorkCellLane(lane_key)
            except ValueError as exc:
                raise ValueError(
                    f"Config key {lane_key!r} is not a valid WorkCellLane value."
                ) from exc

            LanePolicy(
                lane=lane_enum,
                can_auto_complete=flags["can_auto_complete"],
                requires_human_review=flags["requires_human_review"],
                is_hard_gated=flags["is_hard_gated"],
            )


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_enforcer_instance: LanePolicyEnforcer | None = None
_enforcer_lock: RLock = RLock()


def get_lane_policy_enforcer() -> LanePolicyEnforcer:
    """Return the global :class:`LanePolicyEnforcer` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe.

    To replace the singleton in tests call :func:`reset_lane_policy_enforcer`
    first.

    Returns:
        The singleton :class:`LanePolicyEnforcer`.
    """
    global _enforcer_instance
    with _enforcer_lock:
        if _enforcer_instance is None:
            _enforcer_instance = LanePolicyEnforcer()
            logger.info("LanePolicyEnforcer singleton created")
        return _enforcer_instance


def reset_lane_policy_enforcer() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to
    :func:`get_lane_policy_enforcer` will create a fresh instance.
    """
    global _enforcer_instance
    with _enforcer_lock:
        _enforcer_instance = None
        logger.debug("LanePolicyEnforcer singleton reset")
