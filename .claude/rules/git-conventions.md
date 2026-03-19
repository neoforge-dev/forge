---
description: Git workflow and commit conventions
globs:
  - "**/*"
---

# Git Conventions

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

## Push After Every Commit

**Always push immediately after committing:**

```bash
git commit -m "feat: add new feature"
git push origin main
```

**Why:** Avoids divergence, ensures backup, enables collaboration.

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
