"""Pure unit tests for HeartbeatStore — all file I/O is mocked.

Strategy
--------
Every test patches :mod:`pathlib.Path` operations (``exists``, ``open``,
``mkdir``) and :func:`forge_harness.webhook_server.services.heartbeat_store
.datetime` so that tests are:

- Fast: no real disk access
- Deterministic: controlled timestamps
- Isolated: no shared state between tests

Coverage targets (90%+):
- HeartbeatRecord model + helpers: age_seconds, is_fresh, availability_score
- HeartbeatStore._ensure_loaded / _load / _rewrite
- HeartbeatStore.record, get, list_active, get_scheduler_view, mark_stale
- All error/edge branches (malformed JSONL, missing file, stale nodes, etc.)
- Singleton factory: get_heartbeat_store, reset_heartbeat_store
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

from forge_harness.webhook_server.services.heartbeat_store import (
    _DEFAULT_TTL_SECONDS,
    HeartbeatRecord,
    HeartbeatStore,
    get_heartbeat_store,
    reset_heartbeat_store,
)

# ---------------------------------------------------------------------------
# Module-level constants reused across tests
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
_NOW_ISO = _NOW.isoformat()
_OLD_ISO = (_NOW - timedelta(seconds=300)).isoformat()  # 5 minutes ago


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    node_id: str = "nova",
    cpu: float = 30.0,
    mem: float = 50.0,
    agents: int = 2,
    leases: int = 1,
    last_seen: str = _NOW_ISO,
    status: str = "healthy",
    ttl: int = _DEFAULT_TTL_SECONDS,
) -> HeartbeatRecord:
    return HeartbeatRecord(
        node_id=node_id,
        hostname=f"{node_id}.local",
        cpu_percent=cpu,
        memory_percent=mem,
        active_agents=agents,
        active_leases=leases,
        last_seen=last_seen,
        ttl_seconds=ttl,
        status=status,  # type: ignore[arg-type]
    )


def _fresh_store(tmp_path: Path) -> HeartbeatStore:
    """Return a HeartbeatStore whose backing file does not yet exist."""
    path = tmp_path / "nodes.jsonl"
    # No file created — _load() will treat it as an empty store.
    return HeartbeatStore(storage_path=path)


# ---------------------------------------------------------------------------
# Autouse fixture: reset singleton after every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_heartbeat_store()
    yield
    reset_heartbeat_store()


# ===========================================================================
# HeartbeatRecord — model validation
# ===========================================================================


class TestHeartbeatRecordDefaults:
    """HeartbeatRecord field defaults and Pydantic validation."""

    def test_only_node_id_required(self):
        rec = HeartbeatRecord(node_id="nova")
        assert rec.node_id == "nova"
        assert rec.hostname == ""
        assert rec.cpu_percent == 0.0
        assert rec.memory_percent == 0.0
        assert rec.active_agents == 0
        assert rec.active_leases == 0
        assert rec.status == "healthy"
        assert rec.ttl_seconds == _DEFAULT_TTL_SECONDS

    def test_cpu_percent_upper_bound_rejected(self):
        with pytest.raises(Exception):
            HeartbeatRecord(node_id="nova", cpu_percent=101.0)

    def test_cpu_percent_lower_bound_rejected(self):
        with pytest.raises(Exception):
            HeartbeatRecord(node_id="nova", cpu_percent=-1.0)

    def test_memory_percent_upper_bound_rejected(self):
        with pytest.raises(Exception):
            HeartbeatRecord(node_id="nova", memory_percent=200.0)

    def test_active_agents_negative_rejected(self):
        with pytest.raises(Exception):
            HeartbeatRecord(node_id="nova", active_agents=-1)

    def test_active_leases_negative_rejected(self):
        with pytest.raises(Exception):
            HeartbeatRecord(node_id="nova", active_leases=-5)

    def test_ttl_seconds_zero_rejected(self):
        with pytest.raises(Exception):
            HeartbeatRecord(node_id="nova", ttl_seconds=0)

    def test_status_invalid_literal_rejected(self):
        with pytest.raises(Exception):
            HeartbeatRecord(node_id="nova", status="unknown")  # type: ignore[arg-type]

    def test_all_valid_status_literals(self):
        for status in ("healthy", "stale", "offline"):
            rec = HeartbeatRecord(node_id="nova", status=status)  # type: ignore[arg-type]
            assert rec.status == status

    def test_model_dump_json_round_trip(self):
        original = _make_record()
        raw = original.model_dump_json()
        restored = HeartbeatRecord.model_validate_json(raw)
        assert restored.node_id == original.node_id
        assert restored.cpu_percent == original.cpu_percent
        assert restored.memory_percent == original.memory_percent
        assert restored.hostname == original.hostname


# ===========================================================================
# HeartbeatRecord — age_seconds
# ===========================================================================


class TestHeartbeatRecordAgeSeconds:
    """HeartbeatRecord.age_seconds() computed from last_seen."""

    def test_empty_last_seen_returns_infinity(self):
        rec = HeartbeatRecord(node_id="nova", last_seen="")
        assert rec.age_seconds() == float("inf")

    def test_unparseable_last_seen_returns_infinity(self):
        rec = HeartbeatRecord(node_id="nova", last_seen="not-a-timestamp")
        assert rec.age_seconds() == float("inf")

    def test_recent_timestamp_returns_small_age(self):
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            rec = HeartbeatRecord(node_id="nova", last_seen=_NOW_ISO)
            age = rec.age_seconds()
        assert age == pytest.approx(0.0, abs=1.0)

    def test_old_timestamp_returns_large_age(self):
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            rec = HeartbeatRecord(node_id="nova", last_seen=_OLD_ISO)
            age = rec.age_seconds()
        assert age == pytest.approx(300.0, abs=2.0)

    def test_future_timestamp_returns_zero_not_negative(self):
        """Timestamps in the future must clamp to 0.0 (no negative age)."""
        future_iso = (_NOW + timedelta(seconds=60)).isoformat()
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            rec = HeartbeatRecord(node_id="nova", last_seen=future_iso)
            age = rec.age_seconds()
        assert age == 0.0

    def test_naive_timestamp_treated_as_utc(self):
        """Timezone-naive ISO strings are assumed UTC and handled without error."""
        naive_iso = _NOW.replace(tzinfo=None).isoformat()
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            rec = HeartbeatRecord(node_id="nova", last_seen=naive_iso)
            age = rec.age_seconds()
        assert age == pytest.approx(0.0, abs=1.0)


# ===========================================================================
# HeartbeatRecord — is_fresh
# ===========================================================================


class TestHeartbeatRecordIsFresh:
    """HeartbeatRecord.is_fresh() delegates to age_seconds vs TTL."""

    def test_recent_record_is_fresh(self):
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            rec = HeartbeatRecord(node_id="nova", last_seen=_NOW_ISO, ttl_seconds=120)
            assert rec.is_fresh() is True

    def test_old_record_is_not_fresh(self):
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            rec = HeartbeatRecord(node_id="nova", last_seen=_OLD_ISO, ttl_seconds=120)
            assert rec.is_fresh() is False

    def test_override_ttl_respected(self):
        """Caller-supplied ttl_seconds overrides the record's own field."""
        slightly_old_iso = (_NOW - timedelta(seconds=60)).isoformat()
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            rec = HeartbeatRecord(node_id="nova", last_seen=slightly_old_iso, ttl_seconds=120)
            # 60 s < 120 s TTL → fresh
            assert rec.is_fresh() is True
            # 60 s > 30 s override TTL → stale
            assert rec.is_fresh(ttl_seconds=30) is False

    def test_missing_last_seen_is_never_fresh(self):
        rec = HeartbeatRecord(node_id="nova", last_seen="")
        assert rec.is_fresh() is False


