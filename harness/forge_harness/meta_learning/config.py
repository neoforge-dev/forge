"""
Configuration for the FORGE Meta-Learning System.

Supports loading from YAML files with environment variable overrides.

Environment Variables:
    CODE_ATLAS_API_URL: Base URL for Code Atlas service
    CODE_ATLAS_API_KEY: API key for Code Atlas authentication
    CODE_ATLAS_ENABLED: Set to "false" to disable Code Atlas

    TECH_DILIGENCE_API_URL: Base URL for TechDiligence service
    TECH_DILIGENCE_API_KEY: API key for TechDiligence authentication
    TECH_DILIGENCE_ENABLED: Set to "false" to disable TechDiligence
"""

import os
from pathlib import Path

from pydantic import BaseModel, Field


def _get_bool_env(key: str, default: bool = True) -> bool:
    """Get a boolean value from environment variable."""
    val = os.environ.get(key, "").lower()
    if val in ("false", "0", "no", "off"):
        return False
    if val in ("true", "1", "yes", "on"):
        return True
    return default


class CodeAtlasConfig(BaseModel):
    """Configuration for Code Atlas integration."""

    base_url: str = Field(
        default_factory=lambda: os.environ.get(
            "CODE_ATLAS_API_URL", "https://code-atlas-production.up.railway.app"
        ),
        description="Code Atlas API base URL (env: CODE_ATLAS_API_URL)",
    )
    api_key: str | None = Field(
        default_factory=lambda: os.environ.get("CODE_ATLAS_API_KEY"),
        description="API key for authentication (env: CODE_ATLAS_API_KEY)",
    )
    timeout_seconds: int = Field(30, ge=1, le=300, description="Request timeout")
    cache_ttl_seconds: int = Field(300, ge=0, description="Cache TTL (0 = disabled)")
    enabled: bool = Field(
        default_factory=lambda: _get_bool_env("CODE_ATLAS_ENABLED", True),
        description="Whether Code Atlas integration is enabled (env: CODE_ATLAS_ENABLED)",
    )


class TechDiligenceConfig(BaseModel):
    """Configuration for Tech Diligence integration."""

    base_url: str = Field(
        default_factory=lambda: os.environ.get("TECH_DILIGENCE_API_URL", "http://localhost:8081"),
        description="Tech Diligence API base URL (env: TECH_DILIGENCE_API_URL)",
    )
    api_key: str | None = Field(
        default_factory=lambda: os.environ.get("TECH_DILIGENCE_API_KEY"),
        description="API key for authentication (env: TECH_DILIGENCE_API_KEY)",
    )
    timeout_seconds: int = Field(60, ge=1, le=600, description="Request timeout")
    poll_interval_seconds: int = Field(5, ge=1, le=60, description="Job poll interval")
    cache_ttl_seconds: int = Field(3600, ge=0, description="Report cache TTL")
    enabled: bool = Field(
        default_factory=lambda: _get_bool_env("TECH_DILIGENCE_ENABLED", True),
        description="Whether Tech Diligence is enabled (env: TECH_DILIGENCE_ENABLED)",
    )


class LearningStoreConfig(BaseModel):
    """Configuration for the Learning Store."""

    storage_path: Path = Field(Path(".forge/learning"), description="Path to store learning data")
    patterns_file: str = Field("patterns.json", description="Patterns data file")
    decisions_file: str = Field("decisions.json", description="Decisions data file")
    max_records: int = Field(10000, ge=100, description="Max records before rotation")
    auto_persist: bool = Field(True, description="Auto-persist on each write")


class DecisionEngineConfig(BaseModel):
    """Configuration for the Decision Engine."""

    # Score thresholds for actions
    block_threshold: float = Field(
        0.3, ge=0.0, le=1.0, description="Aggregate score below this blocks"
    )
    human_review_threshold: float = Field(
        0.5, ge=0.0, le=1.0, description="Score below this requires human review"
    )
    caution_threshold: float = Field(
        0.7, ge=0.0, le=1.0, description="Score below this proceeds with caution"
    )

    # Confidence requirements
    min_confidence_for_proceed: float = Field(
        0.6, ge=0.0, le=1.0, description="Min confidence to proceed autonomously"
    )

    # Feature flags
    enable_pattern_learning: bool = Field(True, description="Learn from successful patterns")
    enable_decision_recording: bool = Field(True, description="Record decisions for analysis")


class MetaLearningConfig(BaseModel):
    """Root configuration for the Meta-Learning System."""

    code_atlas: CodeAtlasConfig = Field(default_factory=CodeAtlasConfig)
    tech_diligence: TechDiligenceConfig = Field(default_factory=TechDiligenceConfig)
    learning_store: LearningStoreConfig = Field(default_factory=LearningStoreConfig)
    decision_engine: DecisionEngineConfig = Field(default_factory=DecisionEngineConfig)

    # Global settings
    dry_run: bool = Field(False, description="If true, don't persist any data")
    verbose: bool = Field(False, description="Enable verbose logging")

    @classmethod
    def load_from_yaml(cls, path: Path) -> "MetaLearningConfig":
        """Load configuration from a YAML file."""
        import yaml

        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls(**data)

    @classmethod
    def default(cls) -> "MetaLearningConfig":
        """Return default configuration."""
        return cls()

    def save_to_yaml(self, path: Path) -> None:
        """Save configuration to a YAML file."""
        import yaml

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)


_config: MetaLearningConfig | None = None


def get_config() -> MetaLearningConfig:
    """Get the global meta-learning configuration."""
    global _config
    if _config is None:
        _config = MetaLearningConfig()
    return _config
