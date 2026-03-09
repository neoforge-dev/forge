"""Simple integration test for priority dispatch."""

from __future__ import annotations

from forge_harness.fleet.priority_dispatch import (
    AGENT_CAPABILITIES,
    AgentStatus,
    match_task_to_agent,
)
from forge_harness.iteration.prioritizer import PrioritizedTask


def test_agent_capabilities():
    """Test that agent capabilities are properly defined."""
    expected_agents = ["tech", "qa", "game", "codex", "gemini", "kimi"]

    for agent in expected_agents:
        assert agent in AGENT_CAPABILITIES
        assert "domains" in AGENT_CAPABILITIES[agent]
        assert "keywords" in AGENT_CAPABILITIES[agent]


def test_task_matching():
    """Test basic task matching."""
    task = PrioritizedTask(
        feature_id="TEST-001",
        title="Backend API implementation",
        score=8.0,
        impact=9.0,
        urgency=7.0,
        effort=3.0,
        domain_weight=1.5,
        reasoning="Test",
        metadata={"domain": "neoforge-dev", "priority": "high"},
    )

    agent = AgentStatus(
        name="tech",
        is_idle=True,
        last_output="Ready",
        capabilities=AGENT_CAPABILITIES["tech"],
    )

    matched = match_task_to_agent(task, [agent])
    assert matched is not None
    assert matched.name == "tech"
