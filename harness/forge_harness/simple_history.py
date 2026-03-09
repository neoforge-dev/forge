"""
Simplified pattern history using append-only JSONL.

Replaces complex DecisionEngine/LearningStore with ~50 lines.
Per Kimi's recommendation: "Simple log provides same 'learning' with 1/20th the code."
"""

import json
from datetime import UTC, datetime
from pathlib import Path


class SimpleHistory:
    """
    Simplified pattern history using append-only JSONL.

    Records action outcomes and provides basic pattern matching for decision support.
    Thread-safe through append-only writes. No external dependencies.
    """

    def __init__(self, history_file: Path | None = None):
        """
        Initialize history tracker.

        Args:
            history_file: Path to JSONL history file. Defaults to .forge/state/history.jsonl
        """
        if history_file is None:
            self.history_file = Path.cwd() / ".forge" / "state" / "history.jsonl"
        else:
            self.history_file = Path(history_file)

        # Ensure directory exists
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

        # Create file if it doesn't exist
        if not self.history_file.exists():
            self.history_file.touch()

    def record(
        self, domain: str, project: str, action: str, success: bool, context: dict | None = None
    ) -> None:
        """
        Record an action outcome.

        Args:
            domain: Domain name (e.g., 'codeswiftr-com')
            project: Project name (e.g., 'interview-simulator')
            action: Action type (e.g., 'deploy', 'test', 'build')
            success: Whether action succeeded
            context: Optional context data (error message, duration, etc.)
        """
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "domain": domain,
            "project": project,
            "action": action,
            "success": success,
            "context": context or {},
        }

        # Append to JSONL file (atomic write)
        with open(self.history_file, "a") as f:
            f.write(json.dumps(record) + "\n")

    def get_success_rate(self, domain: str, action: str, limit: int = 10) -> float:
        """
        Calculate success rate for recent matching actions.

        Args:
            domain: Domain to filter by
            action: Action type to filter by
            limit: Number of recent records to consider

        Returns:
            Success rate as float 0.0-1.0, or 0.5 if no history
        """
        matching = self._get_matching_records(domain, action, limit)

        if not matching:
            return 0.5  # Neutral default when no history

        successes = sum(1 for r in matching if r["success"])
        return successes / len(matching)

    def get_recent_patterns(self, domain: str, action: str, limit: int = 5) -> list[dict]:
        """
        Get recent successful patterns for context.

        Args:
            domain: Domain to filter by
            action: Action type to filter by
            limit: Number of successful records to return

        Returns:
            List of successful record dicts with timestamp, context, etc.
        """
        matching = self._get_matching_records(domain, action, limit * 2)
        successful = [r for r in matching if r["success"]]
        return successful[:limit]

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get the most recent records across all domains/actions."""
        if not self.history_file.exists():
            return []

        try:
            lines = self.history_file.read_text().splitlines()
        except OSError:
            return []

        recent: list[dict] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            recent.append(record)
            if len(recent) >= limit:
                break
        return recent

    def should_proceed(
        self, domain: str, action: str, threshold: float = 0.6
    ) -> tuple[bool, float]:
        """
        Simple decision helper based on historical success rate.

        Args:
            domain: Domain to check
            action: Action type to check
            threshold: Minimum success rate to proceed (default 0.6)

        Returns:
            Tuple of (should_proceed, success_rate)
        """
        success_rate = self.get_success_rate(domain, action)
        return (success_rate >= threshold, success_rate)

    def get_cross_domain_success_rate(
        self,
        action: str,
        exclude_domain: str | None = None,
        min_samples: int = 5,
        limit: int = 50,
    ) -> tuple[float, int]:
        """Get aggregated success rate for an action across all domains.

        Args:
            action: Action type to search for (e.g., "feature:authentication")
            exclude_domain: Domain to exclude from aggregation (typically current domain)
            min_samples: Minimum samples required before returning non-default rate
            limit: Max records to consider

        Returns:
            Tuple of (success_rate, total_count). Returns (0.5, 0) if insufficient data.
        """
        if not self.history_file.exists():
            return (0.5, 0)

        matching = []

        # Read file in reverse for most recent records
        try:
            with open(self.history_file) as f:
                lines = f.readlines()
        except OSError:
            return (0.5, 0)

        for line in reversed(lines):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
                # Match action and optionally exclude domain
                if record["action"] == action:
                    if exclude_domain is None or record["domain"] != exclude_domain:
                        matching.append(record)
                        if len(matching) >= limit:
                            break
            except (json.JSONDecodeError, KeyError):
                continue  # Skip malformed lines

        # Return default if insufficient data
        if len(matching) < min_samples:
            return (0.5, 0)

        # Calculate success rate
        successes = sum(1 for r in matching if r["success"])
        success_rate = successes / len(matching)
        return (success_rate, len(matching))

    def _get_matching_records(self, domain: str, action: str, limit: int) -> list[dict]:
        """
        Get matching records from history file.

        Reads file in reverse to get most recent records first.
        """
        if not self.history_file.exists():
            return []

        matching = []

        # Read file in reverse for most recent records
        with open(self.history_file) as f:
            lines = f.readlines()

        for line in reversed(lines):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
                if record["domain"] == domain and record["action"] == action:
                    matching.append(record)
                    if len(matching) >= limit:
                        break
            except (json.JSONDecodeError, KeyError):
                continue  # Skip malformed lines

        return matching
