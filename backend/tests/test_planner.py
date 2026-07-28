from app import planner
from app.geo import haversine_mi
from conftest import LA, SF, run

CAR = dict(full_range_mi=205.0, battery_pct=90.0)


def _plan(world, origin=LA, destination=SF, **kw):
    args = {**CAR, **kw}
    return run(planner.plan_trip(origin, destination, **args))


def test_short_trip_no_stops(world):
    dest = (34.9, -118.24)  # ~59mi north of LA
    plan = _plan(world, destination=dest)
    assert plan["feasible"]
    assert plan["stops"] == []
    assert plan["arrival_pct"] >= plan["reserve_floor_pct"]
    assert plan["leeway_mi"] > 0
    assert plan["weather"] is not None


def test_long_trip_gets_real_multi_stop_plan(world):
    world.stations_along(LA, SF, every_mi=40)
    plan = _plan(world)
    assert plan["feasible"]
    assert 1 <= len(plan["stops"]) <= 4
    floor = plan["reserve_floor_pct"]
    for stop in plan["stops"]:
        assert stop["reachable"]
        assert stop["arrive_pct"] >= floor - 1  # rounded to whole %
        assert stop["charge_to_pct"] == 80
        assert stop["charge_time_min"] and stop["charge_time_min"] > 0
    assert plan["arrival_pct"] >= floor
    # Stops come back in driving order
    miles = [haversine_mi(LA[0], LA[1], s["lat"], s["lon"]) for s in plan["stops"]]
    assert miles == sorted(miles)


def test_every_hop_reports_distance_and_drive_time(world):
    """Each stop carries the real drive INTO it; the plan carries the last
    hop into the destination - and the hops must sum to the whole trip."""
    world.stations_along(LA, SF, every_mi=40)
    plan = _plan(world)
    assert plan["feasible"] and plan["stops"]
    for stop in plan["stops"]:
        assert stop["leg_distance_mi"] > 0
        assert stop["leg_drive_min"] > 0
    assert plan["last_leg_distance_mi"] > 0
    assert plan["last_leg_drive_min"] > 0
    hops = sum(s["leg_distance_mi"] for s in plan["stops"]) + plan["last_leg_distance_mi"]
    assert abs(hops - plan["distance_mi"]) < 1
    # The fake world drives at 60mph, so minutes ~= miles per hop
    for s in plan["stops"]:
        assert abs(s["leg_drive_min"] - s["leg_distance_mi"]) <= 2


def test_incomplete_plan_reports_no_last_leg(world):
    plan = _plan(world)  # zero stations: planning stops partway
    assert not plan["feasible"]
    assert plan["last_leg_distance_mi"] is None
    assert plan["last_leg_drive_min"] is None


def test_no_chargers_is_honest_not_fake_feasible(world):
    plan = _plan(world)  # zero stations anywhere
    assert not plan["feasible"]
    assert "No fast charger" in plan["note"]
    assert not plan["rate_limited"]


def test_ocm_outage_reported_as_outage_not_absence(world):
    world.fail_ocm = True
    plan = _plan(world)
    assert not plan["feasible"]
    assert "data source failed" in plan["note"]


def test_rate_limit_interrupts_honestly(world):
    world.directions_script = [403]  # daily quota gone on the first call
    plan = _plan(world)
    assert not plan["feasible"]
    assert plan["rate_limited"]
    assert "usage limit" in plan["note"]


def test_429_retries_then_succeeds(world):
    dest = (34.9, -118.24)
    world.directions_script = [429, 429, None]
    plan = _plan(world, destination=dest)
    assert plan["feasible"]
    assert not plan["rate_limited"]


def test_charger_filters(world):
    world.stations_along(LA, SF, every_mi=40, network="Tesla (Supercharger)", is_supercharger=True)
    world.stations_along(LA, SF, every_mi=37, network="Electrify America", is_supercharger=False, max_kw=150)

    tesla = _plan(world, charger_filter="tesla_only")
    assert tesla["feasible"]
    assert all(s["is_supercharger"] for s in tesla["stops"])

    non_tesla = _plan(world, charger_filter="non_tesla")
    assert non_tesla["feasible"]
    assert all(not s["is_supercharger"] for s in non_tesla["stops"])

    ea_only = _plan(world, preferred_networks=("Electrify America",))
    assert ea_only["feasible"]
    assert all(s["network"] == "Electrify America" for s in ea_only["stops"])


def test_min_charger_kw_filter(world):
    world.stations_along(LA, SF, every_mi=40, max_kw=50)
    world.stations_along(LA, SF, every_mi=55, max_kw=250)
    plan = _plan(world, min_charger_kw=150)
    assert plan["feasible"]
    assert all(s["max_kw"] >= 150 for s in plan["stops"])


