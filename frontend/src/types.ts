export interface LatLon {
  lat: number
  lon: number
}

export interface GeocodeResult {
  label: string
  lat: number
  lon: number
}

export interface ChargingStop {
  title: string
  lat: number
  lon: number
  network: string
  is_supercharger: boolean
  max_kw: number
  arrive_pct: number
  charge_to_pct: number
  charge_time_min: number | null
  reachable: boolean
}

export type StopMode = 'fewest_stops' | 'fastest_trip' | 'best_amenities'

export interface PlanResponse {
  distance_mi: number
  duration_min: number
  geometry: [number, number][] // [lon, lat]
  reserve_floor_pct: number
  feasible: boolean
  arrival_pct: number
  leeway_mi: number
  stops: ChargingStop[]
  note: string | null
}
