"""Tests for feature_tracker.py - Feature Tracker Service.

Tests cover:
- FeatureProgress model creation, serialisation, and deserialisation
- FeatureTracker initialisation and path defaults
- Private helper methods (_ensure_progress_dir, _read_all_progress,
  _write_all_progress, _get_or_create_progress)
- load_features with and without features.json files, domain/project filters
- get_feature by ID
- update_progress with explicit and auto-derived statuses, validation errors
- assign_agent with status auto-promotion
- link_task with deduplication
- get_stats aggregate statistics
- Singleton helpers get_feature_tracker / reset_feature_tracker
- Thread-safety (basic smoke test)
- Edge cases: empty file, malformed JSONL lines, missing keys in features.json
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import forge_harness.webhook_server.services.feature_tracker as ft_module
from forge_harness.webhook_server.services.feature_tracker import (
    _VALID_STATUSES,
    FeatureProgress,
    FeatureTracker,
    get_feature_tracker,
    reset_feature_tracker,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """Reset the module-level singleton before and after every test."""
    reset_feature_tracker()
    yield
    reset_feature_tracker()


@pytest.fixture
def tmp_features_dir(tmp_path: Path) -> Path:
    """Return a temporary directory to serve as the features root."""
    return tmp_path / "portfolio"


@pytest.fixture
def tmp_progress_file(tmp_path: Path) -> Path:
    """Return a temporary JSONL progress file path (not yet created)."""
    return tmp_path / ".forge" / "features" / "progress.jsonl"


@pytest.fixture
def tracker(tmp_features_dir: Path, tmp_progress_file: Path) -> FeatureTracker:
    """Create a FeatureTracker with isolated tmp directories."""
    tmp_features_dir.mkdir(parents=True, exist_ok=True)
    return FeatureTracker(
        features_dir=tmp_features_dir,
        progress_file=tmp_progress_file,
    )


# =============================================================================
# FeatureProgress Model Tests
# =============================================================================


class TestFeatureProgressModel:
    """Tests for the FeatureProgress Pydantic model."""

    def test_creation_with_defaults(self) -> None:
        """Should create a FeatureProgress with sensible defaults."""
        fp = FeatureProgress(
            feature_id="feat-001",
            name="Login",
            domain="auth-domain",
            project="auth-project",
        )

        assert fp.feature_id == "feat-001"
        assert fp.name == "Login"
        assert fp.domain == "auth-domain"
        assert fp.project == "auth-project"
        assert fp.status == "planned"
        assert fp.completion_pct == 0.0
        assert fp.assigned_agent is None
        assert fp.tasks == []
        assert fp.started_at.tzinfo is not None
        assert fp.updated_at.tzinfo is not None

    def test_creation_with_all_fields(self) -> None:
        """Should create a FeatureProgress with all fields supplied."""
        now = datetime.now(UTC)
        fp = FeatureProgress(
            feature_id="feat-002",
            name="Dashboard",
            domain="ui",
            project="webapp",
            status="in_progress",
            completion_pct=42.0,
            assigned_agent="forge:opencode",
            tasks=["task-a", "task-b"],
            started_at=now,
            updated_at=now,
        )

        assert fp.status == "in_progress"
        assert fp.completion_pct == 42.0
        assert fp.assigned_agent == "forge:opencode"
        assert fp.tasks == ["task-a", "task-b"]

    def test_completion_pct_bounds(self) -> None:
        """completion_pct must stay within [0, 100]."""
        with pytest.raises(Exception):
            FeatureProgress(
                feature_id="f",
                name="n",
                domain="d",
                project="p",
                completion_pct=-1.0,
            )
        with pytest.raises(Exception):
            FeatureProgress(
                feature_id="f",
                name="n",
                domain="d",
                project="p",
                completion_pct=100.1,
            )

    def test_to_dict_round_trip(self) -> None:
        """to_dict should produce a dict that from_dict re-creates correctly."""
        now = datetime.now(UTC)
        original = FeatureProgress(
            feature_id="feat-003",
            name="Search",
            domain="is",
            project="search-ui",
            status="testing",
            completion_pct=75.5,
            assigned_agent="forge:kimi",
            tasks=["t1", "t2"],
            started_at=now,
            updated_at=now,
        )

        d = original.to_dict()
        restored = FeatureProgress.from_dict(d)

        assert restored.feature_id == original.feature_id
        assert restored.name == original.name
        assert restored.domain == original.domain
        assert restored.project == original.project
        assert restored.status == original.status
        assert restored.completion_pct == original.completion_pct
        assert restored.assigned_agent == original.assigned_agent
        assert restored.tasks == original.tasks

    def test_to_dict_contains_iso_strings(self) -> None:
        """Timestamps in to_dict should be ISO strings."""
        fp = FeatureProgress(
            feature_id="f",
            name="n",
            domain="d",
            project="p",
        )
        d = fp.to_dict()

        assert isinstance(d["started_at"], str)
        assert isinstance(d["updated_at"], str)
        # Parseable
        datetime.fromisoformat(d["started_at"])
        datetime.fromisoformat(d["updated_at"])

    def test_from_dict_missing_feature_id_generates_uuid(self) -> None:
        """from_dict should generate a UUID when feature_id is absent."""
        fp = FeatureProgress.from_dict({"name": "No-ID"})
        assert fp.feature_id  # non-empty
        # Validates as UUID
        uuid.UUID(fp.feature_id)

    def test_from_dict_naive_datetime_gets_utc(self) -> None:
        """from_dict should attach UTC tzinfo to naive datetime strings."""
        naive_iso = "2024-01-15T10:30:00"  # no tz info
        fp = FeatureProgress.from_dict(
            {
                "feature_id": "f",
                "name": "n",
                "domain": "d",
                "project": "p",
                "started_at": naive_iso,
                "updated_at": naive_iso,
            }
        )

        assert fp.started_at.tzinfo is not None
        assert fp.updated_at.tzinfo is not None

    def test_from_dict_aware_datetime_preserved(self) -> None:
        """from_dict should preserve timezone on aware datetime strings."""
        aware_iso = "2024-06-01T12:00:00+00:00"
        fp = FeatureProgress.from_dict(
            {
                "feature_id": "f",
                "name": "n",
                "domain": "d",
                "project": "p",
                "started_at": aware_iso,
                "updated_at": aware_iso,
            }
        )

        assert fp.started_at.tzinfo is not None

    def test_from_dict_datetime_object_input(self) -> None:
        """from_dict should handle actual datetime objects (not just strings)."""
        now_naive = datetime(2024, 3, 1, 8, 0, 0)  # no tzinfo
        fp = FeatureProgress.from_dict(
            {
                "feature_id": "f",
                "name": "n",
                "domain": "d",
                "project": "p",
                "started_at": now_naive,
                "updated_at": datetime.now(UTC),
            }
        )
        assert fp.started_at.tzinfo is not None

    def test_from_dict_invalid_datetime_falls_back(self) -> None:
        """from_dict should fall back to now() for non-string, non-datetime values."""
        fp = FeatureProgress.from_dict(
            {
                "feature_id": "f",
                "name": "n",
                "domain": "d",
                "project": "p",
                "started_at": 12345,  # integer — invalid
            }
        )
        # Should still produce a valid datetime
        assert fp.started_at.tzinfo is not None

    def test_from_dict_defaults(self) -> None:
        """from_dict should apply defaults for missing optional fields."""
        fp = FeatureProgress.from_dict({})
        assert fp.status == "planned"
        assert fp.completion_pct == 0.0
        assert fp.assigned_agent is None
        assert fp.tasks == []
        assert fp.domain == "unknown"
        assert fp.project == "unknown"


# =============================================================================
# FeatureTracker Initialisation Tests
# =============================================================================


class TestFeatureTrackerInit:
    """Tests for FeatureTracker.__init__ path defaults."""

    def test_default_features_dir_is_cwd(self) -> None:
        """With no args, features_dir defaults to current directory."""
        tr = FeatureTracker()
        assert tr.features_dir == Path(".")

    def test_default_progress_file(self) -> None:
        """With no args, progress_file defaults to .forge/features/progress.jsonl."""
        tr = FeatureTracker()
        assert tr.progress_file == Path(".forge") / "features" / "progress.jsonl"

    def test_custom_paths_string(self, tmp_path: Path) -> None:
        """Should accept string paths and convert to Path objects."""
        feat_dir = str(tmp_path / "feats")
        prog_file = str(tmp_path / "prog.jsonl")
        tr = FeatureTracker(features_dir=feat_dir, progress_file=prog_file)

        assert tr.features_dir == Path(feat_dir)
        assert tr.progress_file == Path(prog_file)

    def test_custom_paths_path(self, tmp_path: Path) -> None:
        """Should accept Path objects directly."""
        feat_dir = tmp_path / "feats"
        prog_file = tmp_path / "prog.jsonl"
        tr = FeatureTracker(features_dir=feat_dir, progress_file=prog_file)

        assert tr.features_dir == feat_dir
        assert tr.progress_file == prog_file


# =============================================================================
# Private Helper Tests
# =============================================================================


class TestPrivateHelpers:
    """Tests for internal helper methods."""

    def test_ensure_progress_dir_creates_parent(
        self, tracker: FeatureTracker
    ) -> None:
        """_ensure_progress_dir should create the parent directory tree."""
        assert not tracker.progress_file.parent.exists()
        tracker._ensure_progress_dir()
        assert tracker.progress_file.parent.is_dir()

    def test_ensure_progress_dir_idempotent(self, tracker: FeatureTracker) -> None:
        """_ensure_progress_dir called twice should not raise."""
        tracker._ensure_progress_dir()
        tracker._ensure_progress_dir()  # second call — no error
        assert tracker.progress_file.parent.is_dir()

    def test_read_all_progress_no_file(self, tracker: FeatureTracker) -> None:
        """_read_all_progress returns empty dict when file does not exist."""
        result = tracker._read_all_progress()
        assert result == {}

    def test_read_all_progress_empty_file(self, tracker: FeatureTracker) -> None:
        """_read_all_progress returns empty dict for an empty file."""
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text("")
        result = tracker._read_all_progress()
        assert result == {}

    def test_read_all_progress_skips_blank_lines(
        self, tracker: FeatureTracker
    ) -> None:
        """_read_all_progress should skip blank lines gracefully."""
        tracker._ensure_progress_dir()
        fp = FeatureProgress(
            feature_id="feat-x",
            name="X",
            domain="d",
            project="p",
        )
        tracker.progress_file.write_text(
            "\n" + json.dumps(fp.to_dict()) + "\n\n"
        )
        result = tracker._read_all_progress()
        assert "feat-x" in result

    def test_read_all_progress_skips_malformed_lines(
        self, tracker: FeatureTracker
    ) -> None:
        """_read_all_progress logs a warning and skips malformed JSON."""
        tracker._ensure_progress_dir()
        fp = FeatureProgress(
            feature_id="feat-good",
            name="Good",
            domain="d",
            project="p",
        )
        tracker.progress_file.write_text(
            "NOT JSON\n" + json.dumps(fp.to_dict()) + "\n"
        )
        result = tracker._read_all_progress()
        # The good record should still be loaded
        assert "feat-good" in result

    def test_write_all_progress_round_trip(self, tracker: FeatureTracker) -> None:
        """_write_all_progress + _read_all_progress should produce identical data."""
        records = {
            "feat-a": FeatureProgress(
                feature_id="feat-a",
                name="A",
                domain="dom",
                project="proj",
                status="in_progress",
                completion_pct=30.0,
            ),
            "feat-b": FeatureProgress(
                feature_id="feat-b",
                name="B",
                domain="dom",
                project="proj",
            ),
        }
        tracker._write_all_progress(records)
        loaded = tracker._read_all_progress()

        assert set(loaded.keys()) == {"feat-a", "feat-b"}
        assert loaded["feat-a"].status == "in_progress"
        assert loaded["feat-a"].completion_pct == 30.0

    def test_write_all_progress_empty_dict(self, tracker: FeatureTracker) -> None:
        """Writing an empty dict should produce an empty file (not crash)."""
        tracker._write_all_progress({})
        assert tracker.progress_file.exists()
        content = tracker.progress_file.read_text()
        # File may be empty or contain only a trailing newline
        assert content.strip() == ""

    def test_get_or_create_returns_existing(self, tracker: FeatureTracker) -> None:
        """_get_or_create_progress returns existing record unchanged."""
        fp = FeatureProgress(
            feature_id="feat-e",
            name="Existing",
            domain="d",
            project="p",
            status="testing",
        )
        records: dict[str, FeatureProgress] = {"feat-e": fp}
        result = tracker._get_or_create_progress("feat-e", records)

        assert result is fp
        assert result.status == "testing"

    def test_get_or_create_creates_new(self, tracker: FeatureTracker) -> None:
        """_get_or_create_progress inserts a new record when absent."""
        records: dict[str, FeatureProgress] = {}
        result = tracker._get_or_create_progress(
            "feat-new",
            records,
            name="New",
            domain="dom",
            project="proj",
        )

        assert result.feature_id == "feat-new"
        assert result.name == "New"
        assert result.domain == "dom"
        assert result.project == "proj"
        assert "feat-new" in records


# =============================================================================
# load_features Tests
# =============================================================================


class TestLoadFeatures:
    """Tests for FeatureTracker.load_features."""

    def test_load_features_empty(self, tracker: FeatureTracker) -> None:
        """Returns empty list when no progress and no features.json files."""
        result = tracker.load_features()
        assert result == []

    def test_load_features_from_progress_file(
        self, tracker: FeatureTracker
    ) -> None:
        """Features stored in progress.jsonl are returned."""
        tracker.update_progress("feat-1", 50.0, domain="dom", project="proj")
        result = tracker.load_features()
        assert len(result) == 1
        assert result[0].feature_id == "feat-1"

    def test_load_features_sorted_by_feature_id(
        self, tracker: FeatureTracker
    ) -> None:
        """Results must be sorted by feature_id ascending."""
        tracker.update_progress("zzz", 0.0)
        tracker.update_progress("aaa", 0.0)
        tracker.update_progress("mmm", 0.0)

        result = tracker.load_features()
        ids = [r.feature_id for r in result]
        assert ids == sorted(ids)

    def test_load_features_domain_filter(self, tracker: FeatureTracker) -> None:
        """domain filter restricts results."""
        tracker.update_progress(
            "feat-x", 0.0, domain="alpha", project="p"
        )
        tracker.update_progress(
            "feat-y", 0.0, domain="beta", project="p"
        )
        result = tracker.load_features(domain="alpha")
        assert len(result) == 1
        assert result[0].feature_id == "feat-x"

    def test_load_features_project_filter(self, tracker: FeatureTracker) -> None:
        """project filter restricts results."""
        tracker.update_progress(
            "feat-a", 0.0, domain="dom", project="proj-1"
        )
        tracker.update_progress(
            "feat-b", 0.0, domain="dom", project="proj-2"
        )
        result = tracker.load_features(project="proj-2")
        assert len(result) == 1
        assert result[0].feature_id == "feat-b"

    def test_load_features_combined_filter(self, tracker: FeatureTracker) -> None:
        """Both domain and project filters can be applied simultaneously."""
        tracker.update_progress("f1", 0.0, domain="d1", project="p1")
        tracker.update_progress("f2", 0.0, domain="d1", project="p2")
        tracker.update_progress("f3", 0.0, domain="d2", project="p1")

        result = tracker.load_features(domain="d1", project="p1")
        assert len(result) == 1
        assert result[0].feature_id == "f1"

    def test_load_features_from_features_json_list(
        self, tmp_features_dir: Path, tmp_progress_file: Path
    ) -> None:
        """Features defined in a features.json (list format) are discovered."""
        domain_dir = tmp_features_dir / "my-domain" / "my-project"
        domain_dir.mkdir(parents=True)
        features_data = [
            {"id": "fid-001", "name": "Feature One"},
            {"id": "fid-002", "name": "Feature Two"},
        ]
        (domain_dir / "features.json").write_text(json.dumps(features_data))

        tr = FeatureTracker(
            features_dir=tmp_features_dir,
            progress_file=tmp_progress_file,
        )
        result = tr.load_features()

        ids = {r.feature_id for r in result}
        assert "fid-001" in ids
        assert "fid-002" in ids

    def test_load_features_from_features_json_dict_format(
        self, tmp_features_dir: Path, tmp_progress_file: Path
    ) -> None:
        """Features defined in a features.json (dict with 'features' key) are discovered."""
        domain_dir = tmp_features_dir / "dom" / "proj"
        domain_dir.mkdir(parents=True)
        features_data = {
            "features": [
                {"feature_id": "dict-001", "title": "Dict Feature"},
            ]
        }
        (domain_dir / "features.json").write_text(json.dumps(features_data))

        tr = FeatureTracker(
            features_dir=tmp_features_dir,
            progress_file=tmp_progress_file,
        )
        result = tr.load_features()

        ids = {r.feature_id for r in result}
        assert "dict-001" in ids

    def test_load_features_json_skips_items_without_id(
        self, tmp_features_dir: Path, tmp_progress_file: Path
    ) -> None:
        """Items without an id/feature_id in features.json are skipped."""
        domain_dir = tmp_features_dir / "dom" / "proj"
        domain_dir.mkdir(parents=True)
        features_data = [
            {"name": "No ID here"},
            {"id": "valid-id", "name": "Valid"},
        ]
        (domain_dir / "features.json").write_text(json.dumps(features_data))

        tr = FeatureTracker(
            features_dir=tmp_features_dir,
            progress_file=tmp_progress_file,
        )
        result = tr.load_features()

        ids = {r.feature_id for r in result}
        assert "valid-id" in ids
        # The nameless entry is skipped — only one result
        assert len(result) == 1

    def test_load_features_merges_definition_into_progress(
        self, tmp_features_dir: Path, tmp_progress_file: Path
    ) -> None:
        """A pre-existing progress record gets name/domain/project updated from features.json."""
        domain_dir = tmp_features_dir / "new-dom" / "new-proj"
        domain_dir.mkdir(parents=True)

        # Write progress with empty name and unknown domain/project
        tr = FeatureTracker(
            features_dir=tmp_features_dir,
            progress_file=tmp_progress_file,
        )
        tr.update_progress("merge-id", 10.0)  # domain/project = "unknown"

        # Now add a features.json that provides the name and location
        features_data = [{"id": "merge-id", "name": "Merged Name"}]
        (domain_dir / "features.json").write_text(json.dumps(features_data))

        result = tr.load_features()
        merged = next(r for r in result if r.feature_id == "merge-id")

        assert merged.name == "Merged Name"
        assert merged.domain == "new-dom"
        assert merged.project == "new-proj"

    def test_load_features_bad_features_json_is_skipped(
        self, tmp_features_dir: Path, tmp_progress_file: Path
    ) -> None:
        """Corrupt features.json files produce a warning log but no exception."""
        domain_dir = tmp_features_dir / "bad-dom"
        domain_dir.mkdir(parents=True)
        (domain_dir / "features.json").write_text("NOT JSON")

        tr = FeatureTracker(
            features_dir=tmp_features_dir,
            progress_file=tmp_progress_file,
        )
        # Should not raise
        result = tr.load_features()
        assert isinstance(result, list)

    def test_load_features_infers_domain_project_from_path(
        self, tmp_features_dir: Path, tmp_progress_file: Path
    ) -> None:
        """Domain/project are inferred from the path parts of features.json."""
        domain_dir = tmp_features_dir / "inferred-domain" / "inferred-project"
        domain_dir.mkdir(parents=True)
        features_data = [{"id": "path-feat", "name": "Path Feature"}]
        (domain_dir / "features.json").write_text(json.dumps(features_data))

        tr = FeatureTracker(
            features_dir=tmp_features_dir,
            progress_file=tmp_progress_file,
        )
        result = tr.load_features()

        feat = next(r for r in result if r.feature_id == "path-feat")
        assert feat.domain == "inferred-domain"
        assert feat.project == "inferred-project"

    def test_load_features_explicit_domain_in_item(
        self, tmp_features_dir: Path, tmp_progress_file: Path
    ) -> None:
        """Explicit domain/project fields in features.json take precedence over path."""
        domain_dir = tmp_features_dir / "path-dom" / "path-proj"
        domain_dir.mkdir(parents=True)
        features_data = [
            {
                "id": "explicit-feat",
                "name": "Explicit",
                "domain": "override-dom",
                "project": "override-proj",
            }
        ]
        (domain_dir / "features.json").write_text(json.dumps(features_data))

        tr = FeatureTracker(
            features_dir=tmp_features_dir,
            progress_file=tmp_progress_file,
        )
        result = tr.load_features()

        feat = next(r for r in result if r.feature_id == "explicit-feat")
        assert feat.domain == "override-dom"
        assert feat.project == "override-proj"


# =============================================================================
# get_feature Tests
# =============================================================================


class TestGetFeature:
    """Tests for FeatureTracker.get_feature."""

    def test_get_feature_returns_none_when_missing(
        self, tracker: FeatureTracker
    ) -> None:
        """get_feature returns None for an unknown feature_id."""
        result = tracker.get_feature("nonexistent")
        assert result is None

    def test_get_feature_returns_existing(self, tracker: FeatureTracker) -> None:
        """get_feature returns the FeatureProgress for a known ID."""
        tracker.update_progress("feat-known", 60.0)
        result = tracker.get_feature("feat-known")

        assert result is not None
        assert result.feature_id == "feat-known"
        assert result.completion_pct == 60.0

    def test_get_feature_does_not_modify_store(
        self, tracker: FeatureTracker
    ) -> None:
        """Calling get_feature should not create a new record in the store."""
        tracker.get_feature("ghost-feat")
        all_features = tracker.load_features()
        assert all_features == []


# =============================================================================
# update_progress Tests
# =============================================================================


class TestUpdateProgress:
    """Tests for FeatureTracker.update_progress."""

    def test_update_creates_new_record(self, tracker: FeatureTracker) -> None:
        """update_progress creates a record when none exists."""
        fp = tracker.update_progress(
            "new-feat",
            50.0,
            name="New Feature",
            domain="dom",
            project="proj",
        )

        assert fp.feature_id == "new-feat"
        assert fp.completion_pct == 50.0
        assert fp.name == "New Feature"

    def test_update_persists_to_disk(self, tracker: FeatureTracker) -> None:
        """update_progress writes to the JSONL file."""
        tracker.update_progress("persist-feat", 25.0)
        loaded = tracker._read_all_progress()

        assert "persist-feat" in loaded
        assert loaded["persist-feat"].completion_pct == 25.0

    def test_update_overwrites_existing(self, tracker: FeatureTracker) -> None:
        """Subsequent update_progress calls overwrite the previous value."""
        tracker.update_progress("feat-up", 10.0)
        tracker.update_progress("feat-up", 80.0)

        result = tracker.get_feature("feat-up")
        assert result is not None
        assert result.completion_pct == 80.0

    # Status auto-derivation ------------------------------------------------

    def test_status_auto_zero_pct_is_planned(
        self, tracker: FeatureTracker
    ) -> None:
        """0% completion auto-derives to 'planned'."""
        fp = tracker.update_progress("f", 0.0)
        assert fp.status == "planned"

    def test_status_auto_full_pct_is_done(
        self, tracker: FeatureTracker
    ) -> None:
        """100% completion auto-derives to 'done'."""
        fp = tracker.update_progress("f", 100.0)
        assert fp.status == "done"

    def test_status_auto_partial_pct_is_in_progress(
        self, tracker: FeatureTracker
    ) -> None:
        """Partial completion auto-derives to 'in_progress'."""
        for pct in (1.0, 50.0, 99.9):
            fp = tracker.update_progress("f", pct)
            assert fp.status == "in_progress", f"Expected in_progress for {pct}%"

    def test_explicit_status_overrides_auto(
        self, tracker: FeatureTracker
    ) -> None:
        """Explicit status argument takes precedence over auto-derivation."""
        fp = tracker.update_progress("f", 50.0, status="testing")
        assert fp.status == "testing"

    def test_all_valid_explicit_statuses(self, tracker: FeatureTracker) -> None:
        """All four valid status values should be accepted."""
        for status in ("planned", "in_progress", "testing", "done"):
            fp = tracker.update_progress("f", 0.0, status=status)
            assert fp.status == status

    # Validation errors -----------------------------------------------------

    def test_raises_for_negative_pct(self, tracker: FeatureTracker) -> None:
        """completion_pct < 0 raises ValueError."""
        with pytest.raises(ValueError, match="completion_pct must be in"):
            tracker.update_progress("f", -1.0)

    def test_raises_for_pct_over_100(self, tracker: FeatureTracker) -> None:
        """completion_pct > 100 raises ValueError."""
        with pytest.raises(ValueError, match="completion_pct must be in"):
            tracker.update_progress("f", 100.1)

    def test_raises_for_invalid_status(self, tracker: FeatureTracker) -> None:
        """An unknown status string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid status"):
            tracker.update_progress("f", 50.0, status="unknown-status")

    def test_updated_at_is_refreshed(self, tracker: FeatureTracker) -> None:
        """updated_at should change on each update_progress call."""
        fp1 = tracker.update_progress("f", 10.0)
        t1 = fp1.updated_at
        fp2 = tracker.update_progress("f", 20.0)
        t2 = fp2.updated_at
        # updated_at should be >= t1
        assert t2 >= t1


