---
title: Leeway API
emoji: 🔋
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Leeway API

FastAPI service deployed to a Hugging Face Space (Docker SDK, free CPU tier).
The YAML block above is the Space config Hugging Face reads — keep it at the top.

## Deploy (one-time setup)

1. Create a free account at huggingface.co, then **New Space** → name
   `leeway-api` → SDK **Docker** → hardware **CPU basic (free)**.
2. Add repo secret `HF_TOKEN` (write access) and repo variable `HF_SPACE`
   (e.g. `lyhjeremy/leeway-api`) on the GitHub repo, so CI can push to it.
3. Push this `backend/` directory. CI does this automatically on every push
   to `backend/**` (`.github/workflows/deploy-space.yml`).

Local dev: `pip install -r requirements.txt && uvicorn app.main:app --reload --port 7860`
Health check: `GET /api/health` — returns `{"status": "ok", "version": "..."}`;
the version string is bumped by hand on real changes so a stale Space build
is visible immediately rather than assumed fixed.

Stage 0 scope only: no routing engine, no data sources yet — this endpoint
exists purely to prove the frontend and backend are connected end to end.
