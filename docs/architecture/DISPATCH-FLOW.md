<!-- Owner: prya | Review-after: 2026-06-25 -->
<!-- Trigger: changes to dispatch.go or handlers_task.go -->
<!-- See also: MULTI_AGENT_ORCHESTRATION.md, forge-shared/modules/dispatch-decision.md -->

# Dispatch Flow

How tasks move from orchestrator intent to agent execution to committed results.

## Primary Flow: Queue-Based (99% reliable)

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant D as forged daemon
    participant DB as SQLite
    participant A as Fleet Agent
    participant R as Results
    participant G as Git

    O->>D: POST /api/tasks (create)
    D->>DB: INSERT task (state=QUEUED)
    D-->>O: task ID

    loop Agent polls every ~30s
        A->>D: GET /api/tasks/claimable
        D->>DB: SELECT unclaimed tasks
        D-->>A: task list
    end

    A->>D: POST /api/tasks/{id}/claim
    D->>DB: UPDATE state=DISPATCHED
    D-->>A: task payload

    A->>A: Execute task

    loop During execution
        A->>D: POST /api/agents/{id}/heartbeat
    end

    A->>R: Write .forge/heartbeat/results/{agent}-{taskid}.md
    A->>D: POST /api/tasks/{id}/complete
    D->>DB: UPDATE state=COMPLETED

    O->>R: Read results
    O->>G: git add + commit + push
```

## Secondary Flow: Direct Dispatch (specific agent needed)

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant T as tmux
    participant A as Named Agent

    O->>O: Write .forge/dispatches/{agent}-{taskid}.md
    O->>T: tmux send-keys -t forge:{agent}
    T->>A: Message delivered

    A->>A: Read dispatch file
    A->>A: Execute task
    A->>A: Write .forge/heartbeat/results/{agent}-{taskid}.md

    O->>O: Read results
    O->>O: git commit
```

## Worktree Flow: Code Changes (100% reliable)

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant W as Worktree Agent
    participant B as Git Branch

    O->>W: Agent tool (isolation: worktree)
    W->>B: Create feat/TASK-{id} branch
    W->>W: Make code changes
    W->>W: Run tests
    W-->>O: Return results + branch name

    O->>B: Review changes
    O->>O: Merge to main + push
```

## Dispatch Decision Tree

| Task Type | Method | Reliability |
|-----------|--------|-------------|
| Routine fleet work | Queue (`forge task create`) | ~99% |
| Code changes | Worktree (`Agent` tool) | 100% |
| Named agent override | `forge dispatch send forge:AGENT` | ~95% |
| Research/analysis | Queue or direct dispatch | ~99% |

## Related Docs

- **Dispatch decision module:** `forge-shared/modules/dispatch-decision.md`
- **Task FSM:** `docs/architecture/TASK-FSM.md`
- **System map:** `docs/architecture/SYSTEM-MAP.md`
- **Agent orchestration:** `docs/architecture/MULTI_AGENT_ORCHESTRATION.md`
