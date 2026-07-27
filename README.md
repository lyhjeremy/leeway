# Leeway

*The second opinion before you leave.*

Every EV planner finds you a charger. This one will also spend a minute of
your day driving around the block so you don't have to cross four lanes of
traffic without a light.

Leeway plans road trips across the mainland US around the battery your car
has today,
then spends a detour budget you set, in minutes, on hazards other planners
route you straight through: unprotected left turns, unsignaled
crossings of 4+ lane roads, rail level crossings, and live Caltrans
closures. Afterwards it asks how the trip went, and keeps the record of how
wrong it was.

Built around a real 2021 Tesla Model 3 Standard Range Plus: 205 mi at 100%,
down from 263 new. Full product plan: `../LEEWAY_PRODUCT_PLAN.md` (one level
up, not in this repo — this repo is the codebase only).

### The unusual parts

- **Hazard-aware rerouting.** Four detectors, each with its own switch:
  unprotected left, unsignaled 4+ lane crossing, rail crossing, Caltrans
  closure (that last one California only). Behind them sits a detour budget
  of 5, 10, or 20 minutes.
  Anything avoidable inside the budget gets routed around, and the rest is
  flagged. Signals, lane counts and crossings come from OSM geometry;
  closures come from the Caltrans feed.
- **Degradation as the starting point.** The plan begins from the range
  your car shows at 100% today, not the factory number.
- **A self-grading record.** Log the arrival percentage you saw. Leeway
  keeps predicted against actual with no favorable rounding, and
  after three substantial trips starts correcting toward your car, biased
  so an optimistic correction can never make a plan bolder than stock.
- **Plain-language stops.** "Add an In-N-Out after an hour of driving."
  Each candidate is priced by a routed detour rather than a straight line.

**Live:** https://lyhjeremy.github.io/leeway/ ·
**Product overview:** https://lyhjeremy.github.io/leeway/overview/ ·
backend health at `https://leeway-api.onrender.com/api/health`.

## What it does

- Reroutes around point hazards, each detector its own switch: unprotected
  left turns, unsignaled crossings of 4+ lane roads, rail crossings, and
  active Caltrans lane closures. A detour budget (5, 10, or 20 minutes; 5 is
  the default) decides what gets avoided and what only gets flagged. It also
  warns without rerouting on steep descents, twisty sections, sun glare, and
  strong wind.
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
  correction is biased to the safe side. A hungrier-than-predicted car gets
  the full adjustment, a better-than-predicted one only half, never below
  0.9, and the plan says out loud when it's active.
- Charging preferences: fewest stops / fastest trip / best amenities,
  Superchargers-only or non-Tesla-only, specific networks (ChargePoint,
  Electrify America, EVgo, Tesla, Blink), a minimum charger speed, and a
  break rhythm ("stop at least every 2h") that plans stops for the driver
  rather than the battery when that binds first.
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
LCS (lane closures, California only), Gemini (voice query parsing).
Coverage is the mainland US. Successful provider responses are cached in-process (routes 6h,
stations 30min, weather 15min) to stretch the free-tier quotas — the ORS
daily directions ceiling is the stack's real scarcity, documented at
roughly 150–200 plans/day.

The public Overpass servers are the other scarcity, and they fail as
latency rather than as an error. Measured against production, the three
hazard queries alone turned a 2-mile plan into a 99-second one, because a
mirror that accepts connections and then hangs costs a full timeout on
every retry rotation. Timeouts are 5s connect and 20s read, so a dead
mirror is cheap. The planner gives each check 15 seconds before dropping
it to no flags, and a circuit breaker stops asking for five minutes after
three consecutive failures. The breaker matters more than it looks: a check
that times out caches nothing, so without a breaker every later plan re-pays the
full cost of discovering the same outage. Safety flags are the one part
of a plan allowed to go missing, because a plan that never returns is
worse than a plan without warnings.

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

The backend test suite (85 and counting) runs fully offline against a faked
provider layer (synthetic routes with configurable speed/elevation/highway
mix, canned stations, weather, OSM and Caltrans data) — they cover the range
math,
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
