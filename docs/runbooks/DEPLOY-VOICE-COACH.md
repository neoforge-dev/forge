# Voice Coach Deploy Runbook

**Status:** READY — human gates only
**Est. Time:** 35 minutes (after API keys)
**Revenue Target:** $2-3K/mo

## Pre-Deploy (5 min, automated)

- [ ] Fix ecdsa dependency (CVE-2024-23342): update to ≥0.19.2 in pyproject.toml
- [ ] Build forge-shared wheel:
  ```bash
  cd forge-shared && python -m build --wheel
  cp dist/forge_shared-*.whl ../services/voice-coach/app/backend/wheels/
  ```

## Human Gate 1: API Keys (10 min)

Obtain these keys and set them as Railway environment variables:

| Key | Source | Required |
|-----|--------|----------|
| `GROQ_API_KEY` | console.groq.com | Yes — transcription |
| `OPENROUTER_API_KEY` | openrouter.ai | Yes — AI coaching |
| `STRIPE_SECRET_KEY` | stripe.com dashboard | Yes — payments |
| `STRIPE_WEBHOOK_SECRET` | stripe.com webhooks | Yes — payment events |
| `JWT_SECRET_KEY` | `openssl rand -hex 32` | Yes — auth tokens |
| `PAYG_WEBHOOK_SECRET` | `openssl rand -hex 32` | Yes — webhook verify |

## Human Gate 2: Railway Deploy (15 min)

```bash
railway login
cd services/voice-coach/app/backend
railway link
# Set env vars in Railway dashboard → Settings → Variables
railway up --detach
railway run alembic upgrade head   # Run migrations
railway logs --follow              # Verify startup
```

## Human Gate 3: Smoke Test (5 min)

- [ ] `curl https://<app>.railway.app/api/health` → 200
- [ ] Test signup flow in browser
- [ ] Test recording creation
- [ ] Test transcription (upload audio)
- [ ] Monitor logs for 15 min — no errors

## Post-Deploy

- [ ] Set up Stripe webhook endpoint pointing to Railway URL
- [ ] Configure custom domain (if ready)
- [ ] Enable Sentry error tracking
