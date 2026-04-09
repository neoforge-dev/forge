# Code Quality & Standards

<!-- Last Updated: 2026-03-21 -->

## Language-Specific Tools

### Go (`cmd/forge/`, `cmd/forged/`)

```bash
gofmt -w .                    # Format all Go files
go vet ./...                  # Static analysis
go test ./...                 # Run all tests
go test -race ./...           # Race condition detection (required before merge)
go build -o forge .           # Build CLI
go build -o forged .          # Build daemon
```

- Coverage target: 83.4% (structural ceiling — see `cmd/forged/TEST_MAP.md`)
- Skip-list functions are intentionally untested (OS-dependent or tmux-dependent)
- Never create `coverage_wave*_test.go` — extend canonical test files only

### Python (`harness/`, portfolio backends)

```bash
uv add <package>              # Add dependency (NEVER pip install)
uv run ruff check .           # Lint
uv run ruff format .          # Format (replaces black)
uv run mypy .                 # Type checking
uv run pytest                 # Run tests
uv run pytest --cov           # Coverage report
```

- Ruff replaces flake8/black/isort — use only ruff
- mypy for all production code; `# type: ignore` requires a comment explaining why
- Coverage target: 70% minimum per service

### JavaScript / TypeScript (portfolio frontends)

```bash
npm run lint                  # ESLint
npm run format                # Prettier
npm run test                  # vitest (preferred) or jest
npm run build                 # Vite production build
```

- ESLint + Prettier enforced
- vitest for new projects; jest acceptable on existing projects
- Coverage target: 70% minimum for critical paths

### iOS (Swift — `harness/` managed)

```bash
xcodebuild -scheme SCHEME -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build
xcodebuild test -scheme SCHEME -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

- XCTest for unit tests; XCUITest for UI flows
- Run via `uv run python -m forge_harness.cli_v2 ios test`

---

## Documentation

- Log outcomes in nearest `docs/progress.md`
- Update `docs/PLAN.md` when completing sprints
- Keep `CLAUDE.md` current with focus areas

---

## Code Quality Rules

- Run tests before committing
- No TODOs without issue references
- Prefer editing existing files over creating new ones
- Keep solutions simple — no over-engineering
- Error messages must include recovery steps (Go CLI rule)
- Never expose stack traces in production responses

---

## Dependencies

| Language | Add command | Lockfile |
|----------|-------------|----------|
| Python | `uv add <pkg>` | `uv.lock` |
| Node/JS | `npm install <pkg>` | `package-lock.json` |
| Go | `go get <module>` | `go.sum` |

- Pin versions in production
- Audit regularly

---

## Error Handling

- Use structured `AppError` with error codes (Python/FastAPI backends)
- Go CLI errors must tell the user what went wrong AND how to fix it
- Log with context (request_id, user_id) in backend services
- Graceful degradation for non-critical services

---

## Performance

- Async-first for I/O operations (Python/FastAPI)
- Connection pooling for databases
- Rate limiting on all public endpoints
- Cache where appropriate

---

## Pre-Commit Checklist

- [ ] Tests pass: `go test -race ./...` / `uv run pytest` / `npm test`
- [ ] Linting passes: `gofmt` / `ruff check` / `eslint`
- [ ] Type checking passes: `mypy` (Python), TypeScript strict mode
- [ ] No hardcoded secrets
- [ ] Conventional commit format used
- [ ] No new `coverage_wave*_test.go` files (Go — extend canonical files)

---

## Deprecated Tools (Do Not Use)

- ~~flake8~~ → use `ruff check`
- ~~black~~ → use `ruff format`
- ~~isort~~ → ruff handles imports
- ~~pip install~~ → use `uv add`
- ~~forge-harness CLI for fleet ops~~ → use `forge` Go CLI
