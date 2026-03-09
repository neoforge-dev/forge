"""
Ralph Loop Harness - Autonomous Feature Implementation
======================================================

Implements the Ralph Wiggum Loop pattern for autonomous
iterative feature development.

The loop:
1. Reads features.json for next pending feature
2. Implements the feature (via agent or orchestration)
3. Runs tests
4. Updates feature status
5. Repeats until all features pass or max iterations reached

Usage:
    from forge_harness.ralph_loop import (
        RalphLoopHarness,
        FeatureSpec,
        LoopConfig,
        create_ralph_loop,
        create_ralph_loop_from_registry,  # Auto-wires meta-learning
    )

    # Simple usage (no meta-learning)
    loop = create_ralph_loop(
        features_path=Path("features.json"),
        max_iterations=100,
    )

    # With meta-learning (auto-wires DecisionEngine, ApprovalQueue)
    loop = create_ralph_loop_from_registry(
        features_path=Path("features.json"),
        domain="codeswiftr-com",
        project="interview-simulator",
    )

    result = await loop.run()
"""

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .daily_notes import EventType, get_daily_notes_service
from .logging_config import get_logger
from .memory_flush import ContextMonitor, MemoryFlushManager
from .simple_history import SimpleHistory

if TYPE_CHECKING:
    from .agent_telemetry import AgentTelemetry
    from .approval_queue import ApprovalQueueHarness
    from .config import ForgeSettings
    from .meta_learning import DecisionEngine
    from .meta_learning.bridges.code_atlas import CodeAtlasBridge
    from .meta_learning.schemas import DecisionContext, DecisionRecommendation

logger = get_logger(__name__)


# =============================================================================
# Feature Models
# =============================================================================


