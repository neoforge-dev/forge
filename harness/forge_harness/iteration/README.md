# FORGE Harness Iteration Module

Smart task prioritization for autonomous feature development.

## Overview

The iteration module provides intelligent task prioritization based on multiple factors:

- **Impact**: Dependencies and priority level
- **Urgency**: Failing tests and critical issues
- **Effort**: Estimated implementation cost (tokens)
- **Domain Boost**: High-priority domains get 1.5x multiplier

## Installation

The module is part of `forge_harness` and requires no additional dependencies.

```bash
cd harness
uv sync
```

## Quick Start

```python
from forge_harness.iteration.prioritizer import prioritize_tasks

# Load and prioritize tasks from features.json
tasks = prioritize_tasks("features.json")

# Display top 5 tasks
for task in tasks[:5]:
    print(f"[{task.task_id}] {task.name}")
    print(f"  Score: {task.score:.2f}")
    print(f"  {task.reasoning}")
```

## API Reference

### `prioritize_tasks(features_json_path: str) -> list[PrioritizedTask]`

Main entry point. Loads features from JSON and returns ranked tasks.

**Parameters:**
- `features_json_path`: Path to features.json file

**Returns:**
- List of `PrioritizedTask` objects sorted by score (highest first)

**Raises:**
- `FileNotFoundError`: If features file doesn't exist
- `json.JSONDecodeError`: If file is not valid JSON

### `PrioritizedTask`

Dataclass representing a prioritized task.

**Attributes:**
- `task_id: str` - Unique task identifier
- `name: str` - Human-readable task name
- `score: float` - Final priority score (higher = more important)
- `impact: float` - Impact component of score
- `urgency: float` - Urgency component of score
- `effort: float` - Effort component (subtracted from total)
- `reasoning: str` - Human-readable explanation

## Scoring Formula

### Components

**Impact** = (dependency_count × 2) + priority_weight

Priority weights:
- critical: 4
- high: 3
- medium: 2
- low: 1

**Urgency** = base (1.0) + boosts
- Failing tests: +3
- Critical priority: +2

**Effort** = estimated_tokens / 1000

**Final Score** = (impact + urgency - effort) × domain_multiplier

### Domain Boost

Tasks in these domains get 1.5x multiplier:
- `voice-coach`
- `command_center`

## Features JSON Structure

```json
{
  "features": [
    {
      "id": "TASK-001",
      "title": "Task Name",
      "priority": "high",
      "domain": "voice-coach",
      "dependencies": ["TASK-002", "TASK-003"],
      "estimated_tokens": 2500,
      "has_failing_tests": true
    }
  ]
}
```

### Required Fields
- `id` - Task identifier

### Optional Fields
- `title` or `name` - Task name (defaults to "Unnamed task")
- `priority` - Priority level (defaults to "medium")
- `domain` - Domain name (defaults to "")
- `dependencies` - List of dependency IDs (defaults to [])
- `estimated_tokens` - Implementation cost (defaults to 1000)
- `has_failing_tests` - Whether task has failing tests (defaults to false)

## Examples

### Basic Usage

```python
from forge_harness.iteration.prioritizer import prioritize_tasks

tasks = prioritize_tasks("features.json")

# Print top 10 tasks
for i, task in enumerate(tasks[:10], 1):
    print(f"{i}. [{task.task_id}] {task.name} (score: {task.score:.2f})")
```

### Filter by Score Range

```python
tasks = prioritize_tasks("features.json")

# Get high-priority tasks (score >= 10)
high_priority = [t for t in tasks if t.score >= 10]

# Get urgent tasks (failing tests)
urgent = [t for t in tasks if t.urgency >= 4]
```

### Score Distribution

```python
tasks = prioritize_tasks("features.json")

score_ranges = {
    "Very High (10+)": [t for t in tasks if t.score >= 10],
    "High (5-10)": [t for t in tasks if 5 <= t.score < 10],
    "Medium (3-5)": [t for t in tasks if 3 <= t.score < 5],
    "Low (<3)": [t for t in tasks if t.score < 3],
}

for range_name, range_tasks in score_ranges.items():
    print(f"{range_name}: {len(range_tasks)} tasks")
```

### Command-Line Usage

Run the example script:

```bash
cd harness
uv run python forge_harness/iteration/example.py
```

## Testing

Run the test suite:

```bash
cd harness
uv run pytest tests/test_prioritizer.py -v
```

With coverage:

```bash
uv run pytest tests/test_prioritizer.py -v --cov=forge_harness/iteration --cov-report=term-missing
```

