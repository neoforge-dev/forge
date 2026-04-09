# FORGE Operator Playbook (Progressive Disclosure)

**Audience:** node leads, lead orchestrators, fleet agents (all providers).

**Goal:** predictable operations across nodes + agentic coders by making the *CLI the contract* and using skills/hooks only as “reasoning assist”.

**Status:** draft (assembled from canonical docs + live CLI v4 help). See **Sources** at end.

---

## Level 0 — 90 seconds to “I can operate this”

### The 3 control surfaces

```
            (Human chat)                 (Deterministic ops)
  Telegram/Discord/…  ──► OpenClaw ──►  forge CLI v4  ──►  v3 daemon / CC / workers
                                 │
                                 └──► (optional) tmux only for interactive approvals/restarts
```

1. **forge CLI v4** = canonical verbs/nouns for fleet, dispatch, queue, approvals.
2. **v3 daemon** (`cmd/forge-v3`) = backend API surface + websocket hub.
3. **Heartbeat/patrol loops** = automation that keeps the system moving and safe.

### The 5 commands you use first

```bash
forge status                     # Fleet + daemon health (use this; fleet status/health never shipped)
forge fleet list                  # Agent listing
forge node status                 # All-node heartbeat
forge node list                   # Cross-node mesh
qmd search "<topic>"
```

### The single safe way to delegate

```bash
forge dispatch send <agentId> "Task: …"   # inline
# or
forge dispatch send <agentId> --file .forge/dispatches/<task>.md
```

### Output contract (how work is “real”)

```
Dispatch in  : forge dispatch send …
Work happens : in the agent window
Results out  : .forge/heartbeat/results/<agent>-<topic|taskId>.md
```

---

## Level 1 — Roles & responsibilities (don’t improvise)

### Role map

```
                 ┌─────────────────────────────┐
                 │        Lead Orchestrator    │
                 │  - delegates + reviews      │
                 │  - commits/ships (if allowed)
                 └──────────────┬──────────────┘
                                │ dispatch/queue
                                ▼
     ┌──────────────────────────────────────────────────┐
     │                 Fleet Agents                      │
     │  - execute atomic tasks                            │
     │  - write results markdown                          │
     │  - do NOT commit/push/approve/dispatch-to-others    │
     └──────────────────────────────────────────────────┘
```

### Orchestrator “golden loop”

```
Observe → Decide → Delegate → Verify → Integrate → Checkpoint
```

Practical mapping:
- **Observe**: `forge status`, `forge fleet health`, `forge queue stats`
- **Decide**: check `docs/PROMPT.md` and `docs/PLAN.md`
- **Delegate**: `forge dispatch send …` (or queue-driven dispatch)
- **Verify**: read `.forge/heartbeat/results/*.md`, run tests/coverage
- **Integrate**: merge to main quickly (no long-lived branches)
- **Checkpoint**: handoff/notes + update docs that drift

### Fleet agent “definition of done”

A fleet agent is done when:
1. They produced a results markdown with: **what changed / evidence / remaining work / blockers**
2. They listed exact commands to reproduce (tests, build, etc.)

---

## Level 2 — The operating model (nodes, leads, cross-node)

### Node topology (current mental model)

```
            ┌───────────────────────┐
            │        prya           │
            │  hub / lead / CC      │
            └───────────┬───────────┘
                        │
          xnode/events  │  lead directives
                        │
   ┌──────────────┬─────┴───────┬──────────────┐
   │              │             │              │
┌──▼───┐       ┌──▼───┐      ┌──▼───┐       ┌──▼───┐
│ nova │       │ sati │      │ vega │       │ gaea │
│ iOS  │       │ cpu  │      │ aux  │       │ off  │
└──────┘       └──────┘      └──────┘       └──────┘
```

**Convention:** on each node, the orchestrator window is `forge:${HOSTNAME}`.

### Cross-node communication (preferred)

Use `forge lead …` for cross-node directives (see Canonical Workflow doc). `forge xnode` never existed as a top-level command.

In v4 operational reality: if `lead/xnode` subcommands are not present/complete, treat cross-node as:
- **queue + dispatch** routed through the hub
- or fallback to the documented xnode storage patterns (see canonical workflow)

**Operator rule:** never invent an interface. Use `forge --help` and `forge <noun> --help`.

---

## Level 3 — Automation: heartbeat, patrols, loops, and “endless operations”

### Heartbeat vs Patrols vs Ralph/Flywheel

```
Heartbeat loop (orchestrator)     Patrols (system)              Ralph/Flywheel
┌────────────────────────────┐   ┌─────────────────────────┐   ┌──────────────────────┐
│ picks next best work       │   │ enforce invariants       │   │ autonomous feature   │
│ delegates + checks results │   │ requeue/timeouts/ttl     │   │ loops (domain scoped)│
└────────────────────────────┘   └─────────────────────────┘   └──────────────────────┘
```

**Guiding principle:**
- Patrols keep the machine safe.
- Heartbeat keeps the machine productive.
- Flywheel/loop keeps a *single domain* moving when it’s already stable.

