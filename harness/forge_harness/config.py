"""
Unified FORGE Harness Configuration
====================================

This module provides a unified configuration system for the FORGE harness
using pydantic-settings. It consolidates configuration from:
- Environment variables (FORGE_* prefix)
- .env files
- Direct instantiation

All configuration is validated at load time using Pydantic.

Usage:
    from forge_harness.config import ForgeSettings, get_settings

    # Get singleton settings instance
    settings = get_settings()

    # Or create fresh instance
    settings = ForgeSettings()

    # Access settings
    print(settings.domain)
    print(settings.max_iterations)

    # Convert to legacy configs for backward compatibility
    harness_config = settings.to_harness_config()
    loop_config = settings.to_loop_config()
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from .harness_registry import HarnessConfig
    from .ralph_loop import LoopConfig


class ForgeSettings(BaseSettings):
    """Unified FORGE Harness configuration with validation.

    This class consolidates all harness configuration into a single
    validated settings object. It supports:
    - Environment variables with FORGE_ prefix
    - .env file loading
    - Default values
    - Type validation

    Attributes:
        domain: Domain name (e.g., "codeswiftr-com")
        project: Project name (e.g., "interview-simulator")
        project_dir: Path to project directory
        dry_run: Whether to run in dry-run mode

        checkpoint_dir: Path for orchestration checkpoints
        session_dir: Path for session state storage
        approval_storage_dir: Path for approval queue storage
        learning_store_dir: Path for meta-learning data

        notion_api_token: Notion API token for content library
        notion_database_id: Notion database ID for content
        notion_data_source_id: Notion data source ID (alias for database_id)
        slack_webhook_url: Slack webhook for notifications
        github_token: GitHub token for API access
        github_repo: GitHub repo (format: "owner/repo")
        posthog_api_key: PostHog API key for analytics

        railway_token: Railway deployment token
        cloudflare_api_token: Cloudflare API token
        cloudflare_account_id: Cloudflare account ID

        meta_learning_enabled: Enable meta-learning features
        code_atlas_url: Code Atlas API URL
        tech_diligence_url: Tech Diligence API URL

        max_iterations: Maximum Ralph loop iterations
        max_failures_per_feature: Max failures before blocking
        checkpoint_interval: Save checkpoint every N iterations
        test_command: Command to run tests
        lint_command: Optional command to run lint checks
        timeout_seconds: Timeout per feature implementation
        approval_timeout_hours: Timeout for human approval

        webhook_token: Token for webhook authentication
        webhook_require_auth: Whether to require auth for webhooks
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_format: Log format (text, json)
    """

    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow environment variables without prefix for common ones
        env_nested_delimiter="__",
    )

    # =========================================================================
    # Core Settings
    # =========================================================================
    domain: str | None = None
    project: str | None = None
    project_dir: Path | None = None
    dry_run: bool = False

    # =========================================================================
    # Path Settings
    # =========================================================================
    checkpoint_dir: Path = Path(".forge/orchestration_checkpoints")
    session_dir: Path = Path(".forge/sessions")
    approval_storage_dir: Path = Path(".forge/approvals")
    learning_store_dir: Path = Path(".forge/learning")
    ralph_checkpoint_dir: Path = Path(".forge/ralph_checkpoints")

    # =========================================================================
    # Integration Tokens (sensitive - hidden from repr)
    # =========================================================================
    notion_api_token: str | None = Field(default=None, repr=False)
    notion_database_id: str | None = None
    notion_data_source_id: str | None = None  # Alias for database_id
    notion_linked_database_id: str | None = None

    slack_webhook_url: str | None = Field(default=None, repr=False)

    github_token: str | None = Field(default=None, repr=False)
    github_repo: str | None = None

    posthog_api_key: str | None = Field(default=None, repr=False)

    # =========================================================================
    # Deployment Settings
    # =========================================================================
    railway_token: str | None = Field(default=None, repr=False)
    cloudflare_api_token: str | None = Field(default=None, repr=False)
    cloudflare_account_id: str | None = None

    # =========================================================================
    # Meta-Learning Settings
    # =========================================================================
    # Meta-learning is enabled by default for local pattern learning.
    # This enables the DecisionEngine to learn from Ralph Loop outcomes,
    # track patterns, and improve autonomous decision-making over time.
    # External bridges (Code Atlas, Tech Diligence) are optional and provide
    # additional signals when configured.
    meta_learning_enabled: bool = True
    code_atlas_url: str | None = None
    tech_diligence_url: str | None = None

    # =========================================================================
    # Loop Settings (Ralph Loop)
    # =========================================================================
    max_iterations: int = 100
    max_failures_per_feature: int = 5
    checkpoint_interval: int = 10
    test_command: str = "uv run pytest tests/ -v --tb=short -x"
    lint_command: str | None = None
    timeout_seconds: int = 3600
    approval_timeout_hours: float = 24.0

    # =========================================================================
    # Webhook Server Settings
    # =========================================================================
    webhook_token: str | None = Field(default=None, repr=False)
    webhook_require_auth: bool = True

    # =========================================================================
    # LLM Settings
    # =========================================================================
    llm_provider: str = "claude"  # claude, openai, gemini, etc.
    llm_model: str = "claude-sonnet-4-20250514"

    # =========================================================================
    # Logging Settings
    # =========================================================================
    log_level: str = "INFO"
    log_format: str = "text"  # "text" or "json"

    # =========================================================================
    # Validators
    # =========================================================================

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is valid."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return upper

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        """Validate log format is valid."""
        valid_formats = {"text", "json"}
        lower = v.lower()
        if lower not in valid_formats:
            raise ValueError(f"log_format must be one of {valid_formats}")
        return lower

    @field_validator("max_iterations", "max_failures_per_feature", "checkpoint_interval")
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        """Validate positive integer values."""
        if v < 1:
            raise ValueError("Value must be positive")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        """Validate timeout is reasonable."""
        if v < 30:
            raise ValueError("timeout_seconds must be at least 30")
        if v > 86400:  # 24 hours
            raise ValueError("timeout_seconds must not exceed 86400 (24 hours)")
        return v

    # =========================================================================
    # Factory Methods
    # =========================================================================

    @classmethod
    def from_env(
        cls,
        domain: str | None = None,
        project: str | None = None,
        **overrides: Any,
    ) -> ForgeSettings:
        """Create settings from environment with optional overrides.

        Args:
            domain: Override domain from environment
            project: Override project from environment
            **overrides: Additional settings to override

        Returns:
            ForgeSettings instance
        """
        # Build overrides dict
        settings_overrides = {}
        if domain is not None:
            settings_overrides["domain"] = domain
        if project is not None:
            settings_overrides["project"] = project
        settings_overrides.update(overrides)

        return cls(**settings_overrides)

    # =========================================================================
    # Backward Compatibility
    # =========================================================================

    def to_harness_config(self) -> HarnessConfig:
        """Convert to legacy HarnessConfig for backward compatibility.

        Returns:
            HarnessConfig populated from these settings
        """
        from .harness_registry import HarnessConfig

        return HarnessConfig(
            domain=self.domain,
            project=self.project,
            project_dir=self.project_dir,
            checkpoint_dir=self.checkpoint_dir,
            session_dir=self.session_dir,
            approval_storage_dir=self.approval_storage_dir,
            notion_api_token=self.notion_api_token,
            notion_database_id=self.notion_data_source_id or self.notion_database_id,
            slack_webhook_url=self.slack_webhook_url,
            github_token=self.github_token,
            github_repo=self.github_repo,
            posthog_api_key=self.posthog_api_key,
            dry_run=self.dry_run,
            meta_learning_enabled=self.meta_learning_enabled,
            learning_store_dir=self.learning_store_dir,
            code_atlas_url=self.code_atlas_url,
            tech_diligence_url=self.tech_diligence_url,
        )

    def to_loop_config(self) -> LoopConfig:
        """Convert to legacy LoopConfig for backward compatibility.

        Returns:
            LoopConfig populated from these settings
        """
        from .ralph_loop import LoopConfig

        return LoopConfig(
            domain=self.domain,
            project=self.project,
            max_iterations=self.max_iterations,
            max_failures_per_feature=self.max_failures_per_feature,
            checkpoint_interval=self.checkpoint_interval,
            test_command=self.test_command,
            lint_command=self.lint_command,
            timeout_seconds=self.timeout_seconds,
            dry_run=self.dry_run,
            approval_timeout_hours=self.approval_timeout_hours,
        )

    # =========================================================================
    # Utility Methods
    # =========================================================================

    @property
    def effective_notion_database_id(self) -> str | None:
        """Get the effective Notion database ID (data_source_id takes precedence)."""
        return self.notion_data_source_id or self.notion_database_id

    @property
    def has_notion(self) -> bool:
        """Check if Notion integration is configured."""
        return bool(self.notion_api_token and self.effective_notion_database_id)

    @property
    def has_github(self) -> bool:
        """Check if GitHub integration is configured."""
        return bool(self.github_token)

    @property
    def has_slack(self) -> bool:
        """Check if Slack integration is configured."""
        return bool(self.slack_webhook_url)

    @property
    def has_posthog(self) -> bool:
        """Check if PostHog analytics is configured."""
        return bool(self.posthog_api_key)

    @property
    def has_meta_learning(self) -> bool:
        """Check if meta-learning is enabled.

        Meta-learning works with local LearningStore for pattern tracking.
        External bridges (Code Atlas, Tech Diligence) are optional and
        provide additional signals when configured.
        """
        return self.meta_learning_enabled

    @property
    def has_external_bridges(self) -> bool:
        """Check if external meta-learning bridges are configured."""
        return bool(self.code_atlas_url or self.tech_diligence_url)

    def get_project_path(self, forge_root: Path | None = None) -> Path | None:
        """Get full project path.

        Args:
            forge_root: Optional FORGE root directory

        Returns:
            Full path to project or None if not configured
        """
        if self.project_dir:
            return self.project_dir

        if forge_root and self.domain and self.project:
            return forge_root / self.domain / self.project

        return None


# =========================================================================
# Singleton Access
# =========================================================================


@lru_cache(maxsize=1)
def get_settings() -> ForgeSettings:
    """Get cached ForgeSettings singleton.

    Returns:
        ForgeSettings instance (cached)
    """
    return ForgeSettings()


def clear_settings_cache() -> None:
    """Clear the settings cache (useful for testing)."""
    get_settings.cache_clear()
