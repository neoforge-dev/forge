"""Tests for the FORGE control-plane TaskLease model (CP-2001).

Covers:
1. LeaseState enum values are correct strings.
2. VALID_LEASE_TRANSITIONS contract completeness — every state is present.
3. All valid transitions succeed (parametrized full matrix).
4. Invalid transitions raise LeaseTransitionError.
5. validate_lease_transition accepts raw string arguments.
6. TaskLease default values and field constraints.
7. TaskLease.claim() happy path and illegal-state error cases.
8. TaskLease.renew() with renewal counting and max_renewals semantics.
9. TaskLease.release() clears path_lock and transitions to releasing.
10. TaskLease.is_expired() with time-frozen comparisons.
11. LeaseStats.from_leases() aggregation logic.
12. JSON serialisation round-trip (model_dump / model_validate).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from forge_harness.models.lease import (
    VALID_LEASE_TRANSITIONS,
    LeaseState,
    LeaseStats,
    LeaseTransitionError,
    TaskLease,
    validate_lease_transition,
)

# ---------------------------------------------------------------------------
# Shorthand alias
# ---------------------------------------------------------------------------

S = LeaseState

# ---------------------------------------------------------------------------
# Fixed reference time (UTC naive, consistent with datetime.utcnow() usage)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 2, 22, 12, 0, 0)
_FUTURE = _NOW + timedelta(seconds=600)
_PAST = _NOW - timedelta(seconds=1)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_lease(**overrides) -> TaskLease:
    """Construct a minimal valid TaskLease with sensible defaults."""
    defaults: dict = dict(
        task_id="CP-2001",
        owner_node="nova",
        owner_agent="forge:claude",
        state=S.UNCLAIMED,
    )
    defaults.update(overrides)
    return TaskLease(**defaults)


def _claimed_lease(**overrides) -> TaskLease:
    """Return a lease already in the CLAIMED state, with owner populated."""
    lease = _make_lease(
        state=S.CLAIMED,
        owner_node="nova",
        owner_agent="forge:claude",
        claimed_at=_NOW,
        expires_at=_FUTURE,
        **overrides,
    )
    return lease


def _active_lease(**overrides) -> TaskLease:
    """Return a lease already in the ACTIVE state."""
    lease = _make_lease(
        state=S.ACTIVE,
        owner_node="nova",
        owner_agent="forge:claude",
        claimed_at=_NOW,
        expires_at=_FUTURE,
        **overrides,
    )
    return lease


# ===========================================================================
# 1. LeaseState enum values
# ===========================================================================


class TestLeaseStateEnum:
    """Enum members carry the correct lowercase string values."""

    def test_unclaimed_value(self):
        assert S.UNCLAIMED.value == "unclaimed"

    def test_claimed_value(self):
        assert S.CLAIMED.value == "claimed"

    def test_active_value(self):
        assert S.ACTIVE.value == "active"

    def test_renewing_value(self):
        assert S.RENEWING.value == "renewing"

    def test_releasing_value(self):
        assert S.RELEASING.value == "releasing"

    def test_expired_value(self):
        assert S.EXPIRED.value == "expired"

    def test_requeued_value(self):
        assert S.REQUEUED.value == "requeued"

    def test_total_state_count(self):
        assert len(LeaseState) == 7

    def test_lease_state_is_str_subclass(self):
        assert isinstance(S.UNCLAIMED, str)


# ===========================================================================
# 2. VALID_LEASE_TRANSITIONS contract completeness
# ===========================================================================


class TestTransitionTableCompleteness:
    """Transition table must include every LeaseState as a key."""

    def test_every_state_has_a_table_entry(self):
        for state in LeaseState:
            assert state in VALID_LEASE_TRANSITIONS, (
                f"State {state.value!r} is missing from VALID_LEASE_TRANSITIONS"
            )

    def test_all_target_states_are_valid_enum_members(self):
        for from_s, targets in VALID_LEASE_TRANSITIONS.items():
            for to_s in targets:
                assert isinstance(to_s, LeaseState), (
                    f"Target {to_s!r} for {from_s.value!r} is not a LeaseState"
                )

    def test_unclaimed_can_reach_claimed(self):
        assert S.CLAIMED in VALID_LEASE_TRANSITIONS[S.UNCLAIMED]

    def test_claimed_can_reach_active(self):
        assert S.ACTIVE in VALID_LEASE_TRANSITIONS[S.CLAIMED]

    def test_active_can_reach_renewing(self):
        assert S.RENEWING in VALID_LEASE_TRANSITIONS[S.ACTIVE]

    def test_renewing_can_return_to_active(self):
        assert S.ACTIVE in VALID_LEASE_TRANSITIONS[S.RENEWING]

    def test_requeued_can_reach_claimed(self):
        assert S.CLAIMED in VALID_LEASE_TRANSITIONS[S.REQUEUED]

    def test_transition_values_are_frozensets(self):
        for targets in VALID_LEASE_TRANSITIONS.values():
            assert isinstance(targets, frozenset)


# ===========================================================================
# 3. Valid transitions — full parametrized matrix
# ===========================================================================


class TestValidTransitions:
    """Every (from, to) pair in VALID_LEASE_TRANSITIONS must return True."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            (from_s, to_s)
            for from_s, targets in VALID_LEASE_TRANSITIONS.items()
            for to_s in sorted(targets, key=lambda s: s.value)
        ],
    )
    def test_valid_pair_returns_true(self, from_state: LeaseState, to_state: LeaseState):
        assert validate_lease_transition(from_state, to_state) is True

    def test_unclaimed_to_claimed(self):
        assert validate_lease_transition(S.UNCLAIMED, S.CLAIMED) is True

    def test_claimed_to_active(self):
        assert validate_lease_transition(S.CLAIMED, S.ACTIVE) is True

    def test_claimed_to_releasing(self):
        assert validate_lease_transition(S.CLAIMED, S.RELEASING) is True

    def test_claimed_to_expired(self):
        assert validate_lease_transition(S.CLAIMED, S.EXPIRED) is True

    def test_active_to_renewing(self):
        assert validate_lease_transition(S.ACTIVE, S.RENEWING) is True

    def test_active_to_releasing(self):
        assert validate_lease_transition(S.ACTIVE, S.RELEASING) is True

    def test_active_to_expired(self):
        assert validate_lease_transition(S.ACTIVE, S.EXPIRED) is True

    def test_renewing_to_active(self):
        assert validate_lease_transition(S.RENEWING, S.ACTIVE) is True

    def test_renewing_to_expired(self):
        assert validate_lease_transition(S.RENEWING, S.EXPIRED) is True

    def test_releasing_to_requeued(self):
        assert validate_lease_transition(S.RELEASING, S.REQUEUED) is True

    def test_releasing_to_unclaimed(self):
        assert validate_lease_transition(S.RELEASING, S.UNCLAIMED) is True

    def test_expired_to_requeued(self):
        assert validate_lease_transition(S.EXPIRED, S.REQUEUED) is True

    def test_expired_to_unclaimed(self):
        assert validate_lease_transition(S.EXPIRED, S.UNCLAIMED) is True

    def test_requeued_to_claimed(self):
        assert validate_lease_transition(S.REQUEUED, S.CLAIMED) is True


