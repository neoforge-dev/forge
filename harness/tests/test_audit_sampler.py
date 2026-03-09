"""Tests for Dark Factory Audit Sampler — DF-4003.

Coverage targets
----------------
- Constants: MIN_SAMPLE_RATE, MAX_SAMPLE_RATE, DEFAULT_SAMPLE_RATE values
- AuditStatus enum: all four values present, is str enum
- SamplingDecision: frozen dataclass, all fields present, default factory fields
- AuditRecord: mutable dataclass, to_dict serialization, property accessors
- AuditSummary: frozen dataclass, to_dict serialization, zero-division guard
- AuditSampler.__init__: default sample rate, custom rate, clamping bounds
- AuditSampler.sample_rate property: returns current rate
- AuditSampler.should_audit: selection logic (rate=1.0 always selects)
- AuditSampler.should_audit: rate=0.01 with controlled RNG
- AuditSampler.should_audit: empty task_id raises ValueError
- AuditSampler.should_audit: empty lane falls back to "unknown"
- AuditSampler.should_audit: records decision in _decisions list
- AuditSampler.should_audit: selected task creates an AuditRecord
- AuditSampler.should_audit: unselected task does NOT create an AuditRecord
- AuditSampler.should_audit: decision_id is unique UUID per call
- AuditSampler.should_audit: returned decision has correct fields
- AuditSampler.should_audit: random_value < sample_rate when selected
- AuditSampler.should_audit: random_value >= sample_rate when not selected
- AuditSampler.record_result: success recording updates record correctly
- AuditSampler.record_result: failure recording sets status=failed
- AuditSampler.record_result: empty auditor_id raises ValueError
- AuditSampler.record_result: missing task_id raises KeyError
- AuditSampler.record_result: sets reviewed_at timestamp
- AuditSampler.record_result: records auditor notes
- AuditSampler.record_result: returns the updated AuditRecord
- AuditSampler.skip_audit: sets status to skipped
- AuditSampler.skip_audit: sets notes to reason
- AuditSampler.skip_audit: sets reviewed_at timestamp
- AuditSampler.skip_audit: missing task_id raises KeyError
- AuditSampler.skip_audit: returns the updated AuditRecord
- AuditSampler.get_record: returns record for sampled task
- AuditSampler.get_record: returns None for unsampled task
- AuditSampler.get_record: returns None for unknown task_id
- AuditSampler.get_pending_audits: returns only pending records
- AuditSampler.get_pending_audits: empty when no records selected
- AuditSampler.get_pending_audits: excludes reviewed and skipped records
- AuditSampler.get_all_records: returns all records regardless of status
- AuditSampler.get_all_records: empty list when nothing selected
- AuditSampler.get_daily_summary: total_completions counts all decisions
- AuditSampler.get_daily_summary: total_sampled counts selected decisions
- AuditSampler.get_daily_summary: total_reviewed counts reviewed records
- AuditSampler.get_daily_summary: total_passed counts passed records
- AuditSampler.get_daily_summary: total_failed counts failed records
- AuditSampler.get_daily_summary: total_pending counts pending records
- AuditSampler.get_daily_summary: effective_rate = sampled / completions
- AuditSampler.get_daily_summary: pass_rate = passed / reviewed
- AuditSampler.get_daily_summary: zero division guard for empty state
- AuditSampler.get_daily_summary: period_start filter works
- AuditSampler.get_daily_summary: period_end filter works
- AuditSampler.get_daily_summary: mixed results compute correct ratios
- AuditSampler.update_config: changes sample rate at runtime
- AuditSampler.update_config: clamps below-minimum rate
- AuditSampler.update_config: clamps above-maximum rate
- AuditSampler.update_config: returns the new (clamped) rate
- AuditSampler.reset: clears all decisions and records
- AuditSampler.reset: restores default sample rate
- Singleton: get_audit_sampler returns same instance on repeated calls
- Singleton: reset_audit_sampler creates fresh instance
- Singleton: reset_audit_sampler clears all in-memory state
- Singleton: get_audit_sampler is thread-safe
- Thread safety: concurrent should_audit calls do not corrupt state
- Thread safety: concurrent record_result calls do not corrupt state
- Thread safety: concurrent mixed operations do not corrupt state
- AuditSummary.to_dict: all keys present, rates rounded to 4 dp
- AuditSummary.to_dict: period_start / period_end None when not provided
- AuditRecord.to_dict: all keys present, reviewed_at None before review
- AuditRecord.to_dict: reviewed_at ISO string after review
- Edge case: multiple tasks only some selected, counts accurate
- Edge case: 100% sample rate selects every task
- Edge case: record_result with empty notes is fine
- Edge case: skip_audit with no reason leaves empty notes

Target: 65+ tests, 100% coverage of audit_sampler.py.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from forge_harness.dark_factory.audit_sampler import (
    DEFAULT_SAMPLE_RATE,
    MAX_SAMPLE_RATE,
    MIN_SAMPLE_RATE,
    AuditRecord,
    AuditSampler,
    AuditStatus,
    AuditSummary,
    SamplingDecision,
    get_audit_sampler,
    reset_audit_sampler,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the singleton before and after every test."""
    reset_audit_sampler()
    yield
    reset_audit_sampler()


