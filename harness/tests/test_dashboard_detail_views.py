"""
Tests for dashboard detail views.

These tests require REAL Rich library objects (Panel, Text, Table) rather than
mocks.  Other dashboard test modules (test_dashboard.py, test_dashboard_deep.py,
test_dashboard_extended.py) inject MagicMock objects into sys.modules for Rich at
module-load time, which contaminates the process-wide sys.modules cache.

To guarantee correct behaviour regardless of test collection order we:
1. Restore the real Rich modules in sys.modules (importlib forces a fresh import).
2. Reload dashboard_detail_views so its module-level ``from rich.panel import Panel``
   picks up the real classes.
"""

import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure real Rich modules are available before importing the code under test.
# Other test files may have replaced sys.modules entries with MagicMock.
# ---------------------------------------------------------------------------
_RICH_MODS = ["rich", "rich.console", "rich.layout", "rich.live",
              "rich.panel", "rich.table", "rich.text"]

for _mod_name in _RICH_MODS:
    _mod = sys.modules.get(_mod_name)
    if _mod is not None and type(_mod).__name__ == "MagicMock":
        del sys.modules[_mod_name]

# Force-import real Rich via importlib (avoids isort issues with bare imports)
for _mod_name in ("rich", "rich.panel", "rich.table", "rich.text"):
    importlib.import_module(_mod_name)

# Reload the module under test so it binds to the real Panel/Text/Table
importlib.import_module("forge_harness.dashboard_detail_views")
importlib.reload(sys.modules["forge_harness.dashboard_detail_views"])

from forge_harness.dashboard_core import AgentState
from forge_harness.dashboard_detail_views import (
    render_agent_detail,
    render_approval_detail,
    render_pattern_detail,
    render_pipeline_detail,
)
from forge_harness.metrics_common import ApprovalSummary, PipelineStatus


def test_render_agent_detail():
    """Test agent detail rendering."""
    agent = AgentState(
        id="forge:cursor",
        name="cursor",
        role="cursor",
        project="brandfocus-ai/voice-coach",
        task="Implement feature IS-042",
        status="active",
        progress=75,
        last_activity=datetime.now(UTC) - timedelta(minutes=5),
        is_stale=False,
    )

    panel = render_agent_detail(agent)
    assert panel is not None
    assert "cursor" in str(panel.renderable)


def test_render_approval_detail(tmp_path):
    """Test approval detail rendering."""
    # Create temp approval file
    approval_dir = tmp_path / ".forge/approvals"
    approval_dir.mkdir()

    approval_id = "test123"
    approval_data = {
        "id": approval_id,
        "type": "content",
        "domain": "codeswiftr-com",
        "title": "Review blog post",
        "description": "Please review the Python interview guide",
        "metadata": {
            "word_count": 1500,
            "quality_score": 91,
        },
        "status": "pending",
        "priority": "high",
        "tier": "PHONE",
        "risk_score": 0.3,
        "created_at": datetime.now(UTC).isoformat(),
    }

    approval_file = approval_dir / f"approval_{approval_id}.json"
    approval_file.write_text(json.dumps(approval_data))

    approval = ApprovalSummary(
        id=approval_id,
        type="content",
        title="Review blog post",
        domain="codeswiftr-com",
        priority="high",
        tier="PHONE",
        risk_score=0.3,
        created_at=datetime.now(UTC),
    )

    panel = render_approval_detail(approval, approval_dir)
    assert panel is not None
    assert "Review blog post" in str(panel.renderable)
    assert "quality_score" in str(panel.renderable)


