"""What-if grid (no network)."""
from datetime import date, datetime, time

import pytest

from checker.scenarios import clock_times, price_levels, scenario_grid
from core.black_scholes import bs_price
from core.expiry import ET

D = date(2026, 10, 15)
at = lambda h, m, d=D: datetime.combine(d, time(h, m), tzinfo=ET)


def test_price_levels_round_and_centered():
    levels = price_levels(770.36, 1.5)          # ±3 range -> $0.5 steps
    assert levels == sorted(levels, reverse=True)
    assert len(levels) <= 17 and 770.5 in levels and 770.0 in levels
    assert levels[0] >= 773 and levels[-1] <= 767.5
    assert all(round(p * 2) == p * 2 for p in levels)


def test_price_levels_wide_market():
    assert len(price_levels(770, 20)) <= 17


def test_clock_times_mid_session():
    ts = clock_times(at(13, 7), D)
    assert ts[0] == at(13, 7) and ts[-1] == at(16, 0)
    assert [t.strftime("%H:%M") for t in ts[1:-1]] == ["13:30", "14:00", "14:30", "15:00", "15:30"]


def test_clock_times_skips_a_column_too_close_to_now():
    ts = clock_times(at(13, 25), D)
    assert ts[1] == at(14, 0)                   # 13:30 is only 5 minutes away


def test_clock_times_before_open_uses_expiry_day():
    ts = clock_times(at(20, 0, date(2026, 10, 14)), D)
    assert ts[0] == at(9, 30) and ts[-1] == at(16, 0)
    assert all((b - a).seconds == 3600 for a, b in zip(ts[1:-2], ts[2:-1]))


def test_grid_values():
    g = scenario_grid(770.0, 772.0, "call", 0.18, 0.04, 0.50, at(13, 0), D)
    assert len(g["pnl"]) == len(g["prices"]) and len(g["pnl"][0]) == len(g["times"])
    assert g["times"][-1] == "4:00" and g["first_is_now"]
    i = g["prices"].index(775.0)
    assert g["pnl"][i][-1] == pytest.approx(3.0 - 0.50)        # at the close: intrinsic - premium
    j = g["prices"].index(770.0)
    assert g["pnl"][j][0] == pytest.approx(bs_price(770, 772, 180 / 525_600, 0.04, 0.18, "call") - 0.50)
    assert g["pnl"][j][-1] == pytest.approx(-0.50)             # OTM at the close: lose the premium