## Integration with Ralph Loop

The prioritizer can be integrated with the Ralph Loop for automatic task ranking:

```python
from forge_harness.iteration.prioritizer import prioritize_tasks
from forge_harness.ralph_loop import create_ralph_loop

# Prioritize tasks
tasks = prioritize_tasks("features.json")

# Extract top N tasks
top_tasks = [task.task_id for task in tasks[:5]]

# Run Ralph Loop on prioritized tasks
loop = create_ralph_loop(
    features_path="features.json",
    feature_filter=lambda f: f["id"] in top_tasks,
)
await loop.run()
```

## Architecture

```
forge_harness/iteration/
├── __init__.py          # Module exports
├── prioritizer.py       # Core prioritization logic
├── example.py           # Example usage script
└── README.md           # This file

tests/
└── test_prioritizer.py  # Comprehensive test suite
```

## Design Decisions

### Why These Scoring Factors?

- **Impact**: Tasks with many dependents block more work
- **Urgency**: Failing tests indicate immediate problems
- **Effort**: Prefer quick wins when scores are similar
- **Domain Boost**: Focus on strategic domains first

### Why These Weights?

The weights were chosen to balance:
- Dependency impact (2x multiplier creates noticeable difference)
- Urgency signals (failing tests = critical, +3 is substantial)
- Effort penalty (keeps high-cost tasks from dominating)
- Domain focus (1.5x boost noticeable but not overwhelming)

### Extensibility

To add new scoring factors:

1. Add calculation function in `prioritizer.py`
2. Update `PrioritizedTask` dataclass if needed
3. Modify score calculation in `prioritize_tasks()`
4. Update `_build_reasoning()` to include new factor
5. Add tests in `test_prioritizer.py`

## Related

- [Ralph Loop](../ralph_loop.py) - Autonomous feature loop
- [Flywheel](../flywheel.py) - Compounding development
- [Quality Loop](../quality_loop.py) - Quality gates

## License

Part of the FORGE Harness. See repository license for details.

# FORGE Harness - Iteration Module

Automated assessment, planning, and execution for autonomous development loops.

## Overview

The iteration module implements the **assess-plan-execute** cycle for FORGE's autonomous development workflow. It provides fast (<60s), comprehensive state assessment across the entire portfolio.

## Architecture

```
iteration/
├── __init__.py          # Public API exports
├── assess.py            # Assessment phase (HRN-011)
├── prioritizer.py       # Smart task prioritization (HRN-012)
├── plan.py             # Planning phase (future)
├── execute.py          # Execution phase (future)
├── demo_assess.py      # Assessment demo
├── demo_prioritizer.py # Prioritizer demo
└── README.md           # This file
```

## Assess Phase (HRN-011)

The assess phase collects current state across all active projects:

### What It Collects

1. **Agent Status** - All active tmux sessions with agent classification
2. **Git Status** - Staged, modified, untracked, and conflicted files across domains
3. **Test Results** - Failed tests from pytest cache
4. **Issues** - Detected problems (failed tests, conflicts, uncommitted changes)

### Performance

- **Target**: <60 seconds for full portfolio scan
- **Actual**: ~0.01-0.5s for typical portfolios
- **Parallelization**: Git operations run concurrently where possible

### Usage

```python
from forge_harness.iteration import assess_status

# Run assessment
report = assess_status()

# Check results
print(f"Active agents: {len(report.agents)}")
print(f"Failed tests: {report.test_results.failed_count}")
print(f"Projects with changes: {len([p for p in report.git_status.projects if p.has_changes])}")
print(f"Issues: {len(report.issues)}")

# Export to JSON
with open("assessment.json", "w") as f:
    f.write(report.to_json())
```

### CLI Usage

```bash
# Run assessment and display results
cd harness
uv run python forge_harness/iteration/demo_assess.py

# Or use the test command
python -c 'from forge_harness.iteration.assess import assess_status; report = assess_status(); print(report.to_json())'
```

## Data Models

### AgentStatus

Represents a single agent (tmux session):

```python
@dataclass
class AgentStatus:
    session_name: str
    agent_type: AgentType  # BACKEND, FRONTEND, DEBUG, QA, CONTENT, EXTERNAL, UNKNOWN
    is_active: bool
    working_directory: Path | None
    last_activity: datetime | None
    current_task: str | None
    metadata: dict[str, Any]
```

### ProjectStatus

Git status for a single project:

