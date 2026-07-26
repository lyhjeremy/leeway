# Leeway

*The second opinion before you leave.*

A trip planner for EVs whose batteries have lost range with age — built
around a real 2021 Tesla Model 3 Standard Range Plus (205 mi at 100%, down
from 263 new). Custom range input, safety-aware routing, and voice-driven
stop search. Full product plan: `../LEEWAY_PRODUCT_PLAN.md` (one level up,
not in this repo — this repo is the codebase only).

**Live:** frontend at https://lyhjeremy.github.io/leeway/ · backend health at
`https://leeway-api.hf.space/api/health` (Space name TBD once created).

## Status

**Stage 0 (infrastructure) — in progress.** Frontend renders a hardcoded
LA → SF route on a real map; backend is a bare health-check endpoint. No
real routing, range math, or feature logic yet — that's Stage 1.

## Structure

- `frontend/` — React + Vite + TS, MapLibre GL, deployed to GitHub Pages.
- `backend/` — FastAPI, deployed to a Hugging Face Space (Docker, free CPU).
- `.github/workflows/` — CI: `pages.yml` builds+deploys the frontend on every
  push to `frontend/**`; `deploy-space.yml` pushes `backend/` to the Space.

## Local dev

```
cd frontend && npm install && npm run dev
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --port 7861
```

## A real bug found during Stage 0

`maplibre-gl@6.0.0` (released 2026-07-25, the same day this was built) failed
to load any vector tiles — the base style's raster hillshade layer rendered
fine, but the vector `openmaptiles` source never issued a single tile
request, with no error surfaced anywhere (not `map.on('error')`, not a
failed network request). Pinned to `5.9.0` and the map rendered correctly on
the first retry. Worth re-testing a `6.x` upgrade later once it's had time to
mature, but don't assume a `maplibre-gl` major bump is safe without visually
checking that tiles actually render — a silent, error-free failure like this
is exactly the kind of regression automated checks (build success, no
console errors) won't catch.
