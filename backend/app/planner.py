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

import asyncio
import math
import re
from datetime import datetime, timezone

import httpx

from . import crossings, providers, range_model, safety, sun
from .geo import bearing_deg, cumulative_distances_mi, haversine_mi, headwind_component_mph, nearest_route_point, point_at_distance

# Widen if nothing pans out, before giving up. The 75-mile tier exists for
# the interior West: DC-fast chargers in Nevada, Wyoming and west Texas are
# routinely further apart than California's 50-mile worst case. It only costs
# quota when the first three tiers already found nothing, since the loop stops
# at the first radius that yields a verified candidate.
SEARCH_RADII_MI = [15, 30, 50, 75]
STOP_MODE_WINDOW_MI = 20
# Ten covers 600 miles in a car whose real range has fallen to ~150 - the
# driver this product exists for, who needs ~9 stops to do it. Six truncated
# that plan silently. This is no longer the cost guard it used to be, because
# MAX_VERIFY_CALLS_PER_PLAN below bounds the API spend directly; MAX_STOPS now
# only has to stop the loop running away.
MAX_STOPS = 10
MAX_CANDIDATES_VERIFIED_PER_RADIUS = 6  # real-directions calls are not free API quota

# ORS allows 40 directions calls per minute, and the backoff for exceeding it
# is long enough that Render's ~100s proxy cuts the request first - the driver
# gets "planning took too long" and the quota is spent anyway. This bounds one
# plan's verification calls so a pathological route returns a partial plan
# with an honest note instead. Generous: a normal two-stop plan uses under 10.
MAX_VERIFY_CALLS_PER_PLAN = 60

# Excludes Level 1/2 chargers (typically <=19.2kW) from consideration - a
# real find, driving this constant: a search near a genuine I-5 stop once
# returned a 3.7kW hospital parking-lot charger (725-minute charge time) as
# a candidate alongside a real Tesla Supercharger at the same location.
# Below this threshold isn't a realistic road-trip stop, full stop.
MIN_CHARGING_KW = 20

# How much each mile of perpendicular offset from the route costs in the
# ranking, found by a real failure: a 50kW bank parking lot in Fillmore
# (17mi off I-5) outranked an on-route 250kW Supercharger because its
# projection landed 10mi farther up the road and the sort only looked at
# distance-along-route. A detour costs at least the drive out and back
# (2x), plus it's slower non-highway driving - hence 2.5.
OFFSET_PENALTY = 2.5

# A chosen stop has to actually get you down the road. Without this the
# planner can pick a charger at its own current position - trivially
# "reachable", so it verifies, charges to 80%, and loops having moved zero
# miles. Found on a real Denver -> Salt Lake City plan that burned every stop
# slot and stalled at 360 of 520 miles, having chosen the same Wyoming
# Supercharger twice in a row. California's charger density hid it: the ranked
# list almost always had something genuinely forward in it.
MIN_STOP_PROGRESS_MI = 8.0

# Two stations within this distance of each other are one physical location
# (e.g. three OCM entries for the same Fillmore parking lot) - verifying
# each against ORS wastes rate-limited quota re-testing the same spot.
DEDUPE_RADIUS_MI = 0.3

# Safety-avoidance time budgets: how many extra minutes a reroute around
# flagged spots (unprotected lefts, rail crossings) is allowed to cost
# before the direct route wins and the spots stay as warnings. Three tiers
# per Jeremy's request (2026-07-27): 5 is the default everywhere - someone
# who never touches the options still gets cheap detours - 10 and 20 for
# progressively more cautious drivers.
AVOID_BUDGET_MIN = {"avoid_quick": 5.0, "avoid_hard": 10.0, "avoid_max": 20.0}

# A hazard this close to a leg endpoint (origin, destination, or a chosen
# charger) is usually the entrance to that place itself - the Castaic
# charger's own exit lefts, for example. Routing around it would mean not
# arriving at all, so these stay flags no matter the safety mode.
HAZARD_ENDPOINT_CLEARANCE_MI = 0.15

# Half-size of the square no-go box drawn around an avoided hazard, meters.
AVOID_BOX_HALF_M = 70.0

# ORS rejects avoid_polygons on requests longer than ~150km with a 2004 error
# (see the note in providers.directions_alternatives). _safe_directions reads
# that as "no route", so before this the whole avoidance pass gave up on any
# leg over ~93 miles and told the driver no detour existed - blaming the road
# for a provider limit. A 205-mile car runs 110-125 miles between stops, so
# that was most legs of most real trips. Long legs are now routed in pieces
# that each stay under the cap.
ORS_AVOID_POLYGON_MAX_MI = 85.0


def _rank_candidates(route: dict, start_pct: float, full_range_mi: float,
                      reserve_pct: float, reserve_mi: float, stop_mode: str,
                      stations: list[dict], weather_adjustment: float = 0.0) -> list[dict]:
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
    pct_used_curve = range_model.cumulative_pct_used(cum, route["elevations_m"], full_range_mi, route["highway_fraction"], weather_adjustment)
    floor = range_model.reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi)
    target_mi = range_model.distance_to_floor_mi_elevation_aware(
        cum, route["elevations_m"], full_range_mi, start_pct, route["highway_fraction"], reserve_pct, reserve_mi, weather_adjustment,
    )

    candidates = []
    for s in stations:
        if s["max_kw"] < MIN_CHARGING_KW:
            continue
        dist_along, offset_mi = nearest_route_point(coords, cum, s["lat"], s["lon"])
        approx_arrival_pct = start_pct - range_model.pct_used_at_distance(cum, pct_used_curve, dist_along)
        # Net progress: how far along the trip this stop really gets you,
        # after paying for the detour to reach it. Without the offset term
        # a charger far off-route can outrank an on-route one purely
        # because its perpendicular projection lands farther up the road.
        net_progress_mi = dist_along - OFFSET_PENALTY * offset_mi
        # _approx_feasible is a ranking signal only, never a filter - the
        # approximation can still be wrong (it doesn't know the real driving
        # distance to a charger set back from the route), so an
        # "infeasible"-looking candidate still gets tried for real, just
        # after the more promising ones.
        candidates.append({
            **s,
            "distance_along_route_mi": dist_along,
            "offset_mi": offset_mi,
            "_net_progress_mi": net_progress_mi,
            "_approx_feasible": approx_arrival_pct >= floor,
        })

    if stop_mode == "fastest_trip":
        candidates.sort(key=lambda c: (not c["_approx_feasible"], abs(c["_net_progress_mi"] - target_mi) > STOP_MODE_WINDOW_MI, -c["max_kw"]))
    elif stop_mode == "best_amenities":
        candidates.sort(key=lambda c: (not c["_approx_feasible"], abs(c["_net_progress_mi"] - target_mi) > STOP_MODE_WINDOW_MI, -c["connector_count"]))
    else:
        candidates.sort(key=lambda c: (not c["_approx_feasible"], -c["_net_progress_mi"]))

    return _dedupe_by_location(candidates)


