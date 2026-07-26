"""Trip-aware range estimate.

Not a physics simulator - a documented, honest heuristic. Two adjustments on
top of a flat mi-per-% baseline, both real, well-known EV effects:

1. Highway driving costs more energy per mile than mixed/city driving at EV
   speeds (aero drag scales with v^2). +15% consumption on the highway
   fraction of the trip is a commonly cited real-world delta for a car like
   the Model 3, not a made-up number, but it is still an approximation - the
   real value varies by speed, wind, and temperature (temperature/wind are
   Stage 2, not modeled here yet).
2. Net elevation gain costs real energy; net elevation loss recovers some of
   it via regenerative braking, but not all - round-trip regen efficiency is
   commonly cited around 60-70%. ~180 Wh per 100 ft climbed is a standard
   rule-of-thumb for a car in this weight class (~3600 lb); at this car's
   ~250 Wh/mi baseline that is roughly 0.7 equivalent miles per 100 ft of net
   climb, and descent is credited back at 60% of that.

Both constants are named below so they're easy to revisit once Stage 5's
real logged-trip data exists to calibrate against - that's the whole point
of Stage 5, not something to fake precision on now.
"""

from dataclasses import dataclass

from .geo import FT_PER_METER

HIGHWAY_CONSUMPTION_PENALTY = 0.15  # +15% energy/mile on the highway fraction
EQUIV_MI_PER_100FT_CLIMB = 0.7
REGEN_RECOVERY_EFFICIENCY = 0.6  # fraction of climb-penalty recovered on descent

# Charge-time estimate only (never used for the range/feasibility numbers
# above). 250 Wh/mi is a commonly cited combined-efficiency figure for a
# Model 3 Standard Range Plus - used here only to convert a %-of-battery gap
# into real kWh. 0.7x peak kW is a rough stand-in for real DC fast-charging
# curves, which taper well below their rated peak above roughly 50%; this is
# not a real charging-curve model, just enough to give an honest ballpark
# total trip time rather than omitting it.
ASSUMED_WH_PER_MILE = 250
CHARGING_CURVE_UTILIZATION = 0.7


def estimate_charge_time_min(full_range_mi: float, from_pct: float, to_pct: float, station_max_kw: float) -> float | None:
    if not station_max_kw or station_max_kw <= 0:
        return None
    capacity_kwh = full_range_mi * ASSUMED_WH_PER_MILE / 1000
    energy_kwh = max(0.0, (to_pct - from_pct) / 100 * capacity_kwh)
    effective_kw = station_max_kw * CHARGING_CURVE_UTILIZATION
    return (energy_kwh / effective_kw) * 60


@dataclass
class RangeEstimate:
    arrival_pct: float
    pct_used: float
    effective_distance_mi: float
    reserve_floor_pct: float
    feasible: bool
    leeway_mi: float


def reserve_floor_pct(full_range_mi: float, reserve_pct: float, reserve_mi: float) -> float:
    """The reserve floor as a %, taking whichever of the two default rules
    (15% or 30mi) binds tighter for this car's real range - a 205mi car's
    30mi floor is ~14.6%, just under 15%, so 15% is the one that binds by
    design at that range; a longer-range car would have 30mi bind instead."""
    return max(reserve_pct, (reserve_mi / full_range_mi) * 100)


def distance_to_floor_mi(
    full_range_mi: float,
    start_pct: float,
    highway_fraction: float,
    reserve_pct: float = 15.0,
    reserve_mi: float = 30.0,
) -> float:
    """Roughly how far the car can go before hitting the reserve floor,
    ignoring elevation entirely. Kept only as a cheap fallback when no real
    elevation profile is available - prefer distance_to_floor_mi_elevation_aware
    whenever per-vertex elevation exists. A real test on this project found
    exactly why the flat version is dangerous: a route with a front-loaded
    climb (net elevation near zero over the whole trip) can exhaust the real
    reserve well before this ignore-elevation estimate says it will, because
    this formula spreads the trip's *net* elevation change evenly across the
    whole distance instead of respecting where the climbing actually happens."""
    baseline_pct_per_mi = 100.0 / full_range_mi
    effective_pct_per_mi = baseline_pct_per_mi * (1 + HIGHWAY_CONSUMPTION_PENALTY * highway_fraction)
    floor = reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi)
    return max(0.0, (start_pct - floor) / effective_pct_per_mi)