# =============================================================================
# assign_agent Tests
# =============================================================================


class TestAssignAgent:
    """Tests for FeatureTracker.assign_agent."""

    def test_assign_creates_record_if_absent(
        self, tracker: FeatureTracker
    ) -> None:
        """assign_agent creates a progress record when one does not exist."""
        fp = tracker.assign_agent("brand-new", "forge:agent-1")

        assert fp.feature_id == "brand-new"
        assert fp.assigned_agent == "forge:agent-1"

    def test_assign_sets_agent_id(self, tracker: FeatureTracker) -> None:
        """assign_agent sets the assigned_agent field correctly."""
        tracker.update_progress("feat-a", 0.0)
        fp = tracker.assign_agent("feat-a", "forge:opencode")

        assert fp.assigned_agent == "forge:opencode"

    def test_assign_promotes_planned_to_in_progress(
        self, tracker: FeatureTracker
    ) -> None:
        """Assigning an agent to a 'planned' feature auto-promotes it to 'in_progress'."""
        tracker.update_progress("feat-b", 0.0)  # status = planned
        fp = tracker.assign_agent("feat-b", "forge:kimi")

        assert fp.status == "in_progress"

    def test_assign_does_not_change_non_planned_status(
        self, tracker: FeatureTracker
    ) -> None:
        """Assigning to a non-planned feature leaves the status unchanged."""
        tracker.update_progress("feat-c", 75.0, status="testing")
        fp = tracker.assign_agent("feat-c", "forge:agent-x")

        assert fp.status == "testing"

    def test_assign_persists_to_disk(self, tracker: FeatureTracker) -> None:
        """assign_agent writes the record to disk."""
        tracker.assign_agent("persist-a", "forge:lead")
        loaded = tracker._read_all_progress()

        assert "persist-a" in loaded
        assert loaded["persist-a"].assigned_agent == "forge:lead"

    def test_assign_updates_updated_at(self, tracker: FeatureTracker) -> None:
        """assign_agent refreshes updated_at."""
        tracker.update_progress("feat-d", 0.0)
        fp_old = tracker.get_feature("feat-d")
        assert fp_old is not None

        fp_new = tracker.assign_agent("feat-d", "forge:new-agent")
        assert fp_new.updated_at >= fp_old.updated_at

    def test_reassign_agent(self, tracker: FeatureTracker) -> None:
        """A feature can be re-assigned to a different agent."""
        tracker.assign_agent("feat-e", "forge:agent-1")
        fp = tracker.assign_agent("feat-e", "forge:agent-2")

        assert fp.assigned_agent == "forge:agent-2"


