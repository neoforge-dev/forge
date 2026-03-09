"""Tests for forge_harness/webhook_server/api/content.py"""

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.fixture
def mock_forge_root(tmp_path):
    """Create a mock FORGE root directory structure."""
    # Create domain/project/content structure
    domain_dir = tmp_path / "test-domain"
    domain_dir.mkdir()

    project_dir = domain_dir / "test-project"
    project_dir.mkdir()

    content_dir = project_dir / "content"
    content_dir.mkdir()

    # Create some markdown files
    (content_dir / "blog-posts.md").write_text("# Blog Post\n\nContent here.\n" * 50)
    (content_dir / "social-posts.md").write_text("# Social Media\n\nPost content.\n" * 30)
    (content_dir / "case-studies.md").write_text("# Case Study\n\nDetailed analysis.\n" * 40)

    return tmp_path


class TestAPIResponse:
    """Tests for api_response helper function."""

    def test_api_response_success(self):
        """Test API response with successful data."""
        from forge_harness.webhook_server.api.content import api_response

        result = api_response({"key": "value"})

        assert result["success"] is True
        assert result["data"] == {"key": "value"}
        assert result["error"] is None
        assert "timestamp" in result

    def test_api_response_with_error(self):
        """Test API response with error."""
        from forge_harness.webhook_server.api.content import api_response

        error = {"code": "ERROR", "message": "Something went wrong"}
        result = api_response({"key": "value"}, error=error)

        assert result["success"] is False
        assert result["error"] == error


class TestCountWords:
    """Tests for _count_words helper function."""

    def test_count_words_success(self, tmp_path):
        """Test counting words in a file."""
        from forge_harness.webhook_server.api.content import _count_words

        test_file = tmp_path / "test.md"
        test_file.write_text("This is a test file with ten words in it.")

        count = _count_words(test_file)

        assert count == 10

    def test_count_words_empty_file(self, tmp_path):
        """Test counting words in empty file."""
        from forge_harness.webhook_server.api.content import _count_words

        test_file = tmp_path / "empty.md"
        test_file.write_text("")

        count = _count_words(test_file)

        assert count == 0

    def test_count_words_multiline(self, tmp_path):
        """Test counting words across multiple lines."""
        from forge_harness.webhook_server.api.content import _count_words

        test_file = tmp_path / "multiline.md"
        test_file.write_text("Line one\nLine two\nLine three")

        count = _count_words(test_file)

        assert count == 6

    def test_count_words_file_error(self, tmp_path):
        """Test error handling when file cannot be read."""
        from forge_harness.webhook_server.api.content import _count_words

        non_existent = tmp_path / "nonexistent.md"

        count = _count_words(non_existent)

        assert count == 0


class TestGetContentTypes:
    """Tests for _get_content_types helper function."""

    def test_get_content_types_blog(self, tmp_path):
        """Test identifying blog content."""
        from forge_harness.webhook_server.api.content import _get_content_types

        content_dir = tmp_path / "content"
        content_dir.mkdir()
        (content_dir / "blog-posts.md").write_text("content")

        types = _get_content_types(content_dir)

        assert "blog_posts" in types

    def test_get_content_types_social(self, tmp_path):
        """Test identifying social media content."""
        from forge_harness.webhook_server.api.content import _get_content_types

        content_dir = tmp_path / "content"
        content_dir.mkdir()
        (content_dir / "social-posts.md").write_text("content")
        (content_dir / "linkedin-posts.md").write_text("content")

        types = _get_content_types(content_dir)

        assert "social_media" in types

    def test_get_content_types_case_studies(self, tmp_path):
        """Test identifying case studies."""
        from forge_harness.webhook_server.api.content import _get_content_types

        content_dir = tmp_path / "content"
        content_dir.mkdir()
        (content_dir / "case-studies.md").write_text("content")

        types = _get_content_types(content_dir)

        assert "case_studies" in types

    def test_get_content_types_email(self, tmp_path):
        """Test identifying email sequences."""
        from forge_harness.webhook_server.api.content import _get_content_types

        content_dir = tmp_path / "content"
        content_dir.mkdir()
        (content_dir / "email-sequences.md").write_text("content")

        types = _get_content_types(content_dir)

        assert "email_sequences" in types

    def test_get_content_types_lead_magnets(self, tmp_path):
        """Test identifying lead magnets."""
        from forge_harness.webhook_server.api.content import _get_content_types

        content_dir = tmp_path / "content"
        content_dir.mkdir()
        (content_dir / "lead-magnets.md").write_text("content")

        types = _get_content_types(content_dir)

        assert "lead_magnets" in types

    def test_get_content_types_strategy(self, tmp_path):
        """Test identifying strategy docs."""
        from forge_harness.webhook_server.api.content import _get_content_types

        content_dir = tmp_path / "content"
        content_dir.mkdir()
        (content_dir / "CONTENT_STRATEGY.md").write_text("content")

        types = _get_content_types(content_dir)

        assert "strategy" in types

    def test_get_content_types_sorted(self, tmp_path):
        """Test that content types are returned sorted."""
        from forge_harness.webhook_server.api.content import _get_content_types

        content_dir = tmp_path / "content"
        content_dir.mkdir()
        (content_dir / "blog-posts.md").write_text("content")
        (content_dir / "email-sequences.md").write_text("content")
        (content_dir / "case-studies.md").write_text("content")

        types = _get_content_types(content_dir)

        assert types == sorted(types)


