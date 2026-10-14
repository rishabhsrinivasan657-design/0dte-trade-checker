"""Primary data source: Alpaca's free 'indicative' options feed via alpaca-py.

Caveat: indicative quotes are derived from the market, not the official OPRA
consolidated quote (that needs a paid subscription). Good for learning, not for execution.
"""
import os
from datetime import date, timedelta

import pandas as pd
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockLatestTradeRequest

from data.occ import parse_occ

CHAIN_COLUMNS = ["symbol", "expiry", "type", "strike", "bid", "ask", "quote_time", "vendor_iv"]


class AlpacaUnavailable(RuntimeError):
    pass


def _keys() -> tuple[str, str]:
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret or key.startswith("your_"):
        raise AlpacaUnavailable("Alpaca keys missing (set ALPACA_API_KEY / ALPACA_SECRET_KEY)")
    return key, secret


def get_spot(symbol: str = "SPY") -> float:
    """Last trade on the free IEX feed. IEX is one exchange, but for SPY the price is fine."""
    trade = StockHistoricalDataClient(*_keys()).get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
    )
    return float(trade[symbol].price)


def get_chain(expiry_from: date, spot: float, width: float = 15.0, symbol: str = "SPY") -> pd.DataFrame:
    """All contracts of the nearest expiry on/after expiry_from, strikes within spot ± width."""
    snapshots = OptionHistoricalDataClient(*_keys()).get_option_chain(
        OptionChainRequest(
            underlying_symbol=symbol,
            feed=OptionsFeed.INDICATIVE,
            expiration_date_gte=expiry_from,
            expiration_date_lte=expiry_from + timedelta(days=7),
            strike_price_gte=spot - width,
            strike_price_lte=spot + width,
        )
    )
    rows = []
    for sym, snap in snapshots.items():
        q = snap.latest_quote
        rows.append({
            "symbol": sym,
            **parse_occ(sym),
            "bid": q.bid_price if q else float("nan"),
            "ask": q.ask_price if q else float("nan"),
            "quote_time": q.timestamp if q else pd.NaT,
            "vendor_iv": snap.implied_volatility,
        })
    if not rows:
        raise AlpacaUnavailable(f"Alpaca returned no {symbol} contracts on/after {expiry_from}")
    df = pd.DataFrame(rows, columns=CHAIN_COLUMNS)
    return df[df["expiry"] == df["expiry"].min()].reset_index(drop=True)
