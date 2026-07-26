import { useState } from 'react'
import type { Units } from './types'

const MI_TO_KM = 1.609344

interface Props {
  currentRangeMi: number
  units: Units
  onSave: (rangeMi: number) => void
  onClose: () => void
}

export default function CarSetup({ currentRangeMi, units, onSave, onClose }: Props) {
  const [batteryNow, setBatteryNow] = useState(68)
  // The dashboard shows whatever unit the car is set to - let people copy
  // it over as-is. Everything is stored in miles internally.
  const [rangeShown, setRangeShown] = useState(
    Math.round(currentRangeMi * 0.68 * (units === 'km' ? MI_TO_KM : 1)),
  )

  const rangeShownMi = units === 'km' ? rangeShown / MI_TO_KM : rangeShown
  const derivedRangeMi = batteryNow > 0 ? Math.round((rangeShownMi / batteryNow) * 100) : currentRangeMi
  const derivedDisplay = Math.round(derivedRangeMi * (units === 'km' ? MI_TO_KM : 1))
  // No production EV has a 50-mile or a 900-mile pack. Out-of-bounds numbers
  // here are always a typo'd dashboard reading (e.g. battery 1%, range 999)
  // - stress-tested combination that used to save a 99,900 mi "range".
  const plausible = batteryNow >= 1 && batteryNow <= 100 && derivedRangeMi >= 50 && derivedRangeMi <= 600

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="logo-word" style={{ marginBottom: 4 }}>
          Find your real range
        </div>
        <p className="modal-lede">
          Copy over what your car's screen shows right now. That's all we need.
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
            <span className="unit">{units}</span>
          </label>
        </div>

        <div className="modal-result">
          <div className="modal-result-big">
            {plausible ? `≈ ${derivedDisplay} ${units} on a full charge` : 'Those numbers don’t look right'}
          </div>
          <div className="modal-result-sub">
            {plausible
              ? 'This is the number every plan will use from now on.'
              : 'Double-check the battery % and range on your car’s screen.'}
          </div>
        </div>

        <div className="modal-actions">
          <button className="plan-btn" onClick={() => onSave(derivedRangeMi)} disabled={!plausible}>
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
