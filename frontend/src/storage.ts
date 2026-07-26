import type { GeocodeResult } from './types'

const RANGE_KEY = 'leeway.fullRangeMi'
const TRIPS_KEY = 'leeway.recentTrips'
const MAX_RECENT = 5

export function loadFullRangeMi(): number | null {
  const raw = localStorage.getItem(RANGE_KEY)
  return raw ? Number(raw) : null
}

export function saveFullRangeMi(mi: number) {
  localStorage.setItem(RANGE_KEY, String(mi))
}

export interface RecentTrip {
  origin: GeocodeResult
  destination: GeocodeResult
}

export function loadRecentTrips(): RecentTrip[] {
  try {
    return JSON.parse(localStorage.getItem(TRIPS_KEY) ?? '[]')
  } catch {
    return []
  }
}

export function saveRecentTrip(trip: RecentTrip) {
  const existing = loadRecentTrips().filter(
    (t) => !(t.origin.label === trip.origin.label && t.destination.label === trip.destination.label),
  )
  const next = [trip, ...existing].slice(0, MAX_RECENT)
  localStorage.setItem(TRIPS_KEY, JSON.stringify(next))
}
