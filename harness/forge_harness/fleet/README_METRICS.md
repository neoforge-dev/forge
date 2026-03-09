# Fleet Metrics System

Track agent utilization, success rates, and performance over time for the FORGE Harness fleet.

## Features

- **Task Lifecycle Tracking** - Record start/end times for every agent task
- **Success/Failure Rates** - Track outcomes and calculate success metrics
- **Agent Utilization** - Measure active time vs. total time per agent
- **Task Type Analysis** - Compare performance across different task types
- **Time-Series Queries** - Filter metrics by time period (last 7 days, 30 days, etc.)
- **SQLite Storage** - Persistent metrics storage with efficient indexing
- **JSON Export** - Export reports for external analysis

## Quick Start

```python
from forge_harness.fleet.metrics import create_fleet_metrics

# Initialize metrics system
metrics = create_fleet_metrics()

# Record task lifecycle
metrics.record_task_start("agent-1", "task-123", task_type="feature")
# ... task execution ...
metrics.record_task_end("task-123", outcome="success")

# Get aggregate metrics
report = metrics.get_metrics(days=7)
print(f"Success rate: {report.success_rate:.1%}")
print(f"Avg duration: {report.avg_duration_seconds:.1f}s")

# Get per-agent metrics
agent_metrics = metrics.get_agent_metrics("agent-1")
print(f"Utilization: {agent_metrics.utilization:.1%}")
```

## Database Schema

The metrics system uses a SQLite database (`.forge/metrics.db`) with the following schema:

```sql
CREATE TABLE task_events (
    task_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_type TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT,
    duration_seconds REAL,
    metadata TEXT
);

-- Indexes for efficient queries
CREATE INDEX idx_agent_started ON task_events(agent_id, started_at);
CREATE INDEX idx_task_type ON task_events(task_type);
CREATE INDEX idx_started_at ON task_events(started_at);
CREATE INDEX idx_outcome ON task_events(outcome);
```

## API Reference

### FleetMetrics

Main class for metrics tracking.

#### Methods

##### `record_task_start(agent_id, task_id, task_type=None, metadata=None)`

Record task start event.

**Parameters:**
- `agent_id` (str): Agent identifier
- `task_id` (str): Unique task identifier
- `task_type` (str, optional): Type of task (e.g., "feature", "bugfix", "refactor")
- `metadata` (dict, optional): Additional metadata to store

**Returns:** `bool` - True if successful

**Example:**
```python
metrics.record_task_start(
    agent_id="agent-1",
    task_id="task-123",
    task_type="feature",
    metadata={"priority": "high", "domain": "voice-coach"}
)
```

##### `record_task_end(task_id, outcome, metadata=None)`

Record task completion.

**Parameters:**
- `task_id` (str): Task identifier
- `outcome` (str): Task outcome ("success", "failure", "cancelled")
- `metadata` (dict, optional): Additional metadata (merged with start metadata)

**Returns:** `bool` - True if successful

**Example:**
```python
metrics.record_task_end(
    task_id="task-123",
    outcome="success",
    metadata={"lines_changed": 150, "files_modified": 5}
)
```

##### `get_metrics(days=7)`

Get aggregate metrics for time period.

**Parameters:**
- `days` (int): Number of days to look back (default: 7)

**Returns:** `MetricsReport` - Aggregate metrics

**Example:**
```python
report = metrics.get_metrics(days=30)
print(f"Total tasks: {report.total_tasks}")
print(f"Success rate: {report.success_rate:.1%}")
print(f"Task types: {report.task_types}")
```

##### `get_agent_metrics(agent_id, days=7)`

Get metrics for specific agent.

**Parameters:**
- `agent_id` (str): Agent identifier
- `days` (int): Number of days to look back (default: 7)

**Returns:** `AgentMetrics` - Per-agent metrics

**Example:**
```python
agent_metrics = metrics.get_agent_metrics("agent-1", days=7)
print(f"Total tasks: {agent_metrics.total_tasks}")
print(f"Success rate: {agent_metrics.success_rate:.1%}")
print(f"Utilization: {agent_metrics.utilization:.1%}")
print(f"Avg duration: {agent_metrics.avg_duration_seconds:.1f}s")
```

##### `get_task_type_metrics(task_type, days=7)`

Get metrics for specific task type.

**Parameters:**
- `task_type` (str): Task type identifier
- `days` (int): Number of days to look back (default: 7)

