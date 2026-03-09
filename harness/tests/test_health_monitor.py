"""Tests for the FORGE Agent Health Monitor."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from forge_harness.health_monitor import AgentHealth, FleetHealth, HealthMonitor, HealthStatus


class TestHealthStatus:
    """Test HealthStatus enum."""

    def test_status_values(self):
        assert HealthStatus.HEALTHY == "HEALTHY"
        assert HealthStatus.EXHAUSTED == "EXHAUSTED"
        assert HealthStatus.WARNING == "WARNING"
        assert HealthStatus.FAILED == "FAILED"
        assert HealthStatus.IDLE == "IDLE"
        assert HealthStatus.UNKNOWN == "UNKNOWN"

    def test_status_from_string(self):
        assert HealthStatus("HEALTHY") == HealthStatus.HEALTHY
        assert HealthStatus("EXHAUSTED") == HealthStatus.EXHAUSTED


class TestAgentHealth:
    """Test AgentHealth dataclass."""

    def test_to_dict(self):
        health = AgentHealth(
            agent="forge:claude",
            status=HealthStatus.HEALTHY,
            context_pct=75,
            activity="active",
            can_dispatch=True,
        )
        d = health.to_dict()
        assert d["agent"] == "forge:claude"
        assert d["status"] == "HEALTHY"
        assert d["context_pct"] == 75
        assert d["can_dispatch"] is True

    def test_to_json(self):
        health = AgentHealth(
            agent="forge:claude",
            status=HealthStatus.EXHAUSTED,
            context_pct=10,
            activity="idle",
            can_dispatch=False,
        )
        parsed = json.loads(health.to_json())
        assert parsed["status"] == "EXHAUSTED"
        assert parsed["can_dispatch"] is False

    def test_unknown_context(self):
        health = AgentHealth(
            agent="forge:test",
            status=HealthStatus.UNKNOWN,
            context_pct=-1,
            activity="unknown",
            can_dispatch=True,
        )
        assert health.context_pct == -1


class TestFleetHealth:
    """Test FleetHealth dataclass."""

    def test_empty_fleet(self):
        fleet = FleetHealth(agents=[])
        d = fleet.to_dict()
        assert d["agents"] == []
        assert d["summary"]["total"] == 0

    def test_fleet_summary(self):
        agents = [
            AgentHealth("forge:a", HealthStatus.HEALTHY, 80, "active", True),
            AgentHealth("forge:b", HealthStatus.EXHAUSTED, 10, "idle", False),
            AgentHealth("forge:c", HealthStatus.WARNING, 35, "idle", False),
        ]
        fleet = FleetHealth(agents=agents, total=3, healthy=1, exhausted=1, warning=1, failed=0)
        d = fleet.to_dict()
        assert d["summary"]["total"] == 3
        assert d["summary"]["exhausted"] == 1
        assert len(d["agents"]) == 3


class TestHealthMonitor:
    """Test HealthMonitor with mocked subprocess calls."""

    def test_check_healthy_agent(self, tmp_path):
        script = tmp_path / ".forge" / "scripts" / "check-agent-health.sh"
        script.parent.mkdir(parents=True)
        script.touch()
        script.chmod(0o755)

        monitor = HealthMonitor(forge_root=tmp_path)

        mock_output = json.dumps(
            {
                "agent": "forge:claude",
                "status": "HEALTHY",
                "context_pct": 75,
                "activity": "active",
                "can_dispatch": True,
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = mock_output
            result = monitor.check("forge:claude")

        assert result.status == HealthStatus.HEALTHY
        assert result.context_pct == 75
        assert result.can_dispatch is True

    def test_check_exhausted_agent(self, tmp_path):
        script = tmp_path / ".forge" / "scripts" / "check-agent-health.sh"
        script.parent.mkdir(parents=True)
        script.touch()
        script.chmod(0o755)

        monitor = HealthMonitor(forge_root=tmp_path)

        mock_output = json.dumps(
            {
                "agent": "forge:claude",
                "status": "EXHAUSTED",
                "context_pct": 10,
                "activity": "idle",
                "can_dispatch": False,
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = mock_output
            result = monitor.check("forge:claude")

        assert result.status == HealthStatus.EXHAUSTED
        assert result.can_dispatch is False

    def test_should_dispatch_healthy(self, tmp_path):
        script = tmp_path / ".forge" / "scripts" / "check-agent-health.sh"
        script.parent.mkdir(parents=True)
        script.touch()
        script.chmod(0o755)

        monitor = HealthMonitor(forge_root=tmp_path)

        mock_output = json.dumps(
            {
                "agent": "forge:claude",
                "status": "HEALTHY",
                "context_pct": 75,
                "activity": "active",
                "can_dispatch": True,
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = mock_output
            assert monitor.should_dispatch("forge:claude") is True

    def test_should_not_dispatch_exhausted(self, tmp_path):
        script = tmp_path / ".forge" / "scripts" / "check-agent-health.sh"
        script.parent.mkdir(parents=True)
        script.touch()
        script.chmod(0o755)

        monitor = HealthMonitor(forge_root=tmp_path)

        mock_output = json.dumps(
            {
                "agent": "forge:claude",
                "status": "EXHAUSTED",
                "context_pct": 10,
                "activity": "idle",
                "can_dispatch": False,
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = mock_output
            assert monitor.should_dispatch("forge:claude") is False

    def test_missing_script_returns_unknown(self, tmp_path):
        monitor = HealthMonitor(forge_root=tmp_path)
        result = monitor.check("forge:claude")
        assert result.status == HealthStatus.UNKNOWN
        assert result.can_dispatch is True  # fail-open: unknown means allow

    def test_check_all_agents(self, tmp_path):
        script = tmp_path / ".forge" / "scripts" / "check-agent-health.sh"
        script.parent.mkdir(parents=True)
        script.touch()
        script.chmod(0o755)

        monitor = HealthMonitor(forge_root=tmp_path)

        mock_output = json.dumps(
            {
                "agents": [
                    {
                        "agent": "forge:claude",
                        "status": "HEALTHY",
                        "context_pct": 75,
                        "activity": "active",
                        "can_dispatch": True,
                    },
                    {
                        "agent": "forge:kimi",
                        "status": "EXHAUSTED",
                        "context_pct": 8,
                        "activity": "idle",
                        "can_dispatch": False,
                    },
                ],
                "summary": {"total": 2, "healthy": 1, "exhausted": 1, "warning": 0, "failed": 0},
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = mock_output
            fleet = monitor.check_all()

        assert fleet.total == 2
        assert fleet.healthy == 1
        assert fleet.exhausted == 1
        assert len(fleet.agents) == 2
        assert fleet.agents[0].status == HealthStatus.HEALTHY
        assert fleet.agents[1].status == HealthStatus.EXHAUSTED
