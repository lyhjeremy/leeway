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
    # A 600-mile trip on a tiny effective range: hits MAX_STOPS and says so
    world.stations_along(LA, SF, every_mi=15)
    plan = _plan(world, full_range_mi=60.0, battery_pct=100.0, reserve_mi=5.0, reserve_pct=10.0)
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
