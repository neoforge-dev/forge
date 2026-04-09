# Multi-Agent Orchestration Architecture

_Last updated: 2026-01-18_

FORGE multi-agent system enabling 5-10 concurrent specialized agents with shared memory, coordinated work, and human oversight.

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent memory | Long-term + Short-term | Persistent learnings, fresh task context |
| Authority model | CTO > PM > Oracle > Engineers, human notified on conflicts | Clear hierarchy, human stays informed |
| Prompt ownership | Global within FORGE monorepo | Knowledge sharing, consistent patterns |
| RL scope | Start with prompt optimization | Measurable, low risk, compound over time |
| Agent scale | 5-10 concurrent | Realistic for single human operator |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              HUMAN LAYER                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │ Multi-Agent     │  │ Approval Queue  │  │ Conflict        │         │
│  │ Dashboard       │  │ (Tier-Aware)    │  │ Notifications   │         │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘         │
│           └────────────────────┼────────────────────┘                   │
├────────────────────────────────┼────────────────────────────────────────┤
│                         ORCHESTRATION LAYER                              │
│                                │                                         │
│  ┌─────────────────────────────┴─────────────────────────────┐          │
│  │                      MESSAGE BUS                           │          │
│  │  (Redis Streams: broadcasts, requests, claims, escalations)│          │
│  └─────────────────────────────┬─────────────────────────────┘          │
│                                │                                         │
│  ┌──────────┬──────────┬───────┴───┬──────────┬──────────┐              │
│  │   CTO    │    PM    │  Oracle   │ Engineer │ Engineer │  ...        │
│  │ Agent    │  Agent   │  Agent    │ Agent 1  │ Agent 2  │  (5-10)     │
│  └────┬─────┴────┬─────┴─────┬─────┴────┬─────┴────┬─────┘              │
│       │          │           │          │          │                     │
├───────┴──────────┴───────────┴──────────┴──────────┴────────────────────┤
│                           SHARED STATE LAYER                             │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │ MEMORY STORE │  │ PROMPT       │  │ WORK CLAIMS  │                   │
│  │              │  │ REGISTRY     │  │              │                   │
│  │ Long-term:   │  │              │  │ Locks:       │                   │
│  │ • Patterns   │  │ • Templates  │  │ • Files      │                   │
│  │ • Decisions  │  │ • Versions   │  │ • Modules    │                   │
│  │ • Learnings  │  │ • Outcomes   │  │ • Features   │                   │
│  │              │  │ • A/B Tests  │  │              │                   │
│  │ Short-term:  │  │              │  │ Assignments: │                   │
│  │ • Session    │  │              │  │ • Agent→Work │                   │
│  │ • Task       │  │              │  │ • Priorities │                   │
│  └──────────────┘  └──────────────┘  └──────────────┘                   │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │ DECISION LOG │  │ OUTCOME LOG  │  │ CONFLICT LOG │                   │
│  │ (Append-only)│  │ (For RL)     │  │ (Audit)      │                   │
│  └──────────────┘  └──────────────┘  └──────────────┘                   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Agent Roles

### Role Hierarchy

```
                    HUMAN
                      │
                      ▼
        ┌─────────────────────────┐
        │          CTO            │  Authority: Architecture, Security, Patterns
        │   (Strategic Override)  │  Can override: PM, Oracle, Engineers
        └────────────┬────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
        ▼                         ▼
┌───────────────┐        ┌───────────────┐
│      PM       │        │    Oracle     │  Authority: Coordination, Conflicts
│  (Backlog)    │        │ (Orchestrator)│  Can override: Engineers
└───────┬───────┘        └───────┬───────┘
        │                        │
        └────────────┬───────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   ┌─────────┐  ┌─────────┐  ┌─────────┐
   │Engineer │  │Engineer │  │   QA    │  Authority: Implementation
   │    1    │  │    2    │  │         │  Can override: None
   └─────────┘  └─────────┘  └─────────┘
```

### Role Definitions

