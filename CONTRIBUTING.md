# Contributing to FORGE

Thanks for your interest in contributing! This guide will help you get started.

## Quick Start

```bash
# Clone (shallow recommended — portfolio submodules are large)
git clone --depth 1 https://github.com/neoforge-dev/forge.git
cd forge

# Build the CLI
cd cmd/forge && go build ./... && ./forge version

# Verify everything works
./forge status
```

## Development Workflow

1. **Fork** the repository
2. **Create a branch** from `main`: `git checkout -b feat/your-feature`
3. **Make changes** — keep commits focused and atomic
4. **Run checks** before committing:
   ```bash
   cd harness
   uv run ruff check .        # Linting
   uv run ruff format .       # Formatting
   uv run pytest tests/ -x    # Tests
   ```
5. **Open a PR** against `main`

## Code Standards

- **Python**: 3.12+, ruff for linting/formatting, type hints encouraged
- **Go**: `cmd/forge/` (CLI) and `cmd/forged/` (daemon) — `go vet`, `go test`
- **Commits**: [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `chore:`, `docs:`)
- **Tests**: Required for new features and bug fixes

## Project Structure

```
forge/
├── cmd/forge/             # CLI binary (Go)
├── cmd/forged/            # Daemon binary (Go) — HTTP :8081, SQLite
├── harness/               # Python harness (iOS automation, agent SDK)
│   ├── forge_harness/     # Main package
│   │   ├── ios_harness/   # iOS build/test automation
│   │   └── ...
│   └── tests/             # Test suite
├── services/ apps/ ios/ tools/ games/ research/  # Products by type
├── forge-shared/          # Shared libraries across projects
├── docs/                  # Documentation
└── .forge/                # Runtime state (gitignored)
```

## Testing

```bash
cd harness
uv run pytest tests/ -v                  # Full suite
uv run pytest tests/ --cov=forge_harness # With coverage
```

- Every new feature needs tests
- Every bug fix needs a regression test
- Use `pytest-asyncio` for async code

## What to Work On

- Check [Issues](https://github.com/neoforge-dev/forge/issues) for `good first issue` labels
- See `docs/BACKLOG.md` for the feature backlog
- Run `forge doctor` to find health check failures

## Code of Conduct

Be respectful, constructive, and inclusive. We follow the [Contributor Covenant](https://www.contributor-covenant.org/).

## Questions?

Open a [Discussion](https://github.com/neoforge-dev/forge/discussions) or check `docs/GETTING_STARTED.md`.
