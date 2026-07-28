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

import math

from .geo import bearing_deg, haversine_mi
from . import providers

TURN_WINDOW_M = 80
MIN_LEFT_TURN_DEG = 55.0
MAX_TURN_POINTS = 40  # caps the Overpass query size on very turn-heavy routes
SIGNAL_RADIUS_M = 40
MAJOR_ROAD_RADIUS_M = 30
DEDUPE_MI = 0.25
RAIL_BUFFER_M = 30
MAX_POLY_POINTS = 150  # hard cap on polyline points in an around: query

# A left turn OFF an artery you're already driving is normal driving - you
# wait in its turn pocket / center turn lane and cross only the oncoming
# side. The hazard worth flagging is a left FROM a side street ACROSS the
# artery: every lane, unsignaled, no refuge. The two are told apart by the
# angle between the incoming route direction and the major way's own
# direction at the turn: roughly parallel = you were on it (fine),
# otherwise = you're cutting across it (flag).
TURN_FROM_ROAD_PARALLEL_DEG = 35.0

MI_PER_M = 0.000621371


# Above this trip length, asking Overpass about the whole polyline stops
# working. MAX_POLY_POINTS spreads its budget over the entire route, so a
# 600-mile trip gets a point every 4 miles and the "polyline" Overpass sees is
# a chain of chords that cuts corners by miles. The 30m buffer around that
# shape finds roads near the chords rather than roads near the route, and the
# failure mode is an empty result, which reads as "safe".
WHOLE_ROUTE_MAX_MI = 60.0

# Past that, only look where the hazards live. Unprotected lefts and unsignaled
# crossings are surface-street events: they happen leaving home, arriving, and
# getting in and out of charging stops. The interstate between them has none.
HAZARD_WINDOW_MI = 6.0


