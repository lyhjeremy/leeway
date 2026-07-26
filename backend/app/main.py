import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import providers
from .planner import plan_trip
from .providers import OCMNotConfigured, ORSNotConfigured

# Bumped by hand on real changes so a stale deploy is visible immediately in
# /api/health rather than assumed fixed - a lesson learned the hard way on an
# earlier project (see [[skillcompass-flagship-project]]).
VERSION = "0.3.1"

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
    lat: float
    lon: float


class PlanRequest(BaseModel):
    origin: LatLon
    destination: LatLon
    battery_pct: float
    full_range_mi: float
    reserve_pct: float = 15.0
    reserve_mi: float = 30.0
    charge_to_pct: float = 80.0
    stop_mode: str = "fewest_stops"  # fewest_stops | fastest_trip | best_amenities
    avoid_tolls: bool = False  # tolls allowed by default, per the product plan
    avoid_highways: bool = False


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
        )
    except ORSNotConfigured:
        raise HTTPException(503, "Routing isn't configured yet (ORS_API_KEY missing).")
    except OCMNotConfigured:
        raise HTTPException(503, "Charging-station lookup isn't configured yet (OCM_API_KEY missing).")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Routing/charging provider error: {e}")
