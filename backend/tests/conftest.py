"""Shared fixtures: a fully faked provider layer so every test runs offline.

The fakes stand in for ORS directions, OCM station search, Open-Meteo,
Overpass, and the Caltrans feeds. Routes are straight lines with a
configurable speed, highway fraction, and elevation profile - enough for the
planner's math to run for real while touching zero network. This is the same
approach used to verify the v0.14 batch offline when the routing quota was
resting; committing it means every future change gets the same check for
free.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app import planner, providers  # noqa: E402
from app.geo import cumulative_distances_mi, haversine_mi  # noqa: E402

LA = (34.05, -118.24)
SF = (37.7749, -122.4194)


def run(coro):
    return asyncio.run(coro)


class FakeWorld:
    """One knob-covered stand-in for every external data source."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch):
        self.stations: list[dict] = []
        self.weather = {"temp_f": 70.0, "wind_speed_mph": 0.0, "wind_from_deg": 0.0, "forecast": False}
        self.mph = 60.0
        self.highway_fraction = 0.8
        # meters of elevation at a given mile along any route; flat by default
        self.elevation_at_mile = lambda mi: 100.0
        self.overpass_result: dict = {"elements": []}
        self.closures: list[dict] = []
        self.fail_ocm = False
        # Per-call scripted failures for directions: a list of HTTP status
        # ints consumed one per call (None = succeed). Empty = always succeed.
        self.directions_script: list[int | None] = []
        self.directions_calls: list[tuple] = []
        self._next_station_id = 1

        monkeypatch.setattr(providers, "directions", self._directions)
        monkeypatch.setattr(providers, "find_charging_stations", self._find_stations)
        monkeypatch.setattr(providers, "current_weather", self._weather)
        monkeypatch.setattr(providers, "overpass_raw", self._overpass)
        monkeypatch.setattr(providers, "caltrans_closures", self._caltrans)
        # Retry waits are real seconds; tests shouldn't sleep through them.
        monkeypatch.setattr(planner, "_RETRY_DELAYS_S", [0, 0, 0])

    # -- station helpers ---------------------------------------------------

    def add_station(self, lat: float, lon: float, **kw) -> dict:
        s = {
            "id": self._next_station_id,
            "title": kw.get("title", f"Station {self._next_station_id}"),
            "lat": lat,
            "lon": lon,
            "network": kw.get("network", "Tesla (Supercharger)"),
            "is_supercharger": kw.get("is_supercharger", True),
            "max_kw": kw.get("max_kw", 250),
            "connector_count": kw.get("connector_count", 8),
            "stall_count": kw.get("stall_count", 12),
            "cost": kw.get("cost"),
            "photo_url": kw.get("photo_url"),
        }
        self._next_station_id += 1
        self.stations.append(s)
        return s

    def stations_along(self, origin, destination, every_mi: float = 40.0, **kw) -> list[dict]:
        """Stations dotted along the straight origin->destination line."""
        total = haversine_mi(origin[0], origin[1], destination[0], destination[1])
        out = []
        mi = every_mi
        while mi < total:
            frac = mi / total
            lat = origin[0] + (destination[0] - origin[0]) * frac
            lon = origin[1] + (destination[1] - origin[1]) * frac
            out.append(self.add_station(lat, lon, **kw))
            mi += every_mi
        return out

    # -- provider fakes ----------------------------------------------------

    async def _directions(self, origin, destination, avoid_tolls=False, avoid_highways=False,
                          avoid_polygons=None, via=None, avoid_ferries=False):
        self.directions_calls.append((origin, destination))
        # ORS rejects avoid_polygons past ~150km with a 2004 error, returned as
        # an HTTP 400. The fake owes callers that limit, or the planner's
        # long-leg handling is never actually exercised here.
        if avoid_polygons and haversine_mi(*origin, *destination) > 93.0:
            req = httpx.Request("POST", providers.ORS_BASE)
            raise httpx.HTTPStatusError(
                "fake 2004: avoid_polygons too large", request=req,
                response=httpx.Response(400, request=req),
            )
        if self.directions_script:
            status = self.directions_script.pop(0)
            if status is not None:
                req = httpx.Request("POST", providers.ORS_BASE)
                raise httpx.HTTPStatusError(
                    f"fake {status}", request=req, response=httpx.Response(status, request=req)
                )

        points = [origin]
        if via is not None:
            points.append(via)
        points.append(destination)

        coords: list[tuple[float, float]] = []
        for (alat, alon), (blat, blon) in zip(points, points[1:]):
            seg_mi = haversine_mi(alat, alon, blat, blon)
            n = max(2, int(seg_mi) + 1)
            for i in range(n):
                frac = i / (n - 1)
                coords.append((alon + (blon - alon) * frac, alat + (blat - alat) * frac))

        cum = cumulative_distances_mi(coords)
        elevations_m = [self.elevation_at_mile(mi) for mi in cum]
        ascent_m = sum(max(0.0, b - a) for a, b in zip(elevations_m, elevations_m[1:]))
        descent_m = sum(max(0.0, a - b) for a, b in zip(elevations_m, elevations_m[1:]))
        distance_mi = cum[-1]
        return {
            "distance_mi": distance_mi,
            "duration_min": distance_mi / self.mph * 60,
            "ascent_ft": ascent_m * 3.28084,
            "descent_ft": descent_m * 3.28084,
            "highway_fraction": self.highway_fraction,
            "geometry": coords,
            "elevations_m": elevations_m,
        }

    async def _find_stations(self, lat, lon, radius_mi=15, max_results=50):
        if self.fail_ocm:
            raise httpx.ConnectError("fake OCM outage")
        near = [s for s in self.stations if haversine_mi(lat, lon, s["lat"], s["lon"]) <= radius_mi]
        near.sort(key=lambda s: haversine_mi(lat, lon, s["lat"], s["lon"]))
        return near[:max_results]

    async def _weather(self, lat, lon, at_epoch=None):
        return dict(self.weather)

    async def _overpass(self, query):
        return self.overpass_result

    async def _caltrans(self):
        return self.closures


@pytest.fixture
def world(monkeypatch):
    return FakeWorld(monkeypatch)
