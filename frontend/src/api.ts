import type { GeocodeResult, LatLon, PlanResponse, StopMode, VoiceSearchResponse } from './types'

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
  forcedStop?: LatLon | null
  forcedStopTitle?: string
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
      forced_stop: params.forcedStop ?? null,
      forced_stop_title: params.forcedStopTitle ?? 'Your chosen stop',
    }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `plan failed: ${res.status}`)
  }
  return res.json()
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
