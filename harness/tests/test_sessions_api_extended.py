"""Tests for webhook_server/api/sessions.py — API endpoint coverage.

Tests the FastAPI router mounted at /api/sessions using the TestClient.
Authentication is bypassed by overriding the verify_auth dependency.
"""

from dataclasses import asdict
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_harness.session_tracker import AgentSession
from forge_harness.webhook_server.api.sessions import api_response, router
from forge_harness.webhook_server.core.dependencies import verify_auth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_test_app() -> FastAPI:
    """Create a minimal FastAPI app with the sessions router and auth bypassed."""
    app = FastAPI()
    app.include_router(router)
    # Override auth dependency so tests don't need real tokens
    app.dependency_overrides[verify_auth] = lambda: {
        "sub": "test-user",
        "permissions": ["read", "write"],
    }
    return app


@pytest.fixture
def client():
    """Return a TestClient with auth bypassed."""
    app = build_test_app()
    return TestClient(app, raise_server_exceptions=True)


def _make_session(**kwargs) -> AgentSession:
    defaults = dict(
        session_name="forge:cursor",
        window_name="cursor",
        agent_type="claude",
        domain="codeswiftr-com",
        project="interview-simulator",
        current_task="implement feature",
        started_at="2026-01-01T10:00:00",
        last_activity="2026-01-01T11:00:00",
        terminal_output="Working on it...",
        status="active",
    )
    defaults.update(kwargs)
    return AgentSession(**defaults)


# ---------------------------------------------------------------------------
# api_response() unit tests
# ---------------------------------------------------------------------------


class TestApiResponseHelper:
    """Tests for the api_response() utility function."""

    def test_success_response_structure(self):
        resp = api_response(data={"key": "value"})
        assert resp["success"] is True
        assert resp["data"] == {"key": "value"}
        assert resp["error"] is None
        assert "timestamp" in resp

    def test_success_response_with_none_data(self):
        resp = api_response()
        assert resp["success"] is True
        assert resp["data"] is None

    def test_error_response_with_code_and_message(self):
        resp = api_response(error_code="NOT_FOUND", error_message="Session not found")
        assert resp["success"] is False
        assert resp["data"] is None
        assert resp["error"]["code"] == "NOT_FOUND"
        assert resp["error"]["message"] == "Session not found"

    def test_error_response_defaults_to_unknown_code(self):
        resp = api_response(error_message="Something went wrong")
        assert resp["error"]["code"] == "UNKNOWN_ERROR"

    def test_error_response_defaults_to_generic_message(self):
        resp = api_response(error_code="INTERNAL_ERROR")
        assert resp["error"]["message"] == "An error occurred"

    def test_timestamp_is_iso_format(self):
        resp = api_response(data="test")
        # Should parse without exception
        datetime.fromisoformat(resp["timestamp"])

    def test_success_with_list_data(self):
        resp = api_response(data=[1, 2, 3])
        assert resp["data"] == [1, 2, 3]
        assert resp["success"] is True


# ---------------------------------------------------------------------------
# GET /api/sessions/active
# ---------------------------------------------------------------------------


