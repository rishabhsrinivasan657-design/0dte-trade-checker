"""After you buy: hold or sell? Two studies on the 5-minute 0DTE option archive.

1. Hold vs sell now (state table)
   Every 15 minutes on every archived day, for every contract that traded: if you owned it right
   then, was selling (last trade minus an assumed half-spread) better or worse than holding to
   the close? Grouped by call/put, distance from SPY in typical moves, and time left.
   This is what the position tracker looks up live.

2. Exit rules
   For every backtest entry (research/backtest.py), follow the option's 5-minute prices and apply
   rules like "take profit at +100%" or "sell by 3:30 PM". Compared with holding to 4 PM.
   Checks happen on 5-minute bar closes, like someone checking every 5 minutes; real fills
   would vary.
"""
from datetime import date, datetime, time, timedelta
from math import ceil, isnan, sqrt

import numpy as np
import pandas as pd

from core.expiry import ET, MINUTES_PER_YEAR
from core.implied_vol import implied_vol
from research.backtest import RATE, half_spread, minutes_left, price_at
from research.insights import z_bin, z_label

STATE_TIMES = [(datetime.combine(date.min, time(9, 45)) + timedelta(minutes=15 * i)).time() for i in range(25)]  # 9:45 .. 15:45
BAR = timedelta(minutes=5)
MAX_AGE = timedelta(minutes=10)
TIME_BUCKET_MINUTES = 30


def time_bucket30(mins_left: float) -> int:
    """1 = last 30 minutes, 2 = 30–60 minutes left, ... 13 = 6–6.5 hours left."""
    return max(1, min(13, ceil(mins_left / TIME_BUCKET_MINUTES)))


def time_bucket30_label(b: int) -> str:
    hi, lo = b * TIME_BUCKET_MINUTES, (b - 1) * TIME_BUCKET_MINUTES
    fmt = lambda m: f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"
    return f"{fmt(lo)}–{fmt(hi)} left" if lo else f"last {hi} minutes"


def last_prices(opts: pd.DataFrame, t_end: datetime) -> pd.DataFrame:
    """Last trade per contract in bars ending at or before t_end and no older than MAX_AGE."""
    starts = opts["timestamp"]
    ok = opts[(starts <= t_end - BAR) & (starts >= t_end - BAR - MAX_AGE)]
    return ok.sort_values("timestamp").groupby(["type", "strike"], as_index=False).last()


def atm_iv_at(px: pd.DataFrame, S: float, T: float) -> float:
    """Average implied vol of the call and put at the strike nearest spot (both must have traded)."""
    both = px.groupby("strike").filter(lambda g: set(g["type"]) == {"call", "put"})
    if both.empty:
        return float("nan")
    k = both["strike"].iloc[(both["strike"] - S).abs().argmin()]
    ivs = [implied_vol(float(r["close"]), S, k, T, RATE, r["type"]) for _, r in both[both["strike"] == k].iterrows()]
    ivs = [v for v in ivs if not isnan(v)]
    return float(np.mean(ivs)) if ivs else float("nan")


def day_states(day: date, spy_day: pd.DataFrame, opts: pd.DataFrame, times=STATE_TIMES) -> list[dict]:
    if opts.empty or spy_day.index[-1].time() < time(15, 55):
        return []
    close = float(spy_day["close"].iloc[-1])
    rows = []
    for t in times:
        S = price_at(spy_day, t)
        if isnan(S):
            continue
        mins = minutes_left(t)
        T = mins / MINUTES_PER_YEAR
        px = last_prices(opts, datetime.combine(day, t, tzinfo=ET))
        iv = atm_iv_at(px, S, T)
        if isnan(iv) or iv <= 0:
            continue
        one_sd = S * iv * sqrt(T)
        for _, r in px.iterrows():
            p = float(r["close"])
            if p <= 0:
                continue
            K, cp = float(r["strike"]), r["type"]
            moneyness = (K - S) if cp == "call" else (S - K)
            payoff = max(close - K, 0.0) if cp == "call" else max(K - close, 0.0)
            sell = max(p - half_spread(p), 0.0)
            if sell <= 0:
                continue
            rows.append({"date": day, "minutes_left": mins, "type": cp, "strike": K, "z": moneyness / one_sd,
                         "price": p, "sell": sell, "payoff": payoff})
    return rows


