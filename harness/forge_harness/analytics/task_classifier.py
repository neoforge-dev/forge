"""
Task Classifier for Next Task Recommendation Engine.

Provides task type detection, complexity estimation, and skill extraction.
"""


from ..models import (
    CodebaseContext,
    ComplexityLevel,
    Skill,
    TaskAnalysis,
    TaskType,
)

# Keyword-based classification rules
KEYWORD_RULES: dict[TaskType, list[str]] = {
    # Backend APIs
    TaskType.API_SIMPLE: ["get", "list", "fetch", "basic post", "endpoint", "retrieve"],
    TaskType.API_STATEFUL: [
        "create", "update", "delete", "state", "database", "migration",
        "save", "persist", "transaction"
    ],
    TaskType.API_BATCH: ["batch", "bulk", "import", "export", "multi", "bulk insert", "bulk update"],
    TaskType.API_EXTERNAL: ["webhook", "external", "api call", "integration", "third-party", "oauth"],

    # Frontend
    TaskType.FRONTEND_COMPONENT: [
        "component", "ui element", "button", "form", "card", "modal",
        "dropdown", "input", "checkbox", "radio", "select", "table"
    ],
    TaskType.FRONTEND_PAGE: ["page", "route", "view", "screen", "layout", "dashboard"],
    TaskType.FRONTEND_STYLES: ["style", "css", "design", "theme", "responsive", "typography", "color"],
    TaskType.FRONTEND_STATE: ["state", "redux", "context", "store", "hook", "useeffect", "usestate"],

    # Testing
    TaskType.TEST_UNIT: ["unit test", "test function", "mock", "pytest", "unittest"],
    TaskType.TEST_INTEGRATION: ["integration test", "api test", "endpoint test", "service test"],
    TaskType.TEST_E2E: ["e2e", "end-to-end", "playwright", "cypress", "user flow", "browser test"],

    # Infrastructure
    TaskType.INFRA_DEPLOY: ["deploy", "railway", "vercel", "production", "staging", "release"],
    TaskType.INFRA_CI_CD: ["github action", "workflow", "ci cd", "pipeline", "github workflow", "action"],
    TaskType.INFRA_DOCKER: ["docker", "container", "dockerfile", "docker-compose", "containerize"],

    # Documentation
    TaskType.DOCS_API: ["api docs", "endpoint documentation", "openapi", "swagger", "api reference"],
    TaskType.DOCS_USER: ["user guide", "documentation", "readme", "manual", "tutorial"],
    TaskType.DOCS_ARCHITECTURE: ["architecture", "design doc", "technical spec", "adr"],

    # Research
    TaskType.RESEARCH_TECHNICAL: ["research", "investigate", "explore", "analyze", "evaluate"],
    TaskType.RESEARCH_MARKET: ["market", "competitive", "analysis", "competitor", "benchmark"],

    # Refactoring
    TaskType.REFACTOR_CLEANUP: ["cleanup", "refactor", "organize", "cleanup code", "simplify"],
    TaskType.REFACTOR_OPTIMIZE: ["optimize", "performance", "speed", "performance", "efficient"],
    TaskType.REFACTOR_MIGRATE: ["migrate", "upgrade", "update", "migration", "convert"],
}

# Complexity keywords
COMPLEXITY_KEYWORDS: list[str] = [
    "architecture", "migration", "integration", "performance",
    "security", "authentication", "database", "distributed",
    "refactor", "optimize", "scalable", "enterprise", "critical",
    "breaking", "legacy", "monolith", "microservice"
]

# Skill keywords mapping
SKILL_KEYWORDS: dict[Skill, list[str]] = {
    Skill.PYTHON: ["python", "py"],
    Skill.TYPESCRIPT: ["typescript", "ts"],
    Skill.JAVASCRIPT: ["javascript", "js"],
    Skill.FASTAPI: ["fastapi", "fast api"],
    Skill.DJANGO: ["django"],
    Skill.FLASK: ["flask"],
    Skill.EXPRESS: ["express", "expressjs"],
    Skill.REACT: ["react", "reactjs"],
    Skill.VUE: ["vue", "vuejs"],
    Skill.SQL: ["sql"],
    Skill.POSTGRESQL: ["postgresql", "postgres"],
    Skill.MONGODB: ["mongodb", "mongo"],
    Skill.REDIS: ["redis"],
    Skill.PYTEST: ["pytest", "py.test"],
    Skill.DOCKER: ["docker", "container"],
    Skill.KUBERNETES: ["kubernetes", "k8s"],
    Skill.AUTHENTICATION: ["authentication", "auth", "jwt", "oauth", "login"],
    Skill.GRAPHQL: ["graphql", "gql"],
    Skill.RESEARCH: ["research", "investigate", "analyze"],
    Skill.DOCUMENTATION: ["documentation", "docs", "readme"],
}


