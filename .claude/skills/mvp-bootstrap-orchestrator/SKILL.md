---
name: mvp-bootstrap-orchestrator
description: Plan and kick off a new domain MVP by harvesting the living documentation pyramid, selecting the right playbooks, and producing a ready-to-execute backlog.
auto_execute: true
disable-model-invocation: false
allowed-tools: [Read, Write, Bash, Skill]
---

# MVP Bootstrap Orchestrator

End-to-end workflow for bootstrapping a new MVP in the FORGE portfolio, from domain selection and market analysis through to first deployment.

## When to Use

- **New MVP**: Starting a new project from scratch
- **Domain expansion**: Adding a new product to an existing domain
- **Market validation**: Testing a new niche or product category
- **Portfolio rebalancing**: Prioritizing among 89 projects across 11 domains
- **Idea intake**: Converting external ideas into FORGE-ready projects

## Prerequisites

- Domain exists in portfolio (see `.claude/modules/project-registry.md`)
- Access to portfolio context (`docs/00-portfolio-digest.md`)
- Understanding of FORGE tech stack (`.claude/modules/tech-stack.md`)
- Human approval for new domain creation (if needed)

---

## Complete Bootstrap Workflow

### Phase 0: Domain Selection & Market Analysis

**Objective:** Choose the right domain and validate market opportunity.

#### Step 0.1: Review Portfolio Context

Read strategic documents to understand current priorities:

```bash
# Essential context files
/Users/bogdan/work/FORGE/docs/00-portfolio-digest.md  # Portfolio status
/Users/bogdan/work/FORGE/.claude/modules/project-registry.md  # All domains/projects
/Users/bogdan/work/FORGE/docs/PLAN.md  # Current sprint priorities
/Users/bogdan/work/FORGE/docs/CTO_STRATEGY_REVIEW.md  # Strategic focus areas
```

**Priority Tier System:**
- **Tier 1 (60%)**: Revenue-generating products (Interview Simulator, Voice Coach)
- **Tier 2 (25%)**: Authority-building B2B tools (Tech Debt Analyzer, GraphRAG Patterns)
- **Tier 3 (10%)**: Consumer/games
- **Tier 4 (5%)**: COPPA-blocked (requires compliance first)
- **Tier 5**: Development/early stage

**Validate:**
- [ ] New MVP aligns with current strategic focus
- [ ] Domain has capacity (not overloaded with active projects)
- [ ] Tier assignment is appropriate

#### Step 0.2: Run Niche Explorer

Use the `/niche-explorer` skill to perform structured market analysis:

```bash
/niche-explorer

Domain: {domain-name}
Focus: {target market/problem}
Constraints:
- Compliance requirements (COPPA/HIPAA/GDPR)
- Target users
- Tech stack preferences
```

**Niche Explorer outputs:**
- Market size estimates (TAM/SAM/SOM)
- Competitive landscape (3-5 competitors analyzed)
- Gap analysis (underserved segments)
- Opportunity scoring (market size, competition, fit score)
- MVP recommendation with differentiation

**Output location:** `{domain}/explorations/{mvp-name}-exploration.md`

**Quality checklist:**
- [ ] At least 3 competitors analyzed with strengths/weaknesses
- [ ] Market size estimates with data sources
- [ ] Clear differentiation from existing solutions
- [ ] Specific target user personas defined
- [ ] Compliance requirements identified
- [ ] Fit score justification provided

#### Step 0.3: Human Decision Gate

Before proceeding, get human approval for:
- **New domain creation** (if creating a new domain)
- **Tier placement** (which priority tier)
- **Resource allocation** (% of portfolio effort)
- **Timeline commitment** (2 weeks, 4 weeks, etc.)

**Document decision** in `docs/PROMPT.md` with timestamp.

---

### Phase 1: MVP Specification & Planning

**Objective:** Convert market opportunity into actionable feature backlog.

#### Step 1.1: Run MVP Spec Writer

Use the `/mvp-spec-writer` skill to generate features.json:

