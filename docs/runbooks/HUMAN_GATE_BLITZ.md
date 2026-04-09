# Human Gate Blitz — S194

**Total time:** ~75 min | **Deadline:** April 6, 2026
**Council P1 (3-0):** All gates must clear by April 6 or products enter kill review.

Do these in order. Each gate is self-contained.

---

## Gate 1: Mirrably CORS Restart (~10 min)

**Unblocks:** Demo page + 10 outreach emails ready to send

1. Open [Railway Dashboard](https://railway.app) → mirrably-api service → **Variables**
2. Set/verify `CORS_ORIGINS`:
   ```
   https://mirrably.com,https://demo.mirrably.com,https://mirrably-demo.pages.dev,https://admin.mirrably.com
   ```
   No spaces, no trailing slashes, all HTTPS.
3. **Deployments → Redeploy** latest successful build
4. Verify:
   ```bash
   curl -sS "https://<railway-host>/v1/health"
   ```
5. Load demo in browser — confirm no CORS errors in Network tab

**Deep doc:** `docs/mirrably/RAILWAY_DEPLOY.md`

---

## Gate 2: Voice Coach Deploy (~15 min)

**Unblocks:** Voice Coach live API

1. Install Railway CLI (if needed): `npm i -g @railway/cli && railway login`
2. Link project:
   ```bash
   cd services/voice-coach/app/backend
   railway link    # select or create Voice Coach project
   ```
3. Add **PostgreSQL** via Railway Dashboard → New → Database → PostgreSQL
4. Set environment variables (minimum):
   ```bash
   railway variables set SECRET_KEY="$(openssl rand -hex 32)"
   railway variables set GROQ_API_KEY="<from groq.com/keys>"
   railway variables set ALLOWED_ORIGINS='["https://brandfocus.ai","https://voicecoach.brandfocus.ai"]'
   ```
5. Deploy: `railway up`
6. Run migrations: `railway run alembic upgrade head`
7. Verify:
   ```bash
   bash bin/smoke-test-vc.sh
   # or: curl -sS "https://<railway-host>/api/health"
   ```

**Deep doc:** `services/voice-coach/DEPLOY_CHECKLIST.md`

---

## Gate 3: Stripe — IS Team Products (~15 min)

**Unblocks:** Interview Simulator paid tier

1. Go to [Stripe Dashboard](https://dashboard.stripe.com) → Products
2. Create product: **Interview Simulator Pro**
   - Price: $9.99/month (or per S194 pricing research)
   - Recurring, monthly billing
3. Copy the **Price ID** (`price_xxx`)
4. Set in the IS backend env (Railway or `.env.production`):
   ```
   STRIPE_PRICE_ID=price_xxx
   STRIPE_SECRET_KEY=sk_live_xxx
   STRIPE_WEBHOOK_SECRET=whsec_xxx
   ```
5. Set up webhook in Stripe → Developers → Webhooks:
   - Endpoint: `https://<is-api-host>/api/webhooks/stripe`
   - Events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`

---

## Gate 4: Cloudflare Email Catch-All (~5 min)

**Unblocks:** Prospect email replies to outreach

1. Open [Cloudflare Dashboard](https://dash.cloudflare.com) → mirrably.com → **Email Routing**
2. Enable email routing if not already active
3. Add **catch-all rule**: Route `*@mirrably.com` → your inbox
4. Verify: Send a test email to `test@mirrably.com` — confirm delivery

---

## Gate 5: App Store Connect — Kids Category (~30 min)

**Unblocks:** DynaStory TestFlight distribution

1. Open [App Store Connect](https://appstoreconnect.apple.com) → Apps → DynaStory
2. Go to **App Information** → **Category**
3. Set Primary Category: **Kids** (or Education > Kids subcategory)
4. Complete **Kids category questionnaire**:
   - Age range: 5-8 or 6-8 (match app content)
   - No third-party analytics (required for Kids)
   - No advertising
   - COPPA compliance: Yes
5. If not yet created, create the app:
   - Bundle ID: match Xcode project
   - SKU: `dynastory-001` (or similar)
   - Primary language: English
6. Upload TestFlight build from Xcode or CI
7. Add internal testers + submit for TestFlight review

**Deep doc:** Check `apps/dyna-story/` for Xcode project details

---

## After All Gates Clear

1. **Send Mirrably outreach** — 10 personalized emails in `docs/outreach/personalized-batch-1.md`
2. **Post LinkedIn content** — 15 posts ready in `docs/outreach/linkedin-posts-week1.md`
3. **Verify GSC** — Google Search Console for codeswiftr.com
4. Run `forge status` to confirm all services healthy
5. Update `docs/PLAN.md` — mark gates as DONE

---

*Created: 2026-04-05 (S194 — consolidated from RAILWAY_S191_HUMAN_GATES_PREP.md + REVENUE_UNBLOCK_CHECKLIST.md)*
