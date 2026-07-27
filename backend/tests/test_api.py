import time

from fastapi.testclient import TestClient

from app.main import VERSION, app
from conftest import LA, SF

client = TestClient(app)


def _plan_body(**overrides):
    body = {
        "origin": {"lat": LA[0], "lon": LA[1]},
        "destination": {"lat": SF[0], "lon": SF[1]},
        "battery_pct": 90,
        "full_range_mi": 205,
    }
    body.update(overrides)
    return body


def test_health_reports_version_and_config():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == VERSION
    for key in ("ors_configured", "ocm_configured", "gemini_configured"):
        assert isinstance(data[key], bool)


def test_garbage_inputs_get_422_not_crashes():
    # The stress-test findings, kept honest forever
    assert client.post("/api/plan", json=_plan_body(battery_pct=150)).status_code == 422
    assert client.post("/api/plan", json=_plan_body(battery_pct=0)).status_code == 422
    assert client.post("/api/plan", json=_plan_body(full_range_mi=0)).status_code == 422
    assert client.post("/api/plan", json=_plan_body(full_range_mi=-50)).status_code == 422
    assert client.post("/api/plan", json=_plan_body(origin={"lat": 999, "lon": 0})).status_code == 422


def test_charge_to_below_reserve_floor_rejected():
    # 100mi range -> 30mi reserve floor is 30%; charging to 33% can't clear it
    resp = client.post("/api/plan", json=_plan_body(full_range_mi=100, charge_to_pct=33))
    assert resp.status_code == 422
    assert "reserve floor" in resp.json()["detail"]


def test_departure_beyond_forecast_window_rejected():
    resp = client.post("/api/plan", json=_plan_body(departure_epoch=time.time() + 30 * 86400))
    assert resp.status_code == 422
    assert "seven days" in resp.json()["detail"]


def test_stint_under_30_minutes_rejected():
    resp = client.post("/api/plan", json=_plan_body(max_stint_min=15))
    assert resp.status_code == 422


def test_calibration_factor_clamped_at_the_door():
    # The client computes this from its own logs; a corrupted or hand-crafted
    # value must not be able to halve consumption or double it
    assert client.post("/api/plan", json=_plan_body(calibration_factor=0.5)).status_code == 422
    assert client.post("/api/plan", json=_plan_body(calibration_factor=2.5)).status_code == 422


def test_arrival_target_needs_headroom_below_charge_to():
    resp = client.post("/api/plan", json=_plan_body(arrival_target_pct=75, charge_to_pct=80))
    assert resp.status_code == 422


def test_full_plan_through_the_api(world):
    world.stations_along(LA, SF, every_mi=40)
    resp = client.post("/api/plan", json=_plan_body())
    assert resp.status_code == 200
    plan = resp.json()
    assert plan["feasible"]
    assert plan["stops"]
    assert plan["arrival_pct"] >= plan["reserve_floor_pct"]
    # Whole numbers only - decimals implied accuracy the model doesn't have
    assert plan["arrival_pct"] == int(plan["arrival_pct"])


def test_km_and_celsius_localization_through_the_api(world):
    resp = client.post("/api/plan", json=_plan_body(units="km", temp_unit="C"))
    assert resp.status_code == 200
    plan = resp.json()
    # 70°F fake weather reads as 21°C
    assert "21°C" in plan["weather"]["summary"]
    # The no-charger note (no stations in this world) speaks km
    assert plan["note"] and "km" in plan["note"]


def test_routes_endpoint_returns_baseline_corridor(world):
    resp = client.post("/api/routes", json={
        "origin": {"lat": LA[0], "lon": LA[1]},
        "destination": {"lat": SF[0], "lon": SF[1]},
    })
    assert resp.status_code == 200
    routes = resp.json()["routes"]
    assert routes, "at least the baseline corridor must come back"
    assert routes[0]["via"] is None
    assert routes[0]["distance_mi"] > 300
