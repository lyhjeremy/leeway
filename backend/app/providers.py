"""Thin clients for the external data sources this backend depends on:
OpenRouteService (geocoding + directions + elevation, needs a free API key),
Open Charge Map (charging stations, no key needed under 250 results/call),
Open-Meteo (current weather, no key needed at all - confirmed via a real
call, not assumed), and Overpass (OSM point-of-interest search for the
voice stop-finder, no key needed - confirmed via a real cafe search that
returned real drive_through/brand tags).
"""

import asyncio
import os

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


class ORSNotConfigured(Exception):
    pass


def _require_key():
    if not ORS_API_KEY:
        raise ORSNotConfigured("ORS_API_KEY is not set")


async def geocode(query: str) -> list[dict]:
    _require_key()
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

    body = {
        "coordinates": [[olon, olat], [dlon, dlat]],
        "elevation": True,
        "extra_info": ["waycategory"],
    }
    options: dict = {}
    if avoid_features:
        options["avoid_features"] = avoid_features
    if avoid_polygons:
        options["avoid_polygons"] = {"type": "MultiPolygon", "coordinates": [[ring] for ring in avoid_polygons]}
    if options:
        body["options"] = options

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

    return {
        "distance_mi": distance_mi,
        "duration_min": duration_min,
        "ascent_ft": ascent_m * FT_PER_METER,
        "descent_ft": descent_m * FT_PER_METER,
        "highway_fraction": highway_fraction,
        "geometry": coords,
        "elevations_m": elevations_m,
    }


class OCMNotConfigured(Exception):
    pass


async def find_charging_stations(lat: float, lon: float, radius_mi: float = 15, max_results: int = 50) -> list[dict]:
    if not OCM_API_KEY:
        raise OCMNotConfigured("OCM_API_KEY is not set")
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
                "verbose": "false",
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
        out.append({
            "id": poi.get("ID"),
            "title": title,
            "lat": addr.get("Latitude"),
            "lon": addr.get("Longitude"),
            "network": operator or "Unknown network",
            "is_supercharger": is_supercharger,
            "max_kw": max_kw,
            "connector_count": len(conns),
        })
    return [s for s in out if s["lat"] is not None and s["lon"] is not None]


async def current_weather(lat: float, lon: float) -> dict:
    """No API key needed at all - real call confirmed working. Returns a
    trip-day snapshot at one point (the route's midpoint - see planner.py),
    not per-segment; weather varies continuously along any real route, so a
    single snapshot is already the honest level of precision here."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{METEO_BASE}/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,wind_speed_10m,wind_direction_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    current = data["current"]
    return {
        "temp_f": current["temperature_2m"],
        "wind_speed_mph": current["wind_speed_10m"],
        "wind_from_deg": current["wind_direction_10m"],
    }


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
