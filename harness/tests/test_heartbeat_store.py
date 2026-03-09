"""Tests for HeartbeatStore (CP-2003).

Coverage targets
----------------
- HeartbeatRecord model validation and helpers (age_seconds, is_fresh,
  availability_score)
- HeartbeatStore.record()      — upsert + persistence
- HeartbeatStore.get()         — retrieval with live status computation
- HeartbeatStore.list_active() — TTL filtering + ranking
- HeartbeatStore.get_scheduler_view() — full scheduler dict schema
- HeartbeatStore.mark_stale()  — TTL-based staleness mutation
- JSONL persistence round-trip — reload after restart
- Thread safety                — concurrent record() calls
- Singleton factory            — get/reset helpers
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.services.heartbeat_store import (
    HeartbeatRecord,
    HeartbeatStore,
    get_heartbeat_store,
    reset_heartbeat_store,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure each test starts with a clean singleton."""
    reset_heartbeat_store()
    yield
    reset_heartbeat_store()


@pytest.fixture()
def store(tmp_path: Path) -> HeartbeatStore:
    """Return a HeartbeatStore backed by a temp directory."""
    return HeartbeatStore(storage_path=tmp_path / ".forge" / "heartbeats" / "nodes.jsonl")


@pytest.fixture()
def sample_data() -> dict:
    """Minimal valid telemetry payload for a node."""
    return {
        "hostname": "nova.local",
        "cpu_percent": 30.0,
        "memory_percent": 50.0,
        "active_agents": 3,
        "active_leases": 1,
    }


# ===========================================================================
# HeartbeatRecord unit tests
# ===========================================================================


