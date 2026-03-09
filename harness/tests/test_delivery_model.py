"""Tests for the FORGE xnode cross-node delivery state model.

Covers:
1. All valid delivery state transitions (every cell in VALID_DELIVERY_TRANSITIONS).
2. Invalid transitions raise DeliveryTransitionError.
3. Terminal states block every outbound move.
4. DeliveryRecord Pydantic field validation.
5. DeliveryStats computation from a list of records.
6. JSON serialization round-trip (model_dump / model_validate).
7. String-valued state acceptance (from JSON deserialization paths).
8. Contract completeness — every DeliveryState is in VALID_DELIVERY_TRANSITIONS.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from forge_harness.models.delivery import (
    TERMINAL_DELIVERY_STATES,
    VALID_DELIVERY_TRANSITIONS,
    DeliveryRecord,
    DeliveryState,
    DeliveryStats,
    DeliveryTransitionError,
    validate_delivery_transition,
)

# ---------------------------------------------------------------------------
# Shorthand
# ---------------------------------------------------------------------------

S = DeliveryState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 2, 22, 12, 0, 0, tzinfo=UTC)


def _make_record(**overrides) -> DeliveryRecord:
    """Construct a minimal valid DeliveryRecord with sensible defaults."""
    defaults = dict(
        message_id="msg_20260222_abc12345",
        task_id="IS-219",
        source_node="nova",
        target_node="sati",
        source_agent="orchestrator",
        target_agent="orchestrator",
        state=S.QUEUED,
        created_at=_NOW,
        payload_summary="Task IS-219: implement delivery model",
    )
    defaults.update(overrides)
    return DeliveryRecord(**defaults)


# ===========================================================================
# 1. All valid transitions succeed
# ===========================================================================


class TestValidTransitions:
    """Every (from, to) pair in VALID_DELIVERY_TRANSITIONS must return True."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            (from_s, to_s)
            for from_s, targets in VALID_DELIVERY_TRANSITIONS.items()
            for to_s in targets
        ],
    )
    def test_valid_pair_returns_true(self, from_state: DeliveryState, to_state: DeliveryState):
        assert validate_delivery_transition(from_state, to_state) is True

    def test_queued_to_dispatched(self):
        assert validate_delivery_transition(S.QUEUED, S.DISPATCHED) is True

    def test_queued_to_failed(self):
        assert validate_delivery_transition(S.QUEUED, S.FAILED) is True

    def test_queued_to_expired(self):
        assert validate_delivery_transition(S.QUEUED, S.EXPIRED) is True

    def test_dispatched_to_delivered(self):
        assert validate_delivery_transition(S.DISPATCHED, S.DELIVERED) is True

    def test_dispatched_to_failed(self):
        assert validate_delivery_transition(S.DISPATCHED, S.FAILED) is True

    def test_dispatched_to_expired(self):
        assert validate_delivery_transition(S.DISPATCHED, S.EXPIRED) is True

    def test_delivered_to_acked(self):
        assert validate_delivery_transition(S.DELIVERED, S.ACKED) is True

    def test_delivered_to_nacked(self):
        assert validate_delivery_transition(S.DELIVERED, S.NACKED) is True

    def test_delivered_to_expired(self):
        assert validate_delivery_transition(S.DELIVERED, S.EXPIRED) is True


# ===========================================================================
# 2. Invalid transitions raise DeliveryTransitionError
# ===========================================================================