```python
@dataclass
class ProjectStatus:
    domain: str
    project: str
    path: Path
    branch: str
    staged: list[str]
    modified: list[str]
    untracked: list[str]
    conflicts: list[str]
    ahead: int
    behind: int
```

### TestResults

Aggregated test results:

```python
@dataclass
class TestResults:
    total_tests: int
    passed_count: int
    failed_count: int
    skipped_count: int
    error_count: int
    duration: float
    failed_tests: list[TestResult]
    last_run: datetime | None
```

### Issue

Detected issue requiring attention:

```python
@dataclass
class Issue:
    issue_type: IssueType  # FAILED_TEST, LINT_ERROR, GIT_CONFLICT, etc.
    severity: str  # critical, high, medium, low
    message: str
    location: str | None
    details: dict[str, Any]
```

### AssessmentReport

Complete assessment with all collected data:

```python
@dataclass
class AssessmentReport:
    agents: list[AgentStatus]
    git_status: GitStatus
    test_results: TestResults
    issues: list[Issue]
    assessed_at: datetime
    duration_seconds: float
```

## Agent Classification

The assess phase automatically classifies tmux sessions by agent type:

| Pattern | Agent Type |
|---------|------------|
| `gemini-*`, `codex-*`, `opencode-*`, `cursor-*`, `amp-*` | EXTERNAL |
| `qa-*`, `test-*` | QA |
| `backend-*`, `*-api`, `*-service` | BACKEND |
| `frontend-*`, `*-ui`, `*-web` | FRONTEND |
| `debug-*`, `fix-*` | DEBUG |
| `content-*`, `marketing-*` | CONTENT |
| Others | UNKNOWN |

## Issue Detection

The assess phase identifies common issues:

### Failed Tests
- **Severity**: High (>5 failures), Medium (1-5 failures)
- **Detection**: Reads pytest cache lastfailed
- **Action**: Review test output, fix bugs

### Git Conflicts
- **Severity**: Critical
- **Detection**: `git status --porcelain` conflict markers
- **Action**: Resolve conflicts immediately

### Uncommitted Changes
- **Severity**: Medium (>10 files)
- **Detection**: Count of staged + modified files
- **Action**: Commit or discard changes

### Inactive Agents
- **Severity**: Low
- **Detection**: Inactive agent with current_task set
- **Action**: Restart or clean up agent session

## Integration

### Ralph Loop

The Ralph loop uses assess phase to check state before each iteration:

```python
from forge_harness.iteration import assess_status

# In Ralph loop
report = assess_status()

# Check for blockers
if report.git_status.total_conflicts > 0:
    raise ValueError("Cannot proceed with unresolved conflicts")

if report.test_results.failed_count > 10:
    logger.warning("High number of failed tests")
```

### Flywheel

The flywheel uses assess phase for continuous monitoring:

```python
# In flywheel main loop
while True:
    report = assess_status()

    # Auto-recovery logic
    if report.test_results.failed_count > 0:
        dispatch_fix_agent(report.test_results.failed_tests)

    # Progress tracking
    track_metrics(report.get_summary())

    time.sleep(300)  # Check every 5 minutes
```

### Command Center

The command center exposes assess data via API:

```python
@app.get("/api/assess")
async def get_assessment():
    report = assess_status()
    return report.to_dict()
```

## Testing

Comprehensive unit tests cover all functionality:

```bash
# Run all tests
cd harness
uv run pytest tests/test_iteration/test_assess.py -v

# Run specific test class
uv run pytest tests/test_iteration/test_assess.py::TestAgentStatus -v

# Run with coverage
uv run pytest tests/test_iteration/ --cov=forge_harness.iteration --cov-report=html
```

## Performance Optimization

### Current Optimizations

1. **Parallel Git Operations** - All project git status queries run concurrently
2. **Cached DomainRegistry** - Domain configuration loaded once and reused
3. **Short Timeouts** - 5s timeout on subprocess calls to fail fast
4. **Minimal Data** - Only collect necessary information

### Future Optimizations

1. **Incremental Scanning** - Only scan changed projects
2. **Background Collection** - Pre-collect state in background thread
3. **Result Caching** - Cache assessment results for 30s
4. **Selective Depth** - Option to skip test/git details for faster overview

## Troubleshooting

### Slow Assessment (>10s)

- **Check**: Number of projects being scanned
- **Fix**: Skip inactive projects or use `FORGE_SCAN_ACTIVE_ONLY=1`

### Missing Projects

- **Check**: DomainRegistry loading correctly
- **Fix**: Verify `domains.yaml` is valid and projects exist

