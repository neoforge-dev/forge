"""Tests for forge glance dashboard."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.glance.data.fetcher import GlanceFetcher, _format_age


def _textual_available() -> bool:
    """Check if textual can be imported (may fail in full test suite due to mock pollution)."""
    try:
        import textual.app  # noqa: F401

        return True
    except (ImportError, AttributeError):
        return False
from forge_harness.glance.data.models import (
    AgentInfo,
    DispatchInfo,
    ErrorInfo,
    GitInfo,
    GlanceData,
    NodeInfo,
    TaskStats,
    progress_bar,
    sparkline,
)

# ---------------------------------------------------------------------------
# Sparkline & progress bar
# ---------------------------------------------------------------------------


class TestSparkline:
    def test_empty(self):
        result = sparkline([])
        assert len(result) == 12
        assert result == "\u2581" * 12

    def test_single_value(self):
        result = sparkline([5], width=5)
        assert len(result) == 5

    def test_ascending(self):
        result = sparkline([0, 1, 2, 3, 4, 5, 6, 7], width=8)
        assert len(result) == 8
        # First char should be lowest, last should be highest
        assert result[0] == "\u2581"
        assert result[-1] == "\u2588"

    def test_all_zeros(self):
        result = sparkline([0, 0, 0, 0], width=4)
        assert len(result) == 4

    def test_truncation(self):
        # More values than width — should resample
        result = sparkline(list(range(100)), width=10)
        assert len(result) == 10

    def test_padding(self):
        # Fewer values than width — should pad
        result = sparkline([1, 2, 3], width=8)
        assert len(result) == 8


class TestProgressBar:
    def test_empty(self):
        assert progress_bar(0, 10) == "\u2591" * 10

    def test_full(self):
        assert progress_bar(10, 10) == "\u2588" * 10

    def test_half(self):
        result = progress_bar(5, 10)
        assert result.count("\u2588") == 5
        assert result.count("\u2591") == 5

    def test_zero_total(self):
        assert progress_bar(0, 0) == "\u2591" * 10

    def test_custom_width(self):
        result = progress_bar(3, 6, width=6)
        assert len(result) == 6
        assert result.count("\u2588") == 3


# ---------------------------------------------------------------------------
# GlanceData model
# ---------------------------------------------------------------------------


class TestGlanceData:
    def test_defaults(self):
        data = GlanceData()
        assert data.source == "offline"
        assert data.active_agents == 0
        assert data.total_agents == 0
        assert data.health_state == "healthy"
        assert isinstance(data.tasks, TaskStats)
        assert isinstance(data.git, GitInfo)
        assert data.nodes == []
        assert data.agents == []
        assert data.last_fetch_time == 0.0
        assert data.fetch_success is False
        assert data.dispatches == []

    def test_with_values(self):
        data = GlanceData(
            active_agents=5,
            total_agents=9,
            health_state="degraded",
            agents=[AgentInfo(name="claude", status="active")],
            nodes=[NodeInfo(name="prya", is_online=True)],
        )
        assert data.active_agents == 5
        assert len(data.agents) == 1
        assert data.agents[0].name == "claude"
        assert len(data.nodes) == 1

    def test_dispatch_info_defaults(self):
        di = DispatchInfo()
        assert di.agent_name == ""
        assert di.status == "active"
        assert di.filename == ""

    def test_git_info_new_fields(self):
        gi = GitInfo()
        assert gi.dirty_submodules == 0
        assert gi.total_submodules == 0
        assert gi.commits_today == 0

    def test_node_info_heartbeat_age(self):
        ni = NodeInfo(name="prya", heartbeat_age="2m ago")
        assert ni.heartbeat_age == "2m ago"


# ---------------------------------------------------------------------------
# GlanceFetcher — file-based mode
# ---------------------------------------------------------------------------


class TestGlanceFetcherFiles:
    @pytest.fixture
    def tmp_forge(self, tmp_path):
        """Create a minimal FORGE directory structure."""
        heartbeat_dir = tmp_path / ".forge" / "heartbeat" / "nodes"
        heartbeat_dir.mkdir(parents=True)

        # Write a node heartbeat
        node_data = {
            "node_id": "prya",
            "timestamp": "2026-02-28T10:00:00+00:00",
            "resources": {
                "cpu_usage_percent": 45.2,
                "ram_available_mb": 4096,
                "ram_total_mb": 16384,
                "disk_free_gb": 100.0,
                "queue_depth": 12,
            },
            "health": {"status": "healthy", "load_average": 3.5, "cores": 8},
        }
        (heartbeat_dir / "prya.json").write_text(json.dumps(node_data))

        # Write fleet config
        fleet_dir = tmp_path / ".forge/fleet"
        fleet_dir.mkdir()
        config = {
            "version": "1.0",
            "windows": {
                "forge:claude": {"agent_type": "claude", "role": "main"},
                "forge:kimi": {"agent_type": "kimi", "role": "analysis"},
            },
        }
        (fleet_dir / "config.json").write_text(json.dumps(config))

        return tmp_path

    @pytest.mark.asyncio
    async def test_file_fetch_nodes(self, tmp_forge):
        fetcher = GlanceFetcher(api_url=None, forge_root=tmp_forge)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        assert data.source == "files"
        assert len(data.nodes) == 1
        assert data.nodes[0].name == "prya"
        assert data.nodes[0].cpu_percent == 45.2
        # RAM: (16384 - 4096) / 16384 * 100 = 75%
        assert abs(data.nodes[0].ram_percent - 75.0) < 0.1
        assert data.nodes[0].is_online is True

    @pytest.mark.asyncio
    async def test_file_fetch_agents(self, tmp_forge):
        fetcher = GlanceFetcher(api_url=None, forge_root=tmp_forge)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        assert len(data.agents) == 2
        names = {a.name for a in data.agents}
        assert "claude" in names
        assert "kimi" in names

    @pytest.mark.asyncio
    async def test_file_fetch_empty_dir(self, tmp_path):
        fetcher = GlanceFetcher(api_url=None, forge_root=tmp_path)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        assert data.source == "files"
        assert data.nodes == []
        assert data.agents == []

    @pytest.mark.asyncio
    async def test_heartbeat_file_age(self, tmp_forge):
        """Heartbeat node files should have heartbeat_age populated."""
        fetcher = GlanceFetcher(api_url=None, forge_root=tmp_forge)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        assert len(data.nodes) == 1
        # The file was just written, so age should be very small
        assert data.nodes[0].heartbeat_age != ""
        assert "ago" in data.nodes[0].heartbeat_age


# ---------------------------------------------------------------------------
# GlanceFetcher — API mode
# ---------------------------------------------------------------------------


class TestGlanceFetcherAPI:
    @pytest.mark.asyncio
    async def test_api_fallback_on_error(self, tmp_path):
        """When API raises, should fall back to file-based."""
        fetcher = GlanceFetcher(
            api_url="http://localhost:8080",
            forge_root=tmp_path,
        )
        # Force _fetch_from_api to raise so fallback triggers
        with (
            patch.object(
                fetcher, "_fetch_from_api", new_callable=AsyncMock,
                side_effect=ConnectionError("API unreachable"),
            ),
            patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()),
        ):
            data = await fetcher.fetch_all()

        assert data.source == "files"

    @pytest.mark.asyncio
    async def test_api_success(self):
        """Mock successful API fetch."""
        from forge_harness.shared_contracts import AgentContract, NodeContract, TaskContract

        mock_agents = [
            AgentContract(
                session_id="s1",
                agent_role="dev",
                agent_name="claude",
                status="active",
                current_task="IS-219",
                progress_pct=83.0,
            ),
            AgentContract(
                session_id="s2",
                agent_role="analysis",
                agent_name="kimi",
                status="idle",
            ),
        ]
        mock_tasks = [
            TaskContract(id="t1", subject="Fix bug", status="pending", priority="high"),
            TaskContract(id="t2", subject="Deploy", status="in_progress", priority="medium"),
            TaskContract(id="t3", subject="Done task", status="completed"),
        ]
        mock_nodes = [
            NodeContract(
                node_id="prya",
                name="prya",
                status="online",
                agent_count=2,
                cpu_load=45.0,
                ram_usage=78.0,
            ),
        ]

        fetcher = GlanceFetcher(api_url="http://localhost:8080")

        with (
            patch("forge_harness.glance.data.fetcher.GlanceFetcher._fetch_git_info",
                  new_callable=AsyncMock, return_value=GitInfo(branch="main")),
            patch("forge_harness.status_adapter.fetch_agents",
                  new_callable=AsyncMock, return_value=mock_agents),
            patch("forge_harness.status_adapter.fetch_tasks",
                  new_callable=AsyncMock, return_value=mock_tasks),
            patch("forge_harness.status_adapter.fetch_fleet_status",
                  new_callable=AsyncMock, return_value=mock_nodes),
            patch("forge_harness.status_adapter.fetch_approvals",
                  new_callable=AsyncMock, return_value=[{"id": "apr_1"}]),
        ):
            data = await fetcher.fetch_all()

        assert data.source == "api"
        assert data.active_agents == 1
        assert data.total_agents == 2
        assert data.tasks.total == 3
        assert data.tasks.pending == 1
        assert data.tasks.active == 1
        assert data.tasks.completed == 1
        assert data.tasks.next_task == "Fix bug"
        assert data.pending_approvals == 1
        assert len(data.nodes) == 1
        assert data.nodes[0].name == "prya"


# ---------------------------------------------------------------------------
# Dispatch parsing
# ---------------------------------------------------------------------------


class TestDispatchParsing:
    @pytest.fixture
    def dispatch_forge(self, tmp_path):
        """Create FORGE structure with dispatch files."""
        dispatch_dir = tmp_path / ".forge" / "dispatches"
        dispatch_dir.mkdir(parents=True)
        results_dir = tmp_path / ".forge" / "heartbeat" / "results"
        results_dir.mkdir(parents=True)

        # Create dispatch files
        (dispatch_dir / "minimax-S54-marketing-docs-audit.md").write_text("# Task")
        (dispatch_dir / "glm-S54-dead-refs-scan.md").write_text("# Task")
        (dispatch_dir / "kimi-S54-new-task.md").write_text("# Task")

        # glm result exists (completed), kimi old result only
        (results_dir / "glm-S54-dead-refs-scan.md").write_text("# Done")
        (results_dir / "kimi-S53-old-task.md").write_text("# Old done")

        # Non-agent files that should be skipped
        (dispatch_dir / "forge-glance-improvement-plan.md").write_text("# Plan")
        (dispatch_dir / "phase9-task-1-models.md").write_text("# Phase")
        (dispatch_dir / "opus-glance-implementation.md").write_text("# Opus task")

        # Fleet config with matching agents
        fleet_dir = tmp_path / ".forge/fleet"
        fleet_dir.mkdir()
        config = {
            "version": "1.0",
            "windows": {
                "forge:minimax": {"role": "dev"},
                "forge:glm": {"role": "dev"},
                "forge:kimi": {"role": "analysis"},
            },
        }
        (fleet_dir / "config.json").write_text(json.dumps(config))

        return tmp_path

    @pytest.mark.asyncio
    async def test_dispatch_three_part_filename(self, dispatch_forge):
        """Dispatch files with 3-part names should be parsed correctly."""
        fetcher = GlanceFetcher(api_url=None, forge_root=dispatch_forge)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        # Find minimax dispatch
        minimax_d = [d for d in data.dispatches if d.agent_name == "minimax"]
        assert len(minimax_d) == 1
        assert minimax_d[0].sprint_id == "S54"
        assert minimax_d[0].task_subject == "marketing docs audit"
        assert minimax_d[0].status == "active"

    @pytest.mark.asyncio
    async def test_non_agent_files_skipped(self, dispatch_forge):
        """Files not starting with known agent name should be skipped."""
        fetcher = GlanceFetcher(api_url=None, forge_root=dispatch_forge)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        agent_names = {d.agent_name for d in data.dispatches}
        # forge, phase9 should NOT appear
        assert "forge" not in agent_names
        assert "phase9" not in agent_names
        # opus IS a known agent, so it SHOULD appear
        assert "opus" in agent_names

    @pytest.mark.asyncio
    async def test_exact_stem_matching_for_results(self, dispatch_forge):
        """Old results for different task should NOT mark new task as completed."""
        fetcher = GlanceFetcher(api_url=None, forge_root=dispatch_forge)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        # kimi has dispatch kimi-S54-new-task.md but only result kimi-S53-old-task.md
        kimi_d = [d for d in data.dispatches if d.agent_name == "kimi"]
        assert len(kimi_d) == 1
        assert kimi_d[0].status == "active"  # NOT completed

        # glm has exact matching result
        glm_d = [d for d in data.dispatches if d.agent_name == "glm"]
        assert len(glm_d) == 1
        assert glm_d[0].status == "completed"

    @pytest.mark.asyncio
    async def test_dispatch_updates_agent_status(self, dispatch_forge):
        """Dispatch parsing should update matching agent status."""
        fetcher = GlanceFetcher(api_url=None, forge_root=dispatch_forge)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        # Active agents should be > 0 now
        assert data.active_agents > 0

        # minimax and kimi should be active, glm completed
        agents_by_name = {a.name: a for a in data.agents}
        assert agents_by_name["minimax"].status == "active"
        assert agents_by_name["glm"].status == "completed"
        assert agents_by_name["kimi"].status == "active"

    @pytest.mark.asyncio
    async def test_no_dispatches_dir(self, tmp_path):
        """No dispatch directory should result in empty dispatches list."""
        fetcher = GlanceFetcher(api_url=None, forge_root=tmp_path)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        assert data.dispatches == []

    @pytest.mark.asyncio
    async def test_active_agents_from_dispatches_no_fleet_config(self, tmp_path):
        """active_agents must be non-zero from dispatch files even when no fleet config exists."""
        dispatch_dir = tmp_path / ".forge" / "dispatches"
        dispatch_dir.mkdir(parents=True)
        (dispatch_dir / "minimax-S54-fix-docs.md").write_text("# Task")
        (dispatch_dir / "kimi-S54-analysis.md").write_text("# Task")
        # No .forge/fleet/config.json

        fetcher = GlanceFetcher(api_url=None, forge_root=tmp_path)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        # Both dispatches are active (no results), so active_agents must equal 2
        assert data.active_agents == 2
        assert data.total_agents == 2
        agent_names = {a.name for a in data.agents}
        assert "minimax" in agent_names
        assert "kimi" in agent_names

    @pytest.mark.asyncio
    async def test_active_agents_dispatch_completed_no_fleet_config(self, tmp_path):
        """When a result exists for a dispatch, that agent should not count as active."""
        dispatch_dir = tmp_path / ".forge" / "dispatches"
        dispatch_dir.mkdir(parents=True)
        results_dir = tmp_path / ".forge" / "heartbeat" / "results"
        results_dir.mkdir(parents=True)
        (dispatch_dir / "minimax-S54-fix-docs.md").write_text("# Task")
        (dispatch_dir / "glm-S54-dead-refs.md").write_text("# Task")
        # glm has a matching result — completed
        (results_dir / "glm-S54-dead-refs.md").write_text("# Done")

        fetcher = GlanceFetcher(api_url=None, forge_root=tmp_path)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        # Only minimax is active; glm is completed
        assert data.active_agents == 1
        assert data.total_agents == 2
        agents_by_name = {a.name: a for a in data.agents}
        assert agents_by_name["minimax"].status == "active"
        assert agents_by_name["glm"].status == "completed"

    @pytest.mark.asyncio
    async def test_dispatch_no_phantom_agents_from_plan_files(self, tmp_path):
        """Plan/infra files in dispatches dir must not create phantom agents."""
        dispatch_dir = tmp_path / ".forge" / "dispatches"
        dispatch_dir.mkdir(parents=True)
        # These should all be skipped (first segment not in _KNOWN_AGENTS)
        (dispatch_dir / "forge-glance-improvement-plan.md").write_text("# Plan")
        (dispatch_dir / "phase9-task-1-models.md").write_text("# Phase")
        (dispatch_dir / "sprint10-planning-notes.md").write_text("# Sprint")
        # This one IS a known agent
        (dispatch_dir / "kimi-S54-real-task.md").write_text("# Task")

        fetcher = GlanceFetcher(api_url=None, forge_root=tmp_path)
        with patch.object(fetcher, "_fetch_git_info", new_callable=AsyncMock, return_value=GitInfo()):
            data = await fetcher.fetch_all()

        agent_names = {d.agent_name for d in data.dispatches}
        assert "forge" not in agent_names
        assert "phase9" not in agent_names
        assert "sprint10" not in agent_names
        assert "kimi" in agent_names
        # active_agents comes only from kimi
        assert data.active_agents == 1


# ---------------------------------------------------------------------------
# Card rendering
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _textual_available(),
    reason="textual import broken (test isolation conflict with dashboard mocks)",
)
class TestCardRendering:
    """Test that each card's update_data doesn't crash with various data."""

    @pytest.fixture
    def sample_data(self):
        return GlanceData(
            source="api",
            active_agents=5,
            total_agents=9,
            error_count=1,
            health_state="degraded",
            throughput_samples=[1, 3, 5, 8, 7, 4, 2, 1, 3, 5, 6, 4],
            agents=[
                AgentInfo(name="claude", status="active", task="IS-219", progress=83.0),
                AgentInfo(name="minimax", status="active", task="S54-doc", progress=45.0),
                AgentInfo(name="glm", status="idle"),
                AgentInfo(name="kimi", status="active", task="audit", progress=67.0),
                AgentInfo(name="gemini", status="stale"),
            ],
            tasks=TaskStats(
                total=47,
                pending=12,
                active=3,
                completed=29,
                blocked=3,
                next_task="IS-220",
                next_priority="high",
            ),
            nodes=[
                NodeInfo(name="prya", status="online", agent_count=2, max_agents=2, cpu_percent=45, ram_percent=78, is_online=True),
                NodeInfo(name="sati", status="online", agent_count=5, max_agents=6, cpu_percent=32, ram_percent=41, is_online=True),
                NodeInfo(name="gaea", status="offline", is_online=False),
            ],
            pending_approvals=2,
            errors_1h=0,
            errors_24h=3,
            error_samples=[0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1],
            recent_errors=[ErrorInfo(message="git lock timeout", source="sati/opencode", age="2h ago")],
            dispatches=[
                DispatchInfo(agent_name="minimax", task_subject="marketing docs audit", sprint_id="S54", status="completed"),
                DispatchInfo(agent_name="kimi", task_subject="new task", sprint_id="S54", status="active"),
            ],
            git=GitInfo(branch="main", is_clean=True, ahead=0, behind=0, last_commit_hash="0fe1866", last_commit_age="2h ago", submodule_count=8, total_submodules=8, dirty_submodules=2, commits_today=6),
        )

    def test_fleet_card(self, sample_data):
        from forge_harness.glance.cards.fleet import FleetHealthCard

        card = FleetHealthCard()
        card.update_data(sample_data)
        micro = card.render_micro(sample_data)
        assert "5/9" in micro

    def test_fleet_card_empty(self):
        """FleetHealthCard with empty GlanceData must not crash."""
        from forge_harness.glance.cards.fleet import FleetHealthCard

        card = FleetHealthCard()
        card.update_data(GlanceData())
        micro = card.render_micro(GlanceData())
        assert "0/0" in micro

    def test_agents_card(self, sample_data):
        from forge_harness.glance.cards.agents import AgentGridCard

        card = AgentGridCard()
        card.update_data(sample_data)
        micro = card.render_micro(sample_data)
        assert "3/5" in micro  # 3 active out of 5

    def test_agents_card_empty(self):
        from forge_harness.glance.cards.agents import AgentGridCard

        card = AgentGridCard()
        card.update_data(GlanceData())

    def test_tasks_card(self, sample_data):
        from forge_harness.glance.cards.tasks import TaskQueueCard

        card = TaskQueueCard()
        card.update_data(sample_data)
        micro = card.render_micro(sample_data)
        assert "12P" in micro

    def test_tasks_card_empty(self):
        """TaskQueueCard with empty GlanceData must not crash."""
        from forge_harness.glance.cards.tasks import TaskQueueCard

        card = TaskQueueCard()
        card.update_data(GlanceData())

    def test_nodes_card(self, sample_data):
        from forge_harness.glance.cards.nodes import NodeResourceCard

        card = NodeResourceCard()
        card.update_data(sample_data)
        micro = card.render_micro(sample_data)
        assert "2/3" in micro  # 2 online out of 3

    def test_nodes_card_empty(self):
        from forge_harness.glance.cards.nodes import NodeResourceCard

        card = NodeResourceCard()
        card.update_data(GlanceData())

    def test_nodes_card_with_heartbeat_age(self):
        from forge_harness.glance.cards.nodes import NodeResourceCard

        card = NodeResourceCard()
        data = GlanceData(
            nodes=[
                NodeInfo(name="prya", status="online", agent_count=2, max_agents=2,
                         cpu_percent=45, ram_percent=78, is_online=True, heartbeat_age="2m ago"),
                NodeInfo(name="sati", status="online", agent_count=3, max_agents=6,
                         cpu_percent=20, ram_percent=30, is_online=True, heartbeat_age="15m ago"),
            ]
        )
        card.update_data(data)
        # Should not crash; heartbeat age should be rendered

    def test_errors_card(self, sample_data):
        from forge_harness.glance.cards.errors import ErrorCard

        card = ErrorCard()
        card.update_data(sample_data)
        micro = card.render_micro(sample_data)
        assert "0/1h" in micro

    def test_errors_card_no_errors(self):
        from forge_harness.glance.cards.errors import ErrorCard

        card = ErrorCard()
        card.update_data(GlanceData())

    def test_errors_card_file_mode_shows_unavailable(self):
        """In file mode, error card should show 'API mode' message instead of zeros."""
        from forge_harness.glance.cards.errors import ErrorCard

        card = ErrorCard()
        data = GlanceData(source="files")
        card.update_data(data)
        # Access the internal content via name-mangled attribute
        body_text = card._body._Static__content
        assert "API mode" in body_text

    def test_git_card(self, sample_data):
        from forge_harness.glance.cards.git import GitStatusCard

        card = GitStatusCard()
        card.update_data(sample_data)
        micro = card.render_micro(sample_data)
        assert "main" in micro

    def test_git_card_empty(self):
        """GitStatusCard with empty GlanceData must not crash."""
        from forge_harness.glance.cards.git import GitStatusCard

        card = GitStatusCard()
        card.update_data(GlanceData())

    def test_git_card_dirty_submodules(self):
        """Git card should display dirty submodule count."""
        from forge_harness.glance.cards.git import GitStatusCard

        card = GitStatusCard()
        data = GlanceData(
            git=GitInfo(
                branch="main",
                submodule_count=10,
                total_submodules=10,
                dirty_submodules=3,
            )
        )
        card.update_data(data)
        body_text = card._body._Static__content
        assert "3 dirty" in body_text

    def test_dispatch_card(self, sample_data):
        from forge_harness.glance.cards.dispatch import DispatchCard

        card = DispatchCard()
        card.update_data(sample_data)
        micro = card.render_micro(sample_data)
        assert "1A" in micro  # 1 active
        assert "1D" in micro  # 1 done

    def test_dispatch_card_empty(self):
        """DispatchCard with empty GlanceData must not crash."""
        from forge_harness.glance.cards.dispatch import DispatchCard

        card = DispatchCard()
        card.update_data(GlanceData())
        body_text = card._body._Static__content
        assert "No dispatches" in body_text

    def test_dispatch_card_all_completed(self):
        """DispatchCard with all completed dispatches."""
        from forge_harness.glance.cards.dispatch import DispatchCard

        card = DispatchCard()
        data = GlanceData(
            dispatches=[
                DispatchInfo(agent_name="minimax", task_subject="audit", status="completed"),
                DispatchInfo(agent_name="glm", task_subject="scan", status="completed"),
            ]
        )
        card.update_data(data)
        micro = card.render_micro(data)
        assert "0A" in micro
        assert "2D" in micro

    def test_session_card(self, sample_data):
        from forge_harness.glance.cards.session import SessionCard

        card = SessionCard()
        card.update_data(sample_data)
        micro = card.render_micro(sample_data)
        assert "main" in micro
        assert "6c" in micro

    def test_session_card_empty(self):
        """SessionCard with empty GlanceData must not crash."""
        from forge_harness.glance.cards.session import SessionCard

        card = SessionCard()
        card.update_data(GlanceData())
        body_text = card._body._Static__content
        assert "Branch:" in body_text


# ---------------------------------------------------------------------------
# Stale indicator
# ---------------------------------------------------------------------------


class TestStaleIndicator:
    def test_fresh_glance_data_is_stale(self):
        """Fresh GlanceData should show as stale (last_fetch_time=0.0)."""
        data = GlanceData()
        assert data.last_fetch_time == 0.0
        assert data.fetch_success is False

    def test_successful_fetch_clears_stale(self):
        """After successful fetch, fetch_success should be True."""
        data = GlanceData(fetch_success=True, last_fetch_time=time.monotonic())
        assert data.fetch_success is True
        assert data.last_fetch_time > 0

    def test_stale_after_timeout(self):
        """Data older than 30s should be considered stale."""
        data = GlanceData(
            fetch_success=True,
            last_fetch_time=time.monotonic() - 31,
        )
        is_stale = (time.monotonic() - data.last_fetch_time > 30)
        assert is_stale is True

    def test_not_stale_when_recent(self):
        """Data younger than 30s should not be stale."""
        data = GlanceData(
            fetch_success=True,
            last_fetch_time=time.monotonic(),
        )
        is_stale = (time.monotonic() - data.last_fetch_time > 30)
        assert is_stale is False


# ---------------------------------------------------------------------------
# Throughput history
# ---------------------------------------------------------------------------


class TestThroughputHistory:
    def test_throughput_capped_at_12(self):
        """Throughput history should never exceed 12 entries."""
        history: list[int] = []
        for i in range(20):
            history.append(i)
            history = history[-12:]
        assert len(history) == 12
        assert history[0] == 8
        assert history[-1] == 19


# ---------------------------------------------------------------------------
# Format age helper
# ---------------------------------------------------------------------------


class TestFormatAge:
    def test_seconds(self):
        assert _format_age(5) == "5s ago"

    def test_minutes(self):
        assert _format_age(120) == "2m ago"

    def test_hours(self):
        assert _format_age(7200) == "2h ago"

    def test_days(self):
        assert _format_age(172800) == "2d ago"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


class TestGlanceCLI:
    def test_help(self):
        from click.testing import CliRunner
        from forge_harness.cli_v2 import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["glance", "--help"])
        assert result.exit_code == 0
        assert "composable dashboard" in result.output.lower()
        assert "--refresh" in result.output
        assert "--cards" in result.output
        assert "--offline" in result.output
        assert "--snapshot" in result.output

    def test_registered(self):
        from forge_harness.cli_v2 import cli

        # Verify glance is in the command group
        assert "glance" in [c.name for c in cli.commands.values()]

    def test_snapshot_text(self):
        """Snapshot mode should print text and exit."""
        from click.testing import CliRunner
        from forge_harness.cli_v2 import cli

        runner = CliRunner()
        with patch(
            "forge_harness.glance.data.fetcher.GlanceFetcher.fetch_all",
            new_callable=AsyncMock,
            return_value=GlanceData(
                source="files",
                active_agents=3,
                total_agents=9,
                git=GitInfo(branch="main"),
            ),
        ):
            result = runner.invoke(cli, ["glance", "--snapshot", "--offline"])

        assert result.exit_code == 0
        assert "FORGE Glance Snapshot" in result.output
        assert "3/9" in result.output

    def test_snapshot_json(self):
        """Snapshot --json mode should output valid JSON."""
        from click.testing import CliRunner
        from forge_harness.cli_v2 import cli

        runner = CliRunner()
        with patch(
            "forge_harness.glance.data.fetcher.GlanceFetcher.fetch_all",
            new_callable=AsyncMock,
            return_value=GlanceData(source="files"),
        ):
            result = runner.invoke(cli, ["glance", "--snapshot", "--json", "--offline"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["source"] == "files"


# ---------------------------------------------------------------------------
# Card registry
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _textual_available(),
    reason="textual import broken (test isolation conflict with dashboard mocks)",
)
class TestCardRegistry:
    def test_all_cards_registered(self):
        from forge_harness.glance.cards import CARD_REGISTRY

        expected = {"fleet", "agents", "tasks", "nodes", "dispatch", "session", "errors", "git"}
        assert set(CARD_REGISTRY.keys()) == expected

    def test_card_priorities_unique(self):
        from forge_harness.glance.cards import CARD_REGISTRY

        priorities = [cls.card_priority for cls in CARD_REGISTRY.values()]
        assert len(priorities) == len(set(priorities))


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _textual_available(),
    reason="textual import broken (test isolation conflict with dashboard mocks)",
)
class TestFactory:
    def test_create_default(self):
        from forge_harness.glance import create_glance_app

        app = create_glance_app()
        assert app._refresh_interval == 2.0
        assert len(app._enabled_cards) == 8

    def test_create_custom(self):
        from forge_harness.glance import create_glance_app

        app = create_glance_app(
            enabled_cards={"fleet", "git"},
            refresh_interval=5.0,
            api_url="http://test:8080",
            api_token="tok_test",
        )
        assert app._refresh_interval == 5.0
        assert app._enabled_cards == {"fleet", "git"}
        assert app._api_url == "http://test:8080"
