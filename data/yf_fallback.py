"""Fallback data source: yfinance. About 15 minutes delayed and unofficial (scrapes Yahoo)."""
from datetime import date

import pandas as pd
import yfinance as yf

from data.alpaca_client import CHAIN_COLUMNS
from data.occ import parse_occ


def get_spot(symbol: str = "SPY") -> tuple[float, None]:
    """Latest price (Yahoo doesn't tell us its time)."""
    return float(yf.Ticker(symbol).fast_info["last_price"]), None


def get_chain(expiry_from: date, spot: float, width: float = 15.0, symbol: str = "SPY") -> pd.DataFrame:
    """Same shape as alpaca_client.get_chain."""
    ticker = yf.Ticker(symbol)
    expiries = [e for e in ticker.options if date.fromisoformat(e) >= expiry_from]
    if not expiries:
        raise RuntimeError(f"yfinance has no {symbol} expiries on/after {expiry_from}")
    oc = ticker.option_chain(expiries[0])
    raw = pd.concat([oc.calls, oc.puts], ignore_index=True)
    raw = raw[(raw["strike"] - spot).abs() <= width]

    parsed = pd.DataFrame([parse_occ(s) for s in raw["contractSymbol"]], index=raw.index)
    df = pd.DataFrame({
        "symbol": raw["contractSymbol"],
        "expiry": parsed["expiry"],
        "type": parsed["type"],
        "strike": parsed["strike"],
        "bid": raw["bid"],
        "ask": raw["ask"],
        "quote_time": raw["lastTradeDate"],   # yfinance has no quote time; last trade is the closest thing
        "vendor_iv": raw["impliedVolatility"],
    })
    return df[CHAIN_COLUMNS].reset_index(drop=True)
