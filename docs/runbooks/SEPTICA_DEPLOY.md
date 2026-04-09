# Septica Deployment Runbook

**Agent:** glm | **Date:** 2026-03-31 | **Type:** research
**Source:** `games/card-games-elixir/` + `apps/septica-landing/`

---

## 1. Pre-Deploy Checklist

### Stack Summary

| Component | Details |
|-----------|---------|
| Language | Elixir 1.17+ / Erlang/OTP 27 |
| Framework | Phoenix 1.7+ with Bandit adapter |
| Database | PostgreSQL via Ecto (postgrex) |
| Auth | Guardian JWT + bcrypt_elixir |
| Payments | stripity_stripe 3.2 |
| Rate Limiting | Hammer (ETS backend) |
| Real-time | Phoenix Channels (WebSocket) |
| CORS | Corsica |
| Release | Mix release (Dockerfile multi-stage, Alpine) |

### Key Dependencies (from mix.exs)

- `phoenix ~> 1.7`
- `bandit ~> 1.5` (HTTP adapter, replaces Cowboy)
- `ecto_sql ~> 3.12` + `postgrex ~> 0.19`
- `guardian ~> 2.3` (JWT auth)
- `stripity_stripe ~> 3.2` (Stripe SDK)
- `hammer ~> 6.1` (rate limiting, ETS backend)
- `corsica ~> 2.1` (CORS)
- `bcrypt_elixir ~> 3.2` (password hashing)

### Deployment Files

- **Dockerfile:** `games/card-games-elixir/Dockerfile` (multi-stage Alpine build, runs migrations on startup)
- **fly.toml:** Does NOT exist
- **rel/ directory:** Does NOT exist (uses `mix release` inside Dockerfile)
- **Landing page:** `apps/septica-landing/` (static HTML: `index.html`, `play.html`, `robots.txt`, `sitemap.xml`)

### Database Migrations (4 files)

| Migration | Purpose |
|-----------|---------|
| `20260327000001_create_users.exs` | Users table |
| `20260327000002_create_game_records.exs` | Game records |
| `20260327000003_create_leaderboard_entries.exs` | Leaderboard |
| `20260328000001_add_premium_to_users.exs` | Premium columns |

### External Service Dependencies

| Service | Purpose | Required? |
|---------|---------|-----------|
| PostgreSQL | Primary database | **YES** |
| Stripe | Payments & subscriptions | **YES** (for monetization) |
| Redis | **NOT used** — Hammer uses ETS | No |

---

## 2. Environment Variables

### Required (app crashes without these)

| Variable | Example | Source |
|----------|---------|--------|
| `DATABASE_URL` | `ecto://user:pass@host:5432/card_games_prod` | `runtime.exs:14` |
| `SECRET_KEY_BASE` | 64+ byte random string (`mix phx.gen.secret`) | `runtime.exs:25` |
| `STRIPE_SECRET_KEY` | `sk_live_...` or `sk_test_...` | `runtime.exs:3` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` | `runtime.exs:6` |
| `STRIPE_PRICE_AD_FREE` | `price_1R...` (Stripe Price ID) | `runtime.exs:7` |
| `STRIPE_PRICE_PREMIUM` | `price_1R...` (Stripe Price ID) | `runtime.exs:8` |
| `STRIPE_SUCCESS_URL` | `https://septica.codeswiftr.com/premium/success?session_id={CHECKOUT_SESSION_ID}` | `runtime.exs:9` |
| `STRIPE_CANCEL_URL` | `https://septica.codeswiftr.com/premium/cancel` | `runtime.exs:10` |

### Optional (have defaults)

| Variable | Default | Source |
|----------|---------|--------|
| `PORT` | `4000` | `runtime.exs:32` |
| `POOL_SIZE` | `10` | `runtime.exs:22` |
| `PHX_HOST` | `localhost` | `runtime.exs:31` |
| `GUARDIAN_SECRET_KEY` | Falls back to `SECRET_KEY_BASE` | `runtime.exs:55` |
| `CORS_ORIGINS` | `https://septica.codeswiftr.com,https://codeswiftr.com` | `runtime.exs:47-49` |

---

## 3. Railway Deploy Steps

The Dockerfile is already Railway-ready. It auto-runs migrations on startup via:
```
CMD ["sh", "-c", "bin/card_games eval 'CardGames.Release.migrate()' && bin/card_games start"]
```

### Step-by-Step

