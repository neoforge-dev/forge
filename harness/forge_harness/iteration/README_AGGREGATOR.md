# Result Aggregator

Combines outputs from multiple parallel agents into a single coherent report with deduplication and conflict detection.

## Features

- **Multi-Agent Aggregation**: Combine results from 2+ agents into unified report
- **Status Grouping**: Group results by outcome (success, failure, partial, timeout, error)
- **Intelligent Deduplication**: Use similarity matching to identify common findings
- **Conflict Detection**: Highlight contradictory results and inconsistencies
- **Markdown Reports**: Generate comprehensive reports with agent attribution
- **Configurable Similarity**: Adjust threshold for finding deduplication (0.0-1.0)

## Installation

The aggregator is part of the `forge_harness.iteration` module:

```python
from forge_harness.iteration import ResultAggregator, AgentResult, ResultStatus
```

## Quick Start

### Basic Usage

```python
from forge_harness.iteration import ResultAggregator, AgentResult, ResultStatus

# Create aggregator
aggregator = ResultAggregator()

# Prepare agent results
results = [
    AgentResult(
        agent_id="backend-1",
        task_id="HRN-005",
        status=ResultStatus.SUCCESS,
        findings=["Fixed API bug", "Added tests"],
    ),
    AgentResult(
        agent_id="qa-1",
        task_id="HRN-005",
        status=ResultStatus.SUCCESS,
        findings=["All tests pass", "Coverage 95%"],
    ),
]

# Aggregate results
report = aggregator.aggregate(results)

# Print markdown report
print(report.to_markdown())

# Check metrics
print(f"Success Rate: {report.success_rate:.1f}%")
print(f"Common Findings: {len(report.common_findings)}")
print(f"Conflicts: {len(report.conflicts)}")
```

### Custom Similarity Threshold

```python
# Use stricter similarity threshold
aggregator = ResultAggregator(similarity_threshold=0.9)

# Use looser similarity threshold
aggregator = ResultAggregator(similarity_threshold=0.6)
```

### Disable Conflict Detection

```python
# Disable conflict detection for performance
aggregator = ResultAggregator(conflict_detection=False)
```

## Core Classes

### AgentResult

Represents a single agent's task execution result:

```python
AgentResult(
    agent_id="backend-1",           # Unique agent identifier
    task_id="HRN-005",              # Task identifier
    status=ResultStatus.SUCCESS,    # Result status
    findings=["Finding 1", "..."],  # List of findings/outputs
    errors=["Error 1", "..."],      # List of errors (optional)
    metadata={"key": "value"},      # Additional metadata (optional)
    started_at=datetime.now(utc),   # Start timestamp
    completed_at=datetime.now(utc), # Completion timestamp (optional)
    output="Full output text",      # Full output text (optional)
)
```

**Status Options:**
- `ResultStatus.SUCCESS` - Task completed successfully
- `ResultStatus.FAILURE` - Task failed completely
- `ResultStatus.PARTIAL` - Task partially completed
- `ResultStatus.TIMEOUT` - Task timed out
- `ResultStatus.ERROR` - Task encountered error
- `ResultStatus.UNKNOWN` - Status unknown

### AggregatedReport

The combined report from multiple agent results:

```python
report = aggregator.aggregate(results)

# Metrics
report.task_id                # Task identifier
report.total_agents           # Total number of agents
report.successful_agents      # Number of successful agents
report.failed_agents          # Number of failed agents
report.partial_agents         # Number of partially successful agents
report.success_rate           # Success rate as percentage
report.duration_seconds       # Total duration in seconds

# Findings
report.common_findings        # List[DuplicateFinding]
report.unique_findings        # Dict[agent_id, List[str]]

# Issues
report.conflicts              # List[Conflict]
report.errors                 # List[Dict[agent_id, error]]

# Grouping
report.results_by_status      # Dict[ResultStatus, List[AgentResult]]

# Export
report.to_markdown()          # Generate markdown report
report.to_dict()              # Convert to dictionary
```

### DuplicateFinding

Represents a finding reported by multiple agents:

