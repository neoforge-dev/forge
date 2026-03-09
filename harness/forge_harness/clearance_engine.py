"""
Clearance Engine for Multi-Agent Coordination
==========================================

Role-based work allocation with conflict detection.

Components:
------------
- RoleClearance: Define role-based work rules
- WorkAllocator: Assign work based on role, priority, conflicts
- ConflictDetector: Detect file conflicts with other agents
- DependencyTracker: Track task dependencies
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .state_store import (
    AgentRole,
    AssignmentStatus,
    ConflictRisk,
    StateStore,
    WorkAssignment,
)

logger = get_logger(__name__)


# =============================================================================
# Role Clearance Rules
# =============================================================================


class RoleClearance:
    """Role-based work clearance rules.

    Defines what each agent role can/cannot work on, and when human
    approval is required.
    """

    # Role work rules
    ROLE_RULES = {
        AgentRole.CTO: {
            "can_work_on": ["*"],  # Everything
            "cannot_work_on": [],
            "requires_human": [
                "major_architectural_change",
                "security_critical",
                "breaking_api_change",
                "database_schema_change",
            ],
            "focus_areas": [
                "architecture",
                "tech_stack_decisions",
                "security",
                "compliance",
                "human_gates",
            ],
        },
        AgentRole.PM: {
            "can_work_on": [
                "docs/",
                "PLAN.md",
                "CLAUDE.md",
                "*.md",
                "README*",
                "content/",
                "marketing/",
            ],
            "cannot_work_on": [
                "src/",
                "backend/",
                "frontend/",
                "tests/",
                "package.json",
                "pyproject.toml",
            ],
            "requires_human": [
                "product_decision",
                "priority_change",
                "scope_change",
            ],
            "focus_areas": [
                "planning",
                "priorities",
                "documentation",
                "content",
                "user_stories",
            ],
        },
        AgentRole.BUILDER: {
            "can_work_on": [
                "src/",
                "backend/",
                "frontend/",
                "tests/",
                "package.json",
                "pyproject.toml",
                "requirements*.txt",
            ],
            "cannot_work_on": [
                "docs/",
                "PLAN.md",
                "CLAUDE.md",
                "production/",
                ".env*",
            ],
            "requires_human": [
                "deploy_to_production",
                "database_migration",
            ],
            "focus_areas": [
                "implementation",
                "testing",
                "refactoring",
                "deployment",
            ],
        },
        AgentRole.QA: {
            "can_work_on": [
                "tests/",
                "e2e/",
                "test-*.py",
                "*.test.*",
                "*.spec.*",
            ],
            "cannot_work_on": [
                "src/",
                "backend/",
                "frontend/",
                "docs/",
            ],
            "requires_human": [
                "test_failure_investigation",
                "blocker_bug_report",
            ],
            "focus_areas": [
                "testing",
                "quality_assurance",
                "code_review",
                "verification",
            ],
        },
        AgentRole.CONTENT: {
            "can_work_on": [
                "content/",
                "marketing/",
                "blog/",
                "posts/",
                "*.md",
            ],
            "cannot_work_on": [
                "src/",
                "backend/",
                "frontend/",
                "tests/",
                "package.json",
                "pyproject.toml",
            ],
            "requires_human": [
                "brand_decision",
                "content_strategy_change",
            ],
            "focus_areas": [
                "content",
                "marketing",
                "blog_posts",
                "social_media",
                "lead_magnets",
            ],
        },
    }

    # Task type to human gate mapping
    TASK_HUMAN_GATES = {
        "feature": ["major_architectural_change"],
        "bug": ["blocker_bug_report"],
        "refactor": ["major_architectural_change"],
        "test": [],
        "doc": [],
        "content": ["brand_decision", "content_strategy_change"],
    }

    @classmethod
    def can_work_on_file(cls, role: AgentRole, file_path: str) -> tuple[bool, str]:
        """Check if role can work on file.

        Args:
            role: Agent role.
            file_path: File path to check.

        Returns:
            (can_work, reason) tuple.
        """
        rules = cls.ROLE_RULES.get(role)
        if not rules:
            return False, f"Unknown role: {role}"

        can_work_on = rules["can_work_on"]
        cannot_work_on = rules["cannot_work_on"]

        # Check explicit cannot_work_on
        for pattern in cannot_work_on:
            if re.match(pattern.replace("*", ".*"), file_path):
                return (
                    False,
                    f"Role {role.value} cannot work on {file_path} (blocked by pattern: {pattern})",
                )

        # Check can_work_on
        for pattern in can_work_on:
            if pattern == "*" or re.match(pattern.replace("*", ".*"), file_path):
                return True, ""

        return False, f"Role {role.value} is not authorized to work on {file_path}"

    @classmethod
    def requires_human(cls, role: AgentRole, task_type: str, task_tags: list[str] = []) -> bool:
        """Check if task requires human approval.

        Args:
            role: Agent role.
            task_type: Task type (feature, bug, refactor, test, doc, content).
            task_tags: Optional task tags for classification.

        Returns:
            True if human approval required.
        """
        rules = cls.ROLE_RULES.get(role)
        if not rules:
            return True  # Unknown role requires human

        # Check role-specific human gates
        role_gates = rules["requires_human"]

        # Check task-specific human gates
        task_gates = cls.TASK_HUMAN_GATES.get(task_type, [])

        # Check if any gate matches task tags
        # No need to check if task_tags is None since we use default empty list
        all_gates = role_gates + task_gates
        for tag in task_tags:
            for gate in all_gates:
                if gate.lower() in tag.lower():
                    return True

        return False

    @classmethod
    def get_focus_areas(cls, role: AgentRole) -> list[str]:
        """Get focus areas for role.

        Args:
            role: Agent role.

        Returns:
            List of focus areas.
        """
        rules = cls.ROLE_RULES.get(role)
        return rules.get("focus_areas", []) if rules else []


# =============================================================================
# Conflict Detection
# =============================================================================


class ConflictDetector:
    """Detect conflicts between work assignments."""

    def __init__(self, state_store: StateStore):
        """Initialize conflict detector.

        Args:
            state_store: Persistent state store.
        """
        self.state_store = state_store

    def detect_conflicts(
        self,
        files: list[str],
        session_id: str,
        domain: str,
        project: str,
    ) -> tuple[ConflictRisk, list[dict]]:
        """Detect conflicts with other active agents.

        Args:
            files: Files to be modified.
            session_id: Agent session ID (exclude from conflict check).
            domain: Project domain.
            project: Project name.

        Returns:
            (risk, conflicts) tuple where conflicts is list of conflict details.
        """
        conflicts = []

        # Get all active agents in same project
        active_agents = self.state_store.get_active_agents(domain=domain, project=project)
        other_agents = [a for a in active_agents if a.session_id != session_id]

        if not other_agents:
            return ConflictRisk.LOW, []

        # Check each active agent for conflicts
        for agent in other_agents:
            if not agent.assignment_id:
                continue

            assignment = self.state_store.get_assignment(agent.assignment_id)
            if not assignment:
                continue

            # Check file overlap
            overlap = set(files) & set(assignment.files_affected)
            if overlap:
                conflicts.append(
                    {
                        "agent_id": agent.session_id,
                        "agent_role": agent.agent_role.value,
                        "assignment_id": agent.assignment_id,
                        "files": list(overlap),
                        "type": "file_conflict",
                    }
                )

        # Check recent commits (last 1 hour)
        recent_conflicts = self._check_recent_commits(files, project)
        conflicts.extend(recent_conflicts)

        # Determine risk level
        if conflicts:
            if len(conflicts) >= 3 or any(len(c["files"]) >= 5 for c in conflicts):
                return ConflictRisk.HIGH, conflicts
            elif len(conflicts) >= 2:
                return ConflictRisk.MEDIUM, conflicts
            else:
                return ConflictRisk.LOW, conflicts

        return ConflictRisk.LOW, []

    def _check_recent_commits(self, files: list[str], project: str) -> list[dict]:
        """Check recent commits for file conflicts.

        Args:
            files: Files to check.
            project: Project name.

        Returns:
            List of conflict details.
        """
        conflicts = []

        try:
            import subprocess

            # Get commits from last 1 hour
            cmd = [
                "git",
                "log",
                "--since=1 hour ago",
                "--name-only",
                "--pretty=format:%H|%an|%s",
                f"--={project}",
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0 or not result.stdout:
                return conflicts

            # Parse commits
            lines = result.stdout.strip().split("\n")
            current_commit = None
            modified_files = []

            for line in lines:
                if "|" in line:
                    # New commit
                    current_commit = line
                    modified_files = []
                elif line.strip():
                    # Modified file
                    modified_files.append(line.strip())

                    # Check if this file is in our list
                    if line.strip() in files and current_commit is not None:
                        parts = current_commit.split("|")
                        conflicts.append(
                            {
                                "type": "recent_commit",
                                "commit_hash": parts[0] if len(parts) > 0 else "unknown",
                                "author": parts[1] if len(parts) > 1 else "unknown",
                                "message": parts[2] if len(parts) > 2 else "unknown",
                                "file": line.strip(),
                            }
                        )

        except Exception as e:
            logger.warning(f"Failed to check recent commits: {e}")

        return conflicts


# =============================================================================
# Dependency Tracking
# =============================================================================


class DependencyTracker:
    """Track task dependencies and block/unblock assignments."""

    def __init__(self, state_store: StateStore):
        """Initialize dependency tracker.

        Args:
            state_store: Persistent state store.
        """
        self.state_store = state_store

    def is_blocked(self, task_dependencies: list[str]) -> tuple[bool, list[str]]:
        """Check if task is blocked by incomplete dependencies.

        Args:
            task_dependencies: List of dependency task IDs.

        Returns:
            (is_blocked, blocking_dependencies) tuple.
        """
        blocking = []

        for dep_id in task_dependencies:
            assignment = self.state_store.get_assignment(dep_id)

            # If dependency exists and not completed, it's blocking
            if assignment and assignment.status != AssignmentStatus.COMPLETED:
                blocking.append(
                    {
                        "dependency_id": dep_id,
                        "status": assignment.status.value,
                        "assigned_to": assignment.session_id,
                    }
                )

        return len(blocking) > 0, blocking

    def update_blocking_status(
        self,
        assignment_id: str,
        blocking: list[str],
        blocked_by: list[str],
    ) -> bool:
        """Update assignment blocking status.

        Args:
            assignment_id: Assignment ID.
            blocking: List of assignment IDs this is blocking.
            blocked_by: List of assignment IDs blocking this.

        Returns:
            True if successful.
        """
        assignment = self.state_store.get_assignment(assignment_id)
        if not assignment:
            return False

        assignment.blocking = blocking
        assignment.blocked_by = blocked_by

        # Save updated assignment
        return self.state_store.create_assignment(assignment)


# =============================================================================
# Work Assignment
# =============================================================================


@dataclass
class AssignmentRequest:
    """Work assignment request from agent."""

    session_id: str
    role: AgentRole
    domain: str
    project: str
    preferences: dict[str, Any] = field(default_factory=dict)
    focus_tags: list[str] = field(default_factory=list)


@dataclass
class ClearanceResponse:
    """Clearance response to agent."""

    success: bool
    message: str
    assignment_id: str | None = None
    task_type: str | None = None
    task_description: str | None = None
    priority: int | None = None
    work_tree_path: str | None = None
    files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    requires_human: bool = False
    human_reason: str | None = None


# =============================================================================
# Work Allocator
# =============================================================================


class WorkAllocator:
    """Allocate work to agents based on role, priority, and conflicts."""

    def __init__(
        self,
        state_store: StateStore,
        forge_root: Path,
        github_repo: str | None = None,
    ):
        """Initialize work allocator.

        Args:
            state_store: Persistent state store.
            forge_root: FORGE repository root.
            github_repo: GitHub repository (owner/repo format).
        """
        self.state_store = state_store
        self.forge_root = forge_root
        self.github_repo = github_repo
        self.role_clearance = RoleClearance()
        self.conflict_detector = ConflictDetector(state_store)
        self.dependency_tracker = DependencyTracker(state_store)

    def allocate_work(self, request: AssignmentRequest) -> ClearanceResponse:
        """Allocate work to agent based on request.

        Args:
            request: Assignment request from agent.

        Returns:
            Clearance response with assignment details.
        """
        logger.info(f"Allocating work for {request.session_id} ({request.role.value})")

        # Step 1: Get available work from multiple sources
        available_work = self._get_available_work(request)

        if not available_work:
            return ClearanceResponse(
                success=False,
                message="No available work for this agent",
            )

        # Step 2: Filter by role clearance
        cleared_work = self._filter_by_role_clearance(request.role, available_work)
        if not cleared_work:
            return ClearanceResponse(
                success=False,
                message=f"No work available for role {request.role.value}",
            )

        # Step 3: Filter by focus tags/preferences
        focused_work = self._filter_by_focus(request, cleared_work)
        if not focused_work:
            focused_work = cleared_work  # Fallback to all cleared work

        # Step 4: Sort by priority (ICE score)
        focused_work.sort(key=lambda x: x.get("priority", 0), reverse=True)

        # Step 5: Check for conflicts
        best_work = focused_work[0]
        risk, conflicts = self.conflict_detector.detect_conflicts(
            files=best_work.get("files_affected", []),
            session_id=request.session_id,
            domain=request.domain,
            project=request.project,
        )

        # Step 6: Check dependencies
        dependencies = best_work.get("dependencies", [])
        is_blocked, blocking = self.dependency_tracker.is_blocked(dependencies)

        # Step 7: Check human gates
        task_type = best_work.get("task_type", "feature")
        task_tags = best_work.get("tags", [])
        requires_human = self.role_clearance.requires_human(request.role, task_type, task_tags)
        human_reason = None
        if requires_human:
            human_reason = (
                f"Task type '{task_type}' for role '{request.role.value}' requires human approval"
            )

        # Step 8: Determine work tree strategy
        work_tree_path = None
        if risk in [ConflictRisk.MEDIUM, ConflictRisk.HIGH]:
            # Create work tree to avoid conflicts
            work_tree_path = str(self.forge_root / "worktrees" / request.session_id)

        # Step 9: Create assignment
        assignment_id = str(uuid.uuid4())[:8]

        # Extract blocked_by list with proper type handling
        blocked_by_list = []
        for b in blocking:
            # Use dict.get method instead of [] access for type safety
            dep_id = b.get("dependency_id") if isinstance(b, dict) else None
            if isinstance(dep_id, str):
                blocked_by_list.append(dep_id)

        assignment = WorkAssignment(
            assignment_id=assignment_id,
            session_id=request.session_id,
            task_type=task_type,
            task_description=best_work.get("description", ""),
            priority=best_work.get("priority", 0),
            domain=request.domain,
            project=request.project,
            files_affected=best_work.get("files_affected", []),
            dependencies=dependencies,
            conflict_risk=risk,
            blocked_by=blocked_by_list,
        )

        # Step 10: Acquire work lock
        task_id = best_work.get("task_id", f"assignment-{assignment_id}")
        if not self.state_store.acquire_work_lock(task_id, request.session_id):
            return ClearanceResponse(
                success=False,
                message=f"Task '{task_id}' is already locked by another agent",
            )

        # Step 11: Save assignment
        self.state_store.create_assignment(assignment)
        self.state_store.assign_work(request.session_id, assignment_id, work_tree_path)

        return ClearanceResponse(
            success=True,
            message="Work assigned successfully",
            assignment_id=assignment_id,
            task_type=task_type,
            task_description=best_work.get("description", ""),
            priority=best_work.get("priority", 0),
            work_tree_path=work_tree_path,
            files=best_work.get("files_affected", []),
            dependencies=dependencies,
            conflicts=conflicts,
            requires_human=requires_human,
            human_reason=human_reason,
        )

    def _get_available_work(self, request: AssignmentRequest) -> list[dict]:
        """Get available work from multiple sources.

        Args:
            request: Assignment request.

        Returns:
            List of available work items.
        """
        work_items = []

        # Source 1: GitHub Issues
        work_items.extend(self._get_github_issues(request))

        # Source 2: PLAN.md epics
        work_items.extend(self._get_plan_epics(request))

        # Source 3: Living docs active context
        work_items.extend(self._get_living_docs_tasks(request))

        return work_items

    def _get_github_issues(self, request: AssignmentRequest) -> list[dict]:
        """Get work from GitHub Issues.

        Args:
            request: Assignment request.

        Returns:
            List of work items.
        """
        work_items = []

        try:
            import subprocess

            # Get open issues for this project
            cmd = [
                "gh",
                "issue",
                "list",
                "--repo",
                self.github_repo or "bogdan-veliscu/FORGE",
                "--search",
                f"project:{request.project}",
                "--json",
                "number,title,body,labels,state",
                "--limit",
                "20",
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                logger.warning(f"Failed to fetch GitHub issues: {result.stderr}")
                return work_items

            import json

            issues = json.loads(result.stdout)
            for issue in issues:
                if issue["state"] != "OPEN":
                    continue

                # Extract priority from labels
                priority_label = next(
                    (l for l in issue["labels"] if l.get("name", "").startswith("priority:")),
                    None,
                )
                priority = 0
                if priority_label:
                    match = re.search(r"priority:(\d+)", priority_label["name"])
                    if match:
                        priority = int(match.group(1))

                work_items.append(
                    {
                        "task_id": f"github-issue-{issue['number']}",
                        "task_type": "feature",  # Default, could parse from body/labels
                        "description": issue["title"],
                        "priority": priority,
                        "files_affected": [],  # Would need to parse from body
                        "dependencies": [],
                        "tags": [l["name"] for l in issue["labels"]],
                        "source": "github",
                    }
                )

        except Exception as e:
            logger.warning(f"Failed to get GitHub issues: {e}")

        return work_items

    def _get_plan_epics(self, request: AssignmentRequest) -> list[dict]:
        """Get work from PLAN.md epics.

        Args:
            request: Assignment request.

        Returns:
            List of work items.
        """
        work_items = []

        try:
            plan_path = self.forge_root / request.domain / request.project / "PLAN.md"
            if not plan_path.exists():
                return work_items

            content = plan_path.read_text()

            # Parse ICE-scored epics
            # Format: ## 1. Epic Name [I:8 C:9 E:7] (Score: 7.2)
            epic_pattern = (
                r"##\s+(\d+)\.\s+(.+?)\s*\[I:(\d+)\s+C:(\d+)\s+E:(\d+)\]\s*\(Score:\s*([\d.]+)\)"
            )
            matches = re.findall(epic_pattern, content)

            for epic_num, name, impact, confidence, ease, score in matches:
                work_items.append(
                    {
                        "task_id": f"plan-epic-{epic_num}",
                        "task_type": "feature",
                        "description": name.strip(),
                        "priority": int(float(score) * 10),  # Scale to integer
                        "files_affected": [],  # Would need to parse tasks under epic
                        "dependencies": [],
                        "tags": [],
                        "source": "plan",
                    }
                )

        except Exception as e:
            logger.warning(f"Failed to get PLAN.md epics: {e}")

        return work_items

    def _get_living_docs_tasks(self, request: AssignmentRequest) -> list[dict]:
        """Get work from living docs active context.

        Args:
            request: Assignment request.

        Returns:
            List of work items.
        """
        work_items = []

        try:
            active_context_path = (
                self.forge_root / request.domain / request.project / "docs" / "active-context.md"
            )
            if not active_context_path.exists():
                return work_items

            content = active_context_path.read_text()

            # Parse task list
            # Format: - [ ] Task description
            task_pattern = r"-\s+\[\s*\]\s+(.+?)(?=\n|$)"
            matches = re.findall(task_pattern, content)

            for i, task in enumerate(matches):
                work_items.append(
                    {
                        "task_id": f"living-docs-task-{i}",
                        "task_type": "feature",
                        "description": task.strip(),
                        "priority": 50,  # Default priority
                        "files_affected": [],
                        "dependencies": [],
                        "tags": ["living-docs"],
                        "source": "living-docs",
                    }
                )

        except Exception as e:
            logger.warning(f"Failed to get living docs tasks: {e}")

        return work_items

    def _filter_by_role_clearance(
        self,
        role: AgentRole,
        work_items: list[dict],
    ) -> list[dict]:
        """Filter work items by role clearance.

        Args:
            role: Agent role.
            work_items: List of work items.

        Returns:
            Filtered list of work items.
        """
        focus_areas = self.role_clearance.get_focus_areas(role)

        # If no focus areas, return all
        if not focus_areas:
            return work_items

        cleared = []
        for item in work_items:
            # Check if task matches focus area
            task_desc = item.get("description", "").lower()
            tags = [t.lower() for t in item.get("tags", [])]

            matches_focus = any(
                area in task_desc or any(area in tag for tag in tags) for area in focus_areas
            )

            if matches_focus:
                cleared.append(item)

        return cleared

    def _filter_by_focus(
        self,
        request: AssignmentRequest,
        work_items: list[dict],
    ) -> list[dict]:
        """Filter work items by agent preferences/focus tags.

        Args:
            request: Assignment request.
            work_items: List of work items.

        Returns:
            Filtered list of work items.
        """
        if not request.focus_tags:
            return work_items

        focused = []
        for item in work_items:
            # Check if item matches any focus tag
            tags = [t.lower() for t in item.get("tags", [])]
            desc = item.get("description", "").lower()

            matches_focus = any(
                tag.lower() in desc or tag.lower() in tags for tag in request.focus_tags
            )

            if matches_focus:
                focused.append(item)

        return focused


# =============================================================================
# Clearance Engine (Main Interface)
# =============================================================================


class ClearanceEngine:
    """Main clearance engine for multi-agent work allocation."""

    def __init__(self, state_store: StateStore, forge_root: Path, github_repo: str | None = None):
        """Initialize clearance engine.

        Args:
            state_store: Persistent state store.
            forge_root: FORGE repository root.
            github_repo: GitHub repository (owner/repo format).
        """
        self.state_store = state_store
        self.forge_root = forge_root
        self.github_repo = github_repo
        self.work_allocator = WorkAllocator(state_store, forge_root, github_repo)

    def request_clearance(
        self,
        session_id: str,
        agent_role: AgentRole,
        domain: str,
        project: str,
        preferences: dict[str, Any] | None = None,
        focus_tags: list[str] | None = None,
    ) -> ClearanceResponse:
        """Request work clearance for agent.

        Args:
            session_id: Agent session ID.
            agent_role: Agent role.
            domain: Project domain.
            project: Project name.
            preferences: Agent preferences (optional).
            focus_tags: Focus tags for work allocation (optional).

        Returns:
            Clearance response with assignment or rejection reason.
        """
        request = AssignmentRequest(
            session_id=session_id,
            role=agent_role,
            domain=domain,
            project=project,
            preferences=preferences or {},
            focus_tags=focus_tags or [],
        )

        return self.work_allocator.allocate_work(request)

    def complete_assignment(
        self,
        session_id: str,
        assignment_id: str,
        summary: str,
    ) -> bool:
        """Mark assignment as completed.

        Args:
            session_id: Agent session ID.
            assignment_id: Assignment ID.
            summary: Completion summary.

        Returns:
            True if successful.
        """
        from .state_store import AssignmentStatus

        # Update assignment status
        assignment = self.state_store.get_assignment(assignment_id)
        if not assignment:
            logger.error(f"Assignment {assignment_id} not found")
            return False

        assignment.status = AssignmentStatus.COMPLETED
        assignment.completed_at = datetime.now(UTC)

        if not self.state_store.create_assignment(assignment):
            return False

        # Release work lock
        task_id = f"assignment-{assignment_id}"
        self.state_store.release_work_lock(task_id, session_id)

        # Unassign from agent
        # Pass None for assignment_id now that the assign_work method accepts it
        self.state_store.assign_work(session_id, None, None)

        # Publish completion event
        self.state_store.publish_status_change(
            session_id,
            {
                "event": "assignment_completed",
                "assignment_id": assignment_id,
                "summary": summary,
            },
        )

        logger.info(f"Assignment {assignment_id} completed by {session_id}")
        return True

    def get_assignments(
        self,
        domain: str | None = None,
        project: str | None = None,
        status: AssignmentStatus | None = None,
    ) -> list[WorkAssignment]:
        """Get all assignments.

        Args:
            domain: Filter by domain (optional).
            project: Filter by project (optional).
            status: Filter by status (optional).

        Returns:
            List of assignments sorted by priority (highest first).
        """
        assignments = self.state_store.list_assignments(domain, project, status)
        if not isinstance(assignments, list):
            return []
        return assignments
