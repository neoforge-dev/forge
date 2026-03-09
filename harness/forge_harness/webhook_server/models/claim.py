"""Completion Claim Model for evidence-based task verification in FORGE.

Defines the canonical claim states and legally valid transitions for
completion claims submitted by agents after finishing work.  Each claim
carries an :class:`EvidenceManifest` that captures the concrete artefacts
(commit, changed files, CI check results) that justify the completion.

State machine overview
-----------------------

    submitted ──► verifying ──► approved
                      │
                      └──────► rejected

Terminal states: approved, rejected
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class ClaimStatus(str, Enum):
    """Canonical lifecycle states for a completion claim.

    String-valued so that states round-trip through JSON without extra
    serialisation plumbing (mirrors the pattern used by TaskLifecycleState
    and DeliveryState).
    """

    SUBMITTED = "submitted"  # Claim received; not yet inspected.
    VERIFYING = "verifying"  # Claim is being evaluated against evidence.
    APPROVED = "approved"  # All evidence checks passed; claim accepted.
    REJECTED = "rejected"  # One or more evidence checks failed; claim denied.


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

#: Maps each status to the frozenset of statuses it may legally transition into.
#: An empty frozenset marks a terminal status.
VALID_CLAIM_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.SUBMITTED: frozenset(
        {
            ClaimStatus.VERIFYING,
        }
    ),
    ClaimStatus.VERIFYING: frozenset(
        {
            ClaimStatus.APPROVED,
            ClaimStatus.REJECTED,
        }
    ),
    # --- Terminal statuses (empty transition sets) ---
    ClaimStatus.APPROVED: frozenset(),
    ClaimStatus.REJECTED: frozenset(),
}

#: Convenience set of terminal statuses (no valid outbound transitions).
TERMINAL_CLAIM_STATUSES: frozenset[ClaimStatus] = frozenset(
    status for status, targets in VALID_CLAIM_TRANSITIONS.items() if not targets
)


# ---------------------------------------------------------------------------
# Transition helper
# ---------------------------------------------------------------------------


class ClaimTransitionError(ValueError):
    """Raised when a claim status transition violates the contract.

    Attributes:
        from_status: The status the claim is currently in.
        to_status:   The status that was requested.
        allowed:     The set of statuses that would have been valid.
    """

    def __init__(
        self,
        from_status: ClaimStatus,
        to_status: ClaimStatus,
        allowed: frozenset[ClaimStatus],
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        self.allowed = allowed

        if allowed:
            allowed_names = ", ".join(sorted(s.value for s in allowed))
            detail = f"allowed next statuses: [{allowed_names}]"
        else:
            detail = "status is terminal; no further transitions are permitted"

        super().__init__(
            f"Invalid claim transition {from_status.value!r} -> {to_status.value!r}: {detail}"
        )


def validate_claim_transition(
    from_status: ClaimStatus | str,
    to_status: ClaimStatus | str,
) -> bool:
    """Assert that a claim status transition is valid under the contract.

    Args:
        from_status: Current claim status (``ClaimStatus`` or its string value).
        to_status:   Requested next claim status.

    Returns:
        ``True`` when the transition is valid.

    Raises:
        ClaimTransitionError: When the requested transition is not in the contract.
        ValueError: When either argument is not a recognised ``ClaimStatus`` value.
    """
    if not isinstance(from_status, ClaimStatus):
        from_status = ClaimStatus(from_status)  # raises ValueError for unknown values
    if not isinstance(to_status, ClaimStatus):
        to_status = ClaimStatus(to_status)  # raises ValueError for unknown values

    allowed = VALID_CLAIM_TRANSITIONS[from_status]
    if to_status not in allowed:
        raise ClaimTransitionError(from_status, to_status, allowed)

    return True


# ---------------------------------------------------------------------------
# Evidence sub-model
# ---------------------------------------------------------------------------


class CheckResult(BaseModel):
    """Result of a single CI check (tests, lint, typecheck, etc.).

    Args:
        passed: Whether the check passed.
    """

    passed: bool = Field(..., description="Whether the check passed.")

    model_config = {"extra": "allow"}

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        """Accept and preserve extra fields supplied by the agent."""


class EvidenceManifest(BaseModel):
    """Evidence attached to a completion claim.

    Each field captures a concrete, verifiable artefact that the claim
    verifier inspects before transitioning to :attr:`ClaimStatus.APPROVED`
    or :attr:`ClaimStatus.REJECTED`.

    Args:
        task_id:       FORGE task identifier (e.g. ``"IS-219"``).
        agent_id:      Submitting agent identifier (e.g. ``"forge:nova"``).
        commit_sha:    Full or abbreviated git commit SHA produced by the agent.
        changed_files: Repository-relative paths of files modified in the commit.
        checks:        Mapping of check name to :class:`CheckResult`.  The
                       canonical keys are ``tests``, ``lint``, and ``typecheck``;
                       additional keys are allowed and preserved.
    """

    task_id: str = Field(..., description="FORGE task identifier.")
    agent_id: str = Field(..., description="Agent that performed the work.")
    commit_sha: str = Field(..., description="Git commit SHA for the work.")
    changed_files: list[str] = Field(
        default_factory=list,
        description="Repository-relative paths changed in the commit.",
    )
    checks: dict[str, CheckResult] = Field(
        default_factory=lambda: {
            "tests": CheckResult(passed=False),
            "lint": CheckResult(passed=False),
            "typecheck": CheckResult(passed=False),
        },
        description=(
            "CI check results keyed by check name.  Canonical keys: "
            "``tests``, ``lint``, ``typecheck``."
        ),
    )

    @property
    def all_checks_passed(self) -> bool:
        """Return ``True`` only when every check in the manifest passed."""
        return bool(self.checks) and all(c.passed for c in self.checks.values())


# ---------------------------------------------------------------------------
# Top-level claim model
# ---------------------------------------------------------------------------


class CompletionClaim(BaseModel):
    """Pydantic model representing a single evidence-based completion claim.

    Covers the full lifecycle from initial submission through verification
    to approval or rejection.

    Args:
        claim_id:         Unique claim identifier (UUID4, auto-generated).
        task_id:          FORGE task identifier.
        agent_id:         Agent that submitted the claim.
        status:           Current claim status.  Defaults to
                          :attr:`ClaimStatus.SUBMITTED`.
        evidence:         Evidence manifest attached to the claim.
        submitted_at:     UTC timestamp when the claim was created.
        verified_at:      UTC timestamp when verification completed (``None``
                          until then).
        rejection_reason: Human-readable reason populated when the claim is
                          rejected (``None`` for approved claims).
    """

    claim_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique claim identifier (UUID4).",
    )
    task_id: str = Field(..., description="FORGE task identifier.")
    agent_id: str = Field(..., description="Agent that submitted the claim.")
    status: ClaimStatus = Field(
        default=ClaimStatus.SUBMITTED,
        description="Current claim status.",
    )
    evidence: EvidenceManifest = Field(..., description="Evidence manifest.")
    submitted_at: datetime = Field(..., description="UTC timestamp when the claim was submitted.")
    verified_at: datetime | None = Field(
        None,
        description="UTC timestamp when verification completed.",
    )
    rejection_reason: str | None = Field(
        None,
        description="Populated when the claim is rejected.",
    )

    model_config = {"use_enum_values": False}
