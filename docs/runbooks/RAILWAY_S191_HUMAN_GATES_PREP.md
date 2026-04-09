# Railway prep — S191 human gates (P1)

**Audience:** Human operator (typically via **nova** — see `forge-shared/modules/human-gates.md`)  
**Source of truth:** `docs/PLAN.md` → Human Gates table (April 2026 P1)  
**Time budget:** ~25 min combined (~10 min Mirrably + ~15 min Voice Coach), plus first-time Railway account setup

This page is the **single prep sheet** for the **two Railway rows** blocking revenue. Deep dives stay in the linked service docs.

---

## What you are unblocking

| Gate (PLAN) | Unblocks | Canonical deep doc |
|-------------|----------|-------------------|
| **Railway: Mirrably CORS restart** | Demo + outreach (browser calls to API succeed) | [`docs/mirrably/RAILWAY_DEPLOY.md`](../mirrably/RAILWAY_DEPLOY.md) |
| **Railway: Voice Coach deploy** | Live VC API + downstream smoke | [`services/voice-coach/DEPLOY_CHECKLIST.md`](../../services/voice-coach/DEPLOY_CHECKLIST.md) · [`services/voice-coach/DEPLOY_NOW.md`](../../services/voice-coach/DEPLOY_NOW.md) |

---

## Shared prep (do once per session)

1. **Railway CLI**
   ```bash
   npm i -g @railway/cli
   railway login
   ```
2. **Routing:** Council S194 routes human gates through **nova** (`forge-shared/modules/human-gates.md`). Use the same Railway account/project links the fleet already uses; avoid spinning duplicate projects.
3. **Git (multi-agent nodes):** `export GIT_OPTIONAL_LOCKS=0` before any `git` use; writes via `bash bin/gitsafe.sh` (Council S190).

---

## Gate A — Mirrably (~10 min): CORS + redeploy

**Production API** for Mirrably is **`apps/mirrably-api`** on Railway (see `docs/PROMPT-nova.md` live URL pattern). There is a separate **`apps/forge-tryon-api`** tree — confirm the Railway service **root directory** matches the service you intend to ship (usually `apps/mirrably-api`).

### Checklist

1. Open Railway → **mirrably-api** (or equivalent) service → **Variables**.
2. Set / verify **`CORS_ORIGINS`** — comma-separated **https** origins, **no spaces**, covering at least:
   - Public demo / marketing / admin frontends you use (e.g. `mirrably-demo.pages.dev`, `admin.mirrably.com`, `mirrably.com` as applicable).
   - See [`apps/mirrably-api/app/config.py`](../../apps/mirrably-api/app/config.py) and [`.env.production.example`](../../apps/mirrably-api/.env.production.example).
3. If **code** on `main` already contains CORS default fixes, you still need a **redeploy** so Railway picks them up:
   - **Deployments → Redeploy** latest successful build, **or** push an empty commit to trigger build.
4. **Verify**
   ```bash
   curl -sS "https://<your-railway-host>/v1/health"
   ```
   From the browser, load the demo and confirm network tab shows **no CORS** failures on API calls.

**Full procedure:** [`docs/mirrably/RAILWAY_DEPLOY.md`](../mirrably/RAILWAY_DEPLOY.md) (Postgres, `DATABASE_URL` reference, public URL, admin key).

---

## Gate B — Voice Coach (~15 min): `railway up`

**Monorepo path:** `services/voice-coach/app/backend` (not legacy `brandfocus-ai/...` paths in older snippets).

### Checklist

1. ```bash
   cd services/voice-coach/app/backend
   railway link    # select or create Voice Coach project
   ```
2. Add **PostgreSQL** (and optional Redis) per [`DEPLOY_CHECKLIST.md`](../../services/voice-coach/DEPLOY_CHECKLIST.md).
3. **Variables:** copy from [`.env.production.example`](../../services/voice-coach/app/backend/.env.production.example) (or template referenced there). Minimum set includes **`SECRET_KEY`**, **`DATABASE_URL`** (from Railway), **`STRIPE_*`** / webhooks as needed, **`ALLOWED_ORIGINS`** (JSON array string per checklist, e.g. `brandfocus.ai` surfaces).
4. Deploy:
   ```bash
   railway up
   ```
   Or use [`app/backend/scripts/deploy.sh`](../../services/voice-coach/app/backend/scripts/deploy.sh) if configured for this repo layout.
5. **Migrations:** `railway run alembic upgrade head` (from linked service context).
6. **Verify**
   ```bash
   bash bin/smoke-test-vc.sh
   ```
   (from repo root) or `curl` health endpoints documented in `DEPLOY_NOW.md`.

**Extended keys / frontend:** [`services/voice-coach/DEPLOY_NOW.md`](../../services/voice-coach/DEPLOY_NOW.md).

---

## Done = both true

| Check | Mirrably | Voice Coach |
|-------|----------|-------------|
| Public URL responds | `/v1/health` OK | `/api/health` (or service health path) OK |
| Browser / demo | No CORS errors on API | N/A (often CF Pages later) |
| DB | Postgres linked | Migrations applied |

---

## If something fails

- **Build fails:** Check Railway build logs; monorepo **root directory** must match `apps/mirrably-api` or `services/voice-coach/app/backend` respectively.
- **Boot loop / crash:** Missing env var — compare to `.env.production.example` in that service.
- **Still CORS:** Typo in `CORS_ORIGINS` / wrong scheme (http vs https) / trailing slash mismatch — align with FastAPI middleware expectations in `app/main.py`.

---

**Last updated:** 2026-04-05 (S191 prep doc, TASK-SHARP-STAR-178)
