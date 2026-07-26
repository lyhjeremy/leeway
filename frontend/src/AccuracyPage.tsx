import { useEffect, useState } from 'react'
import { loadLoggedTrips, loadRangeHistory, loadUnits, type LoggedTrip, type RangeHistoryEntry } from './storage'

const MI_TO_KM = 1.609344

function formatDate(ms: number) {
  return new Date(ms).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export default function AccuracyPage() {
  const [trips, setTrips] = useState<LoggedTrip[]>([])
  const [rangeHistory, setRangeHistory] = useState<RangeHistoryEntry[]>([])
  const units = loadUnits()
  const dist = (mi: number) => `${Math.round(units === 'km' ? mi * MI_TO_KM : mi)} ${units}`

  useEffect(() => {
    setTrips(loadLoggedTrips())
    setRangeHistory(loadRangeHistory())
  }, [])

  return (
    <div className="accuracy-page">
      <div className="accuracy-header">
        <span className="logo-dot" />
        <span className="logo-word">Leeway</span>
        <a href="#" className="back-link">
          ← Back to planner
        </a>
      </div>
      <div className="accuracy-body">
        <h1>Your accuracy record</h1>
        <p>
          Everything on this page lives in <strong>this browser only</strong>. Leeway has no account system and no
          database, so nothing leaves your device. Every trip you've answered "how did it go?" for shows up here,
          predicted next to actual, with no favorable rounding.
        </p>
        {trips.length === 0 && (
          <p className="muted">
            No trips logged yet. Plan a real trip, drive it, then answer "how did that trip go?" the next time you
            open Leeway - it'll show up here.
          </p>
        )}
        {trips.length > 0 && (
          <table className="accuracy-table">
            <thead>
              <tr>
                <th>Logged</th>
                <th>Trip</th>
                <th>Predicted</th>
                <th>Actual</th>
                <th>Diff</th>
              </tr>
            </thead>
            <tbody>
              {trips.map((t, i) => {
                const diff = t.actualArrivalPct - t.predictedArrivalPct
                return (
                  <tr key={i}>
                    <td>{formatDate(t.loggedAt)}</td>
                    <td>
                      {t.originLabel.split(',')[0]} → {t.destinationLabel.split(',')[0]}
                    </td>
                    <td>{t.predictedArrivalPct}%</td>
                    <td>{t.actualArrivalPct}%</td>
                    <td>{diff === 0 ? 'exact' : diff > 0 ? `+${diff} pts` : `${diff} pts`}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}

        <h1 style={{ marginTop: 40 }}>Battery health trend</h1>
        <p>
          Every time you re-run "find your real range" in car setup, the result is logged here with a date - a rough
          trend of how your pack's real-world range is changing over time.
        </p>
        {rangeHistory.length === 0 && (
          <p className="muted">No range readings logged yet. Use "find your real range" in the planner to start.</p>
        )}
        {rangeHistory.length > 0 && (
          <table className="accuracy-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Full range</th>
              </tr>
            </thead>
            <tbody>
              {rangeHistory.map((r, i) => (
                <tr key={i}>
                  <td>{formatDate(r.date)}</td>
                  <td>{dist(r.fullRangeMi)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
