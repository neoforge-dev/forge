---
description: Git workflow and commit conventions
globs:
  - "**/*"
---

# Git Conventions

_Quick reference for agents. Full workflows (conflict resolution, submodule handling, multi-node push): `forge-shared/modules/git-workflow.md`_

## Commit Rules by Agent Type

| Agent type | Commit? | Where? | Push? |
|---|---|---|---|
| **Fleet agent** | **NEVER** | — | **NEVER** |
| **Worktree agent (Task tool)** | YES | Assigned branch only (e.g., feat/TASK-X) | YES — immediately |
| **Lead orchestrator** | YES | main only | YES — after every commit |

**No long-lived feature branches.** Merge to main and push within the same session.

## Conventional Commits

Use conventional commit format:

```bash
# Format
type(scope): subject

body (optional)

footer (optional)
```

### Types

| Type | Use For |
|------|---------|
| `feat` | New features |
| `fix` | Bug fixes |
| `docs` | Documentation changes |
| `style` | Formatting, semicolons, etc. |
| `refactor` | Code restructuring |
| `test` | Adding tests |
| `chore` | Build process, dependencies |

### Examples

```bash
# Feature
feat(forged): add dispatch hygiene patrol

# Bug fix
fix(patrol): repair zombie patrol test boundary condition

# Documentation
docs: update PROMPT.md with S119 progress

# Chore/cleanup
chore: delete 786 dead harness fleet files (553K lines)

# Test
test: add 37 coverage tests for completion, openclaw, fleet scaler

# Refactor
refactor(forged): consolidate handler method validation wave tests
```

## Commit-Pull-Push Sequence (Council TC-S158)

**Always follow this exact sequence:**

```bash
git commit -m "feat: add new feature"
git pull --rebase origin main   # sync with remote AFTER commit
git push origin main
```

**Why:** Committing first saves your work safely. Pulling after commit means any rebase conflicts happen with a clean commit to fall back to. Never `pull --rebase` before commit — it introduces upstream changes into uncommitted work.

**If push is rejected:** `git pull --rebase origin main` then retry push. Resolve conflicts against your committed work, not uncommitted changes.

## Commit Cadence (Council TC-S159, 3-0)

**Rule: Commit-pull-push after every logical unit.** Never accumulate >5 uncommitted files.

A "logical unit" = one feature, one fix, one doc update, or one batch of related changes. When in doubt, commit more often.

**No Dirty Handoff:** Before running `/handoff`, verify `git status` shows 0 uncommitted changes. If changes exist, commit them first or explicitly document why they're uncommitted.

## gitsafe.sh (Council S175, P1 — Mandatory on Multi-Agent Nodes)

On multi-agent nodes (gaea, nova, sati, prya), concurrent git operations race on `.git/index.lock`. **Use `bin/gitsafe.sh`** instead of raw `git` for all write operations:

```bash
# Instead of:
git add file.py && git commit -m "feat: add feature"

# Use:
bash bin/gitsafe.sh add file.py && bash bin/gitsafe.sh commit -m "feat: add feature"
```

**Read-only commands (status, diff, log)** can use regular `git`.

### Nodes that need gitsafe.sh
| Node | RAM | Multi-agent? | Use gitsafe? |
|------|-----|--------------|-------------|
| gaea | 16GB | YES | ✅ Yes |
| nova | 48GB | YES | ✅ Yes |
| sati | 64GB | YES | ✅ Yes |
| prya | 16GB | YES (8+ agents in tmux) | ✅ Yes |
| vega | 16GB | NO | ❌ No (auxiliary only) |

### Single-agent fallback (TC-S159, deprecated)
On nodes where `git add`/`commit` fails with `fatal: unable to write new index file`, use the tmp index pattern manually:
```bash
cp .git/index /tmp/forge-git-index-N && \
GIT_INDEX_FILE=/tmp/forge-git-index-N git add <files> && \
cp /tmp/forge-git-index-N .git/index
```
**gitsafe.sh is preferred** — it handles this automatically.

## Branch Strategy

- **main:** Production-ready code only
- **feat/TASK-ID:** Worktree agent branches (short-lived)
- **No long-lived feature branches** — merge and push same session

## Pre-Commit Checklist

- [ ] Tests pass: `go test ./...` or `pytest`
- [ ] Linting passes: `ruff check` or `gofmt`
- [ ] Type checking passes: `mypy` (Python)
- [ ] No hardcoded secrets
- [ ] Conventional commit format used

## Git Tools

**Allowed for fleet agents:**
- `git status` — check state
- `git diff` — review changes
- `git log --oneline` — view history

**NOT allowed for fleet agents:**
- `git commit`
- `git push`
- `git merge`
- `git rebase`

## Full Documentation

See `forge-shared/modules/git-workflow.md` for complete git conventions.
