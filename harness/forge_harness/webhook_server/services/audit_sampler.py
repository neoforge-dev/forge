"""Audit Sampling Pipeline — DF-4003.

Implements a configurable sampling gate for Dark Factory audit records.
The sampler answers two questions for every task event:

1. **Should this event be recorded in the audit log?** — answered by
   :meth:`AuditSampler.should_sample`.
2. **Write the audit record.** — performed by :meth:`AuditSampler.record_audit`.

Sampling Behaviour
------------------
* Default sample rate is 12% (0.12) for all lanes that are not
  human-gated.
* Human-gated lanes (``security_change``, ``auth``, ``billing``,
  ``deployment``) are **always** audited (effective rate = 100%).
* Lane-level overrides can be passed to the constructor to tune rates
  for specific lanes independently of the default.
* Setting the ``AUDIT_SAMPLE_SEED`` environment variable seeds the
  Python :mod:`random` module for deterministic replay in tests.

Audit Log
---------
Records are appended as JSON lines to
``.forge/audit/dark_factory_audit.jsonl`` (relative to the FORGE
repository root).  The directory is created automatically if it does not
exist.

Usage
-----
::

    from forge_harness.webhook_server.services.audit_sampler import (
        AuditSampler,
        default_sampler,
    )

    # Use the module-level singleton
    if default_sampler.should_sample(task_id="abc123", lane="api_simple"):
        record = default_sampler.record_audit(
            task_id="abc123",
            lane="api_simple",
            outcome={"status": "completed", "evaluator_passed": True},
            sampler_decision=True,
        )

    # Or construct a custom sampler for testing
    sampler = AuditSampler(
        sample_rate=0.5,
        lane_overrides={"docs": 1.0},
        audit_log_path="/tmp/test_audit.jsonl",
    )
"""

from __future__ import annotations

import json
import os
import random
import socket
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default sampling rate applied to all lanes unless overridden.
_DEFAULT_SAMPLE_RATE: float = 0.12

#: Lanes that are always audited regardless of the configured sample rate.
#: These correspond to human-gated lanes where a complete audit trail is
#: mandatory for compliance and security review.
ALWAYS_AUDITED_LANES: frozenset[str] = frozenset(
    {
        "security_change",
        "auth",
        "billing",
        "deployment",
    }
)

#: Environment variable used to seed the random number generator for
#: deterministic behaviour in tests and replays.
_SEED_ENV_VAR = "AUDIT_SAMPLE_SEED"

#: Relative path (from the FORGE root) where audit records are written.
_DEFAULT_AUDIT_SUBPATH = Path(".forge") / "audit" / "dark_factory_audit.jsonl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_forge_root() -> Path:
    """Walk up the directory tree to find the FORGE repository root.

    Heuristic: the root is the first ancestor directory that contains a
    ``harness/`` sub-directory **or** a ``pyproject.toml`` at the harness
    level.  Falls back to the current working directory.

    Returns:
        The resolved FORGE root :class:`~pathlib.Path`.
    """
    current = Path(__file__).resolve().parent
    # Walk up a maximum of 10 levels to avoid traversing the whole filesystem.
    for _ in range(10):
        # harness/forge_harness/webhook_server/services/ → harness is 3 levels up
        # FORGE root is 4 levels up from this file.
        candidate_harness = current / "harness"
        if candidate_harness.is_dir() and (candidate_harness / "forge_harness").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # Fallback: treat CWD as root so the path is still deterministic.
    return Path.cwd()


def _seed_random_if_env_set() -> None:
    """Seed the global :mod:`random` state from ``AUDIT_SAMPLE_SEED`` if set.

    This is called once at :class:`AuditSampler` instantiation time so that
    tests can produce repeatable sampling decisions by setting the env var
    before creating the sampler.
    """
    raw = os.environ.get(_SEED_ENV_VAR)
    if raw is not None:
        try:
            seed_value = int(raw)
        except ValueError:
            # Non-integer seeds are accepted by random.seed() as-is.
            seed_value = raw  # type: ignore[assignment]
        random.seed(seed_value)
        logger.debug(
            "AuditSampler: random seeded from %s=%r",
            _SEED_ENV_VAR,
            raw,
            extra={"seed": raw},
        )


# ---------------------------------------------------------------------------
# AuditSampler
# ---------------------------------------------------------------------------


