"""Strategy Lab: backtests of 0DTE SPY strategies on the 5-minute option archive (2024 onward).

Strategies (each row = one trade; prices per share, x100 for one contract):
  short_straddle    sell the at-the-money call + put, hold to 4 PM. Tagged with the ML forecast so the
                    "ML-gated premium selling" idea can be tested: sell only when the ML expects a smaller move
                    than the options are priced for.
  short_condor      iron condor: sell call + put about 0.75 typical moves away, buy wings $2 further. Entered on
                    the hour 10:00-15:00 to test entry time, ML gating and event days (FOMC / CPI).
  breakeven_condor  the popular "breakeven iron condor": sell ~12-delta call + put, $3 wings, and close a side
                    if it ever costs as much to buy back as the WHOLE credit collected (so a stop on one side
                    roughly breaks even). Same trade without stops for comparison.
  momentum          intraday momentum: if SPY is up from yesterday's close at 10:00, buy an at-the-money call at
                    3:30 PM (put if down), hold to 4 PM. The underlying-only version uses SPY since 2018.

Honesty rules, as in research/backtest.py:
  * signals use only data known at entry
  * trade prices + an assumed half-spread per leg on every entry and early exit (max 1c, 1%); held to 4 PM =
    intrinsic value, no exit cost
  * every row keeps gross P/L and its cost, so results can be re-run at 0x / 1x / 2x the cost assumption
"""
from datetime import date, datetime, time, timedelta
from math import isnan, pi, sqrt

import numpy as np
import pandas as pd

from core.expiry import ET, MINUTES_PER_YEAR
from core.greeks import delta as bs_delta
from core.implied_vol import implied_vol
from research.backtest import RATE, half_spread, minutes_left, price_at

BAR = timedelta(minutes=5)
MAX_AGE = timedelta(minutes=20)          # a leg's last trade must be this fresh at entry
HOURLY = [time(h, 0) for h in range(10, 16)]
SD_PER_STRADDLE = sqrt(pi / 2)          # one standard deviation ≈ 1.25 × the ATM straddle


# ---------------- per-day option prices ----------------
class DayBook:
    """Last-trade prices for every contract on one day, on a 5-minute grid of bar END times."""

    def __init__(self, day: date, opts: pd.DataFrame):
        self.day = day
        o = opts.assign(end=opts["timestamp"] + BAR)
        px = o.pivot_table(index="end", columns=["type", "strike"], values="close", aggfunc="last").sort_index()
        grid = pd.date_range(datetime.combine(day, time(9, 35), tzinfo=ET), datetime.combine(day, time(16, 0), tzinfo=ET), freq="5min")
        px = px.reindex(grid)
        seen = px.notna().mul(np.arange(len(px))[:, None]).where(px.notna())
        self.px = px.ffill()                                   # last known trade price
        self.age_bars = pd.DataFrame(np.arange(len(px))[:, None] - seen.ffill().to_numpy(), index=px.index, columns=px.columns)
        self.strikes = {cp: sorted(px[cp].columns) if cp in px.columns.get_level_values(0) else [] for cp in ("call", "put")}

    def _row(self, t: time) -> int | None:
        ts = datetime.combine(self.day, t, tzinfo=ET)
        pos = self.px.index.searchsorted(ts, side="right") - 1
        return pos if pos >= 0 else None

    def price(self, cp: str, k: float, t: time) -> float:
        """Last trade at or before t, if it is at most MAX_AGE old; else nan."""
        i = self._row(t)
        if i is None or (cp, k) not in self.px.columns:
            return float("nan")
        p, age = self.px[(cp, k)].iloc[i], self.age_bars[(cp, k)].iloc[i]
        return float(p) if not isnan(p) and age * 5 <= MAX_AGE.total_seconds() / 60 else float("nan")

    def path(self, cp: str, k: float, after: time) -> pd.Series:
        """Forward-filled prices at every bar end after `after` (used for stops)."""
        ts = datetime.combine(self.day, after, tzinfo=ET)
        s = self.px[(cp, k)] if (cp, k) in self.px.columns else pd.Series(dtype=float)
        return s[s.index > ts]


