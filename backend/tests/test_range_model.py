from app import range_model as rm


def test_reserve_floor_picks_the_binding_rule():
    # 205mi car: 30mi is ~14.6%, so the 15% rule binds
    assert rm.reserve_floor_pct(205, 15, 30) == 15
    # 100mi car: 30mi is 30%, which binds over 15%
    assert rm.reserve_floor_pct(100, 15, 30) == 30


def test_estimate_arrival_flat_baseline():
    # Half the battery for half the (city) range, no adjustments
    est = rm.estimate_arrival(200, 100, 100, highway_fraction=0.0, ascent_ft=0, descent_ft=0)
    assert abs(est.arrival_pct - 50) < 1e-9
    assert est.feasible


def test_highway_driving_costs_more():
    city = rm.estimate_arrival(200, 100, 100, 0.0, 0, 0)
    highway = rm.estimate_arrival(200, 100, 100, 1.0, 0, 0)
    assert highway.arrival_pct < city.arrival_pct
    # +15% consumption on a 50%-of-battery trip = 7.5 points more used
    assert abs((city.arrival_pct - highway.arrival_pct) - 7.5) < 1e-6


def test_round_trip_over_a_hill_costs_net_energy():
    # Climb and fully descend the same 1000ft: regen recovers only 60%,
    # so the trip must cost more than the flat equivalent.
    flat = rm.estimate_arrival(200, 100, 100, 0.0, 0, 0)
    hill = rm.estimate_arrival(200, 100, 100, 0.0, ascent_ft=1000, descent_ft=1000)
    assert hill.arrival_pct < flat.arrival_pct


def test_infeasible_when_arrival_below_floor():
    est = rm.estimate_arrival(200, 40, 100, 0.0, 0, 0)  # arrives at -10%
    assert not est.feasible
    assert est.leeway_mi < 0


def test_weather_adjustment_cold_hot_wind_and_clamps():
    assert rm.weather_adjustment_fraction(60, 0) == 0
    assert rm.weather_adjustment_fraction(70, 0) == 0  # mild band
    # Freezing: 28 degrees below reference at 0.6%/degF
    assert abs(rm.weather_adjustment_fraction(32, 0) - 0.168) < 1e-9
    # Hot: 10 over the hot reference at 0.3%/degF
    assert abs(rm.weather_adjustment_fraction(95, 0) - 0.03) < 1e-9
    # Headwind adds, tailwind subtracts
    assert rm.weather_adjustment_fraction(70, 10) > 0
    assert rm.weather_adjustment_fraction(70, -10) < 0
    # Clamped on both sides: no free energy, no infinite penalty
    assert rm.weather_adjustment_fraction(-40, 40) == 0.5
    assert rm.weather_adjustment_fraction(70, -100) == -0.25


def test_load_adjustment():
    assert rm.load_adjustment_fraction(0, 0) == 0
    # 4 people + 4 bags = 900lb -> 7.2%
    assert abs(rm.load_adjustment_fraction(4, 4) - 0.072) < 1e-9


def test_charge_time_estimate():
    # 205mi * 250Wh/mi = 51.25kWh pack; 20->80% = 30.75kWh at 250kW*0.7
    minutes = rm.estimate_charge_time_min(205, 20, 80, 250)
    assert 10 < minutes < 11
    # Already at/above target -> zero time, not negative
    assert rm.estimate_charge_time_min(205, 90, 80, 250) == 0
    assert rm.estimate_charge_time_min(205, 20, 80, 0) is None


def test_front_loaded_climb_hits_the_floor_early():
    """The Grapevine case: a big climb early in the route must pull the
    reserve-floor point closer than the flat math admits, even when the
    route's NET elevation change is ~zero."""
    n = 101
    cum = [i * 1.0 for i in range(n)]  # 100 miles, a vertex per mile
    # 5000ft up over the first 20 miles, back down the next 20, flat after
    elevations_m = []
    for mi in cum:
        if mi <= 20:
            ft = mi / 20 * 5000
        elif mi <= 40:
            ft = (40 - mi) / 20 * 5000
        else:
            ft = 0
        elevations_m.append(ft / 3.28084)

    flat_estimate = rm.distance_to_floor_mi(205, 30, highway_fraction=0.5)
    aware = rm.distance_to_floor_mi_elevation_aware(cum, elevations_m, 205, 30, 0.5)
    assert aware < flat_estimate

    # And the cumulative curve is monotone through the climb but dips
    # (recovers charge) on the descent's regen credit
    curve = rm.cumulative_pct_used(cum, elevations_m, 205, 0.5)
    assert curve[20] > curve[10] > 0
    assert curve[40] < curve[20]  # descent recovered some, not all
    assert curve[40] > curve[20] - (curve[20] - curve[0]) * 0.7  # regen is partial


def test_pct_used_interpolation_matches_curve_endpoints():
    cum = [0.0, 10.0, 20.0]
    elev = [100.0, 100.0, 100.0]
    curve = rm.cumulative_pct_used(cum, elev, 200, 0.0)
    assert rm.pct_used_at_distance(cum, curve, 0) == 0
    assert rm.pct_used_at_distance(cum, curve, 20) == curve[-1]
    mid = rm.pct_used_at_distance(cum, curve, 15)
    assert curve[1] < mid < curve[2]


def test_describe_weather_wording():
    assert rm.describe_weather(74, 5) == "74°F, light headwind"
    assert rm.describe_weather(50, -25) == "50°F, strong tailwind"
    assert rm.describe_weather(70, 0) == "70°F, calm wind"
