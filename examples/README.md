# Examples

Starter material for new FORGE deployments. Copy files to `config/` and customize.

| Directory | Purpose |
|-----------|---------|
| `examples/portfolio/` | Copy to `config/portfolio/` — defines your domains and products |
| `examples/routing/` | Copy to `config/routing/` — defines node topology (leave empty for single-node) |

## Quick Start

```bash
cp examples/portfolio/sample-portfolio-state.yaml config/portfolio/portfolio-state.yaml
# Edit config/portfolio/portfolio-state.yaml to match your projects
forge portfolio status
```

`examples/` = copyable starter material. `config/` = live configuration (gitignored).
