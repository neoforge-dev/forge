"""
Configuration for Continuous Runner.
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class ContinuousRunnerSettings(BaseSettings):
    """Configuration for Continuous Runner."""

    # Loop configuration
    domain: str = os.environ.get("FORGE_DOMAIN", "")
    project: str = os.environ.get("FORGE_PROJECT", "")
    features_path: Path | None = (
        Path(os.environ.get("FEATURES_PATH")) if os.environ.get("FEATURES_PATH") else None
    )
    forge_root: Path = Path.cwd()

    # Timeouts and intervals
    approval_timeout_hours: float = 24.0
    loop_cooldown_seconds: float = 60.0
    max_iterations_per_run: int = 50

    # Webhook server configuration
    webhook_port: int = 8082
    webhook_host: str = "0.0.0.0"
    github_webhook_secret: str | None = os.environ.get("GITHUB_WEBHOOK_SECRET")

    # Railway integration
    railway_api_key: str | None = os.environ.get("RAILWAY_API_KEY")
    railway_service_id: str | None = os.environ.get("RAILWAY_SERVICE_ID")

    model_config = SettingsConfigDict(
        env_prefix="FORGE_CR_",
        env_file=".env",
        extra="ignore",
    )


settings = ContinuousRunnerSettings()
