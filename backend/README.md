# Leeway API

FastAPI service, deployed to Render's free web-service tier (Docker runtime).

**Not on Hugging Face Spaces** — as of 2026-07-25, HF requires a PRO
subscription for *new* Docker/Gradio Spaces even on the free CPU tier
(`Static Spaces are free for everyone, but hosting Gradio and Docker Spaces
on free cpu-basic requires a PRO subscription` — confirmed via a real
`create_repo` 402 response, not a stale assumption). Existing Spaces created
before this policy (e.g. SkillCompass's) are presumably grandfathered in, but
this project needed a fresh Space, so it moved to Render instead — the one
platform found with a genuine no-credit-card free Docker tier as of mid-2026.

## Deploy (one-time setup, done via Render's dashboard — needs GitHub OAuth)

1. Sign up free at render.com (no credit card).
2. **New** → **Blueprint** → connect the `lyhjeremy/leeway` GitHub repo →
   Render reads `render.yaml` at the repo root and creates the `leeway-api`
   web service automatically (free plan, Docker runtime, `backend/` as root).
3. Done — Render auto-deploys on every push to `main` from then on (no
   custom CI workflow needed; this is Render's own GitHub App webhook).

Tradeoff accepted: the free plan spins the service down after 15 minutes
idle, so the first request after a gap has a real cold-start delay (Render
docs; expect several seconds, not instant). Acceptable here — Leeway's own
usage pattern is ~2-4 real uses a month, so an idle backend is the common
case anyway, per the product plan.

Local dev: `pip install -r requirements.txt && uvicorn app.main:app --reload --port 7861`
Health check: `GET /api/health` — returns `{"status": "ok", "version": "..."}`;
the version string is bumped by hand on real changes so a stale deploy is
visible immediately rather than assumed fixed.

Stage 0 scope only: no routing engine, no data sources yet — this endpoint
exists purely to prove the frontend and backend are connected end to end.