class TestHeartbeatRecord:
    """Tests for the HeartbeatRecord Pydantic model and its helper methods."""

    def test_defaults(self):
        """All optional fields have sensible defaults."""
        rec = HeartbeatRecord(node_id="nova")
        assert rec.hostname == ""
        assert rec.cpu_percent == 0.0
        assert rec.memory_percent == 0.0
        assert rec.active_agents == 0
        assert rec.active_leases == 0
        assert rec.status == "healthy"
        assert rec.ttl_seconds == 120

    def test_age_seconds_fresh(self):
        """A just-recorded timestamp should report near-zero age."""
        now = datetime.now(UTC).isoformat()
        rec = HeartbeatRecord(node_id="nova", last_seen=now)
        assert rec.age_seconds() < 2.0

    def test_age_seconds_old(self):
        """A timestamp from 5 minutes ago should report ~300 s age."""
        old_ts = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        rec = HeartbeatRecord(node_id="nova", last_seen=old_ts)
        assert 295 < rec.age_seconds() < 310

    def test_age_seconds_empty_last_seen(self):
        """Missing last_seen should return infinity so the node is treated as stale."""
        rec = HeartbeatRecord(node_id="nova", last_seen="")
        assert rec.age_seconds() == float("inf")

    def test_age_seconds_unparseable_timestamp(self):
        """A completely invalid timestamp string should return infinity."""
        rec = HeartbeatRecord(node_id="nova", last_seen="not-a-date-at-all")
        assert rec.age_seconds() == float("inf")

    def test_age_seconds_naive_timestamp(self):
        """Naive (timezone-unaware) ISO timestamps are handled without error."""
        naive_ts = datetime.utcnow().isoformat()  # no tzinfo
        rec = HeartbeatRecord(node_id="nova", last_seen=naive_ts)
        age = rec.age_seconds()
        assert 0 <= age < 5.0

    def test_is_fresh_within_ttl(self):
        """A recent record should be considered fresh."""
        now = datetime.now(UTC).isoformat()
        rec = HeartbeatRecord(node_id="nova", last_seen=now, ttl_seconds=120)
        assert rec.is_fresh() is True

    def test_is_fresh_past_ttl(self):
        """A record older than its TTL should not be considered fresh."""
        old_ts = (datetime.now(UTC) - timedelta(seconds=200)).isoformat()
        rec = HeartbeatRecord(node_id="nova", last_seen=old_ts, ttl_seconds=120)
        assert rec.is_fresh() is False

    def test_is_fresh_override_ttl(self):
        """An explicit ttl_seconds argument overrides the record's own TTL."""
        old_ts = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        rec = HeartbeatRecord(node_id="nova", last_seen=old_ts, ttl_seconds=120)
        # 60 s < 120 s -> fresh with default
        assert rec.is_fresh() is True
        # 60 s > 30 s -> stale with override
        assert rec.is_fresh(ttl_seconds=30) is False

    def test_availability_score_idle(self):
        """Zero load should yield a score of 1.0."""
        now = datetime.now(UTC).isoformat()
        rec = HeartbeatRecord(
            node_id="nova",
            last_seen=now,
            cpu_percent=0.0,
            memory_percent=0.0,
            status="healthy",
        )
        assert rec.availability_score() == pytest.approx(1.0)

    def test_availability_score_saturated(self):
        """Full load should yield a score of 0.0."""
        now = datetime.now(UTC).isoformat()
        rec = HeartbeatRecord(
            node_id="nova",
            last_seen=now,
            cpu_percent=100.0,
            memory_percent=100.0,
            status="healthy",
        )
        assert rec.availability_score() == pytest.approx(0.0)

    def test_availability_score_midpoint(self):
        """50% CPU + 50% memory should yield 0.5."""
        now = datetime.now(UTC).isoformat()
        rec = HeartbeatRecord(
            node_id="nova",
            last_seen=now,
            cpu_percent=50.0,
            memory_percent=50.0,
            status="healthy",
        )
        assert rec.availability_score() == pytest.approx(0.5)

    def test_availability_score_stale_node_returns_zero(self):
        """Stale nodes should always score 0.0 so they rank last."""
        now = datetime.now(UTC).isoformat()
        rec = HeartbeatRecord(
            node_id="nova",
            last_seen=now,
            cpu_percent=0.0,
            memory_percent=0.0,
            status="stale",
        )
        assert rec.availability_score() == 0.0

    def test_availability_score_offline_node_returns_zero(self):
        """Offline nodes should also score 0.0."""
        now = datetime.now(UTC).isoformat()
        rec = HeartbeatRecord(
            node_id="nova",
            last_seen=now,
            cpu_percent=10.0,
            memory_percent=20.0,
            status="offline",
        )
        assert rec.availability_score() == 0.0

    def test_field_validation_cpu_clamp(self):
        """cpu_percent must be within [0, 100]."""
        with pytest.raises(Exception):
            HeartbeatRecord(node_id="nova", cpu_percent=150.0)

    def test_field_validation_negative_active_agents(self):
        """active_agents must be >= 0."""
        with pytest.raises(Exception):
            HeartbeatRecord(node_id="nova", active_agents=-1)

    def test_model_dump_json_round_trip(self):
        """A record should survive JSON serialisation and deserialisation."""
        now = datetime.now(UTC).isoformat()
        original = HeartbeatRecord(
            node_id="nova",
            hostname="nova.local",
            cpu_percent=42.0,
            memory_percent=58.0,
            active_agents=2,
            active_leases=1,
            last_seen=now,
        )
        raw_json = original.model_dump_json()
        restored = HeartbeatRecord.model_validate_json(raw_json)
        assert restored.node_id == original.node_id
        assert restored.cpu_percent == original.cpu_percent
        assert restored.memory_percent == original.memory_percent


# ===========================================================================
# HeartbeatStore — record and retrieval
# ===========================================================================