def _dedupe_by_location(ranked: list[dict]) -> list[dict]:
    """Collapse multiple OCM entries for the same physical parking lot,
    keeping the best-ranked one (list is already in rank order). Saves
    real ORS verification calls - three co-located Fillmore entries were
    being verified back-to-back against the same answer."""
    kept: list[dict] = []
    for c in ranked:
        # Miles per degree of longitude shrinks toward the poles. The old
        # constant 55 hardcoded cos(lat) for latitude 37, the middle of
        # California: it is 12% too tight in south Texas and 19% too loose on
        # the northern border. Derive it from the candidate instead.
        mi_per_deg_lon = max(1e-6, 69.17 * math.cos(math.radians(c["lat"])))
        dup = any(
            abs(c["lat"] - k["lat"]) < DEDUPE_RADIUS_MI / 69.0
            and abs(c["lon"] - k["lon"]) < DEDUPE_RADIUS_MI / mi_per_deg_lon
            for k in kept
        )
        if not dup:
            kept.append(c)
    return kept


class RateLimited(Exception):
    """ORS's per-minute quota is exhausted and stayed exhausted through the
    backoff. Deliberately distinct from a 404: a rate-limited candidate is
    NOT unroutable, and treating it that way made the planner claim 'no
    charger exists in the Central Valley' on a leg where dozens do - the
    real failure a user hit on a 3-stop LA->SF plan where legs 1-3 had
    already burned most of the minute's quota."""


_RETRY_DELAYS_S = [2, 8, 20]

# Longest any single Overpass-backed hazard check may hold up a plan.
# Enough for a healthy server (rail ~7s, left turns ~14s measured), and
# past it the check degrades to no flags. Paired with the circuit breaker
# in providers: without one, a timed-out check caches nothing and so
# re-pays this cost on every subsequent plan.
POINT_HAZARD_BUDGET_S = 15


