"""
Tests for Pipeline API Endpoints
================================

Tests the pipeline API endpoints in webhook_server.py
that read checkpoint data from disk.
"""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def checkpoint_dirs(tmp_path):
    """Create temporary checkpoint directories."""
    orchestration_dir = tmp_path / ".forge/orchestration_checkpoints"
    ralph_dir = tmp_path / ".forge/ralph_checkpoints"
    orchestration_dir.mkdir()
    ralph_dir.mkdir()

    return {
        "root": tmp_path,
        "orchestration": orchestration_dir,
        "ralph": ralph_dir,
    }


@pytest.fixture
def sample_orchestration_checkpoint(checkpoint_dirs):
    """Create a sample orchestration checkpoint file."""
    checkpoint_file = checkpoint_dirs["orchestration"] / "content_publish_20260127_120000.json"

    checkpoint_data = {
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
        "step_outputs": {
            "validate": {"valid": True},
        },
        "completed_steps": ["validate"],
        "current_step_index": 1,
        "created_at": "2026-01-27T12:00:00Z",
        "human_gates": {},
    }

    with open(checkpoint_file, "w") as f:
        json.dump(checkpoint_data, f)

    return checkpoint_file


@pytest.fixture
def sample_ralph_checkpoint(checkpoint_dirs):
    """Create a sample Ralph loop checkpoint file."""
    checkpoint_file = checkpoint_dirs["ralph"] / "checkpoint_20260127_130000.json"

    checkpoint_data = {
        "iteration": 5,
        "timestamp": "2026-01-27T13:00:00Z",
        "features_path": "codeswiftr-com/interview-simulator/features.json",
        "config": {
            "max_iterations": 100,
            "checkpoint_interval": 5,
            "timeout_seconds": 300,
        },
        "stats": {
            "passing": 3,
            "failing": 1,
            "blocked": 0,
            "pending": 2,
            "in_progress": 1,
        },
    }

    with open(checkpoint_file, "w") as f:
        json.dump(checkpoint_data, f)

    return checkpoint_file


@pytest.fixture
def app_client(checkpoint_dirs, monkeypatch):
    """Create FastAPI test client with mocked checkpoint directories."""
    # Mock Path.cwd() to return our test directory
    monkeypatch.setattr("pathlib.Path.cwd", lambda: checkpoint_dirs["root"])

    from forge_harness.webhook_server import AuthConfig, create_app

    auth_config = AuthConfig(
        bearer_token="test-token",
        require_auth=True,
    )
    app = create_app(auth_config=auth_config)
    return TestClient(app)


