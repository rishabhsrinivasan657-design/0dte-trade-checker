"""Chain cleaning and expected move, on a small hand-made chain (no network)."""
from datetime import date
from math import exp, pi, sqrt

import pandas as pd
import pytest

from data.chain import atm_strike, clean_chain, expected_move, parity_spot
from data.occ import parse_occ

EXP = date(2026, 10, 14)


def make_chain(rows):
    return pd.DataFrame(
        [{"symbol": f"{t}{k}", "expiry": EXP, "type": t, "strike": k, "bid": b, "ask": a,
          "quote_time": None, "vendor_iv": None} for t, k, b, a in rows]
    )


RAW = make_chain([
    ("call", 777.0, 2.70, 2.75),
    ("call", 778.0, 2.10, 2.16),
    ("call", 779.0, 1.60, 1.66),
    ("call", 790.0, 0.00, 0.02),   # zero bid -> drop
    ("call", 791.0, 0.01, 0.03),   # 100% wide but only 2 cents -> keep
    ("put",  777.0, 1.45, 1.50),
    ("put",  778.0, 1.90, 1.94),
    ("put",  779.0, 1.00, 3.00),   # $2 wide on a $2 option -> drop
    ("put",  780.0, 3.00, 2.90),   # crossed (ask < bid) -> drop
])


def test_clean_adds_mid_and_spread():
    c = clean_chain(RAW)
    row = c[(c["type"] == "call") & (c["strike"] == 778.0)].iloc[0]
    assert row["mid"] == pytest.approx(2.13)
    assert row["spread"] == pytest.approx(0.06)


def test_clean_drops_bad_quotes():
    kept = set(zip(clean_chain(RAW)["type"], clean_chain(RAW)["strike"]))
    assert ("call", 790.0) not in kept
    assert ("put", 779.0) not in kept
    assert ("put", 780.0) not in kept
    assert ("call", 791.0) in kept
    assert len(kept) == 6


def test_atm_needs_both_legs():
    c = clean_chain(RAW)
    assert atm_strike(c, 778.2) == 778.0
    # 779 is closest to 778.9, but its put was dropped -> fall back to 778
    assert atm_strike(c, 778.9) == 778.0


def test_atm_tie_picks_lower_strike():
    assert atm_strike(clean_chain(RAW), 777.5) == 777.0


def test_expected_move():
    em = expected_move(clean_chain(RAW), 778.0)
    assert em["straddle"] == pytest.approx(2.13 + 1.92)
    assert em["low"] == pytest.approx(778.0 - 4.05)
    assert em["high"] == pytest.approx(778.0 + 4.05)
    assert em["move_pct"] == pytest.approx(4.05 / 778.0)
    assert em["one_sd_move"] == pytest.approx(4.05 * sqrt(pi / 2))


def test_parse_occ():
    assert parse_occ("SPY261014P00778500") == {"expiry": EXP, "type": "put", "strike": 778.5}
    with pytest.raises(ValueError):
        parse_occ("SPY")


def test_parity_spot_recovers_price_the_quotes_imply():
    # Quotes built from SPY = 769.42 (stale), while the "last price" says 770.22
    T, r, S = 200 / 525_600, 0.04, 769.42
    rows = []
    for k, put in [(769.0, 1.06), (770.0, 1.55), (771.0, 2.19)]:
        call = put + S - k * exp(-r * T)          # put-call parity
        rows += [("call", k, call - 0.005, call + 0.005), ("put", k, put - 0.005, put + 0.005)]
    assert parity_spot(clean_chain(make_chain(rows)), 770.22, T, r) == pytest.approx(S, abs=1e-9)


def test_parity_spot_without_pairs_falls_back_to_quoted():
    only_calls = make_chain([("call", 770.0, 1.0, 1.02)])
    assert parity_spot(clean_chain(only_calls), 770.22, 0.0004, 0.04) == 770.22