**Returns:** `TaskTypeMetrics` - Per-task-type metrics

**Example:**
```python
task_metrics = metrics.get_task_type_metrics("feature", days=7)
print(f"Total: {task_metrics.total_tasks}")
print(f"Success rate: {task_metrics.success_rate:.1%}")
print(f"Avg duration: {task_metrics.avg_duration_seconds:.1f}s")
```

##### `list_active_agents(days=7)`

List agents active in time period.

**Returns:** `List[str]` - List of agent IDs

##### `list_task_types(days=7)`

List task types in time period.

**Returns:** `List[str]` - List of task types

##### `cleanup_old_metrics(days=90)`

Remove metrics older than specified days.

**Parameters:**
- `days` (int): Keep metrics from last N days (default: 90)

**Returns:** `int` - Number of records deleted

### Data Classes

#### MetricsReport

Aggregate metrics for a time period.

**Attributes:**
- `total_tasks` (int): Total number of tasks
- `completed_tasks` (int): Successfully completed tasks
- `failed_tasks` (int): Failed tasks
- `in_progress_tasks` (int): Tasks still in progress
- `success_rate` (float): Success rate (0.0 to 1.0)
- `avg_duration_seconds` (float): Average task duration
- `total_agents` (int): Number of active agents
- `period_start` (datetime): Start of measurement period
- `period_end` (datetime): End of measurement period
- `task_types` (Dict[str, int]): Task type counts

#### AgentMetrics

Per-agent metrics.

**Attributes:**
- `agent_id` (str): Agent identifier
- `total_tasks` (int): Total tasks
- `completed_tasks` (int): Completed tasks
- `failed_tasks` (int): Failed tasks
- `success_rate` (float): Success rate
- `avg_duration_seconds` (float): Average duration
- `utilization` (float): Active time / total time
- `active_time_seconds` (float): Total active time
- `total_time_seconds` (float): Total time in period
- `first_task` (datetime): First task timestamp
- `last_task` (datetime): Last task timestamp

#### TaskTypeMetrics

Per-task-type metrics.

**Attributes:**
- `task_type` (str): Task type
- `total_tasks` (int): Total tasks
- `completed_tasks` (int): Completed tasks
- `failed_tasks` (int): Failed tasks
- `success_rate` (float): Success rate
- `avg_duration_seconds` (float): Average duration

## CLI Usage

```bash
# View metrics for last 7 days
forge-harness fleet metrics

# View metrics for specific agent
forge-harness fleet metrics --agent agent-1

# View metrics for last 30 days
forge-harness fleet metrics --days 30

# View metrics for specific task type
forge-harness fleet metrics --type feature

# Export metrics to JSON
forge-harness fleet metrics --export metrics.json
```

## Integration Examples

### Ralph Loop Integration

```python
from forge_harness.fleet.metrics import create_fleet_metrics
from forge_harness.ralph_loop import create_ralph_loop

metrics = create_fleet_metrics()

class MetricsRalphLoop:
    def __init__(self):
        self.metrics = metrics
        self.loop = create_ralph_loop()

    async def run_iteration(self, feature):
        task_id = f"ralph-{feature['id']}"

        # Record start
        self.metrics.record_task_start(
            agent_id="ralph",
            task_id=task_id,
            task_type="feature",
            metadata={
                "feature_id": feature["id"],
                "domain": feature.get("domain"),
            }
        )

        try:
            # Run iteration
            result = await self.loop.iterate(feature)

            # Record success
            self.metrics.record_task_end(
                task_id=task_id,
                outcome="success" if result.success else "failure",
                metadata={
                    "tests_passed": result.tests_passed,
                    "iterations": result.iterations,
                }
            )
        except Exception as e:
            # Record failure
            self.metrics.record_task_end(
                task_id=task_id,
                outcome="failure",
                metadata={"error": str(e)}
            )
```

### Fleet Dashboard Integration

```python
from forge_harness.fleet.metrics import create_fleet_metrics
from forge_harness.fleet.dashboard import get_fleet_status

def show_fleet_with_metrics():
    # Get current fleet status
    fleet = get_fleet_status()

    # Get metrics for each agent
    metrics = create_fleet_metrics()

    for agent in fleet.agents:
        agent_metrics = metrics.get_agent_metrics(agent.agent_id, days=7)

        print(f"\n{agent.name}:")
        print(f"  Status: {agent.status}")
        print(f"  Tasks (7d): {agent_metrics.total_tasks}")
        print(f"  Success rate: {agent_metrics.success_rate:.1%}")
        print(f"  Utilization: {agent_metrics.utilization:.1%}")
```

