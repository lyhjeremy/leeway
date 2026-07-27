from app.geo import (
    bearing_deg,
    cumulative_distances_mi,
    destination_point,
    haversine_mi,
    headwind_component_mph,
    nearest_route_point,
    point_at_distance,
)

LA = (34.05, -118.24)
SF = (37.7749, -122.4194)


def test_haversine_la_to_sf():
    # Known great-circle distance, ~347 statute miles
    d = haversine_mi(*LA, *SF)
    assert 340 < d < 355


def test_haversine_zero():
    assert haversine_mi(*LA, *LA) == 0.0


def test_bearing_cardinal_directions():
    assert abs(bearing_deg(34.0, -118.0, 35.0, -118.0) - 0) < 1  # due north
    assert abs(bearing_deg(34.0, -118.0, 33.0, -118.0) - 180) < 1  # due south
    assert abs(bearing_deg(0.0, 0.0, 0.0, 1.0) - 90) < 1  # due east on the equator


def test_headwind_component_signs():
    # Wind FROM the direction you're heading = full headwind
    assert headwind_component_mph(20, 0, 0) == 20
    # Wind from behind = full tailwind (negative)
    assert headwind_component_mph(20, 180, 0) == -20
    # Pure crosswind contributes ~nothing
    assert abs(headwind_component_mph(20, 90, 0)) < 1e-9


def test_destination_point_round_trips_through_haversine():
    lat, lon = destination_point(34.05, -118.24, 90, 50)
    assert abs(haversine_mi(34.05, -118.24, lat, lon) - 50) < 0.5


def test_cumulative_and_point_at_distance():
    # Three points due north, ~69 miles per degree of latitude
    coords = [(-118.0, 34.0), (-118.0, 35.0), (-118.0, 36.0)]
    cum = cumulative_distances_mi(coords)
    assert cum[0] == 0.0
    assert 68 < cum[1] < 70
    assert 137 < cum[2] < 139

    lon, lat = point_at_distance(coords, cum, cum[1] / 2)
    assert abs(lat - 34.5) < 0.01
    # Past the end clamps to the last vertex
    assert point_at_distance(coords, cum, 10_000) == coords[-1]
    assert point_at_distance(coords, cum, -5) == coords[0]


def test_nearest_route_point_offset():
    # Dense northbound route: nearest_route_point is vertex-based by design
    coords = [(-118.0, 34.0 + i * 0.1) for i in range(21)]
    cum = cumulative_distances_mi(coords)
    # A point half a degree east of the route's mile-34 vertex
    dist_along, offset = nearest_route_point(coords, cum, 34.5, -117.5)
    assert 30 < dist_along < 40
    assert 26 < offset < 31
