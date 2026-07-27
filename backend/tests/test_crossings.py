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
    """Northbound on a side street, then left across an east-west artery:
    the incoming direction is perpendicular to the crossed way, so this is
    a genuine cut-across-every-lane left."""
    coords = _l_shaped_route("left")
    cum = cumulative_distances_mi(coords)
    t = crossings.find_sharp_left_turns(coords, cum)[0]

    # An east-west primary road running through the turn point, no signal
    world.overpass_result = {
        "elements": [
            {"type": "way",
             "geometry": [{"lat": t["lat"], "lon": t["lon"] - 0.002}, {"lat": t["lat"], "lon": t["lon"] + 0.002}],
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


def test_left_off_the_artery_itself_not_flagged(world):
    """Eastbound ON the artery, then left into a side street: you wait in
    the artery's own turn pocket and cross only oncoming traffic - normal
    driving, per the product owner's explicit rule, so no flag even though
    a major unsignaled way sits at the turn."""
    coords = [(-118.0, 34.0)]
    lat, lon = 34.0, -118.0
    for _ in range(int(0.5 / 0.02)):
        lat, lon = destination_point(lat, lon, 90, 0.02)  # east along the artery
        coords.append((lon, lat))
    for _ in range(int(0.5 / 0.02)):
        lat, lon = destination_point(lat, lon, 0, 0.02)  # left, due north
        coords.append((lon, lat))
    cum = cumulative_distances_mi(coords)
    t = crossings.find_sharp_left_turns(coords, cum)[0]
    assert t["angle_deg"] >= 55  # it IS a sharp left

    # The artery runs east-west through the turn - parallel to the approach
    world.overpass_result = {
        "elements": [
            {"type": "way",
             "geometry": [{"lat": t["lat"], "lon": t["lon"] - 0.002}, {"lat": t["lat"], "lon": t["lon"] + 0.002}],
             "tags": {"highway": "primary", "name": "Olympic Blvd", "lanes": "8"}},
        ]
    }
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


# --- nationwide: long routes must not dilute the hazard search -------------

def _long_route(miles: float, per_mile: int = 2):
    """A straight west-to-east polyline of the given length."""
    n = int(miles * per_mile)
    coords = [(-118.0 + (i / per_mile) / 54.6, 34.0) for i in range(n)]
    return coords, crossings_cum(coords)


def crossings_cum(coords):
    from app.geo import cumulative_distances_mi
    return cumulative_distances_mi(coords)


def test_short_route_still_searches_end_to_end():
    """Under the threshold nothing changes: one polyline, whole route."""
    coords, cum = _long_route(20)
    polys = crossings._search_polylines(coords, cum, [0.0, cum[-1]])
    assert len(polys) == 1
    assert len(polys[0]) == len(coords)


def test_long_route_searches_only_around_anchors():
    """A 600-mile trip must not spread its point budget over 600 miles - at
    that spacing the polyline Overpass sees cuts corners by miles and the
    empty result reads as 'safe'."""
    coords, cum = _long_route(600)
    anchors = [0.0, 150.0, 300.0, 450.0, cum[-1]]
    polys = crossings._search_polylines(coords, cum, anchors)

    assert len(polys) == len(anchors), "one search corridor per anchor"
    searched = sum(
        crossings_cum(p)[-1] for p in polys if len(p) > 1
    )
    assert searched < 120, f"searched {searched:.0f} mi of corridor, expected ~60"

    # every point sits within a window of some anchor
    for poly in polys:
        for lon, _lat in poly:
            mile = min(cum[i] for i, c in enumerate(coords) if c[0] == lon)
            assert any(abs(mile - a) <= crossings.HAZARD_WINDOW_MI + 1 for a in anchors)


def test_windows_merge_when_stops_are_close_together():
    coords, cum = _long_route(600)
    merged = crossings._windows(cum, [100.0, 104.0, 400.0])
    assert len(merged) == 2, merged
    assert merged[0][0] < 100 and merged[0][1] > 104


def test_each_window_gets_its_own_around_clause():
    """Concatenating windows into one coordinate list would make Overpass
    draw a search corridor straight across the gap between them."""
    coords, cum = _long_route(600)
    polys = crossings._search_polylines(coords, cum, [0.0, 300.0, cum[-1]])
    clause = crossings._around_clauses(polys, 30, '["railway"="level_crossing"]')
    assert clause.count("(around:30,") == len(polys)


def test_turn_search_is_not_all_spent_in_the_first_city():
    """MAX_TURN_POINTS is taken in route order, so without windowing every
    slot goes to the origin metro and the rest of the drive is never seen."""
    coords, cum = _long_route(600, per_mile=6)
    # a sharp left every half mile for the first 40 miles
    for i in range(0, 240, 3):
        coords[i] = (coords[i][0], coords[i][1] + 0.004)
    cum = crossings_cum(coords)
    anchors = [0.0, cum[-1]]
    windowed = crossings.find_sharp_left_turns(coords, cum, anchors)
    assert all(
        t["mile"] <= crossings.HAZARD_WINDOW_MI + 1 or t["mile"] >= cum[-1] - crossings.HAZARD_WINDOW_MI - 1
        for t in windowed
    ), "turns found outside the anchor windows"