### No Test Results

- **Check**: Pytest cache exists (`.pytest_cache/`)
- **Fix**: Run tests at least once to populate cache

### Incorrect Agent Classification

- **Check**: Agent session names follow conventions
- **Fix**: Rename session or update classification logic in `_classify_agent_type()`

## Related

- **HRN-011**: Automated assess phase requirement
- **Ralph Loop**: Autonomous development loop (`ralph_loop.py`)
- **Flywheel**: Continuous development cycle (`flywheel.py`)
- **Domain Registry**: Portfolio configuration (`domain_registry.py`)

## Prioritizer (HRN-012)

The smart prioritizer ranks tasks by impact, urgency, effort, and domain weights. It considers failing tests, broken builds, and task dependencies.

### Features

1. **Multi-dimensional Scoring** - Impact (1-10), Urgency (1-10), Effort (1-5)
2. **Dependency Analysis** - Identifies blockers and dependencies
3. **Failure Boosting** - Increases priority for tasks related to failing tests/builds
4. **Domain Weights** - Applies multipliers based on domain priority (e.g., voice-coach: 1.5x)
5. **Multiple Strategies** - Impact-first, quick-wins, balanced, urgency-first, dependency-first

### Usage

```python
from forge_harness.iteration import prioritize_tasks, PrioritizationStrategy
from forge_harness.iteration import assess_status
from pathlib import Path

# Get assessment data
assessment = assess_status()

# Prioritize tasks
tasks = prioritize_tasks(
    features_paths=[Path("features.json")],
    assessment_report=assessment,
    strategy=PrioritizationStrategy.BALANCED,
    domain_weights={"voice-coach": 1.5, "interview-simulator": 1.3}
)

# Show top 5
for task in tasks[:5]:
    print(f"{task.feature_id}: {task.title} (score: {task.score:.2f})")
    print(f"  {task.reasoning}")
    if task.blockers:
        print(f"  ⚠️  Blocked by: {', '.join(task.blockers)}")
```

### Strategies

| Strategy | Formula | Best For |
|----------|---------|----------|
| `BALANCED` | `(impact × urgency) / effort × weight` | General purpose |
| `IMPACT_FIRST` | `(impact × 2 + urgency) × weight` | Maximum business value |
| `QUICK_WINS` | `(impact × urgency) / effort × weight` | Fast ROI |
| `URGENCY_FIRST` | `(urgency × 2 + impact) / effort × weight` | Time-sensitive work |
| `DEPENDENCY_FIRST` | Boosts unblocked tasks | Clear blockers first |

### Domain Weights

Default multipliers by domain priority:

```python
{
    "voice-coach": 1.5,          # Active development
    "interview-simulator": 1.3,   # Active development
    "brand-brain": 1.3,           # Active development
    "harness": 1.2,               # Infrastructure
    "forge-shared": 1.1,          # Shared utilities
    # All others: 1.0
}
```

### Effort Estimation

Automatically estimates effort (1-5) from acceptance criteria:

- **1 (Trivial)**: Simple change (1 criterion, no complexity)
- **2 (Simple)**: Basic feature (2 criteria, minimal complexity)
- **3 (Medium)**: Standard feature (3 criteria, some complexity)
- **4 (Complex)**: Integration work (4+ criteria, external APIs, DB changes)
- **5 (Very Complex)**: Major work (6+ criteria, webhooks, performance, migrations)

Complexity indicators:
- External integrations (+2)
- Database/schema changes (+1)
- Performance optimization (+1)
- Testing/validation (+0.5)

### Failure Boosting

Automatically boosts priority when assessment detects issues:

- **Failing tests** → 1.5x boost for test-related tasks
- **Build errors** → 1.8x boost for build/lint tasks
- **Git conflicts** → 1.7x boost for merge tasks

### CLI Usage

```bash
# Run prioritizer demo
cd harness
uv run python forge_harness/iteration/demo_prioritizer.py

# Or use directly
python -c 'from forge_harness.iteration import prioritize_tasks; tasks = prioritize_tasks(); print(f"Top task: {tasks[0].title}")'
```

### PrioritizedTask Model

```python
@dataclass
class PrioritizedTask:
    feature_id: str
    title: str
    score: float              # Composite priority score
    impact: int               # 1-10
    urgency: int              # 1-10
    effort: int               # 1-5
    domain_weight: float      # Multiplier
    reasoning: str            # Why this score
    dependencies: list[str]   # Other features this depends on
    blockers: list[str]       # Incomplete dependencies
    status: str
    epic: str | None
    acceptance_criteria: list[str]
    test_command: str | None
    metadata: dict[str, Any]
```

