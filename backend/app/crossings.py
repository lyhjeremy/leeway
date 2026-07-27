"""Overpass-backed safety flags: unprotected left turns and rail crossings.

These are the Stage 2 flags that need real OSM data, deferred until now
because they're a genuinely different kind of check from steep descents and
sun glare (which come free with the route's own elevation profile and
astronomy). Two honest scope limits, stated up front:

- These flags WARN about what's on the planned route; they don't re-route
  around it. ORS's public API has no way to penalize specific turn types,
  so "no unprotected left unless necessary" as a routing constraint isn't
  buildable on this stack - detection and a heads-up is what's real.
- Overpass (the free community OSM server) is flaky under load, so every
  check here degrades to "no flags" rather than failing the plan. A missing
  warning is the accepted cost of a $0 stack; an invented one is not.

The turn detection is pure geometry (no API): walk the route polyline,
measure the bearing change around each vertex using ~80m windows, and call
anything bending 55°+ left within that short arc a real left turn - highway
interchange ramps curve more gradually and don't trip it. Each turn point
is then checked against real OSM data: a traffic signal within 40m makes it
protected; a nearby primary/trunk/secondary road (or anything tagged 4+
lanes) makes it a turn across traffic worth flagging.
"""

from .geo import bearing_deg, haversine_mi
from . import providers

TURN_WINDOW_M = 80
MIN_LEFT_TURN_DEG = 55.0
MAX_TURN_POINTS = 40  # caps the Overpass query size on very turn-heavy routes
SIGNAL_RADIUS_M = 40
MAJOR_ROAD_RADIUS_M = 30
DEDUPE_MI = 0.25
RAIL_BUFFER_M = 30
MAX_POLY_POINTS = 300  # decimation target for the rail-crossing polyline query

MI_PER_M = 0.000621371


def _turn_angle_deg(b_in: float, b_out: float) -> float:
    """Signed change of heading; negative = left (bearings run clockwise)."""
    return ((b_out - b_in + 540) % 360) - 180


def find_sharp_left_turns(coords: list[tuple[float, float]], cum: list[float]) -> list[dict]:
    """Left turns of MIN_LEFT_TURN_DEG+ within a short arc. coords are
    (lon, lat) as everywhere else. Pure geometry, no API calls."""
    window_mi = TURN_WINDOW_M * MI_PER_M
    turns = []
    last_kept_mi = -1e9

    for i in range(1, len(coords) - 1):
        # find the points ~one window behind and ahead of vertex i
        j = i
        while j > 0 and cum[i] - cum[j] < window_mi:
            j -= 1
        k = i
        while k < len(coords) - 1 and cum[k] - cum[i] < window_mi:
            k += 1
        if cum[i] - cum[j] < window_mi * 0.5 or cum[k] - cum[i] < window_mi * 0.5:
            continue  # ends of the route - not enough arc to measure

        lon_j, lat_j = coords[j]
        lon_i, lat_i = coords[i]
        lon_k, lat_k = coords[k]
        b_in = bearing_deg(lat_j, lon_j, lat_i, lon_i)
        b_out = bearing_deg(lat_i, lon_i, lat_k, lon_k)
        angle = _turn_angle_deg(b_in, b_out)

        if angle <= -MIN_LEFT_TURN_DEG and cum[i] - last_kept_mi > DEDUPE_MI:
            turns.append({"lat": lat_i, "lon": lon_i, "mile": cum[i], "angle_deg": round(-angle)})
            last_kept_mi = cum[i]
            if len(turns) >= MAX_TURN_POINTS:
                break
    return turns


