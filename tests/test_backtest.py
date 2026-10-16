"""Backtest on hand-made days (no network): exact answers we can check by hand."""
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from core.black_scholes import bs_price
from core.expiry import ET, MINUTES_PER_YEAR
from research.backtest import (RATE, BacktestConfig, calibration, daily_open_close, half_spread,
                               minutes_left, moves_to_close, option_price_at, price_at, run, run_day,
                               summarize)


def spy_day(day, price_fn, last=time(15, 55)):
    idx, t = [], datetime.combine(day, time(9, 30), tzinfo=ET)
    while t.time() <= last:
        idx.append(t)
        t += timedelta(minutes=5)
    closes = [price_fn(i.time()) for i in idx]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1}, index=pd.DatetimeIndex(idx))


def option_bars(day, rows):
    """rows: (type, strike, bar_start 'HH:MM', close)"""
    return pd.DataFrame([{"symbol": f"{cp}{k}", "type": cp, "strike": float(k), "close": c,
                          "timestamp": datetime.combine(day, time.fromisoformat(s), tzinfo=ET)} for cp, k, s, c in rows])


D = date(2026, 3, 2)


def test_minutes_left():
    assert minutes_left(time(10, 0)) == 360
    assert minutes_left(time(15, 0)) == 60


def test_price_at_uses_bar_ending_at_t():
    bars = spy_day(D, lambda t: 100.0 if t < time(12, 0) else 101.0)
    assert price_at(bars, time(12, 0)) == 100.0     # bar 11:55-12:00 closed at 100
    assert price_at(bars, time(12, 5)) == 101.0


def test_moves_to_close_and_daily():
    bars = spy_day(D, lambda t: 100.0 if t < time(15, 0) else 102.0)
    m = moves_to_close(bars)
    assert m.loc[D, 60] == pytest.approx(0.02)       # 15:00 -> close
    assert m.loc[D, 360] == pytest.approx(0.02)
    d = daily_open_close(bars)
    assert d.loc[D, "Open"] == 100.0 and d.loc[D, "Close"] == 102.0


def test_half_day_excluded_from_history():
    bars = spy_day(D, lambda t: 100.0, last=time(12, 55))
    assert moves_to_close(bars).empty
    assert daily_open_close(bars).empty


def test_option_price_staleness():
    opts = option_bars(D, [("call", 100, "11:30", 1.0), ("call", 100, "11:50", 1.2)])
    t_end = datetime.combine(D, time(12, 0), tzinfo=ET)
    assert option_price_at(opts, "call", 100, t_end) == 1.2
    old = option_bars(D, [("call", 100, "11:30", 1.0)])
    assert np.isnan(option_price_at(old, "call", 100, t_end))     # 30 min old -> unusable
    future = option_bars(D, [("call", 100, "12:00", 9.0)])
    assert np.isnan(option_price_at(future, "call", 100, t_end))  # bar ending 12:05 is the future


def fair_price(S, K, mins, sigma, cp):
    return round(bs_price(S, K, mins / MINUTES_PER_YEAR, RATE, sigma, cp), 4)


def test_run_day_pnl_and_signals():
    # SPY 100 all day until 15:00, then jumps to 102 at the close
    bars = spy_day(D, lambda t: 100.0 if t < time(15, 0) else 102.0)
    px = fair_price(100, 101, 60, 0.80, "call")       # high vol so a $1-OTM option has a real price
    opts = option_bars(D, [("call", 101, "14:55", px)])
    past_days = [D - timedelta(days=i) for i in range(40, 0, -1)]
    past_moves = pd.DataFrame({60: [0.0] * 30 + [0.02] * 10}, index=past_days)   # 25% of afternoons rallied 2%
    past_daily = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=past_days)
    cfg = BacktestConfig(times=(time(15, 0),))
    rows = run_day(D, bars, opts, past_moves, past_daily, cfg)
    assert len(rows) == 1
    r = rows[0]
    assert r["iv"] == pytest.approx(0.80, rel=1e-3)
    assert r["entry"] == pytest.approx(px + half_spread(px))
    assert r["payoff"] == pytest.approx(1.0) and r["itm"]
    assert r["pnl"] == pytest.approx(1.0 - r["entry"])
    assert r["hist_basis"] == "intraday" and r["hist_prob"] == pytest.approx(0.25)
    assert r["moneyness"] == pytest.approx(1.0)


def test_no_lookahead_in_run():
    # Day 2's own rally must not appear in its history
    d1, d2 = D, D + timedelta(days=1)
    spy = pd.concat([spy_day(d1, lambda t: 100.0), spy_day(d2, lambda t: 100.0 if t < time(15, 0) else 102.0)])
    px = fair_price(100, 101, 60, 0.8, "call")
    opts = {d1: option_bars(d1, [("call", 101, "14:55", px)]), d2: option_bars(d2, [("call", 101, "14:55", px)])}
    trades = run(spy, lambda d: opts[d], [d1, d2], BacktestConfig(times=(time(15, 0),)))
    day2 = trades[trades["date"] == d2].iloc[0]
    assert day2["hist_daily"] == 0.0          # only day 1 (flat) is in its history
    day1 = trades[trades["date"] == d1].iloc[0]
    assert np.isnan(day1["hist_daily"])       # no history before day 1


def test_spread_multiplier():
    bars = spy_day(D, lambda t: 100.0)
    px = fair_price(100, 100, 60, 0.2, "call")
    opts = option_bars(D, [("call", 100, "14:55", px)])
    no_cost = run_day(D, bars, opts, pd.DataFrame(), pd.DataFrame({"Open": [], "Close": []}),
                      BacktestConfig(times=(time(15, 0),), spread_multiplier=0))[0]
    assert no_cost["entry"] == pytest.approx(px)


def make_trades():
    return pd.DataFrame({
        "date": [D, D, D + timedelta(1), D + timedelta(1)],
        "verdict": ["Cheap", "Expensive", "Cheap", "Expensive"],
        "entry": [1.0, 1.0, 1.0, 1.0], "pnl": [1.0, -1.0, 0.0, -1.0],
        "itm": [True, False, True, False], "prob_itm": [0.6, 0.3, 0.55, 0.2],
    })


def test_summarize():
    s = summarize(make_trades(), "verdict").set_index("verdict")
    assert s.loc["Cheap", "return_per_dollar"] == pytest.approx(0.5)
    assert s.loc["Expensive", "return_per_dollar"] == pytest.approx(-1.0)
    assert s.loc["Cheap", "win_rate"] == pytest.approx(0.5)
    assert s.loc["Cheap", "ci_low"] <= 0.5 <= s.loc["Cheap", "ci_high"]
    assert s.loc["Cheap", "ex_best_day"] == pytest.approx(0.0)        # day 1 (+$1) removed -> only day 2 (0)


def test_calibration():
    c = calibration(make_trades(), "prob_itm", bins=(0, 0.5, 1))
    assert list(c["n"]) == [2, 2]
    assert list(c["actual"]) == [0.0, 1.0]
