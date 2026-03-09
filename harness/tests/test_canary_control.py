"""Tests for Dark Factory Canary Control — DF-4004.

Coverage targets
----------------
- RolloutStage enum: all three values present
- TransitionType enum: all four values present
- RolloutTransition: to_dict output, frozen fields
- RolloutState: effective_stage with and without pause, to_dict output
- RolloutSummary: to_dict, frozenset serialization
- CanaryControl.__init__: empty state, empty transitions
- CanaryControl.set_stage: promote disabled→canary, canary→enabled
- CanaryControl.set_stage: revert enabled→canary via set_stage (explicit downgrade)
- CanaryControl.set_stage: same-stage no-op classified as promote
- CanaryControl.set_stage: empty operator raises ValueError
- CanaryControl.set_stage: empty node_id raises ValueError
- CanaryControl.set_stage: empty lane raises ValueError
- CanaryControl.set_stage: clears paused state on direct set
- CanaryControl.set_stage: reason propagated to transition
- CanaryControl.revert: enabled→canary one step
- CanaryControl.revert: canary→disabled one step
- CanaryControl.revert: disabled raises ValueError
- CanaryControl.revert: empty operator raises ValueError
- CanaryControl.revert: reason propagated to transition
- CanaryControl.revert: transition_type is revert
- CanaryControl.pause_node: pauses non-disabled lanes
- CanaryControl.pause_node: skips already-paused lanes
- CanaryControl.pause_node: skips disabled-stage lanes
- CanaryControl.pause_node: preserves pre_pause_stage for resume
- CanaryControl.pause_node: empty operator raises ValueError
- CanaryControl.pause_node: returns transitions list
- CanaryControl.pause_node: node not found returns empty list
- CanaryControl.pause_lane: pauses lane across all nodes
- CanaryControl.pause_lane: skips already-paused entries
- CanaryControl.pause_lane: skips disabled entries
- CanaryControl.pause_lane: empty operator raises ValueError
- CanaryControl.resume_node: restores all paused lanes
- CanaryControl.resume_node: skips non-paused lanes
- CanaryControl.resume_node: empty operator raises ValueError
- CanaryControl.resume_node: transition_type is resume
- CanaryControl.resume_lane: restores across all nodes
- CanaryControl.resume_lane: skips non-paused entries
- CanaryControl.resume_lane: empty operator raises ValueError
- CanaryControl.is_autonomous_allowed: True for canary stage
- CanaryControl.is_autonomous_allowed: True for enabled stage
- CanaryControl.is_autonomous_allowed: False for disabled stage
- CanaryControl.is_autonomous_allowed: False for unknown pair
- CanaryControl.is_autonomous_allowed: False when paused (canary stage)
- CanaryControl.is_autonomous_allowed: False when paused (enabled stage)
- CanaryControl.is_canary: True only for canary stage
- CanaryControl.is_canary: False for enabled stage
- CanaryControl.is_canary: False for disabled stage
- CanaryControl.is_canary: False for unknown pair
- CanaryControl.is_canary: False when paused
- CanaryControl.get_state: returns state for known pair
- CanaryControl.get_state: returns None for unknown pair
- CanaryControl.get_node_states: returns all lanes for a node
- CanaryControl.get_node_states: returns empty list for unknown node
- CanaryControl.get_lane_states: returns all nodes for a lane
- CanaryControl.get_lane_states: returns empty list for unknown lane
- CanaryControl.get_transitions: returns all transitions reversed
- CanaryControl.get_transitions: filters by node_id
- CanaryControl.get_transitions: filters by lane
- CanaryControl.get_transitions: limit respected
- CanaryControl.get_transitions: empty history returns empty list
- CanaryControl.get_summary: counts by effective stage
- CanaryControl.get_summary: paused_count correct
- CanaryControl.get_summary: total_pairs correct
- CanaryControl.get_summary: nodes and lanes frozensets correct
- CanaryControl.get_summary: empty controller returns zero summary
- CanaryControl.export_state: keys include states and transition_count
- CanaryControl.export_state: state keys are node/lane strings
- CanaryControl.import_state: round-trip restores stage and paused
- CanaryControl.import_state: invalid stage string defaults to disabled
- CanaryControl.import_state: malformed key (no slash) is skipped
- CanaryControl.import_state: returns count of imported states
- CanaryControl.import_state: empty states dict returns 0
- CanaryControl.reset: clears all states and transitions
- Singleton: get_canary_control returns same instance
- Singleton: reset_canary_control returns fresh instance
- Singleton: reset_canary_control clears state from previous instance
- Thread safety: concurrent set_stage calls do not corrupt state

Target: 70+ tests.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

import pytest

from forge_harness.dark_factory.canary_control import (
    CanaryControl,
    RolloutStage,
    RolloutState,
    RolloutSummary,
    RolloutTransition,
    TransitionType,
    get_canary_control,
    reset_canary_control,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the singleton before and after every test."""
    reset_canary_control()
    yield
    reset_canary_control()


