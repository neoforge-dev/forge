# Legacy Tooling Policy

**Status:** Active
**Updated:** 2026-03-08

Use this policy to keep onboarding strict and reduce confusion for both agents and humans.

## Deprecated for New Work

| Tool or Pattern | Status | Use Instead |
|---|---|---|
| `forge-harness ...` | Deprecated | `forge ...` |
| “CLI v2” as canonical wording | Deprecated | active `forge` CLI |
| Direct tmux for task delivery | Deprecated | `forge dispatch send` |
| Ad hoc wrapper-first flows in `bin/` | Deprecated | direct `forge` commands |
| Ad hoc `curl /api/...` for normal operator workflows | Deprecated | `forge` CLI commands |
| Portfolio planning spread across many docs | Deprecated | `forge portfolio ...` + `docs/portfolio/portfolio-state.yaml` |

## Compatibility-Only

These may still exist during transition, but they are not onboarding tools:

- `harness/scripts/`
- `harness/command_center/docs/`
- `docs/v3/`
- `docs/plans/`
- `docs/FORGE_CLI_V2_REFERENCE.md`
- `docs/V3_GETTING_STARTED.md`
- `docs/TOOLING.md`

## Fail-Fast Rule

If a legacy wrapper remains in `bin/`, it should either:

1. be updated and listed in [ACTIVE_SURFACES.md](./docs/ACTIVE_SURFACES.md), or
2. fail fast with a message pointing to the canonical `forge` command.

## Removal Criteria

A legacy doc or script should be deleted when:

1. its replacement exists,
2. active docs no longer point to it,
3. the docs policy checks pass without exemptions.

## Canonical References

- [ACTIVE_SURFACES.md](./docs/ACTIVE_SURFACES.md)
- [CANONICAL_WORKFLOW.md](./docs/runbooks/CANONICAL_WORKFLOW.md)
- [OPERATING_LOOP_V1.md](./docs/portfolio/OPERATING_LOOP_V1.md)
