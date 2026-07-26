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
import { fetchRoutes, planTrip } from './api'
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
  loadFullRangeMi,
  loadRecentTrips,
  loadUnits,
  logRangeHistory,
  logTripResult,
  saveFullRangeMi,
  savePendingTrip,
  saveRecentTrip,
  saveUnits,
  shouldPromptForPendingTrip,
  type PendingTrip,
  type RecentTrip,
} from './storage'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'https://leeway-api.onrender.com'

export const MI_TO_KM = 1.609344

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
  { value: 'avoid_quick', label: '+3 min max' },
  { value: 'avoid_hard', label: '+10 min max' },
]

// First-visit demo trip - shown pre-filled so the value is visible before
// anyone types anything. Real coordinates (Culver City -> SF Mission), not
// geocoded on load, so this renders instantly even before ORS is configured.
const DEMO_ORIGIN: GeocodeResult = { label: 'Culver City, Los Angeles', lat: 34.0211, lon: -118.3965 }
const DEMO_DESTINATION: GeocodeResult = { label: 'Mission District, San Francisco', lat: 37.7599, lon: -122.4194 }

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

  const [apiStatus, setApiStatus] = useState<'checking' | 'ok' | 'down'>('checking')
  const [units, setUnits] = useState<Units>(() => loadUnits())
  const [origin, setOrigin] = useState<GeocodeResult | null>(DEMO_ORIGIN)
  const [destination, setDestination] = useState<GeocodeResult | null>(DEMO_DESTINATION)
  const [batteryPct, setBatteryPct] = useState(68)
  const [fullRangeMi, setFullRangeMi] = useState<number>(() => loadFullRangeMi() ?? 205)
  const [stopMode, setStopMode] = useState<StopMode>('fewest_stops')
  const [chargerFilter, setChargerFilter] = useState<ChargerFilter>('all')
  const [safetyMode, setSafetyMode] = useState<SafetyMode>('flag_only')
  const [avoidTolls, setAvoidTolls] = useState(false)
  const [avoidHighways, setAvoidHighways] = useState(false)
  const [showOptions, setShowOptions] = useState(false)
  const [chargeToPct, setChargeToPct] = useState(80)
  const [reservePct, setReservePct] = useState(15)
  const [arrivalTargetPct, setArrivalTargetPct] = useState(0)
  const [passengers, setPassengers] = useState(0)
  const [suitcases, setSuitcases] = useState(0)
  const [tempOverrideOn, setTempOverrideOn] = useState(false)
  const [tempOverrideF, setTempOverrideF] = useState(70)
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCarSetup, setShowCarSetup] = useState(false)
  const [recentTrips, setRecentTrips] = useState<RecentTrip[]>(() => loadRecentTrips())
  const [excludedStationIds, setExcludedStationIds] = useState<number[]>([])
  const [waypoints, setWaypoints] = useState<Waypoint[]>([])
  const [routeAlts, setRouteAlts] = useState<RouteAlt[] | null>(null)
  const [chosenAlt, setChosenAlt] = useState(0)
  const [pickingStop, setPickingStop] = useState(false)
  const [shareMsg, setShareMsg] = useState<string | null>(null)
  const [showTripCard, setShowTripCard] = useState(false)
  const [pendingTrip, setPendingTrip] = useState<PendingTrip | null>(() => shouldPromptForPendingTrip())

  // All numbers live in miles internally; only the display converts.
  const dist = (mi: number) => `${Math.round(units === 'km' ? mi * MI_TO_KM : mi)} ${units}`
  const tempDisplay = (f: number) => (units === 'km' ? Math.round(((f - 32) * 5) / 9) : Math.round(f))
  const tempFromDisplay = (v: number) => (units === 'km' ? (v * 9) / 5 + 32 : v)

  function toggleUnits() {
    const next = units === 'mi' ? 'km' : 'mi'
    setUnits(next)
    saveUnits(next)
  }

  useEffect(() => {
    if (!mapContainer.current) return
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://tiles.openfreemap.org/styles/liberty',
      center: [-120.5, 36.2],
      zoom: 5.6,
      // Collapses the attribution to an (i) button - the full line wrapped
      // to two rows on a 320px screen and covered a third of the map.
      attributionControl: { compact: true },
    })
    // MapLibre leaves the compact attribution expanded until first toggle;
    // start it collapsed, the (i) button re-opens it.
    map.once('load', () => {
      map.getContainer().querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show')
    })
    mapRef.current = map
    return () => map.remove()
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    map.getCanvas().style.cursor = pickingStop ? 'crosshair' : ''
    if (!pickingStop) return

    const onClick = (e: maplibregl.MapMouseEvent) => {
      const next = [...waypoints, { lat: e.lngLat.lat, lon: e.lngLat.lng, title: `Map stop ${waypoints.length + 1}` }]
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
          map.addLayer(
            {
              id: `alt-line-${i}`,
              type: 'line',
              source: `alt-${i}`,
              paint: { 'line-color': '#8b9083', 'line-width': 3, 'line-dasharray': [2, 2] },
            },
            'route-line',
          )
        }
      })
    }
    if (map.isStyleLoaded()) draw()
    else map.once('load', draw)
  }, [routeAlts])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !plan) return

    const drawRoute = () => {
      const geojson = {
        type: 'Feature' as const,
        properties: {},
        geometry: { type: 'LineString' as const, coordinates: plan.geometry },
      }
      const existing = map.getSource('route') as maplibregl.GeoJSONSource | undefined
      if (existing) {
        existing.setData(geojson)
      } else {
        map.addSource('route', { type: 'geojson', data: geojson })
        map.addLayer({
          id: 'route-line',
          type: 'line',
          source: 'route',
          paint: { 'line-color': '#2f2e2b', 'line-width': 5 },
        })
      }

      markersRef.current.forEach((m) => m.remove())
      markersRef.current = []

      if (origin) {
        markersRef.current.push(
          new maplibregl.Marker({ color: '#0b0b0b' }).setLngLat([origin.lon, origin.lat]).addTo(map),
        )
      }
      if (destination) {
        markersRef.current.push(
          new maplibregl.Marker({ color: '#0b0b0b' }).setLngLat([destination.lon, destination.lat]).addTo(map),
        )
      }

      // One shared popup: hover previews it, tap/click pins it (mobile has
      // no hover). While pinned it accepts pointer events so the maps link
      // is clickable; as a hover preview it passes them through so it can't
      // block other markers. Content is DOM-built - OCM titles must not be
      // able to inject markup.
      const popup = new maplibregl.Popup({ closeButton: false, offset: 16, maxWidth: '270px' })
      let pinned = false
      popup.on('close', () => {
        pinned = false
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
        const show = (pin: boolean) => {
          popup.setLngLat([lon, lat]).setDOMContent(build()).addTo(map)
          if (pin) popup.addClassName('popup-pinned')
          else popup.removeClassName('popup-pinned')
        }
        el.addEventListener('mouseenter', () => {
          if (!pinned) show(false)
        })
        el.addEventListener('mouseleave', () => {
          if (!pinned) popup.remove()
        })
        el.addEventListener('click', (e) => {
          e.stopPropagation()
          pinned = true
          show(true)
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
      map.fitBounds(bounds, { padding: 60, duration: 500 })
    }

    if (map.isStyleLoaded()) drawRoute()
    else map.once('load', drawRoute)
  }, [plan, origin, destination])

  async function runPlan(overrides: {
    excludedStationIds: number[]
    waypoints: Waypoint[]
    via?: { lat: number; lon: number } | null
  }) {
    if (!origin || !destination) {
      setError('Pick both a start and a destination from the dropdown.')
      return
    }
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
    try {
      const allWaypoints = [...overrides.waypoints]
      if (overrides.via) {
        allWaypoints.push({ ...overrides.via, title: 'via', hidden: true })
      }
      const result = await planTrip({
        origin: { lat: origin.lat, lon: origin.lon },
        destination: { lat: destination.lat, lon: destination.lon },
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
        excludedStationIds: overrides.excludedStationIds,
        waypoints: allWaypoints,
      })
      setPlan(result)
      saveRecentTrip({ origin, destination })
      setRecentTrips(loadRecentTrips())
      savePendingTrip({
        originLabel: origin.label,
        destinationLabel: destination.label,
        predictedArrivalPct: result.arrival_pct,
        feasible: result.feasible,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong planning that trip.')
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
    runPlan({ excludedStationIds, waypoints: next, via: currentVia() })
  }

  function handleAddVoiceStop(lat: number, lon: number, title: string) {
    // A voice-found stop is just another waypoint - same mechanism as
    // clicking the map, not a separate insertion path.
    const next = [...waypoints, { lat, lon, title }]
    setWaypoints(next)
    runPlan({ excludedStationIds, waypoints: next, via: currentVia() })
  }

  async function handleCompareRoutes() {
    if (!origin || !destination) return
    try {
      const alts = await fetchRoutes(
        { lat: origin.lat, lon: origin.lon },
        { lat: destination.lat, lon: destination.lon },
        avoidTolls,
        avoidHighways,
      )
      setRouteAlts(alts)
      setChosenAlt(0)
      if (alts.length === 1) setShareMsg('No genuinely different corridor exists for this trip')
      setTimeout(() => setShareMsg(null), 3000)
    } catch {
      setShareMsg('Could not fetch other corridors right now')
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
  }

  function handleDismissPendingTrip() {
    clearPendingTrip()
    setPendingTrip(null)
  }

  const rangeDisplay = Math.round(units === 'km' ? fullRangeMi * MI_TO_KM : fullRangeMi)

  // Tells you the collapsed options section is hiding something you set.
  const changedOptions = [
    stopMode !== 'fewest_stops',
    chargerFilter !== 'all',
    safetyMode !== 'flag_only',
    avoidTolls,
    avoidHighways,
    waypoints.length > 0,
    chargeToPct !== 80,
    reservePct !== 15,
    arrivalTargetPct > 0,
    passengers > 0,
    suitcases > 0,
    tempOverrideOn,
  ].filter(Boolean).length

  return (
    <div className="app-root">
      <header className="app-header">
        <span className="logo-dot" />
        <span className="logo-word">Leeway</span>
        <span className="tag">the second opinion before you leave</span>
        <button className="unit-toggle" onClick={toggleUnits} title="Switch units">
          {units === 'mi' ? 'mi · °F' : 'km · °C'}
        </button>
        <span className={`api-chip api-chip--${apiStatus}`}>
          backend: {apiStatus === 'checking' ? 'checking…' : apiStatus === 'ok' ? 'connected' : 'unreachable'}
        </span>
      </header>
      <div className="app-body">
        <aside className="panel panel-left">
          <div className="field-group">
            <LocationInput placeholder="Start" dotClass="dot-a" value={origin} onChange={setOrigin} />
            <LocationInput placeholder="Destination" dotClass="dot-b" value={destination} onChange={setDestination} />
          </div>

          {recentTrips.length > 0 && (
            <div className="recents">
              Recent:{' '}
              {recentTrips.map((t, i) => (
                <button key={i} className="chip" onClick={() => pickRecentTrip(t)}>
                  {t.origin.label.split(',')[0]} → {t.destination.label.split(',')[0]}
                </button>
              ))}
            </div>
          )}

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
            <button className="link-btn" style={{ marginLeft: 0 }} onClick={() => setShowOptions((v) => !v)}>
              {showOptions
                ? 'fewer options ▾'
                : changedOptions > 0
                  ? `more options (${changedOptions} set) ▸`
                  : 'more options ▸'}
            </button>
            {showOptions && (
              <div className="options">
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
            <div className="row-label">Your stops along the way</div>
            {waypoints.length > 0 && (
              <div className="recents" style={{ marginTop: 0, marginBottom: 6 }}>
                {waypoints.map((w, i) => (
                  <button key={i} className="chip" onClick={() => removeWaypoint(i)} title="Remove this stop">
                    {w.title} ✕
                  </button>
                ))}
              </div>
            )}
            {waypoints.length < 5 && (
              <button className="link-btn" style={{ marginLeft: 0 }} onClick={() => setPickingStop((v) => !v)}>
                {pickingStop ? 'click the map to add it…' : '+ add a stop (click the map)'}
              </button>
            )}
          </div>

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
                ? 'Unprotected lefts and rail crossings get flagged, route unchanged.'
                : `Reroutes around them when the detour adds ${safetyMode === 'avoid_quick' ? '3' : '10'} minutes or less.`}
            </div>
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
                    <span className="range-unit">{units === 'km' ? '°C' : '°F'}</span>
                  </div>
                )}
                {!tempOverrideOn && (
                  <div className="seg-hint">Live weather at the route midpoint is used unless you set your own.</div>
                )}
          </div>
              </div>
            )}
          </div>

          <button className="plan-btn" onClick={handlePlan} disabled={loading}>
            {loading ? 'Planning…' : 'Plan this trip'}
          </button>

          {error && <div className="error-box">{error}</div>}
        </aside>

        <div className="map-wrap">
          <div ref={mapContainer} className="map" />
          {plan && origin && destination && (
            <VoiceBar origin={origin} destination={destination} onAddStop={handleAddVoiceStop} />
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

          {plan && (
            <>
              <div className={`verdict ${plan.feasible ? 'verdict-ok' : 'verdict-bad'}`}>
                <div className="status">
                  {plan.feasible
                    ? '✓ Makeable with your reserve'
                    : plan.rate_limited
                      ? '⚠ Planning got interrupted - plan again in a minute'
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
                </div>
                {plan.weather && <div className="sub">Range adjusted for {plan.weather.summary}</div>}
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
                    compare other corridors →
                  </button>
                )}
                {routeAlts && routeAlts.length > 1 && (
                  <div>
                    <div className="row-label">Corridors</div>
                    <div className="recents" style={{ marginTop: 0 }}>
                      {routeAlts.map((alt, i) => (
                        <button
                          key={i}
                          className={`chip${i === chosenAlt ? ' chip-on' : ''}`}
                          onClick={() => handleChooseAlt(i)}
                          disabled={loading}
                        >
                          {i === 0 ? 'direct' : `alt ${i}`} · {dist(alt.distance_mi)} ·{' '}
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
