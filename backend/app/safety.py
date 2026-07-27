"""Safety flags that need no external data source beyond what routing
already provides - steep descents (real elevation profile, already fetched
for the range math) and sun glare (pure geometry, see sun.py).

The other five flags in the product plan (unprotected left turns, wide
unsignaled crossings, ungated rail crossings, fog/wind, school zones) need
real OSM intersection/lane-tag data via Overpass, a separate and more
error-prone subsystem - deliberately not rushed here alongside these two.
"""

from .geo import FT_PER_METER

# 6% sustained grade is the threshold California's own highway signage uses
# for steep-grade warnings (e.g. the Grapevine's descent); "sustained" means
# at least MIN_RUN_MI of it, not a brief dip, to avoid flagging every small
# rolling hill as if it were dangerous.
MIN_GRADE_PCT = 6.0
MIN_RUN_MI = 1.0


def find_steep_descents(coords: list[tuple[float, float]], elevations_m: list[float], cum_mi: list[float]) -> list[dict]:
    """coords are (lon, lat). Walks the real elevation profile with a
    sliding ~1mi window, flags contiguous windows where the descent grade
    exceeds MIN_GRADE_PCT, and merges adjacent flagged windows into single
    reported segments rather than one entry per window."""
    n = len(cum_mi)
    if n < 2:
        return []

    flagged = [False] * n
    j = 0
    for i in range(n):
        target = cum_mi[i] + MIN_RUN_MI
        while j < n - 1 and cum_mi[j] < target:
            j += 1
        run_mi = cum_mi[j] - cum_mi[i]
        if run_mi < MIN_RUN_MI * 0.9:  # too close to the route's end for a full window
            continue
        drop_ft = (elevations_m[i] - elevations_m[j]) * FT_PER_METER
        grade_pct = (drop_ft / (run_mi * 5280)) * 100 if run_mi > 0 else 0
        if grade_pct >= MIN_GRADE_PCT:
            for k in range(i, j + 1):
                flagged[k] = True

    segments = []
    start = None
    for i in range(n):
        if flagged[i] and start is None:
            start = i
        elif not flagged[i] and start is not None:
            segments.append((start, i - 1))
            start = None
    if start is not None:
        segments.append((start, n - 1))

    out = []
    for start_i, end_i in segments:
        length_mi = cum_mi[end_i] - cum_mi[start_i]
        drop_ft = (elevations_m[start_i] - elevations_m[end_i]) * FT_PER_METER
        if length_mi <= 0 or drop_ft <= 0:
            continue
        avg_grade_pct = (drop_ft / (length_mi * 5280)) * 100
        mid_i = (start_i + end_i) // 2
        out.append({
            "type": "steep_descent",
            "description": f"~{round(avg_grade_pct)}% grade descent over {round(length_mi, 1)} mi - real brake-fade territory.",
            "length_mi": round(length_mi, 1),
            "grade_pct": round(avg_grade_pct, 1),
            "lat": coords[mid_i][1],
            "lon": coords[mid_i][0],
        })
    return out


# Sustained winding stretches - canyon roads, mountain passes. Pure
# geometry: bucket the route into half-mile bins of accumulated bearing
# change; consecutive bins over the threshold merge into one section.
TWISTY_BIN_MI = 0.5
TWISTY_DEG_PER_MI = 220.0
TWISTY_MIN_LEN_MI = 1.5
MIN_BEND_DEG = 20.0


def find_twisty_sections(coords: list[tuple[float, float]], cum: list[float]) -> list[dict]:
    from .geo import bearing_deg

    if len(coords) < 3:
        return []
    n_bins = int(cum[-1] / TWISTY_BIN_MI) + 1
    bin_deg = [0.0] * n_bins
    bin_bends = [0] * n_bins
    prev_bearing = None
    for i in range(1, len(coords)):
        lon1, lat1 = coords[i - 1]
        lon2, lat2 = coords[i]
        b = bearing_deg(lat1, lon1, lat2, lon2)
        if prev_bearing is not None:
            turn = abs(((b - prev_bearing + 540) % 360) - 180)
            idx = min(int(cum[i] / TWISTY_BIN_MI), n_bins - 1)
            bin_deg[idx] += turn
            if turn >= MIN_BEND_DEG:
                bin_bends[idx] += 1
        prev_bearing = b

    flags = []
    i = 0
    while i < n_bins:
        if bin_deg[i] / TWISTY_BIN_MI >= TWISTY_DEG_PER_MI:
            j = i
            while j + 1 < n_bins and bin_deg[j + 1] / TWISTY_BIN_MI >= TWISTY_DEG_PER_MI:
                j += 1
            start_mi = i * TWISTY_BIN_MI
            end_mi = (j + 1) * TWISTY_BIN_MI
            if end_mi - start_mi >= TWISTY_MIN_LEN_MI:
                bends = sum(bin_bends[i : j + 1])
                flags.append({
                    "kind": "twisty",
                    "description": (
                        f"Twisty road from mile {start_mi:.0f} to mile {end_mi:.0f} - "
                        f"about {bends} real bends. Take it unhurried."
                    ),
                    "lat": None,
                    "lon": None,
                    "length_mi": round(end_mi - start_mi, 1),
                })
            i = j + 1
        else:
            i += 1
    # a mid-section pin helps on the map
    for f in flags:
        pass
    return flags[:4]
