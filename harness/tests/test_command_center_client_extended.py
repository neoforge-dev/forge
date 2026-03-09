"""Extended tests for CommandCenterClient.

Focuses on coverage gaps not addressed by the existing test files:
- Decision operations (list_decisions, get_decision and sync wrappers)
- HTTP 401, 403, 500 error-code handling
- Specific argument forwarding to _request (query params, POST body)
- Edge-case response shapes (list vs dict wrappers, empty data)
- Failure branches for count/stats/broadcast helpers
- reorder_tasks when data is not a dict
- complete_agent without summary
- register_agent with optional name
- update_agent_progress with files_modified
- list_patterns with category filter and dict wrapper
- record_outcome with context
- get_recommended_tasks with all optional params and recommendation wrapper
- list_tasks with direct list response
- close() when session is already None
- AgentStatus enum value coverage
- Approval tier variants (watch, phone)
- SSE non-200 status and exception paths
- URL construction validation
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest

from forge_harness.command_center_client import (
    Agent,
    AgentStatus,
    APIResponse,
    Approval,
    ApprovalStatus,
    CommandCenterClient,
    DecisionTier,
    Pattern,
    SSEEvent,
    Task,
    _parse_datetime,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> CommandCenterClient:
    """Return a fresh CommandCenterClient with no session."""
    return CommandCenterClient()


@pytest.fixture()
def authed_client() -> CommandCenterClient:
    """Return a client initialised with a bearer token."""
    return CommandCenterClient(token="secret-token")


def _make_api_response(success: bool, data=None, error: str | None = None) -> APIResponse:
    return APIResponse(success=success, data=data, error=error)


# ---------------------------------------------------------------------------
# AgentStatus enum coverage
# ---------------------------------------------------------------------------


class TestAgentStatusEnum:
    """Ensure all AgentStatus values are exercised."""

    def test_active_value(self) -> None:
        assert AgentStatus.ACTIVE.value == "active"

    def test_idle_value(self) -> None:
        assert AgentStatus.IDLE.value == "idle"

    def test_completed_value(self) -> None:
        assert AgentStatus.COMPLETED.value == "completed"

    def test_error_value(self) -> None:
        assert AgentStatus.ERROR.value == "error"


# ---------------------------------------------------------------------------
# Approval tier variants
# ---------------------------------------------------------------------------


class TestApprovalTierVariants:
    """Cover watch and phone decision tiers that existing tests skip."""

    def test_tier_watch(self) -> None:
        approval = Approval.from_dict({"id": "a1", "tier": "watch"})
        assert approval.tier == DecisionTier.WATCH

    def test_tier_phone(self) -> None:
        approval = Approval.from_dict({"id": "a1", "tier": "phone"})
        assert approval.tier == DecisionTier.PHONE

    def test_tier_none_when_missing(self) -> None:
        approval = Approval.from_dict({"id": "a1"})
        assert approval.tier is None

    def test_resolved_fields_populated(self) -> None:
        data = {
            "id": "a2",
            "resolved_at": "2026-02-01T10:00:00Z",
            "resolved_by": "human-operator",
            "notes": "All good",
        }
        approval = Approval.from_dict(data)
        assert approval.resolved_by == "human-operator"
        assert approval.notes == "All good"
        assert approval.resolved_at is not None


# ---------------------------------------------------------------------------
# _request: argument forwarding
# ---------------------------------------------------------------------------


class TestRequestArgumentForwarding:
    """Verify _request passes data and params correctly to aiohttp."""

    @pytest.mark.asyncio
    async def test_get_sends_params_not_json_body(self, client: CommandCenterClient) -> None:
        """GET requests should use params kwarg, not json."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": []})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_response)
        client._session = mock_session

        await client._request("GET", "/api/agents", params={"status": "active"})

        _, kwargs = mock_session.request.call_args
        assert kwargs.get("params") == {"status": "active"}
        assert "json" not in kwargs

    @pytest.mark.asyncio
    async def test_post_sends_json_body(self, client: CommandCenterClient) -> None:
        """POST requests should attach the body via json kwarg."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_response)
        client._session = mock_session

        payload = {"role": "dev", "task": "work"}
        await client._request("POST", "/api/agents/register", data=payload)

        _, kwargs = mock_session.request.call_args
        assert kwargs.get("json") == payload

    @pytest.mark.asyncio
    async def test_url_is_correctly_constructed(self, client: CommandCenterClient) -> None:
        """URL must be base_url + path with no double slashes."""
        client.base_url = "http://myserver:9000"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_response)
        client._session = mock_session

        await client._request("GET", "/api/tasks/stats")

        positional_args, _ = mock_session.request.call_args
        assert positional_args[1] == "http://myserver:9000/api/tasks/stats"

    @pytest.mark.asyncio
    async def test_authorization_header_present_when_token_set(
        self, authed_client: CommandCenterClient
    ) -> None:
        """Authorization header must be Bearer <token> when token is configured."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_response)
        authed_client._session = mock_session

        await authed_client._request("GET", "/api/agents")

        _, kwargs = mock_session.request.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer secret-token"

    @pytest.mark.asyncio
    async def test_no_authorization_header_without_token(
        self, client: CommandCenterClient
    ) -> None:
        """Authorization header must be absent when no token is configured."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_response)
        client._session = mock_session

        await client._request("GET", "/api/agents")

        _, kwargs = mock_session.request.call_args
        headers = kwargs.get("headers", {})
        assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# HTTP error status codes: 401, 403, 500
# ---------------------------------------------------------------------------


class TestHTTPErrorCodes:
    """Parametrized tests for 4xx/5xx status codes."""

    @pytest.mark.parametrize(
        "status_code,detail",
        [
            (401, "Unauthorized"),
            (403, "Forbidden"),
            (422, "Unprocessable Entity"),
            (500, "Internal Server Error"),
            (503, "Service Unavailable"),
        ],
    )
    @pytest.mark.asyncio
    async def test_request_returns_failure_for_error_status(
        self, client: CommandCenterClient, status_code: int, detail: str
    ) -> None:
        mock_response = MagicMock()
        mock_response.status = status_code
        mock_response.json = AsyncMock(return_value={"detail": detail})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_response)
        client._session = mock_session

        response = await client._request("GET", "/api/test")

        assert response.success is False
        assert detail in (response.error or "")

    @pytest.mark.asyncio
    async def test_401_prevents_agent_list(self, client: CommandCenterClient) -> None:
        """list_agents must return [] on a 401 response."""
        mock_response = MagicMock()
        mock_response.status = 401
        mock_response.json = AsyncMock(return_value={"detail": "Unauthorized"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_response)
        client._session = mock_session

        agents = await client.list_agents()
        assert agents == []

    @pytest.mark.asyncio
    async def test_500_prevents_task_create(self, client: CommandCenterClient) -> None:
        """create_task must return None on a 500 response."""
        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.json = AsyncMock(return_value={"detail": "Internal Server Error"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_response)
        client._session = mock_session

        result = await client.create_task("Task X")
        assert result is None

    @pytest.mark.asyncio
    async def test_403_prevents_approval(self, client: CommandCenterClient) -> None:
        """approve must return False on a 403 response."""
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.json = AsyncMock(return_value={"detail": "Forbidden"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_response)
        client._session = mock_session

        result = await client.approve("req-1")
        assert result is False


# ---------------------------------------------------------------------------
# Timeout and network error handling
# ---------------------------------------------------------------------------


class TestNetworkErrors:
    """Cover OS/network-level errors beyond ConnectionRefusedError."""

    @pytest.mark.asyncio
    async def test_os_error_returns_failure(self, client: CommandCenterClient) -> None:
        mock_session = MagicMock()
        mock_session.request = MagicMock(side_effect=OSError("Network unreachable"))
        client._session = mock_session

        response = await client._request("GET", "/api/agents")

        assert response.success is False
        assert "Network unreachable" in (response.error or "")

    @pytest.mark.asyncio
    async def test_connection_error_returns_failure(self, client: CommandCenterClient) -> None:
        mock_session = MagicMock()
        mock_session.request = MagicMock(side_effect=ConnectionError("Connection dropped"))
        client._session = mock_session

        response = await client._request("GET", "/api/agents")

        assert response.success is False

    @pytest.mark.asyncio
    async def test_generic_exception_returns_failure(self, client: CommandCenterClient) -> None:
        mock_session = MagicMock()
        mock_session.request = MagicMock(side_effect=RuntimeError("Unexpected"))
        client._session = mock_session

        response = await client._request("GET", "/api/agents")

        assert response.success is False
        assert "Unexpected" in (response.error or "")


# ---------------------------------------------------------------------------
# Session lifecycle edge cases
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """Additional session management edge cases."""

    @pytest.mark.asyncio
    async def test_close_when_session_is_none_is_safe(
        self, client: CommandCenterClient
    ) -> None:
        """close() with no active session must not raise."""
        assert client._session is None
        await client.close()  # should be a no-op

    @pytest.mark.asyncio
    async def test_ensure_session_raises_import_error_without_aiohttp(
        self, client: CommandCenterClient
    ) -> None:
        """_ensure_session must raise ImportError when aiohttp is unavailable."""
        with patch.dict("sys.modules", {"aiohttp": None}):
            with pytest.raises(ImportError, match="aiohttp"):
                await client._ensure_session()


# ---------------------------------------------------------------------------
# Agent operations — edge cases
# ---------------------------------------------------------------------------


class TestAgentOperationsEdgeCases:
    """Additional coverage for agent-related methods."""

    @pytest.mark.asyncio
    async def test_list_agents_with_status_filter_passes_param(
        self, client: CommandCenterClient
    ) -> None:
        """list_agents should pass the status value as a query param."""
        mock_response = _make_api_response(success=True, data=[])

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.list_agents(status=AgentStatus.IDLE)

        mock_req.assert_called_once_with("GET", "/api/agents", params={"status": "idle"})

    @pytest.mark.asyncio
    async def test_list_agents_agents_wrapped_in_dict(
        self, client: CommandCenterClient
    ) -> None:
        """list_agents should unwrap a {agents: [...]} response shape."""
        data = {"agents": [{"id": "a1"}, {"id": "a2"}]}
        mock_response = _make_api_response(success=True, data=data)

        with patch.object(client, "_request", return_value=mock_response):
            agents = await client.list_agents()

        assert len(agents) == 2
        assert agents[0].id == "a1"
        assert agents[1].id == "a2"

    @pytest.mark.asyncio
    async def test_register_agent_includes_optional_name(
        self, client: CommandCenterClient
    ) -> None:
        """register_agent must include name in the POST body when supplied."""
        mock_response = _make_api_response(
            success=True, data={"id": "x", "role": "dev", "name": "MyAgent"}
        )

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            agent = await client.register_agent(
                role="dev", task="Build stuff", name="MyAgent"
            )

        assert agent is not None
        assert agent.name == "MyAgent"
        _, kwargs = mock_req.call_args
        assert kwargs["data"]["name"] == "MyAgent"

    @pytest.mark.asyncio
    async def test_update_agent_progress_includes_files_modified(
        self, client: CommandCenterClient
    ) -> None:
        """update_agent_progress must forward files_modified when provided."""
        mock_response = _make_api_response(success=True)

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            result = await client.update_agent_progress(
                "agent-1",
                75,
                current_task="Running tests",
                files_modified=["a.py", "b.py"],
            )

        assert result is True
        _, kwargs = mock_req.call_args
        assert kwargs["data"]["files_modified"] == ["a.py", "b.py"]
        assert kwargs["data"]["current_task"] == "Running tests"

    @pytest.mark.asyncio
    async def test_complete_agent_without_summary_sends_empty_body(
        self, client: CommandCenterClient
    ) -> None:
        """complete_agent with no summary should POST an empty dict body."""
        mock_response = _make_api_response(success=True)

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            result = await client.complete_agent("agent-1")

        assert result is True
        _, kwargs = mock_req.call_args
        assert kwargs["data"] == {}

    @pytest.mark.asyncio
    async def test_heartbeat_failure_returns_false(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Agent not found")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.heartbeat("nonexistent-agent")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_message_custom_type(self, client: CommandCenterClient) -> None:
        """send_message should forward the custom message_type."""
        mock_response = _make_api_response(success=True)

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.send_message("agent-1", "Please retry", message_type="query")

        _, kwargs = mock_req.call_args
        assert kwargs["data"]["type"] == "query"
        assert kwargs["data"]["content"] == "Please retry"

    @pytest.mark.asyncio
    async def test_broadcast_message_returns_zero_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="No agents")

        with patch.object(client, "_request", return_value=mock_response):
            count = await client.broadcast_message("Hello")

        assert count == 0

    @pytest.mark.asyncio
    async def test_broadcast_message_returns_zero_when_data_not_dict(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True, data="ok")

        with patch.object(client, "_request", return_value=mock_response):
            count = await client.broadcast_message("Hello")

        assert count == 0


# ---------------------------------------------------------------------------
# Approval operations — edge cases
# ---------------------------------------------------------------------------


class TestApprovalOperationsEdgeCases:
    """Additional coverage for approval-related methods."""

    @pytest.mark.asyncio
    async def test_list_approvals_approvals_wrapped_in_dict(
        self, client: CommandCenterClient
    ) -> None:
        """list_approvals should unwrap a {approvals: [...]} shape."""
        data = {"approvals": [{"id": "a1"}, {"id": "a2"}]}
        mock_response = _make_api_response(success=True, data=data)

        with patch.object(client, "_request", return_value=mock_response):
            approvals = await client.list_approvals()

        assert len(approvals) == 2

    @pytest.mark.asyncio
    async def test_get_approval_failure_returns_none(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Not found")

        with patch.object(client, "_request", return_value=mock_response):
            approval = await client.get_approval("nonexistent")

        assert approval is None

    @pytest.mark.asyncio
    async def test_get_approval_count_returns_zero_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Error")

        with patch.object(client, "_request", return_value=mock_response):
            count = await client.get_approval_count()

        assert count == 0

    @pytest.mark.asyncio
    async def test_get_approval_count_returns_zero_when_data_not_dict(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True, data=None)

        with patch.object(client, "_request", return_value=mock_response):
            count = await client.get_approval_count()

        assert count == 0

    @pytest.mark.asyncio
    async def test_get_approval_stats_returns_empty_dict_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Down")

        with patch.object(client, "_request", return_value=mock_response):
            stats = await client.get_approval_stats()

        assert stats == {}

    @pytest.mark.asyncio
    async def test_reject_sends_correct_payload(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True)

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.reject("req-1", reason="Not ready")

        _, kwargs = mock_req.call_args
        assert kwargs["data"]["reason"] == "Not ready"
        assert kwargs["data"]["rejected_by"] == "cli"

    @pytest.mark.asyncio
    async def test_approve_sends_correct_payload(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True)

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.approve("req-1", notes="Looks good")

        _, kwargs = mock_req.call_args
        assert kwargs["data"]["notes"] == "Looks good"
        assert kwargs["data"]["approved_by"] == "cli"


# ---------------------------------------------------------------------------
# Portfolio operations — failure paths
# ---------------------------------------------------------------------------


class TestPortfolioOperationsFailurePaths:
    """Ensure failures return the correct empty/None sentinel."""

    @pytest.mark.asyncio
    async def test_list_portfolio_returns_empty_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Down")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.list_portfolio()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_portfolio_returns_empty_when_data_is_not_list(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True, data={"domains": []})

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.list_portfolio()

        # data is a dict, not a list, so should return []
        assert result == []

    @pytest.mark.asyncio
    async def test_get_domain_returns_none_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Not found")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_domain("missing-domain")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_project_returns_none_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Not found")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_project("d1", "missing-project")

        assert result is None


# ---------------------------------------------------------------------------
# Pattern operations — edge cases
# ---------------------------------------------------------------------------


class TestPatternOperationsEdgeCases:
    """Additional pattern coverage."""

    @pytest.mark.asyncio
    async def test_list_patterns_with_category_passes_param(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True, data=[])

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.list_patterns(category="auth")

        _, kwargs = mock_req.call_args
        assert kwargs["params"]["category"] == "auth"

    @pytest.mark.asyncio
    async def test_list_patterns_unwraps_dict_response(
        self, client: CommandCenterClient
    ) -> None:
        data = {"patterns": [{"id": "p1", "name": "P1"}, {"id": "p2", "name": "P2"}]}
        mock_response = _make_api_response(success=True, data=data)

        with patch.object(client, "_request", return_value=mock_response):
            patterns = await client.list_patterns()

        assert len(patterns) == 2
        assert patterns[0].id == "p1"

    @pytest.mark.asyncio
    async def test_list_patterns_returns_empty_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Error")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.list_patterns()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_pattern_returns_none_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Not found")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_pattern("ghost")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_pattern_without_description(
        self, client: CommandCenterClient
    ) -> None:
        """create_pattern without description should NOT include description key in body."""
        mock_response = _make_api_response(
            success=True, data={"id": "p1", "name": "Bare"}
        )

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            pattern = await client.create_pattern("Bare")

        assert pattern is not None
        _, kwargs = mock_req.call_args
        assert "description" not in kwargs["data"]

    @pytest.mark.asyncio
    async def test_record_outcome_with_context(self, client: CommandCenterClient) -> None:
        mock_response = _make_api_response(success=True)

        ctx = {"domain": "test", "retries": 3}
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            result = await client.record_outcome("p1", True, context=ctx)

        assert result is True
        _, kwargs = mock_req.call_args
        assert kwargs["data"]["context"] == ctx
        assert kwargs["data"]["success"] is True

    @pytest.mark.asyncio
    async def test_record_outcome_failure_returns_false(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Server error")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.record_outcome("p1", False)

        assert result is False


# ---------------------------------------------------------------------------
# Task operations — edge cases
# ---------------------------------------------------------------------------


class TestTaskOperationsEdgeCases:
    """Additional task-related coverage."""

    @pytest.mark.asyncio
    async def test_list_tasks_with_direct_list_response(
        self, client: CommandCenterClient
    ) -> None:
        """list_tasks must handle a plain list response (no dict wrapper)."""
        task_list = [{"id": "t1"}, {"id": "t2"}]
        mock_response = _make_api_response(success=True, data=task_list)

        with patch.object(client, "_request", return_value=mock_response):
            tasks = await client.list_tasks()

        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_list_tasks_returns_empty_on_invalid_data(
        self, client: CommandCenterClient
    ) -> None:
        """list_tasks should return [] when data is neither dict nor list."""
        mock_response = _make_api_response(success=True, data="unexpected-string")

        with patch.object(client, "_request", return_value=mock_response):
            tasks = await client.list_tasks()

        assert tasks == []

    @pytest.mark.asyncio
    async def test_list_tasks_passes_priority_filter(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True, data=[])

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.list_tasks(status="pending", priority="high", limit=20)

        _, kwargs = mock_req.call_args
        assert kwargs["params"]["status"] == "pending"
        assert kwargs["params"]["priority"] == "high"
        assert kwargs["params"]["limit"] == 20

    @pytest.mark.asyncio
    async def test_get_task_returns_none_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Not found")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_task("ghost-task")

        assert result is None

    @pytest.mark.asyncio
    async def test_claim_task_returns_none_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Already claimed")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.claim_task("task-1", "agent-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_dispatch_task_returns_none_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Task not found")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.dispatch_task("task-1", "agent-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_task_with_required_role(
        self, client: CommandCenterClient
    ) -> None:
        """create_task should include required_role when supplied."""
        mock_response = _make_api_response(success=True, data={"id": "t1"})

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.create_task(
                subject="Build X",
                description="Desc",
                priority="high",
                required_role="backend-engineer",
            )

        _, kwargs = mock_req.call_args
        assert kwargs["data"]["required_role"] == "backend-engineer"

    @pytest.mark.asyncio
    async def test_update_task_sends_only_provided_fields(
        self, client: CommandCenterClient
    ) -> None:
        """update_task should only include explicitly passed fields."""
        mock_response = _make_api_response(success=True, data={"id": "t1"})

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.update_task("t1", status="completed")

        _, kwargs = mock_req.call_args
        assert kwargs["data"] == {"status": "completed"}
        assert "subject" not in kwargs["data"]
        assert "priority" not in kwargs["data"]

    @pytest.mark.asyncio
    async def test_update_task_returns_none_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Not found")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.update_task("t1", subject="New name")

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_task_returns_false_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Not found")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.delete_task("ghost")

        assert result is False

    @pytest.mark.asyncio
    async def test_get_task_stats_returns_empty_dict_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Down")

        with patch.object(client, "_request", return_value=mock_response):
            stats = await client.get_task_stats()

        assert stats == {}

    @pytest.mark.asyncio
    async def test_reorder_tasks_returns_empty_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Server error")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.reorder_tasks(["t1", "t2"])

        assert result == []

    @pytest.mark.asyncio
    async def test_reorder_tasks_returns_empty_when_data_is_not_dict(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True, data="ok")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.reorder_tasks(["t1"])

        assert result == []

    @pytest.mark.asyncio
    async def test_get_recommended_tasks_with_all_params(
        self, client: CommandCenterClient
    ) -> None:
        """All optional params should be forwarded as query params."""
        mock_response = _make_api_response(success=True, data=[])

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.get_recommended_tasks(
                limit=5,
                agent_id="agent-1",
                include_blocked=True,
                min_priority="high",
                task_type="feature",
                domain="codeswiftr-com",
            )

        _, kwargs = mock_req.call_args
        p = kwargs["params"]
        assert p["limit"] == 5
        assert p["agent_id"] == "agent-1"
        assert p["include_blocked"] == "true"
        assert p["min_priority"] == "high"
        assert p["task_type"] == "feature"
        assert p["domain"] == "codeswiftr-com"

    @pytest.mark.asyncio
    async def test_get_recommended_tasks_returns_empty_on_failure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="Error")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_recommended_tasks()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_recommended_tasks_with_recommendations_wrapper(
        self, client: CommandCenterClient
    ) -> None:
        """Should unwrap the {recommendations: [{task: {...}}, ...]} shape."""
        recs = [
            {"task": {"id": "t1", "subject": "A"}, "score": 0.9},
            {"task": {"id": "t2", "subject": "B"}, "score": 0.8},
        ]
        mock_response = _make_api_response(
            success=True, data={"recommendations": recs, "meta": {}}
        )

        with patch.object(client, "_request", return_value=mock_response):
            tasks = await client.get_recommended_tasks()

        assert len(tasks) == 2
        assert tasks[0]["id"] == "t1"
        assert tasks[1]["id"] == "t2"

    @pytest.mark.asyncio
    async def test_get_recommended_tasks_with_direct_list_response(
        self, client: CommandCenterClient
    ) -> None:
        task_list = [{"id": "t1"}, {"id": "t2"}]
        mock_response = _make_api_response(success=True, data=task_list)

        with patch.object(client, "_request", return_value=mock_response):
            tasks = await client.get_recommended_tasks()

        assert len(tasks) == 2


# ---------------------------------------------------------------------------
# Decision operations (not covered by any existing test file)
# ---------------------------------------------------------------------------


class TestDecisionOperations:
    """Full coverage for list_decisions and get_decision."""

    @pytest.mark.asyncio
    async def test_list_decisions_success(self, client: CommandCenterClient) -> None:
        payload = {
            "decisions": [
                {"id": "d1", "context": "auth", "tier": "watch"},
                {"id": "d2", "context": "deploy", "tier": "desktop"},
            ],
            "count": 2,
        }
        mock_response = _make_api_response(success=True, data=payload)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.list_decisions()

        assert result["count"] == 2
        assert len(result["decisions"]) == 2

    @pytest.mark.asyncio
    async def test_list_decisions_failure_returns_empty_structure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="DB down")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.list_decisions()

        assert result == {"decisions": [], "count": 0}

    @pytest.mark.asyncio
    async def test_list_decisions_non_dict_data_returns_empty_structure(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True, data=[{"id": "d1"}])

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.list_decisions()

        assert result == {"decisions": [], "count": 0}

    @pytest.mark.asyncio
    async def test_list_decisions_with_all_filters(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(
            success=True, data={"decisions": [], "count": 0}
        )

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.list_decisions(
                domain="codeswiftr-com",
                project="interview-simulator",
                context="auth",
                limit=25,
            )

        _, kwargs = mock_req.call_args
        p = kwargs["params"]
        assert p["domain"] == "codeswiftr-com"
        assert p["project"] == "interview-simulator"
        assert p["context"] == "auth"
        assert p["limit"] == 25

    @pytest.mark.asyncio
    async def test_get_decision_success(self, client: CommandCenterClient) -> None:
        decision = {"id": "d1", "tier": "watch", "context": "deploy"}
        mock_response = _make_api_response(success=True, data=decision)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_decision("d1")

        assert result is not None
        assert result["id"] == "d1"

    @pytest.mark.asyncio
    async def test_get_decision_not_found_returns_none(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=False, error="404: not found")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_decision("ghost")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_decision_non_dict_data_returns_none(
        self, client: CommandCenterClient
    ) -> None:
        mock_response = _make_api_response(success=True, data="unexpected-string")

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_decision("d1")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_decision_failure_without_404_returns_none(
        self, client: CommandCenterClient
    ) -> None:
        """A 500-level failure should also return None (not raise)."""
        mock_response = _make_api_response(
            success=False, error="Internal server error"
        )

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_decision("d1")

        assert result is None

    def test_sync_list_decisions(self, client: CommandCenterClient) -> None:
        expected = {"decisions": [{"id": "d1"}], "count": 1}
        with patch.object(client, "list_decisions", new_callable=AsyncMock) as mock_l:
            mock_l.return_value = expected
            result = client.sync_list_decisions(domain="codeswiftr-com", limit=5)

        assert result["count"] == 1
        mock_l.assert_awaited_once()

    def test_sync_get_decision(self, client: CommandCenterClient) -> None:
        decision = {"id": "d1", "tier": "phone"}
        with patch.object(client, "get_decision", new_callable=AsyncMock) as mock_g:
            mock_g.return_value = decision
            result = client.sync_get_decision("d1")

        assert result["tier"] == "phone"
        mock_g.assert_awaited_once()


# ---------------------------------------------------------------------------
# SSE edge cases
# ---------------------------------------------------------------------------


class TestSSEEdgeCases:
    """Cover SSE non-200 status and exception paths."""

    @pytest.mark.asyncio
    async def test_subscribe_events_non_200_yields_nothing(
        self, client: CommandCenterClient
    ) -> None:
        """A non-200 SSE response must yield no events."""
        mock_response = MagicMock()
        mock_response.status = 503
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        client._session = mock_session

        events = []
        async for event in client.subscribe_events():
            events.append(event)

        assert events == []

    @pytest.mark.asyncio
    async def test_subscribe_events_exception_yields_nothing(
        self, client: CommandCenterClient
    ) -> None:
        """An exception during SSE streaming must be swallowed and yield nothing."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=ConnectionError("Dropped"))
        client._session = mock_session

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            events = []
            async for event in client.subscribe_events():
                events.append(event)

        assert events == []

    @pytest.mark.asyncio
    async def test_subscribe_events_skips_comment_lines(
        self, client: CommandCenterClient
    ) -> None:
        """Lines starting with ':' (keep-alive comments) must be skipped."""
        mock_response = MagicMock()
        mock_response.status = 200

        mock_response.content = MagicMock()
        mock_response.content.__aiter__ = Mock(return_value=mock_response.content)
        mock_response.content.__anext__ = AsyncMock()
        mock_response.content.__anext__.side_effect = [
            b": keep-alive\n",
            b"\n",
            b'data: {"type": "ping", "data": {}}\n',
            StopAsyncIteration,
        ]

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        client._session = mock_session

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            events = []
            async for event in client.subscribe_events():
                events.append(event)

        assert len(events) == 1
        assert events[0].type == "ping"

    @pytest.mark.asyncio
    async def test_subscribe_events_token_passed_as_query_param(
        self, authed_client: CommandCenterClient
    ) -> None:
        """When a token is set it must be forwarded as a query param to SSE."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.content = MagicMock()
        mock_response.content.__aiter__ = Mock(return_value=mock_response.content)
        mock_response.content.__anext__ = AsyncMock(side_effect=StopAsyncIteration)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        authed_client._session = mock_session

        with patch.object(authed_client, "_ensure_session", new_callable=AsyncMock):
            async for _ in authed_client.subscribe_events():
                pass

        _, kwargs = mock_session.get.call_args
        assert kwargs.get("params", {}).get("token") == "secret-token"


# ---------------------------------------------------------------------------
# SSEEvent parsing edge cases
# ---------------------------------------------------------------------------


class TestSSEEventParsing:
    """Additional SSEEvent.from_line scenarios."""

    def test_from_line_with_timestamp(self) -> None:
        line = 'data: {"type": "tick", "data": {}, "timestamp": "2026-02-22T12:00:00Z"}'
        event = SSEEvent.from_line(line)

        assert event is not None
        assert event.type == "tick"
        assert event.timestamp is not None

    def test_from_line_missing_type_defaults_to_unknown(self) -> None:
        line = 'data: {"data": {"id": "1"}}'
        event = SSEEvent.from_line(line)

        assert event is not None
        assert event.type == "unknown"

    def test_from_line_missing_data_key_defaults_to_empty_dict(self) -> None:
        line = 'data: {"type": "noop"}'
        event = SSEEvent.from_line(line)

        assert event is not None
        assert event.data == {}

    def test_from_line_empty_data_value(self) -> None:
        """data: prefix with empty JSON string should return None."""
        event = SSEEvent.from_line("data: ")
        assert event is None
