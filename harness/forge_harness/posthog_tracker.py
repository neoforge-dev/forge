"""
PostHog Session Tracking for FORGE Harness
===========================================

Tracks autonomous agent session metrics to PostHog for visibility
into harness performance, issue completion rates, and deployment success.

Uses the FORGE PostHog reverse proxy at api.codeswiftr.com/ph.
"""

import asyncio
import os
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

try:
    from forge_shared.analytics import PostHogClient

    POSTHOG_AVAILABLE = True
except ImportError:
    PostHogClient = None
    POSTHOG_AVAILABLE = False


# Default PostHog configuration
DEFAULT_POSTHOG_HOST = "https://api.codeswiftr.com/ph"


@dataclass
class SessionMetrics:
    """Metrics collected during a harness session."""

    issues_attempted: int = 0
    issues_completed: int = 0
    issues_failed: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    coverage_backend: float | None = None
    coverage_frontend: float | None = None
    commits_made: int = 0
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    errors_encountered: list[str] = field(default_factory=list)


class HarnessTracker:
    """
    PostHog tracker for FORGE harness sessions.

    Tracks session lifecycle events and metrics for observability.

    Usage:
        tracker = HarnessTracker(
            domain="codeswiftr-com",
            project="interview-simulator",
            session_id="abc123"
        )

        tracker.session_started(model="claude-opus-4-5-20251101")

        # During session
        tracker.issue_started(issue_id=42, issue_title="Add login")
        tracker.issue_completed(issue_id=42, duration_minutes=15)

        # End session
        tracker.session_ended(metrics)
    """

    def __init__(
        self,
        domain: str,
        project: str,
        session_id: str,
        api_key: str | None = None,
        host: str | None = None,
        enabled: bool = True,
    ):
        """
        Initialize the tracker.

        Args:
            domain: FORGE domain name
            project: Project name within domain
            session_id: Unique session identifier
            api_key: PostHog API key (defaults to POSTHOG_API_KEY env var)
            host: PostHog host URL (defaults to FORGE reverse proxy)
            enabled: Whether tracking is enabled
        """
        self.domain = domain
        self.project = project
        self.session_id = session_id
        self.start_time = datetime.now()
        self.enabled = enabled and POSTHOG_AVAILABLE
        self._client: PostHogClient | None = None

        if self.enabled:
            api_key = api_key or os.environ.get("POSTHOG_API_KEY")
            if not api_key:
                self.enabled = False
                return

            self._client = PostHogClient(
                api_key=api_key,
                host=host or os.environ.get("POSTHOG_HOST", DEFAULT_POSTHOG_HOST),
                enabled=True,
                flush_at=1,
                flush_interval=1,
            )

    @property
    def distinct_id(self) -> str:
        """Get the distinct ID for this session."""
        return f"harness_{self.session_id}"

    def _capture(self, event: str, properties: dict[str, Any] | None = None) -> None:
        """Capture an event to PostHog."""
        if not self.enabled or self._client is None:
            return

        base_properties = {
            "domain": self.domain,
            "project": self.project,
            "session_id": self.session_id,
            "service": "forge-harness",
        }

        if properties:
            base_properties.update(properties)

        self._run_async(
            self._client.track(
                event=event,
                distinct_id=self.distinct_id,
                properties=base_properties,
            )
        )

    def _run_async(self, coro: Awaitable[None]) -> None:
        """Run analytics calls without blocking the request path."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return

        loop.create_task(coro)

    def track_event(self, event: str, properties: dict[str, Any] | None = None) -> None:
        """
        Track a custom event.

        Public method for tracking arbitrary events.

        Args:
            event: Event name
            properties: Optional additional properties
        """
        self._capture(event, properties)

    def session_started(
        self,
        model: str,
        is_first_run: bool = False,
        max_iterations: int | None = None,
    ) -> None:
        """
        Track session start.

        Args:
            model: Claude model being used
            is_first_run: Whether this is the first run for this project
            max_iterations: Maximum iterations configured (None = unlimited)
        """
        self._capture(
            "harness_session_started",
            {
                "model": model,
                "is_first_run": is_first_run,
                "max_iterations": max_iterations,
            },
        )

    def issue_started(self, issue_id: int, issue_title: str, priority: str) -> None:
        """
        Track when work begins on an issue.

        Args:
            issue_id: GitHub issue number
            issue_title: Issue title
            priority: Issue priority (critical, high, medium, low)
        """
        self._capture(
            "harness_issue_started",
            {
                "issue_id": issue_id,
                "issue_title": issue_title,
                "priority": priority,
            },
        )

    def issue_completed(
        self,
        issue_id: int,
        issue_title: str,
        duration_minutes: int,
        tests_added: int = 0,
    ) -> None:
        """
        Track issue completion.

        Args:
            issue_id: GitHub issue number
            issue_title: Issue title
            duration_minutes: Time spent on issue
            tests_added: Number of tests added
        """
        self._capture(
            "harness_issue_completed",
            {
                "issue_id": issue_id,
                "issue_title": issue_title,
                "duration_minutes": duration_minutes,
                "tests_added": tests_added,
            },
        )

    def issue_failed(
        self,
        issue_id: int,
        issue_title: str,
        reason: str,
        duration_minutes: int,
    ) -> None:
        """
        Track when an issue cannot be completed.

        Args:
            issue_id: GitHub issue number
            issue_title: Issue title
            reason: Why the issue failed (blocked, error, human_gate, etc.)
            duration_minutes: Time spent before failure
        """
        self._capture(
            "harness_issue_failed",
            {
                "issue_id": issue_id,
                "issue_title": issue_title,
                "failure_reason": reason,
                "duration_minutes": duration_minutes,
            },
        )

    def tests_run(
        self,
        test_type: str,
        passed: int,
        failed: int,
        coverage: float | None = None,
    ) -> None:
        """
        Track test execution.

        Args:
            test_type: Type of tests (backend, frontend, e2e)
            passed: Number of tests passed
            failed: Number of tests failed
            coverage: Coverage percentage (if available)
        """
        self._capture(
            "harness_tests_run",
            {
                "test_type": test_type,
                "tests_passed": passed,
                "tests_failed": failed,
                "coverage": coverage,
            },
        )

    def verification_completed(
        self,
        issue_number: int,
        passed: bool,
        tests_passed: int,
        tests_failed: int,
        coverage_backend: float,
        coverage_frontend: float,
        lint_errors: int,
    ) -> None:
        """
        Track post-coding verification results.

        Args:
            issue_number: GitHub issue number
            passed: Whether verification passed all quality gates
            tests_passed: Total tests passed (backend + frontend)
            tests_failed: Total tests failed (backend + frontend)
            coverage_backend: Backend code coverage percentage
            coverage_frontend: Frontend code coverage percentage
            lint_errors: Number of linting errors
        """
        self._capture(
            "harness_verification_completed",
            {
                "issue_number": issue_number,
                "verification_passed": passed,
                "tests_passed": tests_passed,
                "tests_failed": tests_failed,
                "coverage_backend": coverage_backend,
                "coverage_frontend": coverage_frontend,
                "lint_errors": lint_errors,
            },
        )

    def human_gate_triggered(self, gate_type: str, details: str) -> None:
        """
        Track when a human gate is triggered.

        Args:
            gate_type: Type of gate (security, compliance, architecture, etc.)
            details: Description of what triggered the gate
        """
        self._capture(
            "harness_human_gate",
            {
                "gate_type": gate_type,
                "details": details,
            },
        )

    def deployment_triggered(
        self,
        target: str,
        success: bool,
        duration_seconds: int | None = None,
        error: str | None = None,
    ) -> None:
        """
        Track deployment attempt.

        Args:
            target: Deployment target (railway, cloudflare)
            success: Whether deployment succeeded
            duration_seconds: Time taken for deployment
            error: Error message if failed
        """
        self._capture(
            "harness_deployment",
            {
                "target": target,
                "success": success,
                "duration_seconds": duration_seconds,
                "error": error,
            },
        )

    def error_occurred(self, error_type: str, error_message: str) -> None:
        """
        Track an error during the session.

        Args:
            error_type: Category of error (test_failure, build_error, api_error, etc.)
            error_message: Error details
        """
        self._capture(
            "harness_error",
            {
                "error_type": error_type,
                "error_message": error_message[:500],  # Truncate long errors
            },
        )

    def iteration_completed(
        self,
        iteration_number: int,
        issue_id: int,
        duration_minutes: float,
        tests_added: int,
        coverage_delta: float,
        tokens_input: int,
        tokens_output: int,
        api_cost_usd: float,
    ) -> None:
        """
        Track detailed iteration metrics for cost and performance analysis.

        Args:
            iteration_number: Which iteration this was (1-based)
            issue_id: GitHub issue number being worked on
            duration_minutes: Time spent on this iteration
            tests_added: Number of tests added in this iteration
            coverage_delta: Change in coverage (can be negative)
            tokens_input: Input tokens consumed
            tokens_output: Output tokens generated
            api_cost_usd: Estimated API cost for this iteration
        """
        self._capture(
            "harness_iteration_completed",
            {
                "iteration_number": iteration_number,
                "issue_id": issue_id,
                "duration_minutes": round(duration_minutes, 2),
                "tests_added": tests_added,
                "coverage_delta": round(coverage_delta, 2),
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "api_cost_usd": round(api_cost_usd, 4),
            },
        )

    def error_pattern_detected(
        self,
        error_type: str,
        error_hash: str,
        recurrence_count: int,
        first_seen: str | None = None,
    ) -> None:
        """
        Track recurring error patterns for debugging and prevention.

        Args:
            error_type: Category of error (import_error, test_failure, lint_error, etc.)
            error_hash: Hash of the error message for deduplication
            recurrence_count: How many times this pattern has been seen
            first_seen: ISO timestamp when this pattern was first seen
        """
        self._capture(
            "harness_error_pattern",
            {
                "error_type": error_type,
                "error_hash": error_hash,
                "recurrence_count": recurrence_count,
                "first_seen": first_seen,
            },
        )

    def preflight_completed(
        self,
        passed: bool,
        issues_found: int,
        fixes_applied: int,
        issues_remaining: list[str],
    ) -> None:
        """
        Track pre-flight check results.

        Args:
            passed: Whether pre-flight checks passed
            issues_found: Number of issues initially found
            fixes_applied: Number of auto-fixes applied
            issues_remaining: List of issues that couldn't be auto-fixed
        """
        self._capture(
            "harness_preflight_completed",
            {
                "preflight_passed": passed,
                "issues_found": issues_found,
                "fixes_applied": fixes_applied,
                "issues_remaining": issues_remaining[:10],  # Limit to first 10
            },
        )

    # =========================================================================
    # Content Generation Events
    # =========================================================================

    def content_generation_started(
        self,
        brief_id: str,
        content_type: str,
        title: str,
        auto_approve: bool = False,
    ) -> None:
        """
        Track start of content generation.

        Args:
            brief_id: Unique brief identifier
            content_type: Type of content (blog_posts, social_media, etc.)
            title: Content title/topic
            auto_approve: Whether auto-approve mode is enabled
        """
        self._capture(
            "content_generation_started",
            {
                "brief_id": brief_id,
                "content_type": content_type,
                "title": title,
                "auto_approve": auto_approve,
            },
        )

    def content_generation_completed(
        self,
        brief_id: str,
        content_type: str,
        final_stage: str,
        revision_rounds: int,
        duration_seconds: float,
        word_count: int | None = None,
        error_count: int = 0,
    ) -> None:
        """
        Track completion of content generation.

        Args:
            brief_id: Unique brief identifier
            content_type: Type of content
            final_stage: Final stage (approved, published, error, review)
            revision_rounds: Number of revision iterations
            duration_seconds: Total generation time
            word_count: Generated content word count
            error_count: Number of errors encountered
        """
        self._capture(
            "content_generation_completed",
            {
                "brief_id": brief_id,
                "content_type": content_type,
                "final_stage": final_stage,
                "revision_rounds": revision_rounds,
                "duration_seconds": round(duration_seconds, 2),
                "word_count": word_count,
                "error_count": error_count,
                "success": final_stage in ("approved", "published"),
            },
        )

    def content_revision_requested(
        self,
        brief_id: str,
        content_type: str,
        revision_round: int,
        feedback_preview: str,
    ) -> None:
        """
        Track revision request from human feedback.

        Args:
            brief_id: Unique brief identifier
            content_type: Type of content
            revision_round: Current revision round number
            feedback_preview: First 200 chars of feedback
        """
        self._capture(
            "content_revision_requested",
            {
                "brief_id": brief_id,
                "content_type": content_type,
                "revision_round": revision_round,
                "feedback_preview": feedback_preview[:200],
            },
        )

    def content_approved(
        self,
        brief_id: str,
        content_type: str,
        approval_method: str,
        revision_rounds: int,
    ) -> None:
        """
        Track content approval.

        Args:
            brief_id: Unique brief identifier
            content_type: Type of content
            approval_method: How it was approved (human, auto_approve, timeout)
            revision_rounds: Number of revisions before approval
        """
        self._capture(
            "content_approved",
            {
                "brief_id": brief_id,
                "content_type": content_type,
                "approval_method": approval_method,
                "revision_rounds": revision_rounds,
            },
        )

    def content_batch_completed(
        self,
        content_types: list[str],
        total_pieces: int,
        successful: int,
        failed: int,
        duration_minutes: float,
    ) -> None:
        """
        Track batch content generation completion.

        Args:
            content_types: List of content types generated
            total_pieces: Total content pieces attempted
            successful: Number successfully generated
            failed: Number that failed
            duration_minutes: Total batch duration
        """
        self._capture(
            "content_batch_completed",
            {
                "content_types": content_types,
                "total_pieces": total_pieces,
                "successful": successful,
                "failed": failed,
                "success_rate": round(successful / max(total_pieces, 1), 2),
                "duration_minutes": round(duration_minutes, 2),
            },
        )

    def content_published(
        self,
        brief_id: str,
        content_type: str,
        platform: str,
        word_count: int = 0,
    ) -> None:
        """
        Track content publication to a platform.

        Args:
            brief_id: Brief identifier
            content_type: Type of content
            platform: Target platform (wordpress, linkedin, twitter, etc.)
            word_count: Word count of published content
        """
        self._capture(
            "content_published",
            {
                "brief_id": brief_id,
                "content_type": content_type,
                "platform": platform,
                "word_count": word_count,
            },
        )

    def content_tier_allocation(
        self,
        domain: str,
        tier: int,
        allocation_percentage: int,
        content_types: list[str],
    ) -> None:
        """
        Track content tier allocation for a domain.

        Args:
            domain: FORGE domain
            tier: Tier level (1, 2, or 3)
            allocation_percentage: Percentage allocation for tier
            content_types: Content types allocated
        """
        self._capture(
            "content_tier_allocation",
            {
                "domain": domain,
                "tier": tier,
                "allocation_percentage": allocation_percentage,
                "content_types": content_types,
            },
        )

    # ========== Decision Pipeline Tracking ==========

    def decision_made(
        self,
        decision_id: str,
        action: str,
        confidence: float,
        feature_id: str,
        reasoning: str,
    ) -> None:
        """
        Track a decision made by the decision engine.

        Args:
            decision_id: Unique ID for the decision
            action: Recommended action (PROCEED, HUMAN_REVIEW, BLOCK)
            confidence: Confidence score (0.0 to 1.0)
            feature_id: Feature being decided on
            reasoning: Human-readable reasoning for the decision
        """
        self._capture(
            "harness_decision_made",
            {
                "decision_id": decision_id,
                "action": action,
                "confidence": confidence,
                "feature_id": feature_id,
                "reasoning": reasoning,
            },
        )

    def feedback_recorded(
        self,
        decision_id: str,
        outcome: str,
        feature_id: str,
        error_message: str | None = None,
    ) -> None:
        """
        Track feedback recorded for a decision outcome.

        Args:
            decision_id: ID of the original decision
            outcome: Outcome result (success, failure)
            feature_id: Feature the decision was for
            error_message: Error message if outcome was failure
        """
        self._capture(
            "harness_feedback_recorded",
            {
                "decision_id": decision_id,
                "outcome": outcome,
                "feature_id": feature_id,
                "error_message": error_message,
            },
        )

    def bridge_called(
        self,
        bridge_name: str,
        operation: str,
        latency_ms: float,
        success: bool,
        error: str | None = None,
    ) -> None:
        """
        Track a call to an external bridge (Code Atlas, Tech Diligence).

        Args:
            bridge_name: Name of bridge (code_atlas, tech_diligence)
            operation: Operation performed (get_signals, query_rag, etc.)
            latency_ms: Call latency in milliseconds
            success: Whether the call succeeded
            error: Error message if call failed
        """
        self._capture(
            "harness_bridge_called",
            {
                "bridge_name": bridge_name,
                "operation": operation,
                "latency_ms": latency_ms,
                "success": success,
                "error": error,
            },
        )

    def pattern_applied(
        self,
        pattern_id: str,
        feature_id: str,
        confidence: float,
        source: str,
    ) -> None:
        """
        Track a pattern being applied to a feature.

        Args:
            pattern_id: ID of the pattern applied
            feature_id: Feature the pattern was applied to
            confidence: Confidence score for the pattern match
            source: Source of the pattern (code_atlas, learning_store)
        """
        self._capture(
            "harness_pattern_applied",
            {
                "pattern_id": pattern_id,
                "feature_id": feature_id,
                "confidence": confidence,
                "source": source,
            },
        )

    def session_ended(
        self,
        metrics: SessionMetrics,
        end_reason: str = "completed",
    ) -> None:
        """
        Track session end with final metrics.

        Args:
            metrics: Session metrics collected during the run
            end_reason: Why session ended (completed, max_iterations, error, interrupted)
        """
        duration_minutes = (datetime.now() - self.start_time).total_seconds() / 60

        self._capture(
            "harness_session_ended",
            {
                "duration_minutes": round(duration_minutes, 1),
                "end_reason": end_reason,
                "issues_attempted": metrics.issues_attempted,
                "issues_completed": metrics.issues_completed,
                "issues_failed": metrics.issues_failed,
                "tests_passed": metrics.tests_passed,
                "tests_failed": metrics.tests_failed,
                "coverage_backend": metrics.coverage_backend,
                "coverage_frontend": metrics.coverage_frontend,
                "commits_made": metrics.commits_made,
                "files_changed": metrics.files_changed,
                "lines_added": metrics.lines_added,
                "lines_removed": metrics.lines_removed,
                "errors_count": len(metrics.errors_encountered),
            },
        )

        # Flush any remaining events
        if self.enabled and self._client is not None:
            self._run_async(self._client.flush())


class NoOpTracker(HarnessTracker):
    """A no-op tracker for when PostHog is disabled or unavailable."""

    def __init__(self, *args, **kwargs):
        """Initialize with tracking disabled."""
        self.domain = kwargs.get("domain", "")
        self.project = kwargs.get("project", "")
        self.session_id = kwargs.get("session_id", "")
        self.start_time = datetime.now()
        self.enabled = False

    def _capture(self, event: str, properties: dict[str, Any] | None = None) -> None:
        """No-op capture."""
        pass

    def track_event(self, event: str, properties: dict[str, Any] | None = None) -> None:
        """No-op track_event."""
        pass

    def iteration_completed(self, *args, **kwargs) -> None:
        """No-op iteration_completed."""
        pass

    def error_pattern_detected(self, *args, **kwargs) -> None:
        """No-op error_pattern_detected."""
        pass

    def preflight_completed(self, *args, **kwargs) -> None:
        """No-op preflight_completed."""
        pass

    def content_generation_started(self, *args, **kwargs) -> None:
        """No-op content_generation_started."""
        pass

    def content_generation_completed(self, *args, **kwargs) -> None:
        """No-op content_generation_completed."""
        pass

    def content_revision_requested(self, *args, **kwargs) -> None:
        """No-op content_revision_requested."""
        pass

    def content_approved(self, *args, **kwargs) -> None:
        """No-op content_approved."""
        pass

    def content_batch_completed(self, *args, **kwargs) -> None:
        """No-op content_batch_completed."""
        pass

    def content_published(self, *args, **kwargs) -> None:
        """No-op content_published."""
        pass

    def content_tier_allocation(self, *args, **kwargs) -> None:
        """No-op content_tier_allocation."""
        pass

    # Decision pipeline no-ops
    def decision_made(self, *args, **kwargs) -> None:
        """No-op decision_made."""
        pass

    def feedback_recorded(self, *args, **kwargs) -> None:
        """No-op feedback_recorded."""
        pass

    def bridge_called(self, *args, **kwargs) -> None:
        """No-op bridge_called."""
        pass

    def pattern_applied(self, *args, **kwargs) -> None:
        """No-op pattern_applied."""
        pass


def create_tracker(
    domain: str,
    project: str,
    session_id: str,
    enabled: bool = True,
) -> HarnessTracker:
    """
    Factory function to create appropriate tracker.

    Returns HarnessTracker if PostHog is available and enabled,
    otherwise returns NoOpTracker.

    Args:
        domain: FORGE domain name
        project: Project name
        session_id: Unique session identifier
        enabled: Whether tracking should be enabled

    Returns:
        HarnessTracker or NoOpTracker instance
    """
    if not enabled or not POSTHOG_AVAILABLE:
        return NoOpTracker(domain=domain, project=project, session_id=session_id)

    api_key = os.environ.get("POSTHOG_API_KEY")
    if not api_key:
        return NoOpTracker(domain=domain, project=project, session_id=session_id)

    return HarnessTracker(
        domain=domain,
        project=project,
        session_id=session_id,
        api_key=api_key,
        enabled=True,
    )
