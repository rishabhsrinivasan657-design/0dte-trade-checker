"""Phase 0 smoke test: can we reach Alpaca and yfinance and get a SPY option chain?

Run:  .venv/bin/python scripts/check_connections.py
Keys are read from .env and never printed.
"""
import os
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

ET = ZoneInfo("America/New_York")
OCC_RE = re.compile(r"^(?P<root>[A-Z]+)(?P<yymmdd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_occ(symbol: str) -> dict:
    """SPY261014C00580000 -> expiry 2026-10-14, call, strike 580.0"""
    m = OCC_RE.match(symbol)
    return {
        "expiry": datetime.strptime(m["yymmdd"], "%y%m%d").date(),
        "type": "call" if m["cp"] == "C" else "put",
        "strike": int(m["strike"]) / 1000,
    }


def first_live_expiry_date(now: datetime) -> date:
    """Today's options expire at 4:00 PM ET; after that, the earliest live expiry is tomorrow or later."""
    return now.date() if now.time() < time(16, 0) else now.date() + timedelta(days=1)


def check_alpaca(today: date) -> None:
    from alpaca.data.enums import DataFeed, OptionsFeed
    from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import OptionChainRequest, StockLatestTradeRequest

    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret or key.startswith("your_"):
        print("Alpaca: SKIPPED — fill ALPACA_API_KEY / ALPACA_SECRET_KEY in .env")
        return

    stock = StockHistoricalDataClient(key, secret)
    spot = stock.get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols="SPY", feed=DataFeed.IEX)
    )["SPY"].price
    print(f"Alpaca: SPY last trade (IEX feed) = {spot:.2f}")

    opts = OptionHistoricalDataClient(key, secret)
    chain = opts.get_option_chain(
        OptionChainRequest(
            underlying_symbol="SPY",
            feed=OptionsFeed.INDICATIVE,
            expiration_date_gte=today,
            expiration_date_lte=today + timedelta(days=7),
            strike_price_gte=spot - 5,
            strike_price_lte=spot + 5,
        )
    )
    rows = []
    for sym, snap in chain.items():
        q = snap.latest_quote
        rows.append({
            "symbol": sym, **parse_occ(sym),
            "bid": q.bid_price if q else None,
            "ask": q.ask_price if q else None,
            "iv": snap.implied_volatility,
            "delta": snap.greeks.delta if snap.greeks else None,
        })
    df = pd.DataFrame(rows)
    nearest = df["expiry"].min()
    df = df[df["expiry"] == nearest].sort_values(["type", "strike"])
    label = "0DTE (today)" if nearest == datetime.now(ET).date() else "next expiry"
    print(f"Alpaca: {len(df)} contracts within ±$5 for {nearest} [{label}]")
    print(df.drop(columns="symbol").head(8).to_string(index=False))


def check_yfinance(today: date) -> None:
    import yfinance as yf

    t = yf.Ticker("SPY")
    spot = t.fast_info["last_price"]
    expiries = [e for e in t.options if date.fromisoformat(e) >= today]
    nearest = expiries[0]
    calls = t.option_chain(nearest).calls
    near = calls[(calls.strike - spot).abs() <= 3]
    print(f"\nyfinance: SPY = {spot:.2f}, nearest expiry {nearest}, {len(calls)} calls")
    print(near[["contractSymbol", "strike", "bid", "ask", "impliedVolatility"]].to_string(index=False))


if __name__ == "__main__":
    load_dotenv()
    now = datetime.now(ET)
    print(f"Now (ET): {now:%Y-%m-%d %H:%M}\n")
    for name, fn in [("Alpaca", check_alpaca), ("yfinance", check_yfinance)]:
        try:
            fn(first_live_expiry_date(now))
        except Exception as e:  # smoke test: report and keep going
            print(f"{name}: FAILED — {type(e).__name__}: {e}")