```python
finding = report.common_findings[0]

finding.finding               # Canonical finding text
finding.agent_ids             # List of agents that reported it
finding.count                 # Number of agents (len(agent_ids))
finding.similarity_score      # Average similarity score (0.0-1.0)
finding.original_texts        # Original texts from each agent
```

### Conflict

Represents a conflict or inconsistency between results:

```python
conflict = report.conflicts[0]

conflict.severity             # ConflictSeverity enum
conflict.description          # Description of conflict
conflict.agent_ids            # Agents involved in conflict
conflict.details              # Additional details (dict)
```

**Severity Levels:**
- `ConflictSeverity.CRITICAL` - Contradictory results (e.g., success vs failure)
- `ConflictSeverity.WARNING` - Inconsistent but not contradictory
- `ConflictSeverity.INFO` - Minor differences

## Advanced Usage

### Handling Partial Failures

```python
results = [
    AgentResult(
        agent_id="agent-1",
        task_id="HRN-005",
        status=ResultStatus.SUCCESS,
        findings=["Task completed"],
    ),
    AgentResult(
        agent_id="agent-2",
        task_id="HRN-005",
        status=ResultStatus.FAILURE,
        errors=["Connection timeout"],
    ),
    AgentResult(
        agent_id="agent-3",
        task_id="HRN-005",
        status=ResultStatus.PARTIAL,
        findings=["Completed 50%"],
    ),
]

report = aggregator.aggregate(results)

# Check for critical conflicts
critical_conflicts = [
    c for c in report.conflicts
    if c.severity == ConflictSeverity.CRITICAL
]

if critical_conflicts:
    print("WARNING: Critical conflicts detected!")
    for conflict in critical_conflicts:
        print(f"  - {conflict.description}")
```

### Analyzing Common Findings

```python
report = aggregator.aggregate(results)

# Sort by number of agents reporting
common = sorted(report.common_findings, key=lambda x: x.count, reverse=True)

print("Most Common Findings:")
for finding in common[:5]:
    print(f"  {finding.count}x: {finding.finding}")
    print(f"     Similarity: {finding.similarity_score:.2%}")
```

### Exporting Results

```python
# Export to dictionary for JSON serialization
data = report.to_dict()
import json
with open("report.json", "w") as f:
    json.dump(data, f, indent=2)

# Export to markdown
markdown = report.to_markdown()
with open("report.md", "w") as f:
    f.write(markdown)
```

### Metadata Conflict Detection

```python
results = [
    AgentResult(
        agent_id="agent-1",
        task_id="HRN-005",
        status=ResultStatus.SUCCESS,
        metadata={"version": "1.0.0", "test_count": 39},
    ),
    AgentResult(
        agent_id="agent-2",
        task_id="HRN-005",
        status=ResultStatus.SUCCESS,
        metadata={"version": "1.0.1", "test_count": 39},
    ),
]

report = aggregator.aggregate(results)

# Check for metadata conflicts
metadata_conflicts = [
    c for c in report.conflicts
    if "metadata" in c.description.lower()
]

for conflict in metadata_conflicts:
    print(f"Conflict: {conflict.description}")
    print(f"Details: {conflict.details}")
```

## Similarity Matching

The aggregator uses Python's `difflib.SequenceMatcher` to calculate similarity between findings:

- **Score 1.0**: Identical text
- **Score 0.75+**: Very similar (default threshold)
- **Score 0.5-0.75**: Moderately similar
- **Score <0.5**: Different

Adjust the threshold based on your needs:

```python
# Strict matching (only very similar findings grouped)
aggregator = ResultAggregator(similarity_threshold=0.9)

# Loose matching (group more findings together)
aggregator = ResultAggregator(similarity_threshold=0.6)
```

## Performance Considerations

- **O(n²) complexity** for similarity matching, where n = total findings
- **Large result sets**: Consider higher similarity threshold to reduce comparisons
- **Memory usage**: ~1KB per finding + metadata
- **Disable conflict detection** if not needed for better performance

## Integration Examples

### With Dispatch System

