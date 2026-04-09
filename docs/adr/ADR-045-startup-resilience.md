# ADR-045: Startup Resilience — Environment Validation & Path Hardening

**Status:** ACCEPTED (council 2-0, 2026-03-22)
**Author:** prya lead
**Reviewers:** gemini (APPROVE), kimi (APPROVE)

## Context

After a node restart on 2026-03-22, `forge daemon start/restart` and `forge doctor` all failed. Root cause: `FORGE_ROOT` env var in `~/.zshrc` pointed to `/Users/bogdan/work/FORGE` (old macOS path) instead of `/home/openclaw/work/FORGE`. The Go CLI trusts `FORGE_ROOT` blindly across 30+ call sites — no directory-existence check anywhere.

**Impact:** Complete daemon startup failure. Required manual debugging to identify the stale env var.

**Class of bug:** Migration debt — hardcoded absolute paths that don't survive OS/user changes.

## Decision

Implement 6 defensive fixes to prevent stale `FORGE_ROOT` from silently breaking the CLI:

| # | Fix | Where | Status |
|---|-----|-------|--------|
| 1 | Validate FORGE_ROOT on CLI init — warn + unset if directory doesn't exist | `cmd/forge/main.go` PersistentPreRunE | Done |
| 2 | `forge doctor` checks FORGE_ROOT validity as first check | `cmd/forge/doctor.go` | Done |
| 3 | `forge daemon restart` validates srcDir before `go build` | `cmd/forge/daemon.go` | Done |
| 4 | `findDaemonBinary()` falls back to cwd-relative path | `cmd/forge/daemon.go` | Done |
| 5 | `forge-startup.sh` exports FORGE_ROOT to child processes | `.forge/scripts/forge-startup.sh` | Done |
| 6 | Convention: use `$HOME` not absolute paths in shell configs | Documentation | Done |

## What We Won't Do

- **Systemd service** — premature; startup script is sufficient
- **Auto-path-rewriting migration tool** — one-time manual fix is sufficient
- **Make FORGE_ROOT optional everywhere** — it's useful for multi-worktree setups

## Council Feedback (incorporated)

Both reviewers suggested:
- **Centralize FORGE_ROOT resolution** into a single `resolveForgeRoot()` function (tracked as follow-up, not blocking)
- **Debug-level logging** of resolved FORGE_ROOT (tracked as follow-up)
- **`forge env` diagnostic** should print all resolved paths (tracked as follow-up)

## Consequences

- Bad `FORGE_ROOT` now produces an immediate stderr warning instead of silent downstream failures
- `forge doctor` catches the issue as a named check
- `forge daemon restart` fails with an actionable error message before attempting `go build`
- Binary discovery has one more fallback (cwd-relative) before giving up
- All processes spawned by `forge-startup.sh` inherit a correct `FORGE_ROOT`
