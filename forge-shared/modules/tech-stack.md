# Tech Stack Standards

## Core Stack

| Layer | Standard | Notes |
|-------|----------|-------|
| Backend | FastAPI + SQLModel + PostgreSQL | Async-first |
| Frontend | **React 18+ / Vite** (default) | See below |
| Package Manager | Astral uv (MANDATORY - never pip) | Fast, reliable |
| Deploy | Railway (backend) + Cloudflare Pages (frontend) | Auto-deploy |

## Frontend Framework Decision

**Default: React + Vite + Tailwind CSS**

Use React for all new projects unless there's a specific reason not to.

| When | Choice | Rationale |
|------|--------|-----------|
| **Default** | React + Vite | Ecosystem, agent familiarity, shared patterns |
| Complex dashboards | React + Vite | State management, charts, forms |
| Simple landing pages | Lit PWA | Minimal JS, web components |
| Existing Lit projects | Lit PWA | Don't migrate unless necessary |

**React Stack:**
- React 18.x + Vite 5.x
- React Router 6.x (routing)
- Zustand (global state)
- TanStack Query (server state)
- React Hook Form (forms)
- Tailwind CSS 3.x (styling)
- Recharts (charts)
- vite-plugin-pwa (PWA)

## Backend Patterns

```python
# Standard FastAPI structure
app/
├── api/routes/          # Endpoints grouped by domain
├── auth/                # JWT + product access
├── config.py            # Pydantic Settings
├── exceptions.py        # Structured AppError
├── infrastructure/      # DB, storage, external APIs
├── middleware/          # Rate limit, request ID, security
└── services/            # Business logic

# Standard imports
from app.config import settings
from app.exceptions import AppError, ErrorCode
from app.auth.dependencies import get_current_user
```

## Frontend Patterns

```typescript
// DEFAULT: React + Vite
// Standard structure for all new projects
src/
├── components/     # Reusable UI components
│   ├── ui/         # Generic (Button, Modal, etc.)
│   └── [feature]/  # Feature-specific
├── pages/          # Route components
├── api/            # API clients
├── hooks/          # Custom React hooks
├── stores/         # Zustand stores
├── types/          # TypeScript types
└── utils/          # Helpers, formatters
```

## Testing Standards

| Type | Tool | Minimum |
|------|------|---------|
| Unit | pytest / vitest | 70% coverage |
| Integration | pytest-asyncio | Critical paths |
| E2E | Playwright | Happy paths |

## Quality Gates

- Ruff for Python (check + format)
- ESLint + Prettier for TypeScript
- Pre-commit hooks enforced
- All tests must pass before merge
