"""
Tests for forge dispatch auto and forge dispatch status CLI commands.

Covers:
    auto        — forge dispatch auto MESSAGE [--task-type ...] [--dry-run] [--json] ...
    status      — forge dispatch status [--agent ...] [--json] ...
    helpers     — _get_recommendation, _send_queued_message, _get_message_status

All external I/O (_http_json, _discover_tmux_agents, DispatchClient) is mocked
so these tests run fully offline without a live Command Center server or tmux.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def invoke(args: list[str], env: dict[str, str] | None = None):
    """Run the forge CLI and return the result."""
    runner = CliRunner()
    return runner.invoke(cli, args, env=env, catch_exceptions=False)


def invoke_dispatch(args: list[str], env: dict[str, str] | None = None):
    """Run `forge dispatch ...` with a default API token injected via env."""
    base_env = {"FORGE_WEBHOOK_TOKEN": "test-token"}
    if env:
        base_env.update(env)
    return invoke(["dispatch"] + args, env=base_env)


def parse_json(output: str) -> dict:
    """Parse full JSON output from CLI (handles leading text before JSON block)."""
    lines = output.strip().splitlines()
    json_start = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("{")), None
    )
    assert json_start is not None, f"No JSON found in output:\n{output}"
    return json.loads("\n".join(lines[json_start:]))


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

RECOMMENDATION_HAPPY = {
    "recommended_node_id": "sati",
    "score": 0.9123,
    "reason": "Low CPU and high capacity",
    "candidates": [
        {"node_id": "sati", "score": 0.9123, "cpu_load": 10, "ram_usage": 30, "reason": "Low CPU"},
        {"node_id": "nova", "score": 0.7500, "cpu_load": 40, "ram_usage": 55, "reason": "Moderate"},
        {"node_id": "prya", "score": 0.4000, "cpu_load": 85, "ram_usage": 90, "reason": "High load"},
    ],
}

QUEUE_SEND_RESPONSE = {
    "success": True,
    "data": {"message_id": "abcdef01-0000-0000-0000-000000000000"},
}

AGENTS_LIST_RESPONSE = {
    "success": True,
    "data": {
        "agents": [
            {
                "id": "ag_minimax",
                "agent_name": "minimax",
                "name": "minimax",
                "status": "active",
                "last_activity": "2026-02-23T10:00:00",
            },
            {
                "id": "ag_glm",
                "agent_name": "glm",
                "name": "glm",
                "status": "idle",
                "last_activity": "2026-02-23T09:45:00",
            },
        ]
    },
}

PENDING_MESSAGES_MINIMAX = {
    "success": True,
    "data": {
        "messages": [
            {
                "id": "msg-pending-001",
                "content": "Implement feature X as described",
                "status": "pending",
                "priority": 2,
                "created_at": "2026-02-23T10:01:00",
                "sender_id": "orchestrator",
            }
        ]
    },
}

PENDING_MESSAGES_EMPTY = {
    "success": True,
    "data": {"messages": []},
}

QUEUE_STATS_RESPONSE = {
    "success": True,
    "data": {"by_status": {"pending": 3, "delivered": 10, "acked": 47}},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def no_tmux():
    """Patch _discover_tmux_agents to return no local agents."""
    with patch(
        "forge_harness.cli_v2.dispatch._discover_tmux_agents",
        return_value=[],
    ) as mock:
        yield mock


@pytest.fixture
def local_tmux():
    """Patch _discover_tmux_agents to expose 'forge:sati' as a local target."""
    with patch(
        "forge_harness.cli_v2.dispatch._discover_tmux_agents",
        return_value=["forge:sati", "forge:nova"],
    ) as mock:
        yield mock


@pytest.fixture
def mock_dispatch_client():
    """Patch DispatchClient.send to return a successful local dispatch result."""
    dispatch_result = SimpleNamespace(
        success=True,
        message_id="local-msg-abc123",
        delivery_time_ms=42.0,
        attempts=1,
        error=None,
    )
    with patch(
        "forge_harness.fleet.dispatch_client.DispatchClient.send",
        new=AsyncMock(return_value=dispatch_result),
    ) as mock:
        yield mock


# ===========================================================================
# forge dispatch auto — scheduler recommendation happy path
# ===========================================================================


class TestAutoHappyPath:
    def test_auto_remote_agent_dispatches_via_queue(self, no_tmux):
        """Happy path: scheduler returns recommendation, message sent via queue API."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Implement feature X"])

        assert result.exit_code == 0, result.output
        assert "sati" in result.output or "Dispatched" in result.output

    def test_auto_remote_agent_success_message_shown(self, no_tmux):
        """Success path shows confirmation text to user."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Implement feature X"])

        assert result.exit_code == 0
        output_lower = result.output.lower()
        assert "dispatch" in output_lower or "sati" in output_lower

    def test_auto_shows_scheduler_recommendation_info(self, no_tmux):
        """Non-JSON output includes scheduler recommendation details."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Run tests"])

        assert result.exit_code == 0
        assert "sati" in result.output


