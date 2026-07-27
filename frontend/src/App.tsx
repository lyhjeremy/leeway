import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import './App.css'
import LocationInput from './LocationInput'
import CarSetup from './CarSetup'
import TripCard from './TripCard'
import VoiceBar from './VoiceBar'
import AccuracyPage from './AccuracyPage'
import TripFeedback from './TripFeedback'
import { fetchRoutes, geocode, planTrip } from './api'
import { cancelTripLogNudge, scheduleTripLogNudge, shareNative, syncStatusBar } from './native'
import type {
  ChargerFilter,
  ChargingStop,
  GeocodeResult,
  PlanResponse,
  RouteAlt,
  SafetyMode,
  StopMode,
  Units,
  Waypoint,
} from './types'
import {
  clearPendingTrip,
  computeCalibration,
  loadDistUnit,
  loadFullRangeMi,
  loadRecentTrips,
  loadTempUnit,
  logRangeHistory,
  logTripResult,
  saveDistUnit,
  saveFullRangeMi,
  savePendingTrip,
  saveRecentTrip,
  saveTempUnit,
  saveTheme,
  shouldPromptForPendingTrip,
  type PendingTrip,
  type RecentTrip,
} from './storage'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'https://leeway-api.onrender.com'

export const MI_TO_KM = 1.609344

// Pads the recent-trips row to three chips while real history is short.
// Clearly marked "try:" - a suggestion pretending to be your history would
// be a small lie, and this product doesn't do those.
const SUGGESTED_TRIPS: RecentTrip[] = [
  {
    origin: { label: 'Los Angeles, CA', lat: 34.0522, lon: -118.2437 },
    destination: { label: 'San Francisco, CA', lat: 37.7749, lon: -122.4194 },
  },
  {
    origin: { label: 'New York, NY', lat: 40.7128, lon: -74.006 },
    destination: { label: 'Boston, MA', lat: 42.3601, lon: -71.0589 },
  },
  {
    origin: { label: 'Chicago, IL', lat: 41.8781, lon: -87.6298 },
    destination: { label: 'Detroit, MI', lat: 42.3314, lon: -83.0458 },
  },
]

const STOP_MODES: { value: StopMode; label: string }[] = [
  { value: 'fewest_stops', label: 'Fewest stops' },
  { value: 'fastest_trip', label: 'Fastest trip' },
  { value: 'best_amenities', label: 'Best amenities' },
]

const CHARGER_FILTERS: { value: ChargerFilter; label: string }[] = [
  { value: 'all', label: 'All chargers' },
  { value: 'tesla_only', label: 'Superchargers' },
  { value: 'non_tesla', label: 'Non-Tesla' },
]

const SAFETY_MODES: { value: SafetyMode; label: string }[] = [
  { value: 'flag_only', label: 'Flag only' },
  { value: 'avoid_quick', label: '+5 min' },
  { value: 'avoid_hard', label: '+10 min' },
  { value: 'avoid_max', label: '+20 min' },
]

const SAFETY_BUDGET_LABEL: Record<string, string> = {
  avoid_quick: '5',
  avoid_hard: '10',
  avoid_max: '20',
}

// First-visit demo trip - shown pre-filled so the value is visible before
// anyone types anything. Real coordinates (Culver City -> SF Mission), not
// geocoded on load, so this renders instantly even before ORS is configured.
// Mainland USA. The overview screenshots are all framed on this view.
const US_CENTER: [number, number] = [-98.5, 39.5]
const US_ZOOM = 3.5

const DEMO_ORIGIN: GeocodeResult = { label: 'Culver City, Los Angeles', lat: 34.0211, lon: -118.3965 }
const DEMO_DESTINATION: GeocodeResult = { label: 'Mission District, San Francisco', lat: 37.7599, lon: -122.4194 }

// Fiord's roads are darker than its ground (#3C4357 on #45516E), so they
// vanish at city zoom. These overrides lift roads and state lines to
// clearly-lighter blues while the ground stays night-dark - checked
// against the style's real layer ids, applied after every style load.
const NIGHT_ROAD_TWEAKS: [string, string][] = [
  ['highway_minor', 'hsl(224, 20%, 60%)'],
  ['highway_major_inner', 'hsl(224, 28%, 74%)'],
  ['highway_major_casing', 'hsl(224, 22%, 50%)'],
  ['highway_major_subtle', 'hsla(224, 28%, 68%, 0.6)'],
  ['highway_motorway_inner', 'hsl(224, 32%, 80%)'],
  ['highway_motorway_casing', 'hsl(224, 22%, 52%)'],
  ['highway_motorway_subtle', 'hsla(224, 32%, 72%, 0.45)'],
  ['highway_path', 'hsl(211, 24%, 52%)'],
  ['tunnel_motorway_inner', 'hsl(224, 18%, 44%)'],
  ['boundary_state', 'hsla(195, 50%, 76%, 0.6)'],
]

function applyNightRoadContrast(map: maplibregl.Map) {
  map.once('style.load', () => {
    for (const [id, color] of NIGHT_ROAD_TWEAKS) {
      if (map.getLayer(id)) map.setPaintProperty(id, 'line-color', color)
    }
  })
}

