# FORGE CLI Audit — S150

**Date:** 2026-03-20
**Status:** Phase 1 fixes shipped, Phase 2 planned

## Fixes Shipped (S150)

| Issue | Fix | Commit |
|-------|-----|--------|
| `fleet list` showed 29 ghost agents from stale files | Now uses daemon API (same as `agent list`) | ef253911 |
| NO_COLOR not respected | Added `init()` checks in doctor.go + output/colors.go | ef253911 |
| Stale `.forge/heartbeat/nodes/*.json` files | Deleted (gitignored, local only) | local |
| `status` returned exit 0 when offline | Returns exit 1 with recovery hint | 1dd63651 |
| `daemon status` returned exit 0 when down | Returns exit 1 with recovery hint | 1dd63651 |
| Silent fallback in patrol/approval/queue list | Now prints stderr warning | 1dd63651 |

## Remaining Issues (Prioritized)

### P0 — Fix Now

| # | Issue | Impact |
|---|-------|--------|
| 3 | `queue` overlaps with `task` — different data sources for same concept | Agents confused about which to use |
| 2 | `fleet status` vs `fleet health` vs `status` — 3 overlapping commands | Users don't know which to run |

### P1 — Fix This Sprint

| # | Issue | Impact |
|---|-------|--------|
| 1 | `fleet` has 12 subcommands | Too many, violates "do one thing well" |
| 9 | `--format json` not consistent across all commands | Scripts break on some commands |
| 6 | Error hints inconsistent across commands | Some errors unhelpful |

### P2 — Backlog

| # | Issue | Impact |
|---|-------|--------|
| 7 | Inconsistent flag names | Minor learning curve |
| 8 | Missing aliases for common commands | Convenience |
| 10 | Command grouping in help | Discoverability |
| 11 | Missing arg validation patterns | Edge case errors |
| 12 | Config precedence not in help text | Documentation gap |
| 14 | Exit code inconsistency (some use exit 3) | POSIX non-compliance |
| 15 | Status command makes 7 API calls | Slow if any timeout |

## Recommended Consolidation

### Merge `queue` into `task`
- `forge queue list` → `forge task list --source local` (or deprecate entirely)
- `forge queue depth` → `forge task list --format quiet | wc -l`

### Simplify `fleet` subcommands
Keep: `list`, `status`, `windows`, `spawn`, `kill`
Move: `budget` → `forge budget`, `metrics` → `forge metrics`
Remove: `health` (merge into `status`), `inventory` (merge into `list`), `capabilities` (merge into `node list`), `recommendations` (merge into `status`)

### Status command convention
- `forge status` — quick overview (keep as-is, it's the morning standup)
- `forge <noun> list` — enumeration
- `forge doctor` — deep diagnostics
- Remove `fleet status` and `fleet health` (use `forge status` instead)
