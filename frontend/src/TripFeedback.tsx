import { useState } from 'react'
import type { PendingTrip } from './storage'

interface Props {
  pending: PendingTrip
  onLog: (actualArrivalPct: number) => void
  onDismiss: () => void
}

// Fires on the visit *after* a real plan was made (see
// shouldPromptForPendingTrip in storage.ts) - never on the same session you
// planned it, since you haven't driven anywhere yet. This is what turns a
// one-shot planner into something that gets more honest about its own
// accuracy over time.
export default function TripFeedback({ pending, onLog, onDismiss }: Props) {
  // Kept as a string so a cleared field is "not answered yet" instead of
  // silently becoming 0% - an empty input used to log "22 pts worse".
  const [actualText, setActualText] = useState(String(pending.predictedArrivalPct))
  const actualPct = Number(actualText)
  const valid = actualText.trim() !== '' && Number.isFinite(actualPct) && actualPct >= 0 && actualPct <= 100
  const diff = actualPct - pending.predictedArrivalPct

  return (
    <div className="modal-backdrop" onClick={onDismiss}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="logo-word" style={{ marginBottom: 4 }}>
          How did that trip go?
        </div>
        <p className="modal-lede">
          You planned {pending.originLabel.split(',')[0]} → {pending.destinationLabel.split(',')[0]}, predicted to
          arrive at {Math.round(pending.predictedArrivalPct)}%. What did the car actually show when you got there?
        </p>

        <div className="modal-inputs">
          <label>
            <span>Actual arrival</span>
            <input
              type="number"
              min={0}
              max={100}
              value={actualText}
              onChange={(e) => setActualText(e.target.value)}
            />
            <span className="unit">%</span>
          </label>
        </div>

        <div className="modal-result">
          <div className="modal-result-big">
            {!valid
              ? 'Enter the arrival %, 0-100'
              : diff === 0
                ? 'Spot on'
                : diff > 0
                  ? `${diff} pts better than predicted`
                  : `${Math.abs(diff)} pts worse than predicted`}
          </div>
          <div className="modal-result-sub">Logged to your accuracy record - this device only, for now.</div>
        </div>

        <div className="modal-actions">
          <button className="plan-btn" onClick={() => onLog(actualPct)} disabled={!valid}>
            Log it
          </button>
          <button className="modal-skip" onClick={onDismiss}>
            Skip
          </button>
        </div>
      </div>
    </div>
  )
}
