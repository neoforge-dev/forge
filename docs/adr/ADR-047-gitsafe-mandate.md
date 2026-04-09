# ADR-047: Mandate gitsafe.sh on Multi-Agent Nodes

**Status:** Accepted
**Date:** 2026-03-29
**Council:** S175, P1 (4-0 unanimous)
**Supersedes:** TC-S159 GIT_INDEX_FILE workaround

---

## Context

Multi-agent nodes (gaea, nova, sati) experience git `index.lock` contention when multiple agents or worktree agents commit concurrently. S175 had 3 manual workarounds in a single session. The previous solution (TC-S159) documented a manual `cp .git/index /tmp/forge-git-index-N && GIT_INDEX_FILE=...` pattern — functional but error-prone and easy to forget.

## Decision

Mandate `bin/gitsafe.sh` as the default git wrapper for all **write operations** on multi-agent nodes. Read-only commands (status, diff, log) can use regular `git`.

### Nodes affected

| Node | RAM | Multi-agent? |
|------|-----|--------------|
| gaea | 16GB | YES (kimi, gemini, pi) |
| nova | 48GB | YES (worktree + fleet) |
| sati | 64GB | YES (opencode, kilo, kimi) |
| prya | 16GB | **NO** (single orchestrator only) |
| vega | 16GB | **NO** (auxiliary only) |

## Implementation

`bin/gitsafe.sh` works by:
1. Copying `.git/index` to `/tmp/forge-git-index-$$`
2. Running the git command with `GIT_INDEX_FILE` pointing to the tmp file
3. Copying the tmp file back to `.git/index` on exit

```bash
# Instead of:
git add file.py && git commit -m "feat: add feature"

# Use:
bash bin/gitsafe.sh add file.py && bash bin/gitsafe.sh commit -m "feat: add feature"
```

The script:
- Uses `set -e` — fails fast on any error
- Traps EXIT to clean up tmp file
- Auto-detects repo root via `git rev-parse --show-toplevel`

## Consequences

| | |
|---|---|
| **Positive** | No more manual lock cleanup during sessions |
| **Positive** | Worktree agents and orchestrator can commit concurrently |
| **Positive** | Fleet agents can safely write to git without index corruption |
| **Negative** | Slight overhead on write operations (2 file copies) |
| **Negative** | Must remember to use `bash bin/gitsafe.sh` instead of `git` |
| **Risk** | If tmp file is corrupted, index could be damaged (mitigated by size check — script refuses to copy back if size is 0) |

## Rollout

1. **Immediately:** Fleet orchestrators on gaea/nova/sati start using `gitsafe.sh` for all write operations
2. **Agents:** Fleet agents do NOT commit (unchanged — orchestrator commits all work)
3. **Worktree agents:** Must use `gitsafe.sh` when committing to main

## References

- Script: `bin/gitsafe.sh`
- Original workaround: `git-conventions.md` §GIT_INDEX_FILE Workaround (TC-S159)
- Git workflow: `forge-shared/modules/git-workflow.md`