def atm_straddle(book: DayBook, S: float, t: time) -> tuple[float, float, float] | None:
    """(strike, call price, put price) at the strike nearest spot with both legs priced."""
    both = sorted(set(book.strikes["call"]) & set(book.strikes["put"]), key=lambda k: abs(k - S))
    for k in both[:3]:
        c, p = book.price("call", k, t), book.price("put", k, t)
        if not (isnan(c) or isnan(p)):
            return k, c, p
    return None


def _cost(*prices: float, mult: float = 1.0) -> float:
    return mult * sum(half_spread(p) for p in prices)


def _intrinsic(cp: str, k: float, close: float) -> float:
    return max(close - k, 0.0) if cp == "call" else max(k - close, 0.0)


# ---------------- strategies ----------------
def short_straddle(book: DayBook, S: float, close: float, t: time) -> dict | None:
    atm = atm_straddle(book, S, t)
    if atm is None:
        return None
    k, c, p = atm
    gross_credit = c + p
    cost = _cost(c, p)
    payout = _intrinsic("call", k, close) + _intrinsic("put", k, close)
    return {"strategy": "short_straddle", "variant": "ATM", "strikes": f"{k:g}",
            "credit": gross_credit - cost, "gross_pnl": gross_credit - payout, "cost": cost,
            "basis": gross_credit - cost, "max_loss": float("nan")}


def short_condor(book: DayBook, S: float, close: float, t: time, k_sd: float = 0.75, width: float = 2.0) -> dict | None:
    atm = atm_straddle(book, S, t)
    if atm is None:
        return None
    sd = (atm[1] + atm[2]) * SD_PER_STRADDLE
    calls = [k for k in book.strikes["call"] if k >= S + k_sd * sd]
    puts = [k for k in book.strikes["put"] if k <= S - k_sd * sd]
    if not calls or not puts:
        return None
    kc, kp = min(calls), max(puts)
    return _condor(book, close, t, kc, kc + width, kp, kp - width, "short_condor", f"{k_sd:g} sd, ${width:g} wings")


def _condor(book, close, t, kc, kcl, kp, kpl, name, variant):
    legs = {"cs": book.price("call", kc, t), "cl": book.price("call", kcl, t),
            "ps": book.price("put", kp, t), "pl": book.price("put", kpl, t)}
    if any(isnan(v) for v in legs.values()):
        return None
    gross_credit = legs["cs"] - legs["cl"] + legs["ps"] - legs["pl"]
    if gross_credit <= 0.02:
        return None
    width = min(kcl - kc, kp - kpl)
    cost = _cost(*legs.values())
    payout = (min(_intrinsic("call", kc, close), kcl - kc) + min(_intrinsic("put", kp, close), kp - kpl))
    credit = gross_credit - cost
    return {"strategy": name, "variant": variant, "strikes": f"{kpl:g}/{kp:g} – {kc:g}/{kcl:g}",
            "credit": credit, "gross_pnl": gross_credit - payout, "cost": cost,
            "basis": width - credit, "max_loss": width - credit, "_legs": legs, "_k": (kc, kcl, kp, kpl)}


def pick_by_delta(book: DayBook, cp: str, S: float, t: time, target: float) -> float | None:
    """Out-of-the-money strike whose |delta| is closest to target (deltas from each strike's own IV)."""
    T = minutes_left(t) / MINUTES_PER_YEAR
    best, best_gap = None, 1.0
    for k in book.strikes[cp]:
        if (cp == "call" and k <= S) or (cp == "put" and k >= S):
            continue
        p = book.price(cp, k, t)
        if isnan(p) or p <= 0:
            continue
        iv = implied_vol(p, S, k, T, RATE, cp)
        if isnan(iv):
            continue
        gap = abs(abs(bs_delta(S, k, T, RATE, iv, cp)) - target)
        if gap < best_gap:
            best, best_gap = k, gap
    return best