| Role | Agent ID | Responsibilities | Authorities | Constraints |
|------|----------|------------------|-------------|-------------|
| **CTO** | `cto-strategic` | Architecture decisions, tech debt, security review, pattern approval | Override any technical decision, approve breaking changes | Cannot override business priorities (PM domain) |
| **PM** | `pm-backlog` | Backlog prioritization, requirements, acceptance criteria, scope decisions | Override feature scope, defer/cut features | Cannot override technical patterns (CTO domain) |
| **Oracle** | `oracle-coordinator` | Work assignment, conflict resolution, cross-agent coordination | Assign work, resolve resource conflicts, escalate | Cannot make business or architecture decisions |
| **Engineer** | `eng-{domain}` | Implementation, tests, documentation | Code changes within assigned scope | Must respect work claims, escalate blockers |
| **QA** | `qa-guardian` | Test coverage, quality gates, regression detection | Block deploys on quality failures | Cannot modify production code |

### Conflict Resolution Matrix

| Conflict Type | First Resolver | Escalation | Human Notified |
|---------------|----------------|------------|----------------|
| Two engineers want same file | Oracle | CTO | No |
| Engineer disagrees with CTO pattern | CTO decides | Human | Yes |
| PM and CTO disagree on scope vs. quality | Human decides | - | Yes |
| Deadlock (no progress 30+ min) | Oracle | Human | Yes |

---

## Memory System

### Long-Term Memory (Persistent)

Stored in `~/.forge/memory/` or database, persists across all sessions.

```python
class LongTermMemory(BaseModel):
    """Persistent memory shared across all agents."""

    # Architectural patterns learned
    patterns: list[PatternRecord]

    # Decisions made with rationale
    decisions: list[DecisionRecord]

    # Cross-project learnings
    learnings: list[LearningRecord]

    # Agent-specific preferences (per role)
    agent_preferences: dict[str, AgentPreferences]

    # Prompt effectiveness data
    prompt_outcomes: list[PromptOutcome]


class PatternRecord(BaseModel):
    """A learned architectural pattern."""

    pattern_id: str
    name: str
    description: str

    # Where it applies
    domains: list[str]  # ["*"] for global
    task_types: list[str]

    # The pattern itself
    template: str  # Code or instruction template
    examples: list[str]
    anti_patterns: list[str]  # What to avoid

    # Provenance
    introduced_by: str  # agent_id or "human"
    introduced_at: datetime
    approved_by: str | None  # CTO or human

    # Effectiveness
    applications: int = 0
    successes: int = 0


class DecisionRecord(BaseModel):
    """A significant decision for audit trail."""

    decision_id: str
    timestamp: datetime

    # Who and what
    made_by: str  # agent_id
    overridden_by: str | None
    decision_type: str  # "architecture", "priority", "scope", etc.

    # Content
    question: str
    decision: str
    rationale: str

    # Context
    domain: str | None
    project: str | None
    related_decisions: list[str]  # decision_ids

    # Outcome (filled later)
    outcome: str | None  # "successful", "reverted", "modified"
    outcome_notes: str | None
```

### Short-Term Memory (Session-Scoped)

Fresh each session, discarded or selectively promoted to long-term.

```python
class ShortTermMemory(BaseModel):
    """Session-scoped memory for a single agent."""

    agent_id: str
    session_id: str
    started_at: datetime

    # Current task context
    current_task: TaskContext | None
    task_history: list[TaskContext]  # This session only

    # Working context
    files_read: list[str]
    files_modified: list[str]
    decisions_made: list[str]  # References to DecisionRecord

    # Communication state
    pending_requests: list[AgentRequest]
    received_messages: list[AgentMessage]

    # Scratchpad (agent's working notes)
    notes: dict[str, Any]


class TaskContext(BaseModel):
    """Context for a single task within a session."""

    task_id: str
    feature_id: str | None

    # What we're doing
    description: str
    acceptance_criteria: list[str]

    # Relevant long-term memory (loaded at task start)
    relevant_patterns: list[str]  # pattern_ids
    relevant_decisions: list[str]  # decision_ids
    similar_past_tasks: list[str]  # For context

    # Progress
    status: str  # "in_progress", "blocked", "complete"
    blockers: list[str]
    progress_notes: list[str]
```

### Memory Promotion Rules

```
SHORT-TERM → LONG-TERM promotion criteria:

1. PATTERN PROMOTION
   - Used successfully 3+ times across sessions
   - Approved by CTO or human
   - No regressions caused

2. DECISION PROMOTION
   - Affects architecture or process
   - Overrides a previous decision
   - Flagged as "significant" by any agent

3. LEARNING PROMOTION
   - Explicit "lesson learned" from failure
   - Cross-project applicability
   - Human-validated insight
```

