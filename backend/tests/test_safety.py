from app.geo import cumulative_distances_mi, destination_point
from app.safety import find_steep_descents, find_twisty_sections


def _northbound_route(miles: float, step_mi: float = 0.1):
    """Straight route due north, a vertex every step_mi."""
    coords = [(-118.0, 34.0)]
    lat, lon = 34.0, -118.0
    n = int(miles / step_mi)
    for _ in range(n):
        lat, lon = destination_point(lat, lon, 0, step_mi)
        coords.append((lon, lat))
    return coords


def test_steep_descent_flagged():
    # 6.5% down for the first 3 miles, then flat - Grapevine-ish
    coords = _northbound_route(5)
    cum = cumulative_distances_mi(coords)
    drop_ft_per_mi = 5280 * 0.065
    elevations_m = [max(0.0, (3 - mi) * drop_ft_per_mi) / 3.28084 for mi in cum]

    flags = find_steep_descents(coords, elevations_m, cum)
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "steep_descent"
    assert 5.5 <= f["grade_pct"] <= 7.5
    assert f["length_mi"] >= 2.5
    assert "grade descent" in f["description"]


def test_gentle_grade_not_flagged():
    coords = _northbound_route(5)
    cum = cumulative_distances_mi(coords)
    drop_ft_per_mi = 5280 * 0.03  # 3% is a normal highway grade
    elevations_m = [max(0.0, (3 - mi) * drop_ft_per_mi) / 3.28084 for mi in cum]
    assert find_steep_descents(coords, elevations_m, cum) == []


def test_climb_not_flagged_as_descent():
    coords = _northbound_route(5)
    cum = cumulative_distances_mi(coords)
    climb_ft_per_mi = 5280 * 0.08
    elevations_m = [mi * climb_ft_per_mi / 3.28084 for mi in cum]
    assert find_steep_descents(coords, elevations_m, cum) == []


def _zigzag_route(miles: float, step_mi: float = 0.05):
    """Alternating N/E headings every vertex - a synthetic canyon road."""
    coords = [(-118.0, 34.0)]
    lat, lon = 34.0, -118.0
    n = int(miles / step_mi)
    for i in range(n):
        bearing = 0 if i % 2 == 0 else 90
        lat, lon = destination_point(lat, lon, bearing, step_mi)
        coords.append((lon, lat))
    return coords


def test_twisty_section_flagged():
    coords = _zigzag_route(2.5)
    # Continue straight for 2 more miles
    lat, lon = coords[-1][1], coords[-1][0]
    for _ in range(20):
        lat, lon = destination_point(lat, lon, 0, 0.1)
        coords.append((lon, lat))
    cum = cumulative_distances_mi(coords)

    flags = find_twisty_sections(coords, cum)
    assert len(flags) == 1
    assert flags[0]["kind"] == "twisty"
    assert "bends" in flags[0]["description"]
    assert flags[0]["length_mi"] >= 1.5


def test_straight_road_not_twisty():
    coords = _northbound_route(5)
    cum = cumulative_distances_mi(coords)
    assert find_twisty_sections(coords, cum) == []
