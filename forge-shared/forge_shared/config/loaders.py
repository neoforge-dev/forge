"""
Configuration loaders for different sources.

Provides functions to load configuration from environment variables,
files, and other sources.

Example:
    ```python
    from forge_shared.config import load_config_from_env, load_config_from_file

    # Load from environment
    config = load_config_from_env(AppConfig)

    # Load from file
    config = load_config_from_file(AppConfig, "config.json")
    ```
"""

import json
import os
from pathlib import Path
from typing import Any, Type, TypeVar

from pydantic import ValidationError

T = TypeVar("T", bound="BaseConfig")


def load_config_from_env(
    config_class: Type[T],
    prefix: str = "",
    **kwargs: Any,
) -> T:
    """
    Load configuration from environment variables.

    Args:
        config_class: Configuration class to instantiate
        prefix: Prefix for environment variables
        **kwargs: Additional overrides

    Returns:
        Configuration instance

    Example:
        ```python
        config = load_config_from_env(AppConfig, prefix="APP_")
        ```
    """
    # Prepare environment overrides
    env_overrides = {}

    if prefix:
        # Load prefixed environment variables
        for key, value in os.environ.items():
            if key.startswith(prefix):
                config_key = key[len(prefix) :].lower()
                env_overrides[config_key] = value

    # Merge with kwargs
    env_overrides.update(kwargs)

    # Create config instance
    try:
        return config_class(**env_overrides)
    except ValidationError as exc:
        raise ValueError(f"Configuration validation failed: {exc}") from exc


def load_config_from_file(
    config_class: Type[T],
    file_path: str | Path,
    file_format: str = "json",
) -> T:
    """
    Load configuration from file.

    Args:
        config_class: Configuration class to instantiate
        file_path: Path to configuration file
        file_format: File format (json, yaml, toml)

    Returns:
        Configuration instance

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is invalid

    Example:
        ```python
        config = load_config_from_file(AppConfig, "config.json")
        ```
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {file_path}")

    # Read file based on format
    if file_format == "json":
        with open(file_path) as f:
            config_data = json.load(f)
    elif file_format == "yaml":
        try:
            import yaml

            with open(file_path) as f:
                config_data = yaml.safe_load(f)
        except ImportError:
            raise ValueError("PyYAML is required for YAML configuration files")
    elif file_format == "toml":
        try:
            import tomllib

            with open(file_path, "rb") as f:
                config_data = tomllib.load(f)
        except ImportError:
            raise ValueError("tomllib is required for TOML configuration files")
    else:
        raise ValueError(f"Unsupported file format: {file_format}")

    # Create config instance
    try:
        return config_class(**config_data)
    except ValidationError as exc:
        raise ValueError(f"Configuration validation failed: {exc}") from exc


def load_config_from_dict(
    config_class: Type[T],
    config_data: dict[str, Any],
) -> T:
    """
    Load configuration from dictionary.

    Args:
        config_class: Configuration class to instantiate
        config_data: Configuration dictionary

    Returns:
        Configuration instance

    Example:
        ```python
        config_data = {"database_url": "postgresql://...", "debug": True}
        config = load_config_from_dict(AppConfig, config_data)
        ```
    """
    try:
        return config_class(**config_data)
    except ValidationError as exc:
        raise ValueError(f"Configuration validation failed: {exc}") from exc
