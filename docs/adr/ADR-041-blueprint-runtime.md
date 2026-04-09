# ADR-041: Blueprint Runtime for Durable Task Orchestration

**Date:** 2026-03-10
**Status:** ACCEPTED (council vote 3-0, 2026-03-14; implementation complete 2026-03-17)
**Decision Makers:** Bogdan Veliscu (CTO), council
**Extends:** ADR-024 (Git Worktree Isolation), ADR-028 (Task FSM), ADR-040 (Final CLI Consolidation)

---

## Context

FORGE already has strong low-level building blocks:

- durable tasks and task state transitions
- worktree isolation primitives
- deterministic validation commands
- agent dispatch
- approvals and review checkpoints
- a daemon-backed control plane

What it does not have is a clean orchestration primitive between:

- "a task exists in the system"
- "an agent received a giant custom prompt"

Today, too much execution policy lives in prompts, habits, and doc fragments:

- "run cheap checks first"
- "use the right agent"
- "stop retrying after N failures"
- "collect evidence before review"

That makes execution noisy, harder to repeat, and harder for new humans or agents to understand. The existing `forge workflow` surface is a generic shell runner; it is not durable, task-aware, or expressive enough to be the canonical execution model for FORGE.

The right next step is not to copy Stripe's infrastructure wholesale. It is to encode the best parts of the workflow as a thin runtime on top of FORGE's existing task system.

---

## Decision

FORGE will introduce a **Blueprint Runtime v1** as the canonical orchestration layer for encoded task execution.

### 1. Blueprint Runtime v1 is task-linked, linear, and resumable

A blueprint is a typed sequence of steps attached to a task. Blueprint runs must be durable and resumable after interruption or partial failure.

Blueprint v1 is intentionally limited:

- linear step sequence
- explicit step states
- resume from the first incomplete step
- no general DAG branching in v1

### 2. Blueprint step types are narrow and explicit

Blueprint v1 supports these step types:

- `check`
  - run deterministic validation such as `forge check`
- `dispatch`
  - send work to an agent or selected bundle
- `review`
  - wait for or request human/council review
- `complete`
  - attach evidence and transition the task forward
- `shell`
  - narrow escape hatch for deterministic commands only

All additional step types require a follow-up ADR or an explicit amendment to this ADR.

### 3. Blueprints are defined in repo configuration

Canonical blueprint definitions live under:

`config/blueprints/`

This keeps execution policy versioned with the repo and discoverable by both humans and agents.

### 4. The public CLI surface is minimal

Blueprint v1 adds these canonical commands:

- `forge blueprint validate <file-or-id>`
- `forge blueprint run --task <task-id> <blueprint-id>`

Optional read-only inspection commands may be added if needed, but the goal is to keep the initial surface small.

### 5. Blueprint runs persist as first-class runtime state

Blueprint execution state must not live only in transient logs.

The persistence model is:

- dedicated blueprint run records for runtime state
- dedicated step records for per-step execution state
- mirrored task/plan events for audit visibility

This separates execution bookkeeping from task state while preserving a readable audit trail.

### 6. Deterministic steps run in code, not by prompt convention

Checks, evidence packaging, and similar deterministic behavior must be encoded in the runtime rather than repeated in prose prompts.

The blueprint runtime is responsible for:

- executing deterministic steps
- persisting step results
- stopping on explicit failure conditions
- resuming safely

### 7. Agent loops stay small

Agent steps are still valuable, but they must be bounded by the blueprint rather than acting as the workflow engine themselves.

The runtime decides:

- when an agent step starts
- what evidence must exist before and after it
- when review is required
- when the task can complete

### 8. The first canonical blueprint is `coding/default`

The first production blueprint should target the most common execution path:

- cheap deterministic checks
- agent implementation dispatch
- review/evidence packaging
- task completion transition

This gives FORGE the fastest path from giant prompts to encoded workflow.

---

## Non-Goals

This ADR does not authorize or require:

- a general DAG orchestration engine
- container/devbox orchestration
- large centralized tool servers
- nested autonomous orchestration loops
- another top-level operator surface beyond `forge`

This ADR also does not settle node heartbeat semantics. Node participation and heartbeat should be handled separately from blueprint execution.

---

## Consequences

### Positive

- Common task execution becomes more deterministic and less prompt-driven.
- FORGE gains a clear middle layer between task state and agent sessions.
- Deterministic checks and evidence collection move into code where they belong.
- Humans and agents can inspect and resume execution from durable runtime state.
- The system becomes easier to onboard because the workflow is encoded, not memorized.

### Negative

- FORGE gains a new runtime subsystem that must be designed carefully.
- Schema and daemon changes are required to persist blueprint runs and steps.
- There is short-term migration cost while existing prompt-heavy flows move to blueprints.
- A poorly scoped blueprint model could become another confusing surface if the CLI and docs drift again.

### Neutral

- Existing ad hoc task execution can continue temporarily while canonical blueprints are introduced.
- The old `workflow` surface may remain during transition, but it should no longer be treated as the long-term orchestration model.
- Guidance bundles, execution profiles, and node heartbeat can evolve independently on top of this runtime.

---

## Implementation Notes

The expected implementation direction is:

1. keep Blueprint Runtime v1 linear and resumable
2. reuse existing task FSM and worktree primitives
3. encode deterministic checks first
4. attach evidence at completion as a first-class artifact
5. avoid broadening the public CLI until the runtime contract is stable

The first implementation should prefer a small end-to-end slice over a broad but incomplete framework.