class TestHeartbeatStoreRecord:
    """Tests for HeartbeatStore.record() and HeartbeatStore.get()."""

    def test_record_returns_heartbeat_record(self, store, sample_data):
        rec = store.record("nova", sample_data)
        assert isinstance(rec, HeartbeatRecord)
        assert rec.node_id == "nova"

    def test_record_sets_last_seen(self, store, sample_data):
        before = datetime.now(UTC)
        rec = store.record("nova", sample_data)
        after = datetime.now(UTC)

        ts = datetime.fromisoformat(rec.last_seen)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        assert before <= ts <= after

    def test_record_sets_status_healthy(self, store, sample_data):
        rec = store.record("nova", sample_data)
        assert rec.status == "healthy"

    def test_record_stores_all_fields(self, store, sample_data):
        rec = store.record("nova", sample_data)
        assert rec.hostname == "nova.local"
        assert rec.cpu_percent == 30.0
        assert rec.memory_percent == 50.0
        assert rec.active_agents == 3
        assert rec.active_leases == 1

    def test_record_upsert_overwrites_previous(self, store, sample_data):
        store.record("nova", sample_data)
        updated = {**sample_data, "cpu_percent": 80.0}
        rec = store.record("nova", updated)
        assert rec.cpu_percent == 80.0
        # Only one record should exist for this node
        assert len(store.list_active()) == 1

    def test_get_existing_node(self, store, sample_data):
        store.record("nova", sample_data)
        rec = store.get("nova")
        assert rec is not None
        assert rec.node_id == "nova"

    def test_get_missing_node_returns_none(self, store):
        result = store.get("nonexistent-node")
        assert result is None

    def test_get_stale_node_returns_stale_status(self, store, sample_data):
        """get() should compute live status without mutating the store."""
        old_ts = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        # Manually insert a record with an old timestamp bypassing record()
        with store._lock:
            store._ensure_loaded()
            store._records["prya"] = HeartbeatRecord(
                node_id="prya",
                hostname="prya.local",
                last_seen=old_ts,
                ttl_seconds=120,
                status="healthy",
            )

        rec = store.get("prya")
        assert rec is not None
        assert rec.status == "stale"

    def test_multiple_nodes_stored_independently(self, store, sample_data):
        store.record("nova", sample_data)
        store.record("prya", {**sample_data, "hostname": "prya.local", "cpu_percent": 70.0})
        assert store.get("nova") is not None
        assert store.get("prya") is not None


# ===========================================================================
# HeartbeatStore — list_active and TTL filtering
# ===========================================================================


class TestHeartbeatStoreListActive:
    """Tests for HeartbeatStore.list_active() TTL filtering and ranking."""

    def test_fresh_nodes_included(self, store, sample_data):
        store.record("nova", sample_data)
        active = store.list_active()
        assert len(active) == 1
        assert active[0].node_id == "nova"

    def test_stale_nodes_excluded(self, store, sample_data):
        """Nodes with last_seen older than ttl_seconds should be excluded."""
        store.record("nova", sample_data)
        old_ts = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        with store._lock:
            store._ensure_loaded()
            store._records["prya"] = HeartbeatRecord(
                node_id="prya",
                last_seen=old_ts,
                ttl_seconds=120,
                status="healthy",
            )
        # Only the fresh "nova" should appear
        active = store.list_active(ttl_seconds=120)
        node_ids = [r.node_id for r in active]
        assert "nova" in node_ids
        assert "prya" not in node_ids

    def test_ranked_by_availability_descending(self, store):
        """Higher-availability nodes should appear earlier in the list."""
        now = datetime.now(UTC).isoformat()
        # nova: low load -> high score
        store.record("nova", {"cpu_percent": 10.0, "memory_percent": 20.0})
        # prya: high load -> low score
        store.record("prya", {"cpu_percent": 90.0, "memory_percent": 85.0})

        active = store.list_active()
        assert len(active) == 2
        assert active[0].node_id == "nova"
        assert active[1].node_id == "prya"
        assert active[0].availability_score() > active[1].availability_score()

    def test_empty_store_returns_empty_list(self, store):
        assert store.list_active() == []

    def test_custom_ttl_applied(self, store, sample_data):
        """A very short TTL should cause a just-recorded node to be excluded."""
        store.record("nova", sample_data)
        # With a 0-second TTL virtually every record is stale
        # (age > 0 s due to elapsed time between record() and list_active())
        time.sleep(0.01)
        active = store.list_active(ttl_seconds=0)
        assert len(active) == 0


# ===========================================================================
# HeartbeatStore — get_scheduler_view
# ===========================================================================