def test_skip_stop_excludes_station(world):
    world.stations_along(LA, SF, every_mi=40)
    first = _plan(world)
    assert first["feasible"] and first["stops"]
    skipped_id = first["stops"][0]["id"]

    second = _plan(world, excluded_station_ids=(skipped_id,))
    assert second["feasible"]
    assert skipped_id not in [s["id"] for s in second["stops"]]


def test_break_rhythm_forces_early_stop_and_keeps_charge(world):
    """A 30-minute stint on a 100-mile trip must stop around mile 30 even
    though the battery could go much further - and arriving above the
    charge-to target must NOT pretend parking drained it to 80%."""
    dest = (35.5, -118.24)  # ~100mi north
    world.stations_along(LA, dest, every_mi=10)
    plan = _plan(world, destination=dest, battery_pct=100.0, max_stint_min=30.0)
    assert plan["feasible"]
    assert plan["stops"], "the rhythm must force at least one stop"
    first_stop_mi = haversine_mi(LA[0], LA[1], plan["stops"][0]["lat"], plan["stops"][0]["lon"])
    # 30min at 60mph = 30 miles (candidate legs get 15% slack)
    assert first_stop_mi <= 35
    assert plan["stops"][0]["arrive_pct"] > 80


def test_waypoint_passes_battery_through(world):
    """An errand stop is not a charger: the battery arrives at X% and
    leaves at X%."""
    dest = (35.5, -118.24)
    waypoint = {"lat": 34.7, "lon": -118.24, "title": "Errand", "hidden": False}
    plan = _plan(world, destination=dest, waypoints=(waypoint,))
    assert plan["feasible"]
    wp_stops = [s for s in plan["stops"] if s.get("is_waypoint")]
    assert len(wp_stops) == 1
    assert wp_stops[0]["charge_to_pct"] == wp_stops[0]["arrive_pct"]
    assert wp_stops[0]["title"] == "Errand"
    # The hop INTO the waypoint is its sub-trip's final leg (~45mi here)
    assert 40 < wp_stops[0]["leg_distance_mi"] < 50
    assert wp_stops[0]["leg_drive_min"] > 0


def test_arrival_target_adds_a_late_stop(world):
    dest = (35.5, -118.24)  # ~100mi: fine at 90% unless you must arrive full-ish
    world.stations_along(LA, dest, every_mi=10)
    base = _plan(world, destination=dest)
    assert base["feasible"] and not base["stops"]

    demanding = _plan(world, destination=dest, arrival_target_pct=60.0)
    assert demanding["feasible"]
    assert demanding["stops"], "hitting a 60% arrival target on this trip needs a charge"
    assert demanding["arrival_pct"] >= 60


def test_stop_cap_is_honest(world):
    # Beyond MAX_STOPS the plan must stop and say so rather than loop. The
    # range here is deliberately absurd: MAX_STOPS rose to 10 so that a real
    # degraded battery (~150 mi) can finish 600 miles, and this case has to
    # stay past the cap to keep testing the cap.
    world.stations_along(LA, SF, every_mi=10)
    plan = _plan(world, full_range_mi=35.0, battery_pct=100.0, reserve_mi=2.0, reserve_pct=10.0)
    assert not plan["feasible"]
    assert "more than" in plan["note"]


def test_charge_to_below_floor_stops_honestly(world):
    world.stations_along(LA, SF, every_mi=40)
    # floor = max(15, 30/205*100) = 15; charging only to 14% can't make progress
    plan = _plan(world, charge_to_pct=14.0)
    assert not plan["feasible"]
    assert "reserve floor" in plan["note"]


def test_weather_flows_into_the_estimate(world):
    dest = (35.5, -118.24)
    mild = _plan(world, destination=dest)
    world.weather = {"temp_f": 20.0, "wind_speed_mph": 30.0, "wind_from_deg": 0.0, "forecast": False}
    # Wind from the north on a northbound trip = headwind; freezing cold
    brutal = _plan(world, destination=dest)
    assert brutal["arrival_pct"] < mild["arrival_pct"]
    assert brutal["weather"]["summary"] != mild["weather"]["summary"]


def test_load_costs_range(world):
    dest = (35.5, -118.24)
    empty = _plan(world, destination=dest)
    loaded = _plan(world, destination=dest, passengers=4, suitcases=4)
    assert loaded["arrival_pct"] < empty["arrival_pct"]