async def _safe_directions(origin, destination, avoid_tolls, avoid_highways, avoid_polygons=None, avoid_ferries=False):
    """None on a real ORS 404 (genuinely no route between these two points
    under the current constraints - confirmed via real testing: this
    happens both for a single candidate charger and, less obviously, for
    the main remaining-route call on a later leg, when avoid_highways
    leaves no valid surface-street path from some intermediate stop to the
    final destination). Every call site decides for itself what "no route
    here" means for the plan; this helper only avoids duplicating the
    try/except at each of them.

    A 429 is retried with growing backoff (ORS's directions quota is
    per-minute, so a 20s wait genuinely helps where a 2s one may not) and
    raises RateLimited if the quota never frees up - never None, because
    "no route exists" and "stop asking for a minute" need different plans."""
    for attempt, delay in enumerate([*_RETRY_DELAYS_S, None]):
        try:
            return await providers.directions(origin, destination, avoid_tolls, avoid_highways, avoid_polygons, avoid_ferries=avoid_ferries)
        except httpx.HTTPStatusError as e:
            # 403 is ORS's daily-quota exhaustion ("Quota exceeded") - like a
            # 429 it means "the route probably exists, stop asking", except
            # waiting a few seconds won't help, so no retry.
            if e.response.status_code == 403:
                raise RateLimited()
            if e.response.status_code != 429:
                return None
            if delay is None:
                raise RateLimited()
            await asyncio.sleep(delay)


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
    excluded_station_ids: tuple = (),
    waypoints: tuple = (),  # dicts: {lat, lon, title, hidden} - visited in order, battery passes through
    safety_mode: str = "avoid_quick",  # flag_only | avoid_quick (5min) | avoid_hard (10) | avoid_max (20)
    charger_filter: str = "all",  # all | tesla_only | non_tesla
    arrival_target_pct: float = 0.0,  # arrive at the final destination with at least this much
    passengers: int = 0,  # beyond the driver
    suitcases: int = 0,
    temp_override_f: float | None = None,  # trust the driver's number over the midpoint forecast
    hazard_types: tuple = ("unprotected_left", "wide_crossing", "rail_crossing", "lane_closure"),
    departure_epoch: float | None = None,  # None = leaving now
    max_stint_min: float = 0.0,  # 0 = no break rhythm; else no driving leg longer than this
    preferred_networks: tuple = (),  # empty = any network
    min_charger_kw: float = 20.0,
    avoid_ferries: bool = False,
    # Stage 5's feedback loop: a consumption multiplier learned from the
    # driver's own logged predicted-vs-actual trips (computed client-side,
    # where the logs live). 1.0 = no logged history worth applying. The
    # frontend already biases it to the safe side; the request model clamps
    # it again as defense in depth.
    calibration_factor: float = 1.0,
) -> dict:
    if waypoints:
        # Each waypoint splits the trip into independently-planned sub-trips
        # that get spliced back together, reusing all the multi-stop logic.
        # The battery PASSES THROUGH a waypoint at whatever it arrived with -
        # a Starbucks or an errand isn't a charger, and the old forced-stop
        # behavior that silently assumed a full charge there was a lie.
        points = [origin] + [(w["lat"], w["lon"]) for w in waypoints] + [destination]
        subs = []
        batt = battery_pct
        for i in range(len(points) - 1):
            is_last = i == len(points) - 2
            sub = await plan_trip(
                points[i], points[i + 1], batt, full_range_mi, reserve_pct, reserve_mi,
                charge_to_pct, stop_mode, avoid_tolls, avoid_highways, excluded_station_ids,
                safety_mode=safety_mode, charger_filter=charger_filter,
                arrival_target_pct=arrival_target_pct if is_last else 0.0,
                passengers=passengers, suitcases=suitcases, temp_override_f=temp_override_f,
                hazard_types=hazard_types,
                departure_epoch=departure_epoch, max_stint_min=max_stint_min,
                preferred_networks=preferred_networks, min_charger_kw=min_charger_kw,
                avoid_ferries=avoid_ferries, calibration_factor=calibration_factor,
            )
            subs.append(sub)
            batt = max(sub["arrival_pct"], 1.0)  # a failed sub-trip still chains, feasible=False carries the truth

        stops = list(subs[0]["stops"])
        for i, w in enumerate(waypoints):
            if not w.get("hidden"):
                stops.append({
                    "id": None,
                    "title": w.get("title") or "Your stop",
                    "lat": w["lat"],
                    "lon": w["lon"],
                    "network": "Stop by",
                    "is_supercharger": False,
                    "is_waypoint": True,
                    "max_kw": 0,
                    "arrive_pct": subs[i]["arrival_pct"],
                    "charge_to_pct": subs[i]["arrival_pct"],  # passthrough, no charging assumed
                    "charge_time_min": None,
                    "reachable": subs[i]["feasible"],
                    "stall_count": None,
                    "cost": None,
                    "photo_url": None,
                    # The leg INTO a waypoint is its sub-trip's final hop.
                    "leg_distance_mi": subs[i]["last_leg_distance_mi"],
                    "leg_drive_min": subs[i]["last_leg_drive_min"],
                })
            stops.extend(subs[i + 1]["stops"])

        notes = [s["note"] for s in subs if s["note"]]
        return {
            "distance_mi": round(sum(s["distance_mi"] for s in subs), 1),
            "duration_min": round(sum(s["duration_min"] for s in subs)),
            "geometry": [pt for s in subs for pt in s["geometry"]],
            "reserve_floor_pct": subs[0]["reserve_floor_pct"],
            "feasible": all(s["feasible"] for s in subs),
            "arrival_pct": subs[-1]["arrival_pct"],
            "leeway_mi": subs[-1]["leeway_mi"],
            "stops": stops,
            "note": " ".join(dict.fromkeys(notes)) or None,
            "rate_limited": any(s["rate_limited"] for s in subs),
            "weather": subs[0]["weather"],
            "safety_flags": [f for s in subs for f in s["safety_flags"]],
            "calibration_factor": calibration_factor,
            "last_leg_distance_mi": subs[-1]["last_leg_distance_mi"],
            "last_leg_drive_min": subs[-1]["last_leg_drive_min"],
        }

    stops_out = []
    used_station_ids: set = set()
    verify_calls = 0
    verify_budget_spent = False
    leg_geometries = []
    leg_elevations = []
    # Parallel record of each leg's endpoints + route, kept so the
    # safety-avoidance pass can re-route legs around flagged spots without
    # re-running charger selection.
    leg_records: list[dict] = []
    total_distance_mi = 0.0
    total_duration_min = 0.0

    weather = await _trip_weather(origin, destination, temp_override_f, departure_epoch)
    # One combined consumption adjustment: weather (live or overridden
    # temperature, wind), passenger/luggage load, and the driver's own
    # logged-trip calibration. Threaded through every estimate under the
    # historical name weather_adjustment.
    weather_adjustment = (
        (weather["adjustment"] if weather else 0.0)
        + range_model.load_adjustment_fraction(passengers, suitcases)
        + (calibration_factor - 1.0)
    )

    current_start = origin
    current_pct = battery_pct
    note = None
    rate_limited = False
    # The drive into the destination itself (distance/minutes) - the legs
    # into each charging stop live on the stops; this covers the last hop.
    # Stays None when planning couldn't complete, so the UI never shows a
    # leg the planner didn't actually verify.
    final_leg: dict | None = None

    for _ in range(MAX_STOPS + 1):
        try:
            remaining = await _safe_directions(current_start, destination, avoid_tolls, avoid_highways, avoid_ferries=avoid_ferries)
        except RateLimited:
            note = (
                "The routing provider's usage limit interrupted planning partway - "
                "everything up to here is real, the rest is unplanned. Try again in a little while."
            )
            final_arrival_pct = current_pct
            final_leeway_mi = 0.0
            feasible = False
            rate_limited = True
            break
        if remaining is None:
            note = (
                "No route found from the current point to the destination under these constraints "
                "(e.g. avoiding highways/tolls) - the plan is incomplete past this point."
            )
            final_arrival_pct = current_pct
            final_leeway_mi = 0.0
            feasible = False
            break

        estimate = range_model.estimate_arrival(
            full_range_mi, current_pct, remaining["distance_mi"], remaining["highway_fraction"],
            remaining["ascent_ft"], remaining["descent_ft"], reserve_pct, reserve_mi, weather_adjustment,
        )

        stint_ok = max_stint_min <= 0 or remaining["duration_min"] <= max_stint_min
        if estimate.feasible and estimate.arrival_pct >= arrival_target_pct and stint_ok:
            leg_geometries.append(remaining["geometry"])
            leg_elevations.append(remaining["elevations_m"])
            leg_records.append({"start": current_start, "end": destination, "start_pct": current_pct, "route": remaining})
            total_distance_mi += remaining["distance_mi"]
            total_duration_min += remaining["duration_min"]
            final_arrival_pct = estimate.arrival_pct
            final_leeway_mi = estimate.leeway_mi
            final_leg = {"distance_mi": remaining["distance_mi"], "duration_min": remaining["duration_min"]}
            feasible = True
            break

        # When the break rhythm (not the battery) forces this stop, search
        # for a charger near where the stint runs out rather than where the
        # battery would.
        stint_cap_mi = None
        if max_stint_min > 0 and remaining["duration_min"] > max_stint_min:
            stint_cap_mi = remaining["distance_mi"] * (max_stint_min / remaining["duration_min"])
        chosen = None
        chosen_leg = None
        chosen_leg_estimate = None
        # A wider radius re-returns every station the smaller radius already
        # had - without this, candidates that already failed verification get
        # re-verified at each radius, burning the same rate-limited quota on
        # the same answer.
        already_failed_ids: set = set()

        ocm_failed = False
        try:
            for radius in SEARCH_RADII_MI:
                stations = []
                seen_ids: set = set()
                for search_lat, search_lon in _search_centers(
                    remaining, current_pct, full_range_mi, reserve_pct, reserve_mi, weather_adjustment, radius,
                    cap_mi=stint_cap_mi,
                ):
                    try:
                        found = await providers.find_charging_stations(search_lat, search_lon, radius_mi=radius)
                    except httpx.HTTPError:
                        # A flaky station lookup is "couldn't check here", not
                        # "no chargers here" - remembered so the no-charger note
                        # below can say which one actually happened.
                        ocm_failed = True
                        continue
                    for s in found:
                        if s["id"] not in seen_ids:
                            seen_ids.add(s["id"])
                            stations.append(s)
                if excluded_station_ids:
                    stations = [s for s in stations if s["id"] not in excluded_station_ids]
                if charger_filter == "tesla_only":
                    stations = [s for s in stations if s["is_supercharger"]]
                elif charger_filter == "non_tesla":
                    stations = [s for s in stations if not s["is_supercharger"]]
                if preferred_networks:
                    keys = [n.lower() for n in preferred_networks]
                    stations = [s for s in stations if any(k in s["network"].lower() for k in keys)]
                if min_charger_kw > MIN_CHARGING_KW:
                    stations = [s for s in stations if s["max_kw"] >= min_charger_kw]
                if not stations:
                    continue
                ranked = _rank_candidates(remaining, current_pct, full_range_mi, reserve_pct, reserve_mi, stop_mode, stations, weather_adjustment)
                ranked = [
                    c for c in ranked
                    if c["id"] not in already_failed_ids
                    and c["id"] not in used_station_ids
                    and c["distance_along_route_mi"] >= MIN_STOP_PROGRESS_MI
                ]

                for candidate in ranked[:MAX_CANDIDATES_VERIFIED_PER_RADIUS]:
                    if verify_calls >= MAX_VERIFY_CALLS_PER_PLAN:
                        verify_budget_spent = True
                        break
                    verify_calls += 1
                    leg_to_stop = await _safe_directions(current_start, (candidate["lat"], candidate["lon"]), avoid_tolls, avoid_highways, avoid_ferries=avoid_ferries)
                    if leg_to_stop is None:
                        # A specific candidate can be genuinely unroutable under
                        # the current constraints (e.g. only reachable via a
                        # highway when avoid_highways is on) - ORS returns a real
                        # 404 for that pair. One bad candidate shouldn't kill the
                        # whole plan; just try the next one. Confirmed via a real
                        # avoid_highways request that hit exactly this.
                        already_failed_ids.add(candidate["id"])
                        continue
                    leg_estimate = range_model.estimate_arrival(
                        full_range_mi, current_pct, leg_to_stop["distance_mi"], leg_to_stop["highway_fraction"],
                        leg_to_stop["ascent_ft"], leg_to_stop["descent_ft"], reserve_pct, reserve_mi, weather_adjustment,
                    )
                    stint_leg_ok = max_stint_min <= 0 or leg_to_stop["duration_min"] <= max_stint_min * 1.15
                    if leg_estimate.feasible and stint_leg_ok:
                        chosen, chosen_leg, chosen_leg_estimate = candidate, leg_to_stop, leg_estimate
                        break
                    already_failed_ids.add(candidate["id"])

                if chosen is not None or verify_budget_spent:
                    break
        except RateLimited:
            note = (
                "The routing provider's usage limit interrupted planning partway - "
                "everything up to here is real, the rest is unplanned. Try again in a little while."
            )
            leg_geometries.append(remaining["geometry"])
            leg_elevations.append(remaining["elevations_m"])
            total_distance_mi += remaining["distance_mi"]
            total_duration_min += remaining["duration_min"]
            final_arrival_pct = estimate.arrival_pct
            final_leeway_mi = estimate.leeway_mi
            feasible = False
            rate_limited = True
            break

        if chosen is None:
            if verify_budget_spent:
                note = (
                    "This route needed more charger checks than one plan is allowed to make, "
                    "so planning stopped here rather than stall. Everything above is real. "
                    "Try a shorter trip, or set a higher minimum charger speed to narrow the search."
                )
            elif ocm_failed:
                note = (
                    "The charging-station data source failed while planning this leg - "
                    "there may well be a charger here that couldn't be checked. Try again shortly."
                )
            else:
                note = (
                    f"No fast charger ({MIN_CHARGING_KW}kW or more) checked out as reachable within "
                    f"{SEARCH_RADII_MI[-1]} miles of where the battery would hit reserve. "
                    "Treat everything past that point as unplanned."
                )
            leg_geometries.append(remaining["geometry"])
            leg_elevations.append(remaining["elevations_m"])
            total_distance_mi += remaining["distance_mi"]
            total_duration_min += remaining["duration_min"]
            final_arrival_pct = estimate.arrival_pct
            final_leeway_mi = estimate.leeway_mi
            feasible = False
            break

        leg_geometries.append(chosen_leg["geometry"])
        leg_elevations.append(chosen_leg["elevations_m"])
        leg_records.append({"start": current_start, "end": (chosen["lat"], chosen["lon"]), "start_pct": current_pct, "route": chosen_leg})
        total_distance_mi += chosen_leg["distance_mi"]
        total_duration_min += chosen_leg["duration_min"]

        charge_time_min = range_model.estimate_charge_time_min(
            full_range_mi, chosen_leg_estimate.arrival_pct, charge_to_pct, chosen["max_kw"],
        )
        if charge_time_min:
            total_duration_min += charge_time_min

        stops_out.append({
            "id": chosen["id"],
            "title": chosen["title"],
            "lat": chosen["lat"],
            "lon": chosen["lon"],
            "network": chosen["network"],
            "is_supercharger": chosen["is_supercharger"],
            "is_waypoint": False,
            "max_kw": chosen["max_kw"],
            "arrive_pct": round(chosen_leg_estimate.arrival_pct),
            "charge_to_pct": charge_to_pct,
            "charge_time_min": round(charge_time_min) if charge_time_min else None,
            "reachable": True,  # real-verified above, not the approximation
            "stall_count": chosen.get("stall_count"),
            "cost": chosen.get("cost"),
            "photo_url": chosen.get("photo_url"),
            # The drive INTO this stop from the previous point (origin or the
            # stop before it) - real verified-leg numbers, not a slice.
            "leg_distance_mi": round(chosen_leg["distance_mi"], 1),
            "leg_drive_min": round(chosen_leg["duration_min"]),
        })

        if chosen["id"] is not None:
            used_station_ids.add(chosen["id"])
        current_start = (chosen["lat"], chosen["lon"])
        # A rhythm-forced break can arrive ABOVE the charge target - you
        # don't lose charge by parking, so the next leg starts at whichever
        # is higher.
        current_pct = max(charge_to_pct, chosen_leg_estimate.arrival_pct)
        # Defense in depth behind main.py's request-level check: if charging
        # to the target still can't clear the floor, another stop won't
        # help either - stop here honestly instead of picking the same
        # station until the stop cap.
        if charge_to_pct <= range_model.reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi):
            note = (
                f"Charging to {charge_to_pct:.0f}% doesn't clear your reserve floor, so more "
                "stops can't help - raise the charge-to level or lower the reserve."
            )
            final_arrival_pct = current_pct
            final_leeway_mi = 0.0
            feasible = False
            break
    else:
        note = f"This trip needs more than {MAX_STOPS} charging stops - the plan below stops there rather than loop forever."
        final_arrival_pct = current_pct
        final_leeway_mi = 0.0
        feasible = False

    geometry = [pt for leg in leg_geometries for pt in leg]
    elevations_m = [e for leg in leg_elevations for e in leg]
    point_flags = await _point_hazard_flags(
        geometry, hazard_types, departure_epoch, _leg_anchor_miles(leg_geometries, cumulative_distances_mi(geometry)))

    if feasible and point_flags and safety_mode in AVOID_BUDGET_MIN and leg_records:
        budget_min = AVOID_BUDGET_MIN[safety_mode]
        endpoints = [origin, destination] + [(s["lat"], s["lon"]) for s in stops_out]
        # The endpoint clearance protects arrivals (a charger's own exit
        # lefts must not make the charger unreachable) - but on a short hop
        # the fixed radius swallowed the WHOLE route, so a genuinely
        # avoidable mid-route hazard was never even tried (found live: an
        # unsignaled 7-lane crossing between two addresses a block apart,
        # detour budget set, and the planner still only flagged it). Scale
        # the guard down with trip length; 30m is still "at the doorstep".
        clearance_mi = min(HAZARD_ENDPOINT_CLEARANCE_MI, max(0.02, total_distance_mi * 0.2))
        avoidable = [
            f for f in point_flags
            if all(haversine_mi(f["lat"], f["lon"], la, lo) > clearance_mi for la, lo in endpoints)
        ]
        if avoidable:
            reroute = await _try_avoid_hazards(
                leg_records, avoidable, avoid_tolls, avoid_highways,
                full_range_mi, reserve_pct, reserve_mi, charge_to_pct, weather_adjustment,
                avoid_ferries,
            )
            if reroute is None:
                note = _append_note(note, (
                    f"Tried to route around {len(avoidable)} flagged spot(s) but no workable "
                    "detour exists - they stay flagged below."
                ))
            elif reroute["added_min"] > budget_min:
                note = _append_note(note, (
                    f"Routing around {len(avoidable)} flagged spot(s) would add "
                    f"~{round(reroute['added_min'])} min - over your {round(budget_min)}-min cap, "
                    "so the direct route stays."
                ))
            else:
                leg_geometries = [r["geometry"] for r in reroute["routes"]]
                leg_elevations = [r["elevations_m"] for r in reroute["routes"]]
                geometry = [pt for leg in leg_geometries for pt in leg]
                elevations_m = [e for leg in leg_elevations for e in leg]
                total_distance_mi = sum(r["distance_mi"] for r in reroute["routes"])
                charge_total_min = 0.0
                for i, (stop, arrive_pct) in enumerate(zip(stops_out, reroute["stop_arrive_pcts"])):
                    charge_min = range_model.estimate_charge_time_min(
                        full_range_mi, arrive_pct, charge_to_pct, stop["max_kw"],
                    )
                    stop["arrive_pct"] = round(arrive_pct)
                    stop["charge_time_min"] = round(charge_min) if charge_min else None
                    # The detour changed the legs - the per-stop leg numbers
                    # must describe the rerouted drive, not the original.
                    stop["leg_distance_mi"] = round(reroute["routes"][i]["distance_mi"], 1)
                    stop["leg_drive_min"] = round(reroute["routes"][i]["duration_min"])
                    charge_total_min += charge_min or 0.0
                final_leg = {
                    "distance_mi": reroute["routes"][-1]["distance_mi"],
                    "duration_min": reroute["routes"][-1]["duration_min"],
                }
                total_duration_min = reroute["drive_min"] + charge_total_min
                final_arrival_pct = reroute["final_arrival_pct"]
                final_leeway_mi = reroute["final_leeway_mi"]
                note = _append_note(note, (
                    f"Rerouted around {len(avoidable)} flagged spot(s) for about "
                    f"+{max(1, round(reroute['added_min']))} min."
                ))
                point_flags = await _point_hazard_flags(
        geometry, hazard_types, departure_epoch, _leg_anchor_miles(leg_geometries, cumulative_distances_mi(geometry)))

    safety_flags = _static_safety_flags(origin, destination, geometry, elevations_m, weather, departure_epoch) + point_flags

    return {
        "distance_mi": round(total_distance_mi, 1),
        "duration_min": round(total_duration_min),  # driving + estimated charge time, see range_model.estimate_charge_time_min
        "geometry": geometry,
        "reserve_floor_pct": round(range_model.reserve_floor_pct(full_range_mi, reserve_pct, reserve_mi)),
        "feasible": feasible,
        # whole percentages and miles - decimal precision here implied an
        # accuracy the range math doesn't actually have
        "arrival_pct": round(final_arrival_pct),
        "leeway_mi": round(final_leeway_mi),
        "stops": stops_out,
        "note": note,
        "rate_limited": rate_limited,  # true = retrying in a minute may fully fix this plan
        "weather": weather,  # None if the weather fetch failed - see _trip_weather
        "safety_flags": safety_flags,
        # Echoed back so the UI (and anyone reading the response) can see the
        # learned adjustment was actually applied, not silently dropped.
        "calibration_factor": calibration_factor,
        # The last hop (into the destination); per-stop hops ride on stops.
        "last_leg_distance_mi": round(final_leg["distance_mi"], 1) if final_leg else None,
        "last_leg_drive_min": round(final_leg["duration_min"]) if final_leg else None,
    }


