# Release Checklist

**Audience**: Lead orchestrator, on-call engineer
**Last updated**: 2026-02-26
**Related**: `VOICE_COACH_DEPLOY_CHECKLIST.md`, `REVENUE_UNBLOCK_CHECKLIST.md`

Pareto coverage — 80% of risk, practical steps only. Skip items that clearly do not apply to the project being released.

---

## Phase 1 — Pre-Deploy (15 min)

### 1.1 Code Quality Gates

```bash
# From the project's backend directory
uv run ruff check .
uv run mypy .
uv run pytest --tb=short -q
```

- [ ] `ruff` exits 0 — no lint errors
- [ ] `mypy` exits 0 — no type errors (known exceptions: IS Stripe subscriptions.py — tracked)
- [ ] All tests pass — or known-failing tests are documented

### 1.2 Secret Hygiene

```bash
# Scan for accidental secrets committed to the repo
git log --oneline -20
git diff HEAD~1 -- . ':(exclude)*.lock' | grep -iE "(secret|token|password|api_key|sk_live|sk_test)" | head -20
```

- [ ] No secrets visible in recent commits
- [ ] `.env` is in `.gitignore` and not staged
- [ ] No Railway tokens, Stripe keys, or JWT secrets in any committed file

### 1.3 State Files Updated

- [ ] `docs/PROMPT.md` reflects current sprint state
- [ ] `docs/PLAN.md` sprint is up to date
- [ ] Migration files are committed and `alembic_version` is current:
  ```bash
  # In project directory
  alembic history --verbose | head -5
  ```

### 1.4 Branch / Merge Status

```bash
git log --oneline origin/main..HEAD
git status
```

- [ ] All changes committed on `main` (or feature branch merged to main)
- [ ] No staged/unstaged local changes that were not intended
- [ ] Submodules are at the correct commit if applicable

---

## Phase 2 — Deploy Steps (per domain)

### 2a. Backend — Railway (IS, Voice Coach, Code Atlas, any FastAPI project)

```bash
# Pre-deploy: confirm Railway CLI is installed and logged in
railway whoami

# Link to the correct project if not already linked
railway link

# Deploy from project backend directory
cd <domain>/<project>/app/backend
railway up

# Run database migrations immediately after successful deploy
railway run alembic upgrade head

# Confirm deploy registered
railway deployments | head -5
```

- [ ] Deploy exits 0 with no error
- [ ] Migrations applied: `railway run alembic current` shows latest revision
- [ ] No `CRITICAL` or `ERROR` in first 30 seconds of logs: `railway logs --tail 50`

**Required env vars (verify before first deploy):**

| Service | Minimum required vars |
|---------|-----------------------|
| Voice Coach | `JWT_SECRET_KEY`, `PAYG_WEBHOOK_SECRET`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` |
| Interview Simulator | `DATABASE_URL`, `JWT_SECRET_KEY`, `STRIPE_SECRET_KEY` |
| Code Atlas | `RAILWAY_TOKEN`, `CLOUDFLARE_API_TOKEN`, `VITE_API_URL` |
| Any project | `DATABASE_URL` (auto-set by Railway PostgreSQL), `PORT` (auto-set) |

### 2b. Frontend — Cloudflare Pages

```bash
# Install Wrangler if needed
npm install -g wrangler

# Build first
cd <domain>/<project>/app/frontend
npm run build

# Deploy
wrangler pages deploy dist --project-name=<project-name>
```

- [ ] Build exits 0
- [ ] Deploy URL resolves and loads in browser
- [ ] `VITE_API_URL` points to the correct Railway backend URL (not localhost)

### 2c. forged Daemon (internal only)

```bash
forge daemon start
# Or manually:
cd cmd/forged && go build -o forged . && ./forged --port 8081 --ws-port 8082 --db .forge/forge-v3.db
```

- [ ] Health endpoint responds: `curl http://localhost:8081/health`
- [ ] XNode listener is up: `forge node list`
- [ ] Fleet agents are online: `forge status`

### 2d. Stripe Webhooks (first deploy or URL change)

When Railway assigns a new URL, update the Stripe webhook endpoint:

1. Stripe Dashboard > Developers > Webhooks > Add endpoint
2. URL: `https://<railway-url>/api/billing/stripe-webhook`
3. Events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`
4. Copy the signing secret into Railway: `railway variables set STRIPE_WEBHOOK_SECRET="whsec_..."`

---

## Phase 3 — Post-Deploy Smoke Tests (5 min)

Run the smoke test script against the live URL:

```bash
./harness/scripts/smoke-test.sh https://<deployed-url>
```

Expected: all checks pass, exit code 0.

Manual spot-checks if smoke script is not available:

```bash
# Health
curl -sf https://<url>/api/health | python3 -m json.tool

# Unauthenticated access should return 401 (not 500 or 404)
curl -s -o /dev/null -w "%{http_code}" https://<url>/api/v1/sessions

# OpenAPI docs should be accessible
curl -sf -o /dev/null -w "%{http_code}" https://<url>/docs
```

### Login Flow (manual, ~2 min)

- [ ] Register a new account via the frontend or API
- [ ] Login returns a valid JWT (inspect the response, check `access_token` field)
- [ ] A protected endpoint called with that token returns 200, not 401

---

## Phase 4 — Rollback Procedure

Use these steps if a deploy causes degraded availability or data errors within 30 minutes of deploy.

### 4.1 Backend Rollback (Railway)

```bash
# List recent deployments
railway deployments

# Roll back to the previous good deployment
railway rollback <deployment-id>

# Or redeploy from a specific git commit
git checkout <last-good-sha>
railway up
```

- [ ] Rollback deploy exits 0
- [ ] Health check passes after rollback: `curl https://<url>/api/health`

### 4.2 Database Migration Rollback

Only run if the bad deploy included a schema migration that caused data errors.

```bash
# Downgrade one migration step
railway run alembic downgrade -1

# Verify current revision after downgrade
railway run alembic current
```

**WARNING**: Downgrading migrations can cause data loss. Only do this if:
- The migration ran in the last 30 minutes
- No new user data was written against the new schema
- You have confirmed a recent backup exists (Railway point-in-time recovery or `pg_dump`)

### 4.3 Frontend Rollback (Cloudflare Pages)

1. Cloudflare Dashboard > Pages > `<project-name>` > Deployments
2. Find the previous successful deployment
3. Click the three-dot menu > Rollback to this deployment

- [ ] Previous deployment is now active
- [ ] Frontend loads and `VITE_API_URL` still points to a healthy backend

### 4.4 Notify Stakeholders

If a public-facing service was down for more than 5 minutes:
- Post a brief status note in the relevant Slack channel
- Update `docs/PROMPT.md` with what happened and how it was fixed

---

## Quick Reference

| Step | Command |
|------|---------|
| Lint | `uv run ruff check .` |
| Type check | `uv run mypy .` |
| Run tests | `uv run pytest -q` |
| Deploy backend | `railway up` |
| Migrate DB | `railway run alembic upgrade head` |
| Deploy frontend | `wrangler pages deploy dist` |
| Smoke test | `./harness/scripts/smoke-test.sh <url>` |
| View logs | `railway logs -f` |
| Rollback backend | `railway rollback <id>` |
| Rollback DB | `railway run alembic downgrade -1` |
| Fleet health | `forge status` |
| CC health | `curl http://localhost:8081/health` |
