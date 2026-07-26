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
  id: number | null
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

export interface SafetyFlag {
  type?: 'steep_descent' | 'sun_glare'
  kind?: 'unprotected_left' | 'rail_crossing' | 'strong_wind'
  description: string
  lat?: number | null
  lon?: number | null
  length_mi?: number
  grade_pct?: number
  mile?: number
}

export interface WeatherInfo {
  adjustment: number
  summary: string
  temp_f: number
  headwind_mph: number
}

export interface VoiceResult {
  name: string
  brand: string | null
  lat: number
  lon: number
  drive_through: string | null
  opening_hours: string | null
  detour_min: number
  within_budget: boolean
}

export interface VoiceSearchResponse {
  parsed: {
    category: string
    brand: string | null
    drive_through_required: boolean
    max_detour_min: number
  }
  results: VoiceResult[]
  note: string | null
}

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
  rate_limited: boolean
  weather: WeatherInfo | null
  safety_flags: SafetyFlag[]
}
