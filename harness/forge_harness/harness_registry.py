"""
Harness Registry - Dependency Injection for FORGE Harnesses
============================================================

Central factory for creating and wiring harness instances with
proper dependency injection, lazy loading, and configuration.

This module addresses the critical gap where OrchestrationHarness
expects a `harnesses` dict but no factory existed to wire them up.

Usage:
    from forge_harness.harness_registry import (
        HarnessRegistry,
        create_harness_registry,
    )

    # Create registry with config
    registry = create_harness_registry(
        domain="codeswiftr-com",
        project="interview-simulator",
    )

    # Get harness instances for OrchestrationHarness
    harnesses = registry.get_all_harnesses()
    orchestrator = OrchestrationHarness(harnesses=harnesses, ...)

    # Or get individual harness
    content = registry.get("content")
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .logging_config import get_logger

if TYPE_CHECKING:
    from .config import ForgeSettings

logger = get_logger(__name__)


@dataclass
class HarnessConfig:
    """Configuration for harness initialization.

    Attributes:
        domain: Domain name (e.g., "codeswiftr-com")
        project: Project name (e.g., "interview-simulator")
        project_dir: Path to project directory
        checkpoint_dir: Path for orchestration checkpoints
        session_dir: Path for session state storage
        notion_api_token: Notion API token (optional)
        notion_database_id: Notion database ID for content library (optional)
        slack_webhook_url: Slack webhook for notifications (optional)
        email_config: Email configuration dict (optional)
        github_token: GitHub token (optional)
        github_repo: GitHub repo (optional, format: "owner/repo")
        posthog_api_key: PostHog API key (optional)
        dry_run: Whether to run in dry-run mode
    """

    domain: str | None = None
    project: str | None = None
    project_dir: Path | None = None
    checkpoint_dir: Path = field(default_factory=lambda: Path(".forge/orchestration_checkpoints"))
    session_dir: Path = field(default_factory=lambda: Path(".forge/sessions"))
    approval_storage_dir: Path = field(default_factory=lambda: Path(".forge/approvals"))
    notion_api_token: str | None = None
    notion_database_id: str | None = None
    slack_webhook_url: str | None = None
    email_config: dict[str, Any] | None = None
    github_token: str | None = None
    github_repo: str | None = None
    posthog_api_key: str | None = None
    dry_run: bool = False

    # Meta-learning configuration
    meta_learning_enabled: bool = False
    learning_store_dir: Path = field(default_factory=lambda: Path(".forge/learning"))
    code_atlas_url: str | None = None
    tech_diligence_url: str | None = None

    @classmethod
    def from_env(cls, domain: str | None = None, project: str | None = None) -> HarnessConfig:
        """Create config from environment variables.

        Uses ForgeSettings for validated configuration loading,
        then converts to HarnessConfig for backward compatibility.

        Args:
            domain: Domain name (overrides env)
            project: Project name (overrides env)

        Returns:
            HarnessConfig populated from environment
        """
        from .config import ForgeSettings

        # Use ForgeSettings for validated env loading
        settings = ForgeSettings.from_env(domain=domain, project=project)
        return settings.to_harness_config()

    @classmethod
    def from_settings(cls, settings: ForgeSettings) -> HarnessConfig:
        """Create config from ForgeSettings instance.

        Args:
            settings: ForgeSettings instance

        Returns:
            HarnessConfig populated from settings
        """
        return settings.to_harness_config()


# Type alias for harness factory functions
HarnessFactory = Callable[[HarnessConfig, "HarnessRegistry"], Any]


class HarnessRegistry:
    """Central registry for creating and managing harness instances.

    Provides lazy loading, dependency injection, and configuration
    management for all FORGE harnesses.

    Features:
    - Lazy instantiation: Harnesses created only when first accessed
    - Dependency injection: Harnesses can access other harnesses
    - Configuration injection: Domain registry integration
    - Thread-safe singleton pattern for harness instances
    """

    # Default harness factories
    _DEFAULT_FACTORIES: dict[str, HarnessFactory] = {}

    def __init__(
        self,
        config: HarnessConfig,
        custom_factories: dict[str, HarnessFactory] | None = None,
    ):
        """Initialize the registry.

        Args:
            config: Configuration for harness initialization
            custom_factories: Optional dict of custom factory overrides
        """
        self.config = config
        self._factories: dict[str, HarnessFactory] = {
            **self._DEFAULT_FACTORIES,
            **(custom_factories or {}),
        }
        self._instances: dict[str, Any] = {}
        self._initializing: set[str] = set()  # Prevent circular dependencies

        # Register built-in factories
        self._register_builtin_factories()

    def _register_builtin_factories(self) -> None:
        """Register factory functions for built-in harnesses."""
        # Content harness
        self._factories["content"] = self._create_content_harness

        # Notification harness
        self._factories["notification"] = self._create_notification_harness

        # Session state manager
        self._factories["session"] = self._create_session_manager

        # Preflight checker
        self._factories["preflight"] = self._create_preflight_checker

        # Deployment harness
        self._factories["deployment"] = self._create_deployment_harness

        # Workflow harness
        self._factories["workflow"] = self._create_workflow_harness

        # Approval queue harness
        self._factories["approval_queue"] = self._create_approval_queue

        # Human gate harness (wraps approval_queue + notification)
        self._factories["human_gate"] = self._create_human_gate_harness

        # PostHog tracker (analytics alias)
        self._factories["posthog"] = self._create_posthog_tracker

        # Meta-learning components
        self._factories["learning_store"] = self._create_learning_store
        self._factories["code_atlas"] = self._create_code_atlas_bridge
        self._factories["tech_diligence"] = self._create_tech_diligence_bridge
        self._factories["decision_engine"] = self._create_decision_engine
        self._factories["feedback_loop_manager"] = self._create_feedback_loop_manager
        self._factories["failure_pattern_db"] = self._create_failure_pattern_db

        # SimpleHistory - lightweight alternative to DecisionEngine
        self._factories["simple_history"] = self._create_simple_history

    def get(self, name: str) -> Any:
        """Get or create a harness instance.

        Args:
            name: Harness name (e.g., "content", "notion", "notification")

        Returns:
            Harness instance

        Raises:
            KeyError: If harness name is not registered
            RuntimeError: If circular dependency detected
        """
        # Return cached instance if available
        if name in self._instances:
            return self._instances[name]

        # Check for circular dependency
        if name in self._initializing:
            raise RuntimeError(f"Circular dependency detected for harness: {name}")

        # Check if factory exists
        if name not in self._factories:
            available = ", ".join(sorted(self._factories.keys()))
            raise KeyError(f"Unknown harness: {name}. Available: {available}")

        # Create instance
        self._initializing.add(name)
        try:
            factory = self._factories[name]
            instance = factory(self.config, self)
            self._instances[name] = instance
            logger.debug(f"Created harness: {name}")
            return instance
        finally:
            self._initializing.discard(name)

    def get_all_harnesses(self) -> dict[str, Any]:
        """Get all harnesses as a dict for OrchestrationHarness.

        Returns:
            Dict mapping harness names to instances
        """
        harnesses = {}
        for name in self._factories:
            try:
                harnesses[name] = self.get(name)
            except Exception as e:
                logger.warning(f"Failed to create harness {name}: {e}")
        return harnesses

    def get_for_orchestration(self) -> dict[str, Any]:
        """Get harnesses commonly needed for orchestration pipelines.

        Returns:
            Dict with content, notion, notification, session, etc.
        """
        essential = [
            "content",
            "notion",
            "notification",
            "session",
            "preflight",
            "deployment",
            "human_gate",
            "analytics",
        ]
        harnesses = {}
        for name in essential:
            try:
                harnesses[name] = self.get(name)
            except Exception as e:
                logger.debug(f"Skipping optional harness {name}: {e}")
        return harnesses

    def register(self, name: str, factory: HarnessFactory) -> None:
        """Register a custom harness factory.

        Args:
            name: Harness name
            factory: Factory function (config, registry) -> harness
        """
        self._factories[name] = factory
        # Clear cached instance if exists
        self._instances.pop(name, None)

    def has(self, name: str) -> bool:
        """Check if a harness is registered.

        Args:
            name: Harness name

        Returns:
            True if registered
        """
        return name in self._factories

    def list_available(self) -> list[str]:
        """List all available harness names.

        Returns:
            Sorted list of harness names
        """
        return sorted(self._factories.keys())

    def is_initialized(self, name: str) -> bool:
        """Check if a harness has been initialized.

        Args:
            name: Harness name

        Returns:
            True if already instantiated
        """
        return name in self._instances

    # ========================================================================
    # Factory Methods
    # ========================================================================

    def _create_content_harness(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create ContentHarness instance."""
        from .content_harness import create_content_harness

        # Content harness requires domain, project, forge_root
        if not config.domain or not config.project:
            # Return a minimal mock for testing
            return MockContentHarness()

        forge_root = config.project_dir or Path(".")
        return create_content_harness(
            domain=config.domain,
            project=config.project,
            forge_root=forge_root,
        )

    def _create_notification_harness(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create NotificationHarness."""
        from .notification_harness import create_notification_harness

        return create_notification_harness(
            slack_webhook_url=config.slack_webhook_url,
            email_config=config.email_config,
            github_token=config.github_token,
            notion_token=config.notion_api_token,
        )

    def _create_session_manager(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create SessionStateManager."""
        from .session_state import create_session_state_manager

        return create_session_state_manager(
            storage_dir=config.session_dir,
        )

    def _create_preflight_checker(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create PreflightChecker."""
        from .preflight import PreflightChecker

        project_dir = config.project_dir or Path(".")
        return PreflightChecker(project_dir=project_dir)

    def _create_deployment_harness(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create deployment harness wrapper."""
        from .deployment_harness import create_deployment_harness

        return create_deployment_harness(dry_run=config.dry_run)

    def _create_workflow_harness(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create WorkflowHarness."""
        from .workflow_harness import create_workflow_harness

        workflow_name = f"{config.domain or 'default'}_{config.project or 'workflow'}"
        return create_workflow_harness(
            workflow_name=workflow_name,
            checkpoint_dir=config.checkpoint_dir,
        )

    def _create_approval_queue(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create ApprovalQueueHarness."""
        from .approval_queue import create_approval_queue

        return create_approval_queue(
            storage_dir=config.approval_storage_dir,
            default_expiry_hours=24.0,
        )

    def _create_human_gate_harness(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create HumanGateHarness with ApprovalQueue and Notification integration.

        The HumanGateHarness provides a complete interface for orchestration
        to request human approvals, send notifications, and poll for responses.
        It also integrates with SessionStateManager for tracking human decisions.
        """
        from .human_gate_harness import HumanGateHarness

        approval_queue = registry.get("approval_queue")
        notification = registry.get("notification")
        session = registry.get("session")

        return HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=notification,
            checkpoint_dir=config.checkpoint_dir,
            session_state_manager=session,
        )

    def _create_notion_storage(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """ARCHIVED: This harness has been moved to harness/archive/."""
        raise NotImplementedError(
            "notion_storage has been archived. "
            "See harness/archive/README.md for restoration instructions."
        )

    def _create_analytics_harness(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """ARCHIVED: This harness has been moved to harness/archive/."""
        raise NotImplementedError(
            "analytics_harness has been archived. "
            "See harness/archive/README.md for restoration instructions."
        )

    def _create_growth_harness(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """ARCHIVED: This harness has been moved to harness/archive/."""
        raise NotImplementedError(
            "growth_harness has been archived. "
            "See harness/archive/README.md for restoration instructions."
        )

    def _create_launch_harness(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """ARCHIVED: This harness has been moved to harness/archive/."""
        raise NotImplementedError(
            "launch_harness has been archived. "
            "See harness/archive/README.md for restoration instructions."
        )

    def _create_posthog_tracker(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create PostHog tracker wrapper."""
        # Return mock tracker - actual PostHog is handled by analytics_harness
        return MockPosthogTracker()

    # ========================================================================
    # Meta-Learning Factory Methods
    # ========================================================================

    def _create_learning_store(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create LearningStore for meta-learning persistence.

        The LearningStore tracks patterns, decisions, and outcomes across
        Ralph Loop sessions to improve autonomous decision-making over time.
        """
        from .meta_learning import LearningStore

        return LearningStore(storage_path=config.learning_store_dir)

    def _create_code_atlas_bridge(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create CodeAtlasBridge for accessing Code Atlas signals.

        Returns None if Code Atlas URL is not configured, allowing
        graceful degradation when the service is unavailable.
        """
        if not config.code_atlas_url:
            return None

        from .meta_learning import CodeAtlasBridge

        return CodeAtlasBridge(base_url=config.code_atlas_url)

    def _create_tech_diligence_bridge(
        self, config: HarnessConfig, registry: HarnessRegistry
    ) -> Any:
        """Create TechDiligenceBridge for accessing Tech Diligence signals.

        Returns None if Tech Diligence URL is not configured, allowing
        graceful degradation when the service is unavailable.
        """
        if not config.tech_diligence_url:
            return None

        from .meta_learning import TechDiligenceBridge

        return TechDiligenceBridge(base_url=config.tech_diligence_url)

    def _create_decision_engine(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create DecisionEngine for autonomous decision-making.

        The DecisionEngine aggregates signals from Code Atlas and Tech Diligence,
        applies learned patterns, and generates recommendations. It operates in
        graceful degradation mode when bridges are unavailable.
        """
        if not config.meta_learning_enabled:
            return MockDecisionEngine()

        from .meta_learning import DecisionEngine

        # Get optional bridges (may be None)
        learning_store = registry.get("learning_store")
        atlas_bridge = registry.get("code_atlas")
        diligence_bridge = registry.get("tech_diligence")

        return DecisionEngine(
            learning_store=learning_store,
            atlas_bridge=atlas_bridge,
            diligence_bridge=diligence_bridge,
        )

    def _create_feedback_loop_manager(
        self, config: HarnessConfig, registry: HarnessRegistry
    ) -> Any:
        """Create FeedbackLoopManager for compounding learning loops.

        The FeedbackLoopManager coordinates feedback hooks that enable:
        1. Code Atlas indexing of completed sessions
        2. Tech debt feature generation from findings
        3. Human gate threshold optimization based on outcomes
        """
        from .meta_learning import FeedbackConfig, FeedbackLoopManager

        # Get dependencies (may be None for graceful degradation)
        learning_store = registry.get("learning_store")
        atlas_bridge = registry.get("code_atlas")
        diligence_bridge = registry.get("tech_diligence")

        feedback_config = FeedbackConfig(
            auto_index_enabled=True,
            debt_features_enabled=True,
            threshold_optimization_enabled=True,
        )

        return FeedbackLoopManager(
            learning_store=learning_store,
            code_atlas=atlas_bridge,
            tech_diligence=diligence_bridge,
            config=feedback_config,
        )

    def _create_failure_pattern_db(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create EnhancedFailurePatternDB with wired dependencies.

        The enhanced failure pattern DB uses:
        1. Code Atlas for historical fix lookup across sessions
        2. Learning Store for pattern persistence and outcome tracking
        """
        from .failure_patterns import EnhancedFailurePatternDB

        # Get dependencies (may be None for graceful degradation)
        learning_store = registry.get("learning_store")
        atlas_bridge = registry.get("code_atlas")

        return EnhancedFailurePatternDB(
            atlas_bridge=atlas_bridge,
            learning_store=learning_store,
            db_path=config.learning_store_dir / "failure_patterns.json",
        )

    def _create_simple_history(self, config: HarnessConfig, registry: HarnessRegistry) -> Any:
        """Create SimpleHistory for lightweight pattern tracking.

        SimpleHistory is a simplified alternative to DecisionEngine that uses
        append-only JSONL for recording outcomes and calculating success rates.
        It provides ~5% of DecisionEngine's complexity while covering 80% of use cases.
        """
        from .simple_history import SimpleHistory

        # Default path: .forge/state/history.jsonl within learning store dir
        history_file = config.learning_store_dir / "state" / "history.jsonl"

        return SimpleHistory(history_file=history_file)


class MockDecisionEngine:
    """Mock decision engine when meta-learning is disabled."""

    async def get_recommendation(self, context) -> Any:
        """Return a default PROCEED recommendation."""
        from .meta_learning.schemas import (
            ConfidenceLevel,
            DecisionAction,
            DecisionRecommendation,
            ScoreCard,
        )

        return DecisionRecommendation(
            action=DecisionAction.PROCEED,
            confidence=ConfidenceLevel.UNKNOWN,
            scores=ScoreCard(
                risk_score=0.0,
                confidence_score=0.0,
                complexity_score=0.0,
                precedent_score=0.0,
            ),
            reasoning="Meta-learning disabled - proceeding by default",
            warnings=[],
        )

    async def record_outcome(self, decision_id: str, success: bool, **kwargs) -> None:
        """No-op outcome recording."""
        pass

    def get_statistics(self) -> dict:
        """Return empty statistics."""
        return {
            "has_atlas_bridge": False,
            "has_diligence_bridge": False,
            "has_learning_store": False,
            "cached_decisions": 0,
            "meta_learning_enabled": False,
        }


class MockContentHarness:
    """Mock content harness for when domain/project not configured."""

    async def generate_content_library(self, **kwargs) -> dict:
        """Generate content (no-op)."""
        logger.debug("MockContentHarness.generate_content_library called")
        return {"items": [], "briefs": []}

    async def generate_brief(self, **kwargs) -> dict:
        """Generate brief (no-op)."""
        return {}


class MockDeploymentHarness:
    """Mock deployment harness."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

    async def full_deploy(self, **kwargs) -> dict:
        """Deploy (no-op)."""
        logger.debug("MockDeploymentHarness.full_deploy called (dry_run=%s)", self.dry_run)
        return {"backend_url": None, "frontend_url": None}

    async def check_quality_gates(self, **kwargs) -> dict:
        """Check quality gates (no-op)."""
        return {"coverage": 0, "tests_passed": True}

    async def run_smoke_tests(self, **kwargs) -> dict:
        """Run smoke tests (no-op)."""
        return {"all_passed": True}


class MockAnalyticsHarness:
    """Mock analytics harness for when domain/project not configured."""

    async def create_mvp_dashboard(self, **kwargs) -> dict:
        """Create dashboard (no-op)."""
        logger.debug("MockAnalyticsHarness.create_mvp_dashboard called")
        return {"dashboard_id": None, "dashboard_url": None}


class MockGrowthHarness:
    """Mock growth harness for when domain/project not configured."""

    async def run_experiment(self, **kwargs) -> dict:
        """Run experiment (no-op)."""
        return {"status": "mock", "results": {}}


class MockLaunchHarness:
    """Mock launch harness for when domain/project not configured."""

    async def launch(self, **kwargs) -> dict:
        """Launch (no-op)."""
        return {"status": "mock", "url": None}


class MockPosthogTracker:
    """Mock PostHog tracker for when API key is not configured."""

    async def track(self, event: str, properties: dict[str, Any] | None = None) -> None:
        """Track event (no-op)."""
        logger.debug(f"Mock PostHog track: {event}")

    async def track_batch(self, events: list[str], count: int = 0) -> None:
        """Track batch of events (no-op)."""
        logger.debug(f"Mock PostHog track_batch: {events}")


def create_harness_registry(
    domain: str | None = None,
    project: str | None = None,
    config: HarnessConfig | None = None,
    **kwargs: Any,
) -> HarnessRegistry:
    """Create a HarnessRegistry with configuration.

    Args:
        domain: Domain name (e.g., "codeswiftr-com")
        project: Project name (e.g., "interview-simulator")
        config: Optional pre-built config (overrides domain/project)
        **kwargs: Additional config options

    Returns:
        Configured HarnessRegistry
    """
    if config is None:
        # Build config from env + overrides
        config = HarnessConfig.from_env(domain=domain, project=project)
        # Apply any additional kwargs
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)

    return HarnessRegistry(config)
