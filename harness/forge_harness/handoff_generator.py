"""
Handoff Prompt Generator for FORGE Fleet Restart System
=========================================================

Automatically generates handoff prompts for agents with high context usage (>50%)
to preserve context across system restarts.

Usage:
    from forge_harness.handoff_generator import HandoffGenerator

    generator = HandoffGenerator(forge_root="/Users/bogdan/work/FORGE")

    # Generate handoffs for all agents >50% context
    results = generator.generate_all_handoffs()

    # Generate handoff for specific agent
    handoff = generator.generate_handoff(agent_data)
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


class HandoffGenerator:
    """Generate handoff prompts for agent context preservation."""

    def __init__(
        self,
        forge_root: str | Path | None = None,
        context_threshold: int = 50,
    ):
        """Initialize handoff generator.

        Args:
            forge_root: Path to FORGE repository root
            context_threshold: Context percentage threshold for handoff generation (default: 50)
        """
        if forge_root is None:
            forge_root = Path.cwd()
            # Try to find FORGE root
            for _ in range(10):
                if (forge_root / ".forge/fleet").exists():
                    break
                parent = forge_root.parent
                if parent == forge_root:
                    forge_root = Path.cwd()
                    break
                forge_root = parent

        self.forge_root = Path(forge_root)
        self.context_threshold = context_threshold
        self.fleet_dir = self.forge_root / ".forge/fleet"
        self.handoffs_dir = self.forge_root / ".forge/handoffs"
        self.state_file = self.fleet_dir / "state.json"

        # Ensure directories exist
        self.handoffs_dir.mkdir(exist_ok=True)
        self.fleet_dir.mkdir(exist_ok=True)

    def load_fleet_state(self) -> dict[str, Any]:
        """Load current fleet state from state.json.

        Returns:
            Fleet state dictionary

        Raises:
            FileNotFoundError: If state.json doesn't exist
            json.JSONDecodeError: If state.json is invalid
        """
        if not self.state_file.exists():
            raise FileNotFoundError(
                f"Fleet state file not found: {self.state_file}\n"
                "Run 'forge-harness fleet save' first."
            )

        with open(self.state_file) as f:
            return json.load(f)

    def needs_handoff(self, agent_data: dict[str, Any]) -> bool:
        """Check if agent needs handoff based on context percentage.

        Args:
            agent_data: Agent state dictionary

        Returns:
            True if agent needs handoff, False otherwise
        """
        # Check explicit needs_handoff flag
        if agent_data.get("needs_handoff", False):
            return True

        # Check context percentage
        context_str = agent_data.get("context_percent", "0")
        if context_str == "unknown":
            return False

        try:
            context_percent = int(context_str)
            return context_percent >= self.context_threshold
        except (ValueError, TypeError):
            return False

    def generate_handoff_prompt(self, agent_id: str, agent_data: dict[str, Any]) -> str:
        """Generate handoff prompt markdown content for an agent.

        Args:
            agent_id: Agent identifier (e.g., "forge:tech")
            agent_data: Agent state dictionary

        Returns:
            Markdown content for handoff prompt
        """
        session = agent_data.get("session", "unknown")
        window = agent_data.get("window", "unknown")
        agent_type = agent_data.get("agent_type", "unknown")
        context_percent = agent_data.get("context_percent", "unknown")
        current_task = agent_data.get("current_task", "No task description available")
        working_dir = agent_data.get("working_directory", "unknown")
        saved_at = agent_data.get("saved_at", "unknown")

        # Generate handoff content
        content = f"""# Handoff: {agent_id}

