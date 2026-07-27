"""The provider caches exist to stretch real free-tier quotas (ORS's daily
directions ceiling most of all) - these tests prove identical requests
genuinely skip the network, and that the cache keys on everything that
changes the answer."""

import httpx
import pytest

from app import providers
from app.providers import _TTLCache
from conftest import run


def test_ttl_cache_get_set_and_expiry():
    cache = _TTLCache(ttl_s=60, max_entries=8)
    assert cache.get("k") is None
    cache.set("k", {"v": 1})
    assert cache.get("k") == {"v": 1}

    expired = _TTLCache(ttl_s=-1, max_entries=8)  # everything born expired
    expired.set("k", 1)
    assert expired.get("k") is None


def test_ttl_cache_eviction_keeps_newest():
    cache = _TTLCache(ttl_s=60, max_entries=4)
    for i in range(4):
        cache.set(i, i)
    cache.set(4, 4)  # over the cap: evicts the oldest quarter
    assert cache.get(0) is None
    assert cache.get(4) == 4


FAKE_ORS_GEOJSON = {
    "features": [{
        "properties": {
            "summary": {"distance": 160_934.0, "duration": 5_400.0},  # 100mi, 90min
            "ascent": 100.0,
            "descent": 50.0,
            "extras": {"waycategory": {"values": [[0, 10, 1]]}},
        },
        "geometry": {"coordinates": [[-118.24, 34.05, 100.0], [-122.42, 37.77, 50.0]]},
    }]
}

FAKE_OCM = [{
    "ID": 1,
    "AddressInfo": {"Title": "Test SC", "Latitude": 35.0, "Longitude": -119.0},
    "OperatorInfo": {"Title": "Tesla"},
    "Connections": [{"PowerKW": 250, "ConnectionType": {"Title": "NACS"}}],
}]


class CountingClient:
    """Stands in for httpx.AsyncClient; serves canned bodies and counts hits."""

    requests = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        CountingClient.requests += 1
        return httpx.Response(200, json=FAKE_ORS_GEOJSON, request=httpx.Request("POST", url))

    async def get(self, url, **kwargs):
        CountingClient.requests += 1
        return httpx.Response(200, json=FAKE_OCM, request=httpx.Request("GET", url))


@pytest.fixture
def counting_network(monkeypatch):
    monkeypatch.setattr(providers.httpx, "AsyncClient", CountingClient)
    monkeypatch.setattr(providers, "ORS_API_KEY", "test-key")
    monkeypatch.setattr(providers, "OCM_API_KEY", "test-key")
    providers._directions_cache.clear()
    providers._stations_cache.clear()
    providers._geocode_cache.clear()
    providers._weather_cache.clear()
    CountingClient.requests = 0
    yield
    providers._directions_cache.clear()
    providers._stations_cache.clear()
    providers._geocode_cache.clear()
    providers._weather_cache.clear()


def test_identical_directions_requests_hit_network_once(counting_network):
    a = run(providers.directions((34.05, -118.24), (37.77, -122.42)))
    b = run(providers.directions((34.05, -118.24), (37.77, -122.42)))
    assert CountingClient.requests == 1
    assert b == a
    assert abs(a["distance_mi"] - 100) < 0.1


def test_directions_cache_keys_on_options(counting_network):
    run(providers.directions((34.05, -118.24), (37.77, -122.42)))
    # Same endpoints, different constraints: must NOT reuse the cached route
    run(providers.directions((34.05, -118.24), (37.77, -122.42), avoid_highways=True))
    run(providers.directions((34.05, -118.24), (37.77, -122.42), avoid_polygons=[[[0, 0], [0, 1], [1, 1], [0, 0]]]))
    run(providers.directions((34.05, -118.24), (37.77, -122.42), via=(35.0, -119.0)))
    assert CountingClient.requests == 4


def test_identical_station_searches_hit_network_once(counting_network):
    a = run(providers.find_charging_stations(35.0, -119.0, radius_mi=15))
    b = run(providers.find_charging_stations(35.0, -119.0, radius_mi=15))
    assert CountingClient.requests == 1
    assert a == b and a[0]["is_supercharger"]
    # A different radius is a different search
    run(providers.find_charging_stations(35.0, -119.0, radius_mi=30))
    assert CountingClient.requests == 2
