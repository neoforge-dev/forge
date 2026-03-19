# ADR-001: CLI v2 as Canonical Forge Entry Point

**Date:** 2026-02-23
**Status:** Accepted
**Decision Makers:** Bogdan (CTO), Sati Lead Orchestrator

## Context

Multiple entry points existed for the `forge` command:
1. `forge` (CLI v2) — Python/Click, 48+ modules in `harness/forge_harness/cli_v2/`
2. `forge-harness` (legacy) — Python/Click, older entry point
3. `forge-cli.sh` — Bash wrapper delegating to various scripts
4. `forge-queue.sh` — Bash, SQLite-based task queue

This caused confusion: different commands available depending on which entry point was used, duplicated logic across Python and Bash, and inconsistent behavior.

## Decision

**CLI v2 (`forge_harness/cli_v2/`) is the canonical entry point.** All other entry points are deprecated.

- `forge` alias points to `uv run python -m forge_harness.cli_v2` from the harness directory
- 100% feature parity: all 28 CC backend endpoints have CLI wrappers (50 modules total)
- `forge-harness` entrypoint is deprecated
- `forge-cli.sh` and `forge-queue.sh` are deprecated

## Consequences

### Positive
- Single source of truth for CLI commands
- Consistent `--json` output pattern across all commands
- 100% CC feature parity achieved
- Better error handling, circuit breakers, and retry logic

### Negative
- Must run from `harness/` directory (or configure PATH)
- `forge` shell function can shadow the CLI v2 binary

### Neutral
- Legacy scripts kept as fallbacks during transition period
- Documentation consolidation needed (CLI_TOOLS_REFERENCE.md + CLI_INVENTORY.md)