```bash
/mvp-spec-writer

Exploration: {domain}/explorations/{mvp-name}-exploration.md
Project: {project-name}
Constraints:
- Backend: FastAPI (or Go for specific use cases)
- Frontend: React + Vite (default) or Lit PWA (simple landing pages)
- Auth: JWT (or OAuth2 for enterprise)
- Max P0 features: 7 (MVP blocker features only)
- Target completion: 2-4 weeks
- Compliance: [COPPA/HIPAA/GDPR requirements]
```

**MVP Spec Writer outputs:**
- `features.json` with prioritized backlog (P0/P1/P2)
- Dependency graph between features
- Complexity estimates (low/medium/high)
- Acceptance criteria for each feature
- Tech stack configuration
- Estimated total effort

**Feature priority framework:**
- **P0 (Must Have)**: Core value proposition, essential flows, basic auth
- **P1 (Should Have)**: Enhanced UX, analytics, performance
- **P2 (Nice to Have)**: Advanced features, integrations, admin tools

**Quality checklist:**
- [ ] 5-7 P0 features (not too many for MVP)
- [ ] Clear acceptance criteria (testable)
- [ ] Realistic complexity estimates
- [ ] Proper dependency ordering
- [ ] Compliance requirements in technical notes
- [ ] Total effort estimate reasonable for timeline

#### Step 1.2: Tech Stack Selection

Based on `.claude/modules/tech-stack.md` standards:

**Backend (choose one):**
- **FastAPI** (default): Async-first Python, SQLModel, PostgreSQL
- **Go**: For high-performance services, real-time features

**Frontend (choose one):**
- **React + Vite** (default): Complex dashboards, forms, state management
- **Lit PWA**: Simple landing pages, minimal JS, web components

**Database:**
- **PostgreSQL** (default): Relational data, ACID transactions
- **MongoDB**: Document-based, flexible schema (rare use cases)

**Package managers:**
- **Python**: Astral `uv` (MANDATORY - never pip)
- **Node**: npm or pnpm

**Deployment:**
- **Backend**: Railway (auto-deploy from main)
- **Frontend**: Cloudflare Pages (auto-deploy from main)
- **Database**: Railway PostgreSQL
- **CDN**: Cloudflare

**Document in features.json:**
```json
{
  "tech_stack": {
    "backend": "FastAPI",
    "frontend": "React + Vite",
    "database": "PostgreSQL",
    "auth": "JWT",
    "deployment": "Railway + Cloudflare Pages"
  }
}
```

#### Step 1.3: Create Directory Structure

**Standard FORGE project structure:**

```
{domain}/{project}/
├── backend/
│   ├── app/
│   │   ├── api/routes/          # Endpoints grouped by domain
│   │   ├── auth/                # JWT + product access
│   │   ├── config.py            # Pydantic Settings
│   │   ├── exceptions.py        # Structured AppError
│   │   ├── infrastructure/      # DB, storage, external APIs
│   │   ├── middleware/          # Rate limit, request ID, security
│   │   ├── services/            # Business logic
│   │   └── main.py
│   ├── tests/
│   ├── alembic/                 # Database migrations
│   ├── pyproject.toml           # uv dependencies
│   ├── Makefile                 # dev, test, format, lint
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ui/              # Generic (Button, Modal)
│   │   │   └── [feature]/       # Feature-specific
│   │   ├── pages/               # Route components
│   │   ├── api/                 # API clients
│   │   ├── hooks/               # Custom React hooks
│   │   ├── stores/              # Zustand stores
│   │   ├── types/               # TypeScript types
│   │   └── utils/               # Helpers, formatters
│   ├── public/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── .env.example
├── docs/
│   ├── CLAUDE.md                # Project-level instructions (Level 2)
│   ├── project-brief.md         # MVP overview
│   ├── PLAN.md                  # Feature backlog
│   ├── active-context.md        # Current status
│   └── api.md                   # API documentation
├── features.json                # Output from mvp-spec-writer
├── .github/
│   └── workflows/
│       └── {project}-ci.yml     # CI/CD pipeline
└── docker-compose.yml           # Local development
```