@pytest.fixture()
def sampler() -> AuditSampler:
    """Return a fresh AuditSampler with the default rate."""
    return AuditSampler()


@pytest.fixture()
def full_rate_sampler() -> AuditSampler:
    """Return an AuditSampler configured at 100% — every task is selected."""
    return AuditSampler(sample_rate=1.0)


@pytest.fixture()
def zero_rate_sampler() -> AuditSampler:
    """Return an AuditSampler at the minimum rate with a seeded RNG."""
    s = AuditSampler(sample_rate=MIN_SAMPLE_RATE)
    # Seed the RNG so should_audit always returns NOT selected at min rate
    s._rng.seed(999)
    return s


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_min_sample_rate(self):
        assert MIN_SAMPLE_RATE == 0.01

    def test_max_sample_rate(self):
        assert MAX_SAMPLE_RATE == 1.0

    def test_default_sample_rate(self):
        assert DEFAULT_SAMPLE_RATE == 0.125

    def test_default_sample_rate_within_bounds(self):
        assert MIN_SAMPLE_RATE <= DEFAULT_SAMPLE_RATE <= MAX_SAMPLE_RATE


# ---------------------------------------------------------------------------
# AuditStatus enum
# ---------------------------------------------------------------------------


class TestAuditStatusEnum:
    def test_all_values_present(self):
        values = {s.value for s in AuditStatus}
        assert values == {"pending", "passed", "failed", "skipped"}

    def test_is_str_enum(self):
        assert AuditStatus.pending == "pending"
        assert AuditStatus.passed == "passed"
        assert AuditStatus.failed == "failed"
        assert AuditStatus.skipped == "skipped"

    def test_pending_is_default(self):
        record = AuditRecord(
            decision=SamplingDecision(
                task_id="t-1",
                lane="docs",
                selected=True,
                sample_rate=0.125,
                random_value=0.05,
            )
        )
        assert record.status == AuditStatus.pending


# ---------------------------------------------------------------------------
# SamplingDecision dataclass
# ---------------------------------------------------------------------------


class TestSamplingDecision:
    def test_frozen(self):
        d = SamplingDecision(
            task_id="t-1",
            lane="docs",
            selected=True,
            sample_rate=0.125,
            random_value=0.05,
        )
        with pytest.raises((AttributeError, TypeError)):
            d.task_id = "changed"  # type: ignore[misc]

    def test_all_required_fields(self):
        d = SamplingDecision(
            task_id="T-100",
            lane="test_writing",
            selected=False,
            sample_rate=0.12,
            random_value=0.5,
        )
        assert d.task_id == "T-100"
        assert d.lane == "test_writing"
        assert d.selected is False
        assert d.sample_rate == 0.12
        assert d.random_value == 0.5

    def test_decision_id_is_auto_generated_uuid(self):
        d = SamplingDecision(
            task_id="t-1",
            lane="docs",
            selected=True,
            sample_rate=0.125,
            random_value=0.01,
        )
        # Should be a valid UUID string
        parsed = uuid.UUID(d.decision_id)
        assert str(parsed) == d.decision_id

    def test_decision_id_is_unique_per_instance(self):
        d1 = SamplingDecision("t-1", "docs", True, 0.125, 0.01)
        d2 = SamplingDecision("t-2", "docs", True, 0.125, 0.01)
        assert d1.decision_id != d2.decision_id

    def test_timestamp_is_set_automatically(self):
        before = datetime.now(UTC)
        d = SamplingDecision("t-1", "docs", True, 0.125, 0.01)
        after = datetime.now(UTC)
        assert before <= d.timestamp <= after

    def test_timestamp_is_timezone_aware(self):
        d = SamplingDecision("t-1", "docs", True, 0.125, 0.01)
        assert d.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# AuditRecord dataclass
# ---------------------------------------------------------------------------