class FeatureStatus(Enum):
    """Status of a feature in the loop."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSING = "passing"
    FAILING = "failing"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    COMPLETED = "completed"  # Added: features marked complete in features.json


@dataclass
class FeatureSpec:
    """Specification for a feature to implement.

    Attributes:
        id: Unique feature identifier (e.g., "auth-001")
        name: Human-readable name
        description: Detailed description
        status: Current status
        priority: Priority level (critical, high, medium, low)
        acceptance_criteria: List of acceptance criteria
        depends_on: List of feature IDs this depends on
        tests: List of test names that verify this feature
        estimated_tokens: Estimated tokens to implement
        attempts: Number of implementation attempts
        last_error: Last error message if failing
        files_to_create: List of files to create for this feature
        files_to_modify: List of files to modify for this feature
        category: Feature category for history matching (e.g., "authentication", "api")
        tags: Feature tags for classification
    """

    id: str
    name: str
    description: str
    status: FeatureStatus = FeatureStatus.PENDING
    priority: str = "medium"
    acceptance_criteria: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    estimated_tokens: int = 4000
    attempts: int = 0
    last_error: str | None = None
    files_to_create: list[str] = field(default_factory=list)
    files_to_modify: list[str] = field(default_factory=list)
    category: str | None = (
        None  # Feature category for history matching (e.g., "authentication", "api")
    )
    tags: list[str] = field(default_factory=list)  # Feature tags for classification

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority,
            "acceptance_criteria": self.acceptance_criteria,
            "depends_on": self.depends_on,
            "tests": self.tests,
            "estimated_tokens": self.estimated_tokens,
            "attempts": self.attempts,
            "last_error": self.last_error,
        }
        # Only include files lists if non-empty
        if self.files_to_create:
            result["files_to_create"] = self.files_to_create
        if self.files_to_modify:
            result["files_to_modify"] = self.files_to_modify
        # Include category and tags if present
        if self.category:
            result["category"] = self.category
        if self.tags:
            result["tags"] = self.tags
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeatureSpec":
        """Create from dict.

        Accepts either 'name' or 'title' for the feature name.
        Accepts either 'depends_on' or 'dependencies' for dependencies.
        """
        # Support both 'name' and 'title' fields
        name = data.get("name") or data.get("title")
        if not name:
            raise KeyError("Feature must have 'name' or 'title' field")

        # Support both 'depends_on' and 'dependencies' fields
        depends_on = data.get("depends_on") or data.get("dependencies", [])

        return cls(
            id=data["id"],
            name=name,
            description=data["description"],
            status=FeatureStatus(data.get("status", "pending")),
            priority=data.get("priority", "medium"),
            acceptance_criteria=data.get("acceptance_criteria", []),
            depends_on=depends_on,
            tests=data.get("tests", []),
            estimated_tokens=data.get("estimated_tokens", 4000),
            attempts=data.get("attempts", 0),
            last_error=data.get("last_error"),
            files_to_create=data.get("files_to_create", []),
            files_to_modify=data.get("files_to_modify", []),
            category=data.get("category"),
            tags=data.get("tags", []),
        )


# =============================================================================
# Loop Configuration
# =============================================================================


@dataclass
class LoopConfig:
    """Configuration for Ralph loop execution.

    Attributes:
        domain: Domain name for meta-learning context (e.g., "codeswiftr-com")
        project: Project name for meta-learning context (e.g., "interview-simulator")
        max_iterations: Maximum loop iterations
        max_failures_per_feature: Max failures before marking blocked
        checkpoint_interval: Save checkpoint every N iterations
        test_command: Command to run tests
        lint_command: Optional command to run lint checks
        timeout_seconds: Timeout per feature implementation
        dry_run: If True, don't actually implement features
        approval_timeout_hours: Timeout for human approval requests
        history_boost_high: Priority boost for >high_threshold success
        history_penalty_low: Priority penalty for <low_threshold success
        history_threshold_high: Success rate above this gets boost
        history_threshold_low: Success rate below this gets penalty
        history_min_samples: Minimum records before applying boost/penalty
    """

    domain: str | None = None
    project: str | None = None
    max_iterations: int = 100
    max_failures_per_feature: int = 5
    checkpoint_interval: int = 10
    test_command: str = "uv run pytest tests/ -v --tb=short -x"
    lint_command: str | None = None
    timeout_seconds: int = 3600
    dry_run: bool = False
    approval_timeout_hours: float = 24.0
    working_dir: Path | None = None
    history_boost_high: float = -0.5  # Priority boost for >high_threshold success
    history_penalty_low: float = 0.5  # Priority penalty for <low_threshold success
    history_threshold_high: float = 0.7  # Success rate above this gets boost
    history_threshold_low: float = 0.3  # Success rate below this gets penalty
    history_min_samples: int = 3  # Minimum records before applying boost/penalty
    # Cross-domain pattern propagation (P2/Loop 3)
    cross_domain_enabled: bool = True
    cross_domain_boost: float = -0.15  # Smaller than same-domain (-0.5)
    cross_domain_min_success: float = 0.8  # Only propagate high-success patterns
    cross_domain_min_samples: int = 5

    @classmethod
    def from_env(
        cls,
        domain: str | None = None,
        project: str | None = None,
    ) -> "LoopConfig":
        """Create LoopConfig from environment variables via ForgeSettings.

        Args:
            domain: Override domain from environment
            project: Override project from environment

        Returns:
            LoopConfig populated from environment/ForgeSettings
        """
        from .config import ForgeSettings

        settings = ForgeSettings.from_env(domain=domain, project=project)
        return settings.to_loop_config()

    @classmethod
    def from_settings(cls, settings: "ForgeSettings") -> "LoopConfig":
        """Create LoopConfig from ForgeSettings.

        Args:
            settings: ForgeSettings instance

        Returns:
            LoopConfig populated from settings
        """
        return settings.to_loop_config()


@dataclass
class LoopResult:
    """Result of Ralph loop execution.

    Attributes:
        success: Whether all features passed
        iterations: Number of iterations executed
        features_completed: Number of features that passed
        features_blocked: Number of features blocked
        features_remaining: Number of features still pending
        total_tokens: Estimated total tokens used
        duration_seconds: Total execution time
        checkpoint_path: Path to final checkpoint
    """

    success: bool
    iterations: int
    features_completed: int
    features_blocked: int
    features_remaining: int
    total_tokens: int
    duration_seconds: float
    checkpoint_path: Path | None = None


# =============================================================================
# Feature Store
# =============================================================================


class FeatureStore:
    """Manages feature state persistence.

    Reads and writes features.json with atomic updates.
    """

    def __init__(self, features_path: Path):
        """Initialize feature store.

        Args:
            features_path: Path to features.json
        """
        self.features_path = Path(features_path)
        self._features: dict[str, FeatureSpec] = {}

    def load(self) -> list[FeatureSpec]:
        """Load features from file.

        Supports two formats:
        - Wrapper format: {"features": [...], "version": "1.0"}
        - Plain list format: [{"id": "...", ...}, ...]

        Returns:
            List of FeatureSpec objects sorted by priority
        """
        if not self.features_path.exists():
            logger.warning(f"Features file not found: {self.features_path}")
            return []

        try:
            data = json.loads(self.features_path.read_text())

            # Handle both formats: plain list or wrapper object with "features" key
            if isinstance(data, list):
                features_data = data
            elif isinstance(data, dict):
                features_data = data.get("features", [])
            else:
                logger.warning(f"Unexpected JSON type in {self.features_path}: {type(data)}")
                features_data = []

            # Parse each feature, skipping invalid entries
            for f_data in features_data:
                if not isinstance(f_data, dict):
                    logger.warning(f"Skipping non-dict feature entry: {type(f_data)}")
                    continue
                try:
                    spec = FeatureSpec.from_dict(f_data)
                    self._features[spec.id] = spec
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(f"Skipping invalid feature entry: {e}")
                    continue

            # Sort by priority
            priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            sorted_features = sorted(
                self._features.values(),
                key=lambda f: priority_order.get(f.priority, 4),
            )

            logger.info(f"Loaded {len(sorted_features)} features from {self.features_path}")
            return sorted_features

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse features JSON: {e}")
            return []

    def save(self) -> None:
        """Save features to file atomically."""
        # Build output dict
        features_list = [f.to_dict() for f in self._features.values()]
        output = {
            "version": "1.0",
            "features": features_list,
        }

        # Write atomically: temp file then rename
        temp_path = self.features_path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(json.dumps(output, indent=2))
            temp_path.rename(self.features_path)
            logger.info(f"Saved {len(features_list)} features to {self.features_path}")
        except OSError as e:
            logger.error(f"Failed to save features: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    def get(self, feature_id: str) -> FeatureSpec | None:
        """Get feature by ID.

        Args:
            feature_id: Feature ID

        Returns:
            FeatureSpec or None if not found
        """
        return self._features.get(feature_id)

    def update(self, feature: FeatureSpec) -> None:
        """Update feature in store.

        Args:
            feature: Updated feature
        """
        self._features[feature.id] = feature

    async def get_next_pending(
        self,
        simple_history: SimpleHistory | None = None,
        recent_success_actions: set[str] | None = None,
        config: LoopConfig | None = None,
        feature_type_extractor: Any = None,
        code_atlas_bridge: "CodeAtlasBridge | None" = None,
    ) -> FeatureSpec | None:
        """Get next pending or failing feature respecting dependencies AND history.

        If simple_history is provided, boosts priority of features matching
        successful action patterns (compounding learning).

        If code_atlas_bridge is provided, further boosts priority of features
        with similar solutions found in Code Atlas.

        Args:
            simple_history: Optional SimpleHistory for pattern-based prioritization
            recent_success_actions: Set of recently successful actions for extra boost
            config: Optional LoopConfig for history threshold/boost values
            feature_type_extractor: Optional callable(feature) -> str for feature type extraction
            code_atlas_bridge: Optional CodeAtlasBridge for solution pattern matching

        Returns:
            Next feature to implement, or None if none available
        """
        # Priority order
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}

        # Find pending/failing features with satisfied dependencies
        candidates = []
        for feature in self._features.values():
            # Consider PENDING or FAILING features (FAILING will be retried)
            if feature.status not in (FeatureStatus.PENDING, FeatureStatus.FAILING):
                continue

            # Check if all dependencies are passing or completed
            deps_satisfied = True
            for dep_id in feature.depends_on:
                dep = self._features.get(dep_id)
                if dep is None or dep.status not in (
                    FeatureStatus.PASSING,
                    FeatureStatus.COMPLETED,
                ):
                    deps_satisfied = False
                    break

            if deps_satisfied:
                candidates.append(feature)

        if not candidates:
            return None

        # Apply history-based priority boost if available
        if simple_history:
            # Use config values or defaults
            min_samples = config.history_min_samples if config else 3
            threshold_high = config.history_threshold_high if config else 0.7
            threshold_low = config.history_threshold_low if config else 0.3
            boost_high = config.history_boost_high if config else -0.5
            penalty_low = config.history_penalty_low if config else 0.5

            for feature in candidates:
                # Extract feature type for pattern matching
                if feature_type_extractor:
                    feature_type = feature_type_extractor(feature)
                else:
                    # Fallback to ID prefix
                    feature_type = feature.id.split("-")[0] if "-" in feature.id else "unknown"

                action = f"feature:{feature_type}"

                # Get domain from config (matches recording behavior)
                domain = config.domain if config and config.domain else "forge"

                # Get historical success rate (defaults to 0.5 if no history)
                success_rate = simple_history.get_success_rate(
                    domain=domain,  # Use actual domain, not hardcoded "forge"
                    action=action,
                    limit=10,
                )

                # Check if we have enough samples before applying boost/penalty
                matching_count = len(
                    simple_history._get_matching_records(domain, action, limit=100)
                )

                # Apply boost: high success = priority boost, low success = deprioritize
                history_boost = 0.0
                if matching_count < min_samples:
                    # Insufficient data, stay neutral - don't penalize new patterns
                    logger.debug(
                        "Insufficient history for %s (type=%s, samples=%d < min=%d)",
                        feature.id,
                        feature_type,
                        matching_count,
                        min_samples,
                    )
                elif success_rate > threshold_high:
                    history_boost = boost_high  # Boost priority (lower sort key)
                    logger.debug(
                        "Boosting %s (type=%s, success_rate=%.2f, threshold=%.2f, boost=%.2f)",
                        feature.id,
                        feature_type,
                        success_rate,
                        threshold_high,
                        boost_high,
                    )
                elif success_rate < threshold_low:
                    history_boost = penalty_low  # Deprioritize (higher sort key)
                    logger.debug(
                        "Deprioritizing %s (type=%s, success_rate=%.2f, threshold=%.2f, penalty=%.2f)",
                        feature.id,
                        feature_type,
                        success_rate,
                        threshold_low,
                        penalty_low,
                    )

                # Cross-domain pattern propagation (P2)
                if (
                    config
                    and config.cross_domain_enabled
                    and history_boost == 0.0  # No strong local signal
                    and matching_count < min_samples  # Insufficient local data
                ):
                    cross_rate, cross_count = simple_history.get_cross_domain_success_rate(
                        action=action,
                        exclude_domain=domain,
                        min_samples=config.cross_domain_min_samples,
                    )
                    if cross_count >= config.cross_domain_min_samples:
                        if cross_rate >= config.cross_domain_min_success:
                            history_boost = config.cross_domain_boost
                            logger.debug(
                                "Cross-domain boost for %s (type=%s, cross_rate=%.2f, count=%d)",
                                feature.id,
                                feature_type,
                                cross_rate,
                                cross_count,
                            )

                if recent_success_actions and action in recent_success_actions:
                    history_boost -= 0.25  # Extra boost for recent success
                    logger.debug(
                        "Recent success boost %s (type=%s)",
                        feature.id,
                        feature_type,
                    )
                feature._history_boost = history_boost
        else:
            # No history available, all features get neutral boost
            for feature in candidates:
                feature._history_boost = 0

        # Apply Code Atlas boost if available
        if code_atlas_bridge:
            for feature in candidates:
                try:
                    query = f"{feature.name}: {feature.description}"
                    # Search for similar solutions in Code Atlas
                    results = await code_atlas_bridge.search_solutions(query, limit=3)
                    if results:
                        # Boost priority if similar solutions found
                        feature._atlas_boost = -0.25
                        logger.debug(
                            "Atlas boost for %s: found %d similar solutions",
                            feature.id,
                            len(results),
                        )
                    else:
                        feature._atlas_boost = 0.0
                except Exception as e:
                    # Graceful fallback - don't block on Code Atlas errors
                    logger.debug(
                        "Code Atlas query failed for %s, continuing without boost: %s",
                        feature.id,
                        e,
                    )
                    feature._atlas_boost = 0.0
        else:
            # No Code Atlas available, all features get neutral boost
            for feature in candidates:
                feature._atlas_boost = 0.0

        # Sort by: FAILING first (retry), then by priority + history boost + atlas boost
        # This ensures we retry failing features before starting new ones,
        # while still applying history-based learning and Code Atlas pattern matching
        candidates.sort(
            key=lambda f: (
                0 if f.status == FeatureStatus.FAILING else 1,
                priority_order.get(f.priority, 4)
                + getattr(f, "_history_boost", 0)
                + getattr(f, "_atlas_boost", 0),
            )
        )
        return candidates[0]

    def get_stats(self) -> dict[str, int]:
        """Get feature status counts.

        Returns:
            Dict with counts per status
        """
        stats: dict[str, int] = {}
        for status in FeatureStatus:
            stats[status.value] = 0

        for feature in self._features.values():
            stats[feature.status.value] += 1

        return stats


# =============================================================================
# Ralph Loop Harness
# =============================================================================


class RalphLoopHarness:
    """Harness for running Ralph Wiggum loops.

    Coordinates feature implementation with test verification
    and state persistence. Optionally integrates with the Meta-Learning
    DecisionEngine for intelligent decision-making and approval flows.
    """

    def __init__(
        self,
        feature_store: FeatureStore,
        config: LoopConfig,
        orchestrator: Any | None = None,
        decision_engine: "DecisionEngine | None" = None,
        approval_queue: "ApprovalQueueHarness | None" = None,
        feedback_loop_manager: Any | None = None,
        code_atlas_bridge: "CodeAtlasBridge | None" = None,
        failure_pattern_db: Any | None = None,
        telemetry: "AgentTelemetry | None" = None,
        simple_history: SimpleHistory | None = None,
    ):
        """Initialize Ralph loop harness.

        Args:
            feature_store: Store for feature state
            config: Loop configuration
            orchestrator: Optional OrchestrationHarness for complex features
            decision_engine: Optional DecisionEngine for meta-learning integration
            approval_queue: Optional ApprovalQueueHarness for human review flow
            feedback_loop_manager: Optional FeedbackLoopManager for compounding learning
            code_atlas_bridge: Optional CodeAtlasBridge for pattern retrieval
            failure_pattern_db: Optional EnhancedFailurePatternDB for fix suggestions
            telemetry: Optional AgentTelemetry for Command Center integration
            simple_history: Optional SimpleHistory for lightweight outcome tracking
        """
        self.store = feature_store
        self.config = config
        self.orchestrator = orchestrator
        self.decision_engine = decision_engine
        self.approval_queue = approval_queue
        self.feedback_loop_manager = feedback_loop_manager
        self.code_atlas_bridge = code_atlas_bridge
        self.failure_pattern_db = failure_pattern_db
        self.telemetry = telemetry
        self.simple_history = simple_history
        self._iteration = 0
        self._start_time: datetime | None = None
        self._decision_ids: dict[str, str] = {}  # feature_id -> decision_id

        # Memory flush integration (MoltBot pattern) - default enabled
        forge_root = feature_store.features_path.parent
        self._memory_monitor = ContextMonitor(threshold=0.7)
        self._memory_manager = MemoryFlushManager(forge_root=forge_root)
        self._estimated_token_usage = 0  # Track approximate token usage
        logger.info("Memory flush enabled (threshold: 70%)")

    def _extract_feature_type(self, feature: FeatureSpec) -> str:
        """Extract feature type for history matching.

        Priority order:
        1. feature.category if set (e.g., "authentication", "api")
        2. First tag if tags exist
        3. Keyword-based inference from ID, name, and description
        4. ID prefix (existing behavior as fallback)
        5. If api_endpoint, stratify by complexity

        Keywords matched:
        - "test", "unit", "coverage", "pytest", "spec" → "testing"
        - "auth", "login", "oauth", "jwt", "token" → "authentication"
        - "api", "endpoint", "crud", "rest", "graphql" → "api_endpoint"

        API endpoint stratification:
        - External/webhook keywords → "api_endpoint:external"
        - Batch/import/export keywords → "api_endpoint:batch"
        - Database migrations in dependencies → "api_endpoint:stateful"
        - Service layer files → "api_endpoint:stateful"
        - Default → "api_endpoint:simple"

        Args:
            feature: Feature to extract type from

        Returns:
            Feature type string for history matching
        """

        def _normalize_type(value: str) -> str:
            value_lower = value.lower()
            if value_lower in ("auth", "authentication", "login", "oauth", "jwt", "token"):
                return "authentication"
            if value_lower in ("test", "tests", "testing", "spec", "specs", "qa"):
                return "testing"
            if value_lower in (
                "api",
                "api_endpoint",
                "endpoint",
                "endpoints",
                "crud",
                "graphql",
                "rest",
            ):
                return "api_endpoint"
            return value

        if feature.category:
            base_type = _normalize_type(feature.category)
        elif feature.tags:
            base_type = _normalize_type(feature.tags[0])
        else:
            base_type = None

        name_desc = f"{feature.name} {feature.description}".lower()
        if base_type is None:
            if any(
                keyword in name_desc
                for keyword in ("test", "tests", "testing", "spec", "unit", "coverage", "pytest")
            ):
                base_type = "testing"
            elif any(
                keyword in name_desc for keyword in ("auth", "login", "oauth", "jwt", "token")
            ):
                base_type = "authentication"
            elif any(
                keyword in name_desc
                for keyword in ("api", "endpoint", "crud", "graphql", "rest", "webhook")
            ):
                base_type = "api_endpoint"
            else:
                base_type = _normalize_type(
                    feature.id.split("-")[0] if "-" in feature.id else feature.id
                )

        # Apply stratification for api_endpoint types
        if base_type != "api_endpoint":
            return base_type

        # Stratify API endpoints by complexity
        description = feature.description.lower() if feature.description else ""
        batch_keywords = ("batch", "import", "export")
        external_keywords = ("external", "webhook")

        if any(keyword in description for keyword in external_keywords):
            return "api_endpoint:external"
        if any(keyword in description for keyword in batch_keywords):
            return "api_endpoint:batch"

        depends_on = [dep.lower() for dep in feature.depends_on]
        if any("migration" in dep or "migrations" in dep or "alembic" in dep for dep in depends_on):
            return "api_endpoint:stateful"

        service_markers = (
            "/service/",
            "/services/",
            "service.py",
            "services.py",
            "service_layer",
        )
        for path in feature.files_to_modify:
            lower_path = path.lower()
            if any(marker in lower_path for marker in service_markers):
                return "api_endpoint:stateful"

        return "api_endpoint:simple"

    async def _select_next_feature(self) -> FeatureSpec | None:
        """Select next feature using SimpleHistory and Code Atlas for compounding."""
        recent_success_actions: set[str] | None = None
        if self.simple_history:
            recent = self.simple_history.get_recent(limit=10)
            recent_success_actions = {
                record.get("action")
                for record in recent
                if record.get("success") is True and record.get("action")
            }

        return await self.store.get_next_pending(
            simple_history=self.simple_history,
            recent_success_actions=recent_success_actions,
            config=self.config,
            feature_type_extractor=self._extract_feature_type,
            code_atlas_bridge=self.code_atlas_bridge,
        )

    async def run(self) -> LoopResult:
        """Run the Ralph loop until completion or max iterations.

        Returns:
            LoopResult with execution stats
        """
        self._start_time = datetime.now(UTC)
        total_tokens = 0
        checkpoint_path: Path | None = None

        # Register agent with Command Center
        if self.telemetry:
            await self.telemetry.register()
            await self.telemetry.start_heartbeat_loop()

        # Load features
        self.store.load()
        logger.info(f"Starting Ralph loop with max {self.config.max_iterations} iterations")

        # Main loop
        while self._iteration < self.config.max_iterations:
            self._iteration += 1
            stats = self.store.get_stats()

            # Check for completion (all passing or blocked, none pending/failing/in_progress)
            if stats["pending"] == 0 and stats["in_progress"] == 0 and stats["failing"] == 0:
                logger.info("All features completed!")
                break

            # Get next feature (with history-based and Code Atlas prioritization if available)
            feature = await self._select_next_feature()
            if feature is None:
                # No more features available (blocked or all done)
                remaining = stats["pending"] + stats["failing"]
                if stats["blocked"] > 0 and remaining == 0:
                    logger.warning(f"All remaining features blocked: {stats['blocked']}")
                    break
                elif remaining > 0:
                    # Features pending/failing but dependencies not met
                    logger.warning(f"Features have unsatisfied dependencies: {remaining} waiting")
                    break
                else:
                    logger.info("No more features to process")
                    break

            logger.info(f"[{self._iteration}] Processing: {feature.id} - {feature.name}")

            # Mark as in progress
            feature.status = FeatureStatus.IN_PROGRESS
            feature.attempts += 1
            self.store.update(feature)

            # Report progress to Command Center
            if self.telemetry:
                await self.telemetry.report_progress(
                    progress=int((stats["passing"] / max(1, len(self.store._features))) * 100),
                    current_task=f"Implementing {feature.id}: {feature.name}",
                )

            # Query DecisionEngine (if available)
            recommendation = await self._get_decision(feature)
            if recommendation:
                from .meta_learning.schemas import DecisionAction

                # Handle BLOCK recommendation
                if recommendation.action == DecisionAction.BLOCK:
                    feature.status = FeatureStatus.BLOCKED
                    feature.last_error = f"Blocked by DecisionEngine: {recommendation.reasoning}"
                    logger.warning(f"[{self._iteration}] BLOCKED by DecisionEngine: {feature.id}")
                    self.store.update(feature)
                    if not self.config.dry_run:
                        self.store.save()
                    continue

                # Handle HUMAN_REVIEW_REQUIRED recommendation
                if recommendation.action == DecisionAction.HUMAN_REVIEW_REQUIRED:
                    approved = await self._request_human_review(feature, recommendation)
                    if not approved:
                        feature.status = FeatureStatus.BLOCKED
                        feature.last_error = "Blocked: Human review rejected or timed out"
                        logger.warning(f"[{self._iteration}] BLOCKED by human review: {feature.id}")
                        self.store.update(feature)
                        if not self.config.dry_run:
                            self.store.save()
                        continue

            # Check context usage and trigger memory flush if needed
            await self._check_and_flush_memory(feature)

            # Track estimated token usage
            self._estimated_token_usage += feature.estimated_tokens

            # Implement feature (if not dry run)
            if not self.config.dry_run:
                await self._implement_feature(feature)
                total_tokens += feature.estimated_tokens

            # Run tests
            passed, error = await self._run_tests(feature)

            # Update status
            if passed:
                feature.status = FeatureStatus.PASSING
                feature.last_error = None
                logger.info(f"[{self._iteration}] PASSED: {feature.id}")

                # Log feature completion to daily notes
                try:
                    daily_notes = get_daily_notes_service()
                    test_info = (
                        f"{len(feature.tests)} tests" if feature.tests else "all tests passing"
                    )
                    file_paths = self._extract_file_paths(feature)
                    await daily_notes.log_event(
                        event_type=EventType.FEATURE_COMPLETION,
                        title=f"{feature.id}: {feature.name}",
                        details={
                            "tests": test_info,
                            "files": file_paths if file_paths else ["No specific files tracked"],
                            "attempts": feature.attempts,
                            "priority": feature.priority,
                        },
                    )
                except Exception as e:
                    logger.debug(f"Failed to log feature completion to daily notes: {e}")
            else:
                feature.last_error = error
                if feature.attempts >= self.config.max_failures_per_feature:
                    feature.status = FeatureStatus.BLOCKED
                    logger.warning(
                        f"[{self._iteration}] BLOCKED: {feature.id} after {feature.attempts} attempts"
                    )
                    # Report error to Command Center
                    if self.telemetry:
                        await self.telemetry.report_error(
                            error=f"Feature blocked: {feature.id}",
                            context={
                                "feature_id": feature.id,
                                "feature_name": feature.name,
                                "attempts": feature.attempts,
                                "last_error": error,
                            },
                        )
                else:
                    feature.status = FeatureStatus.FAILING
                    logger.warning(f"[{self._iteration}] FAILED: {feature.id} - {error}")

                    # Log feature failure to daily notes
                    try:
                        daily_notes = get_daily_notes_service()
                        error_summary = (
                            (error[:200] + "...") if error and len(error) > 200 else error
                        )
                        await daily_notes.log_event(
                            event_type=EventType.FEATURE_FAILURE,
                            title=f"{feature.id}: {feature.name}",
                            details={
                                "error": error_summary or "Unknown error",
                                "attempts": feature.attempts,
                                "max_attempts": self.config.max_failures_per_feature,
                                "priority": feature.priority,
                            },
                        )
                    except Exception as e:
                        logger.debug(f"Failed to log feature failure to daily notes: {e}")

                    # Report error to Command Center
                    if self.telemetry:
                        await self.telemetry.report_error(
                            error=f"Feature failed: {feature.id}",
                            context={
                                "feature_id": feature.id,
                                "feature_name": feature.name,
                                "attempts": feature.attempts,
                                "last_error": error,
                            },
                        )

            # Record outcome for meta-learning
            await self._record_outcome(feature, success=passed)

            # Adjust thresholds based on accumulated outcomes (closes feedback loop)
            if (
                self.decision_engine
                and self.decision_engine.learning_store
                and self.config.domain
                and self.config.project
            ):
                adjustment = self.decision_engine.learning_store.adjust_thresholds(
                    domain=self.config.domain,
                    project=self.config.project,
                )
                if adjustment:
                    logger.info(
                        f"Threshold adjustment: {adjustment.reason}",
                        extra={"adjustment": adjustment.new_thresholds},
                    )

                    # Log threshold adjustment to daily notes
                    try:
                        daily_notes = get_daily_notes_service()
                        await daily_notes.log_event(
                            event_type=EventType.THRESHOLD_ADJUSTMENT,
                            title=adjustment.reason,
                            details={
                                "domain": self.config.domain,
                                "project": self.config.project,
                                "new_thresholds": adjustment.new_thresholds,
                            },
                        )
                    except Exception as e:
                        logger.debug(f"Failed to log threshold adjustment to daily notes: {e}")

            # Index to Code Atlas for pattern learning (compounding loop)
            await self._index_feature_to_atlas(feature, success=passed)

            self.store.update(feature)
            if not self.config.dry_run:
                self.store.save()

            # Save checkpoint periodically (skip during dry run)
            if not self.config.dry_run and self._iteration % self.config.checkpoint_interval == 0:
                checkpoint_path = await self._save_checkpoint()

        # Final stats
        end_time = datetime.now(UTC)
        duration = (end_time - self._start_time).total_seconds()
        stats = self.store.get_stats()

        # Trigger feedback loops for compounding learning
        await self._trigger_feedback_loops(stats, duration)

        # Mark agent as complete
        if self.telemetry:
            await self.telemetry.complete(
                summary={
                    "features_completed": stats["passing"],
                    "features_blocked": stats["blocked"],
                    "features_remaining": stats["pending"] + stats["in_progress"],
                    "iterations": self._iteration,
                    "duration_seconds": duration,
                    "success": stats["pending"] == 0
                    and stats["failing"] == 0
                    and stats["blocked"] == 0,
                }
            )

        return LoopResult(
            success=stats["pending"] == 0 and stats["failing"] == 0 and stats["blocked"] == 0,
            iterations=self._iteration,
            features_completed=stats["passing"],
            features_blocked=stats["blocked"],
            features_remaining=stats["pending"] + stats["in_progress"],
            total_tokens=total_tokens,
            duration_seconds=duration,
            checkpoint_path=checkpoint_path,
        )

    async def _get_prior_patterns(self, feature: FeatureSpec) -> list:
        """Retrieve patterns from learning store matching this feature.

        Args:
            feature: Feature to get patterns for

        Returns:
            List of PatternRecord objects matching the feature type
        """
        from .meta_learning.learning_store import LearningStore

        try:
            # Get learning store
            learning_path = Path(".forge/learning")
            if not learning_path.exists():
                logger.debug("Learning store not found, skipping pattern retrieval")
                return []

            store = LearningStore(learning_path)

            # Extract feature type
            feature_type = self._extract_feature_type(feature)
            logger.debug(f"Feature {feature.id} classified as type: {feature_type}")

            # Query patterns matching this type
            # Pattern type should match feature_implementation with this feature type
            patterns = store.get_patterns_by_type(
                pattern_type=f"feature_implementation:{feature_type}"
            )

            # Filter by confidence threshold
            high_confidence_patterns = [
                p for p in patterns if p.confidence >= 0.7 and p.effectiveness >= 0.6
            ]

            if high_confidence_patterns:
                logger.info(
                    f"Found {len(high_confidence_patterns)} high-confidence patterns for {feature.id} "
                    f"(type: {feature_type})"
                )
            else:
                logger.debug(f"No high-confidence patterns found for {feature.id}")

            # Sort by effectiveness and limit to top 5
            high_confidence_patterns.sort(key=lambda p: p.effectiveness, reverse=True)
            return high_confidence_patterns[:5]

        except Exception as e:
            logger.warning(f"Failed to retrieve patterns for {feature.id}: {e}")
            return []

    async def _query_atlas_for_context(self, feature: FeatureSpec) -> str | None:
        """Query Code Atlas for relevant patterns and context before implementing.

        This enables the compounding loop: past sessions inform future implementations.

        Args:
            feature: Feature to get context for

        Returns:
            Context string to enrich the feature, or None if unavailable
        """
        if not self.code_atlas_bridge:
            return None

        try:
            # Build a query from the feature
            query = f"How to implement: {feature.name}. {feature.description}"

            # Add domain/project context
            context = {
                "domain": self.config.domain,
                "project": self.config.project,
                "feature_id": feature.id,
            }

            # Query RAG for relevant patterns
            response = await self.code_atlas_bridge.query_rag(query, context=context)

            if response.confidence >= 0.5 and response.answer:
                logger.info(
                    f"Atlas context for {feature.id}: confidence={response.confidence:.2f}, "
                    f"sources={len(response.sources)}"
                )
                return f"\n\n## Relevant Context from Past Sessions\n{response.answer}"
            else:
                logger.debug(f"Atlas low confidence for {feature.id}: {response.confidence:.2f}")
                return None

        except Exception as e:
            logger.warning(f"Atlas query failed for {feature.id}: {e}")
            return None

    async def _check_and_flush_memory(self, feature: FeatureSpec) -> None:
        """
        Check context usage and trigger memory flush if needed.

        Based on MoltBot research: Flush at 70% context usage before compaction.

        Args:
            feature: Current feature being processed
        """
        if not hasattr(self, "_memory_monitor") or not self._memory_monitor:
            return

        # Estimate token usage (rough approximation)
        # Assume average token is ~4 characters
        estimated_tokens = self._estimated_token_usage
        max_tokens = 200000  # Claude Opus 4.5 context window

        # Check if flush should be triggered
        if self._memory_monitor.should_flush(estimated_tokens, max_tokens):
            logger.info(
                f"Context usage at {estimated_tokens}/{max_tokens} tokens ({estimated_tokens / max_tokens * 100:.1f}%)"
            )
            logger.info("Triggering pre-compaction memory flush...")

            # Build context summary
            context = f"""# Current Task Context