async def unprotected_left_flags(coords: list[tuple[float, float]], cum: list[float]) -> list[dict]:
    """Flags for left turns that cross a bigger road with no traffic signal.
    One batched Overpass query for all turn points; results are matched back
    to their turn point locally by distance."""
    turns = find_sharp_left_turns(coords, cum)
    if not turns:
        return []

    clauses = []
    for t in turns:
        clauses.append(f'node(around:{SIGNAL_RADIUS_M},{t["lat"]:.5f},{t["lon"]:.5f})["highway"~"^(traffic_signals|stop)$"];')
        clauses.append(f'way(around:{MAJOR_ROAD_RADIUS_M},{t["lat"]:.5f},{t["lon"]:.5f})["highway"~"^(primary|trunk|secondary)$"];')
        clauses.append(f'way(around:{MAJOR_ROAD_RADIUS_M},{t["lat"]:.5f},{t["lon"]:.5f})["lanes"~"^([4-9]|1[0-9])$"];')
    query = f"[out:json][timeout:25];({''.join(clauses)});out tags center;"

    try:
        data = await providers.overpass_raw(query)
    except Exception:
        return []  # Overpass down - degrade to no flags, never a failed plan

    signals: list[tuple[float, float]] = []
    major_roads: list[tuple[float, float, str]] = []
    for el in data.get("elements", []):
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None:
            continue
        tags = el.get("tags", {})
        if el["type"] == "node":
            signals.append((lat, lon))
        else:
            name = tags.get("name") or tags.get("ref") or "a bigger road"
            major_roads.append((lat, lon, name))

    flags = []
    for t in turns:
        has_signal = any(haversine_mi(t["lat"], t["lon"], la, lo) < SIGNAL_RADIUS_M * MI_PER_M * 1.5 for la, lo in signals)
        if has_signal:
            continue
        crossed = next(
            (name for la, lo, name in major_roads if haversine_mi(t["lat"], t["lon"], la, lo) < MAJOR_ROAD_RADIUS_M * MI_PER_M * 2.5),
            None,
        )
        if crossed is None:
            continue
        flags.append({
            "kind": "unprotected_left",
            "description": f"Unsignaled left turn across {crossed} at mile {t['mile']:.0f} - wait for a real gap, or reroute yourself around it.",
            "lat": t["lat"],
            "lon": t["lon"],
            "mile": round(t["mile"], 1),
        })
    return flags


