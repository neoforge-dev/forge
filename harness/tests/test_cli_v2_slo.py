"""Tests for forge slo CLI commands.

Covers forge_harness/cli_v2/slo.py:
    - forge slo dashboard
    - forge slo breaches [--window N]
    - forge slo lanes LANE_NAME

Strategy:
    - CliRunner.invoke for all integration paths.
    - CommandCenterClient._request is mocked to return pre-built APIResponse
      objects so no real HTTP is attempted.
    - JSON envelope shape is validated for --json output paths.
    - Error/unavailability branches are exercised.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

from forge_harness.command_center_client import APIResponse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def healthy_slos():
    """Sample /api/slo response payload (all lanes healthy)."""
    return {
        "slos": [
            {
                "lane": "api_simple",
                "status": "healthy",
                "success_rate": 0.98,
                "mean_lead_time_seconds": 1.2,
                "slo_target_seconds": 5.0,
                "sample_count": 120,
            },
            {
                "lane": "deployment",
                "status": "healthy",
                "success_rate": 1.0,
                "mean_lead_time_seconds": 45.0,
                "slo_target_seconds": 300.0,
                "sample_count": 8,
            },
        ],
        "count": 2,
    }


@pytest.fixture
def breached_slos():
    """Sample /api/slo/breaches response payload with one breach."""
    return {
        "breaches": [
            {
                "lane": "api_complex",
                "status": "breached",
                "success_rate": 0.72,
                "mean_lead_time_seconds": 18.5,
                "slo_target_seconds": 10.0,
                "sample_count": 50,
            }
        ],
        "count": 1,
        "window_minutes": 60,
    }


@pytest.fixture
def lane_detail():
    """Sample /api/slo/{lane} response for a single lane."""
    return {
        "lane": "api_simple",
        "status": "healthy",
        "success_rate": 0.98,
        "mean_lead_time_seconds": 1.2,
        "slo_target_seconds": 5.0,
        "sample_count": 120,
    }


def _make_response(data, success=True):
    """Build an APIResponse from a data payload."""
    return APIResponse(success=success, data=data, error=None)


def _patch_request(monkeypatch_or_patch, return_value):
    """Return a context manager that patches CommandCenterClient._request."""
    mock_resp = _make_response(return_value)
    mock_coro = AsyncMock(return_value=mock_resp)
    return patch(
        "forge_harness.command_center_client.CommandCenterClient._request",
        mock_coro,
    )


# ---------------------------------------------------------------------------
# forge slo dashboard
# ---------------------------------------------------------------------------


class TestSloDashboard:
    """Tests for `forge slo dashboard`."""

    def test_dashboard_renders_table(self, runner, healthy_slos):
        """Dashboard shows a Rich table with lane data."""
        mock_resp = _make_response(healthy_slos)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "dashboard"])

        assert result.exit_code == 0, result.output
        assert "api_simple" in result.output
        assert "deployment" in result.output
        # Status column should reflect healthy
        assert "healthy" in result.output

    def test_dashboard_json_output(self, runner, healthy_slos):
        """Dashboard --json returns a valid FORGE envelope with slo data."""
        mock_resp = _make_response(healthy_slos)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "--json", "dashboard"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["command"] == "forge slo dashboard"
        assert "data" in payload
        assert "slos" in payload["data"]

    def test_dashboard_service_unavailable(self, runner):
        """Dashboard exits with code 4 when API returns None."""
        mock_resp = APIResponse(success=False, data=None, error="connection refused")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "dashboard"])

        assert result.exit_code == 4

    def test_dashboard_service_unavailable_json(self, runner):
        """Dashboard --json emits error envelope when API unavailable."""
        mock_resp = APIResponse(success=False, data=None, error="connection refused")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "--json", "dashboard"])

        assert result.exit_code == 4
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_dashboard_empty_slos(self, runner):
        """Dashboard shows a dim message when no SLO data is returned."""
        mock_resp = _make_response({"slos": [], "count": 0})
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "dashboard"])

        assert result.exit_code == 0, result.output
        assert "No SLO data available" in result.output

    def test_dashboard_breached_lane_highlighted(self, runner):
        """Dashboard output contains breach indication for breached lanes."""
        data = {
            "slos": [
                {
                    "lane": "api_complex",
                    "status": "breached",
                    "success_rate": 0.70,
                    "mean_lead_time_seconds": 20.0,
                    "slo_target_seconds": 10.0,
                    "sample_count": 30,
                }
            ],
            "count": 1,
        }
        mock_resp = _make_response(data)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "dashboard"])

        assert result.exit_code == 0, result.output
        assert "api_complex" in result.output
        assert "breached" in result.output

    def test_dashboard_missing_optional_fields(self, runner):
        """Dashboard handles lanes that are missing optional numeric fields."""
        data = {
            "slos": [
                {
                    "lane": "review",
                    "status": "unknown",
                    # success_rate, mean_lead_time_seconds, slo_target_seconds absent
                }
            ],
            "count": 1,
        }
        mock_resp = _make_response(data)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "dashboard"])

        assert result.exit_code == 0, result.output
        assert "review" in result.output


# ---------------------------------------------------------------------------
# forge slo breaches
# ---------------------------------------------------------------------------


class TestSloBreaches:
    """Tests for `forge slo breaches`."""

    def test_breaches_with_active_breach(self, runner, breached_slos):
        """Breaches command shows breached lanes in a table."""
        mock_resp = _make_response(breached_slos)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "breaches"])

        assert result.exit_code == 0, result.output
        assert "api_complex" in result.output

    def test_breaches_no_breaches(self, runner):
        """Breaches command prints a green 'no breaches' message when list is empty."""
        mock_resp = _make_response({"breaches": [], "count": 0, "window_minutes": 60})
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "breaches"])

        assert result.exit_code == 0, result.output
        assert "No SLO breaches" in result.output

    def test_breaches_json_output(self, runner, breached_slos):
        """Breaches --json returns a valid FORGE envelope."""
        mock_resp = _make_response(breached_slos)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "--json", "breaches"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["command"] == "forge slo breaches"
        assert "breaches" in payload["data"]

    def test_breaches_custom_window(self, runner):
        """--window flag is forwarded to the API as window_minutes param."""
        captured_params: list[dict] = []

        async def _fake_request(self, method, path, data=None, params=None):
            captured_params.append(params or {})
            return _make_response({"breaches": [], "count": 0, "window_minutes": 30})

        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            _fake_request,
        ):
            result = runner.invoke(cli, ["slo", "breaches", "--window", "30"])

        assert result.exit_code == 0, result.output
        assert any(p.get("window_minutes") == 30 for p in captured_params)

    def test_breaches_service_unavailable(self, runner):
        """Breaches exits with code 4 when API is unreachable."""
        mock_resp = APIResponse(success=False, data=None, error="connection refused")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "breaches"])

        assert result.exit_code == 4

    def test_breaches_service_unavailable_json(self, runner):
        """Breaches --json emits error envelope when unavailable."""
        mock_resp = APIResponse(success=False, data=None, error="timeout")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "--json", "breaches"])

        assert result.exit_code == 4
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_breaches_missing_optional_fields(self, runner):
        """Breaches handles entries without optional numeric fields gracefully."""
        data = {
            "breaches": [
                {
                    "lane": "testing",
                    "status": "breached",
                    # success_rate absent
                }
            ],
            "count": 1,
            "window_minutes": 60,
        }
        mock_resp = _make_response(data)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "breaches"])

        assert result.exit_code == 0, result.output
        assert "testing" in result.output


# ---------------------------------------------------------------------------
# forge slo lanes LANE_NAME
# ---------------------------------------------------------------------------


class TestSloLanes:
    """Tests for `forge slo lanes LANE_NAME`."""

    def test_lanes_shows_detail(self, runner, lane_detail):
        """Lanes command displays detailed stats for a single lane."""
        mock_resp = _make_response(lane_detail)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "lanes", "api_simple"])

        assert result.exit_code == 0, result.output
        assert "api_simple" in result.output
        assert "healthy" in result.output
        assert "98.0%" in result.output or "0.98" in result.output or "98" in result.output

    def test_lanes_json_output(self, runner, lane_detail):
        """Lanes --json returns a valid FORGE envelope with lane data."""
        mock_resp = _make_response(lane_detail)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "--json", "lanes", "api_simple"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["command"] == "forge slo lanes"
        assert payload["data"]["lane"] == "api_simple"

    def test_lanes_not_found(self, runner):
        """Lanes exits with code 6 when lane is not recognised by the API."""
        mock_resp = APIResponse(success=False, data=None, error="not found")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "lanes", "bad_lane"])

        assert result.exit_code == 6

    def test_lanes_not_found_json(self, runner):
        """Lanes --json emits error envelope for unknown lane."""
        mock_resp = APIResponse(success=False, data=None, error="not found")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "--json", "lanes", "bad_lane"])

        assert result.exit_code == 6
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_lanes_missing_argument(self, runner):
        """Lanes errors when no LANE_NAME is provided."""
        result = runner.invoke(cli, ["slo", "lanes"])
        assert result.exit_code != 0

    def test_lanes_breached_status(self, runner):
        """Lanes displays 'breached' status for a lane in breach."""
        data = {
            "lane": "api_complex",
            "status": "breached",
            "success_rate": 0.70,
            "mean_lead_time_seconds": 18.5,
            "slo_target_seconds": 10.0,
            "sample_count": 50,
        }
        mock_resp = _make_response(data)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "lanes", "api_complex"])

        assert result.exit_code == 0, result.output
        assert "breached" in result.output

    def test_lanes_missing_numeric_fields(self, runner):
        """Lanes handles responses that omit numeric fields without crashing."""
        data = {
            "lane": "review",
            "status": "healthy",
            # No success_rate, mean_lead_time_seconds, slo_target_seconds
        }
        mock_resp = _make_response(data)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "lanes", "review"])

        assert result.exit_code == 0, result.output
        assert "review" in result.output

    def test_lanes_unknown_status(self, runner):
        """Lanes handles 'unknown' status values without errors."""
        data = {
            "lane": "testing",
            "status": "unknown",
            "sample_count": 0,
        }
        mock_resp = _make_response(data)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["slo", "lanes", "testing"])

        assert result.exit_code == 0, result.output
        assert "unknown" in result.output


# ---------------------------------------------------------------------------
# Help text sanity checks
# ---------------------------------------------------------------------------


class TestSloHelp:
    """Verify --help output is present and correct for all slo subcommands."""

    def test_slo_group_help(self, runner):
        result = runner.invoke(cli, ["slo", "--help"])
        assert result.exit_code == 0
        assert "dashboard" in result.output
        assert "breaches" in result.output
        assert "lanes" in result.output

    def test_dashboard_help(self, runner):
        result = runner.invoke(cli, ["slo", "dashboard", "--help"])
        assert result.exit_code == 0

    def test_breaches_help(self, runner):
        result = runner.invoke(cli, ["slo", "breaches", "--help"])
        assert result.exit_code == 0
        assert "--window" in result.output

    def test_lanes_help(self, runner):
        result = runner.invoke(cli, ["slo", "lanes", "--help"])
        assert result.exit_code == 0
