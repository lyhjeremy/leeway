"""Small pure-Python geo helpers - no need for a GIS library at this scale."""

import math

MI_PER_METER = 0.000621371
FT_PER_METER = 3.28084


def haversine_mi(lat1, lon1, lat2, lon2) -> float:
    r_mi = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_mi * math.asin(math.sqrt(a))


def cumulative_distances_mi(coords: list[tuple[float, float]]) -> list[float]:
    """coords is a list of (lon, lat). Returns cumulative distance in miles
    at each vertex, same length as coords, starting at 0."""
    cum = [0.0]
    for i in range(1, len(coords)):
        lon1, lat1 = coords[i - 1]
        lon2, lat2 = coords[i]
        cum.append(cum[-1] + haversine_mi(lat1, lon1, lat2, lon2))
    return cum


def point_at_distance(coords: list[tuple[float, float]], cum: list[float], target_mi: float) -> tuple[float, float]:
    """Interpolate the (lon, lat) point at target_mi along the route."""
    if target_mi <= 0:
        return coords[0]
    if target_mi >= cum[-1]:
        return coords[-1]
    for i in range(1, len(cum)):
        if cum[i] >= target_mi:
            seg_len = cum[i] - cum[i - 1]
            frac = 0 if seg_len == 0 else (target_mi - cum[i - 1]) / seg_len
            lon1, lat1 = coords[i - 1]
            lon2, lat2 = coords[i]
            return (lon1 + (lon2 - lon1) * frac, lat1 + (lat2 - lat1) * frac)
    return coords[-1]


def nearest_route_point(coords: list[tuple[float, float]], cum: list[float], lat: float, lon: float) -> tuple[float, float]:
    """Nearest-vertex approximation. Returns (distance_along_route_mi,
    offset_from_route_mi) - the offset is how far the point sits from the
    route itself, used to reject POIs that fall inside a route's bounding
    box (which can be large for a curving route) but aren't actually near
    the road."""
    best_i, best_d = 0, float("inf")
    for i, (clon, clat) in enumerate(coords):
        d = haversine_mi(lat, lon, clat, clon)
        if d < best_d:
            best_d, best_i = d, i
    return cum[best_i], best_d


def nearest_route_distance_mi(coords: list[tuple[float, float]], cum: list[float], lat: float, lon: float) -> float:
    """Nearest-vertex approximation of how far along the route a given point
    projects to. Fine at this scale since ORS route geometries are already
    densely sampled - an exact perpendicular-segment projection would be
    marginally more accurate but isn't worth the complexity here."""
    dist_along, _ = nearest_route_point(coords, cum, lat, lon)
    return dist_along


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees 0-360
    (0=north, 90=east). Used as a single trip-wide travel direction for the
    headwind calculation - a real route curves, but one bearing for a
    same-day weather snapshot is already an approximation, so a second
    approximation here (straight-line origin-to-destination bearing instead
    of per-segment) doesn't materially change anything."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360


def headwind_component_mph(wind_speed_mph: float, wind_from_deg: float, travel_bearing_deg: float) -> float:
    """Positive = headwind, negative = tailwind. wind_from_deg is the
    meteorological convention (direction the wind blows FROM). A headwind is
    maximal when the wind blows from the direction you're heading towards -
    i.e. wind_from_deg aligned with travel_bearing_deg."""
    angle_diff = math.radians(wind_from_deg - travel_bearing_deg)
    return wind_speed_mph * math.cos(angle_diff)
