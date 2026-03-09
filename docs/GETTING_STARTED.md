# Getting Started

This is the active onboarding path for FORGE.

If another doc teaches a different setup story, start with [ACTIVE_SURFACES.md](ACTIVE_SURFACES.md) and [CANONICAL_WORKFLOW.md](runbooks/CANONICAL_WORKFLOW.md) instead.

## Install

```bash
git clone https://github.com/neoforge-dev/forge.git
cd FORGE/cmd/forge
go build -o forge .
mkdir -p ~/.local/bin
cp forge ~/.local/bin/forge
```

Optional daemon build:

```bash
cd ../forge-v3
go build -o forge-v3 .
cp forge-v3 ~/.local/bin/forge-v3
```

## Configure

```bash
forge init --node-id "$(hostname -s)" --control-plane http://forge-control-plane:8081
```

That writes the control-plane configuration to `~/.forge/config.toml`.

Configuration lives in `config/` (gitignored). See `examples/` for starter templates.

```bash
cp examples/portfolio/sample-portfolio-state.yaml config/portfolio/portfolio-state.yaml
# Edit config/portfolio/portfolio-state.yaml to match your projects
```

## Verify

```bash
forge status
forge fleet status
forge portfolio status
forge node list
```

## First Useful Commands

```bash
forge task list
forge portfolio list
forge portfolio show <product-key>
forge dispatch send forge:kimi "Read .forge/dispatches/task.md — EXECUTE now"
```

## One-Command Node Join

For a new node:

```bash
cd /path/to/forge && bash bin/forge-node-join.sh
```

## What To Read Next

- [README.md](../README.md)
- [AGENTS.md](../AGENTS.md)
- [CANONICAL_WORKFLOW.md](runbooks/CANONICAL_WORKFLOW.md)
- [OPERATING_LOOP_V1.md](portfolio/OPERATING_LOOP_V1.md)

## What Not To Use For Onboarding

Do not start from:

- older harness-based CLI guides
- `docs/v3/`
- `harness/scripts/`
- old wrapper-first flows in `bin/`