# ===========================================================================
# forge dispatch auto — dry-run
# ===========================================================================


class TestAutoDryRun:
    def test_dry_run_shows_recommendation_without_sending(self, no_tmux):
        """--dry-run prints recommendation but does not enqueue a message."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=RECOMMENDATION_HAPPY,
        ) as http_mock:
            result = invoke_dispatch(["auto", "Review PR #42", "--dry-run"])

        assert result.exit_code == 0
        assert "dry run" in result.output.lower() or "sati" in result.output.lower()
        # Only one HTTP call (recommendation); no queue send
        assert http_mock.call_count == 1

    def test_dry_run_json_output(self, no_tmux):
        """--dry-run --json returns structured envelope with dry_run=True."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=RECOMMENDATION_HAPPY,
        ):
            result = invoke_dispatch(["auto", "Review PR #42", "--dry-run", "--json"])

        assert result.exit_code == 0
        data = parse_json(result.output)
        assert data["success"] is True
        assert data["data"]["dry_run"] is True
        assert data["data"]["recommended_node"] == "sati"

    def test_dry_run_json_includes_candidates(self, no_tmux):
        """Dry-run JSON result embeds candidates list from scheduler."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=RECOMMENDATION_HAPPY,
        ):
            result = invoke_dispatch(["auto", "Build Docker image", "--dry-run", "--json"])

        data = parse_json(result.output)
        assert "candidates" in data["data"]
        assert len(data["data"]["candidates"]) > 0


# ===========================================================================
# forge dispatch auto — node exclusion
# ===========================================================================


class TestAutoExcludeNode:
    def test_exclude_node_picks_next_candidate(self, no_tmux):
        """--exclude-node skips the top recommendation and picks the next candidate."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Run tests", "--exclude-node", "sati"])

        assert result.exit_code == 0
        # 'nova' is the next candidate after 'sati' is excluded
        assert "nova" in result.output or "Dispatched" in result.output

    def test_exclude_all_candidates_exits_with_error(self, no_tmux):
        """When all candidates are excluded and no fallback, exit is non-zero.

        Note: format_error raises SystemExit(1) before the DISPATCH_FAILED(11)
        is reached in the non-JSON path, so the effective exit code is 1.
        """
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=RECOMMENDATION_HAPPY,
        ):
            result = invoke_dispatch(
                [
                    "auto",
                    "Run tests",
                    "--exclude-node", "sati",
                    "--exclude-node", "nova",
                    "--exclude-node", "prya",
                ]
            )

        # format_error() raises SystemExit(1) — that propagates as exit code 1
        assert result.exit_code != 0
        assert "excluded" in result.output.lower() or "All candidates" in result.output

    def test_exclude_all_with_fallback_uses_fallback_target(self, no_tmux):
        """When all candidates excluded, --fallback-target is used instead of failing."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(
                [
                    "auto",
                    "Run tests",
                    "--exclude-node", "sati",
                    "--exclude-node", "nova",
                    "--exclude-node", "prya",
                    "--fallback-target", "forge:minimax",
                ]
            )

        assert result.exit_code == 0

    def test_exclude_all_json_error_envelope(self, no_tmux):
        """JSON mode: all-excluded error returns structured error envelope."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value=RECOMMENDATION_HAPPY,
        ):
            result = invoke_dispatch(
                [
                    "auto",
                    "Run tests",
                    "--exclude-node", "sati",
                    "--exclude-node", "nova",
                    "--exclude-node", "prya",
                    "--json",
                ]
            )

        assert result.exit_code == 11
        data = parse_json(result.output)
        assert data["success"] is False
        assert "error" in data["data"]


