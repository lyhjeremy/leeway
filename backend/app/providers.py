"""Thin clients for the external data sources this backend depends on:
OpenRouteService (geocoding + directions + elevation, needs a free API key),
Open Charge Map (charging stations, no key needed under 250 results/call),
Open-Meteo (current weather, no key needed at all - confirmed via a real
call, not assumed), and Overpass (OSM point-of-interest search for the
voice stop-finder, no key needed - confirmed via a real cafe search that
returned real drive_through/brand tags).
"""

import asyncio
import json
import os
import time

import httpx

from .geo import FT_PER_METER, MI_PER_METER

ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
OCM_API_KEY = os.environ.get("OCM_API_KEY", "")
ORS_BASE = "https://api.openrouteservice.org"
OCM_BASE = "https://api.openchargemap.io/v3"
METEO_BASE = "https://api.open-meteo.com/v1"
OVERPASS_BASE = "https://overpass-api.de/api/interpreter"

# California bounding box - biases geocoding results since this product is
# CA-only for now, per the product plan.
CA_BBOX = {"min_lon": -124.48, "min_lat": 32.53, "max_lon": -114.13, "max_lat": 42.01}


class _TTLCache:
    """Tiny in-process TTL cache for successful provider responses. Exists to
    stretch the free-tier quotas, which are this stack's real scarcity: the
    ORS directions quota (~150-200 plans/day documented ceiling) once ran out
    mid-verification, and a single planning session re-requests identical
    routes constantly - skip-a-stop replans, corridor re-plans, and the
    landing page's sample trip are all repeats. Only successes are cached;
    errors and rate limits always pass through so the retry logic upstream
    keeps seeing the truth.

    Values are returned by reference, not copied - every caller in this
    codebase treats provider responses as read-only (the planner copies
    station dicts before annotating them). Keep it that way."""

    def __init__(self, ttl_s: float, max_entries: int):
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._store: dict = {}  # key -> (expires_at, value)

    def get(self, key):
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key, value):
        if len(self._store) >= self.max_entries:
            # Evict the oldest-inserted quarter (dicts keep insertion order).
            # Crude next to LRU, but cache misses here only cost an API call
            # we'd have made anyway - simplicity wins.
            for k in list(self._store)[: max(1, self.max_entries // 4)]:
                del self._store[k]
        self._store[key] = (time.time() + self.ttl_s, value)

    def clear(self):
        self._store.clear()


# Route geometry/duration from ORS has no live-traffic component, so a route
# answered this morning is still right this afternoon - 6h is conservative.
# Directions results carry full geometry (+elevation), so the entry cap is
# what bounds memory, sized for Render's free 512MB instance.
_directions_cache = _TTLCache(ttl_s=6 * 3600, max_entries=64)
_geocode_cache = _TTLCache(ttl_s=24 * 3600, max_entries=256)
# Charging stations come and go on week scales; 30min mostly serves replans
# within one planning session (skip-stop, corridor switches) without letting
# a whole day run on stale data.
_stations_cache = _TTLCache(ttl_s=1800, max_entries=128)
_weather_cache = _TTLCache(ttl_s=900, max_entries=64)


class ORSNotConfigured(Exception):
    pass


def _require_key():
    if not ORS_API_KEY:
        raise ORSNotConfigured("ORS_API_KEY is not set")


async def geocode(query: str) -> list[dict]:
    _require_key()
    cache_key = query.strip().lower()
    cached = _geocode_cache.get(cache_key)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{ORS_BASE}/geocode/search",
            params={
                "api_key": ORS_API_KEY,
                "text": query,
                "boundary.rect.min_lon": CA_BBOX["min_lon"],
                "boundary.rect.min_lat": CA_BBOX["min_lat"],
                "boundary.rect.max_lon": CA_BBOX["max_lon"],
                "boundary.rect.max_lat": CA_BBOX["max_lat"],
                "size": 5,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    out = []
    for feat in data.get("features", []):
        lon, lat = feat["geometry"]["coordinates"][:2]
        out.append({"label": feat["properties"].get("label", query), "lat": lat, "lon": lon})
    _geocode_cache.set(cache_key, out)
    return out


def _highway_fraction_from_extras(extras: dict, n_coords: int) -> float | None:
    """ORS's waycategory extra_info is a bitmask per coordinate-index range,
    where bit 1 (value & 1) means 'highway'. Returns None if the shape isn't
    what we expect, so the caller can fall back to a speed-based estimate
    instead of crashing on an ORS response-format assumption we haven't
    verified live yet."""
    try:
        segments = extras["waycategory"]["values"]  # [[fromIdx, toIdx, code], ...]
        highway_len = 0
        total_len = 0
        for frm, to, code in segments:
            seg_len = max(to - frm, 1)
            total_len += seg_len
            if int(code) & 1:
                highway_len += seg_len
        return highway_len / total_len if total_len else None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


async def directions(
    origin: tuple[float, float],
    destination: tuple[float, float],
    avoid_tolls: bool = False,
    avoid_highways: bool = False,
    avoid_polygons: list[list[list[float]]] | None = None,
    via: tuple[float, float] | None = None,
    avoid_ferries: bool = False,
) -> dict:
    """origin/destination are (lat, lon). Returns distance_mi, duration_min,
    ascent_ft, descent_ft, highway_fraction, geometry as [(lon, lat), ...],
    and elevations_m (parallel array, meters per vertex).

    avoid_polygons is a list of GeoJSON-style polygon rings ([[lon,lat],...],
    first point repeated last) that the route must not pass through - ORS's
    only mechanism for steering around a specific intersection, since its
    public API has no turn-type penalties. Used by the safety-avoidance
    pass to route around flagged unprotected lefts and rail crossings."""
    _require_key()
    olat, olon = origin
    dlat, dlon = destination
    avoid_features = []
    if avoid_tolls:
        avoid_features.append("tollways")
    if avoid_highways:
        avoid_features.append("highways")
    if avoid_ferries:
        avoid_features.append("ferries")

    coordinates = [[olon, olat], [dlon, dlat]]
    body = {
        "coordinates": coordinates,
        "elevation": True,
        "extra_info": ["waycategory"],
    }
    if via is not None:
        coordinates.insert(1, [via[1], via[0]])
        # A corridor-seeking via point is a rough geometric offset that can
        # land miles from any road (mountains, fields) - let ORS snap it to
        # the nearest road however far that is, instead of erroring at its
        # default 350m limit.
        body["radiuses"] = [-1, -1, -1]
    options: dict = {}
    if avoid_features:
        options["avoid_features"] = avoid_features
    if avoid_polygons:
        options["avoid_polygons"] = {"type": "MultiPolygon", "coordinates": [[ring] for ring in avoid_polygons]}
    if options:
        body["options"] = options

    # ~1m coordinate precision; identical requests within the TTL cost zero
    # quota. Keyed on everything that changes the answer, including avoid
    # polygons and via points.
    cache_key = json.dumps([
        round(olat, 5), round(olon, 5), round(dlat, 5), round(dlon, 5),
        sorted(avoid_features), avoid_polygons,
        [round(via[0], 5), round(via[1], 5)] if via is not None else None,
    ])
    cached = _directions_cache.get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{ORS_BASE}/v2/directions/driving-car/geojson",
            headers={"Authorization": ORS_API_KEY, "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    feature = data["features"][0]
    props = feature["properties"]
    summary = props["summary"]
    coords_3d = feature["geometry"]["coordinates"]
    coords = [(c[0], c[1]) for c in coords_3d]
    elevations_m = [c[2] for c in coords_3d]

    ascent_m = props.get("ascent", 0.0)
    descent_m = props.get("descent", 0.0)
    distance_mi = summary["distance"] * MI_PER_METER
    duration_min = summary["duration"] / 60

    highway_fraction = _highway_fraction_from_extras(props.get("extras", {}), len(coords))
    if highway_fraction is None:
        avg_speed_mph = distance_mi / (duration_min / 60) if duration_min else 0
        highway_fraction = max(0.0, min(1.0, (avg_speed_mph - 25) / 35))

    result = {
        "distance_mi": distance_mi,
        "duration_min": duration_min,
        "ascent_ft": ascent_m * FT_PER_METER,
        "descent_ft": descent_m * FT_PER_METER,
        "highway_fraction": highway_fraction,
        "geometry": coords,
        "elevations_m": elevations_m,
    }
    _directions_cache.set(cache_key, result)
    return result


class OCMNotConfigured(Exception):
    pass


async def find_charging_stations(lat: float, lon: float, radius_mi: float = 15, max_results: int = 50) -> list[dict]:
    if not OCM_API_KEY:
        raise OCMNotConfigured("OCM_API_KEY is not set")
    # ~110m position precision - search centers are interpolated route
    # points, so hashing the exact floats would never hit.
    cache_key = (round(lat, 3), round(lon, 3), round(radius_mi, 1), max_results)
    cached = _stations_cache.get(cache_key)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{OCM_BASE}/poi/",
            params={
                "key": OCM_API_KEY,
                "output": "json",
                "latitude": lat,
                "longitude": lon,
                "distance": radius_mi,
                "distanceunit": "Miles",
                "maxresults": max_results,
                # compact=true silently strips OperatorInfo AND the nested
                # ConnectionType object on every connection - confirmed via a
                # real side-by-side call, not assumed. Both are needed (network
                # name, and real Supercharger detection), so this stays false
                # even though the payload is bigger; traffic here is low-volume
                # personal use, not worth optimizing away.
                "compact": "false",
                # verbose gives MediaItems (user photos), NumberOfPoints
                # (stall count) and UsageCost - all shown in the stop popups.
                "verbose": "true",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    out = []
    for poi in data:
        addr = poi.get("AddressInfo") or {}
        conns = poi.get("Connections") or []
        title = addr.get("Title", "Charging station") or "Charging station"
        operator = (poi.get("OperatorInfo") or {}).get("Title", "") or ""
        conn_titles = " ".join((c.get("ConnectionType") or {}).get("Title", "") or "" for c in conns)
        is_supercharger = any("tesla" in s.lower() for s in (operator, conn_titles, title))
        max_kw = max((c.get("PowerKW") or 0) for c in conns) if conns else 0
        media = poi.get("MediaItems") or []
        photo_url = next((m.get("ItemThumbnailURL") for m in media if m.get("ItemThumbnailURL")), None)
        cost = (poi.get("UsageCost") or "").strip() or None
        out.append({
            "id": poi.get("ID"),
            "title": title,
            "lat": addr.get("Latitude"),
            "lon": addr.get("Longitude"),
            "network": operator or "Unknown network",
            "is_supercharger": is_supercharger,
            "max_kw": max_kw,
            "connector_count": len(conns),
            "stall_count": poi.get("NumberOfPoints"),
            "cost": cost,
            "photo_url": photo_url,
        })
    result = [s for s in out if s["lat"] is not None and s["lon"] is not None]
    _stations_cache.set(cache_key, result)
    return result


async def current_weather(lat: float, lon: float, at_epoch: float | None = None) -> dict:
    """No API key needed at all - real call confirmed working. Returns a
    trip-day snapshot at one point (the route's midpoint - see planner.py),
    not per-segment; weather varies continuously along any real route, so a
    single snapshot is already the honest level of precision here.

    With at_epoch set (a departure time), the snapshot comes from the hourly
    FORECAST at that hour instead of current conditions - leaving at 6am
    should plan against 6am weather, not tonight's."""
    use_forecast = at_epoch is not None and abs(at_epoch - time.time()) > 3600
    # Hour-bucketed: two plans in the same hour with the same rough midpoint
    # share one forecast call.
    cache_key = (round(lat, 2), round(lon, 2), int(at_epoch // 3600) if use_forecast else None)
    cached = _weather_cache.get(cache_key)
    if cached is not None:
        return cached
    params = {
        "latitude": lat,
        "longitude": lon,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
    }
    if use_forecast:
        params["hourly"] = "temperature_2m,wind_speed_10m,wind_direction_10m"
        params["timeformat"] = "unixtime"
        params["forecast_days"] = 8
    else:
        params["current"] = "temperature_2m,wind_speed_10m,wind_direction_10m"

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{METEO_BASE}/forecast", params=params)
        resp.raise_for_status()
        data = resp.json()

    if use_forecast:
        hours = data["hourly"]["time"]
        idx = min(range(len(hours)), key=lambda i: abs(hours[i] - at_epoch))
        result = {
            "temp_f": data["hourly"]["temperature_2m"][idx],
            "wind_speed_mph": data["hourly"]["wind_speed_10m"][idx],
            "wind_from_deg": data["hourly"]["wind_direction_10m"][idx],
            "forecast": True,
        }
    else:
        current = data["current"]
        result = {
            "temp_f": current["temperature_2m"],
            "wind_speed_mph": current["wind_speed_10m"],
            "wind_from_deg": current["wind_direction_10m"],
            "forecast": False,
        }
    _weather_cache.set(cache_key, result)
    return result


async def directions_alternatives(
    origin: tuple[float, float],
    destination: tuple[float, float],
    avoid_tolls: bool = False,
    avoid_highways: bool = False,
    avoid_ferries: bool = False,
) -> list[dict]:
    """Up to 3 route corridors, baseline first, as {distance_mi,
    duration_min, geometry, via}. `via` is the (lat, lon) waypoint that
    reproduces the corridor when the full planner re-runs along it (null
    for the baseline).

    Both of ORS's built-in mechanisms hit hard public-API limits found via
    real 2004 errors: the alternative-routes algorithm caps at 100km and
    avoid-polygons at 150km, so neither can generate road-trip
    alternatives. Instead: route through via points offset perpendicular
    from the baseline's midpoint (left and right of the corridor) - for
    LA->SF on I-5 that's what lands the 101 and 99 corridors. A via that
    falls somewhere unroutable, or produces a barely-different or absurdly
    longer route, is dropped - fewer than 3 corridors is an answer, not an
    error."""
    from .geo import bearing_deg, cumulative_distances_mi, destination_point, point_at_distance

    baseline = await directions(origin, destination, avoid_tolls, avoid_highways, avoid_ferries=avoid_ferries)
    total = baseline["distance_mi"]
    out = [{**baseline, "via": None}]

    cum = cumulative_distances_mi(baseline["geometry"])
    mid_lon, mid_lat = point_at_distance(baseline["geometry"], cum, total / 2)
    trip_bearing = bearing_deg(origin[0], origin[1], destination[0], destination[1])
    offset_mi = min(60.0, max(12.0, total * 0.15))

    for side_bearing in ((trip_bearing + 90) % 360, (trip_bearing - 90) % 360):
        via = destination_point(mid_lat, mid_lon, side_bearing, offset_mi)
        try:
            alt = await directions(origin, destination, avoid_tolls, avoid_highways, via=via, avoid_ferries=avoid_ferries)
        except httpx.HTTPError:
            continue
        too_long = alt["distance_mi"] > total * 1.5
        too_similar = abs(alt["distance_mi"] - total) < total * 0.02
        if too_long or too_similar:
            continue
        out.append({**alt, "via": {"lat": via[0], "lon": via[1]}})

    return [
        {
            "distance_mi": round(r["distance_mi"], 1),
            "duration_min": round(r["duration_min"]),
            "geometry": r["geometry"],
            "via": r["via"],
        }
        for r in out
    ]


CALTRANS_DISTRICTS = [f"d{i}" for i in range(1, 13)]
CALTRANS_TTL_S = 1800  # closures change on hour scales; don't hammer 12 feeds per plan
_caltrans_cache: dict = {"ts": 0.0, "closures": []}


async def caltrans_closures() -> list[dict]:
    """Active lane/full closures statewide from Caltrans' free district feeds
    (cwwp2.dot.ca.gov LCS). Cached for CALTRANS_TTL_S; a district feed that
    fails just contributes nothing - missing a closure flag beats failing
    the plan."""
    now = time.time()
    if now - _caltrans_cache["ts"] < CALTRANS_TTL_S:
        return _caltrans_cache["closures"]

    out = []
    async with httpx.AsyncClient(timeout=12) as client:
        district_urls = [
            f"https://cwwp2.dot.ca.gov/data/d{i}/lcs/lcsStatusD{i:02d}.json" for i in range(1, 13)
        ]

        async def fetch(url: str) -> list[dict]:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json().get("data", [])
            except Exception:
                return []

        results = await asyncio.gather(*(fetch(u) for u in district_urls))

    for records in results:
        for rec in records:
            lcs = rec.get("lcs", {})
            closure = lcs.get("closure", {})
            ts = closure.get("closureTimestamp", {})
            loc = lcs.get("location", {}).get("begin", {})
            try:
                start = float(ts.get("closureStartEpoch") or 0)
                end = float(ts.get("closureEndEpoch") or 0)
                lat = float(loc.get("beginLatitude"))
                lon = float(loc.get("beginLongitude"))
            except (TypeError, ValueError):
                continue
            out.append({
                "start_epoch": start,
                "end_epoch": end,
                "id": closure.get("closureID", ""),
                "lat": lat,
                "lon": lon,
                "route": loc.get("beginRoute", "the highway"),
                "direction": lcs.get("location", {}).get("travelFlowDirection", ""),
                "type": closure.get("typeOfClosure", "Lane"),
                "work": closure.get("typeOfWork", ""),
                "lanes_closed": closure.get("lanesClosed", ""),
                "total_lanes": closure.get("totalExistingLanes", ""),
                "until": f"{ts.get('closureEndDate', '')} {ts.get('closureEndTime', '')}".strip(),
            })

    _caltrans_cache["ts"] = now
    _caltrans_cache["closures"] = out
    return out


async def overpass_raw(query: str) -> dict:
    """Run any Overpass QL query with the retry loop this flaky public
    instance needs (see search_overpass's docstring for the evidence).
    Shared by the POI search and the safety-flag crossing checks."""
    headers = {"User-Agent": "Leeway-EV-Trip-Planner/0.1 (github.com/lyhjeremy/leeway)"}
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(4):
            resp = await client.post(OVERPASS_BASE, data={"data": query}, headers=headers)
            if resp.status_code in (406, 429, 503, 504) and attempt < 3:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
    return {"elements": []}


async def search_overpass(bbox: tuple[float, float, float, float], tag_filters: list[tuple[str, str]], max_results: int = 60) -> list[dict]:
    """bbox is (min_lat, min_lon, max_lat, max_lon). tag_filters is a list of
    (key, value) pairs, OR'd together (e.g. [("amenity","cafe"),("shop","coffee")]).
    Returns real OSM tags per POI - name, brand, drive_through, opening_hours,
    whatever exists - so the caller can filter/rank on real data instead of
    guessing.

    The public overpass-api.de instance is genuinely unreliable under load -
    confirmed via repeated real calls returning a mix of 406, 504, and 200 for
    the *identical* query seconds apart, with no client-side pattern (same
    result from curl and httpx, with or without a descriptive User-Agent).
    This is a documented characteristic of this free, shared community
    resource, not something to engineer around client-side beyond a real
    retry loop - so that's what this does, rather than assuming one failure
    means the query itself is bad."""
    min_lat, min_lon, max_lat, max_lon = bbox
    bbox_str = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    clauses = "".join(f'node["{k}"="{v}"]({bbox_str});' for k, v in tag_filters)
    query = f"[out:json][timeout:20];({clauses});out center {max_results};"
    data = await overpass_raw(query)

    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        lat, lon = el.get("lat"), el.get("lon")
        if lat is None or lon is None:
            continue
        out.append({
            "id": el.get("id"),
            "name": tags.get("name") or tags.get("brand") or "Unnamed",
            "brand": tags.get("brand"),
            "lat": lat,
            "lon": lon,
            "drive_through": tags.get("drive_through"),
            "opening_hours": tags.get("opening_hours"),
            "tags": tags,
        })
    return out