class TestGetSchedulerView:
    """Tests for the scheduler view dict returned by get_scheduler_view()."""

    def test_empty_store_schema(self, store):
        """An empty store should still return a fully-formed schema."""
        view = store.get_scheduler_view()
        assert "generated_at" in view
        assert "ttl_seconds" in view
        assert "total_nodes" in view
        assert "active_nodes" in view
        assert "stale_nodes" in view
        assert "ranked_nodes" in view
        assert "node_loads" in view
        assert "capacities" in view
        assert "recommended_node_id" in view

    def test_empty_store_values(self, store):
        view = store.get_scheduler_view()
        assert view["total_nodes"] == 0
        assert view["active_nodes"] == 0
        assert view["stale_nodes"] == 0
        assert view["ranked_nodes"] == []
        assert view["recommended_node_id"] is None

    def test_single_node_view(self, store, sample_data):
        store.record("nova", sample_data)
        view = store.get_scheduler_view()

        assert view["total_nodes"] == 1
        assert view["active_nodes"] == 1
        assert view["stale_nodes"] == 0
        assert view["recommended_node_id"] == "nova"
        assert len(view["ranked_nodes"]) == 1

    def test_ranked_nodes_fields(self, store, sample_data):
        """Each entry in ranked_nodes must have the required scheduler fields."""
        store.record("nova", sample_data)
        view = store.get_scheduler_view()
        node = view["ranked_nodes"][0]

        required_keys = {
            "node_id",
            "hostname",
            "availability_score",
            "cpu_percent",
            "memory_percent",
            "active_agents",
            "active_leases",
            "age_seconds",
            "status",
        }
        assert required_keys.issubset(node.keys())

    def test_node_loads_present(self, store, sample_data):
        store.record("nova", sample_data)
        view = store.get_scheduler_view()
        assert "nova" in view["node_loads"]
        load = view["node_loads"]["nova"]
        assert load["cpu_percent"] == 30.0
        assert load["memory_percent"] == 50.0

    def test_capacities_present(self, store, sample_data):
        store.record("nova", sample_data)
        view = store.get_scheduler_view()
        assert "nova" in view["capacities"]
        cap = view["capacities"]["nova"]
        assert "available_agents" in cap
        assert "availability_score" in cap

    def test_recommended_node_is_best_score(self, store):
        """The recommended_node_id should be the highest-scoring node."""
        store.record("nova", {"cpu_percent": 10.0, "memory_percent": 20.0})
        store.record("prya", {"cpu_percent": 80.0, "memory_percent": 90.0})
        view = store.get_scheduler_view()
        assert view["recommended_node_id"] == "nova"

    def test_stale_nodes_not_in_ranked(self, store, sample_data):
        """Stale nodes must not appear in ranked_nodes or node_loads."""
        store.record("nova", sample_data)
        old_ts = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        with store._lock:
            store._ensure_loaded()
            store._records["prya"] = HeartbeatRecord(
                node_id="prya",
                last_seen=old_ts,
                ttl_seconds=120,
                status="healthy",
            )
        view = store.get_scheduler_view()
        ranked_ids = [n["node_id"] for n in view["ranked_nodes"]]
        assert "prya" not in ranked_ids
        assert "prya" not in view["node_loads"]
        assert view["total_nodes"] == 2
        assert view["stale_nodes"] == 1

    def test_generated_at_is_recent(self, store):
        before = datetime.now(UTC)
        view = store.get_scheduler_view()
        after = datetime.now(UTC)
        ts = datetime.fromisoformat(view["generated_at"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        assert before <= ts <= after

    def test_ttl_seconds_reflected_in_view(self, store):
        view = store.get_scheduler_view(ttl_seconds=60)
        assert view["ttl_seconds"] == 60


# ===========================================================================
# HeartbeatStore — mark_stale
# ===========================================================================


class TestMarkStale:
    """Tests for HeartbeatStore.mark_stale()."""

    def test_mark_stale_returns_affected_node_ids(self, store, sample_data):
        """Nodes past TTL should be included in the returned list."""
        old_ts = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        with store._lock:
            store._ensure_loaded()
            store._records["prya"] = HeartbeatRecord(
                node_id="prya",
                last_seen=old_ts,
                ttl_seconds=120,
                status="healthy",
            )
        marked = store.mark_stale(ttl_seconds=120)
        assert "prya" in marked

    def test_fresh_nodes_not_marked(self, store, sample_data):
        """Nodes within TTL must not be marked stale."""
        store.record("nova", sample_data)
        marked = store.mark_stale(ttl_seconds=120)
        assert "nova" not in marked

    def test_already_stale_nodes_not_re_marked(self, store):
        """Nodes already in stale status must not be returned again."""
        old_ts = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        with store._lock:
            store._ensure_loaded()
            store._records["prya"] = HeartbeatRecord(
                node_id="prya",
                last_seen=old_ts,
                ttl_seconds=120,
                status="stale",  # already stale
            )
        marked = store.mark_stale(ttl_seconds=120)
        assert "prya" not in marked

    def test_mark_stale_persists_to_disk(self, store):
        """After mark_stale(), the JSONL file should reflect the stale status."""
        old_ts = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        with store._lock:
            store._ensure_loaded()
            store._records["prya"] = HeartbeatRecord(
                node_id="prya",
                last_seen=old_ts,
                ttl_seconds=120,
                status="healthy",
            )
        store.mark_stale(ttl_seconds=120)

        # Read the file directly and verify the status field
        lines = store._path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["status"] == "stale"

    def test_mark_stale_empty_store(self, store):
        """mark_stale() on an empty store should return an empty list."""
        assert store.mark_stale() == []


# ===========================================================================
# JSONL persistence round-trip
# ===========================================================================


class TestJSONLPersistence:
    """Tests for JSONL-backed persistence and cross-instance reload."""

    def test_record_creates_jsonl_file(self, store, sample_data):
        store.record("nova", sample_data)
        assert store._path.exists()

    def test_jsonl_file_has_one_line_per_node(self, store, sample_data):
        store.record("nova", sample_data)
        store.record("prya", {**sample_data, "hostname": "prya.local"})
        lines = store._path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_upsert_does_not_duplicate_lines(self, store, sample_data):
        """Recording the same node twice should not create two lines."""
        store.record("nova", sample_data)
        store.record("nova", {**sample_data, "cpu_percent": 60.0})
        lines = store._path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_reload_from_jsonl(self, tmp_path, sample_data):
        """A fresh HeartbeatStore instance should reload records from disk."""
        path = tmp_path / ".forge" / "heartbeats" / "nodes.jsonl"
        store1 = HeartbeatStore(storage_path=path)
        store1.record("nova", sample_data)

        # Create a second instance pointing at the same file
        store2 = HeartbeatStore(storage_path=path)
        rec = store2.get("nova")
        assert rec is not None
        assert rec.node_id == "nova"
        assert rec.cpu_percent == 30.0

    def test_malformed_lines_skipped(self, tmp_path):
        """Malformed JSONL lines should be silently skipped on load."""
        path = tmp_path / "nodes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write one good line and one bad line
        good = HeartbeatRecord(
            node_id="nova",
            hostname="nova.local",
            last_seen=datetime.now(UTC).isoformat(),
        )
        path.write_text(good.model_dump_json() + "\n{bad json\n", encoding="utf-8")

        store = HeartbeatStore(storage_path=path)
        rec = store.get("nova")
        assert rec is not None
        assert rec.node_id == "nova"

    def test_missing_file_treated_as_empty_store(self, tmp_path):
        """A missing JSONL file should not raise — the store starts empty."""
        path = tmp_path / "nonexistent" / "nodes.jsonl"
        store = HeartbeatStore(storage_path=path)
        assert store.list_active() == []

    def test_blank_lines_in_jsonl_skipped(self, tmp_path):
        """Blank lines interspersed in the JSONL file must be skipped cleanly."""
        path = tmp_path / "nodes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        good = HeartbeatRecord(
            node_id="nova",
            hostname="nova.local",
            last_seen=datetime.now(UTC).isoformat(),
        )
        # Write good record, then a blank line, then another good record
        good2 = HeartbeatRecord(
            node_id="prya",
            hostname="prya.local",
            last_seen=datetime.now(UTC).isoformat(),
        )
        path.write_text(
            good.model_dump_json() + "\n\n" + good2.model_dump_json() + "\n",
            encoding="utf-8",
        )
        store = HeartbeatStore(storage_path=path)
        assert store.get("nova") is not None
        assert store.get("prya") is not None

    def test_jsonl_records_are_valid_json(self, store, sample_data):
        """Each line written to the JSONL file must be parseable JSON."""
        store.record("nova", sample_data)
        store.record("prya", {**sample_data, "hostname": "prya.local"})
        for line in store._path.read_text(encoding="utf-8").strip().splitlines():
            data = json.loads(line)  # must not raise
            assert "node_id" in data
            assert "last_seen" in data


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    """Tests for concurrent access to HeartbeatStore."""

    def test_concurrent_record_calls_all_succeed(self, store):
        """Multiple threads recording different nodes must all succeed."""
        errors: list[Exception] = []
        node_count = 20

        def worker(node_id: str) -> None:
            try:
                store.record(node_id, {"cpu_percent": 50.0, "memory_percent": 50.0})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"node-{i}",)) for i in range(node_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        active = store.list_active()
        assert len(active) == node_count

    def test_concurrent_record_same_node_is_safe(self, store):
        """Concurrent upserts for the same node must not corrupt state."""
        errors: list[Exception] = []

        def worker(cpu: float) -> None:
            try:
                store.record("nova", {"cpu_percent": cpu, "memory_percent": 50.0})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(float(i),)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Exactly one record for "nova"
        rec = store.get("nova")
        assert rec is not None
        assert rec.node_id == "nova"

    def test_concurrent_read_write_consistency(self, store, sample_data):
        """Reads during concurrent writes should not raise exceptions."""
        errors: list[Exception] = []
        stop_event = threading.Event()

        def writer() -> None:
            i = 0
            while not stop_event.is_set():
                try:
                    store.record(f"node-{i % 5}", {"cpu_percent": float(i % 100)})
                    i += 1
                except Exception as exc:
                    errors.append(exc)
                    break

        def reader() -> None:
            for _ in range(50):
                try:
                    store.list_active()
                    store.get_scheduler_view()
                except Exception as exc:
                    errors.append(exc)

        write_thread = threading.Thread(target=writer)
        read_thread = threading.Thread(target=reader)
        write_thread.start()
        read_thread.start()
        read_thread.join()
        stop_event.set()
        write_thread.join(timeout=2)

        assert errors == []


# ===========================================================================
# Singleton factory
# ===========================================================================


class TestSingletonFactory:
    """Tests for get_heartbeat_store() and reset_heartbeat_store()."""

    def test_get_returns_same_instance(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        store_a = get_heartbeat_store(storage_path=path)
        store_b = get_heartbeat_store()  # no path on second call
        assert store_a is store_b

    def test_reset_allows_new_singleton(self, tmp_path):
        path_a = tmp_path / "a" / "nodes.jsonl"
        path_b = tmp_path / "b" / "nodes.jsonl"
        store_a = get_heartbeat_store(storage_path=path_a)
        reset_heartbeat_store()
        store_b = get_heartbeat_store(storage_path=path_b)
        assert store_a is not store_b

    def test_singleton_path_ignored_on_second_call(self, tmp_path):
        path_a = tmp_path / "a" / "nodes.jsonl"
        path_b = tmp_path / "b" / "nodes.jsonl"
        store_a = get_heartbeat_store(storage_path=path_a)
        # Second call with a different path should be ignored
        store_b = get_heartbeat_store(storage_path=path_b)
        assert store_a is store_b
        assert store_b._path == path_a

    def test_default_path_used_when_no_path_given(self):
        """First call without storage_path should use the default constant."""
        from forge_harness.webhook_server.services.heartbeat_store import _DEFAULT_STORE_PATH

        store = get_heartbeat_store()
        assert store._path == _DEFAULT_STORE_PATH
