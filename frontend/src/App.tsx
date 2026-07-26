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
import { planTrip } from './api'
import type { ChargingStop, GeocodeResult, LatLon, PlanResponse, StopMode } from './types'
import {
  clearPendingTrip,
  loadFullRangeMi,
  loadRecentTrips,
  logRangeHistory,
  logTripResult,
  saveFullRangeMi,
  savePendingTrip,
  saveRecentTrip,
  shouldPromptForPendingTrip,
  type PendingTrip,
  type RecentTrip,
} from './storage'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'https://leeway-api.onrender.com'

const STOP_MODES: { value: StopMode; label: string }[] = [
  { value: 'fewest_stops', label: 'Fewest stops' },
  { value: 'fastest_trip', label: 'Fastest trip' },
  { value: 'best_amenities', label: 'Best amenities' },
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
  const [origin, setOrigin] = useState<GeocodeResult | null>(DEMO_ORIGIN)
  const [destination, setDestination] = useState<GeocodeResult | null>(DEMO_DESTINATION)
  const [batteryPct, setBatteryPct] = useState(68)
  const [fullRangeMi, setFullRangeMi] = useState<number>(() => loadFullRangeMi() ?? 205)
  const [stopMode, setStopMode] = useState<StopMode>('fewest_stops')
  const [avoidTolls, setAvoidTolls] = useState(false)
  const [avoidHighways, setAvoidHighways] = useState(false)
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCarSetup, setShowCarSetup] = useState(false)
  const [recentTrips, setRecentTrips] = useState<RecentTrip[]>(() => loadRecentTrips())
  const [excludedStationIds, setExcludedStationIds] = useState<number[]>([])
  const [forcedStop, setForcedStop] = useState<LatLon | null>(null)
  const [forcedStopTitle, setForcedStopTitle] = useState('Your chosen stop')
  const [pickingStop, setPickingStop] = useState(false)
  const [shareMsg, setShareMsg] = useState<string | null>(null)
  const [showTripCard, setShowTripCard] = useState(false)
  const [pendingTrip, setPendingTrip] = useState<PendingTrip | null>(() => shouldPromptForPendingTrip())

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
      const chosen = { lat: e.lngLat.lat, lon: e.lngLat.lng }
      setForcedStop(chosen)
      setForcedStopTitle('Your chosen stop')
      setPickingStop(false)
      runPlan({ excludedStationIds, forcedStop: chosen, forcedStopTitle: 'Your chosen stop' })
    }
    map.on('click', onClick)
    return () => {
      map.off('click', onClick)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickingStop])

  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then((r) => (r.ok ? setApiStatus('ok') : setApiStatus('down')))
      .catch(() => setApiStatus('down'))
  }, [])

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
      // no hover), clicking the map closes it. Content is built with DOM
      // nodes, not an HTML string - station titles come from OCM and
      // shouldn't be able to inject markup.
      const popup = new maplibregl.Popup({ closeButton: false, offset: 16, maxWidth: '260px' })
      const attachPopup = (el: HTMLElement, lon: number, lat: number, lines: [string, ...string[]]) => {
        const show = () => {
          const box = document.createElement('div')
          const title = document.createElement('div')
          title.className = 'pop-title'
          title.textContent = lines[0]
          box.appendChild(title)
          for (const line of lines.slice(1)) {
            const sub = document.createElement('div')
            sub.className = 'pop-sub'
            sub.textContent = line
            box.appendChild(sub)
          }
          popup.setLngLat([lon, lat]).setDOMContent(box).addTo(map)
        }
        el.addEventListener('mouseenter', show)
        el.addEventListener('mouseleave', () => popup.remove())
        el.addEventListener('click', (e) => {
          e.stopPropagation()
          show()
        })
      }

      for (const stop of plan.stops) {
        const el = document.createElement('div')
        el.className = stop.is_supercharger ? 'pin pin-supercharger' : 'pin pin-ccs'
        attachPopup(el, stop.lon, stop.lat, [
          stop.title,
          `${stop.network}${stop.max_kw ? ` · up to ${Math.round(stop.max_kw)} kW` : ''}`,
          `Arrive ${stop.arrive_pct}% → charge to ${stop.charge_to_pct}%${
            stop.charge_time_min ? ` (~${stop.charge_time_min} min)` : ''
          }`,
        ])
        markersRef.current.push(new maplibregl.Marker({ element: el }).setLngLat([stop.lon, stop.lat]).addTo(map))
      }
      for (const flag of plan.safety_flags) {
        if (flag.lat == null || flag.lon == null) continue
        const el = document.createElement('div')
        el.className = 'pin-hazard'
        attachPopup(el, flag.lon, flag.lat, ['Heads up', flag.description])
        markersRef.current.push(new maplibregl.Marker({ element: el }).setLngLat([flag.lon, flag.lat]).addTo(map))
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

  async function runPlan(overrides: { excludedStationIds: number[]; forcedStop: LatLon | null; forcedStopTitle?: string }) {
    if (!origin || !destination) {
      setError('Pick both a start and a destination from the dropdown.')
      return
    }
    // A cleared number input reads as 0, which the backend (rightly) refuses.
    if (!Number.isFinite(fullRangeMi) || fullRangeMi < 50 || fullRangeMi > 600) {
      setError("Enter your car's real 100% range first (between 50 and 600 miles).")
      return
    }
    setLoading(true)
    setError(null)
    try {
      const result = await planTrip({
        origin: { lat: origin.lat, lon: origin.lon },
        destination: { lat: destination.lat, lon: destination.lon },
        batteryPct,
        fullRangeMi,
        stopMode,
        avoidTolls,
        avoidHighways,
        excludedStationIds: overrides.excludedStationIds,
        forcedStop: overrides.forcedStop,
        forcedStopTitle: overrides.forcedStopTitle ?? forcedStopTitle,
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
    setForcedStop(null)
    runPlan({ excludedStationIds: [], forcedStop: null })
  }

  function handleSkipStop(id: number | null) {
    if (id == null) return
    const next = [...excludedStationIds, id]
    setExcludedStationIds(next)
    runPlan({ excludedStationIds: next, forcedStop })
  }

  function clearForcedStop() {
    setForcedStop(null)
    runPlan({ excludedStationIds, forcedStop: null })
  }

  function handleAddVoiceStop(lat: number, lon: number, title: string) {
    // A voice-found stop is just another forced waypoint - reuses the exact
    // same mandatory-stop mechanism as "insist on a stop (click the map)",
    // not a new insertion path.
    const chosen = { lat, lon }
    setForcedStop(chosen)
    setForcedStopTitle(title)
    runPlan({ excludedStationIds, forcedStop: chosen, forcedStopTitle: title })
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

  return (
    <div className="app-root">
      <header className="app-header">
        <span className="logo-dot" />
        <span className="logo-word">Leeway</span>
        <span className="tag">the second opinion before you leave</span>
        <span className={`api-chip api-chip--${apiStatus}`}>
          backend: {apiStatus === 'checking' ? 'checking…' : apiStatus === 'ok' ? 'connected' : 'unreachable'}
        </span>
      </header>
      <div className="app-body">
        <aside className="panel">
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
                min={50}
                max={400}
                value={fullRangeMi}
                onChange={(e) => setFullRangeMi(Number(e.target.value))}
              />
              <span className="range-unit">mi</span>
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
            {forcedStop ? (
              <div className="recents" style={{ marginTop: 8 }}>
                Forced stop set on the map ·{' '}
                <button className="link-btn" style={{ marginLeft: 0 }} onClick={clearForcedStop}>
                  clear
                </button>
              </div>
            ) : (
              <button
                className="link-btn"
                style={{ marginTop: 8, marginLeft: 0 }}
                onClick={() => setPickingStop((v) => !v)}
              >
                {pickingStop ? 'click the map to pick a stop…' : 'insist on a stop (click the map)'}
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
          </div>

          <button className="plan-btn" onClick={handlePlan} disabled={loading}>
            {loading ? 'Planning…' : 'Plan this trip'}
          </button>

          {error && <div className="error-box">{error}</div>}

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
                  {plan.arrival_pct < 0
                    ? `About ${Math.abs(Math.round(plan.leeway_mi))} mi short`
                    : `Arrive at ${plan.arrival_pct}%`}{' '}
                  <small>{plan.leeway_mi >= 0 ? `${plan.leeway_mi} mi of leeway` : ''}</small>
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
                  {plan.distance_mi} mi · {plan.stops.length} stop{plan.stops.length === 1 ? '' : 's'} ·{' '}
                  {Math.round(plan.duration_min / 60)} h {plan.duration_min % 60} min total
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

              <div className="itin">
                {plan.stops.map((s, i) => (
                  <div className="leg" key={i}>
                    <span className={s.is_supercharger ? 'pin-dot pin-dot-sc' : 'pin-dot pin-dot-ccs'} />
                    <div>
                      <div className="t">{s.title}</div>
                      <div className="d">
                        {s.network} · arrive {s.arrive_pct}% → charge to {s.charge_to_pct}%
                        {s.charge_time_min ? ` · ${s.charge_time_min} min` : ''}
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
        <div className="map-wrap">
          <div ref={mapContainer} className="map" />
          {plan && origin && destination && (
            <VoiceBar origin={origin} destination={destination} onAddStop={handleAddVoiceStop} />
          )}
        </div>
      </div>
      {showCarSetup && (
        <CarSetup currentRangeMi={fullRangeMi} onSave={handleCarSetupSave} onClose={() => setShowCarSetup(false)} />
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
          onClose={() => setShowTripCard(false)}
        />
      )}
    </div>
  )
}

export default App
