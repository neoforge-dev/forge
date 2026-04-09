# Next Task Engine Architecture

**Document Version:** 1.0
**Date:** 2026-02-09
**Architect:** Claude (FORGE Portfolio)
**Implementer:** forge:opencode
**Status:** Design Phase - Ready for Implementation

---

## Executive Summary

The **Next Task Engine** transforms Command Center from a passive task list into an intelligent task orchestrator. By analyzing agent capabilities, historical performance, and codebase patterns, it recommends the optimal task-agent pairing in real-time.

**Current State:** Client-side priority sorting (naive)
**Target State:** Multi-factor intelligent ranking (0.89 predicted accuracy)

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Next Task Engine                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ Task Analysis│    │ Agent Profile│    │   History    │      │
│  │   Module     │───▶│    Module    │◀───│   Module     │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│          │                   │                   │              │
│          └───────────────────┼───────────────────┘              │
│                              ▼                                  │
│                    ┌───────────────────┐                        │
│                    │  Scoring Engine   │                        │
│                    │  (Multi-factor)   │                        │
│                    └───────────────────┘                        │
│                              │                                  │
│                              ▼                                  │
│                    ┌───────────────────┐                        │
│                    │  Rank & Sort     │                        │
│                    └───────────────────┘                        │
│                              │                                  │
│                              ▼                                  │
│                    ┌───────────────────┐                        │
│                    │  Recommendation  │                        │
│                    │     Output       │                        │
│                    └───────────────────┘                        │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Models

### Task Analysis Model

```python
@dataclass
class TaskAnalysis:
    """Enriched task with analysis metadata."""

    # Base Task Fields
    id: str
    subject: str
    description: str
    priority: int  # 1-10, higher = more urgent
    domain: str | None
    project: str | None
    status: str  # "pending", "in_progress", "completed", "blocked"

    # Analysis Fields (Computed)
    task_type: TaskType  # Enum: API, FRONTEND, TESTS, RESEARCH, REFACTOR, etc.
    complexity: ComplexityLevel  # Enum: TRIVIAL, SIMPLE, MODERATE, COMPLEX, EXPERT
    dependencies: list[str]  # Task IDs this depends on
    dependents: list[str]  # Task IDs that depend on this
    blocked: bool  # True if dependencies not satisfied
    estimated_duration: timedelta | None  # Based on similar tasks
    required_skills: set[Skill]  # Skills needed (PYTHON, REACT, TESTING, etc.)

    # Scoring Fields (Computed)
    priority_score: float  # 0-1, normalized priority
    capability_match_score: float | None  # 0-1, per agent
    historical_score: float | None  # 0-1, per agent
    final_score: float | None  # 0-1, weighted combination
    recommended_agent: str | None  # Agent ID for best match
```

### Task Types (Enum)

```python
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
```

### Complexity Level (Enum)

```python
class ComplexityLevel(Enum):
    """Estimated complexity based on description analysis."""

    TRIVIAL = 1  # < 30 minutes, minimal changes
    SIMPLE = 2   # 30m - 2h, straightforward
    MODERATE = 3  # 2h - 1 day, some complexity
    COMPLEX = 4   # 1-3 days, significant complexity
    EXPERT = 5    # 3+ days, requires deep expertise
```

### Agent Profile Model

```python
@dataclass
class AgentProfile:
    """Agent capabilities and preferences."""

    # Identity
    agent_id: str  # e.g., "forge:gemini", "forge:codex"
    name: str
    model: str  # e.g., "gemini-2.0-flash", "claude-opus-4.6"

    # Capabilities (0-1 confidence)
    capabilities: dict[TaskType, float]  # Task type -> proficiency

    # Skills (0-1 confidence)
    skills: dict[Skill, float]  # Skill -> proficiency

    # Preferences
    max_complexity: ComplexityLevel  # Maximum complexity willing to handle
    preferred_domains: list[str]  # Domains of expertise
    avoids: list[TaskType]  # Task types to avoid

    # Current State
    current_task_id: str | None
    status: str  # "idle", "busy", "paused", "error"
    last_activity: datetime

    # Performance Metrics
    total_tasks_completed: int
    success_rate: float  # 0-1
    avg_task_duration: timedelta
    avg_quality_score: float  # 0-1, from code review/rework rate

    # Historical Performance (by TaskType)
    task_type_history: dict[TaskType, TaskTypeHistory]
```