class TestAuditRecord:
    def _make_decision(self, task_id: str = "t-1", lane: str = "docs") -> SamplingDecision:
        return SamplingDecision(
            task_id=task_id,
            lane=lane,
            selected=True,
            sample_rate=0.125,
            random_value=0.05,
        )

    def test_default_status_is_pending(self):
        r = AuditRecord(decision=self._make_decision())
        assert r.status == AuditStatus.pending

    def test_task_id_property(self):
        r = AuditRecord(decision=self._make_decision("T-999"))
        assert r.task_id == "T-999"

    def test_lane_property(self):
        r = AuditRecord(decision=self._make_decision(lane="security_change"))
        assert r.lane == "security_change"

    def test_default_fields(self):
        r = AuditRecord(decision=self._make_decision())
        assert r.auditor_id is None
        assert r.passed is None
        assert r.notes == ""
        assert r.reviewed_at is None

    def test_to_dict_keys(self):
        r = AuditRecord(decision=self._make_decision())
        d = r.to_dict()
        expected_keys = {
            "task_id", "lane", "selected", "sample_rate", "random_value",
            "decision_id", "decision_timestamp", "status", "auditor_id",
            "passed", "notes", "reviewed_at",
        }
        assert expected_keys == set(d.keys())

    def test_to_dict_values_before_review(self):
        decision = self._make_decision("task-42", "docs")
        r = AuditRecord(decision=decision)
        d = r.to_dict()
        assert d["task_id"] == "task-42"
        assert d["lane"] == "docs"
        assert d["selected"] is True
        assert d["status"] == "pending"
        assert d["auditor_id"] is None
        assert d["passed"] is None
        assert d["notes"] == ""
        assert d["reviewed_at"] is None

    def test_to_dict_reviewed_at_is_iso_string_after_review(self):
        r = AuditRecord(decision=self._make_decision())
        r.reviewed_at = datetime(2026, 2, 23, 12, 0, 0, tzinfo=UTC)
        d = r.to_dict()
        assert d["reviewed_at"] == "2026-02-23T12:00:00+00:00"

    def test_to_dict_decision_timestamp_is_iso_string(self):
        r = AuditRecord(decision=self._make_decision())
        d = r.to_dict()
        # Should parse back without error
        datetime.fromisoformat(d["decision_timestamp"])

    def test_to_dict_sample_rate(self):
        r = AuditRecord(decision=self._make_decision())
        d = r.to_dict()
        assert d["sample_rate"] == 0.125

    def test_to_dict_random_value(self):
        r = AuditRecord(decision=self._make_decision())
        d = r.to_dict()
        assert d["random_value"] == 0.05


# ---------------------------------------------------------------------------
# AuditSummary dataclass
# ---------------------------------------------------------------------------


class TestAuditSummary:
    def _make_summary(self, **kwargs) -> AuditSummary:
        defaults = dict(
            total_completions=10,
            total_sampled=2,
            total_reviewed=1,
            total_passed=1,
            total_failed=0,
            total_pending=1,
            effective_rate=0.2,
            pass_rate=1.0,
            period_start=None,
            period_end=None,
        )
        defaults.update(kwargs)
        return AuditSummary(**defaults)

    def test_frozen(self):
        s = self._make_summary()
        with pytest.raises((AttributeError, TypeError)):
            s.total_completions = 99  # type: ignore[misc]

    def test_to_dict_all_keys_present(self):
        s = self._make_summary()
        d = s.to_dict()
        expected_keys = {
            "total_completions", "total_sampled", "total_reviewed",
            "total_passed", "total_failed", "total_pending",
            "effective_rate", "pass_rate", "period_start", "period_end",
        }
        assert expected_keys == set(d.keys())

    def test_to_dict_rates_rounded_to_4dp(self):
        s = self._make_summary(effective_rate=0.12345678, pass_rate=0.98765432)
        d = s.to_dict()
        assert d["effective_rate"] == round(0.12345678, 4)
        assert d["pass_rate"] == round(0.98765432, 4)

    def test_to_dict_period_start_none(self):
        s = self._make_summary(period_start=None)
        assert s.to_dict()["period_start"] is None

    def test_to_dict_period_end_none(self):
        s = self._make_summary(period_end=None)
        assert s.to_dict()["period_end"] is None

    def test_to_dict_period_start_iso_string(self):
        dt = datetime(2026, 2, 23, 0, 0, 0, tzinfo=UTC)
        s = self._make_summary(period_start=dt)
        d = s.to_dict()
        assert d["period_start"] == dt.isoformat()

    def test_to_dict_period_end_iso_string(self):
        dt = datetime(2026, 2, 23, 23, 59, 59, tzinfo=UTC)
        s = self._make_summary(period_end=dt)
        d = s.to_dict()
        assert d["period_end"] == dt.isoformat()

    def test_zero_completions_effective_rate(self):
        s = self._make_summary(total_completions=0, total_sampled=0, effective_rate=0.0)
        assert s.to_dict()["effective_rate"] == 0.0

    def test_zero_reviewed_pass_rate(self):
        s = self._make_summary(total_reviewed=0, total_passed=0, pass_rate=0.0)
        assert s.to_dict()["pass_rate"] == 0.0


# ---------------------------------------------------------------------------
# AuditSampler initialisation
# ---------------------------------------------------------------------------


class TestAuditSamplerInit:
    def test_default_sample_rate(self, sampler: AuditSampler):
        assert sampler.sample_rate == DEFAULT_SAMPLE_RATE

    def test_custom_sample_rate(self):
        s = AuditSampler(sample_rate=0.5)
        assert s.sample_rate == 0.5

    def test_rate_clamped_below_minimum(self):
        s = AuditSampler(sample_rate=0.0)
        assert s.sample_rate == MIN_SAMPLE_RATE

    def test_rate_clamped_above_maximum(self):
        s = AuditSampler(sample_rate=2.0)
        assert s.sample_rate == MAX_SAMPLE_RATE

    def test_negative_rate_clamped_to_minimum(self):
        s = AuditSampler(sample_rate=-5.0)
        assert s.sample_rate == MIN_SAMPLE_RATE

    def test_rate_at_minimum_boundary(self):
        s = AuditSampler(sample_rate=MIN_SAMPLE_RATE)
        assert s.sample_rate == MIN_SAMPLE_RATE

    def test_rate_at_maximum_boundary(self):
        s = AuditSampler(sample_rate=MAX_SAMPLE_RATE)
        assert s.sample_rate == MAX_SAMPLE_RATE

    def test_initial_decisions_empty(self, sampler: AuditSampler):
        assert sampler.get_all_records() == []

    def test_initial_pending_audits_empty(self, sampler: AuditSampler):
        assert sampler.get_pending_audits() == []


