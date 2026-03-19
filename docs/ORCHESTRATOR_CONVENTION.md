# Orchestrator Convention

**Convention over configuration:** The orchestrator is the agent that resides in the tmux session `forge` with window name **equal to the hostname**.

---

## Role & Focus

The orchestrator (`forge:${HOSTNAME}`) must focus on:

| Focus | Examples |
|-------|----------|
| **Orchestration** | Dispatch work, assign tasks, balance load |
| **Process** | Sprint reviews, handoffs, workflow improvements |
| **Unblocking** | Identify blockers, nudge idle agents, verify deliverables |
| **Big picture** | Priorities, PLAN alignment, fleet health |

The orchestrator should **NOT** take hands-on implementation tasks (e.g. pick-from-plan, code changes, research). Those go to other agents. The orchestrator delegates; it does not execute small tasks.

**Lead window = orchestration only:** The heartbeat **never** dispatches to the orchestrator window (`forge:${HOSTNAME}`). That window is excluded from the delegate list entirely — no trivial tasks, no pick-from-plan. Convention over configuration: **always** forge:${HOSTNAME}.

---

## Rule

| Convention | Value |
|------------|-------|
| **Session** | `forge` |
| **Window** | `$HOSTNAME` (hostname of the machine) |
| **Orchestrator target** | `forge:${HOSTNAME}` |

**Example:** On host `node-3`, the orchestrator window is `forge:node-3`.

---

## Rationale

- **Portable:** No config file; same setup on any machine
- **Single source of truth:** Hostname identifies the orchestrator node
- **Multi-node:** In a multi-machine fleet, each host runs its orchestrator in `forge:${HOSTNAME}`

---

## Enforcement

Scripts that reference the orchestrator window MUST use:

```bash
ORCHESTRATOR_WINDOW="$HOSTNAME"
ORCHESTRATOR_TARGET="forge:${ORCHESTRATOR_WINDOW}"
```

Convention over configuration — no override.

---

## Where This Applies

| Component | Usage |
|-----------|-------|
| `forge up --tmux` | Creates/uses window `forge:${HOSTNAME}`; starts daemon + agents |
| `forge heartbeat eval` | Evaluates session counter; run in a loop for orchestrator monitoring |
| `docs/PROMPT.md` | Fleet status: orchestrator = `forge:${HOSTNAME}` |
| `forged` daemon | Orchestrator agent ID = `forge:${HOSTNAME}` |

---

## Migration from `forge:heartbeat`

Legacy: some docs referenced `forge:heartbeat`. Replaced by `forge:${HOSTNAME}` per this convention.
