"""Backtest: if you had bought 0DTE SPY options on past days, what happened?

For every archived day, at each decision time (10:00 ... 15:00 ET), for strikes within $5 of SPY:
  1. entry  = option's last trade price in the 5-min bar ending at that time + an assumed half-spread
  2. signals computed ONLY from data available at that moment (no look-ahead):
        market probability ITM (Black-Scholes N(d2) at the option's implied vol),
        history hit rates (previous 60 days intraday / previous 3 years daily), verdict
  3. exit = held to expiry: payoff = intrinsic value at SPY's 4:00 PM close

Caveats: entry prices are TRADES (Alpaca has no historical bid/ask for options), so the spread
cost is an assumption; r is a constant 4% (negligible at 0DTE); options data starts in 2024.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isnan

import numpy as np
import pandas as pd

from checker.history_check import FAIR_BAND, MIN_INTRADAY_DAYS, SESSION_MINUTES, verdict
from core.black_scholes import prob_itm
from core.expiry import ET, MINUTES_PER_YEAR
from core.implied_vol import implied_vol

DECISION_TIMES = [time(h, 0) for h in range(10, 16)]
STRIKE_WINDOW = 5.0
RATE = 0.04
MAX_QUOTE_AGE = timedelta(minutes=10)
INTRADAY_LOOKBACK_DAYS = 60
DAILY_LOOKBACK_DAYS = 756        # ~3 years of trading days


def half_spread(price: float) -> float:
    """Assumed cost of crossing half the bid-ask spread: 1 cent, or 1% of the price if larger."""
    return max(0.01, 0.01 * price)


def minutes_left(t: time) -> int:
    return (16 * 60) - (t.hour * 60 + t.minute)


# ---------------- SPY helpers (5-min bars, index = bar START, ET) ----------------
def daily_open_close(spy: pd.DataFrame) -> pd.DataFrame:
    """Open (first bar) and close (last bar) for each full session, as the app's history check expects."""
    g = spy.groupby(spy.index.date)
    out = pd.DataFrame({"Open": g["open"].first(), "Close": g["close"].last(), "last_bar": g.apply(lambda d: d.index[-1].time())})
    return out[out["last_bar"] >= time(15, 55)].drop(columns="last_bar")


def price_at(day_bars: pd.DataFrame, t: time) -> float:
    """SPY price at time t = close of the bar that ENDS at t (starts 5 minutes earlier)."""
    ref = (datetime.combine(date.min, t) - timedelta(minutes=5)).time()
    before = day_bars[day_bars.index.time <= ref]
    return float(before["close"].iloc[-1]) if len(before) else float("nan")


def moves_to_close(spy: pd.DataFrame, times=DECISION_TIMES) -> pd.DataFrame:
    """% move from each decision time to the close, per full session. Index = date, columns = minutes left."""
    rows = {}
    for day, bars in spy.groupby(spy.index.date):
        if bars.index[-1].time() < time(15, 55):
            continue
        close = bars["close"].iloc[-1]
        rows[day] = {minutes_left(t): close / price_at(bars, t) - 1 for t in times}
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


# ---------------- options helpers ----------------
def option_price_at(opts: pd.DataFrame, option_type: str, strike: float, t_end: datetime) -> float:
    """Last trade price in a bar ending at or before t_end, no older than MAX_QUOTE_AGE."""
    rows = opts[(opts["type"] == option_type) & (opts["strike"] == strike)]
    bar_starts = rows["timestamp"]
    ok = rows[(bar_starts <= t_end - timedelta(minutes=5)) & (bar_starts >= t_end - timedelta(minutes=5) - MAX_QUOTE_AGE)]
    return float(ok["close"].iloc[-1]) if len(ok) else float("nan")


def strike_iv(opts: pd.DataFrame, S: float, K: float, T: float, cp: str, px: float, t_end: datetime) -> float:
    """Same rule as the live checker: for an in-the-money option, solve IV from the out-of-the-money
    option at the same strike (pure time value, so far less sensitive to price noise)."""
    itm = K < S if cp == "call" else K > S
    if itm:
        other = "put" if cp == "call" else "call"
        opx = option_price_at(opts, other, K, t_end)
        if not isnan(opx) and opx > 0:
            iv = implied_vol(opx, S, K, T, RATE, other)
            if not isnan(iv):
                return iv
    return implied_vol(px, S, K, T, RATE, cp)


@dataclass
class BacktestConfig:
    times: tuple = tuple(DECISION_TIMES)
    strike_window: float = STRIKE_WINDOW
    spread_multiplier: float = 1.0   # 0 = no costs, 2 = double the assumed spread