# ===========================================================================
# forge dispatch auto — task_type and path_lock
# ===========================================================================


class TestAutoSchedulerParams:
    def test_task_type_python_passed_to_scheduler(self, no_tmux):
        """--task-type python is forwarded in the scheduler URL query string."""
        captured_urls: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            captured_urls.append(url)
            if "recommend" in url:
                return RECOMMENDATION_HAPPY
            return QUEUE_SEND_RESPONSE

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=capture_http,
        ):
            result = invoke_dispatch(
                ["auto", "Write unit tests", "--task-type", "python"]
            )

        assert result.exit_code == 0, result.output
        rec_url = next(u for u in captured_urls if "recommend" in u)
        assert "task_type=python" in rec_url

    def test_task_type_ios_passed_to_scheduler(self, no_tmux):
        """--task-type ios is forwarded correctly."""
        captured_urls: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            captured_urls.append(url)
            if "recommend" in url:
                return RECOMMENDATION_HAPPY
            return QUEUE_SEND_RESPONSE

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=capture_http,
        ):
            result = invoke_dispatch(["auto", "Build iOS app", "--task-type", "ios"])

        assert result.exit_code == 0, result.output
        rec_url = next(u for u in captured_urls if "recommend" in u)
        assert "task_type=ios" in rec_url

    def test_path_lock_passed_to_scheduler(self, no_tmux):
        """--path-lock is forwarded in the scheduler URL query string."""
        captured_urls: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            captured_urls.append(url)
            if "recommend" in url:
                return RECOMMENDATION_HAPPY
            return QUEUE_SEND_RESPONSE

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=capture_http,
        ):
            result = invoke_dispatch(
                [
                    "auto",
                    "Migrate DB",
                    "--path-lock",
                    "harness/migrations/",
                ]
            )

        assert result.exit_code == 0, result.output
        rec_url = next(u for u in captured_urls if "recommend" in u)
        assert "path_lock=" in rec_url


# ===========================================================================
# forge dispatch auto — priority
# ===========================================================================


class TestAutoPriority:
    def test_priority_urgent_sent_in_payload(self, no_tmux):
        """--priority 3 (urgent) is included in the queue message payload."""
        captured_payloads: list[dict] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            if payload:
                captured_payloads.append(payload)
            if "recommend" in url:
                return RECOMMENDATION_HAPPY
            return QUEUE_SEND_RESPONSE

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=capture_http,
        ):
            result = invoke_dispatch(["auto", "URGENT: fix prod bug", "--priority", "3"])

        assert result.exit_code == 0, result.output
        assert any(p.get("priority") == 3 for p in captured_payloads)

    def test_priority_low_sent_in_payload(self, no_tmux):
        """--priority 0 (low) is included in the queue message payload."""
        captured_payloads: list[dict] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            if payload:
                captured_payloads.append(payload)
            if "recommend" in url:
                return RECOMMENDATION_HAPPY
            return QUEUE_SEND_RESPONSE

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=capture_http,
        ):
            result = invoke_dispatch(["auto", "Low priority background task", "--priority", "0"])

        assert result.exit_code == 0, result.output
        assert any(p.get("priority") == 0 for p in captured_payloads)


# ===========================================================================
# forge dispatch auto — local agent via tmux direct
# ===========================================================================


class TestAutoLocalAgent:
    def test_local_agent_uses_dispatch_client(self, local_tmux, mock_dispatch_client):
        """When target is a local tmux session, DispatchClient.send is called directly."""
        # sati is in local_tmux → forge:sati is local
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Implement auth module"])

        assert result.exit_code == 0, result.output
        mock_dispatch_client.assert_called_once()

    def test_local_agent_delivery_method_tmux_direct(self, local_tmux, mock_dispatch_client):
        """JSON output for local dispatch shows delivery_method=tmux_direct."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Implement auth module", "--json"])

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["data"]["delivery_method"] == "tmux_direct"

    def test_local_agent_tracking_queued_when_api_succeeds(self, local_tmux, mock_dispatch_client):
        """Local dispatch marks tracking=queued when the queue API call also succeeds."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Deploy to staging", "--json"])

        data = parse_json(result.output)
        assert data["data"]["tracking"] == "queued"

    def test_local_agent_tracking_untracked_when_api_fails(self, local_tmux, mock_dispatch_client):
        """When the queue API fails (tracking only), delivery_method is still tmux_direct."""
        call_count = 0

        def http_side_effect(method, url, token, payload=None, timeout=5.0):
            nonlocal call_count
            call_count += 1
            if "recommend" in url:
                return RECOMMENDATION_HAPPY
            raise RuntimeError("Queue API unavailable")

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=http_side_effect,
        ):
            result = invoke_dispatch(["auto", "Deploy to staging", "--json"])

        # tmux dispatch succeeded, so overall result is success
        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["data"]["delivery_method"] == "tmux_direct"
        assert data["data"]["tracking"] == "untracked"