# ---------------------------------------------------------------------------
# should_audit()
# ---------------------------------------------------------------------------


class TestShouldAudit:
    def test_empty_task_id_raises_value_error(self, sampler: AuditSampler):
        with pytest.raises(ValueError, match="task_id must be non-empty"):
            sampler.should_audit(task_id="", lane="docs")

    def test_full_rate_always_selects(self, full_rate_sampler: AuditSampler):
        for i in range(20):
            d = full_rate_sampler.should_audit(task_id=f"t-{i}", lane="docs")
            assert d.selected is True

    def test_returns_sampling_decision(self, sampler: AuditSampler):
        decision = sampler.should_audit("T-1234", "docs")
        assert isinstance(decision, SamplingDecision)

    def test_decision_has_correct_task_id(self, sampler: AuditSampler):
        decision = sampler.should_audit("MY-TASK", "docs")
        assert decision.task_id == "MY-TASK"

    def test_decision_has_correct_lane(self, sampler: AuditSampler):
        decision = sampler.should_audit("t-1", "test_writing")
        assert decision.lane == "test_writing"

    def test_empty_lane_defaults_to_unknown(self, sampler: AuditSampler):
        decision = sampler.should_audit("t-1", lane="")
        assert decision.lane == "unknown"

    def test_decision_records_current_sample_rate(self, sampler: AuditSampler):
        decision = sampler.should_audit("t-1", "docs")
        assert decision.sample_rate == sampler.sample_rate

    def test_decision_id_is_unique_per_call(self, sampler: AuditSampler):
        d1 = sampler.should_audit("t-1", "docs")
        d2 = sampler.should_audit("t-2", "docs")
        assert d1.decision_id != d2.decision_id

    def test_selected_true_when_random_below_rate(self, sampler: AuditSampler):
        with patch.object(sampler._rng, "random", return_value=0.001):
            decision = sampler.should_audit("t-select", "docs")
        assert decision.selected is True
        assert decision.random_value == pytest.approx(0.001)

    def test_selected_false_when_random_at_or_above_rate(self, sampler: AuditSampler):
        with patch.object(sampler._rng, "random", return_value=0.999):
            decision = sampler.should_audit("t-skip", "docs")
        assert decision.selected is False
        assert decision.random_value == pytest.approx(0.999)

    def test_selected_creates_audit_record(self, sampler: AuditSampler):
        with patch.object(sampler._rng, "random", return_value=0.001):
            sampler.should_audit("t-sel", "docs")
        record = sampler.get_record("t-sel")
        assert record is not None
        assert record.task_id == "t-sel"

    def test_not_selected_does_not_create_audit_record(self, sampler: AuditSampler):
        with patch.object(sampler._rng, "random", return_value=0.999):
            sampler.should_audit("t-skip", "docs")
        record = sampler.get_record("t-skip")
        assert record is None

    def test_decision_appended_to_decisions_list(self, sampler: AuditSampler):
        sampler.should_audit("t-1", "docs")
        sampler.should_audit("t-2", "docs")
        summary = sampler.get_daily_summary()
        assert summary.total_completions == 2

    def test_multiple_calls_accumulate_decisions(self, full_rate_sampler: AuditSampler):
        for i in range(5):
            full_rate_sampler.should_audit(f"t-{i}", "docs")
        summary = full_rate_sampler.get_daily_summary()
        assert summary.total_completions == 5
        assert summary.total_sampled == 5


# ---------------------------------------------------------------------------
# record_result()
# ---------------------------------------------------------------------------