def cumulative_pct_used(
    cum_mi: list[float],
    elevations_m: list[float],
    full_range_mi: float,
    highway_fraction: float,
) -> list[float]:
    """Per-vertex cumulative %-of-battery-used, walking real segment-level
    distance and elevation change - not a net-elevation average. This is
    what makes the Grapevine case work: the climb is real and immediate in
    this list, not smeared evenly across the whole route the way
    distance_to_floor_mi's ignore-elevation math would smear it.
    Highway fraction is still applied uniformly per mile (ORS's waycategory
    breakdown isn't reliable enough per-segment to do better - see
    providers.py), same limitation as estimate_arrival."""
    baseline_pct_per_mi = 100.0 / full_range_mi
    effective_pct_per_mi = baseline_pct_per_mi * (1 + HIGHWAY_CONSUMPTION_PENALTY * highway_fraction)

    pct_used = [0.0]
    for i in range(1, len(cum_mi)):
        seg_mi = cum_mi[i] - cum_mi[i - 1]
        delta_ft = (elevations_m[i] - elevations_m[i - 1]) * FT_PER_METER
        if delta_ft > 0:
            seg_effective_mi = seg_mi + (delta_ft / 100.0) * EQUIV_MI_PER_100FT_CLIMB
        else:
            seg_effective_mi = seg_mi - (abs(delta_ft) / 100.0) * EQUIV_MI_PER_100FT_CLIMB * REGEN_RECOVERY_EFFICIENCY
        pct_used.append(pct_used[-1] + seg_effective_mi * effective_pct_per_mi)
    return pct_used


def pct_used_at_distance(cum_mi: list[float], pct_used_curve: list[float], target_mi: float) -> float:
    """Interpolate the cumulative_pct_used curve at an arbitrary distance -
    used to score a charging candidate at its real position along the route
    without recomputing the whole curve per candidate."""
    if target_mi <= 0:
        return 0.0
    if target_mi >= cum_mi[-1]:
        return pct_used_curve[-1]
    for i in range(1, len(cum_mi)):
        if cum_mi[i] >= target_mi:
            seg_len = cum_mi[i] - cum_mi[i - 1]
            frac = 0 if seg_len == 0 else (target_mi - cum_mi[i - 1]) / seg_len
            return pct_used_curve[i - 1] + (pct_used_curve[i] - pct_used_curve[i - 1]) * frac
    return pct_used_curve[-1]


def distance_to_floor_mi_elevation_aware(
    cum_mi: list[float],
    elevations_m: list[float],
    full_range_mi: float,
    start_pct: float,
    highway_fraction: float,
    reserve_pct: float = 15.0,
    reserve_mi: float = 30.0,
) -> float:
    """Where the reserve floor is actually hit along this specific route,
    using real per-vertex elevation. Falls back to the flat estimate only if
    the floor is never hit within the given geometry (i.e. the route as given
    is already feasible - the caller shouldn't be calling this in that case,
    but returning the route's full length rather than raising is the safer
    failure mode)."""
    target_pct_used = start_pct - reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi)
    pct_used = cumulative_pct_used(cum_mi, elevations_m, full_range_mi, highway_fraction)
    for i in range(1, len(pct_used)):
        if pct_used[i] >= target_pct_used:
            seg_pct = pct_used[i] - pct_used[i - 1]
            frac = 0 if seg_pct == 0 else (target_pct_used - pct_used[i - 1]) / seg_pct
            return cum_mi[i - 1] + (cum_mi[i] - cum_mi[i - 1]) * frac
    return cum_mi[-1]


def estimate_arrival(
    full_range_mi: float,
    start_pct: float,
    distance_mi: float,
    highway_fraction: float,
    ascent_ft: float,
    descent_ft: float,
    reserve_pct: float = 15.0,
    reserve_mi: float = 30.0,
) -> RangeEstimate:
    baseline_pct_per_mi = 100.0 / full_range_mi
    effective_pct_per_mi = baseline_pct_per_mi * (1 + HIGHWAY_CONSUMPTION_PENALTY * highway_fraction)

    climb_penalty_mi = (ascent_ft / 100.0) * EQUIV_MI_PER_100FT_CLIMB
    descent_credit_mi = (descent_ft / 100.0) * EQUIV_MI_PER_100FT_CLIMB * REGEN_RECOVERY_EFFICIENCY
    effective_distance_mi = distance_mi + climb_penalty_mi - descent_credit_mi

    pct_used = effective_distance_mi * effective_pct_per_mi
    arrival_pct = start_pct - pct_used

    floor = reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi)
    leeway_mi = (arrival_pct - floor) / baseline_pct_per_mi

    return RangeEstimate(
        arrival_pct=arrival_pct,
        pct_used=pct_used,
        effective_distance_mi=effective_distance_mi,
        reserve_floor_pct=floor,
        feasible=arrival_pct >= floor,
        leeway_mi=leeway_mi,
    )
