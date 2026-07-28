# Leeway frontend

React + TypeScript + Vite, MapLibre GL for the map, deployed to GitHub Pages
by `.github/workflows/pages.yml` on every push touching `frontend/**`.

```
npm install
npm run dev        # local dev server
npm run build      # tsc -b && vite build, output in dist/
npm test           # vitest, the calibration math
npm run lint       # oxlint
npm run sync:ios   # build + cap sync ios (see ../IOS_HANDOFF.md)
```

`vite.config.ts` sets `base: '/leeway/'` because Pages serves the app from a
subpath. Serving `dist/` at a web root will 404 every asset — put it under a
`leeway/` directory if you need to preview a production build locally.

## Layout

- `App.tsx` — the planner: route list, options tabs, map, verdict card. Most
  of the app lives here.
- `api.ts` — the backend calls. Every one has an abort timeout, and 502/504
  are translated, because those come from Render's proxy giving up on a slow
  plan rather than from the planner itself.
- `storage.ts` — everything device-local: car range, units, theme, recent
  trips, logged trips, and the recency-weighted calibration factor. Every
  read and write is wrapped, because Chrome with cookies blocked throws on
  *any* localStorage access and an unguarded read white-screens the app
  before first paint.
- `LocationInput.tsx`, `VoiceBar.tsx`, `CarSetup.tsx`, `TripCard.tsx`,
  `TripFeedback.tsx`, `AccuracyPage.tsx` — one screen or control each.
- `native.ts` — the iOS-only paths (local notification, share sheet, status
  bar). No-ops on the web.
- `public/overview/` — the product overview page, plain HTML, deployed
  alongside the app at `/leeway/overview/`.

## Things that will bite you

**Don't fight MapLibre's attribution with a MutationObserver.** Holding
`maplibregl-compact-show` off with an observer whose callback changes a class
makes the observer and MapLibre spin against each other. On a phone-width map
that pegs the main thread and the app never finishes loading; a desktop map
never enters compact mode, so it looks completely fine while mobile is dead.
This shipped once. The chip row clears a two-row attribution in CSS instead.

**Chrome will not resize a window below ~500px.** Testing phone layouts by
resizing silently leaves you on a 500px viewport and everything looks fine.
Use a sized iframe.

**`maplibre-gl` is held at `^5.9.0`,** which takes 5.x updates and stops
short of 6. That range is deliberate: 6.0.0 loaded no vector tiles at all and
surfaced no error anywhere — not `map.on('error')`, not a failed request. Do
not widen it without visually confirming tiles still render, because nothing
automated catches this. The build passes and the console stays clean.

**Images need `height: auto`.** The overview page sets `width`/`height`
attributes to reserve layout space; without `height: auto` those attributes
also fix the rendered height, and every screenshot renders at full pixel
height in a narrow column.
