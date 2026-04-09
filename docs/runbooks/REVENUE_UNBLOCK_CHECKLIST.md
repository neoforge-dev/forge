# Revenue Unblock Checklist

Three human gates blocking revenue. Total time: ~60 minutes.

---

## 1. Code Atlas Deploy (~15 min)

**Repo:** `github.com/codeswiftr/code-atlas` (or wherever the Code Atlas submodule points)

### Add GitHub Secrets (Settings > Secrets and variables > Actions)

**Secrets** (Repository secrets):
```
RAILWAY_TOKEN         → Railway dashboard > Account Settings > Tokens > Create
CLOUDFLARE_API_TOKEN  → Cloudflare dashboard > My Profile > API Tokens > Create Token > "Edit Cloudflare Pages"
```

**Variables** (Repository variables):
```
CLOUDFLARE_ACCOUNT_ID → Cloudflare dashboard > right sidebar > Account ID
VITE_API_URL          → (set AFTER Railway deploy gives you the URL, e.g. https://code-atlas-production.up.railway.app/api/v1)
```

### Trigger deploy
```bash
# Option A: Push any change to trigger CD
cd codeswiftr-com/code-atlas && git commit --allow-empty -m "chore: trigger CD" && git push

# Option B: Manual trigger
# GitHub > Actions > CD > Run workflow
```

### Verify
- Railway: Check deployment health at `https://<railway-url>/health`
- Cloudflare: Visit the Pages URL, verify it loads

---

## 2. Voice Coach Deploy (~30 min)

### Railway setup
```bash
# Install Railway CLI (if not installed)
npm install -g @railway/cli

# Login and create project
railway login
railway init  # Or link to existing project

# Add PostgreSQL
# Railway Dashboard > New > Database > PostgreSQL

# Add Redis (optional but recommended)
# Railway Dashboard > New > Database > Redis
```

### Set environment variables
```bash
# Generate secrets
JWT_SECRET=$(openssl rand -hex 32)
PAYG_SECRET=$(openssl rand -hex 32)

# Set in Railway (minimum required)
railway variables set JWT_SECRET_KEY="$JWT_SECRET"
railway variables set PAYG_WEBHOOK_SECRET="$PAYG_SECRET"
railway variables set GROQ_API_KEY="<from groq.com/keys>"
railway variables set OPENROUTER_API_KEY="<from openrouter.ai/keys>"

# Stripe (for payments)
railway variables set STRIPE_SECRET_KEY="<from stripe.com/dashboard/apikeys>"
railway variables set STRIPE_WEBHOOK_SECRET="<from stripe.com/webhooks>"
```

### Deploy
```bash
cd brandfocus-ai/voice-coach/app/backend
railway up  # Deploys from Dockerfile

# Run migrations after first successful deploy
railway run alembic upgrade head

# Verify
curl https://<railway-url>/api/health
```

### Full guide
See `brandfocus-ai/voice-coach/DEPLOY_NOW.md` (35-min step-by-step)

---

## 3. IS Content Publishing (~2-4 hrs)

**29 pieces ready** in `codeswiftr-com/interview-simulator/content/`:

### Blog posts (20 pieces)
Publish to blog platform (WordPress/Ghost/etc.):
- See `docs/runbooks/IS_CONTENT_PUBLISHING_CHECKLIST.md` for full list
- Each post is ~600-1000 words, SEO-optimized
- Add UTM links: `?utm_source=blog&utm_medium=organic&utm_campaign=is_launch`

### LinkedIn posts (5 pieces)
- Copy from content directory
- Schedule via Buffer/LinkedIn: 1 post per day for a week
- Add link to app.codeswiftr.com with UTM tracking

### Quick publish order (highest impact first)
1. "Complete Behavioral Interview Guide" (evergreen SEO)
2. "STAR Method Examples" (high-volume search term)
3. "Top 10 Interview Mistakes" (social shareability)
4. LinkedIn announcement post
5. Remaining pieces over next 2 weeks

---

## Verification After All Deploys

```bash
# Code Atlas
curl -s https://<code-atlas-url>/health | python3 -m json.tool

# Voice Coach
curl -s https://<voice-coach-url>/api/health | python3 -m json.tool

# Interview Simulator (already live)
curl -s https://interview-simulator-api-production.up.railway.app/health
```

---

**After completing these three gates, the revenue ratio flips from 8% to 80% product work.**
