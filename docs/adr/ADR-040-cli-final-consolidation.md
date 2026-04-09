# ADR-040: Final CLI Consolidation — One CLI, One Daemon, No Scripts

**Date:** 2026-03-08
**Status:** Accepted
**Decision Makers:** Bogdan Veliscu (CTO), Council (codex/gemini/glm/claude-1)
**Amends:** ADR-029 (extends to completion)

---

## Decision

**ONE CLI:** `forge` binary (`cmd/forge/`) — the only interface for all fleet operations.
**ONE Daemon:** `forged` binary (`cmd/forged/`) — the only runtime process.
**ZERO scripts:** All shell scripts and Python CLI modules are deleted. No deprecation wrappers.

This is not a migration. It is a hard cutover. The Python harness CLI v2
(`harness/forge_harness/cli_v2/`) and all shell scripts in `bin/` and `harness/scripts/`
are removed once Go parity is confirmed per council audit.

---

## Context

After ADR-029 was implemented (Go CLI v4 with 30+ commands), the following still existed:
- Python CLI v2: 61 modules, ~50K lines — all marked `[DEPRECATED in V3]`
- bin/ scripts: 16 shell scripts duplicating daemon start/stop, fleet ops, node setup
- harness/scripts/: 8+ scripts for service installation and smoke tests

This created **3 parallel ways** to do every operation, causing:
- Agents choosing the wrong tool (Python vs Go)
- Docs referencing deleted or obsolete commands
- CLAUDE.md instructions conflicting with each other
- Test failures from stale Python imports
- Confusion for new agents onboarding

---

## What Gets Deleted (by category)

### Category 1: Full Go parity — DELETE NOW

**Python CLI modules** (Go CLI has full coverage):
| Module | Go equivalent |
|--------|---------------|
| `df.py` | `forge lane` |
| `features.py` | `forge task` |
| `status.py` | `forge status` |
| `up.py` / `down.py` | `forge up` / `forge down` |
| `config.py` | `forge config` |
| `tasks.py` | `forge task` |
| `work.py` | `forge work` |
| `node.py` | `forge node` |
| `state_manager.py` | `forge state` |
| `search.py` | `qmd search` (external CLI) |
| `relay.py` | `forge relay` (added S89) |
| `lead.py` | `forge lead` (added S89) |
| `dispatch.py` | `forge dispatch` |
| `git_guard.py` | `forge git` |
| `handoff.py` | `forge handoff` |
| `heartbeat.py` | `forge heartbeat` (confirm scope) |

