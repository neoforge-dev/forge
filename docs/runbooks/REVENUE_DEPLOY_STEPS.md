# Revenue Deploy Steps

Quick-deploy guide for Code Atlas and Voice Coach. Copy-paste commands.

---

## Code Atlas Deploy (~15 min)

### 1. Railway Login (token may be expired)

```bash
# Check if logged in
railway whoami

# If not logged in:
railway login
# Opens browser - authenticate with GitHub
```

### 2. Set GitHub Secrets

**Repo:** `github.com/codeswiftr/code-atlas`

In repo Settings → Secrets and variables → Actions:

**Secrets:**
| Secret | Value |
|--------|-------|
| `RAILWAY_TOKEN` | Railway dashboard → Account Settings → Tokens → Create |

**Variables:**
| Variable | Value |
|----------|-------|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard (right sidebar) |
| `VITE_API_URL` | Set AFTER Railway deploy (see step 4) |

### 3. Trigger Deploy

```bash
# Option A: Push to main
cd codeswiftr-com/code-atlas
git commit --allow-empty -m "chore: trigger CD" && git push

# Option B: GitHub Actions manual trigger
gh workflow run cd.yml -f ref=main
```

### 4. Get Railway URL (AFTER deploy)

```bash
# Check Railway dashboard or:
railway service list
# Look for the production service URL
```

### 5. Update VITE_API_URL Variable

In GitHub repo Settings → Variables:
- Add `VITE_API_URL` = `https://<your-railway-url>/api/v1`

### 6. Verify

```bash
# Backend health
curl -s https://<railway-url>/health | python3 -m json.tool

# Frontend loads
# Visit: https://code-atlas.pages.dev (or custom domain)
```

---

## Voice Coach Deploy (~20 min)

### 1. Railway Login

```bash
railway whoami
railway login  # If needed
```

### 2. Link Project

```bash
cd brandfocus-ai/voice-coach/app
railway link
# Select existing project or create new
```

### 3. Set Environment Variables

```bash
# Generate secrets
JWT_SECRET=$(openssl rand -hex 32)
PAYG_SECRET=$(openssl rand -hex 32)

# Required
railway variables set JWT_SECRET_KEY="$JWT_SECRET"
railway variables set PAYG_WEBHOOK_SECRET="$PAYG_SECRET"
railway variables set GROQ_API_KEY="gsk_..."
railway variables set OPENROUTER_API_KEY="sk-or-v1-..."

# Stripe (for payments)
railway variables set STRIPE_SECRET_KEY="sk_live_..."
railway variables set STRIPE_WEBHOOK_SECRET="whsec_..."

# Optional (recommended)
railway variables set POSTHOG_API_KEY="phc_..."
railway variables set SENTRY_DSN="https://...@sentry.io/..."
railway variables set RESEND_API_KEY="re_..."
```

### 4. Add Database

```bash
# Railway dashboard → Your Project → + New → Database → PostgreSQL
# Or CLI:
railway add postgresql
```

### 5. Deploy

```bash
cd brandfocus-ai/voice-coach/app
railway up --detach
```

### 6. Run Migrations (first deploy only)

```bash
railway run alembic upgrade head
```

### 7. Verify

```bash
# Backend health
curl -s https://<railway-url>/api/health | python3 -m json.tool

# Frontend
# Visit Cloudflare Pages URL (configure in Railway if needed)
```

---

## Post-Deploy Verification

```bash
# Code Atlas
curl -s https://<code-atlas-url>/health

# Voice Coach
curl -s https://<voice-coach-url>/api/health

# Interview Simulator (should already work)
curl -s https://interview-simulator-api-production.up.railway.app/health
```

---

## Rollback (if needed)

```bash
# Railway dashboard → Deployments → Previous deployment → Redeploy
# Or CLI:
railway deployments  # List
railway redeploy <deployment-id>
```

---

## Reference

- Full VC guide: `brandfocus-ai/voice-coach/DEPLOY_NOW.md`
- Code Atlas CLAUDE: `codeswiftr-com/code-atlas/CLAUDE.md`
- Revenue checklist: `docs/runbooks/REVENUE_UNBLOCK_CHECKLIST.md`
