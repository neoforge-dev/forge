"""CI contract gate for the FORGE Dark Factory task lifecycle state machine.

Purpose
-------
This module is a *drift detector*, not a functional test.  It freezes a
SHA-256 hash of the serialized ``VALID_TRANSITIONS`` table so that any
unauthorised edit to the state machine causes CI to fail immediately.

If you intentionally change the state machine, you MUST update
``FROZEN_TRANSITION_HASH`` below.  The easiest way to regenerate it:

    cd harness
    uv run python -c "
    import hashlib, json
    from forge_harness.models.task_lifecycle import VALID_TRANSITIONS

    data = {s.value: sorted([t.value for t in targets])
            for s, targets in VALID_TRANSITIONS.items()}
    print(hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest())
    "

Then paste the printed hash as the new value of ``FROZEN_TRANSITION_HASH``.

Tests
-----
1. ``test_transition_hash_matches_frozen_value`` — Hash gate.  Fails on any
   structural change to the transition table, whether a new edge was added,
   an edge was removed, or a state was renamed.

2. ``test_every_state_has_a_transition_table_entry`` — Completeness.  Every
   member of ``TaskLifecycleState`` must appear as a key in
   ``VALID_TRANSITIONS``; missing entries mean the scheduler has no policy for
   that state.

3. ``test_all_target_states_are_valid_enum_members`` — Referential integrity.
   Every value in every target frozenset must resolve to a known
   ``TaskLifecycleState`` member.  Catches typos or stale string literals.

4. ``test_terminal_states_have_no_outbound_transitions`` — Terminal-state
   invariant.  States whose outbound set is empty in ``VALID_TRANSITIONS``
   must match ``TERMINAL_STATES`` and must not silently gain edges.

5. ``test_terminal_states_constant_matches_derived_set`` — Consistency.
   ``TERMINAL_STATES`` (the convenience constant) must equal the set computed
   directly from ``VALID_TRANSITIONS`` so the two sources never diverge.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from forge_harness.models.task_lifecycle import (
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    TaskLifecycleState,
)

# ---------------------------------------------------------------------------
# Frozen contract hash
#
# This value was regenerated on 2026-02-22 (DF-1002) after adding BLOCKED state
# and evaluator_failed → blocked, evaluator_running → completed transitions.
# forge_harness/webhook_server/models/task_lifecycle.py.
#
# HOW TO UPDATE:
#   Run the snippet in the module docstring above, then replace the string
#   below with the new hash.  Commit the updated hash together with the
#   state-machine change so the PR diff shows both in context.
# ---------------------------------------------------------------------------

FROZEN_TRANSITION_HASH = "9a4f0c288ee41ed359ce135cd861f7673ca00af2a5b4905e80ce623a7af7c47b"


# ---------------------------------------------------------------------------
# Serialisation helper (must be identical to the generation recipe)
# ---------------------------------------------------------------------------


def compute_transition_hash() -> str:
    """Return a deterministic SHA-256 hex digest of ``VALID_TRANSITIONS``.

    The digest is computed over a canonicalised JSON document:
    - Keys are the string *values* of each source state (not enum names).
    - Each value is the *sorted* list of target state string values.
    - The outer object is serialised with ``sort_keys=True`` so insertion
      order in the dict never influences the hash.
    """
    data = {s.value: sorted([t.value for t in targets]) for s, targets in VALID_TRANSITIONS.items()}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


# ===========================================================================
# Test 1 — Hash gate (primary drift detector)
# ===========================================================================


class TestTransitionHashGate:
    """Detect any structural change to VALID_TRANSITIONS via a frozen hash."""

    def test_transition_hash_matches_frozen_value(self) -> None:
        """VALID_TRANSITIONS must match the frozen contract hash.

        If this test fails, the state machine has changed.  You have two
        choices:

        a) Revert the change to ``task_lifecycle.py`` if it was unintentional.
        b) Regenerate the hash (see module docstring) and update
           ``FROZEN_TRANSITION_HASH`` in this file if the change was
           deliberate.

        Never update the hash without a corresponding PR review — the frozen
        hash is the enforcement mechanism for the change-control policy.
        """
        current_hash = compute_transition_hash()
        assert current_hash == FROZEN_TRANSITION_HASH, (
            f"\n\nTask lifecycle state machine has DRIFTED from the frozen contract.\n\n"
            f"  Frozen hash : {FROZEN_TRANSITION_HASH}\n"
            f"  Current hash: {current_hash}\n\n"
            f"If this change is intentional, regenerate the hash by running:\n\n"
            f"    cd harness\n"
            f'    uv run python -c "\n'
            f"    import hashlib, json\n"
            f"    from forge_harness.models.task_lifecycle import VALID_TRANSITIONS\n"
            f"    data = {{s.value: sorted([t.value for t in targets])\n"
            f"             for s, targets in VALID_TRANSITIONS.items()}}\n"
            f"    print(hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest())\n"
            f'    "\n\n'
            f"Then update FROZEN_TRANSITION_HASH in\n"
            f"tests/test_lifecycle_contract_gate.py and commit both files "
            f"together.\n"
        )


# ===========================================================================
# Test 2 — Completeness: every state has a table entry
# ===========================================================================


class TestTransitionTableCompleteness:
    """Every TaskLifecycleState member must appear as a key in VALID_TRANSITIONS."""

    @pytest.mark.parametrize(
        "state",
        list(TaskLifecycleState),
        ids=[s.value for s in TaskLifecycleState],
    )
    def test_every_state_has_a_transition_table_entry(self, state: TaskLifecycleState) -> None:
        """The scheduler must have an explicit policy for every known state.

        A missing entry means the webhook server has no defined behaviour when
        a task reaches that state, which is a silent contract gap.
        """
        assert state in VALID_TRANSITIONS, (
            f"State {state.value!r} is missing from VALID_TRANSITIONS.  "
            f"Add an entry (even an empty frozenset for terminal states) to "
            f"forge_harness/webhook_server/models/task_lifecycle.py."
        )

    def test_transition_table_covers_all_enum_members(self) -> None:
        """Bulk assertion: VALID_TRANSITIONS key count == TaskLifecycleState count."""
        all_states = set(TaskLifecycleState)
        table_keys = set(VALID_TRANSITIONS.keys())
        missing = all_states - table_keys
        extra = table_keys - all_states
        assert not missing and not extra, (
            f"VALID_TRANSITIONS coverage mismatch.\n"
            f"  States missing from table : {sorted(s.value for s in missing)}\n"
            f"  Table keys not in enum    : {sorted(s.value for s in extra)}"
        )


# ===========================================================================
# Test 3 — Referential integrity: all targets are valid enum members
# ===========================================================================


class TestTargetStateIntegrity:
    """Every target state in VALID_TRANSITIONS must be a real TaskLifecycleState."""

    @pytest.mark.parametrize(
        "from_state,to_state",
        [(from_s, to_s) for from_s, targets in VALID_TRANSITIONS.items() for to_s in targets],
        ids=[
            f"{from_s.value}->{to_s.value}"
            for from_s, targets in VALID_TRANSITIONS.items()
            for to_s in targets
        ],
    )
    def test_all_target_states_are_valid_enum_members(
        self, from_state: TaskLifecycleState, to_state: TaskLifecycleState
    ) -> None:
        """Target states must be TaskLifecycleState instances, not raw strings."""
        assert isinstance(to_state, TaskLifecycleState), (
            f"Target {to_state!r} in the transition set for {from_state.value!r} "
            f"is not a TaskLifecycleState member.  This indicates a stale string "
            f"literal or a rename that was not fully applied."
        )

    def test_no_target_state_references_unknown_value(self) -> None:
        """All target values must resolve when looked up in the enum."""
        for from_s, targets in VALID_TRANSITIONS.items():
            for to_s in targets:
                try:
                    resolved = TaskLifecycleState(to_s.value)
                except ValueError as exc:
                    pytest.fail(
                        f"Target {to_s!r} for {from_s.value!r} could not be "
                        f"resolved to a TaskLifecycleState: {exc}"
                    )
                assert resolved is to_s, (
                    f"Value round-trip failed for target {to_s!r} from "
                    f"{from_s.value!r}: resolved to {resolved!r}"
                )


# ===========================================================================
# Test 4 — Terminal states have no outbound transitions
# ===========================================================================


class TestTerminalStateInvariant:
    """States with an empty outbound set must truly be terminal."""

    @pytest.mark.parametrize(
        "state",
        sorted(TERMINAL_STATES, key=lambda s: s.value),
        ids=[s.value for s in sorted(TERMINAL_STATES, key=lambda s: s.value)],
    )
    def test_terminal_states_have_no_outbound_transitions(self, state: TaskLifecycleState) -> None:
        """A terminal state must have an empty frozenset in VALID_TRANSITIONS.

        If a state appears in TERMINAL_STATES but has outbound edges, the
        constant and the table are inconsistent — the scheduler would allow
        transitions it should block.
        """
        outbound = VALID_TRANSITIONS[state]
        assert outbound == frozenset(), (
            f"Terminal state {state.value!r} has unexpected outbound edges: "
            f"{sorted(t.value for t in outbound)}.  Either remove the edges "
            f"or remove {state.value!r} from TERMINAL_STATES."
        )

    def test_at_least_two_terminal_states_exist(self) -> None:
        """The lifecycle must have at least 'completed' and 'cancelled' as terminals."""
        assert TaskLifecycleState.COMPLETED in TERMINAL_STATES, (
            "'completed' must be a terminal state"
        )
        assert TaskLifecycleState.CANCELLED in TERMINAL_STATES, (
            "'cancelled' must be a terminal state"
        )
        assert len(TERMINAL_STATES) >= 2, (
            f"Expected at least 2 terminal states, found {len(TERMINAL_STATES)}: "
            f"{sorted(s.value for s in TERMINAL_STATES)}"
        )


# ===========================================================================
# Test 5 — TERMINAL_STATES constant matches the derived set
# ===========================================================================


class TestTerminalStatesConstantConsistency:
    """TERMINAL_STATES must equal the set derived directly from VALID_TRANSITIONS."""

    def test_terminal_states_constant_matches_derived_set(self) -> None:
        """Catch any divergence between the precomputed constant and the table.

        ``TERMINAL_STATES`` is defined as a convenience constant at module
        load time.  If ``VALID_TRANSITIONS`` is edited but the constant
        definition is cached or stale, the two could disagree.  This test
        ensures they are always in sync.
        """
        derived = frozenset(state for state, targets in VALID_TRANSITIONS.items() if not targets)
        assert TERMINAL_STATES == derived, (
            f"TERMINAL_STATES constant does not match the set derived from "
            f"VALID_TRANSITIONS.\n"
            f"  Constant : {sorted(s.value for s in TERMINAL_STATES)}\n"
            f"  Derived  : {sorted(s.value for s in derived)}\n\n"
            f"Update the TERMINAL_STATES definition in task_lifecycle.py to "
            f"match the transition table."
        )
