"""
Tests for Memories API endpoints.

Tests:
- GET /api/memories - List memories from .forge/memories/
- GET /api/memories/{memory_id} - Get specific memory
"""

from pathlib import Path

import pytest


class TestMemoriesRouter:
    """Test that memories router is properly configured."""

    def test_memories_router_imports(self):
        """Test that memories router can be imported."""
        from forge_harness.webhook_server.api.memories import router
        assert router is not None

    def test_list_memories_endpoint_exists(self):
        """Test that /api/memories endpoint exists."""
        from forge_harness.webhook_server.api.memories import router
        routes = [r.path for r in router.routes]
        assert "/api/memories" in routes

    def test_get_memory_endpoint_exists(self):
        """Test that /api/memories/{memory_id} endpoint exists."""
        from forge_harness.webhook_server.api.memories import router
        routes = [r.path for r in router.routes]
        assert any("/api/memories/{memory_id}" in r for r in routes)


class TestMemoriesAPIResponseFormat:
    """Test API response format."""

    def test_api_response_format(self):
        """Test api_response helper produces correct format."""
        from forge_harness.webhook_server.api.memories import api_response

        result = api_response({"items": []})
        assert result["success"] is True
        assert result["data"] == {"items": []}
        assert result["error"] is None
        assert "timestamp" in result


class TestMemoryMetadataModel:
    """Test MemoryMetadata model."""

    def test_memory_metadata_fields(self):
        """Test MemoryMetadata has all required fields."""
        from forge_harness.webhook_server.api.memories import MemoryMetadata

        memory = MemoryMetadata(
            id="test-id",
            title="Test Memory",
            path=".forge/memories/test.md",
            summary="Test summary",
            category="test",
            last_modified="2026-02-10T00:00:00Z"
        )
        assert memory.id == "test-id"
        assert memory.title == "Test Memory"
        assert memory.path == ".forge/memories/test.md"
        assert memory.summary == "Test summary"
        assert memory.category == "test"
        assert memory.last_modified == "2026-02-10T00:00:00Z"


class TestHelperFunctions:
    """Test helper functions."""

    def test_get_forge_repo_root_exists(self):
        """Test _get_forge_repo_root function exists."""
        from forge_harness.webhook_server.api.memories import _get_forge_repo_root
        assert callable(_get_forge_repo_root)

    def test_read_memory_content_exists(self):
        """Test _read_memory_content function exists."""
        from forge_harness.webhook_server.api.memories import _read_memory_content
        assert callable(_read_memory_content)

    def test_extract_title_exists(self):
        """Test _extract_title function exists."""
        from forge_harness.webhook_server.api.memories import _extract_title
        assert callable(_extract_title)

    def test_get_category_exists(self):
        """Test _get_category function exists."""
        from forge_harness.webhook_server.api.memories import _get_category
        assert callable(_get_category)


class TestEndpointFunctions:
    """Test endpoint functions directly."""

    def test_list_memories_is_async(self):
        """Test list_memories is an async function."""
        import inspect

        from forge_harness.webhook_server.api.memories import list_memories
        assert inspect.iscoroutinefunction(list_memories)

    def test_get_memory_is_async(self):
        """Test get_memory is an async function."""
        import inspect

        from forge_harness.webhook_server.api.memories import get_memory
        assert inspect.iscoroutinefunction(get_memory)
