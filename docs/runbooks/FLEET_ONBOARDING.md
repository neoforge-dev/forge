# Fleet Agent Onboarding Runbook

**For:** New or re-initialized fleet agents joining the FORGE fleet.
**Time:** 2 minutes to read, then start working.

---

## Big Picture

FORGE is a multi-domain MVP portfolio with 11 domains and 95 products. We're in **Revenue Sprint S150** — $0 MRR, 3 products deploy-ready (Interview Simulator, Voice Coach, Study Flow), all blocked on human gates.

**Your job:** Execute tasks assigned to you. Write results. Don't commit.

---

## How It Works

```
Orchestrator creates task → You claim it → Execute → Write results → Orchestrator commits
```

### Dispatch Hierarchy (how you get work)

1. **Daemon mode** (PRIMARY): Run `forge work --daemon --interval 30s` — auto-polls queue, self-assigns tasks
2. **Direct dispatch**: Orchestrator sends you a task via `forge dispatch send` → read your dispatch file
3. **Task tool**: Orchestrator spawns you as a worktree agent for code changes

### Your Workflow

1. **Find your task**: `ls .forge/dispatches/` or check the task queue
2. **Execute**: Do the work described in the dispatch
3. **Write results** to: `.forge/heartbeat/results/{your-name}-{task-id}.md`
4. **Stop**: Your scope ends when results are written

### Result File Format

```markdown
## Status: COMPLETE

## Deliverables
- [x] What you did
- [x] Files created/modified

## Evidence
- Build: OK / Tests: PASS / etc.
```

---

## Rules (Non-Negotiable)

| DO | DON'T |
|----|-------|
| Write results to `.forge/heartbeat/results/` | NEVER `git commit` |
| Only modify files listed in your dispatch | NEVER `git push` |
| Use `Read`/`Grep`/`Glob` tools (not cat/grep/find) | NEVER dispatch to other agents |
| Run `go build`/`go test`/`pytest` to verify | NEVER edit source code unless dispatch says to |

---

## Key Files

| What | Where |
|------|-------|
| Your role + rules | `CLAUDE.md` (agent section) |
| Current sprint state | `docs/PROMPT-prya.md` |
| Revenue strategy | `docs/STRATEGY_REVENUE_SPRINT.md` |
| Dispatch decision tree | `forge-shared/modules/dispatch-decision.md` |
| Infrastructure map | `docs/INFRASTRUCTURE_MAP.md` |
| Domain context | `.forge/context/{domain}/lead-context.md` |

---

## Agent Capabilities

| Agent | Best For | Avoid |
|-------|----------|-------|
| claude | Primary implementation, hard bugs | — |
| gemini | Research, audits, architecture | Long code tasks |
| minimax | Implementation, docs, content | go-test |
| glm | Scaffolding, FastAPI services | go-test, ios |
| kimi | Coverage, triage, rapid fixes | Large refactors |
| pi | Fast triage, quick edits, voting | Heavy code |

---

## Current Blockers

- **Human gates** (50 min Bogdan): Stripe keys, Railway deploy, PostHog projects
- **No revenue**: All 3 Tier A products ready but not deployed
- **What you CAN do**: Docs, research, content, reviews, audits, test writing, code reviews

---

## Quick Start

```bash
# Check what's available
forge task list

# Claim a task
forge task claim TASK-ID

# Read your dispatch
cat .forge/dispatches/YOUR-DISPATCH.md

# When done, write results
# .forge/heartbeat/results/YOUR-NAME-TASK-ID.md
```
