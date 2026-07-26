"""Sun-glare safety flag - pure geometry, no external data source at all.

Standard low-precision solar position formulas (declination from day-of-year,
true solar time from longitude only - no timezone/DST lookup needed, no
equation-of-time correction). Good to a few degrees, which is what a
courtesy "the sun may be in your eyes" warning needs - not an ephemeris.
Skipping the equation-of-time correction means "true solar time" can be off
from clock time by up to ~15 minutes at points in the year, which shifts the
computed sun position by a few degrees - acceptable for this purpose, and
documented rather than silently assumed precise.
"""

import math


def solar_position(lat: float, lon: float, utc_dt) -> tuple[float, float]:
    """Returns (altitude_deg, azimuth_deg). Azimuth measured clockwise from
    true north (0=N, 90=E, 180=S, 270=W), matching geo.bearing_deg's convention
    so the two are directly comparable."""
    day_of_year = utc_dt.timetuple().tm_yday
    declination = 23.45 * math.sin(math.radians(360 / 365 * (day_of_year - 81)))

    utc_hour = utc_dt.hour + utc_dt.minute / 60 + utc_dt.second / 3600
    # Wrapped to [0, 24) before computing hour_angle - without this, a
    # negative or >24 solar_time_hours (very common: lon/15 alone can shift
    # it by several hours) produces an hour_angle outside [-180, 180]. The
    # altitude formula still works then (cos() is periodic), which masked
    # this for a while, but the morning/afternoon sign check for azimuth
    # below does NOT handle unwrapped angles correctly - confirmed by a
    # real test that had sunrise and sunset both landing at ~the same
    # azimuth, which is astronomically impossible.
    solar_time_hours = (utc_hour + lon / 15) % 24
    hour_angle = (solar_time_hours - 12) * 15

    lat_r, decl_r, ha_r = math.radians(lat), math.radians(declination), math.radians(hour_angle)

    sin_alt = math.sin(lat_r) * math.sin(decl_r) + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r)
    altitude = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))

    cos_az = (math.sin(decl_r) - math.sin(lat_r) * sin_alt) / (math.cos(lat_r) * math.cos(math.radians(altitude)) + 1e-9)
    az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
    azimuth = (360 - az) if hour_angle > 0 else az

    return altitude, azimuth


# A "low sun" glare window: below ~15 deg altitude is the commonly cited band
# where a low sun is genuinely blinding through a windshield; below about -2
# it has set enough that it's dusk/dark rather than glare.
GLARE_MAX_ALTITUDE = 15.0
GLARE_MIN_ALTITUDE = -2.0
GLARE_MAX_ANGLE_OFF_HEADING = 30.0  # sun roughly ahead of you, not off to the side


def angular_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def check_sun_glare(lat: float, lon: float, utc_dt, travel_bearing_deg: float) -> dict | None:
    altitude, azimuth = solar_position(lat, lon, utc_dt)
    if not (GLARE_MIN_ALTITUDE <= altitude <= GLARE_MAX_ALTITUDE):
        return None
    diff = angular_diff(azimuth, travel_bearing_deg)
    if diff > GLARE_MAX_ANGLE_OFF_HEADING:
        return None
    return {
        "type": "sun_glare",
        "description": "Low sun roughly ahead of you around this time - may cause glare.",
        "sun_altitude_deg": round(altitude, 1),
        "angle_off_heading_deg": round(diff, 1),
    }