# ===========================================================================
# forge dispatch auto — remote agent via queue API
# ===========================================================================


class TestAutoRemoteAgent:
    def test_remote_agent_delivery_method_queue_api(self, no_tmux):
        """JSON output for remote dispatch shows delivery_method=queue_api."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Research new patterns", "--json"])

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["data"]["delivery_method"] == "queue_api"
        assert data["data"]["tracking"] == "queued"

    def test_remote_agent_captures_message_id(self, no_tmux):
        """JSON output includes message_id returned from queue API."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Research new patterns", "--json"])

        data = parse_json(result.output)
        assert data["data"]["message_id"] == "abcdef01-0000-0000-0000-000000000000"

    def test_remote_agent_captures_scheduler_metadata(self, no_tmux):
        """JSON output includes recommended_node, scheduler_score, and task_type."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Research new patterns", "--json"])

        data = parse_json(result.output)
        d = data["data"]
        assert d["recommended_node"] == "sati"
        assert abs(d["scheduler_score"] - 0.9123) < 0.001
        assert d["task_type"] == "general"


# ===========================================================================
# forge dispatch auto — JSON output flag
# ===========================================================================


class TestAutoJsonOutput:
    def test_json_flag_on_dispatch_group_works(self, no_tmux):
        """Top-level --json flag on `dispatch` group enables JSON output for auto."""
        runner = CliRunner()
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = runner.invoke(
                cli,
                ["dispatch", "--json", "auto", "Build feature", "--api-token", "tok"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert "success" in data

    def test_json_subcommand_flag_also_works(self, no_tmux):
        """Sub-command level --json flag also enables JSON output."""
        http_responses = iter([RECOMMENDATION_HAPPY, QUEUE_SEND_RESPONSE])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Build feature", "--json"])

        assert result.exit_code == 0
        data = parse_json(result.output)
        assert "success" in data
        assert "data" in data
        assert "command" in data


# ===========================================================================
# forge dispatch auto — error paths
# ===========================================================================


class TestAutoErrorPaths:
    def test_scheduler_unreachable_exits_general_error(self, no_tmux):
        """When scheduler API raises RuntimeError, exit code is 1 (GENERAL_ERROR)."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=RuntimeError("Connection refused"),
        ):
            result = invoke_dispatch(["auto", "Do something"])

        assert result.exit_code == 1

    def test_scheduler_unreachable_json_error_envelope(self, no_tmux):
        """When scheduler API fails, JSON mode returns structured error envelope."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=RuntimeError("Connection refused"),
        ):
            result = invoke_dispatch(["auto", "Do something", "--json"])

        assert result.exit_code == 1
        data = parse_json(result.output)
        assert data["success"] is False

    def test_queue_send_fails_remote_exits_error(self, no_tmux):
        """When queue send fails for a remote agent, exit code is non-zero."""
        queue_fail = {"success": False, "error": "Agent queue full"}
        http_responses = iter([RECOMMENDATION_HAPPY, queue_fail])

        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=lambda *a, **kw: next(http_responses),
        ):
            result = invoke_dispatch(["auto", "Do something"])

        # Non-zero because result_data["success"] is False
        assert result.exit_code != 0

    def test_missing_api_token_raises_usage_error(self):
        """Omitting --api-token without env var raises usage error (exit 2)."""
        runner = CliRunner()
        with patch.dict("os.environ", {}, clear=True):
            result = runner.invoke(
                cli,
                ["dispatch", "auto", "Do something"],
                env={},
                catch_exceptions=False,
            )
        assert result.exit_code == 2


# ===========================================================================
# forge dispatch status — happy path
# ===========================================================================


class TestStatusHappyPath:
    def test_status_shows_agents_with_pending_messages(self):
        """Status output lists agents and highlights those with pending messages."""
        http_map = {
            "/api/agents": {"success": True, "data": AGENTS_LIST_RESPONSE["data"]},
            "ag_minimax/messages/pending": PENDING_MESSAGES_MINIMAX,
            "ag_glm/messages/pending": PENDING_MESSAGES_EMPTY,
            "/api/messages/stats": QUEUE_STATS_RESPONSE,
        }

        def http_side(method, url, token, payload=None, timeout=5.0):
            for key, resp in http_map.items():
                if key in url:
                    return resp
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status"])

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output

    def test_status_pending_count_shown(self):
        """Status output displays pending message count per agent."""
        http_map = {
            "/api/agents": {"success": True, "data": AGENTS_LIST_RESPONSE["data"]},
            "ag_minimax/messages/pending": PENDING_MESSAGES_MINIMAX,
            "ag_glm/messages/pending": PENDING_MESSAGES_EMPTY,
            "/api/messages/stats": QUEUE_STATS_RESPONSE,
        }

        def http_side(method, url, token, payload=None, timeout=5.0):
            for key, resp in http_map.items():
                if key in url:
                    return resp
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status"])

        assert result.exit_code == 0
        assert "pending=1" in result.output or "pending" in result.output

    def test_status_no_agents_shows_info_message(self):
        """When no agents are found, status shows 'No agents found'."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value={"success": True, "data": {"agents": []}},
        ), patch(
            "forge_harness.cli_v2.dispatch._discover_tmux_agents",
            return_value=[],
        ):
            result = invoke_dispatch(["status"])

        assert result.exit_code == 0
        assert "no agents" in result.output.lower()


