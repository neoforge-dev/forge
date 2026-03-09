"""
Tests for Quality API endpoints.

Tests:
- GET /api/quality - List quality metrics for all projects
- GET /api/quality/{project} - Get specific project quality metrics
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestQualityRouter:
    """Test that quality router is properly configured."""

    def test_quality_router_imports(self):
        """Test that quality router can be imported."""
        from forge_harness.webhook_server.api.quality import router
        assert router is not None

    def test_list_quality_endpoint_exists(self):
        """Test that /api/quality endpoint exists."""
        from forge_harness.webhook_server.api.quality import router
        routes = [r.path for r in router.routes]
        assert "/api/quality" in routes

    def test_get_project_quality_endpoint_exists(self):
        """Test that /api/quality/{project} endpoint exists."""
        from forge_harness.webhook_server.api.quality import router
        routes = [r.path for r in router.routes]
        assert any("/api/quality/{project}" in r for r in routes)


class TestQualityAPIResponseFormat:
    """Test API response format."""

    def test_api_response_format(self):
        """Test api_response helper produces correct format."""
        from forge_harness.webhook_server.api.quality import api_response

        result = api_response({"test": "data"})
        assert result["success"] is True
        assert result["data"] == {"test": "data"}
        assert result["error"] is None
        assert "timestamp" in result

    def test_api_response_format_with_error(self):
        """Test api_response helper with error."""
        from forge_harness.webhook_server.api.quality import api_response

        result = api_response(None, {"code": "test_error", "message": "Test error"})
        assert result["success"] is False
        assert result["data"] is None
        assert result["error"] == {"code": "test_error", "message": "Test error"}
        assert "timestamp" in result


class TestQualityScorecard:
    """Test quality scorecard data structure."""

    def test_scorecard_has_expected_projects(self):
        """Test that scorecard contains expected projects."""
        from forge_harness.webhook_server.api.quality import QUALITY_SCORECARD

        assert "voice-coach" in QUALITY_SCORECARD
        assert "interview-simulator" in QUALITY_SCORECARD
        assert "adguild-platform" in QUALITY_SCORECARD
        assert "skillswiftr" in QUALITY_SCORECARD

    def test_scorecard_project_structure(self):
        """Test that each project has required fields."""
        from forge_harness.webhook_server.api.quality import QUALITY_SCORECARD

        for project, metrics in QUALITY_SCORECARD.items():
            assert "domain" in metrics
            assert "ruff" in metrics
            assert "mypy" in metrics
            # tests and coverage may be None for some projects
            assert "tests" in metrics or metrics.get("tests") is None
            assert "coverage" in metrics or metrics.get("coverage") is None


class TestHelperFunctions:
    """Test helper functions."""

    def test_load_cache_with_no_file(self):
        """Test _load_cache when cache file doesn't exist."""
        from forge_harness.webhook_server.api.quality import _load_cache

        with patch("forge_harness.webhook_server.api.quality.QUALITY_CACHE") as mock_cache:
            mock_cache.exists.return_value = False
            result = _load_cache()
            assert result == {}

    def test_load_cache_with_valid_json(self):
        """Test _load_cache with valid JSON."""
        from forge_harness.webhook_server.api.quality import _load_cache

        test_data = {"voice-coach": {"tests": 1200, "coverage": 92}}

        with patch("forge_harness.webhook_server.api.quality.QUALITY_CACHE") as mock_cache:
            mock_path = Mock()
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps(test_data)
            mock_cache.exists = mock_path.exists
            mock_cache.read_text = mock_path.read_text

            result = _load_cache()
            assert result == test_data

    def test_merge_with_scorecard_no_cache(self):
        """Test _merge_with_scorecard with empty cache."""
        from forge_harness.webhook_server.api.quality import _merge_with_scorecard

        result = _merge_with_scorecard({})
        assert len(result) > 0
        assert all("project" in p for p in result)
        assert all("source" in p for p in result)
        assert all(p["source"] == "scorecard" for p in result)

    def test_merge_with_scorecard_with_cache(self):
        """Test _merge_with_scorecard with cache data."""
        from forge_harness.webhook_server.api.quality import _merge_with_scorecard

        cache = {
            "voice-coach": {"tests": 1200, "coverage": 92, "last_updated": "2026-02-16"}
        }
        result = _merge_with_scorecard(cache)

        vc_project = next((p for p in result if p["project"] == "voice-coach"), None)
        assert vc_project is not None
        assert vc_project["tests"] == 1200
        assert vc_project["coverage"] == 92
        assert vc_project["source"] == "cache"


class TestQualityEndpoints:
    """Test quality API endpoints."""

    @pytest.mark.asyncio
    async def test_get_quality_metrics_structure(self):
        """Test get_quality_metrics returns correct structure."""
        from forge_harness.webhook_server.api.quality import get_quality_metrics

        # Mock the auth dependency
        result = await get_quality_metrics(_=None)

        # Result should be a JSONResponse
        assert hasattr(result, "body")
        content = json.loads(result.body)

        assert "success" in content
        assert "data" in content
        assert "timestamp" in content

        data = content["data"]
        assert "projects" in data
        assert "summary" in data

        summary = data["summary"]
        assert "total_projects" in summary
        assert "total_tests" in summary
        assert "average_coverage" in summary
        assert "total_ruff_errors" in summary
        assert "total_mypy_errors" in summary
        assert "projects_at_zero_lint" in summary

    @pytest.mark.asyncio
    async def test_get_project_quality_success(self):
        """Test get_project_quality for existing project."""
        from forge_harness.webhook_server.api.quality import get_project_quality

        result = await get_project_quality("voice-coach", _=None)

        assert hasattr(result, "body")
        content = json.loads(result.body)

        assert content["success"] is True
        assert content["data"]["project"] == "voice-coach"
        assert content["data"]["domain"] == "brandfocus-ai"
        assert "tests" in content["data"]
        assert "coverage" in content["data"]
        assert "ruff_errors" in content["data"]
        assert "mypy_errors" in content["data"]

    @pytest.mark.asyncio
    async def test_get_project_quality_not_found(self):
        """Test get_project_quality for non-existent project."""
        from forge_harness.webhook_server.api.quality import get_project_quality

        result = await get_project_quality("non-existent-project", _=None)

        assert hasattr(result, "status_code")
        assert result.status_code == 404

        content = json.loads(result.body)
        assert content["success"] is False
        assert content["error"]["code"] == "not_found"


class TestQualitySummaryStats:
    """Test quality summary statistics calculation."""

    @pytest.mark.asyncio
    async def test_summary_calculations(self):
        """Test that summary stats are calculated correctly."""
        from forge_harness.webhook_server.api.quality import QUALITY_SCORECARD, get_quality_metrics

        result = await get_quality_metrics(_=None)
        content = json.loads(result.body)
        summary = content["data"]["summary"]

        # Verify total projects count
        assert summary["total_projects"] == len(QUALITY_SCORECARD)

        # Verify total tests is positive
        assert summary["total_tests"] > 0

        # Verify average coverage is reasonable (0-100 or None)
        if summary["average_coverage"] is not None:
            assert 0 <= summary["average_coverage"] <= 100

        # Verify total errors are non-negative
        assert summary["total_ruff_errors"] >= 0
        assert summary["total_mypy_errors"] >= 0

        # Verify projects at zero lint
        assert 0 <= summary["projects_at_zero_lint"] <= summary["total_projects"]