### Task Type History Model

```python
@dataclass
class TaskTypeHistory:
    """Historical performance for specific task type."""

    task_type: TaskType
    tasks_completed: int
    tasks_failed: int
    success_rate: float  # 0-1
    avg_duration: timedelta
    avg_quality_score: float  # 0-1
    last_completed: datetime
```

---

## Scoring Algorithm

### Multi-Factor Scoring Formula

```python
def calculate_task_score(
    task: TaskAnalysis,
    agent: AgentProfile,
    time_decay: float = 0.1  # Weight for recency
) -> float:
    """
    Calculate score for task-agent pairing.

    Returns: float in range [0, 1]
    """

    # Factor 1: Priority Score (Weight: 0.4)
    priority_score = task.priority_score  # Normalized 0-1

    # Factor 2: Capability Match (Weight: 0.4)
    capability_score = calculate_capability_match(task, agent)

    # Factor 3: Historical Performance (Weight: 0.2)
    historical_score = calculate_historical_score(task, agent)

    # Weighted Combination
    final_score = (
        0.4 * priority_score +
        0.4 * capability_score +
        0.2 * historical_score
    )

    # Apply Penalties
    if task.blocked:
        final_score *= 0.1  # Heavy penalty for blocked tasks

    if task.complexity.value > agent.max_complexity.value:
        final_score *= 0.3  # Penalty for exceeding agent capability

    if agent.status != "idle":
        final_score *= 0.5  # Penalty for busy agents

    # Time Decay (boost recent successful completions)
    if task.task_type in agent.task_type_history:
        history = agent.task_type_history[task.task_type]
        days_since_last = (datetime.now() - history.last_completed).days
        recency_boost = exp(-time_decay * days_since_last)
        final_score *= (1 + 0.1 * recency_boost)

    return max(0, min(1, final_score))  # Clamp to [0, 1]
```

### Capability Match Calculation

```python
def calculate_capability_match(
    task: TaskAnalysis,
    agent: AgentProfile
) -> float:
    """
    Calculate how well agent capabilities match task requirements.

    Returns: float in range [0, 1]
    """

    # Direct Task Type Match
    task_type_score = agent.capabilities.get(task.task_type, 0.0)

    # Skills Match
    if task.required_skills:
        skill_scores = [
            agent.skills.get(skill, 0.0)
            for skill in task.required_skills
        ]
        skills_score = sum(skill_scores) / len(skill_scores)
    else:
        skills_score = 0.5  # Neutral if no skills specified

    # Domain Preference
    domain_score = 0.5  # Base score
    if task.domain and task.domain in agent.preferred_domains:
        domain_score = 1.0

    # Task Type Avoidance
    if task.task_type in agent.avoids:
        task_type_score *= 0.2  # Heavy penalty for avoided types

    # Combine (70% task type, 20% skills, 10% domain)
    capability_score = (
        0.7 * task_type_score +
        0.2 * skills_score +
        0.1 * domain_score
    )

    return capability_score
```

### Historical Performance Calculation

```python
def calculate_historical_score(
    task: TaskAnalysis,
    agent: AgentProfile
) -> float:
    """
    Calculate score based on agent's historical performance.

    Returns: float in range [0, 1]
    """

    # Overall Success Rate (40% weight)
    success_rate = agent.success_rate

    # Task-Type Specific Performance (40% weight)
    if task.task_type in agent.task_type_history:
        history = agent.task_type_history[task.task_type]
        type_success_rate = history.success_rate
        type_quality = history.avg_quality_score
        type_performance = (type_success_rate + type_quality) / 2
    else:
        # Cold start penalty for new task types
        type_performance = 0.3

    # Recent Performance Trend (20% weight)
    # Calculate trend from last 5 completed tasks
    recent_scores = get_recent_performance_scores(agent.agent_id, n=5)
    if recent_scores:
        trend = sum(recent_scores) / len(recent_scores)
    else:
        trend = 0.5  # Neutral for no history

    # Combine
    historical_score = (
        0.4 * success_rate +
        0.4 * type_performance +
        0.2 * trend
    )

    return historical_score
```

