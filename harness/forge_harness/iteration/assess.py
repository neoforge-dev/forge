"""Automated Assessment Phase for FORGE Harness.

Collects current state across all active projects in <60 seconds:
- Active tmux sessions and agent status
- Git status (staged, unstaged, untracked files)
- Test results from last run
- Failed tests, lint errors, type errors

Usage:
    from forge_harness.iteration.assess import assess_status

    report = assess_status()
    print(f"Active agents: {len(report.agents)}")
    print(f"Modified files: {len(report.git_status.modified)}")
    print(f"Failed tests: {report.test_results.failed_count}")
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from forge_harness.config import get_settings
from forge_harness.domain_registry import DomainRegistry
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class AgentType(str, Enum):
    """Types of agents in the FORGE ecosystem."""

    BACKEND = "backend-engineer"
    FRONTEND = "frontend-builder"
    DEBUG = "debug-detective"
    QA = "qa-tester"
    CONTENT = "content-writer"
    EXTERNAL = "external"  # Gemini, Codex, OpenCode, etc.
    UNKNOWN = "unknown"


class IssueType(str, Enum):
    """Types of issues detected during assessment."""

    FAILED_TEST = "failed_test"
    LINT_ERROR = "lint_error"
    TYPE_ERROR = "type_error"
    GIT_CONFLICT = "git_conflict"
    UNCOMMITTED_CHANGES = "uncommitted_changes"
    MISSING_DEPENDENCY = "missing_dependency"
    BUILD_ERROR = "build_error"
    RUNTIME_ERROR = "runtime_error"


class OverallHealth(str, Enum):
    """High-level health summary for the assessment."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILING = "failing"

    @property
    def emoji(self) -> str:
        """Emoji representation for dashboards."""
        return {
            OverallHealth.HEALTHY: "✅",
            OverallHealth.DEGRADED: "⚠️",
            OverallHealth.FAILING: "🔴",
        }[self]


@dataclass
class LintResults:
    """Linting results summary."""

    error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {"error_count": self.error_count}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LintResults:
        """Create from dictionary."""
        return cls(error_count=int(data.get("error_count", 0)))


class FailedTestsList(list):
    """List wrapper that compares equal to its length when compared to an int."""

    def __eq__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, int):
            return len(self) == other
        return list.__eq__(self, other)