# ===========================================================================
# 4. Invalid transitions raise LeaseTransitionError
# ===========================================================================


class TestInvalidTransitions:
    """Illegal (from, to) pairs must raise LeaseTransitionError."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            # Backwards moves
            (S.CLAIMED, S.UNCLAIMED),
            (S.ACTIVE, S.CLAIMED),
            (S.ACTIVE, S.UNCLAIMED),
            (S.RENEWING, S.CLAIMED),
            (S.RENEWING, S.UNCLAIMED),
            (S.RENEWING, S.RELEASING),
            (S.REQUEUED, S.UNCLAIMED),
            (S.REQUEUED, S.ACTIVE),
            # Skipping states
            (S.UNCLAIMED, S.ACTIVE),
            (S.UNCLAIMED, S.RENEWING),
            (S.UNCLAIMED, S.RELEASING),
            (S.UNCLAIMED, S.EXPIRED),
            (S.UNCLAIMED, S.REQUEUED),
            (S.CLAIMED, S.RENEWING),
            (S.CLAIMED, S.REQUEUED),
            # Self-transitions (no state may transition to itself)
            (S.UNCLAIMED, S.UNCLAIMED),
            (S.CLAIMED, S.CLAIMED),
            (S.ACTIVE, S.ACTIVE),
            (S.RENEWING, S.RENEWING),
            (S.RELEASING, S.RELEASING),
            (S.EXPIRED, S.EXPIRED),
            (S.REQUEUED, S.REQUEUED),
        ],
    )
    def test_invalid_pair_raises_lease_transition_error(
        self, from_state: LeaseState, to_state: LeaseState
    ):
        with pytest.raises(LeaseTransitionError):
            validate_lease_transition(from_state, to_state)

    def test_error_carries_from_state(self):
        with pytest.raises(LeaseTransitionError) as exc_info:
            validate_lease_transition(S.UNCLAIMED, S.ACTIVE)
        assert exc_info.value.from_state is S.UNCLAIMED

    def test_error_carries_to_state(self):
        with pytest.raises(LeaseTransitionError) as exc_info:
            validate_lease_transition(S.UNCLAIMED, S.ACTIVE)
        assert exc_info.value.to_state is S.ACTIVE

    def test_error_carries_allowed_set(self):
        with pytest.raises(LeaseTransitionError) as exc_info:
            validate_lease_transition(S.UNCLAIMED, S.ACTIVE)
        assert S.CLAIMED in exc_info.value.allowed

    def test_error_message_contains_from_and_to(self):
        with pytest.raises(LeaseTransitionError) as exc_info:
            validate_lease_transition(S.ACTIVE, S.CLAIMED)
        msg = str(exc_info.value)
        assert "active" in msg
        assert "claimed" in msg

    def test_error_message_contains_allowed_states(self):
        with pytest.raises(LeaseTransitionError) as exc_info:
            validate_lease_transition(S.ACTIVE, S.UNCLAIMED)
        msg = str(exc_info.value)
        assert "renewing" in msg or "releasing" in msg or "expired" in msg

    def test_lease_transition_error_is_subclass_of_exception(self):
        with pytest.raises(Exception):
            validate_lease_transition(S.UNCLAIMED, S.EXPIRED)

    def test_error_message_terminal_branch_when_allowed_is_empty(self):
        """LeaseTransitionError with empty allowed set uses terminal-state message."""
        err = LeaseTransitionError(S.RELEASING, S.ACTIVE, frozenset())
        msg = str(err)
        assert "terminal" in msg
        assert err.allowed == frozenset()

    def test_unknown_from_state_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_lease_transition("ghost_state", S.CLAIMED)

    def test_unknown_to_state_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_lease_transition(S.UNCLAIMED, "ghost_state")


# ===========================================================================
# 5. String argument acceptance in validate_lease_transition
# ===========================================================================


class TestStringStateAcceptance:
    """validate_lease_transition must accept raw string values."""

    def test_string_from_and_to_accepted(self):
        assert validate_lease_transition("unclaimed", "claimed") is True

    def test_mixed_string_from_and_enum_to(self):
        assert validate_lease_transition("unclaimed", S.CLAIMED) is True

    def test_mixed_enum_from_and_string_to(self):
        assert validate_lease_transition(S.UNCLAIMED, "claimed") is True

    def test_invalid_string_transition_raises_transition_error(self):
        with pytest.raises(LeaseTransitionError):
            validate_lease_transition("unclaimed", "active")

    def test_full_nominal_path_as_strings(self):
        """Walk through the primary lifecycle using raw string values."""
        path = ["unclaimed", "claimed", "active", "releasing", "requeued", "claimed"]
        for from_s, to_s in zip(path, path[1:]):
            assert validate_lease_transition(from_s, to_s) is True


# ===========================================================================
# 6. TaskLease default values and field constraints
# ===========================================================================


class TestTaskLeaseDefaults:
    """Default field values and Pydantic constraints for TaskLease."""

    def test_lease_id_is_uuid4_format(self):
        lease = _make_lease()
        parsed = uuid.UUID(lease.lease_id, version=4)
        assert str(parsed) == lease.lease_id

    def test_each_instance_gets_unique_lease_id(self):
        a = _make_lease()
        b = _make_lease()
        assert a.lease_id != b.lease_id

    def test_default_state_is_unclaimed(self):
        lease = _make_lease()
        assert lease.state is S.UNCLAIMED

    def test_default_claimed_at_is_none(self):
        lease = _make_lease()
        assert lease.claimed_at is None

    def test_default_expires_at_is_none(self):
        lease = _make_lease()
        assert lease.expires_at is None

    def test_default_lease_duration_is_300(self):
        lease = _make_lease()
        assert lease.lease_duration_seconds == 300

    def test_default_renewals_is_zero(self):
        lease = _make_lease()
        assert lease.renewals == 0

    def test_default_max_renewals_is_ten(self):
        lease = _make_lease()
        assert lease.max_renewals == 10

    def test_default_path_lock_is_none(self):
        lease = _make_lease()
        assert lease.path_lock is None

    def test_default_metadata_is_empty_dict(self):
        lease = _make_lease()
        assert lease.metadata == {}

    def test_created_at_is_datetime(self):
        lease = _make_lease()
        assert isinstance(lease.created_at, datetime)

    def test_updated_at_is_datetime(self):
        lease = _make_lease()
        assert isinstance(lease.updated_at, datetime)

    def test_task_id_is_required(self):
        with pytest.raises((ValidationError, TypeError)):
            TaskLease()

    def test_state_accepts_string_value(self):
        lease = _make_lease(state="unclaimed")
        assert lease.state is S.UNCLAIMED

    def test_invalid_state_string_raises_validation_error(self):
        with pytest.raises(ValidationError):
            _make_lease(state="not_a_real_state")

    def test_path_lock_stores_file_path(self):
        lease = _make_lease(path_lock="src/api/routes.py")
        assert lease.path_lock == "src/api/routes.py"

    def test_metadata_stores_arbitrary_data(self):
        meta = {"sprint": "S6", "priority": 1, "tags": ["backend"]}
        lease = _make_lease(metadata=meta)
        assert lease.metadata["sprint"] == "S6"
        assert lease.metadata["tags"] == ["backend"]

    def test_lease_duration_must_be_positive(self):
        with pytest.raises(ValidationError):
            _make_lease(lease_duration_seconds=0)

    def test_renewals_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            _make_lease(renewals=-1)


# ===========================================================================
# 7. TaskLease.claim()
# ===========================================================================


class TestTaskLeaseClaim:
    """claim() happy path, owner population, and error cases."""

    def test_claim_from_unclaimed_succeeds(self):
        lease = _make_lease()
        lease.claim("nova", "forge:claude")
        assert lease.state is S.CLAIMED

    def test_claim_sets_owner_node(self):
        lease = _make_lease()
        lease.claim("prya", "forge:glm")
        assert lease.owner_node == "prya"

    def test_claim_sets_owner_agent(self):
        lease = _make_lease()
        lease.claim("nova", "forge:claude")
        assert lease.owner_agent == "forge:claude"

    def test_claim_sets_claimed_at(self):
        lease = _make_lease()
        before = datetime.utcnow()
        lease.claim("nova", "forge:claude")
        after = datetime.utcnow()
        assert before <= lease.claimed_at <= after

    def test_claim_sets_expires_at(self):
        lease = _make_lease(lease_duration_seconds=300)
        lease.claim("nova", "forge:claude")
        assert lease.expires_at is not None
        delta = (lease.expires_at - lease.claimed_at).total_seconds()
        assert 298 <= delta <= 302  # allow 2-second execution tolerance

    def test_claim_respects_lease_duration_seconds(self):
        lease = _make_lease(lease_duration_seconds=60)
        lease.claim("nova", "forge:claude")
        delta = (lease.expires_at - lease.claimed_at).total_seconds()
        assert 58 <= delta <= 62

    def test_claim_updates_updated_at(self):
        lease = _make_lease()
        before = datetime.utcnow()
        lease.claim("nova", "forge:claude")
        assert lease.updated_at >= before

    def test_claim_from_requeued_succeeds(self):
        lease = _make_lease(state=S.REQUEUED)
        lease.claim("nova", "forge:claude")
        assert lease.state is S.CLAIMED

    def test_claim_from_active_raises_transition_error(self):
        lease = _active_lease()
        with pytest.raises(LeaseTransitionError):
            lease.claim("nova", "forge:claude")

    def test_claim_from_claimed_raises_transition_error(self):
        lease = _claimed_lease()
        with pytest.raises(LeaseTransitionError):
            lease.claim("nova", "forge:claude")

    def test_claim_from_releasing_raises_transition_error(self):
        lease = _make_lease(state=S.RELEASING)
        with pytest.raises(LeaseTransitionError):
            lease.claim("nova", "forge:claude")

    def test_claim_from_expired_raises_transition_error(self):
        lease = _make_lease(state=S.EXPIRED)
        with pytest.raises(LeaseTransitionError):
            lease.claim("nova", "forge:claude")

    def test_claim_from_renewing_raises_transition_error(self):
        lease = _make_lease(state=S.RENEWING)
        with pytest.raises(LeaseTransitionError):
            lease.claim("nova", "forge:claude")


# ===========================================================================
# 8. TaskLease.renew()
# ===========================================================================


class TestTaskLeaseRenew:
    """renew() extends expires_at, increments renewals, and enforces state."""

    def test_renew_from_active_succeeds(self):
        lease = _active_lease()
        lease.renew()
        assert lease.state is S.ACTIVE

    def test_renew_increments_renewals_counter(self):
        lease = _active_lease()
        assert lease.renewals == 0
        lease.renew()
        assert lease.renewals == 1

    def test_renew_twice_increments_to_two(self):
        lease = _active_lease()
        lease.renew()
        lease.renew()
        assert lease.renewals == 2

    def test_renew_extends_expires_at(self):
        old_expires = _FUTURE
        lease = _make_lease(
            state=S.ACTIVE,
            owner_node="nova",
            owner_agent="forge:claude",
            claimed_at=_NOW,
            expires_at=old_expires,
            lease_duration_seconds=300,
        )
        lease.renew()
        assert lease.expires_at > old_expires

    def test_renew_with_custom_duration(self):
        lease = _active_lease(lease_duration_seconds=300)
        lease.renew(duration=60)
        delta = (lease.expires_at - datetime.utcnow()).total_seconds()
        assert 58 <= delta <= 62

    def test_renew_updates_updated_at(self):
        lease = _active_lease()
        before = datetime.utcnow()
        lease.renew()
        assert lease.updated_at >= before

    def test_renew_from_unclaimed_raises_transition_error(self):
        lease = _make_lease()
        with pytest.raises(LeaseTransitionError):
            lease.renew()

    def test_renew_from_claimed_raises_transition_error(self):
        lease = _claimed_lease()
        with pytest.raises(LeaseTransitionError):
            lease.renew()

    def test_renew_from_releasing_raises_transition_error(self):
        lease = _make_lease(state=S.RELEASING)
        with pytest.raises(LeaseTransitionError):
            lease.renew()

    def test_renew_from_expired_raises_transition_error(self):
        lease = _make_lease(state=S.EXPIRED)
        with pytest.raises(LeaseTransitionError):
            lease.renew()

    def test_renew_from_requeued_raises_transition_error(self):
        lease = _make_lease(state=S.REQUEUED)
        with pytest.raises(LeaseTransitionError):
            lease.renew()

    def test_max_renewals_is_not_enforced_by_renew_itself(self):
        """renew() does not enforce max_renewals — caller responsibility."""
        lease = _active_lease(max_renewals=2)
        for _ in range(5):
            lease.renew()
        assert lease.renewals == 5

    def test_renewals_field_tracks_cumulative_count(self):
        lease = _active_lease()
        for i in range(1, 6):
            lease.renew()
            assert lease.renewals == i


# ===========================================================================
# 9. TaskLease.release()
# ===========================================================================


class TestTaskLeaseRelease:
    """release() transitions to releasing and clears path_lock."""

    def test_release_from_active_succeeds(self):
        lease = _active_lease()
        lease.release()
        assert lease.state is S.RELEASING

    def test_release_from_claimed_succeeds(self):
        lease = _claimed_lease()
        lease.release()
        assert lease.state is S.RELEASING

    def test_release_clears_path_lock(self):
        lease = _active_lease(path_lock="harness/forge_harness/models/lease.py")
        assert lease.path_lock is not None
        lease.release()
        assert lease.path_lock is None

    def test_release_clears_path_lock_when_already_none(self):
        lease = _active_lease(path_lock=None)
        lease.release()
        assert lease.path_lock is None

    def test_release_updates_updated_at(self):
        lease = _active_lease()
        before = datetime.utcnow()
        lease.release()
        assert lease.updated_at >= before

    def test_release_from_unclaimed_raises_transition_error(self):
        lease = _make_lease()
        with pytest.raises(LeaseTransitionError):
            lease.release()

    def test_release_from_renewing_raises_transition_error(self):
        lease = _make_lease(state=S.RENEWING)
        with pytest.raises(LeaseTransitionError):
            lease.release()

    def test_release_from_releasing_raises_transition_error(self):
        lease = _make_lease(state=S.RELEASING)
        with pytest.raises(LeaseTransitionError):
            lease.release()

    def test_release_from_expired_raises_transition_error(self):
        lease = _make_lease(state=S.EXPIRED)
        with pytest.raises(LeaseTransitionError):
            lease.release()

    def test_release_from_requeued_raises_transition_error(self):
        lease = _make_lease(state=S.REQUEUED)
        with pytest.raises(LeaseTransitionError):
            lease.release()


# ===========================================================================
# 10. TaskLease.is_expired()
# ===========================================================================


class TestIsExpired:
    """is_expired() checks whether the current UTC time exceeds expires_at."""

    def test_is_expired_false_when_expires_at_is_none(self):
        lease = _make_lease()
        assert lease.is_expired() is False

    def test_is_expired_false_when_expires_at_in_future(self):
        lease = _make_lease(expires_at=datetime.utcnow() + timedelta(hours=1))
        assert lease.is_expired() is False

    def test_is_expired_true_when_expires_at_in_past(self):
        lease = _make_lease(expires_at=datetime.utcnow() - timedelta(seconds=1))
        assert lease.is_expired() is True

    def test_is_expired_with_frozen_time_before_expiry(self):
        expires = _NOW + timedelta(seconds=10)
        lease = _make_lease(expires_at=expires)
        # Freeze time to before expiry
        with patch("forge_harness.models.lease.datetime") as mock_dt:
            mock_dt.utcnow.return_value = _NOW
            result = lease.is_expired()
        assert result is False

    def test_is_expired_with_frozen_time_after_expiry(self):
        expires = _NOW - timedelta(seconds=10)
        lease = _make_lease(expires_at=expires)
        # Freeze time to after expiry
        with patch("forge_harness.models.lease.datetime") as mock_dt:
            mock_dt.utcnow.return_value = _NOW
            result = lease.is_expired()
        assert result is True

    def test_is_expired_boundary_exactly_at_expiry(self):
        """is_expired uses strict greater-than: expires_at == now is NOT expired."""
        expires = _NOW
        lease = _make_lease(expires_at=expires)
        with patch("forge_harness.models.lease.datetime") as mock_dt:
            mock_dt.utcnow.return_value = _NOW
            result = lease.is_expired()
        assert result is False


# ===========================================================================
# 11. LeaseStats.from_leases() aggregation
# ===========================================================================


class TestLeaseStatsAggregation:
    """Aggregation logic in LeaseStats.from_leases."""

    def test_empty_list_total_is_zero(self):
        stats = LeaseStats.from_leases([])
        assert stats.total == 0

    def test_empty_list_by_state_is_empty(self):
        stats = LeaseStats.from_leases([])
        assert stats.by_state == {}

    def test_empty_list_active_path_locks_is_zero(self):
        stats = LeaseStats.from_leases([])
        assert stats.active_path_locks == 0

    def test_empty_list_expired_count_is_zero(self):
        stats = LeaseStats.from_leases([])
        assert stats.expired_count == 0

    def test_empty_list_mean_renewals_is_none(self):
        stats = LeaseStats.from_leases([])
        assert stats.mean_renewals is None

    def test_total_matches_input_length(self):
        leases = [_make_lease() for _ in range(7)]
        stats = LeaseStats.from_leases(leases)
        assert stats.total == 7

    def test_by_state_counts_unclaimed(self):
        leases = [_make_lease(state=S.UNCLAIMED) for _ in range(3)]
        stats = LeaseStats.from_leases(leases)
        assert stats.by_state["unclaimed"] == 3

    def test_by_state_counts_multiple_states(self):
        leases = [
            _make_lease(state=S.UNCLAIMED),
            _make_lease(state=S.CLAIMED),
            _make_lease(state=S.CLAIMED),
            _active_lease(),
        ]
        stats = LeaseStats.from_leases(leases)
        assert stats.by_state["unclaimed"] == 1
        assert stats.by_state["claimed"] == 2
        assert stats.by_state["active"] == 1

    def test_by_state_omits_zero_count_states(self):
        leases = [_make_lease(state=S.EXPIRED)]
        stats = LeaseStats.from_leases(leases)
        assert "expired" in stats.by_state
        assert "unclaimed" not in stats.by_state

    def test_active_path_locks_counts_non_none_path_lock(self):
        leases = [
            _active_lease(path_lock="src/foo.py"),
            _active_lease(path_lock="src/bar.py"),
            _active_lease(path_lock=None),
        ]
        stats = LeaseStats.from_leases(leases)
        assert stats.active_path_locks == 2

    def test_expired_count_counts_expired_state(self):
        leases = [
            _make_lease(state=S.EXPIRED),
            _make_lease(state=S.EXPIRED),
            _make_lease(state=S.UNCLAIMED),
        ]
        stats = LeaseStats.from_leases(leases)
        assert stats.expired_count == 2

    def test_mean_renewals_computed_from_renewed_leases(self):
        leases = [
            _active_lease(renewals=2),
            _active_lease(renewals=4),
        ]
        stats = LeaseStats.from_leases(leases)
        assert stats.mean_renewals == pytest.approx(3.0)

    def test_mean_renewals_excludes_zero_renewal_leases(self):
        leases = [
            _active_lease(renewals=0),  # excluded from mean
            _active_lease(renewals=6),
        ]
        stats = LeaseStats.from_leases(leases)
        assert stats.mean_renewals == pytest.approx(6.0)

    def test_mean_renewals_none_when_no_renewals(self):
        leases = [_active_lease(renewals=0), _make_lease()]
        stats = LeaseStats.from_leases(leases)
        assert stats.mean_renewals is None

    def test_from_leases_direct_construction(self):
        stats = LeaseStats(
            total=5,
            by_state={"active": 3, "expired": 2},
            active_path_locks=1,
            expired_count=2,
            mean_renewals=1.5,
        )
        assert stats.total == 5
        assert stats.mean_renewals == pytest.approx(1.5)


# ===========================================================================
# 12. JSON serialisation round-trip
# ===========================================================================


class TestSerializationRoundTrip:
    """model_dump / model_validate round-trips through JSON."""

    def test_task_lease_round_trips_via_dict(self):
        original = _make_lease(
            state=S.UNCLAIMED,
            path_lock="src/api/routes.py",
            metadata={"sprint": "S6"},
        )
        data = original.model_dump()
        restored = TaskLease.model_validate(data)
        assert restored.lease_id == original.lease_id
        assert restored.task_id == original.task_id
        assert restored.state is original.state
        assert restored.path_lock == original.path_lock
        assert restored.metadata == original.metadata

    def test_task_lease_round_trips_via_json_string(self):
        original = _make_lease(
            state=S.ACTIVE,
            owner_node="nova",
            owner_agent="forge:claude",
            claimed_at=_NOW,
            expires_at=_FUTURE,
        )
        json_str = original.model_dump_json()
        data = json.loads(json_str)
        restored = TaskLease.model_validate(data)
        assert restored.lease_id == original.lease_id
        assert restored.state is S.ACTIVE

    def test_lease_state_serialises_as_string_value(self):
        lease = _active_lease()
        data = lease.model_dump()
        assert data["state"] == "active"

    def test_null_optional_fields_preserved_in_round_trip(self):
        original = _make_lease(
            claimed_at=None,
            expires_at=None,
            path_lock=None,
        )
        data = original.model_dump()
        restored = TaskLease.model_validate(data)
        assert restored.claimed_at is None
        assert restored.expires_at is None
        assert restored.path_lock is None

    def test_metadata_dict_round_trips(self):
        meta = {"key": "value", "nested": {"a": 1}}
        original = _make_lease(metadata=meta)
        data = original.model_dump()
        restored = TaskLease.model_validate(data)
        assert restored.metadata == meta

    def test_lease_stats_round_trips_via_dict(self):
        original = LeaseStats(
            total=4,
            by_state={"active": 2, "expired": 2},
            active_path_locks=1,
            expired_count=2,
            mean_renewals=3.0,
        )
        data = original.model_dump()
        restored = LeaseStats.model_validate(data)
        assert restored.total == 4
        assert restored.expired_count == 2
        assert restored.mean_renewals == pytest.approx(3.0)

    def test_all_lease_states_accepted_by_task_lease(self):
        for state in LeaseState:
            lease = _make_lease(state=state)
            assert lease.state is state

    def test_lease_id_survives_round_trip(self):
        original = _make_lease()
        data = original.model_dump()
        restored = TaskLease.model_validate(data)
        assert restored.lease_id == original.lease_id
        uuid.UUID(restored.lease_id, version=4)  # assert valid UUID4