---

## Task Type Detection Algorithm

### Natural Language Classification

```python
def detect_task_type(task: Task) -> TaskType:
    """
    Detect task type from subject and description using NLP.

    Uses keyword matching, pattern recognition, and ML classification.
    """

    text = f"{task.subject} {task.description}".lower()

    # Keyword-based classification (fast path)
    keyword_rules = {
        # Backend APIs
        TaskType.API_SIMPLE: ["get", "list", "fetch", "basic post", "endpoint"],
        TaskType.API_STATEFUL: ["create", "update", "delete", "state", "database", "migration"],
        TaskType.API_BATCH: ["batch", "bulk", "import", "export", "multi"],
        TaskType.API_EXTERNAL: ["webhook", "external", "api call", "integration"],

        # Frontend
        TaskType.FRONTEND_COMPONENT: ["component", "ui element", "button", "form", "card"],
        TaskType.FRONTEND_PAGE: ["page", "route", "view", "screen"],
        TaskType.FRONTEND_STYLES: ["style", "css", "design", "theme", "responsive"],
        TaskType.FRONTEND_STATE: ["state", "redux", "context", "store", "hook"],

        # Testing
        TaskType.TEST_UNIT: ["unit test", "test function", "mock"],
        TaskType.TEST_INTEGRATION: ["integration test", "api test", "endpoint test"],
        TaskType.TEST_E2E: ["e2e", "end-to-end", "playwright", "cypress", "user flow"],

        # Infrastructure
        TaskType.INFRA_DEPLOY: ["deploy", "railway", "vercel", "production"],
        TaskType.INFRA_CI_CD: ["github action", "workflow", "ci cd", "pipeline"],
        TaskType.INFRA_DOCKER: ["docker", "container", "dockerfile"],

        # Documentation
        TaskType.DOCS_API: ["api docs", "endpoint documentation", "openapi"],
        TaskType.DOCS_USER: ["user guide", "documentation", "readme"],
        TaskType.DOCS_ARCHITECTURE: ["architecture", "design doc", "technical spec"],

        # Research
        TaskType.RESEARCH_TECHNICAL: ["research", "investigate", "explore", "analyze"],
        TaskType.RESEARCH_MARKET: ["market", "competitive", "analysis"],

        # Refactoring
        TaskType.REFACTOR_CLEANUP: ["cleanup", "refactor", "organize"],
        TaskType.REFACTOR_OPTIMIZE: ["optimize", "performance", "speed"],
        TaskType.REFACTOR_MIGRATE: ["migrate", "upgrade", "update"],
    }

    # Check keyword matches
    best_match = None
    best_score = 0

    for task_type, keywords in keyword_rules.items():
        score = sum(1 for keyword in keywords if keyword in text)
        if score > best_score:
            best_score = score
            best_match = task_type

    if best_match and best_score > 0:
        return best_match

    # Fallback: Use ML classifier (trained from historical tasks)
    return classify_task_ml(task)
```

### Complexity Estimation

