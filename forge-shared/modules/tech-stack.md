# Tech Stack Standards

<!-- Last Updated: 2026-03-21 -->
> Ratified by Tech Council TC-TECHSTACK-S150 (2026-03-21, 3-1 vote).
> See `docs/council/TC-TECHSTACK-S150.md` for full reasoning.

## Infrastructure Layer (FORGE Tooling)

| Binary | Source | Role |
|--------|--------|------|
| `forge` | `cmd/forge/` | Go CLI — all fleet operations |
| `forged` | `cmd/forged/` | Go daemon — HTTP API :8081, SQLite |

Python harness (`harness/`) is **iOS only** — `uv run python -m forge_harness.cli_v2 ios <cmd>`.
No Python harness for fleet operations. Use `forge` CLI directly.

## Stack Strategy: Hybrid (Pragmatic Evolution)

| Use Case | Stack | Notes |
|----------|-------|-------|
| Existing revenue products | FastAPI + React/HTMX + PostgreSQL | Don't touch working code |
| FORGE infrastructure | Go | CLI + daemon, ~30K LOC |
| New real-time/streaming | Phoenix/LiveView + PostgreSQL | Greenfield only (S152+) |
| New simple CRUD MVPs | Go + HTMX + PostgreSQL | Single binary, fast deploy |
| iOS apps | SwiftUI + Swift concurrency | calmconnect-io, babybit-es |
| Default (new complex web) | FastAPI + HTMX + PostgreSQL | Agents trained, patterns proven |

## Decision Framework

```
FORGE infra (CLI, daemon)?           → Go
Existing product with revenue?       → Keep current stack, adopt Rails 8 patterns
NEW product, real-time/streaming?    → Phoenix/LiveView (greenfield only)
NEW product, simple CRUD (<5 models)?→ Go + HTMX
Default for NEW complex web apps     → FastAPI + HTMX
```

## Portfolio Product Stack

| Layer | Standard | Notes |
|-------|----------|-------|
| Backend | FastAPI + SQLAlchemy 2.0 + PostgreSQL | Async-first; SQLite for dev |
| Frontend | **HTMX + Jinja2** (default for new) | SPA only when justified |
| Alt Frontend | React 19 + Vite + Tailwind (complex UIs) | Dashboards, charts, rich interactions |
| Package Manager (Python) | `uv` (MANDATORY — never pip) | `uv add`, `uv run` |
| Package Manager (JS) | `npm` | Standard; pnpm acceptable on existing projects |
| Package Manager (Go) | Go modules | `go mod tidy` |
| Jobs | **PostgreSQL-backed queue** (not Celery/Redis) | Rails 8 Solid Queue pattern |
| Cache | **PostgreSQL/SQLite table** (not Redis) | Rails 8 Solid Cache pattern |
| Auth | forge-auth module (JWT + sessions + teams) | Shared scaffold |
| Deploy (backend) | Railway / Docker + VPS | Auto-deploy from main |
| Deploy (frontend) | Cloudflare Pages | Static builds from Vite |
| Deploy (iOS) | App Store via Xcode Cloud | GATE-C required |

## Frontend Framework Decision

| When | Choice | Rationale |
|------|--------|-----------|
| **Default (new products)** | HTMX + Jinja2 | Server-rendered, agent-friendly, minimal JS |
| Complex dashboards, charts | React + Vite + Tailwind | State management, rich interactions |
| Real-time collaborative | Phoenix LiveView | BEAM best-in-class (greenfield only) |
| Simple landing pages | Lit PWA or static HTML | Minimal JS, web components |
| Existing React projects | Keep React | Don't migrate unless necessary |
| Native mobile | SwiftUI | iOS apps on nova node only |

## Rails 8 Patterns (Adopted — All Stacks)

1. **DB-backed job queue** — PostgreSQL table + poller, no Redis/Celery
2. **Server-rendered HTML default** — HTMX, not SPA for every product
3. **Shared auth/billing scaffold** — forge-auth module, stop rebuilding
4. **One-command deploy** — `forge deploy` wrapping Docker + VPS
5. **Monolith-first** — no microservices for MVPs, ever
6. **Convention over configuration** — standard project skeleton per stack

## Backend Structure (FastAPI)

```python
app/
├── api/routes/          # Endpoints grouped by domain
├── auth/                # JWT + product access (forge-auth)
├── config.py            # Pydantic Settings
├── exceptions.py        # Structured AppError
├── infrastructure/      # DB, storage, external APIs
├── middleware/           # Rate limit, request ID, security
├── services/            # Business logic
└── templates/           # Jinja2 templates (HTMX)
```

## Backend Structure (Go + HTMX)

```go
cmd/server/
├── main.go              # Entry point + router
├── handlers/            # HTTP handlers
├── middleware/           # Auth, logging, rate limit
├── models/              # Database models (sqlc)
├── services/            # Business logic
├── templates/           # Go HTML templates (HTMX)
└── static/              # CSS, JS (minimal)
```

## Frontend Structure (React — when justified)

```typescript
src/
├── components/          # Reusable UI components
│   ├── ui/              # Generic (Button, Modal, etc.)
│   └── [feature]/       # Feature-specific
├── pages/               # Route components
├── api/                 # API clients
├── hooks/               # Custom React hooks
├── stores/              # Zustand stores
├── types/               # TypeScript types
└── utils/               # Helpers, formatters
```

## Go CLI/Daemon Patterns

```go
// cmd/forge/ — CLI entry point per noun
// cmd/forged/ — Daemon; SQLite via mattn/go-sqlite3
// Error messages MUST include recovery steps
// Tests: go test -race ./... (83.4% structural ceiling)
```

## Testing Standards

| Type | Tool | Minimum |
|------|------|---------|
| Unit (Python) | pytest + pytest-asyncio | 70% coverage |
| Unit (Go) | go test -race | 83.4% (structural ceiling — see skip-list) |
| Unit (JS/TS) | vitest / jest | 70% coverage |
| Integration | pytest-asyncio / go test | Critical paths |
| E2E | Playwright (portfolio products only) | Happy paths |
| iOS | XCTest via `xcodebuild test` | Critical flows |

## Quality Gates

- Python: `ruff check` + `ruff format` (never flake8/black)
- Go: `gofmt`, `go vet`, `go test -race ./...`
- TypeScript: ESLint + Prettier
- Pre-commit hooks enforced on all projects
- All tests must pass before merge

## Deploy Standards

- **Target**: Hetzner/Railway VPS (no k8s)
- **Container**: Docker (multi-stage builds)
- **Frontend CDN**: Cloudflare Pages
- **CI/CD**: GitHub Actions
- **Monitoring**: PostHog (analytics) + Sentry (errors)
- **One-command**: `forge deploy <product>` (planned S151)

## Deprecated / Removed

- ~~Playwright MCP~~ — not used; use Playwright CLI or `agent-browser` CLI
- ~~`forge-harness` Python CLI~~ — deleted; use `forge` Go CLI
- ~~`uv run python -m forge_harness.cli_v2` for fleet ops~~ — iOS only now
- ~~pip~~ — always use `uv`