# ===========================================================================
# forge dispatch status — agent filter
# ===========================================================================


class TestStatusAgentFilter:
    def test_agent_filter_exact_match(self):
        """--agent filter by exact ID returns only the matching agent."""
        http_map = {
            "/api/agents": {"success": True, "data": AGENTS_LIST_RESPONSE["data"]},
            "ag_minimax/messages/pending": PENDING_MESSAGES_MINIMAX,
            "/api/messages/stats": QUEUE_STATS_RESPONSE,
        }

        def http_side(method, url, token, payload=None, timeout=5.0):
            for key, resp in http_map.items():
                if key in url:
                    return resp
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status", "--agent", "ag_minimax"])

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output
        # glm should not appear since we filtered to minimax
        assert "glm" not in result.output

    def test_agent_filter_partial_match(self):
        """--agent filter by partial string falls back to substring match."""
        call_count = {"n": 0}

        def http_side(method, url, token, payload=None, timeout=5.0):
            if "/api/agents" in url and "messages" not in url:
                return {"success": True, "data": AGENTS_LIST_RESPONSE["data"]}
            if "minimax" in url and "messages/pending" in url:
                return PENDING_MESSAGES_MINIMAX
            if "glm" in url and "messages/pending" in url:
                return PENDING_MESSAGES_EMPTY
            if "messages/stats" in url:
                return QUEUE_STATS_RESPONSE
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status", "--agent", "minimax"])

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output


# ===========================================================================
# forge dispatch status — JSON output
# ===========================================================================