def _append_note(note: str | None, extra: str) -> str:
    return f"{note} {extra}" if note else extra


_KM_PATTERNS = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*miles\b"), lambda m: f"{round(float(m.group(1)) * 1.609)} km"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*mi\b"), lambda m: f"{round(float(m.group(1)) * 1.609)} km"),
    (re.compile(r"\bmile\s+(\d+)"), lambda m: f"km {round(float(m.group(1)) * 1.609)}"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*mph\b"), lambda m: f"{round(float(m.group(1)) * 1.609)} km/h"),
]

# Separate from distance: someone can think in miles but °C (or km and °F).
# -? matters: "-30°F" without it converts only the "30" and renders "--1°C"
_C_PATTERNS = [
    (re.compile(r"(-?\d+(?:\.\d+)?)\s*°F"), lambda m: f"{round((float(m.group(1)) - 32) * 5 / 9)}°C"),
]


def _localize(plan: dict, patterns: list) -> dict:
    """Convert units inside narrative strings (notes, flag descriptions,
    weather summaries). Numeric fields stay in miles - the frontend owns
    numeric display and converts them itself; this exists because sentences
    like 'descent over 5.1 mi' are composed server-side and a km driver
    shouldn't have to read them in miles. The templates are all authored in
    this codebase, so the patterns are a closed set."""
    def conv(text: str | None) -> str | None:
        if not text:
            return text
        for pat, repl in patterns:
            text = pat.sub(repl, text)
        return text

    plan["note"] = conv(plan["note"])
    for f in plan["safety_flags"]:
        f["description"] = conv(f["description"])
    if plan.get("weather"):
        plan["weather"]["summary"] = conv(plan["weather"]["summary"])
    return plan


