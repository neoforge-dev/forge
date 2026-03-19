# CI/CD Templates Quick Reference Card

**TL;DR:** Copy-paste templates for adding CI to your project.

## 5-Minute Setup

### Step 1: Create workflow file

```bash
# Create your project's workflow file
touch .github/workflows/my-project-ci.yml
```

### Step 2: Choose your template

#### Backend Only
```yaml
name: My Backend CI

on:
  push:
    branches: [main]
    paths:
      - 'domain/project/backend/**'
      - '.github/workflows/my-project-ci.yml'
  pull_request:
    paths:
      - 'domain/project/backend/**'

jobs:
  backend-ci:
    uses: ./.github/workflows/reusable-backend-ci.yml
    with:
      project_path: 'domain/project/backend'
      python_version: '3.11'
      coverage_threshold: 70
```

#### Frontend Only
```yaml
name: My Frontend CI

on:
  push:
    branches: [main]
    paths:
      - 'domain/project/frontend/**'
      - '.github/workflows/my-project-ci.yml'
  pull_request:
    paths:
      - 'domain/project/frontend/**'

jobs:
  frontend-ci:
    uses: ./.github/workflows/reusable-frontend-ci.yml
    with:
      project_path: 'domain/project/frontend'
      node_version: '20'
      enable_tests: true
```

#### Full Stack (Backend + Frontend)
```yaml
name: My Project CI

on:
  push:
    branches: [main]
    paths:
      - 'domain/project/**'
      - '.github/workflows/my-project-ci.yml'
  pull_request:
    paths:
      - 'domain/project/**'

jobs:
  backend-ci:
    uses: ./.github/workflows/reusable-backend-ci.yml
    with:
      project_path: 'domain/project/backend'
      python_version: '3.11'
      coverage_threshold: 70

  frontend-ci:
    uses: ./.github/workflows/reusable-frontend-ci.yml
    with:
      project_path: 'domain/project/frontend'
      node_version: '20'
```

### Step 3: Commit and test

```bash
git add .github/workflows/my-project-ci.yml
git commit -m "ci: add CI pipeline with reusable templates"
git push origin feature-branch
```

## Common Configurations

### Backend: Python 3.12, 80% coverage, no Docker
```yaml
backend-ci:
  uses: ./.github/workflows/reusable-backend-ci.yml
  with:
    project_path: 'my-domain/my-project/backend'
    python_version: '3.12'
    coverage_threshold: 80
    enable_docker_build: false
```

### Backend: Disable mypy during migration
```yaml
backend-ci:
  uses: ./.github/workflows/reusable-backend-ci.yml
  with:
    project_path: 'my-domain/my-project/backend'
    enable_mypy: false
```

### Frontend: Node 22, no tests yet
```yaml
frontend-ci:
  uses: ./.github/workflows/reusable-frontend-ci.yml
  with:
    project_path: 'my-domain/my-project/frontend'
    node_version: '22'
    enable_tests: false
```

### Frontend: Custom build env vars
```yaml
frontend-ci:
  uses: ./.github/workflows/reusable-frontend-ci.yml
  with:
    project_path: 'my-domain/my-project/frontend'
    build_env_vars: |
      {
        "VITE_API_URL": "${{ secrets.API_URL }}",
        "VITE_POSTHOG_KEY": "${{ secrets.POSTHOG_KEY }}"
      }
```

## Adding Deployment

### Deploy after CI passes

```yaml
jobs:
  backend-ci:
    uses: ./.github/workflows/reusable-backend-ci.yml
    with:
      project_path: 'domain/project/backend'

  frontend-ci:
    uses: ./.github/workflows/reusable-frontend-ci.yml
    with:
      project_path: 'domain/project/frontend'

  deploy:
    needs: [backend-ci, frontend-ci]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Deploy
        run: ./scripts/deploy.sh
```

### Railway Deployment
```yaml
  deploy-backend:
    needs: [backend-ci]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Install Railway CLI
        run: npm install -g @railway/cli
      - name: Deploy
        run: railway up --service my-api
        working-directory: domain/project/backend
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
```

### Cloudflare Pages Deployment
```yaml
  deploy-frontend:
    needs: [frontend-ci]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - name: Install and build
        working-directory: domain/project/frontend
        run: |
          npm ci
          npm run build
        env:
          VITE_API_URL: ${{ secrets.API_URL }}
      - name: Deploy
        uses: cloudflare/wrangler-action@v3
        with:
          apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          command: pages deploy dist --project-name=my-project
          workingDirectory: domain/project/frontend
```

## Troubleshooting

### Coverage fails
```
FAILED: Coverage is below 70%
```
**Fix:** Lower threshold temporarily
```yaml
with:
  coverage_threshold: 60
```

### mypy errors
```
mypy found errors in 5 files
```
**Fix:** Disable during migration
```yaml
with:
  enable_mypy: false
```

### Tests not found
```
npm run test:run: command not found
```
**Fix:** Disable tests
```yaml
with:
  enable_tests: false
```

### Docker build fails
```
ERROR: Dockerfile not found
```
**Fix:** Disable Docker build
```yaml
with:
  enable_docker_build: false
```

### Build env vars not working
```
VITE_API_URL is undefined
```
**Fix:** Use proper JSON syntax
```yaml
with:
  build_env_vars: '{"VITE_API_URL": "https://api.example.com"}'
```

## Validation

### Test your workflow
```bash
# Validate YAML syntax
.github/workflows/validate-workflow.sh .github/workflows/my-project-ci.yml

# Test locally with act (requires Docker)
act push -W .github/workflows/my-project-ci.yml
```

## Input Reference

### Backend Inputs
| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `project_path` | ✅ Yes | - | Path to backend directory |
| `python_version` | No | `3.11` | Python version (3.11, 3.12, etc.) |
| `coverage_threshold` | No | `70` | Minimum coverage % |
| `enable_mypy` | No | `true` | Run mypy type checking |
| `enable_docker_build` | No | `true` | Verify Docker image builds |

### Frontend Inputs
| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `project_path` | ✅ Yes | - | Path to frontend directory |
| `node_version` | No | `20` | Node.js version |
| `enable_tests` | No | `true` | Run vitest/jest tests |
| `build_env_vars` | No | `{}` | Build env vars (JSON object) |

## Examples in This Repo

- **Simple:** `.github/workflows/voice-coach-ci.yml`
- **With Deployment:** `.github/workflows/interview-simulator-reusable.yml`

## Full Documentation

- **Usage Guide:** [README.md](./README.md)
- **Migration Guide:** [MIGRATION_CHECKLIST.md](./MIGRATION_CHECKLIST.md)
- **Sprint Summary:** [SPRINT9_SUMMARY.md](./SPRINT9_SUMMARY.md)

## Need Help?

1. Check [README.md](./README.md) troubleshooting section
2. Review [MIGRATION_CHECKLIST.md](./MIGRATION_CHECKLIST.md)
3. Look at example workflows in this directory
4. Create issue with `ci-migration` label

---

**Pro Tip:** Start with the simplest configuration and add features incrementally. You can always enable mypy, Docker builds, and tests later!
