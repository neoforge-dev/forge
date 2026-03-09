"""
Tests for LLM Configuration Endpoints
=====================================

Tests for GET /api/config/llm and POST /api/config/llm endpoints
in forge_harness.webhook_server.
"""

import json

import pytest


@pytest.fixture
def config_dir(tmp_path):
    """Create temporary config directory."""
    config_dir = tmp_path / ".forge/config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


@pytest.fixture
def llm_config_file(config_dir):
    """Return path to LLM config file."""
    return config_dir / "llm.json"


@pytest.fixture
def mock_app(tmp_path, monkeypatch):
    """Create mock FastAPI app for testing with temp config directory."""
    try:
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import create_app
    except ImportError:
        pytest.skip("FastAPI not installed")

    # Set temp config directory via environment variable
    config_dir = tmp_path / ".forge/config"
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(config_dir))

    # Disable auth for testing
    from forge_harness.webhook_server import AuthConfig

    auth_config = AuthConfig(require_auth=False)

    # Create app with mock notification harness
    app = create_app(None, auth_config=auth_config)

    return TestClient(app)


class TestLLMConfigGetEndpoint:
    """Tests for GET /api/config/llm endpoint."""

    def test_llm_config_get_endpoint_returns_defaults(self, mock_app):
        """Test GET endpoint returns default config when no file exists."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        response = mock_app.get("/api/config/llm")

        assert response.status_code == 200
        data = response.json()

        assert data["success"] is True
        assert "data" in data
        config = data["data"]

        # Verify default values
        assert config["provider"] == "anthropic"
        assert config["model"] == "claude-opus-4-5-20251101"
        assert config["temperature"] == 0.7
        assert config["max_tokens"] == 4096

    def test_llm_config_get_endpoint_returns_saved_config(self, mock_app, tmp_path):
        """Test GET endpoint returns saved config from file."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        # Create config file with custom values
        config_dir = tmp_path / ".forge/config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "llm.json"

        custom_config = {
            "provider": "openai",
            "model": "gpt-4",
            "temperature": 0.5,
            "max_tokens": 2048,
        }
        config_file.write_text(json.dumps(custom_config, indent=2))

        response = mock_app.get("/api/config/llm")

        assert response.status_code == 200
        data = response.json()
        config = data["data"]

        # Verify custom values
        assert config["provider"] == "openai"
        assert config["model"] == "gpt-4"
        assert config["temperature"] == 0.5
        assert config["max_tokens"] == 2048


class TestLLMConfigPostEndpoint:
    """Tests for POST /api/config/llm endpoint."""

    def test_llm_config_post_endpoint_updates_config(self, mock_app):
        """Test POST endpoint updates and persists configuration."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        # Update config
        response = mock_app.post(
            "/api/config/llm",
            json={
                "provider": "gemini",
                "model": "gemini-pro",
                "temperature": 0.8,
                "max_tokens": 8192,
            },
        )

        assert response.status_code == 200
        data = response.json()
        config = data["data"]

        # Verify updated values in response
        assert config["provider"] == "gemini"
        assert config["model"] == "gemini-pro"
        assert config["temperature"] == 0.8
        assert config["max_tokens"] == 8192

    def test_llm_config_post_endpoint_partial_update(self, mock_app):
        """Test POST endpoint with partial update (only some fields)."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        # First, set initial config
        mock_app.post(
            "/api/config/llm",
            json={
                "provider": "openai",
                "model": "gpt-4",
                "temperature": 0.5,
                "max_tokens": 2048,
            },
        )

        # Update only model
        response = mock_app.post("/api/config/llm", json={"model": "gpt-3.5-turbo"})

        assert response.status_code == 200
        data = response.json()
        config = data["data"]

        # Model should be updated
        assert config["model"] == "gpt-3.5-turbo"
        # Other fields should remain unchanged
        assert config["provider"] == "openai"
        assert config["temperature"] == 0.5
        assert config["max_tokens"] == 2048


class TestLLMConfigPersistence:
    """Tests for configuration persistence."""

    def test_llm_config_persists_to_file(self, mock_app, tmp_path):
        """Test that config changes persist to filesystem."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        config_dir = tmp_path / ".forge/config"
        config_file = config_dir / "llm.json"

        # Update config
        mock_app.post(
            "/api/config/llm",
            json={
                "provider": "anthropic",
                "model": "claude-3-opus",
                "temperature": 0.9,
                "max_tokens": 4096,
            },
        )

        # Verify file was created
        assert config_file.exists()

        # Read file directly and verify content
        saved_config = json.loads(config_file.read_text())
        assert saved_config["provider"] == "anthropic"
        assert saved_config["model"] == "claude-3-opus"
        assert saved_config["temperature"] == 0.9
        assert saved_config["max_tokens"] == 4096

    def test_llm_config_loads_persisted_data(self, mock_app):
        """Test that GET loads previously persisted config."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        # Save config
        mock_app.post(
            "/api/config/llm",
            json={
                "provider": "cohere",
                "model": "command-r",
                "temperature": 0.6,
                "max_tokens": 3000,
            },
        )

        # Retrieve config
        response = mock_app.get("/api/config/llm")

        assert response.status_code == 200
        data = response.json()
        config = data["data"]

        # Verify persisted values are returned
        assert config["provider"] == "cohere"
        assert config["model"] == "command-r"
        assert config["temperature"] == 0.6
        assert config["max_tokens"] == 3000

    def test_llm_config_creates_directory_if_missing(self, mock_app, tmp_path):
        """Test that config directory is created if it doesn't exist."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        config_dir = tmp_path / ".forge/config"
        config_file = config_dir / "llm.json"

        # Ensure directory doesn't exist initially
        if config_dir.exists():
            import shutil

            shutil.rmtree(config_dir)

        # Save config
        mock_app.post("/api/config/llm", json={"provider": "openai"})

        # Verify directory was created
        assert config_dir.exists()
        assert config_file.exists()
