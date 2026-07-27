"""The provider caches exist to stretch real free-tier quotas (ORS's daily
directions ceiling most of all) - these tests prove identical requests
genuinely skip the network, and that the cache keys on everything that
changes the answer."""

import time

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
    providers._overpass_cache.clear()
    providers.reset_overpass_breaker()
    CountingClient.requests = 0
    yield
    providers._directions_cache.clear()
    providers._stations_cache.clear()
    providers._geocode_cache.clear()
    providers._weather_cache.clear()
    providers._overpass_cache.clear()
    providers.reset_overpass_breaker()


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


def test_identical_overpass_queries_hit_network_once(counting_network):
    """Hazard detection must be consistent across replans - the flaky public
    Overpass instance returning flags on one plan and nothing on the next
    meant the detour pass silently had nothing to avoid (seen live)."""
    a = run(providers.overpass_raw('[out:json];node(1);out;'))
    b = run(providers.overpass_raw('[out:json];node(1);out;'))
    assert CountingClient.requests == 1
    assert a == b
    run(providers.overpass_raw('[out:json];node(2);out;'))
    assert CountingClient.requests == 2


class FailoverClient:
    """Primary Overpass refuses connections; the kumi mirror answers."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        FailoverClient.calls.append(url)
        if "overpass-api.de" in url:
            raise httpx.ConnectError("all connection attempts failed")
        return httpx.Response(200, json={"elements": [{"id": 1}]}, request=httpx.Request("POST", url))


class AllDownClient(FailoverClient):
    async def post(self, url, **kw):
        FailoverClient.calls.append(url)
        raise httpx.ConnectError("all connection attempts failed")


@pytest.fixture
def no_sleep(monkeypatch):
    async def instant(_s):
        return None
    monkeypatch.setattr(providers.asyncio, "sleep", instant)


def test_overpass_fails_over_to_the_mirror(counting_network, no_sleep, monkeypatch):
    monkeypatch.setattr(providers.httpx, "AsyncClient", FailoverClient)
    FailoverClient.calls = []
    result = run(providers.overpass_raw("[out:json];node(42);out;"))
    assert result == {"elements": [{"id": 1}]}
    assert "overpass-api.de" in FailoverClient.calls[0]
    assert "kumi.systems" in FailoverClient.calls[1]


def test_overpass_all_down_raises_never_pretends_empty(counting_network, no_sleep, monkeypatch):
    """'Couldn't ask' must never masquerade as 'nothing there' - an empty
    result here would tell voice search there are no cafes in LA."""
    monkeypatch.setattr(providers.httpx, "AsyncClient", AllDownClient)
    FailoverClient.calls = []
    with pytest.raises(httpx.HTTPError):
        run(providers.overpass_raw("[out:json];node(43);out;"))
    assert len(FailoverClient.calls) == 3  # every attempt across both instances


def test_breaker_stops_asking_a_dead_overpass_on_every_plan(counting_network, no_sleep, monkeypatch):
    """The real production failure: a check that times out caches nothing,
    so without a breaker EVERY later plan pays the full retry cost again -
    measured as a flat 20s on every plan for as long as the outage lasts."""
    monkeypatch.setattr(providers.httpx, "AsyncClient", AllDownClient)
    FailoverClient.calls = []
    for i in range(3):
        with pytest.raises(httpx.HTTPError):
            run(providers.overpass_raw(f"[out:json];node({100 + i});out;"))
    calls_before = len(FailoverClient.calls)

    # Breaker is open now: further queries fail instantly, touching no network.
    with pytest.raises(providers.OverpassUnavailable):
        run(providers.overpass_raw("[out:json];node(999);out;"))
    assert len(FailoverClient.calls) == calls_before


def test_breaker_still_serves_cached_answers_while_open(counting_network, no_sleep, monkeypatch):
    """An open breaker must not blind us to answers we already hold."""
    monkeypatch.setattr(providers.httpx, "AsyncClient", FailoverClient)
    FailoverClient.calls = []
    warm = run(providers.overpass_raw("[out:json];node(7);out;"))

    providers._overpass_breaker["open_until"] = time.time() + 300
    assert run(providers.overpass_raw("[out:json];node(7);out;")) == warm


def test_a_success_closes_the_breaker(counting_network, no_sleep, monkeypatch):
    monkeypatch.setattr(providers.httpx, "AsyncClient", AllDownClient)
    FailoverClient.calls = []
    for i in range(3):
        with pytest.raises(httpx.HTTPError):
            run(providers.overpass_raw(f"[out:json];node({200 + i});out;"))
    assert providers._overpass_breaker["open_until"] > 0

    providers.reset_overpass_breaker()
    monkeypatch.setattr(providers.httpx, "AsyncClient", FailoverClient)
    run(providers.overpass_raw("[out:json];node(8);out;"))
    assert providers._overpass_breaker["failures"] == 0


def test_identical_station_searches_hit_network_once(counting_network):
    a = run(providers.find_charging_stations(35.0, -119.0, radius_mi=15))
    b = run(providers.find_charging_stations(35.0, -119.0, radius_mi=15))
    assert CountingClient.requests == 1
    assert a == b and a[0]["is_supercharger"]
    # A different radius is a different search
    run(providers.find_charging_stations(35.0, -119.0, radius_mi=30))
    assert CountingClient.requests == 2