---

## Message Bus

### Redis Streams Structure

```
STREAMS:
  forge:broadcast          # One-to-all messages
  forge:requests           # Request/response pairs
  forge:claims             # Work claim announcements
  forge:escalations        # Escalation to higher authority
  forge:human              # Messages requiring human attention

CONSUMER GROUPS:
  cto-strategic            # CTO agent
  pm-backlog               # PM agent
  oracle-coordinator       # Oracle agent
  eng-{domain}             # Engineer agents
  qa-guardian              # QA agent
  dashboard                # Human dashboard
```

### Message Types

```python
class BroadcastMessage(BaseModel):
    """One-to-all announcement."""

    message_id: str
    timestamp: datetime
    sender: str  # agent_id

    type: Literal["announcement", "pattern_update", "decision", "status_change"]

    # Content
    title: str
    body: str
    metadata: dict[str, Any]

    # Targeting (empty = all agents)
    target_roles: list[str] = []  # ["engineer", "qa"]


class RequestMessage(BaseModel):
    """Request requiring response from specific agent."""

    request_id: str
    timestamp: datetime
    sender: str
    target: str  # Specific agent_id or role

    type: Literal["question", "approval", "review", "clarification"]

    # Content
    question: str
    context: dict[str, Any]
    options: list[str] | None  # If multiple choice

    # Response tracking
    deadline: datetime | None
    response_id: str | None  # Filled when responded


class ClaimMessage(BaseModel):
    """Work claim to prevent conflicts."""

    claim_id: str
    timestamp: datetime
    agent_id: str

    # What's being claimed
    claim_type: Literal["file", "module", "feature", "domain"]
    resource: str  # Path or identifier

    # Duration
    expires_at: datetime  # Auto-release if not renewed

    # Status
    status: Literal["claimed", "released", "contested"]


class EscalationMessage(BaseModel):
    """Escalation to higher authority."""

    escalation_id: str
    timestamp: datetime
    sender: str

    # Escalation chain
    escalate_to: str  # agent_id or "human"
    reason: str

    # Context
    original_issue: str
    attempts_made: list[str]  # What was tried

    # For human escalations
    tier: str | None  # "watch", "phone", "desktop"
    urgency: str  # "low", "medium", "high", "critical"
```

### Communication Patterns

```python
class AgentCommunicator:
    """Handles all inter-agent communication."""

    def __init__(self, agent_id: str, redis_client: Redis):
        self.agent_id = agent_id
        self.redis = redis_client

    async def broadcast(self, message: BroadcastMessage) -> None:
        """Send message to all agents."""
        await self.redis.xadd(
            "forge:broadcast",
            {"data": message.model_dump_json()}
        )

    async def request(
        self,
        target: str,
        question: str,
        timeout_seconds: float = 300,
    ) -> ResponseMessage | None:
        """Send request and wait for response."""
        request = RequestMessage(
            request_id=generate_id(),
            timestamp=utcnow(),
            sender=self.agent_id,
            target=target,
            type="question",
            question=question,
        )

        await self.redis.xadd(
            "forge:requests",
            {"data": request.model_dump_json()}
        )

        # Wait for response
        return await self._wait_for_response(
            request.request_id,
            timeout_seconds,
        )

    async def claim(
        self,
        resource: str,
        claim_type: str = "file",
        duration_minutes: int = 30,
    ) -> bool:
        """Attempt to claim a resource. Returns True if successful."""
        claim = ClaimMessage(
            claim_id=generate_id(),
            timestamp=utcnow(),
            agent_id=self.agent_id,
            claim_type=claim_type,
            resource=resource,
            expires_at=utcnow() + timedelta(minutes=duration_minutes),
            status="claimed",
        )

        # Atomic claim with SETNX
        key = f"forge:claim:{claim_type}:{resource}"
        success = await self.redis.setnx(key, claim.model_dump_json())

        if success:
            await self.redis.expire(key, duration_minutes * 60)
            await self.redis.xadd(
                "forge:claims",
                {"data": claim.model_dump_json()}
            )

        return success

    async def escalate(
        self,
        to: str,
        reason: str,
        context: dict[str, Any],
        tier: str = "phone",
    ) -> str:
        """Escalate to higher authority."""
        escalation = EscalationMessage(
            escalation_id=generate_id(),
            timestamp=utcnow(),
            sender=self.agent_id,
            escalate_to=to,
            reason=reason,
            original_issue=context.get("issue", ""),
            attempts_made=context.get("attempts", []),
            tier=tier if to == "human" else None,
            urgency=context.get("urgency", "medium"),
        )

        stream = "forge:human" if to == "human" else "forge:escalations"
        await self.redis.xadd(stream, {"data": escalation.model_dump_json()})

        return escalation.escalation_id
```