```python
from forge_harness.iteration import (
    dispatch_tasks,
    ProgressMonitor,
    ResultAggregator,
    AgentResult,
)

# Dispatch tasks to multiple agents
dispatch_results = dispatch_tasks(["HRN-001", "HRN-002", "HRN-003"])

# Monitor progress
monitor = ProgressMonitor()
for result in dispatch_results:
    if result.success:
        monitor.register_session(result.session_id, task_id=result.task_id)

# Wait for completion...

# Collect results
agent_results = []
for result in dispatch_results:
    output = monitor.collect_output(result.session_id)
    agent_results.append(
        AgentResult(
            agent_id=result.agent_id,
            task_id=result.task_id,
            status=determine_status(output),
            findings=extract_findings(output),
        )
    )

# Aggregate
aggregator = ResultAggregator()
report = aggregator.aggregate(agent_results)
print(report.to_markdown())
```

### With Iteration Journal

```python
from forge_harness.iteration import (
    ResultAggregator,
    IterationJournal,
    EntryType,
)

# Aggregate results
report = aggregator.aggregate(results)

# Log to journal
journal = IterationJournal()
journal.add_entry(
    entry_type=EntryType.ASSESSMENT,
    content=f"Multi-agent task completed: {report.success_rate:.1f}% success",
    metadata={
        "task_id": report.task_id,
        "agents": report.total_agents,
        "success_rate": report.success_rate,
        "conflicts": len(report.conflicts),
    },
)

# Save report
journal.add_entry(
    entry_type=EntryType.REPORT,
    content=report.to_markdown(),
    metadata={"task_id": report.task_id},
)
```

## Testing

Run the test suite:

```bash
# Run all aggregator tests
uv run pytest tests/test_iteration/test_aggregator.py -v

# Run with coverage
uv run pytest tests/test_iteration/test_aggregator.py --cov=forge_harness.iteration.aggregator

# Run demo
python -m forge_harness.iteration.demo_aggregator
```

## Error Handling

The aggregator validates inputs and provides clear error messages:

```python
# Empty results list
try:
    report = aggregator.aggregate([])
except ValueError as e:
    print(e)  # "Results list cannot be empty"

# Inconsistent task IDs
try:
    results = [
        AgentResult(agent_id="a1", task_id="HRN-001", status="success"),
        AgentResult(agent_id="a2", task_id="HRN-002", status="success"),
    ]
    report = aggregator.aggregate(results)
except ValueError as e:
    print(e)  # "Results have inconsistent task IDs: {'HRN-001', 'HRN-002'}"

# Invalid similarity threshold
try:
    aggregator = ResultAggregator(similarity_threshold=1.5)
except ValueError as e:
    print(e)  # "similarity_threshold must be between 0.0 and 1.0"
```

## Best Practices

1. **Use consistent task IDs**: All results must have the same task_id
2. **Include timestamps**: Set started_at and completed_at for duration tracking
3. **Add metadata**: Include relevant context in metadata dict
4. **Descriptive findings**: Use clear, descriptive finding text for better deduplication
5. **Enable conflict detection**: Keep enabled unless performance is critical
6. **Tune similarity threshold**: Adjust based on your finding text patterns
7. **Check conflicts**: Always review conflicts in production use

## Examples

See `demo_aggregator.py` for complete working examples:

```bash
python -m forge_harness.iteration.demo_aggregator
```

Demos include:
1. Successful aggregation with deduplication
2. Partial failure handling
3. Conflict detection
4. Similarity threshold comparison
5. Many parallel agents (10+ agents)

## API Reference

### ResultAggregator

```python
class ResultAggregator:
    def __init__(
        self,
        similarity_threshold: float = 0.75,
        conflict_detection: bool = True,
    ):
        """Initialize result aggregator.

        Args:
            similarity_threshold: Minimum similarity for deduplication (0.0-1.0)
            conflict_detection: Enable conflict detection
        """

    def aggregate(
        self,
        results: list[AgentResult]
    ) -> AggregatedReport:
        """Aggregate results from multiple agents.

        Args:
            results: List of agent results to aggregate

        Returns:
            Aggregated report with deduplicated findings and conflicts

        Raises:
            ValueError: If results list is empty or has inconsistent task IDs
        """
```

## License

MIT License - Part of FORGE Harness