class TestScanContentDirectories:
    """Tests for _scan_content_directories function."""

    def test_scan_content_directories_success(self, mock_forge_root):
        """Test scanning content directories."""
        from forge_harness.webhook_server.api.content import _scan_content_directories

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", mock_forge_root):
            result = _scan_content_directories()

            assert "projects" in result
            assert "summary" in result
            assert len(result["projects"]) == 1

            project = result["projects"][0]
            assert project["domain"] == "test-domain"
            assert project["project"] == "test-project"
            assert project["has_content"] is True
            assert project["file_count"] == 3

    def test_scan_empty_forge_root(self, tmp_path):
        """Test scanning with no projects."""
        from forge_harness.webhook_server.api.content import _scan_content_directories

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", tmp_path):
            result = _scan_content_directories()

            assert len(result["projects"]) == 0
            assert result["summary"]["total_projects_with_content"] == 0

    def test_scan_skips_hidden_dirs(self, tmp_path):
        """Test that hidden directories are skipped."""
        from forge_harness.webhook_server.api.content import _scan_content_directories

        # Create hidden directory
        hidden_dir = tmp_path / ".hidden"
        hidden_dir.mkdir()
        project_dir = hidden_dir / "project"
        project_dir.mkdir()
        content_dir = project_dir / "content"
        content_dir.mkdir()
        (content_dir / "blog.md").write_text("content")

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", tmp_path):
            result = _scan_content_directories()

            assert len(result["projects"]) == 0


class TestGetContentLibraries:
    """Tests for GET /api/content endpoint."""

    @pytest.mark.asyncio
    async def test_get_content_libraries_success(self, mock_forge_root):
        """Test getting content libraries."""
        from forge_harness.webhook_server.api.content import get_content_libraries

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", mock_forge_root):
            response = await get_content_libraries(_=mock_auth)

            import json

            content = json.loads(response.body)
            assert content["success"] is True
            assert "projects" in content["data"]
            assert "summary" in content["data"]

    @pytest.mark.asyncio
    async def test_get_content_libraries_error(self):
        """Test handling error in content scan."""
        from forge_harness.webhook_server.api.content import get_content_libraries

        mock_auth = AsyncMock()

        with patch(
            "forge_harness.webhook_server.api.content._scan_content_directories",
            side_effect=Exception("Scan failed"),
        ):
            response = await get_content_libraries(_=mock_auth)

            assert response.status_code == 500
            import json

            content = json.loads(response.body)
            assert content["success"] is False