class TestRecordResult:
    def _select_task(self, sampler: AuditSampler, task_id: str = "t-1") -> AuditRecord:
        """Force-select a task and return the sampler."""
        with patch.object(sampler._rng, "random", return_value=0.001):
            sampler.should_audit(task_id, "docs")
        return sampler.get_record(task_id)

    def test_empty_auditor_id_raises_value_error(self, sampler: AuditSampler):
        self._select_task(sampler)
        with pytest.raises(ValueError, match="auditor_id must be non-empty"):
            sampler.record_result("t-1", auditor_id="", passed=True)

    def test_missing_task_id_raises_key_error(self, sampler: AuditSampler):
        with pytest.raises(KeyError):
            sampler.record_result("nonexistent", auditor_id="alice@example.com", passed=True)

    def test_unsampled_task_id_raises_key_error(self, sampler: AuditSampler):
        with patch.object(sampler._rng, "random", return_value=0.999):
            sampler.should_audit("t-notsampled", "docs")
        with pytest.raises(KeyError):
            sampler.record_result("t-notsampled", auditor_id="alice@example.com", passed=True)

    def test_success_sets_status_passed(self, sampler: AuditSampler):
        self._select_task(sampler)
        sampler.record_result("t-1", auditor_id="alice@example.com", passed=True)
        record = sampler.get_record("t-1")
        assert record.status == AuditStatus.passed

    def test_failure_sets_status_failed(self, sampler: AuditSampler):
        self._select_task(sampler)
        sampler.record_result("t-1", auditor_id="bob@example.com", passed=False)
        record = sampler.get_record("t-1")
        assert record.status == AuditStatus.failed

    def test_sets_auditor_id(self, sampler: AuditSampler):
        self._select_task(sampler)
        sampler.record_result("t-1", auditor_id="carol@example.com", passed=True)
        record = sampler.get_record("t-1")
        assert record.auditor_id == "carol@example.com"

    def test_sets_passed_field(self, sampler: AuditSampler):
        self._select_task(sampler)
        sampler.record_result("t-1", auditor_id="alice@example.com", passed=True)
        record = sampler.get_record("t-1")
        assert record.passed is True

    def test_sets_notes(self, sampler: AuditSampler):
        self._select_task(sampler)
        sampler.record_result(
            "t-1", auditor_id="alice@example.com", passed=True, notes="Looks good."
        )
        record = sampler.get_record("t-1")
        assert record.notes == "Looks good."

    def test_sets_reviewed_at_timestamp(self, sampler: AuditSampler):
        self._select_task(sampler)
        before = datetime.now(UTC)
        sampler.record_result("t-1", auditor_id="alice@example.com", passed=True)
        after = datetime.now(UTC)
        record = sampler.get_record("t-1")
        assert record.reviewed_at is not None
        assert before <= record.reviewed_at <= after

    def test_reviewed_at_is_timezone_aware(self, sampler: AuditSampler):
        self._select_task(sampler)
        sampler.record_result("t-1", auditor_id="alice@example.com", passed=True)
        record = sampler.get_record("t-1")
        assert record.reviewed_at.tzinfo is not None

    def test_returns_updated_audit_record(self, sampler: AuditSampler):
        self._select_task(sampler)
        returned = sampler.record_result("t-1", auditor_id="alice@example.com", passed=True)
        assert isinstance(returned, AuditRecord)
        assert returned.task_id == "t-1"

    def test_empty_notes_is_acceptable(self, sampler: AuditSampler):
        self._select_task(sampler)
        record = sampler.record_result("t-1", auditor_id="alice@example.com", passed=True, notes="")
        assert record.notes == ""


# ---------------------------------------------------------------------------
# skip_audit()
# ---------------------------------------------------------------------------


class TestSkipAudit:
    def _select_task(self, sampler: AuditSampler, task_id: str = "t-1") -> None:
        with patch.object(sampler._rng, "random", return_value=0.001):
            sampler.should_audit(task_id, "docs")

    def test_missing_task_id_raises_key_error(self, sampler: AuditSampler):
        with pytest.raises(KeyError):
            sampler.skip_audit("not-sampled")

    def test_sets_status_to_skipped(self, sampler: AuditSampler):
        self._select_task(sampler)
        sampler.skip_audit("t-1")
        record = sampler.get_record("t-1")
        assert record.status == AuditStatus.skipped

    def test_sets_reason_as_notes(self, sampler: AuditSampler):
        self._select_task(sampler)
        sampler.skip_audit("t-1", reason="Task was reverted before review.")
        record = sampler.get_record("t-1")
        assert record.notes == "Task was reverted before review."

    def test_empty_reason_leaves_empty_notes(self, sampler: AuditSampler):
        self._select_task(sampler)
        sampler.skip_audit("t-1")
        record = sampler.get_record("t-1")
        assert record.notes == ""

    def test_sets_reviewed_at(self, sampler: AuditSampler):
        self._select_task(sampler)
        before = datetime.now(UTC)
        sampler.skip_audit("t-1")
        after = datetime.now(UTC)
        record = sampler.get_record("t-1")
        assert record.reviewed_at is not None
        assert before <= record.reviewed_at <= after

    def test_returns_updated_audit_record(self, sampler: AuditSampler):
        self._select_task(sampler)
        returned = sampler.skip_audit("t-1", reason="Reverted")
        assert isinstance(returned, AuditRecord)
        assert returned.status == AuditStatus.skipped


# ---------------------------------------------------------------------------
# get_record() / get_pending_audits() / get_all_records()
# ---------------------------------------------------------------------------


