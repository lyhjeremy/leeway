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
- Adjusts range for live weather (temperature, headwind), passenger and
  luggage load, and an optional manual temperature. Set a departure time
  and the weather becomes the hourly forecast for that hour (up to 7 days
  out), sun glare computes for when you actually leave, and construction
  closures filter to what will be active on the road.
- Learns your car: logged predicted-vs-actual trips feed a recency-weighted
  correction into future estimates once 3+ substantial trips exist. The
  correction is biased to the safe side — a hungrier-than-predicted car
  gets the full adjustment, a better-than-predicted one only half, never
  below 0.9 — and the plan says out loud when it's active.
- Charging preferences: fewest stops / fastest trip / best amenities,
  Superchargers-only or non-Tesla-only, specific networks (ChargePoint,
  Electrify America, EVgo, Tesla, Blink), a minimum charger speed, and a
  break rhythm ("stop at least every 2h") that plans stops for the driver
  rather than the battery when that binds first.
- Safety flags, each its own switch: unprotected left turns, unsignaled
  crossings of 4+ lane roads, rail crossings, and active Caltrans lane
  closures (all from real data), plus steep descents, twisty sections,
  sun glare, and strong-wind days. A detour budget (5, 10, or 20 minutes - 5 is the default)
  reroutes around point hazards when the cost fits.
- A route editor in the Google style: up to three stops between start and
  destination, searchable, drag-to-reorder in any direction, with route
  alternatives (I-5 vs 101 vs 99) to compare and pick.
- Finds stops by voice or text ("a Starbucks with a drive-through that
  won't add much time") and verifies each candidate's real detour.
- Full-bleed map with floating cards, light and dark themes (dark gets
  its own night basemap), mi/km and °F/°C independently switchable.
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
Map (stations), Open-Meteo (weather), Overpass/OSM (POI search), the US
Census geocoder (exact house numbers Pelias lacks), Caltrans
LCS (lane closures), Gemini (voice query parsing). Coverage is California
for now. Successful provider responses are cached in-process (routes 6h,
stations 30min, weather 15min) to stretch the free-tier quotas — the ORS
daily directions ceiling is the stack's real scarcity, documented at
roughly 150–200 plans/day.

## iOS app

The same frontend ships as a native iOS app: a Capacitor shell (in
`frontend/ios/`, Swift Package Manager, no CocoaPods) that adds what the
website can't do — a local-notification reminder to log how a planned trip
went, the native share sheet for sending a stop to the Tesla app, and
theme-matched status bar / safe areas. The built web bundle is committed,
so building the app needs only Xcode: see `IOS_HANDOFF.md` for the
sign-and-ship steps.

## Local dev

```
cd frontend && npm install && npm run dev
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --port 7861
```

## Tests

The backend test suite (74 and counting) runs fully offline against a faked provider layer
(synthetic routes with configurable speed/elevation/highway mix, canned
stations, weather, OSM and Caltrans data) — they cover the range math,
every safety-flag detector, the planner end to end including its honest
failure notes, and the provider caches. The frontend's calibration math
has its own vitest suite. CI runs all of it plus lint and build on every
push.

```
cd backend && python -m pytest tests
cd frontend && npm test
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
