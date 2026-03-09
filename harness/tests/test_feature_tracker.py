"""Comprehensive tests for forge_harness.webhook_server.services.feature_tracker.

Coverage targets:
- FeatureProgress.from_dict / to_dict
- FeatureTracker.load_features — domain/project filters, features.json merging
- FeatureTracker.get_feature — hit and miss
- FeatureTracker.update_progress — pct, auto-status, explicit status, validation
- FeatureTracker.assign_agent — sets agent, transitions planned→in_progress
- FeatureTracker.link_task — adds task, deduplicates
- FeatureTracker.get_stats — totals, by_status, by_domain
- Persistence — data survives a new FeatureTracker instance
- Singleton — get_feature_tracker / reset_feature_tracker
- Thread safety — concurrent updates do not corrupt state
- Edge cases — corrupt lines, empty file, missing dir
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forge_harness.webhook_server.services.feature_tracker import (
    FeatureProgress,
    FeatureTracker,
    get_feature_tracker,
    reset_feature_tracker,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the singleton is cleared before and after every test."""
    reset_feature_tracker()
    yield
    reset_feature_tracker()


@pytest.fixture
def progress_file(tmp_path: Path) -> Path:
    """Return a path for a temporary JSONL progress file."""
    return tmp_path / ".forge" / "features" / "progress.jsonl"


@pytest.fixture
def features_dir(tmp_path: Path) -> Path:
    """Return a temporary features directory."""
    d = tmp_path / "portfolio"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def tracker(features_dir: Path, progress_file: Path) -> FeatureTracker:
    """Return a fresh FeatureTracker bound to temp directories."""
    return FeatureTracker(features_dir=features_dir, progress_file=progress_file)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_feature(
    tracker: FeatureTracker,
    feature_id: str = "feat-001",
    name: str = "Login Flow",
    domain: str = "codeswiftr-com",
    project: str = "interview-sim",
    completion_pct: float = 0.0,
    status: str = "planned",
) -> FeatureProgress:
    """Create/update a feature progress record."""
    return tracker.update_progress(
        feature_id=feature_id,
        completion_pct=completion_pct,
        status=status,
        name=name,
        domain=domain,
        project=project,
    )


def _make_features_json(features_dir: Path, domain: str, project: str, features: list) -> Path:
    """Write a features.json file under features_dir/domain/project/."""
    proj_dir = features_dir / domain / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    feat_file = proj_dir / "features.json"
    feat_file.write_text(json.dumps(features, indent=2))
    return feat_file


# ===========================================================================
# FeatureProgress model
# ===========================================================================