def breakeven_condor(book: DayBook, S: float, close: float, t: time, target_delta: float = 0.12,
                     width: float = 3.0) -> list[dict]:
    """Two rows: with the per-side stop, and the same condor held to 4 PM."""
    kc, kp = pick_by_delta(book, "call", S, t, target_delta), pick_by_delta(book, "put", S, t, target_delta)
    if kc is None or kp is None:
        return []
    base = _condor(book, close, t, kc, kc + width, kp, kp - width, "breakeven_condor", "held to 4 PM")
    if base is None:
        return []
    legs = base.pop("_legs")
    base.pop("_k")
    gross_credit = legs["cs"] - legs["cl"] + legs["ps"] - legs["pl"]
    rows = [dict(base)]

    # stop: close a side when buying it back costs the whole credit collected
    gross_pnl, cost, stops = gross_credit, _cost(*legs.values()), 0
    for cp, ks, kl in (("call", kc, kc + width), ("put", kp, kp - width)):
        short, long_ = book.path(cp, ks, t), book.path(cp, kl, t)
        mark = (short - long_).dropna()
        hit = mark[mark >= gross_credit]
        if len(hit):
            ts = hit.index[0]
            ps, pl = float(short.loc[ts]), float(long_.loc[ts])
            gross_pnl -= ps - pl
            cost += _cost(ps, pl)
            stops += 1
        else:
            gross_pnl -= min(_intrinsic(cp, ks, close), width)
    rows.append({**base, "variant": "with breakeven stops", "gross_pnl": gross_pnl, "cost": cost, "stops": stops})
    return rows


def momentum_option(book: DayBook, S_entry: float, close: float, r_first: float, entry: time = time(15, 30)) -> dict | None:
    cp = "call" if r_first > 0 else "put"
    atm = atm_straddle(book, S_entry, entry)
    if atm is None:
        return None
    k = atm[0]
    p = atm[1] if cp == "call" else atm[2]
    cost = _cost(p)
    return {"strategy": "momentum", "variant": f"buy ATM {cp} at 3:30", "strikes": f"{k:g} {cp}",
            "credit": -(p + cost), "gross_pnl": _intrinsic(cp, k, close) - p, "cost": cost,
            "basis": p + cost, "max_loss": p + cost, "direction": cp}


# ---------------- underlying momentum study (SPY 2018+) ----------------
def momentum_table(spy: pd.DataFrame, first: time = time(10, 0), last: time = time(15, 30)) -> pd.DataFrame:
    """Per day: r_first = prev close -> 10:00, r_last = 15:30 -> close, and a look-ahead-free 'strong' flag
    (|r_first| above the 67th percentile of the previous 60 days)."""
    rows, prev_close = [], None
    for day, bars in spy.groupby(spy.index.date):
        if bars.index[-1].time() < time(15, 55):
            prev_close = float(bars["close"].iloc[-1])
            continue
        s_first, s_last, close = price_at(bars, first), price_at(bars, last), float(bars["close"].iloc[-1])
        if prev_close and not (isnan(s_first) or isnan(s_last)):
            rows.append({"date": day, "r_first": s_first / prev_close - 1, "r_last": close / s_last - 1,
                         "s_last": s_last, "close": close})
        prev_close = close
    df = pd.DataFrame(rows)
    thresh = df["r_first"].abs().shift(1).rolling(60, min_periods=40).quantile(0.67)
    df["strong"] = df["r_first"].abs() > thresh
    df["signal_ret"] = np.sign(df["r_first"]) * df["r_last"]       # long SPY 3:30->close if up at 10:00, else short
    return df


# ---------------- run everything ----------------
def run(spy: pd.DataFrame, load_options, days: list[date], progress: bool = False) -> pd.DataFrame:
    by_day = {d: g for d, g in spy.groupby(spy.index.date)}
    mom = momentum_table(spy).set_index("date")
    rows = []
    for i, day in enumerate(days):
        bars = by_day.get(day)
        opts = load_options(day)
        if bars is None or opts.empty or bars.index[-1].time() < time(15, 55):
            continue
        book, close = DayBook(day, opts), float(bars["close"].iloc[-1])
        out = []
        for t in HOURLY:
            S = price_at(bars, t)
            if isnan(S):
                continue
            for r in (short_straddle(book, S, close, t), short_condor(book, S, close, t)):
                if r:
                    out.append({**r, "time": t.strftime("%H:%M"), "spot": S})
        for t in (time(9, 45), time(10, 0)):
            S = price_at(bars, t)
            if not isnan(S):
                out += [{**r, "time": t.strftime("%H:%M"), "spot": S} for r in breakeven_condor(book, S, close, t)]
        if day in mom.index:
            m = mom.loc[day]
            r = momentum_option(book, float(m["s_last"]), close, float(m["r_first"]))
            if r:
                out.append({**r, "time": "15:30", "spot": float(m["s_last"]), "strong": bool(m["strong"]), "r_first": float(m["r_first"])})
        for r in out:
            r.pop("_legs", None)
            r.pop("_k", None)
            rows.append({"date": day, "close": close, **r})
        if progress and i % 100 == 0:
            print(f"  strategies {day}: {len(rows):,} trades", flush=True)
    df = pd.DataFrame(rows)
    df["minutes_left"] = [minutes_left(time.fromisoformat(t)) for t in df["time"]]
    return df