```python
def estimate_complexity(task: Task, codebase_context: CodebaseContext) -> ComplexityLevel:
    """
    Estimate task complexity based on multiple factors.
    """

    text = f"{task.subject} {task.description}".lower()

    complexity_score = 0  # Start at baseline

    # Factor 1: Description Length
    word_count = len(text.split())
    if word_count > 100:
        complexity_score += 1
    elif word_count > 50:
        complexity_score += 0.5

    # Factor 2: Technical Keywords
    complex_keywords = [
        "architecture", "migration", "integration", "performance",
        "security", "authentication", "database", "distributed",
        "refactor", "optimize", "scalable"
    ]
    complexity_score += sum(1 for kw in complex_keywords if kw in text) * 0.5

    # Factor 3: Scope Indicators
    if "multiple" in text or "several" in text or "batch" in text:
        complexity_score += 1

    if "end-to-end" in text or "full" in text or "complete" in text:
        complexity_score += 1

    # Factor 4: Codebase Context (from Code Atlas)
    if codebase_context:
        # Check if task touches multiple files
        if codebase_context.affected_file_count > 5:
            complexity_score += 1
        elif codebase_context.affected_file_count > 2:
            complexity_score += 0.5

        # Check dependency depth
        if codebase_context.max_dependency_depth > 3:
            complexity_score += 1
        elif codebase_context.max_dependency_depth > 1:
            complexity_score += 0.5

        # Check if touching critical systems
        if codebase_context.touches_critical_system:
            complexity_score += 1

    # Map to ComplexityLevel
    if complexity_score < 1:
        return ComplexityLevel.TRIVIAL
    elif complexity_score < 2:
        return ComplexityLevel.SIMPLE
    elif complexity_score < 3.5:
        return ComplexityLevel.MODERATE
    elif complexity_score < 5:
        return ComplexityLevel.COMPLEX
    else:
        return ComplexityLevel.EXPERT
```

---

## Code Atlas Integration

### Leveraging Code Atlas Patterns

Code Atlas provides deep codebase analysis that enhances task intelligence:

```python
@dataclass
class CodebaseContext:
    """Context from Code Atlas analysis."""

    # File Impact Analysis
    affected_files: list[str]  # Files likely to be modified
    affected_file_count: int
    affected_modules: list[str]  # High-level modules affected

    # Dependency Analysis
    max_dependency_depth: int  # Deepest dependency chain
    circular_dependencies: bool  # Potential circular deps detected
    critical_path: bool  # Task is on critical path

    # System Impact
    touches_critical_system: bool  # Auth, payments, data storage
    breaking_change_risk: float  # 0-1, risk of breaking changes

    # Test Coverage
    existing_test_coverage: float  # 0-1, current coverage for affected code
    test_gap_score: float  # 0-1, how much tests are needed

    # Complexity Metrics (from Code Atlas)
    cyclomatic_complexity: float | None  # Average complexity of affected code
    maintainability_index: float | None  # Maintainability score

    # Domain Knowledge
    domain_patterns: list[str]  # Patterns detected in codebase
    anti_patterns: list[str]  # Anti-patterns detected
    suggested_improvements: list[str]
```

### Integration Points

1. **Task Creation:** When tasks are created, run Code Atlas analysis on affected files
2. **Dependency Detection:** Use Code Atlas to find dependencies between tasks
3. **Complexity Estimation:** Use Code Atlas metrics for better complexity scoring
4. **Test Recommendations:** Suggest test tasks based on coverage gaps

```python
def enrich_with_code_atlas(
    task: Task,
    code_atlas_client: CodeAtlasClient
) -> TaskAnalysis:
    """
    Enrich task with Code Atlas analysis.
    """

    # Identify affected files from task description
    affected_files = code_atlas_client.identify_files(
        query=task.description,
        domain=task.domain,
        project=task.project
    )

    # Analyze dependencies
    deps = code_atlas_client.analyze_dependencies(affected_files)

    # Calculate complexity metrics
    complexity_metrics = code_atlas_client.get_complexity_metrics(affected_files)

    # Detect patterns and anti-patterns
    patterns = code_atlas_client.detect_patterns(affected_files)

    # Build CodebaseContext
    context = CodebaseContext(
        affected_files=affected_files,
        affected_file_count=len(affected_files),
        affected_modules=deps.modules,
        max_dependency_depth=deps.max_depth,
        circular_dependencies=deps.has_circular,
        critical_path=deps.is_critical,
        touches_critical_system=any(f in CRITICAL_SYSTEMS for f in affected_files),
        breaking_change_risk=deps.breaking_risk,
        existing_test_coverage=complexity_metrics.coverage,
        test_gap_score=1 - complexity_metrics.coverage,
        cyclomatic_complexity=complexity_metrics.cyclomatic,
        maintainability_index=complexity_metrics.maintainability,
        domain_patterns=patterns.good,
        anti_patterns=patterns.bad,
        suggested_improvements=patterns.suggestions
    )

    # Create enriched TaskAnalysis
    return TaskAnalysis(
        # ... base task fields ...
        complexity=estimate_complexity(task, context),
        dependencies=deps.task_dependencies,
        dependents=deps.task_dependents,
        blocked=len(deps.task_dependencies) > 0,
        required_skills=extract_required_skills(task, context),
        codebase_context=context
    )
```