class TestFeatureProgressModel:
    def test_from_dict_basic(self):
        data = {
            "feature_id": "f1",
            "name": "Auth",
            "domain": "codeswiftr-com",
            "project": "is",
            "status": "in_progress",
            "completion_pct": 50.0,
            "assigned_agent": "forge:opencode",
            "tasks": ["t1", "t2"],
            "started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
        }
        fp = FeatureProgress.from_dict(data)
        assert fp.feature_id == "f1"
        assert fp.name == "Auth"
        assert fp.domain == "codeswiftr-com"
        assert fp.project == "is"
        assert fp.status == "in_progress"
        assert fp.completion_pct == 50.0
        assert fp.assigned_agent == "forge:opencode"
        assert fp.tasks == ["t1", "t2"]
        assert fp.started_at.tzinfo is not None
        assert fp.updated_at.tzinfo is not None

    def test_from_dict_naive_datetimes_get_utc(self):
        data = {
            "feature_id": "f2",
            "name": "X",
            "domain": "d",
            "project": "p",
            "started_at": "2026-01-01T10:00:00",  # no tzinfo
            "updated_at": "2026-01-01T11:00:00",  # no tzinfo
        }
        fp = FeatureProgress.from_dict(data)
        assert fp.started_at.tzinfo is not None
        assert fp.updated_at.tzinfo is not None

    def test_from_dict_defaults(self):
        fp = FeatureProgress.from_dict({"feature_id": "f3"})
        assert fp.status == "planned"
        assert fp.completion_pct == 0.0
        assert fp.assigned_agent is None
        assert fp.tasks == []

    def test_to_dict_round_trips(self):
        fp = FeatureProgress(
            feature_id="f4",
            name="OAuth",
            domain="d",
            project="p",
            status="testing",
            completion_pct=75.0,
            assigned_agent="ag-1",
            tasks=["task-1"],
        )
        d = fp.to_dict()
        fp2 = FeatureProgress.from_dict(d)
        assert fp2.feature_id == fp.feature_id
        assert fp2.status == fp.status
        assert fp2.completion_pct == fp.completion_pct
        assert fp2.tasks == fp.tasks
        assert fp2.assigned_agent == fp.assigned_agent

    def test_to_dict_contains_all_keys(self):
        fp = FeatureProgress(feature_id="f5", name="N", domain="d", project="p")
        d = fp.to_dict()
        assert set(d.keys()) == {
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


# ===========================================================================
# FeatureTracker.load_features
# ===========================================================================


class TestLoadFeatures:
    def test_empty_returns_empty_list(self, tracker: FeatureTracker):
        assert tracker.load_features() == []

    def test_returns_stored_records(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1", domain="d1")
        _write_feature(tracker, "f2", domain="d2")
        results = tracker.load_features()
        assert len(results) == 2

    def test_filter_by_domain(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1", domain="codeswiftr-com")
        _write_feature(tracker, "f2", domain="brandfocus-ai")
        results = tracker.load_features(domain="codeswiftr-com")
        assert len(results) == 1
        assert results[0].feature_id == "f1"

    def test_filter_by_project(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1", domain="d", project="interview-sim")
        _write_feature(tracker, "f2", domain="d", project="voice-coach")
        results = tracker.load_features(project="interview-sim")
        assert len(results) == 1
        assert results[0].feature_id == "f1"

    def test_filter_by_domain_and_project(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1", domain="d1", project="p1")
        _write_feature(tracker, "f2", domain="d1", project="p2")
        _write_feature(tracker, "f3", domain="d2", project="p1")
        results = tracker.load_features(domain="d1", project="p1")
        assert len(results) == 1
        assert results[0].feature_id == "f1"

    def test_sorted_by_feature_id(self, tracker: FeatureTracker):
        _write_feature(tracker, "zzz")
        _write_feature(tracker, "aaa")
        _write_feature(tracker, "mmm")
        results = tracker.load_features()
        ids = [r.feature_id for r in results]
        assert ids == sorted(ids)

    def test_merges_features_json_definitions(self, features_dir: Path, progress_file: Path):
        _make_features_json(
            features_dir,
            "codeswiftr-com",
            "interview-sim",
            [{"id": "feat-auth", "name": "Auth Module"}],
        )
        tracker = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        results = tracker.load_features()
        assert len(results) == 1
        assert results[0].feature_id == "feat-auth"
        assert results[0].name == "Auth Module"
        assert results[0].status == "planned"
        assert results[0].domain == "codeswiftr-com"

    def test_features_json_merges_with_existing_progress(
        self, features_dir: Path, progress_file: Path
    ):
        """Progress record + features.json definition → merged without overwriting status."""
        tracker = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        tracker.update_progress("feat-auth", completion_pct=60.0, status="testing")

        _make_features_json(
            features_dir,
            "codeswiftr-com",
            "interview-sim",
            [{"id": "feat-auth", "name": "Auth Module"}],
        )

        results = tracker.load_features()
        assert len(results) == 1
        fp = results[0]
        assert fp.completion_pct == 60.0
        assert fp.status == "testing"
        assert fp.name == "Auth Module"  # name filled in from features.json

    def test_features_json_list_variant(self, features_dir: Path, progress_file: Path):
        """features.json as plain list is handled."""
        _make_features_json(
            features_dir,
            "d",
            "p",
            [{"id": "f1"}, {"id": "f2"}],
        )
        tracker = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        results = tracker.load_features()
        assert len(results) == 2

    def test_features_json_with_dict_wrapper(self, features_dir: Path, progress_file: Path):
        """features.json as {"features": [...]} is handled."""
        proj_dir = features_dir / "d" / "p"
        proj_dir.mkdir(parents=True, exist_ok=True)
        feat_file = proj_dir / "features.json"
        feat_file.write_text(
            json.dumps({"features": [{"id": "f-wrapped", "name": "Wrapped Feature"}]})
        )
        tracker = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        results = tracker.load_features()
        assert any(r.feature_id == "f-wrapped" for r in results)

    def test_corrupt_features_json_skipped(self, features_dir: Path, progress_file: Path):
        """Malformed features.json does not crash load_features."""
        proj_dir = features_dir / "d" / "p"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "features.json").write_text("NOT_JSON{{{{")
        tracker = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        results = tracker.load_features()
        assert isinstance(results, list)


# ===========================================================================
# FeatureTracker.get_feature
# ===========================================================================


class TestGetFeature:
    def test_returns_feature_for_existing_id(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1")
        fp = tracker.get_feature("f1")
        assert fp is not None
        assert fp.feature_id == "f1"

    def test_returns_none_for_missing_id(self, tracker: FeatureTracker):
        assert tracker.get_feature("does-not-exist") is None

    def test_returns_none_when_no_progress_file(self, tmp_path: Path):
        tracker = FeatureTracker(
            features_dir=tmp_path,
            progress_file=tmp_path / "nope.jsonl",
        )
        assert tracker.get_feature("any") is None


# ===========================================================================
# FeatureTracker.update_progress
# ===========================================================================


class TestUpdateProgress:
    def test_creates_record_when_none_exists(self, tracker: FeatureTracker):
        fp = tracker.update_progress("new-feat", completion_pct=0.0)
        assert fp.feature_id == "new-feat"

    def test_updates_completion_pct(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1", completion_pct=0.0)
        fp = tracker.update_progress("f1", completion_pct=75.0)
        assert fp.completion_pct == 75.0

    def test_auto_status_zero_pct_is_planned(self, tracker: FeatureTracker):
        fp = tracker.update_progress("f1", completion_pct=0.0)
        assert fp.status == "planned"

    def test_auto_status_mid_pct_is_in_progress(self, tracker: FeatureTracker):
        fp = tracker.update_progress("f1", completion_pct=50.0)
        assert fp.status == "in_progress"

    def test_auto_status_100_pct_is_done(self, tracker: FeatureTracker):
        fp = tracker.update_progress("f1", completion_pct=100.0)
        assert fp.status == "done"

    def test_explicit_status_overrides_auto(self, tracker: FeatureTracker):
        fp = tracker.update_progress("f1", completion_pct=50.0, status="testing")
        assert fp.status == "testing"

    def test_all_valid_statuses_accepted(self, tracker: FeatureTracker):
        for s in ("planned", "in_progress", "testing", "done"):
            fp = tracker.update_progress(f"feat-{s}", completion_pct=0.0, status=s)
            assert fp.status == s

    def test_invalid_status_raises_value_error(self, tracker: FeatureTracker):
        with pytest.raises(ValueError, match="Invalid status"):
            tracker.update_progress("f1", completion_pct=10.0, status="invalid_state")

    def test_pct_below_zero_raises_value_error(self, tracker: FeatureTracker):
        with pytest.raises(ValueError, match="completion_pct"):
            tracker.update_progress("f1", completion_pct=-1.0)

    def test_pct_above_100_raises_value_error(self, tracker: FeatureTracker):
        with pytest.raises(ValueError, match="completion_pct"):
            tracker.update_progress("f1", completion_pct=101.0)

    def test_pct_boundary_0_accepted(self, tracker: FeatureTracker):
        fp = tracker.update_progress("f1", completion_pct=0.0)
        assert fp.completion_pct == 0.0

    def test_pct_boundary_100_accepted(self, tracker: FeatureTracker):
        fp = tracker.update_progress("f1", completion_pct=100.0)
        assert fp.completion_pct == 100.0

    def test_updated_at_is_refreshed(self, tracker: FeatureTracker):
        fp1 = _write_feature(tracker, "f1")
        old_ts = fp1.updated_at
        import time

        time.sleep(0.02)
        fp2 = tracker.update_progress("f1", completion_pct=10.0)
        assert fp2.updated_at >= old_ts

    def test_persists_to_disk(self, tracker: FeatureTracker, progress_file: Path):
        tracker.update_progress("f1", completion_pct=33.0)
        # New tracker reading the same file
        tracker2 = FeatureTracker(
            features_dir=tracker.features_dir,
            progress_file=progress_file,
        )
        fp = tracker2.get_feature("f1")
        assert fp is not None
        assert fp.completion_pct == 33.0


# ===========================================================================
# FeatureTracker.assign_agent
# ===========================================================================


class TestAssignAgent:
    def test_sets_assigned_agent(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1")
        fp = tracker.assign_agent("f1", "forge:opencode")
        assert fp.assigned_agent == "forge:opencode"

    def test_transitions_planned_to_in_progress(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1", completion_pct=0.0, status="planned")
        fp = tracker.assign_agent("f1", "forge:opencode")
        assert fp.status == "in_progress"

    def test_does_not_override_existing_status(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1", completion_pct=80.0, status="testing")
        fp = tracker.assign_agent("f1", "forge:codex")
        assert fp.status == "testing"

    def test_creates_record_if_missing(self, tracker: FeatureTracker):
        fp = tracker.assign_agent("brand-new", "forge:gemini")
        assert fp.feature_id == "brand-new"
        assert fp.assigned_agent == "forge:gemini"

    def test_persists_to_disk(self, tracker: FeatureTracker, progress_file: Path):
        tracker.assign_agent("f1", "forge:opencode")
        tracker2 = FeatureTracker(
            features_dir=tracker.features_dir,
            progress_file=progress_file,
        )
        fp = tracker2.get_feature("f1")
        assert fp is not None
        assert fp.assigned_agent == "forge:opencode"

    def test_reassign_overwrites_previous_agent(self, tracker: FeatureTracker):
        tracker.assign_agent("f1", "forge:opencode")
        fp = tracker.assign_agent("f1", "forge:codex")
        assert fp.assigned_agent == "forge:codex"


# ===========================================================================
# FeatureTracker.link_task
# ===========================================================================


class TestLinkTask:
    def test_adds_task_id(self, tracker: FeatureTracker):
        fp = tracker.link_task("f1", "task-abc")
        assert "task-abc" in fp.tasks

    def test_duplicate_task_ignored(self, tracker: FeatureTracker):
        tracker.link_task("f1", "task-abc")
        fp = tracker.link_task("f1", "task-abc")
        assert fp.tasks.count("task-abc") == 1

    def test_multiple_different_tasks(self, tracker: FeatureTracker):
        tracker.link_task("f1", "task-1")
        tracker.link_task("f1", "task-2")
        fp = tracker.link_task("f1", "task-3")
        assert set(fp.tasks) == {"task-1", "task-2", "task-3"}

    def test_creates_record_if_missing(self, tracker: FeatureTracker):
        fp = tracker.link_task("new-feat", "task-xyz")
        assert fp.feature_id == "new-feat"
        assert "task-xyz" in fp.tasks

    def test_persists_to_disk(self, tracker: FeatureTracker, progress_file: Path):
        tracker.link_task("f1", "task-111")
        tracker2 = FeatureTracker(
            features_dir=tracker.features_dir,
            progress_file=progress_file,
        )
        fp = tracker2.get_feature("f1")
        assert fp is not None
        assert "task-111" in fp.tasks


# ===========================================================================
# FeatureTracker.get_stats
# ===========================================================================


class TestGetStats:
    def test_empty_tracker_returns_zeros(self, tracker: FeatureTracker):
        stats = tracker.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"]["planned"] == 0
        assert stats["by_domain"] == {}

    def test_by_status_counts(self, tracker: FeatureTracker):
        tracker.update_progress("f1", 0.0, status="planned")
        tracker.update_progress("f2", 50.0, status="in_progress")
        tracker.update_progress("f3", 80.0, status="testing")
        tracker.update_progress("f4", 100.0, status="done")

        stats = tracker.get_stats()
        assert stats["total"] == 4
        assert stats["by_status"]["planned"] == 1
        assert stats["by_status"]["in_progress"] == 1
        assert stats["by_status"]["testing"] == 1
        assert stats["by_status"]["done"] == 1

    def test_by_domain_counts(self, tracker: FeatureTracker):
        _write_feature(tracker, "f1", domain="codeswiftr-com")
        _write_feature(tracker, "f2", domain="codeswiftr-com")
        _write_feature(tracker, "f3", domain="brandfocus-ai")

        stats = tracker.get_stats()
        assert stats["by_domain"]["codeswiftr-com"] == 2
        assert stats["by_domain"]["brandfocus-ai"] == 1

    def test_total_matches_record_count(self, tracker: FeatureTracker):
        for i in range(7):
            tracker.update_progress(f"feat-{i}", completion_pct=float(i * 10))
        stats = tracker.get_stats()
        assert stats["total"] == 7

    def test_by_status_initialised_for_all_valid_statuses(self, tracker: FeatureTracker):
        stats = tracker.get_stats()
        for s in ("planned", "in_progress", "testing", "done"):
            assert s in stats["by_status"]


# ===========================================================================
# Persistence across instances
# ===========================================================================


class TestPersistence:
    def test_data_survives_new_instance(self, features_dir: Path, progress_file: Path):
        t1 = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        t1.update_progress(
            "persist-feat", 60.0, status="testing", name="X", domain="d", project="p"
        )
        t1.assign_agent("persist-feat", "forge:opencode")
        t1.link_task("persist-feat", "task-99")

        t2 = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        fp = t2.get_feature("persist-feat")
        assert fp is not None
        assert fp.completion_pct == 60.0
        assert fp.status == "testing"
        assert fp.assigned_agent == "forge:opencode"
        assert "task-99" in fp.tasks

    def test_multiple_features_persist(self, features_dir: Path, progress_file: Path):
        t1 = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        for i in range(5):
            t1.update_progress(f"feat-{i}", completion_pct=float(i * 20))

        t2 = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        assert len(t2.load_features()) == 5

    def test_corrupt_jsonl_lines_skipped(self, features_dir: Path, progress_file: Path):
        """Malformed JSONL lines are silently skipped; valid records load fine."""
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "NOT_JSON\n"
            + json.dumps(
                {
                    "feature_id": "valid-feat",
                    "name": "Valid",
                    "domain": "d",
                    "project": "p",
                    "status": "planned",
                    "completion_pct": 0.0,
                    "assigned_agent": None,
                    "tasks": [],
                    "started_at": datetime.now(UTC).isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            + "\n"
        )
        t = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        features = t.load_features()
        assert len(features) == 1
        assert features[0].feature_id == "valid-feat"

    def test_empty_progress_file_handled(self, features_dir: Path, progress_file: Path):
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text("")
        t = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        assert t.load_features() == []


# ===========================================================================
# Singleton pattern
# ===========================================================================


class TestSingleton:
    def test_get_feature_tracker_returns_same_instance(
        self, features_dir: Path, progress_file: Path
    ):
        t1 = get_feature_tracker(features_dir=features_dir, progress_file=progress_file)
        t2 = get_feature_tracker()
        assert t1 is t2

    def test_reset_clears_singleton(self, features_dir: Path, progress_file: Path):
        t1 = get_feature_tracker(features_dir=features_dir, progress_file=progress_file)
        reset_feature_tracker()
        t2 = get_feature_tracker(features_dir=features_dir, progress_file=progress_file)
        assert t1 is not t2

    def test_second_call_ignores_new_kwargs(self, tmp_path: Path):
        """After first call, new constructor args are silently ignored."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        t1 = get_feature_tracker(features_dir=dir_a)
        t2 = get_feature_tracker(features_dir=dir_b)  # ignored
        assert t1 is t2
        assert t1.features_dir == dir_a

    def test_singleton_thread_safe(self, features_dir: Path, progress_file: Path):
        results: list[FeatureTracker] = []
        lock = threading.Lock()

        def getter():
            t = get_feature_tracker(features_dir=features_dir, progress_file=progress_file)
            with lock:
                results.append(t)

        threads = [threading.Thread(target=getter) for _ in range(20)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert all(t is results[0] for t in results)


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_updates_no_corruption(self, features_dir: Path, progress_file: Path):
        """Multiple threads updating different features must not corrupt the file."""
        t = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(idx: int):
            try:
                t.update_progress(f"feat-{idx}", completion_pct=float(idx), domain="d", project="p")
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"Thread errors: {errors}"
        results = t.load_features()
        assert len(results) == 20

    def test_concurrent_link_task_no_duplicates(self, features_dir: Path, progress_file: Path):
        """Concurrent link_task calls for the same task_id must not create duplicates."""
        t = FeatureTracker(features_dir=features_dir, progress_file=progress_file)
        t.update_progress("f1", completion_pct=0.0)

        errors: list[Exception] = []
        lock = threading.Lock()

        def linker():
            try:
                t.link_task("f1", "shared-task")
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=linker) for _ in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"Thread errors: {errors}"
        fp = t.get_feature("f1")
        assert fp is not None
        assert fp.tasks.count("shared-task") == 1


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_default_features_dir_is_cwd(self):
        t = FeatureTracker(features_dir=None)
        assert t.features_dir == Path(".")

    def test_default_progress_file_path(self):
        t = FeatureTracker()
        assert t.progress_file == Path(".forge") / "features" / "progress.jsonl"

    def test_progress_dir_created_on_write(self, tmp_path: Path):
        deep_path = tmp_path / "deep" / "nested" / "progress.jsonl"
        t = FeatureTracker(progress_file=deep_path)
        t.update_progress("f1", 0.0)
        assert deep_path.exists()

    def test_get_feature_with_no_file_returns_none(self, tmp_path: Path):
        t = FeatureTracker(
            features_dir=tmp_path,
            progress_file=tmp_path / "nonexistent.jsonl",
        )
        assert t.get_feature("anything") is None

    def test_load_features_no_dir_returns_empty(self, tmp_path: Path):
        t = FeatureTracker(
            features_dir=tmp_path / "no_such_dir",
            progress_file=tmp_path / "no_such.jsonl",
        )
        assert t.load_features() == []
