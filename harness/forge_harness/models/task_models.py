"""
Task and Agent Data Models for Next Task Recommendation Engine.

Provides enums and dataclasses for task analysis, agent profiles,
and scoring components.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class TaskType(Enum):
    """Categories of work based on pattern analysis."""

    # Backend (API endpoints)
    API_SIMPLE = "api:simple"  # GET, basic POST
    API_STATEFUL = "api:stateful"  # Requires DB state
    API_BATCH = "api:batch"  # Multi-item operations
    API_EXTERNAL = "api:external"  # Calls other services

    # Frontend (UI components)
    FRONTEND_COMPONENT = "frontend:component"  # React/Vue component
    FRONTEND_PAGE = "frontend:page"  # Full page/route
    FRONTEND_STYLES = "frontend:styles"  # CSS/styling work
    FRONTEND_STATE = "frontend:state"  # State management

    # Testing
    TEST_UNIT = "test:unit"  # Unit tests
    TEST_INTEGRATION = "test:integration"  # Integration tests
    TEST_E2E = "test:e2e"  # End-to-end tests

    # Infrastructure
    INFRA_DEPLOY = "infra:deploy"  # Deployment configuration
    INFRA_CI_CD = "infra:ci_cd"  # CI/CD pipeline
    INFRA_DOCKER = "infra:docker"  # Docker/containerization

    # Documentation
    DOCS_API = "docs:api"  # API documentation
    DOCS_USER = "docs:user"  # User-facing docs
    DOCS_ARCHITECTURE = "docs:architecture"  # Architecture docs

    # Research
    RESEARCH_TECHNICAL = "research:technical"  # Technical research
    RESEARCH_MARKET = "research:market"  # Market/competitive research

    # Refactoring
    REFACTOR_CLEANUP = "refactor:cleanup"  # Code cleanup
    REFACTOR_OPTIMIZE = "refactor:optimize"  # Performance optimization
    REFACTOR_MIGRATE = "refactor:migrate"  # Migration/upgrade

    # Generic
    GENERAL = "general"  # Unclassified


class ComplexityLevel(Enum):
    """Estimated complexity based on description analysis."""

    TRIVIAL = 1  # < 30 minutes, minimal changes
    SIMPLE = 2  # 30m - 2h, straightforward
    MODERATE = 3  # 2h - 1 day, some complexity
    COMPLEX = 4  # 1-3 days, significant complexity
    EXPERT = 5  # 3+ days, requires deep expertise


class Skill(Enum):
    """Skills that can be required for tasks."""

    # Programming Languages
    PYTHON = "PYTHON"
    TYPESCRIPT = "TYPESCRIPT"
    JAVASCRIPT = "JAVASCRIPT"
    JAVA = "JAVA"
    GO = "GO"
    RUST = "RUST"
    SWIFT = "SWIFT"

    # Backend Frameworks
    FASTAPI = "FASTAPI"
    DJANGO = "DJANGO"
    FLASK = "FLASK"
    EXPRESS = "EXPRESS"
    SPRING = "SPRING"

    # Frontend Frameworks
    REACT = "REACT"
    VUE = "VUE"
    ANGULAR = "ANGULAR"
    SVELTE = "SVELTE"

    # Data & Database
    SQL = "SQL"
    POSTGRESQL = "POSTGRESQL"
    MONGODB = "MONGODB"
    REDIS = "REDIS"
    SQLALCHEMY = "SQLALCHEMY"

    # Testing
    PYTEST = "PYTEST"
    JEST = "JEST"
    PLAYWRIGHT = "PLAYWRIGHT"
    CYPRESS = "CYPRESS"

    # DevOps & Infrastructure
    DOCKER = "DOCKER"
    KUBERNETES = "KUBERNETES"
    AWS = "AWS"
    GCP = "GCP"
    TERRAFORM = "TERRAFORM"

    # AI/ML
    PYTORCH = "PYTORCH"
    TENSORFLOW = "TENSORFLOW"
    OPENAI = "OPENAI"
    LANGCHAIN = "LANGCHAIN"

    # Specialized
    AUTHENTICATION = "AUTHENTICATION"
    GRAPHQL = "GRAPHQL"
    REDUX = "REDUX"
    RESEARCH = "RESEARCH"
    DOCUMENTATION = "DOCUMENTATION"
    ANALYSIS = "ANALYSIS"


@dataclass
class TaskTypeHistory:
    """Historical performance for specific task type."""

    task_type: TaskType
    tasks_completed: int = 0
    tasks_failed: int = 0
    success_rate: float = 0.0  # 0-1
    avg_duration: timedelta = timedelta()
    avg_quality_score: float = 0.0  # 0-1
    last_completed: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "task_type": self.task_type.value,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "success_rate": self.success_rate,
            "avg_duration": self.avg_duration.total_seconds(),
            "avg_quality_score": self.avg_quality_score,
            "last_completed": self.last_completed.isoformat() if self.last_completed else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskTypeHistory":
        """Create from dictionary."""
        return cls(
            task_type=TaskType(data["task_type"]),
            tasks_completed=data["tasks_completed"],
            tasks_failed=data["tasks_failed"],
            success_rate=data["success_rate"],
            avg_duration=timedelta(seconds=data["avg_duration"]),
            avg_quality_score=data["avg_quality_score"],
            last_completed=datetime.fromisoformat(data["last_completed"]) if data["last_completed"] else None,
        )


@dataclass
class AgentProfile:
    """Agent capabilities and preferences."""

    # Identity
    agent_id: str  # e.g., "forge:opencode", "forge:codex"
    name: str
    model: str  # e.g., "gemini-2.0-flash", "claude-opus-4.6"

    # Capabilities (0-1 confidence)
    capabilities: dict[TaskType, float] = field(default_factory=dict)

    # Skills (0-1 confidence)
    skills: dict[Skill, float] = field(default_factory=dict)

    # Preferences
    max_complexity: ComplexityLevel = ComplexityLevel.MODERATE
    preferred_domains: list[str] = field(default_factory=list)
    avoids: list[TaskType] = field(default_factory=list)

    # Current State
    current_task_id: str | None = None
    status: str = "idle"  # "idle", "busy", "paused", "error"
    last_activity: datetime = field(default_factory=datetime.now)

    # Performance Metrics
    total_tasks_completed: int = 0
    success_rate: float = 0.0  # 0-1
    avg_task_duration: timedelta = timedelta()
    avg_quality_score: float = 0.0  # 0-1

    # Historical Performance (by TaskType)
    task_type_history: dict[TaskType, TaskTypeHistory] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "model": self.model,
            "capabilities": {k.value: v for k, v in self.capabilities.items()},
            "skills": {k.value: v for k, v in self.skills.items()},
            "max_complexity": self.max_complexity.value,
            "preferred_domains": self.preferred_domains,
            "avoids": [t.value for t in self.avoids],
            "current_task_id": self.current_task_id,
            "status": self.status,
            "last_activity": self.last_activity.isoformat(),
            "total_tasks_completed": self.total_tasks_completed,
            "success_rate": self.success_rate,
            "avg_task_duration": self.avg_task_duration.total_seconds(),
            "avg_quality_score": self.avg_quality_score,
            "task_type_history": {k.value: v.to_dict() for k, v in self.task_type_history.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentProfile":
        """Create from dictionary."""
        capabilities = {}
        for k, v in data.get("capabilities", {}).items():
            capabilities[TaskType(k)] = v

        skills = {}
        for k, v in data.get("skills", {}).items():
            skills[Skill(k)] = v

        avoids = [TaskType(t) for t in data.get("avoids", [])]

        task_type_history = {}
        for k, v in data.get("task_type_history", {}).items():
            task_type_history[TaskType(k)] = TaskTypeHistory.from_dict(v)

        return cls(
            agent_id=data["agent_id"],
            name=data["name"],
            model=data["model"],
            capabilities=capabilities,
            skills=skills,
            max_complexity=ComplexityLevel(data["max_complexity"]),
            preferred_domains=data.get("preferred_domains", []),
            avoids=avoids,
            current_task_id=data.get("current_task_id"),
            status=data.get("status", "idle"),
            last_activity=datetime.fromisoformat(data["last_activity"]) if data.get("last_activity") else datetime.now(),
            total_tasks_completed=data.get("total_tasks_completed", 0),
            success_rate=data.get("success_rate", 0.0),
            avg_task_duration=timedelta(seconds=data.get("avg_task_duration", 0)),
            avg_quality_score=data.get("avg_quality_score", 0.0),
            task_type_history=task_type_history,
        )

    def is_available(self) -> bool:
        """Check if agent is available to take new tasks."""
        return self.status in ("idle", "waiting")


@dataclass
class CodebaseContext:
    """Context from Code Atlas analysis."""

    # File Impact Analysis
    affected_files: list[str] = field(default_factory=list)
    affected_file_count: int = 0
    affected_modules: list[str] = field(default_factory=list)

    # Dependency Analysis
    max_dependency_depth: int = 0
    circular_dependencies: bool = False
    critical_path: bool = False

    # System Impact
    touches_critical_system: bool = False
    breaking_change_risk: float = 0.0  # 0-1

    # Test Coverage
    existing_test_coverage: float = 0.0  # 0-1
    test_gap_score: float = 0.0  # 0-1

    # Complexity Metrics
    cyclomatic_complexity: float | None = None
    maintainability_index: float | None = None

    # Domain Knowledge
    domain_patterns: list[str] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    suggested_improvements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "affected_files": self.affected_files,
            "affected_file_count": self.affected_file_count,
            "affected_modules": self.affected_modules,
            "max_dependency_depth": self.max_dependency_depth,
            "circular_dependencies": self.circular_dependencies,
            "critical_path": self.critical_path,
            "touches_critical_system": self.touches_critical_system,
            "breaking_change_risk": self.breaking_change_risk,
            "existing_test_coverage": self.existing_test_coverage,
            "test_gap_score": self.test_gap_score,
            "cyclomatic_complexity": self.cyclomatic_complexity,
            "maintainability_index": self.maintainability_index,
            "domain_patterns": self.domain_patterns,
            "anti_patterns": self.anti_patterns,
            "suggested_improvements": self.suggested_improvements,
        }


@dataclass
class TaskAnalysis:
    """Enriched task with analysis metadata."""

    # Base Task Fields
    id: str
    subject: str
    description: str
    priority: int  # 1-10, higher = more urgent
    domain: str | None = None
    project: str | None = None
    status: str = "pending"  # "pending", "in_progress", "completed", "blocked"

    # Analysis Fields (Computed)
    task_type: TaskType = TaskType.GENERAL
    complexity: ComplexityLevel = ComplexityLevel.SIMPLE
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    blocked: bool = False
    estimated_duration: timedelta | None = None
    required_skills: set[Skill] = field(default_factory=set)

    # Scoring Fields (Computed)
    priority_score: float = 0.0  # 0-1, normalized priority
    capability_match_score: float | None = None  # 0-1, per agent
    historical_score: float | None = None  # 0-1, per agent
    final_score: float | None = None  # 0-1, weighted combination
    recommended_agent: str | None = None  # Agent ID for best match

    # Codebase Context
    codebase_context: CodebaseContext | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "priority": self.priority,
            "domain": self.domain,
            "project": self.project,
            "status": self.status,
            "task_type": self.task_type.value,
            "complexity": self.complexity.value,
            "dependencies": self.dependencies,
            "dependents": self.dependents,
            "blocked": self.blocked,
            "estimated_duration": self.estimated_duration.total_seconds() if self.estimated_duration else None,
            "required_skills": [s.value for s in self.required_skills],
            "priority_score": self.priority_score,
            "capability_match_score": self.capability_match_score,
            "historical_score": self.historical_score,
            "final_score": self.final_score,
            "recommended_agent": self.recommended_agent,
            "codebase_context": self.codebase_context.to_dict() if self.codebase_context else None,
        }


@dataclass
class RecommendationFeedback:
    """Feedback on recommendation quality."""

    recommendation_id: str
    task_id: str
    agent_id: str
    recommended: bool  # Was this task recommended?
    accepted: bool  # Did agent accept the recommendation?
    completed: bool  # Was task completed successfully?
    duration: timedelta  # Actual time taken
    quality_score: float | None = None  # 0-1, from code review
    feedback: str | None = None  # Agent feedback

    # Learning signals
    was_good_recommendation: bool = False
    should_adjust_algorithm: bool = False
    adjustment_notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "recommendation_id": self.recommendation_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "recommended": self.recommended,
            "accepted": self.accepted,
            "completed": self.completed,
            "duration": self.duration.total_seconds(),
            "quality_score": self.quality_score,
            "feedback": self.feedback,
            "was_good_recommendation": self.was_good_recommendation,
            "should_adjust_algorithm": self.should_adjust_algorithm,
            "adjustment_notes": self.adjustment_notes,
        }