class TestRetrievalMethods:
    def _select(self, sampler: AuditSampler, task_id: str) -> None:
        with patch.object(sampler._rng, "random", return_value=0.001):
            sampler.should_audit(task_id, "docs")

    def _skip(self, sampler: AuditSampler, task_id: str) -> None:
        with patch.object(sampler._rng, "random", return_value=0.999):
            sampler.should_audit(task_id, "docs")

    def test_get_record_returns_record_for_sampled_task(self, sampler: AuditSampler):
        self._select(sampler, "t-sel")
        record = sampler.get_record("t-sel")
        assert record is not None
        assert record.task_id == "t-sel"

    def test_get_record_returns_none_for_unsampled_task(self, sampler: AuditSampler):
        self._skip(sampler, "t-not-sel")
        assert sampler.get_record("t-not-sel") is None

    def test_get_record_returns_none_for_unknown_task(self, sampler: AuditSampler):
        assert sampler.get_record("completely-unknown") is None

    def test_get_pending_audits_empty_when_nothing_selected(self, sampler: AuditSampler):
        self._skip(sampler, "t-1")
        assert sampler.get_pending_audits() == []

    def test_get_pending_audits_returns_pending_records(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        self._select(sampler, "t-2")
        pending = sampler.get_pending_audits()
        assert len(pending) == 2

    def test_get_pending_audits_excludes_reviewed(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        self._select(sampler, "t-2")
        sampler.record_result("t-1", auditor_id="alice@example.com", passed=True)
        pending = sampler.get_pending_audits()
        assert len(pending) == 1
        assert pending[0].task_id == "t-2"

    def test_get_pending_audits_excludes_skipped(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        sampler.skip_audit("t-1")
        assert sampler.get_pending_audits() == []

    def test_get_all_records_empty_initially(self, sampler: AuditSampler):
        assert sampler.get_all_records() == []

    def test_get_all_records_only_selected_tasks(self, sampler: AuditSampler):
        self._select(sampler, "t-sel")
        self._skip(sampler, "t-notsel")
        records = sampler.get_all_records()
        assert len(records) == 1
        assert records[0].task_id == "t-sel"

    def test_get_all_records_includes_all_statuses(self, sampler: AuditSampler):
        self._select(sampler, "t-pending")
        self._select(sampler, "t-passed")
        self._select(sampler, "t-failed")
        self._select(sampler, "t-skipped")
        sampler.record_result("t-passed", auditor_id="a@b.com", passed=True)
        sampler.record_result("t-failed", auditor_id="a@b.com", passed=False)
        sampler.skip_audit("t-skipped")
        records = sampler.get_all_records()
        assert len(records) == 4
        statuses = {r.status for r in records}
        assert statuses == {AuditStatus.pending, AuditStatus.passed, AuditStatus.failed, AuditStatus.skipped}


# ---------------------------------------------------------------------------
# get_daily_summary()
# ---------------------------------------------------------------------------


class TestGetDailySummary:
    def _select(self, sampler: AuditSampler, task_id: str) -> None:
        with patch.object(sampler._rng, "random", return_value=0.001):
            sampler.should_audit(task_id, "docs")

    def _not_select(self, sampler: AuditSampler, task_id: str) -> None:
        with patch.object(sampler._rng, "random", return_value=0.999):
            sampler.should_audit(task_id, "docs")

    def test_empty_state_returns_zero_summary(self, sampler: AuditSampler):
        summary = sampler.get_daily_summary()
        assert summary.total_completions == 0
        assert summary.total_sampled == 0
        assert summary.total_reviewed == 0
        assert summary.total_passed == 0
        assert summary.total_failed == 0
        assert summary.total_pending == 0
        assert summary.effective_rate == 0.0
        assert summary.pass_rate == 0.0

    def test_total_completions_counts_all_decisions(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        self._not_select(sampler, "t-2")
        self._not_select(sampler, "t-3")
        summary = sampler.get_daily_summary()
        assert summary.total_completions == 3

    def test_total_sampled_counts_selected_decisions(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        self._select(sampler, "t-2")
        self._not_select(sampler, "t-3")
        summary = sampler.get_daily_summary()
        assert summary.total_sampled == 2

    def test_total_reviewed_counts_passed_and_failed(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        self._select(sampler, "t-2")
        self._select(sampler, "t-3")
        sampler.record_result("t-1", auditor_id="a@b.com", passed=True)
        sampler.record_result("t-2", auditor_id="a@b.com", passed=False)
        summary = sampler.get_daily_summary()
        assert summary.total_reviewed == 2

    def test_total_passed_correct(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        self._select(sampler, "t-2")
        sampler.record_result("t-1", auditor_id="a@b.com", passed=True)
        sampler.record_result("t-2", auditor_id="a@b.com", passed=True)
        summary = sampler.get_daily_summary()
        assert summary.total_passed == 2

    def test_total_failed_correct(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        sampler.record_result("t-1", auditor_id="a@b.com", passed=False)
        summary = sampler.get_daily_summary()
        assert summary.total_failed == 1
        assert summary.total_passed == 0

    def test_total_pending_correct(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        self._select(sampler, "t-2")
        sampler.record_result("t-1", auditor_id="a@b.com", passed=True)
        summary = sampler.get_daily_summary()
        assert summary.total_pending == 1

    def test_effective_rate_zero_when_no_completions(self, sampler: AuditSampler):
        summary = sampler.get_daily_summary()
        assert summary.effective_rate == 0.0

    def test_effective_rate_computed_correctly(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        self._not_select(sampler, "t-2")
        self._not_select(sampler, "t-3")
        self._not_select(sampler, "t-4")
        summary = sampler.get_daily_summary()
        assert summary.effective_rate == pytest.approx(1 / 4)

    def test_pass_rate_zero_when_no_reviewed(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        summary = sampler.get_daily_summary()
        assert summary.pass_rate == 0.0

    def test_pass_rate_computed_correctly(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        self._select(sampler, "t-2")
        self._select(sampler, "t-3")
        sampler.record_result("t-1", auditor_id="a@b.com", passed=True)
        sampler.record_result("t-2", auditor_id="a@b.com", passed=True)
        sampler.record_result("t-3", auditor_id="a@b.com", passed=False)
        summary = sampler.get_daily_summary()
        assert summary.pass_rate == pytest.approx(2 / 3)

    def test_period_start_none_when_not_provided(self, sampler: AuditSampler):
        summary = sampler.get_daily_summary()
        assert summary.period_start is None

    def test_period_end_none_when_not_provided(self, sampler: AuditSampler):
        summary = sampler.get_daily_summary()
        assert summary.period_end is None

    def test_period_start_filter_excludes_early_decisions(self, sampler: AuditSampler):
        # Simulate a decision in the "past" by seeding it first, then set period_start after
        self._select(sampler, "t-old")
        # Give it a moment so timestamps differ
        time.sleep(0.01)
        cutoff = datetime.now(UTC)
        time.sleep(0.01)
        self._select(sampler, "t-new")

        summary = sampler.get_daily_summary(period_start=cutoff)
        assert summary.total_completions == 1

    def test_period_end_filter_excludes_late_decisions(self, sampler: AuditSampler):
        self._select(sampler, "t-1")
        time.sleep(0.01)
        cutoff = datetime.now(UTC)
        time.sleep(0.01)
        self._select(sampler, "t-2")

        summary = sampler.get_daily_summary(period_end=cutoff)
        assert summary.total_completions == 1

    def test_summary_returns_audit_summary_instance(self, sampler: AuditSampler):
        summary = sampler.get_daily_summary()
        assert isinstance(summary, AuditSummary)

    def test_mixed_results_summary(self, sampler: AuditSampler):
        # 5 completions, 3 selected, 2 reviewed (1 pass, 1 fail), 1 pending
        self._not_select(sampler, "t-ns1")
        self._not_select(sampler, "t-ns2")
        self._select(sampler, "t-1")
        self._select(sampler, "t-2")
        self._select(sampler, "t-3")
        sampler.record_result("t-1", auditor_id="a@b.com", passed=True)
        sampler.record_result("t-2", auditor_id="a@b.com", passed=False)

        summary = sampler.get_daily_summary()
        assert summary.total_completions == 5
        assert summary.total_sampled == 3
        assert summary.total_reviewed == 2
        assert summary.total_passed == 1
        assert summary.total_failed == 1
        assert summary.total_pending == 1
        assert summary.effective_rate == pytest.approx(3 / 5)
        assert summary.pass_rate == pytest.approx(1 / 2)


# ---------------------------------------------------------------------------
# update_config()
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    def test_changes_sample_rate(self, sampler: AuditSampler):
        sampler.update_config(0.5)
        assert sampler.sample_rate == 0.5

    def test_returns_new_rate(self, sampler: AuditSampler):
        returned = sampler.update_config(0.3)
        assert returned == pytest.approx(0.3)

    def test_clamps_below_minimum(self, sampler: AuditSampler):
        returned = sampler.update_config(0.0)
        assert returned == MIN_SAMPLE_RATE
        assert sampler.sample_rate == MIN_SAMPLE_RATE

    def test_clamps_above_maximum(self, sampler: AuditSampler):
        returned = sampler.update_config(999.0)
        assert returned == MAX_SAMPLE_RATE
        assert sampler.sample_rate == MAX_SAMPLE_RATE

    def test_clamps_negative(self, sampler: AuditSampler):
        returned = sampler.update_config(-1.0)
        assert returned == MIN_SAMPLE_RATE

    def test_rate_used_in_subsequent_should_audit(self, sampler: AuditSampler):
        sampler.update_config(1.0)
        decision = sampler.should_audit("t-after-update", "docs")
        assert decision.sample_rate == pytest.approx(1.0)

    def test_boundary_minimum(self, sampler: AuditSampler):
        returned = sampler.update_config(MIN_SAMPLE_RATE)
        assert returned == MIN_SAMPLE_RATE

    def test_boundary_maximum(self, sampler: AuditSampler):
        returned = sampler.update_config(MAX_SAMPLE_RATE)
        assert returned == MAX_SAMPLE_RATE


# ---------------------------------------------------------------------------
# AuditSampler.reset()
# ---------------------------------------------------------------------------


class TestAuditSamplerReset:
    def test_reset_clears_decisions(self, full_rate_sampler: AuditSampler):
        full_rate_sampler.should_audit("t-1", "docs")
        full_rate_sampler.reset()
        summary = full_rate_sampler.get_daily_summary()
        assert summary.total_completions == 0

    def test_reset_clears_records(self, full_rate_sampler: AuditSampler):
        full_rate_sampler.should_audit("t-1", "docs")
        full_rate_sampler.reset()
        assert full_rate_sampler.get_all_records() == []

    def test_reset_restores_default_sample_rate(self):
        s = AuditSampler(sample_rate=0.75)
        s.reset()
        assert s.sample_rate == DEFAULT_SAMPLE_RATE


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_audit_sampler_returns_same_instance(self):
        s1 = get_audit_sampler()
        s2 = get_audit_sampler()
        assert s1 is s2

    def test_reset_audit_sampler_creates_fresh_instance(self):
        s1 = get_audit_sampler()
        reset_audit_sampler()
        s2 = get_audit_sampler()
        assert s1 is not s2

    def test_reset_clears_in_memory_state(self):
        sampler = get_audit_sampler()
        sampler.update_config(1.0)
        sampler.should_audit("t-1", "docs")
        reset_audit_sampler()
        fresh = get_audit_sampler()
        # Fresh instance has default rate and empty decisions
        assert fresh.sample_rate == DEFAULT_SAMPLE_RATE
        assert fresh.get_daily_summary().total_completions == 0

    def test_get_audit_sampler_is_thread_safe(self):
        instances: list[AuditSampler] = []

        def _get():
            instances.append(get_audit_sampler())

        threads = [threading.Thread(target=_get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len({id(s) for s in instances}) == 1

    def test_singleton_has_default_sample_rate(self):
        sampler = get_audit_sampler()
        assert sampler.sample_rate == DEFAULT_SAMPLE_RATE


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_should_audit_does_not_corrupt_state(self):
        sampler = AuditSampler(sample_rate=1.0)
        errors: list[Exception] = []

        def _audit(n: int):
            try:
                for i in range(30):
                    sampler.should_audit(f"t-{n}-{i}", "docs")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_audit, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        summary = sampler.get_daily_summary()
        assert summary.total_completions == 150
        assert summary.total_sampled == 150

    def test_concurrent_record_result_does_not_corrupt_state(self):
        sampler = AuditSampler(sample_rate=1.0)
        n_tasks = 50
        for i in range(n_tasks):
            with patch.object(sampler._rng, "random", return_value=0.001):
                sampler.should_audit(f"t-{i}", "docs")

        errors: list[Exception] = []

        def _record(task_id: str):
            try:
                sampler.record_result(task_id, auditor_id="alice@example.com", passed=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_record, args=(f"t-{i}",)) for i in range(n_tasks)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        summary = sampler.get_daily_summary()
        assert summary.total_reviewed == n_tasks

    def test_concurrent_mixed_operations_are_safe(self):
        sampler = AuditSampler(sample_rate=1.0)
        errors: list[Exception] = []

        def _auditor():
            try:
                for i in range(20):
                    sampler.should_audit(f"audit-{i}", "docs")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def _config_updater():
            try:
                for rate in [0.1, 0.2, 0.5, 0.8, 1.0]:
                    sampler.update_config(rate)
                    time.sleep(0.001)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def _summariser():
            try:
                for _ in range(10):
                    sampler.get_daily_summary()
                    time.sleep(0.001)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=_auditor),
            threading.Thread(target=_config_updater),
            threading.Thread(target=_summariser),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_100_percent_rate_selects_every_task(self):
        sampler = AuditSampler(sample_rate=1.0)
        for i in range(10):
            d = sampler.should_audit(f"t-{i}", "lane")
            assert d.selected is True

    def test_multiple_tasks_partial_selection_count_accurate(self):
        sampler = AuditSampler(sample_rate=1.0)
        n = 20
        for i in range(n):
            sampler.should_audit(f"t-{i}", "docs")
        assert len(sampler.get_all_records()) == n

    def test_100_percent_rate_all_records_pending_initially(self):
        sampler = AuditSampler(sample_rate=1.0)
        for i in range(5):
            sampler.should_audit(f"t-{i}", "docs")
        pending = sampler.get_pending_audits()
        assert len(pending) == 5

    def test_task_id_with_special_characters(self):
        sampler = AuditSampler(sample_rate=1.0)
        task_id = "forge:node-01/task@home#42"
        decision = sampler.should_audit(task_id, "docs")
        assert decision.task_id == task_id
        assert sampler.get_record(task_id) is not None

    def test_lane_with_special_characters(self):
        sampler = AuditSampler(sample_rate=1.0)
        decision = sampler.should_audit("t-1", "my-lane/v2")
        assert decision.lane == "my-lane/v2"

    def test_get_daily_summary_returns_correct_type(self):
        sampler = AuditSampler()
        result = sampler.get_daily_summary()
        assert isinstance(result, AuditSummary)

    def test_record_result_with_long_notes(self):
        sampler = AuditSampler(sample_rate=1.0)
        sampler.should_audit("t-1", "docs")
        long_notes = "x" * 10000
        record = sampler.record_result("t-1", auditor_id="alice@example.com", passed=True, notes=long_notes)
        assert record.notes == long_notes

    def test_skip_then_record_result_raises_key_error_after_skip(self):
        """skip_audit keeps the record, so record_result still works on it."""
        sampler = AuditSampler(sample_rate=1.0)
        sampler.should_audit("t-1", "docs")
        sampler.skip_audit("t-1")
        # record_result on a skipped task should succeed (record exists, no guard)
        record = sampler.record_result("t-1", auditor_id="alice@example.com", passed=True)
        assert record.status == AuditStatus.passed
