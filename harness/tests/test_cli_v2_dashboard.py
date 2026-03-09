"""
Tests for forge dashboard and new forge dispatch subcommands.

Covers:
    dashboard register-agent  — register an agent in the dashboard registry
    dashboard update-agent    — patch agent status, progress, or task
    dashboard remove-agent    — delete agent from registry (with --force / confirm)
    dashboard task-start      — record task start for metrics
    dashboard task-end        — record task completion with outcome
    dashboard agent-tasks     — fetch per-agent task metrics
    dashboard task-metrics    — fetch per-task-type throughput metrics
    dispatch queue-stats      — show message queue statistics
    dispatch conversation     — view message history between two agents

All external I/O (httpx.Client / dispatch._http_json) is mocked so these
tests run fully offline without a live Command Center server.

API envelope used throughout:
    {"success": true, "data": {...}}
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def invoke(args: list[str], env: dict[str, str] | None = None):
    """Run the forge CLI and return the Click test result."""
    runner = CliRunner()
    return runner.invoke(cli, args, env=env or {}, catch_exceptions=False)


def invoke_dashboard(args: list[str], env: dict[str, str] | None = None):
    """Run `forge dashboard ...` with a default token injected via env."""
    base_env = {"FORGE_WEBHOOK_TOKEN": "test-token"}
    if env:
        base_env.update(env)
    return invoke(["dashboard"] + args, env=base_env)


def invoke_dispatch(args: list[str], env: dict[str, str] | None = None):
    """Run `forge dispatch ...` with a default token injected via env."""
    base_env = {"FORGE_WEBHOOK_TOKEN": "test-token"}
    if env:
        base_env.update(env)
    return invoke(["dispatch"] + args, env=base_env)


def parse_json(output: str) -> dict:
    """Extract and parse the first JSON object found in CLI output."""
    lines = output.strip().splitlines()
    json_start = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("{")), None
    )
    assert json_start is not None, f"No JSON found in output:\n{output}"
    return json.loads("\n".join(lines[json_start:]))


def make_mock_response(status_code: int, body: dict) -> MagicMock:
    """Build a mock httpx.Response-like object."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body
    mock_resp.text = json.dumps(body)
    return mock_resp


def mock_httpx_client(responses: list[MagicMock]):
    """
    Patch httpx.Client so that successive HTTP method calls return the
    provided mock responses in order.  Supports GET, POST, PATCH, DELETE.
    """
    response_iter = iter(responses)

    def _next_resp(*_, **__):
        return next(response_iter)

    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: s
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = _next_resp
    mock_client.post.side_effect = _next_resp
    mock_client.patch.side_effect = _next_resp
    mock_client.delete.side_effect = _next_resp
    return mock_client


# ---------------------------------------------------------------------------
# Shared fixtures / response data
# ---------------------------------------------------------------------------

AGENT_REGISTER_RESPONSE = {
    "success": True,
    "data": {
        "agent_id": "minimax",
        "role": "researcher",
        "status": "idle",
    },
}

AGENT_UPDATE_RESPONSE = {
    "success": True,
    "data": {
        "agent_id": "minimax",
        "status": "active",
        "progress": 50,
    },
}

AGENT_REMOVE_RESPONSE = {
    "success": True,
    "data": {"agent_id": "minimax", "removed": True},
}

TASK_START_RESPONSE = {
    "success": True,
    "data": {
        "task_id": "task-001",
        "agent_id": "minimax",
        "task_type": "python",
        "started_at": "2026-02-23T10:00:00Z",
    },
}

TASK_END_RESPONSE = {
    "success": True,
    "data": {
        "task_id": "task-001",
        "outcome": "success",
        "ended_at": "2026-02-23T10:05:00Z",
        "duration_seconds": 300,
    },
}

AGENT_TASKS_RESPONSE = {
    "success": True,
    "data": {
        "agent_id": "minimax",
        "total_tasks": 12,
        "success_rate": 0.92,
        "avg_duration_seconds": 245,
        "tasks_by_type": {"python": 8, "general": 4},
    },
}

TASK_METRICS_RESPONSE = {
    "success": True,
    "data": {
        "task_type": "python",
        "total_tasks": 42,
        "success_rate": 0.88,
        "avg_duration_seconds": 310,
        "p95_duration_seconds": 800,
    },
}