---

## API Specification

### GET /api/tasks/recommended

**Endpoint:** `GET /api/tasks/recommended`

**Description:** Get intelligent task recommendations for agents.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | string | No | Filter recommendations for specific agent |
| `limit` | integer | No | Max recommendations to return (default: 10) |
| `include_blocked` | boolean | No | Include blocked tasks (default: false) |
| `min_priority` | integer | No | Minimum priority threshold (1-10) |
| `task_type` | string | No | Filter by task type |
| `domain` | string | No | Filter by domain |

**Response:**

```json
{
  "recommendations": [
    {
      "task": {
        "id": "task-123",
        "subject": "Add user authentication API endpoint",
        "description": "Implement JWT-based authentication...",
        "priority": 8,
        "domain": "brandfocus-ai",
        "project": "voice-coach",
        "status": "pending"
      },
      "analysis": {
        "task_type": "api:stateful",
        "complexity": "moderate",
        "dependencies": [],
        "blocked": false,
        "estimated_duration": "2h 30m",
        "required_skills": ["PYTHON", "FASTAPI", "JWT", "AUTHENTICATION"]
      },
      "scoring": {
        "priority_score": 0.8,
        "capability_matches": [
          {
            "agent_id": "forge:opencode",
            "score": 0.92,
            "capability_score": 0.95,
            "historical_score": 0.88,
            "breakdown": {
              "task_type_match": 0.95,
              "skills_match": 0.90,
              "domain_preference": 1.0
            }
          },
          {
            "agent_id": "forge:codex",
            "score": 0.75,
            "capability_score": 0.80,
            "historical_score": 0.65,
            "breakdown": {
              "task_type_match": 0.85,
              "skills_match": 0.75,
              "domain_preference": 0.5
            }
          }
        ],
        "recommended_agent": "forge:opencode",
        "recommendation_confidence": 0.92
      },
      "codebase_context": {
        "affected_files": [
          "brandfocus-ai/voice-coach/backend/app/api/auth.py",
          "brandfocus-ai/voice-coach/backend/app/security/jwt.py"
        ],
        "affected_modules": ["authentication", "api"],
        "touches_critical_system": true,
        "existing_test_coverage": 0.65,
        "test_gap_score": 0.35,
        "breaking_change_risk": 0.2
      }
    }
  ],
  "meta": {
    "total_tasks": 42,
    "recommended_count": 10,
    "algorithm_version": "1.0",
    "generated_at": "2026-02-09T19:30:00Z"
  }
}
```

**Response Fields:**

- **task:** Base task information
- **analysis:** Enriched analysis (task type, complexity, dependencies)
- **scoring:** Score breakdown per agent, recommendation
- **codebase_context:** Code Atlas integration data
- **meta:** Metadata about recommendations

---

## Implementation Roadmap

### Phase 1: Foundation (Week 1)

**Tasks:**
1. Create data models (TaskAnalysis, AgentProfile, etc.)
2. Implement task type detection algorithm
3. Implement complexity estimation
4. Create basic agent capability profiles

**Deliverables:**
- `harness/forge_harness/models/task_analysis.py`
- `harness/forge_harness/models/agent_profile.py`
- `harness/forge_harness/analytics/task_classifier.py`
- `harness/forge_harness/analytics/complexity_estimator.py`