@dataclass(init=False)
class AgentStatus:
    """Status of a single agent (tmux session)."""

    session_name: str | None
    agent_type: AgentType | None
    is_active: bool
    # Compatibility fields for older assess API
    name: str | None
    command: str | None
    raw_status: str | None
    working_directory: Path | None
    last_activity: datetime | None
    current_task: str | None
    metadata: dict[str, Any]

    def __init__(
        self,
        session_name: str | None = None,
        agent_type: AgentType | bool | None = None,
        is_active: bool | str = False,
        name: str | None = None,
        command: str | None = None,
        raw_status: str | None = None,
        working_directory: Path | None = None,
        last_activity: datetime | None = None,
        current_task: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Legacy positional signature: (name, is_active, command, raw_status)
        if isinstance(agent_type, bool) and isinstance(is_active, str):
            raw_status_legacy = name
            name = session_name
            session_name = session_name
            command = is_active
            is_active = agent_type
            raw_status = raw_status_legacy
            agent_type = AgentType.UNKNOWN

        if agent_type is None:
            agent_type = AgentType.UNKNOWN

        self.session_name = session_name
        self.name = name or session_name
        self.agent_type = agent_type if isinstance(agent_type, AgentType) else AgentType.UNKNOWN
        self.is_active = bool(is_active)
        self.command = command
        self.raw_status = raw_status
        self.working_directory = working_directory
        self.last_activity = last_activity
        self.current_task = current_task
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_name": self.session_name,
            "name": self.name,
            "agent_type": self.agent_type.value if self.agent_type else AgentType.UNKNOWN.value,
            "is_active": self.is_active,
            "command": self.command,
            "raw_status": self.raw_status,
            "working_directory": str(self.working_directory) if self.working_directory else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "current_task": self.current_task,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentStatus:
        """Create from dictionary."""
        agent_type = data.get("agent_type", AgentType.UNKNOWN.value)
        return cls(
            session_name=data.get("session_name") or data.get("name"),
            name=data.get("name") or data.get("session_name"),
            agent_type=AgentType(agent_type)
            if agent_type in AgentType._value2member_map_
            else AgentType.UNKNOWN,
            is_active=bool(data.get("is_active", False)),
            command=data.get("command"),
            raw_status=data.get("raw_status"),
            working_directory=Path(data["working_directory"])
            if data.get("working_directory")
            else None,
            last_activity=datetime.fromisoformat(data["last_activity"])
            if data.get("last_activity")
            else None,
            current_task=data.get("current_task"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ProjectStatus:
    """Git status for a single project."""

    domain: str
    project: str
    path: Path
    branch: str
    staged: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    ahead: int = 0
    behind: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "domain": self.domain,
            "project": self.project,
            "path": str(self.path),
            "branch": self.branch,
            "staged": self.staged,
            "modified": self.modified,
            "untracked": self.untracked,
            "conflicts": self.conflicts,
            "ahead": self.ahead,
            "behind": self.behind,
        }

    @property
    def is_clean(self) -> bool:
        """Check if working directory is clean."""
        return not (self.staged or self.modified or self.untracked or self.conflicts)

    @property
    def has_changes(self) -> bool:
        """Check if there are any changes."""
        return bool(self.staged or self.modified or self.untracked)


@dataclass
class GitStatus:
    """Aggregated git status across all projects."""

    projects: list[ProjectStatus] = field(default_factory=list)
    # Compatibility fields for single-repo assessment
    branch: str = ""
    modified_files: list[str] = field(default_factory=list)
    added_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    is_clean: bool = True
    total_staged: int = 0
    total_modified: int = 0
    total_untracked: int = 0
    total_conflicts: int = 0
    uncommitted_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "projects": [p.to_dict() for p in self.projects],
            "branch": self.branch,
            "modified_files": self.modified_files,
            "added_files": self.added_files,
            "untracked_files": self.untracked_files,
            "is_clean": self.is_clean,
            "total_staged": self.total_staged,
            "total_modified": self.total_modified,
            "total_untracked": self.total_untracked,
            "total_conflicts": self.total_conflicts,
            "uncommitted_count": self.uncommitted_count,
        }

    def __post_init__(self) -> None:
        if self.uncommitted_count == 0:
            self.uncommitted_count = self.total_staged + self.total_modified + self.total_untracked
        if not self.modified_files and self.total_modified:
            self.modified_files = ["<unknown>"] * self.total_modified
        if not self.added_files and self.total_staged:
            self.added_files = ["<unknown>"] * self.total_staged
        if not self.untracked_files and self.total_untracked:
            self.untracked_files = ["<unknown>"] * self.total_untracked
        if self.uncommitted_count == 0:
            self.is_clean = True
        else:
            self.is_clean = False

    @property
    def has_uncommitted_changes(self) -> bool:
        """Check if there are uncommitted changes."""
        return self.total_staged > 0 or self.total_modified > 0


@dataclass
class TestResult:
    """Single test result."""

    test_name: str
    status: str  # passed, failed, skipped, error
    duration: float | None = None
    message: str | None = None
    traceback: str | None = None
    file_path: str | None = None
    line_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "test_name": self.test_name,
            "status": self.status,
            "duration": self.duration,
            "message": self.message,
            "traceback": self.traceback,
            "file_path": self.file_path,
            "line_number": self.line_number,
        }