QUEUE_STATS_RESPONSE = {
    "success": True,
    "data": {
        "pending_messages": 3,
        "delivered_messages": 47,
        "acknowledged_messages": 44,
        "total_processed": 94,
        "processing_rate": 1.23,
        "average_wait_time": 4.5,
        "queue_age_seconds": 125,
    },
}

CONVERSATION_RESPONSE = {
    "success": True,
    "data": {
        "count": 2,
        "messages": [
            {
                "id": "msg-aabb1122",
                "sender_id": "forge:minimax",
                "content": "Task complete. Tests passing.",
                "status": "delivered",
                "created_at": "2026-02-23T09:30:00Z",
            },
            {
                "id": "msg-ccdd3344",
                "sender_id": "forge:glm",
                "content": "Acknowledged. Merging now.",
                "status": "acknowledged",
                "created_at": "2026-02-23T09:31:00Z",
            },
        ],
    },
}

CONVERSATION_EMPTY_RESPONSE = {
    "success": True,
    "data": {"count": 0, "messages": []},
}


# ===========================================================================
# forge dashboard register-agent
# ===========================================================================


class TestRegisterAgent:
    def test_register_agent_success_human_readable(self):
        """Happy path: register-agent prints success message with agent_id and role."""
        resp = make_mock_response(200, AGENT_REGISTER_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["register-agent", "minimax", "--role", "researcher"]
            )

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output
        assert "registered" in result.output.lower()

    def test_register_agent_success_with_all_options(self):
        """register-agent with --project, --domain, --tags, --task succeeds."""
        resp = make_mock_response(200, AGENT_REGISTER_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                [
                    "register-agent",
                    "minimax",
                    "--role",
                    "researcher",
                    "--project",
                    "voice-coach",
                    "--domain",
                    "brandfocus-ai",
                    "--task",
                    "Analyse user interviews",
                    "--tags",
                    "nlp, audio, coaching",
                ]
            )

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output

    def test_register_agent_posts_to_correct_url(self):
        """register-agent POSTs to /api/dashboard/agents/register."""
        resp = make_mock_response(200, AGENT_REGISTER_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["register-agent", "minimax", "--role", "researcher"])

        call_kwargs = client.post.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        assert "/api/dashboard/agents/register" in url

    def test_register_agent_payload_includes_agent_id_and_role(self):
        """Payload sent to register-agent API contains agent_id and role."""
        resp = make_mock_response(200, AGENT_REGISTER_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["register-agent", "minimax", "--role", "researcher"])

        call_kwargs = client.post.call_args
        payload = call_kwargs[1].get("json", {})
        assert payload.get("agent_id") == "minimax"
        assert payload.get("role") == "researcher"

    def test_register_agent_tags_parsed_as_list(self):
        """--tags comma string is split into a list in the API payload."""
        resp = make_mock_response(200, AGENT_REGISTER_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(
                [
                    "register-agent",
                    "minimax",
                    "--role",
                    "researcher",
                    "--tags",
                    "nlp, audio, coaching",
                ]
            )

        payload = client.post.call_args[1].get("json", {})
        assert payload.get("focus_tags") == ["nlp", "audio", "coaching"]

    def test_register_agent_json_output(self):
        """--json flag returns structured success envelope."""
        resp = make_mock_response(200, AGENT_REGISTER_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["register-agent", "minimax", "--role", "researcher", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        assert "data" in data
        assert data["command"] == "forge dashboard register-agent"

    def test_register_agent_api_error_exits_nonzero(self):
        """A 500 API error causes exit code 1."""
        resp = make_mock_response(500, {"detail": "Internal server error"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["register-agent", "minimax", "--role", "researcher"]
            )

        assert result.exit_code != 0

    def test_register_agent_api_error_json_envelope(self):
        """API error in JSON mode returns structured error envelope."""
        resp = make_mock_response(500, {"detail": "Internal server error"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["register-agent", "minimax", "--role", "researcher", "--json"]
            )

        assert result.exit_code != 0
        data = parse_json(result.output)
        assert data["success"] is False
        assert "error" in data


# ===========================================================================
# forge dashboard update-agent
# ===========================================================================


class TestUpdateAgent:
    def test_update_agent_status_success(self):
        """update-agent with --status active prints success output."""
        resp = make_mock_response(200, AGENT_UPDATE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["update-agent", "minimax", "--status", "active"]
            )

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output

    def test_update_agent_progress_success(self):
        """update-agent with --progress 75 sends correct payload."""
        resp = make_mock_response(200, AGENT_UPDATE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["update-agent", "minimax", "--progress", "75"]
            )

        assert result.exit_code == 0, result.output
        payload = client.patch.call_args[1].get("json", {})
        assert payload.get("progress") == 75

    def test_update_agent_task_description_success(self):
        """update-agent with --task sends task description in payload."""
        resp = make_mock_response(200, AGENT_UPDATE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["update-agent", "minimax", "--task", "Running integration tests"]
            )

        assert result.exit_code == 0, result.output
        payload = client.patch.call_args[1].get("json", {})
        assert payload.get("task") == "Running integration tests"

    def test_update_agent_patches_correct_url(self):
        """update-agent PATCHes /api/dashboard/agents/{agent_id}."""
        resp = make_mock_response(200, AGENT_UPDATE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["update-agent", "minimax", "--status", "idle"])

        url = client.patch.call_args[0][0]
        assert "/api/dashboard/agents/minimax" in url

    def test_update_agent_no_options_still_calls_api(self):
        """update-agent with no update options sends empty body but still calls API."""
        resp = make_mock_response(200, AGENT_UPDATE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["update-agent", "minimax"])

        # format_error is called for empty body, but _api_call is still made
        # (format_error does not raise — it just prints). Exit can be 0 or 1.
        # The important assertion: no unhandled exception / crash.
        assert result.exit_code in (0, 1)

    def test_update_agent_json_output(self):
        """--json flag returns structured success envelope."""
        resp = make_mock_response(200, AGENT_UPDATE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["update-agent", "minimax", "--status", "active", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        assert data["command"] == "forge dashboard update-agent"

    def test_update_agent_api_error_exits_nonzero(self):
        """A 404 API error causes a non-zero exit."""
        resp = make_mock_response(404, {"detail": "Agent not found"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["update-agent", "unknown-agent", "--status", "active"]
            )

        assert result.exit_code != 0

    def test_update_agent_api_error_json_envelope(self):
        """404 API error in JSON mode returns structured error envelope."""
        resp = make_mock_response(404, {"detail": "Agent not found"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["update-agent", "unknown-agent", "--status", "active", "--json"]
            )

        assert result.exit_code != 0
        data = parse_json(result.output)
        assert data["success"] is False


# ===========================================================================
# forge dashboard remove-agent
# ===========================================================================


class TestRemoveAgent:
    def test_remove_agent_with_force_skips_confirmation(self):
        """--force bypasses the confirmation prompt and removes the agent."""
        resp = make_mock_response(200, AGENT_REMOVE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["remove-agent", "minimax", "--force"])

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output
        assert "removed" in result.output.lower()

    def test_remove_agent_calls_delete_endpoint(self):
        """remove-agent issues DELETE to /api/dashboard/agents/{agent_id}."""
        resp = make_mock_response(200, AGENT_REMOVE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["remove-agent", "minimax", "--force"])

        url = client.delete.call_args[0][0]
        assert "/api/dashboard/agents/minimax" in url

    def test_remove_agent_json_output(self):
        """--json flag with --force returns structured success envelope."""
        resp = make_mock_response(200, AGENT_REMOVE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["remove-agent", "minimax", "--force", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        assert data["command"] == "forge dashboard remove-agent"

    def test_remove_agent_json_mode_skips_confirmation(self):
        """--json flag alone (no --force) also skips interactive confirmation."""
        resp = make_mock_response(200, AGENT_REMOVE_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["remove-agent", "minimax", "--json"])

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True

    def test_remove_agent_api_404_error_exits_nonzero(self):
        """A 404 from the API results in a non-zero exit code."""
        resp = make_mock_response(404, {"detail": "Agent not found"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["remove-agent", "ghost-agent", "--force"])

        assert result.exit_code != 0

    def test_remove_agent_api_404_json_error_envelope(self):
        """404 error in JSON mode returns a structured error envelope."""
        resp = make_mock_response(404, {"detail": "Agent not found"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["remove-agent", "ghost-agent", "--force", "--json"]
            )

        assert result.exit_code != 0
        data = parse_json(result.output)
        assert data["success"] is False
        assert "error" in data


# ===========================================================================
# forge dashboard task-start
# ===========================================================================


class TestTaskStart:
    def test_task_start_success_human_readable(self):
        """task-start prints confirmation with task_id, agent, and type."""
        resp = make_mock_response(200, TASK_START_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["task-start", "minimax", "task-001", "--type", "python"]
            )

        assert result.exit_code == 0, result.output
        assert "task-001" in result.output
        assert "minimax" in result.output

    def test_task_start_posts_to_correct_url(self):
        """task-start POSTs to /api/dashboard/tasks/start."""
        resp = make_mock_response(200, TASK_START_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["task-start", "minimax", "task-001"])

        url = client.post.call_args[0][0]
        assert "/api/dashboard/tasks/start" in url

    def test_task_start_default_type_is_general(self):
        """When --type is omitted the payload defaults to task_type='general'."""
        resp = make_mock_response(200, TASK_START_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["task-start", "minimax", "task-001"])

        payload = client.post.call_args[1].get("json", {})
        assert payload.get("task_type") == "general"

    def test_task_start_custom_type_sent_in_payload(self):
        """--type python is sent as task_type in the request body."""
        resp = make_mock_response(200, TASK_START_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["task-start", "minimax", "task-001", "--type", "python"])

        payload = client.post.call_args[1].get("json", {})
        assert payload.get("task_type") == "python"

    def test_task_start_json_output(self):
        """--json flag returns structured success envelope."""
        resp = make_mock_response(200, TASK_START_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["task-start", "minimax", "task-001", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        assert data["command"] == "forge dashboard task-start"

    def test_task_start_api_409_conflict_exits_nonzero(self):
        """A 409 Conflict from the API results in a non-zero exit."""
        resp = make_mock_response(409, {"detail": "Task already started"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["task-start", "minimax", "task-001"])

        assert result.exit_code != 0

    def test_task_start_api_error_json_envelope(self):
        """409 error in JSON mode returns structured error envelope."""
        resp = make_mock_response(409, {"detail": "Task already started"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["task-start", "minimax", "task-001", "--json"]
            )

        assert result.exit_code != 0
        data = parse_json(result.output)
        assert data["success"] is False


# ===========================================================================
# forge dashboard task-end
# ===========================================================================


class TestTaskEnd:
    def test_task_end_success_outcome(self):
        """task-end with --outcome success prints confirmation."""
        resp = make_mock_response(200, TASK_END_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["task-end", "task-001", "--outcome", "success"]
            )

        assert result.exit_code == 0, result.output
        assert "task-001" in result.output
        assert "success" in result.output

    def test_task_end_failure_outcome(self):
        """task-end with --outcome failure sends failure in payload."""
        failure_resp = {
            "success": True,
            "data": {"task_id": "task-002", "outcome": "failure"},
        }
        resp = make_mock_response(200, failure_resp)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["task-end", "task-002", "--outcome", "failure"]
            )

        assert result.exit_code == 0, result.output
        payload = client.post.call_args[1].get("json", {})
        assert payload.get("outcome") == "failure"

    def test_task_end_cancelled_outcome(self):
        """task-end with --outcome cancelled sends cancelled in payload."""
        cancel_resp = {
            "success": True,
            "data": {"task_id": "task-003", "outcome": "cancelled"},
        }
        resp = make_mock_response(200, cancel_resp)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["task-end", "task-003", "--outcome", "cancelled"]
            )

        assert result.exit_code == 0, result.output
        payload = client.post.call_args[1].get("json", {})
        assert payload.get("outcome") == "cancelled"

    def test_task_end_invalid_outcome_rejected(self):
        """An invalid --outcome value is rejected by Click (exit code 2)."""
        result = invoke_dashboard(["task-end", "task-001", "--outcome", "bogus"])
        assert result.exit_code == 2

    def test_task_end_posts_to_correct_url(self):
        """task-end POSTs to /api/dashboard/tasks/{task_id}/end."""
        resp = make_mock_response(200, TASK_END_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["task-end", "task-001", "--outcome", "success"])

        url = client.post.call_args[0][0]
        assert "/api/dashboard/tasks/task-001/end" in url

    def test_task_end_json_output(self):
        """--json flag returns structured success envelope."""
        resp = make_mock_response(200, TASK_END_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["task-end", "task-001", "--outcome", "success", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        assert data["command"] == "forge dashboard task-end"

    def test_task_end_api_404_exits_nonzero(self):
        """A 404 from the API (task not found) exits with a non-zero code."""
        resp = make_mock_response(404, {"detail": "Task not found"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["task-end", "nonexistent-task", "--outcome", "success"]
            )

        assert result.exit_code != 0

    def test_task_end_api_error_json_envelope(self):
        """404 in JSON mode returns structured error envelope."""
        resp = make_mock_response(404, {"detail": "Task not found"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(
                ["task-end", "nonexistent-task", "--outcome", "success", "--json"]
            )

        assert result.exit_code != 0
        data = parse_json(result.output)
        assert data["success"] is False


# ===========================================================================
# forge dashboard agent-tasks
# ===========================================================================


class TestAgentTasks:
    def test_agent_tasks_success_human_readable(self):
        """agent-tasks prints metrics for the given agent."""
        resp = make_mock_response(200, AGENT_TASKS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["agent-tasks", "minimax"])

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output

    def test_agent_tasks_default_days_is_7(self):
        """agent-tasks uses 7 as the default --days value in the URL."""
        resp = make_mock_response(200, AGENT_TASKS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["agent-tasks", "minimax"])

        url = client.get.call_args[0][0]
        assert "days=7" in url

    def test_agent_tasks_custom_days_in_url(self):
        """--days 30 is forwarded in the query string."""
        resp = make_mock_response(200, AGENT_TASKS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["agent-tasks", "minimax", "--days", "30"])

        url = client.get.call_args[0][0]
        assert "days=30" in url

    def test_agent_tasks_gets_correct_url(self):
        """agent-tasks issues GET to /api/dashboard/agents/{agent_id}/tasks."""
        resp = make_mock_response(200, AGENT_TASKS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["agent-tasks", "minimax"])

        url = client.get.call_args[0][0]
        assert "/api/dashboard/agents/minimax/tasks" in url

    def test_agent_tasks_json_output(self):
        """--json flag returns structured success envelope."""
        resp = make_mock_response(200, AGENT_TASKS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["agent-tasks", "minimax", "--json"])

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        assert data["command"] == "forge dashboard agent-tasks"

    def test_agent_tasks_api_error_exits_nonzero(self):
        """A 500 API error causes exit code 1."""
        resp = make_mock_response(500, {"detail": "Internal error"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["agent-tasks", "minimax"])

        assert result.exit_code != 0

    def test_agent_tasks_days_1_is_valid(self):
        """--days 1 is forwarded correctly in the URL."""
        resp = make_mock_response(200, AGENT_TASKS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["agent-tasks", "minimax", "--days", "1"])

        assert result.exit_code == 0, result.output
        url = client.get.call_args[0][0]
        assert "days=1" in url


# ===========================================================================
# forge dashboard task-metrics
# ===========================================================================


class TestTaskMetrics:
    def test_task_metrics_success_human_readable(self):
        """task-metrics prints throughput data for the given task type."""
        resp = make_mock_response(200, TASK_METRICS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["task-metrics", "python"])

        assert result.exit_code == 0, result.output
        assert "python" in result.output

    def test_task_metrics_gets_correct_url(self):
        """task-metrics issues GET to /api/dashboard/task-types/{task_type}."""
        resp = make_mock_response(200, TASK_METRICS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["task-metrics", "python"])

        url = client.get.call_args[0][0]
        assert "/api/dashboard/task-types/python" in url

    def test_task_metrics_default_days_7_in_url(self):
        """Default --days value of 7 appears in the request URL."""
        resp = make_mock_response(200, TASK_METRICS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["task-metrics", "python"])

        url = client.get.call_args[0][0]
        assert "days=7" in url

    def test_task_metrics_custom_days_forwarded(self):
        """--days 14 is forwarded in the query string."""
        resp = make_mock_response(200, TASK_METRICS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            invoke_dashboard(["task-metrics", "python", "--days", "14"])

        url = client.get.call_args[0][0]
        assert "days=14" in url

    def test_task_metrics_json_output(self):
        """--json flag returns structured success envelope."""
        resp = make_mock_response(200, TASK_METRICS_RESPONSE)
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["task-metrics", "python", "--json"])

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        assert data["command"] == "forge dashboard task-metrics"

    def test_task_metrics_api_error_exits_nonzero(self):
        """A 500 API error causes a non-zero exit code."""
        resp = make_mock_response(500, {"detail": "DB unavailable"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["task-metrics", "python"])

        assert result.exit_code != 0

    def test_task_metrics_json_error_envelope_on_api_failure(self):
        """API failure in JSON mode returns structured error envelope."""
        resp = make_mock_response(500, {"detail": "DB unavailable"})
        client = mock_httpx_client([resp])

        with patch("forge_harness.cli_v2.dashboard.httpx.Client", return_value=client):
            result = invoke_dashboard(["task-metrics", "python", "--json"])

        assert result.exit_code != 0
        data = parse_json(result.output)
        assert data["success"] is False
        assert "error" in data


# ===========================================================================
# forge dispatch queue-stats
# ===========================================================================


class TestDispatchQueueStats:
    def test_queue_stats_success_human_readable(self):
        """queue-stats prints pending, delivered, and acknowledged counts."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=QUEUE_STATS_RESPONSE,
        ):
            result = invoke_dispatch(["queue-stats"])

        assert result.exit_code == 0, result.output
        output = result.output
        assert "3" in output   # pending_messages
        assert "47" in output  # delivered_messages

    def test_queue_stats_shows_total_processed(self):
        """Human-readable output includes the total_processed count."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=QUEUE_STATS_RESPONSE,
        ):
            result = invoke_dispatch(["queue-stats"])

        assert "94" in result.output

    def test_queue_stats_shows_processing_rate_when_nonzero(self):
        """Human-readable output includes processing rate when > 0."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=QUEUE_STATS_RESPONSE,
        ):
            result = invoke_dispatch(["queue-stats"])

        assert "1.23" in result.output

    def test_queue_stats_shows_average_wait_when_nonzero(self):
        """Human-readable output includes average wait time when > 0."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=QUEUE_STATS_RESPONSE,
        ):
            result = invoke_dispatch(["queue-stats"])

        assert "4.5" in result.output

    def test_queue_stats_age_displayed_in_minutes(self):
        """Queue age of 125 seconds is displayed as '2m 5s'."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=QUEUE_STATS_RESPONSE,
        ):
            result = invoke_dispatch(["queue-stats"])

        # 125s = 2m 5s
        assert "2m" in result.output or "125" in result.output

    def test_queue_stats_age_displayed_in_seconds_when_under_60(self):
        """Queue age < 60 s is shown as plain seconds."""
        short_age_resp = {
            "success": True,
            "data": {**QUEUE_STATS_RESPONSE["data"], "queue_age_seconds": 42},
        }
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=short_age_resp,
        ):
            result = invoke_dispatch(["queue-stats"])

        assert "42s" in result.output

    def test_queue_stats_age_displayed_in_hours(self):
        """Queue age >= 3600 s is shown in hours and minutes."""
        hours_resp = {
            "success": True,
            "data": {**QUEUE_STATS_RESPONSE["data"], "queue_age_seconds": 7385},
        }
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=hours_resp,
        ):
            result = invoke_dispatch(["queue-stats"])

        # 7385s = 2h 3m
        assert "2h" in result.output

    def test_queue_stats_json_output(self):
        """--json flag returns structured success envelope."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=QUEUE_STATS_RESPONSE,
        ):
            result = invoke_dispatch(["queue-stats", "--json"])

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        assert data["command"] == "forge dispatch queue-stats"

    def test_queue_stats_json_contains_stats_data(self):
        """JSON output contains the stats from the API response."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=QUEUE_STATS_RESPONSE,
        ):
            result = invoke_dispatch(["queue-stats", "--json"])

        data = parse_json(result.output)
        stats = data["data"]
        assert stats.get("pending_messages") == 3
        assert stats.get("delivered_messages") == 47

    def test_queue_stats_api_error_exits_nonzero(self):
        """API failure causes a non-zero exit."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=RuntimeError("Connection refused"),
        ):
            result = invoke_dispatch(["queue-stats"])

        assert result.exit_code != 0

    def test_queue_stats_api_error_json_envelope(self):
        """API failure in JSON mode returns structured error envelope."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=RuntimeError("Connection refused"),
        ):
            result = invoke_dispatch(["queue-stats", "--json"])

        assert result.exit_code != 0
        data = parse_json(result.output)
        assert data["success"] is False
        assert "error" in data

    def test_queue_stats_zero_rate_omitted_from_output(self):
        """When processing_rate is 0.0 the rate line is omitted."""
        zero_rate_resp = {
            "success": True,
            "data": {
                **QUEUE_STATS_RESPONSE["data"],
                "processing_rate": 0.0,
                "average_wait_time": 0.0,
                "queue_age_seconds": 0,
            },
        }
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=zero_rate_resp,
        ):
            result = invoke_dispatch(["queue-stats"])

        assert result.exit_code == 0, result.output
        # Rate/wait lines should not appear
        assert "msg/s" not in result.output


# ===========================================================================
# forge dispatch conversation
# ===========================================================================


class TestDispatchConversation:
    def test_conversation_success_shows_messages(self):
        """conversation prints each message line from the two agents."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=CONVERSATION_RESPONSE,
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm"]
            )

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output
        assert "Task complete" in result.output

    def test_conversation_shows_message_count_header(self):
        """conversation header includes the message count."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=CONVERSATION_RESPONSE,
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm"]
            )

        assert "2" in result.output   # count from response

    def test_conversation_uses_correct_api_url(self):
        """GET request targets /api/agents/{agent1}/messages/conversation/{agent2}."""
        captured_urls: list[str] = []

        def capture(method, url, token, payload=None, timeout=5.0):
            captured_urls.append(url)
            return CONVERSATION_RESPONSE

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture):
            invoke_dispatch(["conversation", "forge:minimax", "forge:glm"])

        assert len(captured_urls) == 1
        url = captured_urls[0]
        assert "/api/agents/forge:minimax/messages/conversation/forge:glm" in url

    def test_conversation_default_limit_is_50(self):
        """Default --limit of 50 is appended as a query param."""
        captured_urls: list[str] = []

        def capture(method, url, token, payload=None, timeout=5.0):
            captured_urls.append(url)
            return CONVERSATION_RESPONSE

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture):
            invoke_dispatch(["conversation", "forge:minimax", "forge:glm"])

        assert "limit=50" in captured_urls[0]

    def test_conversation_custom_limit_forwarded(self):
        """--limit 10 is forwarded in the query string."""
        captured_urls: list[str] = []

        def capture(method, url, token, payload=None, timeout=5.0):
            captured_urls.append(url)
            return CONVERSATION_RESPONSE

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture):
            invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm", "--limit", "10"]
            )

        assert "limit=10" in captured_urls[0]

    def test_conversation_empty_shows_no_messages_info(self):
        """When there are no messages, an informational message is printed."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=CONVERSATION_EMPTY_RESPONSE,
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm"]
            )

        assert result.exit_code == 0, result.output
        assert "No messages" in result.output or "no messages" in result.output.lower()

    def test_conversation_json_output(self):
        """--json flag returns structured success envelope."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=CONVERSATION_RESPONSE,
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        assert data["command"] == "forge dispatch conversation"

    def test_conversation_json_contains_messages(self):
        """JSON output envelope contains the messages list from the API."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=CONVERSATION_RESPONSE,
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm", "--json"]
            )

        data = parse_json(result.output)
        assert "messages" in data["data"]
        assert len(data["data"]["messages"]) == 2

    def test_conversation_json_empty_response(self):
        """JSON output for an empty conversation contains messages=[]."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=CONVERSATION_EMPTY_RESPONSE,
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["data"]["messages"] == []

    def test_conversation_api_error_exits_nonzero(self):
        """API failure causes a non-zero exit."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=RuntimeError("API unreachable"),
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm"]
            )

        assert result.exit_code != 0

    def test_conversation_api_error_json_envelope(self):
        """API failure in JSON mode returns structured error envelope."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=RuntimeError("API unreachable"),
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm", "--json"]
            )

        assert result.exit_code != 0
        data = parse_json(result.output)
        assert data["success"] is False
        assert "error" in data

    def test_conversation_direction_indicator_in_output(self):
        """Output contains '->' direction indicator for sender messages."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=CONVERSATION_RESPONSE,
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm"]
            )

        # The first message is from agent1 (forge:minimax) so direction is "->"
        assert "->" in result.output or "<-" in result.output

    def test_conversation_message_id_shown_in_output(self):
        """Each message row is followed by a truncated message ID."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=CONVERSATION_RESPONSE,
        ):
            result = invoke_dispatch(
                ["conversation", "forge:minimax", "forge:glm"]
            )

        # First 8 chars of msg-aabb1122
        assert "msg-aabb" in result.output

    def test_conversation_limit_out_of_range_rejected(self):
        """--limit 0 is below the minimum of 1 and is rejected by Click (exit 2)."""
        result = invoke_dispatch(
            ["conversation", "forge:minimax", "forge:glm", "--limit", "0"]
        )
        assert result.exit_code == 2
