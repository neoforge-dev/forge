# Agent Rules Summary

_Single source of truth for agent type permissions. Referenced by `CLAUDE.md`, `dispatch-decision.md`, `git-conventions.md`, and `orchestrator-rules.md`._

## Master Permissions Table

| Action | Fleet Agent | Worktree Agent (Task tool) | Lead Orchestrator |
|--------|-------------|---------------------------|-------------------|
| `git commit` | **NEVER** | YES — assigned branch only | YES — main only |
| `git push` | **NEVER** | YES — immediately after commit | YES — after every commit |
| Dispatch to other agents (`forge dispatch send`) | **NEVER** | **NEVER** | YES |
| Create handoff documents | **NEVER** | **NEVER** | YES |
| `forge approval decide` | **NEVER** | **NEVER** | YES |
| `forge lane promote` | **NEVER** | **NEVER** | YES |
| Edit source code files | Only if dispatch explicitly permits | YES | **NEVER** — delegate |
| Create task (`forge task create`) | **NEVER** | **NEVER** | YES |

## Scope Boundaries

### Fleet Agent (`$FORGE_AGENT_TYPE=fleet`)
- Read dispatch file → execute task → write results → **STOP**
- Results go to `.forge/heartbeat/results/AGENTNAME-TASKID.md`
- **Scope ends** when result file is written

### Worktree Agent (Task tool / `EnterWorktree`)
- Make code change → run test command → report results
- **Scope ends** when acceptance criteria pass
- Commits to assigned branch (`feat/TASK-X`) only, then pushes immediately

### Lead Orchestrator (`$FORGE_AGENT_TYPE=orchestrator`)
- Plans, delegates, reviews, unblocks — does NOT write code
- Commits to `main` only; pushes immediately after each commit
- Context > 50% → `/handoff-clean`; Context > 75% → **HARD STOP** + `/handoff`

## Quick Reference: What To Use

| Need | Use |
|------|-----|
| Code change | Task tool (worktree) — 100% reliable |
| Fleet agent task | `forge dispatch send AGENT "Read .forge/dispatches/FILE.md — EXECUTE now"` |
| Cross-node work | `forge task create` — agents claim from queue |

_Last updated: 2026-03-19 (S120)_
