"""Thin clients for the two external data sources this backend depends on:
OpenRouteService (geocoding + directions + elevation, needs a free API key)
and Open Charge Map (charging stations, no key needed under 250 results/call).
"""

import os

import httpx

from .geo import FT_PER_METER, MI_PER_METER

ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
OCM_API_KEY = os.environ.get("OCM_API_KEY", "")
ORS_BASE = "https://api.openrouteservice.org"
OCM_BASE = "https://api.openchargemap.io/v3"

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


async def directions(origin: tuple[float, float], destination: tuple[float, float]) -> dict:
    """origin/destination are (lat, lon). Returns distance_mi, duration_min,
    ascent_ft, descent_ft, highway_fraction, and geometry as [(lon, lat), ...]."""
    _require_key()
    olat, olon = origin
    dlat, dlon = destination
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{ORS_BASE}/v2/directions/driving-car/geojson",
            headers={"Authorization": ORS_API_KEY, "Content-Type": "application/json"},
            json={
                "coordinates": [[olon, olat], [dlon, dlat]],
                "elevation": True,
                "extra_info": ["waycategory"],
            },
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