@pytest.fixture()
def ctrl() -> CanaryControl:
    """Return a fresh CanaryControl instance (not the singleton)."""
    return CanaryControl()


# ---------------------------------------------------------------------------
# Helper constants
# ---------------------------------------------------------------------------

OP = "alice"
NODE = "prya"
LANE = "docs"
LANE2 = "tests"
NODE2 = "sati"


# ===========================================================================
# RolloutStage enum
# ===========================================================================


class TestRolloutStageEnum:
    def test_has_disabled(self):
        assert RolloutStage.disabled.value == "disabled"

    def test_has_canary(self):
        assert RolloutStage.canary.value == "canary"

    def test_has_enabled(self):
        assert RolloutStage.enabled.value == "enabled"

    def test_is_str_subclass(self):
        assert isinstance(RolloutStage.canary, str)

    def test_exactly_three_members(self):
        assert len(list(RolloutStage)) == 3


# ===========================================================================
# TransitionType enum
# ===========================================================================


class TestTransitionTypeEnum:
    def test_has_promote(self):
        assert TransitionType.promote.value == "promote"

    def test_has_revert(self):
        assert TransitionType.revert.value == "revert"

    def test_has_pause(self):
        assert TransitionType.pause.value == "pause"

    def test_has_resume(self):
        assert TransitionType.resume.value == "resume"

    def test_exactly_four_members(self):
        assert len(list(TransitionType)) == 4

    def test_is_str_subclass(self):
        assert isinstance(TransitionType.pause, str)


# ===========================================================================
# RolloutTransition dataclass
# ===========================================================================


class TestRolloutTransition:
    def _make(self, **overrides) -> RolloutTransition:
        defaults = dict(
            transition_id=str(uuid.uuid4()),
            node_id=NODE,
            lane=LANE,
            from_stage=RolloutStage.disabled,
            to_stage=RolloutStage.canary,
            transition_type=TransitionType.promote,
            operator=OP,
        )
        defaults.update(overrides)
        return RolloutTransition(**defaults)

    def test_to_dict_contains_required_keys(self):
        t = self._make()
        d = t.to_dict()
        assert set(d.keys()) == {
            "transition_id", "node_id", "lane", "from_stage", "to_stage",
            "transition_type", "operator", "reason", "timestamp",
        }

    def test_to_dict_stage_values_are_strings(self):
        t = self._make()
        d = t.to_dict()
        assert d["from_stage"] == "disabled"
        assert d["to_stage"] == "canary"

    def test_to_dict_transition_type_is_string(self):
        t = self._make()
        assert t.to_dict()["transition_type"] == "promote"

    def test_to_dict_reason_empty_by_default(self):
        t = self._make()
        assert t.to_dict()["reason"] == ""

    def test_to_dict_reason_populated(self):
        t = self._make(reason="eval drop")
        assert t.to_dict()["reason"] == "eval drop"

    def test_timestamp_is_iso_string(self):
        t = self._make()
        ts = t.to_dict()["timestamp"]
        # Must be parseable as ISO datetime
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    def test_frozen_immutable(self):
        t = self._make()
        with pytest.raises((AttributeError, TypeError)):
            t.operator = "bob"  # type: ignore[misc]

    def test_transition_id_propagated(self):
        tid = str(uuid.uuid4())
        t = self._make(transition_id=tid)
        assert t.to_dict()["transition_id"] == tid


# ===========================================================================
# RolloutState dataclass
# ===========================================================================