def with_costs(df: pd.DataFrame, mult: float = 1.0) -> pd.DataFrame:
    """P/L and return on the trade's capital basis at `mult` × the assumed spread cost."""
    out = df.copy()
    out["pnl"] = out["gross_pnl"] - mult * out["cost"]
    out["entry"] = out["basis"]
    out["itm"] = out["pnl"] > 0
    out["prob_itm"] = np.nan
    return out


def summary(df: pd.DataFrame, by, summarize) -> pd.DataFrame:
    """backtest.summarize plus the things that matter for selling premium: worst trade, worst day,
    result without the single worst day, and the same result before costs."""
    net = with_costs(df, 1.0)
    s = summarize(net, by)
    gross = summarize(with_costs(df, 0.0), by)[[*(by if isinstance(by, list) else [by]), "return_per_dollar"]]
    s = s.merge(gross.rename(columns={"return_per_dollar": "gross_return"}), on=by if isinstance(by, list) else [by])
    extra = []
    for key, g in net.groupby(by):
        day = g.groupby("date")[["pnl", "entry"]].sum()
        worst = day["pnl"].idxmin()
        rest = day.drop(worst)
        extra.append({**(dict(zip(by, key)) if isinstance(by, list) else {by: key}),
                      "avg_pnl_contract": float(g["pnl"].mean() * 100),
                      "worst_trade_contract": float(g["pnl"].min() * 100),
                      "worst_day": str(worst),
                      "ex_worst_day": float(rest["pnl"].sum() / rest["entry"].sum()) if len(rest) else float("nan")})
    return s.merge(pd.DataFrame(extra), on=by if isinstance(by, list) else [by])


MIN_DAYS = 20   # fewer distinct days than this and the bootstrap range is meaningless


def verdict(row: dict) -> str:
    """Plain-English outcome from the after-cost 90% range and the before-cost result."""
    if row.get("days", MIN_DAYS) < MIN_DAYS:
        return "Too few days to tell"
    if row["ci_low"] > 0:
        return "Worked after costs"
    if row["return_per_dollar"] > 0:
        return "Positive after costs, but within the range of luck"
    if row["gross_return"] > 0:
        return "Worked only before trading costs"
    return "Did not work"


def momentum_stats(df: pd.DataFrame, n_boot: int = 1000, seed: int = 0) -> dict:
    """Underlying-only intraday momentum: average 3:30->close return when trading in the direction of the
    prev close->10:00 move, with a bootstrap 90% range (resampling days)."""
    rng = np.random.default_rng(seed)
    out = {}
    for name, g in (("all days", df), ("strong 10:00 move", df[df["strong"]]),
                    ("since 2024 (0DTE era)", df[pd.to_datetime(df["date"]) >= "2024-01-01"])):
        r = g["signal_ret"].to_numpy()
        boots = [rng.choice(r, len(r)).mean() for _ in range(n_boot)] if len(r) else [np.nan]
        out[name] = {"days": int(len(r)), "hit_rate": float((r > 0).mean()) if len(r) else np.nan,
                     "avg_bp": float(r.mean() * 1e4) if len(r) else np.nan,
                     "ci_low_bp": float(np.percentile(boots, 5) * 1e4), "ci_high_bp": float(np.percentile(boots, 95) * 1e4),
                     "corr": float(np.corrcoef(g["r_first"], g["r_last"])[0, 1]) if len(r) > 2 else np.nan}
    return out
