import time

from app import crossings
from app.geo import cumulative_distances_mi, destination_point
from conftest import run


def _l_shaped_route(turn: str = "left", leg_mi: float = 0.5, step_mi: float = 0.02):
    """North for leg_mi, then a hard 90-degree turn west (left) or east."""
    coords = [(-118.0, 34.0)]
    lat, lon = 34.0, -118.0
    for _ in range(int(leg_mi / step_mi)):
        lat, lon = destination_point(lat, lon, 0, step_mi)
        coords.append((lon, lat))
    bearing = 270 if turn == "left" else 90
    for _ in range(int(leg_mi / step_mi)):
        lat, lon = destination_point(lat, lon, bearing, step_mi)
        coords.append((lon, lat))
    return coords


def test_sharp_left_turn_detected_and_right_turn_ignored():
    left = _l_shaped_route("left")
    turns = crossings.find_sharp_left_turns(left, cumulative_distances_mi(left))
    assert len(turns) == 1
    assert turns[0]["angle_deg"] >= 55

    right = _l_shaped_route("right")
    assert crossings.find_sharp_left_turns(right, cumulative_distances_mi(right)) == []


def test_unprotected_left_flagged_against_fake_osm(world):
    coords = _l_shaped_route("left")
    cum = cumulative_distances_mi(coords)
    t = crossings.find_sharp_left_turns(coords, cum)[0]

    # A primary road at the turn, no signal -> flagged
    world.overpass_result = {
        "elements": [
            {"type": "way", "center": {"lat": t["lat"], "lon": t["lon"]},
             "tags": {"highway": "primary", "name": "Main St"}},
        ]
    }
    flags = run(crossings.unprotected_left_flags(coords, cum))
    assert len(flags) == 1
    assert flags[0]["kind"] == "unprotected_left"
    assert "Main St" in flags[0]["description"]

    # Same turn with a traffic signal right there -> protected, no flag
    world.overpass_result["elements"].append(
        {"type": "node", "lat": t["lat"], "lon": t["lon"], "tags": {"highway": "traffic_signals"}}
    )
    assert run(crossings.unprotected_left_flags(coords, cum)) == []


def test_left_turn_with_no_major_road_not_flagged(world):
    coords = _l_shaped_route("left")
    cum = cumulative_distances_mi(coords)
    world.overpass_result = {"elements": []}  # residential nothingness
    assert run(crossings.unprotected_left_flags(coords, cum)) == []


def _straight_north_route(miles: float = 1.0, step_mi: float = 0.02):
    coords = [(-118.0, 34.0)]
    lat, lon = 34.0, -118.0
    for _ in range(int(miles / step_mi)):
        lat, lon = destination_point(lat, lon, 0, step_mi)
        coords.append((lon, lat))
    return coords


def _east_west_way(lat: float, name="Wide Blvd", lanes="6", extra_tags=None):
    tags = {"highway": "primary", "name": name, "lanes": lanes}
    tags.update(extra_tags or {})
    return {
        "type": "way",
        "tags": tags,
        "geometry": [{"lat": lat, "lon": -118.01}, {"lat": lat, "lon": -117.99}],
    }


def test_wide_crossing_flagged_when_unsignaled(world):
    coords = _straight_north_route()
    cum = cumulative_distances_mi(coords)
    mid_lat = coords[len(coords) // 2][1]
    world.overpass_result = {"elements": [_east_west_way(mid_lat)]}

    flags = run(crossings.wide_crossing_flags(coords, cum))
    assert len(flags) == 1
    assert flags[0]["kind"] == "wide_crossing"
    assert "Wide Blvd" in flags[0]["description"]


def test_wide_crossing_with_signal_or_bridge_not_flagged(world):
    coords = _straight_north_route()
    cum = cumulative_distances_mi(coords)
    mid_lat = coords[len(coords) // 2][1]

    world.overpass_result = {
        "elements": [
            _east_west_way(mid_lat),
            {"type": "node", "lat": mid_lat, "lon": -118.0, "tags": {"highway": "traffic_signals"}},
        ]
    }
    assert run(crossings.wide_crossing_flags(coords, cum)) == []

    # A grade-separated overpass crosses on paper, not on the ground
    world.overpass_result = {"elements": [_east_west_way(mid_lat, extra_tags={"bridge": "yes"})]}
    assert run(crossings.wide_crossing_flags(coords, cum)) == []


def test_rail_crossing_flagged_and_deduped(world):
    coords = _straight_north_route()
    cum = cumulative_distances_mi(coords)
    mid_lat = coords[len(coords) // 2][1]
    # Two OSM nodes for the same physical crossing, meters apart
    world.overpass_result = {
        "elements": [
            {"type": "node", "lat": mid_lat, "lon": -118.0},
            {"type": "node", "lat": mid_lat + 0.0002, "lon": -118.0},
        ]
    }
    flags = run(crossings.rail_crossing_flags(coords, cum))
    assert len(flags) == 1
    assert flags[0]["kind"] == "rail_crossing"


def test_lane_closure_only_when_active_and_on_route(world):
    coords = _straight_north_route()
    cum = cumulative_distances_mi(coords)
    mid_lat = coords[len(coords) // 2][1]
    now = time.time()

    active = {
        "id": "C1", "start_epoch": now - 3600, "end_epoch": now + 3600,
        "lat": mid_lat, "lon": -118.0, "route": "SR-1", "direction": "NB",
        "type": "Lane", "work": "Drainage work", "lanes_closed": "1", "total_lanes": "2",
        "until": "2026-08-01 06:00",
    }
    expired = {**active, "id": "C2", "start_epoch": now - 7200, "end_epoch": now - 3600}
    far_away = {**active, "id": "C3", "lat": mid_lat, "lon": -119.5}
    world.closures = [active, expired, far_away]

    flags = run(crossings.lane_closure_flags(coords, cum))
    assert len(flags) == 1
    assert flags[0]["kind"] == "lane_closure"
    assert "SR-1" in flags[0]["description"]

    # With a departure time after the closure ends, nothing is active
    assert run(crossings.lane_closure_flags(coords, cum, at_epoch=now + 7200)) == []
