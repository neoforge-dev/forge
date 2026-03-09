"""Comprehensive unit tests for forge_harness.webhook_server.services.feature_tracker.

All external I/O (filesystem) is mocked so no real files are touched.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.services.feature_tracker import (
    _VALID_STATUSES,
    FeatureProgress,
    FeatureTracker,
    get_feature_tracker,
    reset_feature_tracker,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _make_progress(**kwargs: Any) -> FeatureProgress:
    """Return a FeatureProgress with sensible defaults."""
    defaults: dict[str, Any] = {
        "feature_id": "feat-001",
        "name": "Test Feature",
        "domain": "codeswiftr-com",
        "project": "interview-sim",
    }
    defaults.update(kwargs)
    return FeatureProgress(**defaults)


def _tracker(tmp_path: Path) -> FeatureTracker:
    """Return a FeatureTracker wired to *tmp_path* so we never touch real FS."""
    progress_file = tmp_path / ".forge" / "features" / "progress.jsonl"
    return FeatureTracker(
        features_dir=tmp_path,
        progress_file=progress_file,
    )


# ===========================================================================
# FeatureProgress model
# ===========================================================================


class TestFeatureProgressModel:
    """Tests for the FeatureProgress Pydantic model."""

    def test_defaults(self) -> None:
        fp = _make_progress()
        assert fp.status == "planned"
        assert fp.completion_pct == 0.0
        assert fp.assigned_agent is None
        assert fp.tasks == []
        assert fp.started_at.tzinfo is not None
        assert fp.updated_at.tzinfo is not None

    def test_to_dict_keys(self) -> None:
        fp = _make_progress()
        d = fp.to_dict()
        expected_keys = {
            "feature_id",
            "name",
            "domain",
            "project",
            "status",
            "completion_pct",
            "assigned_agent",
            "tasks",
            "started_at",
            "updated_at",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values(self) -> None:
        fp = _make_progress(feature_id="feat-X", name="X", domain="dom", project="proj")
        d = fp.to_dict()
        assert d["feature_id"] == "feat-X"
        assert d["name"] == "X"
        assert d["domain"] == "dom"
        assert d["project"] == "proj"
        assert d["status"] == "planned"
        assert d["completion_pct"] == 0.0
        assert d["assigned_agent"] is None
        assert d["tasks"] == []
        assert isinstance(d["started_at"], str)
        assert isinstance(d["updated_at"], str)

    def test_to_dict_tasks_copied(self) -> None:
        fp = _make_progress(tasks=["t1", "t2"])
        d = fp.to_dict()
        d["tasks"].append("t3")
        assert "t3" not in fp.tasks

    def test_from_dict_roundtrip(self) -> None:
        fp = _make_progress(
            feature_id="feat-rt",
            name="Roundtrip",
            domain="d",
            project="p",
            status="in_progress",
            completion_pct=42.0,
            assigned_agent="forge:claude",
            tasks=["t1"],
        )
        restored = FeatureProgress.from_dict(fp.to_dict())
        assert restored.feature_id == fp.feature_id
        assert restored.name == fp.name
        assert restored.domain == fp.domain
        assert restored.project == fp.project
        assert restored.status == fp.status
        assert restored.completion_pct == fp.completion_pct
        assert restored.assigned_agent == fp.assigned_agent
        assert restored.tasks == fp.tasks

    def test_from_dict_missing_feature_id_generates_uuid(self) -> None:
        data: dict[str, Any] = {"name": "No ID"}
        fp = FeatureProgress.from_dict(data)
        # Should be a valid UUID-ish string
        assert fp.feature_id
        uuid.UUID(fp.feature_id)  # raises if not UUID

    def test_from_dict_naive_datetime_gets_utc(self) -> None:
        naive_iso = "2024-01-15T10:00:00"
        data = {
            "feature_id": "f1",
            "name": "N",
            "domain": "d",
            "project": "p",
            "started_at": naive_iso,
            "updated_at": naive_iso,
        }
        fp = FeatureProgress.from_dict(data)
        assert fp.started_at.tzinfo == UTC
        assert fp.updated_at.tzinfo == UTC

    def test_from_dict_aware_datetime_preserved(self) -> None:
        aware_iso = "2024-01-15T10:00:00+00:00"
        data = {
            "feature_id": "f1",
            "name": "N",
            "domain": "d",
            "project": "p",
            "started_at": aware_iso,
            "updated_at": aware_iso,
        }
        fp = FeatureProgress.from_dict(data)
        assert fp.started_at.tzinfo is not None

    def test_from_dict_missing_datetime_falls_back_to_now(self) -> None:
        data: dict[str, Any] = {"feature_id": "f2", "name": "N", "domain": "d", "project": "p"}
        before = datetime.now(UTC)
        fp = FeatureProgress.from_dict(data)
        after = datetime.now(UTC)
        assert before <= fp.started_at <= after

    def test_from_dict_invalid_datetime_value_falls_back_to_now(self) -> None:
        data = {
            "feature_id": "f3",
            "name": "N",
            "domain": "d",
            "project": "p",
            "started_at": 99999,  # not str or datetime
            "updated_at": None,
        }
        before = datetime.now(UTC)
        fp = FeatureProgress.from_dict(data)
        after = datetime.now(UTC)
        assert before <= fp.started_at <= after

    def test_from_dict_with_datetime_object_aware(self) -> None:
        now = datetime.now(UTC)
        data = {
            "feature_id": "f4",
            "name": "N",
            "domain": "d",
            "project": "p",
            "started_at": now,
            "updated_at": now,
        }
        fp = FeatureProgress.from_dict(data)
        assert fp.started_at.tzinfo is not None

    def test_from_dict_with_datetime_object_naive(self) -> None:
        naive_dt = datetime(2024, 1, 1, 12, 0, 0)
        data = {
            "feature_id": "f5",
            "name": "N",
            "domain": "d",
            "project": "p",
            "started_at": naive_dt,
            "updated_at": naive_dt,
        }
        fp = FeatureProgress.from_dict(data)
        assert fp.started_at.tzinfo == UTC

    def test_completion_pct_validation_bounds(self) -> None:
        fp = _make_progress(completion_pct=0.0)
        assert fp.completion_pct == 0.0
        fp2 = _make_progress(completion_pct=100.0)
        assert fp2.completion_pct == 100.0

    def test_completion_pct_out_of_bounds_raises(self) -> None:
        with pytest.raises(Exception):
            FeatureProgress(
                feature_id="x",
                name="X",
                domain="d",
                project="p",
                completion_pct=-1.0,
            )
        with pytest.raises(Exception):
            FeatureProgress(
                feature_id="x",
                name="X",
                domain="d",
                project="p",
                completion_pct=101.0,
            )

    def test_from_dict_defaults_domain_project(self) -> None:
        fp = FeatureProgress.from_dict({"feature_id": "f99", "name": "N"})
        assert fp.domain == "unknown"
        assert fp.project == "unknown"


# ===========================================================================
# FeatureTracker internals
# ===========================================================================


class TestFeatureTrackerInit:
    def test_default_paths(self) -> None:
        tracker = FeatureTracker()
        assert tracker.features_dir == Path(".")
        assert tracker.progress_file == Path(".forge") / "features" / "progress.jsonl"

    def test_custom_paths(self, tmp_path: Path) -> None:
        pf = tmp_path / "custom_progress.jsonl"
        tracker = FeatureTracker(features_dir=tmp_path, progress_file=pf)
        assert tracker.features_dir == tmp_path
        assert tracker.progress_file == pf

    def test_string_paths_converted(self, tmp_path: Path) -> None:
        tracker = FeatureTracker(
            features_dir=str(tmp_path),
            progress_file=str(tmp_path / "p.jsonl"),
        )
        assert isinstance(tracker.features_dir, Path)
        assert isinstance(tracker.progress_file, Path)


class TestReadAllProgress:
    def test_returns_empty_when_file_absent(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        records = tracker._read_all_progress()
        assert records == {}

    def test_reads_valid_records(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = _make_progress(feature_id="feat-read")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(json.dumps(fp.to_dict()) + "\n")
        records = tracker._read_all_progress()
        assert "feat-read" in records
        assert records["feat-read"].name == "Test Feature"

    def test_skips_empty_lines(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = _make_progress(feature_id="feat-empty-line")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(
            "\n" + json.dumps(fp.to_dict()) + "\n\n"
        )
        records = tracker._read_all_progress()
        assert len(records) == 1

    def test_skips_malformed_json_lines(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = _make_progress(feature_id="feat-good")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(
            "NOT JSON\n" + json.dumps(fp.to_dict()) + "\n"
        )
        records = tracker._read_all_progress()
        # malformed line is skipped, valid line is read
        assert "feat-good" in records

    def test_later_record_overwrites_earlier_for_same_id(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp1 = _make_progress(feature_id="feat-dup", name="First")
        fp2 = _make_progress(feature_id="feat-dup", name="Second")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(
            json.dumps(fp1.to_dict()) + "\n" + json.dumps(fp2.to_dict()) + "\n"
        )
        records = tracker._read_all_progress()
        assert records["feat-dup"].name == "Second"


class TestWriteAllProgress:
    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        assert not tracker.progress_file.parent.exists()
        fp = _make_progress()
        tracker._write_all_progress({"feat-001": fp})
        assert tracker.progress_file.parent.exists()

    def test_writes_jsonl_content(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = _make_progress(feature_id="feat-w")
        tracker._write_all_progress({"feat-w": fp})
        lines = tracker.progress_file.read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["feature_id"] == "feat-w"

    def test_writes_empty_records_as_empty_file(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker._write_all_progress({})
        assert tracker.progress_file.read_text() == ""

    def test_write_oserror_logged(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker._ensure_progress_dir()
        fp = _make_progress()
        # Patch pathlib.Path.write_text at the class level so the instance is covered
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            # Should not raise — only log
            tracker._write_all_progress({"feat-001": fp})


class TestGetOrCreateProgress:
    def test_creates_new_record(self) -> None:
        tracker = FeatureTracker()
        records: dict[str, FeatureProgress] = {}
        fp = tracker._get_or_create_progress(
            "feat-new", records, name="New", domain="d", project="p"
        )
        assert fp.feature_id == "feat-new"
        assert "feat-new" in records

    def test_returns_existing_record(self) -> None:
        tracker = FeatureTracker()
        existing = _make_progress(feature_id="feat-exist")
        records = {"feat-exist": existing}
        returned = tracker._get_or_create_progress("feat-exist", records)
        assert returned is existing

    def test_new_record_uses_provided_metadata(self) -> None:
        tracker = FeatureTracker()
        records: dict[str, FeatureProgress] = {}
        fp = tracker._get_or_create_progress(
            "feat-meta", records, name="Meta", domain="meta-dom", project="meta-proj"
        )
        assert fp.name == "Meta"
        assert fp.domain == "meta-dom"
        assert fp.project == "meta-proj"


# ===========================================================================
# FeatureTracker.load_features
# ===========================================================================


class TestLoadFeatures:
    def test_empty_when_no_file(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        assert tracker.load_features() == []

    def test_returns_progress_records(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = _make_progress(feature_id="feat-lf1")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(json.dumps(fp.to_dict()) + "\n")
        results = tracker.load_features()
        assert len(results) == 1
        assert results[0].feature_id == "feat-lf1"

    def test_filter_by_domain(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp1 = _make_progress(feature_id="f1", domain="dom-a")
        fp2 = _make_progress(feature_id="f2", domain="dom-b")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(
            json.dumps(fp1.to_dict()) + "\n" + json.dumps(fp2.to_dict()) + "\n"
        )
        results = tracker.load_features(domain="dom-a")
        assert all(r.domain == "dom-a" for r in results)
        assert len(results) == 1

    def test_filter_by_project(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp1 = _make_progress(feature_id="f1", domain="dom-a", project="proj-x")
        fp2 = _make_progress(feature_id="f2", domain="dom-a", project="proj-y")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(
            json.dumps(fp1.to_dict()) + "\n" + json.dumps(fp2.to_dict()) + "\n"
        )
        results = tracker.load_features(project="proj-x")
        assert len(results) == 1
        assert results[0].project == "proj-x"

    def test_sorted_by_feature_id(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        for fid in ["feat-c", "feat-a", "feat-b"]:
            fp = _make_progress(feature_id=fid)
            tracker._ensure_progress_dir()
            with tracker.progress_file.open("a") as fh:
                fh.write(json.dumps(fp.to_dict()) + "\n")
        results = tracker.load_features()
        ids = [r.feature_id for r in results]
        assert ids == sorted(ids)

    def test_features_json_list_format(self, tmp_path: Path) -> None:
        """features.json as a plain JSON array."""
        tracker = _tracker(tmp_path)
        domain_dir = tmp_path / "codeswiftr-com" / "myproject"
        domain_dir.mkdir(parents=True)
        feat_data = [{"id": "feat-json-1", "name": "From JSON"}]
        (domain_dir / "features.json").write_text(json.dumps(feat_data))
        results = tracker.load_features()
        ids = [r.feature_id for r in results]
        assert "feat-json-1" in ids

    def test_features_json_dict_format(self, tmp_path: Path) -> None:
        """features.json as {"features": [...]}."""
        tracker = _tracker(tmp_path)
        domain_dir = tmp_path / "brandfocus-ai" / "voice-coach"
        domain_dir.mkdir(parents=True)
        feat_data = {"features": [{"id": "feat-dict-1", "title": "Dict Feature"}]}
        (domain_dir / "features.json").write_text(json.dumps(feat_data))
        results = tracker.load_features()
        ids = [r.feature_id for r in results]
        assert "feat-dict-1" in ids

    def test_features_json_items_without_id_skipped(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        domain_dir = tmp_path / "dom" / "proj"
        domain_dir.mkdir(parents=True)
        feat_data = [{"name": "No ID here"}]
        (domain_dir / "features.json").write_text(json.dumps(feat_data))
        results = tracker.load_features()
        assert results == []

    def test_features_json_uses_feature_id_key(self, tmp_path: Path) -> None:
        """Items may use 'feature_id' as the key instead of 'id'."""
        tracker = _tracker(tmp_path)
        domain_dir = tmp_path / "dom" / "proj"
        domain_dir.mkdir(parents=True)
        feat_data = [{"feature_id": "feat-alt-key", "name": "Alt Key"}]
        (domain_dir / "features.json").write_text(json.dumps(feat_data))
        results = tracker.load_features()
        ids = [r.feature_id for r in results]
        assert "feat-alt-key" in ids

    def test_features_json_infers_domain_project_from_path(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        domain_dir = tmp_path / "inferred-domain" / "inferred-project"
        domain_dir.mkdir(parents=True)
        (domain_dir / "features.json").write_text(json.dumps([{"id": "feat-inf"}]))
        results = tracker.load_features()
        assert results[0].domain == "inferred-domain"
        assert results[0].project == "inferred-project"

    def test_features_json_honours_explicit_domain_project(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        domain_dir = tmp_path / "path-domain" / "path-project"
        domain_dir.mkdir(parents=True)
        feat_data = [{"id": "feat-exp", "domain": "explicit-domain", "project": "explicit-proj"}]
        (domain_dir / "features.json").write_text(json.dumps(feat_data))
        results = tracker.load_features()
        r = results[0]
        assert r.domain == "explicit-domain"
        assert r.project == "explicit-proj"

    def test_features_json_merges_with_existing_progress(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        domain_dir = tmp_path / "dom" / "proj"
        domain_dir.mkdir(parents=True)
        (domain_dir / "features.json").write_text(
            json.dumps([{"id": "feat-merge", "name": "Merged Name"}])
        )
        # Also write a progress record with no name
        existing = _make_progress(feature_id="feat-merge", name="", domain="unknown", project="unknown")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(json.dumps(existing.to_dict()) + "\n")
        results = tracker.load_features()
        assert len(results) == 1
        # Name should be updated from features.json
        assert results[0].name == "Merged Name"

    def test_features_json_does_not_overwrite_existing_non_unknown_domain(
        self, tmp_path: Path
    ) -> None:
        tracker = _tracker(tmp_path)
        domain_dir = tmp_path / "dom" / "proj"
        domain_dir.mkdir(parents=True)
        (domain_dir / "features.json").write_text(
            json.dumps([{"id": "feat-nodmn", "name": "N"}])
        )
        existing = _make_progress(feature_id="feat-nodmn", domain="already-set", project="p")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(json.dumps(existing.to_dict()) + "\n")
        results = tracker.load_features()
        assert results[0].domain == "already-set"

    def test_malformed_features_json_is_skipped(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        domain_dir = tmp_path / "dom" / "proj"
        domain_dir.mkdir(parents=True)
        (domain_dir / "features.json").write_text("NOT JSON AT ALL")
        # Should not raise, just skip the malformed file
        results = tracker.load_features()
        assert results == []

    def test_features_json_at_root_level_gets_unknown_project(self, tmp_path: Path) -> None:
        """A features.json directly under the domain dir (only 2 path parts)."""
        tracker = _tracker(tmp_path)
        dom_dir = tmp_path / "some-domain"
        dom_dir.mkdir()
        (dom_dir / "features.json").write_text(json.dumps([{"id": "feat-root-dom"}]))
        results = tracker.load_features()
        assert results[0].domain == "some-domain"
        assert results[0].project == "unknown"

    def test_features_json_at_root_gets_unknown_domain_and_project(
        self, tmp_path: Path
    ) -> None:
        """A features.json directly under features_dir (no domain nesting)."""
        tracker = _tracker(tmp_path)
        (tmp_path / "features.json").write_text(json.dumps([{"id": "feat-root"}]))
        results = tracker.load_features()
        assert results[0].domain == "unknown"
        assert results[0].project == "unknown"


# ===========================================================================
# FeatureTracker.get_feature
# ===========================================================================


class TestGetFeature:
    def test_returns_none_for_unknown_id(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        assert tracker.get_feature("no-such-id") is None

    def test_returns_record_for_known_id(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = _make_progress(feature_id="feat-gf")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(json.dumps(fp.to_dict()) + "\n")
        result = tracker.get_feature("feat-gf")
        assert result is not None
        assert result.feature_id == "feat-gf"


# ===========================================================================
# FeatureTracker.update_progress
# ===========================================================================


class TestUpdateProgress:
    def test_creates_new_record(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.update_progress("feat-new", completion_pct=50.0)
        assert fp.feature_id == "feat-new"
        assert fp.completion_pct == 50.0

    def test_updates_existing_record(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.update_progress("feat-upd", completion_pct=10.0)
        fp = tracker.update_progress("feat-upd", completion_pct=80.0)
        assert fp.completion_pct == 80.0

    def test_auto_status_zero_pct_is_planned(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.update_progress("feat-z", completion_pct=0.0)
        assert fp.status == "planned"

    def test_auto_status_partial_is_in_progress(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.update_progress("feat-p", completion_pct=55.0)
        assert fp.status == "in_progress"

    def test_auto_status_100_pct_is_done(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.update_progress("feat-d", completion_pct=100.0)
        assert fp.status == "done"

    def test_explicit_status_overrides_auto(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.update_progress("feat-s", completion_pct=50.0, status="testing")
        assert fp.status == "testing"

    @pytest.mark.parametrize("status", list(_VALID_STATUSES))
    def test_all_valid_statuses_accepted(self, status: str, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.update_progress("feat-vs", completion_pct=10.0, status=status)
        assert fp.status == status

    def test_invalid_status_raises(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        with pytest.raises(ValueError, match="Invalid status"):
            tracker.update_progress("feat-bad", completion_pct=10.0, status="bogus")

    def test_negative_pct_raises(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        with pytest.raises(ValueError, match="completion_pct"):
            tracker.update_progress("feat-neg", completion_pct=-1.0)

    def test_pct_above_100_raises(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        with pytest.raises(ValueError, match="completion_pct"):
            tracker.update_progress("feat-big", completion_pct=101.0)

    def test_persists_to_file(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.update_progress("feat-persist", completion_pct=75.0)
        records = tracker._read_all_progress()
        assert "feat-persist" in records
        assert records["feat-persist"].completion_pct == 75.0

    def test_updated_at_is_refreshed(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        before = datetime.now(UTC)
        fp = tracker.update_progress("feat-ts", completion_pct=20.0)
        after = datetime.now(UTC)
        assert before <= fp.updated_at <= after

    def test_metadata_passed_to_new_record(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.update_progress(
            "feat-meta",
            completion_pct=10.0,
            name="Meta Feature",
            domain="my-domain",
            project="my-project",
        )
        assert fp.name == "Meta Feature"
        assert fp.domain == "my-domain"
        assert fp.project == "my-project"


# ===========================================================================
# FeatureTracker.assign_agent
# ===========================================================================


class TestAssignAgent:
    def test_creates_record_and_assigns(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.assign_agent("feat-aa", "forge:claude")
        assert fp.assigned_agent == "forge:claude"

    def test_planned_status_transitions_to_in_progress(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.assign_agent("feat-trans", "forge:gpt")
        assert fp.status == "in_progress"

    def test_non_planned_status_is_not_changed(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        # First push it to done
        tracker.update_progress("feat-done", completion_pct=100.0)
        fp = tracker.assign_agent("feat-done", "forge:gemini")
        assert fp.status == "done"

    def test_empty_agent_id_does_not_transition_status(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        # New record starts as 'planned'
        fp = tracker.assign_agent("feat-emp", "")
        # status stays planned because agent_id is falsy
        assert fp.status == "planned"

    def test_persists_agent_assignment(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.assign_agent("feat-ag-persist", "forge:kimi")
        records = tracker._read_all_progress()
        assert records["feat-ag-persist"].assigned_agent == "forge:kimi"

    def test_updated_at_is_refreshed(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        before = datetime.now(UTC)
        fp = tracker.assign_agent("feat-aa-ts", "forge:agent")
        after = datetime.now(UTC)
        assert before <= fp.updated_at <= after


# ===========================================================================
# FeatureTracker.link_task
# ===========================================================================


class TestLinkTask:
    def test_adds_task_to_new_feature(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        fp = tracker.link_task("feat-lt", "task-001")
        assert "task-001" in fp.tasks

    def test_adds_task_to_existing_feature(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.update_progress("feat-lt2", completion_pct=10.0)
        fp = tracker.link_task("feat-lt2", "task-002")
        assert "task-002" in fp.tasks

    def test_duplicate_task_ignored(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.link_task("feat-dup", "task-dup")
        fp = tracker.link_task("feat-dup", "task-dup")
        assert fp.tasks.count("task-dup") == 1

    def test_multiple_tasks_accumulated(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.link_task("feat-multi", "task-a")
        tracker.link_task("feat-multi", "task-b")
        fp = tracker.link_task("feat-multi", "task-c")
        assert set(fp.tasks) == {"task-a", "task-b", "task-c"}

    def test_persists_task_link(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.link_task("feat-lt-persist", "task-persist")
        records = tracker._read_all_progress()
        assert "task-persist" in records["feat-lt-persist"].tasks

    def test_updated_at_is_refreshed(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        before = datetime.now(UTC)
        fp = tracker.link_task("feat-lt-ts", "task-x")
        after = datetime.now(UTC)
        assert before <= fp.updated_at <= after


# ===========================================================================
# FeatureTracker.get_stats
# ===========================================================================


class TestGetStats:
    def test_empty_stats(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        stats = tracker.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {s: 0 for s in _VALID_STATUSES}
        assert stats["by_domain"] == {}

    def test_counts_by_status(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.update_progress("f1", completion_pct=0.0)   # planned
        tracker.update_progress("f2", completion_pct=50.0)  # in_progress
        tracker.update_progress("f3", completion_pct=100.0) # done
        stats = tracker.get_stats()
        assert stats["total"] == 3
        assert stats["by_status"]["planned"] == 1
        assert stats["by_status"]["in_progress"] == 1
        assert stats["by_status"]["done"] == 1

    def test_counts_by_domain(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.update_progress("f1", completion_pct=0.0, domain="dom-a")
        tracker.update_progress("f2", completion_pct=0.0, domain="dom-a")
        tracker.update_progress("f3", completion_pct=0.0, domain="dom-b")
        stats = tracker.get_stats()
        assert stats["by_domain"]["dom-a"] == 2
        assert stats["by_domain"]["dom-b"] == 1

    def test_non_standard_status_counted(self, tmp_path: Path) -> None:
        """A record with a status not in _VALID_STATUSES should still be counted."""
        tracker = _tracker(tmp_path)
        # Manually inject a record with an unusual status
        fp = _make_progress(feature_id="f-odd", status="custom")
        tracker._ensure_progress_dir()
        tracker.progress_file.write_text(json.dumps(fp.to_dict()) + "\n")
        stats = tracker.get_stats()
        assert stats["by_status"].get("custom", 0) == 1

    def test_total_matches_record_count(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        for i in range(5):
            tracker.update_progress(f"feat-{i}", completion_pct=float(i * 10))
        stats = tracker.get_stats()
        assert stats["total"] == 5


# ===========================================================================
# Thread-safety smoke test
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_updates_do_not_corrupt(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        errors: list[Exception] = []

        def worker(fid: str) -> None:
            try:
                for pct in range(0, 101, 10):
                    tracker.update_progress(fid, completion_pct=float(pct))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"feat-{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        stats = tracker.get_stats()
        assert stats["total"] == 5


# ===========================================================================
# Singleton helpers
# ===========================================================================


class TestSingletonHelpers:
    def setup_method(self) -> None:
        reset_feature_tracker()

    def teardown_method(self) -> None:
        reset_feature_tracker()

    def test_get_feature_tracker_returns_instance(self) -> None:
        tracker = get_feature_tracker()
        assert isinstance(tracker, FeatureTracker)

    def test_get_feature_tracker_returns_same_instance(self) -> None:
        t1 = get_feature_tracker()
        t2 = get_feature_tracker()
        assert t1 is t2

    def test_second_call_ignores_new_arguments(self, tmp_path: Path) -> None:
        t1 = get_feature_tracker(features_dir=tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        t2 = get_feature_tracker(features_dir=other)
        assert t1 is t2
        assert t2.features_dir == tmp_path

    def test_reset_clears_singleton(self) -> None:
        t1 = get_feature_tracker()
        reset_feature_tracker()
        t2 = get_feature_tracker()
        assert t1 is not t2

    def test_reset_idempotent(self) -> None:
        reset_feature_tracker()
        reset_feature_tracker()  # Should not raise

    def test_singleton_custom_paths(self, tmp_path: Path) -> None:
        pf = tmp_path / "p.jsonl"
        tracker = get_feature_tracker(features_dir=tmp_path, progress_file=pf)
        assert tracker.features_dir == tmp_path
        assert tracker.progress_file == pf

    def test_get_feature_tracker_thread_safe(self) -> None:
        instances: list[FeatureTracker] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                instances.append(get_feature_tracker())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # All threads should see the exact same singleton
        assert all(inst is instances[0] for inst in instances)


# ===========================================================================
# End-to-end integration (all real FS ops in tmp_path)
# ===========================================================================


class TestEndToEnd:
    def test_full_workflow(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)

        # Load empty
        assert tracker.load_features() == []

        # Create via update_progress
        tracker.update_progress(
            "feat-e2e",
            completion_pct=10.0,
            name="E2E Feature",
            domain="test-domain",
            project="test-project",
        )

        # Assign agent
        tracker.assign_agent("feat-e2e", "forge:claude")

        # Link tasks
        tracker.link_task("feat-e2e", "task-1")
        tracker.link_task("feat-e2e", "task-2")
        tracker.link_task("feat-e2e", "task-2")  # duplicate

        # Complete it
        fp = tracker.update_progress("feat-e2e", completion_pct=100.0, status="done")

        assert fp.status == "done"
        assert fp.completion_pct == 100.0
        assert fp.assigned_agent == "forge:claude"
        assert fp.tasks == ["task-1", "task-2"]

        # Stats
        stats = tracker.get_stats()
        assert stats["total"] == 1
        assert stats["by_status"]["done"] == 1
        assert stats["by_domain"]["test-domain"] == 1

    def test_get_feature_after_update(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.update_progress("feat-ga", completion_pct=33.3)
        fp = tracker.get_feature("feat-ga")
        assert fp is not None
        assert fp.completion_pct == pytest.approx(33.3)

    def test_persistence_across_tracker_instances(self, tmp_path: Path) -> None:
        pf = tmp_path / "p.jsonl"
        tracker1 = FeatureTracker(features_dir=tmp_path, progress_file=pf)
        tracker1.update_progress("feat-cross", completion_pct=60.0, name="Cross")

        tracker2 = FeatureTracker(features_dir=tmp_path, progress_file=pf)
        fp = tracker2.get_feature("feat-cross")
        assert fp is not None
        assert fp.completion_pct == 60.0
        assert fp.name == "Cross"
