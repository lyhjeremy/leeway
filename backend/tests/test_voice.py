"""Voice stop-finder results must be tellable-apart: two same-brand hits
need their street address, added minutes AND added distance - '+1 min' twice
with no address was a real user confusion."""

import pytest

from app import gemini, voice
from conftest import LA, run

DEST = (34.9, -118.24)  # ~59mi north of LA

PARSED = '{"category": "coffee", "brand": "Starbucks", "drive_through_required": false, "max_detour_min": 5}'


@pytest.fixture
def canned_gemini(monkeypatch):
    async def fake_generate(prompt, max_tokens=200):
        return PARSED
    monkeypatch.setattr(gemini, "gemini_generate_json", fake_generate)


def test_overpass_outage_reports_honestly(world, canned_gemini, monkeypatch):
    """Both Overpass instances down surfaced as a raw 'all connection
    attempts failed' 502 to the user - it must say what happened and that
    the plan itself is fine."""
    import httpx

    from app import providers

    async def down(*a, **k):
        raise httpx.ConnectError("all connection attempts failed")

    monkeypatch.setattr(providers, "search_overpass", down)
    with pytest.raises(voice.VoiceSearchError) as e:
        run(voice.find_stops("starbucks along the way", LA, DEST))
    assert "overloaded" in str(e.value)
    assert "unaffected" in str(e.value)


def test_results_carry_address_and_detour_distance(world, canned_gemini):
    mid_lat = (LA[0] + DEST[0]) / 2
    world.overpass_result = {
        "elements": [
            {"id": 1, "lat": mid_lat, "lon": -118.26, "tags": {
                "name": "Starbucks", "brand": "Starbucks",
                "addr:housenumber": "10861", "addr:street": "Weyburn Ave", "addr:city": "Los Angeles",
            }},
            {"id": 2, "lat": mid_lat + 0.02, "lon": -118.22, "tags": {
                "name": "Starbucks", "brand": "Starbucks",
            }},
        ]
    }
    out = run(voice.find_stops("starbucks along the way", LA, DEST))
    assert len(out["results"]) == 2
    by_addr = {r["address"] for r in out["results"]}
    # The tagged one carries its full street address; the untagged one is an
    # honest None, never an invented address
    assert "10861 Weyburn Ave, Los Angeles" in by_addr
    assert None in by_addr
    for r in out["results"]:
        assert r["detour_mi"] is not None and r["detour_mi"] >= 0
        assert isinstance(r["detour_min"], int)
