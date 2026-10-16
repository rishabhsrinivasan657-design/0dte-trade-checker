"""The Checker's bottom line: one rating for this option, with plain-English reasons.

Rating comes from the trade-score ML model (research/trade_model.py): its prediction for this trade is
placed among the predictions for all past trades, and the past trades in the same rating group tell you
what actually happened (out-of-sample). Reasons are simple rules that explain the biggest drivers;
they're descriptive, not the model's internals.
"""
from math import sqrt
from pathlib import Path

import joblib
import numpy as np

from core.expiry import MINUTES_PER_YEAR
from research import trade_model as tm

TRADE_MODEL_FILE = Path(__file__).resolve().parents[1] / "models" / "trade_model.joblib"
SESSION_MINUTES = 390
OVERPAY_WARN = 1.10      # premium ≥ 10% above the ask -> reason
OVERPAY_AVOID = 1.25     # premium ≥ 25% above the ask -> Avoid, whatever the model says


def load_bundle(path: Path = TRADE_MODEL_FILE) -> dict | None:
    return joblib.load(path) if path.exists() else None


def z_distance(trade) -> float:
    """How many typical moves (to the close) the strike is out of the money; negative = in the money."""
    moneyness = (trade.strike - trade.spot) if trade.option_type == "call" else (trade.spot - trade.strike)
    one_sd = trade.spot * trade.expected["atm_iv"] * sqrt(max(trade.minutes_left, 1) / MINUTES_PER_YEAR)
    return moneyness / one_sd


def reasons(trade, z: float, ml_ratio: float | None, similar: dict | None) -> list[dict]:
    """Up to three short reasons, most important first. tone: bad | neutral | good."""
    out = []  # (priority, tone, text)
    if z > 1.0:
        out.append((10, "bad", f"Far out of the money ({z:.1f} typical moves): the worst group in testing"))
    elif z > 0.5:
        out.append((8, "bad", f"Out of the money by {z:.1f} typical moves"))
    elif z < -0.25:
        out.append((5, "neutral", "In the money: wins more often, smaller payoff"))
    else:
        out.append((4, "neutral", f"Near the money: {trade.prob_itm:.0%} chance to finish in the money"))

    need = trade.move_vs_expected
    if need > 1.0:
        out.append((9, "bad", f"Needs {need:.1f}× the expected move to break even"))
    elif need < 0.5:
        out.append((3, "good", f"Breakeven only {need:.1f}× the expected move away"))

    if trade.minutes_left < 60:
        out.append((7, "bad", "Under an hour left: fastest time decay"))

    if ml_ratio is not None and np.isfinite(ml_ratio):
        diff = ml_ratio - 1
        if diff < -0.08:
            out.append((6, "bad", f"ML expects a {abs(diff):.0%} smaller move than priced (a bad sign for buyers)"))
        elif diff > 0.08:
            out.append((4, "good", f"ML expects a {diff:.0%} bigger move than priced"))

    spread = (trade.ask - trade.bid) / trade.mid if trade.mid > 0 else 0
    if spread > 0.10:
        out.append((6, "bad", f"Wide bid/ask spread: {spread:.0%} of the price"))

    if similar and similar.get("trades", 0) >= 150:
        r = similar["return_per_dollar"]
        out.append((5 if r < -0.2 else 2, "bad" if r < -0.1 else "neutral",
                    f"Similar past trades: {'−' if r < 0 else '+'}{abs(r) * 100:.0f}¢ per $1, won {similar['win_rate']:.0%}"))

    if trade.ask > 0 and trade.premium >= trade.ask * OVERPAY_WARN:
        out.append((11, "bad", f"You'd pay {trade.premium / trade.ask - 1:.0%} above the ask ({trade.ask:.2f})"))

    out.sort(key=lambda x: -x[0])
    return [{"tone": tone, "text": text} for _, tone, text in out[:3]]


def decide(bundle: dict | None, trade, hist_intraday: float | None, ml_ratio: float | None,
           vix_prev: float | None, move_so_far: float | None, similar: dict | None) -> dict:
    z = z_distance(trade)
    why = reasons(trade, z, ml_ratio, similar)
    base = {"z": z, "reasons": why, "missing": [k for k, v in (("history", hist_intraday), ("ml_forecast", ml_ratio)) if v is None]}
    if bundle is None:
        return {**base, "available": False, "why_unavailable": "The trade-score model hasn't been trained yet."}
    if trade.minutes_left > SESSION_MINUTES:
        return {**base, "available": False,
                "why_unavailable": "Ratings are for same-day trades; this option doesn't expire today."}
    row = tm.live_row(trade.option_type, z, trade.minutes_left, trade.iv, trade.expected["atm_iv"], trade.prob_itm,
                      hist_intraday, ml_ratio, vix_prev, move_so_far, trade.mid)
    score = float(bundle["model"].predict(row)[0])
    preds = bundle["sorted_preds"]
    percentile = float(np.searchsorted(preds, score) / len(preds))
    key, label = tm.rating_for(percentile)
    override = None
    if trade.ask > 0 and trade.premium >= trade.ask * OVERPAY_AVOID:
        # the model rates buying at the market price; paying far above the ask is worse than anything it saw
        key, label = "avoid", "Avoid"
        override = f"You'd pay {trade.premium / trade.ask - 1:.0%} more than the ask. Buy at the ask or better."
    group = next((r for r in bundle["ratings"] if r["rating"] == key), None)
    return {**base, "available": True, "rating": key, "label": label, "score": score, "override": override,
            "percentile": percentile, "group": group, "trained_through": bundle.get("trained_through"),
            "n_trades": bundle.get("n_trades")}
