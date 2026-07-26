import type { GeocodeResult, PlanResponse } from './types'

interface Props {
  plan: PlanResponse
  origin: GeocodeResult
  destination: GeocodeResult
  batteryPct: number
  onClose: () => void
}

// Deliberately condensed to fit one screen with no scrolling - the whole
// point is that this survives the trip as a screenshot on your phone, even
// though the car's own screen can't show the web app. No map, no controls,
// just the numbers you need mid-drive.
export default function TripCard({ plan, origin, destination, batteryPct, onClose }: Props) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card trip-card" onClick={(e) => e.stopPropagation()}>
        <div className="logo-word" style={{ marginBottom: 2 }}>
          Leeway trip card
        </div>
        <p className="modal-lede" style={{ marginBottom: 14 }}>
          {origin.label.split(',')[0]} → {destination.label.split(',')[0]}
        </p>

        <div className="trip-card-row trip-card-start">
          <span className="trip-card-dot" />
          <div>
            <div className="trip-card-title">Leave with {batteryPct}%</div>
          </div>
        </div>

        {plan.stops.map((s, i) => (
          <div className="trip-card-row" key={i}>
            <span className={s.is_supercharger ? 'trip-card-pin sc' : 'trip-card-pin ccs'} />
            <div>
              <div className="trip-card-title">{s.title}</div>
              <div className="trip-card-sub">
                Arrive {s.arrive_pct}% → charge to {s.charge_to_pct}%
                {s.charge_time_min ? ` (~${s.charge_time_min} min)` : ''}
              </div>
            </div>
          </div>
        ))}

        <div className="trip-card-row trip-card-end">
          <span className="trip-card-dot outline" />
          <div>
            <div className="trip-card-title">Arrive at {plan.arrival_pct}%</div>
            <div className="trip-card-sub">
              {plan.distance_mi} mi · {Math.round(plan.duration_min / 60)}h {plan.duration_min % 60}min total
            </div>
          </div>
        </div>

        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button className="modal-skip" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
