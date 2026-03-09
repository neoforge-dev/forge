"""Iteration loop executor for FORGE Harness.

Runs N iterations autonomously with progress tracking and failure handling.

Includes meta-learning integration for compounding engineering velocity:
- DecisionEngine queries for intelligent routing
- Pattern outcome recording for effectiveness tracking
- Threshold adjustment based on success rates
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from forge_harness.logging_config import get_logger

from .session_orchestrator import (
    create_session_orchestrator,
)
from .tracker import TrackerStats

if TYPE_CHECKING:
    from forge_harness.meta_learning import DecisionEngine
    from forge_harness.meta_learning.learning_store import LearningStore

logger = get_logger(__name__)


@dataclass
class LoopConfig:
    """Configuration for iteration loop."""

    project_name: str
    domain: str | None = None
    max_iterations: int = 100
    cooldown_seconds: float = 30.0
    max_consecutive_failures: int = 3
    auto_commit: bool = True
    auto_pr: bool = False
    stop_on_all_complete: bool = True
    enable_meta_learning: bool = True  # Enable DecisionEngine and pattern tracking


@dataclass
class IterationSummary:
    """Summary of a single iteration."""

    iteration_number: int
    success: bool
    features_completed: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class LoopResult:
    """Result of the entire loop execution."""

    success: bool
    iterations_completed: int
    iterations_failed: int
    total_features: int
    total_duration_seconds: float
    summaries: list[IterationSummary] = field(default_factory=list)
    stopped_reason: str | None = None
    final_stats: TrackerStats | None = None


class LoopExecutor:
    """Executes multiple iterations in a loop.

    Includes meta-learning integration:
    - DecisionEngine for intelligent routing decisions
    - LearningStore for pattern outcome tracking
    - Automatic threshold adjustment based on success rates
    """

    def __init__(
        self,
        config: LoopConfig,
        repo_path: Path | None = None,
        feature_source: Callable[[], list[str]] | None = None,
        decision_engine: "DecisionEngine | None" = None,
        learning_store: "LearningStore | None" = None,
    ):
        self.config = config
        self.repo_path = repo_path or Path.cwd()
        self.feature_source = feature_source or self._default_feature_source
        self._orchestrator = create_session_orchestrator(
            project_name=config.project_name,
            repo_path=self.repo_path,
            auto_commit=config.auto_commit,
            auto_pr=config.auto_pr,
        )
        self._should_stop = False
        self._consecutive_failures = 0

        # Meta-learning integration
        self.decision_engine = decision_engine
        self.learning_store = learning_store

        # Auto-create learning store if meta-learning enabled and not provided
        if config.enable_meta_learning and learning_store is None:
            self._init_meta_learning()

    def _init_meta_learning(self) -> None:
        """Initialize meta-learning components if enabled."""
        try:
            from forge_harness.meta_learning.learning_store import LearningStore

            learning_path = Path(".forge/learning")
            if learning_path.exists() or self.config.enable_meta_learning:
                learning_path.mkdir(parents=True, exist_ok=True)
                self.learning_store = LearningStore(learning_path)
                logger.info("Meta-learning: LearningStore initialized")

                # Try to create DecisionEngine with the learning store
                try:
                    from forge_harness.meta_learning import DecisionEngine

                    self.decision_engine = DecisionEngine(
                        learning_store=self.learning_store,
                    )
                    logger.info("Meta-learning: DecisionEngine initialized")
                except ImportError:
                    logger.debug("DecisionEngine not available")
        except ImportError:
            logger.debug("Meta-learning components not available")
        except Exception as e:
            logger.warning(f"Failed to initialize meta-learning: {e}")

    def _default_feature_source(self) -> list[str]:
        """Default feature source - returns empty list."""
        return []

    def _record_iteration_outcome(
        self,
        iteration: int,
        features: list[str],
        success: bool,
        duration: float,
    ) -> None:
        """Record iteration outcome to meta-learning store.

        Updates pattern effectiveness tracking for compounding learning.

        Args:
            iteration: Iteration number
            features: Features worked on
            success: Whether iteration succeeded
            duration: Duration in seconds
        """
        if not self.learning_store:
            return

        try:
            from forge_harness.meta_learning.schemas import PatternRecord

            # Record pattern for each feature type
            feature_types = set()
            for feature in features:
                # Extract feature type from name (e.g., "auth-001" -> "auth")
                if "-" in feature:
                    feature_types.add(feature.split("-")[0])
                else:
                    feature_types.add("general")

            for feature_type in feature_types:
                pattern_id = f"iteration-{feature_type}"

                # Ensure pattern exists
                existing = self.learning_store.get_pattern(pattern_id)
                if existing is None:
                    # Compute context signature
                    context_sig = self.learning_store.compute_context_signature(
                        domain=self.config.domain or "unknown",
                        project=self.config.project_name,
                        file_paths=[],
                        tags=[feature_type, "iteration"],
                    )
                    new_pattern = PatternRecord(
                        pattern_id=pattern_id,
                        context_signature=context_sig,
                        pattern_type=f"iteration:{feature_type}",
                        total_applications=0,
                        successful_applications=0,
                    )
                    self.learning_store.record_pattern(new_pattern)

                # Update outcome
                self.learning_store.update_pattern_outcome(pattern_id, success)

            logger.debug(
                f"Recorded iteration {iteration} outcome: success={success}, "
                f"feature_types={feature_types}"
            )

            # Adjust thresholds if domain/project configured
            if self.config.domain and self.config.project_name:
                adjustment = self.learning_store.adjust_thresholds(
                    domain=self.config.domain,
                    project=self.config.project_name,
                )
                if adjustment:
                    logger.info(f"Threshold adjustment: {adjustment.reason}")

        except Exception as e:
            logger.warning(f"Failed to record iteration outcome: {e}")

    def stop(self) -> None:
        """Signal the loop to stop after current iteration."""
        self._should_stop = True
        logger.info("Stop signal received, will stop after current iteration")

    def run(self) -> LoopResult:
        """Run the iteration loop synchronously."""
        start_time = datetime.now()
        summaries: list[IterationSummary] = []
        iterations_completed = 0
        iterations_failed = 0
        total_features = 0
        stopped_reason = None

        for i in range(1, self.config.max_iterations + 1):
            if self._should_stop:
                stopped_reason = "Manual stop requested"
                break

            if self._consecutive_failures >= self.config.max_consecutive_failures:
                stopped_reason = f"Too many consecutive failures ({self._consecutive_failures})"
                break

            logger.info(f"=== Starting Iteration {i}/{self.config.max_iterations} ===")

            # Get features to implement
            features = self.feature_source()
            if not features and self.config.stop_on_all_complete:
                stopped_reason = "All features completed"
                break

            # Run iteration
            iter_start = datetime.now()
            try:
                self._orchestrator.start_iteration()

                # Simulate work (in real usage, agents would do the work)
                result = self._orchestrator.complete_iteration(
                    features=features,
                    tests_added=len(features) * 5,  # Estimate
                    lines_added=len(features) * 100,  # Estimate
                )

                iter_duration = (datetime.now() - iter_start).total_seconds()

                if result.success:
                    iterations_completed += 1
                    total_features += len(result.features_completed)
                    self._consecutive_failures = 0

                    summaries.append(
                        IterationSummary(
                            iteration_number=i,
                            success=True,
                            features_completed=result.features_completed,
                            duration_seconds=iter_duration,
                        )
                    )

                    # Record success to meta-learning
                    self._record_iteration_outcome(
                        iteration=i,
                        features=result.features_completed,
                        success=True,
                        duration=iter_duration,
                    )
                else:
                    iterations_failed += 1
                    self._consecutive_failures += 1

                    summaries.append(
                        IterationSummary(
                            iteration_number=i,
                            success=False,
                            error=result.error,
                            duration_seconds=iter_duration,
                        )
                    )

                    # Record failure to meta-learning
                    self._record_iteration_outcome(
                        iteration=i,
                        features=features,
                        success=False,
                        duration=iter_duration,
                    )

            except Exception as e:
                iterations_failed += 1
                self._consecutive_failures += 1
                iter_duration = (datetime.now() - iter_start).total_seconds()

                summaries.append(
                    IterationSummary(
                        iteration_number=i,
                        success=False,
                        error=str(e),
                        duration_seconds=iter_duration,
                    )
                )
                logger.error(f"Iteration {i} failed with error: {e}")

            # Cooldown between iterations
            if i < self.config.max_iterations and not self._should_stop:
                logger.info(f"Cooldown: {self.config.cooldown_seconds}s")
                time.sleep(self.config.cooldown_seconds)

        total_duration = (datetime.now() - start_time).total_seconds()

        return LoopResult(
            success=iterations_failed == 0 or iterations_completed > iterations_failed,
            iterations_completed=iterations_completed,
            iterations_failed=iterations_failed,
            total_features=total_features,
            total_duration_seconds=total_duration,
            summaries=summaries,
            stopped_reason=stopped_reason,
            final_stats=self._orchestrator.get_stats(),
        )

    async def run_async(self) -> LoopResult:
        """Run the iteration loop asynchronously."""
        start_time = datetime.now()
        summaries: list[IterationSummary] = []
        iterations_completed = 0
        iterations_failed = 0
        total_features = 0
        stopped_reason = None

        for i in range(1, self.config.max_iterations + 1):
            if self._should_stop:
                stopped_reason = "Manual stop requested"
                break

            if self._consecutive_failures >= self.config.max_consecutive_failures:
                stopped_reason = f"Too many consecutive failures ({self._consecutive_failures})"
                break

            logger.info(f"=== Starting Iteration {i}/{self.config.max_iterations} ===")

            features = self.feature_source()
            if not features and self.config.stop_on_all_complete:
                stopped_reason = "All features completed"
                break

            iter_start = datetime.now()
            try:
                self._orchestrator.start_iteration()
                result = self._orchestrator.complete_iteration(
                    features=features,
                    tests_added=len(features) * 5,
                    lines_added=len(features) * 100,
                )

                iter_duration = (datetime.now() - iter_start).total_seconds()

                if result.success:
                    iterations_completed += 1
                    total_features += len(result.features_completed)
                    self._consecutive_failures = 0
                    summaries.append(
                        IterationSummary(
                            iteration_number=i,
                            success=True,
                            features_completed=result.features_completed,
                            duration_seconds=iter_duration,
                        )
                    )

                    # Record success to meta-learning
                    self._record_iteration_outcome(
                        iteration=i,
                        features=result.features_completed,
                        success=True,
                        duration=iter_duration,
                    )
                else:
                    iterations_failed += 1
                    self._consecutive_failures += 1
                    summaries.append(
                        IterationSummary(
                            iteration_number=i,
                            success=False,
                            error=result.error,
                            duration_seconds=iter_duration,
                        )
                    )

                    # Record failure to meta-learning
                    self._record_iteration_outcome(
                        iteration=i,
                        features=features,
                        success=False,
                        duration=iter_duration,
                    )

            except Exception as e:
                iterations_failed += 1
                self._consecutive_failures += 1
                iter_duration = (datetime.now() - iter_start).total_seconds()
                summaries.append(
                    IterationSummary(
                        iteration_number=i,
                        success=False,
                        error=str(e),
                        duration_seconds=iter_duration,
                    )
                )

                # Record exception to meta-learning
                self._record_iteration_outcome(
                    iteration=i,
                    features=features,
                    success=False,
                    duration=iter_duration,
                )

            if i < self.config.max_iterations and not self._should_stop:
                await asyncio.sleep(self.config.cooldown_seconds)

        return LoopResult(
            success=iterations_failed == 0 or iterations_completed > iterations_failed,
            iterations_completed=iterations_completed,
            iterations_failed=iterations_failed,
            total_features=total_features,
            total_duration_seconds=(datetime.now() - start_time).total_seconds(),
            summaries=summaries,
            stopped_reason=stopped_reason,
            final_stats=self._orchestrator.get_stats(),
        )

    def generate_summary_report(self, result: LoopResult) -> str:
        """Generate a markdown summary report."""
        lines = [
            "# Iteration Loop Summary",
            "",
            f"**Project:** {self.config.project_name}",
            f"**Total Duration:** {result.total_duration_seconds:.1f}s",
            f"**Stop Reason:** {result.stopped_reason or 'Completed all iterations'}",
            "",
            "## Statistics",
            "",
            f"- Iterations Completed: {result.iterations_completed}",
            f"- Iterations Failed: {result.iterations_failed}",
            f"- Total Features: {result.total_features}",
            f"- Success Rate: {result.iterations_completed / max(1, result.iterations_completed + result.iterations_failed) * 100:.1f}%",
            "",
            "## Iteration Details",
            "",
            "| # | Status | Features | Duration | Error |",
            "|---|--------|----------|----------|-------|",
        ]

        for summary in result.summaries:
            status = "✅" if summary.success else "❌"
            features = ", ".join(summary.features_completed[:2])
            if len(summary.features_completed) > 2:
                features += f" +{len(summary.features_completed) - 2}"
            error = (
                summary.error[:30] + "..."
                if summary.error and len(summary.error) > 30
                else (summary.error or "-")
            )
            lines.append(
                f"| {summary.iteration_number} | {status} | {features or '-'} | {summary.duration_seconds:.1f}s | {error} |"
            )

        return "\n".join(lines)


def create_loop_executor(
    project_name: str,
    domain: str | None = None,
    max_iterations: int = 100,
    cooldown_seconds: float = 30.0,
    repo_path: Path | None = None,
    feature_source: Callable[[], list[str]] | None = None,
    enable_meta_learning: bool = True,
) -> LoopExecutor:
    """Factory function to create a LoopExecutor.

    Args:
        project_name: Name of the project
        domain: Domain name for meta-learning context (e.g., "codeswiftr-com")
        max_iterations: Maximum iterations to run
        cooldown_seconds: Seconds between iterations
        repo_path: Repository path (defaults to cwd)
        feature_source: Callable that returns list of features to work on
        enable_meta_learning: Enable DecisionEngine and pattern tracking

    Returns:
        Configured LoopExecutor with optional meta-learning integration
    """
    config = LoopConfig(
        project_name=project_name,
        domain=domain,
        max_iterations=max_iterations,
        cooldown_seconds=cooldown_seconds,
        enable_meta_learning=enable_meta_learning,
    )
    return LoopExecutor(config=config, repo_path=repo_path, feature_source=feature_source)


def create_loop_executor_with_meta_learning(
    project_name: str,
    domain: str,
    max_iterations: int = 100,
    repo_path: Path | None = None,
    feature_source: Callable[[], list[str]] | None = None,
) -> LoopExecutor:
    """Factory function to create a fully-wired LoopExecutor with meta-learning.

    This is the recommended way to create a LoopExecutor for production use.
    It automatically:
    - Creates a LearningStore for pattern persistence
    - Creates a DecisionEngine for intelligent routing
    - Enables pattern outcome recording
    - Enables threshold adjustment

    Args:
        project_name: Name of the project
        domain: Domain name (required for threshold adjustment)
        max_iterations: Maximum iterations to run
        repo_path: Repository path (defaults to cwd)
        feature_source: Callable that returns list of features to work on

    Returns:
        Fully-wired LoopExecutor with meta-learning enabled
    """
    from forge_harness.meta_learning import DecisionEngine
    from forge_harness.meta_learning.learning_store import LearningStore

    # Create learning store
    learning_path = Path(".forge/learning")
    learning_path.mkdir(parents=True, exist_ok=True)
    learning_store = LearningStore(learning_path)

    # Create decision engine
    decision_engine = DecisionEngine(learning_store=learning_store)

    config = LoopConfig(
        project_name=project_name,
        domain=domain,
        max_iterations=max_iterations,
        enable_meta_learning=True,
    )

    return LoopExecutor(
        config=config,
        repo_path=repo_path,
        feature_source=feature_source,
        decision_engine=decision_engine,
        learning_store=learning_store,
    )
