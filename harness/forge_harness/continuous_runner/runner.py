"""
Continuous Ralph Loop Runner for Railway Deployment.
"""

import asyncio
import os
import signal
from datetime import timedelta
from pathlib import Path

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalType,
    create_approval_queue_from_env,
)
from forge_harness.continuous_runner.config import settings
from forge_harness.continuous_runner.health import monitor
from forge_harness.logging_config import get_logger
from forge_harness.meta_learning.decision_engine import DecisionEngine
from forge_harness.meta_learning.schemas import (
    DecisionAction,
    DecisionContext,
    DiligenceSignals,
    ScoreCard,
)
from forge_harness.notification_harness import (
    create_notification_harness,
)
from forge_harness.ralph_loop import (
    FeatureStore,
    LoopConfig,
    RalphLoopHarness,
)

logger = get_logger(__name__)


class ContinuousRalphRunner:
    """
    Continuous runner that keeps Ralph Loop working with human approval gates.
    """

    def __init__(
        self,
        domain: str = settings.domain,
        project: str = settings.project,
        forge_root: Path = settings.forge_root,
        features_path: Path | None = settings.features_path,
        approval_timeout_hours: float = settings.approval_timeout_hours,
        loop_cooldown_seconds: float = settings.loop_cooldown_seconds,
        max_iterations_per_run: int = settings.max_iterations_per_run,
    ):
        self.domain = domain
        self.project = project
        self.forge_root = forge_root

        # Derive paths if not provided
        if features_path:
            self.features_path = features_path
        elif domain and project:
            project_path = self.forge_root / domain / project
            self.features_path = project_path / "features.json"
        else:
            self.features_path = None

        self.approval_timeout = timedelta(hours=approval_timeout_hours)
        self.loop_cooldown = loop_cooldown_seconds
        self.max_iterations_per_run = max_iterations_per_run

        # State
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._current_feature: str | None = None

        # Components (lazy initialized)
        self._approval_queue = None
        self._notification_harness = None
        self._decision_engine = None

        # Update initial status
        monitor.update_component("ralph_loop", "starting")

    async def _get_approval_queue(self):
        """Get or create approval queue."""
        if self._approval_queue is None:
            self._approval_queue = create_approval_queue_from_env()
        return self._approval_queue

    async def _get_notification_harness(self):
        """Get or create notification harness."""
        if self._notification_harness is None:
            self._notification_harness = create_notification_harness()
        return self._notification_harness

    def _get_decision_engine(self):
        """Get or create decision engine."""
        if self._decision_engine is None:
            self._decision_engine = DecisionEngine()
        return self._decision_engine

    def _classify_tier(
        self,
        feature_id: str,
        feature_name: str,
        action_type: str,
        risk_score: float = 0.0,
    ) -> str:
        """Classify the decision tier."""
        engine = self._get_decision_engine()

        context = DecisionContext(
            domain=self.domain,
            project=self.project,
            feature_id=feature_id,
            tags=[action_type],
        )

        scores = ScoreCard(
            risk_score=risk_score,
            confidence_score=0.5,
            complexity_score=0.3,
            precedent_score=0.5,
        )

        diligence = DiligenceSignals(
            risk_level="low" if risk_score < 0.3 else "medium" if risk_score < 0.6 else "high"
        )

        action_map = {
            "feature_complete": DecisionAction.HUMAN_REVIEW_REQUIRED,
            "deploy": DecisionAction.HUMAN_REVIEW_REQUIRED,
            "test_retry": DecisionAction.PROCEED_WITH_CAUTION,
            "blocked": DecisionAction.BLOCK,
        }
        action = action_map.get(action_type, DecisionAction.HUMAN_REVIEW_REQUIRED)

        tier = engine.classify_tier(action, scores, context, diligence)
        return tier.value

    async def _request_human_approval(
        self,
        title: str,
        description: str,
        approval_type: ApprovalType,
        feature_id: str | None = None,
        risk_score: float = 0.0,
        context: dict | None = None,
    ) -> bool:
        """Request human approval via Slack with tier-aware notification."""
        queue = await self._get_approval_queue()
        notifier = await self._get_notification_harness()

        request = await queue.create_request(
            type=approval_type,
            title=title,
            description=description,
            domain=self.domain,
            project=self.project,
            priority=ApprovalPriority.HIGH,
            metadata=context or {},
        )

        tier = self._classify_tier(
            feature_id=feature_id or "unknown",
            feature_name=title,
            action_type=approval_type.value,
            risk_score=risk_score,
        )

        dashboard_url = os.environ.get("FORGE_DASHBOARD_URL")
        if dashboard_url:
            dashboard_url = f"{dashboard_url}/approvals/{request.id}"

        await notifier.send_approval_notification(
            request_id=request.id,
            title=title,
            description=description,
            tier=tier,
            domain=self.domain,
            project=self.project,
            feature_id=feature_id,
            approval_type=approval_type.value,
            risk_score=risk_score,
            context=context,
            dashboard_url=dashboard_url,
        )

        logger.info(
            "Approval requested: %s (tier=%s, request_id=%s)",
            title,
            tier,
            request.id,
        )

        deadline = asyncio.get_event_loop().time() + self.approval_timeout.total_seconds()
        poll_interval = 30.0

        while asyncio.get_event_loop().time() < deadline:
            if self._shutdown_event.is_set():
                logger.info("Shutdown requested, cancelling approval wait")
                return False

            current = await queue.get_request(request.id)
            if current:
                if current.status.value == "approved":
                    logger.info("Approval granted: %s", request.id)
                    return True
                elif current.status.value == "rejected":
                    logger.info("Approval rejected: %s", request.id)
                    return False

            await asyncio.sleep(poll_interval)

        logger.warning("Approval timeout: %s", request.id)
        return False

    async def _run_single_loop(self) -> bool:
        """Run a single iteration of the Ralph Loop."""
        if not self.features_path or not self.features_path.exists():
            logger.error("Features file not found: %s", self.features_path)
            return False

        store = FeatureStore(self.features_path)

        config = LoopConfig(
            max_iterations=self.max_iterations_per_run,
            max_failures_per_feature=3,
            checkpoint_interval=5,
            timeout_seconds=300,
            dry_run=False,
        )

        loop = RalphLoopHarness(feature_store=store, config=config)

        logger.info(
            "Starting Ralph Loop run: domain=%s, project=%s, features=%s",
            self.domain,
            self.project,
            self.features_path,
        )

        result = await loop.run()

        logger.info(
            "Ralph Loop run completed: success=%s, iterations=%d, completed=%d, blocked=%d",
            result.success,
            result.iterations,
            result.features_completed,
            result.features_blocked,
        )

        if result.features_completed > 0:
            approved = await self._request_human_approval(
                title=f"✅ {result.features_completed} features completed",
                description=f"Ralph Loop completed {result.features_completed} features. Review to continue.",
                approval_type=ApprovalType.FEATURE,
                risk_score=0.2,
                context={
                    "completed": result.features_completed,
                    "remaining": result.features_remaining,
                    "blocked": result.features_blocked,
                },
            )
            if not approved:
                logger.info("Batch approval not granted, pausing runner")
                return False

        if result.features_blocked > 0:
            await self._request_human_approval(
                title=f"🚫 {result.features_blocked} features blocked",
                description=f"Ralph Loop has {result.features_blocked} blocked features.",
                approval_type=ApprovalType.FEATURE,
                risk_score=0.5,
                context={
                    "blocked": result.features_blocked,
                    "remaining": result.features_remaining,
                },
            )

        return result.features_remaining > 0

    async def run(self):
        """Run the continuous loop until shutdown."""
        self._running = True
        logger.info(
            "Starting Continuous Ralph Runner: domain=%s, project=%s",
            self.domain,
            self.project,
        )
        monitor.update_component("ralph_loop", "healthy")

        # Set up signal handlers for graceful shutdown (needed for tests)
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._handle_shutdown)
        except (RuntimeError, ValueError):
            # Might fail in some environments (e.g. windows or inside certain tests)
            pass

        try:
            while self._running and not self._shutdown_event.is_set():
                try:
                    monitor.update_component("ralph_loop", "working")
                    should_continue = await self._run_single_loop()

                    if not should_continue:
                        logger.info("No more work to do, entering idle state")
                        monitor.update_component("ralph_loop", "idle")
                        await asyncio.wait_for(
                            self._shutdown_event.wait(),
                            timeout=self.loop_cooldown * 10,
                        )
                    else:
                        logger.info("Cooldown: %ds before next run", self.loop_cooldown)
                        monitor.update_component("ralph_loop", "cooldown")
                        await asyncio.sleep(self.loop_cooldown)

                except TimeoutError:
                    continue
                except Exception as e:
                    logger.exception("Error in Ralph Loop run: %s", e)
                    monitor.update_component("ralph_loop", "error")
                    await asyncio.sleep(self.loop_cooldown * 2)

        except asyncio.CancelledError:
            logger.info("Runner cancelled")
        finally:
            self._running = False
            monitor.update_component("ralph_loop", "stopped")
            logger.info("Continuous Ralph Runner stopped")

    def _handle_shutdown(self):
        """Handle shutdown signal."""
        logger.info("Shutdown requested")
        self._running = False
        self._shutdown_event.set()


def main():
    """Entry point for continuous runner."""
    # Create runner
    runner = ContinuousRalphRunner()

    # Run
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
