"""Similar-trades lookup and realistic-chance curves (no network)."""
from datetime import date, timedelta
from math import sqrt

import numpy as np
import pandas as pd
import pytest

from research import backtest as bt
from research.insights import (MIN_TRADES, Z_EDGES, add_buckets, apply_curve, brier, evaluate_curve, evidence,
                               fit_curve, lookup_similar, similar_table, time_bucket, z_bin, z_distance, z_label)


def test_z_distance_is_in_typical_moves():
    # SPY 770, 18% vol, 60 min left: one typical move ≈ 770 * 0.18 * sqrt(60/525600) ≈ $1.48
    one = 770 * 0.18 * sqrt(60 / 525_600)
    assert z_distance(one, 770, 0.18, 60) == pytest.approx(1.0)
    assert z_distance(-one / 2, 770, 0.18, 60) == pytest.approx(-0.5)


def test_same_z_across_price_levels():
    # $1.25 OTM at SPY 480 ≈ $2.00 OTM at SPY 770 (same % distance, same vol, same time)
    assert z_distance(480 * 0.0026, 480, 0.2, 120) == pytest.approx(z_distance(770 * 0.0026, 770, 0.2, 120))


def test_bins_and_labels():
    assert Z_EDGES[z_bin(0.3)] == 0.25 and Z_EDGES[z_bin(-0.3)] == -0.5
    assert z_label(z_bin(0.3)) == "0.25–0.5 typical moves out of the money"
    assert z_label(z_bin(-0.3)) == "0.25–0.5 typical moves in the money"
    assert z_label(z_bin(5)) == "more than 1.5 typical moves out of the money"
    assert z_label(z_bin(-5)) == "more than 1 typical move in the money"


@pytest.mark.parametrize("mins,bucket", [(360, "10–11 AM"), (300, "10–11 AM"), (240, "12–1 PM"),
                                         (180, "12–1 PM"), (120, "2–4 PM"), (20, "2–4 PM"), (1500, None)])
def test_time_buckets(mins, bucket):
    assert time_bucket(mins) == bucket


def fake_trades(n_days=30, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_days):
        d = date(2025, 1, 1) + timedelta(days=i)
        for t, mins in [("10:00", 360), ("14:00", 120)]:
            for k in range(-3, 4):
                p = float(np.clip(0.5 - 0.15 * k, 0.02, 0.98))
                itm = rng.random() < p * 0.8           # market overstates every probability by 25%
                entry = 1.0
                rows.append({"date": d, "time": t, "minutes_left": mins, "type": "call", "strike": 100 + k,
                             "spot": 100.0, "moneyness": float(k), "entry": entry, "prob_itm": p, "itm": itm,
                             "pnl": (1.5 if itm else 0.0) - entry})
    return pd.DataFrame(rows)


def test_add_buckets_and_similar_table():
    tr = fake_trades()
    atm = pd.Series(0.2, index=pd.MultiIndex.from_frame(tr[["date", "time"]].drop_duplicates()))
    t = add_buckets(tr, atm)
    assert set(t["time_bucket"]) == {"10–11 AM", "2–4 PM"}
    assert t.loc[t["moneyness"] == 0, "z"].eq(0).all()
    table = similar_table(t, lambda df, by: bt.summarize(df, by, n_boot=50))
    assert sum(r["trades"] for r in table) == len(t)
    assert {"type", "z_bin", "time_bucket", "z_label", "return_per_dollar"} <= set(table[0])


def test_lookup_similar():
    row = {"type": "call", "z_bin": z_bin(0.3), "time_bucket": "2–4 PM", "trades": 500, "z_label": "old wording"}
    small = {**row, "type": "put", "trades": MIN_TRADES - 1}
    table = [row, small]
    hit = lookup_similar(table, "call", 0.3, 60)
    assert hit["trades"] == 500
    assert hit["z_label"] == z_label(z_bin(0.3))                     # label comes from code
    assert lookup_similar(table, "call", 0.3, 300) is None           # different time of day
    assert lookup_similar(table, "call", 0.9, 60) is None            # different distance
    assert lookup_similar(table, "put", 0.3, 60) is None             # too few trades -> no claim
    assert lookup_similar(table, "call", 0.3, 1500) is None          # next-day expiry


def test_realistic_curve_fixes_overconfidence():
    tr = fake_trades(200)
    curve = fit_curve(tr["prob_itm"].to_numpy(), tr["itm"].to_numpy())
    assert apply_curve(0.5, curve) == pytest.approx(0.4, abs=0.05)    # learned the 0.8x shrink
    ev = evaluate_curve(tr, "prob_itm", "itm")
    assert ev["brier_realistic"] < ev["brier_market"]
    assert 0 <= apply_curve(-1, curve) and apply_curve(2, curve) <= 1     # clipped


def test_brier():
    assert brier(np.array([1.0, 0.0]), np.array([True, False])) == 0
    assert brier(np.array([0.5]), np.array([True])) == 0.25


def test_evidence_bundle():
    insights = {"similar": [], "curves": {"itm": {"x": [0, 1], "y": [0, 0.9]}, "profit": {"x": [0, 1], "y": [0, 0.8]}},
                "options_range": ["2024-02-01", "2026-10-14"],
                "model_verdicts": [{"model_verdict": "Expensive", "return_per_dollar": -0.18}],
                "all_trades": [{"return_per_dollar": -0.07}]}
    e = evidence(insights, "call", 2.0, 770, 0.18, 60, 0.5, 0.4)
    assert e["realistic_itm"] == pytest.approx(0.45)
    assert e["realistic_profit"] == pytest.approx(0.32)
    assert e["similar"] is None
    assert e["model_verdicts"]["Expensive"]["return_per_dollar"] == -0.18