def build_states(spy: pd.DataFrame, load_options, days: list[date], progress: bool = False) -> pd.DataFrame:
    by_day = {d: g for d, g in spy.groupby(spy.index.date)}
    rows = []
    for i, d in enumerate(days):
        if d in by_day:
            rows += day_states(d, by_day[d], load_options(d))
        if progress and i % 100 == 0:
            print(f"  states {d}: {len(rows):,}", flush=True)
    df = pd.DataFrame(rows)
    if len(df):
        df["z_bin"] = [z_bin(z) for z in df["z"]]
        df["t_bucket"] = [time_bucket30(m) for m in df["minutes_left"]]
    return df


def hold_vs_sell_table(states: pd.DataFrame, summarize) -> list[dict]:
    """Per (type, z_bin, t_bucket): holding to the close vs selling now, per $1 you'd get by selling.
    return_per_dollar > 0 means holding did better on average."""
    s = states.assign(entry=states["sell"], pnl=states["payoff"] - states["sell"],
                      itm=states["payoff"] > 0, prob_itm=np.nan)
    out = summarize(s, ["type", "z_bin", "t_bucket"])
    win = s.groupby(["type", "z_bin", "t_bucket"])["pnl"].apply(lambda x: float((x > 0).mean())).rename("hold_won").reset_index()
    return out.merge(win, on=["type", "z_bin", "t_bucket"]).to_dict("records")


def lookup_hold(table: list[dict], option_type: str, z: float, mins_left: float, min_n: int = 150) -> dict | None:
    if mins_left > 390:
        return None
    zb, tb = z_bin(z), time_bucket30(mins_left)
    for r in table:
        if r["type"] == option_type and r["z_bin"] == zb and r["t_bucket"] == tb:
            return {**r, "t_label": time_bucket30_label(tb), "z_label": z_label(zb)} if r["trades"] >= min_n else None
    return None


# ---------------- exit rules ----------------
EXIT_RULES = {
    "hold": "Hold to 4 PM",
    "tp50": "Take profit at +50%",
    "tp100": "Take profit at +100%",
    "sl50": "Cut losses at −50%",
    "tp100_sl50": "+100% profit or −50% loss",
    "by1530": "Sell by 3:30 PM",
}


def apply_rule(rule: str, entry: float, path_times: list[time], path_prices: np.ndarray, payoff: float) -> float:
    """Proceeds per contract under a rule. path = bar END times and last-trade prices after entry."""
    if rule == "hold":
        return payoff
    for t, p in zip(path_times, path_prices):
        sell = max(p - half_spread(p), 0.0)
        if rule == "tp50" and sell >= 1.5 * entry:
            return sell
        if rule in ("tp100", "tp100_sl50") and sell >= 2.0 * entry:
            return sell
        if rule in ("sl50", "tp100_sl50") and sell <= 0.5 * entry:
            return sell
        if rule == "by1530" and t >= time(15, 30):
            return sell
    return payoff


def simulate_exits(trades: pd.DataFrame, load_options, rules=tuple(EXIT_RULES), progress: bool = False) -> pd.DataFrame:
    """One row per (entry trade, rule) with proceeds and pnl."""
    out = []
    for i, (day, g) in enumerate(trades.groupby("date")):
        opts = load_options(day)
        if opts.empty:
            continue
        opts = opts.assign(end=opts["timestamp"] + BAR).sort_values("end")
        paths = {k: (list(v["end"].dt.time), v["close"].to_numpy()) for k, v in opts.groupby(["type", "strike"])}
        for r in g.itertuples(index=False):
            times, prices = paths.get((r.type, r.strike), ([], np.array([])))
            entry_t = time.fromisoformat(r.time)
            after = [j for j, t in enumerate(times) if t > entry_t]
            pt, pp = [times[j] for j in after], prices[after] if after else np.array([])
            for rule in rules:
                proceeds = apply_rule(rule, r.entry, pt, pp, r.payoff)
                out.append({"date": day, "time": r.time, "type": r.type, "strike": r.strike, "z": getattr(r, "z", np.nan),
                            "rule": rule, "entry": r.entry, "proceeds": proceeds, "pnl": proceeds - r.entry,
                            "itm": r.payoff > 0, "prob_itm": r.prob_itm})
        if progress and i % 100 == 0:
            print(f"  exits {day}", flush=True)
    return pd.DataFrame(out)
