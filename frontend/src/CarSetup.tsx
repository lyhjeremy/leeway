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
          <div className="modal-result-big">≈ {derivedRange} mi on a full charge</div>
          <div className="modal-result-sub">This is the number every plan will use from now on.</div>
        </div>

        <div className="modal-actions">
          <button className="plan-btn" onClick={() => onSave(derivedRange)}>
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