**1. Create Railway project**
```bash
railway init
# or via dashboard: railway.new → "Deploy from GitHub repo"
```

**2. Add PostgreSQL service**
```bash
railway add --database postgres
# Note the DATABASE_URL from: railway variables
```

**3. Configure the backend service**

Railway deploys from the repo root, but the Elixir app is in `games/card-games-elixir/`. Set the root directory:
```bash
# In Railway dashboard → Service Settings → Root Directory:
games/card-games-elixir
```

Or via CLI:
```bash
railway variables set ROOT_DIRECTORY=games/card-games-elixir
```

**4. Set all environment variables**
```bash
railway variables set DATABASE_URL="ecto://user:pass@host:5432/card_games_prod"
railway variables set SECRET_KEY_BASE="$(mix phx.gen.secret)"
railway variables set STRIPE_SECRET_KEY="sk_live_..."
railway variables set STRIPE_WEBHOOK_SECRET="whsec_..."
railway variables set STRIPE_PRICE_AD_FREE="price_1R..."
railway variables set STRIPE_PRICE_PREMIUM="price_1R..."
railway variables set STRIPE_SUCCESS_URL="https://septica.codeswiftr.com/premium/success?session_id={CHECKOUT_SESSION_ID}"
railway variables set STRIPE_CANCEL_URL="https://septica.codeswiftr.com/premium/cancel"
railway variables set PHX_HOST="septica.codeswiftr.com"
railway variables set PORT="4000"
railway variables set POOL_SIZE="20"
railway variables set CORS_ORIGINS="https://septica.codeswiftr.com,https://codeswiftr.com"
```

**5. Deploy**
```bash
railway up
# Migrations run automatically in the CMD entrypoint
```

**6. Verify health endpoint**
```bash
curl https://<your-railway-app>.up.railway.app/api/health
# Expected: {"status":"ok","version":"0.1.0","database":"connected"}
```

**7. Generate a custom domain (optional but recommended)**
In Railway dashboard → Settings → Domains → add `api.septica.codeswiftr.com` (then point DNS via Cloudflare CNAME).

---

## 4. Stripe Products Setup

### Products to Create in Stripe Dashboard

The code references two price IDs via `config :card_games, :stripe`:

| Product Name | Price ID Env Var | Mode | Billing | Notes |
|-------------|-----------------|------|---------|-------|
| **Septica Ad-Free** | `STRIPE_PRICE_AD_FREE` | `payment` (one-time) | One-time | 30-day premium, no recurring billing |
| **Septica Premium** | `STRIPE_PRICE_PREMIUM` | `subscription` | Monthly recurring | Auto-renews, managed via webhooks |

### Stripe Setup Steps