**Feature:** {feature.id} - {feature.name}
**Description:** {feature.description}
**Status:** {feature.status.value}
**Attempts:** {feature.attempts}
**Priority:** {feature.priority}

**Progress Summary:**
- Iteration: {self._iteration}/{self.config.max_iterations}
- Features passing: {self.store.get_stats()["passing"]}
- Features blocked: {self.store.get_stats()["blocked"]}
- Features remaining: {self.store.get_stats()["pending"]}

**Recent Work:**
{feature.last_error if feature.last_error else "No recent errors"}
"""

            # Use a mock provider for memory extraction since we don't have direct LLM access here
            # The actual LLM interaction would need to be done by the orchestrator or agent
            try:
                # Save a basic context snapshot even without LLM summary
                await self._memory_manager._save_session_note(
                    memory_summary="Context snapshot saved before potential compaction",
                    context=context,
                    domain=self.config.domain,
                    project=self.config.project,
                    feature_id=feature.id,
                )
                self._memory_monitor.record_flush()
                logger.info("Memory flush completed successfully")
            except Exception as e:
                logger.error(f"Memory flush failed: {e}")

    async def _implement_feature(self, feature: FeatureSpec) -> bool:
        """Implement a single feature.

        Args:
            feature: Feature to implement

        Returns:
            True if implementation succeeded
        """
        # Retrieve prior patterns from learning store
        prior_patterns = await self._get_prior_patterns(feature)
        if prior_patterns:
            # Format patterns as context
            pattern_context = "\n\n## Prior Patterns\n"
            pattern_context += "Based on similar features in the past:\n\n"
            for i, pattern in enumerate(prior_patterns, 1):
                pattern_context += (
                    f"{i}. Pattern `{pattern.pattern_id}` "
                    f"(effectiveness: {pattern.effectiveness:.1%}, "
                    f"confidence: {pattern.confidence:.1%}, "
                    f"used {pattern.total_applications}x)\n"
                )
            feature.description = feature.description + pattern_context
            logger.info(f"Enriched {feature.id} with {len(prior_patterns)} prior patterns")

        # Query Atlas for relevant context before implementing (compounding loop)
        atlas_context = await self._query_atlas_for_context(feature)
        if atlas_context:
            # Enrich feature description with Atlas context
            feature.description = feature.description + atlas_context
            logger.info(f"Enriched {feature.id} with Atlas context")

        import time

        start_time = time.time()

        if self.orchestrator is not None:
            try:
                # Use orchestrator to implement feature
                logger.info(f"Implementing {feature.id} via orchestrator")

                # Check if orchestrator has implement_feature method
                if hasattr(self.orchestrator, "implement_feature"):
                    success, error = await self.orchestrator.implement_feature(feature)
                    duration = time.time() - start_time

                    # Record pattern outcome for RL learning
                    await self._record_pattern_outcome(
                        feature=feature,
                        success=success,
                        duration=duration,
                        error=error if not success else None,
                    )

                    if not success:
                        logger.warning(f"Orchestrator failed: {error}")
                    return success
                else:
                    # Fallback for orchestrators without implement_feature
                    logger.warning("Orchestrator missing implement_feature method")
                    return True
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"Orchestrator error: {e}")

                # Record failure outcome
                await self._record_pattern_outcome(
                    feature=feature, success=False, duration=duration, error=str(e)
                )
                return False
        else:
            # No orchestrator - feature must be implemented manually
            logger.info(f"No orchestrator - {feature.id} requires manual implementation")
            return True  # Allow tests to determine pass/fail

    async def _record_pattern_outcome(
        self, feature: FeatureSpec, success: bool, duration: float, error: str | None = None
    ) -> None:
        """Record pattern outcome for RL learning.

        This feeds the meta-learning system with real execution data,
        enabling the decision engine to tune confidence thresholds
        and the pattern library to track effectiveness.

        Args:
            feature: The feature that was implemented
            success: Whether implementation succeeded
            duration: Time taken in seconds
            error: Error message if failed
        """
        try:
            # Use the feedback_loop_manager's learning store if available
            learning_store = None

            if self.feedback_loop_manager is not None:
                learning_store = getattr(self.feedback_loop_manager, "learning_store", None)

            if learning_store is None:
                # Create a local learning store
                try:
                    from pathlib import Path

                    from .meta_learning.learning_store import LearningStore

                    store_path = Path(".forge/learning")
                    learning_store = LearningStore(store_path)
                except Exception:
                    logger.debug("No learning store available - skipping pattern outcome recording")
                    return

            # Determine pattern ID from feature type or use default
            feature_type = getattr(feature, "type", None) or self._extract_feature_type(feature)
            pattern_id = f"{feature_type}-implementation"

            # Compute context signature for this pattern
            domain = getattr(self, "domain", "unknown")
            project = getattr(self, "project", "unknown")
            context_signature = learning_store.compute_context_signature(
                domain=domain,
                project=project,
                file_paths=[],  # No specific files for feature patterns
                tags=[feature_type, feature.priority],
            )

            # Ensure pattern exists before updating outcome
            existing_pattern = learning_store.get_pattern(pattern_id)
            if existing_pattern is None:
                from .meta_learning.schemas import PatternRecord

                new_pattern = PatternRecord(
                    pattern_id=pattern_id,
                    context_signature=context_signature,
                    pattern_type=feature_type,
                    total_applications=0,
                    successful_applications=0,
                )
                learning_store.record_pattern(new_pattern)

            # Update pattern outcome (synchronous call)
            learning_store.update_pattern_outcome(pattern_id, success)

            logger.info(
                f"Recorded pattern outcome for {feature.id}: {'success' if success else 'failure'}"
            )
        except Exception as e:
            # Don't fail the main operation if recording fails
            logger.warning(f"Failed to record pattern outcome: {e}")

    async def _run_tests(self, feature: FeatureSpec) -> tuple[bool, str | None]:
        """Run tests for a feature.

        Args:
            feature: Feature to test

        Returns:
            Tuple of (passed, error_message)
        """
        lint_passed, lint_error = await self._run_lint(feature)
        if not lint_passed:
            return False, lint_error

        # Build test command
        cmd = self.config.test_command
        if feature.tests:
            # Filter to specific tests
            test_filter = " or ".join(feature.tests)
            cmd = f"{cmd} -k '{test_filter}'"

        if self.config.dry_run:
            logger.info(f"Dry run: would execute '{cmd}'")
            return True, None

        try:
            # Determine working directory for tests
            cwd = self.config.working_dir or self.store.features_path.parent

            # Run tests
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.timeout_seconds,
            )

            output = stdout.decode("utf-8") + stderr.decode("utf-8")

            if proc.returncode == 0:
                return True, None
            else:
                # Extract error message from output
                error_lines = []
                for line in output.split("\n"):
                    if "FAILED" in line or "ERROR" in line or "AssertionError" in line:
                        error_lines.append(line.strip())
                error_msg = "\n".join(error_lines[-5:]) if error_lines else "Tests failed"
                return False, error_msg

        except TimeoutError:
            return False, f"Tests timed out after {self.config.timeout_seconds}s"
        except Exception as e:
            return False, f"Test execution error: {e}"

    async def _run_lint(self, feature: FeatureSpec) -> tuple[bool, str | None]:
        """Run lint checks if configured.

        Args:
            feature: Feature to lint (unused; kept for parity with _run_tests)

        Returns:
            Tuple of (passed, error_message)
        """
        if not self.config.lint_command:
            return True, None

        cmd = self.config.lint_command

        if self.config.dry_run:
            logger.info(f"Dry run: would execute lint '{cmd}'")
            return True, None

        try:
            cwd = self.config.working_dir or self.store.features_path.parent
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.timeout_seconds,
            )

            output = stdout.decode("utf-8") + stderr.decode("utf-8")
            if proc.returncode == 0:
                return True, None

            error_lines = []
            for line in output.split("\n"):
                if "error" in line.lower() or "FAILED" in line or "E" in line:
                    error_lines.append(line.strip())
            error_msg = "\n".join(error_lines[-5:]) if error_lines else "Lint failed"
            return False, error_msg
        except TimeoutError:
            return False, f"Lint timed out after {self.config.timeout_seconds}s"
        except Exception as e:
            return False, f"Lint execution error: {e}"

    async def _save_checkpoint(self) -> Path:
        """Save loop checkpoint.

        Returns:
            Path to checkpoint file
        """
        checkpoint_dir = self.store.features_path.parent / ".forge/ralph_checkpoints"
        checkpoint_dir.mkdir(exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        checkpoint_path = checkpoint_dir / f"checkpoint_{timestamp}.json"

        checkpoint_data = {
            "iteration": self._iteration,
            "timestamp": datetime.now(UTC).isoformat(),
            "features_path": str(self.store.features_path),
            "config": asdict(self.config),
            "stats": self.store.get_stats(),
        }

        checkpoint_path.write_text(json.dumps(checkpoint_data, indent=2))
        logger.info(f"Saved checkpoint: {checkpoint_path}")

        return checkpoint_path

    async def resume(self, checkpoint_path: Path) -> LoopResult:
        """Resume loop from checkpoint.

        Args:
            checkpoint_path: Path to checkpoint file

        Returns:
            LoopResult from continued execution
        """
        # Load checkpoint
        checkpoint_data = json.loads(checkpoint_path.read_text())

        # Restore state
        self._iteration = checkpoint_data["iteration"]
        logger.info(f"Resuming from iteration {self._iteration}")

        # Continue running
        return await self.run()

    # =========================================================================
    # Meta-Learning Integration
    # =========================================================================

    def _build_decision_context(self, feature: FeatureSpec) -> "DecisionContext":
        """Build a DecisionContext from a FeatureSpec.

        Args:
            feature: Feature to build context for

        Returns:
            DecisionContext for the decision engine
        """
        from .meta_learning.schemas import DecisionContext

        # Extract tags from feature metadata
        tags = []
        if feature.priority == "critical":
            tags.append("critical")
        if feature.priority == "high":
            tags.append("high-priority")
        if "security" in feature.name.lower() or "auth" in feature.name.lower():
            tags.append("security")
        if "migration" in feature.name.lower() or "refactor" in feature.name.lower():
            tags.append("refactor")

        # Extract file_paths from feature metadata
        file_paths = self._extract_file_paths(feature)

        return DecisionContext(
            domain=self.config.domain or "unknown",
            project=self.config.project or "unknown",
            feature_id=feature.id,
            file_paths=file_paths,
            tags=tags,
            description=feature.description,
        )

    def _extract_file_paths(self, feature: FeatureSpec) -> list[str]:
        """Extract file paths from feature metadata.

        Looks for file paths in:
        - feature.tests (test file paths)
        - feature.depends_on (related feature IDs → might indicate files)
        - feature.description (parses backtick-quoted paths)
        - feature.acceptance_criteria (parses file references)

        Args:
            feature: Feature to extract paths from

        Returns:
            List of file paths relevant to this feature
        """
        file_paths: list[str] = []

        # Extract from tests field
        if feature.tests:
            for test in feature.tests:
                if "/" in test or "\\" in test or test.endswith(".py"):
                    file_paths.append(test)

        # Parse backtick-quoted paths from description
        if feature.description:
            import re

            # Match paths in backticks (e.g., `src/foo/bar.py`)
            backtick_matches = re.findall(
                r"`([^`]+(?:\.py|\.ts|\.js|\.tsx|\.jsx|/[^`]+))`", feature.description
            )
            file_paths.extend(backtick_matches)

        # Parse file references from acceptance criteria
        if feature.acceptance_criteria:
            import re

            for criterion in feature.acceptance_criteria:
                # Match paths in backticks or explicit file references
                backtick_matches = re.findall(
                    r"`([^`]+(?:\.py|\.ts|\.js|\.tsx|\.jsx|/[^`]+))`", criterion
                )
                file_paths.extend(backtick_matches)
                # Match "in file.py" or "file.py:" patterns
                file_ref_matches = re.findall(
                    r"(?:in |modify |update |create )([a-zA-Z0-9_/.-]+\.(?:py|ts|js|tsx|jsx))",
                    criterion,
                )
                file_paths.extend(file_ref_matches)

        # Deduplicate while preserving order
        seen = set()
        unique_paths = []
        for path in file_paths:
            if path not in seen:
                seen.add(path)
                unique_paths.append(path)

        return unique_paths

    async def _get_decision(self, feature: FeatureSpec) -> "DecisionRecommendation | None":
        """Query the DecisionEngine for a recommendation.

        Args:
            feature: Feature to get decision for

        Returns:
            DecisionRecommendation or None if engine unavailable
        """
        if not self.decision_engine:
            return None

        try:
            context = self._build_decision_context(feature)
            recommendation = await self.decision_engine.get_recommendation(context)

            # Store decision ID for outcome recording
            if hasattr(recommendation, "decision_id"):
                self._decision_ids[feature.id] = recommendation.decision_id

            logger.info(
                f"Decision for {feature.id}: {recommendation.action.value} "
                f"(confidence={recommendation.confidence.value})"
            )

            return recommendation
        except Exception as e:
            logger.warning(f"DecisionEngine error for {feature.id}: {e}")
            return None

    async def _record_outcome(self, feature: FeatureSpec, success: bool) -> None:
        """Record the outcome of a feature implementation.

        Records to both SimpleHistory (if available) and DecisionEngine (if available).
        SimpleHistory provides lightweight, fast tracking while DecisionEngine
        enables full meta-learning with pattern matching.

        Args:
            feature: Completed feature
            success: Whether implementation succeeded
        """
        # Record to SimpleHistory (lightweight, always succeeds)
        await self._record_simple_history_outcome(feature, success)

        # Record to DecisionEngine (full meta-learning)
        if not self.decision_engine:
            await self._record_learning_store_outcome(feature, success)
            return

        decision_id = self._decision_ids.get(feature.id)
        if not decision_id:
            await self._record_learning_store_outcome(feature, success)
            return

        try:
            await self.decision_engine.record_outcome(
                decision_id=decision_id,
                success=success,
            )
            logger.debug(f"Recorded outcome for {feature.id}: success={success}")
        except Exception as e:
            logger.warning(f"Failed to record outcome for {feature.id}: {e}")
            await self._record_learning_store_outcome(feature, success)

    async def _record_learning_store_outcome(self, feature: FeatureSpec, success: bool) -> None:
        """Record decision outcome directly to the learning store.

        This is a fallback when no DecisionEngine decision_id is available.
        """
        try:
            learning_store = None
            if self.decision_engine and self.decision_engine.learning_store:
                learning_store = self.decision_engine.learning_store

            if learning_store is None:
                from pathlib import Path

                from .meta_learning.learning_store import LearningStore

                learning_store = LearningStore(Path(".forge/learning"))

            from .meta_learning.schemas import DecisionAction, DecisionRecord

            domain = self.config.domain or getattr(self, "domain", "unknown") or "unknown"
            project = self.config.project or getattr(self, "project", "unknown") or "unknown"
            feature_type = getattr(feature, "type", None) or self._extract_feature_type(feature)
            tags = ["ralph_loop", feature_type, "tests", "lint"]

            context_signature = learning_store.compute_context_signature(
                domain=domain,
                project=project,
                file_paths=self._extract_file_paths(feature),
                tags=tags,
            )

            decision_id = self._decision_ids.get(feature.id)
            if not decision_id:
                decision_id = f"ralph-{feature.id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
                learning_store.record_decision(
                    DecisionRecord(
                        decision_id=decision_id,
                        context_signature=context_signature,
                        domain=domain,
                        project=project,
                        recommended_action=DecisionAction.PROCEED,
                        actual_action=DecisionAction.PROCEED,
                    )
                )

            learning_store.record_outcome(
                decision_id=decision_id,
                success=success,
                actual_action=DecisionAction.PROCEED,
            )
            logger.debug(
                "Recorded learning store outcome for %s: success=%s",
                feature.id,
                success,
            )
        except Exception as e:
            logger.warning(f"Failed to record learning store outcome for {feature.id}: {e}")

    async def _record_simple_history_outcome(self, feature: FeatureSpec, success: bool) -> None:
        """Record outcome to SimpleHistory for lightweight tracking.

        SimpleHistory is a simplified alternative to DecisionEngine that uses
        append-only JSONL. It's fast, reliable, and provides basic success rate
        tracking without the complexity of full meta-learning.

        Args:
            feature: Completed feature
            success: Whether implementation succeeded
        """
        if not self.simple_history:
            return

        try:
            # Extract feature type for action categorization
            feature_type = self._extract_feature_type(feature)

            # Build context with useful metadata
            context = {
                "feature_id": feature.id,
                "feature_name": feature.name,
                "priority": feature.priority,
                "attempts": feature.attempts,
            }
            if feature.last_error and not success:
                # Truncate error for storage efficiency
                context["error"] = (
                    feature.last_error[:200]
                    if len(feature.last_error) > 200
                    else feature.last_error
                )

            # Record to SimpleHistory
            self.simple_history.record(
                domain=self.config.domain or "unknown",
                project=self.config.project or "unknown",
                action=f"feature:{feature_type}",
                success=success,
                context=context,
            )

            logger.debug(
                "Recorded SimpleHistory outcome for %s: success=%s (action=feature:%s)",
                feature.id,
                success,
                feature_type,
            )
        except Exception as e:
            # SimpleHistory failures should never break the main loop
            logger.warning(f"Failed to record SimpleHistory outcome for {feature.id}: {e}")

    async def _index_feature_to_atlas(self, feature: FeatureSpec, success: bool) -> dict:
        """Index completed feature to Code Atlas for pattern learning.

        This enables the compounding loop: successful implementations
        become patterns for future sessions.

        Args:
            feature: Completed feature
            success: Whether implementation succeeded

        Returns:
            Dict with indexing result (indexed: bool, message: str)
        """
        if not self.code_atlas_bridge:
            return {"indexed": False, "reason": "no_bridge"}

        try:
            # Build session summary for this feature
            session_summary = {
                "session_id": f"{self.config.domain}_{self.config.project}_{feature.id}",
                "domain": self.config.domain or "unknown",
                "project": self.config.project or "unknown",
                "feature_id": feature.id,
                "feature_name": feature.name,
                "feature_description": feature.description,
                "success": success,
                "attempts": feature.attempts,
                "priority": feature.priority,
                "acceptance_criteria": feature.acceptance_criteria,
                "file_paths": self._extract_file_paths(feature),
                "timestamp": datetime.now(UTC).isoformat(),
                "tags": [
                    f"priority:{feature.priority}",
                    f"status:{'passing' if success else 'failing'}",
                ],
            }

            # Add category tags based on feature content
            if "auth" in feature.name.lower() or "security" in feature.name.lower():
                session_summary["tags"].append("category:security")
            if "api" in feature.name.lower() or "endpoint" in feature.name.lower():
                session_summary["tags"].append("category:api")
            if "test" in feature.name.lower():
                session_summary["tags"].append("category:testing")
            if "refactor" in feature.name.lower() or "migration" in feature.name.lower():
                session_summary["tags"].append("category:refactor")

            result = await self.code_atlas_bridge.index_session(session_summary=session_summary)

            if result.get("indexed"):
                logger.info(
                    f"Indexed feature {feature.id} to Code Atlas",
                    extra={"feature_id": feature.id, "success": success},
                )
            else:
                logger.debug(
                    f"Atlas indexing skipped for {feature.id}: {result.get('reason', 'unknown')}"
                )

            return result

        except Exception as e:
            logger.warning(f"Failed to index feature {feature.id} to Atlas: {e}")
            return {"indexed": False, "error": str(e)}

    async def _trigger_feedback_loops(self, stats: dict[str, int], duration: float) -> None:
        """Trigger feedback loops at the end of a session.

        This enables the compounding learning effect by:
        1. Indexing session to Code Atlas for future retrieval
        2. Generating tech debt features from quality findings
        3. Optimizing human gate thresholds based on outcomes
        4. Persisting learned patterns to improve future decisions

        Args:
            stats: Session statistics from feature store
            duration: Session duration in seconds
        """
        # Build session summary for indexing
        session_summary_dict = {
            "session_id": f"{self.config.domain}_{self.config.project}_{self._iteration}",
            "domain": self.config.domain or "unknown",
            "project": self.config.project or "unknown",
            "started_at": self._start_time.isoformat()
            if self._start_time
            else datetime.now(UTC).isoformat(),
            "ended_at": datetime.now(UTC).isoformat(),
            "features_completed": stats.get("passing", 0),
            "features_blocked": stats.get("blocked", 0),
            "features_failing": stats.get("failing", 0),
            "total_iterations": self._iteration,
            "duration_seconds": duration,
            "success_rate": (
                stats.get("passing", 0)
                / max(
                    1, stats.get("passing", 0) + stats.get("blocked", 0) + stats.get("failing", 0)
                )
            ),
            "tags": [
                f"domain:{self.config.domain or 'unknown'}",
                f"project:{self.config.project or 'unknown'}",
                "type:session_summary",
            ],
        }

        # Direct Atlas indexing (fallback when no feedback_loop_manager)
        if self.code_atlas_bridge:
            try:
                atlas_result = await self.code_atlas_bridge.index_session(
                    session_summary=session_summary_dict
                )
                if atlas_result.get("indexed"):
                    logger.info(
                        "Session summary indexed to Code Atlas",
                        extra={
                            "session_id": session_summary_dict["session_id"],
                            "features_completed": stats.get("passing", 0),
                        },
                    )
            except Exception as e:
                logger.warning(f"Failed to index session summary to Atlas: {e}")

        # Check if we have a feedback loop manager for additional processing
        if not hasattr(self, "feedback_loop_manager") or self.feedback_loop_manager is None:
            logger.debug("No feedback loop manager - direct Atlas indexing only")
            return

        try:
            from .meta_learning import SessionSummary

            # Build typed session summary for feedback loop manager
            session_summary = SessionSummary(
                session_id=session_summary_dict["session_id"],
                domain=self.config.domain or "unknown",
                project=self.config.project or "unknown",
                started_at=self._start_time or datetime.now(UTC),
                ended_at=datetime.now(UTC),
                features_completed=stats.get("passing", 0),
                features_blocked=stats.get("blocked", 0),
                total_decisions=self._iteration,
                success_rate=session_summary_dict["success_rate"],
                files_modified=[],  # Could be tracked if needed
                patterns_learned=0,  # Could be tracked if needed
            )

            # Trigger feedback loops
            result = await self.feedback_loop_manager.on_session_complete(session_summary)
            logger.info(
                f"Feedback loops completed: indexed={result.get('hooks', {}).get('post_session', {}).get('indexed', False)}, "
                f"optimized={result.get('hooks', {}).get('optimization', {}).get('optimized', False)}"
            )

        except Exception as e:
            logger.warning(f"Failed to trigger feedback loops: {e}")

    async def _request_human_review(
        self, feature: FeatureSpec, recommendation: "DecisionRecommendation"
    ) -> bool:
        """Request human review for a feature.

        Creates an approval request and waits for human decision.

        Args:
            feature: Feature requiring review
            recommendation: DecisionEngine recommendation

        Returns:
            True if approved, False if rejected or timed out
        """
        if not self.approval_queue:
            logger.warning(f"No approval queue - skipping human review for {feature.id}")
            return True  # Proceed if no approval queue

        try:
            from .approval_queue import ApprovalStatus, ApprovalType

            # Create approval request
            request = await self.approval_queue.create_request(
                type=ApprovalType.FEATURE,
                domain=self.config.domain or "unknown",
                title=f"Human Review Required: {feature.name}",
                description=(
                    f"Feature ID: {feature.id}\n"
                    f"Description: {feature.description}\n\n"
                    f"Decision Engine Reasoning:\n{recommendation.reasoning}\n\n"
                    f"Warnings: {len(recommendation.warnings)}\n"
                    + "\n".join(f"- {w.message}" for w in recommendation.warnings[:5])
                ),
                expiry_hours=self.config.approval_timeout_hours,
            )

            logger.info(f"Created approval request {request.id} for {feature.id}")

            # Poll for approval (with timeout)
            timeout_secs = self.config.approval_timeout_hours * 3600
            poll_interval = 60  # Poll every minute
            elapsed = 0

            while elapsed < timeout_secs:
                updated_request = await self.approval_queue.get_request(request.id)
                if updated_request:
                    if updated_request.status == ApprovalStatus.APPROVED:
                        logger.info(f"Feature {feature.id} approved by human")
                        return True
                    elif updated_request.status == ApprovalStatus.REJECTED:
                        logger.info(f"Feature {feature.id} rejected by human")
                        return False
                    elif updated_request.status == ApprovalStatus.EXPIRED:
                        logger.warning(f"Approval request expired for {feature.id}")
                        return False

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            logger.warning(f"Approval timeout for {feature.id}")
            return False  # Timeout = rejection

        except Exception as e:
            logger.error(f"Human review error for {feature.id}: {e}")
            return False


# =============================================================================
# GitHub Integration
# =============================================================================


class GitHubFeatureTracker:
    """Tracks features via GitHub Issues.

    Creates/updates GitHub Issues to track feature progress.
    """

    # Status label mappings
    STATUS_LABELS = {
        FeatureStatus.PENDING: "status:pending",
        FeatureStatus.IN_PROGRESS: "status:in-progress",
        FeatureStatus.PASSING: "status:passing",
        FeatureStatus.FAILING: "status:failing",
        FeatureStatus.BLOCKED: "status:blocked",
        FeatureStatus.SKIPPED: "status:skipped",
        FeatureStatus.COMPLETED: "status:completed",
    }

    def __init__(self, github_repo: str, github_token: str | None = None):
        """Initialize GitHub tracker.

        Args:
            github_repo: Repository in "owner/repo" format
            github_token: GitHub API token (falls back to GITHUB_TOKEN env var)
        """
        import os

        self.repo = github_repo
        self.token = github_token or os.environ.get("GITHUB_TOKEN")
        self._issue_cache: dict[str, int] = {}  # feature_id → issue_number

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for GitHub API."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _feature_to_issue_body(self, feature: FeatureSpec) -> str:
        """Convert feature to GitHub Issue body."""
        body = f"""## {feature.name}