def _seg_intersection(p1, p2, p3, p4):
    """2D segment intersection in (lon, lat) space. Returns the point or
    None. Good enough at street scale where degrees are locally planar."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-12:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


async def wide_crossing_flags(coords: list[tuple[float, float]], cum: list[float]) -> list[dict]:
    """The founding safety ask: crossing a main road (4+ lanes or a
    primary/trunk artery) straight-through with no traffic signal.

    Mechanics: fetch major-way geometries near the route from OSM, compute
    real segment intersections with the route polyline, keep crossings where
    the route passes straight through (little bearing change - a turn onto
    the road is the unprotected-left detector's job), the two roads actually
    cross (bearing difference well off parallel), and no signal sits within
    SIGNAL_RADIUS_M.

    Honest limit: this is 2D - a grade-separated overpass intersects on
    paper but not on the ground. Crossing ways tagged bridge/tunnel or
    motorway are excluded, which removes most of those, but a rare false
    positive is possible; the copy says 'check it' rather than 'gospel'."""
    step = max(1, len(coords) // MAX_POLY_POINTS)
    sampled = coords[::step]
    poly = ",".join(f"{lat:.5f},{lon:.5f}" for lon, lat in sampled)
    query = (
        f'[out:json][timeout:30];'
        f'(way(around:30,{poly})["highway"~"^(primary|trunk|secondary)$"];'
        f'way(around:30,{poly})["lanes"~"^([4-9]|1[0-9])$"]["highway"];);'
        f'out tags geom;'
        f'node(around:30,{poly})["highway"="traffic_signals"];out;'
    )
    try:
        data = await providers.overpass_raw(query)
    except Exception:
        return []

    signals: list[tuple[float, float]] = []
    ways = []
    for el in data.get("elements", []):
        if el.get("type") == "node" and el.get("tags", {}).get("highway") == "traffic_signals":
            signals.append((el["lat"], el["lon"]))
        elif el.get("type") == "way" and el.get("geometry"):
            tags = el.get("tags", {})
            if tags.get("bridge") or tags.get("tunnel") or tags.get("highway") in ("motorway", "motorway_link"):
                continue
            ways.append({
                "name": tags.get("name") or tags.get("ref") or "a main road",
                "lanes": tags.get("lanes"),
                "geom": [(g["lon"], g["lat"]) for g in el["geometry"]],
            })

    flags = []
    last_mi = -1e9
    for i in range(len(coords) - 1):
        if flags and cum[i] - last_mi < DEDUPE_MI:
            continue
        a, b = coords[i], coords[i + 1]
        route_bearing = bearing_deg(a[1], a[0], b[1], b[0])
        for w in ways:
            hit = None
            for j in range(len(w["geom"]) - 1):
                hit = _seg_intersection(a, b, w["geom"][j], w["geom"][j + 1])
                if hit:
                    way_bearing = bearing_deg(w["geom"][j][1], w["geom"][j][0], w["geom"][j + 1][1], w["geom"][j + 1][0])
                    break
            if not hit:
                continue
            lon, lat = hit
            # actually crossing, not merging into or running alongside
            cross_angle = abs(_turn_angle_deg(route_bearing, way_bearing))
            if cross_angle < 40 or cross_angle > 140:
                continue
            # straight-through: the route doesn't bend here (a bend is a turn,
            # covered by the unprotected-left check)
            if i > 0:
                prev_bearing = bearing_deg(coords[i - 1][1], coords[i - 1][0], a[1], a[0])
                if abs(_turn_angle_deg(prev_bearing, route_bearing)) > 25:
                    continue
            if any(haversine_mi(lat, lon, sl, so) < SIGNAL_RADIUS_M * MI_PER_M * 1.5 for sl, so in signals):
                continue
            lanes = f"{w['lanes']}-lane " if w["lanes"] else ""
            flags.append({
                "kind": "wide_crossing",
                "description": (
                    f"Crossing {lanes}{w['name']} at mile {cum[i]:.0f} with no signal - "
                    "worth a look before you commit to this route."
                ),
                "lat": lat,
                "lon": lon,
                "mile": round(cum[i], 1),
            })
            last_mi = cum[i]
            break
        if len(flags) >= 12:
            break
    return flags


CLOSURE_ON_ROUTE_MI = 0.5


async def lane_closure_flags(coords: list[tuple[float, float]], cum: list[float]) -> list[dict]:
    """Active Caltrans lane/full closures sitting on (or within half a mile
    of) the route. Statewide feed is cached in providers; here it's just a
    proximity match against the polyline."""
    from .geo import cumulative_distances_mi, nearest_route_point  # local import avoids cycles

    try:
        closures = await providers.caltrans_closures()
    except Exception:
        return []
    if not closures:
        return []

    flags = []
    seen_ids: set = set()
    for c in closures:
        if c["id"] in seen_ids:
            continue
        dist_along, offset = nearest_route_point(coords, cum, c["lat"], c["lon"])
        if offset > CLOSURE_ON_ROUTE_MI:
            continue
        seen_ids.add(c["id"])
        lanes = ""
        if c["lanes_closed"] and c["total_lanes"]:
            lanes = f", lanes {c['lanes_closed']} of {c['total_lanes']} closed"
        work = f" ({c['work'].lower()})" if c["work"] else ""
        until = f" until {c['until'].split(' ')[0]}" if c["until"] else ""
        flags.append({
            "kind": "lane_closure",
            "description": (
                f"Caltrans {c['type'].lower()} closure on {c['route']} {c['direction']} "
                f"near mile {dist_along:.0f}{lanes}{work}{until}."
            ),
            "lat": c["lat"],
            "lon": c["lon"],
            "mile": round(dist_along, 1),
        })
        if len(flags) >= 10:
            break
    flags.sort(key=lambda f: f["mile"])
    return flags


async def rail_crossing_flags(coords: list[tuple[float, float]], cum: list[float]) -> list[dict]:
    """Rail level crossings on the route itself, from OSM's
    railway=level_crossing nodes within a tight buffer of the polyline."""
    step = max(1, len(coords) // MAX_POLY_POINTS)
    sampled = coords[::step]
    poly = ",".join(f"{lat:.5f},{lon:.5f}" for lon, lat in sampled)
    query = f'[out:json][timeout:25];node(around:{RAIL_BUFFER_M},{poly})["railway"="level_crossing"];out;'

    try:
        data = await providers.overpass_raw(query)
    except Exception:
        return []

    flags = []
    last_mi = -1e9
    crossings_found = []
    for el in data.get("elements", []):
        lat, lon = el.get("lat"), el.get("lon")
        if lat is None:
            continue
        best_i = min(range(len(coords)), key=lambda i: haversine_mi(lat, lon, coords[i][1], coords[i][0]))
        crossings_found.append((cum[best_i], lat, lon))

    for mile, lat, lon in sorted(crossings_found):
        if mile - last_mi < DEDUPE_MI:
            continue
        last_mi = mile
        flags.append({
            "kind": "rail_crossing",
            "description": f"Rail crossing at mile {mile:.0f} - never queue across the tracks.",
            "lat": lat,
            "lon": lon,
            "mile": round(mile, 1),
        })
    return flags