def run_day(day: date, spy_day: pd.DataFrame, opts: pd.DataFrame, past_moves: pd.DataFrame,
            past_daily: pd.DataFrame, cfg: BacktestConfig = BacktestConfig()) -> list[dict]:
    """All trades for one day. past_* must contain only days BEFORE `day`."""
    if opts.empty or spy_day.index[-1].time() < time(15, 55):
        return []
    close = float(spy_day["close"].iloc[-1])
    intraday_hist = past_moves.tail(INTRADAY_LOOKBACK_DAYS)
    daily_hist = past_daily.tail(DAILY_LOOKBACK_DAYS)
    daily_ret = (daily_hist["Close"] / daily_hist["Open"] - 1).to_numpy()

    out = []
    for t in cfg.times:
        S = price_at(spy_day, t)
        if isnan(S):
            continue
        mins = minutes_left(t)
        T = mins / MINUTES_PER_YEAR
        t_end = datetime.combine(day, t, tzinfo=ET)
        hist_moves = intraday_hist[mins].dropna().to_numpy() if mins in intraday_hist else np.array([])
        scaled_daily = daily_ret * np.sqrt(mins / SESSION_MINUTES)

        strikes = sorted({k for k in opts["strike"].unique() if abs(k - S) <= cfg.strike_window})
        for K in strikes:
            for cp in ("call", "put"):
                px = option_price_at(opts, cp, K, t_end)
                if isnan(px) or px <= 0:
                    continue
                iv = strike_iv(opts, S, K, T, cp, px, t_end)
                if isnan(iv):
                    continue
                entry = px + cfg.spread_multiplier * half_spread(px)
                need = K / S - 1
                hit = (lambda m: m >= need) if cp == "call" else (lambda m: m <= need)
                h_intra = float(hit(hist_moves).mean()) if len(hist_moves) else float("nan")
                h_daily = float(hit(scaled_daily).mean()) if len(scaled_daily) else float("nan")
                use_intra = len(hist_moves) >= MIN_INTRADAY_DAYS
                h = h_intra if use_intra else h_daily
                p_mkt = prob_itm(S, K, T, RATE, iv, cp)
                payoff = max(close - K, 0.0) if cp == "call" else max(K - close, 0.0)
                out.append({
                    "date": day, "time": t.strftime("%H:%M"), "minutes_left": mins, "type": cp, "strike": K,
                    "spot": S, "moneyness": (K - S) if cp == "call" else (S - K),   # >0 = out of the money
                    "price": px, "entry": entry, "iv": iv, "prob_itm": p_mkt,
                    "hist_intraday": h_intra, "hist_daily": h_daily,
                    "hist_basis": "intraday" if use_intra else "daily", "hist_prob": h,
                    "verdict": verdict(p_mkt, h) if not isnan(h) else None,
                    "close": close, "payoff": payoff, "itm": payoff > 0,
                    "pnl": payoff - entry, "ret": payoff / entry - 1,
                })
    return out


def run(spy: pd.DataFrame, load_options, days: list[date], cfg: BacktestConfig = BacktestConfig(),
        progress: bool = False) -> pd.DataFrame:
    """Backtest over `days`. load_options(day) -> that day's option bars (see data.archive.load_options)."""
    moves = moves_to_close(spy, cfg.times)
    daily = daily_open_close(spy)
    by_day = {d: g for d, g in spy.groupby(spy.index.date)}
    rows = []
    for i, day in enumerate(days):
        if day not in by_day:
            continue
        rows += run_day(day, by_day[day], load_options(day), moves[moves.index < day], daily[daily.index < day], cfg)
        if progress and i % 50 == 0:
            print(f"  {day}: {len(rows):,} trades so far", flush=True)
    return pd.DataFrame(rows)


# ---------------- analysis ----------------
def summarize(trades: pd.DataFrame, by: str | list[str], n_boot: int = 500, seed: int = 0) -> pd.DataFrame:
    """Per group: trades, days, win rate, average return per $1 of premium (with a 90% bootstrap
    interval resampling whole DAYS, because trades on the same day move together)."""
    rng = np.random.default_rng(seed)
    out = []
    for key, g in trades.groupby(by):
        day_ret = g.groupby("date").apply(lambda d: d["pnl"].sum() / d["entry"].sum())
        weights = g.groupby("date")["entry"].sum()
        boots = []
        vals, w = day_ret.to_numpy(), weights.to_numpy()
        for _ in range(n_boot):
            idx = rng.integers(0, len(vals), len(vals))
            boots.append(np.average(vals[idx], weights=w[idx]))
        out.append({
            **(dict(zip(by, key)) if isinstance(by, list) else {by: key}),
            "trades": len(g), "days": g["date"].nunique(),
            "win_rate": float((g["pnl"] > 0).mean()),
            "itm_rate": float(g["itm"].mean()),
            "avg_prob_itm": float(g["prob_itm"].mean()),
            "return_per_dollar": float(g["pnl"].sum() / g["entry"].sum()),
            "ci_low": float(np.percentile(boots, 5)), "ci_high": float(np.percentile(boots, 95)),
            "ex_best_day": _ex_best_day(g),
        })
    return pd.DataFrame(out)


def _ex_best_day(g: pd.DataFrame) -> float:
    """Return per $1 with the single best day removed: shows when one rare day drives the average."""
    by_day = g.groupby("date")[["pnl", "entry"]].sum()
    if len(by_day) < 2:
        return float("nan")
    rest = by_day.drop(by_day["pnl"].idxmax())
    return float(rest["pnl"].sum() / rest["entry"].sum())


def calibration(trades: pd.DataFrame, prob_col: str, bins=(0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, 1)) -> pd.DataFrame:
    """Predicted probability (binned) vs how often the option actually finished ITM."""
    t = trades.dropna(subset=[prob_col])
    cut = pd.cut(t[prob_col], bins, include_lowest=True)
    g = t.groupby(cut, observed=True)
    return pd.DataFrame({"predicted": g[prob_col].mean(), "actual": g["itm"].mean(), "n": g.size()}).reset_index(drop=True)