### Phase 2: Scoring Engine (Week 2)

**Tasks:**
1. Implement multi-factor scoring algorithm
2. Implement capability match calculation
3. Implement historical performance tracking
4. Create scoring integration tests

**Deliverables:**
- `harness/forge_harness/analytics/scoring_engine.py`
- `harness/forge_harness/analytics/capability_matcher.py`
- `harness/forge_harness/analytics/historical_tracker.py`
- `tests/analytics/test_scoring_engine.py`

### Phase 3: Code Atlas Integration (Week 3)

**Tasks:**
1. Create Code Atlas client wrapper
2. Implement dependency detection
3. Implement complexity metrics integration
4. Add test gap detection

**Deliverables:**
- `harness/forge_harness/integrations/code_atlas_client.py`
- `harness/forge_harness/analytics/dependency_analyzer.py`
- `tests/integrations/test_code_atlas.py`

### Phase 4: API & Frontend (Week 4)

**Tasks:**
1. Implement `/api/tasks/recommended` endpoint
2. Add real-time recommendation updates
3. Update Command Center UI with recommendations
4. Add recommendation feedback loop

**Deliverables:**
- `harness/forge_harness/api/recommendations.py`
- `harness/command_center/src/hooks/useTaskRecommendations.ts`
- `harness/command_center/src/components/TaskRecommendations.tsx`

### Phase 5: Machine Learning Enhancement (Week 5-6) (Optional)

**Tasks:**
1. Train ML model on historical task completions
2. Implement A/B testing for algorithm parameters
3. Add recommendation quality metrics
4. Implement continuous learning

**Deliverables:**
- ML model for task classification
- A/B testing framework
- Recommendation analytics dashboard

---

## Success Metrics

### Accuracy Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Recommendation Acceptance Rate | >80% | % of recommended tasks accepted by agents |
| Task Completion Rate | >90% | % of recommended tasks completed successfully |
| Agent Satisfaction | >4.5/5 | Agent feedback on recommendations |
| Time-to-Complete Improvement | >20% | Reduction in task completion time |

### System Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Recommendation Latency | <500ms | Time from request to response |
| Analysis Accuracy | >85% | Task type classification accuracy |
| Dependency Detection | >90% | % of dependencies correctly identified |
| Complexity Prediction | >75% | Within 1 complexity level |

---

## Agent Capability Profiles (Initial)

### forge:opencode
```python
AgentProfile(
    agent_id="forge:opencode",
    name="OpenCode",
    model="opencode-latest",

    capabilities={
        TaskType.API_SIMPLE: 0.95,
        TaskType.API_STATEFUL: 0.90,
        TaskType.API_BATCH: 0.85,
        TaskType.API_EXTERNAL: 0.80,
        TaskType.TEST_UNIT: 0.75,
        TaskType.TEST_INTEGRATION: 0.70,
    },

    skills={
        Skill.PYTHON: 0.95,
        Skill.FASTAPI: 0.90,
        Skill.SQLALCHEMY: 0.85,
        Skill.PYTEST: 0.80,
        Skill.AUTHENTICATION: 0.85,
    },

    max_complexity=ComplexityLevel.EXPERT,
    preferred_domains=["codeswiftr-com", "brandfocus-ai"],
    avoids=[TaskType.FRONTEND_COMPONENT, TaskType.RESEARCH_MARKET],
)
```

### forge:codex
```python
AgentProfile(
    agent_id="forge:codex",
    name="Codex",
    model="codex-latest",

    capabilities={
        TaskType.FRONTEND_COMPONENT: 0.90,
        TaskType.FRONTEND_PAGE: 0.85,
        TaskType.FRONTEND_STYLES: 0.80,
        TaskType.API_SIMPLE: 0.75,
        TaskType.TEST_UNIT: 0.70,
    },

    skills={
        Skill.TYPESCRIPT: 0.90,
        Skill.REACT: 0.85,
        Skill.CSS: 0.80,
        Skill.JAVASCRIPT: 0.85,
    },

    max_complexity=ComplexityLevel.COMPLEX,
    preferred_domains=["brandfocus-ai", "thebrightharbor-com"],
    avoids=[TaskType.INFRA_DEPLOY, TaskType.RESEARCH_TECHNICAL],
)
```

