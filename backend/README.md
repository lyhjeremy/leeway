# Leeway API

FastAPI service, deployed to Render's free web-service tier (Docker runtime).

**Not on Hugging Face Spaces.** As of 2026-07-25, HF requires a PRO
subscription for *new* Docker/Gradio Spaces even on the free CPU tier
(`Static Spaces are free for everyone, but hosting Gradio and Docker Spaces
on free cpu-basic requires a PRO subscription`, confirmed via a real
`create_repo` 402 response, not a stale assumption). Existing Spaces created
before this policy (e.g. SkillCompass's) are presumably grandfathered in, but
this project needed a fresh Space, so it moved to Render instead. The one
platform found with a genuine no-credit-card free Docker tier as of mid-2026.

## Deploy (one-time setup, done via Render's dashboard: needs GitHub OAuth)

1. Sign up free at render.com (no credit card).
2. **New** → **Blueprint** → connect the `lyhjeremy/leeway` GitHub repo →
   Render reads `render.yaml` at the repo root and creates the `leeway-api`
   web service automatically (free plan, Docker runtime, `backend/` as root).
3. Done, Render auto-deploys on every push to `main` from then on (no
   custom CI workflow needed; this is Render's own GitHub App webhook).

Tradeoff accepted: the free plan spins the service down after 15 minutes
idle, so the first request after a gap has a real cold-start delay (Render
docs; expect several seconds, not instant). Acceptable here, Leeway's own
usage pattern is ~2-4 real uses a month, so an idle backend is the common
case anyway, per the product plan.

Local dev: `pip install -r requirements.txt && uvicorn app.main:app --reload --port 7861`
Health check: `GET /api/health`: returns `{"status": "ok", "version": "..."}`;
the version string is bumped by hand on real changes so a stale deploy is
visible immediately rather than assumed fixed.

## Stage 1: real routing

`/api/geocode` and `/api/plan` proxy to OpenRouteService (routing, elevation,
geocoding, free tier: 2,500 req/day) and Open Charge Map (charging
stations, no key needed under 250 results/call). Set `ORS_API_KEY` as a
Render environment variable to enable them, without it, both return a
clear 503 rather than failing silently. `/api/plan` loops to insert *multiple*
charging stops when a trip genuinely needs more than one (a short-range car
on a long trip often does), see `app/planner.py`'s docstring and
`app/range_model.py` for the honest, documented range-estimate assumptions
(none of it pretends to be a physics simulator).

Live-tested against the real ORS API since. Real plans that have run end to
end in production include Los Angeles → Santa Barbara, Chicago → Detroit,
New York → Boston and Denver → Salt Lake City, the last of which is what
surfaced the sparse-region planner bugs documented in the root README.

**The 40-calls-per-minute limit is the binding constraint, not the daily
2,000.** Its backoff outlasts Render's ~100s proxy timeout, so a plan that
exceeds it spends quota and still returns "planning took too long" to the
driver. `MAX_VERIFY_CALLS_PER_PLAN` in `app/planner.py` bounds one plan's
spend for that reason. Trips needing nine or ten charging stops cannot be
verified inside the minute limit at all and will end with the rate-limit
note; that is arithmetic, and self-hosted routing is what removes it.

<!-- deploy nudge: 0.16.5 -->
