"""Exit research on hand-made price paths (no network)."""
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from core.black_scholes import bs_price
from core.expiry import ET, MINUTES_PER_YEAR
from research.backtest import RATE, half_spread, summarize
from research.insights import Z_EDGES
from research.exits import (apply_rule, build_states, day_states, hold_vs_sell_table, last_prices, lookup_hold,
                            simulate_exits, time_bucket30, time_bucket30_label)
from tests.test_backtest import spy_day

D = date(2026, 3, 2)


def bars(rows):
    """rows: (type, strike, bar_start 'HH:MM', close)"""
    return pd.DataFrame([{"type": cp, "strike": float(k), "close": c,
                          "timestamp": datetime.combine(D, time.fromisoformat(s), tzinfo=ET)} for cp, k, s, c in rows])


@pytest.mark.parametrize("mins,b", [(390, 13), (360, 12), (31, 2), (30, 1), (5, 1)])
def test_time_bucket30(mins, b):
    assert time_bucket30(mins) == b


def test_bucket_labels():
    assert time_bucket30_label(1) == "last 30 minutes"
    assert time_bucket30_label(3) == "1h 00m–1h 30m left"


def test_last_prices_freshness():
    b = bars([("call", 100, "11:30", 1.0), ("call", 100, "11:50", 1.2), ("put", 100, "11:00", 3.0)])
    px = last_prices(b, datetime.combine(D, time(12, 0), tzinfo=ET))
    assert list(px["close"]) == [1.2]                      # put trade is 1 hour old -> dropped


# ---------------- exit rules ----------------
T = [time(14, 5), time(14, 10), time(14, 15)]


def test_hold_returns_payoff():
    assert apply_rule("hold", 1.0, T, np.array([5.0, 5.0, 5.0]), payoff=0.0) == 0.0


def test_take_profit_triggers_on_sell_value():
    path = np.array([1.2, 2.2, 0.1])
    # +100%: needs sell value >= 2.0 -> 2.2 - half_spread(2.2) = 2.178
    assert apply_rule("tp100", 1.0, T, path, payoff=0.0) == pytest.approx(2.2 - half_spread(2.2))
    # +50%: the first bar sells for 1.2 - 0.012 = 1.188, below 1.5, so it also waits for the 2.2 bar
    assert apply_rule("tp50", 1.0, T, path, payoff=0.0) == pytest.approx(2.2 - half_spread(2.2))


def test_stop_loss_and_combo():
    path = np.array([0.8, 0.45, 3.0])
    assert apply_rule("sl50", 1.0, T, path, payoff=3.0) == pytest.approx(0.45 - half_spread(0.45))
    assert apply_rule("tp100_sl50", 1.0, T, path, payoff=3.0) == pytest.approx(0.45 - half_spread(0.45))
    assert apply_rule("tp100", 1.0, T, path, payoff=3.0) == pytest.approx(3.0 - half_spread(3.0))


def test_sell_by_1530():
    times = [time(15, 25), time(15, 30), time(15, 35)]
    assert apply_rule("by1530", 1.0, times, np.array([0.9, 0.7, 0.5]), payoff=0.0) == pytest.approx(0.7 - half_spread(0.7))
    assert apply_rule("by1530", 1.0, [], np.array([]), payoff=2.0) == 2.0     # nothing traded -> held


def test_simulate_exits_uses_only_prices_after_entry():
    opts = bars([("call", 100, "13:50", 9.9),               # before the 14:00 entry -> ignored
                 ("call", 100, "14:00", 2.5), ("call", 100, "14:30", 0.3)])
    opts["symbol"] = "x"
    trades = pd.DataFrame([{"date": D, "time": "14:00", "type": "call", "strike": 100.0, "entry": 1.0,
                            "payoff": 0.0, "prob_itm": 0.4}])
    ex = simulate_exits(trades, lambda d: opts).set_index("rule")
    assert ex.loc["hold", "proceeds"] == 0.0
    assert ex.loc["tp100", "proceeds"] == pytest.approx(2.5 - half_spread(2.5))   # bar ending 14:05
    assert ex.loc["sl50", "proceeds"] == pytest.approx(0.3 - half_spread(0.3))


# ---------------- hold vs sell states ----------------
def fair_opts(S, sigma, start="14:55"):
    rows = []
    for k in (99, 100, 101):
        for cp in ("call", "put"):
            rows.append((cp, k, start, round(bs_price(S, k, 60 / MINUTES_PER_YEAR, RATE, sigma, cp), 4)))
    return bars(rows)


def test_day_states_payoff_and_z():
    spy = spy_day(D, lambda t: 100.0 if t < time(15, 0) else 101.0)    # closes at 101
    rows = day_states(D, spy, fair_opts(100.0, 0.8), times=[time(15, 0)])
    call101 = next(r for r in rows if r["type"] == "call" and r["strike"] == 101)
    assert call101["payoff"] == 0.0 and call101["z"] > 0
    put99 = next(r for r in rows if r["type"] == "put" and r["strike"] == 99)
    assert put99["z"] > 0                                              # put below spot = out of the money
    call99 = next(r for r in rows if r["type"] == "call" and r["strike"] == 99)
    assert call99["payoff"] == 2.0 and call99["z"] < 0
    assert all(r["sell"] < r["price"] for r in rows)


def test_hold_vs_sell_table_and_lookup():
    spy = spy_day(D, lambda t: 100.0)
    states = build_states(spy, lambda d: fair_opts(100.0, 0.8), [D])
    table = hold_vs_sell_table(states.assign(date=D), lambda df, by: summarize(df, by, n_boot=20))
    assert sum(r["trades"] for r in table) == len(states)
    r = table[0]
    assert "hold_won" in r and -1 <= r["return_per_dollar"]
    # look it up with a z from the middle of that row's bin and a time inside its bucket
    lo, hi = Z_EDGES[r["z_bin"]], Z_EDGES[r["z_bin"] + 1]
    z_mid = hi - 0.01 if np.isinf(lo) else lo + 0.01 if np.isinf(hi) else (lo + hi) / 2
    hit = lookup_hold(table, r["type"], z_mid, r["t_bucket"] * 30 - 1, min_n=1)
    assert hit["trades"] == r["trades"] and "t_label" in hit
    assert lookup_hold(table, r["type"], z_mid, r["t_bucket"] * 30 - 1, min_n=10**6) is None   # too few -> no claim
    assert lookup_hold(table, "call", 0.1, 1500, min_n=1) is None
