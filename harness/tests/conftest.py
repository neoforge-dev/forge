"""Pytest configuration for forge-harness tests."""

import os
import sys
from pathlib import Path

import pytest

# ============================================================================
# CRITICAL: Set FORGE_CONFIG_DIR BEFORE any imports
# This MUST happen at module load time because webhook_server.py
# evaluates Path(config_dir_path) at import time, not runtime.
# ============================================================================

# Set FORGE_CONFIG_DIR before importing any forge_harness modules
# to avoid "Path parameters cannot have a default value" errors
if "FORGE_CONFIG_DIR" not in os.environ:
    # Create a temp directory for tests
    test_config_base = Path("/tmp/forge-harness-tests")
    test_config_base.mkdir(parents=True, exist_ok=True)
    os.environ["FORGE_CONFIG_DIR"] = str(test_config_base / "default_config")

# Ensure the config directory exists
config_dir = Path(os.environ["FORGE_CONFIG_DIR"])
config_dir.mkdir(parents=True, exist_ok=True)

# Add the forge_harness package to the path
harness_root = Path(__file__).parent.parent
sys.path.insert(0, str(harness_root))


# ============================================================================
# Environment Setup for Tests (additional fixtures)
# ============================================================================

@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    """Set up additional environment variables for tests."""
    # Update FORGE_CONFIG_DIR for this specific test
    test_config_dir = tmp_path / ".forge/config"
    test_config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(test_config_dir))
    return test_config_dir


# ============================================================================
# Workflow Harness Fixtures
# ============================================================================

@pytest.fixture
def workflow_harness(tmp_path):
    """Create a WorkflowHarness instance for testing."""
    from forge_harness import create_workflow_harness
    return create_workflow_harness(
        workflow_name="test-workflow",
        checkpoint_dir=tmp_path / "checkpoints"
    )


@pytest.fixture
def checkpoint_dir(tmp_path):
    """Create a temporary checkpoint directory."""
    cp_dir = tmp_path / "checkpoints"
    cp_dir.mkdir(parents=True, exist_ok=True)
    return cp_dir


# ============================================================================
# Domain Registry Fixtures
# ============================================================================

@pytest.fixture
def domain_registry():
    """Load and return the DomainRegistry."""
    from forge_harness import DomainRegistry
    DomainRegistry.load()
    return DomainRegistry


@pytest.fixture
def sample_domain(domain_registry):
    """Get a sample domain entry for testing."""
    return domain_registry.get("thebrightharbor-com")


# ============================================================================
# Content Harness Fixtures
# ============================================================================

@pytest.fixture
def content_storage(tmp_path):
    """Create a file-based content storage for testing."""
    from forge_harness import create_file_storage
    return create_file_storage(tmp_path / "content")


@pytest.fixture
def sample_brief():
    """Create a sample ContentBrief for testing."""
    from forge_harness import ContentBrief
    return ContentBrief(
        id="test-001",
        title="Test Brief",
        content_type="blog_posts",
        requirements="Write a test blog post",
        keywords=["test", "example"],
        audience="developers",
        length="medium",
        tone="professional",
    )


@pytest.fixture
def sample_content_item():
    """Create a sample ContentItem for testing."""
    from forge_harness import ContentItem, ContentStage
    return ContentItem(
        brief_id="test-001",
        title="Test Article",
        content_type="blog_posts",
        stage=ContentStage.DRAFT,
        generated_content="# Test Article\n\nThis is test content.",
    )


# ============================================================================
# Notion Storage Fixtures (Mocked)
# ============================================================================

@pytest.fixture
def mock_notion_storage():
    """Create a mock Notion storage for testing.

    NOTE: notion_storage has been archived.
    See harness/archive/README.md for restoration instructions.
    """
    pytest.skip("notion_storage has been archived")