**Generated:** {datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")}
**Context Used:** {context_percent}%
**Agent Type:** {agent_type}
**Session/Window:** {session}:{window}
**Working Directory:** {working_dir}
**Saved At:** {saved_at}

## Current Task

{current_task.strip()}

## Context Recovery Instructions

This agent was saved with {context_percent}% context usage. To continue work:

1. **Review the Current Task** above to understand what was being worked on
2. **Check Working Directory** to ensure you're in the correct location
3. **Review Recent Changes** to understand progress made
4. **Continue Work** where the agent left off

## Resume Command

```bash
# Navigate to working directory
cd {working_dir}

# If using tmux, attach to the session
tmux attach -t {session}:{window}

# If starting fresh, use the current task description above
```

## Technical Details

- **Agent Type:** {agent_type}
- **Session:** {session}
- **Window:** {window}
- **Context:** {context_percent}% used
- **Threshold:** {self.context_threshold}% (handoff generated)

## Next Steps

Based on the current task description:

1. Read any relevant documentation mentioned in the task
2. Check for any TODO comments or pending work
3. Run tests to verify current state
4. Continue implementation from where the agent stopped

## Troubleshooting

If context is insufficient:

- Check git history for recent changes
- Review related documentation
- Look for checkpoint files in `.forge_checkpoints/`
- Check Command Center dashboard for task history

---

*Auto-generated by FORGE Handoff Generator*
*Timestamp: {datetime.now(UTC).isoformat()}*
"""
        return content

    def generate_handoff(self, agent_id: str, agent_data: dict[str, Any]) -> Path | None:
        """Generate handoff prompt file for a single agent.

        Args:
            agent_id: Agent identifier (e.g., "forge:tech")
            agent_data: Agent state dictionary

        Returns:
            Path to generated handoff file, or None if skipped
        """
        if not self.needs_handoff(agent_data):
            logger.debug(f"Agent {agent_id} does not need handoff (context below threshold)")
            return None

        # Generate filename
        session = agent_data.get("session", "unknown")
        window = agent_data.get("window", "unknown")
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        filename = f"{session}_{window}_{timestamp}.md"
        handoff_path = self.handoffs_dir / filename

        # Generate content
        content = self.generate_handoff_prompt(agent_id, agent_data)

        # Write to file
        with open(handoff_path, "w") as f:
            f.write(content)

        logger.info(f"Generated handoff for {agent_id}: {handoff_path}")
        return handoff_path

    def generate_all_handoffs(self) -> dict[str, Path | None]:
        """Generate handoff prompts for all agents that need them.

        Returns:
            Dictionary mapping agent IDs to handoff file paths (or None if skipped)
        """
        state = self.load_fleet_state()
        agents = state.get("agents", {})

        results = {}
        for agent_id, agent_data in agents.items():
            try:
                handoff_path = self.generate_handoff(agent_id, agent_data)
                results[agent_id] = handoff_path
            except Exception as e:
                logger.error(f"Failed to generate handoff for {agent_id}: {e}")
                results[agent_id] = None

        return results

    def list_handoffs(self) -> list[Path]:
        """List all existing handoff files.

        Returns:
            List of handoff file paths, sorted by modification time (newest first)
        """
        handoff_files = list(self.handoffs_dir.glob("*.md"))
        return sorted(handoff_files, key=lambda p: p.stat().st_mtime, reverse=True)

    def get_handoff_for_agent(
        self,
        session: str,
        window: str,
    ) -> Path | None:
        """Get most recent handoff for specific agent.

        Args:
            session: tmux session name
            window: tmux window name

        Returns:
            Path to most recent handoff file, or None if not found
        """
        pattern = f"{session}_{window}_*.md"
        handoffs = sorted(
            self.handoffs_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return handoffs[0] if handoffs else None

    def archive_old_handoffs(self, days: int = 7) -> list[Path]:
        """Archive handoff files older than specified days.

        Args:
            days: Number of days to keep handoffs (default: 7)

        Returns:
            List of archived file paths
        """
        archive_dir = self.handoffs_dir / "archive"
        archive_dir.mkdir(exist_ok=True)

        cutoff = datetime.now(UTC).timestamp() - (days * 86400)
        archived = []

        for handoff_file in self.handoffs_dir.glob("*.md"):
            if handoff_file.stat().st_mtime < cutoff:
                # Move to archive
                archive_path = archive_dir / handoff_file.name
                handoff_file.rename(archive_path)
                archived.append(archive_path)
                logger.info(f"Archived old handoff: {handoff_file.name}")

        return archived

    def update_fleet_state_with_handoffs(
        self,
        handoff_results: dict[str, Path | None],
    ) -> None:
        """Update fleet state.json with handoff file paths.

        Args:
            handoff_results: Dictionary mapping agent IDs to handoff paths
        """
        state = self.load_fleet_state()
        agents = state.get("agents", {})

        for agent_id, handoff_path in handoff_results.items():
            if agent_id in agents and handoff_path is not None:
                if "handoff" not in agents[agent_id]:
                    agents[agent_id]["handoff"] = {}

                # Store relative path from FORGE root
                rel_path = handoff_path.relative_to(self.forge_root)
                agents[agent_id]["handoff"]["prompt_path"] = str(rel_path)
                agents[agent_id]["handoff"]["generated_at"] = datetime.now(UTC).isoformat()

        # Update state file
        state["last_updated"] = datetime.now(UTC).isoformat()
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

        logger.info(f"Updated fleet state with {len(handoff_results)} handoff paths")


def create_handoff_generator(
    forge_root: str | Path | None = None,
    context_threshold: int = 50,
) -> HandoffGenerator:
    """Factory function to create HandoffGenerator instance.

    Args:
        forge_root: Path to FORGE repository root
        context_threshold: Context percentage threshold for handoff generation

    Returns:
        Configured HandoffGenerator instance
    """
    return HandoffGenerator(
        forge_root=forge_root,
        context_threshold=context_threshold,
    )