class AuditSampler:
    """Configurable sampling gate for Dark Factory audit records.

    Args:
        sample_rate: Default fraction of non-gated task events to audit.
            Must be in the range [0.0, 1.0].  Defaults to 0.12 (12%).
        lane_overrides: Optional mapping of lane name to a per-lane
            sample rate.  Values outside [0.0, 1.0] raise
            :class:`ValueError` at construction time.  Gated lanes in
            :data:`ALWAYS_AUDITED_LANES` are always 1.0 regardless of
            any override supplied here.
        audit_log_path: Explicit path to the JSONL audit file.  When
            ``None`` (default) the path is resolved as
            ``{forge_root}/.forge/audit/dark_factory_audit.jsonl``.
        node_id: Identifier embedded in every audit record (e.g.
            ``"forge:nova"``).  Defaults to the machine hostname.
    """

    def __init__(
        self,
        sample_rate: float = _DEFAULT_SAMPLE_RATE,
        lane_overrides: dict[str, float] | None = None,
        audit_log_path: str | Path | None = None,
        node_id: str | None = None,
    ) -> None:
        if not (0.0 <= sample_rate <= 1.0):
            raise ValueError(
                f"sample_rate must be in [0.0, 1.0], got {sample_rate!r}"
            )

        self._sample_rate: float = sample_rate
        self._lane_overrides: dict[str, float] = {}
        self._write_lock = Lock()

        if lane_overrides:
            for lane_name, rate in lane_overrides.items():
                if not (0.0 <= rate <= 1.0):
                    raise ValueError(
                        f"lane_overrides[{lane_name!r}] must be in [0.0, 1.0], "
                        f"got {rate!r}"
                    )
                self._lane_overrides[lane_name] = rate

        # Resolve audit log path.
        if audit_log_path is not None:
            self._audit_path = Path(audit_log_path)
        else:
            forge_root = _resolve_forge_root()
            self._audit_path = forge_root / _DEFAULT_AUDIT_SUBPATH

        # Ensure parent directory exists.
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)

        # Node identifier embedded in every record.
        self._node_id: str = node_id or socket.gethostname()

        # Seed RNG if the env var is set.
        _seed_random_if_env_set()

        logger.info(
            "AuditSampler initialised",
            extra={
                "sample_rate": self._sample_rate,
                "lane_overrides": self._lane_overrides,
                "audit_log_path": str(self._audit_path),
                "node_id": self._node_id,
            },
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_sample(self, task_id: str, lane: str) -> bool:
        """Decide whether a task event should be written to the audit log.

        Human-gated lanes (:data:`ALWAYS_AUDITED_LANES`) always return
        ``True``.  For all other lanes the decision is probabilistic,
        using :func:`random.random` against the effective sample rate.

        Args:
            task_id: Opaque task identifier (used for logging only).
            lane: Work-cell lane name (e.g. ``"api_simple"``,
                ``"security_change"``).

        Returns:
            ``True`` if the event should be recorded; ``False`` otherwise.
        """
        # Always audit human-gated lanes.
        if lane in ALWAYS_AUDITED_LANES:
            logger.debug(
                "AuditSampler: always auditing gated lane",
                extra={"task_id": task_id, "lane": lane},
            )
            return True

        effective_rate = self._lane_overrides.get(lane, self._sample_rate)
        decision = random.random() < effective_rate  # noqa: S311 — not cryptographic

        logger.debug(
            "AuditSampler: sampling decision",
            extra={
                "task_id": task_id,
                "lane": lane,
                "effective_rate": effective_rate,
                "sampled": decision,
            },
        )
        return decision

    def record_audit(
        self,
        task_id: str,
        lane: str,
        outcome: dict[str, Any],
        sampler_decision: bool,
    ) -> dict[str, Any]:
        """Build an audit record and append it to the JSONL audit log.

        The record is written regardless of *sampler_decision* — it is
        the caller's responsibility to pass the value returned by
        :meth:`should_sample`.  Recording the decision itself (including
        ``audited=False``) allows operators to reconstruct the full
        sampling trace.

        Args:
            task_id: Opaque identifier of the task being audited.
            lane: Work-cell lane the task executed in.
            outcome: Arbitrary outcome dict (e.g. evaluator result,
                status, timing).  Must be JSON-serialisable.
            sampler_decision: The boolean returned by
                :meth:`should_sample` for this task.  Stored verbatim
                in the record as the ``audited`` field.

        Returns:
            The audit record dict that was written to disk.  Callers can
            inspect or forward it without re-reading the log file.

        Raises:
            OSError: If the log file cannot be opened for appending after
                the directory has been created.  The exception is logged
                at WARNING level and then re-raised so callers can
                implement retry logic if required.
        """
        record: dict[str, Any] = {
            "task_id": task_id,
            "lane": lane,
            "outcome": outcome,
            "sampled_at": datetime.now(UTC).isoformat(),
            "audited": sampler_decision,
            "node_id": self._node_id,
        }

        self._append_record(record)
        return record

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_record(self, record: dict[str, Any]) -> None:
        """Serialise *record* as a JSON line and append to the audit file.

        Thread-safe via :attr:`_write_lock`.

        Args:
            record: The audit record dict to persist.

        Raises:
            OSError: Propagated from file I/O failures after logging.
        """
        line = json.dumps(record, default=str) + "\n"
        try:
            with self._write_lock:
                with open(self._audit_path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except OSError as exc:
            logger.warning(
                "AuditSampler: failed to write audit record",
                extra={
                    "task_id": record.get("task_id"),
                    "lane": record.get("lane"),
                    "path": str(self._audit_path),
                    "error": str(exc),
                },
            )
            raise

    # ------------------------------------------------------------------
    # Introspection helpers (useful for tests and operators)
    # ------------------------------------------------------------------

    @property
    def sample_rate(self) -> float:
        """The default sample rate this sampler was constructed with."""
        return self._sample_rate

    @property
    def audit_log_path(self) -> Path:
        """Absolute path to the JSONL audit log file."""
        return self._audit_path

    def effective_rate_for_lane(self, lane: str) -> float:
        """Return the effective sample rate for *lane*.

        Returns 1.0 for lanes in :data:`ALWAYS_AUDITED_LANES`.

        Args:
            lane: Work-cell lane name.

        Returns:
            Float in [0.0, 1.0] representing the probability that a task
            in *lane* is selected for auditing.
        """
        if lane in ALWAYS_AUDITED_LANES:
            return 1.0
        return self._lane_overrides.get(lane, self._sample_rate)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: Default sampler instance.  Import and use this for normal production code.
#: Override ``AUDIT_SAMPLE_SEED`` before the module is imported to get
#: deterministic behaviour in tests.
default_sampler: AuditSampler = AuditSampler()
