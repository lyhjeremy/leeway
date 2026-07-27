from datetime import datetime, timedelta, timezone

from app.sun import check_sun_glare, solar_position

LA_LAT, LA_LON = 34.05, -118.24


def _scan_day(bearing: float):
    """Minutes during 2026-07-01 (UTC) where the glare check fires in LA."""
    hits = []
    t = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for minute in range(0, 24 * 60, 10):
        dt = t + timedelta(minutes=minute)
        if check_sun_glare(LA_LAT, LA_LON, dt, bearing):
            hits.append(dt)
    return hits


def test_glare_fires_heading_west_at_sunset_but_not_east():
    westbound_hits = _scan_day(270)
    assert westbound_hits, "a summer evening heading due west must glare at some point"
    # At those same times, heading east must NOT glare (sun is behind you)
    for dt in westbound_hits:
        assert check_sun_glare(LA_LAT, LA_LON, dt, 90) is None


def test_both_sunrise_and_sunset_glare_exist_and_differ():
    east_hits = _scan_day(90)  # sunrise glare, morning
    west_hits = _scan_day(270)  # sunset glare, evening
    assert east_hits and west_hits
    # Morning and evening events must be hours apart - the historical bug
    # had sunrise and sunset landing at the same azimuth
    gap_h = abs((west_hits[0] - east_hits[-1]).total_seconds()) / 3600
    assert gap_h > 4


def test_no_glare_at_midday():
    # Local solar noon in LA is ~19:53 UTC; the July sun is ~79 degrees up
    noonish = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)
    altitude, _ = solar_position(LA_LAT, LA_LON, noonish)
    assert altitude > 60
    for bearing in (0, 90, 180, 270):
        assert check_sun_glare(LA_LAT, LA_LON, noonish, bearing) is None


def test_no_glare_in_the_middle_of_the_night():
    midnight = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)  # ~2am local
    altitude, _ = solar_position(LA_LAT, LA_LON, midnight)
    assert altitude < -10
    assert check_sun_glare(LA_LAT, LA_LON, midnight, 270) is None