class TestPipelinesAPI:
    """Tests for /api/pipelines endpoints."""

    def test_list_pipelines_empty(self, app_client):
        """Test listing pipelines when no checkpoints exist."""
        response = app_client.get(
            "/api/pipelines",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 0
        assert data["data"]["pipelines"] == []

    def test_list_pipelines_with_orchestration(self, app_client, sample_orchestration_checkpoint):
        """Test listing pipelines with orchestration checkpoint."""
        response = app_client.get(
            "/api/pipelines",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1

        pipeline = data["data"]["pipelines"][0]
        assert pipeline["name"] == "content_publish"
        assert pipeline["type"] == "orchestration"
        assert pipeline["status"] == "running"
        assert pipeline["current_step"] == 1
        assert pipeline["total_steps"] == 3
        assert pipeline["domain"] == "codeswiftr-com"
        assert pipeline["project"] == "interview-simulator"

    def test_list_pipelines_with_ralph(self, app_client, sample_ralph_checkpoint):
        """Test listing pipelines with Ralph checkpoint."""
        response = app_client.get(
            "/api/pipelines",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1

        pipeline = data["data"]["pipelines"][0]
        assert "Ralph Loop" in pipeline["name"]
        assert pipeline["type"] == "ralph_loop"
        assert pipeline["status"] == "failed"  # Has failing features
        assert pipeline["current_step"] == 5
        assert pipeline["total_steps"] == 100

    def test_list_pipelines_with_both_types(
        self,
        app_client,
        sample_orchestration_checkpoint,
        sample_ralph_checkpoint,
    ):
        """Test listing pipelines with both checkpoint types."""
        response = app_client.get(
            "/api/pipelines",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 2

        types = {p["type"] for p in data["data"]["pipelines"]}
        assert types == {"orchestration", "ralph_loop"}

    def test_list_pipelines_filter_by_status(
        self,
        app_client,
        sample_orchestration_checkpoint,
        sample_ralph_checkpoint,
    ):
        """Test filtering pipelines by status."""
        response = app_client.get(
            "/api/pipelines?status=running",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1
        assert data["data"]["pipelines"][0]["status"] == "running"

    def test_list_pipelines_filter_by_type(
        self,
        app_client,
        sample_orchestration_checkpoint,
        sample_ralph_checkpoint,
    ):
        """Test filtering pipelines by type."""
        response = app_client.get(
            "/api/pipelines?type=orchestration",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1
        assert data["data"]["pipelines"][0]["type"] == "orchestration"

    def test_list_pipelines_with_limit(
        self,
        app_client,
        checkpoint_dirs,
    ):
        """Test limiting number of pipelines returned."""
        # Create multiple checkpoints
        for i in range(5):
            checkpoint_file = checkpoint_dirs["orchestration"] / f"pipeline_{i}.json"
            checkpoint_data = {
                "pipeline_name": f"pipeline_{i}",
                "pipeline_steps": [],
                "context": {},
                "step_outputs": {},
                "completed_steps": [],
                "current_step_index": 0,
                "created_at": datetime.now(UTC).isoformat(),
                "human_gates": {},
            }
            with open(checkpoint_file, "w") as f:
                json.dump(checkpoint_data, f)

        response = app_client.get(
            "/api/pipelines?limit=3",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 3

    def test_list_pipelines_unauthorized(self, app_client):
        """Test listing pipelines without authentication."""
        response = app_client.get("/api/pipelines")

        assert response.status_code == 401

    def test_recent_pipelines(self, app_client, sample_orchestration_checkpoint):
        """Test getting recent pipelines."""
        response = app_client.get(
            "/api/pipelines/recent",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1
        assert data["data"]["pipelines"][0]["name"] == "content_publish"

    def test_recent_pipelines_with_type_filter(
        self,
        app_client,
        sample_orchestration_checkpoint,
        sample_ralph_checkpoint,
    ):
        """Test filtering recent pipelines by type."""
        response = app_client.get(
            "/api/pipelines/recent?type=ralph_loop",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1
        assert data["data"]["pipelines"][0]["type"] == "ralph_loop"

    def test_pipeline_stats_empty(self, app_client):
        """Test getting pipeline stats when no checkpoints exist."""
        response = app_client.get(
            "/api/pipelines/stats",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["total"] == 0
        assert data["data"]["by_status"] == {
            "pending": 0,
            "running": 0,
            "success": 0,
            "failed": 0,
            "paused": 0,
        }
        assert data["data"]["by_type"] == {
            "orchestration": 0,
            "ralph_loop": 0,
        }

    def test_pipeline_stats_with_data(
        self,
        app_client,
        sample_orchestration_checkpoint,
        sample_ralph_checkpoint,
    ):
        """Test getting pipeline stats with checkpoint data."""
        response = app_client.get(
            "/api/pipelines/stats",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["total"] == 2
        assert data["data"]["by_status"]["running"] == 1
        assert data["data"]["by_status"]["failed"] == 1
        assert data["data"]["by_type"]["orchestration"] == 1
        assert data["data"]["by_type"]["ralph_loop"] == 1


class TestPipelineDataReader:
    """Tests for PipelineDataReader class."""

    def test_read_empty_directories(self, checkpoint_dirs):
        """Test reading from empty checkpoint directories."""
        from forge_harness.pipeline_data import PipelineDataReader

        reader = PipelineDataReader(checkpoint_dirs["root"])
        pipelines = reader.get_recent_pipelines(limit=10)

        assert len(pipelines) == 0

    def test_read_orchestration_checkpoint(self, checkpoint_dirs, sample_orchestration_checkpoint):
        """Test reading orchestration checkpoint."""
        from forge_harness.pipeline_data import PipelineDataReader

        reader = PipelineDataReader(checkpoint_dirs["root"])
        pipelines = reader.get_recent_pipelines(limit=10)

        assert len(pipelines) == 1
        pipeline = pipelines[0]

        assert pipeline.name == "content_publish"
        assert pipeline.type == "orchestration"
        assert pipeline.status == "running"
        assert pipeline.current_step == 1
        assert pipeline.total_steps == 3
        assert pipeline.domain == "codeswiftr-com"
        assert pipeline.project == "interview-simulator"
        assert "completed_steps" in pipeline.metadata

    def test_read_ralph_checkpoint(self, checkpoint_dirs, sample_ralph_checkpoint):
        """Test reading Ralph loop checkpoint."""
        from forge_harness.pipeline_data import PipelineDataReader

        reader = PipelineDataReader(checkpoint_dirs["root"])
        pipelines = reader.get_recent_pipelines(limit=10)

        assert len(pipelines) == 1
        pipeline = pipelines[0]

        assert "Ralph Loop" in pipeline.name
        assert pipeline.type == "ralph_loop"
        assert pipeline.status == "failed"  # Has failing features
        assert pipeline.current_step == 5
        assert pipeline.total_steps == 100
        assert "stats" in pipeline.metadata
        assert pipeline.metadata["stats"]["passing"] == 3
        assert pipeline.metadata["stats"]["failing"] == 1

    def test_status_determination_orchestration(self, checkpoint_dirs):
        """Test status determination for orchestration checkpoints."""
        from forge_harness.pipeline_data import PipelineDataReader

        # Completed pipeline
        completed_file = checkpoint_dirs["orchestration"] / "completed.json"
        with open(completed_file, "w") as f:
            json.dump(
                {
                    "pipeline_name": "completed",
                    "pipeline_steps": [{"name": "step1"}],
                    "context": {},
                    "step_outputs": {},
                    "completed_steps": ["step1"],
                    "current_step_index": 1,
                    "created_at": datetime.now(UTC).isoformat(),
                    "human_gates": {},
                },
                f,
            )

        reader = PipelineDataReader(checkpoint_dirs["root"])
        pipelines = reader.get_recent_pipelines(limit=10)

        completed = [p for p in pipelines if p.name == "completed"][0]
        assert completed.status == "success"

    def test_status_determination_ralph(self, checkpoint_dirs):
        """Test status determination for Ralph checkpoints."""
        from forge_harness.pipeline_data import PipelineDataReader

        # Success case (all features passing)
        success_file = checkpoint_dirs["ralph"] / "success.json"
        with open(success_file, "w") as f:
            json.dump(
                {
                    "iteration": 10,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "features_path": "test/features.json",
                    "config": {"max_iterations": 100},
                    "stats": {
                        "passing": 5,
                        "failing": 0,
                        "blocked": 0,
                        "pending": 0,
                        "in_progress": 0,
                    },
                },
                f,
            )

        # Failed case (blocked features)
        failed_file = checkpoint_dirs["ralph"] / "failed.json"
        with open(failed_file, "w") as f:
            json.dump(
                {
                    "iteration": 10,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "features_path": "test/features.json",
                    "config": {"max_iterations": 100},
                    "stats": {
                        "passing": 2,
                        "failing": 0,
                        "blocked": 2,
                        "pending": 0,
                        "in_progress": 0,
                    },
                },
                f,
            )

        reader = PipelineDataReader(checkpoint_dirs["root"])
        pipelines = reader.get_recent_pipelines(limit=10)

        success = [p for p in pipelines if "success" in p.checkpoint_path][0]
        failed = [p for p in pipelines if "failed" in p.checkpoint_path][0]

        assert success.status == "success"
        assert failed.status == "failed"

    def test_filter_by_status(
        self, checkpoint_dirs, sample_orchestration_checkpoint, sample_ralph_checkpoint
    ):
        """Test filtering pipelines by status."""
        from forge_harness.pipeline_data import PipelineDataReader

        reader = PipelineDataReader(checkpoint_dirs["root"])

        # Get all
        all_pipelines = reader.get_recent_pipelines(limit=10)
        assert len(all_pipelines) == 2

        # Filter by running
        running = reader.get_recent_pipelines(limit=10, status="running")
        assert len(running) == 1
        assert running[0].status == "running"

        # Filter by failed
        failed = reader.get_recent_pipelines(limit=10, status="failed")
        assert len(failed) == 1
        assert failed[0].status == "failed"

    def test_filter_by_type(
        self, checkpoint_dirs, sample_orchestration_checkpoint, sample_ralph_checkpoint
    ):
        """Test filtering pipelines by type."""
        from forge_harness.pipeline_data import PipelineDataReader

        reader = PipelineDataReader(checkpoint_dirs["root"])

        # Filter by orchestration
        orchestration = reader.get_recent_pipelines(limit=10, type_filter="orchestration")
        assert len(orchestration) == 1
        assert orchestration[0].type == "orchestration"

        # Filter by ralph_loop
        ralph = reader.get_recent_pipelines(limit=10, type_filter="ralph_loop")
        assert len(ralph) == 1
        assert ralph[0].type == "ralph_loop"

    def test_sort_by_updated_at(self, checkpoint_dirs):
        """Test pipelines are sorted by most recent."""
        import time

        from forge_harness.pipeline_data import PipelineDataReader

        # Create multiple checkpoints with different timestamps
        for i in range(3):
            checkpoint_file = checkpoint_dirs["orchestration"] / f"pipeline_{i}.json"
            with open(checkpoint_file, "w") as f:
                json.dump(
                    {
                        "pipeline_name": f"pipeline_{i}",
                        "pipeline_steps": [],
                        "context": {},
                        "step_outputs": {},
                        "completed_steps": [],
                        "current_step_index": 0,
                        "created_at": datetime.now(UTC).isoformat(),
                        "human_gates": {},
                    },
                    f,
                )
            time.sleep(0.01)  # Ensure different mtimes

        reader = PipelineDataReader(checkpoint_dirs["root"])
        pipelines = reader.get_recent_pipelines(limit=10)

        assert len(pipelines) == 3
        # Most recent should be pipeline_2
        assert pipelines[0].name == "pipeline_2"
        assert pipelines[1].name == "pipeline_1"
        assert pipelines[2].name == "pipeline_0"

    def test_pipeline_stats(
        self, checkpoint_dirs, sample_orchestration_checkpoint, sample_ralph_checkpoint
    ):
        """Test getting pipeline statistics."""
        from forge_harness.pipeline_data import PipelineDataReader

        reader = PipelineDataReader(checkpoint_dirs["root"])
        stats = reader.get_pipeline_stats()

        assert stats["total"] == 2
        assert stats["by_status"]["running"] == 1
        assert stats["by_status"]["failed"] == 1
        assert stats["by_type"]["orchestration"] == 1
        assert stats["by_type"]["ralph_loop"] == 1

    def test_handle_invalid_json(self, checkpoint_dirs):
        """Test handling of invalid JSON in checkpoint files."""
        from forge_harness.pipeline_data import PipelineDataReader

        # Create invalid JSON file
        invalid_file = checkpoint_dirs["orchestration"] / "invalid.json"
        with open(invalid_file, "w") as f:
            f.write("{invalid json}")

        reader = PipelineDataReader(checkpoint_dirs["root"])
        pipelines = reader.get_recent_pipelines(limit=10)

        # Should skip invalid file and not crash
        assert len(pipelines) == 0

    def test_handle_missing_fields(self, checkpoint_dirs):
        """Test handling of checkpoint files with missing fields."""
        from forge_harness.pipeline_data import PipelineDataReader

        # Create checkpoint with minimal data
        minimal_file = checkpoint_dirs["orchestration"] / "minimal.json"
        with open(minimal_file, "w") as f:
            json.dump(
                {
                    "pipeline_name": "minimal",
                    "pipeline_steps": [],
                },
                f,
            )

        reader = PipelineDataReader(checkpoint_dirs["root"])
        pipelines = reader.get_recent_pipelines(limit=10)

        # Should handle missing fields gracefully
        assert len(pipelines) == 1
        pipeline = pipelines[0]
        assert pipeline.name == "minimal"
        assert pipeline.domain is None
        assert pipeline.project is None