**Create structure:**
```bash
# Run this from FORGE root
mkdir -p {domain}/{project}/{backend/app,frontend/src,docs}
```

#### Step 1.4: Create Living Documentation Pyramid

**Level 0 (Portfolio):** Already exists in `docs/00-portfolio-digest.md`

**Level 1 (Domain):** Update or create `{domain}/CLAUDE.md`

Template:
```markdown
# {Domain Name} Domain Rules

## Project Registry
| Project | Target Users | Tech Stack | Status | Tier |
|---------|--------------|------------|--------|------|
| {project} | {users} | FastAPI + React | In Progress | {tier} |

## Tech Stack Overview
[Backend, Frontend, Deployment patterns]

## Quality Standards
[Performance requirements, Test coverage, API standards]

## Human Gates
[Domain-specific escalation rules]

## Quick Commands
```bash
cd {project}/backend && uv sync && make dev
```
```

**Level 2 (Project):** Create `{domain}/{project}/docs/CLAUDE.md`

Template:
```markdown
# {Project Name}

**Status:** {Status}
**Tier:** {Tier}
**Target Users:** {Users}

## Quick Start
```bash
# Backend
cd backend && uv sync && make dev

# Frontend
cd frontend && npm install && npm run dev
```

## Features
[Link to features.json or backlog]

## Tech Stack
[Backend, Frontend, Database, Auth, Deployment]

## Development Workflow
[Local setup, testing, deployment]

## Human Gates
[Project-specific escalation rules]
```

**Level 3 (Feature):** Created during development in `docs/` as needed

**Validate:**
- [ ] All three pyramid levels created
- [ ] Domain CLAUDE.md updated with new project
- [ ] Project CLAUDE.md has quick start commands
- [ ] Features.json referenced in docs

---

### Phase 2: GitHub Setup & Issue Tracking

**Objective:** Set up version control and task tracking.

#### Step 2.1: Initialize Git Repository

```bash
# From project root
cd {domain}/{project}
git init
git add .
git commit -m "feat: initial project scaffold for {project}

- Backend structure (FastAPI + PostgreSQL)
- Frontend structure (React + Vite)
- Living documentation pyramid (Levels 1-2)
- features.json with P0/P1/P2 backlog

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

**Create .gitignore:**
```
# Python
__pycache__/
*.py[cod]
.venv/
*.log

# Node
node_modules/
dist/
.env
.env.local

# IDE
.vscode/
.idea/

# OS
.DS_Store
```

#### Step 2.2: Create GitHub Repository

```bash
# Using GitHub CLI
gh repo create {domain}-{project} --private --description "{Project description}"

# Push initial commit
git remote add origin git@github.com:{org}/{domain}-{project}.git
git branch -M main
git push -u origin main
```

#### Step 2.3: Convert features.json to GitHub Issues

For each feature in `features.json`, create a GitHub issue:

```bash
# Example for feature PS-001
gh issue create \
  --title "[P0] Project scaffold with FastAPI backend" \
  --label "P0,backend,infrastructure" \
  --body "$(cat <<'EOF'
**Feature ID:** PS-001
**Priority:** P0 (MVP Blocker)
**Type:** Infrastructure
**Complexity:** Medium (4 hours)

## Acceptance Criteria
- [ ] FastAPI app with health endpoint returns 200
- [ ] SQLAlchemy async setup with User model
- [ ] Pytest configuration with 1+ passing test
- [ ] Docker setup with hot reload

## Dependencies
None

## Technical Notes
Use /fastapi-service-template skill

---
From features.json
EOF
)"
```

**Automate with script:**
```bash
# Use harness script to bulk-create issues
python harness/scripts/features-to-issues.py \
  --features {domain}/{project}/features.json \
  --repo {org}/{domain}-{project}