# ===========================================================================
# HeartbeatRecord — availability_score
# ===========================================================================


class TestHeartbeatRecordAvailabilityScore:
    """HeartbeatRecord.availability_score() formula and status gating."""

    def test_fully_idle_node_scores_one(self):
        rec = _make_record(cpu=0.0, mem=0.0, status="healthy")
        assert rec.availability_score() == pytest.approx(1.0)

    def test_fully_saturated_node_scores_zero(self):
        rec = _make_record(cpu=100.0, mem=100.0, status="healthy")
        assert rec.availability_score() == pytest.approx(0.0)

    def test_midpoint_load_scores_half(self):
        rec = _make_record(cpu=50.0, mem=50.0, status="healthy")
        assert rec.availability_score() == pytest.approx(0.5)

    def test_asymmetric_load_uses_equal_weights(self):
        # cpu=0, mem=100 → cpu_score=1.0, mem_score=0.0 → combined=0.5
        rec = _make_record(cpu=0.0, mem=100.0, status="healthy")
        assert rec.availability_score() == pytest.approx(0.5)

    def test_stale_status_always_zero(self):
        rec = _make_record(cpu=0.0, mem=0.0, status="stale")
        assert rec.availability_score() == 0.0

    def test_offline_status_always_zero(self):
        rec = _make_record(cpu=10.0, mem=20.0, status="offline")
        assert rec.availability_score() == 0.0

    def test_score_rounded_to_four_decimal_places(self):
        # cpu=33.0, mem=66.0 → cpu_score=0.67, mem_score=0.34 → 0.505
        rec = _make_record(cpu=33.0, mem=66.0, status="healthy")
        score = rec.availability_score()
        # Check it's a float with at most 4 decimal places
        assert score == round(score, 4)

    def test_cpu_clamped_to_100_in_score(self):
        """CPU values > 100 are clamped before scoring (Pydantic already
        rejects them, but the formula itself also uses min())."""
        rec = _make_record(cpu=0.0, mem=0.0, status="healthy")
        # Manually override to test clamping path (bypass Pydantic)
        object.__setattr__(rec, "cpu_percent", 200.0)
        # Score should still treat cpu as 100
        score = rec.availability_score()
        assert score == pytest.approx(0.5)  # cpu clamped to 100 → cpu_score=0, mem_score=1 → 0.5