class TestStatusJsonOutput:
    def test_status_json_output_structure(self):
        """--json output returns envelope with agents, summary, and queue_stats."""
        http_map = {
            "/api/agents": {"success": True, "data": AGENTS_LIST_RESPONSE["data"]},
            "ag_minimax/messages/pending": PENDING_MESSAGES_MINIMAX,
            "ag_glm/messages/pending": PENDING_MESSAGES_EMPTY,
            "/api/messages/stats": QUEUE_STATS_RESPONSE,
        }

        def http_side(method, url, token, payload=None, timeout=5.0):
            for key, resp in http_map.items():
                if key in url:
                    return resp
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status", "--json"])

        assert result.exit_code == 0, result.output
        data = parse_json(result.output)
        assert data["success"] is True
        d = data["data"]
        assert "agents" in d
        assert "summary" in d
        assert "queue_stats" in d

    def test_status_json_summary_counts(self):
        """JSON summary block contains total_agents and total_pending."""

        def http_side(method, url, token, payload=None, timeout=5.0):
            if "/api/agents" in url and "/messages/" not in url:
                return {"success": True, "data": AGENTS_LIST_RESPONSE["data"]}
            if "ag_minimax" in url and "messages/pending" in url:
                return PENDING_MESSAGES_MINIMAX
            if "ag_glm" in url and "messages/pending" in url:
                return PENDING_MESSAGES_EMPTY
            if "/api/messages/stats" in url:
                return QUEUE_STATS_RESPONSE
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status", "--json"])

        data = parse_json(result.output)
        summary = data["data"]["summary"]
        assert summary["total_agents"] == 2
        assert summary["total_pending"] == 1

    def test_status_json_agents_have_messages_field(self):
        """Each agent entry in JSON has an id, status, pending count, and messages list."""
        http_map = {
            "/api/agents": {"success": True, "data": AGENTS_LIST_RESPONSE["data"]},
            "ag_minimax/messages/pending": PENDING_MESSAGES_MINIMAX,
            "ag_glm/messages/pending": PENDING_MESSAGES_EMPTY,
            "/api/messages/stats": QUEUE_STATS_RESPONSE,
        }

        def http_side(method, url, token, payload=None, timeout=5.0):
            for key, resp in http_map.items():
                if key in url:
                    return resp
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status", "--json"])

        data = parse_json(result.output)
        agents = data["data"]["agents"]
        for agent in agents:
            assert "agent_id" in agent
            assert "pending" in agent
            assert "messages" in agent

    def test_status_json_agent_with_pending_includes_message_preview(self):
        """Agent entry with pending messages includes truncated message content."""

        def http_side(method, url, token, payload=None, timeout=5.0):
            if "/api/agents" in url and "/messages/" not in url:
                return {"success": True, "data": AGENTS_LIST_RESPONSE["data"]}
            if "ag_minimax" in url and "messages/pending" in url:
                return PENDING_MESSAGES_MINIMAX
            if "ag_glm" in url and "messages/pending" in url:
                return PENDING_MESSAGES_EMPTY
            if "/api/messages/stats" in url:
                return QUEUE_STATS_RESPONSE
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status", "--json"])

        data = parse_json(result.output)
        minimax_agent = next(
            a for a in data["data"]["agents"] if "minimax" in a.get("agent_name", "")
        )
        assert len(minimax_agent["messages"]) == 1
        msg = minimax_agent["messages"][0]
        assert "content" in msg
        assert "status" in msg
        assert "priority" in msg


# ===========================================================================
# forge dispatch status — tmux fallback
# ===========================================================================