class TestInvalidTransitions:
    """Non-existent edges must raise DeliveryTransitionError."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            # Backward moves
            (S.DISPATCHED, S.QUEUED),
            (S.DELIVERED, S.QUEUED),
            (S.DELIVERED, S.DISPATCHED),
            (S.ACKED, S.QUEUED),
            (S.NACKED, S.QUEUED),
            # Skipping moves
            (S.QUEUED, S.DELIVERED),
            (S.QUEUED, S.ACKED),
            (S.QUEUED, S.NACKED),
            (S.DISPATCHED, S.ACKED),
            (S.DISPATCHED, S.NACKED),
            # Terminal-state lockout
            (S.ACKED, S.DISPATCHED),
            (S.ACKED, S.DELIVERED),
            (S.NACKED, S.DELIVERED),
            (S.FAILED, S.QUEUED),
            (S.EXPIRED, S.QUEUED),
            # Self-transitions
            (S.QUEUED, S.QUEUED),
            (S.DELIVERED, S.DELIVERED),
            (S.ACKED, S.ACKED),
        ],
    )
    def test_invalid_pair_raises_transition_error(
        self, from_state: DeliveryState, to_state: DeliveryState
    ):
        with pytest.raises(DeliveryTransitionError):
            validate_delivery_transition(from_state, to_state)

    def test_transition_error_carries_from_state(self):
        with pytest.raises(DeliveryTransitionError) as exc_info:
            validate_delivery_transition(S.QUEUED, S.ACKED)
        assert exc_info.value.from_state is S.QUEUED

    def test_transition_error_carries_to_state(self):
        with pytest.raises(DeliveryTransitionError) as exc_info:
            validate_delivery_transition(S.QUEUED, S.ACKED)
        assert exc_info.value.to_state is S.ACKED

    def test_transition_error_carries_allowed_set(self):
        with pytest.raises(DeliveryTransitionError) as exc_info:
            validate_delivery_transition(S.QUEUED, S.ACKED)
        # QUEUED may only go to dispatched, failed, expired
        assert S.DISPATCHED in exc_info.value.allowed

    def test_transition_error_message_is_descriptive(self):
        with pytest.raises(DeliveryTransitionError) as exc_info:
            validate_delivery_transition(S.DISPATCHED, S.QUEUED)
        msg = str(exc_info.value)
        assert "dispatched" in msg
        assert "queued" in msg

    def test_unknown_from_state_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_delivery_transition("ghost_state", S.DISPATCHED)

    def test_unknown_to_state_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_delivery_transition(S.QUEUED, "ghost_state")


# ===========================================================================
# 3. Terminal states block every outbound transition
# ===========================================================================


class TestTerminalStates:
    """acked, nacked, failed, expired are terminal — nothing can follow them."""

    def test_terminal_states_set_is_non_empty(self):
        assert len(TERMINAL_DELIVERY_STATES) >= 4

    @pytest.mark.parametrize("expected", [S.ACKED, S.NACKED, S.FAILED, S.EXPIRED])
    def test_expected_state_is_terminal(self, expected: DeliveryState):
        assert expected in TERMINAL_DELIVERY_STATES

    def test_queued_is_not_terminal(self):
        assert S.QUEUED not in TERMINAL_DELIVERY_STATES

    def test_dispatched_is_not_terminal(self):
        assert S.DISPATCHED not in TERMINAL_DELIVERY_STATES

    def test_delivered_is_not_terminal(self):
        assert S.DELIVERED not in TERMINAL_DELIVERY_STATES

    @pytest.mark.parametrize("terminal", sorted(TERMINAL_DELIVERY_STATES, key=lambda s: s.value))
    def test_terminal_state_has_empty_outbound_set(self, terminal: DeliveryState):
        assert VALID_DELIVERY_TRANSITIONS[terminal] == frozenset()

    @pytest.mark.parametrize(
        "terminal, target",
        [(terminal, target) for terminal in TERMINAL_DELIVERY_STATES for target in DeliveryState],
    )
    def test_every_move_from_terminal_raises(self, terminal: DeliveryState, target: DeliveryState):
        with pytest.raises(DeliveryTransitionError):
            validate_delivery_transition(terminal, target)


# ===========================================================================
# 4. DeliveryRecord field validation
# ===========================================================================


class TestDeliveryRecordValidation:
    """Pydantic field constraints and coercions on DeliveryRecord."""

    def test_minimal_required_fields(self):
        record = _make_record()
        assert record.message_id == "msg_20260222_abc12345"
        assert record.state is S.QUEUED
        assert record.task_id == "IS-219"

    def test_optional_fields_default_to_none(self):
        record = _make_record(
            task_id=None,
            source_agent=None,
            target_agent=None,
            dispatched_at=None,
            delivered_at=None,
            acked_at=None,
            expires_at=None,
            error=None,
        )
        assert record.task_id is None
        assert record.source_agent is None
        assert record.error is None

    def test_payload_summary_truncated_to_120_chars(self):
        long_payload = "x" * 200
        record = _make_record(payload_summary=long_payload)
        assert len(record.payload_summary) == 120
        assert record.payload_summary == "x" * 120

    def test_payload_summary_short_string_preserved(self):
        record = _make_record(payload_summary="short summary")
        assert record.payload_summary == "short summary"

    def test_payload_summary_exactly_120_chars_preserved(self):
        exactly = "a" * 120
        record = _make_record(payload_summary=exactly)
        assert record.payload_summary == exactly

    def test_payload_summary_defaults_to_empty_string(self):
        record = _make_record(payload_summary="")
        assert record.payload_summary == ""

    def test_state_default_is_queued(self):
        record = DeliveryRecord(
            message_id="msg_x",
            source_node="nova",
            target_node="sati",
            created_at=_NOW,
        )
        assert record.state is S.QUEUED

    def test_state_accepts_enum_member(self):
        record = _make_record(state=S.DISPATCHED)
        assert record.state is S.DISPATCHED

    def test_state_accepts_string_value(self):
        record = _make_record(state="dispatched")
        assert record.state is S.DISPATCHED

    def test_invalid_state_string_raises_validation_error(self):
        with pytest.raises(ValidationError):
            _make_record(state="not_a_real_state")

    def test_missing_message_id_raises_validation_error(self):
        with pytest.raises(ValidationError):
            DeliveryRecord(
                source_node="nova",
                target_node="sati",
                created_at=_NOW,
            )

    def test_missing_source_node_raises_validation_error(self):
        with pytest.raises(ValidationError):
            DeliveryRecord(
                message_id="msg_x",
                target_node="sati",
                created_at=_NOW,
            )

    def test_missing_created_at_raises_validation_error(self):
        with pytest.raises(ValidationError):
            DeliveryRecord(
                message_id="msg_x",
                source_node="nova",
                target_node="sati",
            )

    def test_timestamps_are_preserved(self):
        dispatched = _NOW + timedelta(seconds=1)
        delivered = _NOW + timedelta(seconds=2)
        acked = _NOW + timedelta(seconds=5)
        expires = _NOW + timedelta(minutes=30)
        record = _make_record(
            dispatched_at=dispatched,
            delivered_at=delivered,
            acked_at=acked,
            expires_at=expires,
        )
        assert record.dispatched_at == dispatched
        assert record.delivered_at == delivered
        assert record.acked_at == acked
        assert record.expires_at == expires

    def test_error_field_stores_message(self):
        record = _make_record(state=S.FAILED, error="Connection refused to sati:2222")
        assert record.error == "Connection refused to sati:2222"

    def test_error_field_none_when_not_failed(self):
        record = _make_record(state=S.QUEUED, error=None)
        assert record.error is None

    def test_all_delivery_states_accepted_by_record(self):
        for state in DeliveryState:
            record = _make_record(state=state)
            assert record.state is state


# ===========================================================================
# 5. DeliveryStats computation
# ===========================================================================


class TestDeliveryStats:
    """Aggregation logic in DeliveryStats.from_records."""

    def test_empty_list_produces_zero_total(self):
        stats = DeliveryStats.from_records([])
        assert stats.total == 0

    def test_empty_list_produces_none_mean(self):
        stats = DeliveryStats.from_records([])
        assert stats.mean_delivery_seconds is None

    def test_empty_list_produces_empty_by_state(self):
        stats = DeliveryStats.from_records([])
        assert stats.by_state == {}

    def test_total_count_matches_input_length(self):
        records = [_make_record() for _ in range(5)]
        stats = DeliveryStats.from_records(records)
        assert stats.total == 5

    def test_by_state_counts_are_accurate(self):
        records = [
            _make_record(state=S.QUEUED),
            _make_record(state=S.QUEUED),
            _make_record(state=S.DISPATCHED),
            _make_record(state=S.ACKED, acked_at=_NOW + timedelta(seconds=10)),
        ]
        stats = DeliveryStats.from_records(records)
        assert stats.by_state.get("queued") == 2
        assert stats.by_state.get("dispatched") == 1
        assert stats.by_state.get("acked") == 1

    def test_by_state_omits_zero_count_states(self):
        records = [_make_record(state=S.FAILED)]
        stats = DeliveryStats.from_records(records)
        # Only 'failed' should appear — states with 0 count are excluded
        assert "failed" in stats.by_state
        assert "queued" not in stats.by_state

    def test_mean_delivery_seconds_computed_from_acked_records(self):
        records = [
            _make_record(
                state=S.ACKED,
                created_at=_NOW,
                acked_at=_NOW + timedelta(seconds=10),
            ),
            _make_record(
                state=S.ACKED,
                created_at=_NOW,
                acked_at=_NOW + timedelta(seconds=30),
            ),
        ]
        stats = DeliveryStats.from_records(records)
        assert stats.mean_delivery_seconds == pytest.approx(20.0)

    def test_mean_delivery_only_counts_acked_at_set_records(self):
        records = [
            _make_record(state=S.QUEUED),  # no acked_at — excluded from mean
            _make_record(
                state=S.ACKED,
                created_at=_NOW,
                acked_at=_NOW + timedelta(seconds=60),
            ),
        ]
        stats = DeliveryStats.from_records(records)
        assert stats.mean_delivery_seconds == pytest.approx(60.0)

    def test_mean_delivery_none_when_no_acked_records(self):
        records = [
            _make_record(state=S.QUEUED),
            _make_record(state=S.DISPATCHED),
        ]
        stats = DeliveryStats.from_records(records)
        assert stats.mean_delivery_seconds is None

    def test_stats_with_mixed_terminal_states(self):
        records = [
            _make_record(state=S.ACKED, acked_at=_NOW + timedelta(seconds=5)),
            _make_record(state=S.NACKED),
            _make_record(state=S.FAILED),
            _make_record(state=S.EXPIRED),
        ]
        stats = DeliveryStats.from_records(records)
        assert stats.total == 4
        assert stats.by_state["acked"] == 1
        assert stats.by_state["nacked"] == 1
        assert stats.by_state["failed"] == 1
        assert stats.by_state["expired"] == 1

    def test_delivery_stats_direct_construction(self):
        stats = DeliveryStats(
            total=10,
            by_state={"queued": 3, "acked": 7},
            mean_delivery_seconds=15.5,
        )
        assert stats.total == 10
        assert stats.mean_delivery_seconds == pytest.approx(15.5)


# ===========================================================================
# 6. JSON serialization round-trip
# ===========================================================================


class TestSerializationRoundTrip:
    """model_dump / model_validate round-trips through JSON."""

    def test_delivery_record_round_trips_via_dict(self):
        original = _make_record(
            state=S.DISPATCHED,
            dispatched_at=_NOW + timedelta(seconds=1),
            expires_at=_NOW + timedelta(minutes=30),
        )
        data = original.model_dump()
        restored = DeliveryRecord.model_validate(data)
        assert restored.message_id == original.message_id
        assert restored.state is original.state
        assert restored.source_node == original.source_node
        assert restored.target_node == original.target_node

    def test_delivery_record_round_trips_via_json_string(self):
        original = _make_record(state=S.ACKED, acked_at=_NOW + timedelta(seconds=10))
        json_str = original.model_dump_json()
        data = json.loads(json_str)
        restored = DeliveryRecord.model_validate(data)
        assert restored.message_id == original.message_id
        assert restored.state is S.ACKED

    def test_delivery_state_serializes_as_string_value(self):
        record = _make_record(state=S.DELIVERED)
        data = record.model_dump()
        # state must serialize to its string value, not the enum member name
        assert data["state"] == "delivered"

    def test_null_optional_fields_preserved_in_round_trip(self):
        original = _make_record(
            task_id=None,
            dispatched_at=None,
            acked_at=None,
            error=None,
        )
        data = original.model_dump()
        restored = DeliveryRecord.model_validate(data)
        assert restored.task_id is None
        assert restored.acked_at is None
        assert restored.error is None

    def test_delivery_stats_round_trips_via_dict(self):
        original = DeliveryStats(
            total=3,
            by_state={"queued": 1, "acked": 2},
            mean_delivery_seconds=12.5,
        )
        data = original.model_dump()
        restored = DeliveryStats.model_validate(data)
        assert restored.total == 3
        assert restored.by_state == {"queued": 1, "acked": 2}
        assert restored.mean_delivery_seconds == pytest.approx(12.5)


# ===========================================================================
# 7. String-valued state acceptance
# ===========================================================================


class TestStringStateAcceptance:
    """validate_delivery_transition must work with raw string values."""

    def test_string_from_and_to_states_accepted(self):
        assert validate_delivery_transition("queued", "dispatched") is True

    def test_mixed_string_and_enum_from(self):
        assert validate_delivery_transition("queued", S.DISPATCHED) is True

    def test_mixed_string_and_enum_to(self):
        assert validate_delivery_transition(S.QUEUED, "dispatched") is True

    def test_invalid_string_transition_raises_transition_error(self):
        with pytest.raises(DeliveryTransitionError):
            validate_delivery_transition("queued", "acked")

    def test_unknown_from_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_delivery_transition("not_real", "dispatched")

    def test_unknown_to_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_delivery_transition("queued", "not_real")

    def test_full_happy_path_as_strings(self):
        """Walk the full nominal path using raw string values."""
        path = ["queued", "dispatched", "delivered", "acked"]
        for from_s, to_s in zip(path, path[1:]):
            assert validate_delivery_transition(from_s, to_s) is True


# ===========================================================================
# 8. Contract completeness
# ===========================================================================


class TestContractCompleteness:
    """Transition table must cover every DeliveryState member."""

    def test_every_state_has_a_table_entry(self):
        for state in DeliveryState:
            assert state in VALID_DELIVERY_TRANSITIONS, (
                f"State {state.value!r} is missing from VALID_DELIVERY_TRANSITIONS"
            )

    def test_all_target_states_are_valid_enum_members(self):
        for from_s, targets in VALID_DELIVERY_TRANSITIONS.items():
            for to_s in targets:
                assert isinstance(to_s, DeliveryState), (
                    f"Target {to_s!r} for {from_s.value!r} is not a DeliveryState"
                )

    def test_all_states_coverable_from_queued(self):
        """All non-terminal states are reachable from QUEUED via valid transitions."""
        reachable: set[DeliveryState] = {S.QUEUED}
        queue = [S.QUEUED]
        while queue:
            current = queue.pop()
            for next_state in VALID_DELIVERY_TRANSITIONS.get(current, frozenset()):
                if next_state not in reachable:
                    reachable.add(next_state)
                    queue.append(next_state)
        # Every state should be reachable (the graph is connected from QUEUED)
        for state in DeliveryState:
            assert state in reachable, f"{state.value!r} is not reachable from QUEUED"

    def test_transition_error_is_subclass_of_value_error(self):
        with pytest.raises(ValueError):
            validate_delivery_transition(S.ACKED, S.QUEUED)

    def test_delivery_state_enum_has_correct_values(self):
        assert S.QUEUED.value == "queued"
        assert S.DISPATCHED.value == "dispatched"
        assert S.DELIVERED.value == "delivered"
        assert S.ACKED.value == "acked"
        assert S.NACKED.value == "nacked"
        assert S.FAILED.value == "failed"
        assert S.EXPIRED.value == "expired"