### Monitoring and Alerts

```python
from forge_harness.fleet.metrics import create_fleet_metrics

def check_fleet_health():
    metrics = create_fleet_metrics()
    report = metrics.get_metrics(days=1)  # Last 24 hours

    # Alert on low success rate
    if report.success_rate < 0.5:
        send_alert(
            severity="high",
            message=f"Fleet success rate dropped to {report.success_rate:.1%}"
        )

    # Alert on low agent utilization
    for agent_id in metrics.list_active_agents(days=1):
        agent_metrics = metrics.get_agent_metrics(agent_id, days=1)

        if agent_metrics.utilization < 0.1:
            send_alert(
                severity="low",
                message=f"Agent {agent_id} utilization only {agent_metrics.utilization:.1%}"
            )
```

## Best Practices

### 1. Consistent Task IDs

Use consistent task ID formats for easier tracking:

```python
# Good
task_id = f"{agent_id}-{timestamp}-{feature_id}"

# Bad
task_id = "task123"  # No context
```

### 2. Meaningful Task Types

Use descriptive task types that align with your workflow:

```python
# Examples
task_type = "feature"     # New feature development
task_type = "bugfix"      # Bug fix
task_type = "refactor"    # Code refactoring
task_type = "test"        # Test writing
task_type = "docs"        # Documentation
```

### 3. Rich Metadata

Add metadata to tasks for better analysis:

```python
metrics.record_task_start(
    agent_id="agent-1",
    task_id="task-123",
    task_type="feature",
    metadata={
        "priority": "high",
        "domain": "voice-coach",
        "project": "voice-coach-app",
        "issue_id": "IS-042",
        "estimated_hours": 2.5,
    }
)

metrics.record_task_end(
    task_id="task-123",
    outcome="success",
    metadata={
        "lines_changed": 150,
        "files_modified": 5,
        "actual_hours": 2.0,
        "tests_added": 12,
    }
)
```

### 4. Regular Cleanup

Schedule regular cleanup to prevent database bloat:

```python
import schedule

def cleanup_old_metrics():
    metrics = create_fleet_metrics()
    deleted = metrics.cleanup_old_metrics(days=90)
    logger.info(f"Cleaned up {deleted} old metric records")

# Run daily at 2 AM
schedule.every().day.at("02:00").do(cleanup_old_metrics)
```

### 5. Error Handling

Always handle errors when recording metrics:

```python
try:
    metrics.record_task_start("agent-1", "task-123")
    # ... task execution ...
    metrics.record_task_end("task-123", outcome="success")
except Exception as e:
    logger.error(f"Failed to record metrics: {e}")
    # Continue with task execution
```

## Performance Considerations

- **Database Location**: Store `.forge/metrics.db` on fast local storage
- **Batch Operations**: For bulk imports, use transactions
- **Index Maintenance**: SQLite automatically maintains indexes
- **Query Optimization**: Use appropriate time ranges for queries
- **Cleanup Schedule**: Run cleanup during low-activity periods

## Troubleshooting

### Database locked errors

If you encounter database locked errors, ensure connections are properly closed:

```python
# Good - using context manager
with metrics._get_connection() as conn:
    cursor = conn.cursor()
    # ... operations ...

# Bad - manual connection management
conn = sqlite3.connect(metrics.db_path)
# ... might not close properly ...
```

### Missing metrics

Check that tasks are being recorded with both start and end:

```python
# Verify task was started
report = metrics.get_metrics(days=1)
print(f"In progress: {report.in_progress_tasks}")

# Check specific task
with metrics._get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM task_events WHERE task_id = ?", ("task-123",))
    print(cursor.fetchone())
```

### Inaccurate utilization

Utilization is calculated as: `active_time / total_time_in_period`

Ensure:
- Tasks are properly ended (not left in progress)
- Time period matches your analysis window
- Agent was actually active during the period

## Related Documentation

- [Fleet Dashboard](README.md) - Real-time fleet monitoring
- [Context Rotation](README.md#context-rotation) - Automatic context management
- [Ralph Loop Guide](../../docs/RALPH_LOOP_GUIDE.md) - Autonomous feature loop
- [CLI Reference](../../docs/CLI_REFERENCE.md) - Full CLI documentation
