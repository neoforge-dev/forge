"""Feature Tracker Service

Provides progress tracking for features across the portfolio, linking agent
work sessions to specific features with JSONL-backed persistence and
thread-safe singleton access.

Features are defined in ``features.json`` files per project. Progress records
are persisted as JSONL at ``.forge/features/progress.jsonl``.

Usage::

    from forge_harness.webhook_server.services.feature_tracker import (
        get_feature_tracker,
        reset_feature_tracker,
    )

    tracker = get_feature_tracker()

    features = tracker.load_features(domain="codeswiftr-com")
    tracker.update_progress("feat-001", completion_pct=50, status="in_progress")
    tracker.assign_agent("feat-001", "forge:opencode")
    tracker.link_task("feat-001", "task-abc123")

    stats = tracker.get_stats()
    # {"total": 1, "by_status": {"in_progress": 1}, "by_domain": {"codeswiftr-com": 1}}
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tracker_instance: FeatureTracker | None = None
_tracker_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Valid feature statuses
# ---------------------------------------------------------------------------

FeatureStatus = Literal["planned", "in_progress", "testing", "done"]

_VALID_STATUSES: frozenset[str] = frozenset(["planned", "in_progress", "testing", "done"])


# ---------------------------------------------------------------------------
# FeatureProgress model
# ---------------------------------------------------------------------------


class FeatureProgress(BaseModel):
    """Progress record for a single feature.

    Attributes:
        feature_id:     Unique identifier for the feature.
        name:           Human-readable feature name.
        domain:         Domain identifier (e.g. ``"codeswiftr-com"``).
        project:        Project identifier (e.g. ``"interview-sim"``).
        status:         Current status: planned, in_progress, testing, done.
        completion_pct: Percentage complete (0–100).
        assigned_agent: Agent ID currently working on this feature, if any.
        tasks:          List of task IDs linked to this feature.
        started_at:     UTC datetime when the feature was first tracked.
        updated_at:     UTC datetime of the last update.
    """

    feature_id: str
    name: str
    domain: str
    project: str
    status: str = "planned"
    completion_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    assigned_agent: str | None = None
    tasks: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON persistence."""
        return {
            "feature_id": self.feature_id,
            "name": self.name,
            "domain": self.domain,
            "project": self.project,
            "status": self.status,
            "completion_pct": self.completion_pct,
            "assigned_agent": self.assigned_agent,
            "tasks": list(self.tasks),
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeatureProgress:
        """Deserialise from a plain dict."""

        def _parse_dt(val: Any) -> datetime:
            if isinstance(val, datetime):
                if val.tzinfo is None:
                    return val.replace(tzinfo=UTC)
                return val
            if isinstance(val, str):
                dt = datetime.fromisoformat(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            return datetime.now(UTC)

        return cls(
            feature_id=data.get("feature_id", str(uuid.uuid4())),
            name=data.get("name", ""),
            domain=data.get("domain", "unknown"),
            project=data.get("project", "unknown"),
            status=data.get("status", "planned"),
            completion_pct=float(data.get("completion_pct", 0.0)),
            assigned_agent=data.get("assigned_agent"),
            tasks=list(data.get("tasks", [])),
            started_at=_parse_dt(data.get("started_at", datetime.now(UTC))),
            updated_at=_parse_dt(data.get("updated_at", datetime.now(UTC))),
        )


# ---------------------------------------------------------------------------
# FeatureTracker
# ---------------------------------------------------------------------------


class FeatureTracker:
    """
    Progress tracker for portfolio features.

    Reads feature definitions from ``features.json`` files under *features_dir*
    (the FORGE portfolio root) and persists progress records as JSONL at
    ``.forge/features/progress.jsonl`` relative to the working directory or an
    injected *progress_file* path.

    All public methods are thread-safe via an internal RLock.

    Attributes:
        features_dir: Root directory to scan for ``features.json`` files.
        progress_file: Path to the JSONL progress store.
    """

    def __init__(
        self,
        features_dir: Path | str | None = None,
        progress_file: Path | str | None = None,
    ) -> None:
        """Initialise the FeatureTracker.

        Args:
            features_dir: Root directory to scan for ``features.json`` files.
                          Defaults to the current working directory.
            progress_file: Path to the JSONL progress file.
                           Defaults to ``.forge/features/progress.jsonl``
                           relative to the current working directory.
        """
        if features_dir is None:
            features_dir = Path(".")
        self.features_dir = Path(features_dir)

        if progress_file is None:
            progress_file = Path(".forge") / "features" / "progress.jsonl"
        self.progress_file = Path(progress_file)

        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_progress_dir(self) -> None:
        """Create progress file's parent directory if absent."""
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)

    def _read_all_progress(self) -> dict[str, FeatureProgress]:
        """Load all progress records from disk into a dict keyed by feature_id."""
        records: dict[str, FeatureProgress] = {}
        if not self.progress_file.exists():
            return records

        with self.progress_file.open() as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    fp = FeatureProgress.from_dict(data)
                    records[fp.feature_id] = fp
                except (json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
                    logger.warning(f"Skipping malformed progress record at line {lineno}: {exc}")

        return records

    def _write_all_progress(self, records: dict[str, FeatureProgress]) -> None:
        """Atomically rewrite the entire JSONL progress file."""
        self._ensure_progress_dir()
        try:
            lines = [json.dumps(fp.to_dict(), default=str) for fp in records.values()]
            self.progress_file.write_text("\n".join(lines) + ("\n" if lines else ""))
        except OSError as exc:
            logger.error(f"Failed to write feature progress file: {exc}")

    def _get_or_create_progress(
        self,
        feature_id: str,
        records: dict[str, FeatureProgress],
        *,
        name: str = "",
        domain: str = "unknown",
        project: str = "unknown",
    ) -> FeatureProgress:
        """Return existing progress record or create a new one in *records*."""
        if feature_id not in records:
            records[feature_id] = FeatureProgress(
                feature_id=feature_id,
                name=name,
                domain=domain,
                project=project,
            )
        return records[feature_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_features(
        self,
        domain: str | None = None,
        project: str | None = None,
    ) -> list[FeatureProgress]:
        """Load feature progress records with optional filters.

        Features with no existing progress record are returned with default
        ``planned`` status and 0% completion.  If a ``features.json`` file
        is present under *features_dir*, feature definitions are merged with
        any stored progress.

        Args:
            domain:  Optional domain filter.
            project: Optional project filter.

        Returns:
            List of FeatureProgress records, sorted by feature_id.
        """
        with self._lock:
            records = self._read_all_progress()

            # Augment with feature definitions from features.json files
            for feat_file in self.features_dir.rglob("features.json"):
                try:
                    raw = json.loads(feat_file.read_text())
                    # features.json may be a list or a dict with a "features" key
                    items: list[dict[str, Any]] = []
                    if isinstance(raw, list):
                        items = raw
                    elif isinstance(raw, dict):
                        items = raw.get("features", [])

                    for item in items:
                        fid = item.get("id") or item.get("feature_id")
                        if not fid:
                            continue
                        fname = item.get("name") or item.get("title") or fid
                        # Infer domain/project from file path relative to features_dir
                        rel = feat_file.relative_to(self.features_dir)
                        parts = rel.parts
                        inferred_domain = parts[0] if len(parts) >= 2 else "unknown"
                        inferred_project = parts[1] if len(parts) >= 3 else "unknown"
                        feat_domain = item.get("domain", inferred_domain)
                        feat_project = item.get("project", inferred_project)

                        if fid not in records:
                            records[fid] = FeatureProgress(
                                feature_id=fid,
                                name=fname,
                                domain=feat_domain,
                                project=feat_project,
                            )
                        else:
                            # Update name/domain/project from definition if missing
                            fp = records[fid]
                            if not fp.name:
                                object.__setattr__(fp, "name", fname)  # type: ignore[call-overload]
                            if fp.domain == "unknown":
                                object.__setattr__(fp, "domain", feat_domain)
                            if fp.project == "unknown":
                                object.__setattr__(fp, "project", feat_project)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Could not parse features.json at {feat_file}: {exc}")

            results = list(records.values())

            # Apply filters
            if domain is not None:
                results = [r for r in results if r.domain == domain]
            if project is not None:
                results = [r for r in results if r.project == project]

            results.sort(key=lambda r: r.feature_id)
            return results

    def get_feature(self, feature_id: str) -> FeatureProgress | None:
        """Retrieve a single feature progress record by ID.

        Args:
            feature_id: The feature identifier.

        Returns:
            FeatureProgress if found, None otherwise.
        """
        with self._lock:
            records = self._read_all_progress()
            return records.get(feature_id)

    def update_progress(
        self,
        feature_id: str,
        completion_pct: float,
        status: str | None = None,
        name: str = "",
        domain: str = "unknown",
        project: str = "unknown",
    ) -> FeatureProgress:
        """Update the completion percentage (and optionally status) for a feature.

        Creates a new progress record if none exists yet.

        Args:
            feature_id:     The feature identifier.
            completion_pct: New completion percentage (0–100).
            status:         Optional new status.  If omitted the status is
                            auto-derived: 0% → planned, 1–99% → in_progress,
                            100% → done.
            name:           Feature name (used only when creating a new record).
            domain:         Domain (used only when creating a new record).
            project:        Project (used only when creating a new record).

        Returns:
            The updated FeatureProgress.

        Raises:
            ValueError: If completion_pct is outside [0, 100] or status is invalid.
        """
        if not (0.0 <= completion_pct <= 100.0):
            raise ValueError(f"completion_pct must be in [0, 100], got {completion_pct}")

        if status is not None and status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of: {sorted(_VALID_STATUSES)}"
            )

        with self._lock:
            records = self._read_all_progress()
            fp = self._get_or_create_progress(
                feature_id, records, name=name, domain=domain, project=project
            )
            fp.completion_pct = completion_pct

            if status is not None:
                fp.status = status
            else:
                # Auto-derive status from percentage
                if completion_pct == 0.0:
                    fp.status = "planned"
                elif completion_pct >= 100.0:
                    fp.status = "done"
                else:
                    fp.status = "in_progress"

            fp.updated_at = datetime.now(UTC)
            self._write_all_progress(records)
            logger.info(
                f"Updated feature {feature_id}: {completion_pct:.1f}% / {fp.status}"
            )
            return fp

    def assign_agent(self, feature_id: str, agent_id: str) -> FeatureProgress:
        """Assign an agent to work on a feature.

        Creates a progress record if none exists.

        Args:
            feature_id: The feature identifier.
            agent_id:   The agent identifier to assign.

        Returns:
            The updated FeatureProgress.
        """
        with self._lock:
            records = self._read_all_progress()
            fp = self._get_or_create_progress(feature_id, records)
            fp.assigned_agent = agent_id
            fp.updated_at = datetime.now(UTC)
            if fp.status == "planned" and agent_id:
                fp.status = "in_progress"
            self._write_all_progress(records)
            logger.info(f"Assigned agent {agent_id} to feature {feature_id}")
            return fp

    def link_task(self, feature_id: str, task_id: str) -> FeatureProgress:
        """Link a task ID to a feature.

        Duplicate task IDs are silently ignored.

        Args:
            feature_id: The feature identifier.
            task_id:    The task identifier to link.

        Returns:
            The updated FeatureProgress.
        """
        with self._lock:
            records = self._read_all_progress()
            fp = self._get_or_create_progress(feature_id, records)
            if task_id not in fp.tasks:
                fp.tasks.append(task_id)
            fp.updated_at = datetime.now(UTC)
            self._write_all_progress(records)
            logger.debug(f"Linked task {task_id} to feature {feature_id}")
            return fp

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate feature statistics.

        Returns::

            {
                "total": int,
                "by_status": {"planned": int, "in_progress": int, ...},
                "by_domain": {"domain-name": int, ...},
            }
        """
        with self._lock:
            records = self._read_all_progress()
            by_status: dict[str, int] = {s: 0 for s in _VALID_STATUSES}
            by_domain: dict[str, int] = {}

            for fp in records.values():
                by_status[fp.status] = by_status.get(fp.status, 0) + 1
                by_domain[fp.domain] = by_domain.get(fp.domain, 0) + 1

            return {
                "total": len(records),
                "by_status": by_status,
                "by_domain": by_domain,
            }


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


def get_feature_tracker(
    features_dir: Path | str | None = None,
    progress_file: Path | str | None = None,
) -> FeatureTracker:
    """Return the module-level FeatureTracker singleton.

    Args:
        features_dir:  Passed only on the *first* call; ignored thereafter.
        progress_file: Passed only on the *first* call; ignored thereafter.

    Returns:
        The singleton FeatureTracker instance.
    """
    global _tracker_instance

    with _tracker_lock:
        if _tracker_instance is None:
            _tracker_instance = FeatureTracker(
                features_dir=features_dir,
                progress_file=progress_file,
            )
        return _tracker_instance


def reset_feature_tracker() -> None:
    """Destroy the singleton instance.

    Primarily useful in tests to ensure isolation between test cases.
    """
    global _tracker_instance
    with _tracker_lock:
        _tracker_instance = None