---

## Prompt Registry

### Storage Structure

```
harness/prompts/
├── registry.json           # Prompt index with metadata
├── system/                 # System prompts by role
│   ├── cto.md
│   ├── pm.md
│   ├── oracle.md
│   ├── engineer.md
│   └── qa.md
├── tasks/                  # Task-specific prompts
│   ├── implementation/
│   │   ├── fastapi_endpoint.md
│   │   ├── react_component.md
│   │   └── test_suite.md
│   ├── review/
│   │   ├── code_review.md
│   │   ├── architecture_review.md
│   │   └── security_review.md
│   └── planning/
│       ├── feature_breakdown.md
│       └── sprint_planning.md
└── outcomes/               # Outcome logs for RL
    ├── 2026-01/
    │   ├── outcomes.jsonl
    │   └── analysis.json
    └── 2026-02/
```

### Registry Schema

```python
class PromptRegistry(BaseModel):
    """Global prompt registry for FORGE."""

    version: str
    updated_at: datetime

    # Indexed prompts
    prompts: dict[str, PromptEntry]  # prompt_id -> entry

    # Active A/B tests
    ab_tests: list[ABTest]

    # Aggregated stats
    total_applications: int
    overall_success_rate: float


class PromptEntry(BaseModel):
    """A single prompt in the registry."""

    prompt_id: str
    version: int
    status: Literal["active", "testing", "deprecated"]

    # Classification
    role: str  # "cto", "pm", "engineer", "*"
    task_type: str  # "implementation", "review", "planning"
    domains: list[str]  # ["*"] for global

    # Content
    file_path: str  # Relative to prompts/
    variables: list[str]  # Template variables

    # Metadata
    created_by: str
    created_at: datetime
    last_modified: datetime

    # Effectiveness (updated from outcomes)
    applications: int = 0
    successes: int = 0
    failures: int = 0
    avg_tokens_in: float = 0
    avg_tokens_out: float = 0
    avg_duration_s: float = 0

    @property
    def success_rate(self) -> float:
        if self.applications == 0:
            return 0.0
        return self.successes / self.applications


class ABTest(BaseModel):
    """An active A/B test between prompt variants."""

    test_id: str
    started_at: datetime
    ends_at: datetime

    # Variants
    control_prompt_id: str
    variant_prompt_id: str
    traffic_split: float  # 0.1 = 10% to variant

    # Results
    control_applications: int = 0
    control_successes: int = 0
    variant_applications: int = 0
    variant_successes: int = 0

    # Status
    status: Literal["running", "concluded"]
    winner: str | None  # prompt_id of winner
```

### Prompt Loading