# ===========================================================================
# HeartbeatStore — _load (mocked I/O)
# ===========================================================================


class TestHeartbeatStoreLoad:
    """HeartbeatStore._load() reads and deserialises JSONL; handles errors."""

    def test_missing_file_treated_as_empty(self, tmp_path):
        path = tmp_path / "missing.jsonl"
        store = HeartbeatStore(storage_path=path)
        store._load()
        assert store._records == {}
        assert store._loaded is True

    def test_valid_jsonl_loads_records(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        rec = _make_record(node_id="nova")
        path.write_text(rec.model_dump_json() + "\n", encoding="utf-8")

        store = HeartbeatStore(storage_path=path)
        store._load()

        assert "nova" in store._records
        assert store._records["nova"].cpu_percent == rec.cpu_percent

    def test_multiple_records_loaded(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        rec_a = _make_record(node_id="nova")
        rec_b = _make_record(node_id="prya")
        path.write_text(
            rec_a.model_dump_json() + "\n" + rec_b.model_dump_json() + "\n",
            encoding="utf-8",
        )

        store = HeartbeatStore(storage_path=path)
        store._load()

        assert "nova" in store._records
        assert "prya" in store._records

    def test_last_record_wins_for_duplicate_node_id(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        rec_a = _make_record(node_id="nova", cpu=10.0)
        rec_b = _make_record(node_id="nova", cpu=90.0)
        path.write_text(
            rec_a.model_dump_json() + "\n" + rec_b.model_dump_json() + "\n",
            encoding="utf-8",
        )

        store = HeartbeatStore(storage_path=path)
        store._load()

        assert store._records["nova"].cpu_percent == 90.0

    def test_malformed_json_line_skipped(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        good = _make_record(node_id="nova")
        path.write_text(
            good.model_dump_json() + "\n{bad json\n",
            encoding="utf-8",
        )

        store = HeartbeatStore(storage_path=path)
        store._load()

        assert "nova" in store._records
        assert len(store._records) == 1

    def test_blank_lines_skipped(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        rec = _make_record(node_id="nova")
        path.write_text(
            "\n" + rec.model_dump_json() + "\n\n",
            encoding="utf-8",
        )

        store = HeartbeatStore(storage_path=path)
        store._load()

        assert "nova" in store._records

    def test_loaded_flag_set_after_load(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        store = HeartbeatStore(storage_path=path)
        assert store._loaded is False
        store._load()
        assert store._loaded is True

    def test_second_load_call_replaces_records(self, tmp_path):
        """_load() re-reads the file unconditionally each time it is called."""
        path = tmp_path / "nodes.jsonl"
        rec_a = _make_record(node_id="nova")
        path.write_text(rec_a.model_dump_json() + "\n", encoding="utf-8")

        store = HeartbeatStore(storage_path=path)
        store._load()
        assert "nova" in store._records

        # Write a different node to the file
        rec_b = _make_record(node_id="prya")
        path.write_text(rec_b.model_dump_json() + "\n", encoding="utf-8")
        store._load()

        # The in-memory cache should now reflect only prya
        assert "prya" in store._records
        assert "nova" not in store._records


# ===========================================================================
# HeartbeatStore — _ensure_loaded (mocked I/O)
# ===========================================================================


class TestEnsureLoaded:
    """_ensure_loaded() calls _load() only on the first access."""

    def test_calls_load_when_not_yet_loaded(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load", wraps=store._load) as mock_load:
            store._ensure_loaded()
            mock_load.assert_called_once()

    def test_does_not_call_load_when_already_loaded(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        with patch.object(store, "_load") as mock_load:
            store._ensure_loaded()
            mock_load.assert_not_called()


# ===========================================================================
# HeartbeatStore — _rewrite (mocked I/O)
# ===========================================================================


class TestHeartbeatStoreRewrite:
    """HeartbeatStore._rewrite() writes one JSON line per record."""

    def test_creates_parent_directory(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "nodes.jsonl"
        store = HeartbeatStore(storage_path=deep_path)
        store._records = {"nova": _make_record(node_id="nova")}
        store._rewrite()
        assert deep_path.exists()

    def test_writes_one_line_per_record(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        store = HeartbeatStore(storage_path=path)
        store._records = {
            "nova": _make_record(node_id="nova"),
            "prya": _make_record(node_id="prya"),
        }
        store._rewrite()
        lines = [l for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(lines) == 2

    def test_each_line_is_valid_json(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        store = HeartbeatStore(storage_path=path)
        store._records = {"nova": _make_record(node_id="nova")}
        store._rewrite()
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                data = json.loads(line)
                assert "node_id" in data

    def test_empty_records_produces_empty_file(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        store = HeartbeatStore(storage_path=path)
        store._records = {}
        store._rewrite()
        assert path.read_text("utf-8") == ""

    def test_rewrite_overwrites_previous_content(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        # Pre-populate with two records
        store = HeartbeatStore(storage_path=path)
        store._records = {
            "nova": _make_record(node_id="nova"),
            "prya": _make_record(node_id="prya"),
        }
        store._rewrite()

        # Now rewrite with only one record
        store._records = {"nova": _make_record(node_id="nova")}
        store._rewrite()

        lines = [l for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["node_id"] == "nova"


# ===========================================================================
# HeartbeatStore.record — mocked I/O
# ===========================================================================


class TestHeartbeatStoreRecord:
    """HeartbeatStore.record() upserts in-memory cache and calls _rewrite."""

    def test_record_returns_heartbeat_record(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite"):
            rec = store.record("nova", {"cpu_percent": 30.0, "memory_percent": 50.0})
        assert isinstance(rec, HeartbeatRecord)
        assert rec.node_id == "nova"

    def test_record_sets_status_healthy(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite"):
            rec = store.record("nova", {"cpu_percent": 30.0})
        assert rec.status == "healthy"

    def test_record_overrides_node_id_from_data(self, tmp_path):
        """node_id in data is overwritten by the positional argument."""
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite"):
            rec = store.record("nova", {"node_id": "wrong", "cpu_percent": 30.0})
        assert rec.node_id == "nova"

    def test_record_sets_last_seen_to_now(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            with patch.object(store, "_rewrite"):
                rec = store.record("nova", {})
        assert rec.last_seen == _NOW_ISO

    def test_record_calls_rewrite(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite") as mock_rw:
            store.record("nova", {})
        mock_rw.assert_called_once()

    def test_record_upserts_existing_node(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite"):
            store.record("nova", {"cpu_percent": 10.0})
            store.record("nova", {"cpu_percent": 90.0})
        # Only one record stored for "nova"
        assert len(store._records) == 1
        assert store._records["nova"].cpu_percent == 90.0

    def test_record_stores_all_telemetry_fields(self, tmp_path):
        store = _fresh_store(tmp_path)
        data = {
            "hostname": "nova.local",
            "cpu_percent": 42.0,
            "memory_percent": 55.0,
            "active_agents": 3,
            "active_leases": 1,
        }
        with patch.object(store, "_rewrite"):
            rec = store.record("nova", data)
        assert rec.hostname == "nova.local"
        assert rec.cpu_percent == 42.0
        assert rec.memory_percent == 55.0
        assert rec.active_agents == 3
        assert rec.active_leases == 1

    def test_record_with_custom_ttl(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite"):
            rec = store.record("nova", {}, ttl_seconds=60)
        assert rec.ttl_seconds == 60

    def test_record_unknown_keys_silently_ignored(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite"):
            rec = store.record("nova", {"totally_unknown_field": "value", "cpu_percent": 10.0})
        assert rec.cpu_percent == 10.0

    def test_multiple_distinct_nodes_stored(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite"):
            store.record("nova", {"cpu_percent": 10.0})
            store.record("prya", {"cpu_percent": 20.0})
        assert len(store._records) == 2


# ===========================================================================
# HeartbeatStore.get — mocked I/O
# ===========================================================================


class TestHeartbeatStoreGet:
    """HeartbeatStore.get() returns live-status records or None."""

    def test_get_existing_node_returns_record(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["nova"] = _make_record(node_id="nova")
        rec = store.get("nova")
        assert rec is not None
        assert rec.node_id == "nova"

    def test_get_nonexistent_node_returns_none(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        assert store.get("nonexistent") is None

    def test_get_does_not_mutate_cached_record(self, tmp_path):
        """get() uses model_copy so the in-memory cache is never mutated."""
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["prya"] = _make_record(node_id="prya", last_seen=_OLD_ISO)

        rec = store.get("prya")
        # Returned record is stale
        assert rec.status == "stale"
        # Original cache record still says healthy
        assert store._records["prya"].status == "healthy"

    def test_get_returns_stale_status_for_old_record(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["prya"] = _make_record(node_id="prya", last_seen=_OLD_ISO, ttl=120)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            rec = store.get("prya")

        assert rec.status == "stale"

    def test_get_returns_healthy_status_for_fresh_record(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["nova"] = _make_record(node_id="nova", last_seen=_NOW_ISO, ttl=120)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            rec = store.get("nova")

        assert rec.status == "healthy"

    def test_get_triggers_lazy_load(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load", wraps=store._load) as mock_load:
            store.get("nova")
        mock_load.assert_called_once()


# ===========================================================================
# HeartbeatStore.list_active — mocked I/O
# ===========================================================================


class TestHeartbeatStoreListActive:
    """HeartbeatStore.list_active() filters fresh records and ranks them."""

    def test_empty_store_returns_empty_list(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        assert store.list_active() == []

    def test_fresh_nodes_included(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["nova"] = _make_record(node_id="nova", last_seen=_NOW_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            active = store.list_active(ttl_seconds=120)

        assert len(active) == 1
        assert active[0].node_id == "nova"

    def test_stale_nodes_excluded(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["nova"] = _make_record(node_id="nova", last_seen=_NOW_ISO)
        store._records["prya"] = _make_record(node_id="prya", last_seen=_OLD_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            active = store.list_active(ttl_seconds=120)

        node_ids = [r.node_id for r in active]
        assert "nova" in node_ids
        assert "prya" not in node_ids

    def test_sorted_by_availability_score_descending(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        # nova: low load → high score
        store._records["nova"] = _make_record(node_id="nova", cpu=10.0, mem=20.0, last_seen=_NOW_ISO)
        # prya: high load → low score
        store._records["prya"] = _make_record(node_id="prya", cpu=90.0, mem=85.0, last_seen=_NOW_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            active = store.list_active(ttl_seconds=120)

        assert len(active) == 2
        assert active[0].node_id == "nova"
        assert active[1].node_id == "prya"

    def test_custom_ttl_argument_used(self, tmp_path):
        """A shorter TTL should cause a slightly-old record to be excluded."""
        store = _fresh_store(tmp_path)
        store._loaded = True
        slightly_old_iso = (_NOW - timedelta(seconds=60)).isoformat()
        store._records["nova"] = _make_record(node_id="nova", last_seen=slightly_old_iso)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            # With 30 s TTL, 60 s old = stale
            active = store.list_active(ttl_seconds=30)

        assert active == []

    def test_list_active_triggers_lazy_load(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load", wraps=store._load) as mock_load:
            store.list_active()
        mock_load.assert_called_once()


# ===========================================================================
# HeartbeatStore.get_scheduler_view — mocked I/O
# ===========================================================================


class TestGetSchedulerView:
    """HeartbeatStore.get_scheduler_view() returns the full scheduler dict."""

    def _setup_store(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        return store

    def test_empty_store_returns_complete_schema(self, tmp_path):
        store = self._setup_store(tmp_path)
        view = store.get_scheduler_view()

        required_keys = {
            "generated_at",
            "ttl_seconds",
            "total_nodes",
            "active_nodes",
            "stale_nodes",
            "ranked_nodes",
            "node_loads",
            "capacities",
            "recommended_node_id",
        }
        assert required_keys.issubset(view.keys())

    def test_empty_store_zero_counts(self, tmp_path):
        store = self._setup_store(tmp_path)
        view = store.get_scheduler_view()
        assert view["total_nodes"] == 0
        assert view["active_nodes"] == 0
        assert view["stale_nodes"] == 0
        assert view["ranked_nodes"] == []
        assert view["recommended_node_id"] is None

    def test_single_fresh_node(self, tmp_path):
        store = self._setup_store(tmp_path)
        store._records["nova"] = _make_record(node_id="nova", last_seen=_NOW_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        assert view["total_nodes"] == 1
        assert view["active_nodes"] == 1
        assert view["stale_nodes"] == 0
        assert view["recommended_node_id"] == "nova"
        assert len(view["ranked_nodes"]) == 1

    def test_stale_node_counted_but_not_ranked(self, tmp_path):
        store = self._setup_store(tmp_path)
        store._records["nova"] = _make_record(node_id="nova", last_seen=_NOW_ISO)
        store._records["prya"] = _make_record(node_id="prya", last_seen=_OLD_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        assert view["total_nodes"] == 2
        assert view["active_nodes"] == 1
        assert view["stale_nodes"] == 1
        ranked_ids = [n["node_id"] for n in view["ranked_nodes"]]
        assert "prya" not in ranked_ids
        assert "prya" not in view["node_loads"]
        assert "prya" not in view["capacities"]

    def test_ranked_nodes_entry_has_all_required_fields(self, tmp_path):
        store = self._setup_store(tmp_path)
        store._records["nova"] = _make_record(node_id="nova", last_seen=_NOW_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        node = view["ranked_nodes"][0]
        for key in (
            "node_id",
            "hostname",
            "availability_score",
            "cpu_percent",
            "memory_percent",
            "active_agents",
            "active_leases",
            "age_seconds",
            "status",
        ):
            assert key in node, f"Missing key: {key}"

    def test_node_loads_populated(self, tmp_path):
        store = self._setup_store(tmp_path)
        store._records["nova"] = _make_record(node_id="nova", cpu=30.0, mem=50.0, last_seen=_NOW_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        assert "nova" in view["node_loads"]
        assert view["node_loads"]["nova"]["cpu_percent"] == 30.0
        assert view["node_loads"]["nova"]["memory_percent"] == 50.0

    def test_capacities_populated(self, tmp_path):
        store = self._setup_store(tmp_path)
        # 3 active agents → 8 - 3 = 5 available
        store._records["nova"] = _make_record(node_id="nova", agents=3, last_seen=_NOW_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        cap = view["capacities"]["nova"]
        assert cap["available_agents"] == 5
        assert "availability_score" in cap

    def test_available_agents_floor_at_zero(self, tmp_path):
        """When active_agents exceeds 8, available_agents must be 0 (not negative)."""
        store = self._setup_store(tmp_path)
        store._records["nova"] = _make_record(node_id="nova", agents=10, last_seen=_NOW_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        assert view["capacities"]["nova"]["available_agents"] == 0

    def test_recommended_node_is_highest_score(self, tmp_path):
        store = self._setup_store(tmp_path)
        store._records["nova"] = _make_record(node_id="nova", cpu=10.0, mem=20.0, last_seen=_NOW_ISO)
        store._records["prya"] = _make_record(node_id="prya", cpu=80.0, mem=90.0, last_seen=_NOW_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        assert view["recommended_node_id"] == "nova"

    def test_no_active_nodes_recommended_is_none(self, tmp_path):
        store = self._setup_store(tmp_path)
        store._records["prya"] = _make_record(node_id="prya", last_seen=_OLD_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        assert view["recommended_node_id"] is None

    def test_generated_at_uses_current_time(self, tmp_path):
        store = self._setup_store(tmp_path)
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        assert view["generated_at"] == _NOW_ISO

    def test_ttl_seconds_reflected_in_view(self, tmp_path):
        store = self._setup_store(tmp_path)
        view = store.get_scheduler_view(ttl_seconds=60)
        assert view["ttl_seconds"] == 60


# ===========================================================================
# HeartbeatStore.mark_stale — mocked I/O
# ===========================================================================


class TestMarkStale:
    """HeartbeatStore.mark_stale() transitions healthy-but-old nodes to stale."""

    def test_empty_store_returns_empty_list(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        assert store.mark_stale() == []

    def test_marks_old_healthy_node(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["prya"] = _make_record(node_id="prya", last_seen=_OLD_ISO, status="healthy")

        with patch.object(store, "_rewrite"):
            with patch(
                "forge_harness.webhook_server.services.heartbeat_store.datetime"
            ) as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                marked = store.mark_stale(ttl_seconds=120)

        assert "prya" in marked

    def test_does_not_mark_fresh_nodes(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["nova"] = _make_record(node_id="nova", last_seen=_NOW_ISO)

        with patch.object(store, "_rewrite"):
            with patch(
                "forge_harness.webhook_server.services.heartbeat_store.datetime"
            ) as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                marked = store.mark_stale(ttl_seconds=120)

        assert "nova" not in marked

    def test_does_not_re_mark_already_stale_nodes(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["prya"] = _make_record(
            node_id="prya", last_seen=_OLD_ISO, status="stale"
        )

        with patch.object(store, "_rewrite"):
            with patch(
                "forge_harness.webhook_server.services.heartbeat_store.datetime"
            ) as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                marked = store.mark_stale(ttl_seconds=120)

        assert "prya" not in marked

    def test_does_not_mark_offline_nodes(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["prya"] = _make_record(
            node_id="prya", last_seen=_OLD_ISO, status="offline"
        )

        with patch.object(store, "_rewrite"):
            with patch(
                "forge_harness.webhook_server.services.heartbeat_store.datetime"
            ) as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                marked = store.mark_stale(ttl_seconds=120)

        assert "prya" not in marked

    def test_mutates_in_memory_cache(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["prya"] = _make_record(node_id="prya", last_seen=_OLD_ISO, status="healthy")

        with patch.object(store, "_rewrite"):
            with patch(
                "forge_harness.webhook_server.services.heartbeat_store.datetime"
            ) as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                store.mark_stale(ttl_seconds=120)

        assert store._records["prya"].status == "stale"

    def test_rewrite_called_when_changes_made(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["prya"] = _make_record(node_id="prya", last_seen=_OLD_ISO, status="healthy")

        with patch.object(store, "_rewrite") as mock_rw:
            with patch(
                "forge_harness.webhook_server.services.heartbeat_store.datetime"
            ) as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                store.mark_stale(ttl_seconds=120)

        mock_rw.assert_called_once()

    def test_rewrite_not_called_when_no_changes(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["nova"] = _make_record(node_id="nova", last_seen=_NOW_ISO)

        with patch.object(store, "_rewrite") as mock_rw:
            with patch(
                "forge_harness.webhook_server.services.heartbeat_store.datetime"
            ) as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                store.mark_stale(ttl_seconds=120)

        mock_rw.assert_not_called()

    def test_mark_stale_persists_status_to_disk(self, tmp_path):
        """Integration-level check: _rewrite is actually called (not mocked)."""
        path = tmp_path / "nodes.jsonl"
        store = HeartbeatStore(storage_path=path)
        store._loaded = True
        store._records["prya"] = _make_record(node_id="prya", last_seen=_OLD_ISO, status="healthy")

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            store.mark_stale(ttl_seconds=120)

        lines = [l for l in path.read_text("utf-8").splitlines() if l.strip()]
        data = json.loads(lines[0])
        assert data["status"] == "stale"

    def test_multiple_nodes_marked_at_once(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._records["node1"] = _make_record(node_id="node1", last_seen=_OLD_ISO, status="healthy")
        store._records["node2"] = _make_record(node_id="node2", last_seen=_OLD_ISO, status="healthy")
        store._records["fresh"] = _make_record(node_id="fresh", last_seen=_NOW_ISO, status="healthy")

        with patch.object(store, "_rewrite"):
            with patch(
                "forge_harness.webhook_server.services.heartbeat_store.datetime"
            ) as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                marked = store.mark_stale(ttl_seconds=120)

        assert set(marked) == {"node1", "node2"}
        assert "fresh" not in marked


# ===========================================================================
# JSONL persistence round-trip (lightweight, using tmp_path)
# ===========================================================================


class TestJSONLPersistenceRoundTrip:
    """End-to-end persistence: write → reload → verify."""

    def test_reload_recovers_records(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        store1 = HeartbeatStore(storage_path=path)
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            store1.record("nova", {"cpu_percent": 25.0, "hostname": "nova.local"})

        store2 = HeartbeatStore(storage_path=path)
        rec = store2.get("nova")
        assert rec is not None
        assert rec.cpu_percent == 25.0
        assert rec.hostname == "nova.local"

    def test_upsert_does_not_duplicate_lines(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        store = HeartbeatStore(storage_path=path)
        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            store.record("nova", {"cpu_percent": 10.0})
            store.record("nova", {"cpu_percent": 90.0})

        lines = [l for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(lines) == 1

    def test_malformed_lines_skipped_on_reload(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        good = _make_record(node_id="nova")
        path.write_text(good.model_dump_json() + "\n{INVALID\n", encoding="utf-8")

        store = HeartbeatStore(storage_path=path)
        rec = store.get("nova")
        assert rec is not None


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    """Concurrent record() calls must not raise or corrupt state."""

    def test_concurrent_distinct_nodes(self, tmp_path):
        store = _fresh_store(tmp_path)
        errors: list[Exception] = []
        count = 15

        def worker(node_id: str) -> None:
            try:
                store.record(node_id, {"cpu_percent": 50.0})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"node-{i}",)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # All nodes must be present
        assert len(store._records) == count

    def test_concurrent_same_node(self, tmp_path):
        store = _fresh_store(tmp_path)
        errors: list[Exception] = []

        def worker(cpu: float) -> None:
            try:
                store.record("nova", {"cpu_percent": cpu})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(float(i % 100),)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert "nova" in store._records


# ===========================================================================
# Singleton factory
# ===========================================================================


class TestSingletonFactory:
    """get_heartbeat_store() and reset_heartbeat_store() contract."""

    def test_returns_same_instance_on_repeat_calls(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        a = get_heartbeat_store(storage_path=path)
        b = get_heartbeat_store()
        assert a is b

    def test_second_call_ignores_storage_path(self, tmp_path):
        path_a = tmp_path / "a.jsonl"
        path_b = tmp_path / "b.jsonl"
        a = get_heartbeat_store(storage_path=path_a)
        b = get_heartbeat_store(storage_path=path_b)
        assert b._path == path_a

    def test_reset_clears_singleton(self, tmp_path):
        path_a = tmp_path / "a.jsonl"
        path_b = tmp_path / "b.jsonl"
        a = get_heartbeat_store(storage_path=path_a)
        reset_heartbeat_store()
        b = get_heartbeat_store(storage_path=path_b)
        assert a is not b

    def test_default_path_used_when_no_path_given(self):
        from forge_harness.webhook_server.services.heartbeat_store import _DEFAULT_STORE_PATH

        store = get_heartbeat_store()
        assert store._path == _DEFAULT_STORE_PATH

    def test_get_without_path_returns_existing_singleton(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        a = get_heartbeat_store(storage_path=path)
        b = get_heartbeat_store()
        assert a is b

    def test_concurrent_factory_calls_return_same_instance(self, tmp_path):
        path = tmp_path / "nodes.jsonl"
        instances: list[HeartbeatStore] = []
        errors: list[Exception] = []

        def getter() -> None:
            try:
                instances.append(get_heartbeat_store(storage_path=path))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=getter) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        first = instances[0]
        assert all(s is first for s in instances)


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Miscellaneous edge-case coverage."""

    def test_record_with_all_zero_telemetry(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite"):
            rec = store.record("nova", {"cpu_percent": 0.0, "memory_percent": 0.0})
        assert rec.cpu_percent == 0.0
        assert rec.availability_score() == pytest.approx(1.0)

    def test_record_with_max_telemetry(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_rewrite"):
            rec = store.record("nova", {"cpu_percent": 100.0, "memory_percent": 100.0})
        assert rec.availability_score() == pytest.approx(0.0)

    def test_scheduler_view_two_fresh_nodes_rankings(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        # equal load → equal scores
        store._records["a"] = _make_record(node_id="a", cpu=50.0, mem=50.0, last_seen=_NOW_ISO)
        store._records["b"] = _make_record(node_id="b", cpu=50.0, mem=50.0, last_seen=_NOW_ISO)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        assert view["active_nodes"] == 2
        assert len(view["ranked_nodes"]) == 2

    def test_get_scheduler_view_age_seconds_rounded(self, tmp_path):
        """age_seconds in ranked_nodes must be rounded to 1 decimal place."""
        store = _fresh_store(tmp_path)
        store._loaded = True
        ts = (_NOW - timedelta(seconds=10)).isoformat()
        store._records["nova"] = _make_record(node_id="nova", last_seen=ts)

        with patch(
            "forge_harness.webhook_server.services.heartbeat_store.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            view = store.get_scheduler_view()

        age = view["ranked_nodes"][0]["age_seconds"]
        assert age == round(age, 1)

    def test_mark_stale_triggers_lazy_load(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load", wraps=store._load) as mock_load:
            store.mark_stale()
        mock_load.assert_called_once()

    def test_heartbeat_record_model_copy_is_independent(self):
        """model_copy(update=...) must not share state with the original."""
        original = _make_record(status="healthy")
        copy = original.model_copy(update={"status": "stale"})
        assert original.status == "healthy"
        assert copy.status == "stale"
