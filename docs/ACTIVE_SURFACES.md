# Active Surfaces

**Purpose:** make the correct path obvious and make stale surfaces easy to quarantine.

## Canonical Operator Surface

Use these first:

- [README.md](../README.md)
- [AGENTS.md](../AGENTS.md)
- [GETTING_STARTED.md](GETTING_STARTED.md)
- [CANONICAL_WORKFLOW.md](runbooks/CANONICAL_WORKFLOW.md)
- [LEGACY_TOOLING_POLICY.md](runbooks/LEGACY_TOOLING_POLICY.md)
- [OPERATING_LOOP_V1.md](portfolio/OPERATING_LOOP_V1.md)
- [portfolio-state.yaml](portfolio/portfolio-state.yaml)
- [OPEN_SOURCE_SPLIT_PLAN.md](OPEN_SOURCE_SPLIT_PLAN.md)

## Canonical Tooling

- `forge` CLI
- `cmd/forge-v3` daemon
- `bin/forge-node-join.sh`

## Canonical Commands

```bash
forge status
forge fleet status
forge portfolio status
forge task list
forge dispatch send forge:<agent> "Read .forge/dispatches/<task>.md — EXECUTE now"
forge daemon status
forge node list
```

## Not Onboarding Surfaces

These may still be useful as historical/reference material, but they are not active onboarding surfaces:

- `docs/v3/`
- `docs/plans/`
- `docs/sessions/`
- `harness/command_center/docs/`
- `harness/scripts/`
- `docs/FORGE_CLI_V2_REFERENCE.md`
- `docs/V3_GETTING_STARTED.md`
- `docs/TOOLING.md`

## Current Standard

If a doc or script teaches a different runtime story than the canonical files above, treat it as stale until updated.
