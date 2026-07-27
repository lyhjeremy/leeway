// The calibration math is the one piece of frontend logic where a subtle bug
// breaks the product's core promise (errors land on the safe side), so it
// gets real tests. localStorage is stubbed - storage.ts already tolerates
// any storage weirdness, so a plain in-memory Map is enough.

import { beforeEach, describe, expect, it } from 'vitest'
import { computeCalibration, type LoggedTrip } from './storage'

const store = new Map<string, string>()

beforeEach(() => {
  store.clear()
  const fakeStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  }
  ;(globalThis as Record<string, unknown>).window = {
    localStorage: fakeStorage,
    sessionStorage: fakeStorage,
  }
})

function seedTrips(trips: Partial<LoggedTrip>[]) {
  const full = trips.map((t, i) => ({
    loggedAt: 1700000000000 - i * 86400000,
    originLabel: 'A',
    destinationLabel: 'B',
    predictedArrivalPct: 40,
    actualArrivalPct: 40,
    startBatteryPct: 90,
    ...t,
  }))
  store.set('leeway.loggedTrips', JSON.stringify(full))
}

describe('computeCalibration', () => {
  it('returns null with no logs', () => {
    expect(computeCalibration()).toBeNull()
  })

  it('returns null below 3 usable trips', () => {
    seedTrips([{ actualArrivalPct: 30 }, { actualArrivalPct: 30 }])
    expect(computeCalibration()).toBeNull()
  })

  it('ignores legacy logs without a start battery %', () => {
    seedTrips([
      { actualArrivalPct: 30, startBatteryPct: undefined },
      { actualArrivalPct: 30, startBatteryPct: undefined },
      { actualArrivalPct: 30, startBatteryPct: undefined },
    ])
    expect(computeCalibration()).toBeNull()
  })

  it('ignores trips that barely used the battery', () => {
    // predicted used = 5 points each: all noise, none usable
    seedTrips([
      { predictedArrivalPct: 85, actualArrivalPct: 80 },
      { predictedArrivalPct: 85, actualArrivalPct: 80 },
      { predictedArrivalPct: 85, actualArrivalPct: 80 },
    ])
    expect(computeCalibration()).toBeNull()
  })

  it('applies the full correction when the car runs hungrier than predicted', () => {
    // predicted 50 points used, actually used 60 -> ratio 1.2 on every trip
    seedTrips([
      { actualArrivalPct: 30 },
      { actualArrivalPct: 30 },
      { actualArrivalPct: 30 },
    ])
    const cal = computeCalibration()
    expect(cal).not.toBeNull()
    expect(cal!.factor).toBeCloseTo(1.2, 2)
    expect(cal!.tripsUsed).toBe(3)
  })

  it('halves and floors the correction when the car runs better than predicted', () => {
    // predicted 50 used, actually 40 -> ratio 0.8; optimistic corrections are
    // the dangerous kind, so factor = (1 + 0.8) / 2 = 0.9, never below 0.9
    seedTrips([
      { actualArrivalPct: 50 },
      { actualArrivalPct: 50 },
      { actualArrivalPct: 50 },
    ])
    const cal = computeCalibration()
    expect(cal).not.toBeNull()
    expect(cal!.factor).toBeCloseTo(0.9, 2)

    // Even a wildly better-than-predicted car can't push below the floor
    seedTrips([
      { actualArrivalPct: 65 },
      { actualArrivalPct: 65 },
      { actualArrivalPct: 65 },
    ])
    expect(computeCalibration()!.factor).toBeGreaterThanOrEqual(0.9)
  })

  it('returns null when logs match predictions - no noise adjustments', () => {
    seedTrips([
      { actualArrivalPct: 40 },
      { actualArrivalPct: 40 },
      { actualArrivalPct: 40 },
    ])
    expect(computeCalibration()).toBeNull()
  })

  it('weighs recent trips more than old ones', () => {
    // Newest first in storage: recent trips ran hungry, older ones exact
    seedTrips([
      { actualArrivalPct: 25 }, // ratio 1.3
      { actualArrivalPct: 25 }, // ratio 1.3
      { actualArrivalPct: 40 }, // ratio 1.0
      { actualArrivalPct: 40 }, // ratio 1.0
    ])
    const recentHungry = computeCalibration()!
    seedTrips([
      { actualArrivalPct: 40 },
      { actualArrivalPct: 40 },
      { actualArrivalPct: 25 },
      { actualArrivalPct: 25 },
    ])
    const oldHungry = computeCalibration()!
    expect(recentHungry.factor).toBeGreaterThan(oldHungry.factor)
  })

  it('caps a single wild log entry instead of letting it dominate', () => {
    seedTrips([
      { actualArrivalPct: -160 }, // garbage: ratio would be 5, capped at 2
      { actualArrivalPct: 40 },
      { actualArrivalPct: 40 },
    ])
    const cal = computeCalibration()
    // Even with the cap, the result stays inside the planner's accepted range
    if (cal) expect(cal.factor).toBeLessThanOrEqual(1.5)
  })
})
