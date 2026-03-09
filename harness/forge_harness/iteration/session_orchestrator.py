"""Session orchestrator for FORGE Harness.

Coordinates iteration tracking, auto-committing, and PR generation
into a unified workflow for compound engineering sessions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from forge_harness.logging_config import get_logger

from .auto_commit import CommitContext, CommitResult, create_auto_committer
from .pr_generator import PRContext, PRResult, create_pr_generator
from .tracker import IterationRecord, TrackerStats, create_tracker

logger = get_logger(__name__)


@dataclass
class SessionConfig:
    """Configuration for a session."""

    project_name: str
    base_branch: str = "main"
    auto_commit: bool = True
    auto_pr: bool = False
    pr_draft: bool = True
    commit_scope: str = "harness"


@dataclass
class SessionResult:
    """Result of a complete session."""

    success: bool
    iteration_number: int
    features_completed: list[str] = field(default_factory=list)
    commit_result: CommitResult | None = None
    pr_result: PRResult | None = None
    stats: TrackerStats | None = None
    error: str | None = None
    duration_seconds: float = 0.0


class SessionOrchestrator:
    """Orchestrates complete development sessions."""

    def __init__(
        self, config: SessionConfig, repo_path: Path | None = None, tracker_path: Path | None = None
    ):
        self.config = config
        self.repo_path = repo_path or Path.cwd()
        self.tracker = create_tracker(tracker_path)
        self.committer = create_auto_committer(self.repo_path)
        self.pr_generator = create_pr_generator(self.repo_path)
        self._current_iteration: int | None = None

    def start_iteration(self) -> int:
        """Start a new iteration. Returns iteration number."""
        # Determine iteration number
        stats = self.tracker.get_stats()
        iteration_num = stats.total_iterations + 1

        self.tracker.start_iteration(iteration_num)
        self._current_iteration = iteration_num

        logger.info(f"Started iteration {iteration_num}")
        return iteration_num

    def complete_iteration(
        self,
        features: list[str],
        tests_added: int = 0,
        lines_added: int = 0,
        files_changed: list[str] | None = None,
        notes: str = "",
    ) -> SessionResult:
        """Complete the current iteration with optional commit and PR."""
        start_time = datetime.now()

        if self._current_iteration is None:
            return SessionResult(
                success=False,
                iteration_number=0,
                error="No iteration in progress. Call start_iteration() first.",
            )

        iteration_num = self._current_iteration
        commit_result = None
        pr_result = None

        try:
            # Commit if configured
            if self.config.auto_commit and features:
                commit_context = CommitContext(
                    features=features,
                    tests_added=tests_added,
                    lines_added=lines_added,
                    files_changed=files_changed or [],
                    iteration_number=iteration_num,
                    scope=self.config.commit_scope,
                )
                commit_result = self.committer.commit(commit_context)

                if not commit_result.success:
                    logger.warning(f"Commit failed: {commit_result.error}")

            # Create PR if configured
            if self.config.auto_pr and commit_result and commit_result.success:
                pr_context = PRContext(
                    title=f"feat({self.config.commit_scope}): {', '.join(features[:2])}",
                    features=features,
                    tests_added=tests_added,
                    lines_added=lines_added,
                    iterations_completed=1,
                    base_branch=self.config.base_branch,
                    draft=self.config.pr_draft,
                )
                pr_result = self.pr_generator.create_pr(pr_context)

            # Complete the iteration in tracker
            self.tracker.complete_iteration(
                features=features,
                tests_added=tests_added,
                lines_added=lines_added,
                commit_hash=commit_result.commit_hash if commit_result else None,
                notes=notes,
            )

            self._current_iteration = None

            duration = (datetime.now() - start_time).total_seconds()

            return SessionResult(
                success=True,
                iteration_number=iteration_num,
                features_completed=features,
                commit_result=commit_result,
                pr_result=pr_result,
                stats=self.tracker.get_stats(),
                duration_seconds=duration,
            )

        except Exception as e:
            logger.error(f"Session failed: {e}")
            self.tracker.fail_iteration(reason=str(e))
            self._current_iteration = None

            return SessionResult(
                success=False,
                iteration_number=iteration_num,
                error=str(e),
            )

    def fail_iteration(self, reason: str = "") -> SessionResult:
        """Mark current iteration as failed."""
        if self._current_iteration is None:
            return SessionResult(
                success=False, iteration_number=0, error="No iteration in progress"
            )

        iteration_num = self._current_iteration
        self.tracker.fail_iteration(reason=reason)
        self._current_iteration = None

        return SessionResult(
            success=False,
            iteration_number=iteration_num,
            error=reason,
            stats=self.tracker.get_stats(),
        )

    def get_stats(self) -> TrackerStats:
        """Get current session statistics."""
        return self.tracker.get_stats()

    def get_recent_iterations(self, count: int = 5) -> list[IterationRecord]:
        """Get recent iteration records."""
        return self.tracker.get_recent(count)


def create_session_orchestrator(
    project_name: str,
    repo_path: Path | None = None,
    auto_commit: bool = True,
    auto_pr: bool = False,
) -> SessionOrchestrator:
    """Factory function to create a SessionOrchestrator."""
    config = SessionConfig(
        project_name=project_name,
        auto_commit=auto_commit,
        auto_pr=auto_pr,
    )
    return SessionOrchestrator(config=config, repo_path=repo_path)
