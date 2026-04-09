# Startup Simulator Deploy Runbook

**Status:** Ready for Cloudflare Pages deploy
**Est. Time:** 15 minutes
**Stack:** React 19 + Vite + TypeScript (static SPA — no backend needed)

## Pre-Deploy (verified)

- [x] Code present: `services/startup-simulator/frontend/package.json`
- [x] SPA fallback: `frontend/public/_redirects` → `/*    /index.html   200` (copied to `dist/`)
- [x] Install deps: `cd services/startup-simulator/frontend && npm install`
- [x] Run tests: `npm test` (981 tests)
- [x] Build: `npm run build` → outputs to `dist/`

## Deploy to Cloudflare Pages (5 min)

### Option A: Wrangler CLI
```bash
cd services/startup-simulator/frontend
npx wrangler pages deploy dist --project-name startup-simulator
```

### Option B: Cloudflare Dashboard
1. Go to Cloudflare Dashboard → Pages → Create a project
2. Connect GitHub repo or direct upload
3. Build command: `npm run build`
4. Output directory: `dist`
5. Node version: 20

## Post-Deploy

- [ ] Verify site loads at the Pages URL
- [ ] Test core gameplay flow
- [ ] Configure custom domain (codeswiftr.com/startup-simulator or similar)
- [ ] Set up analytics (PostHog or Cloudflare Web Analytics)

## Notes

- Pure static SPA — no backend, no API keys, no database
- Uses IndexedDB via Dexie for local persistence
- All game logic runs client-side
- Zero ongoing infrastructure cost on Cloudflare Pages free tier
- `tsconfig.app.json` excludes test files from `tsc -b` so production build type-checks only app code; tests remain in `npm test`.
