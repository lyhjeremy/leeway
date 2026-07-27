"""House-number geocoding: the hosted ORS/Pelias geocoder tops out at
street level for most addresses (no interpolation), so a query that starts
with a house number falls through to the US Census geocoder and its match
goes first. Found via a real user query: '555 levering ave' only ever
offered 'Levering Avenue'."""

import httpx
import pytest

from app import providers
from conftest import run

PELIAS_STREET_ONLY = {
    "features": [{
        "geometry": {"coordinates": [-118.4545, 34.0667]},
        "properties": {"label": "Levering Avenue, Los Angeles, CA, USA"},
    }]
}

PELIAS_WITH_HOUSE = {
    "features": [{
        "geometry": {"coordinates": [-122.3937, 37.7955]},
        "properties": {"label": "1 Ferry Building, San Francisco, CA, USA", "housenumber": "1"},
    }]
}

CENSUS_MATCH = {
    "result": {"addressMatches": [{
        "matchedAddress": "555 LEVERING AVE, LOS ANGELES, CA, 90024",
        "coordinates": {"x": -118.45454, "y": 34.0667},
    }]}
}

CENSUS_EMPTY = {"result": {"addressMatches": []}}


class FakeClient:
    pelias = PELIAS_STREET_ONLY
    census = CENSUS_MATCH
    census_calls = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, **kw):
        if "census.gov" in url:
            FakeClient.census_calls += 1
            body = FakeClient.census
        else:
            body = FakeClient.pelias
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


@pytest.fixture
def fake_geocoders(monkeypatch):
    monkeypatch.setattr(providers.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(providers, "ORS_API_KEY", "test-key")
    providers._geocode_cache.clear()
    FakeClient.pelias = PELIAS_STREET_ONLY
    FakeClient.census = CENSUS_MATCH
    FakeClient.census_calls = 0
    yield
    providers._geocode_cache.clear()


def test_house_number_query_gets_exact_census_match_first(fake_geocoders):
    results = run(providers.geocode("555 levering ave"))
    assert results[0]["label"] == "555 Levering Ave, Los Angeles, CA 90024"
    assert abs(results[0]["lat"] - 34.0667) < 1e-4
    # The street-level Pelias result is still offered, just not first
    assert any("Levering Avenue" in r["label"] for r in results[1:])


def test_no_census_call_when_pelias_already_has_the_house_number(fake_geocoders):
    FakeClient.pelias = PELIAS_WITH_HOUSE
    results = run(providers.geocode("1 Ferry Building, San Francisco"))
    assert FakeClient.census_calls == 0
    assert results[0]["label"].startswith("1 Ferry Building")


def test_no_census_call_without_a_leading_house_number(fake_geocoders):
    run(providers.geocode("Culver City"))
    assert FakeClient.census_calls == 0


def test_census_miss_leaves_pelias_results_untouched(fake_geocoders):
    FakeClient.census = CENSUS_EMPTY
    results = run(providers.geocode("99999 levering ave"))
    assert results[0]["label"].startswith("Levering Avenue")