def localize_km(plan: dict) -> dict:
    return _localize(plan, _KM_PATTERNS)


def localize_c(plan: dict) -> dict:
    return _localize(plan, _C_PATTERNS)


def _leg_anchor_miles(leg_geometries: list[list[tuple[float, float]]],
                      cum: list[float]) -> list[float]:
    """Distances along the whole trip where a leg starts or ends: the origin,
    every charging stop and waypoint, and the destination. These are the only
    places a driver is on surface streets, so they are where the hazard search
    is worth spending on a long trip."""
    if not cum:
        return []
    anchors = [0.0]
    idx = 0
    for leg in leg_geometries[:-1]:
        idx += len(leg)
        if 0 <= idx < len(cum):
            anchors.append(cum[idx])
    anchors.append(cum[-1])
    return anchors


async def _point_hazard_flags(geometry: list[tuple[float, float]], hazard_types: tuple,
                               departure_epoch: float | None = None,
                               anchors_mi: list[float] | None = None) -> list[dict]:
    """The Overpass-backed point hazards, each type individually opt-outable
    (unprotected lefts, unsignaled wide-road crossings, rail crossings).
    Separate from the static flags because the safety-avoidance pass needs
    these BEFORE deciding whether to re-route, and again after. Degrades to
    no-flags when Overpass is down, never to a failed plan."""
    if len(geometry) < 2:
        return []
    cum = cumulative_distances_mi(geometry)
    checks = []
    if "unprotected_left" in hazard_types:
        checks.append(crossings.unprotected_left_flags(geometry, cum, anchors_mi))
    if "wide_crossing" in hazard_types:
        checks.append(crossings.wide_crossing_flags(geometry, cum, anchors_mi))
    if "rail_crossing" in hazard_types:
        checks.append(crossings.rail_crossing_flags(geometry, cum, anchors_mi))
    if "lane_closure" in hazard_types:
        checks.append(crossings.lane_closure_flags(geometry, cum, departure_epoch))
    if not checks:
        return []

    # Hard ceiling per check: the flags are a bonus layer the plan must
    # never wait on. overpass_raw already bounds each attempt, but its full
    # retry chain across a struggling primary and a hanging mirror can still
    # add up to a minute (seen live: 50-100s plans that were 100% Overpass
    # retries) - past the budget, that check degrades to no flags.
    async def bounded(check):
        try:
            return await asyncio.wait_for(check, POINT_HAZARD_BUDGET_S)
        except asyncio.TimeoutError:
            # Tell the breaker. wait_for CANCELS the request mid-flight, so
            # overpass_raw never reaches its own failure counter - without
            # this the breaker never opens on the one symptom that matters
            # most, and every plan keeps paying the full budget. Measured in
            # production before this line existed: 32.9s then 15.3s, the
            # second landing exactly on the budget.
            providers.note_overpass_failure()
            return []

    results = await asyncio.gather(*(bounded(c) for c in checks))
    return [f for group in results for f in group]


