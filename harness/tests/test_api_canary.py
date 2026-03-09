"""Comprehensive tests for forge_harness/webhook_server/api/canary.py

Tests cover all 7 endpoints:
- GET  /api/canary/rollouts                       - List rollout states
- POST /api/canary/rollouts                       - Create/update rollout state
- GET  /api/canary/rollouts/{rollout_id}          - Get rollout status
- POST /api/canary/rollouts/{rollout_id}/pause    - Pause a rollout
- POST /api/canary/rollouts/{rollout_id}/resume   - Resume a rollout
- POST /api/canary/rollouts/{rollout_id}/promote  - Promote rollout stage
- POST /api/canary/rollouts/{rollout_id}/rollback - Revert rollout stage

All external dependencies (canary_control singleton) are mocked.
Auth is bypassed via dependency_overrides.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers: mock rollout state/transition objects
# ---------------------------------------------------------------------------


def _make_rollout_state(
    node_id: str = "prya",
    lane: str = "docs",
    stage: str = "disabled",
    effective_stage: str = "disabled",
    paused: bool = False,
    pre_pause_stage: str | None = None,
) -> Mock:
    """Return a mock RolloutState-like object.

    Note: effective_stage must be the actual RolloutStage enum so that
    identity comparisons in the router (effective == RolloutStage.disabled)
    work correctly.
    """
    from forge_harness.dark_factory.canary_control import RolloutStage

    state = Mock()
    state.node_id = node_id
    state.lane = lane
    state.stage = RolloutStage(stage)
    state.effective_stage = RolloutStage(effective_stage)
    state.paused = paused
    state.pre_pause_stage = RolloutStage(pre_pause_stage) if pre_pause_stage else None
    state.updated_at = datetime.now(UTC)
    state.to_dict.return_value = {
        "node_id": node_id,
        "lane": lane,
        "stage": stage,
        "effective_stage": effective_stage,
        "paused": paused,
        "pre_pause_stage": pre_pause_stage,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    return state


def _make_transition(
    node_id: str = "prya",
    lane: str = "docs",
    from_stage: str = "disabled",
    to_stage: str = "canary",
    transition_type: str = "promote",
) -> Mock:
    """Return a mock RolloutTransition-like object."""
    transition = Mock()
    transition.transition_id = "txn-001"
    transition.node_id = node_id
    transition.lane = lane
    transition.from_stage = Mock(value=from_stage)
    transition.to_stage = Mock(value=to_stage)
    transition.transition_type = Mock(value=transition_type)
    transition.operator = "api"
    transition.reason = ""
    transition.timestamp = datetime.now(UTC)
    transition.to_dict.return_value = {
        "transition_id": "txn-001",
        "node_id": node_id,
        "lane": lane,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "transition_type": transition_type,
        "operator": "api",
        "reason": "",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return transition


def _make_summary() -> Mock:
    """Return a mock RolloutSummary-like object."""
    summary = Mock()
    summary.to_dict.return_value = {
        "total_pairs": 0,
        "enabled_count": 0,
        "canary_count": 0,
        "disabled_count": 0,
        "paused_count": 0,
        "nodes": [],
        "lanes": [],
    }
    return summary


def _make_canary_controller(
    state: Mock | None = None,
    transition: Mock | None = None,
    states_export: dict | None = None,
) -> Mock:
    """Return a mock CanaryControl controller."""
    controller = Mock()
    _state = state or _make_rollout_state()
    _transition = transition or _make_transition()

    controller.get_state.return_value = _state
    controller.set_stage.return_value = _transition
    controller.pause_pair.return_value = _transition
    controller.resume_pair.return_value = _transition
    controller.revert.return_value = _transition
    controller.get_transitions.return_value = [_transition]
    controller.get_summary.return_value = _make_summary()

    # export_state format: {"states": {"node_id/lane": state_dict}}
    if states_export is None:
        states_export = {
            "states": {
                "prya/docs": {
                    "stage": "disabled",
                    "effective_stage": "disabled",
                    "paused": False,
                    "pre_pause_stage": None,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            }
        }
    controller.export_state.return_value = states_export
    return controller


# ---------------------------------------------------------------------------
# App factory with auth bypassed
# ---------------------------------------------------------------------------


def _build_app(controller: Mock) -> FastAPI:
    """Build a minimal FastAPI app with the canary router and auth bypassed."""
    from forge_harness.webhook_server.api import canary as canary_module
    from forge_harness.webhook_server.core import dependencies

    app = FastAPI()
    app.include_router(canary_module.router)

    # Bypass auth
    _admin_user = {"sub": "test", "permissions": ["admin"]}
    app.dependency_overrides[dependencies.verify_auth] = lambda: _admin_user
    app.dependency_overrides[dependencies.verify_unified_auth] = lambda: _admin_user

    def _noop_require_permission(permission: str):
        async def _check():
            return _admin_user
        return _check

    app.dependency_overrides[dependencies.require_permission] = _noop_require_permission

    return app


# ---------------------------------------------------------------------------
# GET /api/canary/rollouts
# ---------------------------------------------------------------------------


class TestListCanaryRollouts:
    """Tests for GET /api/canary/rollouts."""

    def test_returns_200_with_rollouts(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "rollouts" in data
        assert "count" in data
        assert "summary" in data

    def test_returns_list_of_rollout_rows(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts")

        data = resp.json()["data"]
        rollouts = data["rollouts"]
        assert isinstance(rollouts, list)
        assert len(rollouts) == 1
        row = rollouts[0]
        assert row["rollout_id"] == "prya:docs"
        assert row["node_id"] == "prya"
        assert row["lane"] == "docs"

    def test_filter_by_node_id(self):
        controller = _make_canary_controller(
            states_export={
                "states": {
                    "prya/docs": {
                        "stage": "disabled",
                        "effective_stage": "disabled",
                        "paused": False,
                        "pre_pause_stage": None,
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                    "sati/docs": {
                        "stage": "canary",
                        "effective_stage": "canary",
                        "paused": False,
                        "pre_pause_stage": None,
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                }
            }
        )
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts?node_id=prya")

        data = resp.json()["data"]
        assert all(r["node_id"] == "prya" for r in data["rollouts"])

    def test_filter_by_lane(self):
        controller = _make_canary_controller(
            states_export={
                "states": {
                    "prya/docs": {
                        "stage": "disabled",
                        "effective_stage": "disabled",
                        "paused": False,
                        "pre_pause_stage": None,
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                    "prya/test_writing": {
                        "stage": "canary",
                        "effective_stage": "canary",
                        "paused": False,
                        "pre_pause_stage": None,
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                }
            }
        )
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts?lane=docs")

        data = resp.json()["data"]
        assert all(r["lane"] == "docs" for r in data["rollouts"])

    def test_invalid_lane_returns_400(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts?lane=invalid_lane_xyz")

        assert resp.status_code == 400

    def test_limit_query_param_respected(self):
        # Two states in export, limit=1 should return 1 rollout
        controller = _make_canary_controller(
            states_export={
                "states": {
                    "prya/docs": {
                        "stage": "disabled",
                        "effective_stage": "disabled",
                        "paused": False,
                        "pre_pause_stage": None,
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                    "sati/docs": {
                        "stage": "canary",
                        "effective_stage": "canary",
                        "paused": False,
                        "pre_pause_stage": None,
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                }
            }
        )
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts?limit=1")

        data = resp.json()["data"]
        assert data["count"] == 1
        assert len(data["rollouts"]) == 1

    def test_empty_states_returns_empty_list(self):
        controller = _make_canary_controller(states_export={"states": {}})
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts")

        data = resp.json()["data"]
        assert data["count"] == 0
        assert data["rollouts"] == []

    def test_malformed_state_key_is_skipped(self):
        # A key without "/" separator should be silently skipped
        controller = _make_canary_controller(
            states_export={
                "states": {
                    "nodewithoutslash": {
                        "stage": "disabled",
                        "effective_stage": "disabled",
                        "paused": False,
                        "pre_pause_stage": None,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                }
            }
        )
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts")

        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 0


# ---------------------------------------------------------------------------
# POST /api/canary/rollouts
# ---------------------------------------------------------------------------


class TestCreateCanaryRollout:
    """Tests for POST /api/canary/rollouts."""

    def test_creates_rollout_successfully(self):
        state = _make_rollout_state(stage="canary", effective_stage="canary")
        transition = _make_transition(from_stage="disabled", to_stage="canary")
        controller = _make_canary_controller(state=state, transition=transition)

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts",
                json={"node_id": "prya", "lane": "docs", "stage": "canary"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "rollout" in data
        assert "transition" in data

    def test_invalid_lane_returns_400(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts",
                json={"node_id": "prya", "lane": "not_a_lane", "stage": "canary"},
            )

        assert resp.status_code == 400

    def test_invalid_stage_returns_400(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts",
                json={"node_id": "prya", "lane": "docs", "stage": "supersonic"},
            )

        assert resp.status_code == 400

    def test_empty_node_id_returns_422(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts",
                json={"node_id": "", "lane": "docs", "stage": "canary"},
            )

        assert resp.status_code == 422

    def test_state_missing_after_set_returns_500(self):
        controller = _make_canary_controller()
        controller.get_state.return_value = None  # Simulate missing state

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts",
                json={"node_id": "prya", "lane": "docs", "stage": "canary"},
            )

        assert resp.status_code == 500

    def test_node_id_is_lowercased(self):
        state = _make_rollout_state(node_id="prya")
        transition = _make_transition()
        controller = _make_canary_controller(state=state, transition=transition)

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts",
                json={"node_id": "PRYA", "lane": "docs", "stage": "disabled"},
            )

        assert resp.status_code == 200
        # set_stage was called with lowercase node_id
        call_kwargs = controller.set_stage.call_args
        assert call_kwargs.kwargs["node_id"] == "prya"

    def test_custom_operator_and_reason(self):
        state = _make_rollout_state()
        transition = _make_transition()
        controller = _make_canary_controller(state=state, transition=transition)

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts",
                json={
                    "node_id": "prya",
                    "lane": "docs",
                    "stage": "disabled",
                    "operator": "alice",
                    "reason": "test run",
                },
            )

        assert resp.status_code == 200
        call_kwargs = controller.set_stage.call_args
        assert call_kwargs.kwargs["operator"] == "alice"
        assert call_kwargs.kwargs["reason"] == "test run"


# ---------------------------------------------------------------------------
# GET /api/canary/rollouts/{rollout_id}
# ---------------------------------------------------------------------------


class TestGetCanaryRollout:
    """Tests for GET /api/canary/rollouts/{rollout_id}."""

    def test_returns_rollout_with_transitions(self):
        state = _make_rollout_state()
        transition = _make_transition()
        controller = _make_canary_controller(state=state, transition=transition)

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts/prya:docs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "rollout" in data
        assert "transitions" in data
        assert isinstance(data["transitions"], list)

    def test_returns_404_for_missing_rollout(self):
        controller = _make_canary_controller()
        controller.get_state.return_value = None

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts/prya:docs")

        assert resp.status_code == 404

    def test_invalid_rollout_id_format_returns_400(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            # No colon separator
            resp = client.get("/api/canary/rollouts/pryadocs")

        assert resp.status_code == 400

    def test_invalid_lane_in_rollout_id_returns_400(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts/prya:invalid_lane_xyz")

        assert resp.status_code == 400

    def test_rollout_id_row_fields(self):
        state = _make_rollout_state(node_id="sati", lane="test_writing", stage="enabled")
        controller = _make_canary_controller(state=state)

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.get("/api/canary/rollouts/sati:test_writing")

        data = resp.json()["data"]
        rollout = data["rollout"]
        assert rollout["rollout_id"] == "sati:test_writing"
        assert rollout["node_id"] == "sati"
        assert rollout["lane"] == "test_writing"


# ---------------------------------------------------------------------------
# POST /api/canary/rollouts/{rollout_id}/pause
# ---------------------------------------------------------------------------


class TestPauseCanaryRollout:
    """Tests for POST /api/canary/rollouts/{rollout_id}/pause."""

    def test_pauses_rollout_successfully(self):
        state = _make_rollout_state(paused=True, pre_pause_stage="canary")
        transition = _make_transition(transition_type="pause")
        controller = _make_canary_controller(state=state, transition=transition)

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/pause",
                json={"operator": "alice", "reason": "maintenance"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "rollout" in data
        assert "transition" in data

    def test_already_paused_returns_409(self):
        controller = _make_canary_controller()
        controller.pause_pair.side_effect = ValueError("Rollout is already paused")

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/pause",
                json={"operator": "api"},
            )

        assert resp.status_code == 409

    def test_state_missing_after_pause_returns_500(self):
        transition = _make_transition(transition_type="pause")
        controller = _make_canary_controller(transition=transition)
        controller.pause_pair.return_value = transition
        controller.get_state.return_value = None

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/pause",
                json={"operator": "api"},
            )

        assert resp.status_code == 500

    def test_invalid_rollout_id_returns_400(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya_no_colon/pause",
                json={"operator": "api"},
            )

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/canary/rollouts/{rollout_id}/resume
# ---------------------------------------------------------------------------


class TestResumeCanaryRollout:
    """Tests for POST /api/canary/rollouts/{rollout_id}/resume."""

    def test_resumes_rollout_successfully(self):
        state = _make_rollout_state(paused=False, stage="canary")
        transition = _make_transition(transition_type="resume", to_stage="canary")
        controller = _make_canary_controller(state=state, transition=transition)
        controller.resume_pair.return_value = transition

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/resume",
                json={"operator": "alice"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "rollout" in data
        assert "transition" in data

    def test_not_paused_returns_409(self):
        controller = _make_canary_controller()
        controller.resume_pair.side_effect = ValueError("Rollout is not paused")

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/resume",
                json={"operator": "api"},
            )

        assert resp.status_code == 409

    def test_state_missing_after_resume_returns_500(self):
        transition = _make_transition(transition_type="resume")
        controller = _make_canary_controller(transition=transition)
        controller.resume_pair.return_value = transition
        controller.get_state.return_value = None

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/resume",
                json={"operator": "api"},
            )

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/canary/rollouts/{rollout_id}/promote
# ---------------------------------------------------------------------------


class TestPromoteCanaryRollout:
    """Tests for POST /api/canary/rollouts/{rollout_id}/promote."""

    def test_promotes_disabled_to_canary(self):
        # current effective_stage is disabled -> should promote to canary
        current_state = _make_rollout_state(stage="disabled", effective_stage="disabled")
        promoted_state = _make_rollout_state(stage="canary", effective_stage="canary")
        transition = _make_transition(from_stage="disabled", to_stage="canary")
        controller = _make_canary_controller(state=current_state, transition=transition)
        # First call returns current state, subsequent call returns promoted state
        controller.get_state.side_effect = [current_state, promoted_state]
        controller.set_stage.return_value = transition

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/promote",
                json={"operator": "alice"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "rollout" in data
        assert "transition" in data

    def test_promotes_canary_to_enabled(self):
        current_state = _make_rollout_state(stage="canary", effective_stage="canary")
        enabled_state = _make_rollout_state(stage="enabled", effective_stage="enabled")
        transition = _make_transition(from_stage="canary", to_stage="enabled")
        controller = _make_canary_controller(state=current_state, transition=transition)
        controller.get_state.side_effect = [current_state, enabled_state]

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/promote",
                json={"operator": "alice"},
            )

        assert resp.status_code == 200

    def test_already_enabled_returns_409(self):
        current_state = _make_rollout_state(stage="enabled", effective_stage="enabled")
        controller = _make_canary_controller(state=current_state)
        controller.get_state.return_value = current_state

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/promote",
                json={"operator": "api"},
            )

        assert resp.status_code == 409

    def test_explicit_stage_in_body_is_used(self):
        current_state = _make_rollout_state(stage="disabled", effective_stage="disabled")
        enabled_state = _make_rollout_state(stage="enabled", effective_stage="enabled")
        transition = _make_transition(from_stage="disabled", to_stage="enabled")
        controller = _make_canary_controller(state=current_state, transition=transition)
        controller.get_state.side_effect = [current_state, enabled_state]

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/promote",
                json={"operator": "alice", "stage": "enabled"},
            )

        assert resp.status_code == 200

    def test_invalid_explicit_stage_returns_400(self):
        current_state = _make_rollout_state(stage="disabled", effective_stage="disabled")
        controller = _make_canary_controller(state=current_state)

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/promote",
                json={"operator": "alice", "stage": "hypermode"},
            )

        assert resp.status_code == 400

    def test_promote_with_no_existing_state(self):
        # get_state returns None (no state yet): effective_stage defaults to disabled
        transition = _make_transition(from_stage="disabled", to_stage="canary")
        after_state = _make_rollout_state(stage="canary", effective_stage="canary")
        controller = _make_canary_controller()
        controller.get_state.side_effect = [None, after_state]
        controller.set_stage.return_value = transition

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/promote",
                json={"operator": "api"},
            )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/canary/rollouts/{rollout_id}/rollback
# ---------------------------------------------------------------------------


class TestRollbackCanaryRollout:
    """Tests for POST /api/canary/rollouts/{rollout_id}/rollback."""

    def test_rolls_back_successfully(self):
        state = _make_rollout_state(stage="disabled", effective_stage="disabled")
        transition = _make_transition(
            from_stage="canary", to_stage="disabled", transition_type="revert"
        )
        controller = _make_canary_controller(state=state, transition=transition)
        controller.revert.return_value = transition

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/rollback",
                json={"operator": "alice", "reason": "quality gate failed"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "rollout" in data
        assert "transition" in data

    def test_already_disabled_returns_409(self):
        controller = _make_canary_controller()
        controller.revert.side_effect = ValueError("Already at disabled stage")

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/rollback",
                json={"operator": "api"},
            )

        assert resp.status_code == 409

    def test_state_missing_after_rollback_returns_500(self):
        transition = _make_transition(transition_type="revert")
        controller = _make_canary_controller(transition=transition)
        controller.revert.return_value = transition
        controller.get_state.return_value = None

        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/prya:docs/rollback",
                json={"operator": "api"},
            )

        assert resp.status_code == 500

    def test_invalid_rollout_id_returns_400(self):
        controller = _make_canary_controller()
        with patch(
            "forge_harness.webhook_server.api.canary.get_canary_control",
            return_value=controller,
        ):
            app = _build_app(controller)
            client = TestClient(app)
            resp = client.post(
                "/api/canary/rollouts/nocolon/rollback",
                json={"operator": "api"},
            )

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Pydantic model validation tests
# ---------------------------------------------------------------------------


class TestCreateCanaryRolloutRequest:
    """Validate Pydantic model CreateCanaryRolloutRequest."""

    def test_minimal_valid_request(self):
        from forge_harness.webhook_server.api.canary import CreateCanaryRolloutRequest

        req = CreateCanaryRolloutRequest(node_id="prya", lane="docs")
        assert req.node_id == "prya"
        assert req.lane == "docs"
        assert req.stage == "canary"
        assert req.operator == "api"
        assert req.reason == ""

    def test_empty_node_id_raises(self):
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.canary import CreateCanaryRolloutRequest

        with pytest.raises(ValidationError):
            CreateCanaryRolloutRequest(node_id="", lane="docs")

    def test_empty_lane_raises(self):
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.canary import CreateCanaryRolloutRequest

        with pytest.raises(ValidationError):
            CreateCanaryRolloutRequest(node_id="prya", lane="")


class TestCanaryActionRequest:
    """Validate Pydantic model CanaryActionRequest."""

    def test_defaults(self):
        from forge_harness.webhook_server.api.canary import CanaryActionRequest

        req = CanaryActionRequest()
        assert req.operator == "api"
        assert req.reason == ""
        assert req.stage is None

    def test_custom_values(self):
        from forge_harness.webhook_server.api.canary import CanaryActionRequest

        req = CanaryActionRequest(operator="alice", reason="planned", stage="enabled")
        assert req.operator == "alice"
        assert req.reason == "planned"
        assert req.stage == "enabled"

    def test_empty_operator_raises(self):
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.canary import CanaryActionRequest

        with pytest.raises(ValidationError):
            CanaryActionRequest(operator="")
