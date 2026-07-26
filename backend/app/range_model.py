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
    ignoring elevation (elevation isn't uniform along a route, so this is
    only used to pick a search *center* for candidate charging stations -
    every actual feasibility number still goes through estimate_arrival
    with real elevation for that specific leg)."""
    baseline_pct_per_mi = 100.0 / full_range_mi
    effective_pct_per_mi = baseline_pct_per_mi * (1 + HIGHWAY_CONSUMPTION_PENALTY * highway_fraction)
    floor = reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi)
    return max(0.0, (start_pct - floor) / effective_pct_per_mi)


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
