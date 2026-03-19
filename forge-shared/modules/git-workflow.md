# Git Workflow

FORGE portfolio uses a trunk-based development model with multi-node collaboration.

## Branch Strategy

**Main branch (`main`)** is the default for all work.
- Feature branches only for multi-day tasks or complex features
- Branch naming: `feat/`, `fix/`, `docs/`, `refactor/`, `test/`
- Keep branches short-lived (< 1 day when possible)

## Commit Convention

Use conventional commit format: `type(scope): description`

| Type | Usage |
|------|-------|
| `feat` | New feature |
| `fix` | Bug fix |
| `test` | Test additions or fixes |
| `chore` | Dependencies, config, maintenance |
| `docs` | Documentation only |
| `refactor` | Code restructuring without behavior change |

**Examples:**
```bash
test(harness): add 183 tests for webhook_server_main coverage
fix(xnode): acquire SSE session token before connecting
feat(interview-simulator): add mock interview mode with timer
chore(deps): bump pytest from 8.0.0 to 8.0.1
```

## Commit Frequency

**Commit often. Integrate often.**
- Commit after completing a logical unit of work (test file, feature, fix)
- Avoid long-lived uncommitted changes (high conflict risk with multi-node fleet)
- Use `forge git commit` for automatic retry, mutex coordination, and conflict resolution

## Conflict Resolution

Multi-node environment requires careful sync:

```bash
# Before pushing, always rebase
git fetch origin main
git rebase origin/main

# If conflicts occur:
git status                    # Check what's conflicted
# Edit conflicted files
git add <resolved-files>
git rebase --continue

# For submodules: checkout dirty before rebase
git submodule foreach 'git stash'  # If needed
```

## Multi-Node Fleet

Multiple agents (node-1, node-3, node-2, node-4) push to `main` simultaneously.
- Use `forge git commit` to handle race conditions
- Fetch + rebase before any push
- Small, frequent commits reduce conflict surface area

## Git Index Lock

Stale `.git/index.lock` (0-byte) occurs when git processes crash.

**Safe to remove:**
```bash
rm .git/index.lock
```

**Recurring issue** during worktree cleanup — lock is always 0-byte artifact, never valid state.

## Submodules

FORGE uses submodules for domain projects. When switching branches:
```bash
git submodule update --init --recursive
```

## Worktrees

For parallel development, use git worktrees:
```bash
git worktree add ../forge-worktree <branch>
```

Cleanup after use:
```bash
git worktree remove ../forge-worktree
```

## Push Workflow (Multi-Node)

Always sync before pushing to avoid divergence:

```bash
# Standard push (fetch without recursing submodules)
git -c fetch.recurseSubmodules=no fetch origin main && git stash && git rebase origin/main && git stash pop && git push

# With forge git commit (handles index.lock, mutex, retry)
forge git commit "commit message"
```

**Common issue**: Branches diverge when multiple nodes push. Fix:
```bash
git stash
git fetch origin main
git rebase origin/main   # NOT merge
git stash pop
git push
```

## Submodule Commit Pattern (Avoid Detached HEAD)

Portfolio submodules (`portfolio/{domain}/{project}`) require explicit branch checkout before committing. Git submodules check out in detached HEAD by default when FORGE advances the pointer.

**Always do this before committing inside a submodule:**
```bash
cd portfolio/domain/project
git checkout main          # REQUIRED — avoids detached HEAD
# make changes
git add <files>
git commit -m "..."
git push origin main
```

**If you already committed in detached HEAD:**
```bash
git checkout main
git merge HEAD@{1}         # merge the detached commit back
git push origin main
```

**If push is rejected (remote ahead):**
```bash
git stash
git pull --rebase
git stash pop
git push origin main
```

---

## Orchestrator vs Agent Commits

- **Only the lead orchestrator commits and pushes.** Fleet agents MUST NOT commit.
- Dispatch files should include `DO NOT COMMIT` instruction
- Agents write results to `.forge/heartbeat/results/` for lead to collect

## Safety Rules

- **Never force push to main**
- **Never skip pre-commit hooks** (`--no-verify`) unless explicitly approved
- **Never commit secrets** (.env, credentials, API keys)
- **Create NEW commits** after hook failure — never amend previous