**Shell scripts in bin/** (Go CLI covers):
| Script | Go equivalent |
|--------|---------------|
| `forge-start` | `forge daemon start` |
| `forged-start-prya` | `forge daemon start` |
| `forged-restart` | `forge daemon restart` |
| `forge-fleet` | `forge fleet` |
| `forge-monitor` | `forge monitor` |
| `forge-v3-start-gaea` / `forge-v3-start-nova` | `forge fleet spawn <agent>` |
| `forge-v3-mesh-status` | `forge node list` |
| `fleet-windows` | `forge fleet windows` |

### Category 2: Go gap must be filled first — PORT THEN DELETE

| Python module | Gap | Go implementation needed |
|---------------|-----|--------------------------|
| `complete.py` | pytest+git+push in one shot | Extend `forge ship` to run test suite |
| `context.py` | context% UI + tmux session recovery | Extend `forge context` show with % display |
| `doctor.py` | Full health suite (SQLite, git lock, daemon, fleet) | Extend `forge status` into full `forge doctor` |

### Category 3: Scope analysis needed (council review)

| Module | Question |
|--------|---------|
| `claims.py` | Evidence verification — different from `forge task claim`? |
| `heartbeat.py` | Lead orchestrator loop vs. agent send — overlap with `forge work --daemon`? |
| `evaluator.py` | Used? Covered by patrol? |
| `flywheel.py` | Used? Covered by `forge work --daemon`? |

### Category 4: Python-only, keep intentionally

| Module | Reason to keep |
|--------|----------------|
| `ios.py` | iOS Typer commands — no Go equivalent, mobile-specific |
| `content.py` | Content generation workflows — portfolio-specific, not fleet ops |
| `ship.py` | Portfolio deploy (Railway) — different from `forge ship` (git workflow) |
| `bootstrap.py` | Project scaffold — no Go equivalent yet |

---

## Go CLI Gaps to Fix (Priority Order)

### P0: `forge monitor` — expose 6-view TUI

The forged TUI (`cmd/forged/tui_dashboard.go`, 6 views) is richer than `forge monitor` (4 views).
Add `forge tui` command or merge the 6-view TUI into `forge monitor`.
This is the human control surface — must be accessible from the canonical CLI.

### P1: `forge doctor` — full health suite

Current `forge status` shows fleet overview. Need dedicated `forge doctor` that:
- Pings daemon (`/health`)
- Checks SQLite integrity
- Detects `.git/index.lock` stale locks
- Checks `.forge/` directory structure
- Lists offline agents with last-seen times
- Shows token budget status

### P2: `forge work --loop` / `--daemon` verification

Verify `forge work --daemon` in `cmd/forge/workflow_work.go` correctly:
- Claims next available task from queue
- Executes task (via Agent tool or subprocess)
- Marks complete
- Loops until interrupted
If not: implement in `workflow_work.go`.

### P3: `forge ship` — test integration

`forge ship` currently: quality gates + git commit + push.
Python `complete.py` additionally runs the project test suite.
Extend `forge ship` with `--run-tests` flag that runs `go test` or `pytest` before committing.

---

## bin/ Scripts Disposition

| Script | Action | Notes |
|--------|--------|-------|
| `fleet-windows` | DELETE | `forge fleet windows` exists |
| `forge` | DELETE | symlink? Check target |
| `forge-fleet` | DELETE | `forge fleet` exists |
| `forge-monitor` | DELETE | `forge monitor` exists |
| `forge-server` | REVIEW | what does it start? |
| `forge-sprint` | REVIEW | still used? |
| `forge-start` | DELETE | `forge daemon start` |
| `forge-v3-deploy-nova` | REVIEW | covered by `forge node join`? |
| `forge-v3-mesh-status` | DELETE | `forge node list` |
| `forge-v3-start-gaea` | DELETE | `forge fleet spawn` |
| `forge-v3-start-nova` | DELETE | `forge fleet spawn` |
| `forged-logs` | DELETE | `tail -f /tmp/forged.log` or `forge daemon logs` |
| `forged-restart` | DELETE | `forge daemon restart` |
| `forged-start-prya` | DELETE | `forge daemon start` |
| `node-migrate-v3` | KEEP | still needed for new node setup |
| `setup-ghostty-fleet` | REVIEW | one-time setup, may still be needed |

---

## harness/scripts/ Disposition

| Script | Action |
|--------|--------|
| `bootstrap-xnode.sh` | DELETE — xnode is SQLite now |
| `forge-heartbeat.sh` | DELETE — patrol system replaced |
| `forge-eval.py` | REVIEW |
| `generate-test-data.py` | REVIEW — may be needed for tests |
| `hourly-qmd-update.sh` | KEEP — qmd is external |
| `install-*.sh` | REVIEW — systemd service installers |
| `smoke-test.sh` | REVIEW — CI? |

---

## harness/forge_harness/ (Python package) — Long-term

The whole `harness/forge_harness/` package is NOT deleted — it still contains:
- `webhook_server_main.py` — Command Center (ADR-014 partial, Python side still running)
- `ralph_loop.py` — portfolio autonomous feature loop
- `continuous_runner/` — Railway worker
- `meta_learning/` — decision engine
- iOS harness (`ios_harness/`)
- Test suite

Only `cli_v2/` (the CLI module) is deleted.
The harness package itself stays until ADR-014 CC retirement is complete.

---

## Implementation Sequence

1. **Council audit** (this session) → produces definitive safe-delete list
2. **P0 gap fixes** → implement any critical Go CLI missing features
3. **Wave 1 deletions** → delete all Category 1 modules (full parity confirmed)
4. **Wave 2 port+delete** → implement Go gaps for Category 2, then delete Python
5. **harness/__init__.py** → remove all deleted module imports
6. **CLAUDE.md update** → single CLI reference section
7. **docs/ cleanup** → remove all references to Python CLI commands

---

## Non-Goals

- Backwards compatibility wrappers — none. Hard delete only.
- Deprecation warnings — none. The Python CLI is already marked deprecated.
- Feature flags — none. Cutover is immediate once Go parity is confirmed.

---

**Status: ACCEPTED**
**Council review dispatched:** 2026-03-08 (codex/gemini/glm/claude-1)
**Expected completion:** Current session (S89)