```python
class PromptLoader:
    """Load and select prompts for agents."""

    def __init__(self, registry_path: Path):
        self.registry_path = registry_path
        self._registry: PromptRegistry | None = None

    def get_system_prompt(self, role: str) -> str:
        """Get the system prompt for an agent role."""
        path = self.registry_path / "system" / f"{role}.md"
        return path.read_text()

    def get_task_prompt(
        self,
        task_type: str,
        task_name: str,
        role: str = "*",
        domain: str = "*",
    ) -> tuple[str, str]:
        """
        Get the best prompt for a task.
        Returns (prompt_id, prompt_content).

        Considers:
        1. Role-specific vs generic
        2. Domain-specific vs generic
        3. A/B test assignment
        4. Success rate (prefer higher)
        """
        registry = self._load_registry()

        # Find matching prompts
        candidates = [
            p for p in registry.prompts.values()
            if p.task_type == task_type
            and p.status in ("active", "testing")
            and (p.role == role or p.role == "*")
            and (domain in p.domains or "*" in p.domains)
        ]

        if not candidates:
            raise ValueError(f"No prompt found for {task_type}/{task_name}")

        # Check A/B tests
        for test in registry.ab_tests:
            if test.status == "running":
                if test.control_prompt_id in [c.prompt_id for c in candidates]:
                    # Assign to test
                    if random.random() < test.traffic_split:
                        selected_id = test.variant_prompt_id
                    else:
                        selected_id = test.control_prompt_id
                    break
        else:
            # No A/B test, select by success rate (with exploration)
            selected = self._select_with_exploration(candidates)
            selected_id = selected.prompt_id

        # Load content
        entry = registry.prompts[selected_id]
        content = (self.registry_path / entry.file_path).read_text()

        return selected_id, content

    def _select_with_exploration(
        self,
        candidates: list[PromptEntry],
        exploration_rate: float = 0.1,
    ) -> PromptEntry:
        """Select prompt with epsilon-greedy exploration."""
        if random.random() < exploration_rate:
            # Explore: random selection
            return random.choice(candidates)
        else:
            # Exploit: select best success rate (with min samples)
            viable = [c for c in candidates if c.applications >= 10]
            if not viable:
                viable = candidates
            return max(viable, key=lambda c: c.success_rate)
```

---

## Outcome Logging for RL

### Outcome Schema

```python
class PromptOutcome(BaseModel):
    """A single prompt application outcome."""

    outcome_id: str
    timestamp: datetime

    # What was used
    prompt_id: str
    prompt_version: int
    agent_id: str
    session_id: str

    # Context
    task_type: str
    domain: str
    project: str
    feature_id: str | None

    # Variables used
    variables: dict[str, str]

    # Results
    success: bool

    # Metrics
    tokens_in: int
    tokens_out: int
    duration_s: float

    # Details
    error_type: str | None  # If failed
    human_override: bool  # Was outcome changed by human?
    override_reason: str | None

    # For analysis
    tags: list[str]  # ["auth", "fastapi", "crud"]
```

### Outcome Collection

```python
class OutcomeCollector:
    """Collects outcomes for RL analysis."""

    def __init__(self, outcomes_dir: Path):
        self.outcomes_dir = outcomes_dir

    async def record(self, outcome: PromptOutcome) -> None:
        """Record an outcome to the log."""
        # Append to JSONL file
        month_dir = self.outcomes_dir / outcome.timestamp.astimezone().strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)

        outcomes_file = month_dir / "outcomes.jsonl"
        with outcomes_file.open("a") as f:
            f.write(outcome.model_dump_json() + "\n")

        # Update registry stats (async)
        await self._update_registry_stats(outcome)

    async def _update_registry_stats(self, outcome: PromptOutcome) -> None:
        """Update prompt registry with new outcome."""
        # This would update the registry.json atomically
        pass
```

### Weekly Analysis Job

```python
class RLAnalyzer:
    """Analyzes outcomes and generates prompt improvements."""

    def __init__(self, outcomes_dir: Path, registry_path: Path):
        self.outcomes_dir = outcomes_dir
        self.registry_path = registry_path

    async def run_weekly_analysis(self) -> AnalysisReport:
        """Run weekly RL analysis."""

        # Load recent outcomes
        outcomes = self._load_recent_outcomes(days=7)

        # Group by prompt
        by_prompt = self._group_by_prompt(outcomes)

        # Identify patterns
        insights = []

        for prompt_id, prompt_outcomes in by_prompt.items():
            # Success rate analysis
            success_rate = sum(1 for o in prompt_outcomes if o.success) / len(prompt_outcomes)

            # Failure pattern analysis
            failures = [o for o in prompt_outcomes if not o.success]
            failure_patterns = self._analyze_failures(failures)

            # Token efficiency
            avg_tokens = sum(o.tokens_out for o in prompt_outcomes) / len(prompt_outcomes)

            insights.append(PromptInsight(
                prompt_id=prompt_id,
                applications=len(prompt_outcomes),
                success_rate=success_rate,
                failure_patterns=failure_patterns,
                avg_tokens=avg_tokens,
                recommendations=self._generate_recommendations(
                    prompt_id, success_rate, failure_patterns
                ),
            ))

        # Generate report
        return AnalysisReport(
            period_start=datetime.now() - timedelta(days=7),
            period_end=datetime.now(),
            total_outcomes=len(outcomes),
            insights=insights,
            suggested_ab_tests=self._suggest_ab_tests(insights),
        )

    def _analyze_failures(self, failures: list[PromptOutcome]) -> list[str]:
        """Identify common failure patterns."""
        patterns = []

        # Group by error type
        by_error = {}
        for f in failures:
            error = f.error_type or "unknown"
            by_error.setdefault(error, []).append(f)

        for error, cases in by_error.items():
            if len(cases) >= 3:  # Pattern threshold
                patterns.append(f"{error}: {len(cases)} occurrences")

        return patterns

    def _generate_recommendations(
        self,
        prompt_id: str,
        success_rate: float,
        failure_patterns: list[str],
    ) -> list[str]:
        """Generate improvement recommendations."""
        recs = []

        if success_rate < 0.7:
            recs.append(f"Low success rate ({success_rate:.0%}) - review prompt")

        if "timeout" in str(failure_patterns).lower():
            recs.append("Timeout failures - consider simplifying prompt")

        if "validation" in str(failure_patterns).lower():
            recs.append("Validation failures - add examples to prompt")

        return recs
```

