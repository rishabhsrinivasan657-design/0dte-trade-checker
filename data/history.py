"""Historical SPY prices from yfinance, for the 'how often did this actually happen?' check.

Daily: ~3 years of open/close (raw prices, not dividend-adjusted: we only compare within one day).
Intraday: 5-minute bars, which Yahoo only serves for about the last 60 days.
Both drop the current (unfinished) day.
"""
from datetime import date
from functools import lru_cache

import pandas as pd
import yfinance as yf

from core.expiry import ET


@lru_cache(maxsize=4)
def daily_bars(as_of: date, symbol: str = "SPY", years: int = 3) -> pd.DataFrame:
    """Columns Open, Close; index = trading date. as_of is part of the cache key (refresh daily)."""
    df = yf.Ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=False)
    if df.empty:
        raise RuntimeError("yfinance returned no daily history")
    df.index = df.index.tz_convert(ET).date
    return df.loc[df.index < as_of, ["Open", "Close"]]


@lru_cache(maxsize=4)
def intraday_bars(as_of: date, symbol: str = "SPY", days: int = 60, interval: str = "5m") -> pd.DataFrame:
    """Columns Open, Close; index = bar START time in ET (a 15:55 bar closes at 16:00)."""
    df = yf.Ticker(symbol).history(period=f"{days}d", interval=interval)
    if df.empty:
        raise RuntimeError("yfinance returned no intraday history")
    df.index = df.index.tz_convert(ET)
    return df.loc[df.index.date < as_of, ["Open", "Close"]]
