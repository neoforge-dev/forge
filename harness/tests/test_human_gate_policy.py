"""Tests for HumanGatePolicy — DF-4002.

Covers:
- GateResult and AuditEntry data classes
- GateStatus enum values
- HumanGateViolation exception
- enforce(): not_gated path (all non-gated lanes)
- enforce(): approved path (gated lane with human approval)
- enforce(): blocked path (gated lane, no approval, no override)
- enforce(): override_permitted path (valid override)
- enforce(): override_rejected path (invalid override — bad reason / missing id)
- Override conditions: reason length, overrider_id presence
- Audit log: entries recorded for all outcomes
- get_audit_log_for_task() filtering
- get_override_entries() filtering
- clear_audit_log()
- is_lane_gated(), add_gated_lane(), remove_gated_lane()
- get_gated_lanes()
- reset()
- Singleton factory (get_human_gate_policy / reset_human_gate_policy)
- Thread safety (basic)
- DEFAULT_HARD_GATED_LANES contents
- String enum coercion in task dicts
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from forge_harness.dark_factory.human_gate_policy import (
    DEFAULT_HARD_GATED_LANES,
    AuditEntry,
    GateResult,
    GateStatus,
    HumanGatePolicy,
    HumanGateViolationError,
    get_human_gate_policy,
    reset_human_gate_policy,
)
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import WorkCellLane

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LONG_REASON = "This is a valid override reason that exceeds twenty characters."
_SHORT_REASON = "short"
_OVERRIDER = "alice@example.com"


def make_task(
    task_type: TaskType | str,
    risk_tier: RiskTier | str,
    task_id: str = "task-001",
) -> dict[str, Any]:
    return {"task_id": task_id, "task_type": task_type, "risk_tier": risk_tier}


# Hard-gated task (security_change at any risk)
def security_task(task_id: str = "sec-task") -> dict[str, Any]:
    return make_task(TaskType.security_change, RiskTier.high, task_id=task_id)


# Hard-gated task (deployment)
def deploy_task(task_id: str = "deploy-task") -> dict[str, Any]:
    return make_task(TaskType.deployment, RiskTier.medium, task_id=task_id)


# Non-gated task (docs)
def docs_task(task_id: str = "docs-task") -> dict[str, Any]:
    return make_task(TaskType.docs_update, RiskTier.low, task_id=task_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_singleton():
    reset_human_gate_policy()
    yield
    reset_human_gate_policy()


@pytest.fixture()
def gate() -> HumanGatePolicy:
    return HumanGatePolicy()


# ---------------------------------------------------------------------------
# 1. DEFAULT_HARD_GATED_LANES
# ---------------------------------------------------------------------------


class TestDefaultHardGatedLanes:
    def test_security_change_is_gated(self):
        assert WorkCellLane.security_change in DEFAULT_HARD_GATED_LANES

    def test_deployment_is_gated(self):
        assert WorkCellLane.deployment in DEFAULT_HARD_GATED_LANES

    def test_docs_not_gated(self):
        assert WorkCellLane.docs not in DEFAULT_HARD_GATED_LANES

    def test_test_writing_not_gated(self):
        assert WorkCellLane.test_writing not in DEFAULT_HARD_GATED_LANES

    def test_api_simple_not_gated(self):
        assert WorkCellLane.api_simple not in DEFAULT_HARD_GATED_LANES

    def test_is_frozenset(self):
        assert isinstance(DEFAULT_HARD_GATED_LANES, frozenset)


# ---------------------------------------------------------------------------
# 2. GateStatus enum
# ---------------------------------------------------------------------------


class TestGateStatus:
    def test_all_expected_statuses_exist(self):
        assert GateStatus.not_gated
        assert GateStatus.approved
        assert GateStatus.blocked
        assert GateStatus.override_permitted
        assert GateStatus.override_rejected

    def test_statuses_are_strings(self):
        for status in GateStatus:
            assert isinstance(status.value, str)


# ---------------------------------------------------------------------------
# 3. HumanGateViolation exception
# ---------------------------------------------------------------------------


class TestHumanGateViolationError:
    def test_is_exception_subclass(self):
        assert issubclass(HumanGateViolationError, Exception)

    def test_carries_task_id(self):
        exc = HumanGateViolationError(
            task_id="t1",
            work_cell_lane=WorkCellLane.security_change,
            rejected_reason="No approval",
        )
        assert exc.task_id == "t1"

    def test_carries_lane(self):
        exc = HumanGateViolationError(
            task_id="t1",
            work_cell_lane=WorkCellLane.deployment,
            rejected_reason="No approval",
        )
        assert exc.work_cell_lane == WorkCellLane.deployment

    def test_carries_rejected_reason(self):
        exc = HumanGateViolationError(
            task_id="t1",
            work_cell_lane=WorkCellLane.security_change,
            rejected_reason="missing override_reason",
        )
        assert "missing override_reason" in exc.rejected_reason

    def test_carries_audit_entry_id(self):
        exc = HumanGateViolationError(
            task_id="t1",
            work_cell_lane=WorkCellLane.security_change,
            rejected_reason="No approval",
            audit_entry_id="abc-123",
        )
        assert exc.audit_entry_id == "abc-123"

    def test_str_representation_contains_task_id(self):
        exc = HumanGateViolationError(
            task_id="my-task",
            work_cell_lane=WorkCellLane.security_change,
            rejected_reason="No approval",
        )
        assert "my-task" in str(exc)

    def test_audit_entry_id_defaults_to_none(self):
        exc = HumanGateViolationError(
            task_id="t1",
            work_cell_lane=WorkCellLane.security_change,
            rejected_reason="x",
        )
        assert exc.audit_entry_id is None


# ---------------------------------------------------------------------------
# 4. enforce() — not_gated path
# ---------------------------------------------------------------------------


class TestEnforceNotGated:
    def test_docs_task_not_gated(self, gate):
        result = gate.enforce(docs_task())
        assert result.status == GateStatus.not_gated
        assert result.may_proceed is True
        assert result.is_gated is False

    def test_test_writing_not_gated(self, gate):
        task = make_task(TaskType.test_writing, RiskTier.low)
        result = gate.enforce(task)
        assert result.status == GateStatus.not_gated

    def test_api_simple_not_gated(self, gate):
        task = make_task(TaskType.api_endpoint, RiskTier.low)
        result = gate.enforce(task)
        assert result.status == GateStatus.not_gated

    def test_not_gated_returns_gate_result_instance(self, gate):
        result = gate.enforce(docs_task())
        assert isinstance(result, GateResult)

    def test_not_gated_task_id_preserved(self, gate):
        result = gate.enforce(make_task(TaskType.docs_update, RiskTier.low, task_id="my-doc"))
        assert result.task_id == "my-doc"

    def test_not_gated_override_id_is_none(self, gate):
        result = gate.enforce(docs_task())
        assert result.override_id is None

    def test_not_gated_records_audit_entry(self, gate):
        gate.enforce(docs_task())
        log = gate.get_audit_log()
        assert len(log) == 1
        assert log[0].status == GateStatus.not_gated

    def test_refactor_task_not_gated(self, gate):
        task = make_task(TaskType.code_refactor, RiskTier.medium)
        result = gate.enforce(task)
        assert result.status == GateStatus.not_gated

    def test_api_stateful_not_gated(self, gate):
        task = make_task(TaskType.api_endpoint, RiskTier.medium)
        result = gate.enforce(task)
        assert result.status == GateStatus.not_gated


# ---------------------------------------------------------------------------
# 5. enforce() — approved path
# ---------------------------------------------------------------------------


class TestEnforceApproved:
    def test_security_change_approved(self, gate):
        result = gate.enforce(security_task(), approved=True)
        assert result.status == GateStatus.approved
        assert result.may_proceed is True

    def test_deployment_approved(self, gate):
        result = gate.enforce(deploy_task(), approved=True)
        assert result.status == GateStatus.approved
        assert result.may_proceed is True

    def test_approved_is_gated(self, gate):
        result = gate.enforce(security_task(), approved=True)
        assert result.is_gated is True

    def test_approved_no_override_id(self, gate):
        result = gate.enforce(security_task(), approved=True)
        assert result.override_id is None

    def test_approved_records_audit_entry(self, gate):
        gate.enforce(security_task(), approved=True)
        log = gate.get_audit_log()
        assert len(log) == 1
        entry = log[0]
        assert entry.status == GateStatus.approved
        assert entry.approved is True

    def test_approved_task_id_preserved(self, gate):
        result = gate.enforce(security_task(task_id="sec-99"), approved=True)
        assert result.task_id == "sec-99"


# ---------------------------------------------------------------------------
# 6. enforce() — blocked path
# ---------------------------------------------------------------------------


class TestEnforceBlocked:
    def test_security_change_without_approval_raises(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(security_task())

    def test_deployment_without_approval_raises(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(deploy_task())

    def test_exception_carries_correct_task_id(self, gate):
        with pytest.raises(HumanGateViolationError) as exc_info:
            gate.enforce(security_task(task_id="blocked-task"))
        assert exc_info.value.task_id == "blocked-task"

    def test_exception_carries_correct_lane(self, gate):
        with pytest.raises(HumanGateViolationError) as exc_info:
            gate.enforce(security_task())
        assert exc_info.value.work_cell_lane == WorkCellLane.security_change

    def test_blocked_records_audit_entry(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(security_task())
        log = gate.get_audit_log()
        assert len(log) == 1
        assert log[0].status == GateStatus.blocked

    def test_blocked_audit_has_rejected_reason(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(security_task())
        entry = gate.get_audit_log()[0]
        assert entry.rejected_reason is not None
        assert len(entry.rejected_reason) > 0

    def test_exception_references_audit_entry_id(self, gate):
        with pytest.raises(HumanGateViolationError) as exc_info:
            gate.enforce(security_task())
        log = gate.get_audit_log()
        assert exc_info.value.audit_entry_id == log[0].entry_id


# ---------------------------------------------------------------------------
# 7. enforce() — override_permitted path
# ---------------------------------------------------------------------------


class TestEnforceOverridePermitted:
    def test_valid_override_returns_gate_result(self, gate):
        result = gate.enforce(
            security_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        assert isinstance(result, GateResult)

    def test_valid_override_status_is_override_permitted(self, gate):
        result = gate.enforce(
            security_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        assert result.status == GateStatus.override_permitted

    def test_valid_override_may_proceed(self, gate):
        result = gate.enforce(
            security_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        assert result.may_proceed is True

    def test_valid_override_is_gated(self, gate):
        result = gate.enforce(
            security_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        assert result.is_gated is True

    def test_valid_override_id_populated(self, gate):
        result = gate.enforce(
            security_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        assert result.override_id is not None
        assert len(result.override_id) > 0

    def test_valid_override_reason_preserved(self, gate):
        result = gate.enforce(
            security_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        assert result.override_reason == _LONG_REASON

    def test_valid_override_overrider_id_preserved(self, gate):
        result = gate.enforce(
            security_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        assert result.overrider_id == _OVERRIDER

    def test_valid_override_records_audit_entry(self, gate):
        gate.enforce(
            security_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        log = gate.get_audit_log()
        assert len(log) == 1
        entry = log[0]
        assert entry.status == GateStatus.override_permitted
        assert entry.force_override is True
        assert entry.override_reason == _LONG_REASON
        assert entry.overrider_id == _OVERRIDER

    def test_override_works_for_deployment(self, gate):
        result = gate.enforce(
            deploy_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        assert result.status == GateStatus.override_permitted

    def test_override_id_in_audit_matches_result(self, gate):
        result = gate.enforce(
            security_task(),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        entries = gate.get_override_entries()
        assert len(entries) == 1
        assert entries[0].entry_id == result.override_id


# ---------------------------------------------------------------------------
# 8. enforce() — override_rejected path
# ---------------------------------------------------------------------------


class TestEnforceOverrideRejected:
    def test_missing_overrider_id_raises(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                security_task(),
                force_override=True,
                override_reason=_LONG_REASON,
                overrider_id="",  # missing
            )

    def test_missing_override_reason_raises(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                security_task(),
                force_override=True,
                override_reason="",  # missing
                overrider_id=_OVERRIDER,
            )

    def test_short_override_reason_raises(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                security_task(),
                force_override=True,
                override_reason=_SHORT_REASON,  # too short
                overrider_id=_OVERRIDER,
            )

    def test_whitespace_only_reason_raises(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                security_task(),
                force_override=True,
                override_reason="   ",
                overrider_id=_OVERRIDER,
            )

    def test_whitespace_only_overrider_id_raises(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                security_task(),
                force_override=True,
                override_reason=_LONG_REASON,
                overrider_id="   ",
            )

    def test_rejected_records_override_rejected_status(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                security_task(),
                force_override=True,
                override_reason=_SHORT_REASON,
                overrider_id=_OVERRIDER,
            )
        log = gate.get_audit_log()
        assert len(log) == 1
        assert log[0].status == GateStatus.override_rejected

    def test_rejected_audit_has_rejected_reason(self, gate):
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                security_task(),
                force_override=True,
                override_reason=_SHORT_REASON,
                overrider_id=_OVERRIDER,
            )
        entry = gate.get_audit_log()[0]
        assert entry.rejected_reason is not None

    def test_exception_references_audit_entry_on_rejection(self, gate):
        with pytest.raises(HumanGateViolationError) as exc_info:
            gate.enforce(
                security_task(),
                force_override=True,
                override_reason="",
                overrider_id=_OVERRIDER,
            )
        log = gate.get_audit_log()
        assert exc_info.value.audit_entry_id == log[0].entry_id

    def test_exactly_20_char_reason_is_permitted(self, gate):
        """A reason of exactly 20 characters meets the minimum requirement."""
        reason_20 = "x" * 20
        result = gate.enforce(
            security_task(),
            force_override=True,
            override_reason=reason_20,
            overrider_id=_OVERRIDER,
        )
        assert result.status == GateStatus.override_permitted

    def test_19_char_reason_is_rejected(self, gate):
        reason_19 = "x" * 19
        with pytest.raises(HumanGateViolationError):
            gate.enforce(
                security_task(),
                force_override=True,
                override_reason=reason_19,
                overrider_id=_OVERRIDER,
            )


# ---------------------------------------------------------------------------
# 9. Audit log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_log_empty_initially(self, gate):
        assert gate.get_audit_log() == []

    def test_log_grows_with_each_enforce_call(self, gate):
        gate.enforce(docs_task())
        gate.enforce(docs_task())
        assert len(gate.get_audit_log()) == 2

    def test_log_is_a_copy(self, gate):
        gate.enforce(docs_task())
        log = gate.get_audit_log()
        log.clear()
        # Internal log should still have one entry
        assert len(gate.get_audit_log()) == 1

    def test_log_contains_audit_entry_instances(self, gate):
        gate.enforce(docs_task())
        log = gate.get_audit_log()
        assert all(isinstance(e, AuditEntry) for e in log)

    def test_audit_entry_has_uuid_entry_id(self, gate):
        gate.enforce(docs_task())
        entry = gate.get_audit_log()[0]
        import uuid
        uuid.UUID(entry.entry_id)  # must not raise

    def test_audit_entry_has_recorded_at_timestamp(self, gate):
        from datetime import datetime
        gate.enforce(docs_task())
        entry = gate.get_audit_log()[0]
        assert isinstance(entry.recorded_at, datetime)

    def test_mixed_outcome_log(self, gate):
        gate.enforce(docs_task())  # not_gated
        gate.enforce(security_task(), approved=True)  # approved
        with pytest.raises(HumanGateViolationError):
            gate.enforce(deploy_task())  # blocked
        log = gate.get_audit_log()
        statuses = {e.status for e in log}
        assert GateStatus.not_gated in statuses
        assert GateStatus.approved in statuses
        assert GateStatus.blocked in statuses

    def test_get_audit_log_for_task_filters_correctly(self, gate):
        gate.enforce(make_task(TaskType.docs_update, RiskTier.low, "task-A"))
        gate.enforce(make_task(TaskType.docs_update, RiskTier.low, "task-B"))
        gate.enforce(make_task(TaskType.docs_update, RiskTier.low, "task-A"))
        entries = gate.get_audit_log_for_task("task-A")
        assert len(entries) == 2
        assert all(e.task_id == "task-A" for e in entries)

    def test_get_audit_log_for_task_empty_when_no_match(self, gate):
        gate.enforce(docs_task("task-X"))
        entries = gate.get_audit_log_for_task("nonexistent")
        assert entries == []

    def test_get_override_entries_only_returns_override_attempts(self, gate):
        gate.enforce(docs_task())  # not_gated (no override)
        gate.enforce(security_task(), approved=True)  # approved (no override)
        gate.enforce(
            security_task(task_id="sec-2"),
            force_override=True,
            override_reason=_LONG_REASON,
            overrider_id=_OVERRIDER,
        )
        overrides = gate.get_override_entries()
        assert len(overrides) == 1
        assert overrides[0].force_override is True

    def test_clear_audit_log_returns_count(self, gate):
        gate.enforce(docs_task())
        gate.enforce(docs_task())
        count = gate.clear_audit_log()
        assert count == 2

    def test_clear_audit_log_empties_log(self, gate):
        gate.enforce(docs_task())
        gate.clear_audit_log()
        assert gate.get_audit_log() == []

    def test_clear_audit_log_zero_when_empty(self, gate):
        count = gate.clear_audit_log()
        assert count == 0


# ---------------------------------------------------------------------------
# 10. Gate configuration (is_lane_gated / add / remove)
# ---------------------------------------------------------------------------


class TestGateConfiguration:
    def test_security_change_is_gated(self, gate):
        assert gate.is_lane_gated(WorkCellLane.security_change) is True

    def test_deployment_is_gated(self, gate):
        assert gate.is_lane_gated(WorkCellLane.deployment) is True

    def test_docs_not_gated(self, gate):
        assert gate.is_lane_gated(WorkCellLane.docs) is False

    def test_add_gated_lane(self, gate):
        gate.add_gated_lane(WorkCellLane.docs)
        assert gate.is_lane_gated(WorkCellLane.docs) is True

    def test_add_gated_lane_makes_enforce_raise(self, gate):
        gate.add_gated_lane(WorkCellLane.docs)
        with pytest.raises(HumanGateViolationError):
            gate.enforce(docs_task())

    def test_remove_gated_lane(self, gate):
        gate.remove_gated_lane(WorkCellLane.security_change)
        assert gate.is_lane_gated(WorkCellLane.security_change) is False

    def test_remove_gated_lane_allows_task_through(self, gate):
        gate.remove_gated_lane(WorkCellLane.security_change)
        result = gate.enforce(security_task())
        assert result.status == GateStatus.not_gated

    def test_get_gated_lanes_returns_frozenset(self, gate):
        lanes = gate.get_gated_lanes()
        assert isinstance(lanes, frozenset)

    def test_get_gated_lanes_contains_defaults(self, gate):
        lanes = gate.get_gated_lanes()
        assert WorkCellLane.security_change in lanes
        assert WorkCellLane.deployment in lanes

    def test_add_lane_reflected_in_get_gated_lanes(self, gate):
        gate.add_gated_lane(WorkCellLane.research)
        assert WorkCellLane.research in gate.get_gated_lanes()

    def test_remove_lane_reflected_in_get_gated_lanes(self, gate):
        gate.remove_gated_lane(WorkCellLane.deployment)
        assert WorkCellLane.deployment not in gate.get_gated_lanes()


# ---------------------------------------------------------------------------
# 11. reset()
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_restores_default_gated_lanes(self, gate):
        gate.remove_gated_lane(WorkCellLane.security_change)
        gate.reset()
        assert gate.is_lane_gated(WorkCellLane.security_change) is True

    def test_reset_clears_audit_log(self, gate):
        gate.enforce(docs_task())
        gate.reset()
        assert gate.get_audit_log() == []

    def test_added_lanes_cleared_on_reset(self, gate):
        gate.add_gated_lane(WorkCellLane.research)
        gate.reset()
        assert gate.is_lane_gated(WorkCellLane.research) is False


# ---------------------------------------------------------------------------
# 12. Custom config at construction time
# ---------------------------------------------------------------------------


class TestCustomConfig:
    def test_custom_hard_gated_lanes(self):
        gate = HumanGatePolicy(
            hard_gated_lanes=frozenset({WorkCellLane.docs})
        )
        assert gate.is_lane_gated(WorkCellLane.docs) is True
        assert gate.is_lane_gated(WorkCellLane.security_change) is False

    def test_empty_gated_lanes_allows_all(self):
        gate = HumanGatePolicy(hard_gated_lanes=frozenset())
        # security_change would normally be gated — should pass now
        result = gate.enforce(security_task())
        assert result.status == GateStatus.not_gated

    def test_all_lanes_gated(self):
        gate = HumanGatePolicy(hard_gated_lanes=frozenset(WorkCellLane))
        with pytest.raises(HumanGateViolationError):
            gate.enforce(docs_task())


# ---------------------------------------------------------------------------
# 13. Singleton factory
# ---------------------------------------------------------------------------


class TestSingletonFactory:
    def test_get_returns_human_gate_policy(self):
        gate = get_human_gate_policy()
        assert isinstance(gate, HumanGatePolicy)

    def test_same_instance_on_repeated_calls(self):
        a = get_human_gate_policy()
        b = get_human_gate_policy()
        assert a is b

    def test_reset_creates_fresh_instance(self):
        a = get_human_gate_policy()
        reset_human_gate_policy()
        b = get_human_gate_policy()
        assert a is not b

    def test_singleton_defaults_to_hard_gated_lanes(self):
        gate = get_human_gate_policy()
        assert gate.is_lane_gated(WorkCellLane.security_change) is True


# ---------------------------------------------------------------------------
# 14. String enum coercion
# ---------------------------------------------------------------------------


class TestStringEnumCoercion:
    def test_accepts_string_task_type(self, gate):
        task = make_task("docs-update", "low")
        result = gate.enforce(task)
        assert result.status == GateStatus.not_gated

    def test_accepts_string_for_gated_lane(self, gate):
        task = make_task("security-change", "high")
        with pytest.raises(HumanGateViolationError):
            gate.enforce(task)

    def test_invalid_task_type_raises_value_error(self, gate):
        with pytest.raises(ValueError):
            gate.enforce({"task_type": "not-real", "risk_tier": "low"})

    def test_missing_task_type_raises_key_error(self, gate):
        with pytest.raises(KeyError):
            gate.enforce({"risk_tier": "low"})


# ---------------------------------------------------------------------------
# 15. Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_enforce_not_gated(self, gate):
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(10):
                    gate.enforce(docs_task())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_add_remove_gated_lanes(self, gate):
        errors: list[Exception] = []

        def adder():
            try:
                for _ in range(5):
                    gate.add_gated_lane(WorkCellLane.research)
            except Exception as exc:
                errors.append(exc)

        def remover():
            try:
                for _ in range(5):
                    gate.remove_gated_lane(WorkCellLane.research)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=adder) for _ in range(3)]
        threads += [threading.Thread(target=remover) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_audit_log_reads(self, gate):
        errors: list[Exception] = []

        def writer():
            try:
                for _ in range(5):
                    gate.enforce(docs_task())
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                for _ in range(10):
                    gate.get_audit_log()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
