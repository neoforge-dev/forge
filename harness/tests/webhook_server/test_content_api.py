"""Tests for content library API endpoints."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client with mocked auth."""
    from forge_harness.webhook_server_main import create_app

    app = create_app()

    # Override the verify_auth dependency to always pass
    def override_verify_auth():
        return None

    from forge_harness.webhook_server.core import dependencies
    app.dependency_overrides[dependencies.verify_auth] = override_verify_auth

    return TestClient(app)


def test_get_content_libraries(client, tmp_path, monkeypatch):
    """Test GET /api/content endpoint."""
    # Create mock content structure
    forge_root = tmp_path / "forge"
    domain_dir = forge_root / "test-domain"
    project_dir = domain_dir / "test-project"
    content_dir = project_dir / "content"
    content_dir.mkdir(parents=True)

    # Create some markdown files
    (content_dir / "blog-posts.md").write_text("# Blog Post\n\nThis is a test blog post with some content.")
    (content_dir / "social-posts.md").write_text("# Social Media\n\nTest social content.")

    # Mock FORGE_ROOT
    monkeypatch.setenv("FORGE_ROOT", str(forge_root))
    with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", forge_root):
        response = client.get("/api/content")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert "data" in data
    assert "projects" in data["data"]
    assert "summary" in data["data"]

    # Verify project data
    projects = data["data"]["projects"]
    assert len(projects) == 1
    project = projects[0]
    assert project["domain"] == "test-domain"
    assert project["project"] == "test-project"
    assert project["has_content"] is True
    assert project["file_count"] == 2
    assert project["total_words"] > 0
    assert "blog_posts" in project["content_types"]
    assert "social_media" in project["content_types"]

    # Verify summary
    summary = data["data"]["summary"]
    assert summary["total_projects_with_content"] == 1
    assert summary["total_files"] == 2
    assert summary["total_words"] > 0


def test_get_project_content(client, tmp_path, monkeypatch):
    """Test GET /api/content/{domain}/{project} endpoint."""
    # Create mock content structure
    forge_root = tmp_path / "forge"
    domain_dir = forge_root / "test-domain"
    project_dir = domain_dir / "test-project"
    content_dir = project_dir / "content"
    content_dir.mkdir(parents=True)

    # Create markdown files
    (content_dir / "blog-posts.md").write_text("# Blog\n\nContent here.")
    (content_dir / "README.md").write_text("# README\n\nProject content overview.")

    # Mock FORGE_ROOT
    monkeypatch.setenv("FORGE_ROOT", str(forge_root))
    with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", forge_root):
        response = client.get("/api/content/test-domain/test-project")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    project_data = data["data"]
    assert project_data["domain"] == "test-domain"
    assert project_data["project"] == "test-project"
    assert project_data["file_count"] == 2
    assert project_data["total_words"] > 0
    assert "blog_posts" in project_data["content_types"]

    # Verify files list
    files = project_data["files"]
    assert len(files) == 2
    assert all("name" in f for f in files)
    assert all("word_count" in f for f in files)
    assert all("last_modified" in f for f in files)


def test_get_project_content_not_found(client, tmp_path, monkeypatch):
    """Test GET /api/content/{domain}/{project} when project doesn't exist."""
    forge_root = tmp_path / "forge"
    forge_root.mkdir()

    monkeypatch.setenv("FORGE_ROOT", str(forge_root))
    with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", forge_root):
        response = client.get("/api/content/nonexistent/project")

    assert response.status_code == 404
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "not_found"


def test_get_content_stats(client, tmp_path, monkeypatch):
    """Test GET /api/content/stats endpoint."""
    # Create mock content structure with multiple projects
    forge_root = tmp_path / "forge"

    # Project 1
    p1_dir = forge_root / "domain1" / "project1" / "content"
    p1_dir.mkdir(parents=True)
    (p1_dir / "blog-posts.md").write_text("# Blog 1\n\nContent for project 1.")
    (p1_dir / "social-posts.md").write_text("# Social 1\n\nSocial content.")

    # Project 2
    p2_dir = forge_root / "domain2" / "project2" / "content"
    p2_dir.mkdir(parents=True)
    (p2_dir / "case-studies.md").write_text("# Case Study\n\nDetailed case study content.")

    monkeypatch.setenv("FORGE_ROOT", str(forge_root))
    with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", forge_root):
        response = client.get("/api/content/stats")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    stats = data["data"]
    assert stats["total_projects"] == 2
    assert stats["total_files"] == 3
    assert stats["total_words"] > 0
    assert stats["avg_words_per_project"] > 0
    assert stats["avg_files_per_project"] == 1.5

    # Verify content type counts
    assert "blog_posts" in stats["content_types"]
    assert "social_media" in stats["content_types"]
    assert "case_studies" in stats["content_types"]

    # Verify domain stats
    assert "domain1" in stats["domains"]
    assert "domain2" in stats["domains"]
    assert stats["domains"]["domain1"]["projects"] == 1
    assert stats["domains"]["domain2"]["projects"] == 1


def test_content_type_detection(client, tmp_path, monkeypatch):
    """Test that content types are correctly detected from filenames."""
    forge_root = tmp_path / "forge"
    content_dir = forge_root / "test-domain" / "test-project" / "content"
    content_dir.mkdir(parents=True)

    # Create files with different content type indicators
    (content_dir / "blog-posts.md").write_text("Blog content")
    (content_dir / "social-media.md").write_text("Social content")
    (content_dir / "case-studies.md").write_text("Case study content")
    (content_dir / "email-sequences.md").write_text("Email content")
    (content_dir / "lead-magnets.md").write_text("Lead magnet content")
    (content_dir / "CONTENT_STRATEGY.md").write_text("Strategy content")

    monkeypatch.setenv("FORGE_ROOT", str(forge_root))
    with patch("forge_harness.webhook_server.api.content.FORGE_ROOT", forge_root):
        response = client.get("/api/content/test-domain/test-project")

    assert response.status_code == 200
    data = response.json()

    content_types = data["data"]["content_types"]
    assert "blog_posts" in content_types
    assert "social_media" in content_types
    assert "case_studies" in content_types
    assert "email_sequences" in content_types
    assert "lead_magnets" in content_types
    assert "strategy" in content_types