---

## Dashboard Requirements

### Multi-Agent View

```
┌─────────────────────────────────────────────────────────────────────────┐
│  FORGE Multi-Agent Dashboard                    [Auto-refresh: 5s]     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  AGENTS (6 active)                                                       │
│  ┌─────────────┬─────────────┬─────────────┬─────────────┬────────────┐│
│  │ CTO         │ PM          │ Oracle      │ Eng-1       │ Eng-2      ││
│  │ 🟢 Active   │ 🟡 Waiting  │ 🟢 Active   │ 🟢 Active   │ 🔴 Blocked ││
│  │             │             │             │             │            ││
│  │ Reviewing   │ Awaiting    │ Assigning   │ voice-coach │ Needs      ││
│  │ auth pattern│ CTO input   │ work        │ /auth       │ Stripe key ││
│  │             │             │             │             │            ││
│  │ 45m active  │ 12m waiting │ 2m active   │ 23m active  │ 8m blocked ││
│  └─────────────┴─────────────┴─────────────┴─────────────┴────────────┘│
│                                                                          │
│  WORK CLAIMS                                                             │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Eng-1 → voice-coach/app/auth/* (expires: 15m)                    │  │
│  │ Eng-2 → interview-sim/backend/billing.py (BLOCKED - needs key)   │  │
│  │ CTO   → harness/patterns/auth.md (expires: 30m)                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  PENDING APPROVALS (2)                                    [View All]    │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ 🔴 DESKTOP  CTO override on PM scope decision      [Approve/Rej] │  │
│  │ 🟡 PHONE    Feature auth-001 ready for review      [Approve/Rej] │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  RECENT ACTIVITY                                                         │
│  ├─ 2m ago   Oracle: Assigned billing to Eng-2                          │
│  ├─ 5m ago   CTO: Broadcast - "Use Pydantic Settings pattern"           │
│  ├─ 8m ago   Eng-2: Escalated - Missing STRIPE_SECRET_KEY               │
│  ├─ 12m ago  PM: Requested CTO review on scope                          │
│  └─ 15m ago  Eng-1: Claimed voice-coach/auth/*                          │
│                                                                          │
│  [Focus: CTO] [Focus: PM] [Focus: Eng-1] [All Agents] [Prompt Stats]   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Agent Focus View

When focusing on a single agent:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Agent: Eng-1 (voice-coach)                          [Back to Overview] │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  STATUS: 🟢 Active                                                       │
│  CURRENT TASK: Implement JWT authentication                              │
│  CLAIMED: voice-coach/app/auth/* (15m remaining)                        │
│                                                                          │
│  MEMORY (Short-term)                                                     │
│  ├─ Files read: 12                                                       │
│  ├─ Files modified: 3                                                    │
│  └─ Decisions: 2 (referenced long-term patterns)                        │
│                                                                          │
│  RELEVANT PATTERNS (from Long-term Memory)                              │
│  ├─ jwt_auth_fastapi (success: 94%, 47 applications)                    │
│  └─ pydantic_settings (success: 98%, 156 applications)                  │
│                                                                          │
│  RECENT ACTIONS                                                          │
│  ├─ Modified: app/auth/router.py (+45 lines)                            │
│  ├─ Modified: app/auth/service.py (+62 lines)                           │
│  ├─ Modified: app/core/security.py (+28 lines)                          │
│  └─ Running: pytest tests/test_auth.py                                  │
│                                                                          │
│  COMMUNICATION                                                           │
│  ├─ Received: CTO broadcast about Pydantic Settings (5m ago)            │
│  └─ Sent: Claim for auth module (23m ago)                               │
│                                                                          │
│  PROMPT USED: tasks/implementation/fastapi_endpoint.md (v3)             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Foundation (Week 1)

| Task | Description | Effort |
|------|-------------|--------|
| Message Bus | Redis Streams setup with consumer groups | 2d |
| Work Claims | Atomic claim/release with expiry | 1d |
| Agent Base | Base class with communication methods | 1d |
| Basic Dashboard | CLI view of active agents | 1d |

### Phase 2: Memory System (Week 2)

| Task | Description | Effort |
|------|-------------|--------|
| Long-term Memory | Schema + storage + loading | 2d |
| Short-term Memory | Session-scoped state | 1d |
| Memory Promotion | Rules for short→long promotion | 1d |
| Memory API | Query and update endpoints | 1d |

### Phase 3: Role Implementation (Week 3)

| Task | Description | Effort |
|------|-------------|--------|
| CTO Agent | System prompt + authorities | 1d |
| PM Agent | System prompt + authorities | 1d |
| Oracle Agent | Coordination logic + conflict resolution | 2d |
| Engineer Agent | Enhanced with claims + memory | 1d |

### Phase 4: Prompt Registry (Week 4)

| Task | Description | Effort |
|------|-------------|--------|
| Registry Schema | Storage + versioning | 1d |
| Prompt Loader | Selection with A/B support | 1d |
| Outcome Collection | Logging infrastructure | 1d |
| Initial Prompts | Migrate existing to registry | 2d |

### Phase 5: RL Pipeline (Week 5)

| Task | Description | Effort |
|------|-------------|--------|
| Weekly Analyzer | Batch analysis job | 2d |
| A/B Framework | Test creation + traffic split | 2d |
| Improvement Generator | Claude-assisted prompt refinement | 1d |

### Phase 6: Dashboard (Week 6)

| Task | Description | Effort |
|------|-------------|--------|
| Multi-Agent TUI | Rich-based dashboard | 2d |
| Agent Focus View | Detailed single-agent view | 1d |
| Real-time Updates | WebSocket streaming | 1d |
| Prompt Stats View | Registry effectiveness view | 1d |

---

## File Structure

```
harness/
├── forge_harness/
│   ├── multi_agent/
│   │   ├── __init__.py
│   │   ├── bus.py              # Message bus (Redis Streams)
│   │   ├── claims.py           # Work claim system
│   │   ├── communicator.py     # Agent communication
│   │   ├── coordinator.py      # Oracle logic
│   │   └── dashboard.py        # Multi-agent dashboard
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── long_term.py        # Persistent memory
│   │   ├── short_term.py       # Session memory
│   │   ├── promotion.py        # Memory promotion rules
│   │   └── schemas.py          # Memory schemas
│   ├── prompts/
│   │   ├── __init__.py
│   │   ├── registry.py         # Prompt registry
│   │   ├── loader.py           # Prompt loading + A/B
│   │   ├── outcomes.py         # Outcome collection
│   │   └── analyzer.py         # RL analysis
│   └── agents/
│       ├── __init__.py
│       ├── base.py             # Base agent with memory + comms
│       ├── cto.py              # CTO agent
│       ├── pm.py               # PM agent
│       ├── oracle.py           # Oracle agent
│       ├── engineer.py         # Engineer agent
│       └── qa.py               # QA agent
├── prompts/
│   ├── registry.json
│   ├── system/
│   ├── tasks/
│   └── outcomes/
└── tests/
    ├── multi_agent/
    ├── memory/
    └── prompts/
```

---

## Next Steps

1. **Review this architecture** - Does this match your vision?
2. **Prioritize phases** - Which capabilities are most urgent?
3. **Redis setup** - Local or Railway Redis for message bus?
4. **Initial agents** - Start with CTO + Oracle + 1 Engineer, or full roster?

Ready to start implementation when you approve the direction.