def _windows(cum: list[float], anchors_mi: list[float]) -> list[tuple[float, float]]:
    """Merged mile ranges to actually search, HAZARD_WINDOW_MI either side of
    each anchor. Overlapping windows are merged so a cluster of nearby stops
    becomes one span rather than several."""
    total = cum[-1] if cum else 0.0
    spans = sorted(
        (max(0.0, a - HAZARD_WINDOW_MI), min(total, a + HAZARD_WINDOW_MI))
        for a in anchors_mi
    )
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _search_polylines(coords: list[tuple[float, float]], cum: list[float],
                      anchors_mi: list[float] | None) -> list[list[tuple[float, float]]]:
    """The polylines to hand Overpass, as a list because each one needs its own
    `around:` clause. They cannot be concatenated into a single coordinate
    list: Overpass reads a coordinate list as one continuous polyline, so
    joining two windows 200 miles apart would draw a search corridor straight
    across everything between them."""
    if not coords:
        return []
    total = cum[-1] if cum else 0.0
    if anchors_mi is None or total <= WHOLE_ROUTE_MAX_MI:
        return [_decimate(coords)]

    out = []
    budget = max(1, MAX_POLY_POINTS // max(1, len(_windows(cum, anchors_mi))))
    for start, end in _windows(cum, anchors_mi):
        seg = [c for c, d in zip(coords, cum) if start <= d <= end]
        if len(seg) >= 2:
            out.append(_decimate(seg, cap=budget))
    return out or [_decimate(coords)]


def _around_clauses(polylines: list[list[tuple[float, float]]], radius_m: int,
                    body: str) -> str:
    """One `around:` clause per window, unioned. `body` is the tag filter that
    follows, e.g. '["railway"="level_crossing"]'."""
    parts = []
    for poly in polylines:
        pts = ",".join(f"{lat:.5f},{lon:.5f}" for lon, lat in poly)
        parts.append(f"{{kind}}(around:{radius_m},{pts}){body};")
    return "".join(parts)


def _decimate(coords: list[tuple[float, float]], cap: int = MAX_POLY_POINTS) -> list[tuple[float, float]]:
    """Thin a route polyline to at most MAX_POLY_POINTS points for an
    Overpass `around:` filter. Overpass reads a coordinate list as a
    POLYLINE, not as separate points, so thinning keeps full route coverage
    and only cuts corners slightly - a 30m radius absorbs that.

    Ceiling division, because floor division doesn't actually respect the
    cap: with 577 vertices and a 300 cap, 577//300 == 1 sent all 577
    points, and the constant quietly meant nothing below 2x its value."""
    if len(coords) <= cap:
        return coords
    step = -(-len(coords) // cap)
    sampled = coords[::step]
    # Keep the true endpoint - a thinned line that stops short would miss
    # hazards in the last stretch.
    if sampled[-1] != coords[-1]:
        sampled.append(coords[-1])
    return sampled


def _pt_seg_dist_mi(lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance from a (lat, lon) point to a segment of (lon, lat) points,
    planar approximation - fine at street scale."""
    kx = math.cos(math.radians(lat)) * 69.17  # miles per degree of longitude
    ky = 69.05
    ax, ay = (a[0] - lon) * kx, (a[1] - lat) * ky
    bx, by = (b[0] - lon) * kx, (b[1] - lat) * ky
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + t * dx, ay + t * dy)


def _turn_angle_deg(b_in: float, b_out: float) -> float:
    """Signed change of heading; negative = left (bearings run clockwise)."""
    return ((b_out - b_in + 540) % 360) - 180


def find_sharp_left_turns(coords: list[tuple[float, float]], cum: list[float],
                          anchors_mi: list[float] | None = None) -> list[dict]:
    """Left turns of MIN_LEFT_TURN_DEG+ within a short arc. coords are
    (lon, lat) as everywhere else. Pure geometry, no API calls.

    The MAX_TURN_POINTS cap is taken in route order, so on a long trip all 40
    would be spent inside the origin city and the rest of the drive would
    never be looked at. Given anchors, only vertices near one are considered,
    which is where surface-street turns happen anyway."""
    window_mi = TURN_WINDOW_M * MI_PER_M
    turns = []
    last_kept_mi = -1e9
    spans = None
    if anchors_mi is not None and cum and cum[-1] > WHOLE_ROUTE_MAX_MI:
        spans = _windows(cum, anchors_mi)

    for i in range(1, len(coords) - 1):
        if spans is not None and not any(a <= cum[i] <= b for a, b in spans):
            continue
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
            turns.append({"lat": lat_i, "lon": lon_i, "mile": cum[i], "angle_deg": round(-angle), "bearing_in": b_in})
            last_kept_mi = cum[i]
            if len(turns) >= MAX_TURN_POINTS:
                break
    return turns


async def unprotected_left_flags(coords: list[tuple[float, float]], cum: list[float],
                                  anchors_mi: list[float] | None = None) -> list[dict]:
    """Flags for left turns that CUT ACROSS a bigger road with no traffic
    signal. A left off a road you're already on - waiting in its own turn
    pocket / center turn lane, crossing only oncoming traffic - is normal
    driving and deliberately NOT flagged (told apart by comparing the
    incoming route bearing with the major way's bearing at the turn). One
    batched Overpass query for all turn points; results are matched back to
    their turn point locally by distance."""
    turns = find_sharp_left_turns(coords, cum, anchors_mi)
    if not turns:
        return []

    node_clauses = []
    way_clauses = []
    for t in turns:
        node_clauses.append(f'node(around:{SIGNAL_RADIUS_M},{t["lat"]:.5f},{t["lon"]:.5f})["highway"~"^(traffic_signals|stop)$"];')
        way_clauses.append(f'way(around:{MAJOR_ROAD_RADIUS_M},{t["lat"]:.5f},{t["lon"]:.5f})["highway"~"^(primary|trunk|secondary)$"];')
        way_clauses.append(f'way(around:{MAJOR_ROAD_RADIUS_M},{t["lat"]:.5f},{t["lon"]:.5f})["lanes"~"^([4-9]|1[0-9])$"];')
    # Way geometry (not just centers) so each turn can compare its incoming
    # direction against the major way's direction - see
    # TURN_FROM_ROAD_PARALLEL_DEG for why that distinction matters.
    # Server-side timeout stays under the 20s httpx read timeout in
    # providers.overpass_raw, so the server gives up before we stop listening.
    query = f"[out:json][timeout:18];({''.join(node_clauses)});out;({''.join(way_clauses)});out tags geom;"

    try:
        data = await providers.overpass_raw(query)
    except Exception:
        return []  # Overpass down - degrade to no flags, never a failed plan

    signals: list[tuple[float, float]] = []
    major_ways: list[dict] = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el.get("type") == "node" and el.get("lat") is not None:
            signals.append((el["lat"], el["lon"]))
        elif el.get("type") == "way" and el.get("geometry"):
            major_ways.append({
                "name": tags.get("name") or tags.get("ref") or "a bigger road",
                "geom": [(g["lon"], g["lat"]) for g in el["geometry"]],
            })

    max_dist_mi = MAJOR_ROAD_RADIUS_M * MI_PER_M * 2.5
    flags = []
    for t in turns:
        has_signal = any(haversine_mi(t["lat"], t["lon"], la, lo) < SIGNAL_RADIUS_M * MI_PER_M * 1.5 for la, lo in signals)
        if has_signal:
            continue
        crossed = None
        for w in major_ways:
            best_dist, best_bearing = None, None
            for j in range(len(w["geom"]) - 1):
                d = _pt_seg_dist_mi(t["lat"], t["lon"], w["geom"][j], w["geom"][j + 1])
                if best_dist is None or d < best_dist:
                    a, b = w["geom"][j], w["geom"][j + 1]
                    best_dist, best_bearing = d, bearing_deg(a[1], a[0], b[1], b[0])
            if best_dist is None or best_dist > max_dist_mi:
                continue
            # Roughly parallel to the incoming route = this is the road being
            # driven, and the left is out of its own turn pocket / center
            # lane - normal driving, not a flag. Only a way CUT ACROSS
            # counts.
            diff = abs(_turn_angle_deg(t["bearing_in"], best_bearing))
            if min(diff, 180 - diff) <= TURN_FROM_ROAD_PARALLEL_DEG:
                continue
            crossed = w["name"]
            break
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


async def wide_crossing_flags(coords: list[tuple[float, float]], cum: list[float],
                               anchors_mi: list[float] | None = None) -> list[dict]:
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
    polylines = _search_polylines(coords, cum, anchors_mi)
    if not polylines:
        return []
    ways = _around_clauses(polylines, 30, '["highway"~"^(primary|trunk|secondary)$"]').format(kind="way")
    lanes = _around_clauses(polylines, 30, '["lanes"~"^([4-9]|1[0-9])$"]["highway"]').format(kind="way")
    signals = _around_clauses(polylines, 30, '["highway"="traffic_signals"]').format(kind="node")
    query = (
        f'[out:json][timeout:18];'
        f'({ways}{lanes});'
        f'out tags geom;'
        f'({signals});out;'
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


async def lane_closure_flags(coords: list[tuple[float, float]], cum: list[float],
                              at_epoch: float | None = None) -> list[dict]:
    """Active Caltrans lane/full closures sitting on (or within half a mile
    of) the route. Statewide feed is cached in providers; here it's just a
    proximity match against the polyline.

    California only, and there is no national equivalent to fall back on:
    every state DOT publishes its own feed in its own shape, and some publish
    none. A route that never enters California would match nothing anyway, so
    it skips the lookup rather than pull 12 district feeds to prove it."""
    from .geo import cumulative_distances_mi, nearest_route_point  # local import avoids cycles

    import time

    if not providers.touches_california(coords):
        return []

    try:
        closures = await providers.caltrans_closures()
    except Exception:
        return []
    if not closures:
        return []

    at = at_epoch if at_epoch is not None else time.time()
    flags = []
    seen_ids: set = set()
    # nearest_route_point walks the whole polyline, so running it for every
    # active closure statewide is hundreds of full-route scans - synchronous
    # work that blocks the event loop and that asyncio.wait_for cannot
    # interrupt, since it happens after the last await. A box around the route
    # throws out the ones that were never going to match.
    pad = CLOSURE_ON_ROUTE_MI / 60.0
    min_lon = min(p[0] for p in coords) - pad
    max_lon = max(p[0] for p in coords) + pad
    min_lat = min(p[1] for p in coords) - pad
    max_lat = max(p[1] for p in coords) + pad

    for c in closures:
        if c["id"] in seen_ids:
            continue
        # active when you'll actually be on the road, not when you planned
        if not (c["start_epoch"] <= at <= c["end_epoch"]):
            continue
        if not (min_lat <= c["lat"] <= max_lat and min_lon <= c["lon"] <= max_lon):
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


async def rail_crossing_flags(coords: list[tuple[float, float]], cum: list[float],
                               anchors_mi: list[float] | None = None) -> list[dict]:
    """Rail level crossings on the route itself, from OSM's
    railway=level_crossing nodes within a tight buffer of the polyline."""
    polylines = _search_polylines(coords, cum, anchors_mi)
    if not polylines:
        return []
    nodes = _around_clauses(polylines, RAIL_BUFFER_M, '["railway"="level_crossing"]').format(kind="node")
    query = f'[out:json][timeout:18];({nodes});out;'

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
