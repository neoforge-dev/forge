"""
CC-P2-010: Orchestration Page Pipeline Status Integration Test
================================================================

Tests that the Orchestration page correctly loads pipeline data from
checkpoint files.
"""

import json

import pytest


@pytest.fixture
def setup_checkpoint_dirs(tmp_path):
    """Create checkpoint directories with sample data."""
    forge_root = tmp_path / "forge"
    forge_root.mkdir()

    orchestration_dir = forge_root / ".forge/orchestration_checkpoints"
    ralph_dir = forge_root / ".forge/ralph_checkpoints"
    orchestration_dir.mkdir()
    ralph_dir.mkdir()

    # Create orchestration checkpoint
    orchestration_file = orchestration_dir / "content_publish_20260205.json"
    orchestration_data = {
        "pipeline_name": "content_publish",
        "pipeline_steps": [
            {"name": "validate", "description": "Validate content"},
            {"name": "publish", "description": "Publish to platform"},
            {"name": "notify", "description": "Send notifications"},
        ],
        "context": {
            "domain": "codeswiftr-com",
            "project": "interview-simulator",
        },
        "step_outputs": {"validate": {"valid": True}},
        "completed_steps": ["validate"],
        "current_step_index": 1,
        "created_at": "2026-02-05T10:00:00Z",
        "human_gates": {},
    }
    with open(orchestration_file, "w") as f:
        json.dump(orchestration_data, f)

    # Create Ralph loop checkpoint
    ralph_file = ralph_dir / "ralph_loop_20260205.json"
    ralph_data = {
        "iteration": 10,
        "timestamp": "2026-02-05T11:00:00Z",
        "features_path": "brandfocus-ai/voice-coach/features.json",
        "config": {
            "max_iterations": 100,
            "checkpoint_interval": 5,
        },
        "stats": {
            "passing": 5,
            "failing": 0,
            "blocked": 0,
            "pending": 3,
            "in_progress": 1,
        },
    }
    with open(ralph_file, "w") as f:
        json.dump(ralph_data, f)

    return {
        "root": forge_root,
        "orchestration": orchestration_dir,
        "ralph": ralph_dir,
    }


def test_pipeline_data_reader(setup_checkpoint_dirs):
    """Test PipelineDataReader reads checkpoint files correctly."""
    from forge_harness.pipeline_data import PipelineDataReader

    reader = PipelineDataReader(setup_checkpoint_dirs["root"])
    pipelines = reader.get_recent_pipelines(limit=10)

    assert len(pipelines) == 2

    # Check orchestration pipeline
    orch = [p for p in pipelines if p.type == "orchestration"][0]
    assert orch.name == "content_publish"
    assert orch.status == "running"
    assert orch.current_step == 1
    assert orch.total_steps == 3
    assert orch.domain == "codeswiftr-com"
    assert orch.project == "interview-simulator"

    # Check Ralph loop pipeline
    ralph = [p for p in pipelines if p.type == "ralph_loop"][0]
    assert "Ralph Loop" in ralph.name
    assert ralph.status == "running"
    assert ralph.current_step == 10
    assert ralph.total_steps == 100


def test_pipeline_api_endpoint(setup_checkpoint_dirs, monkeypatch):
    """Test /api/pipelines endpoint returns correct data."""
    from fastapi.testclient import TestClient

    # Mock Path.cwd() to return test directory
    monkeypatch.setattr("pathlib.Path.cwd", lambda: setup_checkpoint_dirs["root"])

    from forge_harness.webhook_server import AuthConfig, create_app

    auth_config = AuthConfig(bearer_token="test-token", require_auth=True)
    app = create_app(auth_config=auth_config)
    client = TestClient(app)

    response = client.get(
        "/api/pipelines",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["count"] == 2

    pipelines = data["data"]["pipelines"]
    assert len(pipelines) == 2

    # Verify response structure matches frontend Pipeline type
    for pipeline in pipelines:
        assert "id" in pipeline
        assert "name" in pipeline
        assert "type" in pipeline
        assert pipeline["type"] in ["orchestration", "ralph_loop"]
        assert "status" in pipeline
        assert pipeline["status"] in ["pending", "running", "success", "failed", "paused"]
        assert "current_step" in pipeline
        assert "total_steps" in pipeline
        assert "domain" in pipeline
        assert "project" in pipeline
        assert "started_at" in pipeline
        assert "completed_at" in pipeline
        assert "updated_at" in pipeline
        assert "error" in pipeline
        assert "checkpoint_path" in pipeline
        assert "metadata" in pipeline


def test_pipeline_stats_endpoint(setup_checkpoint_dirs, monkeypatch):
    """Test /api/pipelines/stats endpoint."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr("pathlib.Path.cwd", lambda: setup_checkpoint_dirs["root"])

    from forge_harness.webhook_server import AuthConfig, create_app

    auth_config = AuthConfig(bearer_token="test-token", require_auth=True)
    app = create_app(auth_config=auth_config)
    client = TestClient(app)

    response = client.get(
        "/api/pipelines/stats",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["total"] == 2
    assert data["data"]["by_type"]["orchestration"] == 1
    assert data["data"]["by_type"]["ralph_loop"] == 1
    assert data["data"]["by_status"]["running"] == 2


def test_pipeline_filter_by_type(setup_checkpoint_dirs, monkeypatch):
    """Test filtering pipelines by type."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr("pathlib.Path.cwd", lambda: setup_checkpoint_dirs["root"])

    from forge_harness.webhook_server import AuthConfig, create_app

    auth_config = AuthConfig(bearer_token="test-token", require_auth=True)
    app = create_app(auth_config=auth_config)
    client = TestClient(app)

    # Filter by orchestration
    response = client.get(
        "/api/pipelines?type=orchestration",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["count"] == 1
    assert data["data"]["pipelines"][0]["type"] == "orchestration"

    # Filter by ralph_loop
    response = client.get(
        "/api/pipelines?type=ralph_loop",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["count"] == 1
    assert data["data"]["pipelines"][0]["type"] == "ralph_loop"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
