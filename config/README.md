# FORGE Config Directory

**Status:** DESIGN TEMPLATES — not yet wired to daemon code.

These YAML files define the **intended** configuration schema for FORGE subsystems.
They are referenced in `docs/FORGE_SIMPLIFICATION_PLAN.md` (Blueprint Runtime, Epic 2)
but are **not loaded at runtime** by the daemon (`cmd/forged/`).

## Directory Structure

```
config/
├── blueprints/          # Blueprint Runtime templates (ADR-041)
│   ├── coding/          # default.yaml, bugfix.yaml, feature.yaml
│   ├── deploy/          # verify.yaml
│   └── testing/         # wave.yaml
├── dark-factory/        # Dark Factory approval tiers
│   └── approval-tiers.yaml
├── portfolio/           # Portfolio state (ACTIVE — read by forge CLI)
│   └── portfolio-state.yaml
└── routing/             # Agent/node routing templates
    ├── agent-registry.yaml
    ├── agent-roles.yaml
    ├── node-registry.yaml
    └── task-envelope-v1.yaml
```

## What IS wired to code

- `config/portfolio/portfolio-state.yaml` — read by `forge portfolio status` CLI command

## What is NOT wired to code

- `config/blueprints/` — design templates for Blueprint Runtime (Epic 2, not started)
- `config/routing/` — design templates for agent/node routing (not loaded by daemon)
- `config/dark-factory/` — design template for approval tiers (hardcoded in daemon)

## When will these be wired?

Per `docs/FORGE_SIMPLIFICATION_PLAN.md`:
- Epic 2 (Blueprint Runtime MVP) will load `config/blueprints/`
- Epic 1 (Runtime Contracts) will load `config/routing/`
- Neither has been started as of S120.