class TestGetProjectContent:
    """Tests for GET /api/content/{domain}/{project} endpoint."""

    @pytest.mark.asyncio
    async def test_get_project_content_success(self, mock_forge_root):
        """Test getting content for a specific project."""
        from forge_harness.webhook_server.api.content import get_project_content

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", mock_forge_root):
            response = await get_project_content("test-domain", "test-project", _=mock_auth)

            import json

            content = json.loads(response.body)
            assert content["success"] is True
            assert content["data"]["domain"] == "test-domain"
            assert content["data"]["project"] == "test-project"
            assert content["data"]["file_count"] == 3
            assert len(content["data"]["files"]) == 3

    @pytest.mark.asyncio
    async def test_get_project_content_not_found(self, tmp_path):
        """Test getting content for non-existent project."""
        from forge_harness.webhook_server.api.content import get_project_content

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", tmp_path):
            response = await get_project_content("nonexistent", "project", _=mock_auth)

            assert response.status_code == 404
            import json

            content = json.loads(response.body)
            assert content["success"] is False
            assert content["error"]["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_get_project_content_no_files(self, tmp_path):
        """Test getting content when directory has no markdown files."""
        from forge_harness.webhook_server.api.content import get_project_content

        # Create structure without markdown files
        domain_dir = tmp_path / "domain"
        domain_dir.mkdir()
        project_dir = domain_dir / "project"
        project_dir.mkdir()
        content_dir = project_dir / "content"
        content_dir.mkdir()

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", tmp_path):
            response = await get_project_content("domain", "project", _=mock_auth)

            assert response.status_code == 404
            import json

            content = json.loads(response.body)
            assert content["error"]["code"] == "no_content"


class TestGetContentStats:
    """Tests for GET /api/content/stats endpoint."""

    @pytest.mark.asyncio
    async def test_get_content_stats_success(self, mock_forge_root):
        """Test getting aggregate content statistics."""
        from forge_harness.webhook_server.api.content import get_content_stats

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", mock_forge_root):
            response = await get_content_stats(_=mock_auth)

            import json

            content = json.loads(response.body)
            assert content["success"] is True
            assert "total_projects" in content["data"]
            assert "total_files" in content["data"]
            assert "total_words" in content["data"]
            assert "content_types" in content["data"]
            assert "domains" in content["data"]

    @pytest.mark.asyncio
    async def test_get_content_stats_calculates_averages(self, mock_forge_root):
        """Test that stats include calculated averages."""
        from forge_harness.webhook_server.api.content import get_content_stats

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", mock_forge_root):
            response = await get_content_stats(_=mock_auth)

            import json

            content = json.loads(response.body)
            stats = content["data"]

            assert "avg_words_per_project" in stats
            assert "avg_files_per_project" in stats
            assert stats["avg_words_per_project"] > 0

    @pytest.mark.asyncio
    async def test_get_content_stats_domain_grouping(self, mock_forge_root):
        """Test that stats are grouped by domain."""
        from forge_harness.webhook_server.api.content import get_content_stats

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", mock_forge_root):
            response = await get_content_stats(_=mock_auth)

            import json

            content = json.loads(response.body)
            domains = content["data"]["domains"]

            assert "test-domain" in domains
            assert domains["test-domain"]["projects"] == 1
            assert domains["test-domain"]["files"] == 3

    @pytest.mark.asyncio
    async def test_get_content_stats_empty(self, tmp_path):
        """Test stats with no content."""
        from forge_harness.webhook_server.api.content import get_content_stats

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", tmp_path):
            response = await get_content_stats(_=mock_auth)

            import json

            content = json.loads(response.body)
            stats = content["data"]

            assert stats["total_projects"] == 0
            assert stats["avg_words_per_project"] == 0
            assert stats["avg_files_per_project"] == 0

    @pytest.mark.asyncio
    async def test_get_content_stats_error(self):
        """Test error handling in stats calculation."""
        from forge_harness.webhook_server.api.content import get_content_stats

        mock_auth = AsyncMock()

        with patch(
            "forge_harness.webhook_server.api.content._scan_content_directories",
            side_effect=Exception("Calculation failed"),
        ):
            response = await get_content_stats(_=mock_auth)

            assert response.status_code == 500
            import json

            content = json.loads(response.body)
            assert content["success"] is False
