# FORGE Harness Agent Selector

Intelligent agent selection for task routing based on keyword analysis and weighted scoring.

## Overview

The Agent Selector analyzes task descriptions and automatically selects the most appropriate agent based on:
- Keyword matching with configurable weights
- Confidence scoring
- Alternative agent suggestions
- Human-readable reasoning

## Available Agents

| Agent | Command | Best For |
|-------|---------|----------|
| **CODEX** | `codex exec` | Quick code generation, tests, frontend, React, TypeScript |
| **OPENCODE** | `opencode run` | Backend APIs, Python services, FastAPI, databases |
| **GEMINI** | `gemini -y` | Research, content, documentation, analysis |
| **CLAUDE** | `claude` | Architecture, complex bugs, refactoring, planning |

## Quick Start

```python
from forge_harness.agents import select_agent, analyze_task

# Simple selection
task = {
    "name": "Create React login component",
    "description": "Build a login form with validation"
}
agent = select_agent(task)
print(agent)  # AgentType.CODEX

# Detailed analysis
match = analyze_task(task)
print(f"Agent: {match.agent.name}")
print(f"Score: {match.score:.1f}")
print(f"Confidence: {match.confidence}")
print(f"Keywords: {match.matched_keywords}")
print(f"Reasoning: {match.reasoning}")
```

## Usage Examples

### Frontend/UI Tasks

```python
task = {
    "name": "Fix CSS layout",
    "description": "Fix responsive layout issues in the dashboard",
    "tags": ["frontend", "ui", "css"]
}
agent = select_agent(task)
# Returns: AgentType.CODEX
```

### Backend/API Tasks

```python
task = {
    "name": "Create user API",
    "description": "Build REST API endpoints for user management",
    "domain": "backend"
}
agent = select_agent(task)
# Returns: AgentType.OPENCODE
```

### Research Tasks

```python
task = {
    "name": "Research competitors",
    "description": "Investigate and analyze competitor pricing"
}
agent = select_agent(task)
# Returns: AgentType.GEMINI
```

### Complex Tasks

```python
task = {
    "name": "Design system architecture",
    "description": "Design microservices architecture for scaling"
}
agent = select_agent(task)
# Returns: AgentType.CLAUDE
```

## Custom Configuration

### Custom Keyword Mappings

```python
from forge_harness.agents import AgentSelector, AgentType

custom_mappings = {
    AgentType.CODEX: {
        "svelte": 2.0,
        "solid": 2.0,
    },
    AgentType.OPENCODE: {
        "graphql": 2.0,
        "grpc": 2.0,
    },
    # ... other agents
}

selector = AgentSelector(keyword_mappings=custom_mappings)
agent = selector.select_agent(task)
```

### Dynamic Keyword Management

```python
selector = AgentSelector()

# Add new keyword
selector.add_keyword(AgentType.GEMINI, "podcast", 2.0)

# Remove keyword
selector.remove_keyword(AgentType.CODEX, "react")

# Use modified selector
agent = selector.select_agent(task)
```

### Custom Default Agent

```python
# Use GEMINI as default for ambiguous tasks
selector = AgentSelector(default_agent=AgentType.GEMINI)
```

## Keyword Weights

Weights determine the strength of keyword matches:

- **2.0**: Strong indicator (e.g., "frontend", "backend", "research")
- **1.5**: Moderate indicator (e.g., "test", "python", "write")
- **1.0**: Weak indicator (e.g., "fix", "issue")

## Confidence Levels

- **High**: Winner score > 3.0 points ahead of second place
- **Medium**: Winner score > 1.0 points ahead of second place
- **Low**: Winner score < 1.0 ahead, or total score < 1.0

## Integration

### With Task Queue

```python
from forge_harness.agents import select_agent
from forge_harness.task_queue import TaskQueue

queue = TaskQueue()
task = queue.get_next_task()

# Auto-select agent
agent = select_agent(task)
command = f"{agent.command} '{task['description']}'"

# Execute
import subprocess
subprocess.run(command, shell=True, cwd=task['project_path'])
```

### With Orchestration Harness

```python
from forge_harness.agents import AgentSelector
from forge_harness.orchestration_harness import OrchestrationHarness

selector = AgentSelector()
harness = OrchestrationHarness()

for task in harness.get_pending_tasks():
    match = selector.analyze_task(task)
    
    print(f"Task: {task['name']}")
    print(f"Selected: {match.agent.name}")
    print(f"Reasoning: {match.reasoning}")
    
    if match.confidence == "low":
        print(f"⚠️  Consider alternatives: {match.alternatives}")
```

## Word Boundary Matching

The selector uses word boundary matching to avoid false positives:

```python
# "api" in "capital" won't match
task = {"name": "Capital growth", "description": "Rapid expansion"}
match = analyze_task(task)
assert "api" not in match.matched_keywords

# "api" as whole word will match
task = {"name": "Build API", "description": "REST API endpoints"}
match = analyze_task(task)
assert "api" in match.matched_keywords
```

## Testing

Run the test suite:

```bash
cd /Users/bogdan/work/FORGE/harness
uv run pytest tests/test_agent_selector.py -v
```

## Architecture

The selector follows these steps:

1. **Extract Text**: Combine all task fields (name, description, tags, etc.)
2. **Score Agents**: Match keywords with word boundaries, sum weights
3. **Rank Results**: Sort agents by total score
4. **Calculate Confidence**: Based on score gap between top agents
5. **Generate Reasoning**: Explain selection with matched keywords

## Future Enhancements

- Machine learning-based selection
- Historical performance tracking
- Agent specialization profiles
- Dynamic weight adjustment based on outcomes
- Multi-agent task decomposition
