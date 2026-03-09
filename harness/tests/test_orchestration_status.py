"""Tests for orchestration status auto-sync."""

import json
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from forge_harness.orchestration_status import (
    OrchestrationStatusSync,
    sync_orchestration_status,
)
from forge_harness.state_store import AgentRole, AgentStatus, AgentType


@pytest.fixture
def mock_agents():
    """Create mock agent session data."""
    mock_agent_1 = Mock()
    mock_agent_1.session_id = "agent-001"
    mock_agent_1.domain = "codeswiftr-com"
    mock_agent_1.project = "interview-simulator"
    mock_agent_1.agent_role = AgentRole.BUILDER
    mock_agent_1.agent_type = AgentType.CLAUDE_CODE
    mock_agent_1.status = AgentStatus.ACTIVE
    mock_agent_1.current_task = "Implementing JWT auth"
    mock_agent_1.registered_at = datetime(2026, 1, 27, 10, 0, 0, tzinfo=UTC)
    mock_agent_1.last_heartbeat = datetime(2026, 1, 27, 10, 30, 0, tzinfo=UTC)

    mock_agent_2 = Mock()
    mock_agent_2.session_id = "agent-002"
    mock_agent_2.domain = "codeswiftr-com"
    mock_agent_2.project = "interview-simulator"
    mock_agent_2.agent_role = AgentRole.CONTENT
    mock_agent_2.agent_type = AgentType.CLAUDE_CODE
    mock_agent_2.status = AgentStatus.IDLE
    mock_agent_2.current_task = None
    mock_agent_2.registered_at = None
    mock_agent_2.last_heartbeat = datetime(2026, 1, 27, 10, 25, 0, tzinfo=UTC)

    return [mock_agent_1, mock_agent_2]


@pytest.mark.asyncio
async def test_get_current_status(tmp_path, mock_agents):
    """Should get current orchestration status from agent telemetry."""
    mock_state_store = Mock()
    mock_state_store.get_active_agents = Mock(return_value=mock_agents)
    mock_state_store.connect = Mock(return_value="sqlite")

    with patch(
        "forge_harness.orchestration_status.StateStore",
        return_value=mock_state_store,
    ):
        sync = OrchestrationStatusSync(forge_root=tmp_path)
        status = await sync.get_current_status()

        assert status["version"] == "1.0"
        assert "updated_at" in status
        assert status["summary"]["total_agents"] == 2
        assert status["summary"]["active"] == 1
        assert status["summary"]["idle"] == 1
        assert status["summary"]["domains"] == 1
        assert status["summary"]["projects"] == 1

        # Check domain/project structure
        assert "codeswiftr-com" in status["domains"]
        assert "interview-simulator" in status["domains"]["codeswiftr-com"]

        agents = status["domains"]["codeswiftr-com"]["interview-simulator"]
        assert len(agents) == 2
        assert agents[0]["agent_id"] == "agent-001"
        assert agents[0]["role"] == "builder"
        assert agents[0]["status"] == "active"
        assert agents[0]["task"] == "Implementing JWT auth"
        assert agents[0]["progress"] is None  # AgentSession doesn't track progress


@pytest.mark.asyncio
async def test_sync_status_creates_files(tmp_path, mock_agents):
    """Should create JSON and markdown status files."""
    mock_state_store = Mock()
    mock_state_store.get_active_agents = Mock(return_value=mock_agents)
    mock_state_store.connect = Mock(return_value="sqlite")

    with patch(
        "forge_harness.orchestration_status.StateStore",
        return_value=mock_state_store,
    ):
        output_dir = tmp_path / "output"
        sync = OrchestrationStatusSync(forge_root=tmp_path, output_dir=output_dir)

        status = await sync.sync_status()

        # Check JSON file
        json_path = output_dir / "ORCHESTRATION_STATUS.json"
        assert json_path.exists()

        with open(json_path) as f:
            json_data = json.load(f)
            assert json_data["version"] == "1.0"
            assert json_data["summary"]["total_agents"] == 2

        # Check Markdown file
        md_path = output_dir / "ORCHESTRATION_STATUS.md"
        assert md_path.exists()

        md_content = md_path.read_text()
        assert "# Orchestration Status" in md_content
        assert "**Total Agents:** 2" in md_content
        assert "**Active:** 1" in md_content
        assert "codeswiftr-com" in md_content
        assert "interview-simulator" in md_content


