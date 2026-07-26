"""Ties routing + range math + charging-station search into one trip plan.

Multi-stop by construction: a short-range car on a long trip may genuinely
need more than one charging stop, so this loops - plan a leg, check if it's
feasible, and if not, find a real charger and repeat from there - rather
than assuming one stop is always enough. Each leg's distance/elevation/
highway-mix comes from a real ORS directions call for that exact sub-route,
not a proportional slice of the whole trip, so the numbers for legs 2+ are
as real as leg 1's.

Candidate selection is a two-pass process, and this split matters: a first
pass approximates each candidate's reachability using the *route polyline's*
distance to it (cheap, no extra API calls, used only to rank candidates),
then every top-ranked candidate is re-verified with a REAL directions call
before being accepted. The approximation alone is not trustworthy - it
measures how far along the route a charger's nearest point projects to, not
the real driving distance to reach it, and a real test on this route found
exactly that gap: a charger the approximation called reachable turned out,
via a real routing call, to require more distance than the battery actually
had. So the approximation only ever picks candidates worth *trying*; it
never decides feasibility on its own.
"""

from . import providers, range_model
from .geo import cumulative_distances_mi, nearest_route_distance_mi, point_at_distance

SEARCH_RADII_MI = [15, 30, 50]  # widen if nothing pans out, before giving up
STOP_MODE_WINDOW_MI = 20
MAX_STOPS = 6  # safety cap - if a real trip needs more than this, say so rather than loop forever
MAX_CANDIDATES_VERIFIED_PER_RADIUS = 8  # real-directions calls are not free API quota

# Excludes Level 1/2 chargers (typically <=19.2kW) from consideration - a
# real find, driving this constant: a search near a genuine I-5 stop once
# returned a 3.7kW hospital parking-lot charger (725-minute charge time) as
# a candidate alongside a real Tesla Supercharger at the same location.
# Below this threshold isn't a realistic road-trip stop, full stop.
MIN_CHARGING_KW = 20


def _rank_candidates(route: dict, start_pct: float, full_range_mi: float,
                      reserve_pct: float, reserve_mi: float, stop_mode: str,
                      stations: list[dict]) -> list[dict]:
    """Best-first list of power-filtered, approximately-reachable candidates.
    Approximate only - see module docstring. Never trusted as final feasibility.

    Uses the real per-vertex elevation profile (cumulative_pct_used), not a
    proportional slice of the route's aggregate ascent/descent - a route can
    have a front-loaded climb (e.g. the Grapevine on I-5, ~7400ft of real
    climb in the first 84mi of a 381mi trip with near-zero *net* elevation
    change overall) where the proportional-slice version was badly wrong: it
    smears that climb evenly across the whole trip and thinks a candidate at
    mile 84 is still comfortably reachable when the real elevation profile
    says otherwise. Found via a real test on this exact route, not a
    hypothetical."""
    coords = route["geometry"]
    cum = cumulative_distances_mi(coords)
    pct_used_curve = range_model.cumulative_pct_used(cum, route["elevations_m"], full_range_mi, route["highway_fraction"])
    floor = range_model.reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi)
    target_mi = range_model.distance_to_floor_mi_elevation_aware(
        cum, route["elevations_m"], full_range_mi, start_pct, route["highway_fraction"], reserve_pct, reserve_mi,
    )

    candidates = []
    for s in stations:
        if s["max_kw"] < MIN_CHARGING_KW:
            continue
        dist_along = nearest_route_distance_mi(coords, cum, s["lat"], s["lon"])
        approx_arrival_pct = start_pct - range_model.pct_used_at_distance(cum, pct_used_curve, dist_along)
        # _approx_feasible is a ranking signal only, never a filter - the
        # approximation can still be wrong (it doesn't know the real driving
        # distance to a charger set back from the route), so an
        # "infeasible"-looking candidate still gets tried for real, just
        # after the more promising ones.
        candidates.append({**s, "distance_along_route_mi": dist_along, "_approx_feasible": approx_arrival_pct >= floor})

    if stop_mode == "fastest_trip":
        candidates.sort(key=lambda c: (not c["_approx_feasible"], abs(c["distance_along_route_mi"] - target_mi) > STOP_MODE_WINDOW_MI, -c["max_kw"]))
    elif stop_mode == "best_amenities":
        candidates.sort(key=lambda c: (not c["_approx_feasible"], abs(c["distance_along_route_mi"] - target_mi) > STOP_MODE_WINDOW_MI, -c["connector_count"]))
    else:
        candidates.sort(key=lambda c: (not c["_approx_feasible"], -c["distance_along_route_mi"]))

    return candidates