1. Go to [Stripe Dashboard → Products](https://dashboard.stripe.com/products)
2. **Create "Septica Ad-Free" product:**
   - Name: `Septica Ad-Free`
   - Description: `Remove ads from Septica for 30 days`
   - Price: Set your price (e.g., $1.99 one-time)
   - Billing: One-time
3. **Create "Septica Premium" product:**
   - Name: `Septica Premium`
   - Description: `Full premium access with ad-free play and exclusive features`
   - Price: Set your price (e.g., $4.99/month)
   - Billing: Recurring monthly
4. Copy the `price_1R...` IDs into the environment variables

### Webhook Configuration

| Setting | Value |
|---------|-------|
| **Endpoint URL** | `https://<backend-url>/api/webhooks/stripe` |
| **Events to listen** | `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted` |

The webhook handler processes:
- `checkout.session.completed` → Upgrades user to premium (30 days)
- `customer.subscription.updated` → Extends or revokes based on subscription status (`active`/`trialing` = extend, `canceled`/`unpaid`/`past_due` = downgrade)
- `customer.subscription.deleted` → Downgrades user from premium

### Checkout Flow

The frontend calls `POST /api/premium/checkout` with `{"plan": "ad_free"}` or `{"plan": "premium"}` (default). The backend creates a Stripe Checkout Session and returns the hosted payment URL.

---

## 5. DNS & Landing Page

### Landing Page

The landing page at `apps/septica-landing/` is **static HTML** (`index.html`, `play.html`, `robots.txt`, `sitemap.xml`). No build step needed.

### Cloudflare Pages Setup

1. **Connect repo** in Cloudflare Pages
2. **Build settings:**
   - Root directory: `apps/septica-landing`
   - Build command: (leave empty — static files only)
   - Output directory: `/` (or `.`)
3. **Custom domain:** `septica.codeswiftr.com`
   - Add CNAME record pointing to the Cloudflare Pages URL
4. The landing page already references `https://septica.codeswiftr.com` in meta tags and canonical URL

### CORS Configuration

The backend already allows these origins (hardcoded in `runtime.exs:39-44`):
- `https://septica.codeswiftr.com`
- `https://codeswiftr.com`

Override with `CORS_ORIGINS` env var if using different domains.

### check_origin

The Phoenix endpoint validates WebSocket origins against:
- `https://septica.codeswiftr.com`
- `https://codeswiftr.com`

---

## 6. Smoke Test Checklist

After deployment, verify each item:

- [ ] **Health endpoint:** `curl https://<api-url>/api/health` → `{"status":"ok","version":"0.1.0","database":"connected"}`
- [ ] **User registration:** `POST /api/auth/register` with `{"username":"test","password":"test123"}` → 200 + JWT token
- [ ] **User login:** `POST /api/auth/login` with credentials → 200 + JWT token
- [ ] **Guest token:** `POST /api/auth/guest` → 200 + JWT token
- [ ] **Stripe checkout flow:** `POST /api/premium/checkout` with `Authorization: Bearer <token>` and `{"plan":"premium"}` → returns Stripe checkout URL
- [ ] **Premium status:** `GET /api/users/<id>/premium` → returns premium status JSON
- [ ] **Webhook endpoint:** Send test webhook from Stripe Dashboard → verify 200 response
- [ ] **WebSocket connection:** Connect to `wss://<api-url>/socket` and join `lobby` channel → confirm connected
- [ ] **ELO matchmaking:** Join lobby, trigger matchmaking → verify queue entry via lobby channel events
- [ ] **Leaderboard:** `GET /api/leaderboard/septica` → returns leaderboard data
- [ ] **Rate limiting:** Send >5 login attempts in 1 minute → verify 429 response
- [ ] **CORS:** From `https://septica.codeswiftr.com`, API requests include correct CORS headers

### Quick curl commands

```bash
# Health
curl -s https://API_URL/api/health | jq .

# Register
curl -s -X POST https://API_URL/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"smoketest","password":"test1234"}' | jq .

# Login
TOKEN=$(curl -s -X POST https://API_URL/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"smoketest","password":"test1234"}' | jq -r '.token')

# Checkout
curl -s -X POST https://API_URL/api/premium/checkout \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"plan":"premium"}' | jq .

# Leaderboard
curl -s https://API_URL/api/leaderboard/septica | jq .
```

---

## 7. Quick Reference: API Endpoints

| Method | Path | Auth? | Rate Limit | Purpose |
|--------|------|-------|------------|---------|
| GET | `/api/health` | No | 100/min | Health check + DB status |
| POST | `/api/auth/register` | No | 3/hour | Create account |
| POST | `/api/auth/login` | No | 5/min | Get JWT token |
| POST | `/api/auth/guest` | No | 5/10min | Guest token |
| GET | `/api/leaderboard/:game_type` | No | 100/min | Leaderboard |
| GET | `/api/users/:id` | No | 100/min | User profile |
| GET | `/api/users/:id/premium` | No | 100/min | Premium status |
| GET | `/api/games/:id` | No | 100/min | Game replay |
| POST | `/api/premium/activate` | Yes | 100/min | Activate premium |
| POST | `/api/premium/checkout` | Yes | 100/min | Stripe checkout |
| POST | `/api/webhooks/stripe` | No | 60/min | Stripe webhook |

---

## 8. Troubleshooting

| Symptom | Check |
|----------|-------|
| App crashes on start | `DATABASE_URL` or `SECRET_KEY_BASE` missing — check Railway logs |
| DB migration fails | Check DATABASE_URL is correct and PG service is running |
| Stripe checkout returns 502 | `STRIPE_SECRET_KEY` invalid or missing |
| Webhook returns 401 | `STRIPE_WEBHOOK_SECRET` doesn't match Stripe Dashboard signing secret |
| CORS errors | Verify `CORS_ORIGINS` includes the frontend domain |
| WebSocket rejected | Verify `PHX_HOST` matches the actual domain; `check_origin` in `runtime.exs:39-44` must include frontend origin |
| Migrations not running | The Dockerfile CMD runs them automatically; check container logs for `CardGames.Release.migrate()` output |
