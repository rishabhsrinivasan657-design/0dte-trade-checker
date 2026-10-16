"""Historical archive for the backtest and the ML model, saved under data/cache/ (git-ignored).

  spy_5m.parquet           SPY 5-minute bars (SIP feed), regular session, 2018 onward
  vix_daily.parquet        VIX daily close (yfinance), a feature for the model
  options/YYYY-MM-DD.parquet   5-minute TRADE bars for that day's 0DTE SPY options

Caveat: Alpaca's history for options is trade bars, not bid/ask quotes, and only goes
back to early 2024. The backtest therefore adds an assumed spread cost on entry.
"""
import os
from datetime import date, datetime, time, timedelta
from math import ceil, floor
from pathlib import Path

import pandas as pd
import yfinance as yf

from core.expiry import ET

CACHE = Path(__file__).parent / "cache"
SPY_FILE = CACHE / "spy_5m.parquet"
VIX_FILE = CACHE / "vix_daily.parquet"
OPTIONS_DIR = CACHE / "options"
STRIKE_PAD = 6          # collect strikes from (day low - 6) to (day high + 6)
SYMBOLS_PER_REQUEST = 50


def _clients():
    from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Alpaca keys needed to build the archive")
    return StockHistoricalDataClient(key, secret), OptionHistoricalDataClient(key, secret)


def _regular_session(df: pd.DataFrame) -> pd.DataFrame:
    t = df.index.time
    return df[(t >= time(9, 30)) & (t < time(16, 0))]


# ---------------- SPY ----------------
def update_spy(start: date = date(2018, 1, 1), end: date | None = None) -> pd.DataFrame:
    """Download (or extend) SPY 5-minute bars up to `end` (default: yesterday)."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    stock, _ = _clients()
    end = end or (datetime.now(ET).date() - timedelta(days=1))
    old = load_spy() if SPY_FILE.exists() else None
    if old is not None and len(old):
        start = old.index[-1].date() + timedelta(days=1)
    if start > end:
        return old

    frames, cur = [], start
    while cur <= end:
        nxt = min(date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1), end + timedelta(days=1))
        bars = stock.get_stock_bars(StockBarsRequest(
            symbol_or_symbols="SPY", timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=datetime.combine(cur, time(0, 0), tzinfo=ET), end=datetime.combine(nxt, time(0, 0), tzinfo=ET),
            feed=DataFeed.SIP, adjustment=Adjustment.RAW)).df
        if len(bars):
            bars = bars.droplevel("symbol")
            bars.index = bars.index.tz_convert(ET)
            frames.append(_regular_session(bars[["open", "high", "low", "close", "volume"]]))
        print(f"  SPY {cur:%Y-%m}: {len(bars)} bars", flush=True)
        cur = nxt
    new = pd.concat(([old] if old is not None else []) + frames)
    new = new[~new.index.duplicated()].sort_index()
    CACHE.mkdir(parents=True, exist_ok=True)
    new.to_parquet(SPY_FILE)
    return new


def load_spy() -> pd.DataFrame:
    df = pd.read_parquet(SPY_FILE)
    df.index = df.index.tz_convert(ET)
    return df


# ---------------- VIX ----------------
def update_vix() -> pd.DataFrame:
    df = yf.Ticker("^VIX").history(period="max", interval="1d")[["Close"]].rename(columns={"Close": "vix"})
    df.index = df.index.tz_convert(ET).date
    df = df[df.index >= date(2017, 1, 1)]
    df.index.name = "date"
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(VIX_FILE)
    return df


def load_vix() -> pd.Series:
    return pd.read_parquet(VIX_FILE)["vix"]


# ---------------- 0DTE options ----------------
def occ_symbol(day: date, option_type: str, strike: float) -> str:
    return f"SPY{day:%y%m%d}{'C' if option_type == 'call' else 'P'}{round(strike * 1000):08d}"


def update_options(start: date = date(2024, 2, 1), end: date | None = None) -> int:
    """Download each trading day's 0DTE option bars (one file per day; existing days are skipped)."""
    from alpaca.data.requests import OptionBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    _, opt = _clients()
    spy = load_spy()
    OPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    days = sorted({d for d in spy.index.date if start <= d <= (end or date.max)})
    done = 0
    for day in days:
        out = OPTIONS_DIR / f"{day}.parquet"
        if out.exists():
            continue
        session = spy[spy.index.date == day]
        lo, hi = floor(session["low"].min()) - STRIKE_PAD, ceil(session["high"].max()) + STRIKE_PAD
        symbols = [occ_symbol(day, cp, k) for k in range(lo, hi + 1) for cp in ("call", "put")]
        frames = []
        for i in range(0, len(symbols), SYMBOLS_PER_REQUEST):
            bars = opt.get_option_bars(OptionBarsRequest(
                symbol_or_symbols=symbols[i:i + SYMBOLS_PER_REQUEST], timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                start=datetime.combine(day, time(9, 30), tzinfo=ET),
                end=datetime.combine(day, time(16, 0), tzinfo=ET))).df
            if len(bars):
                frames.append(bars.reset_index())
        df = pd.concat(frames) if frames else pd.DataFrame(columns=["symbol", "timestamp"])
        if len(df):
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(ET)
            df = df[["symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap"]]
        df.to_parquet(out)          # an empty file marks "no 0DTE data that day" so we don't retry
        done += 1
        print(f"  options {day}: {df['symbol'].nunique() if len(df) else 0} contracts, {len(df)} bars", flush=True)
    return done


def load_options(day: date) -> pd.DataFrame:
    """One day's 0DTE bars with parsed type/strike. Empty frame if none."""
    path = OPTIONS_DIR / f"{day}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(ET)
    df["type"] = df["symbol"].str[9].map({"C": "call", "P": "put"})
    df["strike"] = df["symbol"].str[10:].astype(int) / 1000
    return df


def option_days() -> list[date]:
    return sorted(date.fromisoformat(p.stem) for p in OPTIONS_DIR.glob("*.parquet"))
