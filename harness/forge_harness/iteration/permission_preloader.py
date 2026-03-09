"""Permission pre-loader for agent sessions.

Pre-configures Claude Code and other agents with needed permissions before task execution.
Manages --dangerously-skip-permissions mode and per-command approvals.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class PermissionLevel(Enum):
    """Permission levels for agent operations."""

    NONE = "none"
    READ_ONLY = "read_only"
    WRITE_LOCAL = "write_local"
    WRITE_ALL = "write_all"
    DANGEROUS = "dangerous"


@dataclass
class PermissionRule:
    """A rule granting specific permissions."""

    pattern: str  # glob pattern for paths
    level: PermissionLevel
    commands: list[str] = field(default_factory=list)  # allowed commands
    reason: str = ""


@dataclass
class PermissionSet:
    """Set of permissions for an agent session."""

    rules: list[PermissionRule] = field(default_factory=list)
    global_level: PermissionLevel = PermissionLevel.READ_ONLY
    allowed_tools: set[str] = field(default_factory=set)
    blocked_paths: list[str] = field(default_factory=list)


@dataclass
class PreloadResult:
    """Result of permission pre-loading."""

    success: bool
    permissions: PermissionSet
    warnings: list[str] = field(default_factory=list)
    applied_at: datetime = field(default_factory=datetime.now)


class PermissionPreloader:
    """Pre-loads permissions for agent sessions."""

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or Path(".forge/permissions.json")
        self._default_rules = self._create_default_rules()

    def _create_default_rules(self) -> list[PermissionRule]:
        """Create sensible default permission rules."""
        return [
            PermissionRule(
                pattern="*.py",
                level=PermissionLevel.WRITE_LOCAL,
                commands=["pytest", "mypy", "ruff"],
                reason="Python development",
            ),
            PermissionRule(
                pattern="*.md",
                level=PermissionLevel.WRITE_ALL,
                commands=["cat", "head", "tail"],
                reason="Documentation",
            ),
            PermissionRule(
                pattern=".env*", level=PermissionLevel.NONE, reason="Secrets protection"
            ),
            PermissionRule(
                pattern="**/node_modules/**", level=PermissionLevel.NONE, reason="Dependencies"
            ),
        ]

    def load_config(self) -> PermissionSet:
        """Load permissions from config file."""
        if not self.config_path.exists():
            logger.info("No permission config found, using defaults")
            return PermissionSet(rules=self._default_rules)

        try:
            data = json.loads(self.config_path.read_text())
            rules = [
                PermissionRule(
                    pattern=r.get("pattern", "*"),
                    level=PermissionLevel(r.get("level", "read_only")),
                    commands=r.get("commands", []),
                    reason=r.get("reason", ""),
                )
                for r in data.get("rules", [])
            ]
            return PermissionSet(
                rules=rules or self._default_rules,
                global_level=PermissionLevel(data.get("global_level", "read_only")),
                allowed_tools=set(data.get("allowed_tools", [])),
                blocked_paths=data.get("blocked_paths", []),
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Error loading config: {e}")
            return PermissionSet(rules=self._default_rules)

    def preload(self, task_type: str = "general") -> PreloadResult:
        """Pre-load permissions for a task.

        Args:
            task_type: Type of task (general, test, deploy, etc.)

        Returns:
            PreloadResult with applied permissions
        """
        permissions = self.load_config()
        warnings = []

        # Add task-specific rules
        if task_type == "test":
            permissions.allowed_tools.update(["pytest", "coverage", "mypy"])
        elif task_type == "deploy":
            warnings.append("Deploy tasks require manual approval")
        elif task_type == "dangerous":
            permissions.global_level = PermissionLevel.DANGEROUS
            warnings.append("Running in dangerous mode - all permissions granted")

        return PreloadResult(success=True, permissions=permissions, warnings=warnings)

    def check_permission(self, path: Path, operation: str, permissions: PermissionSet) -> bool:
        """Check if an operation is permitted on a path.

        Args:
            path: Path to check
            operation: Operation type (read, write, execute)
            permissions: Current permission set

        Returns:
            True if operation is permitted
        """
        path_str = str(path)

        # Check blocked paths
        for blocked in permissions.blocked_paths:
            if path_str.startswith(blocked) or path.match(blocked):
                return False

        # Check rules
        for rule in permissions.rules:
            if path.match(rule.pattern):
                if rule.level == PermissionLevel.NONE:
                    return False
                if operation == "write" and rule.level in (
                    PermissionLevel.WRITE_LOCAL,
                    PermissionLevel.WRITE_ALL,
                ):
                    return True
                if operation == "read":
                    return True

        # Fall back to global level
        if permissions.global_level == PermissionLevel.NONE:
            return False
        if operation == "write":
            return permissions.global_level in (
                PermissionLevel.WRITE_LOCAL,
                PermissionLevel.WRITE_ALL,
                PermissionLevel.DANGEROUS,
            )
        # Read operations allowed for anything except NONE
        return True

    def generate_cli_flags(self, permissions: PermissionSet) -> list[str]:
        """Generate CLI flags for Claude Code based on permissions.

        Args:
            permissions: Permission set to convert

        Returns:
            List of CLI flags
        """
        flags = []

        if permissions.global_level == PermissionLevel.DANGEROUS:
            flags.append("--dangerously-skip-permissions")
        else:
            for tool in permissions.allowed_tools:
                flags.append(f"--allowedTools={tool}")

        return flags


def create_preloader(config_path: Path | None = None) -> PermissionPreloader:
    """Factory function to create a PermissionPreloader."""
    return PermissionPreloader(config_path)