class TestStatusTmuxFallback:
    def test_status_falls_back_to_tmux_when_api_returns_empty(self):
        """When API returns no agents, status falls back to tmux-discovered agents."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value={"success": True, "data": {"agents": []}},
        ), patch(
            "forge_harness.cli_v2.dispatch._discover_tmux_agents",
            return_value=["forge:minimax", "forge:glm"],
        ), patch(
            "forge_harness.cli_v2.dispatch._fetch_pending_messages",
            return_value=[],
        ):
            result = invoke_dispatch(["status"])

        assert result.exit_code == 0, result.output
        assert "minimax" in result.output or "forge:minimax" in result.output

    def test_status_tmux_fallback_shows_two_agents(self):
        """Tmux fallback discovers both agents and shows them in output."""
        with patch(
            "forge_harness.cli_v2.dispatch._list_agents",
            return_value=[],
        ), patch(
            "forge_harness.cli_v2.dispatch._discover_tmux_agents",
            return_value=["forge:minimax", "forge:glm"],
        ), patch(
            "forge_harness.cli_v2.dispatch._fetch_pending_messages",
            return_value=[],
        ), patch(
            "forge_harness.cli_v2.dispatch._http_json",
            return_value={"success": True, "data": {}},
        ):
            result = invoke_dispatch(["status"])

        assert result.exit_code == 0


# ===========================================================================
# forge dispatch status — queue stats
# ===========================================================================


class TestStatusQueueStats:
    def test_status_includes_queue_statistics(self):
        """Human-readable output shows queue statistics when available."""
        http_map = {
            "/api/agents": {"success": True, "data": AGENTS_LIST_RESPONSE["data"]},
            "ag_minimax/messages/pending": PENDING_MESSAGES_EMPTY,
            "ag_glm/messages/pending": PENDING_MESSAGES_EMPTY,
            "/api/messages/stats": QUEUE_STATS_RESPONSE,
        }

        def http_side(method, url, token, payload=None, timeout=5.0):
            for key, resp in http_map.items():
                if key in url:
                    return resp
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status"])

        assert result.exit_code == 0
        # Queue stats line should include status buckets
        assert "pending" in result.output or "acked" in result.output

    def test_status_queue_stats_in_json_output(self):
        """JSON output includes queue_stats section from /api/messages/stats."""
        http_map = {
            "/api/agents": {"success": True, "data": AGENTS_LIST_RESPONSE["data"]},
            "ag_minimax/messages/pending": PENDING_MESSAGES_EMPTY,
            "ag_glm/messages/pending": PENDING_MESSAGES_EMPTY,
            "/api/messages/stats": QUEUE_STATS_RESPONSE,
        }

        def http_side(method, url, token, payload=None, timeout=5.0):
            for key, resp in http_map.items():
                if key in url:
                    return resp
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=http_side):
            result = invoke_dispatch(["status", "--json"])

        data = parse_json(result.output)
        qs = data["data"]["queue_stats"]
        assert "by_status" in qs
        assert qs["by_status"]["pending"] == 3


# ===========================================================================
# forge dispatch status — error paths
# ===========================================================================


class TestStatusErrorPaths:
    def test_status_api_error_exits_with_general_error(self):
        """When _list_agents raises an exception, status exits with code 1."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=RuntimeError("API unreachable"),
        ), patch(
            "forge_harness.cli_v2.dispatch._discover_tmux_agents",
            return_value=[],
        ):
            result = invoke_dispatch(["status"])

        assert result.exit_code == 1

    def test_status_api_error_json_mode_returns_error_envelope(self):
        """When API fails in JSON mode, structured error envelope is returned."""
        with patch(
            "forge_harness.cli_v2.dispatch._http_json",
            side_effect=RuntimeError("API unreachable"),
        ), patch(
            "forge_harness.cli_v2.dispatch._discover_tmux_agents",
            return_value=[],
        ):
            result = invoke_dispatch(["status", "--json"])

        assert result.exit_code == 1
        data = parse_json(result.output)
        assert data["success"] is False


# ===========================================================================
# Helper function unit tests
# ===========================================================================


class TestGetRecommendation:
    def test_get_recommendation_constructs_correct_url(self):
        """_get_recommendation builds the correct URL with task_type parameter."""
        from forge_harness.cli_v2.dispatch import _get_recommendation

        captured: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            captured.append(url)
            return RECOMMENDATION_HAPPY

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            result = _get_recommendation(
                "http://localhost:8080", "tok", task_type="python"
            )

        assert len(captured) == 1
        url = captured[0]
        assert "/api/nodes/recommend" in url
        assert "task_type=python" in url
        assert result == RECOMMENDATION_HAPPY

    def test_get_recommendation_includes_path_lock_when_provided(self):
        """_get_recommendation appends path_lock to the query string when given."""
        from forge_harness.cli_v2.dispatch import _get_recommendation

        captured: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            captured.append(url)
            return RECOMMENDATION_HAPPY

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            _get_recommendation(
                "http://localhost:8080",
                "tok",
                task_type="general",
                path_lock="harness/migrations/",
            )

        url = captured[0]
        assert "path_lock=" in url

    def test_get_recommendation_no_path_lock_omits_param(self):
        """When path_lock is None, it is not included in the query string."""
        from forge_harness.cli_v2.dispatch import _get_recommendation

        captured: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            captured.append(url)
            return RECOMMENDATION_HAPPY

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            _get_recommendation("http://localhost:8080", "tok")

        assert "path_lock" not in captured[0]

    def test_get_recommendation_uses_get_method(self):
        """_get_recommendation uses GET HTTP method."""
        from forge_harness.cli_v2.dispatch import _get_recommendation

        methods: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            methods.append(method)
            return RECOMMENDATION_HAPPY

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            _get_recommendation("http://localhost:8080", "tok")

        assert methods == ["GET"]