def _static_safety_flags(origin: tuple[float, float], destination: tuple[float, float],
                          geometry: list[tuple[float, float]], elevations_m: list[float],
                          weather: dict | None, departure_epoch: float | None = None) -> list[dict]:
    """Steep descents (real elevation, most severe first), a sun-glare check
    from the trip bearing, and a strong-wind flag from the same weather
    snapshot the range math uses. No network calls."""
    if len(geometry) < 2:
        return []
    cum = cumulative_distances_mi(geometry)
    flags = safety.find_steep_descents(geometry, elevations_m, cum)
    flags.sort(key=lambda f: -(f["grade_pct"] * f["length_mi"]))

    flags.extend(safety.find_twisty_sections(geometry, cum))

    bearing = bearing_deg(origin[0], origin[1], destination[0], destination[1])
    mid_lat, mid_lon = (origin[0] + destination[0]) / 2, (origin[1] + destination[1]) / 2
    glare_time = (
        datetime.fromtimestamp(departure_epoch, tz=timezone.utc) if departure_epoch else datetime.now(timezone.utc)
    )
    glare = sun.check_sun_glare(mid_lat, mid_lon, glare_time, bearing)
    if glare:
        flags.append(glare)

    if weather and weather.get("wind_speed_mph", 0) >= 25:
        flags.append({
            "kind": "strong_wind",
            "description": (
                f"{round(weather['wind_speed_mph'])} mph winds today - the range math already "
                "accounts for the head/tailwind component, but expect buffeting, especially "
                "in gusts and around trucks."
            ),
            "lat": None,
            "lon": None,
        })
    return flags


