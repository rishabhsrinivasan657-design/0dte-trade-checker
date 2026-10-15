"""History check + verdict: how often did SPY really move this far by the close?

Two versions:
  daily    ~3 years of open->close moves, shrunk to the time left with the square-root-of-time
           rule (move over a fraction f of the day ≈ full-day move × √f). Many samples, one assumption.
  intraday ~60 days of 5-minute bars: the actual move from this time of day to the close.
           No assumption, but far fewer samples.
The verdict compares the market's chance of finishing ITM with history's hit rate.
"""
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from math import sqrt

import pandas as pd

from checker.trade_check import TradeCheck
from core.expiry import MARKET_CLOSE, MARKET_OPEN

SESSION_MINUTES = 390          # 9:30 -> 16:00
FAIR_BAND = 0.05               # within ±5 percentage points = "Fair"
MIN_INTRADAY_DAYS = 30         # fewer than this -> verdict uses the daily version
BAR = timedelta(minutes=5)


@dataclass
class HitRates:
    basis: str                 # "daily" or "intraday"
    n_days: int
    first_day: object
    last_day: object
    itm: float                 # share of days SPY ended past the strike
    profit: float              # share of days SPY ended past the breakeven


@dataclass
class HistoryCheck:
    needed_itm_pct: float      # % move from spot to strike (signed)
    needed_profit_pct: float   # % move from spot to breakeven (signed)
    window_minutes: float      # how much of the session we measured (≤ 390)
    daily: HitRates | None
    intraday: HitRates | None
    market_prob: float         # market's chance of finishing ITM (from the trade check)
    hist_prob: float           # history's ITM hit rate used for the verdict
    basis: str
    verdict: str               # "Cheap" / "Fair" / "Expensive"
    notes: list[str] = field(default_factory=list)


def window_minutes(minutes_left: float) -> float:
    """Minutes of the regular session to look at. Overnight/next-day expiry -> the whole session."""
    return min(max(minutes_left, 5.0), SESSION_MINUTES)


def daily_moves(daily: pd.DataFrame, window: float) -> pd.Series:
    """Open->close % move each day, shrunk to the window with the √time rule."""
    return (daily["Close"] / daily["Open"] - 1) * sqrt(window / SESSION_MINUTES)


def intraday_moves(bars: pd.DataFrame, window: float) -> pd.Series:
    """% move from (close - window) to the close, for each past day with a full session."""
    start_t = (datetime.combine(datetime.today(), MARKET_CLOSE) - timedelta(minutes=window)).time()
    moves = {}
    for day, g in bars.groupby(bars.index.date):
        times = g.index.time
        if times[-1] < time(15, 55):        # half day / missing data -> skip
            continue
        close = g["Close"].iloc[-1]
        if start_t <= MARKET_OPEN:
            ref = g["Open"].iloc[0]           # whole-day window: start from the 9:30 open
        else:
            # price at start_t = close of the bar that ENDS at start_t (starts 5 min earlier)
            ref_time = (datetime.combine(day, start_t) - BAR).time()
            before = g[times <= ref_time]
            if before.empty:
                continue
            ref = before["Close"].iloc[-1]
        moves[day] = close / ref - 1
    return pd.Series(moves, dtype=float)


def hit_rate(moves: pd.Series, needed_pct: float, option_type: str) -> float:
    """Share of days the move went at least as far as needed in the option's direction."""
    if moves.empty:
        return float("nan")
    hits = moves >= needed_pct if option_type == "call" else moves <= needed_pct
    return float(hits.mean())


def verdict(market_prob: float, hist_prob: float, band: float = FAIR_BAND) -> str:
    """History says it happens MORE often than the market prices -> the option looks cheap."""
    if hist_prob > market_prob + band:
        return "Cheap"
    if hist_prob < market_prob - band:
        return "Expensive"
    return "Fair"


def _rates(basis: str, moves: pd.Series, itm_pct: float, profit_pct: float, cp: str) -> HitRates | None:
    if moves.empty:
        return None
    return HitRates(basis, len(moves), moves.index.min(), moves.index.max(),
                    hit_rate(moves, itm_pct, cp), hit_rate(moves, profit_pct, cp))


def history_check(trade: TradeCheck, daily: pd.DataFrame | None,
                  intraday: pd.DataFrame | None) -> HistoryCheck:
    cp = trade.option_type
    itm_pct = trade.strike / trade.spot - 1
    profit_pct = trade.breakeven / trade.spot - 1
    window = window_minutes(trade.minutes_left)
    notes = []
    if trade.minutes_left > SESSION_MINUTES:
        notes.append("Expiry is not today: comparing with full-session (9:30–4:00) moves; "
                     "overnight moves are not included.")

    d = _rates("daily", daily_moves(daily, window), itm_pct, profit_pct, cp) if daily is not None else None
    i = (_rates("intraday", intraday_moves(intraday, window), itm_pct, profit_pct, cp)
         if intraday is not None else None)

    if i is not None and i.n_days >= MIN_INTRADAY_DAYS:
        chosen = i
    elif d is not None:
        chosen = d
        if i is not None:
            notes.append(f"Only {i.n_days} intraday days available; verdict uses the daily version.")
    else:
        raise ValueError("no historical data available")

    return HistoryCheck(
        needed_itm_pct=itm_pct, needed_profit_pct=profit_pct, window_minutes=window,
        daily=d, intraday=i, market_prob=trade.prob_itm, hist_prob=chosen.itm, basis=chosen.basis,
        verdict=verdict(trade.prob_itm, chosen.itm), notes=notes,
    )