class TestRolloutState:
    def test_default_stage_is_disabled(self):
        s = RolloutState(node_id=NODE, lane=LANE)
        assert s.stage == RolloutStage.disabled

    def test_default_paused_is_false(self):
        s = RolloutState(node_id=NODE, lane=LANE)
        assert s.paused is False

    def test_default_pre_pause_stage_is_none(self):
        s = RolloutState(node_id=NODE, lane=LANE)
        assert s.pre_pause_stage is None

    def test_effective_stage_not_paused(self):
        s = RolloutState(node_id=NODE, lane=LANE, stage=RolloutStage.canary)
        assert s.effective_stage == RolloutStage.canary

    def test_effective_stage_when_paused_returns_disabled(self):
        s = RolloutState(
            node_id=NODE, lane=LANE,
            stage=RolloutStage.enabled, paused=True,
        )
        assert s.effective_stage == RolloutStage.disabled

    def test_effective_stage_canary_paused_returns_disabled(self):
        s = RolloutState(
            node_id=NODE, lane=LANE,
            stage=RolloutStage.canary, paused=True,
        )
        assert s.effective_stage == RolloutStage.disabled

    def test_to_dict_keys(self):
        s = RolloutState(node_id=NODE, lane=LANE)
        d = s.to_dict()
        assert set(d.keys()) == {
            "node_id", "lane", "stage", "effective_stage",
            "paused", "pre_pause_stage", "updated_at",
        }

    def test_to_dict_pre_pause_stage_none(self):
        s = RolloutState(node_id=NODE, lane=LANE)
        assert s.to_dict()["pre_pause_stage"] is None

    def test_to_dict_pre_pause_stage_value(self):
        s = RolloutState(
            node_id=NODE, lane=LANE,
            pre_pause_stage=RolloutStage.canary,
        )
        assert s.to_dict()["pre_pause_stage"] == "canary"

    def test_to_dict_stage_and_effective_stage_strings(self):
        s = RolloutState(node_id=NODE, lane=LANE, stage=RolloutStage.enabled)
        d = s.to_dict()
        assert d["stage"] == "enabled"
        assert d["effective_stage"] == "enabled"

    def test_to_dict_paused_effective_stage(self):
        s = RolloutState(
            node_id=NODE, lane=LANE,
            stage=RolloutStage.enabled, paused=True,
        )
        d = s.to_dict()
        assert d["stage"] == "enabled"
        assert d["effective_stage"] == "disabled"
        assert d["paused"] is True


# ===========================================================================
# RolloutSummary dataclass
# ===========================================================================


class TestRolloutSummary:
    def _make(self, **overrides) -> RolloutSummary:
        defaults = dict(
            total_pairs=3,
            enabled_count=1,
            canary_count=1,
            disabled_count=1,
            paused_count=0,
            nodes=frozenset({"prya", "sati"}),
            lanes=frozenset({"docs", "tests"}),
        )
        defaults.update(overrides)
        return RolloutSummary(**defaults)

    def test_to_dict_keys(self):
        s = self._make()
        d = s.to_dict()
        assert set(d.keys()) == {
            "total_pairs", "enabled_count", "canary_count", "disabled_count",
            "paused_count", "nodes", "lanes",
        }

    def test_to_dict_nodes_is_sorted_list(self):
        s = self._make(nodes=frozenset({"sati", "prya", "nova"}))
        d = s.to_dict()
        assert d["nodes"] == ["nova", "prya", "sati"]

    def test_to_dict_lanes_is_sorted_list(self):
        s = self._make(lanes=frozenset({"tests", "docs", "api"}))
        d = s.to_dict()
        assert d["lanes"] == ["api", "docs", "tests"]

    def test_to_dict_counts(self):
        s = self._make(total_pairs=5, enabled_count=2, canary_count=1, disabled_count=2, paused_count=1)
        d = s.to_dict()
        assert d["total_pairs"] == 5
        assert d["enabled_count"] == 2
        assert d["canary_count"] == 1
        assert d["disabled_count"] == 2
        assert d["paused_count"] == 1

    def test_frozen_immutable(self):
        s = self._make()
        with pytest.raises((AttributeError, TypeError)):
            s.total_pairs = 999  # type: ignore[misc]


# ===========================================================================
# CanaryControl initialization
# ===========================================================================