# =============================================================================
# link_task Tests
# =============================================================================


class TestLinkTask:
    """Tests for FeatureTracker.link_task."""

    def test_link_task_creates_record_if_absent(
        self, tracker: FeatureTracker
    ) -> None:
        """link_task creates a progress record when none exists."""
        fp = tracker.link_task("brand-new-feat", "task-001")

        assert fp.feature_id == "brand-new-feat"
        assert "task-001" in fp.tasks

    def test_link_task_appends_task(self, tracker: FeatureTracker) -> None:
        """link_task appends the task_id to the tasks list."""
        tracker.update_progress("feat-t", 0.0)
        fp = tracker.link_task("feat-t", "task-abc")

        assert "task-abc" in fp.tasks

    def test_link_multiple_tasks(self, tracker: FeatureTracker) -> None:
        """Multiple distinct task IDs can be linked."""
        tracker.update_progress("feat-multi", 0.0)
        tracker.link_task("feat-multi", "t1")
        tracker.link_task("feat-multi", "t2")
        fp = tracker.link_task("feat-multi", "t3")

        assert fp.tasks == ["t1", "t2", "t3"]

    def test_link_task_deduplication(self, tracker: FeatureTracker) -> None:
        """Linking the same task_id twice is a no-op (no duplicates)."""
        tracker.update_progress("feat-dedup", 0.0)
        tracker.link_task("feat-dedup", "dup-task")
        fp = tracker.link_task("feat-dedup", "dup-task")

        assert fp.tasks.count("dup-task") == 1

    def test_link_task_persists(self, tracker: FeatureTracker) -> None:
        """link_task writes the updated tasks list to disk."""
        tracker.link_task("feat-disk", "task-disk")
        loaded = tracker._read_all_progress()

        assert "feat-disk" in loaded
        assert "task-disk" in loaded["feat-disk"].tasks

    def test_link_task_updates_updated_at(self, tracker: FeatureTracker) -> None:
        """link_task refreshes the updated_at timestamp."""
        tracker.update_progress("feat-ts", 0.0)
        fp_old = tracker.get_feature("feat-ts")
        assert fp_old is not None

        fp_new = tracker.link_task("feat-ts", "new-task")
        assert fp_new.updated_at >= fp_old.updated_at