def test_render_pipeline_detail(tmp_path):
    """Test pipeline detail rendering."""
    # Create temp checkpoint
    checkpoint_dir = tmp_path / ".forge/orchestration_checkpoints"
    checkpoint_dir.mkdir()

    pipeline_name = "test_pipeline"
    checkpoint_data = {
        "pipeline": {
            "name": pipeline_name,
            "steps": [
                {"name": "step1"},
                {"name": "step2"},
                {"name": "step3"},
            ],
        },
        "step_results": {
            "step1": {"status": "success"},
        },
        "status": "running",
        "started_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
    }

    checkpoint_file = checkpoint_dir / f"{pipeline_name}.json"
    checkpoint_file.write_text(json.dumps(checkpoint_data))

    pipeline = PipelineStatus(
        name=pipeline_name,
        current_step="step2",
        step_index=1,
        total_steps=3,
        status="running",
        started_at=datetime.now(UTC) - timedelta(minutes=10),
        error=None,
    )

    panel = render_pipeline_detail(pipeline, checkpoint_dir)
    assert panel is not None
    assert pipeline_name in str(panel.renderable)
    assert "step1" in str(panel.renderable)
    assert "step2" in str(panel.renderable)


def test_render_pattern_detail():
    """Test pattern detail rendering."""
    pattern = {
        "id": "pattern123",
        "name": "FastAPI CRUD endpoint",
        "category": "backend",
        "description": "Standard CRUD endpoint pattern for FastAPI",
        "success_rate": 0.85,
        "uses": 42,
        "successes": 36,
        "failures": 6,
        "recent_uses": [
            {
                "timestamp": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
                "outcome": "success",
                "domain": "codeswiftr-com",
            },
            {
                "timestamp": (datetime.now(UTC) - timedelta(hours=5)).isoformat(),
                "outcome": "failure",
                "domain": "brandfocus-ai",
            },
        ],
        "tags": ["backend", "fastapi", "crud"],
    }

    panel = render_pattern_detail(pattern)
    assert panel is not None
    assert "FastAPI CRUD endpoint" in str(panel.renderable)
    assert "85" in str(panel.renderable)  # Success rate
    assert "42" in str(panel.renderable)  # Uses


def test_render_agent_detail_minimal():
    """Test agent detail with minimal data."""
    agent = AgentState(
        id="test",
        name="test-agent",
        role="test-agent",
        project=None,
        task=None,
        status="idle",
        progress=0,
        last_activity=None,
        is_stale=False,
    )

    panel = render_agent_detail(agent)
    assert panel is not None
    assert "test-agent" in str(panel.renderable)


def test_render_approval_detail_missing_file(tmp_path):
    """Test approval detail when file is missing."""
    approval_dir = tmp_path / ".forge/approvals"
    approval_dir.mkdir()

    approval = ApprovalSummary(
        id="missing",
        type="content",
        title="Missing approval",
        domain="test",
        priority="normal",
        tier="PHONE",
        risk_score=0.5,
        created_at=datetime.now(UTC),
    )

    panel = render_approval_detail(approval, approval_dir)
    assert panel is not None
    assert "Unable to load full context" in str(panel.renderable)


def test_render_pipeline_detail_with_error():
    """Test pipeline detail with error state."""
    pipeline = PipelineStatus(
        name="error_pipeline",
        current_step="step2",
        step_index=1,
        total_steps=3,
        status="paused",
        started_at=datetime.now(UTC) - timedelta(minutes=5),
        error="Database connection failed",
    )

    panel = render_pipeline_detail(pipeline, Path("/nonexistent"))
    assert panel is not None
    assert "error_pipeline" in str(panel.renderable)
    assert "Database connection failed" in str(panel.renderable)


def test_render_pattern_detail_no_recent_uses():
    """Test pattern detail with no recent uses."""
    pattern = {
        "id": "pattern456",
        "name": "Test pattern",
        "category": "testing",
        "success_rate": 0.9,
        "uses": 0,
        "successes": 0,
        "failures": 0,
        "recent_uses": [],
        "tags": [],
    }

    panel = render_pattern_detail(pattern)
    assert panel is not None
    assert "Test pattern" in str(panel.renderable)
    assert "No recent uses" in str(panel.renderable)
