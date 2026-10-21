"""Strategy Lab on hand-made days (no network): payoffs, costs, stops, signals, events."""
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from core.expiry import ET
from data.events import event_type
from research import strategies as st
from research.backtest import half_spread
from tests.test_backtest import spy_day

D = date(2026, 3, 2)


def bars(rows):
    """rows: (type, strike, 'HH:MM' bar start, close)"""
    return pd.DataFrame([{"type": cp, "strike": float(k), "close": c,
                          "timestamp": datetime.combine(D, time.fromisoformat(s), tzinfo=ET)} for cp, k, s, c in rows])


def flat_book(prices: dict, start="09:50"):
    """Every contract trades once at `start` at the given price (stays fresh for MAX_AGE)."""
    return st.DayBook(D, bars([(cp, k, start, p) for (cp, k), p in prices.items()]))


def test_daybook_freshness_and_path():
    book = st.DayBook(D, bars([("call", 100, "09:50", 1.0), ("call", 100, "11:00", 2.0)]))
    assert book.price("call", 100, time(10, 0)) == 1.0           # 5 minutes old
    assert np.isnan(book.price("call", 100, time(10, 30)))       # 40 minutes old: stale
    assert book.price("call", 100, time(11, 5)) == 2.0
    assert book.path("call", 100, time(10, 0)).iloc[-1] == 2.0   # forward-filled to the close


def test_short_straddle_pnl():
    book = flat_book({("call", 100): 1.0, ("put", 100): 1.0})
    r = st.short_straddle(book, 100.0, 101.0, time(10, 0))
    assert r["gross_pnl"] == pytest.approx(2.0 - 1.0)            # collected 2, paid out 1 at the close
    assert r["cost"] == pytest.approx(2 * half_spread(1.0))
    assert r["credit"] == pytest.approx(2.0 - r["cost"])


CONDOR = {("call", 100): 1.0, ("put", 100): 1.0,
          ("call", 102): 0.30, ("call", 104): 0.05, ("put", 98): 0.30, ("put", 96): 0.05}


def test_short_condor_strikes_and_payoff():
    book = flat_book(CONDOR)
    r = st.short_condor(book, 100.0, 103.0, time(10, 0))       # sd = 2 × 1.2533 -> shorts ≥ 101.88 / ≤ 98.12
    assert r["strikes"] == "96/98 – 102/104"
    assert r["gross_pnl"] == pytest.approx(0.5 - 1.0)           # credit 0.50, call side $1 in the money
    assert r["max_loss"] == pytest.approx(2.0 - r["credit"])
    pinned = st.short_condor(book, 100.0, 100.0, time(10, 0))
    assert pinned["gross_pnl"] == pytest.approx(0.5)             # everything expires worthless
    blown = st.short_condor(book, 100.0, 110.0, time(10, 0))
    assert blown["gross_pnl"] == pytest.approx(0.5 - 2.0)        # loss capped by the $2 wing


def test_breakeven_condor_stop(monkeypatch):
    # call side spikes to 0.55 at 13:00 (>= 0.50 credit) then expires worthless at 101
    rows = [(cp, k, "09:50", p) for (cp, k), p in CONDOR.items()] + [("call", 102, "12:55", 0.60)]
    book = st.DayBook(D, bars(rows))
    monkeypatch.setattr(st, "pick_by_delta", lambda b, cp, S, t, target: 102.0 if cp == "call" else 98.0)
    held, stopped = st.breakeven_condor(book, 100.0, 101.0, time(10, 0), width=2.0)
    assert held["variant"] == "held to 4 PM" and held["gross_pnl"] == pytest.approx(0.5)
    assert stopped["stops"] == 1
    assert stopped["gross_pnl"] == pytest.approx(0.5 - (0.60 - 0.05))   # bought the call side back at 0.55
    assert stopped["cost"] > held["cost"]                                 # paid spread again to exit


def test_momentum_option_direction():
    book = flat_book({("call", 100): 0.40, ("put", 100): 0.45}, start="15:20")
    up = st.momentum_option(book, 100.0, 101.0, r_first=+0.003)
    down = st.momentum_option(book, 100.0, 101.0, r_first=-0.003)
    assert up["direction"] == "call" and up["gross_pnl"] == pytest.approx(1.0 - 0.40)
    assert down["direction"] == "put" and down["gross_pnl"] == pytest.approx(-0.45)


def test_momentum_table_signal_and_no_lookahead():
    days = [D + timedelta(days=i) for i in range(3)]
    spy = pd.concat([
        spy_day(days[0], lambda t: 100.0),
        spy_day(days[1], lambda t: 101.0 if t < time(15, 30) else 102.0),       # up at 10:00, rallies late
        spy_day(days[2], lambda t: 100.0 if t < time(15, 30) else 99.0),        # down at 10:00, falls late
    ])
    m = st.momentum_table(spy).set_index("date")
    assert m.loc[days[1], "r_first"] == pytest.approx(0.01)
    assert m.loc[days[1], "signal_ret"] > 0 and m.loc[days[2], "signal_ret"] > 0
    assert m["strong"].eq(False).all()                                # < 40 days of history: never "strong"


def test_with_costs_and_verdict():
    df = pd.DataFrame({"date": [D, D], "gross_pnl": [0.10, -0.02], "cost": [0.02, 0.02], "basis": [1.0, 1.0]})
    assert st.with_costs(df, 0)["pnl"].tolist() == [0.10, -0.02]
    assert st.with_costs(df, 2)["pnl"].tolist() == pytest.approx([0.06, -0.06])
    assert st.verdict({"ci_low": 0.01, "return_per_dollar": 0.05, "gross_return": 0.08}) == "Worked after costs"
    assert st.verdict({"ci_low": -0.02, "return_per_dollar": -0.01, "gross_return": 0.03}) == "Worked only before trading costs"
    assert st.verdict({"ci_low": -0.2, "return_per_dollar": -0.1, "gross_return": -0.05}) == "Did not work"
    assert st.verdict({"ci_low": 0.5, "return_per_dollar": 0.3, "gross_return": 0.3, "days": 1}) == "Too few days to tell"


def test_event_calendar():
    assert event_type(date(2025, 9, 17)) == "FOMC"
    assert event_type(date(2024, 6, 12)) == "FOMC+CPI"
    assert event_type(date(2025, 10, 15)) is None                    # October 2025 CPI was never published
    assert event_type(date(2025, 8, 22)) is None                     # notation vote, not a meeting
