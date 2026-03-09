"""Lane Graduation Policy Service — DF-5005.

Formalises the criteria under which a Dark Factory work-cell lane may
"graduate" from requiring human oversight to operating fully autonomously.

Graduation Criteria
-------------------
A lane is eligible for graduation when **all** of the following hold:

* ``evaluator_pass_rate >= 0.95`` — 95%+ of tasks pass the automated evaluator.
* ``requeue_rate <= 0.05`` — at most 5% of tasks are re-queued (agent failures /
  retries that exceed the normal retry budget).
* ``escaped_defects == 0`` — zero defects that escaped the evaluator and were
  caught by a downstream human reviewer.
* ``human_touch_ratio <= 0.10`` — humans intervene in at most 10% of tasks.
* ``sample_size >= 20`` — the metrics window must contain at least 20 tasks for
  the decision to be statistically meaningful.

Hard-Blocked Lanes
------------------
The following lanes are **permanently ineligible** regardless of metrics:

* ``auth``
* ``security``
* ``deploy``
* ``billing``

These lanes involve irreversible or compliance-critical operations that mandate
a human gate by policy.  No set of metrics can override this restriction.

Singleton Pattern
-----------------
Use :func:`get_graduation_policy` to obtain the shared instance.  In tests,
construct a fresh :class:`LaneGraduationPolicy` directly — the service is
stateless, so no reset function is needed.

Usage
-----
::

    from forge_harness.webhook_server.services.graduation_policy import (
        LaneMetrics,
        get_graduation_policy,
    )

    policy = get_graduation_policy()
    metrics = LaneMetrics(
        evaluator_pass_rate=0.97,
        requeue_rate=0.03,
        escaped_defects=0,
        human_touch_ratio=0.05,
        sample_size=50,
    )
    decision = policy.check_graduation_eligibility(lane="api_simple", metrics=metrics)
    # GraduationDecision(eligible=True, blockers=[], ...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Lanes that are permanently ineligible for graduation, regardless of metrics.
#: These lanes involve irreversible or compliance-critical operations.
HARD_BLOCKED_LANES: frozenset[str] = frozenset({"auth", "security", "deploy", "billing"})

# Eligibility thresholds — expressed as module-level constants so they can be
# referenced from tests and documentation without hardcoding magic numbers.
_MIN_EVALUATOR_PASS_RATE: float = 0.95
_MAX_REQUEUE_RATE: float = 0.05
_MAX_ESCAPED_DEFECTS: int = 0
_MAX_HUMAN_TOUCH_RATIO: float = 0.10
_MIN_SAMPLE_SIZE: int = 20


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaneMetrics:
    """Snapshot of operational metrics for a single work-cell lane.

    All rate fields are expressed in the range [0.0, 1.0].

    Attributes:
        evaluator_pass_rate: Fraction of tasks that passed the automated
            evaluator in the measurement window.
        requeue_rate: Fraction of tasks that were re-queued after initial
            execution due to agent failure or retry budget exhaustion.
        escaped_defects: Count of defects that bypassed the evaluator and were
            caught by a downstream human reviewer.  Must be a non-negative int.
        human_touch_ratio: Fraction of tasks in which a human intervened
            (approved, corrected, or overrode an automated decision).
        sample_size: Total number of tasks included in the measurement window.
            Decisions based on fewer than :data:`_MIN_SAMPLE_SIZE` tasks are
            considered statistically insufficient.
    """

    evaluator_pass_rate: float
    requeue_rate: float
    escaped_defects: int
    human_touch_ratio: float
    sample_size: int


@dataclass(frozen=True)
class GraduationDecision:
    """Outcome of a graduation eligibility check.

    Attributes:
        eligible: True when the lane meets all graduation criteria and is not
            hard-blocked.  False otherwise.
        blockers: Human-readable descriptions of each failed criterion.  Empty
            when ``eligible`` is True.
        recommended_action: A short action string for the dashboard or operator.
            One of ``"graduate"``, ``"monitor"``, or ``"blocked"``.
        lane: The lane identifier this decision was computed for.
    """

    eligible: bool
    blockers: list[str] = field(default_factory=list)
    recommended_action: str = "monitor"
    lane: str = ""


# ---------------------------------------------------------------------------
# LaneGraduationPolicy
# ---------------------------------------------------------------------------


class LaneGraduationPolicy:
    """Evaluates whether a work-cell lane is ready to graduate.

    The policy is **stateless** — it does not track metrics across calls.
    Callers are responsible for computing and supplying :class:`LaneMetrics`
    from their own data stores.

    Thread safety: all public methods are read-only with respect to shared
    state, so concurrent calls are safe without additional locking.
    """

    def check_graduation_eligibility(
        self,
        lane: str,
        metrics: LaneMetrics,
    ) -> GraduationDecision:
        """Evaluate whether *lane* meets the graduation criteria.

        The check short-circuits immediately for lanes in
        :data:`HARD_BLOCKED_LANES` — no metric evaluation is performed.

        Args:
            lane: Identifier of the work-cell lane to evaluate
                (e.g. ``"api_simple"``).
            metrics: Current operational metrics snapshot for the lane.

        Returns:
            A :class:`GraduationDecision` describing the outcome.  When
            ``eligible`` is ``False`` the ``blockers`` list contains one entry
            per failed criterion.
        """
        logger.debug(
            "GraduationPolicy.check_graduation_eligibility: lane=%s sample_size=%d",
            lane,
            metrics.sample_size,
        )

        # ---- Hard-block check -------------------------------------------
        if lane in HARD_BLOCKED_LANES:
            decision = GraduationDecision(
                eligible=False,
                blockers=[
                    f"Lane '{lane}' is permanently blocked from graduation by policy."
                ],
                recommended_action="blocked",
                lane=lane,
            )
            logger.info(
                "GraduationPolicy: lane=%s is hard-blocked — decision=blocked",
                lane,
            )
            return decision

        # ---- Accumulate blockers for each failed criterion ---------------
        blockers: list[str] = []

        if metrics.sample_size < _MIN_SAMPLE_SIZE:
            blockers.append(
                f"Insufficient sample size: {metrics.sample_size} tasks "
                f"(minimum {_MIN_SAMPLE_SIZE} required)."
            )

        if metrics.evaluator_pass_rate < _MIN_EVALUATOR_PASS_RATE:
            blockers.append(
                f"Evaluator pass rate too low: "
                f"{metrics.evaluator_pass_rate:.4f} "
                f"(minimum {_MIN_EVALUATOR_PASS_RATE} required)."
            )

        if metrics.requeue_rate > _MAX_REQUEUE_RATE:
            blockers.append(
                f"Requeue rate too high: "
                f"{metrics.requeue_rate:.4f} "
                f"(maximum {_MAX_REQUEUE_RATE} allowed)."
            )

        if metrics.escaped_defects > _MAX_ESCAPED_DEFECTS:
            blockers.append(
                f"Escaped defects present: "
                f"{metrics.escaped_defects} "
                f"(must be {_MAX_ESCAPED_DEFECTS})."
            )

        if metrics.human_touch_ratio > _MAX_HUMAN_TOUCH_RATIO:
            blockers.append(
                f"Human touch ratio too high: "
                f"{metrics.human_touch_ratio:.4f} "
                f"(maximum {_MAX_HUMAN_TOUCH_RATIO} allowed)."
            )

        eligible = len(blockers) == 0
        recommended_action = "graduate" if eligible else "monitor"

        decision = GraduationDecision(
            eligible=eligible,
            blockers=blockers,
            recommended_action=recommended_action,
            lane=lane,
        )

        logger.info(
            "GraduationPolicy: lane=%s eligible=%s blockers=%d action=%s",
            lane,
            eligible,
            len(blockers),
            recommended_action,
        )
        return decision


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_policy_instance: LaneGraduationPolicy | None = None
_policy_lock: Lock = Lock()


def get_graduation_policy() -> LaneGraduationPolicy:
    """Return the global :class:`LaneGraduationPolicy` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe via a module-level :class:`~threading.Lock`.

    Returns:
        The singleton :class:`LaneGraduationPolicy`.
    """
    global _policy_instance
    with _policy_lock:
        if _policy_instance is None:
            _policy_instance = LaneGraduationPolicy()
            logger.info("LaneGraduationPolicy singleton created")
        return _policy_instance