def test_calibration_factor_flows_into_the_estimate(world):
    """Stage 5's feedback loop: a hungrier-than-stock car (factor > 1) must
    arrive with less, and the factor must be echoed back, not silently
    dropped."""
    dest = (35.5, -118.24)
    stock = _plan(world, destination=dest)
    hungry = _plan(world, destination=dest, calibration_factor=1.15)
    assert hungry["arrival_pct"] < stock["arrival_pct"]
    assert hungry["calibration_factor"] == 1.15
    assert stock["calibration_factor"] == 1.0
    # ~15% more consumption on a ~55%-of-battery trip is ~8 points
    assert 5 <= stock["arrival_pct"] - hungry["arrival_pct"] <= 12


def test_calibration_factor_survives_waypoint_split(world):
    dest = (35.5, -118.24)
    waypoint = {"lat": 34.7, "lon": -118.24, "title": "Errand", "hidden": False}
    plan = _plan(world, destination=dest, waypoints=(waypoint,), calibration_factor=1.1)
    assert plan["calibration_factor"] == 1.1


def test_short_trip_hazard_still_gets_the_detour_attempt(world):
    """Found live: two addresses a block apart across an unsignaled 7-lane
    road, detour budget set to 10 min - and the planner only flagged it,
    because the fixed 0.15mi endpoint-clearance guard swallowed the entire
    short route. The guard must scale down with trip length so a mid-route
    hazard stays avoidable."""
    origin = (34.0, -118.0)
    destination = (34.0036, -118.0)  # ~0.25 mi due north
    # An unsignaled east-west 7-lane artery crossing the route's midpoint
    world.overpass_result = {
        "elements": [{
            "type": "way",
            "tags": {"highway": "primary", "name": "Big Blvd", "lanes": "7"},
            "geometry": [{"lat": 34.0018, "lon": -118.01}, {"lat": 34.0018, "lon": -117.99}],
        }]
    }

    flagged_only = run(planner.plan_trip(origin, destination, **CAR, safety_mode="flag_only"))
    assert any(f.get("kind") == "wide_crossing" for f in flagged_only["safety_flags"])
    assert not (flagged_only["note"] or "").startswith("Rerouted")

    avoided = run(planner.plan_trip(origin, destination, **CAR, safety_mode="avoid_hard"))
    # The fake router returns the same route for the detour (0 added min),
    # so the reroute must be ATTEMPTED and accepted - before the fix the
    # note stayed empty because the hazard was never considered avoidable.
    assert avoided["note"] and "Rerouted around 1 flagged spot" in avoided["note"]

    # All three budget tiers reach the avoidance pass - including the
    # default (avoid_quick, 5 min) and the widest (avoid_max, 20 min)
    for mode in ("avoid_quick", "avoid_max"):
        p = run(planner.plan_trip(origin, destination, **CAR, safety_mode=mode))
        assert p["note"] and "Rerouted around" in p["note"], mode


def test_localize_km_and_c():
    plan = {
        "note": "No fast charger within 50 miles of mile 30.",
        "safety_flags": [{"description": "descent over 5.1 mi at 74°F"}],
        "weather": {"summary": "74°F, light headwind"},
    }
    km = planner.localize_km(dict(plan, safety_flags=[dict(plan["safety_flags"][0])], weather=dict(plan["weather"])))
    assert "80 km" in km["note"]
    assert "km 48" in km["note"]
    assert "8 km" in km["safety_flags"][0]["description"]

    c = planner.localize_c(dict(plan, safety_flags=[dict(plan["safety_flags"][0])], weather=dict(plan["weather"])))
    assert "23°C" in c["weather"]["summary"]


# --- sparse-charging regions (the interior West) ---------------------------

def test_a_charger_at_your_feet_is_not_a_stop(world):
    """The Denver -> Salt Lake City failure: with nothing reachable ahead, the
    planner chose a Supercharger at its own current position. That verifies
    trivially, charges to 80%, and the loop repeats having moved zero miles.
    It burned all seven stop slots and stalled at 360 of 520 miles, listing
    the same Wyoming Supercharger twice in a row."""
    far = (41.5, -118.24)  # ~515 mi north of LA, well beyond one charge
    # One good charger partway, plus a decoy sitting right on the origin.
    world.add_station(LA[0] + 0.001, LA[1] + 0.001, max_kw=250)
    world.add_station(35.5, -118.24, max_kw=250)

    plan = _plan(world, destination=far)

    chosen = [(s["lat"], s["lon"]) for s in plan["stops"]]
    assert len(chosen) == len(set(chosen)), f"same stop chosen twice: {chosen}"
    for s in plan["stops"]:
        assert haversine_mi(LA[0], LA[1], s["lat"], s["lon"]) > planner.MIN_STOP_PROGRESS_MI, \
            "chose a charger that does not advance the route"


