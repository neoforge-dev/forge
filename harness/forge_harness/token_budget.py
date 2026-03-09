"""
Token Budget Tracker for FORGE Harness
=======================================

Proactive token budget monitoring to prevent rate limit hits during marathon sessions.

Context:
    During a 60-output documentation marathon, Kimi hit rate limits after 5 outputs
    due to lack of token budget tracking. This module provides real-time monitoring
    and handoff recommendations.

Usage:
    from forge_harness.token_budget import TokenBudgetTracker, create_tracker_from_env

    # Create tracker with default limits
    tracker = TokenBudgetTracker()

    # Record output
    status = tracker.record_output(agent_id="kimi", tokens_used=45000)
    if status["should_handoff"]:
        print(f"Warning: {status['recommendation']}")

    # Estimate remaining capacity
    remaining = tracker.estimate_remaining_outputs(agent_id="kimi", avg_tokens=40000)
    print(f"Estimated {remaining} outputs remaining")

    # Check if handoff needed
    if tracker.should_handoff(agent_id="kimi"):
        handoff_to_next_agent()

Reference:
    Based on Kimi's token model from .forge/WORKFLOW_EVALUATION_KIMI.md
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class AgentBudget:
    """Token budget configuration for a single agent."""

    agent_id: str
    """Unique identifier for the agent (kimi, codex, gemini, etc.)"""

    hourly_limit: int
    """Maximum output tokens per hour"""

    tokens_used: int = 0
    """Cumulative tokens used in current session"""

    output_count: int = 0
    """Number of outputs generated in current session"""

    session_start: datetime = field(default_factory=datetime.now)
    """When the current budget session started"""

    last_output: datetime | None = None
    """Timestamp of last output"""

    def reset(self) -> None:
        """Reset token usage for new session."""
        self.tokens_used = 0
        self.output_count = 0
        self.session_start = datetime.now()
        self.last_output = None
        logger.info(f"Reset token budget for {self.agent_id}")

    def should_auto_reset(self) -> bool:
        """Check if budget should auto-reset (hourly window passed)."""
        return datetime.now() - self.session_start > timedelta(hours=1)


class TokenBudgetTracker:
    """
    Tracks token usage per agent and provides handoff recommendations.

    Token Budget Model (based on Kimi evaluation):
        - Kimi: 250K tokens/hour (conservative estimate)
        - Codex: 500K tokens/hour
        - Gemini: 1M tokens/hour

    Thresholds:
        - WARNING (75%): Prepare handoff, notify orchestrator
        - CRITICAL (90%): Immediate handoff required
    """

    # Threshold constants
    WARNING_THRESHOLD = 0.75  # 75% - prepare handoff
    CRITICAL_THRESHOLD = 0.90  # 90% - immediate handoff

    # Default hourly limits (tokens)
    DEFAULT_LIMITS = {
        "kimi": 250000,  # Conservative based on observed marathon
        "codex": 500000,  # Higher capacity
        "gemini": 1000000,  # Highest capacity
        "claude": 500000,  # Default for Claude variants
        "gpt4": 500000,  # OpenAI GPT-4
    }

    def __init__(
        self,
        custom_limits: dict[str, int] | None = None,
        auto_reset: bool = True,
    ):
        """
        Initialize token budget tracker.

        Args:
            custom_limits: Override default limits for specific agents
            auto_reset: Automatically reset budgets after 1 hour
        """
        self.budgets: dict[str, AgentBudget] = {}
        self.custom_limits = custom_limits or {}
        self.auto_reset = auto_reset

        logger.info(
            f"TokenBudgetTracker initialized with thresholds: "
            f"WARNING={self.WARNING_THRESHOLD:.0%}, "
            f"CRITICAL={self.CRITICAL_THRESHOLD:.0%}"
        )

    def _get_or_create_budget(self, agent_id: str) -> AgentBudget:
        """Get existing budget or create new one with default/custom limit."""
        if agent_id not in self.budgets:
            # Determine hourly limit
            if agent_id in self.custom_limits:
                hourly_limit = self.custom_limits[agent_id]
            elif agent_id in self.DEFAULT_LIMITS:
                hourly_limit = self.DEFAULT_LIMITS[agent_id]
            else:
                # Default to conservative limit for unknown agents
                hourly_limit = 250000
                logger.warning(
                    f"Unknown agent '{agent_id}', using conservative limit: {hourly_limit:,}"
                )

            self.budgets[agent_id] = AgentBudget(agent_id=agent_id, hourly_limit=hourly_limit)
            logger.info(f"Created budget for {agent_id}: {hourly_limit:,} tokens/hour")

        budget = self.budgets[agent_id]

        # Auto-reset if enabled and hourly window passed
        if self.auto_reset and budget.should_auto_reset():
            budget.reset()

        return budget

    def record_output(self, agent_id: str, tokens_used: int) -> dict[str, any]:
        """
        Record token usage from an agent output.

        Args:
            agent_id: Agent identifier
            tokens_used: Number of tokens consumed in this output

        Returns:
            Status dict with:
                - status: "OK" | "WARNING" | "CRITICAL"
                - utilization: Percentage of budget used (0.0-1.0)
                - utilization_pct: Formatted percentage string
                - tokens_used: Cumulative tokens used
                - tokens_remaining: Tokens remaining in budget
                - output_count: Number of outputs in session
                - should_handoff: Boolean recommendation
                - recommendation: Human-readable action
                - session_duration: Minutes since session start
        """
        budget = self._get_or_create_budget(agent_id)

        # Update budget
        budget.tokens_used += tokens_used
        budget.output_count += 1
        budget.last_output = datetime.now()

        # Calculate utilization
        utilization = budget.tokens_used / budget.hourly_limit
        tokens_remaining = budget.hourly_limit - budget.tokens_used
        session_duration = (datetime.now() - budget.session_start).total_seconds() / 60

        # Determine status
        if utilization >= self.CRITICAL_THRESHOLD:
            status = "CRITICAL"
            should_handoff = True
            recommendation = (
                f"HANDOFF NOW - {agent_id} at {utilization:.0%} budget. Immediate handoff required."
            )
        elif utilization >= self.WARNING_THRESHOLD:
            status = "WARNING"
            should_handoff = True
            recommendation = (
                f"PREPARE HANDOFF - {agent_id} at {utilization:.0%} budget. "
                f"Plan handoff for next task."
            )
        else:
            status = "OK"
            should_handoff = False
            recommendation = f"{agent_id} operating normally ({utilization:.0%} budget)"

        result = {
            "status": status,
            "utilization": utilization,
            "utilization_pct": f"{utilization:.0%}",
            "tokens_used": budget.tokens_used,
            "tokens_remaining": tokens_remaining,
            "hourly_limit": budget.hourly_limit,
            "output_count": budget.output_count,
            "should_handoff": should_handoff,
            "recommendation": recommendation,
            "session_duration_minutes": round(session_duration, 1),
        }

        # Log based on status
        if status == "CRITICAL":
            logger.error(
                f"{agent_id} CRITICAL: {utilization:.0%} budget "
                f"({budget.tokens_used:,}/{budget.hourly_limit:,} tokens, "
                f"{budget.output_count} outputs)"
            )
        elif status == "WARNING":
            logger.warning(
                f"{agent_id} WARNING: {utilization:.0%} budget "
                f"({budget.tokens_used:,}/{budget.hourly_limit:,} tokens, "
                f"{budget.output_count} outputs)"
            )
        else:
            logger.info(
                f"{agent_id} output #{budget.output_count}: "
                f"+{tokens_used:,} tokens "
                f"({utilization:.0%} budget)"
            )

        return result

    def estimate_remaining_outputs(self, agent_id: str, avg_tokens: int) -> int:
        """
        Estimate how many more outputs agent can produce.

        Args:
            agent_id: Agent identifier
            avg_tokens: Average tokens per output (from recent history)

        Returns:
            Estimated number of remaining outputs (0 if budget exhausted)
        """
        budget = self._get_or_create_budget(agent_id)
        tokens_remaining = budget.hourly_limit - budget.tokens_used

        if tokens_remaining <= 0:
            return 0

        # Conservative estimate (assume slightly higher than average)
        safety_margin = 1.1
        estimated = int(tokens_remaining / (avg_tokens * safety_margin))

        logger.debug(
            f"{agent_id} estimated outputs: {estimated} "
            f"(remaining: {tokens_remaining:,}, avg: {avg_tokens:,})"
        )

        return max(0, estimated)

    def should_handoff(self, agent_id: str) -> bool:
        """
        Check if agent should hand off to another agent.

        Args:
            agent_id: Agent identifier

        Returns:
            True if utilization >= WARNING_THRESHOLD
        """
        budget = self._get_or_create_budget(agent_id)
        utilization = budget.tokens_used / budget.hourly_limit
        return utilization >= self.WARNING_THRESHOLD

    def get_status(self, agent_id: str) -> dict[str, any]:
        """
        Get current budget status for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            Status dict with utilization, remaining tokens, and recommendation
        """
        budget = self._get_or_create_budget(agent_id)
        utilization = budget.tokens_used / budget.hourly_limit
        tokens_remaining = budget.hourly_limit - budget.tokens_used
        session_duration = (datetime.now() - budget.session_start).total_seconds() / 60

        # Determine recommendation
        if utilization >= self.CRITICAL_THRESHOLD:
            recommendation = "HANDOFF NOW - Budget critical"
        elif utilization >= self.WARNING_THRESHOLD:
            recommendation = "PREPARE HANDOFF - Budget warning"
        else:
            recommendation = "Continue - Budget healthy"

        return {
            "agent_id": agent_id,
            "status": (
                "CRITICAL"
                if utilization >= self.CRITICAL_THRESHOLD
                else "WARNING"
                if utilization >= self.WARNING_THRESHOLD
                else "OK"
            ),
            "utilization": utilization,
            "utilization_pct": f"{utilization:.0%}",
            "tokens_used": budget.tokens_used,
            "tokens_remaining": tokens_remaining,
            "hourly_limit": budget.hourly_limit,
            "output_count": budget.output_count,
            "recommendation": recommendation,
            "session_start": budget.session_start.isoformat(),
            "session_duration_minutes": round(session_duration, 1),
            "last_output": budget.last_output.isoformat() if budget.last_output else None,
        }

    def get_all_statuses(self) -> dict[str, dict[str, any]]:
        """Get status for all tracked agents."""
        return {agent_id: self.get_status(agent_id) for agent_id in self.budgets}

    def reset_agent(self, agent_id: str) -> None:
        """Manually reset budget for a specific agent."""
        if agent_id in self.budgets:
            self.budgets[agent_id].reset()
        else:
            logger.warning(f"Cannot reset unknown agent: {agent_id}")

    def reset_all(self) -> None:
        """Reset budgets for all agents."""
        for budget in self.budgets.values():
            budget.reset()
        logger.info("Reset all agent budgets")


def create_tracker_from_env() -> TokenBudgetTracker:
    """
    Create TokenBudgetTracker with limits from environment variables.

    Looks for environment variables:
        - TOKEN_LIMIT_KIMI
        - TOKEN_LIMIT_CODEX
        - TOKEN_LIMIT_GEMINI
        - etc.

    Returns:
        Configured TokenBudgetTracker instance
    """
    import os

    custom_limits = {}

    # Check for custom limits in environment
    for agent_id in ["kimi", "codex", "gemini", "claude", "gpt4"]:
        env_var = f"TOKEN_LIMIT_{agent_id.upper()}"
        if env_var in os.environ:
            try:
                custom_limits[agent_id] = int(os.environ[env_var])
                logger.info(
                    f"Using custom limit for {agent_id}: {custom_limits[agent_id]:,} tokens/hour"
                )
            except ValueError:
                logger.error(f"Invalid token limit in {env_var}: {os.environ[env_var]}")

    return TokenBudgetTracker(custom_limits=custom_limits)


# Example usage and testing
if __name__ == "__main__":
    # Example: Marathon documentation session (like Kimi's 60-output marathon)
    print("=== Token Budget Tracker Demo ===\n")

    tracker = TokenBudgetTracker()

    # Simulate Kimi's pattern guide marathon
    print("Simulating Kimi documentation marathon:\n")

    guide_sizes = [
        ("DATABASE_PATTERNS.md", 44000),
        ("TESTING_PATTERNS.md", 35000),
        ("LOGGING_PATTERNS.md", 42000),
        ("API_VERSIONING.md", 38000),
        ("FILE_UPLOAD.md", 45000),
        ("CORS_PATTERNS.md", 40000),  # Should trigger warning around here
    ]

    for guide_name, tokens in guide_sizes:
        status = tracker.record_output(agent_id="kimi", tokens_used=tokens)

        print(f"Output: {guide_name}")
        print(f"  Status: {status['status']}")
        print(f"  Budget: {status['utilization_pct']}")
        print(f"  Tokens: {status['tokens_used']:,} / {status['hourly_limit']:,}")
        print(f"  Remaining: {status['tokens_remaining']:,}")
        print(f"  Recommendation: {status['recommendation']}")

        # Estimate remaining capacity
        remaining_outputs = tracker.estimate_remaining_outputs(agent_id="kimi", avg_tokens=40000)
        print(f"  Estimated remaining outputs: {remaining_outputs}\n")

        if status["should_handoff"]:
            print("  ⚠️  HANDOFF RECOMMENDED ⚠️\n")

        if status["status"] == "CRITICAL":
            print("  🚨 CRITICAL - Stop and handoff immediately! 🚨\n")
            break

    # Show final status
    print("\n=== Final Status ===")
    final_status = tracker.get_status("kimi")
    print(f"Agent: {final_status['agent_id']}")
    print(f"Status: {final_status['status']}")
    print(f"Utilization: {final_status['utilization_pct']}")
    print(f"Output Count: {final_status['output_count']}")
    print(f"Session Duration: {final_status['session_duration_minutes']} minutes")
    print(f"Recommendation: {final_status['recommendation']}")
