"""Voice-driven stop-finder: natural language -> structured search -> real
POI results with a real detour check. Three real, verified pieces:

1. Parsing (Gemini): free-text -> {category, brand, drive_through_required,
   max_detour_min}. The one place this backend uses an LLM.
2. Search (Overpass): keyless OSM POI search, real tags (drive_through,
   brand, opening_hours) - not guessed. The public instance is unreliable
   under load (see providers.search_overpass's docstring); retried there.
3. Detour (ORS): every candidate's real added time is checked with an
   actual routing call comparing (through-the-POI duration) vs (direct
   duration) - same real-verify-don't-approximate discipline as the
   charging-stop search, not a straight-line guess.
"""

import json
import re

import httpx

from . import gemini, providers
from .geo import cumulative_distances_mi, nearest_route_point

# category -> OSM tag (key, value) pairs, OR'd together in the Overpass query.
# Deliberately generic from day one (not coffee-only) per the product plan.
CATEGORY_TAGS = {
    "coffee": [("amenity", "cafe"), ("shop", "coffee")],
    "gas": [("amenity", "fuel")],
    "food": [("amenity", "restaurant"), ("amenity", "fast_food")],
    "restroom": [("amenity", "toilets")],
    "grocery": [("shop", "supermarket"), ("shop", "convenience")],
    "pharmacy": [("amenity", "pharmacy")],
}

SEARCH_RADIUS_DEG = 0.15  # ~10 miles - a real "quick stop" search area, not the whole trip
MAX_CANDIDATES_VERIFIED = 6

PARSE_PROMPT = """Extract a structured stop request from this driver's spoken \
request. Respond with ONLY a JSON object, no other text, matching exactly:
{{"category": one of {categories}, "brand": string or null (e.g. "Starbucks", \
only if a specific brand/chain is named), "drive_through_required": true or \
false, "max_detour_min": integer}}

For max_detour_min, use your best judgment of what the driver implied: \
"won't add much time" or "quick" means 5, unspecified means 10, "don't mind \
a longer detour" or similar means 20.

Driver's request: "{query}"
"""


class VoiceSearchError(Exception):
    pass


def _poi_address(tags: dict) -> str | None:
    """Street address from OSM addr:* tags - the thing that tells two
    same-brand results apart. Honest None when OSM has no address for the
    POI rather than a made-up one."""
    street_part = " ".join(x for x in [tags.get("addr:housenumber"), tags.get("addr:street")] if x)
    parts = [p for p in [street_part, tags.get("addr:city")] if p]
    return ", ".join(parts) or None


async def parse_voice_query(text: str) -> dict:
    prompt = PARSE_PROMPT.format(categories=list(CATEGORY_TAGS.keys()), query=text)
    raw = await gemini.gemini_generate_json(prompt, max_tokens=200)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Gemini occasionally wraps JSON in a code fence despite the
        # instruction not to - strip one before giving up.
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip())
        parsed = json.loads(cleaned)

    category = parsed.get("category")
    if category not in CATEGORY_TAGS:
        raise VoiceSearchError(f"Couldn't match \"{text}\" to a known stop type ({', '.join(CATEGORY_TAGS)}).")
    return {
        "category": category,
        "brand": parsed.get("brand"),
        "drive_through_required": bool(parsed.get("drive_through_required")),
        "max_detour_min": int(parsed.get("max_detour_min") or 10),
    }


async def find_stops(query_text: str, origin: tuple[float, float], destination: tuple[float, float]) -> dict:
    parsed = await parse_voice_query(query_text)

    route = await providers.directions(origin, destination)
    coords = route["geometry"]
    cum = cumulative_distances_mi(coords)

    # Search near the route's midpoint with a real ~10mi radius, not the
    # whole trip's bounding box - both more reliable (a smaller Overpass
    # query is less likely to time out) and truer to the actual ask: "a
    # stop along the way" means somewhere near where you are, not anywhere
    # across a 400-mile trip.
    mid_lon, mid_lat = coords[len(coords) // 2]
    bbox = (mid_lat - SEARCH_RADIUS_DEG, mid_lon - SEARCH_RADIUS_DEG, mid_lat + SEARCH_RADIUS_DEG, mid_lon + SEARCH_RADIUS_DEG)

    try:
        pois = await providers.search_overpass(bbox, CATEGORY_TAGS[parsed["category"]])
    except httpx.HTTPError:
        # Both community Overpass instances down/overloaded - say what
        # actually happened instead of a raw "connection attempts failed".
        raise VoiceSearchError(
            "The map data source behind stop search (OpenStreetMap's Overpass) is "
            "overloaded right now - the trip plan itself is unaffected. Try the "
            "search again in a minute."
        )

    if parsed["brand"]:
        brand_lower = parsed["brand"].lower()
        brand_matches = [p for p in pois if brand_lower in (p["name"] or "").lower() or brand_lower in (p["brand"] or "").lower()]
        if brand_matches:
            pois = brand_matches

    if parsed["drive_through_required"]:
        dt_matches = [p for p in pois if p["drive_through"] == "yes"]
        if dt_matches:
            pois = dt_matches
        # else: fall through to all candidates - OSM's drive_through tag
        # coverage is spotty, and no real candidates is worse than an
        # untagged one that might still have a drive-through.

    if not pois:
        return {"parsed": parsed, "results": [], "note": f"No {parsed['category']} found near the route."}

    for p in pois:
        p["distance_along_route_mi"], p["offset_from_route_mi"] = nearest_route_point(coords, cum, p["lat"], p["lon"])
    pois.sort(key=lambda p: p["offset_from_route_mi"])

    direct_duration_min = route["duration_min"]
    verified = []
    for p in pois[:MAX_CANDIDATES_VERIFIED]:
        leg1 = await providers.directions(origin, (p["lat"], p["lon"]))
        leg2 = await providers.directions((p["lat"], p["lon"]), destination)
        detour_min = round((leg1["duration_min"] + leg2["duration_min"]) - direct_duration_min)
        detour_mi = max(0.0, round((leg1["distance_mi"] + leg2["distance_mi"]) - route["distance_mi"], 1))
        verified.append({
            "name": p["name"],
            "brand": p["brand"],
            "lat": p["lat"],
            "lon": p["lon"],
            "address": _poi_address(p.get("tags") or {}),
            "drive_through": p["drive_through"],
            "opening_hours": p["opening_hours"],
            "detour_min": detour_min,
            "detour_mi": detour_mi,
            "within_budget": detour_min <= parsed["max_detour_min"],
        })

    verified.sort(key=lambda v: v["detour_min"])
    in_budget = [v for v in verified if v["within_budget"]]
    return {
        "parsed": parsed,
        "results": in_budget or verified,  # honest fallback: show the closest even if over budget, clearly marked
        "note": None if in_budget else f"Nothing found within {parsed['max_detour_min']} min - showing the closest option instead.",
    }
