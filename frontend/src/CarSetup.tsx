import { useState } from 'react'

interface Props {
  currentRangeMi: number
  onSave: (rangeMi: number) => void
  onClose: () => void
}

export default function CarSetup({ currentRangeMi, onSave, onClose }: Props) {
  const [batteryNow, setBatteryNow] = useState(68)
  const [rangeShown, setRangeShown] = useState(Math.round(currentRangeMi * 0.68))

  const derivedRange = batteryNow > 0 ? Math.round((rangeShown / batteryNow) * 100) : currentRangeMi
  // No production EV has a 50-mile or a 900-mile pack. Out-of-bounds numbers
  // here are always a typo'd dashboard reading (e.g. battery 1%, range 999)
  // - stress-tested combination that used to save a 99,900 mi "range".
  const plausible = batteryNow >= 1 && batteryNow <= 100 && derivedRange >= 50 && derivedRange <= 600

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="logo-word" style={{ marginBottom: 4 }}>
          Find your real range
        </div>
        <p className="modal-lede">
          Look at your car's screen right now and copy over what it says — that's all we need.
        </p>

        <div className="modal-inputs">
          <label>
            <span>Battery now</span>
            <input
              type="number"
              min={1}
              max={100}
              value={batteryNow}
              onChange={(e) => setBatteryNow(Number(e.target.value))}
            />
            <span className="unit">%</span>
          </label>
          <label>
            <span>Range shown</span>
            <input
              type="number"
              min={1}
              value={rangeShown}
              onChange={(e) => setRangeShown(Number(e.target.value))}
            />
            <span className="unit">mi</span>
          </label>
        </div>

        <div className="modal-result">
          <div className="modal-result-big">
            {plausible ? `≈ ${derivedRange} mi on a full charge` : 'Those numbers don’t look right'}
          </div>
          <div className="modal-result-sub">
            {plausible
              ? 'This is the number every plan will use from now on.'
              : 'Double-check the battery % and miles on your car’s screen.'}
          </div>
        </div>

        <div className="modal-actions">
          <button className="plan-btn" onClick={() => onSave(derivedRange)} disabled={!plausible}>
            Save & plan my first trip
          </button>
          <button className="modal-skip" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
