# bin/ — Shell Script Inventory

> Quick reference for all scripts. Run from repo root (`./bin/<script>`).

---

## Session Management

### `session-preflight.sh` — REMOVED (S188)
**Replaced by:** `forge preflight` (Go CLI). Use that instead.

**Prerequisites:**
- `forge` CLI on PATH
- Daemon reachable at `localhost:8081` or `prya:8081`
- `tmux` session named `forge` running

**Status:** Active

---

### `gitsafe.sh`
Git wrapper that avoids `.git/index.lock` contention on multi-agent nodes (nova, sati). Copies the index to a temp file, runs the git command against it, then copies back.

**Usage:**
```bash
gitsafe add file.py
gitsafe commit -m "message"
gitsafe status   # works fine too, but regular git is OK for read-only
```

**When to use:** Use for write commands (`add`, `commit`, `reset`, `checkout`) on shared nodes. Regular `git` is fine for read-only commands (`status`, `diff`, `log`).

**Prerequisites:**
- Must be run from inside the git repository

**Status:** Active — Council S163 (unanimous approval)

---

## Deployment

### `deploy-smoke-test.sh`
Post-deploy HTTP smoke tests for IS and VC backends. Checks health endpoints, auth protection, and registration flow.

**Usage:**
```bash
./bin/deploy-smoke-test.sh is          # Interview Simulator only
./bin/deploy-smoke-test.sh vc          # Voice Coach only
./bin/deploy-smoke-test.sh all         # Both products
./bin/deploy-smoke-test.sh is --base-url http://localhost:8000  # localhost override
```

**When to use:** After every Railway deploy. Verify all checks pass before declaring deploy complete.

**Prerequisites:**
- `curl` with network access to deployed endpoints
- Backend must be reachable (connection refused = not deployed yet, expected before first deploy)

**Status:** Active

---

### `mirrably-health-check.sh`
Post-deploy HTTP checks for **Mirrably** production (API health JSON, core CDN scripts, eyewear widget bundle, marketing site, admin app). Aligns with `docs/mirrably/status-page-uptime-monitoring-spec.md`.

**Usage:**
```bash
./bin/mirrably-health-check.sh
```

**Manual CDN smoke (consolidated hostname):** `curl -sI https://cdn.mirrably.com/forge-tryon.esm.js` — expect `200` and `content-type: application/javascript`.

**Environment overrides:** The script **does not** read `MIRABLY_API_BASE` / `MIRABLY_CDN` yet; edit `SERVICES` in the script for staging URLs, or use `curl` probes from [`docs/mirrably/status-page-uptime-monitoring-spec.md`](../docs/mirrably/status-page-uptime-monitoring-spec.md).

**When to use:** After Mirrably API / CDN / Pages deploys; exit code 0 before marking release green.

**Prerequisites:** `curl`, valid TLS trust store (HTTP `000` usually means DNS/TLS/firewall — see script hints).

**Status:** Active

---

### `deploy-verify.sh`
Landing page health checks across all 11 portfolio domains. Verifies HTTP 200, page title, og:title meta tag, description meta tag, and lead capture form presence.

**Usage:**
```bash
./bin/deploy-verify.sh all             # All domains
./bin/deploy-verify.sh codeswiftr.com  # Single domain
```

**When to use:** After Cloudflare Pages or hosting deploys to verify landing pages are live and configured correctly.

**Prerequisites:**
- `curl` with network access to all domain URLs

**Status:** Active

---

### `stripe-setup.sh`
Creates Stripe products and prices for Interview Simulator, Voice Coach, and (deprecated) Study Flow. Idempotent — safely re-runs if products already exist.

**Usage:**
```bash
./bin/stripe-setup.sh sk_live_xxxx          # via argument
STRIPE_SECRET_KEY=sk_live_xxxx ./bin/stripe-setup.sh  # via env var
./bin/stripe-setup.sh --live sk_live_xxxx  # live mode
```

**When to use:** One-time setup before first deploy. Re-run to verify existing products.

**Prerequisites:**
- `stripe` CLI installed (`brew install stripe/stripe-cli/stripe`)
- Stripe API key (test or live)
- Python 3 for JSON parsing

**Status:** Active

---

## Testing

### `smoke-test-is.sh`
Detailed smoke tests for Interview Simulator (codeswiftr.com) backend. Covers health, auth (register + login + me), interview session creation, lead capture, and Stripe checkout endpoint.

**Usage:**
```bash
IS_API_URL=https://api.codeswiftr.com ./bin/smoke-test-is.sh
```

**When to use:** Manual verification after IS backend deploy, before announcing to users.

**Prerequisites:**
- `curl`, `jq`
- Backend at `https://api.codeswiftr.com` (or override via `IS_API_URL` env var)

**Status:** Active

---

### `smoke-test-vc.sh`
Detailed smoke tests for Voice Coach (brandfocus.ai) backend. Covers health endpoints (including /health/live, /health/ready, /health/detailed), blog API, and auth-protected endpoints.

**Usage:**
```bash
VC_API_URL=https://voice-coach-web-production.up.railway.app ./bin/smoke-test-vc.sh
```