## Dispatcher (HRN-013) ✅ Complete

The delegation dispatcher routes prioritized tasks to optimal agents based on type, availability, and specialization. It creates tmux sessions, sends task prompts, tags sessions with metadata, and updates task status.

### Features

1. **Tmux Session Management** - Creates isolated sessions for each task
2. **Agent Selection** - Automatic routing based on task type (backend, frontend, test, debug, content)
3. **Fallback Handling** - Secondary agent selection if primary unavailable
4. **Task Tracking** - Persists dispatch results to `.forge/dispatch/` directory
5. **Status Management** - Updates task status to 'in_progress', 'completed', 'failed'

### Agent Routing

| Task Type | Primary Agent | Fallback Agents |
|-----------|---------------|-----------------|
| Backend (api, database, server) | backend-engineer | opencode → codex → claude |
| Frontend (ui, component, react) | frontend-builder | cursor → claude |
| Testing (test, e2e, integration) | qa-tester | backend → claude |
| Debug (debug, fix, error) | debug-detective | backend → claude |
| Content (content, docs, blog) | content-writer | gemini → claude |

### Usage

```python
from forge_harness.iteration import dispatch_task, dispatch_tasks, get_dispatch_status

# Dispatch single task
result = dispatch_task("HRN-001")
if result.success:
    print(f"Task {result.task_id} dispatched to {result.session_id}")
    print(f"Agent: {result.agent_type.value}")

# Dispatch multiple tasks (max_parallel enforced)
results = dispatch_tasks(
    task_ids=["HRN-001", "HRN-002", "HRN-003"],
    max_parallel=2
)

# Check dispatch status
status = get_dispatch_status("HRN-001")
if status:
    print(f"Status: {status.status}")
```

### CLI Usage

```bash
# Dispatch task
python -m forge_harness.iteration.dispatch HRN-001

# With custom features file
python -m forge_harness.iteration.dispatch HRN-001 --features features.json

# With custom forge root
python -m forge_harness.iteration.dispatch HRN-001 --forge-root /path/to/forge
```

### Task Prompt Format

The dispatcher builds structured prompts with:
- Feature ID and title
- Numbered acceptance criteria
- Test command
- Epic classification

Example:
```
Implement HRN-001: Task agent parallel dispatch

Acceptance Criteria:
1. Can dispatch 2-5 Task agents in parallel
2. Each agent receives isolated task definition

Test Command: pytest tests/test_parallel_dispatch.py -v

Epic: Multi-Agent Executor
```

### Session Tagging

Sessions are tagged with metadata using tmux user variables:
```bash
tmux set-option -t forge-task-HRN-001 @task_id HRN-001
tmux set-option -t forge-task-HRN-001 @agent_type backend-engineer
tmux set-option -t forge-task-HRN-001 @epic "Multi-Agent Executor"
```

### Tracking Files

Dispatch results are persisted to `.forge/dispatch/{task_id}.json`:
```json
{
  "success": true,
  "task_id": "HRN-001",
  "session_id": "forge-task-HRN-001",
  "agent_type": "backend-engineer",
  "status": "in_progress",
  "dispatched_at": "2026-02-04T17:41:00Z",
  "metadata": {
    "title": "Task agent parallel dispatch",
    "epic": "Multi-Agent Executor",
    "acceptance_criteria": [...],
    "test_command": "pytest ..."
  }
}
```

### DispatchResult Model

```python
@dataclass
class DispatchResult:
    success: bool
    task_id: str
    session_id: str | None
    agent_type: AgentType | None
    status: str  # dispatched, failed, unavailable
    error: str | None
    dispatched_at: datetime | None
    metadata: dict[str, Any]
```

### Error Handling

The dispatcher handles:
- tmux not installed/unavailable
- Session creation failures
- Task not found in features.json
- Agent unavailability (tries fallback)
- File system errors (tracking directory)
- Timeouts (10s for tmux commands)

All errors logged and return structured `DispatchResult` with error details.

### Testing

Comprehensive test suite with 31 tests and 79% coverage:
```bash
cd harness
uv run pytest tests/test_iteration/test_dispatch.py -v

# Result: 31 passed in 2.82s
```

## Monitor Phase (HRN-014) ✅ Complete

The iteration monitor tracks active tasks, collects feedback, and detects completion/failure. It polls tmux sessions for output markers, handles timeouts, and provides real-time progress updates.