@dataclass
class TestResults:
    """Aggregated test results."""

    project_path: Path | None = None
    total_tests: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    # Compatibility fields
    passed_tests: int = 0
    skipped_tests: int = 0
    pass_rate: float | None = None
    is_passing: bool | None = None
    error_message: str | None = None
    duration: float = 0.0
    failed_tests: FailedTestsList = field(default_factory=FailedTestsList)
    last_run: datetime | None = None

    def __post_init__(self) -> None:
        # Sync counts between legacy and current fields
        if self.passed_tests and not self.passed_count:
            self.passed_count = self.passed_tests
        if self.passed_count and not self.passed_tests:
            self.passed_tests = self.passed_count
        if self.skipped_tests and not self.skipped_count:
            self.skipped_count = self.skipped_tests
        if self.skipped_count and not self.skipped_tests:
            self.skipped_tests = self.skipped_count
        # Normalize failed_tests to list wrapper
        if isinstance(self.failed_tests, int):
            self.failed_tests = FailedTestsList([None] * int(self.failed_tests))
        elif not isinstance(self.failed_tests, FailedTestsList):
            self.failed_tests = FailedTestsList(self.failed_tests)
        # If failed_count not set, infer from failed_tests length
        if self.failed_count == 0 and self.failed_tests:
            self.failed_count = len(self.failed_tests)
        # Compute pass_rate/is_passing if not provided
        if self.total_tests and self.pass_rate is None:
            self.pass_rate = round((self.passed_tests / self.total_tests) * 100, 2)
        if self.is_passing is None:
            self.is_passing = self.failed_count == 0 and self.error_count == 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "project_path": str(self.project_path) if self.project_path else None,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_count,
            "skipped_tests": self.skipped_tests,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "error_count": self.error_count,
            "pass_rate": self.pass_rate,
            "is_passing": self.is_passing,
            "error_message": self.error_message,
            "duration": self.duration,
            "failed_tests_detail": [
                t.to_dict() for t in self.failed_tests if hasattr(t, "to_dict")
            ],
            "last_run": self.last_run.isoformat() if self.last_run else None,
        }

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_tests == 0:
            return 1.0
        return self.passed_count / self.total_tests

    @property
    def failure_rate(self) -> float:
        """Calculate failure rate percentage."""
        if self.total_tests == 0:
            return 0.0
        return (self.failed_count / self.total_tests) * 100


@dataclass
class Issue:
    """Detected issue requiring attention."""

    issue_type: IssueType
    severity: str  # critical, high, medium, low
    message: str
    location: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "issue_type": self.issue_type.value,
            "severity": self.severity,
            "message": self.message,
            "location": self.location,
            "details": self.details,
        }


@dataclass
class AssessmentReport:
    """Complete assessment report of current state.

    This is the primary output of the assess phase, providing a
    comprehensive snapshot of the FORGE portfolio state.
    """

    # Agent status
    agents: list[AgentStatus] = field(default_factory=list)

    # Git status
    git_status: GitStatus = field(default_factory=GitStatus)

    # Test results
    test_results: TestResults = field(default_factory=TestResults)

    # Lint errors (compat)
    lint_errors: LintResults = field(default_factory=LintResults)

    # Overall health (compat)
    overall_health: OverallHealth = OverallHealth.HEALTHY

    # Issues
    issues: list[Issue] = field(default_factory=list)

    # Metadata
    assessed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "agents": [a.to_dict() for a in self.agents],
            "git_status": self.git_status.to_dict(),
            "test_results": self.test_results.to_dict(),
            "lint_errors": self.lint_errors.to_dict(),
            "overall_health": self.overall_health.value,
            "issues": [i.to_dict() for i in self.issues],
            "assessed_at": self.assessed_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "summary": self.get_summary(),
        }

    def get_summary(self) -> dict[str, Any]:
        """Get high-level summary statistics."""
        return {
            "active_agents": len([a for a in self.agents if a.is_active]),
            "total_agents": len(self.agents),
            "projects_with_changes": len([p for p in self.git_status.projects if p.has_changes]),
            "total_projects": len(self.git_status.projects),
            "failed_tests": self.test_results.failed_count,
            "total_tests": self.test_results.total_tests,
            "critical_issues": len([i for i in self.issues if i.severity == "critical"]),
            "total_issues": len(self.issues),
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssessmentReport:
        """Create from dictionary."""
        agents = [AgentStatus.from_dict(a) for a in data.get("agents", [])]
        git_status = (
            GitStatus(**data.get("git_status", {})) if data.get("git_status") else GitStatus()
        )
        test_payload = data.get("test_results", {}) if data.get("test_results") else {}
        allowed_test_keys = {
            "project_path",
            "total_tests",
            "passed_count",
            "failed_count",
            "skipped_count",
            "error_count",
            "passed_tests",
            "skipped_tests",
            "pass_rate",
            "is_passing",
            "error_message",
            "duration",
            "failed_tests",
            "last_run",
        }
        test_payload = {k: v for k, v in test_payload.items() if k in allowed_test_keys}
        test_results = TestResults(**test_payload) if test_payload else TestResults()
        lint_errors = LintResults.from_dict(data.get("lint_errors", {}))
        overall_health = OverallHealth(data.get("overall_health", OverallHealth.HEALTHY.value))
        assessed_at = (
            datetime.fromisoformat(data["assessed_at"])
            if data.get("assessed_at")
            else datetime.now(UTC)
        )
        return cls(
            agents=agents,
            git_status=git_status,
            test_results=test_results,
            lint_errors=lint_errors,
            overall_health=overall_health,
            issues=[],
            assessed_at=assessed_at,
            duration_seconds=float(data.get("duration_seconds", 0.0)),
        )

    @classmethod
    def from_json(cls, json_str: str) -> AssessmentReport:
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))