def detect_task_type(subject: str, description: str) -> TaskType:
    """
    Detect task type from subject and description using keyword matching.

    Args:
        subject: Task subject/title
        description: Task description

    Returns:
        Detected TaskType
    """
    text = f"{subject} {description}".lower()

    # Check keyword matches
    best_match: TaskType | None = None
    best_score = 0

    for task_type, keywords in KEYWORD_RULES.items():
        score = sum(1 for keyword in keywords if keyword in text)
        if score > best_score:
            best_score = score
            best_match = task_type

    if best_match and best_score > 0:
        return best_match

    # Fallback to GENERAL if no matches
    return TaskType.GENERAL


def estimate_complexity(
    subject: str,
    description: str,
    codebase_context: CodebaseContext | None = None,
) -> ComplexityLevel:
    """
    Estimate task complexity based on multiple factors.

    Args:
        subject: Task subject/title
        description: Task description
        codebase_context: Optional CodebaseContext from Code Atlas

    Returns:
        Estimated ComplexityLevel
    """
    text = f"{subject} {description}".lower()
    complexity_score = 0.0

    # Factor 1: Description Length
    word_count = len(text.split())
    if word_count > 100:
        complexity_score += 1.0
    elif word_count > 50:
        complexity_score += 0.5

    # Factor 2: Technical Keywords
    complexity_score += sum(0.5 for kw in COMPLEXITY_KEYWORDS if kw in text)

    # Factor 3: Scope Indicators
    if "multiple" in text or "several" in text or "batch" in text:
        complexity_score += 1.0

    if "end-to-end" in text or "full" in text or "complete" in text:
        complexity_score += 1.0

    if "across" in text or "entire" in text or "system-wide" in text:
        complexity_score += 1.5

    # Factor 4: Codebase Context (from Code Atlas)
    if codebase_context:
        # Check if task touches multiple files
        if codebase_context.affected_file_count > 5:
            complexity_score += 1.0
        elif codebase_context.affected_file_count > 2:
            complexity_score += 0.5

        # Check dependency depth
        if codebase_context.max_dependency_depth > 3:
            complexity_score += 1.0
        elif codebase_context.max_dependency_depth > 1:
            complexity_score += 0.5

        # Check if touching critical systems
        if codebase_context.touches_critical_system:
            complexity_score += 1.0

        # Check breaking change risk
        if codebase_context.breaking_change_risk > 0.5:
            complexity_score += 0.5

        # Check cyclomatic complexity
        if codebase_context.cyclomatic_complexity:
            if codebase_context.cyclomatic_complexity > 20:
                complexity_score += 1.0
            elif codebase_context.cyclomatic_complexity > 10:
                complexity_score += 0.5

    # Map to ComplexityLevel
    if complexity_score < 1.0:
        return ComplexityLevel.TRIVIAL
    elif complexity_score < 2.0:
        return ComplexityLevel.SIMPLE
    elif complexity_score < 3.5:
        return ComplexityLevel.MODERATE
    elif complexity_score < 5.0:
        return ComplexityLevel.COMPLEX
    else:
        return ComplexityLevel.EXPERT


def extract_required_skills(
    subject: str,
    description: str,
) -> set[Skill]:
    """
    Extract required skills from task text.

    Args:
        subject: Task subject/title
        description: Task description

    Returns:
        Set of required Skills
    """
    text = f"{subject} {description}".lower()
    skills: set[Skill] = set()

    for skill, keywords in SKILL_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                skills.add(skill)
                break

    return skills


def normalize_priority(priority: int) -> float:
    """
    Normalize priority to 0-1 scale.

    Args:
        priority: Priority value (1-10)

    Returns:
        Normalized priority (0-1)
    """
    return max(0, min(1, priority / 10))


class TaskClassifier:
    """
    Classifier for task analysis and type detection.
    """

    def __init__(self):
        """Initialize the task classifier."""
        pass

    def analyze_task(
        self,
        task_id: str,
        subject: str,
        description: str,
        priority: int,
        domain: str | None = None,
        project: str | None = None,
        status: str = "pending",
        codebase_context: CodebaseContext | None = None,
    ) -> TaskAnalysis:
        """
        Analyze a task and return enriched TaskAnalysis.

        Args:
            task_id: Unique task identifier
            subject: Task subject/title
            description: Task description
            priority: Task priority (1-10)
            domain: Optional domain name
            project: Optional project name
            status: Task status
            codebase_context: Optional CodebaseContext

        Returns:
            Enriched TaskAnalysis
        """
        # Detect task type
        task_type = detect_task_type(subject, description)

        # Estimate complexity
        complexity = estimate_complexity(subject, description, codebase_context)

        # Extract required skills
        required_skills = extract_required_skills(subject, description)

        # Normalize priority
        priority_score = normalize_priority(priority)

        return TaskAnalysis(
            id=task_id,
            subject=subject,
            description=description,
            priority=priority,
            domain=domain,
            project=project,
            status=status,
            task_type=task_type,
            complexity=complexity,
            dependencies=[],
            dependents=[],
            blocked=False,
            estimated_duration=None,
            required_skills=required_skills,
            priority_score=priority_score,
            codebase_context=codebase_context,
        )
