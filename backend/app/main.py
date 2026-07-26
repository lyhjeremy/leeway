import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import gemini, providers
from .gemini import GeminiNotConfigured
from .planner import plan_trip
from .providers import OCMNotConfigured, ORSNotConfigured
from .voice import VoiceSearchError, find_stops

# Bumped by hand on real changes so a stale deploy is visible immediately in
# /api/health rather than assumed fixed - a lesson learned the hard way on an
# earlier project (see [[skillcompass-flagship-project]]).
VERSION = "0.7.1"

app = FastAPI(title="Leeway API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://lyhjeremy.github.io",
        "http://localhost:5173",
        "http://localhost:4173",
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
    forced_stop: LatLon | None = None  # "insist on this stop" - mandatory waypoint
    forced_stop_title: str = "Your chosen stop"


@app.post("/api/plan")
async def plan(req: PlanRequest):
    try:
        return await plan_trip(
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
            forced_stop=(req.forced_stop.lat, req.forced_stop.lon) if req.forced_stop else None,
            forced_stop_title=req.forced_stop_title,
        )
    except ORSNotConfigured:
        raise HTTPException(503, "Routing isn't configured yet (ORS_API_KEY missing).")
    except OCMNotConfigured:
        raise HTTPException(503, "Charging-station lookup isn't configured yet (OCM_API_KEY missing).")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Routing/charging provider error: {e}")


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
