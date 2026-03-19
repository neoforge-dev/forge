"""Tests for configuration loaders."""

import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from forge_shared.config import (
    BaseConfig,
    load_config_from_dict,
    load_config_from_env,
    load_config_from_file,
)


class TestLoadConfigFromEnv:
    """Test load_config_from_env function."""

    def test_load_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test loading configuration from environment variables."""
        monkeypatch.setenv("APP_NAME", "test-app")
        monkeypatch.setenv("ENVIRONMENT", "production")

        config = load_config_from_env(BaseConfig)

        assert config.app_name == "test-app"
        assert config.environment == "production"

    def test_load_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test loading with environment variable prefix."""
        monkeypatch.setenv("APP__APP_NAME", "test-app")
        monkeypatch.setenv("APP__PORT", "9000")

        config = load_config_from_env(BaseConfig, prefix="APP__")

        assert config.app_name == "test-app"
        assert config.port == 9000

    def test_load_with_kwargs_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that kwargs override environment variables."""
        monkeypatch.setenv("APP_NAME", "env-app")

        config = load_config_from_env(BaseConfig, app_name="kwarg-app")

        assert config.app_name == "kwarg-app"

    def test_validation_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test validation error for invalid environment values."""
        monkeypatch.setenv("PORT", "invalid")

        with pytest.raises(ValueError, match="Configuration validation failed"):
            load_config_from_env(BaseConfig)


class TestLoadConfigFromFile:
    """Test load_config_from_file function."""

    def test_load_from_json(self) -> None:
        """Test loading from JSON file."""
        config_data = {
            "app_name": "test-app",
            "environment": "production",
            "port": 9000,
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            temp_path = f.name

        try:
            config = load_config_from_file(BaseConfig, temp_path, "json")

            assert config.app_name == "test-app"
            assert config.environment == "production"
            assert config.port == 9000
        finally:
            os.unlink(temp_path)

    def test_load_from_yaml(self) -> None:
        """Test loading from YAML file."""
        config_data = """
app_name: test-app
environment: production
port: 9000
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_data)
            temp_path = f.name

        try:
            config = load_config_from_file(BaseConfig, temp_path, "yaml")

            assert config.app_name == "test-app"
            assert config.environment == "production"
            assert config.port == 9000
        finally:
            os.unlink(temp_path)

    def test_load_from_toml(self) -> None:
        """Test loading from TOML file."""
        config_data = """
app_name = "test-app"
environment = "production"
port = 9000
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write(config_data)
            temp_path = f.name

        try:
            config = load_config_from_file(BaseConfig, temp_path, "toml")

            assert config.app_name == "test-app"
            assert config.environment == "production"
            assert config.port == 9000
        finally:
            os.unlink(temp_path)

    def test_file_not_found(self) -> None:
        """Test error when file doesn't exist."""
        with pytest.raises(FileNotFoundError, match="Configuration file not found"):
            load_config_from_file(BaseConfig, "nonexistent.json", "json")

    def test_unsupported_format(self) -> None:
        """Test error for unsupported file format."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test")
            temp_path = f.name

        try:
            with pytest.raises(ValueError, match="Unsupported file format"):
                load_config_from_file(BaseConfig, temp_path, "txt")
        finally:
            os.unlink(temp_path)

    def test_validation_error(self) -> None:
        """Test validation error for invalid config data."""
        config_data = {
            "port": "invalid",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            temp_path = f.name

        try:
            with pytest.raises(ValueError, match="Configuration validation failed"):
                load_config_from_file(BaseConfig, temp_path, "json")
        finally:
            os.unlink(temp_path)


class TestLoadConfigFromDict:
    """Test load_config_from_dict function."""

    def test_load_from_dict(self) -> None:
        """Test loading from dictionary."""
        config_data = {
            "app_name": "test-app",
            "environment": "production",
            "port": 9000,
        }

        config = load_config_from_dict(BaseConfig, config_data)

        assert config.app_name == "test-app"
        assert config.environment == "production"
        assert config.port == 9000

    def test_validation_error(self) -> None:
        """Test validation error for invalid dict data."""
        config_data = {
            "port": "invalid",
        }

        with pytest.raises(ValueError, match="Configuration validation failed"):
            load_config_from_dict(BaseConfig, config_data)

    def test_empty_dict(self) -> None:
        """Test loading from empty dict uses defaults."""
        config = load_config_from_dict(BaseConfig, {})

        assert config.app_name == "forge-app"
        assert config.environment == "development"


class TestConfigIntegration:
    """Integration tests for configuration loading."""

    def test_multiple_sources_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test precedence of configuration sources."""
        # Set up file
        config_data = {
            "app_name": "file-app",
            "port": 8000,
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            temp_path = f.name

        try:
            # Set environment variable
            monkeypatch.setenv("PORT", "9000")

            # Load from file and override with environment
            config = load_config_from_file(BaseConfig, temp_path, "json")
            config = load_config_from_env(BaseConfig, prefix="", port=10000)

            # Environment should take precedence
            assert config.port == 10000
            assert config.app_name == "forge-app"  # Default
        finally:
            os.unlink(temp_path)

    def test_pathlib_path(self) -> None:
        """Test that pathlib.Path works for file path."""
        config_data = {"app_name": "test-app"}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            temp_path = Path(f.name)

        try:
            config = load_config_from_file(BaseConfig, temp_path, "json")
            assert config.app_name == "test-app"
        finally:
            os.unlink(temp_path)