```

**Labels to create:**
- Priority: `P0`, `P1`, `P2`
- Type: `backend`, `frontend`, `infrastructure`, `content`
- Complexity: `low`, `medium`, `high`

**Validate:**
- [ ] All P0 features have GitHub issues
- [ ] Issues have proper labels
- [ ] Dependency order is clear
- [ ] Acceptance criteria are testable

---

### Phase 3: Infrastructure & Deployment Setup

**Objective:** Configure Railway, Cloudflare, and CI/CD pipelines.

#### Step 3.1: Railway Backend Setup

**Prerequisites:**
- Railway account with payment method
- PostgreSQL add-on available

**Steps:**
1. Create new Railway project: `{domain}-{project}-api`
2. Add PostgreSQL database
3. Link GitHub repo (auto-deploy from main)
4. Configure environment variables:

```bash
# Required for all projects
DATABASE_URL=${{Postgres.DATABASE_URL}}  # Auto-injected
REDIS_URL=${{Redis.REDIS_URL}}          # If using Redis
SECRET_KEY=<generate-random-256-bit-key>
ENVIRONMENT=production

# Project-specific (example)
OPENAI_API_KEY=<from-1password>
STRIPE_SECRET_KEY=<from-1password>
STRIPE_WEBHOOK_SECRET=<from-stripe-dashboard>
```

**Generate SECRET_KEY:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

5. Configure Railway settings:
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path:** `/health`
   - **Restart Policy:** On failure
   - **Auto-deploy:** Enabled from `main` branch

6. Get Railway domain: `{project}-api-production.up.railway.app`

#### Step 3.2: Cloudflare Pages Frontend Setup

**Prerequisites:**
- Cloudflare account
- Domain registered (or use `pages.dev` subdomain)

**Steps:**
1. Create new Pages project: `{domain}-{project}`
2. Link GitHub repo (auto-deploy from main)
3. Configure build settings:
   - **Framework preset:** Vite
   - **Build command:** `npm run build`
   - **Build output directory:** `dist`
   - **Root directory:** `frontend`

4. Environment variables:
```bash
VITE_API_URL=https://{project}-api-production.up.railway.app
VITE_ENVIRONMENT=production
```

5. Configure custom domain (optional):
   - Add CNAME: `{project}.{domain}.com` → `{project}.pages.dev`
   - SSL/TLS: Full (strict)

6. Get Cloudflare URL: `{project}.pages.dev` or custom domain

#### Step 3.3: Configure CORS

Update backend `app/main.py`:

```python
from fastapi.middleware.cors import CORSMiddleware