# ============================================================================
# Assessment Functions
# ============================================================================


def _assess_agents() -> list[AgentStatus]:
    """Compatibility wrapper for agent assessment."""
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-a", "-F", "#S:#W:#F:#W"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    agents: list[AgentStatus] = []
    if result.returncode != 0:
        return agents
    for line in result.stdout.splitlines():
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 4:
            continue
        name = f"{parts[0]}:{parts[1]}"
        active_flag, command = parts[2], parts[3]
        if not name.startswith("forge:"):
            continue
        agents.append(
            AgentStatus(
                name=name,
                is_active=active_flag == "1",
                command=command,
                raw_status=line,
            )
        )
    return agents


def _assess_git_status(project_path: str | Path) -> GitStatus:
    """Assess git status for a single repository."""
    try:
        result_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=project_path,
            check=False,
        )
        branch = result_branch.stdout.strip()
        result_status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=project_path,
            check=False,
        )
        modified: list[str] = []
        added: list[str] = []
        untracked: list[str] = []
        for line in result_status.stdout.splitlines():
            if not line:
                continue
            status = line[:2]
            path = line[3:].strip()
            if status == "??":
                untracked.append(path)
            elif "A" in status:
                added.append(path)
            elif "M" in status:
                modified.append(path)
        return GitStatus(
            branch=branch,
            modified_files=modified,
            added_files=added,
            untracked_files=untracked,
            uncommitted_count=len(modified) + len(added) + len(untracked),
            is_clean=(len(modified) + len(added) + len(untracked)) == 0,
        )
    except FileNotFoundError:
        return GitStatus(branch="", is_clean=True)


def _assess_test_results(project_path: str | Path) -> TestResults:
    """Assess pytest results for a single repository."""
    report_path = Path(project_path) / ".pytest_report.json"
    try:
        result = subprocess.run(
            ["pytest", "-q", "--json-report", f"--json-report-file={report_path}"],
            capture_output=True,
            text=True,
            cwd=project_path,
            check=False,
        )
    except FileNotFoundError as exc:
        return TestResults(total_tests=0, error_message=str(exc))

    # Try JSON report first
    try:
        with open(report_path) as f:
            report = json.load(f)
        summary = report.get("summary", {})
        total = int(summary.get("total", 0))
        passed = int(summary.get("passed", 0))
        failed = int(summary.get("failed", 0))
        skipped = int(summary.get("skipped", 0))
        results = TestResults(
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
        )
        results.is_passing = failed == 0
        return results
    except Exception:
        # Fallback to stdout parsing
        stdout = result.stdout or ""
        passed = _extract_test_count(stdout, "passed")
        failed = _extract_test_count(stdout, "failed")
        skipped = _extract_test_count(stdout, "skipped")
        total = passed + failed + skipped
        results = TestResults(
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
        )
        results.is_passing = failed == 0
        return results


