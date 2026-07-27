import type { GeocodeResult } from './types'

// Chrome with cookies blocked (and some private-browsing modes) throws a
// SecurityError on *any* localStorage access, including reads - without
// these guards the whole app white-screens before first paint. Verified by
// stress test, not hypothetical.
function storageGet(store: 'local' | 'session', key: string): string | null {
  try {
    return (store === 'local' ? window.localStorage : window.sessionStorage).getItem(key)
  } catch {
    return null
  }
}

function storageSet(store: 'local' | 'session', key: string, value: string) {
  try {
    ;(store === 'local' ? window.localStorage : window.sessionStorage).setItem(key, value)
  } catch {
    // storage unavailable - the app still works, it just won't remember
  }
}

function storageRemove(store: 'local' | 'session', key: string) {
  try {
    ;(store === 'local' ? window.localStorage : window.sessionStorage).removeItem(key)
  } catch {
    // ignore
  }
}

const RANGE_KEY = 'leeway.fullRangeMi'
const TRIPS_KEY = 'leeway.recentTrips'
const UNITS_KEY = 'leeway.units' // legacy combined pref, still read as a fallback
const DIST_UNIT_KEY = 'leeway.distUnit'
const TEMP_UNIT_KEY = 'leeway.tempUnit'
const MAX_RECENT = 5

export function loadDistUnit(): 'mi' | 'km' {
  const v = storageGet('local', DIST_UNIT_KEY)
  if (v === 'mi' || v === 'km') return v
  return storageGet('local', UNITS_KEY) === 'km' ? 'km' : 'mi'
}

export function saveDistUnit(unit: 'mi' | 'km') {
  storageSet('local', DIST_UNIT_KEY, unit)
}

export function loadTempUnit(): 'F' | 'C' {
  const v = storageGet('local', TEMP_UNIT_KEY)
  if (v === 'F' || v === 'C') return v
  // someone who'd chosen km under the combined pref was seeing °C too
  return storageGet('local', UNITS_KEY) === 'km' ? 'C' : 'F'
}

export function saveTempUnit(unit: 'F' | 'C') {
  storageSet('local', TEMP_UNIT_KEY, unit)
}

const THEME_KEY = 'leeway.theme'

export function loadTheme(): 'light' | 'dark' | null {
  const t = storageGet('local', THEME_KEY)
  return t === 'dark' || t === 'light' ? t : null
}

export function saveTheme(theme: 'light' | 'dark') {
  storageSet('local', THEME_KEY, theme)
}

export function loadFullRangeMi(): number | null {
  const raw = storageGet('local', RANGE_KEY)
  return raw ? Number(raw) : null
}

export function saveFullRangeMi(mi: number) {
  storageSet('local', RANGE_KEY, String(mi))
}

export interface RecentTrip {
  origin: GeocodeResult
  destination: GeocodeResult
}

export function loadRecentTrips(): RecentTrip[] {
  try {
    return JSON.parse(storageGet('local', TRIPS_KEY) ?? '[]')
  } catch {
    return []
  }
}

export function saveRecentTrip(trip: RecentTrip) {
  const existing = loadRecentTrips().filter(
    (t) => !(t.origin.label === trip.origin.label && t.destination.label === trip.destination.label),
  )
  const next = [trip, ...existing].slice(0, MAX_RECENT)
  storageSet('local', TRIPS_KEY, JSON.stringify(next))
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
  // Start-of-trip battery %. Needed to turn a logged trip into a consumption
  // ratio (actual used / predicted used) for calibration; optional because
  // trips logged before this field existed don't have it.
  startBatteryPct?: number
}

export function savePendingTrip(trip: Omit<PendingTrip, 'id' | 'plannedAt'>) {
  const id = `${Date.now()}`
  const full: PendingTrip = { ...trip, id, plannedAt: Date.now() }
  storageSet('local', PENDING_TRIP_KEY, JSON.stringify(full))
  // Marks this trip as "just planned in the current tab session" so the
  // "how did it go?" prompt doesn't fire immediately on the same visit -
  // it's meant to greet you on the *next* visit, per the product plan.
  storageSet('session', JUST_PLANNED_SESSION_KEY, id)
}

