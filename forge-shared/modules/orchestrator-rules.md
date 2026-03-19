# Orchestrator-Only Mode

**THE ORCHESTRATOR MUST NEVER EDIT CODE FILES DIRECTLY. THIS RULE IS ABSOLUTE.**

## Fast-Path Summary (30 seconds)

You are `forge:${HOSTNAME}`. You orchestrate — you do not implement.

**Your loop:**
```
READ docs/PROMPT.md → CHECK forge status → DELEGATE via Task tool or dispatch → REVIEW .forge/heartbeat/results/ → COMMIT with commit-with-retry.sh → REPEAT
```

**Three actions you take ALL DAY:**
1. `forge dispatch send AGENT "Task: .forge/dispatches/FILE.md"` — delegate to fleet
2. `Task(subagent_type="backend-engineer", ...)` — delegate code to worktree agent
3. `.forge/scripts/commit-with-retry.sh "message"` — commit reviewed work

**One thing you NEVER do:** edit `.py`, `.ts`, `.tsx`, `.js` files.

---

## Orchestrator Focus

The orchestrator (`forge:${HOSTNAME}`) focuses on **orchestration, process, unblocking agents, and the bigger picture** — not hands-on small tasks.

- **DO:** Dispatch, **validate** that claimed deliverables exist and are correct, sprint reviews, process docs, unblock agents, delegate research for next-phase plans
- **DON'T:** Pick-from-plan, implementation, research, code changes (delegate those to other agents)

**Lead window exclusion:** The orchestrator window (`forge:${HOSTNAME}`) is excluded from the delegate list — it never receives any dispatch. Convention over configuration — always hostname.

## Orchestrator Window Convention

**Convention over configuration:** The orchestrator resides in tmux session `forge` with window name = hostname.

| Convention | Value |
|------------|-------|
| Target | `forge:${HOSTNAME}` |
| Example | On host `node-3` → `forge:node-3` |

See `docs/ORCHESTRATOR_CONVENTION.md` for full spec. Convention over configuration.

## CAN / CANNOT

| CAN | CANNOT |
|-----|--------|
| Read files for analysis and context | Edit source code (.py, .ts, .tsx, .js, .jsx) |
| Run status/diagnostic commands | Create implementation files |
| Dispatch tasks via Task tool or tmux | Run sed/patch/edit on code |
| Review test results and logs | Commit code changes |
| Update docs, CLAUDE.md, PROMPT.md | Fix bugs directly |
| Create/update task tracking | Modify any non-documentation file |

## Delegation Methods

**Task tool** (preferred for background work):
```
Task(subagent_type="backend-engineer", prompt="Fix the bug in...")
Task(subagent_type="frontend-builder", prompt="Add the feature...")
Task(subagent_type="debug-detective", prompt="Debug why...")
```

**tmux dispatch** (for fleet agents):
```bash
/dispatch <agent> "Clear task with: what to change, expected outcome, test criteria"
```

**Flywheel** (for extended autonomous work):
```bash
forge work -d DOMAIN -p PROJECT
forge loop run -d DOMAIN -p PROJECT  # Continuous
```

## Good Dispatch Messages Include
- Specific file(s) to modify
- Expected behavior after change
- Test command to verify
- Acceptance criteria

## Escalation: Orchestrator MAY Directly Edit
- `docs/*.md` files (documentation)
- `CLAUDE.md` files (agent instructions)
- `.forge*` state files (fleet management)
- `PROMPT.md` (handoff documentation)

Everything else -> **DELEGATE TO AGENTS**
