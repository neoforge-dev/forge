"""
Permission Pre-loader for FORGE Fleet
======================================

Configures all agents with required permissions at fleet startup to avoid
interactive prompts during autonomous operation.

Features:
- Load permission profiles from JSON config
- Apply permissions via agent initialization scripts
- Verify permissions are active via test invocation
- Support per-agent and per-domain permission sets
- Fail fast if critical permissions missing

Usage:
    from forge_harness.fleet.permissions import PermissionPreloader

    preloader = PermissionPreloader()

    # Load and apply permissions
    preloader.load_permissions("config/agent_permissions.json")
    preloader.apply_permissions("backend-engineer", ["file:write", "git:commit"])

    # Verify permissions
    if not preloader.verify_permissions("backend-engineer"):
        raise RuntimeError("Critical permissions missing")
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)


# Default permission sets per agent type
DEFAULT_PERMISSIONS: dict[str, list[str]] = {
    "orchestrator": [
        "file:read",
        "git:status",
        "git:log",
        "command:exec",
        "tmux:send",
        "task:create",
    ],
    "backend-engineer": [
        "file:read",
        "file:write",
        "file:create",
        "git:status",
        "git:commit",
        "git:push",
        "command:exec",
        "test:run",
        "db:migrate",
    ],
    "frontend-builder": [
        "file:read",
        "file:write",
        "file:create",
        "git:status",
        "git:commit",
        "git:push",
        "command:exec",
        "test:run",
        "npm:install",
    ],
    "debug-detective": [
        "file:read",
        "git:status",
        "git:diff",
        "command:exec",
        "test:run",
        "log:read",
    ],
    "qa-specialist": [
        "file:read",
        "file:write",
        "git:status",
        "command:exec",
        "test:run",
        "test:debug",
    ],
    "content-creator": [
        "file:read",
        "file:write",
        "file:create",
        "git:status",
        "git:commit",
        "api:read",
    ],
}

# Critical permissions that must be verified for each agent type
CRITICAL_PERMISSIONS: dict[str, list[str]] = {
    "orchestrator": ["file:read", "task:create"],
    "backend-engineer": ["file:read", "file:write", "test:run"],
    "frontend-builder": ["file:read", "file:write", "test:run"],
    "debug-detective": ["file:read", "test:run"],
    "qa-specialist": ["file:read", "test:run"],
    "content-creator": ["file:read", "file:write"],
}

# Domain-specific permission overrides
DOMAIN_PERMISSIONS: dict[str, list[str]] = {
    "production": [
        "deployment:stage",
        "deployment:prod",
        "db:migrate:prod",
    ],
    "development": [
        "file:delete",
        "db:reset",
    ],
    "testing": [
        "test:run",
        "test:debug",
        "test:coverage",
    ],
}


@dataclass
class PermissionProfile:
    """Permission profile for an agent."""

    agent_type: str
    permissions: set[str] = field(default_factory=set)
    domain_permissions: dict[str, set[str]] = field(default_factory=dict)
    critical_permissions: set[str] = field(default_factory=set)
    last_applied: datetime | None = None
    verified: bool = False


@dataclass
class PermissionVerificationResult:
    """Result of permission verification."""

    agent_id: str
    agent_type: str
    verified: bool
    missing_permissions: list[str]
    critical_missing: list[str]
    timestamp: datetime
    error_message: str | None = None


class PermissionPreloader:
    """Pre-loads and manages agent permissions for autonomous operation."""

    def __init__(
        self,
        config_path: Path | None = None,
        fail_on_missing_critical: bool = True,
    ):
        """
        Initialize permission pre-loader.

        Args:
            config_path: Optional path to custom permission config JSON
            fail_on_missing_critical: If True, raise error on missing critical permissions
        """
        self.config_path = config_path
        self.fail_on_missing_critical = fail_on_missing_critical
        self.profiles: dict[str, PermissionProfile] = {}
        self.applied_permissions: dict[str, list[str]] = {}
        self.verification_history: list[PermissionVerificationResult] = []

        logger.info("Initialized PermissionPreloader")

    def load_permissions(self, config_path: Path | None = None) -> dict[str, PermissionProfile]:
        """
        Load permission profiles from JSON config file.

        Args:
            config_path: Path to permissions JSON config

        Returns:
            Dictionary mapping agent types to permission profiles

        Raises:
            FileNotFoundError: If config file not found
            json.JSONDecodeError: If config file is invalid JSON
        """
        path = config_path or self.config_path

        if path and path.exists():
            logger.info(f"Loading permissions from {path}")
            with open(path) as f:
                config = json.load(f)

            # Parse custom permissions
            for agent_type, perms in config.get("agents", {}).items():
                profile = PermissionProfile(
                    agent_type=agent_type,
                    permissions=set(perms.get("permissions", [])),
                    domain_permissions={
                        domain: set(domain_perms)
                        for domain, domain_perms in perms.get("domain_permissions", {}).items()
                    },
                    critical_permissions=set(perms.get("critical_permissions", [])),
                )
                self.profiles[agent_type] = profile

            logger.info(f"Loaded {len(self.profiles)} custom permission profiles")
        else:
            # Use default permissions
            logger.info("No custom config found, using default permissions")
            for agent_type, perms in DEFAULT_PERMISSIONS.items():
                profile = PermissionProfile(
                    agent_type=agent_type,
                    permissions=set(perms),
                    critical_permissions=set(CRITICAL_PERMISSIONS.get(agent_type, [])),
                )
                self.profiles[agent_type] = profile

        return self.profiles

    def apply_permissions(
        self, agent_type: str, permissions: list[str] | None = None, domain: str | None = None
    ) -> bool:
        """
        Apply permissions to an agent type.

        Args:
            agent_type: Type of agent (e.g., "backend-engineer")
            permissions: Optional list of permissions to apply (uses profile if None)
            domain: Optional domain for domain-specific permissions

        Returns:
            True if permissions applied successfully

        Raises:
            ValueError: If agent_type not found in profiles
        """
        # Get or create profile
        if agent_type not in self.profiles:
            if permissions:
                # Create profile from provided permissions
                profile = PermissionProfile(
                    agent_type=agent_type,
                    permissions=set(permissions),
                    critical_permissions=set(CRITICAL_PERMISSIONS.get(agent_type, [])),
                )
                self.profiles[agent_type] = profile
            else:
                raise ValueError(f"Unknown agent type: {agent_type}")
        else:
            profile = self.profiles[agent_type]

        # Determine final permission set
        final_permissions = set(permissions) if permissions else profile.permissions.copy()

        # Add domain-specific permissions
        if domain:
            if domain in profile.domain_permissions:
                final_permissions.update(profile.domain_permissions[domain])
            elif domain in DOMAIN_PERMISSIONS:
                final_permissions.update(DOMAIN_PERMISSIONS[domain])

        # Apply permissions (in real implementation, this would configure agent)
        self.applied_permissions[agent_type] = sorted(final_permissions)
        profile.last_applied = datetime.now(UTC)

        logger.info(
            f"Applied {len(final_permissions)} permissions to {agent_type}"
            + (f" (domain: {domain})" if domain else "")
        )

        return True

    def verify_permissions(self, agent_id: str, agent_type: str | None = None) -> bool:
        """
        Verify that an agent has all required permissions.

        Args:
            agent_id: Unique identifier for the agent instance
            agent_type: Type of agent (auto-detected if None)

        Returns:
            True if all critical permissions are present

        Raises:
            RuntimeError: If fail_on_missing_critical is True and critical perms missing
        """
        # Auto-detect agent type from ID if not provided
        if not agent_type:
            agent_type = self._detect_agent_type(agent_id)

        if not agent_type or agent_type not in self.profiles:
            error_msg = f"Cannot verify permissions for unknown agent type: {agent_type}"
            logger.error(error_msg)
            result = PermissionVerificationResult(
                agent_id=agent_id,
                agent_type=agent_type or "unknown",
                verified=False,
                missing_permissions=[],
                critical_missing=[],
                timestamp=datetime.now(UTC),
                error_message=error_msg,
            )
            self.verification_history.append(result)
            return False

        profile = self.profiles[agent_type]
        applied = set(self.applied_permissions.get(agent_type, []))

        # Check for missing permissions
        missing = profile.permissions - applied
        critical_missing = profile.critical_permissions - applied

        verified = len(critical_missing) == 0

        # Create verification result
        result = PermissionVerificationResult(
            agent_id=agent_id,
            agent_type=agent_type,
            verified=verified,
            missing_permissions=sorted(missing),
            critical_missing=sorted(critical_missing),
            timestamp=datetime.now(UTC),
        )

        self.verification_history.append(result)

        if critical_missing:
            error_msg = (
                f"Agent {agent_id} ({agent_type}) missing critical permissions: {critical_missing}"
            )
            logger.error(error_msg)

            if self.fail_on_missing_critical:
                raise RuntimeError(error_msg)

            return False

        if missing:
            logger.warning(
                f"Agent {agent_id} ({agent_type}) missing non-critical permissions: {missing}"
            )

        logger.info(f"Verified permissions for {agent_id} ({agent_type})")
        profile.verified = True

        return verified

    def get_permissions(self, agent_type: str, domain: str | None = None) -> list[str]:
        """
        Get the full list of permissions for an agent type.

        Args:
            agent_type: Type of agent
            domain: Optional domain for domain-specific permissions

        Returns:
            List of all permissions for the agent type
        """
        if agent_type not in self.profiles:
            return []

        profile = self.profiles[agent_type]
        permissions = profile.permissions.copy()

        # Add domain permissions
        if domain and domain in profile.domain_permissions:
            permissions.update(profile.domain_permissions[domain])

        return sorted(permissions)

    def get_verification_history(
        self, agent_id: str | None = None, agent_type: str | None = None
    ) -> list[PermissionVerificationResult]:
        """
        Get verification history, optionally filtered by agent ID or type.

        Args:
            agent_id: Optional agent ID to filter by
            agent_type: Optional agent type to filter by

        Returns:
            List of verification results
        """
        results = self.verification_history

        if agent_id:
            results = [r for r in results if r.agent_id == agent_id]

        if agent_type:
            results = [r for r in results if r.agent_type == agent_type]

        return results

    def export_config(self, output_path: Path) -> None:
        """
        Export current permission profiles to JSON config file.

        Args:
            output_path: Path to write config JSON
        """
        config = {
            "agents": {
                agent_type: {
                    "permissions": sorted(profile.permissions),
                    "domain_permissions": {
                        domain: sorted(perms)
                        for domain, perms in profile.domain_permissions.items()
                    },
                    "critical_permissions": sorted(profile.critical_permissions),
                }
                for agent_type, profile in self.profiles.items()
            }
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(config, f, indent=2)

        logger.info(f"Exported permission config to {output_path}")

    def reset_agent_permissions(self, agent_type: str) -> bool:
        """
        Reset an agent's permissions to defaults.

        Args:
            agent_type: Type of agent to reset

        Returns:
            True if reset successful
        """
        if agent_type not in DEFAULT_PERMISSIONS:
            logger.warning(f"Cannot reset unknown agent type: {agent_type}")
            return False

        profile = PermissionProfile(
            agent_type=agent_type,
            permissions=set(DEFAULT_PERMISSIONS[agent_type]),
            critical_permissions=set(CRITICAL_PERMISSIONS.get(agent_type, [])),
        )
        self.profiles[agent_type] = profile

        # Reapply
        return self.apply_permissions(agent_type)

    def add_permission(self, agent_type: str, permission: str, critical: bool = False) -> bool:
        """
        Add a permission to an agent type.

        Args:
            agent_type: Type of agent
            permission: Permission to add
            critical: Whether this is a critical permission

        Returns:
            True if permission added successfully
        """
        if agent_type not in self.profiles:
            logger.error(f"Cannot add permission to unknown agent type: {agent_type}")
            return False

        profile = self.profiles[agent_type]
        profile.permissions.add(permission)

        if critical:
            profile.critical_permissions.add(permission)

        logger.info(f"Added permission '{permission}' to {agent_type}")
        return True

    def remove_permission(self, agent_type: str, permission: str) -> bool:
        """
        Remove a permission from an agent type.

        Args:
            agent_type: Type of agent
            permission: Permission to remove

        Returns:
            True if permission removed successfully
        """
        if agent_type not in self.profiles:
            logger.error(f"Cannot remove permission from unknown agent type: {agent_type}")
            return False

        profile = self.profiles[agent_type]
        profile.permissions.discard(permission)
        profile.critical_permissions.discard(permission)

        logger.info(f"Removed permission '{permission}' from {agent_type}")
        return True

    @staticmethod
    def _detect_agent_type(agent_id: str) -> str | None:
        """
        Detect agent type from agent ID.

        Args:
            agent_id: Agent identifier (e.g., "forge:tech-001")

        Returns:
            Detected agent type or None
        """
        # Parse common patterns
        if "tech" in agent_id.lower():
            return "backend-engineer"
        elif "frontend" in agent_id.lower() or "ui" in agent_id.lower():
            return "frontend-builder"
        elif "debug" in agent_id.lower():
            return "debug-detective"
        elif "qa" in agent_id.lower() or "test" in agent_id.lower():
            return "qa-specialist"
        elif "content" in agent_id.lower():
            return "content-creator"
        elif "orchestrat" in agent_id.lower():
            return "orchestrator"

        return None


def create_permission_preloader(
    config_path: Path | None = None, fail_on_missing_critical: bool = True
) -> PermissionPreloader:
    """
    Create a permission pre-loader with optional config.

    Args:
        config_path: Optional path to custom permissions JSON
        fail_on_missing_critical: If True, raise error on missing critical permissions

    Returns:
        Configured PermissionPreloader instance
    """
    preloader = PermissionPreloader(
        config_path=config_path, fail_on_missing_critical=fail_on_missing_critical
    )
    preloader.load_permissions()
    return preloader