# =============================================================================
# get_stats Tests
# =============================================================================


class TestGetStats:
    """Tests for FeatureTracker.get_stats."""

    def test_get_stats_empty(self, tracker: FeatureTracker) -> None:
        """Returns zeroed stats when no records exist."""
        stats = tracker.get_stats()

        assert stats["total"] == 0
        assert stats["by_domain"] == {}
        # by_status should contain all valid statuses initialised to 0
        for status in _VALID_STATUSES:
            assert stats["by_status"][status] == 0

    def test_get_stats_total_count(self, tracker: FeatureTracker) -> None:
        """total reflects the correct number of persisted features."""
        tracker.update_progress("f1", 0.0)
        tracker.update_progress("f2", 0.0)
        tracker.update_progress("f3", 0.0)

        stats = tracker.get_stats()
        assert stats["total"] == 3

    def test_get_stats_by_status(self, tracker: FeatureTracker) -> None:
        """by_status counts features per status correctly."""
        tracker.update_progress("f1", 0.0, status="planned")
        tracker.update_progress("f2", 50.0, status="in_progress")
        tracker.update_progress("f3", 50.0, status="in_progress")
        tracker.update_progress("f4", 100.0, status="done")

        stats = tracker.get_stats()
        assert stats["by_status"]["planned"] == 1
        assert stats["by_status"]["in_progress"] == 2
        assert stats["by_status"]["done"] == 1
        assert stats["by_status"]["testing"] == 0

    def test_get_stats_by_domain(self, tracker: FeatureTracker) -> None:
        """by_domain counts features per domain correctly."""
        tracker.update_progress("f1", 0.0, domain="alpha")
        tracker.update_progress("f2", 0.0, domain="alpha")
        tracker.update_progress("f3", 0.0, domain="beta")

        stats = tracker.get_stats()
        assert stats["by_domain"]["alpha"] == 2
        assert stats["by_domain"]["beta"] == 1

    def test_get_stats_unknown_status_counted(
        self, tracker: FeatureTracker
    ) -> None:
        """get_stats gracefully handles a status not in _VALID_STATUSES (stored in JSONL)."""
        # Directly write a JSONL record with a non-standard status
        tracker._ensure_progress_dir()
        bad_record = FeatureProgress(
            feature_id="f-bad",
            name="Bad",
            domain="d",
            project="p",
        )
        bad_dict = bad_record.to_dict()
        bad_dict["status"] = "custom-status"
        tracker.progress_file.write_text(json.dumps(bad_dict) + "\n")

        stats = tracker.get_stats()
        # Total should still include the bad record
        assert stats["total"] == 1
        # Unknown status lands in by_status under its key
        assert "custom-status" in stats["by_status"]


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    """Tests for get_feature_tracker and reset_feature_tracker."""

    def test_get_feature_tracker_returns_singleton(self) -> None:
        """get_feature_tracker returns the same instance on repeated calls."""
        t1 = get_feature_tracker()
        t2 = get_feature_tracker()
        assert t1 is t2

    def test_get_feature_tracker_creates_instance(self) -> None:
        """get_feature_tracker creates a FeatureTracker instance."""
        tracker = get_feature_tracker()
        assert isinstance(tracker, FeatureTracker)

    def test_reset_clears_singleton(self) -> None:
        """reset_feature_tracker allows a fresh instance on the next call."""
        t1 = get_feature_tracker()
        reset_feature_tracker()
        t2 = get_feature_tracker()
        assert t1 is not t2

    def test_get_feature_tracker_custom_paths_only_first_call(
        self, tmp_path: Path
    ) -> None:
        """Custom paths are only used on the first call; subsequent calls ignore them."""
        prog_file_1 = tmp_path / "first.jsonl"
        prog_file_2 = tmp_path / "second.jsonl"

        t1 = get_feature_tracker(progress_file=prog_file_1)
        t2 = get_feature_tracker(progress_file=prog_file_2)

        # Both should be the same singleton created with prog_file_1
        assert t1 is t2
        assert t1.progress_file == prog_file_1

    def test_reset_then_custom_paths(self, tmp_path: Path) -> None:
        """After reset, a new call can supply different custom paths."""
        prog_file_1 = tmp_path / "first.jsonl"
        prog_file_2 = tmp_path / "second.jsonl"

        get_feature_tracker(progress_file=prog_file_1)
        reset_feature_tracker()
        t2 = get_feature_tracker(progress_file=prog_file_2)

        assert t2.progress_file == prog_file_2

    def test_reset_sets_none(self) -> None:
        """reset_feature_tracker sets the module-level variable to None."""
        get_feature_tracker()
        reset_feature_tracker()
        assert ft_module._tracker_instance is None


