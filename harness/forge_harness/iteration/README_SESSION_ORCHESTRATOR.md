# Session Orchestrator

The Session Orchestrator provides a unified workflow for coordinating iteration tracking, auto-committing, and PR generation in FORGE Harness engineering sessions.

## Overview

The Session Orchestrator ties together three key components:
1. **IterationTracker** - Tracks iteration history and progress metrics
2. **AutoCommitter** - Generates conventional commits from changes
3. **PRGenerator** - Creates GitHub pull requests with context

## Quick Start

```python
from forge_harness.iteration import create_session_orchestrator

# Create orchestrator
orchestrator = create_session_orchestrator(
    project_name="voice-coach",
    auto_commit=True,
    auto_pr=True,
)

# Start iteration
iteration_num = orchestrator.start_iteration()

# Do work...

# Complete iteration with auto-commit and PR
result = orchestrator.complete_iteration(
    features=["Add pitch analysis", "Implement feedback system"],
    tests_added=15,
    lines_added=500,
)

if result.success:
    print(f"✓ Iteration {result.iteration_number} completed")
    print(f"  Commit: {result.commit_result.commit_hash}")
    print(f"  PR: {result.pr_result.pr_url}")
```

## Components

### SessionConfig

Configuration for a development session:

```python
from forge_harness.iteration import SessionConfig

config = SessionConfig(
    project_name="my-project",
    base_branch="main",           # Base branch for PRs
    auto_commit=True,              # Auto-commit on completion
    auto_pr=False,                 # Auto-create PR
    pr_draft=True,                 # Create draft PRs
    commit_scope="backend",        # Conventional commit scope
)
```

### SessionOrchestrator

Main orchestrator class:

```python
from forge_harness.iteration import SessionOrchestrator

orchestrator = SessionOrchestrator(
    config=config,
    repo_path=Path("/path/to/repo"),
    tracker_path=Path(".forge/learning/iteration_history.json"),
)

# Start iteration
iteration_num = orchestrator.start_iteration()

# Complete iteration
result = orchestrator.complete_iteration(
    features=["Feature 1", "Feature 2"],
    tests_added=10,
    lines_added=300,
    files_changed=["file1.py", "file2.py"],
    notes="All working",
)

# Handle failure
result = orchestrator.fail_iteration(
    reason="Tests failed"
)

# Get stats
stats = orchestrator.get_stats()
print(f"Completed: {stats.completed_iterations}/{stats.total_iterations}")
```

### SessionResult

Result object returned from operations:

```python
@dataclass
class SessionResult:
    success: bool
    iteration_number: int
    features_completed: List[str]
    commit_result: Optional[CommitResult]
    pr_result: Optional[PRResult]
    stats: Optional[TrackerStats]
    error: Optional[str]
    duration_seconds: float
```

## Usage Patterns

### Basic Session (Auto-Commit Only)

```python
orchestrator = create_session_orchestrator(
    project_name="my-project",
    auto_commit=True,
    auto_pr=False,
)

orchestrator.start_iteration()
result = orchestrator.complete_iteration(
    features=["Feature A"],
    tests_added=5,
)
# Commits automatically, no PR
```

### Full Workflow (Auto-Commit + PR)

```python
orchestrator = create_session_orchestrator(
    project_name="my-project",
    auto_commit=True,
    auto_pr=True,
)

orchestrator.start_iteration()
result = orchestrator.complete_iteration(
    features=["Feature A", "Feature B"],
    tests_added=10,
    lines_added=500,
)
# Commits and creates PR automatically
```

### Multiple Iterations

```python
orchestrator = create_session_orchestrator(
    project_name="my-project",
    auto_commit=True,
)

# Iteration 1
orchestrator.start_iteration()
result1 = orchestrator.complete_iteration(
    features=["Setup"],
    tests_added=5,
)

# Iteration 2
orchestrator.start_iteration()
result2 = orchestrator.complete_iteration(
    features=["Core features"],
    tests_added=20,
)

# View progress
stats = orchestrator.get_stats()
print(f"Total features: {stats.total_features}")
```

### Failure Handling

```python
orchestrator = create_session_orchestrator(
    project_name="my-project",
    auto_commit=True,
)

orchestrator.start_iteration()

# Handle failure
result = orchestrator.fail_iteration(
    reason="Tests failed - authentication bug"
)

# Start recovery iteration
orchestrator.start_iteration()
result = orchestrator.complete_iteration(
    features=["Fix authentication bug"],
    tests_added=3,
)
```

## Integration with Other Components

### With IterationTracker

```python
# The orchestrator uses the tracker internally
tracker = orchestrator.tracker

# Get recent iterations
recent = tracker.get_recent(count=10)
for record in recent:
    print(f"{record.iteration_number}: {record.status}")
```