origins = [
    "http://localhost:5173",  # Local development
    "https://{project}.pages.dev",  # Cloudflare Pages
    "https://{project}.{domain}.com",  # Custom domain
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

#### Step 3.4: Create GitHub Actions CI/CD

Create `.github/workflows/{project}-ci.yml`:

```yaml
name: {Project} CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  backend-tests:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: {domain}/{project}/backend

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v1
        with:
          version: "latest"

      - name: Set up Python
        run: uv python install 3.11

      - name: Install dependencies
        run: uv sync

      - name: Run tests
        run: uv run pytest tests/ -v --cov=app --cov-report=term

      - name: Lint with ruff
        run: |
          uv run ruff check app/
          uv run ruff format --check app/

  frontend-tests:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: {domain}/{project}/frontend

    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Run tests
        run: npm run test

      - name: Lint
        run: npm run lint

      - name: Type check
        run: npm run type-check
```

**Validate:**
- [ ] Railway project created with PostgreSQL
- [ ] Cloudflare Pages project created
- [ ] Custom domain configured (if applicable)
- [ ] CORS origins include all environments
- [ ] CI/CD pipeline passes on push

---

### Phase 4: Development Kickoff

**Objective:** Start building features with Ralph Loop or agents.

#### Step 4.1: Choose Development Mode

**Option A: Autonomous Development (Ralph Loop)**

For fully autonomous feature implementation:

```bash
# Run Ralph Loop with features.json
cd harness
uv run forge loop run \
  --domain {domain} \
  --project {project} \
  --features ../{domain}/{project}/features.json

# Or continuous iteration mode
uv run forge loop run \
  --domain {domain} \
  --project {project}
```

**Option B: Agent Fleet Dispatch**

For orchestrator-directed development:

```bash
# Dispatch to specialized agent via forge
forge dispatch send forge:tech "Build the backend for {project} following features.json P0 backlog"

# Monitor progress
forge fleet status --node tech
```

**Option C: Manual Development**

For hands-on development with Claude assistance:

```bash
# Start backend
cd {domain}/{project}/backend
uv sync
make dev

# Start frontend (new terminal)
cd {domain}/{project}/frontend
npm install
npm run dev
```

#### Step 4.2: Track Progress

**Update project status:**
- Mark GitHub issues as "In Progress" when started
- Update `docs/active-context.md` with current focus
- Log progress in `docs/PROMPT.md` for handoffs

**Monitor quality gates:**
- Backend tests: 70%+ coverage
- Frontend tests: 60%+ coverage
- Linting: 0 errors
- Security: No critical vulnerabilities

#### Step 4.3: First Feature Checkpoint

After completing first P0 feature:

**Validate:**
- [ ] Health endpoint returns 200
- [ ] Database connection works
- [ ] Docker setup runs locally
- [ ] Tests pass in CI/CD
- [ ] Documentation updated

**Commit:**
```bash
git add .
git commit -m "feat: implement {feature-title}

{acceptance-criteria-summary}

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
git push origin main
```

---

### Phase 5: Pre-Launch Checklist

**Objective:** Ensure MVP is production-ready before launch.

#### Step 5.1: Quality Validation

**Backend:**
- [ ] All P0 endpoints implemented and tested
- [ ] Auth/JWT token generation works
- [ ] Database migrations run successfully
- [ ] Error handling returns proper status codes
- [ ] Rate limiting configured
- [ ] Logging includes request_id
- [ ] Health/readiness endpoints respond
- [ ] Test coverage ≥70%

**Frontend:**
- [ ] All P0 user flows implemented
- [ ] Forms validate input client-side
- [ ] Loading states on async actions
- [ ] Error messages user-friendly
- [ ] Responsive design (mobile/tablet/desktop)
- [ ] Accessibility (ARIA labels, keyboard nav)
- [ ] PWA manifest (if applicable)
- [ ] Test coverage ≥60%

**Infrastructure:**
- [ ] Railway deployment successful
- [ ] Cloudflare Pages deployment successful
- [ ] Environment variables set correctly
- [ ] CORS configured for all origins
- [ ] Database backups enabled
- [ ] Monitoring/alerting configured (Sentry)

#### Step 5.2: Security Audit

**Run dependency audit:**
```bash
# Backend
cd backend
uv run pip-audit

# Frontend
cd frontend
npm audit --production
```

**Security checklist:**
- [ ] No hardcoded secrets in code
- [ ] JWT secret is 256-bit random
- [ ] Password hashing uses bcrypt/argon2
- [ ] SQL injection prevention (parameterized queries)
- [ ] XSS prevention (Content-Security-Policy headers)
- [ ] HTTPS enforced on all endpoints
- [ ] Rate limiting on auth endpoints

**Human gate:** Security changes require human review (see `.claude/modules/human-gates.md`)

#### Step 5.3: Compliance Validation

**COPPA (if TheBrightHarbor domain):**
- [ ] Parents are account owners (not children)
- [ ] No personal info collected from children <13
- [ ] Privacy policy links visible
- [ ] Parental consent flow implemented
- [ ] Data deletion on request

**HIPAA-lite (if CalmConnect domain):**
- [ ] Health data encrypted at rest
- [ ] No PHI in logs
- [ ] Audit trail for data access
- [ ] Data export on request

**GDPR (all domains):**
- [ ] Cookie consent banner
- [ ] Privacy policy published
- [ ] Data export endpoint
- [ ] Account deletion endpoint

**Human gate:** Compliance changes require human review

#### Step 5.4: Deployment Checklist

**Pre-deploy:**
- [ ] All tests passing in CI/CD
- [ ] Staging environment validated (if applicable)
- [ ] Database migration dry-run successful
- [ ] Rollback plan documented

**Deploy:**
```bash
# Backend auto-deploys on push to main
git push origin main

# Monitor Railway deployment logs
railway logs --project {project}-api-production

# Frontend auto-deploys on push to main
# Monitor Cloudflare Pages deployment
```

**Post-deploy:**
- [ ] Health endpoint returns 200
- [ ] Database migrations applied
- [ ] Auth flow works end-to-end
- [ ] Error rates <1%
- [ ] Response times <200ms (p95)
- [ ] Monitor for 30 minutes

**Human gate:** Production deployments to Tier 1 projects require human approval

---

## Output Checklist

After running the full bootstrap workflow, you should have:

### Documentation
- [ ] Exploration report: `{domain}/explorations/{mvp-name}-exploration.md`
- [ ] Features.json: `{domain}/{project}/features.json`
- [ ] Domain CLAUDE.md updated: `{domain}/CLAUDE.md`
- [ ] Project CLAUDE.md created: `{domain}/{project}/docs/CLAUDE.md`
- [ ] Project brief: `{domain}/{project}/docs/project-brief.md`
- [ ] Active context: `{domain}/{project}/docs/active-context.md`

### Infrastructure
- [ ] Git repository initialized
- [ ] GitHub repository created
- [ ] GitHub issues created for all P0 features
- [ ] Railway project configured with PostgreSQL
- [ ] Cloudflare Pages project configured
- [ ] CI/CD pipeline passing

### Code
- [ ] Backend scaffold with FastAPI
- [ ] Frontend scaffold with React + Vite
- [ ] Docker setup for local development
- [ ] Test infrastructure (pytest + vitest)
- [ ] Linting/formatting configured (ruff + eslint)
- [ ] At least 1 passing test (backend + frontend)

### Deployment
- [ ] Backend deployed to Railway
- [ ] Frontend deployed to Cloudflare Pages
- [ ] Health endpoint responding
- [ ] Database migrations applied
- [ ] Environment variables configured

### Quality Gates Passed
- [ ] Security audit clean
- [ ] Compliance requirements identified
- [ ] Test coverage ≥70% (backend), ≥60% (frontend)
- [ ] Human gates documented for regulated changes

---

## Related Skills

- `/niche-explorer` - Market analysis and opportunity identification (prerequisite)
- `/mvp-spec-writer` - Convert exploration to features.json (prerequisite)
- `/living-docs update` - Update documentation pyramid
- `/forge loop run` - Autonomous feature development
- `/content-library-producer` - Generate marketing content library
- `/compliance-playbook-writer` - Generate compliance documentation

---

## Common Issues & Solutions

### Issue: "Too many P0 features in features.json"
**Solution:** Review with CTO. MVP should have 5-7 P0 features max. Move enhancements to P1.

### Issue: "Domain not in project registry"
**Solution:** Get human approval to create new domain. Update `.claude/modules/project-registry.md`.

### Issue: "Railway deployment fails with 'Database connection refused'"
**Solution:** Check `DATABASE_URL` environment variable. Ensure PostgreSQL add-on attached.

### Issue: "CORS errors on frontend after deployment"
**Solution:** Add Cloudflare Pages URL to `origins` in `app/main.py`. Redeploy backend.

### Issue: "CI/CD pipeline fails with 'uv: command not found'"
**Solution:** Ensure GitHub Actions workflow includes `astral-sh/setup-uv` step.

### Issue: "Compliance requirements unclear"
**Solution:** Run `/compliance-playbook-writer` skill. Escalate to human for COPPA/HIPAA.

---

## Example Invocation

```
/mvp-bootstrap-orchestrator

Domain: babybit-es
Project: pedi-sync
Description: Share baby nutrition data with pediatricians
Tier: 2 (Authority Building)
Timeline: 2 weeks
Compliance: COPPA (parents are account owners)
```

**Expected output:**
1. Niche exploration report with market validation
2. features.json with 6 P0 features
3. GitHub repo with issues created
4. Railway + Cloudflare deployment configured
5. First feature implemented and deployed
6. All quality gates passed