### forge:gemini
```python
AgentProfile(
    agent_id="forge:gemini",
    name="Gemini",
    model="gemini-2.0-flash",

    capabilities={
        TaskType.RESEARCH_TECHNICAL: 0.95,
        TaskType.RESEARCH_MARKET: 0.90,
        TaskType.DOCS_ARCHITECTURE: 0.85,
        TaskType.DOCS_API: 0.80,
        TaskType.REFACTOR_CLEANUP: 0.75,
    },

    skills={
        Skill.RESEARCH: 0.95,
        Skill.DOCUMENTATION: 0.90,
        Skill.ANALYSIS: 0.85,
        Skill.PYTHON: 0.70,
    },

    max_complexity=ComplexityLevel.MODERATE,
    preferred_domains=["all"],
    avoids=[TaskType.INFRA_CI_CD],
)
```

---

## Feedback Loop & Continuous Learning

### Recommendation Feedback

```python
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
    quality_score: float | None  # 0-1, from code review
    feedback: str | None  # Agent feedback

    # Learning signals
    was_good_recommendation: bool  # Human/expert judgment
    should_adjust_algorithm: bool
    adjustment_notes: str | None
```

### Algorithm Tuning

```python
def update_algorithm_weights(
    feedback_history: list[RecommendationFeedback]
) -> AlgorithmWeights:
    """
    Update scoring algorithm weights based on feedback.

    Uses simple gradient descent to optimize for:
    - Recommendation acceptance rate
    - Task completion rate
    - Time-to-complete reduction
    """

    # Calculate current performance metrics
    acceptance_rate = calculate_acceptance_rate(feedback_history)
    completion_rate = calculate_completion_rate(feedback_history)
    time_reduction = calculate_time_reduction(feedback_history)

    # Target values
    target_acceptance = 0.8
    target_completion = 0.9
    target_time_reduction = 0.2

    # Calculate errors
    acceptance_error = target_acceptance - acceptance_rate
    completion_error = target_completion - completion_rate
    time_error = target_time_reduction - time_reduction

    # Adjust weights (gradient descent)
    learning_rate = 0.01

    new_priority_weight = max(0.1, min(0.7,
        current_weights.priority_weight + learning_rate * acceptance_error
    ))

    new_capability_weight = max(0.1, min(0.7,
        current_weights.capability_weight + learning_rate * completion_error
    ))

    new_historical_weight = max(0.1, min(0.7,
        current_weights.historical_weight + learning_rate * time_error
    ))

    # Normalize to sum to 1.0
    total = new_priority_weight + new_capability_weight + new_historical_weight
    new_priority_weight /= total
    new_capability_weight /= total
    new_historical_weight /= total

    return AlgorithmWeights(
        priority_weight=new_priority_weight,
        capability_weight=new_capability_weight,
        historical_weight=new_historical_weight
    )
```

---

## Security & Safety Considerations

### Task Sanitization

```python
def sanitize_task_input(task: Task) -> Task:
    """
    Sanitize task input to prevent injection attacks.
    """

    # Remove potentially dangerous content
    task.subject = sanitize_html(task.subject)
    task.description = sanitize_html(task.description)

    # Check for suspicious patterns
    suspicious_patterns = [
        r"__import__",
        r"eval\(",
        r"exec\(",
        r"os\.system",
        r"subprocess\.call",
    ]

    for pattern in suspicious_patterns:
        if re.search(pattern, task.description, re.IGNORECASE):
            logger.warning(f"Suspicious pattern detected in task {task.id}")
            # Flag for review

    return task
```

### Agent Capability Limits