### With AutoCommitter

```python
# The orchestrator uses the committer internally
committer = orchestrator.committer

# You can also use it directly
from forge_harness.iteration import CommitContext

context = CommitContext(
    features=["Feature A"],
    tests_added=5,
    iteration_number=1,
)
result = committer.commit(context)
```

### With PRGenerator

```python
# The orchestrator uses the PR generator internally
pr_gen = orchestrator.pr_generator

# You can also use it directly
from forge_harness.iteration import PRContext

context = PRContext(
    title="feat(backend): add authentication",
    features=["Add auth", "Add validation"],
    tests_added=10,
)
result = pr_gen.create_pr(context)
```

## Configuration Options

### Commit Behavior

```python
config = SessionConfig(
    project_name="my-project",
    auto_commit=True,              # Enable/disable auto-commit
    commit_scope="backend",        # Conventional commit scope
)
```

### PR Behavior

```python
config = SessionConfig(
    project_name="my-project",
    auto_pr=True,                  # Enable/disable auto-PR
    pr_draft=True,                 # Create as draft
    base_branch="develop",         # Target branch
)
```

## Examples

See `session_orchestrator_example.py` for complete working examples:
- Basic session with auto-commit
- Full workflow with auto-commit and PR
- Multiple iterations
- Failure handling and recovery

## Testing

Comprehensive tests are provided in `tests/test_session_orchestrator.py`:

```bash
# Run tests
uv run pytest tests/test_session_orchestrator.py -v

# With coverage
uv run pytest tests/test_session_orchestrator.py --cov=forge_harness.iteration.session_orchestrator
```

Test coverage: **100%** (25/25 tests passing)

## Architecture

The Session Orchestrator follows a clean architecture pattern:

1. **Configuration Layer** - `SessionConfig` defines behavior
2. **Orchestration Layer** - `SessionOrchestrator` coordinates components
3. **Component Layer** - `IterationTracker`, `AutoCommitter`, `PRGenerator`
4. **Result Layer** - `SessionResult` provides unified response

```
SessionOrchestrator
├── IterationTracker (tracks history)
├── AutoCommitter (creates commits)
└── PRGenerator (creates PRs)
```

## Factory Functions

Convenient factory functions are provided:

```python
from forge_harness.iteration import (
    create_session_orchestrator,
    create_tracker,
    create_auto_committer,
    create_pr_generator,
)

# Create orchestrator
orchestrator = create_session_orchestrator(
    project_name="my-project",
    auto_commit=True,
    auto_pr=False,
)

# Or create components individually
tracker = create_tracker()
committer = create_auto_committer()
pr_gen = create_pr_generator()
```

## Best Practices

1. **Always start iterations** - Call `start_iteration()` before doing work
2. **Handle failures** - Use `fail_iteration()` when something goes wrong
3. **Review stats** - Check `get_stats()` to monitor progress
4. **Configure wisely** - Enable auto-PR only for mature features
5. **Use draft PRs** - Set `pr_draft=True` for review before marking ready

## Error Handling

The orchestrator handles errors gracefully:

```python
result = orchestrator.complete_iteration(
    features=["Feature A"],
)

if not result.success:
    print(f"Error: {result.error}")
    # Iteration is automatically marked as failed
    # You can start a new iteration to recover
```

## Integration with FORGE Harness

The Session Orchestrator is designed to integrate with:
- Ralph Loop (autonomous feature development)
- Flywheel (compounding autonomous loops)
- Command Center (monitoring and control)
- Quality Gates (testing and validation)

## API Reference

### SessionOrchestrator

- `start_iteration() -> int` - Start new iteration, returns iteration number
- `complete_iteration(...) -> SessionResult` - Complete iteration with results
- `fail_iteration(reason: str) -> SessionResult` - Mark iteration as failed
- `get_stats() -> TrackerStats` - Get session statistics
- `get_recent_iterations(count: int) -> List[IterationRecord]` - Get recent iterations

### SessionConfig

- `project_name: str` - Project name (required)
- `base_branch: str` - Base branch for PRs (default: "main")
- `auto_commit: bool` - Enable auto-commit (default: True)
- `auto_pr: bool` - Enable auto-PR (default: False)
- `pr_draft: bool` - Create draft PRs (default: True)
- `commit_scope: str` - Commit scope (default: "harness")

### SessionResult

- `success: bool` - Operation success status
- `iteration_number: int` - Iteration number
- `features_completed: List[str]` - Completed features
- `commit_result: Optional[CommitResult]` - Commit result
- `pr_result: Optional[PRResult]` - PR result
- `stats: Optional[TrackerStats]` - Session statistics
- `error: Optional[str]` - Error message if failed
- `duration_seconds: float` - Operation duration

## License

Part of FORGE Harness - MIT License
