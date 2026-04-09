# Railway Redis — monitoring runbook

**Tasks:** TASK-SAFE-BLAZE-256, TASK-SMART-FLASH-417  
**Audience:** Operators  
**Last updated:** 2026-04-04  

---

## Scope

This runbook covers **Railway-hosted Redis** used by FORGE apps. The best-documented consumer in-repo is **Voice Coach** (`services/voice-coach`): FastAPI web + Celery worker share **`REDIS_URL`** for cache, rate limiting, and Celery broker/backend.

**Other stacks:** Mirrably API (`apps/mirrably-api`) does **not** use Redis today. If you add Redis to another Railway project, reuse the same dashboard checks and variable-reference pattern below.

**Related:** [`services/voice-coach/app/backend/railway.toml`](../../services/voice-coach/app/backend/railway.toml) · [`services/voice-coach/docs/ENV_REFERENCE.md`](../../services/voice-coach/docs/ENV_REFERENCE.md) · [`docs/runbooks/REVENUE_UNBLOCK_CHECKLIST.md`](REVENUE_UNBLOCK_CHECKLIST.md) (Redis bullet)

---

## 1. What to watch (Railway dashboard)

1. Open [Railway](https://railway.app) → the **project** that contains Redis (e.g. Voice Coach).
2. Select the **Redis** service (or “Redis” plugin).
3. **Metrics** (wording may vary):
   - **Memory** — sustained growth near the plan limit → risk of OOM / evictions.
   - **CPU** — unusual spikes with stuck workers → investigate app or runaway clients.
   - **Connections** — sudden drops can mean clients failing; sustained max can mean pool leaks.
4. **Deployments / status** — service should be **Active**; accidental pause or failed deploy blocks dependents.
5. **Logs** (Redis service) — connection errors, restarts, OOM messages.

Configure **project notifications** (email/Slack) in Railway so deploy failures and usage thresholds surface without manual polling.

---

## 2. Wiring checklist (dependent services)

For Voice Coach, each of **web** and **worker** must reference the same Redis:

| Variable | Typical Railway pattern |
|----------|-------------------------|
| `REDIS_URL` | Variable reference → `${{Redis.REDIS_URL}}` (name matches your Redis service) |
| `CELERY_BROKER_URL` | Same as `REDIS_URL` |
| `CELERY_RESULT_BACKEND` | Same as `REDIS_URL` |

After changing references, **redeploy** web and worker so processes pick up env.

Source: comments in [`railway.toml`](../../services/voice-coach/app/backend/railway.toml).

---

## 3. Verification

| Check | How |
|--------|-----|
| App accepted Redis | Deploy logs / runtime logs for **Voice Coach web**: look for `Redis connection established` (see [`redis_client.py`](../../services/voice-coach/app/backend/app/infrastructure/cache/redis_client.py)). On failure the app logs a **warning** and may use **in-memory fallback** — API can look “fine” while cache/rate limits are degraded. |
| Celery broker | **Worker** service logs should show the worker starting without broker connection errors. |
| HTTP health | `GET /api/health` stays 200 even when Redis is down (basic check does not ping Redis). **`/api/health/detailed` does not currently include Redis** — do not rely on it for Redis alone. |

Optional: from a trusted admin context, exercise a path that uses cache or rate limits and confirm expected behavior.

---

## 4. Failure modes and response

| Symptom | Likely cause | Actions |
|---------|--------------|---------|
| Celery tasks never run | Redis down or wrong `CELERY_*` URL | Fix Redis service; fix env references; restart **worker** |
| Elevated API latency / DB load | Redis unavailable → fallback paths | Restore Redis; scale or restart web if needed |
| OOM / evictions on Redis | Memory pressure, huge queues | Inspect key usage; increase plan or trim Celery result expiry; fix runaway publishers |
| Auth / session oddities | If JWT blacklist or session store uses Redis | Restore connectivity; validate no split-brain URLs between services |

**Recovery order:** stabilize **Redis** → redeploy or restart **worker** → restart **web** if connections were wedged.

---

## 5. Security notes

- Treat **`REDIS_URL`** as a secret (includes password on Railway).
- Do not paste live URLs into tickets or public docs.
- Align with [`docs/runbooks/SECURITY_RUNBOOK.md`](SECURITY_RUNBOOK.md) (Redis AUTH / network exposure).

---

## 6. Gaps / improvements (optional backlog)

- Add **Redis** to Voice Coach **`/api/health/detailed`** so uptime monitors can alert on cache/broker health without parsing logs.
- Add a **synthetic check** (cron + `redis-cli PING` or small script) if Railway metrics are insufficient.
