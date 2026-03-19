# GitHub Actions Workflow Migration Checklist

This checklist helps migrate existing project-specific workflows to the new reusable templates.

## Pre-Migration Assessment

- [ ] Review existing workflow at `.github/workflows/<project>-ci.yml`
- [ ] Identify backend and/or frontend components
- [ ] Check for custom CI steps not covered by templates
- [ ] Document any deployment steps (Railway, Cloudflare, etc.)

## Backend Migration

### 1. Identify Current Configuration

Current workflow uses:
- [ ] Python version: ___________
- [ ] Package manager: pip / uv / poetry
- [ ] Linter: ruff / flake8 / pylint
- [ ] Type checker: mypy / pyright
- [ ] Test framework: pytest
- [ ] Database: PostgreSQL / MySQL / SQLite
- [ ] Cache: Redis / Memcached
- [ ] Coverage threshold: ___________
- [ ] Docker build: Yes / No

### 2. Map to Reusable Template

```yaml
backend-ci:
  uses: ./.github/workflows/reusable-backend-ci.yml
  with:
    project_path: 'domain/project/backend'
    python_version: '3.11'              # From step 1
    coverage_threshold: 70              # From step 1
    enable_mypy: true                   # If using mypy
    enable_docker_build: false          # If no Dockerfile
```

### 3. Migration Steps

- [ ] Create new workflow file: `.github/workflows/<project>-ci-new.yml`
- [ ] Add backend job using reusable template
- [ ] Test on feature branch
- [ ] Verify all checks pass
- [ ] Rename old workflow: `<project>-ci.yml.bak`
- [ ] Rename new workflow: `<project>-ci.yml`
- [ ] Delete backup after 1 week

## Frontend Migration

### 1. Identify Current Configuration

Current workflow uses:
- [ ] Node version: ___________
- [ ] Package manager: npm / yarn / pnpm
- [ ] Linter: eslint
- [ ] Framework: React / Vue / Svelte
- [ ] Build tool: Vite / Webpack / Next.js
- [ ] Test framework: Vitest / Jest
- [ ] Build env vars: ___________

### 2. Map to Reusable Template

```yaml
frontend-ci:
  uses: ./.github/workflows/reusable-frontend-ci.yml
  with:
    project_path: 'domain/project/frontend'
    node_version: '20'                  # From step 1
    enable_tests: true                  # If tests exist
    build_env_vars: '{}'                # From step 1
```

### 3. Migration Steps

- [ ] Add frontend job using reusable template
- [ ] Configure build env vars as JSON
- [ ] Test on feature branch
- [ ] Verify build artifacts are created
- [ ] Merge to main

## Full Project Migration Example

### Before (140 lines)

```yaml
name: My Project CI

on:
  push:
    branches: [main]
    paths:
      - 'my-domain/my-project/**'

jobs:
  backend-test:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: my-domain/my-project/backend
    services:
      postgres:
        image: postgres:16
        # ... 20 more lines
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        # ... 30 more lines

  frontend-test:
    runs-on: ubuntu-latest
    # ... 40 more lines

  deploy:
    # ... 30 more lines
```

### After (40 lines)

```yaml
name: My Project CI

on:
  push:
    branches: [main]
    paths:
      - 'my-domain/my-project/**'
      - '.github/workflows/my-project-ci.yml'
      - '.github/workflows/reusable-*.yml'

jobs:
  backend-ci:
    uses: ./.github/workflows/reusable-backend-ci.yml
    with:
      project_path: 'my-domain/my-project/backend'
      python_version: '3.11'

  frontend-ci:
    uses: ./.github/workflows/reusable-frontend-ci.yml
    with:
      project_path: 'my-domain/my-project/frontend'

  deploy:
    needs: [backend-ci, frontend-ci]
    # ... deployment steps unchanged
```

**Savings:** 100 lines removed, 71% reduction

## Common Migration Patterns

### Pattern 1: Different Python Version

