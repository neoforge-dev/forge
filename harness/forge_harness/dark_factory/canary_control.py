"""Canary Rollout Control — DF-4004
====================================

Implements node-by-node and lane-by-lane rollout controls for the
Dark Factory autonomous completion pipeline.

When expanding autonomous task completion from a small pilot to the full
portfolio, the canary controller provides granular control over which
nodes and which lanes have autonomy enabled.  It supports three rollout
stages per (node, lane) pair:

- ``disabled`` — all tasks go through human review (default safe state).
- ``canary``   — autonomous completions enabled but audit sampling is
                 increased (e.g. 50%) and the pair can be reverted
                 instantly.
- ``enabled``  — full autonomous operation at normal audit rate.

Key capabilities:
1. **Pause** a node or lane without losing in-flight tasks (tasks remain
   in queue, only new routing is affected).
2. **Revert** a (node, lane) pair from ``enabled`` → ``canary`` →
   ``disabled`` with a single call.
3. **Full audit trail** of every state transition.
4. **Config-driven**: rollout state can be serialised to JSON for
   persistence across restarts.

Design principles
-----------------
* **Additive**: enabling canary on node A does not affect node B.
* **Fail-safe**: any unknown (node, lane) pair defaults to ``disabled``.
* **Thread-safe**: all state guarded by ``RLock``.
* **Singleton**: use :func:`get_canary_control` / :func:`reset_canary_control`.

Usage
-----
::

    from forge_harness.dark_factory.canary_control import (
        get_canary_control,
        RolloutStage,
    )

    ctrl = get_canary_control()

    # Enable canary on prya for docs lane
    ctrl.set_stage("prya", "docs", RolloutStage.canary, operator="alice")

    # Check if autonomous completions are allowed
    ctrl.is_autonomous_allowed("prya", "docs")  # True (canary allows autonomous)
    ctrl.is_autonomous_allowed("sati", "docs")  # False (not yet enabled)

    # Promote to full
    ctrl.set_stage("prya", "docs", RolloutStage.enabled, operator="alice")

    # Emergency revert
    ctrl.revert("prya", "docs", operator="alice", reason="eval pass rate dropped")

    # Pause an entire node
    ctrl.pause_node("prya", operator="alice", reason="maintenance window")

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-4004)
docs/runbooks/DARK_LANE_INCIDENT_RUNBOOK.md
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RolloutStage(str, Enum):
    """Rollout stage for a (node, lane) pair."""

    disabled = "disabled"
    canary = "canary"
    enabled = "enabled"


class TransitionType(str, Enum):
    """Type of rollout state transition."""

    promote = "promote"     # disabled → canary, canary → enabled
    revert = "revert"       # enabled → canary, canary → disabled
    pause = "pause"         # any → disabled (emergency)
    resume = "resume"       # restore to previous stage after pause


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RolloutTransition:
    """Immutable record of a rollout state transition.

    Attributes:
        transition_id:  Unique identifier for this transition.
        node_id:        The node affected.
        lane:           The work-cell lane affected.
        from_stage:     Previous rollout stage.
        to_stage:       New rollout stage.
        transition_type: Type of transition (promote/revert/pause/resume).
        operator:       Who triggered the transition.
        reason:         Optional reason for the transition.
        timestamp:      UTC timestamp.
    """

    transition_id: str
    node_id: str
    lane: str
    from_stage: RolloutStage
    to_stage: RolloutStage
    transition_type: TransitionType
    operator: str
    reason: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "node_id": self.node_id,
            "lane": self.lane,
            "from_stage": self.from_stage.value,
            "to_stage": self.to_stage.value,
            "transition_type": self.transition_type.value,
            "operator": self.operator,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class RolloutState:
    """Mutable state of a (node, lane) pair.

    Attributes:
        node_id:        The node.
        lane:           The work-cell lane.
        stage:          Current rollout stage.
        paused:         Whether this pair is paused (overrides stage).
        pre_pause_stage: Stage before pausing (for resume).
        updated_at:     Last state change timestamp.
    """

    node_id: str
    lane: str
    stage: RolloutStage = RolloutStage.disabled
    paused: bool = False
    pre_pause_stage: RolloutStage | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def effective_stage(self) -> RolloutStage:
        """The effective stage accounting for pause state."""
        return RolloutStage.disabled if self.paused else self.stage

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "lane": self.lane,
            "stage": self.stage.value,
            "effective_stage": self.effective_stage.value,
            "paused": self.paused,
            "pre_pause_stage": self.pre_pause_stage.value if self.pre_pause_stage else None,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class RolloutSummary:
    """Summary of rollout state across all nodes and lanes.

    Attributes:
        total_pairs:     Total (node, lane) pairs tracked.
        enabled_count:   Pairs at ``enabled`` stage.
        canary_count:    Pairs at ``canary`` stage.
        disabled_count:  Pairs at ``disabled`` stage.
        paused_count:    Pairs currently paused.
        nodes:           Set of registered nodes.
        lanes:           Set of registered lanes.
    """

    total_pairs: int
    enabled_count: int
    canary_count: int
    disabled_count: int
    paused_count: int
    nodes: frozenset[str]
    lanes: frozenset[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_pairs": self.total_pairs,
            "enabled_count": self.enabled_count,
            "canary_count": self.canary_count,
            "disabled_count": self.disabled_count,
            "paused_count": self.paused_count,
            "nodes": sorted(self.nodes),
            "lanes": sorted(self.lanes),
        }


# ---------------------------------------------------------------------------
# Canary Control
# ---------------------------------------------------------------------------


class CanaryControl:
    """Node-by-node and lane-by-lane rollout controller.

    Thread-safe singleton that manages the rollout state for every
    (node, lane) pair in the Dark Factory.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[tuple[str, str], RolloutState] = {}
        self._transitions: list[RolloutTransition] = []
        logger.info("CanaryControl initialised")

    def _key(self, node_id: str, lane: str) -> tuple[str, str]:
        return (node_id, lane)

    def _get_or_create(self, node_id: str, lane: str) -> RolloutState:
        """Get existing state or create a new disabled one."""
        key = self._key(node_id, lane)
        if key not in self._states:
            self._states[key] = RolloutState(node_id=node_id, lane=lane)
        return self._states[key]

    def _record_transition(
        self,
        node_id: str,
        lane: str,
        from_stage: RolloutStage,
        to_stage: RolloutStage,
        transition_type: TransitionType,
        operator: str,
        reason: str = "",
    ) -> RolloutTransition:
        """Record a transition in the audit log."""
        transition = RolloutTransition(
            transition_id=str(uuid.uuid4()),
            node_id=node_id,
            lane=lane,
            from_stage=from_stage,
            to_stage=to_stage,
            transition_type=transition_type,
            operator=operator,
            reason=reason,
        )
        self._transitions.append(transition)
        logger.info(
            "Rollout transition: %s/%s %s → %s (%s by %s)%s",
            node_id,
            lane,
            from_stage.value,
            to_stage.value,
            transition_type.value,
            operator,
            f" reason={reason}" if reason else "",
        )
        return transition

    def set_stage(
        self,
        node_id: str,
        lane: str,
        stage: RolloutStage,
        operator: str,
        reason: str = "",
    ) -> RolloutTransition:
        """Set the rollout stage for a (node, lane) pair.

        Args:
            node_id: Target node identifier.
            lane: Work-cell lane string.
            stage: Desired rollout stage.
            operator: Who is making this change.
            reason: Optional reason.

        Returns:
            The :class:`RolloutTransition` recording this change.

        Raises:
            ValueError: If operator is empty or stage transition is invalid.
        """
        if not operator:
            raise ValueError("operator must be non-empty")
        if not node_id:
            raise ValueError("node_id must be non-empty")
        if not lane:
            raise ValueError("lane must be non-empty")

        with self._lock:
            state = self._get_or_create(node_id, lane)
            old_stage = state.effective_stage

            # Determine transition type
            stage_order = {RolloutStage.disabled: 0, RolloutStage.canary: 1, RolloutStage.enabled: 2}
            if stage_order.get(stage, 0) > stage_order.get(old_stage, 0):
                t_type = TransitionType.promote
            elif stage_order.get(stage, 0) < stage_order.get(old_stage, 0):
                t_type = TransitionType.revert
            else:
                t_type = TransitionType.promote  # no-op same stage

            state.stage = stage
            state.paused = False
            state.pre_pause_stage = None
            state.updated_at = datetime.now(UTC)

            return self._record_transition(
                node_id, lane, old_stage, stage, t_type, operator, reason
            )

    def revert(
        self,
        node_id: str,
        lane: str,
        operator: str,
        reason: str = "",
    ) -> RolloutTransition:
        """Revert a (node, lane) pair one stage down.

        enabled → canary → disabled.

        Args:
            node_id: Target node.
            lane: Work-cell lane.
            operator: Who is reverting.
            reason: Optional reason.

        Returns:
            The :class:`RolloutTransition`.

        Raises:
            ValueError: If already at disabled stage.
        """
        if not operator:
            raise ValueError("operator must be non-empty")

        with self._lock:
            state = self._get_or_create(node_id, lane)
            old_stage = state.effective_stage

            if old_stage == RolloutStage.disabled:
                raise ValueError(
                    f"{node_id}/{lane} is already at disabled stage; cannot revert further"
                )

            new_stage = (
                RolloutStage.canary
                if old_stage == RolloutStage.enabled
                else RolloutStage.disabled
            )

            state.stage = new_stage
            state.paused = False
            state.pre_pause_stage = None
            state.updated_at = datetime.now(UTC)

            return self._record_transition(
                node_id, lane, old_stage, new_stage,
                TransitionType.revert, operator, reason,
            )

    def pause_node(
        self,
        node_id: str,
        operator: str,
        reason: str = "",
    ) -> list[RolloutTransition]:
        """Pause all lanes on a node (emergency stop).

        All (node, *) pairs are effectively set to ``disabled`` without
        losing their pre-pause stage, so they can be resumed later.

        Args:
            node_id: Node to pause.
            operator: Who is pausing.
            reason: Optional reason.

        Returns:
            List of transitions for each affected lane.
        """
        if not operator:
            raise ValueError("operator must be non-empty")

        transitions = []
        with self._lock:
            for key, state in self._states.items():
                if key[0] == node_id and not state.paused and state.stage != RolloutStage.disabled:
                    old_stage = state.effective_stage
                    state.pre_pause_stage = state.stage
                    state.paused = True
                    state.updated_at = datetime.now(UTC)

                    t = self._record_transition(
                        node_id, state.lane, old_stage, RolloutStage.disabled,
                        TransitionType.pause, operator, reason,
                    )
                    transitions.append(t)

        return transitions

    def pause_lane(
        self,
        lane: str,
        operator: str,
        reason: str = "",
    ) -> list[RolloutTransition]:
        """Pause a lane across all nodes (lane-level emergency stop).

        Args:
            lane: Lane to pause.
            operator: Who is pausing.
            reason: Optional reason.

        Returns:
            List of transitions for each affected node.
        """
        if not operator:
            raise ValueError("operator must be non-empty")

        transitions = []
        with self._lock:
            for key, state in self._states.items():
                if key[1] == lane and not state.paused and state.stage != RolloutStage.disabled:
                    old_stage = state.effective_stage
                    state.pre_pause_stage = state.stage
                    state.paused = True
                    state.updated_at = datetime.now(UTC)

                    t = self._record_transition(
                        state.node_id, lane, old_stage, RolloutStage.disabled,
                        TransitionType.pause, operator, reason,
                    )
                    transitions.append(t)

        return transitions

    def pause_pair(
        self,
        node_id: str,
        lane: str,
        operator: str,
        reason: str = "",
    ) -> RolloutTransition:
        """Pause one specific (node, lane) rollout.

        Preserves the current stage in ``pre_pause_stage`` so the rollout can
        later be resumed exactly to its previous state.

        Raises:
            ValueError: If operator/node/lane is empty, rollout is already paused,
                        or rollout is already disabled.
        """
        if not operator:
            raise ValueError("operator must be non-empty")
        if not node_id:
            raise ValueError("node_id must be non-empty")
        if not lane:
            raise ValueError("lane must be non-empty")

        with self._lock:
            state = self._get_or_create(node_id, lane)
            if state.paused:
                raise ValueError(f"{node_id}/{lane} is already paused")
            if state.stage == RolloutStage.disabled:
                raise ValueError(f"{node_id}/{lane} is already disabled; cannot pause")

            old_stage = state.effective_stage
            state.pre_pause_stage = state.stage
            state.paused = True
            state.updated_at = datetime.now(UTC)

            return self._record_transition(
                node_id,
                lane,
                old_stage,
                RolloutStage.disabled,
                TransitionType.pause,
                operator,
                reason,
            )

    def resume_node(
        self,
        node_id: str,
        operator: str,
        reason: str = "",
    ) -> list[RolloutTransition]:
        """Resume all paused lanes on a node.

        Restores each lane to its pre-pause stage.

        Args:
            node_id: Node to resume.
            operator: Who is resuming.
            reason: Optional reason.

        Returns:
            List of transitions for each resumed lane.
        """
        if not operator:
            raise ValueError("operator must be non-empty")

        transitions = []
        with self._lock:
            for key, state in self._states.items():
                if key[0] == node_id and state.paused:
                    old_stage = state.effective_stage  # disabled (paused)
                    restore_stage = state.pre_pause_stage or RolloutStage.disabled
                    state.paused = False
                    state.stage = restore_stage
                    state.pre_pause_stage = None
                    state.updated_at = datetime.now(UTC)

                    t = self._record_transition(
                        node_id, state.lane, old_stage, restore_stage,
                        TransitionType.resume, operator, reason,
                    )
                    transitions.append(t)

        return transitions

    def resume_lane(
        self,
        lane: str,
        operator: str,
        reason: str = "",
    ) -> list[RolloutTransition]:
        """Resume a paused lane across all nodes.

        Args:
            lane: Lane to resume.
            operator: Who is resuming.
            reason: Optional reason.

        Returns:
            List of transitions.
        """
        if not operator:
            raise ValueError("operator must be non-empty")

        transitions = []
        with self._lock:
            for key, state in self._states.items():
                if key[1] == lane and state.paused:
                    old_stage = state.effective_stage
                    restore_stage = state.pre_pause_stage or RolloutStage.disabled
                    state.paused = False
                    state.stage = restore_stage
                    state.pre_pause_stage = None
                    state.updated_at = datetime.now(UTC)

                    t = self._record_transition(
                        state.node_id, lane, old_stage, restore_stage,
                        TransitionType.resume, operator, reason,
                    )
                    transitions.append(t)

        return transitions

    def resume_pair(
        self,
        node_id: str,
        lane: str,
        operator: str,
        reason: str = "",
    ) -> RolloutTransition:
        """Resume one specific paused (node, lane) rollout."""
        if not operator:
            raise ValueError("operator must be non-empty")
        if not node_id:
            raise ValueError("node_id must be non-empty")
        if not lane:
            raise ValueError("lane must be non-empty")

        with self._lock:
            state = self._get_or_create(node_id, lane)
            if not state.paused:
                raise ValueError(f"{node_id}/{lane} is not paused")

            old_stage = state.effective_stage
            restore_stage = state.pre_pause_stage or RolloutStage.disabled
            state.paused = False
            state.stage = restore_stage
            state.pre_pause_stage = None
            state.updated_at = datetime.now(UTC)

            return self._record_transition(
                node_id,
                lane,
                old_stage,
                restore_stage,
                TransitionType.resume,
                operator,
                reason,
            )

    def is_autonomous_allowed(self, node_id: str, lane: str) -> bool:
        """Check if autonomous completions are allowed for (node, lane).

        Returns True only if the effective stage is ``canary`` or ``enabled``.
        Unknown pairs default to ``disabled`` (False).
        """
        with self._lock:
            key = self._key(node_id, lane)
            state = self._states.get(key)
            if state is None:
                return False
            return state.effective_stage in (RolloutStage.canary, RolloutStage.enabled)

    def is_canary(self, node_id: str, lane: str) -> bool:
        """Check if a (node, lane) pair is in canary mode.

        Canary mode means autonomous is allowed but with increased audit
        sampling.
        """
        with self._lock:
            key = self._key(node_id, lane)
            state = self._states.get(key)
            if state is None:
                return False
            return state.effective_stage == RolloutStage.canary

    def get_state(self, node_id: str, lane: str) -> RolloutState | None:
        """Get the rollout state for a (node, lane) pair."""
        with self._lock:
            return self._states.get(self._key(node_id, lane))

    def get_node_states(self, node_id: str) -> list[RolloutState]:
        """Get all rollout states for a node."""
        with self._lock:
            return [
                s for (n, _), s in self._states.items()
                if n == node_id
            ]

    def get_lane_states(self, lane: str) -> list[RolloutState]:
        """Get all rollout states for a lane across nodes."""
        with self._lock:
            return [
                s for (_, lane_name), s in self._states.items()
                if lane_name == lane
            ]

    def get_transitions(
        self,
        node_id: str | None = None,
        lane: str | None = None,
        limit: int = 50,
    ) -> list[RolloutTransition]:
        """Get transition history, optionally filtered.

        Args:
            node_id: Filter by node (None = all).
            lane: Filter by lane (None = all).
            limit: Max transitions to return.

        Returns:
            List of transitions, most recent first.
        """
        with self._lock:
            filtered = self._transitions
            if node_id:
                filtered = [t for t in filtered if t.node_id == node_id]
            if lane:
                filtered = [t for t in filtered if t.lane == lane]
            return list(reversed(filtered[-limit:]))

    def get_summary(self) -> RolloutSummary:
        """Get a summary of rollout state across all nodes and lanes."""
        with self._lock:
            states = list(self._states.values())
            nodes = set()
            lanes = set()
            enabled = canary = disabled = paused = 0

            for s in states:
                nodes.add(s.node_id)
                lanes.add(s.lane)
                if s.paused:
                    paused += 1
                eff = s.effective_stage
                if eff == RolloutStage.enabled:
                    enabled += 1
                elif eff == RolloutStage.canary:
                    canary += 1
                else:
                    disabled += 1

            return RolloutSummary(
                total_pairs=len(states),
                enabled_count=enabled,
                canary_count=canary,
                disabled_count=disabled,
                paused_count=paused,
                nodes=frozenset(nodes),
                lanes=frozenset(lanes),
            )

    def export_state(self) -> dict[str, Any]:
        """Export full rollout state for persistence/JSON serialisation."""
        with self._lock:
            return {
                "states": {
                    f"{k[0]}/{k[1]}": v.to_dict()
                    for k, v in self._states.items()
                },
                "transition_count": len(self._transitions),
            }

    def import_state(self, data: dict[str, Any]) -> int:
        """Import rollout state from a serialised dict.

        Returns the number of states imported.
        """
        with self._lock:
            states_data = data.get("states", {})
            count = 0
            for key_str, state_dict in states_data.items():
                parts = key_str.split("/", 1)
                if len(parts) != 2:
                    continue
                node_id, lane = parts
                stage_str = state_dict.get("stage", "disabled")
                try:
                    stage = RolloutStage(stage_str)
                except ValueError:
                    stage = RolloutStage.disabled

                state = self._get_or_create(node_id, lane)
                state.stage = stage
                state.paused = state_dict.get("paused", False)
                pre = state_dict.get("pre_pause_stage")
                state.pre_pause_stage = RolloutStage(pre) if pre else None
                count += 1

            logger.info("CanaryControl imported %d rollout states", count)
            return count

    def reset(self) -> None:
        """Reset all state. For testing."""
        with self._lock:
            self._states.clear()
            self._transitions.clear()
            logger.info("CanaryControl reset")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: CanaryControl | None = None
_instance_lock = RLock()


def get_canary_control() -> CanaryControl:
    """Get the shared CanaryControl singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = CanaryControl()
        return _instance


def reset_canary_control() -> None:
    """Reset the singleton (for tests)."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.reset()
        _instance = None