**When to use:** Manual verification after VC backend deploy.

**Prerequisites:**
- `curl`, `jq`
- Backend URL (defaults to Railway production URL)

**Status:** Active

---

### `smoke-test-worker.sh`
Smoke tests for the Lead Capture Cloudflare Worker. Tests worker health, cross-domain waitlist capture (codeswiftr.com, brandfocus.ai, thebrightharbor.com), lead magnet delivery, and duplicate detection.

**Usage:**
```bash
./bin/smoke-test-worker.sh
```

**When to use:** After deploying or updating the Cloudflare Worker that handles lead capture.

**Prerequisites:**
- `curl` with access to `api.codeswiftr.com`, `api.brandfocus.ai`, `api.thebrightharbor.com`
- Cloudflare Worker deployed and reachable

**Status:** Active

---

### `ios-green-loop.sh`
Loops iOS build + test cycle until all tests pass. Shuts down stale simulators, boots fresh simulator, builds for testing, runs tests serially (to avoid clone crashes), retries up to 10 times.

**Usage:**
```bash
./bin/ios-green-loop.sh                        # defaults: ios/calm-connect-ios, Scheduler
./bin/ios-green-loop.sh ios/calm-connect-ios Scheduler
```

**When to use:** After making iOS code changes to verify tests go green before committing. Particularly useful when tests are flaky due to simulator state.

**Prerequisites:**
- `xcbeautify` on PATH (`brew install xcbeautify`)
- Xcode project with the named scheme
- iPhone 17 Pro simulator available
- `xcodebuild`, `xcrun` available

**Status:** Active

---

## Infrastructure

### `trinity-dashboard.sh` — REMOVED (S188)
**Replaced by:** `forge dashboard` (Go CLI). Use that instead.

---

### `daemon-watchdog.sh`
Monitors the forged daemon every 30 seconds. If the health endpoint fails, waits a 5-second grace period, then restarts via `forge daemon start`. Designed to run in the `forge-monitor` tmux session.

**Usage:**
```bash
# Run in forge-monitor tmux session:
bash bin/daemon-watchdog.sh
```

**When to use:** Always, when the daemon should be kept alive (e.g., production nodes). Start in the `forge-monitor` tmux session as a background process.

**Prerequisites:**
- `forge` CLI on PATH
- Daemon restart permissions
- Should run inside `tmux` to survive terminal closes

**Status:** Active

---

### `setup-worktree.sh`
Creates (or attaches to) a git worktree for parallel agent work. Worktrees live at `../forge-mono-{branch-slug}`.

**Usage:**
```bash
./bin/setup-worktree.sh feat/my-feature                    # Full repo worktree
./bin/setup-worktree.sh feat/my-feature services/allergen-coach  # With scope hint
```

**When to use:** When spawning a worktree agent for parallel implementation work.

**Prerequisites:**
- Git with worktree support
- Parent directory must exist one level up from repo root

**Status:** Active

---

### `sparse-checkout-profiles.sh`
Configures git sparse checkout to reduce working tree size on 16GB nodes. Four profiles: `prya` (infra only), `vega` (brandfocus-ai), `gaea` (thebrightharbor + adguild), `full` (disable sparse checkout).

**Usage:**
```bash
./bin/sparse-checkout-profiles.sh prya    # Hub node — infra only
./bin/sparse-checkout-profiles.sh vega   # brandfocus-ai domain
./bin/sparse-checkout-profiles.sh gaea   # thebrightharbor + adguild
./bin/sparse-checkout-profiles.sh full   # Disable — full checkout
```

**When to use:** On node setup or migration. When disk space is constrained.

**Prerequisites:**
- Git 2.25+ for `git sparse-checkout` subcommand
- Must be run from repo root

**Status:** Active

---

## Reference

### `post-deploy-checklist.md`
Not a script — a markdown checklist for manual steps after Railway backend deploys. Covers automated smoke tests, manual verification (signup, login, email), Stripe test payment flow, SEO sitemap submission, and go-to-market tasks.

**Usage:**
```bash
cat bin/post-deploy-checklist.md
# or open in editor
```

**When to use:** After every backend deploy. Step through the checklist before announcing a deploy.

**Status:** Active (reference document)

---

## Deprecated / Removed

### `.forge/scripts/context-monitor.sh`
~~Cron health check for FORGE daemon.~~

**Status:** **Removed (S172)** — Superseded by `forge status` and daemon patrol system. Deleted from `.forge/scripts/` as part of script consolidation.

---

### `deploy-study-flow.sh`
~~Post-deploy smoke test for Study Flow backend.~~

**Status:** **Removed** — Study Flow was killed in Council S159 (2026-03-24). No longer in the repo.

---

### `smoke-test-sf.sh`
~~Detailed smoke tests for Study Flow backend.~~

**Status:** **Removed** — Study Flow was killed in Council S159 (2026-03-24). No longer in the repo.

---

### `setup-stripe-products.sh`
~~Duplicate of `stripe-setup.sh`.~~

**Status:** **Removed** — superseded by `bin/stripe-setup.sh`. If you have this file locally, use `stripe-setup.sh` instead.