```yaml
# Old: Python 3.12
- name: Set up Python
  uses: actions/setup-python@v5
  with:
    python-version: '3.12'

# New: Just specify version
with:
  python_version: '3.12'
```

### Pattern 2: Custom Database

```yaml
# Old: MySQL instead of PostgreSQL
services:
  mysql:
    image: mysql:8.0
    env:
      MYSQL_DATABASE: test_db

# New: Template uses PostgreSQL by default
# Update your tests to use PostgreSQL or
# keep custom MySQL service in project workflow
```

### Pattern 3: Additional Test Commands

```yaml
# Old: Security scanning after tests
- name: Run tests
  run: pytest
- name: Security scan
  run: bandit -r app/

# New: Add custom job after reusable template
jobs:
  backend-ci:
    uses: ./.github/workflows/reusable-backend-ci.yml
    # ...

  security-scan:
    needs: [backend-ci]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Security scan
        run: bandit -r app/
```

### Pattern 4: Build Environment Variables

```yaml
# Old: Multiple env vars
- name: Build
  run: npm run build
  env:
    VITE_API_URL: ${{ secrets.API_URL }}
    VITE_POSTHOG_KEY: ${{ secrets.POSTHOG_KEY }}
    VITE_ENV: production

# New: Pass as JSON
with:
  build_env_vars: |
    {
      "VITE_API_URL": "${{ secrets.API_URL }}",
      "VITE_POSTHOG_KEY": "${{ secrets.POSTHOG_KEY }}",
      "VITE_ENV": "production"
    }
```

## Testing Your Migration

### 1. Create Test Branch

```bash
git checkout -b test/workflow-migration
```

### 2. Update Workflow

```bash
cp .github/workflows/my-project-ci.yml .github/workflows/my-project-ci.yml.bak
# Edit .github/workflows/my-project-ci.yml with new reusable template
```

### 3. Test Locally (Optional)

```bash
# Install act for local testing
brew install act

# Run workflow locally
act push -W .github/workflows/my-project-ci.yml
```

### 4. Test on GitHub

```bash
git add .github/workflows/my-project-ci.yml
git commit -m "test: migrate to reusable workflow templates"
git push origin test/workflow-migration
```

### 5. Verify Checks Pass

- [ ] All jobs complete successfully
- [ ] Coverage reports uploaded
- [ ] Build artifacts created
- [ ] No regression in test results

### 6. Merge to Main

```bash
git checkout main
git merge test/workflow-migration
git push origin main
```

## Rollback Plan

If migration causes issues:

```bash
# Restore backup
cp .github/workflows/my-project-ci.yml.bak .github/workflows/my-project-ci.yml
git add .github/workflows/my-project-ci.yml
git commit -m "revert: rollback workflow migration"
git push origin main
```

## Migration Priority Order

### Phase 1: Low-Risk Projects (Week 1)
- [ ] Marketing Template (simple, no backend)
- [ ] Desk Workout Generator (small, low traffic)

### Phase 2: Medium Projects (Week 2)
- [ ] Voice Coach (well-tested, active development)
- [ ] Interview Simulator (already using similar pattern)

### Phase 3: Critical Projects (Week 3+)
- [ ] Command Center (requires careful testing)
- [ ] AdGuild Platform (production traffic)

## Success Metrics

Track these metrics to measure migration success:

- [ ] **Workflow file size:** Average reduction of 60-80%
- [ ] **CI runtime:** No increase (should be same or faster)
- [ ] **Maintenance time:** Reduce duplicated work across projects
- [ ] **Consistency:** All projects use same quality gates

## Support

**Questions or Issues?**
- Review [README.md](./README.md) for usage examples
- Check [GitHub Actions docs](https://docs.github.com/en/actions/using-workflows/reusing-workflows)
- Create issue in FORGE repo with `ci-migration` label

**Migration Help:**
- Ping DevOps team in `#infrastructure` Slack channel
- Schedule pairing session for complex migrations
