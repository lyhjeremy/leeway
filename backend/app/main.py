import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal

from pydantic import BaseModel, Field

from . import gemini, providers
from .gemini import GeminiNotConfigured
from .planner import localize_c, localize_km, plan_trip
from .providers import OCMNotConfigured, ORSNotConfigured
from .voice import VoiceSearchError, find_stops

# Bumped by hand on real changes so a stale deploy is visible immediately in
# /api/health rather than assumed fixed - a lesson learned the hard way on an
# earlier project (see [[skillcompass-flagship-project]]).
VERSION = "0.16.5"

app = FastAPI(title="Leeway API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://lyhjeremy.github.io",
        "http://localhost:5173",
        "http://localhost:4173",
        # The iOS app (Capacitor WKWebView) serves the bundled frontend from
        # its own scheme - these are its Origin headers, not real hosts.
        "capacitor://localhost",
        "https://localhost",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "ors_configured": bool(providers.ORS_API_KEY),
        "ocm_configured": bool(providers.OCM_API_KEY),
        "gemini_configured": bool(gemini.GEMINI_API_KEY),
    }


@app.get("/api/geocode")
async def geocode(q: str = Query(min_length=2)):
    try:
        return {"results": await providers.geocode(q)}
    except ORSNotConfigured:
        raise HTTPException(503, "Routing isn't configured yet (ORS_API_KEY missing).")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Geocoding provider error: {e}")