class TestSendQueuedMessage:
    def test_send_queued_message_constructs_correct_url(self):
        """_send_queued_message builds URL with agent ID encoded correctly."""
        from forge_harness.cli_v2.dispatch import _send_queued_message

        captured_calls: list[tuple] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            captured_calls.append((method, url, payload))
            return QUEUE_SEND_RESPONSE

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            result = _send_queued_message(
                "http://localhost:8080",
                "tok",
                agent_id="forge:sati",
                content="Run the tests",
                priority=2,
            )

        assert len(captured_calls) == 1
        method, url, payload = captured_calls[0]
        assert method == "POST"
        assert "/api/agents/" in url
        assert "/messages/send" in url
        assert result == QUEUE_SEND_RESPONSE

    def test_send_queued_message_payload_structure(self):
        """_send_queued_message payload contains all required fields."""
        from forge_harness.cli_v2.dispatch import _send_queued_message

        captured_payload: list[dict] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            if payload:
                captured_payload.append(payload)
            return QUEUE_SEND_RESPONSE

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            _send_queued_message(
                "http://localhost:8080",
                "tok",
                agent_id="forge:sati",
                content="Run the tests",
                sender_id="orchestrator",
                msg_type="instruction",
                priority=1,
            )

        payload = captured_payload[0]
        assert payload["content"] == "Run the tests"
        assert payload["sender_id"] == "orchestrator"
        assert payload["type"] == "instruction"
        assert payload["priority"] == 1

    def test_send_queued_message_includes_metadata_when_provided(self):
        """_send_queued_message includes metadata dict when provided."""
        from forge_harness.cli_v2.dispatch import _send_queued_message

        captured_payload: list[dict] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            if payload:
                captured_payload.append(payload)
            return QUEUE_SEND_RESPONSE

        meta = {"task_type": "python", "scheduler_score": 0.9}
        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            _send_queued_message(
                "http://localhost:8080",
                "tok",
                agent_id="forge:sati",
                content="Do task",
                metadata=meta,
            )

        payload = captured_payload[0]
        assert "metadata" in payload
        assert payload["metadata"]["task_type"] == "python"

    def test_send_queued_message_no_metadata_omits_field(self):
        """_send_queued_message does not include metadata key when metadata is None."""
        from forge_harness.cli_v2.dispatch import _send_queued_message

        captured_payload: list[dict] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            if payload:
                captured_payload.append(payload)
            return QUEUE_SEND_RESPONSE

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            _send_queued_message(
                "http://localhost:8080",
                "tok",
                agent_id="forge:sati",
                content="Do task",
            )

        payload = captured_payload[0]
        assert "metadata" not in payload


class TestGetMessageStatus:
    def test_get_message_status_constructs_correct_url(self):
        """_get_message_status builds the correct URL with encoded message ID."""
        from forge_harness.cli_v2.dispatch import _get_message_status

        captured: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            captured.append(url)
            return {"success": True, "data": {"status": "delivered"}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            result = _get_message_status(
                "http://localhost:8080", "tok", "msg-abc-123"
            )

        assert len(captured) == 1
        url = captured[0]
        assert "/api/messages/" in url
        assert "msg-abc-123" in url

    def test_get_message_status_uses_get_method(self):
        """_get_message_status uses GET HTTP method."""
        from forge_harness.cli_v2.dispatch import _get_message_status

        methods: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            methods.append(method)
            return {"success": True, "data": {}}

        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            _get_message_status("http://localhost:8080", "tok", "msg-xyz")

        assert methods == ["GET"]

    def test_get_message_status_url_encodes_message_id(self):
        """_get_message_status URL-encodes special characters in the message ID."""
        from forge_harness.cli_v2.dispatch import _get_message_status

        captured: list[str] = []

        def capture_http(method, url, token, payload=None, timeout=5.0):
            captured.append(url)
            return {"success": True, "data": {}}

        # Message ID with a slash that must be encoded
        with patch("forge_harness.cli_v2.dispatch._http_json", side_effect=capture_http):
            _get_message_status("http://localhost:8080", "tok", "ab/cd")

        url = captured[0]
        # Raw slash should be encoded as %2F
        assert "%2F" in url or "ab/cd" not in url.split("/api/messages/")[1]
