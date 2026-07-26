# Leeway

*The second opinion before you leave.*

A trip planner for EVs whose batteries have lost range with age, built
around a real 2021 Tesla Model 3 Standard Range Plus (205 mi at 100%, down
from 263 new). You tell it your car's actual range, it tells you what will
really be left in the battery when you arrive, with charging stops it has
verified against real routing data rather than guessed at. Full product
plan: `../LEEWAY_PRODUCT_PLAN.md` (one level up, not in this repo — this
repo is the codebase only).

**Live:** https://lyhjeremy.github.io/leeway/ · backend health at
`https://leeway-api.onrender.com/api/health`.

## What it does

- Plans charging stops with elevation-aware range math (a proportional
  slice of trip-wide climb data called I-5's Grapevine "reachable" when a
  real per-leg check proved it wasn't — every candidate stop is verified
  with a real routing call before it's accepted).
- Adjusts range for live weather (temperature, headwind) at plan time.
- Flags steep descents and sun glare along the route, with more safety
  flags planned.
- Finds stops by voice or text ("a Starbucks with a drive-through that
  won't add much time") and verifies each candidate's real detour.
- Logs predicted vs. actual arrival per trip, on your device, and shows
  the running record on the accuracy page.
- Works as an installable PWA with a screenshot-friendly trip card for
  the drive itself.

## Structure

- `frontend/` — React + Vite + TS, MapLibre GL, deployed to GitHub Pages.
- `backend/` — FastAPI on Render (Docker runtime, free tier) — see
  `backend/README.md` for why it's not on Hugging Face Spaces.
- `.github/workflows/pages.yml` — builds and deploys the frontend on every
  push to `frontend/**`. The backend deploys via Render's own GitHub
  integration (`render.yaml` at the repo root).

Data sources: OpenRouteService (routing, geocoding, elevation), Open Charge
Map (stations), Open-Meteo (weather), Overpass/OSM (POI search), Gemini
(voice query parsing). Coverage is California for now.

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