# =============================================================================
# Thread-Safety Smoke Test
# =============================================================================


class TestThreadSafety:
    """Basic concurrency smoke tests."""

    def test_concurrent_update_progress(self, tracker: FeatureTracker) -> None:
        """Many threads calling update_progress on the same feature should not raise."""
        errors: list[Exception] = []

        def do_update(pct: float) -> None:
            try:
                tracker.update_progress("shared-feat", pct, domain="d", project="p")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=do_update, args=(float(i),))
            for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent errors: {errors}"

    def test_concurrent_link_task(self, tracker: FeatureTracker) -> None:
        """Many threads linking unique tasks to the same feature should not raise."""
        tracker.update_progress("thread-feat", 0.0)
        errors: list[Exception] = []

        def do_link(idx: int) -> None:
            try:
                tracker.link_task("thread-feat", f"task-{idx}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=do_link, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent link_task errors: {errors}"
        fp = tracker.get_feature("thread-feat")
        assert fp is not None
        assert len(fp.tasks) == 20

    def test_singleton_thread_safe(self) -> None:
        """Concurrent calls to get_feature_tracker always return the same instance."""
        results: list[FeatureTracker] = []

        def get_tracker() -> None:
            results.append(get_feature_tracker())

        threads = [threading.Thread(target=get_tracker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All results should be the exact same object
        assert all(r is results[0] for r in results)


# =============================================================================
# Persistence Edge Cases
# =============================================================================


class TestPersistenceEdgeCases:
    """Edge cases around file I/O and persistence."""

    def test_write_progress_os_error_logs_and_does_not_raise(
        self, tracker: FeatureTracker
    ) -> None:
        """_write_all_progress logs an error on OSError but does not propagate."""
        records = {
            "f": FeatureProgress(
                feature_id="f", name="n", domain="d", project="p"
            )
        }
        # Patch Path.write_text on the class level, guarded by the specific path
        original_write_text = Path.write_text

        def raise_on_progress_file(self: Path, *args: object, **kwargs: object) -> None:
            if self == tracker.progress_file:
                raise OSError("disk full")
            return original_write_text(self, *args, **kwargs)  # type: ignore[return-value]

        with patch.object(Path, "write_text", raise_on_progress_file):
            # Should not raise
            tracker._write_all_progress(records)

    def test_multiple_writes_replace_not_append(
        self, tracker: FeatureTracker
    ) -> None:
        """Each _write_all_progress replaces the file rather than appending."""
        records_1 = {
            "f1": FeatureProgress(
                feature_id="f1", name="F1", domain="d", project="p"
            )
        }
        records_2 = {
            "f2": FeatureProgress(
                feature_id="f2", name="F2", domain="d", project="p"
            )
        }

        tracker._write_all_progress(records_1)
        tracker._write_all_progress(records_2)

        loaded = tracker._read_all_progress()
        # Only records_2 should be present
        assert "f1" not in loaded
        assert "f2" in loaded

    def test_load_features_handles_no_features_json(
        self, tracker: FeatureTracker
    ) -> None:
        """load_features works correctly when no features.json files exist."""
        tracker.update_progress("from-progress", 20.0)
        result = tracker.load_features()

        assert len(result) == 1
        assert result[0].feature_id == "from-progress"

    def test_progress_file_has_trailing_newline(
        self, tracker: FeatureTracker
    ) -> None:
        """The written JSONL file should end with a newline when records exist."""
        tracker.update_progress("nl-feat", 0.0)
        content = tracker.progress_file.read_text()
        assert content.endswith("\n")
