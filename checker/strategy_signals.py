"""Live Strategy Lab signals: today's setup for each strategy, from the live option chain.

Live quotes are used the way a trader would fill: SELL at the bid, BUY at the ask.
Rules match research/strategies.py so the live setup is the trade the backtest measured.
"""
from datetime import datetime, time
from math import isnan, pi, sqrt

import numpy as np
import pandas as pd

from core.black_scholes import prob_itm
from core.expiry import ET, MINUTES_PER_YEAR
from core.greeks import delta as bs_delta
from core.implied_vol import implied_vol
from data.chain import option_row
from data.events import event_type

ML_SELL_GATE = 0.90          # sell premium only when ML vol / market vol is below this
CONDOR_SD, CONDOR_WIDTH = 0.75, 2.0
BE_DELTA, BE_WIDTH = 0.12, 3.0
MOMENTUM_SET, MOMENTUM_ENTRY = time(10, 0), time(15, 30)


def _q(chain, cp, k):
    try:
        r = option_row(chain, k, cp)
        return {"bid": float(r["bid"]), "ask": float(r["ask"]), "mid": float(r["mid"])}
    except KeyError:
        return None


def _range_prob(S, lo, hi, T, r, iv) -> float:
    """Market-implied chance SPY finishes between lo and hi at the close."""
    return max(0.0, prob_itm(S, lo, T, r, iv, "call") - prob_itm(S, hi, T, r, iv, "call"))


def _condor(chain, S, T, r, iv, kc, kp, width, name):
    legs = [("sell", "call", kc), ("buy", "call", kc + width), ("sell", "put", kp), ("buy", "put", kp - width)]
    quotes = [_q(chain, cp, k) for _, cp, k in legs]
    if any(q is None for q in quotes):
        return None
    fills = [q["bid"] if a == "sell" else q["ask"] for (a, _, _), q in zip(legs, quotes)]
    credit = fills[0] - fills[1] + fills[2] - fills[3]
    if credit <= 0.02:
        return None
    lo, hi = kp - credit, kc + credit
    return {
        "name": name,
        "legs": [{"action": a, "type": cp, "strike": k, "price": f} for (a, cp, k), f in zip(legs, fills)],
        "credit": credit, "max_loss": width - credit, "width": width,
        "breakevens": [lo, hi], "short_strikes": [kp, kc],
        "chance_max_profit": _range_prob(S, kp, kc, T, r, iv),
        "chance_profit": _range_prob(S, lo, hi, T, r, iv),
    }


def condor_setup(chain, S, T, r, atm_iv, straddle) -> dict | None:
    sd = straddle * sqrt(pi / 2)
    calls = sorted(k for k in chain.loc[chain["type"] == "call", "strike"] if k >= S + CONDOR_SD * sd)
    puts = sorted((k for k in chain.loc[chain["type"] == "put", "strike"] if k <= S - CONDOR_SD * sd), reverse=True)
    if not calls or not puts:
        return None
    return _condor(chain, S, T, r, atm_iv, calls[0], puts[0], CONDOR_WIDTH, "Iron condor (0.75 typical moves, $2 wings)")


def pick_delta_strike(chain, cp, S, T, r, target) -> float | None:
    best, gap = None, 1.0
    for _, row in chain[chain["type"] == cp].iterrows():
        k = float(row["strike"])
        if (cp == "call" and k <= S) or (cp == "put" and k >= S):
            continue
        iv = implied_vol(float(row["mid"]), S, k, T, r, cp)
        if isnan(iv):
            continue
        g = abs(abs(bs_delta(S, k, T, r, iv, cp)) - target)
        if g < gap:
            best, gap = k, g
    return best


def breakeven_setup(chain, S, T, r, atm_iv) -> dict | None:
    kc, kp = pick_delta_strike(chain, "call", S, T, r, BE_DELTA), pick_delta_strike(chain, "put", S, T, r, BE_DELTA)
    if kc is None or kp is None:
        return None
    c = _condor(chain, S, T, r, atm_iv, kc, kp, BE_WIDTH, "Breakeven iron condor (~12 delta, $3 wings)")
    if c:
        c["stop"] = f"Close a side if buying it back costs ${c['credit']:.2f} or more (the whole credit)."
    return c


def momentum_signal(bars: pd.DataFrame, now: datetime, threshold_days: int = 60) -> dict:
    """Prev close -> 10:00 move; the trade (ATM call if up, put if down) is entered at 3:30 PM."""
    now = now.astimezone(ET)
    today = now.date()
    past = bars[bars.index.date < today]
    todays = bars[bars.index.date == today]
    if past.empty:
        return {"state": "unavailable", "text": "No price history."}
    prev_close = float(past["close"].iloc[-1])
    if now.time() < MOMENTUM_SET or todays.empty:
        return {"state": "waiting", "text": "Signal sets at 10:00 AM ET."}
    s10 = todays[todays.index.time < MOMENTUM_SET]
    if s10.empty:
        return {"state": "unavailable", "text": "No 10:00 price yet."}
    r_first = float(s10["close"].iloc[-1]) / prev_close - 1
    # "strong" = |move| above the 67th percentile of recent days (same rule as the backtest)
    hist = []
    for d, g in past.groupby(past.index.date):
        before = g[g.index.time < MOMENTUM_SET]
        prior = bars[bars.index.date < d]
        if len(before) and len(prior):
            hist.append(abs(float(before["close"].iloc[-1]) / float(prior["close"].iloc[-1]) - 1))
    thresh = float(np.quantile(hist[-threshold_days:], 0.67)) if len(hist) >= 20 else float("nan")
    direction = "call" if r_first > 0 else "put"
    window = "open" if MOMENTUM_ENTRY <= now.time() < time(16, 0) else ("later" if now.time() < MOMENTUM_ENTRY else "closed")
    return {"state": "set", "r_first": r_first, "direction": direction,
            "strong": bool(abs(r_first) > thresh) if not isnan(thresh) else None, "threshold": thresh,
            "window": window,
            "text": f"SPY {'up' if r_first > 0 else 'down'} {abs(r_first):.2%} from yesterday's close at 10:00 → "
                    f"{direction.upper()} at 3:30 PM"}


def signals(m, em, ml_vol: float | None, bars: pd.DataFrame | None, live: bool) -> dict:
    """Everything the Strategies page shows. m = MarketSnapshot, em = data.chain.expected_move(...)."""
    T, r, S, iv = m.T, m.r, m.spot, em["atm_iv"]
    ratio = ml_vol / iv if ml_vol else None
    gate = None if ratio is None else ("sell" if ratio < ML_SELL_GATE else "hold off")
    return {
        "live": live,
        "event": event_type(m.expiry),
        "ml_ratio": ratio, "gate": gate, "gate_threshold": ML_SELL_GATE,
        "condor": condor_setup(m.chain, S, T, r, iv, em["straddle"]),
        "breakeven": breakeven_setup(m.chain, S, T, r, iv),
        "momentum": momentum_signal(bars, m.as_of) if bars is not None and live else
                    {"state": "unavailable", "text": "Available during market hours."},
        "expected_move": em["move"], "atm_iv": iv,
    }