{feature.description}

### Details
- **Feature ID:** `{feature.id}`
- **Priority:** {feature.priority}
- **Status:** {feature.status.value}
- **Attempts:** {feature.attempts}

### Acceptance Criteria
"""
        for criteria in feature.acceptance_criteria:
            body += f"- [ ] {criteria}\n"

        if feature.depends_on:
            body += "\n### Dependencies\n"
            for dep in feature.depends_on:
                body += f"- `{dep}`\n"

        if feature.tests:
            body += "\n### Tests\n"
            for test in feature.tests:
                body += f"- `{test}`\n"

        if feature.last_error:
            body += f"\n### Last Error\n```\n{feature.last_error}\n```\n"

        body += "\n---\n*Managed by Ralph Loop Harness*"
        return body

    async def sync_to_issues(self, features: list[FeatureSpec]) -> dict[str, int]:
        """Sync features to GitHub Issues.

        Args:
            features: List of features to sync

        Returns:
            Dict mapping feature_id to issue number
        """
        try:
            import httpx
        except ImportError:
            logger.warning("httpx not installed, cannot sync to GitHub")
            return {}

        if not self.token:
            logger.warning("No GitHub token configured, cannot sync issues")
            return {}

        result: dict[str, int] = {}

        async with httpx.AsyncClient() as client:
            # First, search for existing issues
            search_url = "https://api.github.com/search/issues"
            query = f"repo:{self.repo} is:issue label:ralph-loop"
            resp = await client.get(
                search_url,
                params={"q": query},
                headers=self._get_headers(),
            )

            existing_issues: dict[str, int] = {}
            if resp.status_code == 200:
                data = resp.json()
                for issue in data.get("items", []):
                    # Extract feature ID from title
                    title = issue.get("title", "")
                    if title.startswith("["):
                        feature_id = title.split("]")[0][1:]
                        existing_issues[feature_id] = issue["number"]

            # Sync each feature
            for feature in features:
                title = f"[{feature.id}] {feature.name}"
                body = self._feature_to_issue_body(feature)
                labels = ["ralph-loop", self.STATUS_LABELS.get(feature.status, "status:pending")]

                if feature.id in existing_issues:
                    # Update existing issue
                    issue_number = existing_issues[feature.id]
                    url = f"https://api.github.com/repos/{self.repo}/issues/{issue_number}"
                    resp = await client.patch(
                        url,
                        json={"title": title, "body": body, "labels": labels},
                        headers=self._get_headers(),
                    )
                    if resp.status_code == 200:
                        result[feature.id] = issue_number
                        logger.info(f"Updated issue #{issue_number} for {feature.id}")
                    else:
                        logger.warning(f"Failed to update issue: {resp.status_code}")
                else:
                    # Create new issue
                    url = f"https://api.github.com/repos/{self.repo}/issues"
                    resp = await client.post(
                        url,
                        json={"title": title, "body": body, "labels": labels},
                        headers=self._get_headers(),
                    )
                    if resp.status_code == 201:
                        issue_number = resp.json()["number"]
                        result[feature.id] = issue_number
                        logger.info(f"Created issue #{issue_number} for {feature.id}")
                    else:
                        logger.warning(f"Failed to create issue: {resp.status_code}")

        self._issue_cache.update(result)
        return result

    async def update_issue_status(
        self, feature_id: str, issue_number: int, status: FeatureStatus
    ) -> None:
        """Update issue status labels.

        Args:
            feature_id: Feature ID
            issue_number: GitHub issue number
            status: New status
        """
        try:
            import httpx
        except ImportError:
            logger.warning("httpx not installed, cannot update issue")
            return

        if not self.token:
            logger.warning("No GitHub token configured, cannot update issue")
            return

        new_label = self.STATUS_LABELS.get(status, "status:pending")

        async with httpx.AsyncClient() as client:
            # Get current labels
            url = f"https://api.github.com/repos/{self.repo}/issues/{issue_number}/labels"
            resp = await client.get(url, headers=self._get_headers())

            if resp.status_code != 200:
                logger.warning(f"Failed to get labels: {resp.status_code}")
                return

            current_labels = [label["name"] for label in resp.json()]

            # Remove old status labels, add new one
            new_labels = [l for l in current_labels if not l.startswith("status:")]
            new_labels.append(new_label)

            # Update labels
            resp = await client.put(
                url,
                json={"labels": new_labels},
                headers=self._get_headers(),
            )

            if resp.status_code == 200:
                logger.info(f"Updated issue #{issue_number} status to {status.value}")

                # Add comment for significant status changes
                if status in (
                    FeatureStatus.PASSING,
                    FeatureStatus.COMPLETED,
                    FeatureStatus.BLOCKED,
                ):
                    comment_url = (
                        f"https://api.github.com/repos/{self.repo}/issues/{issue_number}/comments"
                    )
                    if status in (FeatureStatus.PASSING, FeatureStatus.COMPLETED):
                        comment = (
                            f":white_check_mark: Feature `{feature_id}` is now **{status.value}**!"
                        )
                    else:
                        comment = (
                            f":x: Feature `{feature_id}` is now **blocked** after max attempts."
                        )

                    await client.post(
                        comment_url,
                        json={"body": comment},
                        headers=self._get_headers(),
                    )
            else:
                logger.warning(f"Failed to update labels: {resp.status_code}")


# =============================================================================
# Factory Functions
# =============================================================================


def create_ralph_loop(
    features_path: Path | str,
    max_iterations: int = 100,
    max_failures_per_feature: int = 5,
    dry_run: bool = False,
    orchestrator: Any | None = None,
    decision_engine: "DecisionEngine | None" = None,
    approval_queue: "ApprovalQueueHarness | None" = None,
    feedback_loop_manager: Any | None = None,
    code_atlas_bridge: "CodeAtlasBridge | None" = None,
    failure_pattern_db: Any | None = None,
    domain: str | None = None,
    project: str | None = None,
    test_command: str | None = None,
    working_dir: Path | str | None = None,
    telemetry: "AgentTelemetry | None" = None,
    enable_telemetry: bool = True,
    simple_history: SimpleHistory | None = None,
) -> RalphLoopHarness:
    """Create a Ralph loop harness.

    Args:
        features_path: Path to features.json
        max_iterations: Maximum loop iterations
        max_failures_per_feature: Max failures before blocking
        dry_run: If True, don't actually implement
        orchestrator: Optional OrchestrationHarness
        decision_engine: Optional DecisionEngine for meta-learning
        approval_queue: Optional ApprovalQueueHarness for human review
        feedback_loop_manager: Optional FeedbackLoopManager for compounding learning
        code_atlas_bridge: Optional CodeAtlasBridge for pattern retrieval
        failure_pattern_db: Optional EnhancedFailurePatternDB for fix suggestions
        domain: Domain name for meta-learning context
        project: Project name for meta-learning context
        test_command: Override test command (default uses LoopConfig default)
        telemetry: Optional AgentTelemetry for Command Center integration
        enable_telemetry: Auto-create telemetry from env if True (default: True)
        simple_history: Optional SimpleHistory for lightweight outcome tracking

    Returns:
        Configured RalphLoopHarness
    """
    store = FeatureStore(Path(features_path))
    config_kwargs: dict[str, Any] = {
        "domain": domain,
        "project": project,
        "max_iterations": max_iterations,
        "max_failures_per_feature": max_failures_per_feature,
        "dry_run": dry_run,
    }
    if test_command:
        config_kwargs["test_command"] = test_command
    if working_dir is not None:
        config_kwargs["working_dir"] = Path(working_dir)
    config = LoopConfig(**config_kwargs)

    # Auto-create telemetry if enabled and not provided
    if telemetry is None and enable_telemetry:
        try:
            from .agent_telemetry import create_agent_telemetry_from_env

            telemetry = create_agent_telemetry_from_env(
                domain=domain,
                project=project,
                role="ralph-loop",
            )
            logger.info("Auto-created telemetry for Ralph loop")
        except Exception as e:
            logger.debug(f"Could not auto-create telemetry: {e}")
            telemetry = None

    return RalphLoopHarness(
        feature_store=store,
        config=config,
        orchestrator=orchestrator,
        decision_engine=decision_engine,
        approval_queue=approval_queue,
        feedback_loop_manager=feedback_loop_manager,
        code_atlas_bridge=code_atlas_bridge,
        failure_pattern_db=failure_pattern_db,
        telemetry=telemetry,
        simple_history=simple_history,
    )


def create_ralph_loop_from_registry(
    features_path: Path | str,
    registry: Any | None = None,
    max_iterations: int = 100,
    max_failures_per_feature: int = 5,
    dry_run: bool = False,
    orchestrator: Any | None = None,
    domain: str | None = None,
    project: str | None = None,
    test_command: str | None = None,
    working_dir: Path | str | None = None,
    enable_telemetry: bool = True,
) -> RalphLoopHarness:
    """Create a Ralph loop harness with meta-learning wired from HarnessRegistry.

    This factory automatically wires the DecisionEngine, ApprovalQueue,
    FeedbackLoopManager, CodeAtlasBridge, and FailurePatternDB from the
    HarnessRegistry, enabling the compounding feedback loops.

    Args:
        features_path: Path to features.json
        registry: Optional HarnessRegistry (created from env if None)
        max_iterations: Maximum loop iterations
        max_failures_per_feature: Max failures before blocking
        dry_run: If True, don't actually implement
        orchestrator: Optional OrchestrationHarness
        domain: Domain name for meta-learning context
        project: Project name for meta-learning context
        test_command: Override test command (default uses LoopConfig default)
        working_dir: CWD for test runs (e.g. harness root for command_center)
        enable_telemetry: Auto-create telemetry from env if True (default: True)

    Returns:
        Configured RalphLoopHarness with meta-learning enabled
    """
    # Create registry from environment if not provided
    if registry is None:
        from .harness_registry import create_harness_registry

        registry = create_harness_registry()

    # Get meta-learning components from registry
    decision_engine = None
    approval_queue = None
    feedback_loop_manager = None
    code_atlas_bridge = None
    failure_pattern_db = None
    simple_history = None

    try:
        simple_history = registry.get("simple_history")
        logger.info("Wired SimpleHistory from registry")
    except Exception as e:
        logger.debug(f"Could not get simple_history from registry: {e}")

    try:
        decision_engine = registry.get("decision_engine")
        logger.info("Wired DecisionEngine from registry")
    except Exception as e:
        logger.warning(f"Could not get decision_engine from registry: {e}")

    try:
        approval_queue = registry.get("approval_queue")
        logger.info("Wired ApprovalQueue from registry")
    except Exception as e:
        logger.warning(f"Could not get approval_queue from registry: {e}")

    try:
        feedback_loop_manager = registry.get("feedback_loop_manager")
        logger.info("Wired FeedbackLoopManager from registry")
    except Exception as e:
        logger.warning(f"Could not get feedback_loop_manager from registry: {e}")

    try:
        code_atlas_bridge = registry.get("code_atlas")
        logger.info("Wired CodeAtlasBridge from registry")
    except Exception as e:
        logger.warning(f"Could not get code_atlas from registry: {e}")

    try:
        failure_pattern_db = registry.get("failure_pattern_db")
        logger.info("Wired FailurePatternDB from registry")
    except Exception as e:
        logger.warning(f"Could not get failure_pattern_db from registry: {e}")

    return create_ralph_loop(
        features_path=features_path,
        max_iterations=max_iterations,
        max_failures_per_feature=max_failures_per_feature,
        dry_run=dry_run,
        orchestrator=orchestrator,
        decision_engine=decision_engine,
        approval_queue=approval_queue,
        feedback_loop_manager=feedback_loop_manager,
        code_atlas_bridge=code_atlas_bridge,
        failure_pattern_db=failure_pattern_db,
        domain=domain,
        project=project,
        test_command=test_command,
        working_dir=working_dir,
        enable_telemetry=enable_telemetry,
        simple_history=simple_history,
    )


def _extract_priority_from_line(line: str) -> str | None:
    """Extract priority from a line if present.

    Args:
        line: Line to check for priority

    Returns:
        Priority string or None if not found
    """
    line_upper = line.upper()
    if "P0" in line_upper or "CRITICAL" in line_upper:
        return "critical"
    elif "P1" in line_upper or ("HIGH" in line_upper and "PRIORITY" in line_upper):
        return "high"
    elif "P2" in line_upper:
        return "medium"
    elif "P3" in line_upper or "LOW" in line_upper:
        return "low"
    return None


def create_features_from_plan(plan_path: Path) -> list[FeatureSpec]:
    """Generate features.json from PLAN.md.

    Parses markdown task tables with format:
    | Task ID | Task | Effort | ... |
    | H1.1.1 | Extract CLI parsing tests | 1.5h | ... |

    Args:
        plan_path: Path to PLAN.md

    Returns:
        List of FeatureSpec objects
    """
    if not plan_path.exists():
        raise FileNotFoundError(f"Plan file not found: {plan_path}")

    content = plan_path.read_text()
    features: list[FeatureSpec] = []

    # Parse context: current epic/section for grouping
    current_section = ""
    current_priority = "medium"

    lines = content.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Track Epic headers (## Epic H1: ...)
        if line.startswith("## Epic") or line.startswith("## "):
            line.replace("## ", "").strip()
            # Check for priority in current line
            priority_found = _extract_priority_from_line(line)
            if priority_found:
                current_priority = priority_found
            else:
                # Look ahead for **Priority:** line
                for j in range(i + 1, min(i + 5, len(lines))):
                    next_line = lines[j].strip()
                    if next_line.startswith("##"):
                        break  # New section, stop looking
                    priority_found = _extract_priority_from_line(next_line)
                    if priority_found:
                        current_priority = priority_found
                        break
            i += 1
            continue

        # Track section headers (### H1.1: ...)
        if line.startswith("### "):
            current_section = line.replace("### ", "").strip()
            i += 1
            continue

        # Look for task tables with ID columns
        # Format: | Task ID | Task | Effort | ... |
        if line.startswith("|") and ("Task ID" in line or "task id" in line.lower()):
            # Found a task table header
            # Skip the separator row (|---|---|...)
            i += 1
            if i < len(lines) and lines[i].strip().startswith("|") and "---" in lines[i]:
                i += 1

            # Parse table rows
            while i < len(lines):
                row = lines[i].strip()
                if not row.startswith("|"):
                    break

                # Parse table row
                cells = [c.strip() for c in row.split("|")[1:-1]]  # Remove empty first/last

                if len(cells) >= 2:
                    task_id = cells[0].strip()
                    task_name = cells[1].strip()

                    # Skip empty rows or rows that look like headers
                    if not task_id or task_id.lower() == "task id" or "---" in task_id:
                        i += 1
                        continue

                    # Extract effort estimate (column 3 if present)
                    effort = cells[2].strip() if len(cells) >= 3 else "2h"

                    # Estimate tokens from effort
                    tokens = _estimate_tokens_from_effort(effort)

                    # Create feature spec
                    feature = FeatureSpec(
                        id=task_id,
                        name=task_name,
                        description=f"{current_section}: {task_name}"
                        if current_section
                        else task_name,
                        status=FeatureStatus.PENDING,
                        priority=current_priority,
                        acceptance_criteria=[f"Task {task_id} implemented and tested"],
                        depends_on=[],
                        tests=[],
                        estimated_tokens=tokens,
                    )

                    # Infer dependencies from task ordering within same section
                    if features and features[-1].id.startswith(task_id.rsplit(".", 1)[0] + "."):
                        # Same parent section, add dependency
                        feature.depends_on.append(features[-1].id)

                    features.append(feature)

                i += 1
            continue

        # Also check for simpler task tables (| Task | Est | Priority |)
        if line.startswith("|") and "Task" in line and "Est" in line:
            # Found a simpler task table
            i += 1
            if i < len(lines) and lines[i].strip().startswith("|") and "---" in lines[i]:
                i += 1

            # Generate IDs for tasks without explicit IDs
            task_counter = 1

            while i < len(lines):
                row = lines[i].strip()
                if not row.startswith("|"):
                    break

                cells = [c.strip() for c in row.split("|")[1:-1]]

                if len(cells) >= 2:
                    task_name = cells[0].strip()
                    effort = cells[1].strip() if len(cells) >= 2 else "2h"

                    if not task_name or "---" in task_name or task_name.lower() == "task":
                        i += 1
                        continue

                    # Generate ID from section or use counter
                    section_prefix = (
                        current_section.split(":")[0].strip() if current_section else "T"
                    )
                    task_id = f"{section_prefix}.{task_counter}"
                    task_counter += 1

                    tokens = _estimate_tokens_from_effort(effort)

                    feature = FeatureSpec(
                        id=task_id,
                        name=task_name,
                        description=f"{current_section}: {task_name}"
                        if current_section
                        else task_name,
                        status=FeatureStatus.PENDING,
                        priority=current_priority,
                        acceptance_criteria=[f"Task completed: {task_name}"],
                        depends_on=[],
                        tests=[],
                        estimated_tokens=tokens,
                    )

                    features.append(feature)

                i += 1
            continue

        i += 1

    logger.info(f"Parsed {len(features)} features from {plan_path}")
    return features


def _estimate_tokens_from_effort(effort: str) -> int:
    """Estimate token count from effort string.

    Args:
        effort: Effort string like "1.5h", "30m", "2h"

    Returns:
        Estimated tokens (roughly 2000 tokens per hour of work)
    """
    effort = effort.lower().strip()

    try:
        if "h" in effort:
            # Hours: "1.5h", "2h"
            hours = float(effort.replace("h", "").strip())
            return int(hours * 2000)
        elif "m" in effort:
            # Minutes: "30m", "45m"
            minutes = float(effort.replace("m", "").strip())
            return int((minutes / 60) * 2000)
        else:
            # Default to 2h estimate
            return 4000
    except (ValueError, TypeError):
        return 4000