class LatLon(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Waypoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    title: str = Field(default="Your stop", max_length=120)
    hidden: bool = False  # a route-shaping via point, not a place you care to see listed


# Bounds found the hard way in a stress test: full_range_mi=0 crashed with a
# ZeroDivisionError deep in the range math, a negative range produced a
# "feasible" plan where the battery charged itself while driving, and
# battery_pct=150 predicted arrival at 143%. Garbage gets a 422 here instead.
class PlanRequest(BaseModel):
    origin: LatLon
    destination: LatLon
    battery_pct: float = Field(gt=0, le=100)
    full_range_mi: float = Field(ge=10, le=600)
    reserve_pct: float = Field(default=15.0, ge=0, le=50)
    reserve_mi: float = Field(default=30.0, ge=0, le=200)
    charge_to_pct: float = Field(default=80.0, ge=30, le=100)
    stop_mode: str = "fewest_stops"  # fewest_stops | fastest_trip | best_amenities
    avoid_tolls: bool = False  # tolls allowed by default, per the product plan
    avoid_highways: bool = False
    excluded_station_ids: list[int] = []  # "skip this stop" - re-plan without these OCM station IDs
    waypoints: list[Waypoint] = Field(default=[], max_length=5)  # visited in order, battery passes through
    safety_mode: Literal["flag_only", "avoid_quick", "avoid_hard"] = "flag_only"
    charger_filter: Literal["all", "tesla_only", "non_tesla"] = "all"
    arrival_target_pct: float = Field(default=0.0, ge=0, le=95)  # arrive with at least this much
    passengers: int = Field(default=0, ge=0, le=6)  # beyond the driver
    suitcases: int = Field(default=0, ge=0, le=10)
    temp_override_f: float | None = Field(default=None, ge=-30, le=130)
    hazard_types: list[Literal["unprotected_left", "wide_crossing", "rail_crossing", "lane_closure"]] = Field(
        default=["unprotected_left", "wide_crossing", "rail_crossing", "lane_closure"],
    )
    departure_epoch: float | None = None  # unix seconds; None = leaving now
    max_stint_min: float = Field(default=0.0, ge=0, le=600)  # 0 = off
    preferred_networks: list[str] = Field(default=[], max_length=8)
    min_charger_kw: float = Field(default=20.0, ge=20, le=350)
    avoid_ferries: bool = False
    # Stage 5 feedback loop: consumption multiplier the client computes from
    # its own logged trips (the logs live in the browser, so the client owns
    # the math - see computeCalibration in storage.ts). Bounds are defense in
    # depth on top of the client's own safe-side clamps: a corrupted or
    # hand-crafted value can't halve consumption or double it.
    calibration_factor: float = Field(default=1.0, ge=0.9, le=1.5)
    units: Literal["mi", "km"] = "mi"  # numeric fields stay miles; narrative strings get localized
    temp_unit: Literal["F", "C"] = "F"  # independent of distance - km with °F is a real combination


@app.post("/api/plan")
async def plan(req: PlanRequest):
    # The reserve floor is max(reserve_pct, reserve_mi as a %), so a small
    # full range can push the floor above charge_to_pct - and a plan that
    # charges to at-or-below its own floor can never make progress (the
    # planner would pick the same station over and over until the stop cap).
    floor_pct = max(req.reserve_pct, req.reserve_mi / req.full_range_mi * 100)
    if req.charge_to_pct <= floor_pct + 5:
        raise HTTPException(
            422,
            f"Charging to {req.charge_to_pct:.0f}% can't clear your reserve floor of "
            f"{floor_pct:.0f}% (your {req.reserve_mi:.0f}-mile reserve is a big share of a "
            f"{req.full_range_mi:.0f}-mile range). Raise the charge-to level or lower the reserve.",
        )
    import time as _time

    if req.departure_epoch is not None:
        now = _time.time()
        if not (now - 7200 <= req.departure_epoch <= now + 7 * 86400):
            raise HTTPException(
                422,
                "Departure must be between two hours ago and seven days out - "
                "the weather forecast doesn't reach further.",
            )
    if 0 < req.max_stint_min < 30:
        raise HTTPException(422, "A break rhythm under 30 minutes would stop more than it drives.")
    if req.arrival_target_pct > req.charge_to_pct - 10:
        raise HTTPException(
            422,
            f"An arrival target of {req.arrival_target_pct:.0f}% needs headroom below the "
            f"{req.charge_to_pct:.0f}% charge-to level - lower the target or raise charge-to.",
        )
    try:
        result = await plan_trip(
            origin=(req.origin.lat, req.origin.lon),
            destination=(req.destination.lat, req.destination.lon),
            battery_pct=req.battery_pct,
            full_range_mi=req.full_range_mi,
            reserve_pct=req.reserve_pct,
            reserve_mi=req.reserve_mi,
            charge_to_pct=req.charge_to_pct,
            stop_mode=req.stop_mode,
            avoid_tolls=req.avoid_tolls,
            avoid_highways=req.avoid_highways,
            excluded_station_ids=tuple(req.excluded_station_ids),
            waypoints=tuple(w.model_dump() for w in req.waypoints),
            safety_mode=req.safety_mode,
            charger_filter=req.charger_filter,
            arrival_target_pct=req.arrival_target_pct,
            passengers=req.passengers,
            suitcases=req.suitcases,
            temp_override_f=req.temp_override_f,
            hazard_types=tuple(req.hazard_types),
            departure_epoch=req.departure_epoch,
            max_stint_min=req.max_stint_min,
            preferred_networks=tuple(n[:40] for n in req.preferred_networks),
            min_charger_kw=req.min_charger_kw,
            avoid_ferries=req.avoid_ferries,
            calibration_factor=req.calibration_factor,
        )
        if req.units == "km":
            result = localize_km(result)
        if req.temp_unit == "C":
            result = localize_c(result)
        return result
    except ORSNotConfigured:
        raise HTTPException(503, "Routing isn't configured yet (ORS_API_KEY missing).")
    except OCMNotConfigured:
        raise HTTPException(503, "Charging-station lookup isn't configured yet (OCM_API_KEY missing).")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Routing/charging provider error: {e}")


class RoutesRequest(BaseModel):
    origin: LatLon
    destination: LatLon
    avoid_tolls: bool = False
    avoid_highways: bool = False
    avoid_ferries: bool = False


@app.post("/api/routes")
async def routes(req: RoutesRequest):
    """Alternative route corridors for the picker - overview only, the real
    plan re-runs along whichever one gets chosen."""
    try:
        return {
            "routes": await providers.directions_alternatives(
                (req.origin.lat, req.origin.lon),
                (req.destination.lat, req.destination.lon),
                req.avoid_tolls,
                req.avoid_highways,
                req.avoid_ferries,
            )
        }
    except ORSNotConfigured:
        raise HTTPException(503, "Routing isn't configured yet (ORS_API_KEY missing).")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Routing provider error: {e}")


class VoiceSearchRequest(BaseModel):
    query: str
    origin: LatLon
    destination: LatLon


@app.post("/api/voice-search")
async def voice_search(req: VoiceSearchRequest):
    try:
        return await find_stops(req.query, (req.origin.lat, req.origin.lon), (req.destination.lat, req.destination.lon))
    except GeminiNotConfigured:
        raise HTTPException(503, "Voice search isn't configured yet (GEMINI_API_KEY missing).")
    except ORSNotConfigured:
        raise HTTPException(503, "Routing isn't configured yet (ORS_API_KEY missing).")
    except VoiceSearchError as e:
        raise HTTPException(422, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Voice search provider error: {e}")