function App() {
  const [route, setRoute] = useState(window.location.hash)
  useEffect(() => {
    const onHashChange = () => setRoute(window.location.hash)
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  // The planner stays mounted underneath the accuracy page - unmounting it
  // threw away the whole planned trip every time someone glanced at the
  // accuracy record and came back.
  return (
    <>
      <div style={{ display: route === '#accuracy' ? 'none' : 'contents' }}>
        <Planner />
      </div>
      {route === '#accuracy' && <AccuracyPage />}
    </>
  )
}

function Planner() {
  const mapContainer = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markersRef = useRef<maplibregl.Marker[]>([])
  const lastBoundsRef = useRef<maplibregl.LngLatBounds | null>(null)
  // True once the CURRENT style's 'style.load' has fired. NOT the same as
  // map.isStyleLoaded(), which stays false until every tile source finishes
  // loading - on a slow tile CDN that meant "style.load already fired, the
  // gate says not ready, the once-listener waits forever, route never
  // draws". Reset on every setStyle.
  const styleReadyRef = useRef(false)

  const [apiStatus, setApiStatus] = useState<'checking' | 'ok' | 'down'>('checking')
  const [units, setUnits] = useState<Units>(() => loadDistUnit())
  const [tempUnit, setTempUnit] = useState<'F' | 'C'>(() => loadTempUnit())
  const [origin, setOrigin] = useState<GeocodeResult | null>(DEMO_ORIGIN)
  const [destination, setDestination] = useState<GeocodeResult | null>(DEMO_DESTINATION)
  // Raw field text, kept so Plan can resolve a typed-but-never-picked place
  // itself instead of demanding a dropdown interaction.
  const [originText, setOriginText] = useState(DEMO_ORIGIN.label)
  const [destText, setDestText] = useState(DEMO_DESTINATION.label)
  // Middle stops live between origin and destination as editable rows; a
  // null entry is an empty row being typed into. Cap of three keeps trips
  // honest - beyond that you're planning a tour, not a drive.
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null)
  // Bumped after every reorder so the rows remount and re-read their
  // values - without it a row's inner text can stay with its old position.
  const [orderVersion, setOrderVersion] = useState(0)
  const [batteryPct, setBatteryPct] = useState(68)
  const [fullRangeMi, setFullRangeMi] = useState<number>(() => loadFullRangeMi() ?? 205)
  const [stopMode, setStopMode] = useState<StopMode>('fewest_stops')
  const [chargerFilter, setChargerFilter] = useState<ChargerFilter>('all')
  // Default is the +5 min detour budget, not flag-only: someone who never
  // opens the options still gets routed around cheap-to-avoid hazards.
  const [safetyMode, setSafetyMode] = useState<SafetyMode>('avoid_quick')
  const [hazardTypes, setHazardTypes] = useState<Record<string, boolean>>({
    unprotected_left: true,
    wide_crossing: true,
    rail_crossing: true,
    lane_closure: true,
  })
  const [avoidTolls, setAvoidTolls] = useState(false)
  const [avoidHighways, setAvoidHighways] = useState(false)
  // The options live under four always-visible tabs grouped by what the
  // driver is deciding about. null = all collapsed (the default): tapping a
  // tab opens its pane, tapping the open tab closes it again.
  const [optionsTab, setOptionsTab] = useState<'safety' | 'charging' | 'route' | 'trip' | null>(null)
  const [chargeToPct, setChargeToPct] = useState(80)
  const [reservePct, setReservePct] = useState(15)
  const [arrivalTargetPct, setArrivalTargetPct] = useState(0)
  const [passengers, setPassengers] = useState(0)
  const [suitcases, setSuitcases] = useState(0)
  const [tempOverrideOn, setTempOverrideOn] = useState(false)
  const [tempOverrideF, setTempOverrideF] = useState(70)
  const [departureLocal, setDepartureLocal] = useState('') // '' = leaving now
  const [maxStintMin, setMaxStintMin] = useState(0) // 0 = off, 5-min steps
  const [minChargerKw, setMinChargerKw] = useState(20)
  const [networks, setNetworks] = useState<Record<string, boolean>>({
    ChargePoint: false,
    'Electrify America': false,
    EVgo: false,
    Tesla: false,
    Blink: false,
  })
  const [avoidFerries, setAvoidFerries] = useState(false)
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCarSetup, setShowCarSetup] = useState(false)
  const [recentTrips, setRecentTrips] = useState<RecentTrip[]>(() => loadRecentTrips())
  const [excludedStationIds, setExcludedStationIds] = useState<number[]>([])
  const [waypoints, setWaypoints] = useState<(GeocodeResult | null)[]>([])
  const [routeAlts, setRouteAlts] = useState<RouteAlt[] | null>(null)
  const [chosenAlt, setChosenAlt] = useState(0)
  const [pickingStop, setPickingStop] = useState(false)
  const [shareMsg, setShareMsg] = useState<string | null>(null)
  const [showTripCard, setShowTripCard] = useState(false)
  const [pendingTrip, setPendingTrip] = useState<PendingTrip | null>(() => shouldPromptForPendingTrip())
  // Learned from this browser's logged trips; null until 3+ meaningful logs
  // exist. Recomputed when a new trip gets logged.
  const [calibration, setCalibration] = useState(() => computeCalibration())

  // All numbers live in miles internally; only the display converts.
  const dist = (mi: number) => `${Math.round(units === 'km' ? mi * MI_TO_KM : mi)} ${units}`
  const driveTime = (min: number) => (min >= 60 ? `${Math.floor(min / 60)} h ${min % 60} min` : `${min} min`)
  const tempDisplay = (f: number) => (tempUnit === 'C' ? Math.round(((f - 32) * 5) / 9) : Math.round(f))
  const tempFromDisplay = (v: number) => (tempUnit === 'C' ? (v * 9) / 5 + 32 : v)

  function toggleUnits() {
    const next = units === 'mi' ? 'km' : 'mi'
    setUnits(next)
    saveDistUnit(next)
  }

  function toggleTempUnit() {
    const next = tempUnit === 'F' ? 'C' : 'F'
    setTempUnit(next)
    saveTempUnit(next)
  }

  // The floating cards cover the map's left and right edges on desktop -
  // pad any bounds-fit so the route isn't hidden underneath them.
  const fitPadding = () =>
    window.innerWidth > 1000 ? { top: 90, bottom: 60, left: 410, right: 430 } : 60

  function handleLocateMe() {
    if (!navigator.geolocation) {
      setShareMsg("This browser doesn't share location")
      setTimeout(() => setShareMsg(null), 2500)
      return
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const o = { label: 'My location', lat: pos.coords.latitude, lon: pos.coords.longitude }
        setOrigin(o)
        setOriginText(o.label)
        mapRef.current?.flyTo({ center: [o.lon, o.lat], zoom: 11, duration: 800 })
      },
      () => {
        setShareMsg("Couldn't get your location - check the browser permission")
        setTimeout(() => setShareMsg(null), 2500)
      },
      { enableHighAccuracy: true, timeout: 8000 },
    )
  }

  function handleRecenter() {
    const map = mapRef.current
    if (!map) return
    if (lastBoundsRef.current) map.fitBounds(lastBoundsRef.current, { padding: fitPadding(), duration: 500 })
    else map.flyTo({ center: US_CENTER, zoom: US_ZOOM, duration: 500 })
  }

  // main.tsx already stamped the theme on <html> before first paint.
  const [theme, setTheme] = useState<'light' | 'dark'>(
    () => (document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'),
  )

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    saveTheme(next)
    document.documentElement.dataset.theme = next
    syncStatusBar(next)
    // The map gets its own night: swap the whole style, then force the
    // route effect to re-run once the new style loads - setStyle wipes
    // every source, layer, and the collapsed-attribution state.
    const map = mapRef.current
    if (!map) return
    styleReadyRef.current = false
    // diff: false forces a full style reload. The default diffing path can
    // morph one style into the other WITHOUT firing 'style.load' - and then
    // everything queued on that event (the route redraw, the re-triggering
    // below) starves forever. A full reload is slower but deterministic.
    map.setStyle(
      next === 'dark' ? 'https://tiles.openfreemap.org/styles/fiord' : 'https://tiles.openfreemap.org/styles/liberty',
      { diff: false },
    )
    // 'style.load', NOT 'styledata': styledata fires while the old style is
    // still being torn down, so a redraw triggered there can land its layers
    // in the doomed style and lose them (seen live as "toggle theme = route,
    // pins, and stops all vanish"). style.load fires once the new style is
    // ready for layers.
    map.once('style.load', () => {
      map.getContainer().querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show')
      setPlan((p) => (p ? { ...p } : p))
      setRouteAlts((r) => (r ? [...r] : r))
    })
    if (next === 'dark') applyNightRoadContrast(map)
  }

  useEffect(() => {
    if (!mapContainer.current) return
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style:
        document.documentElement.dataset.theme === 'dark'
          ? 'https://tiles.openfreemap.org/styles/fiord'
          : 'https://tiles.openfreemap.org/styles/liberty',
      center: US_CENTER,
      zoom: US_ZOOM,
      // Explicit, not implicit: render at the device's real pixel density -
      // a phone at DPR 3 rendering at a stale/lower ratio is exactly the
      // "map looks low resolution" report.
      pixelRatio: window.devicePixelRatio || 1,
      // Collapses the attribution to an (i) button - the full line wrapped
      // to two rows on a 320px screen and covered a third of the map.
      attributionControl: { compact: true },
    })
    // MapLibre leaves the compact attribution expanded until first toggle;
    // start it collapsed, the (i) button re-opens it.
    map.once('load', () => {
      map.getContainer().querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show')
    })
    if (document.documentElement.dataset.theme === 'dark') applyNightRoadContrast(map)
    map.on('style.load', () => {
      styleReadyRef.current = true
    })
    mapRef.current = map
    if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__leewayMap = map
    // A late resize nudge plus one on orientation change: if the canvas was
    // sized before the stacked-layout CSS settled (fonts, dvh, URL-bar
    // collapse), the buffer stays stale and the map renders stretched and
    // blurry on phones.
    const settle = window.setTimeout(() => map.resize(), 600)
    const onOrient = () => window.setTimeout(() => map.resize(), 250)
    window.addEventListener('orientationchange', onOrient)
    return () => {
      window.clearTimeout(settle)
      window.removeEventListener('orientationchange', onOrient)
      map.remove()
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    map.getCanvas().style.cursor = pickingStop ? 'crosshair' : ''
    if (!pickingStop) return

    const onClick = (e: maplibregl.MapMouseEvent) => {
      const next = [...waypoints, { label: `Map stop ${waypoints.length + 1}`, lat: e.lngLat.lat, lon: e.lngLat.lng }]
      setWaypoints(next)
      setPickingStop(false)
      runPlan({ excludedStationIds, waypoints: next })
    }
    map.on('click', onClick)
    return () => {
      map.off('click', onClick)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickingStop, waypoints])

  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then((r) => (r.ok ? setApiStatus('ok') : setApiStatus('down')))
      .catch(() => setApiStatus('down'))
  }, [])

  // A different trip means the old corridor comparison no longer applies.
  useEffect(() => {
    setRouteAlts(null)
    setChosenAlt(0)
    const map = mapRef.current
    if (!map) return
    for (const i of [1, 2]) {
      if (map.getLayer(`alt-line-${i}`)) map.removeLayer(`alt-line-${i}`)
      if (map.getSource(`alt-${i}`)) map.removeSource(`alt-${i}`)
    }
  }, [origin, destination])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !routeAlts) return
    const draw = () => {
      routeAlts.forEach((alt, i) => {
        if (i === 0) return // the baseline is the planned route itself
        const data = {
          type: 'Feature' as const,
          properties: {},
          geometry: { type: 'LineString' as const, coordinates: alt.geometry },
        }
        const existing = map.getSource(`alt-${i}`) as maplibregl.GeoJSONSource | undefined
        if (existing) existing.setData(data)
        else {
          map.addSource(`alt-${i}`, { type: 'geojson', data })
          // Below the route line when it exists. After a theme swap wipes
          // every layer, this effect can run before the route redraws -
          // anchoring on the missing 'route-line' then THROWS mid-dispatch
          // and silently kills the queued route redraw behind it (found as
          // "toggle dark while comparing corridors = route vanishes"). The
          // route is added after us in that case, so it lands on top anyway.
          const anchor = map.getLayer('route-line') ? 'route-line' : undefined
          map.addLayer(
            {
              id: `alt-line-${i}`,
              type: 'line',
              source: `alt-${i}`,
              paint: { 'line-color': '#8b9083', 'line-width': 3, 'line-dasharray': [2, 2] },
            },
            anchor,
          )
        }
      })
    }
    if (styleReadyRef.current) draw()
    else map.once('style.load', draw)
  }, [routeAlts])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !plan) return
    // A rate-limited plan can come back with no geometry at all - building
    // map bounds from geometry[0] === undefined threw and unmounted the
    // whole app. The verdict and note still render; the map just keeps its
    // previous view.
    if (plan.geometry.length < 2) return

    const drawRoute = () => {
      // Colors read at draw time so a theme toggle (which re-triggers this
      // effect after the style swap) picks the right contrast. The route is
      // a vivid blue over a contrasting casing halo - the old ink-on-gray
      // line disappeared into the road network on both themes.
      const dark = document.documentElement.dataset.theme === 'dark'
      const routeColor = dark ? '#7fb2ff' : '#155ccc'
      const casingColor = dark ? '#0c1017' : '#ffffff'
      const markerColor = dark ? '#e8eadf' : '#0b0b0b'
      const geojson = {
        type: 'Feature' as const,
        properties: {},
        geometry: { type: 'LineString' as const, coordinates: plan.geometry },
      }
      const existing = map.getSource('route') as maplibregl.GeoJSONSource | undefined
      if (existing) {
        existing.setData(geojson)
        map.setPaintProperty('route-line', 'line-color', routeColor)
        map.setPaintProperty('route-casing', 'line-color', casingColor)
      } else {
        map.addSource('route', { type: 'geojson', data: geojson })
        map.addLayer({
          id: 'route-casing',
          type: 'line',
          source: 'route',
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': casingColor, 'line-width': 10 },
        })
        map.addLayer({
          id: 'route-line',
          type: 'line',
          source: 'route',
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': routeColor, 'line-width': 5.5 },
        })
      }

      markersRef.current.forEach((m) => m.remove())
      markersRef.current = []

      // Small always-visible tags beside the anchor points, so start, stops,
      // and destination read at a glance without hovering anything.
      const addTag = (lon: number, lat: number, text: string) => {
        const el = document.createElement('div')
        el.className = 'map-tag'
        el.textContent = text
        markersRef.current.push(
          new maplibregl.Marker({ element: el, anchor: 'left', offset: [13, -6] }).setLngLat([lon, lat]).addTo(map),
        )
      }
      const shortName = (title: string) => {
        const s = title.split(' · ')[0].split(',')[0]
        return s.length > 18 ? `${s.slice(0, 17)}…` : s
      }

      if (origin) {
        markersRef.current.push(
          new maplibregl.Marker({ color: markerColor }).setLngLat([origin.lon, origin.lat]).addTo(map),
        )
        addTag(origin.lon, origin.lat, 'Start')
      }
      if (destination) {
        markersRef.current.push(
          new maplibregl.Marker({ color: markerColor }).setLngLat([destination.lon, destination.lat]).addTo(map),
        )
        addTag(destination.lon, destination.lat, `Arrive ${plan.arrival_pct}%`)
      }

      // One shared popup. Hover opens it; leaving the pin starts a short
      // grace timer instead of closing instantly, and entering the bubble
      // cancels the timer - without that, the maps link inside was
      // unreachable (the bubble vanished the moment the cursor left the
      // pin to travel toward it). Click pins it until the next map click.
      // Content is DOM-built - OCM titles must not be able to inject markup.
      const popup = new maplibregl.Popup({ closeButton: false, offset: 16, maxWidth: '270px' })
      let pinned = false
      let closeTimer: number | undefined
      const scheduleClose = () => {
        window.clearTimeout(closeTimer)
        closeTimer = window.setTimeout(() => {
          if (!pinned) popup.remove()
        }, 300)
      }
      const holdOpen = () => window.clearTimeout(closeTimer)
      popup.on('close', () => {
        pinned = false
      })
      popup.on('open', () => {
        const el = popup.getElement()
        if (el) {
          el.onmouseenter = holdOpen
          el.onmouseleave = scheduleClose
        }
      })
      const buildStopContent = (stop: ChargingStop) => {
        const box = document.createElement('div')
        const title = document.createElement('div')
        title.className = 'pop-title'
        title.textContent = stop.title
        box.appendChild(title)
        const facts = document.createElement('div')
        facts.className = 'pop-sub'
        const bits = [stop.network]
        if (stop.max_kw) bits.push(`up to ${Math.round(stop.max_kw)} kW`)
        if (stop.stall_count) bits.push(`${stop.stall_count} stall${stop.stall_count === 1 ? '' : 's'}`)
        facts.textContent = bits.join(' · ')
        box.appendChild(facts)
        const line = document.createElement('div')
        line.className = 'pop-sub'
        line.textContent = stop.is_waypoint
          ? `Pass through with ${stop.arrive_pct}%`
          : `Arrive ${stop.arrive_pct}% → charge to ${stop.charge_to_pct}%${
              stop.charge_time_min ? ` (~${stop.charge_time_min} min)` : ''
            }`
        box.appendChild(line)
        if (stop.cost) {
          const cost = document.createElement('div')
          cost.className = 'pop-sub'
          cost.textContent = stop.cost
          box.appendChild(cost)
        }
        if (stop.photo_url) {
          const img = document.createElement('img')
          img.className = 'pop-img'
          img.src = stop.photo_url
          img.alt = stop.title
          img.loading = 'lazy'
          box.appendChild(img)
        }
        const link = document.createElement('a')
        link.className = 'pop-link'
        link.href = `https://www.google.com/maps/search/?api=1&query=${stop.lat},${stop.lon}`
        link.target = '_blank'
        link.rel = 'noopener'
        link.textContent = 'Open in Google Maps →'
        box.appendChild(link)
        return box
      }
      const buildFlagContent = (description: string) => {
        const box = document.createElement('div')
        const title = document.createElement('div')
        title.className = 'pop-title'
        title.textContent = 'Heads up'
        box.appendChild(title)
        const sub = document.createElement('div')
        sub.className = 'pop-sub'
        sub.textContent = description
        box.appendChild(sub)
        return box
      }
      const attachPopup = (el: HTMLElement, lon: number, lat: number, build: () => HTMLElement) => {
        const show = () => {
          popup.setLngLat([lon, lat]).setDOMContent(build()).addTo(map)
        }
        el.addEventListener('mouseenter', () => {
          holdOpen()
          if (!pinned) show()
        })
        el.addEventListener('mouseleave', () => {
          if (!pinned) scheduleClose()
        })
        el.addEventListener('click', (e) => {
          e.stopPropagation()
          pinned = true
          holdOpen()
          show()
        })
      }

      for (const stop of plan.stops) {
        const el = document.createElement('div')
        el.className = stop.is_waypoint
          ? 'pin pin-waypoint'
          : stop.is_supercharger
            ? 'pin pin-supercharger'
            : 'pin pin-ccs'
        attachPopup(el, stop.lon, stop.lat, () => buildStopContent(stop))
        markersRef.current.push(new maplibregl.Marker({ element: el }).setLngLat([stop.lon, stop.lat]).addTo(map))
        addTag(stop.lon, stop.lat, shortName(stop.title))
      }
      for (const flag of plan.safety_flags) {
        if (flag.lat == null || flag.lon == null) continue
        const el = document.createElement('div')
        el.className = 'pin-hazard'
        attachPopup(el, flag.lon, flag.lat, () => buildFlagContent(flag.description))
        // anchor bottom: the triangle's tip touches the flagged spot and its
        // body sits above it, so it can't cover (and steal clicks from) a
        // charging-stop pin at the same location - which really happens: the
        // unprotected-left flags at the Castaic exit sit exactly on the
        // Castaic charger.
        markersRef.current.push(
          new maplibregl.Marker({ element: el, anchor: 'bottom' }).setLngLat([flag.lon, flag.lat]).addTo(map),
        )
      }

      const bounds = plan.geometry.reduce(
        (b, c) => b.extend(c as [number, number]),
        new maplibregl.LngLatBounds(plan.geometry[0], plan.geometry[0]),
      )
      lastBoundsRef.current = bounds
      map.fitBounds(bounds, { padding: fitPadding(), duration: 500 })
    }

    // Gate on our own styleReadyRef, not map.isStyleLoaded(): the latter
    // stays false until every TILE source finishes, so on a slow CDN the
    // 'style.load' event had already fired while the gate still said no -
    // and a once-listener for an already-fired event waits forever.
    if (styleReadyRef.current) drawRoute()
    else map.once('style.load', drawRoute)
  }, [plan, origin, destination])

  async function runPlan(overrides: {
    excludedStationIds: number[]
    waypoints: (GeocodeResult | null)[]
    via?: { lat: number; lon: number } | null
    // After a drag-reorder the setState calls haven't landed when the
    // replan fires - the new endpoints must ride along explicitly or the
    // request goes out with the pre-drag origin/destination (found by a
    // real drag test: the rows swapped, the request didn't).
    origin?: GeocodeResult | null
    destination?: GeocodeResult | null
  }) {
    // A cleared number input reads as 0, which the backend (rightly) refuses.
    if (!Number.isFinite(fullRangeMi) || fullRangeMi < 50 || fullRangeMi > 600) {
      setError(
        units === 'km'
          ? "Enter your car's real 100% range first (between 80 and 965 km)."
          : "Enter your car's real 100% range first (between 50 and 600 miles).",
      )
      return
    }
    // The floor is max(reserve %, 30-mile reserve as a %) - on a small range
    // the mile part can climb above the charge-to slider, and a plan that
    // charges to at-or-below its own floor can never make progress.
    const floorPct = Math.max(reservePct, (30 / fullRangeMi) * 100)
    if (chargeToPct <= floorPct + 5) {
      setError(
        `Charging to ${chargeToPct}% can't clear your reserve floor of ${Math.round(floorPct)}%. ` +
          'Raise the charge-to level (advanced) or lower the reserve.',
      )
      return
    }
    if (arrivalTargetPct > 0 && arrivalTargetPct > chargeToPct - 10) {
      setError(`An arrival target of ${arrivalTargetPct}% needs headroom below the ${chargeToPct}% charge-to level.`)
      return
    }
    setLoading(true)
    setError(null)
    // Resolve typed-but-never-picked places right here - requiring a
    // dropdown pick made Plan fail whenever the dropdown flaked (slow
    // backend wake-up, a tap elsewhere closing it).
    let o = overrides.origin !== undefined ? overrides.origin : origin
    let d = overrides.destination !== undefined ? overrides.destination : destination
    if (!o || !d) {
      try {
        if (!o && originText.trim().length >= 3) o = (await geocode(originText))[0] ?? null
        if (!d && destText.trim().length >= 3) d = (await geocode(destText))[0] ?? null
      } catch {
        setError('Search hiccuped - the backend may be waking up. Try planning again in a moment.')
        setLoading(false)
        return
      }
      if (o) setOrigin(o)
      if (d) setDestination(d)
      if (!o || !d) {
        const missing = !o ? originText : destText
        setError(
          missing.trim().length >= 3
            ? `Couldn't find "${missing}". Try a city, or a street address with its state.`
            : 'Enter both a start and a destination.',
        )
        setLoading(false)
        return
      }
    }
    try {
      const allWaypoints: Waypoint[] = overrides.waypoints
        .filter((w): w is GeocodeResult => w !== null)
        .map((w) => ({ lat: w.lat, lon: w.lon, title: w.label.split(',')[0] }))
      if (overrides.via) {
        allWaypoints.push({ ...overrides.via, title: 'via', hidden: true })
      }
      const result = await planTrip({
        origin: { lat: o.lat, lon: o.lon },
        destination: { lat: d.lat, lon: d.lon },
        batteryPct,
        fullRangeMi,
        stopMode,
        chargeToPct,
        reservePct,
        avoidTolls,
        avoidHighways,
        safetyMode,
        chargerFilter,
        arrivalTargetPct,
        passengers,
        suitcases,
        tempOverrideF: tempOverrideOn ? tempOverrideF : null,
        units,
        tempUnit,
        hazardTypes: Object.keys(hazardTypes).filter((k) => hazardTypes[k]),
        departureEpoch: departureLocal ? Date.parse(departureLocal) / 1000 : null,
        maxStintMin,
        minChargerKw,
        preferredNetworks: Object.keys(networks).filter((k) => networks[k]),
        avoidFerries,
        excludedStationIds: overrides.excludedStationIds,
        waypoints: allWaypoints,
        calibrationFactor: calibration?.factor,
      })
      setPlan(result)
      saveRecentTrip({ origin: o, destination: d })
      setRecentTrips(loadRecentTrips())
      savePendingTrip({
        originLabel: o.label,
        destinationLabel: d.label,
        predictedArrivalPct: result.arrival_pct,
        feasible: result.feasible,
        startBatteryPct: batteryPct,
      })
      // iOS app only: a local notification after the drive should be over,
      // nudging the ten-second predicted-vs-actual log. No-op on the web.
      scheduleTripLogNudge({
        originLabel: o.label,
        destinationLabel: d.label,
        departureEpoch: departureLocal ? Date.parse(departureLocal) / 1000 : null,
        durationMin: result.duration_min,
      })
    } catch (e) {
      // A network-level failure surfaces as TypeError('Failed to fetch') -
      // raw browser-speak that reads like a bug. Say what it usually means.
      setError(
        e instanceof TypeError
          ? "Can't reach the planner right now - it may be waking up. Try again in a moment."
          : e instanceof Error
            ? e.message
            : 'Something went wrong planning that trip.',
      )
      setPlan(null)
    } finally {
      setLoading(false)
    }
  }

  function handlePlan() {
    setExcludedStationIds([])
    runPlan({ excludedStationIds: [], waypoints, via: currentVia() })
  }

  function currentVia() {
    return routeAlts && chosenAlt > 0 ? routeAlts[chosenAlt].via : null
  }

  function handleSkipStop(id: number | null) {
    if (id == null) return
    const next = [...excludedStationIds, id]
    setExcludedStationIds(next)
    runPlan({ excludedStationIds: next, waypoints, via: currentVia() })
  }

  function removeWaypoint(index: number) {
    const next = waypoints.filter((_, i) => i !== index)
    setWaypoints(next)
    if (waypoints[index] !== null) runPlan({ excludedStationIds, waypoints: next, via: currentVia() })
  }

  function handleAddVoiceStop(lat: number, lon: number, title: string) {
    // A voice-found stop is just another waypoint - same mechanism as
    // typing one into a row, not a separate insertion path.
    if (waypoints.length >= 3) return
    const next = [...waypoints, { label: title, lat, lon }]
    setWaypoints(next)
    runPlan({ excludedStationIds, waypoints: next, via: currentVia() })
  }

  function handleRoutePointChange(i: number, r: GeocodeResult | null) {
    const last = waypoints.length + 1
    if (i === 0) {
      setOrigin(r)
      if (r) setOriginText(r.label)
      return
    }
    if (i === last) {
      setDestination(r)
      if (r) setDestText(r.label)
      return
    }
    const mids = [...waypoints]
    mids[i - 1] = r
    setWaypoints(mids)
    if (r && origin && destination) runPlan({ excludedStationIds, waypoints: mids, via: currentVia() })
  }

  // Any point can trade places with any other - start, stops, destination.
  function handleRouteDrop() {
    if (dragIdx === null || dragOverIdx === null || dragIdx === dragOverIdx) {
      setDragIdx(null)
      setDragOverIdx(null)
      return
    }
    const list: (GeocodeResult | null)[] = [origin, ...waypoints, destination]
    const [moved] = list.splice(dragIdx, 1)
    list.splice(dragOverIdx, 0, moved)
    const newOrigin = list[0]
    const newDest = list[list.length - 1]
    const mids = list.slice(1, -1)
    setOrigin(newOrigin)
    setOriginText(newOrigin?.label ?? '')
    setDestination(newDest)
    setDestText(newDest?.label ?? '')
    setWaypoints(mids)
    setDragIdx(null)
    setDragOverIdx(null)
    setOrderVersion((v) => v + 1)
    setChosenAlt(0) // a picked corridor belonged to the old point order
    if (newOrigin && newDest) {
      runPlan({ excludedStationIds, waypoints: mids, via: null, origin: newOrigin, destination: newDest })
    }
  }

  async function handleCompareRoutes() {
    if (!origin || !destination) return
    try {
      const alts = await fetchRoutes(
        { lat: origin.lat, lon: origin.lon },
        { lat: destination.lat, lon: destination.lon },
        avoidTolls,
        avoidHighways,
        avoidFerries,
      )
      setRouteAlts(alts)
      setChosenAlt(0)
      if (alts.length === 1) setShareMsg('No genuinely different route exists for this trip')
      setTimeout(() => setShareMsg(null), 3000)
    } catch {
      setShareMsg('Could not fetch other routes right now')
      setTimeout(() => setShareMsg(null), 2500)
    }
  }

  function handleChooseAlt(i: number) {
    setChosenAlt(i)
    const via = routeAlts && i > 0 ? routeAlts[i].via : null
    runPlan({ excludedStationIds, waypoints, via })
  }

  async function handleShareStop(stop: ChargingStop) {
    // A maps link, not a "geo:" URI - the Tesla app and most nav apps
    // register as share targets for maps links (that's the real mechanism
    // behind "share a stop's address into the Tesla app" from the product
    // plan), while a raw geo: URI has much weaker cross-app support.
    const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${stop.lat},${stop.lon}`
    // Inside the iOS shell WKWebView has no navigator.share - the native
    // share sheet (which is how a stop reaches the Tesla app) goes through
    // the Capacitor plugin instead.
    if (await shareNative({ title: stop.title, text: `Charging stop: ${stop.title}`, url: mapsUrl })) return
    if (navigator.share) {
      try {
        await navigator.share({ title: stop.title, text: `Charging stop: ${stop.title}`, url: mapsUrl })
        return
      } catch {
        // user cancelled the share sheet, or the platform rejected it - fall
        // through to the clipboard fallback below rather than doing nothing
      }
    }
    try {
      await navigator.clipboard.writeText(mapsUrl)
      setShareMsg(`Copied "${stop.title}" map link`)
    } catch {
      setShareMsg('Could not share - long-press the map pin instead')
    }
    setTimeout(() => setShareMsg(null), 2500)
  }

  function pickRecentTrip(trip: RecentTrip) {
    setOrigin(trip.origin)
    setDestination(trip.destination)
    setWaypoints([])
  }

  function handleCarSetupSave(rangeMi: number) {
    setFullRangeMi(rangeMi)
    saveFullRangeMi(rangeMi)
    logRangeHistory(rangeMi)
    setShowCarSetup(false)
  }

  function handleLogTripResult(actualArrivalPct: number) {
    if (!pendingTrip) return
    logTripResult(pendingTrip, actualArrivalPct)
    setPendingTrip(null)
    setCalibration(computeCalibration())
    cancelTripLogNudge()
  }

  function handleDismissPendingTrip() {
    clearPendingTrip()
    setPendingTrip(null)
    cancelTripLogNudge()
  }

  const rangeDisplay = Math.round(units === 'km' ? fullRangeMi * MI_TO_KM : fullRangeMi)

  // Always three trip chips: real history first, "try:" suggestions filling
  // the rest so the row never sits mostly empty.
  const shownRecents = recentTrips.slice(0, 3)
  const suggestedTrips = SUGGESTED_TRIPS.filter(
    (s) =>
      !shownRecents.some(
        (r) => r.origin.label === s.origin.label && r.destination.label === s.destination.label,
      ),
  ).slice(0, Math.max(0, 3 - shownRecents.length))

  // A green dot on any tab whose settings differ from the defaults - a
  // collapsed tab must never hide something the driver set.
  const tabChanged: Record<'safety' | 'charging' | 'route' | 'trip', boolean> = {
    safety: safetyMode !== 'avoid_quick' || Object.values(hazardTypes).some((v) => !v),
    charging:
      Object.values(networks).some(Boolean) ||
      chargeToPct !== 80 ||
      reservePct !== 15 ||
      arrivalTargetPct > 0 ||
      minChargerKw > 20,
    route: avoidTolls || avoidHighways || avoidFerries || waypoints.some(Boolean),
    trip: passengers > 0 || suitcases > 0 || tempOverrideOn || departureLocal !== '' || maxStintMin > 0,
  }

  return (
    <div className="app-root">
      <div className="app-body">
        <aside className="panel panel-left">
          <div className="brand-row">
            <span className="logo-dot" />
            <span className="logo-word">Leeway</span>
            <span className="tag">the second opinion before you leave</span>
          </div>

          {/* One editable list: start, up to three stops, destination -
              every row drags to trade places with any other. */}
          <div className={`field-group${dragIdx !== null ? ' dragging' : ''}`}>
            {[origin, ...waypoints, destination].map((pt, i, arr) => {
              const isFirst = i === 0
              const isLast = i === arr.length - 1
              return (
                <div
                  key={`${orderVersion}-${i}-${arr.length}`}
                  className={`route-row${
                    dragOverIdx === i && dragIdx !== null && dragIdx !== i ? ' drop-target' : ''
                  }`}
                  draggable={dragIdx === i}
                  onDragStart={(e) => {
                    // Firefox needs data to start a drag at all; empty text
                    // also means a stray default-drop pastes nothing.
                    e.dataTransfer.setData('text/plain', '')
                    e.dataTransfer.effectAllowed = 'move'
                  }}
                  onDragOver={(e) => {
                    e.preventDefault()
                    setDragOverIdx(i)
                  }}
                  onDrop={(e) => {
                    e.preventDefault()
                    handleRouteDrop()
                  }}
                  onDragEnd={() => {
                    setDragIdx(null)
                    setDragOverIdx(null)
                  }}
                >
                  <span
                    className="drag-handle"
                    title="Drag to reorder"
                    onMouseDown={() => setDragIdx(i)}
                    onMouseUp={() => setDragIdx(null)}
                  >
                    ⠿
                  </span>
                  <LocationInput
                    placeholder={isFirst ? 'Start' : isLast ? 'Destination' : 'Search a stop along the way'}
                    dotClass={isFirst ? 'dot-a' : isLast ? 'dot-b' : 'dot-wp'}
                    value={pt}
                    onChange={(r) => handleRoutePointChange(i, r)}
                    onTextChange={isFirst ? setOriginText : isLast ? setDestText : undefined}
                  />
                  {!isFirst && !isLast && (
                    <button className="row-remove" onClick={() => removeWaypoint(i - 1)} title="Remove this stop">
                      ✕
                    </button>
                  )}
                </div>
              )
            })}
          </div>

          {waypoints.length < 3 && (
            <button
              className="link-btn"
              style={{ marginLeft: 0, marginTop: -4 }}
              onClick={() => setWaypoints([...waypoints, null])}
            >
              + add a stop along the way
            </button>
          )}

          <div>
            <div className="row-label">{shownRecents.length > 0 ? 'Recent trips' : 'Try a trip'}</div>
            <div className="recents">
              {shownRecents.map((t, i) => (
                <button key={i} className="chip" onClick={() => pickRecentTrip(t)}>
                  {t.origin.label.split(',')[0]} → {t.destination.label.split(',')[0]}
                </button>
              ))}
              {suggestedTrips.map((t, i) => (
                <button key={`s${i}`} className="chip chip-suggest" onClick={() => pickRecentTrip(t)}>
                  try: {t.origin.label.split(',')[0]} → {t.destination.label.split(',')[0]}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="row-label">Battery right now</div>
            <div className="battery-row">
              <span className="pct-value">{batteryPct}%</span>
              <input
                type="range"
                min={1}
                max={100}
                value={batteryPct}
                onChange={(e) => setBatteryPct(Number(e.target.value))}
              />
            </div>
          </div>

          <div>
            <div className="row-label">Your car's real range at 100%</div>
            <div className="battery-row">
              <input
                className="range-input"
                type="number"
                min={units === 'km' ? 80 : 50}
                max={units === 'km' ? 965 : 600}
                value={rangeDisplay}
                onChange={(e) => {
                  const v = Number(e.target.value)
                  setFullRangeMi(units === 'km' ? v / MI_TO_KM : v)
                }}
              />
              <span className="range-unit">{units}</span>
              <button className="link-btn" onClick={() => setShowCarSetup(true)}>
                find my real range
              </button>
            </div>
          </div>

          <div>
            <div className="row-label">Charging stops</div>
            <div className="seg">
              {STOP_MODES.map((m) => (
                <button key={m.value} className={m.value === stopMode ? 'on' : ''} onClick={() => setStopMode(m.value)}>
                  {m.label}
                </button>
              ))}
            </div>
            <div className="seg" style={{ marginTop: 8 }}>
              {CHARGER_FILTERS.map((m) => (
                <button
                  key={m.value}
                  className={m.value === chargerFilter ? 'on' : ''}
                  onClick={() => setChargerFilter(m.value)}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="options-tabs" role="tablist">
              {(
                [
                  ['safety', 'Safety'],
                  ['charging', 'Charging'],
                  ['route', 'Route'],
                  ['trip', 'Trip'],
                ] as ['safety' | 'charging' | 'route' | 'trip', string][]
              ).map(([value, label]) => (
                <button
                  key={value}
                  className={value === optionsTab ? 'on' : ''}
                  aria-expanded={value === optionsTab}
                  onClick={() => setOptionsTab((prev) => (prev === value ? null : value))}
                >
                  {label}
                  {tabChanged[value] && <span className="tab-dot" title="something set here" />}
                  <span className="tab-caret">{value === optionsTab ? '▴' : '▾'}</span>
                </button>
              ))}
            </div>
            {optionsTab !== null && (
              <div className="options">
          {optionsTab === 'safety' && (
          <div>
            <div className="row-label">Hazard detours</div>
            <div className="seg">
              {SAFETY_MODES.map((m) => (
                <button key={m.value} className={m.value === safetyMode ? 'on' : ''} onClick={() => setSafetyMode(m.value)}>
                  {m.label}
                </button>
              ))}
            </div>
            <div className="seg-hint">
              {safetyMode === 'flag_only'
                ? 'For each hazard below: ON = warn about it on the map (the route itself never changes in this mode). OFF = don’t check for it.'
                : `For each hazard below: ON = route around it when the detour adds ${
                    SAFETY_BUDGET_LABEL[safetyMode]
                  } minutes or less, otherwise warn about it. OFF = don’t check for it.`}
            </div>
            <div className="toggles" style={{ marginTop: 10 }}>
              {(
                [
                  ['unprotected_left', 'Avoid left turns cutting across big roads'],
                  ['wide_crossing', 'Avoid crossing 4+ lane roads without a signal'],
                  ['rail_crossing', 'Avoid rail crossings'],
                  ['lane_closure', 'Avoid construction / lane closures (California only)'],
                ] as [string, string][]
              ).map(([key, label]) => (
                <label className="tog" key={key}>
                  <span>{label}</span>
                  <input
                    type="checkbox"
                    className="switch"
                    checked={hazardTypes[key]}
                    onChange={(e) => setHazardTypes({ ...hazardTypes, [key]: e.target.checked })}
                  />
                </label>
              ))}
            </div>
          </div>
          )}

          {optionsTab === 'charging' && (
          <>
          <div>
            <div className="row-label">Charger networks</div>
            <div className="recents" style={{ marginTop: 8 }}>
              {Object.keys(networks).map((n) => (
                <button
                  key={n}
                  className={`chip${networks[n] ? ' chip-on' : ''}`}
                  onClick={() => setNetworks({ ...networks, [n]: !networks[n] })}
                >
                  {n}
                </button>
              ))}
            </div>
            <div className="seg-hint">Pick networks to limit stops to them - none picked means any network.</div>
          </div>

          <div className="advanced">
                <div className="row-label" style={{ marginTop: 10 }}>
                  Charge to at each stop
                </div>
                <div className="battery-row">
                  <span className="pct-value">{chargeToPct}%</span>
                  <input
                    type="range"
                    min={50}
                    max={100}
                    value={chargeToPct}
                    onChange={(e) => setChargeToPct(Number(e.target.value))}
                  />
                </div>
                <div className="seg-hint">80% is the fast-charging sweet spot - above it, charging slows a lot.</div>

                <div className="row-label" style={{ marginTop: 12 }}>
                  Reserve floor
                </div>
                <div className="battery-row">
                  <span className="pct-value">{reservePct}%</span>
                  <input
                    type="range"
                    min={5}
                    max={30}
                    value={reservePct}
                    onChange={(e) => setReservePct(Number(e.target.value))}
                  />
                </div>
                <div className="seg-hint">
                  No plan will ever count on dipping below this. Lower is braver, not smarter.
                </div>

                <div className="row-label" style={{ marginTop: 12 }}>
                  Arrive with at least
                </div>
                <div className="battery-row">
                  <span className="pct-value">{arrivalTargetPct === 0 ? 'off' : `${arrivalTargetPct}%`}</span>
                  <input
                    type="range"
                    min={0}
                    max={70}
                    step={5}
                    value={arrivalTargetPct}
                    onChange={(e) => setArrivalTargetPct(Number(e.target.value))}
                  />
                </div>
                <div className="seg-hint">Adds a charging stop near the end if needed - useful when there's no charging at your destination.</div>

                <div className="row-label" style={{ marginTop: 12 }}>
                  Minimum charger speed
                </div>
                <div className="battery-row">
                  <span className="pct-value" style={{ width: 72 }}>{minChargerKw} kW</span>
                  <input
                    type="range"
                    min={20}
                    max={250}
                    step={10}
                    value={minChargerKw}
                    onChange={(e) => setMinChargerKw(Number(e.target.value))}
                  />
                </div>
                <div className="seg-hint">Skips chargers slower than this when picking stops.</div>
          </div>
          </>
          )}

          {optionsTab === 'route' && (
          <>
          <div>
            <div className="row-label">Your stops along the way</div>
            <div className="seg-hint" style={{ marginTop: 0 }}>
              Search one in the route list up top, say it into the mic on the map, or pick a spot by hand:
            </div>
            {waypoints.length < 3 && (
              <button className="link-btn" style={{ marginLeft: 0, marginTop: 6 }} onClick={() => setPickingStop((v) => !v)}>
                {pickingStop ? 'click the map to add it…' : '+ add a stop by clicking the map'}
              </button>
            )}
          </div>


          <div className="toggles">
            <label className="tog">
              <span>Allow toll roads</span>
              <input
                type="checkbox"
                className="switch"
                checked={!avoidTolls}
                onChange={(e) => setAvoidTolls(!e.target.checked)}
              />
            </label>
            <label className="tog">
              <span>Avoid highways</span>
              <input
                type="checkbox"
                className="switch"
                checked={avoidHighways}
                onChange={(e) => setAvoidHighways(e.target.checked)}
              />
            </label>
            <label className="tog">
              <span>Avoid ferries</span>
              <input
                type="checkbox"
                className="switch"
                checked={avoidFerries}
                onChange={(e) => setAvoidFerries(e.target.checked)}
              />
            </label>
          </div>
          </>
          )}

          {optionsTab === 'trip' && (
          <div className="advanced">
                <div className="row-label" style={{ marginTop: 10 }}>
                  Load
                </div>
                <div className="load-row">
                  <label>
                    <span>passengers</span>
                    <input
                      type="number"
                      min={0}
                      max={6}
                      value={passengers}
                      onChange={(e) => setPassengers(Math.max(0, Math.min(6, Math.round(Number(e.target.value) || 0))))}
                    />
                  </label>
                  <label>
                    <span>suitcases</span>
                    <input
                      type="number"
                      min={0}
                      max={10}
                      value={suitcases}
                      onChange={(e) => setSuitcases(Math.max(0, Math.min(10, Math.round(Number(e.target.value) || 0))))}
                    />
                  </label>
                </div>
                <div className="seg-hint">Beyond the driver. A full car costs a few percent of range, not a scare number.</div>

                <div className="row-label" style={{ marginTop: 12 }}>
                  Leaving
                </div>
                <div className="battery-row">
                  <input
                    className="range-input"
                    style={{ width: 'auto', flex: 1 }}
                    type="datetime-local"
                    value={departureLocal}
                    onChange={(e) => setDepartureLocal(e.target.value)}
                  />
                  {departureLocal && (
                    <button className="link-btn" onClick={() => setDepartureLocal('')}>
                      now
                    </button>
                  )}
                </div>
                <div className="seg-hint">
                  {departureLocal
                    ? 'Weather, sun glare, and closures plan for this departure.'
                    : 'Leaving now - set a time to plan against the forecast instead.'}
                </div>

                <div className="row-label" style={{ marginTop: 12 }}>
                  Break at least every
                </div>
                <div className="battery-row">
                  <span className="pct-value" style={{ width: 72 }}>
                    {maxStintMin === 0 ? 'off' : `${Math.floor(maxStintMin / 60)}h${String(maxStintMin % 60).padStart(2, '0')}`}
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={360}
                    step={5}
                    value={maxStintMin}
                    onChange={(e) => {
                      const v = Number(e.target.value)
                      setMaxStintMin(v > 0 && v < 30 ? 30 : v)
                    }}
                  />
                </div>
                <div className="seg-hint">Forces a charging stop before any driving stretch runs longer than this.</div>

                <label className="tog" style={{ marginTop: 12 }}>
                  <span>Set temperature myself</span>
                  <input
                    type="checkbox"
                    className="switch"
                    checked={tempOverrideOn}
                    onChange={(e) => setTempOverrideOn(e.target.checked)}
                  />
                </label>
                {tempOverrideOn && (
                  <div className="battery-row" style={{ marginTop: 8 }}>
                    <input
                      className="range-input"
                      type="number"
                      value={tempDisplay(tempOverrideF)}
                      onChange={(e) => setTempOverrideF(tempFromDisplay(Number(e.target.value)))}
                    />
                    <span className="range-unit">{tempUnit === 'C' ? '°C' : '°F'}</span>
                  </div>
                )}
                {!tempOverrideOn && (
                  <div className="seg-hint">Live weather at the route midpoint is used unless you set your own.</div>
                )}
          </div>
          )}
              </div>
            )}
          </div>

          <div className="plan-cta">
            <button className="plan-btn" onClick={handlePlan} disabled={loading}>
              {loading ? 'Planning…' : 'Plan this trip'}
            </button>
          </div>

          {error && <div className="error-box">{error}</div>}
        </aside>

        <div className="map-wrap">
          <div ref={mapContainer} className="map" />
          <div className="float-chips">
            <button className="unit-toggle" onClick={handleLocateMe} title="Start from my location">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="7" />
                <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
                <line x1="12" y1="2" x2="12" y2="5" />
                <line x1="12" y1="19" x2="12" y2="22" />
                <line x1="2" y1="12" x2="5" y2="12" />
                <line x1="19" y1="12" x2="22" y2="12" />
              </svg>
            </button>
            <button className="unit-toggle" onClick={handleRecenter} title="Re-center the route">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 9V5a1 1 0 0 1 1-1h4" />
                <path d="M15 4h4a1 1 0 0 1 1 1v4" />
                <path d="M20 15v4a1 1 0 0 1-1 1h-4" />
                <path d="M9 20H5a1 1 0 0 1-1-1v-4" />
              </svg>
            </button>
            <button className="unit-toggle" onClick={toggleUnits} title="Switch distance unit">
              {units}
            </button>
            <button className="unit-toggle" onClick={toggleTempUnit} title="Switch temperature unit">
              {tempUnit === 'F' ? '°F' : '°C'}
            </button>
            <button
              className="unit-toggle theme-toggle"
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {theme === 'dark' ? '☀' : '☾'}
            </button>
            {apiStatus === 'down' && (
              <span className="api-chip api-chip--down">can't reach the planner - it may be waking up</span>
            )}
          </div>
          {/* Visible from the first paint, not gated on having planned - a
              first-time visitor should see the stop-search bar exists. It
              only needs endpoints, which the sample trip pre-fills. */}
          {origin && destination && (
            <VoiceBar origin={origin} destination={destination} units={units} onAddStop={handleAddVoiceStop} />
          )}
        </div>

        <aside className="panel panel-right">
          {!plan && (
            <div className="results-empty">
              <div className="row-label">The verdict</div>
              <div className="gauge" style={{ margin: '10px 0 6px' }}>
                <div className="gauge-track">
                  <div className="gauge-reserve" style={{ width: '15%' }} />
                </div>
                <div className="gauge-scale">
                  <span>0%</span>
                  <span>reserve</span>
                  <span>100%</span>
                </div>
              </div>
              <p className="seg-hint" style={{ marginTop: 4 }}>
                Plan a trip and this gauge fills to what's left in the battery when you arrive, next to every charging
                stop the plan verified.
              </p>
            </div>
          )}

          {/* A rate-limited plan with nothing in it has no real numbers to
              show - "Arrive at 68%" would just echo the starting battery
              with a straight face. Say what happened and stop there. */}
          {plan && plan.rate_limited && plan.distance_mi === 0 && (
            <div className="verdict verdict-bad">
              <div className="status">⚠ Planning couldn't run</div>
              <div className="sub note">
                The routing provider's usage limit is used up right now. Nothing got planned - try again in a little
                while.
              </div>
            </div>
          )}

          {plan && !(plan.rate_limited && plan.distance_mi === 0) && (
            <>
              <div className={`verdict ${plan.feasible ? 'verdict-ok' : 'verdict-bad'}`}>
                <div className="status">
                  {plan.feasible
                    ? '✓ Makeable with your reserve'
                    : plan.rate_limited
                      ? '⚠ Planning got interrupted partway - try again in a little while'
                      : plan.stops.length > 0
                        ? "⚠ Plan incomplete - couldn't lock in the leg after your last stop"
                        : plan.arrival_pct < 0
                          ? "⚠ Won't make it as planned - charge before you leave"
                          : '⚠ Tight - check the plan below'}
                </div>
                <div className="big">
                  {plan.arrival_pct < 0 ? `About ${dist(Math.abs(plan.leeway_mi))} short` : `Arrive at ${plan.arrival_pct}%`}{' '}
                  <small>{plan.leeway_mi >= 0 ? `${dist(plan.leeway_mi)} of leeway` : ''}</small>
                </div>
                <div className="gauge">
                  <div className="gauge-track">
                    <div
                      className={`gauge-fill${
                        plan.arrival_pct < 0 ? ' empty' : plan.arrival_pct < plan.reserve_floor_pct ? ' low' : ''
                      }`}
                      style={{ width: `${Math.min(Math.max(plan.arrival_pct, 3), 100)}%` }}
                    />
                    <div className="gauge-reserve" style={{ width: `${plan.reserve_floor_pct}%` }} />
                  </div>
                  <div className="gauge-scale">
                    <span>0%</span>
                    <span>reserve {plan.reserve_floor_pct}%</span>
                    <span>100%</span>
                  </div>
                </div>
                <div className="sub">
                  {dist(plan.distance_mi)} · {plan.stops.length} stop{plan.stops.length === 1 ? '' : 's'} ·{' '}
                  {Math.floor(plan.duration_min / 60)} h {plan.duration_min % 60} min total
                  {(() => {
                    const chargeMin = plan.stops.reduce((a, s) => a + (s.charge_time_min ?? 0), 0)
                    return chargeMin > 0 ? ` (incl. ~${chargeMin} min charging)` : ''
                  })()}
                </div>
                {plan.weather && <div className="sub">Range adjusted for {plan.weather.summary}</div>}
                {plan.calibration_factor != null && plan.calibration_factor !== 1 && calibration && (
                  <div className="sub">
                    Tuned to your car: your last {calibration.tripsUsed} logged trips ran{' '}
                    {Math.round(Math.abs(calibration.factor - 1) * 100)}%{' '}
                    {calibration.factor > 1 ? 'hungrier' : 'easier on the battery'} than the stock estimate.
                  </div>
                )}
                {plan.safety_flags.slice(0, 3).map((f, i) => (
                  <div className="sub safety-flag" key={i}>
                    ⚠ {f.description}
                  </div>
                ))}
                {plan.safety_flags.length > 3 && (
                  <div className="sub safety-flag">+{plan.safety_flags.length - 3} more flagged on the map</div>
                )}
                {plan.note && <div className="sub note">{plan.note}</div>}
              </div>

              <div>
                {!routeAlts && (
                  <button className="link-btn" style={{ marginLeft: 0 }} onClick={handleCompareRoutes}>
                    see other routes →
                  </button>
                )}
                {routeAlts && routeAlts.length > 1 && (
                  <div>
                    <div className="row-label">Routes</div>
                    <div className="recents" style={{ marginTop: 0 }}>
                      {routeAlts.map((alt, i) => (
                        <button
                          key={i}
                          className={`chip${i === chosenAlt ? ' chip-on' : ''}`}
                          onClick={() => handleChooseAlt(i)}
                          disabled={loading}
                        >
                          {String.fromCharCode(65 + i)} · {dist(alt.distance_mi)} ·{' '}
                          {Math.floor(alt.duration_min / 60)}h{alt.duration_min % 60}
                        </button>
                      ))}
                    </div>
                    <div className="seg-hint">Times are driving only - charging stops get planned after you pick.</div>
                  </div>
                )}
              </div>

              {/* The trip as a timeline - a route is a sequence along one
                  line, so the rail is information, not decoration. */}
              <div className="itin">
                <div className="leg">
                  <span className="pin-dot pin-dot-start" />
                  <div>
                    <div className="t">Leave with {batteryPct}%</div>
                    <div className="d">{origin?.label.split(',')[0]}</div>
                  </div>
                </div>
                {plan.stops.map((s, i) => (
                  <div className="leg" key={i}>
                    <span
                      className={
                        s.is_waypoint ? 'pin-dot pin-dot-wp' : s.is_supercharger ? 'pin-dot pin-dot-sc' : 'pin-dot pin-dot-ccs'
                      }
                    />
                    <div>
                      {s.leg_drive_min != null && s.leg_distance_mi != null && (
                        <div className="leg-hop">
                          ↓ {dist(s.leg_distance_mi)} · {driveTime(s.leg_drive_min)}
                        </div>
                      )}
                      <div className="t">{s.title}</div>
                      <div className="d">
                        {s.is_waypoint
                          ? `your stop · pass through with ${s.arrive_pct}%`
                          : `${s.network} · arrive ${s.arrive_pct}% → charge to ${s.charge_to_pct}%${
                              s.charge_time_min ? ` · ${s.charge_time_min} min` : ''
                            }`}
                        {!s.reachable && ' · may not be reachable, double-check before you leave'}
                      </div>
                    </div>
                    <div className="leg-actions">
                      <button className="link-btn" onClick={() => handleShareStop(s)}>
                        share
                      </button>
                      {s.id != null && (
                        <button className="link-btn" onClick={() => handleSkipStop(s.id)} disabled={loading}>
                          skip
                        </button>
                      )}
                    </div>
                  </div>
                ))}
                <div className="leg">
                  <span className="pin-dot pin-dot-end" />
                  <div>
                    {plan.last_leg_drive_min != null && plan.last_leg_distance_mi != null && (
                      <div className="leg-hop">
                        ↓ {dist(plan.last_leg_distance_mi)} · {driveTime(plan.last_leg_drive_min)}
                      </div>
                    )}
                    <div className="t">
                      {plan.arrival_pct < 0 ? "Won't make it as planned" : `Arrive at ${plan.arrival_pct}%`}
                    </div>
                    <div className="d">{destination?.label.split(',')[0]}</div>
                  </div>
                </div>
              </div>

              <button className="link-btn" style={{ marginLeft: 0 }} onClick={() => setShowTripCard(true)}>
                trip card (for the drive) →
              </button>
            </>
          )}

          <a className="accuracy-link" href="#accuracy">
            Accuracy record →
          </a>
        </aside>
      </div>
      {showCarSetup && (
        <CarSetup
          currentRangeMi={fullRangeMi}
          units={units}
          onSave={handleCarSetupSave}
          onClose={() => setShowCarSetup(false)}
        />
      )}
      {pendingTrip && (
        <TripFeedback pending={pendingTrip} onLog={handleLogTripResult} onDismiss={handleDismissPendingTrip} />
      )}
      {shareMsg && <div className="share-toast">{shareMsg}</div>}
      {showTripCard && plan && origin && destination && (
        <TripCard
          plan={plan}
          origin={origin}
          destination={destination}
          batteryPct={batteryPct}
          units={units}
          onClose={() => setShowTripCard(false)}
        />
      )}
    </div>
  )
}

export default App