@pytest.mark.asyncio
async def test_detect_phase_exploration(tmp_path):
    """Should detect exploration phase from agent tasks."""
    mock_agent = Mock()
    mock_agent.session_id = "agent-001"
    mock_agent.domain = "test-domain"
    mock_agent.project = "test-project"
    mock_agent.agent_role = AgentRole.BUILDER
    mock_agent.agent_type = AgentType.CLAUDE_CODE
    mock_agent.status = AgentStatus.ACTIVE
    mock_agent.current_task = "Running niche exploration"
    mock_agent.registered_at = None
    mock_agent.last_heartbeat = None

    mock_state_store = Mock()
    mock_state_store.get_active_agents = Mock(return_value=[mock_agent])

    with patch(
        "forge_harness.orchestration_status.StateStore",
        return_value=mock_state_store,
    ):
        sync = OrchestrationStatusSync(forge_root=tmp_path)
        status = await sync.get_current_status()

        assert status["phase"] == "exploration"


@pytest.mark.asyncio
async def test_detect_phase_backend_development(tmp_path):
    """Should detect backend development phase."""
    mock_agent = Mock()
    mock_agent.session_id = "agent-001"
    mock_agent.domain = "test-domain"
    mock_agent.project = "test-project"
    mock_agent.agent_role = AgentRole.BUILDER
    mock_agent.agent_type = AgentType.CLAUDE_CODE
    mock_agent.status = AgentStatus.ACTIVE
    mock_agent.current_task = "Implementing FastAPI endpoints"
    mock_agent.registered_at = None
    mock_agent.last_heartbeat = None

    mock_state_store = Mock()
    mock_state_store.get_active_agents = Mock(return_value=[mock_agent])

    with patch(
        "forge_harness.orchestration_status.StateStore",
        return_value=mock_state_store,
    ):
        sync = OrchestrationStatusSync(forge_root=tmp_path)
        status = await sync.get_current_status()

        assert status["phase"] == "backend_development"


@pytest.mark.asyncio
async def test_detect_phase_idle(tmp_path):
    """Should detect idle phase when no agents."""
    mock_state_store = Mock()
    mock_state_store.get_active_agents = Mock(return_value=[])

    with patch(
        "forge_harness.orchestration_status.StateStore",
        return_value=mock_state_store,
    ):
        sync = OrchestrationStatusSync(forge_root=tmp_path)
        status = await sync.get_current_status()

        assert status["phase"] == "idle"


@pytest.mark.asyncio
async def test_convenience_function(tmp_path, mock_agents):
    """Should work via convenience function."""
    mock_state_store = Mock()
    mock_state_store.get_active_agents = Mock(return_value=mock_agents)
    mock_state_store.connect = Mock(return_value="sqlite")

    with patch(
        "forge_harness.orchestration_status.StateStore",
        return_value=mock_state_store,
    ):
        output_dir = tmp_path / "output"
        status = await sync_orchestration_status(tmp_path, output_dir)

        assert status["summary"]["total_agents"] == 2
        assert (output_dir / "ORCHESTRATION_STATUS.json").exists()
        assert (output_dir / "ORCHESTRATION_STATUS.md").exists()


@pytest.mark.asyncio
async def test_markdown_generation_formatting(tmp_path, mock_agents):
    """Should generate properly formatted markdown."""
    mock_state_store = Mock()
    mock_state_store.get_active_agents = Mock(return_value=mock_agents)
    mock_state_store.connect = Mock(return_value="sqlite")

    with patch(
        "forge_harness.orchestration_status.StateStore",
        return_value=mock_state_store,
    ):
        sync = OrchestrationStatusSync(forge_root=tmp_path)
        status = await sync.get_current_status()
        markdown = sync._generate_markdown(status)

        # Check structure
        assert "# Orchestration Status" in markdown
        assert "## Summary" in markdown
        assert "## Active Work" in markdown
        assert "### codeswiftr-com" in markdown
        assert "#### interview-simulator" in markdown

        # Check table headers
        assert "| Agent | Role | Status | Task | Progress |" in markdown
        assert "|-------|------|--------|------|----------|" in markdown

        # Check data rows
        assert "`agent-00`" in markdown
        assert "builder" in markdown
        assert "Implementing JWT auth" in markdown
        assert "N/A" in markdown  # No progress tracking in AgentSession