def _extract_test_count(output: str, label: str) -> int:
    """Extract count like '24 passed' from pytest output."""
    match = re.search(rf"(\d+)\s+{re.escape(label)}", output)
    return int(match.group(1)) if match else 0


def _assess_lint_errors(project_path: str | Path) -> LintResults:
    """Assess lint errors using ruff."""
    try:
        result = subprocess.run(
            ["ruff", "check", "."],
            capture_output=True,
            text=True,
            cwd=project_path,
            check=False,
        )
    except FileNotFoundError:
        return LintResults(error_count=0)

    if result.returncode == 0:
        return LintResults(error_count=0)

    # Parse "× N error(s) found" from stderr
    match = re.search(r"(\d+)\s+error\(s\)\s+found", result.stderr or "")
    if match:
        return LintResults(error_count=int(match.group(1)))
    return LintResults(error_count=0)


def _determine_overall_health(lint_errors: int, test_results: TestResults) -> OverallHealth:
    """Determine overall health based on lint and test results."""
    failure_rate = test_results.failure_rate
    pass_rate = test_results.pass_rate or 0.0

    if lint_errors >= 20 or failure_rate >= 20.0:
        return OverallHealth.FAILING
    if lint_errors >= 5 or pass_rate < 90.0 or not test_results.is_passing:
        return OverallHealth.DEGRADED
    return OverallHealth.HEALTHY


