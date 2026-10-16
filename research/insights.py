"""Turn the backtest into things the checker can use live.

1. Similar trades: past trades grouped by (call/put, distance from SPY, time of day).
   Distance is measured in "typical moves to the close" (z = how far out of the money /
   the ATM 1-standard-deviation move), so a $2 strike in 2024 at SPY 480 and in 2026 at
   SPY 770 are compared fairly.
2. Realistic chances: isotonic regression maps "the market said p" to "it actually happened
   q of the time" (for finishing in the money, and for finishing past breakeven).
Both are saved to models/insights.json and looked up by the app at request time.
"""
from math import sqrt

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from core.expiry import MINUTES_PER_YEAR

Z_EDGES = [-np.inf, -1.0, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.5, np.inf]
TIME_BUCKETS = [("10–11 AM", 270, 999), ("12–1 PM", 150, 270), ("2–4 PM", 0, 150)]   # by minutes left
MIN_TRADES = 150


# ---------------- distance & time buckets ----------------
def z_distance(moneyness: float, spot: float, atm_iv: float, minutes_left: float) -> float:
    """How many 'typical moves to the close' the strike is out of the money (negative = in the money)."""
    return moneyness / (spot * atm_iv * sqrt(minutes_left / MINUTES_PER_YEAR))


def z_bin(z: float) -> int:
    return int(np.searchsorted(Z_EDGES, z, side="right") - 1)


def z_label(i: int) -> str:
    """0 is always a bin edge, so every bin is entirely in or entirely out of the money."""
    lo, hi = Z_EDGES[i], Z_EDGES[i + 1]
    moves = lambda v: f"{abs(v):g} typical move{'' if abs(v) == 1 else 's'}"
    if np.isinf(lo):
        return f"more than {moves(hi)} in the money"
    if np.isinf(hi):
        return f"more than {moves(lo)} out of the money"
    if lo >= 0:
        return f"{lo:g}–{hi:g} typical moves out of the money"
    return f"{abs(hi):g}–{abs(lo):g} typical moves in the money"


def time_bucket(minutes_left: float) -> str | None:
    if minutes_left > 390:
        return None                      # not a same-day trade
    for name, lo, hi in TIME_BUCKETS:
        if lo <= minutes_left < hi or (hi == 999 and minutes_left >= lo):
            return name
    return None


def add_buckets(trades: pd.DataFrame, atm_iv: pd.Series) -> pd.DataFrame:
    """atm_iv: indexed by (date, time). Adds z, z_bin, time_bucket."""
    t = trades.join(atm_iv.rename("atm_iv"), on=["date", "time"])
    t = t.dropna(subset=["atm_iv"])
    t["z"] = [z_distance(m, s, v, ml) for m, s, v, ml in zip(t["moneyness"], t["spot"], t["atm_iv"], t["minutes_left"])]
    t["z_bin"] = [z_bin(z) for z in t["z"]]
    t["time_bucket"] = [time_bucket(m) for m in t["minutes_left"]]
    return t


def similar_table(trades: pd.DataFrame, summarize) -> list[dict]:
    """One row per (type, z_bin, time_bucket) with the backtest summary (summarize = backtest.summarize)."""
    s = summarize(trades, ["type", "z_bin", "time_bucket"])
    s["z_label"] = [z_label(i) for i in s["z_bin"]]
    return s.to_dict("records")


def lookup_similar(table: list[dict], option_type: str, z: float, minutes_left: float) -> dict | None:
    tb = time_bucket(minutes_left)
    if tb is None:
        return None
    zb = z_bin(z)
    for r in table:
        if r["type"] == option_type and r["z_bin"] == zb and r["time_bucket"] == tb:
            # labels come from code, not the file, so wording fixes apply without a re-run
            return {**r, "z_label": z_label(zb)} if r["trades"] >= MIN_TRADES else None
    return None


def evidence(insights: dict, option_type: str, moneyness: float, spot: float, atm_iv: float,
             minutes_left: float, prob_itm: float, prob_profit: float) -> dict:
    """Everything the checker shows from the backtest, for one live trade."""
    z = z_distance(moneyness, spot, atm_iv, minutes_left)
    curves = insights["curves"]
    return {
        "z": z,
        "similar": lookup_similar(insights["similar"], option_type, z, minutes_left),
        "realistic_itm": float(apply_curve(prob_itm, curves["itm"])),
        "realistic_profit": float(apply_curve(prob_profit, curves["profit"])),
        "options_range": insights["options_range"],
        "model_verdicts": {r["model_verdict"]: r for r in insights["model_verdicts"]},
        "all_trades": insights["all_trades"][0],
    }


# ---------------- realistic chances ----------------
def fit_curve(pred: np.ndarray, outcome: np.ndarray) -> dict:
    iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(pred, outcome.astype(float))
    return {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}


def apply_curve(p: float | np.ndarray, curve: dict):
    return np.interp(p, curve["x"], curve["y"])


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y.astype(float)) ** 2))


def evaluate_curve(df: pd.DataFrame, pred_col: str, outcome_col: str, train_frac: float = 2 / 3) -> dict:
    """Fit on the earliest dates, score on the later ones: does the correction help on unseen days?"""
    dates = sorted(df["date"].unique())
    cut = dates[int(len(dates) * train_frac)]
    train, test = df[df["date"] < cut], df[df["date"] >= cut]
    curve = fit_curve(train[pred_col].to_numpy(), train[outcome_col].to_numpy())
    y = test[outcome_col].to_numpy()
    raw, fixed = test[pred_col].to_numpy(), apply_curve(test[pred_col].to_numpy(), curve)
    return {"test_from": str(cut), "n_test": int(len(test)),
            "brier_market": brier(raw, y), "brier_realistic": brier(fixed, y)}
