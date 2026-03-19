# FORGE GitHub Actions Workflows

This directory contains shared CI/CD pipeline templates for the FORGE portfolio.

## Reusable Workflow Templates

### 1. Backend CI Template (`reusable-backend-ci.yml`)

**Purpose:** Standardized CI pipeline for Python/FastAPI backend projects.

**Features:**
- Lint with `ruff` (check + format)
- Type check with `mypy` (optional)
- Test with `pytest` (coverage enforcement)
- Docker build verification (optional)
- PostgreSQL 16 + Redis 7 services
- Uses `astral-sh/setup-uv` for package management

**Usage:**
```yaml
jobs:
  backend-ci:
    uses: ./.github/workflows/reusable-backend-ci.yml
    with:
      project_path: 'domain/project/backend'
      python_version: '3.11'              # Optional, default: 3.11
      coverage_threshold: 70              # Optional, default: 70
      enable_mypy: true                   # Optional, default: true
      enable_docker_build: true           # Optional, default: true
```

**Inputs:**
| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `project_path` | Yes | - | Relative path to backend directory |
| `python_version` | No | `3.11` | Python version (3.11, 3.12, etc.) |
| `coverage_threshold` | No | `70` | Minimum coverage percentage |
| `enable_mypy` | No | `true` | Run mypy type checking |
| `enable_docker_build` | No | `true` | Verify Docker image builds |

**Jobs:**
1. **lint** - Ruff linting and format checking
2. **typecheck** - mypy type checking (if enabled)
3. **test** - pytest with coverage (PostgreSQL + Redis services)
4. **build** - Docker build verification (if enabled)

---

### 2. Frontend CI Template (`reusable-frontend-ci.yml`)

**Purpose:** Standardized CI pipeline for React/Vite frontend projects.

**Features:**
- Lint with ESLint
- Type check with TypeScript compiler
- Test with Vitest (optional)
- Build verification with Vite
- Build artifact upload

**Usage:**
```yaml
jobs:
  frontend-ci:
    uses: ./.github/workflows/reusable-frontend-ci.yml
    with:
      project_path: 'domain/project/frontend'
      node_version: '20'                  # Optional, default: 20
      enable_tests: true                  # Optional, default: true
      build_env_vars: '{"VITE_API_URL": "https://api.example.com"}'
```

**Inputs:**
| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `project_path` | Yes | - | Relative path to frontend directory |
| `node_version` | No | `20` | Node.js version |
| `enable_tests` | No | `true` | Run vitest/jest tests |
| `build_env_vars` | No | `{}` | Additional build env vars (JSON) |

**Jobs:**
1. **lint** - ESLint checking
2. **typecheck** - TypeScript compilation check
3. **test** - Vitest/Jest tests (if enabled)
4. **build** - Production build with Vite

---

### 3. Voice Coach CI (`voice-coach-ci.yml`)

**Purpose:** Example implementation using both reusable templates.

**Features:**
- Calls `reusable-backend-ci.yml` for backend
- Calls `reusable-frontend-ci.yml` for frontend
- Triggers on push/PR to `brandfocus-ai/voice-coach/app/**`

**Usage:**
This workflow is automatically triggered. Use it as a template for other projects.

---

## Creating a New Project Workflow

### Example: Interview Simulator CI

```yaml
name: Interview Simulator CI

on:
  push:
    branches: [main]
    paths:
      - 'codeswiftr-com/interview-simulator/**'
      - '.github/workflows/interview-simulator-ci.yml'
  pull_request:
    paths:
      - 'codeswiftr-com/interview-simulator/**'

jobs:
  backend-ci:
    uses: ./.github/workflows/reusable-backend-ci.yml
    with:
      project_path: 'codeswiftr-com/interview-simulator/backend'
      python_version: '3.12'
      coverage_threshold: 75

  frontend-ci:
    uses: ./.github/workflows/reusable-frontend-ci.yml
    with:
      project_path: 'codeswiftr-com/interview-simulator/frontend'
      node_version: '20'
      build_env_vars: '{"VITE_API_URL": "https://api.interview-simulator.com"}'
```

---

## Best Practices

### 1. Path Triggers
Always include the workflow file itself in the path trigger:
```yaml
paths:
  - 'domain/project/**'
  - '.github/workflows/project-ci.yml'
  - '.github/workflows/reusable-*.yml'  # Include if using reusable workflows
```

### 2. Coverage Thresholds
- **New projects:** Start at 70%, increase over time
- **Mature projects:** 80%+ for critical services
- **MVPs:** 60% minimum

### 3. Docker Build
- Enable for projects with Dockerfile
- Disable for projects deploying to PaaS (Railway, Vercel)

### 4. Type Checking
- Enable mypy for production services
- Use `continue-on-error: true` during migration
- Remove once type hints are complete

### 5. Build Env Vars
Pass environment-specific variables as JSON:
```yaml
build_env_vars: |
  {
    "VITE_API_URL": "${{ secrets.API_URL }}",
    "VITE_POSTHOG_KEY": "${{ secrets.POSTHOG_KEY }}"
  }
```

---

## Migration Guide

### Converting Existing Workflow to Reusable Template

**Before:**
```yaml
jobs:
  backend-test:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: my-project/backend
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v5
      # ... many more steps
```

**After:**
```yaml
jobs:
  backend-ci:
    uses: ./.github/workflows/reusable-backend-ci.yml
    with:
      project_path: 'my-project/backend'
      python_version: '3.11'
```

**Benefits:**
- 80% less code duplication
- Standardized quality gates
- Easier maintenance
- Consistent CI across all projects

---

## Troubleshooting

### Issue: Coverage below threshold
```
FAILED: Coverage is below 70%
```
**Solution:** Either increase coverage or lower threshold temporarily:
```yaml
with:
  coverage_threshold: 60  # Lower threshold
```

### Issue: mypy errors
```
mypy found errors
```
**Solution:** Disable temporarily during migration:
```yaml
with:
  enable_mypy: false
```

### Issue: Docker build fails
```
ERROR: failed to solve: failed to compute cache key
```
**Solution:** Ensure Dockerfile exists or disable:
```yaml
with:
  enable_docker_build: false
```

### Issue: Tests not running
```
npm run test:run: command not found
```
**Solution:** Ensure package.json has test script or disable:
```yaml
with:
  enable_tests: false
```

---

## Related Documentation

- [Sprint 9 Plan](../../docs/PLAN.md#sprint-9)
- [Tech Stack Standards](../../.claude/modules/tech-stack.md)
- [Deployment Checklist](../../docs/DEPLOYMENT_CHECKLIST.md)

---

## Maintenance

**Owner:** DevOps / Infrastructure Team
**Last Updated:** 2026-02-06
**Review Frequency:** Quarterly

**Changelog:**
- 2026-02-06: Initial creation (Sprint 9, XD-S9-02/03)
