"""Ties routing + range math + charging-station search into one trip plan.

Multi-stop by construction: a short-range car on a long trip may genuinely
need more than one charging stop, so this loops - plan a leg, check if it's
feasible, and if not, find a real charger and repeat from there - rather
than assuming one stop is always enough. Each leg's distance/elevation/
highway-mix comes from a real ORS directions call for that exact sub-route,
not a proportional slice of the whole trip, so the numbers for legs 2+ are
as real as leg 1's.

The one deliberately-approximate part: to decide *where* to search for a
charger along a leg that's too long, we assume uniform consumption along
that leg's geometry (real elevation is lumpy, not uniform) to pick a search
center. Every candidate charger found near that point then gets its own
real feasibility check via the range model, so the approximation only ever
affects where we search, never the feasibility number a user sees.
"""

from . import providers, range_model
from .geo import cumulative_distances_mi, nearest_route_distance_mi, point_at_distance

SEARCH_RADIUS_MI = 15
STOP_MODE_WINDOW_MI = 20
MAX_STOPS = 6  # safety cap - if a real trip needs more than this, say so rather than loop forever


def _pick_candidate(route: dict, start_pct: float, full_range_mi: float,
                     reserve_pct: float, reserve_mi: float, stop_mode: str,
                     stations: list[dict]) -> dict | None:
    coords = route["geometry"]
    cum = cumulative_distances_mi(coords)
    target_mi = range_model.distance_to_floor_mi(full_range_mi, start_pct, route["highway_fraction"], reserve_pct, reserve_mi)

    total_mi = route["distance_mi"]
    candidates = []
    for s in stations:
        dist_along = nearest_route_distance_mi(coords, cum, s["lat"], s["lon"])
        frac = dist_along / total_mi if total_mi else 0
        est = range_model.estimate_arrival(
            full_range_mi, start_pct, dist_along, route["highway_fraction"],
            route["ascent_ft"] * frac, route["descent_ft"] * frac, reserve_pct, reserve_mi,
        )
        candidates.append({**s, "distance_along_route_mi": dist_along, "_estimate": est})

    if not candidates:
        return None

    reachable = [c for c in candidates if c["_estimate"].feasible]
    pool = reachable or candidates

    if stop_mode == "fastest_trip":
        windowed = [c for c in pool if abs(c["distance_along_route_mi"] - target_mi) <= STOP_MODE_WINDOW_MI] or pool
        return max(windowed, key=lambda c: c["max_kw"])
    if stop_mode == "best_amenities":
        windowed = [c for c in pool if abs(c["distance_along_route_mi"] - target_mi) <= STOP_MODE_WINDOW_MI] or pool
        return max(windowed, key=lambda c: c["connector_count"])
    return max(pool, key=lambda c: c["distance_along_route_mi"])


async def plan_trip(
    origin: tuple[float, float],
    destination: tuple[float, float],
    battery_pct: float,
    full_range_mi: float,
    reserve_pct: float = 15.0,
    reserve_mi: float = 30.0,
    charge_to_pct: float = 80.0,
    stop_mode: str = "fewest_stops",
) -> dict:
    stops_out = []
    leg_geometries = []
    total_distance_mi = 0.0
    total_duration_min = 0.0

    current_start = origin
    current_pct = battery_pct
    note = None

    for _ in range(MAX_STOPS + 1):
        remaining = await providers.directions(current_start, destination)
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

        stations = await providers.find_charging_stations(*_search_point(remaining, current_pct, full_range_mi, reserve_pct, reserve_mi), radius_mi=SEARCH_RADIUS_MI)
        chosen = _pick_candidate(remaining, current_pct, full_range_mi, reserve_pct, reserve_mi, stop_mode, stations) if stations else None

        if chosen is None:
            note = "Couldn't find a charging station near this route - the plan below is incomplete."
            leg_geometries.append(remaining["geometry"])
            total_distance_mi += remaining["distance_mi"]
            total_duration_min += remaining["duration_min"]
            final_arrival_pct = estimate.arrival_pct
            final_leeway_mi = estimate.leeway_mi
            feasible = False
            break

        leg_to_stop = await providers.directions(current_start, (chosen["lat"], chosen["lon"]))
        leg_estimate = range_model.estimate_arrival(
            full_range_mi, current_pct, leg_to_stop["distance_mi"], leg_to_stop["highway_fraction"],
            leg_to_stop["ascent_ft"], leg_to_stop["descent_ft"], reserve_pct, reserve_mi,
        )
        leg_geometries.append(leg_to_stop["geometry"])
        total_distance_mi += leg_to_stop["distance_mi"]
        total_duration_min += leg_to_stop["duration_min"]

        charge_time_min = range_model.estimate_charge_time_min(
            full_range_mi, leg_estimate.arrival_pct, charge_to_pct, chosen["max_kw"],
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
            "arrive_pct": round(leg_estimate.arrival_pct, 1),
            "charge_to_pct": charge_to_pct,
            "charge_time_min": round(charge_time_min) if charge_time_min else None,
            "reachable": leg_estimate.feasible,
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
    target_mi = range_model.distance_to_floor_mi(full_range_mi, start_pct, route["highway_fraction"], reserve_pct, reserve_mi)
    lon, lat = point_at_distance(coords, cum, target_mi)
    return lat, lon