def test_no_station_is_used_twice_in_one_plan(world):
    world.stations_along(LA, SF, every_mi=40)
    plan = _plan(world)
    ids = [s["id"] for s in plan["stops"] if s["id"] is not None]
    assert len(ids) == len(set(ids)), f"repeated station ids: {ids}"


def test_hazard_avoidance_survives_a_leg_longer_than_the_ors_polygon_cap(world):
    """ORS refuses avoid_polygons past ~150km and _safe_directions reads the
    refusal as "no route", so the whole avoidance pass used to give up and
    tell the driver no detour existed. A 205-mile car runs 110-125 miles
    between stops, so that was most legs of most real trips - the headline
    feature was quietly off outside short hops."""
    origin = (34.0, -118.0)
    destination = (35.7, -118.0)  # ~117 mi, comfortably over the cap
    mid_lat = (origin[0] + destination[0]) / 2
    world.overpass_result = {
        "elements": [{
            "type": "way",
            "tags": {"highway": "primary", "name": "Big Blvd", "lanes": "7"},
            "geometry": [{"lat": mid_lat, "lon": -118.01}, {"lat": mid_lat, "lon": -117.99}],
        }]
    }
    world.stations_along(origin, destination, every_mi=40)

    plan = run(planner.plan_trip(origin, destination, **CAR, safety_mode="avoid_hard"))

    assert plan["feasible"]
    note = plan["note"] or ""
    assert "Rerouted around" in note, f"avoidance never ran on a long leg: {note!r}"
    assert "no workable detour" not in note


def test_joined_route_pieces_add_up(world):
    """A split leg must report real summed numbers, not a proportional slice
    of the original - the mistake this module's docstring warns about."""
    a = {"distance_mi": 40.0, "duration_min": 45.0, "ascent_ft": 100.0, "descent_ft": 50.0,
         "highway_fraction": 1.0, "geometry": [(-118.0, 34.0), (-118.0, 34.5)],
         "elevations_m": [10.0, 20.0]}
    b = {"distance_mi": 60.0, "duration_min": 60.0, "ascent_ft": 200.0, "descent_ft": 25.0,
         "highway_fraction": 0.5, "geometry": [(-118.0, 34.5), (-118.0, 35.0)],
         "elevations_m": [20.0, 30.0]}
    j = planner._join_routes([a, b])
    assert j["distance_mi"] == 100.0
    assert j["duration_min"] == 105.0
    assert j["ascent_ft"] == 300.0 and j["descent_ft"] == 75.0
    assert abs(j["highway_fraction"] - 0.7) < 1e-9, "must be distance-weighted"
    assert j["geometry"] == [(-118.0, 34.0), (-118.0, 34.5), (-118.0, 35.0)], "joint not deduped"
    assert len(j["elevations_m"]) == len(j["geometry"])


def test_a_plan_cannot_spend_unbounded_routing_calls(world, monkeypatch):
    """ORS allows 40 directions calls a minute and the backoff for exceeding
    it outlasts Render's ~100s proxy, so an unbounded plan spends the quota
    AND still shows "planning took too long". The budget turns that into a
    partial plan with a note."""
    monkeypatch.setattr(planner, "MAX_VERIFY_CALLS_PER_PLAN", 5)
    origin, destination = (34.0, -118.0), (40.0, -118.0)  # ~414 mi
    # Plenty of on-route chargers that rank fine but all sit beyond what this
    # battery can reach, so every one of them costs a verification call and
    # none is ever accepted.
    for i in range(60):
        world.add_station(34.5 + i * 0.02, -118.0, max_kw=50)

    plan = run(planner.plan_trip(origin, destination, full_range_mi=60.0, battery_pct=100.0))

    assert len(world.directions_calls) < 40, "spent more calls than the budget allows"
    assert not plan["feasible"]
    assert "more charger checks than one plan is allowed" in (plan["note"] or "")


def test_eight_stops_available_for_a_badly_degraded_battery(world):
    """600 miles in a car down to ~150 real miles needs more than six stops.
    Six silently truncated the plan for exactly the driver this app is for."""
    assert planner.MAX_STOPS >= 10
    origin, destination = (34.0, -118.0), (42.7, -118.0)  # ~600 mi
    world.stations_along(origin, destination, every_mi=30)
    plan = run(planner.plan_trip(origin, destination, full_range_mi=150.0, battery_pct=100.0))
    assert plan["feasible"], plan.get("note")
    assert len(plan["stops"]) > 6, f"only {len(plan['stops'])} stops used"