def _avoid_box(lat: float, lon: float) -> list[list[float]]:
    """Square GeoJSON ring around a point, AVOID_BOX_HALF_M half-size."""
    import math
    dlat = AVOID_BOX_HALF_M / 111_000.0
    dlon = dlat / max(0.2, math.cos(math.radians(lat)))
    return [
        [lon - dlon, lat - dlat],
        [lon + dlon, lat - dlat],
        [lon + dlon, lat + dlat],
        [lon - dlon, lat + dlat],
        [lon - dlon, lat - dlat],
    ]


async def _avoid_route(rec: dict, polygons: list, avoid_tolls: bool,
                        avoid_highways: bool, avoid_ferries: bool) -> dict | None:
    """One leg, re-routed with the no-go boxes in place. Legs longer than
    ORS_AVOID_POLYGON_MAX_MI are split at real points on their own geometry
    and each piece routed for real, then joined. Every number in the result
    is a routing result - nothing is a proportional slice of the whole, which
    is the mistake this module's docstring exists to warn about."""
    original = rec["route"]
    total_mi = original["distance_mi"]
    if total_mi <= ORS_AVOID_POLYGON_MAX_MI:
        return await _safe_directions(rec["start"], rec["end"], avoid_tolls,
                                      avoid_highways, polygons, avoid_ferries=avoid_ferries)

    pieces_n = -(-int(total_mi) // int(ORS_AVOID_POLYGON_MAX_MI))
    coords = original["geometry"]
    cum = cumulative_distances_mi(coords)
    # Split points taken off the original polyline, so each piece follows the
    # route the driver was already going to take.
    waypoints = [rec["start"]]
    for i in range(1, pieces_n):
        lon, lat = point_at_distance(coords, cum, total_mi * i / pieces_n)
        waypoints.append((lat, lon))
    waypoints.append(rec["end"])

    legs = []
    for a, b in zip(waypoints, waypoints[1:]):
        piece = await _safe_directions(a, b, avoid_tolls, avoid_highways,
                                       polygons, avoid_ferries=avoid_ferries)
        if piece is None:
            return None
        legs.append(piece)
    return _join_routes(legs)


def _join_routes(legs: list[dict]) -> dict:
    """Concatenate real route pieces into one leg. highway_fraction is
    distance-weighted; everything else adds."""
    distance = sum(l["distance_mi"] for l in legs)
    geometry: list = []
    elevations: list = []
    for l in legs:
        # drop the duplicated joint vertex
        geometry.extend(l["geometry"] if not geometry else l["geometry"][1:])
        elevations.extend(l["elevations_m"] if not elevations else l["elevations_m"][1:])
    return {
        "distance_mi": distance,
        "duration_min": sum(l["duration_min"] for l in legs),
        "ascent_ft": sum(l["ascent_ft"] for l in legs),
        "descent_ft": sum(l["descent_ft"] for l in legs),
        "highway_fraction": (
            sum(l["highway_fraction"] * l["distance_mi"] for l in legs) / distance
            if distance else 0.0
        ),
        "geometry": geometry,
        "elevations_m": elevations,
    }


async def _try_avoid_hazards(leg_records: list[dict], hazards: list[dict],
                              avoid_tolls: bool, avoid_highways: bool,
                              full_range_mi: float, reserve_pct: float, reserve_mi: float,
                              charge_to_pct: float, weather_adjustment: float,
                              avoid_ferries: bool = False) -> dict | None:
    """Re-route every leg with no-go boxes over the flagged spots, then
    re-run the full range math on the new legs. Returns the replacement
    plan pieces plus how many minutes it costs - the caller owns the
    keep-or-revert decision against the user's time budget. None when any
    leg becomes unroutable or infeasible, or the reroute gets rate limited:
    the direct route plus warnings is better than a broken detour."""
    polygons = [_avoid_box(h["lat"], h["lon"]) for h in hazards]

    routes = []
    stop_arrive_pcts = []
    final_est = None
    try:
        for rec in leg_records:
            new_route = await _avoid_route(rec, polygons, avoid_tolls, avoid_highways, avoid_ferries)
            if new_route is None:
                return None
            est = range_model.estimate_arrival(
                full_range_mi, rec["start_pct"], new_route["distance_mi"], new_route["highway_fraction"],
                new_route["ascent_ft"], new_route["descent_ft"], reserve_pct, reserve_mi, weather_adjustment,
            )
            if not est.feasible:
                return None
            routes.append(new_route)
            final_est = est
            if rec is not leg_records[-1]:
                stop_arrive_pcts.append(est.arrival_pct)
    except RateLimited:
        return None

    old_drive = sum(r["route"]["duration_min"] for r in leg_records)
    new_drive = sum(r["duration_min"] for r in routes)

    return {
        "routes": routes,
        "added_min": new_drive - old_drive,
        "drive_min": new_drive,
        "stop_arrive_pcts": stop_arrive_pcts,
        "final_arrival_pct": round(final_est.arrival_pct, 1),
        "final_leeway_mi": round(final_est.leeway_mi, 1),
    }


def _search_point(route: dict, start_pct: float, full_range_mi: float, reserve_pct: float, reserve_mi: float, weather_adjustment: float = 0.0):
    coords = route["geometry"]
    cum = cumulative_distances_mi(coords)
    target_mi = range_model.distance_to_floor_mi_elevation_aware(
        cum, route["elevations_m"], full_range_mi, start_pct, route["highway_fraction"], reserve_pct, reserve_mi, weather_adjustment,
    )
    lon, lat = point_at_distance(coords, cum, target_mi)
    return lat, lon


def _search_centers(route: dict, start_pct: float, full_range_mi: float, reserve_pct: float,
                     reserve_mi: float, weather_adjustment: float, radius_mi: float,
                     cap_mi: float | None = None) -> list[tuple[float, float]]:
    """Where to look for chargers: at the point the battery would hit the
    reserve floor, AND a second center back along the route. The second one
    matters twice over: stations just before the floor point arrive with
    real margin instead of straddling the floor, and OCM caps results at
    the nearest N - in a dense area (found for real around Livermore), a
    wider radius just returns the same nearest stations again, so widening
    the search means moving its center, not only its edge."""
    coords = route["geometry"]
    cum = cumulative_distances_mi(coords)
    target_mi = range_model.distance_to_floor_mi_elevation_aware(
        cum, route["elevations_m"], full_range_mi, start_pct, route["highway_fraction"], reserve_pct, reserve_mi, weather_adjustment,
    )
    if cap_mi is not None:
        target_mi = min(target_mi, cap_mi)
    centers = [target_mi]
    back_mi = target_mi - radius_mi * 0.9
    if back_mi > 5:
        centers.append(back_mi)
    out = []
    for mi in centers:
        lon, lat = point_at_distance(coords, cum, mi)
        out.append((lat, lon))
    return out


async def _trip_weather(origin: tuple[float, float], destination: tuple[float, float],
                         temp_override_f: float | None = None,
                         departure_epoch: float | None = None) -> dict | None:
    """One trip-day snapshot at the route's rough midpoint - not per-leg or
    per-segment (weather varies continuously anyway, so a single snapshot is
    already the honest precision level here). Weather is a nice-to-have
    honesty add-on, not core routing infra, so a fetch failure degrades to
    "no adjustment" rather than failing the whole plan - unless the driver
    supplied their own temperature, which still counts on its own.

    temp_override_f replaces the forecast temperature (the driver's number
    wins - they can see their car's thermometer) while wind stays live."""
    try:
        mid_lat = (origin[0] + destination[0]) / 2
        mid_lon = (origin[1] + destination[1]) / 2
        weather = await providers.current_weather(mid_lat, mid_lon, departure_epoch)
    except httpx.HTTPError:
        weather = None

    if weather is None and temp_override_f is None:
        return None

    if weather is None:
        temp_f, headwind, wind_speed = temp_override_f, 0.0, 0.0
    else:
        temp_f = temp_override_f if temp_override_f is not None else weather["temp_f"]
        bearing = bearing_deg(origin[0], origin[1], destination[0], destination[1])
        headwind = headwind_component_mph(weather["wind_speed_mph"], weather["wind_from_deg"], bearing)
        wind_speed = weather["wind_speed_mph"]

    summary = range_model.describe_weather(temp_f, headwind)
    if temp_override_f is not None:
        summary += " (your temperature)"
    elif weather is not None and weather.get("forecast"):
        summary += " (forecast for your departure)"
    return {
        "adjustment": range_model.weather_adjustment_fraction(temp_f, headwind),
        "summary": summary,
        "temp_f": temp_f,
        "headwind_mph": round(headwind, 1),
        "wind_speed_mph": wind_speed,  # raw speed for the strong-wind safety flag
    }
