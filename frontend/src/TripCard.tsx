import type { GeocodeResult, PlanResponse, Units } from './types'

const MI_TO_KM = 1.609344

interface Props {
  plan: PlanResponse
  origin: GeocodeResult
  destination: GeocodeResult
  batteryPct: number
  units: Units
  onClose: () => void
}

// Deliberately condensed to fit one screen with no scrolling - the whole
// point is that this survives the trip as a screenshot on your phone, even
// though the car's own screen can't show the web app. No map, no controls,
// just the numbers you need mid-drive.
export default function TripCard({ plan, origin, destination, batteryPct, units, onClose }: Props) {
  const dist = (mi: number) => `${Math.round(units === 'km' ? mi * MI_TO_KM : mi)} ${units}`
  const driveTime = (min: number) => (min >= 60 ? `${Math.floor(min / 60)}h ${min % 60}min` : `${min}min`)
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
            <span className={s.is_waypoint ? 'trip-card-pin wp' : s.is_supercharger ? 'trip-card-pin sc' : 'trip-card-pin ccs'} />
            <div>
              {s.leg_drive_min != null && s.leg_distance_mi != null && (
                <div className="trip-card-hop">↓ {dist(s.leg_distance_mi)} · {driveTime(s.leg_drive_min)}</div>
              )}
              <div className="trip-card-title">{s.title}</div>
              <div className="trip-card-sub">
                {s.is_waypoint
                  ? `Pass through with ${s.arrive_pct}%`
                  : `Arrive ${s.arrive_pct}% → charge to ${s.charge_to_pct}%${
                      s.charge_time_min ? ` (~${s.charge_time_min} min)` : ''
                    }`}
              </div>
            </div>
          </div>
        ))}

        <div className="trip-card-row trip-card-end">
          <span className="trip-card-dot outline" />
          <div>
            {plan.last_leg_drive_min != null && plan.last_leg_distance_mi != null && (
              <div className="trip-card-hop">
                ↓ {dist(plan.last_leg_distance_mi)} · {driveTime(plan.last_leg_drive_min)}
              </div>
            )}
            <div className="trip-card-title">Arrive at {plan.arrival_pct}%</div>
            <div className="trip-card-sub">
              {dist(plan.distance_mi)} · {Math.floor(plan.duration_min / 60)}h {plan.duration_min % 60}min total
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
