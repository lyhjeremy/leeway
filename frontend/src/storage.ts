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

// Stage 5: trip logging. Everything here is local to this device - there's
// no shared backend yet, so "the public accuracy page" for now means "your
// own accuracy history on this browser," not a crowd-sourced record. See
// AccuracyPage.tsx for the honest framing of that gap.
const PENDING_TRIP_KEY = 'leeway.pendingTrip'
const LOGGED_TRIPS_KEY = 'leeway.loggedTrips'
const RANGE_HISTORY_KEY = 'leeway.rangeHistory'
const JUST_PLANNED_SESSION_KEY = 'leeway.justPlannedId'

export interface PendingTrip {
  id: string
  plannedAt: number
  originLabel: string
  destinationLabel: string
  predictedArrivalPct: number
  feasible: boolean
}

export function savePendingTrip(trip: Omit<PendingTrip, 'id' | 'plannedAt'>) {
  const id = `${Date.now()}`
  const full: PendingTrip = { ...trip, id, plannedAt: Date.now() }
  localStorage.setItem(PENDING_TRIP_KEY, JSON.stringify(full))
  // Marks this trip as "just planned in the current tab session" so the
  // "how did it go?" prompt doesn't fire immediately on the same visit -
  // it's meant to greet you on the *next* visit, per the product plan.
  sessionStorage.setItem(JUST_PLANNED_SESSION_KEY, id)
}

export function loadPendingTrip(): PendingTrip | null {
  try {
    return JSON.parse(localStorage.getItem(PENDING_TRIP_KEY) ?? 'null')
  } catch {
    return null
  }
}

export function shouldPromptForPendingTrip(): PendingTrip | null {
  const pending = loadPendingTrip()
  if (!pending) return null
  const justPlannedId = sessionStorage.getItem(JUST_PLANNED_SESSION_KEY)
  return justPlannedId === pending.id ? null : pending
}

export function clearPendingTrip() {
  localStorage.removeItem(PENDING_TRIP_KEY)
}

export interface LoggedTrip {
  loggedAt: number
  originLabel: string
  destinationLabel: string
  predictedArrivalPct: number
  actualArrivalPct: number
}

export function loadLoggedTrips(): LoggedTrip[] {
  try {
    return JSON.parse(localStorage.getItem(LOGGED_TRIPS_KEY) ?? '[]')
  } catch {
    return []
  }
}

export function logTripResult(pending: PendingTrip, actualArrivalPct: number) {
  const next: LoggedTrip = {
    loggedAt: Date.now(),
    originLabel: pending.originLabel,
    destinationLabel: pending.destinationLabel,
    predictedArrivalPct: pending.predictedArrivalPct,
    actualArrivalPct,
  }
  const existing = loadLoggedTrips()
  localStorage.setItem(LOGGED_TRIPS_KEY, JSON.stringify([next, ...existing]))
  clearPendingTrip()
}

export interface RangeHistoryEntry {
  date: number
  fullRangeMi: number
}

export function loadRangeHistory(): RangeHistoryEntry[] {
  try {
    return JSON.parse(localStorage.getItem(RANGE_HISTORY_KEY) ?? '[]')
  } catch {
    return []
  }
}

export function logRangeHistory(fullRangeMi: number) {
  const existing = loadRangeHistory()
  localStorage.setItem(RANGE_HISTORY_KEY, JSON.stringify([...existing, { date: Date.now(), fullRangeMi }]))
}