class TestCanaryControlInit:
    def test_initial_state_empty(self, ctrl):
        assert ctrl._states == {}

    def test_initial_transitions_empty(self, ctrl):
        assert ctrl._transitions == []

    def test_get_state_unknown_pair_returns_none(self, ctrl):
        assert ctrl.get_state(NODE, LANE) is None

    def test_get_summary_empty_controller(self, ctrl):
        summary = ctrl.get_summary()
        assert summary.total_pairs == 0
        assert summary.enabled_count == 0
        assert summary.canary_count == 0
        assert summary.disabled_count == 0
        assert summary.paused_count == 0
        assert summary.nodes == frozenset()
        assert summary.lanes == frozenset()


# ===========================================================================
# set_stage
# ===========================================================================


class TestSetStage:
    def test_promote_disabled_to_canary(self, ctrl):
        t = ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        assert t.from_stage == RolloutStage.disabled
        assert t.to_stage == RolloutStage.canary
        assert t.transition_type == TransitionType.promote

    def test_promote_canary_to_enabled(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        t = ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        assert t.from_stage == RolloutStage.canary
        assert t.to_stage == RolloutStage.enabled
        assert t.transition_type == TransitionType.promote

    def test_revert_enabled_to_canary_via_set_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        t = ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        assert t.transition_type == TransitionType.revert

    def test_same_stage_no_op_classified_as_promote(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        t = ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        assert t.transition_type == TransitionType.promote

    def test_state_updated_after_set_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state is not None
        assert state.stage == RolloutStage.canary

    def test_set_stage_clears_paused_flag(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.paused is False

    def test_set_stage_clears_pre_pause_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.pre_pause_stage is None

    def test_reason_propagated(self, ctrl):
        t = ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP, reason="pilot start")
        assert t.reason == "pilot start"

    def test_empty_operator_raises(self, ctrl):
        with pytest.raises(ValueError, match="operator"):
            ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator="")

    def test_empty_node_id_raises(self, ctrl):
        with pytest.raises(ValueError, match="node_id"):
            ctrl.set_stage("", LANE, RolloutStage.canary, operator=OP)

    def test_empty_lane_raises(self, ctrl):
        with pytest.raises(ValueError, match="lane"):
            ctrl.set_stage(NODE, "", RolloutStage.canary, operator=OP)

    def test_transition_recorded_in_history(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        transitions = ctrl.get_transitions()
        assert len(transitions) == 1

    def test_operator_propagated_to_transition(self, ctrl):
        t = ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator="bob")
        assert t.operator == "bob"

    def test_transition_id_is_uuid(self, ctrl):
        t = ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        parsed = uuid.UUID(t.transition_id)
        assert str(parsed) == t.transition_id

    def test_promote_disabled_to_enabled_directly(self, ctrl):
        t = ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        assert t.transition_type == TransitionType.promote
        assert t.to_stage == RolloutStage.enabled


# ===========================================================================
# revert
# ===========================================================================


class TestRevert:
    def test_enabled_reverts_to_canary(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        t = ctrl.revert(NODE, LANE, operator=OP)
        assert t.to_stage == RolloutStage.canary
        assert t.from_stage == RolloutStage.enabled

    def test_canary_reverts_to_disabled(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        t = ctrl.revert(NODE, LANE, operator=OP)
        assert t.to_stage == RolloutStage.disabled
        assert t.from_stage == RolloutStage.canary

    def test_disabled_raises_value_error(self, ctrl):
        with pytest.raises(ValueError, match="already at disabled"):
            ctrl.revert(NODE, LANE, operator=OP)

    def test_empty_operator_raises(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        with pytest.raises(ValueError, match="operator"):
            ctrl.revert(NODE, LANE, operator="")

    def test_transition_type_is_revert(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        t = ctrl.revert(NODE, LANE, operator=OP)
        assert t.transition_type == TransitionType.revert

    def test_reason_propagated(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        t = ctrl.revert(NODE, LANE, operator=OP, reason="eval drop")
        assert t.reason == "eval drop"

    def test_double_revert_reaches_disabled(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.revert(NODE, LANE, operator=OP)
        ctrl.revert(NODE, LANE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.stage == RolloutStage.disabled

    def test_revert_clears_paused_flag(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        # After pause, effective_stage is disabled, so revert should raise
        with pytest.raises(ValueError, match="already at disabled"):
            ctrl.revert(NODE, LANE, operator=OP)

    def test_state_stage_updated_after_revert(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.revert(NODE, LANE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.stage == RolloutStage.canary


# ===========================================================================
# pause_node
# ===========================================================================


class TestPauseNode:
    def test_pauses_canary_lane(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        transitions = ctrl.pause_node(NODE, operator=OP)
        assert len(transitions) == 1
        state = ctrl.get_state(NODE, LANE)
        assert state.paused is True

    def test_pauses_enabled_lane(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        transitions = ctrl.pause_node(NODE, operator=OP)
        assert len(transitions) == 1
        assert transitions[0].transition_type == TransitionType.pause

    def test_skips_disabled_lanes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.disabled, operator=OP)
        transitions = ctrl.pause_node(NODE, operator=OP)
        assert transitions == []

    def test_skips_already_paused_lanes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        transitions = ctrl.pause_node(NODE, operator=OP)
        # Second pause returns no new transitions
        assert transitions == []

    def test_preserves_pre_pause_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.pre_pause_stage == RolloutStage.canary

    def test_preserves_enabled_pre_pause_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.pre_pause_stage == RolloutStage.enabled

    def test_empty_operator_raises(self, ctrl):
        with pytest.raises(ValueError, match="operator"):
            ctrl.pause_node(NODE, operator="")

    def test_unknown_node_returns_empty_list(self, ctrl):
        transitions = ctrl.pause_node("ghost-node", operator=OP)
        assert transitions == []

    def test_effective_stage_disabled_after_pause(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.effective_stage == RolloutStage.disabled

    def test_pauses_multiple_lanes_on_node(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.enabled, operator=OP)
        transitions = ctrl.pause_node(NODE, operator=OP)
        assert len(transitions) == 2

    def test_only_affects_target_node(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        # NODE2/LANE should remain unpaused
        state2 = ctrl.get_state(NODE2, LANE)
        assert state2.paused is False


# ===========================================================================
# pause_lane
# ===========================================================================


class TestPauseLane:
    def test_pauses_lane_across_nodes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.enabled, operator=OP)
        transitions = ctrl.pause_lane(LANE, operator=OP)
        assert len(transitions) == 2

    def test_skips_disabled_entries(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.disabled, operator=OP)
        transitions = ctrl.pause_lane(LANE, operator=OP)
        assert transitions == []

    def test_skips_already_paused_entries(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_lane(LANE, operator=OP)
        transitions = ctrl.pause_lane(LANE, operator=OP)
        assert transitions == []

    def test_empty_operator_raises(self, ctrl):
        with pytest.raises(ValueError, match="operator"):
            ctrl.pause_lane(LANE, operator="")

    def test_transition_type_is_pause(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        transitions = ctrl.pause_lane(LANE, operator=OP)
        assert transitions[0].transition_type == TransitionType.pause

    def test_does_not_affect_other_lanes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.canary, operator=OP)
        ctrl.pause_lane(LANE, operator=OP)
        state2 = ctrl.get_state(NODE, LANE2)
        assert state2.paused is False


# ===========================================================================
# resume_node
# ===========================================================================


class TestResumeNode:
    def test_restores_pre_pause_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        ctrl.resume_node(NODE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.paused is False
        assert state.stage == RolloutStage.canary

    def test_restores_enabled_pre_pause_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        ctrl.resume_node(NODE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.stage == RolloutStage.enabled

    def test_skips_non_paused_lanes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        transitions = ctrl.resume_node(NODE, operator=OP)
        assert transitions == []

    def test_empty_operator_raises(self, ctrl):
        with pytest.raises(ValueError, match="operator"):
            ctrl.resume_node(NODE, operator="")

    def test_transition_type_is_resume(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        transitions = ctrl.resume_node(NODE, operator=OP)
        assert transitions[0].transition_type == TransitionType.resume

    def test_clears_pre_pause_stage_after_resume(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        ctrl.resume_node(NODE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.pre_pause_stage is None

    def test_resumes_multiple_lanes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.enabled, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        transitions = ctrl.resume_node(NODE, operator=OP)
        assert len(transitions) == 2

    def test_only_affects_target_node(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        ctrl.pause_node(NODE2, operator=OP)
        ctrl.resume_node(NODE, operator=OP)
        state2 = ctrl.get_state(NODE2, LANE)
        assert state2.paused is True


# ===========================================================================
# resume_lane
# ===========================================================================


class TestResumeLane:
    def test_restores_across_all_nodes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.enabled, operator=OP)
        ctrl.pause_lane(LANE, operator=OP)
        transitions = ctrl.resume_lane(LANE, operator=OP)
        assert len(transitions) == 2

    def test_skips_non_paused_entries(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        transitions = ctrl.resume_lane(LANE, operator=OP)
        assert transitions == []

    def test_empty_operator_raises(self, ctrl):
        with pytest.raises(ValueError, match="operator"):
            ctrl.resume_lane(LANE, operator="")

    def test_restores_correct_stage_per_node(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.enabled, operator=OP)
        ctrl.pause_lane(LANE, operator=OP)
        ctrl.resume_lane(LANE, operator=OP)
        assert ctrl.get_state(NODE, LANE).stage == RolloutStage.canary
        assert ctrl.get_state(NODE2, LANE).stage == RolloutStage.enabled

    def test_clears_pre_pause_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_lane(LANE, operator=OP)
        ctrl.resume_lane(LANE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state.pre_pause_stage is None

    def test_does_not_affect_other_lanes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.canary, operator=OP)
        ctrl.pause_lane(LANE, operator=OP)
        ctrl.pause_lane(LANE2, operator=OP)
        ctrl.resume_lane(LANE, operator=OP)
        state2 = ctrl.get_state(NODE, LANE2)
        assert state2.paused is True


# ===========================================================================
# is_autonomous_allowed
# ===========================================================================


class TestIsAutonomousAllowed:
    def test_true_for_canary_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        assert ctrl.is_autonomous_allowed(NODE, LANE) is True

    def test_true_for_enabled_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        assert ctrl.is_autonomous_allowed(NODE, LANE) is True

    def test_false_for_disabled_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.disabled, operator=OP)
        assert ctrl.is_autonomous_allowed(NODE, LANE) is False

    def test_false_for_unknown_pair(self, ctrl):
        assert ctrl.is_autonomous_allowed("ghost", "lane") is False

    def test_false_when_paused_at_canary(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        assert ctrl.is_autonomous_allowed(NODE, LANE) is False

    def test_false_when_paused_at_enabled(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        assert ctrl.is_autonomous_allowed(NODE, LANE) is False


# ===========================================================================
# is_canary
# ===========================================================================


class TestIsCanary:
    def test_true_for_canary_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        assert ctrl.is_canary(NODE, LANE) is True

    def test_false_for_enabled_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        assert ctrl.is_canary(NODE, LANE) is False

    def test_false_for_disabled_stage(self, ctrl):
        assert ctrl.is_canary(NODE, LANE) is False

    def test_false_for_unknown_pair(self, ctrl):
        assert ctrl.is_canary("ghost", "lane") is False

    def test_false_when_paused_at_canary(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        assert ctrl.is_canary(NODE, LANE) is False


# ===========================================================================
# get_state / get_node_states / get_lane_states
# ===========================================================================


class TestGetState:
    def test_get_state_known_pair(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state is not None
        assert state.node_id == NODE
        assert state.lane == LANE

    def test_get_state_unknown_pair_returns_none(self, ctrl):
        assert ctrl.get_state("nobody", "nowhere") is None

    def test_get_node_states_returns_all_lanes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.enabled, operator=OP)
        states = ctrl.get_node_states(NODE)
        assert len(states) == 2
        lanes = {s.lane for s in states}
        assert lanes == {LANE, LANE2}

    def test_get_node_states_unknown_node_returns_empty(self, ctrl):
        assert ctrl.get_node_states("ghost") == []

    def test_get_node_states_only_own_node(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.canary, operator=OP)
        states = ctrl.get_node_states(NODE)
        assert all(s.node_id == NODE for s in states)

    def test_get_lane_states_returns_all_nodes(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.enabled, operator=OP)
        states = ctrl.get_lane_states(LANE)
        assert len(states) == 2
        nodes = {s.node_id for s in states}
        assert nodes == {NODE, NODE2}

    def test_get_lane_states_unknown_lane_returns_empty(self, ctrl):
        assert ctrl.get_lane_states("ghost-lane") == []

    def test_get_lane_states_only_own_lane(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.canary, operator=OP)
        states = ctrl.get_lane_states(LANE)
        assert all(s.lane == LANE for s in states)


# ===========================================================================
# get_transitions
# ===========================================================================


class TestGetTransitions:
    def test_returns_most_recent_first(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        transitions = ctrl.get_transitions()
        assert transitions[0].to_stage == RolloutStage.enabled
        assert transitions[1].to_stage == RolloutStage.canary

    def test_filter_by_node_id(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.canary, operator=OP)
        transitions = ctrl.get_transitions(node_id=NODE)
        assert all(t.node_id == NODE for t in transitions)
        assert len(transitions) == 1

    def test_filter_by_lane(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.canary, operator=OP)
        transitions = ctrl.get_transitions(lane=LANE)
        assert all(t.lane == LANE for t in transitions)
        assert len(transitions) == 1

    def test_limit_respected(self, ctrl):
        for i in range(10):
            ctrl.set_stage(f"node-{i}", LANE, RolloutStage.canary, operator=OP)
        transitions = ctrl.get_transitions(limit=3)
        assert len(transitions) == 3

    def test_empty_history_returns_empty_list(self, ctrl):
        assert ctrl.get_transitions() == []

    def test_filter_by_node_and_lane(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.canary, operator=OP)
        transitions = ctrl.get_transitions(node_id=NODE, lane=LANE)
        assert len(transitions) == 1
        assert transitions[0].node_id == NODE
        assert transitions[0].lane == LANE


# ===========================================================================
# get_summary
# ===========================================================================


class TestGetSummary:
    def test_total_pairs_correct(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.enabled, operator=OP)
        summary = ctrl.get_summary()
        assert summary.total_pairs == 2

    def test_enabled_count(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.canary, operator=OP)
        summary = ctrl.get_summary()
        assert summary.enabled_count == 1

    def test_canary_count(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        summary = ctrl.get_summary()
        assert summary.canary_count == 1

    def test_disabled_count(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.disabled, operator=OP)
        summary = ctrl.get_summary()
        assert summary.disabled_count == 1

    def test_paused_count(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.enabled, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        summary = ctrl.get_summary()
        assert summary.paused_count == 2

    def test_paused_counts_as_disabled_effective(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        summary = ctrl.get_summary()
        # Effective stage is disabled due to pause
        assert summary.enabled_count == 0
        assert summary.disabled_count == 1

    def test_nodes_frozenset(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.enabled, operator=OP)
        summary = ctrl.get_summary()
        assert summary.nodes == frozenset({NODE, NODE2})

    def test_lanes_frozenset(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE2, RolloutStage.enabled, operator=OP)
        summary = ctrl.get_summary()
        assert summary.lanes == frozenset({LANE, LANE2})

    def test_empty_summary(self, ctrl):
        summary = ctrl.get_summary()
        assert summary.total_pairs == 0
        assert summary.nodes == frozenset()


# ===========================================================================
# export_state / import_state
# ===========================================================================


class TestExportImportState:
    def test_export_has_states_key(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        exported = ctrl.export_state()
        assert "states" in exported

    def test_export_has_transition_count_key(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        exported = ctrl.export_state()
        assert "transition_count" in exported

    def test_export_transition_count_correct(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        exported = ctrl.export_state()
        assert exported["transition_count"] == 2

    def test_export_state_key_format(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        exported = ctrl.export_state()
        assert f"{NODE}/{LANE}" in exported["states"]

    def test_round_trip_restores_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        exported = ctrl.export_state()

        fresh = CanaryControl()
        count = fresh.import_state(exported)
        assert count == 1
        state = fresh.get_state(NODE, LANE)
        assert state is not None
        assert state.stage == RolloutStage.canary

    def test_round_trip_restores_paused_flag(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        exported = ctrl.export_state()

        fresh = CanaryControl()
        fresh.import_state(exported)
        state = fresh.get_state(NODE, LANE)
        assert state.paused is True

    def test_round_trip_restores_pre_pause_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.pause_node(NODE, operator=OP)
        exported = ctrl.export_state()

        fresh = CanaryControl()
        fresh.import_state(exported)
        state = fresh.get_state(NODE, LANE)
        assert state.pre_pause_stage == RolloutStage.canary

    def test_import_invalid_stage_defaults_to_disabled(self, ctrl):
        data = {"states": {f"{NODE}/{LANE}": {"stage": "bogus", "paused": False}}}
        fresh = CanaryControl()
        fresh.import_state(data)
        state = fresh.get_state(NODE, LANE)
        assert state.stage == RolloutStage.disabled

    def test_import_malformed_key_skipped(self):
        data = {"states": {"no-slash-here": {"stage": "canary", "paused": False}}}
        fresh = CanaryControl()
        count = fresh.import_state(data)
        assert count == 0

    def test_import_empty_states_returns_zero(self):
        fresh = CanaryControl()
        count = fresh.import_state({"states": {}})
        assert count == 0

    def test_import_returns_correct_count(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.set_stage(NODE2, LANE, RolloutStage.enabled, operator=OP)
        exported = ctrl.export_state()

        fresh = CanaryControl()
        count = fresh.import_state(exported)
        assert count == 2


# ===========================================================================
# reset
# ===========================================================================


class TestReset:
    def test_reset_clears_states(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.reset()
        assert ctrl._states == {}

    def test_reset_clears_transitions(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        ctrl.reset()
        assert ctrl._transitions == []

    def test_reset_allows_fresh_state(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.reset()
        assert ctrl.get_state(NODE, LANE) is None


# ===========================================================================
# Singleton
# ===========================================================================


class TestSingleton:
    def test_get_canary_control_returns_same_instance(self):
        a = get_canary_control()
        b = get_canary_control()
        assert a is b

    def test_reset_canary_control_creates_fresh_instance(self):
        a = get_canary_control()
        reset_canary_control()
        b = get_canary_control()
        assert a is not b

    def test_reset_clears_state_from_previous_instance(self):
        ctrl = get_canary_control()
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        reset_canary_control()
        fresh = get_canary_control()
        assert fresh.get_state(NODE, LANE) is None

    def test_singleton_state_shared_across_callers(self):
        a = get_canary_control()
        a.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        b = get_canary_control()
        assert b.get_state(NODE, LANE) is not None


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_set_stage_does_not_corrupt_state(self):
        """Fire 50 threads each setting a distinct (node, lane) pair."""
        ctrl = CanaryControl()
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                ctrl.set_stage(f"node-{idx}", f"lane-{idx}", RolloutStage.canary, operator=OP)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(ctrl._states) == 50

    def test_concurrent_set_stage_same_pair_no_corruption(self):
        """Multiple threads racing on the same (node, lane) pair."""
        ctrl = CanaryControl()
        errors: list[Exception] = []

        def worker() -> None:
            try:
                ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ===========================================================================
# Pair pause/resume
# ===========================================================================


class TestPairPauseResume:
    def test_pause_pair_preserves_pre_pause_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)

        transition = ctrl.pause_pair(NODE, LANE, operator=OP)
        state = ctrl.get_state(NODE, LANE)

        assert transition.transition_type == TransitionType.pause
        assert transition.to_stage == RolloutStage.disabled
        assert state is not None
        assert state.paused is True
        assert state.pre_pause_stage == RolloutStage.canary
        assert state.effective_stage == RolloutStage.disabled

    def test_pause_pair_rejects_disabled_stage(self, ctrl):
        with pytest.raises(ValueError, match="already disabled"):
            ctrl.pause_pair(NODE, LANE, operator=OP)

    def test_resume_pair_restores_previous_stage(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.enabled, operator=OP)
        ctrl.pause_pair(NODE, LANE, operator=OP)

        transition = ctrl.resume_pair(NODE, LANE, operator=OP)
        state = ctrl.get_state(NODE, LANE)

        assert transition.transition_type == TransitionType.resume
        assert transition.to_stage == RolloutStage.enabled
        assert state is not None
        assert state.paused is False
        assert state.pre_pause_stage is None
        assert state.effective_stage == RolloutStage.enabled

    def test_resume_pair_rejects_non_paused(self, ctrl):
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        with pytest.raises(ValueError, match="is not paused"):
            ctrl.resume_pair(NODE, LANE, operator=OP)
        state = ctrl.get_state(NODE, LANE)
        assert state is not None
        assert state.stage == RolloutStage.canary

    def test_concurrent_get_summary_does_not_raise(self):
        """Summary reads during concurrent writes should not raise."""
        ctrl = CanaryControl()
        ctrl.set_stage(NODE, LANE, RolloutStage.canary, operator=OP)
        errors: list[Exception] = []

        def writer(idx: int) -> None:
            try:
                ctrl.set_stage(f"n-{idx}", LANE, RolloutStage.enabled, operator=OP)
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                ctrl.get_summary()
            except Exception as exc:
                errors.append(exc)

        threads = [
            *(threading.Thread(target=writer, args=(i,)) for i in range(20)),
            *(threading.Thread(target=reader) for _ in range(20)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