export function loadPendingTrip(): PendingTrip | null {
  try {
    return JSON.parse(storageGet('local', PENDING_TRIP_KEY) ?? 'null')
  } catch {
    return null
  }
}

export function shouldPromptForPendingTrip(): PendingTrip | null {
  const pending = loadPendingTrip()
  if (!pending) return null
  const justPlannedId = storageGet('session', JUST_PLANNED_SESSION_KEY)
  return justPlannedId === pending.id ? null : pending
}

export function clearPendingTrip() {
  storageRemove('local', PENDING_TRIP_KEY)
}

export interface LoggedTrip {
  loggedAt: number
  originLabel: string
  destinationLabel: string
  predictedArrivalPct: number
  actualArrivalPct: number
  startBatteryPct?: number
}

export function loadLoggedTrips(): LoggedTrip[] {
  try {
    return JSON.parse(storageGet('local', LOGGED_TRIPS_KEY) ?? '[]')
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
    startBatteryPct: pending.startBatteryPct,
  }
  const existing = loadLoggedTrips()
  storageSet('local', LOGGED_TRIPS_KEY, JSON.stringify([next, ...existing]))
  clearPendingTrip()
}

// Stage 5's actual feedback loop: turn the logged predicted-vs-actual record
// into a consumption multiplier the planner applies to future estimates.
// Computed here because the logs live in this browser - there is no backend
// database to learn from yet.
export interface Calibration {
  factor: number // consumption multiplier sent to the planner
  tripsUsed: number
  observedRatio: number // raw recency-weighted actual/predicted consumption
}

const CALIBRATION_MIN_TRIPS = 3
const CALIBRATION_MIN_PREDICTED_USED_PCT = 10 // tiny trips are all noise
const CALIBRATION_MAX_TRIPS = 10
const CALIBRATION_RECENCY_DECAY = 0.8

export function computeCalibration(): Calibration | null {
  // Only trips that recorded a start % can yield a consumption ratio, and
  // only trips that used a meaningful slice of battery say anything real.
  const usable = loadLoggedTrips().filter(
    (t) =>
      typeof t.startBatteryPct === 'number' &&
      t.startBatteryPct - t.predictedArrivalPct >= CALIBRATION_MIN_PREDICTED_USED_PCT &&
      t.startBatteryPct - t.actualArrivalPct > 0,
  )
  if (usable.length < CALIBRATION_MIN_TRIPS) return null

  // Logs are stored newest-first; weight recent trips more (battery health
  // and driving habits drift, last summer's trips matter less than last week's)
  let num = 0
  let den = 0
  let w = 1
  for (const t of usable.slice(0, CALIBRATION_MAX_TRIPS)) {
    const predictedUsed = t.startBatteryPct! - t.predictedArrivalPct
    const actualUsed = t.startBatteryPct! - t.actualArrivalPct
    // A single wild log entry (typo, forgot a detour) can't dominate
    const ratio = Math.min(2, Math.max(0.5, actualUsed / predictedUsed))
    num += ratio * w
    den += w
    w *= CALIBRATION_RECENCY_DECAY
  }
  const observed = num / den

  // Safe-side asymmetry, the product's core promise: if the car does WORSE
  // than predicted, apply the full correction; if it does BETTER, apply only
  // half and never below 0.9 - optimistic corrections are the dangerous kind.
  const factor = observed >= 1 ? Math.min(1.5, observed) : Math.max(0.9, (1 + observed) / 2)
  if (Math.abs(factor - 1) < 0.02) return null // within noise - say nothing

  return {
    factor: Math.round(factor * 1000) / 1000,
    tripsUsed: Math.min(usable.length, CALIBRATION_MAX_TRIPS),
    observedRatio: Math.round(observed * 1000) / 1000,
  }
}

export interface RangeHistoryEntry {
  date: number
  fullRangeMi: number
}

export function loadRangeHistory(): RangeHistoryEntry[] {
  try {
    return JSON.parse(storageGet('local', RANGE_HISTORY_KEY) ?? '[]')
  } catch {
    return []
  }
}

export function logRangeHistory(fullRangeMi: number) {
  const existing = loadRangeHistory()
  storageSet('local', RANGE_HISTORY_KEY, JSON.stringify([...existing, { date: Date.now(), fullRangeMi }]))
}