class TestListActiveSessionsEndpoint:
    """Tests for GET /api/sessions/active."""

    def test_returns_200_with_empty_list(self, client):
        mock_tracker = MagicMock()
        mock_tracker.get_sessions_summary.return_value = {
            "total": 0,
            "by_status": {},
            "by_type": {},
            "sessions": [],
        }
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/active")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["sessions"] == []
        assert body["data"]["total"] == 0
        assert body["data"]["by_status"] == {}
        assert body["data"]["by_type"] == {}

    def test_returns_session_list(self, client):
        session_dict = asdict(_make_session())
        mock_tracker = MagicMock()
        mock_tracker.get_sessions_summary.return_value = {
            "total": 1,
            "by_status": {"active": 1},
            "by_type": {"claude": 1},
            "sessions": [session_dict],
        }
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/active")
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["window_name"] == "cursor"

    def test_context_usage_calculated_from_output_length(self, client):
        session_dict = asdict(_make_session(terminal_output="x" * 250))
        mock_tracker = MagicMock()
        mock_tracker.get_sessions_summary.return_value = {
            "total": 1,
            "by_status": {},
            "by_type": {},
            "sessions": [session_dict],
        }
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/active")
        data = response.json()["data"]
        # 250 chars / 500 * 100 = 50
        assert data["sessions"][0]["context_usage"] == 50

    def test_context_usage_capped_at_100(self, client):
        session_dict = asdict(_make_session(terminal_output="x" * 1000))
        mock_tracker = MagicMock()
        mock_tracker.get_sessions_summary.return_value = {
            "total": 1,
            "by_status": {},
            "by_type": {},
            "sessions": [session_dict],
        }
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/active")
        data = response.json()["data"]
        assert data["sessions"][0]["context_usage"] == 100

    def test_context_usage_zero_when_no_output(self, client):
        session_dict = asdict(_make_session(terminal_output=None))
        mock_tracker = MagicMock()
        mock_tracker.get_sessions_summary.return_value = {
            "total": 1,
            "by_status": {},
            "by_type": {},
            "sessions": [session_dict],
        }
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/active")
        data = response.json()["data"]
        assert data["sessions"][0]["context_usage"] == 0

    def test_multiple_sessions_returned(self, client):
        sessions = [
            asdict(_make_session(window_name="cursor", session_name="forge:cursor")),
            asdict(
                _make_session(
                    window_name="gemini", session_name="forge:gemini", agent_type="gemini"
                )
            ),
        ]
        mock_tracker = MagicMock()
        mock_tracker.get_sessions_summary.return_value = {
            "total": 2,
            "by_status": {},
            "by_type": {},
            "sessions": sessions,
        }
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/active")
        assert len(response.json()["data"]["sessions"]) == 2


# ---------------------------------------------------------------------------
# GET /api/sessions/{window_name}
# ---------------------------------------------------------------------------


class TestGetSessionDetailsEndpoint:
    """Tests for GET /api/sessions/{window_name}."""

    def test_returns_200_with_session_data(self, client):
        session = _make_session()
        mock_tracker = MagicMock()
        mock_tracker.get_session.return_value = session
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/cursor")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["window_name"] == "cursor"
        assert body["data"]["agent_type"] == "claude"

    def test_returns_404_when_session_not_found(self, client):
        mock_tracker = MagicMock()
        mock_tracker.get_session.return_value = None
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/nonexistent")
        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "not_found"
        assert "nonexistent" in body["error"]["message"]

    def test_calls_get_session_with_correct_window_name(self, client):
        session = _make_session(window_name="my-agent", session_name="forge:my-agent")
        mock_tracker = MagicMock()
        mock_tracker.get_session.return_value = session
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            client.get("/api/sessions/my-agent")
        mock_tracker.get_session.assert_called_once_with("my-agent")

    def test_response_includes_all_session_fields(self, client):
        session = _make_session()
        mock_tracker = MagicMock()
        mock_tracker.get_session.return_value = session
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/cursor")
        data = response.json()["data"]
        assert "session_name" in data
        assert "domain" in data
        assert "project" in data
        assert "status" in data
        assert "terminal_output" in data


# ---------------------------------------------------------------------------
# POST /api/sessions/{window_name}/metadata
# ---------------------------------------------------------------------------