async def plan_trip(
    origin: tuple[float, float],
    destination: tuple[float, float],
    battery_pct: float,
    full_range_mi: float,
    reserve_pct: float = 15.0,
    reserve_mi: float = 30.0,
    charge_to_pct: float = 80.0,
    stop_mode: str = "fewest_stops",
    avoid_tolls: bool = False,
    avoid_highways: bool = False,
) -> dict:
    stops_out = []
    leg_geometries = []
    total_distance_mi = 0.0
    total_duration_min = 0.0

    current_start = origin
    current_pct = battery_pct
    note = None

    for _ in range(MAX_STOPS + 1):
        remaining = await providers.directions(current_start, destination, avoid_tolls, avoid_highways)
        estimate = range_model.estimate_arrival(
            full_range_mi, current_pct, remaining["distance_mi"], remaining["highway_fraction"],
            remaining["ascent_ft"], remaining["descent_ft"], reserve_pct, reserve_mi,
        )

        if estimate.feasible:
            leg_geometries.append(remaining["geometry"])
            total_distance_mi += remaining["distance_mi"]
            total_duration_min += remaining["duration_min"]
            final_arrival_pct = estimate.arrival_pct
            final_leeway_mi = estimate.leeway_mi
            feasible = True
            break

        search_lat, search_lon = _search_point(remaining, current_pct, full_range_mi, reserve_pct, reserve_mi)
        chosen = None
        chosen_leg = None
        chosen_leg_estimate = None

        for radius in SEARCH_RADII_MI:
            stations = await providers.find_charging_stations(search_lat, search_lon, radius_mi=radius)
            if not stations:
                continue
            ranked = _rank_candidates(remaining, current_pct, full_range_mi, reserve_pct, reserve_mi, stop_mode, stations)

            for candidate in ranked[:MAX_CANDIDATES_VERIFIED_PER_RADIUS]:
                leg_to_stop = await providers.directions(current_start, (candidate["lat"], candidate["lon"]), avoid_tolls, avoid_highways)
                leg_estimate = range_model.estimate_arrival(
                    full_range_mi, current_pct, leg_to_stop["distance_mi"], leg_to_stop["highway_fraction"],
                    leg_to_stop["ascent_ft"], leg_to_stop["descent_ft"], reserve_pct, reserve_mi,
                )
                if leg_estimate.feasible:
                    chosen, chosen_leg, chosen_leg_estimate = candidate, leg_to_stop, leg_estimate
                    break

            if chosen is not None:
                break

        if chosen is None:
            note = (
                f"No real-verified reachable fast-charging station (>={MIN_CHARGING_KW}kW) found within "
                f"{SEARCH_RADII_MI[-1]} miles of where the battery would hit reserve - "
                "this plan is genuinely incomplete past this point, not just approximate."
            )
            leg_geometries.append(remaining["geometry"])
            total_distance_mi += remaining["distance_mi"]
            total_duration_min += remaining["duration_min"]
            final_arrival_pct = estimate.arrival_pct
            final_leeway_mi = estimate.leeway_mi
            feasible = False
            break

        leg_geometries.append(chosen_leg["geometry"])
        total_distance_mi += chosen_leg["distance_mi"]
        total_duration_min += chosen_leg["duration_min"]

        charge_time_min = range_model.estimate_charge_time_min(
            full_range_mi, chosen_leg_estimate.arrival_pct, charge_to_pct, chosen["max_kw"],
        )
        if charge_time_min:
            total_duration_min += charge_time_min

        stops_out.append({
            "title": chosen["title"],
            "lat": chosen["lat"],
            "lon": chosen["lon"],
            "network": chosen["network"],
            "is_supercharger": chosen["is_supercharger"],
            "max_kw": chosen["max_kw"],
            "arrive_pct": round(chosen_leg_estimate.arrival_pct, 1),
            "charge_to_pct": charge_to_pct,
            "charge_time_min": round(charge_time_min) if charge_time_min else None,
            "reachable": True,  # real-verified above, not the approximation
        })

        current_start = (chosen["lat"], chosen["lon"])
        current_pct = charge_to_pct
    else:
        note = f"This trip needs more than {MAX_STOPS} charging stops - the plan below stops there rather than loop forever."
        final_arrival_pct = current_pct
        final_leeway_mi = 0.0
        feasible = False

    geometry = [pt for leg in leg_geometries for pt in leg]

    return {
        "distance_mi": round(total_distance_mi, 1),
        "duration_min": round(total_duration_min),  # driving + estimated charge time, see range_model.estimate_charge_time_min
        "geometry": geometry,
        "reserve_floor_pct": round(range_model.reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi), 1),
        "feasible": feasible,
        "arrival_pct": round(final_arrival_pct, 1),
        "leeway_mi": round(final_leeway_mi, 1),
        "stops": stops_out,
        "note": note,
    }


def _search_point(route: dict, start_pct: float, full_range_mi: float, reserve_pct: float, reserve_mi: float):
    coords = route["geometry"]
    cum = cumulative_distances_mi(coords)
    target_mi = range_model.distance_to_floor_mi_elevation_aware(
        cum, route["elevations_m"], full_range_mi, start_pct, route["highway_fraction"], reserve_pct, reserve_mi,
    )
    lon, lat = point_at_distance(coords, cum, target_mi)
    return lat, lon