### Features

1. **Task Monitoring** - Polls active sessions for completion markers
2. **Timeout Detection** - Auto-retry or escalate stalled tasks
3. **Output Analysis** - Parses session output for errors and warnings
4. **Status Updates** - Updates dispatch tracking with progress
5. **Feedback Collection** - Extracts learnings from task outcomes

### Usage

```python
from forge_harness.iteration import IterationMonitor

# Create monitor
monitor = IterationMonitor()

# Monitor active tasks
results = await monitor.monitor_active_tasks()

# Check specific task
status = await monitor.check_task_status("HRN-001")
```

## Journal Phase (HRN-015) ✅ Complete

The iteration journal captures decisions, patterns, and learnings during development sessions. It writes timestamped summaries to `docs/PROMPT.md` for session continuity.

### Features

1. **Entry Types** - Decisions, patterns, blockers, context, next steps
2. **Markdown Formatting** - Structured sections with timestamps
3. **PROMPT.md Integration** - Appends to existing documentation
4. **Query & Filter** - Get entries by type, find unresolved blockers
5. **Auto-Write Mode** - Optionally write after each entry
6. **JSON Export** - Export journal data for analysis

### Usage

```python
from forge_harness.iteration import IterationJournal

# Create journal
journal = IterationJournal()

# Log session activities
journal.log_decision(
    decision="Use Zustand over Context",
    reasoning="Better performance and simpler API"
)

journal.log_pattern(
    pattern="TanStack Query with wrapper",
    outcome="Extract json.data for clean queries"
)

journal.log_blocker(
    blocker="SSE reconnection loop",
    resolution="Use stable refs in useEffect"
)

journal.log_next_step("Implement bulk approval actions")

# Write to PROMPT.md
journal.write_to_prompt_md()
```

### Entry Types

| Type | Purpose | Example |
|------|---------|---------|
| `DECISION` | What and why | "Use Zustand: Better performance" |
| `PATTERN` | Reusable insights | "TanStack Query: Extract data wrapper" |
| `BLOCKER` | Issues + resolutions | "SSE loop: Use stable refs" |
| `CONTEXT` | General observations | "Working on Phase 4 features" |
| `NEXT_STEP` | Continuation points | "Add E2E tests" |

### PROMPT.md Format

```markdown
## [2026-02-04 18:26] Session Notes

### Decisions Made
- **Decision**: Use React DnD for drag-and-drop
  - **Why**: Most popular library with TypeScript support

### Patterns Discovered
- **Pattern**: TanStack Query with wrapper
  - **Outcome**: Extract json.data for clean queries

### Blockers Encountered
- **Blocker**: SSE reconnection loop
  - **Resolution**: Use stable refs
- **Blocker**: Database timeout
  - **Status**: Unresolved

### Context Notes
- Working on Command Center Phase 4 features

### Next Steps
- Implement bulk approval actions
- Add E2E tests
```

### Query & Filter

```python
# Get entries by type
decisions = journal.get_entries_by_type(EntryType.DECISION)
patterns = journal.get_entries_by_type(EntryType.PATTERN)

# Find unresolved blockers
unresolved = journal.get_unresolved_blockers()
for blocker in unresolved:
    print(f"Blocker: {blocker.metadata['blocker']}")

# Export to JSON
data = journal.export_json()
```

### Auto-Write Mode

```python
# Write after each entry
journal = IterationJournal(auto_write=True)
journal.log_decision("Decision", "Reasoning")  # Writes immediately
```

### CLI Usage

```bash
# Run journal demo
cd harness
uv run python demo_journal.py
```

### Testing

Comprehensive test suite with 31 tests and 97% coverage:
```bash
cd harness
uv run pytest tests/test_iteration/test_journal.py -v

# Result: 31 passed in 2.90s
```

### Integration

**Ralph Loop**:
```python
# After feature completion
journal.log_pattern(feature.pattern, feature.outcome)
journal.write_to_prompt_md()
```

**Flywheel**:
```python
# After iteration
for result in results:
    if result.success:
        journal.log_pattern(result.pattern, result.outcome)
    else:
        journal.log_blocker(result.error)
journal.write_to_prompt_md()
```

## Future Work

### Assessment Extensions
- **Lint Results**: Collect ESLint/Ruff output
- **Type Errors**: Mypy/TypeScript errors
- **Build Errors**: Compilation failures
- **Dependency Issues**: Missing packages, version conflicts
- **Security Scans**: Known vulnerabilities