class TestUpdateSessionMetadataPost:
    """Tests for POST /api/sessions/{window_name}/metadata."""

    def test_returns_200_and_updated_true(self, client):
        mock_tracker = MagicMock()
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.post(
                "/api/sessions/cursor/metadata",
                json={"domain": "codeswiftr-com", "project": "vc"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["updated"] is True
        assert body["data"]["window"] == "cursor"

    def test_calls_save_session_metadata_with_body(self, client):
        mock_tracker = MagicMock()
        payload = {"domain": "test-domain", "task": "do stuff"}
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            client.post("/api/sessions/cursor/metadata", json=payload)
        mock_tracker.save_session_metadata.assert_called_once_with("cursor", payload)

    def test_accepts_empty_body(self, client):
        mock_tracker = MagicMock()
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.post("/api/sessions/cursor/metadata", json={})
        assert response.status_code == 200

    def test_window_name_in_response(self, client):
        mock_tracker = MagicMock()
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.post("/api/sessions/special-window/metadata", json={"x": "y"})
        assert response.json()["data"]["window"] == "special-window"


# ---------------------------------------------------------------------------
# PUT /api/sessions/{window_name}/metadata
# ---------------------------------------------------------------------------


class TestUpdateSessionMetadataPut:
    """Tests for PUT /api/sessions/{window_name}/metadata."""

    def test_returns_200_and_updated_true(self, client):
        mock_tracker = MagicMock()
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.put(
                "/api/sessions/cursor/metadata",
                json={"domain": "brandfocus-ai"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["updated"] is True

    def test_calls_save_session_metadata_with_body(self, client):
        mock_tracker = MagicMock()
        payload = {"project": "voice-coach"}
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            client.put("/api/sessions/cursor/metadata", json=payload)
        mock_tracker.save_session_metadata.assert_called_once_with("cursor", payload)

    def test_put_and_post_have_same_behaviour(self, client):
        """POST and PUT endpoints must behave identically."""
        mock_tracker_post = MagicMock()
        mock_tracker_put = MagicMock()
        payload = {"domain": "same-domain"}

        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker_post,
        ):
            post_resp = client.post("/api/sessions/win/metadata", json=payload)

        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker_put,
        ):
            put_resp = client.put("/api/sessions/win/metadata", json=payload)

        assert post_resp.status_code == put_resp.status_code
        assert post_resp.json()["data"] == put_resp.json()["data"]


# ---------------------------------------------------------------------------
# GET /api/sessions/{window_name}/output
# ---------------------------------------------------------------------------


class TestGetSessionOutputEndpoint:
    """Tests for GET /api/sessions/{window_name}/output."""

    def test_returns_200_with_output(self, client):
        mock_tracker = MagicMock()
        mock_tracker.get_session_output.return_value = "line1\nline2\nline3"
        mock_tracker.list_tmux_sessions.return_value = ["cursor"]
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/cursor/output")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["window"] == "cursor"
        assert "line1" in body["data"]["output"]

    def test_default_lines_is_50(self, client):
        mock_tracker = MagicMock()
        mock_tracker.get_session_output.return_value = "some output"
        mock_tracker.list_tmux_sessions.return_value = ["cursor"]
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            client.get("/api/sessions/cursor/output")
        # lines=50 is default; verify get_session_output was called with 50
        mock_tracker.get_session_output.assert_called_once_with("cursor", 50)

    def test_custom_lines_param(self, client):
        mock_tracker = MagicMock()
        mock_tracker.get_session_output.return_value = "output"
        mock_tracker.list_tmux_sessions.return_value = ["cursor"]
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            client.get("/api/sessions/cursor/output?lines=30")
        mock_tracker.get_session_output.assert_called_once_with("cursor", 30)

    def test_lines_capped_at_200(self, client):
        mock_tracker = MagicMock()
        mock_tracker.get_session_output.return_value = "output"
        mock_tracker.list_tmux_sessions.return_value = ["cursor"]
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            client.get("/api/sessions/cursor/output?lines=500")
        # Should be capped at 200
        mock_tracker.get_session_output.assert_called_once_with("cursor", 200)

    def test_returns_404_when_no_output_and_not_in_sessions(self, client):
        mock_tracker = MagicMock()
        mock_tracker.get_session_output.return_value = ""
        mock_tracker.list_tmux_sessions.return_value = []  # window not found
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/missing/output")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"
        assert "missing" in body["error"]["message"]

    def test_returns_200_with_empty_output_when_session_exists(self, client):
        """Empty output is OK as long as the session is in the tmux list."""
        mock_tracker = MagicMock()
        mock_tracker.get_session_output.return_value = ""
        mock_tracker.list_tmux_sessions.return_value = ["cursor"]
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/cursor/output")
        assert response.status_code == 200
        assert response.json()["data"]["output"] == ""

    def test_response_includes_lines_field(self, client):
        mock_tracker = MagicMock()
        mock_tracker.get_session_output.return_value = "stuff"
        mock_tracker.list_tmux_sessions.return_value = ["cursor"]
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mock_tracker,
        ):
            response = client.get("/api/sessions/cursor/output?lines=25")
        assert response.json()["data"]["lines"] == 25
