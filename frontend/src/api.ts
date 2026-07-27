import type { ChargerFilter, GeocodeResult, LatLon, PlanResponse, RouteAlt, SafetyMode, StopMode, Units, VoiceSearchResponse, Waypoint } from './types'

export const API_BASE = import.meta.env.VITE_API_BASE ?? 'https://leeway-api.onrender.com'

export async function geocode(query: string): Promise<GeocodeResult[]> {
  const res = await fetch(`${API_BASE}/api/geocode?q=${encodeURIComponent(query)}`)
  if (!res.ok) throw new Error(`geocode failed: ${res.status}`)
  const data = await res.json()
  return data.results
}

export async function planTrip(params: {
  origin: LatLon
  destination: LatLon
  batteryPct: number
  fullRangeMi: number
  reservePct?: number
  reserveMi?: number
  chargeToPct?: number
  stopMode?: StopMode
  avoidTolls?: boolean
  avoidHighways?: boolean
  excludedStationIds?: number[]
  waypoints?: Waypoint[]
  safetyMode?: SafetyMode
  chargerFilter?: ChargerFilter
  arrivalTargetPct?: number
  passengers?: number
  suitcases?: number
  tempOverrideF?: number | null
  units?: Units
  tempUnit?: 'F' | 'C'
  hazardTypes?: string[]
  departureEpoch?: number | null
  maxStintMin?: number
  minChargerKw?: number
  preferredNetworks?: string[]
  avoidFerries?: boolean
}): Promise<PlanResponse> {
  const res = await fetch(`${API_BASE}/api/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      origin: params.origin,
      destination: params.destination,
      battery_pct: params.batteryPct,
      full_range_mi: params.fullRangeMi,
      reserve_pct: params.reservePct ?? 15,
      reserve_mi: params.reserveMi ?? 30,
      charge_to_pct: params.chargeToPct ?? 80,
      stop_mode: params.stopMode ?? 'fewest_stops',
      avoid_tolls: params.avoidTolls ?? false,
      avoid_highways: params.avoidHighways ?? false,
      excluded_station_ids: params.excludedStationIds ?? [],
      waypoints: params.waypoints ?? [],
      safety_mode: params.safetyMode ?? 'flag_only',
      charger_filter: params.chargerFilter ?? 'all',
      arrival_target_pct: params.arrivalTargetPct ?? 0,
      passengers: params.passengers ?? 0,
      suitcases: params.suitcases ?? 0,
      temp_override_f: params.tempOverrideF ?? null,
      units: params.units ?? 'mi',
      temp_unit: params.tempUnit ?? 'F',
      hazard_types: params.hazardTypes ?? ['unprotected_left', 'wide_crossing', 'rail_crossing', 'lane_closure'],
      departure_epoch: params.departureEpoch ?? null,
      max_stint_min: params.maxStintMin ?? 0,
      min_charger_kw: params.minChargerKw ?? 20,
      preferred_networks: params.preferredNetworks ?? [],
      avoid_ferries: params.avoidFerries ?? false,
    }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    // FastAPI validation errors send detail as a list of objects, not a string
    const detail = typeof body.detail === 'string' ? body.detail : null
    throw new Error(detail ?? `plan failed: ${res.status}`)
  }
  return res.json()
}

export async function fetchRoutes(
  origin: LatLon,
  destination: LatLon,
  avoidTolls: boolean,
  avoidHighways: boolean,
  avoidFerries = false,
): Promise<RouteAlt[]> {
  const res = await fetch(`${API_BASE}/api/routes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      origin,
      destination,
      avoid_tolls: avoidTolls,
      avoid_highways: avoidHighways,
      avoid_ferries: avoidFerries,
    }),
  })
  if (!res.ok) throw new Error(`routes failed: ${res.status}`)
  const data = await res.json()
  return data.routes
}

export async function voiceSearch(query: string, origin: LatLon, destination: LatLon): Promise<VoiceSearchResponse> {
  const res = await fetch(`${API_BASE}/api/voice-search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, origin, destination }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `voice search failed: ${res.status}`)
  }
  return res.json()
}