### The safe “endless loop” pattern

```
Every 2 minutes:
  1) Read fleet/queue health
  2) Pick ONE highest leverage task
  3) Delegate to ONE best-fit agent
  4) Wait for results file(s)
  5) Integrate (or queue follow-ups)
  6) Write checkpoint note
```

**Constraints (non-negotiable):**
- No raw tmux for dispatch.
- No code edits by orchestrator (delegate implementation).
- Keep loops *small-batch*: one wave at a time to avoid context rot + review debt.

### tmux send-keys: only for interactive prompts/restarts

If you must use tmux send-keys (approvals, restarts), follow the canonical race-free rule:

```
1) send the full multi-line text
2) wait ~0.1s
3) send Enter in a separate call
```

> Recommendation: we should wrap this behind a single `forge` subcommand so humans/agents never have to remember timing.

---

## Tooling inventory (progressive disclosure)

### A) CLI v4 nouns you should actually care about

- **fleet**: `forge fleet list|windows|spawn|kill`
- **agent**: `forge agent list|show|tasks`
- **dispatch**: `forge dispatch send|show|clean`
- **task**: `forge task list|show|create|claim|complete`
- **node**: `forge node list|status`
- **daemon**: `forge daemon status|start|stop|restart`

> ⚠️ **NOT YET SHIPPED:** `forge fleet status|health|broadcast`, `forge queue`, `forge approval` — Dark Factory commands that were planned but never implemented.

### B) Infrastructure / directories (what exists)

```
.forge/dispatches/              # task briefs (local)
.forge/heartbeat/results/       # results markdown (local)
.forge/context/                 # "royal jelly" persistent context
.forge_sessions/                # handoffs/sessions
.serena/                        # Serena project config (memories may be empty)
cmd/forge/                      # CLI v4 source
cmd/forge-v3/                   # v3 daemon source
harness/                        # legacy/python control plane + bridges
```

### C) TUI/dashboards

- `forge status` is the primary operator view.
- ⚠️ `forge status --watch` (TUI) was never shipped — do not use.
- Browser UI at `/ui` is the primary GUI.

---

## Best practices: skills & custom commands across multiple agentic coders

### First principles

1. **CLI-first contract**: any operation that changes state must be expressible as a CLI command.
2. **Skills = prompts, not infrastructure**: skills help thinking; they must never be the only way to do an operation.
3. **Single source of truth**: one canonical doc + one canonical generator.
4. **Progressive disclosure**: new agents should discover *just enough* to proceed.

### Recommended structure (portable across providers)

```
/docs/runbooks/                 # human/operator workflows
/docs/cli/                      # machine-verified CLI references (help snapshots)
/forge-shared/modules/          # durable "contracts" (dispatch, git, review, etc.)
/.claude/skills/                # project skills (Claude-specific)
# plus a CLI-based mirror:
forge skill list / forge skill show   # (proposed) to make skills discoverable everywhere
```

### Anti-patterns to kill

- "Use skill X" without a CLI fallback
- raw tmux dispatch as the primary delivery mechanism
- multiple competing “canonical” docs (v2 vs v4) without an archive policy

---

## Where we can simplify/streamline next (concrete proposals)

### 1) One canonical operator doc (and auto-lint for drift)
- Make this playbook the entry point and link out.
- Add a CI/local check that validates all docs examples against `forge --help` outputs.

### 2) Unify naming: task vs queue vs dispatch
Right now we conceptually have:
- dispatch (message → task)
- queue (tracking)
- task (work unit)

Proposal:
- pick **one noun** as the user-facing concept (likely `task`)
- keep internal queue/dispatch as implementation details

### 3) Provide a “safe send-keys” wrapper
If we still need tmux at all:
- implement `forge tmux send --multiline --enter-delay-ms 100`
- forbid direct `tmux send-keys` in docs (except in troubleshooting)

### 4) Skills/commands inventory generator
- create a generator that produces `docs/cli/SKILLS_COMMANDS_INVENTORY.md`
- sources:
  - `.claude/skills/**`
  - `.claude/hooks/**`
  - `forge-shared/modules/**`
  - `docs/**` references

### 5) Make QMD a first-class discovery step
- bake into operator loop: every “how do I …” begins with `qmd search`.

---

## Open questions / Council review requests

These should be decided by council (Kimi/Pi/Gemini/Codex reviews in flight):

1. Should we treat **bin/orchestrator-heartbeat.sh** as canonical, or re-home it under `forge patrol` / `forge heartbeat`?
2. Do we need to re-expose `forge lead/xnode` in CLI v4, or fully commit to queue/dispatch for cross-node?
3. What is the minimal set of “must know” docs for onboarding (<= 5)?

---

## Sources

- `docs/PROMPT.md` (current sprint + heartbeat status)
- `docs/runbooks/CANONICAL_WORKFLOW.md` (legacy v2 model, still useful concepts)
- `docs/TOOLING.md` (progressive disclosure tooling reference)
- `docs/ORCHESTRATOR_CONVENTION.md`
- `CLAUDE.md` (repo root; agent start commands + tmux send-keys rules)
