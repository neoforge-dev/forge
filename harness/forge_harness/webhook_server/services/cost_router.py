"""Cost-Aware Model Routing Service — DF-3003
=============================================

Thread-safe singleton service that resolves the appropriate model tier for
every incoming task based on configured routing policies, quality targets, and
estimated token costs.

Architecture
------------
The router is a singleton wrapping the ``DEFAULT_ROUTING_POLICIES`` and
``COST_PROFILES`` defined in the model layer.  All decisions are persisted to
a JSONL log and kept in an in-memory deque for fast summary queries.

Resolution algorithm
--------------------
1. Look up the RoutingPolicy for (lane, risk_tier).
2. Apply the quality_target override if one is supplied by the caller.
3. Walk tiers from cheapest-to-most-expensive within [min_tier, max_tier].
4. Select the first tier whose quality_score >= quality_target.
5. If no tier meets the quality target, clamp to max_tier.
6. Estimate cost from COST_PROFILES and estimated token counts.
7. Persist the RoutingDecision and return it.

Fallback
--------
When no policy exists for a (lane, risk_tier) pair the router falls back to
ModelTier.standard and logs a warning.

Thread safety
-------------
All mutable state is guarded by a single :class:`threading.RLock`.

Persistence
-----------
Decisions are appended to ``.forge/routing/decisions.jsonl`` (relative to the
current working directory) so that cost history survives process restarts.

Singleton pattern
-----------------
Use :func:`get_cost_router` to obtain the shared instance.  In tests call
:func:`reset_cost_router` before each test to start from a clean slate.

Usage
-----
::

    from forge_harness.webhook_server.services.cost_router import get_cost_router
    from forge_harness.webhook_server.models.work_cell import WorkCellLane
    from forge_harness.webhook_server.models.lane_policy import RiskTier

    router = get_cost_router()
    decision = router.make_decision(
        task_id="task-001",
        lane=WorkCellLane.security_change,
        risk_tier=RiskTier.high,
        estimated_tokens=(2000, 500),
    )
    # decision.selected_tier → ModelTier.premium

Reference
---------
forge_harness/webhook_server/models/cost_routing.py  (models + policies)
forge_harness/webhook_server/models/work_cell.py     (WorkCellLane)
forge_harness/webhook_server/models/lane_policy.py   (RiskTier)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-3003)
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from threading import RLock

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.cost_routing import (
    COST_PROFILES,
    DEFAULT_ROUTING_POLICIES,
    ModelTier,
    RoutingDecision,
    RoutingPolicy,
)
from forge_harness.webhook_server.models.lane_policy import RiskTier
from forge_harness.webhook_server.models.work_cell import WorkCellLane

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum routing decisions held in the in-memory log.
_MAX_LOG_ENTRIES: int = 500

#: JSONL persistence path relative to the process working directory.
_DECISIONS_JSONL_PATH: str = ".forge/routing/decisions.jsonl"

#: Default token estimates used when the caller does not supply a token count.
_DEFAULT_INPUT_TOKENS: int = 1_000
_DEFAULT_OUTPUT_TOKENS: int = 500

#: Ascending tier order used for tier resolution walks.
_TIER_ASCENDING: list[ModelTier] = [
    ModelTier.economy,
    ModelTier.standard,
    ModelTier.premium,
]

#: Ordinal rank (used for clamping and comparisons).
_TIER_ORDER: dict[ModelTier, int] = {
    ModelTier.economy: 0,
    ModelTier.standard: 1,
    ModelTier.premium: 2,
}


# ---------------------------------------------------------------------------
# CostRouter
# ---------------------------------------------------------------------------


class CostRouter:
    """Resolves model tiers for tasks using cost and quality routing policies.

    Parameters
    ----------
    policies
        Mapping of (WorkCellLane, RiskTier) → RoutingPolicy.  Defaults to
        :data:`DEFAULT_ROUTING_POLICIES`.
    cost_profiles
        Mapping of ModelTier → CostProfile.  Defaults to
        :data:`COST_PROFILES`.
    decisions_path
        Path to the JSONL persistence file.  Defaults to
        ``.forge/routing/decisions.jsonl``.

    Attributes
    ----------
    _lock
        Re-entrant lock guarding all mutable state.
    _policies
        Reference to the routing policy table.
    _profiles
        Reference to the cost profile table.
    _log
        Bounded in-memory log of recent :class:`RoutingDecision` objects.
    _decisions_path
        Path to the JSONL persistence file.
    _total_estimated_cost
        Running total of estimated costs for all decisions (USD).
    _tier_counts
        Per-tier decision counts.
    """

    def __init__(
        self,
        policies: dict[tuple[WorkCellLane, RiskTier], RoutingPolicy] | None = None,
        cost_profiles: dict[ModelTier, CostProfile] | None = None,  # noqa: F821
        decisions_path: str | Path = _DECISIONS_JSONL_PATH,
    ) -> None:
        self._lock: RLock = RLock()
        self._policies = policies if policies is not None else DEFAULT_ROUTING_POLICIES
        self._profiles = cost_profiles if cost_profiles is not None else COST_PROFILES
        self._log: deque[RoutingDecision] = deque(maxlen=_MAX_LOG_ENTRIES)
        self._decisions_path: Path = Path(decisions_path)
        self._total_estimated_cost: float = 0.0
        self._tier_counts: dict[ModelTier, int] = {t: 0 for t in ModelTier}

        self._load_persisted_decisions()
        logger.info("CostRouter initialised (decisions_path=%s)", self._decisions_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_tier(
        self,
        lane: WorkCellLane,
        risk_tier: RiskTier,
        quality_target: float | None = None,
    ) -> ModelTier:
        """Return the cheapest model tier that satisfies the routing policy.

        Resolution steps:

        1. Look up the RoutingPolicy for (lane, risk_tier).  Fall back to the
           ``standard`` tier when no policy exists.
        2. Apply the caller-supplied ``quality_target`` override if given,
           clamped to [0.0, 1.0].
        3. Walk tiers in ascending cost order within [min_tier, max_tier].
        4. Return the first tier whose quality_score >= effective_quality_target.
        5. If none qualifies, return max_tier (best quality wins).

        Args:
            lane:           Target work-cell lane.
            risk_tier:      Risk tier of the task.
            quality_target: Optional quality target override in [0.0, 1.0].
                            When supplied this overrides the policy's default
                            quality_target.

        Returns:
            The selected :class:`ModelTier`.
        """
        policy = self._policies.get((lane, risk_tier))

        if policy is None:
            logger.warning(
                "CostRouter.resolve_tier: no policy for lane=%s risk_tier=%s; "
                "falling back to standard tier",
                lane.value,
                risk_tier.value,
            )
            return ModelTier.standard

        # Determine the effective quality target
        if quality_target is not None:
            effective_quality = max(0.0, min(1.0, quality_target))
        else:
            effective_quality = policy.quality_target

        min_rank = _TIER_ORDER[policy.min_model_tier]
        max_rank = _TIER_ORDER[policy.max_model_tier]

        # Walk tiers cheapest-first within the allowed range
        for tier in _TIER_ASCENDING:
            rank = _TIER_ORDER[tier]
            if rank < min_rank:
                continue
            if rank > max_rank:
                break
            profile = self._profiles[tier]
            if profile.quality_score >= effective_quality:
                return tier

        # No tier met the quality target within the allowed range — use max
        return policy.max_model_tier

    def estimate_cost(
        self,
        model_tier: ModelTier,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> float:
        """Return estimated cost in USD for the given tier and token counts.

        Cost = (input_tokens / 1000) * cost_per_1k_input
             + (output_tokens / 1000) * cost_per_1k_output

        Args:
            model_tier:               The model tier to price.
            estimated_input_tokens:   Estimated number of input tokens.
            estimated_output_tokens:  Estimated number of output tokens.

        Returns:
            Estimated cost in USD as a float rounded to 8 decimal places.
        """
        profile = self._profiles[model_tier]
        cost = (estimated_input_tokens / 1000.0) * profile.cost_per_1k_input_tokens + (
            estimated_output_tokens / 1000.0
        ) * profile.cost_per_1k_output_tokens
        return round(cost, 8)

    def make_decision(
        self,
        task_id: str,
        lane: WorkCellLane,
        risk_tier: RiskTier,
        estimated_tokens: tuple[int, int] | None = None,
    ) -> RoutingDecision:
        """Resolve the model tier for a task and record the routing decision.

        Steps:

        1. Resolve the model tier via :meth:`resolve_tier`.
        2. Estimate the task cost from token counts (or defaults when not
           supplied).
        3. Build a :class:`RoutingDecision` with full reasoning.
        4. Persist the decision to JSONL and the in-memory log.
        5. Update aggregate statistics.

        Args:
            task_id:          Unique task identifier.
            lane:             Target work-cell lane.
            risk_tier:        Risk tier of the task.
            estimated_tokens: Optional ``(input_tokens, output_tokens)`` tuple.
                              When omitted, default estimates are used.

        Returns:
            A :class:`RoutingDecision` capturing the full routing outcome.
        """
        reasoning: list[str] = []

        # 1 — resolve tier
        selected_tier = self.resolve_tier(lane, risk_tier)

        # 2 — determine token counts
        policy = self._policies.get((lane, risk_tier))
        if policy is not None:
            reasoning.append(
                f"Policy matched: lane={lane.value} risk_tier={risk_tier.value} "
                f"→ min={policy.min_model_tier.value} max={policy.max_model_tier.value} "
                f"quality_target={policy.quality_target:.2f}"
            )
            policy_ref = f"{lane.value}/{risk_tier.value}"
        else:
            reasoning.append(
                f"No policy for lane={lane.value} risk_tier={risk_tier.value}; "
                "using standard tier fallback"
            )
            policy_ref = "fallback/standard"

        reasoning.append(
            f"Selected tier: {selected_tier.value} "
            f"(quality_score={self._profiles[selected_tier].quality_score:.2f})"
        )

        # 3 — estimate cost
        if estimated_tokens is not None:
            input_tokens, output_tokens = estimated_tokens
        else:
            input_tokens, output_tokens = _DEFAULT_INPUT_TOKENS, _DEFAULT_OUTPUT_TOKENS
            reasoning.append(
                f"Token counts not supplied; using defaults "
                f"({_DEFAULT_INPUT_TOKENS} input, {_DEFAULT_OUTPUT_TOKENS} output)"
            )

        cost = self.estimate_cost(selected_tier, input_tokens, output_tokens)
        reasoning.append(
            f"Estimated cost: ${cost:.6f} USD "
            f"({input_tokens} input + {output_tokens} output tokens)"
        )

        # 4 — build decision
        quality_est = self._profiles[selected_tier].quality_score
        decision = RoutingDecision(
            task_id=task_id,
            selected_tier=selected_tier,
            routing_policy=policy_ref,
            cost_estimate=cost,
            quality_estimate=quality_est,
            reasoning=reasoning,
        )

        # 5 — persist and update stats
        self._append_decision(decision)

        logger.info(
            "CostRouter.make_decision: task=%s lane=%s risk_tier=%s "
            "tier=%s cost=$%.6f quality=%.2f",
            task_id,
            lane.value,
            risk_tier.value,
            selected_tier.value,
            cost,
            quality_est,
        )
        return decision

    def get_decision_log(self, limit: int = 50) -> list[RoutingDecision]:
        """Return recent routing decisions in reverse-chronological order.

        Args:
            limit: Maximum number of decisions to return.  Defaults to 50.

        Returns:
            List of :class:`RoutingDecision` objects, most recent first.
        """
        with self._lock:
            entries = list(self._log)
        entries.reverse()
        return entries[:limit]

    def get_cost_summary(self) -> dict:
        """Return aggregate statistics across all decisions made by this router.

        Returns:
            Dict with keys:

            - ``total_decisions`` (int): total decisions recorded.
            - ``by_tier`` (dict[str, int]): decision count per tier.
            - ``total_estimated_cost`` (float): cumulative estimated cost (USD).
            - ``avg_cost_per_task`` (float): average cost per decision (USD).
        """
        with self._lock:
            total = sum(self._tier_counts.values())
            by_tier = {tier.value: count for tier, count in self._tier_counts.items()}
            total_cost = self._total_estimated_cost

        avg_cost = (total_cost / total) if total > 0 else 0.0
        return {
            "total_decisions": total,
            "by_tier": by_tier,
            "total_estimated_cost": round(total_cost, 8),
            "avg_cost_per_task": round(avg_cost, 8),
        }

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_persisted_decisions(self) -> None:
        """Reload statistics from the JSONL persistence file on startup.

        Only aggregate counters are rebuilt (total cost, tier counts).
        Full decision objects are not restored to the in-memory log to keep
        startup fast.  Malformed lines are silently skipped.
        """
        if not self._decisions_path.exists():
            logger.debug(
                "CostRouter._load_persisted_decisions: no file at %s",
                self._decisions_path,
            )
            return

        loaded = 0
        with self._decisions_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    tier_value = record.get("selected_tier")
                    cost = float(record.get("cost_estimate", 0.0))
                    tier = ModelTier(tier_value)
                    self._tier_counts[tier] += 1
                    self._total_estimated_cost += cost
                    loaded += 1
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    logger.debug(
                        "CostRouter._load_persisted_decisions: skipping malformed line: %r",
                        line,
                    )

        logger.info(
            "CostRouter._load_persisted_decisions: reloaded stats from %d records",
            loaded,
        )

    def _persist_decision(self, decision: RoutingDecision) -> None:
        """Append a single decision record to the JSONL file.

        Creates parent directories when needed.  Write errors are logged but
        never re-raised so that the main routing flow is not interrupted.

        Args:
            decision: The :class:`RoutingDecision` to persist.
        """
        record = {
            "task_id": decision.task_id,
            "selected_tier": decision.selected_tier.value,
            "routing_policy": decision.routing_policy,
            "cost_estimate": decision.cost_estimate,
            "quality_estimate": decision.quality_estimate,
            "reasoning": decision.reasoning,
            "decided_at": decision.decided_at.isoformat(),
        }
        try:
            self._decisions_path.parent.mkdir(parents=True, exist_ok=True)
            with self._decisions_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning("CostRouter._persist_decision: write failed: %s", exc)

    def _append_decision(self, decision: RoutingDecision) -> None:
        """Update stats, in-memory log, and JSONL file for a new decision."""
        with self._lock:
            self._log.append(decision)
            self._tier_counts[decision.selected_tier] += 1
            self._total_estimated_cost += decision.cost_estimate
        self._persist_decision(decision)


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_router: CostRouter | None = None
_router_lock: RLock = RLock()


def get_cost_router() -> CostRouter:
    """Return the global :class:`CostRouter` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe.

    To replace the singleton (e.g. in tests) call :func:`reset_cost_router`
    first.

    Returns:
        The singleton :class:`CostRouter`.
    """
    global _router
    with _router_lock:
        if _router is None:
            _router = CostRouter()
            logger.info("CostRouter singleton created")
        return _router


def reset_cost_router() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to :func:`get_cost_router`
    will create a fresh instance.
    """
    global _router
    with _router_lock:
        _router = None
        logger.debug("CostRouter singleton reset")
