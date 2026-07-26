import { useEffect, useState } from 'react'

interface AccuracyEntry {
  date: string
  trip: string
  predicted_arrival_pct: number
  actual_arrival_pct: number
  notes?: string
}

export default function AccuracyPage() {
  const [entries, setEntries] = useState<AccuracyEntry[] | null>(null)

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}accuracy.json`)
      .then((r) => r.json())
      .then(setEntries)
      .catch(() => setEntries([]))
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
        <h1>Accuracy record</h1>
        <p>
          Every real trip Leeway has predicted, next to what actually happened. No trip is left out, and no favorable
          rounding — this is the honesty check for the "second opinion" promise.
        </p>
        {entries === null && <p className="muted">Loading…</p>}
        {entries && entries.length === 0 && (
          <p className="muted">
            No real trips logged yet. This page will fill in as real trips are driven and logged — the first entry
            is coming once Stage 1's end-to-end verification trip is complete.
          </p>
        )}
        {entries && entries.length > 0 && (
          <table className="accuracy-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Trip</th>
                <th>Predicted</th>
                <th>Actual</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={i}>
                  <td>{e.date}</td>
                  <td>{e.trip}</td>
                  <td>{e.predicted_arrival_pct}%</td>
                  <td>{e.actual_arrival_pct}%</td>
                  <td>{e.notes ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
