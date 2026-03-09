"""Claims Service for evidence-based task completion verification.

Provides a file-backed JSONL store and the full lifecycle for completion
claims: submission, verification, approval/rejection, and querying.

Usage
-----
Inject the singleton into application code::

    from forge_harness.webhook_server.services.claims_service import (
        get_claims_service,
    )

    svc = get_claims_service()
    claim = svc.submit(
        task_id="IS-219",
        agent_id="forge:nova",
        evidence=EvidenceManifest(
            task_id="IS-219",
            agent_id="forge:nova",
            commit_sha="abc1234",
            changed_files=["src/main.py"],
            checks={
                "tests": CheckResult(passed=True),
                "lint": CheckResult(passed=True),
                "typecheck": CheckResult(passed=True),
            },
        ),
    )
    verified = svc.verify(claim.claim_id)

To replace the singleton in tests call :func:`reset_claims_service` first.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.claim import (
    ClaimStatus,
    CompletionClaim,
    EvidenceManifest,
    validate_claim_transition,
)

logger = get_logger(__name__)

# Default JSONL storage path relative to FORGE repo root.
_DEFAULT_CLAIMS_PATH = Path(".forge/claims/claims.jsonl")


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class ClaimsService:
    """File-backed service for managing evidence-based completion claims.

    All claims are persisted to a JSONL file; each line is a
    JSON-serialised :class:`CompletionClaim`.  The in-memory cache
    (``_claims``) mirrors the file and is rebuilt on demand when the file
    is read by a fresh singleton.

    Thread safety is provided by a single :class:`threading.Lock` that
    guards both the in-memory cache and file I/O.

    Args:
        storage_path: Path to the JSONL backing file.  The parent directory
            is created automatically on first write.  Defaults to
            ``Path(".forge/claims/claims.jsonl")``.
    """

    def __init__(self, storage_path: Path = _DEFAULT_CLAIMS_PATH) -> None:
        self._path = storage_path
        self._lock: Lock = Lock()
        self._claims: dict[str, CompletionClaim] = {}
        self._loaded: bool = False
        logger.info(
            "ClaimsService initialised",
            extra={"storage_path": str(self._path)},
        )

    # ------------------------------------------------------------------
    # Internal persistence helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load claims from disk if not already loaded (lazy init)."""
        if self._loaded:
            return
        self._load()

    def _load(self) -> None:
        """Read all claims from the JSONL file into the in-memory cache.

        Missing file is treated as an empty store (not an error).
        """
        if not self._path.exists():
            self._loaded = True
            return

        loaded: dict[str, CompletionClaim] = {}
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    claim = CompletionClaim.model_validate(data)
                    loaded[claim.claim_id] = claim
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Skipping malformed claim on line %d: %s",
                        lineno,
                        exc,
                        extra={"path": str(self._path), "line_number": lineno},
                    )

        self._claims = loaded
        self._loaded = True
        logger.debug(
            "Loaded %d claims from disk",
            len(loaded),
            extra={"storage_path": str(self._path)},
        )

    def _rewrite(self) -> None:
        """Rewrite the full JSONL file from the in-memory cache.

        Called after every mutating operation to keep the file consistent
        with the in-memory state.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            for claim in self._claims.values():
                fh.write(claim.model_dump_json() + "\n")

    def _persist_claim(self, claim: CompletionClaim) -> None:
        """Append or update a claim in both the cache and the backing file."""
        self._claims[claim.claim_id] = claim
        self._rewrite()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(
        self,
        task_id: str,
        agent_id: str,
        evidence: EvidenceManifest,
    ) -> CompletionClaim:
        """Create and persist a new completion claim.

        The claim is initialised with :attr:`ClaimStatus.SUBMITTED` and the
        current UTC timestamp.

        Args:
            task_id:   FORGE task identifier (e.g. ``"IS-219"``).
            agent_id:  Submitting agent identifier (e.g. ``"forge:nova"``).
            evidence:  Evidence manifest describing the completed work.

        Returns:
            The newly created :class:`CompletionClaim`.
        """
        claim = CompletionClaim(
            task_id=task_id,
            agent_id=agent_id,
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        with self._lock:
            self._ensure_loaded()
            self._persist_claim(claim)

        logger.info(
            "Claim submitted",
            extra={
                "claim_id": claim.claim_id,
                "task_id": task_id,
                "agent_id": agent_id,
            },
        )
        return claim

    def verify(self, claim_id: str) -> CompletionClaim:
        """Run evidence verification and transition the claim to approved or rejected.

        Verification proceeds in two steps:

        1. ``submitted`` -> ``verifying`` — the claim enters the verification
           phase (validates the transition is legal).
        2. ``verifying`` -> ``approved`` | ``rejected`` — the evidence
           manifest is inspected; if all checks pass the claim is approved,
           otherwise it is rejected with a reason listing the failed checks.

        Args:
            claim_id: The :attr:`CompletionClaim.claim_id` to verify.

        Returns:
            The updated :class:`CompletionClaim` after verification.

        Raises:
            KeyError:  When ``claim_id`` is not found.
            ClaimTransitionError: When the current status does not permit
                transitioning to ``verifying``.
        """
        with self._lock:
            self._ensure_loaded()
            claim = self._claims.get(claim_id)
            if claim is None:
                raise KeyError(f"Claim not found: {claim_id!r}")

            # Step 1: submitted -> verifying
            validate_claim_transition(claim.status, ClaimStatus.VERIFYING)
            claim = claim.model_copy(
                update={"status": ClaimStatus.VERIFYING}
            )
            self._persist_claim(claim)

        logger.debug(
            "Claim transitioned to verifying",
            extra={"claim_id": claim_id},
        )

        # Step 2: inspect evidence, transition to approved or rejected
        evidence = claim.evidence
        failed_checks = [
            name for name, result in evidence.checks.items() if not result.passed
        ]
        verified_at = datetime.now(UTC)

        if not failed_checks:
            new_status = ClaimStatus.APPROVED
            rejection_reason = None
        else:
            new_status = ClaimStatus.REJECTED
            rejection_reason = "Failed checks: " + ", ".join(sorted(failed_checks))

        with self._lock:
            validate_claim_transition(claim.status, new_status)
            claim = claim.model_copy(
                update={
                    "status": new_status,
                    "verified_at": verified_at,
                    "rejection_reason": rejection_reason,
                }
            )
            self._persist_claim(claim)

        logger.info(
            "Claim verified",
            extra={
                "claim_id": claim_id,
                "status": new_status.value,
                "rejection_reason": rejection_reason,
            },
        )
        return claim

    def list_claims(
        self,
        status: ClaimStatus | None = None,
    ) -> list[CompletionClaim]:
        """Return all claims, optionally filtered by status.

        Args:
            status: When provided, only claims with this
                :attr:`CompletionClaim.status` are returned.

        Returns:
            List of matching :class:`CompletionClaim` objects, ordered by
            :attr:`CompletionClaim.submitted_at` ascending.
        """
        with self._lock:
            self._ensure_loaded()
            claims = list(self._claims.values())

        if status is not None:
            claims = [c for c in claims if c.status == status]

        claims.sort(key=lambda c: c.submitted_at)
        return claims

    def get_claim(self, claim_id: str) -> CompletionClaim | None:
        """Return the claim with the given ID, or ``None`` if not found.

        Args:
            claim_id: The :attr:`CompletionClaim.claim_id` to look up.

        Returns:
            :class:`CompletionClaim` when found, ``None`` otherwise.
        """
        with self._lock:
            self._ensure_loaded()
            return self._claims.get(claim_id)

    def get_stats(self) -> dict[str, int]:
        """Return counts of claims grouped by status.

        Returns:
            Dict mapping each :attr:`ClaimStatus` value (string) to its
            count.  Statuses with zero claims are included with a count of
            ``0`` so callers can rely on a fixed key set.

        Example::

            {
                "submitted": 2,
                "verifying": 0,
                "approved": 5,
                "rejected": 1,
            }
        """
        with self._lock:
            self._ensure_loaded()
            counts: dict[str, int] = {s.value: 0 for s in ClaimStatus}
            for claim in self._claims.values():
                key = (
                    claim.status.value
                    if isinstance(claim.status, ClaimStatus)
                    else str(claim.status)
                )
                counts[key] = counts.get(key, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_service_instance: ClaimsService | None = None
_service_lock: Lock = Lock()


def get_claims_service(
    storage_path: Path | None = None,
) -> ClaimsService:
    """Return the global :class:`ClaimsService` singleton.

    On the first call the singleton is constructed with ``storage_path``
    (defaulting to ``Path(".forge/claims/claims.jsonl")``).  Subsequent calls
    return the cached instance and ignore ``storage_path``.

    To replace the singleton (e.g. in tests) call
    :func:`reset_claims_service` first.

    Args:
        storage_path: Override the JSONL backing file path.  Only honoured
            on the first call.

    Returns:
        The singleton :class:`ClaimsService`.
    """
    global _service_instance
    with _service_lock:
        if _service_instance is None:
            path = storage_path if storage_path is not None else _DEFAULT_CLAIMS_PATH
            _service_instance = ClaimsService(storage_path=path)
        return _service_instance


def reset_claims_service() -> None:
    """Reset the global singleton — intended for use in tests only."""
    global _service_instance
    with _service_lock:
        _service_instance = None
