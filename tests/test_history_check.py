"""History check on hand-made price histories (no network)."""
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from checker.history_check import (daily_moves, hit_rate, history_check, intraday_moves, verdict,
                                   window_minutes)
from core.expiry import ET


def daily_frame(pct_moves):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(len(pct_moves))]
    return pd.DataFrame({"Open": 100.0, "Close": [100 * (1 + m) for m in pct_moves]}, index=days)


def intraday_day(day, price_at):
    """5-minute bars 9:30..15:55 for one day; price_at(t) gives each bar's close."""
    idx, closes = [], []
    t = datetime.combine(day, time(9, 30), tzinfo=ET)
    while t.time() <= time(15, 55):
        idx.append(t)
        closes.append(price_at(t.time()))
        t += timedelta(minutes=5)
    return pd.DataFrame({"Open": 100.0, "Close": closes}, index=pd.DatetimeIndex(idx))


def trade(option_type="call", strike=101.0, breakeven=101.5, spot=100.0, prob_itm=0.20, minutes_left=390):
    return SimpleNamespace(option_type=option_type, strike=strike, breakeven=breakeven, spot=spot,
                           prob_itm=prob_itm, minutes_left=minutes_left)


# ---------- pieces ----------
def test_window_minutes():
    assert window_minutes(174) == 174
    assert window_minutes(2000) == 390      # next-day expiry -> one full session
    assert window_minutes(0) == 5


def test_daily_moves_sqrt_time_scaling():
    d = daily_frame([0.02, -0.01])
    full = daily_moves(d, 390)
    quarter = daily_moves(d, 97.5)          # a quarter of the day -> half the move (√0.25)
    assert full.tolist() == pytest.approx([0.02, -0.01])
    assert quarter.tolist() == pytest.approx([0.01, -0.005])


def test_hit_rate_direction():
    moves = pd.Series([0.02, 0.005, -0.01, -0.02])
    assert hit_rate(moves, 0.01, "call") == 0.25      # only +2% cleared +1%
    assert hit_rate(moves, -0.01, "put") == 0.5       # -1% and -2% cleared -1%


@pytest.mark.parametrize("market,hist,expected", [
    (0.20, 0.30, "Cheap"), (0.20, 0.22, "Fair"), (0.20, 0.10, "Expensive"), (0.20, 0.25, "Fair"),
])
def test_verdict(market, hist, expected):
    assert verdict(market, hist) == expected


def test_intraday_moves_from_time_of_day():
    # Flat at 100 until 13:00, then 102 for the rest of the day
    day = date(2026, 9, 1)
    bars = intraday_day(day, lambda t: 100.0 if t < time(13, 0) else 102.0)
    # 180 min window -> start 13:00 -> price = close of the 12:55 bar (100) -> close 102 -> +2%
    assert intraday_moves(bars, 180)[day] == pytest.approx(0.02)
    # 60 min window -> start 15:00 -> already 102 -> 0%
    assert intraday_moves(bars, 60)[day] == pytest.approx(0.0)
    # full day -> from the 9:30 open (100)
    assert intraday_moves(bars, 390)[day] == pytest.approx(0.02)


def test_intraday_skips_half_days():
    full = intraday_day(date(2026, 9, 1), lambda t: 100.0)
    half = intraday_day(date(2026, 9, 2), lambda t: 100.0)
    half = half[half.index.time < time(13, 0)]
    assert list(intraday_moves(pd.concat([full, half]), 120).index) == [date(2026, 9, 1)]


# ---------- end to end ----------
def make_intraday(n_days, n_up):
    """n_days full sessions; on n_up of them SPY rallies 100 -> 102 in the last hour."""
    frames = []
    for i in range(n_days):
        up = i < n_up
        frames.append(intraday_day(date(2026, 8, 1) + timedelta(days=i),
                                   lambda t, up=up: 102.0 if up and t >= time(15, 0) else 100.0))
    return pd.concat(frames)


def test_uses_intraday_when_enough_days():
    t = trade(minutes_left=60, prob_itm=0.10)   # needs +1% in the last hour
    h = history_check(t, daily_frame([0.0] * 100), make_intraday(40, 10))
    assert h.basis == "intraday"
    assert h.intraday.n_days == 40
    assert h.hist_prob == pytest.approx(10 / 40)
    assert h.verdict == "Cheap"                 # history 25% vs market 10%
    assert h.needed_itm_pct == pytest.approx(0.01)
    assert h.needed_profit_pct == pytest.approx(0.015)


def test_falls_back_to_daily_with_few_intraday_days():
    t = trade(minutes_left=390, prob_itm=0.20)
    d = daily_frame([0.02] * 2 + [0.0] * 98)   # 2% of days cleared +1%
    h = history_check(t, d, make_intraday(10, 5))
    assert h.basis == "daily"
    assert h.hist_prob == pytest.approx(0.02)
    assert h.verdict == "Expensive"
    assert any("intraday" in n for n in h.notes)


def test_put_profit_rate():
    t = trade("put", strike=99.0, breakeven=98.5, prob_itm=0.05)
    d = daily_frame([-0.02, -0.012, 0.0, 0.01])   # -2% clears both, -1.2% clears strike only
    h = history_check(t, d, None)
    assert h.daily.itm == pytest.approx(0.5)
    assert h.daily.profit == pytest.approx(0.25)


def test_next_day_expiry_note():
    h = history_check(trade(minutes_left=1500), daily_frame([0.0] * 5), None)
    assert h.window_minutes == 390
    assert any("not today" in n for n in h.notes)


def test_no_data_raises():
    with pytest.raises(ValueError):
        history_check(trade(), None, None)