def query_tmux_sessions() -> list[AgentStatus]:
    """Query all active tmux sessions for agent status.

    Returns:
        List of AgentStatus for each tmux session
    """
    agents = []

    try:
        # List all tmux sessions
        result = subprocess.run(
            [
                "tmux",
                "list-sessions",
                "-F",
                "#{session_name}:#{session_created}:#{session_activity}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            logger.warning("No tmux sessions found or tmux not running")
            return agents

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            parts = line.split(":")
            if len(parts) < 3:
                continue

            session_name = parts[0]
            created_time = int(parts[1])
            activity_time = int(parts[2])

            # Get working directory for the session
            cwd_result = subprocess.run(
                ["tmux", "display-message", "-t", session_name, "-p", "#{pane_current_path}"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            working_dir = Path(cwd_result.stdout.strip()) if cwd_result.returncode == 0 else None

            # Determine agent type from session name
            agent_type = _classify_agent_type(session_name)

            # Check if session is active (activity within last 5 minutes)
            is_active = (time.time() - activity_time) < 300

            agents.append(
                AgentStatus(
                    session_name=session_name,
                    agent_type=agent_type,
                    is_active=is_active,
                    working_directory=working_dir,
                    last_activity=datetime.fromtimestamp(activity_time, tz=UTC),
                    metadata={"created_at": created_time},
                )
            )

        logger.info(f"Found {len(agents)} tmux sessions")

    except subprocess.TimeoutExpired:
        logger.error("Timeout querying tmux sessions")
    except FileNotFoundError:
        logger.warning("tmux not found - skipping agent status")
    except Exception as e:
        logger.error(f"Error querying tmux sessions: {e}")

    return agents


def _classify_agent_type(session_name: str) -> AgentType:
    """Classify agent type from session name.

    Args:
        session_name: Tmux session name

    Returns:
        AgentType classification
    """
    session_lower = session_name.lower()

    # Check external agents first (most specific)
    if any(x in session_lower for x in ["gemini", "codex", "opencode", "cursor", "amp"]):
        return AgentType.EXTERNAL
    # Then check specific agent types (most specific first)
    elif "qa" in session_lower:
        return AgentType.QA
    elif "backend" in session_lower:
        return AgentType.BACKEND
    elif "frontend" in session_lower or "ui" in session_lower:
        return AgentType.FRONTEND
    elif "debug" in session_lower or "fix" in session_lower:
        return AgentType.DEBUG
    elif "content" in session_lower or "marketing" in session_lower:
        return AgentType.CONTENT
    # Check for test keyword (but not if it's clearly a web test)
    elif "test" in session_lower and "web" not in session_lower:
        return AgentType.QA
    # Generic patterns last
    elif "api" in session_lower or "service" in session_lower:
        return AgentType.BACKEND
    elif "web" in session_lower:
        return AgentType.FRONTEND
    else:
        return AgentType.UNKNOWN


def collect_git_status() -> GitStatus:
    """Collect git status across all domain projects.

    Returns:
        GitStatus with aggregated information
    """
    get_settings()
    registry = DomainRegistry()

    projects = []
    total_staged = 0
    total_modified = 0
    total_untracked = 0
    total_conflicts = 0

    # Get FORGE root
    forge_root = Path.cwd()
    if forge_root.name != "FORGE":
        # Try to find FORGE root
        for parent in forge_root.parents:
            if parent.name == "FORGE":
                forge_root = parent
                break

    for domain_key in registry.keys():
        domain = registry.get(domain_key)
        if not domain or not domain.active:
            continue

        for product_name in domain.products:
            project_path = forge_root / domain_key / product_name
            if not project_path.exists() or not (project_path / ".git").exists():
                continue

            try:
                project_status = _get_project_git_status(domain_key, product_name, project_path)
                projects.append(project_status)

                total_staged += len(project_status.staged)
                total_modified += len(project_status.modified)
                total_untracked += len(project_status.untracked)
                total_conflicts += len(project_status.conflicts)

            except Exception as e:
                logger.warning(f"Error getting git status for {domain_key}/{product_name}: {e}")

    git_status = GitStatus(
        projects=projects,
        total_staged=total_staged,
        total_modified=total_modified,
        total_untracked=total_untracked,
        total_conflicts=total_conflicts,
    )

    logger.info(
        f"Git status: {len(projects)} projects, "
        f"{total_modified} modified, {total_staged} staged, {total_untracked} untracked"
    )

    return git_status


def _get_project_git_status(domain: str, project: str, path: Path) -> ProjectStatus:
    """Get git status for a single project.

    Args:
        domain: Domain name
        project: Project name
        path: Project path

    Returns:
        ProjectStatus
    """
    # Get current branch
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=5,
    )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"

    # Get status
    status_result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uno"],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=5,
    )

    staged = []
    modified = []
    untracked = []
    conflicts = []

    if status_result.returncode == 0:
        for line in status_result.stdout.strip().split("\n"):
            if not line:
                continue

            status_code = line[:2]
            file_path = line[3:]

            if "U" in status_code or status_code in ["DD", "AA", "DU", "UD", "AU", "UA"]:
                conflicts.append(file_path)
            elif status_code[0] in ["M", "A", "D", "R", "C"]:
                staged.append(file_path)
            elif status_code[1] in ["M", "D"]:
                modified.append(file_path)
            elif status_code == "??":
                untracked.append(file_path)

    # Get ahead/behind count
    ahead = 0
    behind = 0
    ahead_behind_result = subprocess.run(
        ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if ahead_behind_result.returncode == 0:
        parts = ahead_behind_result.stdout.strip().split()
        if len(parts) == 2:
            ahead = int(parts[0])
            behind = int(parts[1])

    return ProjectStatus(
        domain=domain,
        project=project,
        path=path,
        branch=branch,
        staged=staged,
        modified=modified,
        untracked=untracked,
        conflicts=conflicts,
        ahead=ahead,
        behind=behind,
    )


def collect_test_results() -> TestResults:
    """Collect test results from last run.

    Looks for pytest cache and result files to extract test information.

    Returns:
        TestResults with aggregated test data
    """
    test_results = TestResults()

    # Try to find pytest cache in current directory or FORGE root
    cache_paths = [
        Path.cwd() / ".pytest_cache",
        Path.cwd() / "harness" / ".pytest_cache",
    ]

    for cache_path in cache_paths:
        if not cache_path.exists():
            continue

        try:
            # Read lastfailed if it exists
            lastfailed_path = cache_path / "v" / "cache" / "lastfailed"
            if lastfailed_path.exists():
                with open(lastfailed_path) as f:
                    lastfailed_data = json.load(f)
                    for test_path, always_fail in lastfailed_data.items():
                        test_results.failed_tests.append(
                            TestResult(
                                test_name=test_path,
                                status="failed",
                                file_path=test_path.split("::")[0] if "::" in test_path else None,
                            )
                        )
                    test_results.failed_count = len(lastfailed_data)

            # Read nodeids to get total test count
            nodeids_path = cache_path / "v" / "cache" / "nodeids"
            if nodeids_path.exists():
                with open(nodeids_path) as f:
                    nodeids_data = json.load(f)
                    test_results.total_tests = len(nodeids_data)

            # Read stepwise to get last run time
            stepwise_path = cache_path / "v" / "cache" / "stepwise"
            if stepwise_path.exists():
                stat = stepwise_path.stat()
                test_results.last_run = datetime.fromtimestamp(stat.st_mtime, tz=UTC)

            test_results.passed_count = test_results.total_tests - test_results.failed_count

            logger.info(
                f"Test results: {test_results.total_tests} total, "
                f"{test_results.passed_count} passed, {test_results.failed_count} failed"
            )
            break

        except Exception as e:
            logger.warning(f"Error reading pytest cache from {cache_path}: {e}")

    return test_results


def identify_issues(report: AssessmentReport) -> list[Issue]:
    """Identify issues from assessment data.

    Args:
        report: Assessment report to analyze

    Returns:
        List of identified issues
    """
    issues = []

    # Check for failed tests
    if report.test_results.failed_count > 0:
        issues.append(
            Issue(
                issue_type=IssueType.FAILED_TEST,
                severity="high" if report.test_results.failed_count > 5 else "medium",
                message=f"{report.test_results.failed_count} failed tests",
                details={
                    "failed_count": report.test_results.failed_count,
                    "total_count": report.test_results.total_tests,
                },
            )
        )

    # Check for git conflicts
    if report.git_status.total_conflicts > 0:
        issues.append(
            Issue(
                issue_type=IssueType.GIT_CONFLICT,
                severity="critical",
                message=f"{report.git_status.total_conflicts} files with merge conflicts",
                details={"conflict_count": report.git_status.total_conflicts},
            )
        )

    # Check for uncommitted changes
    uncommitted_count = report.git_status.total_modified + report.git_status.total_staged
    if uncommitted_count > 10:
        issues.append(
            Issue(
                issue_type=IssueType.UNCOMMITTED_CHANGES,
                severity="medium",
                message=f"{uncommitted_count} uncommitted changes across projects",
                details={
                    "modified": report.git_status.total_modified,
                    "staged": report.git_status.total_staged,
                },
            )
        )

    # Check for inactive agents with pending work
    for agent in report.agents:
        if not agent.is_active and agent.current_task:
            issues.append(
                Issue(
                    issue_type=IssueType.RUNTIME_ERROR,
                    severity="low",
                    message=f"Inactive agent {agent.session_name} with pending task",
                    location=agent.session_name,
                    details={
                        "agent_type": agent.agent_type.value,
                        "task": agent.current_task,
                    },
                )
            )

    return issues


def assess_status() -> AssessmentReport:
    """Run complete assessment and return report.

    This is the main entry point for the assess phase. It collects all
    relevant state information and produces a comprehensive report.

    Returns:
        AssessmentReport with complete assessment
    """
    start_time = time.time()

    logger.info("Starting assessment phase")

    # Collect all data in parallel where possible
    agents = _assess_agents()
    git_status = collect_git_status()
    test_results = _assess_test_results(Path.cwd())
    lint_results = _assess_lint_errors(Path.cwd())
    overall_health = _determine_overall_health(lint_results.error_count, test_results)

    # Build report
    report = AssessmentReport(
        agents=agents,
        git_status=git_status,
        test_results=test_results,
        lint_errors=lint_results,
        overall_health=overall_health,
    )

    # Identify issues
    report.issues = identify_issues(report)

    # Calculate duration
    report.duration_seconds = time.time() - start_time

    logger.info(
        f"Assessment complete in {report.duration_seconds:.2f}s: "
        f"{len(agents)} agents, {len(git_status.projects)} projects, "
        f"{test_results.total_tests} tests, {len(report.issues)} issues"
    )

    return report
