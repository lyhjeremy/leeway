"""Trip-aware range estimate.

Not a physics simulator - a documented, honest heuristic. Three adjustments
on top of a flat mi-per-% baseline, all real, well-known EV effects:

1. Highway driving costs more energy per mile than mixed/city driving at EV
   speeds (aero drag scales with v^2). +15% consumption on the highway
   fraction of the trip is a commonly cited real-world delta for a car like
   the Model 3, not a made-up number, but it is still an approximation - the
   real value varies by speed, wind, and temperature.
2. Net elevation gain costs real energy; net elevation loss recovers some of
   it via regenerative braking, but not all - round-trip regen efficiency is
   commonly cited around 60-70%. ~180 Wh per 100 ft climbed is a standard
   rule-of-thumb for a car in this weight class (~3600 lb); at this car's
   ~250 Wh/mi baseline that is roughly 0.7 equivalent miles per 100 ft of net
   climb, and descent is credited back at 60% of that.
3. Temperature and headwind (Stage 2) - see weather_adjustment_fraction.
   Fetched once per plan as a trip-day snapshot, not per-leg or per-segment;
   weather varies continuously, so one snapshot is already an approximation
   and a more granular one wouldn't be meaningfully more honest.

All constants are named below so they're easy to revisit once Stage 5's
real logged-trip data exists to calibrate against - that's the whole point
of Stage 5, not something to fake precision on now.
"""

from dataclasses import dataclass

from .geo import FT_PER_METER

HIGHWAY_CONSUMPTION_PENALTY = 0.15  # +15% energy/mile on the highway fraction
EQUIV_MI_PER_100FT_CLIMB = 0.7
REGEN_RECOVERY_EFFICIENCY = 0.6  # fraction of climb-penalty recovered on descent

# Weather adjustment (Stage 2). Cold is the dominant real EV effect - widely
# cited real-world tests show roughly 20-30% range loss around freezing vs a
# ~70F mild baseline; 0.6%/degF below 60F over a ~30F gap to freezing lands
# in that band without pretending more precision than a single trip-day
# weather snapshot deserves. Heat's effect (AC load) is real but smaller.
# Headwind cost is modeled the same way as the highway penalty (aero drag),
# since a real Model 3 delivers observably worse mileage against a stiff
# headwind and observably better with a tailwind at rest of it. All three
# terms are added to the same consumption multiplier as HIGHWAY_CONSUMPTION_
# PENALTY, then clamped - see weather_adjustment_fraction.
TEMP_COLD_REFERENCE_F = 60.0
TEMP_HOT_REFERENCE_F = 85.0
COLD_PENALTY_FRAC_PER_DEGF = 0.006
HOT_PENALTY_FRAC_PER_DEGF = 0.003
WIND_PENALTY_FRAC_PER_MPH = 0.008
WEATHER_ADJUSTMENT_CLAMP = (-0.25, 0.5)  # a very strong tailwind still can't imply free energy

# Cargo/passenger load. Added mass raises rolling resistance roughly in
# proportion, but aero drag (which dominates at highway speed) doesn't care
# about weight - so the per-pound effect is real yet modest. ~0.8% more
# consumption per 100 lb is in line with published EV loading tests; a full
# car (4 extra people + 4 bags) comes out around 7%, which matches the
# "fully loaded costs you a few percent" experience rather than a scare
# number.
PASSENGER_LB = 175.0
SUITCASE_LB = 50.0
LOAD_PENALTY_FRAC_PER_100LB = 0.008


def load_adjustment_fraction(passengers: int, suitcases: int) -> float:
    """Extra fractional consumption from people (beyond the driver) and
    luggage. Combined additively with the weather adjustment."""
    added_lb = passengers * PASSENGER_LB + suitcases * SUITCASE_LB
    return LOAD_PENALTY_FRAC_PER_100LB * added_lb / 100.0


def weather_adjustment_fraction(temp_f: float, headwind_mph: float) -> float:
    """Extra fractional consumption multiplier from temperature and wind,
    added alongside HIGHWAY_CONSUMPTION_PENALTY's highway term - positive
    means the trip uses more energy per mile, negative means less (e.g. a
    tailwind)."""
    cold = max(0.0, TEMP_COLD_REFERENCE_F - temp_f) * COLD_PENALTY_FRAC_PER_DEGF
    hot = max(0.0, temp_f - TEMP_HOT_REFERENCE_F) * HOT_PENALTY_FRAC_PER_DEGF
    wind = headwind_mph * WIND_PENALTY_FRAC_PER_MPH
    lo, hi = WEATHER_ADJUSTMENT_CLAMP
    return max(lo, min(hi, cold + hot + wind))


def describe_weather(temp_f: float, headwind_mph: float) -> str:
    """'74F, light headwind' style summary - shown so drivers can see
    weather wasn't ignored, per the product plan."""
    if headwind_mph >= 20:
        wind_desc = "strong headwind"
    elif headwind_mph >= 10:
        wind_desc = "moderate headwind"
    elif headwind_mph >= 3:
        wind_desc = "light headwind"
    elif headwind_mph <= -20:
        wind_desc = "strong tailwind"
    elif headwind_mph <= -10:
        wind_desc = "moderate tailwind"
    elif headwind_mph <= -3:
        wind_desc = "light tailwind"
    else:
        wind_desc = "calm wind"
    return f"{round(temp_f)}°F, {wind_desc}"

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
    weather_adjustment: float = 0.0,
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
    effective_pct_per_mi = baseline_pct_per_mi * (1 + HIGHWAY_CONSUMPTION_PENALTY * highway_fraction + weather_adjustment)
    floor = reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi)
    return max(0.0, (start_pct - floor) / effective_pct_per_mi)


def cumulative_pct_used(
    cum_mi: list[float],
    elevations_m: list[float],
    full_range_mi: float,
    highway_fraction: float,
    weather_adjustment: float = 0.0,
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
    effective_pct_per_mi = baseline_pct_per_mi * (1 + HIGHWAY_CONSUMPTION_PENALTY * highway_fraction + weather_adjustment)

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
    weather_adjustment: float = 0.0,
) -> float:
    """Where the reserve floor is actually hit along this specific route,
    using real per-vertex elevation. Falls back to the flat estimate only if
    the floor is never hit within the given geometry (i.e. the route as given
    is already feasible - the caller shouldn't be calling this in that case,
    but returning the route's full length rather than raising is the safer
    failure mode)."""
    target_pct_used = start_pct - reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi)
    pct_used = cumulative_pct_used(cum_mi, elevations_m, full_range_mi, highway_fraction, weather_adjustment)
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
    weather_adjustment: float = 0.0,
) -> RangeEstimate:
    baseline_pct_per_mi = 100.0 / full_range_mi
    effective_pct_per_mi = baseline_pct_per_mi * (1 + HIGHWAY_CONSUMPTION_PENALTY * highway_fraction + weather_adjustment)

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