```python
def enforce_agent_limits(
    task: TaskAnalysis,
    agent: AgentProfile
) -> bool:
    """
    Enforce agent capability limits to prevent overloading.
    """

    # Check complexity limit
    if task.complexity.value > agent.max_complexity.value:
        logger.warning(
            f"Task {task.id} exceeds {agent.agent_id} capability limit"
        )
        return False

    # Check if agent is avoiding this task type
    if task.task_type in agent.avoids:
        logger.info(
            f"Task {task.id} type is avoided by {agent.agent_id}"
        )
        return False

    # Check current workload
    if agent.status == "busy" and task.priority < 8:
        logger.info(
            f"Task {task.id} deferred due to {agent.agent_id} workload"
        )
        return False

    return True
```

---

## Testing Strategy

### Unit Tests

```python
def test_scoring_algorithm():
    """Test multi-factor scoring algorithm."""

    # Create test task and agent
    task = TaskAnalysis(
        id="test-1",
        subject="Add user authentication",
        priority=8,
        task_type=TaskType.API_STATEFUL,
        complexity=ComplexityLevel.MODERATE,
        priority_score=0.8,
        blocked=False
    )

    agent = AgentProfile(
        agent_id="forge:opencode",
        capabilities={TaskType.API_STATEFUL: 0.9},
        success_rate=0.85,
        status="idle"
    )

    # Calculate score
    score = calculate_task_score(task, agent)

    # Assertions
    assert 0 <= score <= 1
    assert score > 0.7  # Should be high match
```

### Integration Tests

```python
async def test_recommendation_api():
    """Test /api/tasks/recommended endpoint."""

    # Setup
    client = AsyncClient(app=app, base_url="http://test")

    # Create test tasks
    await create_test_tasks(count=10)

    # Get recommendations
    response = await client.get("/api/tasks/recommended?limit=5")

    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert len(data["recommendations"]) == 5
    assert "scoring" in data["recommendations"][0]
    assert "recommended_agent" in data["recommendations"][0]["scoring"]
```

---

## Appendix: Example Scenarios

### Scenario 1: API Endpoint Task

**Task:** "Add user authentication endpoint with JWT tokens"

**Analysis:**
- Task Type: `API_STATEFUL`
- Complexity: `MODERATE`
- Required Skills: PYTHON, FASTAPI, JWT, AUTHENTICATION
- Dependencies: None

**Recommendations:**
1. **forge:opencode** (Score: 0.92)
   - Capability Match: 0.95 (excellent at API endpoints)
   - Historical: 0.88 (high success rate on auth tasks)
2. **forge:codex** (Score: 0.65)
   - Capability Match: 0.60 (not specialized in backend)

**Recommended Agent:** forge:opencode

### Scenario 2: Frontend Component Task

**Task:** "Create user profile card component with avatar"

**Analysis:**
- Task Type: `FRONTEND_COMPONENT`
- Complexity: `SIMPLE`
- Required Skills: TYPESCRIPT, REACT, CSS
- Dependencies: None

**Recommendations:**
1. **forge:codex** (Score: 0.88)
   - Capability Match: 0.90 (excellent at React components)
   - Historical: 0.82 (strong frontend track record)
2. **forge:opencode** (Score: 0.45)
   - Capability Match: 0.40 (not specialized in frontend)

**Recommended Agent:** forge:codex

### Scenario 3: Research Task

**Task:** "Research best practices for real-time data synchronization"

**Analysis:**
- Task Type: `RESEARCH_TECHNICAL`
- Complexity: `MODERATE`
- Required Skills: RESEARCH, ANALYSIS, DOCUMENTATION
- Dependencies: None

**Recommendations:**
1. **forge:gemini** (Score: 0.94)
   - Capability Match: 0.95 (specialized in research)
   - Historical: 0.92 (excellent research track record)
2. **forge:opencode** (Score: 0.55)
   - Capability Match: 0.50 (not specialized in research)

**Recommended Agent:** forge:gemini

---

**Document Status:** Complete - Ready for Implementation by forge:opencode
**Next Steps:** Review architecture, create implementation tasks, begin Phase 1
